# Portable artifact schemas

The six pipeline-stage schemas use JSON Schema Draft 2020-12 and one strict
`2.1.0` envelope:

```json
{"schema_version":"2.1.0","stage":"tc-generator","artifacts":{},"warnings":[]}
```

| Schema | Stage | Required evidence in `artifacts` |
|---|---|---|
| `context-marker-output.schema.json` | `context-marker` | requirement provenance and source context |
| `tc-generator-output.schema.json` | `tc-generator` | requirements, test cases, coverage mappings |
| `tc-reviewer-output.schema.json` | `tc-reviewer` | review verdict and accepted/corrected cases |
| `tc-to-autotest-output.schema.json` | `tc-to-autotest` | automation matrix, generated files and methods |
| `autotest-reviewer-output.schema.json` | `autotest-reviewer` | review verdict and accepted/corrected methods |
| `orchestrator-output.schema.json` | `orchestrate` | run verdict, execution evidence and trace audit |

Every stable object boundary uses `additionalProperties: false`. The sole
documented open map is `.skillsrc`'s `skills_registry`, whose keys are
manifest-defined skill IDs. JSON Schema ensures typed, non-empty identifiers
and mappings; `trace-check` performs value-level cross-reference checks.

Validate one artifact with the portable helper:

```bash
python tools/validate_artifact.py schemas/tc-generator-output.schema.json artifact.json
```

It writes deterministic UTF-8 JSON: exit `0` is valid, `1` is an invalid
artifact, and `2` is an invalid schema, input, dependency, or runtime error.
Diagnostic `path` values are RFC-6901 JSON Pointers.
