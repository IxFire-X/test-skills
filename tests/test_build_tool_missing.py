"""FaT-NB: init and ready name a missing build tool of a Java module instead of dropping the test block silently
(run-two-repos: no mvn on PATH, ready only said «пойдёт как cases-only»)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_first_run import _fake_mvn, _java_project


def _no_build_tool_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_init_without_mvn_names_the_missing_build_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    project = _java_project(tmp_path)
    _no_build_tool_on_path(tmp_path, monkeypatch)
    receipt = ensure_skillsrc(project, {}, True)
    assert receipt["status"] == "created"
    [warning] = [item for item in receipt["warnings"] if item.startswith("BUILD_TOOL_NOT_FOUND")]
    assert "mvn" in warning and "PATH" in warning and "mvnw" in warning and "повторите init" in warning, warning
    assert b"\ntest:" not in (project / ".skillsrc").read_bytes() and b"  test:" not in (project / ".skillsrc").read_bytes()


def test_init_with_mvn_writes_the_test_block_as_before_and_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.skillsrc_manifest import load_skillsrc

    project = _java_project(tmp_path)
    launcher = _fake_mvn(tmp_path, monkeypatch)
    receipt = ensure_skillsrc(project, {}, True)
    assert receipt["status"] == "created" and not [item for item in receipt["warnings"] if item.startswith("BUILD_TOOL_NOT_FOUND")]
    [module] = load_skillsrc(project / ".skillsrc")["modules"]
    assert module["test"] == {"framework": "junit5", "adapter_id": "maven:selected-symbols-v1", "executable": str(launcher.resolve()),
                              "build_profile": "default", "adapter_parameters": {}}


def test_ready_names_the_same_reason_in_execution_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.ready import check

    project = _java_project(tmp_path)
    _no_build_tool_on_path(tmp_path, monkeypatch)
    ensure_skillsrc(project, {}, True)
    report = check(project, docs=["docs/feature.md"], profile="local-pilot-v1")
    row = next(item for item in report["checks"] if item["id"] == "execution:build")
    assert row["level"] == "WARN" and "BUILD_TOOL_NOT_FOUND" in row["message"] and "mvn" in row["message"], row
    assert "PATH" in row["fix"] and "init" in row["fix"], row


def test_a_gradle_module_without_wrapper_names_the_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import build_tool_missing

    project = tmp_path / "gradle"
    project.mkdir()
    reason, fix = build_tool_missing({"root": ".", "stack": {"language": "java", "build_tool": "gradle"}}, project)
    assert reason.startswith("BUILD_TOOL_NOT_FOUND") and ("gradlew.bat" if os.name == "nt" else "gradlew") in reason and "wrapper" in fix
    (project / ("gradlew.bat" if os.name == "nt" else "gradlew")).write_text("", encoding="utf-8")
    assert build_tool_missing({"root": ".", "stack": {"language": "java", "build_tool": "gradle"}}, project) is None
    assert build_tool_missing({"root": ".", "stack": {"language": "python", "build_tool": "pip"}}, project) is None
