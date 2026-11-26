"""C4: tests/snapshots.py — a run is built once per process and restored as built."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.test_reviewer_protocol as protocol
from tests import snapshots
from tests.test_review_requested import _build_is_patched, _generated_run
from tests.test_rework_stage import _reviewed
from tests.test_run_report import _finished_run
from tools import run_journal
from tools.run_pipeline import main


def _build(project: Path) -> dict:
    (project / "a.txt").write_text("built", encoding="utf-8")
    return {"calls": [1]}


def test_a_restore_puts_back_the_tree_as_built(tmp_path: Path) -> None:
    project, value = snapshots.restored(tmp_path, "t-restore", _build)
    built = (project / "a.txt").stat().st_mtime_ns
    (project / "a.txt").write_text("changed", encoding="utf-8")
    (project / "stray.txt").write_text("x", encoding="utf-8")
    value["calls"].append(2)

    snapshots._HOLDER.pop("t-restore")  # the next test
    again, value = snapshots.restored(tmp_path, "t-restore", lambda _project: pytest.fail("built twice"))
    assert again == project and sorted(path.name for path in project.iterdir()) == ["a.txt"]
    assert (project / "a.txt").read_text(encoding="utf-8") == "built" and (project / "a.txt").stat().st_mtime_ns == built
    assert value == {"calls": [1]}


def test_a_snapshot_that_differs_from_the_build_is_refused(tmp_path: Path) -> None:
    project, _value = snapshots.restored(tmp_path, "t-differs", _build)
    (project.parent / "saved" / "a.txt").write_text("forged", encoding="utf-8")
    snapshots._HOLDER.pop("t-differs")
    with pytest.raises(AssertionError, match="not the run as built"):
        snapshots.restored(tmp_path, "t-differs", _build)


def test_a_restored_run_is_new_files_and_status_reads_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The run facts keep no file identity (st_ino, st_ctime): a copy with new inodes at the same path is the same run."""
    first = (_finished_run(tmp_path) / "events.jsonl").stat().st_ino
    snapshots._HOLDER.pop("finished-run")
    run_root = _finished_run(tmp_path)
    assert (run_root / "events.jsonl").stat().st_ino != first
    capsys.readouterr()
    assert main(["status", "--project", str(run_root.parents[1]), "--run", run_root.name]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_errors"] == []


def test_a_folder_that_is_not_the_tmp_path_of_the_test_gets_a_fresh_build(tmp_path: Path) -> None:
    project, value = snapshots.restored(tmp_path / "other", "t-folder", _build)
    assert project == tmp_path / "other" / "project" and (project / "a.txt").is_file() and value == {"calls": [1]}


def test_a_second_run_of_one_key_in_one_test_is_a_fresh_build_beside_the_first(tmp_path: Path) -> None:
    first, _value = snapshots.restored(tmp_path, "t-twice", _build)
    second, _value = snapshots.restored(tmp_path, "t-twice", _build)
    assert second == tmp_path / "project" != first and (first / "a.txt").is_file() and (second / "a.txt").is_file()


def test_a_test_that_patched_a_function_of_the_build_gets_a_build_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = protocol._new_run
    monkeypatch.setattr(protocol, "_new_run", lambda project: real(project))
    assert _generated_run(tmp_path)["project"] == tmp_path / "project"


def test_a_patch_in_the_module_that_defines_a_function_of_the_build_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    real = run_journal.derive_state
    monkeypatch.setattr(run_journal, "derive_state", lambda *args, **kwargs: real(*args, **kwargs))
    assert _build_is_patched()


def test_a_test_that_patched_anything_before_the_review_gets_a_review_of_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("C4_ANY_PATCH", "1")
    assert not _reviewed(tmp_path, monkeypatch)[0]["project"].parent.name.startswith("reviewed-")


def test_a_test_that_patched_nothing_gets_the_snapshot(tmp_path: Path) -> None:
    assert _generated_run(tmp_path)["project"].parent.name.startswith("generated-run-")
