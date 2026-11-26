"""The ledger of the reviewer (v0.8, the cut of the core): the consent to a self review and the disposition policy, the ledger of the
reviewer session and its reading, the lifecycle of the reviewer and its terminal evidence, the statement of a fresh context.  Right
above the receipts (``run_receipts``) and below every other module of the journal: it imports none of them.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/r6.tsv``);
``pilot_state`` takes from here what its code reads and still serves every name to its callers (the facade of the cut).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.confined_output import OutputConfinementError, read_confined_bytes
from tools.run_store import (  # the run store, below (П1 of the cut of v0.8)
    DIGEST as _DIGEST,
    PACK_ROOT as _PACK_ROOT,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    is_reparse as _is_reparse,
    run_boundary as _run_boundary,
    run_root_of as _run_root,
)
from tools.run_journal import derive_state
from tools.run_receipts import ReceiptFileUnbound, bind_bytes, read_attempt_receipt_with_state as _read_attempt_receipt_with_state


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


# public names of the same objects: what ``pilot_state`` and the modules above read (П1 of the cut of v0.8)
disposition_policy_at = _disposition_policy_at
read_reviewer_session_ledger_with_state = _read_reviewer_session_ledger_with_state
self_review_accepted_at = _self_review_accepted_at
terminal_reviewer_evidence_with_state = _terminal_reviewer_evidence_with_state
