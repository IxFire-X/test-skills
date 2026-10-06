"""Regression tests for the 2026-10-05 pipeline review: bounded review retries (B10)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_reviewer_protocol import _digest, _host, _prepared


def _assessment(part: dict) -> dict:
    return {"coverage": [{"scope_id": scope["scope_id"], "status": "CHECKED", "evidence": ["fixture"], "assessment": "Fixture semantic assessment."} for scope in part["scopes"]],
            "findings": [], "corrections": [], "required_checks": []}


def _events(root: Path, attempt: str, stage_prefix: str = "tc-reviewer:canonical:") -> list[tuple[str, str]]:
    from tools.pilot_state import derive_state
    return [(event["event_type"], event["stage_instance_id"]) for event in derive_state(root)["events"]
            if event.get("attempt_id") == attempt and str(event.get("stage_instance_id", "")).startswith(stage_prefix)]


def _complete_remaining(root: Path, attempt: str) -> dict:
    from tools.pilot_state import finish_review, next_review_part, open_review_part, submit_review_part
    while (part := next_review_part(root, attempt)) is not None:
        opened = open_review_part(root, attempt, "canonical", _host(part["part_id"], invocation=f"host-{part['part_id']}-final"))
        submit_review_part(root, attempt, "canonical", opened["input"]["part_id"], _assessment(part))
    return finish_review(root, attempt)


def test_b10_transport_failure_reopens_the_part_with_a_new_invocation(tmp_path: Path) -> None:
    from tools.pilot_state import fail_review_part, next_review_part, open_review_part, read_reviewer_session_ledger, submit_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part = next_review_part(root, attempt)
    part_id = part["part_id"]
    first = open_review_part(root, attempt, "canonical", _host(part_id, invocation="invocation-1"))
    assert first["stage_instance_id"] == f"tc-reviewer:canonical:{part_id}"

    failed = fail_review_part(root, attempt, "canonical", part_id, "TRANSPORT", "network timeout after 120 s")
    assert failed == {"part_id": part_id, "failed_try": 1, "failure_class": "TRANSPORT", "retry_allowed": True, "tries_left": 2}
    # Repeating the report of the same failure is idempotent.
    assert fail_review_part(root, attempt, "canonical", part_id, "TRANSPORT", "network timeout after 120 s") == failed

    again = next_review_part(root, attempt)
    assert again is not None and again["part_id"] == part_id and again == part
    with pytest.raises(ValueError, match="fresh reviewer invocation"):
        open_review_part(root, attempt, "canonical", _host(part_id, invocation="invocation-1"))
    second = open_review_part(root, attempt, "canonical", _host(part_id, invocation="invocation-2"))
    assert second["stage_instance_id"] == f"tc-reviewer:canonical:{part_id}-try2"
    assert second["boundary"]["reviewer_invocation_id"] == "invocation-2"
    submit_review_part(root, attempt, "canonical", part_id, _assessment(part), transport_attempts=3)

    # Every try is in the journal: request and response of try 1, request and response of try 2.
    assert _events(root, attempt) == [
        ("REVIEW_REQUESTED", f"tc-reviewer:canonical:{part_id}"), ("MODEL_REQUESTED", f"tc-reviewer:canonical:{part_id}"),
        ("MODEL_RESPONSE_RECEIVED", f"tc-reviewer:canonical:{part_id}"),
        ("REVIEW_REQUESTED", f"tc-reviewer:canonical:{part_id}-try2"), ("MODEL_REQUESTED", f"tc-reviewer:canonical:{part_id}-try2"),
        ("MODEL_RESPONSE_RECEIVED", f"tc-reviewer:canonical:{part_id}-try2"),
    ]
    retries = [event for event in read_reviewer_session_ledger(root, attempt, review_key="canonical")["events"] if event["event_type"] == "REVIEW_PART_RETRY"]
    assert retries == [{"ordinal": 2, "event_type": "REVIEW_PART_RETRY", "part_id": part_id, "failed_try": 1, "failure_class": "TRANSPORT", "reason": "network timeout after 120 s"}]

    finished = _complete_remaining(root, attempt)
    assert finished["session"]["status"] == "COMPLETED" and finished["aggregate"]["complete"] and finished["aggregate"]["eligible"]


def test_b10_invalid_content_can_be_retried_and_three_failed_tries_block_the_part(tmp_path: Path) -> None:
    from tools.pilot_state import fail_review_part, finish_review, next_review_part, open_review_part, read_reviewer_session_ledger, submit_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part = next_review_part(root, attempt)
    part_id = part["part_id"]
    for number in (1, 2, 3):
        open_review_part(root, attempt, "canonical", _host(part_id, invocation=f"invocation-{number}"))
        with pytest.raises(ValueError, match="invalid model stage artifact"):
            submit_review_part(root, attempt, "canonical", part_id, {"coverage": [], "findings": [], "corrections": [], "required_checks": []})
        outcome = fail_review_part(root, attempt, "canonical", part_id, "CONTENT", "assessment did not match the part schema")
        assert outcome["failed_try"] == number and outcome["retry_allowed"] is (number < 3) and outcome["tries_left"] == 3 - number

    following = next_review_part(root, attempt)
    assert following is None or following["part_id"] != part_id
    with pytest.raises(ValueError):
        fail_review_part(root, attempt, "canonical", part_id, "CONTENT", "fourth")
    blocked = [event for event in read_reviewer_session_ledger(root, attempt, review_key="canonical")["events"] if event["event_type"] == "REVIEW_PART_BLOCKED"]
    assert blocked == [{"ordinal": 4, "event_type": "REVIEW_PART_BLOCKED", "part_id": part_id, "reason": "assessment did not match the part schema", "failure_class": "CONTENT"}]

    while (part := next_review_part(root, attempt)) is not None:
        open_review_part(root, attempt, "canonical", _host(part["part_id"]))
        submit_review_part(root, attempt, "canonical", part["part_id"], _assessment(part))
    finished = finish_review(root, attempt)
    assert finished["session"]["status"] == "ABORTED" and not finished["aggregate"]["complete"]
    assert finished["session"]["events"][-1] == {"ordinal": len(finished["session"]["events"]), "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REWORK"}


def test_b10_transport_block_is_not_recorded_as_rework(tmp_path: Path) -> None:
    from tools.pilot_state import fail_review_part, finish_review, next_review_part, open_review_part, submit_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part_id = next_review_part(root, attempt)["part_id"]
    for number in (1, 2, 3):
        open_review_part(root, attempt, "canonical", _host(part_id, invocation=f"invocation-{number}"))
        fail_review_part(root, attempt, "canonical", part_id, "TRANSPORT", "connection reset")
    while (part := next_review_part(root, attempt)) is not None:
        open_review_part(root, attempt, "canonical", _host(part["part_id"]))
        submit_review_part(root, attempt, "canonical", part["part_id"], _assessment(part))
    finished = finish_review(root, attempt)

    assert finished["session"]["status"] == "ABORTED"
    assert finished["session"]["events"][-1]["reason_code"] == "REVIEW_TRANSPORT_FAILED"
    assert not any(event["event_type"] == "AUTHORITATIVE_VERDICT" for event in finished["session"]["events"])


def test_b10_explicit_block_records_its_failure_class(tmp_path: Path) -> None:
    from tools.pilot_state import block_review_part, finish_review, next_review_part, read_reviewer_session_ledger

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part_id = next_review_part(root, attempt)["part_id"]
    with pytest.raises(ValueError, match="failure class"):
        block_review_part(root, attempt, "canonical", part_id, "host is offline", failure_class="NETWORK")
    block_review_part(root, attempt, "canonical", part_id, "host is offline", failure_class="TRANSPORT")
    blocked = [event for event in read_reviewer_session_ledger(root, attempt, review_key="canonical")["events"] if event["event_type"] == "REVIEW_PART_BLOCKED"]
    assert blocked[0]["failure_class"] == "TRANSPORT"
    remaining = next_review_part(root, attempt)
    if remaining is None:
        assert finish_review(root, attempt)["session"]["events"][-1]["reason_code"] == "REVIEW_TRANSPORT_FAILED"


def test_b10_failure_needs_an_open_try_and_a_known_class(tmp_path: Path) -> None:
    from tools.pilot_state import fail_review_part, next_review_part, open_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part_id = next_review_part(root, attempt)["part_id"]
    with pytest.raises(ValueError, match="no open invocation"):
        fail_review_part(root, attempt, "canonical", part_id, "TRANSPORT", "timeout")
    open_review_part(root, attempt, "canonical", _host(part_id))
    for failure_class, reason in (("NETWORK", "timeout"), ("TRANSPORT", ""), ("TRANSPORT", "   ")):
        with pytest.raises(ValueError):
            fail_review_part(root, attempt, "canonical", part_id, failure_class, reason)


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_b10_reviewer_response_accepts_up_to_three_transport_attempts(tmp_path: Path, attempts: int) -> None:
    from tools.pilot_state import derive_state, next_review_part, open_review_part, submit_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part = next_review_part(root, attempt)
    open_review_part(root, attempt, "canonical", _host(part["part_id"]))
    submit_review_part(root, attempt, "canonical", part["part_id"], _assessment(part), transport_attempts=attempts)
    response = [event for event in derive_state(root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and str(event.get("stage_instance_id", "")).startswith("tc-reviewer:")]
    assert [event["transport_attempts"] for event in response] == [attempts]


def test_b10_fourth_transport_attempt_is_rejected(tmp_path: Path) -> None:
    from tools.pilot_state import next_review_part, open_review_part, submit_review_part

    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    part = next_review_part(root, attempt)
    open_review_part(root, attempt, "canonical", _host(part["part_id"]))
    with pytest.raises(ValueError, match="transport attempt"):
        submit_review_part(root, attempt, "canonical", part["part_id"], _assessment(part), transport_attempts=4)


def test_b10_review_cli_exposes_fail_part_and_three_transport_attempts() -> None:
    from tools.orchestrate_test_case_revision import main

    for argv in (["fail-part", "--help"], ["submit-part", "--help"], ["block-part", "--help"]):
        with pytest.raises(SystemExit) as stopped:
            main(argv)
        assert stopped.value.code == 0


def test_b10_transport_abort_is_a_valid_unaccepted_terminal_reason() -> None:
    from tests.test_exit_policy import _facts
    from tools.pilot_state import exit_code, terminal_result

    result = terminal_result(_facts(completion="PARTIAL", reason_code="REVIEW_TRANSPORT_FAILED", reviewer_session_complete=False,
                                    reviewer_pre_verdict_abort=True, authoritative_verdict=None, authoritative_verdict_count=0), "cases-only-v1")
    assert result["accepted"] is False and result["reason_code"] == "REVIEW_TRANSPORT_FAILED" and exit_code(result) == 1
