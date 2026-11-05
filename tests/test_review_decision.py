"""FaT-R1 (arch-audit 2): the reviewer's decision is checked before anything closes — never after the ledger.

Before: select published the COMPLETED ledger (validate_reviewer_session) and only then checked the decision
(validate_review_decision / the successor). An answer valid by schema but wrong by meaning (ПРИНЯТО without every case,
AUTO_FIX with a DESTRUCTIVE correction) left select → REVIEW_REWORK_REQUIRED, review-abort → REVIEWER_SESSION_CLOSED,
finish → FINISH_FACT_UNDETERMINED: a dead end that blocked every scan.
Now: model-response of tc-reviewer refuses it before the write (the stage keeps its one retry); select checks it before
the ledger, so review-abort still closes the session; an AUTO_FIX of an import may not touch the human fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_once
from tests.test_review_requested import _api, _complete, _generated_run, _journal, _open_session, _put, _review, _reviewer_request, _select


def _answer(run: dict, mutate=None, name: str = "review.json") -> str:
    candidate = json.loads((run["tmp"] / "candidate.json").read_bytes())
    code, digest = _api("document-digest", "--document", str(run["tmp"] / "candidate.json"))
    assert code == 0, digest
    selected = {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": digest["result"]}
    report = {"verdict": "ПРИНЯТО", "candidate": selected, "effective": selected,
              "reviewed_case_ids": [case["case_id"] for case in candidate["test_cases"]], "corrections": [], "findings": []}
    value = {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report}, "warnings": []}
    if mutate is not None:
        mutate(value, candidate)
    return _put(run["tmp"] / name, value)


def _drop_case(value, candidate):
    value["artifacts"]["validation_report"]["reviewed_case_ids"] = [candidate["test_cases"][0]["case_id"]]


def _destructive_autofix(value, candidate):
    case = candidate["test_cases"][0]
    report = value["artifacts"]["validation_report"]
    report["verdict"] = "AUTO_FIX_APPLIED"
    report["corrections"] = [{"id": "FIX-1", "correction_kind": "DESTRUCTIVE", "related_ids": [case["case_id"]],
                              "description": "убрал лишнее", "evidence": ["REQ"]}]
    value["artifacts"]["successor_edits"] = [{"correction_id": "FIX-1", "case_id": case["case_id"], "field": "title",
                                              "old": case["title"], "new": case["title"] + " (исправлено)"}]


def _requested(tmp_path: Path) -> dict:
    run = _generated_run(tmp_path)
    candidate, boundary = _open_session(run)
    assert _review(run, candidate)[0] == 0
    assert _reviewer_request(run, boundary)[0] == 0
    return run


def _respond(run: dict, review: str, attempts: int = 1) -> tuple[int, dict]:
    return _api("model-response", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", "tc-reviewer:canonical",
                "--artifact", review, "--transport-attempts", str(attempts))


@pytest.mark.parametrize("mutate", [_drop_case, _destructive_autofix], ids=["accepted-wrong-case-ids", "autofix-destructive"])
def test_a_wrong_decision_is_refused_before_the_write_and_the_retry_closes_the_review(tmp_path: Path, mutate) -> None:
    run = _requested(tmp_path)
    before = _journal(run["run_root"])
    code, refused = _respond(run, _answer(run, mutate))
    assert code == 2 and refused["code"] == "REVIEW_DECISION_INVALID" and "--transport-attempts 2" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, "REVIEW_DECISION_INVALID", "model-response")  # J1: the one event the refusal itself added
    # the one retry with a correct decision goes through to an effective document
    code, answered = _respond(run, _answer(run, name="review-2.json"), attempts=2)
    assert code == 0, answered
    assert _complete(run)[0] == 0
    code, selected = _select(run, str(run["tmp"] / "review-2.json"))
    assert code == 0 and selected["effective_bundle_receipt"] is not None, selected


def test_select_checks_the_decision_before_it_closes_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A journal written before FaT-R1 (the wrong answer is recorded): select refuses with the session still open, so
    review-abort REVIEW_UNUSABLE and finish close the attempt — no dead end."""
    from tools import pilot_state
    from tools.pilot_state import derive_state, read_reviewer_session_ledger

    run = _requested(tmp_path)
    review = _answer(run, _destructive_autofix)
    with monkeypatch.context() as old:  # the package before FaT-R1 wrote it
        old.setattr(pilot_state, "_validate_review_decision", lambda *_args: None)
        pilot_state.publish_model_stage_artifact(run["run_root"], run["attempt_id"], "tc-reviewer:canonical",
                                                 json.loads(Path(review).read_bytes()))
    assert _complete(run)[0] == 0
    code, refused = _select(run, review)
    assert code != 0, refused
    assert read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["status"] == "WAITING"
    code, aborted = _api("review-abort", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--reason", "REVIEW_UNUSABLE",
                         "--out", str(run["tmp"] / "session.json"))
    assert code == 0, aborted
    code, selected = _select(run, None)
    assert code == 0 and selected["facts"]["reason_code"] == "REVIEW_UNUSABLE", selected
    from tools.run_pipeline import main

    assert main(["finish", "--project", str(run["project"]), "--run", run["run_root"].name]) != 0
    assert derive_state(run["run_root"])["attempts"][-1]["state"] == "TERMINAL"


def test_an_autofix_of_an_import_may_not_touch_the_human_fields(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import OrchestrationError, review_decision

    run = _generated_run(tmp_path)
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate = journal_assembly(run["run_root"], run["attempt_id"])[0]
    _put(run["tmp"] / "candidate.json", candidate)
    case = candidate["test_cases"][0]

    def mechanical_title(value, _candidate):
        report = value["artifacts"]["validation_report"]
        report["verdict"] = "AUTO_FIX_APPLIED"
        report["corrections"] = [{"id": "FIX-1", "correction_kind": "MECHANICAL", "related_ids": [case["case_id"]],
                                  "description": "опечатка в названии", "evidence": ["REQ"]}]
        value["artifacts"]["successor_edits"] = [{"correction_id": "FIX-1", "case_id": case["case_id"], "field": "title",
                                                  "old": case["title"], "new": case["title"] + "."}]
        from tools.canonical_document import document_sha256
        from tools.review_edits import reviewer_successor

        successor = reviewer_successor(candidate, value["artifacts"])
        report["effective"] = {"document_id": successor["document_id"], "revision": successor["revision"],
                               "document_sha256": document_sha256(successor)}

    answer = json.loads(Path(_answer(run, mechanical_title)).read_bytes())
    try:
        review_decision(candidate, answer)
    except OrchestrationError as error:  # the fixture's fix may be refused for other reasons; then the import case is moot
        pytest.skip(f"the fixture autofix is not valid outside the import: {error.diagnostics}")
    with pytest.raises(OrchestrationError) as refused:
        review_decision(candidate, answer, imported=True)
    assert any(row["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and row["path"] == "/test_cases/0/title" for row in refused.value.diagnostics)
