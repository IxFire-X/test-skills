"""R1.4–R1.5, Р13: what a compact review re-checks and what it carries.

* a behavioral correction is re-checked only in the parts that hold the corrected case;
  a ``management.*`` correction is not re-checked at all (D3);
* a rework r2 carries a case area when the fingerprint of its projection is the same,
  r1 checked it and no BLOCKING finding names the case; a fully carried part is not
  issued, a partly carried one is issued with its whole context and only its new areas;
* the carry key holds the area input, role policy, SKILL and instruction digests, model
  and mode: changing any of them cancels the carry; every carried area names its source.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import pytest

from tests.live_step5 import Replay
from tests.review_scaling_helpers import clean_compact_answer, part_text, step5_snapshot, synthetic_document, synthetic_snapshot
from tools import review_compact
from tools.review_modes import build_offline_plan

PARENT = "0" * 31 + "1"


def _carry(parent_plan: dict, *, checked: list[str] | None = None, findings: list | None = None, policy: Any = "same") -> dict:
    areas = [area["area_id"] for part in parent_plan["parts"] for area in part["areas"]]
    parent_policy = {"mode": "compact-v1", "role_policy": "canonical-reviewer-v2", "skill_digest": "sha256:" + "a" * 64,
                     "instructions_digest": "sha256:" + "b" * 64, "model_id": "claude-fable-5-1"}
    child_policy = parent_policy if policy == "same" else {**parent_policy, **policy}
    return {"plan": parent_plan, "aggregate": {"checked_scope_ids": areas if checked is None else checked, "findings": findings or []},
            "from_attempt_id": PARENT, "policy": child_policy, "parent_policy": parent_policy}


def _plan(snapshot: dict, carry: dict | None = None, budget: int = 200_000) -> dict:
    payload = review_compact.compact_payload(snapshot, {"mode": "compact-v1", "role_policy": "canonical-reviewer-v2", "skill_digest": "sha256:" + "a" * 64,
                                                        "instructions_digest": "sha256:" + "b" * 64, "model_id": "claude-fable-5-1"})
    from tools.review_parts import document_index, review_digest

    specification = {"review_kind": "tc-reviewer", "revision": 1, "snapshot_digest": review_digest(payload), "instructions": "x",
                     "response_reserve_bytes": 20_000, "document_index": document_index(payload)}
    return review_compact.build_compact_plan(specification, payload, input_byte_budget=budget, carry=carry)


def _answered(plan: dict) -> set[str]:
    return {area["area_id"] for part in plan["parts"] for area in review_compact.answer_areas(part)}


def test_a_changed_byte_of_one_case_resends_only_its_areas() -> None:
    snapshot = step5_snapshot()
    parent = _plan(snapshot)
    child = copy.deepcopy(snapshot)
    child["document"]["test_cases"][5]["steps"][0]["test_data"] += "."
    plan = _plan(child, _carry(parent))
    case = child["document"]["test_cases"][5]["case_id"]
    cross = [area["area_id"] for area in plan["parts"][0]["areas"] if area["kind"] == "cross"]
    assert _answered(plan) == {f"local-{case}", *cross}
    carried = {row["area"]["area_id"] for row in plan["carried"]}
    assert len(carried) == 12 and f"local-{case}" not in carried  # 11 other cases and the source area
    assert all(row["from_attempt_id"] == PARENT and row["from_plan_digest"] == parent["digest"] and row["from_part_id"] == "part-000001"
               for row in plan["carried"])
    # The partly carried part is still sent whole, and says what it does not need to answer.
    text = plan["parts"][0]["text"]
    assert "Перенесены из прошлого ревью" in text and all(f"[{other['case_id']}]" in text for other in child["document"]["test_cases"])


def test_a_blocking_finding_or_an_unchecked_area_is_never_carried() -> None:
    snapshot = step5_snapshot()
    parent = _plan(snapshot)
    child = copy.deepcopy(snapshot)
    child["document"]["test_cases"][5]["steps"][0]["test_data"] += "."
    named = child["document"]["test_cases"][2]["case_id"]
    plan = _plan(child, _carry(parent, findings=[{"severity": "BLOCKING", "related_ids": [named]}]))
    assert f"local-{named}" in _answered(plan)
    areas = [area["area_id"] for part in parent["parts"] for area in part["areas"]]
    plan = _plan(child, _carry(parent, checked=[area for area in areas if area != "local-TC-B1-001"]))
    assert "local-TC-B1-001" in _answered(plan)


@pytest.mark.parametrize("change", [{"skill_digest": "sha256:" + "c" * 64}, {"instructions_digest": "sha256:" + "d" * 64},
                                    {"model_id": "claude-haiku-4-5-20251001"}, {"role_policy": "canonical-reviewer-v3"}])
def test_a_policy_change_cancels_the_whole_carry(change: dict) -> None:
    snapshot = step5_snapshot()
    parent = _plan(snapshot)
    child = copy.deepcopy(snapshot)
    child["document"]["test_cases"][5]["steps"][0]["test_data"] += "."
    plan = _plan(child, _carry(parent, policy=change))
    assert "carried" not in plan and _answered(plan) == {area["area_id"] for part in parent["parts"] for area in part["areas"]}


def test_a_fully_carried_part_is_not_issued() -> None:
    sizes = [3000] * 60
    document = synthetic_document(sizes, seed=3)
    parent = _plan(synthetic_snapshot(document), budget=60_000)
    assert len(parent["parts"]) > 3
    child = copy.deepcopy(document)
    child["test_cases"][0]["steps"][0]["action"] += "!"
    plan = _plan(synthetic_snapshot(child), _carry(parent), budget=60_000)
    changed = child["test_cases"][0]["case_id"]
    assert len(plan["parts"]) < len(parent["parts"])
    assert all(changed in part["case_ids"] for part in plan["parts"])
    assert f"local-{changed}" in _answered(plan)


def test_the_policy_reflects_skill_instructions_model_and_mode() -> None:
    policy = review_compact.review_policy("tc-reviewer", instructions="x", model_id="m")
    assert set(policy) == {"mode", "role_policy", "skill_digest", "instructions_digest", "model_id"}
    assert policy["skill_digest"] == review_compact.skill_digest("tc-reviewer") != review_compact.skill_digest("autotest-reviewer")
    assert review_compact.review_policy("tc-reviewer", instructions="y", model_id="m")["instructions_digest"] != policy["instructions_digest"]


# --------------------------------------------------------------------------- through the driver


CASE = "TC-B1-006"


class CompactRework:
    def __init__(self) -> None:
        self.first: str | None = None
        self.r2_texts: list[str] = []

    def override(self, task: Mapping[str, Any]) -> Any:
        if str(task.get("stage", "")).startswith("tc-generator:") and task.get("rework"):
            import json

            brief = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))
            case = copy.deepcopy(next(item for item in brief["r1_cases"] if item["case_id"] == CASE))
            case["steps"][0]["expectations"][0]["text"] = "Приложение отклоняет запрос как некорректный.\n\nHTTP 400 Bad Request"
            return {"test_cases": [case]}
        if task.get("review_mode") != "compact-v1":
            return None
        self.first = self.first or task["attempt_id"]
        answer = clean_compact_answer(part_text(task))
        if task["attempt_id"] == self.first:
            answer["findings"] = [{"severity": "BLOCKING", "code": "EXPECTATION_CLAIM_WITHOUT_ASSERTION", "related_ids": [CASE],
                                   "message": "Ожидание «студент не возвращается» ничем не проверяется."}]
        else:
            self.r2_texts.append(part_text(task))
        return answer


def test_compact_rework_r2_reviews_only_the_changed_case(tmp_path: Path) -> None:
    from tools.pilot_state import derive_state, read_review_aggregate, read_review_plan

    scenario = CompactRework()
    replay = Replay("2c10d733", tmp_path, override=scenario.override)
    code, done = replay.drive(replay.start_with("--review-mode", "compact-v1")[1])
    assert done["action"] == "done" and done["result"]["reason_code"] is None, done
    root = replay.run_root(done)
    parent, child = (attempt["attempt_id"] for attempt in derive_state(root)["attempts"])
    plan = read_review_plan(root, child)
    assert len(scenario.r2_texts) == 1 == len(plan["parts"])
    answered = [area["area_id"] for area in review_compact.answer_areas(plan["parts"][0])]
    assert answered[0] == f"local-{CASE}" and len(answered) == 2 and answered[1].startswith("cross-")
    aggregate = read_review_aggregate(root, child)["aggregate"]
    assert len(aggregate["carried"]) == 12 and all(row["from_attempt_id"] == parent for row in aggregate["carried"])
    assert aggregate["complete"] and aggregate["eligible"]


def test_a_behavioral_correction_is_rechecked_in_the_parts_holding_the_case(tmp_path: Path) -> None:
    from tools.pilot_state import read_review_aggregate

    def review(task):
        if task.get("review_mode") != "compact-v1":
            return None
        text = part_text(task)
        answer = clean_compact_answer(text)
        if "проверка)" not in text.split("\n", 1)[0]:
            answer["corrections"] = [{"target_id": "TC-B1-005", "field": "objective", "before": None, "after": None, "why": "Уточнить цель."}]
            # A correction no WARNING/BLOCKING finding names is a rewording: journalled, not re-checked (2026-10-08).
            answer["findings"] = [{"severity": "WARNING", "code": "OBJECTIVE_IMPRECISE", "related_ids": ["TC-B1-005"], "message": "Цель неточна."}]
            snapshot_case = next(line for line in text.splitlines() if line.startswith("  objective: ") and "[TC-B1-005]" in text)
            del snapshot_case
        return answer

    replay = Replay("2c10d733", tmp_path)

    def fill(task):
        answer = review(task)
        if answer and answer["corrections"]:
            from tools.pilot_state import read_attempt_receipt

            root = replay.project / ".pilot-runs" / task["run_id"]
            document = read_attempt_receipt(root, task["attempt_id"], "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]["document"]
            before = next(case for case in document["test_cases"] if case["case_id"] == "TC-B1-005")["objective"]
            answer["corrections"][0].update(before=before, after=before + " Значение id берётся из параметра запроса.")
        return answer

    replay.override = fill
    code, done = replay.drive(replay.start_with("--review-mode", "compact-v1")[1])
    tasks = replay.tasks("tc-reviewer:")
    assert len(tasks) == 2, [task["task_id"] for task in tasks]  # the base part and one check part
    check = part_text(tasks[1])
    assert "Предложенная правка TC-B1-005.objective" in check
    aggregate = read_review_aggregate(replay.run_root(done), done["result"]["attempt_id"])
    assert aggregate["output"]["artifacts"]["validation_report"]["verdict"] == "AUTO_FIX_APPLIED"
