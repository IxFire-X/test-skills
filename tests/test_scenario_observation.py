"""The scenario observations of a run (``scenario-observation-receipt``): what the code of ``finish`` records when a run is finished again, resumed or
read back — the producer, the journal binding, a tampered or hand-placed file."""
import pytest
from pathlib import Path


def _terminal_retry_observation(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    second = finalize_durable_execution_attempt(run_root, attempt_id)
    assert first["result"] == second["result"] and second["idempotent"] is True
    return (
        run_root, attempt_id,
        second["scenario_observation_receipts"]["terminal-idempotent-retry"],
    )


def test_fresh_non_git_observation_is_produced_by_public_finalizer(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    assert not (tmp_path / "project" / ".git").exists()
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["fresh-non-git"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "fresh-non-git", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "NON_GIT_PROJECT"
    assert loaded["record"]["action"]["kind"] == "RUN_PIPELINE"


def test_waiting_for_model_resume_observation_is_produced_after_real_model_pair(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["waiting-for-model-resume"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "waiting-for-model-resume", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "MODEL_WAITING"
    assert loaded["record"]["action"]["kind"] == "RESUME_MODEL_ATTEMPT"
    assert [row["event_type"] for row in loaded["events"]] == [
        "WAITING_FOR_MODEL", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "ATTEMPT_TERMINAL",
    ]


def test_one_user_question_resume_observation_is_produced_after_answered_model_pair(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(
        tmp_path, wait_for_input=True,
    )
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["one-user-question-resume"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "one-user-question-resume", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "USER_QUESTION_PENDING"
    assert loaded["record"]["action"]["kind"] == "RESUME_AFTER_USER_ANSWER"
    assert len(loaded["record"]["before"]["question_digests"]) == 1
    assert loaded["record"]["before"]["answer_digests"] == []
    assert len(loaded["record"]["after"]["answer_digests"]) == 1


def test_the_observation_schemas_name_only_the_scenarios_that_can_be_observed(pack_root: Path):
    text = (pack_root / "schemas" / "scenario-observation-receipt.schema.json").read_text(encoding="utf-8")
    assert "retained-native-rerun" not in text and "terminal-idempotent-retry" in text
    from tools.pilot_state import scenario_observation_target

    with pytest.raises(ValueError):
        scenario_observation_target(Path("."), "a" * 32, "retained-native-rerun")


def test_a_journal_with_the_events_of_the_old_rerun_and_process_stop_is_still_read(tmp_path: Path):
    """Old runs are read (P4b, Q3): the writers of RETAINED_NATIVE_RERUN and PROCESS_STOPPED are gone, the event types stay known to the journal
    and to the guard, so a run that holds them is not a corrupt journal."""
    from tools import pilot_state

    assert "RETAINED_NATIVE_RERUN" in pilot_state._EVENT_TYPES and "PROCESS_STOPPED" in pilot_state._EVENT_TYPES
    assert "RETAINED_NATIVE_RERUN" in pilot_state._ATTEMPT_EVENTS and "PROCESS_STOPPED" in pilot_state._ATTEMPT_EVENTS
    with pytest.raises(ValueError, match="verified process-scope recovery"):
        pilot_state.append_event(tmp_path, "PROCESS_STOPPED", actor="controller", attempt_id="a" * 32, artifact_digest="sha256:" + "b" * 64)


def test_retained_native_rerun_event_cannot_be_appended_directly(tmp_path: Path):
    from tools.pilot_state import append_event

    with pytest.raises(ValueError, match="retained-rerun controller"):
        append_event(
            tmp_path, "RETAINED_NATIVE_RERUN", actor="controller",
            attempt_id="a" * 32, artifact_digest="sha256:" + "b" * 64,
        )


def test_terminal_retry_scenario_uses_controller_owned_durable_observation(tmp_path: Path):
    from tools.pilot_state import derive_state, read_scenario_observation

    run_root, attempt_id, published = _terminal_retry_observation(tmp_path)
    loaded = read_scenario_observation(
        run_root, attempt_id, "terminal-idempotent-retry", published["digest"],
    )
    state = derive_state(run_root)
    assert loaded.get("verified") is True
    binding = [
        row for row in state["events"]
        if row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
        and row.get("artifact_digest") == loaded["digest"]
        and row.get("attempt_id") == attempt_id
    ]
    assert binding == [loaded["binding_event"]]
    assert loaded["record"]["before"] == loaded["record"]["after"]
    assert loaded["record"]["before"]["execution_start_count"] == 1
    assert len(loaded["record"]["before"]["terminal_artifact_digests"]) == 4


def test_terminal_retry_observation_cannot_be_published_outside_second_finalize(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    before = pilot_state.derive_state(run_root)["events"]

    with pytest.raises((AttributeError, ValueError)):
        pilot_state._publish_terminal_retry_observation(
            run_root,
            attempt_id,
            first["result"],
            pilot_state._terminal_retry_snapshot(run_root, attempt_id),
        )

    assert pilot_state.derive_state(run_root)["events"] == before
    assert not (run_root / "scenario-observations" / f"{attempt_id}.terminal-idempotent-retry.json").exists()


def test_release_scenario_observation_tamper_fails_closed(tmp_path: Path):
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, published = _terminal_retry_observation(tmp_path)
    target = run_root / published["path"]
    target.write_bytes(published["bytes"].replace(b'"terminal_event_count":1', b'"terminal_event_count":2'))
    with pytest.raises(ValueError, match="scenario observation"):
        read_scenario_observation(
            run_root, attempt_id, "terminal-idempotent-retry", published["digest"],
        )


def test_hand_placed_terminal_retry_observation_without_journal_is_rejected(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import (
        _canonical_bytes, _sealed, _terminal_retry_snapshot, derive_state,
        read_scenario_observation, read_terminal_result,
    )

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    assert first.get("idempotent") is not True
    assert "terminal-idempotent-retry" not in first.get("scenario_observation_receipts", {})
    state = derive_state(run_root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    terminal = read_terminal_result(run_root, attempt_id)
    terminal_events = [
        row for row in state["events"]
        if row.get("attempt_id") == attempt_id and row.get("event_type") == "ATTEMPT_TERMINAL"
    ]
    snapshot = _terminal_retry_snapshot(run_root, attempt_id)
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"],
        "scenario_id": "terminal-idempotent-retry",
        "precondition": {"kind": "TERMINAL_ATTEMPT", "snapshot_digest": snapshot["digest"]},
        "action": {
            "kind": "RETRY_TERMINAL_ATTEMPT",
            "before_digest": snapshot["digest"], "after_digest": snapshot["digest"],
        },
        "before": snapshot, "after": snapshot,
        "event_digests": [terminal_events[0]["digest"]],
        "receipt_digests": snapshot["terminal_artifact_digests"],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    record = _sealed(value)
    target = run_root / "scenario-observations" / f"{attempt_id}.terminal-idempotent-retry.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_canonical_bytes(record))
    with pytest.raises(ValueError, match="scenario observation|terminal retry"):
        read_scenario_observation(
            run_root, attempt_id, "terminal-idempotent-retry", record["digest"],
        )

def test_a_repeated_finish_gives_the_same_result_with_one_start_and_one_terminal(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import derive_state

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    retry = finalize_durable_execution_attempt(run_root, attempt_id)
    assert retry["idempotent"] is True and retry["result"] == first["result"]
    events = [row["event_type"] for row in derive_state(run_root)["events"] if row.get("attempt_id") == attempt_id]
    assert events.count("EXECUTION_STARTED") == 1 and events.count("ATTEMPT_TERMINAL") == 1
