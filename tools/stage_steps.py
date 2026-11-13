"""V2: the agent's cycle for the stages that went over to it — ``status`` → ``prepare <stage>`` → the model → the answer in
``<run>/incoming/<stage>.json`` → ``submit <stage>`` → ``status``.  ``prepare`` publishes the request from the journal and says what the model
reads and where the answer goes; ``submit`` reads the answer from that one place, checks and records it (``--dry-run`` writes nothing); both are
safe to repeat.  A stage joins by a row of ``STEPS``: its steps (``input_digests``, size, ``done``) and what the code does inside the call that
recorded an answer.  The code counts inside the step the agent called: it calls no model, starts no process, chooses no next step."""

from __future__ import annotations

import contextlib
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import batch_assembly, cases_import, confined_output, context_marker_parts, output_guard, pilot_state, scope_pause, step_size
from tools.project_inventory import read_inventory_receipt
from tools.revision_selection import REWORK_REVIEW_STAGE, REWORK_STAGE

SLOT_DIR = "incoming"


def slot(root: Path, stage: str) -> Path:
    """Where the agent puts the answer of ``stage``: built here from the id of a stage the table knows, never taken from the agent."""
    return Path(root) / SLOT_DIR / (stage.replace(":", "-") + ".json")


def _marker_accepted(root: Path, _stage: str) -> Mapping[str, Any]:
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
    rework = _rework_step(root, attempt_id, REWORK_STAGE in done)
    return [*steps, *([rework] if rework else [])]


def _rework_step(root: Path, attempt_id: str, answered: bool) -> dict[str, Any] | None:
    """The rework of the rejected cases: a step while the first review waits for it (its receipt is in the journal) and the set is not published."""
    from tools.pilot_state import read_effective_canonical_if_present, rework_round_if_present

    if rework_round_if_present(root, attempt_id) is None or read_effective_canonical_if_present(root, attempt_id) is not None:
        return None
    from tools.orchestrate_test_case_revision import abort_rework

    return {"stage": REWORK_STAGE, "input_digests": [], "done": answered, "invocation": "tc-generator-rework-1", "abort": lambda reason, dry_run: abort_rework(root, attempt_id, reason, dry_run)}


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


def _assemble(root: Path, stage: str, value: Mapping[str, Any], _dry_run: bool, _extra: Any) -> dict[str, Any] | None:
    """Before the write of the last answer of the generator: the candidate of all the batches — a refusal leaves the answer unwritten, so the one retry is
    still there.  The same ``assemble_candidate`` the review reads from the journal (``journal_assembly``)."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    plan, header = (pilot_state.generator_input(root.parents[1], root, state, attempt_id, kind) for kind in ("plan", "header"))
    if stage != _batch_stage(max(plan["batches"], key=pilot_state.batch_ordinal)):
        return None
    earlier = pilot_state.accepted_earlier_fragments(root.parents[1], root, state, attempt_id, plan, stage.split(":", 1)[1])
    return _candidate(root, *batch_assembly.assemble_candidate(dict(header), dict(plan), [*earlier, dict(value)]))


def _generator_accepted(root: Path, _stage: str) -> Mapping[str, Any]:
    """Every batch answered: the candidate the journal gives (a repeated ``submit`` of the last batch says it again)."""
    from tools.orchestrate_test_case_revision import journal_assembly

    if not all(step["done"] for step in _generator_steps(root) if step["stage"] != REWORK_STAGE):
        return {}
    return _candidate(root, *journal_assembly(root, str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]))[:2])



def _reviewer_steps(root: Path) -> list[dict[str, Any]]:
    """The review of the candidate once every batch of the generator is answered.  The row is a family: a later round is one more step with its own ``open``."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    batches = [step for step in _generator_steps(root) if step["stage"] != REWORK_STAGE]
    if not batches or not all(step["done"] for step in batches):
        return []
    from tools.orchestrate_test_case_revision import REVIEW_STAGE, abort_review, abort_rework_review, open_review, open_rework_review, rework_label

    steps = [{"stage": REVIEW_STAGE, "input_digests": [], "done": bool(_events(state, attempt_id, REVIEW_STAGE, "MODEL_RESPONSE_RECEIVED")), "extra": "evidence",
              "open": lambda model_id, invocation_id, fresh: open_review(root, attempt_id, model_id=model_id, invocation_id=invocation_id, fresh_context=fresh),
              "abort": lambda reason, dry_run: abort_review(root, attempt_id, reason, dry_run)}]
    from tools.pilot_state import read_effective_canonical_if_present, rework_round_if_present

    answered = _events(state, attempt_id, REWORK_STAGE, "MODEL_RESPONSE_RECEIVED") and rework_round_if_present(root, attempt_id) is not None
    reviewed = _events(state, attempt_id, REWORK_REVIEW_STAGE, "MODEL_RESPONSE_RECEIVED")
    if answered and (reviewed or read_effective_canonical_if_present(root, attempt_id) is None):  # the rework is answered and the set is not published (or the answer of the second round stands): the second round, on the reworked cases only
        steps.append({"stage": REWORK_REVIEW_STAGE, "input_digests": [], "done": bool(_events(state, attempt_id, REWORK_REVIEW_STAGE, "MODEL_RESPONSE_RECEIVED")),
                      "invocation": rework_label(root, attempt_id, REWORK_STAGE),  # no label from the host: the generator's of the rework (the same session)
                      "open": lambda model_id, invocation_id, fresh: open_rework_review(root, attempt_id, model_id=model_id, invocation_id=invocation_id, fresh_context=fresh),
                      "abort": lambda reason, dry_run: abort_rework_review(root, attempt_id, reason, dry_run)})
    return steps


def _reviewer_accepted(root: Path, stage: str) -> Mapping[str, Any]:
    """The answer is recorded: the selection by the journal — what a refusal left is finished by the repeat."""
    from tools.orchestrate_test_case_revision import select_review, select_rework_review

    if stage == REWORK_REVIEW_STAGE:
        return select_rework_review(root, str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]))
    return select_review(root, str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]))


def _reviewer_check(root: Path, stage: str, value: Mapping[str, Any], dry_run: bool, evidence: Any) -> dict[str, Any] | None:
    """After every check of the answer, before its write: the evidence it names (a refusal leaves the answer unwritten; the same files again are no duplicate);
    a dry run says what the selection would publish."""
    from tools.orchestrate_test_case_revision import provide_reviewer_evidence, select_review, select_rework_review

    attempt_id = str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"])
    if stage == REWORK_REVIEW_STAGE:  # no evidence in the second round; a dry run says what the set would be
        return select_rework_review(root, attempt_id, review=value, dry_run=True) if dry_run else None
    if evidence is not None:
        if not isinstance(evidence, list) or not all(isinstance(path, str) for path in evidence):
            raise ValueError("REVIEW_EVIDENCE_INVALID: evidence — список путей внутри проекта; fix: evidence: [\"src/файл.py\", …] или без ключа")
        provide_reviewer_evidence(root, attempt_id, evidence, dry_run=dry_run, again_ok=True)
    return select_review(root, attempt_id, review=value, dry_run=True) if dry_run else None


# ---- V5: the automation — the generator and the reviewer of the revision the code names (r1; r2 only after a correction, before the materialization) ----

def _automation_steps(root: Path, family: str) -> list[dict[str, Any]]:
    """The steps of ``tc-to-autotest`` or ``autotest-reviewer``: the revision is the code's (``automation_next`` names the stage the chain waits for), never the agent's.
    A cases-only run and a run whose cases are not yet accepted have none; r2 is a step only where the chain waits for it and nothing was materialized."""
    from tools import finalize_attempt, review_view

    state = pilot_state.derive_state(root)
    attempt = state["attempts"][-1]
    attempt_id = str(attempt["attempt_id"])
    if attempt["policy_profile"] == "cases-only-v1" or "document" not in pilot_state.effective_canonical_or_reason(root, attempt_id):  # «not yet» is a list without steps; a canon the journal accepted and the disk lost is its refusal
        return []
    _outcome, waits = finalize_attempt.automation_next(root, attempt_id)
    steps: list[dict[str, Any]] = []
    for revision in (1, 2):
        generator, reviewer = f"tc-to-autotest:r{revision}", f"autotest-reviewer:r{revision}"
        taken = revision == 1 or bool(_events(state, attempt_id, generator, "MODEL_REQUESTED")) or (waits == generator and not _materialization_began(root, attempt_id))
        if not taken:
            break
        answered = bool(_events(state, attempt_id, generator, "MODEL_RESPONSE_RECEIVED"))
        steps += [{"stage": generator, "input_digests": [], "done": answered, "invocation": f"tc-to-autotest-r{revision}",
                   "before": lambda: review_view.ensure_autotest_view(root.parents[1], root, pilot_state.derive_state(root))},  # the view the generator reads, built before the size is measured
                  {"stage": reviewer, "input_digests": [], "done": bool(_events(state, attempt_id, reviewer, "MODEL_RESPONSE_RECEIVED")),
                   "open": lambda model_id, invocation_id, fresh, revision=revision: _open_automation_review(root, attempt_id, revision, model_id, invocation_id, fresh),
                   **({} if answered else {"waits": f"ждёт принятого ответа {generator}"})}]
    return [step for step in steps if step["stage"].startswith(family + ":")]


def _materialization_began(root: Path, attempt_id: str) -> bool:
    """Whether the journal read back the ownership of the materialization — the way ``open_automation_review_boundary`` knows r2 is forbidden."""
    try:
        pilot_state.read_attempt_receipt(root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK")
    except ValueError:
        return False
    return True


def _automation_check(root: Path, stage: str, value: Mapping[str, Any]) -> None:
    """After every check of the answer of the generator, before its write (the dry run too): what is counted without a build — the meaning
    (``validate_automation_artifact``) and the static compatibility with the runner of the module.  A refusal leaves the answer unwritten."""
    from tools import execution_gate

    attempt = pilot_state.derive_state(root)["attempts"][-1]
    document = pilot_state.read_effective_canonical(root, str(attempt["attempt_id"]))["document"]
    rows = execution_gate.load_mode_b("automation_validation").validate_automation_artifact(dict(value), dict(document))
    adapters = execution_gate.load_mode_b("execution_adapters")
    adapter = pilot_state.frozen_baseline(root, attempt)["adapter_id"]
    language = "python" if adapter == adapters.PYTEST else "java" if adapter in {adapters.MAVEN, adapters.SYSTEM_MAVEN, adapters.GRADLE} else None
    if not rows and language is not None:
        compatibility = execution_gate.load_mode_b("run_tests").validate_artifact_runner_compatibility(
            root.parents[1] / str(attempt["module"]), language, document, value, materialized=False)
        rows = list(compatibility.diagnostics)
    if rows:
        raise pilot_state.ModelStageArtifactInvalid(
            f"invalid model stage artifact: {stage}: " + "; ".join(f"{row['path'] or '/'} {row['code']}" for row in rows[:3]) + (f" and {len(rows) - 3} more" if len(rows) > 3 else "")
            + "; fix: исправь ответ в слоте по этим строкам и повтори submit (--dry-run проверяет, ничего не записывая); ничего не записано, "
            "единственный повтор стадии — submit --transport-attempts 2")


def boundary_of_revision(root: Path, attempt_id: str, revision: int) -> dict[str, Any]:
    """The boundary of the review of the automation r<revision> read back from the journal; ``{}`` where it is not there."""
    try:
        return dict(pilot_state.read_attempt_receipt(root, attempt_id, f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"])
    except (KeyError, TypeError, ValueError, OSError):
        return {}


def _open_automation_review(root: Path, attempt_id: str, revision: int, model_id: str | None, invocation_id: str | None, fresh_context: bool | None) -> dict[str, Any]:
    """``prepare autotest-reviewer:rN``: the boundary (read back, or made from the journal), ``REVIEW_REQUESTED`` and the request of the reviewer — what five commands did.
    The label of the boundary is decided before it is read, by the statement and the labels alone, so the same call again is the same call.  A session that is not
    fresh (SELF: the run admitted it) gets the label the host named when it is no one else's, else the code's, and its boundary says ``distinct_invocations: false``."""
    from tools.orchestrate_test_case_revision import ReviewRefused

    if fresh_context is None and not pilot_state.self_review_accepted(root):  # a run that admitted SELF needs no statement: the session is the same
        raise ReviewRefused("FRESH_CONTEXT_REQUIRED", "prepare без --fresh-context: прогон не принимал ревью в той же сессии; fix: --fresh-context yes — ревьюер отдельный субагент или "
                            "вызов без истории генерации; ревью в той же сессии — человек согласен: --fresh-context no --accept-self-review (или review.self: true в .skillsrc)")
    fresh = bool(fresh_context)
    pilot_state.require_fresh_context_statement(root, fresh)
    stage, generator_stage = f"autotest-reviewer:r{revision}", f"tc-to-autotest:r{revision}"
    asked = _events(pilot_state.derive_state(root), attempt_id, generator_stage, "MODEL_REQUESTED")
    generator = str(pilot_state.read_model_request(root, attempt_id, generator_stage, str(asked[-1]["artifact_digest"]))["invocation_id"])
    if fresh and invocation_id is None:
        raise ReviewRefused("REVIEW_INVOCATION_REQUIRED", "--fresh-context yes без --invocation-id: независимость заявляет хост, назвав сессию ревьюера; "
                            "fix: --invocation-id <метка сессии ревьюера> (не метка генератора) или --fresh-context no, если ревью в той же сессии")
    if fresh and invocation_id == generator:
        raise pilot_state.IndependenceUnverified("INDEPENDENCE_UNVERIFIED: метка ревьюера та же, что у генератора; fix: назови сессию ревьюера своей --invocation-id; "
                                                 "ревью в той же сессии — --fresh-context no (только в прогоне, принявшем SELF)")
    label = invocation_id if invocation_id not in (None, generator) else f"autotest-review-self-r{revision}"
    if revision == 2 and label == boundary_of_revision(root, attempt_id, 1).get("reviewer_invocation_id"):  # the chain refuses it at the acceptance: one session reviews one revision
        raise ReviewRefused("REVIEW_INVOCATION_REQUIRED", "метка ревьюера та же, что у r1, а приём ревью такую цепочку отклонит; fix: ревью r2 — новой сессией: другая --invocation-id")
    from tools import review_view

    review_view.ensure_autotest_view(root.parents[1], root, pilot_state.derive_state(root))  # the view the reviewer reads: made again if it was changed after the generator
    boundary = boundary_of_revision(root, attempt_id, revision)
    if not boundary:
        facts = pilot_state.automation_boundary_facts(root, attempt_id, revision, fresh_context=fresh, reviewer_invocation_id=label)
        boundary = dict(pilot_state.open_automation_review_boundary(root, attempt_id, facts)["record"])
    if boundary["reviewer_invocation_id"] != label or boundary["host_isolation"]["fresh_context"] != fresh:
        raise ReviewRefused("REVIEW_ALREADY_OPEN", f"ревью автотестов уже открыто с ревьюером {boundary['reviewer_invocation_id']} (fresh_context={boundary['host_isolation']['fresh_context']}); "
                            "fix: повтори prepare с теми же --invocation-id и --fresh-context или иди дальше: submit")
    pilot_state.request_review(root, attempt_id, stage, str(boundary["automation_digest"]))
    asked = _events(pilot_state.derive_state(root), attempt_id, stage, "MODEL_REQUESTED")
    if not asked:
        pilot_state.publish_model_request(root, attempt_id, stage, model_id=model_id, invocation_id=label, input_digests=[])
    elif pilot_state.read_model_request(root, attempt_id, stage, str(asked[-1]["artifact_digest"]))["invocation_id"] != label:
        raise ReviewRefused("REVIEW_ALREADY_OPEN", "запрос ревьюера автотестов уже записан с другой меткой; fix: повтори prepare с теми же --invocation-id и --fresh-context")
    answer = slot(root, stage)
    return {"generator_checks": ["смысл ответа генератора (validate_automation_artifact)", "статическая совместимость с раннером"],
            "for_human": (f"Ревью автотестов в этой же сессии (SELF; результат не называется независимым): прочти файлы из read (список — read.list_path), проверь автотесты "
                          f"по эффективному документу и запиши ответ в {answer}. Затем `submit` этой стадии.") if not fresh else
                         ("Ревью автотестов в свежей сессии (руками): открой новую сессию GigaCode или выполни /clear — в ней не должно быть истории генерации; "
                          f"вставь «проведи ревью автотестов test-skills {root.name}»: модель прочтёт файлы из read и запишет ответ в {answer}. Вернись в рабочую сессию — `submit` этой стадии.")}


STEPS: dict[str, tuple[Callable, Callable, Callable | None]] = {
    "context-marker": (lambda root: context_marker_parts.plan(root)["parts"], _marker_accepted, None),
    "tc-generator": (_generator_steps, _generator_accepted, _assemble),
    "tc-reviewer": (_reviewer_steps, _reviewer_accepted, _reviewer_check),
    "tc-to-autotest": (lambda root: _automation_steps(root, "tc-to-autotest"), lambda root, _stage: {}, lambda root, stage, value, _dry, _extra: _automation_check(root, stage, value)),
    "autotest-reviewer": (lambda root: _automation_steps(root, "autotest-reviewer"), lambda root, _stage: {}, None),
    "scope": (_scope_steps, lambda root, _stage: {}, None),
    "plan": (_plan_steps, lambda root, _stage: {}, None),
}


def _events(state: Mapping[str, Any], attempt_id: str, stage: str, kind: str) -> list[Mapping[str, Any]]:
    return [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage and event["event_type"] == kind]


def _locate(run_root: Path, stage: str) -> tuple[Path, Path, Mapping[str, Any], str, Mapping[str, Any]]:
    project, root = output_guard.existing_run(run_root)
    state = pilot_state.derive_state(root)
    if not state["attempts"]:
        raise ValueError("STAGE_NO_ATTEMPT: the run has no attempt yet; fix: scan создаёт попытку — повтори scan")
    row = STEPS.get(stage.split(":", 1)[0])
    if stage.split(":", 1)[0] in ("tc-to-autotest", "autotest-reviewer") and state["attempts"][-1]["policy_profile"] == "cases-only-v1":
        raise ValueError(f"MODEL_STAGE_NOT_IN_PROFILE: {stage} is a stage of the automation, and this run is cases-only-v1; fix: прогон только кейсов — "
                         "автотесты строит новый прогон local-pilot-v1 (scan без --profile cases-only-v1) в поставке с режимом Б")
    steps = row[0](root) if row else []
    step = next((item for item in steps if item["stage"] == stage), None)
    if step is None:
        raise ValueError(f"STAGE_UNKNOWN: {stage} — не стадия, которую ведут prepare и submit; fix: next.stage из `status` "
                         f"({', '.join(item['stage'] for item in steps) or 'эта стадия идёт своей командой или её в этом прогоне нет (scope: разметка ещё не принята) — next.verb'})")
    return project, root, state, str(state["attempts"][-1]["attempt_id"]), step


def _accepted(root: Path, stage: str) -> Mapping[str, Any]:
    if stage == REWORK_STAGE:
        return {"rework": {"answered": True}}
    return STEPS[stage.split(":", 1)[0]][1](root, stage)


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


def _refuse_terminal(state: Mapping[str, Any], attempt_id: str) -> None:
    """A closed attempt takes no request and no answer, whatever the stage."""
    if next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)["state"] == "TERMINAL":
        raise ValueError(f"ATTEMPT_TERMINAL: the attempt {attempt_id} is closed; nothing more is asked or recorded in it")


def _refuse_unready(stage: str, step: Mapping[str, Any]) -> None:
    if "waits" in step:
        raise ValueError(f"STAGE_WAITS: {stage}: {step['waits']}; fix: сначала prepare и submit этой стадии")


LOST_WORD_MARK = "codeword-lost-"  # + the stage as its slot is named (``:`` → ``-``): one file for each, created once and never changed


def _mark_lost_word(project: Path, root: Path, stage: str) -> None:
    """The word of the batch was shown once and is asked for again: a mark of the run for the report, no refusal (the repeat is a call that succeeded).
    The mark is a file of its own, made exclusively; a repeat finds it and writes nothing."""
    confined_output.create_confined_bytes_exclusive(project, root, root / (LOST_WORD_MARK + stage.replace(":", "-")), stage.encode("utf-8"))


def _cached(stage: str) -> contextlib.AbstractContextManager:
    """The automation stages read the journal and its receipts many times in one call: one closure cache for the call (the way ``open_review`` runs)."""
    from tools.project_inventory import inventory_cache

    if not stage.startswith(("tc-to-autotest:", "autotest-reviewer:")):
        return contextlib.nullcontext()
    stack = contextlib.ExitStack()
    stack.enter_context(pilot_state.closure_cache())
    stack.enter_context(inventory_cache())
    return stack


def prepare(run_root: Path, stage: str, **kwargs: Any) -> dict[str, Any]:
    """Publish the request of ``stage`` and say what the model reads and where the answer goes (see ``_prepare``)."""
    with _cached(stage):
        return _prepare(run_root, stage, **kwargs)


def submit(run_root: Path, stage: str, load: Callable[[str, Path], Any], **kwargs: Any) -> dict[str, Any]:
    """Take the answer of ``stage`` from its slot and record it (see ``_submit``)."""
    with _cached(stage):
        return _submit(run_root, stage, load, **kwargs)


def _prepare(run_root: Path, stage: str, *, model_id: str | None = None, invocation_id: str | None = None, reuse_from: str | None = None,
            fresh_context: bool | None = None, accept_self_review: bool = False) -> dict[str, Any]:
    """Publish the request of ``stage`` and say what the model reads and where the answer goes; a reuse answers it from the source run.
    ``invocation_id`` is the host's label of the session that does the work (the review compares it with the reviewer's: same label, no independence);
    without it the code's label; ``fresh_context`` is the host's statement for a step that opens a session (the review); ``accept_self_review`` is the
    human's consent to a review in the same session, which the host passes with ``fresh_context`` ``no`` (it is written once for the run, before the review opens)."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if accept_self_review and ("open" not in step or fresh_context is not False):  # the consent is the host's word for a review step with «the same session»: nothing is written otherwise
        raise ValueError("INVALID_ARGUMENT: --accept-self-review — согласие человека на ревью в той же сессии, только с --fresh-context no и только у стадии ревью; "
                         "fix: prepare --stage <стадия ревью> --fresh-context no --accept-self-review после явного «да» человека (SKILL orchestrate §1)")
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
    _refuse_terminal(state, attempt_id)
    _refuse_unready(stage, step)
    if reuse_from is None:
        _refuse_linked_inbox(root)
    if accept_self_review:
        pilot_state.grant_self_review(root, attempt_id)  # before the review it belongs to; a repeat writes nothing
    opened = step["open"](model_id, invocation_id, fresh_context) if "open" in step else {}  # the session is opened (its request is a part of that) or recognised again
    state = pilot_state.derive_state(root) if opened else state
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
    found = step_size.request_files(root, stage)  # the automation also reads the Java level and junit-params
    word = {key: request[key] for key in ("nonce", "nonce_note") if key in request}  # the control word is shown once, by the call that wrote the request
    if requested and stage.startswith("tc-generator:BATCH-"):
        word = {"nonce_note": pilot_state.codeword_lost_note(root, state, attempt_id, stage).strip()}
        _mark_lost_word(project, root, stage)
    told = {key: opened[key] for key in ("reviewer_view", "canaries", "for_human", "questions_for_analyst", "warnings", "generator_checks") if key in opened}
    return {"stage": stage, "request_digest": request["digest"], "answer_slot": str(slot(root, stage)), "read": step_size.compact_inputs(root, stage, found)["files_summary"],
            "budget": _budget(root, step, found), **word, **told}


def _submit(run_root: Path, stage: str, load: Callable[[str, Path], Any], *, dry_run: bool = False, transport_attempts: int = 1, abort: str | None = None) -> dict[str, Any]:
    """Take the answer of ``stage`` from its slot and record it; ``load(path, project)`` reads the file strictly (``api._load_answer``)."""
    project, root, state, attempt_id, step = _locate(run_root, stage)
    if abort is not None:  # a step that can end without an answer says how
        if "abort" not in step:
            raise ValueError(f"STAGE_NO_ABORT: {stage} не прерывается; fix: --abort только у ревью (REVIEW_CONTEXT_LIMIT или REVIEW_UNUSABLE) и у доработки (REWORK_UNUSABLE)")
        return {"stage": stage, **step["abort"](abort, dry_run)}
    if "direct" in step:  # a recorded answer stands without a file; where the step rechecks, a file there is checked again
        if step["done"] and not (step.get("recheck") and slot(root, stage).is_file()):
            return {"stage": stage, **step["direct"][0](root, step)}
        _refuse_terminal(state, attempt_id)
        return {"stage": stage, **step["direct"][1](root, load(str(slot(root, stage)), project).value, dry_run)}
    if _events(state, attempt_id, stage, "MODEL_RESPONSE_RECEIVED"):  # the recorded answer stands: the slot is not read again
        return {"stage": stage, "answered": True, **({} if dry_run else _accepted(root, stage))}
    _refuse_unready(stage, step)
    if not _events(state, attempt_id, stage, "MODEL_REQUESTED"):
        raise ValueError(f"STAGE_NOT_PREPARED: {stage} has no request yet; fix: prepare --stage {stage}, затем модель, затем submit")
    _refuse_terminal(state, attempt_id)  # a closed attempt takes no answer: refused before the slot is read, nothing written
    if stage == "tc-reviewer:canonical":  # a session that was closed takes none either
        from tools.orchestrate_test_case_revision import refuse_closed_review

        refuse_closed_review(root, attempt_id)
    answer = load(str(slot(root, stage)), project)
    check, told = STEPS[stage.split(":", 1)[0]][2], {}
    key = step.get("extra")  # a key of the slot that is not part of the answer (the evidence of the review): taken out before the answer is checked
    value, extra = ({k: v for k, v in answer.value.items() if k != key}, answer.value.get(key)) if key and isinstance(answer.value, Mapping) else (answer.value, None)
    published = pilot_state.publish_model_stage_artifact(root, attempt_id, stage, value, transport_attempts=transport_attempts, raw_bytes=answer.raw_bytes,
                                                         dry_run=dry_run, before_write=(lambda value: told.update(check(root, stage, value, dry_run, extra) or {})) if check else None)
    return {**{key: value for key, value in published.items() if key != "artifact"}, **(told or ({} if dry_run else _accepted(root, stage)))}


def stage_status(root: Path, family: str) -> dict[str, Any]:
    """What ``status`` says of the marker or the generator: the next verb and the id of the step, who waits, the slot and the size.  Only reads."""
    state = pilot_state.derive_state(root)
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    parts = STEPS[family][0](root) or (STEPS["plan"][0](root) if family == "tc-generator" else [])  # the generator before its plan: the step of the plan
    if not parts:
        return {}
    pending = [part for part in parts if not part["done"]]
    asked = next((part for part in pending if _events(state, attempt_id, part["stage"], "MODEL_REQUESTED")), None)
    step = asked or (pending[0] if pending else parts[-1])  # every part of the marker answered, the merge not: submit repeats it
    verb = "submit" if asked or not pending or ("direct" in step and slot(root, step["stage"]).is_file()) else "prepare"
    return {"next": {"verb": verb, "stage": step["stage"]}, "waiting_for": "model" if asked else "agent",
            **({"budget": _budget(root, step)} if "bytes" in step or asked else {}), **({} if "by_code" in step else {"answer_slot": str(slot(root, step["stage"]))})}
