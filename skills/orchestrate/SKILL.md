---
name: orchestrate
description: Use when a request needs coordinated V3 test-case selection, automation generation, execution evidence, and trace finalization across the testing pipeline.
---

# V3 test-pipeline orchestration

Follow Pipeline 2.0 in `contracts/pipeline.json` and the [orchestration contract](references/orchestration-contract.md). Resolve skill paths only through `skill_files`; do not use copies or aliases.

## Lifecycle

1. Validate and publish candidate canonical JSON with `tools.publish_test_case_bundle` before review.
2. Call `orchestrate_revision` from `tools.orchestrate_test_case_revision` with candidate, review, output directory, and CSV profile. Publish a valid full successor before selecting it; choose exactly one effective revision and effective digest.
3. Pass only effective JSON/digest downstream. Generate and statically review automation; on reviewer auto-fix, regenerate then review again.
4. Skip `tools.run_tests.py` only for BLOCKED or zero-pair manual branches. Build trace with `tools.build_trace_document`, call `validate_trace_document`, then call `tools.trace_check` for every terminal branch.
5. Call `finalize_orchestration` to finalize exactly one status: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`, `FAIL`, or `NOT_RUNNABLE`.

Markdown/CSV are immutable human projections: never parse them for automation, hand-author receipts, merge revisions, or treat manual/blocker states as PASS. Do not hand-build the terminal carrier. Keep generated tests isolated; do not change project code, dependencies, configuration, or secrets.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. Stop on a failed lifecycle receipt, unselected effective revision, invalid trace, or a status/branch mismatch.
