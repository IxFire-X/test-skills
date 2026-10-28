---
name: tc-generator
description: Use when a 5.0.0 source-requirement context batch must become one canonical candidate fragment without inventing technical behavior.
---

# Canonical test-case generation

Consume valid 5.0.0 context and emit JSON-only bare candidate fragment: the top-level object is the fragment of `schemas/candidate-fragment.schema.json` itself, with its own `digest`, no `artifacts` envelope (`tools.api model-response` accepts only that). Read the [generation contract](references/case-generation-contract.md); `schemas/candidate-fragment.schema.json`, `schemas/canonical-test-document.schema.json`, and `tools.canonical_document` define the machine source.

The controller invokes this role once per deterministic batch with declared immutable
inputs and persists/readback-checks the complete fragment before assembly. Do not create
run state, review your own output, publish projections, or write generated project files.

## Procedure

1. Validate the context envelope. Before cases, derive the coverage conditions in the fragment's existing canonical `requirements` and source mappings as specified by the generation contract. A source paragraph may map to several conditions; do not default to one-to-one copying. Derive only supported requirements, capabilities, and technical details.
2. Create exactly one candidate fragment for the controller-owned batch namespace. Preserve distinct `SREQ-*` source IDs, assign only the controller-authorized canonical IDs, and emit explicit many-to-many source -> canonical -> case mappings. A case may cite only canonical requirements owned by that batch; `out_of_scope` is never a generator decision.
   From batch 2 the fifth input lists `operation_capabilities` of the earlier batches: reuse each one verbatim (same ID, same bytes) for the same adapter/action; define a new capability only for a new action.
   End-to-end case first (FaT-E2E). When the batch's requirements describe a process — several groups of requirements joined by shared data or state (create → change → read → remove, order → pay → ship) — the first case of the batch (`display_order` 1) is one positive path through the whole process, each step feeding the next. For a library, it is the typical chain of use: create or configure the object, call the main operation, check the result, call again on that result. The end-to-end case cites the canonical requirements of every group it passes, not only a "full cycle" requirement. It never replaces the focused cases: every requirement keeps its own cases. Order comes only from `display_order`; `requirement_ids` keep requirement order and case IDs follow the usual scheme. A case cites only its own batch: when the groups sit in different batches, build the path inside the batch. No process in the document (independent rules or functions) — no end-to-end case.
3. For every step, write three human fields: `action`, `test_data`, and `expectations[].text`. Keep the structured operation, typed inputs, previous-step output references, outputs, and assertions as a separate exact automation model. The three human fields must be independently executable and cover the machine semantics without printing binding or assertion expressions.
4. For a manual or blocked step, use its exact canonical manual/blocker branch and reason; do not turn a project-generation obstacle into invented behavior.
5. Validate the fragment's canonical content through `tools.canonical_document` and return the bare fragment (no generator envelope); the controller checks it with `tools.api validate-fragment`, `assemble` and `validate-canonical` before `model-response`. The controller assembles all immutable fragments into the sole canonical document before reviewer or projection work.

Apply the mandatory [human scenario rules](references/case-generation-contract.md#human-scenario-rules) to every human field and its structured inputs/assertions. They are the single format authority for Russian wording, HTTP/JSON, placeholders, and requirement oracles.
The shared canonical validator checks every step, including the last. Expected is a
criterion before execution: describe the observable system result, never «Проверено
автотестом» or «проверка пройдена». Runtime PASS/FAIL belongs in the execution report.
One supported action with its meaningful oracle is sufficient; do not add steps or ID
transfer to satisfy a length heuristic.
Отказ = что именно наблюдается: поле или код ошибки, класс исключения, статус, данные не изменились;
один флаг исхода (`failed=true`) — не оракул. Данные — из требований, не придумывать.
Не утверждать поведение, которого нет в документе: такое ожидание — пробел, а не assertion.

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
Record the design in the same answer. A requirement with input parameters gets `design`:
its parameters (`P-…` with a required `name`, 1–80 characters), valid and invalid classes (`C-…`; `label` only when the ID does not
speak for itself, at most 40 characters) and numeric `bounds` (`min`/`max`, `step`, `unit`).
A requirement without inputs gets no `design`. Every case gets `technique` (`EP`, `BVA`, `DT`,
`PAIRWISE`, `OTHER`) and `design_refs`: which class or bound (`min`, `max`, `below_min`,
`above_max`) it checks; a DT/PAIRWISE case names one class per parameter. Code compares the
references with the design and gives the reviewer hints; do not add cases only to silence them.
Variants of one design parameter whose cases share the steps, operations and checks and differ
only in values (literal inputs, literal expected values, `test_data`, expectation texts, title)
get one `param_group` (`PG-…`, canonical `1.1.0`): still one case per variant, each with its own
`design_refs` variant of that one parameter. People then see one case with a data table, one row
per case ID. Code checks the group and guesses none; do not group cases whose steps or checks differ.

## Projection rule

JSON is the sole machine source. The v4 standalone HTML preview takes human wording from `action`, `test_data`, and `expectations[].text`, and uses structured literal path/query inputs only to resolve the displayed URL in its three-column table `Шаг`, `Тестовые данные / запрос`, `Ожидаемый результат`. Machine outputs/assertions and binding expressions remain available to automation but never render in those cells. HTML preview and Zephyr CSV are human/export projections: they are never automation inputs and no second editable semantic copy exists.

## Stop conditions

Stop on invalid or pre-5.0 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Do not infer a blueprint from human prose, emit projections directly, or repair a rejected canonical fragment.
