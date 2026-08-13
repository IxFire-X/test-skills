# Pipeline: test-pipeline

Generated from `contracts/pipeline.json`. Do not edit manually.

Version: `2.0`

## Steps

| Step | `kind` | `accepts` | `forwards` | `produces` |
|---|---|---|---|---|
| `context-marker` | `skill` | `raw_content` | — | `analytics_documentation`, `source_code_and_diff` |
| `tc-generator` | `skill` | `analytics_documentation`, `source_code_and_diff` | `analytics_documentation`, `source_code_and_diff` | `candidate_document` |
| `publish-candidate` | `tool` | `candidate_document` | `candidate_document` | `candidate_bundle_receipt` |
| `tc-reviewer` | `skill` | `candidate_document` | `candidate_document` | `validation_report`, `successor_document` |
| `revision-orchestrator` | `tool` | `candidate_document`, `candidate_bundle_receipt`, `validation_report`, `successor_document` | `candidate_bundle_receipt`, `validation_report` | `successor_bundle_receipt`, `effective_document`, `effective_bundle_receipt` |
| `tc-to-autotest` | `skill` | `effective_document`, `effective_bundle_receipt` | `effective_document`, `effective_bundle_receipt` | `automation_artifact` |
| `autotest-reviewer` | `skill` | `effective_document`, `automation_artifact` | `effective_document`, `automation_artifact` | `autotest_review` |
| `run-tests` | `tool` | `effective_document`, `automation_artifact`, `autotest_review` | `effective_document`, `automation_artifact`, `autotest_review` | `run_result` |
| `build-trace` | `tool` | `effective_document`, `automation_artifact`, `run_result` | `effective_document`, `automation_artifact`, `run_result` | `trace_document` |
| `trace-check` | `tool` | `trace_document` | `trace_document` | `trace_audit` |
| `finalize-orchestration` | `tool` | `effective_document`, `effective_bundle_receipt`, `automation_artifact`, `autotest_review`, `run_result`, `trace_document`, `trace_audit` | — | `orchestrator_output` |

## Transitions

| From | Predicates | Transform |
|---|---|---|
| `tc-reviewer` | `review_verdict` = `ПРИНЯТО` | `revision_orchestrator_selects_candidate` |
| `tc-reviewer` | `review_verdict` = `AUTO_FIX_APPLIED` | `revision_orchestrator_validates_publishes_selects_successor` |
| `tc-reviewer` | `review_verdict` = `ТРЕБУЕТ ДОРАБОТКИ` | `stop_rework` |
| `autotest-reviewer` | `review_verdict` = `ПРИНЯТО`, `automation_status` = `BLOCKED` | `build_trace_without_run` |
| `autotest-reviewer` | `review_verdict` = `ПРИНЯТО`, `required_symbol_pairs` = `0` | `build_trace_without_run` |
| `autotest-reviewer` | `review_verdict` = `ПРИНЯТО`, `required_symbol_pairs` = `one_or_more` | `run_tests` |
| `autotest-reviewer` | `review_verdict` = `AUTO_FIX_APPLIED` | `regenerate_automation_and_review_again` |
| `autotest-reviewer` | `review_verdict` = `ТРЕБУЕТ ДОРАБОТКИ` | `stop_rework` |
| `run-tests` | `execution_verdict` = `PASS` | `build_trace_then_trace_check` |
| `run-tests` | `execution_verdict` = `FAIL` | `build_trace_then_trace_check` |
| `run-tests` | `execution_verdict` = `NOT_RUNNABLE` | `build_trace_then_trace_check` |
| `trace-check` | `trace_verdict` = `PASS` | `finalize_orchestration` |
| `trace-check` | `trace_verdict` = `FAIL` | `stop_invalid_trace` |
