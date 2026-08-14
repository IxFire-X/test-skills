# Change-Scoped Semantic Evidence Design

Date: 2026-08-14

## Problem

The pipeline removed raw `source_code_and_diff` from downstream generation to prevent source files and test symbols from multiplying test cases. That isolation was correct, but it left no first-class replacement for feature-change scope. The current full-project behavior accounting can prove that every authorized byte range was processed, yet cannot answer which existing behaviors changed relative to a trusted baseline.

The InvenTree calibration exposed a second gap. Batch candidates became authoritative before semantic review:

- attempt-02 wrote mechanically valid but false HTTP routes for model, plugin, filesystem, and template path helpers;
- attempt-03 fixed route recognition, then wrote a false `no_supported_observable_fact` for `src/frontend/README.md`, which states a public plugin UI contract. Path-category treatment also threatened deployment configuration such as `netlify.toml`.

Deterministic validation caught neither error because shape, digest, byte range, and meaning are different properties. Discovering the error after hundreds of create-only results forced the whole attempt to stop.

The replacement is one evidence flow with two controls:

1. establish a complete immutable baseline, then compute a reviewed semantic scope for later change sets;
2. keep every extraction result a candidate until separate false-claim and omission audits both accept it.

Only promoted semantic evidence can enter context reduction and test generation.

## Decisions

### One trivial external façade

The common caller uses one deep `feature_flow` module and does not configure modes, artifact counts, reviewer counts, shards, or case counts:

```python
advance_feature_flow(
    project,
    analytics,
    run_root,
    baseline_receipt=None,
    change_input=None,
    recorded_artifact=None,
    controller=None,
) -> FeatureFlowAction
```

The CLI mirrors it:

```text
python tools/test_classification.py feature-flow \
  --project <root> \
  --analytics <file> \
  --run-root <create-only-directory> \
  [--baseline-receipt <receipt.json>] \
  [--base <git-object>] [--head <git-object>] \
  [--worktree] \
  [--patch-manifest <closed.json>] \
  [--record <candidate-or-audit.json>]
```

`.skillsrc` is discovered automatically and remains unchanged. Exactly one module must be selected from direct path, symbol, route, or supplied requirement evidence. Multiple plausible modules block rather than guess.

The façade validates inputs, derives state entirely from immutable readback, and returns one closed action:

```text
RUN_FULL_BASELINE
PRODUCE_CHANGE_SCOPE
RUN_SCOPE_FALSE_INCLUSION_AUDIT
RUN_SCOPE_OMISSION_AUDIT
PRODUCE_BATCH_CANDIDATE
RUN_BATCH_FALSE_CLAIM_AUDIT
RUN_BATCH_OMISSION_AUDIT
PROMOTE_BATCH
BUILD_CONTEXT
GENERATE_CHANGED_BEHAVIOR
COMPLETE
BLOCKED
```

The caller performs the requested LLM task, submits the closed artifact with `--record`, and calls the same command again. `feature_flow` owns mode selection, path derivation, ordering, retries, resume, assurance, and receipt construction.

### Two internal deep modules

`baseline_lifecycle` owns baseline validation and the only conversion of full predecessor evidence into a scope capability. `change_scope` owns Git/patch acquisition, metadata comparison, impact closure, scope review, and a promoted scope receipt:

```python
start_scope(inputs) -> ScopeSnapshot
record_scope(snapshot, candidate_or_audit) -> ScopeSnapshot
advance_scope(snapshot, controller=None) -> ScopeAction
```

`batch_promotion` owns generation chains and promoted semantic batch results:

```python
start_promotion(scope_receipt, plan) -> PromotionSnapshot
record_promotion(snapshot, candidate_or_audit) -> PromotionSnapshot
advance_promotion(snapshot, controller=None) -> PromotionAction
```

These are internal interfaces used only by the façade and focused tests. Their implementations may share the existing source inventory, planning, current-byte, stable-fragment, and validated-context helpers. No orchestrator may reconstruct their joins.

### Validated `ScopePredecessor` capability

`baseline_lifecycle` exposes one internal factory:

```python
bind_scope_predecessor(
    baseline: ValidatedBaseline,
    predecessor_source_inventory_envelope,
    predecessor_context_v5_envelope,
    predecessor_behavior_context_receipt,
) -> ScopePredecessor
```

The factory is called by `feature_flow` after it has read immutable predecessor artifacts. It first validates the exact full source-inventory envelope, the exact V5 context-marker envelope, and the exact behavior-context receipt against the artifact digests carried by the already validated Baseline Receipt. The binding chain is exact: `ValidatedBaseline.receipt_sha256` identifies the Baseline Receipt; its `authorized_behavior_sources_sha256`, `managed_behavior_context_sha256`, `behavior_source_accounting_sha256`, and `behavior_context_receipt_sha256` identify the full predecessor carriers; the source envelope's internal authorized-source digest, both V5 context projections' authorized-source digests, the accounting receipt digest, and the receipt's authorized-source digest must agree. The full source envelope and V5 context envelope each satisfy their closed local schema before any projection is made.

The factory then reuses the baseline module's pure stored-context relation validator; it does not copy a second partial validator into `change_scope`. That validator must prove the complete V5 joins for authorized source order and uniqueness, requirement display order and source links, product projections, represented and no-fact dispositions, fragment registry ownership, fragment-group coverage, and receipt source outcomes. It returns a recursively immutable, opaque `ScopePredecessor` containing only:

- validated baseline identity: baseline receipt digest, repository identity, target commit, target tree, and selected module;
- the ordered authorized source identity slots required for before-side joins (`source_id`, kind, canonical path, and content digest); and
- ordered baseline requirement IDs.

The capability has no public constructor, serializer, JSON projection, digest, or deserialization route. Its concrete type and any trust guard are private to `baseline_lifecycle`; only the factory can create it. It contains no raw source bytes, raw diff, full envelope, V5 receipt, fragment, group, outcome, planner item, prompt, or reasoning. `change_scope` may consume its internal read-only slots but may neither recover nor request the discarded carriers.

`ScopeInputs` is exactly:

```python
ScopeInputs(
    project_root, run_mode, selected_module, analytics_sha256, change_input,
    current_source_inventory, current_test_inventory, predecessor, relations=(),
)
```

It has no `baseline`, `baseline_source_inventory_envelope`, or `baseline_context_envelope` field. `FULL` requires `predecessor is None` and no change input. `CHANGE_SET` requires an exact `ScopePredecessor`; its selected module and repository must equal the inputs, and its target commit/tree and derived base snapshot must equal the closed change input's base. A foreign object, a forged look-alike, a missing capability, or any mismatch is rejected before candidate creation. Scope snapshots and candidates retain only safe identity metadata, public change-record metadata/digests, inventories, relation IDs/locators, source IDs, and requirement IDs; they never retain raw source/diff bytes, predecessor envelopes, receipts, fragments, groups, outcomes, or planner content.

Failure to validate or bind a predecessor is `BASELINE_BINDING` internally. At the façade mode-selection boundary it is treated exactly as an unavailable compatible baseline and returns `RUN_FULL_BASELINE`; it never starts a partial `CHANGE_SET`. This introduces no catalog, version registry, persisted capability, or new artifact family.

## Run modes

### Mandatory initial `FULL`

No exact compatible baseline means a full run. The initial run always:

1. resolves the selected module from `.skillsrc`;
2. binds the exact Git tree when available and the exact current file digests;
3. creates the complete authorized behavior-source inventory and complete technical test inventory;
4. plans every authorized source range;
5. promotes every semantic batch;
6. builds the full managed behavior context and a terminal run receipt;
7. advances a durable baseline only when the target is an exact committed Git tree and every terminal acceptance gate passes.

A supplied diff on the first run is recorded as an input digest but never narrows the baseline. For an initial Git `FULL`, the durable target is exactly the clean committed `HEAD` commit and tree. A dirty Git worktree may run only as a frozen content-snapshot `FULL`; it is explicitly `PROVISIONAL` and cannot advance a durable baseline. A non-Git project may likewise run only as a provisional full content-digest snapshot and cannot claim Git identity or become an incremental predecessor.

### Subsequent `CHANGE_SET`

Incremental execution requires an exact Pipeline 6 baseline and a `ScopePredecessor` minted from its full source, V5 context, and behavior-context receipt evidence. Module, `.skillsrc` identity, source-inventory contract, repository identity, base tree, full context, and receipt digests must all validate. Otherwise the façade automatically returns `RUN_FULL_BASELINE`.

A change set is one closed variant:

- `git_range`: exact repository identity, base commit/tree and head commit/tree;
- `git_worktree`: exact base commit/tree plus a frozen snapshot covering staged, unstaged, and untracked files;
- `patch_manifest`: controller-supplied closed before/after manifest with exact content digests and optional rename relations.

Only `git_range` targets a durable successor baseline. Its base commit/tree must equal the predecessor baseline target exactly, and its head must be a committed tree in the same repository. `git_worktree` and `patch_manifest` runs are always provisional, even when all semantic and test gates accept.

For a worktree snapshot, staged, unstaged, and untracked content is frozen before semantic work. Later filesystem changes produce source drift; they are never silently absorbed. Ignored files remain outside scope unless already authorized by `.skillsrc` and explicitly present in the supplied manifest.

The diff is a seed, not the semantic boundary. The implementation may cheaply rebuild a full metadata registry of paths, symbols, imports, route/config registrations, source/test ownership, and content digests. The LLM reads only directly changed evidence and the mechanically derived impact closure.

### Automatic widening

Ambiguity widens conservatively in this fixed order:

```text
symbol -> file -> deterministic domain -> selected module -> FULL refresh
```

Widen when rename identity, binary meaning, deletion impact, cross-file binding, generated/config behavior, or dependency closure cannot be proved at the narrower level. Missing, stale, foreign, incompatible, or partially readable baseline evidence immediately selects `FULL`; the user is not asked to tune scope.

## Change scope

`change_scope` accepts only the capability above for predecessor authority. It does not accept or read predecessor source/context/receipt envelopes, behavior fragments, accounting groups, source outcomes, or planner data. Its before-side joins use the capability's ordered slots; its candidate and receipt bind the capability's baseline receipt digest, never an independently supplied predecessor digest.

### Mechanical change records

Every changed path has one closed kind: `added`, `modified`, `deleted`, `renamed`, or `binary`. A rename carries old and new paths and before/after digests; similarity is evidence only when the underlying Git adapter supplies it. Binary rows carry digests and size, never raw bytes. Text rows bind before and after digests and locator ranges; raw diff text is not persisted.

The closed `change-record` V1 union is:

```text
added    = {change_id, kind:"added", path, after:{source_id,content_sha256,size_bytes,text}}
modified = {change_id, kind:"modified", path,
            before:{source_id,content_sha256,size_bytes,text},
            after:{source_id,content_sha256,size_bytes,text}}
deleted  = {change_id, kind:"deleted", path, before:{source_id,content_sha256,size_bytes,text}}
renamed  = {change_id, kind:"renamed", old_path, new_path, similarity_basis,
            before:{source_id,content_sha256,size_bytes,text},
            after:{source_id,content_sha256,size_bytes,text}}
binary   = {change_id, kind:"binary", path, binary_change:"added|modified|deleted",
            before:null|{source_id,content_sha256,size_bytes,text:false},
            after:null|{source_id,content_sha256,size_bytes,text:false}}
```

Every object is closed. `text` is `true` on the four text variants. Added requires only `after`; deleted only `before`; modified and renamed require both; binary-added requires only `after`, binary-deleted only `before`, and binary-modified both. `change_id` is the deterministic hash of the canonical closed row excluding `change_id`. Product `source_id` on each side is recomputed from that side's authoritative inventory identity; it is never copied across a rename unless the inventory contract itself proves identity preservation.

The supplied `patch-manifest` V1 is also closed:

```json
{
  "schema_version": "1.0.0",
  "artifact": "patch-manifest",
  "repository_id": "sha256:...",
  "base_snapshot_sha256": "sha256:...",
  "target_snapshot_sha256": "sha256:...",
  "changes": [],
  "content_blobs": [
    {
      "content_sha256": "sha256:...",
      "size_bytes": 123,
      "controller_blob_id": "BLOB-..."
    }
  ]
}
```

`changes` contains the exact union above in canonical order. `content_blobs` binds controller-only byte inputs by digest and length; paths and raw content are not stored there. Every required before/after digest has exactly one readable blob or an exact repository-tree object; every foreign, duplicate, missing, or mismatched blob blocks. `base_snapshot_sha256` must equal the predecessor snapshot for a provisional patch run, but a patch manifest never advances the durable Git baseline.

The candidate scope contains typed inclusion reasons:

```text
DIRECT_TEXT_CHANGE
DIRECT_BINARY_CHANGE
ADDED_SOURCE
DELETED_SOURCE
RENAMED_SOURCE
SYMBOL_RELATION
IMPORT_RELATION
ROUTE_BINDING_RELATION
CONFIG_RUNTIME_RELATION
REQUIREMENT_SOURCE_RELATION
TEST_EVIDENCE_RELATION
DOMAIN_WIDENING
MODULE_WIDENING
FULL_REFRESH
```

Each indirect inclusion cites at least one closed relation:

```json
{
  "relation_kind": "IMPORT_RELATION",
  "from_source_id": "SOURCE-...",
  "to_source_id": "SOURCE-...",
  "evidence_locator": {
    "content_sha256": "sha256:...",
    "start_byte": 120,
    "end_byte": 148
  }
}
```

Relations express why evidence must be read; they do not assert that behavior changed.

### Change-aware behavior plan and result

The V1 behavior plan/result protocol represents only the complete current-side source snapshot of a `FULL` run. Every `CHANGE_SET` source, including an added source, uses the closed change-aware Plan V2 and Batch Result V2; a deleted, before-side, modified, or renamed row can never be represented by V1.

Each Plan V2 item contains:

```json
{
  "item_id": "ITEM-000001",
  "change_id": "CHANGE-...",
  "change_kind": "modified",
  "baseline_source_id": "SOURCE-...",
  "current_source_id": "SOURCE-...",
  "domain_key": "backend/domain",
  "evidence_sides": [
    {
      "side": "before",
      "content_sha256": "sha256:...",
      "accounted_range": {"start": 0, "end": 100},
      "read_range": {"start": 0, "end": 116}
    },
    {
      "side": "after",
      "content_sha256": "sha256:...",
      "accounted_range": {"start": 0, "end": 104},
      "read_range": {"start": 0, "end": 120}
    }
  ]
}
```

The change-aware planner receives the reviewed scope candidate explicitly:
`build_change_context_plan(project, selected_module, scope_receipt, scope_candidate,
baseline_inventory, current_inventory, byte_resolver)`. It schema-validates the
candidate and requires `artifact_sha256(scope_candidate) ==
scope_receipt.candidate_sha256`; the receipt remains the promotion binding and is
not enlarged with a duplicate change list. `project` and `selected_module` are
required to reproduce the canonical `domain_key`; `byte_resolver` supplies bytes
only after side identity and digest checks.

Relation-expanded or widened sources that are not themselves change rows use a
closed `change_kind: "context"` item with `change_id: null`. Context items carry
only the authenticated side identities and ranges needed as supporting evidence;
their results may emit `supporting_context` observations, never changed
fragments or deletion tombstones. A source reached by several changes remains one
context item with the candidate's ordered relation evidence; no arbitrary trigger
change is invented.

Added requires null `baseline_source_id` and only `after`; deleted requires null `current_source_id` and only `before`; modified requires both sides and both identities; renamed requires both sides, both identities, and the exact promoted rename `change_id`; binary uses the sides required by its `binary_change`; context uses whichever authenticated side(s) exist and `change_id: null`. A logical item may contain multiple ordered `evidence_sides` rows for one side, so unequal before/after chunk counts are represented without zipping, duplication, or omission. Ranges cover each selected side exactly, with the existing UTF-8 overlap rules for text. Binary items expose bounded metadata and content digests to scope review but no synthetic text ranges; unresolved binary semantics widen or block.

Plan order is deterministic: rows occupying a baseline slot (`modified`, `deleted`, `renamed`) retain baseline inventory order; surviving current-only `added` rows follow in current inventory order. Split items preserve side, source, and byte-range order. No source or side may be silently duplicated or omitted.

Batch Result V2 binds `schema_version:"2.0.0"`, exact scope receipt, plan, batch, item order, and one result per item. Its terminal outcome is `changed_behavior_fragments`, `deleted_behavior_tombstones`, `supporting_context`, or `no_changed_observable_fact`. Context results are non-promoting observations only. Each changed fragment has `effect: added|modified|retired` and one or more evidence locators of the closed form `{side:"before|after", content_sha256, start_byte, end_byte}`. Added effects require `after`; retired effects require `before`; modified effects require at least one locator on each side. Locators must match the item's exact side digest and `read_range`; fragment ownership uses the anchor on the effect's authoritative side (`after` for added/modified, `before` for retired). Fragment IDs hash effect, both source identities, owning item, and all locators.

A deleted source with supported baseline behavior emits promoted retired tombstones; it never fabricates current bytes. A rename or modification with no semantic change emits `no_changed_observable_fact` after comparing both sides. Current-byte validation rereads every `after` side from the exact target tree/snapshot; `before` bytes come only from the predecessor committed tree or frozen manifest digest. Receipt composition validates both again before applying tombstones or replacements.

### Scope candidate V1

```json
{
  "schema_version": "1.0.0",
  "artifact": "change-scope-candidate",
  "run_mode": "CHANGE_SET",
  "selected_module": "root",
  "baseline_receipt_sha256": "sha256:...",
  "analytics_sha256": "sha256:...",
  "change_input_sha256": "sha256:...",
  "current_source_inventory_sha256": "sha256:...",
  "current_test_inventory_sha256": "sha256:...",
  "changes": [],
  "included_sources": [
    {
      "source_id": "SOURCE-...",
      "reason": "DIRECT_TEXT_CHANGE",
      "relation_ids": []
    }
  ],
  "included_test_symbols": [],
  "relations": [],
  "baseline_requirement_ids": [],
  "widening_level": "file"
}
```

All arrays are canonically ordered and exact. `included_test_symbols` always comes from a freshly rebuilt complete technical test inventory. It identifies impacted technical evidence only. Existing or changed tests never originate product requirements or canonical cases. The candidate is a public safe projection: it may contain IDs, content digests, paths, locators, reasons, and change metadata, but never raw source/diff bytes, full predecessor carriers, V5 receipt content, fragment/group/outcome data, planner items, prompts, responses, or reasoning.

### Scope semantic audits

Before scope promotion, two separate audits receive the exact candidate, authorized before/after evidence locators, baseline links, and current metadata registry:

- `false_inclusion` rejects unrelated files, lexical coincidences, test-only origin, and needless widening;
- `omission` rejects missed transitive behavior, deletion/rename effects, README or public contract changes, runtime configuration, route bindings, and incomplete impact closure.

Both must return `ACCEPT`. Any `REWORK` creates a new immutable scope generation. A majority, later audit, or successful downstream batch cannot rehabilitate a rejected generation.

### Scope audit V1

```json
{
  "schema_version": "1.0.0",
  "artifact": "change-scope-audit",
  "candidate_sha256": "sha256:...",
  "audit_kind": "omission",
  "verdict": "REWORK",
  "findings": [
    {
      "finding_id": "FINDING-<64 lowercase hex>",
      "code": "IMPACT_SOURCE_OMITTED",
      "candidate_pointer": "/included_sources",
      "source_id": "SOURCE-...",
      "evidence_locator": {
        "content_sha256": "sha256:...",
        "start_byte": 0,
        "end_byte": 64
      },
      "summary": "A public configuration binding is affected."
    }
  ]
}
```

`ACCEPT` requires no findings; `REWORK` requires at least one. Finding codes are closed by kind. Summaries are short and safe; they do not quote source.

### Scope receipt V1

Promotion mechanically emits:

```json
{
  "schema_version": "1.0.0",
  "artifact": "change-scope-receipt",
  "run_mode": "CHANGE_SET",
  "baseline_receipt_sha256": "sha256:...",
  "candidate_sha256": "sha256:...",
  "audit_sha256s": {
    "false_inclusion": "sha256:...",
    "omission": "sha256:..."
  },
  "change_input_sha256": "sha256:...",
  "analytics_sha256": "sha256:...",
  "included_source_ids": [],
  "included_test_symbol_pairs": [],
  "review_mode": "SEQUENTIAL",
  "independence_attestation_sha256": null
}
```

For `FULL`, the same shape uses `baseline_receipt_sha256: null`, `run_mode: FULL`, includes every authorized source, and records `FULL_REFRESH` as the governing reason.

## Semantic batch promotion

Each scoped planned batch follows exactly:

```text
candidate -> false-claim audit -> omission audit -> atomic promotion
```

The closed candidate envelope binds run mode, scope receipt, plan, batch, generation, parent candidate, triggering `REWORK` audits, and exactly one nested result:

```json
{
  "schema_version": "1.0.0",
  "artifact": "semantic-batch-candidate",
  "run_mode": "CHANGE_SET",
  "scope_receipt_sha256": "sha256:...",
  "plan_sha256": "sha256:...",
  "batch_id": "BATCH-000001",
  "generation": 1,
  "parent_candidate_sha256": null,
  "triggering_audit_sha256s": [],
  "result_schema_version": "2.0.0",
  "result": {}
}
```

`run_mode:"FULL"` requires `result_schema_version:"1.0.0"` and `result` must satisfy exactly Batch Result V1. `run_mode:"CHANGE_SET"` requires `result_schema_version:"2.0.0"` and `result` must satisfy exactly change-aware Batch Result V2. Cross-mode payloads, a mismatched declared/embedded version, or a hybrid object fail before audit. Generation 1 has no parent. Later generations may descend only from the immediately preceding rejected generation and must retain the same run mode and result version.

The false-claim audit checks unsupported or inflated actor, operation, condition, outcome, evidence, and route claims. A route still requires framework provenance, registration sink, and bound target. The omission audit independently checks material supported facts lost by the candidate, especially `no_supported_observable_fact`, deletions, boundary-spanning facts, README contracts, and runtime configuration.

Each audit reads exact authorized source ranges plus the candidate, but not producer reasoning, peer-audit output, or previous-generation audit output. A `REWORK` seals the generation and requests the next candidate for only that batch. Both exact audits must accept the same candidate generation before promotion.

### Promotion V1

```json
{
  "schema_version": "1.0.0",
  "artifact": "semantic-batch-promotion",
  "run_mode": "CHANGE_SET",
  "scope_receipt_sha256": "sha256:...",
  "plan_sha256": "sha256:...",
  "batch_id": "BATCH-000001",
  "generation": 1,
  "candidate_sha256": "sha256:...",
  "result_schema_version": "2.0.0",
  "result_sha256": "sha256:...",
  "audit_sha256s": {
    "false_claim": "sha256:...",
    "omission": "sha256:..."
  },
  "review_mode": "SEQUENTIAL",
  "independence_attestation_sha256": null
}
```

Promotion revalidates the candidate's exact mode/result conjunction and binds the canonical nested result digest. Receipt construction revalidates it again: a FULL receipt accepts only promotions of V1 results, and a CHANGE_SET receipt accepts only promotions of V2 results. Direct or loose authoritative writes of either V1 or V2 results are forbidden; neither result schema is itself authority without the candidate, two accepting audits, and promotion.

## Review assurance

The mandatory portable baseline is `SEQUENTIAL`. One ordinary LLM may perform producer and audit roles in the same conversation, but as three separate action prompts. The two audits remain blind to each other's artifacts. This reduces error without pretending contextual independence.

When a host supports fresh isolated contexts, the same interfaces and artifacts may automatically report `INDEPENDENT`. A trusted controller adapter must attest distinct producer, false-claim reviewer, and omission reviewer execution/context IDs; exact candidate/audit digests; freshness; and the blind input policy. Reviewers must also be distinct from each other.

The module accepts that proof only as an unforgeable in-memory controller capability. JSON, CLI flags, prompts, environment variables, agent names, or copied attestation fields cannot request `INDEPENDENT`. Without valid attestation, mode is `SEQUENTIAL`; an explicit fabricated claim is rejected.

No user setting chooses assurance mode.

## Context composition and generation

The baseline receipt contains the complete promoted source outcome and fragment registry. A change-set receipt is composite:

```json
{
  "schema_version": "2.0.0",
  "artifact": "behavior-context-receipt",
  "run_mode": "CHANGE_SET",
  "selected_module": "root",
  "baseline_receipt_sha256": "sha256:...",
  "scope_receipt_sha256": "sha256:...",
  "authorized_behavior_sources_sha256": "sha256:...",
  "context_plan_sha256": "sha256:...",
  "promotion_sha256s": [],
  "unchanged_source_bindings": [],
  "fragment_registry": [],
  "source_outcomes": [],
  "changed_requirement_ids": [],
  "retired_requirements": [],
  "assurance": {
    "review_mode": "SEQUENTIAL",
    "independent_promotion_count": 0,
    "sequential_promotion_count": 1,
    "reworked_batch_count": 0
  }
}
```

`unchanged_source_bindings` proves exact baseline/current content-digest equality. Added and changed sources derive only from promoted current candidates. Deleted sources retain baseline evidence as tombstones until semantic review proves which requirement links survive elsewhere. A rename is not automatically a delete-plus-add behavior change.

Every `retired_requirements` row is the exact closed requirement tombstone later used by the document delta: `{object_kind:"requirement", object_id, baseline_object_sha256, reason, support_evidence}`. Its support evidence must prove `NO_SURVIVING_SOURCE_SUPPORT`, list zero surviving source IDs, and bind one or more promoted retirement fragment IDs. `changed_behavior_context` copies those rows byte-for-byte. The composed full managed context preserves IDs for semantically unchanged requirements. A requirement is retired only when promoted current evidence and reviewed deletion impact prove it no longer exists; removal of one supporting file is insufficient when another source still supports it.

The generator receives only a validated `changed_behavior_context` projection containing new and changed requirements, digest-bound requirement tombstones, and necessary stable context links. It never receives raw project source, raw diff, analytics body, source/test inventory, scope ledger, promotion ledger, technical test text, or accounting sidecar. An empty semantic delta generates zero new cases.

### Canonical Document Delta V1

On `FULL`, `tc-generator` emits a complete `candidate_document` under the existing canonical document schema. On `CHANGE_SET`, it emits only a closed `canonical-document-delta` V1:

```json
{
  "schema_version": "1.0.0",
  "artifact": "canonical-document-delta",
  "document_id": "TCDOC-inventree",
  "baseline_document_sha256": "sha256:...",
  "baseline_revision": 4,
  "target_revision": 5,
  "source": {
    "behavior_context_receipt_sha256": "sha256:...",
    "changed_behavior_context_sha256": "sha256:..."
  },
  "metadata": {},
  "operation_capabilities": {
    "reused_ids": [],
    "added": [],
    "modified": [],
    "retired": [
      {
        "object_kind": "operation_capability",
        "object_id": "CAP-retired",
        "baseline_object_sha256": "sha256:...",
        "reason": "CAPABILITY_NO_LONGER_REFERENCED",
        "support_evidence": {
          "status": "DEPENDENTS_RETIRED",
          "surviving_object_ids": []
        }
      }
    ]
  },
  "requirements": {
    "reused_ids": [],
    "added": [],
    "modified": [],
    "retired": [
      {
        "object_kind": "requirement",
        "object_id": "REQ-retired",
        "baseline_object_sha256": "sha256:...",
        "reason": "REQUIREMENT_NO_LONGER_OBSERVABLE",
        "support_evidence": {
          "status": "NO_SURVIVING_SOURCE_SUPPORT",
          "surviving_source_ids": [],
          "retirement_fragment_ids": ["FRAGMENT-..."]
        }
      }
    ]
  },
  "test_cases": {
    "reused_ids": [],
    "added": [],
    "modified": [
      {
        "case_id": "TC-existing",
        "baseline_case_sha256": "sha256:...",
        "replacement": {}
      }
    ],
    "retired": [
      {
        "object_kind": "test_case",
        "object_id": "TC-retired",
        "baseline_object_sha256": "sha256:...",
        "reason": "CASE_REQUIREMENTS_RETIRED",
        "support_evidence": {
          "status": "DEPENDENTS_RETIRED",
          "surviving_object_ids": []
        }
      }
    ]
  }
}
```

`metadata` is the complete target canonical metadata. `added` entries are complete canonical objects. `modified` capability and requirement entries use the same `{id, baseline_object_sha256, replacement}` shape as cases, with the appropriate ID name; every replacement is a complete canonical object with the same identity.

Every retirement in all three collections is a closed tombstone row with `object_kind`, `object_id`, exact canonical baseline-object digest, a reason closed for that kind, and closed support evidence. Capability reasons are `CAPABILITY_NO_LONGER_REFERENCED|CAPABILITY_REPLACED`; requirement reasons are `REQUIREMENT_NO_LONGER_OBSERVABLE|REQUIREMENT_MERGED`; case reasons are `CASE_REQUIREMENTS_RETIRED|CASE_MERGED|CASE_NO_LONGER_OBSERVABLE`. Requirements alone require `{status:"NO_SURVIVING_SOURCE_SUPPORT", surviving_source_ids:[], retirement_fragment_ids:[...]}` with at least one promoted retirement fragment. A requirement with any surviving current support cannot be retired. Capabilities and cases require `{status:"DEPENDENTS_RETIRED", surviving_object_ids:[]}`; any surviving target reference blocks retirement. No bare retirement ID is valid in the behavior receipt, changed context, or document delta.

For each collection, every baseline identity appears exactly once across `reused`, `modified`, and tombstone-backed `retired`; every added identity is absent from baseline; sets are disjoint; no foreign, duplicate, colliding, or missing identity is permitted. The applier recomputes every tombstone's baseline object digest, reason/support compatibility, retirement fragment binding, and absence of surviving references before removal. Reused objects are copied byte-for-byte. Added and replacement objects are schema-valid and reference only target requirements/capabilities. Physical output order is canonical: retained baseline objects keep baseline relative order with replacements in their original slots; tombstoned objects disappear; added objects follow in their delta order after deterministic scenario-key/requirement ordering. The applier then rewrites every `display_order` to the resulting physical order and validates all cross-references.

The deterministic seam is:

```python
apply_document_delta(
    baseline_document,
    delta,
    behavior_context_receipt,
    changed_behavior_context,
) -> AppliedDocumentDelta
```

`AppliedDocumentDelta` is closed: `{status:"CHANGED|UNCHANGED", candidate_document, publication_required, delta_sha256, baseline_document_sha256}`. It verifies exact document ID, baseline bytes/digest/revision, both source digests, collection partitions, object digests, identities, ordering, and the complete canonical schema.

For a semantic change, `target_revision` must equal `baseline_revision + 1`; the complete result keeps `document_id`, sets `revision` to the target, and sets `parent_sha256` to the exact baseline document digest. `publication_required` is true.

A zero-op delta has identical metadata, every baseline identity in `reused_ids`, and empty added/modified/retired arrays. It must set `target_revision` equal to `baseline_revision`. The applier returns the exact baseline document bytes, `status:"UNCHANGED"`, and `publication_required:false`; no bundle, review, correction, or new revision is published. The validated baseline effective document and bundle continue into the tail through `select-unchanged-document`.

For nonzero deltas, `publish-candidate` receives only the complete materialized candidate. `tc-reviewer` reviews that complete document, never a partial delta. `ПРИНЯТО` retains it; `AUTO_FIX_APPLIED` must emit one complete canonical successor with revision `candidate.revision + 1` and parent equal to the candidate digest; `ТРЕБУЕТ ДОРАБОТКИ` stops. Corrections never edit or replace the delta; the revision orchestrator selects exactly one complete candidate or successor as today. The immutable delta/application receipt remains origin evidence for baseline advancement.

## Durable baseline advancement

`finalize-orchestration` emits a closed terminal run receipt binding every exact artifact used for acceptance. `advance-baseline` may create a successor only for:

- an initial clean committed-Git `FULL`; or
- a committed `git_range` whose base commit/tree exactly equals the predecessor baseline target and whose head is the resulting committed target tree.

The run must have accepted classification, accepted or valid auto-fixed test-case review, accepted automation review, trace `PASS`, a final orchestration artifact, and overall status `PASS`, `PASS_WITH_MANUAL_REMAINDER`, or `MANUAL_ONLY`. `FAIL`, `NOT_RUNNABLE`, `BLOCKED`, any `REWORK`, incomplete audit, missing artifact, provisional worktree/patch/non-Git snapshot, or source drift can never advance a durable baseline.

The closed Baseline Receipt V1 is:

```json
{
  "schema_version": "1.0.0",
  "artifact": "feature-baseline-receipt",
  "predecessor_baseline_sha256": null,
  "run_mode": "FULL",
  "repository_id": "sha256:...",
  "target_commit": "<40-or-64 lowercase hex>",
  "target_tree": "<Git tree object id>",
  "selected_module": "root",
  "analytics_sha256": "sha256:...",
  "artifacts": {
    "technical_test_inventory_sha256": "sha256:...",
    "authorized_behavior_sources_sha256": "sha256:...",
    "managed_behavior_context_sha256": "sha256:...",
    "behavior_source_accounting_sha256": "sha256:...",
    "behavior_context_receipt_sha256": "sha256:...",
    "effective_technical_evidence_sha256": "sha256:...",
    "effective_document_sha256": "sha256:...",
    "effective_bundle_receipt_sha256": "sha256:...",
    "trace_document_sha256": "sha256:...",
    "trace_audit_sha256": "sha256:...",
    "orchestrator_output_sha256": "sha256:...",
    "terminal_run_receipt_sha256": "sha256:..."
  },
  "fingerprints": {
    "pipeline_contract_sha256": "sha256:...",
    "policy_bundle_sha256": "sha256:...",
    "tool_bundle_sha256": "sha256:...",
    "schema_bundle_sha256": "sha256:..."
  }
}
```

A successor `CHANGE_SET` baseline requires nonnull predecessor digest and target equal to the accepted git-range head. Tool, policy, schema, or pipeline fingerprint incompatibility forces a new `FULL`; fingerprints are digests of closed ordered file/digest registries, not version labels alone.

Advancement writes the content-addressed receipt and a create-only predecessor link, flushes, reopens, and verifies both. At most one distinct successor may occupy a predecessor link. Concurrent identical advancement is idempotent; competing target trees return `BASELINE_CONFLICT` and neither becomes implicit latest. The next incremental base must name the unique validated successor and its base must equal that receipt's target commit/tree. No mutable latest pointer exists; an ambiguous or broken chain forces `FULL`.

## Pipeline 6.0

This trust and topology change is Pipeline `6.0`. Context Marker is `6.0.0`; behavior context receipt is `2.0.0`; the mode-aware `tc-generator` envelope is `4.0.0`; change scope, delta, promotion, terminal-run, and baseline artifacts begin at `1.0.0`. The bare canonical document and existing reviewer/tail artifact versions remain unchanged. `.skillsrc` stays version 3.0 and its schema and generated contents do not change.

The closed Pipeline 6 carrier universe `U` is, in order:

```text
raw_content, technical_test_inventory, authorized_behavior_sources,
change_scope_receipt, managed_behavior_context, changed_behavior_context,
behavior_source_accounting, behavior_context_receipt,
technical_test_classification, classification_review,
effective_technical_evidence, canonical_document_delta,
delta_application_receipt, unchanged_document_selection,
candidate_document, candidate_bundle_receipt, validation_report,
successor_document, successor_bundle_receipt, effective_document,
effective_bundle_receipt, automation_artifact, autotest_review, run_result,
trace_document, trace_audit, orchestrator_output, terminal_run_receipt,
baseline_advancement, successor_baseline_receipt
```

For every row below, arrays are exact and ordered. `rejects` is normatively the ordered complement `U - accepts`; Pipeline schema/checker materializes and verifies that exact array. Thus an omitted carrier is rejected, not implicitly tolerated.

| Stage | accepts | forwards | produces |
|---|---|---|---|
| `source-inventory` | `[raw_content]` | `[raw_content]` | `[technical_test_inventory, authorized_behavior_sources]` |
| `change-scope` | `[raw_content, technical_test_inventory, authorized_behavior_sources]` | `[raw_content, technical_test_inventory, authorized_behavior_sources]` | `[change_scope_receipt]` |
| `context-marker` | `[raw_content, technical_test_inventory, authorized_behavior_sources, change_scope_receipt]` | `[technical_test_inventory, authorized_behavior_sources, change_scope_receipt]` | `[managed_behavior_context, changed_behavior_context, behavior_source_accounting, behavior_context_receipt]` |
| `test-classifier` | `[technical_test_inventory, authorized_behavior_sources, change_scope_receipt, managed_behavior_context, changed_behavior_context, behavior_source_accounting, behavior_context_receipt]` | `[technical_test_inventory, authorized_behavior_sources, change_scope_receipt, managed_behavior_context, changed_behavior_context, behavior_source_accounting, behavior_context_receipt]` | `[technical_test_classification]` |
| `test-classifier-reviewer` | `[technical_test_inventory, authorized_behavior_sources, change_scope_receipt, managed_behavior_context, changed_behavior_context, behavior_source_accounting, behavior_context_receipt, technical_test_classification]` | `[changed_behavior_context]` | `[classification_review, effective_technical_evidence]` |
| `tc-generator` | `[changed_behavior_context]` | `[changed_behavior_context]` | FULL: `[candidate_document]`; CHANGE_SET: `[canonical_document_delta]` |
| `apply-document-delta` | `[changed_behavior_context, canonical_document_delta]` | `[]` | changed: `[delta_application_receipt, candidate_document]`; zero-op: `[delta_application_receipt, unchanged_document_selection]` |
| `select-unchanged-document` | `[delta_application_receipt, unchanged_document_selection]` | `[]` | `[effective_document, effective_bundle_receipt]` loaded and verified from the bound predecessor baseline |
| `publish-candidate` | `[candidate_document]` | `[candidate_document]` | `[candidate_bundle_receipt]` |
| `tc-reviewer` | `[candidate_document]` | `[candidate_document]` | `[validation_report, successor_document]` |
| `revision-orchestrator` | `[candidate_document, candidate_bundle_receipt, validation_report, successor_document]` | `[candidate_bundle_receipt, validation_report]` | `[successor_bundle_receipt, effective_document, effective_bundle_receipt]` |
| `tc-to-autotest` | `[effective_document, effective_bundle_receipt]` | `[effective_document, effective_bundle_receipt]` | `[automation_artifact]` |
| `autotest-reviewer` | `[effective_document, automation_artifact]` | `[effective_document, automation_artifact]` | `[autotest_review]` |
| `run-tests` | `[effective_document, automation_artifact, autotest_review]` | `[effective_document, automation_artifact, autotest_review]` | `[run_result]` |
| `build-trace` | `[effective_document, automation_artifact, run_result]` | `[effective_document, automation_artifact, run_result]` | `[trace_document]` |
| `trace-check` | `[trace_document]` | `[trace_document]` | `[trace_audit]` |
| `finalize-orchestration` | `[effective_document, effective_bundle_receipt, automation_artifact, autotest_review, run_result, trace_document, trace_audit]` | `[]` | `[orchestrator_output, terminal_run_receipt]` |
| `advance-baseline` | `[terminal_run_receipt]` | `[]` | always `[baseline_advancement]`; eligible only `[successor_baseline_receipt]` |

`changed_behavior_context` is therefore accepted and forwarded unchanged by both classifier stages before becoming the generator's sole semantic input. `managed_behavior_context` never enters generation. The full classifier still validates against the complete managed context and keeps changed context byte-identical; its semantic classification outputs do not influence case boundaries.

Branch rules are closed: FULL generator output contains exactly `candidate_document`; CHANGE_SET contains exactly `canonical_document_delta`. Nonzero delta runs pass through the applier then the existing publish/review/revision route. Zero-op runs pass only through applier and `select-unchanged-document`, then join at `tc-to-autotest`. No stage can produce both branch alternatives. The applier resolves the baseline document through the controller-bound baseline receipt, not an LLM carrier, and verifies its digest before output.

`finalize-orchestration` constructs `terminal_run_receipt` from its accepted tail artifacts plus the same run root's already validated, content-addressed prefix ledger. The ledger manifest binds every prefix digest before the first tail stage, so this is a deterministic readback dependency, not an undeclared carrier or semantic input. Missing, foreign, or mutable prefix evidence makes terminal-receipt production impossible and blocks baseline advancement.

No scope, accounting, receipt, raw source/diff/analytics, or technical evidence carrier enters generation or the tail. Full runs set `changed_behavior_context` to the complete validated behavior projection; change-set runs carry only the reviewed semantic delta and stable baseline identity/digest index needed to author the closed document delta.

## Persistence, concurrency, and resume

All authoritative artifacts are canonical compact UTF-8 JSON with closed schemas and create-only deterministic paths. Run roots contain manifest, immutable scope generations, immutable batch generations, promotions, receipt, and assurance summary. No mutable `current.json` exists.

Writes validate first, write a unique temporary sibling, flush where supported, atomically create without replacement, reopen, and verify bytes/digest. Identical concurrent writes are idempotent after readback. Different bytes for the same canonical path return a conflict and overwrite nothing.

Each scope or batch generation has an exclusive lease epoch. A lease permits an attempted write but is not authority; committed readback is authority. Stale leases may be superseded after controller liveness checks. Batches can be sharded internally and processed in parallel; plan order controls final receipt order, and users never choose shard count.

Resume revalidates repository identity, baseline, `.skillsrc`, inventories, frozen change input, plan, and current bytes; ignores incomplete temporary files; rejects gaps, branches, duplicate promotions, and out-of-order audits; then returns the exact next action. Drift triggers full refresh before semantic work or blocks an already frozen attempt; it never mutates the attempt in place.

## Safety

Durable artifacts contain only IDs, digests, typed relations/reasons, byte locators, verdicts, bounded safe summaries, and assurance counts. They never contain raw source, raw diff, analytics text, prompts, responses, transcripts, hidden reasoning, supplied-input paths, environment values, credentials, tokens, cookies, private keys, or controller secrets.

Evidence locators bind a content digest and half-open byte range. Diagnostics never echo rejected values or excerpts. Detected secret exposure stops the attempt without copying the secret into a finding.

The deterministic modules validate shape, digest, identity, ordering, current bytes, range confinement, chain integrity, atomicity, and assurance claims. They never infer semantic truth from filenames, extensions, AST nodes, framework names, or keywords.

## Error taxonomy

All direct CLI failures use safe `{path, code, message}`, exit 2, and no traceback:

| Code | Meaning |
|---|---|
| `FEATURE_FLOW_INPUT` | CLI/input variant is incomplete, conflicting, or unsafe. |
| `BASELINE_MISSING` | No exact compatible baseline exists; action is full refresh. |
| `BASELINE_BINDING` | Repository/tree/module/inventory/context identity differs. |
| `CHANGE_INPUT` | Git range, worktree snapshot, or patch manifest is invalid. |
| `CHANGE_SOURCE_DRIFT` | Frozen before/after or current bytes changed. |
| `CHANGE_SCOPE_SHAPE` | Scope candidate/audit/receipt violates its closed schema. |
| `CHANGE_SCOPE_BINDING` | Scope artifact has a foreign digest, generation, or source. |
| `CHANGE_SCOPE_ORDER` | Change, source, relation, symbol, or generation order is invalid. |
| `CHANGE_SCOPE_AUDIT` | Verdict/findings, reason, relation, locator, or code is invalid. |
| `CHANGE_SCOPE_COVERAGE` | Promoted scope omits required direct or widened evidence. |
| `CHANGE_PLAN_SHAPE` | Change-aware Plan V2 or side/range union is invalid. |
| `CHANGE_RESULT_SHAPE` | Batch Result V2, effect, tombstone, or locator is invalid. |
| `CHANGE_SIDE_BINDING` | Before/after digest, source identity, side, or target snapshot differs. |
| `BATCH_PROMOTION_SHAPE` | Candidate, audit, or promotion shape is invalid. |
| `BATCH_PROMOTION_BINDING` | Plan, scope, batch, item, candidate, or audit binding differs. |
| `BATCH_PROMOTION_MODE` | FULL/V1 or CHANGE_SET/V2 mode-result conjunction is violated. |
| `BATCH_PROMOTION_ORDER` | Audit or generation order is invalid. |
| `BATCH_PROMOTION_REWORK` | Rejected generation was promoted or chain is incomplete. |
| `PROMOTION_ASSURANCE` | Independent assurance is fabricated or unverifiable. |
| `PROMOTION_COVERAGE` | Final receipt lacks exactly one promotion per scoped batch. |
| `DOCUMENT_DELTA_SHAPE` | Canonical delta or application receipt violates its closed schema. |
| `DOCUMENT_DELTA_BINDING` | Baseline/source/document/revision/object digest does not match. |
| `DOCUMENT_DELTA_IDENTITY` | Collection partition is missing, foreign, duplicate, or colliding. |
| `DOCUMENT_DELTA_NOOP` | A claimed zero-op changes bytes, identities, metadata, or revision. |
| `BASELINE_INELIGIBLE` | Provisional or nonaccepted run attempted durable advancement. |
| `BASELINE_CHAIN` | Incremental base is not the exact predecessor target. |
| `BASELINE_FINGERPRINT` | Pipeline, policy, tool, or schema fingerprint is incompatible. |
| `BASELINE_CONFLICT` | One predecessor has competing create-only successor targets. |
| `FLOW_CONFLICT` | Concurrent writers supplied different canonical bytes. |
| `FLOW_ATOMIC_WRITE` | Atomic persistence or mandatory readback failed. |
| `FLOW_SAFE_TEXT` | Durable text violates the safety envelope. |

Diagnostic precedence is drift/baseline eligibility, shape, binding/fingerprint, identity/order/state, audit, assurance, coverage, persistence.

For predecessor capability construction, invalid full carrier shape is reported as `BASELINE_BINDING` after safe local-schema diagnostics are contained; foreign, stale, incomplete, or forged carriers and forged capabilities use the same code. `feature_flow` converts these mode-selection failures to `RUN_FULL_BASELINE`; it does not expose the rejected carrier or downgrade the error into an incremental warning.

## Attempt-04 pilot

Attempts 02 and 03 remain immutable terminal `REWORK` evidence. Attempt-04 starts under a new create-only root only after implementation verification.

The fixed pilot has one supported and one deceptive/no-fact control in each of 12 categories:

1. registered route versus URL/path helper;
2. public README/package contract versus contributor prose;
3. runtime/deployment config versus inert formatting config;
4. authorization behavior versus permission-name metadata;
5. domain validation versus passive field declaration;
6. state transition versus assignment helper;
7. serialization/API outcome versus plumbing;
8. task/event behavior versus registry/import scaffolding;
9. import/export/report behavior versus filesystem path construction;
10. public plugin seam versus internal bookkeeping;
11. genuine no-fact source versus false no-fact;
12. boundary-spanning fact versus overlap-only duplicate.

The known attempt-02 false-route batches and attempt-03 README/config cases are mandatory controls. The 24 controls exercise scope selection, both scope audits, candidate extraction, both batch audits, and promotion. Every oracle must pass in mandatory `SEQUENTIAL` mode. When trusted isolated contexts exist, the same artifacts may additionally calibrate `INDEPENDENT`; absence of that facility does not block portability.

## Finite RED matrix

Implementation begins with these public-interface failures:

1. `test_initial_full_requires_exact_committed_tree` — clean committed Git FULL is durable; dirty/non-Git FULL is provisional.
2. `test_missing_stale_or_incompatible_baseline_forces_full` — no partial incremental fallback.
3. `test_git_range_requires_predecessor_target_as_base` — repository, base commit/tree, and head commit/tree bind exactly.
4. `test_worktree_snapshot_freezes_staged_unstaged_and_untracked` — later byte drift blocks and run remains provisional.
5. `test_patch_manifest_closed_before_after_union` — unsafe, duplicate, missing-blob, digest, and rename errors fail.
6. `test_change_record_variants_and_canonical_order` — add/modify/delete/rename/binary side cardinalities are exact.
7. `test_diff_is_seed_and_relations_expand_impact_closure` — import/route/config/requirement relations include indirect evidence.
8. `test_ambiguity_widens_symbol_file_domain_module_full` — no skipped or user-tuned level.
9. `test_scope_rejects_foreign_reason_relation_and_locator` — closed types and digest-bound locators.
10. `test_full_test_inventory_with_only_impacted_scope_pairs` — tests never originate requirements.
11. `test_scope_requires_both_audits_on_same_generation` — one acceptance never promotes.
12. `test_scope_rework_requires_immediate_successor_generation` — no majority or late rehabilitation.
13. `test_full_candidate_requires_exact_v1_result` — FULL rejects V2, hybrids, and declared/embedded version mismatch.
14. `test_change_set_candidate_requires_exact_v2_result` — CHANGE_SET rejects V1, hybrids, and declared/embedded version mismatch.
15. `test_change_plan_v2_side_identity_range_and_order` — before/after coverage and source IDs are exact.
16. `test_change_result_v2_effect_requires_correct_evidence_sides` — added/modified/retired locator rules hold.
17. `test_deleted_behavior_requires_promoted_tombstone` — no fabricated current bytes.
18. `test_modified_and_renamed_noop_compare_both_sides` — lexical/path changes do not imply behavior change.
19. `test_batch_false_claim_audit_catches_known_false_routes` — attempt-02 regression stays RED without audit.
20. `test_batch_omission_audit_catches_readme_config_and_false_no_fact` — attempt-03 regression stays RED without audit.
21. `test_batch_requires_both_audits_on_same_generation` — any rework prevents promotion.
22. `test_receipt_rejects_loose_v1_and_v2_results` — direct writes and unpromoted results of either version are impossible.
23. `test_assurance_defaults_sequential_and_rejects_fabrication` — only trusted distinct fresh contexts yield independent.
24. `test_composite_receipt_rejects_promotion_and_unchanged_drift` — missing/duplicate/foreign rows fail.
25. `test_delta_schema_closes_source_baseline_revision_and_collections` — unknown or unbound fields fail.
26. `test_apply_delta_partitions_every_baseline_identity_once` — foreign/missing/duplicate/collision cases fail.
27. `test_apply_delta_reuses_ids_and_materializes_full_document` — replacements are complete, ordered, and cross-valid.
28. `test_every_capability_requirement_and_case_retirement_is_tombstoned` — kind, ID, baseline digest, closed reason, and support evidence are mandatory; bare IDs fail.
29. `test_zero_op_delta_returns_exact_baseline_without_publication` — no new revision, bundle, review, or case.
30. `test_nonzero_delta_revision_parent_and_review_flow` — complete candidate precedes publisher/reviewer; correction is a full successor.
31. `test_pipeline6_exact_carrier_complements_and_branches` — changed context crosses both classifier stages; no impossible product or forbidden carrier reaches generation/tail.
32. `test_baseline_advances_only_eligible_terminal_acceptance` — provisional, FAIL, NOT_RUNNABLE, BLOCKED, and REWORK never advance.
33. `test_baseline_receipt_binds_complete_artifacts_and_fingerprints` — all inventory/context/classification/document/trace/final digests are exact.
34. `test_baseline_successor_atomic_compare_and_readback` — identical concurrency is idempotent; competing target trees conflict.
35. `test_resume_ignores_temporary_files_and_returns_exact_action` — stale leases/gaps/branches cannot become state.
36. `test_safe_artifacts_and_diagnostics_do_not_echo_seeded_secret` — raw source/diff/prompt/reasoning remain absent.
37. `test_attempt04_fixed_24_control_pilot_blocks_any_mismatch` — all 12 categories pass the portable floor.
38. `test_pipeline6_versions_docs_doctor_renderer_and_legacy_rejection` — Pipeline 5/V5/Receipt V1 remain historical rejection rows and the full Pipeline 6 suite passes.
39. `test_scope_predecessor_is_baseline_minted_and_opaque` — only `bind_scope_predecessor` can create a recursively immutable capability; it exposes exactly baseline identity, ordered source slots, and ordered requirement IDs, with no JSON projection or raw predecessor carrier.
40. `test_scope_predecessor_revalidates_full_v5_receipt_joins` — foreign/digest-matched, incomplete, represented/no-fact, fragment/group/outcome, or requirement/product/source-link mismatch rejects before `CHANGE_SET` candidate creation and makes the façade select `RUN_FULL_BASELINE`.
41. `test_scope_inputs_require_exact_predecessor_capability` — `FULL` accepts only `None`; `CHANGE_SET` rejects a missing, forged, module/repository/base-mismatched, or raw-envelope predecessor and snapshots/candidates retain no raw source/diff/receipt/fragment/planner content.

Tests cross the external `feature_flow` interface. Focused internal module tests cover only invariants not observable through a practical façade scenario.

## Migration and rollout

Migration is one-way and shadowed before authority changes:

1. implement closed schemas and modules while Pipeline 5 remains authoritative;
2. run clean-commit FULL shadow baselines and the 12-category pilot; compare full context and complete materialized documents with V5 without publishing or advancing a baseline;
3. run CHANGE_SET shadows across add/modify/delete/rename/binary, zero-op, rework, dirty-worktree, patch, and committed git-range examples; verify expected scope, V2 side evidence, delta, full document, and advancement eligibility;
4. exercise the exact Pipeline 6 carrier complement, both generator branches, zero-op bypass, terminal receipt, fingerprint rejection, and atomic competing-successor behavior;
5. switch atomically to Pipeline 6, Context Marker V6, mode-aware Generator V4, Receipt V2, delta materialization, and baseline advancement only after all RED, compatibility, and shadow gates pass;
6. start InvenTree attempt-04 from a new root; never import or wrap attempt-02/03 batch results as promoted evidence.

Before each CHANGE_SET shadow, the façade must mint `ScopePredecessor` from readback of the exact predecessor full source envelope, V5 context envelope, and behavior-context receipt. Historical loose envelopes, receipts, or a reserialized capability cannot cross this seam. Historical V1 plans and V1 batch-result payloads remain valid only as nested FULL candidates. V2 plans/results are valid only as nested CHANGE_SET candidates. Neither version is directly authoritative. Historical V1 receipts and all loose or unreviewed V1/V2 results remain immutable evidence but are rejected by the live Pipeline 6 route. Deleted/before/rename evidence must be reprocessed as V2; no migration tool may fabricate modes, sides, capabilities, audits, promotions, tombstones, deltas, terminal receipts, or baselines.

## Costs and risks

The initial baseline is deliberately expensive: the LLM reads and promotes the whole authorized project. Later runs rebuild cheap metadata globally but read semantically only direct and impact-closure evidence, normally reducing cost substantially.

Normal promoted work requires three semantic passes per scope or batch generation. `SEQUENTIAL` adds latency and correlated-error risk; isolated `INDEPENDENT` contexts reduce execution correlation but still share model and prompt biases. Specialized opposing audits, exact evidence locators, fixed pilots, and honest assurance labels reduce but cannot eliminate semantic error.

Other residual risks:

- incomplete dependency metadata can miss transitive impact; conservative widening and omission audit mitigate it;
- over-widening can approach full-run cost; false-inclusion audit and deterministic relations constrain it;
- deletion semantics can retire behavior too early; tombstones and surviving-source checks prevent mechanical retirement;
- binary/generated/config files may be opaque; ambiguity widens or refreshes rather than inventing meaning;
- repeated auditor disagreement can grow immutable artifacts; rework stays batch-local and remains honest;
- controller attestation defects can overstate independence; the default is `SEQUENTIAL` and trust requires an in-memory capability;
- long-running source drift can invalidate work; frozen inputs and repeated digest checks stop rather than mix snapshots.

The intended trade is higher evidence cost at the exact point of change for much lower risk of generating tests from unsupported or incomplete behavior.

## Acceptance

The design is accepted when a plain single LLM can run the complete action loop with no tuning; the first run always creates a complete exact baseline; later exact-baseline runs generate only reviewed changed behavior; ambiguity widens safely; tests remain technical evidence rather than requirements; every authoritative scope and batch has two semantic accepts; assurance is reported honestly; and no unpromoted or raw evidence can reach generation.
