#!/usr/bin/env python3
"""Publish and select one reviewed canonical document revision for the V5 integrity chain."""

from __future__ import annotations

import argparse
import copy
import hashlib
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

from tools.canonical_document import CanonicalDocumentError, canonical_bytes, document_sha256, require_valid_canonical_document
from tools.publish_test_case_bundle import Receipt, publish_bundle, verify_bundle
from tools.revision_selection import SelectionError, reviewer_verdict_is_bound, select_effective_document, validate_review_decision
from tools.schema_validation import StrictJsonError, classify_version, load_json_strict, schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SCHEMA = ROOT / "schemas" / "tc-reviewer-output.schema.json"
REVIEWER_SESSION_SCHEMA = ROOT / "schemas" / "reviewer-session.schema.json"
CONTEXT_RECEIPT_SCHEMA = ROOT / "schemas" / "context-selection-receipt.schema.json"
ASSEMBLY_RECEIPT_SCHEMA = ROOT / "schemas" / "assembly-receipt.schema.json"
INVENTORY_RECEIPT_SCHEMA = ROOT / "schemas" / "inventory-receipt.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "orchestrator-output.schema.json"
AUTOMATION_REVIEW_SCHEMA = ROOT / "schemas" / "autotest-reviewer-output.schema.json"
TRACE_AUDIT_SCHEMA = ROOT / "schemas" / "trace-audit-output.schema.json"
_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE = _PROFILE_V4
_PROFILES = frozenset({_PROFILE_V4})
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
    return Receipt(value.document_id, value.revision, value.csv_profile, value.json_path, value.preview_path, value.csv_path, value.document_sha256, value.preview_sha256, value.csv_sha256)


@dataclass(frozen=True)
class OrchestrationResult:
    candidate_document: Mapping[str, Any]
    candidate_bundle_receipt: Receipt
    candidate_publication_status: Literal["UNREVIEWED"]
    successor_document: Mapping[str, Any] | None
    successor_bundle_receipt: Receipt | None
    effective_document: Mapping[str, Any] | None
    effective_bundle_receipt: Receipt | None
    status: str
    facts: Mapping[str, Any]
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


def _expected_paths(document: Mapping[str, Any], output_dir: str | os.PathLike[str], profile: str) -> tuple[str, str, str]:
    base = Path(output_dir).resolve() / f"{document['document_id']}.r{document['revision']}"
    return str(base) + ".json", str(base) + (".html" if profile == _PROFILE_V4 else ".md"), str(base) + ".zephyr-scale.csv"


def _receipt_rows(receipt: Any, document: Mapping[str, Any], output_dir: str | os.PathLike[str] | None, profile: str) -> list[dict[str, str]]:
    if type(receipt) is not Receipt:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    expected_paths = _expected_paths(document, output_dir, profile) if output_dir is not None else None
    expected = {
        "document_id": document["document_id"], "revision": document["revision"], "csv_profile": profile,
        "document_sha256": document_sha256(dict(document)),
    }
    fields = ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")
    for field in fields:
        value = getattr(receipt, field)
        if field in expected and value != expected[field]:
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
        if field in {"preview_sha256", "csv_sha256"} and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
    if expected_paths is not None and (receipt.json_path, receipt.preview_path, receipt.csv_path) != expected_paths:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    if expected_paths is None and any(not isinstance(getattr(receipt, field), str) or not getattr(receipt, field) for field in ("json_path", "preview_path", "csv_path")):
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    return []


def _review_rows(review: Any) -> list[dict[str, str]]:
    version = classify_version(review)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [_diag("/schema_version", version["code"], version["message"])]
    rows = schema_diagnostics(review, REVIEW_SCHEMA, ROOT)
    if rows:
        return [_diag(str(row.get("path", "")), str(row.get("code", "ORCHESTRATION_REVIEW"))) for row in rows]
    return []


def _validate_profile(profile: str) -> None:
    if profile not in _PROFILES:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/csv_profile", "ORCHESTRATION_PROFILE")])


def _session_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _host_isolation_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt_value(receipt: Receipt) -> dict[str, Any]:
    return {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")}


def _exact_receipt(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    plain = _plain(value)
    if not isinstance(plain, dict) or plain.get("digest") != _session_digest(plain):
        raise ValueError(f"REVIEWER_PROTOCOL: invalid {label} receipt")
    return plain


def reviewer_package_binding(
    candidate: Mapping[str, Any],
    candidate_receipt: Receipt,
    assembly_receipt: Mapping[str, Any],
    inventory_receipt: Mapping[str, Any],
    context_receipts: Sequence[Mapping[str, Any]],
    *,
    context_marker_output: Mapping[str, Any],
    generator_fragments: Sequence[Mapping[str, Any]],
    run_root: Path,
    attempt_id: str,
) -> dict[str, Any]:
    """Bind reviewer input to exact canonical and controller receipt bytes."""
    candidate_value = _plain(candidate)
    require_valid_canonical_document(candidate_value)
    if _receipt_rows(candidate_receipt, candidate_value, None, candidate_receipt.csv_profile) or _readback_rows(candidate_receipt, candidate_value):
        raise ValueError("REVIEWER_PROTOCOL: candidate receipt is invalid")
    assembly, inventory = _exact_receipt(assembly_receipt, "assembly"), _exact_receipt(inventory_receipt, "inventory")
    if schema_diagnostics(assembly, ASSEMBLY_RECEIPT_SCHEMA, ROOT) or assembly.get("document_digest") != document_sha256(candidate_value):
        raise ValueError("REVIEWER_PROTOCOL: assembly receipt is invalid")
    if schema_diagnostics(inventory, INVENTORY_RECEIPT_SCHEMA, ROOT):
        raise ValueError("REVIEWER_PROTOCOL: inventory receipt is invalid")
    from tools.pilot_state import derive_state, read_context_selection, read_model_stage_artifact

    contexts = [_plain(item) for item in context_receipts]
    if not contexts or len({item.get("digest") for item in contexts}) != len(contexts):
        raise ValueError("REVIEWER_PROTOCOL: context receipts are missing or duplicated")
    for item in contexts:
        try:
            readback = read_context_selection(Path(run_root), attempt_id, str(item.get("digest", "")))
        except ValueError:
            readback = None
        if schema_diagnostics(item, CONTEXT_RECEIPT_SCHEMA, ROOT) or item.get("digest") != _session_digest(item) or item.get("inventory_digest") != inventory["digest"] or readback != item:
            raise ValueError("REVIEWER_PROTOCOL: context receipt is invalid")
    marker = _plain(context_marker_output)
    marker_artifacts = marker.get("artifacts") if isinstance(marker, dict) else None
    marker_analytics = marker_artifacts.get("analytics_documentation") if isinstance(marker_artifacts, dict) else None
    if (
        not isinstance(marker, dict)
        or not isinstance(marker_analytics, dict)
        or marker_analytics.get("requirements") != candidate_value.get("source_requirements")
    ):
        raise ValueError("REVIEWER_PROTOCOL: context-marker output is invalid")
    marker_digest = "sha256:" + hashlib.sha256(canonical_bytes(marker)).hexdigest()
    marker_publication = read_model_stage_artifact(
        Path(run_root), attempt_id, "context-marker:baseline", marker_digest,
    )
    if marker_publication.get("artifact") != marker:
        raise ValueError("REVIEWER_PROTOCOL: context-marker output readback differs")
    fragments = [_plain(item) for item in generator_fragments]
    fragments_by_batch = {
        item.get("batch_id"): item for item in fragments if isinstance(item, dict)
    }
    assembly_batches = assembly.get("fragment_digests")
    if (
        not fragments
        or len(fragments_by_batch) != len(fragments)
        or not isinstance(assembly_batches, list)
        or set(fragments_by_batch) != {item.get("batch_id") for item in assembly_batches}
    ):
        raise ValueError("REVIEWER_PROTOCOL: generator fragments are invalid")
    contexts_by_digest = {item["digest"]: item for item in contexts}
    generator_batches = []
    model_bytes = marker_publication["byte_count"]
    for expected in assembly_batches:
        fragment = fragments_by_batch.get(expected.get("batch_id"))
        context_digest = fragment.get("context_receipt_digest") if isinstance(fragment, dict) else None
        if (
            not isinstance(fragment, dict)
            or fragment.get("status") != "COMPLETE"
            or context_digest not in contexts_by_digest
            or fragment.get("context_receipt") != contexts_by_digest[context_digest]
        ):
            raise ValueError("REVIEWER_PROTOCOL: generator fragment is invalid")
        publication = read_model_stage_artifact(
            Path(run_root), attempt_id, f"tc-generator:{fragment['batch_id']}",
            str(expected.get("digest", "")),
        )
        if publication["content_digest"] != expected.get("digest") or publication.get("artifact") != fragment:
            raise ValueError("REVIEWER_PROTOCOL: generator fragment does not match assembly")
        model_bytes += publication["byte_count"]
        generator_batches.append({
            "batch_id": fragment["batch_id"],
            "digest": publication["content_digest"],
            "context_receipt_digest": context_digest,
        })
    assembly_events = [
        event for event in derive_state(Path(run_root))["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") == "CANDIDATE_PUBLISHED"
        and event.get("stage_instance_id") == "assembly"
        and event.get("artifact_digest") == document_sha256(candidate_value)
    ]
    if len(assembly_events) != 1:
        raise ValueError("REVIEWER_PROTOCOL: assembled candidate publication is unbound")
    raw = [canonical_bytes(candidate_value), canonical_bytes(_receipt_value(candidate_receipt)), canonical_bytes(assembly), canonical_bytes(inventory), *(canonical_bytes(item) for item in contexts)]
    binding = {"candidate_digest": document_sha256(candidate_value), "candidate_receipt_digest": "sha256:" + hashlib.sha256(raw[1]).hexdigest(), "assembly_digest": assembly["digest"], "context_marker_output_digest": marker_publication["content_digest"], "generator_batches": generator_batches, "inventory_digest": inventory["digest"], "context_receipt_digests": [item["digest"] for item in contexts], "base_package_byte_count": sum(len(item) for item in raw) + model_bytes + sum(item["byte_count"] for item in contexts)}
    binding["package_digest"] = "sha256:" + hashlib.sha256(canonical_bytes(binding)).hexdigest()
    return binding


def open_reviewer_session(run_root: Path, attempt_id: str, package_binding: Mapping[str, Any], session_start: Mapping[str, Any]) -> dict[str, Any]:
    """Reserve the only canonical reviewer session for an active attempt before invocation."""
    from tools.pilot_state import append_event, publish_attempt_receipt, read_attempt_receipt

    package = _plain(package_binding)
    start = _plain(session_start)
    required = {"session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes"}
    isolation = start.get("host_isolation")
    if (
        set(start) != required
        or package.get("package_digest") != "sha256:" + hashlib.sha256(canonical_bytes({key: item for key, item in package.items() if key != "package_digest"})).hexdigest()
        or not isinstance(isolation, dict)
        or isolation.get("evidence_digest") != _host_isolation_digest(isolation)
    ):
        raise ValueError("REVIEWER_PROTOCOL: invalid reviewer session start")
    facts = {
        **start,
        "canonical_branch_digest": package["candidate_digest"],
        "package_digest": package["package_digest"],
    }
    published = publish_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", facts)
    append_event(Path(run_root), "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    append_event(Path(run_root), "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    boundary = dict(read_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"])
    created = {
        "schema_version": "1.0.0",
        "session_id": start["session_id"],
        "boundary_digest": boundary["digest"],
        "package_binding": package,
        "context_budget_bytes": start["context_budget_bytes"],
        "generator_role": start["generator_role"],
        "reviewer_role": start["reviewer_role"],
        "generator_invocation_id": start["generator_invocation_id"],
        "reviewer_invocation_id": start["reviewer_invocation_id"],
        "host_isolation": start["host_isolation"],
        "events": [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}],
        "status": "WAITING",
    }
    created["digest"] = _session_digest(created)
    _publish_reviewer_ledger(Path(run_root), attempt_id, created)
    return boundary


def _read_reviewer_boundary(run_root: Path, attempt_id: str, package: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import read_attempt_receipt

    record = dict(read_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"])
    expected = {
        "session_id": session.get("session_id"),
        "canonical_branch_digest": package.get("candidate_digest"),
        "package_digest": package.get("package_digest"),
        "generator_role": session.get("generator_role"),
        "reviewer_role": session.get("reviewer_role"),
        "generator_invocation_id": session.get("generator_invocation_id"),
        "reviewer_invocation_id": session.get("reviewer_invocation_id"),
        "host_isolation": session.get("host_isolation"),
        "context_budget_bytes": session.get("context_budget_bytes"),
    }
    if session.get("boundary_digest") != record.get("digest") or any(record.get(key) != item for key, item in expected.items()):
        raise ValueError("REVIEWER_PROTOCOL: reviewer session does not match its immutable attempt boundary")
    return record


def _publish_reviewer_ledger(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> str:
    from tools.pilot_state import publish_reviewer_session_ledger

    receipt = publish_reviewer_session_ledger(Path(run_root), attempt_id, session)
    if receipt.get("record") != dict(session) or receipt.get("digest") != session.get("digest"):
        raise ValueError("REVIEWER_PROTOCOL: reviewer session ledger readback failed")
    return str(receipt["digest"])


def validate_reviewer_session(session: Mapping[str, Any], package_binding: Mapping[str, Any], review_artifact: Mapping[str, Any] | None = None, *, run_root: Path, attempt_id: str, evidence_receipts: Sequence[Mapping[str, Any]] = (), effective_canonical: bool = False) -> dict[str, Any]:
    """Validate the single fresh reviewer ledger and its bounded evidence sequence."""
    value = _plain(session)
    rows = schema_diagnostics(value, REVIEWER_SESSION_SCHEMA, ROOT)
    if rows or value.get("digest") != _session_digest(value):
        raise ValueError("REVIEWER_PROTOCOL: invalid reviewer session receipt")
    package = _plain(package_binding)
    if value["package_binding"] != package or package.get("package_digest") != "sha256:" + hashlib.sha256(canonical_bytes({key: item for key, item in package.items() if key != "package_digest"})).hexdigest():
        raise ValueError("REVIEWER_PROTOCOL: package binding is invalid")
    _read_reviewer_boundary(Path(run_root), attempt_id, package, value)
    events = value["events"]
    from tools.pilot_state import reviewer_lifecycle_projection

    try:
        lifecycle = reviewer_lifecycle_projection(value)
    except ValueError as error:
        raise ValueError("REVIEWER_PROTOCOL: event order is invalid") from error
    evidence = [_plain(item) for item in evidence_receipts]
    if len({item.get("digest") for item in evidence}) != len(evidence):
        raise ValueError("REVIEWER_PROTOCOL: evidence receipts are duplicated")
    for item in evidence:
        try:
            from tools.pilot_state import read_context_selection
            readback = read_context_selection(Path(run_root), attempt_id, str(item.get("digest", "")))
        except ValueError:
            readback = None
        if schema_diagnostics(item, CONTEXT_RECEIPT_SCHEMA, ROOT) or item.get("digest") != _session_digest(item) or item.get("inventory_digest") != package["inventory_digest"] or item.get("digest") in package["context_receipt_digests"] or readback != item:
            raise ValueError("REVIEWER_PROTOCOL: evidence receipt is invalid")
    pending: tuple[str, int] | None = None
    evidence_digests: list[str] = []
    evidence_bytes = package["base_package_byte_count"]
    evidence_index = 0
    for event in events[1:]:
        kind = event["event_type"]
        if kind == "EVIDENCE_REQUESTED":
            if pending is not None or not isinstance(event.get("request_digest"), str) or type(event.get("byte_count")) is not int or event["byte_count"] < 0:
                raise ValueError("REVIEWER_PROTOCOL: invalid evidence request")
            pending = (event["request_digest"], event["byte_count"])
        elif kind == "EVIDENCE_PROVIDED":
            if pending is None or event.get("request_digest") != pending[0] or evidence_index >= len(evidence):
                raise ValueError("REVIEWER_PROTOCOL: invalid evidence response")
            receipt = evidence[evidence_index]
            if event.get("provided_digest") != receipt["digest"] or event.get("byte_count") != receipt["byte_count"] or receipt["byte_count"] > pending[1]:
                raise ValueError("REVIEWER_PROTOCOL: evidence response is not bound to exact receipt bytes")
            evidence_bytes += receipt["byte_count"]
            evidence_digests.append(receipt["digest"])
            expected_cumulative = "sha256:" + hashlib.sha256(canonical_bytes({"package_digest": package["package_digest"], "evidence_digests": evidence_digests})).hexdigest()
            if evidence_bytes > value["context_budget_bytes"]:
                raise ValueError("REVIEWER_PROTOCOL: evidence was transferred beyond the context ceiling")
            if event.get("cumulative_package_digest") != expected_cumulative:
                raise ValueError("REVIEWER_PROTOCOL: cumulative evidence package digest is invalid")
            pending = None
            evidence_index += 1
        elif kind in {"AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED", "REVIEW_SESSION_ABORTED"}:
            continue
    if lifecycle["waiting"]:
        ledger_digest = _publish_reviewer_ledger(Path(run_root), attempt_id, value)
        return {"attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None, "independence": "independence_unverified", "acceptance_eligible": False, "verdict_count": 0, "reviewer_session_digest": ledger_digest}
    if evidence_index != len(evidence):
        raise ValueError("REVIEWER_PROTOCOL: evidence receipt was not transferred")
    if effective_canonical and lifecycle["authoritative_verdict_count"] != 1:
        raise ValueError("REVIEWER_PROTOCOL: terminal verdict invariant is invalid")
    isolation = value["host_isolation"]
    invocation_distinct = value["generator_invocation_id"] != value["reviewer_invocation_id"]
    if isolation["distinct_invocations"] != invocation_distinct or isolation.get("evidence_digest") != _host_isolation_digest(isolation):
        raise ValueError("REVIEWER_PROTOCOL: contradictory invocation isolation evidence")
    verified = bool(isolation["fresh_context"] and isolation["distinct_invocations"] and isolation["role_policy"] == "canonical-reviewer-v1")
    if lifecycle["pre_verdict_abort"]:
        if review_artifact is not None:
            raise ValueError("REVIEWER_PROTOCOL: aborted session cannot have review artifact")
        reason = lifecycle["abort_reason"]
        if evidence_bytes > value["context_budget_bytes"] and reason != "REVIEW_CONTEXT_LIMIT":
            raise ValueError("REVIEWER_PROTOCOL: base package exceeds the context ceiling")
        ledger_digest = _publish_reviewer_ledger(Path(run_root), attempt_id, value)
        return {"attempt_state": "TERMINAL", "coverage": None, "independence": "independence_unverified", "acceptance_eligible": False, "verdict_count": 0, "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "accepted": False, "reason_code": reason, "reviewer_session_digest": ledger_digest}
    if evidence_bytes > value["context_budget_bytes"]:
        raise ValueError("REVIEW_CONTEXT_LIMIT")
    if review_artifact is None:
        raise ValueError("REVIEWER_PROTOCOL: completed session needs review artifact")
    if _review_rows(review_artifact):
        raise ValueError("REVIEWER_PROTOCOL: completed session review artifact is invalid")
    report = _plain(review_artifact).get("artifacts", {}).get("validation_report")
    verdict_event = next(event for event in events if event["event_type"] == "AUTHORITATIVE_VERDICT")
    if not reviewer_verdict_is_bound(report, verdict_event):
        raise ValueError("REVIEWER_PROTOCOL: verdict is not bound to review report")
    ledger_digest = _publish_reviewer_ledger(Path(run_root), attempt_id, value)
    verdict_count = lifecycle["authoritative_verdict_count"]
    return {"independence": "verified" if verified else "independence_unverified", "acceptance_eligible": verified and verdict_count == 1, "verdict_count": verdict_count, "reviewer_session_digest": ledger_digest}


def publish_unreviewed_candidate(
    candidate: Mapping[str, Any],
    output_dir: str | os.PathLike[str],
    csv_profile: str = _PROFILE,
    *,
    run_root: Path,
    attempt_id: str,
    publisher=publish_bundle,
    verifier=verify_bundle,
) -> Receipt:
    value = _plain(candidate); require_valid_canonical_document(value)
    receipt = publisher(value, output_dir, csv_profile)
    if _receipt_rows(receipt, value, output_dir, csv_profile) or verifier(value, output_dir, csv_profile) != receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    from tools.pilot_state import append_event

    append_event(
        run_root, "CANDIDATE_PUBLISHED", actor="controller",
        attempt_id=attempt_id, stage_instance_id="assembly",
        artifact_digest=document_sha256(value),
    )
    return _receipt_copy(receipt)


def orchestrate_revision(candidate: Mapping[str, Any], candidate_receipt: Receipt, package_binding: Mapping[str, Any], reviewer_session: Mapping[str, Any], review_artifact: Mapping[str, Any] | None, output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, *, run_root: Path, attempt_id: str, reviewer_evidence_receipts: Sequence[Mapping[str, Any]] = (), publisher=publish_bundle, verifier=verify_bundle) -> OrchestrationResult:
    """Select the published r1 and publish reviewer-produced r2 only when needed."""
    _validate_profile(csv_profile)
    candidate_value, review_value = _plain(candidate), None if review_artifact is None else _plain(review_artifact)
    try:
        require_valid_canonical_document(candidate_value)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_CANDIDATE")) from None
    rows = _receipt_rows(candidate_receipt, candidate_value, output_dir, csv_profile)
    if rows or verifier(_plain(candidate_value), output_dir, csv_profile) != candidate_receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    candidate_receipt = _receipt_copy(candidate_receipt)
    if review_value is None:
        facts = validate_reviewer_session(reviewer_session, package_binding, None, run_root=run_root, attempt_id=attempt_id, evidence_receipts=reviewer_evidence_receipts)
        if facts.get("attempt_state") == "WAITING_FOR_MODEL":
            return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "WAITING_FOR_MODEL", _freeze(facts), ())
        if facts.get("completion") == "PARTIAL":
            return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, str(facts.get("reason_code", "REWORK")), _freeze(facts), ())
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/review_artifact", "REVIEWER_PROTOCOL")], candidate_receipt)
    rows = _review_rows(review_value)
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows, candidate_receipt) from None
    report = review_value["artifacts"]["validation_report"]
    successor = review_value["artifacts"].get("successor_document")
    verdict = report["verdict"]
    facts = validate_reviewer_session(
        reviewer_session, package_binding, review_value,
        run_root=run_root, attempt_id=attempt_id,
        evidence_receipts=reviewer_evidence_receipts,
        effective_canonical=verdict != "ТРЕБУЕТ ДОРАБОТКИ",
    )
    if not facts["acceptance_eligible"]:
        raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag("/reviewer_session", "INDEPENDENCE_UNVERIFIED")], candidate_receipt)
    decision_rows = validate_review_decision(candidate_value, report, successor)
    if decision_rows:
        raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(decision_rows, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
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
        successor_receipt = None
        if successor is not None:
            raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/successor_document", "ORCHESTRATION_SUCCESSOR")], candidate_receipt) from None
        effective = None
        if verdict != "ТРЕБУЕТ ДОРАБОТКИ":
            try:
                effective = select_effective_document(_plain(candidate_value), _plain(report), None)
            except SelectionError as error:
                raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        rework_facts = {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": None, "reason_code": "REWORK", "accepted": False, "reviewer_session_digest": facts["reviewer_session_digest"]}
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "REWORK", _freeze(rework_facts), ())
    try:
        verified = verifier(_plain(effective), output_dir, csv_profile)
    except Exception:
        raise OrchestrationError("ORCHESTRATION_VERIFICATION", [_diag("", "ORCHESTRATION_VERIFICATION")], candidate_receipt, successor_receipt) from None
    rows = _receipt_rows(verified, effective, output_dir, csv_profile)
    publication_receipt = successor_receipt if successor_receipt is not None else candidate_receipt
    if rows or verified != publication_receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")], candidate_receipt, successor_receipt) from None
    try:
        from tools.pilot_state import publish_effective_canonical

        published_effective = publish_effective_canonical(
            Path(run_root), attempt_id, _plain(effective), _receipt_value(publication_receipt), str(facts["reviewer_session_digest"]),
        )
        if published_effective["record"].get("document") != _plain(effective):
            raise ValueError("effective canonical read-back mismatch")
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag("/effective_canonical", "ORCHESTRATION_EFFECTIVE_RECEIPT")], candidate_receipt, successor_receipt) from error
    return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None if successor is None else _freeze(successor), successor_receipt, _freeze(effective), _receipt_copy(publication_receipt), "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())


def _source(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(_plain(document))}


def _source_rows(value: Any, expected: Mapping[str, Any], path: str) -> list[dict[str, str]]:
    return [] if isinstance(value, Mapping) and dict(value) == dict(expected) else [_diag(path, "ORCHESTRATION_SOURCE")]


def _readback_rows(receipt: Any, document: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = _receipt_rows(receipt, document, None, receipt.csv_profile if type(receipt) is Receipt else "")
    if rows:
        return rows
    paths = (Path(receipt.json_path), Path(receipt.preview_path), Path(receipt.csv_path))
    if len({path.parent.resolve() for path in paths}) != 1:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    try:
        verified = verify_bundle(dict(document), paths[0].parent, receipt.csv_profile)
    except Exception:
        return [_diag("/receipt", "ORCHESTRATION_PUBLICATION_READBACK")]
    return [] if verified == receipt else [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")]


def finalize_orchestration(candidate_document: Mapping[str, Any], tc_review_artifact: Mapping[str, Any], effective_document: Mapping[str, Any], effective_bundle_receipt: Receipt, automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], run_result: Mapping[str, Any] | None, trace_document: Mapping[str, Any], trace_audit_artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a V5 pre-finalization projection, never a terminal authority."""
    candidate, tc_review, document, automation, review, trace, audit = _plain(candidate_document), _plain(tc_review_artifact), _plain(effective_document), _plain(automation_artifact), _plain(autotest_review_artifact), _plain(trace_document), _plain(trace_audit_artifact)
    try:
        require_valid_canonical_document(candidate)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_CANDIDATE")) from None
    try:
        require_valid_canonical_document(document)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_DOCUMENT")) from None
    expected = _source(document)
    rows = _readback_rows(effective_bundle_receipt, document)
    if rows:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows) from None
    review_rows = _review_rows(tc_review)
    if review_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(review_rows, "ORCHESTRATION_TC_REVIEW"))
    report = tc_review["artifacts"]["validation_report"]
    successor = tc_review["artifacts"].get("successor_document")
    decision_rows = validate_review_decision(candidate, report, successor)
    if decision_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(decision_rows, "ORCHESTRATION_TC_REVIEW"))
    try:
        selected = select_effective_document(candidate, report, successor)
    except SelectionError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_TC_REVIEW")) from None
    if selected != document:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/effective_document", "ORCHESTRATION_TC_REVIEW_EFFECTIVE")])
    from tools.automation_validation import required_symbol_pairs, validate_automation_artifact
    automation_rows = validate_automation_artifact(automation, document)
    if automation_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(automation_rows, "ORCHESTRATION_AUTOMATION"))
    from tools.automation_validation import automation_sha256, validate_accepted_autotest_review
    review_rows = validate_accepted_autotest_review(review, automation, document)
    if review_rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(review_rows, "ORCHESTRATION_AUTOTEST_REVIEW"))
    reviewed = review["artifacts"]["autotest_review"]
    rows = _source_rows(reviewed["source"], expected, "/artifacts/autotest_review/source")
    pairs = required_symbol_pairs(automation, document)
    no_run = automation["artifacts"]["automation_status"] == "BLOCKED" or not pairs
    run = None if run_result is None else _plain(run_result)
    if no_run != (run is None):
        rows.append(_diag("/run_result", "ORCHESTRATION_RUN_BRANCH"))
    if run is not None and not isinstance(run, Mapping):
        rows.append(_diag("/run_result", "ORCHESTRATION_RUN_INVALID"))
    elif run is not None:
        rows.extend(_source_rows(run.get("source"), expected, "/run_result/source"))
        if run.get("automation_sha256") != automation_sha256(automation):
            rows.append(_diag("/run_result/automation_sha256", "ORCHESTRATION_RUN_AUTOMATION_DIGEST"))
        if run.get("autotest_review_sha256") != automation_sha256(review):
            rows.append(_diag("/run_result/autotest_review_sha256", "ORCHESTRATION_RUN_REVIEW_DIGEST"))
    from tools.build_trace_document import validate_trace_document
    trace_rows = validate_trace_document(trace, document, automation, review, run)
    if trace_rows:
        rows.extend(_safe(trace_rows, "ORCHESTRATION_TRACE"))
    rows.extend(_source_rows(trace.get("source"), expected, "/trace_document/source"))
    audit_rows = schema_diagnostics(audit, TRACE_AUDIT_SCHEMA, ROOT)
    if audit_rows:
        rows.extend(_safe(audit_rows, "ORCHESTRATION_TRACE_AUDIT"))
    else:
        from tools.trace_check import check
        recomputed_audit = check(trace, document, automation, review, run, require_execution=not no_run)
        if audit != recomputed_audit:
            rows.append(_diag("/trace_audit", "ORCHESTRATION_TRACE_AUDIT_MISMATCH"))
        elif not (audit["valid"] is True and audit["trace_audit"]["verdict"] == "PASS" and not audit["trace_audit"]["errors"] and not audit["errors"]):
            rows.append(_diag("/trace_audit", "ORCHESTRATION_TRACE_AUDIT_INVALID"))
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows)
    result = {
        "schema_version": "5.0.0", "stage": "orchestrate", "warnings": [],
        "artifacts": {"orchestration_result": {
            "tc_review_candidate_source": _source(candidate), "tc_review_effective_source": expected, "effective_source": expected, "effective_bundle_receipt": {field: getattr(effective_bundle_receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")},
            "automation_source": _plain(automation["artifacts"]["source"]), "autotest_review_source": _plain(reviewed["source"]),
            "automation_sha256": automation_sha256(automation), "autotest_review_sha256": automation_sha256(review), "run_source": None if run is None else _plain(run["source"]), "trace_source": _plain(trace["source"]), "trace_sha256": audit["trace_audit"]["trace_sha256"],
            "automation_status": automation["artifacts"]["automation_status"], "autotest_review_verdict": "ПРИНЯТО", "run_verdict": None if run is None else run["verdict"], "lifecycle": _plain(trace["lifecycle"]),
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
    return None if receipt is None else {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")}


def _load_receipt(path: Path) -> Receipt:
    value = load_json_strict(path)
    fields = tuple(Receipt.__dataclass_fields__)
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError("candidate receipt has an invalid shape")
    return Receipt(*(value[field] for field in fields))


def _read_existing_context_receipts(paths: Sequence[Path], run_root: Path, attempt_id: str) -> list[Mapping[str, Any]]:
    """Treat CLI context files as immutable digest pointers, never as evidence bytes."""
    from tools.pilot_state import read_context_selection

    result: list[Mapping[str, Any]] = []
    for path in paths:
        pointer = load_json_strict(path)
        if not isinstance(pointer, Mapping) or not isinstance(pointer.get("digest"), str):
            raise ValueError("invalid context receipt pointer")
        result.append(read_context_selection(run_root, attempt_id, pointer["digest"]))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="Publish and read back revision 1 as UNREVIEWED.")
    publish.add_argument("--candidate", required=True, type=Path)
    publish.add_argument("--output-dir", required=True, type=Path)
    publish.add_argument("--run-root", required=True, type=Path)
    publish.add_argument("--attempt-id", required=True)
    publish.add_argument("--csv-profile", default=_PROFILE)
    evidence = commands.add_parser("publish-evidence", help="Persist and read back one reviewer C-lite evidence receipt.")
    evidence.add_argument("--context-receipt", required=True, type=Path)
    evidence.add_argument("--run-root", required=True, type=Path)
    evidence.add_argument("--attempt-id", required=True)
    open_session = commands.add_parser("open-session", help="Reserve the attempt's only host-owned canonical reviewer session.")
    open_session.add_argument("--candidate", required=True, type=Path)
    open_session.add_argument("--candidate-receipt", required=True, type=Path)
    open_session.add_argument("--assembly-receipt", required=True, type=Path)
    open_session.add_argument("--inventory-receipt", required=True, type=Path)
    open_session.add_argument("--context-receipt", required=True, action="append", type=Path)
    open_session.add_argument("--context-marker-output", required=True, type=Path)
    open_session.add_argument("--generator-fragment", required=True, action="append", type=Path)
    open_session.add_argument("--session-start", required=True, type=Path)
    open_session.add_argument("--run-root", required=True, type=Path)
    open_session.add_argument("--attempt-id", required=True)
    open_session.add_argument("--csv-profile", default=_PROFILE)
    select = commands.add_parser("select", help="Validate one host-owned reviewer session and select the effective revision.")
    select.add_argument("--candidate", required=True, type=Path)
    select.add_argument("--candidate-receipt", required=True, type=Path)
    select.add_argument("--assembly-receipt", required=True, type=Path)
    select.add_argument("--inventory-receipt", required=True, type=Path)
    select.add_argument("--context-receipt", required=True, action="append", type=Path)
    select.add_argument("--context-marker-output", required=True, type=Path)
    select.add_argument("--generator-fragment", required=True, action="append", type=Path)
    select.add_argument("--reviewer-session", required=True, type=Path)
    select.add_argument("--evidence-receipt", action="append", default=[], type=Path)
    select.add_argument("--review", type=Path)
    select.add_argument("--run-root", required=True, type=Path)
    select.add_argument("--attempt-id", required=True)
    select.add_argument("--output-dir", required=True, type=Path)
    select.add_argument("--csv-profile", default=_PROFILE)
    args = parser.parse_args(argv)
    if hasattr(args, "csv_profile") and args.csv_profile not in _PROFILES:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("/csv_profile", "ORCHESTRATION_PROFILE")], "status": "error", "successor_bundle_receipt": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    try:
        if args.command == "publish-evidence":
            from tools.pilot_state import publish_context_selection

            receipt = publish_context_selection(args.run_root, args.attempt_id, load_json_strict(args.context_receipt))
            print(json.dumps({"context_receipt": receipt, "status": "published"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        candidate = load_json_strict(args.candidate)
        if args.command == "publish":
            receipt = publish_unreviewed_candidate(
                candidate, args.output_dir, args.csv_profile,
                run_root=args.run_root, attempt_id=args.attempt_id,
            )
            print(json.dumps({"candidate_bundle_receipt": _receipt_json(receipt), "candidate_publication_status": "UNREVIEWED", "status": "published"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        receipt = _load_receipt(args.candidate_receipt)
        base_contexts = [load_json_strict(path) for path in args.context_receipt]
        if args.command == "open-session":
            from tools.pilot_state import publish_context_selection

            base_contexts = [publish_context_selection(args.run_root, args.attempt_id, value) for value in base_contexts]
        else:
            base_contexts = _read_existing_context_receipts(args.context_receipt, args.run_root, args.attempt_id)
        package = reviewer_package_binding(
            candidate,
            receipt,
            load_json_strict(args.assembly_receipt),
            load_json_strict(args.inventory_receipt),
            base_contexts,
            context_marker_output=load_json_strict(args.context_marker_output),
            generator_fragments=[load_json_strict(path) for path in args.generator_fragment],
            run_root=args.run_root,
            attempt_id=args.attempt_id,
        )
        if args.command == "open-session":
            boundary = open_reviewer_session(args.run_root, args.attempt_id, package, load_json_strict(args.session_start))
            print(json.dumps({"package_binding": package, "reviewer_session_boundary": boundary, "status": "opened"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        result = orchestrate_revision(
            candidate,
            receipt,
            package,
            load_json_strict(args.reviewer_session),
            None if args.review is None else load_json_strict(args.review),
            args.output_dir,
            args.csv_profile,
            run_root=args.run_root,
            attempt_id=args.attempt_id,
            reviewer_evidence_receipts=_read_existing_context_receipts(args.evidence_receipt, args.run_root, args.attempt_id),
        )
    except OrchestrationError as error:
        exit_code = 2 if error.code in {"ORCHESTRATION_PUBLICATION", "ORCHESTRATION_VERIFICATION"} else 1
        print(json.dumps({"candidate_bundle_receipt": _receipt_json(error.candidate_bundle_receipt), "code": error.code, "diagnostics": [dict(row) for row in error.diagnostics], "status": "error", "successor_bundle_receipt": _receipt_json(error.successor_bundle_receipt)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return exit_code
    except (StrictJsonError, OSError, ValueError):
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("", "ORCHESTRATION_IO")], "status": "error", "successor_bundle_receipt": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    output = {"candidate_bundle_receipt": _receipt_json(result.candidate_bundle_receipt), "diagnostics": [], "effective_bundle_receipt": _receipt_json(result.effective_bundle_receipt), "effective_source": None if result.effective_document is None else _source(result.effective_document), "facts": _plain(result.facts), "status": result.status, "successor_bundle_receipt": _receipt_json(result.successor_bundle_receipt)}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
