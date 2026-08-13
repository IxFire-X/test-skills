#!/usr/bin/env python3
"""Build the closed V3 atomic trace from canonical, automation, and run artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
TRACE_SCHEMA = ROOT / "schemas" / "trace-document.schema.json"
RUN_SCHEMA = ROOT / "schemas" / "run-tests-output.schema.json"
from tools.json_cli import JsonArgumentParser


class _TraceArgumentParser(JsonArgumentParser):
    """Emit one value-redacting JSON object for every CLI argument error."""

    def error(self, message: str) -> None:
        print(json.dumps({"status": "error", "errors": [{"path": "", "code": "TRACE_ARGUMENT", "message": "Trace arguments are invalid."}]}, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(2)


def _diag(path: str, code: str, message: str = "Trace input is invalid.") -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _safe(rows: Sequence[Mapping[str, Any]], default: str) -> list[dict[str, str]]:
    return [_diag("" if str(row.get("code", "")) == "SCHEMA_ADDITIONAL_PROPERTIES" else str(row.get("path", "")), str(row.get("code", default))) for row in rows]


class TraceBuildError(ValueError):
    """An immutable, value-redacting collection of trace construction diagnostics."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]) -> None:
        frozen = tuple(MappingProxyType(dict(row)) for row in sorted(diagnostics, key=lambda row: (str(row.get("path", "")), str(row.get("code", "")), str(row.get("message", "")))))
        object.__setattr__(self, "_diagnostics", frozen)
        ValueError.__init__(self, json.dumps([dict(row) for row in frozen], ensure_ascii=False, separators=(",", ":")))

    @property
    def diagnostics(self) -> tuple[Mapping[str, str], ...]:
        return self._diagnostics

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, "_diagnostics"):
            raise AttributeError("TraceBuildError is immutable")
        object.__setattr__(self, name, value)


def _version_rows(value: Any) -> list[dict[str, str]]:
    from tools.schema_validation import classify_version
    version = classify_version(value)
    return [_diag("/schema_version", "V2_1_BREAKING_CHANGE", "V2.1 artifacts are incompatible with V3 trace.")] if version["code"] == "V2_1_BREAKING_CHANGE" else []


def _source(document: Mapping[str, Any]) -> dict[str, Any]:
    from tools.canonical_document import document_sha256
    return {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(dict(document))}


def _source_rows(source: Any, expected: Mapping[str, Any], prefix: str) -> list[dict[str, str]]:
    if not isinstance(source, Mapping):
        return [_diag(prefix, "TRACE_SOURCE")]
    result = []
    for key in ("document_id", "revision", "source_digest"):
        if source.get(key) != expected[key]:
            result.append(_diag(f"{prefix}/{key}", "TRACE_SOURCE"))
    return result


def _has_blocker(document: Mapping[str, Any]) -> bool:
    return any(step["automation_blockers"] for case in document["test_cases"] for step in case["steps"])


def _input_diagnostics(document: Any, automation: Any, run_result: Any | None) -> list[dict[str, str]]:
    from tools.automation_validation import validate_automation_artifact, required_symbol_pairs
    from tools.canonical_document import CanonicalDocumentError, require_valid_canonical_document
    from tools.run_tests import validate_execution_evidence
    from tools.schema_validation import schema_diagnostics

    if not isinstance(document, Mapping):
        return [_diag("", "TRACE_DOCUMENT")]
    try:
        require_valid_canonical_document(dict(document))
    except CanonicalDocumentError as error:
        return _version_rows(document) or _safe(error.diagnostics, "TRACE_DOCUMENT")
    rows = _version_rows(automation)
    if rows:
        return rows
    if not isinstance(automation, Mapping):
        return [_diag("", "TRACE_AUTOMATION")]
    automation_rows = validate_automation_artifact(dict(automation), dict(document))
    if automation_rows:
        return _safe(automation_rows, "TRACE_AUTOMATION")
    if run_result is not None:
        rows = _version_rows(run_result)
        if rows:
            return rows
    artifacts = automation["artifacts"]
    pairs = required_symbol_pairs(dict(automation), dict(document))
    no_run = artifacts["automation_status"] == "BLOCKED" or not pairs
    if no_run:
        return [] if run_result is None else [_diag("/run_result", "TRACE_RUN_FORBIDDEN")]
    if run_result is None:
        return [_diag("/run_result", "TRACE_RUN_REQUIRED")]
    schema = schema_diagnostics(run_result, RUN_SCHEMA, ROOT)
    if schema:
        return _safe(schema, "TRACE_RUN_SCHEMA")
    expected_source = _source(document)
    rows = _source_rows(run_result["source"], expected_source, "/source")
    files = {row["file_id"]: row["content_digest"] for row in artifacts["generated_files"]}
    rows.extend(_safe(validate_execution_evidence(run_result["verdict"], run_result["run_id"], run_result["source"], run_result["execution_evidence"], run_result["evidence_authoritative"], sorted(pairs), files), "TRACE_EVIDENCE"))
    return sorted(rows, key=lambda row: (row["path"], row["code"], row["message"]))


def _construct(document: Mapping[str, Any], automation: Mapping[str, Any], run_result: Mapping[str, Any] | None) -> dict[str, Any]:
    artifacts = automation["artifacts"]
    requirements = [{"requirement_id": row["requirement_id"], "display_order": row["display_order"]} for row in document["requirements"]]
    cases, steps, expectations, assertions = [], [], [], []
    for case in document["test_cases"]:
        cases.append({"case_id": case["case_id"], "display_order": case["display_order"], "requirement_ids": list(case["requirement_ids"])})
        for step in case["steps"]:
            steps.append({"case_id": case["case_id"], "step_id": step["step_id"], "display_order": step["display_order"], "manual_only": step["manual_only"], "blocker_ids": [row["blocker_id"] for row in step["automation_blockers"]]})
            for expectation in step["expectations"]:
                expectations.append({"case_id": case["case_id"], "step_id": step["step_id"], "expectation_id": expectation["expectation_id"], "display_order": expectation["display_order"]})
                for assertion in expectation["assertions"]:
                    assertions.append({"case_id": case["case_id"], "step_id": step["step_id"], "expectation_id": expectation["expectation_id"], "assertion_id": assertion["assertion_id"], "display_order": assertion["display_order"]})
    pairs = {(row["file_id"], row["symbol_id"]) for row in artifacts["implementation_relations"]}
    if _has_blocker(document) or artifacts["automation_status"] == "BLOCKED":
        final = "BLOCKED"
    elif not pairs:
        final = "MANUAL_ONLY"
    elif run_result is not None and run_result["verdict"] == "FAIL":
        final = "FAIL"
    elif run_result is not None and run_result["verdict"] == "NOT_RUNNABLE":
        final = "NOT_RUNNABLE"
    elif artifacts["manual_dispositions"]:
        final = "PASS_WITH_MANUAL_REMAINDER"
    else:
        final = "PASS"
    execution = None if run_result is None else {"verdict": run_result["verdict"], "run_id": run_result["run_id"], "evidence_authoritative": run_result["evidence_authoritative"], "evidence": [dict(row) for row in run_result["execution_evidence"]]}
    return {
        "schema_version": "3.0.0", "stage": "trace", "source": _source(document), "automation_status": artifacts["automation_status"],
        "requirements": requirements, "test_cases": cases, "steps": steps, "expectations": expectations, "assertions": assertions,
        "files": [{"file_id": row["file_id"], "path": row["path"], "language": row["language"], "framework": row["framework"], "file_digest": row["content_digest"]} for row in artifacts["generated_files"]],
        "symbols": [dict(row) for row in artifacts["generated_symbols"]], "implementation_relations": [dict(row) for row in artifacts["implementation_relations"]],
        "manual_dispositions": [dict(row) for row in artifacts["manual_dispositions"]], "automation_diagnostics": [dict(row) for row in artifacts["diagnostics"]],
        "execution": execution, "final_verdict": final,
    }


def build_trace(document: Mapping[str, Any], automation: Mapping[str, Any], run_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate V3 sources then preserve their exact atomic trace topology."""
    rows = _input_diagnostics(document, automation, run_result)
    if rows:
        raise TraceBuildError(rows)
    trace = _construct(document, automation, run_result)
    from tools.schema_validation import schema_diagnostics
    structural = schema_diagnostics(trace, TRACE_SCHEMA, ROOT)
    if structural:
        raise TraceBuildError(_safe(structural, "TRACE_SCHEMA"))
    return trace


def validate_trace_document(trace: Mapping[str, Any], document: Mapping[str, Any], automation: Mapping[str, Any], run_result: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """Validate trace closure and exact equality with the one private construction seam."""
    from tools.schema_validation import schema_diagnostics
    version = _version_rows(trace)
    if version:
        return version
    structural = schema_diagnostics(trace, TRACE_SCHEMA, ROOT)
    if structural:
        return _safe(structural, "TRACE_SCHEMA")
    try:
        expected = build_trace(document, automation, run_result)
    except TraceBuildError as error:
        return [dict(row) for row in error.diagnostics]
    return [] if dict(trace) == expected else [_diag("", "TRACE_MISMATCH", "Trace does not exactly match validated source artifacts.")]


def _write_new(path: Path, trace: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(trace, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    from tools.schema_validation import StrictJsonError, load_json_strict
    parser = _TraceArgumentParser(description=__doc__)
    parser.add_argument("--canonical-document", required=True, type=Path)
    parser.add_argument("--automation-artifact", required=True, type=Path)
    parser.add_argument("--run-result", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        trace = build_trace(load_json_strict(args.canonical_document), load_json_strict(args.automation_artifact), None if args.run_result is None else load_json_strict(args.run_result))
        _write_new(args.output, trace)
    except TraceBuildError as error:
        print(json.dumps({"status": "invalid", "errors": [dict(row) for row in error.diagnostics]}, ensure_ascii=False, separators=(",", ":"))); return 1
    except (StrictJsonError, OSError, FileExistsError):
        print(json.dumps({"status": "error", "errors": [{"path": "", "code": "TRACE_IO", "message": "Trace input or output is unavailable."}]}, ensure_ascii=False, separators=(",", ":"))); return 2
    print(json.dumps({"status": "valid", "output": str(args.output.resolve())}, ensure_ascii=False, separators=(",", ":"))); return 0


if __name__ == "__main__":
    raise SystemExit(main())
