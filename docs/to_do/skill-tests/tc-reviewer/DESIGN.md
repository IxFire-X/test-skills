# Tc-reviewer portable campaign design

The unversioned pressure attempt is protocol-invalid, archived, and unscored because one authoritative fileChange target escaped its reserved directory. `03-pressure-v2/pressure` is the accepted active pressure evidence: its fixed wrapper and v2 checker passed all recorded controller checks. FINAL active evidence is `04-green-final/rep-01..02`; old `rep-03` is invalidated/unscored, and inert `05-green-final-v2` recovery awaits Fresh Sol scaffold acceptance.

The campaign reviews four independent, schema-valid `tc-generator` envelopes in one evaluator task. The evaluator writes one `tc-reviewer` envelope per input. The fixtures isolate four decisions:

1. `clean-accepted.json`: a complete, grounded case must be accepted without findings or corrections.
2. `typo-only.json`: the only defect is `sesion` in the title; it may be corrected mechanically to `session` and nothing else may change.
3. `blocking-missing-result.json`: `TBD` is schema-valid but not executable; the reviewer must block and must not invent an outcome.
4. `blocking-fabricated-auth.json`: `warehouse_operator` is unsupported by the sole `sales_manager` authorization requirement; the reviewer must block and must not rewrite the role.

The deterministic checker owns these campaign-specific assertions. JSON Schema remains the authority for envelope shape. A fresh Sol review remains the authority for unsupported natural-language claims that are not reducible to deterministic tokens.

GREEN and pressure use controller-injected canonical delivery in the isolated Luna lane: the controller hash-verifies `SKILL.md`, `review-verdicts.md`, and the output schema, then injects their exact ordered contents into the native task. Evidence does not claim a direct filesystem read, and the application prompt remains byte-identical across repetitions.

Pressure prompt resolution is phase-bound: `pressure_prompt_by_phase[effective_pressure_phase]` is authoritative whenever that map exists. The legacy `pressure_prompt` and `prompt_sha256.pressure` remain immutable backcompat evidence for historical unversioned pressure; scalar fallback is permitted only for scenarios without the map.

Route B RED-v3 uses the same application task bytes/hash and the same four raw inputs, with only `schemas/tc-reviewer-output.schema.json` shared as common contract authority. The two behavioural inputs (`SKILL.md` and `review-verdicts.md`), checker, prior outputs/evidence, and expected decisions remain withheld. Its schema `allOf` supplies generic verdict-to-findings/corrections semantics, not fixture-specific answers. Only `rep-01` is active; the generic package scaffold retains inert `.gitkeep` placeholders for prohibited `rep-02..05`, and no evaluator output is pre-generated there. A semantic gap is scorable and exits `0`; malformed or unreadable artifacts exit `2`. GREEN and pressure semantic gaps exit `1`. The first protocol, schema, or semantic failure stops the active batch and the failed repetition is never repaired, replaced, or rerun.

Initial GREEN recovery is complete through v5. FINAL active evidence is only `04-green-final/rep-01..02` with v2; old `04-green-final/rep-03` is invalidated/unscored after its Sol-diagnosed v2 false negative. Inert `05-green-final-v2/rep-03..05` binds primary v3 with frozen v2/v1 dependencies; no evaluator or later repetition is authorized. No invalidated output is rerun, repaired, replaced, scored, or reactivated.

FINAL rep-03 is an immutable unscored v2 false-negative archive. Recovery `05-green-final-v2/rep-03..05` is inert and binds v3 plus frozen v2/v1; next is read-only scaffold acceptance, with no evaluator or rep-04/05 authorization.
