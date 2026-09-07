# Portable Testing Skills Frozen Pilot Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Bring the current portable skill-pack into exact conformance with the frozen
pilot architecture, while preserving its strong canonical/schema/provenance code and
proving each phase with regression and acceptance evidence.

**Architecture:** Keep the compatible model-enabled CLI as the model controller and make
the Python layer a deterministic, durable controller/tool boundary. Deep modules own
state, inventory/baseline, batch assembly, generated delta, closed execution adapters,
and finalization. `tools/run_pipeline.py` becomes a thin facade over those modules; it
does not acquire model credentials or implement an LLM client.

**Tech Stack:** Python 3.11+, JSON Schema draft 2020-12, PyYAML, jsonschema, pytest 9.1,
project-native pytest/Maven/Gradle, Markdown Skills and references.

**Spec:**
[`docs/superpowers/specs/2026-08-26-portable-testing-skills-frozen-pilot-contract.md`](../specs/2026-08-26-portable-testing-skills-frozen-pilot-contract.md)

**Global Constraints:**

- Do not start implementation until the user explicitly approves this plan.
- No commit, push, PR, remote creation, global Git configuration, product-source edit,
  existing-test overwrite, dependency installation, clone/worktree, CI/cron mutation,
  or model credential storage is authorized by this plan.
- Baseline commit `4e09522` is separate from implementation commits. Establish or use it
  only under separate explicit commit authorization; do not claim a meaningful diff
  against a different pre-existing pack.
- Preserve every unrelated byte. Production edits follow a failing test and the narrowest
  implementation needed to pass it.
- Add no LLM SDK, provider client, shell-command template, universal dependency analyzer,
  sandbox framework, plugin SDK, database, queue, or telemetry backend.
- `contracts/pipeline.json` remains machine truth. `CONTRACTS.md` and `PIPELINE.md` remain
  deterministic projections.
- A phase may be internally green without making a readiness claim. Only Phase 8 may
  produce `core-pilot-ready for <exact verified tuple>` evidence.

## Accepted execution amendment

- Baseline commit `4e09522` is separate from implementation commits. Each Phase 0–8 ends
  in one local green commit only after explicit commit authorization.
- Before each phase commit, map every current-phase clause to code and tests; make the
  current and all prior tests green; retain artifact write/readback evidence where the
  phase applies; and confirm no assertion was weakened or failure hidden.
- Before each phase commit, require `git diff --check`, an exact phase-only diff, no
  temporary or cache artifacts, and no push.
- Deliver the minimum Phase 1 orchestration spine and an early authorization receipt
  before extending later lifecycle work.
- Make the Phase 5 automation and static-review lifecycle explicit.
- Treat Phase 8 as completion and qualification of integrated evidence, not the first
  integration point.

## 1. Read-only gap analysis

### 1.1 Scope and method

The analysis covered every current top-level document, `contracts/pipeline.json`, all
schemas, all six Skills and their direct references/assets, all Python tools, release
metadata, runtime entry points, and Git state. No existing project file was edited during
the analysis. The target was then initialized as its own empty Git repository after
explicit user authorization; no commit or remote exists.

Priority meanings:

- **P0:** a conforming frozen-architecture run cannot be represented or safely executed.
- **P1:** a run can produce useful artifacts, but acceptance/readiness evidence is false
  or incomplete.
- **P2:** documentation, maintenance, or ergonomics drift that must close before release.

### 1.2 Existing strengths to preserve

- `schemas/canonical-test-document.schema.json` already models multi-step cases, human
  action/test data/expectations, typed inputs/outputs, assertions, manual states, and
  blockers.
- `tools/canonical_document.py`, `tools/automation_validation.py`, and
  `tools/http_binding_v1.py` provide substantial semantic validation rather than relying
  on schema green alone.
- `tools/publish_test_case_bundle.py` uses confined, atomic publication and deterministic
  readback/digests.
- `tools/revision_selection.py` already permits an unchanged candidate or one complete
  successor and rejects partial selection.
- `tools/run_tests.py` already invokes subprocesses with argv arrays, selects exact
  symbols, parses framework reports, and checks generated-file digests.
- `tools/run_pipeline.py` already writes snapshot/attempt receipts, records an execution
  start marker, and rejects an obvious second execution after terminal-like artifacts.
- Current docs honestly mark real Zephyr tenant round-trip and live company execution as
  unverified.

These components should be adapted, not replaced wholesale.

### 1.3 Gap matrix

| ID | Priority | Frozen requirement | Current evidence | Gap / consequence | Closure |
|---|---|---|---|---|---|
| G01 | P0 | One complete machine-readable frozen protocol | `contracts/pipeline.json:3-30` declares Pipeline 3.0/V4 with ten old linear stages, flat verdicts, a company receipt, and isolated-workspace policy | Machine truth cannot represent run/attempt/event/batch/reviewer-session/generated-delta/disposition/finalization artifacts | Phases 0–1, finalized in Phase 8 |
| G02 | P0 | Run boundary plus sequential child-attempt lineage and one nonterminal attempt | `tools/run_pipeline.py:93-101` creates one directory used as both run and attempt; `tools/run_pipeline.py:512-570` stores parent fields but the CLI never creates a child; `tools/run_pipeline.py:861-893` infers terminal state from file presence | No durable run manifest, journal, state reducer, active-attempt conflict, safe close, or true resume | Phase 1 |
| G03 | P0 | Existing valid `.skillsrc` is authoritative and never silently rewritten | `tools/init_skillsrc.py:127-217` additively merges newly detected fields/modules; `tools/init_skillsrc.py:528-535` can write the updated result automatically. Atomic write at `tools/init_skillsrc.py:285-302` is good | Valid user configuration can change without the required proposal/diff/approval/old-byte evidence | Phase 2 |
| G04 | P0 | Safe whole eligible inventory, separate C-lite selections, complete frozen execution baseline | `tools/run_pipeline.py:296-461` immediately reads source bytes and aborts at 64 files/1 MiB; it has no metadata inventory or secret-file classifier. `tools/run_pipeline.py:935-937` and `tools/run_pipeline.py:1364-1366` reject nested modules | Large/normal projects fail for incidental limits; model scope and execution scope are conflated; baseline omits parent/wrapper/build/config/fixture/dependency inputs | Phase 2 |
| G05 | P0 | Distinct source/canonical requirement IDs, default-single batch plan, one fragment per batch, deterministic assembly | `schemas/context-marker-output.schema.json:17-22` reuses canonical requirement objects; `tools/build_context.py:157-181` requires source and canonical requirement tuples to be identical. No batch/fragment/assembly artifacts exist | Many-to-many provenance and cross-batch invariants cannot be represented or audited | Phase 3 |
| G06 | P0 | One fresh role-isolated reviewer session, bounded C-lite retrieval, exactly one authoritative verdict on success | `schemas/tc-reviewer-output.schema.json:5-76` models only a review envelope/verdict; `skills/tc-reviewer/SKILL.md` describes review quality but no session/evidence-call/cardinality receipt | Freshness, reviewer input completeness, independence, context-limit abort, and verdict cardinality are unprovable | Phase 4 |
| G07 | P0 | Nullable orthogonal lifecycle/completion/verification/coverage/reason/accepted fields and exit 0–3 policy | `schemas/orchestrator-output.schema.json:17-24` collapses facts into `final_status`; `schemas/run-tests-output.schema.json:14` has only PASS/FAIL/NOT_RUNNABLE; `tools/run_pipeline.py:141-146` returns 0 for MANUAL_ONLY regardless of profile | Cases-only and local-pilot policy cannot be reproduced; UNKNOWN/waiting/finalization-invalid states are misclassified | Phase 1, completed in Phase 7 |
| G08 | P0 | Closed versioned project-native adapters, nested-module cwd, ordinary pytest configuration/plugins, run-scoped authorization | `tools/run_pipeline.py:1245-1251` uses unversioned profile strings and `tools/run_pipeline.py:1393` defaults to company execution. `tools/run_tests.py:430-437` disables plugin autoload and `tools/run_tests.py:807-812` injects a host config and `--noconftest` | The current runner contradicts the pilot executor and can neither honor project-native pytest nor prove the frozen authorization/adapter contract | Phase 6 |
| G09 | P0 | Generated file set with per-file materialization and complete disposition receipts | `tools/run_pipeline.py:1001-1187` validates a multi-file commit marker and directly checks Java files, but there is no materialization transaction, baseline-absence proof, partial rollback, or disposition model | FAIL/NOT_RUNNABLE cleanup, UNKNOWN preservation, content conflicts, and RETAINED semantics are not enforceable | Phase 5, completed in Phase 7 |
| G10 | P0 | Linear execution→disposition→pre-trace→finalization→terminal-trace lifecycle | `tools/run_pipeline.py:194-247` builds trace before any disposition and writes `orchestrator.json` directly; `tools/orchestrate_test_case_revision.py:216-300` returns a final artifact but no completed/read-back finalization receipt | Current closure has the trace/finalization cycle and cannot terminalize invalid finalization truthfully | Phase 7 |
| G11 | P1 | Artifact-first event evidence and durable waiting/resume | `_new_run` at `tools/run_pipeline.py:93-101` creates only a guard path; there is no `run-manifest.json`, JSONL journal, WAITING state, model-stage receipt, or time-to-first-useful-artifact projection | Early publication is mostly a Skill instruction, not an enforceable/replayable host contract | Phase 1, acceptance in Phase 8 |
| G12 | P1 | Russian locale is explicit; V4 is the sole new Zephyr default; independent pre-1.0 versions fail closed | Canonical schema has no model version or locale. `tools/publish_test_case_bundle.py:26`, `tools/publish_test_case_bundle.py:111`, and `tools/publish_test_case_bundle.py:319`, plus `tools/test_case_projections.py:17` and `tools/test_case_projections.py:560`, still default new direct calls to V1 while docs advertise V4 | Locale and transport behavior can drift; the direct CLI contradicts the frozen projection contract | Phases 3–4 |
| G13 | P1 | Exact verified tuples, adaptive 1→3→5 evals, full regression/release gate | No `tests/` directory exists. `RELEASE.md:14` references absent `requirements-dev.txt`; the repository has no `tools/ci_gate.py`. `tools/doctor.py:92-96` reports generic language verification from local executables | There is no reproducible evidence for a tuple or the frozen release policy | Phases 0 and 8 |
| G14 | P2 | Compatible CLI controls models; local executor is normal pilot path; no isolated workspace promise | `README.md:129-132`, `HOW-IT-WORKS.md:149-151`, and `USAGE.md:41-47` describe company default/local unsafe; `HOW-IT-WORKS.md:26-49` still describes isolated generated-test handling | Users and future implementers will execute the wrong architecture even if code is fixed | Phase 8 |
| G15 | P1 | Environment receipt has allowlisted safe IDs/labels only; controller timeout without framework result is UNKNOWN | `tools/run_tests.py:420-427` inherits nearly all non-obvious host variables; `:845-870` turns any TIMEOUT process evidence into FAIL. The receipt lacks a safe environment-label contract | Secrets/host state are broader than declared and operational timeout is mislabeled as test failure | Phase 6 |
| G16 | P1 | Readiness is qualified by exact tuple; company runner is future/non-core | `contracts/pipeline.json:28` marks broad Java/Python combinations `supported`; `tools/doctor.py:92-96` calls current Python verified and Java verified based on `javac`; `tools/company_runner.py` remains in core doctor requirements | Current readiness claims exceed evidence and remote execution unnecessarily blocks integrity checks | Phase 8 |

### 1.4 Overall verdict

The current package is a useful earlier-contract implementation, not a partially complete
version of the frozen controller. Canonical/validation/projection internals are reusable,
but the pilot is **not architecture-conformant and not core-pilot-ready** today. The
critical path is:

`machine contract -> durable state/result -> authoritative config/inventory/baseline ->`
`batch assembly/reviewer -> generated delta/native execution -> linear finalization ->`
`release evidence`.

## 2. Proposed version assignment

These are mechanical pre-1.0 implementation identifiers, not new product branches:

- package release: `0.5.0-pilot`;
- pipeline contract: `4.0`;
- canonical model/schema: `1.0.0` in the canonical document;
- breaking stage-envelope schemas: `5.0.0`;
- new controller/receipt schemas: `1.0.0`;
- acceptance profiles: existing frozen IDs `cases-only-v1` and `local-pilot-v1`;
- execution adapters: existing frozen `*-v1` IDs;
- CSV default remains `zephyr-scale-step-row-24-v4`;
- release-eval policy: `adaptive-1-3-5-v1`.

Unsupported pre-1.0 versions fail closed; no migrator is added.

## 3. File ownership map

The implementation should use six deep modules and keep `run_pipeline.py` thin:

| Module | Owns | Must not own |
|---|---|---|
| `tools/pilot_state.py` | run/attempt/event journal, state reduction, result axes, acceptance and exit projection | discovery, model calls, subprocesses |
| `tools/project_inventory.py` | eligible inventory, exclusions/secrets, context selections, frozen execution baseline and drift validation | semantic requirement grouping, execution |
| `tools/batch_assembly.py` | batch plan/fragment validation, deterministic canonical assembly, mappings and pre-review audits | model invocation, semantic correction |
| `tools/generated_delta.py` | no-overwrite materialization, ownership/readback, cleanup/retention dispositions | test execution, acceptance policy |
| `tools/execution_adapters.py` | three closed adapters, typed parameters, argv/cwd/environment/report request construction | shell templates, dependency installation, status policy |
| `tools/finalize_attempt.py` | branch obligations, pre-finalization verification, receipt/readback, terminal result and terminal-trace derivation | mutation of prior facts/dispositions |

Existing semantic modules remain authoritative in their current domains. Keep canonical
semantics in `tools/canonical_document.py`, successor identity checks in
`tools/revision_selection.py`, automation semantics in
`tools/automation_validation.py`, and trace relation checks in
`tools/build_trace_document.py`/`tools/trace_check.py`.

## 4. Phase 0 — repository test and contract harness

**Depends on:** user approval of this plan. A baseline commit is a separate authorization
gate, not an action in this plan turn.

**Files:**

- Add `.gitignore`.
- Add `requirements-dev.txt` using only versions already present in
  `release/dependencies.lock.txt`.
- Add `pytest.ini`.
- Add `tests/conftest.py` and `tests/helpers.py`.
- Add `tests/test_current_baseline.py`.
- Add `tests/test_projection_drift.py`.
- Add `tools/ci_gate.py`.
- Update `RELEASE.md` to reference only files that exist.

### Task 0.1: Capture a green current baseline

- [ ] Assert the checked-in current contract is internally valid before any migration.
- [ ] Assert every schema/Skill/tool registered by the current contract exists.
- [ ] Assert projection files are exact renders of machine truth.
- [ ] Run the narrow characterization suite and confirm it passes before production
      edits:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q tests/test_current_baseline.py tests/test_projection_drift.py
```

Expected before implementation: exit 0 against the current Pipeline 3.0 implementation.
The tests describe current integrity, not frozen-architecture conformance.

### Task 0.2: Add one deterministic gate

`tools/ci_gate.py` must run, in order, contract/schema checks, projection drift, and the
full pytest suite. It must return the first non-zero exit without rewriting files.
It must not invoke a model, project test executor, Git mutation, or network.

- [ ] Implement the gate with argv arrays and the current Python interpreter.
- [ ] Make `requirements-dev.txt` contain `-r requirements.txt` and `pytest==9.1.1`;
      do not add a runtime dependency.
- [ ] Confirm missing tools are reported as `NOT_RUNNABLE`, not silently skipped.

**Phase 0 acceptance:** the characterization suite and deterministic gate exit 0;
`RELEASE.md` names real commands/files; no current tool or schema is weakened or changed
to force green.

## 5. Phase 1 — machine contract, durable state, result policy, and minimum orchestration spine

**Depends on:** Phase 0 harness.

**Files:**

- Add `schemas/pilot-common.schema.json`.
- Add `schemas/run-manifest.schema.json`.
- Add `schemas/event.schema.json`.
- Add `schemas/attempt.schema.json`.
- Add `schemas/run-authorization-receipt.schema.json`.
- Add `schemas/terminal-result.schema.json`.
- Add `schemas/finalization-receipt.schema.json` with the minimal common envelope; Phase 7
  completes branch predicates.
- Add `tools/pilot_state.py`.
- Add `tests/test_contract_registry.py`.
- Add `tests/test_schema_closed_world.py`.
- Add `tests/test_pilot_state.py`.
- Add `tests/test_exit_policy.py`.
- Add `tests/test_resume_and_lineage.py`.
- Add `tests/test_orchestration_spine.py`.
- Update `contracts/pipeline.json` and `schemas/pipeline.schema.json` to Pipeline 4.0.
- Update `tools/contract_check.py` and `tools/render_contract_docs.py` for the new
  registries and projections.
- Update `tools/run_pipeline.py` to delegate state creation/status to `pilot_state`.

### Task 1.0: Drive the contract migration with red tests

- [ ] Assert Pipeline `4.0`, every frozen artifact/schema/adapter/policy ID exactly once,
      draft 2020-12 schemas, explicit versions, and closed unknown-property behavior.
- [ ] Run `tests/test_contract_registry.py` and `tests/test_schema_closed_world.py` before
      production edits and capture the expected Pipeline 3.0/missing-schema failures.
- [ ] Make those tests green only by implementing the Pipeline 4.0 registry and schemas;
      do not weaken the assertions.

### Required minimum orchestration spine

Phase 1 makes this live sequence durable and testable:

`invocation -> run -> attempt -> event -> artifact read-back -> factual result -> exit projection`

At run creation, bind the selected request profile and write/read back a safe,
run-scoped authorization receipt. Adapter enforcement remains Phase 6; Phase 1 records
the authorization boundary without selecting or executing an adapter.

### Required public Python seam

```python
def create_run(project: Path, policy_profile: str, authorization: Mapping[str, Any]) -> Mapping[str, Any]: ...
def append_event(run_root: Path, event_type: str, *, actor: str, attempt_id: str | None = None, batch_id: str | None = None, artifact_digest: str | None = None) -> Mapping[str, Any]: ...
def create_attempt(run_root: Path, identity: Mapping[str, Any], baseline: Mapping[str, Any]) -> Mapping[str, Any]: ...
def derive_state(run_root: Path) -> Mapping[str, Any]: ...
def terminal_result(facts: Mapping[str, Any], policy_profile: str) -> Mapping[str, Any]: ...
def exit_code(result: Mapping[str, Any] | None, controller_error: bool = False) -> int: ...
```

### Task 1.1: Prove append-only journal and event order

- [ ] Test the selected request profile and a safe run-scoped authorization receipt are
      bound and read back at run creation in `tests/test_orchestration_spine.py`.
- [ ] Test the minimum spine projects factual result and exit only after its artifact
      read-back, without invoking an execution adapter.
- [ ] Write failing tests for atomic `run-manifest.json` + `RUN_CREATED` before scan.
- [ ] Write failing tests for strict monotonic `seq`, digest readback, and rejection of a
      truncated/rewritten journal.
- [ ] Implement append-only writes using confined paths and flush/fsync before readback.
- [ ] Test duplicate event submission is byte-identical idempotence with the same identity
      or a conflict; it must never allocate two `seq` values for one claimed event.

### Task 1.2: Prove lineage and one active attempt

- [ ] Test run binding to one exact module, sequential child lineage, and rejection of a
      second nonterminal attempt.
- [ ] Test waiting/resume in a new process by loading only persisted bytes.
- [ ] Test terminal attempt immutability and explicit child creation.
- [ ] Test `EXECUTION_UNKNOWN` blocks child execution until recovery proves process stop.

### Task 1.3: Implement orthogonal result and exit projection

- [ ] Test nullable axes before facts exist.
- [ ] Test `COMPLETE+FAIL`, `REWORK`, `EXECUTION_UNKNOWN`, early `FATAL`, and
      `REVIEW_CONTEXT_LIMIT` tuples.
- [ ] Test `reason_code` is assigned once and earlier stage causes remain in events.
- [ ] Test every Q73 exit-priority example for both profiles.

**Phase 1 acceptance:**

```powershell
python -m pytest -q tests/test_pilot_state.py tests/test_exit_policy.py tests/test_resume_and_lineage.py tests/test_contract_registry.py
python -m pytest -q tests/test_orchestration_spine.py
python -m tools.contract_check --root . --full
```

All commands exit 0. A run manifest and first event exist before any scan callback in a
probe. State can be reconstructed after process exit. The live minimum spine binds and
reads back the request profile and authorization receipt before factual result/exit
projection. No model, project code, or execution adapter runs.

## 6. Phase 2 — authoritative `.skillsrc`, inventory, baseline, context selection

**Depends on:** Phase 1 durable run/attempt primitives.

**Files:**

- Add `schemas/inventory-receipt.schema.json`.
- Add `schemas/exclusion-receipt.schema.json`.
- Add `schemas/context-selection-receipt.schema.json`.
- Add `schemas/execution-baseline.schema.json`.
- Add `tools/project_inventory.py`.
- Add `tests/test_skillsrc_authority.py`.
- Add `tests/test_project_inventory.py`.
- Add `tests/test_execution_baseline.py`.
- Add `tests/test_context_selection.py`.
- Update `.skillsrc.example`.
- Update `schemas/skillsrc.schema.json` and `schemas/skillsrc-init-output.schema.json`.
- Update `tools/discover_project.py`, `tools/init_skillsrc.py`,
  `tools/skillsrc_manifest.py`, and `tools/stack_catalog.py`.
- Replace source-snapshot ownership in `tools/run_pipeline.py` with
  `project_inventory.py`.

### `.skillsrc` execution shape

Each module test configuration may contain only the closed fields below; conditional
schema rules constrain them by adapter:

```yaml
test:
  framework: pytest
  adapter_id: pytest:selected-symbols-v1
  interpreter: .venv/Scripts/python.exe
  build_profile: default
  adapter_parameters: {}
```

Java uses one module-local `mvnw|mvnw.cmd` or `gradlew|gradlew.bat` path. There is no
`build_command`, `argv_template`, or free-form shell field.

### Task 2.1: Make existing `.skillsrc` authoritative

- [ ] Test missing manifest automatic atomic creation.
- [ ] Test valid existing bytes remain byte-identical when discovery proposes additions
      or differences.
- [ ] Test the receipt contains a safe proposed digest and diff but no sensitive values.
- [ ] Test execution-significant drift yields one `NEEDS_INPUT` question.
- [ ] Test confirmed replacement preserves old bytes in immutable attempt evidence before
      atomic write.

### Task 2.2: Build metadata inventory before model bytes

- [ ] Traverse the full eligible selected-module tree without the old 64-file/1-MiB stop.
- [ ] Exclude VCS, dependencies, outputs, binaries/generated content, resolved
      `skill_pack_root`, symlink/reparse escapes, and secret-suspected files.
- [ ] Use fixed path rules (`.env`, private-key/certificate extensions, credentials/secrets
      directories) and deterministic content signatures for potential tokens; preserve
      only safe exclusion metadata, never an ordinary secret-content digest.
- [ ] Assign stable opaque file IDs from inventory identity, not model output.
- [ ] Test secret/excluded names never enter a model-context package.

### Task 2.3: Freeze complete execution baseline before attempt creation

- [ ] Include project/module identity, requirements, `.skillsrc`, eligible source/test/
      config/fixture/resource inputs, parent build files, wrapper/interpreter identity,
      adapter/profile/typed parameters, and proved local dependencies.
- [ ] Keep model context selection separate.
- [ ] Validate baseline before every resume and execution.
- [ ] Test unrelated files outside declared baseline do not invalidate an attempt.
- [ ] Test any changed declared input blocks execution and requires a child.
- [ ] Test a late undeclared dependency produces
      `NOT_RUNNABLE/BASELINE_INCOMPLETE`, never baseline mutation.
- [ ] Test exact nested-module cwd/root succeeds and cross-module orchestration is absent.

### Task 2.4: Implement C-lite selections

- [ ] Accept only opaque IDs from the host/model selection artifact.
- [ ] Verify ownership, scope, exclusion, digest, and budget before reading bytes.
- [ ] Add only the closed scanner-proved manifest/config set.
- [ ] Publish/read back one receipt for every exact transmitted byte set.
- [ ] Test no silent truncation and additional batches when one context is full.

**Phase 2 acceptance:** all four narrow test files exit 0; a 100+ file fixture inventories
without the old limit; a secret fixture is excluded without raw/hash leakage; nested
module baseline/resume works; valid `.skillsrc` bytes are unchanged absent approval.

## 7. Phase 3 — source requirements, batch protocol, deterministic assembly

**Depends on:** Phase 2 immutable inventory/baseline/context receipts.

**Files:**

- Add `schemas/batch-plan.schema.json`.
- Add `schemas/candidate-fragment.schema.json`.
- Add `schemas/assembly-receipt.schema.json`.
- Add `tools/batch_assembly.py`.
- Add `tests/test_batch_plan.py`.
- Add `tests/test_batch_assembly.py`.
- Add `tests/test_requirement_traceability.py`.
- Add `tests/test_canonical_locale.py`.
- Update `schemas/context-marker-output.schema.json`.
- Update `schemas/tc-generator-output.schema.json`.
- Update `schemas/canonical-test-document.schema.json` with
  `schema_version="1.0.0"`, `content_locale="ru-RU"`, and explicit mappings.
- Update `tools/build_context.py`, `tools/canonical_document.py`, and
  `tools/human_scenario.py`.
- Update `skills/context-marker/SKILL.md` and its context contract.
- Update `skills/tc-generator/SKILL.md` and its generation contract.

### Required assembly seam

```python
def plan_batches(source_requirements: Sequence[Mapping[str, Any]], grouping_evidence: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...
def validate_fragment(fragment: Mapping[str, Any], plan: Mapping[str, Any], header: Mapping[str, Any], context_receipt: Mapping[str, Any]) -> list[Mapping[str, str]]: ...
def assemble_candidate(header: Mapping[str, Any], plan: Mapping[str, Any], fragments: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]]: ...
```

### Task 3.1: Partition source requirements safely

- [ ] Test one batch is default.
- [ ] Split only on explicit normalized grouping evidence.
- [ ] Reject missing/overlapping `owned_source_requirement_ids` before model invocation.
- [ ] Allocate batch-specific ID namespaces and stable ordinals.

### Task 3.2: Validate one immutable fragment per batch

- [ ] Test complete canonical structures and source→canonical→case mappings.
- [ ] Test all case requirements stay within one batch.
- [ ] Permit one transport/schema retry before publication but no semantic fragment r2.
- [ ] Preserve a failed fragment/model receipt and continue other safe independent batches.

### Task 3.3: Assemble without semantic rewriting

- [ ] Test exact-union of identical capabilities and reject same-ID/different-byte values.
- [ ] Test only top-level `display_order` is renumbered.
- [ ] Test semantic fields remain equivalent as JSON values.
- [ ] Test exact conflict reason codes and no reviewer invocation on assembly failure.
- [ ] Test possible-duplicate fingerprint emits a warning only.
- [ ] Test coverage/provenance audits precede review and are not final trace checks.

**Phase 3 acceptance:** the same fragments in any input order produce the same canonical
bytes/receipt; cross-batch negative fixtures fail with exact reasons; canonical human
fields require `ru-RU`; no fixed case count exists.

## 8. Phase 4 — one reviewer session, correction budget, V4 projections

**Depends on:** Phase 3 assembled candidate and pre-review audits.

**Files:**

- Add `schemas/reviewer-session.schema.json`.
- Add `tests/test_reviewer_protocol.py`.
- Add `tests/test_revision_budget.py`.
- Add `tests/test_zephyr_profiles.py`.
- Add reviewer-session validation to `tools/orchestrate_test_case_revision.py`.
- Update `tools/revision_selection.py`.
- Update `schemas/tc-reviewer-output.schema.json` to `5.0.0`.
- Update `skills/tc-reviewer/SKILL.md` and its review contract.
- Update `skills/orchestrate/SKILL.md` and its orchestration contract.
- Update `tools/publish_test_case_bundle.py`, `tools/test_case_projections.py`,
  `schemas/orchestrator-output.schema.json`, and receipt fields from `markdown_*` to
  `preview_*`.

### Task 4.1: Enforce one logical session and verdict cardinality

- [ ] Test zero or more bounded C-lite evidence pairs in one session.
- [ ] Test at most one `AUTHORITATIVE_VERDICT` under all sequences.
- [ ] Test exactly one verdict for successful session/effective canonical.
- [ ] Test zero at terminal only for explicit pre-verdict abort.
- [ ] Test per-batch, hierarchical, second-session, and second-verdict receipts fail.
- [ ] Test same-model/different-invocation only with host isolation evidence; otherwise
      record `independence_unverified` and reject acceptance.

### Task 4.2: Enforce context ceiling without fallback

- [ ] Bind candidate, requirements, mappings, inventory, context receipts, and evidence.
- [ ] Test bounded evidence retrieval/readback.
- [ ] Test irreducible package yields terminal
      `REVIEW_CONTEXT_LIMIT/PARTIAL/NOT_APPLICABLE` with no per-batch reviewer.

### Task 4.3: Enforce exactly two semantic canonical versions

- [ ] Test revision 1 immediate `UNREVIEWED` publication.
- [ ] Test unchanged acceptance or one complete valid successor revision 2.
- [ ] Test partial/destructive/choice-bearing correction is `REWORK`.
- [ ] Test a third complete version is rejected and earlier bytes remain.

### Task 4.4: Make V4 the sole new projection default

- [ ] Change every public default/example to `zephyr-scale-step-row-24-v4`.
- [ ] Keep historical V1–V3 verification only through an explicit receipt profile.
- [ ] Prove projections preserve human meaning and never become downstream model input.
- [ ] Keep XML opt-in `observed/unverified` with no tenant-readiness claim.

**Phase 4 acceptance:** all allowed/forbidden reviewer sequences are tested; one accepted
canonical has one verdict and verified isolation; direct bundle publication defaults V4;
historical profiles verify only explicitly.

## 9. Phase 5 — automation generation, isolated static review, and generated-delta transaction

**Depends on:** Phase 4 effective canonical and reviewer receipt.

This phase delivers automation generation, isolated static automation review, at most
one correction, a complete `generated_delta`, materialization receipts, and a safe
transaction. `cases-only-v1` never materializes. Materialization requires accepted static
automation review; partial materialization never executes; every file receives a receipt;
and differing existing files are never overwritten.

**Files:**

- Add `schemas/generated-delta.schema.json`.
- Add `schemas/materialization-receipt.schema.json`.
- Add `schemas/disposition-receipt.schema.json`.
- Add `tools/generated_delta.py`.
- Add `tests/test_automation_revision_budget.py`.
- Add `tests/test_generated_delta.py`.
- Add `tests/test_materialization_failure.py`.
- Update `schemas/tc-to-autotest-output.schema.json` and
  `schemas/autotest-reviewer-output.schema.json` to `5.0.0`.
- Update `tools/automation_validation.py`.
- Update both automation Skills and their contracts/assets.
- Remove direct generated-file ownership logic from `tools/run_pipeline.py` only after
  equivalent tests pass in `generated_delta.py`.

### Required generated-delta seam

```python
def materialize_delta(project: Path, module_root: Path, baseline: Mapping[str, Any], automation: Mapping[str, Any], review: Mapping[str, Any]) -> Mapping[str, Any]: ...
def inspect_delta(project: Path, delta: Mapping[str, Any]) -> Mapping[str, Any]: ...
def apply_dispositions(project: Path, delta: Mapping[str, Any], requested: Mapping[str, str]) -> Mapping[str, Any]: ...
```

### Task 5.1: Bound automation and isolated static review to at most one correction

- [ ] Test initial automation plus at most one correction/review.
- [ ] Test the static automation review is isolated and accepted before materialization.
- [ ] Test digest/symbol/relation coverage for every generated file.
- [ ] Test runtime FAIL never triggers regeneration.

### Task 5.2: Materialize a set

- [ ] Test `cases-only-v1` produces no materialization attempt.
- [ ] Test only accepted static automation review may materialize a complete
      `generated_delta`.
- [ ] Test every file has baseline-absence, path, digest, automation/review, and ownership
      receipt.
- [ ] Test no overwrite, escape, collision, or unowned idempotence; differing existing
      files are never overwritten.
- [ ] Test all files are read back before any execution-ready event.

### Task 5.3: Handle partial materialization

- [ ] Inject failure after file 1 of a multi-file delta.
- [ ] Assert execution callback count is zero.
- [ ] Assert written unchanged files are `CLEANED` and unwritten files
      `NOT_MATERIALIZED`.
- [ ] Assert result is
      `TERMINAL/PARTIAL/NOT_APPLICABLE/MATERIALIZATION_INCOMPLETE`.
- [ ] Assert later finalization cannot change these facts.

**Phase 5 acceptance:** generation has at most one isolated static-review correction;
accepted review yields a complete `generated_delta`; `cases-only-v1` never materializes;
all failure injection points leave complete per-file receipts; partial materialization
never executes; differing existing files are never overwritten; and no unrelated
project-byte changes.

## 10. Phase 6 — closed project-native execution adapters

**Depends on:** Phase 5 complete reviewed/materialized delta and Phase 2 baseline.

**Files:**

- Add `tools/execution_adapters.py`.
- Add `tests/test_execution_adapters.py`.
- Add `tests/test_execution_timeout_semantics.py`.
- Add `tests/test_project_native_pytest.py`.
- Add `tests/test_run_scoped_authorization.py`.
- Add `tests/fixtures/reports/pytest-pass.xml`.
- Add `tests/fixtures/reports/pytest-timeout.xml`.
- Add `tests/fixtures/reports/junit5-pass.xml`.
- Add `tests/fixtures/reports/junit5-timeout.xml`.
- Add a minimal project fixture under `tests/fixtures/projects/python-pytest/` containing
  `.skillsrc`, `pyproject.toml`, `src/sample.py`, `tests/conftest.py`, and
  `tests/test_existing.py`.
- Update `tools/run_tests.py`, `tools/execution_preflight.py`, and
  `schemas/run-tests-output.schema.json` to `5.0.0`.
- Update `tools/run_pipeline.py` to make run-authorized local execution the pilot path.
- Update `tools/doctor.py` so company runner is non-core and no broad verified-language
  claim is emitted.
- Retain `tools/company_runner.py` and its schema only as non-core future adapter code;
  remove imports from core closure.

### Closed adapter seam

```python
@dataclass(frozen=True)
class ExecutionRequest:
    adapter_id: str
    executable: str
    argv: tuple[str, ...]
    cwd: str
    selectors: tuple[str, ...]
    timeout_seconds: int
    report_paths: tuple[str, ...]
    environment_labels: tuple[str, ...]

def build_request(adapter_id: str, module: Mapping[str, Any], reviewed_targets: Sequence[Mapping[str, Any]]) -> ExecutionRequest: ...
```

### Task 6.1: Enforce run-scoped authorization

- [ ] Test cases-only never starts project code.
- [ ] Test folder presence alone is insufficient.
- [ ] Test explicit full-pipeline authorization binds one run and exact selectors.
- [ ] Store only a safe authorization fact/digest, never prompt text/credentials.

### Task 6.2: Build only closed argv requests

- [ ] Test the exact three `*-v1` IDs; unknown adapters fail closed.
- [ ] Test module-local interpreter/wrapper, cwd, build profile, typed parameters,
      selectors, timeout, and reports.
- [ ] Reject shell strings, `argv_template`, redirects, pipes, substitutions, and
      arbitrary environment input.
- [ ] Receipt environment contains safe IDs/labels only, never values.

### Task 6.3: Restore project-native behavior

- [ ] Remove host pytest config injection, `--noconftest`, isolated mode, and plugin
      autoload disabling.
- [ ] Prove fixture pytest config, `conftest.py`, fixtures, and declared plugins
      participate while only reviewed node IDs run.
- [ ] Prove Maven/Gradle request builders use module-local wrappers/exact selectors;
      unit tests use injected reports and never download.

### Task 6.4: Separate FAIL, UNKNOWN, and NOT_RUNNABLE

- [ ] Controller/process timeout without authoritative framework report -> `UNKNOWN`.
- [ ] Exact-test timeout in authoritative pytest/JUnit evidence -> `FAIL`.
- [ ] Missing wrapper/interpreter/config before start -> `NOT_RUNNABLE`.
- [ ] Crash/lost report/digest drift after start -> `UNKNOWN`.
- [ ] No UNKNOWN branch auto-runs again.

**Phase 6 acceptance:** project-native pytest config/conftest/fixtures and exact selectors
are proven; timeout/crash/prestart matrices pass; receipts contain no environment values;
core code has no company-runner default/import dependency.

## 11. Phase 7 — dispositions, pre-finalization trace, terminal closure

**Depends on:** Phases 1, 5, and 6.

**Files:**

- Add `tools/finalize_attempt.py`.
- Add `tests/test_disposition_matrix.py`.
- Add `tests/test_finalization_lifecycle.py`.
- Add `tests/test_trace_revisions.py`.
- Add `tests/test_terminal_immutability.py`.
- Complete `schemas/disposition-receipt.schema.json` and
  `schemas/finalization-receipt.schema.json`.
- Update `schemas/trace-document.schema.json`,
  `schemas/trace-audit-output.schema.json`, and
  `schemas/orchestrator-output.schema.json` to `5.0.0`.
- Update `tools/build_trace_document.py`, `tools/trace_check.py`, and
  `tools/orchestrate_test_case_revision.py`; move attempt finalization out of the latter.
- Update `tools/run_pipeline.py` for the exact linear lifecycle.

### Required finalization seam

```python
def build_pre_finalization_trace(branch: Mapping[str, Any], dispositions: Mapping[str, Any]) -> Mapping[str, Any]: ...
def verify_finalization(inputs: Mapping[str, Any], pre_trace: Mapping[str, Any]) -> Mapping[str, Any]: ...
def publish_terminal_result(finalization_receipt: Mapping[str, Any], facts: Mapping[str, Any]) -> Mapping[str, Any]: ...
def derive_terminal_trace(pre_trace: Mapping[str, Any], finalization_receipt: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]: ...
```

### Task 7.1: Implement complete disposition matrix

- [ ] PASS + valid pre-trace/path/digest -> `RETAINED` before finalization.
- [ ] FAIL/NOT_RUNNABLE -> every byte-identical owned file `CLEANED`.
- [ ] Unsafe cleanup/content conflict -> preserved exact reason and accepted false.
- [ ] EXECUTION_UNKNOWN unchanged -> `PRESERVED_EXECUTION_UNKNOWN`.
- [ ] EXECUTION_UNKNOWN changed -> `PRESERVED_CONTENT_CONFLICT` plus unknown evidence.
- [ ] Assert cleanup callback count zero for all UNKNOWN variants.

### Task 7.2: Remove trace/finalization cycle

- [ ] Build/validate `pre_finalization_trace` through dispositions.
- [ ] Mark branch-inapplicable evidence `NOT_APPLICABLE`.
- [ ] Finalizer verifies pre-trace/dispositions and writes/reads one complete receipt.
- [ ] Publish terminal result, then terminal trace referencing receipt/result.

### Task 7.3: Terminalize valid and invalid finalization

- [ ] Valid receipt permits acceptance only when every other predicate passes.
- [ ] Invalid receipt still terminalizes with
      `accepted=false/FINALIZATION_INVALID`.
- [ ] Preserve verification/coverage and prior cause.
- [ ] Test `RETAINED` remains factual when finalization is invalid.
- [ ] Test terminal retry changes no byte and invokes no executor; recovery uses child.

**Phase 7 acceptance:** every branch has a completed/read-back finalization receipt and
terminal result; disposition covers the full set; invalid finalization is truthful;
repeat terminal execution changes no bytes and calls no process.

## 12. Phase 8 — complete and qualify the orchestration flow, docs, and release evals

**Depends on:** all prior phases.

Each Phase 2–7 integrates into the Phase 1 spine as it is delivered. Phase 8 completes
and qualifies that already integrated flow across all branches, acceptance, public docs,
adaptive evaluation, exact-tuple readiness, and the release gate; it must not first
assemble isolated modules.

**Files:**

- Add `schemas/release-eval-receipt.schema.json`.
- Add `evals/__init__.py`.
- Add `evals/release_eval.py`.
- Add `evals/scenarios/pilot-critical.json`.
- Add `tests/test_pipeline_acceptance.py`.
- Add `tests/test_release_eval_policy.py`.
- Add `tests/test_compatibility_contract.py`.
- Update all six `skills/*/SKILL.md` files and direct references/assets.
- Finalize `contracts/pipeline.json`, `schemas/pipeline.schema.json`,
  `tools/contract_check.py`, `tools/render_contract_docs.py`, `CONTRACTS.md`, and
  `PIPELINE.md`.
- Update `README.md`, `USAGE.md`, `HOW-IT-WORKS.md`, and `RELEASE.md`.
- Update `release/dependencies.lock.txt` only if the actual environment lock changes.

### Task 8.1: Prove compatible/verified host evidence

- [ ] Validate exact registry reads, model-stage input digests, artifact readback, one
      unresolved question, resume, separate reviewer invocation, and host identity.
- [ ] Emit `independence_unverified` for absent isolation evidence.
- [ ] Assert no Python tool imports/configures an LLM provider.

### Task 8.2: Add artifact-first lifecycle acceptance

- [ ] Run fresh non-Git, dirty Git, nested-module, secret/exclusion,
      WAITING_FOR_MODEL, one-user-question, reviewer rejection, context-limit,
      materialization failure, execution interruption, terminal retry, and child retry
      scenarios in temporary fixtures.
- [ ] Assert artifact order, event identities/digests, partial preservation, and no
      unrelated byte changes.
- [ ] Assert retained tests run by ordinary project command outside pipeline.

### Task 8.3: Implement adaptive release evaluation

`evals/release_eval.py` aggregates immutable compatible-CLI runs; it does not call a
model. `adaptive-1-3-5-v1` requires one smoke, then three independent critical runs, and
five after instability or any protocol violation. A protocol violation prevents readiness
even if later runs pass.

- [ ] Test stable campaign completes required smoke/three evidence.
- [ ] Test instability or violation raises required count to five.
- [ ] Test exact tuple completeness and no trust transfer.
- [ ] Test real-project evidence binds one immutable snapshot.

### Task 8.4: Align public documents

- [ ] State compatible CLI entry, local native execution, run authorization, two profiles,
      statuses, exits, resume, dispositions, V4 default, tuple readiness, and N/A lines.
- [ ] Remove company-default/local-unsafe/isolated-workspace claims.
- [ ] Keep company runner, Zephyr tenant, and production rollback future/N/A.
- [ ] Generate, never hand-edit, `CONTRACTS.md` and `PIPELINE.md`.

### Task 8.5: Run final local gate

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m tools.contract_check --root . --full
python -m tools.render_contract_docs --root . --check
python -m pytest -q
python -m tools.doctor --root .
python -m tools.ci_gate --root .
git -c safe.directory=D:/AI-Projects/test-skills-portable diff --check
git -c safe.directory=D:/AI-Projects/test-skills-portable status --short
```

Expected: all checks exit 0; status contains only approved implementation paths; no
runtime artifacts, caches, secrets, external-project changes, commit, or remote mutation.

### Task 8.6: Run exact-tuple external gate

The release environment supplies task-specific variables for one authorized real project
and compatible-CLI campaign:

```powershell
python -m evals.release_eval --campaign-dir $env:PILOT_EVAL_CAMPAIGN --project $env:PILOT_E2E_PROJECT --module $env:PILOT_E2E_MODULE --policy local-pilot-v1 --require-real-execution --require-independent-review
```

The command fails closed if any variable/evidence is absent. It does not install or
download the target environment. A Java/Maven tuple requires the project's real JDK and
wrapper evidence; Python requires its selected interpreter/venv and normal pytest
environment. Shopizer is optional and has no special-case code.

**Phase 8 acceptance:** the already integrated Phase 1 spine closes every branch;
local gate green; exact diff scope clean; adaptive campaign green with zero protocol
violations; real execution and fresh reviewer receipts verify one exact tuple. Only then may the release say
`core-pilot-ready for <that exact verified tuple>`. Zephyr tenant, company runner, and
production rollback remain `N/A` unless separately evidenced.

## 13. End-to-end acceptance matrix

| Scenario | Required result/evidence |
|---|---|
| Cases only, all manual, no blockers | `TERMINAL/COMPLETE/NOT_APPLICABLE/MANUAL_ONLY`, valid review/finalization, accepted under `cases-only-v1`, exit 0 |
| Same facts under local profile | accepted false, exit 1 |
| Mixed coverage plus authoritative PASS | accepted under `local-pilot-v1` only when all generated files are RETAINED and every predicate passes |
| Reviewer context irreducible | zero verdict, `PARTIAL/NOT_APPLICABLE/REVIEW_CONTEXT_LIMIT`, accepted false |
| Reviewer success | exactly one session/verdict bound to exact candidate/input digests |
| Existing `.skillsrc` drift | bytes unchanged, proposed diff, one `NEEDS_INPUT`, exit 3 |
| Model unavailable | same attempt `WAITING_FOR_MODEL`, artifacts intact, exit 3 |
| Baseline drift before execution | no executor call; child required |
| Late undeclared dependency | `NOT_RUNNABLE/BASELINE_INCOMPLETE`, no baseline mutation |
| Partial materialization | no execution; cleaned/not-materialized dispositions; terminal partial |
| Authoritative exact-test failure | `COMPLETE/FAIL`, owned files CLEANED, exit 1 |
| Prestart missing wrapper | `NOT_RUNNABLE`, owned files CLEANED, exit 2 |
| Controller timeout, no framework result | `EXECUTION_UNKNOWN`, files preserved, exit 2 |
| Framework exact-test timeout | authoritative `FAIL`, cleanup applies, exit 1 |
| UNKNOWN plus file drift | `PRESERVED_CONTENT_CONFLICT` plus unknown evidence; no cleanup |
| PASS then invalid finalization | RETAINED remains; accepted false, `FINALIZATION_INVALID`, exit 2 |
| Repeat terminal exec | zero executor calls and zero byte changes |
| Different module | separate run; no cross-module orchestration |
| Secret-suspected file | no model exposure and no raw/hash receipt leakage |
| V4 bundle | V4 direct default; historical versions only by explicit receipt |
| Release instability/violation | campaign raises to five; violation blocks readiness |

## 14. Final completion criteria

Implementation is complete only when:

1. every P0/P1 gap is closed by a named test and exact artifact evidence;
2. all machine/schema/semantic/projection checks are green;
3. every frozen erratum is represented in schema, semantic validator, and a negative
   test—not merely prose;
4. all current and new tests pass without disabling, deleting, or narrowing assertions;
5. the exact changed-file set has no unrelated edits;
6. no secret, model credential, environment value, external-project mutation, commit, or
   push occurred outside explicit authorization;
7. a retained test reruns through its ordinary project-native command; and
8. readiness is claimed only for the exact tuple whose adaptive campaign and fresh
   reviewer evidence passed.

At the end of each implementation phase, stop and present the phase diff, exact commands,
outputs, and unresolved `N/A` evidence for approval before advancing.
