"""``materialize --restore`` puts the reviewed bytes back through the confined atomic writer — never a cut file, never through a link."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools import confined_output, exec_commands

NEW = "REVIEWED\n"


def _restore(project: Path, relative: str, monkeypatch: pytest.MonkeyPatch, test_root: str = "tests"):
    digest = "sha256:" + hashlib.sha256(NEW.encode("utf-8")).hexdigest()
    coordinates = {"run_root": project / "run", "attempt": {"attempt_id": "a1"}, "module_root": project, "manifest": {"project": project}}
    inputs = {"automation": {"artifacts": {"generated_files": [{"path": relative, "content": NEW}]}}}
    monkeypatch.setattr("tools.generated_delta.journal_automation", lambda _root, _attempt: inputs)
    monkeypatch.setattr("tools.pilot_state.read_attempt_receipt", lambda *_args: {"record": {"delta": {"test_root": test_root}}})
    return exec_commands._restore_files(coordinates, {"files": [{"path": relative, "content_digest": digest}]})


def _failing_replace(*_args: object) -> None:
    raise OSError("cut in the middle")


def test_a_failed_write_leaves_the_old_file_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "tests" / "t.py"
    target.parent.mkdir()
    target.write_text("RE", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(confined_output, "_replace_output", _failing_replace)
        assert _restore(tmp_path, "tests/t.py", monkeypatch) == ([], ["tests/t.py"])
    assert target.read_text(encoding="utf-8") == "RE" and [path.name for path in target.parent.iterdir()] == ["t.py"]


def test_a_restore_after_a_failed_one_puts_the_reviewed_file_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "tests" / "t.py"
    target.parent.mkdir()
    target.write_text("RE", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(confined_output, "_replace_output", _failing_replace)
        _restore(tmp_path, "tests/t.py", monkeypatch)
    assert _restore(tmp_path, "tests/t.py", monkeypatch) == (["tests/t.py"], [])
    assert target.read_text(encoding="utf-8") == NEW


def test_a_link_above_the_parent_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    (outside / "c").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)
    _link_or_skip(project / "tests" / "b", outside, directory=True)
    assert _restore(project, "tests/b/c/t.py", monkeypatch) == ([], ["tests/b/c/t.py"])
    assert not (outside / "c" / "t.py").exists()


def test_a_target_that_is_a_link_to_a_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside.py"
    (project / "tests").mkdir(parents=True)
    outside.write_text("OLD", encoding="utf-8")
    _link_or_skip(project / "tests" / "t.py", outside, directory=False)
    assert _restore(project, "tests/t.py", monkeypatch) == ([], ["tests/t.py"])
    assert outside.read_text(encoding="utf-8") == "OLD"


@pytest.mark.parametrize("kind", ["dotdot", "other_root"])
def test_a_path_outside_the_test_root_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    for folder in (project / "tests", project / "other", outside):
        folder.mkdir(parents=True)
    relative = {"dotdot": "../outside/t.py", "other_root": "other/t.py"}[kind]
    restored, stuck = _restore(project, relative, monkeypatch)
    assert restored == [] and len(stuck) == 1
    assert not (outside / "t.py").exists() and not (project / "other" / "t.py").exists()


def _link_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"no links on this machine: {error}")
