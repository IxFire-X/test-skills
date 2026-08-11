#!/usr/bin/env python3
"""Build a deterministic requirement-to-run trace document from pipeline artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    "requirements": ROOT / "schemas" / "context-marker-output.schema.json",
    "test_cases": ROOT / "schemas" / "tc-generator-output.schema.json",
    "automation": ROOT / "schemas" / "tc-to-autotest-output.schema.json",
    "run_result": ROOT / "schemas" / "run-tests-output.schema.json",
    "trace": ROOT / "schemas" / "trace-document.schema.json",
}


class BuildError(ValueError):
    """A readable or schema-valid input violates the trace construction contract."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read JSON {path}: {error}") from error


def _schema_errors(document: Any, schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    return [f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}" for error in errors]


def _require_schema(document: Any, name: str) -> None:
    errors = _schema_errors(document, SCHEMAS[name])
    if errors:
        raise BuildError(f"{name} artifact is schema-invalid: {'; '.join(errors)}")


def _unique_by_id(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = item["id"]
        if identifier in indexed:
            raise BuildError(f"duplicate {label} id: {identifier}")
        indexed[identifier] = item
    return indexed


def _validate_topology(
    test_cases: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    files: list[dict[str, Any]],
    methods: list[dict[str, Any]],
    requirement_ids: set[str],
) -> list[dict[str, str]]:
    cases_by_id = _unique_by_id(test_cases, "test case")
    files_by_id = _unique_by_id(files, "file")
    methods_by_id = _unique_by_id(methods, "method")

    for case in test_cases:
        unknown = set(case["requirement_ids"]) - requirement_ids
        if unknown:
            raise BuildError(f"test case {case['id']} references unknown requirements: {sorted(unknown)}")

    matrix_by_case: dict[str, dict[str, Any]] = {}
    for entry in matrix:
        case_id = entry["test_case_id"]
        if case_id in matrix_by_case:
            raise BuildError(f"duplicate automation matrix test case: {case_id}")
        if case_id not in cases_by_id:
            raise BuildError(f"automation matrix references unknown test case: {case_id}")
        unknown_files = set(entry["generated_file_ids"]) - files_by_id.keys()
        unknown_methods = set(entry["generated_method_ids"]) - methods_by_id.keys()
        if unknown_files or unknown_methods:
            raise BuildError(
                f"automation matrix {case_id} has unknown files/methods: "
                f"{sorted(unknown_files)} {sorted(unknown_methods)}"
            )
        matrix_by_case[case_id] = entry

    if set(matrix_by_case) != set(cases_by_id):
        missing = sorted(set(cases_by_id) - set(matrix_by_case))
        raise BuildError(f"automation matrix does not cover all test cases: {missing}")

    used_methods: set[str] = set()
    used_files: set[str] = set()
    mappings: list[dict[str, str]] = []
    for case in test_cases:
        entry = matrix_by_case[case["id"]]
        for method_id in entry["generated_method_ids"]:
            method = methods_by_id[method_id]
            if case["id"] not in method["test_case_ids"]:
                raise BuildError(f"method {method_id} does not declare matrix test case {case['id']}")
            if method["file_id"] not in entry["generated_file_ids"]:
                raise BuildError(f"method {method_id} file is absent from matrix entry {case['id']}")
            if not set(case["requirement_ids"]).issubset(method["requirement_ids"]):
                raise BuildError(f"method {method_id} omits requirements for test case {case['id']}")
            used_methods.add(method_id)
            used_files.add(method["file_id"])
            for requirement_id in case["requirement_ids"]:
                mappings.append(
                    {
                        "requirement_id": requirement_id,
                        "test_case_id": case["id"],
                        "file_id": method["file_id"],
                        "method_id": method_id,
                    }
                )

    if used_methods != set(methods_by_id):
        raise BuildError(f"orphan generated methods: {sorted(set(methods_by_id) - used_methods)}")
    if used_files != set(files_by_id):
        raise BuildError(f"orphan generated files: {sorted(set(files_by_id) - used_files)}")

    for method in methods:
        if method["file_id"] not in files_by_id:
            raise BuildError(f"method {method['id']} references unknown file: {method['file_id']}")
        unknown_cases = set(method["test_case_ids"]) - cases_by_id.keys()
        unknown_requirements = set(method["requirement_ids"]) - requirement_ids
        if unknown_cases or unknown_requirements:
            raise BuildError(
                f"method {method['id']} has unknown cases/requirements: "
                f"{sorted(unknown_cases)} {sorted(unknown_requirements)}"
            )
        expected_requirements: list[str] = []
        for case_id in method["test_case_ids"]:
            for requirement_id in cases_by_id[case_id]["requirement_ids"]:
                if requirement_id not in expected_requirements:
                    expected_requirements.append(requirement_id)
            if method["id"] not in matrix_by_case[case_id]["generated_method_ids"]:
                raise BuildError(f"method {method['id']} test case {case_id} is absent from automation matrix")
        if method["requirement_ids"] != expected_requirements:
            raise BuildError(
                f"method {method['id']} requirement ownership differs from its test cases: "
                f"expected {expected_requirements}, got {method['requirement_ids']}"
            )

    return mappings


def _execution(run_result: dict[str, Any], method_ids: set[str]) -> tuple[bool, dict[str, Any], str]:
    verdict = run_result["verdict"]
    evidence = run_result["execution_evidence"]
    if verdict == "NOT_RUNNABLE":
        if run_result["evidence_authoritative"] or run_result["run_id"] is not None or evidence:
            raise BuildError("NOT_RUNNABLE result contains fabricated execution evidence")
        missing = run_result["environment"].get("missing") or []
        reason = "runner could not execute" + (f": {', '.join(missing)}" if missing else "")
        return False, {"verdict": verdict, "reason": reason, "evidence": [], "allowed_skips": []}, verdict

    if not run_result["evidence_authoritative"] or not run_result["run_id"]:
        raise BuildError("PASS/FAIL result lacks authoritative runner evidence")
    evidence_method_ids = [item["method_id"] for item in evidence]
    if len(evidence_method_ids) != len(set(evidence_method_ids)):
        raise BuildError("runner evidence contains duplicate method IDs")
    if set(evidence_method_ids) != method_ids:
        missing = sorted(method_ids - set(evidence_method_ids))
        foreign = sorted(set(evidence_method_ids) - method_ids)
        raise BuildError(f"runner evidence does not exactly cover generated methods; missing={missing}, foreign={foreign}")
    if any(item["run_id"] != run_result["run_id"] for item in evidence):
        raise BuildError("runner evidence run_id differs from the top-level run_id")
    if verdict == "PASS" and any(item["status"] != "passed" for item in evidence):
        raise BuildError("PASS result contains non-passing method evidence")
    if verdict == "FAIL" and not any(item["status"] in {"failed", "error"} for item in evidence):
        raise BuildError("FAIL result contains no failed or error method evidence")

    command = run_result["target"].get("command")
    runner = run_result["target"].get("runner")
    exit_code = run_result.get("exit_code")
    if not isinstance(command, str) or not command.strip() or runner == "not_applicable" or not isinstance(exit_code, int):
        raise BuildError("runner result lacks command, runner, or exit code")
    return (
        True,
        {
            "verdict": verdict,
            "reason": f"runner-produced {verdict}",
            "command": command,
            "runner": runner,
            "exit_code": exit_code,
            "evidence": evidence,
            "allowed_skips": [],
        },
        verdict,
    )


def build_trace(
    requirements_document: dict[str, Any],
    test_cases_document: dict[str, Any],
    automation_document: dict[str, Any],
    run_result: dict[str, Any],
) -> dict[str, Any]:
    for name, document in (
        ("requirements", requirements_document),
        ("test_cases", test_cases_document),
        ("automation", automation_document),
        ("run_result", run_result),
    ):
        _require_schema(document, name)

    context_requirements = requirements_document["artifacts"]["analytics_documentation"]["requirements"]
    generated = test_cases_document["artifacts"]["generated_test_cases"]
    if context_requirements != generated["requirements"]:
        raise BuildError("requirements drift between context-marker and tc-generator artifacts")

    requirement_ids = {item["id"] for item in context_requirements}
    if len(requirement_ids) != len(context_requirements):
        raise BuildError("duplicate requirement IDs")

    artifacts = automation_document["artifacts"]
    mappings = _validate_topology(
        generated["test_cases"],
        artifacts["automation_matrix"],
        artifacts["generated_test_files"],
        artifacts["generated_test_methods"],
        requirement_ids,
    )
    execution_required, execution, final_verdict = _execution(
        run_result, {method["id"] for method in artifacts["generated_test_methods"]}
    )

    trace = {
        "schema_version": "2.1.0",
        "requirements": [
            {"id": requirement["id"], "provenance": requirement["provenance"]}
            for requirement in context_requirements
        ],
        "test_cases": [
            {"id": case["id"], "requirement_ids": case["requirement_ids"]}
            for case in generated["test_cases"]
        ],
        "generated_files": [
            {"id": generated_file["id"], "path": generated_file["path"]}
            for generated_file in artifacts["generated_test_files"]
        ],
        "methods": [
            {
                "id": method["id"],
                "file_id": method["file_id"],
                "name": method["name"],
                "test_case_ids": method["test_case_ids"],
                "requirement_ids": method["requirement_ids"],
            }
            for method in artifacts["generated_test_methods"]
        ],
        "trace_map": mappings,
        "execution_required": execution_required,
        "execution": execution,
        "final_verdict": final_verdict,
    }
    errors = _schema_errors(trace, SCHEMAS["trace"])
    if errors:
        raise BuildError(f"constructed trace is schema-invalid: {'; '.join(errors)}")
    return trace


def _write_new(path: Path, document: dict[str, Any]) -> None:
    if path.exists():
        raise BuildError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--test-cases", required=True, type=Path)
    parser.add_argument("--automation-artifact", required=True, type=Path)
    parser.add_argument("--run-result", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        trace = build_trace(
            _load_json(args.requirements),
            _load_json(args.test_cases),
            _load_json(args.automation_artifact),
            _load_json(args.run_result),
        )
        _write_new(args.output, trace)
    except BuildError as error:
        print(json.dumps({"status": "invalid", "errors": [str(error)]}, ensure_ascii=False))
        return 1
    except (RuntimeError, OSError) as error:
        print(json.dumps({"status": "error", "errors": [str(error)]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "valid", "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
