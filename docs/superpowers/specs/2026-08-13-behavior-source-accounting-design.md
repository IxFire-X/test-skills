# Behavior Source Accounting Design

Date: 2026-08-13

## Problem

Context Marker V4 binds its output to the digest of the full authorized-source snapshot, but it validates only sources that the model chose to mention. A schema-valid InvenTree calibration therefore represented 5 of 1,685 authorized product sources, produced six requirements and seven cases, and still passed the mechanical context gate. An independent completeness review correctly returned REWORK.

The fix must prove that every authorized source was considered without creating one requirement or one test case per file. It must also keep technical accounting out of the test-case generator.

## Decisions

### Context Marker V5 has two sibling artifacts

The closed `context-marker` envelope becomes version `5.0.0` and contains exactly:

- `managed_behavior_context`: the existing requirement/product-source graph, with the same semantic meaning;
- `behavior_source_accounting`: a technical sidecar that accounts for every authorized source.

`behavior_source_accounting` contains:

- `authorized_behavior_sources_sha256`, equal to the managed context and inventory snapshot;
- `context_receipt_sha256`, equal to the mechanically validated receipt;
- `source_dispositions`, in the exact physical order of `authorized_behavior_sources.sources`;
- `behavior_fragment_groups`, covering every receipt fragment exactly once.

Every disposition is one of two closed variants:

```json
{
  "source_id": "SOURCE-...",
  "disposition": "represented",
  "requirement_ids": ["REQ-0001"]
}
```

```json
{
  "source_id": "SOURCE-...",
  "disposition": "no_supported_observable_fact",
  "reason": "no_supported_actor_operation_or_outcome_after_full_review"
}
```

The reason is deliberately semantic and category-neutral. A file being configuration, documentation, presentation, metadata, or an internal helper never permits exclusion by itself. There is no free-text exclusion field and no source body in the ledger.

### Exact accounting invariants

The public validator enforces:

1. the accounting digest equals the authorized-source snapshot digest;
2. the disposition source IDs equal all authorized source IDs exactly once and in inventory order;
3. a `represented` row has nonempty, unique requirement IDs in canonical requirement order;
4. its requirement IDs equal the exact inverse of `requirement_sources` for that source;
5. a represented `product_file` has an exact `product_sources` row and current matching bytes;
6. a `no_supported_observable_fact` source is absent from `requirement_sources` and `product_sources`;
7. `supplied_requirement` sources cannot be excluded;
8. foreign, duplicate, pending, unreadable, or unaccounted sources fail the gate.

Every receipt fragment has a stable `FRAGMENT-<64 lowercase hex>` ID. `behavior_fragment_groups` is an ordered closed array of `{group_id, fragment_ids, requirement_ids}`. Every receipt fragment ID appears in exactly one group; every group has at least one canonical requirement ID; requirement IDs use canonical order. Multiple fragments may merge into one group and many groups may support one requirement, but no discovered fragment may disappear during reduction.

For each `represented` source, the canonical union of requirement IDs from every group containing that source's fragments must equal both the source disposition's `requirement_ids` and the exact inverse of `requirement_sources`. This prevents a fragment from being mapped to one requirement while its source graph claims another. For each `no_supported_observable_fact` source, the fragment-group union and inverse requirement links must both be empty, and no `product_sources` row may exist.

The ledger proves complete review/accounting. It does not claim that a model found every behavior inside a large file; independent semantic review remains required and must sample exclusions and every major domain.

### Generator isolation remains strict

Pipeline 5.0 registers `behavior_source_accounting` as a Context Marker sidecar, but `tc-generator.accepts` and `tc-generator.forwards` remain exactly `managed_behavior_context`. Generator skills must reject the sidecar, raw product sources, technical inventory, and classification evidence as inputs.

The pipeline version changes from `4.0` to `5.0` because the closed stage envelope and carrier registry change incompatibly. Existing V4 context artifacts remain historical evidence and are rejected by the live V5 route.

### One deep validation seam

`validate-context`, `select`, and `audit_test_portfolio` must call the same public context-envelope loader/validator. The current `_context_requirements` shortcut is removed; no public caller can pass raw requirements or extract them from an incomplete or schema-invalid envelope. The raw-requirement selector becomes a private implementation helper.

The seam returns an immutable `ValidatedBehaviorContext` carrying the requirements and verified receipt identity. It owns schema selection, snapshot binding, current-byte checks, source-graph validation, accounting validation, receipt binding, and diagnostic precedence.

### Closed deterministic batch protocol, not a target count

A new deep helper plans and validates semantic extraction batches from the immutable source inventory. It owns three closed version `1.0.0` schemas:

- `behavior-context-plan.schema.json`
- `behavior-context-batch-result.schema.json`
- `behavior-context-receipt.schema.json`

The public planning CLI is:

```text
python tools/test_classification.py context-plan \
  --project <root> \
  --skillsrc <root>/.skillsrc \
  --module <module-id> \
  --inventory <source-inventory.json> \
  [--supplied-input SOURCE_ID=PATH ...] \
  --output <create-only-context-plan.json>
```

The plan contains the authorized-source digest, selected module identity, and ordered batches. Every batch has a stable `BATCH-######` ID. Every item has a stable `ITEM-######` ID, source ID, deterministic domain key, a half-open non-overlapping `accounted_range`, and a bounded overlapping `read_range`. Accounted ranges for a source cover `[0,content_size)` exactly without gaps or overlaps. Read ranges extend at most 16 KiB before and after the accounted range at valid UTF-8 code-point boundaries. Zero-byte sources get `[0,0)` for both. Oversized one-line files are split by accounted byte ranges, so the 642-KB one-line `icons.json` is representable. The fixed internal byte budget and overlap are not user-configurable and are not test-case targets.

For a product source, `domain_key` is derived mechanically: choose the longest declared source root containing the path; take up to the first three directory segments after that root, excluding the filename; use `_root` if none. Supplied requirements use `_supplied`. Separators are normalized to `/`. This derivation is schema-tested for root, nested, file-valued, and overlapping source roots.

Product-file bytes come only from the confined project path already bound by inventory. Each authorized `supplied_requirement` requires exactly one repeatable `SOURCE_ID=PATH` input; its bytes must match the V1 inventory digest and must be nonempty. Paths are controller inputs and are not persisted. Missing, duplicate, foreign, empty, or mismatched supplied inputs block planning.

Each LLM batch result binds to the canonical plan SHA-256 and exact batch ID, has one item result per planned item in order, and is either:

- `behavior_fragments`, with nonempty structured fragments and evidence byte ranges confined to the item; or
- `no_supported_observable_fact`.

Fragments use closed fields for stable fragment ID, actor, public operation/trigger, conditions, observable outcomes, anchor byte, and evidence ranges. The anchor byte must lie in the item's accounted range; evidence may lie anywhere in its read range. This gives exactly one owning item to a boundary-spanning fragment while still providing bounded adjacent context. Fragments do not define requirements or cases.

The receipt CLI is:

```text
python tools/test_classification.py context-receipt \
  --project <root> \
  --skillsrc <root>/.skillsrc \
  --module <module-id> \
  --inventory <source-inventory.json> \
  --plan <context-plan.json> \
  --batch-result <result.json> [--batch-result <result.json> ...] \
  [--supplied-input SOURCE_ID=PATH ...] \
  --output <create-only-context-receipt.json>
```

It validates the selected normalized module and recomputes every domain key from `.skillsrc`; then it validates the plan against current bytes, exact plan/batch/item IDs, one result per batch, exact item order, anchor/evidence confinement, and every accounted source range exactly once. It emits an immutable receipt containing the inventory digest, plan digest, ordered batch-result digests, the complete ordered fragment registry (fragment ID, source ID, owning item ID), and one source outcome (`source_id`, validated `domain_key`, outcome) per authorized source in inventory order. Downstream semantic audits consume domain keys only from this validated receipt.

The final V5 accounting sidecar includes `context_receipt_sha256`. `validate-context`, `select`, and the portfolio audit require the receipt path, validate its schema/digest, and require dispositions to equal its source outcomes. Every source with fragments must be represented and mapped to requirements; only a source whose complete ranges returned no fragments may be excluded. Fragment groups must cover the receipt fragment registry exactly once, so reduction/deduplication cannot silently discard a discovered fact.

The planner and receipt builder read only already-authorized current files, verify their digests, never import or execute project code, never follow symlinks, and write create-only compact JSON. Plan, batch results, and receipt are run-support evidence; only the receipt and accounting sidecar cross the classification gate, and none enters the generator.

The Context Marker skill must process every planned batch exactly once, persist one closed result per batch, build the receipt, reduce structured fragments by domain, globally deduplicate them, and emit both V5 artifacts. A terminal disposition is required for every source before validation can pass. Reducers consume structured fragments rather than rereading raw source; no project-specific materializer is allowed.

### Adaptive scenario boundaries remain semantic

Requirements, source files, batches, and existing tests do not define case boundaries. The generator groups facts by actor, public operation, initial state, transaction/cleanup, and observable outcome. It splits only when independently executable setup, interface, state, or mutually exclusive outcome requires it. Many files may support one requirement; one case may cover many requirements.

## Diagnostics and safety

All diagnostics use the existing immutable `{path, code, message}` carrier and never include source contents, environment values, or secrets. New stable codes are:

- `BEHAVIOR_ACCOUNTING_SHAPE`
- `BEHAVIOR_ACCOUNTING_DIGEST`
- `BEHAVIOR_ACCOUNTING_COVERAGE`
- `BEHAVIOR_ACCOUNTING_ORDER`
- `BEHAVIOR_ACCOUNTING_LINK`
- `BEHAVIOR_ACCOUNTING_DISPOSITION`
- `BEHAVIOR_PLAN`
- `BEHAVIOR_BATCH_RESULT`
- `BEHAVIOR_RECEIPT`
- `BEHAVIOR_SUPPLIED_INPUT`

Direct CLI mode returns one compact safe JSON object and exit 2 on input/validation errors, with no traceback. Output paths are create-only.

## Compatibility and migration

- Preserve the accepted source-inventory V1 contract and its digest; the InvenTree rerun reuses the immutable 1,685-row inventory.
- Replace checked-in Context Marker V4 fixtures/evals with V5 equivalents; V4 remains a legacy rejection row.
- Update Pipeline registry/schema/checker/doctor/generated docs/orchestrator guidance to 5.0 while preserving automatic `.skillsrc` wording and all V3 execution/trace semantics.
- Pipeline 5 exact prefix carrier arrays are:
  - `source-inventory`: accepts `[raw_content]`; forwards `[raw_content]`; produces `[technical_test_inventory, authorized_behavior_sources]`; rejects `[]`.
  - `context-marker`: accepts `[raw_content, technical_test_inventory, authorized_behavior_sources]`; forwards `[technical_test_inventory, authorized_behavior_sources]`; produces `[managed_behavior_context, behavior_source_accounting, behavior_context_receipt]`; rejects `[]`.
  - `test-classifier`: accepts and forwards `[technical_test_inventory, authorized_behavior_sources, managed_behavior_context, behavior_source_accounting, behavior_context_receipt]`; produces `[technical_test_classification]`; rejects `[]`.
  - `test-classifier-reviewer`: accepts `[technical_test_inventory, authorized_behavior_sources, managed_behavior_context, behavior_source_accounting, behavior_context_receipt, technical_test_classification]`; forwards `[managed_behavior_context]`; produces `[classification_review, effective_technical_evidence]`; rejects `[]`.
  - `tc-generator`: accepts and forwards `[managed_behavior_context]`; produces `[candidate_document]`; rejects `[raw_content, technical_test_inventory, authorized_behavior_sources, behavior_source_accounting, behavior_context_receipt, technical_test_classification, classification_review, effective_technical_evidence]` in that order.
  - No later stage may accept or forward the accounting or receipt carriers.
- Update classifier selection tests so incomplete context no longer succeeds.
- Preserve projection bytes and bundle publication behavior.
- Do not change supported test filename patterns or protected discovery files.

## Real-project rerun

Preserve the rejected InvenTree attempt unchanged. Create these exact new directories:

- `02-context-marker/attempt-02/plan.json`, `batch-results/`, `domain-reductions/`, `receipt.json`, `context-marker-output.json`, `context-validation.json`, `completeness-review.json`
- `03-test-classifier/attempt-02/test-classifier-output.json`, `classifier-validation.json`
- `04-classifier-reviewer/attempt-02/reviewer-output.json`, `effective-technical-evidence.json`
- `05-tc-generator/attempt-02/tc-generator-output.json`, `canonical-document.json`, `canonical-validation.json`, `candidate-bundle/`, `candidate-bundle-receipt.json`
- `05-tc-reviewer/attempt-02/tc-reviewer-output.json`, and conditional `successor-document.json`
- `05-revision-orchestrator/attempt-02/revision-selection.json`, `effective-document.json`, `effective-bundle-receipt.json`, and conditional `successor-bundle/` plus `successor-bundle-receipt.json`
- `06-audit/attempt-02/classification-summary.json`, `case-inventory-review.json`, `case-inventory-summary.json`, `execution-notes.md`

The context completeness review checks every deterministic domain key. For exclusions it reviews all rows when a domain has at most 25. Otherwise it takes the first 10 and last 10 in inventory order, removes those IDs from the remainder, ranks remaining rows by lowercase SHA-256 of `<receipt-sha256>:<source-id>`, and takes the first five distinct rows. Category alone never justifies exclusion. The classifier reviewer covers all 233 pairs and must return `ПРИНЯТО`. The tc-reviewer reviews the candidate once: `ПРИНЯТО` keeps revision 1; `AUTO_FIX_APPLIED` requires one valid successor and existing revision selection; `ТРЕБУЕТ ДОРАБОТКИ` stops. The context completeness and case-inventory reviews must return `ACCEPT`. The case-inventory review checks every domain key, all scopes, duplicate meaning, assertion boundaries, and every unknown scope. Any missing/duplicate/pending disposition or disallowed semantic verdict stops the run.
