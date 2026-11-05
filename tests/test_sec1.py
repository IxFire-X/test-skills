from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# FaT-SEC1 (security review of 79d4052): the agent never installs into a system Python, the probe line hides a secret value
# without control characters, and git never runs a hook of the project (core.fsmonitor).


def test_the_package_installs_nothing_and_offers_no_pip() -> None:
    """P5: no third-party software at runtime, so nothing is installed: no pip command in the tools, an empty requirements.txt."""
    from tools import ready

    assert not hasattr(ready, "pip_command")
    root = Path(__file__).resolve().parents[1]
    offenders = [path.name for path in sorted((root / "tools").glob("*.py")) if "pip install" in path.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_a_secret_in_a_probe_line_keeps_its_key_and_hides_its_value() -> None:
    from tools.exec_probe import _scrub

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
    from tools.ready import _git_row

    project, marker = _hooked_repo(tmp_path)
    for profile in ("cases-only-v1", "local-pilot-v1"):
        assert _git_row(project, profile)["level"] == "OK"
    assert not marker.exists()


def test_scan_state_does_not_run_the_fsmonitor_hook_of_the_project(tmp_path: Path) -> None:
    """V4a: the project state of a run no longer asks git at all; the hook of the project's own configuration has nothing to run on."""
    from tools.pilot_state import _project_state_for

    project, marker = _hooked_repo(tmp_path)
    assert _project_state_for("local-pilot-v1", project) is None
    assert not marker.exists()
