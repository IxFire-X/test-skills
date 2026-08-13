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

`change_scope` owns baseline binding, Git/patch acquisition, metadata comparison, impact closure, scope review, and a promoted scope receipt:

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

## Run modes

### Mandatory initial `FULL`

No exact compatible baseline means a full run. The initial run always:

1. resolves the selected module from `.skillsrc`;
2. binds the exact Git tree when available and the exact current file digests;
3. creates the complete authorized behavior-source inventory and complete technical test inventory;
4. plans every authorized source range;
5. promotes every semantic batch;
6. builds the full managed behavior context and full baseline receipt.

A supplied diff on the first run is recorded as an input digest but never narrows the baseline. A non-Git project is allowed only through a full content-digest snapshot; it cannot claim Git identity.

### Subsequent `CHANGE_SET`

Incremental execution requires an exact Pipeline 6 baseline whose module, `.skillsrc` identity, source-inventory contract, repository identity, base tree, full context, and receipt digests all validate. Otherwise the façade automatically returns `RUN_FULL_BASELINE`.

A change set is one closed variant:

- `git_range`: exact repository identity, base commit/tree and head commit/tree;
- `git_worktree`: exact base commit/tree plus a frozen snapshot covering staged, unstaged, and untracked files;
- `patch_manifest`: controller-supplied closed before/after manifest with exact content digests and optional rename relations.

For a worktree snapshot, staged, unstaged, and untracked content is frozen before semantic work. Later filesystem changes produce source drift; they are never silently absorbed. Ignored files remain outside scope unless already authorized by `.skillsrc` and explicitly present in the supplied manifest.

The diff is a seed, not the semantic boundary. The implementation may cheaply rebuild a full metadata registry of paths, symbols, imports, route/config registrations, source/test ownership, and content digests. The LLM reads only directly changed evidence and the mechanically derived impact closure.

### Automatic widening

Ambiguity widens conservatively in this fixed order:

```text
symbol -> file -> deterministic domain -> selected module -> FULL refresh
```

Widen when rename identity, binary meaning, deletion impact, cross-file binding, generated/config behavior, or dependency closure cannot be proved at the narrower level. Missing, stale, foreign, incompatible, or partially readable baseline evidence immediately selects `FULL`; the user is not asked to tune scope.

## Change scope

### Mechanical change records

Every changed path has one closed kind: `added`, `modified`, `deleted`, `renamed`, or `binary`. A rename carries old and new paths and before/after digests; similarity is evidence only when the underlying Git adapter supplies it. Binary rows carry digests and size, never raw bytes. Text rows bind before and after digests and locator ranges; raw diff text is not persisted.

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

All arrays are canonically ordered and exact. `included_test_symbols` always comes from a freshly rebuilt complete technical test inventory. It identifies impacted technical evidence only. Existing or changed tests never originate product requirements or canonical cases.

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

The candidate wraps the existing V1 batch-result payload and binds scope receipt, plan, batch, generation, parent candidate, and triggering `REWORK` audits. Generation 1 has no parent. Later generations may descend only from the immediately preceding rejected generation.

The false-claim audit checks unsupported or inflated actor, operation, condition, outcome, evidence, and route claims. A route still requires framework provenance, registration sink, and bound target. The omission audit independently checks material supported facts lost by the candidate, especially `no_supported_observable_fact`, deletions, boundary-spanning facts, README contracts, and runtime configuration.

Each audit reads exact authorized source ranges plus the candidate, but not producer reasoning, peer-audit output, or previous-generation audit output. A `REWORK` seals the generation and requests the next candidate for only that batch. Both exact audits must accept the same candidate generation before promotion.

### Promotion V1

```json
{
  "schema_version": "1.0.0",
  "artifact": "semantic-batch-promotion",
  "scope_receipt_sha256": "sha256:...",
  "plan_sha256": "sha256:...",
  "batch_id": "BATCH-000001",
  "generation": 1,
  "candidate_sha256": "sha256:...",
  "batch_result_sha256": "sha256:...",
  "audit_sha256s": {
    "false_claim": "sha256:...",
    "omission": "sha256:..."
  },
  "review_mode": "SEQUENTIAL",
  "independence_attestation_sha256": null
}
```

Direct authoritative batch-result writes are forbidden. Receipt construction accepts promotion artifacts, never loose V1 batch results.

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
  "retired_requirement_ids": [],
  "assurance": {
    "review_mode": "SEQUENTIAL",
    "independent_promotion_count": 0,
    "sequential_promotion_count": 1,
    "reworked_batch_count": 0
  }
}
```

`unchanged_source_bindings` proves exact baseline/current content-digest equality. Added and changed sources derive only from promoted current candidates. Deleted sources retain baseline evidence as tombstones until semantic review proves which requirement links survive elsewhere. A rename is not automatically a delete-plus-add behavior change.

The composed full managed context preserves IDs for semantically unchanged requirements and cases. Changed behaviors receive new revisions through existing canonical revision rules. A requirement is retired only when promoted current evidence and reviewed deletion impact prove it no longer exists; removal of one supporting file is insufficient when another source still supports it.

The generator receives only a validated `changed_behavior_context` projection containing new, changed, and retired requirement identities plus necessary stable context links. It never receives raw project source, raw diff, analytics body, source/test inventory, scope ledger, promotion ledger, technical test text, or accounting sidecar. An empty semantic delta generates zero new cases.

## Pipeline 6.0

This trust and topology change is Pipeline `6.0`. Context Marker becomes `6.0.0`; behavior context receipt becomes `2.0.0`. `.skillsrc` stays version 3.0 and its schema and generated contents do not change.

The exact prefix is:

```text
source-inventory
  produces: technical_test_inventory, authorized_behavior_sources

change-scope
  accepts: raw_content, technical_test_inventory, authorized_behavior_sources
  produces: change_scope_receipt

context-marker
  accepts: raw_content, technical_test_inventory,
           authorized_behavior_sources, change_scope_receipt
  produces: managed_behavior_context, changed_behavior_context,
            behavior_source_accounting, behavior_context_receipt

test-classifier
  accepts/forwards: technical_test_inventory, authorized_behavior_sources,
                    managed_behavior_context, behavior_source_accounting,
                    behavior_context_receipt

test-classifier-reviewer
  accepts: previous carriers plus technical_test_classification
  forwards: changed_behavior_context

tc-generator
  accepts/forwards: changed_behavior_context
  rejects: raw_content, source_code_and_diff, analytics body,
           technical_test_inventory, authorized_behavior_sources,
           change_scope_receipt, managed_behavior_context,
           behavior_source_accounting, behavior_context_receipt,
           technical_test_classification, classification_review,
           effective_technical_evidence
```

No scope, accounting, receipt, or technical evidence carrier enters later generation. Full runs set `changed_behavior_context` equal to the complete validated behavior projection; change-set runs contain only reviewed semantic delta plus stable identity links.

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
| `BATCH_PROMOTION_SHAPE` | Candidate, audit, or promotion shape is invalid. |
| `BATCH_PROMOTION_BINDING` | Plan, scope, batch, item, candidate, or audit binding differs. |
| `BATCH_PROMOTION_ORDER` | Audit or generation order is invalid. |
| `BATCH_PROMOTION_REWORK` | Rejected generation was promoted or chain is incomplete. |
| `PROMOTION_ASSURANCE` | Independent assurance is fabricated or unverifiable. |
| `PROMOTION_COVERAGE` | Final receipt lacks exactly one promotion per scoped batch. |
| `FLOW_CONFLICT` | Concurrent writers supplied different canonical bytes. |
| `FLOW_ATOMIC_WRITE` | Atomic persistence or mandatory readback failed. |
| `FLOW_SAFE_TEXT` | Durable text violates the safety envelope. |

Diagnostic precedence is drift/baseline, shape, binding, ordering/state, audit, assurance, coverage, persistence.

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

1. First run cannot select `CHANGE_SET`; it requires a complete `FULL` baseline.
2. Missing, stale, foreign, incompatible, or drifted baseline selects full refresh.
3. Git range binds exact base/head trees; worktree freezes staged, unstaged, and untracked content.
4. Patch manifests reject missing before/after digests, unsafe paths, duplicate paths, and inconsistent renames.
5. Add, modify, delete, rename, and binary variants are closed and canonically ordered.
6. Diff is only a seed; tested import/route/config/requirement relations expand impact closure.
7. Ambiguity widens symbol, file, domain, module, then full in exact order.
8. Scope candidates reject foreign sources/tests, invalid typed reasons, dangling relations, and bad locators.
9. Full technical inventory is rebuilt; only impacted pairs enter scope; tests never originate requirements.
10. False-inclusion and omission audits both accept the same scope generation before promotion.
11. Any scope `REWORK` requires exactly the next immutable scope generation; no majority or late rehabilitation.
12. Batch candidates preserve existing plan/item/range/fragment invariants.
13. False-claim audit catches the known false routes and inflated evidence.
14. Omission audit catches README/config facts and false no-fact outcomes.
15. Both audits must accept the same batch generation; any rework prevents promotion.
16. Loose batch results and project-specific materializers cannot build a receipt.
17. JSON/CLI/model claims cannot create `INDEPENDENT`; trusted distinct fresh contexts can.
18. Composite receipt rejects missing/duplicate/foreign promotions and unchanged-source drift.
19. Changed context preserves unchanged IDs, represents modifications, and requires proof for retirement.
20. Empty semantic delta produces zero new cases; unchanged cases are not regenerated.
21. Generator receives only `changed_behavior_context` and rejects every raw/scope/technical carrier.
22. Concurrent identical writes are idempotent; differing writes conflict without overwrite.
23. Crash remnants and stale leases do not become state; resume returns the exact next action.
24. Seeded secrets/source excerpts never appear in artifacts or diagnostics.
25. The 24-control pilot blocks rollout on any mismatch.
26. Pipeline 5/V5/Receipt V1 remain historical rejection fixtures; Pipeline 6/V6/Receipt V2 pass contract, doctor, generated docs, renderer, and full suite.

Tests cross the external `feature_flow` interface. Focused internal module tests cover only invariants not observable through a practical façade scenario.

## Migration and rollout

Migration is one-way and shadowed before authority changes:

1. implement closed schemas and modules while Pipeline 5 remains authoritative;
2. run FULL shadow baselines and the 12-category pilot; compare new full context with V5 without feeding generation;
3. run CHANGE_SET shadow cases across add/modify/delete/rename/binary examples and compare expected semantic deltas;
4. switch atomically to Pipeline 6, Context Marker V6, Receipt V2, and changed-context generation only after all RED, compatibility, and shadow gates pass;
5. start InvenTree attempt-04 from a new root; never import or wrap attempt-02/03 batch results as promoted evidence.

Historical V1 plans and V1 batch-result payloads remain valid internal candidate payloads. Historical V1 receipts and unreviewed results remain immutable evidence but are rejected by the live Pipeline 6 route. No migration tool may fabricate missing audits.

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
