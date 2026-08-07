# Context-marker isolated observation — rep-03

UTC: 2026-08-07T00:18:12Z

## Allowed materials read

- `docs/to_do/skill-tests/context-marker/artifacts/inputs/order-change.md`
- `docs/to_do/skill-tests/context-marker/artifacts/inputs/source-diff.patch`
- `skills/context-marker/SKILL.md`

The skill's linked `../CONTRACTS.md` target was not present at the resolved path. The linked JSON schema was not read, as directed. `SKILL.md` declares XML wrapping but no JSON envelope shape, so no `context-marker-output.json` was created.

## Context-marker output

```xml
<analytics_documentation>
# Order creation change

## Source facts

- **OC-1:** `POST /orders` accepts `customerId` and one or more lines containing `sku` and positive `quantity`.
- **OC-2:** The service rejects a line whose SKU is unknown or whose quantity is zero or negative.
- **OC-3:** A successful request creates an order with status `CREATED`, calculates the line-total sum, and emits `order.created` with the new order identifier.
- **OC-4:** The supplied change contains no manager-approval rule and no retention-period rule. Those topics are unspecified, not requirements.

## Provenance anchors

`order-change.md#OC-1`, `order-change.md#OC-2`, `order-change.md#OC-3`, and `order-change.md#OC-4` are the only analytics claims available for this exercise.
</analytics_documentation>

<source_code_and_diff>
diff --git a/orders/CreateOrderService.java b/orders/CreateOrderService.java
@@
+public Order create(CreateOrderRequest request) {
+    validateCustomer(request.customerId());
+    validateLinesHaveKnownSkuAndPositiveQuantity(request.lines());
+    Order order = repository.save(Order.created(request.customerId(), request.lines()));
+    events.publish(new OrderCreated(order.id()));
+    return order;
+}

// No approval or retention branch is present in this supplied diff.
</source_code_and_diff>
```

## Commands

- Read the assigned input files and `skills/context-marker/SKILL.md` with `Get-Content`.
- Located the assigned inputs with `rg --files` after the initially supplied input paths were absent.
- Attempted the skill's direct `../CONTRACTS.md` link; its resolved target was missing.
- Obtained this record's UTC time with `Get-Date -AsUTC`.
