from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_execution_receipt import _execution_facts
from tests.test_exit_policy import _local


def _publish_execution(root: Path, attempt_id: str, report: dict, *, request=None) -> dict:
    from tools import pilot_state
    from tools.execution_adapters import request_digest

    if request is not None:
        pilot_state.claim_execution_start(root, attempt_id, request_digest(request))
    published = pilot_state.publish_attempt_receipt(root, attempt_id, "execution-receipt", {"payload": report})
    pilot_state.append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    pilot_state.append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    return published


def _branch(root: Path, attempt_id: str, durable: dict, **extra: object) -> dict:
    from tools import pilot_state

    return {
        "policy_profile": "local-pilot-v1",
        "run_id": pilot_state.read_run(root)["manifest"]["run_id"],
        "attempt_id": attempt_id,
        # The controller must derive the V5 trace from these exact reviewed
        # artifacts.  It must not accept caller-provided trace relations.
        **durable["trace_inputs"],
        **extra,
    }


def _facts(root: Path, attempt_id: str, *, verification: str, completion: str = "COMPLETE", retained: int = 0, exact_target_pass: bool = False, reason_code: str | None = None, prior_stage_cause: str = "EXECUTION_COMPLETE") -> dict:
    from tools import pilot_state

    return _local(
        run_id=pilot_state.read_run(root)["manifest"]["run_id"],
        attempt_id=attempt_id,
        verification=verification,
        completion=completion,
        coverage="FULL",
        generated_required_count=1,
        generated_materialized_count=1,
        generated_retained_count=retained,
        exact_target_pass=exact_target_pass,
        reason_code=reason_code,
        prior_stage_cause=prior_stage_cause,
    ) | {"policy_profile": "local-pilot-v1"}


def _prestart_report(root: Path, durable: dict) -> dict:
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.run_tests import _report

    inputs = durable["trace_inputs"]
    return _report(
        "NOT_RUNNABLE", root.parent.parent, "python", inputs["automation_artifact"]["artifacts"]["source"],
        automation_sha256(inputs["automation_artifact"]), autotest_review_sha256(inputs["autotest_review"]),
        diagnostics=[{"path": "/execution", "code": "RUNNER_PRESTART", "message": "Execution cannot start."}],
    )


def _unknown_report(root: Path, attempt_id: str, durable: dict, request) -> dict:
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.execution_adapters import PYTEST
    from tools.run_tests import ProcessOutcome, _process_row, _report

    inputs = durable["trace_inputs"]
    source = inputs["automation_artifact"]["artifacts"]["source"]
    timeout = ProcessOutcome(124, "", "", "TIMEOUT")
    return _report(
        "UNKNOWN", Path(request.cwd), "python", source,
        automation_sha256(inputs["automation_artifact"]), autotest_review_sha256(inputs["autotest_review"]),
        run_id="RUN-timeout",
        process_evidence=[_process_row("TIMEOUT", timeout, "RUN-timeout", source, PYTEST, 1.0)],
        authoritative=False,
        exit_code=124,
        request=request,
        duration_sec=1.0,
        run_root=root,
        attempt_id=attempt_id,
    )


def _event_index(events: list[dict], event_type: str, digest: str) -> int:
    return next(index for index, event in enumerate(events) if event["event_type"] == event_type and event.get("artifact_digest") == digest)


def test_pass_trace_order(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, report, request=request)

    result = finalize_phase_one_spine(
        root, _branch(root, attempt_id, durable), durable["delta"], verification="PASS",
        facts=_facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True),
    )

    execution_trace = pilot_state.read_attempt_receipt(root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")
    trace_audit = pilot_state.read_attempt_receipt(root, attempt_id, "trace-audit", "ARTIFACT_READ_BACK")
    plan = pilot_state.read_attempt_receipt(root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")
    assert result["result"]["accepted"] is True
    assert execution_trace["record"]["payload"]["stage"] == "trace"
    assert execution_trace["record"]["payload"]["lifecycle"] == {"projection": "PRE_FINALIZATION", "verification": "PASS"}
    assert trace_audit["record"]["payload"]["stage"] == "trace-check"
    assert trace_audit["record"]["payload"]["valid"] is True
    events = pilot_state.derive_state(root)["events"]
    assert _event_index(events, "ARTIFACT_READ_BACK", execution_trace["digest"]) < _event_index(events, "ARTIFACT_PUBLISHED", plan["digest"])
    assert _event_index(events, "ARTIFACT_READ_BACK", trace_audit["digest"]) < _event_index(events, "ARTIFACT_PUBLISHED", plan["digest"])


def test_not_runnable_closure(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, _passed, _request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, _prestart_report(root, durable))

    result = finalize_phase_one_spine(
        root, _branch(root, attempt_id, durable), durable["delta"], verification="NOT_RUNNABLE",
        facts=_facts(root, attempt_id, verification="NOT_RUNNABLE"),
    )

    dispositions = pilot_state.read_attempt_receipt(root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [row["disposition"] for row in dispositions["files"]] == ["CLEANED"]
    assert not (tmp_path / "project" / "tests" / "test_generated.py").exists()
    assert not any(event["event_type"] == "EXECUTION_STARTED" for event in pilot_state.derive_state(root)["events"])
    assert (result["result"]["completion"], result["result"]["verification"], result["result"]["accepted"]) == ("COMPLETE", "NOT_RUNNABLE", False)


def test_sparse_unknown_closure(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.execution_adapters import request_digest
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, _passed, request, durable = _execution_facts(tmp_path)
    execution = _publish_execution(root, attempt_id, _unknown_report(root, attempt_id, durable, request), request=request)

    result = finalize_phase_one_spine(
        root, _branch(root, attempt_id, durable), durable["delta"], verification="UNKNOWN",
        facts=_facts(
            root, attempt_id, verification="UNKNOWN", completion="PARTIAL", reason_code="EXECUTION_UNKNOWN",
            prior_stage_cause="EXECUTION_UNKNOWN",
        ),
    )

    dispositions = pilot_state.read_attempt_receipt(root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [row["disposition"] for row in dispositions["files"]] == ["PRESERVED_EXECUTION_UNKNOWN"]
    assert dispositions["execution_unknown_evidence_digest"] == execution["digest"]
    assert (tmp_path / "project" / "tests" / "test_generated.py").exists()
    assert (result["result"]["completion"], result["result"]["verification"], result["result"]["reason_code"], result["result"]["accepted"]) == ("PARTIAL", "UNKNOWN", "EXECUTION_UNKNOWN", False)
    events = pilot_state.derive_state(root)["events"]
    unknown = [event for event in events if event["event_type"] == "EXECUTION_UNKNOWN"]
    assert [event["artifact_digest"] for event in unknown] == [execution["digest"]]
    assert _event_index(events, "EXECUTION_STARTED", request_digest(request)) < _event_index(events, "ARTIFACT_READ_BACK", execution["digest"])
    assert _event_index(events, "ARTIFACT_READ_BACK", execution["digest"]) < events.index(unknown[0])

    attempt = next(row for row in pilot_state.derive_state(root)["attempts"] if row["attempt_id"] == attempt_id)
    from tools.project_inventory import read_execution_baseline

    baseline = read_execution_baseline(root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    child_identity = {
        "project": attempt["project"], "module": attempt["module"], "policy_profile": attempt["policy_profile"],
        "parent_attempt_id": attempt_id, "retry_reason": "explicit-execution-retry",
    }
    with pytest.raises(ValueError, match="process-stop"):
        pilot_state.create_attempt(root, child_identity, baseline)


def test_generic_event_api_cannot_fabricate_execution_unknown(tmp_path: Path) -> None:
    from tools import pilot_state

    root, attempt_id, _passed, request, durable = _execution_facts(tmp_path)
    execution = _publish_execution(
        root, attempt_id, _unknown_report(root, attempt_id, durable, request), request=request,
    )

    with pytest.raises(ValueError, match="record_execution_unknown"):
        pilot_state.append_event(
            root, "EXECUTION_UNKNOWN", actor="controller", attempt_id=attempt_id,
            artifact_digest=execution["digest"],
        )


def test_public_execution_receipt_records_unknown_immediately_after_readback(tmp_path: Path) -> None:
    from tools import pilot_state, run_pipeline
    from tools.execution_adapters import request_digest

    root, attempt_id, _passed, request, durable = _execution_facts(tmp_path)
    state = pilot_state.derive_state(root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    run = pilot_state.read_run(root)
    coordinates = {
        "run_root": root,
        "manifest": run["manifest"],
        "authorization": run["authorization"],
        "attempt": attempt,
        "module_root": Path(attempt["project"]),
        "module": {"root": "."},
    }
    pilot_state.claim_execution_start(root, attempt_id, request_digest(request))

    receipt = run_pipeline._publish_execution_receipt(
        coordinates, _unknown_report(root, attempt_id, durable, request),
    )

    events = pilot_state.derive_state(root)["events"]
    assert events[-1]["event_type"] == "EXECUTION_UNKNOWN"
    assert events[-1]["artifact_digest"] == receipt["digest"]
    assert _event_index(events, "ARTIFACT_READ_BACK", receipt["digest"]) < len(events) - 1
    assert not (root / "disposition-receipts" / f"{attempt_id}.json").exists()
    assert not (root / "closure" / attempt_id).exists()


def test_invalid_finalization_closure(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, report, request=request)
    branch = _branch(
        root, attempt_id, durable,
        finalization_inputs={"required_artifacts": {"canonical_digest": "sha256:" + "0" * 64}},
    )

    result = finalize_phase_one_spine(
        root, branch, durable["delta"], verification="PASS",
        facts=_facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True),
    )

    receipt = pilot_state.read_closure_artifact(
        root, attempt_id, "finalization_receipt", result["finalization_receipt"]["digest"],
    )
    assert receipt["valid"] is False
    assert result["result"]["reason_code"] == "FINALIZATION_INVALID"
    assert (result["result"]["verification"], result["result"]["coverage"], result["result"]["accepted"]) == ("PASS", "FULL", False)
    assert result["terminal_trace"]["terminal_result_digest"] == result["result"]["digest"]


def test_late_resume_validation_closes_without_rewriting_pretrace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import finalize_attempt, pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, report, request=request)
    original_verify = finalize_attempt.verify_finalization
    monkeypatch.setattr(
        finalize_attempt,
        "verify_finalization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt after pretrace")),
    )

    with pytest.raises(RuntimeError, match="interrupt after pretrace"):
        finalize_phase_one_spine(
            root,
            _branch(root, attempt_id, durable),
            durable["delta"],
            verification="PASS",
            facts=_facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True),
        )

    pretrace = pilot_state.read_closure_artifact_if_present(
        root, attempt_id, "pre_finalization_trace",
    )
    assert pretrace is not None and pretrace["resume_validation_digest"] is None
    late_validation = pilot_state.publish_resume_validation(
        root, attempt_id, "BASELINE_DRIFT",
    )
    monkeypatch.setattr(finalize_attempt, "verify_finalization", original_verify)
    checked = finalize_attempt._required_trace_artifacts(pretrace)
    checked["post_pretrace_validation_digest"] = late_validation["digest"]
    receipt = finalize_attempt.verify_finalization({
        "run_id": pretrace["run_id"],
        "attempt_id": attempt_id,
        "policy_profile": pretrace["policy_profile"],
        "pre_finalization_trace_digest": pretrace["digest"],
        "post_pretrace_validation_digest": late_validation["digest"],
        "required_artifacts": checked,
    }, pretrace)
    receipt = pilot_state.publish_closure_artifact(
        root, attempt_id, "finalization_receipt", receipt,
    )["record"]

    assert pilot_state.read_closure_artifact_if_present(
        root, attempt_id, "pre_finalization_trace",
    )["digest"] == pretrace["digest"]
    assert receipt["valid"] is True
    assert receipt["errors"] == []
    assert (
        receipt["checked_artifacts"]["post_pretrace_validation_digest"]
        == late_validation["digest"]
    )
    execution = pilot_state.read_attempt_receipt(
        root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    with pytest.raises(ValueError, match="dedicated publisher"):
        pilot_state.publish_attempt_receipt(root, attempt_id, "resume-validation", {
            "baseline_digest": execution["baseline_digest"],
            "execution_request_digest": execution["execution_request_digest"],
            "execution_receipt_digest": execution["digest"],
            "status": "DRIFTED",
            "reason_code": "BASELINE_DRIFT",
        })


def test_resume_reuses_first_durable_invalid_finalization_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, report, request=request)
    invalid_branch = _branch(
        root, attempt_id, durable,
        finalization_inputs={"required_artifacts": {"canonical_digest": "sha256:" + "0" * 64}},
    )
    facts = _facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True)
    original_publish_terminal = pilot_state.publish_terminal_result
    monkeypatch.setattr(
        pilot_state,
        "publish_terminal_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt after finalization receipt")),
    )

    with pytest.raises(RuntimeError, match="interrupt after finalization receipt"):
        finalize_phase_one_spine(
            root, invalid_branch, durable["delta"], verification="PASS", facts=facts,
        )

    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (root / "closure" / attempt_id).rglob("*.json")
        if json.loads(path.read_text(encoding="utf-8")).get("stage") == "finalization"
    ]
    assert len(receipts) == 1 and receipts[0]["valid"] is False
    first_digest = receipts[0]["digest"]
    monkeypatch.setattr(pilot_state, "publish_terminal_result", original_publish_terminal)

    resumed = finalize_phase_one_spine(
        root, _branch(root, attempt_id, durable), durable["delta"], verification="PASS", facts=facts,
    )

    assert resumed["finalization_receipt"]["digest"] == first_digest
    assert resumed["finalization_receipt"]["valid"] is False
    assert resumed["result"]["reason_code"] == "FINALIZATION_INVALID"
    assert resumed["result"]["accepted"] is False
    assert len([
        path for path in (root / "closure" / attempt_id).rglob("*.json")
        if json.loads(path.read_text(encoding="utf-8")).get("stage") == "finalization"
    ]) == 1


def test_resume_recovers_installed_finalization_receipt_before_readback_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    _publish_execution(root, attempt_id, report, request=request)
    branch = _branch(root, attempt_id, durable)
    facts = _facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True)
    original_append = pilot_state.append_event

    def interrupt_finalization_readback(run_root, event_type, **kwargs):
        slot = Path(run_root) / "closure" / attempt_id / "finalization_receipt.json"
        if event_type == "ARTIFACT_READ_BACK" and slot.exists():
            value = json.loads(slot.read_text(encoding="utf-8"))
            if value.get("stage") == "finalization" and kwargs.get("artifact_digest") == value.get("digest"):
                raise RuntimeError("interrupt before finalization readback")
        return original_append(run_root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", interrupt_finalization_readback)
    with pytest.raises(RuntimeError, match="interrupt before finalization readback"):
        finalize_phase_one_spine(
            root, branch, durable["delta"], verification="PASS", facts=facts,
        )

    installed = json.loads(
        (root / "closure" / attempt_id / "finalization_receipt.json").read_text(encoding="utf-8")
    )
    finalization_events = [
        event["event_type"]
        for event in pilot_state.derive_state(root)["events"]
        if event.get("artifact_digest") == installed["digest"]
    ]
    assert finalization_events == ["ARTIFACT_PUBLISHED"]

    monkeypatch.setattr(pilot_state, "append_event", original_append)
    resumed = finalize_phase_one_spine(
        root, branch, durable["delta"], verification="PASS", facts=facts,
    )

    assert resumed["finalization_receipt"] == installed
    assert resumed["result"]["accepted"] is True
    assert [
        event["event_type"]
        for event in pilot_state.derive_state(root)["events"]
        if event.get("artifact_digest") == installed["digest"]
    ] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]


def test_preexecution_closure_is_rejected_before_disposition_after_execution_started(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import FinalizationError
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, _report, request, durable = _execution_facts(tmp_path)
    pilot_state.claim_execution_start(root, attempt_id, request_digest(request))
    facts = _facts(
        root, attempt_id, verification="NOT_APPLICABLE", completion="PARTIAL", retained=0,
        reason_code="MATERIALIZATION_INCOMPLETE", prior_stage_cause="MATERIALIZATION_INCOMPLETE",
    )
    facts.update({"execution_applicability": "NOT_APPLICABLE", "exact_target_pass": False})
    generated = tmp_path / "project" / "tests" / "test_generated.py"

    with pytest.raises(FinalizationError, match="EXECUTION_STARTED"):
        finalize_phase_one_spine(
            root, _branch(root, attempt_id, durable), durable["delta"],
            verification="NOT_APPLICABLE", facts=facts,
        )

    assert generated.exists()
    for directory in ("disposition-plans", "disposition-receipts", "terminal-results"):
        assert not (root / directory / f"{attempt_id}.json").exists()
    assert not (root / "closure" / attempt_id).exists()
    assert not any(event["event_type"] == "ATTEMPT_TERMINAL" for event in pilot_state.derive_state(root)["events"])
