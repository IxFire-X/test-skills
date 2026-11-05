"""FaT-P3: the pause after the context marker — what the human decides before the batches are planned.

The marker keeps its gaps as four-field warnings (``context-artifact-contract.md``).  Requirements without an observable
channel get a case with a blocker — on the 02.11 run 17 of 40.  After the marker the human answers once: «исключи <SREQ>»
(``plan-batches --exclude <id> --reason <text>``) or «продолжай» (``plan-batches --continue``).  The answer is a run
artifact (``scope-decision.json``, published like any run artifact); a plan without the answer is refused with
``SCOPE_PENDING``.  ``scan --no-pause`` (kept in the scan request of the manifest) lifts the stop.

This is a pause, not a driver: the code lists, checks and records; it decides nothing and calls no model.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.fixes import fix_for

DECISION_NAME = "scope-decision.json"
DECISION_BATCH = "scope-decision-v1"  # J1 (A1-9): the events of the decision carry it
REASON_LIMIT = 200  # the reason is the human's text and goes into the report: a line, not a document
LISTED = 20  # the gaps `status` lists inline; the whole list is the draft questions-for-analyst.md
_SEPARATOR = " — "


class ScopePending(ValueError):
    code = "SCOPE_PENDING"


class ScopeRefused(ValueError):
    """A refusal of the answer or of a plan that does not carry it; ``code`` is the machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _marker(root: Path, state: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any] | None:
    """The merged (or single) ``context-marker:baseline`` answer of the attempt, or None before it."""
    from tools import pilot_state

    answers = [str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id
               and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == "context-marker:baseline"]
    if len(answers) != 1:
        return None
    try:
        return pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", answers[0])["artifact"]
    except (OSError, KeyError, ValueError):
        return None


def requirements_of(marker: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(marker["artifacts"]["analytics_documentation"]["requirements"])


def _owners(token: str, requirements: Sequence[Mapping[str, Any]]) -> list[str]:
    """The source requirements a warning names by its first word: the SREQ id itself, else the requirements whose text
    holds that word (``FR-12`` in «FR-12 Оплата…») — a hint for the human, never a decision."""
    ids = [str(row["source_requirement_id"]) for row in requirements]
    if token in ids:
        return [token]
    word = re.compile(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])")
    return [str(row["source_requirement_id"]) for row in requirements if word.search(str(row.get("text") or ""))]


def gap_rows(marker: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The four-field gap warnings of the marker, each with the source requirements it names."""
    requirements = requirements_of(marker)
    rows = []
    for warning in marker.get("warnings") or []:
        text = " ".join(str(warning).split())
        parts = [part.strip() for part in text.split(_SEPARATOR)]
        if len(parts) == 4 and all(parts):
            token = parts[0].split(" ", 1)[0]
            rows.append({"requirement": token, "source_requirement_ids": _owners(token, requirements), "missing": parts[1],
                         "blocks": parts[2], "question": parts[3], "warning": text})
    return rows


def draft_questions(marker: Mapping[str, Any]) -> str:
    """``questions-for-analyst.md`` of the marker's warnings alone — the same text the report builds from all of the run's."""
    from tools.run_report import analyst_questions

    return analyst_questions(marker.get("warnings") or [])


def _bound(root: Path, attempt_id: str, raw: bytes, state: Mapping[str, Any] | None) -> bool:
    """J1 (A1-9): the decision is a fact of the run only with its ARTIFACT_READ_BACK in the journal (``bind_bytes``, batch ``scope-decision-v1``)."""
    from tools import pilot_state

    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    events = (state if state is not None else pilot_state.derive_state(root))["events"]
    return any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") == DECISION_BATCH
               and event.get("artifact_digest") == digest for event in events)


def decision_file_unbound(root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> bool:
    """A file of a decision lies in the run without the events of the journal — an agent's file or the leftover of a cut command; it decides nothing."""
    from tools.confined_output import OutputConfinementError, read_confined_bytes

    root = Path(root).resolve()
    try:
        raw = read_confined_bytes(root.parents[1], root, root / "artifacts" / attempt_id / DECISION_NAME)
    except (OutputConfinementError, OSError):
        return False
    return raw is not None and not _bound(root, attempt_id, raw, state)


def read_decision(root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """The recorded answer of the human, read the way it was written (confined, strict JSON) and checked for its shape: a file
    that is not what ``record_decision`` writes is ``SCOPE_DECISION_INVALID`` — never a decision, never silently «none».  J1 (A1-9): and
    a decision only from the journal — a file of the right shape without its events (planted, or left by a cut command) is none."""
    from tools.confined_output import OutputConfinementError, read_confined_bytes
    from tools.schema_validation import StrictJsonError, loads_json_strict

    root = Path(root).resolve()
    target = root / "artifacts" / attempt_id / DECISION_NAME
    try:
        raw = read_confined_bytes(root.parents[1], root, target)
    except (OutputConfinementError, OSError) as error:
        raise ScopeRefused("SCOPE_DECISION_INVALID", f"{DECISION_NAME} не читается как файл прогона ({type(error).__name__}); "
                           "fix: не подменяй артефакты прогона; другое решение — новый прогон (scan --new-run)") from None
    if raw is None:
        return None
    try:
        value = loads_json_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError, ValueError):
        value = None
    excluded = value.get("excluded") if isinstance(value, dict) else None
    rows_ok = isinstance(excluded, list) and all(
        isinstance(row, dict) and set(row) == {"source_requirement_id", "reason"} and all(isinstance(item, str) and item.strip() for item in row.values())
        and row["source_requirement_id"].startswith("SREQ-") for row in excluded)
    ids = [row["source_requirement_id"] for row in excluded] if rows_ok else []
    valid = (isinstance(value, dict) and set(value) == {"schema_version", "kind", "decision", "excluded"} and value["schema_version"] == "1.0.0"
             and value["kind"] == "scope-decision" and rows_ok and len(set(ids)) == len(ids)
             and ((value["decision"] == "exclude" and bool(excluded)) or (value["decision"] == "continue" and not excluded)))
    if not valid:
        raise ScopeRefused("SCOPE_DECISION_INVALID", f"{DECISION_NAME} не имеет вида решения человека (schema_version, kind scope-decision, "
                           "decision exclude|continue, excluded: пусто при continue и не пусто при exclude); "
                           "fix: не правь артефакты прогона руками; другое решение — новый прогон (scan --new-run)")
    if not _bound(root, attempt_id, raw, state):
        return None
    return value


def _no_pause(root: Path) -> bool:
    from tools import pilot_state

    try:
        request = pilot_state.read_run(root)["manifest"].get("scan_request") or {}
    except (OSError, KeyError, ValueError):
        return False
    return bool(request.get("no_pause"))


def scope_state(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``pending`` — the marker has gaps, the human has not answered and the run was not started with ``--no-pause``."""
    from tools import pilot_state

    root = Path(run_root).resolve()
    state = state if state is not None else pilot_state.derive_state(root)
    marker = _marker(root, state, attempt_id)
    gaps = gap_rows(marker) if marker is not None else []
    decision = read_decision(root, attempt_id, state)
    no_pause = _no_pause(root)
    return {"pending": bool(gaps) and decision is None and not no_pause, "gaps": gaps, "decision": decision, "no_pause": no_pause,
            "known": marker is not None}


def pending_fix(root: Path, attempt_id: str, gaps: Sequence[Mapping[str, Any]]) -> str:
    return ("покажи человеку требования ниже (полный текст вопросов — questions-for-analyst.md в каталоге прогона) и спроси: "
            "«исключить какие-то из них или продолжать как есть?»; ответ передай: `python -m tools.api plan-batches ... "
            f"--run-root {root} --attempt {attempt_id} --exclude <SREQ-id> [--exclude …] --reason \"<текст человека>\"` "
            "или `--continue`; прогон без остановки — начни его с `scan --no-pause`")


def pending_message(root: Path, attempt_id: str, gaps: Sequence[Mapping[str, Any]]) -> str:
    named = [f"{', '.join(row['source_requirement_ids']) or row['requirement']}: {row['missing']}" for row in gaps[:LISTED]]
    unbound = (f" (файл {DECISION_NAME} лежит в прогоне без событий журнала — решением не считается)" if decision_file_unbound(root, attempt_id) else "")
    more = f" (и ещё {len(gaps) - LISTED})" if len(gaps) > LISTED else ""
    return (f"SCOPE_PENDING: разметка оставила {len(gaps)} требований без наблюдаемого канала или другого пробела; "
            f"человек ещё не решил, исключать ли их — {'; '.join(named)}{more}{unbound}; fix: {pending_fix(root, attempt_id, gaps)}")


def write_draft(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> Path | None:
    """The draft ``questions-for-analyst.md`` of the marker's gaps (the report rewrites the file with the whole run's);
    None while the marker has no gaps."""
    from tools import pilot_state

    root = Path(run_root).resolve()
    state = state if state is not None else pilot_state.derive_state(root)
    marker = _marker(root, state, attempt_id)
    if marker is None or not gap_rows(marker):
        return None
    from tools.confined_output import atomic_write_confined_bytes_at_root

    target = root / "questions-for-analyst.md"  # the way the report writes it: confined, never through a link
    atomic_write_confined_bytes_at_root(root.parents[1], root, target, draft_questions(marker).encode("utf-8"))
    return target


def record_decision(run_root: Path, attempt_id: str, *, excluded: Sequence[str], reason: str | None, proceed: bool) -> dict[str, Any]:
    """Publish the human's answer as the run artifact ``scope-decision.json``; the same answer again changes nothing, another
    one is refused (the decision is evidence — a different scope is a new run)."""
    from tools import pilot_state

    root = Path(run_root).resolve()
    if bool(excluded) == proceed:
        raise ScopeRefused("SCOPE_ANSWER_INVALID", "дай ровно один ответ человека: --exclude <SREQ-id> --reason <текст> или --continue; "
                           "fix: повтори plan-batches с одним из двух")
    state = pilot_state.derive_state(root)
    marker = _marker(root, state, attempt_id)
    if marker is None:
        raise ScopeRefused("SCOPE_NO_MARKER", "ответа разметки (context-marker:baseline) ещё нет: нечего исключать; "
                           "fix: сначала разметка и слияние частей")
    if excluded:
        known = {str(row["source_requirement_id"]) for row in requirements_of(marker)}
        unknown = sorted(set(excluded) - known)
        if unknown:
            raise ScopeRefused("SCOPE_UNKNOWN_REQUIREMENT", f"в разметке нет требований {', '.join(unknown)}; fix: SREQ-id из списка status "
                               "или из ответа context-marker:baseline")
        text = " ".join(str(reason or "").split())
        if not text:
            raise ScopeRefused("SCOPE_REASON_REQUIRED", "исключение без причины: --reason <текст человека>; fix: повтори с --reason")
        if len(excluded) == len(known):
            raise ScopeRefused("SCOPE_EMPTY", "исключены все требования — кейсам нечего покрывать; fix: оставь хотя бы одно или закрой прогон")
        value: dict[str, Any] = {"schema_version": "1.0.0", "kind": "scope-decision", "decision": "exclude",
                                 "excluded": [{"source_requirement_id": item, "reason": text[:REASON_LIMIT]} for item in sorted(set(excluded))]}
    else:
        value = {"schema_version": "1.0.0", "kind": "scope-decision", "decision": "continue", "excluded": []}
    existing = read_decision(root, attempt_id, state)
    if existing is not None and existing != value:
        raise ScopeRefused("SCOPE_DECISION_CONFLICT", f"решение уже записано ({existing['decision']}: "
                           f"{', '.join(row['source_requirement_id'] for row in existing['excluded']) or 'без исключений'}); "
                           "fix: другое решение — новый прогон (scan --new-run)")
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    published = pilot_state.bind_bytes(root, attempt_id, root / "artifacts" / attempt_id / DECISION_NAME, data,
                                       digest="sha256:" + hashlib.sha256(data).hexdigest(), batch_id=DECISION_BATCH, label="scope decision")
    return {**value, "path": published["path"], "digest": published["digest"]}


def out_of_scope(decision: Mapping[str, Any] | None) -> list[dict[str, str]]:
    return [dict(row) for row in (decision or {}).get("excluded") or []]


def scope_state_source(root: Path, state: Mapping[str, Any] | None) -> Mapping[str, Any]:
    from tools import pilot_state

    return state if state is not None else pilot_state.derive_state(root)


def check_plan(run_root: Path, attempt_id: str, plan: Mapping[str, Any], state: Mapping[str, Any] | None = None) -> None:
    """Publish time of the plan: it carries the answer of the human — no plan before it, no other ``out_of_scope`` than it."""
    root = Path(run_root).resolve()
    scope = scope_state(root, attempt_id, state)
    if scope["pending"]:
        raise ScopePending(pending_message(root, attempt_id, scope["gaps"]))
    marker = _marker(root, scope_state_source(root, state), attempt_id)
    listed_sources = plan.get("source_requirements") if isinstance(plan, Mapping) else None
    if marker is not None and isinstance(listed_sources, list):
        # every requirement of the marker is in the plan or in out_of_scope with a reason — none is dropped silently
        wanted = {str(row["source_requirement_id"]) for row in requirements_of(marker)}
        covered = {str(row.get("source_requirement_id")) for row in listed_sources if isinstance(row, Mapping)} | {
            str(row.get("source_requirement_id")) for row in plan.get("out_of_scope") or [] if isinstance(row, Mapping)}
        if wanted != covered:
            raise ScopeRefused("SCOPE_PLAN_MISMATCH", f"план теряет требования разметки без записи в out_of_scope: {sorted(wanted - covered) or 'нет'}, "
                               f"лишние: {sorted(covered - wanted) or 'нет'}; fix: пересобери план командой plan-batches из всех требований разметки")
    expected = sorted(row["source_requirement_id"] for row in out_of_scope(scope["decision"]))
    listed = plan.get("out_of_scope") if isinstance(plan, Mapping) else None
    given = sorted(str(row.get("source_requirement_id")) for row in listed or [] if isinstance(row, Mapping))
    if given != expected:
        raise ScopeRefused("SCOPE_PLAN_MISMATCH", f"план называет вне охвата {given or 'ничего'}, решение человека — {expected or 'без исключений'}; "
                           "fix: пересобери план командой plan-batches с --run-root и --attempt этого прогона")


def plan_of(run_root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    """The published batch plan of the attempt, or None (a run before the plan was published as an input, or none yet)."""
    from tools import pilot_state

    root = Path(run_root).resolve()
    try:
        return pilot_state._generator_input(root.parents[1], root, pilot_state.derive_state(root), attempt_id, "plan")
    except (pilot_state.ModelInputsMismatch, OSError, KeyError, ValueError, IndexError):
        return None


def _rows(rows: Any) -> list[tuple[str, str]]:
    return sorted((str(row.get("source_requirement_id")), str(row.get("reason"))) for row in rows or [] if isinstance(row, Mapping))


def published_scope(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Once the plan is published its ``out_of_scope`` is the scope of the run: the plan is bound by the journal, the file
    ``scope-decision.json`` is not (it has no event).  None before the plan.  ``mismatch`` — the file says another thing than the
    plan (or is no answer at all): a rewritten file never changes what the report and ``status`` say, it is named."""
    root = Path(run_root).resolve()
    plan = plan_of(root, attempt_id)
    if plan is None:
        return None
    in_plan = [{"source_requirement_id": str(row["source_requirement_id"]), "reason": str(row["reason"])}
               for row in plan.get("out_of_scope") or [] if isinstance(row, Mapping)]
    try:
        decision, invalid = read_decision(root, attempt_id), False
    except ScopeRefused:
        decision, invalid = None, True
    mismatch = invalid or _rows(out_of_scope(decision)) != _rows(in_plan)
    note = None
    if mismatch:
        said = "не читается как решение человека" if invalid else (
            f"называет вне охвата {', '.join(row['source_requirement_id'] for row in out_of_scope(decision)) or 'ничего'}")
        note = (f"SCOPE_DECISION_MISMATCH: {DECISION_NAME} {said}, а опубликованный план — "
                f"{', '.join(row['source_requirement_id'] for row in in_plan) or 'ничего'}; охват взят из плана; "
                f"fix: {fix_for('SCOPE_DECISION_MISMATCH')}")
    return {"out_of_scope": in_plan, "decision": decision, "invalid": invalid, "mismatch": mismatch, "note": note}


def marker_requirements_in_scope(marker_requirements: Any, run_root: Path, attempt_id: str) -> Any:
    """The marker's requirements as the candidate document holds them: those the human excluded left out (see
    ``batch_assembly.in_scope_requirements``).  Anything that is no list is returned as it is — the check that compares it fails by itself."""
    from tools.batch_assembly import in_scope_requirements

    if not isinstance(marker_requirements, list):
        return marker_requirements
    plan = plan_of(run_root, attempt_id)
    return in_scope_requirements(marker_requirements, plan) if plan and plan.get("out_of_scope") else marker_requirements
