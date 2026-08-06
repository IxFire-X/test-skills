import json
import subprocess
import sys


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
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "run_tests.py"),
            "--project", str(tmp_path),
            "--language", "python",
            "--python-executable", str(missing_python),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(completed.stdout)
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
    output = "\n".join([
        "Tests run: 13, Failures: 0, Errors: 0, Skipped: 0",
        "Tests run: 11, Failures: 0, Errors: 0, Skipped: 0",
        "Tests run: 24, Failures: 0, Errors: 0, Skipped: 0",
    ])
    monkeypatch.setattr(runner, "run_subprocess", lambda cmd, cwd: (0, output, ""))

    result = runner.run_java(str(tmp_path), "mvnw.cmd", None)

    assert result["target"]["runner"] == "maven"
    assert result["stats"]["total"] == 24
    assert result["stats"]["passed"] == 24


def test_windows_project_wrapper_is_resolved_from_project_directory(runner, tmp_path, monkeypatch):
    """Catches invoking mvnw.cmd by PATH instead of its project-local wrapper path."""
    if runner.os.name != "nt":
        return
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
