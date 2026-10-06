"""Deterministic scan/status/exec helpers for the CLI-driven skill pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    _host_root = Path(__file__).resolve().parents[1]
    if str(_host_root) not in sys.path:
        sys.path.insert(0, str(_host_root))

import hashlib
import json
import os
import re
import uuid
from typing import Any, Mapping

from tools.json_cli import JsonArgumentParser
from tools.project_inventory import (
    ContextSelectionError,
    InventoryError,
    build_inventory,
    select_context_batches,
)

ROOT = Path(__file__).resolve().parents[1]

_SCAN_CONTEXT_BYTE_BUDGET = 256 * 1024


class HostStop(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _slug(target: str | None, docs: list[str] | None) -> str:
    if target:
        stem = Path(str(target).replace("\\", "/")).stem
        return re.sub(r"[^A-Za-z0-9._-]+", "-", stem) or "feature"
    if docs and len(docs) == 1:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(docs[0]).stem).strip("-") or "project"
    return "project"


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(path.is_symlink() or attributes & 0x400)


def _confined(project: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    resolved.relative_to(project.resolve())
    return resolved


def _durable_status(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    from tools import pilot_state as pilot
    from tools.project_inventory import read_execution_baseline

    root = Path(run_root).resolve()
    run = pilot.read_run(root)
    state = pilot.derive_state(root)
    attempt = state["attempts"][-1] if state["attempts"] else None
    result = {
        "status": status,
        "stage": stage,
        "project": str(Path(project).resolve()),
        "run_id": run["manifest"]["run_id"],
        "run_root": str(root),
        "attempt_id": attempt.get("attempt_id") if attempt else None,
        "attempt_state": attempt.get("state") if attempt else None,
        "event_count": len(state["events"]),
        "last_confirmed_stage": "run-created",
        "next_expected_artifact": "module selection, inventory and profile baseline",
        "stop_reason": None,
        "evidence_path": str(root / "run-manifest.json"),
        "warnings": [], "warning_evidence_path": None, "evidence_errors": [],
        **extra,
    }
    if attempt is None:
        result["stop_detail"] = "No attempt yet; an early scan failure may have no persisted cause."
        return result
    attempt_id = attempt["attempt_id"]
    events = [row for row in state["events"] if row.get("attempt_id") == attempt_id]
    confirmed: list[tuple[int, dict[str, Any]]] = []

    def record(digest: str, label: str, next_artifact: str | None, path: str, **facts: Any) -> None:
        sequence = max((row["seq"] for row in state["events"] if row.get("artifact_digest") == digest and row.get("attempt_id") in (None, attempt_id)), default=0)
        confirmed.append((sequence, {"last_confirmed_stage": label, "next_expected_artifact": next_artifact, "evidence_path": str(root / path), **facts}))

    def unreadable(path: str) -> None:
        result["evidence_errors"].append({"code": "EVIDENCE_UNVERIFIED", "path": str(root / path)})

    # Requirement gaps survive later empty warning arrays. Current-source drift must
    # not erase an independently valid historical terminal result.
    responses = [row for row in events if row["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    marker = next((row for row in responses if row.get("stage_instance_id") == "context-marker:baseline"), None)
    model_outputs = {}
    if marker:
        marker_path = f"model-stage-artifacts/{attempt_id}/{marker['artifact_digest'][7:]}.json"
        try:
            publication = pilot.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", marker["artifact_digest"])
            model_outputs[marker["artifact_digest"]] = publication
            result["warnings"] = list(publication["artifact"]["warnings"])
            result["warning_evidence_path"] = str(root / publication["path"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(marker_path)
    if attempt["state"] == "TERMINAL":
        terminal = pilot.read_terminal_result(root, attempt_id)
        result.update(last_confirmed_stage="terminal", next_expected_artifact=None,
                      evidence_path=str(root / "terminal-results" / f"{attempt_id}.json"),
                      stop_reason=terminal.get("reason_code"))
        result.update({key: terminal[key] for key in ("completion", "verification", "coverage", "accepted")})
        return result

    baseline_path = f"baselines/{attempt['baseline_digest'][7:]}.json"
    try:
        baseline = read_execution_baseline(root / baseline_path)
        if baseline["digest"] != attempt["baseline_digest"]:
            raise ValueError("baseline digest mismatch")
        record(baseline["digest"], "baseline", "authorized context", baseline_path)
    except (OSError, KeyError, TypeError, ValueError):
        unreadable(baseline_path)
    contexts = [row for row in events if row["event_type"] == "CONTEXT_SELECTED"]
    if contexts:
        context = contexts[-1]
        path = f"context-selections/{attempt_id}/{context['artifact_digest'][7:]}.json"
        try:
            pilot.read_context_selection(root, attempt_id, context["artifact_digest"])
            record(context["artifact_digest"], "authorized-context", "context-marker response", path)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    if responses:
        response = responses[-1]
        stage_id, digest = response["stage_instance_id"], response["artifact_digest"]
        path = f"model-stage-artifacts/{attempt_id}/{digest[7:]}.json"
        try:
            publication = model_outputs.get(digest) or pilot.read_model_stage_artifact(root, attempt_id, stage_id, digest)
            next_artifact = {
                "context-marker": "generator fragments", "tc-generator": "remaining fragments or assembled candidate",
                "tc-reviewer": "reviewer ledger and effective selection or negative closure",
                "tc-to-autotest": "static automation review", "autotest-reviewer": "review disposition and materialization or rework",
            }[stage_id.split(":", 1)[0]]
            record(digest, stage_id, next_artifact, publication["path"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    assembly = next((row for row in reversed(events) if row["event_type"] == "CANDIDATE_PUBLISHED" and row.get("stage_instance_id") == "assembly"), None)
    if assembly:
        # The journal has the candidate digest, but its bundle path arrives with
        # reviewer evidence. Do not claim to have reread an unknown bundle here.
        confirmed.append((assembly["seq"], {
            "last_confirmed_stage": "assembly-publication-event",
            "next_expected_artifact": "canonical reviewer package and boundary",
            "evidence_path": str(root / "events" / f"{assembly['seq']:010d}.json"),
        }))
    ledger_events = [row for row in events if row["event_type"] == "ARTIFACT_READ_BACK" and row.get("batch_id") == "reviewer-ledger-v2-canonical"]
    if ledger_events:
        digest = ledger_events[-1]["artifact_digest"]
        path = f"reviewer-session-ledgers/{attempt_id}/canonical/{digest[7:]}.json"
        try:
            ledger = pilot.read_reviewer_session_ledger(root, attempt_id, digest)
            lifecycle = pilot.reviewer_lifecycle_projection(ledger)
            next_artifact = "reviewer evidence or verdict" if lifecycle["waiting"] else "effective canonical"
            if lifecycle["pre_verdict_abort"] or lifecycle["authoritative_verdict"] == "REJECTED":
                next_artifact = "negative branch trace and finalization"
            record(digest, "canonical-review:" + ledger["status"], next_artifact, path, stop_reason=lifecycle["abort_reason"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    # These readers are read-only; the similarly named recovery helpers may write events.
    for kind, next_artifact in (
        ("effective-canonical", "pre-finalization trace" if attempt["policy_profile"] == "cases-only-v1" else "automation artifact"),
        ("execution-inputs", "materialization"), ("generated-delta", "execution receipt"),
        ("execution-receipt", "execution trace and dispositions"), ("execution-trace", "disposition receipt"),
        ("disposition-receipt", "pre-finalization trace"), ("resume-validation", "validated inputs or child attempt"),
    ):
        path = pilot._receipt_target(root, attempt_id, kind)
        if not path.exists():
            continue
        try:
            receipt = pilot._read_attempt_receipt_with_state(project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK")
            value, facts = receipt["record"], {}
            if kind == "generated-delta" and value["delta"]["facts"]["completion"] != "COMPLETE":
                next_artifact, facts["stop_reason"] = "disposition and finalization", "MATERIALIZATION_INCOMPLETE"
            if kind == "execution-receipt":
                facts["verification"] = value["payload"]["verdict"]
                facts["execution_diagnostics"] = value["payload"].get("diagnostics", [])
            if kind == "resume-validation":
                facts["stop_reason"] = value.get("reason_code")
            record(receipt["digest"], kind, next_artifact, receipt["path"], **facts)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(str(path.relative_to(root)))
    for kind, next_artifact in (("pre_finalization_trace", "finalization receipt"), ("finalization_receipt", "terminal result and trace"), ("terminal_trace", "terminal event")):
        path = root / "closure" / attempt_id / f"{kind}.json"
        if not path.exists():
            continue
        try:
            value = pilot._read_artifact(project, root, path, kind.replace("_", " "))
            value = pilot.read_closure_artifact(root, attempt_id, kind, value["digest"])
            facts = {"stop_reason": "FINALIZATION_INVALID"} if kind == "finalization_receipt" and not value["valid"] else {}
            record(value["digest"], kind, next_artifact, str(path.relative_to(root)), **facts)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(str(path.relative_to(root)))
    if confirmed:
        result.update(max(confirmed, key=lambda row: row[0])[1])
    pending = [row for row in events if row["event_type"] == "MODEL_REQUESTED" and not any(response.get("stage_instance_id") == row.get("stage_instance_id") for response in responses)]
    if pending:
        request = pending[-1]
        path = f"model-requests/{attempt_id}/{request['artifact_digest'][7:]}.json"
        try:
            pilot.read_model_request(root, attempt_id, request["stage_instance_id"], request["artifact_digest"])
            result.update(next_expected_artifact=request["stage_instance_id"] + " response", pending_request_path=str(root / path))
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    if result["evidence_errors"]:
        result.update(stop_reason="EVIDENCE_UNVERIFIED", next_expected_artifact="validated inputs or child attempt")
    elif attempt["state"] == "WAITING_FOR_INPUT" and result["stop_reason"] is None:
        result["stop_detail"] = "WAITING_FOR_INPUT has no persisted cause; consult the originating request."
    return result


def _read_skillsrc_binding(path: Path) -> tuple[dict[str, Any], str]:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    data = path.read_bytes()
    return parse_skillsrc_bytes(data), _digest_bytes(data)


def _docs_entries(project: Path, docs: list[str] | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in docs or []:
        candidate = Path(raw)
        confined = _confined(project, candidate if candidate.is_absolute() else project / candidate)
        if _is_reparse(confined) or not confined.is_file():
            raise HostStop("SNAPSHOT_DOCS", f"docs path is missing or a reparse point: {raw}")
        rel = confined.relative_to(project.resolve()).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        data = confined.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"docs file is not UTF-8: {raw}") from error
        entries.append({"path": rel, "sha256": _digest_bytes(data), "content": text})
    entries.sort(key=lambda row: row["path"])
    return entries


def _docs_projection(snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"path": row["path"], "sha256": row["sha256"]} for row in snapshot]


def _init_skillsrc_run(project: Path, args: Any) -> tuple[Mapping[str, Any], dict[str, Any]]:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.pilot_state import create_run

    run = create_run(
        project,
        getattr(args, "profile", "local-pilot-v1"),
        {"request_id": f"scan-{uuid.uuid4().hex[:12]}", "execution_requested": getattr(args, "profile", "local-pilot-v1") == "local-pilot-v1"},
    )
    if (project / ".skillsrc").is_file():
        _read_skillsrc_binding(project / ".skillsrc")
        receipt = {"status": "unchanged"}
    else:
        # ``--target`` is C-lite context scope, not an instruction to mutate .skillsrc.
        receipt = ensure_skillsrc(project, {}, write=True)
    return run, receipt


def _skillsrc_receipt_exit(project: Path, run_root: Path, receipt: Mapping[str, Any]) -> int | None:
    status = receipt.get("status")
    if status in {"needs_input", "conflict"}:
        _print(_durable_status(project, run_root, stage="skillsrc", status=status, reason=status))
        return 3
    if status not in {"created", "updated", "unchanged"}:
        reason = "SOURCE_TARGET_INVALID" if receipt.get("errors") == ["source_target_invalid"] else status or "error"
        _print(_durable_status(project, run_root, stage="skillsrc", status="error", reason=reason))
        return 2
    return None


def _resolve_scan_module(project: Path, module_id: str | None, skillsrc_document: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Select one exact existing module without constructing an execution adapter."""
    from tools.skillsrc_manifest import normalize_skillsrc, resolve_module_root, select_module

    normalized = normalize_skillsrc(skillsrc_document)
    module = select_module(normalized, module_id)
    return resolve_module_root(project, module), module


def _scan_skill_pack_root(project: Path) -> Path | None:
    try:
        ROOT.resolve().relative_to(project.resolve())
    except ValueError:
        return None
    return ROOT


def _context_scope_ids(inventory: Mapping[str, Any], project: Path, module_root: Path, target: str | None) -> list[str]:
    files = inventory.get("files")
    if not isinstance(files, list):
        raise HostStop("INVENTORY_INVALID", "inventory files are invalid")
    if target is None:
        return [str(item["opaque_id"]) for item in files if isinstance(item, Mapping) and isinstance(item.get("opaque_id"), str)]
    raw = str(target).replace("\\", "/").strip()
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts) or ":" in raw:
        raise HostStop("SOURCE_TARGET_INVALID", "target must stay inside selected module")
    target_path = module_root / Path(*candidate.parts)
    if _is_reparse(target_path) or not target_path.exists():
        raise HostStop("SOURCE_TARGET_INVALID", "target is missing or a reparse point")
    try:
        target_rel = target_path.resolve().relative_to(project.resolve()).as_posix()
        target_path.resolve().relative_to(module_root.resolve())
    except ValueError as error:
        raise HostStop("SOURCE_TARGET_INVALID", "target escapes selected module") from error
    prefix = target_rel + "/"
    selected = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and isinstance(item.get("opaque_id"), str)
        and isinstance(item.get("project_path"), str)
        and (item["project_path"] == target_rel or item["project_path"].startswith(prefix))
    ]
    if not selected:
        raise HostStop("SOURCE_TARGET_INVALID", "target contains no eligible inventory file")
    return selected


def _scan_context_budget(inventory: Mapping[str, Any], skillsrc_document: Mapping[str, Any] | None = None) -> int:
    """Per-batch context budget: ``limits.context_batch_bytes`` in .skillsrc, else the default."""
    del inventory
    declared = skillsrc_document.get("limits") if isinstance(skillsrc_document, Mapping) else None
    value = declared.get("context_batch_bytes") if isinstance(declared, Mapping) else None
    return value if type(value) is int and value > 0 else _SCAN_CONTEXT_BYTE_BUDGET


def _scan_execution_baseline(
    project: Path,
    module_root: Path,
    module: Mapping[str, Any],
    inventory: Mapping[str, Any],
    args: Any,
) -> Mapping[str, Any]:
    from tools.project_inventory import (
        InventoryError,
        build_execution_baseline,
        build_skillsrc_authority_receipt,
        runtime_identity,
        system_maven_path,
    )

    files = inventory.get("files")
    if not isinstance(files, list):
        raise InventoryError("inventory files are invalid")
    skillsrc = next((item for item in files if item.get("project_path") == ".skillsrc"), None)
    if not isinstance(skillsrc, Mapping):
        raise InventoryError("execution baseline lacks .skillsrc")
    module_path = str(inventory.get("module"))
    prefix = "" if module_path == "." else module_path.rstrip("/") + "/"
    parent_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and prefix
        and not str(item.get("project_path", "")).startswith(prefix)
    ]
    execution_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and item.get("opaque_id") not in parent_ids
    ]
    policy_profile = getattr(args, "profile", "local-pilot-v1")
    runtime_facts: dict[str, Any] = {}
    if policy_profile == "local-pilot-v1":
        test = module.get("test")
        if not isinstance(test, Mapping):
            raise InventoryError("selected module test configuration is invalid")
        adapter_id = str(test.get("adapter_id"))
        runtime_facts.update(adapter_id=adapter_id, build_profile=str(test.get("build_profile")), adapter_parameters=dict(test.get("adapter_parameters") or {}))
        if adapter_id == "maven:selected-symbols-v1":
            executable = str(system_maven_path(test.get("executable")))
            runtime_facts.update(executable_path=executable, executable_identity=runtime_identity(module_root, executable, adapter_id=adapter_id))
        else:
            # .skillsrc holds a logical runtime name (M33); the baseline binds the concrete
            # host path, relative to the module or, for a wrapper, to its build root (B5).
            from tools.execution_adapters import AdapterRequestError, resolve_module_runtime

            try:
                runtime_base, runtime_relative = resolve_module_runtime({**module, "module_root": str(module_root)})
            except AdapterRequestError as error:
                raise InventoryError("selected module runtime is unavailable") from error
            interpreter = runtime_relative if isinstance(test.get("interpreter"), str) else None
            wrapper = runtime_relative if isinstance(test.get("wrapper"), str) else None
            runtime_facts.update(interpreter_path=interpreter, wrapper_path=wrapper,
                interpreter_identity=runtime_identity(runtime_base, interpreter) if interpreter is not None else None,
                wrapper_identity=runtime_identity(runtime_base, wrapper) if wrapper is not None else None)
    requirement = {
        "module_id": module.get("id"),
        "selected_target": getattr(args, "target", None),
        "docs": _docs_projection(_docs_entries(project, getattr(args, "docs", None))),
    }
    requirement_digest = _digest_bytes(
        json.dumps(requirement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return build_execution_baseline(
        inventory,
        project_root=project,
        requirements={"requirement_id": f"scan-{_slug(getattr(args, 'target', None), getattr(args, 'docs', None))[:48]}", "digest": requirement_digest},
        skillsrc_file_id=str(skillsrc["opaque_id"]),
        skillsrc_authority=build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes()),
        execution_file_ids=execution_ids,
        parent_build_file_ids=parent_ids,
        policy_profile=policy_profile,
        **runtime_facts,
    )


def cmd_scan(args: Any) -> int:
    project = Path(args.project).resolve()
    run, receipt = _init_skillsrc_run(project, args)
    run_root = Path(str(run["run_root"]))
    stopped = _skillsrc_receipt_exit(project, run_root, receipt)
    if stopped is not None:
        return stopped
    return _scan_run(project, run_root, run, args)


def resume_scan(args: Any, run_root: Path) -> int:
    """Finish ``scan`` for a run that stopped on ``.skillsrc``, once the manifest exists.

    The run keeps its ID: module selection, inventory, baseline, attempt and context
    receipts are written into the run that asked the question.
    """
    from tools.pilot_state import read_run

    project = Path(args.project).resolve()
    if not (project / ".skillsrc").is_file():
        raise HostStop("SKILLSRC_MISSING", ".skillsrc is still missing")
    _read_skillsrc_binding(project / ".skillsrc")
    return _scan_run(project, Path(run_root), read_run(run_root), args)


def _scan_run(project: Path, run_root: Path, run: Mapping[str, Any], args: Any) -> int:
    scope: dict[str, Any] = {"module": None, "parent_files": None, "dependency_files": []}
    try:
        from tools.pilot_state import (
            append_event,
            create_attempt,
            freeze_phase_two_inputs,
            module_selection_digest,
            publish_context_selection,
        )

        skillsrc_document, _skillsrc_digest = _read_skillsrc_binding(project / ".skillsrc")
        module_root, module = _resolve_scan_module(project, getattr(args, "module", None), skillsrc_document)
        module_path = module_root.resolve().relative_to(project).as_posix() or "."
        scope["module"] = module_path
        append_event(
            run_root,
            "MODULE_SELECTED",
            actor="controller",
            artifact_digest=module_selection_digest(project, module_path),
        )
        inventory = build_inventory(
            project,
            module_root,
            skill_pack_root=_scan_skill_pack_root(project),
            generated_roots=(project / ".pilot-runs",),
        )
        prefix = "" if module_path == "." else module_path + "/"
        scope["parent_files"] = [item["project_path"] for item in inventory["files"] if prefix and not item["project_path"].startswith(prefix)]
        baseline = _scan_execution_baseline(project, module_root, module, inventory, args)
        frozen = freeze_phase_two_inputs(run_root, project, module_root, baseline)
        frozen_inventory = frozen["inventory"]
        exclusion_receipt = frozen["exclusions"]
        attempt = create_attempt(
            run_root,
            {"project": str(project), "module": module_path, "policy_profile": run["manifest"]["policy_profile"]},
            frozen["baseline"],
        )
        selected_ids = _context_scope_ids(frozen_inventory, project, module_root, getattr(args, "target", None))
        batches = select_context_batches(
            frozen_inventory,
            project,
            selected_ids,
            byte_budget=_scan_context_budget(frozen_inventory, skillsrc_document),
        )
        for batch in batches:
            publish_context_selection(run_root, str(attempt["attempt_id"]), batch["receipt"])
    except ContextSelectionError as error:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason="CONTEXT_SELECTION_INVALID", detail=str(error), scope=scope))
        return 2
    except (InventoryError, ValueError, OSError) as error:
        detail = str(error) if isinstance(error, InventoryError) else "scan input is unreadable" if isinstance(error, OSError) else "scan input or receipt is invalid"
        _print(_durable_status(project, run_root, stage="scan", status="error", reason="INVENTORY_INVALID", detail=detail, scope=scope))
        return 2
    payload = _durable_status(
        project,
        run_root,
        stage="scan",
        status="ok",
        module_id=module.get("id"),
        scope=scope,
        selected_target=getattr(args, "target", None),
        inventory_digest=frozen_inventory["digest"],
        exclusion_digest=exclusion_receipt["digest"],
        baseline_digest=frozen["baseline"]["digest"],
        inventory_file_count=len(frozen_inventory["files"]),
        exclusion_count=len(exclusion_receipt["exclusions"]),
        context_batch_count=len(batches),
        # Files skipped because they exceed the context budget: explicit, never silent.
        context_gaps=[
            {"project_path": gap["project_path"], "reason_code": gap["reason_code"], "size": gap["size"]}
            for batch in batches for gap in batch["receipt"].get("gaps", [])
        ],
    )
    _print(payload)
    return 0






def _resolve_host_context(
    project: Path,
    module_id: str | None,
    language_override: str | None,
    skillsrc_document: Mapping[str, Any] | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    from tools.run_tests import resolve_execution_context, resolve_execution_context_document
    from tools.skillsrc_manifest import SkillsrcError

    if skillsrc_document is None:
        execution_root, language, module = resolve_execution_context(project, project / ".skillsrc", module_id, language_override)
    else:
        execution_root, language, module = resolve_execution_context_document(
            project, skillsrc_document, module_id, language_override
        )
    if language not in {"python", "java"}:
        raise SkillsrcError(
            "language_unsupported",
            "selected module language is unsupported by the V5 runner",
        )
    expected_framework = "pytest" if language == "python" else "junit5"
    if (module.get("test") or {}).get("framework") != expected_framework:
        raise SkillsrcError("test_framework_unsupported", f"selected {language} module requires {expected_framework}")
    return execution_root, language, module


def _read_run_coordinate(project: Path, run_id: str) -> tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
    from tools.pilot_state import derive_state, read_run

    project = Path(project).resolve()
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise HostStop("EXECUTION_COORDINATES", "run must be one exact durable run ID")
    expected_parent = (project / ".pilot-runs").resolve()
    root = expected_parent / run_id
    try:
        run = read_run(root)
        state = derive_state(root)
    except (OSError, ValueError, KeyError) as error:
        raise HostStop("EXECUTION_COORDINATES", "pilot run root is unreadable or outside this project") from error
    manifest = run.get("manifest")
    if (
        root.parent != expected_parent
        or not isinstance(manifest, Mapping)
        or manifest.get("project") != str(project)
        or run_id != manifest.get("run_id")
        or root.name != run_id
        or not isinstance(state, Mapping)
    ):
        raise HostStop("EXECUTION_COORDINATES", "run ID and project must agree")
    return root, run, state


def _validated_execution_coordinates(project: Path, run_id: str) -> dict[str, Any]:
    """Bind CLI execution to one durable local-pilot run and its latest attempt."""
    root, run, state = _read_run_coordinate(project, run_id)
    manifest = run["manifest"]
    authorization = run.get("authorization")
    if (
        not isinstance(authorization, Mapping)
        or authorization.get("execution_requested") is not True
        or authorization.get("policy_profile") != "local-pilot-v1"
    ):
        raise HostStop("EXECUTION_COORDINATES", "run lacks local execution authorization")
    attempts = state.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise HostStop("EXECUTION_COORDINATES", "run has no attempt to continue")
    attempt = attempts[-1]
    if (
        attempt.get("project") != str(Path(project).resolve())
        or attempt.get("run_id") != manifest["run_id"]
        or attempt.get("policy_profile") != "local-pilot-v1"
        or not isinstance(attempt.get("module"), str)
    ):
        raise HostStop("EXECUTION_COORDINATES", "attempt is not active local-pilot execution state")
    module_root = (project / Path(attempt["module"])).resolve()
    try:
        module_root.relative_to(project)
    except ValueError as error:
        raise HostStop("EXECUTION_COORDINATES", "attempt module escapes project") from error
    if not module_root.is_dir():
        raise HostStop("EXECUTION_COORDINATES", "attempt module root is unavailable")
    return {"run_root": root, "manifest": manifest, "authorization": authorization, "attempt": attempt, "module_root": module_root}


def _restrict_inventory_to_baseline_inputs(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Project a fresh inventory onto the frozen baseline inputs and reseal it.

    After a started execution only a changed or removed baseline input is drift.
    Files the build or the tests created (coverage reports, ``.gradle``, logs,
    IDE state) were never baseline inputs and are not compared.
    """
    frozen = {row.get("opaque_id") for row in baseline.get("inputs", []) if isinstance(row, Mapping)}
    restricted = dict(current)
    restricted["files"] = [
        row for row in current.get("files", [])
        if isinstance(row, Mapping) and row.get("opaque_id") in frozen
    ]
    body = {key: value for key, value in restricted.items() if key != "digest"}
    restricted["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return restricted


def _revalidate_execution_baseline(coordinates: Mapping[str, Any], request: Any, after_start: bool = False) -> None:
    """Reject baseline drift around the one execution of an attempt.

    Before the execution-start claim any difference outside the generated delta
    is drift.  With ``after_start`` only the frozen baseline inputs are compared:
    changed or removed is drift, a new file is not.
    """
    from tools.project_inventory import (
        InventoryError,
        baseline_external_input_paths,
        build_inventory,
        read_execution_baseline,
        runtime_identity,
        validate_execution_baseline,
    )
    from tools.pilot_state import read_attempt_receipt

    try:
        project = Path(str(coordinates["manifest"]["project"])).resolve()
        root, attempt, module_root = Path(coordinates["run_root"]), coordinates["attempt"], Path(coordinates["module_root"])
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        durable_delta = read_attempt_receipt(root, str(attempt["attempt_id"]), "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
        module_prefix = "" if attempt["module"] == "." else str(attempt["module"]).rstrip("/") + "/"
        generated_paths = {module_prefix + str(row["path"]) for row in durable_delta["files"] if isinstance(row, Mapping)}
        current = build_inventory(
            project, module_root, skill_pack_root=ROOT, generated_roots=(root.parent,),
            proved_dependency_files=baseline_external_input_paths(project, baseline),
        )
        current["files"] = [row for row in current["files"] if row.get("project_path") not in generated_paths]
        body = {key: value for key, value in current.items() if key != "digest"}
        current["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if after_start:
            current = _restrict_inventory_to_baseline_inputs(current, baseline)
        result = validate_execution_baseline(
            baseline,
            current,
            requirements=baseline["requirements"],
            adapter_id=request.adapter_id,
            build_profile=request.build_profile,
            adapter_parameters=dict(request.typed_parameters),
            runtime_identity_value=runtime_identity(
                Path(request.cwd), request.executable if request.adapter_id == "maven:selected-symbols-v1" else str(baseline.get("interpreter_path") or baseline.get("wrapper_path")),
                adapter_id=baseline.get("adapter_id"),
            ),
        )
    except (InventoryError, KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("BASELINE_INCOMPLETE", "execution baseline is unreadable or incomplete") from error
    if result.get("status") != "UNCHANGED":
        raise HostStop(str(result.get("reason_code") or "BASELINE_DRIFT"), "frozen execution baseline changed; use a child attempt")


def _reconstruct_started_request(
    coordinates: Mapping[str, Any],
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
) -> tuple[Any, str]:
    """Rebuild the immutable closed request without inspecting generated bytes."""
    from tools.automation_validation import automation_sha256, required_symbol_pairs
    from tools.execution_adapters import ExecutionRequest, PYTEST, SYSTEM_MAVEN, request_digest
    from tools.pilot_state import derive_state, read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline

    root = Path(coordinates["run_root"])
    attempt = coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    module_root = Path(coordinates["module_root"]).resolve()
    try:
        baseline = read_execution_baseline(
            root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
        )
        effective = read_effective_canonical(root, attempt_id)
        durable_delta = read_attempt_receipt(
            root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK",
        )["record"]["delta"]
        if dict(document) != effective["document"] or automation_sha256(automation) != durable_delta["automation_digest"]:
            raise ValueError("recovery carriers do not bind durable inputs")
        pairs = sorted(required_symbol_pairs(dict(automation), dict(effective["document"])))
        artifacts = automation["artifacts"]
        files = {row["file_id"]: row for row in artifacts["generated_files"]}
        symbols = {(row["file_id"], row["symbol_id"]): row for row in artifacts["generated_symbols"]}
        selectors: list[str] = []
        for pair in pairs:
            file_row, symbol = files[pair[0]], symbols[pair]
            locator = symbol["locator"]
            if baseline["adapter_id"] == PYTEST:
                if locator["kind"] == "python_module_function":
                    node = locator["function_name"]
                elif locator["kind"] == "python_class_method":
                    node = "::".join([*locator["qualified_class_name"].split("."), locator["method_name"]])
                else:
                    raise ValueError("python recovery locator is invalid")
                selectors.append(f"{file_row['path']}::{node}")
            else:
                if locator["kind"] != "java_class_method":
                    raise ValueError("java recovery locator is invalid")
                selectors.append(f"{locator['class_fqn']}#{locator['method_name']}")
        adapter_id = baseline["adapter_id"]
        runtime_path = Path(
            baseline["interpreter_path"] if adapter_id == PYTEST else baseline["executable_path"] if adapter_id == SYSTEM_MAVEN else baseline["wrapper_path"]
        )
        from tools.project_inventory import module_runtime_path, system_maven_path
        from tools.execution_adapters import DEFAULT_TIMEOUT_SECONDS, AdapterRequestError, LaunchLayout, command_for, launch_layout
        from tools.skillsrc_manifest import SkillsrcError, load_module_by_root
        # Build root and timeout come from the authoritative .skillsrc (B5, P07); the
        # start-event digest below proves the reconstruction is the request that ran.
        try:
            configured = load_module_by_root(Path(str(attempt["project"])), str(attempt["module"]))
            layout = launch_layout({**configured, "module_root": str(module_root)})
        except (SkillsrcError, AdapterRequestError):
            layout = LaunchLayout(module_root, "", DEFAULT_TIMEOUT_SECONDS)
        executable = str(system_maven_path(str(runtime_path)) if adapter_id == SYSTEM_MAVEN else module_runtime_path(layout.build_root, runtime_path.as_posix()))
        profile = str(baseline["build_profile"])
        typed = tuple(sorted((str(key), str(value)) for key, value in baseline["adapter_parameters"].items()))
        reports, argv = command_for(adapter_id, executable, profile, selectors, module_path=layout.module_path)
        language = "python" if adapter_id == PYTEST else "java"
        request = ExecutionRequest(
            adapter_id, executable, tuple(argv), str(layout.build_root), tuple(selectors), layout.timeout_seconds,
            reports, ("PROJECT_NATIVE_ENV",), profile, typed,
        )
        starts = [
            event for event in derive_state(root)["events"]
            if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_STARTED"
        ]
        if len(starts) != 1 or starts[0].get("artifact_digest") != request_digest(request):
            raise ValueError("reconstructed request does not bind execution start")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_RECOVERY", f"started execution request cannot be reconstructed: {error}") from error
    return request, language


def _lost_execution_report(
    coordinates: Mapping[str, Any],
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Project a lost post-start controller result as sparse authoritative UNKNOWN."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.run_tests import _report

    request, language = _reconstruct_started_request(coordinates, document, automation)
    attempt_id = str(coordinates["attempt"]["attempt_id"])
    source = automation["artifacts"]["source"]
    return _report(
        "UNKNOWN", Path(coordinates["module_root"]), language, source,
        automation_sha256(automation), autotest_review_sha256(review),
        diagnostics=[{
            "path": "/execution", "code": "EXECUTION_RESULT_LOST",
            "message": "Execution started, but no authoritative framework or process result was durably recovered.",
        }],
        run_id=f"RUN-interrupted-{attempt_id}", authoritative=False, exit_code=2,
        duration_sec=0.0, request=request, run_root=Path(coordinates["run_root"]),
        attempt_id=attempt_id,
    )


def _publish_execution_receipt(coordinates: Mapping[str, Any], report: Mapping[str, Any]) -> Mapping[str, Any]:
    """Make the V5 result attempt-owned and prove its durable read-back."""
    from tools.pilot_state import (
        publish_attempt_receipt,
        recover_execution_receipt_events,
        record_execution_unknown,
        record_process_stopped,
    )

    root = Path(coordinates["run_root"])
    attempt_id = str(coordinates["attempt"]["attempt_id"])
    try:
        publish_attempt_receipt(root, attempt_id, "execution-receipt", {"payload": dict(report)})
        readback = recover_execution_receipt_events(root, attempt_id)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_RECEIPT", "execution completed but its durable receipt was not published/read back") from error
    if readback.get("record", {}).get("payload") != dict(report):
        raise HostStop("EXECUTION_RECEIPT", "execution receipt read-back does not equal the V5 report")
    if report.get("verdict") == "UNKNOWN":
        try:
            record_execution_unknown(root, attempt_id, str(readback["digest"]))
            if any(
                isinstance(row, Mapping)
                and row.get("process_scope_stopped") is True
                for row in report.get("process_evidence", [])
            ):
                record_process_stopped(root, attempt_id, str(readback["digest"]))
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise HostStop("EXECUTION_RECEIPT", "UNKNOWN execution receipt could not bind its durable event") from error
    return readback


def _reject_prestart_execution(
    coordinates: Mapping[str, Any],
    reason: str,
    message: str,
    *,
    request: Any = None,
    automation_artifact: Mapping[str, Any] | None = None,
    autotest_review: Mapping[str, Any] | None = None,
) -> int:
    """Persist an attempt-bound pre-process rejection from durable facts only."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline
    from tools.run_tests import _report, _request_is_attempt_local, _request_report_path

    try:
        root, attempt = Path(coordinates["run_root"]), coordinates["attempt"]
        attempt_id = str(attempt["attempt_id"])
        project = Path(str(attempt["project"])).resolve(strict=True)
        module_name = str(attempt["module"])
        module_root = (project / module_name).resolve(strict=True)
        module_root.relative_to(project)
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        delta = read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
        effective = read_effective_canonical(root, attempt_id)
        document = effective["document"]
        adapter_id = baseline["adapter_id"]
        language = "python" if adapter_id == PYTEST else "java" if adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} else None
        if language is None or delta.get("baseline_digest") != attempt["baseline_digest"]:
            raise ValueError("durable execution facts are invalid")
        source = {
            "document_id": document["document_id"],
            "revision": document["revision"],
            "source_digest": effective["document_digest"],
            "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
        }
        exact_request = request if (
            _request_is_attempt_local(request, project, module_root)
            and _request_report_path(request) is not None
        ) else None
        report = _report(
            "NOT_RUNNABLE", module_root, language, source,
            str(delta["automation_digest"]), str(delta["review_digest"]),
            diagnostics=[{"path": "/execution", "code": reason, "message": message}],
            request=exact_request, run_root=root, attempt_id=attempt_id,
        )
        _publish_execution_receipt(coordinates, report)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        _print({
            "status": "error", "reason": "EXECUTION_RECEIPT",
            "message": "pre-start rejection could not be persisted as an execution receipt",
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    if not isinstance(automation_artifact, Mapping) or not isinstance(autotest_review, Mapping):
        _print({
            "status": "error", "reason": "RUNNER_INPUT", "message": "execution carriers are unavailable",
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    try:
        from tools.finalize_attempt import finalize_durable_execution_attempt

        closed = finalize_durable_execution_attempt(
            Path(coordinates["run_root"]), attempt_id,
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        _print({
            "status": "error", "reason": "FINALIZATION", "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    _print({
        "status": "error", "reason": reason, "message": message,
        "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": "NOT_RUNNABLE",
        "accepted": closed["result"]["accepted"], "finalization": "complete",
    })
    return int(closed["exit_code"])


def _execute(
    project: Path,
    coordinates: Mapping[str, Any],
    execution_root: Path,
    language: str,
    executor: str,
    document: Mapping[str, Any],
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
    authorization_receipt: Mapping[str, Any],
    host_isolation_receipt: Mapping[str, Any],
    generated_delta_receipt: Mapping[str, Any],
) -> int:
    from tools.run_tests import (
        build_closed_execution_request,
        build_closed_gate_request,
        run_tests_v5,
        validate_execution_eligibility,
    )

    if executor != "local":
        return _reject_prestart_execution(
            coordinates, "EXECUTOR_UNSUPPORTED", "Pilot execution is project-native local only.",
            automation_artifact=automation, autotest_review=review,
        )

    try:
        if execution_root.resolve() != Path(coordinates["module_root"]).resolve():
            raise HostStop("EXECUTION_COORDINATES", "selected module differs from the attempt module")
        module = coordinates["module"]
        request, _compatibility = build_closed_execution_request(execution_root, language, module, document, automation)
        # Compile/collect gate for the same module and selectors; it runs after the
        # execution-start claim and before the reviewed tests.
        gate_request = build_closed_gate_request(request)
    except HostStop as error:
        return _reject_prestart_execution(
            coordinates, error.code, str(error), automation_artifact=automation, autotest_review=review,
        )
    except (ValueError, TypeError) as error:
        return _reject_prestart_execution(
            coordinates, getattr(error, "code", "RUNNER_INPUT"), str(error),
            automation_artifact=automation, autotest_review=review,
        )
    try:
        _revalidate_execution_baseline(coordinates, request)
        eligibility = validate_execution_eligibility(
            request, authorization_receipt, automation, review,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]), attempt_id=str(coordinates["attempt"]["attempt_id"]),
        )
    except HostStop as error:
        return _reject_prestart_execution(
            coordinates, error.code, str(error), request=request,
            automation_artifact=automation, autotest_review=review,
        )
    except (ValueError, TypeError, OSError) as error:
        return _reject_prestart_execution(
            coordinates, "BASELINE_INCOMPLETE", str(error), request=request,
            automation_artifact=automation, autotest_review=review,
        )
    if eligibility.get("ready") is not True:
        return _reject_prestart_execution(
            coordinates, str(eligibility.get("reason_code", "RUNNER_INPUT")), "execution admission is not ready",
            request=request, automation_artifact=automation, autotest_review=review,
        )

    execution_claimed = False

    def on_execution_start(request_value: Any) -> None:
        nonlocal execution_claimed
        from tools.execution_adapters import request_digest
        from tools.pilot_state import claim_execution_start

        # This callback is the last controller hook before subprocess invocation:
        # re-check both the frozen baseline and the durable generated delta before
        # claiming the one execution start.  Baseline inventory intentionally
        # excludes the pipeline-owned delta, so it cannot prove those bytes.
        _revalidate_execution_baseline(coordinates, request_value)
        final_eligibility = validate_execution_eligibility(
            request_value, authorization_receipt, automation, review,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]), attempt_id=str(coordinates["attempt"]["attempt_id"]),
        )
        if final_eligibility.get("ready") is not True:
            raise HostStop(
                str(final_eligibility.get("reason_code", "RUNNER_INPUT")),
                "durable execution admission changed before the execution-start gate",
            )
        claim_execution_start(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]), request_digest(request_value))
        execution_claimed = True

    def post_execution_check() -> str | None:
        try:
            _revalidate_execution_baseline(coordinates, request, True)
        except HostStop as error:
            return error.code
        return None

    try:
        report = run_tests_v5(
            request, authorization_receipt, document, automation, review,
            host_isolation_receipt=host_isolation_receipt,
            generated_delta_receipt=generated_delta_receipt,
            run_root=Path(coordinates["run_root"]),
            attempt_id=str(coordinates["attempt"]["attempt_id"]),
            on_execution_start=on_execution_start,
            post_execution_check=post_execution_check,
            gate_request=gate_request,
        )
    except HostStop as error:
        if not execution_claimed:
            return _reject_prestart_execution(
                coordinates, error.code, str(error), request=request,
                automation_artifact=automation, autotest_review=review,
            )
        raise
    try:
        _publish_execution_receipt(coordinates, report)
    except HostStop as error:
        _print({"status": "error", "reason": error.code, "message": str(error), "attempt_id": coordinates["attempt"]["attempt_id"]})
        return 2
    try:
        from tools.finalize_attempt import finalize_durable_execution_attempt

        closed = finalize_durable_execution_attempt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        _print({
            "status": "error", "reason": "FINALIZATION", "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    result = closed["result"]
    _print({
        "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
        "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": result.get("verification"),
        "accepted": result.get("accepted"), "executor": "local", "finalization": "complete",
        **_not_runnable_guidance(result, automation, coordinates.get("attempt")),
    })
    return int(closed["exit_code"])


_AUTOMATION_REVISION_BUDGET = 2


def _not_runnable_guidance(result: Mapping[str, Any], automation: Mapping[str, Any], attempt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Tell the caller why a started run was NOT_RUNNABLE and whether r2 is still allowed.

    A compile/collect failure of generated tests may be corrected by automation
    revision r2 while the r1+r2 budget is not exhausted; a failed product check
    (FAIL) is never regenerated.
    """
    reason = result.get("reason_code")
    if result.get("verification") != "NOT_RUNNABLE" or reason not in {"GENERATED_TEST_INVALID", "LAUNCH_FAILED", "TESTS_DESELECTED"}:
        return {}
    guidance: dict[str, Any] = {"reason": reason}
    if reason == "GENERATED_TEST_INVALID":
        artifacts = automation.get("artifacts") if isinstance(automation, Mapping) else None
        revision = artifacts.get("automation_revision") if isinstance(artifacts, Mapping) else None
        allowed = type(revision) is int and 1 <= revision < _AUTOMATION_REVISION_BUDGET and (attempt or {}).get("retry_reason") != "GENERATED_TEST_INVALID"
        guidance["automation_revision"] = revision
        guidance["automation_revision_allowed"] = allowed
        # The corrected revision is generated in a child attempt that declares this reason;
        # pilot_state.create_attempt enforces the same r1+r2 budget.
        guidance["regeneration"] = {"allowed": allowed, "retry_reason": "GENERATED_TEST_INVALID", "automation_revision": revision}
    return guidance


def cmd_exec(args: Any) -> int:
    project = Path(args.project).resolve()
    try:
        coordinates = _validated_execution_coordinates(project, args.run)
    except HostStop as error:
        _print({"status": "error", "reason": error.code, "message": str(error)})
        return 2
    try:
        from tools.pilot_state import exit_code, read_terminal_result

        terminal = read_terminal_result(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]))
    except KeyError:
        terminal = None
    if terminal is not None:
        _print({
            "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
            "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": terminal.get("verification"),
            "accepted": terminal.get("accepted"), "executor": "local", "finalization": "complete",
        })
        return exit_code(terminal)
    execution_receipt_path = (
        Path(coordinates["run_root"]) / "execution-receipts" / f"{coordinates['attempt']['attempt_id']}.json"
    )
    from tools.pilot_state import derive_state, read_attempt_receipt, read_effective_canonical, read_execution_inputs

    started = [
        event for event in derive_state(Path(coordinates["run_root"]))["events"]
        if event.get("attempt_id") == coordinates["attempt"]["attempt_id"]
        and event.get("event_type") == "EXECUTION_STARTED"
    ]
    if execution_receipt_path.exists() or started:
        try:
            from tools.finalize_attempt import finalize_durable_execution_attempt
            from tools.pilot_state import (
                publish_resume_validation,
                read_closure_artifact_if_present,
                read_resume_validation_if_present,
                recover_execution_receipt_events,
            )

            effective = read_effective_canonical(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
            execution_inputs = read_execution_inputs(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
            document = effective["document"]
            automation = execution_inputs["automation_artifact"]
            review = execution_inputs["autotest_review"]
            if execution_receipt_path.exists():
                recovered_execution = recover_execution_receipt_events(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
            else:
                _publish_execution_receipt(
                    coordinates, _lost_execution_report(coordinates, document, automation, review),
                )
                recovered_execution = recover_execution_receipt_events(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
            if recovered_execution["record"]["payload"]["verdict"] != "NOT_RUNNABLE":
                # Atomic installation freezes the verifier inputs.  Recovery may
                # finish its missing read-back binding, but terminal publication
                # still remains gated on that completed binding.
                finalization_receipt = read_closure_artifact_if_present(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                    "finalization_receipt",
                )
                resume_validation = read_resume_validation_if_present(
                    Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                )
                if finalization_receipt is None and resume_validation is None:
                    request, _language = _reconstruct_started_request(coordinates, document, automation)
                    try:
                        _revalidate_execution_baseline(coordinates, request, True)
                    except HostStop as drift:
                        publish_resume_validation(
                            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
                            drift.code,
                        )
            closed = finalize_durable_execution_attempt(
                Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            )
        except (HostStop, KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            _print({
                "status": "error",
                "reason": error.code if isinstance(error, HostStop) else "FINALIZATION",
                "message": str(error),
                "attempt_id": coordinates["attempt"]["attempt_id"],
            })
            return 2
        result = closed["result"]
        _print({
            "status": "ok", "stage": "exec", "run_id": coordinates["manifest"]["run_id"],
            "attempt_id": coordinates["attempt"]["attempt_id"], "verdict": result.get("verification"),
            "accepted": result.get("accepted"), "executor": "local", "finalization": "complete",
        })
        return int(closed["exit_code"])
    try:
        from tools.canonical_document import load_canonical_document
        from tools.run_tests import load_automation_artifact, load_autotest_review_artifact
        from tools.schema_validation import load_json_strict

        missing = [
            "--" + name.replace("_", "-")
            for name in (
                "canonical_document", "automation_artifact", "autotest_review",
                "authorization_receipt", "host_isolation_receipt", "generated_delta_receipt",
            )
            if not getattr(args, name, None)
        ]
        if missing:
            raise ValueError("initial execution requires: " + ", ".join(missing))
        supplied_document = load_canonical_document(Path(args.canonical_document))
        supplied_automation = load_automation_artifact(Path(args.automation_artifact))
        supplied_review = load_autotest_review_artifact(Path(args.autotest_review))
        supplied_authorization = load_json_strict(Path(args.authorization_receipt))
        supplied_isolation = load_json_strict(Path(args.host_isolation_receipt))
        supplied_delta = load_json_strict(Path(args.generated_delta_receipt))
        effective = read_effective_canonical(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
        execution_inputs = read_execution_inputs(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
        )
        durable_delta = read_attempt_receipt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            "generated-delta", "ARTIFACT_READ_BACK",
        )["record"]
        review_revision = execution_inputs["autotest_review"]["artifacts"]["autotest_review"]["automation_revision"]
        durable_isolation = read_attempt_receipt(
            Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"]),
            f"automation-review-boundary-r{review_revision}", "ARTIFACT_READ_BACK",
        )["record"]
        if (
            supplied_document != effective["document"]
            or supplied_automation != execution_inputs["automation_artifact"]
            or supplied_review != execution_inputs["autotest_review"]
            or supplied_authorization != coordinates["authorization"]
            or supplied_isolation != durable_isolation
            or supplied_delta != durable_delta
        ):
            raise ValueError("execution carrier differs from durable attempt evidence")
        document = effective["document"]
        automation = execution_inputs["automation_artifact"]
        review = execution_inputs["autotest_review"]
        authorization = coordinates["authorization"]
        isolation = durable_isolation
        delta = durable_delta
    except (HostStop, KeyError, ValueError, TypeError, OSError) as error:
        code = error.code if isinstance(error, HostStop) else getattr(error, "code", "RUNNER_INPUT")
        _print({
            "status": "error", "reason": code, "message": str(error),
            "attempt_id": coordinates["attempt"]["attempt_id"],
        })
        return 2
    try:
        execution_root, language, module = _resolve_host_context(
            project, getattr(args, "module", None), getattr(args, "language", None)
        )
        if execution_root.resolve() != Path(coordinates["module_root"]).resolve():
            raise HostStop("EXECUTION_COORDINATES", "selected module differs from the attempt module")
        coordinates = {**coordinates, "module": module}
    except (HostStop, ValueError, TypeError, OSError) as error:
        code = error.code if isinstance(error, HostStop) else getattr(error, "code", "RUNNER_INPUT")
        return _reject_prestart_execution(
            coordinates, code, str(error), automation_artifact=automation, autotest_review=review,
        )
    return _execute(
        project, coordinates, execution_root, language, getattr(args, "executor", "local"), document, automation, review,
        authorization, isolation, delta,
    )

def rerun_retained_tests(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Rerun one accepted attempt's retained tests through its recorded native request."""
    from tools.execution_adapters import invoke_request, request_digest
    from tools.pilot_state import (
        _append_event,
        _publish,
        _read_artifact,
        _retained_native_rerun_target,
        _retained_rerun_context,
        _scenario_observation_target,
        append_event,
        derive_state,
        publish_retained_rerun_observation,
        publish_run_artifact_bytes,
        read_effective_canonical,
        read_execution_inputs,
        read_retained_native_rerun,
        read_scenario_observation,
    )
    from tools.run_tests import (
        DURABLE_NATIVE_REPORT_NORMALIZATION,
        _invoke_closed_request,
        _normalize_durable_junit_report,
        _outcome,
        _report_inventory,
        _report_snapshots,
        _records_from_snapshots,
        _request_report_path,
        validate_artifact_runner_compatibility,
    )

    context = _retained_rerun_context(Path(run_root), attempt_id)
    project, root = context["project"], context["root"]
    state = derive_state(root)
    rerun_events = [
        row for row in state["events"]
        if row.get("event_type") == "RETAINED_NATIVE_RERUN"
        and row.get("attempt_id") == attempt_id
    ]
    if len(rerun_events) > 1:
        raise ValueError("retained native rerun lifecycle is ambiguous")
    if rerun_events:
        receipt = read_retained_native_rerun(
            root, attempt_id, str(rerun_events[0].get("artifact_digest", "")),
        )
        observation_target = _scenario_observation_target(
            root, attempt_id, "retained-native-rerun",
        )
        if observation_target.exists():
            observation_record = _read_artifact(
                project, root, observation_target, "scenario observation",
            )
            observation = read_scenario_observation(
                root, attempt_id, "retained-native-rerun",
                str(observation_record["digest"]),
            )
        else:
            observation = publish_retained_rerun_observation(root, attempt_id)
        return {
            "rerun_receipt": receipt,
            "scenario_observation_receipts": {"retained-native-rerun": observation},
            "idempotent": True,
        }
    if _retained_native_rerun_target(root, attempt_id).exists():
        raise ValueError("unbound retained native rerun receipt exists")
    observation_target = _scenario_observation_target(
        root, attempt_id, "retained-native-rerun",
    )
    if observation_target.exists():
        raise ValueError("unbound retained native rerun observation exists")

    request = context["request"]
    if request_digest(request) != context["execution_receipt"]["execution_request_digest"]:
        raise ValueError("retained native rerun request binding is invalid")
    executable = Path(request.executable)
    if not executable.is_file() or (os.name != "nt" and not os.access(executable, os.X_OK)):
        raise ValueError("retained native rerun executable is unavailable")
    if os.name == "nt" and executable.suffix.casefold() == ".exe":
        try:
            with executable.open("rb") as stream:
                executable_header = stream.read(2)
        except OSError as error:
            raise ValueError("retained native rerun executable is unavailable") from error
        if executable_header != b"MZ":
            raise ValueError("retained native rerun executable is invalid")
    report_path = _request_report_path(request)
    if report_path is None:
        raise ValueError("retained native rerun report path is invalid")
    execution_payload = context["execution_receipt"]["payload"]
    language = execution_payload["target"]["language"]
    effective = read_effective_canonical(root, attempt_id)
    execution_inputs = read_execution_inputs(root, attempt_id)
    from tools.execution_adapters import request_module_root

    # Reviewed files and reports are module-relative even when the build starts above it.
    compatibility = validate_artifact_runner_compatibility(
        request_module_root(request).resolve(), language,
        effective["document"], execution_inputs["automation_artifact"],
    )
    if compatibility.status != "READY":
        raise ValueError("retained native rerun reviewed targets are invalid")
    before_inventory = _report_inventory(report_path)
    outcome = _outcome(invoke_request(request, _invoke_closed_request))
    after_inventory = _report_inventory(report_path)
    fresh_paths = sorted(
        (path for path, value in after_inventory.items() if before_inventory.get(path) != value),
        key=str,
    )
    snapshots = _report_snapshots(fresh_paths)
    after_context = _retained_rerun_context(root, attempt_id)
    if after_context["snapshot"] != context["snapshot"]:
        raise ValueError("retained native rerun changed retained test bytes")
    if outcome.kind != "EXIT" or outcome.exit_code != 0 or not fresh_paths:
        raise ValueError("retained native rerun did not produce a passing fresh report")
    if set(snapshots) != set(fresh_paths):
        raise ValueError("retained native rerun report read-back is incomplete")
    rerun_evidence, match_errors = _records_from_snapshots(
        snapshots, compatibility, str(execution_payload["run_id"]),
        execution_payload["source"], java=language == "java",
    )
    expected_pairs = sorted(
        (row["file_id"], row["symbol_id"])
        for row in execution_payload["execution_evidence"]
    )
    observed_pairs = sorted(
        (row["file_id"], row["symbol_id"])
        for row in rerun_evidence
    )
    if (
        match_errors or observed_pairs != expected_pairs
        or any(row["status"] != "PASSED" for row in rerun_evidence)
    ):
        raise ValueError("retained native rerun report does not match reviewed targets")

    def fingerprint(value: tuple[int, int, str] | None) -> Mapping[str, Any] | None:
        if value is None:
            return None
        return {"mtime_ns": value[0], "size": value[1], "digest": "sha256:" + value[2]}

    reports: list[dict[str, Any]] = []
    used_names: dict[str, int] = {}
    cwd = request_module_root(request).resolve()
    for path in fresh_paths:
        raw = snapshots[path]
        after_fingerprint = fingerprint(after_inventory[path])
        source_digest = _digest_bytes(raw)
        if (
            after_fingerprint is None
            or after_fingerprint["size"] != len(raw)
            or after_fingerprint["digest"] != source_digest
        ):
            raise ValueError("retained native rerun report is not a passing fresh report")
        try:
            source_path = path.relative_to(cwd).as_posix()
        except ValueError as error:
            raise ValueError("retained native rerun report escaped its module") from error
        name = path.name or "report.xml"
        count = used_names.get(name, 0)
        used_names[name] = count + 1
        if count:
            name = f"{Path(name).stem}-{count}{Path(name).suffix}"
        durable = _normalize_durable_junit_report(raw)
        published = publish_run_artifact_bytes(
            root, attempt_id, f"retained-rerun/reports/{name}", durable,
        )
        reports.append({
            "source_path": source_path,
            "source_digest": source_digest,
            "before_fingerprint": fingerprint(before_inventory.get(path)),
            "after_fingerprint": after_fingerprint,
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": published["path"],
            "digest": published["digest"],
        })
    value = {
        "schema_version": "1.0.0", "kind": "retained-native-rerun",
        "run_id": context["attempt"]["run_id"], "attempt_id": attempt_id,
        "policy_profile": context["attempt"]["policy_profile"],
        "terminal_result_digest": context["terminal"]["digest"],
        "execution_receipt_digest": context["execution_receipt"]["digest"],
        "execution_request_digest": context["execution_receipt"]["execution_request_digest"],
        "disposition_receipt_digest": context["disposition_receipt"]["digest"],
        "before": context["snapshot"], "after": after_context["snapshot"],
        "outcome": {"kind": outcome.kind, "exit_code": outcome.exit_code},
        "reports": reports,
    }
    target = _retained_native_rerun_target(root, attempt_id)
    record, created, identity = _publish(
        project, root, target, value, "retained native rerun", return_created=True,
    )
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=record["digest"])
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", artifact_digest=record["digest"])
    _append_event(
        project, root, "RETAINED_NATIVE_RERUN", actor="controller",
        attempt_id=attempt_id, batch_id=None, artifact_digest=record["digest"],
    )
    receipt = dict(read_retained_native_rerun(root, attempt_id, str(record["digest"])))
    receipt.update({"created": created, "installed_identity": identity})
    observation = publish_retained_rerun_observation(root, attempt_id)
    return {
        "rerun_receipt": receipt,
        "scenario_observation_receipts": {"retained-native-rerun": observation},
        "idempotent": False,
    }


def cmd_rerun_retained(args: Any) -> int:
    project = Path(args.project).resolve()
    root, run, state = _read_run_coordinate(project, args.run)
    from tools.pilot_state import read_terminal_result

    candidates = []
    for attempt in state["attempts"]:
        if attempt.get("state") != "TERMINAL":
            continue
        terminal = read_terminal_result(root, str(attempt["attempt_id"]))
        if terminal.get("accepted") is True and terminal.get("verification") == "PASS":
            candidates.append(attempt)
    if len(candidates) != 1:
        raise HostStop(
            "RERUN_COORDINATES",
            "run must contain exactly one accepted passing terminal attempt",
        )
    attempt_id = str(candidates[0]["attempt_id"])
    result = rerun_retained_tests(root, attempt_id)
    observation = result["scenario_observation_receipts"]["retained-native-rerun"]
    _print({
        "status": "ok", "stage": "rerun-retained",
        "run_id": run["manifest"]["run_id"], "attempt_id": attempt_id,
        "rerun_receipt_digest": result["rerun_receipt"]["digest"],
        "scenario_observation_digest": observation["digest"],
        "idempotent": result["idempotent"],
    })
    return 0


def cmd_status(args: Any) -> int:
    project = Path(args.project).resolve()
    root, _run, _state = _read_run_coordinate(project, args.run)
    _print(_durable_status(project, root, stage="status"))
    return 0


def run_phase_one_spine(project: Path, policy_profile: str, authorization: Mapping[str, Any], identity: Mapping[str, Any], baseline: Mapping[str, Any], facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist the fixed factual waiting spine without calling model, project, or executor code."""
    from tools.pilot_state import (
        append_event,
        create_attempt,
        create_run,
        derive_state,
        exit_code,
        freeze_phase_two_inputs,
        module_selection_digest,
        publish_attempt_receipt,
        remove_attempt_receipt_if_created,
    )
    from tools.pilot_state import _PublicationUnknown

    if facts:
        raise ValueError("terminal facts are not allowed in the F4 waiting spine")
    project_root = Path(project).resolve()
    module = identity.get("module")
    selection_digest = module_selection_digest(project_root, module)
    run = create_run(project_root, policy_profile, authorization)
    root = Path(run["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=selection_digest)
    module_root = project_root if module == "." else project_root.joinpath(*str(module).replace("\\", "/").split("/"))
    frozen = freeze_phase_two_inputs(root, project_root, module_root, baseline)
    attempt = create_attempt(root, identity, frozen["baseline"])
    artifact = publish_attempt_receipt(root, attempt["attempt_id"], "phase1-artifact", {
        "module_selection_digest": selection_digest,
        "baseline_digest": attempt["baseline_digest"],
    })
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=artifact["digest"])
    result = publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    try:
        append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=result["digest"])
    except _PublicationUnknown:
        raise
    except Exception:
        if not remove_attempt_receipt_if_created(root, result):
            return {
                "run": run,
                "state": derive_state(root),
                "artifact": artifact,
                "artifact_digest": artifact["digest"],
                "result": None,
                "controller_error": {
                    "code": "WAITING_RESULT_CLEANUP_CONFLICT",
                    "run_id": run["manifest"]["run_id"],
                    "attempt_id": attempt["attempt_id"],
                },
                "exit_code": 2,
            }
        raise
    state = derive_state(root)
    return {"run": run, "state": state, "artifact": artifact, "artifact_digest": artifact["digest"], "result": result, "exit_code": exit_code(result["record"])}


def resume_phase_one_spine(run_root: Path) -> Mapping[str, Any]:
    """Read-only fresh-context resume of the one durable F4 waiting spine."""
    from tools.pilot_state import derive_state, exit_code, read_attempt_receipt, read_run
    from tools.project_inventory import (
        InventoryError,
        baseline_external_input_paths,
        build_inventory,
        read_execution_baseline,
        read_inventory_receipt,
        runtime_identity,
        validate_execution_baseline,
    )

    run = read_run(run_root)
    root = Path(run["run_root"])
    state = derive_state(root)
    if len(state["attempts"]) != 1 or state["attempts"][0]["state"] != "WAITING_FOR_MODEL":
        raise ValueError("not a resumable F4 waiting spine")
    attempt = state["attempts"][0]
    if [event["event_type"] for event in state["events"]] != [
        "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND",
        "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED", "ARTIFACT_READ_BACK",
        "WAITING_FOR_MODEL",
    ]:
        raise ValueError("invalid F4 event order")
    try:
        baseline_name = str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"
        baseline = read_execution_baseline(root / "baselines" / baseline_name)
        inventory_name = str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"
        frozen_inventory = read_inventory_receipt(root / "inventories" / inventory_name)
        if frozen_inventory["digest"] != baseline["inventory_digest"]:
            raise InventoryError("baseline inventory binding is invalid")
        project = Path(attempt["project"]).resolve()
        module_root = project if attempt["module"] == "." else project.joinpath(*str(attempt["module"]).split("/"))
        current_inventory = build_inventory(
            project,
            module_root,
            skill_pack_root=ROOT,
            generated_roots=(root.parent,),
            proved_dependency_files=baseline_external_input_paths(project, baseline),
        )
        from tools.project_inventory import validate_execution_baseline_binding

        policy_profile = str(attempt["policy_profile"])
        validate_execution_baseline_binding(baseline, current_inventory, module_root, policy_profile=policy_profile)
        runtime_path = baseline.get("interpreter_path") or baseline.get("wrapper_path") or baseline.get("executable_path")
        current_runtime_identity = None
        if policy_profile == "local-pilot-v1":
            if not isinstance(runtime_path, str):
                raise InventoryError("baseline runtime path is invalid")
            current_runtime_identity = runtime_identity(module_root, runtime_path, adapter_id=baseline.get("adapter_id"))
        validation = validate_execution_baseline(
            baseline, current_inventory, policy_profile=policy_profile,
            requirements=baseline["requirements"], adapter_id=baseline.get("adapter_id"),
            build_profile=baseline.get("build_profile"), adapter_parameters=baseline.get("adapter_parameters"),
            runtime_identity_value=current_runtime_identity,
        )
    except (InventoryError, KeyError, TypeError) as error:
        raise ValueError("execution baseline revalidation failed: BASELINE_INCOMPLETE") from error
    if validation["status"] != "UNCHANGED":
        reason = validation.get("reason_code", validation["status"])
        raise ValueError(f"execution baseline revalidation failed: {reason}")
    artifact = read_attempt_receipt(root, attempt["attempt_id"], "phase1-artifact", "ARTIFACT_READ_BACK")
    result = read_attempt_receipt(root, attempt["attempt_id"], "structured-result", "WAITING_FOR_MODEL")
    record = result["record"]
    if record.get("attempt_state") != "WAITING_FOR_MODEL" or any(record.get(axis) is not None for axis in ("completion", "verification", "coverage")):
        raise ValueError("invalid F4 waiting result")
    return {"run": run, "state": state, "artifact": artifact, "artifact_digest": artifact["digest"], "result": result, "exit_code": exit_code(record)}


def finalize_phase_one_spine(
    run_root: Path, branch: Mapping[str, Any], delta: Mapping[str, Any] | None, *, verification: str, facts: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Durably close the selected attempt through the frozen Phase 7 order."""
    from tools.finalize_attempt import finalize_attempt
    from tools.pilot_state import derive_state, exit_code, read_terminal_result

    state = derive_state(run_root)
    matches = [item for item in state["attempts"] if item["attempt_id"] == branch.get("attempt_id")]
    if len(matches) != 1:
        raise ValueError("finalization attempt is not bound to this run")
    record = finalize_attempt(branch, delta, project=Path(matches[0]["project"]), verification=verification, facts=facts, run_root=run_root)
    if record["idempotent"]:
        return {"result": record["result"], "exit_code": exit_code(record["result"]), "idempotent": True}
    result = read_terminal_result(run_root, str(branch["attempt_id"]))
    return {**record, "result": result, "exit_code": exit_code(result), "idempotent": False}

def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="Deterministic helpers for the CLI-driven test-skills pack.")
    shared = argparse_parent()
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", parents=[shared])
    scan_parser.add_argument("--profile", choices=("local-pilot-v1", "cases-only-v1"), default="local-pilot-v1")
    status_parser = sub.add_parser("status", parents=[shared])
    status_parser.add_argument("--run", required=True, help="Exact durable run ID")
    exec_parser = sub.add_parser("exec", parents=[shared])
    exec_parser.add_argument("--run", required=True)
    exec_parser.add_argument("--language", choices=("python", "java"))
    exec_parser.add_argument("--executor", choices=("local",), default="local")
    exec_parser.add_argument("--canonical-document")
    exec_parser.add_argument("--automation-artifact")
    exec_parser.add_argument("--autotest-review")
    exec_parser.add_argument("--authorization-receipt")
    exec_parser.add_argument("--host-isolation-receipt")
    exec_parser.add_argument("--generated-delta-receipt")
    rerun_parser = sub.add_parser("rerun-retained", parents=[shared])
    rerun_parser.add_argument("--run", required=True, help="Exact durable run ID")
    return parser


def argparse_parent():
    from argparse import ArgumentParser

    shared = ArgumentParser(add_help=False)
    shared.add_argument("--project", required=True)
    shared.add_argument("--target")
    shared.add_argument("--module", help="Exact module ID from inventory/.skillsrc, not its filesystem path")
    shared.add_argument("--docs", action="append", default=None)
    return shared


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    commands = {
        "scan": cmd_scan,
        "exec": cmd_exec,
        "rerun-retained": cmd_rerun_retained,
        "status": cmd_status,
    }
    try:
        return commands[args.command](args)
    except Exception as error:
        from tools.skillsrc_manifest import SkillsrcError

        if isinstance(error, HostStop):
            _print({"status": "error", "reason": error.code, "message": str(error)})
            return 2
        if isinstance(error, SkillsrcError):
            _print({"status": "error", "reason": error.code, "message": str(error)})
            return 2
        if isinstance(error, RuntimeError):
            payload: dict[str, Any] = {"status": "error", "reason": str(error)}
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                payload["reason"] = code
                payload["message"] = str(error)
            _print(payload)
            return 2
        if isinstance(error, (FileNotFoundError, ValueError)):
            _print({"status": "error", "reason": str(error)})
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
