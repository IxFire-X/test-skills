# Context-marker observation — rep-05

## Exact prompt verbatim

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.

Before finishing, run exactly this validator command:
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-05\context-marker-output.json'
```

## Allowed input paths

- `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\inputs\order-change.md`
- `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\inputs\source-diff.patch`

## Output path

`D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-05\context-marker-output.json`

## Actual runtime UTC

`2026-08-07T01:43:37.494Z`

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
            "artifacts/inputs/order-change.md#OC-1"
          ]
        },
        {
          "id": "REQ-OC-2",
          "text": "The service rejects a line whose SKU is unknown or whose quantity is zero or negative.",
          "provenance": [
            "artifacts/inputs/order-change.md#OC-2"
          ]
        },
        {
          "id": "REQ-OC-3",
          "text": "A successful request creates an order with status CREATED, calculates the line-total sum, and emits order.created with the new order identifier.",
          "provenance": [
            "artifacts/inputs/order-change.md#OC-3"
          ]
        }
      ]
    },
    "source_code_and_diff": {
      "sources": [
        "artifacts/inputs/source-diff.patch#L3-L9: CreateOrderService.create validates the customer and line SKU/quantity, persists Order.created, publishes OrderCreated with the order id, and returns the order.",
        "artifacts/inputs/source-diff.patch#L11: No approval or retention branch is present in the supplied diff."
      ]
    }
  },
  "warnings": [
    "artifacts/inputs/order-change.md#OC-4: Manager-approval and retention-period rules are unspecified and are not requirements."
  ]
}
```

## Concise source-grounded summary

`POST /orders` accepts a customer and one or more positive-quantity SKU lines; unknown SKUs and nonpositive quantities are rejected. Successful creation persists a `CREATED` order, calculates the line-total sum, and emits `order.created` with the new identifier. Manager-approval and retention rules remain unspecified.

## Validator command and result

```powershell
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json' 'D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-05\context-marker-output.json'
```

Result (exit code `0`):

```json
{"errors":[],"status":"valid"}
```
