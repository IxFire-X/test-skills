"""D4: one rework loop inside the driver.

REJECTED r1 -> a CANONICAL_REWORK child attempt -> the generator gets r1, the
BLOCKING/WARNING findings and the affected cases and returns only changed cases ->
canonical r2 (parent_sha256 = r1) -> an incremental review of the changed cases and
their pairs; every other scope carries its r1 coverage by identical inputs.
After r2 there is no further rework.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from tests.live_step5 import Replay
from tools.pilot_state import derive_state, read_effective_canonical_if_present, read_review_aggregate, read_terminal_result

CASE = "TC-B1-006"
BLOCKING = {"severity": "BLOCKING", "code": "EXPECTATION_CLAIM_WITHOUT_ASSERTION",
            "message": "Ожидание «студент не возвращается» в TC-B1-006 ничем не проверяется.", "evidence": ["/test_cases/5"], "related_ids": [CASE]}


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Rework:
    """Reviewer and generator answers of one rework scenario on the live 2c10d733 run."""

    def __init__(self, *, reject_r2: bool = False) -> None:
        self.reject_r2 = reject_r2
        self.first_attempt: str | None = None
        self.rework_tasks: list[dict[str, Any]] = []
        self.r2_envelopes: list[dict[str, Any]] = []

    def review(self, task: dict[str, Any], envelope: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        self.first_attempt = self.first_attempt or task["attempt_id"]
        if task["attempt_id"] == self.first_attempt:
            if envelope["part_id"] == "part-000001":
                answer["findings"].append(dict(BLOCKING))
            return answer
        self.r2_envelopes.append(envelope)
        if self.reject_r2 and envelope["part_id"] == "part-000001":
            answer["findings"].append({**BLOCKING, "message": "Исправление не помогло."})
        return answer

    def override(self, task: Mapping[str, Any]) -> Any:
        if str(task.get("stage", "")).startswith("tc-generator:") and task.get("rework"):
            self.rework_tasks.append(dict(task))
            brief = _load(task["inputs"][0])
            case = copy.deepcopy(next(item for item in brief["r1_cases"] if item["case_id"] == CASE))
            case["steps"][0]["expectations"][0]["text"] = "Приложение отклоняет запрос как некорректный.\n\nHTTP 400 Bad Request"
            return {"test_cases": [case]}
        return None


def _expected_r2_scopes() -> set[str]:
    index = int(CASE.rsplit("-", 1)[1]) - 1
    pairs = {f"cross-{min(index, other):06d}-{max(index, other):06d}" for other in range(12) if other != index}
    return {f"local-{CASE}", *pairs}


def test_rejected_r1_is_reworked_once_and_r2_review_is_incremental(tmp_path: Path) -> None:
    scenario = Rework()
    replay = Replay("2c10d733", tmp_path, review=scenario.review, override=scenario.override)
    code, done = replay.drive()
    assert [row["payload"] for row in replay.log if row["payload"].get("action") == "error"] == []
    assert done["action"] == "done", done
    root = replay.run_root(done)
    attempts = derive_state(root)["attempts"]
    assert len(attempts) == 2 and attempts[1]["parent_attempt_id"] == attempts[0]["attempt_id"] and attempts[1]["retry_reason"] == "CANONICAL_REWORK"
    parent, child = (attempt["attempt_id"] for attempt in attempts)
    assert read_terminal_result(root, parent)["reason_code"] == "REWORK"
    result = done["result"]
    assert result["attempt_id"] == child and (result["completion"], result["reason_code"]) == ("COMPLETE", None), result

    # The generator saw r1, BLOCKING/WARNING findings only and the affected cases, and returned only the changed case.
    [task] = scenario.rework_tasks
    brief = _load(task["inputs"][0])
    assert brief["affected_case_ids"] == [CASE]
    assert brief["findings"] and {item["severity"] for item in brief["findings"]} <= {"BLOCKING", "WARNING"}
    assert BLOCKING in brief["findings"]
    assert len(brief["r1_cases"]) == 12

    # r2: a revision of r1 with the changed case, reviewed in a new session.
    r1 = _load(next(iter((root.with_name(root.name + ".driver") / "candidate-bundle").glob("*.r1.json"))))
    effective = read_effective_canonical_if_present(root, child)
    document = effective["document"]
    from tools.canonical_document import document_sha256

    assert (document["revision"], document["parent_sha256"]) == (2, document_sha256(r1))
    changed = [case["case_id"] for case, before in zip(document["test_cases"], r1["test_cases"]) if case != before]
    assert changed == [CASE]

    # The r2 review sent only the changed case and its pairs; everything else was carried from r1.
    sent = {scope["scope_id"] for envelope in scenario.r2_envelopes for scope in envelope["scopes"]}
    assert sent == _expected_r2_scopes()
    aggregate = read_review_aggregate(root, child)["aggregate"]
    carried = {row["scope_id"] for row in aggregate["carried"]}
    assert not carried & sent and len(carried) + len(sent) == 79
    assert all(row["from_attempt_id"] == parent for row in aggregate["carried"])
    assert aggregate["complete"] and aggregate["eligible"]


def test_second_rejection_ends_with_rework_and_no_third_revision(tmp_path: Path) -> None:
    scenario = Rework(reject_r2=True)
    replay = Replay("2c10d733", tmp_path, review=scenario.review, override=scenario.override)
    code, done = replay.drive()
    assert done["action"] == "done" and done["result"]["reason_code"] == "REWORK", done
    assert len(derive_state(replay.run_root(done))["attempts"]) == 2
    assert len(scenario.rework_tasks) == 1
    code, again = replay.next(done["run_id"])
    assert again["action"] == "done" and again["result"]["attempt_id"] == done["result"]["attempt_id"]


def test_rework_child_requires_a_rejected_parent(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt, read_execution_baseline_for_attempt

    replay = Replay("2c10d733", tmp_path)
    code, done = replay.drive()
    root = replay.run_root(done)
    parent = derive_state(root)["attempts"][-1]
    baseline = read_execution_baseline_for_attempt(root, parent["attempt_id"])
    import pytest

    with pytest.raises(ValueError, match="CANONICAL_REWORK"):
        create_attempt(root, {"project": parent["project"], "module": parent["module"], "policy_profile": parent["policy_profile"],
                              "parent_attempt_id": parent["attempt_id"], "retry_reason": "CANONICAL_REWORK"}, baseline)
