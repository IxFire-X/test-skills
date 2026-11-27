"""Deterministic scan/status/materialize/ingest helpers for the CLI-driven skill pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from tools import execution_gate
from tools.fixes import fix_for, human_for
from tools.json_cli import JsonArgumentParser, emit_json, one_closure
from tools.run_facts import (
    human_summary,
    quarantine_summary as _quarantine_summary,
    receipts_warning,
    report_mismatch as _report_mismatch,
    review_notes_summary as _review_notes_summary,
    run_human_summary as _human_summary,
    style_notes as _style_notes,
)
from tools.run_lock import JournalRefusal
from tools.project_inventory import (
    ContextSelectionError,
    ContextStop,
    InventoryError,
    build_inventory,
)
from tools.run_scan import (  # what scan computes (C2); the command scan below prints it
    baseline_check as _baseline_check,
    cases_scope_files as _cases_scope_files,
    check_cases,
    docs_entries as _docs_entries,
    durable_status as _durable_status,
    expand_docs,
    HostStop,
    init_skillsrc_run as _init_skillsrc_run,
    inventory_invalid_detail as _inventory_invalid_detail,
    module_warnings as _module_warnings,
    open_run_of as _open_run_of,
    outside_task_files as _outside_task_files,
    printed,
    project_not_found,
    publish_context as _publish_context,
    read_skillsrc_binding as _read_skillsrc_binding,
    reference_cases as _reference_cases,
    style_samples as _style_samples,
    resolve_scan_module as _resolve_scan_module,
    run_in_progress as _run_in_progress,
    scan_command as _scan_command,
    scan_context_plan as _scan_context_plan,
    scan_cut as _scan_cut,
    scan_execution_baseline as _scan_execution_baseline,
    scan_payload as _scan_payload,
    scan_profile as _scan_profile,
    scan_request as _scan_request,
    scan_skill_pack_root as _scan_skill_pack_root,
    skillsrc_preflight as _skillsrc_preflight,
    task_document_scope as _task_document_scope,
)


_PRINTED: list[dict[str, Any]] = []


_ARGS: list[Any] = []  # the arguments of the running command: the refusal journal needs its run


def _journal_refusal(payload: Mapping[str, Any]) -> None:
    """A refusal of a command that names its run goes to ``<run>/refusals.jsonl`` (code, command, stage — nothing else)."""
    from tools.json_cli import record_refusal
    from tools.output_guard import local_path_text

    args = _ARGS[0] if _ARGS else None
    run, project = getattr(args, "run", None), getattr(args, "project", None)
    run = run if isinstance(run, str) and run else payload.get("run_id")
    if isinstance(run, str) and re.fullmatch(r"[0-9a-f]{32}", run) and project and local_path_text(str(project)):  # a run id, never a path
        record_refusal(Path(str(project)).resolve() / ".pilot-runs" / run, args.command, payload.get("code") or payload.get("reason"), payload.get("stage"))


def _print(payload: dict[str, Any]) -> None:
    if payload.get("status") == "error":
        payload = _envelope(payload)
        _journal_refusal(payload)
    _PRINTED[:] = [payload]
    emit_json(payload, indent=2, sort_keys=False, separators=None)


def _envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The one refusal envelope {status, code, message, fix, next}; ``reason`` stays beside ``code``."""
    reason = payload.get("reason") or payload.get("code") or "ERROR"
    human = human_for(payload.get("code") or reason, str(payload.get("message") or payload.get("detail") or ""))
    return {**payload, "code": payload.get("code") or reason, "message": payload.get("message") or payload.get("detail") or reason,
            "fix": payload.get("fix"), "next": payload.get("next"), **({"human": human} if human else {})}


def _skillsrc_receipt_exit(project: Path, run_root: Path, receipt: Mapping[str, Any], module: str | None = None) -> int | None:
    status = receipt.get("status")
    if status in {"needs_input", "conflict"}:
        from tools.ready import module_choice

        choice = module_choice(project) if module is None else None  # several modules and none named: that is the answer asked for, not a question about one of them
        _print(_durable_status(project, run_root, stage="skillsrc", status=status, reason=status, **({"fix": choice} if choice else {})))
        return 3
    if status not in {"created", "updated", "unchanged"}:
        reason = "SOURCE_TARGET_INVALID" if receipt.get("errors") == ["source_target_invalid"] else status or "error"
        _print(_durable_status(project, run_root, stage="skillsrc", status="error", reason=reason))
        return 2
    return None


def _drift_report(project: Path, manifest: Mapping[str, Any], changed: Sequence[str]) -> dict[str, Any]:
    """The changed paths (never bytes) and the two real ways out."""
    request = manifest.get("scan_request")
    command = _scan_command(project, request, extra=("--new-run",)) if isinstance(request, Mapping) else None
    fix = ("верните эти файлы к состоянию на момент scan — тогда прогон продолжится; или начните новый прогон: "
           + (command or "scan с теми же --module, --docs, --profile и флагом --new-run"))
    return {"changed": list(changed), "fix": fix, **({"command": command} if command else {})}


_SECOND_PASS = {
    "REWORK": "ревью вернуло кейсы на доработку: следующий заход — новый scan того же запроса",
    "AUTOMATION_REJECTED": "автоматизация отклонена ревью: следующий заход — новый scan того же запроса",
    "AUTOMATION_BLOCKED": "автоматизация заблокирована: следующий заход — новый scan того же запроса",
    "AUTOMATION_NOT_MATERIALIZABLE": "принятую автоматизацию нельзя записать в проект: следующий заход — новый scan того же запроса",
    # finish --abandon
    "STAGE_RETRY_EXHAUSTED": "стадия автоматизации исчерпала единственный повтор: следующий заход — новый scan того же запроса",
    "ASSEMBLY_INVALID": "сборка или публикация ревизии автоматизации не прошла: следующий заход — новый scan того же запроса",
    "REVIEW_UNUSABLE": "ревью кейсов не может закончиться вердиктом: следующий заход — новый scan того же запроса",
}


def _automation_split(root: Path, attempt_id: str) -> dict[str, Any]:
    """«автоматизировано N из M, вручную K» of a partial automation, for the human — not the whole set."""
    from tools.finalize_attempt import automation_split

    try:
        split = automation_split(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        split = None
    return {"automation_split": split} if split else {}


def _next_run(project: Path, manifest: Mapping[str, Any], reason_code: Any) -> dict[str, Any]:
    """After a closed negative outcome, the scan command of the same request — printed, never run here."""
    request = manifest.get("scan_request")
    if reason_code not in _SECOND_PASS or not isinstance(request, Mapping):
        return {}
    return {"next_run": {"why": _SECOND_PASS[str(reason_code)], "command": _scan_command(project, request)}}


def _status_drift(project: Path, root: Path, manifest: Mapping[str, Any], attempt: Mapping[str, Any]) -> dict[str, Any]:
    """Status of an open attempt names the files changed since the scan — the same check as materialize/ingest."""
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    try:
        result = _baseline_check(project, root, attempt, module_root)
    except Exception as error:  # not a drift — but said, with its code and fix; the hint never fails status
        from tools.json_cli import error_fields

        fields = error_fields(error, table=False)  # the fix of this place below is more exact than the table's
        return {"drift_check_error": {"code": fields["code"], "message": fields["message"][:500], "fix": fields.get("fix") or (
            "сверка файлов с baseline не прошла — дрейф не проверен; повтори status; если ошибка остаётся, materialize и ingest остановятся "
            "на той же сверке: начни новый прогон тем же scan с --new-run (команда — drift.command в status)")}}
    if result.get("status") == "UNCHANGED":
        return {}
    return {"drift": _drift_report(project, manifest, result.get("changed") or [])}


def _scan_cases(project: Path, args: Any) -> int | None:
    """``--cases`` — the Zephyr CSV is checked before anything is written and becomes the document of the run."""
    from tools.cases_import import CasesImportError

    cases = getattr(args, "cases", None)
    if not cases:
        return None
    try:
        rel, args.cases_read = check_cases(project, cases)
    except HostStop as stop:
        _print({"status": "error", "stage": "scan", "reason": stop.code, "detail": str(stop), "fix": stop.fix})
        return 2
    except CasesImportError as error:
        _print({"status": "error", "stage": "scan", "reason": error.code, "detail": str(error), "fix": error.fix})
        return 2
    args.cases = rel
    return None


@one_closure
def cmd_scan(args: Any) -> int:
    """Scan, then ``run_id: <id>`` as the last line of the output."""
    _PRINTED.clear()
    try:
        _scan_profile(args)
        if missing := project_not_found(Path(args.project).resolve()):  # not «docs path does not exist» for a project that is not there
            raise missing
        args.docs = expand_docs(Path(args.project).resolve(), getattr(args, "docs", None))  # a folder is the files under it, before any reader sees the request
    except HostStop as stop:
        _print({"status": "error", "stage": "scan", "reason": stop.code, "detail": str(stop), "fix": stop.fix})
        return 2
    refused = _scan_cases(Path(args.project).resolve(), args)
    if refused is not None:
        return refused
    # --docs and --reference are read before the run is created, as --cases: a refusal leaves no orphan run
    from tools.skillsrc_manifest import SkillsrcError

    try:
        _skillsrc_preflight(Path(args.project).resolve())
        entries = _docs_entries(Path(args.project).resolve(), getattr(args, "docs", None))
        _cases_scope_files(entries, getattr(args, "cases", None))
        _reference_cases(Path(args.project).resolve(), getattr(args, "reference", None))
        _style_samples(Path(args.project).resolve())
    except HostStop as stop:
        _print({"status": "error", "stage": "scan", "reason": stop.code, "detail": str(stop), "fix": stop.fix})
        return 2
    except SkillsrcError as error:  # a foreign .skillsrc — its code, message and fix, and no run
        _print({"status": "error", "stage": "scan", "reason": error.code, "code": error.code, "message": str(error), "fix": error.fix})
        return 2
    code = _scan(args)
    run_id = _PRINTED[-1].get("run_id") if _PRINTED else None
    if run_id:
        print(f"run_id: {run_id}")
    return code


def _scan(args: Any) -> int:
    project = Path(args.project).resolve()
    request = _scan_request(args)
    _scan_command(project, request)  # the commands of this run print every value of the request: refused here, before the run is written
    abandoned: list[str] = []
    if not getattr(args, "new_run", False):
        from tools.project_inventory import inventory_cache

        # one inventory per module for every unfinished run the scan compares.  Only around the comparison: the
        # scan then writes project files (.skillsrc, the cases document) and a rewrite of the same size within one
        # timestamp tick (Windows) keeps the stamp, so the inventory of the new run is never taken from this cache.
        skipped_runs: list[Path] = []
        cut_runs: list[str] = []
        while True:
            with inventory_cache():
                open_root, abandoned = _open_run_of(project, request, skip=skipped_runs)
            if open_root is None:
                abandoned = [*abandoned, *cut_runs]
                break
            if _scan_cut(open_root, request) is None:
                _print(_run_in_progress(project, open_root, request))
                return 2
            # the scan of this run stopped before its last context batch — the same scan finishes it
            outcome = _resume_scan(project, args, open_root, [*abandoned, *cut_runs])
            if isinstance(outcome, int):
                return outcome
            skipped_runs.append(open_root)
            cut_runs.append(f"{open_root.name} ({outcome})")
    run, receipt = _init_skillsrc_run(project, args, request)
    run_root = Path(str(run["run_root"]))
    stopped = _skillsrc_receipt_exit(project, run_root, receipt, getattr(args, "module", None))
    if stopped is not None:
        return stopped
    scope: dict[str, Any] = {"module": None, "parent_files": None, "dependency_files": []}
    try:
        from tools.pilot_state import (
            append_event,
            create_attempt,
            freeze_phase_two_inputs,
            module_selection_digest,
        )

        skillsrc_document, _skillsrc_digest = _read_skillsrc_binding(project / ".skillsrc")
        module_root, module = _resolve_scan_module(project, getattr(args, "module", None), skillsrc_document)
        module_path = module_root.resolve().relative_to(project).as_posix() or "."
        scope["module"] = module_path
        task_files = _outside_task_files(project, module_root, getattr(args, "docs", None))
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
            proved_dependency_files=task_files,
        )
        prefix = "" if module_path == "." else module_path + "/"
        scope["parent_files"] = [item["project_path"] for item in inventory["files"] if prefix and item.get("origin") != "context" and not item["project_path"].startswith(prefix)]
        baseline = _scan_execution_baseline(project, module_root, module, inventory, args)
        frozen = freeze_phase_two_inputs(run_root, project, module_root, baseline)
        frozen_inventory = frozen["inventory"]
        exclusion_receipt = frozen["exclusions"]
        task_ids, other_task_ids, context_summary, context_warnings = _task_document_scope(
            frozen_inventory, project, getattr(args, "docs", None), module_root=module_root, cases=bool(getattr(args, "cases", None)))
        context_warnings = [*context_warnings, *_module_warnings(module, getattr(args, "module", None))]
        attempt = create_attempt(
            run_root,
            {"project": str(project), "module": module_path, "policy_profile": run["manifest"]["policy_profile"]},
            baseline,  # the fresh one: what lay in the run root was taken only with these very bytes
        )
        plan = _scan_context_plan(project, args, run_root, frozen_inventory, module_root, (task_ids, other_task_ids, context_summary))
        _publish_context(run_root, str(attempt["attempt_id"]), plan)
        batches, context_skipped = plan["batches"], plan["context_skipped"]
    except ContextStop as stop:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason=stop.code, detail=stop.detail, fix=stop.fix, scope=scope))
        return 2
    except ContextSelectionError as error:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason="CONTEXT_SELECTION_INVALID", detail=str(error),
                               **({"fix": error.fix} if error.fix else {}), scope=scope))
        return 2
    except HostStop as stop:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason=stop.code, detail=str(stop), scope=scope,
                               **({"fix": stop.fix} if stop.fix else {})))
        return 2
    except (InventoryError, ValueError, OSError) as error:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason="INVENTORY_INVALID", detail=_inventory_invalid_detail(error),
                               fix=fix_for("INVENTORY_INVALID"), scope=scope))
        return 2
    _print(_scan_payload(project, run_root, args, module, scope, frozen_inventory, exclusion_receipt, frozen["baseline"]["digest"],
                         str(attempt["attempt_id"]), plan, context_summary, context_warnings, abandoned))
    return 0


def _resume_scan(project: Path, args: Any, run_root: Path, abandoned: Sequence[str]) -> int | str:
    """The repeat of a scan finishes the run it cut.  The frozen inventory and baseline of the run are read from the
    journal, the batches are made again from the request, the ones the run has must be the same (by digest), the missing ones are
    written.  Returns the exit code of the scan, or why the run cannot be finished: it is then left as it is and a new one is made."""
    from tools import pilot_state as pilot
    from tools.project_inventory import ContextSelectionError, InventoryError, build_exclusion_receipt

    state = pilot.derive_state(run_root)
    attempt = state["attempts"][-1]
    attempt_id = str(attempt["attempt_id"])
    scope: dict[str, Any] = {"module": None, "parent_files": None, "dependency_files": []}
    try:
        # the journal's readers only: the baseline is the attempt's (its self-digest is the attempt's baseline_digest), the
        # inventory is the one the journal has as INVENTORY_READY
        try:  # the one reader of the attempt's baseline — the file, its self-digest and the journal's EXECUTION_BASELINE_FROZEN
            baseline = pilot.frozen_baseline(run_root, attempt)
        except pilot.BaselineUnbound:
            return "baseline не совпал с записанным в попытке"
        frozen_inventory = pilot._frozen_inventory(project, run_root, str(baseline["inventory_digest"]))
        skillsrc_document, _skillsrc_digest = _read_skillsrc_binding(project / ".skillsrc")
        module_root, module = _resolve_scan_module(project, getattr(args, "module", None), skillsrc_document)
        module_path = module_root.resolve().relative_to(project).as_posix() or "."
        if module_path != attempt.get("module"):
            return "другой модуль"
        scope["module"] = module_path
        prefix = "" if module_path == "." else module_path + "/"
        scope["parent_files"] = [item["project_path"] for item in frozen_inventory["files"]
                                 if prefix and item.get("origin") != "context" and not item["project_path"].startswith(prefix)]
        task_ids, other_task_ids, context_summary, context_warnings = _task_document_scope(
            frozen_inventory, project, getattr(args, "docs", None), module_root=module_root, cases=bool(getattr(args, "cases", None)))
        context_warnings = [*context_warnings, *_module_warnings(module, getattr(args, "module", None))]
        plan = _scan_context_plan(project, args, run_root, frozen_inventory, module_root, (task_ids, other_task_ids, context_summary))
        have = {str(event["artifact_digest"]) for event in state["events"]
                if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"}
        if not have <= {str(batch["receipt"]["digest"]) for batch in plan["batches"]}:
            return "пачки контекста пересчитались иначе, чем записаны"
        _publish_context(run_root, attempt_id, plan)
    except (ContextSelectionError, InventoryError, HostStop, ValueError, OSError, KeyError) as error:
        return f"пересчёт пачек не удался ({type(error).__name__})"
    _print(_scan_payload(project, run_root, args, module, scope, frozen_inventory, build_exclusion_receipt(frozen_inventory),
                         str(attempt["baseline_digest"]), attempt_id, plan, context_summary, context_warnings, abandoned, resumed=True))
    return 0


def _moved_project_note(project: Path, root: Path) -> dict[str, Any] | None:
    """``None`` when the run was made at ``project``; else what to tell about the moved project (code, paths, fix)."""
    from tools.pilot_state import run_project_move

    move = run_project_move(project, root)
    if move is None:
        return None
    where = f"прогон создан в {move['recorded']}, сейчас проект в {move['current']}"
    if move["same_structure"]:
        fix = (f"проект перенесён: {where}. status и report работают и здесь (сверка файлов с baseline открытой попытки здесь не делается); "
               "finish, materialize и ingest — только там, где создан прогон, а здесь — новый scan")
    else:
        shown = ", ".join(move["missing"][:5]) + (" и др." if len(move["missing"]) > 5 else "")
        fix = (f"проект перенесён: {where}, но состав файлов другой (нет: {shown or 'опись прогона не читается'}). "
               "Откройте папку, где создан прогон, или начните новый scan здесь")
    return {"code": "PROJECT_MOVED", "recorded": move["recorded"], "current": move["current"],
            "same_structure": move["same_structure"], "fix": fix}


def _with_moved_note(payload: dict[str, Any], project: Path, root: Path) -> dict[str, Any]:
    note = _moved_project_note(project, root)
    if note is None:
        return payload
    return {**payload, "warnings": [*payload.get("warnings", []), f"PROJECT_MOVED: {note['fix']}"], "project_moved": note}


def _read_run_coordinate(project: Path, run_id: str, *, allow_moved: bool = False) -> tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
    from tools.pilot_state import derive_state, read_run

    project = Path(project).resolve()
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise HostStop("EXECUTION_COORDINATES", "run must be one exact durable run ID")
    expected_parent = (project / ".pilot-runs").resolve()
    root = expected_parent / run_id
    try:
        run = read_run(root)
        state = derive_state(root)
    except JournalRefusal:  # a busy run or a damaged journal says so; it is no wrong coordinate
        raise
    except (OSError, ValueError, KeyError) as error:
        note = _moved_project_note(project, root)  # the run is there, but the project is not where it was made
        raise HostStop("EXECUTION_COORDINATES", "pilot run root is unreadable or outside this project",
                       note["fix"] if note else None) from error
    manifest = run.get("manifest")
    if (
        root.parent != expected_parent
        or not isinstance(manifest, Mapping)
        or (manifest.get("project") != str(project) and not allow_moved)
        or run_id != manifest.get("run_id")
        or root.name != run_id
        or not isinstance(state, Mapping)
    ):
        raise HostStop("EXECUTION_COORDINATES", "run ID and project must agree")
    return root, run, state


# ingest closes a run whose claim of the start was made and whose receipt was not published; finish does not
_INTERRUPTED_FIX = ("приём отчётов начался, но квитанции исполнения нет (ingest прервался): снова вызови ingest с теми же отчётами или "
                    "`ingest --no-reports --reason RESULT_UNAVAILABLE` — он закроет попытку")


def _leave(root: Path, attempt_id: str) -> dict[str, Any]:
    """The mark «оставлен» of a run stopped before the canonical review — the attempt is not closed;
    only the same-request check of scan skips the run. A decision of the run, so a file and its pair of events
    (``bind_bytes``, batch ``left-v1``): written once, a repeat only finishes the events; a file of other bytes is RECEIPT_FILE_UNBOUND."""
    from tools.pilot_state import LEFT_BATCH, LEFT_MARK, bind_bytes

    value = {"attempt_id": attempt_id, "reason": "LEFT_BEFORE_REVIEW"}
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    bind_bytes(root, attempt_id, root / LEFT_MARK, data, digest="sha256:" + hashlib.sha256(data).hexdigest(), batch_id=LEFT_BATCH, label="left mark")
    return value


def _before_canonical_review(root: Path, attempt_id: str) -> bool:
    """The attempt has no reviewer session yet — no closing command exists before the canonical review.  Only the absence of the
    ledger says so: a ledger published and then unreadable (damaged, unbound) is a review that began, and its refusal is the finish's own."""
    from tools.pilot_state import derive_state

    return not any(event.get("attempt_id") == attempt_id and event.get("batch_id") == "reviewer-ledger-v1" and event.get("event_type") == "ARTIFACT_PUBLISHED"
                   for event in derive_state(root)["events"])


def _closed_before_review(project: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The honest answer and the next run — this run stays as it is, --new-run starts another."""
    request = manifest.get("scan_request")
    command = (_scan_command(project, request, extra=("--new-run",)) if isinstance(request, Mapping)
               else "scan с теми же --module, --docs и --profile, что у этого прогона, и --new-run")
    return {
        "status": "error", "stage": "finish", "reason": "FINISH_FACT_UNDETERMINED", "run_id": manifest["run_id"],
        "message": "попытку нельзя закрыть до ревью кейсов: ревью не начиналось, а стадии до ревью не дают закрывающего состояния",
        "fix": "если стадия до ревью ещё может ответить — продолжи по run_pipeline status (state, next); если нет — "
               f"{printed(sys.executable)} -m tools.run_pipeline finish --project {printed(project)} --run {printed(manifest['run_id'])} --abandon "
               "LEFT_BEFORE_REVIEW оставит прогон, и простой scan начнёт новый; следующий заход: " + command,
        "next_run": {"why": "попытка остановилась до ревью кейсов: следующий заход — новый прогон того же запроса", "command": command},
    }


def _exact_fix(fix: str, project: Path, root: Path, attempt_id: str) -> str:
    """The fix of finish with this run's own coordinates, a command to copy as it is."""
    return (fix.replace("python -m tools.", f"{printed(sys.executable)} -m tools.").replace("<project>", printed(project))
            .replace("<run_id>", root.name).replace("<run>/tmp/session.json", printed(root / "tmp" / "session.json"))
            .replace("<run>", printed(root)).replace("<attempt_id>", attempt_id))


def _execution_receipt_bound(root: Path, attempt_id: str) -> bool:
    """Whether the attempt has an execution receipt with its published and read-back events (only reads)."""
    from tools.pilot_state import read_attempt_receipt

    try:
        read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return True


def _refuse_run_of_full_delivery(args: Any) -> None:
    """status, report and finish of a run whose profile needs the automation (local-pilot-v1), in a delivery without it:
    refused at once, before any receipt of the automation is read; the refusal is journaled like any other."""
    from tools.pilot_state import moved_project_allowed, read_run

    run = getattr(args, "run", None)
    if execution_gate.mode_b_available() or not (isinstance(run, str) and re.fullmatch(r"[0-9a-f]{32}", run)):
        return
    try:
        with moved_project_allowed():
            profile = read_run(Path(args.project).resolve() / ".pilot-runs" / run)["manifest"].get("policy_profile")
    except (OSError, ValueError, KeyError, TypeError):
        return  # an unreadable run is the answer of the command itself
    if profile == execution_gate.LOCAL_PILOT:
        raise HostStop("MODE_B_NOT_IN_PACKAGE", f"прогон {run} сделан профилем {profile}, а в этой поставке нет режима автотестов")


@one_closure
def cmd_finish(args: Any) -> int:
    """Close the current attempt in one command — the same order and checks as the manual sequence."""
    _refuse_run_of_full_delivery(args)
    from tools.finalize_attempt import FinalizationError, finalize_durable_branch_attempt, finalize_durable_execution_attempt
    from tools.pilot_state import derive_state

    project = Path(args.project).resolve()
    root, run, state = _read_run_coordinate(project, args.run)
    if not state["attempts"]:
        raise HostStop("FINISH_FACT_UNDETERMINED", "the run has no attempt yet")
    attempt = state["attempts"][-1]
    attempt_id = str(attempt["attempt_id"])
    # the branch is the journal's - the start event, or an execution receipt the journal binds (a NOT_RUNNABLE
    # execution has a receipt and no start); a bare file execution-receipts/<attempt>.json decides nothing
    executed = (any(event.get("attempt_id") == attempt_id and event["event_type"] == "EXECUTION_STARTED" for event in derive_state(root)["events"])
                or _execution_receipt_bound(root, attempt_id))
    refusal = None
    if not executed and attempt.get("state") != "TERMINAL":
        from tools.finalize_attempt import automation_next

        try:
            outcome, detail = automation_next(root, attempt_id)
            refusal = detail if outcome == "AUTOMATION_NOT_MATERIALIZABLE" else None
        except (KeyError, OSError, TypeError, ValueError):
            refusal = None
    abandon = getattr(args, "abandon", None)
    if abandon is not None and (executed or attempt.get("state") == "TERMINAL"):
        from tools import execution_gate

        # ingest only where the attempt is open after an execution start and the package has the execution; a closed
        # attempt is read with report/status
        closes_by_ingest = executed and attempt.get("state") != "TERMINAL" and execution_gate.execution_available()
        raise HostStop("ABANDON_NOT_NEEDED", "попытку закрывает ingest или она уже закрыта: --abandon не нужен",
                       _exact_fix("python -m tools.run_pipeline ingest --project <project> --run <run_id>" if closes_by_ingest else
                                  "попытка закрыта: итог — python -m tools.run_pipeline report --project <project> --run <run_id> "
                                  "(или status); следующий заход — next_run из ответа finish/status", project, root, attempt_id))
    if abandon == "LEFT_BEFORE_REVIEW":  # the run stays as it is; a plain scan of the request starts a new one
        if not _before_canonical_review(root, attempt_id):
            raise HostStop("ABANDON_NOT_NEEDED", "попытка уже дошла до ревью кейсов: LEFT_BEFORE_REVIEW только до него",
                           _exact_fix("python -m tools.run_pipeline finish --project <project> --run <run_id>", project, root, attempt_id))
        closed = _closed_before_review(project, run["manifest"])
        _print({"status": "ok", "stage": "finish", "run_id": run["manifest"]["run_id"], "left": _leave(root, attempt_id),
                "message": "прогон оставлен до ревью кейсов; простой scan того же запроса начнёт новый", "next_run": closed["next_run"]})
        return 0
    from tools.orchestrate_test_case_revision import close_pending_rework, published_dir

    published_dir(root)  # a published directory that leads out of the run: refused before the attempt is closed
    if not executed:
        from tools.finalize_attempt import NEW_RUN_FIX, finish_refusal

        try:
            if attempt.get("state") != "TERMINAL":
                finish_refusal(root, attempt_id, abandon)  # judged by the journal, before the write below
            close_pending_rework(root, attempt_id)  # the first review sent cases back and the rework is not over: r2 and the rejected cases are published now, once
        except FinalizationError as error:
            raise HostStop(str(error).split(":", 1)[0], str(error), _exact_fix(error.fix, project, root, attempt_id)) from error
        except (KeyError, TypeError, ValueError) as error:  # a rework that does not read (its receipt is not the answer's): status would lead back here
            raise HostStop("FINISH_FACT_UNDETERMINED", f"закрыть доработку не удалось ({str(error).split(':', 1)[0][:80]})", _exact_fix(NEW_RUN_FIX, project, root, attempt_id)) from error
    try:
        closed = (finalize_durable_execution_attempt(root, attempt_id) if executed
                  else finalize_durable_branch_attempt(root, attempt_id, abandon))
    except FinalizationError as error:
        if not executed and str(error).startswith("FINISH_FACT_UNDETERMINED") and _before_canonical_review(root, attempt_id):
            _print(_closed_before_review(project, run["manifest"]))
            return 2
        code = str(error).split(":", 1)[0] if str(error).startswith(("FINISH_", "ABANDON_")) else "FINALIZATION"
        fix = getattr(error, "fix", None) or (_INTERRUPTED_FIX if executed and not _execution_receipt_bound(root, attempt_id) else None)
        raise HostStop(code, str(error), _exact_fix(fix, project, root, attempt_id) if fix else None) from error
    except (KeyError, TypeError, ValueError, OSError) as error:
        if not executed or _execution_receipt_bound(root, attempt_id):  # a file of the run that no event binds is no receipt
            raise
        raise HostStop("FINISH_FACT_UNDETERMINED", f"the started execution has no receipt yet ({error})", _INTERRUPTED_FIX) from error
    result = closed["result"]
    manifest = run["manifest"]
    from tools.orchestrate_test_case_revision import close_without_verdict

    unreviewed = close_without_verdict(root, attempt_id)  # a run closed before the verdict leaves the UNREVIEWED file, never a CSV
    _print({
        "status": "ok", "stage": "finish", "run_id": manifest["run_id"], "attempt_id": attempt_id,
        "branch": "execution" if executed else "no-execution", "idempotent": bool(closed.get("idempotent")),
        **{key: result.get(key) for key in ("completion", "verification", "coverage", "accepted", "reason_code")},
        "exit_code": int(closed["exit_code"]), "policy_profile": manifest["policy_profile"],
        **{key: manifest[key] for key in ("requested_policy_profile", "execution_skipped") if key in manifest},
        **({"review_independence": result["review_independence"]} if "review_independence" in result else {}),
        **({"materialization_refusal": refusal} if refusal else {}),
        **({"unreviewed_files": unreviewed} if unreviewed else {}),
        **_quarantine_summary(root, attempt_id),
        **_review_notes_summary(root, attempt_id),
        **_human_summary(project, root, attempt_id, manifest),
        **_style_notes(project, root, attempt_id),
        **_report_mismatch(root, attempt_id),
        **_automation_split(root, attempt_id),
        **({"fix": "только если человек согласен на ревью в той же сессии, повтори scan этого запроса с --accept-self-review: "
                   + (_scan_command(project, manifest["scan_request"], extra=() if manifest["scan_request"].get("accept_self_review")
                                    else ("--accept-self-review",))
                      if isinstance(manifest.get("scan_request"), Mapping)
                      else "scan с теми же --module, --docs и --profile, что у этого прогона, и --accept-self-review")}
           if result.get("reason_code") == "INDEPENDENCE_UNVERIFIED" else {}),
        **_next_run(project, manifest, result.get("reason_code")),
    })
    return int(closed["exit_code"])


def cmd_report(args: Any) -> int:
    """Write the run report and the analyst questions; only reads the journal."""
    _refuse_run_of_full_delivery(args)
    from tools.run_report import GUARANTEE, write_report

    from tools.pilot_state import moved_project_allowed

    project = Path(args.project).resolve()
    with moved_project_allowed():  # a moved project's run is read, never rewritten
        root, run, _state = _read_run_coordinate(project, args.run, allow_moved=True)
        written = {Path(path).name: path for path in write_report(project, run["manifest"]["run_id"])}
    _print(_with_moved_note({"status": "ok", "stage": "report", "run_id": run["manifest"]["run_id"], "report_md": written["report.md"],
                             "report_json": written["report.json"], "questions": written["questions-for-analyst.md"],
                             "guarantee": GUARANTEE, "warnings": []}, project, root))
    return 0


def cmd_status(args: Any) -> int:
    _refuse_run_of_full_delivery(args)
    from tools.pilot_state import closure_cache, moved_project_allowed
    from tools.project_inventory import inventory_cache

    project = Path(args.project).resolve()
    # one block, so the run manifest is validated and the journal derived once; the answer is printed after
    # the block's final full journal check. And the module's inventory built once.
    with moved_project_allowed(), closure_cache(), inventory_cache():  # status of a moved project works (the final check of closure_cache too)
        result = _status_result(project, args.run)
    _print(result)
    return 0


def _status_result(project: Path, run_id: str) -> dict[str, Any]:
    root, run, state = _read_run_coordinate(project, run_id, allow_moved=True)
    attempt = state["attempts"][-1] if state["attempts"] else None
    # the drift of the project is a fact of the run, so it makes the state; a closed or left run is not changed by it (tools.stages)
    open_run = attempt is not None and attempt.get("state") != "TERMINAL" and _moved_project_note(project, root) is None
    # in a moved project the baseline binds the folder name and path of the scan, so the comparison would report a
    # drift of no file at all; the PROJECT_MOVED note says that the check is not made here
    result = _durable_status(project, root, stage="status", **(_status_drift(project, root, run["manifest"], attempt) if open_run else {}))
    if attempt is not None:
        result.update(_review_notes_summary(root, str(attempt["attempt_id"])))
        result.update(_human_summary(project, root, str(attempt["attempt_id"]), run["manifest"]))
        from tools.review_canaries import status_fields

        result.update(status_fields(root, str(attempt["attempt_id"]), result))  # the canaries of the review, REVIEW_UNRELIABLE
    if attempt is not None and attempt.get("state") == "TERMINAL":
        from tools.pilot_state import read_terminal_result

        try:
            result.update(_next_run(project, run["manifest"], read_terminal_result(root, str(attempt["attempt_id"])).get("reason_code")))
        except (KeyError, OSError, TypeError, ValueError):
            pass
    if attempt is not None and (code := receipts_warning(project, root, str(attempt["attempt_id"]))):
        from tools.run_report import RECEIPT_WARNINGS

        result["warnings"] = [*result.get("warnings", []), f"{code}: {RECEIPT_WARNINGS[code]}"]
    return _with_moved_note(result, project, root)


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="Deterministic helpers for the CLI-driven test-skills pack.")
    shared = argparse_parent()
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", parents=[shared])
    scan_parser.add_argument("--profile", choices=("local-pilot-v1", "cases-only-v1"), default=None,
                             help="P8a-1: default is what this package can do — local-pilot-v1 with its execution files, else cases-only-v1.")
    scan_parser.add_argument("--cases", help="FaT-IMP: a Zephyr CSV of ready test cases inside the project; autotests from them, without --docs")
    scan_parser.add_argument("--accept-self-review", action="store_true",
                             help="FaT-4b: the human accepts a reviewer in the same session, without a fresh context (marked SELF).")
    scan_parser.add_argument("--no-pause", action="store_true",
                             help="FaT-P3: no stop after the context marker for the human to exclude requirements with gaps (kept in the run manifest).")
    scan_parser.add_argument("--reference", default=None,
                             help="FaT-REF: a Zephyr Scale CSV of reference cases — compared with the new cases as a review hint")
    scan_parser.add_argument("--new-run", action="store_true",
                             help="FaT-G: start a new run although an unfinished run of the same request exists.")
    if execution_gate.execution_available():  # the policy of failed generated tests is a flag of mode B, not of a delivery of cases
        scan_parser.add_argument("--disposition-policy", choices=("quarantine", "cleanup"),
                                 help="FaT-8: on FAIL quarantine failed methods (default) or clean every generated file as before.")
    status_parser = sub.add_parser("status", parents=[shared])
    finish_parser = sub.add_parser("finish", parents=[shared], help="Close the current attempt (FaT-4): the branch after ingest or a branch without execution.")
    finish_parser.add_argument("--run", required=True)
    finish_parser.add_argument("--abandon", choices=("STAGE_RETRY_EXHAUSTED", "ASSEMBLY_INVALID", "AUTOMATION_NOT_MATERIALIZABLE",
                                                     "LEFT_BEFORE_REVIEW"),
                               help="FaT-AB: close an automation chain that cannot go on, only where no other command closes it.")
    status_parser.add_argument("--run", required=True, help="Exact durable run ID")
    execution = execution_gate.load("exec_commands")  # exec (replaced), materialize and ingest exist only where their module does
    if execution is not None:
        execution.add_parsers(sub, shared)
    report_parser = sub.add_parser("report", parents=[shared], help="FaT-6: report.md/report.json and questions-for-analyst.md of the run, without project content.")
    report_parser.add_argument("--run", required=True, help="Exact durable run ID")
    return parser


def argparse_parent():
    shared = JsonArgumentParser(add_help=False)
    shared.add_argument("--project", required=True)
    shared.add_argument("--target")
    shared.add_argument("--module", help="Exact module ID from inventory/.skillsrc, not its filesystem path")
    shared.add_argument("--docs", action="append", default=None)
    return shared


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    _ARGS[:] = [args]
    commands = {
        "scan": cmd_scan,
        "status": cmd_status,
        "finish": cmd_finish,
        "report": cmd_report,
    }
    execution = execution_gate.load("exec_commands")
    if execution is not None:
        commands.update(execution.COMMANDS)
    try:
        return commands[args.command](args)
    except Exception as error:
        from tools.skillsrc_manifest import SkillsrcError

        if isinstance(error, HostStop):
            _print({"status": "error", "reason": error.code, "message": str(error), **({"fix": error.fix} if error.fix else {})})
            return 2
        if isinstance(error, SkillsrcError):
            _print({"status": "error", "reason": error.code, "message": str(error), **({"fix": error.fix} if error.fix else {})})
            return 2
        if isinstance(error, RuntimeError):
            payload: dict[str, Any] = {"status": "error", "reason": str(error)}
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                payload["reason"] = code
                payload["message"] = str(error)
                if isinstance(error, execution_gate.ModeBMissing):
                    payload["fix"] = fix_for(code)
            _print(payload)
            return 2
        if isinstance(error, (FileNotFoundError, ValueError)):
            # the refusal's own code (CODE: prefix) and fix, like the branch below; INPUT_INVALID otherwise
            from tools.json_cli import error_code_prefix, error_fields

            fields = error_fields(error)
            code = getattr(error, "code", None) or error_code_prefix(fields["message"]) or "INPUT_INVALID"
            _print({"status": "error", "reason": code, **fields, "code": code})
            return 2
        if isinstance(error, (KeyError, TypeError, OSError)):
            # a JSON answer with the code instead of a traceback; an OSError names its file
            from tools.json_cli import error_fields

            fields = error_fields(error)
            _print({"status": "error", "reason": fields["code"], **fields})
            return 2
        # any other error is still a JSON answer, never a traceback the agent cannot read
        # the message is cut: an exception may carry a piece of a file, and the package prints no more than it needs
        _print({"status": "error", "reason": "INTERNAL", "message": f"{type(error).__name__}: {str(error)[:500]}",
                "fix": "сообщите разработчикам пакета вывод этой команды"})
        return 2


# Public names for execution_core: the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.
print_payload = _print
read_run_coordinate = _read_run_coordinate
drift_report = _drift_report
automation_split_fields = _automation_split


if __name__ == "__main__":
    # the execution commands and the tests take ``tools.run_pipeline``; run as ``__main__`` this file is a module of its own
    # (its own ``_print``, ``_PRINTED``), so hand over to the imported module — one ``main``
    from tools.run_pipeline import main as _main

    raise SystemExit(_main())
