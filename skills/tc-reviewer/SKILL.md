---
name: tc-reviewer
description: Use when a complete canonical test-case candidate requires one fresh role-isolated review session, a bounded evidence retrieval, an effective selection, or rework.
---

# Canonical test-case review

Read the [review contract](references/review-verdicts.md), `schemas/tc-reviewer-output.schema.json`, `schemas/reviewer-session.schema.json`, and complete authorized review inputs. Execute the canonical/revision validators; read their source only as needed to diagnose a failure. Review the complete candidate and its exact bare digest.

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
3. Emit the `effective` reference: exact candidate for `ПРИНЯТО`, exact successor for `AUTO_FIX_APPLIED`, and `null` for `ТРЕБУЕТ ДОРАБОТКИ`. Return `ПРИНЯТО` only for the unchanged valid candidate. Return `AUTO_FIX_APPLIED` only with one complete valid successor revision 2, exact candidate digest, preserved source/canonical/case mappings and identity graph, and valid lineage.
4. A destructive, partial, or semantic-choice correction requires `ТРЕБУЕТ ДОРАБОТКИ`; it never selects a partial patch. Revision 3 and a second authoritative verdict are forbidden.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. Do not select an unvalidated successor or reconstruct revisions in prose.
