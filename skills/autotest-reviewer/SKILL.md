---
name: autotest-reviewer
description: Use when selected effective canonical test cases and generated V5 automation require a static, role-isolated completeness and traceability review before execution.
---

# Static automation review

Consume the selected effective canonical document, V5 automation artifact, and declared generated files. Read the [review contract](references/autotest-review-contract.md), `schemas/autotest-reviewer-output.schema.json`, `tools.canonical_document`, and `tools.automation_validation`.

## Procedure

Use the supplied `run_root` and `attempt_id` to read the exact review boundary with
`tools.pilot_state.read_attempt_receipt(run_root, attempt_id, "automation-review-boundary-rN", "ARTIFACT_READ_BACK")`,
where `N` is the automation revision. Its read-only validation follows the current
attempt's effective canonical, receipts and journal bindings. This is required
provenance evidence, not generator conversation; an inline boundary copy alone is
insufficient. Do not inspect generator dialogue, transport logs or unrelated runs.

1. Independently verify source digest and attempt-owned `effective_bundle_receipt_digest`, every declared file's exact UTF-8 `content` and full-byte digest, the deterministic complete `automation_sha256`, the complete physical relation-array digest, project-native setup, and every locator variant. Confirm that each generated framework equals the selected `.skillsrc` module framework and that every Java path is the class FQN under its selected native test root. Call `tools.run_tests.validate_artifact_runner_compatibility(module_root, language, canonical, automation, materialized=False)` on the proposed inline content before accepting; generated files need not exist yet. The controller uses the default on-disk check after materialization and before execution.
2. Verify atomic operation/assertion relation ownership, order, and coverage for every operation and assertion, including canonical manual/blocker branches. Compare every canonical input literal and binding to its actual generated symbol; drift requires rework even when relations and digests otherwise match.
   Follow each claimed relation into the actual symbol: the operation must execute and
   its assertion must inspect the observed result, with the canonical comparator/value.
   Reject tautological assertions, a mocked subject under test, unreachable checks,
   swallowed errors, unintended skip/xfail/disabled markers, and a PASS-only fallback.
   Check deterministic data, fixture cleanup, independence from execution order, bounded
   condition waits, and Linux/Windows path assumptions. This is a source review; do not
   run mutation campaigns or claim that a relation digest proves behavioral coverage.
3. `reviewed_files` equals all declared `(file_id, content_digest)` values in physical order; `reviewed_symbol_pairs` equals all distinct required `(file_id, symbol_id)` pairs exactly once in generated-symbol physical order.
4. One fresh role-isolated reviewer invocation serves each automation version. Before review, the controller must create and read back the exact attempt-owned `automation-review-boundary-r1` or `-r2`; bind its digest as `host_isolation_sha256`. A model statement or caller-supplied receipt is never sufficient. Return a V5 static-review verdict bound to that exact automation digest. `AUTO_FIX_APPLIED` permits only one complete revision-2 regeneration and fresh second review; it never authorizes the old artifact or regeneration after runtime `FAIL`.

This is static review. It does not compile, execute, or claim a runtime result.
An accepted verdict permits the controller to form and materialize the generated delta;
it is not terminal acceptance and cannot replace execution, trace, disposition, or
finalization evidence.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Reject stale source digests, undeclared files, incomplete pairs, and artifacts that require project modification.
