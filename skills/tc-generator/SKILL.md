---
name: tc-generator
description: Use when a 5.0.0 source-requirement context batch must become one canonical candidate fragment without inventing technical behavior.
permissions:
  read: "step inputs from the controller"
  write: "role answer in <run>/tmp/ only"
  network: "none"
  external_delivery: "none"
  dependencies: "none"
  rollback: "delete the run directory"
---

# Canonical test-case generation

Consume valid 5.0.0 context and emit JSON-only bare candidate fragment: the top-level object is the fragment of `schemas/candidate-fragment.schema.json` itself, no `artifacts` envelope. The code puts the mechanics (digest fields, `context_receipt`, `display_order`, ids, defaults, derivable `semantic_type`): do not write it, the list and the id scheme are in [the sample](../orchestrate/references/fragment-sample.md). Write JSON in one line, no indentation (a line break only between cases). Read the [generation contract](references/case-generation-contract.md); `schemas/candidate-fragment.schema.json`, `schemas/canonical-test-document.schema.json`, and `tools.canonical_document` define the machine source.

The controller invokes this role once per deterministic batch with declared immutable
inputs and persists/readback-checks the complete fragment before assembly. Do not create
run state, review your own output, publish projections, or write generated project files.

## Procedure

1. Validate the context envelope. Before cases, derive the coverage conditions in the fragment's existing canonical `requirements` and source mappings as specified by the generation contract. Derive only supported requirements, capabilities, and technical details.
2. Create exactly one candidate fragment for the controller-owned batch namespace. Preserve distinct `SREQ-*` source IDs, assign only the controller-authorized canonical IDs, and emit explicit many-to-many source -> canonical -> case mappings.
   Import of ready cases (FaT-IMP): the last input `imported` holds the cases of this batch from a human CSV (its digest is `batch_skeletons[<batch_id>]` of the plan publication). Keep every case of the batch in its order with title, objective, preconditions, priority, each action, `test_data` and expectation text exactly as given (same counts); derive only canonical requirements, mappings, capabilities and the machine part of each step. What the text does not support stays manual or blocked with its reason — never invent it. The controller refuses any change of a human field (`IMPORT_HUMAN_FIELD_CHANGED`). Copy `management` from the case as well: Key → `external_keys.zephyr_scale`, folder, labels, components, owner, status, estimated time, issues/pages → `external_links`, `custom_fields` (an empty Key — no `zephyr_scale`). A case traces to exactly its `source_requirement_ids` (in stage 1 its `source_requirement_id`), through the canonical requirements of those source requirements (`IMPORT_TRACE_CHANGED`); a case per imported case only, none for any other requirement.
   From batch 2 the input right after the header lists `operation_capabilities` of the earlier batches: reuse each one verbatim (same ID, same bytes) for the same adapter/action; define a new capability only for a new action.
   End-to-end case first (FaT-E2E). When the batch's requirements describe a process — several groups of requirements joined by shared data or state (create → change → read → remove, order → pay → ship) — the first case of the batch (first in the array) is one positive path through the whole process, each step feeding the next. For a library, it is the typical chain of use: create or configure the object, call the main operation, check the result, call again on that result. The end-to-end case cites the canonical requirements of every group it passes, not only a "full cycle" requirement. It never replaces the focused cases: every requirement keeps its own cases. Order is the position in the array; `requirement_ids` keep requirement order and case IDs follow the usual scheme. A case cites only its own batch: when the groups sit in different batches, build the path inside the batch. No process in the document (independent rules or functions) — no end-to-end case.
   Existing project tests (FaT-EC): `read` of `prepare` may list `existing-tests.md` (basis «карта») — which endpoints and classes the project's own tests already call, with their outcomes. It is a reference: write the cases every requirement needs anyway and never weaken a case because a project test exists; a case that adds no new condition to such a test is a duplicate.
3. For every step, write three human fields: `action`, `test_data`, and `expectations[].text`. Keep the structured operation, typed inputs, previous-step output references, outputs, and assertions as a separate exact automation model. The three human fields must be independently executable and cover the machine semantics without printing binding or assertion expressions.
   Key order in the answer (FaT-P3): in every case write the human fields first — `title`, `objective`, `preconditions`, and in each step
   `action`, `test_data`, `expectations[].text` — and the machine part after them (`operation`, `inputs`, `outputs`, assertions). JSON keys
   have no order for the validator; an answer cut off by the output limit then still holds readable cases, but it stays a refusal
   (`MODEL_OUTPUT_TRUNCATED`) — never complete it by hand.
4. For a manual or blocked step, use its exact canonical manual/blocker branch and reason; do not turn a project-generation obstacle into invented behavior.
   Before a blocker `UNSUPPORTED_ASSERTION_DIALECT` (FaT-BL), check whether the assertion fits the operators of the canonical schema: `equals`, `not_equals`, `exists`, `not_exists`, `contains`, `matches`, `greater_than`, `greater_or_equal`, `less_than`, `less_or_equal`, `length_equals`, `schema_matches`. «IDs differ» is `not_equals` with `expected` of kind `step_output` (the other step's output); «exactly N elements» is `length_equals` with an integer `literal`; «all unique» is `not_equals` per pair of outputs. One blocker blocks every autotest of the attempt: use it only when none of these operators expresses the check.
5. Return the bare fragment; the controller checks it before recording it (`submit --dry-run` checks without a record; a refusal goes into the run's journal, which you may not write), and assembles the last batch before its answer is written. The controller assembles all immutable fragments into the sole canonical document before reviewer or projection work.

Apply the mandatory [human scenario rules](references/case-generation-contract.md#human-scenario-rules) and the case form in them to every human field and its structured inputs/assertions. They are the single format authority for Russian wording, HTTP/JSON, placeholders, and requirement oracles; the shared canonical validator checks every step, including the last.
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
For a matching rule or a boundary, also write a case for the nearest non-matching case that must not pass, even when the requirement does not name it: search by prefix — a string where the sequence is not at the start; exact match — a value differing in letter case or by one character; boundary — the value just beyond it. The matching rule itself defines the outcome of a mismatch (not found, rejected): assert it, with an operation and an assertion. The case is an observation (item 7 of «Case form») only when the document really leaves the outcome open; never invent the behavior.

Cases-only mode on a project with code (the batch context holds project code or the map of its tests): write steps with a structured operation and assertions; `manual_only` is an exception with its reason in `manual_reason`, not a way to write a case faster. A documents-only project is a legitimate case: manual steps are the norm there.

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

JSON is the sole machine source. HTML preview and Zephyr CSV (what they show: the generation contract) are human/export projections: never automation inputs, no second editable semantic copy; machine outputs, assertions and binding expressions never render in them.

## Stop conditions

Stop on invalid or pre-5.0 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Do not infer a blueprint from human prose, emit projections directly, or repair a rejected canonical fragment.
