import json
import os
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator


def _validate_output(root, schema_name, completed):
    document = json.loads(completed.stdout)
    schema = json.loads((root / "schemas" / schema_name).read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    return document


def _run_runner(root, *arguments):
    return subprocess.run(
        [sys.executable, str(root / "tools" / "run_tests.py"), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_python_override_precedes_current_interpreter(runner, tmp_path):
    """Catches ignoring an explicit Python interpreter selected for the project."""
    executable = tmp_path / ("python.exe" if runner.os.name == "nt" else "python")
    executable.write_text("", encoding="utf-8")

    assert runner.select_python_interpreter(str(tmp_path), str(executable)) == str(executable)


def test_project_venv_precedes_host_interpreter(runner, tmp_path):
    """Catches running host pytest instead of the project's virtual environment."""
    venv_python = tmp_path / ".venv" / ("Scripts/python.exe" if runner.os.name == "nt" else "bin/python")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    assert runner.select_python_interpreter(str(tmp_path), None) == str(venv_python)


def test_python_cli_reports_selected_interpreter_and_nonzero_not_runnable(root, tmp_path):
    """Catches a missing selected interpreter being reported as a successful execution."""
    missing_python = tmp_path / "not-runnable-python.exe"
    missing_python.write_text("not an executable", encoding="utf-8")
    completed = _run_runner(
        root, "--project", str(tmp_path), "--language", "python",
        "--python-executable", str(missing_python),
    )

    report = _validate_output(root, "run-tests-output.schema.json", completed)
    assert completed.returncode != 0
    assert report["verdict"] == "NOT_RUNNABLE"
    assert report["environment"]["interpreter_path"] == str(missing_python)


def test_java_wrapper_precedes_system_maven(runner, tmp_path, monkeypatch):
    """Catches bypassing a project Maven wrapper in favor of a machine Maven."""
    wrapper = tmp_path / ("mvnw.cmd" if runner.os.name == "nt" else "mvnw")
    wrapper.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "C:/tools/mvn.cmd" if name == "mvn" else "C:/tools/java.exe")

    environment = runner.check_java_env(str(tmp_path))

    assert environment["status"] == "ready"
    assert environment["_runner"] == wrapper.name


def test_maven_uses_final_surefire_aggregate(runner, monkeypatch, tmp_path):
    """Catches reporting only the first suite instead of Maven's final aggregate."""
    output = (
        "Tests run: 13, Failures: 0, Errors: 0, Skipped: 0\n"
        "Tests run: 11, Failures: 0, Errors: 0, Skipped: 0\n"
        "Tests run: 24, Failures: 0, Errors: 0, Skipped: 0"
    )
    monkeypatch.setattr(runner, "run_subprocess", lambda cmd, cwd: (0, output, ""))

    result = runner.run_java(str(tmp_path), "mvnw.cmd", None)

    assert result["target"]["runner"] == "maven"
    assert result["stats"]["total"] == 24
    assert result["stats"]["passed"] == 24


@pytest.mark.skipif(os.name != "nt", reason="Windows command-wrapper behavior")
def test_windows_project_wrapper_is_resolved_from_project_directory(runner, tmp_path, monkeypatch):
    """Catches invoking mvnw.cmd by PATH instead of its project-local wrapper path."""
    wrapper = tmp_path / "mvnw.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")
    observed = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kwargs: observed.setdefault("cmd", cmd) and Completed())
    runner.run_subprocess(["mvnw.cmd", "test"], str(tmp_path))

    assert observed["cmd"] == ["cmd", "/c", str(wrapper), "test"]


@pytest.mark.parametrize("wrapper", ["mvnw", "gradlew"])
def test_posix_project_wrapper_is_invoked_from_project_directory(runner, monkeypatch, tmp_path, wrapper):
    """Catches project wrappers being selected but lost because POSIX PATH omits dot."""
    observed = {}

    def fake_run(command, cwd):
        observed["command"] = command
        observed["cwd"] = cwd
        return 0, "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0", ""

    monkeypatch.setattr(runner, "run_subprocess", fake_run)
    monkeypatch.setattr(runner, "is_windows", lambda: False)

    result = runner.run_java(str(tmp_path), wrapper, None)

    assert result["verdict"] == "PASS"
    assert observed == {"command": [f"./{wrapper}", "test"], "cwd": str(tmp_path)}


def test_no_language_cli_is_nonzero_and_schema_valid(root, tmp_path):
    """Catches the no-.skillsrc NOT_RUNNABLE branch returning a success process code."""
    completed = _run_runner(root, "--project", str(tmp_path))

    report = _validate_output(root, "run-tests-output.schema.json", completed)
    assert completed.returncode == 2
    assert report["verdict"] == "NOT_RUNNABLE"


def test_python_pass_and_fail_cli_outputs_validate_against_schema(root, tmp_path):
    """Catches CLI PASS/FAIL documents drifting from the public output schema."""
    passing = tmp_path / "test_passing.py"
    passing.write_text("def test_passing():\n    assert True\n", encoding="utf-8")
    passed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--pytest-target", str(passing))
    pass_report = _validate_output(root, "run-tests-output.schema.json", passed)
    assert passed.returncode == 0
    assert pass_report["verdict"] == "PASS"

    passing.write_text("def test_failing():\n    assert False\n", encoding="utf-8")
    failed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--pytest-target", str(passing))
    fail_report = _validate_output(root, "run-tests-output.schema.json", failed)
    assert failed.returncode == 1
    assert fail_report["verdict"] == "FAIL"


def test_internal_error_report_is_schema_valid(runner, root):
    """Catches the top-level exception report drifting from the normal CLI contract."""
    report = runner.build_internal_error_report(RuntimeError("forced internal error"))
    schema = json.loads((root / "schemas" / "run-tests-output.schema.json").read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(report)) == []
    assert report["verdict"] == "NOT_RUNNABLE"
