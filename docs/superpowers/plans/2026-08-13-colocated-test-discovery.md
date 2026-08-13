# Co-located Technical-Test Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make automatic `.skillsrc` discovery declare already-supported co-located Python/Java technical-test files without project-specific manifest edits.

**Architecture:** A shared filename predicate in `tools/stack_catalog.py` is consumed by discovery and inventory. Discovery emits sorted module-relative exact file roots beside conventional test directories and binds those structural paths into its fingerprint; inventory remains a strict consumer of declared roots.

**Tech Stack:** Python 3.10+ standard library, `pathlib`, `os.walk`, `unittest`, existing `.skillsrc` V3 schema and public inventory facade.

## Global Constraints

- Normative amendment: `docs/superpowers/specs/2026-08-13-colocated-test-discovery-design.md`.
- Support remains exactly Python `test_*.py | *_test.py` and the existing Java filename set; `tests.py` is out of scope.
- No dependency, project import, project process, secret content, filename-based scope promotion, or hidden fallback scan in inventory.
- Paths are exact, sorted, unique, confined, non-symlink, and relative to their owning module root.
- Do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, or `tools/scan_project.py`.
- Preserve the two user-owned untracked paths in `inventree-clean`; this task does not touch InvenTree.

---

### Task 1: Shared Co-located Test Discovery

**Files:**

- Modify: `tools/stack_catalog.py`
- Modify: `tools/discover_project.py`
- Modify: `tools/test_classification.py`
- Modify: `tests/test_discover_project.py`
- Modify: `tests/test_source_inventory.py`
- Modify only if the real dependency gate requires it: `tests/test_init_skillsrc.py`

**Interfaces:**

- Produces: `is_supported_static_test_file(path: Path, language: str) -> bool`.
- Preserves: `discover_project(project_dir: Path) -> dict[str, Any]` and `build_source_inventories(...) -> SourceInventories`.
- Emits: module-relative `module.paths.source/tests/resources`; exact co-located test files may appear in `paths.tests`.

- [ ] **Step 1: Write focused RED discovery tests**

Add tests that create a synthetic Python module with `src/service.py`, `src/pkg/test_api.py`, `src/pkg/tests.py`, an ignored-directory test, and a symlink when supported. Assert exact module-relative paths, exclusion of unsupported/unsafe rows, and fingerprint change after adding one supported path. Add a nested-module row proving no duplicated module prefix.

- [ ] **Step 2: Run RED**

Run:

```powershell
python -m unittest tests.test_discover_project -v
```

Expected: failures because co-located files are absent and nested conventional paths are project-relative.

- [ ] **Step 3: Write the RED public-flow integration test**

Use the real `discover_project`, `compile_skillsrc`, and `build_source_inventories` interfaces against a temporary project. Assert one discovered test file/symbol and that the sibling production file remains in `authorized_behavior_sources`.

- [ ] **Step 4: Run integration RED**

Run:

```powershell
python -m unittest tests.test_source_inventory -v
```

Expected: failure because the generated manifest has no exact co-located `paths.tests` entry.

- [ ] **Step 5: Implement the smallest GREEN**

Move the existing locked filename predicate to `tools/stack_catalog.py`, import it from both consumers, make `_paths` emit module-relative values, add a confined name-only source-root walk, and include ordered discovered path names in the existing fingerprint. Do not parse pytest configuration or add `tests.py`.

- [ ] **Step 6: Run focused GREEN and compatibility**

Run:

```powershell
python -m unittest tests.test_discover_project tests.test_init_skillsrc tests.test_source_inventory tests.test_skillsrc_manifest -v
python -m unittest tests.test_pipeline_v4 tests.test_orchestrator_skillsrc_bootstrap -v
python tools\doctor.py --root .
python tools\contract_check.py --root . --full
python -m py_compile tools\stack_catalog.py tools\discover_project.py tools\test_classification.py tests\test_discover_project.py tests\test_source_inventory.py
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json tools/scan_project.py
```

Expected: all pass; protected-file command has no output.

- [ ] **Step 7: Commit**

```powershell
git add tools/stack_catalog.py tools/discover_project.py tools/test_classification.py tests/test_discover_project.py tests/test_source_inventory.py tests/test_init_skillsrc.py
git commit -m "fix: discover co-located technical tests"
```

Stage `tests/test_init_skillsrc.py` only when it contains an actual required regression.

