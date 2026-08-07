# Context-marker isolated execution record — green initial rep-04

- UTC: 2026-08-07T00:20:49.9790446Z
- Scope read: `00-scenario.json`, `artifacts/inputs/order-change.md`,
  `artifacts/inputs/source-diff.patch`, `skills/context-marker/SKILL.md`, and its
  directly linked `CONTRACTS.md`.
- Excluded: schema, run metadata, scorecards, other repetitions, and all other
  campaign material.

## Raw prompt

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

## Raw output

The current `context-marker` skill declares XML output tags only; it does not
declare a `context-marker-output.json` envelope. Schema inspection is excluded
for this evaluation. The single declared skill envelope used below is therefore
`analytics_documentation`.

```xml
<analytics_documentation>
# Supported order-creation facts

## Analytics requirements

- **REQ-OC-1:** `POST /orders` accepts `customerId` and one or more lines, each
  with `sku` and a positive `quantity`.
  Provenance: `order-change.md#OC-1`.
- **REQ-OC-2:** Reject a line when its SKU is unknown or its quantity is zero or
  negative.
  Provenance: `order-change.md#OC-2`.
- **REQ-OC-3:** On success, create an order with status `CREATED`, calculate the
  line-total sum, and emit `order.created` with the new order identifier.
  Provenance: `order-change.md#OC-3`.

## Source-code context

- `CreateOrderService.create` validates the request customer and calls
  `validateLinesHaveKnownSkuAndPositiveQuantity` before saving
  `Order.created(request.customerId(), request.lines())`.
  Provenance: `source-diff.patch` (`CreateOrderService.create`).
- After saving, the supplied diff publishes `new OrderCreated(order.id())` and
  returns the order.
  Provenance: `source-diff.patch` (`CreateOrderService.create`).

## Warnings / gaps

- Manager approval and retention period are unspecified, not requirements.
  Provenance: `order-change.md#OC-4`; the supplied diff also has no approval or
  retention branch.
- No authorization policy is stated in either supplied input; none is inferred.
</analytics_documentation>
```

## Concise source-grounded summary

The supplied analytics supports request-shape validation, rejection of unknown
SKUs and non-positive quantities, and successful creation/event behavior. The
diff corroborates validation, persistence, and event publication. It supplies no
manager-approval, retention, or authorization rule.

## Commands and UTC evidence

```text
Get-Content -LiteralPath skills/context-marker/SKILL.md
Get-Content -LiteralPath CONTRACTS.md
Get-Content -LiteralPath docs/to_do/skill-tests/context-marker/00-scenario.json
Get-Content -LiteralPath docs/to_do/skill-tests/context-marker/artifacts/inputs/order-change.md
Get-Content -LiteralPath docs/to_do/skill-tests/context-marker/artifacts/inputs/source-diff.patch
Get-Date -AsUTC -Format o
```

Observed UTC: `2026-08-07T00:20:49.9790446Z`.
