"""V2: the agent's cycle for the stages that went over to it — ``status`` → ``prepare <stage>`` → the model → the answer in
``<run>/incoming/<stage>.json`` → ``submit <stage>`` → ``status``.  ``prepare`` publishes the request from the journal and says what the model
reads and where the answer goes; ``submit`` reads the answer from that one place, checks and records it (``--dry-run`` writes nothing); both are
safe to repeat.  A stage joins by a row of ``STEPS``: its steps (``input_digests``, size, ``done``) and what the code does inside the call that
recorded an answer.  The code counts inside the step the agent called: it calls no model, starts no process, chooses no next step."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import batch_assembly, cases_import, confined_output, context_marker_parts, output_guard, pilot_state, scope_pause, step_size
from tools.project_inventory import read_inventory_receipt

SLOT_DIR = "incoming"


def slot(root: Path, stage: str) -> Path:
    """Where the agent puts the answer of ``stage``: built here from the id of a stage the table knows, never taken from the agent."""
    return Path(root) / SLOT_DIR / (stage.replace(":", "-") + ".json")


def _marker_accepted(root: Path) -> Mapping[str, Any]:
    """The last part answered: the merge by code is the one more step of that call."""
    if context_marker_parts.plan(root)["next"] != "merge":
        return {}
    merged = context_marker_parts.merge_parts(root)
    return {"merged": {key: merged[key] for key in ("content_digest", "questions_draft") if key in merged}}


def _scope_steps(root: Path) -> list[dict[str, Any]]:
    """The scope the human sets after the marker: a step without a request of a model (``direct``: what ``prepare`` says, what ``submit`` does with the answer)."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    scope = scope_pause.scope_state(root, attempt_id, state)
    return [{"stage": "scope", "waiting_for": "human", "done": scope["decision"] is not None, "recheck": True, **scope,
             "direct": (lambda root, step: scope_pause.ask(root, attempt_id, step), lambda root, value, dry_run: scope_pause.take(root, attempt_id, value, dry_run))}
            ] if scope["known"] else []


def _plan_steps(root: Path) -> list[dict[str, Any]]:
    """The plan of the batches once the marker is answered (``direct`` too: no request of a model): the agent names the subject, the header, the plan and the
    skeletons come from the journal.  Published — the step stands as done and its slot is not read again."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    done = pilot_state.published_generator_inputs(root.parents[1], root, state, attempt_id) is not None
    marked = any(event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == "context-marker:baseline"
                 for event in state["events"])  # the answer is there, readable or not: a changed context file is the plan's refusal, not an unknown stage
    return [{"stage": "plan", "waiting_for": "agent", "done": done,
             "direct": (lambda root, step: pilot_state.plan_ask(root, attempt_id, step["done"]),
                        lambda root, value, dry_run: pilot_state.publish_plan(root, attempt_id, value, dry_run))}
            ] if marked else []


MANUAL_SHARE_WARN = 0.5  # a candidate of a project with code whose manual steps are over this share: the reviewer's check is not measured


def _batch_stage(batch: Mapping[str, Any]) -> str:
    return f"tc-generator:{batch['batch_id']}"


def _generator_steps(root: Path) -> list[dict[str, Any]]:
    """The batches of the published plan in order; one waits for the answer of the batch before it (the order is the code's)."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    published = pilot_state.published_generator_inputs(root.parents[1], root, state, attempt_id)
    if published is None:
        return []
    done = {event.get("stage_instance_id") for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"}
    steps: list[dict[str, Any]] = []
    for batch in sorted(published[0]["batches"], key=pilot_state.batch_ordinal):
        step: dict[str, Any] = {"stage": _batch_stage(batch), "input_digests": [], "done": _batch_stage(batch) in done,
                                "invocation": f"tc-generator-{batch['namespace']}"}  # a label: no ":" and no long hex (the id of the batch reads as a secret)
        if pilot_state.batch_ordinal(batch) > 1:  # the capabilities of the batches before it are its fifth input, published by the code before its request
            step["before"] = lambda batch_id=batch["batch_id"]: pilot_state.publish_prior_capabilities(root, attempt_id, batch_id)
        if steps and not steps[-1]["done"]:
            step["waits"] = f"ждёт принятого ответа {steps[-1]['stage']}"
        steps.append(step)
    return steps


def _manual_share(root: Path, document: Mapping[str, Any]) -> dict[str, Any] | None:
    """The manual steps of a candidate of a project with code (the frozen inventory has a source or a test file) when they are over the share."""
    state = pilot_state.derive_state(root)
    inventory = read_inventory_receipt(root / "inventories" / (str(pilot_state.frozen_baseline(root, state["attempts"][-1])["inventory_digest"]).removeprefix("sha256:") + ".json"))
    steps = [step for case in document["test_cases"] for step in case["steps"]]
    manual = sum(1 for step in steps if step["manual_only"])
    if not steps or manual / len(steps) <= MANUAL_SHARE_WARN or not any(row["kind"] in ("source", "test") for row in inventory["files"]):
        return None
    return {"code": "MANUAL_SHARE_HIGH", "manual_steps": manual, "steps": len(steps)}


def _candidate(root: Path, document: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """What the agent is told of the assembled candidate: digests and numbers (the review takes the files from the journal, none is written)."""
    share = _manual_share(root, document)
    return {"candidate": {"document_digest": receipt["document_digest"], "assembly_receipt_digest": receipt["digest"], "cases": len(document["test_cases"]),
                          "warnings": sorted({*(row["code"] for row in receipt["warnings"]), *([share["code"]] if share else [])}), **({"manual_share": share} if share else {})}}


def _assemble(root: Path, stage: str, value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Before the write of the last answer of the generator: the candidate of all the batches — a refusal leaves the answer unwritten, so the one retry is
    still there.  The same ``assemble_candidate`` the review reads from the journal (``journal_assembly``)."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    plan, header = (pilot_state.generator_input(root.parents[1], root, state, attempt_id, kind) for kind in ("plan", "header"))
    if stage != _batch_stage(max(plan["batches"], key=pilot_state.batch_ordinal)):
        return None
    earlier = pilot_state.accepted_earlier_fragments(root.parents[1], root, state, attempt_id, plan, stage.split(":", 1)[1])
    return _candidate(root, *batch_assembly.assemble_candidate(dict(header), dict(plan), [*earlier, dict(value)]))


def _generator_accepted(root: Path) -> Mapping[str, Any]:
    """Every batch answered: the candidate the journal gives (a repeated ``submit`` of the last batch says it again)."""
    from tools.orchestrate_test_case_revision import journal_assembly

    if not all(step["done"] for step in _generator_steps(root)):
        return {}
    return _candidate(root, *journal_assembly(root, str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]))[:2])


STEPS: dict[str, tuple[Callable, Callable, Callable | None]] = {
    "context-marker": (lambda root: context_marker_parts.plan(root)["parts"], _marker_accepted, None),
    "tc-generator": (_generator_steps, _generator_accepted, _assemble),
    "scope": (_scope_steps, lambda root: {}, None),
    "plan": (_plan_steps, lambda root: {}, None),
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
                         f"({', '.join(item['stage'] for item in steps) or 'эта стадия идёт своей командой или её в этом прогоне нет (scope: разметка ещё не принята) — next.verb'})")
    return project, root, state, str(state["attempts"][-1]["attempt_id"]), step


def _accepted(root: Path, stage: str) -> Mapping[str, Any]:
    return STEPS[stage.split(":", 1)[0]][1](root)


def _budget(root: Path, step: Mapping[str, Any], found: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The size of the step against the budget: a step of the table has its bytes, a batch of the generator the files of its published request."""
    if "bytes" in step:
        return {"bytes": step["bytes"]["total"], "budget_bytes": step_size.step_budget(root.parents[1]), "fits": step["fits"], "hint": step["hint"]}
    found = found or step_size.stage_inputs(root, step["stage"], write_views=False)
    report = step_size.measure(root.parents[1], [Path(row["path"]) for row in found["files"]], step["stage"].split(":", 1)[0],
                               extra_bytes=sum(row["bytes"] for row in found["bindings"]))
    return {key: report[key] for key in ("bytes", "budget_bytes", "fits", "hint")}


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
    if "waits" in step:
        raise ValueError(f"STAGE_WAITS: {stage}: {step['waits']}; fix: сначала prepare и submit этой стадии")


def prepare(run_root: Path, stage: str, *, model_id: str | None = None, invocation_id: str | None = None, reuse_from: str | None = None) -> dict[str, Any]:
    """Publish the request of ``stage`` and say what the model reads and where the answer goes; a reuse answers it from the source run.
    ``invocation_id`` is the host's label of the session that does the work (the review compares it with the reviewer's: same label, no independence);
    without it the code's label."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if "by_code" in step:  # the requirements of an import without documents: the code answers (the request, the answer and the skeleton are one transition)
        return {"stage": stage, **{key: value for key, value in cases_import.import_requirements(root, attempt_id).items() if key != "artifact"}}
    if "direct" in step:  # no request of a model: the step says what the answer needs and takes it itself
        if not step["done"]:
            _refuse_linked_inbox(root)
        asked = step["direct"][0](root, step)
        if not step["done"] and not (root / SLOT_DIR).is_dir():
            confined_output.create_confined_directory_exclusive(project, root, root / SLOT_DIR)
        return {"stage": stage, **asked, **({} if step["done"] else {"answer_slot": str(slot(root, stage)), "waiting_for": step["waiting_for"]})}
    if _events(state, attempt_id, stage, "MODEL_RESPONSE_RECEIVED"):
        return {"stage": stage, "answered": True}
    _refuse_unready(stage, step)
    if reuse_from is None:
        _refuse_linked_inbox(root)
    requested = _events(state, attempt_id, stage, "MODEL_REQUESTED")
    if not requested and "before" in step:
        step["before"]()
    request = (pilot_state.read_model_request(root, attempt_id, stage, str(requested[-1]["artifact_digest"])) if requested else
               pilot_state.publish_model_request(root, attempt_id, stage, model_id=model_id, invocation_id=invocation_id or step.get("invocation") or stage.replace(":", "-"), input_digests=step["input_digests"],
                                                 reuse_from=reuse_from))
    reuse = request.get("reuse")
    if isinstance(reuse, Mapping):  # the answer is the source run's, read from it — the slot is not looked at
        source = pilot_state.read_model_stage_artifact(project / ".pilot-runs" / output_guard.run_name(reuse["run_id"]), reuse["attempt_id"], stage,
                                                       reuse["response_digest"])
        pilot_state.publish_model_stage_artifact(root, attempt_id, stage, source["artifact"])
        return {"stage": stage, "answered": True, "model_call": False, "reused_from": reuse["run_id"], **_accepted(root, stage)}
    if not (root / SLOT_DIR).is_dir():
        confined_output.create_confined_directory_exclusive(project, root, root / SLOT_DIR)
    found = step_size.stage_inputs(root, stage)
    word = {key: request[key] for key in ("nonce", "nonce_note") if key in request}  # the control word is shown once, by the call that wrote the request
    if requested and stage.startswith("tc-generator:"):
        word = {"nonce_note": pilot_state.codeword_lost_note(root, state, attempt_id, stage).strip()}
    return {"stage": stage, "request_digest": request["digest"], "answer_slot": str(slot(root, stage)), "read": step_size.compact_inputs(root, stage, found)["files_summary"],
            "budget": _budget(root, step, found), **word}


def submit(run_root: Path, stage: str, load: Callable[[str, Path], Any], *, dry_run: bool = False, transport_attempts: int = 1) -> dict[str, Any]:
    """Take the answer of ``stage`` from its slot and record it; ``load(path, project)`` reads the file strictly (``api._load_answer``)."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if "direct" in step:  # a recorded answer stands without a file; where the step rechecks, a file there is checked again
        if step["done"] and not (step.get("recheck") and slot(root, stage).is_file()):
            return {"stage": stage, **step["direct"][0](root, step)}
        return {"stage": stage, **step["direct"][1](root, load(str(slot(root, stage)), project).value, dry_run)}
    if _events(state, attempt_id, stage, "MODEL_RESPONSE_RECEIVED"):  # the recorded answer stands: the slot is not read again
        return {"stage": stage, "answered": True, **({} if dry_run else _accepted(root, stage))}
    _refuse_unready(stage, step)
    if not _events(state, attempt_id, stage, "MODEL_REQUESTED"):
        raise ValueError(f"STAGE_NOT_PREPARED: {stage} has no request yet; fix: prepare --stage {stage}, затем модель, затем submit")
    answer = load(str(slot(root, stage)), project)
    check, told = STEPS[stage.split(":", 1)[0]][2], {}
    published = pilot_state.publish_model_stage_artifact(root, attempt_id, stage, answer.value, transport_attempts=transport_attempts, raw_bytes=answer.raw_bytes,
                                                         dry_run=dry_run, before_write=(lambda value: told.update(check(root, stage, value) or {})) if check else None)
    return {**{key: value for key, value in published.items() if key != "artifact"}, **(told or ({} if dry_run else _accepted(root, stage)))}


def stage_status(root: Path, family: str) -> dict[str, Any]:
    """What ``status`` says of the marker or the generator: the next verb and the id of the step, who waits, the slot and the size.  Only reads."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    parts = STEPS[family][0](root) or STEPS["plan"][0](root)  # the generator before its plan: the step of the plan
    if not parts:
        return {}
    pending = [part for part in parts if not part["done"]]
    asked = next((part for part in pending if _events(state, attempt_id, part["stage"], "MODEL_REQUESTED")), None)
    step = asked or (pending[0] if pending else parts[-1])  # every part of the marker answered, the merge not: submit repeats it
    verb = "submit" if asked or not pending or ("direct" in step and slot(root, step["stage"]).is_file()) else "prepare"
    return {"next": {"verb": verb, "stage": step["stage"]}, "waiting_for": "model" if asked else "agent",
            **({"budget": _budget(root, step)} if "bytes" in step or asked else {}), **({} if "by_code" in step else {"answer_slot": str(slot(root, step["stage"]))})}
