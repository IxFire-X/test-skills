"""D3: a reviewer correction no longer explodes into one check part per cross scope.

Live run 3e852e76: one mechanical correction of ``/test_cases/10/management/components/0``
registered 66 check parts (one per cross scope of the plan, 4-5 minutes each).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tests.live_step5 import Replay
from tests.test_live_fixes_d2 import live_plan, live_results
from tools.review_parts import additional_review_parts, review_bytes


def _base(run: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = live_plan(run)
    results = [result for result in live_results(run, plan) if result["part_id"] in {part["part_id"] for part in plan["parts"]}]
    plan["additions"], plan["unavailable"] = [], {}
    return plan, results


def test_live_3e852e76_registered_66_check_parts() -> None:
    """The recorded ledger: the defect this item fixes."""
    assert len(live_plan("3e852e76")["additions"]) == 66


def test_management_correction_adds_no_check_parts() -> None:
    plan, results = _base("3e852e76")
    [correction] = [item for result in results for item in result["corrections"]]
    assert correction["path"] == "/test_cases/10/management/components/0"
    assert additional_review_parts(plan, results) == []


def _behavioral(plan: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the live correction by a mechanical edit of one step action of TC-B1-003."""
    results = copy.deepcopy(results)
    document_scope = next(scope for part in plan["parts"] for scope in part["scopes"] if scope["scope_id"] == "local-TC-B1-003")
    import json

    case = json.loads(next(item["content"] for item in document_scope["inputs"] if item["pointer"].startswith("/test_cases/")))
    pointer = next(item["pointer"] for item in document_scope["inputs"] if item["pointer"].startswith("/test_cases/"))
    action = case["steps"][0]["action"]
    owner = next(result for result in results if any(row["scope_id"] == "local-TC-B1-003" for row in result["coverage"]))
    for result in results:
        result["corrections"] = []
    owner["corrections"] = [{"id": "FIX-B1-003-action", "correction_kind": "MECHANICAL", "path": f"{pointer}/steps/0/action",
                             "before": action, "after": action + " ", "related_ids": ["TC-B1-003"],
                             "description": "Синтетическая поведенческая правка шага.", "evidence": [pointer]}]
    return results


def test_behavioral_correction_checks_only_cross_scopes_of_the_case_in_packed_parts() -> None:
    plan, results = _base("3e852e76")
    results = _behavioral(plan, results)
    additions = additional_review_parts(plan, results)
    assert additions, "a behavioral correction still needs a cross check"
    from tools.review_parts import _required_checks

    cross = {scope["scope_id"]: scope for part in plan["parts"] for scope in part["scopes"] if scope["kind"] == "cross"}
    checks = _required_checks(plan, results)
    assert len(checks) == len(additions)
    named = [scope_id for check in checks for scope_id in check["scope_ids"] if scope_id in cross]
    assert 0 < len(named) <= 11 and len(set(named)) == len(named)
    assert all("TC-B1-003" in cross[scope_id]["targets"] for scope_id in named)
    # Packed by bytes: every part fits, and no two neighbouring parts could have been one.
    budget = plan["input_byte_budget"] - plan["snapshot"]["response_reserve_bytes"]
    assert all(part["blocked_reason"] is None and part["input_byte_count"] <= budget for part in additions)
    assert len(additions) <= 2, [part["input_byte_count"] for part in additions]


def test_replay_3e852e76_auto_fix_without_extra_parts(tmp_path: Path) -> None:
    replay = Replay("3e852e76", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-to-autotest:"))
    assert [row["payload"] for row in replay.log if row["payload"].get("action") == "error"] == []
    assert task.get("stage", "").startswith("tc-to-autotest:"), task
    from tools.pilot_state import read_effective_canonical_if_present, read_review_aggregate, read_reviewer_session_ledger

    root = replay.run_root(task)
    ledger = read_reviewer_session_ledger(root, task["attempt_id"], review_key="canonical")
    assert [event["event_type"] for event in ledger["events"]].count("REVIEW_CHECK_ADDED") == 0
    review = read_review_aggregate(root, task["attempt_id"])
    assert review["output"]["artifacts"]["validation_report"]["verdict"] == "AUTO_FIX_APPLIED"
    effective = read_effective_canonical_if_present(root, task["attempt_id"])
    assert effective["document"]["test_cases"][10]["management"]["components"] == ["HelloWorldController"]
    from tools.pilot_state import read_review_plan

    assert len(replay.tasks("tc-reviewer:")) == len(read_review_plan(root, task["attempt_id"])["parts"])
    assert review_bytes(review["aggregate"])  # the aggregate is sealed
