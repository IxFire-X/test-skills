# Portable Skills and Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish six concise host-neutral testing skills at stable ASCII paths, backed by the canonical runtime and an optional Codex adapter.

**Architecture:** Skill identity and contracts are stable machine interfaces under `skills/<skill-id>/`. Human documentation and adapters reference those interfaces; they never redefine them. Existing Russian directories are migrated with `git mv`, then all contract paths and links are updated atomically.

**Tech Stack:** Markdown SKILL.md packages, JSON pipeline contract, Python contract/skill validators, Java/JUnit 5 and Python/pytest templates, optional Codex adapter metadata.

## Global Constraints

- Plan 1 must be accepted before this plan starts.
- `SKILL.md` frontmatter contains exactly `name` and `description`.
- Persistent artifacts go only to `docs/to_do/`.
- Skills never claim execution that the runtime cannot perform.
- `tc-to-autotest` rejects direct `<generated_test_cases>` input.
- Reviewers cannot issue final acceptance without deterministic evidence required by their stage.
- No Codex plugin is required for core use.

---

### Task 1: Canonical ASCII skill layout

**Files:**
- Move: `Разметка контекста/` → `skills/context-marker/`
- Move: `Ручные тест-кейсы/` → `skills/tc-generator/`
- Move: `Валидация тест-кейсов/` → `skills/tc-reviewer/`
- Move: `Автоматизированные кейсы на основе тест-кейсов/` → `skills/tc-to-autotest/`
- Move: `Валидация автотестов/` → `skills/autotest-reviewer/`
- Move: `Оркестратор/` → `skills/orchestrate/`
- Modify: `contracts/pipeline.json`
- Modify: every relative Markdown link referencing moved files
- Create: `tests/test_skill_layout.py`

**Interfaces:**
- Produces canonical paths `skills/<skill-id>/SKILL.md`.
- Contract `steps[*].skill_file` points only to canonical paths.

- [ ] **Step 1: Write a failing layout test**

```python
EXPECTED = {
    "context-marker", "tc-generator", "tc-reviewer",
    "tc-to-autotest", "autotest-reviewer", "orchestrate",
}

def test_contract_uses_ascii_canonical_skill_paths(root, contract):
    paths = {step["skill_file"] for step in contract["steps"] if "skill_file" in step}
    assert paths == {f"skills/{name}/SKILL.md" for name in EXPECTED}
    assert all(path.isascii() for path in paths)
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_skill_layout.py -q`

Expected: FAIL because the contract still uses localized directories.

- [ ] **Step 3: Move directories with Git history**

```bash
git mv "test-orchestration-skills/Разметка контекста" test-orchestration-skills/skills/context-marker
git mv "test-orchestration-skills/Ручные тест-кейсы" test-orchestration-skills/skills/tc-generator
git mv "test-orchestration-skills/Валидация тест-кейсов" test-orchestration-skills/skills/tc-reviewer
git mv "test-orchestration-skills/Автоматизированные кейсы на основе тест-кейсов" test-orchestration-skills/skills/tc-to-autotest
git mv "test-orchestration-skills/Валидация автотестов" test-orchestration-skills/skills/autotest-reviewer
git mv "test-orchestration-skills/Оркестратор" test-orchestration-skills/skills/orchestrate
```

Do not leave duplicated full implementations at the old paths. Git history provides migration visibility.

- [ ] **Step 4: Update contract paths and links**

Run the contract renderer from Plan 1 after editing `pipeline.json`; use a link checker to find every old localized path.

- [ ] **Step 5: Verify layout and contract**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_skill_layout.py tests/test_contract_check.py tests/test_contract_docs.py -q`

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/contract_check.py --root . --full`

- [ ] **Step 6: Commit Task 1**

```bash
git add -A test-orchestration-skills/skills test-orchestration-skills/contracts test-orchestration-skills/CONTRACTS.md test-orchestration-skills/PIPELINE.md test-orchestration-skills/tests/test_skill_layout.py
git commit -m "refactor: move skills to portable paths"
```

### Task 2: Context and manual-test skills

**Files:**
- Modify: `skills/context-marker/SKILL.md`
- Modify: `skills/context-marker/README.md`
- Modify: `skills/context-marker/examples.md`
- Modify: `skills/tc-generator/SKILL.md`
- Modify: `skills/tc-generator/README.md`
- Modify: `skills/tc-generator/examples.md`
- Modify: `skills/tc-reviewer/SKILL.md`
- Modify: `skills/tc-reviewer/README.md`
- Modify: `skills/tc-reviewer/examples.md`
- Create: `tests/test_skill_frontmatter.py`

**Interfaces:**
- Consumes and produces artifact names exactly from `pipeline.json`.
- `tc-generator` produces requirement IDs, TC IDs, coverage, gaps, and assumptions.
- `tc-reviewer` produces one central review verdict and explicit branch artifacts.

- [ ] **Step 1: Add failing structural and content tests**

```python
import pytest
import yaml

@pytest.mark.parametrize("skill_id", ["context-marker", "tc-generator", "tc-reviewer"])
def test_frontmatter_has_only_name_and_description(root, skill_id):
    text = (root / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert set(metadata) == {"name", "description"}

def test_generator_requires_sdd_coverage(root):
    generator_text = (root / "skills/tc-generator/SKILL.md").read_text(encoding="utf-8")
    for token in ("requirement_id", "tc_id", "coverage", "INFERRED"):
        assert token in generator_text
```

- [ ] **Step 2: Run tests and official validator**

Run tests before editing. Also run `quick_validate.py` with `PYTHONUTF8=1`; record current failures rather than assuming the interrupted edits are correct.

- [ ] **Step 3: Make each SKILL.md one operational workflow**

Use this frontmatter shape:

```yaml
---
name: tc-generator
description: Generate requirement-traceable manual test cases from supplied analytics and project context; use before automation when the user asks for test design, API coverage, or SDD test cases.
---
```

Keep inputs, procedure, stop conditions, output artifacts, and validation command. Move explanatory prose to README/examples.

- [ ] **Step 4: Correct reviewer examples**

Include one `ПРИНЯТО` case with complete evidence and one `ТРЕБУЕТ ДОРАБОТКИ` case with an unambiguous blocking defect. Never label a defective example accepted.

- [ ] **Step 5: Validate all three skill packages**

Run quick validation on each directory, contract check, Markdown-link check, and `tests/test_skill_frontmatter.py`.

- [ ] **Step 6: Commit Task 2**

```bash
git add test-orchestration-skills/skills/context-marker test-orchestration-skills/skills/tc-generator test-orchestration-skills/skills/tc-reviewer test-orchestration-skills/tests/test_skill_frontmatter.py
git commit -m "feat: finalize manual testing skills"
```

### Task 3: Autotest generation and review skills

**Files:**
- Modify: `skills/tc-to-autotest/SKILL.md`
- Modify: `skills/tc-to-autotest/README.md`
- Modify: `skills/tc-to-autotest/examples.md`
- Modify: `skills/autotest-reviewer/SKILL.md`
- Modify: `skills/autotest-reviewer/README.md`
- Modify: `skills/autotest-reviewer/examples.md`
- Create: `tests/test_skill_capabilities.py`

**Interfaces:**
- `tc-to-autotest` requires `<test_cases>` or `<corrected_test_cases>` and rejects `<generated_test_cases>`.
- Produces `<generated_files>`, `<automation_matrix>`, `<trace_map>`, and `<automation_bundle_json>`.
- Reviewer supports Java/JUnit 5 and Python/pytest and gates execution.

- [ ] **Step 1: Add failing contract/capability tests**

```python
def test_autotest_generator_rejects_unreviewed_cases(root):
    generator_text = (root / "skills/tc-to-autotest/SKILL.md").read_text(encoding="utf-8")
    assert "<generated_test_cases>" in generator_text
    assert "reject" in generator_text.lower() or "запрещ" in generator_text.lower()

def test_reviewer_names_both_baseline_frameworks(root):
    reviewer_text = (root / "skills/autotest-reviewer/SKILL.md").read_text(encoding="utf-8")
    assert "JUnit 5" in reviewer_text
    assert "pytest" in reviewer_text
```

- [ ] **Step 2: Verify RED against the interrupted edits**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_skill_capabilities.py -q`

- [ ] **Step 3: Finalize project-native generation rules**

Require the generator to inspect current test layout, fixtures, base classes, naming, build files, and dependencies before writing code. Block placeholder assertions such as:

```text
assert True
assert response is not None
// placeholder assertion comment
```

Unresolved semantic facts become blocking gaps; they are not silently invented.

- [ ] **Step 4: Finalize language-aware review rules**

Java checks include JUnit lifecycle, AssertJ/MockMvc/RestAssured project conventions, static state, and wrapper execution. Python checks include fixture scopes, Django database markers, monkeypatch/mock boundaries, parametrization, async handling, and project-venv execution.

- [ ] **Step 5: Validate skills and examples**

Run quick validation, contract check, link check, and capability tests. Inspect that all example verdicts match their findings.

- [ ] **Step 6: Commit Task 3**

```bash
git add test-orchestration-skills/skills/tc-to-autotest test-orchestration-skills/skills/autotest-reviewer test-orchestration-skills/tests/test_skill_capabilities.py
git commit -m "feat: finalize Java and Python automation skills"
```

### Task 4: Verified Java and Python templates

**Files:**
- Modify: `skills/tc-to-autotest/templates/java-junit5.md`
- Modify: `skills/tc-to-autotest/templates/python-pytest.md`
- Modify: `skills/tc-to-autotest/templates/typescript-jest.md`
- Modify: `skills/tc-to-autotest/templates/go-testing.md`
- Create: `tests/test_templates.py`

**Interfaces:**
- Java/Python templates are supported examples, not universal code generators.
- TypeScript/Go templates carry an explicit experimental header and cannot produce acceptance.

- [ ] **Step 1: Add failing template-policy tests**

```python
def load_templates(root):
    base = root / "skills/tc-to-autotest/templates"
    return {name: (base / filename).read_text(encoding="utf-8") for name, filename in {
        "java": "java-junit5.md", "python": "python-pytest.md",
        "typescript": "typescript-jest.md", "go": "go-testing.md",
    }.items()}

def test_supported_templates_have_real_assertions(root):
    template_texts = load_templates(root)
    assert "assert True" not in template_texts["python"]
    assert "placeholder assertion" not in template_texts["java"].lower()

def test_experimental_templates_are_honest(root):
    template_texts = load_templates(root)
    assert "EXPERIMENTAL" in template_texts["typescript"]
    assert "NOT_RUNNABLE" in template_texts["go"]
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_templates.py -q`

- [ ] **Step 3: Add minimal compilable baseline examples**

Java template uses project package names, JUnit 5, existing client conventions, deterministic fixtures, and a meaningful response assertion. Python template uses pytest fixtures and exact domain assertions. Both include `requirement_id` and `tc_id` in display names or metadata.

- [ ] **Step 4: Mark unsupported templates explicitly**

Do not remove TypeScript/Go reference material, but add a non-optional header saying generation/review is experimental and Execution Gate is unavailable.

- [ ] **Step 5: Run tests and commit**

```bash
git add test-orchestration-skills/skills/tc-to-autotest/templates test-orchestration-skills/tests/test_templates.py
git commit -m "docs: verify automation templates"
```

### Task 5: Thin orchestrator and optional Codex adapter

**Files:**
- Modify: `skills/orchestrate/SKILL.md`
- Modify: `skills/orchestrate/README.md`
- Modify: `skills/orchestrate/examples.md`
- Remove or reduce: `skills/orchestrate/SKILL-LITE.md`
- Create: `adapters/README.md`
- Create: `adapters/codex/README.md`
- Create: `adapters/codex/install.ps1`
- Create: `tests/test_orchestrator_projection.py`

**Interfaces:**
- Orchestrator invokes real tools from Plan 1 and branches only on central verdicts.
- Codex adapter copies or links canonical skill packages without changing them.

- [ ] **Step 1: Add a failing orchestrator projection test**

```python
def orchestrator_text(root):
    return (root / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8")

def test_orchestrator_names_every_gate(root):
    text = orchestrator_text(root)
    for command in ("contract_check.py", "scan_project.py", "validate_artifact.py", "trace_check.py", "run_tests.py"):
        assert command in text

def test_orchestrator_never_accepts_not_runnable(root):
    text = orchestrator_text(root)
    assert "NOT_RUNNABLE" in text
    assert "не считается" in text.lower() or "not accepted" in text.lower()
```

- [ ] **Step 2: Verify RED**

Run: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe -m pytest tests/test_orchestrator_projection.py -q`

- [ ] **Step 3: Reduce orchestrator to a control plane**

Its algorithm is exactly:

```text
contract check → scan/context → generate TC → review TC → generate code → review code → execute → trace check → report
```

Bound semantic retry loops, stop for user confirmation on `ТРЕБУЕТ ДОРАБОТКИ`, and persist a run manifest under `docs/to_do`.

- [ ] **Step 4: Implement an idempotent Codex adapter installer**

`install.ps1` accepts `-SkillPackRoot` and `-Destination`, copies only `skills/*`, and supports `-WhatIf`. It must not be required by the generic instructions.

- [ ] **Step 5: Verify generic use without adapter**

Run contract/skill validation from the repository root without installing the adapter. Then run adapter `-WhatIf` and assert it lists exactly six skill directories.

- [ ] **Step 6: Commit Task 5**

```bash
git add test-orchestration-skills/skills/orchestrate test-orchestration-skills/adapters test-orchestration-skills/tests/test_orchestrator_projection.py
git commit -m "feat: add portable orchestration and Codex adapter"
```

### Task 6: Skill-pack documentation and acceptance

**Files:**
- Modify: `Instruction.md`
- Modify: `USER-GUIDE.md`
- Modify: `ROADMAP.md`
- Modify: `schemas/README.md`
- Create: `docs/to_do/skills-acceptance.md`
- Create: `tests/test_markdown_links.py`

**Interfaces:**
- Documentation points to generated contract projections and canonical skill paths.
- Produces an acceptance report for the skills/adapters subsystem.

- [ ] **Step 1: Add a failing local-link test**

```python
import re
from urllib.parse import unquote

LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")

def find_broken_local_links(root):
    failures = []
    for document in root.rglob("*.md"):
        for target in LINK_RE.findall(document.read_text(encoding="utf-8")):
            clean = target.split("#", 1)[0]
            if not clean or "://" in clean or clean.startswith("mailto:"):
                continue
            if not (document.parent / unquote(clean)).resolve().exists():
                failures.append(f"{document.relative_to(root)} -> {target}")
    return sorted(failures)

def test_all_local_markdown_links_resolve(root):
    failures = find_broken_local_links(root)
    assert failures == []
```

- [ ] **Step 2: Remove alternate authorities and stale support claims**

Documentation may describe behavior but must link to `contracts/pipeline.json` for normative details. Remove statements that TypeScript/Go execution works and remove stale localized canonical paths.

- [ ] **Step 3: Run complete skill validation**

Run quick validation for all six skills, contract check, renderer `--check`, schema tests, skill tests, template tests, orchestrator tests, and link tests.

- [ ] **Step 4: Inspect every SKILL.md manually**

Record name, trigger description, inputs, outputs, stop conditions, deterministic command, and artifact path. Any missing item is a failure.

- [ ] **Step 5: Write acceptance evidence and commit**

```bash
git add test-orchestration-skills/Instruction.md test-orchestration-skills/USER-GUIDE.md test-orchestration-skills/ROADMAP.md test-orchestration-skills/schemas/README.md test-orchestration-skills/docs/to_do/skills-acceptance.md test-orchestration-skills/tests/test_markdown_links.py
git commit -m "docs: accept portable skill packages"
```
