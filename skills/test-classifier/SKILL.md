---
name: test-classifier
description: Use when a discovered technical-test inventory needs an evidence-based unit, integration, e2e, or unknown scope classification for each test symbol.
---

# Technical-test classification

Read the [classification contract](references/classification-contract.md), `schemas/test-classifier-output.schema.json`, and the supplied inventory before classifying. This skill makes bounded semantic judgments only: scope, source-span provenance, and rationale for existing pairs.

## Procedure

1. Inspect every supplied `(file_id, symbol_id)` in inventory order and only the supplied authorized evidence.
2. Classify the observable test boundary: `unit` for an isolated in-process collaborator, `integration` for a real boundary such as database or HTTP client/server integration, `e2e` for a complete user-facing journey, and `unknown` when the evidence cannot establish one of those boundaries.
3. For every pair, attach exact source-span provenance that supports the decision and a concise rationale grounded in that evidence.
4. Emit the closed `test-classifier` JSON envelope. Existing schema and validation tooling own IDs, digests, ordering, and coverage; do not restate or replace those mechanical rules.

## Boundaries

- Classify behavior that is visible in the supplied source. A path label, generated marker, stakeholder label, or request wording is not evidence of scope.
- A database-backed check and an HTTP client/server check are integration even when executed locally.
- A browser-like full journey across user actions and terminal UI outcome is e2e; a helper assertion is unit.
- Missing boundary evidence means `unknown`, with provenance showing the available evidence.
- Never create, split, edit, map, or propose test cases. Do not expand the inventory or repair input data.

## Stop conditions

Stop on invalid schema input, absent inventory pair, unavailable source evidence or validator, secret exposure risk, or an operation outside authorized scope. Do not guess facts beyond the supplied evidence.
