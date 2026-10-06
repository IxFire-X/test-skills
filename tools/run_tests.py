#!/usr/bin/env python3
"""V5 generated-test runner with exact ``(file_id, symbol_id)`` evidence."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence

if __package__:
    from .json_cli import JsonArgumentParser
    from .skillsrc_manifest import (
        SkillsrcError,
        load_skillsrc,
        normalize_skillsrc,
        resolve_module_root,
        select_module,
    )
else:
    _bootstrap_root = str(Path(__file__).resolve().parents[1])
    if _bootstrap_root not in sys.path:
        sys.path.insert(0, _bootstrap_root)
    from json_cli import JsonArgumentParser
    from skillsrc_manifest import (
        SkillsrcError,
        load_skillsrc,
        normalize_skillsrc,
        resolve_module_root,
        select_module,
    )

_ROOT = Path(__file__).resolve().parents[1]
_AUTOMATION_SCHEMA = _ROOT / "schemas" / "tc-to-autotest-output.schema.json"
_AUTOTEST_REVIEW_SCHEMA = _ROOT / "schemas" / "autotest-reviewer-output.schema.json"
_TIMEOUT = 600
_PROCESS_STOP_ATTESTATION = object()
_CONTROLLER_STOP_ROWS: set[str] = set()
DURABLE_NATIVE_REPORT_MAX_BYTES = 1024 * 1024
NATIVE_REPORT_MAX_BYTES = 8 * 1024 * 1024
NATIVE_REPORT_SET_MAX_BYTES = 32 * 1024 * 1024
PROCESS_OUTPUT_TAIL_BYTES = 64 * 1024
DURABLE_NATIVE_REPORT_NORMALIZATION = "junit-semantic-v1"
_PROCESS_ERROR_CLASSES = {
    "TIMEOUT": "TimeoutExpired",
    "OS_ERROR": "OSError",
    "COLLECTION_ERROR": "CollectionError",
    "EXECUTION_ERROR": "ExecutionError",
    "JUNIT_MISSING": "JUnitMissing",
    "JUNIT_INVALID": "ParseError",
    "SOURCE_CHANGED": "SourceChanged",
    "BASELINE_DRIFT": "BaselineDrift",
    "NO_TESTS_COLLECTED": "NoTestsCollected",
    "GENERATED_TEST_INVALID": "GeneratedTestInvalid",
    "LAUNCH_FAILED": "LaunchFailed",
    "TESTS_DESELECTED": "TestsDeselected",
    "NONZERO_EXIT_GREEN_REPORT": "NonzeroExitGreenReport",
    "ARTIFACT_PERSISTENCE_FAILED": "ArtifactPersistenceFailed",
}
# Process outcomes that prove the reviewed tests never ran: the attempt is
# NOT_RUNNABLE (cleanup or regeneration is safe), never an ambiguous UNKNOWN.
NOT_RUNNABLE_PROCESS_KINDS = frozenset({"GENERATED_TEST_INVALID", "LAUNCH_FAILED", "TESTS_DESELECTED"})
_NOT_RUNNABLE_MESSAGES = {
    "GENERATED_TEST_INVALID": "Generated tests failed the compile/collect gate or did not compile or import in the run.",
    "LAUNCH_FAILED": "The test process could not be launched.",
    "TESTS_DESELECTED": "The explicitly selected tests were deselected by the project's pytest configuration or environment.",
}
STOP_PROOFS = frozenset({
    "WINDOWS_JOB_OBJECT", "WINDOWS_TASKKILL_TREE", "POSIX_PROCESS_GROUP", "WINDOWS_JOB_TERMINATED",
})
# Variables that change what the test process does.  Only their names and value
# digests are recorded; the values themselves never reach a receipt or a log.
LAUNCH_ENVIRONMENT_NAMES = (
    "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "MAVEN_OPTS", "MAVEN_ARGS",
    "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GRADLE_OPTS", "JAVA_HOME",
)
_SCOPE_MARKER_NAME = "TEST_SKILLS_PROCESS_SCOPE"


def launch_environment_inputs(environment: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Name and digest every launch-significant variable that is set; never its value."""
    source = os.environ if environment is None else environment
    rows: list[dict[str, str]] = []
    for name in sorted(LAUNCH_ENVIRONMENT_NAMES):
        value = source.get(name)
        if isinstance(value, str):
            digest = hashlib.sha256(value.encode("utf-8", errors="surrogateescape")).hexdigest()
            rows.append({"name": name, "value_digest": "sha256:" + digest})
    return rows


class DurableNativeReportError(ValueError):
    """A framework report cannot be preserved inside the durable evidence bound."""


@dataclass(frozen=True)
class RunnerCompatibility:
    status: Literal["READY", "NOT_RUNNABLE"]
    required_pairs: tuple[tuple[str, str], ...]
    bindings: Mapping[tuple[str, str], Mapping[str, Any]]
    verified_file_digests: Mapping[str, str]
    diagnostics: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    stdout: str
    stderr: str
    kind: Literal["EXIT", "TIMEOUT", "OS_ERROR"] = "EXIT"
    process_scope_stopped: bool = False
    stop_proof: Literal["WINDOWS_JOB_OBJECT", "WINDOWS_TASKKILL_TREE", "POSIX_PROCESS_GROUP", "WINDOWS_JOB_TERMINATED"] | None = None
    _stop_attestation: object | None = None

    def __iter__(self):
        yield self.exit_code
        yield self.stdout
        yield self.stderr


def _process_stop_fingerprint(row: Mapping[str, Any]) -> str:
    return json.dumps(dict(row), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def consume_controller_process_stop_proof(report: Mapping[str, Any]) -> bool:
    """Consume the in-process proof minted by a real timed-out subprocess.

    The portable receipt keeps only public evidence.  Its ability to unlock a
    retry is therefore gated here, before PROCESS_STOPPED is persisted.
    """
    rows = report.get("process_evidence")
    if not isinstance(rows, list):
        return False
    stopped = [row for row in rows if isinstance(row, Mapping) and row.get("process_scope_stopped") is True]
    if len(stopped) != 1:
        return False
    row = stopped[0]
    if validate_process_evidence(
        rows, report.get("run_id"), report.get("source", {}), report.get("exit_code"),
        report.get("stats", {}).get("duration_sec") if isinstance(report.get("stats"), Mapping) else None,
        report.get("target"),
    ):
        return False
    fingerprint = _process_stop_fingerprint(row)
    if fingerprint not in _CONTROLLER_STOP_ROWS:
        return False
    _CONTROLLER_STOP_ROWS.remove(fingerprint)
    return True


def resolve_execution_context(
    project_root: Path,
    skillsrc_path: Path,
    module_id: str | None,
    language_override: str | None,
) -> tuple[Path, str, dict[str, Any]]:
    """Resolve one manifest module into a project-confined execution context."""
    return resolve_execution_context_document(project_root, load_skillsrc(skillsrc_path), module_id, language_override)


def resolve_execution_context_document(
    project_root: Path,
    document: Mapping[str, Any],
    module_id: str | None,
    language_override: str | None,
) -> tuple[Path, str, dict[str, Any]]:
    """Resolve an already validated manifest without reading it again."""
    document = normalize_skillsrc(document)
    module = select_module(document, module_id)
    execution_root = resolve_module_root(project_root, module)
    detected_language = module["stack"]["language"]
    if language_override and language_override != detected_language:
        raise SkillsrcError("language_conflict", "--language conflicts with selected module")
    return execution_root, language_override or detected_language, module


class RunnerInputError(ValueError):
    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]] | None = None) -> None:
        object.__setattr__(self, "code", code)
        rows = diagnostics or ({"path": "/artifacts/automation_status", "code": code, "message": "Automation artifact is not directly runnable."},)
        object.__setattr__(self, "diagnostics", tuple(MappingProxyType(dict(row)) for row in rows))
        super().__init__(code)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"code", "diagnostics"} and hasattr(self, name):
            raise AttributeError("RunnerInputError is immutable")
        super().__setattr__(name, value)


def _diag(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _compat(status: Literal["READY", "NOT_RUNNABLE"], pairs: Sequence[tuple[str, str]] = (),
            bindings: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
            digests: Mapping[str, str] | None = None, diagnostics: Sequence[Mapping[str, str]] = ()) -> RunnerCompatibility:
    return RunnerCompatibility(status, tuple(sorted(pairs)), MappingProxyType({key: _freeze(value) for key, value in (bindings or {}).items()}), MappingProxyType(dict(digests or {})), tuple(MappingProxyType(dict(row)) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"]))))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def load_automation_artifact(path: Path) -> dict[str, Any]:
    from tools.schema_validation import classify_version, load_json_strict, schema_diagnostics
    artifact = load_json_strict(path)
    if classify_version(artifact)["code"] == "V2_1_BREAKING_CHANGE":
        raise RunnerInputError("V2_1_BREAKING_CHANGE")
    diagnostics = schema_diagnostics(artifact, _AUTOMATION_SCHEMA, _ROOT)
    if diagnostics:
        raise ValueError(f"{diagnostics[0]['code']}: {diagnostics[0]['path']}")
    return artifact


def load_autotest_review_artifact(path: Path) -> dict[str, Any]:
    from tools.schema_validation import classify_version, load_json_strict, schema_diagnostics
    artifact = load_json_strict(path)
    if classify_version(artifact)["code"] == "V2_1_BREAKING_CHANGE":
        raise RunnerInputError("V2_1_BREAKING_CHANGE")
    diagnostics = schema_diagnostics(artifact, _AUTOTEST_REVIEW_SCHEMA, _ROOT)
    if diagnostics:
        raise RunnerInputError("RUNNER_AUTOTEST_REVIEW", diagnostics)
    return artifact


def _path(root: Path, raw_path: str) -> Path | None:
    raw = Path(raw_path)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    try:
        value = (root / raw).resolve(strict=True)
        value.relative_to(root)
    except (OSError, ValueError):
        return None
    return value if value.is_file() else None


def _python_node(tree: ast.Module, locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] == "python_module_function":
        found = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == locator["function_name"]]
        return locator["function_name"] if len(found) == 1 else None
    if locator["kind"] != "python_class_method":
        return None
    members: list[ast.stmt] = list(tree.body)
    for name in locator["qualified_class_name"].split("."):
        classes = [node for node in members if isinstance(node, ast.ClassDef) and node.name == name]
        if len(classes) != 1:
            return None
        members = list(classes[0].body)
    found = [node for node in members if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == locator["method_name"]]
    return "::".join([*locator["qualified_class_name"].split("."), locator["method_name"]]) if len(found) == 1 else None


def _java_class(path: Path, text: str) -> tuple[str, str] | None:
    masked = _mask_java_noncode(text)
    package = re.search(r"^\s*package\s+([\w$]+(?:\.[\w$]+)*)\s*;", masked, re.MULTILINE)
    class_fqn = (package.group(1) + "." if package else "") + path.stem
    class_name = path.stem
    if len(re.findall(r"\bclass\s+" + re.escape(class_name) + r"\b", masked)) != 1:
        return None
    declaration = re.search(r"\bclass\s+" + re.escape(class_name) + r"\b[^\{]*\{", masked)
    if declaration is None:
        return None
    depth, end = 0, None
    for index in range(declaration.end() - 1, len(masked)):
        if masked[index] == "{": depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return None
    body = list(masked[declaration.end():end])
    nested = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_$][\w$]*\b[^\{]*\{")
    search_at = 0
    while (match := nested.search("".join(body), search_at)) is not None:
        nested_depth, nested_end = 0, None
        for index in range(match.end() - 1, len(body)):
            if body[index] == "{": nested_depth += 1
            elif body[index] == "}":
                nested_depth -= 1
                if nested_depth == 0:
                    nested_end = index + 1
                    break
        if nested_end is None:
            return None
        body[match.start():nested_end] = " " * (nested_end - match.start())
        search_at = nested_end
    return class_fqn, "".join(body)


def _java_node(parsed: tuple[str, str], locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] != "java_class_method" or parsed[0] != locator["class_fqn"]:
        return None
    method = locator["method_name"]
    # Start at a token, not every space left by masked literals and comments.
    pattern = r"\b(?=\w)(?:public|protected|private)?\s*(?:static\s+)?[\w<>\[\]., ?]+\s+" + re.escape(method) + r"\s*\([^;{}]*\)\s*(?:throws[^\{]+)?\{"
    return method if len(re.findall(pattern, parsed[1])) == 1 else None


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


def validate_artifact_runner_compatibility(project: Path, language: Literal["python", "java"], document: Mapping[str, Any], automation_artifact: Mapping[str, Any], *, materialized: bool = True) -> RunnerCompatibility:
    """Check inline proposals before review, or exact on-disk bytes before execution.

    ``materialized=False`` never reads or writes generated files and proves only
    static compatibility. Execution callers keep the default on-disk verification.
    Source parsing is shared and bounded to this invocation, once per file.
    """
    from tools.automation_validation import AutomationArtifactError, required_symbol_pairs
    from tools.canonical_document import validate_canonical_document
    canonical = validate_canonical_document(dict(document))
    if canonical:
        return _compat("NOT_RUNNABLE", diagnostics=[_diag(row["path"], row["code"], "Canonical document is invalid.") for row in canonical])
    try:
        pairs = required_symbol_pairs(dict(automation_artifact), dict(document))
    except AutomationArtifactError as error:
        return _compat("NOT_RUNNABLE", diagnostics=error.diagnostics)
    root, diagnostics, physical, paths, digests, parsed = project.resolve(), [], set(), {}, {}, {}
    expected = (language, "pytest" if language == "python" else "junit5")
    files = automation_artifact["artifacts"]["generated_files"]
    file_map = {row["file_id"]: row for row in files}
    symbols = automation_artifact["artifacts"]["generated_symbols"]
    for index, row in enumerate(files):
        file = _path(root, row["path"]) if materialized else root / row["path"]
        pointer = f"/artifacts/generated_files/{index}"
        if file is None or file in physical:
            diagnostics.append(_diag(pointer + "/path", "RUNNER_FILE_CONFINEMENT", "Generated file is unavailable or escapes the project.")); continue
        physical.add(file)
        try:
            content = file.read_bytes() if materialized else row["content"].encode("utf-8")
        except OSError:
            diagnostics.append(_diag(pointer + "/path", "RUNNER_FILE_CONFINEMENT", "Generated file is unavailable or escapes the project.")); continue
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual != row["content_digest"]: diagnostics.append(_diag(pointer + "/content_digest", "RUNNER_FILE_DIGEST", "Generated file bytes do not match the artifact digest."))
        if (row["language"], row["framework"]) != expected: diagnostics.append(_diag(pointer, "RUNNER_LANGUAGE_FRAMEWORK", "Generated file is incompatible with the requested runner."))
        paths[row["file_id"]], digests[row["file_id"]] = file, actual
        try:
            text = content.decode("utf-8")
            parsed[row["file_id"]] = ast.parse(text, filename=str(file)) if language == "python" else _java_class(file, text)
        except (UnicodeDecodeError, SyntaxError):
            parsed[row["file_id"]] = None
    symbol_map = {(row["file_id"], row["symbol_id"]): (index, row) for index, row in enumerate(symbols)}
    bindings: dict[tuple[str, str], Mapping[str, Any]] = {}
    for pair in sorted(pairs):
        index, symbol = symbol_map.get(pair, (-1, None)); file = paths.get(pair[0])
        source = parsed.get(pair[0])
        node = None if symbol is None or source is None else (_python_node(source, symbol["locator"]) if language == "python" else _java_node(source, symbol["locator"]))
        if node is None:
            diagnostics.append(_diag(f"/artifacts/generated_symbols/{index}/locator", "RUNNER_LOCATOR", "Generated locator is absent or ambiguous.")); continue
        bindings[pair] = {"path": file, "relative_path": file_map[pair[0]]["path"], "locator": symbol["locator"], "node": node}
    return _compat("NOT_RUNNABLE", diagnostics=diagnostics) if diagnostics else _compat("READY", pairs, bindings, digests)


def _source_changed_file_ids(process_evidence: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        file_id
        for row in process_evidence
        if row.get("kind") == "SOURCE_CHANGED" and isinstance(row.get("affected_file_ids"), list)
        for file_id in row["affected_file_ids"]
        if isinstance(file_id, str)
    }


def validate_process_evidence(process_evidence: Sequence[Mapping[str, Any]], run_id: str | None, source: Mapping[str, Any], exit_code: int | None, duration_sec: float | None, target: Mapping[str, Any] | None, prefix: str = "RUNNER", required_file_ids: Sequence[str] = ()) -> list[dict[str, str]]:
    """Validate one process outcome against its enclosing, authoritative run metadata."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    rows: list[dict[str, str]] = []
    profiles = {"pytest": (PYTEST,), "maven": (MAVEN, SYSTEM_MAVEN), "gradle": (GRADLE,)}
    known_file_ids = set(required_file_ids)
    source_changed_rows = 0
    ordinary_rows = 0
    for row in process_evidence:
        if row.get("run_id") != run_id: rows.append(_diag("/process_evidence", f"{prefix}_STALE_PROCESS_EVIDENCE", "Process evidence must belong to the current run."))
        if row.get("source_digest") != source.get("source_digest"): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_EVIDENCE_SOURCE", "Process evidence source digest does not match."))
        kind = row.get("kind")
        if kind != "SOURCE_CHANGED":
            ordinary_rows += 1
        if _PROCESS_ERROR_CLASSES.get(kind) != row.get("error_class"): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_CLASS", "Process kind and error class must match."))
        scope_stopped = row.get("process_scope_stopped")
        stop_proof = row.get("stop_proof")
        if (
            (scope_stopped is not None or stop_proof is not None)
            and not (
                kind == "TIMEOUT"
                and scope_stopped is True
                and stop_proof in STOP_PROOFS
            )
        ):
            rows.append(_diag(
                "/process_evidence", f"{prefix}_PROCESS_STOP_PROOF",
                "Process-stop proof must be a controller-confirmed timeout scope.",
            ))
        cause = row.get("exit_cause")
        if kind == "TIMEOUT":
            exit_valid = type(exit_code) is int and exit_code == 124 and cause == "TIMEOUT"
        elif kind in {"OS_ERROR", "LAUNCH_FAILED"}:
            exit_valid = type(exit_code) is int and exit_code == 127 and cause == "OS_ERROR"
        elif kind == "GENERATED_TEST_INVALID":
            exit_valid = type(exit_code) is int and exit_code > 0 and cause == "NONZERO_EXIT"
        elif kind == "NONZERO_EXIT_GREEN_REPORT":
            exit_valid = type(exit_code) is int and exit_code > 0 and cause == "NONZERO_EXIT"
        elif kind in {"NO_TESTS_COLLECTED", "TESTS_DESELECTED"}:
            exit_valid = type(exit_code) is int and exit_code >= 0 and (
                (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code > 0 and cause == "NONZERO_EXIT")
            )
        elif kind in {"COLLECTION_ERROR", "EXECUTION_ERROR"}:
            exit_valid = type(exit_code) is int and exit_code > 0 and cause == "NONZERO_EXIT"
        elif kind in {"JUNIT_MISSING", "JUNIT_INVALID"}:
            exit_valid = (type(exit_code) is int and exit_code == 0 and cause == "ZERO_EXIT") or (type(exit_code) is int and exit_code != 0 and cause == "NONZERO_EXIT")
        elif kind == "SOURCE_CHANGED":
            exit_valid = type(exit_code) is int and (
                (exit_code == 124 and cause == "TIMEOUT")
                or (exit_code == 127 and cause == "OS_ERROR")
                or (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code != 0 and cause == "NONZERO_EXIT")
            )
            source_changed_rows += 1
            affected = row.get("affected_file_ids")
            if not isinstance(affected, list) or not affected or any(not isinstance(file_id, str) for file_id in affected) or len(set(affected)) != len(affected) or (known_file_ids and not set(affected).issubset(known_file_ids)):
                rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_SOURCE_CHANGED", "Source change evidence must name distinct executed generated files."))
        elif kind in {"BASELINE_DRIFT", "ARTIFACT_PERSISTENCE_FAILED"}:
            exit_valid = type(exit_code) is int and (
                (exit_code == 124 and cause == "TIMEOUT")
                or (exit_code == 127 and cause == "OS_ERROR")
                or (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code != 0 and cause == "NONZERO_EXIT")
            )
        else:
            exit_valid = False
        if not exit_valid:
            rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_EXIT", "Process cause must match the enclosing exit code."))
        if not isinstance(target, Mapping) or row.get("command_profile") != target.get("command") or row.get("command_profile") not in profiles.get(target.get("runner"), ()): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_PROFILE", "Process profile must match the enclosing target."))
        if row.get("duration_sec") != duration_sec: rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_DURATION", "Process duration must match the enclosing run."))
        if row.get("stdout_tail") != "" or row.get("stderr_tail") != "": rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_OUTPUT", "Process output tails must be empty."))
    if source_changed_rows > 1:
        rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_SOURCE_CHANGED", "A run may record source mutation once."))
    if ordinary_rows > 1:
        rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_CARDINALITY", "A run may record one runner outcome and one source mutation at most."))
    return rows


def _not_runnable_process_kind(evidence: Sequence[Mapping[str, Any]], process_evidence: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the one process kind that makes a started execution NOT_RUNNABLE."""
    if evidence or len(process_evidence) != 1:
        return None
    kind = process_evidence[0].get("kind") if isinstance(process_evidence[0], Mapping) else None
    return kind if kind in NOT_RUNNABLE_PROCESS_KINDS else None


def validate_execution_evidence(verdict: str, run_id: str | None, source: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], authoritative: bool, required_pairs: Sequence[tuple[str, str]], verified_file_digests: Mapping[str, str] | None = None, exit_code: int | None = None, stats: Mapping[str, Any] | None = None, reported_diagnostics: Sequence[Mapping[str, Any]] | None = None, process_evidence: Sequence[Mapping[str, Any]] = (), target: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    required, diagnostics, seen = set(required_pairs), [], {}
    if verdict == "NOT_RUNNABLE" and run_id is None:
        return [_diag("/execution_evidence", "RUNNER_PRESTART_EVIDENCE", "Pre-start NOT_RUNNABLE cannot contain evidence.")] if evidence or process_evidence or authoritative else []
    factual_diagnostics = []
    if verdict == "UNKNOWN" and run_id is not None and not evidence and not process_evidence:
        factual_diagnostics.append(_diag(
            "/execution", "EXECUTION_RESULT_LOST",
            "Execution started, but no authoritative framework or process result was durably recovered.",
        ))
    not_runnable_kind = _not_runnable_process_kind(evidence, process_evidence)
    if verdict == "NOT_RUNNABLE" and not_runnable_kind is not None:
        factual_diagnostics.append(_diag("/execution", not_runnable_kind, _NOT_RUNNABLE_MESSAGES[not_runnable_kind]))
    source_changed = _source_changed_file_ids(process_evidence)
    for row in evidence:
        if row.get("run_id") != run_id:
            diagnostics.append(_diag("/execution_evidence", "RUNNER_STALE_EVIDENCE", "Evidence must belong to the current run.")); continue
        pair = row.get("file_id"), row.get("symbol_id")
        if pair not in required: diagnostics.append(_diag("/execution_evidence", "RUNNER_FOREIGN_EVIDENCE", "Current evidence names a non-required pair.")); continue
        if row.get("source_digest") != source.get("source_digest"): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_SOURCE", "Current evidence source digest does not match.")); continue
        if not verified_file_digests or row.get("file_digest") != verified_file_digests.get(pair[0]): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_FILE_DIGEST", "Current evidence file digest is not verified.")); continue
        if pair[0] in source_changed: diagnostics.append(_diag("/execution_evidence", "RUNNER_SOURCE_CHANGED_EVIDENCE", "Source-changed files cannot retain execution evidence.")); continue
        seen.setdefault(pair, []).append(row)
    if any(len(rows) != 1 for rows in seen.values()): diagnostics.append(_diag("/execution_evidence", "RUNNER_DUPLICATE_EVIDENCE", "A required pair has duplicate or conflicting current evidence."))
    statuses = {pair: rows[0]["status"] for pair, rows in seen.items() if len(rows) == 1}
    diagnostics.extend(validate_process_evidence(process_evidence, run_id, source, exit_code, None if stats is None else stats.get("duration_sec"), target, required_file_ids=tuple(file_id for file_id, _ in required_pairs)))
    if verdict == "PASS" and exit_code is not None and exit_code != 0:
        diagnostics.append(_diag("/exit_code", "RUNNER_EXIT_CODE_VERDICT", "PASS requires a zero process exit code."))
    if verdict == "FAIL" and run_id is not None and not any(value in {"FAILED", "ERROR"} for value in statuses.values()) and not process_evidence:
        diagnostics.append(_diag("/execution_evidence", "RUNNER_FAIL_EVIDENCE", "FAIL requires FAILED or ERROR current evidence."))
    complete = (
        not diagnostics
        and not process_evidence
        and len(statuses) == len(required)
        and "SKIPPED" not in statuses.values()
    )
    zero_collect = (
        not diagnostics
        and not evidence
        and len(process_evidence) == 1
        and process_evidence[0].get("kind") == "NO_TESTS_COLLECTED"
        and isinstance(stats, Mapping)
        and all(stats.get(key) == 0 for key in ("total", "passed", "failed", "errors", "skipped"))
    )
    if zero_collect or complete and any(value in {"FAILED", "ERROR"} for value in statuses.values()):
        derived = "FAIL"
    elif complete and exit_code in (None, 0):
        derived = "PASS"
    elif not diagnostics and not_runnable_kind is not None:
        # The process ran, but the reviewed tests provably did not: there is
        # nothing ambiguous to preserve, so this is not UNKNOWN.
        derived = "NOT_RUNNABLE"
    else:
        # Once execution has started, incomplete or process-only evidence proves
        # neither product success nor exact-test failure.  The frozen contract
        # therefore classifies it as UNKNOWN.
        derived = "UNKNOWN"
    if verdict != derived: diagnostics.append(_diag("/verdict", "RUNNER_EVIDENCE_VERDICT", "Verdict does not match current execution evidence."))
    if authoritative != (complete or zero_collect): diagnostics.append(_diag("/evidence_authoritative", "RUNNER_EVIDENCE_AUTHORITATIVE", "Authoritative flag does not match current execution evidence."))
    if run_id is not None and stats is not None:
        current = [row for row in evidence if row.get("run_id") == run_id]
        expected_stats = {"total": len(current), **{name: sum(row.get("status") == status for row in current) for name, status in (("passed", "PASSED"), ("failed", "FAILED"), ("errors", "ERROR"), ("skipped", "SKIPPED"))}}
        if any(stats.get(name) != value for name, value in expected_stats.items()):
            diagnostics.append(_diag("/stats", "RUNNER_STATS", "Stats must exactly equal current execution evidence."))
    if run_id is not None and reported_diagnostics is not None:
        expected_rows = sorted(
            (dict(row) for row in [*diagnostics, *factual_diagnostics]),
            key=lambda row: (row["path"], row["code"], row["message"]),
        )
        actual_rows = [dict(row) for row in reported_diagnostics]
        if actual_rows != expected_rows:
            diagnostics.append(_diag("/diagnostics", "RUNNER_DIAGNOSTICS", "Diagnostics must exactly equal recomputed execution diagnostics."))
    return diagnostics


def run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None = None, *, timeout: int = _TIMEOUT) -> ProcessOutcome:
    # ponytail: temporary files bound RAM; add a disk quota if noisy runners require it.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        return _run_subprocess(command, project, environment, timeout, stdout_file, stderr_file)


def _windows_console_encodings() -> tuple[str, ...]:
    """Code pages a Windows console child writes when its output is not UTF-8."""
    if os.name != "nt":
        return ()
    names: list[str] = []
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for reader in (kernel32.GetConsoleOutputCP, kernel32.GetOEMCP):
            code_page = int(reader())
            if code_page and code_page != 65001 and f"cp{code_page}" not in names:
                names.append(f"cp{code_page}")
    except (AttributeError, OSError, ValueError):
        pass
    return tuple(names)


def _decode_process_output(data: bytes, fallback_encodings: Sequence[str] | None = None) -> str:
    """Decode as UTF-8, then as the Windows console code page, never failing."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in (_windows_console_encodings() if fallback_encodings is None else fallback_encodings):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _output_tail(stream: Any) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - PROCESS_OUTPUT_TAIL_BYTES))
    data = stream.read(PROCESS_OUTPUT_TAIL_BYTES)
    if size > PROCESS_OUTPUT_TAIL_BYTES:
        # Never publish the suffix of a truncated credential-bearing line.
        data = data.partition(b"\n")[2]
    return _decode_process_output(data)


def _run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None, timeout: int, stdout_file: Any, stderr_file: Any) -> ProcessOutcome:
    scope_marker = uuid.uuid4().hex
    child_environment = dict(environment) if environment is not None else dict(os.environ)
    # The marker is inherited by every descendant, including one that leaves the
    # process group with setsid, so a POSIX stop proof can account for it.
    child_environment[_SCOPE_MARKER_NAME] = scope_marker
    kwargs: dict[str, Any] = {
        "cwd": project,
        "stdout": stdout_file,
        "stderr": stderr_file,
        "env": child_environment,
        "shell": False,
    }
    windows_job: int | None = None
    try:
        if os.name == "nt":
            process, windows_job = _start_windows_scoped_process(command, kwargs)
        else:
            kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)
    except OSError:
        return ProcessOutcome(127, "", "", "OS_ERROR")
    try:
        process.wait(timeout=timeout)
        return ProcessOutcome(process.returncode, _output_tail(stdout_file), _output_tail(stderr_file))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            stop_proof = _stop_windows_scope(process, windows_job)
            windows_job = None
        else:
            stop_proof = _stop_posix_scope(process, scope_marker)
        return ProcessOutcome(
            124, _output_tail(stdout_file), _output_tail(stderr_file), "TIMEOUT",
            process_scope_stopped=stop_proof is not None, stop_proof=stop_proof,
            _stop_attestation=_PROCESS_STOP_ATTESTATION if stop_proof is not None else None,
        )
    finally:
        if windows_job is not None:
            _close_windows_handle(windows_job)


# --- POSIX process scope -----------------------------------------------------

_STOP_WAIT_SECONDS = 5.0
_STOP_POLL_SECONDS = 0.05


def _posix_process_table(scope_marker: str | None = None) -> list[tuple[int, int, int, bool]] | None:
    """Return ``(pid, ppid, pgid, carries_marker)`` for every live process.

    Zombies are not live.  ``None`` means the host cannot enumerate processes, in
    which case no stop proof may be issued.
    """
    proc = Path("/proc")
    rows: list[tuple[int, int, int, bool]] = []
    if (proc / "self" / "stat").is_file():
        needle = f"{_SCOPE_MARKER_NAME}={scope_marker}".encode("ascii") if scope_marker else None
        try:
            entries = [entry for entry in os.listdir(proc) if entry.isdigit()]
        except OSError:
            return None
        for entry in entries:
            try:
                stat = (proc / entry / "stat").read_bytes().decode("ascii", errors="replace")
                fields = stat.rpartition(")")[2].split()
                state, ppid, pgid = fields[0], int(fields[1]), int(fields[2])
            except (OSError, ValueError, IndexError):
                continue  # the process exited while the table was being read
            if state in {"Z", "X", "x"}:
                continue
            marked = False
            if needle is not None:
                try:
                    marked = needle in (proc / entry / "environ").read_bytes().split(b"\0")
                except OSError:
                    marked = False
            rows.append((int(entry), ppid, pgid, marked))
        return rows
    try:
        listing = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    for line in listing.stdout.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            continue
        if len(parts) > 3 and parts[3].startswith("Z"):
            continue
        rows.append((pid, ppid, pgid, False))
    return rows


def _posix_scope_pids(table: Sequence[tuple[int, int, int, bool]], pgid: int, known: Sequence[int] = ()) -> set[int]:
    """Live members of the launched scope: the group, marked processes, and their descendants."""
    remembered = set(known)
    scope = {pid for pid, _ppid, group, marked in table if group == pgid or marked or pid in remembered}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _group, _marked in table:
            if ppid in scope and pid not in scope:
                scope.add(pid)
                changed = True
    scope.discard(os.getpid())
    return scope


def _stop_posix_scope(process: Any, scope_marker: str) -> Literal["POSIX_PROCESS_GROUP"] | None:
    """Kill the launched scope and prove that nothing of it is still alive.

    The process group alone is not the scope: a daemon that called ``setsid``
    leaves the group and keeps running.  The proof is issued only after a fresh
    process table shows no live member of the group, no process carrying this
    launch's marker, and no descendant of either.
    """
    pgid = process.pid
    seen: set[int] = set()
    table = _posix_process_table(scope_marker)
    enumerable = table is not None
    if table is not None:
        seen |= _posix_scope_pids(table, pgid)
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while True:
        try:
            os.killpg(pgid, 9)
        except OSError:
            pass
        for pid in sorted(seen):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        try:
            process.wait(timeout=_STOP_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            return None
        table = _posix_process_table(scope_marker)
        if table is None:
            enumerable = False
            break
        alive = _posix_scope_pids(table, pgid, tuple(seen))
        seen |= alive
        if not alive and process.poll() is not None:
            break
        if time.monotonic() >= deadline:
            return None
    if not enumerable or process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
        return None
    # Only unreaped zombies may remain in the group; they hold no resources
    # and cannot run, which is exactly what the process table just proved.
    return "POSIX_PROCESS_GROUP"


# --- Windows Job Object scope ------------------------------------------------
#
# The process is created suspended, assigned to a kill-on-close Job Object and
# only then resumed, so no child can exist outside the job.  Every kernel call
# lives in its own small function; tests replace them to exercise the proof
# logic on any host.

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_WAIT_OBJECT_0 = 0


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    return kernel32


def _close_windows_handle(handle: int) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().CloseHandle(handle))


def _windows_create_kill_job() -> int | None:
    """Create an anonymous Job Object whose processes die when its handle closes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return None
    return int(job)


def _windows_assign_job(job: int, process: Any) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().AssignProcessToJobObject(job, int(process._handle)))


def _assign_windows_kill_job(process: Any) -> int | None:
    """Put one still-suspended process in a new kill-on-close job; ``None`` if that failed."""
    job = _windows_create_kill_job()
    if job is None:
        return None
    if not _windows_assign_job(job, process):
        _close_windows_handle(job)
        return None
    return job


def _windows_resume_process(process: Any) -> bool:
    """Resume a process created with ``CREATE_SUSPENDED``.

    ``subprocess.Popen`` closes the primary thread handle, so the thread is
    found again through a Toolhelp thread snapshot filtered by the owning
    process ID and resumed with the documented ``OpenThread``/``ResumeThread``
    pair.  A suspended process has exactly its primary thread.  If no thread
    could be resumed that way, ``NtResumeProcess`` on the process handle is the
    fallback.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = _kernel32()
    invalid_handle = ctypes.c_void_p(-1).value
    failed = 0xFFFFFFFF
    resumed = 0
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot and snapshot != invalid_handle:
        try:
            kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
            kernel32.Thread32First.restype = wintypes.BOOL
            kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
            kernel32.Thread32Next.restype = wintypes.BOOL
            entry = ThreadEntry32()
            entry.dwSize = ctypes.sizeof(ThreadEntry32)
            more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if thread:
                        try:
                            if kernel32.ResumeThread(thread) != failed:
                                resumed += 1
                        finally:
                            kernel32.CloseHandle(thread)
                more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    if resumed:
        return True
    try:
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        return ntdll.NtResumeProcess(int(process._handle)) == 0
    except (AttributeError, OSError):
        return False


def _windows_terminate_job(job: int) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().TerminateJobObject(job, 1))


def _windows_job_active_processes(job: int) -> int | None:
    """Number of live processes in the job, or ``None`` when it cannot be queried."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    info = BasicAccountingInformation()
    if not _kernel32().QueryInformationJobObject(
        job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None,
    ):
        return None
    return int(info.ActiveProcesses)


def _windows_wait_process(process: Any, milliseconds: int) -> bool:
    if os.name != "nt":
        return False
    return _kernel32().WaitForSingleObject(int(process._handle), milliseconds) == _WAIT_OBJECT_0


def _start_windows_scoped_process(command: list[str], kwargs: Mapping[str, Any]) -> tuple[Any, int | None]:
    """Start suspended, contain in a Job Object, then resume.

    Returns the process and its job handle.  A failed job assignment still lets
    the process run, but without a job there can be no stop proof later.  A
    process that cannot be resumed is killed and reported as a launch failure.
    """
    options = dict(kwargs)
    options["creationflags"] = (
        int(options.get("creationflags", 0))
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | _CREATE_SUSPENDED
    )
    process = subprocess.Popen(command, **options)
    job = _assign_windows_kill_job(process)
    if not _windows_resume_process(process):
        if job is not None:
            _windows_terminate_job(job)
            _close_windows_handle(job)
        try:
            process.kill()
            process.wait(timeout=_STOP_WAIT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise OSError("suspended test process could not be resumed")
    return process, job


def _stop_windows_scope(process: Any, job: int | None) -> Literal["WINDOWS_JOB_TERMINATED"] | None:
    """Terminate the whole job and prove it is empty; without a job only best-effort kill."""
    if job is None:
        _terminate_process_tree(process)
        return None
    proof: Literal["WINDOWS_JOB_TERMINATED"] | None = None
    try:
        terminated = _windows_terminate_job(job)
        _windows_wait_process(process, int(_STOP_WAIT_SECONDS * 1000))
        try:
            process.wait(timeout=_STOP_WAIT_SECONDS)
            exited = True
        except (OSError, subprocess.TimeoutExpired):
            exited = False
        active: int | None = None
        deadline = time.monotonic() + _STOP_WAIT_SECONDS
        while True:
            active = _windows_job_active_processes(job)
            if active is None or active == 0 or time.monotonic() >= deadline:
                break
            time.sleep(_STOP_POLL_SECONDS)
        if terminated and exited and active == 0:
            proof = "WINDOWS_JOB_TERMINATED"
    finally:
        _close_windows_handle(job)  # KILL_ON_JOB_CLOSE reaps anything still inside
    if proof is None:
        _terminate_process_tree(process)
    return proof


def _terminate_process_tree(process: Any) -> None:
    """Best-effort kill without any proof (used when no Job Object contains the tree)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


@dataclass(frozen=True)
class JUnitCase:
    classname: str
    name: str
    file: str | None
    status: str

    @property
    def identity(self) -> tuple[str, str, str | None]:
        return (self.classname, self.name, self.file)


@dataclass(frozen=True)
class JUnitParseResult:
    cases: tuple[JUnitCase, ...]
    malformed: tuple[str, ...]
    duplicate_identities: tuple[tuple[str, str, str | None], ...]


def _norm_junit_file(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value.replace("\\", "/")


def _testcase_status(item: ET.Element) -> str:
    if item.find("failure") is not None:
        return "FAILED"
    if item.find("error") is not None:
        return "ERROR"
    if item.find("skipped") is not None:
        return "SKIPPED"
    return "PASSED"


def parse_junit_element(root: ET.Element) -> JUnitParseResult:
    cases: list[JUnitCase] = []
    malformed: list[str] = []
    seen: dict[tuple[str, str, str | None], int] = {}
    duplicates: list[tuple[str, str, str | None]] = []
    for item in root.iter("testcase"):
        name = item.attrib.get("name")
        if not isinstance(name, str) or not name:
            malformed.append("missing testcase name")
            continue
        classname = item.attrib.get("classname", "")
        row = JUnitCase(classname, name, _norm_junit_file(item.attrib.get("file")), _testcase_status(item))
        identity = row.identity
        count = seen.get(identity, 0) + 1
        seen[identity] = count
        if count == 2:
            duplicates.append(identity)
        cases.append(row)
    return JUnitParseResult(tuple(cases), tuple(malformed), tuple(duplicates))


def parse_junit_bytes(data: bytes) -> JUnitParseResult:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        return JUnitParseResult((), (str(error),), ())
    return parse_junit_element(root)


def _case_matches_pair(case: JUnitCase, binding: Mapping[str, Any], java: bool) -> bool:
    loc = binding["locator"]
    if java:
        base_name = case.name.split("[", 1)[0].split("(", 1)[0]
        return case.classname == loc["class_fqn"] and base_name == loc["method_name"]
    return _pytest_case_rootdir_prefix(case, binding) is not None


def _pytest_rootdir_prefixes(binding: Mapping[str, Any]) -> tuple[str, ...]:
    """Module-root paths relative to every directory pytest may pick as rootdir.

    pytest reports node IDs relative to its rootdir, which is the launch
    directory or one of its ancestors (the nearest one holding the effective
    ``pytest.ini``/``pyproject.toml``/``tox.ini``/``setup.cfg``).  The empty
    prefix is the module root itself.
    """
    relative = str(binding.get("relative_path") or "").replace("\\", "/")
    raw_path = binding.get("path")
    if not relative or raw_path is None:
        return ("",)
    file_parts = Path(raw_path).parts
    depth = len([part for part in relative.split("/") if part])
    module_parts = file_parts[:len(file_parts) - depth]
    if module_parts and Path(module_parts[0]).anchor == module_parts[0]:
        module_parts = module_parts[1:]
    return ("",) + tuple("/".join(module_parts[-count:]) for count in range(1, len(module_parts) + 1))


def _pytest_case_rootdir_prefix(case: JUnitCase, binding: Mapping[str, Any]) -> str | None:
    """Return the rootdir prefix under which this testcase is the bound symbol, if any."""
    loc = binding["locator"]
    base_name = case.name.split("[", 1)[0]
    expected_name = loc["function_name"] if loc["kind"] == "python_module_function" else loc["method_name"]
    if base_name != expected_name:
        return None
    module_relative = str(binding.get("relative_path") or Path(binding["path"]).as_posix()).replace("\\", "/")
    for prefix in _pytest_rootdir_prefixes(binding):
        relative = f"{prefix}/{module_relative}" if prefix else module_relative
        expected_module = relative.removesuffix(".py").replace("/", ".")
        class_module = expected_module if loc["kind"] == "python_module_function" else expected_module + "." + loc["qualified_class_name"]
        if case.classname == class_module and case.file in {None, relative}:
            return prefix
    return None


def match_junit_cases(
    parsed: JUnitParseResult,
    compatibility: RunnerCompatibility,
    run_id: str,
    source: Mapping[str, Any],
    java: bool = False,
    *,
    strict: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    if parsed.malformed:
        errors.append("malformed JUnit testcase")
    if parsed.duplicate_identities:
        errors.append("duplicate JUnit testcase identity")
    observed: dict[tuple[str, str], list[str]] = {pair: [] for pair in compatibility.required_pairs}
    unmatched: list[JUnitCase] = []
    ambiguous = False
    rootdir_prefixes: set[str] = set()
    for case in parsed.cases:
        matches = [pair for pair, binding in compatibility.bindings.items() if _case_matches_pair(case, binding, java)]
        if len(matches) == 1:
            observed[matches[0]].append(case.status)
            if not java:
                rootdir_prefixes.add(str(_pytest_case_rootdir_prefix(case, compatibility.bindings[matches[0]])))
        elif len(matches) > 1:
            ambiguous = True
        else:
            unmatched.append(case)
    if ambiguous:
        errors.append("ambiguous JUnit testcase")
    if len(rootdir_prefixes) > 1:
        # One pytest process has one rootdir; mixed prefixes are not one run.
        errors.append("inconsistent pytest rootdir")
    if strict and unmatched:
        errors.append("unmatched JUnit testcase")
    if strict:
        missing = [pair for pair, values in observed.items() if not values]
        if missing:
            errors.append("missing required pair")
    severity = {"PASSED": 0, "SKIPPED": 1, "FAILED": 2, "ERROR": 3}
    rows = []
    for pair, values in observed.items():
        if values:
            rows.append(
                {
                    "run_id": run_id,
                    "source_digest": source["source_digest"],
                    "file_id": pair[0],
                    "symbol_id": pair[1],
                    "file_digest": compatibility.verified_file_digests[pair[0]],
                    "status": max(values, key=lambda value: severity[value]),
                }
            )
    return rows, errors


def _report_inventory(directory: Path) -> dict[Path, tuple[int, int, str]]:
    result: dict[Path, tuple[int, int, str]] = {}
    if directory.is_dir():
        candidates = directory.rglob("*.xml")
    elif directory.is_file():
        candidates = (directory,)
    else:
        candidates = ()
    for path in candidates:
        try:
            stat = path.stat()
            fingerprint = "oversized"
            if stat.st_size <= NATIVE_REPORT_MAX_BYTES:
                with path.open("rb") as stream:
                    fingerprint = hashlib.sha256(stream.read(NATIVE_REPORT_MAX_BYTES + 1)).hexdigest()
            result[path.resolve()] = (stat.st_mtime_ns, stat.st_size, fingerprint)
        except OSError:
            continue
    return result


def _report_snapshots(paths: Sequence[Path]) -> dict[Path, bytes]:
    """Read each fresh framework report once; all parsing and digests use these bytes."""
    snapshots: dict[Path, bytes] = {}
    total = 0
    for path in paths:
        try:
            with path.open("rb") as stream:
                content = stream.read(NATIVE_REPORT_MAX_BYTES + 1)
            total += len(content)
            if len(content) > NATIVE_REPORT_MAX_BYTES or total > NATIVE_REPORT_SET_MAX_BYTES:
                # An invalid sentinel rejects the whole report set, never a partial PASS.
                return {path.resolve(): b""}
            snapshots[path.resolve()] = content
        except OSError:
            continue
    return snapshots


def _normalize_durable_junit_report(content: bytes) -> bytes:
    """Keep only JUnit identity, counts, and outcome tags in canonical XML."""
    from tools.project_inventory import token_signature_rule

    if len(content) > NATIVE_REPORT_MAX_BYTES:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_LIMIT")
    try:
        source = ET.fromstring(content)
    except ET.ParseError as error:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_INVALID") from error
    if source.tag not in {"testsuite", "testsuites"}:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_INVALID")
    if any(
        token_signature_rule(item.get(key, "").encode("utf-8"))
        for item in source.iter() for key in ("name", "classname", "file", "tests", "failures", "errors", "skipped", "disabled")
    ):
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_SECRET")

    def normalized_suite(item: ET.Element) -> ET.Element:
        attributes = {
            key: item.attrib[key]
            for key in ("name", "tests", "failures", "errors", "skipped", "disabled")
            if key in item.attrib
        }
        result = ET.Element(item.tag, attributes)
        for child in item:
            if child.tag == "testsuite":
                result.append(normalized_suite(child))
            elif child.tag == "testcase":
                case_attributes = {
                    key: child.attrib[key]
                    for key in ("classname", "name", "file")
                    if key in child.attrib
                }
                case = ET.SubElement(result, "testcase", case_attributes)
                for status in ("failure", "error", "skipped"):
                    if child.find(status) is not None:
                        ET.SubElement(case, status)
        return result

    data = ET.tostring(
        normalized_suite(source), encoding="utf-8", xml_declaration=True,
        short_empty_elements=True,
    ) + b"\n"
    if len(data) > DURABLE_NATIVE_REPORT_MAX_BYTES:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_LIMIT")
    return data


def _durable_execution_artifacts(
    snapshots: Mapping[Path, bytes],
    module: Path,
    generated_delta_receipt: Mapping[str, Any],
    outcome: ProcessOutcome,
    run_root: Path | None,
    attempt_id: str | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Copy runner reports and limited output into the run before cleanup."""
    reports = _report_digest_evidence(snapshots, module)
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return reports, []
    from tools.pilot_state import _canonical_runner_output, publish_run_artifact_bytes

    artifacts: list[dict[str, str]] = []
    used_names: dict[str, int] = {}
    prepared_reports = [
        (report, path, _normalize_durable_junit_report(content))
        for report, (path, content) in zip(
            reports, sorted(snapshots.items(), key=lambda row: str(row[0])),
        )
    ]
    for report, path, content in prepared_reports:
        name = path.name or "report.xml"
        count = used_names.get(name, 0)
        used_names[name] = count + 1
        if count:
            stem = Path(name).stem
            suffix = Path(name).suffix
            name = f"{stem}-{count}{suffix}"
        published = publish_run_artifact_bytes(run_root, attempt_id, f"reports/{name}", content)
        artifacts.append({
            "kind": "native_report", "source_path": report["path"],
            "source_digest": report["digest"],
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": published["path"], "digest": published["digest"],
        })
    output = _canonical_runner_output(f"{outcome.stdout or ''}\n{outcome.stderr or ''}")
    if output:
        published = publish_run_artifact_bytes(run_root, attempt_id, "runner-output.txt", output)
        artifacts.append({
            "kind": "runner_output", "path": published["path"],
            "digest": published["digest"],
        })
    delta = generated_delta_receipt.get("delta")
    files = delta.get("files") if isinstance(delta, Mapping) else None
    for row in files if isinstance(files, list) else ():
        relative = row.get("path") if isinstance(row, Mapping) else None
        expected_digest = row.get("content_digest") if isinstance(row, Mapping) else None
        if not isinstance(relative, str) or row.get("materialization") != "MATERIALIZED":
            continue
        source = module / Path(relative)
        try:
            data = source.read_bytes()
        except OSError:
            continue
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if digest != expected_digest:
            continue
        published = publish_run_artifact_bytes(
            run_root, attempt_id, "generated/" + relative.replace("\\", "/"), data,
        )
        artifacts.append({
            "kind": "generated_test", "source_path": relative.replace("\\", "/"),
            "path": published["path"], "digest": published["digest"],
        })
    return reports, artifacts


def _report_digest_evidence(snapshots: Mapping[Path, bytes], module: Path) -> list[dict[str, str]]:
    """Record only fresh framework-report paths and digests, never report contents."""
    rows: list[dict[str, str]] = []
    for path, content in sorted(snapshots.items(), key=lambda row: str(row[0])):
        try:
            relative = path.resolve().relative_to(module.resolve()).as_posix()
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
        except ValueError:
            continue
        rows.append({"path": relative, "digest": digest})
    return rows


def _records_from_snapshots(snapshots: Mapping[Path, bytes], compatibility: RunnerCompatibility, run_id: str, source: Mapping[str, Any], *, java: bool) -> tuple[list[dict[str, str]], list[str]]:
    cases: list[JUnitCase] = []
    malformed: list[str] = []
    duplicates: list[tuple[str, str, str | None]] = []
    identities: set[tuple[str, str, str | None]] = set()
    for content in snapshots.values():
        try:
            parsed = parse_junit_element(ET.fromstring(content))
        except ET.ParseError:
            malformed.append("invalid JUnit report")
            continue
        for case in parsed.cases:
            if case.identity in identities:
                duplicates.append(case.identity)
            identities.add(case.identity)
            cases.append(case)
        malformed.extend(parsed.malformed)
    return match_junit_cases(
        JUnitParseResult(tuple(cases), tuple(malformed), tuple(duplicates)),
        compatibility, run_id, source, java, strict=True,
    )


def _post_run_file_digests(compatibility: RunnerCompatibility) -> Mapping[str, str]:
    """Re-read every executed generated file before accepting its evidence."""
    values: dict[str, str] = {}
    paths = {pair[0]: binding["path"] for pair, binding in compatibility.bindings.items()}
    for file_id, path in paths.items():
        try:
            values[file_id] = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            continue
    return MappingProxyType(values)


def _changed_file_ids(compatibility: RunnerCompatibility, post_run_digests: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(file_id for file_id, digest in compatibility.verified_file_digests.items() if post_run_digests.get(file_id) != digest))


def _outcome(value: Any) -> ProcessOutcome:
    if isinstance(value, ProcessOutcome):
        return value
    code, stdout, stderr = value
    return ProcessOutcome(code, stdout, stderr)


def _process_row(kind: str, outcome: ProcessOutcome, run_id: str, source: Mapping[str, Any], command_profile: str, duration_sec: float, affected_file_ids: Sequence[str] = ()) -> dict[str, Any]:
    row = {
        "run_id": run_id, "source_digest": source["source_digest"], "kind": kind,
        "error_class": _PROCESS_ERROR_CLASSES[kind],
        "exit_cause": "TIMEOUT" if outcome.kind == "TIMEOUT" else "OS_ERROR" if outcome.kind == "OS_ERROR" else "ZERO_EXIT" if outcome.exit_code == 0 else "NONZERO_EXIT",
        "command_profile": command_profile, "duration_sec": duration_sec,
        "stdout_tail": "", "stderr_tail": "",
    }
    if kind == "SOURCE_CHANGED":
        row["affected_file_ids"] = list(affected_file_ids)
    if (
        kind == "TIMEOUT"
        and outcome.process_scope_stopped
        and outcome.stop_proof is not None
        and outcome._stop_attestation is _PROCESS_STOP_ATTESTATION
    ):
        row["process_scope_stopped"] = True
        row["stop_proof"] = outcome.stop_proof
        _CONTROLLER_STOP_ROWS.add(_process_stop_fingerprint(row))
    return row


def _stats(evidence: Sequence[Mapping[str, Any]], duration_sec: float | None = None) -> dict[str, Any]:
    count = {value: sum(row["status"] == value for row in evidence) for value in ("PASSED", "FAILED", "ERROR", "SKIPPED")}
    return {"total": sum(count.values()), "passed": count["PASSED"], "failed": count["FAILED"], "errors": count["ERROR"], "skipped": count["SKIPPED"], "duration_sec": duration_sec}


def _execution_receipt(request: Any, report_evidence: Sequence[Mapping[str, str]], artifact_evidence: Sequence[Mapping[str, str]], *, run_root: Path | None = None, attempt_id: str | None = None, environment_inputs: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
    """Project only launch-safe request facts into a portable V5 execution receipt.

    ``environment_inputs`` are the names and value digests of the launch-significant
    variables the started process inherited; it is recorded only for a started run.
    """
    from tools.execution_adapters import ExecutionRequest, request_digest

    if not isinstance(request, ExecutionRequest):
        return {"adapter_id": None, "executable_path": None, "cwd": None, "run_id": None, "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None, "build_profile": None, "typed_parameters": {}, "argv": [], "selectors": [], "timeout_seconds": None, "report_paths": [], "request_digest": None, "report_evidence": [], "artifact_evidence": []}
    binding = {"run_id": None, "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None}
    if isinstance(run_root, Path) and isinstance(attempt_id, str):
        try:
            from tools.pilot_state import derive_state, read_attempt_receipt, read_run

            run = read_run(run_root)
            attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
            delta = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
            binding = {"run_id": run["manifest"]["run_id"], "attempt_id": attempt_id, "baseline_digest": attempt["baseline_digest"], "generated_delta_digest": delta["digest"]}
        except (KeyError, OSError, StopIteration, TypeError, ValueError):
            pass
    return {
        "adapter_id": request.adapter_id,
        "executable_path": request.executable,
        "cwd": request.cwd,
        **binding,
        "build_profile": request.build_profile,
        "typed_parameters": dict(request.typed_parameters),
        "argv": list(request.argv),
        "selectors": list(request.selectors),
        "timeout_seconds": request.timeout_seconds,
        "report_paths": list(request.report_paths),
        "request_digest": request_digest(request),
        "report_evidence": [dict(row) for row in report_evidence],
        "artifact_evidence": [dict(row) for row in artifact_evidence],
        **({} if environment_inputs is None else {"environment_inputs": [dict(row) for row in environment_inputs]}),
    }


def _report(verdict: str, project: Path, language: str, source: Mapping[str, Any], automation_digest: str, review_digest: str, diagnostics: Sequence[Mapping[str, str]] = (), run_id: str | None = None, evidence: Sequence[Mapping[str, Any]] = (), process_evidence: Sequence[Mapping[str, Any]] = (), authoritative: bool = False, exit_code: int | None = None, runner: str = "not_applicable", interpreter: str | None = None, command_profile: str | None = None, duration_sec: float | None = None, safe_key_labels: Sequence[str] = (), *, request: Any = None, report_evidence: Sequence[Mapping[str, str]] = (), artifact_evidence: Sequence[Mapping[str, str]] = (), run_root: Path | None = None, attempt_id: str | None = None, environment_inputs: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    prestart = verdict == "NOT_RUNNABLE" and run_id is None
    request_backed = _request_report_path(request) is not None
    if request_backed:
        runner, command_profile = {
            PYTEST: ("pytest", PYTEST),
            MAVEN: ("maven", MAVEN),
            SYSTEM_MAVEN: ("maven", SYSTEM_MAVEN),
            GRADLE: ("gradle", GRADLE),
        }[request.adapter_id]
        interpreter = request.executable
        safe_key_labels = request.environment_labels
    source = dict(source)
    source.setdefault("effective_bundle_receipt_digest", "sha256:" + "0" * 64)
    report = {"schema_version": "5.0.0", "stage": "run-tests", "source": source, "automation_sha256": automation_digest, "autotest_review_sha256": review_digest, "verdict": verdict, "target": {"language": language, "framework": "pytest" if language == "python" else "junit5", "runner": runner, "command": command_profile}, "environment": {"status": "ready" if request_backed or not prestart else "partial", "interpreter": interpreter, "interpreter_path": interpreter, "working_dir": str(project), "missing": None if request_backed or not prestart else ["runner_precondition"], "safe_key_labels": sorted(set(safe_key_labels))}, "execution": _execution_receipt(request, report_evidence, artifact_evidence, run_root=run_root, attempt_id=attempt_id, environment_inputs=environment_inputs), "stats": _stats(evidence, duration_sec) if run_id else None, "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": datetime.now(timezone.utc).isoformat(), "exit_code": exit_code, "run_id": run_id, "execution_evidence": [dict(row) for row in sorted(evidence, key=lambda row: (row["file_id"], row["symbol_id"]))], "process_evidence": [dict(row) for row in process_evidence], "evidence_authoritative": authoritative, "diagnostics": [dict(row) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"]))]}
    from tools.schema_validation import schema_diagnostics
    if schema_diagnostics(report, _ROOT / "schemas" / "run-tests-output.schema.json", _ROOT):
        raise RuntimeError("RUNNER_REPORT_SCHEMA")
    return report


def _authorization_allows_execution(receipt: Any, run_root: Path | None) -> bool:
    """Accept only the sealed full-pipeline authorization bound to this run root."""
    from tools.schema_validation import schema_diagnostics

    if not isinstance(receipt, Mapping):
        return False
    value = dict(receipt)
    if schema_diagnostics(value, _ROOT / "schemas" / "run-authorization-receipt.schema.json", _ROOT):
        return False
    digest = value.pop("digest", None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if not (
        digest == "sha256:" + hashlib.sha256(encoded).hexdigest()
        and value["policy_profile"] == "local-pilot-v1"
        and value["execution_requested"] is True
    ):
        return False
    try:
        from tools.pilot_state import read_run

        bound = read_run(Path(run_root))["authorization"] if run_root is not None else None
    except (OSError, TypeError, ValueError, KeyError):
        return False
    return isinstance(bound, Mapping) and dict(bound) == dict(receipt)


def _durable_delta_allows_execution(
    receipt: Any,
    project: Path,
    run_root: Path | None,
    attempt_id: str | None,
    automation_digest: str,
    review_digest: str,
    automation_artifact: Mapping[str, Any],
) -> bool:
    """Require the exact attempt-owned materialization readback, never caller data."""
    try:
        from tools.generated_delta import inspect_delta
        from tools.pilot_state import read_attempt_receipt

        durable = read_attempt_receipt(Path(run_root), str(attempt_id), "generated-delta", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if not isinstance(receipt, Mapping) or dict(receipt) != dict(durable):
        return False
    delta = durable.get("delta")
    artifacts = automation_artifact.get("artifacts") if isinstance(automation_artifact, Mapping) else None
    files = artifacts.get("generated_files") if isinstance(artifacts, Mapping) else None
    if not isinstance(delta, Mapping) or not isinstance(files, list):
        return False
    expected = [(row.get("file_id"), row.get("path"), row.get("content_digest")) for row in files if isinstance(row, Mapping)]
    actual_files = delta.get("files")
    actual = [
        (row.get("file_id"), row.get("path"), row.get("content_digest"))
        for row in actual_files if isinstance(row, Mapping)
    ] if isinstance(actual_files, list) else []
    facts = delta.get("facts")
    return (
        expected == actual
        and delta.get("automation_digest") == automation_digest
        and delta.get("review_digest") == review_digest
        and isinstance(facts, Mapping)
        and facts.get("completion") == "COMPLETE"
        and facts.get("verification") is None
        and all(row.get("materialization") == "MATERIALIZED" and row.get("disposition") is None for row in actual_files)
        and inspect_delta(project, delta) == {"valid": True}
    )


def _attempt_execution_scope(run_root: Path | None, attempt_id: str | None) -> tuple[Path, Path] | None:
    """Recover the immutable project/module pair; callers never choose this scope."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return None
    try:
        from tools.pilot_state import derive_state, read_run

        run = read_run(run_root)
        manifest = run["manifest"]
        attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
        project = Path(str(manifest["project"])).resolve(strict=True)
        module_name = str(attempt["module"])
        raw_module = Path(module_name)
        if raw_module.is_absolute() or ".." in raw_module.parts:
            return None
        module = (project / raw_module).resolve(strict=True)
        module.relative_to(project)
        if not module.is_dir():
            return None
        return project, module
    except (KeyError, OSError, StopIteration, TypeError, ValueError):
        return None


def _request_is_attempt_local(request: Any, project: Path, module: Path) -> bool:
    """Reject cross-project replay and undeclared host tools before a process can start."""
    from tools.execution_adapters import ExecutionRequest, SYSTEM_MAVEN, request_scope_is_closed
    from tools.project_inventory import module_runtime_path, system_maven_path

    if not isinstance(request, ExecutionRequest):
        return False
    try:
        # The launch cwd is the module or its declared build root (B5); a wrapper lives there.
        cwd = Path(request.cwd).resolve(strict=True)
        if request.adapter_id == SYSTEM_MAVEN:
            executable = system_maven_path(request.executable)
            if str(executable) != request.executable:
                return False
        else:
            relative = Path(request.executable).relative_to(cwd).as_posix()
            executable = module_runtime_path(cwd, relative)
    except (OSError, ValueError):
        return False
    return request_scope_is_closed(request, project, module) and executable.is_file() and module.is_relative_to(project)


def validate_execution_eligibility(
    request: Any,
    authorization_receipt: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
    autotest_review_artifact: Mapping[str, Any],
    *,
    generated_delta_receipt: Mapping[str, Any] | None,
    run_root: Path | None,
    attempt_id: str | None,
) -> Mapping[str, Any]:
    """Perform durable, process-free Phase 6 admission checks for one attempt."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256

    scope = _attempt_execution_scope(run_root, attempt_id)
    if scope is None:
        return {"ready": False, "reason_code": "RUNNER_ATTEMPT_SCOPE"}
    project, module = scope
    if not _authorization_allows_execution(authorization_receipt, run_root):
        return {"ready": False, "reason_code": "RUNNER_AUTHORIZATION"}
    automation_digest = automation_sha256(automation_artifact)
    review_digest = autotest_review_sha256(autotest_review_artifact)
    if not _durable_delta_allows_execution(
        generated_delta_receipt, project, run_root, attempt_id, automation_digest, review_digest, automation_artifact,
    ):
        return {"ready": False, "reason_code": "RUNNER_GENERATED_DELTA"}
    if not _request_is_attempt_local(request, project, module):
        return {"ready": False, "reason_code": "RUNNER_REQUEST_PROVENANCE"}
    if _request_report_path(request) is None:
        return {"ready": False, "reason_code": "RUNNER_REQUEST"}
    return {"ready": True, "project": project, "module": module}


def build_closed_execution_request(
    execution_root: Path,
    language: Literal["python", "java"],
    module: Mapping[str, Any],
    canonical_document: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
) -> tuple[Any, RunnerCompatibility]:
    """Bind one reviewed artifact to the selected module's closed adapter request.

    This is deliberately the sole public construction seam: callers cannot supply
    argv, a command template, or a process environment.
    """
    from tools.execution_adapters import (
        GRADLE,
        MAVEN,
        SYSTEM_MAVEN,
        PYTEST,
        AdapterRequestError,
        build_request,
    )

    test = module.get("test") if isinstance(module, Mapping) else None
    adapter_id = test.get("adapter_id") if isinstance(test, Mapping) else None
    expected = {"python": {PYTEST}, "java": {MAVEN, SYSTEM_MAVEN, GRADLE}}[language]
    if adapter_id not in expected:
        raise RunnerInputError(
            "RUNNER_ADAPTER",
            [_diag("/.skillsrc/test/adapter_id", "RUNNER_ADAPTER", "Selected module has no compatible closed adapter.")],
        )
    compatibility = validate_artifact_runner_compatibility(
        execution_root.resolve(), language, canonical_document, automation_artifact
    )
    if compatibility.status != "READY":
        raise RunnerInputError("RUNNER_COMPATIBILITY", compatibility.diagnostics)
    selectors = []
    for pair in compatibility.required_pairs:
        binding = compatibility.bindings[pair]
        if language == "python":
            selector = f"{binding['relative_path']}::{binding['node']}"
        else:
            locator = binding["locator"]
            selector = f"{locator['class_fqn']}#{locator['method_name']}"
        selectors.append({"selector": selector})
    try:
        request_module = dict(module)
        request_module["module_root"] = str(execution_root.resolve())
        # ``framework`` establishes manifest/schema selection; the adapter seam
        # intentionally accepts only launch-significant closed fields.
        runtime_key = "interpreter" if adapter_id == PYTEST else "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"
        request_module["test"] = {
            "adapter_id": test["adapter_id"],
            "build_profile": test["build_profile"],
            "adapter_parameters": test["adapter_parameters"],
            runtime_key: test[runtime_key],
            # Optional launch facts from .skillsrc: build root (B5) and timeout (P07).
            **{key: test[key] for key in ("build_root", "timeout_seconds") if key in test},
        }
        return build_request(adapter_id, request_module, selectors), compatibility
    except AdapterRequestError as error:
        raise RunnerInputError(
            "RUNNER_REQUEST",
            [_diag("/.skillsrc/test", "RUNNER_REQUEST", "Closed module execution request is unavailable.")],
        ) from error


def _request_report_path(request: Any) -> Path | None:
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, SAFE_ENVIRONMENT_LABELS, ExecutionRequest, command_for_request, request_module_root

    if not isinstance(request, ExecutionRequest) or request.adapter_id not in {PYTEST, MAVEN, SYSTEM_MAVEN, GRADLE}:
        return None
    if not request.argv or request.argv[0] != request.executable or request.timeout_seconds <= 0 or len(request.selectors) != len(set(request.selectors)):
        return None
    if set(request.environment_labels) - SAFE_ENVIRONMENT_LABELS or request.environment_labels != ("PROJECT_NATIVE_ENV",):
        return None
    if request.adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} and not request.build_profile:
        return None
    try:
        cwd = Path(request.cwd).resolve(strict=True)
    except OSError:
        return None
    if not cwd.is_dir() or len(request.report_paths) != 1:
        return None
    raw_report = Path(request.report_paths[0])
    if "\x00" in request.report_paths[0] or raw_report.is_absolute() or ".." in raw_report.parts:
        return None
    try:
        # Reports are module-relative; the module may sit below the launch cwd (B5).
        module_root = request_module_root(request).resolve()
        module_root.relative_to(cwd)
        report = (module_root / raw_report).resolve()
        report.relative_to(module_root)
        reports, expected = command_for_request(request)
    except ValueError:
        return None
    return report if request.argv == expected and request.report_paths == reports and request.selectors else None


def _invoke_closed_request(argv: tuple[str, ...], *, cwd: str, timeout: int, shell: bool) -> ProcessOutcome:
    """Bridge the adapter's immutable launch shape to the local subprocess primitive."""
    if shell is not False:
        raise RunnerInputError("RUNNER_REQUEST", [_diag("/request", "RUNNER_REQUEST", "Shell execution is forbidden.")])
    return run_subprocess(list(argv), Path(cwd), timeout=timeout)


def gate_request_is_closed(gate_request: Any, request: Any) -> bool:
    """The gate may differ from the reviewed request only by its closed gate argv."""
    from tools.execution_adapters import AdapterRequestError, ExecutionRequest, gate_command_for, request_module_path

    if not isinstance(gate_request, ExecutionRequest) or not isinstance(request, ExecutionRequest):
        return False
    try:
        expected = gate_command_for(
            request.adapter_id, request.executable, request.build_profile, request.selectors,
            module_path=request_module_path(request),
        )
    except (AdapterRequestError, TypeError, ValueError):
        return False
    return (
        gate_request.argv == expected
        and gate_request.report_paths == ()
        and all(
            getattr(gate_request, name) == getattr(request, name)
            for name in (
                "adapter_id", "executable", "cwd", "selectors", "timeout_seconds",
                "environment_labels", "build_profile", "typed_parameters",
            )
        )
    )


def build_closed_gate_request(request: Any) -> Any:
    """Derive the compile/collect gate of an already built closed execution request.

    The argv is the adapter's own gate policy (``gate_command_for``, the function
    behind ``build_gate_command``) applied to the request's runtime, profile,
    selectors and module path, so the gate can never name another runtime, cwd
    or selector set than the reviewed request.
    """
    from dataclasses import replace

    from tools.execution_adapters import AdapterRequestError, ExecutionRequest, gate_command_for, request_module_path

    failure = RunnerInputError(
        "RUNNER_REQUEST",
        [_diag("/request", "RUNNER_REQUEST", "Closed module gate request is unavailable.")],
    )
    if not isinstance(request, ExecutionRequest):
        raise failure
    try:
        argv = gate_command_for(
            request.adapter_id, request.executable, request.build_profile, request.selectors,
            module_path=request_module_path(request),
        )
    except (AdapterRequestError, TypeError, ValueError) as error:
        raise failure from error
    return replace(request, argv=tuple(argv), report_paths=())


def gate_failure_kind(adapter_id: str | None, outcome: ProcessOutcome) -> str | None:
    """Classify the compile/collect gate; ``None`` means the gate passed."""
    from tools.execution_adapters import PYTEST

    if outcome.kind == "TIMEOUT":
        return "TIMEOUT"
    if outcome.kind == "OS_ERROR":
        return "LAUNCH_FAILED"
    if outcome.exit_code == 0:
        return None
    if outcome.exit_code < 0:
        return "JUNIT_MISSING"  # killed by a signal: nothing is known about the tests
    if adapter_id == PYTEST and outcome.exit_code == 5:
        return "TESTS_DESELECTED"  # explicit node IDs, zero collected
    return "GENERATED_TEST_INVALID"


_PYTEST_IMPORT_FAILURE = re.compile(
    r"(?m)^(?:_* ?ERROR collecting |ImportError while (?:importing|loading) |ERROR: not found: |E\s+(?:ModuleNotFoundError|ImportError|SyntaxError|IndentationError)\b)"
)
_GRADLE_COMPILE_FAILURE = re.compile(r"Execution failed for task '[^'\r\n]*:compile[A-Za-z]*'")


def _output_reports_build_failure(adapter_id: str | None, exit_code: int, output: str) -> bool:
    """True when the runner itself says the tests did not compile or import."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    if exit_code <= 0 or not output:
        return False
    if adapter_id == PYTEST:
        return exit_code in {2, 4} and (
            _PYTEST_IMPORT_FAILURE.search(output) is not None
            or "error during collection" in output
            or "errors during collection" in output
        )
    if adapter_id in {MAVEN, SYSTEM_MAVEN}:
        return "COMPILATION ERROR" in output or "Compilation failure" in output
    if adapter_id == GRADLE:
        return _GRADLE_COMPILE_FAILURE.search(output) is not None or "Compilation failed; see the compiler error output" in output
    return False


def _reports_collection_failure(snapshots: Mapping[Path, bytes]) -> bool:
    """pytest writes a module that failed to import as an errored ``collection failure`` case."""
    for content in snapshots.values():
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            error = case.find("error")
            if error is not None and error.get("message") == "collection failure":
                return True
    return False


def classify_process_result(
    *,
    adapter_id: str | None,
    outcome_kind: str,
    exit_code: int,
    required_count: int,
    evidence_statuses: Sequence[str],
    match_errors: Sequence[str],
    has_report: bool,
    zero_report: bool,
    collection_error: bool = False,
    output: str = "",
    gate_kind: str | None = None,
    post_execution_reason: str | None = None,
) -> tuple[Literal["PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE"], str | None]:
    """Map one finished process to a verdict and its process-evidence kind.

    ``NOT_RUNNABLE`` is reserved for outcomes that prove the reviewed tests did
    not run (launch failure, compile/import failure, deselection).  A failed
    product check is ``FAIL``; anything ambiguous stays ``UNKNOWN``.
    """
    from tools.execution_adapters import PYTEST

    complete = required_count > 0 and len(evidence_statuses) == required_count and not match_errors
    failed = complete and any(status in {"FAILED", "ERROR"} for status in evidence_statuses)
    if post_execution_reason:
        return "UNKNOWN", "TIMEOUT" if outcome_kind == "TIMEOUT" else "BASELINE_DRIFT"
    if outcome_kind == "OS_ERROR":
        return "NOT_RUNNABLE", "LAUNCH_FAILED"
    if outcome_kind == "TIMEOUT":
        if gate_kind is None and failed:
            return "FAIL", None  # an exact framework failure outranks the controller timeout
        return "UNKNOWN", "TIMEOUT"
    if gate_kind is not None:
        return ("NOT_RUNNABLE" if gate_kind in NOT_RUNNABLE_PROCESS_KINDS else "UNKNOWN"), gate_kind
    if exit_code < 0:
        return "UNKNOWN", "JUNIT_INVALID" if has_report else "JUNIT_MISSING"
    if zero_report:
        if adapter_id == PYTEST:
            # Explicit node IDs with a valid empty report: pytest could not find the
            # node (usage error 4) or the project configuration deselected it.
            return "NOT_RUNNABLE", "GENERATED_TEST_INVALID" if exit_code == 4 else "TESTS_DESELECTED"
        return "FAIL", "NO_TESTS_COLLECTED"
    if complete:
        if failed:
            return "FAIL", None
        if any(status == "SKIPPED" for status in evidence_statuses):
            return "UNKNOWN", "JUNIT_INVALID"
        if exit_code != 0:
            return "UNKNOWN", "NONZERO_EXIT_GREEN_REPORT"
        return "PASS", None
    if exit_code > 0 and (collection_error or (not has_report and _output_reports_build_failure(adapter_id, exit_code, output))):
        return "NOT_RUNNABLE", "GENERATED_TEST_INVALID"
    return "UNKNOWN", "JUNIT_INVALID" if has_report else "JUNIT_MISSING"


def _durable_execution_start_events(run_root: Path | None, attempt_id: str | None) -> tuple[Mapping[str, Any], ...] | None:
    """Read the current attempt's durable execution-start events, if available."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return None
    try:
        from tools.pilot_state import derive_state

        return tuple(
            event for event in derive_state(run_root).get("events", [])
            if event.get("event_type") == "EXECUTION_STARTED"
            and event.get("attempt_id") == attempt_id
        )
    except (OSError, TypeError, ValueError):
        return None


def _has_one_new_durable_execution_start(run_root: Path | None, attempt_id: str | None, request: Any) -> bool:
    """Return whether the callback created one request-bound execution start.

    A callback is only the controller's opportunity to make the atomic claim.  It
    is not proof that the claim happened: the runner must read the append-only
    state itself immediately before it can invoke an adapter.  A pre-existing
    start is an interrupted execution, never authorization to replay it.
    """
    try:
        from tools.execution_adapters import request_digest

        expected = request_digest(request)
    except (OSError, TypeError, ValueError):
        return False
    starts = _durable_execution_start_events(run_root, attempt_id)
    return starts is not None and len(starts) == 1 and starts[0].get("artifact_digest") == expected


def run_tests_v5(request: Any, authorization_receipt: Mapping[str, Any], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], *, host_isolation_receipt: Mapping[str, Any] | None = None, generated_delta_receipt: Mapping[str, Any] | None = None, run_root: Path | None = None, attempt_id: str | None = None, on_execution_start: Callable[[Any], None] | None = None, post_execution_check: Callable[[], str | None] | None = None, gate_request: Any = None) -> dict[str, Any]:
    """Execute exactly one closed project-native adapter request after authorization.

    ``gate_request`` is the controller-built compile/collect gate for the same
    request; when present it runs first, under the same execution-start claim.
    """
    from tools.automation_validation import automation_sha256, autotest_review_sha256, validate_accepted_autotest_review
    from tools.execution_adapters import (
        GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, invoke_request, is_zero_test_report,
    )

    artifacts = automation_artifact.get("artifacts", {}) if isinstance(automation_artifact, Mapping) else {}
    source = artifacts.get("source", {"document_id": canonical_document.get("document_id", ""), "revision": canonical_document.get("revision", 1), "source_digest": "sha256:" + "0" * 64})
    automation_digest = automation_sha256(automation_artifact) if isinstance(automation_artifact, Mapping) else "sha256:" + "0" * 64
    review_digest = autotest_review_sha256(autotest_review_artifact) if isinstance(autotest_review_artifact, Mapping) else "sha256:" + "0" * 64
    adapter_id = getattr(request, "adapter_id", None)
    language = "java" if adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} else "python"
    target = {
        PYTEST: ("pytest", PYTEST), MAVEN: ("maven", MAVEN), SYSTEM_MAVEN: ("maven", SYSTEM_MAVEN), GRADLE: ("gradle", GRADLE),
    }.get(adapter_id, ("not_applicable", None))
    scope = _attempt_execution_scope(run_root, attempt_id)
    attempt_project = scope[0] if scope is not None else Path(getattr(request, "cwd", Path.cwd()))
    project = scope[1] if scope is not None else attempt_project
    if scope is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/attempt", "RUNNER_ATTEMPT_SCOPE", "Execution requires one durable attempt scope.")], request=request)
    if not _request_is_attempt_local(request, scope[0], scope[1]):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/request", "RUNNER_REQUEST_PROVENANCE", "Execution request must use the exact attempt module and declared closed runtime.")])
    report_path = _request_report_path(request)
    if report_path is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/request", "RUNNER_REQUEST", "Execution request is not a closed project-native adapter request.")])
    if not _authorization_allows_execution(authorization_receipt, run_root):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/authorization", "RUNNER_AUTHORIZATION", "Execution requires one explicit full-pipeline authorization receipt.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if not _durable_delta_allows_execution(
        generated_delta_receipt, attempt_project, run_root, attempt_id, automation_digest, review_digest, automation_artifact,
    ):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/generated_delta", "RUNNER_GENERATED_DELTA", "Execution requires the exact durable materialization readback.")], request=request, run_root=run_root, attempt_id=attempt_id)
    review_rows = validate_accepted_autotest_review(
        autotest_review_artifact, automation_artifact, dict(canonical_document),
        host_isolation_receipt=host_isolation_receipt, run_root=run_root, attempt_id=attempt_id,
    )
    if review_rows:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, review_rows, request=request, run_root=run_root, attempt_id=attempt_id)
    compatibility = validate_artifact_runner_compatibility(scope[1], language, canonical_document, automation_artifact)
    if compatibility.status != "READY":
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, compatibility.diagnostics, request=request, run_root=run_root, attempt_id=attempt_id)
    expected_selectors = {
        str(binding["relative_path"]) + "::" + str(binding["node"])
        if language == "python" else str(binding["locator"]["class_fqn"]) + "#" + str(binding["locator"]["method_name"])
        for binding in compatibility.bindings.values()
    }
    if set(request.selectors) != expected_selectors:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/selectors", "RUNNER_SELECTORS", "Request selectors must equal exact reviewed node IDs.")], request=request, run_root=run_root, attempt_id=attempt_id)

    if gate_request is not None and not gate_request_is_closed(gate_request, request):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/gate_request", "RUNNER_REQUEST", "Gate request is not the closed compile/collect gate of the execution request.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if on_execution_start is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution requires the controller's atomic execution-start gate.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if _durable_execution_start_events(run_root, attempt_id):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution was already started and cannot be replayed automatically.")], request=request, run_root=run_root, attempt_id=attempt_id)
    try:
        on_execution_start(request)
    except Exception:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim was not durably recorded.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if not _has_one_new_durable_execution_start(run_root, attempt_id, request):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim is absent, ambiguous, or bound to another request.")], request=request, run_root=run_root, attempt_id=attempt_id)
    run_id, started = "RUN-" + uuid.uuid4().hex, time.monotonic()
    environment_inputs = launch_environment_inputs()
    before = _report_inventory(report_path)
    gate_kind: str | None = None
    outcome: ProcessOutcome | None = None
    if gate_request is not None:
        # Compile/collect gate: the same closed runtime and selectors, no test body.
        gate_outcome = _outcome(invoke_request(gate_request, _invoke_closed_request))
        gate_kind = gate_failure_kind(adapter_id, gate_outcome)
        if gate_kind is not None:
            outcome = gate_outcome
    if outcome is None:
        outcome = _outcome(invoke_request(request, _invoke_closed_request))
    duration_sec = time.monotonic() - started
    snapshots: dict[Path, bytes] = {}
    if gate_kind is None:
        after = _report_inventory(report_path)
        fresh = {path for path, fingerprint in after.items() if before.get(path) != fingerprint}
        snapshots = _report_snapshots(tuple(fresh))
    evidence, match_errors = _records_from_snapshots(snapshots, compatibility, run_id, source, java=language == "java")
    zero_report = bool(snapshots) and all(
        is_zero_test_report(content) for content in snapshots.values()
    )
    post_execution_reason = None
    if post_execution_check is not None:
        try:
            post_execution_reason = post_execution_check()
        except Exception:
            post_execution_reason = "BASELINE_DRIFT"
    classified, process_kind = classify_process_result(
        adapter_id=adapter_id,
        outcome_kind=outcome.kind,
        exit_code=outcome.exit_code,
        required_count=len(compatibility.required_pairs),
        evidence_statuses=[row["status"] for row in evidence],
        match_errors=match_errors,
        has_report=bool(snapshots),
        zero_report=zero_report,
        collection_error=adapter_id == PYTEST and _reports_collection_failure(snapshots),
        output=f"{outcome.stdout or ''}\n{outcome.stderr or ''}",
        gate_kind=gate_kind,
        post_execution_reason=post_execution_reason,
    )
    if classified == "NOT_RUNNABLE":
        evidence = []
    process_evidence: list[dict[str, Any]] = []
    if process_kind is not None:
        process_evidence.append(_process_row(process_kind, outcome, run_id, source, target[1], duration_sec))
    post_run_digests = _post_run_file_digests(compatibility)
    changed_file_ids = _changed_file_ids(compatibility, post_run_digests)
    if changed_file_ids:
        evidence = [row for row in evidence if row["file_id"] not in changed_file_ids]
        process_evidence = [row for row in process_evidence if row["kind"] != "NO_TESTS_COLLECTED"]
        process_evidence.append(_process_row("SOURCE_CHANGED", outcome, run_id, source, target[1], duration_sec, changed_file_ids))
        classified = "UNKNOWN"
    zero_collect = classified == "FAIL" and process_kind == "NO_TESTS_COLLECTED"
    authoritative = (
        classified in {"PASS", "FAIL"}
        and (not match_errors or zero_collect)
        and not changed_file_ids
        and (
            len(evidence) == len(compatibility.required_pairs)
            or zero_collect
        )
    )
    if classified == "UNKNOWN" and not process_evidence:
        process_evidence.append(_process_row("JUNIT_INVALID" if snapshots else "JUNIT_MISSING", outcome, run_id, source, target[1], duration_sec))

    def without_artifacts(kind: str) -> None:
        """Keep the result when its durable copies could not be written."""
        nonlocal classified, evidence, authoritative, process_evidence
        if classified == "NOT_RUNNABLE":
            return  # the tests provably did not run; lost copies do not change that
        classified, evidence, authoritative = "UNKNOWN", [], False
        process_evidence = [
            row for row in process_evidence if row.get("kind") == "SOURCE_CHANGED"
        ]
        process_evidence.append(
            _process_row(kind, outcome, run_id, source, target[1], duration_sec)
        )

    try:
        report_evidence, artifact_evidence = _durable_execution_artifacts(
            snapshots, scope[1], generated_delta_receipt, outcome, run_root, attempt_id,
        )
    except DurableNativeReportError:
        report_evidence = _report_digest_evidence(snapshots, scope[1])
        try:
            _unused, artifact_evidence = _durable_execution_artifacts(
                {}, scope[1], generated_delta_receipt, outcome, run_root, attempt_id,
            )
            without_artifacts("JUNIT_INVALID")
        except Exception:
            artifact_evidence = []
            without_artifacts("ARTIFACT_PERSISTENCE_FAILED")
    except Exception:
        # The process already ran: losing this result would leave the attempt
        # with a started execution and nothing to finalize.
        report_evidence = _report_digest_evidence(snapshots, scope[1])
        artifact_evidence = []
        without_artifacts("ARTIFACT_PERSISTENCE_FAILED")
    diagnostics = (
        [_diag("/execution", process_kind, _NOT_RUNNABLE_MESSAGES[process_kind])]
        if classified == "NOT_RUNNABLE" and process_kind in NOT_RUNNABLE_PROCESS_KINDS else ()
    )
    return _report(classified, project, language, source, automation_digest, review_digest, diagnostics, run_id, evidence, process_evidence, authoritative, outcome.exit_code, target[0], request.executable, target[1], duration_sec, request.environment_labels, request=request, report_evidence=report_evidence, artifact_evidence=artifact_evidence, run_root=run_root, attempt_id=attempt_id, environment_inputs=environment_inputs)


def run_tests_v3(project: Path, language: Literal["python", "java"], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], provider_resolver: Any | None = None, adapter_registry: Any | None = None, *, runner_profile: str | None = None) -> dict[str, Any]:
    """Deprecated V3 entry point; it is intentionally incapable of starting code."""
    artifacts = automation_artifact.get("artifacts", {}) if isinstance(automation_artifact, Mapping) else {}
    source = artifacts.get("source", {"document_id": canonical_document.get("document_id", ""), "revision": canonical_document.get("revision", 1), "source_digest": "sha256:" + "0" * 64})
    return _report(
        "NOT_RUNNABLE", Path(project), language, source,
        "sha256:" + "0" * 64, "sha256:" + "0" * 64,
        [_diag("/runner", "RUNNER_LEGACY_DISABLED", "V3 execution is disabled; use the run-authorized V5 path.")],
    )


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="V5 closed project-native generated-test runner.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--skillsrc", help="Path to .skillsrc; defaults to <project>/.skillsrc when present.")
    parser.add_argument("--module", help="Module ID from a version 3 .skillsrc manifest.")
    parser.add_argument("--language", choices=["python", "java"])
    parser.add_argument("--canonical-document", required=True)
    parser.add_argument("--automation-artifact", required=True)
    parser.add_argument("--autotest-review", required=True)
    parser.add_argument("--authorization-receipt", required=True, help="Sealed full-pipeline run authorization receipt.")
    parser.add_argument("--host-isolation-receipt", required=True, help="Host-verified fresh reviewer isolation receipt.")
    parser.add_argument("--generated-delta-receipt", required=True, help="Attempt-owned generated-delta readback receipt.")
    parser.add_argument("--run-root", required=True, help="Pipeline-owned run root that stores the reviewer boundary.")
    parser.add_argument("--attempt-id", required=True, help="Exact active attempt ID bound to the reviewer boundary.")
    args = parser.parse_args(argv)
    try:
        from tools.canonical_document import load_canonical_document

        project_root = Path(args.project).resolve()
        execution_root = project_root
        language = args.language
        module = None
        skillsrc_path = Path(args.skillsrc).resolve() if args.skillsrc else project_root / ".skillsrc"
        if skillsrc_path.is_file():
            execution_root, language, module = resolve_execution_context(
                project_root,
                skillsrc_path,
                args.module,
                language,
            )
        elif args.skillsrc is not None or args.module is not None:
            raise SkillsrcError(
                "skillsrc_required",
                "explicit module selection requires an available .skillsrc",
            )
        else:
            raise SkillsrcError("skillsrc_required", "V5 execution requires a selected module with closed adapter configuration")
        if language not in {"python", "java"}:
            raise SkillsrcError(
                "language_unsupported",
                "selected module language is unsupported by the V5 runner",
            )
        document = load_canonical_document(Path(args.canonical_document))
        automation = load_automation_artifact(Path(args.automation_artifact))
        review = load_autotest_review_artifact(Path(args.autotest_review))
        from tools.schema_validation import load_json_strict

        authorization = load_json_strict(Path(args.authorization_receipt))
        host_isolation = load_json_strict(Path(args.host_isolation_receipt))
        generated_delta = load_json_strict(Path(args.generated_delta_receipt))
        assert module is not None
        request, _compatibility = build_closed_execution_request(
            execution_root, language, module, document, automation
        )
        from tools.execution_adapters import request_digest
        from tools.pilot_state import claim_execution_start

        def claim_start(exact_request: Any) -> None:
            claim_execution_start(Path(args.run_root), args.attempt_id, request_digest(exact_request))

        report = run_tests_v5(
            request, authorization, document, automation, review,
            host_isolation_receipt=host_isolation, generated_delta_receipt=generated_delta,
            run_root=Path(args.run_root), attempt_id=args.attempt_id, on_execution_start=claim_start,
            gate_request=build_closed_gate_request(request),
        )
        if report.get("run_id") is not None:
            # The attempt's single execution was consumed: its result must be durable,
            # otherwise the controller could only record EXECUTION_RESULT_LOST.
            # ``run_pipeline exec`` then finalizes the attempt from this receipt.
            from tools.run_pipeline import _publish_execution_receipt

            _publish_execution_receipt(
                {"run_root": Path(args.run_root).resolve(), "attempt": {"attempt_id": args.attempt_id}}, report,
            )
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2, "NOT_RUNNABLE": 2}[report["verdict"]]
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": getattr(error, "code", "RUNNER_INPUT"),
                        "message": "Runner input is invalid.",
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
