# V3 case-generation contract

The bare document validated by `tools.canonical_document` is canonical. `tools.test_case_projections` renders immutable Markdown and Zephyr CSV; `tools.publish_test_case_bundle` publishes the three-file revision bundle.

## Human and machine ownership

For each case retain a human Title, Goal, and Preconditions. Its steps are in physical order and may be any necessary length. Each numbered step has:

1. **Action:** the human instruction.
2. **Expected Result:** the human-observable result.

The same step owns its operation, typed inputs, outputs, and assertions. An input may reference a previous-step output only. Every assertion is covered by the step's Expected Result. A manual-only step has its `manual_reason`; an automation-blocked step has exact canonical blockers. Preparation belongs in steps, not an undocumented side channel.

Never invent capability arguments, values, operations, assertions, roles, or environment facts. Validate before publication; a projection cannot repair invalid semantics.
