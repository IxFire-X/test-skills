"""Durable, append-only Phase 1 controller state with safe local receipts."""
from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
import os
import re
import sys
import types
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from tools.schema_engine import Draft202012Validator, FormatChecker

from tools import execution_gate, run_lock
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, create_confined_bytes_exclusive, create_confined_directory_exclusive, ensure_project_child_directory, read_confined_bytes, remove_confined_bytes_if_equal


_PROFILES = {"cases-only-v1", "local-pilot-v1"}
_AUTH_KEYS = {"request_id", "execution_requested", "host_id", "accept_self_review", "disposition_policy"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_MIXED_TOKEN = re.compile(r"^(?=.{24,}$)(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])[A-Za-z0-9_-]+$")
_MODEL_EVENT_TYPES = {"MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED"}
_MODEL_STAGE_INSTANCE = re.compile(
    r"^(?:context-marker:(?:baseline|requirements|batch-[0-9]{3})|tc-generator:(?:BATCH-[a-z0-9-]+|rework-1)|assembly|tc-reviewer:(?:canonical|rework-1)|tc-to-autotest:r[12]|autotest-reviewer:r[12])$"
)
REWORK_BATCH = "rework-round-v1"  # the mark of the events of the receipt ``rework-round``: the first review sent cases back and the code waits for the rework
_REWORK_STAGE = "tc-generator:rework-1"
_MODEL_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_MODEL_STAGE_REGISTRY = {
    "context-marker": ("generator", "context-marker-v1"),
    "tc-generator": ("generator", "tc-generator-v1"),
    "tc-reviewer": ("canonical-reviewer", "canonical-reviewer-v1"),
    "tc-to-autotest": ("automation-generator", "tc-to-autotest-v1"),
    "autotest-reviewer": ("automation-reviewer", "autotest-static-reviewer-v1"),
}
_EVENT_TYPES = {"MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "CONTEXT_SELECTED", "ATTEMPT_CREATED", *_MODEL_EVENT_TYPES, "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN", "COMMAND_REFUSED"}
_ACTORS = {"controller"}
_ATTEMPT_EVENTS = {"ATTEMPT_CREATED", "CONTEXT_SELECTED", *_MODEL_EVENT_TYPES, "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN"}
_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"
_PACK_ROOT = _SCHEMA_ROOT.parent
_CLOSURE_KINDS = {"pre_finalization_trace", "finalization_receipt", "terminal_trace"}
_DEDICATED_RECEIPT_TOKEN = object()
class _PublicationUnknown(run_lock.JournalRefusal):
    """A journal writer failed after an unclassifiable durable-state transition."""

    def __init__(self, message: str) -> None:
        super().__init__("PUBLICATION_UNKNOWN", message)


class _InputRefusal(ValueError):
    """An input error met while the journal is read — no journal, or a run of another project: never a damaged journal."""

    code = "INPUT_INVALID"


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


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _parse_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} has duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid {label}") from error
    if not isinstance(value, dict) or _canonical_bytes(value) != data:
        raise ValueError(f"invalid {label}")
    return value


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["digest"] = _digest(result)
    return result


def _check_sealed(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    parsed = dict(value)
    digest = parsed.pop("digest", None)
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or _digest(parsed) != digest:
        raise ValueError(f"invalid {label} digest")
    return dict(value)


@lru_cache(maxsize=64)
def _artifact_validator(schema_text: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_text), format_checker=FormatChecker())


@lru_cache(maxsize=64)
def _artifact_schema_valid(schema_text: str, serialized: str) -> bool:
    return _artifact_validator(schema_text).is_valid(json.loads(serialized))


def _validate_schema(name: str, value: Mapping[str, Any]) -> None:
    from tools.schema_validation import _json_validation_key

    schema_text = (_SCHEMA_ROOT / name).read_text(encoding="utf-8")
    serialized = _json_validation_key(value)
    # inside one closure a successful check of the same bytes is remembered by digest; the global lru cache
    # (64 entries) drops large artifacts such as an inventory, whose uniqueItems check is quadratic.
    cache = _CLOSURE_CACHE.get()
    key = None
    if cache is not None:
        try:
            key = ("schema", name, hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                                             allow_nan=False).encode("utf-8")).hexdigest())
        except (TypeError, ValueError):
            key = None
    if key is not None and key in cache:
        return
    valid = _artifact_schema_valid(schema_text, serialized) if serialized is not None else _artifact_validator(schema_text).is_valid(value)
    if not valid:
        raise ValueError(f"schema validation failed: {name}")
    if key is not None:
        cache[key] = True


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)


def run_root_is_unsafe(project: Path) -> bool:
    """``.pilot-runs`` of the project is a link (a junction, a symlink): no run is written through it."""
    runs = Path(project) / ".pilot-runs"
    return runs.exists() and _is_reparse(runs)


_UNSAFE_RUN_ROOT = "RUN_ROOT_UNSAFE: unsafe run root: .pilot-runs (or the run folder in it) is a link"


def _project_root(project: Path) -> Path:
    raw = Path(project)
    if not raw.exists() or not raw.is_dir() or _is_reparse(raw):
        raise ValueError("project root must be an existing regular directory")
    resolved = raw.resolve(strict=True)
    if _is_reparse(resolved):
        raise ValueError("project root must not be a reparse point")
    return resolved


def _run_root(run_root: Path) -> tuple[Path, Path]:
    root = Path(run_root)
    if not root.exists() or not root.is_dir() or _is_reparse(root) or root.parent.name != ".pilot-runs":
        raise ValueError("invalid run root")
    project = _project_root(root.parent.parent)
    resolved = root.resolve(strict=True)
    try:
        resolved.relative_to(project / ".pilot-runs")
    except ValueError as error:
        raise ValueError("invalid run root") from error
    run_lock.hold(project, resolved)  # the one door to a run: every command that works with it holds its lock
    return project, resolved


def _publish(project: Path, root: Path, target: Path, value: Mapping[str, Any], label: str, *, return_created: bool = False) -> Any:
    sealed = _sealed(value)
    data = _canonical_bytes(sealed)
    schema_names = {
        "run manifest": "run-manifest.schema.json",
        "authorization receipt": "run-authorization-receipt.schema.json",
        "attempt": "attempt.schema.json",
        "terminal result": "terminal-result.schema.json",
        "scenario observation": "scenario-observation-receipt.schema.json",
        "retained native rerun": "retained-native-rerun-receipt.schema.json",
        "disposition receipt": "disposition-receipt.schema.json",
        "finalization receipt": "finalization-receipt.schema.json",
        "pre finalization trace": "pre-finalization-trace.schema.json",
        "terminal trace": "derived-terminal-trace.schema.json",
    }
    if label in schema_names:
        _validate_schema(schema_names[label], sealed)
    created = False
    installed_identity: tuple[int, int, int | None] | None = None
    try:
        created, installed_identity = create_confined_bytes_exclusive(project, root, target, data, return_identity=True)
        readback = read_confined_bytes(project, root, target)
        if readback != data:
            raise ValueError(f"{label} read-back mismatch")
        parsed = _parse_json_bytes(readback, label)
        _check_sealed(parsed, label)
        if label in schema_names:
            _validate_schema(schema_names[label], parsed)
        if label == "terminal result" and not _validate_result_record(parsed):
            raise ValueError("terminal result is invalid")
    except (OutputConfinementError, ValueError) as error:
        if created:
            if installed_identity is None:
                raise ValueError(f"{label} controller rollback error") from error
            try:
                removed = remove_confined_bytes_if_equal(project, root, target, data, expected_identity=installed_identity)
            except OutputConfinementError as cleanup_error:
                raise ValueError(f"{label} controller rollback error") from cleanup_error
            if not removed:
                raise ValueError(f"{label} controller rollback error") from error
        if isinstance(error, OutputConfinementError):
            raise ValueError(f"unsafe {label} path") from error
        raise
    return (parsed, created, installed_identity) if return_created else parsed


def _read_artifact(project: Path, root: Path, target: Path, label: str) -> dict[str, Any]:
    try:
        data = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError(f"unsafe {label} path") from error
    if data is None:
        raise ValueError(f"missing {label}")
    value = _parse_json_bytes(data, label)
    _check_sealed(value, label)
    schema_names = {
        "run manifest": "run-manifest.schema.json",
        "authorization receipt": "run-authorization-receipt.schema.json",
        "attempt": "attempt.schema.json",
        "scenario observation": "scenario-observation-receipt.schema.json",
        "retained native rerun": "retained-native-rerun-receipt.schema.json",
    }
    if label in schema_names:
        _validate_schema(schema_names[label], value)
    return value


def _safe_label(value: Any) -> bool:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in ("token", "secret", "credential", "password", "bearer", "prompt", "api_key")):
        return False
    if lowered.startswith(("ghp_", "github_pat_", "sk-")):
        return False
    return not _JWT.fullmatch(value) and not _MIXED_TOKEN.fullmatch(value)


def _credential_like(value: str) -> bool:
    from tools.project_inventory import token_signature_rule

    lowered = value.lower()
    markers = (
        "token", "secret", "credential", "password", "bearer", "prompt",
        "api_key", "api-key", "apikey", "access_key", "access-key", "accesskey",
        "private_key", "private-key", "privatekey", "client_secret", "client-secret",
        "session_token", "session-token",
    )
    prefixes = (
        "ghp_", "github_pat_", "glpat-", "glpat_", "sk-", "sk_live_", "rk_live_",
        "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-", "hf_", "npm_", "pypi-",
    )
    return (
        any(marker in lowered for marker in markers)
        or lowered.startswith(prefixes)
        or token_signature_rule(value.encode("utf-8", errors="replace")) is not None
        or _JWT.fullmatch(value) is not None
        or _MIXED_TOKEN.fullmatch(value) is not None
    )


def _safe_model_label(value: Any) -> bool:
    if not isinstance(value, str) or _MODEL_LABEL.fullmatch(value) is None:
        return False
    return not _credential_like(value)


def _validate_authorization(policy_profile: str, authorization: Mapping[str, Any]) -> dict[str, Any]:
    if policy_profile not in _PROFILES or set(authorization) - _AUTH_KEYS or set(authorization) < {"request_id", "execution_requested"}:
        raise ValueError("unsafe authorization")
    if not _safe_label(authorization["request_id"]) or not isinstance(authorization["execution_requested"], bool):
        raise ValueError("unsafe authorization")
    if "host_id" in authorization and not _safe_label(authorization["host_id"]):
        raise ValueError("unsafe authorization")
    if "accept_self_review" in authorization and authorization["accept_self_review"] is not True:
        raise ValueError("unsafe authorization")
    if "disposition_policy" in authorization and authorization["disposition_policy"] != "cleanup":
        raise ValueError("unsafe authorization")
    requested = authorization["execution_requested"]
    if (policy_profile == "cases-only-v1" and requested) or (policy_profile == "local-pilot-v1" and not requested):
        raise ValueError("authorization policy mismatch")
    return dict(authorization)


# status and report read a run of a project that was moved or renamed.  The manifest keeps the absolute path of the folder
# where the run was made; inside this block a different path is accepted when the files of the frozen inventories are all there.
_MOVED_PROJECT: contextvars.ContextVar[dict[Any, Any] | None] = contextvars.ContextVar("pilot_moved_project", default=None)


@contextmanager
def moved_project_allowed():
    token = _MOVED_PROJECT.set({})
    try:
        yield
    finally:
        _MOVED_PROJECT.reset(token)


def run_project_move(project: Path, run_root: Path) -> dict[str, Any] | None:
    """``None`` when the run was made at ``project``; else ``{"recorded", "current", "same_structure", "missing"}``.

    The structure is the same when the run has frozen inventories, they read back whole (their own digest), and every file
    they list is in ``project`` at the same relative path.  Contents are not compared here: a changed file is a drift,
    which status reports as before.  Only reads."""
    from tools.project_inventory import InventoryError, read_execution_baseline, read_inventory_receipt

    project, root = Path(project).resolve(), Path(run_root)
    try:  # the sealed manifest (the boundary itself asks here, so not read_run)
        recorded = _read_artifact(project, root, root / "run-manifest.json", "run manifest").get("project")
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return None
    if not isinstance(recorded, str) or recorded == str(project):
        return None
    missing: list[str] = []
    # the inventories the attempts froze: attempt (sealed) -> its baseline (sealed, the digest the attempt names) -> the inventory digest the baseline binds;
    # the journal cannot be read here (it asks the boundary, which asks this), so an inventory lying in the folder that no baseline names is not one
    paths: list[Path] = []
    same = True
    for attempt_path in sorted((root / "attempts").glob("*.json")) if (root / "attempts").is_dir() else []:
        try:
            attempt = _read_artifact(project, root, attempt_path, "attempt")
            baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
            if baseline["digest"] != attempt["baseline_digest"]:
                raise ValueError("baseline digest mismatch")
            paths.append(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
        except (OSError, KeyError, TypeError, ValueError):
            same = False
    same = same and bool(paths)
    for path in paths:
        try:
            files = [str(row["project_path"]) for row in read_inventory_receipt(project, root, path)["files"]]
        except (InventoryError, OSError, KeyError, TypeError, ValueError):
            same = False
            continue
        for rel in files:
            if rel not in missing and (Path(rel).is_absolute() or ".." in Path(rel).parts or not (project / rel).is_file()):
                missing.append(rel)
    return {"recorded": recorded, "current": str(project), "same_structure": same and not missing, "missing": sorted(missing)}


def _moved_project_ok(project: Path, root: Path) -> bool:
    cache = _MOVED_PROJECT.get()
    if cache is None:
        return False
    key = (str(project), str(root))
    if key not in cache:
        move = run_project_move(project, root)
        cache[key] = bool(move and move["same_structure"])
        if cache[key]:
            cache[("recorded", str(project))] = move["recorded"]
    return cache[key]


def _moved_recorded(project: Path) -> str | None:
    """The project path the run was made at, inside moved_project_allowed() once the run was found to be a moved project's."""
    cache = _MOVED_PROJECT.get()
    return None if cache is None else cache.get(("recorded", str(project)))


def _moved_refusal(project: Path, root: Path) -> ValueError | None:
    """The refusal of a command that reads a run made in another folder — ``PROJECT_MOVED`` with where the run was made and what to do — or None
    when the run is not a moved project's (``status`` and ``report`` say the same in their ``project_moved`` note)."""
    move = run_project_move(project, root)
    if move is None:
        return None
    where = f"прогон создан в {move['recorded']}, сейчас проект в {move['current']}"
    if move["same_structure"]:
        fix = ("команды tools.api, finish, materialize и ingest работают только там, где создан прогон: откройте ту папку; status и report читают прогон и здесь; "
               "чтобы работать в этой папке, начните новый scan")
    else:
        shown = ", ".join(move["missing"][:5]) + (" и др." if len(move["missing"]) > 5 else "")
        fix = f"состав файлов другой (нет: {shown or 'опись прогона не читается'}): откройте папку, где создан прогон, или начните новый scan здесь"
    return ValueError(f"PROJECT_MOVED: проект перенесён: {where}; fix: {fix}")


def _run_boundary(project: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_artifact(project, root, root / "run-manifest.json", "run manifest")
    receipt = _read_artifact(project, root, root / "run-authorization-receipt.json", "authorization receipt")
    if manifest.get("run_id") == root.name and manifest.get("project") != str(project) and not _moved_project_ok(project, root):
        refusal = _moved_refusal(project, root)
        if refusal is not None:
            raise refusal
    if manifest.get("run_id") != root.name or (manifest.get("project") != str(project) and not _moved_project_ok(project, root)):
        raise _InputRefusal("invalid run boundary")
    if receipt.get("run_id") != manifest["run_id"] or receipt.get("policy_profile") != manifest.get("policy_profile") or receipt.get("digest") != manifest.get("authorization_digest"):
        raise ValueError("invalid run boundary")
    auth = {key: receipt[key] for key in _AUTH_KEYS if key in receipt}
    if _validate_authorization(receipt["policy_profile"], auth) != auth:
        raise ValueError("invalid run boundary")
    return manifest, receipt


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
    rework_marked: set[str] = set()  # the attempts whose first review sent cases back (V7: the receipt ``rework-round``)
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
    """One journal line against its copy in events/, its schema, seal, seq, run and chain link (as in _events_full)."""
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
        path = event_dir / f"{seq:010d}.json"
        event_bytes = read_confined_bytes(project, root, path)
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
        create_confined_directory_exclusive(root_project, root, root / "tmp")  # PT2: where --out writes; the code makes it, not the agent
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


# inside one closure the same bytes are derived and validated once.  Keys are content digests — the
# journal for the state, the receipt bytes and the journal's last digest for a validation — so every write of
# the closure itself changes the key.  Every file is still read through read_confined_bytes each time; only the
# successful validation is remembered, never an error.  Outside closure_cache() nothing is cached.
_CLOSURE_CACHE: contextvars.ContextVar[dict[Any, Any] | None] = contextvars.ContextVar("pilot_closure_cache", default=None)
# These validations look at the project tree (module directory, runtime paths), not only at the run directory —
# directly (phase1-artifact, execution-receipt) or through a nested read of execution-receipt (resume-validation,
# execution-trace, trace-audit).  Nothing else reads one of these five, so the set is closed.
_UNCACHED_RECEIPT_KINDS = frozenset({"phase1-artifact", "execution-receipt", "resume-validation", "execution-trace", "trace-audit"})


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
    from tools.project_inventory import read_inventory_receipt

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


def _context_selection_target(root: Path, attempt_id: str, receipt_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or not _DIGEST.fullmatch(receipt_digest):
        raise ValueError("invalid context selection identity")
    return root / "context-selections" / attempt_id / (receipt_digest.removeprefix("sha256:") + ".json")


def _model_stage_artifact_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("invalid model stage artifact") from error


def _model_stage_artifact_digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_model_stage_artifact_bytes(value)).hexdigest()


def _model_request_target(root: Path, attempt_id: str, request_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or not _DIGEST.fullmatch(request_digest):
        raise ValueError("invalid model request identity")
    return root / "model-requests" / attempt_id / (
        request_digest.removeprefix("sha256:") + ".json"
    )


def _context_marker_parts(state: Mapping[str, Any], attempt_id: str) -> tuple[tuple[str, str | None] | None, list[tuple[str, str, str | None]]]:
    """``(requirements (request, response) or None, [(batch stage, request, response)…] in batch order)``."""
    requests: dict[str, str] = {}
    responses: dict[str, str] = {}
    for event in state["events"]:
        stage = str(event.get("stage_instance_id") or "")
        if event.get("attempt_id") != attempt_id or not stage.startswith("context-marker:") or stage == "context-marker:baseline":
            continue
        if event["event_type"] == "MODEL_REQUESTED":
            requests[stage] = str(event.get("artifact_digest"))
        elif event["event_type"] == "MODEL_RESPONSE_RECEIVED":
            responses[stage] = str(event.get("artifact_digest"))
    requirements = requests.get("context-marker:requirements")
    batches = [(stage, requests[stage], responses.get(stage)) for stage in sorted(requests) if stage.startswith("context-marker:batch-")]
    return (None if requirements is None else (requirements, responses.get("context-marker:requirements"))), batches


def _published_context_receipts(state: Mapping[str, Any], attempt_id: str, stage_instance_id: str) -> list[str]:
    """Every context receipt the attempt published, once each, in publication order: batch-NNN is the N-th of them.

    A recorded request binds the receipts published before it; reviewer evidence published later is not its input."""
    cutoff = next((event["seq"] for event in state["events"] if event.get("attempt_id") == attempt_id
                   and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage_instance_id), None)
    return list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                              if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"
                              and (cutoff is None or event["seq"] < cutoff)))


def _context_marker_receipt_digests(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str, inputs: Sequence[str],
) -> list[str]:
    """The context receipts a marker request binds after requirements and inventory.

    The requirements step binds none (documents only).  A part binds exactly one receipt and then the requirements
    answer.  The merged ``context-marker:baseline`` of a marker in parts binds every part's receipt in part order, the
    requirements answer and every part answer in the same order (an identical answer once); a single marker binds the
    receipts only, as before.
    """
    requirements, batches = _context_marker_parts(state, attempt_id)
    published = _published_context_receipts(state, attempt_id, stage_instance_id)
    if stage_instance_id == "context-marker:requirements":
        if len(inputs) != 2:
            raise ValueError("context-marker requirements step binds the requirements and the inventory only")
        return []
    if stage_instance_id.startswith("context-marker:batch-"):
        # [inventory, receipt, requirements answer]; the runs of an earlier version also bound the documents digest first
        if len(inputs) not in (3, 4) or requirements is None or requirements[1] is None or inputs[-1] != requirements[1]:
            raise ValueError("context-marker part must bind one context receipt and the requirements answer")
        number = int(stage_instance_id.rsplit("-", 1)[1])
        if not 1 <= number <= len(published) or inputs[-2] != published[number - 1]:
            raise ValueError(f"{stage_instance_id} must bind context receipt {number} of the attempt (batch-NNN is the N-th published receipt)")
        return [inputs[-2]]
    if not batches:
        if list(inputs[2:]) != published:
            raise ValueError("context-marker:baseline must bind every context receipt of the attempt, once, in publication order")
        return list(inputs[2:])
    if requirements is None or requirements[1] is None or any(response is None for _stage, _request, response in batches):
        raise ValueError("merged context-marker request requires every part answer")
    receipts = [_read_model_request_with_state(project, root, state, attempt_id, stage, request)["input_digests"][-2] for stage, request, _response in batches]
    if receipts != published:
        missing = [f"batch-{index:03d}" for index, digest in enumerate(published, start=1) if digest not in receipts]
        raise ValueError(f"CONTEXT_MARKER_PARTS_INCOMPLETE: the parts must cover every context receipt of the attempt once, in order; missing {missing}")
    if list(inputs[2:]) != [*receipts, requirements[1], *dict.fromkeys(str(response) for _stage, _request, response in batches)]:
        raise ValueError("merged context-marker request must bind every part receipt, the requirements answer and every part answer in order")
    return receipts


def _context_marker_snapshots(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage_instance_id: str, inputs: Sequence[str],
    receipts: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """The OpenSpec sources of the receipts the request binds (or of ``receipts``), each checked against its bytes."""
    from tools.build_context import _OPENSPEC_PATH
    from tools.project_inventory import read_execution_baseline

    baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    minimum = 2 if stage_instance_id == "context-marker:requirements" else 3
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    if stage_instance_id.startswith("context-marker:batch-") and len(inputs) == 3:
        head = head[1:]  # a part binds the inventory, not the documents again (the answer of the requirements step does)
    if baseline["digest"] != attempt["baseline_digest"] or list(inputs[:len(head)]) != head or len(inputs) < minimum:
        raise ValueError("context-marker inputs must bind baseline requirements, inventory, and authorized context receipts")
    snapshots: dict[str, dict[str, str]] = {}
    if receipts is None:
        receipts = _context_marker_receipt_digests(project, root, state, str(attempt["attempt_id"]), stage_instance_id, inputs)
    for digest in receipts:
        receipt = read_context_selection(root, str(attempt["attempt_id"]), digest)
        if receipt["inventory_digest"] != baseline["inventory_digest"]:
            raise ValueError("context-marker receipt must bind the attempt inventory")
        for item in receipt["files"]:
            path = item["project_path"].replace("\\", "/")
            if not _OPENSPEC_PATH.fullmatch(path) and not path.startswith("openspec/changes/archive/"):
                continue
            raw = read_confined_bytes(project, project / "openspec", project / path)  # the project itself is no confined root
            if raw is None or "sha256:" + hashlib.sha256(raw).hexdigest() != item["content_digest"]:
                raise ValueError("context-marker OpenSpec source bytes drifted")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("context-marker OpenSpec source is not UTF-8") from error
            snapshots[path] = {"path": path, "sha256": item["content_digest"], "content": content}
    from tools.build_context import openspec_unnamed_baseline

    named = _named_docs(project, root)
    return [row for row in snapshots.values() if not (named and openspec_unnamed_baseline(row["path"], named))]  # the areas --docs does not name are context, no requirements


def _named_docs(project: Path, root: Path) -> list[str]:
    """The files ``--docs`` named, as the project's own paths (from the root, forward slashes) however they were typed."""
    from tools.doc_sections import resolve_spec
    from tools.run_pipeline import relative_to_project

    return [relative_to_project(project.resolve(), resolve_spec(project, doc)[0]) for doc in (read_run(root)["manifest"].get("scan_request") or {}).get("docs") or []]


def _validate_context_marker_sources(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], request: Mapping[str, Any], value: Mapping[str, Any],
) -> None:
    from tools.build_context import openspec_diagnostics
    from tools.context_marker_merge import merge_marker_artifacts, requirements_of

    stage_instance_id = str(request["stage_instance_id"])
    attempt_id = str(attempt["attempt_id"])
    snapshots = _context_marker_snapshots(project, root, state, attempt, stage_instance_id, request["input_digests"])
    requirements, batches = _context_marker_parts(state, attempt_id)
    if stage_instance_id.startswith("context-marker:batch-"):
        answer = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:requirements", str(requirements[1]))["artifact"]
        if requirements_of(value) != requirements_of(answer):
            raise ValueError("CONTEXT_MARKER_REQUIREMENTS_CHANGED: a part may add code observations only, not change the requirements")
        return
    if stage_instance_id != "context-marker:baseline":
        return  # the requirements step: OpenSpec is reconciled on its write (_reconcile_requirements_openspec) and on the merged marker
    if batches:
        # The merged marker is a pure function of the recorded answers: it is recomputed on every write and read.
        answer = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:requirements", str(requirements[1]))["artifact"]
        parts = [_read_model_stage_artifact_with_state(project, root, state, attempt_id, stage, str(response))["artifact"]
                 for stage, _request, response in batches]
        from tools.cases_import import run_marker_requirements

        derived = run_marker_requirements(root, attempt, answer)  # an import with documents
        if dict(value) != merge_marker_artifacts(answer, parts, derived):
            raise ValueError("merged context-marker artifact differs from the merge of its parts")
        from tools.cases_import import is_code_requirements

        if is_code_requirements(root, attempt, answer):
            return  # the requirements of an import without documents are its cases, not an OpenSpec reading
    if snapshots:
        diagnostics = openspec_diagnostics(dict(value), snapshots)
        if diagnostics:
            raise ValueError("CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: " + json.dumps(diagnostics, ensure_ascii=False))


def _validate_model_request_predecessors(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any],
    stage_instance_id: str, invocation_id: str, inputs: Sequence[str],
    request_seq: int | None = None,
) -> None:
    """Bind generator/reviewer requests to their exact durable predecessors."""
    stage = stage_instance_id.split(":", 1)[0]
    if stage not in {"tc-generator", "tc-reviewer"}:
        return
    attempt_id = str(attempt["attempt_id"])
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id]
    cutoff = request_seq if request_seq is not None else state["events"][-1]["seq"] + 1
    if stage_instance_id == _REWORK_STAGE:  # the rework is asked for after the first review sent cases back (its receipt is in the journal before the request)
        if not any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("batch_id") == REWORK_BATCH and event["seq"] < cutoff for event in events):
            raise ValueError("tc-generator:rework-1 is requested only after the first review sent cases back")
    elif stage_instance_id == _REWORK_REVIEW_STAGE:  # the review of the rework: asked for on the answer of the generator of the rework
        if not any(event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id and event["seq"] < cutoff
                   and event.get("artifact_digest") == inputs[0] for event in events):
            raise ValueError("tc-reviewer:rework-1 is requested only on the review request of the answer of the rework")
    elif stage == "tc-generator":
        from tools.project_inventory import read_execution_baseline

        markers = [event for event in events if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
        # from batch 2 a fifth input — the capabilities of the earlier batches (its receipt)
        # the exact list (M context receipts) is checked at publish time by _expected_model_inputs
        if len(inputs) < 4 or len(markers) != 1 or markers[0]["seq"] >= cutoff or inputs[0] != markers[0].get("artifact_digest"):
            raise ValueError("tc-generator input digests must bind its prior marker, context, plan, and header")
        _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:baseline", inputs[0])
        context = read_context_selection(root, attempt_id, inputs[1])
        baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        selected = [event for event in events if event["event_type"] == "CONTEXT_SELECTED" and event.get("artifact_digest") == inputs[1] and event["seq"] < cutoff]
        if baseline["digest"] != attempt["baseline_digest"] or context["inventory_digest"] != baseline["inventory_digest"] or not selected:
            raise ValueError("tc-generator context must bind its prior attempt inventory selection")
    else:
        boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
        # only the package binding is wanted (the same in every revision), so a latest revision still waiting for its READ_BACK will do
        ledger = _read_reviewer_session_ledger_with_state(project, root, state, attempt_id, unconfirmed_ok=True)
        package = ledger["package_binding"]
        reviews = [event for event in events if event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id]
        prior_boundary = any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("artifact_digest") == boundary["digest"] and event["seq"] < cutoff for event in events)
        prior_ledger = any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("batch_id") == "reviewer-ledger-v1" and event["seq"] < cutoff for event in events)
        if (
            list(inputs) != [boundary["canonical_branch_digest"], boundary["package_digest"]]
            or invocation_id != boundary["reviewer_invocation_id"]
            or package.get("candidate_digest") != boundary["canonical_branch_digest"]
            or package.get("package_digest") != boundary["package_digest"]
            or len(reviews) != 1
            or reviews[0].get("artifact_digest") != boundary["canonical_branch_digest"]
            or reviews[0]["seq"] >= cutoff
            or not prior_boundary or not prior_ledger
        ):
            raise ValueError("tc-reviewer input digests and invocation must bind its prior candidate and reviewer boundary")


def _is_context_selection(root: Path, attempt_id: str, digest: str) -> bool:
    try:
        read_context_selection(root, attempt_id, digest)
    except (ValueError, OSError):
        return False
    return True


def _validate_generator_artifact_binding(root: Path, attempt_id: str, request: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    if request["stage"] != "tc-generator" or request.get("stage_instance_id") == _REWORK_STAGE:
        return
    inputs = request["input_digests"]
    context = read_context_selection(root, attempt_id, inputs[1])
    # plan and header follow the context receipts of the plan — one, or its list
    after = inputs.index(value.get("plan_digest"), 2) if value.get("plan_digest") in inputs[2:] else 2
    if (
        [value.get("context_receipt_digest"), value.get("plan_digest"), value.get("header_digest")] != [inputs[1], *inputs[after:after + 2]]
        or any(not _is_context_selection(root, attempt_id, digest) for digest in inputs[2:after])
        or value.get("context_receipt") != context
    ):
        raise ValueError("generator fragment must match its request and stored context receipt")


LEFT_MARK = "left-before-review.json"  # finish --abandon LEFT_BEFORE_REVIEW


def _read_model_request_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    request_digest: str,
) -> Mapping[str, Any]:
    target = _model_request_target(root, attempt_id, request_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        value = _parse_json_bytes(raw, "model request") if raw is not None else None
    except (OutputConfinementError, OSError, ValueError) as error:
        raise ValueError("model request is unreadable") from error
    stage = stage_instance_id.split(":", 1)[0]
    declaration = _MODEL_STAGE_REGISTRY.get(stage)
    if not isinstance(value, Mapping) or declaration is None:
        raise ValueError("model request is unbound")
    _check_sealed(value, "model request")
    _validate_schema("model-request.schema.json", value)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    run_id = attempt.get("run_id") if isinstance(attempt, Mapping) else None
    role, role_policy = declaration
    events = [
        event for event in state["events"]
        if event.get("event_type") == "MODEL_REQUESTED"
        and event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("artifact_digest") == request_digest
    ]
    expected_batch = stage_instance_id.split(":", 1)[1] if stage == "tc-generator" else None
    if (
        attempt is None
        or value.get("digest") != request_digest
        or value.get("run_id") != run_id
        or value.get("attempt_id") != attempt_id
        or value.get("stage_instance_id") != stage_instance_id
        or value.get("stage") != stage
        or value.get("role") != role
        or value.get("role_policy") != role_policy
        or len(events) != 1
        or events[0].get("batch_id") != expected_batch
    ):
        raise ValueError("model request is unbound")
    _validate_model_request_predecessors(
        project, root, state, attempt, stage_instance_id, value["invocation_id"],
        value["input_digests"], events[0]["seq"],
    )
    return dict(value)


def _undeclared_capabilities(fragment: Mapping[str, Any]) -> list[str]:
    """The capabilities the steps of a fragment use and its ``operation_capabilities`` do not declare (those of an earlier batch, not copied)."""
    declared = {row.get("capability_id") for row in fragment.get("operation_capabilities") or [] if isinstance(row, Mapping)}
    used = {step["operation"]["capability_id"] for case in fragment.get("test_cases") or [] if isinstance(case, Mapping) for step in case.get("steps") or []
            if isinstance(step, Mapping) and isinstance(step.get("operation"), Mapping) and step["operation"].get("kind") == "project_action"
            and isinstance(step["operation"].get("capability_id"), str)}
    return sorted(used - declared)


def _validate_model_stage_artifact(stage_instance_id: str, value: Mapping[str, Any]) -> None:
    from tools.schema_validation import schema_diagnostics

    if stage_instance_id.startswith("context-marker:"):
        schema_name = "context-marker-output.schema.json"
        if value.get("stage") != "context-marker":
            raise ValueError("invalid context-marker artifact")
    elif stage_instance_id == _REWORK_STAGE:
        return  # its form and its cases are checked against the journal (``check_rework_answer``): no schema stands for the cases alone
    elif stage_instance_id.startswith("tc-generator:"):
        schema_name = "candidate-fragment.schema.json"
        batch_id = stage_instance_id.split(":", 1)[1]
        body = {key: item for key, item in value.items() if key != "digest"}
        context = value.get("context_receipt")
        context_body = {
            key: item for key, item in context.items() if key != "digest"
        } if isinstance(context, Mapping) else {}
        if value.get("batch_id") != batch_id:
            raise ValueError(f"MODEL_STAGE_ARTIFACT_INVALID: {stage_instance_id}: в слоте лежит фрагмент пачки {value.get('batch_id')}, а ответ этой стадии — фрагмент "
                             f"пачки {batch_id}; fix: положи в слот фрагмент пачки {batch_id} (batch_id и namespace — из плана); ничего не записано, "
                             "отдай его единственным повтором стадии (--transport-attempts 2)")
        if (
            value.get("digest") != _model_stage_artifact_digest(body)
            or not isinstance(context, Mapping)
            or context.get("digest") != _model_stage_artifact_digest(context_body)
        ):
            raise ValueError("invalid generator fragment artifact")
    elif stage_instance_id in ("tc-reviewer:canonical", _REWORK_REVIEW_STAGE):
        schema_name = "tc-reviewer-output.schema.json"
    elif stage_instance_id.startswith("tc-to-autotest:"):
        schema_name = "tc-to-autotest-output.schema.json"
        revision = value.get("artifacts", {}).get("automation_revision")
        if stage_instance_id != f"tc-to-autotest:r{revision}":
            raise ValueError("invalid automation generator artifact")
    elif stage_instance_id.startswith("autotest-reviewer:"):
        schema_name = "autotest-reviewer-output.schema.json"
        revision = value.get("artifacts", {}).get("autotest_review", {}).get("automation_revision")
        if stage_instance_id != f"autotest-reviewer:r{revision}":
            raise ValueError("invalid automation reviewer artifact")
    else:
        raise ValueError("unsupported model stage artifact")
    diagnostics = schema_diagnostics(dict(value), _SCHEMA_ROOT / schema_name, _PACK_ROOT)
    if diagnostics:  # the same retry_prompt validate_artifact gives, so the one retry needs no second command
        from tools.validate_artifact import retry_prompt

        missing = _undeclared_capabilities(value) if stage_instance_id.startswith("tc-generator:BATCH-") and any(str(row.get("path") or "").endswith("/semantic_type") for row in diagnostics) else []
        cause = (f"возможная причина semantic_type: capability {', '.join(missing[:5])}{' …' if len(missing) > 5 else ''} нет в operation_capabilities этого фрагмента, а тип берётся из неё; "
                 "скопируй её слово в слово из входа prior_capabilities (capabilities прошлых пачек) в operation_capabilities этого фрагмента; ") if missing else ""
        raise ModelStageArtifactInvalid(
            f"invalid model stage artifact: {stage_instance_id} does not pass {schema_name}: "
            + "; ".join(f"{row['path'] or '/'} {row['code']}" for row in diagnostics[:3])
            + (f" and {len(diagnostics) - 3} more" if len(diagnostics) > 3 else "")
            + "; fix: " + cause + "give the role its original input and this retry_prompt, then submit --transport-attempts 2 — "
            "nothing is written; retry_prompt:\n"
            + retry_prompt(dict(value), diagnostics, "python -m tools.validate_artifact schemas/" + schema_name + " <файл ответа>")  # the name is one of the closed list above
        )


def _model_stage_artifact_target(root: Path, attempt_id: str, content_digest: str) -> Path:
    if (
        not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
        or not _DIGEST.fullmatch(content_digest)
    ):
        raise ValueError("invalid model stage artifact identity")
    return root / "model-stage-artifacts" / attempt_id / (
        content_digest.removeprefix("sha256:") + ".json"
    )


def _read_model_stage_artifact_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    """Inside one closure a stage answer is read and checked once — its bytes are content-addressed and its request and
    predecessors are earlier events of an append-only journal, so a second read of the same digest checks the same things."""
    kept = _CLOSURE_CACHE.get()
    key = ("stage-artifact", str(root), attempt_id, stage_instance_id, content_digest)
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    result = _read_model_stage_artifact_checked(project, root, state, attempt_id, stage_instance_id, content_digest)
    if kept is not None:
        kept[key] = copy.deepcopy(result)
    return result


def _read_model_stage_artifact_checked(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    from tools.schema_validation import StrictJsonError, loads_json_strict

    target = _model_stage_artifact_target(root, attempt_id, content_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        value = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("model stage artifact is unreadable") from error
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    events = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
    ]
    requests = [event for event in events if event.get("event_type") == "MODEL_REQUESTED"]
    responses = [
        event for event in events
        if event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
        and event.get("artifact_digest") == content_digest
    ]
    candidates = [
        event for event in events
        if event.get("event_type") == "CANDIDATE_PUBLISHED"
        and event.get("artifact_digest") == content_digest
    ]
    event_binding_valid = (
        len(requests) == 1
        and len(responses) == 1
        and requests[0]["seq"] < responses[0]["seq"]
        and (
            len(candidates) == 1 and responses[0]["seq"] < candidates[0]["seq"]
            if stage_instance_id.startswith(("tc-generator:", "tc-to-autotest:"))
            else not candidates
        )
    )
    if (
        attempt is None
        or not isinstance(value, Mapping)
        or raw != _model_stage_artifact_bytes(value)
        or "sha256:" + hashlib.sha256(raw).hexdigest() != content_digest
        or not event_binding_valid
    ):
        raise ValueError("model stage artifact is unbound")
    request = _read_model_request_with_state(
        project, root, state, attempt_id, stage_instance_id,
        str(requests[0].get("artifact_digest", "")),
    )
    _validate_model_stage_artifact(stage_instance_id, value)
    if stage_instance_id.startswith("context-marker:"):
        _validate_context_marker_sources(project, root, state, attempt, request, value)
    _validate_generator_artifact_binding(root, attempt_id, request, value)
    return {
        "artifact": dict(value),
        "byte_count": len(raw),
        "content_digest": content_digest,
        "path": str(target.relative_to(root)).replace("\\", "/"),
    }


def read_context_selection(run_root: Path, attempt_id: str, receipt_digest: str, *, live: bool = True) -> Mapping[str, Any]:
    """Read a C-lite receipt only when immutable bytes and attempt event both bind it. Once per closure — the receipt is
    content-addressed and its event is in the journal for good.  ``live=False`` (the receipt of a reuse source, compared as it was recorded) does not
    read the files of the project again: the digest of the body, the frozen inventory and the event still bind it."""
    kept = _CLOSURE_CACHE.get()
    key = ("context-selection", str(run_root), attempt_id, receipt_digest, live)
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    receipt = _read_context_selection_checked(run_root, attempt_id, receipt_digest, live)
    if kept is not None:
        kept[key] = copy.deepcopy(receipt)
    return receipt


def _read_context_selection_checked(run_root: Path, attempt_id: str, receipt_digest: str, live: bool = True) -> Mapping[str, Any]:
    from tools.project_inventory import ContextSelectionError, validate_context_receipt_binding
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    target = _context_selection_target(root, attempt_id, receipt_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        if raw is None:
            raise FileNotFoundError(str(target.name))
        receipt = loads_json_strict(raw.decode("utf-8"))
        if not isinstance(receipt, dict):
            raise ContextSelectionError("context receipt is not an object")
        inventory = _frozen_inventory(project, root, str(receipt.get("inventory_digest", "")))
        if live:
            validate_context_receipt_binding(inventory, project, receipt)
        else:
            body = json.dumps({key: value for key, value in receipt.items() if key != "digest"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")  # as the scan sealed it
            if receipt.get("digest") != "sha256:" + hashlib.sha256(body).hexdigest() or receipt.get("inventory_digest") != inventory.get("digest"):
                raise ContextSelectionError("context receipt digest or inventory binding is invalid")
    except (OSError, UnicodeDecodeError, StrictJsonError, ContextSelectionError, OutputConfinementError) as error:
        raise ValueError("invalid context selection readback") from error
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    batch_id = f"review-{receipt['batch_index']:04d}"
    event = next((item for item in state["events"] if item["event_type"] == "CONTEXT_SELECTED" and item.get("attempt_id") == attempt_id and item.get("batch_id") == batch_id and item.get("artifact_digest") == receipt_digest), None)
    if attempt is None or event is None or receipt.get("digest") != receipt_digest:
        raise ValueError("unbound context selection receipt")
    return receipt


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


def read_run(run_root: Path) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    manifest, receipt = _run_boundary(project, root)
    return {"run_root": str(root), "manifest": manifest, "authorization": receipt}


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
def _check_phase1_artifact(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    if not common <= set(receipt) or not identity_matches:
        raise ValueError("invalid factual receipt")
    if set(receipt) != common | {"module_selection_digest", "baseline_digest"} or not isinstance(receipt.get("module_selection_digest"), str) or not isinstance(receipt.get("baseline_digest"), str) or receipt["module_selection_digest"] != module_selection_digest(project, attempt["module"]) or receipt["baseline_digest"] != attempt["baseline_digest"]:
        raise ValueError("invalid phase1 artifact")


def _check_review_canaries(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the digests of the canary map and of the reviewer's view, bound to the assembled candidate
    from tools.review_canaries import MAX_CANARIES

    digests = ("candidate_digest", "view_digest", "canary_map_digest")
    if (
        set(receipt) != common | {*digests, "canary_count"}
        or not identity_matches
        or any(not _DIGEST.fullmatch(str(receipt.get(name, ""))) for name in digests)
        or type(receipt.get("canary_count")) is not int
        or not 0 <= receipt["canary_count"] <= MAX_CANARIES
        or not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("attempt_id") == attempt["attempt_id"]
                   and event.get("stage_instance_id") == "assembly" and event.get("artifact_digest") == receipt["candidate_digest"]
                   for event in state["events"])
    ):
        raise ValueError("invalid review-canaries receipt")


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


def _validate_factual_receipt(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    kind: str,
    receipt: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """One factual receipt: the fields every kind has here, the rest by the check of its kind — ``_RECEIPT_CHECKS``, then the kinds of
    cases of ``model_inputs`` (``RECEIPT_CHECKS``, then its families by prefix), then a kind of mode B by its module through the gate; a
    kind of none is refused."""
    from tools import model_inputs  # the kinds of cases: above the journal by layers, in either delivery

    common = {"schema_version", "kind", "run_id", "attempt_id", "policy_profile", "digest"}
    identity_matches = (
        receipt.get("kind") == kind
        and receipt.get("run_id") == attempt["run_id"]
        and receipt.get("attempt_id") == attempt["attempt_id"]
        and receipt.get("policy_profile") == attempt["policy_profile"]
    )
    check = (_RECEIPT_CHECKS.get(kind) or model_inputs.RECEIPT_CHECKS.get(kind)
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
# a kind -> its check (``_validate_factual_receipt``); the kinds of cases are in ``model_inputs`` (``RECEIPT_CHECKS``, ``RECEIPT_FAMILIES``)
_RECEIPT_CHECKS = {
    "phase1-artifact": _check_phase1_artifact, "review-canaries": _check_review_canaries, "structured-result": _check_structured_result,
    "reviewer-session-boundary": _check_reviewer_session_boundary, "effective-canonical": _check_effective_canonical,
}
# the kinds of mode B: their checks are in its module (``execution_journal``), which a delivery of cases does not carry
_MODE_B_KINDS = frozenset({
    "automation-review-boundary-r1", "automation-review-boundary-r2", "execution-inputs", "generated-delta", "execution-receipt",
    "materialization-command", "resume-validation", "execution-trace", "trace-audit", "materialization-ownership", "disposition-plan",
    "disposition-receipt",
})


class ModelStageArtifactInvalid(ValueError):
    """A model answer that does not pass its schema; the message carries the retry_prompt."""

    code = "MODEL_STAGE_ARTIFACT_INVALID"


def publish_review_canaries(run_root: Path, attempt_id: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bind the digests of the canary map and of the reviewer's view to the attempt: the receipt and its event pair, marked with
    ``batch_id`` ``review-canaries-v1`` — whether canaries were seeded is read from these events, not from a file.  The same facts
    again change nothing."""
    from tools.review_canaries import BATCH_ID

    return publish_bound(run_root, attempt_id, "review-canaries", dict(facts), batch_id=BATCH_ID)


_RESULT_EVIDENCE_KEYS = {
    "canonical_schema_valid", "canonical_semantics_valid", "canonical_provenance_valid", "reviewer_session_complete",
    "authoritative_verdict", "authoritative_verdict_count", "reviewer_pre_verdict_abort", "reviewer_isolation_state", "blocker_count", "trace_valid",
    "finalization_completed", "finalization_read_back", "finalization_valid", "materialization_applicability",
    "execution_applicability", "automation_accepted", "generated_required_count", "generated_materialized_count",
    "generated_retained_count", "exact_target_pass", "mixed_manual_traceable", "operational_reliable",
}
_RESULT_BASE_KEYS = {"run_id", "attempt_id", "attempt_state", "completion", "verification", "coverage", "reason_code"}


def _result_facts(facts: Mapping[str, Any], policy_profile: str, *, external_cause_available: bool = True) -> tuple[dict[str, Any], bool]:
    if policy_profile not in _PROFILES or not isinstance(facts, Mapping):
        raise ValueError("invalid result facts")
    waiting = {"run_id", "attempt_id", "attempt_state"}
    if facts.get("attempt_state") in {"ACTIVE", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL"}:
        if (
            not waiting <= set(facts)
            or set(facts) - (waiting | {"completion", "verification", "coverage"})
            or not isinstance(facts.get("run_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", facts["run_id"])
            or not isinstance(facts.get("attempt_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", facts["attempt_id"])
            or any(facts.get(axis) is not None for axis in ("completion", "verification", "coverage"))
        ):
            raise ValueError("invalid waiting result facts")
        return {
            "run_id": facts["run_id"], "attempt_id": facts["attempt_id"], "attempt_state": facts["attempt_state"],
            "completion": None, "verification": None, "coverage": None,
        }, False
    allowed = _RESULT_BASE_KEYS | _RESULT_EVIDENCE_KEYS | {"prior_stage_cause"}
    if set(facts) - {"review_independence"} != allowed or facts.get("attempt_state") != "TERMINAL" or not isinstance(facts.get("run_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", facts["run_id"]) or not isinstance(facts.get("attempt_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", facts["attempt_id"]):
        raise ValueError("invalid result facts")
    if facts["completion"] not in {"COMPLETE", "PARTIAL", "FATAL"} or facts["verification"] not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE", None} or facts["coverage"] not in {"FULL", "MIXED", "MANUAL_ONLY", None} or facts["reason_code"] is not None and not _safe_label(facts["reason_code"]) or not _safe_label(facts["prior_stage_cause"]):
        raise ValueError("invalid result facts")
    e = {key: facts[key] for key in _RESULT_EVIDENCE_KEYS}
    boolean_keys = _RESULT_EVIDENCE_KEYS - {
        "authoritative_verdict", "authoritative_verdict_count", "reviewer_isolation_state", "blocker_count",
        "materialization_applicability", "execution_applicability", "generated_required_count",
        "generated_materialized_count", "generated_retained_count",
    }
    if (
        not all(isinstance(e[key], bool) for key in boolean_keys)
        or e["authoritative_verdict"] not in {"ACCEPTED", "REJECTED", None}
        or e["reviewer_isolation_state"] not in {"verified", "independence_unverified"}
        or e["materialization_applicability"] not in {"REQUIRED", "NOT_APPLICABLE"}
        or e["execution_applicability"] not in {"REQUIRED", "NOT_APPLICABLE"}
        or any(not isinstance(e[key], int) or isinstance(e[key], bool) or e[key] < 0 for key in {"authoritative_verdict_count", "blocker_count", "generated_required_count", "generated_materialized_count", "generated_retained_count"})
        or e["authoritative_verdict_count"] > 1
    ):
        raise ValueError("invalid result facts")
    if not e["finalization_completed"] or not e["finalization_read_back"]:
        raise ValueError("terminal finalization receipt is incomplete")
    if e["reviewer_session_complete"]:
        reviewer_valid = e["authoritative_verdict_count"] == 1 and e["authoritative_verdict"] in {"ACCEPTED", "REJECTED"} and not e["reviewer_pre_verdict_abort"]
    else:
        reviewer_valid = e["reviewer_pre_verdict_abort"] and e["authoritative_verdict_count"] == 0 and e["authoritative_verdict"] is None
    if not reviewer_valid:
        raise ValueError("invalid reviewer terminal invariant")
    if policy_profile == "cases-only-v1":
        if facts["verification"] != "NOT_APPLICABLE" or (e["materialization_applicability"], e["execution_applicability"]) != ("NOT_APPLICABLE", "NOT_APPLICABLE") or e["automation_accepted"] or e["generated_required_count"] != 0 or e["generated_materialized_count"] != 0 or e["generated_retained_count"] != 0 or e["exact_target_pass"]:
            raise ValueError("invalid cases-only applicability facts")
    elif policy_profile == "local-pilot-v1" and (e["materialization_applicability"], e["execution_applicability"]) == ("NOT_APPLICABLE", "NOT_APPLICABLE"):
        if (
            facts["completion"] not in {"PARTIAL", "FATAL"}
            or facts["verification"] != "NOT_APPLICABLE"
            or not facts["reason_code"]
            or e["automation_accepted"] or e["exact_target_pass"]
            or any(e[key] for key in ("generated_required_count", "generated_materialized_count", "generated_retained_count"))
        ):
            raise ValueError("local-pilot cannot use draft cases-only applicability")
    elif e["execution_applicability"] == "NOT_APPLICABLE" and e["exact_target_pass"]:
        raise ValueError("execution-not-applicable cannot claim exact target pass")
    if not e["generated_retained_count"] <= e["generated_materialized_count"] <= e["generated_required_count"]:
        raise ValueError("invalid generated file counts")
    if e["reviewer_pre_verdict_abort"] and facts["completion"] not in {"PARTIAL", "FATAL"}:
        raise ValueError("pre-verdict abort requires partial or fatal completion")
    closure_invalid = not e["finalization_valid"]
    reason = facts["reason_code"]
    if closure_invalid and reason not in {None, "FINALIZATION_INVALID"}:
        raise ValueError("invalid result reason tuple")
    if not closure_invalid and reason == "FINALIZATION_INVALID":
        raise ValueError("invalid result reason tuple")
    if reason == "REWORK" and (facts["completion"] != "PARTIAL" or facts["verification"] != "NOT_APPLICABLE"):
        raise ValueError("invalid result reason tuple")
    if reason == "REWORK" and (not e["reviewer_session_complete"] or e["authoritative_verdict"] != "REJECTED" or e["authoritative_verdict_count"] != 1 or e["reviewer_pre_verdict_abort"]):
        raise ValueError("rework requires one rejected reviewer verdict")
    if reason == "EXECUTION_UNKNOWN" and (facts["completion"] != "PARTIAL" or facts["verification"] != "UNKNOWN"):
        raise ValueError("invalid result reason tuple")
    # cases were sent back and the code subtracted them: the review accepted the rest (one accepting verdict), the set is not whole
    if reason == "PARTIAL_ACCEPTED" and (facts["completion"] != "PARTIAL" or not e["reviewer_session_complete"] or e["authoritative_verdict"] != "ACCEPTED"
                                         or e["authoritative_verdict_count"] != 1 or e["reviewer_pre_verdict_abort"]):
        raise ValueError("invalid result reason tuple")
    # the automated subset passed, the cases with blockers stayed manual
    if reason == "AUTOMATION_PARTIAL" and (policy_profile != "local-pilot-v1" or facts["completion"] != "PARTIAL"
                                           or facts["verification"] != "PASS" or e["blocker_count"] == 0):
        raise ValueError("invalid result reason tuple")
    if reason == "REVIEW_CONTEXT_LIMIT" and (
        facts["completion"] != "PARTIAL"
        or facts["verification"] != "NOT_APPLICABLE"
        or e["authoritative_verdict_count"] != 0
        or e["authoritative_verdict"] is not None
        or e["reviewer_session_complete"]
        or not e["reviewer_pre_verdict_abort"]
    ):
        raise ValueError("invalid result reason tuple")
    # The unequal file-set counts are the factual signal for the frozen
    # partial-materialization branch.  Execution applicability is a
    # consequence to validate below, not part of recognizing the branch:
    # otherwise a resealed contradictory REQUIRED execution tuple could mask
    # the partial state behind FINALIZATION_INVALID.
    partial_materialization = (
        policy_profile == "local-pilot-v1"
        and e["materialization_applicability"] == "REQUIRED"
        and e["generated_required_count"] > e["generated_materialized_count"]
    )
    if partial_materialization and (
        facts["completion"] != "PARTIAL"
        or facts["verification"] != "NOT_APPLICABLE"
        or e["execution_applicability"] != "NOT_APPLICABLE"
        or e["exact_target_pass"]
        or not e["reviewer_session_complete"]
        or e["authoritative_verdict"] != "ACCEPTED"
        or e["authoritative_verdict_count"] != 1
        or e["reviewer_pre_verdict_abort"]
        or not e["automation_accepted"]
        or e["generated_retained_count"] != 0
    ):
        raise ValueError("invalid materialization-incomplete result tuple")
    if not closure_invalid and partial_materialization and (
        reason != "MATERIALIZATION_INCOMPLETE"
        or external_cause_available and facts["prior_stage_cause"] != "MATERIALIZATION_INCOMPLETE"
    ):
        raise ValueError("invalid materialization-incomplete result tuple")
    if closure_invalid and partial_materialization and external_cause_available and facts["prior_stage_cause"] != "MATERIALIZATION_INCOMPLETE":
        raise ValueError("invalid materialization-incomplete external cause")
    if not closure_invalid and reason == "MATERIALIZATION_INCOMPLETE" and not partial_materialization:
        raise ValueError("invalid materialization-incomplete result tuple")
    post_verdict_reasons = {"REWORK", "EXECUTION_UNKNOWN", "MATERIALIZATION_INCOMPLETE"}
    if e["reviewer_pre_verdict_abort"]:
        abort_cause = facts["prior_stage_cause"] if closure_invalid else reason
        if abort_cause is None or abort_cause in post_verdict_reasons:
            raise ValueError("invalid pre-verdict abort reason")
    if facts["verification"] == "UNKNOWN" and not closure_invalid and reason != "EXECUTION_UNKNOWN":
        raise ValueError("invalid unknown result tuple")
    # SELF marks a review admitted by `scan --accept-self-review`; it never stands next to a verified isolation.
    self_review = "review_independence" in facts
    if self_review and (facts["review_independence"] != "SELF" or e["reviewer_isolation_state"] == "verified"):
        raise ValueError("invalid review independence")
    common = all(e[key] is True for key in ("canonical_schema_valid", "canonical_semantics_valid", "canonical_provenance_valid", "reviewer_session_complete", "trace_valid", "finalization_completed", "finalization_read_back", "finalization_valid", "operational_reliable")) and e["authoritative_verdict"] == "ACCEPTED" and e["authoritative_verdict_count"] == 1 and (e["reviewer_isolation_state"] == "verified" or self_review) and e["blocker_count"] == 0
    if policy_profile == "cases-only-v1":
        # Draft/artifact-only: never full-pipeline accepted. Live PASS belongs to local-pilot-v1.
        accepted = False
    else:
        accepted = reason is None and common and facts["completion"] == "COMPLETE" and facts["verification"] == "PASS" and facts["coverage"] in {"FULL", "MIXED"} and (facts["coverage"] != "MIXED" or e["mixed_manual_traceable"]) and e["automation_accepted"] and e["exact_target_pass"] and e["materialization_applicability"] == "REQUIRED" and e["execution_applicability"] == "REQUIRED" and e["generated_required_count"] > 0 and e["generated_required_count"] == e["generated_materialized_count"] == e["generated_retained_count"]
    return dict(facts), accepted


def _terminal_result(facts: Mapping[str, Any], policy_profile: str, *, external_cause_available: bool) -> Mapping[str, Any]:
    facts, accepted = _result_facts(facts, policy_profile, external_cause_available=external_cause_available)
    result = {"schema_version": "1.0.0", "run_id": facts["run_id"], "attempt_id": facts["attempt_id"], "policy_profile": policy_profile, "attempt_state": facts["attempt_state"], "completion": facts["completion"], "verification": facts["verification"], "coverage": facts["coverage"]}
    if facts["attempt_state"] == "TERMINAL":
        result["accepted"] = accepted
        closure_invalid = not all(facts[key] for key in ("finalization_completed", "finalization_read_back", "finalization_valid"))
        reason_code = "FINALIZATION_INVALID" if closure_invalid else facts["reason_code"]
        if reason_code is not None:
            result["reason_code"] = reason_code
        result["trace_valid"] = facts["trace_valid"]
        result["finalization_valid"] = facts["finalization_valid"]
        result["operational_reliable"] = facts["operational_reliable"]
        result["evidence"] = {key: facts[key] for key in _RESULT_EVIDENCE_KEYS}
        if "review_independence" in facts:
            result["review_independence"] = facts["review_independence"]
    return _sealed(result)


def terminal_result(facts: Mapping[str, Any], policy_profile: str) -> Mapping[str, Any]:
    return _terminal_result(facts, policy_profile, external_cause_available=True)


def _validate_result_record(result: Mapping[str, Any]) -> bool:
    try:
        _validate_schema("terminal-result.schema.json", result)
        _check_sealed(result, "terminal result")
        if result["attempt_state"] in {"ACTIVE", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL"}:
            return result.get("completion") is None and result.get("verification") is None and result.get("coverage") is None and "accepted" not in result and "reason_code" not in result
        evidence = result["evidence"]
        facts = {"run_id": result["run_id"], "attempt_id": result["attempt_id"], "attempt_state": result["attempt_state"], "completion": result["completion"], "verification": result["verification"], "coverage": result["coverage"], "reason_code": result.get("reason_code"), "prior_stage_cause": "EXTERNAL", **evidence}
        if "review_independence" in result:
            facts["review_independence"] = result["review_independence"]
        return dict(_terminal_result(facts, result["policy_profile"], external_cause_available=False)) == dict(result)
    except (KeyError, TypeError, ValueError):
        return False


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


def publish_closure_artifact(
    run_root: Path,
    attempt_id: str,
    kind: str,
    artifact: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Publish/read back one immutable pre-terminal closure artifact."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or kind not in _CLOSURE_KINDS:
        raise ValueError("invalid closure artifact attempt")
    value = dict(artifact)
    digest = value.get("digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or _sealed({key: item for key, item in value.items() if key != "digest"}) != value:
        raise ValueError("invalid closure artifact digest")
    if any(value.get(key) != attempt.get(key) for key in ("run_id", "attempt_id", "policy_profile")):
        raise ValueError("closure artifact identity is invalid")
    schema_name = {
        "pre_finalization_trace": "pre-finalization-trace.schema.json",
        "finalization_receipt": "finalization-receipt.schema.json",
        "terminal_trace": "derived-terminal-trace.schema.json",
    }[kind]
    _validate_schema(schema_name, value)
    if kind == "pre_finalization_trace":
        try:
            terminal_reviewer = terminal_reviewer_evidence(
                root, attempt_id,
            )
            effective = read_effective_canonical_if_present(
                root, attempt_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("pre-finalization canonical lineage is invalid") from error
        reviewer = value.get("reviewer")
        expected_reviewer = {
            key: terminal_reviewer[key]
            for key in (
                "digest", "session_complete", "authoritative_verdict",
                "authoritative_verdict_count", "pre_verdict_abort", "isolation", "self_accepted",
            )
            if key in terminal_reviewer
        }
        expected_effective = effective.get("document_digest") if effective is not None else None
        admitted = terminal_reviewer["isolation"] == "verified" or terminal_reviewer.get("self_accepted") is True
        if terminal_reviewer["authoritative_verdict"] == "ACCEPTED" and admitted and effective is None:
            raise ValueError("pre-finalization effective canonical is unavailable")
        if (
            value.get("effective_canonical_digest") != expected_effective
            or value.get("canonical_digest") != terminal_reviewer.get("canonical_digest")
            or not isinstance(reviewer, Mapping)
            or dict(reviewer) != expected_reviewer
        ):
            raise ValueError("pre-finalization canonical lineage is invalid")
        disposition_digest = value.get("disposition_receipt_digest")
        disposition_event_digest = None
        if disposition_digest is not None:
            try:
                disposition_record = read_attempt_receipt(
                    root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
                )["record"]
                disposition = disposition_record["payload"]
                disposition_event_digest = disposition_record["digest"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization disposition binding is invalid") from error
            if (
                disposition.get("digest") != disposition_digest
                or value.get("generated_delta_digest") != disposition.get("generated_delta_digest")
                or value.get("disposition_verification") != disposition.get("verification")
                or value.get("dispositions") != disposition.get("files")
            ):
                raise ValueError("pre-finalization disposition binding is invalid")
        execution = value.get("execution")
        execution_digest = execution.get("execution_receipt_digest") if isinstance(execution, Mapping) else None
        _validate_execution_event_branch(
            state["events"], attempt_id,
            str(execution.get("verification")) if isinstance(execution, Mapping) else "",
            execution_digest if isinstance(execution_digest, str) else None,
        )
        if execution_digest is not None:
            try:
                execution_record = read_attempt_receipt(
                    root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
                )["record"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization execution binding is invalid") from error
            if execution_record.get("digest") != execution_digest or execution_record.get("payload", {}).get("verdict") != execution.get("verification"):
                raise ValueError("pre-finalization execution binding is invalid")
        try:
            resume_validation = read_resume_validation_if_present(
                root, attempt_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("pre-finalization resume validation binding is invalid") from error
        resume_digest = value.get("resume_validation_digest")
        if isinstance(resume_validation, Mapping) and (
            resume_validation.get("execution_receipt_digest") != execution_digest
        ):
            raise ValueError("pre-finalization resume validation binding is invalid")
        if resume_digest is not None:
            if (
                not isinstance(resume_validation, Mapping)
                or resume_validation.get("digest") != resume_digest
                or resume_validation.get("pre_finalization_trace_digest") is not None
            ):
                raise ValueError("pre-finalization resume validation binding is invalid")
        elif isinstance(resume_validation, Mapping):
            if resume_validation.get("pre_finalization_trace_digest") != value.get("digest"):
                raise ValueError("pre-finalization late validation binding is invalid")
            ordered = [
                (index, event.get("event_type"), event.get("artifact_digest"))
                for index, event in enumerate(state["events"])
                if event.get("attempt_id") == attempt_id
                and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
                and event.get("artifact_digest") in {value.get("digest"), resume_validation.get("digest")}
            ]
            pretrace_readbacks = [
                index for index, event_type, digest in ordered
                if event_type == "ARTIFACT_READ_BACK" and digest == value.get("digest")
            ]
            late_events = [
                (index, event_type) for index, event_type, digest in ordered
                if digest == resume_validation.get("digest")
            ]
            if (
                len(pretrace_readbacks) != 1
                or [event_type for _index, event_type in late_events]
                != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
                or pretrace_readbacks[0] >= late_events[0][0]
            ):
                raise ValueError("pre-finalization late validation order is invalid")
        trace_binding = value.get("execution_trace")
        if not isinstance(trace_binding, Mapping):
            raise ValueError("pre-finalization execution trace binding is invalid")
        if trace_binding.get("applicability") == "PRESENT":
            trace_digest = trace_binding.get("trace_receipt_digest")
            audit_digest = trace_binding.get("audit_receipt_digest")
            try:
                trace_record = read_attempt_receipt(
                    root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
                )["record"]
                audit_record = read_attempt_receipt(
                    root, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
                )["record"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization execution trace binding is invalid") from error
            audit_payload = audit_record.get("payload")
            audit_lifecycle = audit_payload.get("trace_audit", {}).get("lifecycle") if isinstance(audit_payload, Mapping) else None
            if (
                trace_record.get("digest") != trace_digest
                or trace_record.get("trace_sha256") != trace_binding.get("trace_sha256")
                or audit_record.get("digest") != audit_digest
                or audit_record.get("execution_trace_receipt_digest") != trace_digest
                or audit_record.get("trace_sha256") != trace_binding.get("trace_sha256")
                or not isinstance(audit_payload, Mapping)
                or trace_binding.get("audit_valid") is not audit_payload.get("valid")
                or not isinstance(audit_lifecycle, Mapping)
                or audit_lifecycle.get("verification") != execution.get("verification")
            ):
                raise ValueError("pre-finalization execution trace binding is invalid")
            readback_positions = {
                event_digest: index
                for index, event in enumerate(state["events"])
                if event.get("attempt_id") == attempt_id
                and event.get("event_type") == "ARTIFACT_READ_BACK"
                and (event_digest := event.get("artifact_digest")) in {trace_digest, audit_digest, disposition_event_digest}
            }
            if (
                set(readback_positions) != {trace_digest, audit_digest, disposition_event_digest}
                or not (readback_positions[trace_digest] < readback_positions[audit_digest] < readback_positions[disposition_event_digest])
            ):
                raise ValueError("pre-finalization lifecycle order is invalid")
        elif trace_binding != {
            "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
            "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
        }:
            raise ValueError("pre-finalization execution trace binding is invalid")
    elif kind == "finalization_receipt":
        pre_digest = value.get("pre_finalization_trace_digest")
        if not isinstance(pre_digest, str):
            raise ValueError("finalization receipt pre-trace binding is invalid")
        pre_trace = read_closure_artifact(
            root, attempt_id, "pre_finalization_trace", pre_digest,
        )
        try:
            from tools.finalize_attempt import verify_finalization

            resume_validation = read_resume_validation_if_present(
                root, attempt_id,
            )
            post_pretrace_digest = (
                resume_validation.get("digest")
                if isinstance(resume_validation, Mapping)
                and resume_validation.get("pre_finalization_trace_digest") == pre_digest
                else None
            )
            verifier_inputs = {
                "run_id": pre_trace["run_id"],
                "attempt_id": pre_trace["attempt_id"],
                "policy_profile": pre_trace["policy_profile"],
                "pre_finalization_trace_digest": pre_digest,
                "required_artifacts": value.get("checked_artifacts"),
            }
            if post_pretrace_digest is not None:
                verifier_inputs["post_pretrace_validation_digest"] = post_pretrace_digest
            expected = verify_finalization(verifier_inputs, pre_trace)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("finalization receipt verification is invalid") from error
        if expected != value:
            raise ValueError("finalization receipt verification is invalid")
    elif kind == "terminal_trace":
        pre_digest = value.get("pre_finalization_trace_digest")
        finalization_digest = value.get("finalization_receipt_digest")
        if (
            not isinstance(pre_digest, str)
            or not isinstance(finalization_digest, str)
            or read_closure_artifact(
                root, attempt_id, "pre_finalization_trace", pre_digest,
            ).get("digest") != pre_digest
            or read_closure_artifact(
                root, attempt_id, "finalization_receipt", finalization_digest,
            ).get("digest") != finalization_digest
        ):
            raise ValueError("terminal trace closure binding is invalid")
        result_digest = value.get("terminal_result_digest")
        try:
            result = _read_artifact(project, root, root / "terminal-results" / f"{attempt_id}.json", "terminal result")
        except (OSError, ValueError) as error:
            raise ValueError("terminal trace result binding is invalid") from error
        result_events = [
            item["event_type"] for item in state["events"]
            if item.get("attempt_id") == attempt_id and item.get("artifact_digest") == result_digest
            and item.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        ]
        if result.get("digest") != result_digest or result_events != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
            raise ValueError("terminal trace result binding is invalid")
    target = _closure_target(root, attempt_id, kind, digest)
    record, created, identity = _publish(project, root, target, {key: item for key, item in value.items() if key != "digest"}, kind.replace("_", " "), return_created=True)
    if record != value:
        raise ValueError("closure artifact read-back mismatch")
    relevant = [
        event["event_type"] for event in derive_state(root)["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == digest
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if relevant == []:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    elif relevant not in (["ARTIFACT_PUBLISHED"], ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]):
        raise ValueError("invalid closure artifact event order")
    if read_confined_bytes(project, root, target) != _canonical_bytes(record):
        raise ValueError("closure artifact lost after publication binding")
    if relevant != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    if read_confined_bytes(project, root, target) != _canonical_bytes(record):
        raise ValueError("closure artifact lost after read-back binding")
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": _canonical_bytes(record), "record": record, "created": created, "installed_identity": identity}


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


def read_closure_artifact_if_present(
    run_root: Path,
    attempt_id: str,
    kind: str,
) -> Mapping[str, Any] | None:
    """Read or finish binding the one immutable closure slot already installed."""
    project, root = _run_root(run_root)
    if kind not in _CLOSURE_KINDS or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid closure artifact identity")
    target = root / "closure" / attempt_id / f"{kind}.json"
    try:
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError("unsafe closure artifact path") from error
    if raw is None:
        return None
    value = _read_artifact(project, root, target, kind.replace("_", " "))
    digest = value.get("digest")
    if not isinstance(digest, str):
        raise ValueError("closure artifact digest is unavailable")
    try:
        return read_closure_artifact(
            root, attempt_id, kind, digest,
        )
    except ValueError:
        # Installation precedes journal binding.  A controller interruption may
        # therefore leave the exact fixed-slot bytes with no READ_BACK event.
        # Republishing the same sealed value is non-mutating and lets the normal
        # publisher recover only the missing ordered binding events; conflicting
        # bytes or event orders still fail closed there.
        return publish_closure_artifact(root, attempt_id, kind, value)["record"]


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


def _validate_accepted_terminal_closure(
    root: Path,
    result: Mapping[str, Any],
    finalization_receipt: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Prevent a low-level caller from projecting acceptance beyond durable trace facts."""
    if result.get("accepted") is not True:
        return
    attempt_id = str(result["attempt_id"])
    pre_digest = finalization_receipt.get("pre_finalization_trace_digest")
    if finalization_receipt.get("valid") is not True or not isinstance(pre_digest, str):
        raise ValueError("accepted terminal result lacks valid pre-finalization evidence")
    pre_trace = read_closure_artifact(
        root, attempt_id, "pre_finalization_trace", pre_digest,
    )
    reviewer = pre_trace.get("reviewer")
    evidence = result.get("evidence")
    applicability = pre_trace.get("evidence")
    execution = pre_trace.get("execution")
    rows = pre_trace.get("dispositions")
    trace_binding = pre_trace.get("execution_trace")
    if not all(isinstance(value, Mapping) for value in (reviewer, evidence, applicability, execution, trace_binding)) or not isinstance(rows, list):
        raise ValueError("accepted terminal result lacks valid pre-finalization evidence")
    reviewer_bindings = {
        "reviewer_session_complete": reviewer.get("session_complete"),
        "authoritative_verdict": reviewer.get("authoritative_verdict"),
        "authoritative_verdict_count": reviewer.get("authoritative_verdict_count"),
        "reviewer_pre_verdict_abort": reviewer.get("pre_verdict_abort"),
        "reviewer_isolation_state": reviewer.get("isolation"),
    }
    if any(evidence.get(key) != value for key, value in reviewer_bindings.items()) or result.get("review_independence") != (
        "SELF" if reviewer.get("self_accepted") is True else None
    ):
        raise ValueError("accepted terminal result contradicts pre-finalization reviewer evidence")
    try:
        effective = read_effective_canonical(root, attempt_id)
        document = effective["document"]
        steps = [
            (case["case_id"], step)
            for case in document["test_cases"]
            for step in case["steps"]
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("accepted terminal result lacks an effective canonical") from error
    from tools.canonical_document import blocker_count as canonical_blocker_count

    blocker_count = canonical_blocker_count(document)
    manual_steps = sum(step["manual_only"] is True for _case_id, step in steps)
    coverage = "MANUAL_ONLY" if manual_steps == len(steps) else "MIXED" if manual_steps else "FULL"
    if (
        blocker_count != 0
        or evidence.get("blocker_count") != blocker_count
        or result.get("coverage") != coverage
        or pre_trace.get("canonical_digest") is None
        or pre_trace.get("effective_canonical_digest") != effective.get("document_digest")
    ):
        raise ValueError("accepted terminal result contradicts canonical coverage")
    profile = result.get("policy_profile")
    if profile == "cases-only-v1":
        if (
            any(applicability.get(key) != "NOT_APPLICABLE" for key in ("materialization", "execution", "dispositions"))
            or execution.get("verification") != "NOT_APPLICABLE"
            or rows
            or pre_trace.get("generated_delta_digest") is not None
            or pre_trace.get("disposition_receipt_digest") is not None
            or trace_binding != {
                "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
                "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
            }
            or evidence.get("materialization_applicability") != "NOT_APPLICABLE"
            or evidence.get("execution_applicability") != "NOT_APPLICABLE"
            or any(evidence.get(key) != 0 for key in (
                "generated_required_count", "generated_materialized_count", "generated_retained_count",
            ))
        ):
            raise ValueError("accepted cases-only result contradicts pre-finalization evidence")
        return
    if profile != "local-pilot-v1":
        raise ValueError("accepted terminal result has unknown policy profile")
    try:
        trace_record = read_attempt_receipt(
            root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
        )["record"]
        audit_record = read_attempt_receipt(
            root, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
        )["record"]
        trace_document = trace_record["payload"]
        audit_document = audit_record["payload"]
        relations = trace_document["implementation_relations"]
        trace_files = trace_document["files"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("accepted local result lacks durable trace evidence") from error
    if (
        any(applicability.get(key) != "PRESENT" for key in ("materialization", "execution", "dispositions"))
        or execution.get("verification") != "PASS"
        or not isinstance(execution.get("execution_receipt_digest"), str)
        or not isinstance(pre_trace.get("generated_delta_digest"), str)
        or not isinstance(pre_trace.get("disposition_receipt_digest"), str)
        or trace_binding.get("applicability") != "PRESENT"
        or trace_binding.get("trace_receipt_digest") != trace_record.get("digest")
        or trace_binding.get("trace_sha256") != trace_record.get("trace_sha256")
        or trace_binding.get("audit_receipt_digest") != audit_record.get("digest")
        or trace_binding.get("audit_valid") is not True
        or audit_document.get("valid") is not True
        or audit_record.get("execution_trace_receipt_digest") != trace_record.get("digest")
        or not isinstance(relations, list)
        or not isinstance(trace_files, list)
        or not rows
        or any(row.get("materialization") != "MATERIALIZED" or row.get("disposition") != "RETAINED" for row in rows if isinstance(row, Mapping))
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise ValueError("accepted local result lacks execution and retained dispositions")
    required_count = len(rows)
    if (
        evidence.get("materialization_applicability") != "REQUIRED"
        or evidence.get("execution_applicability") != "REQUIRED"
        or evidence.get("generated_required_count") != required_count
        or evidence.get("generated_materialized_count") != required_count
        or evidence.get("generated_retained_count") != required_count
        or evidence.get("automation_accepted") is not True
        or evidence.get("exact_target_pass") is not True
        or evidence.get("trace_valid") is not True
        or evidence.get("operational_reliable") is not True
        or evidence.get("mixed_manual_traceable") is not (coverage == "MIXED")
    ):
        raise ValueError("accepted local result contradicts generated-file evidence")
    actual_operations = {
        (row.get("case_id"), row.get("step_id"))
        for row in relations
        if isinstance(row, Mapping) and row.get("kind") == "operation"
    }
    actual_assertions = {
        (row.get("case_id"), row.get("step_id"), row.get("expectation_id"), row.get("assertion_id"))
        for row in relations
        if isinstance(row, Mapping) and row.get("kind") == "assertion"
    }
    expected_operations = {
        (case_id, step["step_id"])
        for case_id, step in steps
        if not step["manual_only"]
    }
    expected_assertions = {
        (case_id, step["step_id"], expectation["expectation_id"], assertion["assertion_id"])
        for case_id, step in steps
        if not step["manual_only"]
        for expectation in step["expectations"]
        for assertion in expectation["assertions"]
    }
    related_files = {row.get("file_id") for row in trace_files if isinstance(row, Mapping)}
    disposition_files = {row["file_id"] for row in rows}
    if not expected_operations <= actual_operations or not expected_assertions <= actual_assertions or related_files != disposition_files:
        raise ValueError("accepted local result has incomplete trace relations")


def publish_terminal_result(run_root: Path, facts: Mapping[str, Any], policy_profile: str, *, finalization_receipt: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    result = terminal_result(facts, policy_profile)
    if result["attempt_state"] != "TERMINAL" or not _validate_result_record(result):
        raise ValueError("terminal result is invalid")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == result["attempt_id"]), None)
    if attempt is None or attempt["run_id"] != result["run_id"] or attempt["policy_profile"] != policy_profile:
        raise ValueError("terminal result binding is invalid")
    if finalization_receipt is None:
        if attempt["state"] != "TERMINAL":
            raise ValueError("terminal result binding is invalid")
    else:
        if attempt["state"] == "TERMINAL":
            raise ValueError("terminal attempt is immutable")
        digest = finalization_receipt.get("digest")
        if not isinstance(digest, str) or read_closure_artifact(
            root, result["attempt_id"], "finalization_receipt", digest,
        ) != finalization_receipt:
            raise ValueError("terminal finalization receipt binding is invalid")
        if (
            finalization_receipt.get("stage") != "finalization"
            or result["finalization_valid"] is not bool(finalization_receipt.get("valid"))
            or any(finalization_receipt.get(key) != result.get(key) for key in ("run_id", "attempt_id", "policy_profile"))
        ):
            raise ValueError("terminal finalization receipt binding is invalid")
        _validate_accepted_terminal_closure(root, result, finalization_receipt, state)
    target = root / "terminal-results" / f"{result['attempt_id']}.json"
    record, created, identity = _publish(project, root, target, {key: value for key, value in result.items() if key != "digest"}, "terminal result", return_created=True)
    if finalization_receipt is not None:
        _bind_events(root, result["attempt_id"], record["digest"])
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": _canonical_bytes(record), "record": record, "created": created, "installed_identity": identity}


def read_terminal_result(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Return only a result already bound by the immutable terminal event."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] != "TERMINAL":
        raise KeyError(attempt_id)
    result = _read_artifact(project, root, root / "terminal-results" / f"{attempt_id}.json", "terminal result")
    terminal = next((item for item in state["events"] if item.get("attempt_id") == attempt_id and item["event_type"] == "ATTEMPT_TERMINAL"), None)
    if terminal is None or terminal.get("artifact_digest") != result.get("digest"):
        raise ValueError("terminal result binding is invalid")
    return result


def _project_state_for(policy_profile: str, project: Path) -> dict[str, Any] | None:
    """What a run says about git in the project at its start — by looking at the file system, never by starting ``git``.

    A project without ``.git`` is sealed as ``GIT_DIRECTORY`` (the precondition of the scenario ``fresh-non-git``); a project with it, and
    every ``cases-only-v1`` run, has ``project_state: null`` — "not taken", which no reader takes for "clean".  That the run did not touch
    the user's files is proved at its end by the inventory (``_project_tree_state``), not by ``git status`` (a clean filter or a hook
    of the project's own configuration runs there)."""
    if policy_profile == "cases-only-v1" or (Path(project) / ".git").exists():
        return None
    return _sealed({"kind": "GIT_DIRECTORY", "path": ".git", "entries": []})


def exit_code(result: Mapping[str, Any] | None, controller_error: bool = False) -> int:
    if controller_error or result is None or not _validate_result_record(result):
        return 2
    if result["attempt_state"] in {"WAITING_FOR_INPUT", "WAITING_FOR_MODEL"}:
        return 3
    if result["attempt_state"] == "ACTIVE":
        return 2
    if result["policy_profile"] == "cases-only-v1":
        return 1
    if result["accepted"]:
        return 0
    evidence = result["evidence"]
    if result["verification"] in {"UNKNOWN", "NOT_RUNNABLE"} or result["completion"] == "FATAL" or result.get("reason_code") == "FINALIZATION_INVALID" or not result["trace_valid"] or not result["operational_reliable"] or not evidence["finalization_completed"] or not evidence["finalization_read_back"]:
        return 2
    return 1


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
read_effective_canonical_with_state = _read_effective_canonical_with_state
read_model_request_with_state = _read_model_request_with_state
read_model_stage_artifact_with_state = _read_model_stage_artifact_with_state
receipt_target = _receipt_target
run_boundary = _run_boundary
safe_label = _safe_label
sealed = _sealed
self_review_accepted_at = _self_review_accepted_at
validate_schema = _validate_schema

# what the modules of the model step and of the observations take from here — public names of the same objects (П1 of the
# cut of v0.8)
MODEL_STAGE_INSTANCE = _MODEL_STAGE_INSTANCE
MODEL_STAGE_REGISTRY = _MODEL_STAGE_REGISTRY
PRIOR_KIND = _PRIOR_KIND
REWORK_REVIEW_STAGE = _REWORK_REVIEW_STAGE
REWORK_STAGE = _REWORK_STAGE
SKELETON_KIND = _SKELETON_KIND
canonical_bytes = _canonical_bytes
context_marker_parts = _context_marker_parts
context_marker_receipt_digests = _context_marker_receipt_digests
context_marker_snapshots = _context_marker_snapshots
context_selection_target = _context_selection_target
model_request_target = _model_request_target
model_stage_artifact_bytes = _model_stage_artifact_bytes
model_stage_artifact_digest = _model_stage_artifact_digest
model_stage_artifact_target = _model_stage_artifact_target
named_docs = _named_docs
parse_json_bytes = _parse_json_bytes
published_context_receipts = _published_context_receipts
safe_model_label = _safe_model_label
validate_context_marker_sources = _validate_context_marker_sources
validate_generator_artifact_binding = _validate_generator_artifact_binding
validate_model_request_predecessors = _validate_model_request_predecessors
validate_model_stage_artifact = _validate_model_stage_artifact


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
    """The modules that took the code of the journal, the lowest first (imported here: they import this module at their top)."""
    from tools import model_inputs, model_steps, scenario_observations

    return (model_inputs, model_steps, scenario_observations)


def _journal_mode_b() -> tuple[types.ModuleType, ...]:
    """The module of the journal of mode B, when the delivery carries mode B: once loaded, from ``sys.modules`` — a miss of the facade
    asks no finder (a blocked module, ``None`` there, still reads as absent); before that, through the gate."""
    loaded = sys.modules.get("tools.execution_journal")
    if loaded is not None:
        return (loaded,)
    return (execution_gate.load_mode_b("execution_journal"),) if execution_gate.mode_b_available() else ()


_facade(__name__, _journal_modules, _journal_mode_b)
