"""H1 B1, B2, B8: the compact driver output, the ``answer`` command of the human gates and the result in ``status``.

The orchestrating CLI session of the host build reads only these short lines; the full task stays in its file
(``task_path``).  A gate is answered by a separate command a person confirms in the CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver

LLM_KEYS = {"action", "run_id", "task_id", "stage", "task_path", "output_path", "input_bytes"}


def _main(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = driver.main(list(args))
    return code, json.loads(capsys.readouterr().out)


def test_a_compact_model_task_names_its_file_and_not_its_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    code, compact = _main(capsys, "next", "--project", str(project), "--run", task["run_id"], "--compact")
    assert code == 3 and compact["action"] == "llm"
    assert LLM_KEYS <= set(compact) <= LLM_KEYS | {"requires_fresh_context"}
    full = json.loads(Path(compact["task_path"]).read_text(encoding="utf-8"))
    assert full["task_id"] == compact["task_id"] and full["inputs"] and compact["input_bytes"] > 0


def test_a_compact_rejection_keeps_the_first_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    Path(task["output_path"]).write_text("{}", encoding="utf-8")
    code, compact = _main(capsys, "submit", "--project", str(project), "--run", task["run_id"], "--task-id", task["task_id"], "--compact")
    assert compact["status"] == "rejected" and 1 <= len(compact["errors"]) <= 5 and compact["errors_total"] >= len(compact["errors"])
    assert all(set(error) <= {"code", "path", "message"} and len(error.get("message", "")) <= 200 for error in compact["errors"])
    assert "inputs" not in compact and "instructions" not in compact


def test_the_context_gate_is_answered_by_the_answer_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1", confirm_context="yes")
    assert task["action"] == "ask_user" and task["task_id"].endswith("ask.confirm-context")
    code, compact = _main(capsys, "next", "--project", str(project), "--run", task["run_id"], "--compact")
    digest = task["confirmation_digest"]
    assert code == 3 and compact["action"] == "ask_user" and "files" not in compact and "documents" not in compact
    assert compact["summary"] == {"documents": len(task["documents"]), "files": len(task["files"]),
                                  "bytes": sum(row["size"] for row in [*task["files"], *task["documents"]])}
    assert compact["listing_path"] == task["listing_path"]
    assert " answer " in compact["answer_command"] and digest in compact["answer_command"] and task["run_id"] in compact["answer_command"]
    code, after = _main(capsys, "answer", "--project", str(project), "--run", task["run_id"], "--task-id", task["task_id"], "--value", digest, "--compact")
    assert code == 3 and after["action"] == "llm", after


def test_answer_refuses_a_task_that_is_not_a_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    code, payload = _main(capsys, "answer", "--project", str(project), "--run", task["run_id"], "--task-id", task["task_id"], "--value", "x")
    assert code == 2 and payload["code"] == "ANSWER_NOT_A_GATE"


def test_status_of_a_finished_run_returns_the_result_of_done(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    done = _drive(project, _start(project, "cases-only-v1"), SavedModel("cases-only-v1"))
    run_root = _run_root(project, done)
    events = len(json.loads(json.dumps(driver.status(project, run_root)))["stages"])
    code, status = _main(capsys, "status", "--project", str(project), "--run", done["run_id"])
    assert code == 0 and status["result"] == done["result"]
    assert len(status["stages"]) == events  # status only reads
    code, compact = _main(capsys, "next", "--project", str(project), "--run", done["run_id"], "--compact")
    assert compact["action"] == "done" and compact["result"]["status"] == done["result"]["status"]
    assert Path(compact["result_path"]).is_file()


def test_status_of_an_open_run_has_no_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    code, status = _main(capsys, "status", "--project", str(project), "--run", task["run_id"])
    assert code == 0 and "result" not in status
