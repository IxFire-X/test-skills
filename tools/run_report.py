"""FaT-6: a run report that may leave the workplace, and the analyst questions of the run (only reads the journal).

``python -m tools.run_pipeline report --project <abs> --run <id>`` writes ``report.md``, ``report.json`` and
``questions-for-analyst.md`` into the run directory.  The report holds step names, times, sizes, counts and codes;
it never copies code, specification text, cases or model answers.  Times are the machine clock of ``observed_at``.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

GUARANTEE = "без кода, текста спеки, кейсов и ответов модели; есть пути тестовых файлов, имена методов и коды"
CLOCK_NOTE = "время по часам машины, не проверяется"
NO_QUESTIONS = "вопросов нет\n"
SEPARATOR = " — "
_ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


# --- questions for the analyst ----------------------------------------------------------------

def analyst_questions(warnings: Iterable[Any]) -> str:
    """Group the four-field gap warnings (context-artifact-contract.md) by requirement; the rest goes to «Прочее»."""
    groups: dict[str, list[list[str]]] = {}
    loose: list[str] = []
    for warning in warnings:
        text = " ".join(str(warning).split())
        parts = [part.strip() for part in text.split(SEPARATOR)]
        if len(parts) == 4 and all(parts):
            groups.setdefault(parts[0].split(" ", 1)[0], []).append(parts)
        elif text:
            loose.append(text)
    if not groups and not loose:
        return NO_QUESTIONS
    lines = ["# Вопросы аналитику", ""]
    for requirement, items in groups.items():
        lines += [f"## {requirement}", ""]
        for source, missing, blocked, question in items:
            lines += [f"- Требование и источник: {source}", f"  - Не хватает: {missing}",
                      f"  - Блокирует: {blocked}", f"  - Вопрос: {question}"]
        lines.append("")
    if loose:
        lines += ["## Прочее", "", *(f"- {text}" for text in loose), ""]
    return "\n".join(lines)


# --- model calls --------------------------------------------------------------------------------

def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _seconds(start: Any, end: Any) -> float | None:
    first, last = _time(start), _time(end)
    return round((last - first).total_seconds(), 3) if first and last else None


def model_calls(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per ``MODEL_REQUESTED``: the time to its response and the transport attempts of the response."""
    rows = []
    for request in (event for event in events if event.get("event_type") == "MODEL_REQUESTED"):
        response = next((
            event for event in events
            if event.get("event_type") == "MODEL_RESPONSE_RECEIVED" and event["seq"] > request["seq"]
            and event.get("attempt_id") == request.get("attempt_id") and event.get("stage_instance_id") == request.get("stage_instance_id")
        ), None)
        rows.append({
            "stage": request.get("stage_instance_id"), "attempt_id": request.get("attempt_id"),
            "requested_at": request.get("observed_at"), "received_at": response.get("observed_at") if response else None,
            "duration_sec": _seconds(request.get("observed_at"), response.get("observed_at")) if response else None,
            "transport_attempts": response.get("transport_attempts") if response else None,
            "request_digest": request.get("artifact_digest"),
        })
    return rows


def _digest_sizes(root: Path) -> dict[str, int]:
    return {path.stem: path.stat().st_size for path in root.rglob("*.json") if len(path.stem) == 64}


def _size(project: Path, root: Path, call: dict[str, Any], sizes: Mapping[str, int]) -> None:
    from tools import pilot_state as pilot
    from tools.step_size import TOKENS_PER_BYTE, role_files, step_budget

    inputs: list[str] = []
    try:
        request = pilot.read_model_request(root, str(call["attempt_id"]), str(call["stage"]), str(call["request_digest"]))
        inputs = list(request.get("input_digests") or [])
    except (KeyError, OSError, TypeError, ValueError):
        pass
    found = [sizes[digest.removeprefix("sha256:")] for digest in inputs if digest.removeprefix("sha256:") in sizes]
    role = str(call["stage"]).split(":", 1)[0]
    try:
        role_bytes = sum(path.stat().st_size for path in role_files(role)) if role in _ROLES else 0
    except ValueError:
        role_bytes = 0
    total = sum(found) + role_bytes
    budget = step_budget(project)
    call.update(input_bytes=sum(found), role_bytes=role_bytes, unresolved_inputs=len(inputs) - len(found),
                tokens_est=round(total * TOKENS_PER_BYTE), budget_bytes=budget, fits=total <= budget)


# --- the report ---------------------------------------------------------------------------------

def _json_at(root: Path, digest: Any) -> Any:
    if not isinstance(digest, str):
        return None
    for path in root.rglob(digest.removeprefix("sha256:") + ".json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def build_report(project: Path, run_id: str) -> tuple[dict[str, Any], list[Any]]:
    """The report and the marker warnings of the run (the warnings feed only the analyst questions)."""
    from tools import pilot_state as pilot
    from tools.run_pipeline import _durable_status_facts

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    run, state = pilot.read_run(root), pilot.derive_state(root)
    events = list(state["events"])
    attempt = state["attempts"][-1] if state["attempts"] else None
    attempt_id = attempt["attempt_id"] if attempt else None
    status = _durable_status_facts(project, root, stage="report")

    sizes = _digest_sizes(root)
    calls = model_calls(events)
    for call in calls:
        _size(project, root, call, sizes)

    terminal: Mapping[str, Any] = {}
    if attempt and attempt["state"] == "TERMINAL":
        try:
            terminal = pilot.read_terminal_result(root, attempt_id)
        except (OSError, KeyError, TypeError, ValueError):
            terminal = {}
    evidence = terminal.get("evidence") or {}
    execution: Mapping[str, Any] = {}
    if attempt_id:
        try:
            execution = pilot.read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        except (KeyError, OSError, TypeError, ValueError):
            execution = {}

    responses = [event for event in events if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    marker = next((event for event in responses if event.get("stage_instance_id") == "context-marker:baseline"), None)
    requirements = None
    if marker:
        try:
            artifact = pilot.read_model_stage_artifact(root, marker["attempt_id"], "context-marker:baseline", marker["artifact_digest"])["artifact"]
            requirements = len(artifact["artifacts"]["analytics_documentation"]["requirements"])
        except (KeyError, OSError, TypeError, ValueError):
            requirements = None
    cases = None
    candidate = next((event for event in reversed(events) if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"), None)
    document = _json_at(root, candidate.get("artifact_digest")) if candidate else None
    if isinstance(document, Mapping) and isinstance(document.get("test_cases"), list):
        cases = len(document["test_cases"])

    if terminal.get("review_independence") == "SELF":
        review = "ревью в той же сессии (SELF), принято по согласию человека"
    elif evidence.get("reviewer_isolation_state") is not None:
        review = "ревью в свежем контексте"
    else:
        review = None
    report = {
        "schema_version": "1.0.0", "run_id": run["manifest"]["run_id"], "policy_profile": run["manifest"]["policy_profile"],
        "guarantee": GUARANTEE, "clock": CLOCK_NOTE,
        "started_at": events[0].get("observed_at") if events else None,
        "finished_at": events[-1].get("observed_at") if events else None,
        "duration_sec": _seconds(events[0].get("observed_at"), events[-1].get("observed_at")) if events else None,
        "steps": [{"seq": event["seq"], "event": event["event_type"], "stage": event.get("stage_instance_id"),
                   "batch": event.get("batch_id"), "at": event.get("observed_at")} for event in events],
        "model_calls": calls,
        "stops": {"stop_reason": status.get("stop_reason"), "reason_code": terminal.get("reason_code"),
                  "evidence_errors": sorted({row["code"] for row in status.get("evidence_errors") or []})},
        "counts": {
            "requirements": requirements,
            "batches": len({event.get("stage_instance_id") for event in responses if str(event.get("stage_instance_id")).startswith("tc-generator:")}),
            "cases": cases, "review_verdict": evidence.get("authoritative_verdict"),
            "automation": evidence.get("automation_accepted"),
            "execution": execution.get("verdict") or terminal.get("verification"),
            "execution_duration_sec": (execution.get("stats") or {}).get("duration_sec"),
            "completion": terminal.get("completion"), "coverage": terminal.get("coverage"), "accepted": terminal.get("accepted"),
        },
        "review": review, "reviewer_isolation_state": evidence.get("reviewer_isolation_state"),
        "quarantine": [{key: row[key] for key in ("file", "method", "ref")} for row in status.get("quarantine") or []],
        "quarantine_unresolved": "QUARANTINE_UNRESOLVED" if status.get("quarantine_unresolved") else None,
        "environment": {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
                        "adapter": (execution.get("execution") or {}).get("adapter_id")},
    }
    return report, list(status.get("warnings") or [])


def _dash(value: Any) -> str:
    return "—" if value is None else str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        f"# Отчёт прогона {report['run_id']}", "", f"Гарантия: {report['guarantee']}.", f"Время: {report['clock']}.", "",
        f"- Профиль: {report['policy_profile']}", f"- Начало: {_dash(report['started_at'])}", f"- Конец: {_dash(report['finished_at'])}",
        f"- Длительность, с: {_dash(report['duration_sec'])}", f"- Ревью: {_dash(report['review'])}", "",
        "## Числа", "", *(f"- {key}: {_dash(value)}" for key, value in counts.items()), "",
        "## Вызовы модели", "", "| Стадия | Начало | Длительность, с | Попытки | Байты входа | Токены (оценка) | Бюджет, Б | Влезает |", "|---|---|---|---|---|---|---|---|",
        *(f"| {_dash(c['stage'])} | {_dash(c['requested_at'])} | {_dash(c['duration_sec'])} | {_dash(c['transport_attempts'])} | "
          f"{_dash(c.get('input_bytes'))}+{_dash(c.get('role_bytes'))} | {_dash(c.get('tokens_est'))} | {_dash(c.get('budget_bytes'))} | {_dash(c.get('fits'))} |"
          for c in report["model_calls"]), "",
        "## Остановки и ошибки", "", *(f"- {key}: {_dash(value) if not isinstance(value, list) else (', '.join(value) or '—')}" for key, value in report["stops"].items()), "",
    ]
    if report["quarantine"] or report["quarantine_unresolved"]:
        lines += ["## Карантин", "", *(f"- {row['file']} :: {row['method']} ({row['ref']})" for row in report["quarantine"])]
        lines += [f"- {report['quarantine_unresolved']}"] if report["quarantine_unresolved"] else []
        lines.append("")
    lines += ["## Шаги", "", "| # | Событие | Стадия | Время |", "|---|---|---|---|",
              *(f"| {s['seq']} | {s['event']} | {_dash(s['stage'])} | {_dash(s['at'])} |" for s in report["steps"]), "",
              "## Окружение", "", *(f"- {key}: {_dash(value)}" for key, value in report["environment"].items()), ""]
    return "\n".join(lines)


def write_report(project: Path, run_id: str) -> list[str]:
    from tools.confined_output import atomic_write_confined_bytes_at_root

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    report, warnings = build_report(project, run_id)
    outputs = {
        "report.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        "report.md": render_markdown(report).encode("utf-8"),
        "questions-for-analyst.md": analyst_questions(warnings).encode("utf-8"),
    }
    written = []
    for name, data in outputs.items():
        atomic_write_confined_bytes_at_root(project, root, root / name, data)
        written.append(str(root / name))
    return written


def write_questions_next_to(output_dir: Path, run_root: Path) -> tuple[str | None, str | None]:
    """Install ``questions-for-analyst.md`` beside a published case set the way the set itself is installed.

    A complete temporary file in the same directory is hard-linked to the target, so the write never follows a
    link and never replaces another file.  Returns ``(path, None)`` or ``(None, warning code)``; it never raises.
    """
    import os
    import tempfile

    from tools.run_pipeline import _durable_status_facts

    try:
        run_root = Path(run_root).resolve()
        warnings = _durable_status_facts(run_root.parents[1], run_root, stage="publish").get("warnings") or []
        data = analyst_questions(warnings).encode("utf-8")
    except Exception:  # noqa: BLE001 - the case set is already published; the report still writes the file
        return None, "ANALYST_QUESTIONS_UNREADABLE"
    temporary: Path | None = None
    try:
        directory = Path(output_dir).resolve()
        if not directory.is_dir():
            return None, "ANALYST_QUESTIONS_WRITE_FAILED"
        target = directory / "questions-for-analyst.md"
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                return None, "ANALYST_QUESTIONS_CONFLICT"
        return str(target), None
    except OSError:
        return None, "ANALYST_QUESTIONS_WRITE_FAILED"
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit("use: python -m tools.run_pipeline report --project <abs> --run <id>")
