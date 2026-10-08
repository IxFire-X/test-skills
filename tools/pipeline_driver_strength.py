"""Post-terminal survivor triage of the driver (opt-in mutation stage, wave 2).

A terminal local attempt with a ``MEASURED`` mutation receipt gets ``mutation-triage``
tasks before ``done``.  The attempt journal is closed (no model event may follow
``EXECUTION_STARTED``) and triage changes nothing in the attempt, so the tasks live
beside it: plan, inputs and accepted answers in ``.pilot-runs/<run>.driver/strength/<attempt>``,
issues and answers in ``driver-log.jsonl``.  With ``next --max-tasks K`` several tasks
come back as one batch, like compact review parts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools import pipeline_driver as driver
from tools.pipeline_driver import DriverError

STAGE = "mutation-triage"


def strength_dir(run_root: Path, attempt: Mapping[str, Any]) -> Path:
    return driver.work_dir(run_root) / "strength" / str(attempt["attempt_id"])[:8]


def proposals_path(run_root: Path, attempt_id: str) -> Path:
    """The one place of the attempt's ``TEST_GAP`` proposals: written here, read by ``suite-update-v1``."""
    return strength_dir(run_root, {"attempt_id": attempt_id}) / "proposals.json"


def _receipt(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    from tools.pilot_state import read_mutation_receipt_if_present

    return read_mutation_receipt_if_present(run_root, str(attempt["attempt_id"]))


def _product_lines(project: Path, run_root: Path, attempt: Mapping[str, Any]):
    """Masked lines of a product file the attempt's context selected (None outside the context)."""
    from tools.project_inventory import select_context_batches

    _baseline, inventory = driver._baseline_and_inventory(run_root, attempt)
    files = {item["project_path"]: item for receipt in driver._context_receipts(run_root, str(attempt["attempt_id"])) for item in receipt["files"]}
    module = str(attempt["module"])

    def lines(path: str):
        item = files.get(path if module == "." else f"{module}/{path}")
        if item is None:
            return None
        for batch in select_context_batches(inventory, project, [item["opaque_id"]], byte_budget=max(int(item.get("size") or 1), 1), include_closed_manifests=False):
            for row in batch["files"]:
                data = row.get("bytes")
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data).decode("utf-8", errors="replace").split("\n")
        return None

    return lines


def _plan(project: Path, run_root: Path, attempt: Mapping[str, Any], receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The deterministic triage tasks of the attempt, built once and kept with their input files."""
    from tools.mutation_triage import build_tasks
    from tools.pilot_state import read_effective_canonical, read_execution_inputs

    directory = strength_dir(run_root, attempt)
    path = directory / "triage-plan.json"
    if path.is_file():
        return list(driver._read_json(path)["tasks"])
    attempt_id = str(attempt["attempt_id"])
    document = read_effective_canonical(run_root, attempt_id)["document"]
    automation = read_execution_inputs(run_root, attempt_id)["automation_artifact"]
    marker = driver._marker_artifact(run_root, attempt_id)
    marker_requirements = marker["artifact"]["artifacts"]["analytics_documentation"]["requirements"] if marker else []
    tasks = build_tasks(receipt, document, automation, marker_requirements, _product_lines(project, run_root, attempt))
    rows = []
    for task in tasks:
        input_path = driver._write_text(directory / "inputs" / f"{task['label']}.input.md", task["text"])
        rows.append({"label": task["label"], "group_ids": task["group_ids"], "input_path": str(input_path),
                     "input_digest": "sha256:" + hashlib.sha256(task["text"].encode("utf-8")).hexdigest()})
    driver._write_json(path, {"schema_version": "1.0.0", "mutation_receipt_digest": receipt["digest"], "tasks": rows})
    return rows


def _answer_path(run_root: Path, attempt: Mapping[str, Any], label: str) -> Path:
    return strength_dir(run_root, attempt) / "answers" / f"{label}.json"


def answers(run_root: Path, attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    directory = strength_dir(run_root, attempt) / "answers"
    return [dict(driver._read_json(path)) for path in sorted(directory.glob("triage-*.json"))] if directory.is_dir() else []


def triage_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Open triage tasks (or a batch of them), or None when nothing is left to triage."""
    from tools.mutation_triage import INSTRUCTIONS, SCHEMA, pending_groups

    receipt = _receipt(run_root, attempt)
    if receipt is None or not pending_groups(receipt):
        return None
    plan = _plan(project, run_root, attempt, receipt)
    open_rows = [row for row in plan if not _answer_path(run_root, attempt, row["label"]).is_file()]
    if not open_rows:
        return None
    attempt_id = str(attempt["attempt_id"])
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    limit = max(1, int(config.get("_max_tasks") or 1))
    tasks = [driver._llm_task(run_root, attempt_id, f"{STAGE}.{row['label']}", stage=f"{STAGE}:{row['label']}", skill=STAGE,
                              inputs=[Path(row["input_path"])], schema=schema, instructions=INSTRUCTIONS,
                              extra={"triage_label": row["label"], "group_ids": list(row["group_ids"]), "post_terminal": True})
             for row in open_rows[:limit]]
    if len(tasks) == 1:
        return tasks[0]
    batch = {"action": "batch", "run_id": run_root.name, "attempt_id": attempt_id, "review_key": STAGE, "tasks": tasks,
             "instructions": "Независимые задачи разбора выживших мутантов: каждую выполни отдельным свежим вызовом, затем submit по одной."}
    driver._log(driver._log_path(run_root.parents[1], run_root.name), {"event": "batch_issued", "review_key": STAGE,
                                                                       "task_ids": [task["task_id"] for task in tasks], "max_tasks": limit})
    return batch


def submit_triage(project: Path, run_root: Path, attempt: Mapping[str, Any], task: Mapping[str, Any], value: Any) -> None:
    """Check one triage answer against its task and keep it; a rejected answer raises TASK_OUTPUT_INVALID."""
    from tools.mutation_triage import validate
    from tools.pilot_state import read_effective_canonical

    path = _answer_path(run_root, attempt, str(task["triage_label"]))
    if path.is_file():
        return  # already answered: submit is idempotent
    receipt = _receipt(run_root, attempt)
    row = next((item for item in _plan(project, run_root, attempt, receipt) if item["label"] == task["triage_label"]), None) if receipt else None
    if row is None:
        raise DriverError("DRIVER_STATE", "the triage task is not in the attempt's triage plan")
    text = Path(row["input_path"]).read_text(encoding="utf-8")
    document = read_effective_canonical(run_root, str(attempt["attempt_id"]))["document"]
    rows = validate({"group_ids": row["group_ids"], "text": text}, value, receipt, document)
    if rows:
        raise DriverError("TASK_OUTPUT_INVALID", "triage answer was not accepted: " + "; ".join(f"{item['code']} {item.get('path', '')}" for item in rows[:8]), rows)
    driver._write_json(path, {**value, "label": row["label"], "input_digest": row["input_digest"], "model_id": driver._config(run_root).get("model_id")})


def strength_summary(run_root: Path, attempt: Mapping[str, Any], summary: dict[str, Any]) -> None:
    """``test_strength`` and, when triage ran, ``strength_triage`` with the report beside the projections."""
    from tools.mutation import summary as strength
    from tools.mutation_triage import counts, decision_rows, dumps, pending_groups, proposals
    from tools.strength_report import write_strength_report

    receipt = _receipt(run_root, attempt)
    if receipt is None:
        return
    rows = decision_rows(answers(run_root, attempt))
    summary["schema_version"] = "1.1.0"
    summary["test_strength"] = strength(receipt)
    summary["paths"].update(write_strength_report(driver._bundle_dir(run_root, attempt), receipt, rows))
    pending = pending_groups(receipt)
    if pending or receipt.get("survivor_groups"):
        if rows:
            driver._write_text(proposals_path(run_root, str(attempt["attempt_id"])), dumps({"mutation_receipt_digest": receipt["digest"], "proposals": proposals(rows)}))
        summary["strength_triage"] = {"groups": len(receipt.get("survivor_groups", [])), "triaged": len(rows), "pending": len(pending) - len(rows),
                                      "over_limit": len(receipt.get("survivor_groups", [])) - len(pending), "decisions": counts(rows)}
