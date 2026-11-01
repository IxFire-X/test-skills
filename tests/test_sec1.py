from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# FaT-SEC1 (security review of 79d4052): the agent never installs into a system Python, the probe line hides a secret value
# without control characters, and git never runs a hook of the project (core.fsmonitor).


def test_pip_in_a_venv_is_the_running_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import ready

    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "-base")
    assert ready.pip_command().startswith(f'"{sys.executable}" -m pip install')


def test_pip_outside_a_venv_is_the_python_of_the_package_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import ready

    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    monkeypatch.setattr(ready, "PACK", tmp_path)
    venv_python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    missing = ready.pip_command()
    assert f'-m venv "{tmp_path / ".venv"}"' in missing and f'"{venv_python}" -m pip install' in missing, missing
    assert f'"{sys.executable}" -m pip' not in missing
    venv_python.parent.mkdir(parents=True)
    venv_python.write_bytes(b"")
    present = ready.pip_command()
    assert present.startswith(f'"{venv_python}" -m pip install') and "-m venv" not in present, present


def test_a_secret_in_a_probe_line_keeps_its_key_and_hides_its_value() -> None:
    from tools.ready import _scrub

    line = _scrub("[ERROR] Failed: password=Qwerty123 and token: abc.def-ghi")
    assert "password=***" in line and "token: ***" in line, line
    assert "Qwerty123" not in line and "abc.def-ghi" not in line
    assert not any(ord(char) < 32 for char in line), repr(line)


def _hooked_repo(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "hook-ran.txt"
    hook = tmp_path / "hook.sh"
    hook.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\nexit 1\n', encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "config", "core.fsmonitor", hook.as_posix()], check=True)
    (project / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-c", f"safe.directory={project.as_posix()}", "-C", str(project), "status", "--porcelain"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not marker.exists():
        pytest.skip("this git does not run core.fsmonitor hooks")
    marker.unlink()
    return project, marker


def test_ready_does_not_run_the_fsmonitor_hook_of_the_project(tmp_path: Path) -> None:
    from tools.ready import check_git

    project, marker = _hooked_repo(tmp_path)
    assert check_git(project)["level"] == "OK"
    assert not marker.exists()


def test_scan_state_does_not_run_the_fsmonitor_hook_of_the_project(tmp_path: Path) -> None:
    from tools.pilot_state import _project_state_snapshot

    project, marker = _hooked_repo(tmp_path)
    assert _project_state_snapshot(project)["kind"] == "GIT_TREE"
    assert not marker.exists()
