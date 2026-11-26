"""The journal of a run (v0.8, the cut of the core): the events and their transitions, the state derived from them and its cache within
a command, the append of an event, the run and its attempts, the module, the frozen inputs and the baseline; the check of the receipt
``phase1-artifact``, the terminal transition and the closure artifact it reads.  Right above the files of a run (``run_store``) and
below every other module of the journal: it imports none of them.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/r5.tsv``);
``pilot_state`` takes from here what its code reads and still serves every name to its callers (the facade of the cut).
"""
from __future__ import annotations

import copy
import functools
import hashlib
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import run_lock
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, create_confined_bytes_exclusive, create_confined_directory_exclusive, ensure_project_child_directory, read_confined_bytes, remove_confined_bytes_if_equal
from tools.project_inventory import read_inventory_receipt
from tools.run_store import (  # the files of a run, right below (П1 of the cut of v0.8)
    CLOSURE_CACHE as _CLOSURE_CACHE,
    DIGEST as _DIGEST,
    InputRefusal as _InputRefusal,
    PACK_ROOT as _PACK_ROOT,
    UNSAFE_RUN_ROOT as _UNSAFE_RUN_ROOT,
    canonical_bytes as _canonical_bytes,
    canonical_digest as _digest,
    check_sealed_value as _check_sealed,
    is_reparse as _is_reparse,
    moved_recorded as _moved_recorded,
    parse_json_bytes as _parse_json_bytes,
    project_root as _project_root,
    project_state_for as _project_state_for,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    read_run,
    run_boundary as _run_boundary,
    run_root_is_unsafe,
    run_root_of as _run_root,
    safe_label as _safe_label,
    sealed as _sealed,
    validate_authorization as _validate_authorization,
    validate_schema as _validate_schema,
)


_MODEL_EVENT_TYPES = {"MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED"}
_MODEL_STAGE_INSTANCE = re.compile(
    r"^(?:context-marker:(?:baseline|requirements|batch-[0-9]{3})|tc-generator:(?:BATCH-[a-z0-9-]+|rework-1)|assembly|tc-reviewer:(?:canonical|rework-1)|tc-to-autotest:r[12]|autotest-reviewer:r[12])$"
)
REWORK_BATCH = "rework-round-v1"  # the mark of the events of the receipt ``rework-round``: the first review sent cases back and the code waits for the rework
_REWORK_STAGE = "tc-generator:rework-1"


_EVENT_TYPES = {"MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "CONTEXT_SELECTED", "ATTEMPT_CREATED", *_MODEL_EVENT_TYPES, "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN", "COMMAND_REFUSED"}
_ACTORS = {"controller"}
_ATTEMPT_EVENTS = {"ATTEMPT_CREATED", "CONTEXT_SELECTED", *_MODEL_EVENT_TYPES, "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN"}
_CLOSURE_KINDS = {"pre_finalization_trace", "finalization_receipt", "terminal_trace"}


class _PublicationUnknown(run_lock.JournalRefusal):
    """A journal writer failed after an unclassifiable durable-state transition."""

    def __init__(self, message: str) -> None:
        super().__init__("PUBLICATION_UNKNOWN", message)


def _typed_corruption(function):
    """A check of the journal that fails says ``JOURNAL_CORRUPT``; a refusal with a code of its own and a missing journal pass as they are."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from tools.json_cli import error_code_prefix

        try:
            return function(*args, **kwargs)
        except ValueError as error:
            if getattr(error, "code", None) or error_code_prefix(str(error)):
                raise
            raise run_lock.JournalRefusal("JOURNAL_CORRUPT", str(error)) from error

    return wrapper


def _validate_event_transitions(events: list[dict[str, Any]]) -> None:
    """Validate the closed Phase 1 order without inferring later-phase semantics."""
    module_selected = False
    inventory_ready = False
    snapshot_bound = False
    phase_two_ready = False
    created: set[str] = set()
    states: dict[str, str] = {}
    unknown: set[str] = set()
    stopped: set[str] = set()
    execution_started: set[str] = set()
    terminal_retries: set[str] = set()
    retained_reruns: set[str] = set()
    model_requests: set[tuple[str, str]] = set()
    model_responses: set[tuple[str, str]] = set()
    model_response_digests: dict[tuple[str, str], str] = {}
    candidates: set[tuple[str, str]] = set()
    candidate_stages: set[tuple[str, str]] = set()
    assembled: set[str] = set()
    review_requests: set[tuple[str, str]] = set()
    context_selected: set[str] = set()
    rework_marked: set[str] = set()  # the attempts whose first review sent cases back (the receipt ``rework-round``)
    for event in events:
        event_type = event["event_type"]
        attempt_id = event.get("attempt_id")
        if event_type == "MODULE_SELECTED":
            if module_selected or attempt_id is not None:
                raise ValueError("invalid event journal")
            module_selected = True
        if event_type == "INVENTORY_READY":
            if not module_selected or attempt_id is not None:
                raise ValueError("invalid event journal")
            inventory_ready = True
            snapshot_bound = False
            phase_two_ready = False
        if event_type == "SNAPSHOT_BOUND":
            if not inventory_ready or snapshot_bound or phase_two_ready or attempt_id is not None:
                raise ValueError("invalid event journal")
            snapshot_bound = True
        if event_type == "EXECUTION_BASELINE_FROZEN":
            if not module_selected or not snapshot_bound or phase_two_ready or attempt_id is not None:
                raise ValueError("invalid event journal")
            phase_two_ready = True
        if event_type == "ATTEMPT_CREATED":
            if not module_selected or not phase_two_ready or attempt_id in created or any(state != "TERMINAL" for state in states.values()):
                raise ValueError("invalid event journal")
            created.add(attempt_id)
            states[attempt_id] = "ACTIVE"
            continue
        if attempt_id is not None:
            if attempt_id not in created:
                raise ValueError("invalid event journal")
            if event_type == "TERMINAL_RETRY_OBSERVED":
                if states.get(attempt_id) != "TERMINAL" or attempt_id in terminal_retries:
                    raise ValueError("invalid terminal retry event")
                terminal_retries.add(attempt_id)
                continue
            if event_type == "RETAINED_NATIVE_RERUN":
                if states.get(attempt_id) != "TERMINAL" or attempt_id in retained_reruns:
                    raise ValueError("invalid retained native rerun event")
                retained_reruns.add(attempt_id)
                continue
            if event_type == "PROCESS_STOPPED":
                if attempt_id not in unknown or attempt_id in stopped:
                    raise ValueError("unproved process-stop event")
                stopped.add(attempt_id)
                continue
            elif states.get(attempt_id) == "TERMINAL":
                raise ValueError("invalid event journal")
            if event_type == "EXECUTION_STARTED":
                if attempt_id in execution_started or any(
                    key[0] == attempt_id and key not in model_responses
                    # the rework or its second round closed by --abort has no answer; the automation of the attempt exists only after the effective canonical (the request reads it), so the result was published
                    and not (key[1] in (_REWORK_STAGE, _REWORK_REVIEW_STAGE) and (attempt_id, "tc-to-autotest:r1") in model_responses)
                    for key in model_requests
                ):
                    raise ValueError("invalid event journal")
                execution_started.add(attempt_id)
            elif event_type == "WAITING_FOR_INPUT":
                states[attempt_id] = "WAITING_FOR_INPUT"
            elif event_type == "WAITING_FOR_MODEL":
                states[attempt_id] = "WAITING_FOR_MODEL"
            elif event_type == "EXECUTION_UNKNOWN":
                unknown.add(attempt_id)
            elif event_type == "ATTEMPT_TERMINAL":
                states[attempt_id] = "TERMINAL"
            elif event_type in _MODEL_EVENT_TYPES:
                stage = event.get("stage_instance_id")
                if not isinstance(stage, str) or attempt_id in execution_started:
                    raise ValueError("invalid model event")
                key = (attempt_id, stage)
                if event_type == "MODEL_REQUESTED":
                    if key in model_requests:
                        raise ValueError("duplicate model request")
                    # the marker in parts — requirements, then the batches, then the merge by code.
                    if stage.startswith("context-marker:") and stage != "context-marker:baseline" and (attempt_id, "context-marker:baseline") in model_requests:
                        raise ValueError("context-marker part request after the merged request")
                    if stage.startswith("context-marker:batch-") and (attempt_id, "context-marker:requirements") not in model_responses:
                        raise ValueError("context-marker part request requires the requirements answer")
                    if stage == "context-marker:baseline" and any(
                        request_attempt == attempt_id and request_stage.startswith("context-marker:")
                        and request_stage != "context-marker:baseline" and (request_attempt, request_stage) not in model_responses
                        for request_attempt, request_stage in model_requests
                    ):
                        raise ValueError("merged context-marker request requires every part answer")
                    if stage.startswith("tc-generator:BATCH-") and attempt_id not in context_selected:
                        raise ValueError("model request requires selected context")
                    if stage.startswith("tc-generator:") and (
                        (attempt_id, "context-marker:baseline") not in model_responses
                        or attempt_id in assembled and not (stage == _REWORK_STAGE and attempt_id in rework_marked)
                        or stage == _REWORK_STAGE and attempt_id not in rework_marked
                    ):
                        raise ValueError("generator request is outside canonical assembly lifecycle")
                    if stage.startswith(("tc-reviewer:", "autotest-reviewer:")) and key not in review_requests:
                        raise ValueError("review model request requires review request")
                    model_requests.add(key)
                elif event_type == "MODEL_RESPONSE_RECEIVED":
                    if key not in model_requests or key in model_responses or stage.startswith("tc-generator:BATCH-") and attempt_id in assembled:
                        raise ValueError("model response requires one request")
                    model_responses.add(key)
                    model_response_digests[key] = str(event.get("artifact_digest"))
                elif event_type == "CANDIDATE_PUBLISHED":
                    if stage == "assembly":
                        if not any(
                            response_attempt == attempt_id and response_stage.startswith("tc-generator:")
                            for response_attempt, response_stage in model_responses
                        ):
                            raise ValueError("assembled candidate requires generator response")
                        if attempt_id in assembled:
                            raise ValueError("assembled candidate already published")
                        assembled.add(attempt_id)
                    elif (
                        not stage.startswith(("tc-generator:", "tc-to-autotest:"))
                        or key not in model_responses
                        or key in candidate_stages
                        or stage.startswith("tc-generator:BATCH-") and attempt_id in assembled
                        or model_response_digests.get(key) != event.get("artifact_digest")
                    ):
                        raise ValueError("candidate publication requires model response")
                    else:
                        candidate_stages.add(key)
                    candidate = (attempt_id, str(event.get("artifact_digest")))
                    if candidate in candidates:
                        raise ValueError("candidate already published")
                    candidates.add(candidate)
                elif event_type == "REVIEW_REQUESTED":
                    if not stage.startswith(("tc-reviewer:", "autotest-reviewer:")) or key in review_requests or (attempt_id, str(event.get("artifact_digest"))) not in candidates:
                        raise ValueError("review request requires exact published candidate")
                    review_requests.add(key)
            elif event_type == "CONTEXT_SELECTED":
                context_selected.add(attempt_id)
            elif event_type == "ARTIFACT_READ_BACK" and event.get("batch_id") == REWORK_BATCH:
                rework_marked.add(attempt_id)


def _pending_event_path(root: Path, seq: int) -> Path:
    return root / "pending-events" / f"{seq:010d}.json"


def _pending_event_value(event: Mapping[str, Any], data: bytes, prior: bytes) -> dict[str, Any]:
    return _sealed({
        "schema_version": "1.0.0",
        "run_id": event["run_id"],
        "seq": event["seq"],
        "event_path": f"events/{event['seq']:010d}.json",
        "event_digest": event["digest"],
        "bytes_digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "bytes_length": len(data),
        "prior_digest": event["prev_digest"],
        "prior_bytes_digest": "sha256:" + hashlib.sha256(prior).hexdigest(),
        "prior_bytes_length": len(prior),
    })


def _read_pending_event(project: Path, root: Path, path: Path) -> tuple[dict[str, Any], bytes]:
    data = read_confined_bytes(project, root, path)
    if data is None:
        raise ValueError("invalid event journal")
    value = _parse_json_bytes(data, "pending event")
    if set(value) != {"schema_version", "run_id", "seq", "event_path", "event_digest", "bytes_digest", "bytes_length", "prior_digest", "prior_bytes_digest", "prior_bytes_length", "digest"} or value.get("schema_version") != "1.0.0" or not re.fullmatch(r"[0-9a-f]{32}", value.get("run_id", "")) or not isinstance(value.get("seq"), int) or value["seq"] < 1 or value.get("event_path") != f"events/{value['seq']:010d}.json" or not _DIGEST.fullmatch(value.get("event_digest", "")) or not _DIGEST.fullmatch(value.get("bytes_digest", "")) or not isinstance(value.get("bytes_length"), int) or value["bytes_length"] < 1 or value.get("prior_digest") is not None and not _DIGEST.fullmatch(value.get("prior_digest", "")) or not _DIGEST.fullmatch(value.get("prior_bytes_digest", "")) or not isinstance(value.get("prior_bytes_length"), int) or value["prior_bytes_length"] < 0 or path.name != f"{value['seq']:010d}.json":
        raise ValueError("invalid event journal")
    _check_sealed(value, "pending event")
    return value, data


def _process_scope_stop_is_proved(payload: Mapping[str, Any]) -> bool:
    rows = payload.get("process_evidence")
    proved = [
        row for row in rows
        if isinstance(row, Mapping)
        and row.get("kind") == "TIMEOUT"
        and row.get("process_scope_stopped") is True
        and row.get("stop_proof") in {
            "WINDOWS_JOB_OBJECT", "WINDOWS_TASKKILL_TREE", "POSIX_PROCESS_GROUP",
        }
    ] if isinstance(rows, list) else []
    return payload.get("verdict") == "UNKNOWN" and len(proved) == 1


def _validate_process_stopped_bindings(
    project: Path,
    root: Path,
    events: list[dict[str, Any]],
) -> None:
    for stopped in (event for event in events if event.get("event_type") == "PROCESS_STOPPED"):
        attempt_id = stopped.get("attempt_id")
        digest = stopped.get("artifact_digest")
        if not isinstance(attempt_id, str) or not isinstance(digest, str):
            raise ValueError("unproved process-stop event")
        try:
            receipt = _read_artifact(
                project, root, root / "execution-receipts" / f"{attempt_id}.json",
                "execution receipt",
            )
            payload = receipt["payload"]
            _validate_schema("run-tests-output.schema.json", payload)
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise ValueError("unproved process-stop event") from error
        relevant = [
            (index, event.get("event_type"))
            for index, event in enumerate(events)
            if event.get("attempt_id") == attempt_id
            and event.get("artifact_digest") == digest
            and event.get("event_type") in {
                "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",
                "EXECUTION_UNKNOWN", "PROCESS_STOPPED",
            }
        ]
        if (
            receipt.get("kind") != "execution-receipt"
            or receipt.get("attempt_id") != attempt_id
            or receipt.get("digest") != digest
            or not _process_scope_stop_is_proved(payload)
            or [event_type for _index, event_type in relevant] != [
                "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK",
                "EXECUTION_UNKNOWN", "PROCESS_STOPPED",
            ]
        ):
            raise ValueError("unproved process-stop event")


@_typed_corruption
def _events(project: Path, root: Path, repair: bool = False) -> list[dict[str, Any]]:
    """The checked event journal; inside closure_cache() only the lines after the checked prefix are checked.
    A record a killed writer left (``pending-events/``) is checked and finished only with ``repair`` — the writer's call, under the lock."""
    cache = _CLOSURE_CACHE.get()
    pending = root / "pending-events"
    if cache is None or pending.is_dir() and any(pending.glob("*.json")):
        return _events_full(project, root, repair)  # a pending marker is checked (and, with repair, finished) by the full path
    try:
        journal_bytes = read_confined_bytes(project, root, root / "events.jsonl")
    except OutputConfinementError:
        journal_bytes = None
    key = ("journal", str(root))
    entry = cache.get(key)
    if journal_bytes is None or not journal_bytes.endswith(b"\n") or entry is None or not journal_bytes.startswith(entry[0]):
        events = _events_full(project, root)
        if journal_bytes is not None and len(events) == journal_bytes.count(b"\n"):
            cache[key] = (journal_bytes, copy.deepcopy(events))
        return events
    prefix, known = entry
    event_dir = root / "events"
    if not event_dir.is_dir() or _is_reparse(event_dir):
        raise ValueError("invalid event journal")
    total = journal_bytes.count(b"\n")
    if sorted(event_dir.glob("*.json")) != [event_dir / f"{seq:010d}.json" for seq in range(1, total + 1)]:
        raise ValueError("invalid event journal")
    manifest, _receipt = _run_boundary(project, root)
    # Only a journal checked by its prefix needs the full check on leaving the block.
    cache.setdefault("journal_roots", {})[str(root)] = (project, root)
    result = copy.deepcopy(known)
    previous = result[-1]["digest"] if result else None
    for seq, line in enumerate(journal_bytes[len(prefix):].splitlines(keepends=True), len(result) + 1):
        result.append(_checked_event_line(project, root, manifest, seq, line, previous))
        previous = result[-1]["digest"]
    if not result or result[0].get("event_type") != "RUN_CREATED" or sum(event["event_type"] == "RUN_CREATED" for event in result) != 1:
        raise ValueError("invalid event journal")
    _validate_event_transitions(result)
    _validate_process_stopped_bindings(project, root, result)
    cache[key] = (journal_bytes, copy.deepcopy(result))
    return result


def _checked_event_line(project: Path, root: Path, manifest: Mapping[str, Any], seq: int, line: bytes, previous: str | None) -> dict[str, Any]:
    """One journal line against its copy in events/, its schema, seal, seq, run and chain link (``_events`` and ``_events_full`` read each line through it)."""
    event_bytes = read_confined_bytes(project, root, root / "events" / f"{seq:010d}.json")
    if event_bytes != line:
        raise ValueError("invalid event journal")
    event = _parse_json_bytes(line, "event journal")
    _check_sealed(event, "event")
    _validate_schema("event.schema.json", event)
    if event.get("schema_version") != "1.0.0" or event.get("seq") != seq or event.get("run_id") != manifest["run_id"] or event.get("prev_digest") != previous:
        raise ValueError("invalid event journal")
    stamp = event.get("observed_at")
    if not isinstance(stamp, str) or not stamp.endswith("Z"):
        raise ValueError("invalid event journal")
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid event journal") from error
    return event


def _remove_record(project: Path, root: Path, path: Path, data: bytes) -> bool:
    """Remove one half-written record (its marker or its copy) when its bytes are still the ones checked."""
    try:
        return remove_confined_bytes_if_equal(project, root, path, data)
    except OutputConfinementError:
        return False


@_typed_corruption
def _events_full(project: Path, root: Path, repair: bool = False) -> list[dict[str, Any]]:
    journal = root / "events.jsonl"
    try:
        journal_bytes = read_confined_bytes(project, root, journal)
    except OutputConfinementError as error:
        raise ValueError("invalid event journal") from error
    if journal_bytes is None:
        raise _InputRefusal("missing event journal")
    if not journal_bytes.endswith(b"\n") or not journal_bytes:
        raise ValueError("invalid event journal")
    lines = journal_bytes.splitlines(keepends=True)
    event_dir = root / "events"
    if not event_dir.is_dir() or _is_reparse(event_dir):
        raise ValueError("invalid event journal")
    manifest, _receipt = _run_boundary(project, root)
    pending: tuple[dict[str, Any], bytes, Path] | None = None
    pending_dir = root / "pending-events"
    if pending_dir.exists():
        if _is_reparse(pending_dir):
            raise ValueError("invalid event journal")
        markers = sorted(pending_dir.glob("*.json"))
        if len(markers) > 1:
            raise ValueError("invalid event journal")
        if markers:
            marker, marker_bytes = _read_pending_event(project, root, markers[0])
            if marker["seq"] not in {len(lines), len(lines) + 1}:
                raise ValueError("invalid event journal")
            pending = marker, marker_bytes, markers[0]
    files = sorted(event_dir.glob("*.json"))
    if files != [event_dir / f"{seq:010d}.json" for seq in range(1, len(files) + 1)]:
        raise ValueError("invalid event journal")
    expected_file_counts = {len(lines)}
    if pending is not None and pending[0]["seq"] == len(lines) + 1:
        expected_file_counts.add(len(lines) + 1)
    if len(files) not in expected_file_counts:
        raise ValueError("invalid event journal")
    previous: str | None = None
    result: list[dict[str, Any]] = []
    for seq, line in enumerate(lines, 1):
        event = _checked_event_line(project, root, manifest, seq, line, previous)
        previous = event["digest"]
        result.append(event)
    if not result or result[0].get("event_type") != "RUN_CREATED" or result[0].get("seq") != 1 or result[0].get("artifact_digest") != manifest["digest"] or "attempt_id" in result[0] or "batch_id" in result[0] or sum(event["event_type"] == "RUN_CREATED" for event in result) != 1:
        raise ValueError("invalid event journal")
    _validate_event_transitions(result)
    _validate_process_stopped_bindings(project, root, result)
    if pending is not None:
        marker, marker_bytes, marker_path = pending
        event_path = root / marker["event_path"]
        event_bytes = read_confined_bytes(project, root, event_path)
        if marker["seq"] == len(result) + 1:
            journal_digest = "sha256:" + hashlib.sha256(journal_bytes).hexdigest()
            if marker["run_id"] != manifest["run_id"] or marker["prior_digest"] != previous or marker["prior_bytes_digest"] != journal_digest or marker["prior_bytes_length"] != len(journal_bytes):
                raise ValueError("invalid event journal")
            if event_bytes is None:
                if repair and not _remove_record(project, root, marker_path, marker_bytes):
                    raise ValueError("invalid event journal")
                return result
            if marker["bytes_length"] != len(event_bytes) or marker["bytes_digest"] != "sha256:" + hashlib.sha256(event_bytes).hexdigest():
                raise ValueError("invalid event journal")
            event = _parse_json_bytes(event_bytes, "event journal")
            _check_sealed(event, "event")
            _validate_schema("event.schema.json", event)
            if event.get("digest") != marker["event_digest"] or event.get("seq") != marker["seq"] or event.get("run_id") != manifest["run_id"] or event.get("prev_digest") != previous:
                raise ValueError("invalid event journal")
            _validate_event_transitions(result + [event])
            if repair and not (_remove_record(project, root, event_path, event_bytes) and _remove_record(project, root, marker_path, marker_bytes)):
                raise ValueError("invalid event journal")
        elif marker["seq"] == len(result):
            committed = result[marker["seq"] - 1]
            committed_bytes = _canonical_bytes(committed)
            prior_bytes = b"".join(lines[:-1])
            prior_event_digest = result[-2]["digest"] if len(result) > 1 else None
            if marker["run_id"] != manifest["run_id"] or marker["prior_digest"] != prior_event_digest or marker["prior_bytes_digest"] != "sha256:" + hashlib.sha256(prior_bytes).hexdigest() or marker["prior_bytes_length"] != len(prior_bytes) or marker["event_digest"] != committed["digest"] or marker["bytes_length"] != len(committed_bytes) or marker["bytes_digest"] != "sha256:" + hashlib.sha256(committed_bytes).hexdigest() or event_bytes != committed_bytes:
                raise ValueError("invalid event journal")
            if repair and not _remove_record(project, root, marker_path, marker_bytes):
                raise ValueError("invalid event journal")
        else:
            raise ValueError("invalid event journal")
    return result


def create_run(project: Path, policy_profile: str, authorization: Mapping[str, Any]) -> Mapping[str, Any]:
    return _create_run(project, policy_profile, authorization, None)


def create_scan_run(project: Path, policy_profile: str, authorization: Mapping[str, Any], scan_request: Mapping[str, Any],
                    skipped: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    """The run of a scan — its flags as given go to the manifest (``scan_request``); lowered when ``skipped``."""
    downgrade = None if skipped is None else {"requested_policy_profile": "local-pilot-v1", **skipped}
    return _create_run(project, "cases-only-v1" if skipped is not None else policy_profile, authorization, downgrade, scan_request)


def create_lowered_run(project: Path, authorization: Mapping[str, Any], skipped: Mapping[str, str]) -> Mapping[str, Any]:
    """A local-pilot request whose build is not ready runs as cases-only-v1; the manifest keeps both profiles."""
    return _create_run(project, "cases-only-v1", authorization, {"requested_policy_profile": "local-pilot-v1", **skipped})


def _create_run(project: Path, policy_profile: str, authorization: Mapping[str, Any], downgrade: Mapping[str, Any] | None,
                scan_request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    safe_auth = _validate_authorization(policy_profile, authorization)
    root_project = _project_root(project)
    project_state = _project_state_for(policy_profile, root_project)
    runs = root_project / ".pilot-runs"
    if run_root_is_unsafe(root_project):
        raise ValueError(_UNSAFE_RUN_ROOT)
    runs_existed = runs.exists()
    run_id = uuid.uuid4().hex
    root = runs / run_id
    receipt: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    try:
        ensure_project_child_directory(root_project, runs)
        create_confined_directory_exclusive(root_project, runs, root)
        if _is_reparse(root):
            raise ValueError(_UNSAFE_RUN_ROOT)
        create_confined_directory_exclusive(root_project, root, root / "tmp")  # where --out writes; the code makes it, not the agent
        receipt_data = {"schema_version": "1.0.0", "run_id": run_id, "request_id": safe_auth["request_id"], "policy_profile": policy_profile, "execution_requested": safe_auth["execution_requested"]}
        if "host_id" in safe_auth:
            receipt_data["host_id"] = safe_auth["host_id"]
        if safe_auth.get("accept_self_review") is True:
            # the human's choice before any review, under the same digest; absent means the prior rules.
            receipt_data["accept_self_review"] = True
        if safe_auth.get("disposition_policy") == "cleanup":
            # decided once at scan; absent means quarantine and the receipt keeps its prior bytes.
            receipt_data["disposition_policy"] = "cleanup"
        receipt = _publish(root_project, root, root / "run-authorization-receipt.json", receipt_data, "authorization receipt")
        manifest_data = {"schema_version": "1.0.0", "run_id": run_id, "project": str(root_project), "policy_profile": policy_profile, "authorization_digest": receipt["digest"], "project_state": project_state}
        if downgrade is not None:
            manifest_data["requested_policy_profile"] = str(downgrade["requested_policy_profile"])
            manifest_data["execution_skipped"] = {"reason": str(downgrade["reason"]), "fix": str(downgrade["fix"])}
        if scan_request is not None:
            # the scan's own flags as given — to compare a repeated scan and to print the next scan; absent in a run of an earlier version.
            manifest_data["scan_request"] = dict(scan_request)
        manifest = _publish(root_project, root, root / "run-manifest.json", manifest_data, "run manifest")
        _append_event(root_project, root, "RUN_CREATED", actor="controller", attempt_id=None, batch_id=None, artifact_digest=manifest["digest"])
        return {"run_root": str(root), "manifest": manifest, "authorization": receipt}
    except _PublicationUnknown:
        raise
    except Exception:
        for target, artifact in ((root / "run-manifest.json", manifest), (root / "run-authorization-receipt.json", receipt)):
            if artifact is not None:
                try:
                    remove_confined_bytes_if_equal(root_project, root, target, _canonical_bytes(artifact))
                except OutputConfinementError:
                    pass
        for directory in (root / "events", root / "tmp", root):
            try:
                directory.rmdir()
            except OSError:
                pass
        if not runs_existed:
            try:
                runs.rmdir()
            except OSError:
                pass
        raise


def _attempts(project: Path, root: Path, events: list[dict[str, Any]], allow_uncommitted_id: str | None = None) -> list[dict[str, Any]]:
    directory = root / "attempts"
    if not directory.exists():
        return []
    if _is_reparse(directory):
        raise ValueError("invalid attempt path")
    creation_events = [event for event in events if event["event_type"] == "ATTEMPT_CREATED"]
    created = {event.get("attempt_id"): event for event in creation_events}
    if len(created) != len(creation_events):
        raise ValueError("invalid attempt")
    paths = list(directory.glob("*.json"))
    allowed = set(created)
    if allow_uncommitted_id is not None:
        allowed.add(allow_uncommitted_id)
    if len(paths) != len(allowed) or {path.stem for path in paths} != allowed:
        raise ValueError("invalid attempt")
    manifest, _receipt = _run_boundary(project, root)
    result: list[dict[str, Any]] = []
    for event in creation_events:
        attempt_id = event.get("attempt_id")
        path = directory / f"{attempt_id}.json"
        attempt = _read_artifact(project, root, path, "attempt")
        if event.get("artifact_digest") != attempt.get("digest"):
            raise ValueError("invalid attempt")
        _validate_attempt_record(project, manifest, attempt, path.stem, result, [item for item in events if item["seq"] < event["seq"]])
        result.append(attempt)
    return result


def _state_for(attempt_id: str, events: list[dict[str, Any]]) -> str:
    state = "ACTIVE"
    for event in events:
        if event.get("attempt_id") != attempt_id:
            continue
        if event["event_type"] == "WAITING_FOR_INPUT":
            state = "WAITING_FOR_INPUT"
        elif event["event_type"] == "WAITING_FOR_MODEL":
            state = "WAITING_FOR_MODEL"
        elif event["event_type"] == "ATTEMPT_TERMINAL":
            state = "TERMINAL"
    return state


def _recover_single_uncommitted_attempt(project: Path, root: Path, events: list[dict[str, Any]], expected: Mapping[str, Any]) -> None:
    """Remove the one sealed attempt left before its creation event could commit."""
    directory = root / "attempts"
    if not directory.exists():
        return
    if _is_reparse(directory):
        raise ValueError("invalid attempt path")
    created = {event.get("attempt_id") for event in events if event["event_type"] == "ATTEMPT_CREATED"}
    candidates = [path for path in directory.glob("*.json") if path.stem not in created]
    if not candidates:
        return
    if len(candidates) != 1:
        raise ValueError("uncommitted attempt is ambiguous")
    candidate = candidates[0]
    attempt = _read_artifact(project, root, candidate, "attempt")
    if attempt.get("attempt_id") != candidate.stem or any(attempt.get(key) != value for key, value in expected.items()) or any(key in attempt for key in {"parent_attempt_id", "retry_reason"} - set(expected)):
        raise ValueError("uncommitted attempt is invalid")
    committed = _attempts(project, root, events, allow_uncommitted_id=candidate.stem)
    if any(_state_for(item["attempt_id"], events) != "TERMINAL" for item in committed):
        raise ValueError("uncommitted attempt is invalid")
    if not committed:
        if "parent_attempt_id" in expected or "retry_reason" in expected:
            raise ValueError("uncommitted attempt is invalid")
    else:
        previous = committed[-1]
        parent_events = [event["event_type"] for event in events if event.get("attempt_id") == previous["attempt_id"]]
        if expected.get("parent_attempt_id") != previous["attempt_id"] or expected.get("retry_reason") is None or previous["module"] != expected["module"] or ("EXECUTION_UNKNOWN" in parent_events and "PROCESS_STOPPED" not in parent_events):
            raise ValueError("uncommitted attempt is invalid")
    try:
        removed = remove_confined_bytes_if_equal(project, root, candidate, _canonical_bytes(attempt))
    except OutputConfinementError as error:
        raise ValueError("uncommitted attempt is invalid") from error
    if not removed:
        raise ValueError("uncommitted attempt is invalid")


@contextmanager
def closure_cache():
    """Remember derived state and successful receipt validations for one closure."""
    if _CLOSURE_CACHE.get() is not None:
        yield
        return
    cache: dict[Any, Any] = {}
    token = _CLOSURE_CACHE.set(cache)
    try:
        yield
        # one full check of every journal the block read, so a copy changed inside the block is still caught.
        for project, root in list(cache.get("journal_roots", {}).values()):
            if (root / "events.jsonl").exists():
                _events_full(project, root)
    finally:
        _CLOSURE_CACHE.reset(token)


def closure_cached(function):
    """Run a public entry point inside one closure_cache(); nested entry points reuse the outer one."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with closure_cache():
            return function(*args, **kwargs)

    wrapper.__closure_cached__ = True
    return wrapper


def derive_state(run_root: Path) -> Mapping[str, Any]:
    cache = _CLOSURE_CACHE.get()
    if cache is None:
        return _derive_state(run_root)
    project, root = _run_root(run_root)
    try:
        journal = read_confined_bytes(project, root, root / "events.jsonl")
    except OutputConfinementError:
        journal = None
    pending = root / "pending-events"
    if journal is None or pending.is_dir() and any(pending.glob("*.json")):
        return _derive_state(run_root)  # a pending marker is checked and recovered by _events every time
    key = ("state", str(root), hashlib.sha256(journal).hexdigest())
    if key not in cache:
        cache[key] = copy.deepcopy(_derive_state(run_root))
        return copy.deepcopy(cache[key])
    state = copy.deepcopy(cache[key])
    # A reused state did not reread the event copies, so the journal gets the full check on leaving the block.
    cache.setdefault("journal_roots", {})[str(root)] = (project, root)
    # The attempt records name a module directory of the project; that check looks at the tree, so it runs again.
    for attempt in state["attempts"]:
        module = _module(project, attempt.get("module"))
        if not _module_selection_is_bound(project, state["events"], module):
            raise ValueError("module selection binding is invalid")
    return state


def _derive_state(run_root: Path) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    events = _events(project, root)
    attempts = _attempts(project, root, events)
    known = {attempt["attempt_id"] for attempt in attempts}
    if any(event["event_type"] in _ATTEMPT_EVENTS and event.get("attempt_id") not in known for event in events):
        raise ValueError("unknown attempt id in event journal")
    projected = [{**attempt, "state": _state_for(attempt["attempt_id"], events)} for attempt in attempts]
    return {"events": events, "attempts": projected}


def _model_lifecycle_projection_with_state(
    state: Mapping[str, Any],
    attempt_id: str,
) -> Mapping[str, Any]:
    if not any(item.get("attempt_id") == attempt_id for item in state["attempts"]):
        raise ValueError("unknown model lifecycle attempt")
    stages: dict[str, dict[str, Mapping[str, Any]]] = {}
    for event in state["events"]:
        if event.get("attempt_id") != attempt_id or event.get("event_type") not in _MODEL_EVENT_TYPES:
            continue
        stage = str(event["stage_instance_id"])
        by_type = stages.setdefault(stage, {})
        event_type = str(event["event_type"])
        if event_type in by_type:
            raise ValueError("ambiguous model lifecycle")
        by_type[event_type] = event
    return {"attempt_id": attempt_id, "stages": stages}


def model_lifecycle_projection(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Return model-stage facts only after the journal transition policy is proven."""
    return _model_lifecycle_projection_with_state(derive_state(run_root), attempt_id)


def model_transport_attempts_allowed(stage_instance_id: str, transport_attempts: Any) -> bool:
    """Every model stage may repeat its call once after a schema or validation error of the answer."""
    return type(transport_attempts) is int and transport_attempts in {1, 2} and _MODEL_STAGE_INSTANCE.fullmatch(str(stage_instance_id)) is not None


def _append_event(project: Path, root: Path, event_type: str, *, actor: str, attempt_id: str | None, batch_id: str | None, artifact_digest: str | None, stage_instance_id: str | None = None, transport_attempts: int | None = None) -> dict[str, Any]:
    events = _events(project, root, repair=True) if (root / "events.jsonl").exists() else []
    manifest, _receipt = _run_boundary(project, root)
    if event_type == "MODEL_RESPONSE_RECEIVED" and transport_attempts is None:
        transport_attempts = 1
    identity = (event_type, actor, attempt_id, batch_id, stage_instance_id, transport_attempts, artifact_digest)
    existing = next((event for event in events if (event["event_type"], event["actor"], event.get("attempt_id"), event.get("batch_id"), event.get("stage_instance_id"), event.get("transport_attempts"), event.get("artifact_digest")) == identity), None)
    if event_type != "RUN_CREATED" and event_type not in _EVENT_TYPES or actor not in _ACTORS or (artifact_digest is not None and not _DIGEST.fullmatch(artifact_digest)):
        raise ValueError("invalid event")
    if event_type == "TERMINAL_RETRY_OBSERVED" and artifact_digest is None:
        raise ValueError("terminal retry event requires an observation digest")
    if event_type == "RETAINED_NATIVE_RERUN" and artifact_digest is None:
        raise ValueError("retained native rerun event requires a receipt digest")
    if event_type == "COMMAND_REFUSED" and (artifact_digest is None or batch_id is not None):
        raise ValueError("refusal event requires the digest of its line and names no batch")
    if event_type == "RUN_CREATED" and events:
        raise ValueError("invalid event")
    if event_type != "RUN_CREATED" and not events:
        raise ValueError("invalid event")
    if event_type == "MODULE_SELECTED" and existing is None and any(event["event_type"] == "MODULE_SELECTED" for event in events):
        raise ValueError("module already selected")
    if event_type in _ATTEMPT_EVENTS and attempt_id is None:
        raise ValueError("attempt event requires attempt id")
    if event_type in {"RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "COMMAND_REFUSED"} and attempt_id is not None:
        raise ValueError("event must not name attempt")
    if event_type in _MODEL_EVENT_TYPES:
        if not isinstance(stage_instance_id, str) or _MODEL_STAGE_INSTANCE.fullmatch(stage_instance_id) is None:
            raise ValueError("invalid model event stage")
        if artifact_digest is None:
            raise ValueError("model event requires an artifact digest")
        if event_type == "MODEL_RESPONSE_RECEIVED":
            if not model_transport_attempts_allowed(stage_instance_id, transport_attempts):
                raise ValueError("invalid model transport attempt count")
        elif transport_attempts is not None:
            raise ValueError("transport attempt count belongs only to model response")
    elif stage_instance_id is not None:
        raise ValueError("non-model event must not name model stage")
    elif transport_attempts is not None:
        raise ValueError("non-model event must not name transport attempts")
    if batch_id is not None and not _safe_label(batch_id):
        raise ValueError("invalid event")
    if attempt_id is not None:
        if event_type == "ATTEMPT_CREATED":
            if not any(event["event_type"] == "MODULE_SELECTED" for event in events) or not any(event["event_type"] == "EXECUTION_BASELINE_FROZEN" for event in events):
                raise ValueError("execution baseline must be frozen first")
            if existing is None and any(event["event_type"] == "ATTEMPT_CREATED" and event.get("attempt_id") == attempt_id for event in events):
                raise ValueError("attempt creation binding is invalid")
            _validate_attempt_creation_candidate(project, root, manifest, events, attempt_id, artifact_digest)
        else:
            known = {item["attempt_id"] for item in _attempts(project, root, events)}
            if attempt_id not in known:
                raise ValueError("unknown attempt id")
        if event_type == "EXECUTION_STARTED" and any(event["event_type"] == "EXECUTION_STARTED" and event.get("attempt_id") == attempt_id for event in events):
            raise ValueError("execution already started")
        if event_type == "ATTEMPT_TERMINAL" and existing is None:
            if artifact_digest is None:
                raise ValueError("terminal event requires a result digest")
            _validate_terminal_transition(project, root, attempt_id, artifact_digest, events)
        if existing is not None:
            return existing
        state = _state_for(attempt_id, events)
        types = [event["event_type"] for event in events if event.get("attempt_id") == attempt_id]
        if event_type == "PROCESS_STOPPED":
            if "EXECUTION_UNKNOWN" not in types or "PROCESS_STOPPED" in types:
                raise ValueError("process-stopped recovery is invalid")
        elif state == "TERMINAL" and event_type not in {
            "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN",
        }:
            raise ValueError("terminal attempt is immutable")
    elif existing is not None:
        return existing
    value: dict[str, Any] = {"schema_version": "1.0.0", "seq": len(events) + 1, "event_type": event_type, "run_id": manifest["run_id"], "actor": actor, "observed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), "prev_digest": events[-1]["digest"] if events else None}
    if attempt_id is not None:
        value["attempt_id"] = attempt_id
    if batch_id is not None:
        value["batch_id"] = batch_id
    if stage_instance_id is not None:
        value["stage_instance_id"] = stage_instance_id
    if transport_attempts is not None:
        value["transport_attempts"] = transport_attempts
    if artifact_digest is not None:
        value["artifact_digest"] = artifact_digest
    event = _sealed(value)
    _validate_schema("event.schema.json", event)
    _validate_event_transitions(events + [event])
    data = _canonical_bytes(event)
    event_path = root / "events" / f"{event['seq']:010d}.json"
    pending_path = _pending_event_path(root, event["seq"])
    journal = root / "events.jsonl"
    previous_journal = read_confined_bytes(project, root, journal)
    prior = previous_journal or b""
    pending_data = _canonical_bytes(_pending_event_value(event, data, prior))
    pending_created = False
    counterpart_created = False
    journal_committed = False
    try:
        pending_created = bool(create_confined_bytes_exclusive(project, root, pending_path, pending_data))
        if not pending_created:
            raise OutputConfinementError("pending event already exists")
        counterpart_created = bool(create_confined_bytes_exclusive(project, root, event_path, data))
        if not counterpart_created:
            raise OutputConfinementError("event counterpart already exists")
        atomic_write_confined_bytes_at_root(project, root, journal, prior + data)
        journal_committed = read_confined_bytes(project, root, journal) == prior + data
        if not journal_committed:
            raise ValueError("event journal read-back mismatch")
        try:
            final_journal = read_confined_bytes(project, root, journal)
            final_counterpart = read_confined_bytes(project, root, event_path)
        except (OSError, OutputConfinementError, ValueError) as read_error:
            raise _PublicationUnknown("committed event read-back is unavailable") from read_error
        if final_journal != prior + data or final_counterpart != data:
            raise _PublicationUnknown("committed event counterpart is unavailable")
        if pending_created:
            try:
                removed_marker = remove_confined_bytes_if_equal(project, root, pending_path, pending_data)
            except OutputConfinementError as cleanup_error:
                raise _PublicationUnknown("committed event marker cleanup is unavailable") from cleanup_error
            if not removed_marker:
                raise _PublicationUnknown("committed event marker cleanup is unavailable")
    except _PublicationUnknown:
        raise
    except (OSError, OutputConfinementError, ValueError) as error:
        try:
            actual_journal = read_confined_bytes(project, root, journal) or b""
        except (OSError, OutputConfinementError, ValueError) as read_error:
            raise _PublicationUnknown("event publication journal is unreadable") from read_error
        if actual_journal == prior + data:
            try:
                final_counterpart = read_confined_bytes(project, root, event_path)
                final_marker = read_confined_bytes(project, root, pending_path)
            except (OSError, OutputConfinementError, ValueError) as read_error:
                raise _PublicationUnknown("committed event read-back is unavailable") from read_error
            if final_counterpart == data and final_marker in {None, pending_data}:
                return event
            raise _PublicationUnknown("committed event counterpart is unavailable") from error
        if actual_journal != prior:
            raise _PublicationUnknown("event publication state is unknown") from error
        if journal_committed:
            raise _PublicationUnknown("event publication state changed after commit") from error
        if counterpart_created:
            try:
                removed = remove_confined_bytes_if_equal(project, root, event_path, data)
            except OutputConfinementError as cleanup_error:
                raise _PublicationUnknown("event publication rollback is incomplete") from cleanup_error
            if not removed:
                raise _PublicationUnknown("event publication rollback is incomplete") from error
        if pending_created:
            try:
                removed = remove_confined_bytes_if_equal(project, root, pending_path, pending_data)
            except OutputConfinementError as cleanup_error:
                raise _PublicationUnknown("event publication rollback is incomplete") from cleanup_error
            if not removed:
                raise _PublicationUnknown("event publication rollback is incomplete") from error
        raise run_lock.JournalRefusal("PUBLICATION_UNKNOWN", "event publication failed") from error
    return event


@closure_cached
def append_event(run_root: Path, event_type: str, *, actor: str, attempt_id: str | None = None, batch_id: str | None = None, artifact_digest: str | None = None, stage_instance_id: str | None = None, transport_attempts: int | None = None) -> Mapping[str, Any]:
    if event_type == "MODEL_REQUESTED":
        raise ValueError("MODEL_REQUESTED requires publish_model_request")
    if event_type == "EXECUTION_STARTED":
        raise ValueError("EXECUTION_STARTED requires claim_execution_start")
    if event_type == "EXECUTION_UNKNOWN":
        raise ValueError("EXECUTION_UNKNOWN requires record_execution_unknown")
    if event_type == "PROCESS_STOPPED":
        raise ValueError("PROCESS_STOPPED requires verified process-scope recovery")
    if event_type == "TERMINAL_RETRY_OBSERVED":
        raise ValueError("TERMINAL_RETRY_OBSERVED requires terminal finalize retry")
    if event_type == "RETAINED_NATIVE_RERUN":
        raise ValueError("RETAINED_NATIVE_RERUN requires the retained-rerun controller")
    project, root = _run_root(run_root)
    return _append_event(project, root, event_type, actor=actor, attempt_id=attempt_id, batch_id=batch_id, artifact_digest=artifact_digest, stage_instance_id=stage_instance_id, transport_attempts=transport_attempts)


_REVIEW_STAGE = re.compile(r"^(?:tc-reviewer:(?:canonical|rework-1)|autotest-reviewer:r[12])$")
_REWORK_REVIEW_STAGE = "tc-reviewer:rework-1"


@closure_cached
def request_review(run_root: Path, attempt_id: str, stage_instance_id: str, candidate_digest: str) -> Mapping[str, Any]:
    """The one REVIEW_REQUESTED of a reviewer stage, on the exact candidate it reviews.

    tc-reviewer:canonical reviews the assembled candidate; autotest-reviewer:rN reviews the candidate of
    tc-to-autotest:rN. The same digest again returns the recorded event; another digest is refused.
    """
    if not isinstance(stage_instance_id, str) or not _REVIEW_STAGE.fullmatch(stage_instance_id):
        raise ValueError("REVIEW_STAGE_INVALID: review requests are for tc-reviewer:canonical and autotest-reviewer:r1|r2 only")
    if not isinstance(candidate_digest, str) or not _DIGEST.fullmatch(candidate_digest):
        raise ValueError("REVIEW_DIGEST_INVALID: the candidate digest must be sha256:<64 hex>")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("REVIEW_ATTEMPT_INVALID: no open attempt " + str(attempt_id))
    if attempt["policy_profile"] == "cases-only-v1" and stage_instance_id.startswith("autotest-reviewer:"):
        raise ValueError(f"MODEL_STAGE_NOT_IN_PROFILE: {stage_instance_id} is a stage of the automation, and this run is cases-only-v1; fix: прогон только кейсов — "
                         "автотесты строит новый прогон local-pilot-v1 (scan без --profile cases-only-v1) в поставке с режимом Б")
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id]
    recorded = [event for event in events if event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id]
    if recorded:
        if recorded[0].get("artifact_digest") == candidate_digest:
            return recorded[0]
        raise ValueError(f"REVIEW_ALREADY_REQUESTED: {stage_instance_id} already reviews {recorded[0].get('artifact_digest')}")
    source = ("assembly" if stage_instance_id == "tc-reviewer:canonical" else _REWORK_STAGE if stage_instance_id == _REWORK_REVIEW_STAGE
              else stage_instance_id.replace("autotest-reviewer:", "tc-to-autotest:", 1))
    candidates = [event for event in events if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == source]
    if not any(event.get("artifact_digest") == candidate_digest for event in candidates):
        published = [str(event.get("artifact_digest")) for event in candidates]
        raise ValueError(f"REVIEW_CANDIDATE_NOT_PUBLISHED: {candidate_digest} is not the published {source} candidate {published}")
    return _append_event(project, root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id, batch_id=None,
                         artifact_digest=candidate_digest, stage_instance_id=stage_instance_id)


class ModulePathError(ValueError):
    """``module`` of an attempt is not a folder of the project; ``str`` is a reason that carries nothing of the project's text."""


_MODULE_PATH_CHARS = frozenset(":") | {chr(code) for code in range(32)}


def module_path_problem(project: Path, value: Any) -> str | None:
    """Why *value* cannot be ``module`` of an attempt — the one check of ``create_attempt``, ``ready`` and the schema's
    complement.  The schema takes any path of parts without ``:`` and control characters (a Cyrillic or spaced folder is a
    folder); a part ``.`` or ``..``, an empty part, a folder that is missing or a link out of the project are refused here."""
    if not isinstance(value, str):
        return "путь модуля не строка"
    raw = value.replace("\\", "/")
    if raw == ".":
        return None
    if not raw or raw.startswith("/") or any(char in _MODULE_PATH_CHARS for char in raw.replace("/", "")) or any(part in {"", ".", ".."} for part in raw.split("/")):
        return "путь модуля должен быть относительным путём папки проекта из частей без «.», «..», «:» и управляющих символов"
    candidate = project.joinpath(*raw.split("/"))
    if not candidate.exists() or not candidate.is_dir():
        return "папки модуля нет в проекте"
    current = project
    for part in raw.split("/"):
        current /= part
        if _is_reparse(current):
            return "папка модуля — ссылка (junction или symlink)"
    try:
        candidate.resolve(strict=True).relative_to(project)
    except ValueError:
        return "папка модуля вне проекта"
    return None


def _module(project: Path, value: Any) -> str:
    problem = module_path_problem(project, value)
    if problem is not None:
        raise ModulePathError(f"module is invalid: {problem}")
    return "." if value == "." else str(value).replace("\\", "/")


def module_selection_digest(project: Path, module: str) -> str:
    """Return the deterministic receipt digest for one normalized project module."""
    root = _project_root(project)
    normalized = _module(root, module)
    return _digest({"schema_version": "1.0.0", "project": str(root), "module": normalized})


def _module_selection_is_bound(project: Path, events: list[dict[str, Any]], module: str) -> bool:
    selected = [event for event in events if event["event_type"] == "MODULE_SELECTED"]
    if len(selected) != 1:
        return False
    if selected[0].get("artifact_digest") == module_selection_digest(project, module):
        return True
    recorded = _moved_recorded(project)  # the digest binds the path where the run was made
    return recorded is not None and selected[0].get("artifact_digest") == _digest(
        {"schema_version": "1.0.0", "project": recorded, "module": _module(_project_root(project), module)})


def _validate_attempt_record(
    project: Path,
    manifest: Mapping[str, Any],
    attempt: Mapping[str, Any],
    filename: str,
    earlier: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    if attempt.get("attempt_id") != filename or attempt.get("run_id") != manifest["run_id"] or attempt.get("project") != manifest["project"] or attempt.get("policy_profile") != manifest["policy_profile"]:
        raise ValueError("invalid attempt")
    module = _module(project, attempt.get("module"))
    if not _module_selection_is_bound(project, events, module):
        raise ValueError("module selection binding is invalid")
    parent = attempt.get("parent_attempt_id")
    retry = attempt.get("retry_reason")
    if not earlier:
        if parent is not None or retry is not None:
            raise ValueError("invalid attempt")
        return
    previous = earlier[-1]
    if parent != previous["attempt_id"] or not _safe_label(retry) or previous["module"] != module:
        raise ValueError("invalid attempt")
    prior_events = [event["event_type"] for event in events if event.get("attempt_id") == parent]
    if _state_for(parent, events) != "TERMINAL" or ("EXECUTION_UNKNOWN" in prior_events and "PROCESS_STOPPED" not in prior_events):
        raise ValueError("invalid attempt")


def _validate_attempt_creation_candidate(project: Path, root: Path, manifest: Mapping[str, Any], events: list[dict[str, Any]], attempt_id: str, artifact_digest: str | None) -> dict[str, Any]:
    attempts = _attempts(project, root, events, allow_uncommitted_id=attempt_id)
    candidate = _read_artifact(project, root, root / "attempts" / f"{attempt_id}.json", "attempt")
    if candidate.get("digest") != artifact_digest:
        raise ValueError("attempt creation binding is invalid")
    if any(event.get("attempt_id") == attempt_id for event in events):
        return candidate
    _validate_attempt_record(project, manifest, candidate, attempt_id, attempts, events)
    return candidate


def create_attempt(run_root: Path, identity: Mapping[str, Any], baseline: Mapping[str, Any]) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    events = _events(project, root)
    manifest, _receipt = _run_boundary(project, root)
    if not any(event["event_type"] == "MODULE_SELECTED" for event in events):
        raise ValueError("module must be selected first")
    if set(identity) - {"project", "module", "policy_profile", "parent_attempt_id", "retry_reason"}:
        raise ValueError("baseline is invalid")
    if identity.get("project") != manifest["project"] or identity.get("policy_profile") != manifest["policy_profile"]:
        raise ValueError("attempt identity mismatch")
    module = _module(project, identity.get("module"))
    if not _module_selection_is_bound(project, events, module):
        raise ValueError("module selection binding is invalid")
    parent = identity.get("parent_attempt_id")
    retry = identity.get("retry_reason")
    if (parent is None and retry is not None) or (parent is not None and not _safe_label(retry)):
        raise ValueError("child attempt lineage is invalid")
    baseline_digest = baseline.get("digest") if isinstance(baseline, Mapping) else None
    if not isinstance(baseline_digest, str) or not _DIGEST.fullmatch(baseline_digest):
        raise ValueError("baseline is invalid")
    expected = {"run_id": manifest["run_id"], "project": manifest["project"], "module": module, "policy_profile": manifest["policy_profile"], "baseline_digest": baseline_digest}
    if parent is not None:
        expected["parent_attempt_id"] = parent
        expected["retry_reason"] = retry
    _recover_single_uncommitted_attempt(project, root, events, expected)
    state = derive_state(root)
    events = list(state["events"])
    if any(item["state"] != "TERMINAL" for item in state["attempts"]):
        raise ValueError("active attempt exists")
    if not state["attempts"]:
        if parent is not None or retry is not None:
            raise ValueError("first attempt cannot have lineage")
    else:
        previous = state["attempts"][-1]
        if parent != previous["attempt_id"] or not _safe_label(retry):
            raise ValueError("child attempt lineage is invalid")
        if previous["project"] != manifest["project"] or previous["module"] != module or previous["policy_profile"] != manifest["policy_profile"]:
            raise ValueError("child attempt identity mismatch")
        old_types = [event["event_type"] for event in events if event.get("attempt_id") == parent]
        if "EXECUTION_UNKNOWN" in old_types and "PROCESS_STOPPED" not in old_types:
            raise ValueError("process-stop evidence required")
    from tools.project_inventory import InventoryError, project_identity, read_execution_baseline, read_inventory_receipt, validate_execution_baseline_binding

    try:
        baseline_name = baseline_digest.removeprefix("sha256:") + ".json"
        frozen_baseline = read_execution_baseline(project, root, root / "baselines" / baseline_name)
        inventory_name = str(frozen_baseline["inventory_digest"]).removeprefix("sha256:") + ".json"
        frozen_inventory = read_inventory_receipt(project, root, root / "inventories" / inventory_name)
        module_root = project if module == "." else project.joinpath(*module.split("/"))
        validate_execution_baseline_binding(frozen_baseline, frozen_inventory, module_root, policy_profile=manifest["policy_profile"])
    except InventoryError as error:
        raise ValueError("execution baseline proof is invalid") from error
    if (
        dict(baseline) != frozen_baseline
        or frozen_baseline.get("module") != module
        or frozen_baseline.get("project_identity") != project_identity(project)
        or frozen_baseline.get("inventory_digest") != frozen_inventory.get("digest")
        or not any(event["event_type"] == "INVENTORY_READY" and event.get("artifact_digest") == frozen_inventory["digest"] for event in events)
        or not any(event["event_type"] == "EXECUTION_BASELINE_FROZEN" and event.get("artifact_digest") == frozen_baseline["digest"] for event in events)
    ):
        raise ValueError("execution baseline proof is invalid")
    attempt_id = uuid.uuid4().hex
    value: dict[str, Any] = {"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": attempt_id, "project": manifest["project"], "module": module, "policy_profile": manifest["policy_profile"], "baseline_digest": baseline["digest"]}
    if parent is not None:
        value["parent_attempt_id"] = parent
        value["retry_reason"] = retry
    target = root / "attempts" / f"{attempt_id}.json"
    attempt = _publish(project, root, target, value, "attempt")
    data = _canonical_bytes(attempt)
    try:
        _append_event(project, root, "ATTEMPT_CREATED", actor="controller", attempt_id=attempt_id, batch_id=None, artifact_digest=attempt["digest"])
    except _PublicationUnknown:
        raise
    except Exception:
        try:
            removed = remove_confined_bytes_if_equal(project, root, target, data)
        except OutputConfinementError as error:
            raise ValueError("attempt rollback failed") from error
        if not removed:
            raise ValueError("attempt rollback failed")
        raise
    return attempt


def freeze_phase_two_inputs(run_root: Path, project_root: Path, module_root: Path, baseline: Mapping[str, Any]) -> Mapping[str, Any]:
    """Publish and read back the inputs that an attempt may bind, before creation."""
    from tools.project_inventory import (
        InventoryError,
        baseline_external_input_paths,
        build_exclusion_receipt,
        build_inventory,
        freeze_execution_baseline,
        freeze_exclusion_receipt,
        freeze_inventory_receipt,
        validate_execution_baseline_binding,
    )

    project, root = _run_root(run_root)
    if Path(project_root).resolve() != project:
        raise ValueError("project identity mismatch")
    events = _events(project, root)
    if not any(event["event_type"] == "MODULE_SELECTED" for event in events):
        raise ValueError("module must be selected first")
    try:
        selected_module = Path(module_root).resolve(strict=True).relative_to(project).as_posix() or "."
    except (OSError, ValueError) as error:
        raise ValueError("module identity mismatch") from error
    if not _module_selection_is_bound(project, events, selected_module):
        raise ValueError("module selection binding is invalid")
    state = derive_state(root)
    if any(item["state"] != "TERMINAL" for item in state["attempts"]):
        raise ValueError("phase two inputs cannot change during an active attempt")
    try:
        # The run root is pipeline-owned generated state, never a project input.
        inventory = build_inventory(
            project,
            module_root,
            skill_pack_root=_PACK_ROOT,
            generated_roots=(root.parent,),
            proved_dependency_files=baseline_external_input_paths(project, baseline),
        )
        if baseline.get("inventory_digest") != inventory["digest"]:
            raise ValueError("execution baseline does not bind the current inventory")
        validate_execution_baseline_binding(baseline, inventory, module_root, policy_profile=read_run(root)["manifest"]["policy_profile"])
        inventory_target = root / "inventories" / (inventory["digest"].removeprefix("sha256:") + ".json")
        exclusion = build_exclusion_receipt(inventory)
        exclusion_target = root / "exclusions" / (exclusion["digest"].removeprefix("sha256:") + ".json")
        baseline_target = root / "baselines" / (str(baseline["digest"]).removeprefix("sha256:") + ".json")
        # A receipt that lies there already is taken only with the bytes of the fresh one (an equal file is no news, any other is refused).
        frozen_inventory = freeze_inventory_receipt(project, root, inventory_target, inventory)
        frozen_exclusions = freeze_exclusion_receipt(project, root, exclusion_target, exclusion)
        frozen_baseline = freeze_execution_baseline(project, root, baseline_target, baseline)
    except InventoryError as error:
        raise ValueError("phase two input receipt is invalid") from error
    append_event(root, "INVENTORY_READY", actor="controller", artifact_digest=frozen_inventory["digest"])
    append_event(root, "SNAPSHOT_BOUND", actor="controller", artifact_digest=frozen_baseline["digest"])
    append_event(root, "EXECUTION_BASELINE_FROZEN", actor="controller", artifact_digest=frozen_baseline["digest"])
    return {"inventory": frozen_inventory, "exclusions": frozen_exclusions, "baseline": frozen_baseline}


def _frozen_inventory(project: Path, root: Path, inventory_digest: str) -> dict[str, Any]:
    if not _DIGEST.fullmatch(inventory_digest):
        raise ValueError("invalid frozen inventory digest")
    receipt = read_inventory_receipt(project, root, root / "inventories" / (inventory_digest.removeprefix("sha256:") + ".json"))
    events = _events(project, root)
    if receipt.get("digest") != inventory_digest or not any(event["event_type"] == "INVENTORY_READY" and event.get("artifact_digest") == inventory_digest for event in events):
        raise ValueError("unbound frozen inventory")
    return receipt


class BaselineUnbound(ValueError):
    """The baseline file under the attempt's name is not the baseline the attempt froze."""

    code = "BASELINE_UNBOUND"


def frozen_baseline(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any]:
    """The execution baseline of *attempt* — the file named by ``attempt.baseline_digest``, whose own self-digest is that digest
    and which the journal froze (``EXECUTION_BASELINE_FROZEN`` on that digest).  Like ``_frozen_inventory``: a file that is only
    called by the right name, a consistent forged pair of baseline and inventory among them, is BASELINE_UNBOUND, not a baseline."""
    from tools.project_inventory import read_execution_baseline

    project, root = _run_root(run_root)
    digest = str(attempt.get("baseline_digest") or "")
    fix = "fix: начни новый прогон тем же scan с --new-run (baseline этого прогона не тот, что записан в попытке)"
    if not _DIGEST.fullmatch(digest):
        raise BaselineUnbound(f"BASELINE_UNBOUND: у попытки нет digest baseline; {fix}")
    try:
        baseline = read_execution_baseline(project, root, root / "baselines" / (digest.removeprefix("sha256:") + ".json"))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BaselineUnbound(f"BASELINE_UNBOUND: baseline {digest} не читается ({type(error).__name__}); {fix}") from error
    frozen = any(event["event_type"] == "EXECUTION_BASELINE_FROZEN" and event.get("artifact_digest") == digest for event in derive_state(root)["events"])
    if baseline.get("digest") != digest or not frozen:
        raise BaselineUnbound(f"BASELINE_UNBOUND: файл baseline {digest} {'не заморожен журналом' if not frozen else 'не совпадает с собственным digest'}; {fix}")
    return baseline


def frozen_skillsrc_matches(project: Path, run_root: Path, attempt: Mapping[str, Any]) -> bool | None:
    """Do the bytes of the project's ``.skillsrc`` equal what the attempt's baseline froze (``skillsrc_authority``)?  The one path
    for every reader of the file after the scan.  None when the journal-bound baseline or the file cannot say (no file, no baseline, unbound)."""
    try:
        frozen = frozen_baseline(run_root, attempt)["skillsrc_authority"]["skillsrc_digest"]
        return "sha256:" + hashlib.sha256((Path(project) / ".skillsrc").read_bytes()).hexdigest() == frozen
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _check_phase1_artifact(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    if not common <= set(receipt) or not identity_matches:
        raise ValueError("invalid factual receipt")
    if set(receipt) != common | {"module_selection_digest", "baseline_digest"} or not isinstance(receipt.get("module_selection_digest"), str) or not isinstance(receipt.get("baseline_digest"), str) or receipt["module_selection_digest"] != module_selection_digest(project, attempt["module"]) or receipt["baseline_digest"] != attempt["baseline_digest"]:
        raise ValueError("invalid phase1 artifact")


def _closure_target(root: Path, attempt_id: str, kind: str, digest: str) -> Path:
    if kind not in _CLOSURE_KINDS or not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or not _DIGEST.fullmatch(digest):
        raise ValueError("invalid closure artifact identity")
    return root / "closure" / attempt_id / f"{kind}.json"


def _validate_execution_event_branch(
    events: Sequence[Mapping[str, Any]], attempt_id: str, verification: str, execution_receipt_digest: str | None,
) -> None:
    attempt_events = [event for event in events if event.get("attempt_id") == attempt_id]
    starts = [event for event in attempt_events if event.get("event_type") == "EXECUTION_STARTED"]
    unknowns = [event for event in attempt_events if event.get("event_type") == "EXECUTION_UNKNOWN"]
    if verification in {"NOT_APPLICABLE", "NOT_RUNNABLE"}:
        if starts or unknowns:
            raise ValueError("pre-execution branch cannot follow EXECUTION_STARTED")
        return
    if verification not in {"PASS", "FAIL", "UNKNOWN"} or len(starts) != 1:
        raise ValueError("execution branch requires one EXECUTION_STARTED")
    if verification != "UNKNOWN":
        if unknowns:
            raise ValueError("non-UNKNOWN branch has execution-unknown evidence")
        return
    if (
        not isinstance(execution_receipt_digest, str)
        or len(unknowns) != 1
        or unknowns[0].get("artifact_digest") != execution_receipt_digest
    ):
        raise ValueError("UNKNOWN branch lacks exact execution-unknown evidence")
    readbacks = [
        index for index, event in enumerate(events)
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") == "ARTIFACT_READ_BACK"
        and event.get("artifact_digest") == execution_receipt_digest
    ]
    if len(readbacks) != 1 or readbacks[0] >= events.index(unknowns[0]):
        raise ValueError("execution-unknown event order is invalid")


def read_closure_artifact(
    run_root: Path,
    attempt_id: str,
    kind: str,
    digest: str,
) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    target = _closure_target(root, attempt_id, kind, digest)
    value = _read_artifact(project, root, target, kind.replace("_", " "))
    state = derive_state(root)
    events = [item for item in state["events"] if item.get("attempt_id") == attempt_id and item.get("artifact_digest") == digest]
    if value.get("digest") != digest or not any(item["event_type"] == "ARTIFACT_PUBLISHED" for item in events) or not any(item["event_type"] == "ARTIFACT_READ_BACK" for item in events):
        raise ValueError("closure artifact binding is invalid")
    return value


def _validate_terminal_transition(
    project: Path,
    root: Path,
    attempt_id: str,
    result_digest: str,
    events: Sequence[Mapping[str, Any]],
) -> None:
    """Require the completed/read-back closure chain before terminal state."""
    result = _read_artifact(
        project, root, root / "terminal-results" / f"{attempt_id}.json", "terminal result"
    )
    result_events = [
        event["event_type"] for event in events
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == result_digest
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if result.get("digest") != result_digest or result_events != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        raise ValueError("terminal result has not completed durable read-back")
    terminal_trace = _read_artifact(
        project, root, root / "closure" / attempt_id / "terminal_trace.json", "terminal trace"
    )
    trace_digest = terminal_trace.get("digest")
    trace_events = [
        event["event_type"] for event in events
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == trace_digest
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if terminal_trace.get("terminal_result_digest") != result_digest or trace_events != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        raise ValueError("terminal trace has not completed durable read-back")
    finalization_digest = terminal_trace.get("finalization_receipt_digest")
    pre_digest = terminal_trace.get("pre_finalization_trace_digest")
    finalization = read_closure_artifact(root, attempt_id, "finalization_receipt", str(finalization_digest))
    pre_trace = read_closure_artifact(root, attempt_id, "pre_finalization_trace", str(pre_digest))
    if (
        finalization.get("pre_finalization_trace_digest") != pre_digest
        or result.get("finalization_valid") is not bool(finalization.get("valid"))
        or any(
            item.get(key) != result.get(key)
            for item in (terminal_trace, finalization, pre_trace)
            for key in ("run_id", "attempt_id", "policy_profile")
        )
    ):
        raise ValueError("terminal closure identities do not bind")
    execution = pre_trace.get("execution")
    execution_digest = execution.get("execution_receipt_digest") if isinstance(execution, Mapping) else None
    if not isinstance(execution, Mapping) or execution.get("verification") != result.get("verification"):
        raise ValueError("terminal execution branch does not bind")
    _validate_execution_event_branch(
        events, attempt_id, str(result.get("verification")),
        execution_digest if isinstance(execution_digest, str) else None,
    )


# public names of the same objects: what ``pilot_state`` reads and serves (П1 of the cut of v0.8)
CLOSURE_KINDS = _CLOSURE_KINDS
MODEL_STAGE_INSTANCE = _MODEL_STAGE_INSTANCE
REWORK_REVIEW_STAGE = _REWORK_REVIEW_STAGE
REWORK_STAGE = _REWORK_STAGE
append_journal_event = _append_event
check_phase1_artifact = _check_phase1_artifact
closure_target = _closure_target
frozen_inventory = _frozen_inventory
model_lifecycle_projection_with_state = _model_lifecycle_projection_with_state
validate_execution_event_branch = _validate_execution_event_branch
