from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def automated_document() -> dict:
    document = canonical_fixture()
    step = document["test_cases"][0]["steps"][0]
    step.update({
        "manual_only": False,
        "manual_reason": None,
        "operation": {
            "kind": "http", "binding_profile": "http-binding-v1",
            "base_url_source": {"kind": "environment", "name": "BASE_URL", "provenance": ["test"]},
            "method": "GET", "path": "/products",
        },
        "test_data": "Тело запроса отсутствует.",
    })
    step["expectations"][0]["text"] = "Отображается список доступных товаров.\n\nHTTP 200 OK"
    step["expectations"][0]["assertions"] = [{
        "assertion_id": "ASSERT-batch-a-001", "display_order": 1,
        "actual": {"kind": "http_status"}, "operator": "equals",
        "expected": {"kind": "literal", "value": 200},
    }]
    return document


def automation(document: dict, revision: int = 1, *, predecessor: str | None = None, correction_review: str | None = None, effective_bundle_receipt_digest: str | None = None) -> dict:
    from tools.canonical_document import document_sha256

    source = {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(document), "effective_bundle_receipt_digest": effective_bundle_receipt_digest or "sha256:" + "0" * 64}
    content = b"def test_products():\n    assert True\n"
    file_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    artifacts = {
        "automation_status": "GENERATED", "source": source,
        "automation_revision": revision,
        "predecessor_automation_sha256": predecessor,
        "correction_review_sha256": correction_review,
        "generated_files": [{"file_id": "FILE-products", "path": "tests/test_products.py", "language": "python", "framework": "pytest", "content": content.decode("utf-8"), "content_digest": file_digest}],
        "generated_symbols": [{"file_id": "FILE-products", "symbol_id": "SYMBOL-products", "locator": {"kind": "python_module_function", "function_name": "test_products"}}],
        "implementation_relations": [
            {"kind": "operation", "case_id": "TC-batch-a-001", "step_id": "STEP-batch-a-001", "file_id": "FILE-products", "symbol_id": "SYMBOL-products"},
            {"kind": "assertion", "case_id": "TC-batch-a-001", "step_id": "STEP-batch-a-001", "expectation_id": "EXP-batch-a-001", "assertion_id": "ASSERT-batch-a-001", "file_id": "FILE-products", "symbol_id": "SYMBOL-products"},
        ],
        "manual_dispositions": [], "diagnostics": [],
    }
    return {"schema_version": "5.0.0", "stage": "tc-to-autotest", "artifacts": artifacts, "warnings": []}


def host_evidence(session_id: str, *, generator: str = "generator-1", reviewer: str = "reviewer-1") -> dict:
    return {
        "generator_invocation_id": generator, "reviewer_invocation_id": reviewer,
        "fresh_context": True, "distinct_invocations": True,
    }


def durable_boundary(tmp_path, artifact: dict, session_id: str, evidence: dict, *, run_root: Path | None = None, attempt_id: str | None = None, generator: str = "generator-1", reviewer: str = "reviewer-1", policy_profile: str = "cases-only-v1", document: dict | None = None) -> tuple[Path, str, dict]:
    from tests.test_reviewer_protocol import _new_run
    from tools.automation_validation import automation_sha256
    from tools.pilot_state import open_automation_review_boundary, publish_model_request, publish_model_stage_artifact

    if run_root is None or attempt_id is None:
        if policy_profile == "cases-only-v1":
            run_root, attempt_id = _new_run(tmp_path)
        else:
            from tests.helpers import build_phase_two_baseline
            from tools.run_pipeline import run_phase_one_spine

            (tmp_path / "tests").mkdir(exist_ok=True)
            identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
            baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
            run = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "auto-r2", "execution_requested": True}, identity, baseline, {})
            run_root = Path(run["run"]["run_root"])
            attempt_id = run["state"]["attempts"][0]["attempt_id"]
    if document is not None:
        from tests.test_generated_delta import _effective_bundle, _published_reviewer_ledger
        from tools.pilot_state import publish_effective_canonical, read_effective_canonical

        try:
            selected = read_effective_canonical(run_root, attempt_id)
        except (KeyError, TypeError, ValueError):
            publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
        else:
            if selected.get("document") != document:
                raise ValueError("test fixture effective selection mismatch")
    body = {key: evidence[key] for key in ("fresh_context", "distinct_invocations")}
    body["evidence_digest"] = _digest(body)
    revision = artifact["artifacts"]["automation_revision"]
    generator_stage = f"tc-to-autotest:r{revision}"
    publish_model_request(
        run_root, attempt_id, generator_stage,
        model_id="model-automation", invocation_id=generator,
        input_digests=[
            artifact["artifacts"]["source"]["source_digest"],
            artifact["artifacts"]["source"]["effective_bundle_receipt_digest"],
        ],
    )
    publish_model_stage_artifact(run_root, attempt_id, generator_stage, artifact)
    receipt = open_automation_review_boundary(run_root, attempt_id, {
        "automation_digest": automation_sha256(artifact), "automation_revision": artifact["artifacts"]["automation_revision"],
        "effective_canonical_digest": artifact["artifacts"]["source"]["source_digest"],
        "effective_bundle_receipt_digest": artifact["artifacts"]["source"]["effective_bundle_receipt_digest"],
        "reviewer_session_id": session_id, "generator_invocation_id": generator, "reviewer_invocation_id": reviewer,
        "role_policy": "autotest-static-reviewer-v1", "host_isolation": body,
    })
    return run_root, attempt_id, dict(receipt["record"])


def review(document: dict, artifact: dict, evidence: dict, verdict: str, *, session_id: str) -> dict:
    from tools.automation_validation import (
        automation_sha256,
        implementation_relations_sha256,
    )

    generated = artifact["artifacts"]
    return {
        "schema_version": "5.0.0", "stage": "autotest-reviewer", "warnings": [],
        "artifacts": {"autotest_review": {
            "source": generated["source"], "automation_revision": generated["automation_revision"],
            "automation_sha256": automation_sha256(artifact),
            "reviewer_session_id": session_id,
            "generator_invocation_id": evidence["generator_invocation_id"],
            "reviewer_invocation_id": evidence["reviewer_invocation_id"],
            "role_policy": "autotest-static-reviewer-v1",
            "host_isolation_sha256": evidence.get("digest", _digest(evidence)),
            "reviewed_files": [{"file_id": row["file_id"], "content_digest": row["content_digest"]} for row in generated["generated_files"]],
            "reviewed_symbol_pairs": [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in generated["generated_symbols"]],
            "reviewed_relations_sha256": implementation_relations_sha256(generated["implementation_relations"]),
            "verdict": verdict, "findings": [],
            "corrections": [] if verdict == "ПРИНЯТО" else [{"id": "FIX-products", "related_ids": ["FILE-products"], "description": "Перегенерировать полный набор.", "evidence": ["relation"]}],
        }},
    }


def codes(rows: list[dict[str, str]]) -> set[str]:
    return {row["code"] for row in rows}


def test_accepts_initial_and_one_complete_controller_bound_correction(tmp_path: Path) -> None:
    from tools.automation_validation import (
        automation_sha256,
        autotest_review_sha256,
        validate_accepted_autotest_review,
        validate_automation_revision_chain,
    )

    document = automated_document()
    from tests.test_generated_delta import _effective_bundle_digest
    bundle_digest = _effective_bundle_digest(document)
    first = automation(document, effective_bundle_receipt_digest=bundle_digest)
    first_evidence = host_evidence("session-1")
    run_root, attempt_id, first_boundary = durable_boundary(tmp_path, first, "session-1", first_evidence, policy_profile="local-pilot-v1", document=document)
    first_review = review(document, first, first_boundary, "AUTO_FIX_APPLIED", session_id="session-1")
    second_evidence = host_evidence("session-2")
    second = automation(document, 2, predecessor=automation_sha256(first), correction_review=autotest_review_sha256(first_review), effective_bundle_receipt_digest=bundle_digest)
    _run_root, _attempt_id, second_boundary = durable_boundary(tmp_path, second, "session-2", second_evidence, run_root=run_root, attempt_id=attempt_id, reviewer="reviewer-2", document=document)
    second_review = review(document, second, second_boundary, "ПРИНЯТО", session_id="session-2")

    assert validate_automation_revision_chain([first, second], [first_review, second_review], document, host_isolation_receipts=[first_boundary, second_boundary], run_root=run_root, attempt_id=attempt_id) == []
    assert validate_accepted_autotest_review(second_review, second, document, host_isolation_receipt=second_boundary, run_root=run_root, attempt_id=attempt_id) == []


def test_rejects_third_review_but_allows_completed_pre_execution_correction_after_later_fail() -> None:
    from tools.automation_validation import automation_sha256, autotest_review_sha256, validate_automation_revision_chain

    document = automated_document()
    first = automation(document)
    first_evidence = host_evidence("session-1")
    first_review = review(document, first, first_evidence, "AUTO_FIX_APPLIED", session_id="session-1")
    second_evidence = host_evidence("session-2", reviewer="reviewer-2")
    second = automation(document, 2, predecessor=automation_sha256(first), correction_review=autotest_review_sha256(first_review))
    second_review = review(document, second, second_evidence, "AUTO_FIX_APPLIED", session_id="session-2")
    third = automation(document, 3, predecessor=automation_sha256(second), correction_review=autotest_review_sha256(second_review))
    third_evidence = host_evidence("session-3", reviewer="reviewer-3")
    third_review = review(document, third, third_evidence, "ПРИНЯТО", session_id="session-3")

    assert "AUTOMATION_REVISION_BUDGET" in codes(validate_automation_revision_chain([first, second, third], [first_review, second_review, third_review], document, host_isolation_receipts=[first_evidence, second_evidence, third_evidence]))
    assert "AUTOMATION_RUNTIME_FAIL_REGENERATION" not in codes(validate_automation_revision_chain([first, second], [first_review, second_review], document, host_isolation_receipts=[first_evidence, second_evidence], runtime_verdict="FAIL"))


def test_rejects_partial_or_unbound_correction_and_missing_review_coverage() -> None:
    from tools.automation_validation import autotest_review_sha256, validate_accepted_autotest_review, validate_automation_revision_chain

    document = automated_document()
    first = automation(document)
    first_evidence = host_evidence("session-1")
    first_review = review(document, first, first_evidence, "AUTO_FIX_APPLIED", session_id="session-1")
    second_evidence = host_evidence("session-2", reviewer="reviewer-2")
    second = automation(document, 2, predecessor="sha256:" + "0" * 64, correction_review=autotest_review_sha256(first_review))
    second["artifacts"]["implementation_relations"].pop()
    second_review = review(document, second, second_evidence, "ПРИНЯТО", session_id="session-2")
    chain_codes = codes(validate_automation_revision_chain([first, second], [first_review, second_review], document, host_isolation_receipts=[first_evidence, second_evidence]))
    assert {"AUTOMATION_CORRECTION_PREDECESSOR", "AUTOMATION_MISSING_ASSERTION_COVERAGE"} <= chain_codes

    accepted = review(document, first, first_evidence, "ПРИНЯТО", session_id="session-1")
    accepted["artifacts"]["autotest_review"]["reviewed_files"] = []
    accepted["artifacts"]["autotest_review"]["reviewed_symbol_pairs"] = []
    accepted["artifacts"]["autotest_review"]["reviewed_relations_sha256"] = "sha256:" + "f" * 64
    coverage_codes = codes(validate_accepted_autotest_review(accepted, first, document, host_isolation_receipt=first_evidence))
    assert {"AUTOTEST_REVIEW_FILE_COVERAGE", "AUTOTEST_REVIEW_COVERAGE", "AUTOTEST_REVIEW_RELATION_COVERAGE"} <= coverage_codes


def test_rejects_self_attested_isolation_and_v5_schema_requires_bound_revision_fields() -> None:
    from tools.automation_validation import validate_accepted_autotest_review
    from tools.schema_validation import schema_diagnostics
    from pathlib import Path

    document = automated_document()
    artifact = automation(document)
    evidence = host_evidence("session-1")
    accepted = review(document, artifact, evidence, "ПРИНЯТО", session_id="session-1")
    assert "AUTOTEST_REVIEW_ISOLATION" in codes(validate_accepted_autotest_review(accepted, artifact, document))
    assert schema_diagnostics(artifact, Path("schemas/tc-to-autotest-output.schema.json"), Path.cwd()) == []
    assert schema_diagnostics(accepted, Path("schemas/autotest-reviewer-output.schema.json"), Path.cwd()) == []


def test_rejects_tampered_attempt_owned_static_review_boundary(tmp_path: Path) -> None:
    from tools.automation_validation import validate_accepted_autotest_review
    from tests.test_generated_delta import _effective_bundle_digest

    document = automated_document()
    artifact = automation(document, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    run_root, attempt_id, boundary = durable_boundary(tmp_path, artifact, "session-1", host_evidence("session-1"), policy_profile="local-pilot-v1", document=document)
    accepted = review(document, artifact, boundary, "ПРИНЯТО", session_id="session-1")
    boundary["reviewer_invocation_id"] = "forged-reviewer"

    assert "AUTOTEST_REVIEW_ISOLATION" in codes(
        validate_accepted_autotest_review(accepted, artifact, document, host_isolation_receipt=boundary, run_root=run_root, attempt_id=attempt_id)
    )


def test_static_review_boundary_rejects_wrong_effective_selection_before_publication(tmp_path: Path) -> None:
    from tests.helpers import build_phase_two_baseline
    from tests.test_generated_delta import _effective_bundle, _effective_bundle_digest, _published_reviewer_ledger
    from tools.automation_validation import automation_sha256
    from tools.pilot_state import open_automation_review_boundary, publish_effective_canonical
    from tools.run_pipeline import run_phase_one_spine

    (tmp_path / "tests").mkdir()
    document = automated_document()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, "local-pilot-v1", {"request_id": "wrong-effective-boundary", "execution_requested": True}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    artifact = automation(document, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    isolation = {"fresh_context": True, "distinct_invocations": True}
    isolation["evidence_digest"] = _digest(isolation)

    with pytest.raises(ValueError, match="invalid automation review boundary"):
        open_automation_review_boundary(run_root, attempt_id, {
            "automation_digest": automation_sha256(artifact), "automation_revision": 1,
            "effective_canonical_digest": "sha256:" + "f" * 64,
            "effective_bundle_receipt_digest": artifact["artifacts"]["source"]["effective_bundle_receipt_digest"],
            "reviewer_session_id": "wrong-effective", "generator_invocation_id": "generator-1",
            "reviewer_invocation_id": "reviewer-1", "role_policy": "autotest-static-reviewer-v1",
            "host_isolation": isolation,
        })
    assert not (run_root / "automation-review-boundaries" / f"{attempt_id}.r1.json").exists()


def test_generated_file_binds_exact_utf8_content_bytes() -> None:
    """The reviewed artifact, not a later caller, owns materialized bytes."""
    from tools.automation_validation import validate_automation_artifact

    document = automated_document()
    artifact = automation(document)
    row = artifact["artifacts"]["generated_files"][0]
    row["content"] = "def test_products():\n    assert True\n"

    assert validate_automation_artifact(artifact, document) == []

    row["content"] = "def test_products():\n    assert False\n"
    assert "AUTOMATION_CONTENT_DIGEST" in codes(validate_automation_artifact(artifact, document))
