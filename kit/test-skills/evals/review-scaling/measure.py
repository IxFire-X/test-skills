"""Замеры ревью: части, байты входа и ответа, время (волна review-scaling, 2026-10-07).

    python evals/review-scaling/measure.py [--mode pairs|compact-v1] [--json out.json] [--skip-replay]

* step5 ``9340016c``: проигрывание настоящих ответов текущим драйвером до первой задачи
  ревью автотестов (``tests/live_step5.Replay``), суммы ``input_byte_count`` планов
  ``canonical`` и ``r1``; ответы и время живого прогона — из фикстуры.
* Petclinic: план ревью кейсов для ``docs/examples/petclinic-owner-lifecycle/cases.json``
  офлайн, без модели, тем же построителем, что и драйвер; ревью автотестов — для
  сентябрьского класса ``b7733d39`` из ``evals/review-scaling/data``.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_data  # noqa: E402


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def live_step5() -> dict:
    """Ответы ревьюеров и длительность ревью живого прогона 9340016c (фикстура)."""
    from tests.live_step5 import load, outputs, run_dir

    events = load(run_dir("9340016c") / "events.jsonl")
    result = {}
    for key, prefix in (("canonical", "tc-reviewer:canonical:"), ("r1", "autotest-reviewer:r1:")):
        rows = [event for event in events if str(event.get("stage_instance_id", "")).startswith(prefix)]
        requested = [_time(event["observed_at"]) for event in rows if event["event_type"] == "MODEL_REQUESTED"]
        received = [_time(event["observed_at"]) for event in rows if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
        answers = [value for label, value in outputs("9340016c").items() if label.startswith(f"review.{key}.")]
        sizes = [len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for value in answers]
        per_part = [(b - a).total_seconds() for a, b in zip(sorted(requested), sorted(received))]
        result[key] = {"parts": len(answers), "response_bytes": sum(sizes), "median_response_bytes": statistics.median(sizes) if sizes else 0,
                       "wall_seconds": (max(received) - min(requested)).total_seconds() if received else None,
                       "part_seconds": per_part}
    return result


def replay_step5(mode: str | None) -> dict:
    """Планы ревью step5 на текущем коде: проигрывание до первой задачи ревью автотестов."""
    from tests.live_step5 import Replay
    from tests.review_scaling_helpers import clean_compact_answer, part_text
    from tools.pilot_state import read_review_plan

    def compact(task):  # compact parts get a valid clean answer; recorded legacy answers fit legacy parts only
        return clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None

    with tempfile.TemporaryDirectory() as tmp:
        replay = Replay("9340016c", Path(tmp), override=compact)
        extra = () if mode is None else ("--review-mode", mode)
        code, task = replay.drive(replay.start_with(*extra)[1], until=lambda item: str(item.get("stage", "")).startswith("autotest-reviewer:"))
        run_root = replay.project / ".pilot-runs" / str(task["run_id"])
        attempt = task["attempt_id"]
        result = {}
        for key in ("canonical", "r1"):
            plan = read_review_plan(run_root, attempt, key)
            result[key] = plan_stats(plan)
        result["canonical_tasks"] = len(replay.tasks("tc-reviewer:"))
        return result


def plan_stats(plan: dict) -> dict:
    parts = plan["parts"]
    areas = sum(len(part.get("scopes") or part.get("areas") or []) for part in parts)
    return {"parts": len(parts), "input_bytes": sum(int(part["input_byte_count"]) for part in parts),
            "blocked": sum(1 for part in parts if part["blocked_reason"]), "scopes": areas,
            "carried": len(plan.get("carried", [])), "max_part_bytes": max(int(part["input_byte_count"]) for part in parts)}


def petclinic(mode: str) -> dict:
    """Офлайн-планы Petclinic тем же построителем, что и у драйвера."""
    from tools.review_modes import build_offline_plan

    result = {}
    snapshot = eval_data.petclinic_case_snapshot()
    result["cases"] = plan_stats(build_offline_plan(snapshot, mode=mode, review_key="canonical"))
    automation = eval_data.petclinic_automation_snapshot()
    if automation is not None:
        result["automation"] = plan_stats(build_offline_plan(automation, mode=mode, review_key="r1"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="pairs")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args(argv)
    report = {"mode": args.mode, "live_step5_9340016c": live_step5(), "petclinic": petclinic(args.mode)}
    if not args.skip_replay:
        report["step5_replay"] = replay_step5(None if args.mode == "pairs" else args.mode)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
