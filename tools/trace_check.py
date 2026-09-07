#!/usr/bin/env python3
"""Safe V5 audit receipt for a pre-finalization trace projection."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
TRACE_SCHEMA = ROOT / "schemas" / "trace-document.schema.json"
AUDIT_SCHEMA = ROOT / "schemas" / "trace-audit-output.schema.json"
from tools.json_cli import JsonArgumentParser


class _TraceArgumentParser(JsonArgumentParser):
    """Emit one value-redacting JSON object for every CLI argument error."""

    def error(self, message: str) -> None:
        print(json.dumps({"status": "error", "errors": [{"path": "", "code": "TRACE_ARGUMENT", "message": "Trace arguments are invalid."}]}, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(2)


def _diag(path: str, code: str, message: str = "Trace input is invalid.") -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _safe(rows: list[Mapping[str, Any]], default: str) -> list[dict[str, str]]:
    return [_diag("" if str(row.get("code", "")) == "SCHEMA_ADDITIONAL_PROPERTIES" else str(row.get("path", "")), str(row.get("code", default))) for row in rows]


def _summary(trace: Any) -> dict[str, int]:
    if not isinstance(trace, Mapping):
        return {key: 0 for key in ("requirements", "test_cases", "steps", "expectations", "assertions", "files", "symbols", "relations", "manual_dispositions", "evidence")}
    execution = trace.get("execution")
    return {
        "requirements": len(trace.get("requirements", [])) if isinstance(trace.get("requirements"), list) else 0,
        "test_cases": len(trace.get("test_cases", [])) if isinstance(trace.get("test_cases"), list) else 0,
        "steps": len(trace.get("steps", [])) if isinstance(trace.get("steps"), list) else 0,
        "expectations": len(trace.get("expectations", [])) if isinstance(trace.get("expectations"), list) else 0,
        "assertions": len(trace.get("assertions", [])) if isinstance(trace.get("assertions"), list) else 0,
        "files": len(trace.get("files", [])) if isinstance(trace.get("files"), list) else 0,
        "symbols": len(trace.get("symbols", [])) if isinstance(trace.get("symbols"), list) else 0,
        "relations": len(trace.get("implementation_relations", [])) if isinstance(trace.get("implementation_relations"), list) else 0,
        "manual_dispositions": len(trace.get("manual_dispositions", [])) if isinstance(trace.get("manual_dispositions"), list) else 0,
        "evidence": len(execution.get("evidence", [])) if isinstance(execution, Mapping) and isinstance(execution.get("evidence"), list) else 0,
    }


def _audit_source(trace: Any) -> dict[str, Any]:
    source = trace.get("source") if isinstance(trace, Mapping) else None
    if isinstance(source, Mapping) and set(source) == {"document_id", "revision", "source_digest"} and isinstance(source.get("document_id"), str) and re.fullmatch(r"TCDOC-[a-z0-9](?:[a-z0-9_.-]*[a-z0-9_-])?", source["document_id"]) and type(source.get("revision")) is int and source["revision"] >= 1 and isinstance(source.get("source_digest"), str) and re.fullmatch(r"sha256:[0-9a-f]{64}", source["source_digest"]):
        return {"document_id": source["document_id"], "revision": source["revision"], "source_digest": source["source_digest"]}
    return {"document_id": "TCDOC-invalid", "revision": 1, "source_digest": "sha256:" + "0" * 64}


def _internal(trace: Any, require_execution: bool) -> list[dict[str, str]]:
    from tools.schema_validation import classify_version, schema_diagnostics
    from tools.build_trace_document import project_declared_order
    from tools.run_tests import validate_process_evidence
    if classify_version(trace)["code"] == "V2_1_BREAKING_CHANGE":
        return [_diag("/schema_version", "V2_1_BREAKING_CHANGE", "V2.1 artifacts are incompatible with V3 trace.")]
    structural = schema_diagnostics(trace, TRACE_SCHEMA, ROOT)
    if structural:
        return _safe(structural, "TRACE_SCHEMA")
    assert isinstance(trace, Mapping)
    rows: list[dict[str, str]] = []
    requirement_order: list[str] = []
    requirements: set[str] = set()
    for index, row in enumerate(trace["requirements"]):
        if row["requirement_id"] in requirements or row["display_order"] != index + 1: rows.append(_diag(f"/requirements/{index}", "TRACE_REQUIREMENT_ORDER"))
        requirements.add(row["requirement_id"])
        requirement_order.append(row["requirement_id"])
    cases: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(trace["test_cases"]):
        if row["case_id"] in cases or row["display_order"] != index + 1:
            rows.append(_diag(f"/test_cases/{index}", "TRACE_CASE_OWNERSHIP"))
        try:
            expected_requirements = project_declared_order(requirement_order, row["requirement_ids"])
        except ValueError:
            rows.append(_diag(f"/test_cases/{index}", "TRACE_CASE_OWNERSHIP"))
        else:
            if row["requirement_ids"] != expected_requirements:
                rows.append(_diag(f"/test_cases/{index}/requirement_ids", "TRACE_CASE_REQUIREMENT_ORDER"))
        cases[row["case_id"]] = row
    steps: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, row in enumerate(trace["steps"]):
        key = (row["case_id"], row["step_id"])
        expected_order = 1 + sum(1 for earlier in trace["steps"][:index] if earlier["case_id"] == row["case_id"])
        if row["case_id"] not in cases or key in steps or row["display_order"] != expected_order: rows.append(_diag(f"/steps/{index}", "TRACE_STEP_OWNERSHIP"))
        steps[key] = row
    expectations: set[tuple[str, str, str]] = set()
    for index, row in enumerate(trace["expectations"]):
        key = (row["case_id"], row["step_id"], row["expectation_id"])
        expected_order = 1 + sum(1 for earlier in trace["expectations"][:index] if (earlier["case_id"], earlier["step_id"]) == key[:2])
        if key in expectations or key[:2] not in steps or row["display_order"] != expected_order: rows.append(_diag(f"/expectations/{index}", "TRACE_EXPECTATION_OWNERSHIP"))
        expectations.add(key)
    assertions: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(trace["assertions"]):
        key = (row["case_id"], row["step_id"], row["expectation_id"], row["assertion_id"])
        expected_order = 1 + sum(1 for earlier in trace["assertions"][:index] if (earlier["case_id"], earlier["step_id"], earlier["expectation_id"]) == key[:3])
        if key in assertions or key[:3] not in expectations or row["display_order"] != expected_order: rows.append(_diag(f"/assertions/{index}", "TRACE_ASSERTION_OWNERSHIP"))
        assertions.add(key)
    case_order = {row["case_id"]: index for index, row in enumerate(trace["test_cases"])}
    step_order = {key: row["display_order"] for key, row in steps.items()}
    expectation_order = {(row["case_id"], row["step_id"], row["expectation_id"]): row["display_order"] for row in trace["expectations"]}
    assertion_order = {(row["case_id"], row["step_id"], row["expectation_id"], row["assertion_id"]): row["display_order"] for row in trace["assertions"]}
    registry_orders = (
        ("/steps", [(case_order.get(row["case_id"], 10**9), row["display_order"]) for row in trace["steps"]]),
        ("/expectations", [(case_order.get(row["case_id"], 10**9), step_order.get((row["case_id"], row["step_id"]), 10**9), row["display_order"]) for row in trace["expectations"]]),
        ("/assertions", [(case_order.get(row["case_id"], 10**9), step_order.get((row["case_id"], row["step_id"]), 10**9), expectation_order.get((row["case_id"], row["step_id"], row["expectation_id"]), 10**9), row["display_order"]) for row in trace["assertions"]]),
    )
    for path, order in registry_orders:
        if order != sorted(order):
            rows.append(_diag(path, "TRACE_PARENT_ORDER"))
    files: dict[str, Mapping[str, Any]] = {}
    paths: set[str] = set()
    for index, row in enumerate(trace["files"]):
        if row["file_id"] in files or row["path"] in paths: rows.append(_diag(f"/files/{index}", "TRACE_DUPLICATE_FILE"))
        files[row["file_id"]] = row
        paths.add(row["path"])
    symbols: set[tuple[str, str]] = set()
    locators: set[tuple[str, str]] = set()
    for index, row in enumerate(trace["symbols"]):
        pair = (row["file_id"], row["symbol_id"])
        locator = (row["file_id"], json.dumps(row["locator"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if row["file_id"] not in files or pair in symbols or locator in locators: rows.append(_diag(f"/symbols/{index}", "TRACE_SYMBOL_OWNERSHIP"))
        symbols.add(pair)
        locators.add(locator)
    required: set[tuple[str, str]] = set()
    relation_keys: set[tuple[Any, ...]] = set()
    for index, relation in enumerate(trace["implementation_relations"]):
        path = f"/implementation_relations/{index}"
        pair = (relation["file_id"], relation["symbol_id"])
        if relation["case_id"] not in cases or (relation["case_id"], relation["step_id"]) not in steps:
            rows.append(_diag(path, "TRACE_RELATION_STEP")); continue
        if steps[(relation["case_id"], relation["step_id"])]["manual_only"] or steps[(relation["case_id"], relation["step_id"])]["blocker_ids"]:
            rows.append(_diag(path, "TRACE_RELATION_NONREADY")); continue
        if relation["kind"] == "assertion" and (relation["case_id"], relation["step_id"], relation["expectation_id"], relation["assertion_id"]) not in assertions:
            rows.append(_diag(path, "TRACE_RELATION_ASSERTION")); continue
        if pair not in symbols or relation["file_id"] not in files:
            rows.append(_diag(path, "TRACE_RELATION_SYMBOL")); continue
        key = (relation["kind"], relation["case_id"], relation["step_id"], relation.get("expectation_id"), relation.get("assertion_id"), *pair)
        if key in relation_keys:
            rows.append(_diag(path, "TRACE_DUPLICATE_RELATION")); continue
        relation_keys.add(key)
        required.add(pair)
    relation_order = []
    for relation in trace["implementation_relations"]:
        relation_order.append((case_order.get(relation["case_id"], 10**9), step_order.get((relation["case_id"], relation["step_id"]), 10**9), 0 if relation["kind"] == "operation" else 1, expectation_order.get((relation["case_id"], relation["step_id"], relation.get("expectation_id")), -1), assertion_order.get((relation["case_id"], relation["step_id"], relation.get("expectation_id"), relation.get("assertion_id")), -1), relation["file_id"], relation["symbol_id"]))
    if relation_order != sorted(relation_order): rows.append(_diag("/implementation_relations", "TRACE_RELATION_ORDER"))
    for pair in symbols:
        if pair not in required: rows.append(_diag("/symbols", "TRACE_ORPHAN_SYMBOL"))
    for file_id in files:
        if not any(pair[0] == file_id for pair in required): rows.append(_diag("/files", "TRACE_ORPHAN_FILE"))
    manual: set[tuple[str, str]] = set()
    for row in trace["manual_dispositions"]:
        key = (row["case_id"], row["step_id"])
        if key in manual or key not in steps or not steps[key]["manual_only"]:
            rows.append(_diag("/manual_dispositions", "TRACE_MANUAL_OWNERSHIP"))
        manual.add(key)
    manual_order = [(case_order.get(row["case_id"], 10**9), step_order.get((row["case_id"], row["step_id"]), 10**9)) for row in trace["manual_dispositions"]]
    if manual_order != sorted(manual_order): rows.append(_diag("/manual_dispositions", "TRACE_MANUAL_ORDER"))
    for key, step in steps.items():
        if step["manual_only"] and key not in manual: rows.append(_diag("/manual_dispositions", "TRACE_MANUAL_COVERAGE"))
        if not step["manual_only"] and not step["blocker_ids"]:
            if not any(row["kind"] == "operation" and (row["case_id"], row["step_id"]) == key for row in trace["implementation_relations"]): rows.append(_diag("/implementation_relations", "TRACE_OPERATION_COVERAGE"))
    for key in assertions:
        step = steps.get((key[0], key[1]))
        if step is None:
            continue
        if not step["manual_only"] and not step["blocker_ids"] and not any(row["kind"] == "assertion" and (row["case_id"], row["step_id"], row["expectation_id"], row["assertion_id"]) == key for row in trace["implementation_relations"]): rows.append(_diag("/implementation_relations", "TRACE_ASSERTION_COVERAGE"))
    for key in expectations:
        step = steps.get((key[0], key[1]))
        if step is not None and not step["manual_only"] and not step["blocker_ids"] and not any(assertion[:3] == key for assertion in assertions):
            rows.append(_diag("/assertions", "TRACE_ASSERTION_TOPOLOGY"))
    if trace["automation_status"] == "BLOCKED":
        if not any(step["blocker_ids"] for step in steps.values()) or not trace["automation_diagnostics"] or any((trace["files"], trace["symbols"], trace["implementation_relations"], trace["manual_dispositions"])): rows.append(_diag("/automation_status", "TRACE_BLOCKED_BRANCH"))
    elif any(step["blocker_ids"] for step in steps.values()) or trace["automation_diagnostics"]:
        rows.append(_diag("/automation_status", "TRACE_GENERATED_BRANCH"))
    execution = trace["execution"]
    blocked_or_manual = trace["automation_status"] == "BLOCKED" or not required
    if blocked_or_manual and execution is not None:
        rows.append(_diag("/execution", "TRACE_EXECUTION_FORBIDDEN"))
    if not blocked_or_manual and execution is None:
        rows.append(_diag("/execution", "TRACE_EXECUTION_REQUIRED"))
    if isinstance(execution, Mapping):
        seen: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for evidence in execution["evidence"]:
            if evidence["run_id"] != execution["run_id"]:
                rows.append(_diag("/execution/evidence", "TRACE_STALE_EVIDENCE")); continue
            if evidence["source_digest"] != trace["source"]["source_digest"]:
                rows.append(_diag("/execution/evidence", "TRACE_EVIDENCE_SOURCE")); continue
            pair = (evidence["file_id"], evidence["symbol_id"])
            if pair not in required or evidence["file_digest"] != files.get(pair[0], {}).get("file_digest"):
                rows.append(_diag("/execution/evidence", "TRACE_EVIDENCE_PAIR")); continue
            seen.setdefault(pair, []).append(evidence)
        if any(len(values) != 1 for values in seen.values()): rows.append(_diag("/execution/evidence", "TRACE_DUPLICATE_EVIDENCE"))
        statuses = {key: values[0]["status"] for key, values in seen.items() if len(values) == 1}
        process = execution["process_evidence"]
        changed_file_ids = {file_id for row in process if row.get("kind") == "SOURCE_CHANGED" and isinstance(row.get("affected_file_ids"), list) for file_id in row["affected_file_ids"] if isinstance(file_id, str)}
        if any(row.get("file_id") in changed_file_ids for row in execution["evidence"]):
            rows.append(_diag("/execution/evidence", "TRACE_SOURCE_CHANGED_EVIDENCE"))
        rows.extend(validate_process_evidence(process, execution["run_id"], trace["source"], execution.get("exit_code"), execution.get("duration_sec"), execution.get("target"), "TRACE", tuple(file_id for file_id, _ in required)))
        evidence_structural_error = any(row["code"] in {"TRACE_EVIDENCE_SOURCE", "TRACE_EVIDENCE_PAIR", "TRACE_DUPLICATE_EVIDENCE", "TRACE_SOURCE_CHANGED_EVIDENCE"} for row in rows)
        process_structural_error = any(row["code"].startswith("TRACE_PROCESS_") or row["code"] == "TRACE_STALE_PROCESS_EVIDENCE" for row in rows)
        derived = "FAIL" if evidence_structural_error or process_structural_error or process or any(status in {"FAILED", "ERROR"} for status in statuses.values()) else "NOT_RUNNABLE" if len(statuses) != len(required) or "SKIPPED" in statuses.values() else "PASS"
        if execution["verdict"] != "UNKNOWN" and execution["verdict"] != derived: rows.append(_diag("/execution/verdict", "TRACE_EXECUTION_VERDICT"))
        if execution["verdict"] == "UNKNOWN" and execution["evidence_authoritative"]:
            rows.append(_diag("/execution/evidence_authoritative", "TRACE_UNKNOWN_EVIDENCE"))
        zero_collect = (
            not evidence_structural_error
            and not process_structural_error
            and not execution["evidence"]
            and len(process) == 1
            and process[0].get("kind") == "NO_TESTS_COLLECTED"
        )
        complete = zero_collect or (
            not evidence_structural_error and not process and len(statuses) == len(required)
        )
        if execution["evidence_authoritative"] != complete: rows.append(_diag("/execution/evidence_authoritative", "TRACE_EVIDENCE_AUTHORITATIVE"))
    expected_lifecycle = {"projection": "PRE_FINALIZATION", "verification": "NOT_APPLICABLE" if execution is None else execution["verdict"]}
    if trace["lifecycle"] != expected_lifecycle:
        rows.append(_diag("/lifecycle", "TRACE_LIFECYCLE"))
    return sorted(rows, key=lambda row: (row["path"], row["code"], row["message"]))


def check(trace: Mapping[str, Any], document: Mapping[str, Any] | None = None, automation: Mapping[str, Any] | None = None, autotest_review: Mapping[str, Any] | None = None, run_result: Mapping[str, Any] | None = None, require_execution: bool = False) -> dict[str, Any]:
    """Audit a trace alone or prove it exactly matches upstream pre-finalization artifacts."""
    supplied = (document is not None, automation is not None, autotest_review is not None, run_result is not None)
    # The audit always enforces the branch's exact obligation; this flag preserves the
    # public CLI spelling without forcing a fabricated run for BLOCKED/MANUAL_ONLY.
    rows = _internal(trace, require_execution)
    if any(supplied) and not (document is not None and automation is not None and autotest_review is not None):
        rows.append(_diag("", "TRACE_EXTERNAL_SET", "External trace inputs must be supplied as a complete branch."))
    elif document is not None and automation is not None and autotest_review is not None:
        from tools.build_trace_document import validate_trace_document
        rows.extend(validate_trace_document(trace, document, automation, autotest_review, run_result))
    rows = sorted(rows, key=lambda row: (row["path"], row["code"], row["message"]))
    required = []
    if isinstance(trace, Mapping) and isinstance(trace.get("implementation_relations"), list):
        required = [{"file_id": file_id, "symbol_id": symbol_id} for file_id, symbol_id in sorted({(row.get("file_id"), row.get("symbol_id")) for row in trace["implementation_relations"] if isinstance(row, Mapping) and isinstance(row.get("file_id"), str) and isinstance(row.get("symbol_id"), str) and re.fullmatch(r"FILE-[A-Za-z0-9_.:-]+", row["file_id"]) and re.fullmatch(r"SYMBOL-[A-Za-z0-9_.:-]+", row["symbol_id"])})]
    from tools.build_trace_document import trace_sha256
    source = _audit_source(trace)
    lifecycle = trace.get("lifecycle") if isinstance(trace, Mapping) and isinstance(trace.get("lifecycle"), Mapping) else {"projection": "PRE_FINALIZATION", "verification": "NOT_APPLICABLE"}
    digest = trace_sha256(trace) if isinstance(trace, Mapping) else "sha256:" + "0" * 64
    return {"schema_version": "5.0.0", "stage": "trace-check", "valid": not rows, "trace_audit": {"verdict": "PASS" if not rows else "FAIL", "trace_sha256": digest, "source": source, "lifecycle": dict(lifecycle), "required_symbol_pairs": required, "relation_count": _summary(trace)["relations"], "errors": [row["code"] for row in rows]}, "errors": rows, "warnings": [], "summary": _summary(trace)}


def main(argv: list[str] | None = None) -> int:
    from tools.schema_validation import StrictJsonError, load_json_strict
    parser = _TraceArgumentParser(description=__doc__); parser.add_argument("document", type=Path); parser.add_argument("--require-execution", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = check(load_json_strict(args.document), require_execution=args.require_execution)
    except (StrictJsonError, OSError):
        report = {"schema_version": "5.0.0", "stage": "trace-check", "valid": False, "trace_audit": {"verdict": "FAIL", "trace_sha256": "sha256:" + "0" * 64, "source": {"document_id": "TCDOC-invalid", "revision": 1, "source_digest": "sha256:" + "0" * 64}, "lifecycle": {"projection": "PRE_FINALIZATION", "verification": "NOT_APPLICABLE"}, "required_symbol_pairs": [], "relation_count": 0, "errors": ["TRACE_IO"]}, "errors": [_diag("", "TRACE_IO", "Trace input is unavailable.")], "warnings": [], "summary": _summary({})}
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":"))); return 2
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":"))); return 0 if report["valid"] else 2 if any(row["code"].startswith("SCHEMA_") or row["code"] == "V2_1_BREAKING_CHANGE" for row in report["errors"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
