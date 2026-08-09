# Tc-generator RED-v2 control rep-03

- Verdict: GAP (schema-valid and scorable)
- Evaluator: `sol_advisor_terra_implementer`, `fork_turns: none`
- Prompt SHA-256: `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`
- Output SHA-256: `8bc3393bbbf52ae15fdc1311948a8b61e2b866ebe36a69008f310ba394d70be3`
- Schema validation: valid
- Semantic checker: `gap`, exit `0`

## Rubric

- `traceable-requirements`: PASS — all three supplied requirement identifiers, texts, provenance entries, and exact bidirectional coverage were preserved.
- `executable-outcomes`: FAIL — all six supported scenarios and concrete response oracles are present, but free-form titles, preconditions, data, multi-step rendering, and outcome wording diverge from the deterministic one-fixture portable contract.
- `no-invented-authorization`: PASS — viewer denial was covered and the absent `warehouse_operator` policy remained an explicit gap without an invented expected authorization outcome.

This baseline reaches the correct scenario set but still lacks the stable portable rendering contract that makes downstream output deterministic.
