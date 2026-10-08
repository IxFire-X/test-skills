"""``run --runner process`` (wave 2, W2.8): a whole run without an orchestrating session (fake CLI)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.live_step5 import DOCS, LIVE
from tools import pipeline_driver as driver
from tools.pilot_state import derive_state, read_terminal_result

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_cli.py"
GENERATED = "src/test/java/net/javaguides/springboot/controller/StudentControllerPipelineTest.java"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(LIVE / "project", project)
    (project / GENERATED).unlink()
    return project


def _run(project: Path, *extra: str, script: str = "ok") -> tuple[int, dict]:
    argv = ["run", "--project", str(project), "--runner", "process", "--docs", DOCS, "--subject", "StudentController", "--model-id", "fake-model-1",
            "--review-runner-command", json.dumps([sys.executable, str(FAKE), "--script", script, "--state", str(project.parent / "fake-state")]), *extra]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = driver.main(argv)
    return code, json.loads(buffer.getvalue())


def test_a_cases_only_run_needs_no_orchestrator(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "cases-only-v1")
    result = payload["result"]
    assert payload["action"] == "done" and (result["completion"], result["verification"], result["review_independence"]) == ("COMPLETE", "NOT_APPLICABLE", "ISOLATED")
    assert code == 1  # cases-only is always a draft (erratum 1)
    run_root = project / ".pilot-runs" / payload["run_id"]
    log = [json.loads(line) for line in (run_root.with_name(payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    collected = {row["task_id"].split(".", 1)[1] for row in log if row.get("event") == "runner_collected"}
    assert {"context-marker"} <= collected and any(label.startswith("tc-generator.") for label in collected)
    assert any(row.get("event") == "runner_collected" and ".review." in row["task_id"] for row in log)  # review parts too
    stages = [event.get("stage_instance_id") for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    assert len(stages) == len(set(stages))


def test_an_unanswered_question_stops_with_exit_3(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".skillsrc").unlink()  # discovery asks which test framework the module uses
    code, payload = _run(project, "--profile", "cases-only-v1")
    assert payload["action"] == "ask_user" and code == 3 and payload["stopped"] == "ASK_USER_UNANSWERED", payload
    assert ".ask.skillsrc." in payload["task_id"] and payload["options"]


def test_an_answer_that_never_fits_fails_as_transport_with_its_reason(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "cases-only-v1", script="prose")
    assert code == 2 and payload["code"] == "RUNNER_TASK_FAILED" and "RUNNER_ANSWER_NOT_JSON" in payload["message"]


def test_subscription_limits_pause_the_run_without_spending_its_tries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2.1 item 7: more limit answers than tries in a row — the run pauses and finishes."""
    from tools import model_runner

    monkeypatch.setattr(model_runner, "RATE_LIMIT_PAUSE", 1)
    monkeypatch.setattr(model_runner, "RATE_LIMIT_MAX_PAUSE", 1)
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "cases-only-v1", script="sublimit,sublimit-text,sublimit,sublimit,ok")
    assert payload["action"] == "done" and payload["result"]["completion"] == "COMPLETE", payload
    log = [json.loads(line) for line in (project / ".pilot-runs" / (payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "runner_rate_limited" for row in log) and not any(row.get("event") == "runner_failed" for row in log)


JAVA_HOME = os.environ.get("TEST_SKILLS_JAVA_HOME")


@pytest.mark.skipif(not JAVA_HOME, reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
def test_a_local_pilot_run_passes_without_an_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "local-pilot-v1", "--require-driver-isolation")
    result = payload["result"]
    assert (code, result["verification"], result["accepted"], result["isolation_evidence"]) == (0, "PASS", True, "DRIVER_PROCESS"), result
    terminal = read_terminal_result(project / ".pilot-runs" / payload["run_id"], result["attempt_id"])
    assert terminal["isolation_evidence"] == "DRIVER_PROCESS" and (project / GENERATED).is_file()


def test_an_interrupted_run_continues_with_its_run_id(tmp_path: Path) -> None:
    """Review 2.1 item 12: `run --run <id>` resumes; the processes already launched are collected, not restarted."""
    from tools.pipeline_driver import DriverError
    from tools.pipeline_driver_run import run

    project = _project(tmp_path)
    command = json.dumps([sys.executable, str(FAKE), "--script", "ok", "--state", str(project.parent / "fake-state")])
    options = {"profile": "cases-only-v1", "docs": [DOCS], "subject": "StudentController", "model_id": "fake-model-1", "review_runner_command": command}
    with pytest.raises(DriverError) as stopped:
        run(project, options, answers={}, limit_seconds=0.5)
    assert stopped.value.code == "RUNNER_TIMEOUT"
    [run_id] = [path.name for path in (project / ".pilot-runs").iterdir() if path.is_dir() and len(path.name) == 32]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = driver.main(["run", "--project", str(project), "--runner", "process", "--run", run_id])
    payload = json.loads(buffer.getvalue())
    assert payload["action"] == "done" and payload["run_id"] == run_id and payload["result"]["completion"] == "COMPLETE", payload
    assert code == 1  # cases-only is always a draft


@pytest.mark.skipif(not JAVA_HOME, reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven and PIT")
def test_a_post_terminal_task_that_never_fits_does_not_undo_the_terminal_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2.1 item 12: survivor triage rejected three times — the accepted attempt stays done (exit 0); the groups stay pending."""
    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))
    project = _project(tmp_path)
    skillsrc = project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("schema_version: 5.0.0", "schema_version: 5.1.0", 1)
                        + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")
    code, payload = _run(project, "--profile", "local-pilot-v1", "--mutation", script="triage-prose")
    assert payload["action"] == "done", payload
    result = payload["result"]
    assert (code, result["verification"], result["accepted"]) == (0, "PASS", True), result
    assert result["strength_triage"]["triaged"] == 0 and result["strength_triage"]["pending"] == result["strength_triage"]["groups"] > 0
    log = (project / ".pilot-runs" / (payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8")
    assert '"event": "triage_failed"' in log
