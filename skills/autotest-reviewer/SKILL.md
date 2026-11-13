---
name: autotest-reviewer
description: Use when selected effective canonical test cases and generated V5 automation require a static, role-isolated completeness and traceability review before execution.
permissions:
  read: "step inputs from the controller"
  write: "role answer in <run>/tmp/ only"
  network: "none"
  external_delivery: "none"
  dependencies: "none"
  rollback: "delete the run directory"
---

# Static automation review

Consume the selected effective canonical document, V5 automation artifact, and every declared generated file plus its required authorized dependencies. Read the [review contract](references/autotest-review-contract.md) and `schemas/autotest-reviewer-output.schema.json`. The generator's answer already passed the meaning check and the static runner check in its `submit` (`generator_checks` of your `prepare` names them): do not redo them; read their source only to diagnose a failure.

## Procedure

`read` of your `prepare` lists what you read: the view of the effective document, the generator's answer and the receipt of the review boundary `automation-review-boundary-rN`
(`N` is the automation revision; its `reviewer_session_id`, `generator_invocation_id` and `reviewer_invocation_id` go into your answer exactly as written there). This is required
provenance evidence, not generator conversation. Do not inspect generator dialogue, transport logs or unrelated runs; the only command you run is the hint check below.

1. Independently verify source digest and attempt-owned `effective_bundle_receipt_digest`, every declared file's exact UTF-8 `content`, project-native setup, and every locator variant. Confirm that each generated framework equals the selected `.skillsrc` module framework and that every Java path is the class FQN under its selected native test root. The static runner check of the inline content was made in the generator's `submit`; the controller repeats the on-disk check after materialization and before execution.
2. Verify atomic operation/assertion relation ownership, order, and coverage for every operation and assertion, including canonical manual/blocker branches. With `excluded_cases` (FaT-PA) the cases with blockers stay manual: check each listed blocker — one the canonical assertion operators can express (`not_equals` with a `step_output`, `length_equals`, …) is a WARNING finding (ПРИНЯТО with a note): a question for the next run's canonical review, never a rejection — the automation cannot change it (`excluded_cases` must equal the canon's blocked cases), and the canonical reviewer owns blockers. Compare every canonical input literal and binding to its actual generated symbol; drift requires rework even when relations and digests otherwise match.
   Группа `param_group` (Java): один `@ParameterizedTest(name = "[{index}] {0}")`, строки-литералы в том же классе с case ID первым аргументом по display_order группы; `submit` генератора проверил это сам (`RUNNER_PARAM_ROWS`, `RUNNER_PARAMS_UNAVAILABLE`).
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
   Run the weak-assertion check on the artifact under review (its file name is the boundary
   `automation_digest` without `sha256:`):
   `python -m tools.autotest_hints --automation "<run_root>/model-stage-artifacts/<attempt_id>/<digest>.json"`
   It prints JSON hints `AT_ANY_EXCEPTION` (incl. `assertThatThrownBy` without a type), `AT_ALWAYS_TRUE` (MockMvc
   `andExpect` counts as a check), `AT_PARTIAL_MATCH`, `AT_SELF_COMPUTED` (the expected value is the very call the
   test checks, or a variable named `expected…` that holds it: the test asks the code what it must answer) and
   `AT_NO_STATE_CHECK` (the test writes, expects a refusal, and nothing after the refusal reads the state back: a
   count, a find, a repeated GET, `verify(…, never())`) (`AT_MORE`) with
   file ID, line, rule and ASSERT ID. They are facts of code, not a verdict: check each against
   the canonical Expected and report it as an ordinary finding when you agree. Ask of `AT_NO_STATE_CHECK` whether the
   case's Expected says that nothing changed; of `AT_SELF_COMPUTED` whether the expected value is written down in the
   case or taken from the code under test.
3. `reviewed_files` equals all declared `(file_id, content_digest)` values in physical order; `reviewed_symbol_pairs` equals all distinct required `(file_id, symbol_id)` pairs exactly once in generated-symbol physical order.
4. One fresh role-isolated reviewer invocation serves each automation version. `prepare` makes and reads back the attempt-owned `automation-review-boundary-r1` or `-r2` before the review. Do not fill `automation_sha256`, `reviewed_relations_sha256`, `host_isolation_sha256` and `reviewed_files[].content_digest`: the code sets them (a placeholder is refused as `MODEL_RESPONSE_DIGEST_MISMATCH`). A model statement or caller-supplied receipt is never sufficient. Return a V5 static-review verdict bound to that exact automation digest. `AUTO_FIX_APPLIED` and `ТРЕБУЕТ ДОРАБОТКИ` each permit only one complete revision-2 regeneration and fresh second review (the agent may finish instead); neither authorizes the old artifact or regeneration after runtime `FAIL`.

This is static review. It does not compile, execute, or claim a runtime result.
An accepted verdict permits the controller to form and materialize the generated delta;
it is not terminal acceptance and cannot replace execution, trace, disposition, or
finalization evidence.

Record violations in existing findings with code evidence and affected case/relation IDs.
For acceptance keep corrections empty; findings may hold only `WARNING`/`INFO` trifles
(naming, a comment, an imprecision that does not change what a test checks); `BLOCKING`
with `ПРИНЯТО` is a schema error. A weak assertion, a wrong expected value, «any exception»
instead of the specific refusal, or a false PASS (a test that passes on wrong behavior) is
never a note: it is `ТРЕБУЕТ ДОРАБОТКИ` with a `BLOCKING` finding (a compile error too; `corrections` empty), or `AUTO_FIX_APPLIED`
when the fix is mechanical (`corrections` ≥ 1, no `BLOCKING`). The code applies nothing in either case: the generator regenerates once (r2) from your findings or corrections. Code shows the notes to the person and never acts on them.
Retain concise answers to the three
questions in the existing top-level warnings as `REVIEW_EVIDENCE` notes (format below).
These notes document source review, not execution or proof of complete semantic coverage.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Reject stale source digests, undeclared files, incomplete pairs, and artifacts that require project modification.
