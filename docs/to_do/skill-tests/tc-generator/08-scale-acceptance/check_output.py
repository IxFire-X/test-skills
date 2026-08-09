#!/usr/bin/env python3
"""Semantic acceptance checker for the isolated tc-generator scale fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON in {path}: {error}") from error


def _shape_error(document: Any, label: str) -> list[str]:
    if not isinstance(document, dict):
        return [f"{label} must be a JSON object"]
    return []


def _output_sections(document: Any) -> tuple[dict[str, Any], list[str]]:
    errors = _shape_error(document, "output")
    if errors:
        return {}, errors
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}, ["output.artifacts must be an object"]
    generated = artifacts.get("generated_test_cases")
    if not isinstance(generated, dict):
        return {}, ["output.artifacts.generated_test_cases must be an object"]
    for field in ("requirements", "test_cases", "coverage"):
        if not isinstance(generated.get(field), list):
            return {}, [f"output.artifacts.generated_test_cases.{field} must be an array"]
    if not isinstance(document.get("warnings"), list):
        return {}, ["output.warnings must be an array"]
    for index, requirement in enumerate(generated["requirements"]):
        if not isinstance(requirement, dict):
            return {}, [f"output requirement {index} must be an object"]
    for index, case in enumerate(generated["test_cases"]):
        if not isinstance(case, dict):
            return {}, [f"output test case {index} must be an object"]
        expected_types = {
            "id": str,
            "requirement_ids": list,
            "title": str,
            "categories": list,
            "priority": str,
            "preconditions": list,
            "test_data": list,
            "steps": list,
            "expected_outcome": str,
        }
        for field, expected_type in expected_types.items():
            if not isinstance(case.get(field), expected_type):
                return {}, [f"output test case {index}.{field} must be a {expected_type.__name__}"]
    for index, coverage in enumerate(generated["coverage"]):
        if not isinstance(coverage, dict):
            return {}, [f"output coverage {index} must be an object"]
        if not isinstance(coverage.get("requirement_id"), str) or not isinstance(coverage.get("test_case_ids"), list):
            return {}, [f"output coverage {index} must contain string requirement_id and array test_case_ids"]
    return generated, []


def _matrix_atoms(matrix: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    atoms: list[dict[str, Any]] = []
    for group in ("happy", "positive_boundary", "negative_boundary", "authorization"):
        value = matrix.get(group)
        if not isinstance(value, list):
            errors.append(f"matrix.{group} must be an array")
        else:
            atoms.extend(value)
    if matrix.get("expected_total") != len(atoms):
        errors.append("matrix.expected_total must equal the number of atoms")
    if any(not isinstance(atom, dict) for atom in atoms):
        errors.append("each matrix atom must be an object")
    return atoms, errors


def _expected_coverage(requirements: list[dict[str, Any]], atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": requirement["id"],
            "test_case_ids": [atom["id"] for atom in atoms if atom["requirement_id"] == requirement["id"]],
        }
        for requirement in requirements
    ]


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text, flags=re.IGNORECASE) is not None


def evaluate(input_document: Any, matrix: Any, output_document: Any) -> tuple[list[str], list[str]]:
    """Return (shape_errors, semantic_errors) without performing JSON Schema validation."""
    shape_errors = _shape_error(input_document, "input") + _shape_error(matrix, "matrix")
    generated, output_errors = _output_sections(output_document)
    shape_errors.extend(output_errors)
    if shape_errors:
        return shape_errors, []

    analytics = input_document.get("artifacts", {}).get("analytics_documentation")
    requirements = analytics.get("requirements") if isinstance(analytics, dict) else None
    if not isinstance(requirements, list) or any(not isinstance(item, dict) for item in requirements):
        return ["input.artifacts.analytics_documentation.requirements must be an array of objects"], []
    if not isinstance(input_document.get("warnings"), list):
        return ["input.warnings must be an array"], []
    atoms, matrix_errors = _matrix_atoms(matrix)
    if matrix_errors:
        return matrix_errors, []

    semantic_errors: list[str] = []
    expected_ids = [f"TC-{number:04d}" for number in range(1, len(atoms) + 1)]
    requirement_ids = [item.get("id") for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)) or any(not isinstance(item, str) for item in requirement_ids):
        return ["input requirements must have unique string IDs"], []

    if output_document.get("schema_version") != "2.1.0":
        semantic_errors.append("schema_version must be 2.1.0")
    if output_document.get("stage") != "tc-generator":
        semantic_errors.append("stage must be tc-generator")
    if generated["requirements"] != requirements:
        semantic_errors.append("generated requirements must copy the input requirements exactly")
    if output_document["warnings"] != input_document["warnings"] or output_document["warnings"] != matrix.get("warnings"):
        semantic_errors.append("warnings must copy the exact input and matrix warning array")

    cases = generated["test_cases"]
    if len(cases) != len(atoms):
        semantic_errors.append(f"expected exactly {len(atoms)} test cases, got {len(cases)}")
    case_ids = [case.get("id") if isinstance(case, dict) else None for case in cases]
    if case_ids != expected_ids:
        semantic_errors.append("test case IDs must be the exact ordered TC-0001..TC-0036 sequence")
    if len(case_ids) != len(set(case_ids)):
        semantic_errors.append("test case IDs must be unique")

    for index, (atom, case) in enumerate(zip(atoms, cases), start=1):
        prefix = f"TC-{index:04d}"
        if not isinstance(case, dict):
            semantic_errors.append(f"{prefix} must be an object")
            continue
        if case.get("requirement_ids") != [atom.get("requirement_id")]:
            semantic_errors.append(f"{prefix} must link exactly to {atom.get('requirement_id')}")
        if case.get("title") != atom.get("title"):
            semantic_errors.append(f"{prefix} title does not match its expected atom")
        if case.get("preconditions") != atom.get("preconditions"):
            semantic_errors.append(f"{prefix} preconditions do not match its expected atom")
        if case.get("categories") != atom.get("categories"):
            semantic_errors.append(f"{prefix} categories do not match its expected atom")
        if case.get("priority") != atom.get("priority"):
            semantic_errors.append(f"{prefix} priority does not match its expected atom")
        expected_data = [f"{field}: {value}" for field, value in atom.get("test_data", {}).items()]
        if case.get("test_data") != expected_data:
            semantic_errors.append(f"{prefix} test_data does not match its expected atom")
        expected_step = {
            "order": 1,
            "action": f"{atom.get('method')} {atom.get('path')}",
            "expected_result": f"HTTP {atom.get('expected_status')} {atom.get('expected_code')}",
        }
        if case.get("steps") != [expected_step]:
            semantic_errors.append(f"{prefix} must have its exact one-step action and oracle")
        if case.get("expected_outcome") != atom.get("expected_outcome"):
            semantic_errors.append(f"{prefix} expected_outcome does not match its expected oracle")
        if atom.get("kind") == "authorization":
            role = atom.get("role")
            role_text = " ".join(
                value
                for value in [case.get("title"), *case.get("preconditions", [])]
                if isinstance(value, str)
            )
            if not isinstance(role, str) or not _has_token(role_text, role):
                semantic_errors.append(f"{prefix} must name the exact denied role {role!r} in title or preconditions")
        case_without_warning = json.dumps(case, ensure_ascii=False, sort_keys=True).lower()
        if "claims_adjuster" in case_without_warning:
            semantic_errors.append(f"{prefix} invents an unsupported claims_adjuster case")

    expected_coverage = _expected_coverage(requirements, atoms)
    if generated["coverage"] != expected_coverage:
        semantic_errors.append("coverage must be the exact ordered reverse requirement-to-case mapping")
    return [], semantic_errors


def _result(status: str, errors: list[str], output_raw: bytes | None, output_document: Any) -> dict[str, Any]:
    generated = (
        output_document.get("artifacts", {}).get("generated_test_cases", {})
        if isinstance(output_document, dict)
        else {}
    )
    return {
        "status": status,
        "errors": errors,
        "artifact_sha256": hashlib.sha256(output_raw).hexdigest() if output_raw is not None else None,
        "requirement_count": len(generated.get("requirements", [])) if isinstance(generated.get("requirements"), list) else 0,
        "test_case_count": len(generated.get("test_cases", [])) if isinstance(generated.get("test_cases"), list) else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output_raw: bytes | None = None
    output_document: Any = {}
    try:
        input_document, _ = _load_json(args.input)
        matrix, _ = _load_json(args.matrix)
        output_document, output_raw = _load_json(args.output)
    except ValueError as error:
        print(json.dumps(_result("error", [str(error)], output_raw, output_document), ensure_ascii=False, sort_keys=True))
        return 2

    shape_errors, semantic_errors = evaluate(input_document, matrix, output_document)
    if shape_errors:
        print(json.dumps(_result("error", shape_errors, output_raw, output_document), ensure_ascii=False, sort_keys=True))
        return 2
    if semantic_errors:
        print(json.dumps(_result("fail", semantic_errors, output_raw, output_document), ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(_result("pass", [], output_raw, output_document), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
