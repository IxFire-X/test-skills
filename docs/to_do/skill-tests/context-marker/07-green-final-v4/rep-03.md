# Context-marker evaluator observation — green final v4 / rep-03

- Recorded UTC: `2026-08-07T01:38:20.4337762Z`
- Read scope: only the two supplied campaign inputs, the current context-marker skill, and its directly linked contract/schema.

## Prompt (verbatim)

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

## Envelope content

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
          "provenance": [
            "order-change.md#OC-1"
          ]
        },
        {
          "id": "REQ-OC-2",
          "text": "The service rejects a line whose SKU is unknown or whose quantity is zero or negative.",
          "provenance": [
            "order-change.md#OC-2"
          ]
        },
        {
          "id": "REQ-OC-3",
          "text": "A successful request creates an order with status CREATED, calculates the line-total sum, and emits order.created with the new order identifier.",
          "provenance": [
            "order-change.md#OC-3"
          ]
        }
      ]
    },
    "source_code_and_diff": {
      "sources": [
        "source-diff.patch#CreateOrderService.create: calls validateCustomer(request.customerId()).",
        "source-diff.patch#CreateOrderService.create: calls validateLinesHaveKnownSkuAndPositiveQuantity(request.lines()).",
        "source-diff.patch#CreateOrderService.create: saves Order.created(request.customerId(), request.lines()) through repository.save.",
        "source-diff.patch#CreateOrderService.create: publishes new OrderCreated(order.id()).",
        "source-diff.patch#end: no approval or retention branch is present in the supplied diff."
      ]
    }
  },
  "warnings": [
    "order-change.md#OC-4: The supplied change contains no manager-approval rule; manager approval is unspecified, not a requirement.",
    "order-change.md#OC-4: The supplied change contains no retention-period rule; retention is unspecified, not a requirement."
  ]
}
```

## Source-grounded summary

The analytics input supports three requirements: the accepted `POST /orders` shape, rejection of unknown-SKU or non-positive-quantity lines, and successful creation with `CREATED` status, line-total calculation, and `order.created` emission. The diff corroborates validation, persistence through `Order.created`, and `OrderCreated(order.id())` publication. Manager approval and retention are explicitly unspecified and remain warnings only.

## Validation

Command run:

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-03\context-marker-output.json
```

Result: `{"errors":[],"status":"valid"}` (exit code 0).
