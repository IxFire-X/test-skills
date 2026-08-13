---
name: tc-reviewer
description: Use when a complete V3 canonical test-case candidate and digest require an independent selection, safe successor review, or rework verdict.
---

# Canonical test-case review

Read the [review contract](references/review-verdicts.md), `schemas/tc-reviewer-output.schema.json`, `tools.canonical_document`, and `tools.revision_selection`. Review the complete candidate and its exact bare digest.

## Procedure

1. Schema- and semantic-validate the candidate. `reviewed_case_ids` equals every case exactly once in physical order.
2. Review every step, data-flow edge, human expectation, assertion, blocker, manual state, and requirement relation. Findings cite a concrete canonical location.
3. Return `ПРИНЯТО` only for the unchanged valid candidate. Return `AUTO_FIX_APPLIED` only with one complete valid successor, exact candidate digest, preserved identity graph, and valid lineage.
4. A destructive, partial, or semantic-choice correction requires `ТРЕБУЕТ ДОРАБОТКИ`; it never selects a partial patch.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. Do not select an unvalidated successor or reconstruct revisions in prose.
