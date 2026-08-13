#!/usr/bin/env python3
"""Build static technical-test and authorized-behavior source inventories."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from types import MappingProxyType
from typing import Any, Mapping, Sequence

try:
    if __package__:
        from .skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc, resolve_module_root, select_module
    else:
        from skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc, resolve_module_root, select_module
except ImportError:  # pragma: no cover - package import is covered by tests
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc, resolve_module_root, select_module


_TEXT_SUFFIXES = {".py", ".java", ".kt", ".go", ".ts", ".tsx", ".js", ".jsx", ".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml", ".sql", ".graphql", ".proto", ".feature", ".html", ".css"}
_EXCLUDED_SEGMENTS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".venv", "venv", "node_modules", "dist", "build", "target", "out", "vendor"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt"}


@dataclass(frozen=True)
class SuppliedInput:
    source_id: str
    content: bytes


@dataclass(frozen=True)
class SourceInventories:
    technical_test_inventory: Mapping[str, Any]
    technical_test_inventory_sha256: str
    authorized_behavior_sources: Mapping[str, Any]
    authorized_behavior_sources_sha256: str


class TestClassificationError(ValueError):
    def __init__(self, diagnostics: Sequence[Mapping[str, str]]):
        self._diagnostics = tuple(_freeze(dict(row)) for row in diagnostics)
        super().__init__(self._diagnostics[0]["message"] if self._diagnostics else "test classification error")

    @property
    def diagnostics(self) -> tuple[Mapping[str, str], ...]:
        return self._diagnostics


class _FrozenList(tuple):
    """An immutable JSON array that retains ordinary list equality for callers."""
    def __eq__(self, other: object) -> bool:
        return tuple(self) == tuple(other) if isinstance(other, (list, tuple)) else False


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    return value


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _error(code: str, path: str, message: str) -> TestClassificationError:
    return TestClassificationError(({"path": path, "code": code, "message": message},))


def _portable_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _confined(root: Path, path: Path, pointer: str) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as error:
        raise _error("INVENTORY_SYMLINK_ESCAPE", pointer, "Declared path escapes the project root.") from error
    return resolved


def _declared_roots(module_root: Path, project_root: Path, values: Sequence[str], pointer: str) -> list[tuple[Path, str]]:
    roots: dict[Path, str] = {}
    for index, value in enumerate(values):
        candidate = module_root / value
        try:
            resolved = _confined(project_root, candidate, f"{pointer}/{index}")
            resolved.relative_to(module_root)
        except TestClassificationError:
            raise
        except ValueError as error:
            raise _error("INVENTORY_UNSAFE_PATH", f"{pointer}/{index}", "Declared path escapes the selected module.") from error
        if not resolved.exists():
            raise _error("INVENTORY_MISSING_ROOT", f"{pointer}/{index}", "Declared root does not exist.")
        roots.setdefault(resolved, _portable_path(project_root, resolved))
    return sorted(roots.items(), key=lambda item: item[1])


def _iter_files(root: Path, project_root: Path, pointer: str) -> list[Path]:
    if root.is_file():
        return [root]
    result: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *names]:
            candidate = current_path / name
            if candidate.is_symlink():
                _confined(project_root, candidate, pointer)
        for name in names:
            candidate = current_path / name
            if candidate.is_file() and not candidate.is_symlink():
                result.append(candidate.resolve())
    return sorted(result, key=lambda item: _portable_path(project_root, item))


def _is_supported_test_file(path: Path, language: str) -> bool:
    name = path.name
    if language == "python":
        return path.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py"))
    return path.suffix == ".java" and (name.endswith("Test.java") or name.endswith("Tests.java") or name.endswith("TestCase.java"))


def _python_testcase(node: ast.ClassDef) -> bool:
    return any((base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else "") == "TestCase" for base in node.bases)


def _python_locators(text: str, path: str) -> list[dict[str, str]]:
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as error:
        raise _error("INVENTORY_PARSE_ERROR", path, "Supported Python test file could not be parsed.") from error
    locators: list[dict[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            locators.append({"kind": "python_module_function", "function_name": node.name})
    def visit(classes: Sequence[ast.stmt], parent: tuple[str, ...] = ()) -> None:
        for node in classes:
            if not isinstance(node, ast.ClassDef):
                continue
            qualified = parent + (node.name,)
            if node.name.startswith("Test") or _python_testcase(node):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test"):
                        locators.append({"kind": "python_class_method", "qualified_class_name": ".".join(qualified), "method_name": member.name})
            visit(node.body, qualified)
    visit(tree.body)
    return locators


def _mask_java_noncode(text: str) -> str:
    """Replace comments and quoted literals with spaces while preserving braces/newlines."""
    result = list(text)
    index, state, escaped = 0, "code", False
    while index < len(text):
        char = text[index]
        if state == "code":
            if text.startswith("//", index):
                result[index:index + 2] = "  "; index += 2; state = "line"; continue
            if text.startswith("/*", index):
                result[index:index + 2] = "  "; index += 2; state = "block"; continue
            if char == '"': result[index] = " "; state = "string"; escaped = False
            elif char == "'": result[index] = " "; state = "char"; escaped = False
        elif state == "line":
            if char == "\n": state = "code"
            else: result[index] = " "
        elif state == "block":
            if text.startswith("*/", index): result[index:index + 2] = "  "; index += 1; state = "code"
            elif char != "\n": result[index] = " "
        else:
            if char == "\n": state = "code"
            elif escaped: result[index] = " "; escaped = False
            elif char == "\\": result[index] = " "; escaped = True
            elif (state == "string" and char == '"') or (state == "char" and char == "'"): result[index] = " "; state = "code"
            else: result[index] = " "
        index += 1
    return "".join(result)


def _matching_brace(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{": depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _java_brace_depth(text: str, opening: int, position: int) -> int:
    if position < opening:
        return -1
    depth = 0
    for char in text[opening:position]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth


def _java_locators(text: str, path: str) -> list[dict[str, str]]:
    masked = _mask_java_noncode(text)
    package = re.search(r"^\s*package\s+([\w$]+(?:\.[\w$]+)*)\s*;", masked, re.MULTILINE)
    classes = [match for match in re.finditer(r"\bclass\s+([A-Za-z_$][\w$]*)\b[^\{]*\{", masked) if match.group(1) == Path(path).stem]
    if len(classes) != 1:
        raise _error("INVENTORY_UNSUPPORTED_LOCATOR", path, "Java test ownership is not representable by the closed locator.")
    declaration = classes[0]
    end = _matching_brace(masked, declaration.end() - 1)
    if end is None:
        raise _error("INVENTORY_PARSE_ERROR", path, "Supported Java test file could not be parsed.")
    annotation = r"@(?:[\w$.]+\.)?(Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b(?:\s*\([^)]*\))?"
    method = r"(?:public|protected|private)?\s*(?:static\s+)?[\w$<>\[\]., ?]+\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^\{]+)?\{"
    extra_annotation = r"\s*@(?:[\w$.]+)(?:\s*\([^)]*\))?"
    found = list(re.finditer(annotation + r"(?:" + extra_annotation + r")*\s*" + method, masked, re.MULTILINE))
    if not found and "@" in masked and "{" not in masked:
        raise _error("INVENTORY_PARSE_ERROR", path, "Supported Java test file could not be parsed.")
    opening = declaration.end() - 1
    if any(match.start() > end or _java_brace_depth(masked, opening, match.start()) != 1 for match in found):
        raise _error("INVENTORY_UNSUPPORTED_LOCATOR", path, "Java test ownership is not representable by the closed locator.")
    class_fqn = (package.group(1) + "." if package else "") + declaration.group(1)
    names = [match.group(2) for match in found]
    if len(names) != len(set(names)):
        raise _error("INVENTORY_UNSUPPORTED_LOCATOR", path, "Java test overloads are not representable by the closed locator.")
    return [{"kind": "java_class_method", "class_fqn": class_fqn, "method_name": name} for name in names]


def _inventory(project_root: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    language = module["stack"]["language"]
    if language not in {"python", "java"}:
        raise _error("INVENTORY_UNSUPPORTED_LANGUAGE", "/stack/language", "Only Python and Java static test inventory is supported.")
    paths = module.get("paths", {})
    roots = _declared_roots(module["_resolved_root"], project_root, paths.get("tests", ()), "/paths/tests")
    files: list[dict[str, str]] = []
    symbols: list[dict[str, Any]] = []
    seen_files: set[Path] = set()
    framework = module.get("test", {}).get("framework") or ("pytest" if language == "python" else "junit5")
    for root, _ in roots:
        for path in _iter_files(root, project_root, "/paths/tests"):
            if path in seen_files or not _is_supported_test_file(path, language):
                continue
            seen_files.add(path)
            portable = _portable_path(project_root, path)
            try:
                text = path.read_text(encoding="utf-8")
                data = path.read_bytes()
            except UnicodeDecodeError as error:
                raise _error("INVENTORY_DECODE_ERROR", portable, "Supported test file is not UTF-8.") from error
            except OSError as error:
                raise _error("INVENTORY_READ_ERROR", portable, "Supported test file could not be read.") from error
            file_id = "FILE-" + hashlib.sha256(portable.encode("utf-8")).hexdigest()
            locators = _python_locators(text, portable) if language == "python" else _java_locators(text, portable)
            files.append({"file_id": file_id, "path": portable, "language": language, "framework": framework, "content_digest": "sha256:" + hashlib.sha256(data).hexdigest()})
            for locator in locators:
                payload = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                symbols.append({"file_id": file_id, "symbol_id": "SYMBOL-" + hashlib.sha256(payload).hexdigest(), "locator": locator})
    files.sort(key=lambda row: row["path"])
    symbols.sort(key=lambda row: (row["file_id"], json.dumps(row["locator"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)))
    return {"module_id": module["id"], "test_roots": [portable for _, portable in roots], "files": files, "symbols": symbols}


def _excluded_product(path: Path, portable: str, test_paths: set[str], test_roots: Sequence[Path]) -> bool:
    lowered = path.name.lower()
    return (
        portable in test_paths or any(path.is_relative_to(root) for root in test_roots)
        or _is_supported_test_file(path, "python") or _is_supported_test_file(path, "java")
        or path.suffix.lower() not in _TEXT_SUFFIXES or lowered == ".env" or lowered.startswith(".env.")
        or path.suffix.lower() in _SECRET_SUFFIXES or bool(set(PurePosixPath(portable).parts) & _EXCLUDED_SEGMENTS)
    )


def _authorized_sources(project_root: Path, module: Mapping[str, Any], inventory: Mapping[str, Any], supplied_inputs: Sequence[SuppliedInput]) -> dict[str, Any]:
    seen_ids: set[str] = set()
    sources: list[dict[str, str]] = []
    for index, supplied in enumerate(supplied_inputs):
        if not isinstance(supplied.source_id, str) or not supplied.source_id:
            raise _error("INVENTORY_INVALID_SOURCE_ID", f"/supplied_inputs/{index}/source_id", "Supplied source ID must be a nonempty string.")
        if supplied.source_id in seen_ids:
            raise _error("INVENTORY_DUPLICATE_SOURCE_ID", f"/supplied_inputs/{index}/source_id", "Supplied source IDs must be unique.")
        if not isinstance(supplied.content, bytes):
            raise _error("INVENTORY_INVALID_SUPPLIED_INPUT", f"/supplied_inputs/{index}", "Supplied input content must be bytes.")
        seen_ids.add(supplied.source_id)
        sources.append({"source_id": supplied.source_id, "kind": "supplied_requirement", "content_digest": "sha256:" + hashlib.sha256(supplied.content).hexdigest()})
    paths = module.get("paths", {})
    values = [*paths.get("source", ()), *(value for group in module.get("feature_sources", {}).values() for value in group)]
    roots = _declared_roots(module["_resolved_root"], project_root, values, "/behavior_sources")
    test_roots = [root for root, _ in _declared_roots(module["_resolved_root"], project_root, paths.get("tests", ()), "/paths/tests")]
    test_paths = {row["path"] for row in inventory["files"]}
    seen_files: set[Path] = set()
    products: list[dict[str, str]] = []
    for root, _ in roots:
        for path in _iter_files(root, project_root, "/behavior_sources"):
            if path in seen_files:
                continue
            seen_files.add(path)
            portable = _portable_path(project_root, path)
            if _excluded_product(path, portable, test_paths, test_roots):
                continue
            try:
                content = path.read_bytes()
            except OSError as error:
                raise _error("INVENTORY_READ_ERROR", portable, "Authorized product file could not be read.") from error
            products.append({"source_id": "SOURCE-" + hashlib.sha256(b"product_file\0" + portable.encode("utf-8")).hexdigest(), "kind": "product_file", "path": portable, "content_digest": "sha256:" + hashlib.sha256(content).hexdigest()})
    products.sort(key=lambda row: row["path"])
    return {"module_id": module["id"], "sources": [*sources, *products]}


def build_source_inventories(project_root: Path, skillsrc: Mapping[str, Any], module_id: str | None, supplied_inputs: Sequence[SuppliedInput]) -> SourceInventories:
    try:
        root = project_root.resolve()
        normalized = normalize_skillsrc(skillsrc)
        module = dict(select_module(normalized, module_id))
        module["_resolved_root"] = resolve_module_root(root, module)
    except SkillsrcError as error:
        code = "INVENTORY_UNSAFE_PATH" if error.code in {"unsafe_path", "unsafe_module_root"} else "INVENTORY_MANIFEST"
        raise _error(code, "/skillsrc", str(error)) from error
    inventory = _inventory(root, module)
    sources = _authorized_sources(root, module, inventory, supplied_inputs)
    return SourceInventories(_freeze(inventory), _digest(inventory), _freeze(sources), _digest(sources))


def _artifact(value: SourceInventories) -> dict[str, Any]:
    return {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {"technical_test_inventory": value.technical_test_inventory, "technical_test_inventory_sha256": value.technical_test_inventory_sha256, "authorized_behavior_sources": value.authorized_behavior_sources, "authorized_behavior_sources_sha256": value.authorized_behavior_sources_sha256}, "warnings": []}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _parse_supplied(values: Sequence[str]) -> tuple[SuppliedInput, ...]:
    parsed: list[SuppliedInput] = []
    for value in values:
        if "=" not in value:
            raise _error("INVENTORY_ARGUMENT", "/supplied-input", "Supplied input must use SOURCE_ID=PATH.")
        source_id, path = value.split("=", 1)
        try:
            parsed.append(SuppliedInput(source_id, Path(path).read_bytes()))
        except OSError as error:
            raise _error("INVENTORY_READ_ERROR", "/supplied-input", "Supplied input file could not be read.") from error
    return tuple(parsed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_classification.py")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--project", required=True)
    inventory.add_argument("--skillsrc", required=True)
    inventory.add_argument("--module")
    inventory.add_argument("--supplied-input", action="append", default=[])
    inventory.add_argument("--output")
    try:
        args = parser.parse_args(argv)
        if args.command != "inventory":
            raise _error("INVENTORY_ARGUMENT", "/command", "Unknown command.")
        output = Path(args.output) if args.output else None
        if output is not None and output.exists():
            raise _error("INVENTORY_OUTPUT_EXISTS", "/output", "Output path already exists.")
        value = build_source_inventories(Path(args.project), load_skillsrc(Path(args.skillsrc)), args.module, _parse_supplied(args.supplied_input))
        payload = json.dumps(_plain(_artifact(value)), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if output is None:
            print(payload)
        else:
            output.write_text(payload + "\n", encoding="utf-8")
        return 0
    except (TestClassificationError, SkillsrcError, OSError, ValueError) as error:
        diagnostics = error.diagnostics if isinstance(error, TestClassificationError) else ({"path": "", "code": "INVENTORY_INPUT", "message": str(error)},)
        print(json.dumps({"status": "error", "diagnostics": _plain(diagnostics)}, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
