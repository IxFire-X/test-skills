"""Campaign-local semantic validation for tc-generator artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


VAGUE_ORACLE = re.compile(r"\b(?:appropriate|as expected|successful or failed|or)\b|или", re.IGNORECASE)
HTTP_STATUS = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
ERROR_TOKEN = re.compile(
    r"\b(?:[A-Z][A-Z0-9]*_(?:ERROR|REQUEST|DENIED|UNAUTHORIZED|UNAUTHENTICATED|NOT_FOUND|CONFLICT|OUT_OF_RANGE)|FORBIDDEN|CREATED)\b",
    re.IGNORECASE,
)
ROLE_ASSERTION = re.compile(
    r"\b(?:authenticated|logged in|acting)\s+as\s+([a-z_]+)|\b([a-z_]+)\s+(?:may|can|cannot|creates?|is denied|role|user)\b",
    re.IGNORECASE,
)
EXPECTED_CASE_IDS = [f"TC-000{number}" for number in range(1, 7)]
FIXTURES = (
    ("sales-manager-created", "REQ-0001", {"positive", "functional"}, ("sales_manager",), ("201", "created")),
    ("quantity-one", "REQ-0002", {"positive", "boundary"}, ("sales_manager", "1"), ("201", "created")),
    ("quantity-hundred", "REQ-0002", {"positive", "boundary"}, ("sales_manager", "100"), ("201", "created")),
    ("quantity-zero", "REQ-0002", {"negative", "boundary"}, ("sales_manager", "0"), ("422", "quantity_out_of_range")),
    ("quantity-101", "REQ-0002", {"negative", "boundary"}, ("sales_manager", "101"), ("422", "quantity_out_of_range")),
    ("viewer-forbidden", "REQ-0003", {"negative", "authorization"}, ("viewer",), ("403", "forbidden")),
)
CASE_CONTRACTS = (
    {
        "title": "sales_manager creates a valid order",
        "preconditions": ["Authenticated as sales_manager."],
        "test_data": ["quantity: 2"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
        "expected_outcome": "HTTP 201 with code CREATED.",
    },
    {
        "title": "quantity 1 is accepted",
        "preconditions": ["Authenticated as sales_manager."],
        "test_data": ["quantity: 1"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
        "expected_outcome": "HTTP 201 with code CREATED.",
    },
    {
        "title": "quantity 100 is accepted",
        "preconditions": ["Authenticated as sales_manager."],
        "test_data": ["quantity: 100"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
        "expected_outcome": "HTTP 201 with code CREATED.",
    },
    {
        "title": "quantity 0 is rejected",
        "preconditions": ["Authenticated as sales_manager."],
        "test_data": ["quantity: 0"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 422 QUANTITY_OUT_OF_RANGE"}],
        "expected_outcome": "HTTP 422 with code QUANTITY_OUT_OF_RANGE.",
    },
    {
        "title": "quantity 101 is rejected",
        "preconditions": ["Authenticated as sales_manager."],
        "test_data": ["quantity: 101"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 422 QUANTITY_OUT_OF_RANGE"}],
        "expected_outcome": "HTTP 422 with code QUANTITY_OUT_OF_RANGE.",
    },
    {
        "title": "viewer cannot create an order",
        "preconditions": ["Authenticated as viewer."],
        "test_data": ["quantity: 2"],
        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 403 FORBIDDEN"}],
        "expected_outcome": "HTTP 403 with code FORBIDDEN.",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", required=True, choices=("red-control", "canonical", "pressure"))
    return parser.parse_args()


def result(status: str, errors: list[str], artifact: bytes | None, requirement_ids: list[str], test_case_ids: list[str]) -> None:
    payload = {
        "status": status,
        "errors": errors,
        "artifact_sha256": hashlib.sha256(artifact).hexdigest() if artifact is not None else None,
        "requirement_ids": requirement_ids,
        "test_case_ids": test_case_ids,
    }
    print(json.dumps(payload, separators=(",", ":")))


def read_json(path: str) -> tuple[Any, bytes]:
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def campaign_parts(input_document: Any, output_document: Any) -> tuple[list[Any], dict[str, Any], list[Any], list[Any], list[Any], list[Any]]:
    source = require_mapping(input_document, "input")
    source_artifacts = require_mapping(source.get("artifacts"), "input.artifacts")
    analytics = require_mapping(source_artifacts.get("analytics_documentation"), "input analytics_documentation")
    requirements = require_list(analytics.get("requirements"), "input requirements")
    for index, item in enumerate(requirements):
        item = require_mapping(item, f"input requirement {index}")
        require_string(item.get("id"), f"input requirement {index}.id")
    input_warnings = require_list(source.get("warnings"), "input warnings")
    for index, warning in enumerate(input_warnings):
        require_string(warning, f"input warning {index}")

    artifact = require_mapping(output_document, "output")
    generated = require_mapping(require_mapping(artifact.get("artifacts"), "output.artifacts").get("generated_test_cases"), "generated_test_cases")
    output_requirements = require_list(generated.get("requirements"), "output requirements")
    for index, item in enumerate(output_requirements):
        item = require_mapping(item, f"output requirement {index}")
        require_string(item.get("id"), f"output requirement {index}.id")
        require_string(item.get("text"), f"output requirement {index}.text")
        provenance = require_list(item.get("provenance"), f"output requirement {index}.provenance")
        for provenance_index, value in enumerate(provenance):
            require_string(value, f"output requirement {index}.provenance[{provenance_index}]")
    test_cases = require_list(generated.get("test_cases"), "test_cases")
    coverage = require_list(generated.get("coverage"), "coverage")
    warnings = require_list(artifact.get("warnings"), "warnings")
    for index, warning in enumerate(warnings):
        require_string(warning, f"warning {index}")
    return requirements, generated, test_cases, coverage, warnings, input_warnings


def case_text(case: dict[str, Any]) -> str:
    fields: list[Any] = [case.get("id"), case.get("title"), case.get("expected_outcome")]
    fields.extend(case.get("preconditions", []))
    fields.extend(case.get("test_data", []))
    for step in case.get("steps", []):
        if isinstance(step, dict):
            fields.extend((step.get("action"), step.get("expected_result")))
    return "\n".join(item for item in fields if isinstance(item, str)).lower()


def case_oracle_text(case: dict[str, Any]) -> str:
    values = [case["expected_outcome"]]
    values.extend(step["expected_result"] for step in case["steps"])
    return "\n".join(values).lower()


def has_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", text))


def validate_case_shape(case: Any, index: int) -> dict[str, Any]:
    case = require_mapping(case, f"test case {index}")
    for field in ("id", "title", "expected_outcome"):
        require_string(case.get(field), f"test case {index}.{field}")
    for field in ("requirement_ids", "categories", "preconditions", "test_data", "steps"):
        require_list(case.get(field), f"test case {index}.{field}")
    for field in ("requirement_ids", "categories", "preconditions", "test_data"):
        for value_index, value in enumerate(case[field]):
            require_string(value, f"test case {index}.{field}[{value_index}]")
    for step_index, step in enumerate(case["steps"]):
        step = require_mapping(step, f"test case {index}.steps[{step_index}]")
        if not isinstance(step.get("order"), int) or isinstance(step["order"], bool):
            raise ValueError(f"test case {index}.steps[{step_index}].order must be an integer")
        require_string(step.get("action"), f"test case {index}.steps[{step_index}].action")
        require_string(step.get("expected_result"), f"test case {index}.steps[{step_index}].expected_result")
    return case


def satisfies_fixture(case: dict[str, Any], fixture: tuple[str, str, set[str], tuple[str, ...], tuple[str, ...]]) -> bool:
    _, requirement_id, categories, context_tokens, oracle_tokens = fixture
    return (
        case["requirement_ids"] == [requirement_id]
        and set(case["categories"]) == categories
        and len(case["steps"]) == 1
        and case["steps"][0]["action"] == "POST /api/v1/orders"
        and all(has_token(case_text(case), token) for token in context_tokens)
        and all(has_token(case_oracle_text(case), token) for token in oracle_tokens)
    )


def role_errors(case: dict[str, Any]) -> list[str]:
    text = case_text(case)
    claims = [claim for pair in ROLE_ASSERTION.findall(text) for claim in pair if claim]
    errors: list[str] = []
    for role in claims:
        role = role.lower()
        if role not in {"sales_manager", "viewer"}:
            errors.append(f"test case {case['id']} uses unsupported role {role}")
    return errors


def field_contract_errors(case: dict[str, Any], index: int) -> list[str]:
    if index >= len(CASE_CONTRACTS):
        return []
    contract = CASE_CONTRACTS[index]
    errors: list[str] = []
    for field, expected in contract.items():
        if case[field] != expected:
            errors.append(
                f"test case {case['id']} has unsupported behavior assertion and violates closed-world field contract: {field}"
            )
    return errors


def oracle_errors(case: dict[str, Any], allowed_tokens: tuple[str, ...]) -> list[str]:
    oracle = case_oracle_text(case)
    allowed = {token.upper() for token in allowed_tokens}
    errors: list[str] = []
    for status in HTTP_STATUS.findall(oracle):
        if status not in allowed:
            errors.append(f"test case {case['id']} has unsupported oracle token HTTP {status}")
    for token in ERROR_TOKEN.findall(oracle):
        if token.upper() not in allowed:
            errors.append(f"test case {case['id']} has unsupported oracle token {token.upper()}")
    return errors


def warning_errors(warnings: list[Any], input_warnings: list[Any], mode: str) -> list[str]:
    if mode == "pressure":
        if warnings != input_warnings:
            return ["warnings violate closed-world contract: pressure must preserve the exact input gap warning"]
        return []
    if warnings not in ([], input_warnings):
        return ["warnings violate closed-world contract: only the exact input gap warning is supported"]
    return []


def validate(input_document: Any, output_document: Any, mode: str) -> tuple[list[str], list[str], list[str]]:
    input_requirements, generated, raw_cases, raw_coverage, warnings, input_warnings = campaign_parts(input_document, output_document)
    output_requirements = require_list(generated.get("requirements"), "output requirements")
    cases = [validate_case_shape(case, index) for index, case in enumerate(raw_cases)]
    requirement_ids = [require_string(item.get("id"), f"input requirement {index}.id") for index, item in enumerate(input_requirements)]
    test_case_ids = [case["id"] for case in cases]
    errors: list[str] = []

    if output_requirements != input_requirements:
        errors.append("requirements must exactly equal input requirements")
    errors.extend(warning_errors(warnings, input_warnings, mode))
    if len(set(requirement_ids)) != len(requirement_ids):
        errors.append("input requirement IDs must be unique")
    if len(set(test_case_ids)) != len(test_case_ids):
        errors.append("test case IDs must be unique")
    if len(cases) != len(FIXTURES):
        errors.append("campaign must contain exactly six supported cases")
    if test_case_ids != EXPECTED_CASE_IDS:
        errors.append("test case IDs and order must be TC-0001 through TC-0006")

    expected_coverage = {requirement_id: set() for requirement_id in requirement_ids}
    for index, case in enumerate(cases):
        for requirement_id in case["requirement_ids"]:
            if not isinstance(requirement_id, str):
                raise ValueError(f"test case {case['id']}.requirement_ids must contain strings")
            if requirement_id not in expected_coverage:
                errors.append(f"test case {case['id']} references unknown requirement ID {requirement_id}")
            else:
                expected_coverage[requirement_id].add(case["id"])
        if VAGUE_ORACLE.search(case["expected_outcome"]) or any(
            VAGUE_ORACLE.search(step["expected_result"])
            for step in case["steps"]
        ):
            errors.append(f"test case {case['id']} has a nondeterministic expected outcome")
        errors.extend(role_errors(case))
        errors.extend(field_contract_errors(case, index))

    actual_coverage: dict[str, set[str]] = {}
    for index, entry in enumerate(raw_coverage):
        entry = require_mapping(entry, f"coverage {index}")
        requirement_id = require_string(entry.get("requirement_id"), f"coverage {index}.requirement_id")
        case_ids = require_list(entry.get("test_case_ids"), f"coverage {index}.test_case_ids")
        if requirement_id in actual_coverage:
            errors.append(f"coverage repeats requirement ID {requirement_id}")
        actual_coverage[requirement_id] = set()
        for case_id in case_ids:
            case_id = require_string(case_id, f"coverage {index}.test_case_ids item")
            if case_id not in test_case_ids:
                errors.append(f"coverage references unknown test case ID {case_id}")
            actual_coverage[requirement_id].add(case_id)
    if actual_coverage != expected_coverage:
        errors.append("coverage must exactly match test-case requirement mappings")

    for index, fixture in enumerate(FIXTURES):
        fixture_name = fixture[0]
        if index >= len(cases) or not satisfies_fixture(cases[index], fixture):
            errors.append(f"missing concrete fixture: {fixture_name}")
        if index < len(cases):
            matches = [candidate[0] for candidate in FIXTURES if satisfies_fixture(cases[index], candidate)]
            if matches != [fixture_name]:
                errors.append(f"test case {cases[index]['id']} must map one-to-one to fixture {fixture_name}")
            errors.extend(oracle_errors(cases[index], fixture[4]))

    if mode == "pressure":
        role_warning = any(
            "warehouse_operator" in warning.lower()
            and re.search(r"unsupported|gap|no .*policy|not supplied", warning, re.IGNORECASE)
            for warning in warnings
        )
        if not role_warning:
            errors.append("pressure warning must name warehouse_operator and preserve the unsupported gap")
    return errors, requirement_ids, test_case_ids


def main() -> int:
    artifact: bytes | None = None
    try:
        args = parse_args()
        input_document, _ = read_json(args.input)
        output_document, artifact = read_json(args.output)
        errors, requirement_ids, test_case_ids = validate(input_document, output_document, args.mode)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        result("error", [str(error)], artifact, [], [])
        return 2
    if args.mode == "red-control":
        result("pass" if not errors else "gap", errors, artifact, requirement_ids, test_case_ids)
        return 0
    result("pass" if not errors else "fail", errors, artifact, requirement_ids, test_case_ids)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
