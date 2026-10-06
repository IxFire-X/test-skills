"""R3.3: the SELF-review context warning (D8) for compact-v1.

Without isolation one session holds every part input and every answer.  For compact
plans the estimate is the sum of part inputs plus the plan's answer reserve per part;
legacy plans keep the D8 sum of inputs.  step5 ``9340016c`` in compact-v1 (cases and
automation, about 150 KB) stays under the default 500 000-byte window, where the legacy
review (1.45 MB) warned.
"""
from __future__ import annotations

from pathlib import Path

from tests.live_step5 import Replay
from tests.review_scaling_helpers import clean_compact_answer, part_text


def _compact(task):
    return clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None


def _to_automation_review(tmp_path: Path, *extra: str) -> dict:
    replay = Replay("9340016c", tmp_path, override=_compact)
    code, task = replay.drive(replay.start_with("--review-mode", "compact-v1", "--reviewer-isolation", "none", *extra)[1],
                              until=lambda item: str(item.get("stage", "")).startswith("autotest-reviewer:"))
    assert str(task.get("stage", "")).startswith("autotest-reviewer:"), task
    return task


def test_compact_step5_self_review_fits_the_default_window(tmp_path: Path) -> None:
    task = _to_automation_review(tmp_path)
    assert task["warnings"] == []


def test_compact_estimate_counts_inputs_and_answer_reserve(tmp_path: Path) -> None:
    from tools.pilot_state import read_review_plan

    task = _to_automation_review(tmp_path, "--review-context-bytes", "100000")
    [warning] = task["warnings"]
    assert warning["code"] == "SELF_REVIEW_CONTEXT_OVERFLOW"
    root = Path(task["output_path"]).parents[1].with_name(task["run_id"])
    total = 0
    for key in ("canonical", "r1"):
        plan = read_review_plan(root, task["attempt_id"], key)
        total += sum(part["input_byte_count"] for part in plan["parts"]) + len(plan["parts"]) * plan["snapshot"]["response_reserve_bytes"]
    assert warning["review_input_bytes"] == total and total < 200_000
