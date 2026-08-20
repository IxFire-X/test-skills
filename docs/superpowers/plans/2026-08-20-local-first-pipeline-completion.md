# Local-First Pipeline Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Pipeline 6 run from `project + analytics + output` whether or not the project uses Git, while retaining automatic Git-backed baselines and later change-scoped runs.

**Architecture:** Keep `tools/feature_flow.py` as the only semantic-prefix façade and `tools/pipeline6_tail.py` as the tail. A local FULL run binds one digest of the exact inventories, completes as `PROVISIONAL`, and never creates a baseline. Git remains an optional optimization for durable baselines and `CHANGE_SET`; no virtual filesystem, local revision service, capability class, or automatic Git mutation is added.

**Tech Stack:** Python 3.12 stdlib, existing JSON Schema/jsonschema validation, existing Pipeline 6 modules.

**Spec:** `docs/superpowers/specs/2026-08-14-change-scoped-semantic-evidence-design.md`

## Global Constraints

- `.skillsrc` is created through existing `ensure_skillsrc`; pipeline code never runs `git init`, `git add`, or `git commit`.
- `advance_feature_flow` accepts optional exact `module_id`; inference is allowed only when unique.
- No Git executable or repository is required for a local FULL run.
- Local and dirty snapshots are `PROVISIONAL`; only a clean committed Git FULL may create a durable baseline.
- Store one `source_snapshot_sha256`; do not add a local revision abstraction or persist raw source bytes.
- Existing FULL/CHANGE_SET candidate, audit, promotion, delta, tail, and baseline contracts remain authoritative.
- Do not run RED checks. Run only the named focused GREEN commands.
- Do not touch attempts 02/03. Do not create attempt-04 until Tasks 1-3 are GREEN and reviewed.
- Do not commit, stage, push, or publish without separate explicit authorization.

---

### Task 1: Local FULL, Exact Module Selection, and Evidence Readback

**Files:**
- Modify: `tools/feature_flow.py`
- Modify: `tools/test_classification.py`
- Modify: `tests/test_feature_flow.py`

**Interfaces:**
- Produces:

```python
def advance_feature_flow(
    project: Path,
    analytics: Path,
    run_root: Path,
    baseline_receipt: Path | None = None,
    change_input: ChangeInputSpec | None = None,
    recorded_artifact: Path | None = None,
    controller: ReviewController | None = None,
    *,
    blob_resolver: BlobResolver | None = None,
    module_id: str | None = None,
) -> FeatureFlowAction: ...

def read_feature_flow_evidence(
    project: Path,
    run_root: Path,
    action: FeatureFlowAction,
    item_id: str,
    side: str | None = None,
    *,
    change_input: ChangeInputSpec | None = None,
    blob_resolver: BlobResolver | None = None,
) -> bytes: ...
```

- CLI adds only `--module <exact-id>` and passes it through.
- `feature-flow-input` adds `source_snapshot_sha256`; `repository_id` and `source_revision` may be null only for a local FULL.
- Batch actions bind the persisted context plan and source inventory in `prerequisites`; CHANGE_SET actions also bind the frozen change input.

- [ ] **Step 1: Add focused GREEN scenarios**

Add tests that exercise only public interfaces:

```python
def test_non_git_project_auto_creates_skillsrc_and_enters_full():
    action = advance_feature_flow(project, analytics, run)
    assert action.kind == "PRODUCE_CHANGE_SCOPE"
    flow = read_json(run / "feature-flow/prefix/000000-flow-input.json")
    assert flow["run_mode"] == "FULL"
    assert flow["repository_id"] is None
    assert flow["source_revision"] is None
    assert flow["durability"] == "PROVISIONAL"
    assert_digest(flow["source_snapshot_sha256"])

def test_multimodule_project_uses_only_explicit_exact_module():
    assert advance_feature_flow(project, analytics, run).kind == "BLOCKED"
    action = advance_feature_flow(project, analytics, run2, module_id="root")
    assert action.kind == "PRODUCE_CHANGE_SCOPE"

def test_batch_action_and_reader_supply_exact_verified_range():
    action = replay_to_first_batch(project, analytics, run)
    item = action.artifact["prompt"]["items"][0]
    data = read_feature_flow_evidence(project, run, action, item["item_id"])
    source = (project / item["path"]).read_bytes()
    assert data == source[item["read_range"]["start"]:item["read_range"]["end"]]
```

The non-Git fixture must have no `.git` directory and must not mock subprocess Git calls.

- [ ] **Step 2: Replace unconditional Git proof with one optional probe**

In `tools/feature_flow.py`, use one helper that returns `None` only for “not a Git repository / Git unavailable” and otherwise returns the exact repository/HEAD/tree/status projection. Other Git errors stay safe `FEATURE_FLOW_INPUT` failures. Compute inventories before freezing the manifest, then compute:

```python
source_snapshot_sha256 = artifact_sha256({
    "selected_module": module["id"],
    "skillsrc_sha256": skillsrc_sha256,
    "authorized_behavior_sources_sha256": inventories.authorized_behavior_sources_sha256,
    "technical_test_inventory_sha256": inventories.technical_test_inventory_sha256,
})
```

Clean committed Git uses the existing blob snapshot reader and `DURABLE`; local/current filesystem bytes use `PROVISIONAL`. Existing worktree, patch, and git-range checks remain unchanged.

- [ ] **Step 3: Thread exact module selection through all replays**

Change `_module(project, changed_paths, module_id)` to call existing normalized `.skillsrc` selection. Every recursive `advance_feature_flow(...)` call must pass `module_id=module_id`; the CLI adds `feature_flow.add_argument("--module")` and forwards it. Unknown, non-containing, or ambiguous selection returns safe `BLOCKED`.

- [ ] **Step 4: Expose the selected batch and one verified reader**

For each batch candidate/audit action, add exact `context_plan`, `source_inventory`, `flow_input`, and optional `change_input` bindings to `prerequisites`, and place the selected batch's closed `items` in the prompt. Implement `read_feature_flow_evidence` by reopening those bindings with existing canonical readback helpers, finding exactly one item, resolving its source from current inventory or frozen change side, verifying full content digest and half-open read range, then returning only that range. Git range/worktree sides use the bound Git snapshot or current digest-checked file. Patch sides require the host to repeat its original `ChangeInputSpec` and `BlobResolver`; the reader reacquires and digest-compares that input before resolving one controller blob. Do not store the returned bytes.

- [ ] **Step 5: Run focused GREEN**

Run:

```powershell
uv run --python 3.12 --with jsonschema --with pyyaml python -m unittest tests.test_feature_flow -q
uv run --python 3.12 --with jsonschema --with pyyaml python -m py_compile tools/feature_flow.py tools/test_classification.py tests/test_feature_flow.py
git diff --check
```

Success: all feature-flow tests pass; local fixture never invokes Git; only the three owned files plus the approved spec/plan are changed.

---

### Task 2: Provisional Local Tail Without a Baseline

**Files:**
- Modify: `tools/pipeline6_tail.py`
- Modify: `tools/baseline_lifecycle.py`
- Modify: `schemas/terminal-run-receipt.schema.json`
- Modify: `tests/test_pipeline6_tail.py`
- Modify: `tests/test_baseline_lifecycle.py`

**Interfaces:**
- Consumes `feature-flow-input.source_snapshot_sha256`, nullable Git identity, and the existing prefix ledger.
- Produces an ordinary terminal receipt plus `baseline-advancement.status == "PROVISIONAL"` with null successor fields for a local FULL.
- Durable Git and provisional Git worktree/patch branches remain byte-compatible except for the new required snapshot digest.

- [ ] **Step 1: Add one local-tail GREEN scenario**

```python
def test_non_git_full_tail_completes_provisionally_without_baseline_writes():
    action = advance_pipeline6_tail(project, run, baseline_root, fingerprints=current())
    assert action.kind == "PROVISIONAL"
    assert action.artifact["baseline_advancement"]["status"] == "PROVISIONAL"
    assert action.artifact["baseline_advancement"]["successor_baseline_receipt"] is None
    assert not (baseline_root / "receipts").exists()
    assert replay_same_call(action).artifact == action.artifact
```

- [ ] **Step 2: Close the minimal local terminal variant**

Add `source_snapshot_sha256` to the prefix ledger and terminal receipt. Permit `repository_id:null` and `change_input:null` only when `run_mode == "FULL"`; require both to be nonnull for existing Git-backed variants. `pipeline6_tail._manifest` copies the digest from the flow input and never fabricates Git values.

- [ ] **Step 3: Return before Git baseline logic**

In `build_terminal_run_receipt`, validate the local FULL conjunction and bind the snapshot. In `advance_baseline`, after terminal schema/readback validation and before repository resolution, return the existing closed `_advancement("PROVISIONAL", predecessor_digest)` for null Git identity. Do not create baseline payload directories, receipts, or links.

- [ ] **Step 4: Run focused GREEN**

Run:

```powershell
uv run --python 3.12 --with jsonschema --with pyyaml python -m unittest tests.test_pipeline6_tail tests.test_baseline_lifecycle -q
uv run --python 3.12 --with jsonschema --with pyyaml python -m py_compile tools/pipeline6_tail.py tools/baseline_lifecycle.py tests/test_pipeline6_tail.py tests/test_baseline_lifecycle.py
uv run --python 3.12 --with jsonschema --with pyyaml python tools/contract_check.py --root . --full
git diff --check
```

Success: local tail is PROVISIONAL with no baseline writes; durable/provisional Git tests remain GREEN; contract check passes.

---

### Task 3: Bound Attempt Report

**Files:**
- Modify: `tests/test_pipeline6_calibration.py`
- Create: `tests/test_attempt04_acceptance.py`

**Interfaces:**
- `attempt-root.json` is a closed safe manifest; `attempt_root_sha256 == artifact_sha256(manifest)`.
- The Task 4 controller executes the existing 24 controls through the public action loop; deterministic code only validates the resulting immutable chain and never derives actual verdicts from expected fields.
- Acceptance test receives one explicitly authorized attempt root through `PIPELINE6_ATTEMPT_ROOT`; it never scans attempts 02/03.

- [ ] **Step 1: Validate the smallest closed attempt manifest**

The acceptance helper requires keys exactly `schema_version`, `artifact`, `suite`, `root_name`, `implementation_sha`, `target`, and `review_mode`. `target` contains only repository digest, commit, tree, module ID, and `.skillsrc` digest. Validate this directly in the acceptance test; do not add a schema or registry entry for a single release-only manifest. No absolute paths or raw source are allowed.

- [ ] **Step 2: Keep report validation mechanical and strict**

Extend `tests/test_pipeline6_calibration.py` only with readback-neutral validation: exact 24 control IDs/order, seven phase names/order, arithmetic consistency, `SEQUENTIAL`, and PASS iff every recorded phase matched. It must not invoke an LLM, manufacture candidate/audit artifacts, or copy expected verdicts into actual verdicts.

- [ ] **Step 3: Bind final acceptance to one root**

`tests/test_attempt04_acceptance.py` must:

```python
root = Path(os.environ["PIPELINE6_ATTEMPT_ROOT"]).resolve()
manifest = strict_json(root / "attempt-root.json")
assert artifact_sha256(manifest) == report["attempt_root_sha256"]
```

It recursively inventories canonical JSON only under that resolved root, rejects symlinks and escapes, permits identical content-addressed copies, recomputes every named calibration/terminal/baseline digest, requires 24 controls × 7 matching phases, and checks the exact clean target commit/tree. It never searches any sibling attempt directory.

- [ ] **Step 4: Run focused GREEN**

Run:

```powershell
uv run --python 3.12 --with jsonschema --with pyyaml python -m unittest tests.test_pipeline6_calibration -q
uv run --python 3.12 --with jsonschema --with pyyaml python -m py_compile tests/test_pipeline6_calibration.py tests/test_attempt04_acceptance.py
uv run --python 3.12 --with jsonschema --with pyyaml python tools/contract_check.py --root . --full
git diff --check
```

Success: forged/missing/reordered report evidence fails, schemas/checker pass, and no InvenTree checkout is opened. The 168 semantic phase observations are produced later by the ordinary Task 4 controller, not by test code.

---

### Task 4: Review, InvenTree Attempt-04, and Final Acceptance

**Files:**
- Modify only after successful real execution: `evals/change-scoped-semantic-evidence/attempt04-acceptance.json`
- External create-only artifacts: `D:\AI-Projects\pipeline-artifacts\inventree-full-project-2026-08-14-pipeline6-attempt-04\**`
- External isolated target: `D:\AI-Projects\real-chain-projects\inventree-pipeline6-attempt04-clean`

**Interfaces:**
- Uses the public `advance_feature_flow`, `read_feature_flow_evidence`, and `advance_pipeline6_tail` interfaces only.
- Uses `module_id="root"`, mandatory `SEQUENTIAL`, a separately authorized baseline root, and the fixed calibration gate.

- [ ] **Step 1: Fresh Sol acceptance of Tasks 1-3**

Review the complete diff read-only. Any `fix-first` invalidates the verdict; correct, rerun the focused GREEN checks, and obtain a new fresh review.

- [ ] **Step 2: Bootstrap only `.skillsrc` for the durable acceptance target**

In the already isolated clean InvenTree worktree, call existing `.skillsrc` initialization, validate/read back it, then ask for separate commit authorization before committing that one file. The product pipeline itself performs no Git mutation. Record the resulting clean commit/tree in `attempt-root.json`.

- [ ] **Step 3: Run calibration, then the real FULL action loop**

Create the attempt root once, run the 24-control calibration first, and stop on any mismatch. Then repeatedly call the public façade with `module_id="root"`; for each action, produce exactly the requested artifact using only its prompt and `read_feature_flow_evidence`, write only `record_path`, and resume. After `READY_FOR_PIPELINE_TAIL`, invoke the public tail with an explicit allowlist adapter/provider registry. No raw source or model reasoning enters durable artifacts.

- [ ] **Step 4: Run final acceptance and one complete suite**

Run:

```powershell
$env:PIPELINE6_ATTEMPT_ROOT='D:\AI-Projects\pipeline-artifacts\inventree-full-project-2026-08-14-pipeline6-attempt-04'
uv run --python 3.12 --with jsonschema --with pyyaml python -m unittest tests.test_attempt04_acceptance -q
uv run --python 3.12 --with jsonschema --with pyyaml --with pytest python -m pytest -p no:cacheprovider -q
uv run --python 3.12 --with jsonschema --with pyyaml python tools/doctor.py --root .
uv run --python 3.12 --with jsonschema --with pyyaml python tools/render_contract_docs.py --root . --check
git diff --check
git status --short
```

Success: calibration PASS, terminal receipt and durable baseline receipt read back from attempt-04, full suite passes once, doctor/renderer pass, attempts 02/03 remain untouched, and all changed files are intentional.
