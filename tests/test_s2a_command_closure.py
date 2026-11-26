"""s2a: finish, scan, prepare and submit open one closure cache for the whole command (the way status and the execution commands do)."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from tests import test_durable_execution_trace as trace
from tests.journal_helpers import assert_refused_once, refusal_log
from tests.test_api_cli import _api, _marker_run, _marker_value, _reversed
from tests.test_first_run import _python_project
from tests.test_reviewer_protocol import _new_run
from tools import pilot_state, stage_steps
from tools.run_pipeline import main


def _count_full_checks(monkeypatch: pytest.MonkeyPatch) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    original = pilot_state._events_full

    def counted(*args, **kwargs):
        counts["full"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "_events_full", counted)
    return counts


def test_finish_checks_the_whole_journal_a_few_times_not_once_per_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, attempt_id, report, request, _durable = trace._execution_facts(tmp_path)
    trace._publish_execution(root, attempt_id, report, request=request)
    counts = _count_full_checks(monkeypatch)
    assert main(["finish", "--project", str(tmp_path / "project"), "--run", root.name]) == 0
    assert counts["full"] <= 4, counts  # 26 before the block, 2 after


def test_api_prepare_and_submit_check_the_journal_in_one_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt_id, _digests = _marker_run(tmp_path)
    counts = _count_full_checks(monkeypatch)
    assert _api(["prepare", "--run-root", str(root), "--stage", "context-marker:baseline", "--model-id", "m", "--invocation-id", "inv"], capsys)[0] == 0
    prepared = counts["full"]
    stage_steps.slot(root, "context-marker:baseline").write_text(json.dumps(_reversed(_marker_value()), ensure_ascii=False), encoding="utf-8")
    assert _api(["submit", "--run-root", str(root), "--stage", "context-marker:baseline"], capsys)[0] == 0
    assert prepared <= 4 and counts["full"] - prepared <= 4, (prepared, counts)  # 14 and more before, 2 after


def test_a_copy_forged_during_scan_gives_the_error_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """scan prints inside its body: its answer is held until the final full check, so the success is never printed before the error."""
    project = _python_project(tmp_path, runtime=False)
    derive, forged = pilot_state._derive_state, {"done": False}

    def derive_then_forge(run_root):
        state = derive(run_root)
        copy = Path(run_root) / "events" / "0000000002.json"
        if not forged["done"] and pilot_state._CLOSURE_CACHE.get() is not None and copy.is_file():
            forged["done"] = True
            copy.write_bytes(copy.read_bytes().replace(b'"seq":2', b'"seq":2 '))
        return state

    monkeypatch.setattr(pilot_state, "_derive_state", derive_then_forge)
    capsys.readouterr()
    code = main(["scan", "--project", str(project), "--docs", "services/api/docs/feature.md", "--module", "nested", "--profile", "cases-only-v1"])
    out = capsys.readouterr().out
    assert forged["done"] and code == 2, out
    assert json.loads(out)["status"] == "error" and out.count('"status"') == 1, out


def test_a_refusal_printed_inside_the_block_is_printed_and_journaled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_root, _attempt_id = _new_run(tmp_path)
    capsys.readouterr()
    seen = refusal_log(run_root)
    assert main(["finish", "--project", str(tmp_path), "--run", run_root.name]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "FINISH_FACT_UNDETERMINED"
    assert_refused_once(run_root, seen, "FINISH_FACT_UNDETERMINED", "finish")


def test_a_copy_forged_during_an_api_submit_gives_the_error_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """An ordinary stage runs through ``_cached`` and ``api.main`` prints after the handler: the success is never printed before the error."""
    root, _attempt_id, _digests = _marker_run(tmp_path)
    assert _api(["prepare", "--run-root", str(root), "--stage", "context-marker:baseline", "--model-id", "m", "--invocation-id", "inv"], capsys)[0] == 0
    stage_steps.slot(root, "context-marker:baseline").write_text(json.dumps(_reversed(_marker_value()), ensure_ascii=False), encoding="utf-8")
    copy, forged, derive = root / "events" / "0000000002.json", {"done": False}, pilot_state._derive_state

    def derive_then_forge(run_root):
        state = derive(run_root)
        if not forged["done"] and pilot_state._CLOSURE_CACHE.get() is not None:
            forged["done"] = True
            copy.write_bytes(copy.read_bytes().replace(b'"seq":2', b'"seq":2 '))
        return state

    monkeypatch.setattr(pilot_state, "_derive_state", derive_then_forge)
    code, answer = _api(["submit", "--run-root", str(root), "--stage", "context-marker:baseline"], capsys)
    assert forged["done"] and code == 2 and answer["status"] == "error", answer

