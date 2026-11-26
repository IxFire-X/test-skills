"""The receipts of an attempt (v0.8, the cut of the core): the folder of each kind, the publication and the reading of a factual
receipt and the dispatcher of its kind, the files bound to the journal, the lost receipts and the written files, the execution
inputs and their recovery.  Right above the journal of events (``run_journal``) and below every other module of the journal: it
imports none of them at its top (the dispatcher asks the owners of the kinds above it when it runs).

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/r6.tsv``);
``pilot_state`` takes from here what its code reads and still serves every name to its callers (the facade of the cut).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from tools import execution_gate
from tools.confined_output import OutputConfinementError, create_confined_bytes_exclusive, read_confined_bytes, remove_confined_bytes_if_equal
from tools.run_store import (  # the run store, below (П1 of the cut of v0.8)
    CLOSURE_CACHE as _CLOSURE_CACHE,
    DIGEST as _DIGEST,
    canonical_bytes as _canonical_bytes,
    check_sealed_value as _check_sealed,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    run_root_of as _run_root,
    safe_label as _safe_label,
    sealed as _sealed,
    terminal_result,
)
from tools.run_journal import append_event, check_phase1_artifact as _check_phase1_artifact, derive_state


_DEDICATED_RECEIPT_TOKEN = object()


# These validations look at the project tree (module directory, runtime paths), not only at the run directory —
# directly (phase1-artifact, execution-receipt) or through a nested read of execution-receipt (resume-validation,
# execution-trace, trace-audit).  Nothing else reads one of these five, so the set is closed.
_UNCACHED_RECEIPT_KINDS = frozenset({"phase1-artifact", "execution-receipt", "resume-validation", "execution-trace", "trace-audit"})


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


# public names of the same objects: what ``pilot_state`` and the modules above read (П1 of the cut of v0.8)
PRIOR_KIND = _PRIOR_KIND
SKELETON_KIND = _SKELETON_KIND
bind_events = _bind_events
execution_inputs_frontier_closed = _execution_inputs_frontier_closed
phase5_generated_delta = _phase5_generated_delta
read_attempt_receipt_with_state = _read_attempt_receipt_with_state
receipt_target = _receipt_target
