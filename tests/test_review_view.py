"""FaT-CV: the reviewer reads a compact view of the cases; AUTO_FIX is a list of exact edits that code applies."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
NIGHT = Path(r"D:\AI-Projects\live\petclinic-fat-night-20261027\.pilot-runs\7a71393605b2482b989c9a824cf30188\cases\TCDOC-owner-lifecycle-night-7a713936.r1.json")
TARGET_BYTES = 300_000
TARGET_CASES = 52


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _role_bytes() -> int:
    from tools.step_size import role_files

    return sum(path.stat().st_size for path in role_files("tc-reviewer"))


# --- the view -----------------------------------------------------------------------------------

def test_the_view_of_the_example_fits_the_target_per_case_with_the_role() -> None:
    from tools.review_view import render

    document = _example()
    view = render(document)
    assert len(view) + _role_bytes() <= TARGET_BYTES * len(document["test_cases"]) / TARGET_CASES
    assert len(view) * 2 < len(EXAMPLE.read_bytes())


@pytest.mark.skipif(not NIGHT.is_file(), reason="the night canonical of 27.10 is on the work machine only")
def test_the_night_canonical_of_52_cases_is_at_most_300_kb_for_the_reviewer() -> None:
    from tools.review_view import render

    document = json.loads(NIGHT.read_text(encoding="utf-8"))
    assert len(document["test_cases"]) == TARGET_CASES
    assert len(render(document)) + _role_bytes() <= TARGET_BYTES


def test_the_view_is_the_same_bytes_for_the_same_candidate() -> None:
    from tools.review_view import render

    document = _example()
    shuffled = json.loads(json.dumps(document, sort_keys=True))
    assert render(document) == render(copy.deepcopy(document)) == render(shuffled)


def test_the_view_keeps_the_human_part_and_one_line_of_checks_but_not_the_machine_part() -> None:
    from tools.canonical_document import document_sha256
    from tools.review_view import render

    document = _example()
    view = render(document).decode("utf-8")
    case = document["test_cases"][0]
    step = next(step for step in case["steps"] if step["expectations"][0]["assertions"])
    for text in (case["case_id"], case["title"], case["objective"], *case["requirement_ids"], step["action"], step["test_data"],
                 step["expectations"][0]["text"], document["requirements"][0]["text"], document["source_requirements"][0]["text"],
                 document_sha256(document)):
        assert text in view
    assert "   Проверки: " in view
    for machine in ("input_id", "INPUT-", "capability_id", "semantic_type", "type_provenance", "provenance", "ASSERT-", '"kind"'):
        assert machine not in view
    reviewed = view.split("reviewed_case_ids по порядку: ", 1)[1].split("\n", 1)[0].split(", ")
    assert reviewed == [item["case_id"] for item in document["test_cases"]]


def test_an_automation_blocker_is_shown_under_its_step_and_the_view_stays_deterministic() -> None:
    from tests.test_finish import _blocked_document
    from tools.review_view import render

    document = _blocked_document()
    view = render(document)
    step = document["test_cases"][0]["steps"][0]
    line = "   Блокер автоматизации [UNRESOLVED_OPERATION] /operation: Адрес сервиса не подтверждён."
    lines = view.decode("utf-8").splitlines()
    assert line in lines and lines.index(line) > next(i for i, text in enumerate(lines) if f"[{step['step_id']}]" in text)
    assert "BLOCK-products" not in view.decode("utf-8") and render(document) == view == render(copy.deepcopy(document))


def test_a_literal_input_missing_from_the_step_data_is_a_hint_not_a_stop() -> None:
    from tools.review_view import DATA_MISMATCH, data_mismatches, render

    document = _example()
    assert data_mismatches(document) == []
    case = document["test_cases"][0]
    step = next(step for step in case["steps"] if any(item["source"]["kind"] == "literal" for item in step["inputs"]))
    item = next(item for item in step["inputs"] if item["source"]["kind"] == "literal")
    item["source"]["value"] = "Zebra-Street-77"
    hints = data_mismatches(document)
    assert hints == [{"code": DATA_MISMATCH, "case_id": case["case_id"], "step_id": step["step_id"], "count": 1, "values": ["Zebra-Street-77"]}]
    assert f"{DATA_MISMATCH} {case['case_id']} {step['step_id']}" in render(document).decode("utf-8")
    item["source"]["value"] = "ab"  # shorter than MIN_LITERAL: no noise on «1» and «a»
    assert data_mismatches(document) == []
    item["source"]["value"] = "Zebra-Street-77"
    item["target"]["sensitive"] = True  # a sensitive value is never named
    assert data_mismatches(document) == []


def test_the_view_command_writes_the_view_of_the_assembled_candidate_only(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.canonical_document import document_sha256
    from tools.review_view import render

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    candidate = tmp_path / "r1.json"
    other = copy.deepcopy(document)
    other["test_cases"][0]["title"] = "Другой кандидат"
    candidate.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    command = [sys.executable, "-m", "tools.review_view", "--project", str(tmp_path), "--run", run_root.name, "--candidate", str(candidate)]
    _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=run_root / "bundle")  # publishes the assembled r1
    refused = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert refused.returncode == 2 and "not the assembled candidate" in json.loads(refused.stdout)["message"]
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    out = json.loads(done.stdout)
    assert done.returncode == 0 and out["candidate_sha256"] == document_sha256(document) and out["data_mismatch"] == 0
    assert Path(out["path"]).read_bytes() == render(document) and out["bytes"] == len(render(document))
    assert Path(out["path"]).parent == run_root / "review-views"


# --- the edits ----------------------------------------------------------------------------------

def _edit(document: dict, **overrides) -> dict:
    case = document["test_cases"][0]
    edit = {"correction_id": "FIX-cv", "case_id": case["case_id"], "field": "title", "old": case["title"], "new": "Уточнённый заголовок"}
    edit.update(overrides)
    return edit


def test_exact_edits_make_the_successor_and_pass_the_usual_checks() -> None:
    from tests.test_requirement_traceability import canonical_fixture as automated_document  # a plain step: free human text
    from tools.canonical_document import document_sha256, require_valid_canonical_document
    from tools.review_edits import apply_edits
    from tools.revision_selection import validate_successor

    document = automated_document()
    before = copy.deepcopy(document)
    case = document["test_cases"][0]
    step = case["steps"][0]
    expectation = step["expectations"][0]
    edits = [_edit(document),
             _edit(document, field="objective", old=case["objective"], new="Уточнённая цель."),
             _edit(document, field="precondition", index=0, old=case["preconditions"][0], new="Уточнённое предусловие."),
             _edit(document, field="action", step_id=step["step_id"], old=step["action"], new="Уточнённое действие."),
             _edit(document, field="test_data", step_id=step["step_id"], old=step["test_data"], new="Уточнённые данные."),
             _edit(document, field="expectation", step_id=step["step_id"], expectation_id=expectation["expectation_id"],
                   old=expectation["text"], new="Уточнённый результат.")]
    successor, rows = apply_edits(document, edits)
    assert rows == [] and document == before
    assert successor["revision"] == 2 and successor["parent_sha256"] == document_sha256(document)
    changed = successor["test_cases"][0]
    assert (changed["title"], changed["objective"], changed["preconditions"][0], changed["steps"][0]["action"],
            changed["steps"][0]["test_data"], changed["steps"][0]["expectations"][0]["text"]) == tuple(edit["new"] for edit in edits)
    require_valid_canonical_document(successor)
    assert validate_successor(document, successor) == []


@pytest.mark.parametrize(("overrides", "code"), [
    ({"old": "не тот текст"}, "EDIT_OLD_MISMATCH"),
    ({"field": "operation"}, "EDIT_FIELD_FORBIDDEN"),
    ({"field": "inputs"}, "EDIT_FIELD_FORBIDDEN"),
    ({"case_id": "TC-unknown"}, "EDIT_TARGET_UNKNOWN"),
    ({"field": "action", "step_id": "STEP-unknown"}, "EDIT_TARGET_UNKNOWN"),
    ({"field": "precondition", "index": 9}, "EDIT_TARGET_UNKNOWN"),
    ({"new": " "}, "EDIT_TEXT"),
    ({"correction_id": "FIX-other"}, "EDIT_CORRECTION_UNKNOWN"),
])
def test_an_edit_that_does_not_match_exactly_is_refused(overrides: dict, code: str) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.review_edits import apply_edits

    document = automated_document()
    successor, rows = apply_edits(document, [_edit(document, **overrides)], {"FIX-cv"})
    assert successor is None and [row["code"] for row in rows] == [code]


def test_one_text_is_edited_once_and_only_an_assembled_revision_1_is_edited() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.review_edits import apply_edits

    document = automated_document()
    twice = [_edit(document), _edit(document, new="Другой заголовок")]
    assert [row["code"] for row in apply_edits(document, twice)[1]] == ["EDIT_DUPLICATE"]
    assert apply_edits(document, [])[1][0]["code"] == "EDIT_LIST"
    parented = copy.deepcopy(document)
    parented["parent_sha256"] = "sha256:" + "1" * 64
    assert apply_edits(parented, [_edit(parented)])[1][0]["code"] == "EDIT_CANDIDATE"


def test_the_reviewer_output_has_exactly_one_successor_form() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import _review_rows
    from tools.review_edits import apply_edits

    document = automated_document()
    edits = [_edit(document)]
    successor, _rows = apply_edits(document, edits)
    reference = lambda value: {"document_id": value["document_id"], "revision": value["revision"], "document_sha256": document_sha256(value)}
    report = {"verdict": "AUTO_FIX_APPLIED", "candidate": reference(document), "effective": reference(successor),
              "reviewed_case_ids": [case["case_id"] for case in document["test_cases"]], "findings": [],
              "corrections": [{"id": "FIX-cv", "correction_kind": "MECHANICAL", "related_ids": [edits[0]["case_id"]],
                               "description": "Уточнить заголовок.", "evidence": ["CREQ"]}]}
    review = {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report, "successor_edits": edits}, "warnings": []}
    assert _review_rows(review) == []
    both = copy.deepcopy(review)
    both["artifacts"]["successor_document"] = successor
    assert _review_rows(both)
    accepted = copy.deepcopy(review)
    accepted["artifacts"]["validation_report"].update(verdict="ПРИНЯТО", effective=reference(document), corrections=[])
    assert _review_rows(accepted)
    machine = copy.deepcopy(review)
    machine["artifacts"]["successor_edits"][0]["field"] = "inputs"
    assert _review_rows(machine)


def test_the_edits_command_prints_the_effective_reference_for_the_report(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.review_edits import apply_edits

    document = automated_document()
    candidate, edits_path = tmp_path / "r1.json", tmp_path / "edits.json"
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    edits = [_edit(document)]
    edits_path.write_text(json.dumps(edits, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "tools.review_edits", "--candidate", str(candidate), "--edits", str(edits_path)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    successor, _rows = apply_edits(document, edits)
    assert done.returncode == 0 and json.loads(done.stdout)["effective"] == {
        "document_id": document["document_id"], "revision": 2, "document_sha256": document_sha256(successor)}
    edits_path.write_text(json.dumps([_edit(document, old="не тот")], ensure_ascii=False), encoding="utf-8")
    bad = subprocess.run([sys.executable, "-m", "tools.review_edits", "--candidate", str(candidate), "--edits", str(edits_path)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert bad.returncode == 1 and json.loads(bad.stdout)["diagnostics"][0]["code"] == "EDIT_OLD_MISMATCH"


# --- the path through the selection and the closure check ---------------------------------------

def _auto_fixed(tmp_path: Path, edits_for):
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    bundle_root = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=bundle_root, edits=edits_for(document))
    return run_root, attempt_id, document, session, package, review, receipt, bundle_root


def test_auto_fix_by_edits_selects_the_successor_and_binds_the_effective_canonical(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import _plain, orchestrate_revision
    from tools.pilot_state import read_effective_canonical
    from tools.review_edits import apply_edits

    run_root, attempt_id, document, session, package, review, receipt, bundle_root = _auto_fixed(tmp_path, lambda doc: [_edit(doc)])
    result = orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    successor, _rows = apply_edits(document, review["artifacts"]["successor_edits"])
    assert result.status == "EFFECTIVE_SELECTED" and _plain(result.effective_document) == successor
    assert _plain(read_effective_canonical(run_root, attempt_id)["document"]) == successor


def test_an_edit_that_does_not_apply_stops_the_selection_with_its_code(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

    run_root, attempt_id, document, session, package, review, receipt, bundle_root = _auto_fixed(tmp_path, lambda doc: [_edit(doc)])
    review = copy.deepcopy(review)
    review["artifacts"]["successor_edits"][0]["old"] = "не тот текст"
    with pytest.raises(OrchestrationError) as error:
        orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    assert "EDIT_OLD_MISMATCH" in json.dumps([dict(row) for row in error.value.diagnostics], ensure_ascii=False)


def test_the_closure_check_rebuilds_the_candidate_from_the_effective_document() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import document_sha256
    from tools.review_edits import apply_edits, edits_rebuild

    document = automated_document()
    edits = [_edit(document)]
    successor, _rows = apply_edits(document, edits)
    assert edits_rebuild(successor, edits, document_sha256(document))
    forged = copy.deepcopy(successor)
    forged["test_cases"][0]["objective"] = "Подменённая цель."
    assert not edits_rebuild(forged, edits, document_sha256(document))
    assert not edits_rebuild(successor, edits, "sha256:" + "0" * 64)
