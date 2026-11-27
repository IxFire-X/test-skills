"""The context marker in parts: the plan of the parts and the merge by code (``tools.stage_steps`` calls both; no command of its own).

``plan(root)`` lists the marker steps of the current attempt: with one context batch the single ``context-marker:baseline`` as before; with more,
``context-marker:requirements`` (documents only), then ``context-marker:batch-NNN`` parts — each the receipts in a row whose worst case fits the
model window (``_groups``) — and the merge.  A part binds the inventory, its receipts and the requirements answer — the documents are read once, by the requirements step.  Each step
comes with its ``input_digests``, its bytes (batch / requirements / role instructions) against the step budget of
``tools.step_size`` and the next step to take.  ``merge_parts`` publishes ``context-marker:baseline`` from the recorded answers:
a merge of the parts by code, recorded with the model id ``controller:context-marker-merge`` — not a model call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from tools import pilot_state
from tools.cases_import import cases_path, docs_run
from tools.run_store import project_of

MERGE_MODEL_ID = "controller:context-marker-merge"


def _attempt(root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    state = pilot_state.derive_state(root)
    if not state["attempts"]:
        raise ValueError("the run has no attempt yet")
    return state, state["attempts"][-1]


def _receipts(root: Path, state: Mapping[str, Any], attempt_id: str) -> list[Mapping[str, Any]]:
    return pilot_state.context_receipts(root, attempt_id, state)


def _responses(state: Mapping[str, Any], attempt_id: str) -> dict[str, str]:
    return {str(event["stage_instance_id"]): str(event["artifact_digest"]) for event in state["events"]
            if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
            and str(event.get("stage_instance_id") or "").startswith("context-marker:")}


def _groups(root: Path, attempt_id: str, receipts: list[Mapping[str, Any]], asked: Mapping[str, str], code: Callable[[str, Mapping[str, Any]], int],
            head: list[str], fixed: int, limit: int, budget: int) -> list[list[Mapping[str, Any]]]:
    """The receipts of each part, in publication order.  A part asked keeps the receipts its request binds; any other takes receipts
    in a row while its worst case — ``fixed`` (the requirements view and the role), the maps and every file of its receipts whole —
    stays within ``limit``, the share of the model window of the generator's batches (the model may open every file of its map), and what it
    is sure to read — ``fixed`` and the maps — within ``budget``, the step budget: a group never makes a part that one receipt would not.
    Recorded bytes only (the byte counts of the receipts, the maps of their files); a window of 0 keeps one receipt a part."""
    from tools.model_records import part_receipts

    by_digest = {str(receipt["digest"]): receipt for receipt in receipts}
    bound = {stage: part_receipts(pilot_state.read_model_request(root, attempt_id, stage, digest)["input_digests"], head)
             for stage, digest in asked.items() if stage.startswith("context-marker:batch-")}
    held = {digest for digests in bound.values() for digest in digests}  # a receipt of a part asked is no other part's
    groups: list[list[Mapping[str, Any]]] = []
    at = 0
    while at < len(receipts):
        stage = f"context-marker:batch-{len(groups) + 1:03d}"
        group = [by_digest[digest] for digest in bound[stage]] if stage in bound else [receipts[at]]
        while (stage not in bound and limit and at + len(group) < len(receipts) and str(receipts[at + len(group)]["digest"]) not in held
               and fixed + sum(code(stage, row) + int(row["byte_count"]) for row in [*group, receipts[at + len(group)]]) <= limit
               and fixed + sum(code(stage, row) for row in [*group, receipts[at + len(group)]]) <= budget):
            group.append(receipts[at + len(group)])
        groups.append(group)
        at += len(group)
    return groups


def plan(root: Path, budget: int | None = None) -> dict[str, Any]:
    from tools.step_size import (code_map_bytes, code_map_path, requirements_view_bytes, requirements_view_path, role_files, run_limits, step_budget,
                                 step_window)

    root = Path(root).resolve()
    project = project_of(root)
    state, attempt = _attempt(root)
    attempt_id = str(attempt["attempt_id"])
    baseline = pilot_state.frozen_baseline(root, attempt)
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    unfinished = pilot_state.context_incomplete(root, attempt_id, state)
    if unfinished is not None:
        raise ValueError(pilot_state.scan_incomplete_message(*unfinished))
    receipts = _receipts(root, state, attempt_id)
    responses = _responses(state, attempt_id)
    limits = run_limits(project, root, state, attempt_id)[0]  # the limits the run froze, as prepare measures a part (review 332f9f60 item 3)
    budget = step_budget(project, limits) if budget is None else budget
    role = sum(path.stat().st_size for path in role_files("context-marker"))
    documents = sum((project / row["path"]).stat().st_size for row in baseline["requirements"].get("docs", []))

    asked = {str(event.get("stage_instance_id")): str(event["artifact_digest"]) for event in state["events"]
             if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_REQUESTED"}
    maps: dict[str, int] = {}

    def batch(stage: str, receipt: Mapping[str, Any]) -> int:
        """The code a marker step reads of its receipt: the map (hybrid C); a step asked with no map on the disk read the files whole, and so
        does a step whose map is empty (no file of the receipt reads back: ``stage_inputs`` gives the files)."""
        if stage in asked and not code_map_path(root, receipt["digest"]).is_file():
            return int(receipt["byte_count"])
        digest = str(receipt["digest"])
        if digest not in maps:
            maps[digest] = len(code_map_bytes(project, root, attempt_id, digest))
        return maps[digest] or int(receipt["byte_count"])

    def step(stage: str, inputs: list[Any], sizes: dict[str, int], over_hint: str) -> dict[str, Any]:
        total = sum(sizes.values())
        return {"stage": stage, "input_digests": inputs, "bytes": {**sizes, "total": total}, "fits": total <= budget,
                "hint": None if total <= budget else over_hint, "done": stage in responses}

    imported = cases_path(root) is not None  # the requirements of an import are answered by code, always a part
    openspec = pilot_state.openspec_marker_by_code(root)  # so are those of OpenSpec files alone: the model never copies them
    if len(receipts) <= 1 and not imported and not openspec and not any(stage.startswith("context-marker:batch-") for stage in asked):  # parts asked: parts
        parts = [step("context-marker:baseline", [*head, *(receipt["digest"] for receipt in receipts)],
                      {"batch": sum(batch("context-marker:baseline", receipt) for receipt in receipts), "requirements": documents, "role": role},
                      "вход разметки больше бюджета шага: уменьшите limits.context_batch_bytes и повторите scan")]
        mode = "single"
    else:
        answer = responses.get("context-marker:requirements")
        parts = [step("context-marker:requirements", head, {"requirements": documents, "role": role},
                      "документы задачи с инструкциями роли больше бюджета шага: сузьте --docs")]
        if openspec or imported and not docs_run(root):  # code, not a model call: no prompt, no budget (an import with --docs: the model)
            parts[0].update(fits=True, hint=None, by_code=True)
        if answer:  # no part before the requirements answer: a part is measured with the view of that answer (status names the requirements step)
            from tools.batch_assembly import WINDOW_SHARE

            read = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer)
            view = len(requirements_view_bytes(read["artifact"], answer))
            old = not requirements_view_path(root, answer).is_file()  # a part asked before the view (none on the disk) read the answer
            limit = int(step_window(project, limits) * WINDOW_SHARE)
            for number, group in enumerate(_groups(root, attempt_id, receipts, asked, batch, head, view + role, limit, budget), start=1):
                stage = f"context-marker:batch-{number:03d}"
                requirements_bytes = read["byte_count"] if old and stage in asked else view
                parts.append(step(stage, [baseline["inventory_digest"], *(receipt["digest"] for receipt in group), answer],  # no documents digest in a part
                                  {"batch": sum(batch(stage, receipt) for receipt in group), "requirements": requirements_bytes, "role": role},
                                  "часть с требованиями больше бюджета шага: уменьшите limits.context_batch_bytes и повторите scan"
                                  + ("; сами требования с инструкциями роли больше бюджета — сузьте --docs" if requirements_bytes + role > budget else "")))
        else:  # before the answer: an estimate a receipt a row (the documents for the view), so status warns of a part over the budget early;
            for receipt in receipts:  # no input digests and a «waits»: prepare refuses it (STAGE_WAITS) — review 332f9f60 item 2
                stage = f"context-marker:batch-{int(receipt['batch_index']):03d}"
                part = step(stage, [], {"batch": batch(stage, receipt), "requirements": documents, "role": role},
                            "часть с требованиями больше бюджета шага: уменьшите limits.context_batch_bytes и повторите scan")
                del part["input_digests"]
                parts.append({**part, "waits": "ждёт ответа context-marker:requirements"})
        mode = "parts"
    pending = [part["stage"] for part in parts if not part["done"]]
    if "context-marker:baseline" in responses:
        following = "done"
    elif pending:
        following = pending[0]
    else:
        following = "merge"
    return {"mode": mode, "attempt_id": attempt_id, "budget_bytes": budget, "parts": parts, "next": following}


def merge_parts(root: Path) -> Mapping[str, Any]:
    """Publish the merged ``context-marker:baseline`` of a marker in parts; stop while any part answer is missing."""
    from tools.context_marker_merge import merge_marker_artifacts

    root = Path(root).resolve()
    state, attempt = _attempt(root)
    attempt_id = str(attempt["attempt_id"])
    receipts = _receipts(root, state, attempt_id)
    responses = _responses(state, attempt_id)
    stages = [part["stage"] for part in plan(root)["parts"]]  # the requirements step and the parts as the plan groups the receipts
    missing = [stage for stage in stages if stage not in responses]
    if missing:
        raise ValueError(f"CONTEXT_MARKER_PARTS_INCOMPLETE: нет ответов {missing}")
    answer = pilot_state.read_model_stage_artifact(root, attempt_id, stages[0], responses[stages[0]])["artifact"]
    parts = [pilot_state.read_model_stage_artifact(root, attempt_id, stage, responses[stage])["artifact"] for stage in stages[1:]]
    baseline = pilot_state.frozen_baseline(root, attempt)
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts),
              responses[stages[0]], *dict.fromkeys(responses[stage] for stage in stages[1:])]
    try:  # the merged marker is built and checked before its request is written — a refusal leaves no orphan request
        from tools.cases_import import run_marker_requirements

        derived = run_marker_requirements(root, attempt, answer)  # an import with documents
        merged = merge_marker_artifacts(answer, parts, derived)
        pilot_state.check_merged_marker(root, attempt_id, inputs, merged)
    except ValueError as error:
        raise ValueError(f"CONTEXT_MARKER_MERGE_INVALID: {str(error).split('; fix: ')[0]}; fix: слияние — код, его повтор даст то же: "
                         "исправить можно только ответы частей — начните разметку заново (scan --new-run); запрос слияния не записан") from None
    pilot_state.publish_code_step_request(root, attempt_id, "context-marker:baseline", model_id=MERGE_MODEL_ID,
                                          invocation_id=f"merge-{attempt_id[:12]}", input_digests=inputs)  # a merge cut here goes on
    published = dict(pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", merged))
    published["warning"] = None  # the merge is code, not a model answer — no STEP_OUTPUT_LARGE about it
    from tools.scope_pause import write_draft

    draft = write_draft(root, attempt_id)  # the gaps of the marker as questions, for the human's decision on the scope
    return {**published, **({"questions_draft": str(draft)} if draft else {})}
