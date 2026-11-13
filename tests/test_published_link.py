"""``<run>/published`` is where the package publishes; a run directory the agent can write must not turn it into a way out of the run.

The directory is not an argument of any command, so the check stands where the code takes it (``published_dir``): a link or junction
in its place is refused with OUTPUT_DIR_OUTSIDE_RUN, nothing is created outside and the journal does not change."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_closure_deadlocks import _run as _pipeline
from tests.test_review_stage import STAGE, _answer, _call, _opened, _prepare, _run, _submit
from tests.test_stage_steps import _events, _junction

CODE = "OUTPUT_DIR_OUTSIDE_RUN"


def _published_leads_out(run: dict, tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    published = run["run_root"] / "published"
    if published.is_dir():
        shutil.rmtree(published)
    _junction(published, outside)
    return outside


def test_prepare_does_not_publish_the_candidate_through_a_published_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    outside = _published_leads_out(run, tmp_path)
    before = _events(run["run_root"])
    code, out = _prepare(capsys, run)
    assert code == 2 and out["code"] == CODE and list(outside.iterdir()) == [] and _events(run["run_root"]) == before


def test_the_verdict_is_not_published_through_a_published_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    outside = _published_leads_out(run, tmp_path)
    before = _events(run["run_root"])
    code, out = _submit(capsys, run, _answer(run))
    assert code == 2 and out["code"] == CODE and list(outside.iterdir()) == [] and _events(run["run_root"]) == before


def test_a_run_closed_without_a_verdict_does_not_mark_it_through_a_published_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    assert _call(capsys, "submit", run["run_root"], STAGE, "--abort", "REVIEW_UNUSABLE")[0] == 0
    outside = _published_leads_out(run, tmp_path)
    before = _events(run["run_root"])
    code, out = _pipeline(capsys, "finish", run["project"], run["run_root"])
    assert code == 2 and CODE in str(out) and list(outside.iterdir()) == [] and _events(run["run_root"]) == before
