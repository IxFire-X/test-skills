# Portable Core and Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a deterministic, schema-first runtime that validates the testing pipeline, scans projects safely, executes Java/Python tests honestly, and proves SDD traceability.

**Architecture:** Keep `contracts/pipeline.json` normative and make every Markdown projection mechanically checkable. Tools are small Python CLIs with JSON stdout, explicit exit codes, no silent writes, and shared `docs/to_do` confinement.

**Tech Stack:** Python 3.10+, `jsonschema>=4.23,<5`, `PyYAML>=6,<7`, pytest, ruff, Java 17/Maven/Gradle, pytest project runners.

## Global Constraints

- Preserve current uncommitted work; inspect each owned diff before editing.
- Use tests before behavior changes.
- Persistent output paths must resolve inside an exact `docs/to_do` ancestor.
- Command-not-found and unavailable dependencies produce `NOT_RUNNABLE`, not `FAIL` or exit-code-zero success.
- Tool JSON is UTF-8 and deterministic apart from explicitly documented timestamps.
- Do not add TypeScript or Go execution claims in this plan.

---

### Task 1: Runtime dependency manifest and doctor

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `tools/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `tests/conftest.py`
- Modify: `USER-GUIDE.md`

**Interfaces:**
- Produces: `doctor.inspect_environment(root: Path) -> dict[str, object]`
- Produces CLI: `python tools/doctor.py --root <skill-pack>`
- Later tasks rely on dependency names and machine-readable readiness checks.

- [ ] **Step 0: Extend shared test fixtures with exact tool names**

```python
import json

@pytest.fixture
def root():
    return ROOT

@pytest.fixture
def doctor():
    return load_tool("doctor")

@pytest.fixture
def render_contract_docs():
    return load_tool("render_contract_docs")

@pytest.fixture
def runner():
    return load_tool("run_tests")

@pytest.fixture
def trace_check():
    return load_tool("trace_check")

@pytest.fixture
def contract():
    return json.loads((ROOT / "contracts/pipeline.json").read_text(encoding="utf-8"))
```

- [ ] **Step 1: Write failing doctor tests**

```python
def test_doctor_reports_core_and_optional_capabilities(doctor, tmp_path):
    report = doctor.inspect_environment(tmp_path)
    assert report["python"]["supported"] is True
    assert report["dependencies"]["jsonschema"]["required"] is True
    assert report["languages"]["typescript"]["execution"] is False

def test_doctor_never_claims_ready_when_required_dependency_is_missing(doctor, monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    report = doctor.inspect_environment(tmp_path)
    assert report["status"] == "NOT_RUNNABLE"
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_doctor.py -q`

Expected: FAIL because `tools/doctor.py` does not exist.

- [ ] **Step 3: Add exact dependency manifests**

```text
# requirements.txt
jsonschema>=4.23,<5
PyYAML>=6,<7

# requirements-dev.txt
-r requirements.txt
pytest>=8,<10
pytest-cov>=6,<8
ruff>=0.9,<1
```

- [ ] **Step 4: Implement the minimal doctor**

```python
def inspect_environment(root: Path) -> dict[str, object]:
    dependencies = {
        name: {"required": True, "available": importlib.util.find_spec(name) is not None}
        for name in ("jsonschema", "yaml")
    }
    ready = all(item["available"] for item in dependencies.values())
    return {
        "status": "PASS" if ready else "NOT_RUNNABLE",
        "python": {"supported": sys.version_info >= (3, 10), "version": platform.python_version()},
        "dependencies": dependencies,
        "languages": {
            "java": {"execution": True},
            "python": {"execution": True},
            "typescript": {"execution": False},
            "go": {"execution": False},
        },
        "root": str(root.resolve()),
    }
```

- [ ] **Step 5: Run tests and CLI smoke check**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_doctor.py -q`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/doctor.py --root .`

Expected: tests PASS; CLI JSON has `status: PASS` in the audit venv.

- [ ] **Step 6: Commit only Task 1 files**

```bash
git add test-orchestration-skills/requirements*.txt test-orchestration-skills/tools/doctor.py test-orchestration-skills/tests/conftest.py test-orchestration-skills/tests/test_doctor.py test-orchestration-skills/USER-GUIDE.md
git commit -m "feat: add portable runtime doctor"
```

### Task 2: Canonical contract and generated documentation projections

**Files:**
- Modify: `contracts/pipeline.json`
- Modify: `schemas/pipeline.schema.json`
- Modify: `tools/contract_check.py`
- Create: `tools/render_contract_docs.py`
- Modify: `CONTRACTS.md`
- Modify: `PIPELINE.md`
- Modify: `tests/test_contract_check.py`
- Create: `tests/test_contract_docs.py`

**Interfaces:**
- Consumes: Draft 2020-12 contract schema.
- Produces: `render_contract_docs.render_contracts(contract: dict) -> str`
- Produces: `render_contract_docs.render_pipeline(contract: dict) -> str`
- Produces CLI check: `python tools/render_contract_docs.py --root . --check`

- [ ] **Step 1: Add failing projection and semantic tests**

```python
def test_markdown_projections_equal_rendered_contract(render_contract_docs, root):
    contract = json.loads((root / "contracts/pipeline.json").read_text(encoding="utf-8"))
    assert (root / "CONTRACTS.md").read_text(encoding="utf-8") == render_contract_docs.render_contracts(contract)
    assert (root / "PIPELINE.md").read_text(encoding="utf-8") == render_contract_docs.render_pipeline(contract)

def test_pass_transition_is_terminal_not_user_rework(contract_check, contract, root):
    report = contract_check.validate_pipeline_contract(contract, root, check_drift=False)
    assert report["status"] == "passed"
    transition = next(t for t in contract["transitions"] if t.get("when", {}).get("execution_verdict") == "PASS")
    assert transition["transform"] == "complete"
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_contract_check.py tests/test_contract_docs.py -q`

Expected: projection test fails and PASS transition exposes the current ambiguous transform.

- [ ] **Step 3: Normalize terminal transitions and schema**

Use explicit transforms:

```json
{"execution_verdict":"PASS", "transform":"complete"}
{"execution_verdict":"FAIL", "transform":"stop_failed"}
{"execution_verdict":"NOT_RUNNABLE", "transform":"stop_not_runnable"}
```

Extend semantic validation to reject any other terminal transform and to validate each declared artifact projection path.

- [ ] **Step 4: Implement deterministic Markdown rendering**

```python
def render_pipeline(contract: dict) -> str:
    rows = ["| Step | Accepts | Produces |", "|---|---|---|"]
    for step in contract["steps"]:
        rows.append(f"| `{step['id']}` | {', '.join(step.get('accepts', []))} | {', '.join(step.get('produces', []))} |")
    return "# Pipeline\n\nGenerated from `contracts/pipeline.json`. Do not edit manually.\n\n" + "\n".join(rows) + "\n"
```

Render verdict branches and capability tables in `CONTRACTS.md`; include a generated-file marker and `--check` byte comparison.

- [ ] **Step 5: Regenerate projections and verify**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/render_contract_docs.py --root .`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/render_contract_docs.py --root . --check`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/contract_check.py --root . --full`

Expected: all exit 0; TypeScript/Go may be warnings only.

- [ ] **Step 6: Commit Task 2**

```bash
git add test-orchestration-skills/contracts test-orchestration-skills/schemas/pipeline.schema.json test-orchestration-skills/tools/contract_check.py test-orchestration-skills/tools/render_contract_docs.py test-orchestration-skills/CONTRACTS.md test-orchestration-skills/PIPELINE.md test-orchestration-skills/tests/test_contract_check.py test-orchestration-skills/tests/test_contract_docs.py
git commit -m "feat: make pipeline contract authoritative"
```

### Task 3: Scanner and Java/Python runner completion

**Files:**
- Modify: `tools/scan_project.py`
- Modify: `tools/run_tests.py`
- Modify: `schemas/scan-project-output.schema.json`
- Modify: `schemas/run-tests-output.schema.json`
- Modify: `tests/test_scan_project.py`
- Modify: `tests/test_run_tests.py`

**Interfaces:**
- Produces: `run_tests.select_python_interpreter(project_dir: str, override: str | None) -> str | None`
- CLI adds: `--python-executable <path>`
- Preserves: wrapper-first Java selection and exact `docs/to_do` confinement.

- [ ] **Step 1: Add failing Python environment-selection tests**

```python
def test_python_override_precedes_current_interpreter(runner, tmp_path):
    executable = tmp_path / ("python.exe" if runner.os.name == "nt" else "python")
    executable.write_text("", encoding="utf-8")
    assert runner.select_python_interpreter(str(tmp_path), str(executable)) == str(executable)

def test_project_venv_precedes_host_interpreter(runner, tmp_path, monkeypatch):
    venv_python = tmp_path / ".venv" / ("Scripts/python.exe" if runner.os.name == "nt" else "bin/python")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    assert runner.select_python_interpreter(str(tmp_path), None) == str(venv_python)
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_run_tests.py -q`

Expected: FAIL because the selection function and flag are absent.

- [ ] **Step 3: Implement interpreter selection and actionable NOT_RUNNABLE**

```python
def select_python_interpreter(project_dir: str, override: str | None) -> str | None:
    candidates = [override]
    root = Path(project_dir)
    candidates.extend([root / ".venv/Scripts/python.exe", root / ".venv/bin/python"])
    candidates.append(Path(sys.executable))
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)
```

Use the selected interpreter for both `import pytest` probing and `-m pytest` execution. Include the chosen path in `environment.interpreter_path`.

- [ ] **Step 4: Add scanner negative fixtures**

Add tests for Python manifest detection, output traversal, Java comments/text blocks, missing target, and read-only `.skillsrc`. Preserve source CDATA exactly while parsing a comment-stripped copy.

- [ ] **Step 5: Run targeted and real fixture checks**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_scan_project.py tests/test_run_tests.py -q`

Run Java with JDK 17 and `step5-java-demo`; expected `PASS`, total `24` before new E2E tests.

Run Python environment discovery against `D:\AI-Projects\InvenTree-master`; expected selected interpreter ends in `.venv\Scripts\python.exe`.

- [ ] **Step 6: Commit Task 3**

```bash
git add test-orchestration-skills/tools/scan_project.py test-orchestration-skills/tools/run_tests.py test-orchestration-skills/schemas/scan-project-output.schema.json test-orchestration-skills/schemas/run-tests-output.schema.json test-orchestration-skills/tests/test_scan_project.py test-orchestration-skills/tests/test_run_tests.py
git commit -m "feat: harden project scanning and execution"
```

### Task 4: Artifact schemas and generic validator

**Files:**
- Modify: `schemas/context-marker-output.schema.json`
- Modify: `schemas/tc-generator-output.schema.json`
- Modify: `schemas/tc-reviewer-output.schema.json`
- Modify: `schemas/tc-to-autotest-output.schema.json`
- Modify: `schemas/autotest-reviewer-output.schema.json`
- Modify: `schemas/orchestrator-output.schema.json`
- Modify: `schemas/skillsrc.schema.json`
- Modify: `schemas/README.md`
- Modify: `shared/schema-validator.md`
- Modify: `tools/validate_artifact.py`
- Create: `tests/test_artifact_contracts.py`

**Interfaces:**
- Produces: `validate_artifact.validate(schema_path: str, artifact_path: str) -> tuple[int, dict]`
- Every schema uses Draft 2020-12 and stable `additionalProperties: false` boundaries.

- [ ] **Step 1: Write schema meta-tests and positive/negative fixtures**

```python
@pytest.mark.parametrize("schema_path", sorted(SCHEMA_DIR.glob("*.schema.json")))
def test_schema_is_valid_draft_2020_12(schema_path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

def test_tc_generator_requires_requirement_coverage():
    schema = json.loads((SCHEMA_DIR / "tc-generator-output.schema.json").read_text(encoding="utf-8"))
    invalid = {"requirements": [], "test_cases": [], "coverage": []}
    errors = list(Draft202012Validator(schema).iter_errors(invalid))
    assert errors
```

- [ ] **Step 2: Verify RED against current interrupted schema work**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_artifact_contracts.py -q`

Expected: at least one missing field/envelope inconsistency fails.

- [ ] **Step 3: Define one envelope per stage**

Use exact top-level keys:

```json
{"schema_version":"2.1.0", "stage":"tc-generator", "artifacts":{}, "warnings":[]}
```

Require requirement provenance, TC coverage, generated files/methods, trace mappings, review verdicts, and execution evidence where applicable. Keep central Russian review verdict values; do not introduce parallel English verdict enums.

- [ ] **Step 4: Finish validator diagnostics**

Preserve exit codes `0=valid`, `1=invalid artifact`, `2=invalid schema/input/missing dependency`. Emit paths as JSON Pointers such as `/artifacts/test_cases/0/id`, not Python repr fragments.

- [ ] **Step 5: Run all schema and CLI tests**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_artifact_contracts.py -q`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\ruff.exe check tools/validate_artifact.py tests/test_artifact_contracts.py`

- [ ] **Step 6: Commit Task 4**

```bash
git add test-orchestration-skills/schemas test-orchestration-skills/shared/schema-validator.md test-orchestration-skills/tools/validate_artifact.py test-orchestration-skills/tests/test_artifact_contracts.py
git commit -m "feat: align pipeline artifact schemas"
```

### Task 5: SDD trace checker

**Files:**
- Create: `schemas/trace-document.schema.json`
- Modify: `tools/trace_check.py`
- Create: `tests/test_trace_check.py`
- Modify: `shared/trace-mapper.md`

**Interfaces:**
- Produces: `trace_check.check(document: dict[str, object], require_execution: bool = False) -> dict[str, object]`
- Produces CLI exit codes: `0=valid`, `1=invalid trace`, `2=input/schema/runtime error`.

- [ ] **Step 1: Write failing trace invariant tests**

```python
import copy

import pytest

@pytest.fixture
def valid_trace():
    return {
        "requirements": [{"id": "REQ-1", "provenance": "source"}],
        "test_cases": [{"id": "TC-1", "requirement_ids": ["REQ-1"]}],
        "generated_files": [{"path": "tests/test_api.py"}],
        "methods": [{"file": "tests/test_api.py", "name": "test_tc_1"}],
        "trace_map": [{"requirement_id": "REQ-1", "tc_id": "TC-1", "file": "tests/test_api.py", "method": "test_tc_1"}],
        "execution_required": True,
        "execution": {"verdict": "PASS", "tests": [{"file": "tests/test_api.py", "method": "test_tc_1", "status": "passed"}]},
        "final_verdict": "PASS",
    }

@pytest.fixture
def trace_fixture(valid_trace):
    def build(mutation):
        document = copy.deepcopy(valid_trace)
        if mutation == "missing_requirement_mapping":
            document["trace_map"] = []
        elif mutation == "orphan_method":
            document["methods"].append({"file": "tests/test_api.py", "name": "test_orphan"})
        elif mutation == "duplicate_mapping":
            document["trace_map"].append(copy.deepcopy(document["trace_map"][0]))
        elif mutation == "not_runnable":
            document["execution"] = {"verdict": "NOT_RUNNABLE", "tests": []}
        return document
    return build

def test_full_trace_with_passed_execution_is_valid(trace_check, valid_trace):
    assert trace_check.check(valid_trace, require_execution=True)["valid"] is True

@pytest.mark.parametrize("mutation,code", [
    ("missing_requirement_mapping", "MISSING_MAPPING"),
    ("orphan_method", "ORPHAN_METHOD"),
    ("duplicate_mapping", "DUPLICATE_MAPPING"),
    ("not_runnable", "EXECUTION_GATE"),
])
def test_invalid_trace_is_rejected(trace_check, trace_fixture, mutation, code):
    result = trace_check.check(trace_fixture(mutation), require_execution=True)
    assert code in {error["code"] for error in result["errors"]}
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_trace_check.py -q`

Expected: current partial implementation misses schema validation, exit-code-2 input errors, or execution-to-method evidence.

- [ ] **Step 3: Require method-level execution evidence**

Extend each execution record to include `file`, `method`, and `status`. Reject a final PASS when any mapped method is absent, failed, errored, or skipped without an explicit allowed-skip rule.

```python
executed = {(item["file"], item["method"]) for item in execution["tests"] if item["status"] == "passed"}
for key in sorted(mapped_methods - executed):
    _error(errors, "MISSING_EXECUTION", f"mapped method did not pass: {key[0]}::{key[1]}")
```

- [ ] **Step 4: Validate input against trace schema before semantics**

Return code `invalid_input_schema` with JSON Pointer paths when the document shape is wrong. Keep semantic errors separate.

- [ ] **Step 5: Run tests, ruff, and CLI smoke**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_trace_check.py -q`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\ruff.exe check tools/trace_check.py tests/test_trace_check.py`

- [ ] **Step 6: Commit Task 5**

```bash
git add test-orchestration-skills/schemas/trace-document.schema.json test-orchestration-skills/tools/trace_check.py test-orchestration-skills/tests/test_trace_check.py test-orchestration-skills/shared/trace-mapper.md
git commit -m "feat: enforce end-to-end SDD traces"
```

### Task 6: Core runtime acceptance gate

**Files:**
- Create: `tests/test_cli_smoke.py`
- Create: `docs/to_do/core-runtime-acceptance.md`
- Modify: `USER-GUIDE.md`

**Interfaces:**
- Consumes every CLI from Tasks 1–5.
- Produces a reproducible acceptance report under `docs/to_do`.

- [ ] **Step 1: Add subprocess smoke tests for every CLI**

```python
@pytest.mark.parametrize("tool,args", [
    ("doctor.py", ["--root", str(ROOT)]),
    ("contract_check.py", ["--root", str(ROOT), "--full"]),
    ("render_contract_docs.py", ["--root", str(ROOT), "--check"]),
])
def test_cli_returns_json_or_clean_check(tool, args):
    result = subprocess.run([sys.executable, ROOT / "tools" / tool, *args], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout
```

- [ ] **Step 2: Run the complete core suite**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests -q`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\ruff.exe check tools tests`

Expected: all tests and lint pass.

- [ ] **Step 3: Re-run real Java baseline**

Set `JAVA_HOME=D:\AI-Projects\.tools\jdk-17` and place Maven on PATH.

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/run_tests.py --project D:\AI-Projects\step5-java-demo --language java`

Expected: `PASS`, `24` total baseline tests, wrapper `mvnw.cmd`, `javac 17.0.10` or newer compatible Java 17.

- [ ] **Step 4: Write the acceptance report from exact outputs**

Record command, exit code, verdict, counts, and unresolved warnings. Do not state that skills themselves are complete; this report accepts only the runtime.

- [ ] **Step 5: Commit Task 6**

```bash
git add test-orchestration-skills/tests/test_cli_smoke.py test-orchestration-skills/docs/to_do/core-runtime-acceptance.md test-orchestration-skills/USER-GUIDE.md
git commit -m "test: accept portable testing runtime"
```
