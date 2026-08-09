# Tc-generator 36-case scale acceptance

Status: **stopped at the first semantic failure; no retry performed**.

The fresh Terra evaluator wrote one output. JSON Schema validation passed. The scale checker observed exactly 18 requirements and exactly 36 test cases, but returned exit code 1 because generated `title` and `preconditions` wording did not exactly equal the hidden expected-matrix wording. No reported error concerned case count, requirement copying, ordering, coverage, categories, HTTP status/code or the `claims_adjuster` warning.

- Started: `2026-08-09T20:33:20.130Z`
- Finished: `2026-08-09T20:38:43.556Z`
- Evaluator: `sol_advisor_terra_implementer` / `native-subagent-v2` / `gpt-5.6-terra/high (role-pinned)`
- Prompt SHA-256: `71b75a40b8088d38efa8f0e44b38dea01a3d7cce9b9dddf3690712f3282fb715`
- Output SHA-256: `fb1fe532209290df0be8a35ee885745f197dc3a0f315526d43bcaadaa0f6c02e`
- Schema: `valid`
- Semantic status: `fail`
- Requirements observed: `18`
- Test cases observed: `36`
- Semantic errors: `41`

This attempt is immutable and excluded from acceptance pending independent Sol diagnosis. The output, validation result, semantic result and literal command observation are preserved in this directory.

