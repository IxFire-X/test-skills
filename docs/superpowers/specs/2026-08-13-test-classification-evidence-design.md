# Managed-scenario and technical-test classification design

**Status:** Proposed after user approval of the natural-owner model

**Date:** 2026-08-13

**Branch:** `codex/adaptive-test-case-granularity`

## 1. Problem

Project discovery currently exposes product behavior and existing test symbols in one body of evidence. A language model can therefore mistake each unit-test function, assertion path, route declaration, or helper for a new Zephyr test case.

The InvenTree calibration demonstrated both failure modes:

- 24 broad requirements became only 12 umbrella cases;
- decomposing raw source evidence produced 11,155 case candidates;
- grouping by runnable test symbol still produced 1,669 candidates, although a test symbol is not necessarily a functional scenario.

The pipeline needs two distinct concepts:

1. **Managed scenarios** describe observable product behavior and may become canonical/Zephyr cases.
2. **Technical test evidence** describes runnable project tests and may support coverage without creating cases.

`unit` versus `integration` is a test-scope axis. `manual` versus `automated` is scenario intent. Runtime PASS/FAIL/NOT_RUNNABLE is a third axis. They must not be collapsed.

## 2. Goals

- Existing unit tests never create canonical or Zephyr cases by themselves.
- Case count follows semantic scenario boundaries, not project size, assertion count, file count, domain count, or a quota.
- Existing integration/end-to-end tests can later satisfy a case only through explicit reviewed operation/assertion relations.
- Unit and unknown evidence remains visible but never becomes authoritative functional coverage.
- The LLM contract stays small; inventory, identity, origin, ordering, linkage, and derived-status rules live in one deep module.
- No per-project taxonomy tuning is required.
- Classification uncertainty is preserved explicitly instead of repaired by a heuristic.

## 3. Non-goals

- No minimum, maximum, target, per-domain quota, or numeric case-count setting.
- No filename-only or framework-only rule that silently upgrades `unknown`.
- No conversion of each assertion, route declaration, test method, or source branch into a case.
- No canonical field claiming a case is implemented or passing before relations/evidence exist.
- No change to the locked 24-column `zephyr-scale-step-row-24-v1` projection in phase 1.
- No TypeScript/Go execution support in this change.

## 4. Natural ownership

| Fact | Owner | Authored or derived |
|---|---|---|
| observable behavior and outcome | managed behavior context/canonical case | authored by behavior workflow |
| scenario boundary | scenario key | authored, semantically reviewed |
| existing test scope | technical classification | one LLM choice, independently reviewed |
| implementation origin | inventory or automation artifact ownership | mechanically derived |
| operation/assertion implementation | atomic automation relation | authored, validated and reviewed |
| manual/automated/mixed intent | canonical `manual_only` values | mechanically derived |
| automation coverage | portfolio analysis | mechanically derived |
| ready/blocked | canonical blockers | mechanically derived |
| runtime result | current execution evidence | observed |

The canonical document remains focused on human test design. It stores neither implementation origin nor actual automation status.

## 5. Pipeline separation and versions

The coordinated registry version is **Pipeline 4.0**. Pipeline 3.0 remains reserved for the separately designed Excel semicolon companion bundle, so unrelated breaking changes do not share a version.

Phase 1 routes artifacts as follows:

```text
project + .skillsrc + supplied inputs ─► inventory tool
       ├─► technical_test_inventory
       └─► authorized_behavior_sources

raw context + both inventories ─► context-marker 4.0.0
       ├─► managed_behavior_context ───────────────────────► tc-generator 3.0.0
       └─► technical_test_inventory ─► test-classifier 1.0.0
                                      └─► classifier-reviewer 1.0.0

tc-generator ─► candidate/effective canonical document (unchanged V3 shape)
```

Independent artifact schemas retain their own versions: new inventory/classification artifacts start at `1.0.0`; the breaking context-marker envelope becomes `4.0.0`; unchanged canonical/generator outputs remain `3.0.0`. This is not legacy mixing: Pipeline 4.0 declares the exact allowed version for every route and rejects all other combinations.

The structural guard is decisive: `tc-generator` receives neither raw `source_code_and_diff`, test inventory, test source, nor test classification. It receives only `managed_behavior_context`.

Every managed requirement must cite at least one supplied requirement or non-test product file from `authorized_behavior_sources`. Test-only evidence may supplement a behavior but cannot originate it. Product-file provenance whose path appears in the test inventory is rejected.

Accepted classification is durably retained beside the attempt but does not enter automation, trace, or final-orchestrator carriers in phase 1. Phase 2 introduces that complete downstream route atomically.

## 6. Minimal LLM contract

The LLM performs three semantic tasks:

1. Extract observable product behaviors independently of the test inventory.
2. For every mechanically inventoried test symbol, choose one scope:
   - `unit`: directly exercises an internal function, class, or module without crossing a product boundary;
   - `integration`: crosses a real component, persistence, service, repository, or API boundary inside the project;
   - `e2e`: exercises a complete user/client journey through an externally observable entry point;
   - `unknown`: evidence is insufficient.
3. In phase 2, propose explicit operation/assertion links for reviewed integration/e2e symbols.

For each scope choice it supplies exact source provenance and one short rationale. It does not choose origin, execution mode, readiness, automation coverage, or a case count.

When uncertain it must emit `unknown`. Deterministic code never upgrades the choice.

## 7. Mechanical test inventory

`tools/test_classification.py` builds `technical_test_inventory` before any generation. It scans only the selected `.skillsrc` module's declared test roots using supported static Python/pytest and Java/JUnit locators; it starts no project process and imports no project code.

Completeness means: every statically discoverable supported test symbol under those declared roots at that snapshot is present exactly once. Dynamically synthesized runtime cases are not separate symbols; they remain executions of their owning locator.

No declared test roots produces a valid empty inventory; roots are never guessed. If a supported test declaration is found but cannot be represented unambiguously by the closed locator union (for example an ambiguous Java overload), inventory fails with `INVENTORY_UNSUPPORTED_LOCATOR` rather than silently omitting it.

The closed inventory contains:

- selected `module_id` and normalized project-relative test roots;
- `files[]`: stable `file_id`, portable path, language, framework, and full-file lowercase SHA-256;
- `symbols[]`: `(file_id, symbol_id)` plus the existing closed Python/Java locator union;
- the envelope carries sibling `technical_test_inventory_sha256`, computed over the bare inventory object only.

Multiple symbols may reference one file. Physical path uniqueness applies to `files[]`; pair uniqueness applies to `symbols[]`. Ordering is canonical and never repaired by consumers.

The classifier cannot add, remove, rename, reparent, or alter inventory entries. Validation re-reads file bytes before review selection, relation creation, and execution; drift is a hard error.

## 8. Classification and review

The closed `test-classifier` envelope carries the exact `technical_test_inventory_sha256` and one row per inventory symbol:

- `file_id`, `symbol_id`;
- `test_scope: unit | integration | e2e | unknown`;
- sorted zero-or-more `requirement_ids` as non-authoritative supporting links;
- nonempty exact source provenance;
- a short classification rationale.

It has no origin field. Inventory membership means `existing`; a symbol introduced later by `tc-to-autotest` means `generated`.

An independent `test-classifier-reviewer` reads source, inventory, requirements, and candidate classification. Its closed result contains:

- exact `technical_test_inventory_sha256` from the inventory envelope;
- `classification_sha256` over the candidate classification's bare canonical bytes;
- `reviewed_symbol_pairs[]`, exactly equal to all inventory pairs in canonical order;
- `verdict`;
- closed `findings[]` with `path`, `code`, `message`, and optional exact `file_id`/`symbol_id` ownership.

It reviews every symbol pair exactly once and returns only:

- `ПРИНЯТО`: select the classification unchanged;
- `ТРЕБУЕТ ДОРАБОТКИ`: stop and return findings to the classifier.

`ПРИНЯТО` requires exact digest agreement, full reviewed-pair coverage, and empty findings. `ТРЕБУЕТ ДОРАБОТКИ` requires the same full coverage and at least one finding. Duplicate, missing, extra, or reordered reviewed pairs are invalid. There is no auto-fix. Semantic scope cannot be proven by JSON Schema or a filename rule; independent review owns that judgment. Acceptance proves reviewed agreement, not mathematical truth.

Missing/extra classifications, incomplete review coverage, duplicate IDs/pairs/paths/locators, stale snapshot/file digests, foreign requirements, unsafe paths, noncanonical order, or missing rationale/provenance are errors.

## 9. Managed behavior context

Before context analysis the inventory tool creates a closed `authorized_behavior_sources` snapshot. It mechanically enumerates:

- each exact supplied requirement/input block as `kind: supplied_requirement`, with controller-assigned `source_id` and SHA-256 of its exact UTF-8 bytes;
- every eligible file under the selected module's `.skillsrc` `feature_sources` and `paths.source` roots as `kind: product_file`, with portable path and mandatory full-file SHA-256.

Declared test roots, every path present in `technical_test_inventory.files`, and supported test-file patterns are subtracted. Unsafe paths, overlaps that cannot be resolved, missing files, and byte drift are hard errors. The envelope carries sibling `authorized_behavior_sources_sha256`, computed over the bare source snapshot only.

`managed_behavior_context` is closed and contains:

- normalized canonical requirement records;
- `authorized_behavior_sources_sha256` matching the sibling digest in the inventory envelope;
- `product_sources[]` with an authorized stable source ID, kind, exact location, mandatory content digest, and concise behavior summary;
- per-requirement sorted `source_ids`.

It contains no file/symbol registry and no assertion inventory. Every source must match an authorized entry byte-for-byte. For every requirement at least one referenced source must be a supplied requirement or a product file absent from the test inventory. Invented paths, relabeled test facts, unauthorized inputs, and stale digests fail before `tc-generator` runs.

The generator then applies the approved scenario key:

`setup/role + initial state + input partition/branch condition + primary action or cohesive dependent action chain + terminal outcome`.

One independently executable scenario becomes one case; assertions remain expectations inside that case.

## 10. Deep module interface

All cross-artifact policy lives in `tools/test_classification.py`. Skills describe intent; schemas close shape; consumers call this facade and do not rebuild its joins.

```python
build_source_inventories(
    project_root,
    skillsrc,
    module_id,
    supplied_inputs,
) -> SourceInventories

validate_managed_behavior_context(
    behavior_context,
    authorized_behavior_sources,
    test_inventory,
    project_root,
) -> tuple[Diagnostic, ...]

validate_technical_test_evidence(
    inventory,
    classification,
    classification_review,
    requirements,
    project_root,
) -> tuple[Diagnostic, ...]

analyze_test_portfolio(
    canonical_document,
    effective_technical_evidence,
    automation_artifact,
    project_root,
) -> PortfolioAnalysis
```

The first three interfaces ship in phase 1. `analyze_test_portfolio` and its `PortfolioAnalysis` type ship atomically with the neutral automation registry in phase 2; phase 1 does not provide a stub.

All return objects and diagnostics are recursively immutable. Inventory and selected evidence have deterministic bare-byte serializers and lowercase SHA-256 identities.

The module proves both inventory scopes within declared roots, exact supplied-input/file bytes, identities, order, review coverage, and cross-artifact relations. It does not pretend static code can prove semantic scope.

## 11. Linking and authority rules

- A technical symbol never creates a requirement or case.
- Unit and unknown symbols may support technical requirement coverage but cannot be authoritative operation/assertion targets.
- Integration and e2e symbols may be authoritative only after accepted classification and explicit atomic relations with valid parent chains.
- Generated origin is not an exception: a generated unit/unknown symbol is non-authoritative and therefore cannot satisfy a managed case.
- Relation review must establish that the symbol actually crosses the canonical operation boundary; matching names are not evidence.
- Multiple symbols for one target retain AND semantics.
- One symbol covers multiple targets only through multiple explicit relations; no Cartesian inference exists.
- Missing links are never repaired from comments, names, or proximity.

## 12. Derived portfolio status

Execution intent comes only from canonical step state:

- `manual`: every step has `manual_only=true`;
- `automated`: every step has `manual_only=false`;
- `mixed`: both kinds occur.

Automation coverage is separate:

- `not_applicable`: no automation-intended step exists;
- `complete`: every ready operation and required assertion has authoritative relations;
- `blocked`: a canonical blocker prevents complete implementation or execution;
- an incomplete relation set for a ready case is invalid, not a fourth state.

Readiness is independently `ready | blocked` from canonical blockers. A case may therefore be `automated + blocked` without claiming implementation or successful execution. PASS/FAIL/NOT_RUNNABLE remains current-run evidence only.

## 13. Phase 2 automation migration

Phase 2 is **Pipeline 5.0**. It routes accepted technical evidence into automation, trace, and final carriers and neutralizes provenance-loaded names:

- `generated_files` becomes `files`;
- `generated_symbols` becomes `symbols`;
- `automation_status: GENERATED|BLOCKED` becomes `automation_status: READY|BLOCKED`;
- every symbol carries derived `implementation_origin: existing|generated` and reviewed `test_scope`;
- relation shape and `(file_id, symbol_id)` identity stay unchanged.

The runner continues to execute confined project-relative files by verified digest and locator. It does not branch on origin. Trace/reviewer schemas consume the same normalized registry.

This is one coordinated breaking migration. No phase-1 sidecar is routed into old V3 automation/trace carriers.

Execution has two isolated lanes:

- `managed`: only authoritative relation pairs; its evidence determines managed functional coverage;
- `technical_regression`: all existing inventory pairs; its evidence is supporting and can never satisfy a managed operation/assertion.

The technical-regression lane retains byte/path/locator/environment preflight but does not apply canonical operation/provider preflight. It is optional for an ordinary feature run and requested explicitly for a full-project run; this is an execution-scope choice, never a test-count setting.

Finalization exposes `managed_status`, optional `technical_regression_verdict`, and `overall_status`. A present technical FAIL makes `overall_status=FAIL`; a present technical NOT_RUNNABLE makes it `NOT_RUNNABLE` unless managed status is already FAIL; technical PASS leaves the managed status unchanged. An omitted technical run has no effect. Unit PASS never upgrades managed status.

## 14. Failure behavior

- Schema or semantic error: stop before publication or relation creation.
- Scope uncertainty: persist `unknown`; reviewer checks that uncertainty was handled honestly.
- Unit/unknown symbol used as authoritative relation: reject at the relation path.
- Snapshot/classification mismatch or changed bytes after snapshot: reject before review selection, relation creation, or subprocess.
- Unauthorized/invented behavior source, test path relabeled as product source, or behavior-source byte drift: reject before generation.
- File digest/locator mismatch: reject before subprocess.
- Missing product behavior for a symbol: retain unlinked technical evidence; create no case.
- Behavior with no executable expected outcome: use an explicit coverage-only reason or canonical blocker; never fill the gap from a test symbol.

## 15. Delivery phases

### Phase 1 — separate classification from case generation

- add inventory/classification/review schemas, module, skills, fixtures, and focused tests;
- add the authorized behavior-source snapshot and strict provenance gate;
- change context-marker to emit validated `managed_behavior_context`;
- route only that context to generator;
- preserve canonical schema and projection bytes;
- persist accepted technical evidence outside V3 automation/trace/final carriers;
- repeat InvenTree inventory and case generation.

This phase fixes the test-symbol-driven case explosion.

### Phase 2 — reuse and execute existing tests

- migrate the automation registry and downstream schemas together;
- allow reviewed integration/e2e symbols to receive atomic relations;
- run related existing symbols through the normal runner;
- run the full technical regression inventory as a separate non-authoritative lane when full-project execution was requested;
- keep supporting and authoritative results distinct in trace/final artifacts.

Phase 2 does not change case-generation granularity.

## 16. Verification strategy

Classification errors distort both inventory and runtime truth, so this change receives focused boundary testing plus one broad acceptance pass—not an unbounded permutation exercise.

### Focused schema/semantic tests

- positive records for all four scopes;
- closed-shape missing/extra/wrong-type mutations;
- multiple symbols in one file;
- duplicate IDs/pairs/paths/locators, missing/extra classifications, foreign requirements, unsafe paths, and noncanonical order;
- stale snapshot digest and changed file bytes;
- complete reviewer pair coverage and both verdict branches;
- invented product path, relabeled test path, supplied-input digest mismatch, and behavior-source file drift;
- immutable diagnostics and analysis objects;
- exact rejection of incompatible stage versions.

### Boundary invariants

- ten unit symbols plus one observable behavior still produce one managed scenario;
- twenty assertions in one native flow do not multiply cases;
- two independently executable branches produce two scenario keys;
- a test-only symbol with no product-source behavior produces evidence and zero cases;
- unit, unknown, and generated-unit authoritative relations fail;
- reviewed integration/e2e relations pass only with exact operation/assertion coverage;
- blocked non-manual cases derive `automated + blocked` without claiming coverage;
- technical-regression FAIL/NOT_RUNNABLE changes overall status but technical PASS never closes managed coverage;
- manual, automated, mixed, coverage, readiness, and runtime status remain independent.

### Mutation and compatibility tests

- mutate one scope, relation, digest, locator, parent, and review pair at a time and assert stable code/path/message;
- mutate one authorized behavior source and reviewer/classification digest at a time;
- prove `tc-generator` has no route/input carrying raw test source, inventory, or classification;
- prove unchanged canonical JSON/Markdown/Zephyr CSV bytes remain byte-identical;
- run canonical, publisher, automation, runner, trace, orchestration, skill-contract, docs, doctor, contract-check, and one full repository suite before acceptance.

### Real InvenTree calibration

Run once in a clean worktree:

- snapshot all statically discoverable supported test symbols;
- verify inventory and accepted classification cover exactly the same pairs;
- prove no symbol becomes a case solely because it exists;
- generate only observable scenarios using scenario keys;
- represent every discovered product behavior exactly once as a case or coverage-only reason;
- inspect a deterministic sample from each major backend domain and scope, plus every `unknown` when few;
- perform one fresh independent audit of the ledger and case inventory.

The calibration has no expected numeric case count. Acceptance is based on semantic uniqueness, completeness, no unit promotion, stable provenance, and zero invented coverage.

## 17. Acceptance criteria

- An ordinary LLM follows the three-task contract without project-specific settings.
- Existing unit-test count has no direct effect on canonical case count.
- Inventory proves every discovered supported symbol was classified exactly once against stable bytes.
- Every symbol has an independently reviewed scope or explicit `unknown`.
- Only explicit reviewed integration/e2e relations are authoritative, regardless of origin.
- Execution intent, automation coverage, readiness, and runtime result are derived separately.
- Classification/portfolio policy exists in one public module and is not duplicated in skills, runner, or trace code.
- Focused, mutation, compatibility, one full-suite, and InvenTree calibration checks pass with recorded evidence.
