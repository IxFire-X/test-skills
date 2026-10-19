"""S9 of the security fixes: execution.mode human — the package runs no build or test itself.

After the generated tests are written (and approved, S3) the driver answers ``human_exec`` with the exact closed
commands (argv lists, also as bash and PowerShell lines), their working directory and the report directory; a person
runs them; ``next --run <id> --junit <report>`` hands the reports back and the same acceptance rules decide.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver
from tools import run_tests


def _until_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options) -> tuple[Path, Path, dict, SavedModel]:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("the driver started a build or test process in human mode")

    monkeypatch.setattr(run_tests, "run_subprocess", forbidden)
    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1", execution_mode="human", **options), model,
                  until=lambda task: task["action"] == "human_exec")
    monkeypatch.undo()
    return project, _run_root(project, task), task, model


def _run_commands(task: dict, *, extra: list[str] = ()) -> list[int]:
    codes = []
    for command in task["commands"]:
        argv = list(command["argv"]) + (list(extra) if command["kind"] == "tests" else [])
        codes.append(subprocess.run(argv, cwd=command["cwd"], capture_output=True, check=False).returncode)
    return codes


def _reports(task: dict) -> list[str]:
    report = Path(task["report_path"])
    return [str(report)] if report.is_file() else [str(path) for path in report.rglob("*.xml")]


def _report_target(task: dict, name: str) -> Path:
    """Where a person's run writes a report: the closed report file itself, or a file of the report directory."""
    report = Path(task["report_path"])
    target = report if Path(task["report_dir"]) != report else report / name
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _human_next(project: Path, run_root: Path, task: dict, *extra: str, capsys) -> tuple[int, dict]:
    code = driver.main(["next", "--project", str(project), "--run", run_root.name, *extra])
    return code, json.loads(capsys.readouterr().out)


def test_the_driver_prints_the_commands_and_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    project, run_root, task, _model = _until_human(tmp_path, monkeypatch)
    assert driver._exit_code(task) == 3
    assert [command["kind"] for command in task["commands"]] == ["gate", "tests"]
    for command in task["commands"]:
        assert command["argv"] and Path(command["cwd"]).is_dir()
        assert command["bash"] and command["powershell"] and command["argv"][0].split("\\")[-1].split("/")[-1] in command["bash"]
    assert task["selectors"] and all(selector in task["commands"][1]["argv"] for selector in task["selectors"])
    assert task["report_dir"] and "--junit" in task["continue"]
    # Asking again gives the same commands; nothing started in the journal before the reports come back.
    again = driver.advance(project, run_root)
    assert again["action"] == "human_exec" and again["commands"] == task["commands"]


def test_the_reports_of_the_person_decide_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tools.pilot_state import read_attempt_receipt

    project, run_root, task, model = _until_human(tmp_path, monkeypatch)
    assert _run_commands(task) == [0, 0]
    reports = _reports(task)
    assert reports
    code, payload = _human_next(project, run_root, task, *[item for path in reports for item in ("--junit", path)], "--exit-code", "0", capsys=capsys)
    done = _drive(project, payload, model) if payload["action"] != "done" else payload
    assert done["action"] == "done" and done["result"]["verification"] == "PASS", done
    receipt = read_attempt_receipt(run_root, task["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert receipt["execution"]["executor"] == "human"


def test_a_report_outside_the_report_directory_is_refused_and_an_empty_one_is_no_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    project, run_root, task, model = _until_human(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere.xml"
    outside.write_text("<testsuite/>", encoding="utf-8")
    code, payload = _human_next(project, run_root, task, "--junit", str(outside), capsys=capsys)
    assert code == 2 and payload["code"] == "HUMAN_REPORT_INVALID"
    code, payload = _human_next(project, run_root, task, "--junit", str(_report_target(task, "missing.xml")), capsys=capsys)
    assert code == 2 and payload["code"] == "HUMAN_REPORT_INVALID"
    assert driver.advance(project, run_root)["action"] == "human_exec"  # still waiting, nothing recorded
    empty = _report_target(task, "empty.xml")
    empty.write_text('<testsuite name="x" tests="0"/>', encoding="utf-8")
    code, payload = _human_next(project, run_root, task, "--junit", str(empty), "--exit-code", "0", capsys=capsys)
    done = _drive(project, payload, model) if payload["action"] not in {"done"} else payload
    assert done["action"] == "done" and done["result"]["verification"] != "PASS", done  # a report that ran nothing proves nothing


def test_more_tests_than_the_selectors_do_not_break_the_acceptance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    project, run_root, task, model = _until_human(tmp_path, monkeypatch)
    from tools.pilot_state import read_attempt_receipt

    # The person also ran a test of the project outside the selectors, and it fails: that is not the reviewed tests' result.
    assert _run_commands(task, extra=["tests/test_existing.py::test_unselected"])[1] == 1
    reports = _reports(task)
    code, payload = _human_next(project, run_root, task, *[item for path in reports for item in ("--junit", path)], capsys=capsys)
    done = _drive(project, payload, model) if payload["action"] != "done" else payload
    assert done["result"]["verification"] == "PASS", done
    receipt = read_attempt_receipt(run_root, task["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert receipt["execution"]["extra_testcases"] == 1  # counted, not evidence


def test_auto_mode_is_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1"), SavedModel("local-pilot-v1"))
    assert done["result"]["verification"] == "PASS"
    with pytest.raises(driver.DriverError):
        _start(_project(tmp_path / "bad", local=True), "local-pilot-v1", execution_mode="robot")


def test_the_run_command_reports_a_driver_error_as_json(tmp_path: Path) -> None:
    """``python -m tools.pipeline_driver run``: a DriverError of the imported module is JSON, not a traceback (kit run, 2026-10-19)."""
    import sys

    project = _project(tmp_path, local=False)
    completed = subprocess.run([sys.executable, "-m", "tools.pipeline_driver", "run", "--project", str(project), "--runner", "process",
                                "--profile", "cases-only-v1", "--docs", "docs/feature.md", "--task-input-bytes", "100", "--confirm-context", "no"],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8",
                               env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert completed.returncode == 2 and "Traceback" not in completed.stderr, completed.stderr[-2000:]
    payload = json.loads(completed.stdout)
    assert payload["action"] == "error" and payload["code"] == "TASK_INPUT_TOO_LARGE" and payload["errors"][0]["threshold"] == 100


def test_a_mutation_step_is_done_only_when_its_output_exists(tmp_path: Path) -> None:
    """S9, PIT in human mode: the runner starts nothing, removes the expected output first and accepts it only when it exists."""
    from tools import mutation

    workdir = tmp_path / "mutation"
    output = workdir / "project-classpath.txt"
    output.parent.mkdir(parents=True)
    output.write_text("stale\n", encoding="utf-8")
    runner = mutation.human_runner(workdir)
    argv = ["mvn", "-q", "dependency:build-classpath", f"-Dmdep.outputFile={output}"]
    with pytest.raises(mutation.HumanMutationPending) as pending:
        runner(argv, tmp_path, None, timeout=10)
    command = pending.value.command
    assert (command["step"], command["step_index"], command["steps_total"], command["expected_output"]) == ("resolve-project", 1, 3, str(output))
    assert not output.exists()  # a stale output can never prove the person's run
    with pytest.raises(mutation.HumanStepIncomplete):
        mutation.human_continue(workdir)
    output.write_text("", encoding="utf-8")
    with pytest.raises(mutation.HumanStepIncomplete):  # empty is not done
        mutation.human_continue(workdir)
    output.write_text("a.jar\n", encoding="utf-8")
    with pytest.raises(mutation.HumanStepIncomplete):  # a resolver step is not answered with a PIT report
        mutation.human_continue(workdir, pit_report=str(output))
    mutation.human_continue(workdir)
    assert runner(argv, tmp_path, None, timeout=10).exit_code == 0  # the stage repeats past the step without a process
    output.unlink()  # the output disappears after the step was done: the step is issued again, no dead end
    with pytest.raises(mutation.HumanMutationPending):
        runner(argv, tmp_path, None, timeout=10)
    output.write_text("a.jar\n", encoding="utf-8")
    mutation.human_continue(workdir)
    assert runner(argv, tmp_path, None, timeout=10).exit_code == 0
    report_dir = workdir / "report"
    pit = ["java", "-cp", "x", "org.pitest.mutationtest.commandline.MutationCoverageReport", "--reportDir", str(report_dir)]
    with pytest.raises(mutation.HumanMutationPending) as pending:
        runner(pit, tmp_path, None, timeout=10)
    assert pending.value.command["step"] == "pit"
    report_dir.mkdir()
    (report_dir / "mutations.xml").write_text("<mutations/>", encoding="utf-8")
    with pytest.raises(mutation.HumanStepIncomplete):
        mutation.human_continue(workdir, pit_report=str(tmp_path / "elsewhere.xml"))
    with pytest.raises(mutation.HumanStepIncomplete):
        mutation.human_continue(workdir)
    mutation.human_continue(workdir, pit_report=str(report_dir / "mutations.xml"))
    assert runner(pit, tmp_path, None, timeout=10).exit_code == 0


def test_a_suite_step_is_done_only_with_its_reports_or_a_failed_build(tmp_path: Path) -> None:
    """S9b, suite-update in human mode: reports inside the report location, or a nonzero exit code; a lost report re-issues the step."""
    from tools import suite_run

    workdir = tmp_path / "human"
    report = tmp_path / "module" / "target" / "surefire-reports"
    report.mkdir(parents=True)
    (report / "old.xml").write_text("<testsuite/>", encoding="utf-8")
    with pytest.raises(suite_run.HumanSuitePending) as pending:
        suite_run._human_run(workdir, ["mvn", "test"], str(tmp_path), report, "suite-run")
    assert pending.value.command["expected_output"] == str(report) and not report.exists()  # only the report location is removed
    with pytest.raises(suite_run.HumanStepIncomplete):
        suite_run.human_continue(workdir, [], None)
    with pytest.raises(suite_run.HumanStepIncomplete):
        suite_run.human_continue(workdir, [str(tmp_path / "elsewhere.xml")], 0)
    report.mkdir()
    (report / "TEST-x.xml").write_text('<testsuite><testcase classname="A" name="b"/></testsuite>', encoding="utf-8")
    suite_run.human_continue(workdir, [str(report / "TEST-x.xml")], None)
    assert suite_run._human_run(workdir, ["mvn", "test"], str(tmp_path), report, "suite-run").returncode == 0
    with pytest.raises(suite_run.HumanSuitePending):  # the same command as another step is a new step
        suite_run._human_run(workdir, ["mvn", "test"], str(tmp_path), report, "repair-run")
    log = tmp_path / "build.log"
    log.write_text("[ERROR] COMPILATION ERROR\n", encoding="utf-8")
    suite_run.human_continue(workdir, [], 1, str(log))  # the build failed before any report
    outcome = suite_run._human_run(workdir, ["mvn", "test"], str(tmp_path), report, "repair-run")
    assert outcome.returncode == 1 and "COMPILATION ERROR" in outcome.stdout
    for path in report.glob("*.xml"):
        path.unlink()
    with pytest.raises(suite_run.HumanSuitePending):  # suite-run reports are gone: issued again, never a dead end
        suite_run._human_run(workdir, ["mvn", "test"], str(tmp_path), report, "suite-run")
