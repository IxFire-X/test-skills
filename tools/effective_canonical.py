"""The effective canonical set of cases (v0.8, the cut of the core): its publication and reading, and the check of its kind
(``RECEIPT_CHECKS``: ``pilot_state._validate_factual_receipt`` takes it from here).

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v2a.tsv``);
what they read of the journal and of ``model_records`` they take by public names, and ``pilot_state`` still serves their names to its
callers (the facade of the cut).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.confined_output import OutputConfinementError, read_confined_bytes
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    DIGEST as _DIGEST,
    PACK_ROOT as _PACK_ROOT,
    ReceiptFileUnbound,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    canonical_bytes as _canonical_bytes,
    closure_cached,
    derive_state,
    publish_bound,
    read_attempt_receipt_with_state as _read_attempt_receipt_with_state,
    read_reviewer_session_ledger_with_state as _read_reviewer_session_ledger_with_state,
    receipt_target as _receipt_target,
    run_root_of as _run_root,
    self_review_accepted_at as _self_review_accepted_at,
    terminal_reviewer_evidence_with_state as _terminal_reviewer_evidence_with_state,
)
from tools.model_records import (  # the names of the records of a step these bodies read (П1 of the cut of v0.8)
    read_model_stage_artifact_with_state as _read_model_stage_artifact_with_state,
)


def _check_effective_canonical(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    expected_keys = common | {"document", "document_digest", "effective_bundle_receipt", "effective_bundle_receipt_digest", "reviewer_session_digest"}
    document = receipt.get("document")
    bundle = receipt.get("effective_bundle_receipt")
    ledger = None
    terminal_reviewer = None
    try:
        from tools.canonical_document import document_sha256, validate_canonical_document
        from tools.revision_selection import effective_review_is_bound
        from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

        reviewer_digest = str(receipt.get("reviewer_session_digest", ""))
        ledger_target = root / "reviewer-session-ledgers" / str(attempt["attempt_id"]) / (reviewer_digest.removeprefix("sha256:") + ".json")
        ledger_bytes = read_confined_bytes(project, root, ledger_target)
        ledger = loads_json_strict(ledger_bytes.decode("utf-8")) if ledger_bytes is not None else None
        terminal_reviewer = _terminal_reviewer_evidence_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        reviewer_boundary = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]), "reviewer-session-boundary",
            "ARTIFACT_READ_BACK",
        )["record"]
        package_binding = ledger.get("package_binding") if isinstance(ledger, Mapping) else None
        reviewer_responses = [
            event for event in state["events"]
            if event.get("attempt_id") == attempt["attempt_id"]
            and event.get("stage_instance_id") == "tc-reviewer:canonical"
            and event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
        ]
        reviewer_publication = _read_model_stage_artifact_with_state(
            project, root, state, str(attempt["attempt_id"]), "tc-reviewer:canonical",
            str(reviewer_responses[0].get("artifact_digest", "")),
        ) if len(reviewer_responses) == 1 else None
        reviewer_output = reviewer_publication.get("artifact") if isinstance(reviewer_publication, Mapping) else None
        report = reviewer_output.get("artifacts", {}).get("validation_report") if isinstance(reviewer_output, Mapping) else None
        successor = reviewer_output.get("artifacts", {}).get("successor_document") if isinstance(reviewer_output, Mapping) else None
        edits = reviewer_output.get("artifacts", {}).get("successor_edits") if isinstance(reviewer_output, Mapping) else None
        if edits is not None:
            # the successor is the candidate with the reviewer's exact edits; undo them to reach the candidate digest.
            # the edits of a canary text were dropped before the successor was made — they are dropped here too.
            from tools.review_canaries import filter_edits, verify as verify_canaries
            from tools.review_edits import edits_rebuild

            edits = filter_edits(verify_canaries(root, str(attempt["attempt_id"])) or [], {"successor_edits": edits})[0].get("successor_edits")

            successor = document if (
                edits is not None and isinstance(document, Mapping) and isinstance(package_binding, Mapping)
                and edits_rebuild(document, edits, str(package_binding.get("candidate_digest")))
            ) else None
        verdict_event = next(
            (event for event in ledger.get("events", []) if isinstance(event, Mapping) and event.get("event_type") == "AUTHORITATIVE_VERDICT"),
            None,
        ) if isinstance(ledger, Mapping) else None
        partial: dict[str, Any] = {}
        if isinstance(report, Mapping) and report.get("verdict") == "ТРЕБУЕТ ДОРАБОТКИ":  # sent back and subtracted: bound to the candidate and the canaries of the journal
            from tools.orchestrate_test_case_revision import journal_assembly
            from tools.review_canaries import verify as verify_canaries

            from tools.orchestrate_test_case_revision import rework_artifacts

            partial = {"candidate": journal_assembly(root, str(attempt["attempt_id"]))[0], "canaries": verify_canaries(root, str(attempt["attempt_id"])) or [],
                       "rework": rework_artifacts(root, str(attempt["attempt_id"]))}  # the answer of the rework and the review of it, when the journal has both
        review_bound = (
            isinstance(package_binding, Mapping)
            and effective_review_is_bound(
                package_binding.get("candidate_digest"),
                dict(document) if isinstance(document, Mapping) else document,
                dict(report) if isinstance(report, Mapping) else report,
                dict(successor) if isinstance(successor, Mapping) else successor,
                dict(verdict_event) if isinstance(verdict_event, Mapping) else verdict_event,
                **partial,
            )
        )
        ledger_events = [event for event in state["events"] if event.get("attempt_id") == attempt["attempt_id"] and event.get("batch_id") == "reviewer-ledger-v1" and event.get("artifact_digest") == reviewer_digest]
        ledger_body = {key: value for key, value in ledger.items() if key != "digest"} if isinstance(ledger, Mapping) else {}
        canonical_ledger_bytes = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") if isinstance(ledger, Mapping) else None
        canonical_ledger_digest = "sha256:" + hashlib.sha256(json.dumps(ledger_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        boundary_events = [
            event for event in state["events"]
            if event.get("attempt_id") == attempt["attempt_id"]
            and event.get("artifact_digest") == reviewer_boundary.get("digest")
            and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        ]
        ledger_bound = (
            isinstance(ledger, Mapping)
            and isinstance(reviewer_boundary, Mapping)
            and isinstance(package_binding, Mapping)
            and ledger.get("digest") == reviewer_digest
            and ledger.get("digest") == canonical_ledger_digest
            and ledger_bytes == canonical_ledger_bytes
            and not schema_diagnostics(dict(ledger), _SCHEMA_ROOT / "reviewer-session.schema.json", _PACK_ROOT)
            and [event["event_type"] for event in ledger_events] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
            and [event["event_type"] for event in boundary_events] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
            and ledger.get("boundary_digest") == reviewer_boundary.get("digest")
            and ledger.get("session_id") == reviewer_boundary.get("session_id")
            and ledger.get("generator_role") == reviewer_boundary.get("generator_role")
            and ledger.get("reviewer_role") == reviewer_boundary.get("reviewer_role")
            and ledger.get("generator_invocation_id") == reviewer_boundary.get("generator_invocation_id")
            and ledger.get("reviewer_invocation_id") == reviewer_boundary.get("reviewer_invocation_id")
            and ledger.get("host_isolation") == reviewer_boundary.get("host_isolation")
            and ledger.get("context_budget_bytes") == reviewer_boundary.get("context_budget_bytes")
            and package_binding.get("candidate_digest") == reviewer_boundary.get("canonical_branch_digest")
            and package_binding.get("package_digest") == reviewer_boundary.get("package_digest")
            and ledger.get("status") == "COMPLETED"
            and terminal_reviewer.get("digest") == reviewer_digest
            and terminal_reviewer.get("authoritative_verdict") == "ACCEPTED"
            and isinstance(ledger.get("host_isolation"), Mapping)
            and (
                ledger["host_isolation"].get("fresh_context") is True
                and ledger["host_isolation"].get("distinct_invocations") is True
                or _self_review_accepted_at(project, root, state)
            )
            and ledger["host_isolation"].get("role_policy") == "canonical-reviewer-v1"
            and isinstance(ledger.get("events"), list)
            and sum(event.get("event_type") == "AUTHORITATIVE_VERDICT" for event in ledger["events"] if isinstance(event, Mapping)) == 1
            and next(event for event in ledger["events"] if isinstance(event, Mapping) and event.get("event_type") == "AUTHORITATIVE_VERDICT").get("verdict") == "ACCEPTED"
            and bool(ledger["events"])
            and isinstance(ledger["events"][-1], Mapping)
            and ledger["events"][-1].get("event_type") == "REVIEW_SESSION_COMPLETED"
        )
    except (ImportError, UnicodeDecodeError, StrictJsonError, ValueError, OutputConfinementError, KeyError, TypeError, AttributeError):
        ledger_bound = False
    normalized_bundle_keys = ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")
    normalized_bundle = {key: bundle.get(key) for key in normalized_bundle_keys} if isinstance(bundle, Mapping) else {}
    bundle_digest = "sha256:" + hashlib.sha256(_canonical_bytes(normalized_bundle)).hexdigest()
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or not isinstance(document, Mapping)
        or bool(validate_canonical_document(dict(document)))
        or receipt.get("document_digest") != document_sha256(dict(document))
        or not isinstance(bundle, Mapping)
        or set(bundle) != set(normalized_bundle_keys)
        or bundle.get("document_id") != document.get("document_id")
        or bundle.get("revision") != document.get("revision")
        or bundle.get("document_sha256") != receipt.get("document_digest")
        or bundle.get("csv_profile") not in {"zephyr-scale-step-row-24-v5", "zephyr-scale-step-row-24-v4"}
        or any(not isinstance(bundle.get(key), str) or not bundle.get(key) for key in ("json_path", "preview_path", "csv_path"))
        or any(not _DIGEST.fullmatch(str(bundle.get(key, ""))) for key in ("document_sha256", "preview_sha256", "csv_sha256"))
        or receipt.get("effective_bundle_receipt_digest") != bundle_digest
        or not _DIGEST.fullmatch(str(receipt.get("reviewer_session_digest", "")))
        or not ledger_bound
        or not review_bound
    ):
        raise ValueError("invalid effective canonical receipt")


def publish_effective_canonical(
    run_root: Path, attempt_id: str, document: Mapping[str, Any], effective_bundle_receipt: Mapping[str, Any], reviewer_session_digest: str,
) -> Mapping[str, Any]:
    """Freeze the sole Phase-4 effective selection before any V5 input exists."""
    bundle_fields = ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")
    bundle = {key: effective_bundle_receipt.get(key) for key in bundle_fields}
    try:
        from tools.canonical_document import document_sha256

        value = {
            "document": dict(document), "document_digest": document_sha256(dict(document)),
            "effective_bundle_receipt": bundle,
            "effective_bundle_receipt_digest": "sha256:" + hashlib.sha256(_canonical_bytes(bundle)).hexdigest(),
            "reviewer_session_digest": reviewer_session_digest,
        }
        published = publish_bound(run_root, attempt_id, "effective-canonical", value)
    except (AttributeError, ValueError) as error:
        if isinstance(error, ReceiptFileUnbound):
            raise
        raise ValueError("invalid effective canonical selection") from error
    if any(published["record"].get(key) != value.get(key) for key in value):
        raise ValueError("conflicting effective canonical selection")
    return {key: item for key, item in published.items() if key != "installed_identity"}


def _read_effective_canonical_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
) -> Mapping[str, Any]:
    readback = _read_attempt_receipt_with_state(
        project, root, state, attempt_id,
        "effective-canonical", "ARTIFACT_READ_BACK",
    )
    events = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == readback["digest"]
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if [event["event_type"] for event in events] != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        raise ValueError("invalid effective canonical event binding")
    return readback["record"]


@closure_cached
def read_effective_canonical(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Return only the fixed attempt-owned effective selection after read-back."""
    project, root = _run_root(run_root)
    return _read_effective_canonical_with_state(
        project, root, derive_state(root), attempt_id,
    )


def read_effective_canonical_if_present(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Return the fixed selection only when its durable slot exists."""
    project, root = _run_root(run_root)
    target = _receipt_target(root, attempt_id, "effective-canonical")
    try:
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError("unsafe effective-canonical path") from error
    if raw is None:
        return None
    return read_effective_canonical(root, attempt_id)


def effective_canonical_or_reason(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """PT2: what ``status`` and the report read — the effective selection, or ``{"effective": None, "reason": …}`` while the journal says there is none.

    "None yet" is decided by the journal, never by a missing file: a file that is there is read as before (validated against its
    events); a file that is gone or replaced where the journal recorded an accepting verdict is the refusal it always was.
    Without a recorded verdict of the review: not yet.  With one that does not accept the candidate (REJECTED): never."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    if next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None) is None:
        return _read_effective_canonical_with_state(project, root, state, attempt_id)  # unknown attempt: its refusal
    if _receipt_target(root, attempt_id, "effective-canonical").exists():
        return _read_effective_canonical_with_state(project, root, state, attempt_id)
    recorded = any(event.get("event_type") == "MODEL_RESPONSE_RECEIVED" and event.get("attempt_id") == attempt_id
                   and event.get("stage_instance_id") == "tc-reviewer:canonical" for event in state["events"])
    if not recorded:
        return {"effective": None, "reason": "ещё нет: вердикт ревью не записан (`submit --stage tc-reviewer:canonical`)"}
    not_yet = {"effective": None, "reason": "ещё нет: ответ ревьюера записан, ревизия не выбрана — повтори `submit --stage tc-reviewer:canonical`"}
    # the order of the writes of the selection (``submit`` of the review) decides what «not yet» is: the ledger revision COMPLETED is published BEFORE the effective
    # canonical (orchestrate_revision: validate_reviewer_session, then publish_effective_canonical).  So a latest revision still waiting for its
    # READ_BACK, a ledger still WAITING (no verdict), and a closed accepting ledger with no attempt-level receipt read back after it all mean the
    # effective canonical was not published yet — a repeat of ``submit`` does it.  Only a published selection whose file is gone is a refusal.
    try:
        ledger = _read_reviewer_session_ledger_with_state(project, root, state, attempt_id, None)
        confirmed = True
    except ValueError:
        ledger, confirmed = _read_reviewer_session_ledger_with_state(project, root, state, attempt_id, None, unconfirmed_ok=True), False  # unreadable too: the refusal
    verdict = next((event.get("verdict") for event in ledger["events"] if isinstance(event, Mapping) and event.get("event_type") == "AUTHORITATIVE_VERDICT"), None)
    if verdict == "REJECTED":
        return {"effective": None, "reason": "effective нет: вердикт ТРЕБУЕТ ДОРАБОТКИ (REJECTED) не принимает кандидата"}
    if verdict is None or not confirmed:
        return not_yet
    closed = next((event["seq"] for event in state["events"] if event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id
                   and event.get("batch_id") == "reviewer-ledger-v1" and event.get("artifact_digest") == ledger["digest"]), None)
    selected = any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") is None
                   and closed is not None and event["seq"] > closed for event in state["events"])
    if not selected:
        return not_yet
    return _read_effective_canonical_with_state(project, root, state, attempt_id)  # a published selection whose file is gone: the refusal

# a kind -> its check (``pilot_state._validate_factual_receipt`` takes it from here)
RECEIPT_CHECKS = {"effective-canonical": _check_effective_canonical}


# public names of the same objects: what the modules above and ``pilot_state``'s callers take from here (П1 of the cut
# of v0.8)
read_effective_canonical_with_state = _read_effective_canonical_with_state
