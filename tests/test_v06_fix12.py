"""fix12: a delivery without the mode of automated tests answers a run of local-pilot-v1 with MODE_B_NOT_IN_PACKAGE and a way out."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_finish import _automation_chain
from tests.test_mode_b_split import ABSENT


def _tree(root: Path) -> dict[str, int]:
    return {path.relative_to(root).as_posix(): path.stat().st_size for path in root.rglob("*") if path.is_file()}


def _answer(text: str) -> dict:
    """The JSON document at the start of the output (scan prints a few lines of plain text after it)."""
    return json.JSONDecoder().raw_decode(text.lstrip())[0]


def _events(run_root: Path) -> list[dict]:
    from tools.pilot_state import derive_state

    return list(derive_state(run_root)["events"])


def _without_mode_b(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ABSENT:  # as a delivery of cases: the nine modules and the execution files are not there
        monkeypatch.setitem(sys.modules, "tools." + name, None)


@pytest.mark.parametrize("command", ["status", "report", "finish"])
def test_a_cases_delivery_refuses_a_run_of_the_full_delivery_with_a_way_out(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                            monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    from tools import run_pipeline

    run_root, _attempt_id = _automation_chain(tmp_path, ["AUTO_FIX_APPLIED"])
    project = run_root.parents[1]
    capsys.readouterr()
    before, events = _tree(run_root), _events(run_root)
    _without_mode_b(monkeypatch)

    code = run_pipeline.main([command, "--project", str(project), "--run", run_root.name])

    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["reason"] == "MODE_B_NOT_IN_PACKAGE", out
    assert out["fix"] and "--new-run" in out["fix"] and not re.search(r"(status|report|finish)", out["fix"]), out
    # nothing but the journal of refusals is written — its line and one event COMMAND_REFUSED — and by finish only: status and report only
    # read and leave the run as it was (external review 9b3d1d20, item 2); no report, nothing published
    after = _tree(run_root)
    written = command == "finish"
    assert {name for name in after if name not in before} == ({"refusals.jsonl", f"events/{len(events) + 1:010d}.json"} if written else set()), set(after) - set(before)
    assert [name for name in before if after[name] != before[name]] == (["events.jsonl"] if written else [])
    assert [event["event_type"] for event in _events(run_root)[len(events):]] == (["COMMAND_REFUSED"] if written else [])
    assert not any(name.startswith(("report", "published")) for name in after)


def test_a_refusal_with_the_code_of_a_missing_mode_prints_its_fix(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import execution_gate, run_pipeline

    def missing(_args: object) -> int:
        raise execution_gate.ModeBMissing("в этой поставке нет модуля режима Б tools/trace_check.py")

    monkeypatch.setattr(run_pipeline, "cmd_status", missing)
    assert run_pipeline.main(["status", "--project", ".", "--run", "0" * 32]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["reason"] == "MODE_B_NOT_IN_PACKAGE" and out["fix"], out


def test_a_cases_delivery_still_answers_status_of_a_run_of_its_own_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_requested import _generated_run
    from tools import run_pipeline
    from tools.pilot_state import read_run

    run = _generated_run(tmp_path, generated=False)
    assert read_run(run["run_root"])["manifest"]["policy_profile"] == "cases-only-v1"
    _without_mode_b(monkeypatch)
    assert run_pipeline.main(["status", "--project", str(run["run_root"].parents[1]), "--run", run["run_root"].name]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_the_advice_of_the_fix_works_a_new_run_of_cases_only_starts_beside_a_run_of_the_full_delivery(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_phase2_scan import _project
    from tools import run_pipeline
    from tools.pilot_state import read_run

    project, _module = _project(tmp_path)
    assert run_pipeline.main(["scan", "--project", str(project), "--module", "nested"]) == 0
    first = _answer(capsys.readouterr().out)
    assert read_run(Path(first["run_root"]))["manifest"]["policy_profile"] == "local-pilot-v1"
    _without_mode_b(monkeypatch)

    assert run_pipeline.main(["scan", "--project", str(project), "--module", "nested", "--new-run"]) == 0
    second = _answer(capsys.readouterr().out)
    assert second["run_id"] != first["run_id"] and read_run(Path(second["run_root"]))["manifest"]["policy_profile"] == "cases-only-v1"
