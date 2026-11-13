"""Deterministic scan/status/materialize/ingest helpers for the CLI-driven skill pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import hashlib
import json
import os
import re
import uuid
from typing import Any, Mapping, Sequence

from tools import execution_gate
from tools.fixes import fix_for
from tools.json_cli import JsonArgumentParser
from tools.run_lock import JournalRefusal
from tools.project_inventory import (
    ContextSelectionError,
    ContextStop,
    InventoryError,
    build_inventory,
    context_filter,
    select_context_batches,
    skillsrc_exclude,
    skillsrc_include,
    skipped_summary,
)

ROOT = Path(__file__).resolve().parents[1]

_SCAN_CONTEXT_BYTE_BUDGET = 256 * 1024  # default of .skillsrc limits.context_batch_bytes


class HostStop(RuntimeError):
    def __init__(self, code: str, message: str = "", fix: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.fix = fix or fix_for(code)  # the table has a way out for the codes without a fix at their raise site


# the text a human pastes into cmd.exe, PowerShell or sh.  One form for the three: a plain token as it is; a token with a space, a bracket
# or a backslash in double quotes (the same in all three); any other token is not printed.  What quotes cannot hold: ``"`` closes them, ``%``
# and ``!`` are expanded by cmd.exe inside them, ``^`` escapes there, ``$`` and the back quote are expanded by sh and PowerShell inside them, and
# ``& | < > ; ' *`` and the line breaks are not worth the doubt for a path; a backslash at the end of a token escapes the closing quote.  The
# dashes, « », № and ``?`` of a Russian heading are plain text inside double quotes to all three (“ ” „ ‘ ’ are quotes to PowerShell).
_BARE_TOKEN = re.compile(r"[A-Za-z0-9_./:=,#+\-]+")
_QUOTABLE_MARKS = " _./:=,#@+-\\()[]{}~—–«»№?"


def command_token(token: str) -> str:
    """The token as it is printed - plain, or in double quotes - or ``HostStop COMMAND_NOT_PRINTABLE`` naming what cannot be printed."""
    if _BARE_TOKEN.fullmatch(token):
        return token
    if all(char.isalnum() or char in _QUOTABLE_MARKS for char in token) and not token.endswith("\\") and "\\\\" not in token:
        return '"' + token + '"'
    bad = sorted({char for char in token if not (char.isalnum() or char in _QUOTABLE_MARKS)} | ({"\\"} if token.endswith("\\") or "\\\\" in token else set()))
    shown = " ".join(repr(char)[1:-1] if not char.isprintable() else char for char in bad)
    raise HostStop("COMMAND_NOT_PRINTABLE", f"в команде для человека есть символ(ы) {shown}: cmd.exe, PowerShell или sh прочтут их как подстановку, экранирование или кавычку "
                   f"(в «{token[:80]}»)",
                   f"переименуйте или переместите проект (или интерпретатор, модуль, документ) в каталог, в имени которого нет {shown}, и начните прогон заново "
                   "(scan --new-run): путь входит в напечатанную команду; для --docs файл#Раздел — раздел без этих символов в заголовке "
                   "или документ целиком")


def printed(value: object) -> str:
    """A value the code puts into a command the agent runs as it is: in double quotes, once ``command_token`` said it prints."""
    command_token(str(value))
    return '"' + str(value) + '"'


def printed_or(value: object, template: str) -> str:
    """``command_token`` for a value in a text that is only advice: the template when no shell takes the value as it is (the advice is not worth a refusal)."""
    try:
        return command_token(str(value))
    except HostStop:
        return template


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
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The one refusal envelope {status, code, message, fix, next}; ``reason`` stays beside ``code``."""
    reason = payload.get("reason") or payload.get("code") or "ERROR"
    return {**payload, "code": payload.get("code") or reason, "message": payload.get("message") or payload.get("detail") or reason,
            "fix": payload.get("fix"), "next": payload.get("next")}


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


def _exclusion_summary(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the scan left out, for a person; derived from the attempt's frozen inventory, never stored."""
    from tools.pilot_state import frozen_baseline
    from tools.project_inventory import read_inventory_receipt
    from tools.scan_rules import exclusion_summary

    root = Path(run_root)
    try:
        baseline = frozen_baseline(root, attempt)
        inventory = read_inventory_receipt(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    except (KeyError, OSError, ValueError):
        return None
    return exclusion_summary(inventory["exclusions"], str(attempt.get("module") or "."))


def _batches_all_answered(root: Path, attempt_id: str, responses: Sequence[Mapping[str, Any]]) -> bool:
    """Whether every batch of the published plan has its answer in the journal (only reads; an unreadable plan is «not yet»)."""
    from tools import pilot_state as pilot

    try:
        batches = pilot.read_attempt_receipt(root, attempt_id, "batch-plan", "ARTIFACT_READ_BACK")["record"]["plan"]["batches"]
        return bool(batches) and {f"tc-generator:{row['batch_id']}" for row in batches} <= {row.get("stage_instance_id") for row in responses}
    except (OSError, KeyError, TypeError, ValueError):
        return False


def _durable_status(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    """The facts of the run and, from them, its ``state``, who is ``waiting_for`` and the ``next`` verb (``tools.stages``)."""
    from tools.pilot_state import read_run
    from tools.stages import next_action

    result = _durable_status_facts(project, run_root, stage=stage, status=status, **extra)
    cut = left = scope = None
    if result.get("attempt_id") is not None:
        try:  # the scan request of the run, to see a scan that was cut
            request = read_run(Path(run_root))["manifest"].get("scan_request")
        except (KeyError, OSError, TypeError, ValueError):
            request = None
        cut = _scan_cut(Path(run_root), request if isinstance(request, Mapping) else None)
        left = _left_mark(Path(run_root)) is not None  # a left run goes nowhere; the plain scan of its request starts anew
        if result.get("next_expected_artifact") == "generator fragments":
            scope = _scope_pause(Path(run_root), str(result["attempt_id"]))
        if cut is not None and not left:  # nothing goes on from half a context
            result["scan_incomplete"] = f"scan оборван: записано {cut}; повтори тот же scan (scan_command) — он достроит недостающее в этом прогоне"
        if (left or cut is not None) and isinstance(request, Mapping):  # after /clear the flags of the request are nowhere else
            result["scan_command"] = _scan_command(project, request)
        note = _scope_mismatch(Path(run_root), str(result["attempt_id"]))
        if note:
            result["scope_warning"] = {"code": "SCOPE_DECISION_MISMATCH", "detail": note}
    result.update(next_action({**result, "scan_cut": cut, "left": left, "scope_pause": scope is not None,
                                 "scope_answer": Path((scope or {}).get("answer_slot", "")).is_file()}))
    result.update(scope or {})
    if result["state"] in ("marker", "generator", "review", "automation", "automation_review"):  # the id of the step (with the revision), the slot and the size: from the steps of the stage
        from tools.stage_steps import stage_status

        try:
            result.update(stage_status(Path(run_root), {"marker": "context-marker", "generator": "tc-generator", "review": "tc-reviewer", "automation": "tc-to-autotest",
                                                                  "automation_review": "autotest-reviewer"}[result["state"]]))
        except (OSError, KeyError, TypeError, ValueError):  # a plan that cannot be made leaves the verb of the table
            pass
    for gone in ("next_expected_artifact", "last_confirmed_stage"):  # the state says it
        result.pop(gone, None)
    return result


def _scope_mismatch(run_root: Path, attempt_id: str) -> str | None:
    """Once the plan is published the scope is its ``out_of_scope``; a ``scope-decision.json`` that says another thing is named.  Only reads."""
    from tools import scope_pause

    try:
        published = scope_pause.published_scope(run_root, attempt_id)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return published["note"] if published is not None and published["mismatch"] else None


def _scope_pause(run_root: Path, attempt_id: str) -> dict[str, Any] | None:
    """After the marker the plan of the batches waits for the human's answer on the gaps of the marker.  Only reads; ``None`` until the
    marker has gaps, the human has not answered and the run keeps its pause."""
    from tools import scope_pause

    try:
        scope = scope_pause.scope_state(run_root, attempt_id)
    except scope_pause.ScopeRefused as error:  # a decision file that is not the human's answer: fail closed, say so
        return {"scope_warning": {"code": error.code, "detail": str(error)}}
    except (OSError, KeyError, TypeError, ValueError):
        return None
    if not scope["pending"]:
        return None
    from tools.stage_steps import slot

    return {**scope_pause.gap_summary(scope["gaps"]), "answer_slot": str(slot(run_root, "scope"))}


def _quarantine_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """What the FAIL closure did to the generated tests, read from the attempt's disposition receipt."""
    from tools import pilot_state as pilot

    try:
        rows = pilot.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["files"]
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    quarantine = [
        {"file": row["path"], "method": item["method"], "ref": item["ref"], "message": item["message"],
         **{key: item[key] for key in ("failed_rows", "disabled_passed_rows") if key in item}}  # a group method's rows
        for row in rows if row.get("disposition") == "QUARANTINED" for item in row["quarantine"]
    ]
    unresolved = [row["path"] for row in rows if row.get("reason_code") == "QUARANTINE_UNRESOLVED"]
    if not quarantine and not unresolved:
        return {}
    return {"quarantine": quarantine, **({"quarantine_unresolved": (
        "QUARANTINE_UNRESOLVED: упавшие методы не удалось однозначно сопоставить с отчётом ingest, "
        f"поэтому сгенерированные файлы убраны целиком, включая прошедшие тесты: {', '.join(unresolved)}"
    )} if unresolved else {})}


def _review_notes_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """How many notes the accepted case/autotest review left and where its answer is; nothing when there are none."""
    from tools.run_report import review_notes

    summary: dict[str, Any] = {}
    for field, review in (("review_notes", "cases"), ("autotest_review_notes", "autotests")):
        try:
            notes = review_notes(run_root, attempt_id, review)
        except (KeyError, OSError, TypeError, ValueError):  # a hint never fails the step
            continue
        if notes["count"]:
            summary[field] = {"count": notes["count"], "path": notes["path"]}
    return summary


_KEPT_DISPOSITIONS = frozenset({"RETAINED", "QUARANTINED", "PRESERVED_EXECUTION_UNKNOWN", "PRESERVED_CONTENT_CONFLICT",
                                 "PRESERVED_CLEANUP_CONFLICT"})


def _style_notes(project: Path, run_root: Path, attempt_id: str) -> dict[str, str]:
    """The project's style checks the selected-tests run skipped and how to format the files left in the project."""
    if not execution_gate.mode_b_available():
        return {}  # a delivery of cases has no build and no style checks to report
    adapters = execution_gate.load_mode_b("execution_adapters")
    from tools import pilot_state as pilot
    from tools.project_inventory import read_execution_baseline

    try:
        attempt = next(row for row in pilot.derive_state(run_root)["attempts"] if row.get("attempt_id") == attempt_id)
        baseline = read_execution_baseline(Path(run_root) / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        project_root = Path(project).resolve()
        module_root = project_root if attempt["module"] == "." else project_root.joinpath(*str(attempt["module"]).split("/"))
        adapter_id = str(baseline["adapter_id"])
        runtime = str(baseline[{adapters.PYTEST: "interpreter_path", adapters.SYSTEM_MAVEN: "executable_path"}.get(adapter_id, "wrapper_path")])
        executable = runtime if adapter_id == adapters.SYSTEM_MAVEN else str(module_root / runtime)
        try:
            rows = pilot.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["files"]
        except (KeyError, OSError, TypeError, ValueError):
            rows = []
        kept = tuple(str(row["path"]) for row in rows if row.get("disposition") in _KEPT_DISPOSITIONS)
        return adapters.style_notes(adapter_id, executable, adapters.project_style_checks(project_root, module_root), kept)
    except (KeyError, OSError, StopIteration, TypeError, ValueError):  # a hint never fails the step
        return {}


def _report_mismatch(run_root: Path, attempt_id: str) -> dict[str, str]:
    """Why an UNKNOWN run could not match its JUnit report, and the fix — never the report text.

    A testcase whose method is selected but whose classname is none of that method's selected classes is what a class
    display name does (Surefire writes it as the classname); the hint names the selected classes only."""
    if not execution_gate.mode_b_available():
        return {}  # a delivery of cases has no test report to match
    from tools import pilot_state as pilot

    parse_junit_bytes, selected_classes_renamed = execution_gate.from_mode_b("run_tests", "parse_junit_bytes", "selected_classes_renamed")

    try:
        payload = pilot.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        if payload.get("verdict") != "UNKNOWN" or not any(row.get("kind") == "JUNIT_INVALID" for row in payload.get("process_evidence", [])):
            return {}
        execution = payload["execution"]
        classes: set[str] = set()
        for row in execution["artifact_evidence"]:
            relative = Path(str(row.get("path", "")))
            if row.get("kind") != "native_report" or relative.is_absolute() or relative.parts[:2] != ("artifacts", attempt_id) or ".." in relative.parts:
                continue
            # audit U4: the reading of run_tests, the one of the verdict
            classes |= selected_classes_renamed(parse_junit_bytes((Path(run_root) / relative).read_bytes()), execution["selectors"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError):  # a hint never fails the step
        return {}
    if not classes:
        return {}
    return {
        "report_mismatch": (f"отчёт JUnit не сопоставлен с тестами: методы класса {', '.join(sorted(classes))} записаны в отчёт "
                            "под другим classname — так делает @DisplayName на классе теста"),
        "report_fix": "убери @DisplayName с класса теста (у методов можно) и начни новый прогон тем же scan",
    }


def _durable_status_facts(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    from tools import pilot_state as pilot
    from tools.project_inventory import read_execution_baseline
    from tools.revision_selection import REWORK_REVIEW_STAGE, REWORK_STAGE

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
        "policy_profile": run["manifest"]["policy_profile"],
        **{key: run["manifest"][key] for key in ("requested_policy_profile", "execution_skipped") if key in run["manifest"]},
        **({"self_review_accepted": True} if pilot.self_review_accepted(root) else {}),  # at scan or at the step of the review
        **({"disposition_policy": "cleanup"} if run["authorization"].get("disposition_policy") == "cleanup" else {}),
        **(_quarantine_summary(root, attempt["attempt_id"]) if attempt else {}),
        "warnings": [], "warning_evidence_path": None, "evidence_errors": [],
        **extra,
    }
    if attempt is None:
        result["stop_detail"] = "No attempt yet; an early scan failure may have no persisted cause."
        return result
    summary = _exclusion_summary(root, attempt)
    if summary is not None:
        result["exclusion_summary"] = summary
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
                "tc-to-autotest": "static automation review", "autotest-reviewer": "review disposition and materialization",
            }[stage_id.split(":", 1)[0]]
            if stage_id.startswith(("context-marker:requirements", "context-marker:batch-")):
                next_artifact = "context-marker parts merge"  # the remaining parts, then the merge by code
            if stage_id == "tc-generator:rework-1":
                next_artifact = "rework answered"  # nobody reviews the rework before V7b: finish publishes r2
            elif stage_id.startswith("tc-generator:") and _batches_all_answered(root, attempt_id, responses):
                next_artifact = "canonical reviewer package and boundary"  # the last answer assembled the candidate; prepare of the review publishes it from the journal
            if stage_id.startswith("autotest-reviewer:"):
                from tools.finalize_attempt import automation_next

                outcome = automation_next(root, attempt_id)[0]
                next_artifact = ("review disposition and materialization" if outcome == "ACCEPTED"
                                 else "automation revision" if outcome == "PENDING" else "automation refused")
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
    ledger_events = [row for row in events if row["event_type"] == "ARTIFACT_READ_BACK" and row.get("batch_id") == "reviewer-ledger-v1"]
    aborted = False  # a review closed without a verdict leaves its request without an answer for good: nothing waits for it
    if ledger_events:
        digest = ledger_events[-1]["artifact_digest"]
        path = f"reviewer-session-ledgers/{attempt_id}/{digest[7:]}.json"
        try:
            ledger = pilot.read_reviewer_session_ledger(root, attempt_id, digest)
            lifecycle = pilot.reviewer_lifecycle_projection(ledger)
            next_artifact = "reviewer evidence or verdict" if lifecycle["waiting"] else "effective canonical"
            if lifecycle["pre_verdict_abort"] or lifecycle["authoritative_verdict"] == "REJECTED":
                next_artifact, aborted = "negative branch trace and finalization", lifecycle["pre_verdict_abort"]
            record(digest, "canonical-review:" + ledger["status"], next_artifact, path, stop_reason=lifecycle["abort_reason"])
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    named_receipts = 0
    # These readers are read-only; the similarly named recovery helpers may write events.
    for kind, next_artifact in (
        ("rework-round", "rework fragment"),
        ("effective-canonical", "pre-finalization trace" if attempt["policy_profile"] == "cases-only-v1" else "automation artifact"),
        ("execution-inputs", "materialization"), ("generated-delta", "materialization command"),
        ("materialization-command", "human run report"), ("execution-receipt", "execution trace and dispositions"), ("execution-trace", "disposition receipt"),
        ("disposition-receipt", "pre-finalization trace"), ("resume-validation", "validated inputs or new scan"),
    ):
        path = pilot._receipt_target(root, attempt_id, kind)
        if not path.exists():
            if kind == "effective-canonical":  # gone where the journal recorded an accepting verdict is a named loss, not «nothing yet»
                try:
                    pilot.effective_canonical_or_reason(root, attempt_id)
                except (OSError, KeyError, TypeError, ValueError):
                    unreadable(str(path.relative_to(root)))
                    named_receipts += 1
            continue
        try:
            receipt = pilot._read_attempt_receipt_with_state(project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK")
            value, facts = receipt["record"], {}
            if kind == "generated-delta" and value["delta"]["facts"]["completion"] != "COMPLETE":
                next_artifact, facts["stop_reason"] = "disposition and finalization", "MATERIALIZATION_INCOMPLETE"
            if kind == "execution-receipt":
                facts["verification"] = value["payload"]["verdict"]
                facts["execution_diagnostics"] = value["payload"].get("diagnostics", [])
                if value["payload"].get("execution_source"):  # a human ran the build — said wherever the result is shown
                    facts["execution_source"] = {**value["payload"]["execution_source"], "note": "результат запуска предоставлен человеком, пакет его не воспроизводил"}
            if kind == "resume-validation":
                facts["stop_reason"] = value.get("reason_code")
            record(receipt["digest"], kind, next_artifact, receipt["path"], **facts)
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(str(path.relative_to(root)))
    lost = pilot.lost_attempt_receipts(root, state, attempt_id)
    if lost:
        # the journal read back a receipt of the attempt (its digest) and no file of the run carries that digest any more; the plan and the header
        # are named by their own path, a receipt of another kind (the journal does not say which) by its digest
        for generator_input, receipt_kind in (("plan", "batch-plan"), ("header", "generator-header")):
            try:
                pilot._generator_input(project, root, state, attempt_id, generator_input)
            except (OSError, KeyError, TypeError, ValueError):
                unreadable(str(pilot._receipt_target(root, attempt_id, receipt_kind).relative_to(root)))
                named_receipts += 1
        for digest in lost[named_receipts:]:
            result["evidence_errors"].append({"code": "EVIDENCE_UNVERIFIED", "path": f"{root}: квитанция {digest} из журнала без файла"})
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
    pending = [row for row in events if row["event_type"] == "MODEL_REQUESTED" and not any(response.get("stage_instance_id") == row.get("stage_instance_id") for response in responses)
               and not (aborted and row.get("stage_instance_id") == "tc-reviewer:canonical")
               and not (row.get("stage_instance_id") in (REWORK_STAGE, REWORK_REVIEW_STAGE) and pilot.read_effective_canonical_if_present(root, attempt_id) is not None)]  # a rework closed unfinished waits for nothing
    if pending:
        request = pending[-1]
        path = f"model-requests/{attempt_id}/{request['artifact_digest'][7:]}.json"
        try:
            pilot.read_model_request(root, attempt_id, request["stage_instance_id"], request["artifact_digest"])
            result.update(next_expected_artifact=request["stage_instance_id"] + " response", pending_request_path=str(root / path))
        except (OSError, KeyError, TypeError, ValueError):
            unreadable(path)
    if result["evidence_errors"]:
        result.update(stop_reason="EVIDENCE_UNVERIFIED", next_expected_artifact="validated inputs or new scan")
    elif attempt["state"] == "WAITING_FOR_INPUT" and result["stop_reason"] is None:
        result["stop_detail"] = "WAITING_FOR_INPUT has no persisted cause; consult the originating request."
    return result


def _read_skillsrc_binding(path: Path) -> tuple[dict[str, Any], str]:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    data = path.read_bytes()
    return parse_skillsrc_bytes(data, project=path.parent), _digest_bytes(data)


def _local_input(raw: str, flag: str) -> None:
    """A path of a scan flag on a share or a device is refused by its text, before anything resolves or opens it."""
    from tools.output_guard import local_path_text

    if not local_path_text(str(raw)):
        raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is not a local path: {raw}",
                       fix=f"укажите {flag} путём внутри проекта, не UNC и не путём устройства")


def _input_files(project: Path, paths: Sequence[str] | None, flag: str, *, outside_fix: str | None = None) -> list[tuple[str, bytes]]:
    """The files a scan flag names (``--docs``, ``--reference``, ``--cases``): inside the project, no reparse point, each
    path once. A path outside the project is the refusal ``SNAPSHOT_DOCS`` with *outside_fix*."""
    files: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for raw in paths or []:
        _local_input(raw, flag)
        candidate = Path(raw)
        try:
            confined = _confined(project, candidate if candidate.is_absolute() else project / candidate)
        except ValueError:
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is outside the project: {raw}",
                           fix=outside_fix or f"укажите {flag} путём внутри проекта") from None
        if _is_reparse(confined) or not confined.is_file():
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is missing or a reparse point: {raw}")
        rel = confined.relative_to(project.resolve()).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        files.append((rel, confined.read_bytes()))
    return files


def _is_zephyr_csv(data: bytes) -> bool:
    """The file is a Zephyr Scale test-case CSV — the shared reader knows its columns (rows may still be wrong)."""
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    try:
        parse_zephyr_csv(data)
    except ZephyrCsvError as error:
        return error.code == "ZEPHYR_CSV_ROWS"
    return True


def _docs_entries(project: Path, docs: list[str] | None) -> list[dict[str, str]]:
    from tools.doc_sections import resolve_spec

    entries: list[dict[str, str]] = []
    sections: dict[str, str | None] = {}  # --docs path#Section — one section of the file, its heading by path
    files: list[tuple[str, bytes]] = []
    for raw in docs or []:
        _local_input(raw, "--docs")  # before resolve_spec looks for a file named with its #
        path, heading = resolve_spec(project, raw)
        for rel, data in _input_files(project, [path], "--docs"):
            if rel in sections:
                if sections[rel] != heading:
                    raise HostStop("TASK_DOC_SECTION_CONFLICT", f"{rel} указан дважды с разными разделами",
                                   fix="один раздел на файл: --docs путь#Раздел один раз (или весь файл без #)")
                continue
            sections[rel] = heading
            files.append((rel, data))
    for rel, data in files:
        if _is_zephyr_csv(data):  # an export of cases is not a requirements document
            raise HostStop("DOCS_ZEPHYR_CSV", f"{rel} — выгрузка тест-кейсов Zephyr, а не документ требований",
                           fix=f"подайте её через scan --reference {printed_or(rel, '<файл>')} (эталон для сравнения), а в --docs — документ требований")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"DOCS_ENCODING: docs file is not UTF-8: {rel}; fix: сохрани документ в UTF-8 и повтори scan") from error
        entry = {"path": rel, "sha256": _digest_bytes(data), "content": text}
        if sections[rel] is not None:
            entry.update(_section_entry(rel, text, str(sections[rel])))
            entry["sha256"] = _digest_bytes(entry["content"].encode("utf-8"))  # what the code's inventory is built from
        entries.append(entry)
    entries.sort(key=lambda row: row["path"])
    return entries


def _section_entry(rel: str, text: str, heading: str) -> dict[str, str]:
    """The entry of ``path#Section``: ``content`` is the section at its source line numbers (the rest of the lines
    empty), ``view`` the same with the note the model reads.  The digest is that of ``content`` and the requirement digest also
    binds the heading (``_docs_projection``); a change of the file is drift of the inventory as before."""
    from tools.doc_sections import SectionMissing, find_section, view

    try:
        section = find_section(text, heading, rel)
    except SectionMissing as error:
        message, _, fix = str(error).partition("; fix: ")
        raise HostStop("TASK_DOC_SECTION_MISSING", message.removeprefix("TASK_DOC_SECTION_MISSING: "), fix=fix or None) from None
    return {"content": view(text, section, rel, note=False), "view": view(text, section, rel), "section": section.heading,
            "lines": f"{section.first}-{section.last}"}


def _reference_cases(project: Path, reference: str | None) -> tuple[str, bytes, list[dict[str, Any]]] | None:
    """``scan --reference <csv>`` read by the shared Zephyr reader; a reader refusal is the scan's refusal."""
    if not reference:
        return None
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    from tools.zephyr_csv import require_human_csv

    rel, data = _input_files(project, [reference], "--reference")[0]
    try:
        cases = parse_zephyr_csv(data)
    except ZephyrCsvError as error:
        raise HostStop(error.code, f"{rel}: {error}", fix="--reference — экспорт тест-кейсов Zephyr Scale в CSV") from error
    try:
        require_human_csv(cases)  # the v4 Test Data holds the machine model, not the human data of a case
    except ZephyrCsvError as error:
        raise HostStop(error.code, f"{rel}: {error}", fix="выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — "
                                                        "в Test Data должен быть текст человека") from error
    return rel, data, cases


def _docs_projection(snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"path": row["path"], "sha256": row["sha256"], **({"section": row["section"]} if "section" in row else {})} for row in snapshot]


def _execution_skipped(project: Path, args: Any) -> dict[str, str] | None:
    """``{"reason", "fix"}`` when the module of a local-pilot request cannot be built and tested by the package."""
    from tools.init_skillsrc import InitError, NeedsInput
    from tools.ready import execution_facts, proposed_modules, select_module
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

    try:
        path = project / ".skillsrc"
        modules = normalize_skillsrc(load_skillsrc(path))["modules"] if path.is_file() else proposed_modules(project, getattr(args, "module", None))
    except (NeedsInput, InitError, SkillsrcError, OSError, ValueError, KeyError, TypeError):
        return None  # .skillsrc and discovery problems are reported by the scan itself
    module, _problem = select_module(modules, getattr(args, "module", None))
    if module is None:
        return None
    if (module.get("stack") or {}).get("language") == "documents":
        # a project of documents only has nothing to build — local-pilot is refused, not lowered
        raise HostStop("NO_BUILD_MODULE", "в проекте только документы, сборки и тестов нет: local-pilot-v1 недоступен",
                       "повтори scan с --profile cases-only-v1 — кейсы по документам без автотестов")
    if (module.get("paths") or {}).get("context") and not module.get("service_url_env"):
        # the tests call an application the package does not start; without the stand's address — cases only
        return {"reason": "модуль только с тестами: приложение поднимает человек, адрес стенда не объявлен",
                "fix": "укажите в .skillsrc у модуля service_url_env: <ИМЯ> — переменную окружения с адресом поднятого стенда"}
    facts = execution_facts(project, module)
    if not facts["ready"]:
        return {"reason": str(facts["reason"]), "fix": str(facts["fix"])}
    # the same baseline blockers the freeze would meet, known before the run is created.
    blockers = _baseline_blockers(project, module)
    if not blockers:
        return None
    return {"reason": "; ".join(f"{row['file']}: {row['reason']}" for row in blockers), "fix": "; ".join(row["fix"] for row in blockers)}


def _baseline_blockers(project: Path, module: Mapping[str, Any]) -> list[dict[str, str]]:
    from tools.project_inventory import InventoryError, baseline_blockers, build_inventory

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    try:
        inventory = build_inventory(root, module_root, skill_pack_root=_scan_skill_pack_root(root), generated_roots=(root / ".pilot-runs",))
    except (InventoryError, OSError, ValueError):
        return []  # the scan itself reports an inventory problem
    return baseline_blockers(root, inventory, module, "local-pilot-v1")


def _scan_disposition_policy(project: Path, args: Any) -> str | None:
    """``cleanup`` when this scan asks for the old FAIL cleaning; the flag beats `.skillsrc`, read only here."""
    flag = getattr(args, "disposition_policy", None)
    if flag is not None:
        return "cleanup" if flag == "cleanup" else None
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc

    try:
        document = load_skillsrc(project / ".skillsrc")  # .skillsrc is a JSON object
    except (OSError, SkillsrcError):
        return None
    return "cleanup" if document.get("disposition_policy") == "cleanup" else None


def _init_skillsrc_run(project: Path, args: Any, scan_request: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], dict[str, Any]]:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.pilot_state import create_lowered_run, create_run
    from tools.skillsrc_manifest import skillsrc_review_self as _skillsrc_review_self

    if (project / ".skillsrc").is_file():
        _read_skillsrc_binding(project / ".skillsrc")  # an input that is refused is refused before the run exists — no orphan run without MODULE_SELECTED
    profile = getattr(args, "profile", "local-pilot-v1")
    skipped = _execution_skipped(project, args) if profile == "local-pilot-v1" else None
    if skipped is not None:
        # no build facts — the cases still run; the run says it is cases-only and what to fix for execution.
        profile = "cases-only-v1"
        args.profile = profile
    authorization = {"request_id": f"scan-{uuid.uuid4().hex[:12]}", "execution_requested": profile == "local-pilot-v1"}
    if getattr(args, "accept_self_review", False) or _skillsrc_review_self(project):  # the flag, or .skillsrc review.self: true
        authorization["accept_self_review"] = True
    if _scan_disposition_policy(project, args) == "cleanup":
        authorization["disposition_policy"] = "cleanup"
    if scan_request is not None:
        from tools.pilot_state import create_scan_run

        run = create_scan_run(project, profile, authorization, scan_request, skipped)
    else:
        run = create_run(project, profile, authorization) if skipped is None else create_lowered_run(project, authorization, skipped)
    if (project / ".skillsrc").is_file():
        receipt = {"status": "unchanged"}
    else:
        # ``--target`` is C-lite context scope, not an instruction to mutate .skillsrc.
        receipt = ensure_skillsrc(project, {}, write=True, selected_module=getattr(args, "module", None))
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

    from tools.skillsrc_manifest import SkillsrcError

    normalized = normalize_skillsrc(skillsrc_document)
    try:
        module = select_module(normalized, module_id)
    except SkillsrcError as error:
        if error.code != "module_unknown":
            raise
        # --module takes the id of .skillsrc, or the folder of that module (the id of a plain folder is its path)
        known = ", ".join(f"{row['id']} (root {row.get('root')})" for row in normalized["modules"])
        ids = [str(row["id"]) for row in normalized["modules"]]
        raise HostStop("MODULE_UNKNOWN", f"модуля {module_id} нет в .skillsrc: --module — id модуля или путь его папки; модули: {known}",
                       f"--module {printed_or(ids[0], '<id>')}" if len(ids) == 1 else "--module <id>: " + " | ".join(ids)) from error
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


def _filtered_context(inventory: Mapping[str, Any], project: Path, module_root: Path, selected_ids: list[str],
                      task_ids: list[str], budget: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Assets, user excludes and large non-sources stay out of the model context, always listed (scan and ready)."""
    return context_filter(inventory, project, selected_ids, byte_budget=budget, excludes=skillsrc_exclude(project, module_root), task_ids=task_ids,
                          includes=skillsrc_include(project, module_root))


def context_preview(inventory: Mapping[str, Any], project: Path, module_root: Path, docs: list[str] | None,
                    target: str | None = None) -> tuple[list[str], list[dict[str, Any]], int]:
    """What the scan would hand the model, without a run: ``(ids, skipped, batch budget)``; only reads."""
    task_ids, other_task_ids, _summary, _warnings = _task_document_scope(inventory, project, docs)
    selected = [opaque for opaque in _context_scope_ids(inventory, project, module_root, target) if opaque not in other_task_ids]
    selected += [opaque for opaque in task_ids if opaque not in selected]
    sectioned = _sectioned_ids(inventory, project, docs)
    selected = [opaque for opaque in selected if opaque not in sectioned]
    task_ids = [opaque for opaque in task_ids if opaque not in sectioned]
    budget = _scan_context_budget(inventory, project)
    kept, skipped = _filtered_context(inventory, project, module_root, selected, task_ids, budget)
    return kept, skipped, budget


def _sectioned_ids(inventory: Mapping[str, Any], project: Path, docs: list[str] | None) -> set[str]:
    """The inventory ids of the documents given as ``path#Section``.  The model reads their section view, not the file:
    they stay out of the context receipts (else the whole file would reach the model once more)."""
    paths = {row["path"] for row in _docs_entries(project, docs) if "section" in row}
    return {str(item["opaque_id"]) for item in inventory.get("files", []) if isinstance(item, Mapping) and str(item["project_path"]) in paths}


def _write_section_views(project: Path, run_root: Path, docs: list[str] | None) -> list[dict[str, str]]:
    """``<run>/doc-sections/<name>.md`` — the view of each ``path#Section`` (the lines of the section at their source
    numbers); the rows tell the agent which file the model reads instead of the document."""
    from tools.confined_output import atomic_write_confined_bytes_at_root
    from tools.doc_sections import view_name

    rows = []
    for entry in _docs_entries(project, docs):
        if "section" not in entry:
            continue
        name = view_name(entry["path"], entry["section"])
        target = run_root / "doc-sections" / name
        atomic_write_confined_bytes_at_root(project, run_root, target, entry["view"].encode("utf-8"))
        rows.append({"path": entry["path"], "section": entry["section"], "lines": entry["lines"],
                     "view": target.relative_to(project).as_posix()})
    return rows


def _scan_context_budget(inventory: Mapping[str, Any], project: Path | None = None) -> int:
    """The byte budget of one context batch: ``.skillsrc limits.context_batch_bytes``, else 256 KiB."""
    del inventory
    path = None if project is None else project / ".skillsrc"
    if path is not None and path.is_file():
        from tools.skillsrc_manifest import load_skillsrc

        value = (load_skillsrc(path).get("limits") or {}).get("context_batch_bytes")
        from tools.step_size import whole_limit

        value = whole_limit(value)
        if value is not None and value > 0:
            return value
    return _SCAN_CONTEXT_BYTE_BUDGET


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
    # a task document outside the module is a proved dependency file of the baseline, not a parent build manifest
    outside_docs = {row["path"] for row in _docs_entries(project, getattr(args, "docs", None))} if prefix else set()
    dependency_ids = [
        str(item["opaque_id"]) for item in files
        if isinstance(item, Mapping) and item.get("origin") != "context" and item.get("project_path") in outside_docs
        and not str(item.get("project_path", "")).startswith(prefix)
    ]
    parent_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and item.get("origin") != "context"
        and prefix
        and not str(item.get("project_path", "")).startswith(prefix)
        and item.get("opaque_id") not in dependency_ids
    ]
    context_ids = [str(item["opaque_id"]) for item in files if isinstance(item, Mapping) and item.get("origin") == "context"]
    execution_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and item.get("opaque_id") not in parent_ids
        and item.get("opaque_id") not in dependency_ids
        and item.get("opaque_id") not in context_ids
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
            interpreter = str(test["interpreter"]) if isinstance(test.get("interpreter"), str) else None
            wrapper = str(test["wrapper"]) if isinstance(test.get("wrapper"), str) else None
            runtime_facts.update(interpreter_path=interpreter, wrapper_path=wrapper,
                interpreter_identity=runtime_identity(module_root, interpreter) if interpreter is not None else None,
                wrapper_identity=runtime_identity(module_root, wrapper) if wrapper is not None else None)
    requirement = {
        "module_id": module.get("id"),
        "selected_target": getattr(args, "target", None),
        "docs": _docs_projection(_docs_entries(project, getattr(args, "docs", None))),
        # the CSV of --cases is the requirement document of an import; its bytes bind the requirement digest
        **({"cases": [{"path": rel, "sha256": _digest_bytes(data)} for rel, data in _input_files(project, [args.cases], "--cases")]}
           if getattr(args, "cases", None) else {}),
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
        proved_dependency_file_ids=dependency_ids,
        context_file_ids=context_ids,
        policy_profile=policy_profile,
        **runtime_facts,
    )


def _scan_request(args: Any) -> dict[str, Any]:
    """The scan flags as given (the requested profile before any lowering) — kept in the run manifest."""
    cases = getattr(args, "cases", None)
    return {
        "module": getattr(args, "module", None), "docs": [str(doc) for doc in (getattr(args, "docs", None) or [])],
        "target": getattr(args, "target", None), "profile": getattr(args, "profile", "local-pilot-v1"),
        "accept_self_review": bool(getattr(args, "accept_self_review", False)),
        "disposition_policy": getattr(args, "disposition_policy", None),
        **({"no_pause": True} if getattr(args, "no_pause", False) else {}),  # a run without the pause after the marker
        **({"reference": str(args.reference)} if getattr(args, "reference", None) else {}),
        **({"cases": str(cases)} if cases else {}),
    }


def relative_to_project(project: Path, raw: Any) -> str:
    """A path as the person typed it (relative or absolute), as the path inside the project with posix separators — the form the context and the inventory know."""
    path = Path(str(raw))
    try:
        return (path if path.is_absolute() else project / path).resolve().relative_to(project).as_posix()
    except (OSError, ValueError):
        return str(raw).replace("\\", "/")


def _request_key(project: Path, request: Mapping[str, Any]) -> tuple[Any, ...]:
    """The same request: the module (resolved by .skillsrc when not given), the --docs paths, the requested profile, the
    --reference and the --cases CSV; a request without --cases (and every journal of an earlier version) has ``None``."""
    module = request.get("module")
    if module is None:
        try:
            from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

            modules = normalize_skillsrc(load_skillsrc(project / ".skillsrc"))["modules"]
            module = modules[0].get("id") if len(modules) == 1 else None
        except Exception:  # an unreadable .skillsrc: the scan itself will say so
            module = None
    docs = [relative_to_project(project, raw) for raw in request.get("docs") or []]
    cases = relative_to_project(project, request["cases"]) if request.get("cases") else None
    return module, tuple(sorted(docs)), request.get("profile"), request.get("reference"), cases


def _open_run_of(project: Path, request: Mapping[str, Any], skip: Sequence[Path] = ()) -> tuple[Path | None, list[str]]:
    """An unfinished run of the same request — its scan selected the context (CONTEXT_SELECTED) and its last
    attempt has no ATTEMPT_TERMINAL.  A run whose scan failed earlier never blocks; runs without ``scan_request``
    (created by an earlier version) are never compared.

    ``(the live run that blocks, the abandoned ones)``.  An unfinished run whose baseline check finds
    drift or fails is abandoned: it cannot go on, so it never blocks; the scan names it and leaves it as it is."""
    from tools import pilot_state as pilot

    runs = project / ".pilot-runs"
    abandoned: list[str] = []
    if not runs.is_dir():
        return None, abandoned
    key = _request_key(project, request)
    for root in sorted(path for path in runs.iterdir() if path.is_dir()):
        if root in skip:  # a cut run the scan could not finish - the caller named it
            continue
        try:
            stored = pilot.read_run(root)["manifest"].get("scan_request")
            if not isinstance(stored, Mapping) or _request_key(project, stored) != key:
                continue
            state = pilot.derive_state(root)
            if not state["attempts"]:
                continue
            attempt = state["attempts"][-1]
            selected = any(event["event_type"] == "CONTEXT_SELECTED" and event.get("attempt_id") == attempt["attempt_id"]
                           for event in state["events"])
            if not selected and attempt.get("state") != "TERMINAL":  # a scan cut before its first context batch
                abandoned.append(f"{root.name} (scan оборван до первой пачки контекста)")
            if selected and attempt.get("state") != "TERMINAL":
                why = _left_mark(root) or _abandoned(project, root, attempt)
                if why is None:
                    return root, abandoned
                abandoned.append(f"{root.name} ({why})")
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None, abandoned


def _abandoned(project: Path, root: Path, attempt: Mapping[str, Any]) -> str | None:
    """Why an unfinished run cannot go on — the same baseline check as materialize/ingest and status; None when it can."""
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    try:
        result = _baseline_check(project, root, attempt, module_root)
    except Exception:  # an unreadable baseline: the run cannot resume
        return "сверка baseline не прошла"
    if result.get("status") == "UNCHANGED":
        return None
    changed = result.get("changed") or []
    return f"дрейф: {len(changed)} файл(ов)" if changed else "сверка baseline не прошла"


def _run_in_progress(project: Path, root: Path, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from tools.finalize_attempt import finish_closes
    from tools.pilot_state import derive_state

    python = f"{printed(sys.executable)} -m tools.run_pipeline"
    try:
        facts = _durable_status(project, root, stage="status")
        where = {key: facts[key] for key in ("state", "waiting_for", "next")}
    except (HostStop, OSError, KeyError, TypeError, ValueError):
        where = {}
    try:
        closes = finish_closes(root, str(derive_state(root)["attempts"][-1]["attempt_id"]))
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        closes = False
    # finish only where it closes the attempt (rework, a refused automation), never as a dead end
    finish = f"закрыть его без продолжения: {python} finish --project {printed(project)} --run {root.name}; " if closes else ""
    return {
        "status": "error", "stage": "scan", "reason": "SCAN_RUN_IN_PROGRESS", "run_id": root.name, "run_root": str(root),
        **where,
        "message": "для этого проекта, документа и профиля уже есть незавершённый прогон; новый не создан",
        "fix": (f"продолжите его: {python} status --project {printed(project)} --run {root.name}; "
                f"{finish}нужен ещё один прогон рядом или этот не закрыть — "
                + (_scan_command(project, request, extra=("--new-run",)) if request is not None else "повторите этот scan с флагом --new-run")),
    }


def _scan_command(project: Path, request: Mapping[str, Any], *, extra: Sequence[str] = ()) -> str:
    """The scan of the same request, its flags as given — printed for the human; the code never runs it.  Every value through
    ``printed`` (``COMMAND_NOT_PRINTABLE`` for one that cmd.exe, PowerShell or sh would read otherwise)."""
    parts = [f"{printed(sys.executable)} -m tools.run_pipeline scan --project {printed(project)}"]
    if request.get("module"):
        parts.append(f"--module {printed(request['module'])}")
    parts += [f"--docs {printed(doc)}" for doc in request.get("docs") or []]
    if request.get("target"):
        parts.append(f"--target {printed(request['target'])}")
    parts.append(f"--profile {command_token(request.get('profile') or 'local-pilot-v1')}")
    if request.get("accept_self_review"):
        parts.append("--accept-self-review")
    if request.get("no_pause"):
        parts.append("--no-pause")
    if request.get("disposition_policy"):
        parts.append(f"--disposition-policy {command_token(request['disposition_policy'])}")
    if request.get("reference"):
        parts.append(f"--reference {printed(request['reference'])}")
    if request.get("cases"):  # the import of the same CSV, never a run of cases from code
        parts.append(f"--cases {printed(request['cases'])}")
    return " ".join([*parts, *extra])


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


def _baseline_check(project: Path, root: Path, attempt: Mapping[str, Any], module_root: Path, request: Any = None) -> Mapping[str, Any]:
    """The one baseline check of materialize/ingest (with the request) and status (from the baseline alone); only reads."""
    from tools.project_inventory import (
        baseline_external_input_paths,
        build_inventory,
        runtime_identity,
        validate_execution_baseline,
    )
    from tools.pilot_state import frozen_baseline, read_attempt_receipt

    baseline = frozen_baseline(root, attempt)
    try:
        durable_delta = read_attempt_receipt(root, str(attempt["attempt_id"]), "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
    except (KeyError, OSError, TypeError, ValueError):
        if request is not None:
            raise
        durable_delta = {"files": []}
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
    if request is not None:
        # materialize/ingest: always against local-pilot-v1 with the runtime identity — never the baseline's own profile
        policy, adapter_id, build_profile, parameters = "local-pilot-v1", request.adapter_id, request.build_profile, dict(request.typed_parameters)
        executable = request.executable if request.adapter_id == "maven:selected-symbols-v1" else None
    else:
        # status: the baseline's own profile, so that a cases-only attempt also names its changed files
        policy, adapter_id, build_profile = str(baseline.get("policy_profile")), baseline.get("adapter_id"), baseline.get("build_profile")
        parameters = baseline.get("adapter_parameters") if policy == "local-pilot-v1" else None
        executable = baseline.get("executable_path") if adapter_id == "maven:selected-symbols-v1" else None
    runtime = executable or str(baseline.get("interpreter_path") or baseline.get("wrapper_path"))
    vanished = None
    try:
        identity = runtime_identity(module_root, runtime, adapter_id=baseline.get("adapter_id")) if policy == "local-pilot-v1" else None
    except (OSError, ValueError):
        if request is not None:
            raise
        # status names the vanished runtime and still compares the files against the scan
        vanished = (runtime if adapter_id == "maven:selected-symbols-v1" else module_prefix + runtime) + " (runtime недоступен)"
        identity_key = ("interpreter_identity" if baseline.get("adapter_id") == "pytest:selected-symbols-v1"
                        else "executable_identity" if baseline.get("adapter_id") == "maven:selected-symbols-v1" else "wrapper_identity")
        identity = baseline.get(identity_key)
    result = validate_execution_baseline(
        baseline, current, requirements=baseline["requirements"], adapter_id=adapter_id, build_profile=build_profile,
        adapter_parameters=parameters, runtime_identity_value=identity, policy_profile=policy, project_root=project,
    )
    if vanished is None:
        return result
    return {**result, "status": "DECLARED_INPUT_DRIFT", "changed": [vanished, *(result.get("changed") or [])]}


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
    from tools.cases_import import CasesImportError, read_cases

    cases = getattr(args, "cases", None)
    if not cases:
        return None
    try:
        [(rel, data)] = _input_files(project, [cases], "--cases",
                                     outside_fix="CSV кейсов должен лежать внутри проекта: укажите --cases путём от корня проекта")
        from tools.cases_import import password_cells, secret_cell
        from tools.project_inventory import _secret_rule

        cases_read = read_cases(data)
        passwords = password_cells(cases_read)  # a plain password in a cell, by row and column, never the value
        if passwords:
            raise CasesImportError("IMPORT_CSV_SECRET", f"{rel}: " + "; ".join(passwords[:5]))
        secret = _secret_rule(project / rel, data, rel)  # the rule the inventory would leave the CSV out by
        if secret is not None:
            cell = secret_cell(cases_read, secret[0])  # and the cell it is in, as for a password
            raise CasesImportError("IMPORT_CSV_SECRET", f"{rel}: " + (f"{cell}: " if cell else "")
                                   + f"правило секретов {secret[0]} ({secret[1]}) — файл не войдёт в опись")
    except HostStop as stop:
        _print({"status": "error", "stage": "scan", "reason": stop.code, "detail": str(stop), "fix": stop.fix})
        return 2
    except CasesImportError as error:
        _print({"status": "error", "stage": "scan", "reason": error.code, "detail": str(error), "fix": error.fix})
        return 2
    args.cases = rel
    return None


def _scan_profile(args: Any) -> None:
    """No ``--profile`` is what the package can do; ``local-pilot-v1`` asked of a package without its execution files
    is refused before any run exists."""
    requested = getattr(args, "profile", None)
    if requested is None:
        args.profile = execution_gate.default_profile()
    elif requested == execution_gate.LOCAL_PILOT and not execution_gate.execution_available():
        raise HostStop("PROFILE_NOT_IN_PACKAGE", "в этой поставке нет файлов исполнения (tools/exec_*.py): local-pilot-v1 она не предлагает")


def _module_warnings(module: Mapping[str, Any], given: str | None) -> list[str]:
    """The warnings of the selected module: ``--module`` in the old hex form, a derived level of the autotests."""
    from tools.module_ids import legacy_warning
    from tools.skillsrc_manifest import level_derived_note

    note = level_derived_note(module) if execution_gate.execution_available() else None  # no execution in the delivery: no level to speak of
    found = [legacy_warning(module, given), f"TEST_LEVEL_DERIVED: {note[0]}; fix: {note[1]}" if note else None]
    return [item for item in found if item]


def _skillsrc_preflight(project: Path) -> None:
    """``.skillsrc`` is read before a run is created — a directory, a link, an unreadable or a foreign file is a refusal
    with its fix and no run behind it (the run_id line of a scan means a run exists)."""
    from tools.skillsrc_manifest import load_skillsrc, skillsrc_file_problem

    path = project / ".skillsrc"
    problem = skillsrc_file_problem(path)
    if problem is not None:
        raise HostStop("SKILLSRC_UNREADABLE", problem)
    if path.is_file():
        load_skillsrc(path)


_SCHEMA_CAUSE = re.compile(r"schema validation failed: [a-z0-9.-]+\.schema\.json")


def _inventory_invalid_detail(error: BaseException) -> str:
    """The cause of INVENTORY_INVALID — the kind of error and the schema or the module it names, never a text of the project."""
    from tools.pilot_state import ModulePathError

    if isinstance(error, InventoryError):
        return str(error)
    if isinstance(error, OSError):
        return "scan input is unreadable"
    if isinstance(error, ModulePathError):
        return f"scan input or receipt is invalid: {error}"
    if isinstance(error, ValueError) and _SCHEMA_CAUSE.fullmatch(str(error)):
        return f"scan input or receipt is invalid: {error}"
    return "scan input or receipt is invalid"


def cmd_scan(args: Any) -> int:
    """Scan, then ``run_id: <id>`` as the last line of the output."""
    _PRINTED.clear()
    try:
        _scan_profile(args)
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
        _docs_entries(Path(args.project).resolve(), getattr(args, "docs", None))
        _reference_cases(Path(args.project).resolve(), getattr(args, "reference", None))
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
            outcome = _resume_scan(project, args, request, open_root, [*abandoned, *cut_runs])
            if isinstance(outcome, int):
                return outcome
            skipped_runs.append(open_root)
            cut_runs.append(f"{open_root.name} ({outcome})")
    run, receipt = _init_skillsrc_run(project, args, request)
    run_root = Path(str(run["run_root"]))
    stopped = _skillsrc_receipt_exit(project, run_root, receipt)
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
            frozen_inventory, project, getattr(args, "docs", None))
        context_warnings = [*context_warnings, *_module_warnings(module, getattr(args, "module", None))]
        attempt = create_attempt(
            run_root,
            {"project": str(project), "module": module_path, "policy_profile": run["manifest"]["policy_profile"]},
            frozen["baseline"],
        )
        plan = _scan_context_plan(project, args, run_root, frozen_inventory, module_root, (task_ids, other_task_ids, context_summary))
        _publish_context(run_root, str(attempt["attempt_id"]), plan)
        batches, context_skipped = plan["batches"], plan["context_skipped"]
    except ContextStop as stop:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason=stop.code, detail=stop.detail, fix=stop.fix, scope=scope))
        return 2
    except ContextSelectionError:
        _print(_durable_status(project, run_root, stage="scan", status="error", reason="CONTEXT_SELECTION_INVALID", scope=scope))
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


def _scan_context_plan(project: Path, args: Any, run_root: Path, frozen_inventory: Mapping[str, Any], module_root: Path,
                       task_info: tuple[list[str], set[str], dict[str, Any]]) -> dict[str, Any]:
    """The context batches of the scan from the frozen inventory and the request (one function for a scan and for the repeat
    that finishes it); ``task_info`` is the result of ``_task_document_scope`` — its summary is filled in place.  Writes only the
    views of the document sections of the run."""
    task_ids, other_task_ids, context_summary = task_info
    selected_ids = _context_scope_ids(frozen_inventory, project, module_root, getattr(args, "target", None))
    # the task document of --docs always goes to the model; the other tasks of to_do do not.
    selected_ids = [opaque for opaque in selected_ids if opaque not in other_task_ids]
    selected_ids += [opaque for opaque in task_ids if opaque not in selected_ids]
    sectioned = _sectioned_ids(frozen_inventory, project, getattr(args, "docs", None))  # path#Section is no context file
    selected_ids = [opaque for opaque in selected_ids if opaque not in sectioned]
    task_ids = [opaque for opaque in task_ids if opaque not in sectioned]
    context_summary["document_sections"] = _write_section_views(project, run_root, getattr(args, "docs", None))
    by_id = {str(item["opaque_id"]): str(item["project_path"]) for item in frozen_inventory["files"]}
    reference = _reference_cases(project, getattr(args, "reference", None))
    if reference is not None:  # the reference CSV is never context of a model
        selected_ids = [opaque for opaque in selected_ids if by_id.get(opaque) != reference[0]]
    context_summary["code_files"] = sum(1 for opaque in selected_ids if opaque not in task_ids and "to_do" not in by_id[opaque].split("/"))
    budget = _scan_context_budget(frozen_inventory, project)
    selected_ids, skipped = _filtered_context(frozen_inventory, project, module_root, selected_ids, task_ids, budget)
    context_skipped = skipped_summary(skipped)
    batches = select_context_batches(
        frozen_inventory,
        project,
        selected_ids,
        byte_budget=budget,
        skipped=skipped,
    )
    if reference is not None:
        context_summary["reference_cases"] = len(reference[2])
    if not batches:  # the closed manifest .skillsrc is always in the context, so this is not an honest run: nothing to cut or finish
        raise ContextSelectionError("the scan selected no context batch")
    return {"batches": batches, "context_skipped": context_skipped, "reference": reference}


def _publish_context(run_root: Path, attempt_id: str, plan: Mapping[str, Any]) -> None:
    """The receipts of the plan the attempt has no event for, in batch order, then the reference receipt (all idempotent)."""
    from tools import pilot_state as pilot

    have = {str(event["artifact_digest"]) for event in pilot.derive_state(run_root)["events"]
            if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"}
    for batch in plan["batches"]:
        if str(batch["receipt"]["digest"]) not in have:
            pilot.publish_context_selection(run_root, attempt_id, batch["receipt"])
    reference = plan["reference"]
    if reference is not None:  # a receipt for the review hint; never context or generator input
        pilot.publish_reference_cases(run_root, attempt_id, *reference)


def _scan_payload(project: Path, run_root: Path, args: Any, module: Mapping[str, Any], scope: Mapping[str, Any],
                  frozen_inventory: Mapping[str, Any], exclusion_receipt: Mapping[str, Any], baseline_digest: str, attempt_id: str,
                  plan: Mapping[str, Any], context_summary: Mapping[str, Any], context_warnings: Sequence[str],
                  abandoned: Sequence[str], **extra: Any) -> dict[str, Any]:
    """The answer of a scan that made (or finished) the run."""
    context_skipped = plan["context_skipped"]
    return _durable_status(
        project,
        run_root,
        stage="scan",
        status="ok",
        module_id=module.get("id"),
        scope=scope,
        selected_target=getattr(args, "target", None),
        inventory_digest=frozen_inventory["digest"],
        exclusion_digest=exclusion_receipt["digest"],
        baseline_digest=baseline_digest,
        inventory_file_count=len(frozen_inventory["files"]),
        exclusion_count=len(exclusion_receipt["exclusions"]),
        context_batch_count=len(plan["batches"]),
        context_summary=context_summary,
        **({"context_skipped": context_skipped} if context_skipped["files"] else {}),
        **({"warnings": list(context_warnings)} if context_warnings else {}),
        **_skillsrc_defaults(project, module),
        **({"abandoned_runs": list(abandoned)} if abandoned else {}),
        **extra,
        reuse=_marker_reuse(run_root, attempt_id),
    )


def _scan_cut(run_root: Path, request: Mapping[str, Any] | None = None) -> str | None:
    """Why the scan of the run stopped before it was done - the context batches written against the count the
    receipts carry, or the reference receipt of ``--reference`` - else None.  Only reads."""
    from tools import pilot_state as pilot

    try:
        state = pilot.derive_state(run_root)
        attempt_id = str(state["attempts"][-1]["attempt_id"])
        if state["attempts"][-1].get("state") == "TERMINAL":
            return None
        unfinished = pilot.context_incomplete(run_root, attempt_id, state)
        if unfinished is not None:
            return f"{unfinished[0]} из {unfinished[1]} пачек контекста"
        if isinstance(request, Mapping) and request.get("reference") and pilot.read_reference_cases(run_root, attempt_id) is None:
            return "нет квитанции --reference"
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return None
    return None


def _resume_scan(project: Path, args: Any, request: Mapping[str, Any], run_root: Path, abandoned: Sequence[str]) -> int | str:
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
        task_ids, other_task_ids, context_summary, context_warnings = _task_document_scope(frozen_inventory, project, getattr(args, "docs", None))
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


def _marker_reuse(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """The scan suggests an earlier accepted run whose context marker has the very same inputs; the agent decides."""
    from tools.pilot_state import marker_reuse_suggestion

    try:
        return marker_reuse_suggestion(run_root, attempt_id)
    except (ValueError, OSError, KeyError, TypeError) as error:  # a suggestion never stops the scan
        return {"run_id": None, "reason": f"поиск не удался: {error}"}


def _skillsrc_defaults(project: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    """A test framework the build files of the module do not show was taken by default — say so."""
    from tools.discover_project import module_test_evidenced

    framework = (module.get("test") or {}).get("framework") if isinstance(module.get("test"), Mapping) else None
    if framework is None or module_test_evidenced(project, module):
        return {}
    return {"skillsrc_defaults": [
        f"{module.get('id')}: test.framework={framework} указан в .skillsrc, а в сборке модуля нет признака тестового "
        f"фреймворка; проверьте, что тесты модуля на {framework}, иначе исправьте .skillsrc"]}


TASK_DOC_FIX = ("документ задачи должен лежать внутри проекта, вне исключённых папок (.git, build, node_modules и т. п.), "
                "быть текстом без секретов и не лежать в самом пакете: перенесите его или уберите названную причину")


def _under_to_do(project_path: str) -> bool:
    return "to_do" in project_path.split("/")[:-1]


def _outside_task_files(project: Path, module_root: Path, docs: list[str] | None) -> list[Path]:
    """The task documents of --docs that lie in the project outside the module (the root of a multi-module project):
    they join the inventory as parent files, bound by its digest, and are read as documents only.  One the rules of the
    inventory leave out stops the scan with its rule."""
    from tools.project_inventory import outside_task_documents

    files, problems = outside_task_documents(project, module_root, [row["path"] for row in _docs_entries(project, docs)],
                                             skill_pack_root=_scan_skill_pack_root(project))
    for path, problem in problems.items():
        raise HostStop("TASK_DOC_OUTSIDE_CONTEXT", f"документ задачи {path} не может войти в inventory: {problem}", TASK_DOC_FIX)
    return files


def _task_document_scope(inventory: Mapping[str, Any], project: Path, docs: list[str] | None) -> tuple[list[str], set[str], dict[str, Any], list[str]]:
    """``(ids of the task documents, ids of other tasks' files in to_do, summary, warnings)``.

    A document of --docs must be a file of the module's inventory (its bytes are bound by the inventory digest);
    otherwise the scan stops with TASK_DOC_OUTSIDE_CONTEXT — cases without their requirements are no cases.
    """
    files = [item for item in inventory.get("files", []) if isinstance(item, Mapping)]
    by_path = {str(item["project_path"]): item for item in files}
    to_do = [item for item in files if _under_to_do(str(item["project_path"]))]
    if not docs:
        warnings = []
        if to_do:
            size = sum(int(item.get("size") or 0) for item in to_do)
            warnings.append(f"модели уйдут все документы из to_do — {len(to_do)} файл(ов), {size} байт; укажите --docs документа задачи")
        return [], set(), {"task_documents": [], "other_tasks_skipped": 0}, warnings
    task_paths = [row["path"] for row in _docs_entries(project, docs)]
    task_ids: list[str] = []
    for path in task_paths:
        item = by_path.get(path)
        if item is None:
            raise HostStop("TASK_DOC_OUTSIDE_CONTEXT", f"документ задачи {path} не входит в inventory выбранного модуля"
                           + _exclusion_note(inventory, path), TASK_DOC_FIX)
        task_ids.append(str(item["opaque_id"]))
    others = {str(item["opaque_id"]) for item in to_do if str(item["project_path"]) not in task_paths}
    return task_ids, others, {"task_documents": task_paths, "other_tasks_skipped": len(others)}, []


def _exclusion_note(inventory: Mapping[str, Any], path: str) -> str:
    for row in inventory.get("exclusions", []):
        excluded = str(row.get("project_path"))
        if path == excluded or path.startswith(excluded + "/"):
            return f" (исключён правилом {row.get('scanner_rule_id')}: {excluded})"
    return " (он вне корня модуля)"






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


def _leave(project: Path, root: Path, attempt_id: str) -> dict[str, Any]:
    """The mark «оставлен» of a run stopped before the canonical review — the attempt is not closed;
    only the same-request check of scan skips the run. A decision of the run, so a file and its pair of events
    (``bind_bytes``, batch ``left-v1``): written once, a repeat only finishes the events; a file of other bytes is RECEIPT_FILE_UNBOUND."""
    from tools.pilot_state import LEFT_BATCH, LEFT_MARK, bind_bytes

    value = {"attempt_id": attempt_id, "reason": "LEFT_BEFORE_REVIEW"}
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    bind_bytes(root, attempt_id, root / LEFT_MARK, data, digest="sha256:" + hashlib.sha256(data).hexdigest(), batch_id=LEFT_BATCH, label="left mark")
    return value


def _left_mark(root: Path) -> str | None:
    """Why the scan skips this run — only when the mark names the run's last attempt (read confined)."""
    from tools.pilot_state import left_attempt

    try:
        return "оставлен до ревью кейсов (finish --abandon LEFT_BEFORE_REVIEW)" if left_attempt(root) else None
    except (OSError, ValueError):
        return None


def _before_canonical_review(root: Path, attempt_id: str) -> bool:
    """The attempt has no reviewer session yet — no closing command exists before the canonical review."""
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        read_reviewer_session_ledger(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return True
    return False


def _closed_before_review(project: Path, manifest: Mapping[str, Any], detail: str) -> dict[str, Any]:
    """The honest answer and the next run — this run stays as it is, --new-run starts another."""
    request = manifest.get("scan_request")
    command = (_scan_command(project, request, extra=("--new-run",)) if isinstance(request, Mapping)
               else "scan с теми же --module, --docs и --profile, что у этого прогона, и --new-run")
    return {
        "status": "error", "stage": "finish", "reason": "FINISH_FACT_UNDETERMINED", "run_id": manifest["run_id"],
        "message": "попытку нельзя закрыть до ревью кейсов: стадии до ревью не дают закрывающего состояния (" + detail + ")",
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


def cmd_finish(args: Any) -> int:
    """Close the current attempt in one command — the same order and checks as the manual sequence."""
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
                       _exact_fix("python -m tools.run_pipeline ingest --project <project> --run <run_id> --reports <каталог с отчётами>" if closes_by_ingest else
                                  "попытка закрыта: итог — python -m tools.run_pipeline report --project <project> --run <run_id> "
                                  "(или status); следующий заход — next_run из ответа finish/status", project, root, attempt_id))
    if abandon == "LEFT_BEFORE_REVIEW":  # the run stays as it is; a plain scan of the request starts a new one
        if not _before_canonical_review(root, attempt_id):
            raise HostStop("ABANDON_NOT_NEEDED", "попытка уже дошла до ревью кейсов: LEFT_BEFORE_REVIEW только до него",
                           _exact_fix("python -m tools.run_pipeline finish --project <project> --run <run_id>", project, root, attempt_id))
        closed = _closed_before_review(project, run["manifest"], "прогон оставлен")
        _print({"status": "ok", "stage": "finish", "run_id": run["manifest"]["run_id"], "left": _leave(project, root, attempt_id),
                "message": "прогон оставлен до ревью кейсов; простой scan того же запроса начнёт новый", "next_run": closed["next_run"]})
        return 0
    from tools.orchestrate_test_case_revision import close_pending_rework, published_dir

    published_dir(root)  # a published directory that leads out of the run: refused before the attempt is closed
    if not executed:
        close_pending_rework(root, attempt_id)  # the first review sent cases back and the rework is not over: r2 and the rejected cases are published now, once
    try:
        closed = (finalize_durable_execution_attempt(root, attempt_id) if executed
                  else finalize_durable_branch_attempt(root, attempt_id, abandon))
    except FinalizationError as error:
        if not executed and str(error).startswith("FINISH_FACT_UNDETERMINED") and _before_canonical_review(root, attempt_id):
            _print(_closed_before_review(project, run["manifest"], str(error)))
            return 2
        code = str(error).split(":", 1)[0] if str(error).startswith(("FINISH_", "ABANDON_")) else "FINALIZATION"
        fix = getattr(error, "fix", None) or (_INTERRUPTED_FIX if executed and not (root / "execution-receipts" / f"{attempt_id}.json").exists() else None)
        raise HostStop(code, str(error), _exact_fix(fix, project, root, attempt_id) if fix else None) from error
    except (KeyError, TypeError, ValueError, OSError) as error:
        if not executed or (root / "execution-receipts" / f"{attempt_id}.json").exists():
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
        from tools.review_canaries import status_fields

        result.update(status_fields(root, str(attempt["attempt_id"]), result))  # the canaries of the review, REVIEW_UNRELIABLE
    if attempt is not None and attempt.get("state") == "TERMINAL":
        from tools.pilot_state import read_terminal_result

        try:
            result.update(_next_run(project, run["manifest"], read_terminal_result(root, str(attempt["attempt_id"])).get("reason_code")))
        except (KeyError, OSError, TypeError, ValueError):
            pass
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
baseline_check = _baseline_check
drift_report = _drift_report
digest_bytes = _digest_bytes
style_notes = _style_notes
report_mismatch = _report_mismatch
automation_split_fields = _automation_split
quarantine_summary = _quarantine_summary


if __name__ == "__main__":
    # the execution commands raise the HostStop of ``tools.run_pipeline``; run as ``__main__`` this file has a class of
    # its own, so hand over to the imported module — one HostStop, one ``main``
    from tools.run_pipeline import main as _main

    raise SystemExit(_main())
