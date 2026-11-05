"""Durable, append-only Phase 1 controller state with safe local receipts."""
from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
import os
import re
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.schema_engine import Draft202012Validator, FormatChecker

from tools import execution_gate
from tools.fixes import delete_field_fix
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, create_confined_bytes_exclusive, create_confined_directory_exclusive, ensure_project_child_directory, read_confined_bytes, remove_confined_bytes_if_equal


_PROFILES = {"cases-only-v1", "local-pilot-v1"}
_AUTH_KEYS = {"request_id", "execution_requested", "host_id", "accept_self_review", "disposition_policy"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_MIXED_TOKEN = re.compile(r"^(?=.{24,}$)(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])[A-Za-z0-9_-]+$")
_MODEL_EVENT_TYPES = {"MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED"}
_MODEL_STAGE_INSTANCE = re.compile(
    r"^(?:context-marker:(?:baseline|requirements|batch-[0-9]{3})|tc-generator:BATCH-[a-z0-9-]+|assembly|tc-reviewer:canonical|tc-to-autotest:r[12]|autotest-reviewer:r[12])$"
)
_MODEL_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_MODEL_STAGE_REGISTRY = {
    "context-marker": ("generator", "context-marker-v1"),
    "tc-generator": ("generator", "tc-generator-v1"),
    "tc-reviewer": ("canonical-reviewer", "canonical-reviewer-v1"),
    "tc-to-autotest": ("automation-generator", "tc-to-autotest-v1"),
    "autotest-reviewer": ("automation-reviewer", "autotest-static-reviewer-v1"),
}
_EVENT_TYPES = {"MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "CONTEXT_SELECTED", "ATTEMPT_CREATED", *_MODEL_EVENT_TYPES, "ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK", "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN"}
_ACTORS = {"controller"}
_ATTEMPT_EVENTS = {"ATTEMPT_CREATED", "CONTEXT_SELECTED", *_MODEL_EVENT_TYPES, "WAITING_FOR_INPUT", "WAITING_FOR_MODEL", "EXECUTION_STARTED", "EXECUTION_UNKNOWN", "ATTEMPT_TERMINAL", "PROCESS_STOPPED", "TERMINAL_RETRY_OBSERVED", "RETAINED_NATIVE_RERUN"}
_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"
_PACK_ROOT = _SCHEMA_ROOT.parent
_CLOSURE_KINDS = {"pre_finalization_trace", "finalization_receipt", "terminal_trace"}
_DEDICATED_RECEIPT_TOKEN = object()
class _PublicationUnknown(ValueError):
    """A journal writer failed after an unclassifiable durable-state transition."""


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
    # FaT-6b: inside one closure a successful check of the same bytes is remembered by digest; the global lru cache
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


def _canonical_runner_output(text: str, *, limit: int = 2000) -> bytes:
    """Return the only publishable UTF-8 runner-output representation."""
    from tools.project_inventory import URL_CREDENTIALS_TEXT, token_signature_rule

    if not isinstance(text, str) or not text:
        return b""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.splitlines(keepends=True):
        content = line[:-1] if line.endswith("\n") else line
        if token_signature_rule(content.encode("utf-8", errors="replace")) or _credential_like(content) or any(_credential_like(fragment) for fragment in content.split()) or URL_CREDENTIALS_TEXT.search(
            content,
        ):
            line = "<redacted>" + ("\n" if line.endswith("\n") else "")
        lines.append(line)
    encoded = "".join(lines).encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return encoded
    return encoded[-limit:].decode("utf-8", errors="ignore").encode("utf-8")


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


# P6a: status and report read a run of a project that was moved or renamed.  The manifest keeps the absolute path of the folder
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
    """P6a: ``None`` when the run was made at ``project``; else ``{"recorded", "current", "same_structure", "missing"}``.

    The structure is the same when the run has frozen inventories, they read back whole (their own digest), and every file
    they list is in ``project`` at the same relative path.  Contents are not compared here: a changed file is a drift,
    which status reports as before.  Only reads."""
    from tools.project_inventory import InventoryError, read_execution_baseline, read_inventory_receipt

    project, root = Path(project).resolve(), Path(run_root)
    try:  # V9a: the sealed manifest (the boundary itself asks here, so not read_run)
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
            baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
            if baseline["digest"] != attempt["baseline_digest"]:
                raise ValueError("baseline digest mismatch")
            paths.append(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
        except (OSError, KeyError, TypeError, ValueError):
            same = False
    same = same and bool(paths)
    for path in paths:
        try:
            files = [str(row["project_path"]) for row in read_inventory_receipt(path)["files"]]
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


def _run_boundary(project: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_artifact(project, root, root / "run-manifest.json", "run manifest")
    receipt = _read_artifact(project, root, root / "run-authorization-receipt.json", "authorization receipt")
    if manifest.get("run_id") != root.name or (manifest.get("project") != str(project) and not _moved_project_ok(project, root)):
        raise ValueError("invalid run boundary")
    if receipt.get("run_id") != manifest["run_id"] or receipt.get("policy_profile") != manifest.get("policy_profile") or receipt.get("digest") != manifest.get("authorization_digest"):
        raise ValueError("invalid run boundary")
    auth = {key: receipt[key] for key in _AUTH_KEYS if key in receipt}
    if _validate_authorization(receipt["policy_profile"], auth) != auth:
        raise ValueError("invalid run boundary")
    return manifest, receipt


def _self_review_accepted_at(project: Path, root: Path) -> bool:
    return _run_boundary(project, root)[1].get("accept_self_review") is True


def _disposition_policy_at(project: Path, root: Path) -> str:
    return "cleanup" if _run_boundary(project, root)[1].get("disposition_policy") == "cleanup" else "quarantine"


def disposition_policy(run_root: Path) -> str:
    """FaT-8: what a FAIL does to generated files — ``quarantine`` (default) or ``cleanup`` (`scan --disposition-policy cleanup`)."""
    project, root = _run_root(run_root)
    return _disposition_policy_at(project, root)


def self_review_accepted(run_root: Path) -> bool:
    """FaT-4b: the run admits a review without a fresh context (`scan --accept-self-review`)."""
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
                    # FaT-5: the marker in parts — requirements, then the batches, then the merge by code.
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
                        or attempt_id in assembled
                    ):
                        raise ValueError("generator request is outside canonical assembly lifecycle")
                    if stage.startswith(("tc-reviewer:", "autotest-reviewer:")) and key not in review_requests:
                        raise ValueError("review model request requires review request")
                    model_requests.add(key)
                elif event_type == "MODEL_RESPONSE_RECEIVED":
                    if key not in model_requests or key in model_responses or stage.startswith("tc-generator:") and attempt_id in assembled:
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
                        or stage.startswith("tc-generator:") and attempt_id in assembled
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


def _events(project: Path, root: Path) -> list[dict[str, Any]]:
    """The checked event journal; inside closure_cache() only the lines after the checked prefix are checked (FaT-9b)."""
    cache = _CLOSURE_CACHE.get()
    pending = root / "pending-events"
    if cache is None or pending.is_dir() and any(pending.glob("*.json")):
        return _events_full(project, root)  # a pending marker is checked and recovered by the full path
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
    """One journal line against its copy in events/, its schema, seal, seq, run and chain link (FaT-9b, as in _events_full)."""
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


def _events_full(project: Path, root: Path) -> list[dict[str, Any]]:
    journal = root / "events.jsonl"
    try:
        journal_bytes = read_confined_bytes(project, root, journal)
    except OutputConfinementError as error:
        raise ValueError("invalid event journal") from error
    if journal_bytes is None:
        raise ValueError("missing event journal")
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
                try:
                    removed_marker = remove_confined_bytes_if_equal(project, root, marker_path, marker_bytes)
                except OutputConfinementError as error:
                    raise ValueError("invalid event journal") from error
                if not removed_marker:
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
            try:
                removed_event = remove_confined_bytes_if_equal(project, root, event_path, event_bytes)
            except OutputConfinementError as error:
                raise ValueError("invalid event journal") from error
            if not removed_event:
                raise ValueError("invalid event journal")
            try:
                removed_marker = remove_confined_bytes_if_equal(project, root, marker_path, marker_bytes)
            except OutputConfinementError as error:
                raise ValueError("invalid event journal") from error
            if not removed_marker:
                raise ValueError("invalid event journal")
        elif marker["seq"] == len(result):
            committed = result[marker["seq"] - 1]
            committed_bytes = _canonical_bytes(committed)
            prior_bytes = b"".join(lines[:-1])
            prior_event_digest = result[-2]["digest"] if len(result) > 1 else None
            if marker["run_id"] != manifest["run_id"] or marker["prior_digest"] != prior_event_digest or marker["prior_bytes_digest"] != "sha256:" + hashlib.sha256(prior_bytes).hexdigest() or marker["prior_bytes_length"] != len(prior_bytes) or marker["event_digest"] != committed["digest"] or marker["bytes_length"] != len(committed_bytes) or marker["bytes_digest"] != "sha256:" + hashlib.sha256(committed_bytes).hexdigest() or event_bytes != committed_bytes:
                raise ValueError("invalid event journal")
            try:
                removed_marker = remove_confined_bytes_if_equal(project, root, marker_path, marker_bytes)
            except OutputConfinementError as error:
                raise ValueError("invalid event journal") from error
            if not removed_marker:
                raise ValueError("invalid event journal")
        else:
            raise ValueError("invalid event journal")
    return result


def create_run(project: Path, policy_profile: str, authorization: Mapping[str, Any]) -> Mapping[str, Any]:
    return _create_run(project, policy_profile, authorization, None)


def create_scan_run(project: Path, policy_profile: str, authorization: Mapping[str, Any], scan_request: Mapping[str, Any],
                    skipped: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    """FaT-G: the run of a scan — its flags as given go to the manifest (``scan_request``); lowered when ``skipped``."""
    downgrade = None if skipped is None else {"requested_policy_profile": "local-pilot-v1", **skipped}
    return _create_run(project, "cases-only-v1" if skipped is not None else policy_profile, authorization, downgrade, scan_request)


def create_lowered_run(project: Path, authorization: Mapping[str, Any], skipped: Mapping[str, str]) -> Mapping[str, Any]:
    """FaT-R: a local-pilot request whose build is not ready runs as cases-only-v1; the manifest keeps both profiles."""
    return _create_run(project, "cases-only-v1", authorization, {"requested_policy_profile": "local-pilot-v1", **skipped})


def _create_run(project: Path, policy_profile: str, authorization: Mapping[str, Any], downgrade: Mapping[str, Any] | None,
                scan_request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    safe_auth = _validate_authorization(policy_profile, authorization)
    root_project = _project_root(project)
    project_state = _project_state_for(policy_profile, root_project)
    runs = root_project / ".pilot-runs"
    if runs.exists() and _is_reparse(runs):
        raise ValueError("unsafe run root")
    runs_existed = runs.exists()
    run_id = uuid.uuid4().hex
    root = runs / run_id
    receipt: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    try:
        ensure_project_child_directory(root_project, runs)
        create_confined_directory_exclusive(root_project, runs, root)
        if _is_reparse(root):
            raise ValueError("unsafe run root")
        create_confined_directory_exclusive(root_project, root, root / "tmp")  # PT2: where --out writes; the code makes it, not the agent
        receipt_data = {"schema_version": "1.0.0", "run_id": run_id, "request_id": safe_auth["request_id"], "policy_profile": policy_profile, "execution_requested": safe_auth["execution_requested"]}
        if "host_id" in safe_auth:
            receipt_data["host_id"] = safe_auth["host_id"]
        if safe_auth.get("accept_self_review") is True:
            # FaT-4b: the human's choice before any review, under the same digest; absent means the prior rules.
            receipt_data["accept_self_review"] = True
        if safe_auth.get("disposition_policy") == "cleanup":
            # FaT-8: decided once at scan; absent means quarantine and the receipt keeps its prior bytes.
            receipt_data["disposition_policy"] = "cleanup"
        receipt = _publish(root_project, root, root / "run-authorization-receipt.json", receipt_data, "authorization receipt")
        manifest_data = {"schema_version": "1.0.0", "run_id": run_id, "project": str(root_project), "policy_profile": policy_profile, "authorization_digest": receipt["digest"], "project_state": project_state}
        if downgrade is not None:
            manifest_data["requested_policy_profile"] = str(downgrade["requested_policy_profile"])
            manifest_data["execution_skipped"] = {"reason": str(downgrade["reason"]), "fix": str(downgrade["fix"])}
        if scan_request is not None:
            # FaT-G: the scan's own flags as given — to compare a repeated scan and to print the next scan; absent before FaT-G.
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


# FaT-9: inside one closure the same bytes are derived and validated once.  Keys are content digests — the
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
    """Remember derived state and successful receipt validations for one closure (FaT-9)."""
    if _CLOSURE_CACHE.get() is not None:
        yield
        return
    cache: dict[Any, Any] = {}
    token = _CLOSURE_CACHE.set(cache)
    try:
        yield
        # FaT-9b: one full check of every journal the block read, so a copy changed inside the block is still caught.
        for project, root in list(cache.get("journal_roots", {}).values()):
            if (root / "events.jsonl").exists():
                _events_full(project, root)
    finally:
        _CLOSURE_CACHE.reset(token)


def closure_cached(function):
    """Run a public entry point inside one closure_cache() (FaT-9b); nested entry points reuse the outer one."""

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


def _validate_automation_model_lifecycle(
    run_root: Path,
    attempt_id: str,
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
    boundary: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Require the exact automation generator/reviewer lifecycle and readbacks."""
    automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")

    try:
        final_revision = automation["artifacts"]["automation_revision"]
        lifecycle = _model_lifecycle_projection_with_state(state, attempt_id)["stages"]
        prior_seq = 0
        for revision in range(1, final_revision + 1):
            generator_stage = f"tc-to-autotest:r{revision}"
            reviewer_stage = f"autotest-reviewer:r{revision}"
            generator_events = lifecycle[generator_stage]
            reviewer_events = lifecycle[reviewer_stage]
            if set(generator_events) != {
                "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED",
            } or set(reviewer_events) != {
                "REVIEW_REQUESTED", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED",
            }:
                raise ValueError
            generator_digest = str(generator_events["MODEL_RESPONSE_RECEIVED"]["artifact_digest"])
            reviewer_digest = str(reviewer_events["MODEL_RESPONSE_RECEIVED"]["artifact_digest"])
            project, root = _run_root(run_root)
            generator_request = _read_model_request_with_state(
                project, root, state, attempt_id, generator_stage,
                str(generator_events["MODEL_REQUESTED"]["artifact_digest"]),
            )
            generated = _read_model_stage_artifact_with_state(
                project, root, state, attempt_id, generator_stage, generator_digest,
            )["artifact"]
            reviewer_request = _read_model_request_with_state(
                project, root, state, attempt_id, reviewer_stage,
                str(reviewer_events["MODEL_REQUESTED"]["artifact_digest"]),
            )
            reviewed = _read_model_stage_artifact_with_state(
                project, root, state, attempt_id, reviewer_stage, reviewer_digest,
            )["artifact"]
            revision_boundary = boundary if revision == final_revision else _read_attempt_receipt_with_state(
                project, root, state, attempt_id,
                f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK",
            )["record"]
            boundary_events = [
                event for event in state["events"]
                if event.get("attempt_id") == attempt_id
                and event.get("artifact_digest") == revision_boundary.get("digest")
                and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
            ]
            ordered = [
                generator_events["MODEL_REQUESTED"],
                generator_events["MODEL_RESPONSE_RECEIVED"],
                generator_events["CANDIDATE_PUBLISHED"],
                *boundary_events,
                reviewer_events["REVIEW_REQUESTED"],
                reviewer_events["MODEL_REQUESTED"],
                reviewer_events["MODEL_RESPONSE_RECEIVED"],
            ]
            generated_digest = automation_sha256(generated)
            reviewed_digest = autotest_review_sha256(reviewed)
            source = generated["artifacts"]["source"]
            if (
                [event["event_type"] for event in boundary_events]
                != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
                or ordered[0]["seq"] <= prior_seq
                or [event["seq"] for event in ordered] != sorted(event["seq"] for event in ordered)
                or revision_boundary.get("automation_digest") != generated_digest
                or revision_boundary.get("automation_revision") != revision
                or revision_boundary.get("effective_canonical_digest") != source["source_digest"]
                or revision_boundary.get("effective_bundle_receipt_digest") != source["effective_bundle_receipt_digest"]
                or generator_digest != generated_digest
                or generator_events["CANDIDATE_PUBLISHED"].get("artifact_digest") != generated_digest
                or generator_request.get("invocation_id") != revision_boundary.get("generator_invocation_id")
                or generator_request.get("input_digests") != [
                    source["source_digest"], source["effective_bundle_receipt_digest"],
                ]
                or reviewer_events["REVIEW_REQUESTED"].get("artifact_digest") != generated_digest
                or reviewer_digest != reviewed_digest
                or reviewed["artifacts"]["autotest_review"].get("automation_sha256") != generated_digest
                or reviewer_request.get("invocation_id") != revision_boundary.get("reviewer_invocation_id")
                or reviewer_request.get("input_digests") != [generated_digest, revision_boundary.get("digest")]
                or revision == final_revision and (
                    generated != dict(automation) or reviewed != dict(review)
                )
            ):
                raise ValueError
            prior_seq = ordered[-1]["seq"]
        if final_revision not in {1, 2}:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("automation model lifecycle is absent or mismatched") from error


def model_transport_attempts_allowed(stage_instance_id: str, transport_attempts: Any) -> bool:
    """FaT-6: every model stage may repeat its call once after a schema or validation error of the answer."""
    return type(transport_attempts) is int and transport_attempts in {1, 2} and _MODEL_STAGE_INSTANCE.fullmatch(str(stage_instance_id)) is not None


def _append_event(project: Path, root: Path, event_type: str, *, actor: str, attempt_id: str | None, batch_id: str | None, artifact_digest: str | None, stage_instance_id: str | None = None, transport_attempts: int | None = None) -> dict[str, Any]:
    events = _events(project, root) if (root / "events.jsonl").exists() else []
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
    if event_type == "RUN_CREATED" and events:
        raise ValueError("invalid event")
    if event_type != "RUN_CREATED" and not events:
        raise ValueError("invalid event")
    if event_type == "MODULE_SELECTED" and existing is None and any(event["event_type"] == "MODULE_SELECTED" for event in events):
        raise ValueError("module already selected")
    if event_type in _ATTEMPT_EVENTS and attempt_id is None:
        raise ValueError("attempt event requires attempt id")
    if event_type in {"RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN"} and attempt_id is not None:
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
        raise ValueError("event publication failed") from error
    return event


@closure_cached  # FaT-9b
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


_REVIEW_STAGE = re.compile(r"^(?:tc-reviewer:canonical|autotest-reviewer:r[12])$")


@closure_cached  # FaT-10b
def request_review(run_root: Path, attempt_id: str, stage_instance_id: str, candidate_digest: str) -> Mapping[str, Any]:
    """FaT-10b: the one REVIEW_REQUESTED of a reviewer stage, on the exact candidate it reviews.

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
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id]
    recorded = [event for event in events if event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id]
    if recorded:
        if recorded[0].get("artifact_digest") == candidate_digest:
            return recorded[0]
        raise ValueError(f"REVIEW_ALREADY_REQUESTED: {stage_instance_id} already reviews {recorded[0].get('artifact_digest')}")
    source = "assembly" if stage_instance_id == "tc-reviewer:canonical" else stage_instance_id.replace("autotest-reviewer:", "tc-to-autotest:", 1)
    candidates = [event for event in events if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == source]
    if not any(event.get("artifact_digest") == candidate_digest for event in candidates):
        published = [str(event.get("artifact_digest")) for event in candidates]
        raise ValueError(f"REVIEW_CANDIDATE_NOT_PUBLISHED: {candidate_digest} is not the published {source} candidate {published}")
    return _append_event(project, root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id, batch_id=None,
                         artifact_digest=candidate_digest, stage_instance_id=stage_instance_id)


def claim_execution_start(run_root: Path, attempt_id: str, request_digest: str) -> Mapping[str, Any]:
    """Atomically reserve the one permitted project execution for an attempt."""
    project, root = _run_root(run_root)
    if not isinstance(request_digest, str) or not _DIGEST.fullmatch(request_digest):
        raise ValueError("invalid execution request digest")
    _manifest, authorization = _run_boundary(project, root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("invalid execution attempt")
    if attempt["policy_profile"] != "local-pilot-v1" or authorization.get("execution_requested") is not True:
        raise ValueError("execution authorization is invalid")
    if any(event["event_type"] == "EXECUTION_STARTED" and event.get("attempt_id") == attempt_id for event in state["events"]):
        raise ValueError("execution already started")
    return _append_event(
        project,
        root,
        "EXECUTION_STARTED",
        actor="controller",
        attempt_id=attempt_id,
        batch_id=None,
        artifact_digest=request_digest,
    )


def record_execution_unknown(run_root: Path, attempt_id: str, execution_receipt_digest: str) -> Mapping[str, Any]:
    """Bind the sole UNKNOWN marker to a read-back execution receipt and start."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or not _DIGEST.fullmatch(str(execution_receipt_digest)):
        raise ValueError("invalid execution-unknown attempt")
    try:
        receipt = read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("execution-unknown receipt is unavailable") from error
    starts = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_STARTED"
    ]
    unknowns = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_UNKNOWN"
    ]
    if (
        receipt.get("digest") != execution_receipt_digest
        or receipt.get("payload", {}).get("verdict") != "UNKNOWN"
        or len(starts) != 1
        or starts[0].get("artifact_digest") != receipt.get("execution_request_digest")
        or len(unknowns) > 1
        or unknowns and unknowns[0].get("artifact_digest") != execution_receipt_digest
    ):
        raise ValueError("execution-unknown evidence is invalid")
    return _append_event(
        project, root, "EXECUTION_UNKNOWN", actor="controller", attempt_id=attempt_id,
        batch_id=None, artifact_digest=execution_receipt_digest,
    )


def record_process_stopped(
    run_root: Path,
    attempt_id: str,
    execution_receipt_digest: str,
) -> Mapping[str, Any]:
    """Bind host-proved timeout scope stoppage; caller assertions are insufficient."""
    (consume_controller_process_stop_proof,) = execution_gate.from_mode_b("run_tests", "consume_controller_process_stop_proof")

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    try:
        receipt = read_attempt_receipt(
            root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("verified process-scope recovery is unavailable") from error
    unknowns = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") == "EXECUTION_UNKNOWN"
    ]
    stopped = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") == "PROCESS_STOPPED"
    ]
    if (
        attempt is None
        or not _DIGEST.fullmatch(str(execution_receipt_digest))
        or receipt.get("digest") != execution_receipt_digest
        or len(unknowns) != 1
        or unknowns[0].get("artifact_digest") != execution_receipt_digest
        or not _process_scope_stop_is_proved(receipt.get("payload", {}))
        or len(stopped) > 1
        or stopped and stopped[0].get("artifact_digest") != execution_receipt_digest
    ):
        raise ValueError("verified process-scope recovery is unavailable")
    if not consume_controller_process_stop_proof(receipt.get("payload", {})):
        raise ValueError("verified process-scope recovery is unavailable")
    return _append_event(
        project, root, "PROCESS_STOPPED", actor="controller", attempt_id=attempt_id,
        batch_id=None, artifact_digest=execution_receipt_digest,
    )


class ModulePathError(ValueError):
    """F5: ``module`` of an attempt is not a folder of the project; ``str`` is a reason that carries nothing of the project's text."""


_MODULE_PATH_CHARS = frozenset(":") | {chr(code) for code in range(32)}


def module_path_problem(project: Path, value: Any) -> str | None:
    """F5: why *value* cannot be ``module`` of an attempt — the one check of ``create_attempt``, ``ready`` and the schema's
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
    recorded = _moved_recorded(project)  # P6a: the digest binds the path where the run was made
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
        frozen_baseline = read_execution_baseline(root / "baselines" / baseline_name)
        inventory_name = str(frozen_baseline["inventory_digest"]).removeprefix("sha256:") + ".json"
        frozen_inventory = read_inventory_receipt(root / "inventories" / inventory_name)
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
        from tools.project_inventory import read_exclusion_receipt, read_execution_baseline, read_inventory_receipt
        frozen_inventory = read_inventory_receipt(inventory_target) if inventory_target.exists() else freeze_inventory_receipt(inventory_target, inventory)
        frozen_exclusions = read_exclusion_receipt(exclusion_target) if exclusion_target.exists() else freeze_exclusion_receipt(exclusion_target, exclusion)
        frozen_baseline = read_execution_baseline(baseline_target) if baseline_target.exists() else freeze_execution_baseline(baseline_target, baseline)
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
    receipt = read_inventory_receipt(root / "inventories" / (inventory_digest.removeprefix("sha256:") + ".json"))
    events = _events(project, root)
    if receipt.get("digest") != inventory_digest or not any(event["event_type"] == "INVENTORY_READY" and event.get("artifact_digest") == inventory_digest for event in events):
        raise ValueError("unbound frozen inventory")
    return receipt


class BaselineUnbound(ValueError):
    """F2 (A1-2): the baseline file under the attempt's name is not the baseline the attempt froze."""

    code = "BASELINE_UNBOUND"


def frozen_baseline(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any]:
    """F2 (A1-2): the execution baseline of *attempt* — the file named by ``attempt.baseline_digest``, whose own self-digest is that digest
    and which the journal froze (``EXECUTION_BASELINE_FROZEN`` on that digest).  Like ``_frozen_inventory``: a file that is only
    called by the right name, a consistent forged pair of baseline and inventory among them, is BASELINE_UNBOUND, not a baseline."""
    from tools.project_inventory import read_execution_baseline

    project, root = _run_root(run_root)
    digest = str(attempt.get("baseline_digest") or "")
    fix = "fix: начни новый прогон тем же scan с --new-run (baseline этого прогона не тот, что записан в попытке)"
    if not _DIGEST.fullmatch(digest):
        raise BaselineUnbound(f"BASELINE_UNBOUND: у попытки нет digest baseline; {fix}")
    try:
        baseline = read_execution_baseline(root / "baselines" / (digest.removeprefix("sha256:") + ".json"))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise BaselineUnbound(f"BASELINE_UNBOUND: baseline {digest} не читается ({type(error).__name__}); {fix}") from error
    frozen = any(event["event_type"] == "EXECUTION_BASELINE_FROZEN" and event.get("artifact_digest") == digest for event in derive_state(root)["events"])
    if baseline.get("digest") != digest or not frozen:
        raise BaselineUnbound(f"BASELINE_UNBOUND: файл baseline {digest} {'не заморожен журналом' if not frozen else 'не совпадает с собственным digest'}; {fix}")
    return baseline


def frozen_skillsrc_matches(project: Path, run_root: Path, attempt: Mapping[str, Any]) -> bool | None:
    """V3 (after V9a): do the bytes of the project's ``.skillsrc`` equal what the attempt's baseline froze (``skillsrc_authority``)?  The one path
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
    """FaT-5: ``(requirements (request, response) or None, [(batch stage, request, response)…] in batch order)``."""
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

    FaT-10c: a recorded request binds the receipts published before it; reviewer evidence published later is not its input."""
    cutoff = next((event["seq"] for event in state["events"] if event.get("attempt_id") == attempt_id
                   and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage_instance_id), None)
    return list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                              if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"
                              and (cutoff is None or event["seq"] < cutoff)))


def _context_marker_receipt_digests(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str, inputs: Sequence[str],
) -> list[str]:
    """The context receipts a marker request binds after requirements and inventory (FaT-5, after main P1).

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
        # FaT-P3: [inventory, receipt, requirements answer]; the runs before P3 also bound the documents digest first
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

    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    minimum = 2 if stage_instance_id == "context-marker:requirements" else 3
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    if stage_instance_id.startswith("context-marker:batch-") and len(inputs) == 3:
        head = head[1:]  # FaT-P3: a part binds the inventory, not the documents again (the answer of the requirements step does)
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
            raw = read_confined_bytes(project, project / "openspec", project / path)  # FaT-AU5: the project itself is no confined root
            if raw is None or "sha256:" + hashlib.sha256(raw).hexdigest() != item["content_digest"]:
                raise ValueError("context-marker OpenSpec source bytes drifted")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("context-marker OpenSpec source is not UTF-8") from error
            snapshots[path] = {"path": path, "sha256": item["content_digest"], "content": content}
    return list(snapshots.values())


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

        derived = run_marker_requirements(root, attempt, answer)  # FaT-IMP3: an import with documents
        if dict(value) != merge_marker_artifacts(answer, parts, derived):
            raise ValueError("merged context-marker artifact differs from the merge of its parts")
        from tools.cases_import import is_code_requirements

        if is_code_requirements(root, attempt, answer):
            return  # FaT-R2: the requirements of an import without documents are its cases, not an OpenSpec reading
        tolerated = _gap_marks(answer, derived)
    else:
        tolerated = set()
    if snapshots:
        diagnostics = _openspec_refusals(openspec_diagnostics(dict(value), snapshots), tolerated)
        if diagnostics:
            raise ValueError("CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: " + json.dumps(diagnostics, ensure_ascii=False))


_OPENSPEC_MISSING_TAIL = " — missing from normalized requirement/scenario set"


def _gap_marks(answer: Mapping[str, Any], derived: Sequence[Mapping[str, Any]] | None) -> set[str]:
    """FaT-IMP3: the provenance marks of the requirements of the answer the merged marker of an import leaves out — the gaps
    (a document requirement no case of the CSV links), named in the report; nothing for any other marker."""
    if derived is None:
        return set()
    kept = {item["source_requirement_id"] for item in derived}
    return {str(mark) for item in answer["artifacts"]["analytics_documentation"]["requirements"]
            if item["source_requirement_id"] not in kept for mark in item.get("provenance") or []}


def _openspec_refusals(diagnostics: Sequence[Mapping[str, str]], tolerated: set[str]) -> list[Mapping[str, str]]:
    """FaT-IMP3: the OpenSpec diagnostics that refuse the marker — every one but OPENSPEC_MISSING of a gap mark."""
    return [item for item in diagnostics if not (item["code"] == "OPENSPEC_MISSING" and item["message"].endswith(_OPENSPEC_MISSING_TAIL)
                                                 and item["message"][:-len(_OPENSPEC_MISSING_TAIL)] in tolerated)]


def _reconcile_requirements_openspec(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], request: Mapping[str, Any], value: Mapping[str, Any],
) -> None:
    """FaT-AU5 (audit D4): the merged marker keeps the requirements of this answer and adds the part warnings, so its OpenSpec
    reconciliation is decided here, against every receipt of the attempt — before the write, while the stage keeps its retry.
    Write time only: a journal written before reads as before; the merge keeps its own check."""
    from tools.build_context import openspec_diagnostics

    from tools.cases_import import is_code_requirements

    stage_instance_id = str(request["stage_instance_id"])
    if is_code_requirements(root, attempt, value):  # FaT-R2: the requirements of an import are its cases, by the CSV bytes
        return
    receipts = _published_context_receipts(state, str(attempt["attempt_id"]), stage_instance_id)
    snapshots = _context_marker_snapshots(project, root, state, attempt, stage_instance_id, request["input_digests"], receipts)
    diagnostics = openspec_diagnostics(dict(value), snapshots) if snapshots else []
    if diagnostics:
        raise ValueError(f"CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: {stage_instance_id}: "  # FaT-R2: a code
                         + json.dumps(diagnostics, ensure_ascii=False)
                         + "; fix: требования ответа — по OpenSpec из квитанций контекста; отдай исправленный ответ единственным "
                         "повтором стадии (--transport-attempts 2), ничего не записано")


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
    if stage == "tc-generator":
        from tools.project_inventory import read_execution_baseline

        markers = [event for event in events if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
        # FaT-BO: from batch 2 a fifth input — the capabilities of the earlier batches (prior-capabilities)
        # the exact list (FaT-DG, FaT-AU3: M context receipts) is checked at publish time by _expected_model_inputs
        if len(inputs) < 4 or len(markers) != 1 or markers[0]["seq"] >= cutoff or inputs[0] != markers[0].get("artifact_digest"):
            raise ValueError("tc-generator input digests must bind its prior marker, context, plan, and header")
        _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:baseline", inputs[0])
        context = read_context_selection(root, attempt_id, inputs[1])
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        selected = [event for event in events if event["event_type"] == "CONTEXT_SELECTED" and event.get("artifact_digest") == inputs[1] and event["seq"] < cutoff]
        if baseline["digest"] != attempt["baseline_digest"] or context["inventory_digest"] != baseline["inventory_digest"] or not selected:
            raise ValueError("tc-generator context must bind its prior attempt inventory selection")
    else:
        boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
        # F2: only the package binding is wanted (the same in every revision), so a latest revision still waiting for its READ_BACK will do
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
    if request["stage"] != "tc-generator":
        return
    inputs = request["input_digests"]
    context = read_context_selection(root, attempt_id, inputs[1])
    # FaT-AU3: plan and header follow the context receipts of the plan — one, or its list
    after = inputs.index(value.get("plan_digest"), 2) if value.get("plan_digest") in inputs[2:] else 2
    if (
        [value.get("context_receipt_digest"), value.get("plan_digest"), value.get("header_digest")] != [inputs[1], *inputs[after:after + 2]]
        or any(not _is_context_selection(root, attempt_id, digest) for digest in inputs[2:after])
        or value.get("context_receipt") != context
    ):
        raise ValueError("generator fragment must match its request and stored context receipt")


LEFT_MARK = "left-before-review.json"  # FaT-O12: finish --abandon LEFT_BEFORE_REVIEW


def left_attempt(run_root: Path) -> str | None:
    """FaT-O12b: the attempt the run was left at before the canonical review — only while it is still the run's last
    attempt (a later attempt is never hidden by an old mark); the mark is read confined."""
    project, root = _run_root(run_root)
    try:
        data = read_confined_bytes(project, root, root / LEFT_MARK)
        value = json.loads(data) if data is not None else None
    except (OutputConfinementError, ValueError):
        return None
    attempts = derive_state(root)["attempts"]
    if (
        not isinstance(value, Mapping) or value.get("reason") != "LEFT_BEFORE_REVIEW" or not attempts
        or value.get("attempt_id") != attempts[-1]["attempt_id"]
    ):
        return None
    return str(value["attempt_id"])


def _refuse_left(root: Path, attempt_id: str) -> None:
    """FaT-O12b: no model step on a left attempt — the plain scan of the request starts a new run."""
    if left_attempt(root) == attempt_id:
        raise ValueError("RUN_LEFT: прогон оставлен до ревью кейсов (finish --abandon LEFT_BEFORE_REVIEW); "
                         "fix: простой scan того же запроса начнёт новый")


@closure_cached  # FaT-9b
def publish_model_request(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    *,
    model_id: str | None,
    invocation_id: str,
    input_digests: Sequence[str],
    reuse_from: str | None = None,
) -> Mapping[str, Any]:
    """Persist and read back the complete model invocation envelope before its event.

    FaT-CR: a context-marker request records the skill_pack_digest of the package; ``reuse_from`` names an earlier run of the
    project whose answer for the same stage and the very same inputs is reused — checked here, recorded as ``reuse``.
    """
    project, root = _run_root(run_root)
    _refuse_left(root, attempt_id)
    state = derive_state(root)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    stage = stage_instance_id.split(":", 1)[0]
    declaration = _MODEL_STAGE_REGISTRY.get(stage)
    inputs = list(input_digests) if not isinstance(input_digests, (str, bytes)) else []
    journal_inputs = (not inputs and attempt is not None and attempt["state"] != "TERMINAL" and declaration is not None
                      and _MODEL_STAGE_INSTANCE.fullmatch(stage_instance_id) is not None)
    if journal_inputs:
        # audit U2: without --input-digest the inputs the journal fixes (FaT-DG); its refusals about an unpublished
        # predecessor stay as they are
        known = _expected_model_inputs(project, root, state, attempt, stage_instance_id)
        if known is None and stage in {"tc-to-autotest", "autotest-reviewer"}:
            known = _automation_inputs(project, root, state, attempt_id, stage_instance_id)
        inputs = [digest for digest, _source in known or ()]
    label_fix = ("метка из букв, цифр и . _ : / - (до 256 знаков), например deepseek-v4 или context-1; "
                 "без ключей, токенов и паролей")
    broken = next(((reason, fix) for failed, reason, fix in (
        (attempt is None, f"no attempt {attempt_id} in this run", "--attempt — attempt_id этого прогона из run_pipeline status"),
        (attempt is not None and attempt["state"] == "TERMINAL", "the attempt is closed (TERMINAL); a new request needs a new run",
         lambda: _new_run_fix(root)),  # only on this refusal: it reads the run manifest
        (_MODEL_STAGE_INSTANCE.fullmatch(stage_instance_id) is None, f"stage {stage_instance_id!r} is not <stage>:<instance>",
         "--stage <stage>:<instance>, например " + _STAGE_EXAMPLES),
        (declaration is None, f"unknown stage {stage!r}; known: {', '.join(sorted(_MODEL_STAGE_REGISTRY))}",
         "--stage одной из стадий " + ", ".join(sorted(_MODEL_STAGE_REGISTRY)) + ", например " + _STAGE_EXAMPLES),
        (model_id is not None and not _safe_model_label(model_id), "--model-id is not a safe label (letters, digits, . _ -, no secret-like value)",
         "--model-id — " + label_fix),
        (not _safe_model_label(invocation_id), "--invocation-id is not a safe label (letters, digits, . _ -, no secret-like value)",
         "--invocation-id — " + label_fix),
        (not inputs, f"MODEL_INPUTS_REQUIRED: the journal does not fix the inputs of {stage_instance_id}, no --input-digest given",
         "--input-digest на каждый вход стадии, в её порядке (для context-marker — из context_marker_parts plan; при неверном входе "
         "отказ MODEL_INPUTS_MISMATCH называет верный digest и его источник)"),
        (len(inputs) != len(set(inputs)), "an --input-digest is repeated", "каждый вход один раз, в порядке стадии"),
        (any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in inputs), "an --input-digest is not sha256:<64 hex>",
         "digest целиком, как его отдала команда публикации входа (sha256:<64 hex>, поле digest или content_digest её result)"),
    ) if failed), None)
    if broken is not None:  # FaT-AU1: the condition, not only «invalid model request»; FaT-H4b: and the way out
        fix = broken[1]() if callable(broken[1]) else broken[1]
        raise ModelRequestInvalid(f"invalid model request: {broken[0]}; fix: {fix}")
    if any(event["event_type"] == "MODEL_REQUESTED" and event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id
           for event in state["events"]):
        raise ModelRequestDuplicate(
            f"duplicate model request: {stage_instance_id} already has its request; fix: do not repeat model-request — the answer "
            "goes to model-response, its one retry is model-response --transport-attempts 2"
        )
    if reuse_from is not None and stage != "context-marker":
        raise ValueError("MODEL_REUSE_REFUSED: only a context-marker answer is reused")
    if stage == "context-marker":
        _context_marker_snapshots(project, root, state, attempt, stage_instance_id, inputs)
    # FaT-DG: the inputs the journal fixes, each wrong position named with its right digest and source; publish time only,
    # so a journal written before the generator inputs were published still replays
    expected = _expected_model_inputs(project, root, state, attempt, stage_instance_id)
    if expected is not None and inputs != [digest for digest, _source in expected]:
        raise _inputs_mismatch(stage_instance_id, inputs, expected)
    _validate_model_request_predecessors(project, root, state, attempt, stage_instance_id, invocation_id, inputs)
    if stage in {"tc-to-autotest", "autotest-reviewer"}:
        _require_next_automation_stage(run_root, attempt_id, stage_instance_id)  # FaT-RQ: publish time only
        if expected is None:  # FaT-AU5 (U1): FaT-DG named a wrong input above; a predecessor it could not read is refused by that read
            pairs = _automation_inputs(project, root, state, attempt_id, stage_instance_id)
            if inputs != [digest for digest, _source in pairs]:
                raise _inputs_mismatch(stage_instance_id, inputs, pairs)
    run_id = read_run(root)["manifest"]["run_id"]
    role, role_policy = declaration
    extra: dict[str, Any] = {}
    if stage == "context-marker":  # FaT-CR: the package the answer is made with, so a reuse can require the same package
        pack = skill_pack_digest()
        if pack is not None:
            extra["skill_pack_digest"] = pack
        if reuse_from is not None:
            extra["reuse"] = _reuse_source(project, root, attempt, stage_instance_id, inputs, role_policy, pack, reuse_from)
    nonce = None
    if stage == "tc-generator":  # P7: the control word of the generator's task — the run keeps its sha256 only
        from tools.review_canaries import nonce_digest

        nonce = secrets.token_hex(6)
        extra["nonce_sha256"] = nonce_digest(nonce)
    receipt = _sealed({
        "schema_version": "1.1.0" if extra else "1.0.0",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stage_instance_id": stage_instance_id,
        "stage": stage,
        "role": role,
        "role_policy": role_policy,
        "model_id": model_id,
        "invocation_id": invocation_id,
        "input_digests": inputs,
        **extra,
    })
    _validate_schema("model-request.schema.json", receipt)
    data = _canonical_bytes(receipt)
    target = _model_request_target(root, attempt_id, receipt["digest"])
    try:
        create_confined_bytes_exclusive(project, root, target, data)
        raw = read_confined_bytes(project, root, target)
        readback = _parse_json_bytes(raw, "model request") if raw is not None else None
    except (OutputConfinementError, OSError, ValueError) as error:
        raise ValueError("model request readback failed") from error
    if raw != data or readback != receipt:
        raise ValueError("model request readback mismatch")
    batch_id = stage_instance_id.split(":", 1)[1] if stage == "tc-generator" else None
    _append_event(
        project, root, "MODEL_REQUESTED", actor="controller", attempt_id=attempt_id,
        batch_id=batch_id, stage_instance_id=stage_instance_id,
        artifact_digest=receipt["digest"],
    )
    published = dict(read_model_request(root, attempt_id, stage_instance_id, receipt["digest"]))
    if nonce is not None:  # only in this output, never in a file: the agent puts it at the head of the generator's task
        published["nonce"] = nonce
        published["nonce_note"] = (f"вставь строку «Контрольное слово: {nonce}» в начало задачи генератору; в его ответ и в файлы прогона "
                                   "слово не копируй — прогон хранит только его sha256; вывод этой команды слово показывает один раз")
    if not str(model_id or "").startswith("controller:"):  # the merge by the code is no model step
        warning, measure_error = _record_step_size(project, root, run_id, stage_instance_id)
        if warning is not None:  # P1: a warning, never a refusal
            published["step_warning"] = warning
        if measure_error is not None:  # nor is a measurement that failed silent
            published["step_measure_error"] = measure_error
    return published


def publish_code_step_request(run_root: Path, attempt_id: str, stage_instance_id: str, *, model_id: str, invocation_id: str,
                              input_digests: Sequence[str]) -> Mapping[str, Any]:
    """F2 (A2-2): the request of a step the code answers itself (the merge of the marker parts, the import of cases).  A command cut
    between this request and the answer is run again and goes on from the request it wrote — the same model, invocation and inputs,
    no answer yet; any other request of the stage (or an answered stage) is what ``publish_model_request`` makes of it,
    MODEL_REQUEST_DUPLICATE."""
    project, root = _run_root(run_root)
    events = [event for event in derive_state(root)["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id]
    requests = [event for event in events if event["event_type"] == "MODEL_REQUESTED"]
    if len(requests) == 1 and not any(event["event_type"] == "MODEL_RESPONSE_RECEIVED" for event in events):
        request = read_model_request(root, attempt_id, stage_instance_id, str(requests[0]["artifact_digest"]))
        if (request.get("model_id") == model_id and request.get("invocation_id") == invocation_id
                and list(request.get("input_digests") or []) == list(input_digests)):
            return request
    return publish_model_request(run_root, attempt_id, stage_instance_id, model_id=model_id, invocation_id=invocation_id,
                                 input_digests=input_digests)


def _record_step_size(project: Path, root: Path, run_id: str, stage_instance_id: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """P1: the measurement of the step the request has just fixed, appended to ``step-sizes.jsonl`` (the report's section) —
    the same measure ``tools.step_size --run --stage`` makes, by the code at the one place every request passes, for every
    stage whose inputs the journal resolves.  Returns ``(warning, error)``: STEP_OVER_BUDGET is a warning and a measurement
    that cannot be taken or written is named (class of the error), neither stops the request."""
    from tools import step_size

    stage = stage_instance_id.split(":", 1)[0]
    try:
        found = step_size.request_files(root, stage_instance_id) if stage in step_size._REQUEST_STAGES else step_size.stage_inputs(root, stage_instance_id)
        state = derive_state(root)
        limits, _note = step_size.run_limits(project, root, state, str(state["attempts"][-1]["attempt_id"]))  # V9a: the limits the run froze
        report = step_size.measure(project, [Path(row["path"]) for row in found["files"]], stage, budget=step_size.step_budget(project, limits),
                                   extra_bytes=sum(row["bytes"] for row in found["bindings"]))
        step_size.record(project, run_id, stage_instance_id, report)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return None, {"code": "STEP_MEASURE_FAILED", "error": type(error).__name__}
    if report["fits"]:
        return None, None
    return {"code": step_size.OVER_BUDGET, "bytes": report["bytes"], "budget_bytes": report["budget_bytes"], "hint": report["hint"]}, None


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


def read_model_request(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    request_digest: str,
) -> Mapping[str, Any]:
    """Read one exact request envelope and verify its journal binding."""
    project, root = _run_root(run_root)
    return _read_model_request_with_state(
        project, root, derive_state(root), attempt_id, stage_instance_id, request_digest,
    )


def check_merged_marker(run_root: Path, attempt_id: str, input_digests: Sequence[str], value: Mapping[str, Any]) -> None:
    """FaT-O3: every check the write of the merged ``context-marker:baseline`` makes that needs no write — the schema and
    order, the inputs, the merge of the parts and OpenSpec — run by ``merge`` before it writes the request."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
    check_model_stage_artifact("context-marker:baseline", value)
    _validate_context_marker_sources(project, root, state, attempt, {"stage_instance_id": "context-marker:baseline",
                                                                     "input_digests": list(input_digests)}, value)


def check_model_stage_artifact(stage_instance_id: str, value: Mapping[str, Any]) -> None:
    """FaT-R2: the checks of an answer that need no journal — the requirement order and the schema of the stage — for code
    that builds an answer itself (cases-import), before it writes the request."""
    misplaced = _requirement_order_rows(stage_instance_id, value)
    if misplaced:
        raise ValueError(f"SOURCE_REQUIREMENT_ORDER: {stage_instance_id}: {misplaced}")
    _validate_model_stage_artifact(stage_instance_id, value)


def _validate_model_stage_artifact(stage_instance_id: str, value: Mapping[str, Any]) -> None:
    from tools.schema_validation import schema_diagnostics

    if stage_instance_id.startswith("context-marker:"):
        schema_name = "context-marker-output.schema.json"
        if value.get("stage") != "context-marker":
            raise ValueError("invalid context-marker artifact")
    elif stage_instance_id.startswith("tc-generator:"):
        schema_name = "candidate-fragment.schema.json"
        batch_id = stage_instance_id.split(":", 1)[1]
        body = {key: item for key, item in value.items() if key != "digest"}
        context = value.get("context_receipt")
        context_body = {
            key: item for key, item in context.items() if key != "digest"
        } if isinstance(context, Mapping) else {}
        if (
            value.get("batch_id") != batch_id
            or value.get("digest") != _model_stage_artifact_digest(body)
            or not isinstance(context, Mapping)
            or context.get("digest") != _model_stage_artifact_digest(context_body)
        ):
            raise ValueError("invalid generator fragment artifact")
    elif stage_instance_id == "tc-reviewer:canonical":
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
    if diagnostics:  # FaT-AU1: the same retry_prompt validate_artifact gives, so the one retry needs no second command
        from tools.validate_artifact import retry_prompt

        raise ModelStageArtifactInvalid(
            f"invalid model stage artifact: {stage_instance_id} does not pass {schema_name}: "
            + "; ".join(f"{row['path'] or '/'} {row['code']}" for row in diagnostics[:3])
            + (f" and {len(diagnostics) - 3} more" if len(diagnostics) > 3 else "")
            + "; fix: give the role its original input and this retry_prompt, then model-response --transport-attempts 2 — "
            "nothing is written; retry_prompt:\n" + retry_prompt(dict(value), diagnostics)
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


_RETRY_EXHAUSTED_FIX = ("fix: python -m tools.run_pipeline finish --project <project> --run <run_id> назовёт выход — "
                        "для стадии автоматизации finish --abandon STAGE_RETRY_EXHAUSTED, до ревью кейсов — scan --new-run")


def _validate_review_decision(root: Path, state: Mapping[str, Any], attempt_id: str, value: Mapping[str, Any]) -> None:
    """FaT-R1: the reviewer's decision against the requested candidate before the answer is written — select would refuse
    it after the ledger is closed, with no way out; here the stage keeps its one retry.  A run whose candidate the journal
    cannot assemble (before FaT-DG) is left to select as before."""
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import OrchestrationError, _run_imported, journal_assembly, review_decision

    requested = next((str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id
                      and event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == "tc-reviewer:canonical"), None)
    try:
        candidate = journal_assembly(root, attempt_id)[0]
    except (KeyError, OSError, TypeError, ValueError):
        return
    if requested is None or document_sha256(candidate) != requested:
        return
    from tools import review_canaries

    canaries = review_canaries.verify(root, attempt_id)  # P7: a map that does not match is a refusal, before anything is written
    if review_canaries.codeword_state(root, attempt_id, value) == "named" and not _self_review_accepted_at(_run_root(root)[0], root):
        raise ValueError("REVIEW_CODEWORD_NAMED: ревьюер назвал контрольное слово генератора — он видел задачу генератора, это ревью в той же "
                         "сессии; fix: проведи ревью в новой сессии (/clear или новое окно: «проведи ревью test-skills <run_id>») и подай её "
                         "ответ как есть; ничего не записано. Ревью в той же сессии допустимо, только если прогон начат с scan --accept-self-review")
    try:
        review_decision(candidate, value, imported=_run_imported(root, attempt_id), canaries=canaries)
    except OrchestrationError as error:
        rows = "; ".join(f"{row.get('path') or '/'} {row.get('code')}" for row in error.diagnostics[:8])
        raise ValueError(f"REVIEW_DECISION_INVALID: {rows}; fix: это ошибка ответа ревьюера — исправь решение и отдай его "
                         "единственным повтором стадии (--transport-attempts 2), ничего не записано") from None


_RETRY_SPENT = "повтор стадии исчерпан: ответ уже подан с --transport-attempts 2, третьего вызова модели нет; "


@closure_cached  # FaT-9b
def publish_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    artifact: Mapping[str, Any],
    *,
    transport_attempts: int = 1,
    raw_bytes: int | None = None,
    dry_run: bool = False,
) -> Mapping[str, Any]:
    """Persist a requested model output, then bind its response/candidate events.

    Audit 2 (FaT-AB): a refused answer given as the one retry (--transport-attempts 2) never advises that retry again — its
    fix is the way out of the stage.  The code does not count refused answers yet (AB2, after the release).

    P2: ``dry_run`` runs every check that precedes the write and returns ``{"dry_run": True, "artifact": <the answer as it would
    be stored>, "content_digest", "filled", "normalized"}`` — nothing is written, no event is appended."""
    try:
        return _publish_model_stage_artifact(run_root, attempt_id, stage_instance_id, artifact,
                                             transport_attempts=transport_attempts, raw_bytes=raw_bytes, dry_run=dry_run)
    except Exception as error:
        if transport_attempts == 2 and "--transport-attempts 2" in str(error):
            error.args = tuple(_retry_spent(arg) if isinstance(arg, str) and "--transport-attempts 2" in arg else arg for arg in error.args)
            if isinstance(getattr(error, "fix", None), str):
                error.fix = _RETRY_SPENT + _RETRY_EXHAUSTED_FIX.removeprefix("fix: ")
        raise


def recorded_model_answer(run_root: Path, attempt_id: str, stage_instance_id: str, artifact: Mapping[str, Any]) -> str:
    """P2: ``"none"`` when the stage has no recorded answer, ``"same"`` when ``artifact`` is the answer recorded (as ``model-response``
    would store it, the digest fields it puts filled in), ``"other"`` when another answer is recorded.  Reads only."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id]
    responses = [event for event in events if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    requests = [event for event in events if event["event_type"] == "MODEL_REQUESTED"]
    if not responses:
        return "none"
    value = dict(artifact)
    if stage_instance_id == "tc-reviewer:canonical":  # P7: as the answer is stored
        from tools.review_canaries import hash_codeword

        value = hash_codeword(value)
    if len(requests) == 1:
        prior = read_model_request(root, attempt_id, stage_instance_id, str(requests[0].get("artifact_digest", "")))
        if not isinstance(prior.get("reuse"), Mapping):
            try:
                value, _filled = _fill_response_bindings(project, root, state, attempt_id, stage_instance_id, prior, value)
            except (ValueError, KeyError, TypeError):
                return "other"
    digest = "sha256:" + hashlib.sha256(_model_stage_artifact_bytes(value)).hexdigest()
    return "same" if responses[0].get("artifact_digest") == digest else "other"


def _retry_spent(text: str) -> str:
    return text.split("; fix: ", 1)[0] + "; fix: " + _RETRY_SPENT + _RETRY_EXHAUSTED_FIX.removeprefix("fix: ")


def _publish_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    artifact: Mapping[str, Any],
    *,
    transport_attempts: int = 1,
    raw_bytes: int | None = None,
    dry_run: bool = False,
) -> Mapping[str, Any]:
    """Persist a requested model output, then bind its response/candidate events.

    FaT-O2: ``raw_bytes`` — the size of the answer as the model gave it (the ``--artifact`` file); the stored artifact is
    compact JSON, so without it the measurement falls back to the stored bytes.
    """
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    _refuse_left(root, attempt_id)  # FaT-O12b
    state = derive_state(root)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    value = dict(artifact)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("unknown model stage artifact attempt")
    # FaT-AB C5: one answer per stage, at most the one retry — --transport-attempts above 2 is refused before anything is
    # written (and so is replacing a recorded answer); refused answers are not counted yet (FaT-AB2), the agent keeps the limit
    if type(transport_attempts) is int and transport_attempts > 2:
        raise ValueError(f"MODEL_STAGE_RETRY_EXHAUSTED: {stage_instance_id}: у стадии один ответ и единственный повтор "
                         f"(--transport-attempts 1 или 2): --transport-attempts больше 2 отказан; ничего не записано; " + _RETRY_EXHAUSTED_FIX)
    requests = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("event_type") == "MODEL_REQUESTED"
    ]
    filled: list[str] = []
    normalized: list[str] = []
    mechanics: list[str] = []
    if stage_instance_id.startswith("tc-generator:"):  # P1: the order of requirement_ids and categories is the code's work, as in validate-fragment
        from tools.batch_assembly import fill_fragment_mechanics, normalize_fragment

        value, normalized = normalize_fragment(value)
        value, mechanics = fill_fragment_mechanics(value)  # V6 / O-12: ids, display_order, defaults, the types the code can take
    if stage_instance_id == "tc-reviewer:canonical":  # P7: the control word the reviewer names is kept as its sha256 only
        from tools.review_canaries import hash_codeword

        value = hash_codeword(value)
    if len(requests) == 1:  # audit U2: the digest fields the journal fixes and the answer leaves out — put by the code
        prior = read_model_request(root, attempt_id, stage_instance_id, str(requests[0].get("artifact_digest", "")))
        if not isinstance(prior.get("reuse"), Mapping):  # FaT-CR: a reused answer is passed as it is
            if (stage_instance_id.startswith("tc-generator:") and isinstance(value, dict) and value.get("status") == "COMPLETE"
                    and value.get("context_receipt") in (None, "")):
                # V6: the context receipt of the batch — by the digest the published plan binds to this batch (never the digest the answer
                # names: a different one is the refusal of the digest binding)
                try:
                    batch = next(row for row in _generator_input(project, root, state, attempt_id, "plan")["batches"]
                                 if row.get("batch_id") == stage_instance_id.partition(":")[2])
                    receipt = read_context_selection(root, attempt_id, str(batch["context_receipt_digest"]))
                    value, mechanics = {**value, "context_receipt": dict(receipt)}, [*mechanics, "/context_receipt"]
                except (KeyError, StopIteration, OSError, TypeError, ValueError):
                    pass  # no plan, no such batch or no receipt: the checks name it
            value, filled = _fill_response_bindings(project, root, state, attempt_id, stage_instance_id, prior, value)
    data = _model_stage_artifact_bytes(value)
    content_digest = "sha256:" + hashlib.sha256(data).hexdigest()
    responses = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
    ]
    if (
        len(requests) != 1
        or len(responses) > 1
        or responses and (
            responses[0].get("artifact_digest") != content_digest
            or responses[0].get("transport_attempts") != transport_attempts
        )
    ):
        if len(requests) == 1 and len(responses) == 1:
            raise ValueError(f"MODEL_STAGE_ANSWERED: {stage_instance_id}: ответ стадии уже записан, другой ответ его не заменит; "
                             "ничего не записано; " + _RETRY_EXHAUSTED_FIX)
        if not requests:  # FaT-AU1
            if stage_instance_id == "tc-reviewer:canonical":  # P2-fix: the request of the reviewer is the work of review-open
                from tools.fixes import REVIEW_CHAIN

                raise ValueError(f"MODEL_STAGE_NOT_REQUESTED: {stage_instance_id}: model stage artifact requires its exact prior request — "
                                 "the stage has none; fix: запрос ревьюера пишет review-open (повтори его с тем же --invocation-id, если он уже был), "
                                 "руками model-request не нужен — " + REVIEW_CHAIN)
            raise ValueError(f"MODEL_STAGE_NOT_REQUESTED: {stage_instance_id}: model stage artifact requires its exact prior request — "
                             "the stage has none; fix: tools.api model-request for this stage first, then model-response")
        raise ValueError("model stage artifact requires its exact prior request")
    request = read_model_request(
        root, attempt_id, stage_instance_id, str(requests[0].get("artifact_digest", "")),
    )
    reuse = request.get("reuse")
    if isinstance(reuse, Mapping) and content_digest != reuse["response_digest"]:  # FaT-CR: a reused answer is not changed
        source = _model_stage_artifact_target(_run_root(Path(root).parent / str(reuse["run_id"]))[1], str(reuse["attempt_id"]), str(reuse["response_digest"]))
        raise ModelReuseMismatch(
            f"MODEL_REUSE_MISMATCH: {stage_instance_id}: ответ {content_digest}, а запрос переиспользует ответ {reuse['response_digest']} "
            f"прогона {reuse['run_id']}; fix: передай файл ответа источника как есть — --artifact {source}, ничего не записано")
    # FaT-DG: digest fields of the answer against the journal before anything is written; the stage keeps its one retry
    mismatches = _response_digest_rows(project, root, state, attempt_id, stage_instance_id, request, value)
    if mismatches:
        raise ModelResponseDigestMismatch(
            f"MODEL_RESPONSE_DIGEST_MISMATCH: {stage_instance_id}: "
            + "; ".join(f"{pointer}: дан {given}, ожидается {expected} — {source}" for pointer, given, expected, source in mismatches)
            + "; fix: " + delete_field_fix(", ".join(pointer for pointer, *_rest in mismatches))
            + "; это ошибка ответа — отдай его единственным повтором стадии (--transport-attempts 2), ничего не записано"
        )
    # FaT-SN: source requirements follow their documents; only a new answer is checked, journals already written read as before
    misplaced = _requirement_order_rows(stage_instance_id, value)
    if misplaced:
        raise ValueError(
            f"SOURCE_REQUIREMENT_ORDER: {stage_instance_id}: {misplaced}; fix: display_order и номера SREQ — требования одного "
            "документа подряд, внутри документа по номеру строки как числу (…md:4 раньше …md:12); отдай исправленный ответ единственным повтором стадии (--transport-attempts 2), "
            "ничего не записано")
    _validate_model_stage_artifact(stage_instance_id, value)
    if stage_instance_id.startswith("tc-generator:"):
        from tools.batch_assembly import BatchAssemblyError, require_batch_coverage, validate_fragment

        try:
            published = _published_generator_inputs(project, root, state, attempt_id)
            if published is None:
                # reachable only for a request published before FaT-DG: a new model-request without the published plan and
                # header is refused by _expected_model_inputs (FaT-HARD, audit 1, 5g); such a run keeps the coverage rule
                require_batch_coverage(value)
            else:  # FaT-DG: the whole fragment check (coverage inside) against the journal, before the write
                validate_fragment(value, dict(published[0]), dict(published[1]), dict(read_context_selection(root, attempt_id, str(request["input_digests"][1]))),
                                  _accepted_earlier_fragments(project, root, state, attempt_id, published[0], stage_instance_id.split(":", 1)[1]),
                                  imported=_imported_for_answer(project, root, state, attempt_id, stage_instance_id.split(":", 1)[1], request))
        except BatchAssemblyError as error:
            retry = "отдай исправленный фрагмент единственным повтором стадии (--transport-attempts 2), ничего не записано"
            rows = getattr(error, "diagnostics", ())  # P1: the whole list, each row with its fix
            raise BatchAssemblyError(error.code, error.detail + "; fix: исправь фрагмент и " + retry, rows,
                                     (getattr(error, "fix", None) + "; " if getattr(error, "fix", None) else "исправь фрагмент и ") + retry) from error
    if stage_instance_id.startswith("context-marker:"):
        _validate_context_marker_sources(project, root, state, attempt, request, value)
    if stage_instance_id == "context-marker:requirements":
        _reconcile_requirements_openspec(project, root, state, attempt, request, value)
        from tools.cases_import import check_requirements_answer

        check_requirements_answer(root, attempt, value)  # FaT-IMP3: the links of an import with documents (by the run, not a label)
    if stage_instance_id == "tc-reviewer:canonical":
        _validate_review_decision(root, state, attempt_id, value)
    _validate_generator_artifact_binding(root, attempt_id, request, value)
    if dry_run:  # P2: every check above ran; nothing below is written
        return {"dry_run": True, "artifact": value, "content_digest": content_digest, "filled": [*mechanics, *filled], "normalized": normalized}
    target = _model_stage_artifact_target(root, attempt_id, content_digest)
    try:
        create_confined_bytes_exclusive(project, root, target, data)
        raw = read_confined_bytes(project, root, target)
        readback = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("model stage artifact readback failed") from error
    if raw != data or readback != value:
        raise ValueError("model stage artifact readback mismatch")
    batch_id = value.get("batch_id") if stage_instance_id.startswith("tc-generator:") else None
    append_event(
        root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
        batch_id=batch_id, stage_instance_id=stage_instance_id,
        artifact_digest=content_digest, transport_attempts=transport_attempts,
    )
    if stage_instance_id.startswith(("tc-generator:", "tc-to-autotest:")):
        append_event(
            root, "CANDIDATE_PUBLISHED", actor="controller", attempt_id=attempt_id,
            batch_id=str(batch_id) if batch_id is not None else None,
            stage_instance_id=stage_instance_id,
            artifact_digest=content_digest,
        )
    published = dict(read_model_stage_artifact(root, attempt_id, stage_instance_id, content_digest))
    # FaT-O: the answer size of every call, and a warning (never a stop) when it is large
    from tools.step_size import output_warning, record_output, run_limits

    limits, _limits_note = run_limits(project, root, state, attempt_id)  # V9a: the output limit of the .skillsrc the run froze
    measured = int(raw_bytes) if raw_bytes is not None else published["byte_count"]
    if raw_bytes is not None:
        record_output(project, root, stage_instance_id, content_digest, measured)
    published.update(output_bytes=measured, output_basis="raw" if raw_bytes is not None else "compact",
                     warning=output_warning(project, measured, limits))
    if mechanics or filled:  # audit U2 / V6: what the code put into the answer — the mechanics by name and count, then the digest fields
        published["filled"] = [*mechanics, *filled]
    if normalized:  # P1: what the code put in order
        published["normalized"] = normalized
    return published


def _read_model_stage_artifact_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    """P2: inside one closure a stage answer is read and checked once — its bytes are content-addressed and its request and
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


def read_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    """Read exact content-addressed model output bytes and revalidate their stage identity."""
    project, root = _run_root(run_root)
    return _read_model_stage_artifact_with_state(
        project, root, derive_state(root), attempt_id, stage_instance_id, content_digest,
    )


@closure_cached  # FaT-9b
def publish_context_selection(run_root: Path, attempt_id: str, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back one inventory-owned exact C-lite byte-set receipt."""
    from tools.project_inventory import ContextSelectionError, freeze_context_receipt, validate_context_receipt_binding
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    value = dict(receipt)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("unknown context selection attempt")
    inventory = _frozen_inventory(project, root, str(value.get("inventory_digest", "")))
    try:
        validate_context_receipt_binding(inventory, project, value)
        target = _context_selection_target(root, attempt_id, str(value.get("digest", "")))
        if target.exists():
            raw = target.read_bytes()
            readback = loads_json_strict(raw.decode("utf-8"))
            if readback != value:
                raise ValueError("context selection readback mismatch")
        else:
            readback = freeze_context_receipt(target, value)
    except (ContextSelectionError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("invalid context selection receipt") from error
    batch_id = f"review-{readback['batch_index']:04d}"
    append_event(root, "CONTEXT_SELECTED", actor="controller", attempt_id=attempt_id, batch_id=batch_id, artifact_digest=readback["digest"])
    return read_context_selection(root, attempt_id, readback["digest"])


def context_progress(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """F3: the context batches the scan has written for the attempt against the count its receipts carry (``batch_count``,
    bound by the journal through the receipt digest): ``{"have", "total"}``.  ``total`` is None before the first batch and for a
    run from before F3 (no count) — the set is then taken as whole.  Only reads the receipt files and the journal."""
    from tools.project_inventory import _digest
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    state = state if state is not None else derive_state(root)
    digests = list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                                 if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"))
    total = None
    for digest in digests:
        try:
            receipt = loads_json_strict(_context_selection_target(root, attempt_id, digest).read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, StrictJsonError, ValueError):
            continue  # a receipt file that cannot be read is the business of the read that needs it (read_context_selection)
        if isinstance(receipt, dict) and receipt.get("digest") == digest and _digest({k: v for k, v in receipt.items() if k != "digest"}) == digest:
            count = receipt.get("batch_count")
            if type(count) is int and (total is None or count > total):
                total = count
    return {"have": len(digests), "total": total}


def context_incomplete(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> tuple[int, int] | None:
    """F3: ``(have, total)`` when the scan of the attempt stopped before its last context batch, else None."""
    progress = context_progress(run_root, attempt_id, state)
    return (progress["have"], progress["total"]) if progress["total"] is not None and progress["have"] < progress["total"] else None


def scan_incomplete_message(have: int, total: int) -> str:
    return (f"SCAN_INCOMPLETE: scan записал {have} из {total} пачек контекста — разметка и план по неполному контексту не строятся; "
            "fix: повтори тот же scan (те же --module, --docs, --profile) — он достроит недостающие пачки в этом прогоне")


def read_context_selection(run_root: Path, attempt_id: str, receipt_digest: str) -> Mapping[str, Any]:
    """Read a C-lite receipt only when immutable bytes and attempt event both bind it.  P2: once per closure — the receipt is
    content-addressed and its event is in the journal for good."""
    kept = _CLOSURE_CACHE.get()
    key = ("context-selection", str(run_root), attempt_id, receipt_digest)
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    receipt = _read_context_selection_checked(run_root, attempt_id, receipt_digest)
    if kept is not None:
        kept[key] = copy.deepcopy(receipt)
    return receipt


def _read_context_selection_checked(run_root: Path, attempt_id: str, receipt_digest: str) -> Mapping[str, Any]:
    from tools.project_inventory import ContextSelectionError, validate_context_receipt_binding
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    target = _context_selection_target(root, attempt_id, receipt_digest)
    try:
        raw = target.read_bytes()
        receipt = loads_json_strict(raw.decode("utf-8"))
        inventory = _frozen_inventory(project, root, str(receipt.get("inventory_digest", "")))
        validate_context_receipt_binding(inventory, project, receipt)
    except (OSError, UnicodeDecodeError, StrictJsonError, ContextSelectionError) as error:
        raise ValueError("invalid context selection readback") from error
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    batch_id = f"review-{receipt['batch_index']:04d}"
    event = next((item for item in state["events"] if item["event_type"] == "CONTEXT_SELECTED" and item.get("attempt_id") == attempt_id and item.get("batch_id") == batch_id and item.get("artifact_digest") == receipt_digest), None)
    if attempt is None or event is None or receipt.get("digest") != receipt_digest:
        raise ValueError("unbound context selection receipt")
    return receipt


class ReviewLedgerUnbound(ValueError):
    """F2 (A2-1): a reviewer ledger file lies without its events and is not the revision being published."""

    code = "REVIEW_LEDGER_UNBOUND"


def publish_reviewer_session_ledger(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> Mapping[str, Any]:
    """Publish/read back one append-only reviewer-ledger revision under its attempt."""
    from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    value = dict(session)
    if attempt is not None and attempt["state"] == "TERMINAL":  # P1: not a bare ValueError — the attempt is closed
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
    pending: set[str] = set()  # F2: the revision this call publishes, found on disk with its events cut short — finished below
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
                raise ReviewLedgerUnbound(  # F2 (A2-1): a file of another revision without its events — not ours to finish
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
    try:
        created, _identity = create_confined_bytes_exclusive(project, root, target, data, return_identity=True)
        raw = read_confined_bytes(project, root, target)
        readback = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("reviewer session ledger readback failed") from error
    if readback != value or raw != data:
        raise ValueError("reviewer session ledger readback mismatch")
    if digest not in recorded_publish_digests:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, batch_id="reviewer-ledger-v1", artifact_digest=digest)
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, batch_id="reviewer-ledger-v1", artifact_digest=digest)
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
    # F2 (A2-1): the latest revision is the last one PUBLISHED; the commands that finish a cut publication (``unconfirmed_ok``) read it
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
    if not verified and _self_review_accepted_at(project, root):
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
    directories = {"phase1-artifact": "phase1-artifacts", "structured-result": "structured-results", "reviewer-session-boundary": "reviewer-session-boundaries", "effective-canonical": "effective-canonicals", "automation-review-boundary-r1": "automation-review-boundaries", "automation-review-boundary-r2": "automation-review-boundaries", "execution-inputs": "execution-inputs", "materialization-ownership": "materialization-ownership", "generated-delta": "generated-deltas", "execution-receipt": "execution-receipts", "resume-validation": "resume-validations", "execution-trace": "execution-traces", "trace-audit": "trace-audits", "disposition-plan": "disposition-plans", "disposition-receipt": "disposition-receipts", "batch-plan": "batch-plans", "generator-header": "generator-headers", "reference-cases": "reference-cases", "review-canaries": "review-canaries"}
    if kind.startswith(_SKELETON_KIND):  # FaT-SKB: the skeleton of one batch, next to the whole one
        if not re.fullmatch(r"BATCH-[a-z0-9-]+", kind[len(_SKELETON_KIND):]) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid factual receipt")
        return root / "imported-cases" / f"{attempt_id}.{kind[len(_SKELETON_KIND):]}.json"
    if kind == "imported-cases":  # FaT-IMP: the skeleton of an import, one per attempt
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ValueError("invalid factual receipt")
        return root / "imported-cases" / f"{attempt_id}.json"
    if kind.startswith(_PRIOR_KIND):  # FaT-BO: one receipt per batch, in its own folder
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


def _phase5_disposition_rows(delta: Mapping[str, Any]) -> list[dict[str, Any]]:
    files = delta.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("invalid generated delta receipt")
    rows: list[dict[str, Any]] = []
    for row in files:
        if not isinstance(row, Mapping):
            raise ValueError("invalid generated delta receipt")
        try:
            rows.append({key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")})
        except KeyError as error:
            raise ValueError("invalid generated delta receipt") from error
    return rows


def _phase5_delta_digest(delta: Mapping[str, Any]) -> str:
    body = {key: value for key, value in delta.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_generated_delta_ownership(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    delta: Mapping[str, Any],
) -> None:
    try:
        ownership = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]), "materialization-ownership",
            "ARTIFACT_READ_BACK",
        )["record"]["payload"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid generated delta receipt") from error
    if not isinstance(ownership, Mapping) or delta.get("digest") != _phase5_delta_digest(delta) or any(delta.get(key) != ownership.get(key) for key in ("module", "test_root", "baseline_digest", "automation_digest", "review_digest", "effective_canonical_digest", "effective_bundle_receipt_digest")):
        raise ValueError("invalid generated delta receipt")
    files, ownership_files = delta.get("files"), ownership.get("files")
    if not isinstance(files, list) or not isinstance(ownership_files, list) or len(files) != len(ownership_files):
        raise ValueError("invalid generated delta receipt")
    file_ids: set[str] = set()
    folded_paths: set[str] = set()
    prefix = str(delta.get("test_root", "")).rstrip("/") + "/"
    for row, owned in zip(files, ownership_files):
        if not isinstance(row, Mapping) or not isinstance(owned, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
            raise ValueError("invalid generated delta receipt")
        path = row["path"].replace("\\", "/")
        expected_ownership = "sha256:" + hashlib.sha256(json.dumps({"baseline": delta.get("baseline_digest"), "automation": delta.get("automation_digest"), "review": delta.get("review_digest"), "path": row["path"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if row["file_id"] in file_ids or path.casefold() in folded_paths or not path.startswith(prefix) or Path(path).is_absolute() or ".." in Path(path).parts or row.get("ownership_digest") != expected_ownership or any(row.get(key) != owned.get(key) for key in ("file_id", "path", "content_digest", "baseline_absent", "ownership_digest")) or any(row.get(key) != delta.get(key) for key in ("baseline_digest", "automation_digest", "review_digest")):
            raise ValueError("invalid generated delta receipt")
        file_ids.add(row["file_id"])
        folded_paths.add(path.casefold())


def _execution_request_from_payload(payload: Mapping[str, Any]) -> Any | None:
    """Reconstruct only the closed, process-significant request recorded by V5."""
    (ExecutionRequest,) = execution_gate.from_mode_b("execution_adapters", "ExecutionRequest")

    execution = payload.get("execution")
    environment = payload.get("environment")
    if not isinstance(execution, Mapping) or not isinstance(environment, Mapping):
        raise ValueError("invalid execution receipt")
    request_digest_value = execution.get("request_digest")
    if request_digest_value is None:
        empty = {
            "adapter_id": None, "executable_path": None, "cwd": None, "build_profile": None,
            "typed_parameters": {}, "argv": [], "selectors": [], "timeout_seconds": None,
            "report_paths": [], "request_digest": None, "report_evidence": [],
            "artifact_evidence": [], "run_id": None,
            "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None,
        }
        if any(execution.get(key) != value for key, value in empty.items()):
            raise ValueError("invalid execution receipt")
        return None
    typed = execution.get("typed_parameters")
    labels = environment.get("safe_key_labels")
    if not isinstance(typed, Mapping) or not isinstance(labels, list):
        raise ValueError("invalid execution receipt")
    try:
        return ExecutionRequest(
            adapter_id=execution["adapter_id"],
            executable=execution["executable_path"],
            argv=tuple(execution["argv"]),
            cwd=execution["cwd"],
            selectors=tuple(execution["selectors"]),
            timeout_seconds=execution["timeout_seconds"],
            report_paths=tuple(execution["report_paths"]),
            environment_labels=tuple(labels),
            build_profile=execution["build_profile"],
            typed_parameters=tuple(sorted((str(key), str(value)) for key, value in typed.items())),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid execution receipt") from error


def _validate_durable_execution_artifacts(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    payload: Mapping[str, Any],
    delta: Mapping[str, Any],
) -> None:
    """Verify the run-scoped bytes already bound into the execution receipt."""
    (is_zero_test_report,) = execution_gate.from_mode_b("execution_adapters", "is_zero_test_report")
    DURABLE_NATIVE_REPORT_MAX_BYTES, DURABLE_NATIVE_REPORT_NORMALIZATION, DurableNativeReportError, _normalize_durable_junit_report = execution_gate.from_mode_b(
        "run_tests", "DURABLE_NATIVE_REPORT_MAX_BYTES", "DURABLE_NATIVE_REPORT_NORMALIZATION", "DurableNativeReportError", "_normalize_durable_junit_report",
    )

    execution = payload["execution"]
    rows = execution.get("artifact_evidence")
    if not isinstance(rows, list):
        raise ValueError("invalid execution receipt")
    if payload.get("run_id") is None:
        if rows:
            raise ValueError("invalid execution receipt")
        return
    attempt_id = str(attempt["attempt_id"])
    prefix = f"artifacts/{attempt_id}/"
    observed: list[tuple[Mapping[str, Any], bytes]] = []
    paths: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError("invalid execution receipt")
        relative = Path(row["path"])
        if (
            row["path"] in paths
            or "\\" in row["path"]
            or relative.is_absolute()
            or ".." in relative.parts
            or not row["path"].startswith(prefix)
        ):
            raise ValueError("invalid execution receipt")
        data = read_confined_bytes(project, root, root / relative)
        digest = "sha256:" + hashlib.sha256(data).hexdigest() if data is not None else None
        if digest != row.get("digest"):
            raise ValueError("invalid execution receipt")
        paths.add(row["path"])
        observed.append((row, data))
    reports = {
        (row.get("path"), row.get("digest"))
        for row in execution.get("report_evidence", ())
        if isinstance(row, Mapping)
    }
    native = {
        (row.get("source_path"), row.get("source_digest"))
        for row, _data in observed if row.get("kind") == "native_report"
    }
    expected_generated = {
        (row.get("path"), row.get("content_digest"))
        for row in delta.get("files", ())
        if isinstance(row, Mapping) and row.get("materialization") == "MATERIALIZED"
    }
    generated = {
        (row.get("source_path"), row.get("digest"))
        for row, _data in observed if row.get("kind") == "generated_test"
    }
    runner_outputs = [row for row, _data in observed if row.get("kind") == "runner_output"]
    for row, data in observed:
        kind = row.get("kind")
        if kind == "native_report":
            try:
                canonical = _normalize_durable_junit_report(data)
            except DurableNativeReportError as error:
                raise ValueError("invalid execution receipt") from error
            if (
                not row["path"].startswith(prefix + "reports/")
                or row.get("normalization") != DURABLE_NATIVE_REPORT_NORMALIZATION
                or len(data) > DURABLE_NATIVE_REPORT_MAX_BYTES
                or data != canonical
            ):
                raise ValueError("invalid execution receipt")
        if kind == "generated_test" and row.get("path") != prefix + "generated/" + str(row.get("source_path")):
            raise ValueError("invalid execution receipt")
        if kind == "runner_output":
            try:
                output = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("invalid execution receipt") from error
            if row["path"] != prefix + "runner-output.txt" or _canonical_runner_output(output) != data:
                raise ValueError("invalid execution receipt")
    if payload.get("verdict") in {"PASS", "FAIL"} and (
        not reports or native != reports or generated != expected_generated
    ):
        raise ValueError("invalid execution receipt")
    if payload.get("verdict") == "FAIL" and len(runner_outputs) != 1:
        raise ValueError("invalid execution receipt")
    zero_collection = any(
        isinstance(row, Mapping) and row.get("kind") == "NO_TESTS_COLLECTED"
        for row in payload.get("process_evidence", ())
    )
    if zero_collection and (
        payload.get("verdict") != "FAIL"
        or not native
        or not all(
            is_zero_test_report(data)
            for row, data in observed if row.get("kind") == "native_report"
        )
    ):
        raise ValueError("invalid execution receipt")


def _validate_closed_execution_request(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    payload: Mapping[str, Any],
    request: Any,
    delta: Mapping[str, Any],
) -> None:
    GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, SAFE_ENVIRONMENT_LABELS, command_for, request_digest = execution_gate.from_mode_b(
        "execution_adapters", "GRADLE", "MAVEN", "SYSTEM_MAVEN", "PYTEST", "SAFE_ENVIRONMENT_LABELS", "command_for", "request_digest",
    )
    from tools.project_inventory import module_runtime_path, read_execution_baseline, system_maven_path

    execution = payload["execution"]
    environment = payload["environment"]
    target = payload["target"]
    module = project if attempt["module"] == "." else project.joinpath(*str(attempt["module"]).split("/"))
    try:
        module = module.resolve()
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        runtime_relative = baseline["interpreter_path"] if request.adapter_id == PYTEST else baseline["executable_path"] if request.adapter_id == SYSTEM_MAVEN else baseline["wrapper_path"]
        expected_executable = system_maven_path(runtime_relative) if request.adapter_id == SYSTEM_MAVEN else module_runtime_path(module, runtime_relative)
        executable = system_maven_path(request.executable) if request.adapter_id == SYSTEM_MAVEN else module_runtime_path(module, Path(request.executable).relative_to(module).as_posix())
        cwd = Path(request.cwd).resolve()
        for report_path in request.report_paths:
            raw = Path(report_path)
            if raw.is_absolute() or ".." in raw.parts or "\x00" in report_path:
                raise ValueError("invalid execution receipt")
            (module / raw).resolve().relative_to(module)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("invalid execution receipt") from error
    if (
        request.adapter_id != baseline.get("adapter_id")
        or request.build_profile != baseline.get("build_profile")
        or dict(request.typed_parameters) != baseline.get("adapter_parameters")
        or executable != expected_executable
        or cwd != module
        or request.timeout_seconds != 600
        or not request.selectors
        or len(request.selectors) != len(set(request.selectors))
        or set(request.environment_labels) - SAFE_ENVIRONMENT_LABELS
        or tuple(request.environment_labels) != ("PROJECT_NATIVE_ENV",)
        or request_digest(request) != execution.get("request_digest")
        or execution.get("run_id") != attempt["run_id"]
        or execution.get("attempt_id") != attempt["attempt_id"]
        or execution.get("baseline_digest") != attempt["baseline_digest"]
        or execution.get("generated_delta_digest") != delta.get("digest")
        or environment.get("working_dir") != str(module)
        or environment.get("interpreter_path") != str(expected_executable)
        or environment.get("interpreter") != str(expected_executable)
        or environment.get("status") != "ready"
        or environment.get("missing") is not None
        or target.get("command") != request.adapter_id
    ):
        raise ValueError("invalid execution receipt")
    expected_reports, expected_argv = command_for(request.adapter_id, request.executable, request.build_profile, request.selectors)
    expected_target = {
        PYTEST: ("python", "pytest", "pytest"),
        MAVEN: ("java", "junit5", "maven"),
        SYSTEM_MAVEN: ("java", "junit5", "maven"),
        GRADLE: ("java", "junit5", "gradle"),
    }
    if request.report_paths != expected_reports or (
        target.get("language"), target.get("framework"), target.get("runner")
    ) != expected_target.get(request.adapter_id):
        raise ValueError("invalid execution receipt")
    if request.argv != expected_argv:
        raise ValueError("invalid execution receipt")
    for row in execution.get("report_evidence", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError("invalid execution receipt")
        evidence_path = Path(row["path"])
        if evidence_path.is_absolute() or ".." in evidence_path.parts or "\x00" in row["path"]:
            raise ValueError("invalid execution receipt")
        normalized = row["path"].replace("\\", "/")
        roots = tuple(value.rstrip("/") for value in request.report_paths)
        if not any(normalized == value or normalized.startswith(value + "/") for value in roots):
            raise ValueError("invalid execution receipt")
    _validate_durable_execution_artifacts(project, root, attempt, payload, delta)


def _validate_factual_receipt(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    kind: str,
    receipt: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    common = {"schema_version", "kind", "run_id", "attempt_id", "policy_profile", "digest"}
    identity_matches = (
        receipt.get("kind") == kind
        and receipt.get("run_id") == attempt["run_id"]
        and receipt.get("attempt_id") == attempt["attempt_id"]
        and receipt.get("policy_profile") == attempt["policy_profile"]
    )
    if kind == "phase1-artifact":
        if not common <= set(receipt) or not identity_matches:
            raise ValueError("invalid factual receipt")
        if set(receipt) != common | {"module_selection_digest", "baseline_digest"} or not isinstance(receipt.get("module_selection_digest"), str) or not isinstance(receipt.get("baseline_digest"), str) or receipt["module_selection_digest"] != module_selection_digest(project, attempt["module"]) or receipt["baseline_digest"] != attempt["baseline_digest"]:
            raise ValueError("invalid phase1 artifact")
    elif kind == "reference-cases":  # FaT-REF: the parsed reference CSV of scan --reference, a hint source only
        reference = receipt.get("reference")
        if (
            set(receipt) != common | {"reference"}
            or not identity_matches
            or not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256", "cases"}
            or not isinstance(reference.get("path"), str)
            or not _DIGEST.fullmatch(str(reference.get("sha256", "")))
            or not isinstance(reference.get("cases"), list)
        ):
            raise ValueError("invalid reference-cases receipt")
    elif kind == "review-canaries":  # P7: the digests of the canary map and of the reviewer's view, bound to the assembled candidate
        digests = ("candidate_digest", "view_digest", "canary_map_digest")
        if (
            set(receipt) != common | {*digests, "canary_count"}
            or not identity_matches
            or any(not _DIGEST.fullmatch(str(receipt.get(name, ""))) for name in digests)
            or type(receipt.get("canary_count")) is not int
            or not 0 <= receipt["canary_count"] <= 3
            or not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("attempt_id") == attempt["attempt_id"]
                       and event.get("stage_instance_id") == "assembly" and event.get("artifact_digest") == receipt["candidate_digest"]
                       for event in state["events"])
        ):
            raise ValueError("invalid review-canaries receipt")
    elif kind in _GENERATOR_INPUT_KINDS.values():  # FaT-DG: the plan or the header with its one self-digest
        field = next(name for name, value in _GENERATOR_INPUT_KINDS.items() if value == kind)
        value = receipt.get(field)
        if (
            set(receipt) != common | {field}
            or not identity_matches
            or not isinstance(value, Mapping)
            or value.get("digest") != _generator_input_digest(value)
            or field == "plan" and not _plan_batches_listed(value)
        ):
            raise ValueError(f"invalid {kind} receipt")
    elif kind == "imported-cases":  # FaT-IMP: the skeleton is the one the CSV bound by the inventory gives (FaT-IMP3: and the links)
        from tools.cases_import import run_skeleton

        found = run_skeleton(root, attempt)
        if set(receipt) != common | {"imported"} or not identity_matches or found is None or receipt.get("imported") != found:
            raise ValueError("invalid imported-cases receipt")
    elif kind.startswith(_SKELETON_KIND):  # FaT-SKB: the slice the whole skeleton and the published plan give, nothing else
        if (
            set(receipt) != common | {"imported"}
            or not identity_matches
            or receipt.get("imported") != _batch_skeleton_value(project, root, state, str(attempt["attempt_id"]), kind[len(_SKELETON_KIND):])
        ):
            raise ValueError(f"invalid {kind} receipt")
    elif kind.startswith(_PRIOR_KIND):  # FaT-BO: the value is the one the journal gives, nothing else
        if (
            set(receipt) != common | {"prior_capabilities"}
            or not identity_matches
            or receipt.get("prior_capabilities") != _prior_capabilities_value(project, root, state, str(attempt["attempt_id"]), kind[len(_PRIOR_KIND):])
        ):
            raise ValueError(f"invalid {kind} receipt")
    elif kind == "structured-result":
        expected = terminal_result({
            "run_id": attempt["run_id"], "attempt_id": attempt["attempt_id"],
            "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
        }, attempt["policy_profile"])
        if dict(receipt) != dict(expected):
            raise ValueError("invalid structured result")
    elif kind == "reviewer-session-boundary":
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
            or not _DIGEST.fullmatch(receipt.get("canonical_branch_digest", ""))
            or not _DIGEST.fullmatch(receipt.get("package_digest", ""))
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
            or not _DIGEST.fullmatch(isolation.get("evidence_digest", ""))
            or isolation.get("evidence_digest") != isolation_digest
        ):
            raise ValueError("invalid reviewer session boundary")
    elif kind == "effective-canonical":
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
                # FaT-CV: the successor is the candidate with the reviewer's exact edits; undo them to reach the candidate digest.
                # P7: the edits of a canary text were dropped before the successor was made — they are dropped here too.
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
            review_bound = (
                isinstance(package_binding, Mapping)
                and effective_review_is_bound(
                    package_binding.get("candidate_digest"),
                    dict(document) if isinstance(document, Mapping) else document,
                    dict(report) if isinstance(report, Mapping) else report,
                    dict(successor) if isinstance(successor, Mapping) else successor,
                    dict(verdict_event) if isinstance(verdict_event, Mapping) else verdict_event,
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
                    or _self_review_accepted_at(project, root)
                )
                and ledger["host_isolation"].get("role_policy") == "canonical-reviewer-v1"
                and isinstance(ledger.get("events"), list)
                and sum(event.get("event_type") == "AUTHORITATIVE_VERDICT" for event in ledger["events"] if isinstance(event, Mapping)) == 1
                and next(event for event in ledger["events"] if isinstance(event, Mapping) and event.get("event_type") == "AUTHORITATIVE_VERDICT").get("verdict") == "ACCEPTED"
                and bool(ledger["events"])
                and isinstance(ledger["events"][-1], Mapping)
                and ledger["events"][-1].get("event_type") == "REVIEW_SESSION_COMPLETED"
            )
        except (ImportError, UnicodeDecodeError, StrictJsonError, ValueError, OutputConfinementError):
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
    elif kind in {"automation-review-boundary-r1", "automation-review-boundary-r2"}:
        expected_keys = common | {
            "automation_digest", "automation_revision", "reviewer_session_id", "generator_invocation_id",
            "reviewer_invocation_id", "role_policy", "host_isolation", "effective_canonical_digest", "effective_bundle_receipt_digest",
        }
        isolation = receipt.get("host_isolation")
        isolation_body = {key: value for key, value in isolation.items() if key != "evidence_digest"} if isinstance(isolation, Mapping) else {}
        isolation_digest = "sha256:" + hashlib.sha256(json.dumps(isolation_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        try:
            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
        except (KeyError, TypeError, ValueError):
            effective = None
        if (
            set(receipt) != expected_keys
            or not identity_matches
            or not _DIGEST.fullmatch(receipt.get("automation_digest", ""))
            or type(receipt.get("automation_revision")) is not int
            or receipt.get("automation_revision") != int(kind[-1])
            or not all(_safe_label(receipt.get(key)) for key in ("reviewer_session_id", "generator_invocation_id", "reviewer_invocation_id"))
            or receipt.get("generator_invocation_id") == receipt.get("reviewer_invocation_id")
            or receipt.get("role_policy") != "autotest-static-reviewer-v1"
            or not _DIGEST.fullmatch(receipt.get("effective_canonical_digest", ""))
            or not _DIGEST.fullmatch(receipt.get("effective_bundle_receipt_digest", ""))
            or not isinstance(effective, Mapping)
            or receipt.get("effective_canonical_digest") != effective.get("document_digest")
            or receipt.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
            or not isinstance(isolation, Mapping)
            or set(isolation) != {"fresh_context", "distinct_invocations", "evidence_digest"}
            or not isinstance(isolation.get("fresh_context"), bool)
            or not isinstance(isolation.get("distinct_invocations"), bool)
            or not (isolation["fresh_context"] and isolation["distinct_invocations"] or _self_review_accepted_at(project, root))
            or not _DIGEST.fullmatch(isolation.get("evidence_digest", ""))
            or isolation.get("evidence_digest") != isolation_digest
        ):
            raise ValueError("invalid automation review boundary")
    elif kind == "execution-inputs":
        automation_sha256, validate_accepted_autotest_review = execution_gate.from_mode_b(
            "automation_validation", "automation_sha256", "validate_accepted_autotest_review",
        )
        from tools.schema_validation import schema_diagnostics

        automation = receipt.get("automation_artifact")
        review = receipt.get("autotest_review")
        _validate_schema("execution-inputs-receipt.schema.json", receipt)
        if (
            set(receipt) != common | {"automation_artifact", "autotest_review"}
            or not identity_matches
            or not isinstance(automation, Mapping)
            or not isinstance(review, Mapping)
            or schema_diagnostics(dict(automation), _SCHEMA_ROOT / "tc-to-autotest-output.schema.json", _PACK_ROOT)
            or schema_diagnostics(dict(review), _SCHEMA_ROOT / "autotest-reviewer-output.schema.json", _PACK_ROOT)
        ):
            raise ValueError("invalid execution inputs receipt")
        try:
            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
            revision = review["artifacts"]["autotest_review"]["automation_revision"]
            boundary = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK",
            )["record"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid execution inputs receipt") from error
        _validate_automation_model_lifecycle(
            root, str(attempt["attempt_id"]), automation, review, boundary, state,
        )
        if (
            automation_sha256(automation) != review["artifacts"]["autotest_review"].get("automation_sha256")
            or automation.get("artifacts", {}).get("source", {}).get("source_digest") != effective.get("document_digest")
            or validate_accepted_autotest_review(
                review, automation, effective["document"], host_isolation_receipt=boundary,
                run_root=root, attempt_id=str(attempt["attempt_id"]),
            )
        ):
            raise ValueError("invalid execution inputs receipt")
    elif kind == "generated-delta":
        delta = receipt.get("delta")
        from tools.schema_validation import schema_diagnostics

        if (
            set(receipt) != common | {"delta"}
            or not identity_matches
            or not isinstance(delta, Mapping)
            or schema_diagnostics(dict(delta), _SCHEMA_ROOT / "generated-delta.schema.json", _PACK_ROOT)
            or delta.get("baseline_digest") != attempt.get("baseline_digest")
            or delta.get("module") != attempt.get("module")
        ):
            raise ValueError("invalid generated delta receipt")
        try:
            automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b(
                "automation_validation", "automation_sha256", "autotest_review_sha256",
            )

            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
            execution_inputs = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                "execution-inputs", "ARTIFACT_READ_BACK",
            )["record"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid generated delta receipt") from error
        if (
            delta.get("effective_canonical_digest") != effective.get("document_digest")
            or delta.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
            or automation_sha256(execution_inputs["automation_artifact"]) != delta.get("automation_digest")
            or autotest_review_sha256(execution_inputs["autotest_review"]) != delta.get("review_digest")
        ):
            raise ValueError("invalid generated delta receipt")
        _validate_generated_delta_ownership(project, root, state, attempt, delta)
    elif kind == "execution-receipt":
        (request_digest,) = execution_gate.from_mode_b("execution_adapters", "request_digest")
        from tools.schema_validation import schema_diagnostics

        payload = receipt.get("payload")
        expected_keys = common | {
            "baseline_digest", "generated_delta_digest", "execution_request_digest", "payload",
        }
        if (
            set(receipt) != expected_keys
            or not identity_matches
            or receipt.get("baseline_digest") != attempt.get("baseline_digest")
            or not isinstance(payload, Mapping)
            or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "run-tests-output.schema.json", _PACK_ROOT)
        ):
            raise ValueError("invalid execution receipt")
        try:
            delta = _phase5_generated_delta(project, root, state, attempt)
            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
            source = payload["source"]
            execution = payload["execution"]
            document = effective["document"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid execution receipt") from error
        if (
            receipt.get("generated_delta_digest") != delta.get("digest")
            or receipt.get("execution_request_digest") != execution.get("request_digest")
            or payload.get("automation_sha256") != delta.get("automation_digest")
            or payload.get("autotest_review_sha256") != delta.get("review_digest")
            or source.get("document_id") != document.get("document_id")
            or source.get("revision") != document.get("revision")
            or source.get("source_digest") != effective.get("document_digest")
            or source.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
            or any(row.get("source_digest") != source.get("source_digest") for row in payload.get("execution_evidence", []))
        ):
            raise ValueError("invalid execution receipt")
        request = _execution_request_from_payload(payload)
        starts = [
            event for event in state["events"]
            if event.get("attempt_id") == attempt["attempt_id"] and event.get("event_type") == "EXECUTION_STARTED"
        ]
        prestart = payload.get("verdict") == "NOT_RUNNABLE" and payload.get("run_id") is None
        if prestart:
            if starts:
                raise ValueError("invalid execution receipt")
            if request is not None:
                _validate_closed_execution_request(project, root, attempt, payload, request, delta)
        else:
            if payload.get("verdict") not in {"PASS", "FAIL", "UNKNOWN"} or request is None:
                raise ValueError("invalid execution receipt")
            _validate_closed_execution_request(project, root, attempt, payload, request, delta)
            if len(starts) != 1 or starts[0].get("artifact_digest") != request_digest(request):
                raise ValueError("invalid execution receipt")
    elif kind == "resume-validation":
        _validate_schema("resume-validation-receipt.schema.json", receipt)
        expected_keys = common | {
            "baseline_digest", "execution_request_digest", "execution_receipt_digest",
            "pre_finalization_trace_digest", "status", "reason_code",
        }
        try:
            execution_record = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                "execution-receipt", "ARTIFACT_READ_BACK",
            )["record"]
            starts = [
                event for event in state["events"]
                if event.get("attempt_id") == attempt["attempt_id"]
                and event.get("event_type") == "EXECUTION_STARTED"
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid resume validation receipt") from error
        reason_code = receipt.get("reason_code")
        pretrace_digest = receipt.get("pre_finalization_trace_digest")
        pretrace_valid = pretrace_digest is None
        if isinstance(pretrace_digest, str):
            try:
                pretrace = _read_artifact(
                    project,
                    root,
                    root / "closure" / str(attempt["attempt_id"]) / "pre_finalization_trace.json",
                    "pre finalization trace",
                )
                pretrace_events = [
                    event["event_type"]
                    for event in state["events"]
                    if event.get("attempt_id") == attempt["attempt_id"]
                    and event.get("artifact_digest") == pretrace_digest
                    and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
                ]
                pretrace_valid = (
                    pretrace.get("digest") == pretrace_digest
                    and pretrace.get("resume_validation_digest") is None
                    and pretrace_events == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
                )
            except (KeyError, TypeError, ValueError, OSError):
                pretrace_valid = False
        if (
            set(receipt) != expected_keys
            or not identity_matches
            or receipt.get("baseline_digest") != attempt.get("baseline_digest")
            or receipt.get("execution_request_digest") != execution_record.get("execution_request_digest")
            or receipt.get("execution_receipt_digest") != execution_record.get("digest")
            or not pretrace_valid
            or execution_record.get("payload", {}).get("verdict") == "NOT_RUNNABLE"
            or len(starts) != 1
            or starts[0].get("artifact_digest") != execution_record.get("execution_request_digest")
            or receipt.get("status") != "DRIFTED"
            or reason_code not in {"BASELINE_DRIFT", "BASELINE_INCOMPLETE", "SKILLSRC_DRIFT"}
        ):
            raise ValueError("invalid resume validation receipt")
    elif kind == "execution-trace":
        automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")
        TraceBuildError, build_trace, trace_sha256 = execution_gate.from_mode_b(
            "build_trace_document", "TraceBuildError", "build_trace", "trace_sha256",
        )
        from tools.schema_validation import schema_diagnostics

        payload = receipt.get("payload")
        automation = receipt.get("automation_artifact")
        review = receipt.get("autotest_review")
        expected_keys = common | {
            "effective_canonical_digest", "generated_delta_digest", "execution_receipt_digest",
            "automation_artifact", "autotest_review", "trace_sha256", "payload",
        }
        try:
            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
            delta = _phase5_generated_delta(project, root, state, attempt)
            execution_record = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                "execution-receipt", "ARTIFACT_READ_BACK",
            )["record"]
            report = execution_record["payload"]
            review_revision = review["artifacts"]["autotest_review"]["automation_revision"]
            review_boundary = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                f"automation-review-boundary-r{review_revision}",
                "ARTIFACT_READ_BACK",
            )["record"]
            expected_trace = build_trace(
                effective["document"], automation, review, report,
                host_isolation_receipt=review_boundary, run_root=root,
                attempt_id=str(attempt["attempt_id"]),
            )
        except (KeyError, TypeError, ValueError, TraceBuildError) as error:
            raise ValueError("invalid execution trace receipt") from error
        if (
            set(receipt) != expected_keys
            or not identity_matches
            or not isinstance(automation, Mapping)
            or not isinstance(review, Mapping)
            or not isinstance(payload, Mapping)
            or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "trace-document.schema.json", _PACK_ROOT)
            or dict(payload) != expected_trace
            or receipt.get("trace_sha256") != trace_sha256(payload)
            or receipt.get("effective_canonical_digest") != effective.get("document_digest")
            or receipt.get("generated_delta_digest") != delta.get("digest")
            or receipt.get("execution_receipt_digest") != execution_record.get("digest")
            or automation_sha256(automation) != delta.get("automation_digest")
            or autotest_review_sha256(review) != delta.get("review_digest")
        ):
            raise ValueError("invalid execution trace receipt")
    elif kind == "trace-audit":
        from tools.schema_validation import schema_diagnostics
        (check,) = execution_gate.from_mode_b("trace_check", "check")

        payload = receipt.get("payload")
        expected_keys = common | {"execution_trace_receipt_digest", "trace_sha256", "payload"}
        try:
            trace_record = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                "execution-trace", "ARTIFACT_READ_BACK",
            )["record"]
            execution_record = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]),
                "execution-receipt", "ARTIFACT_READ_BACK",
            )["record"]
            effective = _read_effective_canonical_with_state(
                project, root, state, str(attempt["attempt_id"]),
            )
            expected_audit = check(trace_record["payload"], require_execution=True, canonical=effective["document"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid trace audit receipt") from error
        if (
            set(receipt) != expected_keys
            or not identity_matches
            or not isinstance(payload, Mapping)
            or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "trace-audit-output.schema.json", _PACK_ROOT)
            or dict(payload) != expected_audit
            or receipt.get("execution_trace_receipt_digest") != trace_record.get("digest")
            or receipt.get("trace_sha256") != trace_record.get("trace_sha256")
        ):
            raise ValueError("invalid trace audit receipt")
    elif kind in {"materialization-ownership", "disposition-plan", "disposition-receipt"}:
        payload = receipt.get("payload")
        if set(receipt) != common | {"payload"} or not identity_matches or not isinstance(payload, Mapping):
            raise ValueError(f"invalid {kind} receipt")
        from tools.schema_validation import schema_diagnostics
        if kind == "materialization-ownership":
            files = payload.get("files")
            test_root = payload.get("test_root")
            seen: set[tuple[str, str]] = set()
            invalid_file = not isinstance(files, list) or not isinstance(test_root, str)
            if not invalid_file:
                prefix = test_root.rstrip("/") + "/"
                for row in files:
                    if not isinstance(row, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
                        invalid_file = True
                        break
                    path = row["path"].replace("\\", "/")
                    identity = (row["file_id"], path.casefold())
                    expected_ownership = "sha256:" + hashlib.sha256(json.dumps({"baseline": payload.get("baseline_digest"), "automation": payload.get("automation_digest"), "review": payload.get("review_digest"), "path": row["path"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                    if identity in seen or not path.startswith(prefix) or Path(path).is_absolute() or ".." in Path(path).parts or row.get("ownership_digest") != expected_ownership:
                        invalid_file = True
                        break
                    seen.add(identity)
            try:
                automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b(
                    "automation_validation", "automation_sha256", "autotest_review_sha256",
                )

                effective = _read_effective_canonical_with_state(
                    project, root, state, str(attempt["attempt_id"]),
                )
                execution_inputs = _read_attempt_receipt_with_state(
                    project, root, state, str(attempt["attempt_id"]),
                    "execution-inputs", "ARTIFACT_READ_BACK",
                )["record"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid materialization ownership receipt") from error
            if schema_diagnostics(dict(payload), _SCHEMA_ROOT / "materialization-receipt.schema.json", _PACK_ROOT) or payload.get("baseline_digest") != attempt.get("baseline_digest") or payload.get("module") != attempt.get("module") or payload.get("effective_canonical_digest") != effective.get("document_digest") or payload.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest") or automation_sha256(execution_inputs["automation_artifact"]) != payload.get("automation_digest") or autotest_review_sha256(execution_inputs["autotest_review"]) != payload.get("review_digest") or invalid_file:
                raise ValueError("invalid materialization ownership receipt")
        else:
            files = payload.get("files")
            file_ids: set[str] = set()
            folded_paths: set[str] = set()
            invalid_file = not isinstance(files, list)
            if not invalid_file:
                for row in files:
                    if not isinstance(row, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
                        invalid_file = True
                        break
                    path = row["path"].replace("\\", "/")
                    if row["file_id"] in file_ids or path.casefold() in folded_paths or Path(path).is_absolute() or ".." in Path(path).parts:
                        invalid_file = True
                        break
                    file_ids.add(row["file_id"])
                    folded_paths.add(path.casefold())
            expected_payload = _sealed({key: value for key, value in payload.items() if key != "digest"})
            if schema_diagnostics(dict(payload), _SCHEMA_ROOT / "disposition-receipt.schema.json", _PACK_ROOT) or invalid_file or expected_payload != payload:
                raise ValueError(f"invalid {kind} receipt")
            delta = _phase5_generated_delta(project, root, state, attempt)
            expected_rows = _phase5_disposition_rows(delta)
            verification = payload.get("verification")
            evidence = payload.get("execution_unknown_evidence_digest")
            if payload.get("generated_delta_digest") != delta.get("digest") or payload.get("files") is None or verification not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"}:
                raise ValueError(f"invalid {kind} receipt")
            if (verification == "UNKNOWN") != isinstance(evidence, str) or (isinstance(evidence, str) and not _DIGEST.fullmatch(evidence)):
                raise ValueError(f"invalid {kind} receipt")
            rows = payload["files"]
            GeneratedDeltaError, resolve_disposition_policy = execution_gate.from_mode_b(
                "generated_delta", "GeneratedDeltaError", "resolve_disposition_policy",
            )

            quarantine = verification == "FAIL" and _disposition_policy_at(project, root) == "quarantine"

            if kind == "disposition-plan":
                if payload.get("stage") != "disposition-plan" or len(rows) != len(expected_rows):
                    raise ValueError("invalid disposition-plan receipt")
                for actual, expected in zip(rows, expected_rows):
                    if not isinstance(actual, Mapping):
                        raise ValueError("invalid disposition-plan receipt")
                    try:
                        operation, requested, _outcomes = resolve_disposition_policy(
                            str(verification),
                            str(expected.get("materialization")),
                            retain_pass=verification == "PASS" and actual.get("requested_disposition") == "RETAINED",
                            quarantine=quarantine,
                        )
                    except GeneratedDeltaError as error:
                        raise ValueError("invalid disposition-plan receipt") from error
                    relation = expected | {"operation": operation, "requested_disposition": requested}
                    quarantine_keys = {key for key in ("quarantine", "quarantined_digest", "quarantine_unresolved") if key in actual}
                    if (
                        any(actual.get(key) != relation.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent", "operation", "requested_disposition"))
                        or actual.get("pre_effect_state") not in ({"NOT_APPLICABLE"} if operation == "NO_FILE" else {"EXACT", "MISSING", "DRIFT", "UNSAFE"})
                        # FaT-8: a quarantine row is either unresolved or names its methods and their marked digest.
                        or operation != "QUARANTINE_IF_EXACT" and quarantine_keys
                        or operation == "QUARANTINE_IF_EXACT" and quarantine_keys not in ({"quarantine_unresolved"}, {"quarantine", "quarantined_digest"})
                        or operation == "QUARANTINE_IF_EXACT" and "quarantine" in actual and (actual["quarantine"] == []) != (actual["quarantined_digest"] is None)
                    ):
                        raise ValueError("invalid disposition-plan receipt")
            else:
                if payload.get("stage") != "dispositions":
                    raise ValueError("invalid disposition-receipt receipt")
                try:
                    plan = _read_attempt_receipt_with_state(
                        project, root, state, str(attempt["attempt_id"]), "disposition-plan",
                        "ARTIFACT_READ_BACK",
                    )["record"]["payload"]
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("invalid disposition-receipt receipt") from error
                if not isinstance(plan, Mapping) or payload.get("disposition_plan_digest") != plan.get("digest") or payload.get("generated_delta_digest") != plan.get("generated_delta_digest") or verification != plan.get("verification") or evidence != plan.get("execution_unknown_evidence_digest"):
                    raise ValueError("invalid disposition-receipt receipt")
                plan_rows = plan.get("files")
                if not isinstance(plan_rows, list) or len(rows) != len(plan_rows) or [
                    {key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")}
                    for row in rows
                ] != expected_rows:
                    raise ValueError("invalid disposition-receipt receipt")
                for final_row, plan_row in zip(rows, plan_rows):
                    if not isinstance(plan_row, Mapping) or any(final_row.get(key) != plan_row.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")):
                        raise ValueError("invalid disposition-receipt receipt")
                    try:
                        operation, requested, outcomes = resolve_disposition_policy(
                            str(verification), str(plan_row.get("materialization")),
                            retain_pass=verification == "PASS" and plan_row.get("requested_disposition") == "RETAINED",
                            quarantine=quarantine,
                        )
                    except GeneratedDeltaError as error:
                        raise ValueError("invalid disposition-receipt receipt") from error
                    if plan_row.get("operation") != operation or plan_row.get("requested_disposition") != requested:
                        raise ValueError("invalid disposition-receipt receipt")
                    legal = outcomes.get(str(plan_row.get("pre_effect_state")), frozenset())
                    if (final_row.get("disposition"), final_row.get("reason_code")) not in legal:
                        raise ValueError("invalid disposition-receipt receipt")
                    if operation == "QUARANTINE_IF_EXACT" and not {
                        "QUARANTINED": plan_row.get("quarantine") not in (None, [])
                        and final_row.get("quarantine") == plan_row.get("quarantine")
                        and final_row.get("quarantined_digest") == plan_row.get("quarantined_digest"),
                        "RETAINED": plan_row.get("quarantine") == [],
                        "CLEANED": plan_row.get("quarantine_unresolved") is True,
                    }.get(str(final_row.get("disposition")), True):
                        raise ValueError("invalid disposition-receipt receipt")
    else:
        raise ValueError("invalid factual receipt")


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
        if kind == "execution-receipt":
            if set(facts) != {"payload"} or not isinstance(facts.get("payload"), Mapping):
                raise ValueError("invalid execution receipt")
            try:
                delta = _phase5_generated_delta(project, root, state, attempt)
                execution = facts["payload"]["execution"]
                request_digest_value = execution["request_digest"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid execution receipt") from error
            facts = {
                "baseline_digest": attempt["baseline_digest"],
                "generated_delta_digest": delta["digest"],
                "execution_request_digest": request_digest_value,
                "payload": dict(facts["payload"]),
            }
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
                    "execution-inputs", "materialization-ownership", "generated-deltas", "execution-receipts", "resume-validations", "execution-traces",
                    "trace-audits", "disposition-plans", "disposition-receipts", "batch-plans", "generator-headers", "reference-cases", "review-canaries",
                    "imported-cases", "prior-capabilities")


def lost_attempt_receipts(root: Path, state: Mapping[str, Any], attempt_id: str) -> list[str]:
    """F1: the digests the journal read back as receipts of the attempt (``ARTIFACT_READ_BACK`` without a batch) that no receipt file of the
    run carries any more — deleted or replaced.  Reads files, writes nothing."""
    wanted = [str(event["artifact_digest"]) for event in state["events"]
              if event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") is None
              and isinstance(event.get("artifact_digest"), str)]
    if not wanted:
        return []
    present: set[str] = set()
    for folder in _RECEIPT_FOLDERS:
        for path in (Path(root) / folder).glob(f"{attempt_id}*.json"):
            try:
                value = _check_sealed(json.loads(path.read_text(encoding="utf-8")), "receipt")  # the digest field must be the digest of the body
            except (OSError, ValueError, TypeError):
                continue
            present.add(value["digest"])
    return sorted(dict.fromkeys(digest for digest in wanted if digest not in present))


def attempt_receipt_or_reason(run_root: Path, attempt_id: str, kind: str, event_type: str) -> Mapping[str, Any]:
    """F1: the answer of ``read-receipt`` — the receipt as ``read_attempt_receipt`` gives it, or ``{"receipt": None, "reason": …}`` while the
    journal says there is none yet.  A file that is there is read the strict way.  The journal binds a receipt by its digest alone (the
    events of a fixed receipt carry no kind), so «not yet» is decided only where the journal fixes it: the boundary of the reviewer
    session exists from ``review-open`` on, and ``review-open`` opens the ledger (events ``reviewer-ledger-v1``) — no such event, no boundary
    yet.  The journal knows the boundary but the file is gone — a refusal with a code and a fix; every other kind without its file is a
    refusal with a code and a fix too (no «not yet» is claimed for a kind whose events cannot be told apart)."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    if next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None) is None:
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, kind, event_type)  # unknown attempt: its refusal
    if _receipt_target(root, attempt_id, kind).exists():
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, kind, event_type)
    if kind == "reviewer-session-boundary" and not any(event.get("attempt_id") == attempt_id and event.get("batch_id") == "reviewer-ledger-v1" for event in state["events"]):
        return {"receipt": None, "reason": "ещё нет: ревью не открыто (граница ревьюера появляется на review-open)"}
    raise ValueError(f"RECEIPT_MISSING: the receipt {kind} of the attempt is not in the run" + (
        "; fix: журнал знает ревью, а файла границы нет — прогон повреждён, ничего не восстанавливай руками: run_pipeline status называет потерю, дальше scan --new-run"
        if kind == "reviewer-session-boundary" else
        f"; fix: квитанции {kind} в прогоне нет — её пишет код шага, который её создаёт; run_pipeline status называет, какой шаг следующий"))


def _publish_bound_attempt_receipt(
    run_root: Path,
    attempt_id: str,
    kind: str,
    facts: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Publish one fixed receipt and add each lifecycle binding at most once."""
    published = publish_attempt_receipt(
        run_root,
        attempt_id,
        kind,
        facts,
        _controller_token=(
            _DEDICATED_RECEIPT_TOKEN
            if kind in {"execution-inputs", "resume-validation"}
            else None
        ),
    )
    return _bind_published_attempt_receipt(run_root, attempt_id, kind, published)


class ReceiptFileUnbound(ValueError):
    """F2 (A2-4): the file of a fixed receipt lies without its events and its bytes are not the ones being published."""

    code = "RECEIPT_FILE_UNBOUND"


def _publish_fixed_receipt_resuming(run_root: Path, attempt_id: str, kind: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """F2 (A2-4): ``_publish_bound_attempt_receipt`` for a receipt a cut command may have left half done — the file there with the
    same bytes gets its missing events; the file there with other bytes is RECEIPT_FILE_UNBOUND (nothing written), not a bare
    read-back error."""
    try:
        return _publish_bound_attempt_receipt(run_root, attempt_id, kind, facts)
    except ValueError as error:
        _project, root = _run_root(run_root)
        if _receipt_target(root, attempt_id, kind).exists() and ("read-back mismatch" in str(error) or str(error).startswith("unsafe ")):
            raise ReceiptFileUnbound(
                f"RECEIPT_FILE_UNBOUND: файл {_receipt_target(root, attempt_id, kind).relative_to(root).as_posix()} лежит без событий журнала, а его "
                "байты не те, что публикуются сейчас; ничего не записано; fix: повтори команду с теми же входами, что дали файл (она допишет "
                "события), иные входы — новый прогон (scan --new-run)") from error
        raise


def _bind_published_attempt_receipt(
    run_root: Path,
    attempt_id: str,
    kind: str,
    published: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Append only the missing ordered events for one installed fixed receipt."""
    _project, root = _run_root(run_root)
    relevant = [
        event["event_type"]
        for event in derive_state(root)["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == published["digest"]
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if relevant == []:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    elif relevant == ["ARTIFACT_PUBLISHED"]:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    elif relevant != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        raise ValueError("invalid fixed receipt event order")
    readback = read_attempt_receipt(root, attempt_id, kind, "ARTIFACT_READ_BACK")
    return {**readback, "created": published["created"], "installed_identity": published["installed_identity"]}


def publish_execution_trace_pair(
    run_root: Path,
    attempt_id: str,
    automation_artifact: Mapping[str, Any],
    autotest_review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Build, persist, and read back the sole V5 execution trace and audit pair."""
    automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")
    TraceBuildError, build_trace, trace_sha256 = execution_gate.from_mode_b("build_trace_document", "TraceBuildError", "build_trace", "trace_sha256")
    (check,) = execution_gate.from_mode_b("trace_check", "check")

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or attempt.get("policy_profile") != "local-pilot-v1":
        raise ValueError("invalid execution trace attempt")
    try:
        existing_trace = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
        )
        existing_audit = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
        )
    except (KeyError, TypeError, ValueError):
        existing_trace = existing_audit = None
    if existing_trace is not None and existing_audit is not None:
        return {"trace": existing_trace, "audit": existing_audit}
    for later_kind in ("disposition-plan", "disposition-receipt"):
        try:
            later_bytes = read_confined_bytes(project, root, _receipt_target(root, attempt_id, later_kind))
        except OutputConfinementError as error:
            raise ValueError("unsafe execution trace lifecycle path") from error
        if later_bytes is not None:
            raise ValueError("execution trace must precede dispositions")
    try:
        effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
        delta = _phase5_generated_delta(project, root, state, attempt)
        execution_record = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        report = execution_record["payload"]
        if (
            automation_sha256(automation_artifact) != delta.get("automation_digest")
            or autotest_review_sha256(autotest_review) != delta.get("review_digest")
        ):
            raise ValueError("trace sources do not bind the generated delta")
        review_revision = autotest_review["artifacts"]["autotest_review"]["automation_revision"]
        review_boundary = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, f"automation-review-boundary-r{review_revision}",
            "ARTIFACT_READ_BACK",
        )["record"]
        trace = build_trace(
            effective["document"], automation_artifact, autotest_review, report,
            host_isolation_receipt=review_boundary, run_root=root, attempt_id=attempt_id,
        )
    except (KeyError, TypeError, ValueError, TraceBuildError) as error:
        raise ValueError("authoritative execution trace cannot be built") from error
    trace_receipt = _publish_bound_attempt_receipt(root, attempt_id, "execution-trace", {
        "effective_canonical_digest": effective["document_digest"],
        "generated_delta_digest": delta["digest"],
        "execution_receipt_digest": execution_record["digest"],
        "automation_artifact": dict(automation_artifact),
        "autotest_review": dict(autotest_review),
        "trace_sha256": trace_sha256(trace),
        "payload": trace,
    })
    audit = check(trace, require_execution=True, canonical=effective["document"])
    audit_receipt = _publish_bound_attempt_receipt(root, attempt_id, "trace-audit", {
        "execution_trace_receipt_digest": trace_receipt["digest"],
        "trace_sha256": trace_receipt["record"]["trace_sha256"],
        "payload": audit,
    })
    return {"trace": trace_receipt, "audit": audit_receipt}


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
        published = publish_attempt_receipt(run_root, attempt_id, "effective-canonical", value)
    except (AttributeError, ValueError) as error:
        raise ValueError("invalid effective canonical selection") from error
    if any(published["record"].get(key) != value.get(key) for key in value):
        raise ValueError("conflicting effective canonical selection")
    _project, root = _run_root(run_root)
    state = derive_state(root)
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("artifact_digest") == published["digest"]]
    published_event = any(event["event_type"] == "ARTIFACT_PUBLISHED" for event in events)
    readback_event = any(event["event_type"] == "ARTIFACT_READ_BACK" for event in events)
    if readback_event and not published_event:
        raise ValueError("invalid effective canonical event order")
    if not published_event:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    if not readback_event:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    return {**read_attempt_receipt(run_root, attempt_id, "effective-canonical", "ARTIFACT_READ_BACK"), "created": published["created"]}


def publish_execution_inputs(
    run_root: Path,
    attempt_id: str,
    automation_artifact: Mapping[str, Any],
    autotest_review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Persist exact reviewed execution/trace carriers before materialization."""
    expected = {
        "automation_artifact": dict(automation_artifact),
        "autotest_review": dict(autotest_review),
    }
    try:
        existing = read_attempt_receipt(
            run_root, attempt_id, "execution-inputs", "ARTIFACT_READ_BACK",
        )
    except (KeyError, TypeError, ValueError):
        existing = None
    if existing is not None:
        if any(existing["record"].get(key) != value for key, value in expected.items()):
            raise ValueError("conflicting execution inputs receipt")
        return {**existing, "created": False, "installed_identity": None}
    project, root = _run_root(run_root)
    try:
        installed = read_confined_bytes(
            project, root, _receipt_target(root, attempt_id, "execution-inputs"),
        )
    except OutputConfinementError as error:
        raise ValueError("unsafe execution-inputs receipt path") from error
    if installed is not None:
        recovered = recover_execution_inputs_events(root, attempt_id)
        if any(recovered["record"].get(key) != value for key, value in expected.items()):
            raise ValueError("conflicting execution inputs receipt")
        return {**recovered, "created": False, "installed_identity": None}
    if _execution_inputs_frontier_closed(run_root, attempt_id):
        raise ValueError("execution-inputs frontier is closed")
    return _publish_bound_attempt_receipt(
        run_root,
        attempt_id,
        "execution-inputs",
        expected,
    )


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


def publish_resume_validation(
    run_root: Path,
    attempt_id: str,
    reason_code: str,
) -> Mapping[str, Any]:
    """Freeze one controller-detected drift before terminal finalization."""
    if reason_code not in {"BASELINE_DRIFT", "BASELINE_INCOMPLETE", "SKILLSRC_DRIFT"}:
        raise ValueError("invalid resume validation reason")
    _project, root = _run_root(run_root)
    # ponytail: one controller owns a run; add run-wide arbitration only if
    # concurrent CLI resume is ever made a supported product mode.
    existing = read_resume_validation_if_present(root, attempt_id)
    if existing is not None:
        if existing.get("reason_code") != reason_code:
            raise ValueError("conflicting resume validation reason")
        return existing
    if read_closure_artifact_if_present(
        root, attempt_id, "finalization_receipt",
    ) is not None:
        raise ValueError("finalization receipt already froze resume validation")
    pretrace = read_closure_artifact_if_present(
        root, attempt_id, "pre_finalization_trace",
    )
    execution = read_attempt_receipt(
        root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    return _publish_bound_attempt_receipt(
        root,
        attempt_id,
        "resume-validation",
        {
            "baseline_digest": execution["baseline_digest"],
            "execution_request_digest": execution["execution_request_digest"],
            "execution_receipt_digest": execution["digest"],
            "pre_finalization_trace_digest": (
                pretrace.get("digest") if isinstance(pretrace, Mapping) else None
            ),
            "status": "DRIFTED",
            "reason_code": reason_code,
        },
    )


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


@closure_cached  # FaT-9b
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
    """PT2: the answer of ``read-effective`` — the effective selection, or ``{"effective": None, "reason": …}`` while the journal says there is none.

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
        return {"effective": None, "reason": "ещё нет: вердикт ревью не записан (review-select --verdict)"}
    not_yet = {"effective": None, "reason": "ещё нет: ответ ревьюера записан, ревизия не выбрана — повтори review-select (тот же --verdict или без него)"}
    # F2: the order of the writes of review-select decides what «not yet» is: the ledger revision COMPLETED is published BEFORE the effective
    # canonical (orchestrate_revision: validate_reviewer_session, then publish_effective_canonical).  So a latest revision still waiting for its
    # READ_BACK, a ledger still WAITING (no verdict), and a closed accepting ledger with no attempt-level receipt read back after it all mean the
    # effective canonical was not published yet — a repeat of review-select does it.  Only a published selection whose file is gone is a refusal.
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


def recover_execution_receipt_events(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Finish binding an installed execution receipt without replaying execution."""
    return _recover_attempt_receipt_events(run_root, attempt_id, "execution-receipt")


@closure_cached  # FaT-9b
def open_automation_review_boundary(run_root: Path, attempt_id: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back the controller-owned isolation boundary for one V5 revision."""
    revision = facts.get("automation_revision")
    if type(revision) is not int or revision not in {1, 2}:
        raise ValueError("invalid automation review boundary revision")
    state = derive_state(_run_root(run_root)[1])
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    run = read_run(run_root)
    if attempt is None:
        raise ValueError("unknown automation review boundary attempt")
    if revision == 2:
        # Revision two is a single pre-execution correction.  Durable ownership
        # is the first materialization boundary, before a final delta can exist.
        try:
            read_attempt_receipt(run_root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK")
        except ValueError:
            pass
        else:
            raise ValueError("automation revision two is forbidden after materialization")
        if attempt["policy_profile"] != "local-pilot-v1" or run["authorization"].get("execution_requested") is not True:
            raise ValueError("automation review boundary lacks local execution authorization")
    kind = f"automation-review-boundary-r{revision}"
    published = publish_attempt_receipt(run_root, attempt_id, kind, facts)
    _project, root = _run_root(run_root)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    readback = read_attempt_receipt(run_root, attempt_id, kind, "ARTIFACT_READ_BACK")
    return {**readback, "created": published["created"]}


class IndependenceUnverified(ValueError):
    """FaT-10d: the host states no fresh context in a run without ``scan --accept-self-review``."""

    code = "INDEPENDENCE_UNVERIFIED"


def require_fresh_context_statement(run_root: Path, fresh_context: bool) -> None:
    """FaT-10d: ``fresh_context`` is the host's statement; ``no`` needs SELF admitted at scan, ``yes`` is left to the protocol."""
    if type(fresh_context) is not bool:
        raise ValueError("fresh_context must be the host's yes or no")
    if not fresh_context and not self_review_accepted(run_root):
        raise IndependenceUnverified(
            "INDEPENDENCE_UNVERIFIED: ревьюер без свежего контекста, а прогон не принял SELF; fix: только если человек согласен "
            "на ревью в той же сессии, повтори scan этого запроса с --accept-self-review; иначе вызови ревьюера отдельным "
            "субагентом без истории генерации и укажи --fresh-context yes"
        )


@closure_cached  # FaT-10d
def automation_boundary_facts(run_root: Path, attempt_id: str, revision: int, *, fresh_context: bool, reviewer_invocation_id: str) -> dict[str, Any]:
    """The ``--facts`` of ``open-automation-boundary`` for revision N from the journal; writes nothing.

    The automation digest is the CANDIDATE_PUBLISHED of ``tc-to-autotest:rN``, the generator invocation its
    MODEL_REQUESTED, the effective pair the read-back effective canonical. ``fresh_context`` and the reviewer
    invocation are the host's statement; the code computes only ``distinct_invocations`` and the digest."""
    if type(revision) is not int or revision not in {1, 2}:
        raise ValueError("AUTOMATION_REVISION_INVALID: the automation revision is 1 or 2")
    require_fresh_context_statement(run_root, fresh_context)
    project, root = _run_root(run_root)
    state = derive_state(root)
    stage = f"tc-to-autotest:r{revision}"
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage]
    requested = [event for event in events if event["event_type"] == "MODEL_REQUESTED"]
    published = [event for event in events if event["event_type"] == "CANDIDATE_PUBLISHED"]
    if len(requested) != 1 or len(published) != 1:
        raise ValueError(f"AUTOMATION_RESPONSE_MISSING: {stage} has no recorded answer yet: tools.api model-response --stage {stage} first")
    generator = str(_read_model_request_with_state(project, root, state, attempt_id, stage, str(requested[0]["artifact_digest"]))["invocation_id"])
    effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
    reviewer = str(reviewer_invocation_id)
    isolation = {"fresh_context": fresh_context, "distinct_invocations": generator != reviewer}
    isolation["evidence_digest"] = "sha256:" + hashlib.sha256(json.dumps(isolation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "automation_digest": str(published[0]["artifact_digest"]), "automation_revision": revision,
        "reviewer_session_id": f"autotest-review-{attempt_id}-r{revision}", "generator_invocation_id": generator,
        "reviewer_invocation_id": reviewer, "role_policy": "autotest-static-reviewer-v1", "host_isolation": isolation,
        "effective_canonical_digest": effective["document_digest"], "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
    }


_GENERATOR_INPUT_KINDS = {"plan": "batch-plan", "header": "generator-header"}  # FaT-DG


def publish_reference_cases(run_root: Path, attempt_id: str, path: str, data: bytes, cases: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """FaT-REF: the reference cases of ``scan --reference`` (parsed by ``zephyr_csv``) as a receipt of the attempt; they
    never enter the context, the generator input or the canon."""
    facts = {"reference": {"path": path, "sha256": "sha256:" + hashlib.sha256(bytes(data)).hexdigest(), "cases": [dict(case) for case in cases]}}
    return _publish_bound_attempt_receipt(run_root, attempt_id, "reference-cases", facts)


def read_reference_cases(run_root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    """FaT-REF: the reference of the attempt, None when the scan had no ``--reference``."""
    try:
        return read_attempt_receipt(run_root, attempt_id, "reference-cases", "ARTIFACT_READ_BACK")["record"]["reference"]
    except (ValueError, OSError, KeyError):
        return None
_PRIOR_KIND = "prior-capabilities-"  # FaT-BO: + batch_id — the fifth tc-generator input from batch 2
_SKELETON_KIND = "imported-cases-"  # FaT-SKB: + batch_id — the skeleton of the batch, its last tc-generator input


def _batch_ordinal(batch: Mapping[str, Any]) -> int:
    """FaT-BO: the ordinal of a published plan batch; a fixture plan without one is a first batch (four inputs)."""
    return batch["ordinal"] if type(batch.get("ordinal")) is int else 1


def _accepted_earlier_fragments(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, plan: Mapping[str, Any],
                                batch_id: str) -> list[dict[str, Any]]:
    """FaT-BO: the accepted fragments (model-response) of the batches before *batch_id*; one missing is refused by name."""
    rows = [row for row in plan.get("batches") or [] if isinstance(row, Mapping)]
    current = next((row for row in rows if row.get("batch_id") == batch_id), None)
    if current is None:
        return []
    fragments: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in sorted((row for row in rows if _batch_ordinal(row) < _batch_ordinal(current)), key=_batch_ordinal):
        stage = f"tc-generator:{row['batch_id']}"
        answers = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == stage]
        if not answers:
            missing.append(f"{row.get('namespace', '')} {row['batch_id']}".strip())
            continue
        fragments.append(dict(_read_model_stage_artifact_with_state(project, root, state, attempt_id, stage, str(answers[-1]["artifact_digest"]))["artifact"]))
    if missing:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: пачки идут по очереди — сначала принятый ответ (model-response) пачки "
                                  f"{', '.join(missing)}, потом {current.get('namespace', '')} {batch_id}")
    return fragments


def _prior_capabilities_value(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str) -> Mapping[str, Any]:
    """FaT-BO: the capabilities of the accepted batches before *batch_id*, built by code from the published plan and the journal."""
    from tools.batch_assembly import BatchAssemblyError, prior_capabilities

    plan = _generator_input(project, root, state, attempt_id, "plan")
    batch = next((row for row in plan["batches"] if row.get("batch_id") == batch_id), None)
    if batch is None:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {batch_id} is not a batch of the published plan {plan['digest']}")
    if _batch_ordinal(batch) == 1:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {batch_id} — первая пачка, у неё четыре входа, capabilities прошлых пачек нет")
    fragments = _accepted_earlier_fragments(project, root, state, attempt_id, plan, batch_id)
    try:
        return prior_capabilities(dict(plan), batch_id, fragments)
    except BatchAssemblyError as error:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {error}") from error


def _imported_cases(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any] | None:
    """FaT-IMP: the published skeleton of an import run; None for a run without --cases; refused when not yet published."""
    from tools.cases_import import cases_path

    if cases_path(root) is None:
        return None
    try:
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, "imported-cases", "ARTIFACT_READ_BACK")["record"]["imported"]
    except (ValueError, OSError, KeyError) as error:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: the cases of the import are not published; fix: tools.api cases-import "
                                  f"--run-root <run> --attempt {attempt_id}") from error


def _batch_skeleton_value(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str) -> Mapping[str, Any]:
    """FaT-SKB: the skeleton of *batch_id* as the whole skeleton and the published plan give it."""
    from tools.cases_import import slice_value

    whole = _imported_cases(project, root, state, attempt_id)
    if whole is None:
        raise ValueError("IMPORT_NOT_CASES_RUN: the run has no --cases")
    return slice_value(whole, _generator_input(project, root, state, attempt_id, "plan"), batch_id)


def _publish_batch_skeletons(run_root: Path, attempt_id: str) -> dict[str, str]:
    """FaT-SKB: publish the skeleton of every batch of the published plan — bookkeeping inside the plan publication the
    agent called; the same plan again changes nothing.  ``{batch_id: digest}``; empty for a run without --cases."""
    from tools.cases_import import cases_path

    project, root = _run_root(run_root)
    if cases_path(root) is None:
        return {}
    state = derive_state(root)
    plan = _generator_input(project, root, state, attempt_id, "plan")
    published: dict[str, str] = {}
    for row in plan["batches"]:
        kind = _SKELETON_KIND + row["batch_id"]
        value = _batch_skeleton_value(project, root, state, attempt_id, row["batch_id"])
        prior = None
        if _receipt_target(root, attempt_id, kind).exists():
            try:  # F2 (A2-4): a file without its events is finished below, not a dead end
                prior = _read_attempt_receipt_with_state(project, root, derive_state(root), attempt_id, kind, "ARTIFACT_READ_BACK")["record"]["imported"]
            except (ValueError, OSError, KeyError):
                prior = None
            if prior is not None and prior != value:
                raise ValueError(f"GENERATOR_INPUT_CONFLICT: the batch {row['batch_id']} already has another skeleton")
        if prior is None:
            _publish_fixed_receipt_resuming(run_root, attempt_id, kind, {"imported": dict(value)})
        published[row["batch_id"]] = str(value["digest"])
    return published


def _imported_for_answer(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str,
                         request: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """FaT-SKB: what model-response checks the human fields against — the skeleton the request took as its last input:
    the batch's own one, or the whole one for a request published before FaT-SKB.  None for a run without --cases."""
    whole = _imported_cases(project, root, state, attempt_id)
    if whole is None or (request.get("input_digests") or [None])[-1] == whole["digest"]:
        return whole
    return _read_attempt_receipt_with_state(project, root, state, attempt_id, _SKELETON_KIND + batch_id, "ARTIFACT_READ_BACK")["record"]["imported"]


@closure_cached  # FaT-IMP
def publish_imported_cases(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """Publish the skeleton of an import run from its CSV; the same value again changes nothing."""
    from tools.cases_import import run_skeleton

    project, root = _run_root(run_root)
    attempt = next(item for item in derive_state(root)["attempts"] if item["attempt_id"] == attempt_id)
    value = run_skeleton(root, attempt)
    if value is None:
        raise ValueError("IMPORT_NOT_CASES_RUN: the run has no --cases")
    _publish_bound_attempt_receipt(run_root, attempt_id, "imported-cases", {"imported": value})
    target = _receipt_target(root, attempt_id, "imported-cases")
    return {"digest": value["digest"], "cases": len(value["cases"]), "path": target.relative_to(root).as_posix()}


@closure_cached  # FaT-BO
def publish_prior_capabilities(run_root: Path, attempt_id: str, batch_id: str) -> dict[str, Any]:
    """Publish the fifth tc-generator input of batch k>=2 — built from the journal, one receipt per batch; the same value
    again changes nothing, another one is refused."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    value = _prior_capabilities_value(project, root, state, attempt_id, batch_id)
    kind = _PRIOR_KIND + batch_id
    target = _receipt_target(root, attempt_id, kind)
    if target.exists():
        try:
            prior = _read_attempt_receipt_with_state(project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK")["record"]["prior_capabilities"]
        except (ValueError, OSError, KeyError):
            prior = None  # F2 (A2-4): the file is there, its events are not — below it is finished (same bytes) or refused (other bytes)
        if prior is not None and prior != value:
            raise ValueError(f"GENERATOR_INPUT_CONFLICT: the batch {batch_id} already has other prior capabilities; another one needs "
                             "a new run (scan --new-run)")
    _publish_fixed_receipt_resuming(run_root, attempt_id, kind, {"prior_capabilities": dict(value)})
    ids = [str(row.get("capability_id")) for row in value["operation_capabilities"]]
    # PT2: the composition in a line — the ids (their names), the body in the file: record.prior_capabilities.operation_capabilities
    return {"batch_id": batch_id, "digest": value["digest"], "capabilities": len(ids), "capability_ids": ids[:60],
            **({"capability_ids_more": len(ids) - 60} if len(ids) > 60 else {}),
            "path": target.relative_to(root).as_posix(), "body_at": "record.prior_capabilities.operation_capabilities"}


_STAGE_EXAMPLES = "context-marker:baseline, tc-generator:BATCH-<id>, tc-reviewer:canonical, tc-to-autotest:r1, autotest-reviewer:r1"


def _new_run_fix(root: Path) -> str:
    """FaT-H4b: the scan of this run's request with --new-run, as other refusals of a closed attempt give it."""
    try:
        from tools.run_pipeline import _scan_command

        request = read_run(root)["manifest"].get("scan_request")
        if isinstance(request, Mapping):
            return "новый прогон того же запроса: " + _scan_command(root.parents[1], request, extra=("--new-run",))
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        pass
    return "новый прогон: scan с теми же --module, --docs и --profile, что у этого прогона, и --new-run"


class ModelRequestInvalid(ValueError):
    """FaT-AU1: a model request that breaks one of its basic conditions, named in the message."""

    code = "MODEL_REQUEST_INVALID"


class ModelRequestDuplicate(ValueError):
    """FaT-AU1: a second request of one stage of the attempt."""

    code = "MODEL_REQUEST_DUPLICATE"


class ModelStageArtifactInvalid(ValueError):
    """FaT-AU1: a model answer that does not pass its schema; the message carries the retry_prompt."""

    code = "MODEL_STAGE_ARTIFACT_INVALID"


class ModelInputsMismatch(ValueError):
    """FaT-DG: a model request whose input digests are not the ones the journal fixes."""

    code = "MODEL_INPUTS_MISMATCH"


class ModelRequestOutOfOrder(ValueError):
    """FaT-RQ: a request of the automation chain that is not its next stage (an orphan r2 before r1)."""

    code = "MODEL_REQUEST_OUT_OF_ORDER"


def _require_next_automation_stage(run_root: Path, attempt_id: str, stage_instance_id: str) -> None:
    """The chain's next stage is what finish reads (automation_next); a final chain is left to the existing checks."""
    from tools.finalize_attempt import automation_next

    try:
        outcome, expected = automation_next(run_root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return
    if outcome == "PENDING" and expected != stage_instance_id:
        raise ModelRequestOutOfOrder(f"MODEL_REQUEST_OUT_OF_ORDER: {stage_instance_id} — следующая стадия цепочки автоматизации {expected}; "
                                     f"fix: model-request --stage {expected}, ничего не записано")


class ModelReuseRefused(ValueError):
    """FaT-CR: the named run is no reuse source for this context-marker request (the reason says why)."""

    code = "MODEL_REUSE_REFUSED"


class ModelReuseMismatch(ValueError):
    """FaT-CR: the answer to a reuse request is not the source answer byte for byte."""

    code = "MODEL_REUSE_MISMATCH"


# FaT-CR: a marker is reused only from a run whose review accepted the cases; AUTO_FIX_APPLIED fixes human fields only, not the marker
ACCEPTED_REVIEW = frozenset({"ПРИНЯТО", "AUTO_FIX_APPLIED"})
_REUSE_FREE = "нет прошлого прогона с разметкой"


def skill_pack_digest() -> str | None:
    """FaT-CR: the skill_pack_digest of this package's release/manifest.json — the one fingerprint of the package (audit U9)."""
    try:
        value = json.loads((Path(__file__).resolve().parents[1] / "release" / "manifest.json").read_text(encoding="utf-8")).get("skill_pack_digest")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and _DIGEST.fullmatch(value) else None


def _review_verdict(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> str | None:
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
              and event.get("stage_instance_id") == "tc-reviewer:canonical"]
    if not events:
        return None
    value = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "tc-reviewer:canonical", str(events[-1]["artifact_digest"]))
    return str(value["artifact"]["artifacts"]["validation_report"]["verdict"])


def _has_drifted(root: Path, attempt_id: str) -> bool:
    return _receipt_target(root, attempt_id, "resume-validation").is_file()


def _marker_source(project: Path, run_id: str) -> dict[str, Any]:
    """The recorded context marker of an earlier run: attempt, verdict, drift and every marker stage (request, answer).

    Reads only: a run with pending journal events is refused, so nothing is recovered or written in the source run."""
    root = project / ".pilot-runs" / run_id
    if not re.fullmatch(r"[0-9a-f]{32}", run_id) or not (root / "events.jsonl").is_file():
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: нет прогона {run_id} в {project / '.pilot-runs'}")
    if (root / "pending-events").is_dir() and any((root / "pending-events").glob("*.json")):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: журнал прогона {run_id} не закрыт")
    _project, root = _run_root(root)
    state = derive_state(root)
    for attempt in reversed(state["attempts"]):
        attempt_id = str(attempt["attempt_id"])
        stages: dict[str, tuple[Mapping[str, Any], str]] = {}
        for event in state["events"]:
            stage = str(event.get("stage_instance_id") or "")
            if event.get("attempt_id") != attempt_id or not stage.startswith("context-marker:") or event["event_type"] != "MODEL_RESPONSE_RECEIVED":
                continue
            request = next(item for item in state["events"] if item.get("attempt_id") == attempt_id and item["event_type"] == "MODEL_REQUESTED"
                           and item.get("stage_instance_id") == stage)
            stages[stage] = (_read_model_request_with_state(project, root, state, attempt_id, stage, str(request["artifact_digest"])),
                             str(event["artifact_digest"]))
        if "context-marker:baseline" in stages:
            return {"run_id": run_id, "root": root, "state": state, "attempt": attempt, "stages": stages,
                    "verdict": _review_verdict(project, root, state, attempt_id), "drift": _has_drifted(root, attempt_id)}
    raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {_REUSE_FREE} — в прогоне {run_id} нет записанной разметки")


def _source_refusal(source: Mapping[str, Any], module: str) -> str | None:
    if source["attempt"].get("module") != module:
        return f"другой модуль ({source['attempt'].get('module')})"
    if source["verdict"] not in ACCEPTED_REVIEW:
        return f"ревью кейсов не принято ({source['verdict'] or 'ревью нет'})"
    if source["drift"]:
        return "дрейф: после исполнения источника baseline разошёлся"
    return None


def _reuse_source(
    project: Path, root: Path, attempt: Mapping[str, Any], stage_instance_id: str, inputs: Sequence[str], role_policy: str, pack: str | None, run_id: str,
) -> dict[str, str]:
    """FaT-CR: the reuse record of a context-marker request — only when every condition on the source holds."""
    if run_id == root.name:
        raise ModelReuseRefused("MODEL_REUSE_REFUSED: тот же прогон — переиспользуется только прошлый прогон")
    source = _marker_source(project, run_id)
    refusal = _source_refusal(source, str(attempt.get("module")))
    if refusal is not None:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: прогон {run_id}: {refusal}")
    if stage_instance_id not in source["stages"]:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: в прогоне {run_id} нет ответа {stage_instance_id}")
    request, response = source["stages"][stage_instance_id]
    if list(request["input_digests"]) != list(inputs):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: входы иные: у источника {list(request['input_digests'])}, здесь {list(inputs)}")
    if pack is None or request.get("skill_pack_digest") != pack or request.get("role_policy") != role_policy:
        # any change of the package cancels the reuse: coarse, but one fingerprint and no second mechanism
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: пакет иной: у источника skill_pack_digest "
                                f"{request.get('skill_pack_digest') or 'не записан (прогон до FaT-CR)'}, здесь {pack or 'не прочитан'}")
    if str(request.get("model_id") or "").startswith("controller:"):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {stage_instance_id} источника сделан кодом (merge) — сделай его тем же merge")
    attempt_id = str(source["attempt"]["attempt_id"])
    _read_model_stage_artifact_with_state(project, source["root"], source["state"], attempt_id, stage_instance_id, response)  # bytes = digest
    return {"run_id": run_id, "attempt_id": attempt_id, "request_digest": str(request["digest"]), "response_digest": response}


def _marker_stage_order(stage: str) -> tuple[int, int]:
    """FaT-CR2: the order the journal accepts — requirements, the parts by number, then the baseline."""
    if stage == "context-marker:requirements":
        return 0, 0
    if stage.startswith("context-marker:batch-"):
        return 1, int(stage.rsplit("-", 1)[1])
    return 2, 0


def _marker_base(source: Mapping[str, Any]) -> list[str]:
    """requirements, inventory and the context receipts a marker of the source bound, in order."""
    stages = source["stages"]
    baseline = list(stages["context-marker:baseline"][0]["input_digests"])
    parts = sorted(stage for stage in stages if stage.startswith("context-marker:batch-"))
    receipts = [str(stages[stage][0]["input_digests"][-2]) for stage in parts] if parts else baseline[2:]
    return [*baseline[:2], *receipts]


def marker_reuse_suggestion(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """FaT-CR: an earlier run of the project whose context marker this attempt may reuse, with the commands — or why not.

    A suggestion only: the agent decides; every reused answer still goes through model-request --reuse-from and
    model-response, the same checks as a fresh one."""
    from tools.project_inventory import read_execution_baseline

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
    baseline = frozen_baseline(root, attempt)
    published = list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                                   if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"))
    current = [baseline["requirements"]["digest"], baseline["inventory_digest"], *published]
    pack = skill_pack_digest()
    first_refusal: dict[str, Any] | None = None
    runs = [path for path in (project / ".pilot-runs").iterdir() if path.name != root.name and (path / "events.jsonl").is_file()]
    for path in sorted(runs, key=lambda item: (item / "events.jsonl").stat().st_mtime, reverse=True):
        try:
            source = _marker_source(project, path.name)
        except (ValueError, OSError, KeyError, TypeError, StopIteration):
            continue
        refusal = _source_refusal(source, str(attempt.get("module")))
        base = _marker_base(source)
        differences = [{"input": name, "given": given, "source": other} for name, given, other in
                       zip(["requirements", "inventory", *(f"квитанция контекста {index}" for index in range(1, len(current) - 1))], current, base)
                       if given != other]
        if len(base) != len(current):
            differences.append({"input": "число квитанций контекста", "given": len(current) - 2, "source": len(base) - 2})
        model_stages = [stage for stage in sorted(source["stages"], key=_marker_stage_order)
                        if not str(source["stages"][stage][0].get("model_id") or "").startswith("controller:")]
        if refusal is None and differences:
            refusal = "входы иные"
        if refusal is None and (pack is None or any(source["stages"][stage][0].get("skill_pack_digest") != pack for stage in model_stages)):
            refusal = "пакет иной (skill_pack_digest не совпал или не записан: прогон до FaT-CR)"
        if refusal is not None:
            first_refusal = first_refusal or {"run_id": None, "source_run_id": path.name, "reason": refusal,
                                              **({"differences": differences} if refusal == "входы иные" else {})}
            continue
        source_attempt = str(source["attempt"]["attempt_id"])
        rows = []
        made_by = {stage: str(source["stages"][stage][0].get("model_id") or "") for stage in source["stages"]}
        if made_by.get("context-marker:requirements") == "controller:cases-import":  # FaT-CR2: an import makes it by code
            rows.append({"stage": "context-marker:requirements", "by": "cases-import",
                         "commands": [f"python -m tools.api cases-import --run-root {root} --attempt {attempt_id}"]})
        for stage in model_stages:
            request, response = source["stages"][stage]
            artifact = _model_stage_artifact_target(source["root"], source_attempt, response)
            head = f"python -m tools.api model-{{kind}} --run-root {root} --attempt {attempt_id} --stage {stage}"
            rows.append({"stage": stage, "response_digest": response, "artifact": str(artifact), "commands": [
                head.format(kind="request") + f" --model-id reuse --invocation-id reuse-{path.name[:8]}-{stage.replace(':', '-')} "
                + " ".join(f"--input-digest {digest}" for digest in request["input_digests"]) + f" --reuse-from {path.name}",
                head.format(kind="response") + f" --artifact {artifact}"]})
        merged = made_by.get("context-marker:baseline") == "controller:context-marker-merge"
        return {"run_id": path.name, "stages": rows,
                **({"then": "слияние частей кодом, не вызов модели: python -m tools.context_marker_parts merge "
                            f"--run-root {root}"} if merged else {})}
    return first_refusal or {"run_id": None, "reason": _REUSE_FREE}


class ModelResponseDigestMismatch(ValueError):
    """FaT-DG: a digest field of a model answer that does not match the journal; nothing is written."""

    code = "MODEL_RESPONSE_DIGEST_MISMATCH"


def _generator_input_digest(value: Mapping[str, Any]) -> str:
    from tools.batch_assembly import with_digest

    return with_digest({key: item for key, item in value.items() if key != "digest"})["digest"]


def generator_input_digest(value: Mapping[str, Any]) -> str:
    """P2: the self-digest ``publish_generator_input`` gives a plan or a header — known before it is published."""
    return _generator_input_digest(value)


def _plan_contexts(plan: Mapping[str, Any]) -> list[str]:
    """FaT-AU3: the context receipts every generator batch of the plan reads, in order (one before the list)."""
    from tools.batch_assembly import context_digests

    if isinstance(plan.get("context_receipt_digests"), list) or "context_receipt_digest" in plan:
        return context_digests(plan)
    batches = plan.get("batches") or []
    return [str(batches[0]["context_receipt_digest"])] if batches and isinstance(batches[0], Mapping) else []


def _plan_batches_listed(plan: Mapping[str, Any]) -> bool:
    batches = plan.get("batches")
    return isinstance(batches, list) and bool(batches) and all(
        isinstance(row, Mapping) and isinstance(row.get("batch_id"), str)
        and _DIGEST.fullmatch(str(row.get("context_receipt_digest", ""))) is not None
        and _DIGEST.fullmatch(str(row.get("header_digest", ""))) is not None
        for row in batches
    )


@closure_cached  # FaT-DG
def _check_plan_against(root: Path, attempt_id: str, item: Mapping[str, Any], header_digest: str, state: Mapping[str, Any]) -> None:
    """P2-fix: the checks a plan must pass before it is published — its batches bind ``header_digest`` and context receipts of the
    attempt, the receipts are a prefix in batch_index order.  They write nothing, so ``tools.api plan`` can run them before it
    publishes the header and a refusal leaves the journal as it was."""
    unfinished = context_incomplete(root, attempt_id, state)
    if unfinished is not None:
        raise ValueError(scan_incomplete_message(*unfinished))
    for row in item["batches"]:
        if row["header_digest"] != header_digest:
            raise ValueError(f"GENERATOR_INPUT_HEADER: batch {row['batch_id']} binds the header {row['header_digest']}, the published "
                             f"header is {header_digest}; fix: put {header_digest} as header_digest in the grouping and rerun plan-batches")
        for context in dict.fromkeys([str(row["context_receipt_digest"]), *_plan_contexts(item)]):  # FaT-AU3: and the list
            try:
                read_context_selection(root, attempt_id, context)
            except ValueError as error:
                selected = sorted({str(event["artifact_digest"]) for event in state["events"]
                                   if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"})
                if context in selected:  # F3 (hunt 8.6): the receipt is the attempt's, its bytes no longer match the project
                    raise ValueError(f"CONTEXT_DRIFTED: the context receipt {context} of batch {row['batch_id']} is the attempt's, but the "
                                     "files of the context changed after the scan; fix: `run_pipeline status` shows drift.changed — "
                                     "restore those files, or start the run again with `scan --new-run`") from error
                raise ValueError(f"GENERATOR_INPUT_CONTEXT: batch {row['batch_id']} binds the context {context}, which "
                                 f"is not a context receipt of this attempt; the attempt has: {', '.join(selected) or 'none'} "
                                 "(tools.api read-context --digest <d>)") from error
    # FaT-H4a (hunt 4.1): the plan reads a prefix, in batch_index order, of every context receipt of the attempt — a skipped
    # receipt (1 and 3 without 2) is one the generator would never read while the note says it saw them all
    ordered = sorted((read_context_selection(root, attempt_id, digest) for digest in dict.fromkeys(
        str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED")),
        key=lambda receipt: int(receipt["batch_index"]))
    contexts, prefix = _plan_contexts(item), [str(receipt["digest"]) for receipt in ordered]
    if contexts != prefix[:len(contexts)]:
        index = next(index for index, (given, wanted) in enumerate(zip(contexts, prefix)) if given != wanted)
        raise ValueError(
            f"GENERATOR_INPUT_CONTEXT: the plan reads the context receipts {', '.join(contexts)}; position {index + 1} must be "
            f"{prefix[index]} (batch_index {ordered[index]['batch_index']}) — the plan takes the receipts of the attempt in batch_index "
            f"order from the first, none skipped: {', '.join(prefix)}; fix: put every digest in the grouping context_receipt_digests "
            "(the lowest batch_index first, also as context_receipt_digest), one --context-receipt each, and rerun plan-batches")


def publish_generator_input(run_root: Path, attempt_id: str, kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Publish the batch plan or the generator header of the attempt with its one digest — the self-digest.

    A value without ``digest`` gets it here; a ``digest`` that is not the self-digest (the sha256 of the file bytes,
    say) is refused with the right one. One plan and one header per attempt; the same value again changes nothing."""
    if kind not in _GENERATOR_INPUT_KINDS or not isinstance(value, Mapping):
        raise ValueError("GENERATOR_INPUT_INVALID: --kind is plan or header, --value a JSON object")
    item = dict(value)
    expected = _generator_input_digest(item)
    if "digest" not in item:
        item["digest"] = expected
    elif item["digest"] != expected:
        raise ValueError(f"GENERATOR_INPUT_DIGEST: the {kind} digest {item['digest']} is not its self-digest {expected} "
                         "(the sha256 of the file bytes is not the digest); drop the field or put this value")
    if kind == "plan" and not _plan_batches_listed(item):
        raise ValueError("GENERATOR_INPUT_INVALID: the plan lists no batches; build it with tools.api plan-batches")
    receipt_kind = _GENERATOR_INPUT_KINDS[kind]
    project, root = _run_root(run_root)
    if kind == "plan":  # FaT-DG: a plan the generator request would refuse is refused now, while another plan is still possible
        state = derive_state(root)
        from tools.scope_pause import check_plan

        check_plan(root, attempt_id, item, state)  # FaT-P3: the pause after the marker — the plan carries the human's answer
        try:
            header = _generator_input(project, root, state, attempt_id, "header")
        except ModelInputsMismatch as error:
            raise ValueError("GENERATOR_INPUT_ORDER: the header is not published yet; fix: first tools.api publish-generator-input "
                             "--kind header --value <file>, build the plan with its result.digest, then publish the plan") from error
        _check_plan_against(root, attempt_id, item, str(header["digest"]), state)

    try:
        prior = _read_attempt_receipt_with_state(project, root, derive_state(root), attempt_id, receipt_kind, "ARTIFACT_READ_BACK")["record"][kind]
    except (ValueError, OSError, KeyError):
        prior = None
    if prior is not None and prior != item:
        raise ValueError(f"GENERATOR_INPUT_CONFLICT: this attempt already has the {kind} {prior.get('digest')}; "
                         "another one needs a new run (scan --new-run)")
    _publish_bound_attempt_receipt(run_root, attempt_id, receipt_kind, {kind: item})
    skeletons = _publish_batch_skeletons(run_root, attempt_id) if kind == "plan" else {}  # FaT-SKB: the last input of each batch
    if kind == "plan":  # FaT-H4a (hunt 4.1): which context batches of the attempt the generator sees — the line of the reviewer view
        from tools.review_view import run_context_note

        try:  # FaT-H4a2: a hint, never a stop — as in the reviewer view
            context = run_context_note(root, attempt_id)
        except (KeyError, OSError, ValueError, TypeError):
            context = None
    else:
        context = None
    return {"kind": kind, "digest": item["digest"],
            "path": str(_receipt_target(root, attempt_id, receipt_kind).relative_to(root)).replace("\\", "/"),
            **({"batch_skeletons": skeletons} if skeletons else {}), **({"context": context} if context else {})}


class PlanPublished(tuple):
    """``(plan, published)`` of :func:`publish_plan` — a tuple for the two ``--out-…`` files, with the notes ``plan-batches`` prints."""

    notes: list[str] = []


def publish_plan(run_root: Path, attempt_id: str, header: Mapping[str, Any], requirements: list[dict[str, Any]], grouping: Mapping[str, Any],
                 project: Path, input_files: list[Path], context_receipt: Any, imported: Mapping[str, Any] | None,
                 out_of_scope: Any = None) -> PlanPublished:
    """P2 (item 9): ``tools.api plan`` — what ``publish-generator-input --kind header``, ``plan-batches`` and ``publish-generator-input
    --kind plan`` do, in that order, with their events and files, in one call.  The grouping needs no ``header_digest``: it is the
    self-digest of the header; a grouping that names another is GENERATOR_INPUT_HEADER.  The plan is built before anything is published,
    so a refusal of the plan (the pause after the marker among them) leaves the journal as it was."""
    from tools.batch_assembly import plan_batches_for_project

    item = dict(header)
    expected = _generator_input_digest(item)
    evidence = dict(grouping)
    if evidence.get("header_digest") in (None, ""):
        evidence["header_digest"] = expected
    elif evidence["header_digest"] != expected:
        raise ValueError(f"GENERATOR_INPUT_HEADER: header_digest of the grouping is {evidence['header_digest']}, the header's own is {expected}; "
                         f"fix: удали header_digest из grouping — код поставит digest заголовка ({expected}) сам")
    plan = plan_batches_for_project(requirements, evidence, project, input_files, context_receipt, imported, out_of_scope)
    item_plan = dict(plan)
    item_plan.setdefault("digest", _generator_input_digest(item_plan))
    if not _plan_batches_listed(item_plan):
        raise ValueError("GENERATOR_INPUT_INVALID: the plan lists no batches; build it with tools.api plan-batches")
    _root = _run_root(run_root)[1]
    from tools.scope_pause import check_plan

    state = derive_state(_root)
    check_plan(_root, attempt_id, item_plan, state)  # the pause after the marker, then the bindings: before anything is written
    _check_plan_against(_root, attempt_id, item_plan, expected, state)
    published_header = publish_generator_input(run_root, attempt_id, "header", item)
    published_plan = publish_generator_input(run_root, attempt_id, "plan", dict(plan))
    result = PlanPublished((dict(plan), {"header": published_header, "plan": published_plan}))
    result.notes = list(plan.notes)
    return result


def _published_generator_inputs(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """FaT-DG: the published (plan, header) of the attempt, or None when the run published neither (before FaT-DG)."""
    try:
        return _generator_input(project, root, state, attempt_id, "plan"), _generator_input(project, root, state, attempt_id, "header")
    except ModelInputsMismatch:
        return None


def _generator_input(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, kind: str) -> Mapping[str, Any]:
    try:
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, _GENERATOR_INPUT_KINDS[kind], "ARTIFACT_READ_BACK")["record"][kind]
    except (ValueError, OSError, KeyError) as error:
        raise ModelInputsMismatch(
            f"MODEL_INPUTS_MISMATCH: the {kind} of this attempt is not published; fix: tools.api publish-generator-input "
            f"--kind {kind} --value <file>, its result.digest is the input"
        ) from error


def _expected_model_inputs(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage_instance_id: str,
) -> list[tuple[str, str]] | None:
    """FaT-DG: the ordered input digests the journal fixes for a model request, each with where it comes from.

    None — the journal does not fix them here (context-marker, or a predecessor is missing: the existing checks name it)."""
    stage, _, instance = stage_instance_id.partition(":")
    attempt_id = str(attempt["attempt_id"])
    if stage == "tc-generator":
        markers = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "context-marker:baseline"]
        if len(markers) != 1:
            return None
        plan, header = _generator_input(project, root, state, attempt_id, "plan"), _generator_input(project, root, state, attempt_id, "header")
        batch = next((row for row in plan["batches"] if row.get("batch_id") == instance), None)
        if batch is None:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {instance} is not a batch of the published plan {plan['digest']}")
        if batch["header_digest"] != header["digest"]:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: the plan binds the header {batch['header_digest']}, the published header is "
                                      f"{header['digest']}; the plan is built from the header digest of publish-generator-input")
        expected = [
            (str(markers[0]["artifact_digest"]), "result.content_digest ответа context-marker:baseline (model-response)"),
            (str(batch["context_receipt_digest"]), f"context_receipt_digest батча {instance} в опубликованном плане"),
            # FaT-AU3: the further context receipts of the plan, in its order
            *((context, f"context_receipt_digests[{index}] опубликованного плана") for index, context in enumerate(_plan_contexts(plan)) if index),
            (str(plan["digest"]), "result.digest команды publish-generator-input --kind plan"),
            (str(header["digest"]), "result.digest команды publish-generator-input --kind header"),
        ]
        if _batch_ordinal(batch) > 1:  # FaT-BO: from batch 2 the capabilities of the accepted earlier batches
            prior = _prior_capabilities_value(project, root, state, attempt_id, instance)
            try:
                _read_attempt_receipt_with_state(project, root, state, attempt_id, _PRIOR_KIND + instance, "ARTIFACT_READ_BACK")
            except (ValueError, OSError, KeyError) as error:
                raise ModelInputsMismatch(
                    f"MODEL_INPUTS_MISMATCH: capabilities прошлых пачек для {instance} не опубликованы; fix: tools.api prior-capabilities "
                    f"--run-root <run> --attempt {attempt_id} --batch-id {instance}, его result.digest — вход сразу после header"
                ) from error
            expected.append((str(prior["digest"]), f"result.digest команды tools.api prior-capabilities --batch-id {instance}"))
        if _imported_cases(project, root, state, attempt_id) is not None:  # FaT-SKB: the skeleton of this batch is its last input
            try:
                skeleton = _read_attempt_receipt_with_state(project, root, state, attempt_id, _SKELETON_KIND + instance, "ARTIFACT_READ_BACK")["record"]["imported"]
            except (ValueError, OSError, KeyError) as error:
                raise ModelInputsMismatch(
                    f"MODEL_INPUTS_MISMATCH: скелет пачки {instance} не опубликован; fix: повтори tools.api publish-generator-input --kind plan "
                    "тем же планом — он опубликует скелеты пачек (batch_skeletons)"
                ) from error
            expected.append((str(skeleton["digest"]), f"batch_skeletons[{instance}] — результат команды publish-generator-input --kind plan"))
        return expected
    try:
        if stage == "tc-reviewer":
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
            return [(str(boundary["canonical_branch_digest"]), "boundary.canonical_branch_digest (read-receipt reviewer-session-boundary)"),
                    (str(boundary["package_digest"]), "boundary.package_digest (read-receipt reviewer-session-boundary)")]
        if stage in {"tc-to-autotest", "autotest-reviewer"}:
            return _automation_inputs(project, root, state, attempt_id, stage_instance_id)
    except (ValueError, OSError, KeyError, TypeError):
        return None
    return None


def _automation_inputs(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str) -> list[tuple[str, str]]:
    """The two inputs of tc-to-autotest / autotest-reviewer read from the journal; an unreadable predecessor raises its read error."""
    stage, _, instance = stage_instance_id.partition(":")
    if stage == "tc-to-autotest":
        effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
        return [(str(effective["document_digest"]), "effective.document_digest (tools.api read-effective)"),
                (str(effective["effective_bundle_receipt_digest"]), "effective.effective_bundle_receipt_digest (tools.api read-effective)")]
    boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, f"automation-review-boundary-{instance}", "ARTIFACT_READ_BACK")["record"]
    return [(str(boundary["automation_digest"]), f"boundary.automation_digest (read-receipt automation-review-boundary-{instance})"),
            (str(boundary["digest"]), f"boundary.digest (read-receipt automation-review-boundary-{instance})")]


def _inputs_mismatch(stage_instance_id: str, inputs: Sequence[str], expected: Sequence[tuple[str, str]]) -> ModelInputsMismatch:
    rows = [f"вход {index}: дан {inputs[index - 1] if index <= len(inputs) else 'нет'}, ожидается {digest} — {source}"
            for index, (digest, source) in enumerate(expected, start=1) if index > len(inputs) or inputs[index - 1] != digest]
    if len(inputs) > len(expected):
        rows.append(f"лишние входы: {', '.join(inputs[len(expected):])}")
    order = " ".join(f"--input-digest {digest}" for digest, _source in expected)
    if stage_instance_id.startswith("tc-reviewer:"):  # P2: the reviewer's request is the work of review-open, one fix for the whole chain
        from tools.fixes import REVIEW_CHAIN

        return ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id}: " + "; ".join(rows) + "; fix: запрос ревьюера кейсов пишет review-open "
                                   "(он знает оба digest сам), руками он не нужен — " + REVIEW_CHAIN)
    return ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id}: " + "; ".join(rows) + f"; fix: повтори model-request с {order}")


_PROVENANCE_LINE = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+)(?!\d)")


def _requirement_order_rows(stage_instance_id: str, value: Mapping[str, Any]) -> str | None:
    """FaT-SN: the first source requirement out of document order, or None.  In display_order the requirements of one
    document come together (the documents in the answer's own order — of --docs or the task first, never forced by path)
    and its lines grow as numbers (`…md:4` before `…md:12`), by the first provenance `path:line` of each; the SREQ numbers
    follow display_order.  A row whose first reference names no line leaves the whole order to the model."""
    if not stage_instance_id.startswith("context-marker"):
        return None
    artifacts = value.get("artifacts") if isinstance(value, Mapping) else None
    documentation = artifacts.get("analytics_documentation") if isinstance(artifacts, Mapping) else None
    rows = documentation.get("requirements") if isinstance(documentation, Mapping) else None
    if not isinstance(rows, list) or not rows:
        return None
    keyed = []
    for row in rows:
        provenance = row.get("provenance") if isinstance(row, Mapping) else None
        found = _PROVENANCE_LINE.match(provenance[0]) if isinstance(provenance, list) and provenance and isinstance(provenance[0], str) else None
        if found is None or not isinstance(row.get("display_order"), int):
            return None  # without a line reference the order is the model's
        keyed.append(((found.group("path"), int(found.group("line"))), row))
    by_display = sorted(keyed, key=lambda item: item[1]["display_order"])
    finished: set[str] = set()
    for (previous, _), ((path, line), row) in zip(by_display, by_display[1:]):
        name = f"{row.get('source_requirement_id')} ({path}:{line}, display_order {row['display_order']})"
        if path != previous[0]:
            finished.add(previous[0])
            if path in finished:
                return f"{name}: требования документа {path} идут не подряд — после строк {previous[0]}"
        elif line < previous[1]:
            return f"{name} стоит после строки {previous[0]}:{previous[1]} того же документа"
    # the number at the end of each ID as a number (SREQ-9 before SREQ-10); an ID without one leaves the numbering alone
    numbers = [re.search(r"(\d+)$", str(row.get("source_requirement_id") or "")) for _key, row in by_display]
    if all(numbers) and [int(found.group(1)) for found in numbers] != sorted(int(found.group(1)) for found in numbers):
        return "номера SREQ идут не в порядке display_order"
    return None


_SELF_DIGEST = "самодайджест фрагмента (без поля digest)"


def _response_bindings(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: Mapping[str, Any],
) -> list[tuple[str, str | None, str]]:
    """FaT-DG, audit U2: the digest fields of a model answer the journal fixes — (pointer, expected, source).  The one list
    both for the check (``_response_digest_rows``) and for the fill (``_fill_response_bindings``); the expected value of the
    fragment self-digest is None — it is the digest of the answer itself, taken last.

    A shape the schema refuses yields what it could read; the schema and the later checks stay the backstop."""
    automation_sha256, autotest_review_sha256, implementation_relations_sha256 = execution_gate.from_mode_b(
        "automation_validation", "automation_sha256", "autotest_review_sha256", "implementation_relations_sha256",
    )

    stage, _, instance = stage_instance_id.partition(":")
    rows: list[tuple[str, str | None, str]] = []

    def add(pointer: str, expected: str | None, source: str) -> None:
        rows.append((pointer, expected, source))

    def answer(stage_id: str, event_type: str) -> Mapping[str, Any]:
        events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_id
                  and event["event_type"] == event_type]
        return _read_model_stage_artifact_with_state(project, root, state, attempt_id, stage_id, str(events[-1]["artifact_digest"]))["artifact"]

    try:
        if stage == "tc-generator":
            inputs = request["input_digests"]
            try:  # FaT-AU3: further context receipts of the published plan shift plan and header
                more = len(_plan_contexts(_generator_input(project, root, state, attempt_id, "plan"))) - 1
            except ModelInputsMismatch:
                more = 0
            for pointer, index, name in (("/context_receipt_digest", 1, "context"), ("/plan_digest", 2 + more, "plan"), ("/header_digest", 3 + more, "header")):
                add(pointer, str(inputs[index]), f"вход {index + 1} запроса ({name})")
            add("/digest", None, _SELF_DIGEST)
        elif stage == "tc-reviewer":
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
            report = value["artifacts"]["validation_report"]
            candidate = str(boundary["canonical_branch_digest"])
            add("/artifacts/validation_report/candidate/document_sha256", candidate, "boundary.canonical_branch_digest")
            if report["verdict"] == "ПРИНЯТО" and isinstance(report.get("effective"), Mapping):
                add("/artifacts/validation_report/effective/document_sha256", candidate, "ПРИНЯТО выбирает кандидата: boundary.canonical_branch_digest")
        elif stage == "tc-to-autotest":
            effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
            artifacts = value["artifacts"]
            add("/artifacts/source/source_digest", str(effective["document_digest"]), "effective.document_digest (tools.api read-effective)")
            add("/artifacts/source/effective_bundle_receipt_digest", str(effective["effective_bundle_receipt_digest"]),
                "effective.effective_bundle_receipt_digest (tools.api read-effective)")
            for index, row in enumerate(artifacts["generated_files"]):
                add(f"/artifacts/generated_files/{index}/content_digest", "sha256:" + hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest(),
                    "sha256 поля content этого файла (UTF-8)")
            if instance == "r2":
                add("/artifacts/predecessor_automation_sha256", automation_sha256(answer("tc-to-autotest:r1", "CANDIDATE_PUBLISHED")),
                    "tools.api automation-digest ответа tc-to-autotest:r1")
                add("/artifacts/correction_review_sha256", autotest_review_sha256(answer("autotest-reviewer:r1", "MODEL_RESPONSE_RECEIVED")),
                    "digest ответа autotest-reviewer:r1")
        elif stage == "autotest-reviewer":
            review = value["artifacts"]["autotest_review"]
            automation = answer(f"tc-to-autotest:{instance}", "CANDIDATE_PUBLISHED")
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, f"automation-review-boundary-{instance}", "ARTIFACT_READ_BACK")["record"]
            add("/artifacts/autotest_review/automation_sha256", automation_sha256(automation), f"tools.api automation-digest ответа tc-to-autotest:{instance}")
            add("/artifacts/autotest_review/reviewed_relations_sha256", implementation_relations_sha256(automation["artifacts"]["implementation_relations"]),
                f"tools.api relations-digest массива implementation_relations ответа tc-to-autotest:{instance} (не digest артефакта)")
            add("/artifacts/autotest_review/host_isolation_sha256", str(boundary["digest"]), f"boundary.digest (read-receipt automation-review-boundary-{instance})")
            for key in ("source_digest", "effective_bundle_receipt_digest"):
                add(f"/artifacts/autotest_review/source/{key}", str(automation["artifacts"]["source"][key]), f"source.{key} ответа tc-to-autotest:{instance}")
            files = {row["file_id"]: row["content_digest"] for row in automation["artifacts"]["generated_files"]}
            for index, row in enumerate(review["reviewed_files"]):
                if row["file_id"] in files:
                    add(f"/artifacts/autotest_review/reviewed_files/{index}/content_digest", files[row["file_id"]],
                        f"content_digest файла {row['file_id']} в ответе tc-to-autotest:{instance}")
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return rows
    return rows


def _pointer_tokens(pointer: str) -> list[str | int]:
    return [int(token) if token.isdigit() else token for token in pointer.strip("/").split("/")]


def _pointer_get(value: Any, pointer: str) -> Any:
    for token in _pointer_tokens(pointer):
        if isinstance(token, int) and isinstance(value, list) and token < len(value):
            value = value[token]
        elif isinstance(token, str) and isinstance(value, Mapping):
            value = value.get(token)
        else:
            return None
    return value


def _self_digest(value: Mapping[str, Any]) -> str:
    return _model_stage_artifact_digest({key: item for key, item in value.items() if key != "digest"})


def _response_digest_rows(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: Mapping[str, Any],
) -> list[tuple[str, Any, str, str]]:
    """FaT-DG: the digest fields of a model answer against the journal — (pointer, given, expected, source) per mismatch."""
    rows = []
    for pointer, expected, source in _response_bindings(project, root, state, attempt_id, stage_instance_id, request, value):
        expected = _self_digest(value) if expected is None else expected
        given = _pointer_get(value, pointer)
        if given != expected:
            rows.append((pointer, given, expected, source))
    return rows


def _fill_response_bindings(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Audit U2: the digest fields of ``_response_bindings`` the answer leaves out (no key, null or "") — the code puts the
    journal value there, the fragment self-digest last.  Only these fields; a field given with another value is left as it
    is for the refusal (MODEL_RESPONSE_DIGEST_MISMATCH) — never mended silently."""
    filled: list[str] = []
    value = json.loads(json.dumps(value, ensure_ascii=False))
    self_digest = None
    for pointer, expected, _source in _response_bindings(project, root, state, attempt_id, stage_instance_id, request, value):
        if _pointer_get(value, pointer) not in (None, ""):
            continue
        if expected is None:
            self_digest = pointer
            continue
        tokens = _pointer_tokens(pointer)
        parent: Any = value
        for token in tokens[:-1]:
            if isinstance(token, int):
                if not isinstance(parent, list) or token >= len(parent):
                    parent = None
                    break
                parent = parent[token]
            else:
                if not isinstance(parent, dict):
                    parent = None
                    break
                parent = parent.setdefault(token, {})
        if isinstance(parent, dict):
            parent[str(tokens[-1])] = expected
            filled.append(pointer)
    if self_digest is not None:
        value["digest"] = _self_digest(value)
        filled.append(self_digest)
    return value, filled


def publish_generated_delta(run_root: Path, attempt_id: str, delta: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bind one complete materialization receipt to the active attempt and read it back."""
    published = publish_attempt_receipt(run_root, attempt_id, "generated-delta", {"delta": dict(delta)})
    _project, root = _run_root(run_root)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    return read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")


def publish_review_canaries(run_root: Path, attempt_id: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """P7: bind the digests of the canary map and of the reviewer's view to the attempt: the receipt and its event pair, marked with
    ``batch_id`` ``review-canaries-v1`` — whether canaries were seeded is read from these events, not from a file.  The same facts
    again change nothing."""
    from tools.review_canaries import BATCH_ID

    published = publish_attempt_receipt(run_root, attempt_id, "review-canaries", dict(facts))
    _project, root = _run_root(run_root)
    seen = {event["event_type"] for event in derive_state(root)["events"] if event.get("attempt_id") == attempt_id
            and event.get("batch_id") == BATCH_ID and event.get("artifact_digest") == published["digest"]}
    for event_type in ("ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"):
        if event_type not in seen:
            append_event(root, event_type, actor="controller", attempt_id=attempt_id, batch_id=BATCH_ID, artifact_digest=published["digest"])
    return {**read_attempt_receipt(root, attempt_id, "review-canaries", "ARTIFACT_READ_BACK"),
            "created": published["created"], "installed_identity": published["installed_identity"]}


def publish_phase5_receipt(run_root: Path, attempt_id: str, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back one fixed-slot Phase 5 receipt before its project effect."""
    if kind not in {"materialization-ownership", "disposition-plan", "disposition-receipt"}:
        raise ValueError("invalid phase5 receipt kind")
    published = publish_attempt_receipt(run_root, attempt_id, kind, {"payload": dict(payload)})
    _project, root = _run_root(run_root)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    readback = read_attempt_receipt(run_root, attempt_id, kind, "ARTIFACT_READ_BACK")
    return {**readback, "created": published["created"], "installed_identity": published["installed_identity"]}


def remove_attempt_receipt_if_created(run_root: Path, receipt: Mapping[str, Any]) -> bool:
    project, root = _run_root(run_root)
    identity = receipt.get("installed_identity")
    if receipt.get("created") is not True or not isinstance(receipt.get("path"), str) or not isinstance(receipt.get("bytes"), bytes) or not isinstance(identity, tuple):
        return False
    try:
        return remove_confined_bytes_if_equal(project, root, root / receipt["path"], receipt["bytes"], expected_identity=identity)
    except OutputConfinementError as error:
        raise ValueError("factual receipt rollback failed") from error


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
    # FaT-PA: the automated subset passed, the cases with blockers stayed manual
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
    # FaT-4b: SELF marks a review admitted by `scan --accept-self-review`; it never stands next to a verified isolation.
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
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=result["attempt_id"], artifact_digest=record["digest"])
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=result["attempt_id"], artifact_digest=record["digest"])
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


def publish_run_artifact_bytes(run_root: Path, attempt_id: str, relative_name: str, data: bytes) -> Mapping[str, Any]:
    """Write/read back one run-scoped durable byte artifact under artifacts/<attempt>/."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("run artifact bytes are required")
    if not isinstance(relative_name, str) or not relative_name or "\x00" in relative_name:
        raise ValueError("invalid run artifact path")
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("invalid run artifact path")
    project, root = _run_root(run_root)
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid run artifact attempt")
    target = root / "artifacts" / attempt_id / relative
    created = create_confined_bytes_exclusive(project, root, target, bytes(data))
    readback = read_confined_bytes(project, root, target)
    if readback != bytes(data):
        raise ValueError("run artifact read-back mismatch")
    digest = "sha256:" + hashlib.sha256(bytes(data)).hexdigest()
    return {
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "digest": digest,
        "created": bool(created),
    }


def _scenario_observation_target(root: Path, attempt_id: str, scenario_id: str) -> Path:
    allowed = {
        "fresh-non-git", "tree-preservation", "waiting-for-model-resume",
        "one-user-question-resume", "terminal-idempotent-retry", "retained-native-rerun",
    }
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or scenario_id not in allowed:
        raise ValueError("invalid scenario observation identity")
    return root / "scenario-observations" / f"{attempt_id}.{scenario_id}.json"


def _project_state_for(policy_profile: str, project: Path) -> dict[str, Any] | None:
    """What a run says about git in the project at its start — by looking at the file system, never by starting ``git``.

    A project without ``.git`` is sealed as ``GIT_DIRECTORY`` (the precondition of the scenario ``fresh-non-git``); a project with it, and
    every ``cases-only-v1`` run, has ``project_state: null`` — "not taken", which no reader takes for "clean".  That the run did not touch
    the user's files is proved at its end by the inventory (``_project_tree_state``), not by ``git status`` (V4a: a clean filter or a hook
    of the project's own configuration runs there)."""
    if policy_profile == "cases-only-v1" or (Path(project) / ".git").exists():
        return None
    return _sealed({"kind": "GIT_DIRECTORY", "path": ".git", "entries": []})


def _project_tree_state(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """V4a: the user's tree as the inventory sees it — the number of files and the digest of their ``(path, content digest)`` pairs."""
    pairs = sorted((str(row["project_path"]), str(row["content_digest"])) for row in rows)
    return _sealed({"kind": "PROJECT_TREE", "files": len(pairs),
                    "tree_digest": "sha256:" + hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()})


def _owned_project_paths(root: Path, attempt: Mapping[str, Any]) -> set[str]:
    """The project paths the pipeline owns in the attempt: the files of its generated delta (relative to the module, so prefixed here)."""
    try:
        delta = read_attempt_receipt(root, str(attempt["attempt_id"]), "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
    except (KeyError, OSError, TypeError, ValueError):
        return set()
    prefix = "" if attempt.get("module") == "." else str(attempt.get("module")).rstrip("/") + "/"
    return {prefix + str(row["path"]) for row in delta.get("files", ()) if isinstance(row, Mapping)}


def _project_tree_states(project: Path, root: Path, attempt: Mapping[str, Any], inventory: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """V4a: ``(before, after)`` — the frozen inventory of the attempt, and the inventory rebuilt now, both without the files the pipeline
    owns.  The same rebuild as ``run_pipeline._baseline_check`` (the same arguments), so what ``status`` calls a drift is what is compared."""
    from tools.project_inventory import baseline_external_input_paths, build_inventory

    owned = _owned_project_paths(root, attempt)
    baseline = frozen_baseline(root, attempt)
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    current = build_inventory(project, module_root, skill_pack_root=Path(__file__).resolve().parents[1], generated_roots=(root.parent,),
                              proved_dependency_files=baseline_external_input_paths(project, baseline))
    keep = lambda row: row.get("project_path") not in owned  # noqa: E731
    return (_project_tree_state(row for row in inventory["files"] if keep(row)),
            _project_tree_state(row for row in current["files"] if keep(row)))


def _terminal_retry_snapshot(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """Seal the terminal bytes and event counts a retry is forbidden to change."""
    terminal = read_terminal_result(run_root, attempt_id)
    closure = [
        read_closure_artifact_if_present(run_root, attempt_id, kind)
        for kind in ("pre_finalization_trace", "finalization_receipt", "terminal_trace")
    ]
    if any(not isinstance(row, Mapping) for row in closure):
        raise ValueError("terminal retry closure is incomplete")
    state = derive_state(run_root)
    attempt_events = [
        row for row in state["events"] if row.get("attempt_id") == attempt_id
    ]
    return _sealed({
        "kind": "TERMINAL",
        "terminal_digest": terminal["digest"],
        "terminal_event_count": sum(
            row.get("event_type") == "ATTEMPT_TERMINAL" for row in attempt_events
        ),
        "execution_start_count": sum(
            row.get("event_type") == "EXECUTION_STARTED" for row in attempt_events
        ),
        "terminal_artifact_digests": sorted([
            terminal["digest"], *(str(row["digest"]) for row in closure if row is not None),
        ]),
    })


def _retained_native_rerun_target(root: Path, attempt_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid retained native rerun identity")
    return root / "retained-native-reruns" / f"{attempt_id}.json"


def _retained_rerun_context(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Read the accepted execution/disposition chain and current retained bytes."""
    (request_digest,) = execution_gate.from_mode_b("execution_adapters", "request_digest")

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        raise ValueError("retained native rerun requires a terminal attempt")
    terminal = read_terminal_result(root, attempt_id)
    execution = read_attempt_receipt(
        root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    delta_receipt = read_attempt_receipt(
        root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK",
    )["record"]
    disposition_receipt = read_attempt_receipt(
        root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    payload = execution.get("payload")
    delta = delta_receipt.get("delta")
    dispositions = disposition_receipt.get("payload")
    request = _execution_request_from_payload(payload) if isinstance(payload, Mapping) else None
    delta_rows = delta.get("files") if isinstance(delta, Mapping) else None
    disposition_rows = dispositions.get("files") if isinstance(dispositions, Mapping) else None
    if (
        attempt.get("policy_profile") != "local-pilot-v1"
        or terminal.get("accepted") is not True
        or terminal.get("verification") != "PASS"
        or not isinstance(payload, Mapping)
        or payload.get("verdict") != "PASS"
        or request is None
        or execution.get("execution_request_digest") != request_digest(request)
        or not isinstance(delta_rows, list)
        or not delta_rows
        or not isinstance(disposition_rows, list)
        or len(disposition_rows) != len(delta_rows)
        or dispositions.get("verification") != "PASS"
        or dispositions.get("generated_delta_digest") != delta.get("digest")
    ):
        raise ValueError("retained native rerun evidence is unavailable")
    module = (
        project
        if attempt.get("module") == "."
        else project.joinpath(*str(attempt.get("module", "")).replace("\\", "/").split("/"))
    )
    test_root = module / str(delta.get("test_root", ""))
    files: list[dict[str, str]] = []
    passed = {
        (row.get("file_id"), row.get("file_digest"))
        for row in payload.get("execution_evidence", ())
        if isinstance(row, Mapping) and row.get("status") == "PASSED"
    }
    for delta_row, disposition_row in zip(delta_rows, disposition_rows):
        if (
            not isinstance(delta_row, Mapping)
            or not isinstance(disposition_row, Mapping)
            or disposition_row.get("disposition") != "RETAINED"
            or any(
                disposition_row.get(key) != delta_row.get(key)
                for key in ("file_id", "path", "content_digest", "materialization")
            )
            or (delta_row.get("file_id"), delta_row.get("content_digest")) not in passed
        ):
            raise ValueError("retained native rerun file set is invalid")
        relative = delta_row.get("path")
        if not isinstance(relative, str):
            raise ValueError("retained native rerun file set is invalid")
        target = module.joinpath(*relative.replace("\\", "/").split("/"))
        try:
            data = read_confined_bytes(project, test_root, target)
        except OutputConfinementError as error:
            raise ValueError("retained native rerun file set is unsafe") from error
        digest = "sha256:" + hashlib.sha256(data).hexdigest() if data is not None else None
        if digest != delta_row.get("content_digest"):
            raise ValueError("retained native rerun file bytes changed")
        files.append({
            "file_id": str(delta_row["file_id"]),
            "path": relative.replace("\\", "/"),
            "digest": str(digest),
        })
    snapshot = _sealed({
        "kind": "RETAINED_FILES",
        "files": sorted(files, key=lambda row: (row["file_id"], row["path"])),
    })
    return {
        "project": project,
        "root": root,
        "attempt": attempt,
        "terminal": terminal,
        "execution_receipt": execution,
        "disposition_receipt": disposition_receipt,
        "request": request,
        "snapshot": snapshot,
    }


def read_retained_native_rerun(
    run_root: Path,
    attempt_id: str,
    expected_digest: str,
) -> Mapping[str, Any]:
    """Read and verify one successful ordinary-command rerun receipt."""
    DURABLE_NATIVE_REPORT_MAX_BYTES, DURABLE_NATIVE_REPORT_NORMALIZATION, _normalize_durable_junit_report, parse_junit_bytes = execution_gate.from_mode_b(
        "run_tests", "DURABLE_NATIVE_REPORT_MAX_BYTES", "DURABLE_NATIVE_REPORT_NORMALIZATION", "_normalize_durable_junit_report", "parse_junit_bytes",
    )

    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise ValueError("retained native rerun digest is invalid")
    context = _retained_rerun_context(run_root, attempt_id)
    project, root = context["project"], context["root"]
    target = _retained_native_rerun_target(root, attempt_id)
    record = _read_artifact(project, root, target, "retained native rerun")
    state = derive_state(root)
    publication = [
        row for row in state["events"]
        if row.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        and row.get("artifact_digest") == expected_digest
        and row.get("attempt_id") is None
    ]
    binding = [
        row for row in state["events"]
        if row.get("event_type") == "RETAINED_NATIVE_RERUN"
        and row.get("attempt_id") == attempt_id
        and row.get("artifact_digest") == expected_digest
    ]
    if (
        record.get("digest") != expected_digest
        or record.get("run_id") != context["attempt"].get("run_id")
        or record.get("attempt_id") != attempt_id
        or record.get("policy_profile") != context["attempt"].get("policy_profile")
        or record.get("terminal_result_digest") != context["terminal"].get("digest")
        or record.get("execution_receipt_digest") != context["execution_receipt"].get("digest")
        or record.get("execution_request_digest")
        != context["execution_receipt"].get("execution_request_digest")
        or record.get("disposition_receipt_digest") != context["disposition_receipt"].get("digest")
        or record.get("before") != record.get("after")
        or record.get("after") != context["snapshot"]
        or [row.get("event_type") for row in publication]
        != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
        or len(binding) != 1
        or not publication[0]["seq"] < publication[1]["seq"] < binding[0]["seq"]
    ):
        raise ValueError("retained native rerun binding is invalid")
    request = context["request"]
    report_root = str(request.report_paths[0]).replace("\\", "/").rstrip("/")
    seen_sources: set[str] = set()
    seen_paths: set[str] = set()
    for row in record["reports"]:
        source_path = str(row["source_path"])
        if (
            source_path in seen_sources
            or row["path"] in seen_paths
            or not (
                source_path == report_root
                or source_path.startswith(report_root + "/")
            )
            or row.get("before_fingerprint") == row.get("after_fingerprint")
            or row["after_fingerprint"].get("digest") != row.get("source_digest")
        ):
            raise ValueError("retained native rerun report binding is invalid")
        data = read_confined_bytes(project, root, root / str(row["path"]))
        digest = "sha256:" + hashlib.sha256(data).hexdigest() if data is not None else None
        parsed = parse_junit_bytes(data or b"")
        if (
            digest != row.get("digest")
            or row.get("normalization") != DURABLE_NATIVE_REPORT_NORMALIZATION
            or len(data or b"") > DURABLE_NATIVE_REPORT_MAX_BYTES
            or _normalize_durable_junit_report(data or b"") != data
            or not parsed.cases
            or parsed.malformed
            or parsed.duplicate_identities
            or any(case.status != "PASSED" for case in parsed.cases)
        ):
            raise ValueError("retained native rerun report is invalid")
        seen_sources.add(source_path)
        seen_paths.add(str(row["path"]))
    return {
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "bytes": _canonical_bytes(record),
        "digest": record["digest"],
        "record": record,
        "binding_event": binding[0],
        "verified": True,
    }


def _resume_observation_events(
    state: Mapping[str, Any], attempt_id: str,
) -> tuple[str, list[Mapping[str, Any]]] | None:
    attempt_events = [
        row for row in state["events"] if row.get("attempt_id") == attempt_id
    ]
    terminal_events = [
        row for row in attempt_events if row.get("event_type") == "ATTEMPT_TERMINAL"
    ]
    if len(terminal_events) != 1:
        return None
    terminal_event = terminal_events[0]
    user_waits = [
        row for row in attempt_events
        if row.get("event_type") == "WAITING_FOR_INPUT"
        and row["seq"] < terminal_event["seq"]
    ]
    if user_waits:
        scenario_id, waiting_event = "one-user-question-resume", user_waits[0]
    else:
        model_waits = [
            row for row in attempt_events
            if row.get("event_type") == "WAITING_FOR_MODEL"
            and row["seq"] < terminal_event["seq"]
        ]
        if not model_waits:
            return None
        scenario_id, waiting_event = "waiting-for-model-resume", model_waits[0]
    requests = [
        row for row in attempt_events
        if row.get("event_type") == "MODEL_REQUESTED"
        and waiting_event["seq"] < row["seq"] < terminal_event["seq"]
    ]
    if not requests:
        return None
    request_event = requests[0]
    responses = [
        row for row in attempt_events
        if row.get("event_type") == "MODEL_RESPONSE_RECEIVED"
        and row.get("stage_instance_id") == request_event.get("stage_instance_id")
        and request_event["seq"] < row["seq"] < terminal_event["seq"]
    ]
    if not responses:
        return None
    return scenario_id, [waiting_event, request_event, responses[0], terminal_event]


def _validate_scenario_observation(
    root: Path,
    attempt: Mapping[str, Any],
    scenario_id: str,
    observation: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    expected = {
        "fresh-non-git": ("NON_GIT_PROJECT", "RUN_PIPELINE"),
        "tree-preservation": ("PROJECT_TREE", "RUN_PRESERVING_TREE"),
        "waiting-for-model-resume": ("MODEL_WAITING", "RESUME_MODEL_ATTEMPT"),
        "one-user-question-resume": ("USER_QUESTION_PENDING", "RESUME_AFTER_USER_ANSWER"),
        "terminal-idempotent-retry": ("TERMINAL_ATTEMPT", "RETRY_TERMINAL_ATTEMPT"),
        "retained-native-rerun": ("RETAINED_FILE_SET", "PROJECT_NATIVE_RERUN"),
    }[scenario_id]
    terminal = read_terminal_result(root, str(attempt["attempt_id"]))
    before = _check_sealed(observation["before"], "scenario observation before")
    after = _check_sealed(observation["after"], "scenario observation after")
    precondition, action = observation["precondition"], observation["action"]
    if (
        observation.get("run_id") != attempt.get("run_id")
        or observation.get("attempt_id") != attempt.get("attempt_id")
        or observation.get("policy_profile") != attempt.get("policy_profile")
        or observation.get("scenario_id") != scenario_id
        or (precondition.get("kind"), precondition.get("snapshot_digest")) != (expected[0], before["digest"])
        or (action.get("kind"), action.get("before_digest"), action.get("after_digest"))
        != (expected[1], before["digest"], after["digest"])
        or observation.get("result") != {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]}
        or terminal["digest"] not in observation.get("receipt_digests", ())
    ):
        raise ValueError("scenario observation binding is invalid")
    project, root = _run_root(root)
    state = derive_state(root)
    indexed = {
        row["digest"]: (index, row)
        for index, row in enumerate(state["events"])
    }
    try:
        selected = [indexed[digest] for digest in observation["event_digests"]]
    except KeyError as error:
        raise ValueError("scenario observation journal binding is invalid") from error
    if (
        [index for index, _row in selected] != sorted(index for index, _row in selected)
        or any(
            row.get("run_id") != attempt.get("run_id")
            or row.get("attempt_id") not in {None, attempt.get("attempt_id")}
            for _index, row in selected
        )
    ):
        raise ValueError("scenario observation journal binding is invalid")
    selected_events = [row for _index, row in selected]
    artifact_binding = [
        row for row in state["events"]
        if row.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        and row.get("artifact_digest") == observation.get("digest")
        and row.get("run_id") == attempt.get("run_id")
        and row.get("attempt_id") is None
    ]
    if (
        [row.get("event_type") for row in artifact_binding]
        != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
        or not selected
        or selected[-1][0] >= artifact_binding[0].get("seq", -1)
    ):
        raise ValueError("scenario observation publication binding is invalid")
    binding_event: Mapping[str, Any] | None = None
    if scenario_id in {"fresh-non-git", "tree-preservation"}:
        from tools.project_inventory import read_execution_baseline, read_inventory_receipt

        manifest = read_run(root)["manifest"]
        initial = manifest.get("project_state")
        baseline = read_execution_baseline(
            root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
        )
        inventory = read_inventory_receipt(
            root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json")
        )
        selected_valid = (
            [row.get("event_type") for row in selected_events]
            == ["RUN_CREATED", "SNAPSHOT_BOUND", "ATTEMPT_TERMINAL"]
            and selected_events[0].get("artifact_digest") == manifest.get("digest")
            and selected_events[1].get("artifact_digest") == attempt.get("baseline_digest")
            and selected_events[2].get("artifact_digest") == terminal.get("digest")
            and observation.get("receipt_digests") == [inventory["digest"], terminal["digest"]]
        )
        if scenario_id == "fresh-non-git":
            valid = (
                isinstance(initial, Mapping)
                and initial.get("kind") == "GIT_DIRECTORY"
                and before == initial
                and after == _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
            )
        else:
            # V4a: no state in the manifest — the "before" is the frozen inventory of the attempt (without the files the pipeline owns)
            owned = _owned_project_paths(root, attempt)
            valid = (
                initial is None
                and attempt.get("policy_profile") == "local-pilot-v1"
                and before == after == _project_tree_state(row for row in inventory["files"] if row.get("project_path") not in owned)
            )
        if not selected_valid or not valid:
            raise ValueError("project state observation is invalid")
    elif scenario_id in {"waiting-for-model-resume", "one-user-question-resume"}:
        derived = _resume_observation_events(state, str(attempt["attempt_id"]))
        if derived is None:
            raise ValueError("resume observation lifecycle is invalid")
        derived_scenario, derived_events = derived
        waiting_event, request_event, response_event, terminal_event = derived_events
        stage_instance_id = str(request_event.get("stage_instance_id", ""))
        request_digest = str(request_event.get("artifact_digest", ""))
        response_digest = str(response_event.get("artifact_digest", ""))
        _read_model_request_with_state(
            project, root, state, str(attempt["attempt_id"]), stage_instance_id, request_digest,
        )
        _read_model_stage_artifact_with_state(
            project, root, state, str(attempt["attempt_id"]), stage_instance_id, response_digest,
        )
        selected_valid = (
            derived_scenario == scenario_id
            and [row["digest"] for row in selected_events]
            == [row["digest"] for row in derived_events]
            and terminal_event.get("artifact_digest") == terminal.get("digest")
            and observation.get("receipt_digests")
            == [request_digest, response_digest, terminal["digest"]]
        )
        if scenario_id == "waiting-for-model-resume":
            expected_before = _sealed({
                "kind": "EVENT", "event_digest": waiting_event["digest"],
            })
            expected_after = _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
        else:
            waiting_receipt = read_attempt_receipt(
                root, str(attempt["attempt_id"]), "structured-result", "WAITING_FOR_MODEL",
            )
            question_digest = str(waiting_event.get("artifact_digest", ""))
            model_waits = [
                row for row in state["events"]
                if row.get("attempt_id") == attempt.get("attempt_id")
                and row.get("event_type") == "WAITING_FOR_MODEL"
                and row.get("artifact_digest") == question_digest
                and row["seq"] < waiting_event["seq"]
            ]
            expected_before = _sealed({
                "kind": "USER_INTERACTION",
                "question_digests": [question_digest],
                "answer_digests": [],
            })
            expected_after = _sealed({
                "kind": "USER_INTERACTION",
                "question_digests": [question_digest],
                "answer_digests": [request_digest],
            })
            selected_valid = (
                selected_valid
                and question_digest == waiting_receipt["digest"]
                and len(model_waits) == 1
            )
        if not selected_valid or before != expected_before or after != expected_after:
            raise ValueError("resume observation is invalid")
    elif scenario_id == "retained-native-rerun":
        start_events = [
            row for row in state["events"]
            if row.get("event_type") == "EXECUTION_STARTED"
            and row.get("attempt_id") == attempt.get("attempt_id")
        ]
        terminal_events = [
            row for row in state["events"]
            if row.get("event_type") == "ATTEMPT_TERMINAL"
            and row.get("attempt_id") == attempt.get("attempt_id")
        ]
        rerun_events = [
            row for row in state["events"]
            if row.get("event_type") == "RETAINED_NATIVE_RERUN"
            and row.get("attempt_id") == attempt.get("attempt_id")
        ]
        if len(start_events) != 1 or len(terminal_events) != 1 or len(rerun_events) != 1:
            raise ValueError("retained native rerun lifecycle is invalid")
        start_event, terminal_event, rerun_event = (
            start_events[0], terminal_events[0], rerun_events[0]
        )
        rerun = read_retained_native_rerun(
            root, str(attempt["attempt_id"]), str(rerun_event.get("artifact_digest", "")),
        )
        rerun_record = rerun["record"]
        expected_receipts = [
            rerun_record["execution_receipt_digest"],
            rerun_record["disposition_receipt_digest"],
            rerun_record["digest"],
            terminal["digest"],
        ]
        if (
            [row.get("digest") for row in selected_events]
            != [start_event["digest"], terminal_event["digest"], rerun_event["digest"]]
            or not start_event["seq"] < terminal_event["seq"] < rerun_event["seq"]
            or start_event.get("artifact_digest") != rerun_record["execution_request_digest"]
            or terminal_event.get("artifact_digest") != terminal["digest"]
            or rerun.get("binding_event") != rerun_event
            or before != rerun_record["before"]
            or after != rerun_record["after"]
            or before != after
            or observation.get("receipt_digests") != expected_receipts
        ):
            raise ValueError("retained native rerun observation is invalid")
        binding_event = rerun_event
    elif scenario_id == "terminal-idempotent-retry":
        terminal_events = [
            row for row in state["events"]
            if row.get("attempt_id") == attempt.get("attempt_id")
            and row.get("event_type") == "ATTEMPT_TERMINAL"
        ]
        current_snapshot = _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
        retry_binding = [
            row for row in state["events"]
            if row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
            and row.get("artifact_digest") == observation.get("digest")
            and row.get("run_id") == attempt.get("run_id")
            and row.get("attempt_id") == attempt.get("attempt_id")
        ]
        post_terminal = (
            len(terminal_events) == 1
            and [row.get("event_type") for row in artifact_binding]
            == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
            and len(retry_binding) == 1
            and terminal_events[0]["seq"] < artifact_binding[0]["seq"]
            < artifact_binding[1]["seq"] < retry_binding[0]["seq"]
        )
        later_execution = any(
            row.get("attempt_id") == attempt.get("attempt_id")
            and row.get("event_type") == "EXECUTION_STARTED"
            and row.get("seq", 0) > terminal_events[0]["seq"]
            for row in state["events"]
        ) if terminal_events else True
        if (
            len(terminal_events) != 1
            or later_execution
            or not post_terminal
            or [row.get("digest") for row in selected_events] != [terminal_events[0]["digest"]]
            or before != after
            or before != current_snapshot
            or observation.get("receipt_digests") != current_snapshot["terminal_artifact_digests"]
        ):
            raise ValueError("terminal retry observation is invalid")
        binding_event = retry_binding[0]
    return selected_events, binding_event


def read_scenario_observation(
    run_root: Path,
    attempt_id: str,
    scenario_id: str,
    expected_digest: str,
) -> Mapping[str, Any]:
    """Read one controller-owned scenario receipt from its fixed run location."""
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise ValueError("scenario observation digest is invalid")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        raise ValueError("scenario observation attempt is invalid")
    target = _scenario_observation_target(root, attempt_id, scenario_id)
    observation = _read_artifact(project, root, target, "scenario observation")
    if observation.get("digest") != expected_digest:
        raise ValueError("scenario observation digest mismatch")
    events, binding_event = _validate_scenario_observation(
        root, attempt, scenario_id, observation,
    )
    data = _canonical_bytes(observation)
    if read_confined_bytes(project, root, target) != data:
        raise ValueError("scenario observation read-back mismatch")
    return {
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "bytes": data,
        "digest": observation["digest"],
        "record": observation,
        "events": events,
        "binding_event": binding_event,
        "verified": True,
    }


def _publish_derived_scenario_observation(
    run_root: Path,
    attempt_id: str,
    scenario_id: str,
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    target = _scenario_observation_target(root, attempt_id, scenario_id)
    record, created, identity = _publish(
        project, root, target, value, "scenario observation", return_created=True,
    )
    append_event(
        root, "ARTIFACT_PUBLISHED", actor="controller",
        artifact_digest=str(record["digest"]),
    )
    append_event(
        root, "ARTIFACT_READ_BACK", actor="controller",
        artifact_digest=str(record["digest"]),
    )
    loaded = dict(read_scenario_observation(
        root, attempt_id, scenario_id, str(record["digest"]),
    ))
    loaded.update({"created": created, "installed_identity": identity})
    return loaded


def publish_project_state_observation(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Derive project-state qualification evidence after a successful terminal action."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        return None
    terminal = read_terminal_result(root, attempt_id)
    if terminal.get("accepted") is not True or terminal.get("verification") != "PASS":
        return None
    manifest = read_run(root)["manifest"]
    before = manifest.get("project_state")
    if before is None:  # V4a: no state — the tree of a local-pilot run is compared by the inventory (below); a cases-only run takes none
        if attempt.get("policy_profile") != "local-pilot-v1":
            return None
    elif not isinstance(before, Mapping) or before.get("kind") != "GIT_DIRECTORY":
        return None
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt

    baseline = read_execution_baseline(
        root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
    )
    inventory = read_inventory_receipt(
        root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json")
    )
    selected = [
        next(row for row in state["events"] if row.get("event_type") == "RUN_CREATED"),
        next(
            row for row in state["events"]
            if row.get("event_type") == "SNAPSHOT_BOUND"
            and row.get("artifact_digest") == attempt["baseline_digest"]
        ),
        next(
            row for row in state["events"]
            if row.get("event_type") == "ATTEMPT_TERMINAL"
            and row.get("attempt_id") == attempt_id
        ),
    ]
    if isinstance(before, Mapping) and before.get("kind") == "GIT_DIRECTORY":
        scenario_id, precondition, action = "fresh-non-git", "NON_GIT_PROJECT", "RUN_PIPELINE"
        after = _terminal_retry_snapshot(root, attempt_id)
    elif before is None:
        scenario_id, precondition, action = "tree-preservation", "PROJECT_TREE", "RUN_PRESERVING_TREE"
        before, after = _project_tree_states(project, root, attempt, inventory)
        if after != before:  # a foreign file changed, went or came during the run: no evidence, not a wrong evidence
            return None
    else:
        return None
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"], "scenario_id": scenario_id,
        "precondition": {"kind": precondition, "snapshot_digest": before["digest"]},
        "action": {
            "kind": action, "before_digest": before["digest"],
            "after_digest": after["digest"],
        },
        "before": dict(before), "after": after,
        "event_digests": [row["digest"] for row in selected],
        "receipt_digests": [inventory["digest"], terminal["digest"]],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    return _publish_derived_scenario_observation(root, attempt_id, scenario_id, value)


def publish_resume_observations(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Mapping[str, Any]]:
    """Derive one completed wait/resume qualification observation from durable events."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        return {}
    terminal = read_terminal_result(root, attempt_id)
    if terminal.get("accepted") is not True or terminal.get("verification") != "PASS":
        return {}
    derived = _resume_observation_events(state, attempt_id)
    if derived is None:
        return {}
    scenario_id, selected = derived
    waiting_event, request_event, response_event, _terminal_event = selected
    stage_instance_id = str(request_event.get("stage_instance_id", ""))
    request_digest = str(request_event.get("artifact_digest", ""))
    response_digest = str(response_event.get("artifact_digest", ""))
    _read_model_request_with_state(project, root, state, attempt_id, stage_instance_id, request_digest)
    _read_model_stage_artifact_with_state(project, root, state, attempt_id, stage_instance_id, response_digest)
    if scenario_id == "waiting-for-model-resume":
        precondition, action = "MODEL_WAITING", "RESUME_MODEL_ATTEMPT"
        before = _sealed({"kind": "EVENT", "event_digest": waiting_event["digest"]})
        after = _terminal_retry_snapshot(root, attempt_id)
    else:
        precondition, action = "USER_QUESTION_PENDING", "RESUME_AFTER_USER_ANSWER"
        waiting_receipt = read_attempt_receipt(
            root, attempt_id, "structured-result", "WAITING_FOR_MODEL",
        )
        question_digest = str(waiting_event.get("artifact_digest", ""))
        if question_digest != waiting_receipt["digest"]:
            return {}
        before = _sealed({
            "kind": "USER_INTERACTION",
            "question_digests": [question_digest],
            "answer_digests": [],
        })
        after = _sealed({
            "kind": "USER_INTERACTION",
            "question_digests": [question_digest],
            "answer_digests": [request_digest],
        })
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"], "scenario_id": scenario_id,
        "precondition": {"kind": precondition, "snapshot_digest": before["digest"]},
        "action": {
            "kind": action, "before_digest": before["digest"],
            "after_digest": after["digest"],
        },
        "before": before, "after": after,
        "event_digests": [row["digest"] for row in selected],
        "receipt_digests": [request_digest, response_digest, terminal["digest"]],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    published = _publish_derived_scenario_observation(root, attempt_id, scenario_id, value)
    return {scenario_id: published}


def publish_retained_rerun_observation(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Derive the retained-rerun observation from its verified receipt and events."""
    _project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        raise ValueError("retained native rerun attempt is invalid")
    terminal = read_terminal_result(root, attempt_id)
    if terminal.get("accepted") is not True or terminal.get("verification") != "PASS":
        raise ValueError("retained native rerun requires an accepted passing attempt")
    start_events = [
        row for row in state["events"]
        if row.get("event_type") == "EXECUTION_STARTED"
        and row.get("attempt_id") == attempt_id
    ]
    terminal_events = [
        row for row in state["events"]
        if row.get("event_type") == "ATTEMPT_TERMINAL"
        and row.get("attempt_id") == attempt_id
    ]
    rerun_events = [
        row for row in state["events"]
        if row.get("event_type") == "RETAINED_NATIVE_RERUN"
        and row.get("attempt_id") == attempt_id
    ]
    if len(start_events) != 1 or len(terminal_events) != 1 or len(rerun_events) != 1:
        raise ValueError("retained native rerun lifecycle is invalid")
    selected = [start_events[0], terminal_events[0], rerun_events[0]]
    if [row["seq"] for row in selected] != sorted(row["seq"] for row in selected):
        raise ValueError("retained native rerun lifecycle is invalid")
    rerun = read_retained_native_rerun(
        root, attempt_id, str(rerun_events[0].get("artifact_digest", "")),
    )
    record = rerun["record"]
    before, after = record["before"], record["after"]
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"],
        "scenario_id": "retained-native-rerun",
        "precondition": {
            "kind": "RETAINED_FILE_SET", "snapshot_digest": before["digest"],
        },
        "action": {
            "kind": "PROJECT_NATIVE_RERUN", "before_digest": before["digest"],
            "after_digest": after["digest"],
        },
        "before": before, "after": after,
        "event_digests": [row["digest"] for row in selected],
        "receipt_digests": [
            record["execution_receipt_digest"], record["disposition_receipt_digest"],
            record["digest"], terminal["digest"],
        ],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    return _publish_derived_scenario_observation(
        root, attempt_id, "retained-native-rerun", value,
    )


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


# Public names for execution_core / finalize_attempt (V1): the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.
append_journal_event = _append_event
check_sealed_value = _check_sealed
frozen_inventory = _frozen_inventory
publish_artifact_value = _publish
read_artifact_value = _read_artifact
sealed_value = _sealed
retained_native_rerun_target = _retained_native_rerun_target
retained_rerun_context = _retained_rerun_context
scenario_observation_target = _scenario_observation_target
run_root_of = _run_root
terminal_retry_snapshot = _terminal_retry_snapshot
generator_input = _generator_input  # V7: the published plan or header of the attempt (step_size, for the projection of the plan)
validate_execution_event_branch = _validate_execution_event_branch
