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



def test_required_check_parts_are_issued_one_at_a_time_in_a_batch_run(tmp_path: Path) -> None:
    """Live Petclinic run (2026-10-08): reviewers' required_checks become extra parts that must run in order;
    `next --max-tasks 4` issued two of them at once and the driver failed (DRIVER_FAILURE: review parts must run sequentially)."""
    import re

    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    checks_asked = {"n": 0}

    def answer(task):
        value = clean_compact_answer(part_text(task))
        cases = sorted(set(re.findall(r"\[(TC-[A-Z0-9-]+)\]", part_text(task))))
        if checks_asked["n"] < 2 and len(cases) >= 2:
            value["required_checks"] = [{"case_ids": [cases[0]], "reason": "Сверить ожидание с соседним кейсом."},
                                        {"case_ids": [cases[-1]], "reason": "Сверить данные шага с соседним кейсом."}]
            checks_asked["n"] += 1
        return value

    task = first
    seen_batches = []
    for _ in range(80):
        if task.get("action") == "done" or not str(task.get("stage", task.get("review_key", ""))).startswith(("tc-reviewer", "canonical")) and task.get("action") != "batch":
            break
        tasks = task["tasks"] if task.get("action") == "batch" else [task]
        seen_batches.append([item["part_id"] for item in tasks])
        for item in tasks:
            Path(item["output_path"]).write_text(json.dumps(answer(item), ensure_ascii=False), encoding="utf-8")
            code, submitted = replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", item["task_id"])
            assert submitted.get("action") != "error", submitted
        code, task = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
        assert task.get("action") != "error", task
    assert checks_asked["n"] == 2
    from tools.pilot_state import read_review_plan

    root = replay.project / ".pilot-runs" / run_id
    plan = read_review_plan(root, first["attempt_id"])
    base = {part["part_id"] for part in plan["parts"]}
    extra = {part_id for batch in seen_batches for part_id in batch} - base  # parts added for the required checks
    assert extra, "the required checks became extra parts"
    assert all(len(batch) == 1 for batch in seen_batches if extra & set(batch)), seen_batches



def test_a_part_whose_opening_was_interrupted_is_resumed_not_rewritten(tmp_path: Path, monkeypatch) -> None:
    """Live Petclinic run (2026-10-08): a failed opening left the part's boundary and REVIEW_REQUESTED without a model
    request; every later `next` failed ("review-part-boundary-… already exists with different content")."""
    from tools import pilot_state

    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    original = pilot_state.publish_model_request
    calls = {"n": 0}

    def interrupted(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("interrupted after the boundary and the review request")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "publish_model_request", interrupted)
    code, failed = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "2")
    assert failed.get("action") == "error", failed
    monkeypatch.setattr(pilot_state, "publish_model_request", original)
    code, batch = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "2")
    assert batch.get("action") == "batch", batch
    for task in batch["tasks"]:
        Path(task["output_path"]).write_text(json.dumps(clean_compact_answer(part_text(task)), ensure_ascii=False), encoding="utf-8")
        code, submitted = replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", task["task_id"])
        assert submitted.get("action") != "error" and submitted.get("status") != "rejected", submitted
    replay.override = lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None
    code, done = replay.drive(replay.next(run_id)[1])
    assert done["action"] == "done", done


def test_required_checks_become_one_part_per_case_set_after_the_base_parts(tmp_path: Path) -> None:
    """Live Petclinic run (2026-10-08): 38 base parts registered 58 extra parts — duplicates of one check in other words,
    whole-part re-checks of a text correction, checks asked by check parts.  A new plan merges them."""
    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    from tools.pilot_state import read_review_plan

    target = next(case_id for part in read_review_plan(replay.project / ".pilot-runs" / run_id, first["attempt_id"])["parts"] for case_id in part["case_ids"])
    issued: list[str] = []

    def answer(task):
        value = clean_compact_answer(part_text(task))
        value["required_checks"] = [{"case_ids": [target], "reason": f"Сверить ожидание (часть {task['part_id']})."}]
        return value

    task = first
    for _ in range(80):
        if task.get("action") != "llm" and task.get("action") != "batch":
            break
        tasks = task["tasks"] if task.get("action") == "batch" else [task]
        if not all(str(item.get("stage", "")).startswith("tc-reviewer:") for item in tasks):
            break
        for item in tasks:
            issued.append(item["part_id"])
            Path(item["output_path"]).write_text(json.dumps(answer(item), ensure_ascii=False), encoding="utf-8")
            code, submitted = replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", item["task_id"])
            assert submitted.get("action") != "error" and submitted.get("status") != "rejected", submitted
        code, task = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
        assert task.get("action") != "error", task
    plan = read_review_plan(replay.project / ".pilot-runs" / run_id, first["attempt_id"])
    base = [part["part_id"] for part in plan["parts"]]
    extra = sorted(set(issued) - set(base))
    assert len(base) >= 4 and len(extra) == 1, (base, extra)  # one merged check; its own required check adds nothing
    assert issued.index(extra[0]) > max(issued.index(part_id) for part_id in base)  # added after every base part


def _finished_review(tmp_path: Path):
    """A compact case review of 2c10d733 answered clean to the end: the run root, attempt, plan, payload and results."""
    from tools.pilot_state import _review_plan_with_state, _review_results, _review_snapshot_payload, derive_state

    replay = Replay("2c10d733", tmp_path)
    replay.override = lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-to-autotest"))
    root = replay.project / ".pilot-runs" / first["run_id"]
    state = derive_state(root)
    attempt_id = state["attempts"][-1]["attempt_id"]
    plan = _review_plan_with_state(root, state, attempt_id, "canonical")
    return plan, _review_snapshot_payload(root, attempt_id, "canonical"), _review_results(root, state, attempt_id, "canonical", plan)


def test_a_correction_is_rechecked_by_its_level_and_only_on_its_cases(tmp_path: Path) -> None:
    """Review scale fix (2026-10-08): a rewording the answer ranks INFO is only journalled; a WARNING correction is
    re-checked on its own case, not on the whole part; checks naming the same cases are one check."""
    from tools import review_compact

    plan, payload, results = _finished_review(tmp_path)
    assert plan.get("check_policy") == review_compact.CHECK_POLICY
    document = payload["document"]
    part = next(part for part in plan["parts"] if len(part["case_ids"]) >= 3)
    result = dict(next(row for row in results if row["part_id"] == part["part_id"]))
    cases = {case["case_id"]: case for case in document["test_cases"]}
    first, second, third = part["case_ids"][:3]
    step_one, step_two = cases[first]["steps"][0], cases[second]["steps"][0]
    result["corrections"] = [
        {"target_id": step_one["step_id"], "field": "action", "before": step_one["action"], "after": step_one["action"] + " (уточнено)", "why": "Смысл шага."},
        {"target_id": step_two["step_id"], "field": "action", "before": step_two["action"], "after": step_two["action"] + ".", "why": "Пунктуация."},
    ]
    result["findings"] = [{"severity": "WARNING", "code": "ACTION_MEANING", "related_ids": [step_one["step_id"]], "message": "Действие неточно."},
                          {"severity": "INFO", "code": "TEXT_GRAMMAR", "related_ids": [step_two["step_id"]], "message": "Пунктуация."}]
    result["required_checks"] = [{"case_ids": [third], "reason": "Сверить ожидание."}, {"case_ids": [third], "reason": "Сверить данные шага."}]
    others = [row for row in results if row["part_id"] != part["part_id"]]
    assert review_compact.validate_answer(plan, part, result, document) == []
    checks = review_compact.required_checks(plan, payload, [*others, result])
    # One packed check: the WARNING correction on its own case and the two checks of the third case merged.
    assert len(checks) == 1 and set(checks[0]["case_ids"]) == {first, third}, checks
    assert "Сверить ожидание." in checks[0]["reason"] and "Сверить данные шага." in checks[0]["reason"]
    assert [item["target_id"] for item in checks[0]["corrections"]] == [step_one["step_id"]]
    assert len(checks[0]["sources"]) == 2
    # Nothing is added while a base part is still unanswered.
    assert review_compact.required_checks(plan, payload, others) == []


def _unpacked(monkeypatch) -> None:
    """Every merged check its own part: the limit and the batch are tested apart from the packing."""
    from tools import review_compact

    original = review_compact._packed_checks
    monkeypatch.setattr(review_compact, "_packed_checks", lambda plan, payload, rows: [check for row in rows for check in original(plan, payload, [row])])


def test_additional_parts_are_batched_and_limited(tmp_path: Path, monkeypatch) -> None:
    """Review scale fix (2026-10-08): additional parts are independent (a batch) and at most a quarter of the base parts;
    the checks beyond the limit stay unchecked with REVIEW_CHECK_LIMIT and the review is incomplete."""
    from tools import review_compact
    from tools.pilot_state import read_review_aggregate, read_review_plan

    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    root = replay.project / ".pilot-runs" / run_id
    plan = read_review_plan(root, first["attempt_id"])
    targets = list(dict.fromkeys(case_id for part in plan["parts"] for case_id in part["case_ids"]))[:3]
    limit = review_compact.check_limit(plan)
    assert len(targets) == 3 and limit < 3  # the small plan allows fewer additions than asked
    _unpacked(monkeypatch)

    def answer(task):
        value = clean_compact_answer(part_text(task))
        value["required_checks"] = [{"case_ids": [case_id], "reason": f"Сверить {case_id}."} for case_id in targets]
        return value

    task, batches = first, []
    for _ in range(40):
        tasks = task["tasks"] if task.get("action") == "batch" else [task] if task.get("action") == "llm" else []
        if not tasks or not all(str(item.get("stage", "")).startswith("tc-reviewer:") for item in tasks):
            break
        batches.append([item["part_id"] for item in tasks])
        for item in tasks:
            Path(item["output_path"]).write_text(json.dumps(answer(item), ensure_ascii=False), encoding="utf-8")
            replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", item["task_id"])
        code, task = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
        assert task.get("action") != "error", task
    base = {part["part_id"] for part in plan["parts"]}
    extra = sorted({part_id for batch in batches for part_id in batch} - base)
    assert len(extra) == limit
    aggregate = read_review_aggregate(root, first["attempt_id"])["aggregate"]
    limited = [row for row in aggregate["unchecked"] if row["reason"].startswith(review_compact.CHECK_LIMIT_REASON)]
    assert len(limited) == 3 - limit and not aggregate["complete"]
    assert task.get("action") == "done" and task["result"]["reason_code"] == "REVIEW_INCOMPLETE", task.get("result")


def test_additional_parts_go_out_in_one_batch(tmp_path: Path, monkeypatch) -> None:
    from tools import review_compact
    from tools.pilot_state import read_review_plan

    monkeypatch.setattr(review_compact, "check_limit", lambda plan: 3)
    _unpacked(monkeypatch)
    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    plan = read_review_plan(replay.project / ".pilot-runs" / run_id, first["attempt_id"])
    targets = list(dict.fromkeys(case_id for part in plan["parts"] for case_id in part["case_ids"]))[:3]
    base = {part["part_id"] for part in plan["parts"]}

    def answer(task):
        value = clean_compact_answer(part_text(task))
        if task["part_id"] in base:
            value["required_checks"] = [{"case_ids": [case_id], "reason": f"Сверить {case_id}."} for case_id in targets]
        return value

    task, batches = first, []
    for _ in range(40):
        tasks = task["tasks"] if task.get("action") == "batch" else [task] if task.get("action") == "llm" else []
        if not tasks or not all(str(item.get("stage", "")).startswith("tc-reviewer:") for item in tasks):
            break
        batches.append([item["part_id"] for item in tasks])
        for item in tasks:
            Path(item["output_path"]).write_text(json.dumps(answer(item), ensure_ascii=False), encoding="utf-8")
            replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", item["task_id"])
        code, task = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
        assert task.get("action") != "error", task
    extra_batches = [batch for batch in batches if set(batch) - base]
    assert any(len(set(batch) - base) == 3 for batch in extra_batches), batches  # the three additions in one batch


def test_small_checks_are_packed_into_one_part_that_closes_the_unchecked_area(tmp_path: Path) -> None:
    """Review scale fix (2026-10-08): on the live Petclinic answers the merged checks were still 21 parts of one or a few
    cases; they are packed while a part fits its budget (2 parts there), and an area a base part left UNCHECKED for its
    own check is closed by the packed part that answers that check."""
    from tools import review_compact
    from tools.pilot_state import read_review_aggregate, read_review_plan

    replay = Replay("2c10d733", tmp_path)
    code, first = replay.drive(replay.start_with(*SMALL)[1], until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_id = first["run_id"]
    root = replay.project / ".pilot-runs" / run_id
    plan = read_review_plan(root, first["attempt_id"])
    base = {part["part_id"] for part in plan["parts"]}
    targets = list(dict.fromkeys(case_id for part in plan["parts"] for case_id in part["case_ids"]))[:3]
    left_open = []

    def answer(task):
        value = clean_compact_answer(part_text(task))
        if task["part_id"] in base:
            value["required_checks"] = [{"case_ids": [case_id], "reason": f"Сверить {case_id}."} for case_id in targets]
            if not left_open:
                value["coverage"][0]["status"] = "UNCHECKED"
                value["coverage"][0]["note"] = "Нужна сверка с соседними кейсами."
                left_open.append(value["coverage"][0]["area_id"])
        return value

    task, batches = first, []
    for _ in range(40):
        tasks = task["tasks"] if task.get("action") == "batch" else [task] if task.get("action") == "llm" else []
        if not tasks or not all(str(item.get("stage", "")).startswith("tc-reviewer:") for item in tasks):
            break
        batches.append([item["part_id"] for item in tasks])
        for item in tasks:
            Path(item["output_path"]).write_text(json.dumps(answer(item), ensure_ascii=False), encoding="utf-8")
            replay.call("submit", "--project", str(replay.project), "--run", run_id, "--task-id", item["task_id"])
        code, task = replay.call("next", "--project", str(replay.project), "--run", run_id, "--max-tasks", "4")
        assert task.get("action") != "error", task
    extra = {part_id for batch in batches for part_id in batch} - base
    assert len(extra) == 1, batches
    aggregate = read_review_aggregate(root, first["attempt_id"])["aggregate"]
    assert left_open and aggregate["complete"] and aggregate["unchecked"] == [], aggregate["unchecked"]
    assert task.get("action") == "done" and (task["result"]["completion"], task["result"]["coverage"]) == ("COMPLETE", "FULL"), task.get("result")


def test_a_check_larger_than_a_part_is_split_into_parts_that_fit(tmp_path: Path) -> None:
    """Live Petclinic run (2026-10-08, restart): a request to check every source requirement named all 81 cases;
    packed with the overlapping checks it became one 577 KB part over the 200 KB budget — blocked, the run PARTIAL."""
    from tools import review_compact

    plan, payload, _results = _finished_review(tmp_path)
    cases = [case["case_id"] for case in payload["document"]["test_cases"]]
    whole = review_compact.check_part(plan, payload, {"case_ids": cases, "reason": "Сверить все кейсы."}, 99)
    small = dict(plan, input_byte_budget=whole["input_byte_count"] + plan["snapshot"]["response_reserve_bytes"] - 1)
    every, one = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    checks = review_compact._packed_checks(small, payload, [
        (cases, {"reasons": ["Сверить все кейсы."], "corrections": [], "sources": [every]}),
        ([cases[0]], {"reasons": ["Сверить первый кейс."], "corrections": [], "sources": [one]}),
    ])
    assert len(checks) >= 2, checks
    assert all(review_compact.check_part(small, payload, check, 99)["blocked_reason"] is None for check in checks)
    assert set().union(*(set(check["case_ids"]) for check in checks)) == set(cases)
    assert sum(every in check["sources"] for check in checks) >= 2 and sum(one in check["sources"] for check in checks) == 1


def test_a_source_continuation_area_names_what_its_part_holds() -> None:
    """Live Petclinic run (2026-10-08, restart): part 2 held only capabilities and project files, yet its source area
    asked for "every condition of the source requirements"; the reviewer found no source text, left the area UNCHECKED
    and asked to check every SREQ — 81 cases."""
    from tools.review_compact import _area_line

    first = _area_line({"area_id": "source-000001", "kind": "source", "targets": ["SRC-1", "SREQ-0001", "SREQ→CREQ"]})
    rest = _area_line({"area_id": "source-000002", "kind": "source", "targets": ["CAP-PETCLINIC-X", "CTX-1"]})
    assert "каждое условие исходных требований" in first
    assert "каждое условие исходных требований" not in rest and "возможности" in rest and "SRC" in rest


def test_a_check_naming_requirements_carries_their_source_requirements(tmp_path: Path) -> None:
    """Live Petclinic run (2026-10-08, restart): check parts asked to compare cases with source requirements held no
    SREQ text; two of them came back UNCHECKED and the review stayed incomplete."""
    from tools import review_compact

    plan, payload, results = _finished_review(tmp_path)
    document = payload["document"]
    mapping = document["source_to_canonical_mappings"][0]
    sreq, creq = mapping["source_requirement_id"], mapping["canonical_requirement_ids"][0]
    base = {part["part_id"] for part in plan["parts"]}
    first = dict(next(row for row in results if row["part_id"] in base))
    first["required_checks"] = [{"requirement_ids": [sreq], "reason": "Сверить кейсы с исходным требованием."},
                                {"requirement_ids": [creq], "reason": "Сверить кейсы требования с исходником."}]
    others = [row for row in results if row["part_id"] != first["part_id"]]
    checks = review_compact.required_checks(plan, payload, [*others, first])
    assert checks and all(sreq in check.get("source_requirement_ids", []) for check in checks), checks
    text = review_compact.check_part(plan, payload, checks[0], len(plan["parts"]) + 1)["text"]
    assert "## SREQ" in text and f"[{sreq}]" in text, text[:2000]
