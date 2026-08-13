---
name: autotest-reviewer
description: Use when selected effective V3 canonical test cases and generated automation require a static, independent completeness and traceability review before execution.
---

# Static automation review

Consume the selected effective canonical document, V3 automation artifact, and declared generated files. Read the [review contract](references/autotest-review-contract.md), `schemas/autotest-reviewer-output.schema.json`, `tools.canonical_document`, and `tools.automation_validation`.

## Procedure

1. Independently verify source digest, every declared file's full-byte digest, project-native setup, and every locator variant.
2. Verify atomic operation/assertion relation ownership, order, and coverage for every operation and assertion, including canonical manual/blocker branches.
3. `reviewed_symbol_pairs` equals all distinct required `(file_id, symbol_id)` pairs exactly once in generated-symbol physical order.
4. Return a V3 static-review verdict. `AUTO_FIX_APPLIED` does not authorize running the old automation artifact: return to regeneration and review.

This is static review. It does not compile, execute, or claim a runtime result.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Reject stale source digests, undeclared files, incomplete pairs, and artifacts that require project modification.
