"""v0.6 fix11: ``finish`` decides by the journal, not by a file of the run directory, and writes nothing before it refuses."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_rework_stage import STAGE, _published, _reviewed
from tests.test_stage_steps import _events, _status
from tools import api


def _finish(run_root: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, dict]:
    from tools.run_pipeline import main

    capsys.readouterr()
    code = main(["finish", "--project", str(run_root.parents[1]), "--run", run_root.name, *extra])
    return code, json.loads(capsys.readouterr().out)


def _nothing_written(run_root: Path, before: tuple[list[str], list[str]]) -> bool:
    return (_events(run_root), sorted(path.name for path in (run_root / "published").iterdir()) if (run_root / "published").is_dir() else []) == before


# 1. a finish in the middle of a rework ------------------------------------------------------------------------------------

def test_finish_in_the_middle_of_a_rework_of_a_build_run_refuses_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_partial_acceptance_run import _fixture_run

    _fixture_run(monkeypatch)
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    before = (_events(root), _published(run))
    code, out = _finish(root, capsys)
    assert code == 2 and out["code"] == "FINISH_FACT_UNDETERMINED", out
    assert "tc-generator:rework-1" in out["fix"] and "--abort REWORK_UNUSABLE" in out["fix"] and "затем finish" not in out["fix"], out
    assert _nothing_written(root, before) and _status(root)["next"] == {"verb": "prepare", "stage": STAGE}
    command = re.search(r"-m tools\.api (submit .*?--abort REWORK_UNUSABLE)", out["fix"])  # the cancel as written, without a prepare before it
    assert command and api.main([word.strip('"') for word in command.group(1).split()]) == 0, capsys.readouterr()
    assert "rework" not in _status(root)["next"]["stage"], _status(root)  # after the cancel status names the automation, not the rework


def test_finish_abandon_in_the_middle_of_a_rework_of_a_cases_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    before = (_events(root), _published(run))
    code, out = _finish(root, capsys, "--abandon", "STAGE_RETRY_EXHAUSTED")
    assert code == 2 and out["code"] == "ABANDON_NOT_NEEDED", out
    assert _nothing_written(root, before) and _status(root)["next"] == {"verb": "prepare", "stage": STAGE}


def test_finish_with_a_spoiled_rework_receipt_says_a_new_run_not_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    receipt = root / "rework-rounds" / f"{run['attempt_id']}.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")  # one file of the run, changed: the journal binds its digest
    before = (_events(root), _published(run))
    code, out = _finish(root, capsys)
    assert code == 2 and out["code"] == "FINISH_FACT_UNDETERMINED" and "--new-run" in out["fix"], out
    assert _nothing_written(root, before)


# 2. the receipts of the written tests are gone, the journal binds them ---------------------------------------------------

def test_finish_does_not_close_an_attempt_whose_bound_receipts_of_the_written_tests_are_gone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exec_prepares import _project
    from tests.test_p4_ingest import _args
    from tools import exec_commands, pilot_state

    project, root, attempt_id = _project(tmp_path)
    assert exec_commands.cmd_materialize(_args(project, root)) == 0
    written = sorted(path for path in project.rglob("test_generated.py") if ".pilot-runs" not in path.parts)
    assert written, "materialize wrote nothing into the project"
    capsys.readouterr()
    for folder in ("materialization-ownership", "generated-deltas"):
        (root / folder / f"{attempt_id}.json").unlink()
    before = (_events(root), [])
    code, out = _finish(root, capsys)
    assert code == 2 and out["code"] == "EVIDENCE_UNVERIFIED", out
    assert _events(root) == before[0] and not (root / "terminal-results" / f"{attempt_id}.json").exists()
    assert pilot_state.derive_state(root)["attempts"][-1]["state"] != "TERMINAL" and all(path.is_file() for path in written)


# 4. one answer for «does finish close it» -----------------------------------------------------------------------------

@pytest.mark.parametrize("build", [False, True])
def test_the_hint_of_scan_offers_finish_only_where_finish_closes_a_waiting_rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build: bool) -> None:
    from tests.test_partial_acceptance_run import _fixture_run
    from tools.run_pipeline import _run_in_progress

    if build:
        _fixture_run(monkeypatch)
    run, _done = _reviewed(tmp_path, monkeypatch)
    assert (" finish " in _run_in_progress(run["project"], run["run_root"])["fix"]) is (not build)


# 5. after a cut select, finish leaves neither REJECTED nor UNREVIEWED ---------------------------------------------------

def test_finish_after_a_select_cut_before_its_rejected_file_leaves_a_file_of_the_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_review_canaries import _answer, _finding, _generated_run, _ids, _open, _select
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, two = _ids(run)
    real = orchestrate.write_status_files

    def cut(run_root, document, output_dir, csv_profile, status, *args, **kwargs):
        if status == "REJECTED":
            raise OSError("cut")
        return real(run_root, document, output_dir, csv_profile, status, *args, **kwargs)

    monkeypatch.setattr(orchestrate, "write_status_files", cut)
    with pytest.raises(OSError, match="cut"):  # the verdict is in the ledger, its file is not written
        _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [one["case_id"], two["case_id"]], "Нет проверки состояния.")]))
    monkeypatch.setattr(orchestrate, "write_status_files", real)
    code, out = _finish(run["run_root"], capsys)
    assert code == 1 and out["reason_code"] == "REWORK", out
    assert [name for name in _published(run) if "REJECTED" in name or "UNREVIEWED" in name], _published(run)


def test_finish_of_a_rejected_run_leaves_a_lying_rejected_file_as_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_review_canaries import _answer, _finding, _generated_run, _ids, _open, _select
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, two = _ids(run)
    _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [one["case_id"], two["case_id"]], "Нет проверки состояния.")]))
    lying = run["run_root"] / "published" / next(name for name in _published(run) if name.endswith("REJECTED.md"))
    lying.write_bytes(b"the form of an earlier version")  # a set rejected by another version of the package: other bytes of the same file
    code, out = _finish(run["run_root"], capsys)
    assert code == 1 and out["reason_code"] == "REWORK", out
    assert lying.read_bytes() == b"the form of an earlier version"
