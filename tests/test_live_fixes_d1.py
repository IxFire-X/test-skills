"""D1: every review outcome ends with a published terminal result, never DRIVER_FAILURE.

Live run d1834358: the aggregate was blocked (UNCHECKED scope), the closure was
written, the terminal result was not, and the driver printed DRIVER_FAILURE.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.live_step5 import Replay
from tools import pilot_state
from tools.pilot_state import read_terminal_result


def _unchecked_last_check(task: dict[str, Any], envelope: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """The recorded answers, except that the late check part could not answer either."""
    if all(scope["scope_id"].startswith("cross-") and len(scope["scope_id"]) == 70 for scope in envelope["scopes"]) and not answer["required_checks"]:
        answer["coverage"] = [{**row, "status": "UNCHECKED"} for row in answer["coverage"]]
    return answer


def _no_driver_failure(replay: Replay) -> None:
    failures = [row["payload"] for row in replay.log if row["payload"].get("action") == "error"]
    assert not failures, failures


def test_d1834358_incomplete_review_publishes_terminal_review_incomplete(tmp_path: Path) -> None:
    replay = Replay("d1834358", tmp_path, review=_unchecked_last_check)
    code, done = replay.drive()
    _no_driver_failure(replay)
    assert done["action"] == "done", done
    result = done["result"]
    assert (result["status"], result["completion"], result["verification"], result["reason_code"], result["accepted"]) == (
        "terminal", "PARTIAL", "NOT_APPLICABLE", "REVIEW_INCOMPLETE", False)
    assert code == 1
    terminal = read_terminal_result(replay.run_root(done), result["attempt_id"])
    assert terminal["evidence"]["reviewer_pre_verdict_abort"] is True
    # A repeated `next` on the terminal run prints the same result.
    code_again, again = replay.next(done["run_id"])
    assert (code_again, again["result"]["reason_code"]) == (1, "REVIEW_INCOMPLETE")


def test_next_recovers_a_run_whose_closure_was_written_without_terminal_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact state d1834358 was left in: finalization receipt valid, terminal result missing."""
    replay = Replay("d1834358", tmp_path, review=_unchecked_last_check)
    original = pilot_state._terminal_result

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("terminal facts violate policy projection")

    monkeypatch.setattr(pilot_state, "_terminal_result", broken)
    code, failed = replay.drive()
    assert (code, failed["action"]) == (2, "error"), failed
    run_id = next(row["payload"]["run_id"] for row in replay.log if row["payload"].get("run_id"))
    root = replay.project / ".pilot-runs" / run_id
    assert any(root.glob("closure/*/finalization_receipt.json"))
    monkeypatch.setattr(pilot_state, "_terminal_result", original)
    code, done = replay.next(run_id)
    assert done["action"] == "done" and done["result"]["reason_code"] == "REVIEW_INCOMPLETE", done


def test_rejected_canonical_review_publishes_terminal_rework(tmp_path: Path) -> None:
    def blocking(task: dict[str, Any], envelope: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        if envelope["part_id"] == "part-000001":
            answer["findings"].append({"severity": "BLOCKING", "code": "ORACLE_WRONG", "message": "Ожидание TC-B1-001 противоречит требованию.",
                                       "evidence": ["/test_cases/0"], "related_ids": ["TC-B1-001"]})
        return answer

    replay = Replay("2c10d733", tmp_path, review=blocking)
    code, done = replay.drive()
    _no_driver_failure(replay)
    result = done["result"]
    assert (result["status"], result["completion"], result["reason_code"], result["accepted"]) == ("terminal", "PARTIAL", "REWORK", False)


def test_pre_verdict_abort_reason_is_never_rework() -> None:
    plan = {"parts": [{"blocked_reason": None}], "additions": []}
    session = {"events": [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}]}
    assert pilot_state._review_abort_reason(plan, session) == "REVIEW_INCOMPLETE"
    blocked = {"events": [*session["events"], {"ordinal": 2, "event_type": "REVIEW_PART_BLOCKED", "part_id": "part-000001", "reason": "x", "failure_class": "CONTENT"}]}
    assert pilot_state._review_abort_reason(plan, blocked) == "REVIEW_INCOMPLETE"


def test_legacy_ledger_abort_with_rework_reads_as_review_incomplete() -> None:
    ledger = {"status": "ABORTED", "events": [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"},
                                             {"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REWORK"}]}
    assert pilot_state.reviewer_lifecycle_projection(ledger)["abort_reason"] == "REVIEW_INCOMPLETE"
