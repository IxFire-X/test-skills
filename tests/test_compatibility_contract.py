import ast
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _executed(
    stage, role, policy, instance, *, model_id=None, invocation_id=None,
    kind="attempt", digest=None, input_digests=None, output_digests=None,
):
    output_digest = digest or "sha256:" + "1" * 64
    return {
        "stage_instance_id": instance, "stage": stage, "status": "EXECUTED",
        "role": role, "role_policy": policy, "model_id": model_id,
        "invocation_id": invocation_id,
        "input_digests": input_digests or ["sha256:" + "0" * 64],
        "output_digests": output_digests or [output_digest],
        "artifact_ref": {"kind": kind, "digest": output_digest},
    }


def _not_applicable(stage, instance, reason, digest=None):
    return {
        "stage_instance_id": instance, "stage": stage, "status": "NOT_APPLICABLE",
        "reason_code": reason,
        "branch_evidence_ref": {"kind": "effective-canonical", "digest": digest or "sha256:" + "4" * 64},
    }


def _evidence(manifest, *, verified_fields=False):
    """A schema-valid but intentionally untrusted declaration for forgery tests."""
    rows = [
        _executed("orchestrate", "controller", "orchestrate-v1", "orchestrate:attempt"),
        _executed("context-marker", "generator", "context-marker-v1", "context-marker:baseline"),
        _executed("tc-generator", "generator", "tc-generator-v1", "tc-generator:BATCH-fixture", model_id="model-generator" if verified_fields else None, invocation_id="generator-1", kind="effective-canonical"),
        _executed("tc-reviewer", "canonical-reviewer", "canonical-reviewer-v1", "tc-reviewer:canonical", model_id="model-reviewer" if verified_fields else None, invocation_id="reviewer-1", kind="model-stage-artifact"),
        _not_applicable("tc-to-autotest", "tc-to-autotest:na", "PROFILE_CASES_ONLY"),
        _not_applicable("autotest-reviewer", "autotest-reviewer:na", "PROFILE_CASES_ONLY"),
    ]
    value = {
        "schema_version": "1.0.0", "compatibility_version": "portable-cli-v1",
        "release_manifest_digest": manifest["digest"],
        "host": {"host_id": "host-1" if verified_fields else None, "runtime_version": "cli-1.0" if verified_fields else None, "os": "windows" if verified_fields else None, "language_runtime": "python-3.12" if verified_fields else None},
        "role_policy": "portable-pilot-roles-v1", "profile": "cases-only-v1",
        "branch": {"canonical_status": "EFFECTIVE", "batch_ids": ["BATCH-fixture"], "automation_eligible": False, "automation_revisions": []},
        "registry": manifest["registry"],
        "durable_context": {"project": "C:/forged", "run_id": "run-forged", "attempt_id": "a" * 32, "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": "sha256:" + "6" * 64, "reviewer_session_digest": "sha256:" + "7" * 64},
        "stages": rows,
        "resume": {"attempt_id": "a" * 32, "resume_validation_digest": None},
        "review_bindings": [{"kind": "canonical", "generator_stage_instance_ids": ["tc-generator:BATCH-fixture"], "reviewer_stage_instance_id": "tc-reviewer:canonical", "reviewer_session_digest": "sha256:" + "7" * 64, "reviewer_boundary_digest": "sha256:" + "8" * 64}],
    }
    value["digest"] = _digest(value)
    return value


def _durable_evidence(manifest, run_root: Path, attempt_id: str):
    from tools.pilot_state import (
        derive_state,
        read_attempt_receipt,
        read_effective_canonical,
        read_execution_inputs,
        read_reviewer_session_ledger,
        read_run,
    )
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.project_inventory import read_execution_baseline

    run, state = read_run(run_root), derive_state(run_root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    ledger = read_reviewer_session_ledger(run_root, attempt_id)
    boundary = read_attempt_receipt(run_root, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")
    effective = read_effective_canonical(run_root, attempt_id)
    inputs = read_execution_inputs(run_root, attempt_id)
    revision = inputs["autotest_review"]["artifacts"]["autotest_review"]["automation_revision"]
    auto_kind = f"automation-review-boundary-r{revision}"
    auto_boundary = read_attempt_receipt(run_root, attempt_id, auto_kind, "ARTIFACT_READ_BACK")
    baseline = read_execution_baseline(
        run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json")
    )
    package = ledger["package_binding"]
    context_marker_digest = package["context_marker_output_digest"]
    from tools.pilot_state import read_model_stage_artifact
    fragments = {
        batch["batch_id"]: read_model_stage_artifact(
            run_root, attempt_id, f"tc-generator:{batch['batch_id']}", batch["digest"],
        )["artifact"]
        for batch in package["generator_batches"]
    }
    automation_digest = automation_sha256(inputs["automation_artifact"])
    review_digest = autotest_review_sha256(inputs["autotest_review"])
    reviewer_output_digest = next(
        event["artifact_digest"] for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == "tc-reviewer:canonical"
        and event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
    )
    generator_rows = [
        _executed(
            "tc-generator", "generator", "tc-generator-v1", f"tc-generator:{batch['batch_id']}",
            model_id="model-generator", invocation_id=(ledger["generator_invocation_id"] if index == 0 else f"{ledger['generator_invocation_id']}-{index + 1}"),
            kind="model-stage-artifact", digest=batch["digest"],
            input_digests=[
                context_marker_digest, batch["context_receipt_digest"],
                fragments[batch["batch_id"]]["plan_digest"],
                fragments[batch["batch_id"]]["header_digest"],
            ],
            output_digests=[batch["digest"]],
        )
        for index, batch in enumerate(package["generator_batches"])
    ]
    rows = [
        _executed("orchestrate", "controller", "orchestrate-v1", "orchestrate:attempt", digest=attempt["digest"], input_digests=[manifest["digest"]]),
        _executed("context-marker", "generator", "context-marker-v1", "context-marker:baseline", model_id="model-context", invocation_id=f"context-{attempt_id}", kind="model-stage-artifact", digest=context_marker_digest, input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]], output_digests=[context_marker_digest]),
        *generator_rows,
        _executed("tc-reviewer", "canonical-reviewer", "canonical-reviewer-v1", "tc-reviewer:canonical", model_id="model-reviewer", invocation_id=ledger["reviewer_invocation_id"], kind="model-stage-artifact", digest=reviewer_output_digest, input_digests=[package["package_digest"]]),
        _executed("tc-to-autotest", "automation-generator", "tc-to-autotest-v1", f"tc-to-autotest:r{revision}", model_id="model-automation", invocation_id=auto_boundary["record"]["generator_invocation_id"], kind="execution-inputs", digest=inputs["digest"], input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"]], output_digests=[automation_digest]),
        _executed("autotest-reviewer", "automation-reviewer", "autotest-static-reviewer-v1", f"autotest-reviewer:r{revision}", model_id="model-automation-reviewer", invocation_id=auto_boundary["record"]["reviewer_invocation_id"], kind=auto_kind, digest=auto_boundary["digest"], input_digests=[automation_digest, auto_boundary["digest"]], output_digests=[review_digest]),
    ]
    value = {
        "schema_version": "1.0.0", "compatibility_version": "portable-cli-v1", "release_manifest_digest": manifest["digest"],
        "host": {"host_id": "host-1", "runtime_version": "cli-1.0", "os": "windows", "language_runtime": "python-3.12"}, "role_policy": "portable-pilot-roles-v1",
        "profile": attempt["policy_profile"], "branch": {"canonical_status": "EFFECTIVE", "batch_ids": [item["batch_id"] for item in package["generator_batches"]], "automation_eligible": True, "automation_revisions": [revision]}, "registry": manifest["registry"],
        "durable_context": {"project": run["manifest"]["project"], "run_id": run["manifest"]["run_id"], "attempt_id": attempt_id, "module": attempt["module"], "policy_profile": attempt["policy_profile"], "baseline_digest": attempt["baseline_digest"], "reviewer_session_digest": ledger["digest"]},
        "stages": rows, "resume": {"attempt_id": attempt_id, "resume_validation_digest": None},
        "review_bindings": [
            {"kind": "canonical", "generator_stage_instance_ids": [f"tc-generator:{item['batch_id']}" for item in package["generator_batches"]], "reviewer_stage_instance_id": "tc-reviewer:canonical", "reviewer_session_digest": ledger["digest"], "reviewer_boundary_digest": boundary["digest"]},
            {"kind": "automation", "revision": revision, "generator_stage_instance_id": f"tc-to-autotest:r{revision}", "reviewer_stage_instance_id": f"autotest-reviewer:r{revision}", "reviewer_boundary_digest": auto_boundary["digest"]},
        ],
    }
    value["digest"] = _digest(value)
    return value


def test_raw_self_digested_structural_json_never_earns_compatible_or_verified(pack_root: Path):
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.release_manifest import build_release_manifest

    manifest = build_release_manifest(pack_root)
    evidence = _evidence(manifest, verified_fields=True)
    schema = json.loads((pack_root / "schemas" / "compatibility-evidence.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(evidence)
    result = validate_compatibility_evidence(evidence, manifest)
    assert result["level"] == "incompatible"
    assert "DURABLE_CONTEXT_REQUIRED" in result["errors"]
    assert result["level"] != "verified"


def test_actual_durable_run_binds_reviewer_boundary_stage_readbacks_and_resume(pack_root: Path, tmp_path: Path):
    from tests.test_execution_receipt import _execution_facts
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.pilot_state import derive_state, read_model_request
    from tools.release_manifest import build_release_manifest

    run_root, attempt_id, _report, _request, _durable = _execution_facts(tmp_path)
    manifest = build_release_manifest(pack_root)
    evidence = _durable_evidence(manifest, run_root, attempt_id)
    generator = next(row for row in evidence["stages"] if row["stage"] == "tc-generator")
    request_event = next(
        event for event in derive_state(run_root)["events"]
        if event.get("event_type") == "MODEL_REQUESTED"
        and event.get("stage_instance_id") == generator["stage_instance_id"]
    )
    assert read_model_request(
        run_root, attempt_id, generator["stage_instance_id"], request_event["artifact_digest"],
    )["input_digests"] == generator["input_digests"]
    assert validate_compatibility_evidence(evidence, manifest, pack_root=pack_root, run_root=run_root) == {
        "level": "compatible", "independence": "verified", "errors": [],
    }


def test_forged_invocation_or_receipt_or_cross_attempt_context_fails_closed(pack_root: Path, tmp_path: Path):
    from tests.test_execution_receipt import _execution_facts
    from tools.compatibility_contract import evidence_digest, validate_compatibility_evidence
    from tools.release_manifest import build_release_manifest

    run_root, attempt_id, _report, _request, _durable = _execution_facts(tmp_path)
    manifest = build_release_manifest(pack_root)
    for mutate in (
        lambda value: value["stages"][2].update(invocation_id="forged-generator"),
        lambda value: value["stages"][3]["artifact_ref"].update(digest="sha256:" + "f" * 64),
        lambda value: value["durable_context"].update(attempt_id="f" * 32),
    ):
        evidence = _durable_evidence(manifest, run_root, attempt_id)
        mutate(evidence)
        evidence["digest"] = evidence_digest(evidence)
        result = validate_compatibility_evidence(evidence, manifest, pack_root=pack_root, run_root=run_root)
        assert result["level"] == "incompatible"


def test_raw_self_digested_release_eval_receipt_cannot_elevate_verified(pack_root: Path, tmp_path: Path):
    from tests.test_execution_receipt import _execution_facts
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.release_manifest import build_release_manifest

    run_root, attempt_id, _report, _request, _durable = _execution_facts(tmp_path)
    manifest = build_release_manifest(pack_root)
    evidence = _durable_evidence(manifest, run_root, attempt_id)
    forged_receipt = {"ready": True, "tuple": {"skill_pack_digest": manifest["skill_pack_digest"]}}
    forged_receipt["digest"] = _digest(forged_receipt)
    result = validate_compatibility_evidence(evidence, manifest, forged_receipt, pack_root=pack_root, run_root=run_root)
    assert result["level"] == "incompatible"
    assert "RELEASE_EVAL_READBACK_REQUIRED" in result["errors"]


def test_controller_modules_do_not_import_an_llm_provider(pack_root: Path):
    forbidden = {"openai", "anthropic", "google.generativeai", "litellm"}
    imported = set()
    for root in (pack_root / "tools", pack_root / "evals"):
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
    assert not {name for name in imported if any(name == item or name.startswith(item + ".") for item in forbidden)}
