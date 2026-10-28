"""FaT-6: a run report that may leave the workplace, and the analyst questions of the run (only reads the journal).

``python -m tools.run_pipeline report --project <abs> --run <id>`` writes ``report.md``, ``report.json`` and
``questions-for-analyst.md`` into the run directory.  The report holds step names, times, sizes, counts and codes;
it never copies code, specification text, cases or model answers.  Times are the machine clock of ``observed_at``.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

GUARANTEE = ("без кода, текста спеки, кейсов и ответов модели; есть пути тестовых файлов, имена методов, коды "
             "и ID требований, кейсов, параметров и классов")
CLOCK_NOTE = "время по часам машины, не проверяется"
NO_QUESTIONS = "вопросов нет\n"
SEPARATOR = " — "
REVIEW_STAGE = "tc-reviewer:canonical"
_NOTE_VERDICTS = frozenset({"ПРИНЯТО", "AUTO_FIX_APPLIED"})
_NOTE_SEVERITIES = ("WARNING", "INFO")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


# --- questions for the analyst ----------------------------------------------------------------

def analyst_questions(warnings: Iterable[Any], notes: Sequence[Mapping[str, Any]] = ()) -> str:
    """Group the four-field gap warnings (context-artifact-contract.md) by requirement; the rest goes to «Прочее».

    FaT-RN: ``notes`` — the WARNING/INFO findings of the accepted review, with their text, under «Замечания ревью».
    """
    groups: dict[str, list[list[str]]] = {}
    loose: list[str] = []
    for warning in warnings:
        text = " ".join(str(warning).split())
        parts = [part.strip() for part in text.split(SEPARATOR)]
        if len(parts) == 4 and all(parts):
            groups.setdefault(parts[0].split(" ", 1)[0], []).append(parts)
        elif text:
            loose.append(text)
    if not groups and not loose and not notes:
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
    if notes:
        lines += ["## Замечания ревью", "", "Ревью приняло кейсы; это мелочи, кейсы код не менял.", ""]
        lines += [f"- [{row['severity']} {row['code']}] {', '.join(row['related_ids'])}: {' '.join(str(row['message']).split())}"
                  for row in notes]
        lines.append("")
    return "\n".join(lines)


# --- notes of the accepted review (FaT-RN) ------------------------------------------------------

def _document_ids(value: Any, found: set[str] | None = None) -> set[str]:
    """Every identifier of a canonical document: values of ``*id`` keys and items of ``*_ids`` lists."""
    found = set() if found is None else found
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, str) and (key == "id" or str(key).endswith("_id")):
                found.add(item)
            elif isinstance(item, list) and str(key).endswith("_ids"):
                found.update(entry for entry in item if isinstance(entry, str))
            _document_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _document_ids(item, found)
    return found


def review_notes(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """FaT-RN: the WARNING/INFO findings of the review that selected the effective document; only reads the journal.

    ``items`` may leave the workplace: a code only when it is an identifier, related IDs only those of the effective
    document (the others are counted).  ``texts`` keep the model text for the analyst questions only.  ``path`` is the
    journal-bound reviewer answer.  The code never changes a case or recomputes the verdict.
    """
    from tools import pilot_state as pilot

    root = Path(run_root)
    empty: dict[str, Any] = {"count": 0, "path": None, "items": [], "texts": []}
    responses = [row for row in pilot.derive_state(root)["events"] if row.get("attempt_id") == attempt_id
                 and row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == REVIEW_STAGE]
    if not responses:
        return empty
    try:
        publication = pilot.read_model_stage_artifact(root, attempt_id, REVIEW_STAGE, str(responses[-1]["artifact_digest"]))
        report = publication["artifact"]["artifacts"]["validation_report"]
        findings = [row for row in report.get("findings") or [] if row.get("severity") in _NOTE_SEVERITIES]
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return empty
    if report.get("verdict") not in _NOTE_VERDICTS or not findings:
        return empty
    try:
        known = _document_ids(pilot.read_effective_canonical(root, attempt_id)["document"])
    except (KeyError, OSError, TypeError, ValueError):
        known = set()  # no effective document yet: no ID is confirmed, all are counted as dropped
    items, texts = [], []
    for row in findings:
        related = [str(value) for value in row.get("related_ids") or []]
        kept = [value for value in related if value in known]
        code = row.get("code") if isinstance(row.get("code"), str) and _SAFE_CODE.fullmatch(row["code"]) else None
        items.append({"severity": row["severity"], "code": code, "related_ids": kept, "dropped_ids": len(related) - len(kept)})
        texts.append({"severity": row["severity"], "code": code or "—", "related_ids": kept or ["—"], "message": row.get("message", "")})
    return {"count": len(items), "path": str(root / publication["path"]), "items": items, "texts": texts}


# --- model calls --------------------------------------------------------------------------------

def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _seconds(start: Any, end: Any) -> float | None:
    first, last = _time(start), _time(end)
    return round((last - first).total_seconds(), 3) if first and last else None


STEP_TIME_NOTE = ("время шага модели — от предыдущего события журнала до ответа: агент пишет MODEL_REQUESTED "
                  "уже с готовым ответом, поэтому «запрос → ответ» в журнале не показывает работу")


def model_calls(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per ``MODEL_REQUESTED``: the step time (FaT-6b: from the previous journal event to the response) and attempts."""
    rows = []
    ordered = sorted(events, key=lambda event: event["seq"])
    for request in (event for event in ordered if event.get("event_type") == "MODEL_REQUESTED"):
        previous = next((event for event in reversed(ordered) if event["seq"] < request["seq"]), None)
        response = next((
            event for event in events
            if event.get("event_type") == "MODEL_RESPONSE_RECEIVED" and event["seq"] > request["seq"]
            and event.get("attempt_id") == request.get("attempt_id") and event.get("stage_instance_id") == request.get("stage_instance_id")
        ), None)
        rows.append({
            "stage": request.get("stage_instance_id"), "attempt_id": request.get("attempt_id"),
            "requested_at": request.get("observed_at"), "received_at": response.get("observed_at") if response else None,
            "step_sec": _seconds((previous or request).get("observed_at"), response.get("observed_at")) if response else None,
            "journal_request_to_response_sec": _seconds(request.get("observed_at"), response.get("observed_at")) if response else None,
            "transport_attempts": response.get("transport_attempts") if response else None,
            "request_digest": request.get("artifact_digest"),
            "response_digest": response.get("artifact_digest") if response else None,
        })
    return rows


def digest_index(root: Path) -> dict[str, int]:
    """FaT-6b: bytes a model reads behind a digest — by file name, file bytes, a receipt's own ``digest`` (also nested
    one level), and the canonical document digest; a context receipt counts its ``byte_count``, not its JSON."""
    import hashlib

    from tools.canonical_document import document_sha256

    index: dict[str, int] = {}
    for path in sorted(Path(root).rglob("*.json")):
        try:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            continue
        size = int(value["byte_count"]) if isinstance(value, Mapping) and isinstance(value.get("byte_count"), int) and "files" in value else len(raw)
        keys = [path.stem, hashlib.sha256(raw).hexdigest()]
        if isinstance(value, Mapping):
            if isinstance(value.get("digest"), str):
                keys.append(value["digest"].removeprefix("sha256:"))
            if isinstance(value.get("test_cases"), list):
                try:
                    keys.append(document_sha256(dict(value)).removeprefix("sha256:"))
                except Exception:  # noqa: BLE001 - a non-canonical document only lacks this key
                    pass
            for nested in value.values():
                if isinstance(nested, Mapping) and isinstance(nested.get("digest"), str):
                    index.setdefault(nested["digest"].removeprefix("sha256:"),
                                     len(json.dumps(nested, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
        for key in keys:
            index.setdefault(key, size)
    return index


def input_bytes(index: Mapping[str, int], digests: Sequence[str]) -> tuple[int, int]:
    """``(resolved bytes, unresolved count)`` of a model request's input digests."""
    found = [index[str(digest).removeprefix("sha256:")] for digest in digests if str(digest).removeprefix("sha256:") in index]
    return sum(found), len(digests) - len(found)


def _size(project: Path, root: Path, call: dict[str, Any], sizes: Mapping[str, int]) -> None:
    from tools import pilot_state as pilot
    from tools.step_size import TOKENS_PER_BYTE, output_warning, role_files, step_budget

    inputs: list[str] = []
    by_code = False
    try:
        request = pilot.read_model_request(root, str(call["attempt_id"]), str(call["stage"]), str(call["request_digest"]))
        inputs = list(request.get("input_digests") or [])
        # FaT-6b: a controller step (the FaT-5 merge of the marker parts) is code, not a model call.
        by_code = str(request.get("model_id") or "").startswith("controller:")
    except (KeyError, OSError, TypeError, ValueError):
        pass
    resolved, unresolved = input_bytes(sizes, inputs)
    # FaT-O: the answer bytes are the journal-bound response artifact behind its digest
    answer = sizes.get(str(call.get("response_digest") or "").removeprefix("sha256:"))
    call.update(output_bytes=answer, output_large=None if answer is None or by_code else output_warning(project, answer) is not None)
    role = str(call["stage"]).split(":", 1)[0]
    try:
        role_bytes = sum(path.stat().st_size for path in role_files(role)) if role in _ROLES else 0
    except ValueError:
        role_bytes = 0
    total = resolved + role_bytes
    budget = step_budget(project)
    call.update(input_bytes=resolved, role_bytes=role_bytes, unresolved_inputs=unresolved, size_complete=unresolved == 0,
                by="code" if by_code else "model")
    if by_code:  # code reads no prompt: no token estimate and no comparison with the step budget of a model
        call.update(role_bytes=0, tokens_est=None, budget_bytes=None, fits=None)
    else:
        call.update(tokens_est=round(total * TOKENS_PER_BYTE), budget_bytes=budget, fits=total <= budget)


_STEP_KEYS = ("step", "bytes", "input_bytes", "role_bytes", "role", "files", "tokens_est", "budget_bytes", "fits", "warning", "at")


def step_sizes(root: Path) -> list[dict[str, Any]]:
    """FaT-RV: the ``tools.step_size --run`` measurements in order; an unreadable line is skipped, not fatal."""
    from tools.step_size import RECORD

    try:
        lines = (Path(root) / RECORD).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, Mapping) and isinstance(value.get("step"), str):
            rows.append({key: value.get(key) for key in _STEP_KEYS})
    return rows


def budget_warnings(calls: Iterable[Mapping[str, Any]], sizes: Iterable[Mapping[str, Any]]) -> list[str]:
    """FaT-RV: ``STEP_OVER_BUDGET`` when a measured step or a journaled model call is over the step budget."""
    from tools.step_size import OVER_BUDGET

    over = any(call.get("by") == "model" and call.get("fits") is False for call in calls) or any(row.get("fits") is False for row in sizes)
    return [OVER_BUDGET] if over else []


_WARNING_TEXT = {"STEP_OVER_BUDGET": "вход шага больше бюджета, прогон шёл дальше",
                 "STEP_OUTPUT_LARGE": "ответ модели больше порога limits.step_output_warn_bytes — только замер, деление выключено",
                 "CV_DATA_MISMATCH": "значения входов шага не названы в его действии и данных (подсказка ревьюеру)"}


def data_mismatch(document: Any) -> list[dict[str, Any]]:
    """FaT-CV: the steps with ``CV_DATA_MISMATCH`` by ID and count only — the values are case data and stay out."""
    from tools.review_view import data_mismatches

    try:
        return [{key: hint[key] for key in ("case_id", "step_id", "count")} for hint in data_mismatches(document)]
    except (KeyError, TypeError, AttributeError):
        return []


# --- the report ---------------------------------------------------------------------------------

def case_document(root: Path, attempt_id: str | None) -> Mapping[str, Any] | None:
    """FaT-6b: the selected case document — the effective canonical of the attempt, else the newest case-sets/r*."""
    from tools import pilot_state as pilot

    if attempt_id:
        try:
            effective = pilot.read_effective_canonical_if_present(root, attempt_id)
            if effective and isinstance(effective.get("document"), Mapping):
                return effective["document"]
        except (KeyError, OSError, TypeError, ValueError):
            pass
    revisions = sorted((path for path in (Path(root) / "case-sets").glob("r*") if path.is_dir()),
                       key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else 0)
    for revision in reversed(revisions):
        for path in sorted(revision.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, Mapping) and isinstance(value.get("test_cases"), list):
                return value
    return None


def _json_at(root: Path, digest: Any) -> Any:
    if not isinstance(digest, str):
        return None
    for path in root.rglob(digest.removeprefix("sha256:") + ".json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


UNUSED_TEST_DESIGN = "генератор не заполнил технику и параметры — подсказки тест-дизайна не строились"


def test_design_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """FaT-TD: technique counts and code hints by ID only — never case text; ``used`` false says the fields were empty."""
    from tools.test_design_hints import capped, design_hints, technique_counts, uses_test_design

    hints = design_hints(document)
    counts: dict[str, int] = {}
    for hint in hints:
        counts[hint["code"]] = counts.get(hint["code"], 0) + 1
    return {"used": uses_test_design(document), "techniques": technique_counts(document), "hints": counts, "gaps": capped(hints)}


def render_test_design(summary: Mapping[str, Any]) -> str:
    lines = ["## Тест-дизайн", ""]
    if not summary["used"]:
        return "\n".join(lines + [f"- {UNUSED_TEST_DESIGN}"])
    lines.append("- Техники: " + ", ".join(f"{name} {count}" for name, count in summary["techniques"].items()))
    lines.append("- Подсказки кода: " + (", ".join(f"{code} {count}" for code, count in summary["hints"].items()) or "нет"))
    lines += [f"  - {gap['code']} {gap.get('requirement_id', '')} {gap.get('parameter', '')} {gap['detail']}".rstrip() for gap in summary["gaps"]]
    return "\n".join(lines)


def expectation_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """FaT-Q: hints on vague expectations of the cases by ID only, on any document."""
    from tools.test_design_hints import capped, expectation_hints

    hints = expectation_hints(document)
    counts: dict[str, int] = {}
    for hint in hints:
        counts[hint["code"]] = counts.get(hint["code"], 0) + 1
    return {"hints": counts, "gaps": capped(hints)}


def render_expectations(summary: Mapping[str, Any]) -> str:
    lines = ["## Ожидания кейсов", "", "- Подсказки кода: " + (", ".join(f"{code} {count}" for code, count in summary["hints"].items()) or "нет")]
    lines += [f"  - {gap['code']} {gap.get('case_id', '')} {gap.get('step_id', '')} {gap['detail']}".rstrip() for gap in summary["gaps"]]
    return "\n".join(lines)


def automation_artifact(root: Path, events: Sequence[Mapping[str, Any]]) -> Any:
    """The newest ``tc-to-autotest`` answer of the run, by its journal digest."""
    response = next((event for event in reversed(events) if event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                     and str(event.get("stage_instance_id")).startswith("tc-to-autotest:")), None)
    return _json_at(root, response.get("artifact_digest")) if response else None


def autotest_summary(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """FaT-Q: weak assertions of the generated tests — file ID, line, rule and ASSERT ID, never the code."""
    from tools.autotest_hints import autotest_hints, capped_autotest_hints, counts

    hints = autotest_hints(artifact)
    return {"hints": counts(hints), "gaps": capped_autotest_hints(hints)}


def render_autotest_assertions(summary: Mapping[str, Any]) -> str:
    lines = ["## Слабые утверждения автотестов", "", "- Подсказки кода: " + (", ".join(f"{code} {count}" for code, count in summary["hints"].items()) or "нет")]
    lines += [f"  - {gap['code']} {gap['detail']}" if "file_id" not in gap else
              f"  - {gap['code']} {gap['file_id']}:{gap['line']} {gap['rule']} {gap.get('assertion_id', '')}".rstrip() for gap in summary["gaps"]]
    return "\n".join(lines)


def _zephyr_import(root: Path) -> Any:
    """FaT-7: only the template path and digest of the import CSV, never its values."""
    try:
        record = json.loads((root / "zephyr-import.json").read_text(encoding="utf-8"))
        return {key: record[key] for key in ("template_path", "template_digest", "columns_added", "rows")}
    except (OSError, ValueError, KeyError, TypeError):
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

    sizes = digest_index(root)
    calls = model_calls(events)
    for call in calls:
        _size(project, root, call, sizes)
    measured = step_sizes(root)

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
    document = case_document(root, attempt_id) or (_json_at(root, candidate.get("artifact_digest")) if candidate else None)
    if isinstance(document, Mapping) and isinstance(document.get("test_cases"), list):
        cases = len(document["test_cases"])
    automation = automation_artifact(root, events)
    mismatched = data_mismatch(document) if cases is not None else []
    notes = review_notes(root, attempt_id) if attempt_id else {"count": 0, "items": []}

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
        "model_calls": calls, "step_sizes": measured,
        "warnings": budget_warnings(calls, measured) + (["STEP_OUTPUT_LARGE"] if any(call.get("output_large") for call in calls) else [])
                    + (["CV_DATA_MISMATCH"] if mismatched else []), "data_mismatch": mismatched,
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
        "review_notes": {"count": notes["count"], "items": notes["items"]},
        "quarantine": [{key: row[key] for key in ("file", "method", "ref")} for row in status.get("quarantine") or []],
        "quarantine_unresolved": "QUARANTINE_UNRESOLVED" if status.get("quarantine_unresolved") else None,
        "zephyr_import": _zephyr_import(root),
        "test_design": test_design_summary(document) if isinstance(document, Mapping) else None,
        "expectations": expectation_summary(document) if isinstance(document, Mapping) else None,
        "autotest_assertions": autotest_summary(automation) if isinstance(automation, Mapping) else None,
        "environment": {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
                        "adapter": (execution.get("execution") or {}).get("adapter_id")},
    }
    return report, list(status.get("warnings") or [])


def _size_cell(call: Mapping[str, Any]) -> str:
    cell = f"{_dash(call.get('input_bytes'))}+{_dash(call.get('role_bytes'))}"
    if not call.get("size_complete", True):
        cell += f" (размер неполный: не найдено {call.get('unresolved_inputs')})"
    return cell


def _tokens_cell(call: Mapping[str, Any]) -> str:
    return "код" if call.get("by") == "code" else _dash(call.get("tokens_est"))


def _dash(value: Any) -> str:
    return "—" if value is None else str(value)


def _note_line(row: Mapping[str, Any]) -> str:
    dropped = f" (отброшено ID: {row['dropped_ids']})" if row["dropped_ids"] else ""
    return f"- {row['severity']} {_dash(row['code'])}: {', '.join(row['related_ids']) or '—'}{dropped}"


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        f"# Отчёт прогона {report['run_id']}", "", f"Гарантия: {report['guarantee']}.", f"Время: {report['clock']}.", "",
        f"- Профиль: {report['policy_profile']}", f"- Начало: {_dash(report['started_at'])}", f"- Конец: {_dash(report['finished_at'])}",
        f"- Длительность, с: {_dash(report['duration_sec'])}", f"- Ревью: {_dash(report['review'])}", "",
        "## Числа", "", *(f"- {key}: {_dash(value)}" for key, value in counts.items()), "",
        "## Вызовы модели", "", f"Время шага: {STEP_TIME_NOTE}.", "",
        "| Стадия | Запрос в журнале | Время шага, с | Попытки | Байты входа | Токены (оценка) | Бюджет, Б | Влезает | Байты ответа |", "|---|---|---|---|---|---|---|---|---|",
        *(f"| {_dash(c['stage'])} | {_dash(c['requested_at'])} | {_dash(c['step_sec'])} | {_dash(c['transport_attempts'])} | "
          f"{_size_cell(c)} | {_tokens_cell(c)} | {_dash(c.get('budget_bytes'))} | {'—' if c.get('by') == 'code' else _dash(c.get('fits'))} | "
          f"{_dash(c.get('output_bytes'))}{' (большой)' if c.get('output_large') else ''} |"
          for c in report["model_calls"]), "",
        "## Предупреждения", "", *(f"- {code}: {_WARNING_TEXT.get(code, code)}" for code in report["warnings"]),
        *(f"  - {row['case_id']} {row['step_id']}: {row['count']}" for row in report.get("data_mismatch") or []),
        *([] if report["warnings"] else ["- нет"]), "",
        *(["## Замечания ревью", "", "Ревью приняло кейсы с замечаниями-мелочами; текст — в questions-for-analyst.md.", "",
           *(_note_line(row) for row in report["review_notes"]["items"]), ""]
          if (report.get("review_notes") or {}).get("count") else []),
        "## Размер входа шагов", "", "Замер `tools.step_size --run` перед вызовом.", "",
        "| Шаг | Байты входа | Роль, Б | Токены (оценка) | Бюджет, Б | Влезает | Время |", "|---|---|---|---|---|---|---|",
        *(f"| {s['step']} | {_dash(s['bytes'])} | {_dash(s['role_bytes'])} | {_dash(s['tokens_est'])} | {_dash(s['budget_bytes'])} | "
          f"{_dash(s['fits'])} | {_dash(s['at'])} |" for s in report["step_sizes"]), "",
        "## Остановки и ошибки", "", *(f"- {key}: {_dash(value) if not isinstance(value, list) else (', '.join(value) or '—')}" for key, value in report["stops"].items()), "",
    ]
    if report.get("test_design"):
        lines += [render_test_design(report["test_design"]), ""]
    if report.get("expectations"):
        lines += [render_expectations(report["expectations"]), ""]
    if report.get("autotest_assertions"):
        lines += [render_autotest_assertions(report["autotest_assertions"]), ""]
    if report["quarantine"] or report["quarantine_unresolved"]:
        lines += ["## Карантин", "", *(f"- {row['file']} :: {row['method']} ({row['ref']})" for row in report["quarantine"])]
        lines += [f"- {report['quarantine_unresolved']}"] if report["quarantine_unresolved"] else []
        lines.append("")
    lines += ["## Шаги", "", "| # | Событие | Стадия | Время |", "|---|---|---|---|",
              *(f"| {s['seq']} | {s['event']} | {_dash(s['stage'])} | {_dash(s['at'])} |" for s in report["steps"]), "",
              "## Окружение", "", *(f"- {key}: {_dash(value)}" for key, value in report["environment"].items()), ""]
    return "\n".join(lines)


def write_report(project: Path, run_id: str) -> list[str]:
    """Build and write the report inside one closure_cache (FaT-6b): each journal and receipt is checked once, not per read."""
    from tools.pilot_state import closure_cache

    with closure_cache():
        return _write_report(project, run_id)


def _write_report(project: Path, run_id: str) -> list[str]:
    from tools.confined_output import atomic_write_confined_bytes_at_root

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    report, warnings = build_report(project, run_id)
    outputs = {
        "report.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        "report.md": render_markdown(report).encode("utf-8"),
        "questions-for-analyst.md": analyst_questions(warnings, _attempt_notes(root)["texts"]).encode("utf-8"),
    }
    written = []
    for name, data in outputs.items():
        atomic_write_confined_bytes_at_root(project, root, root / name, data)
        written.append(str(root / name))
    return written


def _attempt_notes(run_root: Path) -> dict[str, Any]:
    from tools import pilot_state as pilot

    attempts = pilot.derive_state(Path(run_root))["attempts"]
    return review_notes(run_root, attempts[-1]["attempt_id"]) if attempts else {"count": 0, "texts": []}


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
        data = analyst_questions(warnings, _attempt_notes(run_root)["texts"]).encode("utf-8")
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
