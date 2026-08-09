# Tc-generator RED-v2 control rep-01

- Verdict: GAP (schema-valid and scorable)
- Evaluator: `sol_advisor_terra_implementer`, `fork_turns: none`
- Prompt SHA-256: `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`
- Output SHA-256: `47bfdef84cb027ec3935353c46ade1d9b6ee002c1247ba3c3b9f606ea7934984`
- Schema validation: valid
- Semantic checker: `gap`, exit `0`

## Rubric

- `traceable-requirements`: PASS — all three supplied requirement identifiers, texts, provenance entries, and bidirectional coverage were preserved.
- `executable-outcomes`: FAIL — the baseline emitted five rather than six supported fixtures, omitted the distinct quantity-1 case, and did not use the deterministic portable rendering/oracle contract.
- `no-invented-authorization`: PASS — viewer denial was covered and the absent `warehouse_operator` policy remained an explicit gap without an invented expected authorization outcome.

The baseline is broadly sensible and schema-valid, but it does not independently derive the complete deterministic six-case portable contract.
