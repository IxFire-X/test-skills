"""M1: a review answer that breaks the task schema is rejected with the schema diagnostics.

`pipeline_driver._submit_review` computed them behind ``if False`` and always fell back
to one generic REVIEW_ASSESSMENT_INVALID row.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.live_step5 import Replay


def test_invalid_review_answer_gets_schema_diagnostics(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    answer = replay.answer(task)
    del answer["coverage"][0]["evidence"]
    Path(task["output_path"]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    code, rejected = replay.submit(task)
    assert rejected["status"] == "rejected"
    rows = rejected["errors"]
    assert any(row["path"].startswith("/coverage/0") for row in rows), rows
    assert all(row["code"] != "REVIEW_ASSESSMENT_INVALID" for row in rows)


def test_schema_valid_but_unbound_answer_keeps_the_explanation(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    answer = replay.answer(task)
    answer["coverage"] = answer["coverage"][1:]  # schema-valid, but a scope is missing
    Path(task["output_path"]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    code, rejected = replay.submit(task)
    assert rejected["status"] == "rejected"
    assert [row["code"] for row in rejected["errors"]] == ["REVIEW_ASSESSMENT_INVALID"]
