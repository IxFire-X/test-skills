#!/usr/bin/env python3
"""Compose a create-only Phase-1 classified-test portfolio audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.canonical_document import validate_canonical_document
from tools.schema_validation import StrictJsonError, load_json_strict
from tools.test_classification import SuppliedInput, TestClassificationError, load_validated_behavior_context, select_effective_technical_evidence


_SCOPES = ("unit", "integration", "e2e", "unknown")


class AuditError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.diagnostics = (MappingProxyType({"path": path, "code": code, "message": message}),)
        super().__init__(message)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AuditError("AUDIT_ARGUMENT", "/arguments", "Audit CLI arguments are invalid.")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _load(path: Path, pointer: str) -> Mapping[str, Any]:
    try:
        value = load_json_strict(path)
    except (OSError, StrictJsonError) as error:
        raise AuditError("AUDIT_INPUT", pointer, "Audit input artifact could not be read as strict JSON.") from error
    if not isinstance(value, Mapping):
        raise AuditError("AUDIT_INPUT", pointer, "Audit input artifact must be a JSON object.")
    return value


def _emit_error(diagnostics: Sequence[Mapping[str, str]]) -> None:
    print(
        json.dumps({"status": "error", "diagnostics": _plain(diagnostics)}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )


def _write_create_only(output: Path, summary: Mapping[str, Any]) -> None:
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as error:
        raise AuditError("AUDIT_OUTPUT_EXISTS", "/output", "Output path already exists.") from error
    except OSError as error:
        raise AuditError("AUDIT_OUTPUT", "/output", "Audit output could not be created.") from error


def _supplied_inputs(values: Sequence[str]) -> tuple[SuppliedInput, ...]:
    rows: list[SuppliedInput] = []
    for value in values:
        if "=" not in value:
            raise AuditError("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Supplied input must use SOURCE_ID=PATH.")
        source_id, path = value.split("=", 1)
        try:
            rows.append(SuppliedInput(source_id, Path(path).read_bytes()))
        except OSError as error:
            raise AuditError("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Supplied input file could not be read.") from error
    return tuple(rows)


def _summary(inventory: Mapping[str, Any], classification: Mapping[str, Any], review: Mapping[str, Any], validated: Any, canonical: Mapping[str, Any], project: Path) -> dict[str, Any]:
    inventory_artifacts = inventory.get("artifacts")
    if not isinstance(inventory_artifacts, Mapping):
        raise AuditError("AUDIT_INPUT", "/artifacts", "Audit artifacts must use their expected envelopes.")
    selected = select_effective_technical_evidence(inventory, classification, review, validated, project)

    canonical_diagnostics = validate_canonical_document(dict(canonical))
    if canonical_diagnostics:
        raise AuditError("AUDIT_CANONICAL_DOCUMENT", "/canonical-document", "Canonical document is invalid.")

    symbols = selected["symbols"]
    classifications = selected["classifications"]
    scope_counts = {scope: 0 for scope in _SCOPES}
    for row in classifications:
        scope_counts[row["test_scope"]] += 1
    return {
        "status": "PASS",
        "inventory_pair_count": len(symbols),
        "classification_pair_count": len(classifications),
        "reviewed_pair_count": len(symbols),
        "scope_counts": scope_counts,
        "requirement_count": len(canonical["requirements"]),
        "case_count": len(canonical["test_cases"]),
        "uncovered_requirement_ids": [],
        "diagnostics": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    phase1 = commands.add_parser("phase1")
    phase1.add_argument("--project", required=True)
    phase1.add_argument("--inventory", required=True)
    phase1.add_argument("--classification", required=True)
    phase1.add_argument("--review", required=True)
    phase1.add_argument("--context", required=True)
    phase1.add_argument("--receipt", required=True)
    phase1.add_argument("--skillsrc", required=True)
    phase1.add_argument("--module")
    phase1.add_argument("--supplied-input", action="append", default=[])
    phase1.add_argument("--canonical-document", required=True)
    phase1.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command != "phase1":  # argparse constrains this branch for process callers.
            raise AuditError("AUDIT_ARGUMENT", "/command", "Audit command is invalid.")
        output = Path(args.output)
        if output.exists():
            raise AuditError("AUDIT_OUTPUT_EXISTS", "/output", "Output path already exists.")
        inventory = _load(Path(args.inventory), "/inventory")
        validated = load_validated_behavior_context(
            Path(args.context), Path(args.receipt), inventory, Path(args.project), Path(args.skillsrc), args.module, _supplied_inputs(args.supplied_input),
        )
        summary = _summary(
            inventory,
            _load(Path(args.classification), "/classification"),
            _load(Path(args.review), "/review"),
            validated,
            _load(Path(args.canonical_document), "/canonical-document"),
            Path(args.project),
        )
        _write_create_only(output, summary)
        return 0 if summary["status"] == "PASS" else 1
    except TestClassificationError as error:
        _emit_error(error.diagnostics)
        return 2
    except AuditError as error:
        _emit_error(error.diagnostics)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        _emit_error(({"path": "/input", "code": "AUDIT_INPUT", "message": "Audit input artifacts are invalid."},))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
