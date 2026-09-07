import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


def _contract(root: Path) -> dict:
    return json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))


def test_validator_cache_is_keyed_by_every_local_schema_byte(tmp_path: Path) -> None:
    from tools.schema_validation import validator_for

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    draft = "https://json-schema.org/draft/2020-12/schema"
    main = {
        "$schema": draft,
        "$id": "schemas/main.schema.json",
        "$ref": "shared.schema.json",
    }
    shared = {
        "$schema": draft,
        "$id": "schemas/shared.schema.json",
        "type": "object",
    }
    (schemas / "main.schema.json").write_text(json.dumps(main), encoding="utf-8")
    shared_path = schemas / "shared.schema.json"
    shared_path.write_text(json.dumps(shared), encoding="utf-8")

    first = validator_for(Path("schemas/main.schema.json"), tmp_path)
    second = validator_for(Path("schemas/main.schema.json"), tmp_path)
    assert second is first
    assert not list(first.iter_errors({}))

    shared["type"] = "array"
    shared_path.write_text(json.dumps(shared), encoding="utf-8")
    changed = validator_for(Path("schemas/main.schema.json"), tmp_path)
    assert changed is not first
    assert list(changed.iter_errors({}))

    from tools.schema_validation import schema_diagnostics
    assert schema_diagnostics([], Path("schemas/main.schema.json"), tmp_path) == []
    assert schema_diagnostics((), Path("schemas/main.schema.json"), tmp_path)
    shared["minItems"] = 1
    shared_path.write_text(json.dumps(shared), encoding="utf-8")
    assert schema_diagnostics([], Path("schemas/main.schema.json"), tmp_path)


def test_pipeline_schema_rejects_unknown_nested_fields_everywhere(pack_root: Path) -> None:
    schema = json.loads((pack_root / "schemas" / "pipeline.schema.json").read_text(encoding="utf-8"))
    original = _contract(pack_root)
    for field in ("schema_registry", "artifact_registry", "adapter_registry", "policy_profiles", "stage_registry", "projection_profiles", "event_order"):
        contract = copy.deepcopy(original)
        contract[field][0]["unknown"] = True
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(contract)
    contract = copy.deepcopy(original)
    contract["result_axes"]["accepted"]["unknown"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(contract)


def test_pipeline_schema_rejects_empty_registry_identifier(pack_root: Path) -> None:
    schema = json.loads((pack_root / "schemas" / "pipeline.schema.json").read_text(encoding="utf-8"))
    contract = _contract(pack_root)
    contract["artifact_registry"][0]["id"] = ""
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(contract)


def test_pipeline_schema_rejects_false_release_readiness(pack_root: Path) -> None:
    schema = json.loads((pack_root / "schemas" / "pipeline.schema.json").read_text(encoding="utf-8"))
    contract = _contract(pack_root)
    next(row for row in contract["artifact_registry"] if row["id"] == "compatibility_evidence")["semantic_ready"] = False
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(contract)
    contract = _contract(pack_root)
    next(row for row in contract["schema_registry"] if row["id"] == "release-eval-receipt.schema.json")["semantic_ready"] = False
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(contract)
    contract = _contract(pack_root)
    contract["release_qualification"]["ready_tuple"] = {"asserted": True}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(contract)


def _known_boundaries_are_closed(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "object" and "properties" in value and value.get("additionalProperties") is not False:
            return False
        return all(_known_boundaries_are_closed(child) for child in value.values())
    if isinstance(value, list):
        return all(_known_boundaries_are_closed(child) for child in value)
    return True


@pytest.mark.parametrize("name", ["pilot-common.schema.json", "run-manifest.schema.json", "event.schema.json", "attempt.schema.json", "run-authorization-receipt.schema.json", "terminal-result.schema.json", "finalization-receipt.schema.json"])
def test_phase_one_schemas_are_versioned_draft_closed(pack_root: Path, name: str) -> None:
    schema = json.loads((pack_root / "schemas" / name).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"schemas/{name}"
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert _known_boundaries_are_closed(schema)


@pytest.mark.parametrize("name", ["tc-to-autotest-output.schema.json", "autotest-reviewer-output.schema.json", "generated-delta.schema.json", "materialization-receipt.schema.json", "disposition-receipt.schema.json"])
def test_phase_five_schemas_are_draft_identified_and_closed(pack_root: Path, name: str) -> None:
    schema = json.loads((pack_root / "schemas" / name).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"schemas/{name}"
    assert _known_boundaries_are_closed(schema)


@pytest.mark.parametrize("name", ["pre-finalization-trace.schema.json", "derived-terminal-trace.schema.json"])
def test_phase_seven_closure_schemas_are_versioned_draft_closed(pack_root: Path, name: str) -> None:
    schema = json.loads((pack_root / "schemas" / name).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"schemas/{name}"
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert _known_boundaries_are_closed(schema)


@pytest.mark.parametrize("name", ["compatibility-evidence.schema.json", "release-eval-run.schema.json", "release-eval-receipt.schema.json", "release-manifest.schema.json"])
def test_phase_eight_schemas_are_versioned_draft_closed(pack_root: Path, name: str) -> None:
    schema = json.loads((pack_root / "schemas" / name).read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == f"schemas/{name}"
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["additionalProperties"] is False
    assert _known_boundaries_are_closed(schema)


def test_phase_seven_closure_schemas_bind_abort_not_applicable_and_finalization_identity(pack_root: Path) -> None:
    digest = "sha256:" + "a" * 64
    pre_schema = json.loads((pack_root / "schemas" / "pre-finalization-trace.schema.json").read_text(encoding="utf-8"))
    pre = {
        "schema_version": "1.0.0", "stage": "pre_finalization_trace", "policy_profile": "cases-only-v1",
        "run_id": "a" * 32, "attempt_id": "b" * 32, "canonical_digest": digest,
        "effective_canonical_digest": digest,
        "reviewer": {"digest": digest, "session_complete": False, "authoritative_verdict": None, "authoritative_verdict_count": 0, "pre_verdict_abort": True, "isolation": "independence_unverified"},
        "execution_trace": {"applicability": "NOT_APPLICABLE", "trace_receipt_digest": None, "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None}, "execution": {},
        "evidence": {"materialization": "NOT_APPLICABLE", "execution": "NOT_APPLICABLE", "dispositions": "NOT_APPLICABLE"},
        "generated_delta_digest": None, "disposition_receipt_digest": None, "disposition_verification": None,
        "resume_validation_digest": None,
        "dispositions": [], "stage_causes": [], "digest": digest,
    }
    Draft202012Validator(pre_schema).validate(pre)
    invalid_reviewer = copy.deepcopy(pre)
    invalid_reviewer["reviewer"]["authoritative_verdict"] = "ACCEPTED"
    with pytest.raises(ValidationError):
        Draft202012Validator(pre_schema).validate(invalid_reviewer)
    early_terminal = copy.deepcopy(pre)
    early_terminal["canonical_digest"] = None
    early_terminal["effective_canonical_digest"] = None
    Draft202012Validator(pre_schema).validate(early_terminal)
    materialized = {
        **pre,
        "policy_profile": "local-pilot-v1",
        "reviewer": {"digest": digest, "session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1, "pre_verdict_abort": False, "isolation": "verified"},
        "execution_trace": {"applicability": "PRESENT", "trace_receipt_digest": digest, "trace_sha256": digest, "audit_receipt_digest": digest, "audit_valid": True},
        "evidence": {"materialization": "PRESENT", "execution": "PRESENT", "dispositions": "PRESENT"},
        "generated_delta_digest": digest, "disposition_receipt_digest": digest, "disposition_verification": "PASS",
        "dispositions": [{"file_id": "FILE-feature", "path": "tests/test_feature.py", "content_digest": digest, "ownership_digest": digest, "baseline_absent": True, "materialization": "MATERIALIZED", "disposition": "RETAINED"}],
    }
    Draft202012Validator(pre_schema).validate(materialized)
    missing_generated_binding = copy.deepcopy(materialized)
    missing_generated_binding["generated_delta_digest"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(pre_schema).validate(missing_generated_binding)

    receipt_schema = json.loads((pack_root / "schemas" / "finalization-receipt.schema.json").read_text(encoding="utf-8"))
    receipt = {
        "schema_version": "1.0.0", "stage": "finalization", "run_id": "a" * 32, "attempt_id": "b" * 32,
        "policy_profile": "cases-only-v1", "pre_finalization_trace_digest": digest, "valid": True,
        "errors": [], "checked_artifacts": {"canonical_digest": digest, "effective_canonical_digest": digest, "reviewer_session_digest": digest}, "digest": digest,
    }
    Draft202012Validator(receipt_schema).validate(receipt)
    del receipt["attempt_id"]
    with pytest.raises(ValidationError):
        Draft202012Validator(receipt_schema).validate(receipt)
    early_receipt = {
        "schema_version": "1.0.0", "stage": "finalization", "run_id": "a" * 32, "attempt_id": "b" * 32,
        "policy_profile": "cases-only-v1", "pre_finalization_trace_digest": digest, "valid": False,
        "errors": ["REVIEW_CONTEXT_LIMIT"], "checked_artifacts": {}, "digest": digest,
    }
    Draft202012Validator(receipt_schema).validate(early_receipt)

    terminal_schema = json.loads((pack_root / "schemas" / "derived-terminal-trace.schema.json").read_text(encoding="utf-8"))
    terminal = {
        "schema_version": "1.0.0", "stage": "terminal_trace", "policy_profile": "cases-only-v1",
        "pre_finalization_trace_digest": digest, "finalization_receipt_digest": digest, "terminal_result_digest": digest,
        "run_id": "a" * 32, "attempt_id": "b" * 32, "digest": digest,
    }
    Draft202012Validator(terminal_schema).validate(terminal)
    del terminal["policy_profile"]
    with pytest.raises(ValidationError):
        Draft202012Validator(terminal_schema).validate(terminal)


def test_authorization_schema_rejects_secret_like_unknown_fields(pack_root: Path) -> None:
    schema = json.loads((pack_root / "schemas" / "run-authorization-receipt.schema.json").read_text(encoding="utf-8"))
    document = {"schema_version": "1.0.0", "request_id": "request-1", "policy_profile": "cases-only-v1", "execution_requested": False, "digest": "sha256:" + "0" * 64, "credential": "secret"}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)
