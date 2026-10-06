"""D6: `.driver/driver-log.jsonl` tells waiting from working.

Live run 9340016c: 74 minutes passed between a review part request and its answer
(the session limit), then 16 more after reconnecting.  The journal records only the
model request and response, so waiting looked exactly like work.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.live_step5 import Replay, load, run_dir
from tools import pipeline_driver as driver


def _log(replay: Replay, run_id: str) -> list[dict[str, Any]]:
    path = replay.project / ".pilot-runs" / f"{run_id}.driver" / "driver-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_live_9340016c_journal_cannot_tell_waiting_from_work() -> None:
    """The gap the log has to explain: one review part answered 74 minutes after its request."""
    from datetime import datetime

    events = load(run_dir("9340016c") / "events.jsonl")
    stamps: dict[str, dict[str, datetime]] = {}
    for event in events:
        if event["event_type"] in {"MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED"} and str(event.get("stage_instance_id", "")).startswith("autotest-reviewer:"):
            stamps.setdefault(event["stage_instance_id"], {})[event["event_type"]] = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
    gaps = [(row["MODEL_RESPONSE_RECEIVED"] - row["MODEL_REQUESTED"]).total_seconds() / 60 for row in stamps.values() if len(row) == 2]
    assert max(gaps) > 70


def test_every_next_and_submit_is_logged_with_task_age(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, done = replay.drive()
    assert done["action"] == "done"
    log = _log(replay, done["run_id"])
    commands = [row for row in log if row["event"] == "command"]
    assert [row["command"] for row in commands] == [row["argv"][0] for row in replay.log]
    assert all(row["duration_ms"] >= 0 and row["result"]["action"] in {"llm", "ask_user", "done"} for row in commands)
    submits = [row for row in commands if row["command"] == "submit"]
    assert submits and all(row["task_id"] and row["task_age_seconds"] >= 0 for row in submits)
    issued = [row for row in log if row["event"] == "task_issued"]
    assert {row["task_id"] for row in issued} == {row["task_id"] for row in submits}
    assert all(row["at"].endswith("Z") for row in log)


def test_reissued_open_task_records_elapsed_time(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.start()
    code, again = replay.next(task["run_id"])
    assert again["task_id"] == task["task_id"]
    [reissued] = [row for row in _log(replay, task["run_id"]) if row["event"] == "task_reissued"]
    assert reissued["task_id"] == task["task_id"] and reissued["since_first_issue_seconds"] >= 0


def test_failed_review_call_is_logged_with_reason(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    replay.submit(task, "--failed", "TRANSPORT", "--reason", "лимит сессии исчерпан")
    [row] = [row for row in _log(replay, task["run_id"]) if row["event"] == "command" and row.get("failed")]
    assert (row["failed"], row["reason"], row["task_id"]) == ("TRANSPORT", "лимит сессии исчерпан", task["task_id"])


def test_driver_exception_is_logged_with_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.start()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("boom")

    monkeypatch.setattr(driver, "_context_marker_task", broken)
    code, error = replay.next(task["run_id"])
    assert (code, error["code"]) == (2, "DRIVER_FAILURE")
    [row] = [row for row in _log(replay, task["run_id"]) if row["event"] == "error"]
    assert row["code"] == "DRIVER_FAILURE" and "boom" in row["message"] and "_context_marker_task" not in row["message"]
    assert "Traceback" in row["traceback"] and "broken" in row["traceback"]
    code, error = replay.call("submit", "--project", str(replay.project), "--run", task["run_id"], "--task-id", "no-such-task")
    rows = [row for row in _log(replay, task["run_id"]) if row["event"] == "error"]
    assert rows[-1]["code"] == "DRIVER_INPUT" and "Traceback" in rows[-1]["traceback"]


def test_log_stays_outside_the_durable_run(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.start()
    root = replay.run_root(task)
    assert not any(path.name.startswith("driver-log") for path in root.rglob("*"))
    assert (root.with_name(root.name + ".driver") / "driver-log.jsonl").is_file()
