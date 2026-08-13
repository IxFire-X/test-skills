#!/usr/bin/env python3
"""V3 generated-test runner with exact ``(file_id, symbol_id)`` evidence."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

if __package__:
    from .json_cli import JsonArgumentParser
else:
    _bootstrap_root = str(Path(__file__).resolve().parents[1])
    if _bootstrap_root not in sys.path:
        sys.path.insert(0, _bootstrap_root)
    from json_cli import JsonArgumentParser

_ROOT = Path(__file__).resolve().parents[1]
_AUTOMATION_SCHEMA = _ROOT / "schemas" / "tc-to-autotest-output.schema.json"
_TIMEOUT = 600


@dataclass(frozen=True)
class RunnerCompatibility:
    status: Literal["READY", "NOT_RUNNABLE"]
    required_pairs: tuple[tuple[str, str], ...]
    bindings: Mapping[tuple[str, str], Mapping[str, Any]]
    verified_file_digests: Mapping[str, str]
    diagnostics: tuple[Mapping[str, str], ...]


class RunnerInputError(ValueError):
    def __init__(self, code: str) -> None:
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "diagnostics", (MappingProxyType({"path": "/artifacts/automation_status", "code": code,
                                                                      "message": "Automation artifact is not directly runnable."}),))
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


def _python_node(path: Path, locator: Mapping[str, Any]) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
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


def _java_node(path: Path, locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] != "java_class_method":
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    masked = _mask_java_noncode(text)
    package = re.search(r"^\s*package\s+([\w$]+(?:\.[\w$]+)*)\s*;", masked, re.MULTILINE)
    if (package.group(1) + "." if package else "") + path.stem != locator["class_fqn"]:
        return None
    class_name = locator["class_fqn"].rsplit(".", 1)[-1]
    method = locator["method_name"]
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
    direct_body = "".join(body)
    pattern = r"(?:public|protected|private)?\s*(?:static\s+)?[\w<>\[\]., ?]+\s+" + re.escape(method) + r"\s*\([^;{}]*\)\s*(?:throws[^\{]+)?\{"
    return method if len(re.findall(pattern, direct_body)) == 1 else None


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


def validate_artifact_runner_compatibility(project: Path, language: Literal["python", "java"], document: Mapping[str, Any], automation_artifact: Mapping[str, Any]) -> RunnerCompatibility:
    from tools.automation_validation import AutomationArtifactError, required_symbol_pairs
    from tools.canonical_document import validate_canonical_document
    canonical = validate_canonical_document(dict(document))
    if canonical:
        return _compat("NOT_RUNNABLE", diagnostics=[_diag(row["path"], row["code"], "Canonical document is invalid.") for row in canonical])
    try:
        pairs = required_symbol_pairs(dict(automation_artifact), dict(document))
    except AutomationArtifactError as error:
        return _compat("NOT_RUNNABLE", diagnostics=error.diagnostics)
    root, diagnostics, physical, paths, digests = project.resolve(), [], set(), {}, {}
    expected = (language, "pytest" if language == "python" else "junit5")
    files = automation_artifact["artifacts"]["generated_files"]
    symbols = automation_artifact["artifacts"]["generated_symbols"]
    for index, row in enumerate(files):
        file = _path(root, row["path"])
        pointer = f"/artifacts/generated_files/{index}"
        if file is None or file in physical:
            diagnostics.append(_diag(pointer + "/path", "RUNNER_FILE_CONFINEMENT", "Generated file is unavailable or escapes the project.")); continue
        physical.add(file); actual = "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != row["content_digest"]: diagnostics.append(_diag(pointer + "/content_digest", "RUNNER_FILE_DIGEST", "Generated file bytes do not match the artifact digest."))
        if (row["language"], row["framework"]) != expected: diagnostics.append(_diag(pointer, "RUNNER_LANGUAGE_FRAMEWORK", "Generated file is incompatible with the requested runner."))
        paths[row["file_id"]], digests[row["file_id"]] = file, actual
    symbol_map = {(row["file_id"], row["symbol_id"]): (index, row) for index, row in enumerate(symbols)}
    bindings: dict[tuple[str, str], Mapping[str, Any]] = {}
    for pair in sorted(pairs):
        index, symbol = symbol_map.get(pair, (-1, None)); file = paths.get(pair[0])
        node = None if symbol is None or file is None else (_python_node(file, symbol["locator"]) if language == "python" else _java_node(file, symbol["locator"]))
        if node is None:
            diagnostics.append(_diag(f"/artifacts/generated_symbols/{index}/locator", "RUNNER_LOCATOR", "Generated locator is absent or ambiguous.")); continue
        bindings[pair] = {"path": file, "relative_path": files[next(i for i, row in enumerate(files) if row["file_id"] == pair[0])]["path"], "locator": symbol["locator"], "node": node}
    return _compat("NOT_RUNNABLE", diagnostics=diagnostics) if diagnostics else _compat("READY", pairs, bindings, digests)


def validate_execution_evidence(verdict: str, run_id: str | None, source: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], authoritative: bool, required_pairs: Sequence[tuple[str, str]], verified_file_digests: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    required, diagnostics, seen = set(required_pairs), [], {}
    if verdict == "NOT_RUNNABLE" and run_id is None:
        return [_diag("/execution_evidence", "RUNNER_PRESTART_EVIDENCE", "Pre-start NOT_RUNNABLE cannot contain evidence.")] if evidence or authoritative else []
    for row in (row for row in evidence if row.get("run_id") == run_id):
        pair = row.get("file_id"), row.get("symbol_id")
        if pair not in required: diagnostics.append(_diag("/execution_evidence", "RUNNER_FOREIGN_EVIDENCE", "Current evidence names a non-required pair.")); continue
        if row.get("source_digest") != source.get("source_digest"): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_SOURCE", "Current evidence source digest does not match.")); continue
        if not verified_file_digests or row.get("file_digest") != verified_file_digests.get(pair[0]): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_FILE_DIGEST", "Current evidence file digest is not verified.")); continue
        seen.setdefault(pair, []).append(row)
    if any(len(rows) != 1 for rows in seen.values()): diagnostics.append(_diag("/execution_evidence", "RUNNER_DUPLICATE_EVIDENCE", "A required pair has duplicate or conflicting current evidence."))
    statuses = {pair: rows[0]["status"] for pair, rows in seen.items() if len(rows) == 1}
    derived = "FAIL" if diagnostics or any(value in {"FAILED", "ERROR"} for value in statuses.values()) else "NOT_RUNNABLE" if len(statuses) != len(required) or "SKIPPED" in statuses.values() else "PASS"
    complete = not diagnostics and len(statuses) == len(required)
    if verdict != derived: diagnostics.append(_diag("/verdict", "RUNNER_EVIDENCE_VERDICT", "Verdict does not match current execution evidence."))
    if authoritative != complete: diagnostics.append(_diag("/evidence_authoritative", "RUNNER_EVIDENCE_AUTHORITATIVE", "Authoritative flag does not match current execution evidence."))
    return diagnostics


def run_subprocess(command: list[str], project: Path) -> tuple[int, str, str]:
    try:
        value = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        return value.returncode, value.stdout, value.stderr
    except subprocess.TimeoutExpired: return 124, "", ""
    except OSError: return 127, "", ""


def _python() -> str | None:
    value = Path(sys.executable)
    return str(value) if value.is_file() else None


def _java_runner(project: Path) -> tuple[str | None, str | None]:
    java = shutil.which("java")
    for name in ("mvnw.cmd", "mvnw", "gradlew.cmd", "gradlew.bat", "gradlew"):
        if (project / name).is_file(): return java, name
    return java, shutil.which("mvn") or shutil.which("gradle")


def _records(xml: Path, compatibility: RunnerCompatibility, run_id: str, source: Mapping[str, Any], java: bool = False, fresh_reports: set[Path] | None = None) -> list[dict[str, str]]:
    observed: dict[tuple[str, str], list[str]] = {pair: [] for pair in compatibility.required_pairs}
    for report in xml.rglob("*.xml") if xml.is_dir() else [xml]:
        if fresh_reports is not None and report.resolve() not in fresh_reports:
            continue
        try: root = ET.parse(report).getroot()
        except (OSError, ET.ParseError): continue
        for item in root.iter("testcase"):
            name, classname = item.attrib.get("name", "").split("[", 1)[0], item.attrib.get("classname", "")
            status = "FAILED" if item.find("failure") is not None else "ERROR" if item.find("error") is not None else "SKIPPED" if item.find("skipped") is not None else "PASSED"
            for pair, binding in compatibility.bindings.items():
                loc = binding["locator"]
                if java:
                    match = classname == loc["class_fqn"] and name == loc["method_name"]
                else:
                    relative = binding.get("relative_path", binding["path"].as_posix())
                    file_attr = item.attrib.get("file")
                    expected_module = relative.removesuffix(".py").replace("/", ".")
                    class_module = expected_module if loc["kind"] == "python_module_function" else expected_module + "." + loc["qualified_class_name"]
                    same_file = file_attr == relative or (file_attr is None and classname == class_module)
                    match = same_file and (name == loc["function_name"] if loc["kind"] == "python_module_function" else name == loc["method_name"])
                if match: observed[pair].append(status)
    rows = []
    for pair, values in observed.items():
        for status in values: rows.append({"run_id": run_id, "source_digest": source["source_digest"], "file_id": pair[0], "symbol_id": pair[1], "file_digest": compatibility.verified_file_digests[pair[0]], "status": status})
    return rows


def _report_inventory(directory: Path) -> dict[Path, tuple[int, int, str]]:
    result: dict[Path, tuple[int, int, str]] = {}
    for path in directory.rglob("*.xml") if directory.is_dir() else ():
        try:
            data, stat = path.read_bytes(), path.stat()
            result[path.resolve()] = (stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())
        except OSError:
            continue
    return result


def _stats(evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = {value: sum(row["status"] == value for row in evidence) for value in ("PASSED", "FAILED", "ERROR", "SKIPPED")}
    return {"total": sum(count.values()), "passed": count["PASSED"], "failed": count["FAILED"], "errors": count["ERROR"], "skipped": count["SKIPPED"], "duration_sec": None}


def _report(verdict: str, project: Path, language: str, source: Mapping[str, Any], diagnostics: Sequence[Mapping[str, str]] = (), run_id: str | None = None, evidence: Sequence[Mapping[str, Any]] = (), authoritative: bool = False, exit_code: int | None = None, runner: str = "not_applicable", interpreter: str | None = None) -> dict[str, Any]:
    prestart = verdict == "NOT_RUNNABLE" and run_id is None
    return {"schema_version": "3.0.0", "stage": "run-tests", "source": dict(source), "verdict": verdict, "target": {"language": language, "framework": "pytest" if language == "python" else "junit5", "runner": runner, "command": None}, "environment": {"status": "partial" if prestart else "ready", "interpreter": interpreter, "interpreter_path": interpreter, "working_dir": str(project), "missing": ["runner_precondition"] if prestart else None}, "stats": _stats(evidence) if run_id else None, "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": datetime.now(timezone.utc).isoformat(), "exit_code": exit_code, "run_id": run_id, "execution_evidence": [dict(row) for row in sorted(evidence, key=lambda row: (row["file_id"], row["symbol_id"]))], "evidence_authoritative": authoritative, "diagnostics": [dict(row) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"]))]}


def run_tests_v3(project: Path, language: Literal["python", "java"], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], provider_resolver: Any | None = None, adapter_registry: Any | None = None) -> dict[str, Any]:
    from tools.execution_preflight import EnvironmentOnlyProviderResolver, RejectingAdapterRegistry, preflight_execution
    project, artifacts = project.resolve(), automation_artifact.get("artifacts", {})
    source = artifacts.get("source", {"document_id": canonical_document.get("document_id", ""), "revision": canonical_document.get("revision", 1), "source_digest": "sha256:" + "0" * 64})
    if artifacts.get("automation_status") == "BLOCKED": raise RunnerInputError("RUNNER_AUTOMATION_BLOCKED")
    compatibility = validate_artifact_runner_compatibility(project, language, canonical_document, automation_artifact)
    if compatibility.status != "READY": return _report("NOT_RUNNABLE", project, language, source, compatibility.diagnostics)
    if not compatibility.required_pairs: raise RunnerInputError("RUNNER_NO_AUTOMATABLE_SYMBOLS")
    resolver = EnvironmentOnlyProviderResolver(os.environ) if provider_resolver is None else provider_resolver
    registry = RejectingAdapterRegistry() if adapter_registry is None else adapter_registry
    ready = preflight_execution(dict(canonical_document), resolver, registry)
    if ready.status != "READY": return _report("NOT_RUNNABLE", project, language, source, ready.diagnostics)
    run_id = "RUN-" + uuid.uuid4().hex
    if language == "python":
        interpreter = _python()
        if not interpreter or run_subprocess([interpreter, "-c", "import pytest"], project)[0] != 0: return _report("NOT_RUNNABLE", project, language, source, [_diag("/environment", "RUNNER_ENVIRONMENT", "Python runtime or pytest is unavailable.")])
        with tempfile.TemporaryDirectory(prefix="run-tests-v3-") as temporary:
            xml = Path(temporary) / "junit.xml"; targets = [str(row["path"].relative_to(project)) + "::" + row["node"] for _, row in sorted(compatibility.bindings.items())]
            code, _, _ = run_subprocess([interpreter, "-m", "pytest", "-q", "--junitxml", str(xml), *targets], project); evidence = _records(xml, compatibility, run_id, source)
        runner = "pytest"
    else:
        interpreter, executable = _java_runner(project)
        if not interpreter or not executable: return _report("NOT_RUNNABLE", project, language, source, [_diag("/environment", "RUNNER_ENVIRONMENT", "Java runtime or project runner is unavailable.")])
        maven = "mvn" in Path(executable).name.lower(); selectors = [row["locator"]["class_fqn"] + "#" + row["locator"]["method_name"] for row in compatibility.bindings.values()]
        command = [executable, "test", "-Dtest=" + ",".join(selectors)] if maven else [executable, "test", *sum((["--tests", item.replace("#", ".")] for item in selectors), [])]
        report_dir = project / ("target/surefire-reports" if maven else "build/test-results")
        before = _report_inventory(report_dir); code, _, _ = run_subprocess(command, project); after = _report_inventory(report_dir)
        fresh = {path for path, fingerprint in after.items() if before.get(path) != fingerprint}
        evidence = _records(report_dir, compatibility, run_id, source, True, fresh); runner = "maven" if maven else "gradle"
    pairs = {(row["file_id"], row["symbol_id"]) for row in evidence}
    authoritative = len(evidence) == len(compatibility.required_pairs) and pairs == set(compatibility.required_pairs)
    verdict = "FAIL" if code != 0 or any(row["status"] in {"FAILED", "ERROR"} for row in evidence) else "NOT_RUNNABLE" if not authoritative or any(row["status"] == "SKIPPED" for row in evidence) else "PASS"
    diagnostics = validate_execution_evidence(verdict, run_id, source, evidence, authoritative, compatibility.required_pairs, compatibility.verified_file_digests)
    if diagnostics: verdict, authoritative = "FAIL", False
    return _report(verdict, project, language, source, diagnostics, run_id, evidence, authoritative, code, runner, interpreter)


def main() -> int:
    parser = JsonArgumentParser(description="V3 deterministic generated-test runner.")
    parser.add_argument("--project", required=True); parser.add_argument("--language", choices=["python", "java"], required=True)
    parser.add_argument("--canonical-document", required=True); parser.add_argument("--automation-artifact", required=True)
    args = parser.parse_args()
    try:
        from tools.canonical_document import load_canonical_document
        report = run_tests_v3(Path(args.project), args.language, load_canonical_document(Path(args.canonical_document)), load_automation_artifact(Path(args.automation_artifact)))
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":"))); return {"PASS": 0, "FAIL": 1, "NOT_RUNNABLE": 2}[report["verdict"]]
    except Exception as error:
        print(json.dumps({"error": {"code": getattr(error, "code", "RUNNER_INPUT"), "message": "Runner input is invalid."}}, ensure_ascii=False, separators=(",", ":"))); return 2


if __name__ == "__main__": sys.exit(main())
