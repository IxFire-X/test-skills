"""Process runner (wave 2, A2) with a fake CLI: wait, failures, evidence, isolation (W2-Р8, Р9, Р13, Р14)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _answer_and_submit, _project, _run_root, _start
from tools import model_runner
from tools import pipeline_driver as driver
from tools import pipeline_driver_runner as runner
from tools.pilot_state import derive_state, read_terminal_result

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_cli.py"


def _command(script: str, state: Path) -> str:
    return json.dumps([sys.executable, str(FAKE), "--script", script, "--state", str(state)])


def _start_runner(tmp_path: Path, script: str = "ok", *, profile: str = "cases-only-v1", **options) -> tuple[Path, dict]:
    project = _project(tmp_path, local=profile == "local-pilot-v1")
    task = _start(project, profile, review_mode="compact-v1", review_runner="process", review_runner_command=_command(script, tmp_path / "fake-state"), **options)
    return project, task


def _until_done(project: Path, task: dict, model: SavedModel, *, limit: float = 240.0, on_wait=None) -> tuple[dict, list[dict]]:
    """Drive with the saved model for generator tasks and poll ``next`` while the driver's processes run."""
    waits = []
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if task["action"] == "done":
            return task, waits
        if task["action"] == "wait":
            waits.append(task)
            if on_wait is not None:
                on_wait(task)
            time.sleep(0.5)
            task = driver.advance(project, _run_root(project, task))
            continue
        assert task["action"] == "llm", task
        task = _answer_and_submit(project, task, model)
    raise AssertionError("the run did not finish")


def _events(project: Path, done: dict) -> list[dict]:
    return derive_state(project / ".pilot-runs" / done["run_id"])["events"]


def test_next_returns_wait_at_once_and_collects_every_part_once(tmp_path: Path) -> None:
    project, task = _start_runner(tmp_path)
    model = SavedModel("cases-only-v1")
    while task["action"] == "llm":
        task = _answer_and_submit(project, task, model)
    started = time.monotonic()
    assert task["action"] == "wait" and task["running"], task  # the review parts run in the background
    assert time.monotonic() - started < 5
    assert driver._exit_code(task) == 3
    done, _waits = _until_done(project, task, model)
    result = done["result"]
    assert (result["completion"], result["review_independence"]) == ("COMPLETE", "ISOLATED"), result
    events = _events(project, done)
    stages = [event["stage_instance_id"] for event in events if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and str(event.get("stage_instance_id")).startswith("tc-reviewer:")]
    assert stages and len(stages) == len(set(stages))
    log = (project / ".pilot-runs" / (done["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8")
    assert '"event": "runner_launched"' in log and '"event": "runner_collected"' in log


def test_host_submit_of_a_runner_part_is_refused(tmp_path: Path) -> None:
    project, task = _start_runner(tmp_path, "timeout")
    model = SavedModel("cases-only-v1")
    while task["action"] == "llm":
        task = _answer_and_submit(project, task, model)
    [task_id] = task["running"][:1]
    output = _run_root(project, task).with_name(task["run_id"] + ".driver") / "outputs" / f"{task_id}.json"
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(driver.DriverError) as error:
        driver.submit(project, _run_root(project, task), task_id)
    assert error.value.code == "DRIVER_INPUT" and "process runner" in str(error.value)


@pytest.mark.parametrize("script,reason", [
    ("garbage,ok", "RUNNER_OUTPUT_NOT_JSON"),
    ("prose,ok", "RUNNER_ANSWER_NOT_JSON"),
    ("nonzero,ok", "RUNNER_EXIT_3"),
])
def test_a_failed_try_is_retried_with_a_new_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, reason: str) -> None:
    monkeypatch.setattr(model_runner, "RATE_LIMIT_PAUSE", 1)
    project, task = _start_runner(tmp_path, script)
    done, waits = _until_done(project, task, SavedModel("cases-only-v1"))
    assert done["result"]["completion"] == "COMPLETE"
    log = [json.loads(line) for line in (project / ".pilot-runs" / (done["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [row for row in log if row.get("event") == "runner_failed"]
    assert failures and all(row["reason"].startswith(reason) for row in failures) and all(row["retry_allowed"] for row in failures)
    tries = [event["stage_instance_id"] for event in _events(project, done) if event["event_type"] == "MODEL_REQUESTED" and "-try2" in str(event.get("stage_instance_id"))]
    assert tries  # the second try is a new invocation


@pytest.mark.parametrize("limits", ["sublimit,ratelimit,sublimit-text,sublimit,ok"])
def test_rate_and_subscription_limits_pause_without_spending_a_try(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limits: str) -> None:
    """Review 2.1 item 7: four limit answers in a row (more than the three tries of a part) only pause, longer each time."""
    monkeypatch.setattr(model_runner, "RATE_LIMIT_PAUSE", 1)
    monkeypatch.setattr(model_runner, "RATE_LIMIT_MAX_PAUSE", 2)
    project, task = _start_runner(tmp_path, limits)
    done, waits = _until_done(project, task, SavedModel("cases-only-v1"), limit=300)
    assert done["result"]["completion"] == "COMPLETE", done["result"]
    log = [json.loads(line) for line in (project / ".pilot-runs" / (done["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not [row for row in log if row.get("event") == "runner_failed"]
    limited = [row for row in log if row.get("event") == "runner_rate_limited"]
    first = [row["pause_seconds"] for row in limited if row["wait"] <= 4][:4]
    assert first == [1, 2, 2, 2] and any(wait.get("paused_until") for wait in waits)
    assert not [event for event in _events(project, done) if event["event_type"] == "MODEL_REQUESTED" and "-try2" in str(event.get("stage_instance_id"))]


def test_a_limit_that_outlasts_the_waits_spends_a_try(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_runner, "RATE_LIMIT_PAUSE", 1)
    monkeypatch.setattr(model_runner, "RATE_LIMIT_MAX_WAITS", 1)
    project, task = _start_runner(tmp_path, "ratelimit,ratelimit,ok")
    done, _waits = _until_done(project, task, SavedModel("cases-only-v1"), limit=300)
    assert done["result"]["completion"] == "COMPLETE"
    log = [json.loads(line) for line in (project / ".pilot-runs" / (done["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = [row for row in log if row.get("event") == "runner_failed"]
    assert failed and all(row["reason"].startswith("RUNNER_RATE_LIMITED") and row["retry_allowed"] for row in failed)


@pytest.mark.parametrize("message,limited", [
    ("API Error: 429 rate_limit_error", True), ("You've hit your limit · resets 5pm (Europe/Moscow)", True),
    ("Claude AI usage limit reached|1760000000", True), ("You have reached your weekly limit", True), ("Out of usage credits", True),
    ("5-hour limit reached ∙ resets 3am", True), ("Overloaded", True),
    ("prompt is too long: 230000 tokens > 200000 maximum", False), ("Invalid API key", False),
])
def test_limit_messages_are_recognized(message: str, limited: bool) -> None:
    assert model_runner.is_rate_limited(message) is limited


def test_three_failures_block_the_part_as_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_runner, "DEFAULT_TIMEOUT", 3)
    project, task = _start_runner(tmp_path, "timeout")
    done, _waits = _until_done(project, task, SavedModel("cases-only-v1"), limit=300)
    assert done["result"]["reason_code"] == "REVIEW_TRANSPORT_FAILED"
    events = _events(project, done)  # the journal chain still verifies
    requests = [event["stage_instance_id"] for event in events if event["event_type"] == "MODEL_REQUESTED" and str(event.get("stage_instance_id")).startswith("tc-reviewer:")]
    assert any(stage.endswith("-try3") for stage in requests) and not any(stage.endswith("-try4") for stage in requests)
    log = (project / ".pilot-runs" / (done["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8")
    assert "RUNNER_TIMEOUT" in log


def test_a_crash_between_answer_and_record_loses_and_duplicates_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, task = _start_runner(tmp_path)
    model = SavedModel("cases-only-v1")
    while task["action"] == "llm":
        task = _answer_and_submit(project, task, model)
    run_root = _run_root(project, task)
    deadline = time.monotonic() + 120
    while not all(model_runner.state(runner.runner_directory(run_root, task_id)) == "done" for task_id in task["running"]):
        assert time.monotonic() < deadline
        time.sleep(0.3)
    original = driver._submit_review
    calls = {"n": 0}

    def crash(*args, **kwargs):
        calls["n"] += 1
        raise KeyboardInterrupt("orchestrator killed after the process answered, before the journal write")

    monkeypatch.setattr(driver, "_submit_review", crash)
    with pytest.raises(KeyboardInterrupt):
        driver.advance(project, run_root)
    monkeypatch.setattr(driver, "_submit_review", original)
    done, _waits = _until_done(project, driver.advance(project, run_root), model)
    responses = [event["stage_instance_id"] for event in _events(project, done) if event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                 and str(event.get("stage_instance_id")).startswith("tc-reviewer:")]
    assert calls["n"] == 1 and responses and len(responses) == len(set(responses)) and not any("-try2" in stage for stage in responses)


def test_driver_process_evidence_and_the_require_flag(tmp_path: Path) -> None:
    project, task = _start_runner(tmp_path / "process", require_driver_isolation=True)
    done, _waits = _until_done(project, task, SavedModel("cases-only-v1"))
    terminal = read_terminal_result(project / ".pilot-runs" / done["run_id"], done["result"]["attempt_id"])
    assert (terminal["isolation_evidence"], terminal["evidence"]["driver_isolation_required"]) == ("DRIVER_PROCESS", True)
    assert done["result"]["isolation_evidence"] == "DRIVER_PROCESS"
    root = project / ".pilot-runs" / done["run_id"]
    receipts = sorted((root / "review-state" / done["result"]["attempt_id"]).glob("review-part-process-*.json"))
    assert receipts
    record = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert record["user_settings_loaded"] is True and record["model"] == "fake-model-1" and record["model_source"] == "cli_output"
    assert record["tokens"]["total"] > 0 and record["session_id"]
    # A host that only declares a fresh subagent is HOST_DECLARED: with the flag it is not accepted.
    host = _project(tmp_path / "host", local=False)
    task = _start(host, "cases-only-v1", review_mode="compact-v1", require_driver_isolation=True)
    from tests.review_scaling_helpers import clean_compact_answer, part_text

    for _ in range(40):
        if task["action"] == "done":
            break
        if task.get("review_mode") == "compact-v1":
            Path(task["output_path"]).write_text(json.dumps(clean_compact_answer(part_text(task)), ensure_ascii=False), encoding="utf-8")
            task = driver.submit(host, _run_root(host, task), task["task_id"])
        else:
            task = _answer_and_submit(host, task, SavedModel("cases-only-v1"))
    terminal = read_terminal_result(host / ".pilot-runs" / task["run_id"], task["result"]["attempt_id"])
    assert (terminal["isolation_evidence"], terminal["reason_code"]) == ("HOST_DECLARED", "REVIEW_ISOLATION_UNVERIFIED")


@pytest.mark.parametrize("script", ["nosession", "fixed-session"])
def test_unprovable_sessions_are_not_driver_process(tmp_path: Path, script: str) -> None:
    project, task = _start_runner(tmp_path, script, require_driver_isolation=True)
    done, _waits = _until_done(project, task, SavedModel("cases-only-v1"))
    terminal = read_terminal_result(project / ".pilot-runs" / done["run_id"], done["result"]["attempt_id"])
    parts = len(list((project / ".pilot-runs" / done["run_id"] / "review-state" / done["result"]["attempt_id"]).glob("review-part-boundary-*.json")))
    if script == "nosession" or parts > 1:
        assert terminal["isolation_evidence"] == "HOST_DECLARED" and terminal["reason_code"] == "REVIEW_ISOLATION_UNVERIFIED"
    else:  # one part with a fixed session is still a distinct session
        assert terminal["isolation_evidence"] == "DRIVER_PROCESS"


def test_presets_forbid_writes_and_project_files_cannot_set_a_command(tmp_path: Path) -> None:
    claude = model_runner.build_argv(model_runner.RunnerConfig(preset="claude"), model="claude-sonnet-5-5", schema={"type": "object"}, directory=tmp_path / "c")
    assert claude[claude.index("--tools") + 1] == "" and "--no-session-persistence" in claude and "--json-schema" in claude
    codex = model_runner.build_argv(model_runner.RunnerConfig(preset="codex"), model="gpt-x", schema={"type": "object"}, directory=tmp_path / "x")
    assert codex[codex.index("--sandbox") + 1] == "read-only" and "--skip-git-repo-check" in codex and "--output-schema" in codex
    with pytest.raises(model_runner.RunnerError):
        model_runner.configure({"review_runner": {"preset": "claude", "command": ["sh", "-c", "x"]}})
    with pytest.raises(model_runner.RunnerError):
        model_runner.configure({"review_runner": {"preset": "bash"}})
    assert model_runner.configure({"review_runner": {"preset": "codex", "models": {"tc-reviewer": "m"}}}).models == {"tc-reviewer": "m"}
    with pytest.raises(driver.DriverError):
        driver._runner_options({"review_runner_command": "[\"x\"]"})  # a template needs --review-runner process
    # A .skillsrc with a command in review_runner stops the run at scan (schema) before anything is launched.
    project = _project(tmp_path / "skillsrc", local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["review_runner"] = {"preset": "claude", "command": ["sh", "-c", "rm -rf /"]}
    (project / ".skillsrc").write_text(json.dumps(skillsrc), encoding="utf-8")
    payload = _start(project, "cases-only-v1", review_runner="process")
    assert payload["action"] == "done" and payload["result"]["status"] == "error" and payload["result"]["stage"] == "scan"


def test_the_default_run_is_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1", review_mode="compact-v1")
    config = driver._config(_run_root(project, task))
    assert "review_runner" not in config and not runner.enabled(config)
