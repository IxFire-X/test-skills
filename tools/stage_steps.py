"""V2: the agent's cycle for the stages that went over to it — ``status`` → ``prepare <stage>`` → the model → the answer in
``<run>/incoming/<stage>.json`` → ``submit <stage>`` → ``status``.  ``prepare`` publishes the request from the journal and says what the model
reads and where the answer goes; ``submit`` reads the answer from that one place, checks and records it (``--dry-run`` writes nothing); both are
safe to repeat.  A stage joins by a row of ``STEPS``: its steps (``input_digests``, size, ``done``) and what the code does inside the call that
recorded an answer.  The code counts inside the step the agent called: it calls no model, starts no process, chooses no next step."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import confined_output, context_marker_parts, output_guard, pilot_state, step_size

SLOT_DIR = "incoming"


def slot(root: Path, stage: str) -> Path:
    """Where the agent puts the answer of ``stage``: built here from the id of a stage the table knows, never taken from the agent."""
    return Path(root) / SLOT_DIR / (stage.replace(":", "-") + ".json")


def _marker_accepted(root: Path) -> Mapping[str, Any] | None:
    """The last part answered: the merge by code is the one more step of that call."""
    return context_marker_parts.merge_parts(root) if context_marker_parts.plan(root)["next"] == "merge" else None


STEPS: dict[str, tuple[Callable, Callable]] = {
    "context-marker": (lambda root: context_marker_parts.plan(root)["parts"], _marker_accepted),
}


def _events(state: Mapping[str, Any], attempt_id: str, stage: str, kind: str) -> list[Mapping[str, Any]]:
    return [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage and event["event_type"] == kind]


def _locate(run_root: Path, stage: str) -> tuple[Path, Path, Mapping[str, Any], str, Mapping[str, Any]]:
    project, root = output_guard.existing_run(run_root)
    state = pilot_state.derive_state(root)
    if not state["attempts"]:
        raise ValueError("STAGE_NO_ATTEMPT: the run has no attempt yet; fix: scan создаёт попытку — повтори scan")
    row = STEPS.get(stage.split(":", 1)[0])
    steps = row[0](root) if row else []
    step = next((item for item in steps if item["stage"] == stage), None)
    if step is None:
        raise ValueError(f"STAGE_UNKNOWN: {stage} — не стадия, которую ведут prepare и submit; fix: next.stage из `status` "
                         f"({', '.join(item['stage'] for item in steps) or 'эта стадия идёт своей командой — next.verb'})")
    return project, root, state, str(state["attempts"][-1]["attempt_id"]), step


def _accepted(root: Path, stage: str) -> dict[str, Any]:
    merged = STEPS[stage.split(":", 1)[0]][1](root)
    return {"merged": {key: merged[key] for key in ("content_digest", "questions_draft") if key in merged}} if merged else {}


def _budget(root: Path, step: Mapping[str, Any]) -> dict[str, Any]:
    return {"bytes": step["bytes"]["total"], "budget_bytes": step_size.step_budget(root.parents[1]), "fits": step["fits"], "hint": step["hint"]}


def _refuse_linked_inbox(root: Path) -> None:
    """``incoming`` that is a link or a junction leads out of the run: no answer is taken from it and none is announced for it."""
    try:
        details = (root / SLOT_DIR).lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400 or not stat.S_ISDIR(details.st_mode):
        raise ValueError(f"STAGE_INBOX_UNSAFE: {SLOT_DIR} of the run is a link, a junction or no directory; fix: покажи это человеку — каталог "
                         f"{SLOT_DIR} в прогоне подменён, ответ в него не клади; прогон продолжать нельзя — новый прогон тем же scan с --new-run")


def _refuse_unready(stage: str, step: Mapping[str, Any]) -> None:
    if "by_code" in step:
        raise ValueError(f"STAGE_BY_CODE: {stage} отвечает код, не модель; fix: {step['by_code']}")
    if "waits" in step:
        raise ValueError(f"STAGE_WAITS: {stage}: {step['waits']}; fix: сначала prepare и submit этой стадии")


def prepare(run_root: Path, stage: str, *, model_id: str | None = None, reuse_from: str | None = None) -> dict[str, Any]:
    """Publish the request of ``stage`` and say what the model reads and where the answer goes; a reuse answers it from the source run."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if _events(state, attempt_id, stage, "MODEL_RESPONSE_RECEIVED"):
        return {"stage": stage, "answered": True}
    _refuse_unready(stage, step)
    if reuse_from is None:
        _refuse_linked_inbox(root)
    requested = _events(state, attempt_id, stage, "MODEL_REQUESTED")
    request = (pilot_state.read_model_request(root, attempt_id, stage, str(requested[-1]["artifact_digest"])) if requested else
               pilot_state.publish_model_request(root, attempt_id, stage, model_id=model_id, invocation_id=stage, input_digests=step["input_digests"],
                                                 reuse_from=reuse_from))
    reuse = request.get("reuse")
    if isinstance(reuse, Mapping):  # the answer is the source run's, read from it — the slot is not looked at
        source = pilot_state.read_model_stage_artifact(project / ".pilot-runs" / output_guard.run_name(reuse["run_id"]), reuse["attempt_id"], stage,
                                                       reuse["response_digest"])
        pilot_state.publish_model_stage_artifact(root, attempt_id, stage, source["artifact"])
        return {"stage": stage, "answered": True, "model_call": False, "reused_from": reuse["run_id"], **_accepted(root, stage)}
    if not (root / SLOT_DIR).is_dir():
        confined_output.create_confined_directory_exclusive(project, root, root / SLOT_DIR)
    listed = step_size.compact_inputs(root, stage, step_size.stage_inputs(root, stage))["files_summary"]
    return {"stage": stage, "request_digest": request["digest"], "answer_slot": str(slot(root, stage)), "read": listed, "budget": _budget(root, step)}


def submit(run_root: Path, stage: str, load: Callable[[str, Path], Any], *, dry_run: bool = False, transport_attempts: int = 1) -> dict[str, Any]:
    """Take the answer of ``stage`` from its slot and record it; ``load(path, project)`` reads the file strictly (``api._load_answer``)."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if _events(state, attempt_id, stage, "MODEL_RESPONSE_RECEIVED"):  # the recorded answer stands: the slot is not read again
        return {"stage": stage, "answered": True, **({} if dry_run else _accepted(root, stage))}
    _refuse_unready(stage, step)
    if not _events(state, attempt_id, stage, "MODEL_REQUESTED"):
        raise ValueError(f"STAGE_NOT_PREPARED: {stage} has no request yet; fix: prepare --stage {stage}, затем модель, затем submit")
    answer = load(str(slot(root, stage)), project)
    published = pilot_state.publish_model_stage_artifact(root, attempt_id, stage, answer.value, transport_attempts=transport_attempts,
                                                         raw_bytes=answer.raw_bytes, dry_run=dry_run)
    return {**{key: value for key, value in published.items() if key != "artifact"}, **({} if dry_run else _accepted(root, stage))}


def marker_status(root: Path) -> dict[str, Any]:
    """What ``status`` says of the marker: the next verb and the id of the step, who waits, the slot and the size.  Only reads."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    parts = context_marker_parts.plan(root)["parts"]
    pending = [part for part in parts if not part["done"]]
    asked = next((part for part in pending if _events(state, attempt_id, part["stage"], "MODEL_REQUESTED")), None)
    step = asked or (pending[0] if pending else parts[-1])  # every part answered, the merge not: submit repeats it
    verb = "submit" if asked or not pending else "cases-import" if "by_code" in step else "prepare"
    return {"next": {"verb": verb, "stage": step["stage"]}, "waiting_for": "model" if asked else "agent", "budget": _budget(root, step),
            **({} if verb == "cases-import" else {"answer_slot": str(slot(root, step["stage"]))})}
