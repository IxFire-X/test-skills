"""``python -m tools.pipeline_driver run --runner process``: a run without an orchestrating session.

The driver gives every model task to the process runner (review parts, context-marker,
generators, automation, survivor triage), waits for the processes itself and submits the
answers.  ``ask_user`` tasks are answered from ``--answer label=value`` flags or an
``--answers`` JSON file; a question without an answer stops the run with exit code 3, as
a waiting run does.  A rejected answer is retried in a new process with the rejection
appended to its instructions (at most three tries per task); an answer that does not fit
the process output is a transport failure with its reason.  The artifacts are those of
an orchestrated run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from tools import model_runner
from tools import pipeline_driver as driver
from tools import pipeline_driver_runner as runner
from tools.pipeline_driver import DriverError

TRIES = 3
ROLE_OF_STAGE = {"context-marker": "context-marker", "tc-generator": "tc-generator", "tc-to-autotest": "tc-to-autotest",
                 "mutation-triage": "mutation-triage", "tc-reviewer": "tc-reviewer", "autotest-reviewer": "autotest-reviewer"}


def _answer_for(task: Mapping[str, Any], answers: Mapping[str, str]) -> str | None:
    label = str(task["task_id"]).split(".ask.", 1)[-1]
    for key in (label, str(task.get("skillsrc_question_id") or ""), str(task["task_id"])):
        if key and key in answers:
            return answers[key]
    return None


def _run_one(project: Path, run_root: Path, task: Mapping[str, Any], config: Mapping[str, Any], cfg: model_runner.RunnerConfig) -> dict[str, Any]:
    """One model task through fresh processes until its answer is accepted (or the tries run out)."""
    role = ROLE_OF_STAGE.get(str(task["stage"]).split(":", 1)[0], "tc-generator")
    model = runner.role_model(config, role)
    feedback = ""
    last: dict[str, Any] = {}
    reason = None
    number = 0
    while number < TRIES:
        number += 1
        attempt_task = {**task, "task_id": f"{task['task_id']}-run{number}", "instructions": str(task["instructions"]) + feedback}
        directory = runner.runner_directory(run_root, attempt_task["task_id"])
        if (directory / "spec.json").is_file() and model_runner.state(directory) in {"running", "done"}:
            pass  # launched before an interruption (`run --run`): collect that process, do not start another
        else:
            runner.launch_task(run_root, attempt_task, cfg, model)
        while model_runner.state(directory) == "running":
            time.sleep(1)
        if model_runner.state(directory) == "lost":
            reason = "RUNNER_PROCESS_LOST"
            feedback = "\n\nПредыдущий процесс завершился без результата; ответь заново."
            continue
        outcome = model_runner.read_outcome(directory, preset=cfg.preset, configured_model=model)
        if outcome.failure is not None:
            reason = outcome.reason
            if outcome.rate_limited:
                # A rate or usage limit is a pause, not a failed try (review 2.1 item 7); the waits are bounded.
                pause = runner.rate_limit_wait(run_root, attempt_task["task_id"], directory, outcome.reason)
                if pause is not None:
                    time.sleep(pause)
                    number -= 1
                    continue
                reason = f"RUNNER_RATE_LIMITED: still limited after {model_runner.RATE_LIMIT_MAX_WAITS} pauses; {outcome.reason}"
            driver._log(driver._log_path(run_root.parents[1], run_root.name), {"event": "runner_failed", "task_id": task["task_id"], "try": number,
                                                                                "failure_class": outcome.failure, "reason": reason})
            feedback = f"\n\nПредыдущая попытка не дала ответа ({outcome.reason}). Верни только JSON-объект по схеме."
            continue
        Path(task["output_path"]).write_text(json.dumps(outcome.answer, ensure_ascii=False), encoding="utf-8", newline="\n")
        last = driver.submit(project, run_root, str(task["task_id"]), transport_attempts=min(number, 3))
        if last.get("status") != "rejected":
            driver._log(driver._log_path(run_root.parents[1], run_root.name), {"event": "runner_collected", "task_id": task["task_id"], "try": number,
                                                                                "tokens": outcome.evidence["tokens"], "model": outcome.evidence["model"]})
            return last
        reason = f"TASK_OUTPUT_INVALID: {last.get('message')}"
        feedback = ("\n\nКонтроллер отклонил предыдущий ответ. Исправь его по этим ошибкам и верни полный ответ заново:\n"
                    + json.dumps(last.get("errors", [])[:20], ensure_ascii=False))
    raise DriverError("RUNNER_TASK_FAILED", f"{task['task_id']}: no accepted answer after {TRIES} processes; last: {reason}")


def run(project: Path, options: Mapping[str, Any], *, answers: Mapping[str, str], max_tasks: int = 4, limit_seconds: float = 6 * 3600,
        run_id: str | None = None) -> dict[str, Any]:
    """Drive one run to ``done`` (or to an unanswered question) with the process runner for every model task.

    ``run_id`` continues an interrupted run (review 2.1 item 12): the driver state says what is next,
    and processes launched before the interruption are collected rather than started again.
    """
    project = Path(project).resolve()
    if run_id:
        run_root = driver._run_root(project, str(run_id))
        payload = _locked(project, run_root, lambda: driver.advance(project, run_root, max_tasks=max_tasks))
    else:
        options = {**dict(options), "review_runner": "process"}
        payload = driver.start_run(project, options, max_tasks=max_tasks)
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        action = payload.get("action")
        if action == "done":
            return payload
        run_root = driver._run_root(project, str(payload["run_id"]))
        config = driver._config(run_root)
        if action == "wait":
            time.sleep(max(1, min(int(payload.get("poll_seconds") or 5), 15)))
            payload = _locked(project, run_root, lambda: driver.advance(project, run_root, max_tasks=max_tasks))
            continue
        if action == "ask_user":
            value = _answer_for(payload, answers)
            if value is None:
                return {**payload, "stopped": "ASK_USER_UNANSWERED"}
            payload = _locked(project, run_root, lambda: driver.submit(project, run_root, str(payload["task_id"]), answer=value))
            continue
        cfg = runner.runner_config(project, run_root, config)
        tasks = payload["tasks"] if action == "batch" else [payload]
        for task in tasks:
            try:
                payload = _locked(project, run_root, lambda task=task: _run_one(project, run_root, task, driver._config(run_root), cfg))
            except DriverError as error:
                if error.code != "RUNNER_TASK_FAILED" or not task.get("post_terminal"):
                    raise
                # A post-terminal task (survivor triage) never undoes the terminal result: it is closed as failed.
                payload = _locked(project, run_root, lambda task=task, error=error: driver.submit(project, run_root, str(task["task_id"]),
                                                                                                 failed="TRANSPORT", reason=str(error)))
        if action == "batch":
            payload = _locked(project, run_root, lambda: driver.advance(project, run_root, max_tasks=max_tasks))
    raise DriverError("RUNNER_TIMEOUT", "the run did not finish within the time limit")


def _locked(project: Path, run_root: Path, operation):
    from tools.pilot_state import run_lock

    with run_lock(run_root):
        return operation()
