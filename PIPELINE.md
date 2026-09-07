# Pipeline: portable-testing-skills-frozen-pilot

Generated from `contracts/pipeline.json`. Do not edit manually.

Version: `4.0`

## Phase 1 public runtime seam

- `create_run(project: 'Path', policy_profile: 'str', authorization: 'Mapping[str, Any]') -> 'Mapping[str, Any]'`
- `append_event(run_root: 'Path', event_type: 'str', *, actor: 'str', attempt_id: 'str | None' = None, batch_id: 'str | None' = None, artifact_digest: 'str | None' = None, stage_instance_id: 'str | None' = None, transport_attempts: 'int | None' = None) -> 'Mapping[str, Any]'`
- `create_attempt(run_root: 'Path', identity: 'Mapping[str, Any]', baseline: 'Mapping[str, Any]') -> 'Mapping[str, Any]'`
- `derive_state(run_root: 'Path') -> 'Mapping[str, Any]'`
- `terminal_result(facts: 'Mapping[str, Any]', policy_profile: 'str') -> 'Mapping[str, Any]'`
- `exit_code(result: 'Mapping[str, Any] | None', controller_error: 'bool' = False) -> 'int'`

## Event order

`RUN_CREATED` → `MODULE_SELECTED` → `INVENTORY_READY` → `SNAPSHOT_BOUND` → `EXECUTION_BASELINE_FROZEN` → `ATTEMPT_CREATED` → `CONTEXT_SELECTED` → `MODEL_REQUESTED` → `MODEL_RESPONSE_RECEIVED` → `CANDIDATE_PUBLISHED` → `REVIEW_REQUESTED` → `ARTIFACT_READ_BACK` → `WAITING_FOR_MODEL`

## Global ordering constraints

- `RUN_CREATED → SCAN_OR_MODEL_REQUEST`
- `MODULE_SELECTED → ATTEMPT_CREATED`
- `SNAPSHOT_BOUND → ATTEMPT_CREATED`
- `EXECUTION_BASELINE_FROZEN → ATTEMPT_CREATED`
- `ATTEMPT_CREATED → SOURCE_BYTES_TRANSMITTED_TO_MODEL`
- `INVENTORY_READY → MODEL_REQUESTED`
- `SNAPSHOT_BOUND → MODEL_REQUESTED`
- `CONTEXT_SELECTED → MODEL_REQUESTED`
- `MODEL_RESPONSE_RECEIVED → CANDIDATE_PUBLISHED`
- `CANDIDATE_PUBLISHED → REVIEW_REQUESTED`
- `FINALIZATION_RECEIPT_READ_BACK → TERMINAL_EVENT`
- `TERMINAL_EVENT → TERMINAL_RETRY_OBSERVED` (narrow exception)
- `SCENARIO_OBSERVATION_READ_BACK → TERMINAL_RETRY_OBSERVED` (narrow exception)
- `TERMINAL_EVENT → RETAINED_NATIVE_RERUN` (narrow exception)
- `RETAINED_NATIVE_RERUN_RECEIPT_READ_BACK → RETAINED_NATIVE_RERUN` (narrow exception)
- `EXECUTION_UNKNOWN → PROCESS_STOPPED` (narrow exception)

## Reviewer session

- one session; evidence pairs `0..bounded`; successful verdicts `1`
- terminal pre-verdict abort verdicts `0`
- forbidden: `per_batch_reviewers, hierarchical_reviewers, multiple_authoritative_reviewers`

## Physical lifecycle

`MATERIALIZATION` → `EXECUTION` → `EXECUTION_TRACE` → `RETAIN_OR_CLEANUP_DECISION` → `DISPOSITION_RECEIPTS` → `PRE_FINALIZATION_TRACE` → `FINALIZATION_VERIFICATION` → `FINALIZATION_RECEIPT_READ_BACK` → `TERMINAL_RESULT` → `DERIVED_TERMINAL_TRACE` → `TERMINAL_EVENT`

## Result axes

- `attempt_state`: `ACTIVE, WAITING_FOR_INPUT, WAITING_FOR_MODEL, TERMINAL`; nullable `False`
- `completion`: `COMPLETE, PARTIAL, FATAL`; nullable `True`
- `verification`: `PASS, FAIL, UNKNOWN, NOT_RUNNABLE, NOT_APPLICABLE`; nullable `True`
- `coverage`: `FULL, MIXED, MANUAL_ONLY`; nullable `True`
- `reason_code`: preterminal `absent`; terminal `written_once`
- `accepted`: preterminal `absent`; terminal `boolean`

## Normative result tuples

- `complete_fail`: `{"accepted": false, "attempt_state": "TERMINAL", "completion": "COMPLETE", "verification": "FAIL"}`
- `execution_unknown`: `{"accepted": false, "attempt_state": "TERMINAL", "completion": "PARTIAL", "reason_code": "EXECUTION_UNKNOWN", "verification": "UNKNOWN"}`
- `pre_execution_rework`: `{"accepted": false, "attempt_state": "TERMINAL", "completion": "PARTIAL", "reason_code": "REWORK", "verification": "NOT_APPLICABLE"}`
- `early_fatal`: `{"accepted": false, "attempt_state": "TERMINAL", "completion": "FATAL", "coverage": null}`
- `review_context_limit`: `{"accepted": false, "attempt_state": "TERMINAL", "completion": "PARTIAL", "reason_code": "REVIEW_CONTEXT_LIMIT", "verification": "NOT_APPLICABLE"}`
- `invalid_finalization`: `{"accepted": false, "attempt_state": "TERMINAL", "coverage": "PRESERVED", "reason_code": "FINALIZATION_INVALID", "verification": "PRESERVED"}`

## Policies

| Policy | Version | Semantic ready |
|---|---|---|
| `cases-only-v1` | `v1` | `True` |
| `local-pilot-v1` | `v1` | `True` |

## Adapters

| Adapter | Phase | Status | Version |
|---|---|---|---|
| `pytest:selected-symbols-v1` | `6` | `IMPLEMENTED` | `v1` |
| `maven-wrapper:selected-symbols-v1` | `6` | `IMPLEMENTED` | `v1` |
| `gradle-wrapper:selected-symbols-v1` | `6` | `IMPLEMENTED` | `v1` |

## Acceptance predicates

- `cases-only-v1`: `coverage_full_mixed_or_manual_only, verification_not_applicable, canonical_schema_semantic_provenance_valid, successful_full_document_authoritative_review, exactly_one_authoritative_verdict, reviewer_isolation_verified, no_unresolved_blocker, branch_valid_trace, finalization_valid, materialization_not_applicable, execution_not_applicable, draft_artifact_only_not_accepted`
- `local-pilot-v1`: `accepted_canonical, accepted_automation, complete_generated_delta_materialization, authoritative_exact_target_pass, valid_trace, every_required_generated_file_retained, finalization_valid, reviewer_isolation_verified, no_unresolved_blocker, mixed_manual_coverage_traceable`

## Exit priority

- `controller_error_without_trustworthy_attempt_result => 2`
- `waiting_for_input_or_model => 3`
- `valid_terminal_cases_only_v1 => 1`
- `accepted_terminal => 0`
- `unknown_not_runnable_fatal_invalid_closure_or_unreliable_evidence => 2`
- `other_trustworthy_terminal_unaccepted => 1`

## Release qualification

- package: `0.5.0-pilot` via `release/manifest.json`
- compatibility contract: `portable-cli-v1`
- execution profile: `v1`
- release eval: `adaptive-1-3-5-v1` / `pilot-critical-v1`
- component state: `implemented_unverified`; ready tuple: `null`
- core-pilot N/A: `company_runner, zephyr_tenant, production_rollback`
