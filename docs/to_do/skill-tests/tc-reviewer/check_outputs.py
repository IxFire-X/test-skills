#!/usr/bin/env python3
"""Deterministic semantic checker for the portable tc-reviewer campaign."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


FIXTURES = (
    "clean-accepted",
    "typo-only",
    "blocking-missing-result",
    "blocking-fabricated-auth",
)
OUTPUT_SUFFIX = "-tc-reviewer-output.json"
EXPECTED_VERDICTS = {
    "clean-accepted": "ПРИНЯТО",
    "typo-only": "AUTO_FIX_APPLIED",
    "blocking-missing-result": "ТРЕБУЕТ ДОРАБОТКИ",
    "blocking-fabricated-auth": "ТРЕБУЕТ ДОРАБОТКИ",
}
ROLE_CLAIM_PATTERNS = (
    re.compile(r"\bauthenticated\s+as\s+([a-z][a-z0-9_-]*)\b", re.IGNORECASE),
    re.compile(r"\bonly\s+([a-z][a-z0-9_-]*)\s+(?:may|can)\b", re.IGNORECASE),
    re.compile(r"\b([a-z][a-z0-9_-]*)\s+(?:may|can)\s+(?:approve|review|create|delete|update|read|write)\b", re.IGNORECASE),
    re.compile(r"\b(?:use|assign)\s+([a-z][a-z0-9_-]*)\s+role\b", re.IGNORECASE),
    re.compile(r"\brole(?:\s+is\b|\s*[=:])\s*([a-z][a-z0-9_-]*)\b", re.IGNORECASE),
)
ENDPOINT_TOKEN = re.compile(r"/api/v\d+/[A-Za-z0-9_{}./:-]+")
HTTP_STATUS_TOKEN = re.compile(r"\b[1-5]\d\d\b")


class ArtifactError(Exception):
    """Unreadable or structurally unusable campaign artifact."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"{path}: {error}") from error


def _input_payload(document: Any, path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        generated = document["artifacts"]["generated_test_cases"]
        requirements = generated["requirements"]
        test_cases = generated["test_cases"]
    except (KeyError, TypeError) as error:
        raise ArtifactError(f"{path}: unsupported tc-generator input shape") from error
    if not isinstance(requirements, list) or not requirements or not isinstance(test_cases, list) or not test_cases:
        raise ArtifactError(f"{path}: requirements and test_cases must be non-empty arrays")
    return requirements, test_cases


def _output_payload(document: Any, path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    try:
        if document["schema_version"] != "2.1.0" or document["stage"] != "tc-reviewer":
            raise ArtifactError(f"{path}: unsupported tc-reviewer envelope")
        warnings = document["warnings"]
        artifacts = document["artifacts"]
        review = artifacts["validation_report"]
        corrected = artifacts["corrected_test_cases"]
    except (KeyError, TypeError) as error:
        raise ArtifactError(f"{path}: unsupported tc-reviewer output shape") from error
    if not isinstance(review, dict) or not isinstance(corrected, list) or not isinstance(warnings, list):
        raise ArtifactError(f"{path}: unsupported tc-reviewer output shape")
    for field in ("verdict", "reviewed_test_case_ids", "findings", "corrections"):
        if field not in review:
            raise ArtifactError(f"{path}: validation_report.{field} is required")
    if not isinstance(review["findings"], list) or not isinstance(review["corrections"], list):
        raise ArtifactError(f"{path}: findings and corrections must be arrays")
    return review, corrected, warnings


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def _role_claims(value: Any) -> set[str]:
    text = _json_text(value)
    return {
        match.group(1).lower()
        for pattern in ROLE_CLAIM_PATTERNS
        for match in pattern.finditer(text)
    }


def _related_id_errors(
    fixture: str,
    review: dict[str, Any],
    known_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    for collection in ("findings", "corrections"):
        for index, item in enumerate(review[collection]):
            if not isinstance(item, dict) or not isinstance(item.get("related_ids"), list):
                errors.append(f"{fixture}: {collection}[{index}] has unusable related_ids")
                continue
            dangling = set(item["related_ids"]) - known_ids
            if dangling:
                errors.append(f"{fixture}: {collection}[{index}] has dangling related_ids {sorted(dangling)}")
    return errors


def _grounding_errors(fixture: str, input_document: Any, output_document: Any) -> list[str]:
    errors: list[str] = []
    input_text = _json_text(input_document)
    output_text = _json_text(output_document)
    allowed_roles = _role_claims(input_document)
    output_roles = _role_claims(output_document)
    invented_roles = output_roles - allowed_roles
    if invented_roles:
        errors.append(f"{fixture}: output invents role tokens {sorted(invented_roles)}")
    allowed_endpoints = set(ENDPOINT_TOKEN.findall(input_text))
    output_endpoints = set(ENDPOINT_TOKEN.findall(output_text))
    invented_endpoints = output_endpoints - allowed_endpoints
    if invented_endpoints:
        errors.append(f"{fixture}: output invents endpoints {sorted(invented_endpoints)}")
    allowed_statuses = set(HTTP_STATUS_TOKEN.findall(input_text))
    output_statuses = set(HTTP_STATUS_TOKEN.findall(output_text))
    invented_statuses = output_statuses - allowed_statuses
    if invented_statuses:
        errors.append(f"{fixture}: output invents HTTP statuses {sorted(invented_statuses)}")
    return errors


def _blocking_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in review["findings"]
        if isinstance(finding, dict) and finding.get("severity") == "BLOCKING"
    ]


def _code_describes(code: Any, fixture: str) -> bool:
    if not isinstance(code, str):
        return False
    normalized = code.lower().replace("-", "_")
    if fixture == "typo-only":
        return any(token in normalized for token in ("typo", "spell", "title"))
    if fixture == "blocking-missing-result":
        subject = any(token in normalized for token in ("result", "oracle", "expected"))
        defect = any(token in normalized for token in ("missing", "placeholder", "tbd", "non_executable"))
        return subject and defect
    subject = any(token in normalized for token in ("auth", "role", "permission", "policy"))
    defect = any(token in normalized for token in ("unsupported", "fabricated", "ungrounded"))
    return subject and defect


def _has_evidence(evidence: Any, *tokens: str) -> bool:
    if not isinstance(evidence, list):
        return False
    lowered = [item.lower() for item in evidence if isinstance(item, str)]
    return any(all(token.lower() in item for token in tokens) for item in lowered)


def _check_fixture(
    fixture: str,
    input_document: Any,
    output_document: Any,
    output_path: Path,
) -> list[str]:
    errors: list[str] = []
    requirements, test_cases = _input_payload(input_document, output_path)
    review, corrected, warnings = _output_payload(output_document, output_path)
    requirement_ids = [item["id"] for item in requirements]
    test_case_ids = [item["id"] for item in test_cases]
    known_ids = set(requirement_ids) | set(test_case_ids)

    if review["reviewed_test_case_ids"] != test_case_ids:
        errors.append(f"{fixture}: reviewed_test_case_ids must exactly preserve input order")
    if review["verdict"] != EXPECTED_VERDICTS[fixture]:
        errors.append(f"{fixture}: expected verdict {EXPECTED_VERDICTS[fixture]}")
    if warnings != []:
        errors.append(f"{fixture}: warnings must be empty")
    errors.extend(_related_id_errors(fixture, review, known_ids))
    errors.extend(_grounding_errors(fixture, input_document, output_document))

    if fixture == "clean-accepted":
        if review["findings"] or review["corrections"] or corrected:
            errors.append(f"{fixture}: clean input must not produce findings or corrections")
        return errors

    if fixture == "typo-only":
        expected_case = copy.deepcopy(test_cases[0])
        expected_case["title"] = "Delete an owned active session"
        if corrected != [expected_case]:
            errors.append(f"{fixture}: corrected case must change only sesion to session in title")
        if len(review["corrections"]) != 1:
            errors.append(f"{fixture}: exactly one mechanical correction is required")
        if not review["findings"]:
            errors.append(f"{fixture}: typo must be reported")
        if _blocking_findings(review):
            errors.append(f"{fixture}: safe typo must not be blocking")
        finding = review["findings"][0] if len(review["findings"]) == 1 else {}
        if (
            not _code_describes(finding.get("code"), fixture)
            or set(finding.get("related_ids", [])) != {"TC-TYPO-001"}
            or not _has_evidence(
                finding.get("evidence"), "TC-TYPO-001", "title", "sesion"
            )
        ):
            errors.append(f"{fixture}: finding must bind the spelling defect to title evidence")
        correction = review["corrections"][0] if len(review["corrections"]) == 1 else {}
        if (
            set(correction.get("related_ids", [])) != {"TC-TYPO-001"}
            or not _has_evidence(
                correction.get("evidence"), "TC-TYPO-001", "title", "sesion"
            )
        ):
            errors.append(f"{fixture}: correction must bind the same title spelling evidence")
        review_text = _json_text(review)
        if "sesion" not in review_text or "session" not in review_text:
            errors.append(f"{fixture}: correction evidence must identify sesion -> session")
        return errors

    if review["corrections"] or corrected:
        errors.append(f"{fixture}: blocking gaps must not be corrected or rewritten")
    blocking = _blocking_findings(review)
    if len(review["findings"]) != 1 or len(blocking) != 1:
        errors.append(f"{fixture}: exactly one BLOCKING finding is required")
        return errors
    expected_contract = {
        "blocking-missing-result": {
            "related_ids": {"TC-MISSING-001", "REQ-CANCEL-001"},
            "evidence_tokens": (
                ("TC-MISSING-001", "expected_result", "TBD"),
                ("TC-MISSING-001", "expected_outcome", "TBD"),
            ),
        },
        "blocking-fabricated-auth": {
            "related_ids": {"TC-AUTH-001", "REQ-AUTH-001"},
            "evidence_tokens": (
                ("TC-AUTH-001", "precondition", "warehouse_operator"),
                ("REQ-AUTH-001", "sales_manager"),
            ),
        },
    }[fixture]
    if (
        not _code_describes(blocking[0].get("code"), fixture)
        or set(blocking[0].get("related_ids", [])) != expected_contract["related_ids"]
        or not all(
            _has_evidence(blocking[0].get("evidence"), *tokens)
            for tokens in expected_contract["evidence_tokens"]
        )
    ):
        errors.append(f"{fixture}: blocking diagnosis must bind semantic code, IDs, and field evidence")
    blocking_text = _json_text(blocking)
    if fixture == "blocking-missing-result":
        if not any(token in blocking_text for token in ("tbd", "missing", "not specified")):
            errors.append(f"{fixture}: finding must preserve the missing-result evidence")
    elif "warehouse_operator" not in blocking_text:
        errors.append(f"{fixture}: finding must identify unsupported warehouse_operator policy")
    return errors


def check(input_dir: Path, output_dir: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    errors: list[str] = []
    artifact_sha256: dict[str, str] = {}
    reviewed_ids: dict[str, list[str]] = {}
    expected_output_names = {f"{fixture}{OUTPUT_SUFFIX}" for fixture in FIXTURES}
    try:
        actual_output_names = {path.name for path in output_dir.glob(f"*{OUTPUT_SUFFIX}")}
    except OSError as error:
        raise ArtifactError(f"{output_dir}: {error}") from error
    if actual_output_names != expected_output_names:
        errors.append(
            "output set mismatch: "
            f"missing={sorted(expected_output_names - actual_output_names)}, "
            f"extra={sorted(actual_output_names - expected_output_names)}"
        )

    for fixture in FIXTURES:
        input_path = input_dir / f"{fixture}.json"
        output_path = output_dir / f"{fixture}{OUTPUT_SUFFIX}"
        input_document = _read_json(input_path)
        output_document = _read_json(output_path)
        artifact_sha256[fixture] = _sha256(output_path)
        _, test_cases = _input_payload(input_document, input_path)
        reviewed_ids[fixture] = [case["id"] for case in test_cases]
        errors.extend(_check_fixture(fixture, input_document, output_document, output_path))
    return errors, artifact_sha256, reviewed_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("red-control", "canonical", "pressure"), required=True)
    args = parser.parse_args(argv)
    try:
        errors, artifact_sha256, reviewed_ids = check(args.input_dir, args.output_dir)
    except ArtifactError as error:
        print(json.dumps({"status": "error", "errors": [str(error)]}, ensure_ascii=False))
        return 2
    status = "pass" if not errors else ("gap" if args.mode == "red-control" else "fail")
    print(
        json.dumps(
            {
                "status": status,
                "errors": errors,
                "artifact_sha256": artifact_sha256,
                "reviewed_test_case_ids": reviewed_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not errors or args.mode == "red-control" else 1


if __name__ == "__main__":
    sys.exit(main())
