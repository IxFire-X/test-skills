---
name: tc-generator
description: Use when a 5.0.0 source-requirement context batch must become one canonical candidate fragment without inventing technical behavior.
---

# Canonical test-case generation

Consume valid 5.0.0 context and emit one JSON-only bare candidate fragment. Read the [generation contract](references/case-generation-contract.md); `schemas/candidate-fragment.schema.json`, `schemas/canonical-test-document.schema.json`, and `tools.canonical_document` define the machine source. The controller validates the stage output against `schemas/candidate-fragment.schema.json` directly; it does not accept the `tc-generator-output` envelope.

The controller invokes this role once per deterministic batch with declared immutable
inputs and persists/readback-checks the complete fragment before assembly. Do not create
run state, review your own output, publish projections, or write generated project files.

## Procedure

1. Validate the context envelope. Before cases, derive the coverage conditions in the fragment's existing canonical `requirements` and source mappings as specified by the generation contract. A source paragraph may map to several conditions; do not default to one-to-one copying. Derive only supported requirements, capabilities, and technical details.
2. Create exactly one candidate fragment for the controller-owned batch namespace. Preserve distinct `SREQ-*` source IDs, assign only the controller-authorized canonical IDs, and emit explicit many-to-many source -> canonical -> case mappings. A case may cite only canonical requirements owned by that batch; `out_of_scope` is never a generator decision.
3. For every step, write three human fields: `action`, `test_data`, and `expectations[].text`. Keep the structured operation, typed inputs, previous-step output references, outputs, and assertions as a separate exact automation model. The three human fields must be independently executable and cover the machine semantics without printing binding or assertion expressions.
4. For a manual or blocked step, use its exact canonical manual/blocker branch and reason; do not turn a project-generation obstacle into invented behavior.
5. Validate the fragment's canonical content through `tools.canonical_document` and return the bare fragment itself, valid against `schemas/candidate-fragment.schema.json`. Do not wrap it in a `tc-generator-output` envelope or under `artifacts`. Its `batch_id` equals the batch of this stage (`tc-generator:<batch_id>`), its `digest` is the fragment's own digest over all other fields, and the embedded `context_receipt` keeps its own digest; take these service digests from the controller tools, never type them by hand. The controller assembles all immutable fragments into the sole canonical document before reviewer or projection work. It sorts `operation_capabilities` itself, merges an identical capability declared by several batches even when their provenance differs, and rejects a semantic difference as `BATCH_CAPABILITY_CONFLICT` naming both batches.

Apply the mandatory [human scenario rules](references/case-generation-contract.md#human-scenario-rules) to every human field and its structured inputs/assertions. They are the single format authority for Russian wording, HTTP/JSON, placeholders, and requirement oracles.
The shared canonical validator checks every step, including the last. Expected is a
criterion before execution: describe the observable system result, never «Проверено
автотестом» or «проверка пройдена». Runtime PASS/FAIL belongs in the execution report.
One supported action with its meaningful oracle is sufficient; do not add steps or ID
transfer to satisfy a length heuristic.

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

## Rework mode

A task with `rework: true` follows a REJECTED canonical review. Its first input is the
brief: `findings` (BLOCKING and WARNING only), `affected_case_ids` and the batch's
`r1_cases`; the second is the whole r1 document. Fix what the findings name, then return
only `test_cases` with every changed case in full: keep its `case_id` (and the IDs of
unchanged steps, inputs and assertions where they still apply); a new case uses the
brief's `id_prefixes`. Do not return unchanged cases, requirements, mappings or
capabilities: the controller keeps them from r1 and builds canonical r2 with
`parent_sha256` of r1. There is one rework per run.

## Projection rule

JSON is the sole machine source. The standalone HTML preview takes human wording from `action`, `test_data`, and `expectations[].text`, and uses structured literal path/query inputs only to resolve the displayed URL in its three-column table `Шаг`, `Тестовые данные / запрос`, `Ожидаемый результат`. Machine outputs/assertions and binding expressions remain available to automation but never render in those cells. A non-literal path/query value is shown as a readable placeholder such as `<ownerId из шага 1>` or `<переменная окружения X>`. HTML preview and Zephyr CSV are human/export projections: they are never automation inputs and no second editable semantic copy exists. The default CSV profile is `zephyr-scale-step-row-24-v5`, which carries only the human fields (the action with the resolved URL, `test_data` and the expectation text); v4 is produced only on an explicit `--csv-profile zephyr-scale-step-row-24-v4`.

## Stop conditions

Stop on invalid or pre-5.0 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Do not infer a blueprint from human prose, emit projections directly, or repair a rejected canonical fragment.
