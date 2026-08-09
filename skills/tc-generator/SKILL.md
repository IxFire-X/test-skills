---
name: tc-generator
description: Use when converting one context-marker-output v2.1.0 envelope into deterministic, schema-bound manual test cases for an API, operation, resource, or business requirement.
---

# Test-case generator

Produce one portable, deterministic `tc-generator-output.json`. The requirements artifact is the authority for case coverage; source observations only supply explicit technical tokens. Read [the case-generation contract](references/case-generation-contract.md) before task data and use the repository [output schema](../../schemas/tc-generator-output.schema.json) as the machine authority.

## Workflow

1. Read the local contract, then the repository output schema, then accept exactly one `context-marker-output` v2.1.0 envelope as task data. The schema is the machine authority whenever a prose rendering rule is ambiguous.
2. Read `artifacts.analytics_documentation.requirements`; preserve that array exactly, including each `id`, `text`, `provenance`, and order.
3. Use `artifacts.source_code_and_diff` only to resolve an explicit method, path, HTTP status, code, role, resource, field, or bound already supported by a requirement. Do not turn a source observation into a behavior or override a requirement.
4. Extract supported atomic behaviors only. Do not invent roles, policies, fields, requests, response codes, side effects, severities, or oracles.
5. Apply the contract's atom rules, stable ordering, rendering templates, category sets, priority rule, and exact bidirectional coverage mapping. In each `artifacts.generated_test_cases.test_cases[]` item write `requirement_ids` as a nonempty array (even for one ID); only each `artifacts.generated_test_cases.coverage[]` item uses the scalar `requirement_id`.
6. If an atom lacks a concrete required token or oracle, omit it. Preserve the exact input warning array verbatim; never paraphrase or add warnings.
7. Build only the schema envelope: `schema_version` `2.1.0`, `stage` `tc-generator`, `artifacts.generated_test_cases`, and `warnings`.
8. Write exactly one `tc-generator-output.json` to the caller-reserved path. Emit no Markdown, XML, wrapper, batch result, or second output.
9. Validate it with `tools/validate_artifact.py schemas/tc-generator-output.schema.json <output>` and correct only schema or contract violations.

## Boundaries

- Keep every case to one supported atom and one action step; use no generic authentication step, database/log/audit/payment/inventory claim, technical negative, extra field, or unsupported role.
- Use `HIGH` unless the envelope explicitly supports another priority. Do not infer a severity.
- Copy input `warnings` exactly for stable gap disclosure; never add, remove, or paraphrase a warning.
