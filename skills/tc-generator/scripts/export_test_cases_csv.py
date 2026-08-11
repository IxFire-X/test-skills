#!/usr/bin/env python3
"""Export tc-generator test cases to a lossless deterministic CSV companion."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

FIELDNAMES = [
    "test_case_id",
    "requirement_links",
    "title",
    "priority",
    "categories",
    "preconditions",
    "test_data",
    "ordered_steps",
    "expected_outcome",
]
STRUCTURED_FIELDS = {
    "requirement_links": "requirement_ids",
    "categories": "categories",
    "preconditions": "preconditions",
    "test_data": "test_data",
    "ordered_steps": "steps",
}


class ExportError(ValueError):
    """Input or CSV data cannot satisfy the lossless companion contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_input(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read input JSON: {error}") from error
    if not isinstance(document, dict):
        raise TypeError("input JSON must be an object")
    try:
        if document["schema_version"] != "2.1.0" or document["stage"] != "tc-generator":
            raise KeyError("stage")
        cases = document["artifacts"]["generated_test_cases"]["test_cases"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("input is not a tc-generator-output v2.1.0 envelope") from error
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("input test_cases must be a nonempty array")
    _validate_cases(cases)
    return document, raw


def _validate_cases(cases: list[Any]) -> None:
    expected_keys = {
        "id",
        "requirement_ids",
        "title",
        "categories",
        "priority",
        "preconditions",
        "test_data",
        "steps",
        "expected_outcome",
    }
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != expected_keys:
            raise RuntimeError(f"test case {index} has an unsupported shape")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise RuntimeError(f"test case {index} has an invalid or duplicate id")
        seen.add(case["id"])
        for field in ("requirement_ids", "categories", "preconditions", "test_data", "steps"):
            if not isinstance(case[field], list):
                raise TypeError(f"test case {case['id']} field {field} must be an array")
        for field in ("title", "priority", "expected_outcome"):
            if not isinstance(case[field], str):
                raise TypeError(f"test case {case['id']} field {field} must be a string")


def _rows(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "test_case_id": case["id"],
            "requirement_links": _compact_json(case["requirement_ids"]),
            "title": case["title"],
            "priority": case["priority"],
            "categories": _compact_json(case["categories"]),
            "preconditions": _compact_json(case["preconditions"]),
            "test_data": _compact_json(case["test_data"]),
            "ordered_steps": _compact_json(case["steps"]),
            "expected_outcome": case["expected_outcome"],
        }
        for case in cases
    ]


def _render(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _reconstruct(csv_bytes: bytes) -> list[dict[str, Any]]:
    try:
        stream = io.StringIO(csv_bytes.decode("utf-8"), newline="")
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDNAMES:
            raise ExportError(f"CSV header mismatch: {reader.fieldnames}")
        rows = list(reader)
        reconstructed: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if None in row or any(row.get(field) is None for field in FIELDNAMES):
                raise ExportError(f"CSV row {index} has an unsupported shape")
            case: dict[str, Any] = {
                "id": row["test_case_id"],
                "title": row["title"],
                "priority": row["priority"],
                "expected_outcome": row["expected_outcome"],
            }
            for csv_field, case_field in STRUCTURED_FIELDS.items():
                case[case_field] = json.loads(row[csv_field])
            reconstructed.append(case)
        return reconstructed
    except (UnicodeError, csv.Error, json.JSONDecodeError) as error:
        raise ExportError(f"cannot decode CSV companion: {error}") from error


def _verify(csv_bytes: bytes, cases: list[dict[str, Any]]) -> None:
    reconstructed = _reconstruct(csv_bytes)
    if reconstructed != cases:
        raise ExportError("CSV content is not exactly equivalent to JSON test_cases in count, order, or values")


def _write_new_or_identical(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ExportError("existing CSV differs from deterministic output; use a new reserved path")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
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
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output or args.input.with_suffix(".csv")
    try:
        document, input_bytes = _load_input(args.input)
        cases = document["artifacts"]["generated_test_cases"]["test_cases"]
        if args.verify_only:
            csv_bytes = output.read_bytes()
        else:
            csv_bytes = _render(_rows(cases))
            _verify(csv_bytes, cases)
            _write_new_or_identical(output, csv_bytes)
            csv_bytes = output.read_bytes()
        _verify(csv_bytes, cases)
    except ExportError as error:
        print(json.dumps({"status": "mismatch", "errors": [str(error)]}, ensure_ascii=False))
        return 1
    except (RuntimeError, TypeError, OSError) as error:
        print(json.dumps({"status": "error", "errors": [str(error)]}, ensure_ascii=False))
        return 2

    print(
        json.dumps(
            {
                "status": "valid",
                "output": str(output.resolve()),
                "test_case_count": len(cases),
                "json_sha256": _sha256(input_bytes),
                "csv_sha256": _sha256(csv_bytes),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
