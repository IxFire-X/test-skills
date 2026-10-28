---
name: tc-reviewer
description: Use when a complete canonical test-case candidate requires one fresh role-isolated review session, a bounded evidence retrieval, an effective selection, or rework.
---

# Canonical test-case review

Read the [review contract](references/review-verdicts.md), `schemas/tc-reviewer-output.schema.json`, `schemas/reviewer-session.schema.json`, and complete authorized review inputs. Execute the canonical/revision validators; read their source only as needed to diagnose a failure. Review the complete candidate and its exact bare digest.

Read the candidate through its compact view `review-views/<sha>.md` (FaT-CV, `python -m tools.review_view`), not the full JSON: source and canonical requirements, every case with its requirements, objective, preconditions, and each step's action, test data, automation blockers, expected text and one line of its checks. The view header gives the exact candidate digest and `reviewed_case_ids` in order. The machine part (step inputs and outputs, operations, capabilities, provenance) is checked by the canonical validator; the view's «Подсказки кода» line `CV_DATA_MISMATCH` names a step whose literal inputs are missing from its action and data — a fact of code, not a verdict: check it and report a finding when you agree.

## Session boundary

There is exactly one fresh, role-isolated reviewer session for the whole canonical branch. The controller first publishes and reads back revision 1 as `UNREVIEWED`, constructs the exact package binding, then owns the separate session ledger: `REVIEW_SESSION_STARTED`, zero or more bounded `EVIDENCE_REQUESTED`/`EVIDENCE_PROVIDED` pairs, one `AUTHORITATIVE_VERDICT`, then `REVIEW_SESSION_COMPLETED`; an explicit pre-verdict abort such as `REVIEW_CONTEXT_LIMIT` is the only terminal zero-verdict path. The model output never contains the session ledger. Do not create per-batch, hierarchical, or second reviewer sessions. Do not use generator dialogue or reasoning.

Request only permitted C-lite evidence from the immutable inventory. Each request and provided evidence binds the immutable candidate/package digest and stays within the controller context budget. Same-model review is allowed only with distinct invocation IDs, fresh context, reviewer role policy, and host isolation evidence; otherwise report `independence_unverified` and do not claim acceptance.

The authoritative verdict selects or rejects canonical content only. It does not set the
attempt's terminal `accepted`, materialize files, execute code, or bypass the later trace
and finalization predicates of `cases-only-v1` or `local-pilot-v1`.

## Procedure

1. Schema- and semantic-validate the candidate. `reviewed_case_ids` equals every case exactly once in physical order.
2. Review every step, data-flow edge, human field, assertion, disposition and requirement relation. Apply the mandatory [human scenario rules](../tc-generator/references/case-generation-contract.md#human-scenario-rules) and the [review contract](references/review-verdicts.md). Findings cite the exact canonical location and violated requirement or rule; stylistic preference alone cannot reject an unambiguous conforming case.
   Expected must describe a system result before execution in every step, including
   the last; a service-only mark such as «Проверено автотестом» is invalid. A short
   native case with a supported action and oracle needs no HTTP block, minimum step
   count or automatic ID transfer. Runtime PASS/FAIL belongs in the execution report.
   Compare original authorized requirements, normalized context and cases in both directions. Retrieve missing original evidence through the bounded context channel; if unavailable, report the gap. Confirm that steps exercise each claimed behavior and assert an observable result, including applicable negative/boundary conditions. An ID link or objective alone is insufficient. Keep a known defect with a clear required oracle testable; only missing oracle/input/access/setup justifies its corresponding gap.
   The assembly receipt `warnings` may carry code hints on the test design (`TD_NEGATIVE_MISSING`, `TD_BOUNDARY_MISSING`, `TD_PAIR_UNCOVERED`, `TD_BOUNDARY_UNDECLARED`, `TD_REF_UNKNOWN`, `TD_MORE`) and on vague expectations by case and step (`TD_VAGUE_EXPECTATION` — a refusal seen only as a flag, `TD_NEGATIVE_NO_STATE_CHECK` — a refused write without a state check, `TD_WEAK_SUCCESS` — a write confirmed only by «no error»). They are facts of code, not a verdict: check each against the requirement and, when you agree, report it as an ordinary finding; a hint you disagree with needs no finding.
3. Emit the `effective` reference: exact candidate for `ПРИНЯТО`, exact successor for `AUTO_FIX_APPLIED`, and `null` for `ТРЕБУЕТ ДОРАБОТКИ`. Return `ПРИНЯТО` only for the unchanged valid candidate.
   `ПРИНЯТО` may carry `WARNING`/`INFO` findings, only for trifles: wording, an imprecision that does not change the check, a question to the analyst; `corrections` stay empty, no successor, a `BLOCKING` finding with `ПРИНЯТО` is a schema error. A semantic error in an expected result, a wrong reading of a requirement, a false PASS (a check that passes on wrong behavior), or a missing state check is never a note: it is `ТРЕБУЕТ ДОРАБОТКИ` with a `BLOCKING` finding, or `AUTO_FIX_APPLIED` when the fix is mechanical. Code shows the notes to the person and never acts on them.
   Return `AUTO_FIX_APPLIED` only with one complete valid successor revision 2, exact candidate digest, preserved source/canonical/case mappings and identity graph, and valid lineage.
   Give the successor as `artifacts.successor_edits`, not as a document: `[{correction_id, case_id, field, step_id?, expectation_id?, index?, old, new}]`, `field` one of `title`, `objective`, `precondition` (`index`), `action`, `test_data` (`step_id`), `expectation` (`step_id`, `expectation_id`); `old` is the exact current text, `new` replaces it as is, `correction_id` names a `MECHANICAL` correction. Code builds revision 2 from the candidate; `python -m tools.review_edits --candidate <r1.json> --edits <file>` checks the edits and prints the `effective` reference — copy it, never compute a digest.
4. A destructive, partial, or semantic-choice correction requires `ТРЕБУЕТ ДОРАБОТКИ`; it never selects a partial patch. Revision 3 and a second authoritative verdict are forbidden.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. Do not select an unvalidated successor or reconstruct revisions in prose.
