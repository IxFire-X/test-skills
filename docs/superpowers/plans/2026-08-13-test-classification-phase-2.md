# Test Classification Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse independently reviewed existing integration/e2e tests as authoritative implementations of managed cases, execute the complete existing test inventory in a separate technical-regression lane on full-project runs, and preserve both truths through trace/finalization.

**Architecture:** An immutable Phase-1 `EffectiveTechnicalEvidence` is the only source for existing files, locators, scope, and origin. Pipeline 5.0 uses a neutral automation registry (`files`, `symbols`, `READY|BLOCKED`), runs authoritative managed relations separately from the full existing regression inventory, and derives managed, technical, and overall outcomes without allowing unit PASS to satisfy a functional case.

**Tech Stack:** Python 3.10+ standard library, existing `jsonschema` schemas, pytest/JUnit5 runner adapters, stdlib `unittest`, canonical SHA-256 identities, atomic trace relations.

## Коротко по-русски

- Existing integration/e2e тест можно связать с функциональным кейсом и реально выполнить.
- Unit/unknown тест выполняется только в отдельном technical-regression lane и никогда не закрывает Zephyr-кейс.
- Технический FAIL делает общий результат FAIL; технический PASS не превращает непокрытый функциональный кейс в PASS.
- Origin `existing/generated` выводится из артефактов и не задаётся LLM.
- Обычный feature-run запускает managed lane; запрос «весь проект» дополнительно включает полный technical-regression lane без настройки количества.

## Global Constraints

- Start only after every Phase-1 task and its fresh review are complete on `codex/adaptive-test-case-granularity`.
- Normative design: `docs/superpowers/specs/2026-08-13-test-classification-evidence-design.md`, SHA-256 `C9C526BC997340B4E71A5108C1C730D75B518F3E3A029E92B82889BEDAFD218E`.
- Phase 2 pipeline version is exactly `5.0`. Automation, autotest-reviewer, run-tests, trace, and orchestrator stage envelopes become exactly `4.0.0` together; older V3 carriers are rejected before partial consumption.
- Preserve canonical document schema and exact JSON/Markdown/Zephyr CSV bytes.
- Do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, or `tools/scan_project.py`.
- Add no dependency. Existing runner confinement, full-file digest, locator, provider preflight, secret redaction, fresh run ID, and current-run evidence rules remain mandatory.
- `unit|unknown` is never authoritative, regardless of origin. `integration|e2e` requires accepted classification plus explicit atomic relations.
- No heuristic linking, Cartesian inference, test-count setting, relation repair, path normalization, stale evidence, or mixed V3/V4 execution.
- Every task follows RED → smallest GREEN → focused review → commit. Run the full repository suite only in Task 6.

## Locked Phase-2 carriers

`EffectiveTechnicalEvidence` is recursively immutable and exactly:

```json
{
  "technical_test_inventory_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "technical_test_classification_sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "technical_test_review_sha256": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "effective_technical_evidence_sha256": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  "files": [],
  "symbols": [],
  "classifications": []
}
```

The effective digest hashes the bare object with `effective_technical_evidence_sha256` omitted. All other digests identify exact upstream bare artifacts.
The repeated hex values in these examples are shape-only notation. Checked-in fixtures compute all four real digests from the exact Phase-1 artifacts and bare effective carrier; zero or repeated placeholder digests are not accepted test data.

Automation V4:

```json
{
  "schema_version": "4.0.0",
  "stage": "tc-to-autotest",
  "artifacts": {
    "automation_status": "READY",
    "source": {"document_id": "TCDOC-synthetic", "revision": 1, "source_digest": "sha256:4444444444444444444444444444444444444444444444444444444444444444"},
    "technical_evidence_source": {"effective_technical_evidence_sha256": "sha256:3333333333333333333333333333333333333333333333333333333333333333"},
    "files": [],
    "symbols": [],
    "implementation_relations": [],
    "manual_dispositions": [],
    "diagnostics": []
  },
  "warnings": []
}
```

Automation `files/symbols` contain only relation targets. An existing target is copied byte-for-byte from effective evidence; a generated target must be absent from the pre-generation inventory. Orphan files/symbols are invalid for either origin.

Run V4 adds `execution_lane: managed | technical_regression` and exact technical-evidence source. Trace V4 contains distinct `execution` and `technical_regression_execution`. Final V4 contains `managed_status`, optional `technical_regression_verdict`, and `overall_status`.

## Status precedence

```python
def derive_overall_status(managed_status, technical_verdict):
    if managed_status == "FAIL" or technical_verdict == "FAIL":
        return "FAIL"
    if technical_verdict == "NOT_RUNNABLE":
        return "NOT_RUNNABLE"
    return managed_status
```

An omitted technical run passes `None` and has no effect. Technical PASS never changes managed status.

---

### Task 1: Freeze the Effective Technical-Evidence Carrier

**Files:**

- Modify: `tools/test_classification.py`
- Modify: `tests/test_test_classification.py`

**Interfaces:**

`validate_effective_technical_evidence(evidence: Mapping[str, Any], project_root: Path) -> tuple[Mapping[str, str], ...]` validates upstream digest syntax, the recomputable effective digest, pair coverage, current file bytes, and ordering. It cannot recompute the three upstream digests because the selected carrier intentionally does not embed those artifacts; their authenticity is established once by the Phase-1 selector.

`technical_regression_pairs(effective_technical_evidence: Mapping[str, Any], project_root: Path) -> tuple[tuple[str, str], ...]` validates current bytes and returns every existing pair in canonical order.

- [ ] **Step 1: Write RED carrier/digest tests**

```python
def test_effective_digest_excludes_only_its_own_field(self):
    evidence = self.effective_evidence()
    bare = dict(evidence)
    expected = bare.pop("effective_technical_evidence_sha256")
    self.assertEqual(expected, self.stable_sha256(bare))

def test_technical_pairs_include_every_existing_scope(self):
    evidence = self.effective_evidence(scopes=("integration", "unit", "unknown"))
    self.assertEqual(tuple(self.all_pairs(evidence)), technical_regression_pairs(evidence, self.project))
```

Define `effective_evidence`, `all_pairs`, and the exact sorted-key compact UTF-8 `stable_sha256` as test-local helpers. Cover malformed upstream digest syntax, wrong effective digest, missing/extra/reordered pairs, file drift, recursively immutable nested mappings, and stable pair order. A separate Phase-1 regression proves that arbitrary upstream digests cannot enter this carrier through `select_effective_technical_evidence`.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_test_classification -v`

Expected: FAIL because effective-carrier validation/pair selection is absent.

- [ ] **Step 3: Implement exact carrier validation**

```python
bare = dict(evidence)
declared = bare.pop("effective_technical_evidence_sha256")
if _digest(bare) != declared:
    diagnostics.append(_diag("/effective_technical_evidence_sha256", "TECHNICAL_EVIDENCE_DIGEST"))
```

Reuse the Phase-1 `_digest` implementation from the same deep module; do not introduce a second serializer. Re-read every existing file and validate its exact digest before returning any pair.

- [ ] **Step 4: Run GREEN**

```powershell
python -m unittest tests.test_test_classification -v
```

- [ ] **Step 5: Commit**

```powershell
git add tools/test_classification.py tests/test_test_classification.py
git commit -m "feat: freeze effective technical evidence"
```

### Task 2: Neutral Automation V4 Schema and Validator

**Files:**

- Modify: `schemas/tc-to-autotest-output.schema.json`
- Modify: `schemas/autotest-reviewer-output.schema.json`
- Modify: `schemas/test-symbol-registry.schema.json`
- Modify: `tools/automation_validation.py`
- Modify: `tests/fixture_factory.py`
- Modify: `tests/test_automation_relations.py`
- Create: `tests/test_portfolio_analysis.py`
- Create: `tests/fixtures/stages/v4/tc-to-autotest-ready.json`
- Create: `tests/fixtures/stages/v4/tc-to-autotest-manual-only.json`
- Create: `tests/fixtures/stages/v4/tc-to-autotest-blocked.json`

**Interfaces:**

`validate_automation_artifact(artifact: Any, document: dict[str, Any], effective_technical_evidence: Mapping[str, Any], project_root: Path) -> list[dict[str, str]]` is the single semantic validator.

`required_symbol_pairs(artifact: Any, document: dict[str, Any], effective_technical_evidence: Mapping[str, Any], project_root: Path) -> tuple[tuple[str, str], ...]` returns authoritative relation pairs after zero diagnostics.

`tools/test_classification.py` additionally produces immutable `PortfolioAnalysis` with `case_modes`, `case_coverage`, `case_readiness`, `authoritative_pairs`, and `technical_regression_pairs`.

`analyze_test_portfolio(canonical_document, effective_technical_evidence, automation_artifact, project_root) -> PortfolioAnalysis` first calls the public V4 automation validator, then derives the view.

- [ ] **Step 1: Add RED migration and authority tests**

Extend the existing `tests/test_automation_relations.py` factory with `automation_with_symbol(origin, scope)` and `existing_integration_automation()`; both derive IDs/digests from the checked-in effective evidence fixture. Add `assertDiagnostic(code, path, artifact)` as an exact lookup in `validate_automation_artifact`. In `tests/test_portfolio_analysis.py`, define `blocked_document`, `empty_evidence`, and `blocked_automation` from `tests.fixture_factory`.

```python
def test_unit_is_never_authoritative_even_when_generated(self):
    artifact = automation_with_symbol(origin="generated", scope="unit")
    self.assertDiagnostic("AUTOMATION_NONAUTHORITATIVE_SCOPE", "/artifacts/implementation_relations/0", artifact)

def test_existing_relation_must_match_effective_evidence_exactly(self):
    artifact = existing_integration_automation()
    artifact["artifacts"]["symbols"][0]["locator"]["method_name"] = "spoofed"
    self.assertDiagnostic("AUTOMATION_EXISTING_SYMBOL_MISMATCH", "/artifacts/symbols/0", artifact)

def test_blocked_nonmanual_case_is_automated_blocked_without_claiming_coverage(self):
    result = analyze_test_portfolio(self.blocked_document(), self.empty_evidence(), self.blocked_automation(), self.project)
    self.assertEqual("automated", result.case_modes["TC-1"])
    self.assertEqual("blocked", result.case_coverage["TC-1"])
    self.assertEqual("blocked", result.case_readiness["TC-1"])
```

Define the referenced factories as test-local methods in the two owned test modules. Cover V3 rejection, old names/status rejection, all four scope shapes, origin spoof, stale evidence/file digest, generated path already in snapshot, orphan rows, exact operation/assertion coverage, AND semantics, blocked empty arrays, fully manual READY zero pairs, manual/automated/mixed modes, and incomplete ready coverage.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_automation_relations tests.test_portfolio_analysis -v`

Expected: FAIL against the V3 schema/signature.

- [ ] **Step 3: Implement the closed schema and one semantic facade**

```python
if symbol["test_scope"] not in {"integration", "e2e"} and pair in related_pairs:
    diagnostics.append(_diagnostic("AUTOMATION_NONAUTHORITATIVE_SCOPE", relation_path))
if symbol["implementation_origin"] == "existing" and symbol != exact_existing_symbol(pair):
    diagnostics.append(_diagnostic("AUTOMATION_EXISTING_SYMBOL_MISMATCH", symbol_path))
```

Keep relation ordering and parent-chain validation intact. Return tuples from pair APIs only after zero diagnostics.

- [ ] **Step 4: Run GREEN and fixture CLI checks**

```powershell
python -m unittest tests.test_automation_relations tests.test_portfolio_analysis -v
python tools\validate_artifact.py schemas\tc-to-autotest-output.schema.json tests\fixtures\stages\v4\tc-to-autotest-ready.json
python tools\validate_artifact.py schemas\tc-to-autotest-output.schema.json tests\fixtures\stages\v4\tc-to-autotest-manual-only.json
python tools\validate_artifact.py schemas\tc-to-autotest-output.schema.json tests\fixtures\stages\v4\tc-to-autotest-blocked.json
```

- [ ] **Step 5: Commit**

```powershell
git add schemas/tc-to-autotest-output.schema.json schemas/autotest-reviewer-output.schema.json schemas/test-symbol-registry.schema.json tools/automation_validation.py tools/test_classification.py tests/fixture_factory.py tests/test_automation_relations.py tests/test_portfolio_analysis.py tests/fixtures/stages/v4
git commit -m "feat: unify existing and generated automation symbols"
```

### Task 3: Automation Generation and Review Guidance

**Files:**

- Modify: `skills/tc-to-autotest/SKILL.md`
- Modify: `skills/tc-to-autotest/references/automation-output-contract.md`
- Modify: `skills/autotest-reviewer/SKILL.md`
- Modify: `skills/autotest-reviewer/references/autotest-review-contract.md`
- Modify: `skills/tc-to-autotest/assets/java-python-conventions/python-pytest.md`
- Modify: `skills/tc-to-autotest/assets/java-python-conventions/java-junit5.md`
- Modify: `tests/test_skill_contracts_v3.py`
- Modify: `evals/zephyr-test-case-projection/scenarios.json`
- Modify: `evals/zephyr-test-case-projection/rubric.md`

**Interfaces:**

- Generator reuses existing reviewed integration/e2e without editing its file.
- It generates a new symbol only when no suitable existing relation is supported.
- Reviewer reviews only authoritative relation pairs; classifier reviewer already owns completeness/scope inventory review.

- [ ] **Step 1: Add RED guidance tests**

```python
def test_automation_skill_prefers_reviewed_existing_implementation(self):
    text = automation_contract().lower()
    for phrase in ("existing", "integration", "e2e", "do not edit", "atomic relation"):
        self.assertIn(phrase, text)
    self.assertIn("unit and unknown are non-authoritative", text)
```

Add scenarios for exact existing reuse, existing unit pressure, generated integration fallback, stale evidence, one symbol covering multiple explicit targets, and same local symbol ID in different files.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_skill_contracts_v3 -v`

- [ ] **Step 3: Update skills and language assets**

Both language assets must describe how to point to an existing locator without rewriting its source and how to emit a generated locator with `implementation_origin=generated` and `test_scope=integration|e2e`.

- [ ] **Step 4: Run GREEN and skill validators**

```powershell
python -m unittest tests.test_skill_contracts_v3 -v
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\tc-to-autotest
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\autotest-reviewer
```

- [ ] **Step 5: Commit**

```powershell
git add skills/tc-to-autotest skills/autotest-reviewer tests/test_skill_contracts_v3.py evals/zephyr-test-case-projection
git commit -m "feat: reuse reviewed existing test implementations"
```

### Task 4: Managed and Technical Runner Lanes

**Files:**

- Modify: `schemas/run-tests-output.schema.json`
- Modify: `tools/run_tests.py`
- Modify: `tests/test_run_tests_v3.py`
- Modify: `tests/test_run_tests_skillsrc_v3.py`
- Modify: `tests/test_validate_artifact.py`

**Interfaces:**

`ExecutionLane = Literal["managed", "technical_regression"]`.

`validate_artifact_runner_compatibility(project: Path, language: Literal["python", "java"], document: Mapping[str, Any], automation_artifact: Mapping[str, Any], effective_technical_evidence: Mapping[str, Any], execution_lane: ExecutionLane) -> RunnerCompatibility` validates the exact selected registry.

`run_tests_v4(project: Path, language: Literal["python", "java"], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], effective_technical_evidence: Mapping[str, Any], execution_lane: ExecutionLane, provider_resolver: Any | None = None, adapter_registry: Any | None = None) -> dict[str, Any]` is the sole dispatch seam.

- [ ] **Step 1: Add RED lane/boundary tests**

```python
def test_managed_lane_selects_only_relation_pairs(self):
    report = run_tests_v4(self.project, "python", self.document, self.automation, self.evidence, "managed")
    self.assertEqual({("FILE-int", "SYMBOL-int")}, self.evidence_pairs(report))

def test_technical_lane_runs_existing_inventory_but_is_never_authoritative(self):
    report = run_tests_v4(self.project, "python", self.document, self.automation, self.evidence, "technical_regression")
    self.assertEqual(self.all_existing_pairs(), self.evidence_pairs(report))
    self.assertFalse(report["evidence_authoritative"])
```

Cover Python/Java existing locators, generated exclusion from technical lane, file drift/locator ambiguity before subprocess, cross-lane evidence rejection, provider preflight only in managed lane, environment/path/digest preflight in both lanes, exact `--rootdir`, fresh IDs, and V3 rejection.

- [ ] **Step 2: Run RED**

```powershell
python -m unittest tests.test_run_tests_v3 tests.test_run_tests_skillsrc_v3 tests.test_validate_artifact -v
```

- [ ] **Step 3: Refactor registry selection, not subprocess algorithms**

```python
registry = automation_registry(automation_artifact) if execution_lane == "managed" else existing_registry(effective_technical_evidence)
pairs = (
    required_symbol_pairs(automation_artifact, canonical_document, effective_technical_evidence, project)
    if execution_lane == "managed"
    else technical_regression_pairs(effective_technical_evidence, project)
)
```

Keep Python/JUnit node binding, confinement, digest checks, XML parsing, and evidence truth table shared. Technical lane skips canonical provider/adapter preflight but performs every static/runtime safety gate.

- [ ] **Step 4: Run GREEN and CLI smoke**

```powershell
python -m unittest tests.test_run_tests_v3 tests.test_run_tests_skillsrc_v3 tests.test_validate_artifact -v
python tools\run_tests.py --help
```

CLI requires `--technical-evidence` and `--execution-lane managed|technical_regression` in addition to existing project/canonical/automation/module arguments.

- [ ] **Step 5: Commit**

```powershell
git add schemas/run-tests-output.schema.json tools/run_tests.py tests/test_run_tests_v3.py tests/test_run_tests_skillsrc_v3.py tests/test_validate_artifact.py
git commit -m "feat: separate managed and technical test execution"
```

### Task 5: Trace and Final Status Separation

**Files:**

- Modify: `schemas/trace-document.schema.json`
- Modify: `tools/build_trace_document.py`
- Modify: `tools/trace_check.py`
- Modify: `schemas/orchestrator-output.schema.json`
- Modify: `tools/orchestrate_test_case_revision.py`
- Modify: `tests/test_build_trace_v3.py`
- Modify: `tests/test_trace_check_v3.py`
- Modify: `tests/test_orchestration_v3.py`
- Modify: `skills/orchestrate/assets/orchestration-fixtures/accepted-trace-document.json`
- Modify: `skills/orchestrate/assets/orchestration-fixtures/accepted-orchestrator-output.json`
- Modify: `skills/orchestrate/assets/orchestration-fixtures/not-runnable-orchestrator-output.json`

**Interfaces:**

`build_trace(document, effective_technical_evidence, automation, managed_run_result, technical_regression_run_result) -> dict[str, Any]` is the sole trace constructor.

`finalize_orchestration(effective_document, effective_bundle_receipt, effective_technical_evidence, automation_artifact, autotest_review_artifact, managed_run_result, technical_regression_run_result, trace_document) -> Mapping[str, Any]` is the sole final carrier constructor.

`derive_overall_status(managed_status: str, technical_verdict: str | None) -> str` implements the locked precedence table and is used by both builder validation and finalization.

- [ ] **Step 1: Add RED trace/final truth-table tests**

```python
def test_overall_status_precedence(self):
    rows = (
        ("PASS", "PASS", "PASS"),
        ("PASS", "FAIL", "FAIL"),
        ("MANUAL_ONLY", "PASS", "MANUAL_ONLY"),
        ("PASS_WITH_MANUAL_REMAINDER", "NOT_RUNNABLE", "NOT_RUNNABLE"),
        ("FAIL", "NOT_RUNNABLE", "FAIL"),
    )
    for managed, technical, expected in rows:
        with self.subTest(managed=managed, technical=technical):
            self.assertEqual(expected, derive_overall_status(managed, technical))
```

Also test lane-swapped evidence, unit PASS not closing relation, technical FAIL retained, technical run omitted, stale evidence source, origin/scope copied into authoritative symbols, supporting requirement links distinct, manual/BLOCKED no-managed-run branches, and exact rebuild parity.

- [ ] **Step 2: Run RED**

```powershell
python -m unittest tests.test_build_trace_v3 tests.test_trace_check_v3 tests.test_orchestration_v3 -v
```

- [ ] **Step 3: Implement V4 trace and final carriers**

Trace fields include `technical_evidence_source`, authoritative `files/symbols/implementation_relations`, non-authoritative `supporting_requirement_relations`, `execution`, `technical_regression_execution`, `managed_status`, and `overall_status`. Trace validation rebuilds through the public `build_trace` seam.

- [ ] **Step 4: Run GREEN and schema fixture checks**

```powershell
python -m unittest tests.test_build_trace_v3 tests.test_trace_check_v3 tests.test_orchestration_v3 -v
python tools\validate_artifact.py schemas\trace-document.schema.json skills\orchestrate\assets\orchestration-fixtures\accepted-trace-document.json
python tools\validate_artifact.py schemas\orchestrator-output.schema.json skills\orchestrate\assets\orchestration-fixtures\accepted-orchestrator-output.json
```

- [ ] **Step 5: Commit**

```powershell
git add schemas/trace-document.schema.json schemas/orchestrator-output.schema.json tools/build_trace_document.py tools/trace_check.py tools/orchestrate_test_case_revision.py tests/test_build_trace_v3.py tests/test_trace_check_v3.py tests/test_orchestration_v3.py skills/orchestrate/assets/orchestration-fixtures
git commit -m "feat: trace functional and technical outcomes separately"
```

### Task 6: Pipeline 5.0, Docs, and Full InvenTree Acceptance

**Files:**

- Modify: `contracts/pipeline.json`
- Modify: `schemas/pipeline.schema.json`
- Modify: `tools/contract_check.py`
- Modify: `tools/doctor.py`
- Modify: `tools/render_contract_docs.py`
- Modify: `tools/audit_test_portfolio.py`
- Modify: `skills/orchestrate/SKILL.md`
- Modify: `skills/orchestrate/references/orchestration-contract.md`
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `HOW-IT-WORKS.md`
- Regenerate: `CONTRACTS.md`
- Regenerate: `PIPELINE.md`
- Rename: `tests/test_pipeline_v4.py` -> `tests/test_pipeline_v5.py`
- Modify: `tests/test_documentation_v3.py`
- Modify: `tests/test_test_portfolio_audit.py`
- Create: `.superpowers/sdd/2026-08-13-test-classification/phase-2-report.md` (ignored)

**Interfaces:**

Pipeline 5.0 tail is exact:

```text
effective_document + effective_technical_evidence
  -> tc-to-autotest
  -> autotest-reviewer
  -> managed run when authoritative pairs exist
  -> technical-regression run when full-project scope is requested and inventory is nonempty
  -> trace -> trace-check -> finalization
```

`tools/audit_test_portfolio.py phase2` remains a thin report composer. It invokes the public effective-evidence, automation, run, trace, and final validators, writes create-only compact JSON, and owns no duplicate relation or status policy.

- [ ] **Step 1: Add RED exact-route, docs, and audit tests**

Mutate lane order, omit technical evidence from automation, route unit evidence into managed run, keep any V3 stage version, or let technical FAIL retain overall PASS; each mutation must fail schema or `contract_check`.

```python
def test_pipeline_5_has_two_noninterchangeable_run_lanes(self):
    steps = {row["id"]: row for row in pipeline()["steps"]}
    self.assertEqual(["managed_run_result"], steps["run-managed-tests"]["produces"])
    self.assertEqual(["technical_regression_run_result"], steps["run-technical-regression"]["produces"])
```

Extend `tests/test_test_portfolio_audit.py` with a Phase-2 row requiring exact `managed_pairs`, `technical_pairs`, lane verdicts, `managed_status`, `overall_status`, and diagnostics; mutate one lane receipt and technical FAIL/overall PASS to prove the CLI delegates to the public validators.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_pipeline_v5 tests.test_documentation_v3 tests.test_test_portfolio_audit -v`

- [ ] **Step 3: Implement Pipeline 5.0 and regenerate docs**

Preserve the automatic `.skillsrc` bootstrap/module-selection wording verbatim. Document that ordinary feature runs use managed execution while explicit full-project runs add the entire technical inventory, with no numeric setting.

```powershell
python tools\render_contract_docs.py --root .
```

- [ ] **Step 4: Run focused gates, then one full repository suite**

```powershell
python -m unittest tests.test_automation_relations tests.test_run_tests_v3 tests.test_build_trace_v3 tests.test_trace_check_v3 tests.test_orchestration_v3 tests.test_pipeline_v5 tests.test_documentation_v3 tests.test_test_portfolio_audit -v
python tools\contract_check.py --root . --full
python tools\doctor.py --root .
python tools\render_contract_docs.py --root . --check
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q tools skills tests
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json tools/scan_project.py
```

Expected: all exit 0; protected-file diff empty; skips recorded verbatim.

- [ ] **Step 5: Run and preserve one full InvenTree pipeline**

In a new clean InvenTree worktree, run automatic `.skillsrc`, Phase-1 inventories/classification/review, managed scenario generation/review, automation reuse/generation/review, managed execution, the full technical-regression inventory, trace, and finalization. Do not set a case/test target.

Use this exact isolated target; if the path or branch already exists, stop and inspect it rather than deleting or reusing it:

```powershell
git -C D:\AI-Projects\real-chain-projects\inventree-clean worktree add -b codex/inventree-classified-pipeline-20260813 D:\AI-Projects\real-chain-projects\inventree-classified-pipeline-20260813 HEAD
```

Persist separate immutable directories:

```text
07-automation/
08-autotest-review/
09-managed-run/
10-technical-regression-run/
11-trace/
12-final/
13-audit/
```

Acceptance assertions:

```python
assert managed_pairs <= reviewed_integration_e2e_pairs
assert unit_pairs.isdisjoint(managed_pairs)
assert technical_pairs == all_existing_inventory_pairs
assert unit_pass_did_not_change_managed_status
assert overall_status == derive_overall_status(managed_status, technical_verdict)
assert canonical_and_projection_golden_digests_unchanged
```

Perform one fresh review of schema/semantic validation, relation authority, both run lanes, trace reconstruction, final status, and artifact preservation.

Before that review, create the replayable audit output:

```powershell
$inventreeWorktree = 'D:\AI-Projects\real-chain-projects\inventree-classified-pipeline-20260813'
$attemptRoot = 'D:\AI-Projects\real-chain-projects\inventree-classified-pipeline-20260813\docs\to_do\inventree-classified-pipeline-2026-08-13'
python tools\audit_test_portfolio.py phase2 --project $inventreeWorktree --effective-evidence "$attemptRoot\04-classifier-reviewer\effective-technical-evidence.json" --canonical-document "$attemptRoot\06-effective\canonical-document.json" --automation "$attemptRoot\07-automation\tc-to-autotest-output.json" --managed-run "$attemptRoot\09-managed-run\run-tests-output.json" --technical-run "$attemptRoot\10-technical-regression-run\run-tests-output.json" --trace "$attemptRoot\11-trace\trace-document.json" --final "$attemptRoot\12-final\orchestrator-output.json" --output "$attemptRoot\13-audit\portfolio-summary.json"
```

Record this resolved command verbatim in `phase-2-report.md`. Read back the output and require `status == "PASS"`, empty diagnostics, exact authoritative/technical pair counts, and `overall_status == derive_overall_status(managed_status, technical_verdict)`.

- [ ] **Step 6: Commit Pipeline 5.0 and any acceptance-driven RED/GREEN fixes**

```powershell
git add contracts/pipeline.json schemas/pipeline.schema.json tools/contract_check.py tools/doctor.py tools/render_contract_docs.py tools/audit_test_portfolio.py skills/orchestrate README.md USAGE.md HOW-IT-WORKS.md CONTRACTS.md PIPELINE.md tests/test_documentation_v3.py tests/test_test_portfolio_audit.py
git add -A tests/test_pipeline_v4.py tests/test_pipeline_v5.py
git commit -m "feat: complete classified test pipeline 5"
```

If InvenTree acceptance required additional files, stage them with `git add --patch`, inspect `git diff --cached --name-only`, and use a separate `fix: close classified pipeline acceptance gaps` commit only after their focused RED/GREEN rerun.
