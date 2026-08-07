# Context-marker evaluation — rep-01

- UTC start: `2026-08-07T00:10:52.878Z`
- UTC finish: `2026-08-07T00:12:45.245Z`

## Raw prompt

> Read only artifacts/inputs/order-change.md and artifacts/inputs/source-diff.patch; classify supported order facts into analytics requirements and source context/provenance; do not invent manager approval, retention, authorization or other absent rules; write exactly one context-marker-output.json envelope declared by the skill; concise summary.

## Raw output

```json
{
  "schema_version": "2.1.0",
  "stage": "context-marker",
  "artifacts": {
    "analytics_documentation": {
      "requirements": [
        {
          "id": "REQ-OC-1",
          "text": "POST /orders accepts customerId and one or more lines, each containing sku and a positive quantity.",
          "provenance": ["artifacts/inputs/order-change.md#OC-1"]
        },
        {
          "id": "REQ-OC-2",
          "text": "The service rejects a line with an unknown SKU or a quantity that is zero or negative.",
          "provenance": ["artifacts/inputs/order-change.md#OC-2"]
        },
        {
          "id": "REQ-OC-3",
          "text": "A successful request creates an order in CREATED status, calculates the line-total sum, and emits order.created with the new order identifier.",
          "provenance": ["artifacts/inputs/order-change.md#OC-3"]
        }
      ]
    },
    "source_code_and_diff": {
      "sources": [
        "artifacts/inputs/source-diff.patch: CreateOrderService.create validates the customer and lines, saves Order.created, publishes OrderCreated with the order ID, and returns the order."
      ]
    }
  },
  "warnings": [
    "artifacts/inputs/order-change.md#OC-4: manager approval and retention period are unspecified and are not requirements.",
    "artifacts/inputs/source-diff.patch: no approval or retention branch is present in the supplied diff."
  ]
}
```

## Concise summary

Captured the three supported order requirements with analytics provenance, recorded the supplied service-diff context, and retained approval and retention as explicitly unspecified rather than creating rules.

## Actual commands

1. `Get-Content -LiteralPath C:\\Users\\User\\.codex\\plugins\\cache\\openai-curated-remote\\superpowers\\6.2.0\\skills\\using-superpowers\\SKILL.md` (workflow instruction read; dispatched-subagent stop rule applied)
2. `Get-Content` for the context-marker skill and the initially supplied campaign input paths (the two supplied input paths were not found).
3. `rg --files D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills | rg '(?:^|/|\\\\)(order-change\\.md|source-diff\\.patch)$'`
4. `Get-Content` for the located `docs/to_do/skill-tests/context-marker/artifacts/inputs/order-change.md` and `source-diff.patch`, plus the initially resolved direct-link paths (those direct-link paths were not found).
5. `rg --files D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills | rg '(?:^|/|\\\\)(CONTRACTS\\.md|context-marker-output\\.schema\\.json)$'`
6. `Get-Content` for the located root `CONTRACTS.md` and `schemas/context-marker-output.schema.json`.
7. `apply_patch` to create this report and `artifacts/outputs/02-green-initial/rep-01/context-marker-output.json`.
8. `Get-Content -Raw | ConvertFrom-Json` for the exact JSON artifact path, with structural checks for the envelope, three `REQ-OC-*` requirements, one source entry, and two warnings (`JSON_PARSE=PASS`, `ENVELOPE_CHECK=True`).
