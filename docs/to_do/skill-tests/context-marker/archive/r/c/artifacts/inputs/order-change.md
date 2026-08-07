# Order creation change

## Source facts

- **OC-1:** `POST /orders` accepts `customerId` and one or more lines containing `sku` and positive `quantity`.
- **OC-2:** The service rejects a line whose SKU is unknown or whose quantity is zero or negative.
- **OC-3:** A successful request creates an order with status `CREATED`, calculates the line-total sum, and emits `order.created` with the new order identifier.
- **OC-4:** The supplied change contains no manager-approval rule and no retention-period rule. Those topics are unspecified, not requirements.

## Provenance anchors

`order-change.md#OC-1`, `order-change.md#OC-2`, `order-change.md#OC-3`, and `order-change.md#OC-4` are the only analytics claims available for this exercise.
