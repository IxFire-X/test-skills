from tools.finalize_attempt import build_pre_finalization_trace, disposition_receipt, publish_terminal_result, verify_finalization


def _branch():
    return {"policy_profile": "local-pilot-v1", "run_id": "a" * 32, "attempt_id": "b" * 32, "canonical_digest": "sha256:" + "1" * 64, "effective_canonical_digest": "sha256:" + "1" * 64, "reviewer": {"digest": "sha256:" + "2" * 64, "session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1, "isolation": "verified"}, "execution_trace": {"applicability": "PRESENT", "trace_receipt_digest": "sha256:" + "5" * 64, "trace_sha256": "sha256:" + "6" * 64, "audit_receipt_digest": "sha256:" + "7" * 64, "audit_valid": True}, "execution": {"verification": "PASS"}, "evidence": {}, "stage_causes": ["EXECUTION_COMPLETE"]}


def _dispositions():
    return disposition_receipt({"files": [{"file_id": "FILE-x", "path": "tests/test_x.py", "content_digest": "sha256:" + "3" * 64, "ownership_digest": "sha256:" + "4" * 64, "baseline_absent": True, "materialization": "MATERIALIZED", "disposition": "RETAINED"}]}, verification="PASS")


def test_invalid_finalization_still_terminalizes_and_preserves_factual_axes():
    pre = build_pre_finalization_trace(_branch(), _dispositions())
    receipt = verify_finalization({"pre_finalization_trace_digest": "sha256:" + "0" * 64}, pre)
    assert receipt["valid"] is False
    from tests.test_exit_policy import _local
    result = publish_terminal_result(receipt, {**_local(run_id="a" * 32, attempt_id="b" * 32, verification="PASS", coverage="FULL", finalization_valid=False), "policy_profile": "local-pilot-v1"})
    assert result["reason_code"] == "FINALIZATION_INVALID"
    assert result["accepted"] is False
    assert result["verification"] == "PASS" and result["coverage"] == "FULL"


def test_cases_only_explicitly_marks_execution_artifacts_not_applicable():
    branch = {**_branch(), "policy_profile": "cases-only-v1"}
    pre = build_pre_finalization_trace(branch, _dispositions())
    assert pre["evidence"]["materialization"] == "NOT_APPLICABLE"
    assert pre["evidence"]["execution"] == "NOT_APPLICABLE"


def test_durable_closure_binds_readbacks_then_terminal_event_and_derives_acceptance(tmp_path):
    from tests.test_execution_receipt import _execution_facts
    from tests.test_exit_policy import _local
    from tools.execution_adapters import request_digest
    from tools import pilot_state
    from tools.run_pipeline import finalize_phase_one_spine

    root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    pilot_state.claim_execution_start(root, attempt_id, request_digest(request))
    published = pilot_state.publish_attempt_receipt(root, attempt_id, "execution-receipt", {"payload": report})
    pilot_state.append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    pilot_state.append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    run = pilot_state.read_run(root)
    delta = durable["delta"]
    branch = {
        **_branch(),
        "run_id": run["manifest"]["run_id"],
        "attempt_id": attempt_id,
        **durable["trace_inputs"],
    }
    facts = _local(run_id=run["manifest"]["run_id"], attempt_id=attempt_id, generated_required_count=1, generated_materialized_count=1, generated_retained_count=1) | {"policy_profile": "local-pilot-v1"}
    result = finalize_phase_one_spine(root, branch, delta, verification="PASS", facts=facts)
    assert result["result"]["accepted"] is True
    events = pilot_state.derive_state(root)["events"]
    event_types = [item["event_type"] for item in events]
    terminal_index = event_types.index("ATTEMPT_TERMINAL")
    assert event_types[terminal_index - 16:terminal_index + 1] == [
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # execution trace
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # trace audit
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # disposition plan
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # disposition receipt
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # pre-finalization trace
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # finalization receipt
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # terminal result
        "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",  # terminal trace
        "ATTEMPT_TERMINAL",
    ]
    assert [(row["event_type"], row["artifact_digest"]) for row in events[terminal_index + 1:]] == [
        (event_type, observation["record"]["digest"])
        for observation in result["scenario_observation_receipts"].values()
        for event_type in ("ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK")
    ]
    assert finalize_phase_one_spine(root, branch, delta, verification="PASS", facts=facts)["idempotent"] is True


def test_false_green_minimal_trace_and_caller_accepted_are_rejected():
    from tools.finalize_attempt import FinalizationError, build_pre_finalization_trace, publish_terminal_result

    with __import__("pytest").raises(FinalizationError):
        build_pre_finalization_trace({"policy_profile": "local-pilot-v1"}, _dispositions())
    pre = build_pre_finalization_trace(_branch(), _dispositions())
    receipt = verify_finalization({"pre_finalization_trace_digest": pre["digest"]}, pre)
    from tests.test_exit_policy import _local
    with __import__("pytest").raises(FinalizationError):
        publish_terminal_result(receipt, _local() | {"policy_profile": "local-pilot-v1", "accepted": True})


def test_unpublished_or_forged_finalization_cannot_terminalize(tmp_path):
    from helpers import phase_two_baseline
    from tests.test_exit_policy import _local
    from tools import pilot_state
    from tools.finalize_attempt import FinalizationError, publish_terminal_result, verify_finalization

    run = pilot_state.create_run(tmp_path, "local-pilot-v1", {"request_id": "forged-run", "host_id": "host-1", "execution_requested": True})
    root = run["run_root"]
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    attempt = pilot_state.create_attempt(root, identity, phase_two_baseline(root, tmp_path, identity))
    pre = build_pre_finalization_trace({**_branch(), "run_id": run["manifest"]["run_id"], "attempt_id": attempt["attempt_id"]}, _dispositions())
    receipt = verify_finalization({"pre_finalization_trace_digest": pre["digest"]}, pre)
    facts = _local(run_id=run["manifest"]["run_id"], attempt_id=attempt["attempt_id"], generated_required_count=1, generated_materialized_count=1, generated_retained_count=1) | {"policy_profile": "local-pilot-v1"}
    with __import__("pytest").raises(ValueError):
        pilot_state.publish_terminal_result(root, {key: value for key, value in facts.items() if key != "policy_profile"}, "local-pilot-v1", finalization_receipt=receipt)
    with __import__("pytest").raises(FinalizationError):
        publish_terminal_result({**receipt, "valid": True}, facts)


def test_low_level_terminal_publication_cannot_claim_local_pass_without_execution_and_dispositions(tmp_path):
    from tests.test_exit_policy import _local
    from tests.test_finalization_branches import _delta
    from tools import pilot_state
    from tools.finalize_attempt import (
        _required_trace_artifacts,
        build_pre_finalization_trace,
        disposition_receipt,
        verify_finalization,
    )

    _delta_value, run_root, attempt_id = _delta(tmp_path)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    reviewer = pilot_state.terminal_reviewer_evidence(run_root, attempt_id)
    effective = pilot_state.read_effective_canonical(run_root, attempt_id)
    pre = build_pre_finalization_trace({
        "policy_profile": "local-pilot-v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "canonical_digest": reviewer["canonical_digest"],
        "effective_canonical_digest": effective["document_digest"],
        "reviewer": {key: reviewer[key] for key in (
            "digest", "session_complete", "authoritative_verdict",
            "authoritative_verdict_count", "pre_verdict_abort", "isolation",
        )},
        "execution_trace": {"applicability": "NOT_APPLICABLE", "trace_receipt_digest": None, "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None},
        "execution": {"verification": "NOT_APPLICABLE"},
        "evidence": {},
        "stage_causes": ["CANONICAL_COMPLETE"],
    }, disposition_receipt(None, verification="NOT_APPLICABLE"))
    pre = pilot_state.publish_closure_artifact(
        run_root, attempt_id, "pre_finalization_trace", pre,
    )["record"]
    receipt = verify_finalization({
        "run_id": run_id,
        "attempt_id": attempt_id,
        "policy_profile": "local-pilot-v1",
        "pre_finalization_trace_digest": pre["digest"],
        "required_artifacts": _required_trace_artifacts(pre),
    }, pre)
    assert receipt["valid"] is True
    receipt = pilot_state.publish_closure_artifact(
        run_root, attempt_id, "finalization_receipt", receipt,
    )["record"]
    facts = _local(
        run_id=run_id,
        attempt_id=attempt_id,
        generated_required_count=1,
        generated_materialized_count=1,
        generated_retained_count=1,
    )

    with __import__("pytest").raises(ValueError, match="durable trace evidence"):
        pilot_state.publish_terminal_result(
            run_root, facts, "local-pilot-v1", finalization_receipt=receipt,
        )


def test_cases_only_uses_no_generated_delta_and_invalid_receipt_still_terminalizes():
    from tests.test_exit_policy import _facts
    from tools.finalize_attempt import finalize_attempt

    branch = {**_branch(), "policy_profile": "cases-only-v1", "finalization_inputs": {"required_artifacts": {"canonical_digest": "sha256:" + "0" * 64}}}
    facts = _facts() | {"policy_profile": "cases-only-v1"}
    storage = {}
    def publish(kind, value): storage[(kind, value.get("digest", "event"))] = value; return value
    def read(kind, key):
        if kind == "terminal_result": raise KeyError(key)
        return storage[(kind, key)]
    result = finalize_attempt(branch, None, project=__import__("pathlib").Path.cwd(), verification="NOT_APPLICABLE", facts=facts, publish=publish, read=read)
    assert result["result"]["accepted"] is False
    assert result["result"]["reason_code"] == "FINALIZATION_INVALID"
    assert result["pre_finalization_trace"]["evidence"]["execution"] == "NOT_APPLICABLE"
