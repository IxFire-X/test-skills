# Context-marker evaluator — green final v2 / rep-01

- Runtime UTC: `2026-08-07T01:15:59.0605200Z`
- Verdict: BLOCKED — the required validator could not start because its Python dependency is absent.

## Observation

### Raw application prompt

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

The envelope classifies OC-1 through OC-3 as three requirements and keeps their supplied `order-change.md#OC-*` provenance anchors. It preserves the supplied source diff as source-code context. The only relevant gaps are manager approval and retention period, both explicitly unspecified by OC-4; neither was converted into a requirement. No authorization policy was inferred.

## Machine envelope

`artifacts/outputs/05-green-final-v2/rep-01/context-marker-output.json`

## Schema validation

Command:

```text
python tools/validate_artifact.py schemas/context-marker-output.schema.json artifacts/outputs/05-green-final-v2/rep-01/context-marker-output.json
```

Output:

```text
{"errors":[{"message":"missing dependency: No module named 'jsonschema'","path":""}],"status":"error"}
```

Exit status: `1`

The command was executed exactly as shown. The JSON envelope was written, but no schema-validation PASS can be claimed until `jsonschema` is available to the validator.
