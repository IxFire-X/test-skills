"""fix1: a file of the tests that cannot be written in ``materialize`` closes the attempt by the command, not by a new run."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_exec_prepares import _project
from tests.test_p4_ingest import _args
from tools import exec_commands, generated_delta


def _say(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _broken_write(*_args, **_kwargs):
    raise OSError("disk is full")


def test_a_test_file_that_cannot_be_written_closes_the_attempt_as_not_fully_written(tmp_path, capsys, monkeypatch) -> None:
    from tools.run_pipeline import cmd_finish

    project, run_root, attempt_id = _project(tmp_path)
    monkeypatch.setattr(generated_delta, "create_confined_bytes_exclusive", _broken_write)
    code = exec_commands.cmd_materialize(_args(project, run_root))
    said = _say(capsys)
    assert code == 2 and said["reason"] == "MATERIALIZATION_INCOMPLETE" and said["attempt_id"] == attempt_id, said
    assert "tests/test_generated.py" in said["message"] and "disk is full" not in said["message"] and said["fix"], said
    assert not (project / "tests" / "test_generated.py").exists()  # nothing of the package is left in the project
    exec_commands.cmd_materialize(_args(project, run_root))  # the attempt is closed: the repeat gives its result, it does not fail again
    repeated = _say(capsys)
    assert repeated["status"] == "ok" and repeated["accepted"] is False and repeated["verdict"] == "NOT_APPLICABLE", repeated
    assert cmd_finish(SimpleNamespace(project=str(project), run=run_root.name)) == 1  # not accepted, a determined result: the exit code 1
    result = _say(capsys)
    assert result.get("completion") == "PARTIAL" and result.get("reason_code") == "MATERIALIZATION_INCOMPLETE" and result.get("accepted") is False, result


def test_a_link_on_the_way_to_the_reports_is_a_refusal_with_the_attempt_and_a_way_out(tmp_path, capsys, monkeypatch) -> None:
    project, run_root, attempt_id = _project(tmp_path)
    (project / "test-results").mkdir(exist_ok=True)
    monkeypatch.setattr(exec_commands, "_link", lambda path: path.name == "test-results")  # a junction on the way: the tests are already written
    code = exec_commands.cmd_materialize(_args(project, run_root))
    said = _say(capsys)
    assert code == 2 and said["reason"] == "INGEST_REPORT_OUTSIDE_MODULE" and said["attempt_id"] == attempt_id and said["fix"], said


def test_finish_closes_an_attempt_whose_tests_were_written_in_part_when_materialize_was_cut_before_closing_it(tmp_path, capsys, monkeypatch) -> None:
    from tools.run_pipeline import cmd_finish

    project, run_root, _attempt_id = _project(tmp_path)
    monkeypatch.setattr(generated_delta, "create_confined_bytes_exclusive", _broken_write)
    monkeypatch.setattr(exec_commands, "_close_incomplete_materialization", lambda *_args: 2)  # cut here: the delta is published, the attempt is open
    assert exec_commands.cmd_materialize(_args(project, run_root)) == 2
    capsys.readouterr()
    cmd_finish(SimpleNamespace(project=str(project), run=run_root.name))
    result = _say(capsys)
    assert (result.get("completion"), result.get("reason_code"), result.get("accepted")) == ("PARTIAL", "MATERIALIZATION_INCOMPLETE", False), result


def test_a_closure_that_fails_in_materialize_is_an_answer_with_the_attempt_and_finish_as_the_way_out(tmp_path, capsys, monkeypatch) -> None:
    from tools import finalize_attempt

    project, run_root, attempt_id = _project(tmp_path)
    monkeypatch.setattr(generated_delta, "create_confined_bytes_exclusive", _broken_write)

    def broken(*_args, **_kwargs):
        raise OSError("the file is held")

    monkeypatch.setattr(finalize_attempt, "finalize_durable_branch_attempt", broken)
    code = exec_commands.cmd_materialize(_args(project, run_root))
    said = _say(capsys)
    assert code == 2 and said["reason"] == "FINALIZATION" and said["attempt_id"] == attempt_id and "finish" in said["fix"], said
