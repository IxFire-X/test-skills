"""D2: required checks are addressed by case/requirement; a late CHECKED closes an early UNCHECKED.

Live run d1834358: part 2 asked to look at TC-B1-001/002 but could name only the
scopes of its own part, so the check part received TC-B1-003/004/007/008 and
answered UNCHECKED.  Part 4 (requested by part 3 by local scope) answered the same
question CHECKED (CROSS_CHECK_RESOLVED), yet the aggregate stayed blocked.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.live_step5 import Replay, ledger, outputs, review_state
from tools.review_parts import aggregate_review_parts, part_input, review_digest, validate_review_part
from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]


def live_plan(run: str, review_key: str = "canonical") -> dict[str, Any]:
    """The durable plan with the additions its ledger registered, as pilot_state reads it."""
    plan = copy.deepcopy(review_state(run, f"review-plan-{review_key}")["plan"])
    events = ledger(run, review_key)["events"]
    plan["additions"] = [event["part"] for event in events if event["event_type"] == "REVIEW_CHECK_ADDED"]
    plan["unavailable"] = {event["part_id"]: event["reason"] for event in events if event["event_type"] == "REVIEW_PART_BLOCKED"}
    return plan


def live_results(run: str, plan: dict[str, Any], review_key: str = "canonical") -> list[dict[str, Any]]:
    """The recorded answers bound to their parts exactly as submit_review_part binds them."""
    parts = {part["part_id"]: part for part in [*plan["parts"], *plan.get("additions", [])]}
    results = []
    for label, answer in outputs(run).items():
        if label.startswith(f"review.{review_key}."):
            part = parts[label.rsplit(".", 1)[1]]
            results.append({"schema_version": "1.0.0", "plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
                            "part_id": part["part_id"], "input_digest": review_digest(part_input(plan, part)), **answer})
    return results


def test_live_d1834358_late_checked_part_closes_the_early_unchecked_scope() -> None:
    plan = live_plan("d1834358")
    results = live_results("d1834358", plan)
    assert [part["part_id"] for part in plan["additions"]] == ["part-000003", "part-000004"]
    aggregate = aggregate_review_parts(plan, results)
    assert aggregate["diagnostics"] == []
    assert aggregate["unchecked"] == [], aggregate["unchecked"]
    assert aggregate["complete"] and not aggregate["blocked"] and aggregate["eligible"]
    [link] = aggregate["resolved_unchecked"]
    part3 = plan["additions"][0]
    assert link == {"part_id": "part-000003", "scope_id": part3["scopes"][0]["scope_id"], "resolved_by": ["part-000004"]}


def test_unchecked_stays_open_when_the_late_check_is_unchecked_too() -> None:
    plan = live_plan("d1834358")
    results = live_results("d1834358", plan)
    for result in results:
        if result["part_id"] == "part-000004":
            result["coverage"] = [{**row, "status": "UNCHECKED"} for row in result["coverage"]]
    aggregate = aggregate_review_parts(plan, results)
    assert not aggregate["complete"] and aggregate["blocked"] and aggregate.get("resolved_unchecked", []) == []
    assert {row["scope_id"] for row in aggregate["unchecked"]} >= {plan["additions"][0]["scopes"][0]["scope_id"], plan["additions"][1]["scopes"][0]["scope_id"]}


def test_live_aggregates_without_resolution_are_unchanged() -> None:
    """Old completed reviews recompute to the same aggregate (durable receipts stay readable)."""
    for run in ("2c10d733", "9340016c"):
        plan = live_plan(run)
        recorded = review_state(run, "review-aggregate-canonical")["aggregate"]
        assert aggregate_review_parts(plan, live_results(run, plan)) == recorded


def test_sealed_d1834358_aggregate_still_verifies_in_legacy_mode() -> None:
    """The receipt sealed by the live run (blocked) stays readable: pilot_state accepts the legacy recomputation."""
    plan = live_plan("d1834358")
    recorded = review_state("d1834358", "review-aggregate-canonical")["aggregate"]
    assert recorded["blocked"] and not recorded["complete"]
    assert aggregate_review_parts(plan, live_results("d1834358", plan), legacy=True) == recorded


def _schema_rows(check: dict[str, Any]) -> list[dict[str, Any]]:
    result = {"schema_version": "1.0.0", "plan_digest": "sha256:" + "0" * 64, "snapshot_digest": "sha256:" + "0" * 64, "part_id": "part-000001",
              "input_digest": "sha256:" + "0" * 64, "coverage": [{"scope_id": "x", "status": "CHECKED", "evidence": ["e"], "assessment": "a"}],
              "findings": [], "corrections": [], "required_checks": [check]}
    return schema_diagnostics(result, ROOT / "schemas" / "review-part-output.schema.json", ROOT)


def test_required_check_schema_accepts_case_and_requirement_addresses() -> None:
    assert _schema_rows({"case_ids": ["TC-B1-001"], "reason": "r"}) == []
    assert _schema_rows({"requirement_ids": ["CREQ-B1-011"], "reason": "r"}) == []
    assert _schema_rows({"scope_ids": ["local-TC-B1-001"], "case_ids": ["TC-B1-002"], "reason": "r"}) == []
    assert _schema_rows({"reason": "r"}) != []


def test_check_by_case_id_reaches_the_scopes_of_that_case() -> None:
    plan = live_plan("d1834358")
    results = [result for result in live_results("d1834358", plan) if result["part_id"] in {"part-000001", "part-000002"}]
    plan["additions"], plan["unavailable"] = [], {}
    part2 = next(result for result in results if result["part_id"] == "part-000002")
    part2["required_checks"] = [{"case_ids": ["TC-B1-001", "TC-B1-002"], "reason": part2["required_checks"][0]["reason"]}]
    base = next(part for part in plan["parts"] if part["part_id"] == "part-000002")
    assert validate_review_part(plan, base, part2) == []
    from tools.review_parts import additional_review_parts

    [addition] = additional_review_parts(plan, results)
    assert addition["scopes"][0]["targets"] == ["TC-B1-001", "TC-B1-002"]
    by_requirement = {**part2, "required_checks": [{"requirement_ids": ["CREQ-B1-011"], "reason": "порядок полей"}]}
    [addition] = additional_review_parts(plan, [results[0], by_requirement])
    assert {"TC-B1-001", "TC-B1-002", "TC-B1-007"} <= set(addition["scopes"][0]["targets"])
    unknown = {**part2, "required_checks": [{"case_ids": ["TC-B1-999"], "reason": "нет такого кейса"}]}
    assert [row["code"] for row in validate_review_part(plan, base, unknown)] == ["REVIEW_CHECK_SCOPE"]


def test_part_envelope_lists_every_case_for_addressing() -> None:
    plan = live_plan("d1834358")
    envelope = part_input(plan, plan["parts"][1])
    index = envelope.get("document_index")
    # New plans carry the index; the recorded plan predates it and stays byte-identical.
    assert index is None
    from tools.review_parts import document_index

    snapshot = review_state("d1834358", "review-snapshot-canonical")["payload"]
    cases = document_index(snapshot)["cases"]
    assert [row["case_id"] for row in cases] == [f"TC-B1-{number:03d}" for number in range(1, 13)]
    assert "CREQ-B1-011" in next(row for row in cases if row["case_id"] == "TC-B1-001")["requirement_ids"]


def test_replay_d1834358_reaches_automation_without_rework(tmp_path: Path) -> None:
    replay = Replay("d1834358", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-to-autotest:"))
    assert [row["payload"] for row in replay.log if row["payload"].get("action") == "error"] == []
    assert task.get("stage", "").startswith("tc-to-autotest:"), task
    envelope = json.loads(Path(replay.tasks("tc-reviewer:")[0]["inputs"][0]).read_text(encoding="utf-8"))
    assert [row["case_id"] for row in envelope["document_index"]["cases"]][:2] == ["TC-B1-001", "TC-B1-002"]
    from tools.pilot_state import read_review_aggregate

    aggregate = read_review_aggregate(replay.run_root(task), task["attempt_id"])["aggregate"]
    assert aggregate["complete"] and len(aggregate["resolved_unchecked"]) == 1
