---
name: test-classifier-reviewer
description: Use when a completed technical-test classification needs an independent accepted-or-rework review against its inventory and authorized evidence.
---

# Technical-test classification review

Read the [review contract](references/review-contract.md), `schemas/test-classifier-reviewer-output.schema.json`, `schemas/test-classifier-output.schema.json`, and the supplied inventory/classification artifacts. Review independently; this skill does not classify new rows.

## Procedure

1. Bind the review to the exact `technical_test_inventory_sha256` and `classification_sha256` supplied by the artifacts.
2. Review every `(file_id, symbol_id)` pair exactly once in physical order. Check its scope, source-span provenance, and rationale against the authorized evidence.
3. Emit the schema-defined accepted verdict only when the complete classification is valid. Otherwise emit its rework verdict with concrete `findings`, including the path and affected pair when applicable.
4. Emit the closed reviewer envelope. Schema and validation tooling own digest equality, pair coverage, ordering, and verdict mechanics; do not duplicate them as prose policy.

## Boundaries

- Never auto-fix rows, generate a replacement classification, or silently change a scope, provenance, or rationale.
- Never create cases, split cases, map cases, or alter the technical-test inventory.
- A missing or unsupported fact is a finding, not permission to infer it.

## Stop conditions

Stop on invalid schema input, unavailable validator or authorized evidence, digest mismatch, secret exposure risk, or work outside authorized scope. Return rework only through the schema-defined review artifact; do not repair the classified input.
