# Context-marker evaluator report — rep-01

Timestamp (UTC): `2026-08-07T01:21:13Z`

## Application prompt (verbatim)

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
        "source-diff.patch#CreateOrderService.create: create calls validateCustomer and validateLinesHaveKnownSkuAndPositiveQuantity, saves Order.created, and publishes OrderCreated with the saved order id.",
        "source-diff.patch#approval-retention-comment: the supplied diff contains no approval or retention branch."
      ]
    }
  },
  "warnings": [
    "Manager-approval and retention-period rules are unspecified, not requirements (order-change.md#OC-4; source-diff.patch#approval-retention-comment)."
  ]
}
```

## Source-grounded summary

The analytics input supports three requirements: accepted order payload fields and line cardinality; rejection of unknown-SKU, zero-quantity, and negative-quantity lines; and successful creation, line-total calculation, and `order.created` emission. The supplied diff corroborates validation, persistence, and event publication calls. Approval and retention rules are explicitly absent and are retained only as a warning.

## Authoritative validation

Command:

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\06-green-final-v3\rep-01\context-marker-output.json
```

Result:

```text
{"errors":[],"status":"valid"}
```
