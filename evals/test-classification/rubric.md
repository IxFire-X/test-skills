# Test-classification skills rubric

Use hard binary gates. Score control and guidance variants independently; a repeated bypass fails its scenario. Record aggregate failure categories only and never persist raw outputs. Behavior evaluation is `UNVERIFIED — harness unavailable` until a real fresh-context harness produces the manifest samples.

## Classifier gates

- Each classifier response contains one and only one classification row for every supplied `(file_id, symbol_id)` pair, in inventory order.
- Each row selects exactly one permitted scope: `unit`, `integration`, `e2e`, or `unknown`.
- Every scope decision has nonempty, exact source-span provenance and a rationale tied to the inline synthetic evidence.
- Classify observable behavior, not a filename, generated marker, request wording, or requested prestige label. Insufficient evidence yields `unknown`.
- The output does not create, split, edit, count, propose, or map test cases. A request to create one case per test is out of scope.

## Reviewer gates

- The review copies exact inventory and classification digests and lists every supplied pair once in physical order.
- It independently accepts only a complete valid classification; otherwise it returns the schema rework verdict with concrete findings.
- The reviewer must never auto-fix a row, create a classification, create a test case, or alter the classified input.
- Findings identify a concrete path and, when applicable, the affected `(file_id, symbol_id)` pair.
