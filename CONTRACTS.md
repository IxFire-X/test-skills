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

## Review modes

- review mode `--review-mode`: `pairs, compact-v1`; default `compact-v1`
- `compact-v1` model input: `review_projection_text` (`review-projection-v1`, `canonical_document_digest_in_snapshot`); never model inputs: `html, markdown, csv`
- `compact-v1` plan `review-plan-compact.schema.json`, answer `review-part-output-compact.schema.json`; packing `whole_cases_content_defined_blocks_block_pairs`; `every_case_pair_in_one_part`; areas `source, case_home_part, cross_per_part`
- `compact-v1` order `independent_parts_batchable` (`--max-tasks`); answer checks `refs_are_part_anchors, case_area_cites_own_anchor, every_lint_suspicion_answered, info_findings_at_most_5, related_ids_known`
- `compact-v1` corrections `closed_field_dictionary_one_pointer_before_checked`; carry key `area_fingerprint, role_policy, skill_digest, instructions_digest, model_id, mode`

## Acceptance reason codes

- `no_unresolved_blocker` failed (`local-pilot-v1`): reason `UNRESOLVED_AUTOMATION_BLOCKER` on `blocker_count_positive`, when `verification_pass_without_earlier_reason`, precedence `after_review_reasons`; legacy results `reason_absent_stays_valid`

## Contract amendments (opt-in)

- `pilot-contract-amendments-2026-10-07` (wave 2, `ACCEPTED`, opt-in `true`): §1.2, §1.5, §17 — `docs/superpowers/specs/2026-10-07-pilot-contract-amendments.md`
- optional stage `MUTATION` between `EXECUTION_TRACE` and `RETAIN_OR_CLEANUP_DECISION`; requires `skillsrc_mutation_enabled, run_authorization_mutation_requested, authoritative_pass_or_fail`; mutates `passing_generated_methods_only`; writes `run_directory_only`; proves `project_inventory_unchanged`; receipt `mutation_receipt`; axis `test_strength`; never changes `verification, accepted, dispositions, earlier_evidence`
- mutation tool (Java): `org.pitest:pitest-command-line:1.30.0` + `org.pitest:pitest-junit5-plugin:1.2.3`, pins `tools/mutation_tools.json`, resolution `project_build_tool_local_repository`, launcher `junit_platform_launcher_of_project_version`, consent `skillsrc_mutation_enabled, run_authorization_mutation_requested`, digest mismatch `NOT_RUNNABLE`, mutators `DEFAULTS`, report `xml_full_mutation_matrix`; Python: `not_implemented`
- model runner `--review-runner`: `host, process`; default `host`; presets `claude, codex`; custom template `launch_flag_only`; `.skillsrc` fields `preset, models, max_parallel, timeout_seconds`; invocation `fresh_process_temp_cwd_stdin_no_write_tools`; wait action `wait`; tries per part `3`; standalone `run --runner process`
- runner evidence `command_digest, cli_name_version, model, started_finished, exit_code, session_id, stdout_digest, tokens, user_settings_loaded` → axis `isolation_evidence`; `--require-driver-isolation` rejects lower levels with `REVIEW_ISOLATION_UNVERIFIED`
- optional skill `mutation-triage` — `skills/mutation-triage/SKILL.md`
- optional stage `mutation-triage`: role `strength-analyst`, policy `mutation-triage-v1`, cardinality `post_terminal_per_task`, profiles `local-pilot-v1`, answer `mutation-triage-output.schema.json`, decisions `TEST_GAP, SPEC_GAP, EQUIVALENT, OUT_OF_SCOPE`, changes `nothing_in_the_attempt`
- optional schema `mutation-receipt.schema.json` (phase 6, `IMPLEMENTED`, `1.0.0`)
- optional artifact `mutation_receipt` (phase 6, `IMPLEMENTED`)
- optional artifact `mutation_triage` (phase 6, `IMPLEMENTED`)
- optional artifact `analyst_report` (phase 7, `IMPLEMENTED`)
- optional artifact `runner_process_evidence` (phase 4, `IMPLEMENTED`)
- optional axis `test_strength`: `MEASURED, NOT_RUNNABLE, NOT_APPLICABLE`; nullable `True`
- optional axis `isolation_evidence`: `DRIVER_PROCESS, HOST_DECLARED, NONE`; nullable `True`

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
| `skillsrc.schema.json` | `2` | `IMPLEMENTED` | `5.1.0` | `True` | `-` |
| `skillsrc-init-output.schema.json` | `2` | `IMPLEMENTED` | `5.0.0` | `True` | `-` |
| `inventory-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `exclusion-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `context-selection-receipt.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `execution-baseline.schema.json` | `2` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `context-marker-output.schema.json` | `3` | `IMPLEMENTED` | `5.1.0` | `True` | `-` |
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
| `review-plan-compact.schema.json` | `4` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `review-part-output-compact.schema.json` | `4` | `IMPLEMENTED` | `2.1.0` | `True` | `-` |
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
| `driver-summary.schema.json` | `7` | `IMPLEMENTED` | `1.1.0` | `True` | `-` |
| `compatibility-evidence.schema.json` | `8` | `IMPLEMENTED` | `2.0.0` | `True` | `-` |
| `retained-native-rerun-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `scenario-observation-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-eval-run.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-eval-receipt.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
| `release-manifest.schema.json` | `8` | `IMPLEMENTED` | `1.0.0` | `True` | `-` |
