# Pipeline: test-pipeline

Generated from `contracts/pipeline.json`. Do not edit manually.

Version: `4.0`

## Steps

| Step | `kind` | `accepts` | `forwards` | `produces` |
|---|---|---|---|---|
| `source-inventory` | `tool` | `raw_content` | `raw_content` | `technical_test_inventory`, `authorized_behavior_sources` |
| `context-marker` | `skill` | `raw_content`, `technical_test_inventory`, `authorized_behavior_sources` | `technical_test_inventory`, `authorized_behavior_sources` | `managed_behavior_context` |
| `test-classifier` | `skill` | `technical_test_inventory`, `authorized_behavior_sources`, `managed_behavior_context` | `technical_test_inventory`, `authorized_behavior_sources`, `managed_behavior_context` | `technical_test_classification` |
| `test-classifier-reviewer` | `skill` | `technical_test_inventory`, `authorized_behavior_sources`, `managed_behavior_context`, `technical_test_classification` | `managed_behavior_context` | `classification_review`, `effective_technical_evidence` |
| `tc-generator` | `skill` | `managed_behavior_context` | `managed_behavior_context` | `candidate_document` |
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
| `test-classifier-reviewer` | `classification_verdict` = `ПРИНЯТО` | `select_effective_technical_evidence` |
| `test-classifier-reviewer` | `classification_verdict` = `ТРЕБУЕТ ДОРАБОТКИ` | `stop_classification_rework` |
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
