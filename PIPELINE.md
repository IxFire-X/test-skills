# Pipeline

Generated from `contracts/pipeline.json`. Do not edit manually.

| Step | Accepts | Produces |
|---|---|---|
| `context-marker` | raw_content | analytics_documentation, source_code_and_diff |
| `tc-generator` | analytics_documentation, source_code_and_diff | generated_test_cases |
| `tc-reviewer` | generated_test_cases | validation_report, corrected_test_cases |
| `tc-to-autotest` | generated_test_cases, corrected_test_cases | automation_matrix |
| `autotest-reviewer` | generated_test_cases, corrected_test_cases, automation_matrix | autotest_review |
| `run-tests` | automation_matrix | run_tests_verdict |
| `trace-check` | analytics_documentation, generated_test_cases, automation_matrix, run_tests_verdict | trace_audit |
