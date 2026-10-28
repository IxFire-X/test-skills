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
from typing import Any, Mapping, Sequence

from tools.json_cli import JsonArgumentParser
from tools.project_inventory import (
    ContextSelectionError,
    ContextStop,
    InventoryError,
    build_inventory,
    context_filter,
    select_context_batches,
    skillsrc_exclude,
    skipped_summary,
)

ROOT = Path(__file__).resolve().parents[1]

_SCAN_CONTEXT_BYTE_BUDGET = 256 * 1024  # default of .skillsrc limits.context_batch_bytes (FaT-5)


class HostStop(RuntimeError):
    def __init__(self, code: str, message: str = "", fix: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.fix = fix


_PRINTED: list[dict[str, Any]] = []


def _print(payload: dict[str, Any]) -> None:
    _PRINTED[:] = [payload]
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


_NEXT_STEPS = (
    # (substring of next_expected_artifact, stage, action, section of skills/orchestrate/SKILL.md)
    # FaT-B: every value _durable_status_facts sets must match a row (tests/test_fat3.py walks them all).
    ("authorized context", "context", "выбор контекста: опубликовать квитанцию выбора по baseline", "3"),
    ("context-marker response", "context-marker",
     "сначала context_marker_parts plan --run-root <run>: single — запрос context-marker:baseline и вызов модели; "
     "parts — context-marker:requirements, затем части по плану", "3"),
    ("generator fragments", "tc-generator", "запрос генератора на первую пачку плана и его ответ", "3"),
    ("remaining fragments or assembled candidate", "tc-generator",
     "следующая пачка генератора; когда все фрагменты есть — сборка кандидата кодом", "3"),
    ("reviewer ledger and effective selection or negative closure", "canonical-review",
     "завершить журнал ревью и выбрать effective canonical или закрыть отрицательную ветку", "4"),
    ("static automation review", "autotest-reviewer", "открыть границу ревью автотестов и вызвать autotest-reviewer", "5"),
    # FaT-H2b 2.8: after autotest-reviewer the verdict decides — exec, the next revision, or finish
    ("automation revision", "tc-to-autotest", "следующая ревизия автоматизации tc-to-autotest:r2 по замечаниям ревью, затем её ревью autotest-reviewer:r2", "5"),
    ("automation refused", "finish",
     "автоматизация отклонена, заблокирована или не записывается в проект: finish закрывает попытку и печатает next_run — "
     "команду следующего захода; "
     "нового ревью сверх лимита ревизий нет", "6"),
    (" response", "model", "выполнить запрос модели из pending_request_path и опубликовать ответ", "3"),
    ("context-marker parts merge", "context-marker:parts",
     "следующая часть разметки по context_marker_parts plan; когда все части есть — слияние частей кодом (merge), не вызов модели", "3"),
    ("module selection", "scan", "scan: выбрать модуль и заморозить baseline", "2"),
    ("context-marker", "context-marker", "разметка контекста по квитанциям выбора", "3"),
    ("canonical reviewer package", "canonical-review", "собрать пакет ревью и начать независимое ревью", "4"),
    ("reviewer evidence or verdict", "canonical-review", "продолжить ревью: следующая часть или вердикт", "4"),
    ("effective canonical", "canonical-review", "завершить ревью и опубликовать effective canonical", "4"),
    ("automation artifact", "automation", "автоматизация принятых кейсов", "5"),
    ("materialization", "exec", "материализация автотестов", "5"),
    ("execution receipt", "exec", "exec: запуск тестов", "5"),
    ("execution trace", "closure", "трасса исполнения и диспозиции", "6"),
    ("disposition", "closure", "диспозиции и финализация", "6"),
    ("finalization", "closure", "линейное закрытие и финализация", "6"),
    ("terminal", "closure", "терминальный результат", "6"),
    ("validated inputs or new scan", "resume", "файлы проекта изменились после scan: верните их или начните новый прогон (status: drift)", "Resume и дрейф"),
)


def _next_step(status: Mapping[str, Any]) -> dict[str, Any]:
    """FaT-3: what to do next after /clear, from the same facts status shows (only reads)."""
    expected = status.get("next_expected_artifact")
    if expected is None:
        return {"stage": "terminal", "action": "передать человеку результат попытки", "skill_section": "5"}
    for needle, stage, action, section in _NEXT_STEPS:
        if needle in str(expected):
            step = {"stage": stage, "action": action, "skill_section": section}
            if stage == "model":
                step["stage"] = str(expected).removesuffix(" response")
            return step
    return {"stage": "unknown", "action": f"следующий артефакт: {expected}", "skill_section": "Stop conditions"}


def _exclusion_summary(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """FaT-2: what the scan left out, for a person; derived from the attempt's frozen inventory, never stored."""
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt
    from tools.scan_rules import exclusion_summary

    root = Path(run_root)
    try:
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        inventory = read_inventory_receipt(root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    except (KeyError, OSError, ValueError):
        return None
    return exclusion_summary(inventory["exclusions"], str(attempt.get("module") or "."))


def _durable_status(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    result = _durable_status_facts(project, run_root, stage=stage, status=status, **extra)
    if result.get("attempt_id") is not None:
        result["next_step"] = _next_step(result)
    return result


def _quarantine_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """FaT-8: what the FAIL closure did to the generated tests, read from the attempt's disposition receipt."""
    from tools import pilot_state as pilot

    try:
        rows = pilot.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["files"]
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    quarantine = [
        {"file": row["path"], "method": item["method"], "ref": item["ref"], "message": item["message"],
         **{key: item[key] for key in ("failed_rows", "disabled_passed_rows") if key in item}}  # FaT-PT: a group method's rows
        for row in rows if row.get("disposition") == "QUARANTINED" for item in row["quarantine"]
    ]
    unresolved = [row["path"] for row in rows if row.get("reason_code") == "QUARANTINE_UNRESOLVED"]
    if not quarantine and not unresolved:
        return {}
    return {"quarantine": quarantine, **({"quarantine_unresolved": (
        "QUARANTINE_UNRESOLVED: упавшие методы не удалось однозначно сопоставить с отчётом exec, "
        f"поэтому сгенерированные файлы убраны целиком, включая прошедшие тесты: {', '.join(unresolved)}"
    )} if unresolved else {})}


def _review_notes_summary(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """FaT-RN: how many notes the accepted case/autotest review left and where its answer is; nothing when there are none."""
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
    """FaT-L: the project's style checks the selected-tests run skipped and how to format the files left in the project."""
    from tools import execution_adapters as adapters
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
    """FaT-DN: why an UNKNOWN run could not match its JUnit report, and the fix — never the report text.

    A testcase whose method is selected but whose classname is none of that method's selected classes is what a class
    display name does (Surefire writes it as the classname); the hint names the selected classes only."""
    from tools import pilot_state as pilot
    from tools.run_tests import junit_method_name, parse_junit_bytes

    try:
        payload = pilot.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        if payload.get("verdict") != "UNKNOWN" or not any(row.get("kind") == "JUNIT_INVALID" for row in payload.get("process_evidence", [])):
            return {}
        execution = payload["execution"]
        selected: dict[str, set[str]] = {}
        for selector in execution["selectors"]:
            class_fqn, _, method = str(selector).partition("#")
            if method:
                selected.setdefault(method, set()).add(class_fqn)
        classes: set[str] = set()
        for row in execution["artifact_evidence"]:
            relative = Path(str(row.get("path", "")))
            if row.get("kind") != "native_report" or relative.is_absolute() or relative.parts[:2] != ("artifacts", attempt_id) or ".." in relative.parts:
                continue
            for case in parse_junit_bytes((Path(run_root) / relative).read_bytes()).cases:
                method = junit_method_name(case.name)
                if method in selected and case.classname not in selected[method]:
                    classes |= selected[method]
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
        **({"self_review_accepted": True} if run["authorization"].get("accept_self_review") is True else {}),
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
                next_artifact = "context-marker parts merge"  # FaT-5: the remaining parts, then the merge by code
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
    if ledger_events:
        digest = ledger_events[-1]["artifact_digest"]
        path = f"reviewer-session-ledgers/{attempt_id}/{digest[7:]}.json"
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
        ("disposition-receipt", "pre-finalization trace"), ("resume-validation", "validated inputs or new scan"),
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
        result.update(stop_reason="EVIDENCE_UNVERIFIED", next_expected_artifact="validated inputs or new scan")
    elif attempt["state"] == "WAITING_FOR_INPUT" and result["stop_reason"] is None:
        result["stop_detail"] = "WAITING_FOR_INPUT has no persisted cause; consult the originating request."
    return result


def _read_skillsrc_binding(path: Path) -> tuple[dict[str, Any], str]:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    data = path.read_bytes()
    return parse_skillsrc_bytes(data), _digest_bytes(data)


def _input_files(project: Path, paths: Sequence[str] | None, flag: str) -> list[tuple[str, bytes]]:
    """The files a scan flag names (``--docs``, ``--reference``): inside the project, no reparse point, each path once."""
    files: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for raw in paths or []:
        candidate = Path(raw)
        confined = _confined(project, candidate if candidate.is_absolute() else project / candidate)
        if _is_reparse(confined) or not confined.is_file():
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is missing or a reparse point: {raw}")
        rel = confined.relative_to(project.resolve()).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        files.append((rel, confined.read_bytes()))
    return files


def _is_zephyr_csv(data: bytes) -> bool:
    """FaT-REF: the file is a Zephyr Scale test-case CSV — the shared reader knows its columns (rows may still be wrong)."""
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    try:
        parse_zephyr_csv(data)
    except ZephyrCsvError as error:
        return error.code == "ZEPHYR_CSV_ROWS"
    return True


def _docs_entries(project: Path, docs: list[str] | None) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for rel, data in _input_files(project, docs, "--docs"):
        if _is_zephyr_csv(data):  # FaT-REF: an export of cases is not a requirements document
            raise HostStop("DOCS_ZEPHYR_CSV", f"{rel} — выгрузка тест-кейсов Zephyr, а не документ требований",
                           fix=f"подайте её через scan --reference {rel} (эталон для сравнения), а в --docs — документ требований")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"docs file is not UTF-8: {rel}") from error
        entries.append({"path": rel, "sha256": _digest_bytes(data), "content": text})
    entries.sort(key=lambda row: row["path"])
    return entries


def _reference_cases(project: Path, reference: str | None) -> tuple[str, bytes, list[dict[str, Any]]] | None:
    """FaT-REF: ``scan --reference <csv>`` read by the shared Zephyr reader; a reader refusal is the scan's refusal."""
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
        require_human_csv(cases)  # FaT-REF: the v4 Test Data holds the machine model, not the human data of a case
    except ZephyrCsvError as error:
        raise HostStop(error.code, f"{rel}: {error}", fix="выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — "
                                                        "в Test Data должен быть текст человека") from error
    return rel, data, cases


def _docs_projection(snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"path": row["path"], "sha256": row["sha256"]} for row in snapshot]


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
    facts = execution_facts(project, module)
    if not facts["ready"]:
        return {"reason": str(facts["reason"]), "fix": str(facts["fix"])}
    # FaT-S: the same baseline blockers the freeze would meet, known before the run is created.
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
    """FaT-8: ``cleanup`` when this scan asks for the old FAIL cleaning; the flag beats `.skillsrc`, read only here."""
    flag = getattr(args, "disposition_policy", None)
    if flag is not None:
        return "cleanup" if flag == "cleanup" else None
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc

    try:
        document = load_skillsrc(project / ".skillsrc")  # FaT-B: the package writes YAML; JSON is YAML too
    except (OSError, SkillsrcError):
        return None
    return "cleanup" if document.get("disposition_policy") == "cleanup" else None


def _init_skillsrc_run(project: Path, args: Any, scan_request: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], dict[str, Any]]:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.pilot_state import create_lowered_run, create_run

    profile = getattr(args, "profile", "local-pilot-v1")
    skipped = _execution_skipped(project, args) if profile == "local-pilot-v1" else None
    if skipped is not None:
        # FaT-R: no build facts — the cases still run; the run says it is cases-only and what to fix for execution.
        profile = "cases-only-v1"
        args.profile = profile
    authorization = {"request_id": f"scan-{uuid.uuid4().hex[:12]}", "execution_requested": profile == "local-pilot-v1"}
    if getattr(args, "accept_self_review", False):
        authorization["accept_self_review"] = True
    if _scan_disposition_policy(project, args) == "cleanup":
        authorization["disposition_policy"] = "cleanup"
    if scan_request is not None:
        from tools.pilot_state import create_scan_run

        run = create_scan_run(project, profile, authorization, scan_request, skipped)
    else:
        run = create_run(project, profile, authorization) if skipped is None else create_lowered_run(project, authorization, skipped)
    if (project / ".skillsrc").is_file():
        _read_skillsrc_binding(project / ".skillsrc")
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


def _filtered_context(inventory: Mapping[str, Any], project: Path, module_root: Path, selected_ids: list[str],
                      task_ids: list[str], budget: int) -> tuple[list[str], list[dict[str, Any]]]:
    """FaT-A: assets, user excludes and large non-sources stay out of the model context, always listed (scan and ready)."""
    return context_filter(inventory, project, selected_ids, byte_budget=budget, excludes=skillsrc_exclude(project, module_root), task_ids=task_ids)


def context_preview(inventory: Mapping[str, Any], project: Path, module_root: Path, docs: list[str] | None,
                    target: str | None = None) -> tuple[list[str], list[dict[str, Any]], int]:
    """What the scan would hand the model, without a run: ``(ids, skipped, batch budget)``; only reads."""
    task_ids, other_task_ids, _summary, _warnings = _task_document_scope(inventory, project, docs)
    selected = [opaque for opaque in _context_scope_ids(inventory, project, module_root, target) if opaque not in other_task_ids]
    selected += [opaque for opaque in task_ids if opaque not in selected]
    budget = _scan_context_budget(inventory, project)
    kept, skipped = _filtered_context(inventory, project, module_root, selected, task_ids, budget)
    return kept, skipped, budget


def _scan_context_budget(inventory: Mapping[str, Any], project: Path | None = None) -> int:
    """The byte budget of one context batch: ``.skillsrc limits.context_batch_bytes``, else 256 KiB (FaT-5)."""
    del inventory
    path = None if project is None else project / ".skillsrc"
    if path is not None and path.is_file():
        from tools.skillsrc_manifest import load_skillsrc

        value = (load_skillsrc(path).get("limits") or {}).get("context_batch_bytes")
        if isinstance(value, int) and value > 0:
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
            interpreter = str(test["interpreter"]) if isinstance(test.get("interpreter"), str) else None
            wrapper = str(test["wrapper"]) if isinstance(test.get("wrapper"), str) else None
            runtime_facts.update(interpreter_path=interpreter, wrapper_path=wrapper,
                interpreter_identity=runtime_identity(module_root, interpreter) if interpreter is not None else None,
                wrapper_identity=runtime_identity(module_root, wrapper) if wrapper is not None else None)
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


def _scan_request(args: Any) -> dict[str, Any]:
    """FaT-G: the scan flags as given (the requested profile before any lowering) — kept in the run manifest."""
    return {
        "module": getattr(args, "module", None), "docs": [str(doc) for doc in (getattr(args, "docs", None) or [])],
        "target": getattr(args, "target", None), "profile": getattr(args, "profile", "local-pilot-v1"),
        "accept_self_review": bool(getattr(args, "accept_self_review", False)),
        "disposition_policy": getattr(args, "disposition_policy", None),
        **({"reference": str(args.reference)} if getattr(args, "reference", None) else {}),
    }


def _request_key(project: Path, request: Mapping[str, Any]) -> tuple[Any, ...]:
    """The same request: the module (resolved by .skillsrc when not given), the --docs paths and the requested profile."""
    module = request.get("module")
    if module is None:
        try:
            from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

            modules = normalize_skillsrc(load_skillsrc(project / ".skillsrc"))["modules"]
            module = modules[0].get("id") if len(modules) == 1 else None
        except Exception:  # an unreadable .skillsrc: the scan itself will say so
            module = None
    docs = []
    for raw in request.get("docs") or []:
        path = Path(str(raw))
        try:
            docs.append((path if path.is_absolute() else project / path).resolve().relative_to(project).as_posix())
        except (OSError, ValueError):
            docs.append(str(raw).replace("\\", "/"))
    return module, tuple(sorted(docs)), request.get("profile"), request.get("reference")


def _open_run_of(project: Path, request: Mapping[str, Any]) -> tuple[Path | None, list[str]]:
    """FaT-G: an unfinished run of the same request — its scan selected the context (CONTEXT_SELECTED) and its last
    attempt has no ATTEMPT_TERMINAL.  A run whose scan failed earlier never blocks; runs without ``scan_request``
    (created before FaT-G) are never compared.

    FaT-H2b 2.2: ``(the live run that blocks, the abandoned ones)``.  An unfinished run whose baseline check finds
    drift or fails is abandoned: it cannot go on, so it never blocks; the scan names it and leaves it as it is."""
    from tools import pilot_state as pilot

    runs = project / ".pilot-runs"
    abandoned: list[str] = []
    if not runs.is_dir():
        return None, abandoned
    key = _request_key(project, request)
    for root in sorted(path for path in runs.iterdir() if path.is_dir()):
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
            if selected and attempt.get("state") != "TERMINAL":
                why = _abandoned(project, root, attempt)
                if why is None:
                    return root, abandoned
                abandoned.append(f"{root.name} ({why})")
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None, abandoned


def _abandoned(project: Path, root: Path, attempt: Mapping[str, Any]) -> str | None:
    """Why an unfinished run cannot go on — the same baseline check as exec and status; None when it can."""
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    try:
        result = _baseline_check(project, root, attempt, module_root)
    except Exception:  # an unreadable baseline: the run cannot resume
        return "сверка baseline не прошла"
    if result.get("status") == "UNCHANGED":
        return None
    changed = result.get("changed") or []
    return f"дрейф: {len(changed)} файл(ов)" if changed else "сверка baseline не прошла"


def _run_in_progress(project: Path, root: Path) -> dict[str, Any]:
    from tools.finalize_attempt import finish_closes
    from tools.pilot_state import derive_state

    python = f'"{sys.executable}" -m tools.run_pipeline'
    try:
        next_step = _durable_status(project, root, stage="status").get("next_step")
    except (HostStop, OSError, KeyError, TypeError, ValueError):
        next_step = None
    try:
        closes = finish_closes(root, str(derive_state(root)["attempts"][-1]["attempt_id"]))
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        closes = False
    # FaT-H2b 2.2: finish only where it closes the attempt (rework, a refused automation), never as a dead end
    finish = f'закрыть его без продолжения: {python} finish --project "{project}" --run {root.name}; ' if closes else ""
    return {
        "status": "error", "stage": "scan", "reason": "SCAN_RUN_IN_PROGRESS", "run_id": root.name, "run_root": str(root),
        "next_step": next_step,
        "message": "для этого проекта, документа и профиля уже есть незавершённый прогон; новый не создан",
        "fix": (f'продолжите его: {python} status --project "{project}" --run {root.name}; '
                f"{finish}нужен ещё один прогон рядом — повторите этот scan с флагом --new-run"),
    }


def _scan_command(project: Path, request: Mapping[str, Any], *, extra: Sequence[str] = ()) -> str:
    """FaT-G: the scan of the same request, its flags as given — printed for the human; the code never runs it."""
    parts = [f'"{sys.executable}" -m tools.run_pipeline scan --project "{project}"']
    if request.get("module"):
        parts.append(f'--module "{request["module"]}"')
    parts += [f'--docs "{doc}"' for doc in request.get("docs") or []]
    if request.get("target"):
        parts.append(f'--target "{request["target"]}"')
    parts.append(f"--profile {request.get('profile') or 'local-pilot-v1'}")
    if request.get("accept_self_review"):
        parts.append("--accept-self-review")
    if request.get("disposition_policy"):
        parts.append(f"--disposition-policy {request['disposition_policy']}")
    if request.get("reference"):
        parts.append(f'--reference "{request["reference"]}"')
    return " ".join([*parts, *extra])


def _drift_report(project: Path, manifest: Mapping[str, Any], changed: Sequence[str]) -> dict[str, Any]:
    """FaT-G: the changed paths (never bytes) and the two real ways out."""
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
}


def _next_run(project: Path, manifest: Mapping[str, Any], reason_code: Any) -> dict[str, Any]:
    """FaT-G: after a closed negative outcome, the scan command of the same request — printed, never run here."""
    request = manifest.get("scan_request")
    if reason_code not in _SECOND_PASS or not isinstance(request, Mapping):
        return {}
    return {"next_run": {"why": _SECOND_PASS[str(reason_code)], "command": _scan_command(project, request)}}


def _baseline_check(project: Path, root: Path, attempt: Mapping[str, Any], module_root: Path, request: Any = None) -> Mapping[str, Any]:
    """FaT-G: the one baseline check of exec (with its request) and status (from the baseline alone); only reads."""
    from tools.project_inventory import (
        baseline_external_input_paths,
        build_inventory,
        read_execution_baseline,
        runtime_identity,
        validate_execution_baseline,
    )
    from tools.pilot_state import read_attempt_receipt

    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
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
        # exec: always against local-pilot-v1 with the runtime identity, as before FaT-G — never the baseline's own profile
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
        # FaT-H2b 2.11: status names the vanished runtime and still compares the files against the scan
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
    """FaT-G: status of an open attempt names the files changed since the scan — the same check as exec."""
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    try:
        result = _baseline_check(project, root, attempt, module_root)
    except Exception:  # an unreadable baseline is the status's own evidence error, not a drift
        return {}
    if result.get("status") == "UNCHANGED":
        return {}
    return {"drift": _drift_report(project, manifest, result.get("changed") or [])}


def cmd_scan(args: Any) -> int:
    """Scan, then ``run_id: <id>`` as the last line of the output (FaT-G)."""
    _PRINTED.clear()
    code = _scan(args)
    run_id = _PRINTED[-1].get("run_id") if _PRINTED else None
    if run_id:
        print(f"run_id: {run_id}")
    return code


def _scan(args: Any) -> int:
    project = Path(args.project).resolve()
    request = _scan_request(args)
    abandoned: list[str] = []
    if not getattr(args, "new_run", False):
        open_root, abandoned = _open_run_of(project, request)
        if open_root is not None:
            _print(_run_in_progress(project, open_root))
            return 2
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
        task_ids, other_task_ids, context_summary, context_warnings = _task_document_scope(
            frozen_inventory, project, getattr(args, "docs", None))
        attempt = create_attempt(
            run_root,
            {"project": str(project), "module": module_path, "policy_profile": run["manifest"]["policy_profile"]},
            frozen["baseline"],
        )
        selected_ids = _context_scope_ids(frozen_inventory, project, module_root, getattr(args, "target", None))
        # FaT-3: the task document of --docs always goes to the model; the other tasks of to_do do not.
        selected_ids = [opaque for opaque in selected_ids if opaque not in other_task_ids]
        selected_ids += [opaque for opaque in task_ids if opaque not in selected_ids]
        by_id = {str(item["opaque_id"]): str(item["project_path"]) for item in frozen_inventory["files"]}
        reference = _reference_cases(project, getattr(args, "reference", None))
        if reference is not None:  # FaT-REF: the reference CSV is never context of a model
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
        for batch in batches:
            publish_context_selection(run_root, str(attempt["attempt_id"]), batch["receipt"])
        if reference is not None:  # FaT-REF: a receipt for the review hint; never context or generator input
            from tools.pilot_state import publish_reference_cases

            publish_reference_cases(run_root, str(attempt["attempt_id"]), *reference)
            context_summary["reference_cases"] = len(reference[2])
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
        context_summary=context_summary,
        **({"context_skipped": context_skipped} if context_skipped["files"] else {}),
        **({"warnings": context_warnings} if context_warnings else {}),
        **_skillsrc_defaults(project, module),
        **({"abandoned_runs": abandoned} if abandoned else {}),
        reuse=_marker_reuse(run_root, str(attempt["attempt_id"])),
    )
    _print(payload)
    return 0


def _marker_reuse(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """FaT-CR: the scan suggests an earlier accepted run whose context marker has the very same inputs; the agent decides."""
    from tools.pilot_state import marker_reuse_suggestion

    try:
        return marker_reuse_suggestion(run_root, attempt_id)
    except (ValueError, OSError, KeyError, TypeError) as error:  # a suggestion never stops the scan
        return {"run_id": None, "reason": f"поиск не удался: {error}"}


def _skillsrc_defaults(project: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    """FaT-H2b P2b: a test framework the build files of the module do not show was taken by default — say so."""
    from tools.discover_project import module_test_evidenced

    framework = (module.get("test") or {}).get("framework") if isinstance(module.get("test"), Mapping) else None
    if framework is None or module_test_evidenced(project, module):
        return {}
    return {"skillsrc_defaults": [
        f"{module.get('id')}: test.framework={framework} выбран по умолчанию — в сборке модуля нет признака тестового "
        f"фреймворка; проверьте, что тесты модуля на {framework}, иначе исправьте .skillsrc"]}


TASK_DOC_FIX = ("выберите модуль, внутри которого лежит документ (например, корневой --module <ID>), и сузьте --target на нужный "
                "подмодуль; или положите документ внутрь модуля")


def _under_to_do(project_path: str) -> bool:
    return "to_do" in project_path.split("/")[:-1]


def _task_document_scope(inventory: Mapping[str, Any], project: Path, docs: list[str] | None) -> tuple[list[str], set[str], dict[str, Any], list[str]]:
    """FaT-3: ``(ids of the task documents, ids of other tasks' files in to_do, summary, warnings)``.

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
        raise HostStop("EXECUTION_COORDINATES", "run lacks local execution authorization; a run without execution "
                       "(cases-only or lowered to it) closes with run_pipeline finish")
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


def _revalidate_execution_baseline(coordinates: Mapping[str, Any], request: Any) -> None:
    """Reject any non-delta baseline drift before the execution-start gate; the stop names the changed paths (FaT-G)."""
    from tools.project_inventory import InventoryError

    try:
        project = Path(str(coordinates["manifest"]["project"])).resolve()
        root, attempt, module_root = Path(coordinates["run_root"]), coordinates["attempt"], Path(coordinates["module_root"])
        result = _baseline_check(project, root, attempt, module_root, request)
    except (InventoryError, KeyError, TypeError, ValueError, OSError) as error:
        executable = Path(str(getattr(request, "executable", "") or ""))
        if str(executable) not in {"", "."} and not executable.is_file():  # FaT-H2b 2.11: name the vanished runtime
            raise HostStop("BASELINE_INCOMPLETE", f"среда запуска модуля недоступна: {executable}; верните её или начните "
                           "новый прогон (команда — в status: drift)") from error
        raise HostStop("BASELINE_INCOMPLETE", "execution baseline is unreadable or incomplete") from error
    if result.get("status") != "UNCHANGED":
        report = _drift_report(project, coordinates["manifest"], result.get("changed") or [])
        changed = ", ".join(report["changed"]) or "входы baseline"
        raise HostStop(str(result.get("reason_code") or "BASELINE_DRIFT"),
                       f"файлы проекта изменились после scan: {changed}; {report['fix']}", report["fix"])


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
        executable = str(system_maven_path(str(runtime_path)) if adapter_id == SYSTEM_MAVEN else module_runtime_path(module_root, runtime_path.as_posix()))
        profile = str(baseline["build_profile"])
        typed = tuple(sorted((str(key), str(value)) for key, value in baseline["adapter_parameters"].items()))
        from tools.execution_adapters import command_for
        reports, argv = command_for(adapter_id, executable, profile, selectors)
        language = "python" if adapter_id == PYTEST else "java"
        request = ExecutionRequest(
            adapter_id, executable, tuple(argv), str(module_root), tuple(selectors), 600,
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
            _revalidate_execution_baseline(coordinates, request)
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
        **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
    })
    return int(closed["exit_code"])


_NOT_PREPARED_FIX = ("exec запускает только принятую автоматизацию: заверши статическое ревью автотестов "
                     "(autotest-reviewer, вердикт ПРИНЯТО), затем снова exec")
# FaT-H2b 2.9: after a review that did not accept the automation — never push for another review past the limit
_REFUSED_FIX = ("статическое ревью не приняло автоматизацию, ревизий больше нет: закройте попытку командой finish — "
                "она напечатает next_run, команду следующего захода")
_REVISION_FIX = "ревью вернуло автоматизацию на правку: следующая ревизия tc-to-autotest:r2, её ревью autotest-reviewer:r2, затем exec"
# FaT-RL: the review accepted the automation, but exec cannot write it into the project — no later step changes that
_UNMATERIALIZABLE_FIX = ("ревью приняло автоматизацию, но exec не может записать её в проект (причина в message): закройте попытку "
                         "командой finish — она закроет её отрицательно (AUTOMATION_NOT_MATERIALIZABLE) и напечатает next_run")


def _not_prepared_fix(root: Path, attempt_id: str) -> str:
    from tools.finalize_attempt import automation_next

    try:
        outcome, detail = automation_next(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return _NOT_PREPARED_FIX
    if outcome == "PENDING":
        return _REVISION_FIX if detail == "tc-to-autotest:r2" else (
            f"автоматизация не закончена: следующий шаг — {detail}, затем ревью автотестов (ПРИНЯТО) и снова exec")
    return {"AUTOMATION_REJECTED": _REFUSED_FIX, "AUTOMATION_BLOCKED": _REFUSED_FIX,
            "AUTOMATION_NOT_MATERIALIZABLE": _UNMATERIALIZABLE_FIX}.get(outcome, _NOT_PREPARED_FIX)


def _prepare_execution(coordinates: Mapping[str, Any]) -> list[str]:
    """FaT-E: before the first run, materialize the accepted reviewed automation of the attempt, if not done yet.

    Inside the exec step, not a driver: the same ``materialize_delta`` the agent would call, from the journal's own
    tc-to-autotest:rN and autotest-reviewer:rN answers.  Its checks stay as they are; a repeat is a no-op.
    """
    from tools.generated_delta import GeneratedDeltaError, journal_automation, materialize_delta
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical
    from tools.project_inventory import read_execution_baseline

    root = Path(coordinates["run_root"])
    attempt = coordinates["attempt"]
    attempt_id = str(attempt["attempt_id"])
    try:
        read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
        return []
    except (KeyError, OSError, TypeError, ValueError):
        pass
    inputs = journal_automation(root, attempt_id)
    if inputs is None:
        raise HostStop("EXECUTION_NOT_PREPARED", "no static review of the automation in this attempt", _not_prepared_fix(root, attempt_id))
    try:
        baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        document = read_effective_canonical(root, attempt_id)["document"]
        materialize_delta(
            Path(str(coordinates["manifest"]["project"])), Path(coordinates["module_root"]), baseline, inputs["automation"], inputs["review"],
            canonical_document=document, run_root=root, attempt_id=attempt_id,
            automation_history=inputs["automation_history"], review_history=inputs["review_history"],
        )
    except (GeneratedDeltaError, KeyError, TypeError, ValueError, OSError) as error:
        raise HostStop("EXECUTION_NOT_PREPARED", str(error), _not_prepared_fix(root, attempt_id)) from error
    return ["execution-inputs", "generated-delta"]


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
            **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
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
                        _revalidate_execution_baseline(coordinates, request)
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
            **_style_notes(project, Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
            **_report_mismatch(Path(coordinates["run_root"]), str(coordinates["attempt"]["attempt_id"])),
        })
        return int(closed["exit_code"])
    try:
        _prepare_execution(coordinates)
    except HostStop as stop:
        _print({"status": "error", "reason": stop.code, "message": str(stop), **({"fix": stop.fix} if stop.fix else {}),
                "attempt_id": coordinates["attempt"]["attempt_id"]})
        return 2
    try:
        from tools.canonical_document import load_canonical_document
        from tools.run_tests import load_automation_artifact, load_autotest_review_artifact
        from tools.schema_validation import load_json_strict

        # FaT-4: the six carriers come from the attempt's durable evidence; a path given is a check of the same bytes.
        loaders = {
            "canonical_document": load_canonical_document, "automation_artifact": load_automation_artifact,
            "autotest_review": load_autotest_review_artifact, "authorization_receipt": load_json_strict,
            "host_isolation_receipt": load_json_strict, "generated_delta_receipt": load_json_strict,
        }
        supplied = {name: loader(Path(getattr(args, name))) for name, loader in loaders.items() if getattr(args, name, None)}
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
        durable = {
            "canonical_document": effective["document"], "automation_artifact": execution_inputs["automation_artifact"],
            "autotest_review": execution_inputs["autotest_review"], "authorization_receipt": coordinates["authorization"],
            "host_isolation_receipt": durable_isolation, "generated_delta_receipt": durable_delta,
        }
        differing = [name for name, value in supplied.items() if value != durable[name]]
        if differing:
            raise ValueError("execution carrier differs from durable attempt evidence: " + ", ".join("--" + name.replace("_", "-") for name in differing))
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

def _rerun_request_current(request: Any) -> None:
    """FaT-H2a: a Gradle request recorded before --no-build-cache would replay the build cache, not rerun the tests."""
    from tools.execution_adapters import GRADLE

    if request.adapter_id == GRADLE and "--no-build-cache" not in request.argv:
        raise HostStop(
            "RERUN_REQUEST_OUTDATED",
            "команда Gradle изменилась в пакете: теперь cleanTest test --no-build-cache, а этот прогон записан старой "
            "командой — её повтор может взять результат тестов из кэша сборки, а не запустить их",
            "перепроверка этого прогона невозможна; начните новый прогон тем же scan, его тесты запустятся новой командой",
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
    _rerun_request_current(request)
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
    compatibility = validate_artifact_runner_compatibility(
        Path(request.cwd).resolve(), language,
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
    cwd = Path(request.cwd).resolve()
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


# FaT-RL: exec recovers a run whose controller died after the start claim (sparse UNKNOWN), finish does not
_INTERRUPTED_FIX = ("запуск тестов начался, но квитанции исполнения нет (процесс прервался): снова вызови exec — он восстановит "
                    "прерванный запуск как UNKNOWN и закроет попытку")


def cmd_finish(args: Any) -> int:
    """FaT-4: close the current attempt in one command — the same order and checks as the manual sequence."""
    from tools.finalize_attempt import FinalizationError, finalize_durable_branch_attempt, finalize_durable_execution_attempt
    from tools.pilot_state import derive_state

    project = Path(args.project).resolve()
    root, run, state = _read_run_coordinate(project, args.run)
    if not state["attempts"]:
        raise HostStop("FINISH_FACT_UNDETERMINED", "the run has no attempt yet")
    attempt = state["attempts"][-1]
    attempt_id = str(attempt["attempt_id"])
    executed = (root / "execution-receipts" / f"{attempt_id}.json").exists() or any(
        event.get("attempt_id") == attempt_id and event["event_type"] == "EXECUTION_STARTED" for event in derive_state(root)["events"])
    refusal = None
    if not executed and attempt.get("state") != "TERMINAL":
        from tools.finalize_attempt import automation_next

        try:
            outcome, detail = automation_next(root, attempt_id)
            refusal = detail if outcome == "AUTOMATION_NOT_MATERIALIZABLE" else None
        except (KeyError, OSError, TypeError, ValueError):
            refusal = None
    try:
        closed = finalize_durable_execution_attempt(root, attempt_id) if executed else finalize_durable_branch_attempt(root, attempt_id)
    except FinalizationError as error:
        code = str(error).split(":", 1)[0] if str(error).startswith("FINISH_") else "FINALIZATION"
        fix = getattr(error, "fix", None) or (_INTERRUPTED_FIX if executed and not (root / "execution-receipts" / f"{attempt_id}.json").exists() else None)
        raise HostStop(code, str(error), fix) from error
    except (KeyError, TypeError, ValueError, OSError) as error:
        if not executed or (root / "execution-receipts" / f"{attempt_id}.json").exists():
            raise
        raise HostStop("FINISH_FACT_UNDETERMINED", f"the started execution has no receipt yet ({error})", _INTERRUPTED_FIX) from error
    result = closed["result"]
    manifest = run["manifest"]
    _print({
        "status": "ok", "stage": "finish", "run_id": manifest["run_id"], "attempt_id": attempt_id,
        "branch": "execution" if executed else "no-execution", "idempotent": bool(closed.get("idempotent")),
        **{key: result.get(key) for key in ("completion", "verification", "coverage", "accepted", "reason_code")},
        "exit_code": int(closed["exit_code"]), "policy_profile": manifest["policy_profile"],
        **{key: manifest[key] for key in ("requested_policy_profile", "execution_skipped") if key in manifest},
        **({"review_independence": result["review_independence"]} if "review_independence" in result else {}),
        **({"materialization_refusal": refusal} if refusal else {}),
        **_quarantine_summary(root, attempt_id),
        **_review_notes_summary(root, attempt_id),
        **_style_notes(project, root, attempt_id),
        **_report_mismatch(root, attempt_id),
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
    """FaT-6: write the run report and the analyst questions; only reads the journal."""
    from tools.run_report import GUARANTEE, write_report

    project = Path(args.project).resolve()
    _root, run, _state = _read_run_coordinate(project, args.run)
    written = {Path(path).name: path for path in write_report(project, run["manifest"]["run_id"])}
    _print({"status": "ok", "stage": "report", "run_id": run["manifest"]["run_id"], "report_md": written["report.md"],
            "report_json": written["report.json"], "questions": written["questions-for-analyst.md"], "guarantee": GUARANTEE})
    return 0


def cmd_status(args: Any) -> int:
    from tools.pilot_state import closure_cache

    project = Path(args.project).resolve()
    # FaT-ST: one block, so the run manifest is validated and the journal derived once; the answer is printed after
    # the block's final full journal check.
    with closure_cache():
        result = _status_result(project, args.run)
    _print(result)
    return 0


def _status_result(project: Path, run_id: str) -> dict[str, Any]:
    root, run, state = _read_run_coordinate(project, run_id)
    result = _durable_status(project, root, stage="status")
    attempt = state["attempts"][-1] if state["attempts"] else None
    if attempt is not None:
        result.update(_review_notes_summary(root, str(attempt["attempt_id"])))
    if attempt is not None and attempt.get("state") == "TERMINAL":
        from tools.pilot_state import read_terminal_result

        try:
            result.update(_next_run(project, run["manifest"], read_terminal_result(root, str(attempt["attempt_id"])).get("reason_code")))
        except (KeyError, OSError, TypeError, ValueError):
            pass
    elif attempt is not None:
        result.update(_status_drift(project, root, run["manifest"], attempt))
    return result


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
            runtime_identity_value=current_runtime_identity, project_root=project,
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
    scan_parser.add_argument("--accept-self-review", action="store_true",
                             help="FaT-4b: the human accepts a reviewer in the same session, without a fresh context (marked SELF).")
    scan_parser.add_argument("--reference", default=None,
                             help="FaT-REF: a Zephyr Scale CSV of reference cases — compared with the new cases as a review hint")
    scan_parser.add_argument("--new-run", action="store_true",
                             help="FaT-G: start a new run although an unfinished run of the same request exists.")
    scan_parser.add_argument("--disposition-policy", choices=("quarantine", "cleanup"),
                             help="FaT-8: on FAIL quarantine failed methods (default) or clean every generated file as before.")
    status_parser = sub.add_parser("status", parents=[shared])
    finish_parser = sub.add_parser("finish", parents=[shared], help="Close the current attempt (FaT-4): exec branch or a branch without execution.")
    finish_parser.add_argument("--run", required=True)
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
    report_parser = sub.add_parser("report", parents=[shared], help="FaT-6: report.md/report.json and questions-for-analyst.md of the run, without project content.")
    report_parser.add_argument("--run", required=True, help="Exact durable run ID")
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
        "finish": cmd_finish,
        "report": cmd_report,
    }
    try:
        return commands[args.command](args)
    except Exception as error:
        from tools.skillsrc_manifest import SkillsrcError

        if isinstance(error, HostStop):
            _print({"status": "error", "reason": error.code, "message": str(error), **({"fix": error.fix} if error.fix else {})})
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
        if isinstance(error, (KeyError, TypeError, OSError)):
            # FaT-F1: a JSON answer with the code instead of a traceback; an OSError names its file
            from tools.json_cli import error_fields

            fields = error_fields(error)
            _print({"status": "error", "reason": fields["code"], **fields})
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
