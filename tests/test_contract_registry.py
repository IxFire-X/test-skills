import copy
import inspect
import json
from pathlib import Path

import pytest

from tools import pilot_state
from tools.contract_check import validate_pipeline_contract


SCHEMA_ROWS = [
    ("pipeline.schema.json", 1, "IMPLEMENTED", "4.0"), ("pilot-common.schema.json", 1, "IMPLEMENTED", "1.0.0"),
    ("run-manifest.schema.json", 1, "IMPLEMENTED", "1.0.0"), ("event.schema.json", 1, "IMPLEMENTED", "2.0.0"),
    ("model-request.schema.json", 1, "IMPLEMENTED", "2.0.0"),
    ("attempt.schema.json", 1, "IMPLEMENTED", "1.0.0"), ("run-authorization-receipt.schema.json", 1, "IMPLEMENTED", "1.0.0"),
    ("terminal-result.schema.json", 1, "IMPLEMENTED", "1.0.0"), ("finalization-receipt.schema.json", 1, "IMPLEMENTED", "1.0.0"),
    ("skillsrc.schema.json", 2, "IMPLEMENTED", "5.2.0"), ("skillsrc-init-output.schema.json", 2, "IMPLEMENTED", "5.0.0"),
    ("inventory-receipt.schema.json", 2, "IMPLEMENTED", "1.0.0"), ("exclusion-receipt.schema.json", 2, "IMPLEMENTED", "1.0.0"),
    ("context-selection-receipt.schema.json", 2, "IMPLEMENTED", "1.0.0"), ("execution-baseline.schema.json", 2, "IMPLEMENTED", "1.0.0"),
    ("context-marker-output.schema.json", 3, "IMPLEMENTED", "5.1.0"), ("tc-generator-output.schema.json", 3, "IMPLEMENTED", "5.0.0"),
    ("canonical-test-document.schema.json", 3, "IMPLEMENTED", "1.0.0"), ("batch-plan.schema.json", 3, "IMPLEMENTED", "1.0.0"),
    ("candidate-fragment.schema.json", 3, "IMPLEMENTED", "1.0.0"), ("assembly-receipt.schema.json", 3, "IMPLEMENTED", "1.0.0"),
    ("tc-reviewer-output.schema.json", 4, "IMPLEMENTED", "6.0.0"), ("orchestrator-output.schema.json", 4, "IMPLEMENTED", "5.0.0"),
    ("reviewer-session.schema.json", 4, "IMPLEMENTED", "2.0.0"), ("review-plan.schema.json", 4, "IMPLEMENTED", "1.0.0"), ("review-part-output.schema.json", 4, "IMPLEMENTED", "1.0.0"),
    ("review-plan-compact.schema.json", 4, "IMPLEMENTED", "2.0.0"), ("review-part-output-compact.schema.json", 4, "IMPLEMENTED", "2.1.0"), ("tc-to-autotest-output.schema.json", 5, "IMPLEMENTED", "5.0.0"),
    ("autotest-reviewer-output.schema.json", 5, "IMPLEMENTED", "6.0.0"), ("execution-inputs-receipt.schema.json", 5, "IMPLEMENTED", "1.0.0"), ("generated-delta.schema.json", 5, "IMPLEMENTED", "1.0.0"),
    ("materialization-receipt.schema.json", 5, "IMPLEMENTED", "1.0.0"), ("disposition-receipt.schema.json", 5, "IMPLEMENTED", "1.0.0"),
    ("run-tests-output.schema.json", 6, "IMPLEMENTED", "5.0.0"), ("resume-validation-receipt.schema.json", 7, "IMPLEMENTED", "1.0.0"), ("trace-document.schema.json", 7, "IMPLEMENTED", "5.0.0"),
    ("trace-audit-output.schema.json", 7, "IMPLEMENTED", "5.0.0"), ("pre-finalization-trace.schema.json", 7, "IMPLEMENTED", "1.0.0"),
    ("derived-terminal-trace.schema.json", 7, "IMPLEMENTED", "1.0.0"),
    ("driver-summary.schema.json", 7, "IMPLEMENTED", "1.2.0"),
    ("compatibility-evidence.schema.json", 8, "IMPLEMENTED", "2.0.0"), ("retained-native-rerun-receipt.schema.json", 8, "IMPLEMENTED", "1.0.0"), ("scenario-observation-receipt.schema.json", 8, "IMPLEMENTED", "1.0.0"), ("release-eval-run.schema.json", 8, "IMPLEMENTED", "1.0.0"),
    ("release-eval-receipt.schema.json", 8, "IMPLEMENTED", "1.0.0"), ("release-manifest.schema.json", 8, "IMPLEMENTED", "1.0.0"),
]

ARTIFACT_IDS = [
    "run_manifest", "run_authorization_receipt", "attempt", "event_journal", "event", "model_request", "structured_result", "finalization_receipt",
    "skillsrc_configuration", "skillsrc_proposal_receipt", "inventory_receipt", "exclusion_receipt", "context_selection_receipt", "execution_baseline",
    "normalized_requirements", "canonical_candidate", "batch_plan", "candidate_fragment", "canonical_header", "assembly_receipt", "pre_review_audit",
    "authoritative_verdict", "candidate_bundle_receipt", "successor_bundle_receipt", "effective_canonical", "effective_bundle_receipt", "projection_bundle", "reviewer_session", "reviewer_evidence_transfer", "review_plan", "review_part_output", "review_aggregate",
    "automation", "automation_static_review", "execution_inputs", "generated_delta", "generated_file", "materialization_receipt", "disposition_receipt",
    "execution_receipt", "framework_evidence", "environment_receipt", "resume_validation_receipt", "execution_trace", "trace_audit", "orchestrator_output", "pre_finalization_trace", "terminal_result", "derived_terminal_trace", "compatibility_evidence", "retained_native_rerun_receipt", "scenario_observation_receipt", "release_eval_run", "release_eval_receipt", "release_manifest",
]

STAGE_ROWS = [
    {"stage": "orchestrate", "role": "controller", "role_policy": "orchestrate-v1", "cardinality": "once", "profiles": ["cases-only-v1", "local-pilot-v1"]},
    {"stage": "context-marker", "role": "generator", "role_policy": "context-marker-v1", "cardinality": "once", "profiles": ["cases-only-v1", "local-pilot-v1"]},
    {"stage": "tc-generator", "role": "generator", "role_policy": "tc-generator-v1", "cardinality": "per_batch", "profiles": ["cases-only-v1", "local-pilot-v1"]},
    {"stage": "tc-reviewer", "role": "canonical-reviewer", "role_policy": "canonical-reviewer-v2", "cardinality": "per_review_part", "profiles": ["cases-only-v1", "local-pilot-v1"]},
    {"stage": "tc-to-autotest", "role": "automation-generator", "role_policy": "tc-to-autotest-v1", "cardinality": "per_automation_revision", "profiles": ["local-pilot-v1"]},
    {"stage": "autotest-reviewer", "role": "automation-reviewer", "role_policy": "autotest-static-reviewer-v2", "cardinality": "per_review_part", "profiles": ["local-pilot-v1"]},
]


def _contract(root: Path) -> dict:
    return json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))


def _errors(contract: dict, root: Path) -> list[str]:
    return validate_pipeline_contract(contract, root)["errors"]


def test_frozen_registries_are_complete_ordered_and_truthful(pack_root: Path) -> None:
    contract = _contract(pack_root)
    assert [tuple(item[key] for key in ("id", "phase", "implementation_status", "target_version")) for item in contract["schema_registry"]] == SCHEMA_ROWS
    assert [item["id"] for item in contract["artifact_registry"]] == ARTIFACT_IDS
    assert len(contract["artifact_registry"]) == len(set(ARTIFACT_IDS))
    assert [item["id"] for item in contract["artifact_registry"] if item["semantic_ready"]] == ARTIFACT_IDS
    assert [item["id"] for item in contract["schema_registry"] if item["semantic_ready"]] == [row[0] for row in SCHEMA_ROWS[2:]]
    assert contract["schema_registry"][8] == {"id": "finalization-receipt.schema.json", "phase": 1, "implementation_status": "IMPLEMENTED", "target_version": "1.0.0", "semantic_ready": True}
    assert contract["artifact_registry"][7] == {"id": "finalization_receipt", "phase": 1, "implementation_status": "IMPLEMENTED", "semantic_ready": True}
    assert contract["controller_schemas"] == [row[0] for row in SCHEMA_ROWS]
    assert contract["core_skills"] == ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
    assert [item["id"] for item in contract["adapter_registry"]] == ["pytest:selected-symbols-v1", "maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1", "gradle-wrapper:selected-symbols-v1"]
    assert [item["id"] for item in contract["policy_profiles"]] == ["cases-only-v1", "local-pilot-v1"]
    assert all(item["implementation_status"] == "IMPLEMENTED" and item["semantic_ready"] is True for item in contract["adapter_registry"])
    assert all(item["semantic_ready"] is True for item in contract["policy_profiles"])
    assert {item["version"] for item in contract["adapter_registry"] + contract["policy_profiles"]} == {"v1"}
    assert contract["stage_registry"] == STAGE_ROWS
    review = contract["reviewer_session_contract"]
    assert review["logical_review_count"] == "one_per_branch_or_revision"
    assert review["session_count"] == "one_per_declared_part"
    assert review["invocation_order"] == "sequential"
    assert review["coverage"] == ["original_source", "local", "cross_part"]
    assert review["aggregation"] == "controller" and review["successful_verdicts"] == 1
    assert review["incomplete"] == "never_accepted"
    assert contract["projection_profiles"] == [
        {"id": "zephyr-scale-step-row-24-v5", "format": "csv", "mode": "default", "tenant_status": "N_A"},
        {"id": "zephyr-scale-step-row-24-v4", "format": "csv", "mode": "opt_in_compatibility", "tenant_status": "N_A"},
        {"id": "zephyr-scale-xml-observed-v1", "format": "xml", "mode": "opt_in_observed", "tenant_status": "N_A"},
    ]
    assert contract["release_qualification"] == {
        "package_version": "0.5.0-pilot", "compatibility_contract_version": "portable-cli-v1",
        "execution_profile_version": "v1", "release_eval_policy": "adaptive-1-3-5-v1",
        "scenario_suite": "pilot-critical-v1", "manifest_path": "release/manifest.json",
        "component_state": "implemented_unverified", "ready_tuple": None,
        "not_applicable": ["company_runner", "zephyr_tenant", "production_rollback"],
    }


@pytest.mark.parametrize("registry", ["schema_registry", "artifact_registry", "adapter_registry", "policy_profiles", "stage_registry", "projection_profiles"])
def test_checker_rejects_missing_duplicate_and_reordered_registry_entries(pack_root: Path, registry: str) -> None:
    original = _contract(pack_root)
    for mutation in ("missing", "duplicate", "reordered"):
        contract = copy.deepcopy(original)
        if mutation == "missing":
            contract[registry].pop()
        elif mutation == "duplicate":
            contract[registry].append(copy.deepcopy(contract[registry][0]))
        else:
            contract[registry][0], contract[registry][1] = contract[registry][1], contract[registry][0]
        expected = f"{registry} exact truth exact ordered registry mismatch" if registry in {"schema_registry", "artifact_registry"} else f"{registry} exact ordered registry mismatch"
        assert any(expected in error for error in _errors(contract, pack_root))


@pytest.mark.parametrize("field,value", [("phase", 99), ("implementation_status", "NOT_IMPLEMENTED"), ("target_version", "0.0.0")])
def test_checker_rejects_wrong_schema_registry_binding(pack_root: Path, field: str, value: object) -> None:
    contract = _contract(pack_root)
    contract["schema_registry"][23][field] = value
    assert any("schema_registry exact truth exact ordered registry mismatch" in error for error in _errors(contract, pack_root))


def test_checker_rejects_bad_skill_path_runtime_signature_lifecycle_and_result_axes(pack_root: Path) -> None:
    original = _contract(pack_root)
    mutations = [
        ("Skill", lambda c: c["skill_files"].__setitem__("orchestrate", "skills/missing/SKILL.md")),
        ("runtime signature", lambda c: c["runtime_signatures"].__setitem__("create_run", "wrong")),
        ("lifecycle", lambda c: c["event_order"][0].__setitem__("event_type", "ATTEMPT_CREATED")),
        ("result axes", lambda c: c["result_axes"]["accepted"].__setitem__("terminal", "absent")),
    ]
    for reason, mutate in mutations:
        contract = copy.deepcopy(original)
        mutate(contract)
        assert any(reason in error for error in _errors(contract, pack_root)), reason


def test_checker_rejects_existing_skill_alias_and_false_release_readiness(pack_root: Path) -> None:
    original = _contract(pack_root)
    mutations = [
        ("Skill path binding", lambda c: c["skill_files"].__setitem__("orchestrate", "skills/tc-generator/SKILL.md")),
        ("artifact_registry exact truth", lambda c: c["artifact_registry"][-3].__setitem__("semantic_ready", False)),
        ("schema_registry exact truth", lambda c: c["schema_registry"][-1].__setitem__("semantic_ready", False)),
        ("release qualification", lambda c: c["release_qualification"].__setitem__("component_state", "ready")),
        ("release qualification", lambda c: c["release_qualification"]["not_applicable"].pop()),
    ]
    for reason, mutate in mutations:
        contract = copy.deepcopy(original)
        mutate(contract)
        assert any(reason in error for error in _errors(contract, pack_root)), reason


def test_checker_rejects_all_normative_ordering_lifecycle_and_exit_bindings(pack_root: Path) -> None:
    original = _contract(pack_root)
    mutations = [
        ("global event constraints", lambda c: c["global_event_constraints"].__setitem__(0, "nonsense")),
        ("physical lifecycle", lambda c: c.__setitem__("physical_lifecycle", list(reversed(c["physical_lifecycle"])))),
        ("exit priority", lambda c: c["exit_priority"].__setitem__(0, "nonsense")),
        ("reviewer session", lambda c: c["reviewer_session_contract"].__setitem__("successful_verdicts", 2)),
        ("result tuples", lambda c: c["result_tuples"]["execution_unknown"].__setitem__("verification", "FAIL")),
        ("acceptance predicates", lambda c: c["acceptance_predicates"]["cases-only-v1"].pop()),
    ]
    for reason, mutate in mutations:
        contract = copy.deepcopy(original)
        mutate(contract)
        assert any(reason in error for error in _errors(contract, pack_root)), reason


def test_result_tuple_and_cases_only_cardinality_bindings_are_exact(pack_root: Path) -> None:
    contract = _contract(pack_root)
    for name in ("complete_fail", "execution_unknown", "pre_execution_rework", "early_fatal", "review_context_limit"):
        assert contract["result_tuples"][name]["accepted"] is False
    assert contract["result_tuples"]["execution_unknown"]["reason_code"] == "EXECUTION_UNKNOWN"
    assert contract["result_tuples"]["pre_execution_rework"]["reason_code"] == "REWORK"
    assert "verification_not_applicable" in contract["acceptance_predicates"]["cases-only-v1"]
    assert "exactly_one_authoritative_verdict" in contract["acceptance_predicates"]["cases-only-v1"]
    assert "draft_artifact_only_not_accepted" in contract["acceptance_predicates"]["cases-only-v1"]


def test_checker_rejects_required_unaccepted_tuple_and_cases_only_cardinality_mutations(pack_root: Path) -> None:
    original = _contract(pack_root)
    mutations = [
        ("result tuples", lambda c: c["result_tuples"]["execution_unknown"].__setitem__("accepted", True)),
        ("result tuples", lambda c: c["result_tuples"]["pre_execution_rework"].__setitem__("reason_code", "wrong")),
        ("acceptance predicates", lambda c: c["acceptance_predicates"]["cases-only-v1"].remove("exactly_one_authoritative_verdict")),
    ]
    for reason, mutate in mutations:
        contract = copy.deepcopy(original)
        mutate(contract)
        assert any(reason in error for error in _errors(contract, pack_root)), reason


def test_contract_runtime_signatures_match_public_pilot_state_seam(pack_root: Path) -> None:
    contract = _contract(pack_root)
    assert contract["runtime_signatures"] == {name: str(inspect.signature(getattr(pilot_state, name))) for name in ("create_run", "append_event", "create_attempt", "derive_state", "terminal_result", "exit_code")}


def test_checker_rejects_live_runtime_signature_drift(pack_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot_state.create_run, "__signature__", inspect.Signature(), raising=False)
    assert any("runtime signature live mismatch" in error for error in _errors(_contract(pack_root), pack_root))


def test_checker_rejects_projection_drift(pack_root: Path) -> None:
    contract = _contract(pack_root)
    target = pack_root / contract["projections"]["contracts"]
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text("stale", encoding="utf-8")
        assert any("projection drift" in error for error in validate_pipeline_contract(contract, pack_root, check_drift=True)["errors"])
    finally:
        target.write_text(original, encoding="utf-8", newline="\n")


def test_contract_checker_passes_complete_machine_truth(pack_root: Path) -> None:
    assert validate_pipeline_contract(_contract(pack_root), pack_root, check_drift=True) == {"status": "passed", "errors": []}


def test_full_checker_rejects_a_stale_release_manifest(pack_root: Path) -> None:
    contract = _contract(pack_root)
    target = pack_root / contract["release_qualification"]["manifest_path"]
    original = target.read_bytes()
    try:
        target.write_text("{}", encoding="utf-8")
        assert any("release manifest" in error for error in validate_pipeline_contract(contract, pack_root, check_drift=True)["errors"])
    finally:
        target.write_bytes(original)
