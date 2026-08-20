---
name: change-scope
description: Use when test planning starts from an initial project baseline, a committed diff, worktree changes, or a supplied patch without scanning unrelated behavior.
---

# Change-scoped semantic evidence

Read the executable [change-scope contract](references/change-scope-contract.md) and `contracts/pipeline.json`. This stage creates no cases and makes no product-behavior claims: it records the reviewed semantic scope required by Context Marker.

## Procedure

1. Use `tools.feature_flow.advance_feature_flow` as the only public control surface. Perform only its returned producer or audit action, write it to the exact `record_path`, record it, then call the facade again.
2. Preserve the returned `run_mode`. The first compatible run is `FULL`; a later `CHANGE_SET` is available only from a bound compatible baseline. Never request or invent a mode, scope, shard, or count.
3. Complete both mandatory scope audits and both mandatory batch audits. Technical tests are evidence for impact only; they never originate requirements or cases.
4. Stop at `BLOCKED`. `COMPLETE` means only `READY_FOR_PIPELINE_TAIL`; it does not construct a terminal receipt or advance a baseline.

## Boundaries

- Use the portable `SEQUENTIAL` controller. Do not tune parallelism or ask the user for controller settings.
- Raw source, diffs, analytics, predecessor carriers, and audit reasoning never enter generation.
- Keep immutable prefix records and content projections create-only. A prefix ledger is resume authority only for `feature-flow`; tail readback does not make it a generator carrier.

## Stop conditions

Missing, stale, foreign, incompatible, or unreadable baseline evidence selects the safe FULL fallback and is never reinterpreted. Stop on an invalid current-run schema, unresolved module, unavailable validator or tool, secret exposure risk, required invention, or work outside the authorized scope. Do not repair rejected evidence in prose.
