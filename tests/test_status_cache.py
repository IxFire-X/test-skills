"""FaT-ST: status derives the journal and validates the run manifest once per call and starts no process.

On Petclinic one call took 115 s: the manifest lists ~1200 project files, its uniqueItems check is quadratic (~3.4 s)
and is not remembered for a document over 64 KB, and status read the manifest and derived the state many times."""

from __future__ import annotations

import collections
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _terminal_run(tmp_path: Path) -> Path:
    from tests.test_run_report import _finished_run

    return _finished_run(tmp_path)


def _open_run(tmp_path: Path) -> Path:
    from tests.test_review_notes import _selected_run, _two_notes

    return _selected_run(tmp_path, _two_notes)[0]


@pytest.mark.parametrize("make_run", [_terminal_run, _open_run], ids=["terminal", "open"])
def test_status_derives_once_validates_the_manifest_once_and_starts_no_process(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], make_run) -> None:
    from tools import pilot_state
    from tools.run_pipeline import cmd_status

    run_root = make_run(tmp_path)
    capsys.readouterr()
    manifest_schema = (pilot_state._SCHEMA_ROOT / "run-manifest.schema.json").read_text(encoding="utf-8")
    counts: collections.Counter = collections.Counter()
    derive, schema_valid, validator = pilot_state._derive_state, pilot_state._artifact_schema_valid, pilot_state._artifact_validator

    def counted_schema_valid(schema_text: str, serialized: str) -> bool:
        counts.update(["manifest"] if schema_text == manifest_schema else [])
        return schema_valid(schema_text, serialized)

    def counted_validator(schema_text: str):
        # the path of a document over 64 KB (the Petclinic manifest) — no lru cache behind it
        counts.update(["manifest"] if schema_text == manifest_schema else [])
        return validator(schema_text)

    def no_process(*args, **kwargs):
        raise AssertionError(f"status started a process: {args!r}")

    monkeypatch.setattr(pilot_state, "_derive_state", lambda root: counts.update(["derive"]) or derive(root))
    monkeypatch.setattr(pilot_state, "_artifact_schema_valid", counted_schema_valid)
    monkeypatch.setattr(pilot_state, "_artifact_validator", counted_validator)
    monkeypatch.setattr(subprocess, "Popen", no_process)

    assert cmd_status(SimpleNamespace(project=str(tmp_path), run=run_root.name)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok" and output["run_id"] == run_root.name
    assert counts["derive"] == 1, counts
    assert counts["manifest"] == 1, counts


def test_status_still_checks_the_whole_journal_before_it_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """The block's final full check runs before the answer is printed: a forged event copy is an error, not a status."""
    from tools import pilot_state
    from tools.run_pipeline import main

    run_root = _terminal_run(tmp_path)
    capsys.readouterr()
    calls: collections.Counter = collections.Counter()
    full = pilot_state._events_full
    monkeypatch.setattr(pilot_state, "_events_full", lambda project, root: calls.update(["full"]) or full(project, root))
    assert main(["status", "--project", str(tmp_path), "--run", run_root.name]) == 0
    assert calls["full"] >= 1
    capsys.readouterr()

    copy = run_root / "events" / "0000000001.json"
    copy.write_bytes(copy.read_bytes().replace(b'"seq":1', b'"seq":1 ', 1))
    assert main(["status", "--project", str(tmp_path), "--run", run_root.name]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
