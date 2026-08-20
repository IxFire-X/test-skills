# Test-classifier contract

Input is the existing technical-test inventory plus authorized source snippets. Read `schemas/test-classifier-output.schema.json` as the executable carrier contract.

For every inventory `(file_id, symbol_id)` pair, emit one classification row with exactly one `test_scope`: `unit`, `integration`, `e2e`, or `unknown`. Supply nonempty source-span provenance and an evidence-based rationale. Preserve the inventory pair identity; the schema/tooling owns the digest, physical order, and complete-coverage mechanics.

The classifier does not originate requirements, judge implementation quality, generate automation, or create test cases. It runs after the semantic prefix is `READY_FOR_PIPELINE_TAIL`, classifies the supplied technical test only, and never changes or informs the generator-safe changed behavior context. When evidence does not establish the runtime boundary, select `unknown` instead of inferring it from naming or a request.
