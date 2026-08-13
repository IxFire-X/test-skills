"""Validation of V3 automation artifacts against one canonical document."""

from __future__ import annotations

import json
import keyword
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tools.canonical_document import document_sha256, validate_canonical_document
from tools.schema_validation import classify_version, schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "tc-to-autotest-output.schema.json"
_JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_JAVA_KEYWORDS = frozenset("""
abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while _
""".split())

_MESSAGES = {
    "AUTOMATION_SOURCE_DOCUMENT": "source document_id must match the canonical document",
    "AUTOMATION_SOURCE_REVISION": "source revision must match the canonical document",
    "AUTOMATION_SOURCE_DIGEST": "source_digest must identify bare canonical document bytes",
    "AUTOMATION_DUPLICATE_FILE": "file_id must be document-global",
    "AUTOMATION_DUPLICATE_PATH": "generated file path must be document-unique",
    "AUTOMATION_MIXED_LANGUAGE": "generated files must use one language",
    "AUTOMATION_PORTABLE_PATH": "path must be a portable relative slash path",
    "AUTOMATION_UNKNOWN_FILE": "file_id is not declared by generated_files",
    "AUTOMATION_DUPLICATE_SYMBOL": "symbol_id must be unique within its file",
    "AUTOMATION_DUPLICATE_LOCATOR": "a file may not declare the same locator twice",
    "AUTOMATION_INVALID_PYTHON_IDENTIFIER": "Python locator segments must be non-keyword identifiers",
    "AUTOMATION_INVALID_JAVA_IDENTIFIER": "Java locator segments must be non-keyword identifiers",
    "AUTOMATION_LOCATOR_LANGUAGE": "locator kind must match its generated file language",
    "AUTOMATION_UNKNOWN_SYMBOL": "symbol_id is not declared for file_id",
    "AUTOMATION_DUPLICATE_RELATION": "implementation relation key must be unique",
    "AUTOMATION_RELATION_ORDER": "implementation relations must use canonical physical order",
    "AUTOMATION_UNKNOWN_CASE": "case_id is not declared by the canonical document",
    "AUTOMATION_UNKNOWN_STEP": "step_id does not belong to case_id",
    "AUTOMATION_UNKNOWN_EXPECTATION": "expectation_id does not belong to step_id",
    "AUTOMATION_UNKNOWN_ASSERTION": "assertion_id does not belong to expectation_id",
    "AUTOMATION_NONREADY_TARGET": "relations may target only ready steps",
    "AUTOMATION_MISSING_OPERATION_COVERAGE": "ready step requires an operation relation",
    "AUTOMATION_MISSING_ASSERTION_COVERAGE": "ready assertion requires an assertion relation",
    "AUTOMATION_ORPHAN_SYMBOL": "generated symbol must occur in a relation",
    "AUTOMATION_ORPHAN_FILE": "generated file must own a related symbol",
    "AUTOMATION_DUPLICATE_MANUAL_DISPOSITION": "manual disposition must be unique",
    "AUTOMATION_UNKNOWN_MANUAL_STEP": "manual disposition step_id does not belong to case_id",
    "AUTOMATION_MANUAL_DISPOSITION_READY": "manual disposition may target only manual-only steps",
    "AUTOMATION_MANUAL_DISPOSITION_BLOCKED": "manual disposition may not target blocked steps",
    "AUTOMATION_MISSING_MANUAL_DISPOSITION": "manual-only step requires one manual disposition",
    "AUTOMATION_MANUAL_ORDER": "manual dispositions must use canonical case and step order",
    "AUTOMATION_BLOCKER_REQUIRES_BLOCKED": "canonical blockers require BLOCKED automation status",
    "AUTOMATION_BLOCKED_WITHOUT_BLOCKER": "BLOCKED status requires a canonical blocker",
    "AUTOMATION_BLOCKED_CONTENT": "BLOCKED artifacts require diagnostics and four empty coverage arrays",
    "AUTOMATION_GENERATED_DIAGNOSTICS": "GENERATED artifacts require empty diagnostics",
}


class AutomationArtifactError(ValueError):
    """Raised by ``required_symbol_pairs`` for an invalid automation artifact."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]) -> None:
        self._diagnostics = tuple(MappingProxyType(dict(item)) for item in diagnostics)
        super().__init__(json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")))

    @property
    def diagnostics(self) -> tuple[Mapping[str, str], ...]:
        return self._diagnostics


def _pointer(*parts: object) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _diagnostic(code: str, path: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": _MESSAGES[code]}


def _portable(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in path):
        return False
    return all(segment and segment not in {".", ".."} and not segment.endswith((".", " ")) for segment in path.split("/"))


def _python_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _java_name(name: str) -> bool:
    return bool(_JAVA_IDENTIFIER.fullmatch(name)) and name not in _JAVA_KEYWORDS


def _document_index(document: Mapping[str, Any]) -> tuple[dict[str, tuple[int, Mapping[str, Any]]], dict[tuple[str, str], tuple[int, Mapping[str, Any]]]]:
    cases: dict[str, tuple[int, Mapping[str, Any]]] = {}
    steps: dict[tuple[str, str], tuple[int, Mapping[str, Any]]] = {}
    for case_index, case in enumerate(document["test_cases"]):
        cases[case["case_id"]] = (case_index, case)
        for step_index, step in enumerate(case["steps"]):
            steps[(case["case_id"], step["step_id"])] = (step_index, step)
    return cases, steps


def validate_automation_artifact(artifact: Any, document: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic V3 schema and atomic-relation diagnostics."""
    canonical = validate_canonical_document(document)
    if canonical:
        return canonical
    version = classify_version(artifact)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [{"path": "/schema_version", "code": version["code"], "message": version["message"]}]
    structural = schema_diagnostics(artifact, _SCHEMA, _ROOT)
    if structural:
        return structural

    artifacts = artifact["artifacts"]
    diagnostics: list[dict[str, str]] = []
    source = artifacts["source"]
    if source["document_id"] != document["document_id"]:
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_DOCUMENT", "/artifacts/source/document_id"))
    if source["revision"] != document["revision"]:
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_REVISION", "/artifacts/source/revision"))
    if source["source_digest"] != document_sha256(document):
        diagnostics.append(_diagnostic("AUTOMATION_SOURCE_DIGEST", "/artifacts/source/source_digest"))

    files = artifacts["generated_files"]
    file_map: dict[str, Mapping[str, Any]] = {}
    path_seen: set[str] = set()
    languages: set[str] = set()
    for index, row in enumerate(files):
        file_id, path = row["file_id"], row["path"]
        if file_id in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_FILE", _pointer("artifacts", "generated_files", index, "file_id")))
        else:
            file_map[file_id] = row
        if path in path_seen:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_PATH", _pointer("artifacts", "generated_files", index, "path")))
        path_seen.add(path)
        if not _portable(path):
            diagnostics.append(_diagnostic("AUTOMATION_PORTABLE_PATH", _pointer("artifacts", "generated_files", index, "path")))
        if languages and row["language"] not in languages:
            diagnostics.append(_diagnostic("AUTOMATION_MIXED_LANGUAGE", _pointer("artifacts", "generated_files", index, "language")))
        languages.add(row["language"])

    pair_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    locators: dict[str, set[str]] = {}
    for index, row in enumerate(artifacts["generated_symbols"]):
        file_id, symbol_id = row["file_id"], row["symbol_id"]
        base = _pointer("artifacts", "generated_symbols", index)
        if file_id not in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_FILE", base + "/file_id"))
        pair = (file_id, symbol_id)
        if pair in pair_map:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_SYMBOL", base + "/symbol_id"))
        else:
            pair_map[pair] = row
        locator = row["locator"]
        locator_key = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if locator_key in locators.setdefault(file_id, set()):
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_LOCATOR", base + "/locator"))
        locators[file_id].add(locator_key)
        kind = locator["kind"]
        language = file_map.get(file_id, {}).get("language")
        if (kind.startswith("python_") and language != "python") or (kind == "java_class_method" and language != "java"):
            diagnostics.append(_diagnostic("AUTOMATION_LOCATOR_LANGUAGE", base + "/locator/kind"))
        if kind == "python_module_function" and not _python_name(locator["function_name"]):
            diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/function_name"))
        elif kind == "python_class_method":
            if not all(_python_name(part) for part in locator["qualified_class_name"].split(".")):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/qualified_class_name"))
            if not _python_name(locator["method_name"]):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_PYTHON_IDENTIFIER", base + "/locator/method_name"))
        elif kind == "java_class_method":
            if not all(_java_name(part) for part in locator["class_fqn"].split(".")):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_JAVA_IDENTIFIER", base + "/locator/class_fqn"))
            if not _java_name(locator["method_name"]):
                diagnostics.append(_diagnostic("AUTOMATION_INVALID_JAVA_IDENTIFIER", base + "/locator/method_name"))

    cases, steps = _document_index(document)
    relations = artifacts["implementation_relations"]
    relation_keys: set[tuple[Any, ...]] = set()
    valid_relation_rows: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
    target_operations: set[tuple[str, str]] = set()
    target_assertions: set[tuple[str, str, str, str]] = set()
    related_pairs: set[tuple[str, str]] = set()
    for index, row in enumerate(relations):
        base = _pointer("artifacts", "implementation_relations", index)
        pair = (row["file_id"], row["symbol_id"])
        row_valid = row["file_id"] in file_map and pair in pair_map
        if row["file_id"] not in file_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_FILE", base + "/file_id"))
        if pair not in pair_map:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_SYMBOL", base + "/symbol_id"))
        case = cases.get(row["case_id"])
        if case is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_CASE", base + "/case_id"))
            continue
        step_data = steps.get((row["case_id"], row["step_id"]))
        if step_data is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_STEP", base + "/step_id"))
            continue
        step_index, step = step_data
        if step["manual_only"] or step["automation_blockers"]:
            diagnostics.append(_diagnostic("AUTOMATION_NONREADY_TARGET", base + "/step_id"))
            row_valid = False
        expectation_index = assertion_index = -1
        expectation_id = assertion_id = None
        if row["kind"] == "assertion":
            expectation_id, assertion_id = row["expectation_id"], row["assertion_id"]
            expectation = next(((i, item) for i, item in enumerate(step["expectations"]) if item["expectation_id"] == expectation_id), None)
            if expectation is None:
                diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_EXPECTATION", base + "/expectation_id"))
                continue
            expectation_index, expectation_row = expectation
            assertion = next(((i, item) for i, item in enumerate(expectation_row["assertions"]) if item["assertion_id"] == assertion_id), None)
            if assertion is None:
                diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_ASSERTION", base + "/assertion_id"))
                continue
            assertion_index = assertion[0]
        key = (row["kind"], row["case_id"], row["step_id"], expectation_id, assertion_id, row["file_id"], row["symbol_id"])
        if key in relation_keys:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_RELATION", base))
            row_valid = False
        relation_keys.add(key)
        if not row_valid:
            continue
        if row["kind"] == "assertion":
            target_assertions.add((row["case_id"], row["step_id"], expectation_id, assertion_id))
        else:
            target_operations.add((row["case_id"], row["step_id"]))
        related_pairs.add(pair)
        valid_relation_rows.append(((case[0], step_index, 0 if row["kind"] == "operation" else 1, expectation_index, assertion_index, row["file_id"], row["symbol_id"]), row))
    if [row for _, row in valid_relation_rows] != [row for _, row in sorted(valid_relation_rows, key=lambda item: item[0])]:
        diagnostics.append(_diagnostic("AUTOMATION_RELATION_ORDER", "/artifacts/implementation_relations"))

    for index, row in enumerate(artifacts["generated_symbols"]):
        if (row["file_id"], row["symbol_id"]) not in related_pairs:
            diagnostics.append(_diagnostic("AUTOMATION_ORPHAN_SYMBOL", _pointer("artifacts", "generated_symbols", index)))
    for index, row in enumerate(files):
        if not any(pair[0] == row["file_id"] for pair in related_pairs):
            diagnostics.append(_diagnostic("AUTOMATION_ORPHAN_FILE", _pointer("artifacts", "generated_files", index)))

    manual_rows = artifacts["manual_dispositions"]
    manual_keys: set[tuple[str, str]] = set()
    manual_order: list[tuple[int, int]] = []
    for index, row in enumerate(manual_rows):
        base = _pointer("artifacts", "manual_dispositions", index)
        key = (row["case_id"], row["step_id"])
        if key in manual_keys:
            diagnostics.append(_diagnostic("AUTOMATION_DUPLICATE_MANUAL_DISPOSITION", base))
        manual_keys.add(key)
        case, step_data = cases.get(row["case_id"]), steps.get(key)
        if case is None or step_data is None:
            diagnostics.append(_diagnostic("AUTOMATION_UNKNOWN_MANUAL_STEP", base + "/step_id"))
            continue
        step_index, step = step_data
        manual_order.append((case[0], step_index))
        if step["automation_blockers"]:
            diagnostics.append(_diagnostic("AUTOMATION_MANUAL_DISPOSITION_BLOCKED", base))
        elif not step["manual_only"]:
            diagnostics.append(_diagnostic("AUTOMATION_MANUAL_DISPOSITION_READY", base))
    if manual_order != sorted(manual_order):
        diagnostics.append(_diagnostic("AUTOMATION_MANUAL_ORDER", "/artifacts/manual_dispositions"))

    blocked = any(step["automation_blockers"] for _, step in steps.values())
    status = artifacts["automation_status"]
    arrays = ("generated_files", "generated_symbols", "implementation_relations", "manual_dispositions")
    if blocked:
        if status != "BLOCKED":
            diagnostics.append(_diagnostic("AUTOMATION_BLOCKER_REQUIRES_BLOCKED", "/artifacts/automation_status"))
        if not artifacts["diagnostics"] or any(artifacts[name] for name in arrays):
            diagnostics.append(_diagnostic("AUTOMATION_BLOCKED_CONTENT", "/artifacts"))
    elif status == "BLOCKED":
        diagnostics.append(_diagnostic("AUTOMATION_BLOCKED_WITHOUT_BLOCKER", "/artifacts/automation_status"))
    elif artifacts["diagnostics"]:
        diagnostics.append(_diagnostic("AUTOMATION_GENERATED_DIAGNOSTICS", "/artifacts/diagnostics"))

    if status == "GENERATED" and not blocked:
        for (case_id, step_id), (step_index, step) in steps.items():
            case_index = cases[case_id][0]
            step_path = _pointer("test_cases", case_index, "steps", step_index)
            if step["manual_only"]:
                if (case_id, step_id) not in manual_keys:
                    diagnostics.append(_diagnostic("AUTOMATION_MISSING_MANUAL_DISPOSITION", step_path))
                continue
            if step["automation_blockers"]:
                continue
            if (case_id, step_id) not in target_operations:
                diagnostics.append(_diagnostic("AUTOMATION_MISSING_OPERATION_COVERAGE", step_path))
            for expectation_index, expectation in enumerate(step["expectations"]):
                for assertion_index, assertion in enumerate(expectation["assertions"]):
                    if (case_id, step_id, expectation["expectation_id"], assertion["assertion_id"]) not in target_assertions:
                        diagnostics.append(_diagnostic("AUTOMATION_MISSING_ASSERTION_COVERAGE", _pointer("test_cases", case_index, "steps", step_index, "expectations", expectation_index, "assertions", assertion_index)))
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))


def required_symbol_pairs(artifact: Any, document: dict[str, Any]) -> set[tuple[str, str]]:
    """Return required runtime pairs or raise an immutable diagnostic error."""
    diagnostics = validate_automation_artifact(artifact, document)
    if diagnostics:
        raise AutomationArtifactError(diagnostics)
    return {(row["file_id"], row["symbol_id"]) for row in artifact["artifacts"]["implementation_relations"]}
