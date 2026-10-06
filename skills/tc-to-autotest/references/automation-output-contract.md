# V5 automation-output contract

`schemas/tc-to-autotest-output.schema.json`, `tools.canonical_document`, and `tools.automation_validation` are executable truth. Input is the selected effective canonical JSON and its exact digest, never Markdown or Zephyr CSV.

For `GENERATED`, declare `source`, `automation_revision`, `predecessor_automation_sha256`, `correction_review_sha256`, generated files with exact UTF-8 `content` and matching byte digests, generated symbols with the exact locator variant, implementation relations, manual dispositions, and no diagnostics. `source` must include the selected document ID/revision/digest and the exact attempt-owned `effective_bundle_receipt_digest`; never reuse a digest from another attempt or effective selection. Runtime identity is `(file_id, symbol_id)`. An operation relation identifies case/step/pair; an assertion relation additionally identifies expectation/assertion/pair. Relations are atomic. Required pairs are complete and AND-combined.

The initial complete artifact is revision 1 and has null predecessor fields. One and only one corrected complete artifact may be revision 2: it binds both the revision-1 complete automation digest and the exact static-review digest that returned `AUTO_FIX_APPLIED`. A correction is a complete V5 artifact, never a patch or deletion-only delta. A third automation version is invalid. Runtime `FAIL` never permits regeneration. A generated test that fails the controller's compile/collect gate, or does not compile or import in the run, is `NOT_RUNNABLE/GENERATED_TEST_INVALID`, not `FAIL`: the controller may then request one corrected revision in a child attempt with `retry_reason=GENERATED_TEST_INVALID`, provided revision 2 was not already spent in static review and the parent is not itself such a child attempt.

Generated source preserves every canonical literal and every input/output/assertion binding exactly. A source-proven non-secret helper/default with one безопасное детерминированное значение must already be a canonical literal, never a hidden fixture name. Do not substitute a similarly named literal, inferred route, response binding, or assertion expression.

The generator must not recover request values from human `action`, `test_data`, Markdown, reviewer warnings, old revisions, or project helper defaults. Those fields are not automation input. A request-affecting value absent from selected canonical structured inputs is rework, not permission to infer it.

Keep generated tests readable as ordinary project tests. Use descriptive method and
local variable names, retaining case IDs in comments or display names and exact
locators in the artifact. For Java, factor repeated request, readback or assertion mechanics
into small private helpers in the generated class when that removes actual duplication;
pass each assertion ID through to its failure message. Keep concrete scenario inputs,
operation order and expected values visible at the call site. Preserve every selected
canonical assertion and its atomic relation, including setup checks: excess canonical
coverage must be corrected before selection, never silently dropped by automation.
Do not build a generic operation/assertion interpreter, split classes solely to meet a
line limit, or replace independent persistence reads with managed-object comparisons.
Use existing native assertions without redundant casts or repeated regex compilation;
shared helpers must preserve assertion semantics and identify the failing case/check.

For Spring tests, choose context lifetime from the state actually changed by the
scenarios. Reuse a context within a class when confirmed cleanup restores all relevant
state even after failure and cases cannot interfere concurrently. Do not add
`DirtiesContext(AFTER_EACH_TEST_METHOD)` automatically to database scenarios with
complete cleanup. Keep fresh contexts when bean/cache/static state or the selected
canonical isolation requires them; database cleanup alone does not prove that reset.
Factor identical failure-preserving cleanup blocks into one small helper or an existing
fixture when repeated across cases. Keep each case's baseline and cleanup assertion ID
explicit, execute cleanup after setup or assertion failure, and retain the original
failure if cleanup also fails. Do not share mutable scenario data between tests.

Read the testing-work constraints in the selected canonical `requirements` linked by
the applicable cases' `requirement_ids`, even when absent from human case fields.
Preserve restrictions on the subject under test, framework, dependencies and file edits.
They govern implementation; they are not additional product operations/assertions and
cannot supply missing structured request values, setup or expected results.

Before generation, these existing requirements/context must identify the application,
agreed interface and authorized configuration/authentication sources. Test through that
boundary and assert its observable response or state. For Java/backend prefer the external
API; real-component MockMvc is permitted when the run has not required external API.
Follow existing project clients and fixtures. Independent database readback is allowed
when authorized, but cannot replace an action the case requires through the interface.
Keep the accepted operations, data and Expected unchanged; an application defect remains
FAIL. Neither a long scenario nor a particular assertion/HTTP library is required.

For each manual step declare exactly one manual disposition. A canonical blocker blocks only its own case. When at least one other case can be automated, the artifact is `GENERATED`: it automates the unblocked cases, declares one manual disposition for every step of each blocked case and has nonempty diagnostics naming what was left out. `BLOCKED` is permitted only when no case can be automated and has nonempty diagnostics with empty files, symbols, relations, and dispositions. `tools.automation_validation` reports `AUTOMATION_BLOCKED_HAS_AUTOMATABLE_CASE` for `BLOCKED` with an automatable case, `AUTOMATION_BLOCKED_CASE_DIAGNOSTICS` for `GENERATED` with blocked cases and empty diagnostics, and `AUTOMATION_MISSING_BLOCKED_DISPOSITION` for a blocked-case step without its disposition. Blockers keep `accepted=false`. Do not report a project-discovery or dependency failure as canonical blocking.

Use project-native discovery and declare output only below the selected module's active test root. The automation artifact is a proposed file set; only the controller may materialize it after accepted static review. Do not edit the application, existing tests, configuration, lock files, or dependencies. Keep secrets as runtime handles only. Direct canonical/provider-backed execution runs global provider/adapter preflight through `tools.execution_preflight`. Exact accepted generated-source execution is gated by static review of the complete binding chain and then the project-native test process; it never fabricates runtime provider values.

The V5 automation object is the sole source transport. Each `artifacts.generated_files[]` row contains its project-relative path below the selected module test root, exact UTF-8 `content`, and `content_digest`. The digest must already equal SHA-256 of those UTF-8 bytes. Do not emit a second transport, normalize newlines, or write project files before accepted static review and materialization.
