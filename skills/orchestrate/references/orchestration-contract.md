# Pipeline 2.0 orchestration contract

`contracts/pipeline.json`, the V3 stage schemas, and Task 12's `tools.orchestrate_test_case_revision` are executable truth. Candidate JSON is schema/semantic-validated and published by `tools.publish_test_case_bundle` before review. A valid full successor is similarly published before selection. Candidate and successor receipts are immutable audit evidence; only one effective document and its bare digest proceed.

## Exact route

1. `context-marker` produces V3 requirements.
2. `tc-generator` produces canonical JSON; `tools.test_case_projections` and the publisher make immutable Markdown/CSV projections.
3. `tc-reviewer` selects candidate or a complete successor through `tools.orchestrate_test_case_revision`.
4. `tc-to-autotest` and `autotest-reviewer` consume only effective JSON/digest.
5. `tools.run_tests.py` runs only generated nonzero-pair automation.
6. `tools.build_trace_document.py` creates terminal trace evidence; `tools.trace_check.py` validates it for every terminal branch.

Never parse Markdown/CSV, merge revisions, or hand-author receipts. Final status is trace-authoritative: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`, `FAIL`, or `NOT_RUNNABLE`. Manual-only and blocked branches are not PASS.
