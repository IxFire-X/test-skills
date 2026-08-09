# Tc-generator scale acceptance: 36 cases

## Purpose

Prove that the portable `tc-generator` preserves completeness, ordering, and bidirectional requirement coverage on a substantially larger feature. This supplemental acceptance does not alter canonical campaign scores or replace the pressure gate.

## Feature fixture

Use one synthetic but realistic bulk-shipment feature expressed as a context-marker v2.1.0 envelope with 18 explicit requirements and grounded source tokens.

### Six successful operations

1. Create shipment: `POST /api/v1/shipments` → `201 SHIPMENT_CREATED`.
2. Add package: `POST /api/v1/shipments/{shipment_id}/packages` → `201 PACKAGE_ADDED`.
3. Schedule pickup: `POST /api/v1/shipments/{shipment_id}/pickup` → `202 PICKUP_SCHEDULED`.
4. Cancel pickup: `DELETE /api/v1/shipments/{shipment_id}/pickup` → `200 PICKUP_CANCELLED`.
5. Generate label: `POST /api/v1/shipments/{shipment_id}/label` → `201 LABEL_CREATED`.
6. Close manifest: `POST /api/v1/manifests/{manifest_id}/close` → `200 MANIFEST_CLOSED`.

### Six inclusive integer ranges

Each range states both accepted-boundary and outside-range outcomes explicitly, producing four cases in canonical order.

1. `package_count`: `1..200`.
2. `weight_grams`: `1..30000`.
3. `length_cm`: `1..120`.
4. `width_cm`: `1..80`.
5. `height_cm`: `1..80`.
6. `declared_value_cents`: `1..100000`.

Accepted values use the operation's supported success status/code. Immediately outside values return `422` with a field-specific `*_OUT_OF_RANGE` code.

### Six explicit role denials

Each denial names one operation, role, and `403 FORBIDDEN` oracle: viewer/create shipment, auditor/add package, picker/schedule pickup, viewer/cancel pickup, guest/generate label, and picker/close manifest.

The input also contains one exact warning that no policy is supplied for `claims_adjuster`; it must remain a warning and produce no case.

## Expected result

The deterministic order is:

- six happy cases: `TC-0001..TC-0006`;
- twelve positive-boundary cases: `TC-0007..TC-0018`;
- twelve negative-boundary cases: `TC-0019..TC-0030`;
- six authorization-denial cases: `TC-0031..TC-0036`.

Total: exactly 36 cases linked bidirectionally to exactly 18 copied requirements. Every case has one supported atom, one action step, an executable oracle, and no invented policy or behavior.

## Isolation and evidence

All supplemental inputs, expected matrix, prompt, output, checker results, observations, and report live under `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/`. Main campaign metadata and scorecards are unchanged.

One fresh Terra evaluator reads the canonical `SKILL.md`, reference, output schema, and the scale input. It writes one reserved output once. The parent runs the output schema validator once and the scale semantic checker once; the first failure stops the attempt without repair or retry.

## Scale checker

The checker is driven by a machine-readable expected matrix and verifies:

- exact copied requirements, provenance, warnings, case count, IDs, and order;
- exact one-to-one atom rendering, categories, method/path, test data, status, and code;
- exact forward and reverse `REQ↔TC` coverage with no missing, duplicate, dangling, or extra links;
- absence of unsupported `claims_adjuster` cases and other invented assertions.

## Acceptance

Pass requires output-schema success, scale-checker success for all 36 cases, immutable literal-command evidence, and a fresh independent Sol `ship` verdict. This is supplemental breadth evidence only; pressure and final-GREEN remain mandatory.
