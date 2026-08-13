---
name: tc-generator
description: Use when a V3 context artifact must become a canonical test-case document and immutable Zephyr-facing human projections without inventing technical behavior.
---

# Canonical test-case generation

Consume valid V3 context and emit JSON-only `artifacts.canonical_document`. Read the [generation contract](references/case-generation-contract.md); `schemas/tc-generator-output.schema.json`, `schemas/canonical-test-document.schema.json`, and `tools.canonical_document` define the machine source.

## Procedure

1. Validate the context envelope. Derive only supported requirements, capabilities, and technical details.
2. Derive case boundaries before assigning IDs. Use one case per independently executable scenario. Build its scenario key from setup/role, initial state, input partition/branch condition, primary action or cohesive dependent action chain, and terminal outcome. Split a branch only when it is independently executable; deduplicate overlapping evidence from the same control flow. Derive the count from those keys, never from a numeric target.
3. Create one canonical document with stable IDs and physical ordering. A case projects as a Title, Goal, and Preconditions with arbitrary sequential steps; preparation is represented as steps.
4. For every step, provide human action and expected result plus structured operation, typed inputs, previous-step output references, outputs, and assertions when automation is possible. Human expected result covers every machine assertion.
5. For a manual or blocked step, use its exact canonical manual/blocker branch and reason; do not turn a project-generation obstacle into invented behavior.
6. Validate the bare document through `tools.canonical_document`, wrap it in the V3 generator envelope, validate it against `schemas/tc-generator-output.schema.json`, and return it. The pipeline later derives immutable Markdown and Zephyr CSV projections from the selected revision.

## Projection rule

JSON is the sole machine source. Markdown and Zephyr CSV are human/export projections: they are never automation inputs and no second editable semantic copy exists.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Do not infer a blueprint from human prose, emit projections directly, or repair a rejected canonical document.
