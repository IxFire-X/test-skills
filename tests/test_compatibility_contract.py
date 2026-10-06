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
        _executed("tc-reviewer", "canonical-reviewer", "canonical-reviewer-v2", "tc-reviewer:canonical:part-000001", model_id="model-reviewer" if verified_fields else None, invocation_id="reviewer-1", kind="model-stage-artifact"),
        _not_applicable("tc-to-autotest", "tc-to-autotest:na", "PROFILE_CASES_ONLY"),
        _not_applicable("autotest-reviewer", "autotest-reviewer:na", "PROFILE_CASES_ONLY"),
    ]
    value = {
        "schema_version": "2.0.0", "compatibility_version": "portable-cli-v1",
        "release_manifest_digest": manifest["digest"],
        "host": {"host_id": "host-1" if verified_fields else None, "runtime_version": "cli-1.0" if verified_fields else None, "os": "windows" if verified_fields else None, "language_runtime": "python-3.12" if verified_fields else None},
        "role_policy": "portable-pilot-roles-v1", "profile": "cases-only-v1",
        "branch": {"canonical_status": "EFFECTIVE", "batch_ids": ["BATCH-fixture"], "automation_eligible": False, "automation_revisions": []},
        "registry": manifest["registry"],
        "durable_context": {"project": "C:/forged", "run_id": "run-forged", "attempt_id": "a" * 32, "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": "sha256:" + "6" * 64, "reviewer_session_digest": "sha256:" + "7" * 64},
        "stages": rows,
        "resume": {"attempt_id": "a" * 32, "resume_validation_digest": None},
        "review_bindings": [{"kind": "canonical", "generator_stage_instance_ids": ["tc-generator:BATCH-fixture"], "reviewer_stage_instance_ids": ["tc-reviewer:canonical:part-000001"], "reviewer_session_digest": "sha256:" + "7" * 64, "reviewer_boundary_digest": "sha256:" + "8" * 64, "review_aggregate_receipt_digest": "sha256:" + "9" * 64}],
    }
    value["digest"] = _digest(value)
    return value


def _durable_evidence(manifest, run_root: Path, attempt_id: str):
    from tools.pilot_state import (derive_state, read_attempt_receipt, read_reviewer_session_ledger,
                                   read_run, read_model_request, read_review_aggregate, read_review_plan)
    run, state = read_run(run_root), derive_state(run_root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    ledger = read_reviewer_session_ledger(run_root, attempt_id)
    package = read_attempt_receipt(run_root, attempt_id, "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]["package_binding"]
    rows = [_executed("orchestrate", "controller", "orchestrate-v1", "orchestrate:attempt", digest=attempt["digest"], input_digests=[manifest["digest"]])]
    for event in state["events"]:
        if event.get("attempt_id") != attempt_id or event["event_type"] != "MODEL_REQUESTED":
            continue
        instance = event["stage_instance_id"]
        request = read_model_request(run_root, attempt_id, instance, event["artifact_digest"])
        response = next(item for item in state["events"] if item.get("attempt_id") == attempt_id and item.get("stage_instance_id") == instance and item["event_type"] == "MODEL_RESPONSE_RECEIVED")
        rows.append(_executed(request["stage"], request["role"], request["role_policy"], instance,
            model_id=request["model_id"], invocation_id=request["invocation_id"], kind="model-stage-artifact",
            digest=response["artifact_digest"], input_digests=request["input_digests"]))
    revisions = sorted({int(row["stage_instance_id"].split(":")[1][1:]) for row in rows if row["stage"] == "tc-to-autotest"})
    bindings = []
    for key in ["canonical", *[f"r{revision}" for revision in revisions]]:
        session = read_reviewer_session_ledger(run_root, attempt_id, review_key=key)
        plan = read_review_plan(run_root, attempt_id, key)
        parts = [*plan["parts"], *[event["part"] for event in session["events"] if event["event_type"] == "REVIEW_CHECK_ADDED"]]
        stage = "tc-reviewer" if key == "canonical" else "autotest-reviewer"
        boundary = read_attempt_receipt(run_root, attempt_id, "reviewer-session-boundary" if key == "canonical" else f"automation-review-boundary-{key}", "ARTIFACT_READ_BACK")
        binding = {"kind": "canonical" if key == "canonical" else "automation",
            "reviewer_stage_instance_ids": [f"{stage}:{key}:{part['part_id']}" for part in parts],
            "reviewer_boundary_digest": boundary["digest"], "review_aggregate_receipt_digest": read_review_aggregate(run_root, attempt_id, key)["digest"]}
        if key == "canonical":
            binding.update(generator_stage_instance_ids=[f"tc-generator:{item['batch_id']}" for item in package["generator_batches"]], reviewer_session_digest=session["digest"])
        else:
            binding.update(revision=int(key[1]), generator_stage_instance_id=f"tc-to-autotest:{key}")
        bindings.append(binding)
    value = {
        "schema_version": "2.0.0", "compatibility_version": "portable-cli-v1", "release_manifest_digest": manifest["digest"],
        "host": {"host_id": "host-1", "runtime_version": "cli-1.0", "os": "windows", "language_runtime": "python-3.12"}, "role_policy": "portable-pilot-roles-v1",
        "profile": attempt["policy_profile"], "branch": {"canonical_status": "EFFECTIVE", "batch_ids": [item["batch_id"] for item in package["generator_batches"]], "automation_eligible": True, "automation_revisions": revisions}, "registry": manifest["registry"],
        "durable_context": {"project": run["manifest"]["project"], "run_id": run["manifest"]["run_id"], "attempt_id": attempt_id, "module": attempt["module"], "policy_profile": attempt["policy_profile"], "baseline_digest": attempt["baseline_digest"], "reviewer_session_digest": ledger["digest"]},
        "stages": rows, "resume": {"attempt_id": attempt_id, "resume_validation_digest": None}, "review_bindings": bindings,
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
