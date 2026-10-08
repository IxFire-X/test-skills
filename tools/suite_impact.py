"""Impact analysis of a living suite (wave 3, D) — without a model.

Input: the current requirement scan, the suite manifest, optionally JUnit XML reports of the
project's ordinary CI and a git range.  Output:

* requirements by key — ``added``, ``changed`` (text digest; for a Markdown section without an ID
  also its own heading, which carries the method and path), ``removed``, ``renamed`` (same text
  under a new parent heading or ID, or an OpenSpec ``RENAMED`` pair);
* affected cases — ``to_update`` (a linked requirement changed or one of several was removed),
  ``to_retire`` (every linked requirement removed), ``relinked`` (only renamed), plus
  ``requirements_without_cases`` for added requirements;
* tests of the reports that failed on an assertion (``failed``) or broke (``broken``), by case;
* endpoints in the code that are not in the manifest's snapshot and that no requirement names —
  each one a question for the analysts; test cases are not changed for them;
* ``code_under_test_changed`` from the git range (product sources changed), for the strength check.

``needs_model`` tells a CI job whether a model run (``suite-update-v1``) is needed at all.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.suite_manifest import FORMAT_VERSION, SuiteError, read_suite, suite_directory, validate, verify


def own_heading_changed(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """A Markdown section without an explicit ID whose own heading changed (review 2.1 item 5).

    Such a section is keyed by its heading chain, and its heading carries the contract
    (``### 3.1. GET `/student```: method, path, number), so editing the heading is editing the
    requirement, not renaming it.  A renamed parent section changes the key, not the own heading:
    that stays a rename (relinked cases).
    """
    return (str(new["key"]).startswith("md:") and not new.get("explicit_id") and not old.get("explicit_id")
            and " ".join(str(old.get("title") or "").split()) != " ".join(str(new.get("title") or "").split()))


def compare_requirements(old: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from tools.requirement_identity import rename_pairs

    old_by = {row["key"]: row for row in old}
    new_by = {row["key"]: row for row in new}
    renamed = rename_pairs(old, new)
    renamed_old = {pair[0] for pair in renamed}
    renamed_new = {pair[1] for pair in renamed}
    changed = sorted(key for key in old_by.keys() & new_by.keys() if old_by[key]["text_digest"] != new_by[key]["text_digest"])
    changed += sorted(new_key for old_key, new_key in renamed if old_by[old_key]["text_digest"] != new_by[new_key]["text_digest"]
                      or own_heading_changed(old_by[old_key], new_by[new_key]))
    return {
        "added": sorted(key for key in new_by.keys() - old_by.keys() if key not in renamed_new),
        "changed": sorted(set(changed)),
        "removed": sorted(key for key in old_by.keys() - new_by.keys() if key not in renamed_old),
        "renamed": [{"from": old_key, "to": new_key} for old_key, new_key in renamed],
    }


def affected_cases(manifest: Mapping[str, Any], requirements: Mapping[str, Any]) -> dict[str, list[str]]:
    renamed_from = {row["from"]: row["to"] for row in requirements["renamed"]}
    changed_old = set(requirements["changed"]) | {old for old, new in renamed_from.items() if new in requirements["changed"]}
    removed = set(requirements["removed"])
    result: dict[str, list[str]] = {"to_update": [], "to_retire": [], "relinked": []}
    for case in manifest["cases"]:
        if case["status"] == "RETIRED":
            continue
        keys = set(case["requirement_keys"])
        if not keys:
            continue
        if keys <= removed:
            result["to_retire"].append(case["case_id"])
        elif keys & (changed_old | removed):
            result["to_update"].append(case["case_id"])
        elif keys & set(renamed_from):
            result["relinked"].append(case["case_id"])
    return {key: sorted(value) for key, value in result.items()}


def junit_outcomes(paths: Iterable[Path]) -> list[dict[str, str]]:
    """``classname``, ``name`` and ``status`` (``passed``/``failed``/``broken``/``skipped``) of every testcase."""
    rows = []
    for path in paths:
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError) as error:
            raise SuiteError("IMPACT_REPORT_INVALID", f"{path}: {error}") from error
        for case in root.iter("testcase"):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if case.find("failure") is not None:
                status = "failed"
            elif case.find("error") is not None:
                status = "broken"
            elif case.find("skipped") is not None:
                status = "skipped"
            else:
                status = "passed"
            message = None if problem is None else problem.get("message") or next(iter((problem.text or "").strip().splitlines()), None)
            rows.append({"classname": case.get("classname") or "", "name": (case.get("name") or "").split("(", 1)[0].split("[", 1)[0], "status": status,
                         "message": message, "trace": None if problem is None else (problem.text or "")[:20000]})
    return rows


def _method_of(row: Mapping[str, str], manifest: Mapping[str, Any]) -> list[str]:
    """Case IDs whose method a JUnit testcase is (Java ``class#method``; pytest ``module path`` + function)."""
    found = []
    for case in manifest["cases"]:
        for method in case["methods"]:
            locator = method["locator"]
            if "#" in locator:
                owner, name = locator.rsplit("#", 1)
                hit = row["classname"] in {owner, owner.rsplit(".", 1)[-1]} and row["name"] == name
            else:
                module = method["file"].removesuffix(".py").replace("/", ".")
                name = locator.rsplit(".", 1)[-1]
                owner = locator.rsplit(".", 1)[0] if "." in locator else ""
                hit = row["name"] == name and (row["classname"].endswith(module.rsplit(".", 1)[-1]) or row["classname"].endswith(owner or "\0"))
            if hit:
                found.append(case["case_id"])
    return sorted(set(found))


def test_outcomes(manifest: Mapping[str, Any], reports: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"failed": [], "broken": [], "skipped": []}
    for row in junit_outcomes(reports):
        if row["status"] == "passed":
            continue
        cases = _method_of(row, manifest)
        if cases:
            out[row["status"]].append({"test": f"{row['classname']}#{row['name']}", "case_ids": cases})
    return {key: sorted(value, key=lambda item: item["test"]) for key, value in out.items()}


def changed_files(project: Path, git_range: str) -> list[str]:
    try:
        completed = subprocess.run(["git", "-c", f"safe.directory={Path(project).resolve().as_posix()}", "-C", str(project), "diff", "--name-only", git_range],
                                   capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise SuiteError("IMPACT_GIT_RANGE", f"git diff {git_range}: {error}") from error
    if completed.returncode != 0:
        raise SuiteError("IMPACT_GIT_RANGE", f"git diff {git_range}: {completed.stderr.strip()[:300]}")
    return sorted(line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip())


def impact(project: Path, *, docs: Sequence[str] | None = None, junit: Sequence[str] = (), git_range: str | None = None) -> dict[str, Any]:
    from tools.code_surface import changed_parameters, new_endpoints, surface
    from tools.pipeline_driver_suite import _module, _skillsrc
    from tools.requirement_identity import identify_project
    from tools.run_pipeline import _docs_entries

    project = Path(project)
    skillsrc = _skillsrc(project)
    suite_dir = suite_directory(project, skillsrc)
    manifest = read_suite(project, suite_dir)
    if manifest is None:
        raise SuiteError("SUITE_MISSING", f"{suite_dir} holds no suite manifest (a format-0 bundle needs `migrate` first)")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise SuiteError("MIGRATION_NEEDED", f"suite format {manifest.get('format_version')}: run `python -m tools.suite migrate` first")
    problems = validate(manifest)
    if problems:
        raise SuiteError("SUITE_MANIFEST_INVALID", "; ".join(problems[:5]))
    id_pattern = (skillsrc.get("requirements") or {}).get("id_pattern")
    paths = list(docs) if docs else [row["path"] for row in manifest["documents"]]
    entries = _docs_entries(project, paths)
    current = identify_project(project, entries, id_pattern)
    requirements = compare_requirements(manifest["requirements"], current)
    cases = affected_cases(manifest, requirements)
    tests = test_outcomes(manifest, [Path(path) if Path(path).is_absolute() else project / path for path in junit]) if junit else {"failed": [], "broken": [], "skipped": []}
    module = _module(skillsrc, manifest["module_id"])
    sources = list(((module.get("paths") or {}).get("source")) or ["src/main/java", "src", "app"])
    now = surface(project / str(module.get("root") or "."), sources)
    endpoints = new_endpoints(now, manifest["code_surface"], [row["text"] for row in current])
    questions = [{"requirement_ids": ["(code)"], "question": f"В коде есть {row['method']} {row['path']} ({row['handler']}), но ни одно требование его не описывает. "
                  f"Это новое поведение, которое нужно описать, или служебный эндпоинт вне тестов?",
                  "source": {"kind": "code-surface", "ref": row["signature"], "location": row["source"]}} for row in endpoints]
    widened = changed_parameters(now, manifest["code_surface"])
    questions += [{"requirement_ids": ["(code)"], "question": f"У {row['method']} {row['path']} ({row['handler']}) появились параметры "
                   f"{', '.join(row['added_parameters'])}. Требования их описывают или это новое поведение, которое нужно описать?",
                   "source": {"kind": "code-surface", "ref": row["signature"] + " " + ",".join(row["added_parameters"]), "location": row["source"]}}
                  for row in widened]
    changed = changed_files(project, git_range) if git_range else None
    module_root = str(module.get("root") or ".").strip("./")
    product = [f"{module_root + '/' if module_root else ''}{path.strip('/')}/" for path in sources]
    tests_dirs = [f"{module_root + '/' if module_root else ''}{path.strip('/')}/" for path in ((module.get("paths") or {}).get("tests") or [])]
    code_changed = None if changed is None else any(any(path.startswith(prefix) for prefix in product) and not any(path.startswith(prefix) for prefix in tests_dirs)
                                                       for path in changed)
    edited = verify(project, manifest)
    needs_model = bool(requirements["added"] or requirements["changed"] or requirements["removed"] or tests["failed"] or tests["broken"])
    return {
        "suite_dir": suite_dir, "suite_id": manifest["suite_id"], "id_pattern_changed": id_pattern != manifest["id_pattern"],
        "requirements": requirements, "cases": {**cases, "requirements_without_cases": requirements["added"]},
        "tests": tests, "new_endpoints": endpoints, "new_parameters": widened, "analyst_questions": questions,
        "code_surface_digest": {"manifest": (manifest["code_surface"] or {}).get("digest"), "current": now["digest"]},
        "changed_files": changed, "code_under_test_changed": code_changed, "edited": edited, "needs_model": needs_model,
    }
