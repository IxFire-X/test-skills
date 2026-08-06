# Artifact schema validation

Each portable pipeline stage emits the versioned `2.1.0` envelope with exactly
`schema_version`, `stage`, `artifacts`, and `warnings`. The stage value is a
schema constant, so a payload cannot be validated accidentally against a
different stage's contract.

Use `tools/validate_artifact.py SCHEMA ARTIFACT` at every handoff. Its stdout
is deterministic UTF-8 JSON:

```json
{"errors":[{"message":"'id' is a required property","path":"/artifacts/test_cases/0/id"}],"status":"invalid"}
```

Exit codes are fixed: `0` valid, `1` artifact invalid, `2` schema/input/missing
dependency/runtime failure. Schema validation happens before artifact
validation, preventing a malformed schema from being reported as user data
failure.

Schemas require requirement provenance, requirement-to-test-case coverage,
automation file/method mappings, Russian review verdicts (`ПРИНЯТО`,
`AUTO_FIX_APPLIED`, `ТРЕБУЕТ ДОРАБОТКИ`), and execution/trace evidence.
They reject empty evidence where a success claim would otherwise conceal
incomplete work. JSON Schema cannot compare runtime values across independently
listed arrays; `trace-check` is the semantic gate for orphan or mismatched IDs.
