# Context Marker Evaluation — rep-04

- Evaluated at (UTC): `2026-08-07T01:41:13Z`
- Result: PASS

## Canonical prompt (verbatim)

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

## Source-grounded summary

The envelope records three analytics requirements directly supported by `order-change.md#OC-1` through `#OC-3`. The supplied diff is retained as implementation context for validation, persistence, event publication, and its explicit lack of approval/retention branches. `order-change.md#OC-4` is represented only as a warning: manager approval and retention are unspecified. No authorization requirement is inferred.

## Envelope

```json
{
  "schema_version": "2.1.0",
  "stage": "context-marker",
  "artifacts": {
    "analytics_documentation": {
      "requirements": [
        {
          "id": "REQ-OC-1",
          "text": "POST /orders accepts customerId and one or more lines containing sku and positive quantity.",
          "provenance": ["order-change.md#OC-1"]
        },
        {
          "id": "REQ-OC-2",
          "text": "The service rejects a line whose SKU is unknown or whose quantity is zero or negative.",
          "provenance": ["order-change.md#OC-2"]
        },
        {
          "id": "REQ-OC-3",
          "text": "A successful request creates an order with status CREATED, calculates the line-total sum, and emits order.created with the new order identifier.",
          "provenance": ["order-change.md#OC-3"]
        }
      ]
    },
    "source_code_and_diff": {
      "sources": [
        "source-diff.patch#CreateOrderService.create: validates the customer and invokes validateLinesHaveKnownSkuAndPositiveQuantity for request lines.",
        "source-diff.patch#CreateOrderService.create: saves Order.created(request.customerId(), request.lines()) through the repository.",
        "source-diff.patch#CreateOrderService.create: publishes OrderCreated(order.id()) after saving the order.",
        "source-diff.patch#comment: no approval or retention branch is present in the supplied diff."
      ]
    }
  },
  "warnings": [
    "order-change.md#OC-4: manager-approval and retention-period rules are unspecified; no requirement was created for either topic."
  ]
}
```

## Validation

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-04\context-marker-output.json
{"errors":[],"status":"valid"}
```
