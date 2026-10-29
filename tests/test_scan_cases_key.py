"""FaT-CK: `--cases` is part of the scan request — its key and every scan command the package prints (bug-hunt-3, 3.2).

Before: a second CSV was blocked by the open import (SCAN_RUN_IN_PROGRESS), and fix and next_run printed scan without
--cases — run as is, that command silently made a run of cases from code, without the CSV."""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_cases_import as ci


def _argv(command: str) -> list[str]:
    return shlex.split(command[command.index(" -m tools.run_pipeline ") + len(" -m tools.run_pipeline "):], posix=True)


def test_another_csv_is_not_blocked_and_the_same_csv_names_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = ci._project(tmp_path)
    (project / "cases" / "other.csv").write_bytes(ci.CSV.replace("Склейка двух значений", "Другой кейс").encode("utf-8"))
    code, first = ci._scan(project, monkeypatch)
    assert code == 0, first
    code, other = ci._scan(project, monkeypatch, cases="cases/other.csv", new_run=False)
    assert code == 0 and other["run_id"] != first["run_id"], other
    code, same = ci._scan(project, monkeypatch, new_run=False)
    assert code == 2 and same["reason"] == "SCAN_RUN_IN_PROGRESS", same
    assert '--cases "cases/zephyr.csv"' in same["fix"] and "--new-run" in same["fix"], same


def test_next_run_of_an_import_keeps_the_csv_and_makes_an_import_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline
    from tools.pilot_state import read_run

    project = ci._project(tmp_path)
    code, scan = ci._scan(project, monkeypatch)
    assert code == 0, scan
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    code = run_pipeline.cmd_finish(SimpleNamespace(project=str(project), run=Path(scan["run_root"]).name, abandon=None, module=None))
    monkeypatch.undo()
    command = printed[-1]["next_run"]["command"]
    assert code == 2 and '--cases "cases/zephyr.csv"' in command, command

    # what the printed command does, run as is: an import run of the same CSV
    printed.clear()
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    code = run_pipeline.main(_argv(command))
    monkeypatch.undo()
    assert code == 0, printed[-1]
    again = read_run(Path(printed[-1]["run_root"]))["manifest"]["scan_request"]
    assert again["cases"] == "cases/zephyr.csv" and printed[-1]["run_id"] != scan["run_id"], again


def test_a_request_without_cases_keeps_its_old_key(tmp_path: Path) -> None:
    """Journals written before FaT-CK have no `cases`: their key is the one of a request without --cases."""
    from tools.run_pipeline import _request_key

    old = {"module": "m", "docs": ["d.md"], "target": None, "profile": "cases-only-v1", "accept_self_review": False,
           "disposition_policy": None}
    assert _request_key(tmp_path, old) == _request_key(tmp_path, {**old, "cases": None})
    assert _request_key(tmp_path, old) != _request_key(tmp_path, {**old, "cases": "cases/zephyr.csv"})
    assert _request_key(tmp_path, {**old, "cases": "cases\\zephyr.csv"}) == _request_key(tmp_path, {**old, "cases": "cases/zephyr.csv"})
