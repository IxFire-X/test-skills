# Contract Reference

Generated from `contracts/pipeline.json`. Do not edit manually.

## Artifacts

| Artifact | Description |
|---|---|
| `raw_content` | Authorized feature requirements and source references. |
| `technical_test_inventory` | Closed mechanical inventory of supported test files and symbols. |
| `authorized_behavior_sources` | Closed snapshot of authorized requirements and eligible product sources. |
| `change_scope_receipt` | Immutable reviewed FULL or CHANGE_SET scope receipt. |
| `managed_behavior_context` | Complete validated behavior context retained for technical evidence validation. |
| `changed_behavior_context` | Generator-safe complete or change-scoped behavior projection. |
| `behavior_source_accounting` | Complete source-accounting sidecar; never enters generation. |
| `behavior_context_receipt` | Immutable composed behavior-context receipt. |
| `technical_test_classification` | Candidate technical classification after the semantic-prefix handoff. |
| `classification_review` | Independent review of the technical classification. |
| `effective_technical_evidence` | Accepted isolated technical evidence gate; never influences generator semantics. |
| `canonical_document_delta` | CHANGE_SET-only canonical document delta. |
| `delta_application_receipt` | Deterministic delta application result. |
| `unchanged_document_selection` | Zero-op selection bound to the predecessor baseline. |
| `candidate_document` | Candidate bare canonical test document. |
| `candidate_bundle_receipt` | Immutable candidate JSON, Markdown, and CSV receipt. |
| `validation_report` | Reviewed candidate validation report or zero-op branch binding. |
| `successor_document` | Optional complete reviewed successor canonical document. |
| `successor_bundle_receipt` | Optional immutable successor bundle receipt. |
| `effective_document` | One selected bare canonical document. |
| `effective_bundle_receipt` | One selected immutable bundle receipt. |
| `automation_artifact` | Atomic generated automation relations. |
| `autotest_review` | Automated-test review result. |
| `run_result` | Optional execution result. |
| `trace_document` | Atomic trace document. |
| `trace_audit` | Trace validation result. |
| `orchestrator_output` | Existing closed V3 orchestration artifact. |
| `terminal_run_receipt` | Closed terminal receipt bound to prefix readback and tail artifacts. |
| `baseline_advancement` | Durable eligible, ineligible, idempotent, or conflicting baseline advancement. |
| `successor_baseline_receipt` | Eligible successor baseline receipt nested in baseline advancement. |

## Canonical skill files

| Skill | Path |
|---|---|
| `change-scope` | `skills/change-scope/SKILL.md` |
| `context-marker` | `skills/context-marker/SKILL.md` |
| `test-classifier` | `skills/test-classifier/SKILL.md` |
| `test-classifier-reviewer` | `skills/test-classifier-reviewer/SKILL.md` |
| `tc-generator` | `skills/tc-generator/SKILL.md` |
| `tc-reviewer` | `skills/tc-reviewer/SKILL.md` |
| `tc-to-autotest` | `skills/tc-to-autotest/SKILL.md` |
| `autotest-reviewer` | `skills/autotest-reviewer/SKILL.md` |
| `orchestrate` | `skills/orchestrate/SKILL.md` |

## Historical rejection registry

| Component | Version | Live status |
|---|---|---|
| `pipeline` | `5.0` | `REJECTED` |
| `context-marker` | `5.0.0` | `REJECTED` |
| `behavior-context-receipt` | `1.0.0` | `REJECTED` |

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
| `test-classifier-reviewer` | `classification_verdict` = `ПРИНЯТО` | `continue_isolated_technical_evidence_gate` |
| `test-classifier-reviewer` | `classification_verdict` = `ТРЕБУЕТ ДОРАБОТКИ` | `stop_classification_rework` |
| `tc-generator` | `run_mode` = `FULL` | `publish_candidate` |
| `tc-generator` | `run_mode` = `CHANGE_SET` | `apply_document_delta` |
| `apply-document-delta` | `delta_status` = `CHANGED` | `publish_candidate` |
| `apply-document-delta` | `delta_status` = `UNCHANGED` | `select_unchanged_document` |
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
| `trace-check` | `trace_verdict` = `PASS` | `finalize_orchestration_then_advance_baseline` |
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
