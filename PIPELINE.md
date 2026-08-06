# Pipeline

Generated from `contracts/pipeline.json`. Do not edit manually.

| Step | Accepts | Produces |
|---|---|---|
| `context-marker` | raw_content | analytics_documentation, source_code_and_diff |
| `tc-generator` | analytics_documentation, source_code_and_diff | generated_test_cases |
| `tc-reviewer` | generated_test_cases | validation_report, corrected_test_cases |
| `tc-to-autotest` | generated_test_cases, corrected_test_cases | automation_matrix, generated_test_files, generated_test_methods |
| `autotest-reviewer` | generated_test_cases, corrected_test_cases, automation_matrix, generated_test_files, generated_test_methods | autotest_review |
| `run-tests` | generated_test_files, generated_test_methods | run_tests_verdict, execution_evidence |
| `trace-check` | analytics_documentation, generated_test_cases, generated_test_files, generated_test_methods, execution_evidence, run_tests_verdict | trace_audit |
