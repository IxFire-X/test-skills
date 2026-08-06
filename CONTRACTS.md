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
| `generated_test_files` | Project-native generated test source files. |
| `generated_test_methods` | Traceable generated test methods. |
| `autotest_review` | Review result for generated automated tests. |
| `run_tests_verdict` | Deterministic execution evidence. |
| `execution_evidence` | Execution evidence for every generated test method. |
| `trace_audit` | Requirement-to-execution traceability evidence. |

## Review verdict branches

| Reviewer | Verdict | Transform |
|---|---|---|
| `tc-reviewer` | `ПРИНЯТО` | `continue_with_original` |
| `tc-reviewer` | `AUTO_FIX_APPLIED` | `continue_with_corrected` |
| `tc-reviewer` | `ТРЕБУЕТ ДОРАБОТКИ` | `stop_rework` |
| `autotest-reviewer` | `ПРИНЯТО` | `continue_with_original` |
| `autotest-reviewer` | `AUTO_FIX_APPLIED` | `continue_with_corrected` |
| `autotest-reviewer` | `ТРЕБУЕТ ДОРАБОТКИ` | `stop_rework` |

## Execution verdict branches

| Verdict | Transform |
|---|---|
| `PASS` | `continue_trace_audit` |
| `FAIL` | `stop_failed` |
| `NOT_RUNNABLE` | `stop_not_runnable` |
| `PASS` | `complete` |

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

`requirement` → `test_case` → `generated_file` → `generated_method` → `execution_evidence`
