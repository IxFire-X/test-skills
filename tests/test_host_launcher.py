"""H1 B7, B9: the launcher ts.py at the pack root and the directories of CLI agents that are no project code.

The orchestrating session starts every command as ``<python> <pack>/ts.py next|submit|status|answer|doctor`` from the
project directory: no ``cd``, so the command the CLI asks the person to confirm starts with the interpreter itself.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import _project

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "ts.py"


def _ts(cwd: Path, *args: str, launcher: Path = LAUNCHER) -> tuple[int, dict]:
    completed = subprocess.run([sys.executable, str(launcher), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=False)
    assert "Traceback" not in completed.stderr, completed.stderr[-3000:]
    return completed.returncode, json.loads(completed.stdout)


def test_the_launcher_runs_the_driver_from_any_directory(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code, task = _ts(elsewhere, "next", "--project", str(project), "--profile", "cases-only-v1", "--docs", "docs/feature.md",
                     "--confirm-context", "no", "--compact")
    assert code == 3 and task["action"] == "llm", task
    code, status = _ts(elsewhere, "status", "--project", str(project), "--run", task["run_id"])
    assert code == 0 and status["action"] == "status"


def test_the_launcher_runs_the_doctor_of_its_own_pack(tmp_path: Path) -> None:
    code, report = _ts(tmp_path, "doctor")
    assert code == 0 and report["status"] == "PASS" and Path(report["root"]) == ROOT


@pytest.mark.parametrize("args", [(), ("run", "--project", "."), ("scan",), ("--help",)], ids=["none", "run", "scan", "help"])
def test_the_launcher_knows_only_its_commands(tmp_path: Path, args: tuple[str, ...]) -> None:
    code, payload = _ts(tmp_path, *args)
    assert code == 2 and payload["action"] == "error" and payload["code"] == "LAUNCHER_COMMAND", payload


def test_the_launcher_has_no_dynamic_import() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "importlib" not in text and "__import__" not in text and "runpy" not in text and "exec(" not in text


def test_a_changed_launcher_fails_the_doctor(tmp_path: Path) -> None:
    from tools.release_manifest import delivery_files

    copy = tmp_path / "pack"
    for relative in delivery_files(ROOT, "host"):
        (copy / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, copy / relative)
    assert "ts.py" in delivery_files(ROOT, "host")
    code, report = _ts(tmp_path, "doctor", launcher=copy / "ts.py")
    assert code == 0 and report["delivery_profile"] == "host", report
    (copy / "ts.py").write_text((copy / "ts.py").read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    code, report = _ts(tmp_path, "doctor", launcher=copy / "ts.py")
    assert code == 2 and report["status"] == "NOT_RUNNABLE"


@pytest.mark.parametrize("agent", [".gigacode", ".claude", ".codex", ".qwen", ".gemini"])
def test_a_cli_agent_directory_is_no_module(tmp_path: Path, agent: str) -> None:
    """H2 finding: the kit lies in <project>/.gigacode/skills/test-skills and must not become a Python module of .skillsrc."""
    from tools.discover_project import discover_project

    project = _project(tmp_path, local=False)
    kit = project / agent / "skills" / "test-skills"
    (kit / "tools").mkdir(parents=True)
    (kit / "requirements.txt").write_text("jsonschema==4.23.0\n", encoding="utf-8")
    (kit / "pyproject.toml").write_text("[project]\nname = 'kit'\nversion = '0'\n", encoding="utf-8")
    (kit / "tools" / "driver.py").write_text("X = 1\n", encoding="utf-8")
    (kit / "tests").mkdir()
    (kit / "tests" / "test_kit.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    report = discover_project(project)
    paths = json.dumps(report["modules"], ensure_ascii=False)
    assert agent not in paths, report["modules"]
