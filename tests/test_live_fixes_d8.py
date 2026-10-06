"""D8: review without host isolation no longer stops the run.

Before: answering `none` to `reviewer-isolation` stopped with
REVIEWER_ISOLATION_UNAVAILABLE, while a host that answered `fresh` went on unchecked
(A2), so lying paid.  Now `none` reviews the same parts in the same format, marks the
result `review_independence: SELF` and does not accept it (REVIEW_NOT_INDEPENDENT)
unless the run was started with `--accept-self-review`.
"""
from __future__ import annotations

from pathlib import Path

from tests.live_step5 import Replay
from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver
from tools.pilot_state import read_terminal_result


def _terminal(project: Path, done: dict) -> dict:
    return dict(read_terminal_result(project / ".pilot-runs" / done["run_id"], done["result"]["attempt_id"]))


def test_fresh_review_keeps_its_behavior_and_reports_isolated(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1"), SavedModel("local-pilot-v1"))
    result = done["result"]
    assert (result["verification"], result["accepted"], result["exit_code"], result["reason_code"]) == ("PASS", True, 0, None)
    assert result["review_independence"] == "ISOLATED" and not result.get("warnings")
    terminal = _terminal(project, done)
    assert terminal["review_independence"] == "ISOLATED" and terminal["accepted"] is True


def test_self_review_runs_the_whole_route_and_is_not_accepted(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    done = _drive(project, _start(project, "local-pilot-v1", reviewer_isolation="none"), model)
    assert done["action"] == "done"
    result = done["result"]
    assert [stage.split(":")[0] for stage in model.stages] == ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer"]
    assert (result["completion"], result["verification"], result["accepted"], result["reason_code"]) == ("COMPLETE", "PASS", False, "REVIEW_NOT_INDEPENDENT")
    assert result["review_independence"] == "SELF" and result["exit_code"] == 1
    assert (project / "tests" / "test_generated.py").is_file()
    terminal = _terminal(project, done)
    assert (terminal["review_independence"], terminal["evidence"]["review_independence"], terminal["evidence"]["self_review_accepted"]) == ("SELF", "SELF", False)


def test_accept_self_review_flag_accepts_and_keeps_the_mark(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1", reviewer_isolation="none", accept_self_review=True), SavedModel("local-pilot-v1"))
    result = done["result"]
    assert (result["verification"], result["accepted"], result["reason_code"], result["exit_code"]) == ("PASS", True, None, 0)
    assert result["review_independence"] == "SELF"
    terminal = _terminal(project, done)
    assert terminal["review_independence"] == "SELF" and terminal["evidence"]["self_review_accepted"] is True


def test_isolation_question_names_subagents_and_separate_cli_processes(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    question = _drive(project, _start(project, "cases-only-v1", reviewer_isolation=None), SavedModel("cases-only-v1"),
                      until=lambda task: task["action"] == "ask_user")
    assert "субагентом или новым процессом CLI" in question["question"] and "claude -p" in question["question"]
    following = driver.submit(project, _run_root(project, question), question["task_id"], answer="none")
    assert following["action"] == "llm" and following["stage"].startswith("tc-reviewer:")


def test_cases_only_self_review_publishes_cases_with_review_not_independent(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.start_with("--reviewer-isolation", "none")
    code, done = replay.drive(task)
    result = done["result"]
    assert (result["completion"], result["accepted"], result["reason_code"], result["review_independence"]) == ("COMPLETE", False, "REVIEW_NOT_INDEPENDENT", "SELF")
    assert "candidate_bundle" in result["paths"]


def test_self_review_warns_when_review_inputs_exceed_one_context_window(tmp_path: Path) -> None:
    replay = Replay("9340016c", tmp_path)
    code, task = replay.start_with("--reviewer-isolation", "none")
    code, canonical = replay.drive(task, until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    assert not canonical.get("warnings"), canonical.get("warnings")  # ~390 KB fits the default window
    code, automation = replay.drive(canonical, until=lambda task: str(task.get("stage", "")).startswith("autotest-reviewer:"))
    [warning] = automation["warnings"]
    assert warning["code"] == "SELF_REVIEW_CONTEXT_OVERFLOW" and warning["review_input_bytes"] > warning["context_bytes"] == 500_000
    assert "отдельн" in warning["message"]
    log = (replay.run_root(automation).with_name(automation["run_id"] + ".driver") / "driver-log.jsonl").read_text(encoding="utf-8")
    assert "SELF_REVIEW_CONTEXT_OVERFLOW" in log


def test_context_window_threshold_is_a_run_parameter(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.start_with("--reviewer-isolation", "none", "--review-context-bytes", "100000")
    code, review = replay.drive(task, until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    [warning] = review["warnings"]
    assert warning["context_bytes"] == 100_000 and warning["review_input_bytes"] > 100_000
    # With isolation there is no warning: each part is its own context.
    fresh = Replay("2c10d733", tmp_path / "fresh")
    code, task = fresh.start_with("--review-context-bytes", "100000")
    code, review = fresh.drive(task, until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    assert not review.get("warnings")


def test_driver_summary_matches_its_schema(tmp_path: Path) -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1", reviewer_isolation="none"), SavedModel("local-pilot-v1"))
    assert schema_diagnostics(done["result"], root / "schemas" / "driver-summary.schema.json", root) == []
    replay = Replay("2c10d733", tmp_path / "cases")
    code, task = replay.start_with("--reviewer-isolation", "none", "--review-context-bytes", "100000")
    code, done = replay.drive(task)
    assert done["result"]["warnings"] and schema_diagnostics(done["result"], root / "schemas" / "driver-summary.schema.json", root) == []


def test_status_report_shows_review_independence(tmp_path: Path) -> None:
    from tools import run_pipeline

    project = _project(tmp_path, local=False)
    done = _drive(project, _start(project, "cases-only-v1", reviewer_isolation="none"), SavedModel("cases-only-v1"))
    payload = run_pipeline._durable_status(project, project / ".pilot-runs" / done["run_id"], stage="status")
    assert payload["review_independence"] == "SELF" and payload["stop_reason"] == "REVIEW_NOT_INDEPENDENT"
