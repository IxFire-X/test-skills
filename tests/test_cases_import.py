"""FaT-IMP stage 1: ready test cases from a Zephyr CSV become autotests — `scan --cases`, the source requirements by code
(one per case, not a model call), the human fields kept byte for byte."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import MODULE_PYTHON

FIXTURE = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
HEADER = "Name,Objective,Precondition,Priority,Test Script (Step-by-Step) - Step,Test Script (Step-by-Step) - Test Data,Test Script (Step-by-Step) - Expected Result"
CSV = (HEADER + "\r\n"
       "Склейка двух значений,\"Проверить, что combine соединяет значения через двоеточие\",Модуль sample доступен,High,"
       "Вызвать combine с аргументами «a» и «b»,\"left = \"\"a\"\"\nright = \"\"b\"\"\",Возвращена строка «a:b»\r\n"
       "Склейка с пустым значением,Проверить склейку с пустой правой частью,,Normal,Вызвать combine с «a» и пустой строкой,"
       "\"left = \"\"a\"\"\nright = \"\"\"\"\",Возвращена строка «a:»\r\n"
       ",,,,Сравнить результат с ожидаемым,—,\"Строки совпадают\n\nДлина строки 2\"\r\n")


def _project(tmp_path: Path, csv_text: str = CSV) -> Path:
    from tests.test_finish import _module_python

    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
                     "paths": {"source": ["src"], "tests": ["tests"]},
                     "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON,
                              "build_profile": "default", "adapter_parameters": {}},
                     "detected_from": ["pyproject.toml"]}],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    (project / "cases").mkdir()
    (project / "cases" / "zephyr.csv").write_bytes(csv_text.encode("utf-8"))
    (project / "docs").mkdir()
    (project / "docs" / "req.md").write_text("# Сумма\n\nR01. combine склеивает строки через двоеточие.\n", encoding="utf-8")
    return project


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, **flags) -> tuple[int, dict]:
    from tools import run_pipeline

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    args = SimpleNamespace(project=str(project), module="python-pytest", target=None, docs=None, cases="cases/zephyr.csv",
                           profile="local-pilot-v1", new_run=True)
    for key, value in flags.items():
        setattr(args, key, value)
    code = run_pipeline.cmd_scan(args)
    return code, json.loads(json.dumps(printed[-1], ensure_ascii=False))


def test_scan_cases_makes_the_csv_the_requirement_document_of_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    run_root = Path(scan["run_root"])
    manifest = json.loads((run_root / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["scan_request"]["cases"] == "cases/zephyr.csv" and manifest["scan_request"]["docs"] == []
    from tools.project_inventory import read_inventory_receipt

    [inventory] = [read_inventory_receipt(path) for path in (run_root / "inventories").glob("*.json")]
    assert any(row["project_path"] == "cases/zephyr.csv" for row in inventory["files"])  # the CSV bytes are bound by the inventory


@pytest.mark.parametrize(("flags", "csv_text", "code", "named"), [
    ({"docs": ["docs/req.md"]}, CSV, "IMPORT_WITH_DOCS_LATER", "--docs"),
    ({"cases": "../outside.csv"}, CSV, "SNAPSHOT_DOCS", "outside.csv"),
    ({}, HEADER + "\r\nКейс,Цель,,High,Шаг,\"Операция: не определена\",Итог\r\n", "IMPORT_CSV_V4", "Test Data"),
    ({}, "Name,Чужая колонка,Test Script (Step-by-Step) - Step\r\nА,Б,В\r\n", "ZEPHYR_CSV_COLUMNS", "Чужая колонка"),
])
def test_scan_cases_refusals_write_no_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: dict, csv_text: str, code: str, named: str) -> None:
    project = _project(tmp_path, csv_text)
    (tmp_path / "outside.csv").write_text(CSV, encoding="utf-8")
    exit_code, refused = _scan(project, monkeypatch, **flags)
    assert exit_code == 2 and refused["reason"] == code and named in refused["detail"], refused
    assert refused.get("fix")
    assert not (project / ".pilot-runs").exists() or not any((project / ".pilot-runs").iterdir())


def test_cases_import_answers_the_requirements_part_by_code_one_source_requirement_per_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import api, context_marker_parts
    from tools.pilot_state import derive_state, read_model_request, read_model_stage_artifact
    from tools.run_report import build_report

    project = _project(tmp_path)
    _code, scan = _scan(project, monkeypatch)
    run_root = Path(scan["run_root"])
    assert context_marker_parts.plan(run_root)["mode"] == "parts"  # an import always marks the code apart from the cases

    assert api.main(["cases-import", "--run-root", str(run_root), "--attempt", scan["attempt_id"]]) == 0
    state = derive_state(run_root)
    answered = [event for event in state["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    assert [event["stage_instance_id"] for event in answered] == ["context-marker:requirements"]
    requested = next(event for event in state["events"] if event["event_type"] == "MODEL_REQUESTED")
    request = read_model_request(run_root, scan["attempt_id"], "context-marker:requirements", requested["artifact_digest"])
    assert request["model_id"] == "controller:cases-import"
    artifact = read_model_stage_artifact(run_root, scan["attempt_id"], "context-marker:requirements", answered[0]["artifact_digest"])["artifact"]
    requirements = artifact["artifacts"]["analytics_documentation"]["requirements"]
    digest = "sha256:" + hashlib.sha256((project / "cases" / "zephyr.csv").read_bytes()).hexdigest()
    assert [(row["source_requirement_id"], row["text"], row["provenance"], row["digest"]) for row in requirements] == [
        ("SREQ-0001", "Проверить, что combine соединяет значения через двоеточие", ["cases/zephyr.csv#строка 2"], digest),
        ("SREQ-0002", "Проверить склейку с пустой правой частью", ["cases/zephyr.csv#строка 3"], digest),
    ]
    assert context_marker_parts.plan(run_root)["next"] == "context-marker:batch-001"
    report, _rows = build_report(project, run_root.name)
    [row] = [row for row in report["model_calls"] if row["stage"] == "context-marker:requirements"]
    assert row["by"] == "code"
    assert api.main(["cases-import", "--run-root", str(run_root), "--attempt", scan["attempt_id"]]) == 0  # again: nothing new
    assert len([event for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]) == 1


def _manual_fragment(plan: dict, context: dict, imported: dict, index: int) -> dict:
    """A fragment of one batch as the generator would give it: the human fields of the import, manual steps."""
    from tools.batch_assembly import with_digest

    batch = plan["batches"][index]
    ns = batch["namespace"]
    rows = [case for case in imported["cases"] if case["source_requirement_id"] in batch["owned_source_requirement_ids"]]
    management = {"status": None, "folder": None, "components": [], "labels": [], "owner": None, "estimated_time": None,
                  "external_keys": {}, "external_links": {"issues": [], "pages": []}, "custom_fields": {}}
    fragment = {
        "schema_version": "1.0.0", "status": "COMPLETE", "batch_id": batch["batch_id"], "namespace": ns, "plan_digest": plan["digest"],
        "header_digest": plan["header_digest"], "context_receipt_digest": context["digest"], "context_receipt": context,
        "owned_source_requirement_ids": batch["owned_source_requirement_ids"],
        "requirements": [{"requirement_id": f"CREQ-{ns}-{n:03d}", "display_order": n, "text": row["objective"], "provenance": [row["source_requirement_id"]]}
                         for n, row in enumerate(rows, start=1)],
        "source_to_canonical_mappings": [{"source_requirement_id": row["source_requirement_id"], "canonical_requirement_ids": [f"CREQ-{ns}-{n:03d}"]}
                                         for n, row in enumerate(rows, start=1)],
        "operation_capabilities": [],
        "test_cases": [{
            "case_id": f"TC-{ns}-{n:03d}", "display_order": n, "requirement_ids": [f"CREQ-{ns}-{n:03d}"], "title": row["title"],
            "objective": row["objective"], "categories": ["functional"], "priority": row["priority"], "preconditions": row["preconditions"],
            "management": management,
            "steps": [{"step_id": f"STEP-{ns}-{n:03d}-{k}", "display_order": k, "action": step["action"], "test_data": step["test_data"],
                       "manual_only": True, "manual_reason": "Импорт: машинная часть ещё не достроена.", "automation_blockers": [],
                       "operation": None, "inputs": [], "outputs": [],
                       "expectations": [{"expectation_id": f"EXP-{ns}-{n:03d}-{k}-{j}", "display_order": j, "text": text, "assertions": []}
                                        for j, text in enumerate(step["expectations"], start=1)]}
                      for k, step in enumerate(row["steps"], start=1)],
        } for n, row in enumerate(rows, start=1)],
        "diagnostics": [],
    }
    return with_digest(fragment)


def _to_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, csv_text: str = CSV) -> dict:
    """scan --cases → cases-import → the code part of the marker → merge → header and plan published."""
    from tests.test_batch_assembly import _header
    from tools import api, context_marker_parts, pilot_state
    from tools.batch_assembly import plan_batches
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    project = _project(tmp_path, csv_text)
    _code, scan = _scan(project, monkeypatch)
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    assert api.main(["cases-import", "--run-root", str(root), "--attempt", attempt_id]) == 0
    state = pilot_state.derive_state(root)
    requirements_answer = next(event["artifact_digest"] for event in state["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    answer = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", requirements_answer)["artifact"]
    assert answer["artifacts"]["source_code_and_diff"]["sources"] == [REQUIREMENTS_PLACEHOLDER]
    [part] = [row for row in context_marker_parts.plan(root)["parts"] if row["stage"] == "context-marker:batch-001"]
    pilot_state.publish_model_request(root, attempt_id, "context-marker:batch-001", model_id="model-context", invocation_id="context-part-1",
                                      input_digests=part["input_digests"])
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:batch-001", {
        **answer, "artifacts": {**answer["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine склеивает два значения"]}}})
    marker = context_marker_parts.merge_parts(root)
    context = next(pilot_state.read_context_selection(root, attempt_id, event["artifact_digest"]) for event in pilot_state.derive_state(root)["events"]
                   if event["event_type"] == "CONTEXT_SELECTED")
    header = pilot_state.publish_generator_input(root, attempt_id, "header", _header())
    plan = plan_batches(answer["artifacts"]["analytics_documentation"]["requirements"],
                        {"header_digest": header["digest"], "context_receipt_digest": context["digest"]})
    published_plan = pilot_state.publish_generator_input(root, attempt_id, "plan", plan)
    imported = pilot_state.read_attempt_receipt(root, attempt_id, "imported-cases", "ARTIFACT_READ_BACK")["record"]["imported"]
    return {"project": project, "root": root, "attempt_id": attempt_id, "marker": marker["content_digest"], "context": context,
            "header": _header(), "header_digest": header["digest"], "plan": plan, "plan_digest": published_plan["digest"], "imported": imported}


def test_the_skeleton_keeps_the_human_fields_of_the_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_generator(tmp_path, monkeypatch)
    imported = run["imported"]
    assert [case["title"] for case in imported["cases"]] == ["Склейка двух значений", "Склейка с пустым значением"]
    first, second = imported["cases"]
    assert first["priority"] == "HIGH" and second["priority"] == "MEDIUM" and second["preconditions"] == []
    assert first["steps"][0]["test_data"] == 'left = "a"\nright = "b"'
    assert [step["expectations"] for step in second["steps"]] == [["Возвращена строка «a:»"], ["Строки совпадают", "Длина строки 2"]]


def _generator_request(run: dict, *inputs: str) -> tuple[int, dict]:
    from tests.test_review_requested import _api

    batch_id = run["plan"]["batches"][0]["batch_id"]
    return _api("model-request", "--run-root", str(run["root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{batch_id}",
                "--model-id", "model-generator", "--invocation-id", "invoke-generator",
                *[flag for digest in inputs for flag in ("--input-digest", digest)])


def test_the_skeleton_is_the_last_generator_input_and_the_human_fields_are_checked_before_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_requested import _api, _journal, _put
    from tools.batch_assembly import with_digest

    run = _to_generator(tmp_path, monkeypatch)
    four = (run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"])
    code, refused = _generator_request(run, *four)
    assert code == 2 and refused["code"] == "MODEL_INPUTS_MISMATCH" and run["imported"]["digest"] in refused["message"], refused
    assert "cases-import" in refused["message"]
    code, requested = _generator_request(run, *four, run["imported"]["digest"])
    assert code == 0, requested

    good = _manual_fragment(run["plan"], run["context"], run["imported"], 0)
    changed = {key: value for key, value in good.items() if key != "digest"}
    first = changed["test_cases"][0]
    changed["test_cases"] = [dict(first, steps=[dict(first["steps"][0], test_data="другие данные"), *first["steps"][1:]])] + changed["test_cases"][1:]
    tmp = run["root"] / "tmp"
    before = _journal(run["root"])
    response = ("model-response", "--run-root", str(run["root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{good['batch_id']}")
    code, refused = _api(*response, "--artifact", _put(tmp / "changed.json", with_digest(changed)))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/0/steps/0/test_data" in refused["message"], refused
    assert "--transport-attempts 2" in refused["message"] and _journal(run["root"]) == before
    code, validated = _api("validate-fragment", "--fragment", _put(tmp / "changed2.json", with_digest(changed)), "--plan", _put(tmp / "plan.json", run["plan"]),
                           "--header", _put(tmp / "header.json", run["header"]), "--context-receipt", _put(tmp / "ctx.json", run["context"]),
                           "--imported", _put(tmp / "imported.json", run["imported"]))
    assert code == 2 and validated["code"] == "IMPORT_HUMAN_FIELD_CHANGED", validated
    code, answered = _api(*response, "--artifact", _put(tmp / "good.json", good), "--transport-attempts", "2")
    assert code == 0, answered


def test_the_batches_of_an_import_are_estimated_from_the_human_fields_one_case_a_group() -> None:
    from tools.batch_assembly import IMPORT_ANSWER_PER_HUMAN_BYTE, plan_batches
    from tools.cases_import import imported_value, source_requirements, read_cases

    rows = [HEADER] + [f"Кейс {n},Цель {n},,High,Шаг {n},\"{'д' * 3000}\",Итог {n}" for n in range(1, 5)]
    data = ("\r\n".join(rows) + "\r\n").encode("utf-8")
    imported = imported_value("cases/zephyr.csv", data)
    requirements = source_requirements("cases/zephyr.csv", data, read_cases(data))
    evidence = {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64}
    plain = plan_batches(requirements, evidence, window_bytes=200_000, input_bytes=50_000)
    assert len(plain["batches"]) == 1  # the objective «Цель N» alone says nothing about the answer of an import
    plan = plan_batches(requirements, evidence, window_bytes=200_000, input_bytes=50_000, imported=imported)
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001", "SREQ-0002"], ["SREQ-0003", "SREQ-0004"]]
    assert IMPORT_ANSWER_PER_HUMAN_BYTE == 5
    assert not any("не попал" in note or "сквозной" in note for note in plan.notes), plan.notes
    assert "imported 4" in plan.notes[0]


def test_the_review_view_of_an_import_says_the_human_text_is_not_judged() -> None:
    from tests.test_requirement_traceability import canonical_fixture
    from tools.review_view import IMPORT_NOTE, render

    document = canonical_fixture()
    plain = render(document)
    marked = render(document, imported=True)
    assert IMPORT_NOTE.encode("utf-8") in marked and IMPORT_NOTE.encode("utf-8") not in plain
    assert marked.replace(("- " + IMPORT_NOTE + "\n").encode("utf-8"), b"") == plain  # only the one line differs
    assert "не оценивай" in IMPORT_NOTE and "машинную часть" in IMPORT_NOTE


def test_the_skills_name_the_import_steps() -> None:
    root = Path(__file__).resolve().parents[1]
    generator = (root / "skills" / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
    reviewer = (root / "skills" / "tc-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    orchestrate = (root / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "imported" in generator and "IMPORT_HUMAN_FIELD_CHANGED" in generator
    assert "Импорт" in reviewer
    assert "tools.api cases-import --run-root" in orchestrate and "--imported" in orchestrate and "scan --cases" in orchestrate


GENERATED = """import pytest

from sample import combine


@pytest.mark.native
def test_selected(plugin_value: str, conftest_value: str) -> None:
    assert combine(plugin_value, conftest_value) == \"from-project-plugin:from-conftest\"
"""


def _automated_fragment(run: dict) -> dict:
    """The generator's answer for the import: the human fields as imported, the first step completed by a native action."""
    from tools.batch_assembly import with_digest

    fragment = {key: value for key, value in _manual_fragment(run["plan"], run["context"], run["imported"], 0).items() if key != "digest"}
    fragment["operation_capabilities"] = [{
        "capability_id": "CAP-combine", "adapter": "native", "action": "combine_values", "arguments": [],
        "results": [{"name": "value", "semantic_type": {"kind": "json", "type": "string"}}],
        "provenance": ["src/sample.py — combine"],
    }]
    case = fragment["test_cases"][0]
    step = case["steps"][0]
    step.update({"manual_only": False, "manual_reason": None, "operation": {"kind": "project_action", "capability_id": "CAP-combine"}})
    step["expectations"][0]["assertions"] = [{
        "assertion_id": "ASSERT-B1-001", "display_order": 1, "actual": {"kind": "project_result", "name": "value"},
        "operator": "equals", "expected": {"kind": "literal", "value": "a:b"},
    }]
    return with_digest(fragment)


def _automation(document: dict, bundle_digest: str) -> dict:
    from tools.canonical_document import document_sha256

    case = document["test_cases"][0]
    step = case["steps"][0]
    expectation = step["expectations"][0]
    digest = "sha256:" + hashlib.sha256(GENERATED.encode("utf-8")).hexdigest()
    return {"schema_version": "5.0.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
        "automation_status": "GENERATED",
        "source": {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(document),
                   "effective_bundle_receipt_digest": bundle_digest},
        "automation_revision": 1, "predecessor_automation_sha256": None, "correction_review_sha256": None,
        "generated_files": [{"file_id": "FILE-combine", "path": "tests/test_generated.py", "language": "python", "framework": "pytest",
                             "content": GENERATED, "content_digest": digest}],
        "generated_symbols": [{"file_id": "FILE-combine", "symbol_id": "SYMBOL-combine",
                               "locator": {"kind": "python_module_function", "function_name": "test_selected"}}],
        "implementation_relations": [
            {"kind": "operation", "case_id": case["case_id"], "step_id": step["step_id"], "file_id": "FILE-combine", "symbol_id": "SYMBOL-combine"},
            {"kind": "assertion", "case_id": case["case_id"], "step_id": step["step_id"], "expectation_id": expectation["expectation_id"],
             "assertion_id": expectation["assertions"][0]["assertion_id"], "file_id": "FILE-combine", "symbol_id": "SYMBOL-combine"},
        ],
        "manual_dispositions": [{"case_id": row["case_id"], "step_id": item["step_id"]}
                                for row in document["test_cases"] for item in row["steps"] if item["manual_only"]],
        "diagnostics": [],
    }}




def test_ready_cases_of_a_csv_become_an_executed_autotest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """e2e of stage 1, by commands: scan --cases → cases-import → marker parts → plan → generator with the skeleton →
    assemble → publish r1 → reviewer session → accepted review → select (effective) → tc-to-autotest → accepted static
    review → exec of the pytest mini project."""
    from tests.test_automation_revision_budget import durable_boundary, host_evidence, review
    from tests.test_finish import _args
    from tests.test_review_requested import _api, _complete, _open_session, _put, _review, _review_answer, _reviewer_request, _select
    from tools.automation_validation import automation_sha256
    from tools.pilot_state import (append_event, publish_model_request, publish_model_stage_artifact, read_attempt_receipt,
                                   read_model_stage_artifact, read_terminal_result)
    from tools.run_pipeline import cmd_exec

    run = _to_generator(tmp_path, monkeypatch)
    root, attempt_id, tmp = run["root"], run["attempt_id"], run["root"] / "tmp"
    assert _generator_request(run, run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"], run["imported"]["digest"])[0] == 0
    fragment = _automated_fragment(run)
    code, answered = _api("model-response", "--run-root", str(root), "--attempt", attempt_id, "--stage", f"tc-generator:{fragment['batch_id']}",
                          "--artifact", _put(tmp / "fragment.json", fragment))
    assert code == 0, answered

    [inventory] = [json.loads(path.read_text(encoding="utf-8")) for path in (root / "inventories").glob("*.json")]
    marker = read_model_stage_artifact(root, attempt_id, "context-marker:baseline", run["marker"])["artifact"]
    review_run = {"run_root": root, "attempt_id": attempt_id, "project": root, "tmp": tmp,  # bundles inside the run: no drift
                  "header": _put(tmp / "header.json", run["header"]), "plan": _put(tmp / "plan.json", run["plan"]),
                  "fragments": [str(tmp / "fragment.json")], "inventory": _put(tmp / "inventory.json", inventory),
                  "context": _put(tmp / "context.json", run["context"]), "marker": _put(tmp / "marker.json", marker)}
    candidate, boundary = _open_session(review_run, "command")
    document = json.loads((tmp / "candidate.json").read_text(encoding="utf-8"))
    assert [(case["title"], [step["test_data"] for step in case["steps"]]) for case in document["test_cases"]] == [
        (case["title"], [step["test_data"] for step in case["steps"]]) for case in run["imported"]["cases"]]  # the human fields of the CSV
    assert _review(review_run, candidate)[0] == 0
    code, requested = _reviewer_request(review_run, boundary)
    assert code == 0, requested
    code, answered = _api("model-response", "--run-root", str(root), "--attempt", attempt_id, "--stage", "tc-reviewer:canonical",
                          "--artifact", _review_answer(review_run))
    assert code == 0, answered
    code, completed = _complete(review_run)
    assert code == 0, completed
    code, selected = _select(review_run, str(tmp / "review.json"))
    assert code == 0 and selected["effective_bundle_receipt"] is not None, selected
    code, effective = _api("read-effective", "--run-root", str(root), "--attempt", attempt_id)
    assert code == 0, effective

    artifact = _automation(document, effective["result"]["effective_bundle_receipt_digest"])
    evidence = host_evidence("static-session", generator=f"automation-generator-{attempt_id[:12]}", reviewer=f"automation-reviewer-{attempt_id[:12]}")
    _root, _attempt, static_boundary = durable_boundary(run["project"], artifact, "static-session", evidence, run_root=root, attempt_id=attempt_id,
                                                        generator=evidence["generator_invocation_id"], reviewer=evidence["reviewer_invocation_id"])
    static_review = review(document, artifact, static_boundary, "ПРИНЯТО", session_id="static-session")
    append_event(root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id, stage_instance_id="autotest-reviewer:r1",
                 artifact_digest=automation_sha256(artifact))
    boundary_receipt = read_attempt_receipt(root, attempt_id, "automation-review-boundary-r1", "ARTIFACT_READ_BACK")
    publish_model_request(root, attempt_id, "autotest-reviewer:r1", model_id="model-automation-reviewer", invocation_id=evidence["reviewer_invocation_id"],
                          input_digests=[automation_sha256(artifact), boundary_receipt["digest"]])
    publish_model_stage_artifact(root, attempt_id, "autotest-reviewer:r1", static_review)

    from tools import run_pipeline

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)  # the scan above replaced it too
    code = cmd_exec(_args(run["project"], root))
    output = printed[-1]
    assert code == 0 and output["verdict"] == "PASS", output
    assert read_terminal_result(root, attempt_id)["accepted"] is True


def test_a_case_of_the_import_traces_only_to_its_own_source_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The human fields of case A with the trace of case B would put A's autotest under B's requirement."""
    from tests.test_review_requested import _api, _journal, _put
    from tools.batch_assembly import with_digest

    run = _to_generator(tmp_path, monkeypatch)
    assert _generator_request(run, run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"], run["imported"]["digest"])[0] == 0
    good = _manual_fragment(run["plan"], run["context"], run["imported"], 0)
    crossed = {key: value for key, value in good.items() if key != "digest"}
    first, second = crossed["test_cases"]
    crossed["test_cases"] = [dict(first, requirement_ids=second["requirement_ids"]), dict(second, requirement_ids=first["requirement_ids"])]
    tmp = run["root"] / "tmp"
    before = _journal(run["root"])
    response = ("model-response", "--run-root", str(run["root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{good['batch_id']}")
    code, refused = _api(*response, "--artifact", _put(tmp / "crossed.json", with_digest(crossed)))
    assert code == 2 and refused["code"] == "IMPORT_TRACE_CHANGED", refused
    assert "/test_cases/0/requirement_ids" in refused["message"] and "SREQ-0001" in refused["message"] and _journal(run["root"]) == before
    code, answered = _api(*response, "--artifact", _put(tmp / "good.json", good), "--transport-attempts", "2")
    assert code == 0, answered


# --- FaT-IMP2: findings 3.1, 3.3, 3.8 of bug hunt 3 -------------------------------------------------------------------

ROW = "Склейка двух значений,{objective},,High,{step},{data},{expected}\r\n"


def _row(objective: str = "Проверить склейку через двоеточие", step: str = "Вызвать combine с «a» и «b»", data: str = "—",
         expected: str = "Возвращена строка «a:b»") -> str:
    return HEADER + "\r\n" + ROW.format(objective=objective, step=step, data=data, expected=expected)


@pytest.mark.parametrize(("csv_text", "named"), [
    (_row(step=""), "пустой Step"),
    (_row(expected=""), "пустой Expected Result"),
    (_row(expected="Успешно"), "служебный ожидаемый результат «Успешно»"),
])
def test_cells_no_answer_can_keep_are_refused_on_scan_by_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, csv_text: str, named: str) -> None:
    project = _project(tmp_path, csv_text)
    code, refused = _scan(project, monkeypatch)
    assert code == 2 and refused["reason"] == "ZEPHYR_CSV_ROWS" and "строка 2" in refused["detail"] and named in refused["detail"], refused
    assert "заполните" in refused["fix"]
    assert not (project / ".pilot-runs").exists() or not any((project / ".pilot-runs").iterdir())


def test_an_empty_test_data_and_objective_are_normalized_in_the_skeleton_and_the_fragment_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_requested import _api, _put
    from tools.run_report import build_report

    run = _to_generator(tmp_path, monkeypatch, _row(objective="", data=""))
    [case] = run["imported"]["cases"]
    assert case["objective"] == case["title"] == "Склейка двух значений" and case["steps"][0]["test_data"] == "—"
    assert run["imported"]["normalized"] == [{"row": 2, "field": "objective"}, {"row": 2, "field": "test_data"}]
    fragment = _manual_fragment(run["plan"], run["context"], run["imported"], 0)
    tmp = run["root"] / "tmp"
    code, validated = _api("validate-fragment", "--fragment", _put(tmp / "f.json", fragment), "--plan", _put(tmp / "plan.json", run["plan"]),
                           "--header", _put(tmp / "header.json", run["header"]), "--context-receipt", _put(tmp / "ctx.json", run["context"]),
                           "--imported", _put(tmp / "imported.json", run["imported"]))
    assert code == 0, validated
    report, _rows = build_report(run["project"], run["root"].name)
    assert report["import"] == {"csv": "cases/zephyr.csv", "cases": 1, "normalized": 2}


def test_the_review_view_says_how_many_cells_code_normalized() -> None:
    from tests.test_requirement_traceability import canonical_fixture
    from tools.review_view import render

    document = canonical_fixture()
    assert "нормализовано: 2 ячейки" in render(document, imported=True, normalized=2).decode("utf-8")
    assert "нормализовано" not in render(document, imported=True).decode("utf-8")


def test_an_http_expectation_survives_v5_and_the_import_whole() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.cases_import import imported_value
    from tools.test_case_projections import render_zephyr_csv

    document = automated_document()
    data = render_zephyr_csv(document).payload
    [case] = imported_value("cases/zephyr.csv", data)["cases"]
    assert case["steps"][0]["expectations"] == [item["text"] for item in document["test_cases"][0]["steps"][0]["expectations"]]
    assert case["steps"][0]["expectations"] == ["Отображается список доступных товаров.\n\nHTTP 200 OK"]


def test_a_csv_with_a_secret_is_refused_on_scan_with_the_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, _row(data="https://tester:Secr3t9@stand.local/login"))
    code, refused = _scan(project, monkeypatch)
    assert code == 2 and refused["reason"] == "IMPORT_CSV_SECRET" and "url-credentials-v1" in refused["detail"], refused
    assert "Secr3t9" not in json.dumps(refused, ensure_ascii=False) and "секрет" in refused["fix"]
