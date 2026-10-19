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


def _measured_with_survivors(run_root: Path, result: dict) -> None:
    """``strength_triage`` exists only when PIT measured survivors; otherwise say why (CI ubuntu/3.11 on b64a6f4 only showed a KeyError)."""
    from tools.pilot_state import read_mutation_receipt_if_present

    strength = result.get("test_strength") or {}
    receipt = read_mutation_receipt_if_present(run_root, result["attempt_id"]) or {}
    assert strength.get("status") == "MEASURED" and strength.get("survivor_groups", 0) > 0, (strength, receipt.get("reason_code"), receipt.get("message"))


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


def test_the_process_runner_keeps_its_files_outside_the_project(tmp_path: Path) -> None:
    """Live S2 (2026-10-14): the runner's stdin.txt (copies of project files) in .pilot-runs broke Petclinic's nohttp check,
    which reads the whole tree, so the tests never ran.  The runner directory lives outside the project now."""
    project = _project(tmp_path)
    home = tmp_path / "runner home"  # a path with a space, as Windows temp directories often have
    code, payload = _run(project, "--profile", "cases-only-v1", "--runner-dir", str(home))
    assert payload["action"] == "done", payload
    assert not [path for path in project.rglob("*") if path.name in {"stdin.txt", "stdout.txt", "last-message.txt"}]
    tasks = list((home / payload["run_id"] / "runner").iterdir())
    assert tasks and all((task / "stdin.txt").is_file() and (task / "result.json").is_file() for task in tasks)
    log = [json.loads(line) for line in (project / ".pilot-runs" / (payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    launched = [row for row in log if row.get("event") == "runner_launched"]
    assert launched and all(Path(row["directory"]).parent.parent == home / payload["run_id"] for row in launched)


def test_without_a_runner_dir_the_runner_works_under_the_temp_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp dir"))
    (tmp_path / "temp dir").mkdir()
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "cases-only-v1")
    assert payload["action"] == "done", payload
    assert (tmp_path / "temp dir" / "test-skills" / payload["run_id"] / "runner").is_dir()
    assert not list(project.rglob("stdin.txt"))


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
    home = tmp_path / "runner home"
    options = {"profile": "cases-only-v1", "docs": [DOCS], "subject": "StudentController", "model_id": "fake-model-1", "review_runner_command": command,
               "runner_dir": str(home)}
    with pytest.raises(DriverError) as stopped:
        run(project, options, answers={}, limit_seconds=0.5)
    assert stopped.value.code == "RUNNER_TIMEOUT"
    [run_id] = [path.name for path in (project / ".pilot-runs").iterdir() if path.is_dir() and len(path.name) == 32]
    launched_before = {path.name for path in (home / run_id / "runner").iterdir()}  # collected after the resume, not launched again
    assert launched_before
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = driver.main(["run", "--project", str(project), "--runner", "process", "--run", run_id])
    payload = json.loads(buffer.getvalue())
    assert payload["action"] == "done" and payload["run_id"] == run_id and payload["result"]["completion"] == "COMPLETE", payload
    assert code == 1  # cases-only is always a draft
    assert all((home / run_id / "runner" / name / "result.json").is_file() for name in launched_before) and not list(project.rglob("stdin.txt"))


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
    _measured_with_survivors(project / ".pilot-runs" / payload["run_id"], result)
    assert result["strength_triage"]["triaged"] == 0 and result["strength_triage"]["pending"] == result["strength_triage"]["groups"] > 0
    log = (project / ".pilot-runs" / (payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8")
    assert '"event": "triage_failed"' in log


@pytest.mark.skipif(not JAVA_HOME, reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
def test_an_offline_local_pilot_run_passes_from_the_local_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S2a: `.skillsrc` test.offline — every Maven command of the run has -o; the driver-log says the build ran offline."""
    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))
    project = _project(tmp_path)
    skillsrc = project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("    adapter_parameters: {}", "    adapter_parameters: {}\n    offline: true", 1), encoding="utf-8")
    code, payload = _run(project, "--profile", "local-pilot-v1")
    result = payload["result"]
    assert (code, result["verification"], result["accepted"]) == (0, "PASS", True), result
    run_root = project / ".pilot-runs" / payload["run_id"]
    starts = [event for event in derive_state(run_root)["events"] if event["event_type"] == "EXECUTION_STARTED"]
    assert starts
    log = [json.loads(line) for line in (run_root.with_name(payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["build_offline"] for row in log if row.get("event") == "execution_launch"] == [True]
    output = json.loads((run_root.with_name(payload["run_id"] + ".driver") / "artifacts" / f"exec-output-{result['attempt_id'][:8]}.json").read_text(encoding="utf-8"))
    executions = []

    def collect(node):
        if isinstance(node, dict):
            if isinstance(node.get("argv"), list) and node.get("adapter_id"):
                executions.append(node["argv"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(output)
    for path in run_root.rglob("*.json"):  # the durable receipts of the run: the execution report carries the argv that ran
        try:
            collect(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, UnicodeDecodeError):
            continue
    maven = [argv for argv in executions if "-ntp" in argv]
    assert maven and all("-o" in argv for argv in maven), executions


def test_an_explicit_runner_dir_inside_the_project_is_allowed_with_a_warning(tmp_path: Path) -> None:
    """S2b: the operator may keep everything inside the project (company policy); the linter warning goes to the driver-log."""
    project = _project(tmp_path)
    code, payload = _run(project, "--profile", "cases-only-v1", "--runner-dir", str(project / ".runner"))
    assert payload["action"] == "done", payload
    assert (project / ".runner" / payload["run_id"] / "runner").is_dir()
    log = [json.loads(line) for line in (project / ".pilot-runs" / (payload["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    [warning] = [row for row in log if row.get("event") == "warning" and row.get("code") == "RUNNER_DIR_INSIDE_PROJECT"]
    assert warning["directory"] == str((project / ".runner").resolve()) and "линтер" in warning["message"]


def _main(*argv: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = driver.main(list(argv))
    return code, json.loads(buffer.getvalue())


@pytest.mark.parametrize("commands,extra", [
    (("next",), ("--runner-dir", "elsewhere")),                                        # needs --review-runner process (run always has it)
    (("next", "run"), ("--review-runner", "process", "--review-runner-command", "not json")),
    (("next", "run"), ("--review-runner", "process", "--review-runner-command", "[]")),
    (("next", "run"), ("--review-runner", "process", "--runner-dir", ".pilot-runs")),  # resolved from cwd: made absolute below
])
def test_a_bad_launch_option_is_a_json_refusal_before_the_run_exists(tmp_path: Path, commands: tuple[str, ...], extra: tuple[str, ...]) -> None:
    """S2b: options are checked before scan creates .pilot-runs/<run_id>; the refusal is the driver's JSON error, never a traceback."""
    project = _project(tmp_path)
    extra = tuple(str(project / value) if value == ".pilot-runs" else str(tmp_path / value) if value == "elsewhere" else value for value in extra)
    before = sorted(path.name for path in (project / ".pilot-runs").iterdir()) if (project / ".pilot-runs").is_dir() else []
    for command in commands:
        argv = [command, "--project", str(project), "--docs", DOCS, "--profile", "cases-only-v1", *extra] + (["--runner", "process"] if command == "run" else [])
        code, payload = _main(*argv)
        assert (code, payload["action"], payload["code"]) == (2, "error", "DRIVER_INPUT"), (command, payload)
        after = sorted(path.name for path in (project / ".pilot-runs").iterdir()) if (project / ".pilot-runs").is_dir() else []
        assert after == before, (command, after)


@pytest.mark.skipif(not JAVA_HOME, reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
def test_human_mode_runs_no_build_and_no_mutations_and_continues_with_the_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S9 on step5 (Java, --mutation): `run` stops on human_exec, the person runs Maven, `run --run --junit` finishes; PIT never starts."""
    import subprocess

    from tools import mutation, run_tests
    from tools.pilot_state import read_mutation_receipt_if_present

    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))
    project = _project(tmp_path)
    skillsrc = project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8").replace("schema_version: 5.0.0", "schema_version: 5.1.0", 1)
                        + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the package started a build, test or PIT process in human mode")

    monkeypatch.setattr(run_tests, "run_subprocess", forbidden)
    monkeypatch.setattr(mutation, "_run", forbidden)
    code, stopped = _run(project, "--profile", "local-pilot-v1", "--mutation", "--execution-mode", "human")
    assert stopped["action"] == "human_exec" and stopped.get("stopped") == "HUMAN_EXECUTION" and code == 3, stopped
    run_id = stopped["run_id"]
    for command in stopped["commands"]:  # the person runs the printed commands in their own environment
        completed = subprocess.run(command["argv"], cwd=command["cwd"], capture_output=True, check=False)
        assert completed.returncode == 0, (command["kind"], completed.stdout[-2000:], completed.stderr[-2000:])
    report = Path(stopped["report_path"])
    reports = [str(report)] if report.is_file() else [str(path) for path in report.rglob("*.xml")]
    assert reports
    code, payload = _main("run", "--project", str(project), "--runner", "process", "--run", run_id, *[item for path in reports for item in ("--junit", path)])
    assert payload["action"] == "done", payload
    result = payload["result"]
    assert (result["verification"], result["accepted"]) == ("PASS", True), result
    receipt = read_mutation_receipt_if_present(project / ".pilot-runs" / run_id, result["attempt_id"])
    assert receipt is not None and (receipt.get("status"), receipt.get("reason_code")) == ("NOT_APPLICABLE", "HUMAN_EXECUTION_MODE"), receipt
