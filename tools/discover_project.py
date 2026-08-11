#!/usr/bin/env python3
"""Read-only, deterministic inventory of runnable project modules."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from .stack_catalog import (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, IGNORED_DIR_NAMES,
        JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS,
        MANIFEST_LANGUAGES, PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, manifest_language)
else:
    from stack_catalog import (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, IGNORED_DIR_NAMES,
        JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS,
        MANIFEST_LANGUAGES, PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, manifest_language)

MARKERS = {
    "python": (PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, "pip"),
    "java": (JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, "maven"),
    "typescript": (JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS, "npm"),
    "go": (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, "go-mod"),
}


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _confined(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def find_confined_manifests(root: Path) -> list[Path]:
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIR_NAMES and not (current_path / name).is_symlink())
        for name in sorted(files):
            candidate = current_path / name
            if name in MANIFEST_LANGUAGES and not candidate.is_symlink() and _confined(root, candidate):
                found.append(candidate)
    return sorted(found, key=lambda item: _relative(root, item))


def _workspace_children(root: Path, manifests: Sequence[Path]) -> set[Path]:
    children: set[Path] = set()
    for manifest in manifests:
        text = _read(manifest)
        parent = manifest.parent
        if manifest.name == "pom.xml":
            refs = re.findall(r"<module>\s*([^<]+?)\s*</module>", text)
        elif manifest.name.startswith("build.gradle"):
            refs = [value.replace(":", "/").lstrip("/") for value in re.findall(r"include\s*[('\"]+\s*([^'\")]+)", text)]
        elif manifest.name == "package.json":
            try:
                raw = json.loads(text)
                workspace = raw.get("workspaces", [])
                refs = workspace.get("packages", []) if isinstance(workspace, dict) else workspace
            except json.JSONDecodeError:
                refs = []
        elif manifest.name == "go.work":
            refs = re.findall(r"(?:use\s*\(?|\n\s*)(\./[^\s)]+)", text)
        else:
            refs = []
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, str) or any(part == ".." for part in Path(ref).parts):
                continue
            if any(char in ref for char in "*?["):
                for candidate in sorted(parent.glob(ref)):
                    if candidate.is_dir() and _confined(root, candidate): children.add(candidate)
            else:
                candidate = (parent / ref).resolve()
                if candidate.is_dir() and _confined(root, candidate): children.add(candidate)
    return children


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_code_tree(directory: Path) -> bool:
    return any((directory / name).is_dir() for name in ("src", "test", "tests", "spec", "resources"))


def _is_aggregator(directory: Path, manifests: Sequence[Path], children: set[Path]) -> bool:
    if directory in children or _has_code_tree(directory):
        return False
    text = "\n".join(_read(item) for item in manifests if item.parent == directory)
    return bool(re.search(r"<modules>|\bworkspaces\b|\binclude\s*[('\"]|\buse\s*\(", text))


def _id_for(root: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", root.lower()).strip("-") or "root"


def _module_id(relative_root: str, manifests: Sequence[Path]) -> str:
    """Use the root package name when available; otherwise derive from its path."""
    if relative_root == ".":
        package = next((item for item in manifests if item.name == "package.json"), None)
        if package:
            try:
                name = json.loads(_read(package)).get("name")
            except json.JSONDecodeError:
                name = None
            if isinstance(name, str) and name:
                return _id_for(name)
    return _id_for(relative_root)


def _marker_values(text: str, markers: list[tuple[str, str]]) -> list[str]:
    return sorted({value for needle, value in markers if needle in text.lower()})


def _paths(root: Path, module_root: Path) -> dict[str, list[str]]:
    choices = {"source": ("src", "src/main/java", "src/main/kotlin"), "tests": ("tests", "test", "src/test/java", "src/test/kotlin"), "resources": ("resources", "src/main/resources", "src/test/resources")}
    result: dict[str, list[str]] = {}
    for key, names in choices.items():
        existing = sorted(_relative(root, module_root / name) for name in names if (module_root / name).is_dir())
        if existing: result[key] = existing
    return result


def analyze_module_roots(root: Path, manifests: Sequence[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    by_root: dict[Path, list[Path]] = {}
    for manifest in manifests: by_root.setdefault(manifest.parent, []).append(manifest)
    children = _workspace_children(root, manifests)
    modules: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for module_root in sorted(by_root, key=lambda item: _relative(root, item)):
        owned = by_root[module_root]
        if _is_aggregator(module_root, owned, children): continue
        rel_root = _relative(root, module_root)
        module_id = _module_id(rel_root, owned)
        languages = sorted({manifest_language(item.name) for item in owned if manifest_language(item.name)})
        if len(languages) != 1:
            options = [{"id": value, "value": value, "evidence": sorted(_relative(root, item) + ":manifest" for item in owned if manifest_language(item.name) == value)} for value in languages]
            questions.append({"id": f"module:{module_id}:stack.language", "field": f"modules.{module_id}.stack.language", "impact": "Определяет шаблон генерации и средство запуска тестов", "options": options})
            continue
        language = languages[0]
        text = "\n".join(_read(item) for item in owned)
        framework_markers, test_markers, build_tool = MARKERS[language]
        frameworks = _marker_values(text, framework_markers)
        tests = _marker_values(text, test_markers) or (["go-testing"] if language == "go" else [])
        stack: dict[str, Any] = {"language": language, "build_tool": "gradle" if language == "java" and any(item.name.startswith("build.gradle") for item in owned) else build_tool}
        if len(frameworks) == 1: stack["framework"] = frameworks[0]
        if len(frameworks) > 1:
            questions.append({"id": f"module:{module_id}:stack.framework", "field": f"modules.{module_id}.stack.framework", "impact": "Определяет шаблон генерации и средство запуска тестов", "options": [{"id": value, "value": value, "evidence": sorted(_relative(root, item) + ":dependency-marker" for item in owned)} for value in frameworks]})
        module: dict[str, Any] = {"id": module_id, "root": rel_root, "stack": stack, "detected_from": sorted(_relative(root, item) for item in owned)}
        paths = _paths(root, module_root)
        if paths: module["paths"] = paths
        wrapper = {key: name for key, name in (("windows", "mvnw.cmd"), ("linux", "mvnw"), ("windows", "gradlew.bat"), ("linux", "gradlew")) if (module_root / name).is_file()}
        test: dict[str, Any] = {}
        if len(tests) == 1: test["framework"] = tests[0]
        if wrapper: test["wrapper"] = wrapper
        if test: module["test"] = test
        if len(tests) > 1:
            questions.append({"id": f"module:{module_id}:test.framework", "field": f"modules.{module_id}.test.framework", "impact": "Определяет шаблон генерации и средство запуска тестов", "options": [{"id": value, "value": value, "evidence": sorted(_relative(root, item) + f":devDependencies.{value}" for item in owned)} for value in tests]})
        modules.append(module)
    return modules, questions, []


def project_fingerprint(project_dir: Path, evidence_paths: Sequence[str]) -> str:
    root = project_dir.resolve()
    digest = hashlib.sha256()
    for relative in sorted(set(evidence_paths)):
        candidate = root / relative
        if not _confined(root, candidate) or not candidate.is_file() or candidate.is_symlink(): continue
        digest.update(relative.encode("utf-8")); digest.update(b"\0"); digest.update(candidate.read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


def build_report(status: str, project_name: str, modules: list[dict[str, Any]], questions: list[dict[str, Any]], warnings: list[str], errors: list[str]) -> dict[str, Any]:
    return {"status": status, "project_name": project_name, "modules": modules, "questions": questions, "warnings": warnings, "errors": errors, "fingerprint": hashlib.sha256(b"").hexdigest(), "scanned_at": datetime.now(timezone.utc).isoformat()}


def discover_project(project_dir: Path) -> dict[str, Any]:
    root = project_dir.resolve()
    manifests = find_confined_manifests(root)
    if not manifests:
        return build_report("error", root.name, [], [], [], ["build manifests were not found"])
    modules, questions, warnings = analyze_module_roots(root, manifests)
    if not modules:
        return build_report("error", root.name, [], [], warnings, ["runnable modules were not found"])
    evidence = sorted({path for module in modules for path in module["detected_from"]})
    return {"status": "needs_input" if questions else "ready", "project_name": root.name, "modules": sorted(modules, key=lambda item: (item["root"], item["id"])), "questions": sorted(questions, key=lambda item: item["id"]), "warnings": sorted(warnings), "errors": [], "fingerprint": project_fingerprint(root, evidence), "scanned_at": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only project module discovery; never updates .skillsrc.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.project).resolve()
    report = discover_project(root) if root.is_dir() else build_report("error", root.name, [], [], [], ["--project does not exist"])
    if args.output:
        output = Path(args.output).resolve()
        allowed = root / "docs" / "to_do"
        try: output.relative_to(allowed)
        except ValueError: parser.error("--output must be below exact docs/to_do")
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"ready", "needs_input"} else 1


if __name__ == "__main__": raise SystemExit(main())
