#!/usr/bin/env python3
"""Validate frozen Pipeline 4.0 machine truth and deterministic projections."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from tools import pilot_state
from tools.json_cli import JsonArgumentParser


CORE_SKILLS = ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "orchestrate"]
SKILL_PATHS = {
    "context-marker": "skills/context-marker/SKILL.md",
    "tc-generator": "skills/tc-generator/SKILL.md",
    "tc-reviewer": "skills/tc-reviewer/SKILL.md",
    "tc-to-autotest": "skills/tc-to-autotest/SKILL.md",
    "autotest-reviewer": "skills/autotest-reviewer/SKILL.md",
    "orchestrate": "skills/orchestrate/SKILL.md",
}
SCHEMA_ROWS = [
    ("pipeline.schema.json",1,"IMPLEMENTED","4.0"),("pilot-common.schema.json",1,"IMPLEMENTED","1.0.0"),("run-manifest.schema.json",1,"IMPLEMENTED","1.0.0"),("event.schema.json",1,"IMPLEMENTED","2.0.0"),("model-request.schema.json",1,"IMPLEMENTED","2.0.0"),("attempt.schema.json",1,"IMPLEMENTED","1.0.0"),("run-authorization-receipt.schema.json",1,"IMPLEMENTED","1.0.0"),("terminal-result.schema.json",1,"IMPLEMENTED","1.0.0"),("finalization-receipt.schema.json",1,"IMPLEMENTED","1.0.0"),
    ("skillsrc.schema.json",2,"IMPLEMENTED","5.2.0"),("skillsrc-init-output.schema.json",2,"IMPLEMENTED","5.0.0"),("inventory-receipt.schema.json",2,"IMPLEMENTED","1.0.0"),("exclusion-receipt.schema.json",2,"IMPLEMENTED","1.0.0"),("context-selection-receipt.schema.json",2,"IMPLEMENTED","1.0.0"),("execution-baseline.schema.json",2,"IMPLEMENTED","1.0.0"),
    ("context-marker-output.schema.json",3,"IMPLEMENTED","5.1.0"),("tc-generator-output.schema.json",3,"IMPLEMENTED","5.0.0"),("canonical-test-document.schema.json",3,"IMPLEMENTED","1.0.0"),("batch-plan.schema.json",3,"IMPLEMENTED","1.0.0"),("candidate-fragment.schema.json",3,"IMPLEMENTED","1.0.0"),("assembly-receipt.schema.json",3,"IMPLEMENTED","1.0.0"),
    ("tc-reviewer-output.schema.json",4,"IMPLEMENTED","6.0.0"),("orchestrator-output.schema.json",4,"IMPLEMENTED","5.0.0"),("reviewer-session.schema.json",4,"IMPLEMENTED","2.0.0"),("review-plan.schema.json",4,"IMPLEMENTED","1.0.0"),("review-part-output.schema.json",4,"IMPLEMENTED","1.0.0"),("review-plan-compact.schema.json",4,"IMPLEMENTED","2.0.0"),("review-part-output-compact.schema.json",4,"IMPLEMENTED","2.1.0"),
    ("tc-to-autotest-output.schema.json",5,"IMPLEMENTED","5.0.0"),("autotest-reviewer-output.schema.json",5,"IMPLEMENTED","6.0.0"),("execution-inputs-receipt.schema.json",5,"IMPLEMENTED","1.0.0"),("generated-delta.schema.json",5,"IMPLEMENTED","1.0.0"),("materialization-receipt.schema.json",5,"IMPLEMENTED","1.0.0"),("disposition-receipt.schema.json",5,"IMPLEMENTED","1.0.0"),
    ("run-tests-output.schema.json",6,"IMPLEMENTED","5.0.0"),("resume-validation-receipt.schema.json",7,"IMPLEMENTED","1.0.0"),("trace-document.schema.json",7,"IMPLEMENTED","5.0.0"),("trace-audit-output.schema.json",7,"IMPLEMENTED","5.0.0"),("pre-finalization-trace.schema.json",7,"IMPLEMENTED","1.0.0"),("derived-terminal-trace.schema.json",7,"IMPLEMENTED","1.0.0"),("driver-summary.schema.json",7,"IMPLEMENTED","1.1.0"),
    ("compatibility-evidence.schema.json",8,"IMPLEMENTED","2.0.0"),("retained-native-rerun-receipt.schema.json",8,"IMPLEMENTED","1.0.0"),("scenario-observation-receipt.schema.json",8,"IMPLEMENTED","1.0.0"),("release-eval-run.schema.json",8,"IMPLEMENTED","1.0.0"),("release-eval-receipt.schema.json",8,"IMPLEMENTED","1.0.0"),("release-manifest.schema.json",8,"IMPLEMENTED","1.0.0"),
]
ARTIFACT_ROWS = [
    ("run_manifest",1,"IMPLEMENTED"),("run_authorization_receipt",1,"IMPLEMENTED"),("attempt",1,"IMPLEMENTED"),("event_journal",1,"IMPLEMENTED"),("event",1,"IMPLEMENTED"),("model_request",1,"IMPLEMENTED"),("structured_result",1,"IMPLEMENTED"),("finalization_receipt",1,"IMPLEMENTED"),
    ("skillsrc_configuration",2,"IMPLEMENTED"),("skillsrc_proposal_receipt",2,"IMPLEMENTED"),("inventory_receipt",2,"IMPLEMENTED"),("exclusion_receipt",2,"IMPLEMENTED"),("context_selection_receipt",2,"IMPLEMENTED"),("execution_baseline",2,"IMPLEMENTED"),
    ("normalized_requirements",3,"IMPLEMENTED"),("canonical_candidate",3,"IMPLEMENTED"),("batch_plan",3,"IMPLEMENTED"),("candidate_fragment",3,"IMPLEMENTED"),("canonical_header",3,"IMPLEMENTED"),("assembly_receipt",3,"IMPLEMENTED"),("pre_review_audit",3,"IMPLEMENTED"),
    ("authoritative_verdict",4,"IMPLEMENTED"),("candidate_bundle_receipt",4,"IMPLEMENTED"),("successor_bundle_receipt",4,"IMPLEMENTED"),("effective_canonical",4,"IMPLEMENTED"),("effective_bundle_receipt",4,"IMPLEMENTED"),("projection_bundle",4,"IMPLEMENTED"),("reviewer_session",4,"IMPLEMENTED"),("reviewer_evidence_transfer",4,"IMPLEMENTED"),("review_plan",4,"IMPLEMENTED"),("review_part_output",4,"IMPLEMENTED"),("review_aggregate",4,"IMPLEMENTED"),
    ("automation",5,"IMPLEMENTED"),("automation_static_review",5,"IMPLEMENTED"),("execution_inputs",5,"IMPLEMENTED"),("generated_delta",5,"IMPLEMENTED"),("generated_file",5,"IMPLEMENTED"),("materialization_receipt",5,"IMPLEMENTED"),("disposition_receipt",5,"IMPLEMENTED"),
    ("execution_receipt",6,"IMPLEMENTED"),("framework_evidence",6,"IMPLEMENTED"),("environment_receipt",6,"IMPLEMENTED"),("resume_validation_receipt",7,"IMPLEMENTED"),("execution_trace",7,"IMPLEMENTED"),("trace_audit",7,"IMPLEMENTED"),("orchestrator_output",7,"IMPLEMENTED"),("pre_finalization_trace",7,"IMPLEMENTED"),("terminal_result",7,"IMPLEMENTED"),("derived_terminal_trace",7,"IMPLEMENTED"),
    ("compatibility_evidence",8,"IMPLEMENTED"),("retained_native_rerun_receipt",8,"IMPLEMENTED"),("scenario_observation_receipt",8,"IMPLEMENTED"),("release_eval_run",8,"IMPLEMENTED"),("release_eval_receipt",8,"IMPLEMENTED"),("release_manifest",8,"IMPLEMENTED"),
]
ADAPTERS = ["pytest:selected-symbols-v1", "maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1", "gradle-wrapper:selected-symbols-v1"]
POLICIES = ["cases-only-v1", "local-pilot-v1"]
STAGE_ROWS = [
    {"stage": "orchestrate", "role": "controller", "role_policy": "orchestrate-v1", "cardinality": "once", "profiles": POLICIES},
    {"stage": "context-marker", "role": "generator", "role_policy": "context-marker-v1", "cardinality": "once", "profiles": POLICIES},
    {"stage": "tc-generator", "role": "generator", "role_policy": "tc-generator-v1", "cardinality": "per_batch", "profiles": POLICIES},
    {"stage": "tc-reviewer", "role": "canonical-reviewer", "role_policy": "canonical-reviewer-v2", "cardinality": "per_review_part", "profiles": POLICIES},
    {"stage": "tc-to-autotest", "role": "automation-generator", "role_policy": "tc-to-autotest-v1", "cardinality": "per_automation_revision", "profiles": ["local-pilot-v1"]},
    {"stage": "autotest-reviewer", "role": "automation-reviewer", "role_policy": "autotest-static-reviewer-v2", "cardinality": "per_review_part", "profiles": ["local-pilot-v1"]},
]
PROJECTION_ROWS = [
    {"id": "zephyr-scale-step-row-24-v5", "format": "csv", "mode": "default", "tenant_status": "N_A"},
    {"id": "zephyr-scale-step-row-24-v4", "format": "csv", "mode": "opt_in_compatibility", "tenant_status": "N_A"},
    {"id": "zephyr-scale-xml-observed-v1", "format": "xml", "mode": "opt_in_observed", "tenant_status": "N_A"},
]
RELEASE_QUALIFICATION = {
    "package_version": "0.5.0-pilot",
    "compatibility_contract_version": "portable-cli-v1",
    "execution_profile_version": "v1",
    "release_eval_policy": "adaptive-1-3-5-v1",
    "scenario_suite": "pilot-critical-v1",
    "manifest_path": "release/manifest.json",
    "component_state": "implemented_unverified",
    "ready_tuple": None,
    "not_applicable": ["company_runner", "zephyr_tenant", "production_rollback"],
}
CONTROLLER_SCHEMAS = [row[0] for row in SCHEMA_ROWS if row[2] == "IMPLEMENTED"]
VERSIONLESS_RECEIPT_SCHEMAS = {"materialization-receipt.schema.json", "disposition-receipt.schema.json"}
EXPECTED_EVENT_ORDER = [
    "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND",
    "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED", "CONTEXT_SELECTED",
    "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED",
    "REVIEW_REQUESTED", "ARTIFACT_READ_BACK", "WAITING_FOR_MODEL",
]
EXPECTED_AXES = {
    "attempt_state": (["ACTIVE", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "TERMINAL"], False),
    "completion": (["COMPLETE", "PARTIAL", "FATAL"], True),
    "verification": (["PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"], True),
    "coverage": (["FULL", "MIXED", "MANUAL_ONLY"], True),
}
EXPECTED_SIGNATURES = {
    "create_run": "(project: 'Path', policy_profile: 'str', authorization: 'Mapping[str, Any]') -> 'Mapping[str, Any]'",
    "append_event": "(run_root: 'Path', event_type: 'str', *, actor: 'str', attempt_id: 'str | None' = None, batch_id: 'str | None' = None, artifact_digest: 'str | None' = None, stage_instance_id: 'str | None' = None, transport_attempts: 'int | None' = None) -> 'Mapping[str, Any]'",
    "create_attempt": "(run_root: 'Path', identity: 'Mapping[str, Any]', baseline: 'Mapping[str, Any]') -> 'Mapping[str, Any]'",
    "derive_state": "(run_root: 'Path') -> 'Mapping[str, Any]'",
    "terminal_result": "(facts: 'Mapping[str, Any]', policy_profile: 'str') -> 'Mapping[str, Any]'",
    "exit_code": "(result: 'Mapping[str, Any] | None', controller_error: 'bool' = False) -> 'int'",
}
EXPECTED_GLOBAL_CONSTRAINTS = [
    {"before": "RUN_CREATED", "after": "SCAN_OR_MODEL_REQUEST"},
    {"before": "MODULE_SELECTED", "after": "ATTEMPT_CREATED"},
    {"before": "SNAPSHOT_BOUND", "after": "ATTEMPT_CREATED"},
    {"before": "EXECUTION_BASELINE_FROZEN", "after": "ATTEMPT_CREATED"},
    {"before": "ATTEMPT_CREATED", "after": "SOURCE_BYTES_TRANSMITTED_TO_MODEL"},
    {"before": "INVENTORY_READY", "after": "MODEL_REQUESTED"},
    {"before": "SNAPSHOT_BOUND", "after": "MODEL_REQUESTED"},
    {"before": "CONTEXT_SELECTED", "after": "MODEL_REQUESTED"},
    {"before": "MODEL_RESPONSE_RECEIVED", "after": "CANDIDATE_PUBLISHED"},
    {"before": "CANDIDATE_PUBLISHED", "after": "REVIEW_REQUESTED"},
    {"before": "FINALIZATION_RECEIPT_READ_BACK", "after": "TERMINAL_EVENT"},
    {"before": "TERMINAL_EVENT", "after": "TERMINAL_RETRY_OBSERVED", "narrow_exception": True},
    {"before": "SCENARIO_OBSERVATION_READ_BACK", "after": "TERMINAL_RETRY_OBSERVED", "narrow_exception": True},
    {"before": "TERMINAL_EVENT", "after": "RETAINED_NATIVE_RERUN", "narrow_exception": True},
    {"before": "RETAINED_NATIVE_RERUN_RECEIPT_READ_BACK", "after": "RETAINED_NATIVE_RERUN", "narrow_exception": True},
    {"before": "EXECUTION_UNKNOWN", "after": "PROCESS_STOPPED", "narrow_exception": True},
]
EXPECTED_REVIEWER_SESSION = {'logical_review_count': 'one_per_branch_or_revision', 'session_count': 'one_per_declared_part', 'invocation_order': 'sequential', 'coverage': ['original_source', 'local', 'cross_part'], 'aggregation': 'controller', 'incomplete': 'never_accepted', 'successful_verdicts': 1, 'pre_verdict_abort': {'terminal': True, 'verdicts': 0}, 'forbidden': ['shared_growing_context', 'hierarchical_reviewers', 'multiple_authoritative_aggregates'], 'rework': {'max_per_run': 1, 'retry_reason': 'CANONICAL_REWORK', 'trigger': 'authoritative_verdict_rejected', 'generator_input': ['canonical_r1', 'findings_blocking_warning', 'affected_case_ids'], 'generator_output': 'changed_test_cases_only', 'successor': 'canonical_r2_parent_sha256_r1', 'r2_review': 'new_session_incremental_carry_identical_checked_inputs', 'not_reworked': ['unchecked_scope', 'pre_verdict_abort'], 'after_r2_rejected': 'terminal_rework'}, 'self_review': {'isolation_none': 'continue_same_parts_and_format', 'independence_axis': 'review_independence', 'values': ['ISOLATED', 'SELF'], 'self_reason_code': 'REVIEW_NOT_INDEPENDENT', 'accept_flag': '--accept-self-review', 'context_warning': 'SELF_REVIEW_CONTEXT_OVERFLOW', 'default_context_bytes': 500000}, 'modes': {'setting': '--review-mode', 'values': ['pairs', 'compact-v1'], 'default': 'compact-v1', 'compact-v1': {'model_input': 'review_projection_text', 'projection': 'review-projection-v1', 'projection_binding': 'canonical_document_digest_in_snapshot', 'not_model_inputs': ['html', 'markdown', 'csv'], 'plan_schema': 'review-plan-compact.schema.json', 'answer_schema': 'review-part-output-compact.schema.json', 'packing': 'whole_cases_content_defined_blocks_block_pairs', 'pair_guarantee': 'every_case_pair_in_one_part', 'areas': ['source', 'case_home_part', 'cross_per_part'], 'invocation_order': 'independent_parts_batchable', 'batch_flag': '--max-tasks', 'answer_checks': ['refs_are_part_anchors', 'case_area_cites_own_anchor', 'every_lint_suspicion_answered', 'info_findings_at_most_5', 'related_ids_known'], 'corrections': 'closed_field_dictionary_one_pointer_before_checked', 'carry_key': ['area_fingerprint', 'role_policy', 'skill_digest', 'instructions_digest', 'model_id', 'mode']}}}
EXPECTED_PHYSICAL_LIFECYCLE = ["MATERIALIZATION", "EXECUTION", "EXECUTION_TRACE", "RETAIN_OR_CLEANUP_DECISION", "DISPOSITION_RECEIPTS", "PRE_FINALIZATION_TRACE", "FINALIZATION_VERIFICATION", "FINALIZATION_RECEIPT_READ_BACK", "TERMINAL_RESULT", "DERIVED_TERMINAL_TRACE", "TERMINAL_EVENT"]
EXPECTED_RESULT_TUPLES = {"complete_fail": {"attempt_state": "TERMINAL", "completion": "COMPLETE", "verification": "FAIL", "accepted": False}, "execution_unknown": {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "UNKNOWN", "reason_code": "EXECUTION_UNKNOWN", "accepted": False}, "pre_execution_rework": {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "REWORK", "accepted": False}, "early_fatal": {"attempt_state": "TERMINAL", "completion": "FATAL", "coverage": None, "accepted": False}, "review_context_limit": {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "REVIEW_CONTEXT_LIMIT", "accepted": False}, "invalid_finalization": {"attempt_state": "TERMINAL", "accepted": False, "reason_code": "FINALIZATION_INVALID", "verification": "PRESERVED", "coverage": "PRESERVED"}}
EXPECTED_ACCEPTANCE = {"cases-only-v1": ["coverage_full_mixed_or_manual_only", "verification_not_applicable", "canonical_schema_semantic_provenance_valid", "successful_full_document_authoritative_review", "exactly_one_authoritative_verdict", "reviewer_isolation_verified", "no_unresolved_blocker", "branch_valid_trace", "finalization_valid", "materialization_not_applicable", "execution_not_applicable", "draft_artifact_only_not_accepted"], "local-pilot-v1": ["accepted_canonical", "accepted_automation", "complete_generated_delta_materialization", "authoritative_exact_target_pass", "valid_trace", "every_required_generated_file_retained", "finalization_valid", "reviewer_isolation_verified", "review_isolated_or_self_review_accepted", "no_unresolved_blocker", "mixed_manual_coverage_traceable"]}
# A reason the result projection derives when an acceptance predicate fails and no earlier cause is set.
EXPECTED_ACCEPTANCE_REASON_CODES = [
    {"predicate": "no_unresolved_blocker", "profiles": ["local-pilot-v1"], "reason_code": "UNRESOLVED_AUTOMATION_BLOCKER", "evidence": "blocker_count_positive",
     "when": "verification_pass_without_earlier_reason", "precedence": "after_review_reasons", "legacy_results": "reason_absent_stays_valid"},
]
EXPECTED_EXIT_PRIORITY = [{"when": "controller_error_without_trustworthy_attempt_result", "code": 2}, {"when": "waiting_for_input_or_model", "code": 3}, {"when": "cases_only_fatal_invalid_closure_or_unreliable_evidence", "code": 2}, {"when": "valid_terminal_cases_only_v1", "code": 1}, {"when": "accepted_terminal", "code": 0}, {"when": "unknown_not_runnable_fatal_invalid_closure_or_unreliable_evidence", "code": 2}, {"when": "other_trustworthy_terminal_unaccepted", "code": 1}]


# Opt-in amendments of 2026-10-07 (docs/superpowers/specs/2026-10-07-pilot-contract-amendments.md).
# Without the option a run follows the frozen sections above unchanged.
EXPECTED_AMENDMENTS = [
    {"id": "pilot-contract-amendments-2026-10-07", "document": "docs/superpowers/specs/2026-10-07-pilot-contract-amendments.md",
     "status": "ACCEPTED", "opt_in": True, "wave": 2, "sections": ["1.2", "1.5", "17"]},
    {"id": "pilot-contract-amendments-2026-10-07-wave-3", "document": "docs/superpowers/specs/2026-10-07-pilot-contract-amendments.md",
     "status": "ACCEPTED", "opt_in": True, "wave": 3, "sections": ["17.2", "25"]},
]
EXPECTED_OPTIONAL_LIFECYCLE = [
    {"stage": "MUTATION", "after": "EXECUTION_TRACE", "before": "RETAIN_OR_CLEANUP_DECISION",
     "requires": ["skillsrc_mutation_enabled", "run_authorization_mutation_requested", "authoritative_pass_or_fail"],
     "mutates": "passing_generated_methods_only", "writes": "run_directory_only", "proves": "project_inventory_unchanged",
     "receipt": "mutation_receipt", "axis": "test_strength", "never_changes": ["verification", "accepted", "dispositions", "earlier_evidence"]},
]
EXPECTED_OPTIONAL_AXES = {
    "test_strength": {"values": ["MEASURED", "NOT_RUNNABLE", "NOT_APPLICABLE"], "nullable": True},
    "isolation_evidence": {"values": ["DRIVER_PROCESS", "HOST_DECLARED", "NONE"], "nullable": True},
}
EXPECTED_MUTATION_TOOLING = {
    "java": {"tool": "org.pitest:pitest-command-line", "version": "1.30.0", "plugin": "org.pitest:pitest-junit5-plugin", "plugin_version": "1.2.3",
             "pins": "tools/mutation_tools.json", "resolution": "project_build_tool_local_repository", "launcher": "junit_platform_launcher_of_project_version",
             "consent": ["skillsrc_mutation_enabled", "run_authorization_mutation_requested"], "digest_mismatch": "NOT_RUNNABLE", "mutators": "DEFAULTS",
             "report": "xml_full_mutation_matrix"},
    "python": "not_implemented",
}
# New schemas and artifacts of the opt-in features: registered next to the frozen registries.
OPTIONAL_SCHEMA_ROWS = [("mutation-receipt.schema.json", 6, "IMPLEMENTED", "1.0.0")]
OPTIONAL_ARTIFACT_ROWS = [("mutation_receipt", 6, "IMPLEMENTED"), ("mutation_triage", 6, "IMPLEMENTED"), ("analyst_report", 7, "IMPLEMENTED"),
                          ("runner_process_evidence", 4, "IMPLEMENTED")]
OPTIONAL_SKILLS = {"mutation-triage": "skills/mutation-triage/SKILL.md"}
OPTIONAL_STAGE_ROWS = [
    {"stage": "mutation-triage", "role": "strength-analyst", "role_policy": "mutation-triage-v1", "cardinality": "post_terminal_per_task",
     "profiles": ["local-pilot-v1"], "answer_schema": "mutation-triage-output.schema.json", "decisions": ["TEST_GAP", "SPEC_GAP", "EQUIVALENT", "OUT_OF_SCOPE"],
     "changes": "nothing_in_the_attempt"},
]
EXPECTED_MODEL_RUNNER = {
    "setting": "--review-runner", "values": ["host", "process"], "default": "host", "presets": ["claude", "codex"], "custom_template": "launch_flag_only",
    "skillsrc_fields": ["preset", "models", "max_parallel", "timeout_seconds"], "invocation": "fresh_process_temp_cwd_stdin_no_write_tools", "wait_action": "wait",
    "tries_per_part": 3, "evidence": ["command_digest", "cli_name_version", "model", "started_finished", "exit_code", "session_id", "stdout_digest", "tokens", "user_settings_loaded"],
    "evidence_axis": "isolation_evidence", "require_flag": "--require-driver-isolation", "require_reason_code": "REVIEW_ISOLATION_UNVERIFIED",
    "standalone_command": "run --runner process",
}

# Wave 3: quarantine disposition policy (§17 item 2), owned suite tests and the suite-update-v1 proposal profile (§25).
EXPECTED_DISPOSITION_POLICIES = {'setting': '--disposition-policy', 'values': ['cleanup', 'quarantine'], 'defaults': {'local-pilot-v1': 'cleanup', 'suite-update-v1': 'quarantine'}, 'authorization_key': 'disposition_policy', 'quarantine': {'applies_to': 'authoritative_fail_with_method_outcomes', 'all_methods_passed': 'RETAINED', 'failed_methods': 'QUARANTINED', 'rewrite': 'exact_owned_bytes_failed_methods_only', 'marks': {'junit5': 'org.junit.jupiter.api.Disabled', 'pytest': 'pytest.mark.xfail(strict=True)'}, 'explicit_run': {'junit5': 'junit.jupiter.conditions.deactivate', 'pytest': '--runxfail'}, 'never_changes': ['verification', 'coverage', 'accepted', 'result_tuple']}}
EXPECTED_OPTIONAL_PROFILES = [{'id': 'suite-update-v1', 'version': 'v1', 'kind': 'suite_proposal', 'steps': ['MIGRATION', 'IMPACT', 'UPDATE_AND_REPAIR', 'REVIEW', 'AUTOMATION', 'STATIC_REVIEW', 'SUITE_RUN', 'FAILURE_TRIAGE', 'MUTATION', 'MANIFEST', 'SUMMARY'], 'disposition_policy': 'quarantine', 'repair': 'one_try_static_review_expectations_unchanged', 'writes': ['suite_directory', 'manifest_owned_tests'], 'outputs': ['pr_description', 'suite_patch', 'suite_update_result'], 'accepted': 'not_applicable', 'commits': False}]
EXPECTED_SUITE_CONTRACT = {'path_setting': 'suite.path', 'default_path': 'test-cases/', 'manifest_schema': 'suite-manifest.schema.json', 'format_version': '1.0.0', 'migrations': ['0->1.0.0'], 'statuses': ['ACTIVE', 'QUARANTINED', 'RETIRED'], 'created_by': 'local-pilot-v1 --suite', 'authorization_key': 'suite_requested', 'owned_change_proof': 'manifest_slice_digest', 'human_edit': 'never_overwritten_proposal_in_pr'}
EXPECTED_REQUIREMENT_IDENTITY = {'openspec': 'capability_and_requirement_name_with_renames', 'markdown': 'explicit_id_else_path_and_heading_chain', 'builtin_ids': ['REQ', 'AC', 'US', 'FR', 'BR', 'TR', 'ТР', 'ПС'], 'id_pattern_setting': 'requirements.id_pattern', 'text_digest': 'sha256_nfc_collapsed_whitespace', 'sreq_format': 'unchanged', 'stored_in': 'suite_manifest'}


def _error_if_not_equal(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label} exact ordered registry mismatch")


def _load_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_schema_rows() -> list[dict[str, Any]]:
    rows = [
        {"id": name, "phase": phase, "implementation_status": status, "target_version": version, "semantic_ready": False}
        for name, phase, status, version in SCHEMA_ROWS
    ]
    for row in rows[2:]:
        if row["implementation_status"] == "IMPLEMENTED":
            row["semantic_ready"] = True
    return rows


def _expected_artifact_rows() -> list[dict[str, Any]]:
    rows = [
        {"id": name, "phase": phase, "implementation_status": status, "semantic_ready": False}
        for name, phase, status in ARTIFACT_ROWS
    ]
    for row in rows:
        if row["implementation_status"] == "IMPLEMENTED":
            row["semantic_ready"] = True
    return rows


def _known_object_boundaries_are_closed(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("type") == "object" and "properties" in value and value.get("additionalProperties") is not False:
            return False
        return all(_known_object_boundaries_are_closed(child) for child in value.values())
    if isinstance(value, list):
        return all(_known_object_boundaries_are_closed(child) for child in value)
    return True


def _accepted_versions(schema: Mapping[str, Any]) -> list[str]:
    """Versions a schema accepts: one ``const``, or a minor-compatible ``enum`` ending with the target.

    A minor version (2026-10-07 amendments) keeps every earlier minor of the same major
    valid, so existing artifacts and project files stay readable.
    """
    declared = schema.get("properties", {}).get("schema_version", {})
    if "const" in declared:
        return [declared["const"]]
    values = declared.get("enum")
    if not isinstance(values, list) or not values or not all(isinstance(value, str) and value.count(".") == 2 for value in values):
        return []
    parsed = [tuple(int(part) for part in value.split(".")) for value in values]
    if parsed != sorted(set(parsed)) or len({version[0] for version in parsed}) != 1:
        return []
    return list(values)


def _validate_schema_registry(contract: Mapping[str, Any], root: Path, errors: list[str]) -> None:
    rows = contract.get("schema_registry", [])
    _error_if_not_equal(errors, "schema_registry exact truth", rows, _expected_schema_rows())
    if len({row.get("id") for row in rows if isinstance(row, Mapping)}) != len(rows):
        errors.append("duplicate schema_registry")
    for row in rows:
        if isinstance(row, Mapping):
            _validate_schema_file(root, row, errors)


def _validate_schema_file(root: Path, row: Mapping[str, Any], errors: list[str]) -> None:
    name = row.get("id")
    status = row.get("implementation_status")
    path = root / "schemas" / str(name)
    if status in {"IMPLEMENTED", "MIGRATION_PENDING"}:
        if not path.is_file():
            errors.append(f"{status.lower()} schema missing: {name}")
            return
        schema = _load_json(path)
        if schema.get("$id") != f"schemas/{name}":
            errors.append(f"schema id mismatch: {name}")
    if status == "IMPLEMENTED":
        schema = _load_json(path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"implemented schema draft mismatch: {name}")
        if name not in {"pipeline.schema.json", *VERSIONLESS_RECEIPT_SCHEMAS} and _accepted_versions(schema)[-1:] != [row.get("target_version")]:
            errors.append(f"implemented schema version mismatch: {name}")
        if not _known_object_boundaries_are_closed(schema):
            errors.append(f"implemented schema closure mismatch: {name}")
    elif status == "MIGRATION_PENDING":
        # Schema-version equality is not semantic readiness.  A legacy
        # schema may retain its current version while its frozen-pilot
        # migration is still explicitly pending in the registry.
        _load_json(path)
    elif status == "NOT_IMPLEMENTED" and path.exists():
        errors.append(f"future placeholder schema forbidden: {name}")


def _validate_registry_shape(contract: Mapping[str, Any], errors: list[str]) -> None:
    artifact_rows = contract.get("artifact_registry", [])
    _error_if_not_equal(errors, "artifact_registry exact truth", artifact_rows, _expected_artifact_rows())
    if len({row.get("id") for row in artifact_rows if isinstance(row, Mapping)}) != len(artifact_rows):
        errors.append("duplicate artifact_registry")
    adapters = contract.get("adapter_registry", [])
    _error_if_not_equal(errors, "adapter_registry", [row.get("id") for row in adapters if isinstance(row, Mapping)], ADAPTERS)
    if len({row.get("id") for row in adapters if isinstance(row, Mapping)}) != len(adapters):
        errors.append("duplicate adapter_registry")
    if any(row.get("phase") != 6 or row.get("implementation_status") != "IMPLEMENTED" or row.get("version") != "v1" or row.get("semantic_ready") is not True for row in adapters if isinstance(row, Mapping)):
        errors.append("adapter registry readiness mismatch")
    policies = contract.get("policy_profiles", [])
    _error_if_not_equal(errors, "policy_profiles", [row.get("id") for row in policies if isinstance(row, Mapping)], POLICIES)
    if len({row.get("id") for row in policies if isinstance(row, Mapping)}) != len(policies):
        errors.append("duplicate policy_profiles")
    if any(row.get("version") != "v1" or row.get("semantic_ready") is not True for row in policies if isinstance(row, Mapping)):
        errors.append("policy registry readiness mismatch")
    stages = contract.get("stage_registry", [])
    _error_if_not_equal(errors, "stage_registry", stages, STAGE_ROWS)
    if len({row.get("stage") for row in stages if isinstance(row, Mapping)}) != len(stages):
        errors.append("duplicate stage_registry")
    projections = contract.get("projection_profiles", [])
    _error_if_not_equal(errors, "projection_profiles", projections, PROJECTION_ROWS)
    if len({row.get("id") for row in projections if isinstance(row, Mapping)}) != len(projections):
        errors.append("duplicate projection_profiles")
    _error_if_not_equal(errors, "release qualification", contract.get("release_qualification"), RELEASE_QUALIFICATION)


def _validate_bindings(contract: Mapping[str, Any], root: Path, errors: list[str]) -> None:
    _error_if_not_equal(errors, "core_skill", contract.get("core_skills"), CORE_SKILLS)
    skill_files = contract.get("skill_files", {})
    if skill_files != SKILL_PATHS:
        errors.append("Skill path binding mismatch")
    else:
        for skill in CORE_SKILLS:
            path = SKILL_PATHS[skill]
            if not (root / path).is_file():
                errors.append(f"Skill path missing: {skill}")
    _error_if_not_equal(errors, "controller_schemas", contract.get("controller_schemas"), CONTROLLER_SCHEMAS)
    if contract.get("runtime_signatures") != EXPECTED_SIGNATURES:
        errors.append("runtime signature contract mismatch")
    live_signatures = {name: str(inspect.signature(getattr(pilot_state, name))) for name in EXPECTED_SIGNATURES}
    if live_signatures != EXPECTED_SIGNATURES:
        errors.append("runtime signature live mismatch")
    if [item.get("event_type") for item in contract.get("event_order", []) if isinstance(item, Mapping)] != EXPECTED_EVENT_ORDER:
        errors.append("lifecycle event order mismatch")
    _error_if_not_equal(errors, "global event constraints", contract.get("global_event_constraints"), EXPECTED_GLOBAL_CONSTRAINTS)
    _error_if_not_equal(errors, "reviewer session", contract.get("reviewer_session_contract"), EXPECTED_REVIEWER_SESSION)
    _error_if_not_equal(errors, "physical lifecycle", contract.get("physical_lifecycle"), EXPECTED_PHYSICAL_LIFECYCLE)
    axes = contract.get("result_axes", {})
    if not isinstance(axes, Mapping):
        errors.append("result axes mismatch")
        return
    for name, (values, nullable) in EXPECTED_AXES.items():
        value = axes.get(name)
        if not isinstance(value, Mapping) or value.get("values") != values or value.get("nullable") is not nullable:
            errors.append("result axes mismatch")
            break
    accepted = axes.get("accepted")
    if not isinstance(accepted, Mapping) or accepted.get("preterminal") != "absent" or accepted.get("terminal") != "boolean":
        errors.append("result axes mismatch")
    reason_code = axes.get("reason_code")
    if not isinstance(reason_code, Mapping) or reason_code.get("preterminal") != "absent" or reason_code.get("terminal") != "written_once":
        errors.append("result axes mismatch")
    _error_if_not_equal(errors, "result tuples", contract.get("result_tuples"), EXPECTED_RESULT_TUPLES)
    _error_if_not_equal(errors, "acceptance predicates", contract.get("acceptance_predicates"), EXPECTED_ACCEPTANCE)
    _error_if_not_equal(errors, "acceptance reason codes", contract.get("acceptance_reason_codes"), EXPECTED_ACCEPTANCE_REASON_CODES)
    predicates = contract.get("acceptance_predicates") if isinstance(contract.get("acceptance_predicates"), Mapping) else {}
    for row in contract.get("acceptance_reason_codes") or []:
        if not isinstance(row, Mapping) or any(row.get("predicate") not in (predicates.get(profile) or []) for profile in row.get("profiles") or []):
            errors.append("acceptance reason code names a predicate its profile does not have")
    _error_if_not_equal(errors, "exit priority", contract.get("exit_priority"), EXPECTED_EXIT_PRIORITY)
    _validate_amendments(contract, root, errors)


def _validate_amendments(contract: Mapping[str, Any], root: Path, errors: list[str]) -> None:
    """Opt-in amendments: exact truth, existing documents, and an optional stage placed inside the frozen lifecycle."""
    _error_if_not_equal(errors, "contract amendments", contract.get("contract_amendments"), EXPECTED_AMENDMENTS)
    _error_if_not_equal(errors, "optional lifecycle stages", contract.get("optional_lifecycle_stages"), EXPECTED_OPTIONAL_LIFECYCLE)
    lifecycle = list(contract.get("physical_lifecycle") or [])
    for row in contract.get("optional_lifecycle_stages") or []:
        if not isinstance(row, Mapping) or row.get("after") not in lifecycle or row.get("before") not in lifecycle \
                or lifecycle.index(row["before"]) != lifecycle.index(row["after"]) + 1 or row.get("stage") in lifecycle:
            errors.append("optional lifecycle stage is not between two adjacent frozen stages")
    _error_if_not_equal(errors, "optional result axes", contract.get("optional_result_axes"), EXPECTED_OPTIONAL_AXES)
    if set(contract.get("optional_result_axes") or {}) & set(contract.get("result_axes") or {}):
        errors.append("optional result axis shadows a frozen axis")
    _error_if_not_equal(errors, "optional schema registry", contract.get("optional_schema_registry"),
                        [{"id": name, "phase": phase, "implementation_status": status, "target_version": version, "semantic_ready": True}
                         for name, phase, status, version in OPTIONAL_SCHEMA_ROWS])
    _error_if_not_equal(errors, "optional artifact registry", contract.get("optional_artifact_registry"),
                        [{"id": name, "phase": phase, "implementation_status": status, "semantic_ready": True} for name, phase, status in OPTIONAL_ARTIFACT_ROWS])
    frozen = {row.get("id") for row in contract.get("schema_registry") or [] if isinstance(row, Mapping)}
    for row in contract.get("optional_schema_registry") or []:
        if not isinstance(row, Mapping) or row.get("id") in frozen:
            errors.append("optional schema shadows a frozen schema")
            continue
        _validate_schema_file(root, row, errors)
    _error_if_not_equal(errors, "optional skills", contract.get("optional_skills"), OPTIONAL_SKILLS)
    for name, path in (contract.get("optional_skills") or {}).items():
        if name in (contract.get("skill_files") or {}) or not (root / str(path)).is_file():
            errors.append(f"optional skill missing or shadowing a core skill: {name}")
    _error_if_not_equal(errors, "optional stage registry", contract.get("optional_stage_registry"), OPTIONAL_STAGE_ROWS)
    for row in contract.get("optional_stage_registry") or []:
        if isinstance(row, Mapping) and not (root / "schemas" / str(row.get("answer_schema"))).is_file():
            errors.append(f"optional stage answer schema missing: {row.get('answer_schema')}")
    _error_if_not_equal(errors, "mutation tooling", contract.get("mutation_tooling"), EXPECTED_MUTATION_TOOLING)
    _error_if_not_equal(errors, "model runner", contract.get("model_runner"), EXPECTED_MODEL_RUNNER)
    _error_if_not_equal(errors, "disposition policies", contract.get("optional_disposition_policies"), EXPECTED_DISPOSITION_POLICIES)
    _error_if_not_equal(errors, "optional policy profiles", contract.get("optional_policy_profiles"), EXPECTED_OPTIONAL_PROFILES)
    frozen_profiles = {row.get("id") for row in contract.get("policy_profiles") or [] if isinstance(row, Mapping)}
    for row in contract.get("optional_policy_profiles") or []:
        if not isinstance(row, Mapping) or row.get("id") in frozen_profiles or row.get("commits") is not False:
            errors.append("optional policy profile shadows a frozen profile or commits")
    defaults = (contract.get("optional_disposition_policies") or {}).get("defaults") or {}
    if any(defaults.get(profile) not in {None, "cleanup"} for profile in frozen_profiles):
        errors.append("disposition policies change a frozen profile's default")
    _error_if_not_equal(errors, "suite contract", contract.get("suite_contract"), EXPECTED_SUITE_CONTRACT)
    _error_if_not_equal(errors, "requirement identity", contract.get("requirement_identity"), EXPECTED_REQUIREMENT_IDENTITY)


def validate_pipeline_contract(contract: Mapping[str, Any], root: Path, check_drift: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    try:
        schema = _load_json(root / "schemas" / "pipeline.schema.json")
        errors.extend(error.message for error in Draft202012Validator(schema).iter_errors(contract))
    except Exception as exception:
        errors.append(f"pipeline schema unavailable: {exception}")
    _validate_schema_registry(contract, root, errors)
    _validate_registry_shape(contract, errors)
    _validate_bindings(contract, root, errors)
    if check_drift:
        # The amendment documents live in docs/, outside the runtime pack: checked with the projections.
        for row in contract.get("contract_amendments") or []:
            if isinstance(row, Mapping) and not (root / str(row.get("document"))).is_file():
                errors.append(f"amendment document missing: {row.get('document')}")
        from tools.render_contract_docs import _rendered_files
        for relative_path, expected in _rendered_files(contract).items():
            path = root / relative_path
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                errors.append(f"projection drift: {relative_path}")
        try:
            from tools.release_manifest import load_release_manifest

            load_release_manifest(root)
        except Exception as exception:
            errors.append(f"release manifest invalid: {exception}")
    return {"status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--full", action="store_true")
    arguments = parser.parse_args()
    try:
        contract = _load_json(Path(arguments.root).resolve() / "contracts" / "pipeline.json")
        report = validate_pipeline_contract(contract, Path(arguments.root).resolve(), arguments.full)
    except Exception as exception:
        report = {"status": "failed", "errors": [str(exception)]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
