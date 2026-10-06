from pathlib import Path
import pytest


def _facts(run_id: str, attempt_id: str, *, reason: str, verdict: str | None, abort: bool) -> dict:
    from tests.test_exit_policy import _facts as base_facts

    return base_facts(
        run_id=run_id,
        attempt_id=attempt_id,
        completion="PARTIAL",
        verification="NOT_APPLICABLE",
        coverage=None,
        reason_code=reason,
        prior_stage_cause=reason,
        reviewer_session_complete=not abort,
        reviewer_pre_verdict_abort=abort,
        reviewer_isolation_state="independence_unverified" if abort else "verified",
        authoritative_verdict=verdict,
        authoritative_verdict_count=0 if abort else 1,
    ) | {"policy_profile": "cases-only-v1"}


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_rejected_reviewer_branch_closes_without_effective_canonical(tmp_path: Path, profile: str) -> None:
    from tests.test_reviewer_protocol import _new_run
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_requirement_traceability import canonical_fixture
    from tests.helpers import complete_review_parts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_attempt
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    run_root, attempt_id = _new_run(tmp_path, profile)
    _, binding, _, _ = _reviewer_protocol_inputs(canonical_fixture(), run_root, attempt_id, complete=False)
    finished = complete_review_parts(run_root, attempt_id, findings=[{
        "severity": "BLOCKING", "code": "SOURCE_OMISSION", "message": "Original condition omitted.",
        "evidence": ["docs/feature.md"], "related_ids": ["TC-001"],
    }])
    session, review = finished["session"], finished["output"]
    validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]

    closed = finalize_attempt(
        {"attempt_id": attempt_id, "evidence": {}, "stage_causes": ["REWORK"]},
        None,
        project=tmp_path,
        verification="NOT_APPLICABLE",
        facts=_facts(run_id, attempt_id, reason="REWORK", verdict="REJECTED", abort=False) | {"policy_profile": profile},
        run_root=run_root,
    )

    assert closed["pre_finalization_trace"]["effective_canonical_digest"] is None
    assert closed["pre_finalization_trace"]["reviewer"]["authoritative_verdict"] == "REJECTED"
    assert closed["finalization_receipt"]["valid"] is True
    assert closed["result"]["reason_code"] == "REWORK"
    assert closed["result"]["accepted"] is False


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_pre_verdict_context_abort_closes_with_zero_verdict_and_not_applicable_execution(tmp_path: Path, profile: str) -> None:
    from tests.test_reviewer_protocol import _new_run
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_requirement_traceability import canonical_fixture
    from tools import pilot_state
    from tools.finalize_attempt import finalize_attempt
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    run_root, attempt_id = _new_run(tmp_path, profile)
    _, binding, _, _ = _reviewer_protocol_inputs(canonical_fixture(), run_root, attempt_id, complete=False, byte_budget=1100)
    session = pilot_state.finish_review(run_root, attempt_id)["session"]
    validate_reviewer_session(session, binding, run_root=run_root, attempt_id=attempt_id)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]

    closed = finalize_attempt(
        {
            "attempt_id": attempt_id,
            "evidence": {},
            "stage_causes": ["REVIEW_CONTEXT_LIMIT"],
        },
        None,
        project=tmp_path,
        verification="NOT_APPLICABLE",
        facts=_facts(
            run_id, attempt_id, reason="REVIEW_CONTEXT_LIMIT", verdict=None, abort=True,
        ) | {"policy_profile": profile},
        run_root=run_root,
    )

    pre = closed["pre_finalization_trace"]
    assert pre["effective_canonical_digest"] is None
    assert pre["reviewer"]["authoritative_verdict"] is None
    assert pre["reviewer"]["authoritative_verdict_count"] == 0
    assert pre["reviewer"]["pre_verdict_abort"] is True
    assert pre["evidence"] == {
        "materialization": "NOT_APPLICABLE",
        "execution": "NOT_APPLICABLE",
        "dispositions": "NOT_APPLICABLE",
    }
    assert closed["finalization_receipt"]["valid"] is True
    assert closed["result"]["verification"] == "NOT_APPLICABLE"
    assert closed["result"]["reason_code"] == "REVIEW_CONTEXT_LIMIT"
    assert closed["result"]["accepted"] is False
