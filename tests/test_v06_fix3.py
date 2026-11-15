"""v0.6 fix3: the limits of a run are the ones frozen at ``scan`` (``step_size.run_limits``) wherever a step is measured or a session is opened;
``status`` and ``finish`` do not hide the cause and do not advise what cannot be done.  Each test drives the commands of the delivery, as the agent does."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_profile_flows import _api, _cli, _front, cases_only_pack  # noqa: F401  (the fixture: a copy of the delivery of cases)
from tools import pilot_state, step_size

PROFILE = "cases-only-v1"
BIG = 50_000_000


def _edit_skillsrc(project: Path, **limits: int) -> None:
    """The human edits ``.skillsrc`` after the scan."""
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["limits"] = {**document.get("limits", {}), **limits}
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _batch_stage(run: dict) -> str:
    return "tc-generator:" + json.loads(Path(run["plan"]).read_bytes())["batches"][0]["batch_id"]


def _prepare_batch(pack: Path, run: dict) -> tuple[int, dict]:
    return _api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", _batch_stage(run), "--model-id", "model-generator")


def _status(pack: Path, run: dict) -> dict:
    code, status = _cli(pack, "tools.run_pipeline", "status", "--project", str(run["project"]), "--run", run["run_root"].name)
    assert code == 0, status
    return status


def _answer_batches(pack: Path, run: dict) -> None:
    plan = json.loads(Path(run["plan"]).read_bytes())
    for index, path in enumerate(run["fragments"]):
        stage = f"tc-generator:{plan['batches'][index]['batch_id']}"
        code, prepared = _api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", stage, "--model-id", "model-generator")
        assert code == 0, prepared
        Path(prepared["result"]["answer_slot"]).write_bytes(Path(path).read_bytes())
        assert _api(pack, "submit", "--run-root", str(run["run_root"]), "--stage", stage)[0] == 0


def _open_review(pack: Path, run: dict) -> tuple[int, dict]:
    return _api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", "tc-reviewer:canonical", "--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes")


# ---- 1, 2: the limits a step is measured by are the frozen ones — and a ``.skillsrc`` edited after the scan never gets that far ------------------
# Not reproduced as the review put it: ``.skillsrc`` is always a file of the context of the run, so an edit after the scan fails the readback of the context
# receipts (``read_context_selection``: the bytes drifted) before the budget of the reviewer or of a step is computed, and ``status`` says «drift: новый scan».
# What this test pins is the property the finding was about: no review session opens with a budget taken from the file edited since.

def test_a_skillsrc_edited_after_the_scan_opens_no_review_session(tmp_path: Path, cases_only_pack: Path) -> None:
    run = _front(cases_only_pack, tmp_path, PROFILE)
    _answer_batches(cases_only_pack, run)
    _edit_skillsrc(run["project"], context_batch_bytes=BIG)
    code, refused = _open_review(cases_only_pack, run)
    assert code == 2, refused
    with pytest.raises(ValueError):
        pilot_state.read_attempt_receipt(run["run_root"], run["attempt_id"], "reviewer-session-boundary", "ARTIFACT_READ_BACK")
    assert (_status(cases_only_pack, run)["state"], _status(cases_only_pack, run)["next"]["verb"]) == ("drift", "scan")


# ---- 3: status names the step it can name and says why it cannot name the rest -----------------------------------------------------

def test_status_names_the_batch_when_a_view_of_its_inputs_is_gone(tmp_path: Path, cases_only_pack: Path) -> None:
    run = _front(cases_only_pack, tmp_path, PROFILE)
    assert _prepare_batch(cases_only_pack, run)[0] == 0
    found = step_size.stage_inputs(run["run_root"], _batch_stage(run), write_views=False)
    next(Path(row["path"]) for row in found["files"] if row.get("basis") == step_size.PLAN_VIEW).unlink()  # a view the prepare wrote
    status = _status(cases_only_pack, run)
    assert (status["waiting_for"], status["next"]) == ("model", {"verb": "submit", "stage": _batch_stage(run)}), status


# ---- 4: finish decides the way of the refusal by the journal, not by a file ----------------------------------------------------------------

def test_finish_after_an_ingest_cut_at_its_receipt_tells_to_repeat_the_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import flow_args
    from tests.test_run_scoped_authorization import _the_flow_up_to_the_report
    from tools import exec_commands
    from tools.run_pipeline import main

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    original = pilot_state.append_event

    def cut_before_the_readback(root, event_type, **kwargs):  # the receipt file is on the disk, the journal does not bind it
        if event_type == "ARTIFACT_READ_BACK" and (Path(root) / "execution-receipts" / f"{attempt_id}.json").exists():
            raise RuntimeError("cut")
        return original(root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", cut_before_the_readback)
    with pytest.raises(RuntimeError, match="cut"):
        exec_commands.cmd_ingest(flow_args(project, run_root))
    monkeypatch.setattr(pilot_state, "append_event", original)
    capsys.readouterr()
    code = main(["finish", "--project", str(project), "--run", run_root.name])
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and "ingest" in str(refused.get("fix")), refused  # the fix of an ingest cut before its receipt, not the bare «evidence is unavailable»


# ---- 6: the mark of a lost word never stops prepare ------------------------------------------------------------------------------------

def test_a_mark_of_a_lost_word_with_other_bytes_does_not_stop_prepare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_cut_model import _generator_stage, _prepare_argv
    from tests.test_review_open_recovery import _main

    run, stage = _generator_stage(tmp_path)
    assert _main(capsys, _prepare_argv(run, stage))[0] == 0
    assert _main(capsys, _prepare_argv(run, stage))[0] == 0  # the repeat makes the mark
    [mark] = list(run["run_root"].glob("codeword-lost-*"))
    mark.write_bytes(b"another file")  # an agent or a cleaning overwrote it
    assert _main(capsys, _prepare_argv(run, stage))[0] == 0


# ---- 7: the review of a rework asks the attempt before anything else --------------------------------------------------------------------

def test_the_abort_of_the_review_of_a_rework_is_refused_on_a_closed_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Direct call: ``stage_steps`` does not list this step for most closed attempts, so no command reaches the function there."""
    from tests.test_f3_scan_finish import _finish_argv, _pipeline, _rework_closed
    from tools.orchestrate_test_case_revision import abort_rework_review

    run = _rework_closed(tmp_path, capsys)
    assert _pipeline(capsys, _finish_argv(run))[1].get("status") == "ok"
    with pytest.raises(ValueError, match="ATTEMPT_TERMINAL"):
        abort_rework_review(run["run_root"], run["attempt_id"], "REVIEW_UNUSABLE", True)
