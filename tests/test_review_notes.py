"""FaT-RN: an accepted review may carry WARNING/INFO findings; the person sees them, the code never acts on them."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "tc-reviewer-output.schema.json"
MESSAGE = "Формулировка ожидаемого результата неточна: уточнить у аналитика единицы веса."


def _finding(severity: str, case_id: str, **overrides) -> dict:
    finding = {"severity": severity, "code": "WORDING_IMPRECISE", "message": MESSAGE,
               "evidence": ["CREQ-1"], "related_ids": [case_id]}
    finding.update(overrides)
    return finding


def _review(document: dict, verdict: str, findings: list) -> dict:
    from tools.canonical_document import document_sha256

    selected = {"document_id": document["document_id"], "revision": document["revision"], "document_sha256": document_sha256(document)}
    report = {"verdict": verdict, "candidate": selected, "effective": selected,
              "reviewed_case_ids": [case["case_id"] for case in document["test_cases"]], "findings": findings, "corrections": []}
    return {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report}, "warnings": []}


def _schema_errors(review: dict) -> list:
    from tools.schema_validation import schema_diagnostics

    return schema_diagnostics(review, SCHEMA, ROOT)


# --- the contract: three cases ---------------------------------------------------------------------

def test_accepted_with_warning_and_info_is_valid_and_selects_the_candidate_byte_for_byte() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.revision_selection import select_effective_document

    document = automated_document()
    case_id = document["test_cases"][0]["case_id"]
    review = _review(document, "ПРИНЯТО", [_finding("WARNING", case_id), _finding("INFO", case_id, code="ANALYST_QUESTION")])
    assert _schema_errors(review) == []
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    selected = select_effective_document(document, review["artifacts"]["validation_report"])
    assert selected is document and json.dumps(selected, ensure_ascii=False, sort_keys=True) == before


def test_accepted_with_a_blocking_finding_is_invalid() -> None:
    from tests.test_automation_revision_budget import automated_document

    document = automated_document()
    review = _review(document, "ПРИНЯТО", [_finding("WARNING", "TC-x"), _finding("BLOCKING", "TC-x")])
    assert _schema_errors(review)


def test_accepted_with_corrections_or_a_successor_stays_invalid() -> None:
    from tests.test_automation_revision_budget import automated_document

    document = automated_document()
    review = _review(document, "ПРИНЯТО", [_finding("WARNING", "TC-x")])
    corrected = copy.deepcopy(review)
    corrected["artifacts"]["validation_report"]["corrections"] = [
        {"id": "FIX-a", "correction_kind": "MECHANICAL", "related_ids": ["TC-x"], "description": "Уточнить.", "evidence": ["CREQ"]}]
    assert _schema_errors(corrected)
    successor = copy.deepcopy(review)
    successor["artifacts"]["successor_edits"] = [{"correction_id": "FIX-a", "case_id": "TC-x", "field": "title", "old": "a", "new": "b"}]
    assert _schema_errors(successor)


def test_accepted_without_findings_is_as_before() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.revision_selection import select_effective_document

    document = automated_document()
    review = _review(document, "ПРИНЯТО", [])
    assert _schema_errors(review) == []
    assert select_effective_document(document, review["artifacts"]["validation_report"]) is document


# --- the person sees them ---------------------------------------------------------------------------

def _selected_run(tmp_path: Path, findings_for, *, edits_for=None):
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.orchestrate_test_case_revision import orchestrate_revision

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    bundle_root = run_root / "bundle"
    session, package, review, receipt = _reviewer_protocol_inputs(
        document, run_root, attempt_id, bundle_root=bundle_root, findings=findings_for(document),
        edits=edits_for(document) if edits_for else None)
    result = orchestrate_revision(document, receipt, package, session, review, bundle_root, run_root=run_root, attempt_id=attempt_id)
    assert result.status == "EFFECTIVE_SELECTED"
    return run_root, attempt_id, document


def _two_notes(document: dict) -> list:
    case_id = document["test_cases"][0]["case_id"]
    return [_finding("WARNING", case_id, related_ids=[case_id, "TC-not-in-the-document"]),
            _finding("INFO", case_id, code="вопрос аналитику: формулировка")]


def test_the_notes_are_read_from_the_selecting_review_with_safe_codes_and_known_ids(tmp_path: Path) -> None:
    from tools.run_report import review_notes

    run_root, attempt_id, document = _selected_run(tmp_path, _two_notes)
    case_id = document["test_cases"][0]["case_id"]
    notes = review_notes(run_root, attempt_id)
    assert notes["count"] == 2 and Path(notes["path"]).is_file()
    assert notes["items"][0] == {"severity": "WARNING", "code": "WORDING_IMPRECISE", "related_ids": [case_id], "dropped_ids": 1}
    assert notes["items"][1] == {"severity": "INFO", "code": None, "related_ids": [case_id], "dropped_ids": 0}
    assert [row["message"] for row in notes["texts"]] == [MESSAGE, MESSAGE]


def test_the_report_lists_the_notes_without_the_model_text(tmp_path: Path) -> None:
    from tools.run_report import write_report

    run_root, _attempt_id, document = _selected_run(tmp_path, _two_notes)
    write_report(tmp_path, run_root.name)
    report_json = (run_root / "report.json").read_text(encoding="utf-8")
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    report = json.loads(report_json)
    assert report["review_notes"]["count"] == 2 and "texts" not in report["review_notes"]
    assert "## Замечания ревью" in markdown and "WORDING_IMPRECISE" in markdown and "отброшено ID: 1" in markdown
    for text in (report_json, markdown):
        assert MESSAGE not in text and "вопрос аналитику: формулировка" not in text and "TC-not-in-the-document" not in text
    questions = (run_root / "questions-for-analyst.md").read_text(encoding="utf-8")
    assert "## Замечания ревью" in questions and MESSAGE in questions and "[WARNING WORDING_IMPRECISE]" in questions


def test_auto_fix_notes_are_shown_too(tmp_path: Path) -> None:
    from tests.test_review_view import _edit
    from tools.run_report import review_notes

    run_root, attempt_id, document = _selected_run(
        tmp_path, lambda doc: [_finding("WARNING", doc["test_cases"][0]["case_id"])], edits_for=lambda doc: [_edit(doc)])
    notes = review_notes(run_root, attempt_id)
    assert notes["count"] == 1 and notes["items"][0]["related_ids"] == [document["test_cases"][0]["case_id"]]


def test_finish_and_status_give_the_count_and_the_journal_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_finish, cmd_status

    run_root, attempt_id, _document = _selected_run(tmp_path, _two_notes)
    cmd_status(SimpleNamespace(project=str(tmp_path), run=run_root.name))
    status = json.loads(capsys.readouterr().out)
    cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name))
    finish = json.loads(capsys.readouterr().out)
    for output in (status, finish):
        assert set(output["review_notes"]) == {"count", "path"} and output["review_notes"]["count"] == 2, output
        assert Path(output["review_notes"]["path"]).is_file()
        assert "model-stage-artifacts" in output["review_notes"]["path"] and attempt_id in output["review_notes"]["path"]


def test_no_notes_no_section_and_no_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_finish, cmd_status
    from tools.run_report import review_notes, write_report

    run_root, attempt_id, _document = _selected_run(tmp_path, lambda doc: [])
    assert review_notes(run_root, attempt_id)["count"] == 0
    cmd_status(SimpleNamespace(project=str(tmp_path), run=run_root.name))
    assert "review_notes" not in json.loads(capsys.readouterr().out)
    cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name))
    assert "review_notes" not in json.loads(capsys.readouterr().out)
    write_report(tmp_path, run_root.name)
    assert "Замечания ревью" not in (run_root / "report.md").read_text(encoding="utf-8")
    assert "Замечания ревью" not in (run_root / "questions-for-analyst.md").read_text(encoding="utf-8")


# --- the autotest review: the same rule ------------------------------------------------------------

AUTOTEST_SCHEMA = ROOT / "schemas" / "autotest-reviewer-output.schema.json"


def _autotest_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, findings: list):
    from tests import test_automation_revision_budget as budget
    from tests.test_finish import _automation_chain

    original, captured = budget.review, []

    def with_findings(*args, **kwargs):
        reviewed = original(*args, **kwargs)
        reviewed["artifacts"]["autotest_review"]["findings"] = copy.deepcopy(findings)
        captured.append(reviewed)
        return reviewed

    monkeypatch.setattr(budget, "review", with_findings)
    run_root, attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])
    return run_root, attempt_id, captured[0]


def _autotest_note(severity: str = "WARNING") -> dict:
    return {"severity": severity, "code": "ASSERT_WORDING", "message": MESSAGE, "evidence": ["tests/test_products.py"],
            "related_ids": ["FILE-products", "FILE-not-generated"]}


def test_an_accepted_autotest_review_may_carry_a_warning_but_never_a_blocking_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.schema_validation import schema_diagnostics

    _run_root, _attempt_id, reviewed = _autotest_run(tmp_path, monkeypatch, [_autotest_note("WARNING"), _autotest_note("INFO")])
    assert schema_diagnostics(reviewed, AUTOTEST_SCHEMA, ROOT) == []
    blocking = copy.deepcopy(reviewed)
    blocking["artifacts"]["autotest_review"]["findings"].append(_autotest_note("BLOCKING"))
    assert schema_diagnostics(blocking, AUTOTEST_SCHEMA, ROOT)
    clean = copy.deepcopy(reviewed)
    clean["artifacts"]["autotest_review"]["findings"] = []
    assert schema_diagnostics(clean, AUTOTEST_SCHEMA, ROOT) == []


def test_autotest_notes_reach_the_report_the_questions_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_status
    from tools.run_report import review_notes, write_report

    run_root, attempt_id, _reviewed = _autotest_run(tmp_path, monkeypatch, [_autotest_note()])
    notes = review_notes(run_root, attempt_id, "autotests")
    assert notes["count"] == 1 and notes["items"] == [
        {"severity": "WARNING", "code": "ASSERT_WORDING", "related_ids": ["FILE-products"], "dropped_ids": 1}]
    assert "autotest-reviewer" not in str(review_notes(run_root, attempt_id)["path"])
    project = run_root.parents[1]
    cmd_status(SimpleNamespace(project=str(project), run=run_root.name))
    status = json.loads(capsys.readouterr().out)
    assert status["autotest_review_notes"]["count"] == 1 and Path(status["autotest_review_notes"]["path"]).is_file()
    write_report(project, run_root.name)
    report_json = (run_root / "report.json").read_text(encoding="utf-8")
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    assert json.loads(report_json)["autotest_review_notes"]["count"] == 1 and "## Замечания ревью автотестов" in markdown
    for text in (report_json, markdown):
        assert MESSAGE not in text and "FILE-not-generated" not in text
    questions = (run_root / "questions-for-analyst.md").read_text(encoding="utf-8")
    assert "## Замечания ревью автотестов" in questions and MESSAGE in questions
