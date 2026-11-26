"""Durable, append-only Phase 1 controller state with safe local receipts."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import types
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from tools import execution_gate
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, create_confined_bytes_exclusive, create_confined_directory_exclusive, ensure_project_child_directory, read_confined_bytes, remove_confined_bytes_if_equal
from tools.run_store import (  # the files of a run, below the journal (П1 of the cut of v0.8): what the code here reads and serves
    CLOSURE_CACHE as _CLOSURE_CACHE,
    DIGEST as _DIGEST,
    InputRefusal as _InputRefusal,
    PACK_ROOT as _PACK_ROOT,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    UNSAFE_RUN_ROOT as _UNSAFE_RUN_ROOT,
    canonical_bytes as _canonical_bytes,
    canonical_digest as _digest,
    check_sealed_value as _check_sealed,
    credential_like as _credential_like,
    exit_code,
    is_reparse as _is_reparse,
    moved_project_allowed,
    moved_recorded as _moved_recorded,
    parse_json_bytes as _parse_json_bytes,
    project_root as _project_root,
    project_state_for as _project_state_for,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    read_run,
    run_boundary as _run_boundary,
    run_project_move,
    run_root_is_unsafe,
    run_root_of as _run_root,
    safe_label as _safe_label,
    safe_model_label as _safe_model_label,
    sealed as _sealed,
    terminal_result,
    validate_authorization as _validate_authorization,
    validate_result_record as _validate_result_record,
    validate_schema as _validate_schema,
)
from tools.run_journal import (  # the journal of events, right above the files of a run (П1 of the cut of v0.8): what the code here reads and serves
    CLOSURE_KINDS as _CLOSURE_KINDS,
    MODEL_STAGE_INSTANCE as _MODEL_STAGE_INSTANCE,
    REWORK_BATCH,
    REWORK_REVIEW_STAGE as _REWORK_REVIEW_STAGE,
    REWORK_STAGE as _REWORK_STAGE,
    append_event,
    append_journal_event as _append_event,
    check_phase1_artifact as _check_phase1_artifact,
    closure_cache,
    closure_cached,
    closure_target as _closure_target,
    derive_state,
    frozen_baseline,
    frozen_inventory as _frozen_inventory,
    model_lifecycle_projection_with_state as _model_lifecycle_projection_with_state,
    read_closure_artifact,
    validate_execution_event_branch as _validate_execution_event_branch,
)


_MODEL_STAGE_REGISTRY = {
    "context-marker": ("generator", "context-marker-v1"),
    "tc-generator": ("generator", "tc-generator-v1"),
    "tc-reviewer": ("canonical-reviewer", "canonical-reviewer-v1"),
    "tc-to-autotest": ("automation-generator", "tc-to-autotest-v1"),
    "autotest-reviewer": ("automation-reviewer", "autotest-static-reviewer-v1"),
}
_DEDICATED_RECEIPT_TOKEN = object()


SELF_MARK = "self-review-accepted.json"  # the human's consent to a review in the same session, given at the step of the review
SELF_BATCH = "self-review-v1"  # the events of the mark carry it


def _self_mark_bytes(attempt_id: str) -> bytes:
    return json.dumps({"attempt_id": attempt_id, "reason": "SELF_REVIEW_ACCEPTED"}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _self_consent_given(project: Path, root: Path, state: Mapping[str, Any] | None = None) -> bool:
    """The consent counts only with its ARTIFACT_READ_BACK in the journal (``bind_bytes``, batch ``self-review-v1``) and the bytes the code writes —
    a file an agent put there is no consent."""
    try:
        data = read_confined_bytes(project, root, root / SELF_MARK)
        value = json.loads(data) if data is not None else None
    except (OutputConfinementError, ValueError):
        return False
    if not isinstance(value, Mapping) or not isinstance(value.get("attempt_id"), str) or data != _self_mark_bytes(value["attempt_id"]):
        return False
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == value["attempt_id"] and event.get("batch_id") == SELF_BATCH
               and event.get("artifact_digest") == digest for event in (state if state is not None else derive_state(root))["events"])


def _self_review_accepted_at(project: Path, root: Path, state: Mapping[str, Any] | None = None) -> bool:
    return _run_boundary(project, root)[1].get("accept_self_review") is True or _self_consent_given(project, root, state)


def grant_self_review(run_root: Path, attempt_id: str) -> bool:
    """The human's consent to a review in the same session, given at the step of the review: one mark of the run, written before the review it belongs to
    (``bind_bytes``: the bytes are the code's, a repeat writes nothing, a cut one is finished).  ``False`` where the run admitted SELF already."""
    project, root = _run_root(run_root)
    if _self_review_accepted_at(project, root):
        return False
    data = _self_mark_bytes(attempt_id)
    bind_bytes(root, attempt_id, root / SELF_MARK, data, digest="sha256:" + hashlib.sha256(data).hexdigest(), batch_id=SELF_BATCH, label="self-review mark")
    return True


def _disposition_policy_at(project: Path, root: Path) -> str:
    return "cleanup" if _run_boundary(project, root)[1].get("disposition_policy") == "cleanup" else "quarantine"


def disposition_policy(run_root: Path) -> str:
    """What a FAIL does to generated files — ``quarantine`` (default) or ``cleanup`` (`scan --disposition-policy cleanup`)."""
    project, root = _run_root(run_root)
    return _disposition_policy_at(project, root)


def self_review_accepted(run_root: Path) -> bool:
    """The run admits a review without a fresh context (`scan --accept-self-review`, or the consent given at the step of the review)."""
    project, root = _run_root(run_root)
    return _self_review_accepted_at(project, root)


# These validations look at the project tree (module directory, runtime paths), not only at the run directory —
# directly (phase1-artifact, execution-receipt) or through a nested read of execution-receipt (resume-validation,
# execution-trace, trace-audit).  Nothing else reads one of these five, so the set is closed.
_UNCACHED_RECEIPT_KINDS = frozenset({"phase1-artifact", "execution-receipt", "resume-validation", "execution-trace", "trace-audit"})


LEFT_MARK = "left-before-review.json"  # finish --abandon LEFT_BEFORE_REVIEW


class ReviewLedgerUnbound(ValueError):
    """A reviewer ledger file lies without its events and is not the revision being published."""

    code = "REVIEW_LEDGER_UNBOUND"


def publish_reviewer_session_ledger(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> Mapping[str, Any]:
    """Publish/read back one append-only reviewer-ledger revision under its attempt."""
    from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    value = dict(session)
    if attempt is not None and attempt["state"] == "TERMINAL":  # not a bare ValueError — the attempt is closed
        raise ValueError(f"ATTEMPT_TERMINAL: the attempt {attempt_id} is closed; a reviewer session ledger needs an open attempt")
    if attempt is None or schema_diagnostics(value, _SCHEMA_ROOT / "reviewer-session.schema.json", _PACK_ROOT):
        raise ValueError("invalid reviewer session ledger")
    digest = value.get("digest")
    session_body = {key: item for key, item in value.items() if key != "digest"}
    session_digest = "sha256:" + hashlib.sha256(json.dumps(session_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if not _DIGEST.fullmatch(digest or "") or digest != session_digest:
        raise ValueError("invalid reviewer session ledger")
    ledger_root = root / "reviewer-session-ledgers" / attempt_id
    target = ledger_root / (digest.removeprefix("sha256:") + ".json")
    history: list[tuple[int, dict[str, Any]]] = []
    events = state["events"]
    recorded_published = [event for event in events if event["event_type"] == "ARTIFACT_PUBLISHED" and event.get("attempt_id") == attempt_id and event.get("batch_id") == "reviewer-ledger-v1"]
    recorded_read_back = [event for event in events if event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") == "reviewer-ledger-v1"]
    recorded_publish_digests = [event.get("artifact_digest") for event in recorded_published]
    recorded_readback_digests = [event.get("artifact_digest") for event in recorded_read_back]
    pending: set[str] = set()  # the revision this call publishes, found on disk with its events cut short — finished below
    if len(recorded_publish_digests) != len(set(recorded_publish_digests)) or len(recorded_readback_digests) != len(set(recorded_readback_digests)):
        raise ValueError("invalid reviewer session ledger history")
    if ledger_root.exists():
        if not ledger_root.is_dir() or _is_reparse(ledger_root):
            raise ValueError("invalid reviewer session ledger history")
        for path in sorted(ledger_root.glob("*.json")):
            if _is_reparse(path):
                raise ValueError("invalid reviewer session ledger history")
            try:
                raw_history = read_confined_bytes(project, root, path)
                prior = loads_json_strict(raw_history.decode("utf-8")) if raw_history is not None else None
            except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
                raise ValueError("invalid reviewer session ledger history") from error
            if (
                not isinstance(prior, dict)
                or schema_diagnostics(prior, _SCHEMA_ROOT / "reviewer-session.schema.json", _PACK_ROOT)
                or path.stem != str(prior.get("digest", "")).removeprefix("sha256:")
            ):
                raise ValueError("invalid reviewer session ledger history")
            prior_body = {key: item for key, item in prior.items() if key != "digest"}
            if prior.get("digest") != "sha256:" + hashlib.sha256(json.dumps(prior_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest():
                raise ValueError("invalid reviewer session ledger history")
            published = [event for event in recorded_published if event.get("artifact_digest") == prior["digest"]]
            read_back = [event for event in recorded_read_back if event.get("artifact_digest") == prior["digest"]]
            if len(published) <= 1 and not read_back and prior["digest"] != digest:
                raise ReviewLedgerUnbound(  # a file of another revision without its events — not ours to finish
                    f"REVIEW_LEDGER_UNBOUND: файл леджера {path.name} лежит без событий журнала и это не та ревизия, что публикуется сейчас; "
                    "ничего не записано; fix: повтори ту же команду с теми же входами (ответом ревьюера, --path) — она допишет события файла; "
                    "иные входы — новое ревью: новый прогон (scan --new-run)")
            if prior["digest"] == digest and not read_back and len(published) <= 1:
                pending.add(digest)  # file there, events not (or only the first): same bytes — this call writes what is missing
                continue
            if len(published) != 1 or len(read_back) != 1 or read_back[0]["seq"] <= published[0]["seq"]:
                raise ValueError("invalid reviewer session ledger history")
            history.append((published[0]["seq"], prior))
        history.sort(key=lambda item: item[0])
        history_digests = [item[1]["digest"] for item in history]
        if (len({item[0] for item in history}) != len(history) or set(history_digests) != set(recorded_publish_digests) - pending
                or set(history_digests) != set(recorded_readback_digests)):
            raise ValueError("invalid reviewer session ledger history")
    elif recorded_published or recorded_read_back:
        raise ValueError("invalid reviewer session ledger history")
    if history:
        previous = history[-1][1]
        if previous.get("digest") == digest:
            return {"record": previous, "digest": digest, "created": False, "path": str(target.relative_to(root)).replace("\\", "/")}
        if previous.get("status") in {"COMPLETED", "ABORTED"}:
            raise ValueError("reviewer session ledger is already terminal")
        immutable = set(value) - {"events", "status", "digest"}
        prior_events, next_events = previous.get("events"), value.get("events")
        if (
            any(previous.get(key) != value.get(key) for key in immutable)
            or not isinstance(prior_events, list)
            or not isinstance(next_events, list)
            or len(next_events) <= len(prior_events)
            or next_events[: len(prior_events)] != prior_events
        ):
            raise ValueError("reviewer session ledger must prefix-extend its latest revision")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:  # the file and its pair of events — the one contract of bind_bytes (the file is only compared with the bytes computed here)
        bound = bind_bytes(run_root, attempt_id, target, data, digest=digest, batch_id="reviewer-ledger-v1", label="reviewer session ledger")
    except ReceiptFileUnbound as error:
        raise ReviewLedgerUnbound(
            f"REVIEW_LEDGER_UNBOUND: файл леджера {target.name} лежит без событий журнала и это не та ревизия, что публикуется сейчас; "
            "ничего не записано; fix: повтори ту же команду с теми же входами (ответом ревьюера, --path) — она допишет события файла; "
            "иные входы — новое ревью: новый прогон (scan --new-run)") from error
    except (OSError, ValueError) as error:
        raise ValueError("reviewer session ledger readback failed") from error
    created = bound["created"]
    readback = loads_json_strict(data.decode("utf-8"))
    return {"record": readback, "digest": digest, "created": created, "path": str(target.relative_to(root)).replace("\\", "/")}


def _read_reviewer_session_ledger_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    digest: str | None = None,
    *,
    unconfirmed_ok: bool = False,
) -> Mapping[str, Any]:
    from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    published = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("batch_id") == "reviewer-ledger-v1"
        and event.get("event_type") == "ARTIFACT_PUBLISHED"
    ]
    readbacks = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("batch_id") == "reviewer-ledger-v1"
        and event.get("event_type") == "ARTIFACT_READ_BACK"
    ]
    selected = digest if digest is not None else (published[-1].get("artifact_digest") if published else None)
    if attempt is None or not isinstance(selected, str) or not _DIGEST.fullmatch(selected):
        raise ValueError("reviewer session ledger is unavailable")
    confirmed = len([event for event in readbacks if event.get("artifact_digest") == selected])
    # the latest revision is the last one PUBLISHED; the commands that finish a cut publication (``unconfirmed_ok``) read it
    # while its READ_BACK is still to come — the file is content-addressed and checked against that digest below, and an older
    # revision is never read in its place.  Evidence consumers keep the strict reading (both events).
    if (len([event for event in published if event.get("artifact_digest") == selected]) != 1
            or confirmed > 1 or (confirmed == 0 and not (unconfirmed_ok and digest is None))):
        raise ValueError("reviewer session ledger is unbound")
    target = root / "reviewer-session-ledgers" / attempt_id / f"{selected.removeprefix('sha256:')}.json"
    try:
        raw = read_confined_bytes(project, root, target)
        value = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("reviewer session ledger is unreadable") from error
    body = {key: item for key, item in value.items() if key != "digest"} if isinstance(value, Mapping) else {}
    canonical_digest = "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if not isinstance(value, Mapping) or value.get("digest") != selected or canonical_digest != selected or schema_diagnostics(dict(value), _SCHEMA_ROOT / "reviewer-session.schema.json", _PACK_ROOT):
        raise ValueError("reviewer session ledger is invalid")
    boundary = _read_attempt_receipt_with_state(
        project, root, state, attempt_id,
        "reviewer-session-boundary", "ARTIFACT_READ_BACK",
    )["record"]
    if (
        value.get("boundary_digest") != boundary.get("digest")
        or value.get("session_id") != boundary.get("session_id")
        or value.get("generator_invocation_id") != boundary.get("generator_invocation_id")
        or value.get("reviewer_invocation_id") != boundary.get("reviewer_invocation_id")
        or value.get("host_isolation") != boundary.get("host_isolation")
    ):
        raise ValueError("reviewer session ledger boundary is invalid")
    return dict(value)


def read_reviewer_session_ledger(
    run_root: Path,
    attempt_id: str,
    digest: str | None = None,
    *,
    unconfirmed_ok: bool = False,
) -> Mapping[str, Any]:
    """Read the exact latest or named reviewer ledger only after paired events (``unconfirmed_ok``: the latest one may still wait
    for its READ_BACK — for the commands of the review, which finish what a cut left; see the reader below)."""
    project, root = _run_root(run_root)
    return _read_reviewer_session_ledger_with_state(
        project, root, derive_state(root), attempt_id, digest, unconfirmed_ok=unconfirmed_ok,
    )


def reviewer_lifecycle_projection(session: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate reviewer event transitions and project their terminal facts."""
    events = session.get("events")
    if (
        not isinstance(events, list)
        or not events
        or any(not isinstance(event, Mapping) for event in events)
        or [event.get("ordinal") for event in events] != list(range(1, len(events) + 1))
        or events[0].get("event_type") != "REVIEW_SESSION_STARTED"
    ):
        raise ValueError("reviewer lifecycle is invalid")
    pending_request: str | None = None
    verdict_seen = False
    terminal: str | None = None
    for index, event in enumerate(events[1:], start=1):
        kind = event.get("event_type")
        terminal_position = index == len(events) - 1
        if terminal is not None:
            raise ValueError("reviewer lifecycle is invalid")
        if kind == "EVIDENCE_REQUESTED":
            if pending_request is not None or verdict_seen or not isinstance(event.get("request_digest"), str):
                raise ValueError("reviewer lifecycle is invalid")
            pending_request = str(event.get("request_digest"))
        elif kind == "EVIDENCE_PROVIDED":
            if pending_request is None or event.get("request_digest") != pending_request or verdict_seen:
                raise ValueError("reviewer lifecycle is invalid")
            pending_request = None
        elif kind == "AUTHORITATIVE_VERDICT":
            if pending_request is not None or verdict_seen:
                raise ValueError("reviewer lifecycle is invalid")
            verdict_seen = True
        elif kind == "REVIEW_SESSION_COMPLETED":
            if not terminal_position or pending_request is not None or not verdict_seen:
                raise ValueError("reviewer lifecycle is invalid")
            terminal = kind
        elif kind == "REVIEW_SESSION_ABORTED":
            if (
                not terminal_position
                or verdict_seen
                or pending_request is not None
                and event.get("reason_code") != "REVIEW_CONTEXT_LIMIT"
            ):
                raise ValueError("reviewer lifecycle is invalid")
            terminal = kind
        else:
            raise ValueError("reviewer lifecycle is invalid")
    verdicts = [event for event in events if event.get("event_type") == "AUTHORITATIVE_VERDICT"]
    aborts = [event for event in events if event.get("event_type") == "REVIEW_SESSION_ABORTED"]
    status = session.get("status")
    completed = status == "COMPLETED"
    aborted = status == "ABORTED"
    waiting = status == "WAITING"
    valid = (
        completed and len(verdicts) == 1 and not aborts and terminal == "REVIEW_SESSION_COMPLETED"
        and verdicts[0].get("verdict") in {"ACCEPTED", "REJECTED"}
    ) or (
        aborted and not verdicts and len(aborts) == 1 and terminal == "REVIEW_SESSION_ABORTED"
    ) or (
        waiting and not verdicts and terminal is None
    )
    if not valid:
        raise ValueError("reviewer lifecycle is invalid")
    return {
        "session_complete": completed,
        "authoritative_verdict": verdicts[0]["verdict"] if verdicts else None,
        "authoritative_verdict_count": len(verdicts),
        "pre_verdict_abort": aborted,
        "abort_reason": aborts[0].get("reason_code") if aborts else None,
        "waiting": waiting,
    }


def _terminal_reviewer_evidence_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
) -> Mapping[str, Any]:
    ledger = _read_reviewer_session_ledger_with_state(
        project, root, state, attempt_id,
    )
    boundary = _read_attempt_receipt_with_state(
        project, root, state, attempt_id,
        "reviewer-session-boundary", "ARTIFACT_READ_BACK",
    )["record"]
    lifecycle = reviewer_lifecycle_projection(ledger)
    if lifecycle["waiting"]:
        raise ValueError("reviewer terminal evidence is invalid")
    isolation = ledger.get("host_isolation")
    verified = bool(
        isinstance(isolation, Mapping)
        and isolation.get("fresh_context") is True
        and isolation.get("distinct_invocations") is True
        and isolation.get("role_policy") == "canonical-reviewer-v1"
    )
    evidence = {
        "canonical_digest": boundary["canonical_branch_digest"],
        "digest": ledger["digest"],
        **{key: lifecycle[key] for key in (
            "session_complete", "authoritative_verdict", "authoritative_verdict_count",
            "pre_verdict_abort", "abort_reason",
        )},
        "isolation": "verified" if verified else "independence_unverified",
    }
    if not verified and _self_review_accepted_at(project, root, state):
        evidence["self_accepted"] = True
    return evidence


def terminal_reviewer_evidence(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Derive terminal reviewer cardinality and isolation from the durable ledger."""
    project, root = _run_root(run_root)
    return _terminal_reviewer_evidence_with_state(
        project, root, derive_state(root), attempt_id,
    )


def _receipt_target(root: Path, attempt_id: str, kind: str) -> Path:
    directories = {"phase1-artifact": "phase1-artifacts", "structured-result": "structured-results", "reviewer-session-boundary": "reviewer-session-boundaries", "effective-canonical": "effective-canonicals", "automation-review-boundary-r1": "automation-review-boundaries", "automation-review-boundary-r2": "automation-review-boundaries", "execution-inputs": "execution-inputs", "materialization-ownership": "materialization-ownership", "generated-delta": "generated-deltas", "materialization-command": "materialization-commands", "execution-receipt": "execution-receipts", "resume-validation": "resume-validations", "execution-trace": "execution-traces", "trace-audit": "trace-audits", "disposition-plan": "disposition-plans", "disposition-receipt": "disposition-receipts", "batch-plan": "batch-plans", "generator-header": "generator-headers", "reference-cases": "reference-cases", "review-canaries": "review-canaries", "rework-round": "rework-rounds"}
    if kind.startswith(_SKELETON_KIND):  # the skeleton of one batch, next to the whole one
        if not re.fullmatch(r"BATCH-[a-z0-9-]+", kind[len(_SKELETON_KIND):]) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid factual receipt")
        return root / "imported-cases" / f"{attempt_id}.{kind[len(_SKELETON_KIND):]}.json"
    if kind == "imported-cases":  # the skeleton of an import, one per attempt
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid factual receipt")
        return root / "imported-cases" / f"{attempt_id}.json"
    if kind.startswith(_PRIOR_KIND):  # one receipt per batch, in its own folder
        if not re.fullmatch(r"BATCH-[a-z0-9-]+", kind[len(_PRIOR_KIND):]) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid factual receipt")
        return root / "prior-capabilities" / f"{attempt_id}.{kind[len(_PRIOR_KIND):]}.json"
    if kind not in directories or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid factual receipt")
    suffix = "" if not kind.startswith("automation-review-boundary-r") else f".{kind[-2:]}"
    return root / directories[kind] / f"{attempt_id}{suffix}.json"


def _phase5_generated_delta(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> Mapping[str, Any]:
    record = _read_attempt_receipt_with_state(
        project, root, state, str(attempt["attempt_id"]),
        "generated-delta", "ARTIFACT_READ_BACK",
    )["record"]
    delta = record.get("delta")
    if not isinstance(delta, Mapping):
        raise ValueError("invalid generated delta receipt")
    return delta


# The checks of the kinds of factual receipts, one a kind or a family of kinds: the fields every kind has are checked by
# ``_validate_factual_receipt`` before (``common``, ``identity_matches``); the table of kinds is ``_RECEIPT_CHECKS``.
def _check_structured_result(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    expected = terminal_result({
        "run_id": attempt["run_id"], "attempt_id": attempt["attempt_id"],
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    }, attempt["policy_profile"])
    if dict(receipt) != dict(expected):
        raise ValueError("invalid structured result")


def _check_reviewer_session_boundary(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    expected_keys = common | {
        "session_id", "canonical_branch_digest", "package_digest", "generator_role", "reviewer_role",
        "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes",
    }
    isolation = receipt.get("host_isolation")
    isolation_body = {key: value for key, value in isolation.items() if key != "evidence_digest"} if isinstance(isolation, Mapping) else {}
    isolation_digest = "sha256:" + hashlib.sha256(json.dumps(isolation_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or not _safe_label(receipt.get("session_id"))
        or not _DIGEST.fullmatch(str(receipt.get("canonical_branch_digest", "")))
        or not _DIGEST.fullmatch(str(receipt.get("package_digest", "")))
        or type(receipt.get("context_budget_bytes")) is not int
        or receipt.get("context_budget_bytes") < 1
        or receipt.get("generator_role") != "generator"
        or receipt.get("reviewer_role") != "canonical-reviewer"
        or not _safe_label(receipt.get("generator_invocation_id"))
        or not _safe_label(receipt.get("reviewer_invocation_id"))
        or not isinstance(isolation, Mapping)
        or set(isolation) != {"fresh_context", "distinct_invocations", "role_policy", "evidence_digest"}
        or not isinstance(isolation.get("fresh_context"), bool)
        or not isinstance(isolation.get("distinct_invocations"), bool)
        or isolation.get("distinct_invocations") != (receipt.get("generator_invocation_id") != receipt.get("reviewer_invocation_id"))
        or isolation.get("role_policy") != "canonical-reviewer-v1"
        or not _DIGEST.fullmatch(str(isolation.get("evidence_digest", "")))
        or isolation.get("evidence_digest") != isolation_digest
    ):
        raise ValueError("invalid reviewer session boundary")


def _validate_factual_receipt(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    kind: str,
    receipt: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """One factual receipt: the fields every kind has here, the rest by the check of its kind — ``_RECEIPT_CHECKS``, then the
    ``RECEIPT_CHECKS`` of the modules above the journal (``effective_canonical``, ``run_closure``, ``model_inputs``, then the families of
    cases by prefix), then a kind of mode B by its module through the gate; a kind of none is refused."""
    from tools import effective_canonical, model_inputs, run_closure  # the owners of kinds: above the journal by layers, in either delivery

    common = {"schema_version", "kind", "run_id", "attempt_id", "policy_profile", "digest"}
    identity_matches = (
        receipt.get("kind") == kind
        and receipt.get("run_id") == attempt["run_id"]
        and receipt.get("attempt_id") == attempt["attempt_id"]
        and receipt.get("policy_profile") == attempt["policy_profile"]
    )
    check = (_RECEIPT_CHECKS.get(kind) or effective_canonical.RECEIPT_CHECKS.get(kind) or run_closure.RECEIPT_CHECKS.get(kind)
             or model_inputs.RECEIPT_CHECKS.get(kind)
             or next((check for prefix, check in model_inputs.RECEIPT_FAMILIES if kind.startswith(prefix)), None))
    if check is None and kind in _MODE_B_KINDS:  # MODE_B_NOT_IN_PACKAGE in a delivery of cases
        check = execution_gate.from_mode_b("execution_journal", "RECEIPT_CHECKS")[0][kind]
    if check is None:
        raise ValueError("invalid factual receipt")
    check(project, root, attempt, kind, receipt, state, common, identity_matches)


def publish_attempt_receipt(
    run_root: Path,
    attempt_id: str,
    kind: str,
    facts: Mapping[str, Any],
    *,
    _controller_token: object | None = None,
) -> Mapping[str, Any]:
    """Create/read back one immutable factual receipt bound to an existing attempt."""
    if kind in {"execution-inputs", "resume-validation"} and _controller_token is not _DEDICATED_RECEIPT_TOKEN:
        raise ValueError(f"{kind} requires its dedicated publisher")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or set(facts) & {"schema_version", "kind", "run_id", "attempt_id", "policy_profile", "digest"}:
        raise ValueError("invalid factual receipt")
    target = _receipt_target(root, attempt_id, kind)
    if kind == "structured-result":
        value = terminal_result({"run_id": attempt["run_id"], "attempt_id": attempt_id, **dict(facts)}, attempt["policy_profile"])
        _validate_factual_receipt(project, root, attempt, kind, value, state)
        receipt, created, installed_identity = _publish(project, root, target, {key: value for key, value in value.items() if key != "digest"}, "terminal result", return_created=True)
    else:
        if kind == "execution-receipt":  # MODE_B_NOT_IN_PACKAGE in a delivery of cases
            facts = execution_gate.from_mode_b("execution_journal", "execution_receipt_facts")[0](project, root, state, attempt, facts)
        value = {
            "schema_version": "1.0.0", "kind": kind, "run_id": attempt["run_id"],
            "attempt_id": attempt_id, "policy_profile": attempt["policy_profile"], **dict(facts),
        }
        _validate_factual_receipt(project, root, attempt, kind, _sealed(value), state)
        receipt, created, installed_identity = _publish(project, root, target, value, kind, return_created=True)
    data = _canonical_bytes(receipt)
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": data, "digest": receipt["digest"], "record": receipt, "created": created, "installed_identity": installed_identity}


def _read_attempt_receipt_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    kind: str,
    event_type: str,
) -> Mapping[str, Any]:
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError("unknown factual receipt attempt")
    target = _receipt_target(root, attempt_id, kind)
    receipt = _read_artifact(project, root, target, kind)
    cache = _CLOSURE_CACHE.get()
    key = None
    if cache is not None and kind not in _UNCACHED_RECEIPT_KINDS and state["events"]:
        key = ("receipt", str(target), kind, attempt_id, hashlib.sha256(_canonical_bytes(receipt)).hexdigest(), state["events"][-1]["digest"])
    if key is None or key not in cache:
        _validate_factual_receipt(project, root, attempt, kind, receipt, state)
        if key is not None:
            cache[key] = True
    event = next((item for item in state["events"] if item["event_type"] == event_type and item.get("attempt_id") == attempt_id and item.get("artifact_digest") == receipt["digest"]), None)
    if event is None:
        raise ValueError("unbound factual receipt")
    data = _canonical_bytes(receipt)
    if read_confined_bytes(project, root, target) != data:
        raise ValueError("factual receipt read-back mismatch")
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": data, "digest": receipt["digest"], "record": receipt}


def read_attempt_receipt(
    run_root: Path,
    attempt_id: str,
    kind: str,
    event_type: str,
) -> Mapping[str, Any]:
    """Read a factual receipt only when the exact active attempt event binds its digest."""
    project, root = _run_root(run_root)
    return _read_attempt_receipt_with_state(
        project, root, derive_state(root), attempt_id, kind, event_type,
    )


_RECEIPT_FOLDERS = ("phase1-artifacts", "structured-results", "reviewer-session-boundaries", "effective-canonicals", "automation-review-boundaries",
                    "execution-inputs", "materialization-ownership", "generated-deltas", "materialization-commands", "execution-receipts", "resume-validations", "execution-traces",
                    "trace-audits", "disposition-plans", "disposition-receipts", "batch-plans", "generator-headers", "reference-cases", "review-canaries",
                    "imported-cases", "prior-capabilities", "rework-rounds")


def lost_attempt_receipts(root: Path, state: Mapping[str, Any], attempt_id: str) -> list[str]:
    """The digests the journal read back as receipts of the attempt (``ARTIFACT_READ_BACK`` without a batch) that no receipt file of the
    run carries any more — deleted or replaced.  Reads files, writes nothing."""
    wanted = [str(event["artifact_digest"]) for event in state["events"]
              if event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") is None
              and isinstance(event.get("artifact_digest"), str)]
    if not wanted:
        return []
    present: set[str] = set()
    project, run = _run_root(root)
    for pattern in (*(f"{folder}/{attempt_id}*.json" for folder in _RECEIPT_FOLDERS), f"closure/{attempt_id}/*.json", f"terminal-results/{attempt_id}.json"):  # the closure and the result are read back without a batch too
        for path in run.glob(pattern):  # the paths of the run as the core pins it (root may be written relative or in another case)
            try:
                value = _check_sealed(json.loads(read_confined_bytes(project, run, path)), "receipt")  # through the core: a link in the place of a file is no receipt; the digest field must be the digest of the body
            except (OSError, ValueError, TypeError):
                continue
            present.add(value["digest"])
    return sorted(dict.fromkeys(digest for digest in wanted if digest not in present))


def written_files(run_root: Path) -> dict[str, set[str]]:
    """``{project path: {digests}}`` of the files the package wrote in this run: ``content_digest`` of the ownership receipt, ``quarantined_digest``
    of a quarantined row of the final dispositions.  A light read for the model input and the map, not a proof: each receipt is read through
    ``read_confined_bytes``, sealed, and counted only when the journal of the attempt read its digest back.  Anything unclear raises
    ``ValueError`` — the caller then treats no file of the run as written by the package.  Needs no module of mode B; only reads."""
    project, root = _run_root(run_root)
    state, found = derive_state(root), {}
    for attempt in state["attempts"]:
        attempt_id = str(attempt["attempt_id"])
        bound = {event["artifact_digest"] for event in state["events"] if event["event_type"] == "ARTIFACT_READ_BACK"
                 and event.get("attempt_id") == attempt_id and event.get("batch_id") is None}
        payloads = {}
        for kind in ("materialization-ownership", "disposition-receipt"):
            target = _receipt_target(root, attempt_id, kind)
            if target.exists():
                receipt = _read_artifact(project, root, target, kind)
                if receipt["digest"] not in bound:
                    raise ValueError(f"unbound {kind}")
                payloads[kind] = receipt["payload"]
        if "materialization-ownership" not in payloads:
            continue
        owned, test_root = payloads["materialization-ownership"], payloads["materialization-ownership"]["test_root"].rstrip("/") + "/"
        prefix = "" if owned["module"] == "." else owned["module"].rstrip("/") + "/"
        for row in owned["files"]:
            path = row["path"].replace("\\", "/")
            if not path.startswith(test_root) or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise ValueError("written path outside the test root")
            found.setdefault(prefix + path, set()).add(row["content_digest"])
        final = payloads.get("disposition-receipt") or {}
        for row in final.get("files", []) if final.get("stage") == "dispositions" else []:
            key = prefix + row["path"].replace("\\", "/")
            if row["disposition"] == "QUARANTINED" and key in found:
                found[key].add(row["quarantined_digest"])
    return found


class ReceiptFileUnbound(ValueError):
    """The file of a receipt lies without its events and its bytes are not the ones being published (one code for every family)."""

    code = "RECEIPT_FILE_UNBOUND"


_UNBOUND_FIX = ("fix: повтори команду с теми же входами, что дали файл (она допишет события журнала), иные входы — новый прогон "
                "(scan --new-run)")


def _bind_events(root: Path, attempt_id: str, digest: str, *, batch_id: str | None = None) -> None:
    """The one place the pair ARTIFACT_PUBLISHED / ARTIFACT_READ_BACK of a published file is written: only the events the journal
    does not have yet, in order (READ_BACK without PUBLISHED is refused).  ``batch_id`` names the family (``reviewer-ledger-v1``,
    ``left-v1`` …); none for a fixed receipt of the attempt."""
    relevant = [
        event["event_type"]
        for event in derive_state(root)["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == digest
        and event.get("batch_id") == batch_id
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    extra = {} if batch_id is None else {"batch_id": batch_id}
    if relevant == []:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=digest, **extra)
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=digest, **extra)
    elif relevant == ["ARTIFACT_PUBLISHED"]:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=digest, **extra)
    elif relevant != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        raise ValueError("invalid fixed receipt event order")


def _file_is_bound(root: Path, attempt_id: str, target: Path) -> bool:
    """Whether the journal has a ARTIFACT_READ_BACK for the digest the file at ``target`` names (its own ``digest`` field) — a bound file is
    a fact of the run; a file without it is what a cut command left."""
    try:
        digest = json.loads(target.read_bytes().decode("utf-8")).get("digest")
    except (OSError, ValueError, AttributeError):
        return False
    return any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("artifact_digest") == digest
               for event in derive_state(root)["events"])


def publish_bound(run_root: Path, attempt_id: str, kind: str, facts: Mapping[str, Any], *, batch_id: str | None = None) -> Mapping[str, Any]:
    """Publish one fixed receipt of the attempt and bind it to the journal — the single way, for every family, that a command may
    leave half done and run again.

    Contract: the bytes are the ones this call computes from ``facts`` (validated as ``publish_attempt_receipt`` does); the file at the
    receipt's slot is only compared with them, never read as their source.
      * no file                → written, then ARTIFACT_PUBLISHED and ARTIFACT_READ_BACK;
      * the file, the same bytes → the events the journal lacks are written (none twice, in order);
      * the file, other bytes   → ReceiptFileUnbound (RECEIPT_FILE_UNBOUND with its fix), nothing is written.
    ``batch_id`` marks the events of a family (canaries, ledger …).  Returns the read-back receipt (``record``, ``digest``, ``path``,
    ``bytes``) with ``created`` and ``installed_identity``."""
    try:
        published = publish_attempt_receipt(
            run_root, attempt_id, kind, facts,
            _controller_token=(_DEDICATED_RECEIPT_TOKEN if kind in {"execution-inputs", "resume-validation"} else None),
        )
    except ValueError as error:
        _project, root = _run_root(run_root)
        if (_receipt_target(root, attempt_id, kind).exists() and ("read-back mismatch" in str(error) or str(error).startswith("unsafe "))
                and not _file_is_bound(root, attempt_id, _receipt_target(root, attempt_id, kind))):  # a bound file of another value is a conflict, not a cut
            raise ReceiptFileUnbound(
                f"RECEIPT_FILE_UNBOUND: файл {_receipt_target(root, attempt_id, kind).relative_to(root).as_posix()} лежит без событий журнала, "
                f"а его байты не те, что публикуются сейчас ({error}); ничего не записано; {_UNBOUND_FIX}") from error
        raise
    _project, root = _run_root(run_root)
    _bind_events(root, attempt_id, published["digest"], batch_id=batch_id)
    readback = read_attempt_receipt(root, attempt_id, kind, "ARTIFACT_READ_BACK")
    return {**readback, "created": published["created"], "installed_identity": published["installed_identity"]}


def bind_bytes(run_root: Path, attempt_id: str, target: Path, data: bytes, *, digest: str, batch_id: str | None = None,
               label: str) -> Mapping[str, Any]:
    """The same contract for a content-addressed file that is no fixed receipt (a ledger revision, a run artifact of the attempt).

    ``data`` is what the caller computed; ``digest`` the identity the events carry (the record's own digest).  The file at ``target`` is compared with ``data`` and is never the
    source of the bytes (what is found on a disk is not a fact of the run).  No file → written; the same bytes → only the missing events
    are written; other bytes → ReceiptFileUnbound.  Returns ``{created, installed_identity, path, digest}``."""
    project, root = _run_root(run_root)
    if not isinstance(data, (bytes, bytearray)) or not _DIGEST.fullmatch(str(digest)):
        raise ValueError(f"invalid {label}: bytes and a sha256 digest are required")
    data = bytes(data)
    try:
        created, identity = create_confined_bytes_exclusive(project, root, target, data, return_identity=True)
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        if target.exists():
            raise ReceiptFileUnbound(f"RECEIPT_FILE_UNBOUND: файл {target.relative_to(root).as_posix()} ({label}) лежит не с теми байтами, что публикуются "
                                     f"сейчас; ничего не записано; {_UNBOUND_FIX}") from error
        raise ValueError(f"unsafe {label} path") from error
    if raw != data:
        if created:
            try:
                remove_confined_bytes_if_equal(project, root, target, raw or b"", expected_identity=identity)
            except OutputConfinementError:
                pass
        raise ValueError(f"{label} read-back mismatch")
    _bind_events(root, attempt_id, digest, batch_id=batch_id)
    return {"created": created, "installed_identity": identity, "path": str(target.relative_to(root)).replace("\\", "/"), "digest": digest}


def _execution_inputs_frontier_closed(run_root: Path, attempt_id: str) -> bool:
    """Return whether any effect which requires read-back inputs already exists."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    if any(
        event.get("attempt_id") == attempt_id
        and event.get("event_type") == "EXECUTION_STARTED"
        for event in state["events"]
    ):
        return True
    for kind in ("materialization-ownership", "generated-delta"):
        try:
            raw = read_confined_bytes(project, root, _receipt_target(root, attempt_id, kind))
        except OutputConfinementError as error:
            raise ValueError("unsafe execution-inputs frontier path") from error
        if raw is not None:
            return True
    return False


def recover_execution_inputs_events(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Bind an installed input receipt only before materialization/execution."""
    if _execution_inputs_frontier_closed(run_root, attempt_id):
        raise ValueError("execution-inputs frontier is closed")
    return _recover_attempt_receipt_events(run_root, attempt_id, "execution-inputs")


def read_execution_inputs(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Read, or safely finish binding, the durable reviewed carriers."""
    try:
        return read_attempt_receipt(
            run_root, attempt_id, "execution-inputs", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError) as error:
        project, root = _run_root(run_root)
        try:
            installed = read_confined_bytes(
                project, root, _receipt_target(root, attempt_id, "execution-inputs"),
            )
        except OutputConfinementError as confinement_error:
            raise ValueError("unsafe execution-inputs receipt path") from confinement_error
        if installed is None:
            raise error
        return recover_execution_inputs_events(root, attempt_id)["record"]


def read_resume_validation_if_present(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Return or finish binding the one installed post-receipt validation."""
    project, root = _run_root(run_root)
    target = _receipt_target(root, attempt_id, "resume-validation")
    try:
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError("unsafe resume validation path") from error
    if raw is None:
        return None
    try:
        return read_attempt_receipt(
            root, attempt_id, "resume-validation", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError):
        pass
    return _recover_attempt_receipt_events(root, attempt_id, "resume-validation")["record"]


def _recover_attempt_receipt_events(run_root: Path, attempt_id: str, kind: str) -> Mapping[str, Any]:
    """Repair only missing journal bindings for one exact fixed receipt."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("invalid phase5 receipt recovery attempt")
    target = _receipt_target(root, attempt_id, kind)
    receipt = _read_artifact(project, root, target, kind)
    _validate_factual_receipt(project, root, attempt, kind, receipt, state)
    digest = receipt["digest"]
    events = [item for item in state["events"] if item.get("attempt_id") == attempt_id and item.get("artifact_digest") == digest]
    published = any(item["event_type"] == "ARTIFACT_PUBLISHED" for item in events)
    read_back = any(item["event_type"] == "ARTIFACT_READ_BACK" for item in events)
    if read_back and not published:
        raise ValueError("invalid phase5 receipt recovery event order")
    if not published:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    if not read_back:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    return read_attempt_receipt(root, attempt_id, kind, "ARTIFACT_READ_BACK")


def recover_phase5_receipt_events(run_root: Path, attempt_id: str, kind: str) -> Mapping[str, Any]:
    """Repair only missing journal bindings for an already exact fixed Phase 5 receipt."""
    if kind not in {"generated-delta", "disposition-plan", "disposition-receipt"}:
        raise ValueError("invalid phase5 receipt recovery kind")
    return _recover_attempt_receipt_events(run_root, attempt_id, kind)


class IndependenceUnverified(ValueError):
    """The host states no fresh context in a run that admitted no SELF (``scan --accept-self-review`` or ``prepare --accept-self-review``)."""

    code = "INDEPENDENCE_UNVERIFIED"


def require_fresh_context_statement(run_root: Path, fresh_context: bool) -> None:
    """``fresh_context`` is the host's statement; ``no`` needs SELF admitted (at scan or at the step of the review), ``yes`` is left to the protocol."""
    if type(fresh_context) is not bool:
        raise ValueError("fresh_context must be the host's yes or no")
    if not fresh_context and not self_review_accepted(run_root):
        raise IndependenceUnverified(
            "INDEPENDENCE_UNVERIFIED: ревьюер без свежего контекста, а прогон не принял SELF; fix: только если человек согласен "
            "на ревью в той же сессии, повтори prepare с --fresh-context no --accept-self-review; иначе вызови ревьюера отдельным "
            "субагентом без истории генерации и укажи --fresh-context yes"
        )


_PRIOR_KIND = "prior-capabilities-"  # + batch_id — the fifth tc-generator input from batch 2
_SKELETON_KIND = "imported-cases-"  # + batch_id — the skeleton of the batch, its last tc-generator input
# a kind -> its check (``_validate_factual_receipt``); the kinds of the modules above the journal are in their ``RECEIPT_CHECKS``
# (``effective_canonical``, ``run_closure``, ``model_inputs``; the families of cases: ``model_inputs.RECEIPT_FAMILIES``)
_RECEIPT_CHECKS = {
    "phase1-artifact": _check_phase1_artifact, "structured-result": _check_structured_result,
    "reviewer-session-boundary": _check_reviewer_session_boundary,
}
# the kinds of mode B: their checks are in its module (``execution_journal``), which a delivery of cases does not carry
_MODE_B_KINDS = frozenset({
    "automation-review-boundary-r1", "automation-review-boundary-r2", "execution-inputs", "generated-delta", "execution-receipt",
    "materialization-command", "resume-validation", "execution-trace", "trace-audit", "materialization-ownership", "disposition-plan",
    "disposition-receipt",
})


def publish_review_canaries(run_root: Path, attempt_id: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bind the digests of the canary map and of the reviewer's view to the attempt: the receipt and its event pair, marked with
    ``batch_id`` ``review-canaries-v1`` — whether canaries were seeded is read from these events, not from a file.  The same facts
    again change nothing."""
    from tools.review_canaries import BATCH_ID

    return publish_bound(run_root, attempt_id, "review-canaries", dict(facts), batch_id=BATCH_ID)


# Public names for execution_core / finalize_attempt: the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.
append_journal_event = _append_event
check_sealed_value = _check_sealed
frozen_inventory = _frozen_inventory
publish_artifact_value = _publish
run_root_of = _run_root
validate_execution_event_branch = _validate_execution_event_branch
publish_fixed_receipt_resuming = publish_bound  # the public name (execution_core, exec_commands) is the one contract; drop it when they call publish_bound
canonical_digest = _digest


# what ``tools.execution_journal`` (mode B) takes from here — public names of the same objects (П1 of the cut of v0.8)
DIGEST = _DIGEST
PACK_ROOT = _PACK_ROOT
SCHEMA_ROOT = _SCHEMA_ROOT
credential_like = _credential_like
disposition_policy_at = _disposition_policy_at
execution_inputs_frontier_closed = _execution_inputs_frontier_closed
model_lifecycle_projection_with_state = _model_lifecycle_projection_with_state
phase5_generated_delta = _phase5_generated_delta
read_artifact = _read_artifact
read_attempt_receipt_with_state = _read_attempt_receipt_with_state
receipt_target = _receipt_target
run_boundary = _run_boundary
safe_label = _safe_label
sealed = _sealed
self_review_accepted_at = _self_review_accepted_at
validate_schema = _validate_schema

# what the modules of the journal above this one (the records and the inputs of a step of the model, the step, the effective canonical
# set, the closure, the observations) take from here — public names of the same objects (П1 of the cut of v0.8)
CLOSURE_CACHE = _CLOSURE_CACHE
CLOSURE_KINDS = _CLOSURE_KINDS
MODEL_STAGE_INSTANCE = _MODEL_STAGE_INSTANCE
MODEL_STAGE_REGISTRY = _MODEL_STAGE_REGISTRY
PRIOR_KIND = _PRIOR_KIND
REWORK_REVIEW_STAGE = _REWORK_REVIEW_STAGE
REWORK_STAGE = _REWORK_STAGE
SKELETON_KIND = _SKELETON_KIND
bind_events = _bind_events
canonical_bytes = _canonical_bytes
closure_target = _closure_target
parse_json_bytes = _parse_json_bytes
read_reviewer_session_ledger_with_state = _read_reviewer_session_ledger_with_state
safe_model_label = _safe_model_label
terminal_reviewer_evidence_with_state = _terminal_reviewer_evidence_with_state
validate_result_record = _validate_result_record


# The facade of the journal for the cut of v0.8 (handoff: design-core-split.md §2.2 П2; tests: orch-tools/tests/test_facade.py):
# a name moved out is read from the module that took it, and a patch of a name reaches every module of the journal binding it.
_MISSING = object()


def _facade(name: str, owners: Callable[[], Sequence[types.ModuleType]],
            optional: Callable[[], Sequence[types.ModuleType]] = tuple) -> None:
    """Make the loaded module ``name`` the facade of ``owners()`` and ``optional()``."""
    import sys

    def modules() -> tuple[types.ModuleType, ...]:
        return (*owners(), *optional())

    class Facade(types.ModuleType):
        def __getattr__(self, attribute: str) -> Any:
            if attribute.startswith("__") and attribute.endswith("__"):  # the import machinery asks for these: never an owner's
                raise AttributeError(attribute)
            for module in modules():
                value = vars(module).get(attribute, _MISSING)
                if value is not _MISSING:
                    return value
            raise AttributeError(f"module {name!r} has no attribute {attribute!r}")

        def __setattr__(self, attribute: str, value: Any) -> None:
            own = vars(self)
            if attribute.startswith("__") and attribute.endswith("__"):
                types.ModuleType.__setattr__(self, attribute, value)
                return
            current = own.get(attribute, _MISSING)
            if current is _MISSING:
                current = next((vars(module)[attribute] for module in modules() if attribute in vars(module)), _MISSING)
            bound = [module for module in modules() if current is not _MISSING and vars(module).get(attribute, _MISSING) is current]
            if attribute in own or not bound:
                types.ModuleType.__setattr__(self, attribute, value)
            for module in bound:
                setattr(module, attribute, value)

    sys.modules[name].__class__ = Facade


def _journal_modules() -> tuple[types.ModuleType, ...]:
    """The modules that took the code of the journal, the lowest first (imported here: those above it import this module at their top;
    ``run_store`` and ``run_journal`` below it are imported at the top of this one)."""
    from tools import effective_canonical, model_inputs, model_records, model_steps, run_closure, run_journal, run_store, scenario_observations

    return (run_store, run_journal, model_records, effective_canonical, run_closure, model_inputs, model_steps, scenario_observations)


def _journal_mode_b() -> tuple[types.ModuleType, ...]:
    """The module of the journal of mode B, when the delivery carries mode B: once loaded, from ``sys.modules`` — a miss of the facade
    asks no finder (a blocked module, ``None`` there, still reads as absent); before that, through the gate."""
    loaded = sys.modules.get("tools.execution_journal")
    if loaded is not None:
        return (loaded,)
    return (execution_gate.load_mode_b("execution_journal"),) if execution_gate.mode_b_available() else ()


_facade(__name__, _journal_modules, _journal_mode_b)
