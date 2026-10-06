"""R3.2, Р12: batches of independent compact parts.

``next --max-tasks K`` returns up to K open part tasks at once; their answers are
submitted by separate processes in a random order, some twice, and one process dies
between writing the answer artifact and journalling its event.  Afterwards the journal
chain is intact, every part has exactly one recorded answer and the review completes.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

from tests.live_step5 import Replay
from tests.review_scaling_helpers import clean_compact_answer, part_text

ROOT = Path(__file__).resolve().parents[1]
SMALL = ("--review-mode", "compact-v1", "--review-input-bytes", "34000", "--review-reserve-bytes", "3000")

CRASH = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from tools import pilot_state, pipeline_driver
original = pilot_state._append_event_locked
def crashing(project, root, event_type, **kwargs):
    if event_type == "MODEL_RESPONSE_RECEIVED":
        os._exit(17)  # the answer artifact is on disk, its event is not
    return original(project, root, event_type, **kwargs)
pilot_state._append_event_locked = crashing
pipeline_driver.main(sys.argv[2:])
"""


def _submit(project: Path, run_id: str, task_id: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "tools.pipeline_driver", "submit", "--project", str(project), "--run", run_id, "--task-id", task_id],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _review_events(root: Path, attempt_id: str) -> dict[str, list[dict]]:
    from tools.pilot_state import derive_state

    rows: dict[str, list[dict]] = {}
    for event in derive_state(root)["events"]:  # derive_state verifies the whole digest chain
        if event.get("attempt_id") == attempt_id and str(event.get("stage_instance_id", "")).startswith("tc-reviewer:"):
            rows.setdefault(event["stage_instance_id"], []).append(event)
    return rows


def test_parallel_submits_of_a_batch_record_each_part_once(tmp_path: Path) -> None:
    from tools.pilot_state import read_review_aggregate, read_review_plan

    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id, attempt = first["run_id"], first["attempt_id"]
    root = replay.project / ".pilot-runs" / run_id
    plan = read_review_plan(root, attempt)
    assert len(plan["parts"]) >= 4, len(plan["parts"])

    code, batch = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
    assert code == 3 and batch["action"] == "batch" and len(batch["tasks"]) == 4
    tasks = batch["tasks"]
    assert len({task["part_id"] for task in tasks}) == 4 and tasks[0]["task_id"] == first["task_id"]
    log = [json.loads(line) for line in (root.parent / f"{run_id}.driver" / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "batch_issued" and len(row["task_ids"]) == 4 for row in log)

    for task in tasks:
        Path(task["output_path"]).write_text(json.dumps(clean_compact_answer(part_text(task)), ensure_ascii=False), encoding="utf-8")

    # One process dies after writing its answer artifact and before the event.
    crashed = subprocess.run([sys.executable, "-c", CRASH, str(ROOT), "submit", "--project", str(replay.project), "--run", run_id,
                              "--task-id", tasks[1]["task_id"]], cwd=ROOT, capture_output=True)
    assert crashed.returncode == 17
    assert "MODEL_RESPONSE_RECEIVED" not in {event["event_type"] for event in _review_events(root, attempt).get(f"tc-reviewer:canonical:{tasks[1]['part_id']}", [])}

    # The rest are submitted in parallel, in a random order, two of them twice.
    order = [*tasks, tasks[0], tasks[3]]
    random.Random(7).shuffle(order)
    processes = [_submit(replay.project, run_id, task["task_id"]) for task in order]
    outputs = [process.communicate(timeout=600) for process in processes]
    for process, (stdout, stderr) in zip(processes, outputs):
        payload = json.loads(stdout.decode("utf-8"))
        assert process.returncode in {0, 3} and payload.get("action") != "error" and payload.get("status") != "rejected", (payload, stderr[-2000:])

    events = _review_events(root, attempt)
    for task in tasks:
        stage = f"tc-reviewer:canonical:{task['part_id']}"
        kinds = [event["event_type"] for event in events[stage]]
        assert kinds.count("MODEL_REQUESTED") == 1 and kinds.count("MODEL_RESPONSE_RECEIVED") == 1, (stage, kinds)

    replay.override = lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None
    code, done = replay.drive(replay.next(run_id)[1])
    assert done["action"] == "done", done
    aggregate = read_review_aggregate(root, attempt)["aggregate"]
    assert aggregate["complete"] and len(aggregate["parts"]) == len(plan["parts"])


def test_a_legacy_plan_stays_sequential_with_max_tasks(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    code, again = replay.call("next", "--project", str(replay.project), "--run", first["run_id"], "--max-tasks", "4")
    assert again["action"] == "llm" and again["task_id"] == first["task_id"]
