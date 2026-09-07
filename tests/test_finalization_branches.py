from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_exit_policy import _local
from tests.test_generated_delta import _inputs


def _delta(tmp_path: Path, *, two: bool = False, fail_after: int | None = None):
    from tools.generated_delta import materialize_delta

    def add_second(automation: dict) -> None:
        content = "def test_second(): pass\n"
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        automation["artifacts"]["generated_files"].append({
            "file_id": "FILE-second", "path": "tests/test_second.py", "language": "python",
            "framework": "pytest", "content": content, "content_digest": digest,
        })
        automation["artifacts"]["generated_symbols"].append({
            "file_id": "FILE-second", "symbol_id": "SYMBOL-second",
            "locator": {"kind": "python_module_function", "function_name": "test_second"},
        })
        automation["artifacts"]["implementation_relations"].extend([
            {"kind": "operation", "case_id": "TC-batch-a-001", "step_id": "STEP-batch-a-001", "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
            {"kind": "assertion", "case_id": "TC-batch-a-001", "step_id": "STEP-batch-a-001", "expectation_id": "EXP-batch-a-001", "assertion_id": "ASSERT-batch-a-001", "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
        ])
        automation["artifacts"]["implementation_relations"].sort(
            key=lambda row: (0 if row["kind"] == "operation" else 1, row["file_id"]),
        )

    baseline, automation, review, document, run_root, attempt_id = _inputs(
        tmp_path, mutate=add_second if two else None,
    )
    delta = materialize_delta(
        tmp_path, tmp_path, baseline, automation, review,
        canonical_document=document, run_root=run_root, attempt_id=attempt_id,
        fail_after=fail_after,
    )
    return delta, run_root, attempt_id


def _terminal_facts(run_id: str, attempt_id: str, *, verification: str, completion: str = "COMPLETE", coverage: str = "FULL", required: int = 1, materialized: int = 1, retained: int = 0, execution_applicability: str = "REQUIRED", exact_target_pass: bool = False, reason_code: str | None = None, prior_stage_cause: str = "EXECUTION_COMPLETE", finalization_valid: bool = True) -> dict:
    return _local(
        run_id=run_id,
        attempt_id=attempt_id,
        verification=verification,
        completion=completion,
        coverage=coverage,
        generated_required_count=required,
        generated_materialized_count=materialized,
        generated_retained_count=retained,
        execution_applicability=execution_applicability,
        exact_target_pass=exact_target_pass,
        reason_code=reason_code,
        prior_stage_cause=prior_stage_cause,
        finalization_valid=finalization_valid,
    )


@pytest.mark.parametrize("verification", ["FAIL", "NOT_RUNNABLE"])
def test_durable_fail_branches_clean_every_owned_file_and_terminalize_unaccepted(tmp_path: Path, verification: str) -> None:
    from tests.test_durable_execution_trace import _prestart_report, _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import decide_dispositions
    from tools import pilot_state

    run_root, attempt_id, report, request, durable = _execution_facts(
        tmp_path, project=tmp_path, failed=verification == "FAIL",
    )
    delta = durable["delta"]
    if verification == "FAIL":
        _publish_execution(run_root, attempt_id, report, request=request)
    else:
        _publish_execution(run_root, attempt_id, _prestart_report(run_root, durable))
    result = decide_dispositions(tmp_path, delta, verification, pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)

    assert [row["disposition"] for row in result["files"]] == ["CLEANED"]
    assert not (tmp_path / delta["files"][0]["path"]).exists()
    receipt = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert receipt["verification"] == verification
    assert receipt["files"] == [{
        key: row[key]
        for key in ("file_id", "path", "content_digest", "ownership_digest", "baseline_absent", "materialization", "disposition", "reason_code")
        if key in row
    } for row in result["files"]]

    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    terminal = pilot_state.terminal_result(
        _terminal_facts(run_id, attempt_id, verification=verification), "local-pilot-v1"
    )
    assert terminal["attempt_state"] == "TERMINAL"
    assert terminal["completion"] == "COMPLETE"
    assert terminal["verification"] == verification
    assert terminal["accepted"] is False


def test_unknown_branch_preserves_unchanged_and_drifted_files_with_exact_execution_unknown_evidence(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions
    from tools import pilot_state

    evidence = "sha256:" + "a" * 64
    delta, run_root, attempt_id = _delta(tmp_path)
    generated_path = tmp_path / "tests" / "test_products.py"
    before = generated_path.read_bytes()
    unchanged = decide_dispositions(
        tmp_path, delta, "UNKNOWN", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id,
        execution_unknown_evidence_digest=evidence,
    )
    unchanged_file = unchanged["files"][0]
    assert unchanged_file["disposition"] == "PRESERVED_EXECUTION_UNKNOWN"
    assert unchanged_file["reason_code"] == "EXECUTION_UNKNOWN"
    assert generated_path.read_bytes() == before
    unchanged_receipt = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert unchanged_receipt["execution_unknown_evidence_digest"] == evidence

    drift_root = tmp_path / "drift"
    drift_root.mkdir()
    drift_delta, drift_run_root, drift_attempt_id = _delta(drift_root)
    drift_path = drift_root / "tests" / "test_products.py"
    drift_path.write_text("user-owned change\n", encoding="utf-8")
    drift = decide_dispositions(
        drift_root, drift_delta, "UNKNOWN", pre_trace_valid=True, run_root=drift_run_root,
        attempt_id=drift_attempt_id, execution_unknown_evidence_digest=evidence,
    )
    drift_file = drift["files"][0]
    assert drift_file["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert drift_file["reason_code"] == "CONTENT_CONFLICT"
    assert drift_path.read_text(encoding="utf-8") == "user-owned change\n"
    drift_receipt = pilot_state.read_attempt_receipt(drift_run_root, drift_attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert drift_receipt["execution_unknown_evidence_digest"] == evidence


def test_partial_materialization_cleans_only_materialized_files_never_starts_execution_and_terminalizes_not_applicable(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions
    from tools import pilot_state

    delta, run_root, attempt_id = _delta(tmp_path, two=True, fail_after=1)
    assert delta["facts"] == {
        "completion": "PARTIAL", "verification": "NOT_APPLICABLE",
        "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False,
    }
    assert not any(event["event_type"] == "EXECUTION_STARTED" for event in pilot_state.derive_state(run_root)["events"])

    result = decide_dispositions(
        tmp_path, delta, "NOT_APPLICABLE", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id,
    )
    assert [(row["path"], row["disposition"]) for row in result["files"]] == [
        ("tests/test_products.py", "CLEANED"), ("tests/test_second.py", "NOT_MATERIALIZED"),
    ]
    assert not (tmp_path / "tests" / "test_products.py").exists()
    assert not (tmp_path / "tests" / "test_second.py").exists()

    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    terminal = pilot_state.terminal_result(
        _terminal_facts(
            run_id, attempt_id, completion="PARTIAL", verification="NOT_APPLICABLE", coverage="MIXED",
            required=2, materialized=1, retained=0, execution_applicability="NOT_APPLICABLE",
            exact_target_pass=False, reason_code="MATERIALIZATION_INCOMPLETE",
            prior_stage_cause="MATERIALIZATION_INCOMPLETE",
        ),
        "local-pilot-v1",
    )
    assert terminal["accepted"] is False
    assert (terminal["completion"], terminal["verification"], terminal["reason_code"]) == (
        "PARTIAL", "NOT_APPLICABLE", "MATERIALIZATION_INCOMPLETE",
    )


def test_direct_terminal_event_without_completed_closure_is_rejected(tmp_path: Path) -> None:
    from tools import pilot_state

    _delta_value, run_root, attempt_id = _delta(tmp_path)
    before = pilot_state.derive_state(run_root)
    with pytest.raises(ValueError, match="terminal result"):
        pilot_state.append_event(
            run_root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=attempt_id,
            artifact_digest="sha256:" + "f" * 64,
        )
    after = pilot_state.derive_state(run_root)
    assert after == before
    assert after["attempts"][0]["state"] != "TERMINAL"


def test_invalid_finalization_keeps_execution_facts_and_prior_cause_immutable() -> None:
    from tools.finalize_attempt import build_pre_finalization_trace, disposition_receipt, publish_terminal_result, verify_finalization

    run_id, attempt_id = "a" * 32, "b" * 32
    facts = _terminal_facts(
        run_id, attempt_id, verification="FAIL", coverage="MIXED", reason_code=None,
        prior_stage_cause="EXECUTION_FAIL", finalization_valid=False,
    ) | {"policy_profile": "local-pilot-v1"}
    branch = {
        "policy_profile": "local-pilot-v1", "run_id": run_id, "attempt_id": attempt_id,
        "canonical_digest": "sha256:" + "1" * 64,
        "effective_canonical_digest": "sha256:" + "1" * 64,
        "reviewer": {
            "digest": "sha256:" + "2" * 64, "session_complete": True,
            "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1,
            "pre_verdict_abort": False, "isolation": "verified",
        },
        "execution_trace": {
            "applicability": "PRESENT", "trace_receipt_digest": "sha256:" + "5" * 64,
            "trace_sha256": "sha256:" + "6" * 64, "audit_receipt_digest": "sha256:" + "7" * 64,
            "audit_valid": True,
        },
        "execution": {"verification": "FAIL"},
        "evidence": {}, "stage_causes": [facts["prior_stage_cause"]],
    }
    dispositions = disposition_receipt({
        "files": [{
            "file_id": "FILE-x", "path": "tests/test_x.py", "content_digest": "sha256:" + "3" * 64,
            "ownership_digest": "sha256:" + "4" * 64, "baseline_absent": True,
            "materialization": "MATERIALIZED", "disposition": "CLEANED",
        }],
    }, verification="FAIL")
    pre_trace = build_pre_finalization_trace(branch, dispositions)
    receipt = verify_finalization({
        "run_id": run_id, "attempt_id": attempt_id, "policy_profile": "local-pilot-v1",
        "pre_finalization_trace_digest": "sha256:" + "0" * 64,
    }, pre_trace)

    assert receipt["valid"] is False
    result = publish_terminal_result(receipt, facts)
    assert result["reason_code"] == "FINALIZATION_INVALID"
    assert (result["verification"], result["coverage"], result["accepted"]) == ("FAIL", "MIXED", False)
    assert pre_trace["stage_causes"] == ["EXECUTION_FAIL"]
    assert facts["prior_stage_cause"] == "EXECUTION_FAIL"


def test_post_pretrace_validation_is_bound_as_a_required_verifier_artifact() -> None:
    from tools.finalize_attempt import _finalization_verifier_inputs

    late = "sha256:" + "9" * 64
    pretrace = {
        "run_id": "a" * 32,
        "attempt_id": "b" * 32,
        "policy_profile": "local-pilot-v1",
        "digest": "sha256:" + "1" * 64,
        "canonical_digest": "sha256:" + "2" * 64,
        "resume_validation_digest": None,
    }

    inputs = _finalization_verifier_inputs(pretrace, late)

    assert inputs["post_pretrace_validation_digest"] == late
    assert inputs["required_artifacts"]["post_pretrace_validation_digest"] == late


def test_pre_execution_local_partial_tuple_requires_not_applicable_execution_contract() -> None:
    from tools import pilot_state

    facts = _terminal_facts(
        "a" * 32, "b" * 32, completion="PARTIAL", verification="NOT_APPLICABLE", coverage="MIXED",
        required=2, materialized=1, retained=0, execution_applicability="NOT_APPLICABLE",
        exact_target_pass=False, reason_code="MATERIALIZATION_INCOMPLETE",
        prior_stage_cause="MATERIALIZATION_INCOMPLETE",
    )
    result = pilot_state.terminal_result(facts, "local-pilot-v1")
    assert result["accepted"] is False
    assert (result["completion"], result["verification"], result["reason_code"]) == (
        "PARTIAL", "NOT_APPLICABLE", "MATERIALIZATION_INCOMPLETE",
    )
    with pytest.raises(ValueError, match="materialization-incomplete"):
        pilot_state.terminal_result({**facts, "execution_applicability": "REQUIRED"}, "local-pilot-v1")
