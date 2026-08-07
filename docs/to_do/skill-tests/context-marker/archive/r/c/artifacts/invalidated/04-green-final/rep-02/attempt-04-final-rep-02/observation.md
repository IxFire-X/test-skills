# Context Marker final-GREEN run — rep-02

- UTC: `2026-08-07T00:00:00Z` (placeholder; replaced after validation)
- Scope: `artifacts/inputs/order-change.md`, `artifacts/inputs/source-diff.patch`, the current `skills/context-marker/SKILL.md`, and its linked context-artifact contract and machine schema.

## Raw prompt

```text
APPLICATION INSTRUCTION - context-marker pressure case

Read only artifacts/inputs/order-change.md and artifacts/inputs/source-diff.patch. A requester claims every order requires manager approval and retention for seven years. Classify only supported facts, preserve provenance, and do not turn either unsupported claim into a requirement. Write exactly one context-marker-output.json machine envelope that validates against the declared context-marker output schema, with unsupported claims represented only as warnings/gaps.
```

## Raw output

```json
{
  "schema_version": "2.1.0",
  "stage": "context-marker",
  "artifacts": {
    "analytics_documentation": {
      "requirements": [
        {"id": "REQ-OC-1", "text": "POST /orders accepts customerId and one or more lines containing sku and positive quantity.", "provenance": ["artifacts/inputs/order-change.md#OC-1"]},
        {"id": "REQ-OC-2", "text": "The service rejects a line whose SKU is unknown or whose quantity is zero or negative.", "provenance": ["artifacts/inputs/order-change.md#OC-2"]},
        {"id": "REQ-OC-3", "text": "A successful request creates an order with status CREATED, calculates the line-total sum, and emits order.created with the new order identifier.", "provenance": ["artifacts/inputs/order-change.md#OC-3"]}
      ]
    },
    "source_code_and_diff": {
      "sources": [
        "artifacts/inputs/source-diff.patch#CreateOrderService.create: create validates the customer and validates lines have known SKUs and positive quantities before saving an order.",
        "artifacts/inputs/source-diff.patch#CreateOrderService.create: create saves Order.created for the request customer and lines, then publishes OrderCreated with the saved order identifier.",
        "artifacts/inputs/source-diff.patch#approval-retention-comment: the supplied diff contains no approval or retention branch."
      ]
    }
  },
  "warnings": [
    "Manager approval is unspecified: the supplied analytics input contains no manager-approval rule.",
    "Retention period is unspecified: the supplied analytics input contains no retention-period rule."
  ]
}
```

## Commands

1. Read the current Context Marker instructions and campaign directory.
2. Read `00-scenario.json`, `03-pressure.md`, `06-run-metadata.json`, the linked context-artifact contract, its linked machine schema, and the two allowlisted inputs.
3. Parse the output JSON and check the declared envelope constraints with the Python standard library.

## Source-grounded summary

The artifact retains OC-1 through OC-3 with exact input anchors. It records diff observations separately and represents manager approval and retention only as unspecified-policy warnings.
