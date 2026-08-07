# Context-marker final evaluator replica 02

- Runtime UTC: `2026-08-07T01:25:13.7572892Z`
- Scope read: the two supplied campaign inputs, the current `skills/context-marker/SKILL.md`, its direct contract link, and the declared schema.
- Result: valid.

## Canonical prompt (verbatim)

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
        "artifacts/inputs/source-diff.patch#CreateOrderService.create: calls validateCustomer(request.customerId()), validates lines for known SKU and positive quantity, saves Order.created(...), and publishes new OrderCreated(order.id()).",
        "artifacts/inputs/source-diff.patch#approval-retention-comment: no approval or retention branch is present in the supplied diff."
      ]
    }
  },
  "warnings": [
    "Manager approval is unspecified: artifacts/inputs/order-change.md#OC-4 states that the supplied change contains no manager-approval rule; artifacts/inputs/source-diff.patch has no approval branch.",
    "Retention period is unspecified: artifacts/inputs/order-change.md#OC-4 states that the supplied change contains no retention-period rule; artifacts/inputs/source-diff.patch has no retention branch."
  ]
}
```

## Source-grounded summary

The envelope records the three explicit order requirements (accepted request shape, rejected invalid lines, and successful creation behavior) with OC-specific provenance. The diff supplies code context for customer and line validation, persistence, and event publication. Approval and retention remain warnings because the supplied analytics facts explicitly declare both topics unspecified; no authorization policy was added because the supplied inputs do not make one relevant.

## Validation

Command run:

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\06-green-final-v3\rep-02\context-marker-output.json
```

Output:

```json
{"errors":[],"status":"valid"}
```

Exit code: `0`
