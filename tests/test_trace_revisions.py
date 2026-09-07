from tools.finalize_attempt import build_pre_finalization_trace, derive_terminal_trace, disposition_receipt, publish_terminal_result, verify_finalization


def test_derived_terminal_trace_references_receipt_and_result_without_cycle():
    branch = {"policy_profile": "local-pilot-v1", "run_id": "a" * 32, "attempt_id": "b" * 32, "canonical_digest": "sha256:" + "1" * 64, "effective_canonical_digest": "sha256:" + "1" * 64, "reviewer": {"digest": "sha256:" + "2" * 64, "session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1, "isolation": "verified"}, "execution_trace": {"applicability": "PRESENT", "trace_receipt_digest": "sha256:" + "5" * 64, "trace_sha256": "sha256:" + "6" * 64, "audit_receipt_digest": "sha256:" + "7" * 64, "audit_valid": True}, "execution": {"verification": "PASS"}}
    dispositions = disposition_receipt({"files": [{"file_id": "FILE-x", "path": "tests/test_x.py", "content_digest": "sha256:" + "3" * 64, "ownership_digest": "sha256:" + "4" * 64, "baseline_absent": True, "materialization": "MATERIALIZED", "disposition": "RETAINED"}]}, verification="PASS")
    pre = build_pre_finalization_trace(branch, dispositions)
    receipt = verify_finalization({"pre_finalization_trace_digest": pre["digest"]}, pre)
    from tests.test_exit_policy import _local
    result = publish_terminal_result(receipt, {**_local(run_id="a" * 32, attempt_id="b" * 32, finalization_valid=True), "policy_profile": "local-pilot-v1"})
    terminal = derive_terminal_trace(pre, receipt, result)
    assert terminal["pre_finalization_trace_digest"] == pre["digest"]
    assert terminal["finalization_receipt_digest"] == receipt["digest"]
    assert terminal["terminal_result_digest"] == result["digest"]
