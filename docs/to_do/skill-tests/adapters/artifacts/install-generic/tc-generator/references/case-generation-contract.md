# Case-generation contract

Use this deterministic authoring contract with [the output schema](../../../schemas/tc-generator-output.schema.json). It is portable: extract domain words from the supplied envelope; do not treat the worked order example below as fixed vocabulary.

## Input and authority

- Accept exactly one `context-marker-output` v2.1.0 envelope.
- Copy `artifacts.analytics_documentation.requirements` byte-for-value into `artifacts.generated_test_cases.requirements`: preserve every object and its array order, including `id`, `text`, and `provenance`.
- Derive case atoms from requirement text only. Use `artifacts.source_code_and_diff.sources` solely for technical precision when it explicitly confirms a method, path, status, code, role, resource, field, or bound named by a requirement.
- Never infer an absent token. When a required concrete token or oracle is absent, omit that atom. Copy input `warnings` exactly for stable gap disclosure; do not write, rewrite, remove, or augment warnings.

## Deterministic atom recipe

Extract only explicit, independently testable atoms. Assign one case to each atom; never combine atoms.

1. An explicit successful operation produces one happy case.
2. An inclusive numeric range with explicit lower and upper bounds plus explicit failures outside the range produces four cases, in order: lower accepted, upper accepted, immediately-below rejected, immediately-above rejected. Use `lower - 1` and `upper + 1` only when those outside values and their failure oracle are explicit or mechanically entailed by the stated integer inclusive range and explicit outside failure rule.
3. An explicit role denial produces one authorization-denial case.

Order all cases as happy path, positive boundaries, negative boundaries, then authorization. Number them `TC-0001`, `TC-0002`, and so on with four decimal digits. Each case has exactly one requirement ID and each coverage entry is the exact reverse mapping: list every copied requirement once, in input order, with precisely the IDs of its derived cases; no unknown, duplicate, omitted, or extra links.

## Machine field map

The output schema is authoritative. Use these exact field shapes:

| Location | Field | Shape | Meaning |
|---|---|---|---|
| `artifacts.generated_test_cases.test_cases[]` | `requirement_ids` | nonempty array of requirement-ID strings | Requirement(s) that derive this case; the single-ID rule is rendered as an array with one element. |
| `artifacts.generated_test_cases.coverage[]` | `requirement_id` | scalar requirement-ID string | One copied requirement whose reverse mapping is in `test_case_ids`. |

Do not write `test_cases[].requirement_id`; it is not a schema field. This map is universal; the order-creation fixture below only illustrates the contract.

Use exactly one action step, `order: 1`. Select `priority: "HIGH"` unless an explicit requirement provides a different supported priority. Use only these minimal category arrays:

| Atom | Categories |
|---|---|
| successful operation | `positive`, `functional` |
| accepted boundary | `positive`, `boundary` |
| rejected boundary | `negative`, `boundary` |
| role denial | `negative`, `authorization` |

## Rendering recipe

Substitute only extracted tokens. Let `METHOD path` be the explicit operation; `status CODE` its supported success or failure oracle; `allowed_role`, `denied_role`, `resource`, `field`, `lower`, and `upper` be extracted tokens. A representative happy numeric value is `lower + 1` only when it is safe and supported.

| Atom | Title | Preconditions | Test data | Step expected result | Expected outcome |
|---|---|---|---|---|---|
| happy | `<allowed_role> creates a valid <resource>` | `Authenticated as <allowed_role>.` | `<field>: <lower+1>` when applicable | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |
| lower | `<field> <lower> is accepted` | `Authenticated as <allowed_role>.` | `<field>: <lower>` | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |
| upper | `<field> <upper> is accepted` | `Authenticated as <allowed_role>.` | `<field>: <upper>` | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |
| below | `<field> <below> is rejected` | `Authenticated as <allowed_role>.` | `<field>: <below>` | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |
| above | `<field> <above> is rejected` | `Authenticated as <allowed_role>.` | `<field>: <above>` | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |
| denial | `<denied_role> cannot create a <resource>` | `Authenticated as <denied_role>.` | a supported representative request value, such as `<field>: <lower+1>` | `HTTP <status> <CODE>` | `HTTP <status> with code <CODE>.` |

Set `steps[0].action` to exact `<METHOD> <path>`. Do not add login/authentication actions, setup behavior, additional request fields, or outcomes concerning storage, logs, audits, payment, inventory, or unrelated technical failures.

## Normative conformance fixture: order creation

This is a derived fixture, not required domain terminology. From requirements that explicitly state a `sales_manager` order creation success (`201 CREATED`), integer `quantity` range `1..100` with `0` and `101` rejected (`422 QUANTITY_OUT_OF_RANGE`), and viewer denial (`403 FORBIDDEN`), plus `POST /api/v1/orders`, emit in this exact order:

1. `TC-0001`: `sales_manager creates a valid order`; `quantity: 2`; `HTTP 201 CREATED`; `HTTP 201 with code CREATED.`
2. `TC-0002`: `quantity 1 is accepted`; `quantity: 1`; `HTTP 201 CREATED`; `HTTP 201 with code CREATED.`
3. `TC-0003`: `quantity 100 is accepted`; `quantity: 100`; `HTTP 201 CREATED`; `HTTP 201 with code CREATED.`
4. `TC-0004`: `quantity 0 is rejected`; `quantity: 0`; `HTTP 422 QUANTITY_OUT_OF_RANGE`; `HTTP 422 with code QUANTITY_OUT_OF_RANGE.`
5. `TC-0005`: `quantity 101 is rejected`; `quantity: 101`; `HTTP 422 QUANTITY_OUT_OF_RANGE`; `HTTP 422 with code QUANTITY_OUT_OF_RANGE.`
6. `TC-0006`: `viewer cannot create an order`; `quantity: 2`; `HTTP 403 FORBIDDEN`; `HTTP 403 with code FORBIDDEN.`

All six use one step, `POST /api/v1/orders`; cases 1-5 precondition `Authenticated as sales_manager.` and case 6 `Authenticated as viewer.` Preserve an input warning that says no authorization policy is supplied for `warehouse_operator`; it creates no case.

## CSV companion

After the canonical JSON passes schema and semantic validation, invoke `scripts/export_test_cases_csv.py` and place the `.csv` sibling beside it. JSON stays authoritative; CSV exists for human review and later Jira Zephyr field mapping.

Use the exact ordered columns `test_case_id`, `requirement_links`, `title`, `priority`, `categories`, `preconditions`, `test_data`, `ordered_steps`, and `expected_outcome`. Structured list/object fields are canonical compact JSON inside CSV cells so commas, Unicode, empty arrays and the full ordered step objects remain lossless.

The exporter must read the emitted CSV back and prove JSON↔CSV equivalence across row count, test-case order and every field value. A mismatch is a failed stage. Never edit CSV independently or treat it as input to downstream pipeline stages.
