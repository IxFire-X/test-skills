# Batch-fragment generation contract (candidate fragment 1.0.0)

The generator emits one schema-valid immutable candidate fragment for its controller-owned batch, never the assembled document. The controller deterministically assembles fragments into the bare canonical 1.0.0/1.1.0 document validated by `tools.canonical_document`.

The fragment preserves `SREQ-*` source requirements with exact provenance/digests,
`CREQ-*` canonical requirements within the controller-owned batch namespace, and
explicit many-to-many `source_requirement_id -> canonical_requirement_ids -> case_id`
relations. It may not cite a source or canonical requirement owned by another batch,
discard a requirement as `out_of_scope`, or impose a test-count ceiling. Manual-only
steps and automation blockers are the only canonical dispositions for unavailable work.

## Derive coverage conditions before cases

Build the fragment's `requirements` and `source_to_canonical_mappings` before writing
its `test_cases`. This is the pipeline's own coverage-condition inventory; do not
expect the caller, another CLI, or a separate auditor to supply it. Reuse these
existing canonical fields, not a parallel checklist or a new artifact format.

For product behavior, each `CREQ-*` states one independently checkable condition:
the relevant starting state or input partition, the action/trigger, and its observable
outcome. Split distinct branches, boundary sides, field-specific rules and state
transitions even when the source puts them in one paragraph. For example, "name is
required, accepts 30 characters, rejects 31" needs separate blank-name, length-30
and length-31 conditions, each with its required outcome. If empty and whitespace-only
inputs are both specified, retain both explicitly. Several jointly required results
of one action, such as a field error AND unchanged storage, may remain one condition;
do not fragment every response field or manufacture a Cartesian test matrix.

Preserve the original source text byte-for-byte in `source_requirements`; the union
of its mapped canonical conditions must preserve its meaning and every explicit
variant. Canonical conditions need not repeat the full source paragraph. Give each
condition an ID in the batch's namespace and provenance to its source(s). Keep shared
data, prerequisites and testing-work constraints as linked canonical requirements
without turning them into behavioral test quotas or meta-cases. Reconcile the whole
source against this inventory before proceeding, including unlabelled requirements.

Link each case only to the conditions and shared requirements it actually addresses.
Check each behavioral condition against concrete step inputs, actions and assertions,
or its precise manual/blocker gap. One case may cover several conditions and one
condition may need several cases. Mere links, copied condition text and matching
counts do not establish coverage. Missing expected behavior requires an explicit gap
and clarification, not an invented oracle or silent omission.

## Human and machine ownership

For each case retain a human Title, Goal, and Preconditions. Its arbitrary sequential steps are in physical order and may be any necessary length. Each step has `action` (the human instruction), `test_data` (the concrete parameters/request used by the person executing the step) and `expectations[].text` (the human-observable result), and separately owns its operation, typed inputs, outputs, and assertions. An input may reference a previous-step output only. Every assertion is covered by the step's Expected Result. A manual-only step has its `manual_reason`; an automation-blocked step has exact canonical blockers. Preconditions describe the required starting state. Setup the case does not check goes to Preconditions, and its calls run in the capability composition of the first step (exact calls/order, inputs, outputs and provenance), never an undocumented fixture side channel; for `http`, one step is one request, so setup stays ordered steps. Preconditions alone cannot supply executable setup or a missing binding.

Give each case one coherent testing objective, not necessarily one assertion. Keep data separate from actions and
observed outcomes; do not repeat the entire expected result or execution policy in the
objective. A step represents one meaningful action or cohesive preparation, not every
setter or click. Do not add assertions merely to recheck literal
assignments made by test setup; keep checks needed to establish an observable prerequisite
or required behavior. Show required final state and cleanup when the scenario changes
shared state; do not invent cleanup for stateless calls or local disposable objects.

Choose setup for the particular scenario before choosing reusable capabilities.
Do not make every caller compute dates, load catalogs or create related records just
because a shared helper accepts them. A date boundary needs the server date, its origin,
the actual submitted dates and a reproducibility check. A date used only to create an
existing visit belongs to that cohesive preparation with its exact derivation recorded;
an owner-only validation without date-dependent setup needs no calendar operation.
Expose a preparation output as a typed output when a later step consumes it. Do not turn each internal call or conversion into
a separate human action.

Expected results name the affected
objects and the required change or preservation. Several machine checks may support
one precise sentence; they do not require a prose transcription of every comparison.
For an unchanged-state check, identify its actual scope (for example, the owner's
previous contacts, pets and visits). If the assertion compares a broader snapshot,
describe that scope honestly too. Do not remove required preservation assertions for
brevity or add all-table invariants automatically to unrelated scenarios.

Prefer one short objective sentence and a result stated as an observable fact. For
example, an empty-name case can aim to "Убедиться, что владелец без имени не создаётся";
its result can say "Форма сообщает об обязательном имени; новый владелец не сохранён".
Use only outcomes supported at the selected test boundary. Identify a broader snapshot's contents once in the
step that captures it, then refer to that named snapshot rather than repeating its
table list. Keep source call chains in provenance; a characterization expectation
still identifies the current component boundary and the exact observed failure.
For catalog prerequisites, check the entries needed by the scenario. Require the
entire catalog or its order only when that is part of the required behavior; source
seed contents alone do not make catalog equality a requirement.

The HTML preview and the default v5 Zephyr CSV display only `action`, `test_data`, and `expectations[].text`; structured literal path/query inputs resolve the displayed HTTP URL, a non-literal one shows a readable placeholder.

## Canonical physical order

Canonical order recipe: operation_capabilities by capability_id; arguments and results by name; both use ascending Unicode code-point order. Order categories by the precedence enforced by the current canonical validator. For source requirements, canonical requirements, test cases, steps, inputs, outputs, expectations, and assertions, display_order matches physical array order.

Never invent capability arguments, values, operations, assertions, roles, or environment facts. Validate before publication; a projection cannot repair invalid semantics.

An `operation_capability` describes the exact operation a step performs. It need not name an existing
application helper or a preinstalled runtime provider. A capability may compose
confirmed application and existing test-framework APIs for cohesive preparation,
execution and observation. Record the exact calls, order, state/transaction boundary,
argument meaning and result extraction in its provenance; declare all typed arguments,
results and step bindings. A composition label is a test contract, not a claim that
an application method with that name exists. Reuse a capability across cases when its
contract is the same; do not invent a new adapter or dependency to supply it.

Verify the required APIs and setup from authorized project code/tests or available
dependency documentation/metadata the human gave; search for no more. Keep dependency evidence in the authorized technical context and reference it
from capability provenance; do not copy API signatures, framework annotations or
isolation configuration into human Action/Test Data/Expected Result fields.
Absence of a ready-made fixture, helper or capability declaration alone is
not an automation blocker. An unavailable API, inaccessible dependency evidence,
unresolved input/oracle, or forbidden setup still is a concrete gap. Do not assert
that an environment has already started or return an expected constant as an observed
result. Persistence claims need real storage and the declared independent readback;
a mock or rereading the same managed object is not an equivalent composition.

A capability declaration alone never proves that an implementation exists or passes.

## Human scenario rules

Write every ordinary human field in clear Russian, including requirement text, objective, preconditions and manual/blocker reasons. Preserve exact technical tokens. Reject wording when it changes meaning, makes an action ambiguous or violates the mandatory format; stylistic preference alone is not a blocking finding.

### Case form

Not for imported cases (`imported`): their human fields stay as given.

1. **Title** — action + condition; the outcome goes last, only when it is not the ordinary success: «Регистрация без username: отказ». Never start with «Проверка», «Отказ», «Отклонение». Up to 80 characters; data values go to the steps, except a value that is itself the condition («длина city 80»). «и» / «или» in a title: split the case or make a `param_group`; an end-to-end scenario is the exception. Titles in a set are unique. Do not repeat an environment without a cross-environment distinction.
2. **Objective** — «Убедиться, что …»: the requirement or risk the case covers, not a retelling of the title.
3. **Preconditions** — the system state with data and how to reach it; setup the case does not check goes here, not into a step (for `http` a step is a request, so setup stays steps; the machine side is above). No implementation facts (source path, setting name) and no oracle caveats; a case does not depend on the order of other cases.
4. **Step** — one action, one observable result. Data as «field: value», from the document or the starting data; a variable («позиция A») only when the value is unknown in advance. Bulk data: one step with a template where the operation is composite (for `http` a request is a step). No data: `test_data` is `—` (an `http` step without a body: «Тело запроса отсутствует.»), never empty and never filler.
5. **Expected result** names what is seen: a code, a field, on-screen text — not «Статья создана». Above JSON: «Ожидаемое тело» (exact) or «Пример тела» (sample); JSON holds exact values (for `exists`: `<значение присутствующего поля>`), a condition («непустой массив») goes in words above it. No instructions to the tester, no remarks on the document.
6. **Cleanup** — the last step, its action starts with «Уборка:»; its expectation only says it was done (an HTTP step with a status keeps the status line).
7. **Outcome undefined by the document:** a `manual_only` step, `manual_reason` is the question to the analyst, priority `LOW`, label `наблюдение` in `management.labels`, objective «Зафиксировать …». Never «ожидается по реализации»: a case fixes what the program must do, not what it does.
8. **Priority:** `HIGH` — main path and prohibitions from the requirements; `MEDIUM` — variants and boundaries; `LOW` — observations. **Folder** (`management.folder`) — a requirements section, not one per set. Cases that differ in one data value are one `param_group` (rule in `SKILL.md`).

Cases also document behavior for readers unfamiliar with the project. Explain the
domain object and unfamiliar terms where needed; understanding the scenario must not
require reading implementation code. Executing a component case may require technical
skills. Keep Action concise: describe the meaningful operation, put concrete values in
Test Data, and retain preparation details in the structured model. Use an unambiguous
public API name when it identifies the tested interface; avoid repeating full signatures,
setter sequences or framework plumbing in human fields. Do not hide required setup or
observable checks. Use natural Russian around types: «значение типа bytes» or
«последовательность байтов», not «возвращается bytes».

Choose the operation kind for the declared test execution boundary. Use
`operation.kind=http` for direct HTTP execution only when the canonical HTTP contract
can represent the exact method, path, base URL source, request and observations.
The presence of an HTTP route does not force that kind for a project action:
an evidence-backed `project_action` may use the existing framework to submit a form,
inspect its real MVC response/model/errors and reread storage. Preserve the real
method/path and all form values in its structured arguments; do not encode form data
as a fictitious JSON request or invent a base-URL environment variable. Unsupported
direct HTTP binding remains a gap when no supported, authorized native composition
implements the required behavior. Evidence-backed methods and paths belong in Action
when needed to identify the request. Preserve technical tokens exactly. Name the
actual API/function for a direct library/component scenario; keep test-only helper
labels and framework plumbing out of human Action.

For `project_action`, Test Data describes application inputs and the records to observe,
not the complete helper argument map. Form fields, path/query values and concrete setup
records remain exact; internal read selectors, sentinel values and comparison controls
stay in structured inputs/provenance. For an initial snapshot, name the starting
state from Preconditions (for example, “owners already present in the initial clinic
database”), not “saved before this step”. For a later snapshot, name the concrete
preceding action whose result is observed. State what is captured (records, IDs or
count) and preserve the scope used by later comparisons. Do not expose a helper's
`entity="none"` or imply that a server-date control is an HTTP parameter. Identify a previous-step ID as “ID of the owner created in step N”
and bind that exact output structurally. Human wording is a view of those bindings,
never a source from which automation reconstructs missing values.

For an HTTP step, `test_data` contains only a full pretty-printed JSON request body. Literal-only JSON is copyable as-is. A dynamic value uses a readable previous-step placeholder at the exact substitution point. A no-body request says exactly `Тело запроса отсутствует.`; a manual or undefined body says `—`. Canonical Action retains the exact supported method/path signature; the HTML projection resolves literal path/query inputs in its displayed URL. Safe header prerequisites and positive authorization belong in Preconditions; authorization stays in Action only when its absence is the scenario. Never render raw `body:/...`, `env:...`, `secret:...`, or binding listings. A project-native call of an HTTP endpoint follows the same human rule: Action names the method and path, `test_data` is as for `http`.

The Expected Result first states the observable system result, then, for HTTP, a blank line, exact `HTTP <status> <reason>`, and only confirmed response fields as pretty-printed JSON. Omit an unconfirmed body rather than inventing it. Do not print `Выход:`, `Проверка:`, `http_body:/...`, assertion expressions, «успешно», `ok`, or a bare HTTP code. Do not fork `200 или 201`. Data is deterministic: no `random` / `faker` / «любое значение». A source-proven non-secret helper/default with one safe deterministic value is a canonical literal, visible in the URL or body. Use a fixture only when authorized sources do not safely determine that value. Secret values and handles never render; only `safe_label` may appear, and it is not a resolved value.

Expected describes a criterion before execution. «Проверено автотестом», «проверка
пройдена» and other execution-only marks cannot replace the system result, including
in the last step. Mentioning an autotest inside a substantive observable result is not
itself a violation. Actual PASS/FAIL remains in the execution report.
A valid single-action case needs no extra CRUD cycle or ID transfer. Review its oracle
and requirement coverage rather than its length.

For example, with a confirmed GET returning the product list, Expected is:

```text
Приложение возвращает список доступных товаров.

HTTP 200 OK
```

Use the status and response fields established by the authorized requirement and
machine contract. Missing facts remain explicit gaps; this example does not supply them.
HTTP body/status rules are specific to `operation.kind=http`; native operations retain
their supported human data and observable oracle without an invented HTTP block.

Canonical requirements are a semantics-preserving Russian decomposition of their
context requirements into the coverage conditions described above. Do not drop
access-control, isolation, boundary, or other behavior classes while decomposing.
Every relevant warning about product behavior must be addressed by a testable
canonical step or an explicit manual/blocker gap. A known implementation defect with
a clear oracle remains testable; only an actual missing oracle, input, access or
setup justifies the corresponding gap. Prose in generator/reviewer warnings is not coverage.

Distinguish product behavior, scenario prerequisites, and instructions governing this
testing work by meaning and addressee, not by keywords. Preserve the complete source
texts, all canonical conditions, provenance and mappings, including mixed paragraphs.
Instructions such as “do not mock the subject”, “use JUnit 5”, or “do not edit existing
files while generating tests” stay in `requirements`, linked to applicable cases through
`requirement_ids`; automation generation/review and controller checks enforce them.
Do not repeat them in titles, objectives, preconditions, actions, test data or expected
results, create separate meta-cases, or invent product APIs to verify the testing agent.
Required starting state, access and inputs remain visible and structurally represented.
Actual product requirements about permissions, integrity, isolation or other nonfunctional
behavior still need observable checks or justified gaps. For example, “the export must
not modify its input file” requires a before/after content check. A requirement link establishes
applicability, not proof of compliance. Never use this distinction to hide missing
behavior, unsupported execution or an unresolved oracle.

An explicit requirement is the expected-result authority when code or runtime differs.
Distinguish that behavior from a source-derived observation mechanism. A concrete Java
exception class or message is a required oracle only when the requirement or selected
API contract specifies it, or when explicitly scoped characterization is intended.
For a behavioral “owner not found” requirement, source evidence may explain how this
component currently exposes that failure; it does not silently make that class/message
a permanent product requirement. Record the source, test boundary and justification
in existing requirement/capability provenance, and make any characterization scope
visible in the expectation. Prefer the required observable failure and absence of
foreign data/changes when faithfully observable at that boundary. Do not replace it
with “any exception”, a fabricated status, or an unsupported error classifier. Preserve
explicit field error codes exactly. An unresolved observation mechanism remains a gap.
For a requirement-silent characterization, trace the reachable call path: a later not-found branch is not evidence when a lower layer throws first. Correct a mistaken inference only through a reviewed canonical successor, never by weakening an assertion for PASS. Such a disagreement can be the defect the test must detect; it does not authorize a
weaker expectation. Preserve unresolved or manual work as
visible gaps, and request clarification when authoritative requirements conflict or
leave the oracle unresolved. A requirement/runtime disagreement alone is not such a conflict.

The request shown to a person is derived from structured inputs, never a second semantic source. Every request-affecting path/query/header/body value and source-proven default has one exact structured input target/source. Every JSON leaf is owned by that model. For a negative branch, retain all source-proven prerequisites needed to reach the intended branch and vary only its invalid value. Automation must not recover a missing literal or binding from `test_data`.

Use human placeholders for previous-step values without exposing internal step/output IDs. An `exists` assertion renders `<значение присутствующего поля>`, not a sample or a claim of nonempty data: presence alone permits null and empty values. Non-null, nonempty and exact-value requirements need separate supported assertions.

Remember an id from step N and use it later (`step_output`). After create or change, read the result back. A gap is `manual_only` or a canonical blocker, not an invented URL.

Do not invent a catalog of one-shot handle checks or list every route.

## What the check of a fragment refuses most often

The check of a fragment returns the whole list of refusals, each with a `fix`: correct all of them in one pass, yourself, and write no scripts that import or call the package. Write no `digest` or `*_digest` field and do not order `requirement_ids` or `categories` — the code does.

- `SEMANTIC_HUMAN_BODY_UNBOUND`: every leaf of the request JSON in `test_data` has its own input with `target.location = body` and its RFC 6901 pointer (`{"a": {"b": 1}}` → `/a/b`).
- `SEMANTIC_BODY_TARGET_OVERLAP`: body pointers are never equal or nested; `semantic_type` sits beside `target` and `source`, not inside: `{"target": {"location": "body", "pointer": "/a/b", "sensitive": false}, "source": {…}, "semantic_type": {…}}`.
- Blockers (`SEMANTIC_BLOCKER_PATH`, `SEMANTIC_MISSING_ASSERTION_BLOCKER`, `SEMANTIC_SPURIOUS_BLOCKER`) mark only an absent or unconfirmed element: `UNRESOLVED_OPERATION`/`UNSUPPORTED_AUTOMATION_CAPABILITY` → `/operation`; `UNRESOLVED_INPUT_BINDING` → `/inputs`; `UNRESOLVED_OUTPUT_BINDING` → `/outputs`; `UNRESOLVED_ASSERTION`/`UNSUPPORTED_ASSERTION_DIALECT`/`UNCONFIRMED_TYPE` → `/expectations/<n>/assertions`. An expectation of a blocked step without assertions has its own blocker; a fully bound step has none.

## Requirement to case to assertion examples

These illustrate reasoning about coverage; they do not add requirements or replace the canonical schema.

- Requirement: `GET /items/42` returns the item with ID `42`; authorized evidence exposes `id`. Case: request that exact ID with the required setup. Assertion: the observed status equals the required status **and** the observed `id` equals `42`. A `200` check alone would miss the wrong item.
- Requirement: an absent item returns `404`; current code instead raises an exception mapped to `500`. Case: request a proven absent ID after satisfying unrelated prerequisites. Assertion: observed status equals `404`. The resulting runtime FAIL is useful defect evidence; neither `500` nor a blocker is a valid substitute.
- Requirement: an unknown role is “handled correctly”, with no authoritative expected outcome. Preserve the requirement and report the unresolved oracle as a concrete gap. Ask for the intended outcome; do not invent `403`, a success result or a tautological assertion.
