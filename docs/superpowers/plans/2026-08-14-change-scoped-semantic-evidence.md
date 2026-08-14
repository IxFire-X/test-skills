# Change-Scoped Semantic Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pipeline 6 feature flow that builds an exact initial baseline, reduces later work to a reviewed semantic change scope, promotes every semantic batch through two audits, and materializes safe canonical document deltas.

**Architecture:** `tools/feature_flow.py` is the only common facade; it derives the next closed action from immutable readback and delegates to the internal `change_scope` and `batch_promotion` state machines. Deterministic adapters bind Git or patch bytes, schemas close every durable artifact, and context/delta composition admits only promoted evidence. Pipeline routing then exposes changed behavior to generation while keeping raw code, diffs, inventories, and ledgers out of the generator and tail.

**Tech Stack:** Python 3.10+ standard library, Git CLI, JSON Schema Draft 2020-12, existing `jsonschema`/`referencing` validation, `unittest`, canonical compact UTF-8 JSON, existing Pipeline contract/doctor/renderer tools.

## Global Constraints

- Normative design: `docs/superpowers/specs/2026-08-14-change-scoped-semantic-evidence-design.md`; preserve every closed union, version, action, carrier order, error code, and acceptance rule in that document.
- Pipeline version is exactly `6.0`; Context Marker is `6.0.0`; behavior-context receipt is `2.0.0`; mode-aware `tc-generator` is `4.0.0`; change-record, patch-manifest, scope, promotion, canonical delta, terminal-run, and baseline artifacts begin at `1.0.0`.
- The bare canonical document and existing reviewer/automation/run/trace artifact versions remain unchanged.
- `.skillsrc` remains exactly version `3.0`; do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, `schemas/project-discovery-output.schema.json`, `schemas/skillsrc-init-output.schema.json`, `tools/discover_project.py`, `tools/init_skillsrc.py`, `tools/skillsrc_manifest.py`, `tools/stack_catalog.py`, or `tools/scan_project.py`.
- One selected module is mandatory. Resolve it from direct path, symbol, route, or supplied requirement evidence; multiple plausible modules return `BLOCKED` and never guess.
- A missing, stale, foreign, incompatible, partially readable, or ambiguous baseline selects `RUN_FULL_BASELINE`; there is no partial incremental fallback or user-tuned widening.
- Widen only in this order: `symbol -> file -> deterministic domain -> selected module -> FULL refresh`.
- Durable artifacts are closed, canonical compact UTF-8 JSON and contain no raw source, raw diff, analytics text, prompts, responses, transcripts, hidden reasoning, supplied-input paths, environment values, or secrets.
- Writes are validate-first, unique-temporary-sibling, flush, atomic create-without-replacement, reopen, and byte/digest verify. Identical concurrent writes are idempotent; differing bytes return `FLOW_CONFLICT` without overwrite.
- Mandatory semantic assurance is `SEQUENTIAL`. Agent/subagent parallelism and trusted isolated `INDEPENDENT` review are optional optimizations; a plain single worker must complete the whole action loop.
- Tests cross the public `advance_feature_flow` facade wherever the behavior is practical. Internal tests cover schema, ordering, persistence, and state invariants that cannot be isolated through a small facade scenario.
- Follow strict RED -> GREEN within each task. Run only the named focused tests before Task 11. Run the complete test suite exactly once, in Task 11.
- Do not execute, import, start, or test `D:\AI-Projects\InvenTree-master` before Task 11. Attempts 02 and 03 remain immutable and must never be wrapped or imported as promoted Pipeline 6 evidence.
- Every task ends in its own commit. Keep unrelated user changes intact; do not publish, push, or open a PR.

## File Map

### New deterministic modules

- `tools/flow_artifacts.py` — safe diagnostics, canonical digests, create-only persistence/readback, leases, and ledgers; `tools/git_change_adapter.py` — Git/patch acquisition without semantic inference; `tools/baseline_lifecycle.py` — compatibility, terminal receipts, and predecessor chains.
- `tools/change_scope.py` — metadata registry, impact closure, scope generations/audits/promotion; `tools/batch_promotion.py` — candidate/audit/promotion ledger; `tools/document_delta.py` — delta validation/materialization; `tools/feature_flow.py` — sole public facade.

### New schemas

- Change inputs: `schemas/change-record.schema.json`, `schemas/patch-manifest.schema.json`; scope: `schemas/change-scope-candidate.schema.json`, `schemas/change-scope-audit.schema.json`, `schemas/change-scope-receipt.schema.json`; promotion: `schemas/semantic-batch-candidate.schema.json`, `schemas/semantic-batch-audit.schema.json`, `schemas/semantic-batch-promotion.schema.json`.
- Context/delta: `schemas/changed-behavior-context.schema.json`, `schemas/canonical-document-delta.schema.json`, `schemas/delta-application-receipt.schema.json`, `schemas/unchanged-document-selection.schema.json`; lifecycle: `schemas/terminal-run-receipt.schema.json`, `schemas/feature-baseline-receipt.schema.json`, `schemas/baseline-advancement.schema.json`; calibration: `schemas/pipeline6-calibration-report.schema.json`.

### Existing runtime/contracts to modify

- Runtime: `tools/behavior_context_planning.py`, `tools/test_classification.py`, `tools/orchestrate_test_case_revision.py`, `tools/revision_selection.py`, `tools/contract_check.py`, `tools/doctor.py`, `tools/render_contract_docs.py`; live schemas: behavior plan/result/receipt, Context Marker, generator, orchestrator, and pipeline schemas.
- Registry/docs/skills: `contracts/pipeline.json`, `PIPELINE.md`, `CONTRACTS.md`, `README.md`, `USAGE.md`, `HOW-IT-WORKS.md`, the context/classifier/generator/reviewer/orchestrator skill trees, plus new `skills/change-scope/SKILL.md` and its reference.

### New focused tests and fixtures

- Tests: `test_change_records`, `test_baseline_lifecycle`, `test_change_scope`, `test_change_context_v2`, `test_batch_promotion`, `test_behavior_context_v6`, `test_document_delta`, `test_feature_flow`, `test_pipeline_v6`, `test_pipeline6_migration`, and `test_attempt04_acceptance` under `tests/`; fixtures under `tests/fixtures/stages/v6`, `tests/fixtures/change-scope`, and `tests/fixtures/document-delta`.
- Eval: `evals/change-scoped-semantic-evidence/{scenarios.json,rubric.md,attempt04-acceptance.json}`; the acceptance file is generated only in Task 11.
---

### Task 1: Closed Change Inputs, Frozen Snapshots, and Git Adapter

**Files:**
- Create: `tools/flow_artifacts.py`
- Create: `tools/git_change_adapter.py`
- Create: `schemas/change-record.schema.json`
- Create: `schemas/patch-manifest.schema.json`
- Create: `tests/test_change_records.py`
- Create: `tests/fixtures/change-scope/patch-valid.json`
**Interfaces:**
- Produces: `FlowError(code: str, path: str, message: str)`, `canonical_bytes(value: Mapping[str, Any]) -> bytes`, `artifact_sha256(value: Mapping[str, Any]) -> str`, and `write_create_only(run_root: Path, relative_path: PurePosixPath, value: Mapping[str, Any]) -> StoredArtifact`.
- Produces: `ChangeInputSpec(base: str | None, head: str | None, worktree: bool, patch_manifest: Path | None)` and `acquire_change_input(project: Path, spec: ChangeInputSpec, blob_resolver: BlobResolver | None = None) -> Mapping[str, Any]`.
- The adapter returns only closed IDs, digests, sizes, text flags, locator ranges, repository/tree/snapshot identities, and controller blob IDs. Source bytes stay in controller memory.
- [ ] **Step 1: Write the closed-union and adapter RED tests**
Add these exact tests to `tests/test_change_records.py`:
```python
def test_change_record_variants_and_canonical_order():
    """Add/modify/delete/rename/binary rows have exact sides and stable change IDs."""
def test_patch_manifest_closed_before_after_union():
    """Unknown fields, duplicate rows/blobs, missing bytes, digest mismatch, and bad renames fail."""
def test_worktree_snapshot_freezes_staged_unstaged_and_untracked():
    """The snapshot includes all three Git states and rejects later source drift."""
def test_safe_artifacts_and_diagnostics_do_not_echo_seeded_secret():
    """Durable JSON and failures omit source, diff, environment, and a seeded credential."""
```
Build temporary Git repositories in the test. Configure local author identity, create one committed base, then exercise added, modified, deleted, `git mv`, binary, staged, unstaged, and untracked files. Assert `git diff` text and seeded contents are absent from every persisted artifact and diagnostic.
- [ ] **Step 2: Run RED**
Run:
```powershell
python -m unittest tests.test_change_records -v
```
Expected: import failure for `tools.git_change_adapter` and missing schema files.
- [ ] **Step 3: Implement schemas, canonical IDs, acquisition, and create-only writes**
Implement the exact variant cardinalities from the design. Derive IDs using:
```python
change_id = "CHANGE-" + hashlib.sha256(
    canonical_bytes({key: value for key, value in row.items() if key != "change_id"})
).hexdigest()
```
Use `git rev-parse`, `git diff --raw -z`, `git ls-files`, `git cat-file`, and explicit byte reads; never shell-parse human diff text. Validate in this precedence:
```text
CHANGE_SOURCE_DRIFT -> CHANGE_INPUT -> CHANGE_SCOPE_SHAPE ->
CHANGE_SCOPE_BINDING -> CHANGE_SCOPE_ORDER -> FLOW_SAFE_TEXT ->
FLOW_ATOMIC_WRITE/FLOW_CONFLICT
```
For a worktree, freeze an ordered manifest and controller-owned byte map before returning. For a patch manifest, require every side digest to resolve exactly once from the named repository tree or `BlobResolver`; reject foreign or unused blobs.
- [ ] **Step 4: Run focused GREEN and persistence checks**
Run:
```powershell
python -m unittest tests.test_change_records -v
python -m py_compile tools\flow_artifacts.py tools\git_change_adapter.py tests\test_change_records.py
python tools\validate_artifact.py schemas\patch-manifest.schema.json tests\fixtures\change-scope\patch-valid.json
git diff --check
```
Expected: all commands pass; concurrent identical writers read back the same bytes and conflicting writers leave the first artifact unchanged.
- [ ] **Step 5: Commit**
```powershell
git add tools/flow_artifacts.py tools/git_change_adapter.py schemas/change-record.schema.json schemas/patch-manifest.schema.json tests/test_change_records.py tests/fixtures/change-scope/patch-valid.json
git commit -m "feat: bind closed feature change inputs"
```
---

### Task 2: Terminal Receipt and Durable Baseline Lifecycle

**Files:**
- Create: `tools/baseline_lifecycle.py`
- Create: `schemas/terminal-run-receipt.schema.json`
- Create: `schemas/feature-baseline-receipt.schema.json`
- Create: `schemas/baseline-advancement.schema.json`
- Create: `tests/test_baseline_lifecycle.py`
**Interfaces:**
- Consumes: `StoredArtifact`, `artifact_sha256`, and create-only readback from Task 1.
- Produces: `build_terminal_run_receipt(prefix_manifest, tail_artifacts) -> Mapping[str, Any]`, `validate_baseline_receipt(receipt, project, selected_module, fingerprints) -> ValidatedBaseline`, `bind_scope_predecessor(baseline: ValidatedBaseline, predecessor_source_inventory_envelope: Mapping[str, Any], predecessor_context_v5_envelope: Mapping[str, Any], predecessor_behavior_context_receipt: Mapping[str, Any]) -> ScopePredecessor`, `choose_run_mode(target, baseline=None) -> Literal["FULL", "CHANGE_SET"]`, and `advance_baseline(run, terminal_receipt, predecessor=None) -> Mapping[str, Any]`.
- `ValidatedBaseline` exposes exact repository, commit/tree, module, inventory/context/document/bundle digests, fingerprints, and its canonical receipt digest; it never exposes a mutable latest pointer.
- `bind_scope_predecessor` is the only predecessor projection. It validates the exact full source-inventory envelope, V5 context envelope, and behavior-context receipt against the Baseline Receipt's artifact digests; reuses `_validate_stored_context_relations` for the complete V5 graph; and returns a recursively immutable opaque capability containing only baseline identity, ordered authorized source identity slots, and ordered requirement IDs. It has no public constructor, JSON projection, standalone artifact digest, persistence, or deserialization route.
- [ ] **Step 1: Write baseline eligibility and lineage RED tests**
```python
def test_initial_full_requires_exact_committed_tree():
    """Clean committed Git FULL is durable; dirty and non-Git FULL are provisional."""
def test_missing_stale_or_incompatible_baseline_forces_full():
    """Every missing binding or fingerprint mismatch chooses FULL."""
def test_git_range_requires_predecessor_target_as_base():
    """Repository, base commit/tree, and head commit/tree bind exactly."""
def test_baseline_advances_only_eligible_terminal_acceptance():
    """Provisional, failed, not-runnable, blocked, rework, drift, and incomplete runs cannot advance."""
def test_baseline_receipt_binds_complete_artifacts_and_fingerprints():
    """All required prefix, classification, document, bundle, trace, final, tool, policy, schema, and pipeline digests are exact."""
def test_baseline_successor_atomic_compare_and_readback():
    """Identical successors are idempotent and competing target trees return BASELINE_CONFLICT."""
def test_scope_predecessor_revalidates_full_v5_receipt_joins():
    """Exact source/context/receipt digests and every V5 source, requirement, product, represented/no-fact, fragment, group, and outcome join bind before projection."""
def test_scope_predecessor_is_baseline_minted_and_opaque():
    """Only the lifecycle factory returns recursively immutable identity slots; forged, reserialized, or raw-carrier look-alikes fail."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_baseline_lifecycle -v`.
Expected: import failure for `tools.baseline_lifecycle`.
- [ ] **Step 3: Implement terminal validation and create-only predecessor links**
Use these exact eligibility statuses:
```python
ELIGIBLE_FINAL_STATUSES = frozenset({"PASS", "PASS_WITH_MANUAL_REMAINDER", "MANUAL_ONLY"})
INELIGIBLE_FINAL_STATUSES = frozenset({"FAIL", "NOT_RUNNABLE", "BLOCKED"})
```
Compute ordered fingerprints from closed `{path, sha256}` registries, not version labels. Initial advancement requires clean committed `HEAD`; successor advancement requires committed `git_range` and exact predecessor target. Persist the content-addressed receipt first, then one create-only predecessor link; reopen and verify both. Return a closed `baseline-advancement` artifact for eligible, ineligible, idempotent, and conflict outcomes. `bind_scope_predecessor` must validate all three predecessor carriers against `ValidatedBaseline.receipt`, validate both full envelopes with their existing local schemas, recompute every carrier/internal digest join, and reuse `_validate_stored_context_relations` rather than duplicate it. It projects exactly the ordered `source_id`, kind, canonical path, and content digest slots plus ordered requirement IDs; it never returns a retained envelope, receipt, fragment, group, outcome, planner item, or raw bytes. Binding failure is safe `BASELINE_BINDING` and yields no capability.
- [ ] **Step 4: Run focused GREEN**
```powershell
python -m unittest tests.test_baseline_lifecycle -v
python -m py_compile tools\baseline_lifecycle.py tests\test_baseline_lifecycle.py
git diff --check
```
Expected: all pass, including competing-thread tests; no mutable `latest` file is created.
- [ ] **Step 5: Commit**
```powershell
git add tools/baseline_lifecycle.py schemas/terminal-run-receipt.schema.json schemas/feature-baseline-receipt.schema.json schemas/baseline-advancement.schema.json tests/test_baseline_lifecycle.py
git commit -m "feat: add durable feature baselines"
```
---

### Task 3: Change-Scope Registry, Impact Closure, Audits, and Promotion

**Files:**
- Create: `tools/change_scope.py`
- Create: `schemas/change-scope-candidate.schema.json`
- Create: `schemas/change-scope-audit.schema.json`
- Create: `schemas/change-scope-receipt.schema.json`
- Create: `tests/test_change_scope.py`
- Create: `tests/fixtures/change-scope/relations.json`
**Interfaces:**
- Consumes: `ScopePredecessor`, the frozen change input, complete current source/test inventories, analytics digest, and normalized selected module. It never consumes a `ValidatedBaseline` or predecessor source/context/receipt envelope directly.
- `ScopeInputs` is exactly `ScopeInputs(project_root, run_mode, selected_module, analytics_sha256, change_input, current_source_inventory, current_test_inventory, predecessor, relations=())`; it has no baseline or predecessor-envelope fields.
- Produces exactly:
```text
start_scope(inputs: ScopeInputs) -> ScopeSnapshot
record_scope(snapshot: ScopeSnapshot, candidate_or_audit: Mapping[str, Any]) -> ScopeSnapshot
advance_scope(snapshot: ScopeSnapshot, controller: ReviewController | None = None) -> ScopeAction
```
- `ScopeAction.kind` is one of `PRODUCE_CHANGE_SCOPE`, `RUN_SCOPE_FALSE_INCLUSION_AUDIT`, `RUN_SCOPE_OMISSION_AUDIT`, `COMPLETE`, or `BLOCKED`; `COMPLETE` carries the promoted scope receipt.
- The registry relation kinds and inclusion reasons are the closed ordered sets from the design. Relations explain why bytes must be read and never assert behavior change.
- [ ] **Step 1: Write impact, widening, inventory, and audit RED tests**
```python
def test_diff_is_seed_and_relations_expand_impact_closure():
    """Import, route, config, requirement, symbol, and test relations include indirect evidence."""
def test_ambiguity_widens_symbol_file_domain_module_full():
    """Every unresolved level advances exactly once in the fixed order."""
def test_scope_rejects_foreign_reason_relation_and_locator():
    """Reasons, relations, source identities, and digest-bound locators are closed."""
def test_full_test_inventory_with_only_impacted_scope_pairs():
    """The complete rebuilt test inventory yields impacted pairs but originates no requirement."""
def test_scope_requires_both_audits_on_same_generation():
    """One acceptance, mixed generations, and late acceptance cannot promote."""
def test_scope_rework_requires_immediate_successor_generation():
    """REWORK seals a generation and only its immediate descendant may continue."""
def test_scope_inputs_require_exact_predecessor_capability():
    """FULL accepts only None; CHANGE_SET rejects missing, forged, module/repository/base-mismatched, or raw-envelope predecessor authority."""
def test_scope_snapshot_and_candidate_do_not_leak_predecessor_carriers():
    """Snapshots and candidates exclude raw source/diff bytes, envelopes, receipts, fragments, groups, outcomes, and planner content."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_change_scope -v`.
Expected: missing scope schemas/module and no promoted scope receipt.
- [ ] **Step 3: Implement complete metadata registry and deterministic closure**
Rebuild metadata for every authorized current path and every technical test symbol. Store only IDs/digests/locators. `FULL` requires `predecessor is None` and no change input. `CHANGE_SET` requires the exact lifecycle-minted capability and must bind its module, repository, target commit/tree, and derived base snapshot to the frozen change input before candidate creation. Before-side joins use only the capability's ordered slots; no scope code revalidates, retains, serializes, or reconstructs predecessor envelopes/receipt graph. Use exact canonical order: change rows, source inventory order, relation tuple `(relation_kind, from_source_id, to_source_id, locator)`, and `(file_id, symbol_id)` test pairs.
Implement scope generations as this state graph:
```text
candidate[g] -> false_inclusion[g] -> omission[g] -> receipt
      | REWORK                         | REWORK
      +---------------- candidate[g+1]-+
```
An `ACCEPT` audit has zero findings; `REWORK` has at least one kind-specific closed finding. Both audits read the candidate plus authorized evidence but not peer or producer reasoning. A FULL scope includes every source with governing reason `FULL_REFRESH`.
- [ ] **Step 4: Run focused GREEN**
```powershell
python -m unittest tests.test_baseline_lifecycle tests.test_change_scope -v
python -m py_compile tools\baseline_lifecycle.py tools\change_scope.py tests\test_baseline_lifecycle.py tests\test_change_scope.py
python tools\validate_artifact.py schemas\change-scope-receipt.schema.json tests\fixtures\change-scope\relations.json --expect-invalid
git diff --check
```
Expected: the focused lifecycle/scope suites and compile pass; the deliberately non-receipt relation fixture is rejected by the receipt schema without remote schema access. The capability tests prove the full V5 represented/no-fact/fragment/group/outcome graph, factory-only forgery rejection, exact FULL/CHANGE_SET authority, and no predecessor-carrier leak from public snapshots/candidates.
- [ ] **Step 5: Commit**
```powershell
git add tools/change_scope.py schemas/change-scope-candidate.schema.json schemas/change-scope-audit.schema.json schemas/change-scope-receipt.schema.json tests/test_change_scope.py tests/fixtures/change-scope/relations.json
git commit -m "feat: promote reviewed feature change scope"
```
---

### Task 4: Change-Aware Plan V2 and Batch Result V2

**Files:**
- Modify: `tools/behavior_context_planning.py`
- Modify: `schemas/behavior-context-plan.schema.json`
- Modify: `schemas/behavior-context-batch-result.schema.json`
- Create: `tests/test_change_context_v2.py`
- Create: `tests/fixtures/stages/v6/change-plan-v2.json`
- Create: `tests/fixtures/stages/v6/change-result-v2.json`
**Interfaces:**
- Preserves V1 only for complete current-side FULL candidates.
- Produces: `build_change_context_plan(scope_receipt, baseline_inventory, current_inventory, byte_resolver) -> Mapping[str, Any]`, `validate_change_batch_result(scope_receipt, plan, result, byte_resolver) -> Sequence[Mapping[str, str]]`, and `stable_change_fragment_id(item, fragment) -> str`.
- Plan V2 item keys are exactly `item_id`, `change_id`, `change_kind`, `baseline_source_id`, `current_source_id`, `domain_key`, and `evidence_sides`.
- [ ] **Step 1: Write side, range, effect, tombstone, and no-op RED tests**
```python
def test_change_plan_v2_side_identity_range_and_order():
    """Before/after cardinality, coverage, source IDs, baseline slots, additions, and split order are exact."""
def test_change_result_v2_effect_requires_correct_evidence_sides():
    """Added uses after, retired uses before, and modified binds at least one locator on each side."""
def test_deleted_behavior_requires_promoted_tombstone():
    """Deletion preserves baseline evidence and never fabricates current bytes."""
def test_modified_and_renamed_noop_compare_both_sides():
    """No-change results require an authorized comparison of both sides."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_change_context_v2 -v`.
Expected: missing V2 builders and schema rejection of `schema_version: "2.0.0"`.
- [ ] **Step 3: Implement the V2 union beside the historical V1 union**
Make each schema a strict `oneOf` with no hybrid fields. Binary items expose digest/size metadata and no synthetic text range. Derive fragment identity from this exact semantic set:
```python
identity = {
    "effect": fragment["effect"],
    "baseline_source_id": item["baseline_source_id"],
    "current_source_id": item["current_source_id"],
    "item_id": item["item_id"],
    "evidence_locators": fragment["evidence_locators"],
}
```
Reread `after` bytes from the frozen target and `before` bytes only from the predecessor tree or patch blob resolver. Apply existing 64 KiB accounted / 16 KiB UTF-8 overlap rules independently to each selected text side.
- [ ] **Step 4: Run focused GREEN and V1 compatibility**
```powershell
python -m unittest tests.test_change_context_v2 tests.test_behavior_context_v5 -v
python tools\validate_artifact.py schemas\behavior-context-plan.schema.json tests\fixtures\stages\v6\change-plan-v2.json
python tools\validate_artifact.py schemas\behavior-context-batch-result.schema.json tests\fixtures\stages\v6\change-result-v2.json
python -m py_compile tools\behavior_context_planning.py tests\test_change_context_v2.py
git diff --check
```
Expected: V2 and existing V1 focused suites pass; no deleted or before-side evidence is accepted by V1.
- [ ] **Step 5: Commit**
```powershell
git add tools/behavior_context_planning.py schemas/behavior-context-plan.schema.json schemas/behavior-context-batch-result.schema.json tests/test_change_context_v2.py tests/fixtures/stages/v6/change-plan-v2.json tests/fixtures/stages/v6/change-result-v2.json
git commit -m "feat: add change-aware semantic batch protocol"
```
---

### Task 5: Portable Semantic Batch Promotion Ledger

**Files:**
- Create: `tools/batch_promotion.py`
- Create: `schemas/semantic-batch-candidate.schema.json`
- Create: `schemas/semantic-batch-audit.schema.json`
- Create: `schemas/semantic-batch-promotion.schema.json`
- Create: `tests/test_batch_promotion.py`
- Create: `tests/fixtures/stages/v6/semantic-promotion.json`
**Interfaces:**
- Consumes: one promoted scope receipt and one exact V1 or V2 plan.
- Produces exactly:
```text
start_promotion(scope_receipt: Mapping[str, Any], plan: Mapping[str, Any]) -> PromotionSnapshot
record_promotion(snapshot: PromotionSnapshot, candidate_or_audit: Mapping[str, Any]) -> PromotionSnapshot
advance_promotion(snapshot: PromotionSnapshot, controller: ReviewController | None = None) -> PromotionAction
```
- `PromotionAction.kind` is one of `PRODUCE_BATCH_CANDIDATE`, `RUN_BATCH_FALSE_CLAIM_AUDIT`, `RUN_BATCH_OMISSION_AUDIT`, `PROMOTE_BATCH`, `COMPLETE`, or `BLOCKED`.
- Trusted `ReviewController` may provide an unforgeable in-memory `IndependentReviewCapability`; JSON and CLI inputs cannot construct it.
- [ ] **Step 1: Write mode, audit, rework, assurance, and authority RED tests**
```python
def test_full_candidate_requires_exact_v1_result():
    """FULL rejects V2, hybrids, and declared/embedded version mismatch."""
def test_change_set_candidate_requires_exact_v2_result():
    """CHANGE_SET rejects V1, hybrids, and declared/embedded version mismatch."""
def test_batch_false_claim_audit_catches_known_false_routes():
    """Path helpers without framework sink and bound target remain rejected."""
def test_batch_omission_audit_catches_readme_config_and_false_no_fact():
    """Public README/config contracts and supported facts cannot be omitted."""
def test_batch_requires_both_audits_on_same_generation():
    """Mixed generations, one acceptance, and any sealed REWORK cannot promote."""
def test_receipt_rejects_loose_v1_and_v2_results():
    """Neither batch-result version is authority without candidate, two accepts, and promotion."""
def test_assurance_defaults_sequential_and_rejects_fabrication():
    """Only a trusted distinct/fresh/blind in-memory proof yields INDEPENDENT."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_batch_promotion -v`.
Expected: missing promotion module/schemas and direct batch results still appear authoritative to the V5 receipt path.
- [ ] **Step 3: Implement immutable per-batch generations and blind audit prompts**
Enforce this exact conjunction:
```python
MODE_RESULT_VERSION = {"FULL": "1.0.0", "CHANGE_SET": "2.0.0"}
AUDIT_ORDER = ("false_claim", "omission")
```
Generation 1 has no parent. A later candidate names only the immediately preceding rejected candidate and all triggering REWORK audit digests. Each audit input contains authorized evidence and the exact candidate, excluding producer reasoning, peer output, and earlier audit bodies. Promotion revalidates the nested result, both audits, mode/version, generation chain, canonical digest, and readback before returning authority.
- [ ] **Step 4: Run focused GREEN**
```powershell
python -m unittest tests.test_batch_promotion -v
python tools\validate_artifact.py schemas\semantic-batch-promotion.schema.json tests\fixtures\stages\v6\semantic-promotion.json
python -m py_compile tools\batch_promotion.py tests\test_batch_promotion.py
git diff --check
```
Expected: all pass in default `SEQUENTIAL`; fabricated independence and cross-mode payloads fail with their specific safe codes.
- [ ] **Step 5: Commit**
```powershell
git add tools/batch_promotion.py schemas/semantic-batch-candidate.schema.json schemas/semantic-batch-audit.schema.json schemas/semantic-batch-promotion.schema.json tests/test_batch_promotion.py tests/fixtures/stages/v6/semantic-promotion.json
git commit -m "feat: require dual-audit semantic promotion"
```
---

### Task 6: Composite Receipt and Context Marker V6

**Files:**
- Modify: `tools/behavior_context_planning.py`
- Modify: `tools/test_classification.py`
- Modify: `schemas/behavior-context-receipt.schema.json`
- Modify: `schemas/context-marker-output.schema.json`
- Create: `schemas/changed-behavior-context.schema.json`
- Create: `tests/test_behavior_context_v6.py`
- Create: `tests/fixtures/stages/v6/context-marker.json`
- Create: `tests/fixtures/stages/v6/receipt.json`
**Interfaces:**
- Consumes: exact scope receipt, plan, one promotion per scoped batch, predecessor full context for `CHANGE_SET`, current inventories, and frozen byte resolver.
- Produces: `compose_behavior_context(project, module, inventories, scope_receipt, plan, promotions, baseline_context, byte_resolver) -> ComposedBehaviorContext`, where the closed result contains `managed_behavior_context`, `changed_behavior_context`, `behavior_source_accounting`, and behavior-context receipt V2.
- Extends `ValidatedBehaviorContext` with byte-identical `changed_requirements`, `retired_requirements`, and stable baseline links. Existing classifier selection continues to consume the complete managed requirements.
- [ ] **Step 1: Write composite binding and changed-projection RED tests**
```python
def test_composite_receipt_rejects_promotion_and_unchanged_drift():
    """Missing, duplicate, foreign, reordered, loose-result, and changed unchanged bindings fail."""
def test_changed_context_contains_only_delta_tombstones_and_stable_links():
    """Generator projection excludes raw inputs, ledgers, inventories, analytics, and technical test text."""
def test_requirement_retirement_requires_no_surviving_support():
    """Every retirement is byte-identical to the receipt tombstone and cites promoted retired fragments."""
def test_full_context_uses_every_promoted_v1_batch():
    """FULL has complete source outcomes and changed context equal to its validated full projection."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_behavior_context_v6 -v`.
Expected: Context Marker V5 schema rejects V6 and receipt V1 has no scope/promotion/unchanged bindings.
- [ ] **Step 3: Implement V2 receipt composition and shared validated seam**
Require receipt fields exactly as the design, including ordered promotion digests, unchanged source bindings, fragment registry, outcomes, changed IDs, retirement tombstones, and assurance counts. Preserve semantically unchanged requirement IDs. Reject retirement when any current source still supports the requirement.
Remove direct `batch_result_sha256s` authority from the live V2 path. A FULL receipt accepts only promoted nested V1 results; a CHANGE_SET receipt accepts only promoted nested V2 results. Keep V1 receipt/schema handling only in explicit historical rejection/fixture validation, not the live Pipeline 6 loader.
- [ ] **Step 4: Run focused GREEN and classifier compatibility**
```powershell
python -m unittest tests.test_behavior_context_v6 tests.test_test_classification tests.test_test_portfolio_audit -v
python tools\validate_artifact.py schemas\context-marker-output.schema.json tests\fixtures\stages\v6\context-marker.json
python tools\validate_artifact.py schemas\behavior-context-receipt.schema.json tests\fixtures\stages\v6\receipt.json
python -m py_compile tools\behavior_context_planning.py tools\test_classification.py tests\test_behavior_context_v6.py
git diff --check
```
Expected: all focused tests pass; classifiers receive the complete managed context plus unchanged changed-context bytes, while generator-safe projection contains no forbidden carrier body.
- [ ] **Step 5: Commit**
```powershell
git add tools/behavior_context_planning.py tools/test_classification.py schemas/behavior-context-receipt.schema.json schemas/context-marker-output.schema.json schemas/changed-behavior-context.schema.json tests/test_behavior_context_v6.py tests/fixtures/stages/v6/context-marker.json tests/fixtures/stages/v6/receipt.json
git commit -m "feat: compose promoted Pipeline 6 context"
```
---

### Task 7: Canonical Document Delta and Deterministic Applier

**Files:**
- Create: `tools/document_delta.py`
- Create: `schemas/canonical-document-delta.schema.json`
- Create: `schemas/delta-application-receipt.schema.json`
- Create: `schemas/unchanged-document-selection.schema.json`
- Modify: `schemas/tc-generator-output.schema.json`
- Modify: `tools/revision_selection.py`
- Modify: `tools/orchestrate_test_case_revision.py`
- Create: `tests/test_document_delta.py`
- Create: `tests/fixtures/document-delta/nonzero.json`
- Create: `tests/fixtures/document-delta/zero-op.json`
**Interfaces:**
- Produces exactly:
```python
def apply_document_delta(
    baseline_document: Mapping[str, Any],
    delta: Mapping[str, Any],
    behavior_context_receipt: Mapping[str, Any],
    changed_behavior_context: Mapping[str, Any],
) -> AppliedDocumentDelta
```
- `AppliedDocumentDelta` is frozen and serializes to exactly `{status, candidate_document, publication_required, delta_sha256, baseline_document_sha256}`.
- Produces: `select_unchanged_document(applied, validated_baseline) -> tuple[Mapping[str, Any], Receipt]` for the zero-op bypass.
- Generator V4 is a closed mode union: FULL emits only `canonical_document`; CHANGE_SET emits only `canonical_document_delta`.
- [ ] **Step 1: Write schema, partition, tombstone, materialization, and branch RED tests**
```python
def test_delta_schema_closes_source_baseline_revision_and_collections():
    """Unknown fields and unbound document/source/revision values fail."""
def test_apply_delta_partitions_every_baseline_identity_once():
    """Missing, foreign, duplicate, and colliding identities fail in every collection."""
def test_apply_delta_reuses_ids_and_materializes_full_document():
    """Complete replacements retain slots, additions use deterministic order, and references validate."""
def test_every_capability_requirement_and_case_retirement_is_tombstoned():
    """Kind, ID, baseline digest, closed reason, and support evidence are mandatory; bare IDs fail."""
def test_zero_op_delta_returns_exact_baseline_without_publication():
    """Bytes, revision, bundle, and review route remain unchanged."""
def test_nonzero_delta_revision_parent_and_review_flow():
    """A complete candidate precedes publish/review and an auto-fix is a complete successor."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_document_delta -v`.
Expected: missing applier/schema and generator V3 cannot represent the mode branches.
- [ ] **Step 3: Implement the closed partitions and applier**
For each collection enforce:
```text
baseline_ids = reused_ids disjoint-union modified.ids disjoint-union retired.ids
added.ids intersect baseline_ids = empty
```
Recompute every baseline object digest and tombstone support. Copy reused objects byte-for-byte, replace modified objects in baseline slots, remove tombstones, append additions in deterministic delta order, then rewrite all `display_order` fields and call `require_valid_canonical_document`.
A semantic delta requires `target_revision = baseline_revision + 1`, exact parent digest, and publication. A zero-op requires identical metadata, all baseline IDs reused, empty other sets, unchanged revision, exact baseline canonical bytes, and `publication_required=False`.
- [ ] **Step 4: Run focused GREEN and existing revision tests**
```powershell
python -m unittest tests.test_document_delta tests.test_revision_selection tests.test_orchestration_v3 tests.test_canonical_schema tests.test_canonical_semantics -v
python tools\validate_artifact.py schemas\canonical-document-delta.schema.json tests\fixtures\document-delta\nonzero.json
python tools\validate_artifact.py schemas\canonical-document-delta.schema.json tests\fixtures\document-delta\zero-op.json
python -m py_compile tools\document_delta.py tools\revision_selection.py tools\orchestrate_test_case_revision.py tests\test_document_delta.py
git diff --check
```
Expected: all pass; only a materialized full candidate can reach the publisher/reviewer.
- [ ] **Step 5: Commit**
```powershell
git add tools/document_delta.py schemas/canonical-document-delta.schema.json schemas/delta-application-receipt.schema.json schemas/unchanged-document-selection.schema.json schemas/tc-generator-output.schema.json tools/revision_selection.py tools/orchestrate_test_case_revision.py tests/test_document_delta.py tests/fixtures/document-delta/nonzero.json tests/fixtures/document-delta/zero-op.json
git commit -m "feat: apply canonical feature document deltas"
```
---

### Task 8: `feature_flow` Facade, Closed Actions, Resume, and CLI

**Files:**
- Create: `tools/feature_flow.py`
- Modify: `tools/test_classification.py`
- Create: `tests/test_feature_flow.py`
**Interfaces:**
- Produces the only common programmatic entry point:
```python
def advance_feature_flow(
    project: Path,
    analytics: Path,
    run_root: Path,
    baseline_receipt: Path | None = None,
    change_input: ChangeInputSpec | None = None,
    recorded_artifact: Path | None = None,
    controller: ReviewController | None = None,
) -> FeatureFlowAction
```
- `FeatureFlowAction.kind` is exactly one of `RUN_FULL_BASELINE`, `PRODUCE_CHANGE_SCOPE`, `RUN_SCOPE_FALSE_INCLUSION_AUDIT`, `RUN_SCOPE_OMISSION_AUDIT`, `PRODUCE_BATCH_CANDIDATE`, `RUN_BATCH_FALSE_CLAIM_AUDIT`, `RUN_BATCH_OMISSION_AUDIT`, `PROMOTE_BATCH`, `BUILD_CONTEXT`, `GENERATE_CHANGED_BEHAVIOR`, `COMPLETE`, or `BLOCKED`.
- CLI required flags: `python tools/test_classification.py feature-flow --project PROJECT_ROOT --analytics ANALYTICS_JSON --run-root CREATE_ONLY_RUN_ROOT`; optional flags are exactly `--baseline-receipt RECEIPT_JSON`, `--base GIT_OBJECT`, `--head GIT_OBJECT`, `--worktree`, `--patch-manifest CLOSED_PATCH_JSON`, and `--record CANDIDATE_OR_AUDIT_JSON`. `.skillsrc` is automatic and has no CLI override.
- [ ] **Step 1: Write facade-first run-mode, selection, resume, and safe CLI RED tests**
```python
def test_initial_full_requires_exact_committed_tree():
    """Facade returns durable FULL only for the exact clean committed tree."""
def test_missing_stale_or_incompatible_baseline_forces_full():
    """Facade automatically returns RUN_FULL_BASELINE without asking for scope settings."""
def test_git_range_requires_predecessor_target_as_base():
    """Facade blocks a foreign range and accepts the exact predecessor target."""
def test_worktree_snapshot_freezes_staged_unstaged_and_untracked():
    """Facade persists one frozen provisional input and later drift blocks the attempt."""
def test_feature_flow_selects_exactly_one_module_or_blocks():
    """Direct path, symbol, route, and supplied evidence select one module; ambiguity blocks."""
def test_resume_ignores_temporary_files_and_returns_exact_action():
    """Temps, stale leases, gaps, branches, duplicates, and out-of-order audits never become state."""
def test_safe_artifacts_and_diagnostics_do_not_echo_seeded_secret():
    """CLI exits 2 with one safe path/code/message row and no traceback or rejected value."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_feature_flow -v`.
Expected: missing facade and `feature-flow` parser command.
- [ ] **Step 3: Implement immutable-readback orchestration**
On every call, validate repository/baseline/manifest/inventories/change input/plan/current bytes first, scan only canonical committed paths, and derive the next state without a mutable pointer. Dispatch internally:
```text
baseline selection -> change_scope -> batch_promotion -> context composition ->
generation branch -> terminal receipt -> optional baseline advancement
```
The returned action includes only its exact artifact path/digest bindings and bounded prompt inputs. Recording validates the expected action, schema, digest, generation, and write path before persistence. `controller=None` always reports `SEQUENTIAL` and still completes every action.
- [ ] **Step 4: Run focused GREEN and CLI probes**
```powershell
python -m unittest tests.test_feature_flow tests.test_orchestrator_skillsrc_bootstrap -v
python tools\test_classification.py feature-flow --help
python -m py_compile tools\feature_flow.py tools\test_classification.py tests\test_feature_flow.py
git diff --check
```
Expected: tests pass; help shows the exact public flags and no mode/count/reviewer/shard/case tuning flags.
- [ ] **Step 5: Commit**
```powershell
git add tools/feature_flow.py tools/test_classification.py tests/test_feature_flow.py
git commit -m "feat: expose resumable feature flow"
```
---

### Task 9: Pipeline 6 Registry, Skills, Doctor, Renderer, and Public Docs

**Files:**
- Modify registry/schema/tooling: `contracts/pipeline.json`, `schemas/pipeline.schema.json`, `schemas/orchestrator-output.schema.json`, `tools/contract_check.py`, `tools/doctor.py`, `tools/render_contract_docs.py`.
- Create change-scope skill: `skills/change-scope/SKILL.md`, `skills/change-scope/references/change-scope-contract.md`.
- Modify skill contracts: `skills/context-marker/{SKILL.md,references/context-artifact-contract.md}`, `skills/test-classifier/{SKILL.md,references/classification-contract.md}`, `skills/test-classifier-reviewer/{SKILL.md,references/review-contract.md}`, `skills/tc-generator/{SKILL.md,references/case-generation-contract.md}`, `skills/tc-reviewer/SKILL.md`, `skills/orchestrate/{SKILL.md,references/orchestration-contract.md}`.
- Modify/generated docs: `README.md`, `USAGE.md`, `HOW-IT-WORKS.md`, `PIPELINE.md`, `CONTRACTS.md`; tests: create `tests/test_pipeline_v6.py`, modify `tests/test_documentation_v3.py`, `tests/test_skill_contracts_v3.py`, `tests/test_orchestration_v3.py`.
**Interfaces:**
- Consumes all runtime/schema interfaces from Tasks 1-8.
- Produces the exact ordered Pipeline 6 carrier universe and stage rows in the accepted design. For every stage, `rejects` is materialized as ordered `U - accepts` and verified exactly.
- `finalize_orchestration(effective_document, effective_bundle_receipt, automation_artifact, autotest_review_artifact, run_result, trace_document, trace_audit, prefix_ledger)` additionally emits a terminal-run receipt from accepted tail artifacts plus the immutable validated prefix ledger; `advance_baseline(run, terminal_receipt, predecessor)` accepts only that receipt.
- [ ] **Step 1: Write exact Pipeline 6 routing and documentation RED tests**
```python
def test_pipeline6_exact_carrier_complements_and_branches():
    """Every accepts/forwards/produces/rejects array and FULL/CHANGE_SET branch is exact."""
def test_pipeline6_versions_docs_doctor_renderer_and_legacy_rejection():
    """Versions, runtime files, skills, projections, and Pipeline 5 rejection rows agree."""
def test_changed_context_crosses_classifier_stages_byte_identically():
    """Classifier stages preserve changed context and only it reaches generation."""
def test_terminal_receipt_uses_bound_prefix_ledger_not_hidden_carrier():
    """Missing, foreign, or mutable prefix evidence blocks finalization and advancement."""
```
- [ ] **Step 2: Run RED**
```powershell
python -m unittest tests.test_pipeline_v6 tests.test_documentation_v3 tests.test_skill_contracts_v3 tests.test_orchestration_v3 -v
```
Expected: Pipeline 5 version/stages/carriers and V5 skill copy fail the new exact assertions.
- [ ] **Step 3: Replace the live registry and update every skill contract**
Materialize the 29-carrier ordered universe and 18 exact stage rows from the design. Add closed FULL/nonzero-delta/zero-op transitions, terminal receipt, and baseline advancement. Make `contract_check` compare every array, complement, schema, runtime file, core skill path, and projection.
Skill instructions must direct a plain worker to repeatedly call `feature-flow`, perform only the returned producer/audit/generator action, record it, and call again. State explicitly that tests are technical evidence only, raw inputs never enter generation, both audits are mandatory, `SEQUENTIAL` is portable, and no count/mode/scope tuning exists.
Update public docs to lead with initial full baseline then change-scoped runs. Render generated docs from the contract rather than editing their tables by hand.
- [ ] **Step 4: Run focused GREEN, doctor, checker, and renderer**
```powershell
python -m unittest tests.test_pipeline_v6 tests.test_documentation_v3 tests.test_skill_contracts_v3 tests.test_orchestration_v3 -v
python tools\doctor.py --root .
python tools\contract_check.py --root . --full
python tools\render_contract_docs.py --root . --check
python -m py_compile tools\contract_check.py tools\doctor.py tools\render_contract_docs.py tools\orchestrate_test_case_revision.py
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json schemas/project-discovery-output.schema.json schemas/skillsrc-init-output.schema.json tools/discover_project.py tools/init_skillsrc.py tools/skillsrc_manifest.py tools/stack_catalog.py tools/scan_project.py
```
Expected: all checks pass; the protected-file command prints nothing.
- [ ] **Step 5: Commit**
```powershell
git add contracts/pipeline.json schemas/pipeline.schema.json schemas/orchestrator-output.schema.json tools/contract_check.py tools/doctor.py tools/render_contract_docs.py skills/change-scope skills/context-marker skills/test-classifier skills/test-classifier-reviewer skills/tc-generator skills/tc-reviewer/SKILL.md skills/orchestrate README.md USAGE.md HOW-IT-WORKS.md PIPELINE.md CONTRACTS.md tests/test_pipeline_v6.py tests/test_documentation_v3.py tests/test_skill_contracts_v3.py tests/test_orchestration_v3.py
git commit -m "feat: route Pipeline 6 semantic evidence"
```
---

### Task 10: Migration Fixtures, Shadow Runs, and Fixed 24-Control Eval

**Files:**
- Create/modify: `tests/fixtures/stages/v6/**`
- Create: `tests/fixtures/change-scope/project/**`
- Create: `tests/fixtures/document-delta/baseline-document.json`
- Create: `evals/change-scoped-semantic-evidence/scenarios.json`
- Create: `evals/change-scoped-semantic-evidence/rubric.md`
- Create: `schemas/pipeline6-calibration-report.schema.json`
- Create: `tests/test_pipeline6_migration.py`
**Interfaces:**
- Consumes the public `advance_feature_flow` loop and the real schema registry.
- Produces a deterministic synthetic shadow harness covering clean FULL; add/modify/delete/rename/binary; zero-op; scope and batch rework; dirty worktree; patch manifest; committed Git range; both generator branches; fingerprint rejection; and competing successor attempts.
- Produces 24 fixed control IDs: `supported` and `deceptive_or_no_fact` for each of the 12 accepted pilot categories.
- [ ] **Step 1: Write migration and shadow RED tests**
```python
def test_historical_v1_is_nested_full_only_and_v2_is_change_set_only():
    """Loose results and historical receipts are rejected by the live route without mutation."""
def test_pipeline6_shadow_full_and_change_set_matrix():
    """Synthetic Git/patch scenarios produce expected scope, sides, delta, document, and eligibility."""
def test_pipeline6_zero_op_shadow_bypasses_publish_and_review():
    """Unchanged bytes select the predecessor document/bundle and join only at automation."""
def test_fixed_pilot_has_exactly_two_controls_in_each_category():
    """The eval has the accepted 12 categories and known route/README/config regressions."""
def test_no_migration_fabricates_review_or_lineage_artifacts():
    """No adapter synthesizes modes, sides, audits, promotions, tombstones, deltas, terminal receipts, or baselines."""
```
- [ ] **Step 2: Run RED**
Run `python -m unittest tests.test_pipeline6_migration -v`.
Expected: missing V6 fixture matrix, eval corpus, and calibration-report schema.
- [ ] **Step 3: Build the synthetic shadow corpus and one-way compatibility gates**
Use temporary repositories created from `tests/fixtures/change-scope/project/**`; never point the harness at InvenTree. Every scenario records exact expected action order, scope reasons/relations, result side/effect, delta status, final document digest relationship, and baseline eligibility.
The 24-control eval must include the known false-route helper and false-no-fact README/config controls and score all these phases independently:
```text
scope selection, scope false-inclusion, scope omission,
candidate extraction, batch false-claim, batch omission, promotion
```
The safe calibration-report schema stores control ID, expected/actual verdicts, artifact digests, review mode, and overall status only.
- [ ] **Step 4: Run focused GREEN and all migration-only checks**
```powershell
python -m unittest tests.test_pipeline6_migration tests.test_pipeline_v6 tests.test_feature_flow -v
python tools\contract_check.py --root . --full
python tools\doctor.py --root .
python tools\render_contract_docs.py --root . --check
python -m py_compile tests\test_pipeline6_migration.py
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json schemas/project-discovery-output.schema.json schemas/skillsrc-init-output.schema.json tools/discover_project.py tools/init_skillsrc.py tools/skillsrc_manifest.py tools/stack_catalog.py tools/scan_project.py
```
Expected: all pass; no InvenTree process or file read is performed; protected-file command prints nothing.
- [ ] **Step 5: Commit**
```powershell
git add tests/fixtures/stages/v6 tests/fixtures/change-scope tests/fixtures/document-delta/baseline-document.json evals/change-scoped-semantic-evidence schemas/pipeline6-calibration-report.schema.json tests/test_pipeline6_migration.py
git commit -m "test: shadow Pipeline 6 migration"
```
---

### Task 11: InvenTree Attempt-04 Calibration and Final Acceptance

**Files:**
- Create outside repository, create-only: `D:\AI-Projects\pipeline-artifacts\inventree-full-project-2026-08-14-pipeline6-attempt-04/**`
- Create: `evals/change-scoped-semantic-evidence/attempt04-acceptance.json`
- Create: `tests/test_attempt04_acceptance.py`
- Do not modify: `D:\AI-Projects\InvenTree-master/**`, any attempt-02/03 artifact, or any protected automatic-discovery file.
**Interfaces:**
- Consumes only the committed Tasks 1-10 implementation, the clean committed InvenTree tree, its automatically discovered `.skillsrc` V3 identity, and the fixed 24-control eval.
- Produces an immutable clean-commit FULL baseline, reviewed attempt-04 evidence under the exact new root, and a safe checked-in calibration summary validated by `pipeline6-calibration-report.schema.json`.
- No prior attempt batch result is accepted as candidate, audit, promotion, context, delta, terminal receipt, or baseline evidence.
- [ ] **Step 1: Write the acceptance RED test before running InvenTree**
```python
def test_attempt04_fixed_24_control_pilot_blocks_any_mismatch():
    """Exactly 24 controls pass every phase in SEQUENTIAL mode and bind immutable digests."""
def test_attempt04_uses_new_root_and_no_prior_attempt_authority():
    """The summary binds the new root and contains no attempt-02/03 promoted digest."""
```
The test validates the checked-in report schema, exact control IDs, seven accepted phase verdicts per control, `review_mode == "SEQUENTIAL"`, zero mismatches, exact FULL baseline eligibility, and digest readback for every named attempt-04 artifact.
- [ ] **Step 2: Run RED without touching InvenTree**
Run `python -m unittest tests.test_attempt04_acceptance -v`.
Expected: failure because `evals/change-scoped-semantic-evidence/attempt04-acceptance.json` does not exist. This is the first and only pre-calibration check; it must not invoke the project.
- [ ] **Step 3: Freeze the real target and run the complete portable action loop**
First verify `D:\AI-Projects\InvenTree-master` is a clean committed Git tree. If dirty, non-Git, ambiguous, or changed during the run, stop with safe evidence and do not claim acceptance.
Use the exact new root:
```powershell
python tools\test_classification.py feature-flow --project D:\AI-Projects\InvenTree-master --analytics D:\AI-Projects\pipeline-artifacts\inventree-full-project-2026-08-13-raw-sol-high\00-project-bootstrap\attempt-01\skillsrc-init.json --run-root D:\AI-Projects\pipeline-artifacts\inventree-full-project-2026-08-14-pipeline6-attempt-04
```
The controller then performs the returned action, writes only the requested candidate or audit JSON, records it with the same command plus `--record <exact-returned-path>`, and repeats until `COMPLETE` or `BLOCKED`. Use mandatory `SEQUENTIAL`; optional trusted independent calibration may run only as an additional non-authoritative comparison. Process all 24 fixed controls through both scope audits and both batch audits. Do not publish test artifacts or advance a baseline until every terminal gate accepts.
- [ ] **Step 4: Materialize and validate the safe acceptance report**
Create `evals/change-scoped-semantic-evidence/attempt04-acceptance.json` from canonical readback digests, not manually copied claims. It must contain exactly 24 controls, the clean target commit/tree, baseline receipt digest, terminal receipt digest, per-phase artifact digests/verdicts, `SEQUENTIAL` assurance, zero mismatch count, and overall `PASS`.
Run:
```powershell
python tools\validate_artifact.py schemas\pipeline6-calibration-report.schema.json evals\change-scoped-semantic-evidence\attempt04-acceptance.json
python -m unittest tests.test_attempt04_acceptance -v
```
Expected: schema validation and both attempt-04 tests pass. Any mismatch leaves the attempt immutable, returns failure, and prevents baseline advancement.
- [ ] **Step 5: Run final acceptance, including the complete suite exactly once**
```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tools\contract_check.py --root . --full
python tools\doctor.py --root .
python tools\render_contract_docs.py --root . --check
python -m compileall -q tools tests
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json schemas/project-discovery-output.schema.json schemas/skillsrc-init-output.schema.json tools/discover_project.py tools/init_skillsrc.py tools/skillsrc_manifest.py tools/stack_catalog.py tools/scan_project.py
git -C D:\AI-Projects\InvenTree-master status --short
```
Expected: the one full discovery and every deterministic check pass; both protected-file/status commands print nothing. Inspect `git diff --stat` and `git diff` to confirm only planned files changed and no raw/secret content entered fixtures, evals, or reports.
- [ ] **Step 6: Commit the calibration evidence**
```powershell
git add evals/change-scoped-semantic-evidence/attempt04-acceptance.json tests/test_attempt04_acceptance.py
git commit -m "test: accept Pipeline 6 attempt 04"
```
Record the final implementation SHA and the content-addressed external attempt root in the task handoff. Do not commit, push, or modify InvenTree.
---

## Requirement-to-Task Acceptance Map

| Accepted design area | Owning task(s) |
|---|---|
| Change record/patch unions, Git range, worktree freeze, safety | 1 |
| FULL/provisional rules, terminal receipt, fingerprints, durable chain | 2 |
| Diff seed, impact registry, widening, complete test inventory, scope audits | 3 |
| Plan/Result V2 sides, ordering, effects, deletion, rename/no-op | 4 |
| FULL/V1 vs CHANGE_SET/V2, dual batch audits, rework, assurance | 5 |
| Promoted composite context, unchanged bindings, retirement support | 6 |
| Delta partitions/tombstones/applier, zero/nonzero review routes | 7 |
| One facade, automatic module/mode, action loop, persistence/resume/errors | 8 |
| Exact Pipeline 6 carriers/branches/versions/skills/docs/tooling | 9 |
| One-way migration, synthetic shadows, 24-control fixed eval | 10 |
| Real clean-tree attempt-04, one full suite, final evidence | 11 |

## Execution Discipline

Execute Tasks 1-11 in order. A single inline worker is sufficient and is the portability baseline. If subagents are available, use them only to implement one current task at a time; the primary worker reviews the diff, runs that task's focused checks, and accepts its commit before starting the next task. Never run two implementation tasks concurrently because they share schemas, facade types, and live Pipeline contracts.
