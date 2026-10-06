# Contract Reference

Generated from `contracts/pipeline.json`. Do not edit manually.

## Core Skills

- `context-marker` — `skills/context-marker/SKILL.md`
- `tc-generator` — `skills/tc-generator/SKILL.md`
- `tc-reviewer` — `skills/tc-reviewer/SKILL.md`
- `tc-to-autotest` — `skills/tc-to-autotest/SKILL.md`
- `autotest-reviewer` — `skills/autotest-reviewer/SKILL.md`
- `orchestrate` — `skills/orchestrate/SKILL.md`

## Model stage registry

| Stage | Role | Role policy | Cardinality | Profiles |
|---|---|---|---|---|
| `orchestrate` | `controller` | `orchestrate-v1` | `once` | `cases-only-v1, local-pilot-v1` |
| `context-marker` | `generator` | `context-marker-v1` | `once` | `cases-only-v1, local-pilot-v1` |
| `tc-generator` | `generator` | `tc-generator-v1` | `per_batch` | `cases-only-v1, local-pilot-v1` |
| `tc-reviewer` | `canonical-reviewer` | `canonical-reviewer-v2` | `per_review_part` | `cases-only-v1, local-pilot-v1` |
| `tc-to-autotest` | `automation-generator` | `tc-to-autotest-v1` | `per_automation_revision` | `local-pilot-v1` |
| `autotest-reviewer` | `automation-reviewer` | `autotest-static-reviewer-v2` | `per_review_part` | `local-pilot-v1` |

## Projection profiles

| Profile | Format | Mode | Tenant status |
|---|---|---|---|
| `zephyr-scale-step-row-24-v5` | `csv` | `default` | `N_A` |
| `zephyr-scale-step-row-24-v4` | `csv` | `opt_in_compatibility` | `N_A` |
| `zephyr-scale-xml-observed-v1` | `xml` | `opt_in_observed` | `N_A` |

## Canonical rework

- rework: at most `1` per run as a `CANONICAL_REWORK` child attempt after `authoritative_verdict_rejected`
- rework generator: input `canonical_r1, findings_blocking_warning, affected_case_ids`; output `changed_test_cases_only`; successor `canonical_r2_parent_sha256_r1`
- rework review: `new_session_incremental_carry_identical_checked_inputs`; never reworked: `unchecked_scope, pre_verdict_abort`; after r2 rejected: `terminal_rework`

## Review without isolation

- isolation `none`: `continue_same_parts_and_format`; axis `review_independence`: `ISOLATED, SELF`
- `SELF` without `--accept-self-review`: accepted `false`, reason `REVIEW_NOT_INDEPENDENT`
- review inputs above `500000` bytes (run parameter) without isolation: warning `SELF_REVIEW_CONTEXT_OVERFLOW`

## Artifact registry

| Artifact | Phase | Status | Semantic ready | Component state |
|---|---|---|---|---|
| `run_manifest` | `1` | `IMPLEMENTED` | `True` | `-` |
| `run_authorization_receipt` | `1` | `IMPLEMENTED` | `True` | `-` |
| `attempt` | `1` | `IMPLEMENTED` | `True` | `-` |
| `event_journal` | `1` | `IMPLEMENTED` | `True` | `-` |
| `event` | `1` | `IMPLEMENTED` | `True` | `-` |
| `model_request` | `1` | `IMPLEMENTED` | `True` | `-` |
| `structured_result` | `1` | `IMPLEMENTED` | `True` | `-` |
| `finalization_receipt` | `1` | `IMPLEMENTED` | `True` | `-` |
| `skillsrc_configuration` | `2` | `IMPLEMENTED` | `True` | `-` |
| `skillsrc_proposal_receipt` | `2` | `IMPLEMENTED` | `True` | `-` |
| `inventory_receipt` | `2` | `IMPLEMENTED` | `True` | `-` |
| `exclusion_receipt` | `2` | `IMPLEMENTED` | `True` | `-` |
| `context_selection_receipt` | `2` | `IMPLEMENTED` | `True` | `-` |
| `execution_baseline` | `2` | `IMPLEMENTED` | `True` | `-` |
| `normalized_requirements` | `3` | `IMPLEMENTED` | `True` | `-` |
| `canonical_candidate` | `3` | `IMPLEMENTED` | `True` | `-` |
| `batch_plan` | `3` | `IMPLEMENTED` | `True` | `-` |
| `candidate_fragment` | `3` | `IMPLEMENTED` | `True` | `-` |
| `canonical_header` | `3` | `IMPLEMENTED` | `True` | `-` |
| `assembly_receipt` | `3` | `IMPLEMENTED` | `True` | `-` |
| `pre_review_audit` | `3` | `IMPLEMENTED` | `True` | `-` |
| `authoritative_verdict` | `4` | `IMPLEMENTED` | `True` | `-` |
| `candidate_bundle_receipt` | `4` | `IMPLEMENTED` | `True` | `-` |
| `successor_bundle_receipt` | `4` | `IMPLEMENTED` | `True` | `-` |
| `effective_canonical` | `4` | `IMPLEMENTED` | `True` | `-` |
| `effective_bundle_receipt` | `4` | `IMPLEMENTED` | `True` | `-` |
| `projection_bundle` | `4` | `IMPLEMENTED` | `True` | `-` |
| `reviewer_session` | `4` | `IMPLEMENTED` | `True` | `-` |
| `reviewer_evidence_transfer` | `4` | `IMPLEMENTED` | `True` | `-` |
| `review_plan` | `4` | `IMPLEMENTED` | `True` | `-` |
| `review_part_output` | `4` | `IMPLEMENTED` | `True` | `-` |
| `review_aggregate` | `4` | `IMPLEMENTED` | `True` | `-` |
| `automation` | `5` | `IMPLEMENTED` | `True` | `-` |
| `automation_static_review` | `5` | `IMPLEMENTED` | `True` | `-` |
| `execution_inputs` | `5` | `IMPLEMENTED` | `True` | `-` |
| `generated_delta` | `5` | `IMPLEMENTED` | `True` | `-` |
| `generated_file` | `5` | `IMPLEMENTED` | `True` | `-` |
| `materialization_receipt` | `5` | `IMPLEMENTED` | `True` | `-` |
| `disposition_receipt` | `5` | `IMPLEMENTED` | `True` | `-` |
| `execution_receipt` | `6` | `IMPLEMENTED` | `True` | `-` |
| `framework_evidence` | `6` | `IMPLEMENTED` | `True` | `-` |
| `environment_receipt` | `6` | `IMPLEMENTED` | `True` | `-` |
| `resume_validation_receipt` | `7` | `IMPLEMENTED` | `True` | `-` |
| `execution_trace` | `7` | `IMPLEMENTED` | `True` | `-` |
| `trace_audit` | `7` | `IMPLEMENTED` | `True` | `-` |
| `orchestrator_output` | `7` | `IMPLEMENTED` | `True` | `-` |
| `pre_finalization_trace` | `7` | `IMPLEMENTED` | `True` | `-` |
| `terminal_result` | `7` | `IMPLEMENTED` | `True` | `-` |
| `derived_terminal_trace` | `7` | `IMPLEMENTED` | `True` | `-` |
| `compatibility_evidence` | `8` | `IMPLEMENTED` | `True` | `-` |
| `retained_native_rerun_receipt` | `8` | `IMPLEMENTED` | `True` | `-` |
| `scenario_observation_receipt` | `8` | `IMPLEMENTED` | `True` | `-` |
| `release_eval_run` | `8` | `IMPLEMENTED` | `True` | `-` |
| `release_eval_receipt` | `8` | `IMPLEMENTED` | `True` | `-` |
| `release_manifest` | `8` | `IMPLEMENTED` | `True` | `-` |

## Schema registry

| Schema | Phase | Status | Target version | Semantic ready | Component state |
|---|---|---|---|---|---|
| `pipeline.schema.json` | `1` | `IMPLEMENTED` | `4.0` | `False` | `-` |
| `pilot-common.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `False` | `-` |
| `run-manifest.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `event.schema.json` | `1` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `model-request.schema.json` | `1` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `attempt.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `run-authorization-receipt.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `terminal-result.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `finalization-receipt.schema.json` | `1` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `skillsrc.schema.json` | `2` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `skillsrc-init-output.schema.json` | `2` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `inventory-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `exclusion-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `context-selection-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `execution-baseline.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `context-marker-output.schema.json` | `3` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `tc-generator-output.schema.json` | `3` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `canonical-test-document.schema.json` | `3` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `batch-plan.schema.json` | `3` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `candidate-fragment.schema.json` | `3` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `assembly-receipt.schema.json` | `3` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `tc-reviewer-output.schema.json` | `4` | `IMPLEMENTED` | `6.0.0` | `True` | `-` |
| `orchestrator-output.schema.json` | `4` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `reviewer-session.schema.json` | `4` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `review-plan.schema.json` | `4` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `review-part-output.schema.json` | `4` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `tc-to-autotest-output.schema.json` | `5` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `autotest-reviewer-output.schema.json` | `5` | `IMPLEMENTED` | `6.0.0` | `True` | `-` |
| `execution-inputs-receipt.schema.json` | `5` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `generated-delta.schema.json` | `5` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `materialization-receipt.schema.json` | `5` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `disposition-receipt.schema.json` | `5` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `run-tests-output.schema.json` | `6` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `resume-validation-receipt.schema.json` | `7` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `trace-document.schema.json` | `7` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `trace-audit-output.schema.json` | `7` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `pre-finalization-trace.schema.json` | `7` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `derived-terminal-trace.schema.json` | `7` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `driver-summary.schema.json` | `7` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `compatibility-evidence.schema.json` | `8` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `retained-native-rerun-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `scenario-observation-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-eval-run.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-eval-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-manifest.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
