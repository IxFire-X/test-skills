#!/usr/bin/env python3
"""Publish and select one reviewed canonical V3 test-document revision."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.canonical_document import CanonicalDocumentError, document_sha256, require_valid_canonical_document
from tools.publish_test_case_bundle import Receipt, publish_bundle, verify_bundle
from tools.revision_selection import SelectionError, select_effective_document
from tools.schema_validation import StrictJsonError, classify_version, load_json_strict, schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SCHEMA = ROOT / "schemas" / "tc-reviewer-output.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "orchestrator-output.schema.json"
AUTOMATION_REVIEW_SCHEMA = ROOT / "schemas" / "autotest-reviewer-output.schema.json"
_PROFILE = "zephyr-scale-step-row-24-v1"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _diag(path: str, code: str, message: str = "Orchestration failed.") -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _safe(rows: Sequence[Mapping[str, Any]], default: str) -> tuple[Mapping[str, str], ...]:
    return tuple(_freeze(_diag(str(row.get("path", "")), str(row.get("code", default)))) for row in rows)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _receipt_copy(value: Receipt) -> Receipt:
    return Receipt(value.document_id, value.revision, value.csv_profile, value.json_path, value.markdown_path, value.csv_path, value.document_sha256, value.markdown_sha256, value.csv_sha256)


@dataclass(frozen=True)
class OrchestrationResult:
    candidate_document: Mapping[str, Any]
    candidate_bundle_receipt: Receipt
    successor_document: Mapping[str, Any] | None
    successor_bundle_receipt: Receipt | None
    effective_document: Mapping[str, Any] | None
    effective_bundle_receipt: Receipt | None
    status: Literal["EFFECTIVE_SELECTED", "REWORK"]
    diagnostics: tuple[Mapping[str, str], ...]


class OrchestrationError(ValueError):
    """Immutable, value-redacting error with recoverable bundle receipts."""

    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]], candidate_bundle_receipt: Receipt | None = None, successor_bundle_receipt: Receipt | None = None) -> None:
        object.__setattr__(self, "code", str(code))
        object.__setattr__(self, "diagnostics", tuple(_freeze(dict(row)) for row in sorted(diagnostics, key=lambda row: (str(row.get("path", "")), str(row.get("code", "")), str(row.get("message", ""))))))
        object.__setattr__(self, "candidate_bundle_receipt", None if candidate_bundle_receipt is None else _receipt_copy(candidate_bundle_receipt))
        object.__setattr__(self, "successor_bundle_receipt", None if successor_bundle_receipt is None else _receipt_copy(successor_bundle_receipt))
        ValueError.__init__(self, json.dumps({"code": self.code, "diagnostics": [dict(row) for row in self.diagnostics]}, ensure_ascii=False, separators=(",", ":")))

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, "code"):
            raise AttributeError("OrchestrationError is immutable")
        object.__setattr__(self, name, value)


def _expected_paths(document: Mapping[str, Any], output_dir: str | os.PathLike[str]) -> tuple[str, str, str]:
    base = Path(output_dir).resolve() / f"{document['document_id']}.r{document['revision']}"
    return str(base) + ".json", str(base) + ".md", str(base) + ".zephyr-scale.csv"


def _receipt_rows(receipt: Any, document: Mapping[str, Any], output_dir: str | os.PathLike[str] | None, profile: str) -> list[dict[str, str]]:
    if type(receipt) is not Receipt:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    expected_paths = _expected_paths(document, output_dir) if output_dir is not None else None
    expected = {
        "document_id": document["document_id"], "revision": document["revision"], "csv_profile": profile,
        "document_sha256": document_sha256(dict(document)),
    }
    fields = ("document_id", "revision", "csv_profile", "json_path", "markdown_path", "csv_path", "document_sha256", "markdown_sha256", "csv_sha256")
    for field in fields:
        value = getattr(receipt, field)
        if field in expected and value != expected[field]:
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
        if field in {"markdown_sha256", "csv_sha256"} and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
    if expected_paths is not None and (receipt.json_path, receipt.markdown_path, receipt.csv_path) != expected_paths:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    if expected_paths is None and any(not isinstance(getattr(receipt, field), str) or not getattr(receipt, field) for field in ("json_path", "markdown_path", "csv_path")):
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    return []


def _review_rows(review: Any) -> list[dict[str, str]]:
    version = classify_version(review)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [_diag("/schema_version", version["code"], version["message"])]
    return [_diag(str(row.get("path", "")), str(row.get("code", "ORCHESTRATION_REVIEW"))) for row in schema_diagnostics(review, REVIEW_SCHEMA, ROOT)]


def _validate_profile(profile: str) -> None:
    if profile != _PROFILE:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/csv_profile", "ORCHESTRATION_PROFILE")])


def orchestrate_revision(candidate: Mapping[str, Any], review_artifact: Mapping[str, Any], output_dir: str | os.PathLike[str], csv_profile: str, *, publisher=publish_bundle, verifier=verify_bundle) -> OrchestrationResult:
    """Durably publish a candidate, then select and verify exactly one revision."""
    _validate_profile(csv_profile)
    candidate_value, review_value = _plain(candidate), _plain(review_artifact)
    candidate_receipt: Receipt | None = None
    successor_receipt: Receipt | None = None
    try:
        require_valid_canonical_document(candidate_value)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_CANDIDATE")) from None
    try:
        published = publisher(_plain(candidate_value), output_dir, csv_profile)
    except Exception:
        raise OrchestrationError("ORCHESTRATION_PUBLICATION", [_diag("", "ORCHESTRATION_PUBLICATION")]) from None
    rows = _receipt_rows(published, candidate_value, output_dir, csv_profile)
    if rows:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows) from None
    candidate_receipt = _receipt_copy(published)
    rows = _review_rows(review_value)
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows, candidate_receipt) from None
    report = review_value["artifacts"]["validation_report"]
    successor = review_value["artifacts"].get("successor_document")
    verdict = report["verdict"]
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, None, None, None, None, "REWORK", ())
    if verdict == "AUTO_FIX_APPLIED":
        try:
            require_valid_canonical_document(successor)
        except CanonicalDocumentError as error:
            raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_SUCCESSOR"), candidate_receipt) from None
        try:
            effective = select_effective_document(_plain(candidate_value), _plain(report), _plain(successor))
        except SelectionError as error:
            raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
        try:
            published_successor = publisher(_plain(effective), output_dir, csv_profile)
        except Exception:
            raise OrchestrationError("ORCHESTRATION_PUBLICATION", [_diag("", "ORCHESTRATION_PUBLICATION")], candidate_receipt) from None
        successor_receipt = _receipt_copy(published_successor) if type(published_successor) is Receipt else None
        rows = _receipt_rows(published_successor, effective, output_dir, csv_profile)
        if rows:
            raise OrchestrationError("ORCHESTRATION_RECEIPT", rows, candidate_receipt, successor_receipt) from None
    else:
        try:
            effective = select_effective_document(_plain(candidate_value), _plain(report), None)
        except SelectionError as error:
            raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    try:
        verified = verifier(_plain(effective), output_dir, csv_profile)
    except Exception:
        raise OrchestrationError("ORCHESTRATION_VERIFICATION", [_diag("", "ORCHESTRATION_VERIFICATION")], candidate_receipt, successor_receipt) from None
    rows = _receipt_rows(verified, effective, output_dir, csv_profile)
    publication_receipt = successor_receipt if successor_receipt is not None else candidate_receipt
    if rows or verified != publication_receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")], candidate_receipt, successor_receipt) from None
    return OrchestrationResult(_freeze(candidate_value), candidate_receipt, None if successor is None else _freeze(successor), successor_receipt, _freeze(effective), _receipt_copy(publication_receipt), "EFFECTIVE_SELECTED", ())


def _source(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(dict(document))}


def _source_rows(value: Any, expected: Mapping[str, Any], path: str) -> list[dict[str, str]]:
    return [] if isinstance(value, Mapping) and dict(value) == dict(expected) else [_diag(path, "ORCHESTRATION_SOURCE")]


def finalize_orchestration(effective_document: Mapping[str, Any], effective_bundle_receipt: Receipt, automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], run_result: Mapping[str, Any] | None, trace_document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return one closed V3 orchestrator artifact or raise OrchestrationError."""
    document, automation, review, trace = _plain(effective_document), _plain(automation_artifact), _plain(autotest_review_artifact), _plain(trace_document)
    try:
        require_valid_canonical_document(document)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_DOCUMENT")) from None
    expected = _source(document)
    rows = _receipt_rows(effective_bundle_receipt, document, None, effective_bundle_receipt.csv_profile if type(effective_bundle_receipt) is Receipt else "")
    if rows:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows) from None
    from tools.automation_validation import required_symbol_pairs, validate_automation_artifact
    automation_rows = validate_automation_artifact(automation, document)
    if automation_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(automation_rows, "ORCHESTRATION_AUTOMATION"))
    review_version = classify_version(review)
    if review_version["code"] == "V2_1_BREAKING_CHANGE":
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/schema_version", review_version["code"], review_version["message"])])
    review_rows = schema_diagnostics(review, AUTOMATION_REVIEW_SCHEMA, ROOT)
    if review_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(review_rows, "ORCHESTRATION_AUTOTEST_REVIEW"))
    reviewed = review["artifacts"]["autotest_review"]
    rows = _source_rows(reviewed["source"], expected, "/artifacts/autotest_review/source")
    pairs = sorted(required_symbol_pairs(automation, document))
    actual_pairs = [(row["file_id"], row["symbol_id"]) for row in reviewed["reviewed_symbol_pairs"]]
    if reviewed["verdict"] != "ПРИНЯТО":
        rows.append(_diag("/artifacts/autotest_review/verdict", "ORCHESTRATION_AUTOTEST_VERDICT"))
    if actual_pairs != pairs:
        rows.append(_diag("/artifacts/autotest_review/reviewed_symbol_pairs", "ORCHESTRATION_REVIEW_COVERAGE"))
    no_run = automation["artifacts"]["automation_status"] == "BLOCKED" or not pairs
    run = None if run_result is None else _plain(run_result)
    if no_run != (run is None):
        rows.append(_diag("/run_result", "ORCHESTRATION_RUN_BRANCH"))
    if run is not None:
        rows.extend(_source_rows(run.get("source"), expected, "/run_result/source"))
    from tools.build_trace_document import validate_trace_document
    trace_rows = validate_trace_document(trace, document, automation, run)
    if trace_rows:
        rows.extend(_safe(trace_rows, "ORCHESTRATION_TRACE"))
    rows.extend(_source_rows(trace.get("source"), expected, "/trace_document/source"))
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows)
    final = trace["final_verdict"]
    result = {
        "schema_version": "3.0.0", "stage": "orchestrate", "warnings": [],
        "artifacts": {"orchestration_result": {
            "effective_source": expected, "effective_bundle_receipt": {field: getattr(effective_bundle_receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "markdown_path", "csv_path", "document_sha256", "markdown_sha256", "csv_sha256")},
            "automation_source": _plain(automation["artifacts"]["source"]), "autotest_review_source": _plain(reviewed["source"]),
            "run_source": None if run is None else _plain(run["source"]), "trace_source": _plain(trace["source"]),
            "automation_status": automation["artifacts"]["automation_status"], "autotest_review_verdict": "ПРИНЯТО", "run_verdict": None if run is None else run["verdict"], "final_status": final,
        }},
    }
    structural = schema_diagnostics(result, OUTPUT_SCHEMA, ROOT)
    if structural:
        raise OrchestrationError("ORCHESTRATION_OUTPUT", _safe(structural, "ORCHESTRATION_OUTPUT"))
    return _freeze(result)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("", "ORCHESTRATION_ARGUMENT")], "status": "error", "successor_bundle_receipt": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)


def _receipt_json(receipt: Receipt | None) -> dict[str, Any] | None:
    return None if receipt is None else {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "markdown_path", "csv_path", "document_sha256", "markdown_sha256", "csv_sha256")}


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--csv-profile", default=_PROFILE)
    args = parser.parse_args(argv)
    if args.csv_profile != _PROFILE:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("/csv_profile", "ORCHESTRATION_PROFILE")], "status": "error", "successor_bundle_receipt": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    try:
        result = orchestrate_revision(load_json_strict(args.candidate), load_json_strict(args.review), args.output_dir, args.csv_profile)
    except OrchestrationError as error:
        exit_code = 2 if error.code in {"ORCHESTRATION_PUBLICATION", "ORCHESTRATION_VERIFICATION"} else 1
        print(json.dumps({"candidate_bundle_receipt": _receipt_json(error.candidate_bundle_receipt), "code": error.code, "diagnostics": [dict(row) for row in error.diagnostics], "status": "error", "successor_bundle_receipt": _receipt_json(error.successor_bundle_receipt)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return exit_code
    except (StrictJsonError, OSError):
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("", "ORCHESTRATION_IO")], "status": "error", "successor_bundle_receipt": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    output = {"candidate_bundle_receipt": _receipt_json(result.candidate_bundle_receipt), "diagnostics": [], "effective_bundle_receipt": _receipt_json(result.effective_bundle_receipt), "effective_source": None if result.effective_document is None else _source(result.effective_document), "status": result.status, "successor_bundle_receipt": _receipt_json(result.successor_bundle_receipt)}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
