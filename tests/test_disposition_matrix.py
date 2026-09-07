import hashlib

from tests.test_generated_delta import _inputs


def _delta(tmp_path, *, two=False, fail_after=None):
    from tools.generated_delta import materialize_delta

    def add_second(automation):
        source = "def test_second(): pass\n"
        digest = "sha256:" + hashlib.sha256(source.encode()).hexdigest()
        automation["artifacts"]["generated_files"].append({"file_id": "FILE-second", "path": "tests/test_second.py", "language": "python", "framework": "pytest", "content": source, "content_digest": digest})
        automation["artifacts"]["generated_symbols"].append({"file_id": "FILE-second", "symbol_id": "SYMBOL-second", "locator": {"kind": "python_module_function", "function_name": "test_second"}})
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


def test_pass_retains_complete_owned_set_only_after_valid_pretrace(tmp_path):
    from tools.finalize_attempt import decide_dispositions, disposition_receipt

    delta, run_root, attempt_id = _delta(tmp_path)
    result = decide_dispositions(tmp_path, delta, "PASS", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    assert [row["disposition"] for row in result["files"]] == ["RETAINED"]
    receipt = disposition_receipt(result, verification="PASS")
    assert receipt["files"][0]["disposition"] == "RETAINED"


def test_pass_with_invalid_trace_cleans_exact_owned_file_instead_of_retaining_it(tmp_path):
    from tools.finalize_attempt import decide_dispositions

    delta, run_root, attempt_id = _delta(tmp_path)
    generated = tmp_path / "tests" / "test_products.py"

    result = decide_dispositions(
        tmp_path, delta, "PASS", pre_trace_valid=False,
        run_root=run_root, attempt_id=attempt_id,
    )

    assert result["files"][0]["disposition"] == "CLEANED"
    assert not generated.exists()


def test_unknown_never_cleans_and_records_drift_as_content_conflict(tmp_path):
    import pytest
    from tools.finalize_attempt import FinalizationError, decide_dispositions

    delta, run_root, attempt_id = _delta(tmp_path)
    with pytest.raises(FinalizationError, match="execution-unknown evidence"):
        decide_dispositions(tmp_path, delta, "UNKNOWN", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    evidence = "sha256:" + "a" * 64
    unchanged = decide_dispositions(tmp_path, delta, "UNKNOWN", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id, execution_unknown_evidence_digest=evidence)
    assert unchanged["files"][0]["disposition"] == "PRESERVED_EXECUTION_UNKNOWN"
    assert (tmp_path / "tests" / "test_products.py").exists()
    drift_root = tmp_path / "drift"; drift_root.mkdir()
    drift, drift_run_root, drift_attempt_id = _delta(drift_root)
    (drift_root / "tests" / "test_products.py").write_text("user change", encoding="utf-8")
    conflict = decide_dispositions(drift_root, drift, "UNKNOWN", pre_trace_valid=True, run_root=drift_run_root, attempt_id=drift_attempt_id, execution_unknown_evidence_digest=evidence)
    assert conflict["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert (drift_root / "tests" / "test_products.py").read_text(encoding="utf-8") == "user change"


def test_unknown_missing_file_still_gets_complete_conflict_disposition(tmp_path):
    from tools.finalize_attempt import decide_dispositions

    delta, run_root, attempt_id = _delta(tmp_path)
    (tmp_path / "tests" / "test_products.py").unlink()

    result = decide_dispositions(
        tmp_path, delta, "UNKNOWN", pre_trace_valid=True,
        run_root=run_root, attempt_id=attempt_id,
        execution_unknown_evidence_digest="sha256:" + "b" * 64,
    )

    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert result["files"][0]["reason_code"] == "CONTENT_CONFLICT"


def test_unknown_unsafe_file_still_gets_complete_conflict_disposition(tmp_path, monkeypatch):
    from tools import generated_delta
    from tools.confined_output import OutputConfinementError
    from tools.finalize_attempt import decide_dispositions

    delta, run_root, attempt_id = _delta(tmp_path)
    original_read = generated_delta.read_confined_bytes

    def reject_generated_file(project, root, target):
        if target.name == "test_products.py":
            raise OutputConfinementError("unsafe generated path")
        return original_read(project, root, target)

    monkeypatch.setattr(generated_delta, "read_confined_bytes", reject_generated_file)
    result = decide_dispositions(
        tmp_path, delta, "UNKNOWN", pre_trace_valid=True,
        run_root=run_root, attempt_id=attempt_id,
        execution_unknown_evidence_digest="sha256:" + "c" * 64,
    )

    assert result["files"][0]["disposition"] == "PRESERVED_CONTENT_CONFLICT"
    assert result["files"][0]["reason_code"] == "CONTENT_CONFLICT"
