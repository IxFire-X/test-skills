# End-to-End Skill Verification and Acceptance Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the portable skills generate complete, logical, runnable Java and Python tests on real projects, fail honestly on an incomplete project, and satisfy every design-spec acceptance criterion.

**Architecture:** Each fixture run is isolated and writes persistent evidence under `test-orchestration-skills/docs/to_do/`. Fresh agents receive only canonical skills and selected project context. Deterministic tools validate schemas, execute tests, and verify method-level SDD traces; human/agent review then evaluates semantic quality.

**Tech Stack:** `step5-java-demo` with Java 17/Maven/JUnit 5, `InvenTree-master` with its `.venv`/Django/pytest, incomplete `subscription-renewal-service`, JSON Schema, trace checker.

## Global Constraints

- Plans 1 and 2 must be accepted before this plan starts.
- Do not reuse agents that helped author the skills for forward tests.
- Do not provide forward-test agents hidden conversation context or expected output content.
- Persistent artifacts live only under `test-orchestration-skills/docs/to_do/`.
- Do not mutate original fixtures unless the task explicitly says so.
- A passing pre-existing test suite does not prove generated tests are valid; generated methods must execute and appear in trace evidence.
- Mojibake, placeholder assertions, invented requirements, missing negative cases, or orphan mappings fail acceptance.

---

### Task 1: Reproducible E2E workspace and artifact manifest

**Files:**
- Create: `docs/to_do/e2e/manifest.json`
- Create: `docs/to_do/e2e/java/README.md`
- Create: `docs/to_do/e2e/python/README.md`
- Create: `docs/to_do/e2e/not-runnable/README.md`
- Create: `tests/test_e2e_manifest.py`

**Interfaces:**
- Manifest records fixture roots, source commit/hash where available, skill-pack commit, agent/task identity, commands, artifact paths, and timestamps.
- Later tasks append run results but do not change the manifest schema.

- [ ] **Step 1: Write a failing manifest-schema test**

```python
import json
from pathlib import Path

def test_e2e_manifest_declares_all_required_runs(root):
    manifest = json.loads((root / "docs/to_do/e2e/manifest.json").read_text(encoding="utf-8"))
    assert {run["id"] for run in manifest["runs"]} == {"java-step5", "python-inventree", "not-runnable-subscription"}
    assert all(("docs", "to_do") in set(zip(Path(run["artifact_root"]).parts, Path(run["artifact_root"]).parts[1:])) for run in manifest["runs"])
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_e2e_manifest.py -q`

Expected: FAIL because the manifest is absent.

- [ ] **Step 3: Create the manifest with exact targets**

```json
{
  "schema_version": "1.0",
  "runs": [
    {"id":"java-step5", "fixture":"D:/AI-Projects/step5-java-demo", "target":"StudentController", "artifact_root":"test-orchestration-skills/docs/to_do/e2e/java"},
    {"id":"python-inventree", "fixture":"D:/AI-Projects/InvenTree-master", "target":"Part API", "artifact_root":"test-orchestration-skills/docs/to_do/e2e/python"},
    {"id":"not-runnable-subscription", "fixture":"D:/AI-Projects/subscription-renewal-service", "target":"project root", "artifact_root":"test-orchestration-skills/docs/to_do/e2e/not-runnable"}
  ]
}
```

- [ ] **Step 4: Document non-mutation and environment rules**

Java generated source lives in a copied workspace under the Java artifact root. Python generated test modules remain under the Python artifact root and execute against the original backend using its project venv and working directory.

- [ ] **Step 5: Run test and commit**

```bash
git add test-orchestration-skills/docs/to_do/e2e test-orchestration-skills/tests/test_e2e_manifest.py
git commit -m "test: define reproducible E2E fixtures"
```

### Task 2: Fresh-agent Java forward test

**Files:**
- Create: `docs/to_do/e2e/java/context.json`
- Create: `docs/to_do/e2e/java/test-cases.json`
- Create: `docs/to_do/e2e/java/test-cases.md`
- Create: `docs/to_do/e2e/java/test-case-review.json`
- Create: `docs/to_do/e2e/java/automation-bundle.json`
- Create: `docs/to_do/e2e/java/autotest-review.json`
- Create: `docs/to_do/e2e/java/run-result.json`
- Create: `docs/to_do/e2e/java/trace-result.json`
- Create: `docs/to_do/e2e/java/workspace/` as an isolated copy of `step5-java-demo`

**Interfaces:**
- Consumes canonical skills only.
- Generated Java methods carry stable TC IDs and requirement IDs.
- Execution Gate JSON supplies final method counts and verdict.

- [ ] **Step 1: Copy the fixture into the artifact workspace**

Use a recoverable copy and exclude `target/`. Verify the resolved destination begins with `D:\AI-Projects\test-orchestration-skills\docs\to_do\e2e\java\workspace` before writing.

- [ ] **Step 2: Dispatch a fresh agent with a context-only brief**

Give the agent:

```text
Read the canonical skills in skills/context-marker, skills/tc-generator, and skills/tc-reviewer.
Target only StudentController in the copied workspace.
Use actual source as technical evidence. Do not read prior run1/run2 artifacts.
Write context, test model, and review outputs to docs/to_do/e2e/java using the declared schemas.
Stop on ТРЕБУЕТ ДОРАБОТКИ.
```

- [ ] **Step 3: Validate manual-test artifacts**

Run `validate_artifact.py` for context, generator, and reviewer JSON. Inspect coverage for all seven real mappings:

```text
GET /student
GET /students
GET /students/{id}
GET /students/query
POST /students/create
PUT /students/{id}/update
DELETE /students/{id}/delete
```

Reject fake root `POST /` or `PUT /` endpoints and assumptions not present in code.

- [ ] **Step 4: Dispatch a second fresh agent for automation**

Give only accepted test artifacts, canonical automation/reviewer skills, project context, and copied workspace. Require generation in the copied workspace's normal `src/test/java` tree and artifact JSON under `docs/to_do/e2e/java`.

- [ ] **Step 5: Execute generated Java tests**

Set `JAVA_HOME=D:\AI-Projects\.tools\jdk-17` and Maven PATH, then run:

```powershell
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools\run_tests.py --project docs\to_do\e2e\java\workspace --language java
```

Expected: `PASS`; total is greater than the copied baseline `24`; every newly generated test method passes.

- [ ] **Step 6: Run SDD trace gate**

Merge requirement, case, file/method, and execution records into the trace document and run:

```powershell
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools\trace_check.py docs\to_do\e2e\java\trace-document.json --require-execution
```

Expected: valid; no missing/orphan/duplicate mapping.

- [ ] **Step 7: Commit Java evidence**

Commit JSON/Markdown reports and generated source inside the isolated workspace; exclude Maven `target/`.

### Task 3: Fresh-agent Python forward test

**Files:**
- Create: `docs/to_do/e2e/python/context.json`
- Create: `docs/to_do/e2e/python/test-cases.json`
- Create: `docs/to_do/e2e/python/test-cases.md`
- Create: `docs/to_do/e2e/python/test-case-review.json`
- Create: `docs/to_do/e2e/python/test_generated_part_api.py`
- Create: `docs/to_do/e2e/python/automation-bundle.json`
- Create: `docs/to_do/e2e/python/autotest-review.json`
- Create: `docs/to_do/e2e/python/run-result.json`
- Create: `docs/to_do/e2e/python/trace-result.json`

**Interfaces:**
- Target: InvenTree Part API under `InvenTree-master/src/backend/InvenTree/part`.
- Runner: `InvenTree-master/.venv/Scripts/python.exe` selected explicitly.
- Generated file stays under `docs/to_do` but executes with backend working directory/import path.

- [ ] **Step 1: Capture a clean scanner context**

Run scanner read-only against the exact Part API module selected by source inspection. Save output to `docs/to_do/e2e/python/context.json`; verify `.skillsrc` mtime and content remain unchanged.

- [ ] **Step 2: Dispatch a fresh manual-test agent**

The brief forbids reading `test_generated_api.py`, `test_generated_run3_api.py`, and previous `docs/to_do` outputs. Require a bounded test model for selected Part list/detail behavior with authentication, positive, negative, and boundary cases grounded in current source.

- [ ] **Step 3: Reject encoding and evidence defects before automation**

Assert every artifact decodes as UTF-8 and contains `ТК-`, not mojibake sequences such as `╨` or `тАФ`. Validate schema and require all endpoint/status claims to cite source evidence.

- [ ] **Step 4: Dispatch a second fresh automation agent**

Require pytest/Django conventions from the real project, isolated fixtures, meaningful response/domain assertions, and a complete trace map. Write the test module to `docs/to_do/e2e/python/test_generated_part_api.py`.

- [ ] **Step 5: Execute using the project venv**

Run from `D:\AI-Projects\InvenTree-master\src\backend`:

```powershell
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\test-orchestration-skills\tools\run_tests.py --project . --language python --python-executable D:\AI-Projects\InvenTree-master\.venv\Scripts\python.exe --pytest-target D:\AI-Projects\test-orchestration-skills\docs\to_do\e2e\python\test_generated_part_api.py
```

Invoke the tool with a Python interpreter as required by the platform. Expected: `PASS`, at least one collected generated test, no import/collection error.

- [ ] **Step 6: Run trace gate and compare with old defects**

Require valid method-level trace. Explicitly record that new artifacts contain no mojibake and do not inherit unverified assumptions from the old generated test modules.

- [ ] **Step 7: Commit Python evidence**

Commit only artifacts under `docs/to_do/e2e/python`; do not commit fixture database, logs, caches, or source-tree mutations.

### Task 4: Honest NOT_RUNNABLE fixture

**Files:**
- Create: `docs/to_do/e2e/not-runnable/scan-result.json`
- Create: `docs/to_do/e2e/not-runnable/run-result.json`
- Create: `docs/to_do/e2e/not-runnable/assessment.md`
- Create: `tests/test_not_runnable_fixture.py`

**Interfaces:**
- Target: `D:\AI-Projects\subscription-renewal-service`.
- Produces actionable missing-manifest/toolchain evidence without false PASS.

- [ ] **Step 1: Add a failing fixture assertion**

```python
import json

def test_incomplete_subscription_fixture_is_not_runnable(root):
    run_result = json.loads((root / "docs/to_do/e2e/not-runnable/run-result.json").read_text(encoding="utf-8"))
    assert run_result["verdict"] == "NOT_RUNNABLE"
    assert run_result["root_cause"]
    assert run_result["stats"]["total"] is None
```

- [ ] **Step 2: Run scanner and Execution Gate without modifying the fixture**

Capture stdout JSON directly into the declared artifact files. Do not create `.skillsrc`.

- [ ] **Step 3: Verify classification**

Expected: scanner or doctor reports absent project manifests/source; runner returns `NOT_RUNNABLE` with a specific remediation. Any `PASS` or generic `FAIL` is a core-runtime bug and returns to Plan 1.

- [ ] **Step 4: Run test and commit**

```bash
git add test-orchestration-skills/docs/to_do/e2e/not-runnable test-orchestration-skills/tests/test_not_runnable_fixture.py
git commit -m "test: verify honest not-runnable behavior"
```

### Task 5: Semantic artifact review

**Files:**
- Create: `docs/to_do/artifact-review.md`
- Create: `tests/test_artifact_text_quality.py`

**Interfaces:**
- Consumes Java/Python test models, source code, generated tests, run results, and trace results.
- Produces severity-ranked findings and a final `ACCEPT` or `REWORK` per language.

- [ ] **Step 1: Add objective text-quality guards**

```python
FORBIDDEN = ("assert True", "placeholder assertion", "╨", "тАФ")

def test_generated_artifacts_have_no_placeholders_or_mojibake(root):
    generated_texts = [path.read_text(encoding="utf-8") for path in (root / "docs/to_do/e2e").rglob("*") if path.suffix in {".md", ".py", ".java", ".json"}]
    for text in generated_texts:
        assert not any(token in text for token in FORBIDDEN)
```

- [ ] **Step 2: Review manual test models against source**

For every requirement and endpoint, record positive, negative, boundary, authorization/security, and applicable idempotency/concurrency/observability coverage. Mark `N/A` only with source-based justification.

- [ ] **Step 3: Review generated code**

Inspect setup isolation, deterministic data, meaningful assertions, framework conventions, cleanup, mocks/stubs, secrets, flaky waits, parameterization, and exact TC mapping. Cite file and line for every defect.

- [ ] **Step 4: Rework until both languages are accepted**

Any critical defect returns to the responsible fresh implementation agent, followed by schema, execution, and trace revalidation. Do not waive defects because tests happen to pass.

- [ ] **Step 5: Commit the review**

```bash
git add test-orchestration-skills/docs/to_do/artifact-review.md test-orchestration-skills/tests/test_artifact_text_quality.py
git commit -m "test: review generated testing artifacts"
```

### Task 6: Independent forward-skill tests

**Files:**
- Create: `docs/to_do/forward-tests/context-marker.md`
- Create: `docs/to_do/forward-tests/tc-generator.md`
- Create: `docs/to_do/forward-tests/tc-reviewer.md`
- Create: `docs/to_do/forward-tests/tc-to-autotest.md`
- Create: `docs/to_do/forward-tests/autotest-reviewer.md`
- Create: `docs/to_do/forward-tests/orchestrate.md`

**Interfaces:**
- Each fresh agent sees one canonical skill, the minimum fixture input, and its schema.
- Produces observed trigger behavior, contract validity, missing-context behavior, and ambiguity findings.

- [ ] **Step 1: Create one isolated prompt per skill**

Each prompt includes no conversation history and asks the agent to follow the skill exactly. Include a positive case and a pressure case with missing/contradictory context.

- [ ] **Step 2: Dispatch fresh agents**

Run independent tasks in parallel only when they write distinct report files. Agents must not edit the skills during this observation pass.

- [ ] **Step 3: Score each result**

Use four binary gates: correct trigger, exact artifacts, correct stop/fallback, no invented evidence. Any failure returns to Plan 2 and requires a new fresh forward test afterward.

- [ ] **Step 4: Commit reports**

```bash
git add test-orchestration-skills/docs/to_do/forward-tests
git commit -m "test: forward-test portable skills"
```

### Task 7: Final independent review and completion audit

**Files:**
- Create: `docs/to_do/final-sol-review.md`
- Create: `docs/to_do/completion-audit.md`
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes the complete diff, all regression output, E2E artifacts, semantic review, and forward-test reports.
- Produces the authoritative requirement-by-requirement completion verdict.

- [ ] **Step 1: Run verification-before-completion suite**

Run all pytest tests, ruff, contract check, generated-doc check, skill quick validation, Markdown link check, Java E2E, Python E2E, NOT_RUNNABLE fixture, artifact schema validation, and trace validation. Save exact commands and outputs.

- [ ] **Step 2: Request a fresh Sol review**

The reviewer is read-only and receives the design spec, master plan, full diff, and evidence paths. Require severity-ranked findings and an explicit `approve` or `change` verdict. Any critical/high finding is fixed and re-reviewed with fresh evidence.

- [ ] **Step 3: Build the completion matrix**

Use columns:

```text
Requirement | Authoritative evidence | Result | Remaining risk
```

Every acceptance criterion from the design spec must have direct evidence. `Unknown`, indirect evidence, or an unresolved warning means incomplete.

- [ ] **Step 4: Update roadmap honestly**

Mark only Java/Python baseline complete. Keep TypeScript/Go work visible and experimental unless their full evidence was added in a separately approved scope.

- [ ] **Step 5: Commit final evidence**

```bash
git add test-orchestration-skills/docs/to_do/final-sol-review.md test-orchestration-skills/docs/to_do/completion-audit.md test-orchestration-skills/ROADMAP.md
git commit -m "docs: complete testing skills acceptance audit"
```
