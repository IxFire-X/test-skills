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
from tools.fixes import FIXES
from tools import output_guard
from tools.json_cli import error_code_prefix, error_envelope
from tools.publish_test_case_bundle import Receipt, publish_bundle, verify_bundle
from tools.revision_selection import SelectionError, reviewer_verdict_is_bound, select_effective_document, validate_review_decision
from tools.review_edits import reviewer_successor
from tools.schema_validation import StrictJsonError, classify_version, load_json_strict, schema_diagnostics
from tools.pilot_state import closure_cached as _closure_cached


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
_PROFILE_V5 = "zephyr-scale-step-row-24-v5"
_PROFILE = _PROFILE_V5
_PROFILES = frozenset({_PROFILE_V4, _PROFILE_V5})
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


# FaT-9b fix: contextlib (closure_cache) sets __traceback__ on the way out; the error's own fields stay immutable.
_EXCEPTION_SLOTS = frozenset({"__traceback__", "__cause__", "__context__", "__suppress_context__", "__notes__"})


class OrchestrationError(ValueError):
    """Immutable, value-redacting error with recoverable bundle receipts."""

    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]], candidate_bundle_receipt: Receipt | None = None, successor_bundle_receipt: Receipt | None = None) -> None:
        object.__setattr__(self, "code", str(code))
        object.__setattr__(self, "diagnostics", tuple(_freeze(dict(row)) for row in sorted(diagnostics, key=lambda row: (str(row.get("path", "")), str(row.get("code", "")), str(row.get("message", ""))))))
        object.__setattr__(self, "candidate_bundle_receipt", None if candidate_bundle_receipt is None else _receipt_copy(candidate_bundle_receipt))
        object.__setattr__(self, "successor_bundle_receipt", None if successor_bundle_receipt is None else _receipt_copy(successor_bundle_receipt))
        ValueError.__init__(self, json.dumps({"code": self.code, "diagnostics": [dict(row) for row in self.diagnostics]}, ensure_ascii=False, separators=(",", ":")))

    def __setattr__(self, name: str, value: object) -> None:
        if name not in _EXCEPTION_SLOTS and hasattr(self, "code"):
            raise AttributeError("OrchestrationError is immutable")
        object.__setattr__(self, name, value)


def _expected_paths(document: Mapping[str, Any], output_dir: str | os.PathLike[str], profile: str) -> tuple[str, str, str]:
    base = Path(output_dir).resolve() / f"{document['document_id']}.r{document['revision']}"
    return str(base) + ".json", str(base) + (".html" if profile in _PROFILES else ".md"), str(base) + ".zephyr-scale.csv"


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
        # FaT-ER: select got another --output-dir than publish — name both directories; same directory, other files: as before
        published = Path(str(receipt.json_path)).parent
        if Path(output_dir).resolve() == published:
            return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
        return [_diag("/receipt", "ORCHESTRATION_OUTPUT_DIR",
                      f"--output-dir {Path(output_dir).resolve()} не совпадает с каталогом publish {published}; "
                      f"fix: повтори с --output-dir {published}")]
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


def _marker_requirements(rows: Any, run_root: Path, attempt_id: str) -> Any:
    """FaT-P3: the requirements the candidate document holds — those of the marker without the ones the human excluded."""
    from tools.scope_pause import marker_requirements_in_scope

    return marker_requirements_in_scope(rows, Path(run_root), attempt_id)


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
        or _marker_requirements(marker_analytics.get("requirements"), run_root, attempt_id) != candidate_value.get("source_requirements")
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


@_closure_cached  # FaT-10d
def reviewer_session_start(
    run_root: Path,
    attempt_id: str,
    *,
    candidate: Mapping[str, Any],
    candidate_receipt: Path | Receipt,
    assembly_receipt: Mapping[str, Any],
    inventory_receipt: Mapping[str, Any],
    context_receipts: Sequence[Mapping[str, Any]],
    context_marker_output: Mapping[str, Any],
    generator_fragments: Sequence[Mapping[str, Any]],
    fresh_context: bool,
    reviewer_invocation_id: str,
) -> dict[str, Any]:
    """The ``--session-start`` of ``open-session``, from the open-session inputs and the journal; writes nothing.

    Budget rule: ``context_budget_bytes = base_package_byte_count + .skillsrc limits.context_batch_bytes`` (256 KiB by
    default) — the whole review package plus room for C-lite evidence of one context batch. ``fresh_context`` and the
    reviewer invocation are the host's statement; the generator invocation is the one of the first batch's
    ``MODEL_REQUESTED``; the code computes ``distinct_invocations``, the evidence digest and the budget."""
    from tools.pilot_state import derive_state, read_model_request, require_fresh_context_statement
    from tools.run_pipeline import _scan_context_budget

    require_fresh_context_statement(Path(run_root), fresh_context)
    package = reviewer_package_binding(
        candidate, candidate_receipt if isinstance(candidate_receipt, Receipt) else _load_receipt(Path(candidate_receipt)),
        assembly_receipt, inventory_receipt, context_receipts,
        context_marker_output=context_marker_output, generator_fragments=generator_fragments,
        run_root=Path(run_root), attempt_id=attempt_id,
    )
    stage = f"tc-generator:{package['generator_batches'][0]['batch_id']}"
    requested = [event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                 and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    if len(requested) != 1:
        raise ValueError(f"REVIEWER_PROTOCOL: {stage} has no single model request")
    generator = str(read_model_request(Path(run_root), attempt_id, stage, str(requested[0]["artifact_digest"]))["invocation_id"])
    reviewer = str(reviewer_invocation_id)
    isolation = {"fresh_context": fresh_context, "distinct_invocations": generator != reviewer,
                 "role_policy": "canonical-reviewer-v1"}
    isolation["evidence_digest"] = _host_isolation_digest(isolation)
    reserve = _scan_context_budget({}, Path(run_root).resolve().parent.parent)
    return {
        "session_id": f"review-{attempt_id}", "generator_role": "generator", "reviewer_role": "canonical-reviewer",
        "generator_invocation_id": generator, "reviewer_invocation_id": reviewer, "host_isolation": isolation,
        "context_budget_bytes": package["base_package_byte_count"] + reserve,
    }


@_closure_cached  # FaT-9b
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
    budget = start["context_budget_bytes"]
    if type(budget) is not int or budget < package["base_package_byte_count"]:  # FaT-10d: refuse now, not after the review
        raise ValueError(
            f"REVIEW_CONTEXT_LIMIT: context_budget_bytes {budget} is smaller than the review package "
            f"({package['base_package_byte_count']} bytes); build the session start with tools.api session-start"
        )
    facts = {
        **start,
        "canonical_branch_digest": package["candidate_digest"],
        "package_digest": package["package_digest"],
    }
    from tools.pilot_state import derive_state

    published = publish_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", facts)
    # P2-fix: an opening cut short is opened again — the events the first try wrote are not written twice
    recorded = {event["event_type"] for event in derive_state(Path(run_root))["events"]
                if event.get("attempt_id") == attempt_id and event.get("artifact_digest") == published["digest"]}
    for event_type in ("ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"):
        if event_type not in recorded:
            append_event(Path(run_root), event_type, actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
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


_VERDICT_EVENT = {"ПРИНЯТО": "ACCEPTED", "AUTO_FIX_APPLIED": "ACCEPTED", "ТРЕБУЕТ ДОРАБОТКИ": "REJECTED"}


@_closure_cached  # FaT-10c
def complete_reviewer_session(run_root: Path, attempt_id: str, *, review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """FaT-10c: the completed reviewer session for `select`, built from the published ledger and the recorded review.

    The session keeps the ledger events, then adds the one AUTHORITATIVE_VERDICT bound to the recorded
    tc-reviewer:canonical answer and REVIEW_SESSION_COMPLETED. Nothing is written: `select` validates and publishes it.
    P2: ``review`` is the answer of a dry run, not recorded yet — the session is built from it.
    """
    from tools.pilot_state import derive_state, read_model_stage_artifact, read_reviewer_session_ledger, reviewer_lifecycle_projection

    if review is None:
        responses = [event for event in derive_state(Path(run_root))["events"]
                     if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                     and event.get("stage_instance_id") == "tc-reviewer:canonical"]
        if len(responses) != 1:
            raise ValueError("REVIEWER_RESPONSE_MISSING: the tc-reviewer:canonical answer is not recorded; fix: tools.api review-select "
                             "--verdict <файл ответа ревьюера> записывает его и выбирает ревизию")
        review = read_model_stage_artifact(Path(run_root), attempt_id, "tc-reviewer:canonical", str(responses[0]["artifact_digest"]))["artifact"]
    report = _plain(review).get("artifacts", {}).get("validation_report")
    verdict = _VERDICT_EVENT.get(report.get("verdict")) if isinstance(report, dict) else None
    if verdict is None:
        raise ValueError("REVIEWER_VERDICT_INVALID: the recorded review has no ПРИНЯТО / AUTO_FIX_APPLIED / ТРЕБУЕТ ДОРАБОТКИ verdict")
    session = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id)))
    lifecycle = reviewer_lifecycle_projection(session)
    events = list(session["events"])
    bound = {
        "event_type": "AUTHORITATIVE_VERDICT", "verdict": verdict,
        "report_digest": "sha256:" + hashlib.sha256(canonical_bytes(report)).hexdigest(),
        "verdict_digest": "sha256:" + hashlib.sha256(canonical_bytes({"verdict": report.get("verdict"), "effective": report.get("effective")})).hexdigest(),
    }
    if not lifecycle["waiting"]:
        recorded = next((event for event in events if event.get("event_type") == "AUTHORITATIVE_VERDICT"), None)
        if session.get("status") == "COMPLETED" and recorded is not None and {key: recorded.get(key) for key in bound} == bound:
            return session  # already completed with this review: the same session again
        raise ValueError("REVIEWER_SESSION_CLOSED: the published reviewer session is already closed with another outcome")
    events.append({"ordinal": len(events) + 1, **bound})
    events.append({"ordinal": len(events) + 1, "event_type": "REVIEW_SESSION_COMPLETED"})
    session.update(events=events, status="COMPLETED")
    try:
        reviewer_lifecycle_projection(session)
    except ValueError as error:
        raise ValueError("REVIEWER_SESSION_OPEN_EVIDENCE: an evidence request has no answer yet") from error
    session["digest"] = _session_digest(session)
    return session


def _waiting_reviewer_session(run_root: Path, attempt_id: str) -> dict[str, Any]:
    from tools.pilot_state import read_reviewer_session_ledger, reviewer_lifecycle_projection

    session = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id)))
    if not reviewer_lifecycle_projection(session)["waiting"]:
        raise ValueError("REVIEWER_SESSION_CLOSED: the published reviewer session is already closed")
    return session


@_closure_cached  # FaT-10c
def provide_reviewer_evidence(run_root: Path, attempt_id: str, project_paths: Sequence[str]) -> dict[str, Any]:
    """FaT-10c: one C-lite evidence pair for the open reviewer session.

    The named project files of the attempt inventory become one context receipt (the one way to publish evidence since
    FaT-U5 removed publish-evidence);
    the ledger gets EVIDENCE_REQUESTED (the request is the sorted paths and the remaining budget) and EVIDENCE_PROVIDED
    with the cumulative package digest, and the WAITING ledger is published again.
    """
    from tools.pilot_state import derive_state, publish_context_selection
    from tools.project_inventory import ContextSelectionError, read_inventory_receipt, select_context_batches

    session = _waiting_reviewer_session(run_root, attempt_id)
    package = session["package_binding"]
    events = list(session["events"])
    provided = [event for event in events if event.get("event_type") == "EVIDENCE_PROVIDED"]
    remaining = session["context_budget_bytes"] - package["base_package_byte_count"] - sum(event["byte_count"] for event in provided)
    if remaining <= 0:
        raise ValueError("REVIEW_CONTEXT_LIMIT: no context budget is left for evidence; abort the session with REVIEW_CONTEXT_LIMIT")
    attempt = next(row for row in derive_state(Path(run_root))["attempts"] if row["attempt_id"] == attempt_id)
    inventory = read_inventory_receipt(Path(run_root) / "inventories" / (package["inventory_digest"].removeprefix("sha256:") + ".json"))
    by_path = {item["project_path"]: item["opaque_id"] for item in inventory["files"]}
    paths = sorted(set(project_paths))
    unknown = [path for path in paths if path not in by_path]
    if not paths or unknown:
        raise ValueError(f"REVIEWER_EVIDENCE_PATH_UNKNOWN: not in the attempt inventory: {unknown}")
    try:
        batches = select_context_batches(inventory, Path(attempt["project"]), [by_path[path] for path in paths],
                                         byte_budget=remaining, include_closed_manifests=False)
    except ContextSelectionError as error:
        raise ValueError(f"REVIEW_CONTEXT_LIMIT: the evidence does not fit the remaining {remaining} bytes ({error})") from error
    if len(batches) != 1:
        raise ValueError(f"REVIEW_CONTEXT_LIMIT: the evidence does not fit the remaining {remaining} bytes in one receipt")
    selected = batches[0]["receipt"]
    digests = [event["provided_digest"] for event in provided]
    if selected["digest"] in package["context_receipt_digests"] or selected["digest"] in digests:
        raise ValueError("REVIEWER_EVIDENCE_DUPLICATE: these files were already given to the reviewer")
    receipt = publish_context_selection(Path(run_root), attempt_id, selected)
    request_digest = "sha256:" + hashlib.sha256(canonical_bytes({"project_paths": paths, "byte_budget": remaining})).hexdigest()
    digests.append(receipt["digest"])
    events.append({"ordinal": len(events) + 1, "event_type": "EVIDENCE_REQUESTED", "request_digest": request_digest, "byte_count": remaining})
    events.append({
        "ordinal": len(events) + 1, "event_type": "EVIDENCE_PROVIDED", "request_digest": request_digest,
        "provided_digest": receipt["digest"], "byte_count": receipt["byte_count"],
        "cumulative_package_digest": "sha256:" + hashlib.sha256(canonical_bytes({"package_digest": package["package_digest"], "evidence_digests": digests})).hexdigest(),
    })
    session["events"] = events
    session["digest"] = _session_digest(session)
    return {"evidence_receipt": receipt, "reviewer_session_digest": _publish_reviewer_ledger(Path(run_root), attempt_id, session)}


# the pre-verdict aborts: the context ceiling, and FaT-AB D2 — a reviewer answer that twice failed its check or a select
# refusal, so the session cannot end in a verdict
_ABORT_REASONS = ("REVIEW_CONTEXT_LIMIT", "REVIEW_UNUSABLE")


@_closure_cached  # FaT-10c
def abort_reviewer_session(run_root: Path, attempt_id: str, reason_code: str) -> dict[str, Any]:
    """FaT-10c: the aborted reviewer session for `select` (no verdict). Nothing is written: `select` validates and publishes it."""
    from tools.pilot_state import reviewer_lifecycle_projection

    if reason_code not in _ABORT_REASONS:
        raise ValueError(f"REVIEWER_ABORT_REASON_INVALID: one of {list(_ABORT_REASONS)}")
    session = _waiting_reviewer_session(run_root, attempt_id)
    events = list(session["events"])
    events.append({"ordinal": len(events) + 1, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": reason_code})
    session.update(events=events, status="ABORTED")
    reviewer_lifecycle_projection(session)
    session["digest"] = _session_digest(session)
    return session


def _publish_reviewer_ledger(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> str:
    from tools.pilot_state import publish_reviewer_session_ledger

    receipt = publish_reviewer_session_ledger(Path(run_root), attempt_id, session)
    if receipt.get("record") != dict(session) or receipt.get("digest") != session.get("digest"):
        raise ValueError("REVIEWER_PROTOCOL: reviewer session ledger readback failed")
    return str(receipt["digest"])


def validate_reviewer_session(session: Mapping[str, Any], package_binding: Mapping[str, Any], review_artifact: Mapping[str, Any] | None = None, *, run_root: Path, attempt_id: str, evidence_receipts: Sequence[Mapping[str, Any]] = (), dry_run: bool = False, effective_canonical: bool = False) -> dict[str, Any]:
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
        ledger_digest = str(value["digest"]) if dry_run else _publish_reviewer_ledger(Path(run_root), attempt_id, value)  # P2: a dry run publishes nothing
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
    ledger_digest = str(value["digest"]) if dry_run else _publish_reviewer_ledger(Path(run_root), attempt_id, value)
    verdict_count = lifecycle["authoritative_verdict_count"]
    from tools.pilot_state import self_review_accepted

    # FaT-4b: an unisolated review is eligible only in a run started with `scan --accept-self-review`.
    admitted = verified or self_review_accepted(Path(run_root))
    return {"independence": "verified" if verified else "independence_unverified", "acceptance_eligible": admitted and verdict_count == 1, "verdict_count": verdict_count, "reviewer_session_digest": ledger_digest}


@_closure_cached  # FaT-9b
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
    from tools.pilot_state import append_event, derive_state

    # FaT-AU2: the same candidate again (after a lost stdout) gives the same receipt and writes no event; another one is
    # refused as before — now before any bundle file is written
    digest = document_sha256(value)
    recorded = [str(event.get("artifact_digest")) for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                and event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"]
    if recorded and recorded[0] != digest:
        raise ValueError(f"assembled candidate already published: {recorded[0]}, not {digest}")
    receipt = publisher(value, output_dir, csv_profile)
    if _receipt_rows(receipt, value, output_dir, csv_profile) or verifier(value, output_dir, csv_profile) != receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    if not recorded:
        append_event(
            run_root, "CANDIDATE_PUBLISHED", actor="controller",
            attempt_id=attempt_id, stage_instance_id="assembly",
            artifact_digest=digest,
        )
    return _receipt_copy(receipt)


def review_decision(candidate: Mapping[str, Any], review_value: Mapping[str, Any], *, imported: bool = False,
                    candidate_receipt: Receipt | None = None) -> tuple[Any, Any]:
    """FaT-R1: ``(successor, effective)`` of a reviewer answer, or OrchestrationError — every deterministic check of the
    decision, writing nothing: the review shape, the successor edits, the decision against the candidate, the successor
    document and (FaT-IMP) the human fields of an import.  select runs it before it publishes the ledger, model-response
    of tc-reviewer before it writes the answer, so a wrong answer keeps the stage's one retry."""
    candidate_value, review_value = _plain(candidate), _plain(review_value)
    rows = _review_rows(review_value)
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows, candidate_receipt) from None
    report = review_value["artifacts"]["validation_report"]
    try:
        successor = reviewer_successor(candidate_value, review_value["artifacts"])  # FaT-CV: a document or exact edits
    except SelectionError as error:
        raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    decision_rows = validate_review_decision(candidate_value, report, successor)
    if decision_rows:
        raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(decision_rows, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    verdict = report["verdict"]
    if verdict == "AUTO_FIX_APPLIED":
        try:
            require_valid_canonical_document(successor)
        except CanonicalDocumentError as error:
            raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_SUCCESSOR"), candidate_receipt) from None
        changed = _imported_human_changes(candidate_value, _plain(successor)) if imported else []
        if changed:
            raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag(path, "IMPORT_HUMAN_FIELD_CHANGED") for path in changed[:12]],
                                     candidate_receipt) from None
    elif successor is not None:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/successor_document", "ORCHESTRATION_SUCCESSOR")], candidate_receipt) from None
    effective = None
    if verdict != "ТРЕБУЕТ ДОРАБОТКИ":
        try:
            effective = select_effective_document(candidate_value, _plain(report), _plain(successor) if verdict == "AUTO_FIX_APPLIED" else None)
        except SelectionError as error:
            raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    return successor, effective


_IMPORTED_CASE_FIELDS = ("title", "objective", "preconditions", "priority")


def _imported_human_changes(candidate: Mapping[str, Any], successor: Mapping[str, Any]) -> list[str]:
    """FaT-R1: in an import the reviewer's fix may not touch the human fields the import fixed (the fields
    batch_assembly.require_imported_human_fields keeps): case title/objective/preconditions/priority, step action,
    test data and expectation texts — the machine part only."""
    before = {case["case_id"]: case for case in candidate.get("test_cases", [])}
    changed: list[str] = []
    for index, case in enumerate(successor.get("test_cases", [])):
        source = before.get(case.get("case_id"))
        if source is None:
            changed.append(f"/test_cases/{index}")
            continue
        changed += [f"/test_cases/{index}/{field}" for field in _IMPORTED_CASE_FIELDS if case.get(field) != source.get(field)]
        steps, original = case.get("steps") or [], source.get("steps") or []
        if len(steps) != len(original):
            changed.append(f"/test_cases/{index}/steps")
            continue
        for number, (step, row) in enumerate(zip(steps, original)):
            changed += [f"/test_cases/{index}/steps/{number}/{field}" for field in ("action", "test_data") if step.get(field) != row.get(field)]
            if [item.get("text") for item in step.get("expectations") or []] != [item.get("text") for item in row.get("expectations") or []]:
                changed.append(f"/test_cases/{index}/steps/{number}/expectations")
    if len(successor.get("test_cases", [])) != len(before):
        changed.append("/test_cases")
    return changed


def _run_imported(run_root: Path, attempt_id: str) -> bool:
    """Whether the attempt is an import of cases (FaT-IMP): its published cases are the human part."""
    from tools.pilot_state import _imported_cases, _run_root, derive_state

    try:
        project, root = _run_root(Path(run_root))
        return _imported_cases(project, root, derive_state(root), attempt_id) is not None
    except (KeyError, OSError, TypeError, ValueError):
        return False


@_closure_cached  # FaT-9b
def orchestrate_revision(candidate: Mapping[str, Any], candidate_receipt: Receipt, package_binding: Mapping[str, Any], reviewer_session: Mapping[str, Any], review_artifact: Mapping[str, Any] | None, output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, *, run_root: Path, attempt_id: str, reviewer_evidence_receipts: Sequence[Mapping[str, Any]] = (), publisher=publish_bundle, verifier=verify_bundle, dry_run: bool = False) -> OrchestrationResult:
    """Select the published r1 and publish reviewer-produced r2 only when needed.

    P2: ``dry_run`` runs every check of the decision and of the session and publishes nothing — no ledger, no r2 bundle, no
    effective canonical; the result is the selection the real call would make (receipts of what is not published are None)."""
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
    # FaT-R1: every deterministic check of the decision before validate_reviewer_session publishes the COMPLETED ledger
    successor, effective = review_decision(candidate_value, review_value, imported=_run_imported(run_root, attempt_id),
                                           candidate_receipt=candidate_receipt)
    report = review_value["artifacts"]["validation_report"]
    verdict = report["verdict"]
    facts = validate_reviewer_session(
        reviewer_session, package_binding, review_value,
        run_root=run_root, attempt_id=attempt_id,
        evidence_receipts=reviewer_evidence_receipts,
        effective_canonical=verdict != "ТРЕБУЕТ ДОРАБОТКИ", dry_run=dry_run,
    )
    if not facts["acceptance_eligible"]:
        raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag("/reviewer_session", "INDEPENDENCE_UNVERIFIED")], candidate_receipt)
    successor_receipt = None
    if verdict == "AUTO_FIX_APPLIED" and not dry_run:
        try:
            published_successor = publisher(_plain(effective), output_dir, csv_profile)
        except Exception:
            raise OrchestrationError("ORCHESTRATION_PUBLICATION", [_diag("", "ORCHESTRATION_PUBLICATION")], candidate_receipt) from None
        successor_receipt = _receipt_copy(published_successor) if type(published_successor) is Receipt else None
        rows = _receipt_rows(published_successor, effective, output_dir, csv_profile)
        if rows:
            raise OrchestrationError("ORCHESTRATION_RECEIPT", rows, candidate_receipt, successor_receipt) from None
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        rework_facts = {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": None, "reason_code": "REWORK", "accepted": False, "reviewer_session_digest": facts["reviewer_session_digest"]}
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "REWORK", _freeze(rework_facts), ())
    if dry_run and verdict == "AUTO_FIX_APPLIED":  # the successor is not published: its bundle is checked when it is
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", _freeze(successor), None, _freeze(effective), None, "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())
    try:
        verified = verifier(_plain(effective), output_dir, csv_profile)
    except Exception:
        raise OrchestrationError("ORCHESTRATION_VERIFICATION", [_diag("", "ORCHESTRATION_VERIFICATION")], candidate_receipt, successor_receipt) from None
    rows = _receipt_rows(verified, effective, output_dir, csv_profile)
    publication_receipt = successor_receipt if successor_receipt is not None else candidate_receipt
    if rows or verified != publication_receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")], candidate_receipt, successor_receipt) from None
    if dry_run:  # the effective canonical is not published
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None if successor is None else _freeze(successor), None, _freeze(effective), _receipt_copy(publication_receipt), "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())
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


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("", "ORCHESTRATION_ARGUMENT")], "status": "error", "successor_bundle_receipt": None,
                          "message": f"argument error: {message}", "fix": None, "next": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)


def _receipt_json(receipt: Receipt | None) -> dict[str, Any] | None:
    return None if receipt is None else {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")}


def publish_command(candidate: Mapping[str, Any], output_dir: Path, csv_profile: str, *, run_root: Path, attempt_id: str) -> dict[str, Any]:
    """`review-publish` (and the old `publish`): r1 as UNREVIEWED, the analyst questions beside it; the printed fields."""
    from tools.pilot_state import derive_state
    from tools.run_report import write_questions_next_to

    again = any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("attempt_id") == attempt_id
                and event.get("stage_instance_id") == "assembly" for event in derive_state(run_root)["events"])
    receipt = publish_unreviewed_candidate(candidate, output_dir, csv_profile, run_root=run_root, attempt_id=attempt_id)
    # FaT-6: the analyst questions beside the case set; a failed write only warns, the set is published.
    questions, code = write_questions_next_to(output_dir, run_root)
    return {"candidate_bundle_receipt": _receipt_json(receipt), "candidate_publication_status": "UNREVIEWED", "status": "published",
            "questions_for_analyst": questions, "warnings": [{"code": code}] if code else [], "idempotent": again}


def review_package(run_root: Path, attempt_id: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The reviewer package binding of ``review_inputs``."""
    return reviewer_package_binding(
        inputs["candidate"], inputs["candidate_receipt"], inputs["assembly_receipt"], inputs["inventory_receipt"],
        inputs["context_receipts"], context_marker_output=inputs["context_marker_output"],
        generator_fragments=inputs["generator_fragments"], run_root=Path(run_root), attempt_id=attempt_id)


def journal_review_artifact(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """FaT-U5: the tc-reviewer answer of a completed session, from the journal; None for an aborted or waiting one."""
    from tools.pilot_state import derive_state, read_model_stage_artifact

    if _plain(session).get("status") != "COMPLETED":
        return None
    digest = next((str(event["artifact_digest"]) for event in derive_state(Path(run_root))["events"]
                   if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "tc-reviewer:canonical"), None)
    return None if digest is None else read_model_stage_artifact(Path(run_root), attempt_id, "tc-reviewer:canonical", digest)["artifact"]


def journal_evidence_receipts(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """FaT-U5: the C-lite evidence receipts the session's ledger records as provided, in order."""
    from tools.pilot_state import read_context_selection

    return [read_context_selection(Path(run_root), attempt_id, str(event["provided_digest"]))
            for event in _plain(session).get("events", []) if event.get("event_type") == "EVIDENCE_PROVIDED"]


def select_output(result: OrchestrationResult) -> dict[str, Any]:
    return {"candidate_bundle_receipt": _receipt_json(result.candidate_bundle_receipt), "diagnostics": [],
            "effective_bundle_receipt": _receipt_json(result.effective_bundle_receipt),
            "effective_source": None if result.effective_document is None else _source(result.effective_document),
            "facts": _plain(result.facts), "status": result.status, "successor_bundle_receipt": _receipt_json(result.successor_bundle_receipt)}


REVIEW_STAGE = "tc-reviewer:canonical"


def _require_open_attempt(run_root: Path, attempt_id: str) -> None:
    """P2: a review command on a closed attempt answers ATTEMPT_TERMINAL at once (the P1 envelope), before it assembles anything."""
    from tools.pilot_state import derive_state

    attempt = next((row for row in derive_state(Path(run_root))["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is not None and attempt["state"] == "TERMINAL":
        raise ValueError(f"ATTEMPT_TERMINAL: the attempt {attempt_id} is closed; its review is over")


def review_step(step: str, run_root: Path, attempt_id: str, *, files: Mapping[str, Any], output_dir: Path | None = None,
                csv_profile: str = _PROFILE, **values: Any) -> Any:
    """FaT-U5: one tools.api command of the review layer — ``publish``, ``session-start``, ``open`` or ``select`` — over the
    journal inputs; ``files`` are the reviewer-package files the agent passed (compared, never the source).

    P2: the whole command runs in one ``closure_cache()`` and ``inventory_cache()`` — the journal, the receipts and the canon
    are read and checked once per process, not once per function; ``dry_run=True`` (select) publishes nothing."""
    from tools.pilot_state import closure_cache
    from tools.project_inventory import inventory_cache

    with closure_cache(), inventory_cache():
        if step == "publish":
            candidate = files.get("candidate")
            candidate = candidate if candidate is not None else journal_assembly(run_root, attempt_id)[0]
            return publish_command(candidate, output_dir, csv_profile, run_root=run_root, attempt_id=attempt_id)
        if step in {"open", "select"}:
            _require_open_attempt(run_root, attempt_id)
        receipt = files.get("candidate_receipt")
        given = {**files, "candidate_receipt": _load_receipt(Path(receipt)) if receipt else None}
        inputs = review_inputs(run_root, attempt_id, output_dir=output_dir, csv_profile=csv_profile, given=given)
        if step == "session-start":
            return reviewer_session_start(
                run_root, attempt_id, candidate=inputs["candidate"], candidate_receipt=inputs["candidate_receipt"],
                assembly_receipt=inputs["assembly_receipt"], inventory_receipt=inputs["inventory_receipt"],
                context_receipts=inputs["context_receipts"], context_marker_output=inputs["context_marker_output"],
                generator_fragments=inputs["generator_fragments"], fresh_context=values["fresh_context"],
                reviewer_invocation_id=values["reviewer_invocation_id"])
        package = review_package(run_root, attempt_id, inputs)
        if step == "open":
            return {"package_binding": package,
                    "reviewer_session_boundary": open_reviewer_session(run_root, attempt_id, package, values["session_start"])}
        session = values["reviewer_session"]
        review = values.get("review")
        review = review if review is not None else journal_review_artifact(run_root, attempt_id, session)
        return select_output(orchestrate_revision(
            inputs["candidate"], inputs["candidate_receipt"], package, session, review, output_dir, csv_profile,
            run_root=run_root, attempt_id=attempt_id, reviewer_evidence_receipts=journal_evidence_receipts(run_root, attempt_id, session),
            dry_run=bool(values.get("dry_run"))))


class ReviewRefused(ValueError):
    """P2: a refusal of the two-command review with its own code; the fix is the tail of the message, the table has the same."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _resolve_fresh_context(run_root: Path, fresh_context: bool | None) -> bool:
    """P2 (item 7): ``fresh_context`` is the host's statement.  A run that admitted SELF at scan (``--accept-self-review``,
    ``.skillsrc`` ``review.self``) needs none — the code says ``no`` itself; any other run must state it."""
    from tools.pilot_state import require_fresh_context_statement, self_review_accepted

    if fresh_context is None:
        if not self_review_accepted(Path(run_root)):
            raise ReviewRefused("FRESH_CONTEXT_REQUIRED", "review-open без --fresh-context: прогон не принимал ревью в той же сессии; "
                                "fix: --fresh-context yes — ревьюер отдельный субагент или вызов без истории генерации; ревью в той же "
                                "сессии — прогон должен начинаться с scan --accept-self-review (или review.self: true в .skillsrc)")
        fresh_context = False
    require_fresh_context_statement(Path(run_root), fresh_context)
    return fresh_context


def review_open_step(run_root: Path, attempt_id: str, *, files: Mapping[str, Any], output_dir: Path, csv_profile: str = _PROFILE,
                     model_id: str | None = None, invocation_id: str | None = None, fresh_context: bool | None = None,
                     session_start: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """P2: ``tools.api review-open --model-id --invocation-id [--fresh-context]`` — everything before the reviewer's call, in the
    events of the nine commands and their order: the candidate published (assemble → validate-canonical → review-publish, when it
    is not yet), the session start, the package and the boundary, REVIEW_REQUESTED, MODEL_REQUESTED.  It records and computes; the
    model is called by the agent afterwards.  The same call again changes nothing; another reviewer is REVIEW_ALREADY_OPEN."""
    from tools.pilot_state import (closure_cache, derive_state, publish_model_request, read_attempt_receipt, read_model_request,
                                   read_reviewer_session_ledger, request_review)
    from tools.project_inventory import inventory_cache

    if session_start is not None:  # the open-session of the nine (FaT-U5): the session start is the agent's file
        return review_step("open", run_root, attempt_id, files=files, output_dir=output_dir, csv_profile=csv_profile, session_start=session_start)
    missing = [flag for flag, value in (("--model-id", model_id), ("--invocation-id", invocation_id)) if value is None]
    if missing:
        raise ReviewRefused("INVALID_ARGUMENT", f"review-open без {' и '.join(missing)}; fix: review-open --model-id <метка модели ревьюера> "
                            "--invocation-id <id вызова ревьюера> --fresh-context yes|no — id тот же, что у вызова ревьюера")
    with closure_cache(), inventory_cache():
        _require_open_attempt(run_root, attempt_id)
        fresh = _resolve_fresh_context(run_root, fresh_context)
        events = [event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id]
        published = None
        if not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly" for event in events):
            published = publish_command(journal_assembly(run_root, attempt_id)[0], output_dir, csv_profile, run_root=run_root, attempt_id=attempt_id)
        receipt = files.get("candidate_receipt")
        given = {**files, "candidate_receipt": _load_receipt(Path(receipt)) if receipt else None}
        try:
            boundary = dict(read_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"])
        except (KeyError, TypeError, ValueError, OSError):
            boundary = None
        if boundary is not None:
            isolation = boundary.get("host_isolation", {})
            if boundary.get("reviewer_invocation_id") != invocation_id or (fresh_context is not None and isolation.get("fresh_context") != fresh):
                raise ReviewRefused("REVIEW_ALREADY_OPEN", f"ревью уже открыто с ревьюером {boundary.get('reviewer_invocation_id')} "
                                    f"(fresh_context={isolation.get('fresh_context')}); второй сессии нет; "
                                    "fix: повтори review-open с теми же --invocation-id и --fresh-context или иди дальше: review-select --verdict")
            try:  # an opening cut short between the boundary and the ledger: the boundary is there, the ledger is not — open it again
                package = _plain(read_reviewer_session_ledger(Path(run_root), attempt_id)["package_binding"])
            except (KeyError, TypeError, ValueError, OSError):
                boundary = None
        if boundary is None:
            inputs = review_inputs(run_root, attempt_id, output_dir=output_dir, csv_profile=csv_profile, given=given)
            start = reviewer_session_start(
                run_root, attempt_id, candidate=inputs["candidate"], candidate_receipt=inputs["candidate_receipt"],
                assembly_receipt=inputs["assembly_receipt"], inventory_receipt=inputs["inventory_receipt"],
                context_receipts=inputs["context_receipts"], context_marker_output=inputs["context_marker_output"],
                generator_fragments=inputs["generator_fragments"], fresh_context=fresh, reviewer_invocation_id=invocation_id)
            package = review_package(run_root, attempt_id, inputs)
            boundary = open_reviewer_session(run_root, attempt_id, package, start)
        requested = request_review(Path(run_root), attempt_id, REVIEW_STAGE, str(boundary["canonical_branch_digest"]))
        recorded = next((event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                         and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == REVIEW_STAGE), None)
        if recorded is None:
            request = publish_model_request(Path(run_root), attempt_id, REVIEW_STAGE, model_id=model_id, invocation_id=invocation_id,
                                            input_digests=[str(boundary["canonical_branch_digest"]), str(boundary["package_digest"])])
        else:
            request = read_model_request(Path(run_root), attempt_id, REVIEW_STAGE, str(recorded["artifact_digest"]))
            if request.get("invocation_id") != invocation_id or request.get("model_id") != model_id:
                raise ReviewRefused("REVIEW_ALREADY_OPEN", f"запрос ревьюера уже записан (модель {request.get('model_id')}, вызов "
                                    f"{request.get('invocation_id')}); fix: повтори review-open с теми же --model-id и --invocation-id")
        result: dict[str, Any] = {
            "package_binding": package, "reviewer_session_boundary": boundary, "review_requested": dict(requested),
            "model_request": dict(request),
            "next_step": f"ревьюер читает пакет и отвечает; затем tools.api review-select --run-root {run_root} --attempt {attempt_id} "
                         f"--output-dir {output_dir} --verdict <файл ответа ревьюера>"}
        if published is not None:
            result.update(candidate_bundle_receipt=published["candidate_bundle_receipt"], questions_for_analyst=published["questions_for_analyst"],
                          warnings=published["warnings"])
        return result


def _last_reviewer_session(run_root: Path, attempt_id: str, review: Mapping[str, Any] | None) -> dict[str, Any]:
    """P2: the session ``select`` works on when the agent names none — the last revision of the attempt's ledger: an aborted or
    completed one as it is, a waiting one completed with the recorded answer (or the answer of a dry run)."""
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        ledger = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id)))
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ReviewRefused("REVIEW_NOT_OPEN", "у попытки нет журнала сессии ревьюера; fix: сначала tools.api review-open --model-id <модель> "
                            "--invocation-id <id вызова ревьюера> --fresh-context yes|no") from error
    if ledger.get("status") in {"ABORTED", "COMPLETED"}:
        return ledger
    return complete_reviewer_session(run_root, attempt_id, review=review)


def _ledger_status(run_root: Path, attempt_id: str) -> str | None:
    """The status of the last revision of the attempt's reviewer ledger, or None when there is none to read."""
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        return _plain(read_reviewer_session_ledger(Path(run_root), attempt_id)).get("status")
    except (KeyError, TypeError, ValueError, OSError):
        return None


def review_select_step(run_root: Path, attempt_id: str, *, files: Mapping[str, Any], output_dir: Path, csv_profile: str = _PROFILE,
                       verdict: Mapping[str, Any] | None = None, verdict_bytes: int | None = None, transport_attempts: int = 1,
                       reviewer_session: Mapping[str, Any] | None = None, review: Mapping[str, Any] | None = None,
                       dry_run: bool = False) -> dict[str, Any]:
    """P2: ``tools.api review-select [--verdict <file>] [--dry-run]`` — MODEL_RESPONSE_RECEIVED (the stage keeps its one retry,
    ``--transport-attempts 2``), the completed session, the selection.  Without ``--reviewer-session`` the session is the last
    revision of the ledger.  ``dry_run`` runs all of it but writes: it says what would be published, or why not."""
    from tools.pilot_state import closure_cache, publish_model_stage_artifact, recorded_model_answer
    from tools.project_inventory import inventory_cache

    with closure_cache(), inventory_cache():
        _require_open_attempt(run_root, attempt_id)
        pending = None
        answer: dict[str, Any] | None = None
        if verdict is not None:
            known = recorded_model_answer(Path(run_root), attempt_id, REVIEW_STAGE, verdict)
            if known == "other":
                raise ReviewRefused("MODEL_STAGE_ANSWERED", "ответ ревьюера уже записан, другой его не заменит; ничего не записано; "
                                    "fix: review-select без --verdict берёт записанный ответ; другой вердикт — новый прогон (scan --new-run)")
            if known == "none":
                closed = _plain(reviewer_session).get("status") if reviewer_session is not None else _ledger_status(run_root, attempt_id)
                if closed in {"ABORTED", "COMPLETED"}:  # the session is over: a verdict now would be recorded and never used
                    raise ReviewRefused("REVIEW_CLOSED", "сессия ревьюера уже закрыта (прервана или завершена), вердикт в неё не добавить; ничего не записано; "
                                        "fix: ревью закрыто; новое ревью — новый прогон (scan --new-run)")
                if dry_run:
                    dry = publish_model_stage_artifact(Path(run_root), attempt_id, REVIEW_STAGE, verdict, transport_attempts=transport_attempts,
                                                       raw_bytes=verdict_bytes, dry_run=True)
                    pending, answer = dry["artifact"], {"recorded": False, "content_digest": dry["content_digest"], "filled": dry["filled"]}
                else:
                    stored = publish_model_stage_artifact(Path(run_root), attempt_id, REVIEW_STAGE, verdict, transport_attempts=transport_attempts,
                                                          raw_bytes=verdict_bytes)
                    answer = {"recorded": True, "content_digest": stored["content_digest"], "filled": stored.get("filled", [])}
            else:
                answer = {"recorded": True, "content_digest": None, "filled": []}
        session = reviewer_session if reviewer_session is not None else _last_reviewer_session(run_root, attempt_id, pending)
        selected = review_step("select", run_root, attempt_id, files=files, output_dir=output_dir, csv_profile=csv_profile,
                               reviewer_session=session, review=review if review is not None else pending, dry_run=dry_run)
        if not dry_run:
            return selected
        would: dict[str, Any] = {"model_response": answer, "reviewer_ledger": {"status": _plain(session).get("status"), "digest": _plain(session).get("digest")}}
        source = selected.get("effective_source")
        if source is not None:
            would["effective_source"] = source
            would["successor_revision"] = source["revision"] if source["revision"] > 1 else None
        return {**selected, "would_publish": would}


REVIEW_INPUT_FIELDS = ("candidate", "candidate_receipt", "assembly_receipt", "inventory_receipt", "context_receipts",
                       "context_marker_output", "generator_fragments")


def review_inputs(run_root: Path, attempt_id: str, *, output_dir: Path | None = None, csv_profile: str = _PROFILE,
                  given: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """FaT-U5: the seven reviewer-package inputs of the attempt, read from the journal (only reads).

    candidate and assembly receipt — ``assemble_candidate`` over the published plan, header and the tc-generator answers
    (the bytes ``tools.api assemble`` gave); candidate receipt — ``verify_bundle`` of that candidate in ``output_dir``;
    inventory — the attempt baseline's; context receipts — the package's when a session is open, else every receipt the
    attempt published; marker — the context-marker:baseline answer.  A value in ``given`` (a file the agent passed) is
    compared, never trusted: a difference is REVIEW_INPUT_MISMATCH naming the field and its source; a value the journal
    cannot give (a run before FaT-DG) comes from ``given`` as before, else REVIEW_INPUTS_UNAVAILABLE."""
    from tools.batch_assembly import BatchAssemblyError
    from tools.pilot_state import derive_state, read_context_selection, read_model_stage_artifact, read_reviewer_session_ledger, _run_root
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt
    from tools.publish_test_case_bundle import verify_bundle

    given = {key: value for key, value in (given or {}).items() if value not in (None, [])}
    root = Path(run_root)
    project, resolved = _run_root(root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: no attempt {attempt_id} in the run")
    answers = {str(event.get("stage_instance_id")): str(event["artifact_digest"]) for event in state["events"]
               if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"}
    journal: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: dict[str, str] = {}
    try:
        candidate, assembly, fragments = journal_assembly(root, attempt_id)
        journal.update(candidate=candidate, assembly_receipt=assembly, generator_fragments=fragments)
        sources.update(candidate="assemble над plan, header и ответами tc-generator журнала", assembly_receipt="assemble того же",
                       generator_fragments="ответы tc-generator:<batch> журнала по порядку плана")
    except (ValueError, KeyError, BatchAssemblyError, TypeError) as error:
        for key in ("candidate", "assembly_receipt", "generator_fragments"):
            missing[key] = str(error).removeprefix("MODEL_INPUTS_MISMATCH: ").removeprefix("REVIEW_INPUTS_UNAVAILABLE: candidate: ")
    baseline = read_execution_baseline(resolved / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    journal["inventory_receipt"] = read_inventory_receipt(resolved / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    sources["inventory_receipt"] = "опись baseline попытки"
    try:
        digests = list(read_reviewer_session_ledger(root, attempt_id)["package_binding"]["context_receipt_digests"])
        sources["context_receipts"] = "квитанции пакета открытой сессии ревьюера"
    except (KeyError, TypeError, ValueError):
        digests = list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                                     if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"))
        sources["context_receipts"] = "квитанции контекста, опубликованные попыткой"
    journal["context_receipts"] = [read_context_selection(root, attempt_id, digest) for digest in digests]
    if "context-marker:baseline" in answers:
        journal["context_marker_output"] = read_model_stage_artifact(root, attempt_id, "context-marker:baseline", answers["context-marker:baseline"])["artifact"]
        sources["context_marker_output"] = "ответ context-marker:baseline журнала"
    else:
        missing["context_marker_output"] = "нет ответа context-marker:baseline"
    candidate = journal.get("candidate", given.get("candidate"))
    if output_dir is not None and candidate is not None:
        try:
            journal["candidate_receipt"] = verify_bundle(_plain(candidate), output_dir, csv_profile)
            sources["candidate_receipt"] = f"проверка опубликованного bundle в {output_dir}"
        except (OSError, ValueError) as error:
            missing["candidate_receipt"] = f"bundle кандидата в {output_dir} не проверен ({error})"
    else:
        missing["candidate_receipt"] = "нужен --output-dir — каталог, куда review-publish положил кандидата"
    result: dict[str, Any] = {}
    for field in REVIEW_INPUT_FIELDS:
        if field in journal:
            if field in given and _review_key(field, given[field]) != _review_key(field, journal[field]):
                raise ValueError(f"REVIEW_INPUT_MISMATCH: {field}: переданный файл не совпадает с журналом "
                                 f"({sources[field]}); fix: не передавай {field} — код возьмёт его из журнала")
            result[field] = journal[field]
        elif field in given:
            result[field] = given[field]
        else:
            raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: {field}: {missing.get(field, 'нет в журнале')}; fix: сначала этот шаг"
                             " (или передай файл явно, для прогона до FaT-DG)")
    return result


def journal_assembly(run_root: Path, attempt_id: str) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    """FaT-U5: ``(candidate, assembly receipt, fragments)`` — ``assemble_candidate`` over the published plan and header
    and the tc-generator answers of the journal in plan order: the bytes ``tools.api assemble`` gives (only reads)."""
    from tools.batch_assembly import assemble_candidate
    from tools.pilot_state import _generator_input, _run_root, derive_state, read_model_stage_artifact

    project, root = _run_root(Path(run_root))
    state = derive_state(root)
    plan, header = (_generator_input(project, root, state, attempt_id, kind) for kind in ("plan", "header"))
    answers = {str(event.get("stage_instance_id")): str(event["artifact_digest"]) for event in state["events"]
               if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"}
    stages = [f"tc-generator:{batch['batch_id']}" for batch in plan["batches"]]
    absent = [stage for stage in stages if stage not in answers]
    if absent:
        raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: candidate: нет ответа {absent[0]}; fix: сначала model-response этой пачки")
    # P2: the canon is assembled once per process — the plan, the header and the answers are append-only facts of the journal, so
    # one command (publish, package, the decision of the verdict) asks for the same candidate several times and gets one assembly
    from tools.pilot_state import _CLOSURE_CACHE

    kept = _CLOSURE_CACHE.get()
    key = ("journal_assembly", str(root), attempt_id, str(plan["digest"]), str(header["digest"]), tuple(answers[stage] for stage in stages))
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    fragments = [read_model_stage_artifact(root, attempt_id, stage, answers[stage])["artifact"] for stage in stages]
    candidate, assembly = assemble_candidate(_plain(header), _plain(plan), [_plain(item) for item in fragments])
    if kept is not None:
        kept[key] = copy.deepcopy((candidate, assembly, fragments))
    return candidate, assembly, fragments


def _review_key(field: str, value: Any) -> bytes:
    if field == "context_receipts":  # a context file may be the receipt or its digest pointer: the digests decide
        value = [_plain(item).get("digest") if isinstance(_plain(item), dict) else None for item in value]
    return _review_bytes(value)


def _review_bytes(value: Any) -> bytes:
    if isinstance(value, Receipt):
        value = _receipt_json(value)
    elif isinstance(value, (list, tuple)):
        value = [_plain(item) for item in value]
    return canonical_bytes(_plain(value))


def _load_receipt(path: Path) -> Receipt:
    value = load_json_strict(path)
    fields = tuple(Receipt.__dataclass_fields__)
    if not isinstance(value, Mapping) or set(value) != set(fields):
        missing = sorted(set(fields) - set(value)) if isinstance(value, Mapping) else list(fields)
        extra = sorted(set(value) - set(fields)) if isinstance(value, Mapping) else []
        raise ValueError(f"ORCHESTRATION_RECEIPT_SHAPE: candidate receipt has an invalid shape (missing {missing}, extra {extra})")
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


_FIXES = {code: FIXES[code] for code in ("REVIEW_CONTEXT_LIMIT", "ORCHESTRATION_RECEIPT_SHAPE", "REVIEWER_PROTOCOL")}  # FaT-10d; P1: the texts live in tools.fixes


# FaT-U5: the old command names stay until the release to testers; tools.api is the one CLI of the review layer
_RENAMED = {name: {"code": "COMMAND_RENAMED", "message": f"tools.api {new}"}
            for name, new in (("publish", "review-publish"), ("open-session", "review-open"), ("select", "review-select"))}


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="Publish and read back revision 1 as UNREVIEWED.")
    publish.add_argument("--candidate", required=True, type=Path)
    publish.add_argument("--output-dir", required=True, type=Path)
    publish.add_argument("--run-root", required=True, type=Path)
    publish.add_argument("--attempt-id", required=True)
    publish.add_argument("--csv-profile", default=_PROFILE)
    publish.add_argument("--out", type=Path, default=None, help="FaT-AU2: write the candidate receipt (--candidate-receipt) to <run>/tmp/<file>")
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
        print(json.dumps({"candidate_bundle_receipt": None, "code": "ORCHESTRATION_INPUT", "diagnostics": [_diag("/csv_profile", "ORCHESTRATION_PROFILE")], "status": "error", "successor_bundle_receipt": None,
                          "message": f"unknown csv profile {args.csv_profile}", "fix": f"one of {sorted(_PROFILES)}", "next": None}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    try:
        candidate = load_json_strict(args.candidate)
        if args.command == "publish":
            from tools.api import _out_target, _write  # FaT-AU2: the one --out policy and writer of the package

            out = _out_target(str(args.out), args.run_root) if args.out is not None else None  # refused before anything is published
            output_guard.run_output_directory(args.output_dir, args.run_root)
            published = publish_command(candidate, args.output_dir, args.csv_profile, run_root=args.run_root, attempt_id=args.attempt_id)
            published["warnings"] = [*published["warnings"], _RENAMED["publish"]]
            if out is not None:
                published["out"] = _write(out, published["candidate_bundle_receipt"])
            print(json.dumps(published, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "select":
            output_guard.run_output_directory(args.output_dir, args.run_root)
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
            print(json.dumps({"package_binding": package, "reviewer_session_boundary": boundary, "status": "opened",
                              "warnings": [_RENAMED["open-session"]]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
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
        print(json.dumps({**error_envelope(error), "candidate_bundle_receipt": _receipt_json(error.candidate_bundle_receipt),
                          "diagnostics": [dict(row) for row in error.diagnostics], "successor_bundle_receipt": _receipt_json(error.successor_bundle_receipt)},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return exit_code
    except output_guard.OutputRefused as error:  # --out / --output-dir outside the run or the project: its own code and fix
        print(json.dumps({**error_envelope(error), "candidate_bundle_receipt": None, "diagnostics": [_diag("", "ORCHESTRATION_IO")],
                          "successor_bundle_receipt": None, "reason_code": error.code}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    except (StrictJsonError, OSError, ValueError) as error:
        # FaT-10d: the inner refusal code and its fix; FaT-U5: the one envelope, the code is the refusal's own
        envelope = error_envelope(error)  # FaT-U6: the same reading as tools.api
        reason_code = error_code_prefix(envelope["message"])
        print(json.dumps({**envelope, "code": reason_code or "ORCHESTRATION_INPUT", "fix": _FIXES.get(reason_code) or envelope["fix"],
                          "candidate_bundle_receipt": None, "diagnostics": [_diag("", "ORCHESTRATION_IO")], "successor_bundle_receipt": None,
                          "reason_code": reason_code}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))); return 2
    output = {**select_output(result), "warnings": [_RENAMED["select"]]}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
