"""Wave 3 U: the update brief, the merge into the next document revision, the method splice (deterministic)."""
from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from tests.test_suite_impact import EDITS, _edit, _project
from tests.test_suite_manifest import GENERATED, step5  # noqa: F401  (fixture)
from tools.build_context import build_context
from tools.code_slices import slice_file
from tools.requirement_identity import identify
from tools.run_pipeline import _docs_entries
from tools.suite_impact import impact
from tools.suite_merge import MergeError, literal_diagnostics, merge_update, repair_diagnostics, splice, update_brief


def _state(project: Path) -> dict:
    manifest = json.loads((project / "test-cases" / "suite-manifest.json").read_text(encoding="utf-8"))
    document = json.loads((project / "test-cases" / "test-cases.json").read_text(encoding="utf-8"))
    entries = _docs_entries(project, [row["path"] for row in manifest["documents"]])
    return {"manifest": manifest, "document": document, "scan": identify(entries), "envelope": build_context(project, docs_snapshot=entries), "impact": impact(project)}


def _rename_ids(case: dict, old: str, new: str) -> dict:
    """A copy of a case with every ID of ``old`` (e.g. B1-001) under ``new``."""
    return json.loads(json.dumps(case, ensure_ascii=False).replace(f"-{old}", f"-{new}").replace(f"TC-{old}", f"TC-{new}"))


def test_a_changed_requirement_updates_only_its_cases(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    _edit(project, ["change_ac7"])
    state = _state(project)
    brief = update_brief(document=state["document"], manifest=state["manifest"], scan=state["scan"], impact=state["impact"])
    assert sorted(row["key"].rsplit("#", 1)[1].rsplit(" > ", 1)[-1] for row in brief["changed_requirements"]) == ["3.7. DELETE `/students/{id}/delete`", "AC-7"]
    affected = [case["case_id"] for case in brief["affected_cases"]]
    assert affected and all("Student Successfully Deleted!" in json.dumps(case, ensure_ascii=False) for case in brief["affected_cases"])
    answer = {"test_cases": [json.loads(json.dumps(case, ensure_ascii=False).replace("Student Successfully Deleted!", "Student deleted")) for case in brief["affected_cases"]]}
    merged, report = merge_update(document=state["document"], manifest=state["manifest"], envelope=state["envelope"], scan=state["scan"], impact=state["impact"], answer=answer)
    assert merged["revision"] == state["document"]["revision"] + 1 and merged["parent_sha256"].startswith("sha256:")
    assert report["changed_cases"] == sorted(affected) and report["new_cases"] == [] and report["retired_cases"] == []
    unchanged = {case["case_id"]: case for case in state["document"]["test_cases"] if case["case_id"] not in affected}
    assert all(case == unchanged[case["case_id"]] for case in merged["test_cases"] if case["case_id"] in unchanged)
    assert merged["source_requirements"] == state["envelope"]["artifacts"]["analytics_documentation"]["requirements"]


def test_an_added_section_renumbers_sreqs_by_key_and_needs_a_mapping(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    _edit(project, ["add_section"])
    state = _state(project)
    brief = update_brief(document=state["document"], manifest=state["manifest"], scan=state["scan"], impact=state["impact"])
    [added] = brief["added_requirements"]
    renumbering = brief["source_requirement_renumbering"]
    assert renumbering and any(old != new for old, new in renumbering.items())  # later sections moved by one
    assert brief["affected_cases"] == [] and brief["id_prefixes"]["case_id"] == "TC-B1-013"
    template = state["document"]["test_cases"][0]
    new_case = _rename_ids(template, "B1-001", "B1-013")
    new_creq = {**copy.deepcopy(state["document"]["requirements"][0]), "requirement_id": brief["id_prefixes"]["requirement_id"],
                "text": "Эндпоинт /students/count возвращает число студентов.", "provenance": [f"{added['new_source_requirement_id']} — новый раздел 3.0"]}
    new_case["requirement_ids"] = [new_creq["requirement_id"]]
    with pytest.raises(MergeError) as error:
        merge_update(document=state["document"], manifest=state["manifest"], envelope=state["envelope"], scan=state["scan"], impact=state["impact"],
                     answer={"test_cases": [new_case], "requirements": [new_creq]})
    assert {row["code"] for row in error.value.diagnostics} == {"UPDATE_MAPPING_MISSING"}
    answer = {"test_cases": [new_case], "requirements": [new_creq],
              "source_to_canonical_mappings": [{"source_requirement_id": added["new_source_requirement_id"], "canonical_requirement_ids": [new_creq["requirement_id"]]}]}
    merged, report = merge_update(document=state["document"], manifest=state["manifest"], envelope=state["envelope"], scan=state["scan"], impact=state["impact"], answer=answer)
    assert report["new_cases"] == ["TC-B1-013"] and report["changed_cases"] == []
    old_map = {row["source_requirement_id"]: row["canonical_requirement_ids"] for row in state["document"]["source_to_canonical_mappings"]}
    new_map = {row["source_requirement_id"]: row["canonical_requirement_ids"] for row in merged["source_to_canonical_mappings"]}
    assert all(new_map[renumbering[old]] == creqs for old, creqs in old_map.items())
    # Provenance text that cites a moved source requirement cites its new number.
    texts = json.dumps(merged["requirements"], ensure_ascii=False)
    assert all(old not in re.findall(r"SREQ-\d{4}", texts) or old in renumbering.values() for old in renumbering)


def test_a_removed_requirement_retires_its_cases(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    _edit(project, ["remove_hello"])
    state = _state(project)
    brief = update_brief(document=state["document"], manifest=state["manifest"], scan=state["scan"], impact=state["impact"])
    [removed] = brief["removed_requirements"]
    retire = [{"case_id": case_id, "reason": "требование удалено"} for case_id in state["impact"]["cases"]["to_retire"]]
    updates = [case for case in brief["affected_cases"] if case["case_id"] in state["impact"]["cases"]["to_update"]]
    merged, report = merge_update(document=state["document"], manifest=state["manifest"], envelope=state["envelope"], scan=state["scan"], impact=state["impact"],
                                  answer={"test_cases": updates, "retire": retire})
    assert [row["case_id"] for row in report["retired_cases"]] == [row["case_id"] for row in retire]
    assert not {row["case_id"] for row in retire} & {case["case_id"] for case in merged["test_cases"]}
    assert removed["key"]


def test_people_edits_and_unaffected_cases_are_refused(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    _edit(project, ["change_ac7"])
    state = _state(project)
    [first, *_rest] = sorted(state["impact"]["cases"]["to_update"])
    brief = update_brief(document=state["document"], manifest=state["manifest"], scan=state["scan"], impact=state["impact"], edited_cases=[first])
    assert first not in [case["case_id"] for case in brief["affected_cases"]] and brief["edited_by_people"] == [first]
    edited_case = next(case for case in state["document"]["test_cases"] if case["case_id"] == first)
    other = next(case for case in state["document"]["test_cases"] if case["case_id"] not in state["impact"]["cases"]["to_update"])
    with pytest.raises(MergeError) as error:
        merge_update(document=state["document"], manifest=state["manifest"], envelope=state["envelope"], scan=state["scan"], impact=state["impact"],
                     answer={"test_cases": [edited_case, other]}, edited_cases=[first])
    assert {row["code"] for row in error.value.diagnostics} == {"UPDATE_CASE_EDITED_BY_PEOPLE", "UPDATE_CASE_NOT_AFFECTED"}


def test_literal_and_repair_checks(step5: dict) -> None:
    case = step5["document"]["test_cases"][0]
    [generated] = step5["automation"]["artifacts"]["generated_files"]
    symbols = [row for row in step5["automation"]["artifacts"]["generated_symbols"] if row["symbol_id"] == "SYMBOL-B1-001"]
    slices = slice_file(generated, symbols)
    member = slices.symbols["SYMBOL-B1-001"]
    method = "\n".join(slices.lines[member.start - 1:member.end])
    support = "\n".join(line for start, end in slices.support_ranges() for line in slices.lines[start - 1:end])
    assert literal_diagnostics(case, method, support) == []
    assert {row["code"] for row in literal_diagnostics(case, method)} == {"AUTOMATION_LITERAL_MISSING"}  # the constant lives in SUPPORT
    weakened = method.replace('"ASSERT-B1-001-01-1-3"', '"ASSERT-gone"')
    assert {row["code"] for row in literal_diagnostics(case, weakened, support)} == {"AUTOMATION_ASSERTION_MISSING"}
    changed = method.replace("Ramseh", "Ramesh")
    assert "REPAIR_EXPECTATION_CHANGED" in {row["code"] for row in repair_diagnostics(case, method, changed, support)}
    assert repair_diagnostics(case, method, method.replace("    ", "  "), support) == []


def test_splice_replaces_adds_and_removes_methods(step5: dict) -> None:
    [generated] = step5["automation"]["artifacts"]["generated_files"]
    symbols = step5["automation"]["artifacts"]["generated_symbols"]
    locators = {f"{row['locator']['class_fqn']}#{row['locator']['method_name']}": row["locator"] for row in symbols}
    first, second, *_ = sorted(locators)
    added = "    @Test\n    void countStudents() throws Exception {\n        // new case\n    }"
    content = generated["content"].replace("\n", "\r\n")
    out = splice(generated["path"], content, replace={first: "    @Test\n    void replaced() {\n    }"}, add=[added], remove=[{"name": second}], locators=locators,
                 imports=["import java.util.List;"])
    assert "\n" not in out.replace("\r\n", "")
    text = out.replace("\r\n", "\n")
    assert "void replaced()" in text and "void countStudents()" in text and f"void {locators[second]['method_name']}(" not in text
    assert "import java.util.List;" in text and text.rstrip().endswith("}")
    others = [row for row in symbols if f"{row['locator']['class_fqn']}#{row['locator']['method_name']}" not in {first, second}]
    slice_file({**generated, "content": text}, others)  # every other method is still found exactly once


def test_reviews_get_the_product_classes_the_changed_code_refers_to(tmp_path: Path, step5: dict) -> None:
    from tools.suite_update import product_contexts

    project = _project(tmp_path, step5)
    manifest = json.loads((project / "test-cases" / "suite-manifest.json").read_text(encoding="utf-8"))
    code = '        net.javaguides.springboot.bean.Student request = new net.javaguides.springboot.bean.Student(5, "Ivan", "Petrov");'
    paths = [row["path"] for row in product_contexts(project, manifest, step5["document"], code)]
    assert paths[0] == "src/main/java/net/javaguides/springboot/bean/Student.java"  # the referenced class comes first
    secret = project / "src" / "main" / "java" / "net" / "javaguides" / "springboot" / "bean" / "Student.java"
    secret.write_text(secret.read_text(encoding="utf-8") + '\nclass Secret { String password = "hunter2hunter2hunter2"; }\n', encoding="utf-8")
    assert "src/main/java/net/javaguides/springboot/bean/Student.java" not in [row["path"] for row in product_contexts(project, manifest, step5["document"], code)]


_CASE = {"case_id": "TC-1", "steps": [{"expectations": [{"assertions": [{"assertion_id": "ASSERT-1", "expected": {"kind": "literal", "value": 4}},
                                                                      {"assertion_id": "ASSERT-2", "expected": {"kind": "reference", "value": "x"}}]}]}]}
_OLD = """    @Test
    void listsStudents() {
        var students = api.list();
        assertThat(students).as("ASSERT-1").hasSize(4);
        assertThat(students.get(0))
                .as("ASSERT-2")
                .isEqualTo(student(1, "Ramesh"));
    }
"""


@pytest.mark.parametrize("new,changed", [
    (_OLD.replace("hasSize(4)", "hasSize(3)"), True),
    (_OLD.replace('.isEqualTo(student(1, "Ramesh"))', ".isNotNull()"), True),
    (_OLD.replace("hasSize(4)", "hasSizeGreaterThan(0)"), True),
    (_OLD.replace("api.list()", "api.listAll()"), False),  # the actual side may change: that is the repair
    (_OLD.replace('                .as("ASSERT-2")\n                .isEqualTo(student(1, "Ramesh"));', '                .as("ASSERT-2").isEqualTo(student(1,  "Ramesh"));'), False),
])
def test_a_repair_keeps_every_check_whole(new: str, changed: bool) -> None:
    """Review 2.1 item 10: the matcher and its arguments after the assertion label are the expectation; they never change."""
    from tools.suite_merge import repair_diagnostics

    rows = repair_diagnostics(_CASE, _OLD, new)
    assert any(row["code"] == "REPAIR_EXPECTATION_CHANGED" for row in rows) is changed, rows


def test_a_python_repair_keeps_the_expected_side() -> None:
    from tools.suite_merge import repair_diagnostics

    old = '    response = client.get("/students")\n    assert len(response.json()) == 4, "ASSERT-1"\n'
    case = {"case_id": "TC-1", "steps": [{"expectations": [{"assertions": [{"assertion_id": "ASSERT-1", "expected": {"kind": "literal", "value": 4}}]}]}]}
    assert not [row for row in repair_diagnostics(case, old, old.replace("client.get", "api_client.get")) if row["code"] == "REPAIR_EXPECTATION_CHANGED"]
    assert [row for row in repair_diagnostics(case, old, old.replace("== 4", ">= 1")) if row["code"] == "REPAIR_EXPECTATION_CHANGED"]
