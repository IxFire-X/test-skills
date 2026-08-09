#!/usr/bin/env python3
"""Deterministic semantic checker for the blind tc-generator scale scenario."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHAPE, SEMANTIC, PASS = 2, 1, 0


@dataclass(frozen=True)
class Atom:
    requirement_id: str
    categories: tuple[str, ...]
    role: str
    method: str
    path: str
    status: int
    code: str
    allowed_test_data: tuple[tuple[str, ...], ...]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def shape_errors(output: Any) -> list[str]:
    if not isinstance(output, dict):
        return ["output must be an object"]
    if output.get("schema_version") != "2.1.0":
        return ["output.schema_version must be '2.1.0'"]
    if output.get("stage") != "tc-generator":
        return ["output.stage must be 'tc-generator'"]
    generated = output.get("artifacts", {}).get("generated_test_cases") if isinstance(output.get("artifacts"), dict) else None
    if not isinstance(generated, dict):
        return ["output.artifacts.generated_test_cases must be an object"]
    for key in ("requirements", "test_cases", "coverage"):
        if not isinstance(generated.get(key), list):
            return [f"output generated_test_cases.{key} must be a list"]
    if not isinstance(output.get("warnings"), list):
        return ["output.warnings must be a list"]
    for index, case in enumerate(generated["test_cases"]):
        if not isinstance(case, dict):
            return [f"output test case {index} must be an object"]
        for key in ("id", "requirement_ids", "title", "categories", "priority", "preconditions", "test_data", "steps", "expected_outcome"):
            if key not in case:
                return [f"output test case {index} missing {key}"]
        for key in ("requirement_ids", "categories", "preconditions", "test_data", "steps"):
            if not isinstance(case[key], list):
                return [f"output test case {index}.{key} must be a list"]
    return []


def atoms_from_oracle(oracle: dict[str, Any]) -> tuple[Atom, ...]:
    atoms: list[Atom] = []
    for requirement_id, role, method, path, status, code, sample in oracle["operations"]:
        atoms.append(Atom(requirement_id, ("positive", "functional"), role, method, path, status, code, (tuple(sample),)))
    for requirement_id, role, field, lower, upper, outside_lower, outside_upper, method, path, good_status, good_code, bad_code in oracle["ranges"]:
        for value in (lower, upper):
            atoms.append(Atom(requirement_id, ("positive", "boundary"), role, method, path, good_status, good_code, ((f"{field}: {value}",),)))
    for requirement_id, role, field, lower, upper, outside_lower, outside_upper, method, path, good_status, good_code, bad_code in oracle["ranges"]:
        for value in (outside_lower, outside_upper):
            atoms.append(Atom(requirement_id, ("negative", "boundary"), role, method, path, 422, bad_code, ((f"{field}: {value}",),)))
    for requirement_id, role, method, path, sample in oracle["denials"]:
        atoms.append(Atom(requirement_id, ("negative", "authorization"), role, method, path, 403, "FORBIDDEN", (tuple(sample),)))
    return tuple(atoms)


def free_text(case: dict[str, Any]) -> str:
    bits = [case["title"], *case["preconditions"], case["expected_outcome"]]
    for step in case["steps"]:
        if not isinstance(step, dict) or not isinstance(step.get("action"), str) or not isinstance(step.get("expected_result"), str):
            return ""
        bits.extend((step["action"], step["expected_result"]))
    return "\n".join(bits)


def parse_case(case: dict[str, Any], expected: Atom) -> str | None:
    if not isinstance(case["id"], str) or not case["id"].startswith("TC-"):
        return "has invalid id"
    if case["requirement_ids"] != [expected.requirement_id]:
        return "must map to the expected single requirement"
    if tuple(case["categories"]) != expected.categories or case["priority"] != "HIGH":
        return "has unsupported categories or priority"
    if not all(isinstance(value, str) for value in case["preconditions"] + case["test_data"]):
        return "has non-string preconditions or test data"
    if tuple(case["test_data"]) not in expected.allowed_test_data:
        return "uses test data not supported by this input requirement"
    if len(case["steps"]) != 1:
        return "must contain exactly one step"
    step = case["steps"][0]
    if not isinstance(step, dict) or step.get("order") != 1:
        return "must have step order 1"
    if step.get("action") != f"{expected.method} {expected.path}" or step.get("expected_result") != f"HTTP {expected.status} {expected.code}":
        return "does not match the expected endpoint or response oracle"
    return None


def contamination_errors(case: dict[str, Any], atom: Atom, oracle: dict[str, Any]) -> list[str]:
    text = free_text(case)
    if not text:
        return ["has invalid free-text step fields"]
    errors: list[str] = []
    if re.search(r"\bclaims_adjuster\b", text, re.IGNORECASE):
        errors.append("mentions unsupported role claims_adjuster")
    roles = {row[1] for group in ("operations", "ranges", "denials") for row in oracle[group]}
    found_known_roles = {role for role in roles if re.search(rf"\b{re.escape(role)}\b", text, re.IGNORECASE)}
    if found_known_roles != {atom.role}:
        errors.append(f"must mention exactly authorized or denied role '{atom.role}'")
    role_patterns = (
        r"\bAuthenticated\s+as\s+([A-Za-z_][A-Za-z0-9_-]*)",
        r"\bThe\s+([A-Za-z_][A-Za-z0-9_-]*)\s+(?:actor|role)\s+is\s+authenticated\b",
        r"\b([A-Za-z_][A-Za-z0-9_-]*)\s+is\s+denied\b",
        r"\brole\s*[:=]\s*([A-Za-z_][A-Za-z0-9_-]*)",
    )
    claimed_roles = {match.group(1) for pattern in role_patterns for match in re.finditer(pattern, text, re.IGNORECASE)}
    if claimed_roles - {atom.role}:
        errors.append(f"mentions invented or foreign role claim(s): {sorted(claimed_roles - {atom.role})}")
    paths = {row[3] for row in oracle["operations"]} | {row[8] for row in oracle["ranges"]} | {row[3] for row in oracle["denials"]}
    for endpoint in re.findall(r"/api/v\d+(?:/[A-Za-z0-9_{}-]+)+", text):
        if endpoint != atom.path:
            errors.append(f"mentions foreign or unsupported endpoint {endpoint}")
    fields = {row[2] for row in oracle["ranges"]} | {item.split(":", 1)[0] for row in oracle["operations"] + oracle["denials"] for item in row[-1]}
    text_without_paths = text
    for path in paths:
        text_without_paths = text_without_paths.replace(path, "")
    atom_fields = {item.split(":", 1)[0] for data in atom.allowed_test_data for item in data}
    for field in fields - atom_fields:
        if re.search(rf"\b{re.escape(field)}\b", text_without_paths):
            errors.append(f"mentions foreign field {field}")
    for field in re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b\s*(?::|=)\s*[^\s,.;]+", text_without_paths):
        if field not in fields:
            errors.append(f"mentions unsupported field-like identifier {field}")
    statuses = {row[4] for row in oracle["operations"]} | {row[9] for row in oracle["ranges"]} | {422, 403}
    codes = {row[5] for row in oracle["operations"]} | {row[10] for row in oracle["ranges"]} | {row[11] for row in oracle["ranges"]} | {"FORBIDDEN"}
    for status in statuses - {atom.status}:
        if re.search(rf"\b{status}\b", text_without_paths):
            errors.append(f"mentions foreign HTTP status {status}")
    for code in codes - {atom.code}:
        if re.search(rf"\b{re.escape(code)}\b", text):
            errors.append(f"mentions foreign response code {code}")
    known_numbers = {value for row in oracle["ranges"] for value in row[3:7]}
    allowed_numbers = {2, atom.status} | {int(item.rsplit(" ", 1)[1]) for data in atom.allowed_test_data for item in data if re.fullmatch(r"[a-z_]+: -?\d+", item)}
    for token in re.findall(r"(?<![A-Za-z_])-?\d+(?![A-Za-z_])", text_without_paths):
        value = int(token)
        if value in known_numbers and value not in allowed_numbers:
            errors.append(f"mentions foreign boundary number {value}")
    return errors


def validate(input_data: dict[str, Any], oracle: dict[str, Any], output: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    generated = output["artifacts"]["generated_test_cases"]
    requirements = input_data["artifacts"]["analytics_documentation"]["requirements"]
    if generated["requirements"] != requirements:
        errors.append("requirements must exactly copy the blind input")
    if output["warnings"] != input_data["warnings"] or output["warnings"] != oracle["warnings"]:
        errors.append("warnings must exactly copy the blind input")
    expected = atoms_from_oracle(oracle)
    cases = generated["test_cases"]
    if len(cases) != len(expected):
        errors.append(f"test-case count must be exactly {len(expected)}")
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        if case["id"] in case_ids:
            errors.append(f"duplicate test-case id {case['id']}")
        case_ids.add(case["id"])
        if index >= len(expected):
            errors.append(f"test case {index} is an extra unsupported atom")
            continue
        expected_id = f"TC-{index + 1:04d}"
        if case["id"] != expected_id:
            errors.append(f"test case {index} id must be {expected_id}")
        atom = expected[index]
        parse_error = parse_case(case, atom)
        if parse_error:
            errors.append(f"test case {case['id']} {parse_error}")
            continue
        errors.extend(f"test case {case['id']} {error}" for error in contamination_errors(case, atom, oracle))
    coverage_map: dict[str, list[str]] = {}
    for row in generated["coverage"]:
        if not isinstance(row, dict) or not isinstance(row.get("requirement_id"), str) or not isinstance(row.get("test_case_ids"), list) or not all(isinstance(value, str) for value in row.get("test_case_ids", [])):
            errors.append("coverage has invalid shape")
            continue
        if row["requirement_id"] in coverage_map:
            errors.append(f"duplicate coverage row for {row['requirement_id']}")
        coverage_map[row["requirement_id"]] = row["test_case_ids"]
    expected_coverage = {requirement["id"]: [f"TC-{index + 1:04d}" for index, atom in enumerate(expected) if atom.requirement_id == requirement["id"]] for requirement in requirements}
    if coverage_map != expected_coverage:
        errors.append("coverage must exactly map every input requirement to its ordered generated cases")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        input_data, oracle, output = read_json(args.input), read_json(args.oracle), read_json(args.output)
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "errors": [str(error)]}))
        return SHAPE
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    errors = shape_errors(output)
    if errors:
        print(json.dumps({"status": "error", "errors": errors, "artifact_sha256": digest}, sort_keys=True))
        return SHAPE
    errors = validate(input_data, oracle, output)
    print(json.dumps({"status": "pass" if not errors else "fail", "errors": errors, "artifact_sha256": digest}, sort_keys=True))
    return PASS if not errors else SEMANTIC


if __name__ == "__main__":
    raise SystemExit(main())
