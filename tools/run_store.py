"""The files of a run (v0.8, the cut of the core): canonical bytes, the seal and its check, the strict parse, the schemas, the root of
a run and its lock, the publication and the reading of a sealed file, the authorization and the labels, the move of a project, the
terminal result of an attempt and its exit code.  The lowest module of the journal: it imports none of them.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/r4.tsv``);
``pilot_state`` takes from here what its code reads and still serves every name to its callers (the facade of the cut).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from tools.schema_engine import Draft202012Validator, FormatChecker

from tools import run_lock
from tools.confined_output import OutputConfinementError, create_confined_bytes_exclusive, read_confined_bytes, remove_confined_bytes_if_equal


_PROFILES = {"cases-only-v1", "local-pilot-v1"}
_AUTH_KEYS = {"request_id", "execution_requested", "host_id", "accept_self_review", "disposition_policy"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_MIXED_TOKEN = re.compile(r"^(?=.{24,}$)(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])[A-Za-z0-9_-]+$")


_MODEL_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


_SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"
_PACK_ROOT = _SCHEMA_ROOT.parent


class _InputRefusal(ValueError):
    """An input error met while the journal is read — no journal, or a run of another project: never a damaged journal."""

    code = "INPUT_INVALID"


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


# inside one closure the same bytes are derived and validated once.  Keys are content digests — the
# journal for the state, the receipt bytes and the journal's last digest for a validation — so every write of
# the closure itself changes the key.  Every file is still read through read_confined_bytes each time; only the
# successful validation is remembered, never an error.  Outside closure_cache() nothing is cached.
_CLOSURE_CACHE: contextvars.ContextVar[dict[Any, Any] | None] = contextvars.ContextVar("pilot_closure_cache", default=None)


def read_run(run_root: Path) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    manifest, receipt = _run_boundary(project, root)
    return {"run_root": str(root), "manifest": manifest, "authorization": receipt}


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


# public names of the same objects: what ``pilot_state`` reads and serves (П1 of the cut of v0.8)
CLOSURE_CACHE = _CLOSURE_CACHE
DIGEST = _DIGEST
InputRefusal = _InputRefusal
PACK_ROOT = _PACK_ROOT
SCHEMA_ROOT = _SCHEMA_ROOT
UNSAFE_RUN_ROOT = _UNSAFE_RUN_ROOT
canonical_bytes = _canonical_bytes
canonical_digest = _digest
check_sealed_value = _check_sealed
credential_like = _credential_like
is_reparse = _is_reparse
moved_recorded = _moved_recorded
parse_json_bytes = _parse_json_bytes
project_root = _project_root
project_state_for = _project_state_for
publish_artifact_value = _publish
read_artifact = _read_artifact
run_boundary = _run_boundary
run_root_of = _run_root
safe_label = _safe_label
safe_model_label = _safe_model_label
sealed = _sealed
validate_authorization = _validate_authorization
validate_result_record = _validate_result_record
validate_schema = _validate_schema
