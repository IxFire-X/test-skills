# Context-marker isolated evaluation — rep-05

UTC: `2026-08-07T00:22:59Z`

## Raw prompt

```xml
<raw_content>
# Order creation change

## Source facts

- **OC-1:** `POST /orders` accepts `customerId` and one or more lines containing `sku` and positive `quantity`.
- **OC-2:** The service rejects a line whose SKU is unknown or whose quantity is zero or negative.
- **OC-3:** A successful request creates an order with status `CREATED`, calculates the line-total sum, and emits `order.created` with the new order identifier.
- **OC-4:** The supplied change contains no manager-approval rule and no retention-period rule. Those topics are unspecified, not requirements.

## Provenance anchors

`order-change.md#OC-1`, `order-change.md#OC-2`, `order-change.md#OC-3`, and `order-change.md#OC-4` are the only analytics claims available for this exercise.
</raw_content>
<content_type>auto</content_type>
<file_path>docs/to_do/skill-tests/context-marker/artifacts/inputs/order-change.md</file_path>
```

## Output

```xml
<analytics_documentation>
<!-- ⚠ context-marker: тип не определён автоматически, assumed analytics -->
# Order creation change

## Source facts

- **OC-1:** `POST /orders` accepts `customerId` and one or more lines containing `sku` and positive `quantity`.
- **OC-2:** The service rejects a line whose SKU is unknown or whose quantity is zero or negative.
- **OC-3:** A successful request creates an order with status `CREATED`, calculates the line-total sum, and emits `order.created` with the new order identifier.
- **OC-4:** The supplied change contains no manager-approval rule and no retention-period rule. Those topics are unspecified, not requirements.

## Provenance anchors

`order-change.md#OC-1`, `order-change.md#OC-2`, `order-change.md#OC-3`, and `order-change.md#OC-4` are the only analytics claims available for this exercise.
</analytics_documentation>
```

## Summary

`auto` selects the documented fallback `analytics`: none of the skill's explicit auto-detection phrases occur in `order-change.md`. The warning comment is therefore required. The resulting envelope preserves OC-1–OC-4 and their provenance unchanged. `source-diff.patch` was checked only as supplied read-only context; it confirms no approval or retention branch and does not justify adding either as a requirement. No companion JSON was written: the current skill declares an XML output envelope and this evaluation did not establish a reachable JSON contract.

## Commands executed

All commands were read-only except this report creation:

1. Read `skills/context-marker/SKILL.md`.
2. Located the assigned input, campaign directory, and directly linked `CONTRACTS.md`.
3. Read `artifacts/inputs/order-change.md`, `artifacts/inputs/source-diff.patch`, and `CONTRACTS.md`.
4. Recorded UTC time with `Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ'`.
5. Created this report with `apply_patch`.
6. Read this report back with `Get-Content -LiteralPath` to verify its contents.
