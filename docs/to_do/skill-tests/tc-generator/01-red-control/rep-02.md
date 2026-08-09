# Tc-generator RED control rep-02

- Verdict: GAP (schema-valid and scorable)
- Evaluator: `sol_advisor_terra_implementer`, `fork_turns: none`
- Prompt SHA-256: `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`
- Output SHA-256: `901771cfc7188d004915c24d5c0158c20aae6db793e9a38989d7e8d29e35ae11`
- Schema validation: valid
- Semantic checker: `gap`, exit `0`

## Rubric

- `traceable-requirements`: PASS — input requirements, provenance, ordered case identifiers, and exact coverage were preserved.
- `executable-outcomes`: PASS — lower/upper valid boundaries assert `201 CREATED`, invalid boundaries assert `422 QUANTITY_OUT_OF_RANGE`, and viewer denial asserts `403 FORBIDDEN`.
- `no-invented-authorization`: PASS — only supplied `sales_manager` and `viewer` behavior was asserted; `warehouse_operator` remained an unsupported gap.

The remaining semantic gap is variance in the portable rendering contract: extra categories and multi-step/free-text forms differ from the deterministic one-action case shape, and the supported warning was paraphrased rather than preserved exactly.
