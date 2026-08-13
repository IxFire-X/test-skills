# Contract Reference

Generated from `contracts/pipeline.json`. Do not edit manually.

## Artifacts

| Artifact | Description |
|---|---|
| `raw_content` | Unstructured supplied requirements or code context. |
| `technical_test_inventory` | Closed mechanical inventory of supported test files and symbols. |
| `authorized_behavior_sources` | Closed snapshot of supplied requirements and eligible product sources. |
| `managed_behavior_context` | Authorized behavior context isolated from technical test evidence. |
| `technical_test_classification` | Candidate scope classification for every inventoried test symbol. |
| `classification_review` | Independent completeness review of the technical classification. |
| `effective_technical_evidence` | Accepted classification sidecar retained outside the V3 downstream route. |
| `candidate_document` | Candidate bare canonical test document. |
| `candidate_bundle_receipt` | Immutable candidate JSON, Markdown, and CSV receipt. |
| `validation_report` | Reviewed candidate validation report. |
| `successor_document` | Optional full reviewed successor canonical document. |
| `successor_bundle_receipt` | Optional immutable successor bundle receipt. |
| `effective_document` | One selected bare canonical document. |
| `effective_bundle_receipt` | One selected immutable bundle receipt. |
| `automation_artifact` | Atomic generated automation relations. |
| `autotest_review` | Automated-test review result. |
| `run_result` | Optional V3 execution result. |
| `trace_document` | Atomic trace document. |
| `trace_audit` | Trace validation result. |
| `orchestrator_output` | Closed final orchestration artifact. |

## Canonical skill files

| Skill | Path |
|---|---|
| `context-marker` | `skills/context-marker/SKILL.md` |
| `test-classifier` | `skills/test-classifier/SKILL.md` |
| `test-classifier-reviewer` | `skills/test-classifier-reviewer/SKILL.md` |
| `tc-generator` | `skills/tc-generator/SKILL.md` |
| `tc-reviewer` | `skills/tc-reviewer/SKILL.md` |
| `tc-to-autotest` | `skills/tc-to-autotest/SKILL.md` |
| `autotest-reviewer` | `skills/autotest-reviewer/SKILL.md` |
| `orchestrate` | `skills/orchestrate/SKILL.md` |

## Verdict enums

| Verdict type | Values |
|---|---|
| `classification` | `ПРИНЯТО`, `ТРЕБУЕТ ДОРАБОТКИ` |
| `review` | `ПРИНЯТО`, `AUTO_FIX_APPLIED`, `ТРЕБУЕТ ДОРАБОТКИ` |
| `execution` | `PASS`, `FAIL`, `NOT_RUNNABLE` |
| `trace` | `PASS`, `FAIL` |

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

requirement -> case -> step -> expectation -> assertion -> file -> symbol -> current_run_evidence
