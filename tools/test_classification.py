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

try:
    if __package__:
        from .schema_validation import StrictJsonError, load_json_strict, schema_diagnostics
    else:
        from schema_validation import StrictJsonError, load_json_strict, schema_diagnostics
except ImportError:  # pragma: no cover - direct script execution is covered by smoke tests
    from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics

try:
    if __package__:
        from .stack_catalog import is_supported_static_test_file
    else:
        from stack_catalog import is_supported_static_test_file
except ImportError:  # pragma: no cover - package import is covered by tests
    from tools.stack_catalog import is_supported_static_test_file


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
            if path in seen_files or not is_supported_static_test_file(path, language):
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
    file_order = {row["file_id"]: index for index, row in enumerate(files)}
    symbols.sort(key=lambda row: (file_order[row["file_id"]], json.dumps(row["locator"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)))
    return {"module_id": module["id"], "test_roots": [portable for _, portable in roots], "files": files, "symbols": symbols}


def _excluded_product(path: Path, portable: str, test_paths: set[str], test_roots: Sequence[Path]) -> bool:
    lowered = path.name.lower()
    return (
        portable in test_paths or any(path.is_relative_to(root) for root in test_roots)
        or is_supported_static_test_file(path, "python") or is_supported_static_test_file(path, "java")
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


_SCHEMA_ROOT = Path(__file__).resolve().parents[1]


def _diag(path: str, code: str, message: str) -> Mapping[str, str]:
    return _freeze({"path": path, "code": code, "message": message})


def _stage_shape(value: Any, schema: str, code: str) -> tuple[Mapping[str, str], ...]:
    diagnostics = schema_diagnostics(value, _SCHEMA_ROOT / "schemas" / schema, _SCHEMA_ROOT)
    return tuple(_diag(row["path"], code, "Artifact does not satisfy its closed schema.") for row in diagnostics)


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _locator_bytes(locator: Mapping[str, Any]) -> bytes:
    return json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _inventory_diagnostics(inventory: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    artifacts = inventory["artifacts"]
    rows = artifacts["technical_test_inventory"]
    if artifacts["technical_test_inventory_sha256"] != _digest(rows):
        return (_diag("/artifacts/technical_test_inventory_sha256", "INVENTORY_DIGEST", "technical_test_inventory_sha256 must identify the bare inventory."),)
    files = rows["files"]
    symbols = rows["symbols"]
    paths = [row["path"] for row in files]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        return (_diag("/artifacts/technical_test_inventory/files", "INVENTORY_FILE_ORDER", "Inventory files must have unique canonical paths."),)
    file_ids = [row["file_id"] for row in files]
    if len(file_ids) != len(set(file_ids)):
        return (_diag("/artifacts/technical_test_inventory/files", "INVENTORY_FILE_IDS", "Inventory file IDs must be unique."),)
    pairs = [(row["file_id"], row["symbol_id"]) for row in symbols]
    if len(pairs) != len(set(pairs)):
        return (_diag("/artifacts/technical_test_inventory/symbols", "INVENTORY_SYMBOL_PAIRS", "Inventory symbol pairs must be unique."),)
    if any(file_id not in set(file_ids) for file_id, _ in pairs):
        return (_diag("/artifacts/technical_test_inventory/symbols", "INVENTORY_SYMBOL_FILE", "Every symbol must belong to an inventory file."),)
    file_order = {file_id: index for index, file_id in enumerate(file_ids)}
    canonical_symbols = sorted(symbols, key=lambda row: (file_order[row["file_id"]], _locator_bytes(row["locator"])))
    if symbols != canonical_symbols:
        return (_diag("/artifacts/technical_test_inventory/symbols", "INVENTORY_SYMBOL_ORDER", "Inventory symbols must use physical file and canonical locator order."),)
    for index, row in enumerate(files):
        path = Path(row["path"])
        if path.is_absolute() or ".." in PurePosixPath(row["path"]).parts:
            return (_diag(f"/artifacts/technical_test_inventory/files/{index}/path", "INVENTORY_UNSAFE_PATH", "Inventory paths must be safe project-relative paths."),)
    return ()


def _inventory_file_diagnostics(inventory: Mapping[str, Any], project_root: Path) -> tuple[Mapping[str, str], ...]:
    files = inventory["artifacts"]["technical_test_inventory"]["files"]
    for index, row in enumerate(files):
        path = Path(row["path"])
        try:
            resolved = _confined(project_root, project_root / path, f"/artifacts/technical_test_inventory/files/{index}/path")
            content = resolved.read_bytes()
        except TestClassificationError as error:
            return error.diagnostics
        except OSError:
            return (_diag(f"/artifacts/technical_test_inventory/files/{index}/path", "CLASSIFICATION_FILE_READ", "Inventory file could not be read."),)
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual != row["content_digest"]:
            return (_diag(f"/artifacts/technical_test_inventory/files/{index}/content_digest", "CLASSIFICATION_FILE_DRIFT", "Inventory file bytes no longer match the snapshot digest."),)
    return ()


def validate_technical_test_evidence(inventory: Mapping[str, Any], classification: Mapping[str, Any], classification_review: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]], project_root: Path) -> tuple[Mapping[str, str], ...]:
    """Validate closed test classifications and independent-review evidence without judging scope semantics."""
    for value, schema, code in (
        (inventory, "source-inventory-output.schema.json", "INVENTORY_SCHEMA"),
        (classification, "test-classifier-output.schema.json", "CLASSIFICATION_SCHEMA"),
        (classification_review, "test-classifier-reviewer-output.schema.json", "CLASSIFICATION_REVIEW_SCHEMA"),
    ):
        shape = _stage_shape(value, schema, code)
        if shape:
            return shape
    diagnostics = _inventory_diagnostics(inventory)
    if diagnostics:
        return diagnostics
    inventory_rows = inventory["artifacts"]["technical_test_inventory"]
    candidate_rows = classification["artifacts"]["classification"]
    review_rows = classification_review["artifacts"]["classification_review"]
    inventory_digest = inventory["artifacts"]["technical_test_inventory_sha256"]
    if candidate_rows["technical_test_inventory_sha256"] != inventory_digest:
        return (_diag("/artifacts/classification/technical_test_inventory_sha256", "CLASSIFICATION_INVENTORY_DIGEST", "Classification must use the exact inventory digest."),)
    expected_pairs = tuple((row["file_id"], row["symbol_id"]) for row in inventory_rows["symbols"])
    actual_pairs = tuple((row["file_id"], row["symbol_id"]) for row in candidate_rows["classifications"])
    if actual_pairs != expected_pairs:
        return (_diag("/artifacts/classification/classifications", "CLASSIFICATION_PAIR_COVERAGE", "Classifications must equal inventory symbol pairs in canonical order."),)
    requirement_rows = tuple(requirements)
    if any(not isinstance(row, Mapping) or not isinstance(row.get("requirement_id"), str) or not _exact_int(row.get("display_order")) for row in requirement_rows):
        return (_diag("/requirements", "CLASSIFICATION_REQUIREMENTS", "Requirements must have string IDs and exact integer display_order values."),)
    ordered_requirements = [row["requirement_id"] for row in requirement_rows]
    if len(ordered_requirements) != len(set(ordered_requirements)) or [row["display_order"] for row in requirement_rows] != list(range(1, len(requirement_rows) + 1)):
        return (_diag("/requirements", "CLASSIFICATION_REQUIREMENTS", "Requirements must have unique IDs in canonical display order."),)
    files = {row["file_id"]: row for row in inventory_rows["files"]}
    for index, row in enumerate(candidate_rows["classifications"]):
        prefix = f"/artifacts/classification/classifications/{index}"
        links = row["requirement_ids"]
        if len(links) != len(set(links)):
            return (_diag(f"{prefix}/requirement_ids", "CLASSIFICATION_REQUIREMENT_LINK", "Requirement links must be unique."),)
        for link_index, requirement_id in enumerate(links):
            if requirement_id not in ordered_requirements:
                return (_diag(f"{prefix}/requirement_ids/{link_index}", "CLASSIFICATION_REQUIREMENT_LINK", "Requirement link is not present in the supplied requirements."),)
        if links != [requirement_id for requirement_id in ordered_requirements if requirement_id in links]:
            return (_diag(f"{prefix}/requirement_ids", "CLASSIFICATION_REQUIREMENT_ORDER", "Requirement links must follow requirement display order."),)
        spans = row["provenance"]
        previous: tuple[int, int] | None = None
        seen_spans: set[tuple[int, int]] = set()
        line_count: int | None = None
        for span_index, span in enumerate(spans):
            span_path = f"{prefix}/provenance/{span_index}"
            if span["file_id"] != row["file_id"]:
                return (_diag(f"{span_path}/file_id", "CLASSIFICATION_PROVENANCE", "Provenance must remain in the classified inventory file."),)
            start, end = span["start_line"], span["end_line"]
            if not _exact_int(start):
                return (_diag(f"{span_path}/start_line", "CLASSIFICATION_PROVENANCE", "Provenance start_line must be an exact positive integer."),)
            if not _exact_int(end):
                return (_diag(f"{span_path}/end_line", "CLASSIFICATION_PROVENANCE", "Provenance end_line must be an exact positive integer."),)
            if line_count is None:
                try:
                    line_count = len((project_root / files[row["file_id"]]["path"]).read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeDecodeError):
                    return (_diag(f"{prefix}/provenance", "CLASSIFICATION_PROVENANCE", "Provenance source file could not be read as UTF-8."),)
            pair = (start, end)
            if start < 1 or end < start:
                return (_diag(span_path if end < start else f"{span_path}/start_line", "CLASSIFICATION_PROVENANCE", "Provenance spans must be positive with start_line no later than end_line."),)
            if end > line_count:
                return (_diag(f"{span_path}/end_line", "CLASSIFICATION_PROVENANCE", "Provenance span exceeds the current physical file."),)
            if pair in seen_spans or (previous is not None and pair < previous):
                return (_diag(f"{prefix}/provenance", "CLASSIFICATION_PROVENANCE", "Provenance spans must be unique and canonical by line range."),)
            seen_spans.add(pair); previous = pair
    diagnostics = _inventory_file_diagnostics(inventory, project_root)
    if diagnostics:
        return diagnostics
    if review_rows["technical_test_inventory_sha256"] != inventory_digest:
        return (_diag("/artifacts/classification_review/technical_test_inventory_sha256", "CLASSIFICATION_REVIEW_INVENTORY_DIGEST", "Review must use the exact inventory digest."),)
    if review_rows["classification_sha256"] != _digest(candidate_rows):
        return (_diag("/artifacts/classification_review/classification_sha256", "CLASSIFICATION_REVIEW_DIGEST", "Review must identify the bare candidate classification."),)
    reviewed_pairs = tuple((row["file_id"], row["symbol_id"]) for row in review_rows["reviewed_symbol_pairs"])
    if reviewed_pairs != expected_pairs:
        return (_diag("/artifacts/classification_review/reviewed_symbol_pairs", "CLASSIFICATION_REVIEW_COVERAGE", "Reviewed pairs must equal inventory symbol pairs in canonical order."),)
    accepted = review_rows["verdict"] == "ПРИНЯТО"
    if (accepted and review_rows["findings"]) or (not accepted and not review_rows["findings"]):
        return (_diag("/artifacts/classification_review/findings", "CLASSIFICATION_REVIEW_VERDICT", "Review findings must match the review verdict."),)
    return ()


def select_effective_technical_evidence(inventory: Mapping[str, Any], classification: Mapping[str, Any], classification_review: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]], project_root: Path) -> Mapping[str, Any]:
    """Return the accepted immutable technical-evidence carrier, or immutable diagnostics."""
    diagnostics = validate_technical_test_evidence(inventory, classification, classification_review, requirements, project_root)
    if diagnostics:
        raise TestClassificationError(diagnostics)
    review = classification_review["artifacts"]["classification_review"]
    if review["verdict"] != "ПРИНЯТО":
        raise _error("CLASSIFICATION_REVIEW_NOT_ACCEPTED", "/artifacts/classification_review/verdict", "Only an accepted independent review can select technical evidence.")
    technical_inventory = inventory["artifacts"]["technical_test_inventory"]
    candidate = classification["artifacts"]["classification"]
    result: dict[str, Any] = {
        "technical_test_inventory_sha256": inventory["artifacts"]["technical_test_inventory_sha256"],
        "technical_test_classification_sha256": _digest(candidate),
        "technical_test_review_sha256": _digest(review),
        "files": _plain(technical_inventory["files"]),
        "symbols": _plain(technical_inventory["symbols"]),
        "classifications": _plain(candidate["classifications"]),
    }
    result["effective_technical_evidence_sha256"] = _digest(result)
    return _freeze(result)


def validate_managed_behavior_context(behavior_context: Mapping[str, Any], authorized_behavior_sources: Mapping[str, Any], test_inventory: Mapping[str, Any], project_root: Path) -> tuple[Mapping[str, str], ...]:
    """Validate the closed managed-behavior provenance graph against snapshot and current bytes."""
    if not isinstance(behavior_context, Mapping):
        return (_diag("/artifacts/managed_behavior_context", "BEHAVIOR_CONTEXT", "Managed behavior context must be an object."),)
    if not isinstance(authorized_behavior_sources, Mapping) or not isinstance(test_inventory, Mapping):
        return (_diag("/input", "BEHAVIOR_INPUT", "Authorized sources and test inventory must be objects."),)
    required = ("authorized_behavior_sources_sha256", "requirements", "product_sources", "requirement_sources")
    if set(behavior_context) != set(required):
        return (_diag("/artifacts/managed_behavior_context", "BEHAVIOR_CONTEXT", "Managed behavior context must use its closed V4 fields."),)
    sources = authorized_behavior_sources.get("sources")
    inventory_files = test_inventory.get("files")
    requirements = behavior_context["requirements"]
    product_sources = behavior_context["product_sources"]
    requirement_sources = behavior_context["requirement_sources"]
    if not isinstance(sources, list) or not isinstance(inventory_files, list) or not isinstance(requirements, list) or not isinstance(product_sources, list) or not isinstance(requirement_sources, list):
        return (_diag("/artifacts/managed_behavior_context", "BEHAVIOR_CONTEXT", "Managed behavior context inputs must use array carriers."),)
    if set(authorized_behavior_sources) != {"module_id", "sources"} or not isinstance(authorized_behavior_sources.get("module_id"), str):
        return (_diag("/authorized_behavior_sources", "BEHAVIOR_AUTHORIZED_SOURCES", "Authorized sources must use their closed inventory shape."),)
    for index, source in enumerate(sources):
        pointer = f"/authorized_behavior_sources/sources/{index}"
        if not isinstance(source, Mapping):
            return (_diag(pointer, "BEHAVIOR_AUTHORIZED_SOURCES", "Authorized sources must use closed inventory rows."),)
        kind = source.get("kind")
        expected = {"source_id", "kind", "content_digest"} if kind == "supplied_requirement" else {"source_id", "kind", "path", "content_digest"} if kind == "product_file" else None
        if expected is None or set(source) != expected or not isinstance(source.get("source_id"), str) or not isinstance(source.get("content_digest"), str):
            return (_diag(pointer, "BEHAVIOR_AUTHORIZED_SOURCES", "Authorized sources must use supported closed inventory kinds."),)
    if behavior_context["authorized_behavior_sources_sha256"] != _digest(authorized_behavior_sources):
        return (_diag("/artifacts/managed_behavior_context/authorized_behavior_sources_sha256", "BEHAVIOR_AUTHORIZED_DIGEST", "Authorized behavior sources must match their exact snapshot digest."),)
    authorized_by_id: dict[str, Mapping[str, Any]] = {}
    for row in sources:
        if not isinstance(row, Mapping) or not isinstance(row.get("source_id"), str) or row["source_id"] in authorized_by_id:
            return (_diag("/authorized_behavior_sources/sources", "BEHAVIOR_AUTHORIZED_SOURCES", "Authorized source IDs must be unique closed rows."),)
        authorized_by_id[row["source_id"]] = row
    test_paths = {row.get("path") for row in inventory_files if isinstance(row, Mapping) and isinstance(row.get("path"), str)}
    requirement_ids: list[str] = []
    for index, row in enumerate(requirements):
        if not isinstance(row, Mapping) or not isinstance(row.get("requirement_id"), str) or not _exact_int(row.get("display_order")):
            return (_diag(f"/artifacts/managed_behavior_context/requirements/{index}", "BEHAVIOR_REQUIREMENTS", "Requirements must retain canonical IDs and exact display order."),)
        requirement_ids.append(row["requirement_id"])
    if not requirement_ids or len(requirement_ids) != len(set(requirement_ids)) or [row["display_order"] for row in requirements] != list(range(1, len(requirements) + 1)):
        return (_diag("/artifacts/managed_behavior_context/requirements", "BEHAVIOR_REQUIREMENTS", "Requirements must retain unique canonical display order."),)
    product_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(product_sources):
        pointer = f"/artifacts/managed_behavior_context/product_sources/{index}"
        if not isinstance(row, Mapping) or set(row) != {"source_id", "kind", "path", "content_digest", "summary"}:
            return (_diag(pointer, "BEHAVIOR_PRODUCT_SOURCE", "Product source rows must use their closed V4 fields."),)
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or source_id in product_by_id:
            return (_diag(f"{pointer}/source_id", "BEHAVIOR_PRODUCT_SOURCE", "Product source IDs must be unique strings."),)
        authorized = authorized_by_id.get(source_id)
        if not isinstance(authorized, Mapping) or authorized.get("kind") != "product_file":
            return (_diag(f"{pointer}/source_id", "BEHAVIOR_SOURCE_MATCH", "Product source must match an authorized product source."),)
        if any(row.get(field) != authorized.get(field) for field in ("source_id", "kind", "path", "content_digest")):
            return (_diag(pointer, "BEHAVIOR_SOURCE_MATCH", "Product source identity must exactly match the authorized snapshot."),)
        path = row["path"]
        if path in test_paths:
            return (_diag(f"{pointer}/path", "BEHAVIOR_TEST_SOURCE_FORBIDDEN", "Test inventory paths cannot originate managed behavior."),)
        if not isinstance(path, str) or Path(path).is_absolute() or ".." in PurePosixPath(path).parts:
            return (_diag(f"{pointer}/path", "BEHAVIOR_SOURCE_PATH", "Product source paths must be safe project-relative paths."),)
        try:
            resolved = _confined(project_root, project_root / path, f"{pointer}/path")
            actual = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
        except TestClassificationError as error:
            return error.diagnostics
        except OSError:
            return (_diag(f"{pointer}/path", "BEHAVIOR_SOURCE_READ", "Product source bytes could not be read."),)
        if actual != row["content_digest"]:
            return (_diag(f"{pointer}/content_digest", "BEHAVIOR_SOURCE_DRIFT", "Product source bytes no longer match the authorized digest."),)
        product_by_id[source_id] = row
    actual_requirement_ids: list[str] = []
    for index, row in enumerate(requirement_sources):
        pointer = f"/artifacts/managed_behavior_context/requirement_sources/{index}"
        if not isinstance(row, Mapping) or set(row) != {"requirement_id", "source_ids"} or not isinstance(row.get("requirement_id"), str) or not isinstance(row.get("source_ids"), list):
            return (_diag(pointer, "BEHAVIOR_REQUIREMENT_SOURCE", "Requirement source rows must use their closed V4 fields."),)
        actual_requirement_ids.append(row["requirement_id"])
        links = row["source_ids"]
        if not links or any(not isinstance(source_id, str) for source_id in links) or len(links) != len(set(links)):
            return (_diag(f"{pointer}/source_ids", "BEHAVIOR_REQUIREMENT_SOURCE_LINK", "Requirement source links must be nonempty unique source IDs."),)
        if links != sorted(links):
            return (_diag(f"{pointer}/source_ids", "BEHAVIOR_REQUIREMENT_SOURCE_ORDER", "Requirement source IDs must use canonical source ID order."),)
        for link_index, source_id in enumerate(links):
            authorized = authorized_by_id.get(source_id)
            if authorized is None:
                return (_diag(f"{pointer}/source_ids/{link_index}", "BEHAVIOR_REQUIREMENT_SOURCE_LINK", "Requirement source link is not authorized."),)
            if authorized.get("kind") == "product_file":
                if authorized.get("path") in test_paths:
                    return (_diag(f"{pointer}/source_ids/{link_index}", "BEHAVIOR_TEST_SOURCE_FORBIDDEN", "Test inventory paths cannot originate managed behavior."),)
                if source_id not in product_by_id:
                    return (_diag(f"{pointer}/source_ids/{link_index}", "BEHAVIOR_PRODUCT_SOURCE_LINK", "Linked product sources must be present in product_sources."),)
    if actual_requirement_ids != requirement_ids:
        code = "BEHAVIOR_REQUIREMENT_SOURCE_COVERAGE" if set(actual_requirement_ids) != set(requirement_ids) or len(actual_requirement_ids) != len(requirement_ids) else "BEHAVIOR_REQUIREMENT_SOURCE_ORDER"
        return (_diag("/artifacts/managed_behavior_context/requirement_sources", code, "Requirement source rows must equal requirements in canonical order."),)
    return ()


def _context_requirements(path: Path) -> Sequence[Mapping[str, Any]]:
    try:
        context = load_json_strict(path)
    except (OSError, StrictJsonError) as error:
        raise _error("CLASSIFICATION_CONTEXT", "/context", "Context artifact could not be read as strict JSON.") from error
    if not isinstance(context, Mapping) or context.get("schema_version") != "4.0.0" or context.get("stage") != "context-marker":
        raise _error("CLASSIFICATION_CONTEXT", "/schema_version", "Context must be the V4 context-marker envelope.")
    artifacts = context.get("artifacts")
    managed = artifacts.get("managed_behavior_context") if isinstance(artifacts, Mapping) else None
    requirements = managed.get("requirements") if isinstance(managed, Mapping) else None
    if not isinstance(requirements, list):
        raise _error("CLASSIFICATION_CONTEXT", "/artifacts/managed_behavior_context/requirements", "V4 context must provide managed behavior requirements.")
    return requirements


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_classification.py")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--project", required=True)
    inventory.add_argument("--skillsrc", required=True)
    inventory.add_argument("--module")
    inventory.add_argument("--supplied-input", action="append", default=[])
    inventory.add_argument("--output")
    select = commands.add_parser("select")
    select.add_argument("--project", required=True)
    select.add_argument("--inventory", required=True)
    select.add_argument("--classification", required=True)
    select.add_argument("--review", required=True)
    select.add_argument("--context", required=True)
    select.add_argument("--output")
    validate_context = commands.add_parser("validate-context")
    validate_context.add_argument("--project", required=True)
    validate_context.add_argument("--inventory", required=True)
    validate_context.add_argument("--context", required=True)
    try:
        args = parser.parse_args(argv)
        output = Path(args.output) if hasattr(args, "output") and args.output else None
        if output is not None and output.exists():
            raise _error("INVENTORY_OUTPUT_EXISTS" if args.command == "inventory" else "CLASSIFICATION_OUTPUT_EXISTS", "/output", "Output path already exists.")
        if args.command == "inventory":
            value = build_source_inventories(Path(args.project), load_skillsrc(Path(args.skillsrc)), args.module, _parse_supplied(args.supplied_input))
            result = _artifact(value)
        elif args.command == "select":
            try:
                input_inventory = load_json_strict(Path(args.inventory))
                input_classification = load_json_strict(Path(args.classification))
                input_review = load_json_strict(Path(args.review))
            except (OSError, StrictJsonError) as error:
                raise _error("CLASSIFICATION_INPUT", "/input", "Classification input artifact could not be read as strict JSON.") from error
            result = select_effective_technical_evidence(input_inventory, input_classification, input_review, _context_requirements(Path(args.context)), Path(args.project))
        elif args.command == "validate-context":
            try:
                input_inventory = load_json_strict(Path(args.inventory))
            except (OSError, StrictJsonError) as error:
                raise _error("BEHAVIOR_CONTEXT", "/inventory", "Source inventory artifact could not be read as strict JSON.") from error
            try:
                context = load_json_strict(Path(args.context))
            except (OSError, StrictJsonError) as error:
                raise _error("BEHAVIOR_CONTEXT", "/context", "Context artifact could not be read as strict JSON.") from error
            shape = _stage_shape(input_inventory, "source-inventory-output.schema.json", "BEHAVIOR_INVENTORY")
            if shape:
                raise TestClassificationError(shape)
            shape = _stage_shape(context, "context-marker-output.schema.json", "BEHAVIOR_CONTEXT")
            if shape:
                raise TestClassificationError(shape)
            artifacts = input_inventory.get("artifacts") if isinstance(input_inventory, Mapping) else None
            if not isinstance(artifacts, Mapping):
                raise _error("BEHAVIOR_CONTEXT", "/inventory", "Inventory must provide authorized behavior sources.")
            diagnostics = validate_managed_behavior_context(context["artifacts"]["managed_behavior_context"], artifacts["authorized_behavior_sources"], artifacts["technical_test_inventory"], Path(args.project))
            if diagnostics:
                raise TestClassificationError(diagnostics)
            result = {"status": "valid", "diagnostics": []}
        else:  # argparse constrains this branch; retain a safe diagnostic for direct callers.
            raise _error("INVENTORY_ARGUMENT", "/command", "Unknown command.")
        payload = json.dumps(_plain(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
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
