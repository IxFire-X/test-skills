#!/usr/bin/env python3
"""Validate deterministic requirement-to-execution SDD trace documents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, SchemaError
except ImportError as error:  # pragma: no cover - depends on installation
    Draft202012Validator = None
    SchemaError = Exception
    _IMPORT_ERROR: Exception | None = error
else:
    _IMPORT_ERROR = None


DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "trace-document.schema.json"


def _pointer(parts: Iterable[object]) -> str:
    encoded = "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return f"/{encoded}" if encoded else ""


def _schema_path(error: Any) -> str:
    path = list(error.absolute_path)
    if error.validator == "required":
        path.append(error.message.removeprefix("'").split("'", 1)[0])
    elif error.validator == "additionalProperties":
        path.append(error.message.removeprefix("Additional properties are not allowed ('").split("'", 1)[0])
    return _pointer(path)


def _diagnostic(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _report(errors: list[dict[str, str]], mappings: list[dict[str, object]], summary: dict[str, int]) -> dict[str, object]:
    errors.sort(key=lambda item: (item["path"], item["code"], item["message"]))
    mappings.sort(key=lambda item: (str(item["requirement_id"]), str(item["test_case_id"]), str(item["method_id"])))
    messages = sorted({item["message"] for item in errors})
    return {
        "valid": not errors,
        "trace_audit": {"verdict": "PASS" if not errors else "FAIL", "mappings": mappings, "errors": messages},
        "errors": errors,
        "warnings": [],
        "summary": summary,
    }


def _summary(document: object) -> dict[str, int]:
    if not isinstance(document, dict):
        return {"requirements": 0, "test_cases": 0, "generated_files": 0, "methods": 0, "mappings": 0, "evidence": 0}

    def count(name: str, parent: dict[str, object] = document) -> int:
        value = parent.get(name, [])
        return len(value) if isinstance(value, list) else 0

    execution = document.get("execution")
    return {
        "requirements": count("requirements"),
        "test_cases": count("test_cases"),
        "generated_files": count("generated_files"),
        "methods": count("methods"),
        "mappings": count("trace_map"),
        "evidence": count("evidence", execution) if isinstance(execution, dict) else 0,
    }


def _load_validator(schema_path: Path) -> Draft202012Validator:
    if _IMPORT_ERROR is not None or Draft202012Validator is None:
        raise RuntimeError(f"missing jsonschema runtime: {_IMPORT_ERROR}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_diagnostics(error: Any) -> list[dict[str, str]]:
    if error.validator == "additionalProperties" and isinstance(error.instance, dict) and isinstance(error.schema, dict):
        properties = set(error.schema.get("properties", {}))
        patterns = [re.compile(pattern) for pattern in error.schema.get("patternProperties", {})]
        unexpected = sorted(key for key in error.instance if key not in properties and not any(pattern.search(key) for pattern in patterns))
        if unexpected:
            return [_diagnostic("invalid_input_schema", _pointer([*error.absolute_path, key]), f"additional property is not allowed: {key}") for key in unexpected]
    return [_diagnostic("invalid_input_schema", _schema_path(error), error.message)]


def _schema_report(document: object, schema_path: Path) -> list[dict[str, str]]:
    try:
        validator = _load_validator(schema_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError, RuntimeError) as error:
        return [_diagnostic("schema_error", "", f"schema unavailable or invalid: {error}")]
    return [diagnostic for error in sorted(validator.iter_errors(document), key=lambda item: (_schema_path(item), item.message)) for diagnostic in _schema_diagnostics(error)]


def _append(errors: list[dict[str, str]], code: str, path: str, message: str) -> None:
    errors.append(_diagnostic(code, path, message))


def _ids(records: list[dict[str, object]], entity: str, errors: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for index, record in enumerate(records):
        identifier = str(record["id"])
        if identifier in result:
            _append(errors, "DUPLICATE_ID", f"/{entity}/{index}/id", f"duplicate {entity} id: {identifier}")
        else:
            result[identifier] = record
    return result


def _semantic_check(document: dict[str, object], require_execution: bool) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    errors: list[dict[str, str]] = []
    requirements = document["requirements"]
    test_cases = document["test_cases"]
    files = document["generated_files"]
    methods = document["methods"]
    trace_map = document["trace_map"]
    assert isinstance(requirements, list) and isinstance(test_cases, list)
    assert isinstance(files, list) and isinstance(methods, list) and isinstance(trace_map, list)
    requirement_by_id = _ids(requirements, "requirements", errors)
    case_by_id = _ids(test_cases, "test_cases", errors)
    file_by_id = _ids(files, "generated_files", errors)
    method_by_id = _ids(methods, "methods", errors)
    paths: dict[object, int] = {}
    for index, generated_file in enumerate(files):
        path = generated_file["path"]
        if path in paths:
            _append(errors, "DUPLICATE_PATH", f"/generated_files/{index}/path", f"duplicate generated file path: {path}")
        else:
            paths[path] = index
    locators: dict[tuple[object, object], int] = {}
    for index, method in enumerate(methods):
        locator = (method["file_id"], method["name"])
        if locator in locators:
            _append(errors, "DUPLICATE_METHOD_LOCATOR", f"/methods/{index}", f"duplicate generated method locator: {locator[0]}::{locator[1]}")
        else:
            locators[locator] = index

    for index, case in enumerate(test_cases):
        for requirement_id in case["requirement_ids"]:
            if requirement_id not in requirement_by_id:
                _append(errors, "UNKNOWN_REQUIREMENT", f"/test_cases/{index}/requirement_ids", f"unknown requirement: {requirement_id}")
    for index, method in enumerate(methods):
        if method["file_id"] not in file_by_id:
            _append(errors, "UNKNOWN_FILE", f"/methods/{index}/file_id", f"unknown generated file: {method['file_id']}")
        for case_id in method["test_case_ids"]:
            if case_id not in case_by_id:
                _append(errors, "UNKNOWN_TEST_CASE", f"/methods/{index}/test_case_ids", f"unknown test case: {case_id}")
        for requirement_id in method["requirement_ids"]:
            if requirement_id not in requirement_by_id:
                _append(errors, "UNKNOWN_REQUIREMENT", f"/methods/{index}/requirement_ids", f"unknown requirement: {requirement_id}")

    mapping_keys: set[tuple[object, object, object, object]] = set()
    mapped_requirements: set[object] = set()
    mapped_cases: set[object] = set()
    mapped_methods: set[object] = set()
    mapped_case_requirements: set[tuple[object, object]] = set()
    mapped_method_cases: set[tuple[object, object]] = set()
    mapped_method_requirements: set[tuple[object, object]] = set()
    audit_mappings: list[dict[str, object]] = []
    for index, mapping in enumerate(trace_map):
        key = (mapping["requirement_id"], mapping["test_case_id"], mapping["file_id"], mapping["method_id"])
        if key in mapping_keys:
            _append(errors, "DUPLICATE_MAPPING", f"/trace_map/{index}", f"duplicate trace mapping: {'|'.join(map(str, key))}")
        mapping_keys.add(key)
        requirement_id, case_id, file_id, method_id = key
        mapped_requirements.add(requirement_id)
        mapped_cases.add(case_id)
        mapped_methods.add(method_id)
        mapped_case_requirements.add((case_id, requirement_id))
        mapped_method_cases.add((method_id, case_id))
        mapped_method_requirements.add((method_id, requirement_id))
        if requirement_id not in requirement_by_id:
            _append(errors, "UNKNOWN_REQUIREMENT", f"/trace_map/{index}/requirement_id", f"unknown requirement: {requirement_id}")
        if case_id not in case_by_id:
            _append(errors, "UNKNOWN_TEST_CASE", f"/trace_map/{index}/test_case_id", f"unknown test case: {case_id}")
        if file_id not in file_by_id:
            _append(errors, "UNKNOWN_FILE", f"/trace_map/{index}/file_id", f"unknown generated file: {file_id}")
        if method_id not in method_by_id:
            _append(errors, "UNKNOWN_METHOD", f"/trace_map/{index}/method_id", f"unknown generated method: {method_id}")
        case = case_by_id.get(case_id)
        method = method_by_id.get(method_id)
        if case is not None and requirement_id not in case["requirement_ids"]:
            _append(errors, "MAPPING_MISMATCH", f"/trace_map/{index}", f"test case {case_id} does not declare requirement {requirement_id}")
        if method is not None:
            if case_id not in method["test_case_ids"]:
                _append(errors, "MAPPING_MISMATCH", f"/trace_map/{index}", f"method {method_id} does not declare test case {case_id}")
            if requirement_id not in method["requirement_ids"]:
                _append(errors, "MAPPING_MISMATCH", f"/trace_map/{index}", f"method {method_id} does not declare requirement {requirement_id}")
            if method["file_id"] != file_id:
                _append(errors, "MAPPING_MISMATCH", f"/trace_map/{index}", f"method {method_id} belongs to {method['file_id']}, not {file_id}")

    for identifier in sorted(requirement_by_id):
        if identifier not in mapped_requirements:
            _append(errors, "MISSING_MAPPING", "/requirements", f"requirement has no trace mapping: {identifier}")
    for identifier in sorted(case_by_id):
        if identifier not in mapped_cases:
            _append(errors, "MISSING_MAPPING", "/test_cases", f"test case has no trace mapping: {identifier}")
    for case_index, case in enumerate(test_cases):
        for requirement_index, requirement_id in enumerate(case["requirement_ids"]):
            if (case["id"], requirement_id) not in mapped_case_requirements:
                _append(errors, "MISSING_MAPPING", f"/test_cases/{case_index}/requirement_ids/{requirement_index}", f"test case {case['id']} declaration has no trace mapping for requirement {requirement_id}")
    for identifier in sorted(method_by_id):
        if identifier not in mapped_methods:
            _append(errors, "ORPHAN_METHOD", "/methods", f"generated method has no trace mapping: {identifier}")
    for method_index, method in enumerate(methods):
        for case_index, case_id in enumerate(method["test_case_ids"]):
            if (method["id"], case_id) not in mapped_method_cases:
                _append(errors, "MISSING_MAPPING", f"/methods/{method_index}/test_case_ids/{case_index}", f"method {method['id']} declaration has no trace mapping for test case {case_id}")
        for requirement_index, requirement_id in enumerate(method["requirement_ids"]):
            if (method["id"], requirement_id) not in mapped_method_requirements:
                _append(errors, "MISSING_MAPPING", f"/methods/{method_index}/requirement_ids/{requirement_index}", f"method {method['id']} declaration has no trace mapping for requirement {requirement_id}")
    methods_per_file = {method["file_id"] for method in methods if method["file_id"] in file_by_id}
    for identifier in sorted(file_by_id):
        if identifier not in methods_per_file:
            _append(errors, "ORPHAN_FILE", "/generated_files", f"generated file has no generated method: {identifier}")

    execution = document.get("execution")
    evidence_by_method: dict[object, list[dict[str, object]]] = {}
    unavailable = False
    if isinstance(execution, dict):
        unavailable = execution["verdict"] == "NOT_RUNNABLE"
        run_ids: set[object] = set()
        evidence_keys: set[tuple[object, object]] = set()
        for index, evidence in enumerate(execution["evidence"]):
            run_id, method_id = evidence["run_id"], evidence["method_id"]
            if run_id in run_ids:
                _append(errors, "DUPLICATE_ID", f"/execution/evidence/{index}/run_id", f"duplicate execution run id: {run_id}")
            run_ids.add(run_id)
            evidence_key = (run_id, method_id)
            if evidence_key in evidence_keys:
                _append(errors, "DUPLICATE_MAPPING", f"/execution/evidence/{index}", f"duplicate execution evidence: {run_id}|{method_id}")
            evidence_keys.add(evidence_key)
            if method_id not in method_by_id:
                _append(errors, "UNKNOWN_METHOD", f"/execution/evidence/{index}/method_id", f"unknown execution method: {method_id}")
            evidence_by_method.setdefault(method_id, []).append(evidence)
        skip_rules: dict[object, list[dict[str, object]]] = {}
        for index, rule in enumerate(execution["allowed_skips"]):
            method_id = rule["method_id"]
            if method_id not in method_by_id:
                _append(errors, "UNKNOWN_METHOD", f"/execution/allowed_skips/{index}/method_id", f"unknown allowed-skip method: {method_id}")
            skip_rules.setdefault(method_id, []).append(rule)
            if len(skip_rules[method_id]) > 1:
                _append(errors, "DUPLICATE_SKIP_RULE", f"/execution/allowed_skips/{index}/method_id", f"duplicate allowed-skip rule for method: {method_id}")
        skipped_methods = {evidence["method_id"] for evidence in execution["evidence"] if evidence["status"] == "skipped"}
        for method_id in sorted(skip_rules, key=str):
            if method_id not in skipped_methods:
                _append(errors, "UNUSED_SKIP_RULE", "/execution/allowed_skips", f"allowed-skip rule is unused: {method_id}")
        for method_id, records in evidence_by_method.items():
            for record in records:
                if record["status"] in {"failed", "error"}:
                    _append(errors, "EXECUTION_FAILURE", "/execution/evidence", f"execution {record['status']} for method: {method_id}")
                if record["status"] == "skipped" and len(skip_rules.get(method_id, [])) != 1:
                    _append(errors, "DISALLOWED_SKIP", "/execution/evidence", f"skipped method lacks exactly one allowed-skip rule: {method_id}")
        failed_records = [record for records in evidence_by_method.values() for record in records if record["status"] in {"failed", "error"}]
        if execution["verdict"] == "PASS" and failed_records:
            _append(errors, "EXECUTION_VERDICT_MISMATCH", "/execution/verdict", "execution verdict PASS contradicts failed or error evidence")
        if execution["verdict"] == "FAIL" and not failed_records:
            _append(errors, "EXECUTION_VERDICT_MISMATCH", "/execution/verdict", "execution verdict FAIL has no failed or error evidence")
        if execution["verdict"] == "NOT_RUNNABLE" and (execution["evidence"] or execution["allowed_skips"]):
            _append(errors, "EXECUTION_VERDICT_MISMATCH", "/execution/verdict", "execution verdict NOT_RUNNABLE requires empty evidence and allowed skips")

    execution_is_effective = require_execution or bool(document["execution_required"])
    if isinstance(execution, dict) and execution["verdict"] != "PASS":
        _append(errors, "EXECUTION_GATE", "/execution", "supplied execution must have verdict PASS for a valid trace")
    elif execution_is_effective and not isinstance(execution, dict):
        _append(errors, "EXECUTION_GATE", "/execution", "execution is required and must have verdict PASS")
    if isinstance(execution, dict) and execution["verdict"] == "PASS":
        for method_id in sorted(mapped_methods, key=str):
            if method_id not in evidence_by_method:
                _append(errors, "MISSING_EXECUTION", "/execution/evidence", f"mapped method has no execution evidence: {method_id}")

    if not unavailable:
        for mapping in trace_map:
            method_id = mapping["method_id"]
            evidence_ids = sorted({str(item["run_id"]) for item in evidence_by_method.get(method_id, [])})
            audit_mappings.append({"requirement_id": mapping["requirement_id"], "test_case_id": mapping["test_case_id"], "file_id": mapping["file_id"], "method_id": method_id, "evidence_ids": evidence_ids})

    base_errors = list(errors)
    expected_verdict = "NOT_RUNNABLE" if unavailable else "FAIL" if base_errors else "PASS"
    if document["final_verdict"] != expected_verdict:
        _append(errors, "VERDICT_MISMATCH", "/final_verdict", f"claimed final verdict {document['final_verdict']} does not match {expected_verdict}")
    return errors, audit_mappings


def _check(document: object, require_execution: bool, schema_path: Path) -> dict[str, object]:
    summary = _summary(document)
    schema_errors = _schema_report(document, schema_path)
    if schema_errors:
        return _report(schema_errors, [], summary)
    assert isinstance(document, dict)
    errors, mappings = _semantic_check(document, require_execution)
    return _report(errors, mappings, summary)


def check(document: dict[str, object], require_execution: bool = False) -> dict[str, object]:
    """Return a deterministic SDD trace audit without mutating *document*."""
    return _check(document, require_execution, DEFAULT_SCHEMA)


def _emit(report: dict[str, object]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit(_report([_diagnostic("argument_error", "", f"argument error: {message}")], [], _summary({})))
        raise SystemExit(2)


def main() -> int:
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument("document")
    parser.add_argument("--require-execution", action="store_true")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        document = json.loads(Path(args.document).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _emit(_report([_diagnostic("input_error", "", f"document unreadable: {error}")], [], _summary({})))
        return 2
    try:
        report = _check(document, args.require_execution, args.schema)
    except Exception:  # noqa: BLE001 - the CLI must never leak a traceback
        _emit(_report([_diagnostic("runtime_error", "", "runtime error during trace check")], [], _summary(document)))
        return 2
    if any(error["code"] in {"invalid_input_schema", "schema_error"} for error in report["errors"]):
        exit_code = 2
    else:
        exit_code = 0 if report["valid"] else 1
    _emit(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
