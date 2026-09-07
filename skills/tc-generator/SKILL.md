---
name: tc-generator
description: Use when a 5.0.0 source-requirement context batch must become one canonical candidate fragment without inventing technical behavior.
---

# Canonical test-case generation

Consume valid 5.0.0 context and emit JSON-only `artifacts.candidate_fragment`. Read the [generation contract](references/case-generation-contract.md); `schemas/tc-generator-output.schema.json`, `schemas/candidate-fragment.schema.json`, `schemas/canonical-test-document.schema.json`, and `tools.canonical_document` define the machine source.

The controller invokes this role once per deterministic batch with declared immutable
inputs and persists/readback-checks the complete fragment before assembly. Do not create
run state, review your own output, publish projections, or write generated project files.

## Procedure

1. Validate the context envelope. Before cases, derive the coverage conditions in the fragment's existing canonical `requirements` and source mappings as specified by the generation contract. A source paragraph may map to several conditions; do not default to one-to-one copying. Derive only supported requirements, capabilities, and technical details.
2. Create exactly one candidate fragment for the controller-owned batch namespace. Preserve distinct `SREQ-*` source IDs, assign only the controller-authorized canonical IDs, and emit explicit many-to-many source -> canonical -> case mappings. A case may cite only canonical requirements owned by that batch; `out_of_scope` is never a generator decision.
3. For every step, write three human fields: `action`, `test_data`, and `expectations[].text`. Keep the structured operation, typed inputs, previous-step output references, outputs, and assertions as a separate exact automation model. The three human fields must be independently executable and cover the machine semantics without printing binding or assertion expressions.
4. For a manual or blocked step, use its exact canonical manual/blocker branch and reason; do not turn a project-generation obstacle into invented behavior.
5. Validate the fragment's canonical content through `tools.canonical_document`, wrap it in the 5.0.0 generator envelope, validate it against `schemas/tc-generator-output.schema.json`, and return it. The controller assembles all immutable fragments into the sole canonical document before reviewer or projection work.

Apply the mandatory [human scenario rules](references/case-generation-contract.md#human-scenario-rules) to every human field and its structured inputs/assertions. They are the single format authority for Russian wording, HTTP/JSON, placeholders, and requirement oracles.

For each product-behavior requirement, cover all distinct supported behavior classes, not merely
one happy-path case. Consider success, invalid input, relevant boundaries, authorization,
state transitions, and required side effects; include concurrency/idempotency only when
the authorized behavior needs them. Each case must have a reproducible setup and an
observable oracle that could fail if that requirement were broken. A requirement ID in
a title/objective or relation is insufficient. Keep unresolved cases as explicit gaps;
never impose a test-count ceiling or use a full Cartesian matrix without a reason.
Use equivalence classes and boundary values for input rules, feasible decision-rule
combinations for interacting conditions, and transitions for stateful behavior when
applicable. These guide selection within the authorized scope, not mandatory extra cases.

## Projection rule

JSON is the sole machine source. The v4 standalone HTML preview takes human wording from `action`, `test_data`, and `expectations[].text`, and uses structured literal path/query inputs only to resolve the displayed URL in its three-column table `Шаг`, `Тестовые данные / запрос`, `Ожидаемый результат`. Machine outputs/assertions and binding expressions remain available to automation but never render in those cells. HTML preview and Zephyr CSV are human/export projections: they are never automation inputs and no second editable semantic copy exists.

## Stop conditions

Stop on invalid or pre-5.0 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Do not infer a blueprint from human prose, emit projections directly, or repair a rejected canonical fragment.
