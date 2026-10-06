"""D5: a missing `.skillsrc` becomes an `ask_user` task and the same run continues.

Live step5 runs: the driver stopped on `.skillsrc` without a question and the
stopped run could not be continued, so the orchestrator answered
``{"module:root:test.framework": "junit5"}`` by hand outside the driver.
"""
from __future__ import annotations

from pathlib import Path

from tests.live_step5 import Replay


def _without_skillsrc(tmp_path: Path) -> Replay:
    replay = Replay("2c10d733", tmp_path)
    (replay.project / ".skillsrc").unlink()
    return replay


def test_skillsrc_question_is_asked_and_the_same_run_continues(tmp_path: Path) -> None:
    replay = _without_skillsrc(tmp_path)
    code, task = replay.start()
    assert (code, task["action"]) == (3, "ask_user"), task
    assert "test.framework" in task["question"] and [option["value"] for option in task["options"]] == ["junit5"]
    run_id = task["run_id"]
    # Asking again returns the same open question.
    code, again = replay.next(run_id)
    assert again["task_id"] == task["task_id"]
    # An answer outside the options is refused without touching the run.
    code, refused = replay.submit(task, "--answer", "testng")
    assert (code, refused["code"]) == (2, "DRIVER_INPUT")
    code, following = replay.submit(task, "--answer", "junit5")
    assert following["action"] == "llm" and following["stage"] == "context-marker:baseline" and following["run_id"] == run_id
    assert (replay.project / ".skillsrc").is_file()
    code, done = replay.drive(following)
    assert done["action"] == "done" and done["run_id"] == run_id and done["result"]["status"] == "terminal", done
    assert {row["payload"].get("run_id") for row in replay.log if row["payload"].get("run_id")} == {run_id}


def test_answered_skillsrc_submit_is_idempotent(tmp_path: Path) -> None:
    replay = _without_skillsrc(tmp_path)
    code, task = replay.start()
    code, following = replay.submit(task, "--answer", "junit5")
    code, repeated = replay.submit(task, "--answer", "junit5")
    assert repeated["task_id"] == following["task_id"]
