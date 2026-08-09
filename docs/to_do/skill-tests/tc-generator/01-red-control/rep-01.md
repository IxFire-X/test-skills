# Tc-generator RED control rep-01

- Verdict: GAP (schema-valid and scorable)
- Evaluator: `sol_advisor_terra_implementer`, `fork_turns: none`
- Prompt SHA-256: `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`
- Output SHA-256: `6453bf1fb9ee8948d7576d8a1656b2238471680f3c4d31148c0aedd308d4ac30`
- Schema validation: valid
- Semantic checker: `gap`, exit `0`

## Rubric

- `traceable-requirements`: PASS — all three input requirements, their provenance, and bidirectional coverage were preserved.
- `executable-outcomes`: FAIL — positive quantity-boundary cases asserted only that `422 QUANTITY_OUT_OF_RANGE` did not occur instead of asserting the concrete supported `201 CREATED` outcome; case fields also diverged from the deterministic portable rendering contract.
- `no-invented-authorization`: PASS — the supplied viewer denial was covered and the absent `warehouse_operator` policy was preserved as a warning without inventing an authorization outcome.

The baseline demonstrates a concrete documentation gap: schema validity and broadly sensible cases do not ensure deterministic, executable positive oracles or stable portable case rendering.
