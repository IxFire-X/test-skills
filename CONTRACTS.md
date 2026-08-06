# Contract Reference

Generated from `contracts/pipeline.json`. Do not edit manually.

## Artifacts

| Artifact | Description |
|---|---|
| `raw_content` | Unstructured supplied requirements or code context. |
| `analytics_documentation` | Normalized requirements with provenance. |
| `source_code_and_diff` | Read-only project context and supplied changes. |
| `generated_test_cases` | Manual test cases derived from requirements. |
| `validation_report` | Review result for manual test cases. |
| `corrected_test_cases` | Safe corrected manual test cases. |
| `automation_matrix` | Mapping from accepted cases to generated tests. |
| `autotest_review` | Review result for generated automated tests. |
| `run_tests_verdict` | Deterministic execution evidence. |
| `trace_audit` | Requirement-to-execution traceability evidence. |

## Review verdicts

- `ПРИНЯТО`
- `AUTO_FIX_APPLIED`
- `ТРЕБУЕТ ДОРАБОТКИ`

## Execution verdict branches

| Verdict | Transform |
|---|---|
| `PASS` | `complete` |
| `FAIL` | `stop_failed` |
| `NOT_RUNNABLE` | `stop_not_runnable` |

## Language capabilities

| Language | Framework | Generation | Review | Execution | Status |
|---|---|---|---|---|---|
| java | junit5 | true | true | true | supported |
| python | pytest | true | true | true | supported |
| typescript | jest | false | false | false | experimental |
| go | go-testing | false | false | false | experimental |

## Artifact policy

- Persistent artifacts: `docs/to_do`
- Generated test source: isolated workspace under docs/to_do unless explicitly requested

## Traceability

`requirement` → `test_case` → `generated_method` → `execution_evidence`
