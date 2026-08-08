# Context-marker five-run gate decision

Decision: expand from three to five independent FINAL repetitions.

Evidence: fresh Sol semantic review returned ship, overall PASS, STABLE_FOR_FIVE, with all four rubrics true for rep-01, rep-02, and rep-03. Observed variance is limited to semantically equivalent warning wording; contract-significant content is stable.

Constraints: run rep-04 and rep-05 sequentially with the same canonical prompt hash and protocol v1; stop on the first protocol/schema/semantic failure; do not replace, reuse, or repair a repetition.

Evidence file: artifacts/protocol/04-green-final/three-run-semantic-review.json

## Five-run acceptance

The fresh five-run Sol review returned `ship` and `OVERALL PASS`: all 20 rubric booleans are true across rep-01 through rep-05. Requirements, provenance, source observations, canonical branches, and stable IDs are identical; only faithful warning wording varies. The adaptive gate stops at five repetitions, with no further evaluator runs.

Evidence file: artifacts/protocol/04-green-final/five-run-semantic-review.json
