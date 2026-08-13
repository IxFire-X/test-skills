# V3 case-generation contract

The bare document validated by `tools.canonical_document` is canonical. `tools.test_case_projections` renders immutable Markdown and Zephyr CSV; `tools.publish_test_case_bundle` publishes the three-file revision bundle.

## Adaptive case boundary

Use one case per independently executable scenario. Its scenario key is the evidence-backed combination of setup/role, initial state, input partition/branch condition, primary action or cohesive dependent action chain, and terminal outcome.

- Split when another branch is independently executable or changes any scenario-key component and can pass or fail on its own.
- Keep dependent preparation, actions, observations, and outputs in one multi-step case. An assertion is not a case. A coverage record is not a case. Endpoint calls, response fields, permission checks, validation paths, state observations, routes, modules, capabilities, and source symbols remain evidence or nested checks when they belong to the same control flow.
- Deduplicate overlapping evidence from the same control flow. Map every independently executable behavior exactly once: to one case, or to one coverage-only record with an evidence-backed reason when no honest executable scenario can be formed.
- Derive case count from the distinct scenario keys. Accept or invent no minimum, maximum, cap, quota, per-domain count, or numeric target.

## Human and machine ownership

For each case retain a human Title, Goal, and Preconditions. Its steps are in physical order and may be any necessary length. Each numbered step has:

1. **Action:** the human instruction.
2. **Expected Result:** the human-observable result.

The same step owns its operation, typed inputs, outputs, and assertions. An input may reference a previous-step output only. Every assertion is covered by the step's Expected Result. A manual-only step has its `manual_reason`; an automation-blocked step has exact canonical blockers. Preparation belongs in steps, not an undocumented side channel.

Never invent capability arguments, values, operations, assertions, roles, or environment facts. Validate before publication; a projection cannot repair invalid semantics.
