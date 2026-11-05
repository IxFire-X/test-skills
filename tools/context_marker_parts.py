"""The context marker in parts (FaT-5): the plan of the parts and the merge by code.

``python -m tools.context_marker_parts plan --run-root <project>/.pilot-runs/<run_id>`` lists the marker steps of the
current attempt: with one context batch the single ``context-marker:baseline`` as before; with more,
``context-marker:requirements`` (documents only), one ``context-marker:batch-NNN`` per batch and the merge.  A part binds the
inventory, its receipt and the requirements answer — the documents are read once, by the requirements step (FaT-P3).  Each step
comes with its ``input_digests``, its bytes (batch / requirements / role instructions) against the step budget of
``tools.step_size`` and the next step to take.  ``merge`` publishes ``context-marker:baseline`` from the recorded answers:
a merge of the parts by code, recorded with the model id ``controller:context-marker-merge`` — not a model call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from tools import pilot_state
from tools.cases_import import cases_path, docs_run
from tools.json_cli import JsonArgumentParser, cli_error

MERGE_MODEL_ID = "controller:context-marker-merge"
MERGE_TEXT = "слияние частей кодом (не вызов модели): python -m tools.context_marker_parts merge --run-root <run>"


def _attempt(root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    state = pilot_state.derive_state(root)
    if not state["attempts"]:
        raise ValueError("the run has no attempt yet")
    return state, state["attempts"][-1]


def _receipts(root: Path, state: Mapping[str, Any], attempt_id: str) -> list[Mapping[str, Any]]:
    digests = [str(event["artifact_digest"]) for event in state["events"]
               if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"]
    receipts = [pilot_state.read_context_selection(root, attempt_id, digest) for digest in dict.fromkeys(digests)]
    return sorted(receipts, key=lambda receipt: int(receipt["batch_index"]))


def _responses(state: Mapping[str, Any], attempt_id: str) -> dict[str, str]:
    return {str(event["stage_instance_id"]): str(event["artifact_digest"]) for event in state["events"]
            if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
            and str(event.get("stage_instance_id") or "").startswith("context-marker:")}


def plan(root: Path, budget: int | None = None) -> dict[str, Any]:
    from tools.project_inventory import read_execution_baseline
    from tools.step_size import role_files, step_budget

    root = Path(root).resolve()
    project = root.parents[1]
    state, attempt = _attempt(root)
    attempt_id = str(attempt["attempt_id"])
    baseline = pilot_state.frozen_baseline(root, attempt)
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    unfinished = pilot_state.context_incomplete(root, attempt_id, state)
    if unfinished is not None:
        raise ValueError(pilot_state.scan_incomplete_message(*unfinished))
    receipts = _receipts(root, state, attempt_id)
    responses = _responses(state, attempt_id)
    budget = step_budget(project) if budget is None else budget
    role = sum(path.stat().st_size for path in role_files("context-marker"))
    documents = sum((project / row["path"]).stat().st_size for row in baseline["requirements"].get("docs", []))

    def step(stage: str, inputs: list[Any], sizes: dict[str, int], over_hint: str) -> dict[str, Any]:
        total = sum(sizes.values())
        return {"stage": stage, "input_digests": inputs, "bytes": {**sizes, "total": total}, "fits": total <= budget,
                "hint": None if total <= budget else over_hint, "done": stage in responses}

    imported = cases_path(root) is not None  # FaT-IMP: the requirements of an import are answered by code, always a part
    if len(receipts) <= 1 and not imported:
        parts = [step("context-marker:baseline", [*head, *(receipt["digest"] for receipt in receipts)],
                      {"batch": sum(int(receipt["byte_count"]) for receipt in receipts), "requirements": documents, "role": role},
                      "вход разметки больше бюджета шага: уменьшите limits.context_batch_bytes и повторите scan")]
        mode = "single"
    else:
        answer = responses.get("context-marker:requirements")
        requirements_bytes = (pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer)["byte_count"]
                              if answer else documents)
        parts = [step("context-marker:requirements", head, {"requirements": documents, "role": role},
                      "документы задачи с инструкциями роли больше бюджета шага: сузьте --docs")]
        if imported and not docs_run(root):  # code, not a model call: no prompt, no budget (FaT-IMP3: with --docs the model)
            parts[0].update(fits=True, hint=None, by_code=f"python -m tools.api cases-import --run-root {root} --attempt {attempt_id}")
        for receipt in receipts:
            part = step(f"context-marker:batch-{int(receipt['batch_index']):03d}", [baseline["inventory_digest"], receipt["digest"], answer],  # FaT-P3: no documents digest in a part
                        {"batch": int(receipt["byte_count"]), "requirements": requirements_bytes, "role": role},
                        "часть с требованиями больше бюджета шага: уменьшите limits.context_batch_bytes и повторите scan"
                        + ("; сами требования с инструкциями роли больше бюджета — сузьте --docs" if requirements_bytes + role > budget else ""))
            if answer is None:  # FaT-M: no digest list with a null in it before the requirements answer
                del part["input_digests"]
                part["waits"] = "ждёт ответа context-marker:requirements"
            parts.append(part)
        mode = "parts"
    pending = [part["stage"] for part in parts if not part["done"]]
    if "context-marker:baseline" in responses:
        following = "done"
    elif pending:
        following = pending[0]
    else:
        following = "merge"
    return {"mode": mode, "attempt_id": attempt_id, "budget_bytes": budget, "parts": parts, "next": following, "merge": MERGE_TEXT}


def merge_parts(root: Path) -> Mapping[str, Any]:
    """Publish the merged ``context-marker:baseline`` of a marker in parts; stop while any part answer is missing."""
    from tools.context_marker_merge import merge_marker_artifacts
    from tools.project_inventory import read_execution_baseline

    root = Path(root).resolve()
    state, attempt = _attempt(root)
    attempt_id = str(attempt["attempt_id"])
    receipts = _receipts(root, state, attempt_id)
    responses = _responses(state, attempt_id)
    stages = ["context-marker:requirements", *(f"context-marker:batch-{int(receipt['batch_index']):03d}" for receipt in receipts)]
    missing = [stage for stage in stages if stage not in responses]
    if missing:
        raise ValueError(f"CONTEXT_MARKER_PARTS_INCOMPLETE: нет ответов {missing}")
    answer = pilot_state.read_model_stage_artifact(root, attempt_id, stages[0], responses[stages[0]])["artifact"]
    parts = [pilot_state.read_model_stage_artifact(root, attempt_id, stage, responses[stage])["artifact"] for stage in stages[1:]]
    baseline = pilot_state.frozen_baseline(root, attempt)
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts),
              responses[stages[0]], *dict.fromkeys(responses[stage] for stage in stages[1:])]
    try:  # FaT-O3: the merged marker is built and checked before its request is written — a refusal leaves no orphan request
        from tools.cases_import import run_marker_requirements

        derived = run_marker_requirements(root, attempt, answer)  # FaT-IMP3: an import with documents
        merged = merge_marker_artifacts(answer, parts, derived)
        pilot_state.check_merged_marker(root, attempt_id, inputs, merged)
    except ValueError as error:
        raise ValueError(f"CONTEXT_MARKER_MERGE_INVALID: {str(error).split('; fix: ')[0]}; fix: слияние — код, его повтор даст то же: "
                         "исправить можно только ответы частей — начните разметку заново (scan --new-run); запрос слияния не записан") from None
    pilot_state.publish_code_step_request(root, attempt_id, "context-marker:baseline", model_id=MERGE_MODEL_ID,
                                          invocation_id=f"merge-{attempt_id[:12]}", input_digests=inputs)  # F2: a merge cut here goes on
    published = dict(pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", merged))
    published["warning"] = None  # FaT-O: the merge is code, not a model answer — no STEP_OUTPUT_LARGE about it
    from tools.scope_pause import write_draft

    draft = write_draft(root, attempt_id)  # FaT-P3: the gaps of the marker as questions, for the human's decision on the scope
    return {**published, **({"questions_draft": str(draft)} if draft else {})}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="The context marker in parts: plan and merge.")
    parser.add_argument("command", choices=("plan", "merge"))
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            print(json.dumps(plan(args.run_root), ensure_ascii=False, sort_keys=True))
        else:
            published = merge_parts(args.run_root)
            print(json.dumps({"status": "ok", "stage": "context-marker:baseline", "kind": "слияние частей кодом",
                              "content_digest": published["content_digest"],
                              **({"questions_draft": published["questions_draft"],
                                  "next": "пауза: покажи человеку пробелы разметки (status → next_step SCOPE_PENDING)"}
                                 if published.get("questions_draft") else {})}, ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps(cli_error(error, fix="--run-root — каталог прогона .pilot-runs/<run_id> (его создаёт scan); команда запускается из каталога пакета; "
                                   "что делать дальше, называет `python -m tools.run_pipeline status --project <проект> --run <run_id>` (next_step)"),
                         ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
