# Tc-generator RED-v2 control rep-02

- Verdict: GAP (schema-valid and scorable)
- Evaluator: `sol_advisor_terra_implementer`, `fork_turns: none`
- Prompt SHA-256: `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`
- Output SHA-256: `6d8c913e6651462b2a69c5d0b572b50c7b1a76e28da941eba4e5e59f9c5f56dc`
- Schema validation: valid
- Semantic checker: `gap`, exit `0`

## Rubric

- `traceable-requirements`: PASS — all three supplied requirement identifiers, texts, provenance entries, and coverage links were preserved.
- `executable-outcomes`: FAIL — the evaluator combined both valid quantity boundaries into one case and emitted four rather than the six deterministic portable fixtures.
- `no-invented-authorization`: PASS — viewer denial was covered and the missing `warehouse_operator` policy remained a stated gap without an invented authorization outcome.

This independent baseline again produces useful executable cases but does not derive the stable one-case-per-fixture contract.
