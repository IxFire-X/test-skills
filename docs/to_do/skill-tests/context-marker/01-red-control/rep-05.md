# Context-marker isolated evaluation — rep-05

- Evaluator: fresh isolated observer
- Evaluation recorded (UTC): `2026-08-07T00:08:18.2481076Z`
- Inputs read: `artifacts/inputs/order-change.md`, `artifacts/inputs/source-diff.patch`
- Output: `artifacts/outputs/01-red-control/rep-05/context-marker-output.json`

## Raw prompt

> Read only those supplied inputs; classify supported order facts into analytics requirements/source context with provenance; do not invent manager approval, retention, authorization, or other absent requirements; write exactly one context-marker-output.json envelope with schema_version 2.1.0, stage context-marker, REQ-* id/text/provenance requirements, source list, warnings; concise summary. Record raw prompt, raw output/summary, actual commands, UTC times; no rubric conclusion.

## Raw output / summary

Three supported requirements were extracted:

- `REQ-001`: accepted request shape — `order-change.md#OC-1`.
- `REQ-002`: rejection of unknown SKU or non-positive quantity — `order-change.md#OC-2`.
- `REQ-003`: successful-order status, total, and event — `order-change.md#OC-3`.

`CTX-001` records that manager approval and retention are unspecified; `CTX-002` records the supplied implementation context. No approval, retention, authorization, or other absent requirement was inferred.

## Actual commands and UTC times

- `2026-08-07T00:08:18.2481076Z` — `[DateTime]::UtcNow.ToString('o')`
- Prior to output generation — `Get-ChildItem -LiteralPath '...\\artifacts\\inputs' -Recurse -File` (input-path discovery; no file content read).
- Prior to output generation — `Get-Content -LiteralPath order-change.md -Raw` and `Get-Content -LiteralPath source-diff.patch -Raw` (the only supplied-input contents read).
- Prior to output generation — `Test-Path -LiteralPath '...\\01-red-control'` and `Test-Path -LiteralPath '...\\outputs\\01-red-control\\rep-05'`.
- `2026-08-07T00:08:18.2481076Z` — created the output envelope and this evaluation record.
- `2026-08-07T00:09:11.3531454Z` — parsed `context-marker-output.json` with `ConvertFrom-Json` and confirmed schema version, stage, three REQ-* entries, two sources, and one warning.
