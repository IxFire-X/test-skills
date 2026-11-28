# The generator's fragment: structure and how to build a big one

The `tc-generator` answer is one JSON file, a **bare fragment** (no envelope): an object by `schemas/candidate-fragment.schema.json`; the cases in it
follow `schemas/canonical-test-document.schema.json`. Below is only the form. You take the values in `<…>` from the batch inputs (plan, header, the marking
answer); the code puts the mechanics itself (the list under the case sample). Write JSON without indentation and extra spaces; a line break only between cases.

```json
{
  "schema_version": "1.0.0",
  "status": "COMPLETE",
  "batch_id": "<BATCH-… from the plan>",
  "namespace": "<B1, B2, … from the plan>",
  "owned_source_requirement_ids": ["<SREQ-… of this batch>"],
  "requirements": [
    { "requirement_id": "CREQ-<ns>-001", "text": "<условие, выведенное из источника>", "provenance": ["<path:line-line>"] }
  ],
  "source_to_canonical_mappings": [
    { "source_requirement_id": "<SREQ-…>", "canonical_requirement_ids": ["CREQ-<ns>-001"] }
  ],
  "operation_capabilities": [
    { "capability_id": "CAP-<ns>-<name>", "action": "<operation name>", "adapter": "<adapter.id from read; none there — one name of the project's test runner for the whole run>", "arguments": [],
      "results": [ { "name": "count", "semantic_type": { "kind": "json", "type": "integer" } } ], "provenance": ["<source phrase>"] }
  ],
  "test_cases": [ "… cases, the form below …" ]
}
```

A case (fields for the human — in Russian, technical tokens — as in the schema):

```json
{
  "case_id": "TC-<ns>-001", "priority": "HIGH", "categories": ["positive", "functional"],
  "title": "<название>", "objective": "<цель>", "preconditions": ["<условие данных, не условие работы над тестами>"],
  "requirement_ids": ["CREQ-<ns>-001"],
  "management": { "folder": "<папка>", "components": [], "labels": [], "owner": null, "status": null, "estimated_time": null,
                  "custom_fields": {}, "external_keys": {}, "external_links": { "issues": [], "pages": [] } },
  "steps": [
    {
      "action": "<что сделать>", "test_data": "<данные>",
      "operation": { "kind": "project_action", "capability_id": "CAP-<ns>-<name>" },
      "inputs": [ { "target": { "location": "arg", "name": "owner_id" }, "source": { "kind": "literal", "value": 7 } } ],
      "outputs": [ { "source": { "kind": "project_result", "name": "count" } } ],
      "expectations": [
        { "text": "<наблюдаемый результат>",
          "assertions": [ { "operator": "equals", "actual": { "kind": "project_result", "name": "count" }, "expected": { "kind": "literal", "value": 0 } } ] }
      ]
    }
  ]
}
```

The second case is the same frame with another `case_id` and its own `requirement_ids`; each step has its own `operation.capability_id` from `operation_capabilities`.

**The code puts itself (do not write):** `display_order` (the position, from 1); `step_id` = `STEP-<case_id tail after TC->-<NN>` (`TC-B1-003`, step 2 → `STEP-B1-003-02`),
`input_id` = `INPUT-<tail>-<NN>-<k>`, `expectation_id` = `EXP-…-<NN>-<k>`, `assertion_id` = `ASSERT-…-<NN>-<k>-<j>`; `output_id` = the `name` of the source;
`context_receipt`, the digest fields, `diagnostics`; `manual_only: false`, `manual_reason: null`, `automation_blockers: []`, `sensitive`; `semantic_type` when the type
can be derived: for an input — the type of the literal, of the `step_output` output or of the capability argument; for an output — the type of the capability result (`http_status` — integer, `http_header` — string).
Cannot be derived (`fixture`, `environment`, `secret_handle`, `http_body`) — write it yourself. The code does not change your type: one that differs from the literal's type fails the check.
Yours: `case_id`, `requirement_ids`, `capability_id`, texts, values, `output_id` for `http_body`/`http_status`; a `step_output` reference — by the full step id
in this scheme (`"step_id": "STEP-B1-003-02"`).

## An HTTP step (`operation.kind: "http"`)

Two steps of a case: the first creates an owner and returns `ownerId`, the second reads it. The identifier from the answer stays `integer`: a `path` input of that type is allowed, the code builds the URL — do not change the output type to suit the path.
Do not write the response body into the `text` of an expectation: the code builds `Пример тела:` from the `http_body` assertions and the step outputs (and removes the extra); a root pointer and a skipped array index are the exception, there you write one JSON block yourself, last.

```json
[
  { "action": "POST /api/owners", "test_data": "{\n  \"lastName\": \"Davis\"\n}",
    "operation": { "kind": "http", "binding_profile": "http-binding-v1", "base_url_source": { "kind": "environment", "name": "BASE_URL", "provenance": ["<source phrase>"] }, "method": "POST", "path": "/api/owners" },
    "inputs": [ { "target": { "location": "body", "pointer": "/lastName" }, "source": { "kind": "literal", "value": "Davis" } } ],
    "outputs": [ { "output_id": "ownerId", "source": { "kind": "http_body", "pointer": "/id" }, "semantic_type": { "kind": "json", "type": "integer" }, "type_provenance": ["<contract file: schema>"] } ],
    "expectations": [ { "text": "Владелец создан.\n\nHTTP 201 Created", "assertions": [ { "operator": "equals", "actual": { "kind": "http_status" }, "expected": { "kind": "literal", "value": 201 } } ] } ] },
  { "action": "GET /api/owners/{ownerId}", "test_data": "Тело запроса отсутствует.",
    "operation": { "kind": "http", "binding_profile": "http-binding-v1", "base_url_source": { "kind": "environment", "name": "BASE_URL", "provenance": ["<source phrase>"] }, "method": "GET", "path": "/api/owners/{ownerId}" },
    "inputs": [ { "target": { "location": "path", "name": "ownerId" }, "source": { "kind": "step_output", "step_id": "STEP-B1-001-01", "output_id": "ownerId" } } ],
    "outputs": [],
    "expectations": [ { "text": "Владелец найден, фамилия Davis.\n\nHTTP 200 OK", "assertions": [
      { "operator": "equals", "actual": { "kind": "http_status" }, "expected": { "kind": "literal", "value": 200 } },
      { "operator": "equals", "actual": { "kind": "http_body", "pointer": "/lastName", "semantic_type": { "kind": "json", "type": "string" }, "type_provenance": ["<contract file: schema>"] }, "expected": { "kind": "literal", "value": "Davis" } } ] } ] }
]
```

## Three checks that weaken first

Each expectation is only what the schema operators express (`equals`, `contains` — a string in a string, `greater_*`, `length_equals`, …) over
the step outputs. A weak replacement (`greater_than`, "the number of dates") the reviewer returns as BLOCKING. If a check cannot be expressed so, do not replace
it with a weak one: leave the step `manual_only` with `manual_reason` or a blocker (`automation_blockers`). A manual step: `"manual_only": true, "manual_reason": "<почему>", "operation": null, "inputs": [], "outputs": []`
and `"assertions": []` at the expectations — the schema requires these keys for it too.

1. **"Exactly by N"** — the capability itself computes the difference and returns it as a separate result `delta` (type integer):

```jsonl
{"name": "delta", "semantic_type": {"kind": "json", "type": "integer"}}
{"operator": "equals", "actual": {"kind": "project_result", "name": "delta"}, "expected": {"kind": "literal", "value": 1}}
```

2. **Redirect** — the create step returns `owner_id`, the form submit step returns `redirectId`; the expectation compares them, not only "302":

```jsonl
{"operator": "equals", "actual": {"kind": "project_result", "name": "redirectId"}, "expected": {"kind": "step_output", "step_id": "STEP-<case_id tail>-01", "output_id": "owner_id"}}
```

3. **Field error** — the result `telephoneError` (string) collects the pair "field:code"; the expectation names both parts:

```jsonl
{"operator": "equals", "actual": {"kind": "project_result", "name": "telephoneError"}, "expected": {"kind": "literal", "value": "telephone:typeMismatch"}}
```

The types of both sides of `equals` are compatible (integer with integer, string with string); `contains` — only a string in a string.

## Three refusals that come most often

1. **Batch 2 onward: capabilities of past batches.** A step that calls a capability from batch 1 needs its description in `operation_capabilities` of the same batch: the code takes the result type only from there.
   Without the copy the refusal is a schema error of `semantic_type`. Copy the capability from the input `prior_capabilities` word for word (another byte — `BATCH_CAPABILITY_CONFLICT`): `operation_capabilities` of batch 2 has
   `{"capability_id": "CAP-B1-count", "action": "<operation name>", "adapter": "<adapter.id from read; none there — one name of the project's test runner for the whole run>", "arguments": [], "results": [{"name": "count", "semantic_type": {"kind": "json", "type": "integer"}}], "provenance": ["<source phrase>"]}` — as in batch 1.

2. **A reference to a step output — only to a step above** (`SEMANTIC_FORWARD_STEP_OUTPUT`). An expectation of step 2 may refer (in `expected`) to the output of step 1 — `{"kind": "step_output", "step_id": "STEP-B1-003-01", "output_id": "owner_id"}`; an assertion about the output of its own step is an `actual` of the kind `http_status`, `http_header`, `http_body` or `project_result`, not `step_output`.

3. **A variant group `param_group`** (`SEMANTIC_PARAM_GROUP_MISMATCH`). Cases of one group differ only in literal values, `case_id`, title, objective and step texts; `requirement_ids`, `priority`, `categories`,
   preconditions, `operation`, operators, the number of steps and the references between them are the same. A group is cases with one changing design parameter: in `design_refs` each has its own class of one `parameter`, the other `design_refs` are equal
   (`SEMANTIC_PARAM_GROUP_PARAMETER`). A group is the values of one field; two fields change — not a group.

Input projections: the `read` list names files in `<run>/review-views/` (the inventory — `inventory-view-v1`, the batch plan — `plan-view-v1` without requirement lines: they are in the marking answer, `requirements[]` by `source_requirement_id`). Read them, not `inventories/` and `batch-plans/`.

## An imported case (an `imported` row → a fragment case)

An `imported` row and its case (the row's `SREQ-0003` is mapped to `CREQ-B1-003`):

```json
{"row": 5, "key": "PC-T7", "source_requirement_ids": ["SREQ-0003"], "title": "Поиск", "objective": "Найти владельца", "preconditions": ["Есть Davis"], "priority": "HIGH", "management": {"status": "Approved", "folder": "/Owners", "components": [], "labels": ["smoke"], "owner": null, "estimated_time": null, "issues": [], "pages": [], "custom_fields": {"АС": "Petclinic"}}, "steps": [{"row": 5, "action": "Ввести Davis", "test_data": "Davis", "expectations": ["Найден 1"]}]}
```

```json
{"case_id": "TC-B1-001", "categories": ["functional"], "requirement_ids": ["CREQ-B1-003"], "title": "Поиск", "objective": "Найти владельца", "preconditions": ["Есть Davis"], "priority": "HIGH",
 "management": {"status": "Approved", "folder": "/Owners", "components": [], "labels": ["smoke"], "owner": null, "estimated_time": null, "external_keys": {"zephyr_scale": "PC-T7"}, "external_links": {"issues": [], "pages": []}, "custom_fields": {"АС": "Petclinic"}},
 "steps": [{"action": "Ввести Davis", "test_data": "Davis", "operation": {"kind": "project_action", "capability_id": "CAP-B1-search"},
            "inputs": [{"target": {"location": "arg", "name": "query"}, "source": {"kind": "literal", "value": "Davis"}}], "outputs": [{"source": {"kind": "project_result", "name": "count"}}],
            "expectations": [{"text": "Найден 1", "assertions": [{"operator": "equals", "actual": {"kind": "project_result", "name": "count"}, "expected": {"kind": "literal", "value": 1}}]}]}]}
```

From the row — as is: `title`, `objective`, `preconditions`, `priority`, for a step `action`, `test_data`, the expectation texts; `management` the same fields, `key` → `external_keys.zephyr_scale`
(empty — `{}`), `issues`/`pages` → `external_links`. Yours: `case_id`, `categories`, `requirement_ids`, `operation`, `inputs`, `outputs`, `assertions`.

## How to build a fragment of dozens of cases

1. Make the list of cases (by the batch's requirements) and for each — steps, expectations, checks; the code puts the ids of steps and checks by the scheme above, your `case_id` are unique in the batch.
2. The answer is a data file: build it in parts, case by case; write no scripts that import or call the package. Append to one file, JSON without indentation; do not read the `tools` source for the format — the form above and the schemas are enough.
3. Put the file into the stage's `answer_slot` and check it: `python -m tools.api submit --run-root <run> --stage tc-generator:<batch> --dry-run`;
   a refusal names what to fix. The fixed file is the only repeat of the stage; after `submit` the answer does not change.
   An answer with `quality` is no refusal: ask the generator once to fix the listed cases (the same request of the stage, its answer and `quality`), put the new answer into the slot and check it again; no `quality`, or that one pass made — step 4.
4. Accept the answer with the same call without `--dry-run`; the last batch itself checks the assembly of the whole set, no candidate files are needed.
