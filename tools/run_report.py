"""A run report without project code or case texts, and the analyst questions of the run (only reads the journal).

``python -m tools.run_pipeline report --project <abs> --run <id>`` writes ``report.md``, ``report.json`` and
``questions-for-analyst.md`` into the run directory.  The report holds step names, times, sizes, counts and codes;
it never copies code, specification text, cases or model answers.  Times are the machine clock of ``observed_at``.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools import execution_gate, output_guard

GUARANTEE = ("без кода, текста спеки, кейсов и ответов модели; есть пути тестовых файлов, имена методов, коды "
             "и ID требований, кейсов, параметров и классов")
CLOCK_NOTE = "время по часам машины, не проверяется"
HUMAN_RESULT_NOTE = "результат запуска предоставлен человеком, пакет его не воспроизводил"
NO_QUESTIONS = "вопросов нет\n"
SEPARATOR = " — "
# the review kind → its stage prefix, the report key inside the answer and the analyst-questions title
_REVIEWS = {"cases": ("tc-reviewer:", "validation_report", "Замечания ревью", "Ревью приняло кейсы; это мелочи, кейсы код не менял."),
            "autotests": ("autotest-reviewer:", "autotest_review", "Замечания ревью автотестов",
                          "Ревью приняло автотесты; это мелочи, автотесты код не менял.")}
_NOTE_VERDICTS = frozenset({"ПРИНЯТО", "AUTO_FIX_APPLIED"})
_NOTE_SEVERITIES = ("WARNING", "INFO")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


# --- questions for the analyst ----------------------------------------------------------------

def analyst_questions(warnings: Iterable[Any], notes: Sequence[Mapping[str, Any]] = (),
                      autotest_notes: Sequence[Mapping[str, Any]] = (), imported: Mapping[str, Any] | None = None) -> str:
    """Group the four-field gap warnings (context-artifact-contract.md) by requirement; the rest goes to «Прочее».

    ``notes``/``autotest_notes`` — the WARNING/INFO findings of the accepted case/autotest review, with text.
    ``imported`` — the import summary: the document requirements without a case of the CSV, the cases outside
    the documents.
    """
    gap_rows = list((imported or {}).get("gaps") or [])
    outside = list((imported or {}).get("outside") or [])
    groups: dict[str, list[list[str]]] = {}
    loose: list[str] = []
    for warning in warnings:
        text = " ".join(str(warning).split())
        parts = [part.strip() for part in text.split(SEPARATOR)]
        if len(parts) == 4 and all(parts):
            groups.setdefault(parts[0].split(" ", 1)[0], []).append(parts)
        elif text:
            loose.append(text)
    if not groups and not loose and not notes and not autotest_notes and not gap_rows and not outside:
        return NO_QUESTIONS
    lines = ["# Вопросы аналитику", ""]
    if gap_rows:  # a requirement of the documents no imported case checks — no case is made for it
        lines += ["## Требования без кейса в CSV", "",
                  *(f"- {row['source_requirement_id']}: {row['text']} — в CSV нет кейса; нужен ли он?" for row in gap_rows), ""]
    if outside:
        lines += ["## Кейсы CSV вне документов", "",
                  *(f"- строка {row} — кейс не проверяет ни одно требование документов; оставить его?" for row in outside), ""]
    for requirement, items in groups.items():
        lines += [f"## {requirement}", ""]
        for source, missing, blocked, question in items:
            lines += [f"- Требование и источник: {source}", f"  - Не хватает: {missing}",
                      f"  - Блокирует: {blocked}", f"  - Вопрос: {question}"]
        lines.append("")
    if loose:
        lines += ["## Прочее", "", *(f"- {text}" for text in loose), ""]
    for kind, rows in (("cases", notes), ("autotests", autotest_notes)):
        if rows:
            lines += [f"## {_REVIEWS[kind][2]}", "", _REVIEWS[kind][3], ""]
            lines += [f"- [{row['severity']} {row['code']}] {', '.join(row['related_ids'])}: {' '.join(str(row['message']).split())}"
                      for row in rows]
            lines.append("")
    return "\n".join(lines)


# --- notes of the accepted review ------------------------------------------------------

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


def _automation_ids(root: Path, attempt_id: str, stage: str, before: int) -> set[str]:
    """IDs of the automation answer the autotest review read: the newest one of its revision before the review."""
    from tools import pilot_state as pilot

    answers = [row for row in pilot.derive_state(root)["events"] if row.get("attempt_id") == attempt_id and row["seq"] < before
               and row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == stage]
    try:
        return _document_ids(pilot.read_model_stage_artifact(root, attempt_id, stage, str(answers[-1]["artifact_digest"]))["artifact"]) if answers else set()
    except (KeyError, OSError, TypeError, ValueError):
        return set()


def review_notes(run_root: Path, attempt_id: str, review: str = "cases") -> dict[str, Any]:
    """The WARNING/INFO findings of the accepted review (``cases`` or the newest ``autotests``); only reads the journal.

    ``items`` may leave the workplace: a code only when it is an identifier, related IDs only those of the effective
    document, and for the autotest review also of the reviewed automation (the others are counted).  ``texts`` keep the model text for the analyst questions only.  ``path`` is the
    journal-bound reviewer answer.  The code never changes a case or recomputes the verdict.
    """
    from tools import pilot_state as pilot

    root = Path(run_root)
    empty: dict[str, Any] = {"count": 0, "path": None, "items": [], "texts": []}
    prefix, key = _REVIEWS[review][:2]
    responses = [row for row in pilot.derive_state(root)["events"] if row.get("attempt_id") == attempt_id
                 and row["event_type"] == "MODEL_RESPONSE_RECEIVED" and str(row.get("stage_instance_id")).startswith(prefix)]
    if not responses:
        return empty
    stage = str(responses[-1]["stage_instance_id"])
    try:
        publication = pilot.read_model_stage_artifact(root, attempt_id, stage, str(responses[-1]["artifact_digest"]))
        report = publication["artifact"]["artifacts"][key]
        findings = [row for row in report.get("findings") or [] if row.get("severity") in _NOTE_SEVERITIES]
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return empty
    if report.get("verdict") not in _NOTE_VERDICTS or not findings:
        return empty
    try:
        known = _document_ids(pilot.read_effective_canonical(root, attempt_id)["document"])
    except (KeyError, OSError, TypeError, ValueError):
        known = set()  # no effective document yet: no ID is confirmed, all are counted as dropped
    if review == "autotests":
        known |= _automation_ids(root, attempt_id, stage.replace("autotest-reviewer:", "tc-to-autotest:"), responses[-1]["seq"])
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


def test_level(project: Path, root: Path, attempt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The level of the autotests of the attempt's module (``in-process`` or ``stand``) and where it comes from — ``declared`` by
    ``test.level`` of ``.skillsrc``, ``derived`` by the old rule.  The source is the journal, not a file of the run folder: the module is
    ``attempt.module`` and the live ``.skillsrc`` counts only while its bytes are those of the attempt's journal-bound baseline
    (``pilot_state.frozen_skillsrc_matches``, the path ``step_size.run_limits`` takes); otherwise ``unconfirmed``, the value not said.
    None when the delivery has no execution, the run no attempt, or the module no test block."""
    from tools import pilot_state as pilot

    if not execution_gate.execution_available() or attempt is None:  # the cases-only delivery runs no autotests: nothing to say of their level
        return None
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, module_test_level, normalize_skillsrc

    if pilot.frozen_skillsrc_matches(project, root, attempt) is not True:
        return {"value": None, "source": "unconfirmed", "module": None}
    try:
        modules = normalize_skillsrc(load_skillsrc(Path(project) / ".skillsrc"))["modules"]
    except (OSError, SkillsrcError, ValueError, KeyError, TypeError):
        return {"value": None, "source": "unconfirmed", "module": None}
    module = next((item for item in modules if item.get("root") == attempt.get("module")), None)
    found = None if module is None else module_test_level(module)
    return None if found is None else {"value": found[0], "source": found[1], "module": module.get("id")}


def _test_level_line(level: Mapping[str, Any]) -> str:
    if level["source"] == "unconfirmed":
        return "Уровень автотестов: не подтверждён — .skillsrc изменён после scan (или не читается), уровень не назван"
    if level["source"] == "declared":
        return f"Уровень автотестов: {level['value']} (задан в .skillsrc)"
    return f"Уровень автотестов: {level['value']} (выведен из paths.context, а не задан; укажите test.level: {level['value']} в .skillsrc)"


def _seconds(start: Any, end: Any) -> float | None:
    first, last = _time(start), _time(end)
    return round((last - first).total_seconds(), 3) if first and last else None


STEP_TIME_NOTE = ("время шага модели — от предыдущего события журнала до ответа: агент пишет MODEL_REQUESTED "
                  "уже с готовым ответом, поэтому «запрос → ответ» в журнале не показывает работу")


def model_calls(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per ``MODEL_REQUESTED``: the step time (from the previous journal event to the response) and attempts."""
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


def _answer_bytes(root: Path, call: Mapping[str, Any]) -> int | None:
    """The stored answer behind ``response_digest`` — its journal slot, not a search."""
    digest = str(call.get("response_digest") or "")
    path = root / "model-stage-artifacts" / str(call.get("attempt_id")) / (digest.removeprefix("sha256:") + ".json")
    return path.stat().st_size if digest.startswith("sha256:") and path.is_file() else None


def _size(project: Path, root: Path, call: dict[str, Any], raw_sizes: Mapping[str, int] | None = None, *,
          state: Any = None, journals: dict[str, Any] | None = None, limits: Mapping[str, Any] | None = None) -> None:
    from tools import pilot_state as pilot
    from tools.step_size import TOKENS_PER_BYTE, input_size, output_warning, role_files, stage_inputs, step_budget

    inputs: list[str] = []
    by_code = False
    reuse = None
    try:
        request = pilot.read_model_request(root, str(call["attempt_id"]), str(call["stage"]), str(call["request_digest"]))
        inputs = list(request.get("input_digests") or [])
        # a controller step (the merge of the marker parts) is code, not a model call.
        by_code = str(request.get("model_id") or "").startswith("controller:")
        reuse = request.get("reuse")  # the answer of an earlier run, reused — no model call either
    except (KeyError, OSError, TypeError, ValueError):
        pass
    # the same files and bytes as step-inputs — from the journal, the map of existing tests measured, not written
    try:
        found = stage_inputs(root, str(call["stage"]), attempt_id=str(call["attempt_id"]), write_views=False, state=state, journals=journals)
        resolved, unresolved = input_size(found), len(found["unresolved"])
    except (KeyError, OSError, TypeError, ValueError):
        found, resolved, unresolved = None, 0, len(inputs)
    basis = None
    if str(call["stage"]).startswith(("tc-reviewer:", "tc-to-autotest:")) and inputs:
        # the view of the document when it is built, else the canonical JSON
        basis = "вид" if found and any(row.get("basis") == "вид" for row in found["files"]) else "канон"
    call["input_basis"] = basis
    call["composition"] = _composition(found, project, root) if found else None
    # the answer bytes are the journal-bound response artifact behind its digest
    answer = _answer_bytes(root, call)
    # the raw answer bytes when the command measured them, else the stored compact JSON
    raw = (raw_sizes or {}).get(str(call.get("response_digest") or ""))
    # the raw size is the agent's measurement (a file nothing in the journal binds): printed as such, never the ground of a warning —
    # STEP_OUTPUT_LARGE is judged by the stored answer, whose bytes the journal seals
    call["output_raw_bytes"] = raw
    call["output_tokens_est"] = None if (raw or answer) is None else round((raw or answer) * TOKENS_PER_BYTE)  # what the model wrote, as the input is counted
    call["output_basis"] = None if answer is None else "compact"
    call.update(output_bytes=answer, output_large=None if answer is None or by_code else output_warning(project, answer, limits) is not None)
    role = str(call["stage"]).split(":", 1)[0]
    try:
        role_bytes = sum(path.stat().st_size for path in role_files(role)) if role in _ROLES else 0
    except ValueError:
        role_bytes = 0
    total = resolved + role_bytes
    budget = step_budget(project, limits)
    call.update(input_bytes=resolved, role_bytes=role_bytes, unresolved_inputs=unresolved, size_complete=unresolved == 0,
                by="code" if by_code else "reuse" if isinstance(reuse, Mapping) else "model")
    if isinstance(reuse, Mapping):  # nothing was sent to a model; the input it would have read is the saving
        call.update(reused_from=reuse.get("run_id"), saved_bytes=resolved + role_bytes, output_large=None)
    if by_code or isinstance(reuse, Mapping):  # code reads no prompt: no token estimate and no comparison with the step budget of a model
        call.update(role_bytes=0, tokens_est=None, budget_bytes=None, fits=None)
    else:
        call.update(tokens_est=round(total * TOKENS_PER_BYTE), budget_bytes=budget, fits=total <= budget)


_STEP_KEYS = ("step", "bytes", "input_bytes", "role_bytes", "role", "files", "tokens_est", "budget_bytes", "fits", "warning", "at")


def step_sizes(root: Path) -> list[dict[str, Any]]:
    """The ``tools.step_size --run`` measurements in order; an unreadable line is skipped, not fatal."""
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


_KINDS = ("documents", "code", "markup", "plan_header", "map", "other")
_RUN_PLAN_SLOTS = ("generator-headers", "batch-plans")


def _composition(found: Mapping[str, Any], project: Path, root: Path) -> dict[str, int]:
    """The bytes ``stage_inputs`` found, by kind — requirement documents, project code, the answers of the marker, plan and
    header, the map of existing tests, the rest (each file is counted once, as ``input_size`` does)."""
    from tools.step_size import _requirement_files

    try:
        documents = {str(path.resolve()) for path in _requirement_files(project, root)}
    except (KeyError, OSError, TypeError, ValueError):
        documents = set()
    run = root.resolve()
    sums = dict.fromkeys(_KINDS, 0)
    for path, row in {row["path"]: row for row in found["files"]}.items():
        target = Path(path).resolve()
        size = int(row["bytes"])
        if row.get("basis") == "карта":
            kind = "map"
        elif row.get("basis") == "plan-view-v1":
            kind = "plan_header"
        elif str(target) in documents:
            kind = "documents"
        elif not target.is_relative_to(run):
            kind = "code"
        elif target.relative_to(run).parts[0] == "model-stage-artifacts":
            kind = "markup" if _stage_of(target) == "context-marker" else "other"
        elif target.relative_to(run).parts[0] in _RUN_PLAN_SLOTS:
            kind = "plan_header"
        else:
            kind = "other"
        sums[kind] += size
    sums["other"] += sum(int(row["bytes"]) for row in found["bindings"])
    return sums


def _stage_of(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value.get("stage") if isinstance(value, Mapping) else None


def input_composition(calls: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """«Состав входа по видам»: one row per model call, the role bytes next to the kinds of the input."""
    return [{"stage": call["stage"], **call["composition"], "role": call.get("role_bytes") or 0}
            for call in calls if call.get("by") == "model" and call.get("composition")]


def _find(events: Sequence[Mapping[str, Any]], kind: str, prefix: str | None = None, *, last: bool = False) -> Mapping[str, Any] | None:
    found = [event for event in events if event.get("event_type") == kind
             and (prefix is None or str(event.get("stage_instance_id") or "").startswith(prefix))]
    return (found[-1] if last else found[0]) if found else None


def phases(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """«Фазы по времени»: the boundaries are journal events, as the report template names them; a phase whose
    boundary is not in the journal yet has ``sec: None`` — never guessed."""
    ordered = sorted(events, key=lambda event: event["seq"])
    marker_response = _find(ordered, "MODEL_RESPONSE_RECEIVED", "context-marker:baseline", last=True)
    first_generator = _find(ordered, "MODEL_REQUESTED", "tc-generator:")
    plan_read = next((event for event in reversed(ordered) if event["event_type"] == "ARTIFACT_READ_BACK"
                      and first_generator is not None and event["seq"] < first_generator["seq"]), None)
    review_request = _find(ordered, "REVIEW_REQUESTED", "tc-reviewer:")
    review_response = _find(ordered, "MODEL_RESPONSE_RECEIVED", "tc-reviewer:", last=True)
    last_candidate = _find(ordered, "CANDIDATE_PUBLISHED", "tc-generator:BATCH-", last=True)
    rows: list[dict[str, Any]] = []

    def phase(name: str, start: Mapping[str, Any] | None, end: Mapping[str, Any] | None, batch: str | None = None) -> None:
        rows.append({"phase": name, "from": start.get("event_type") if start else None, "to": end.get("event_type") if end else None,
                     "batch": batch, "sec": _seconds(start.get("observed_at"), end.get("observed_at")) if start and end else None})

    phase("подготовка", ordered[0] if ordered else None, _find(ordered, "CONTEXT_SELECTED", last=True))
    phase("разметка", _find(ordered, "MODEL_REQUESTED", "context-marker:"), marker_response)
    phase("план и заголовок", marker_response, plan_read)
    for request in (event for event in ordered if event["event_type"] == "MODEL_REQUESTED"
                    and str(event.get("stage_instance_id") or "").startswith("tc-generator:")):
        published = next((event for event in ordered if event["event_type"] == "CANDIDATE_PUBLISHED"
                          and event.get("stage_instance_id") == request.get("stage_instance_id") and event["seq"] > request["seq"]), None)
        phase("генерация", request, published, str(request.get("batch_id") or request.get("stage_instance_id")))
    phase("сборка и пакет ревью", last_candidate, review_request)
    phase("ревью", review_request, review_response)
    phase("закрытие", review_response, _find(ordered, "ATTEMPT_TERMINAL", last=True))
    return rows


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


def _identifier(value: Any) -> str | None:
    """A command, code, stage or batch name; anything else (a path, a message) is dropped — the report never copies text."""
    return value if isinstance(value, str) and _IDENTIFIER.match(value) else None


REFUSALS_BASIS = "по журналу (событие COMMAND_REFUSED); подробности строки — из refusals.jsonl, сверенной с журналом по digest"  # the journal counts, the file only gives the detail


def refusals(root: Path, events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    """«Отказы»: the refusals are COUNTED BY THE JOURNAL — one ``COMMAND_REFUSED`` event per refusal, its digest the sha256 of the
    line ``<run>/refusals.jsonl`` got (``{at, command, code, stage, batch}``, written by ``tools.api``).  The line gives the detail
    (command, code, stage, batch) of the event it is bound to; an event whose line is gone or changed is counted all the same and
    its detail is ``details_lost``; a line no event confirms is not counted and is named in ``mismatch`` (the agent can edit the file,
    not the journal).  ``None`` when the run has no event, no file and no step measurements of v0.2 (no way to tell a clean run from one
    before v0.2)."""
    import hashlib

    if events is None:
        from tools import pilot_state

        events = pilot_state.derive_state(Path(root))["events"]
    confirmed = [event.get("artifact_digest") for event in events if event.get("event_type") == "COMMAND_REFUSED"]
    try:
        raw_lines = [line for line in (Path(root) / "refusals.jsonl").read_bytes().split(b"\n") if line.strip()]
    except OSError:
        # The file is created by the first refusal, so a clean run of v0.2 has none.  What only v0.2 writes is ``step-sizes.jsonl`` (it measures
        # every step it sends to the model): with it the run is v0.2 and had no refusal; without it there is no way to tell, and the report says so.
        from tools.step_size import RECORD

        if not confirmed and not (Path(root) / RECORD).is_file():
            return None
        raw_lines = []
    by_digest: dict[str, list[bytes]] = {}
    for line in raw_lines:
        by_digest.setdefault("sha256:" + hashlib.sha256(line).hexdigest(), []).append(line)
    rows: list[Mapping[str, Any]] = []
    details_lost = 0
    for digest in confirmed:
        found = by_digest.get(digest) or []
        value = None
        if found:
            try:
                value = json.loads(found.pop(0))
            except ValueError:
                value = None
        if isinstance(value, Mapping):
            rows.append(value)
        else:
            details_lost += 1
    without_event = sum(len(rest) for rest in by_digest.values())
    times = sorted(str(row["at"]) for row in rows if isinstance(row.get("at"), str))
    summary: dict[str, Any] = {"total": len(confirmed), "first_at": times[0] if times else None, "last_at": times[-1] if times else None,
                               "basis": REFUSALS_BASIS, "details_lost": details_lost,
                               "mismatch": {"lines_without_event": without_event, "events_without_line": details_lost} if without_event or details_lost else None}
    for key, name in (("command", "by_command"), ("code", "by_code"), ("stage", "by_stage"), ("batch", "by_batch")):
        counts: dict[str, int] = {}
        for row in rows:
            value = _identifier(row.get(key))
            if value is not None:
                counts[value] = counts.get(value, 0) + 1
        summary[name] = dict(sorted(counts.items()))
    pairs: dict[tuple[str, str], int] = {}
    for row in rows:
        pair = (_identifier(row.get("command")) or "?", _identifier(row.get("code")) or "?")
        pairs[pair] = pairs.get(pair, 0) + 1
    summary["by_command_code"] = [{"command": command, "code": code, "count": count} for (command, code), count in sorted(pairs.items())]
    # a second model-request of a generator batch is refused (MODEL_REQUEST_DUPLICATE) and the word of the first one is not shown again
    summary["codeword_lost"] = sorted({stage for row in rows if row.get("code") == "MODEL_REQUEST_DUPLICATE"
                                       for stage in [_identifier(row.get("stage"))] if stage and stage.startswith("tc-generator:")})
    return summary


def canon_numbers(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """«Канон в числах»: the size of the canonical document (compact JSON) per case, and steps, assertions and
    automation blockers per case."""
    if not isinstance(document, Mapping) or not isinstance(document.get("test_cases"), list) or not document["test_cases"]:
        return None
    cases = document["test_cases"]
    size = len(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    steps = [step for case in cases for step in case.get("steps") or []]
    assertions = sum(len(expectation.get("assertions") or []) for step in steps for expectation in step.get("expectations") or [])
    blockers = sum(len(step.get("automation_blockers") or []) for step in steps)
    return {"cases": len(cases), "bytes": size, "bytes_per_case": round(size / len(cases)),
            "steps_per_case": round(len(steps) / len(cases), 1), "assertions_per_case": round(assertions / len(cases), 1),
            "blockers": blockers, "blockers_per_case": round(blockers / len(cases), 2)}


def remove_commands(relative: str, system: str) -> tuple[str, str | None]:
    """The command that removes the folder *relative* (a posix path from the project) for the shell of the machine, and for Windows the cmd form
    beside the PowerShell one.  PowerShell quotes with apostrophes (one inside is doubled, nothing else is special there), cmd and sh with double quotes."""
    if system == "win32":
        windows = relative.replace("/", "\\")
        quoted = windows.replace("'", "''")
        return f"Remove-Item -LiteralPath '{quoted}' -Recurse -Force", f'rmdir /s /q "{windows}"'
    return f'rm -rf "{relative}"', None


def tmp_usage(root: Path, project: Path) -> dict[str, Any] | None:
    """The size of ``<run>/tmp/`` (the agent's working files) and the command that removes it — the report removes nothing."""
    tmp = Path(root) / "tmp"
    if not tmp.is_dir():
        return None
    files = [path for path in tmp.rglob("*") if path.is_file()]
    relative = tmp.relative_to(project).as_posix()
    command, command_cmd = remove_commands(relative, sys.platform)
    usage = {"path": relative, "files": len(files), "bytes": sum(path.stat().st_size for path in files), "remove_command": command}
    if command_cmd is not None:
        usage["remove_command_cmd"] = command_cmd
    return usage


def budget_warnings(calls: Iterable[Mapping[str, Any]], sizes: Iterable[Mapping[str, Any]] = ()) -> list[str]:
    """``STEP_OVER_BUDGET`` when a model call of the journal is over the step budget; the rows of ``step-sizes.jsonl`` (``sizes``) are
    the agent's measurement — they are printed, they warn of nothing."""
    from tools.step_size import OVER_BUDGET

    del sizes
    over = any(call.get("by") == "model" and call.get("fits") is False for call in calls)
    return [OVER_BUDGET] if over else []


_WARNING_TEXT = {"CODEWORD_LOST": "model-request генератора повторяли (MODEL_REQUEST_DUPLICATE): слово первого запроса показывают один раз — если вывод потерян, генератор шёл без слова, и «слово не названо» у ревьюера ничего не доказывает",
                 "refusals.mismatch": "файл refusals.jsonl и журнал расходятся (строка без события или событие без строки) — число отказов взято из журнала, подробности смотри в отчёте",
                 "REVIEW_UNRELIABLE": "ни одна контрольная ошибка (канарейка) в виде ревьюера не поймана — ревью ненадёжно, accepted=false",
                 "REVIEW_WEAK": "ревьюер поймал не все контрольные ошибки (канарейки) своего вида",
                 "REVIEW_UNMEASURED": "ревью не измерено канарейками (прогон открыт до P7)",
                 "REVIEW_CANARY_MISMATCH": "карта канареек или вид ревьюера не совпадают с квитанцией журнала",
                 "STEP_OVER_BUDGET": "вход шага больше бюджета, прогон шёл дальше",
                 "STEP_OUTPUT_LARGE": "ответ модели больше порога limits.step_output_warn_bytes — только замер, деление выключено",
                 "CV_DATA_MISMATCH": "значения входов шага не названы в его действии и данных (подсказка ревьюеру)",
                 "CV_NO_END_TO_END": "групп требований несколько, а ни один кейс не связывает две из них — нет сквозного кейса "
                                     "(подсказка ревьюеру)",
                 "CV_EXISTING_TEST": "кейсы повторяют тесты проекта — тот же эндпоинт и исход (справка ревьюеру, кейсы остаются)",
                 "CV_REFERENCE": "сравнение с эталонными кейсами scan --reference: пробелы, новые без пары, возможные дубли (подсказка)",
                 "SCOPE_DECISION_MISMATCH": "scope-decision.json расходится с опубликованным планом: охват взят из плана (см. «Охват после разметки»)"}


def existing_repeats(root: Path, attempt_id: str | None, document: Any) -> dict[str, Any] | None:
    """How many cases repeat a test of the project (the map of the generator's context) — counts and IDs only;
    None when the project has no map."""
    from tools.existing_tests import repeats
    from tools.review_view import run_existing_tests

    if not attempt_id or not isinstance(document, Mapping):
        return None
    try:
        existing = run_existing_tests(root, attempt_id)
        return {"repeating": repeats(document, existing), "cases": len(document["test_cases"])} if existing else None
    except (KeyError, OSError, TypeError, ValueError, AttributeError):
        return None


def reference(root: Path, attempt_id: str | None, document: Any) -> dict[str, Any] | None:
    """``CV_REFERENCE`` of the run's case document against its reference cases — IDs and counts only."""
    from tools.pilot_state import read_reference_cases
    from tools.review_view import _reference_counts
    from tools.reference_match import reference_match

    if not attempt_id or not isinstance(document, Mapping):
        return None
    try:
        cases = read_reference_cases(root, attempt_id)
        return None if cases is None else _reference_counts(reference_match(document, cases["cases"]))
    except (KeyError, OSError, TypeError, ValueError, AttributeError):
        return None


def end_to_end(root: Path, attempt_id: str | None, document: Any) -> dict[str, Any] | None:
    """``CV_NO_END_TO_END`` of the run's case document — counts only, no case text."""
    from tools.review_view import end_to_end_hint, run_requirement_groups

    if not attempt_id or not isinstance(document, Mapping):
        return None
    try:
        grouping = run_requirement_groups(root, attempt_id, document)
        return None if grouping is None else end_to_end_hint(document, *grouping)
    except (KeyError, OSError, TypeError, ValueError, AttributeError):
        return None


def data_mismatch(document: Any) -> list[dict[str, Any]]:
    """The steps with ``CV_DATA_MISMATCH`` by ID and count only — the values are case data and stay out."""
    from tools.review_view import data_mismatches

    try:
        return [{key: hint[key] for key in ("case_id", "step_id", "count")} for hint in data_mismatches(document)]
    except (KeyError, TypeError, AttributeError):
        return []


# --- the report ---------------------------------------------------------------------------------

EFFECTIVE_UNREADABLE = "EFFECTIVE_CANON_UNREADABLE: эффективный канон не читается — документ кейсов в отчёт не берётся ни из какого другого источника"


def _effective_document(root: Path, attempt_id: str | None) -> tuple[Mapping[str, Any] | None, bool]:
    """``(document, unreadable)`` — the effective canonical of the attempt, read the strict way (against its events); a file that is
    there and cannot be read is ``unreadable``, not a reason to look anywhere else."""
    from tools import pilot_state as pilot

    if not attempt_id:
        return None, False
    try:  # decided by the journal, not by a file being there: an accepted verdict whose effective file is gone is a refusal here too
        effective = pilot.effective_canonical_or_reason(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return None, True
    if effective.get("effective", 1) is None:  # no verdict yet, or one that does not accept the candidate: the candidate of the journal
        return None, False
    if isinstance(effective.get("document"), Mapping):
        return effective["document"], False
    return None, True


def case_document(root: Path, attempt_id: str | None) -> Mapping[str, Any] | None:
    """The selected case document — the effective canonical of the attempt and nothing else (no file the agent can
    put into the run); before a verdict the report takes the candidate from the journal (``journal_assembly``)."""
    return _effective_document(root, attempt_id)[0]


UNUSED_TEST_DESIGN = "генератор не заполнил технику и параметры — подсказки тест-дизайна не строились"


def test_design_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    """Technique counts and code hints by ID only — never case text; ``used`` false says the fields were empty."""
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
    """Hints on vague expectations of the cases by ID only, on any document."""
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
    if not response:
        return None
    from tools import pilot_state as pilot

    try:  # by the journal (the stage, the digest, the bytes checked), not the first file of that name in the run
        return pilot.read_model_stage_artifact(root, str(response["attempt_id"]), str(response["stage_instance_id"]), str(response["artifact_digest"]))["artifact"]
    except (KeyError, OSError, TypeError, ValueError):
        return None


def autotest_summary(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Weak assertions of the generated tests — file ID, line, rule and ASSERT ID, never the code."""
    autotest_hints, capped_autotest_hints, counts = execution_gate.from_mode_b("autotest_hints", "autotest_hints", "capped_autotest_hints", "counts")

    hints = autotest_hints(artifact)
    return {"hints": counts(hints), "gaps": capped_autotest_hints(hints)}


def render_autotest_assertions(summary: Mapping[str, Any]) -> str:
    lines = ["## Слабые утверждения автотестов", "", "- Подсказки кода: " + (", ".join(f"{code} {count}" for code, count in summary["hints"].items()) or "нет")]
    lines += [f"  - {gap['code']} {gap['detail']}" if "file_id" not in gap else
              f"  - {gap['code']} {gap['file_id']}:{gap['line']} {gap['rule']} {gap.get('assertion_id', '')}".rstrip() for gap in summary["gaps"]]
    return "\n".join(lines)


ZEPHYR_BASIS = "по файлу zephyr-import.json, журналом не подтверждено"


def _zephyr_import(root: Path) -> Any:
    """Only the template path and digest of the import CSV, never its values. The file has no event in the journal (the export runs after
    ``finish``, the attempt is closed): it is printed only for the template it names — the digest of the file at ``template_path`` in the project — and
    labelled as the file's."""
    try:
        record = json.loads((root / "zephyr-import.json").read_text(encoding="utf-8"))
        shown = {key: record[key] for key in ("template_path", "template_digest", "columns_added", "rows")}
        template = output_guard.project_file(Path(root).parent.parent, str(shown["template_path"]))  # its text and place first, then the bytes
        if "sha256:" + hashlib.sha256(template.read_bytes()).hexdigest() != shown["template_digest"]:
            return None
        return {**shown, "basis": ZEPHYR_BASIS}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _import_summary(root: Path, events: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The CSV of an import run, its cases and how many cells code normalized; None for other runs."""
    from tools import pilot_state
    from tools.cases_import import cases_path

    path = cases_path(root)
    attempts = [event.get("attempt_id") for event in events if event.get("attempt_id")]
    if path is None or not attempts:
        return None
    try:
        imported = pilot_state.read_attempt_receipt(root, str(attempts[-1]), "imported-cases", "ARTIFACT_READ_BACK")["record"]["imported"]
    except (KeyError, OSError, ValueError):
        return {"csv": path, "cases": None, "normalized": None}
    from tools.cases_import import outside_rows

    return {"csv": path, "cases": len(imported["cases"]), "normalized": len(imported.get("normalized") or []),
            **({"gaps": list(imported["gaps"]), "outside": outside_rows(imported)} if "gaps" in imported else {})}


def _left(root: Path) -> str | None:
    """«оставлен до ревью кейсов» of a run left by finish --abandon LEFT_BEFORE_REVIEW."""
    from tools.pilot_state import left_attempt

    try:
        return "оставлен до ревью кейсов (finish --abandon LEFT_BEFORE_REVIEW)" if left_attempt(root) else None
    except (OSError, ValueError):
        return None


def _split(root: Path, attempt_id: str | None) -> dict[str, Any] | None:
    """Automated N of M cases, manual K with their blocker codes — the same split ingest and finish print."""
    if not attempt_id:
        return None
    from tools.finalize_attempt import automation_split

    try:
        return automation_split(root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _plan_lost_with_receipts(root: Path, state: Mapping[str, Any], attempt_id: str) -> bool:
    """The plan file is not there while the journal read back a receipt of the attempt that no file carries (the plan is one of them: the
    journal binds a receipt by digest, not by kind, so this is the closest it can say)."""
    from tools import pilot_state as pilot

    return not pilot._receipt_target(root, attempt_id, "batch-plan").is_file() and bool(pilot.lost_attempt_receipts(root, state, attempt_id))


def _scope(root: Path, attempt_id: str | None, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """What the human decided on the gaps of the marker — the requirements left out of the batches, each with the
    reason the human gave; None for a run whose marker has no gaps and whose human excluded nothing (nothing was asked, nothing left out)."""
    if not attempt_id:
        return None
    from tools.fixes import fix_for
    from tools.scope_pause import ScopeRefused, published_scope, scope_state

    # once the plan is published its out_of_scope is the scope; the decision file (no journal event) is only compared with it
    try:
        published = published_scope(root, attempt_id, state)
    except (KeyError, OSError, TypeError, ValueError):
        published = None
    if published is None and _plan_lost_with_receipts(root, state, attempt_id):
        # the journal binds a plan (the generator was asked with it) that cannot be read now: the decision file has no event — it is not the scope
        return {"refused": {"code": "SCOPE_PLAN_UNREADABLE", "fix": fix_for("SCOPE_PLAN_UNREADABLE")}}
    try:
        scope = scope_state(root, attempt_id, state)
    except ScopeRefused as error:  # a decision file that is not the human's answer: the report says so (a ScopeRefused is a ValueError, below)
        if published is None:
            fix = str(error).split("fix: ", 1)[1] if "fix: " in str(error) else fix_for(error.code)  # the text the status prints; the table when it has none
            return {"refused": {"code": error.code, "fix": fix}}
        scope = {"gaps": [], "decision": None, "pending": False}
    except (KeyError, OSError, TypeError, ValueError):
        return None
    decision = scope["decision"]
    excluded = published["out_of_scope"] if published is not None else [dict(row) for row in (decision or {}).get("excluded") or []]
    if not scope["gaps"] and not excluded and not (published and published["mismatch"]):  # an exclusion needs no gap: the human may leave a requirement out of a marker without any
        return None
    answer = "пауза не снималась: ответа человека нет" if scope["pending"] and published is None else (
        "решение не подтверждено журналом (файл без событий)" if published is not None and published.get("unbound") and decision is None and not excluded else (
            "без остановки (scan --no-pause)" if decision is None and not excluded else ("исключены" if excluded else "продолжить как есть")))
    result = {"gaps": len(scope["gaps"]), "answer": answer, "out_of_scope": excluded}
    if published is not None and published["mismatch"]:
        result["mismatch"] = published["note"]
    return result


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

    from tools.step_size import output_sizes

    calls = model_calls(events)
    journals: dict[str, Any] = {}  # the journal of an attempt is read once for all its calls

    raw_sizes = output_sizes(root)
    limits, limits_note = ({}, None)
    if attempt_id:
        from tools.step_size import run_limits

        limits, limits_note = run_limits(project, root, state, attempt_id)  # the limits of the .skillsrc the run froze
    for call in calls:
        _size(project, root, call, raw_sizes, state=state, journals=journals, limits=limits)
    measured = step_sizes(root)
    composition = input_composition(calls)

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
    document, effective_unreadable = _effective_document(root, attempt_id)
    canon_basis = None
    if document is None and attempt_id and not effective_unreadable:
        # PT2: a review that sent the set back (REWORK) has no effective document: the numbers are those of the candidate r1, not accepted
        try:
            from tools.orchestrate_test_case_revision import journal_assembly

            document, canon_basis = journal_assembly(root, attempt_id)[0], "кандидат r1, не принят"
        except (KeyError, OSError, TypeError, ValueError):
            document = None
    if isinstance(document, Mapping) and isinstance(document.get("test_cases"), list):
        cases = len(document["test_cases"])
    automation = automation_artifact(root, events)
    mismatched = data_mismatch(document) if cases is not None else []
    path_hint = end_to_end(root, attempt_id, document) if cases is not None else None
    existing = existing_repeats(root, attempt_id, document) if cases is not None else None
    compared = reference(root, attempt_id, document) if cases is not None else None
    source_count = len(document["source_requirements"]) if isinstance(document, Mapping) and isinstance(document.get("source_requirements"), list) else None
    canonical_count = len(document["requirements"]) if isinstance(document, Mapping) and isinstance(document.get("requirements"), list) else None
    notes = review_notes(root, attempt_id) if attempt_id else {"count": 0, "items": []}
    autotest_notes = review_notes(root, attempt_id, "autotests") if attempt_id else {"count": 0, "items": []}

    from tools import review_canaries

    answer = None
    canaries = None
    codeword = None
    acceptance = None
    if attempt_id:  # what the canaries and the control word say of the review — derived from the journal, nothing stored
        try:
            answer = review_canaries.recorded_answer(root, attempt_id)
        except (OSError, KeyError, TypeError, ValueError):
            answer = None
        canaries = review_canaries.summary(root, attempt_id, answer)
        if answer is None and canaries["status"] != "MISMATCH":  # before the answer only a map that does not match is news
            canaries = None
        try:
            from tools.orchestrate_test_case_revision import acceptance_summary

            acceptance = acceptance_summary(root, attempt_id, answer)
        except (KeyError, OSError, TypeError, ValueError):
            acceptance = None
        if answer is not None:
            codeword = review_canaries.codeword_state(root, attempt_id, answer)
    unreliable = canaries is not None and canaries.get("status") == "UNRELIABLE"
    if codeword == "named":
        review = "ревью в той же сессии (SELF): ревьюер назвал контрольное слово генератора"
    elif terminal.get("review_independence") == "SELF":
        review = "ревью в той же сессии (SELF), принято по согласию человека"
    elif evidence.get("reviewer_isolation_state") is not None:
        review = "ревью в свежем контексте"
    else:
        review = None
    scope = _scope(root, attempt_id, state)
    refused = refusals(root, events)
    report = {
        "schema_version": "1.0.0", "run_id": run["manifest"]["run_id"], "policy_profile": run["manifest"]["policy_profile"], "test_level": test_level(project, root, attempt),
        "guarantee": GUARANTEE, "clock": CLOCK_NOTE,
        "started_at": events[0].get("observed_at") if events else None,
        "finished_at": events[-1].get("observed_at") if events else None,
        "duration_sec": _seconds(events[0].get("observed_at"), events[-1].get("observed_at")) if events else None,
        "steps": [{"seq": event["seq"], "event": event["event_type"], "stage": event.get("stage_instance_id"),
                   "batch": event.get("batch_id"), "at": event.get("observed_at")} for event in events],
        "model_calls": calls, "model_totals": model_totals(calls), "step_sizes": measured,
        "phases": phases(events), "input_composition": composition, "refusals": refused, "canon": canon_numbers(document),
        "tmp": tmp_usage(root, project),
        "warnings": budget_warnings(calls, measured) + (["STEP_OUTPUT_LARGE"] if any(call.get("output_large") for call in calls) else [])
                    + (["CV_DATA_MISMATCH"] if mismatched else []) + (["CV_NO_END_TO_END"] if path_hint else [])
                    + (["CV_EXISTING_TEST"] if existing and existing["repeating"] else [])
                    + (["CV_REFERENCE"] if compared and (compared["gaps"] or compared["new"] or compared["duplicates"]) else [])
                    + (["SCOPE_DECISION_MISMATCH"] if scope and scope.get("mismatch") else [])
                    + ([f"SKILLSRC_CHANGED: {limits_note}"] if limits_note else [])
                    + ([EFFECTIVE_UNREADABLE] if effective_unreadable else [])
                    + (["refusals.mismatch"] if refused and refused["mismatch"] else [])
                    + (["CODEWORD_LOST"] if refused and refused.get("codeword_lost") else [])
                    + ([canaries["warning"]] if canaries and canaries.get("warning") else []),
        "data_mismatch": mismatched, "end_to_end": path_hint, "existing_tests": existing, "reference": compared,
        "import": _import_summary(root, events),
        "automation_split": _split(root, attempt_id),
        "scope": scope,  # the answer on the gaps and the excluded requirements with reasons
        "stops": {"stop_reason": status.get("stop_reason"), "reason_code": terminal.get("reason_code"),
                  "left": _left(root),
                  "evidence_errors": sorted({row["code"] for row in status.get("evidence_errors") or []})},
        "counts": {
            "requirements": requirements,
            "source_requirements": requirements if requirements is not None else source_count,
            "canonical_requirements": canonical_count, "canon_basis": canon_basis,
            "batches": len({event.get("stage_instance_id") for event in responses if str(event.get("stage_instance_id")).startswith("tc-generator:BATCH-")}),
            "cases": cases, "review_verdict": evidence.get("authoritative_verdict"),
            "automation": evidence.get("automation_accepted"),
            "execution": execution.get("verdict") or terminal.get("verification"),
            "execution_duration_sec": (execution.get("stats") or {}).get("duration_sec"),
            "completion": terminal.get("completion"), "coverage": terminal.get("coverage"),
            "accepted": False if unreliable else terminal.get("accepted"),  # REVIEW_UNRELIABLE — not accepted (the sealed result is the terminal's)
            # calls a model made, and answers reused from an earlier run with the input bytes not sent
            "model_calls": sum(1 for call in calls if call.get("by") == "model"),
            "code_steps": sum(1 for call in calls if call.get("by") == "code"),
            "reused_calls": sum(1 for call in calls if call.get("by") == "reuse"),
            "reused_input_bytes": sum(int(call.get("saved_bytes") or 0) for call in calls if call.get("by") == "reuse"),
        },
        "review": review, "reviewer_isolation_state": evidence.get("reviewer_isolation_state"),
        # the run of the tests was a human's — the package did not reproduce it
        "execution_source": ({**execution["execution_source"], "note": HUMAN_RESULT_NOTE} if execution.get("execution_source") else None),
        "acceptance": acceptance, "review_canaries": canaries, "review_codeword": None if codeword is None else {"state": codeword, "note": review_canaries.CODEWORD_NOTES[codeword]},
        "review_notes": {"count": notes["count"], "items": notes["items"]},
        "autotest_review_notes": {"count": autotest_notes["count"], "items": autotest_notes["items"]},
        "quarantine": [{key: row[key] for key in ("file", "method", "ref", "failed_rows", "disabled_passed_rows") if key in row}
                       for row in status.get("quarantine") or []],
        "quarantine_unresolved": "QUARANTINE_UNRESOLVED" if status.get("quarantine_unresolved") else None,
        "zephyr_import": _zephyr_import(root),
        "test_design": test_design_summary(document) if isinstance(document, Mapping) else None,
        "expectations": expectation_summary(document) if isinstance(document, Mapping) else None,
        "autotest_assertions": autotest_summary(automation) if isinstance(automation, Mapping) else None,
        "environment": {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
                        "adapter": (execution.get("execution") or {}).get("adapter_id")},
    }
    return report, list(status.get("warnings") or [])


def model_totals(calls: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """The calls a model made, summed: the bytes it read and wrote and the estimates in tokens (the answer by the bytes it wrote)."""
    made = [call for call in calls if call.get("by") == "model"]
    return {"calls": len(made),
            "input_bytes": sum(int(call.get("input_bytes") or 0) + int(call.get("role_bytes") or 0) for call in made),
            "input_tokens_est": sum(int(call.get("tokens_est") or 0) for call in made),
            "output_bytes": sum(int(call.get("output_raw_bytes") or call.get("output_bytes") or 0) for call in made),
            "output_tokens_est": sum(int(call.get("output_tokens_est") or 0) for call in made)}


def _size_cell(call: Mapping[str, Any]) -> str:
    cell = f"{_dash(call.get('input_bytes'))}+{_dash(call.get('role_bytes'))}"
    if call.get("input_basis"):
        cell += f" ({call['input_basis']})"
    if not call.get("size_complete", True):
        cell += f" (размер неполный: не найдено {call.get('unresolved_inputs')})"
    return cell


def _tokens_cell(call: Mapping[str, Any]) -> str:
    if call.get("by") == "reuse":
        return f"повтор {str(call.get('reused_from') or '')[:8]}"
    return "код" if call.get("by") == "code" else _dash(call.get("tokens_est"))


def _dash(value: Any) -> str:
    return "—" if value is None else str(value)


def _note_line(row: Mapping[str, Any]) -> str:
    dropped = f" (отброшено ID: {row['dropped_ids']})" if row["dropped_ids"] else ""
    return f"- {row['severity']} {_dash(row['code'])}: {', '.join(row['related_ids']) or '—'}{dropped}"


def _notes_section(title: str, subject: str, notes: Mapping[str, Any] | None) -> list[str]:
    if not (notes or {}).get("count"):
        return []
    return [f"## {title}", "", f"Ревью приняло {subject} с замечаниями-мелочами; текст — в questions-for-analyst.md.", "",
            *(_note_line(row) for row in notes["items"]), ""]


def _minutes(seconds: Any) -> str:
    return "—" if seconds is None else f"{seconds / 60:.1f}"


def _phase_lines(report: Mapping[str, Any]) -> list[str]:
    return ["## Фазы по времени", "", "| Фаза | Пачка | От события | До события | Минуты |", "|---|---|---|---|---|",
            *(f"| {row['phase']} | {_dash(row['batch'])} | {_dash(row['from'])} | {_dash(row['to'])} | {_minutes(row['sec'])} |"
              for row in report.get("phases") or []), ""]


def _composition_lines(report: Mapping[str, Any]) -> list[str]:
    rows = report.get("input_composition") or []
    return ["## Состав входа по видам", "", "Байты входа вызова модели по видам (роль — отдельно).", "",
            *(["состава нет", ""] if not rows else [
                "| Стадия | документы | код | разметка | план+заголовок | карта тестов | прочее | роль |", "|---|---|---|---|---|---|---|---|",
                *(f"| {r['stage']} | {r['documents']} | {r['code']} | {r['markup']} | {r['plan_header']} | {r['map']} | {r['other']} | {r['role']} |"
                  for r in rows), ""])]


def _step_size_lines(report: Mapping[str, Any]) -> list[str]:
    return ["## Размер входа шагов", "", "Замер агента (`tools.step_size --run` и model-request): строки файла step-sizes.jsonl, журналом не подтверждены; "
            "предупреждение STEP_OVER_BUDGET строится по вызовам модели из журнала.", "",
            *(["замеров нет", ""] if not report["step_sizes"] else [
                "| Шаг | Байты входа | Роль, Б | Токены (оценка) | Бюджет, Б | Влезает | Время |", "|---|---|---|---|---|---|---|",
                *(f"| {s['step']} | {_dash(s['bytes'])} | {_dash(s['role_bytes'])} | {_dash(s['tokens_est'])} | {_dash(s['budget_bytes'])} | "
                  f"{_dash(s['fits'])} | {_dash(s['at'])} |" for s in report["step_sizes"]), ""])]


def _refusal_lines(report: Mapping[str, Any]) -> list[str]:
    summary = report.get("refusals")
    if summary is None:
        return ["## Отказы", "", "журнала отказов нет (прогон до v0.2)", ""]
    return ["## Отказы", "", f"Всего: {summary['total']}; с {_dash(summary['first_at'])} по {_dash(summary['last_at'])}.",
            f"Число {summary.get('basis') or REFUSALS_BASIS}.",
            *([f"Контрольное слово генератора: повторный model-request у {', '.join(summary['codeword_lost'])} — слово показывали один раз, вывод мог быть потерян."]
              if summary.get("codeword_lost") else []),
            *([f"Расхождение журнала и файла refusals.jsonl: строк без события {summary['mismatch']['lines_without_event']} (не посчитаны), "
               f"событий без строки {summary['mismatch']['events_without_line']} (посчитаны, подробности потеряны)."] if summary.get("mismatch") else []), "",
            "| Команда | Код | Сколько раз |", "|---|---|---|",
            *(f"| {row['command']} | {row['code']} | {row['count']} |" for row in summary["by_command_code"]), "",
            "- по стадиям: " + (", ".join(f"{key} {value}" for key, value in summary["by_stage"].items()) or "—"),
            "- по пачкам: " + (", ".join(f"{key} {value}" for key, value in summary["by_batch"].items()) or "—"), ""]


def _canon_lines(report: Mapping[str, Any]) -> list[str]:
    canon = report.get("canon")
    if canon is None:
        return ["## Канон в числах", "", "канона нет", ""]
    return ["## Канон в числах", "", f"- кейсов: {canon['cases']}; размер канона, Б: {canon['bytes']}; байт на кейс: {canon['bytes_per_case']}",
            f"- шагов на кейс: {canon['steps_per_case']}; утверждений на кейс: {canon['assertions_per_case']}; "
            f"блокеров всего {canon['blockers']}, на кейс {canon['blockers_per_case']}", ""]


def _tmp_lines(report: Mapping[str, Any]) -> list[str]:
    tmp = report.get("tmp")
    if tmp is None:
        return []
    return ["## Рабочие файлы агента", "", f"`{tmp['path']}`: файлов {tmp['files']}, {tmp['bytes']} Б. Отчёт ничего не удаляет; "
            f"убрать можно командой `{tmp['remove_command']}`" + (f" (в cmd: `{tmp['remove_command_cmd']}`)" if tmp.get("remove_command_cmd") else "") + ".", ""]


def _canary_lines(report: Mapping[str, Any]) -> list[str]:
    """The canaries of the reviewer's view and the control word — numbers and the honest note, never the map itself."""
    canaries, codeword = report.get("review_canaries"), report.get("review_codeword")
    if not canaries and not codeword:
        return []
    lines = ["## Контрольные ошибки ревью (канарейки)", ""]
    if canaries:
        lines.append(f"- {canaries['status']}: посеяно {_dash(canaries.get('seeded'))}, поймано {_dash(canaries.get('caught'))}"
                     + (f"; пропущены классы: {', '.join(canaries['missed_classes'])}" if canaries.get("missed_classes") else ""))
        lines.append(f"- {canaries['note']}")
    if codeword:
        lines.append(f"- кодовое слово: {codeword['state']} — {codeword['note']}")
    return lines + [""]


def _acceptance_lines(report: Mapping[str, Any]) -> list[str]:
    """The «Итог» block at the top: the outcome of the review in a line, the warning when the reviewer missed a control error and, for a partial
    acceptance, the numbers, the files to work with and the requirements left without a case."""
    from tools.publish_test_case_bundle import plural
    from tools.test_case_projections import escape_inline

    acc = report.get("acceptance")
    if not acc:
        return []
    total, accepted, removed, rework = acc["generated"], acc["accepted"], acc["removed"], acc.get("rework")
    left = total - accepted  # the cases not in the set
    if acc["outcome"] == "ACCEPTED_WHOLE":
        after = f" ({rework['accepted']} — после доработки)" if rework and rework["accepted"] else ""
        head = f"**ПРИНЯТ: {total} из {total} кейсов{after}.**"
    elif acc["outcome"] == "PARTIAL":
        state = rework["state"] if rework else ""
        words = {"reviewed": ("кейс не прошёл ревью после доработки и в набор не вошёл", "кейса не прошли ревью после доработки и в набор не вошли", "кейсов не прошли ревью после доработки и в набор не вошли")}.get(
            state, ("кейс не дорабатывался", "кейса не дорабатывались", "кейсов не дорабатывались"))
        said = f"Доработка {left} {'кейса' if left % 10 == 1 and left % 100 != 11 else 'кейсов'} не завершена." if state == "unfinished" else f"{left} {plural(left, *words)}."
        head = f"**ПРИНЯТ ЧАСТИЧНО: {accepted} из {total} кейсов.** {said}"
    elif acc["reason"] == "OVER_SHARE":
        head = f"**ТРЕБУЕТ ДОРАБОТКИ:** отклонено {removed} из {total} кейсов — больше допустимого ({acc['share']} %)."
    else:
        head = "**ТРЕБУЕТ ДОРАБОТКИ:** замечание ревьюера относится ко всему набору."
    lines = ["## Итог", "", head, ""]
    canaries = report.get("review_canaries") or {}
    if canaries.get("status") in {"WEAK", "UNRELIABLE"}:
        lines += [f"> **Внимание: ревьюер пропустил {canaries['seeded'] - canaries['caught']} из {canaries['seeded']} контрольных ошибок**, которые код подложил для проверки его внимательности.",
                  "> Ревью могло пропустить и настоящие ошибки — просмотрите принятые кейсы внимательнее перед импортом.", ""]
    if acc["outcome"] != "PARTIAL":
        return lines
    gaps, files = acc["coverage_gaps"], acc["files"]
    rework = rework if rework and rework["state"] != "not_started" else None  # nothing was sent back: the three rows of V6
    first = rework["accepted_first"] if rework else accepted
    lines += ["| | Кейсов |", "|---|---|", f"| Сгенерировано | {total} |", f"| Принято в первом круге ревью | {first} |",
              *([f"| Отклонено и отправлено на доработку | {rework['sent']} |", f"| Принято после доработки | {rework['accepted']} |"] if rework else []),
              f"| Не принято (вынесено отдельно) | {left} |", "",
              f"Ревью: {'независимое (INDEPENDENT)' if report.get('reviewer_isolation_state') == 'verified' else 'в той же сессии (SELF)'}, "
              + ("2 круга: весь набор, затем только доработанный кейс." if rework and rework["state"] == "reviewed" else "1 круг: весь набор."),
              (f"Пробелы покрытия: {len(gaps)} {plural(len(gaps), 'требование осталось', 'требования остались', 'требований остались')} без принятого кейса — см. «Пробелы покрытия»." if gaps
               else "Пробелов покрытия нет: у каждого требования остался принятый кейс."), "",
              "Файлы для работы:", f"- Набор для импорта в Zephyr: {files['csv']} ({accepted} {plural(accepted, 'кейс', 'кейса', 'кейсов')})", f"- Набор для чтения: {files['set']}",
              f"- Не принятые кейсы — не импортировать: {files['rejected']}", "- Вопросы аналитику: questions-for-analyst.md", ""]
    if gaps:
        lines += ["## Пробелы покрытия", "", "| Требование | Почему нет кейса | Что сказал ревьюер |", "|---|---|---|",
                  *(f"| {row['requirement']} | Его покрывали только отклонённые кейсы: {', '.join(f'ТК-{label}' for label in row['labels'])} | "
                    + (escape_inline(", ".join(row["codes"])) + " (текст — в файле набора)" if row["codes"] else "—") + " |" for row in gaps), ""]
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    totals = report.get("model_totals") or model_totals(report["model_calls"])
    lines = [
        f"# Отчёт прогона {report['run_id']}", "", *_acceptance_lines(report), f"Гарантия: {report['guarantee']}.", f"Время: {report['clock']}.", "",
        f"- Профиль: {report['policy_profile']}", *([f"- {_test_level_line(report['test_level'])}"] if report.get("test_level") else []),
        f"- Начало: {_dash(report['started_at'])}", f"- Конец: {_dash(report['finished_at'])}",
        f"- Длительность, с: {_dash(report['duration_sec'])}", f"- Ревью: {_dash(report['review'])}",
        *([f"- Результат запуска тестов: {report['execution_source']['note']} (слово команды: {report['execution_source']['challenge']})"]
          if report.get("execution_source") else []), "",
        "## Числа", "",
        f"Вызовов модели {counts['model_calls']}, шагов кодом {counts['code_steps']}; "
        f"исходных SREQ {_dash(counts['source_requirements'])}, канонических CREQ {_dash(counts['canonical_requirements'])}.", "",
        *(f"- {key}: {_dash(value)}" for key, value in counts.items()),
        *([f"- Автоматизация частичная: {report['automation_split']['summary']}"] if report.get("automation_split") else []), "",
        "## Вызовы модели", "", f"Время шага: {STEP_TIME_NOTE}.", "",
        "| Стадия | Запрос в журнале | Время шага, с | Попытки | Байты входа | Токены (оценка) | Бюджет, Б | Влезает | Байты ответа | Токены ответа (оценка) |", "|---|---|---|---|---|---|---|---|---|---|",
        *(f"| {_dash(c['stage'])} | {_dash(c['requested_at'])} | {_dash(c['step_sec'])} | {_dash(c['transport_attempts'])} | "
          f"{_size_cell(c)} | {_tokens_cell(c)} | {_dash(c.get('budget_bytes'))} | {'—' if c.get('by') in ('code', 'reuse') else _dash(c.get('fits'))} | "
          f"{_dash(c.get('output_raw_bytes') if c.get('output_raw_bytes') is not None else c.get('output_bytes'))}{' (большой)' if c.get('output_large') else ''} | "
          f"{_dash(c.get('output_tokens_est'))} |"
          for c in report["model_calls"]),
        f"| Итого по прогону | вызовов модели {totals['calls']} | | | {totals['input_bytes']} | {totals['input_tokens_est']} | | | "
        f"{totals['output_bytes']} | {totals['output_tokens_est']} |", "",
        "## Предупреждения", "", *(f"- {code}: {_WARNING_TEXT.get(code, code)}" for code in report["warnings"]),
        *(f"  - {row['case_id']} {row['step_id']}: {row['count']}" for row in report.get("data_mismatch") or []),
        *([f"  - групп требований {report['end_to_end']['groups']}, связывающих кейсов 0"] if report.get("end_to_end") else []),
        *([f"  - кейсов, повторяющих тесты проекта: {len(report['existing_tests']['repeating'])} из {report['existing_tests']['cases']}"
           f" ({', '.join(report['existing_tests']['repeating'][:10])})"] if report.get("existing_tests") and report["existing_tests"]["repeating"] else []),
        *([f"  - эталонов {report['reference']['reference']}, пар {report['reference']['pairs']}, пробелов {len(report['reference']['gaps'])}, "
           f"новых без пары {len(report['reference']['new'])}, возможных дублей {len(report['reference']['duplicates'])}"]
          if report.get("reference") and "CV_REFERENCE" in report["warnings"] else []),
        *([] if report["warnings"] else ["- нет"]), "",
        *_canary_lines(report),
        *_notes_section("Замечания ревью", "кейсы", report.get("review_notes")),
        *_notes_section("Замечания ревью автотестов", "автотесты", report.get("autotest_review_notes")),
        *_step_size_lines(report),
        *_phase_lines(report), *_composition_lines(report), *_refusal_lines(report), *_canon_lines(report),
        "## Остановки и ошибки", "", *(f"- {key}: {_dash(value) if not isinstance(value, list) else (', '.join(value) or '—')}" for key, value in report["stops"].items()), "",
    ]
    if report.get("scope"):
        scope = report["scope"]
        lines += ["## Охват после разметки", ""]
        if "refused" in scope:
            lines += [f"- {scope['refused']['code']}: ответ человека не читается как ответ человека, охват не определён; fix: {scope['refused']['fix']}", ""]
        else:
            lines += [f"- пробелов в разметке: {scope['gaps']}; ответ человека: {scope['answer']}",
                      *(f"- вне охвата {row['source_requirement_id']}: {row['reason']} (кейсы для него не пишутся)" for row in scope["out_of_scope"]),
                      *([f"- {scope['mismatch']}"] if scope.get("mismatch") else []), ""]
    if report.get("import"):
        summary = report["import"]
        lines += ["## Импорт кейсов", "", f"- CSV: {summary['csv']}; кейсов: {_dash(summary['cases'])}; нормализовано: "
                  f"{_dash(summary['normalized'])} ячеек кодом (пустая Test Data → «—», пустой Objective → Name)",
                  *([f"- требований без кейса в CSV: {len(summary['gaps'])}"
                     + (" (" + ", ".join(row["source_requirement_id"] for row in summary["gaps"]) + ")" if summary["gaps"] else "")
                     + f"; кейсов вне документов: {len(summary['outside'])}"
                     + (" (строки " + ", ".join(map(str, summary["outside"])) + ")" if summary["outside"] else "")]
                    if "gaps" in summary else []), ""]
    if report.get("test_design"):
        lines += [render_test_design(report["test_design"]), ""]
    if report.get("expectations"):
        lines += [render_expectations(report["expectations"]), ""]
    if report.get("autotest_assertions"):
        lines += [render_autotest_assertions(report["autotest_assertions"]), ""]
    if report["quarantine"] or report["quarantine_unresolved"]:
        lines += ["## Карантин", "", *(f"- {row['file']} :: {row['method']} ({row['ref']})" + (
            f"; упали строки {', '.join(row['failed_rows'])}; вместе с методом отключены прошедшие строки: "
            f"{', '.join(row['disabled_passed_rows']) or 'нет'}" if "failed_rows" in row else "") for row in report["quarantine"])]
        lines += [f"- {report['quarantine_unresolved']}"] if report["quarantine_unresolved"] else []
        lines.append("")
    lines += ["## Шаги", "", "| # | Событие | Стадия | Время |", "|---|---|---|---|",
              *(f"| {s['seq']} | {s['event']} | {_dash(s['stage'])} | {_dash(s['at'])} |" for s in report["steps"]), "",
              *_tmp_lines(report), "## Окружение", "", *(f"- {key}: {_dash(value)}" for key, value in report["environment"].items()), ""]
    return "\n".join(lines)


def write_report(project: Path, run_id: str) -> list[str]:
    """Build and write the report inside one closure_cache: each journal and receipt is checked once, not per read."""
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
        "questions-for-analyst.md": analyst_questions(warnings, *_attempt_notes(root), imported=report.get("import")).encode("utf-8"),
    }
    written = []
    for name, data in outputs.items():
        atomic_write_confined_bytes_at_root(project, root, root / name, data)
        written.append(str(root / name))
    return written


def _attempt_notes(run_root: Path) -> tuple[list[Any], list[Any]]:
    """The note texts of the case review and of the autotest review of the newest attempt."""
    from tools import pilot_state as pilot

    attempts = pilot.derive_state(Path(run_root))["attempts"]
    if not attempts:
        return [], []
    attempt_id = attempts[-1]["attempt_id"]
    return review_notes(run_root, attempt_id)["texts"], review_notes(run_root, attempt_id, "autotests")["texts"]


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
        data = analyst_questions(warnings, *_attempt_notes(run_root)).encode("utf-8")
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
