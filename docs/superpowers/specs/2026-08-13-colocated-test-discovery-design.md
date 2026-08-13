# Co-located Technical-Test Discovery Design

## Decision

Automatic `.skillsrc` discovery must declare statically supported technical-test files that live below a selected module's source roots, even when the project has no conventional `tests/` directory. This is a narrow correction to discovery evidence, not a new test framework or classification rule.

The user delegated this integration decision to the implementation owner on 2026-08-13. The selected design fixes the evidence source rather than hand-editing one InvenTree manifest or making the inventory guess outside `.skillsrc`.

## Evidence and root cause

Three previously generated InvenTree `.skillsrc` files contain no backend `paths.tests`. The repository has 63 committed Python files matching the already locked Phase-1 support patterns `test_*.py | *_test.py` below `src/`. Therefore `build_source_inventories` would correctly consume an empty declared-root list and return an empty backend inventory.

The root cause is `tools/discover_project.py::_paths`: it recognizes only conventional directory names such as `tests`, `test`, and `src/test/java`. The downstream inventory intentionally scans only declared `paths.tests`; changing that invariant would make discovery policy leak into every consumer.

InvenTree also contains `tests.py` files and configures pytest with `python_files = ["test*.py"]`. Those files remain out of scope because Phase 1 deliberately supports only `test_*.py | *_test.py`. This change must not silently widen the locator contract.

## Considered approaches

1. **Selected: enrich automatic discovery with exact supported file paths.** The generated manifest remains the single source of truth; later LLMs and tools need no project-specific instructions.
2. **Rejected: hand-edit the InvenTree `.skillsrc`.** It would make this calibration pass while future projects and fresh LLM runs reproduce the defect.
3. **Rejected: let `test_classification` scan `paths.source` when `paths.tests` is empty.** It would violate the closed inventory interface, turn a valid empty declaration into an implicit guess, and duplicate discovery policy.

## Module and interface design

`tools/stack_catalog.py` owns one shared deterministic predicate:

```python
is_supported_static_test_file(path: Path, language: str) -> bool
```

It returns true only for the already locked Python and Java filename sets. Both `discover_project` and `test_classification` call this interface so filename support cannot drift.

`discover_project._paths` continues to declare conventional test directories. In addition, for Python or Java modules it walks only already detected source directories, without following symlinks, prunes the existing ignored-directory set, and appends exact supported co-located files. All path values are unique, sorted, and relative to the owning module root. Correcting conventional paths to the same module-relative convention is required because consumers resolve every value as `module_root / declared_path`.

An exact file is a valid `paths.tests` entry under the existing schema and inventory implementation. `_iter_files` already accepts files as roots. Declaring one test file excludes only that file from authorized product sources; sibling production files remain eligible.

The discovery fingerprint includes the ordered discovered path names as structural evidence. Adding or removing a co-located test between preview and atomic `.skillsrc` write therefore triggers the existing `project_changed` guard without hashing or exposing test contents.

## Safety and stop behavior

- Do not import or execute project code and start no project process.
- Do not read test contents during discovery.
- Do not follow symlinked files or directories, cross the project/module root, or include ignored directories.
- An absent conventional test directory and no supported co-located files remains a valid empty `paths.tests` omission.
- Do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, or `tools/scan_project.py`.
- Do not infer unit/integration/e2e scope from a filename.

## Acceptance

1. A synthetic root module with `src/service.py` and `src/pkg/test_api.py` discovers `paths.source == ["src"]` and `paths.tests == ["src/pkg/test_api.py"]`.
2. A nested module emits module-relative conventional and exact-file paths; no project-root prefix is duplicated.
3. Compiling the discovery report into `.skillsrc` and passing it to `build_source_inventories` finds the exact test symbol while retaining `src/service.py` as an authorized product source.
4. Unsupported `tests.py`, symlink escapes, ignored directories, and non-test product files are absent from `paths.tests`.
5. Adding or removing a supported co-located path changes the discovery fingerprint.
6. Existing discovery, init-skillsrc, source-inventory, bootstrap, doctor, and Pipeline 4.0 tests remain green.

