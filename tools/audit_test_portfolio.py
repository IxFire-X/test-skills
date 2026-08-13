#!/usr/bin/env python3
"""Compose a create-only Phase-1 classified-test portfolio audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    if __package__:
        from .canonical_document import validate_canonical_document
        from .schema_validation import StrictJsonError, load_json_strict
        from .test_classification import TestClassificationError, select_effective_technical_evidence, validate_managed_behavior_context
    else:
        from canonical_document import validate_canonical_document
        from schema_validation import StrictJsonError, load_json_strict
        from test_classification import TestClassificationError, select_effective_technical_evidence, validate_managed_behavior_context
except ImportError:  # pragma: no cover - package imports are exercised by tests
    from tools.canonical_document import validate_canonical_document
    from tools.schema_validation import StrictJsonError, load_json_strict
    from tools.test_classification import TestClassificationError, select_effective_technical_evidence, validate_managed_behavior_context


_SCOPES = ("unit", "integration", "e2e", "unknown")


class AuditError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.diagnostics = ({"path": path, "code": code, "message": message},)
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


def _summary(inventory: Mapping[str, Any], classification: Mapping[str, Any], review: Mapping[str, Any], context: Mapping[str, Any], canonical: Mapping[str, Any], project: Path) -> dict[str, Any]:
    inventory_artifacts = inventory.get("artifacts")
    context_artifacts = context.get("artifacts")
    if not isinstance(inventory_artifacts, Mapping) or not isinstance(context_artifacts, Mapping):
        raise AuditError("AUDIT_INPUT", "/artifacts", "Audit artifacts must use their expected envelopes.")
    authorized_sources = inventory_artifacts.get("authorized_behavior_sources")
    test_inventory = inventory_artifacts.get("technical_test_inventory")
    behavior_context = context_artifacts.get("managed_behavior_context")
    if not isinstance(authorized_sources, Mapping) or not isinstance(test_inventory, Mapping) or not isinstance(behavior_context, Mapping):
        raise AuditError("AUDIT_INPUT", "/artifacts", "Audit artifacts must provide the managed behavior context and source inventory.")

    context_diagnostics = validate_managed_behavior_context(behavior_context, authorized_sources, test_inventory, project)
    if context_diagnostics:
        raise TestClassificationError(context_diagnostics)

    requirements = behavior_context["requirements"]
    selected = select_effective_technical_evidence(inventory, classification, review, requirements, project)

    canonical_diagnostics = validate_canonical_document(dict(canonical))
    if canonical_diagnostics:
        raise AuditError("AUDIT_CANONICAL_DOCUMENT", "/canonical-document", "Canonical document is invalid.")

    symbols = test_inventory["symbols"]
    classifications = selected["classifications"]
    reviewed_pairs = review["artifacts"]["classification_review"]["reviewed_symbol_pairs"]
    scope_counts = {scope: 0 for scope in _SCOPES}
    for row in classifications:
        scope_counts[row["test_scope"]] += 1
    covered = {requirement_id for case in canonical["test_cases"] for requirement_id in case["requirement_ids"]}
    uncovered = [row["requirement_id"] for row in canonical["requirements"] if row["requirement_id"] not in covered]
    return {
        "status": "PASS" if not uncovered else "FAIL",
        "inventory_pair_count": len(symbols),
        "classification_pair_count": len(classifications),
        "reviewed_pair_count": len(reviewed_pairs),
        "scope_counts": scope_counts,
        "requirement_count": len(canonical["requirements"]),
        "case_count": len(canonical["test_cases"]),
        "uncovered_requirement_ids": uncovered,
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
    phase1.add_argument("--canonical-document", required=True)
    phase1.add_argument("--output", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command != "phase1":  # argparse constrains this branch for process callers.
            raise AuditError("AUDIT_ARGUMENT", "/command", "Audit command is invalid.")
        output = Path(args.output)
        if output.exists():
            raise AuditError("AUDIT_OUTPUT_EXISTS", "/output", "Output path already exists.")
        summary = _summary(
            _load(Path(args.inventory), "/inventory"),
            _load(Path(args.classification), "/classification"),
            _load(Path(args.review), "/review"),
            _load(Path(args.context), "/context"),
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
