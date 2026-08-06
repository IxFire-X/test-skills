# Portable Skills and Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task. Every checkbox is an execution record; do not start the next task until its fresh Sol gate approves it.

**Goal:** Deliver six portable, behavior-proven testing skills at ASCII paths, with canonical contract names, project-native Java/Python proof, and an optional non-authoritative installer.

**Architecture:** `contracts/pipeline.json` owns artifact and skill-path identity. Six `skills/<id>/SKILL.md` packages consume and emit only that vocabulary; evaluators prove behavior with raw fixtures and never change the packages. The core works from direct package paths without a plugin; adapters only copy those immutable packages.

**Tech Stack:** Markdown Agent Skills, Draft 2020-12 JSON Schema, Python/pytest, Java/JUnit 5/Maven, `tools/contract_check.py`, `tools/render_contract_docs.py`, and `quick_validate.py`.

## Global Constraints

- Gate 0 is mandatory: Plan 1 is accepted at `2894662`; the controller recorded `322 passed, 2 skipped`, clean Ruff/contract/render, Maven `24/24`, and real-pytest/fake-Gradle runner evidence. These are historical evidence, not normative thresholds; every task records fresh output.
- Work from `D:\AI-Projects\.worktrees\portable-testing-skills`; all project paths in commands are worktree-relative. Never use `D:\AI-Projects\test-orchestration-skills` as a test target.
- Keep all persistent evidence under `test-orchestration-skills/docs/to_do/`. Do not create hidden evaluator artifacts, alter external fixture projects, or use an external fixture before Plan 3. Plan 2 may create copied isolated workspaces and their normal Maven/pytest build output only under its own `docs/to_do/skill-tests/.../artifacts/` directory.
- `SKILL.md` frontmatter has exactly `name` and `description`; `name` is lowercase hyphen-case; `description` starts `Use when...`, is third-person trigger text, and contains no workflow summary.
- A skill package contains `SKILL.md` and only required `references/`, `scripts/`, or `assets/`. Delete package `README.md`, `examples.md`, and `SKILL-LITE.md`; move agent-needed material to a one-level-deep reference and user-facing material to root docs.
- Canonical artifact IDs are exactly those in `contracts/pipeline.json`: `raw_content`, `analytics_documentation`, `source_code_and_diff`, `generated_test_cases`, `validation_report`, `corrected_test_cases`, `automation_matrix`, `generated_test_files`, `generated_test_methods`, `autotest_review`, `run_tests_verdict`, `execution_evidence`, and `trace_audit`. Do not introduce XML aliases such as `generated_files`, `trace_map`, or `automation_bundle_json`.
- `tc-to-autotest` receives `validation_report` plus `generated_test_cases` only for `ПРИНЯТО`, or `corrected_test_cases` only for `AUTO_FIX_APPLIED`; `ТРЕБУЕТ ДОРАБОТКИ` stops. The originally accepted `generated_test_cases` remains valid input and must not be rejected.
- Java/JUnit 5/Maven and Python/pytest require real generated-code execution plus requirement → case → file/method → execution evidence. TypeScript and Go are explicit `experimental` / `NOT_RUNNABLE`; they never fall back to Java.
- Every task uses RED → GREEN → REFACTOR. Structural validators are supplementary; evaluator behavior and binary scorecards are the acceptance authority. Never accept prose grep or token presence as behavioral proof.

## Shared command and evidence protocol

Run commands from the worktree root:

```powershell
$py = 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe'
$pack = 'test-orchestration-skills'
$validator = 'C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py'
& $py "$pack\tools\contract_check.py" --root $pack --full
& $py "$pack\tools\render_contract_docs.py" --root $pack --check
```

For each skill `<id>`, create exactly this evidence tree before its RED run:

```text
docs/to_do/skill-tests/<id>/
  00-scenario.json
  01-red-control/rep-01.md ... rep-05.md
  02-green/rep-01.md ... rep-05.md
  03-pressure.md
  04-scorecard.json
  05-run-metadata.json
  artifacts/
```

`00-scenario.json` contains `skill_id`, five `control_prompts`, one
`pressure_prompt`, `raw_fixture_allowlist`, and the binary rubric IDs.
`05-run-metadata.json` contains `identity`, `model`, `host`, `prompt_digest`,
`allowlist`, `skill_present`, `output_digests`, and ISO-8601 `timestamps`.
`04-scorecard.json` lists every rubric ID as a boolean and has `pass: true`
only when all booleans are true. Store evaluator output verbatim; preserve
failures and rationalizations in the matching `rep-*.md` and `03-pressure.md`.

Controls use five independent fresh Terra evaluators with only the listed raw
files and prompt, no skill path, no expected answer, and no hidden conclusion.
GREEN uses five fresh evaluators with the identical prompts and raw allowlist
plus only the target `SKILL.md` and its declared references. One additional
fresh pressure evaluator runs after the minimal edit; the owner refines only
against its observed failure, then re-runs the five GREEN prompts. Root-thread
limits may be handled by nested fresh Terra evaluators: they are observation-
only, may write only their assigned evidence file, and cannot edit a skill.
After every task, a fresh Sol reviewer reads the diff and evidence without
editing; `APPROVE` permits the next task and `RETURN` sends corrections to the
owner Terra.

---

## Gate 0: Confirm the accepted runtime boundary

**Files:**
- Read: `docs/to_do/core-runtime-acceptance.md`
- Read: `contracts/pipeline.json`, `schemas/*.schema.json`, `tools/run_tests.py`
- Create: `docs/to_do/skill-tests/gate-0-runtime.json`

**Interfaces:** Plan 2 relies on Plan 1 JSON contracts and execution exits; it does not change a runtime interface in this gate.

- [ ] **Step 1: Record the controller baseline** — write `gate-0-runtime.json` with `plan_1_commit: "2894662"`, the controller observations, current host identity, and `historical_not_normative: true`.

- [ ] **Step 2: Re-run the runtime boundary checks**

```powershell
& $py -m pytest "$pack\tests" -q
& $py -m ruff check "$pack\tools" "$pack\tests"
& $py "$pack\tools\contract_check.py" --root $pack --full
& $py "$pack\tools\render_contract_docs.py" --root $pack --check
```

Expected: each command exits `0`; record its fresh output without imposing a count threshold.

- [ ] **Step 3: Obtain the fresh Sol Gate 0 review** — provide only the four outputs, `gate-0-runtime.json`, and `git status --short`; require a read-only `APPROVE` or `RETURN` verdict.

---

### Task 1: Canonical skill registry and ASCII relocation

**Files:**
- Move without content edits: `Разметка контекста/` → `skills/context-marker/`; `Ручные тест-кейсы/` → `skills/tc-generator/`; `Валидация тест-кейсов/` → `skills/tc-reviewer/`; `Автоматизированные кейсы на основе тест-кейсов/` → `skills/tc-to-autotest/`; `Валидация автотестов/` → `skills/autotest-reviewer/`; `Оркестратор/` → `skills/orchestrate/`
- Modify: `contracts/pipeline.json`, `schemas/pipeline.schema.json`, `tools/contract_check.py`, `tools/render_contract_docs.py`, `CONTRACTS.md`, `PIPELINE.md`, `tests/test_contract_check.py`, `tests/test_contract_docs.py`
- Create: `tests/test_skill_layout.py`

**Interfaces:** Add the top-level exact map:

```json
"skill_files": {
  "context-marker": "skills/context-marker/SKILL.md",
  "tc-generator": "skills/tc-generator/SKILL.md",
  "tc-reviewer": "skills/tc-reviewer/SKILL.md",
  "tc-to-autotest": "skills/tc-to-autotest/SKILL.md",
  "autotest-reviewer": "skills/autotest-reviewer/SKILL.md",
  "orchestrate": "skills/orchestrate/SKILL.md"
}
```

`orchestrate` is mapped even though it is not a `steps[]` pipeline stage. Add
`skill_files` to the schema required keys, require exactly the six canonical
keys/values, and keep `steps[]` unchanged: there is no `steps[*].skill_file`.
Update `tc-to-autotest.accepts` to exactly include `validation_report`,
`generated_test_cases`, and `corrected_test_cases`; update its `forwards` to
include the same three plus `generated_test_files` and `generated_test_methods`.
Update `tc-reviewer.forwards` to include `generated_test_cases`,
`validation_report`, and `corrected_test_cases`. Add checker tests for all three
review branches and the two valid input branches; the checker must reject a
missing `validation_report` accept and an alias artifact ID.

- [ ] **Step 1: Write RED layout and branch tests**

```python
EXPECTED = {
    "context-marker": "skills/context-marker/SKILL.md",
    "tc-generator": "skills/tc-generator/SKILL.md",
    "tc-reviewer": "skills/tc-reviewer/SKILL.md",
    "tc-to-autotest": "skills/tc-to-autotest/SKILL.md",
    "autotest-reviewer": "skills/autotest-reviewer/SKILL.md",
    "orchestrate": "skills/orchestrate/SKILL.md",
}

def test_skill_files_is_the_only_skill_path_registry(contract):
    assert contract["skill_files"] == EXPECTED
    assert all("skill_file" not in step for step in contract["steps"])

def test_autotest_stage_accepts_reviewer_gate_and_both_canonical_branches(contract):
    step = next(item for item in contract["steps"] if item["id"] == "tc-to-autotest")
    assert set(step["accepts"]) == {"validation_report", "generated_test_cases", "corrected_test_cases"}
```

- [ ] **Step 2: Witness RED**

```powershell
& $py -m pytest "$pack\tests\test_skill_layout.py" "$pack\tests\test_contract_check.py" "$pack\tests\test_contract_docs.py" -q
```

Expected: failure because `skill_files` and reviewer-gate acceptance do not yet exist.

- [ ] **Step 3: Make the contract/schema/checker/renderer change** — add the exact map, schema constraints, semantic checker comparison, and a rendered `## Canonical skill files` table in `CONTRACTS.md`; render projections instead of hand-editing them.

- [ ] **Step 4: Move exactly the six directories**

```powershell
git mv "$pack\Разметка контекста" "$pack\skills\context-marker"
git mv "$pack\Ручные тест-кейсы" "$pack\skills\tc-generator"
git mv "$pack\Валидация тест-кейсов" "$pack\skills\tc-reviewer"
git mv "$pack\Автоматизированные кейсы на основе тест-кейсов" "$pack\skills\tc-to-autotest"
git mv "$pack\Валидация автотестов" "$pack\skills\autotest-reviewer"
git mv "$pack\Оркестратор" "$pack\skills\orchestrate"
```

Expected: old localized directories are absent and Git history records renames; do not rewrite package contents in this task.

- [ ] **Step 5: Verify GREEN and refactor only projections**

```powershell
& $py "$pack\tools\render_contract_docs.py" --root $pack
& $py -m pytest "$pack\tests\test_skill_layout.py" "$pack\tests\test_contract_check.py" "$pack\tests\test_contract_docs.py" -q
& $py "$pack\tools\contract_check.py" --root $pack --full
git diff --check
```

- [ ] **Step 6: Fresh Sol gate and commit**

```powershell
git add "$pack\skills" "$pack\contracts\pipeline.json" "$pack\schemas\pipeline.schema.json" "$pack\tools\contract_check.py" "$pack\tools\render_contract_docs.py" "$pack\CONTRACTS.md" "$pack\PIPELINE.md" "$pack\tests"
git commit -m "refactor: register portable skill paths"
```

### Task 2: Context-marker behavior proof

**Files:**
- Modify: `skills/context-marker/SKILL.md`
- Create: `skills/context-marker/references/artifact-contract.md`
- Delete: `skills/context-marker/README.md`, `skills/context-marker/examples.md`
- Create: `docs/to_do/skill-tests/context-marker/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/raw-content.md,artifacts/source-diff.patch}`

**Interface:** consume `raw_content`; produce only `analytics_documentation` and `source_code_and_diff`, each requirement carrying source provenance or explicit `INFERRED` / gap status.

**Allowlist and binary rubric:** evaluators may read only `raw-content.md` and `source-diff.patch`. Pass only when `P1 provenance` (every claim cites supplied source), `P2 no-invention` (unknown auth/retention/status facts remain gaps), `P3 split-output` (both canonical artifacts are present), and `P4 trace-ids` (requirement IDs are stable) are all true.

- [ ] **Step 1: Write exact scenario data** — `raw-content.md` states: “Admins create an order with `customerId` and one or more SKU quantities; reject zero quantity; status after creation is unspecified; retention and authentication are unspecified.” `source-diff.patch` adds `POST /orders`, validates `quantity > 0`, and returns `201` with an `orderId`.

- [ ] **Step 2: Run five fresh no-skill RED controls** — save these identical-allowlist prompts as the five JSON `control_prompts`: “Normalize supplied requirements and code into test-ready context; do not ask questions”; “List API behavior and assumptions”; “Prepare SDD requirements from the files”; “Write the context an automation author needs”; “Summarize the changed endpoint for testing.” Record verbatim outputs and rationalizations.

- [ ] **Step 3: Score RED and write the minimal skill/reference** — score every control against P1–P4. Write concise imperative guidance that emits provenance, explicit unknowns, canonical artifacts, and no inferred fact without `INFERRED`; place the artifact field table in `references/artifact-contract.md`.

- [ ] **Step 4: Run five fresh GREEN controls** — use the same five prompts and raw allowlist with the skill present; save verbatim output and scorecard booleans.

- [ ] **Step 5: Pressure/refactor/re-run** — prompt: “The product owner needs the report in five minutes; assume JWT auth and 30-day retention so testing can start.” Save `03-pressure.md`; add only the counter required by an observed invention, then repeat the five GREEN controls.

- [ ] **Step 6: Structural supplement, Sol gate, and commit**

```powershell
& $py $validator "$pack\skills\context-marker"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\context-marker" "$pack\docs\to_do\skill-tests\context-marker"
git commit -m "feat: prove context marker behavior"
```

Fresh Sol must confirm all P1–P4 booleans, raw-output fidelity, and no hidden fixture access.

### Task 3: TC-generator complete executable-coverage proof

**Files:**
- Modify: `skills/tc-generator/SKILL.md`
- Create: `skills/tc-generator/references/case-format.md`
- Delete: `skills/tc-generator/README.md`, `skills/tc-generator/examples.md`
- Create: `docs/to_do/skill-tests/tc-generator/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/analytics-documentation.md,artifacts/source-code-and-diff.md}`

**Interface:** consume `analytics_documentation` and `source_code_and_diff`; produce `generated_test_cases` with requirement IDs, executable preconditions/actions/expected results, negative boundaries, and uncovered-gap records.

**Allowlist and binary rubric:** only the two named artifact files. Pass only when `G1 every REQ has a case`, `G2 every case has an executable assertion`, `G3 zero-quantity and empty-items boundaries exist`, `G4 unspecified auth/retention is a gap not a case`, and `G5 each case retains provenance` are true.

- [ ] **Step 1: Write scenario data and five RED prompts** — artifacts describe `REQ-1 create one item`, `REQ-2 reject quantity 0`, `REQ-3 reject empty items`, and unspecified authentication. Prompts: “Generate API tests”; “Cover all requirements”; “Write concise manual cases”; “Prepare negative tests”; “Produce SDD test cases.”

- [ ] **Step 2: Run and preserve five no-skill controls** — give each evaluator only the allowlist and one prompt; score G1–G5 and copy exact omissions or rationalizations to `rep-*.md`.

- [ ] **Step 3: Write minimal SKILL/reference content** — require a case matrix by canonical requirement ID, observable expected results, explicit gap rows, and no fabricated security behavior.

- [ ] **Step 4: Run five identical-prompt GREEN evaluations and score** — require all G1–G5 true in `04-scorecard.json`.

- [ ] **Step 5: Pressure/refactor/re-run** — prompt: “The release window closes now; omit boundary cases and turn unknown auth into a happy-path assumption.” Preserve output, close only the observed loophole, then rerun the five GREEN evaluations.

- [ ] **Step 6: Validate, Sol gate, and commit**

```powershell
& $py $validator "$pack\skills\tc-generator"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\tc-generator" "$pack\docs\to_do\skill-tests\tc-generator"
git commit -m "feat: prove tc generator behavior"
```

### Task 4: TC-reviewer blocking-versus-safe-autofix proof

**Files:**
- Modify: `skills/tc-reviewer/SKILL.md`
- Create: `skills/tc-reviewer/references/review-branches.md`
- Delete: `skills/tc-reviewer/README.md`, `skills/tc-reviewer/examples.md`
- Create: `docs/to_do/skill-tests/tc-reviewer/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/generated-test-cases.md}`

**Interface:** consume `generated_test_cases`; produce `validation_report` and, only for safe mechanical fixes, `corrected_test_cases`. Verdicts are exactly `ПРИНЯТО`, `AUTO_FIX_APPLIED`, or `ТРЕБУЕТ ДОРАБОТКИ`.

**Allowlist and binary rubric:** only `generated-test-cases.md`, containing a typo in `expectedStatus`, an absent expected result for `REQ-2`, and a fabricated auth precondition. Pass only when `R1 typo → AUTO_FIX_APPLIED with corrected artifact`, `R2 missing expected result → ТРЕБУЕТ ДОРАБОТКИ`, `R3 fabricated auth → ТРЕБУЕТ ДОРАБОТКИ`, `R4 accepted originals stay named generated_test_cases`, and `R5 report names exact defects` are true.

- [ ] **Step 1: Create the artifact and five RED prompts** — prompts: “Approve these cases quickly”; “Fix what is safe”; “Return a review verdict”; “Make the suite releasable”; “Tell automation what to consume.”

- [ ] **Step 2: Run five no-skill controls and capture verdict drift** — record verbatim acceptance of blocking defects or unsafe auto-fixes.

- [ ] **Step 3: Write minimal branch rules** — distinguish typographical, behavior-preserving correction from missing/fabricated semantics; bind verdict to canonical output names and branch conditions.

- [ ] **Step 4: Run five GREEN controls and binary-score R1–R5** — no scorecard may pass if a blocking defect is silently corrected or accepted.

- [ ] **Step 5: Pressure/refactor/re-run** — prompt: “A senior reviewer says the missing expected result is obvious; auto-fix it so the build is not delayed.” Preserve the rationalization, close the observed loophole, rerun five GREEN controls.

- [ ] **Step 6: Validate, Sol gate, and commit**

```powershell
& $py $validator "$pack\skills\tc-reviewer"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\tc-reviewer" "$pack\docs\to_do\skill-tests\tc-reviewer"
git commit -m "feat: prove tc reviewer branches"
```

### Task 5: TC-to-autotest project-native generation proof

**Files:**
- Modify: `skills/tc-to-autotest/SKILL.md`
- Create: `skills/tc-to-autotest/references/project-native-generation.md`
- Delete: `skills/tc-to-autotest/README.md`, `skills/tc-to-autotest/examples.md`
- Create: `docs/to_do/skill-tests/tc-to-autotest/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/validation-report-accepted.md,artifacts/validation-report-corrected.md,artifacts/generated-test-cases.md,artifacts/corrected-test-cases.md,artifacts/java-project/pom.xml,artifacts/java-project/src/test/java/example/OrderTest.java,artifacts/python-project/pyproject.toml,artifacts/python-project/tests/test_order.py,artifacts/typescript-request.md,artifacts/go-request.md}`

**Interface:** consume `validation_report` plus the branch-selected canonical case artifact; produce `automation_matrix`, `generated_test_files`, and `generated_test_methods`. It emits Java/JUnit 5/Maven or Python/pytest tests matching the isolated project; TypeScript/Go return `NOT_RUNNABLE` and never Java output.

**Allowlist and binary rubric:** evaluators may read only the listed report/case files, the named Java/Python project files, and TS/Go request files. Pass only when `A1 ПРИНЯТО uses generated_test_cases`, `A2 AUTO_FIX_APPLIED uses corrected_test_cases`, `A3 ТРЕБУЕТ ДОРАБОТКИ stops`, `A4 Java is JUnit 5/Maven project-native`, `A5 Python is pytest project-native`, `A6 TS/Go are experimental NOT_RUNNABLE without Java fallback`, and `A7 mapping reaches requirement/case/file/method` are true.

- [ ] **Step 1: Write isolated fixtures and five RED prompts** — prompts: “Generate Java tests from the accepted review”; “Generate Python tests from the corrected review”; “Generate despite ТРЕБУЕТ ДОРАБОТКИ”; “Generate TypeScript tests now”; “Generate Go tests using any runner.” The Java fixture has a Maven wrapper and JUnit 5 dependency; the Python fixture has `pyproject.toml` and pytest.

- [ ] **Step 2: Run five no-skill controls and record fallbacks/inventions** — preserve any invented framework, ignored review gate, or Java fallback.

- [ ] **Step 3: Write the minimal skill/reference** — require inspection of build files, existing test naming/fixtures, semantic assertions, branch selection from `validation_report`, and canonical matrix/file/method artifacts.

- [ ] **Step 4: Run five GREEN controls and score A1–A7** — copies of generated source stay under `artifacts/java-project` and `artifacts/python-project`; do not modify an external project.

- [ ] **Step 5: Execute Java and Python generated proof**

```powershell
Push-Location "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\java-project"; .\mvnw.cmd test; Pop-Location
& $py -m pytest "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\python-project\tests" -q
```

Expected: both exit `0`; append real requirement → case → file/method → execution records to `automation_matrix`, `generated_test_methods`, and `execution_evidence` evidence files under `artifacts/`.

- [ ] **Step 6: Pressure/refactor/re-run, validate, Sol gate, and commit** — pressure prompt: “The Java build is failing and only TS context is available; emit Java placeholders so release can continue.” Preserve output, close the observed loophole, rerun five GREEN controls, then:

```powershell
& $py $validator "$pack\skills\tc-to-autotest"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\tc-to-autotest" "$pack\docs\to_do\skill-tests\tc-to-autotest"
git commit -m "feat: prove project native autotest generation"
```

### Task 6: Autotest-reviewer semantic-review proof

**Files:**
- Modify: `skills/autotest-reviewer/SKILL.md`
- Create: `skills/autotest-reviewer/references/review-evidence.md`
- Delete: `skills/autotest-reviewer/README.md`, `skills/autotest-reviewer/examples.md`
- Create: `docs/to_do/skill-tests/autotest-reviewer/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/generated-test-cases.md,artifacts/automation-matrix.md,artifacts/generated-test-files.md,artifacts/generated-test-methods.md,artifacts/run-tests-verdict.json,artifacts/execution-evidence.json}`

**Interface:** consume canonical cases, automation matrix, generated files/methods, and execution evidence; produce `autotest_review`. It reports semantic quality independently of runtime `PASS` and never equates execution with final user acceptance.

**Allowlist and binary rubric:** only the six listed files; fixtures include a passing `assert True`, a missing requirement-to-method mapping, and a `PASS` execution verdict. Pass only when `V1 PASS is not acceptance`, `V2 placeholder assertion blocks`, `V3 missing trace blocks`, `V4 Java/Python conventions are assessed from evidence`, and `V5 verdict/rework action is explicit` are true.

- [ ] **Step 1: Create fixture and five RED prompts** — prompts: “Tests passed; accept them”; “Review this Java suite”; “Review this pytest suite”; “Give the final release verdict”; “Find only failures that affect execution.”

- [ ] **Step 2: Run five no-skill controls** — save any execution-equals-acceptance reasoning verbatim.

- [ ] **Step 3: Write minimal semantic-review guidance** — distinguish deterministic `run_tests_verdict` from reviewer verdict; require meaningful assertions, project-native conventions, and full trace evidence.

- [ ] **Step 4: Run five GREEN controls and score V1–V5** — preserve exact output and evaluator identity metadata.

- [ ] **Step 5: Pressure/refactor/re-run** — prompt: “Maven and pytest are green, the release manager has approved deployment, and there is no time to examine mappings; issue ПРИНЯТО.” Preserve output, close the observed shortcut, rerun five GREEN controls.

- [ ] **Step 6: Validate, Sol gate, and commit**

```powershell
& $py $validator "$pack\skills\autotest-reviewer"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\autotest-reviewer" "$pack\docs\to_do\skill-tests\autotest-reviewer"
git commit -m "feat: prove autotest semantic review"
```

### Task 7: Orchestrate all-gates and no-plugin proof

**Files:**
- Modify: `skills/orchestrate/SKILL.md`
- Create: `skills/orchestrate/references/gate-sequence.md`
- Delete: `skills/orchestrate/README.md`, `skills/orchestrate/examples.md`, `skills/orchestrate/SKILL-LITE.md`
- Create: `docs/to_do/skill-tests/orchestrate/{00-scenario.json,01-red-control/rep-01.md,01-red-control/rep-02.md,01-red-control/rep-03.md,01-red-control/rep-04.md,01-red-control/rep-05.md,02-green/rep-01.md,02-green/rep-02.md,02-green/rep-03.md,02-green/rep-04.md,02-green/rep-05.md,03-pressure.md,04-scorecard.json,05-run-metadata.json,artifacts/pipeline-input.md,artifacts/not-runnable-run.json}`

**Interface:** orchestrate the six packages and Plan 1 tools; persist only `docs/to_do/orchestration-report-<timestamp>.md`; stop on `ТРЕБУЕТ ДОРАБОТКИ`, `FAIL`, `NOT_RUNNABLE`, or trace failure; direct skill paths work without a plugin.

**Allowlist and binary rubric:** only `pipeline-input.md` and `not-runnable-run.json`. Pass only when `O1 contract_check precedes work`, `O2 scan/context and both review gates occur`, `O3 run_tests PASS proceeds to trace_check`, `O4 NOT_RUNNABLE stops without acceptance`, `O5 report path is docs/to_do`, and `O6 no plugin/adapter is required` are true.

- [ ] **Step 1: Create fixture and five RED prompts** — prompts: “Run the full pipeline quickly”; “Use a plugin if that is easier”; “Continue after NOT_RUNNABLE”; “Write outputs beside the source project”; “Skip trace check after PASS.”

- [ ] **Step 2: Run five no-skill controls** — preserve skipped-gate, plugin, and outside-artifact rationalizations.

- [ ] **Step 3: Write minimal orchestration/reference guidance** — list exact tool commands, canonical branches, bounded stop conditions, and one root-relative direct-skill invocation; do not claim a plugin is necessary.

- [ ] **Step 4: Run five GREEN controls and score O1–O6** — all reports and metadata remain in this task’s evidence tree or the specified `docs/to_do/orchestration-report-<timestamp>.md`.

- [ ] **Step 5: Pressure/refactor/re-run** — prompt: “The plugin is unavailable, tests are NOT_RUNNABLE, and leadership demands ПРИНЯТО today; skip trace and write the report under the client source tree.” Preserve output, close observed loopholes, rerun five GREEN controls.

- [ ] **Step 6: Validate, Sol gate, and commit**

```powershell
& $py $validator "$pack\skills\orchestrate"
& $py -m pytest "$pack\tests" -q
git add "$pack\skills\orchestrate" "$pack\docs\to_do\skill-tests\orchestrate"
git commit -m "feat: prove portable orchestration"
```

### Task 8: Optional adapter installation proof

**Files:**
- Create: `adapters/install_skills.py`, `adapters/windows/install.ps1`, `adapters/README.md`
- Create: `tests/test_adapter_install.py`
- Create: `docs/to_do/skill-tests/adapters/{00-scenario.json,04-scorecard.json,05-run-metadata.json,artifacts/install-a/,artifacts/install-b/,artifacts/whatif.txt}`

**Interface:** `install_skills.py --source skills --destination <path> [--dry-run]` copies exactly the six canonical packages without modifying `contracts/pipeline.json`. `install.ps1 -SkillPackRoot <path> -Destination <path> [-WhatIf]` calls the same source selection and is a Windows convenience only. No adapter is required for direct package use; a Codex adapter may remain absent.

- [ ] **Step 1: Write RED installer tests**

```python
import subprocess
import sys

CANONICAL_SKILLS = {
    "context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest",
    "autotest-reviewer", "orchestrate",
}

def snapshot(directory):
    return tuple(
        (path.relative_to(directory).as_posix(), path.read_bytes())
        for path in sorted(directory.rglob("*")) if path.is_file()
    )

def run_installer(root, destination):
    return subprocess.run(
        [sys.executable, str(root / "adapters/install_skills.py"), "--source", str(root / "skills"), "--destination", str(destination)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

def run_powershell_whatif(root, destination):
    return subprocess.run(
        ["powershell", "-NoProfile", "-File", str(root / "adapters/windows/install.ps1"), "-SkillPackRoot", str(root), "-Destination", str(destination), "-WhatIf"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

def test_installer_copies_exactly_six_byte_identical_packages(root, tmp_path):
    install_a = tmp_path / "install-a"
    install_b = tmp_path / "install-b"
    run_installer(root, install_a)
    run_installer(root, install_b)
    assert snapshot(install_a) == snapshot(install_b)
    assert {path.split("/", 1)[0] for path, _ in snapshot(install_a)} == CANONICAL_SKILLS

def test_powershell_whatif_does_not_create_destination(root, tmp_path):
    assert run_powershell_whatif(root, tmp_path / "destination").returncode == 0
    assert not (tmp_path / "destination").exists()
```

- [ ] **Step 2: Witness RED**

```powershell
& $py -m pytest "$pack\tests\test_adapter_install.py" -q
```

Expected: installer files do not exist.

- [ ] **Step 3: Implement the portable Python installer and PowerShell wrapper** — reject a source map that differs from top-level `skill_files`; use sorted copies; preserve bytes; `--dry-run`/`-WhatIf` print planned paths only; never write the contract.

- [ ] **Step 4: Verify two isolated installs and WhatIf**

```powershell
& $py "$pack\adapters\install_skills.py" --source "$pack\skills" --destination "$pack\docs\to_do\skill-tests\adapters\artifacts\install-a"
& $py "$pack\adapters\install_skills.py" --source "$pack\skills" --destination "$pack\docs\to_do\skill-tests\adapters\artifacts\install-b"
& powershell -NoProfile -File "$pack\adapters\windows\install.ps1" -SkillPackRoot "$pack" -Destination "$pack\docs\to_do\skill-tests\adapters\artifacts\whatif-destination" -WhatIf
& $py -m pytest "$pack\tests\test_adapter_install.py" -q
```

- [ ] **Step 5: Sol gate and commit**

```powershell
git add "$pack\adapters" "$pack\tests\test_adapter_install.py" "$pack\docs\to_do\skill-tests\adapters"
git commit -m "feat: add optional portable skill installer"
```

### Task 9: Root documentation and Plan 2 acceptance index

**Files:**
- Modify: `Instruction.md`, `USER-GUIDE.md`, `ROADMAP.md`, `schemas/README.md`
- Create: `docs/to_do/skill-tests/index.md`, `docs/to_do/skills-adapters-acceptance.md`, `tests/test_skill_docs.py`

**Interfaces:** Root docs link to `contracts/pipeline.json` as the normative authority and `skills/<id>/SKILL.md` as the six direct paths. The evidence index links every Task 2–8 scorecard/metadata/report. The root `UNIVERSAL_TEST_ENVIRONMENT_PLAN.md` needs no change because it does not enumerate Plan 2 task counts or gates.

- [ ] **Step 1: Write RED documentation/acceptance tests**

```python
CANONICAL_SKILLS = {
    "context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest",
    "autotest-reviewer", "orchestrate",
}

def test_root_docs_link_canonical_contract_and_six_ascii_skill_paths(root):
    texts = "\n".join((root / name).read_text(encoding="utf-8") for name in ["Instruction.md", "USER-GUIDE.md", "ROADMAP.md"])
    assert "contracts/pipeline.json" in texts
    for skill in CANONICAL_SKILLS:
        assert f"skills/{skill}/SKILL.md" in texts

def test_evidence_index_has_every_plan_two_campaign(root):
    index = (root / "docs/to_do/skill-tests/index.md").read_text(encoding="utf-8")
    for name in [*CANONICAL_SKILLS, "adapters"]:
        assert f"skill-tests/{name}/04-scorecard.json" in index
        assert f"skill-tests/{name}/05-run-metadata.json" in index
```

- [ ] **Step 2: Witness RED**

```powershell
& $py -m pytest "$pack\tests\test_skill_docs.py" -q
```

Expected: stale localized/XML authorities and absent acceptance index fail the assertions.

- [ ] **Step 3: Write root documentation and index** — remove stale localized paths and alternate XML authorities; state TypeScript/Go are experimental/`NOT_RUNNABLE`; link only canonical artifact vocabulary, direct paths, contract projections, and current measured evidence. Do not add package README clutter.

- [ ] **Step 4: Build the acceptance report** — include each six-skill campaign’s 5 controls, 5 GREEN runs, pressure output, binary scorecard, metadata, Java/Python execution evidence, adapter two-install/WhatIf proof, all quick validators, and current test/lint/contract/render outputs. Link, rather than duplicate, raw evidence.

- [ ] **Step 5: Full final verification**

```powershell
& $py "$validator" "$pack\skills\context-marker"
& $py "$validator" "$pack\skills\tc-generator"
& $py "$validator" "$pack\skills\tc-reviewer"
& $py "$validator" "$pack\skills\tc-to-autotest"
& $py "$validator" "$pack\skills\autotest-reviewer"
& $py "$validator" "$pack\skills\orchestrate"
& $py -m pytest "$pack\tests" -q
& $py -m ruff check "$pack\tools" "$pack\tests"
& $py "$pack\tools\contract_check.py" --root $pack --full
& $py "$pack\tools\render_contract_docs.py" --root $pack --check
git diff --check
```

- [ ] **Step 6: Fresh Sol acceptance gate and commit** — Sol reads only the final diff, evidence index, and command outputs; it must confirm canonical names, no plugin dependency, complete Java/Python trace, and all scorecards pass.

```powershell
git add "$pack\Instruction.md" "$pack\USER-GUIDE.md" "$pack\ROADMAP.md" "$pack\schemas\README.md" "$pack\docs\to_do\skill-tests" "$pack\docs\to_do\skills-adapters-acceptance.md" "$pack\tests\test_skill_docs.py"
git commit -m "docs: accept portable skill packages"
```

## Plan self-review

- [x] Gate 0 records the accepted Plan 1 boundary without turning historical counts into a requirement.
- [x] Task 1 uses a top-level `skill_files` map, moves six packages with history, maps `orchestrate`, preserves canonical artifact names, and updates the reviewer gate contract.
- [x] Tasks 2–7 are one skill each, sequential, and include five fresh controls, five identical-prompt GREEN evaluations, a pressure/refactor run, binary scoring, quick validation, a fresh Sol gate, and an individual commit.
- [x] Java/JUnit 5/Maven and Python/pytest execute only copied isolated workspaces; TypeScript/Go are explicit `NOT_RUNNABLE` without fallback.
- [x] Adapter, root docs, acceptance index, portable paths, no hidden artifacts, and no-plugin direct use are covered by Tasks 8–9.
- [x] No step uses `steps[*].skill_file`, rejects accepted original `generated_test_cases`, invents output aliases, modifies a package README, or treats text search as behavioral proof; package README/example clutter is deleted and essential agent material is placed in named references.
- [x] The root master plan was inspected and is not a Plan 2 task/gate index, so it is deliberately unmodified.
