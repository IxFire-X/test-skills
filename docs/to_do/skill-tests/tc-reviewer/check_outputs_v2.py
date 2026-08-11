#!/usr/bin/env python3
"""Versioned semantic checker for the tc-reviewer campaign.

v2 preserves v1 except that a typo correction may cite supporting known
requirements in addition to the affected test case.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import check_outputs as v1


def _check_typo_fixture(
    input_document: object,
    output_document: object,
    output_path: Path,
) -> list[str]:
    errors: list[str] = []
    _requirements, test_cases = v1._input_payload(input_document, output_path)
    review, corrected, warnings = v1._output_payload(output_document, output_path)
    test_case_ids = [item["id"] for item in test_cases]
    known_ids = {item["id"] for item in _requirements} | set(test_case_ids)

    if review["reviewed_test_case_ids"] != test_case_ids:
        errors.append("typo-only: reviewed_test_case_ids must exactly preserve input order")
    if review["verdict"] != v1.EXPECTED_VERDICTS["typo-only"]:
        errors.append("typo-only: expected verdict AUTO_FIX_APPLIED")
    if warnings != []:
        errors.append("typo-only: warnings must be empty")
    errors.extend(v1._related_id_errors("typo-only", review, known_ids))
    errors.extend(v1._grounding_errors("typo-only", input_document, output_document))

    expected_case = copy.deepcopy(test_cases[0])
    expected_case["title"] = "Delete an owned active session"
    if corrected != [expected_case]:
        errors.append("typo-only: corrected case must change only sesion to session in title")
    if len(review["corrections"]) != 1:
        errors.append("typo-only: exactly one mechanical correction is required")
    if not review["findings"]:
        errors.append("typo-only: typo must be reported")
    if v1._blocking_findings(review):
        errors.append("typo-only: safe typo must not be blocking")
    finding = review["findings"][0] if len(review["findings"]) == 1 else {}
    if (
        not v1._code_describes(finding.get("code"), "typo-only")
        or set(finding.get("related_ids", [])) != {"TC-TYPO-001"}
        or not v1._has_evidence(
            finding.get("evidence"), "TC-TYPO-001", "title", "sesion"
        )
    ):
        errors.append("typo-only: finding must bind the spelling defect to title evidence")
    correction = review["corrections"][0] if len(review["corrections"]) == 1 else {}
    related_ids = correction.get("related_ids")
    if not isinstance(related_ids, list) or "TC-TYPO-001" not in related_ids:
        errors.append("typo-only: correction must include TC-TYPO-001")
    if not v1._has_evidence(
        correction.get("evidence"), "TC-TYPO-001", "title", "sesion"
    ):
        errors.append("typo-only: correction must bind title spelling evidence")
    review_text = v1._json_text(review)
    if "sesion" not in review_text or "session" not in review_text:
        errors.append("typo-only: correction evidence must identify sesion -> session")
    return errors


def _check_fixture(
    fixture: str,
    input_document: object,
    output_document: object,
    output_path: Path,
) -> list[str]:
    if fixture != "typo-only":
        return v1._check_fixture(fixture, input_document, output_document, output_path)
    return _check_typo_fixture(input_document, output_document, output_path)


def check(input_dir: Path, output_dir: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    errors: list[str] = []
    artifact_sha256: dict[str, str] = {}
    reviewed_ids: dict[str, list[str]] = {}
    expected_output_names = {f"{fixture}{v1.OUTPUT_SUFFIX}" for fixture in v1.FIXTURES}
    try:
        actual_output_names = {path.name for path in output_dir.glob(f"*{v1.OUTPUT_SUFFIX}")}
    except OSError as error:
        raise v1.ArtifactError(f"{output_dir}: {error}") from error
    if actual_output_names != expected_output_names:
        errors.append(
            "output set mismatch: "
            f"missing={sorted(expected_output_names - actual_output_names)}, "
            f"extra={sorted(actual_output_names - expected_output_names)}"
        )
    for fixture in v1.FIXTURES:
        input_path = input_dir / f"{fixture}.json"
        output_path = output_dir / f"{fixture}{v1.OUTPUT_SUFFIX}"
        input_document = v1._read_json(input_path)
        output_document = v1._read_json(output_path)
        artifact_sha256[fixture] = v1._sha256(output_path)
        _, test_cases = v1._input_payload(input_document, input_path)
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
    except v1.ArtifactError as error:
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
