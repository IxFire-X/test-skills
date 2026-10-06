---
name: autotest-reviewer
description: Use when selected effective canonical test cases and generated V5 automation require a static, role-isolated completeness and traceability review before execution.
---

# Static automation review

Read the [review contract](references/autotest-review-contract.md), the supplied bounded
part input and `schemas/review-part-output.schema.json`. The controller freezes selected
effective canonical, V5 automation, original sources and authorized dependencies in one
snapshot. Every declared part uses a sequential fresh isolated `autotest-static-reviewer-v2`
invocation. One-part and many-part reviews use the same protocol. Execute relevant
canonical/automation validators; inspect their source only to diagnose a failure.

## Procedure

Use supplied `run_root` and `attempt_id` for read-only durable readers when provenance
needs verification. The logical boundary is `automation-review-boundary-rN`; actual
host/isolation evidence belongs to `review-part-boundary-rN-part-000001` with the real
part ordinal. Do not inspect generator dialogue, transport logs or unrelated runs.
Inside one part envelope each distinct piece of evidence is carried once. The first
occurrence of an input has `content`; an exact repeat in a later scope of the same part
has no `content` and instead carries `content_ref: {scope_id, input}`, naming the scope
and the zero-based input index of that first occurrence. Read the content through the
reference; a `content_ref` entry is complete evidence, not a missing or truncated input.
For each assigned original-source, local or cross-part scope:

1. Independently verify source digest and attempt-owned `effective_bundle_receipt_digest`, every declared file's exact UTF-8 `content` and full-byte digest, the deterministic complete `automation_sha256`, the complete physical relation-array digest, project-native setup, and every locator variant. Confirm that each generated framework equals the selected `.skillsrc` module framework and that every Java path is the class FQN under its selected native test root. Call `tools.run_tests.validate_artifact_runner_compatibility(module_root, language, canonical, automation, materialized=False)` on the proposed inline content before accepting; generated files need not exist yet. The controller uses the default on-disk check after materialization and before execution.
2. Verify atomic operation/assertion relation ownership, order, and coverage for every operation and assertion, including canonical manual/blocker branches. A canonical blocker blocks only its own case: a `GENERATED` artifact must still automate every unblocked case, carry one manual disposition for every step of each blocked case, and have nonempty `diagnostics`; `BLOCKED` is valid only when no case can be automated. Compare every canonical input literal and binding to its actual generated symbol; drift requires rework even when relations and digests otherwise match.
   Follow each claimed relation into the actual symbol: the operation must execute and
   its assertion must inspect the observed result, with the canonical comparator/value.
   Reject tautological assertions, a mocked subject under test, unreachable checks,
   swallowed errors, unintended skip/xfail/disabled markers, and a PASS-only fallback.
   Check deterministic data, fixture cleanup, independence from execution order, bounded
   condition waits, and Linux/Windows path assumptions. This is a source review; do not
   run mutation campaigns or claim that a relation digest proves behavioral coverage.
   For every case, answer with concrete file and class/method evidence:
   (a) what application boundary is declared, and does test → helper → boundary reach it;
   (b) is the target behavior executed by real components rather than a stub or mock;
   (c) does the assertion observe the response/state required by Expected?
   An isolated class/method check, subject substitution or weakened Expected requires
   rework. External API is preferred; MockMvc with real application components is allowed
   unless this run requires external API. A single-request functional case is valid.
   HTTP imports, JUnit annotations, absence of Mockito and correct digests cannot answer
   these questions. Project imports may be the accepted client/configuration.
3. Return exactly `coverage`, `findings`, `corrections` and `required_checks`. Coverage
   lists every assigned scope once in physical order, with status, exact code/source
   evidence and substantive assessment. Request additional bounded cross checks for
   unresolved relationships. A finding does not stop later trustworthy parts. Missing
   or untrusted evidence stays unchecked; shared snapshot corruption prevents trusted
   continuation. Do not silently omit an oversized file or symbol.
4. The controller validates saved parts and uses `finish-review` to publish one v6
   aggregate/output for the complete automation revision. It binds plan and aggregate
   digests, retains the logical `reviewer_session_id`, and derives complete reviewed
   files/pairs/relation coverage. The aggregate is not a model response; invocation IDs
   and isolation stay on real part boundaries. Incomplete coverage cannot be accepted.
   `AUTO_FIX_APPLIED` permits only one complete r2 regeneration and full bounded review
   of that new snapshot. Parts do not spend correction budgets. Never regenerate after
   runtime `FAIL`.

This is static review. It does not compile, execute, or claim a runtime result. The
controller runs its own compile/collect gate after materialization; a generated test
that fails it ends as `NOT_RUNNABLE/GENERATED_TEST_INVALID`.
An accepted verdict permits the controller to form and materialize the generated delta;
it is not terminal acceptance and cannot replace execution, trace, disposition, or
finalization evidence.

Record violations with affected case/relation IDs and concrete source/code evidence.
Retain positive boundary/call-path/oracle evidence in each scope's coverage assessment;
format examples are in the review contract. A summary does not replace original code.

## Stop conditions

Report invalid input, unavailable evidence/tool, required invention or work outside authorized scope as a blocker. Substantive defects require rework and continuation through the remaining trustworthy parts. Reject stale source digests, undeclared files, incomplete pairs and artifacts that require project modification.
