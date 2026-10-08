"""Review parts run by the driver itself (opt-in ``next … --review-runner process``, wave 2 A2).

Instead of returning a review task to the orchestrator, the driver opens the part with a
boundary that declares ``isolation_evidence: DRIVER_PROCESS`` and the runner that will
start it, launches a detached CLI process (``tools.model_runner``) and returns
``{"action": "wait"}`` at once.  The next ``next`` collects finished processes:

* an answer passes the same checks as ``submit``; its process evidence (command digest,
  CLI, model, times, exit code, session, stdout digest, tokens) is published as the part's
  process receipt, then the answer is submitted;
* a transport failure, a timeout, unusable output or a rejected answer closes the try
  (``fail_review_part``); the part is offered again with a new process, at most three tries,
  then it is blocked (``REVIEW_TRANSPORT_FAILED``); a rate limit pauses new launches.

A killed or restarted orchestrator loses nothing: an open part's process keeps running,
its result file is collected exactly once, and a journalled response is never submitted
again.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from tools import model_runner
from tools import pipeline_driver as driver
from tools.pipeline_driver import DriverError

POLL_SECONDS = 15
ROLE_OF = {"canonical": "tc-reviewer"}


def enabled(config: Mapping[str, Any]) -> bool:
    return (config.get("review_runner") or {}).get("mode") == "process"


def runner_config(project: Path, run_root: Path, config: dict[str, Any]) -> model_runner.RunnerConfig:
    """The run's runner, resolved once from the frozen ``.skillsrc`` and the launch flags."""
    section = config["review_runner"]
    if "resolved" not in section:
        from tools.skillsrc_manifest import parse_skillsrc_bytes

        path = Path(project) / ".skillsrc"
        skillsrc = parse_skillsrc_bytes(path.read_bytes()) if path.is_file() else {}
        try:
            resolved = model_runner.configure(skillsrc, preset=section.get("preset"), command=section.get("command"), cli=section.get("cli"))
        except model_runner.RunnerError as error:
            raise DriverError(error.code, str(error)) from error
        section["resolved"] = resolved.to_json()
        section["cli_version"] = model_runner.cli_version(resolved)
        config["review_runner"] = section
        driver._save_config(run_root, config)
    return model_runner.RunnerConfig.from_json(section["resolved"])


def role_model(config: Mapping[str, Any], role: str) -> str | None:
    section = config.get("review_runner") or {}
    resolved = section.get("resolved") or {}
    return (resolved.get("models") or {}).get(role) or config.get("model_id")


def _role(review_key: str) -> str:
    return ROLE_OF.get(review_key, "autotest-reviewer")


def runner_directory(run_root: Path, task_id: str) -> Path:
    return driver.work_dir(run_root) / "runner" / task_id


def _log(run_root: Path, entry: Mapping[str, Any]) -> None:
    driver._log(driver._log_path(run_root.parents[1], run_root.name), entry)


def _state_path(run_root: Path) -> Path:
    return driver.work_dir(run_root) / "runner" / "state.json"


def _paused_until(run_root: Path) -> float:
    path = _state_path(run_root)
    return float(json.loads(path.read_text(encoding="utf-8")).get("paused_until", 0)) if path.is_file() else 0.0


def _runner_state(run_root: Path) -> dict[str, Any]:
    path = _state_path(run_root)
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def _pause(run_root: Path, seconds: int) -> None:
    state = _runner_state(run_root)
    state["paused_until"] = max(float(state.get("paused_until", 0)), time.time() + seconds)
    driver._write_json(_state_path(run_root), state)


def rate_limit_wait(run_root: Path, task_id: str, directory: Path, reason: str | None) -> int | None:
    """A rate or usage limit: pause (doubling) and set the task up for a relaunch without spending a try.

    The finished process directory is kept aside (``<task>.rate-limited-<n>``) so the same task can be
    launched again.  Returns the pause in seconds, or None when the task already waited
    ``RATE_LIMIT_MAX_WAITS`` times — then the limit counts as a failed try.
    """
    state = _runner_state(run_root)
    waits = dict(state.get("rate_waits") or {})
    wait = int(waits.get(task_id, 0)) + 1
    if wait > model_runner.RATE_LIMIT_MAX_WAITS:
        return None
    seconds = model_runner.rate_limit_pause(wait)
    waits[task_id] = wait
    state["rate_waits"] = waits
    state["paused_until"] = max(float(state.get("paused_until", 0)), time.time() + seconds)
    driver._write_json(_state_path(run_root), state)
    directory.rename(directory.with_name(f"{directory.name}.rate-limited-{wait}"))
    _log(run_root, {"event": "runner_rate_limited", "task_id": task_id, "wait": wait, "pause_seconds": seconds, "reason": (reason or "")[:300]})
    return seconds


def host_evidence(config: Mapping[str, Any], review_key: str, runner: model_runner.RunnerConfig, schema: Mapping[str, Any]) -> dict[str, Any]:
    from tools.review_parts import review_digest

    model = role_model(config, _role(review_key))
    isolation: dict[str, Any] = {
        "fresh_context": True, "distinct_invocations": True,
        "role_policy": "canonical-reviewer-v2" if review_key == "canonical" else "autotest-static-reviewer-v2",
        "isolation_evidence": "DRIVER_PROCESS",
        "runner": {"preset": runner.preset, "command_digest": model_runner.command_digest(runner, model=model, schema=schema),
                   "cli": "custom" if runner.command is not None else model_runner.PRESETS[runner.preset]["cli"]},
    }
    isolation["evidence_digest"] = review_digest(isolation)
    section = config.get("review_runner") or {}
    return {"reviewer_invocation_id": f"process-{review_key}-{uuid.uuid4().hex[:16]}", "model_id": model, "host_isolation": isolation,
            "cli": isolation["runner"]["cli"], "cli_version": str(section.get("cli_version") or "unknown"), "settings": f"driver-process-runner:{runner.preset}"}


def launch_task(run_root: Path, task: Mapping[str, Any], runner: model_runner.RunnerConfig, model: str | None) -> None:
    """Start the detached process of one issued task (review part, triage, or any model task in ``run``)."""
    schema = json.loads(Path(task["schema_path"]).read_text(encoding="utf-8"))
    directory = runner_directory(run_root, str(task["task_id"]))
    stdin = model_runner.compose_input(Path(task["skill_path"]), str(task["instructions"]), schema, [Path(path) for path in task["inputs"]])
    argv = model_runner.build_argv(runner, model=model, schema=schema, directory=directory)
    model_runner.launch(directory, argv, stdin, timeout=runner.timeout_seconds, digest=model_runner.command_digest(runner, model=model, schema=schema))
    _log(run_root, {"event": "runner_launched", "task_id": task["task_id"], "stage": task.get("stage"), "preset": runner.preset, "model": model,
                    "input_bytes": len(stdin.encode("utf-8"))})


def _collect(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], task: Mapping[str, Any],
             runner: model_runner.RunnerConfig) -> None:
    """Submit a finished part's answer with its process receipt, or record the failed try."""
    from tools.pilot_state import fail_review_part, publish_review_process_receipt, read_attempt_receipt
    from tools.review_parts import review_digest

    attempt_id = str(attempt["attempt_id"])
    key, part_id, number = task["review_key"], task["part_id"], int(task.get("try", 1))
    model = role_model(config, _role(key))
    outcome = model_runner.read_outcome(runner_directory(run_root, task["task_id"]), preset=runner.preset, configured_model=model)
    failure, reason = outcome.failure, outcome.reason
    if failure is None:
        try:
            if task.get("review_mode") == "compact-v1":
                from tools.pilot_state import review_part_diagnostics

                rows = review_part_diagnostics(run_root, attempt_id, key, part_id, outcome.answer) if isinstance(outcome.answer, dict) else [{"code": "NOT_AN_OBJECT"}]
                if rows:
                    raise DriverError("TASK_OUTPUT_INVALID", "; ".join(row["code"] for row in rows[:6]))
            evidence = outcome.evidence
            if evidence.get("session_id"):
                boundary_kind = f"review-part-boundary-{key}-{part_id}" + ("" if number == 1 else f"-try{number}")
                boundary = read_attempt_receipt(run_root, attempt_id, boundary_kind, "ARTIFACT_READ_BACK")["record"]
                publish_review_process_receipt(run_root, attempt_id, key, part_id, number, {
                    "boundary_digest": boundary["digest"], "answer_digest": review_digest(outcome.answer), "command_digest": evidence["command_digest"],
                    "cli": evidence["cli"], "cli_version": str((config.get("review_runner") or {}).get("cli_version") or "unknown"),
                    "model": evidence["model"], "model_source": evidence["model_source"], "started_at": evidence["started_at"], "finished_at": evidence["finished_at"],
                    "exit_code": evidence["exit_code"], "session_id": str(evidence["session_id"]), "stdout_sha256": evidence["stdout_sha256"],
                    "stdout_bytes": evidence["stdout_bytes"], "tokens": evidence["tokens"], "user_settings_loaded": evidence["user_settings_loaded"]})
            driver._submit_review(run_root, attempt, {**task, "transport_attempts": number}, outcome.answer, failed=None, reason=None)
        except DriverError as error:
            if error.code != "TASK_OUTPUT_INVALID":
                raise
            failure, reason = "CONTENT", f"RUNNER_ANSWER_REJECTED: {error}"[:600]
    if failure is not None:
        if outcome.rate_limited:
            if rate_limit_wait(run_root, str(task["task_id"]), runner_directory(run_root, task["task_id"]), reason) is not None:
                return  # relaunched after the pause; no try spent (review 2.1 item 7)
            reason = f"RUNNER_RATE_LIMITED: still limited after {model_runner.RATE_LIMIT_MAX_WAITS} pauses; {reason or ''}"[:600]
        result = fail_review_part(run_root, attempt_id, key, part_id, failure, reason or failure)
        _log(run_root, {"event": "runner_failed", "task_id": task["task_id"], "failure_class": failure, "reason": reason, "rate_limited": outcome.rate_limited,
                        "retry_allowed": result["retry_allowed"]})
        return
    _log(run_root, {"event": "runner_collected", "task_id": task["task_id"], "session_id": outcome.evidence.get("session_id"),
                    "tokens": outcome.evidence["tokens"], "seconds": round(outcome.evidence["finished_at"] - outcome.evidence["started_at"], 1),
                    "model": outcome.evidence["model"]})


def review_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: dict[str, Any], review_key: str) -> dict[str, Any] | None:
    """Collect, launch and report: ``wait`` while parts run, None when every part has an outcome."""
    from tools.pilot_state import available_review_parts

    runner = runner_config(project, run_root, config)
    attempt_id = str(attempt["attempt_id"])
    paused = _paused_until(run_root) > time.time()
    running: list[str] = []
    for task in driver._open_review_tasks(run_root, attempt_id, review_key):
        directory = runner_directory(run_root, task["task_id"])
        status = model_runner.state(directory) if (directory / "spec.json").is_file() else "unlaunched"
        if status == "running":
            running.append(task["task_id"])
        elif status == "done":
            _collect(project, run_root, attempt, config, task, runner)
        elif status == "lost":
            from tools.pilot_state import fail_review_part

            fail_review_part(run_root, attempt_id, review_key, task["part_id"], "TRANSPORT", "RUNNER_PROCESS_LOST: the process ended without a result")
            _log(run_root, {"event": "runner_failed", "task_id": task["task_id"], "failure_class": "TRANSPORT", "reason": "RUNNER_PROCESS_LOST"})
        elif not paused:  # opened before a crash, never launched: start it now
            launch_task(run_root, task, runner, role_model(config, _role(review_key)))
            running.append(task["task_id"])
    open_ids = {task["part_id"] for task in driver._open_review_tasks(run_root, attempt_id, review_key)}
    available = [envelope for envelope in available_review_parts(run_root, attempt_id, review_key) if envelope["part_id"] not in open_ids]
    paused = _paused_until(run_root) > time.time()
    if available and not paused:
        compact = available[0].get("mode") == "compact-v1"
        schema = driver._review_schema("compact-v1" if compact else "pairs")
        for envelope in available if compact else available[:1]:
            if len(running) >= runner.max_parallel or (not compact and running):
                break
            task = driver._issue_review_task(run_root, attempt, config, review_key, envelope, host=host_evidence(config, review_key, runner, schema),
                                             extra_task={"runner": "process"})
            launch_task(run_root, task, runner, role_model(config, _role(review_key)))
            running.append(task["task_id"])
    # A part set aside by a rate or usage limit is open but not launched: it waits for the pause to end.
    waiting = [task["task_id"] for task in driver._open_review_tasks(run_root, attempt_id, review_key)
               if not (runner_directory(run_root, task["task_id"]) / "spec.json").is_file()]
    if running or available or waiting:
        until = _paused_until(run_root)
        return {"action": "wait", "run_id": run_root.name, "attempt_id": attempt_id, "review_key": review_key, "running": sorted(running),
                "poll_seconds": POLL_SECONDS if running else max(1, int(until - time.time()) + 1), "paused_until": until if until > time.time() else None,
                "instructions": "Части ревью выполняет сам драйвер отдельными процессами CLI. Подожди poll_seconds секунд и снова вызови next."}
    return None
