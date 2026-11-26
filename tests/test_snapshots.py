"""C4: tests/snapshots.py — a run is built once per process and restored as built."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import snapshots


def _build(project: Path) -> dict:
    (project / "a.txt").write_text("built", encoding="utf-8")
    return {"calls": [1]}


def test_a_restore_puts_back_the_tree_as_built(tmp_path: Path) -> None:
    project, value = snapshots.restored(tmp_path, "t-restore", _build)
    built = (project / "a.txt").stat().st_mtime_ns
    (project / "a.txt").write_text("changed", encoding="utf-8")
    (project / "stray.txt").write_text("x", encoding="utf-8")
    value["calls"].append(2)

    again, value = snapshots.restored(tmp_path, "t-restore", lambda _project: pytest.fail("built twice"))
    assert again == project and sorted(path.name for path in project.iterdir()) == ["a.txt"]
    assert (project / "a.txt").read_text(encoding="utf-8") == "built" and (project / "a.txt").stat().st_mtime_ns == built
    assert value == {"calls": [1]}


def test_a_snapshot_that_differs_from_the_build_is_refused(tmp_path: Path) -> None:
    project, _value = snapshots.restored(tmp_path, "t-differs", _build)
    (project.parent / "saved" / "a.txt").write_text("forged", encoding="utf-8")
    with pytest.raises(AssertionError, match="not the run as built"):
        snapshots.restored(tmp_path, "t-differs", _build)


def test_a_restored_run_is_new_files_and_status_reads_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The run facts keep no file identity (st_ino, st_ctime): a copy with new inodes at the same path is the same run."""
    from tests.test_run_report import _finished_run
    from tools.run_pipeline import main

    first = (_finished_run(tmp_path) / "events.jsonl").stat().st_ino
    run_root = _finished_run(tmp_path)
    assert (run_root / "events.jsonl").stat().st_ino != first
    capsys.readouterr()
    assert main(["status", "--project", str(run_root.parents[1]), "--run", run_root.name]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_errors"] == []
