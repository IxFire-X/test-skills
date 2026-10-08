"""Wave 3 D: impact analysis and the code surface (W3-Р2, W3-Р8)."""
from __future__ import annotations

import itertools
import json
import random
import re
import shutil
from pathlib import Path

import pytest

from tests.live_step5 import DOCS
from tests.test_suite_manifest import GENERATED, _manifest, _write, step5  # noqa: F401  (fixture)
from tools.code_surface import new_endpoints, surface
from tools.suite import main as suite_main
from tools.suite_impact import affected_cases, impact

STEP5_CONTROLLER = "src/main/java/net/javaguides/springboot/controller/StudentController.java"
PETCLINIC = Path("D:/AI-Projects/live/petclinic-dev")


def _project(tmp_path: Path, step5: dict) -> Path:
    project = tmp_path / "project"
    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5, surface=surface(project, ["src/main/java"]))
    _write(project, step5, manifest)
    return project


EDITS = {
    "change_ac7": lambda text: text.replace("`Student Successfully Deleted!`", "`Student deleted`"),
    "add_section": lambda text: text.replace("### 3.1. GET `/student`", "### 3.0. GET `/students/count`\nВозвращает число студентов.\n\n### 3.1. GET `/student`", 1),
    "remove_hello": lambda text: re.sub(r"### 3\.8\. GET `/hello-world`\n.*?(?=\n## 4\.)", "", text, flags=re.S),
    "rename_student": lambda text: text.replace("### 3.1. GET `/student`", "### 3.1. GET `/student` (один студент)"),
}


def _edit(project: Path, names) -> None:
    doc = project / DOCS
    raw = doc.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    for name in names:
        changed = EDITS[name](text)
        assert changed != text, name
        text = changed
    doc.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def test_requirement_changes_are_found_by_key(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    clean = impact(project)
    assert clean["requirements"] == {"added": [], "changed": [], "removed": [], "renamed": []}
    assert clean["needs_model"] is False and clean["new_endpoints"] == [] and clean["edited"]["methods"] == []
    _edit(project, EDITS)
    result = impact(project)["requirements"]
    # The deleted-student text is in AC-7 and in section 3.7: both requirements changed.  The heading of 3.1
    # carries its contract (method and path) and has no explicit ID: editing it is a change too (review 2.1 item 5).
    assert sorted(key.split("#", 1)[1].rsplit(" > ", 1)[-1] for key in result["changed"]) == [
        "3.1. GET `/student` (один студент)", "3.7. DELETE `/students/{id}/delete`", "AC-7"]
    assert [key.split("#", 1)[1] for key in result["added"]] == ["Аналитика: StudentController (независимый прогон run2) > 3. Перечень эндпоинтов и контракты API > 3.0. GET `/students/count`"]
    assert [key.split("#", 1)[1].rsplit(" > ", 1)[1] for key in result["removed"]] == ["3.8. GET `/hello-world`"]
    assert [(row["from"].rsplit(" > ", 1)[1], row["to"].rsplit(" > ", 1)[1]) for row in result["renamed"]] == [("3.1. GET `/student`", "3.1. GET `/student` (один студент)")]


@pytest.mark.parametrize("size", [1, 2, 3])
def test_affected_cases_cover_every_case_of_a_changed_key(tmp_path: Path, step5: dict, size: int) -> None:
    """W3-Р2: zero misses — every case linked to a changed or removed key is affected; extras are counted."""
    project = _project(tmp_path, step5)
    manifest = json.loads((project / "test-cases" / "suite-manifest.json").read_text(encoding="utf-8"))
    for combination in itertools.combinations(sorted(EDITS), size):
        _write(project, step5, manifest)  # reset
        (project / DOCS).write_bytes((step5["project"] / DOCS).read_bytes())
        _edit(project, combination)
        result = impact(project)
        touched = set(result["requirements"]["changed"]) | set(result["requirements"]["removed"])
        renamed = {row["from"]: row["to"] for row in result["requirements"]["renamed"]}
        expected = {case["case_id"] for case in manifest["cases"] if set(case["requirement_keys"]) & (touched | {old for old, new in renamed.items() if new in touched})}
        affected = set(result["cases"]["to_update"]) | set(result["cases"]["to_retire"])
        assert expected <= affected, (combination, expected - affected)
        assert not (affected - expected), combination  # exactly the linked cases


def test_a_heading_edit_updates_its_cases_and_a_parent_rename_only_relinks(tmp_path: Path, step5: dict) -> None:
    """Review 2.1 item 5: a Markdown section without an ID is keyed by its heading, and the heading names the endpoint."""
    project = _project(tmp_path, step5)
    manifest = json.loads((project / "test-cases" / "suite-manifest.json").read_text(encoding="utf-8"))
    linked = {case["case_id"] for case in manifest["cases"] if any(key.endswith("> 3.1. GET `/student`") for key in case["requirement_keys"])}
    assert linked
    _edit(project, ["rename_student"])
    heading = impact(project)
    assert set(heading["cases"]["to_update"]) >= linked and not linked & set(heading["cases"]["relinked"])
    assert heading["needs_model"] is True
    # Renaming only the parent section changes every child key but no requirement's own heading or text.
    (project / DOCS).write_bytes((step5["project"] / DOCS).read_bytes())
    raw = (project / DOCS).read_bytes().decode("utf-8")
    parent = next(line for line in raw.splitlines() if line.startswith("## 3."))
    (project / DOCS).write_bytes(raw.replace(parent, parent + " (v2)", 1).encode("utf-8"))
    moved = impact(project)
    assert moved["requirements"]["changed"] == [] and moved["requirements"]["renamed"]
    assert moved["cases"]["to_update"] == [] and set(moved["cases"]["relinked"]) >= linked


def test_the_suite_paths_use_the_document_limits_of_the_skillsrc(tmp_path: Path, step5: dict) -> None:
    """Review 2.1 item 15: a document the pilot accepts under .skillsrc limits is accepted by the suite paths too;
    over the limit the impact analysis stops with a reason, not a traceback."""
    from tools.suite_manifest import SuiteError

    project = _project(tmp_path, step5)
    doc = project / DOCS
    raw = doc.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    doc.write_bytes((raw + newline + "## Приложение" + newline + ("Пояснение без требований. " * 12000) + newline).encode("utf-8"))
    assert doc.stat().st_size > 256 * 1024
    with pytest.raises(SuiteError) as stopped:
        impact(project)
    assert stopped.value.code == "NEED_DOCS_LIMIT"
    skillsrc = project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8") + "limits:\n  docs_file_bytes: 1048576\n  docs_total_bytes: 4194304\n", encoding="utf-8")
    result = impact(project)
    assert [key.rsplit(" > ", 1)[-1] for key in result["requirements"]["added"]] == ["Приложение"]


def test_cases_retire_only_when_all_their_requirements_are_gone() -> None:
    manifest = {"cases": [{"case_id": "TC-1", "status": "ACTIVE", "requirement_keys": ["a", "b"]}, {"case_id": "TC-2", "status": "ACTIVE", "requirement_keys": ["b"]},
                          {"case_id": "TC-3", "status": "ACTIVE", "requirement_keys": ["c"]}, {"case_id": "TC-4", "status": "RETIRED", "requirement_keys": ["b"]}]}
    result = affected_cases(manifest, {"added": [], "changed": [], "removed": ["b"], "renamed": [{"from": "c", "to": "c2"}]})
    assert result == {"to_update": ["TC-1"], "to_retire": ["TC-2"], "relinked": ["TC-3"]}


def test_junit_reports_name_failed_and_broken_cases(tmp_path: Path, step5: dict) -> None:
    project = _project(tmp_path, step5)
    manifest = json.loads((project / "test-cases" / "suite-manifest.json").read_text(encoding="utf-8"))
    first, second = [case["methods"][0]["locator"] for case in manifest["cases"][:2]]
    owner = first.rsplit("#", 1)[0]
    report = tmp_path / "TEST-report.xml"
    report.write_text(f'''<testsuite name="{owner}">
  <testcase classname="{owner}" name="{first.rsplit('#', 1)[1]}()"><failure message="expected 200 but was 500"/></testcase>
  <testcase classname="{owner}" name="{second.rsplit('#', 1)[1]}"><error message="NoSuchMethodError"/></testcase>
  <testcase classname="other.Test" name="unrelated"><failure/></testcase>
</testsuite>''', encoding="utf-8")
    result = impact(project, junit=[str(report)])
    assert [row["case_ids"] for row in result["tests"]["failed"]] == [[manifest["cases"][0]["case_id"]]]
    assert [row["case_ids"] for row in result["tests"]["broken"]] == [[manifest["cases"][1]["case_id"]]]
    assert result["needs_model"] is True


def test_a_new_endpoint_is_a_question_and_changes_no_case(tmp_path: Path, step5: dict, capsys: pytest.CaptureFixture) -> None:
    project = _project(tmp_path, step5)
    controller = project / STEP5_CONTROLLER
    text = controller.read_text(encoding="utf-8")
    closing = text.rstrip().rfind("}")
    controller.write_text(text[:closing] + '    @GetMapping("students/count")\n    public int count() { return 4; }\n}\n', encoding="utf-8")
    assert suite_main(["impact", "--project", str(project)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["signature"] for row in payload["new_endpoints"]] == ["GET /students/count"]
    assert payload["analyst_questions"][0]["source"]["kind"] == "code-surface"
    assert payload["cases"]["to_update"] == [] and payload["needs_model"] is False


# ------------------------------------------------------------------- W3-Р8: noise of the code surface

def _variations(source: str, *, handler: str, path_variable: str | None) -> dict[str, str]:
    """Edits that change no endpoint: reorder, rename a handler, reformat, comments, a renamed path variable."""
    methods = list(re.finditer(r"\n(\s*@(?:Get|Post|Put|Delete|Patch)Mapping\b)", source))
    out = {
        "rename_handler": source.replace(f" {handler}(", f" {handler}Renamed(", 1),
        "reformat_annotations": re.sub(r'@(Get|Post|Put|Delete|Patch)Mapping\("', lambda m: f'@{m.group(1)}Mapping( value = "', source),
        "commented_mapping": source.replace("{", '{\n    // @GetMapping("/commented-out")\n    /* @PostMapping("/also-commented") */', 1),
        "blank_lines": source.replace("\n", "\n\n"),
    }
    if len(methods) >= 2:
        first, second = methods[0].start(), methods[1].start()
        out["reorder"] = source[:first] + source[second:methods[2].start() if len(methods) > 2 else source.rstrip().rfind("}")] + source[first:second] + \
            source[methods[2].start() if len(methods) > 2 else source.rstrip().rfind("}"):]
    if path_variable:
        out["rename_path_variable"] = source.replace("{" + path_variable + "}", "{" + path_variable + "Renamed}")
    return out


def _noise(root: Path, controller: str, handler: str, path_variable: str | None, tmp_path: Path) -> tuple[int, int, int]:
    project = tmp_path / root.name
    shutil.copytree(root, project, ignore=shutil.ignore_patterns(".pilot-runs", ".git", "target", ".tools", "test-cases"))
    snapshot = surface(project, ["src/main/java"])
    original = (project / controller).read_text(encoding="utf-8")
    false, true, total = 0, 0, 0
    for _name, variant in sorted(_variations(original, handler=handler, path_variable=path_variable).items()):
        (project / controller).write_text(variant, encoding="utf-8")
        questions = new_endpoints(surface(project, ["src/main/java"]), snapshot, [])
        false += len(questions)
        total += len(questions)
    closing = original.rstrip().rfind("}")
    (project / controller).write_text(original[:closing] + '\n\t@GetMapping("/added/endpoint")\n\tpublic String added() { return "x"; }\n}\n', encoding="utf-8")
    questions = new_endpoints(surface(project, ["src/main/java"]), snapshot, [])
    true += sum(row["signature"] == "GET /added/endpoint" for row in questions)
    false += sum(row["signature"] != "GET /added/endpoint" for row in questions)
    total += len(questions)
    (project / controller).write_text(original, encoding="utf-8")
    return false, true, total


def test_code_surface_noise_on_step5_and_petclinic_variations(tmp_path: Path) -> None:
    step5_root = Path(__file__).resolve().parent / "fixtures" / "live-step5-20261006" / "project"
    false, true, total = _noise(step5_root, STEP5_CONTROLLER, "getStudents", "id", tmp_path)
    assert true == 1 and false == 0, (false, true, total)
    if not PETCLINIC.is_dir():
        pytest.skip("the Petclinic clone is not on this machine")
    false, true, total = _noise(PETCLINIC, "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java", "showOwner", "ownerId", tmp_path)
    assert true == 1 and false == 0, (false, true, total)
