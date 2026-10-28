"""FaT-RL: no attempt state where exec and finish both refuse without naming the way out.

petclinic-rest 523ec561: the static review said ПРИНЯТО but carried a wrong `reviewed_relations_sha256`; exec refused to
materialize (MATERIALIZATION_CONFLICT), finish answered "exec closes it" — nothing could close the attempt."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_finish import _automation_chain


def _run(capsys: pytest.CaptureFixture[str], command: str, project: Path, run_root: Path) -> tuple[int, dict]:
    from tools.run_pipeline import main

    capsys.readouterr()
    code = main([command, "--project", str(project), "--run", run_root.name])
    return code, json.loads(capsys.readouterr().out)


def _wrong_relations(review: dict) -> None:
    review["artifacts"]["autotest_review"]["reviewed_relations_sha256"] = "sha256:" + "0" * 64


def _status(project: Path, run_root: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    return _run(capsys, "status", project, run_root)[1]


# --------------------------------------------------------------------------------------
# 523ec561 end to end: accepted review, unbound answer
# --------------------------------------------------------------------------------------


def _journal_before_dg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """The 523ec561 journal as the package before FaT-DG wrote it: model-response did not check the digest fields yet."""
    from tools import pilot_state

    with monkeypatch.context() as old:
        old.setattr(pilot_state, "_response_digest_rows", lambda *_args: [])
        return _automation_chain(tmp_path, ["ПРИНЯТО"], edit_review=_wrong_relations)


def test_today_model_response_refuses_the_unbound_answer_and_writes_nothing(tmp_path: Path) -> None:
    """FaT-DG keeps new runs out of this state; the closure below is for journals written before it."""
    from tools.pilot_state import ModelResponseDigestMismatch, derive_state

    with pytest.raises(ModelResponseDigestMismatch) as refused:
        _automation_chain(tmp_path, ["ПРИНЯТО"], edit_review=_wrong_relations)
    assert refused.value.code == "MODEL_RESPONSE_DIGEST_MISMATCH" and "reviewed_relations_sha256" in str(refused.value)
    [root] = list((tmp_path / ".pilot-runs").iterdir())
    events = derive_state(root)["events"]
    assert events[-1]["event_type"] == "MODEL_REQUESTED" and events[-1]["stage_instance_id"] == "autotest-reviewer:r1"
    assert not any(row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == "autotest-reviewer:r1"
                   for row in events)


def test_an_accepted_review_exec_cannot_materialize_is_closed_by_finish(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    root, attempt_id = _journal_before_dg(tmp_path, monkeypatch)

    status = _status(tmp_path, root, capsys)
    assert status["next_step"]["stage"] == "finish", status["next_step"]

    code, refused = _run(capsys, "exec", tmp_path, root)
    assert code == 2 and refused["reason"] == "EXECUTION_NOT_PREPARED", refused
    assert "finish" in refused["fix"] and "AUTOMATION_NOT_MATERIALIZABLE" in refused["fix"], refused

    code, closed = _run(capsys, "finish", tmp_path, root)
    assert closed["status"] == "ok" and closed["branch"] == "no-execution", closed
    assert closed["reason_code"] == "AUTOMATION_NOT_MATERIALIZABLE" and closed["completion"] == "PARTIAL"
    assert closed["verification"] == "NOT_APPLICABLE" and closed["accepted"] is False and code != 0
    assert "chain is invalid" in closed["materialization_refusal"], closed
    assert not (root / "execution-inputs").exists() or not any((root / "execution-inputs").iterdir())

    # the attempt is closed: status is terminal, a repeated finish and exec answer the same result
    assert _status(tmp_path, root, capsys)["attempt_state"] == "TERMINAL"
    code_again, again = _run(capsys, "finish", tmp_path, root)
    assert again["reason_code"] == "AUTOMATION_NOT_MATERIALIZABLE" and again["idempotent"] is True and code_again == code
    _code, after = _run(capsys, "exec", tmp_path, root)
    assert after["status"] == "ok" and after["finalization"] == "complete", after


def test_a_valid_accepted_chain_still_goes_to_exec(tmp_path: Path) -> None:
    from tools.finalize_attempt import automation_next
    from tools.run_pipeline import HostStop, cmd_finish

    root, attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])
    assert automation_next(root, attempt_id)[0] == "ACCEPTED"
    with pytest.raises(HostStop) as stop:
        cmd_finish(SimpleNamespace(project=str(tmp_path), run=root.name))
    assert stop.value.code == "FINISH_FACT_UNDETERMINED" and "exec" in str(stop.value)


def test_a_project_file_on_the_generated_path_is_a_final_refusal_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The project already had a file where the automation writes its test: exec never overwrites it."""
    target = tmp_path / "tests" / "test_products.py"  # the generated path of the fixture automation
    target.parent.mkdir(parents=True)
    target.write_text("def test_mine():\n    pass\n", encoding="utf-8")
    root, _attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])

    _code, refused = _run(capsys, "exec", tmp_path, root)
    assert refused["reason"] == "EXECUTION_NOT_PREPARED" and "finish" in refused["fix"], refused
    _code, closed = _run(capsys, "finish", tmp_path, root)
    assert closed["reason_code"] == "AUTOMATION_NOT_MATERIALIZABLE" and "baseline-absence" in closed["materialization_refusal"], closed
    assert target.read_text(encoding="utf-8") == "def test_mine():\n    pass\n"


# --------------------------------------------------------------------------------------
# the other pairs where both commands refuse: each names the way out
# --------------------------------------------------------------------------------------


def test_a_automation_not_finished_names_the_next_model_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt_id = _automation_chain(tmp_path, [None])  # r1 generated, no review yet
    _code, refused = _run(capsys, "exec", tmp_path, root)
    assert refused["reason"] == "EXECUTION_NOT_PREPARED" and "autotest-reviewer:r1" in refused["fix"], refused
    code, finish = _run(capsys, "finish", tmp_path, root)
    assert code == 2 and finish["reason"] == "FINISH_FACT_UNDETERMINED" and "autotest-reviewer:r1" in finish["fix"], finish


def test_b_an_open_canonical_review_names_how_to_close_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_reviewer_protocol import _new_run

    root, _attempt_id = _new_run(tmp_path, "local-pilot-v1")
    code, finish = _run(capsys, "finish", tmp_path, root)
    assert code == 2 and finish["reason"] == "FINISH_FACT_UNDETERMINED", finish
    assert "--new-run" in finish["fix"] and finish.get("next_run"), finish  # FaT-AB tier C: no closing before the review
    _code, refused = _run(capsys, "exec", tmp_path, root)
    assert refused["status"] == "error" and refused.get("fix"), refused


def test_c_an_accepted_review_without_selection_names_the_selection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _published_reviewer_ledger
    from tests.test_self_review import _run as _self_run

    root, attempt_id = _self_run(tmp_path, "local-pilot-v1", flag=False)
    _published_reviewer_ledger(root, attempt_id, automated_document())
    code, finish = _run(capsys, "finish", tmp_path, root)
    assert code == 2 and finish["reason"] == "FINISH_FACT_UNDETERMINED", finish
    # FaT-AB: a session with a verdict is closed — review-abort refuses it, so the fix does not send there
    assert "effective" in finish["fix"] and "--new-run" in finish["fix"] and "--reason" not in finish["fix"], finish


def test_d_an_interrupted_execution_is_finished_by_exec(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from tests.test_finish import _project
    from tools.pilot_state import derive_state

    project, run_root, attempt_id, _values = _project(tmp_path)

    def killed(*args, **kwargs):
        raise KeyboardInterrupt("the controller died after the start claim")

    monkeypatch.setattr(subprocess, "Popen", killed)
    with pytest.raises(KeyboardInterrupt):
        _run(capsys, "exec", project, run_root)
    monkeypatch.undo()
    assert any(row["event_type"] == "EXECUTION_STARTED" for row in derive_state(run_root)["events"])
    assert not (run_root / "execution-receipts" / f"{attempt_id}.json").exists()

    code, finish = _run(capsys, "finish", project, run_root)
    assert code == 2 and finish["status"] == "error" and "exec" in finish["fix"], finish
    _code, closed = _run(capsys, "exec", project, run_root)
    assert closed["status"] == "ok" and closed["verdict"] == "UNKNOWN" and closed["finalization"] == "complete", closed


def test_e_drift_after_scan_names_both_ways_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Not changed by FaT-RL: exec names the changed files, how to restore them, or a new scan."""
    from tests.test_finish import _project

    project, run_root, _attempt_id, _values = _project(tmp_path)
    source = next(path for path in (project / "src").rglob("*.py"))
    source.write_text(source.read_text(encoding="utf-8") + "\n# changed after scan\n", encoding="utf-8")
    code, refused = _run(capsys, "exec", project, run_root)
    assert code == 2 and refused["status"] == "error", refused
    text = json.dumps(refused, ensure_ascii=False)
    assert "scan" in text and ("вер" in text or "restore" in text), refused
