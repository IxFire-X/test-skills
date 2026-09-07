# V5 automation-output contract

`schemas/tc-to-autotest-output.schema.json`, `tools.canonical_document`, and `tools.automation_validation` are executable truth. Input is the selected effective canonical JSON and its exact digest, never Markdown or Zephyr CSV.

For `GENERATED`, declare `source`, `automation_revision`, `predecessor_automation_sha256`, `correction_review_sha256`, generated files with exact UTF-8 `content` and matching byte digests, generated symbols with the exact locator variant, implementation relations, manual dispositions, and no diagnostics. `source` must include the selected document ID/revision/digest and the exact attempt-owned `effective_bundle_receipt_digest`; never reuse a digest from another attempt or effective selection. Runtime identity is `(file_id, symbol_id)`. An operation relation identifies case/step/pair; an assertion relation additionally identifies expectation/assertion/pair. Relations are atomic. Required pairs are complete and AND-combined.

The initial complete artifact is revision 1 and has null predecessor fields. One and only one corrected complete artifact may be revision 2: it binds both the revision-1 complete automation digest and the exact static-review digest that returned `AUTO_FIX_APPLIED`. A correction is a complete V5 artifact, never a patch or deletion-only delta. A third automation version is invalid. Runtime `FAIL` never permits regeneration.

Generated source preserves every canonical literal and every input/output/assertion binding exactly. A source-proven non-secret helper/default with one безопасное детерминированное значение must already be a canonical literal, never a hidden fixture name. Do not substitute a similarly named literal, inferred route, response binding, or assertion expression.

The generator must not recover request values from human `action`, `test_data`, Markdown, reviewer warnings, old revisions, or project helper defaults. Those fields are not automation input. A request-affecting value absent from selected canonical structured inputs is rework, not permission to infer it.

Read the testing-work constraints in the selected canonical `requirements` linked by
the applicable cases' `requirement_ids`, even when absent from human case fields.
Preserve restrictions on the subject under test, framework, dependencies and file edits.
They govern implementation; they are not additional product operations/assertions and
cannot supply missing structured request values, setup or expected results.

For each manual step declare exactly one manual disposition. `BLOCKED` is permitted only for a canonical blocker and has nonempty diagnostics with empty files, symbols, relations, and dispositions. Do not report a project-discovery or dependency failure as canonical blocking.

Use project-native discovery and declare output only below the selected module's active test root. The automation artifact is a proposed file set; only the controller may materialize it after accepted static review. Do not edit the application, existing tests, configuration, lock files, or dependencies. Keep secrets as runtime handles only. Direct canonical/provider-backed execution runs global provider/adapter preflight through `tools.execution_preflight`. Exact accepted generated-source execution is gated by static review of the complete binding chain and then the project-native test process; it never fabricates runtime provider values.

The V5 automation object is the sole source transport. Each `artifacts.generated_files[]` row contains its project-relative path below the selected module test root, exact UTF-8 `content`, and `content_digest`. The digest must already equal SHA-256 of those UTF-8 bytes. Do not emit a second transport, normalize newlines, or write project files before accepted static review and materialization.
