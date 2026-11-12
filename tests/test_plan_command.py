"""P2 / V3b: ``submit --stage plan`` gives the publication the three commands of before gave (the header, the plan of the batches, the plan) — the same
events and the same bytes of the plan — and keeps the pause after the marker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_no_refusal_event, journal_minus_refusals
from tests.test_scope_pause import GAP, _marker_rows, _paused_run, _submit_plan
from tools import pilot_state
from tools.batch_assembly import plan_batches_for_project


def _shape(root: Path) -> list[tuple]:
    rows = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [(row["event_type"], row.get("stage_instance_id"), row.get("batch_id")) for row in rows]


def test_submit_plan_gives_the_events_and_the_plan_of_the_three_steps_of_before(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    root, attempt_id, _b, receipts = _paused_run(tmp_path / "new", warnings=[])
    code, planned = _submit_plan(capsys, root)
    assert code == 0 and planned["status"] == "ok", planned
    header = dict(pilot_state.generator_input(root.parents[1], root, pilot_state.derive_state(root), attempt_id, "header"))
    plan = dict(pilot_state.generator_input(root.parents[1], root, pilot_state.derive_state(root), attempt_id, "plan"))

    old_root, old_attempt, _b, old_receipts = _paused_run(tmp_path / "old", warnings=[])  # the steps of before, by the functions that stayed
    old_header = pilot_state.publish_generator_input(old_root, old_attempt, "header", {key: value for key, value in header.items() if key != "digest"})
    evidence = {"header_digest": old_header["digest"], "context_receipt_digest": old_receipts[0]["digest"], "context_receipt_digests": [old_receipts[0]["digest"]]}
    digest = pilot_state.derive_state(old_root)["events"]
    answer = next(event["artifact_digest"] for event in digest if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    marker = old_root / pilot_state.read_model_stage_artifact(old_root, old_attempt, "context-marker:baseline", answer)["path"]
    old_plan = plan_batches_for_project(_marker_rows(), evidence, tmp_path / "old", [marker], old_receipts, None, None)
    pilot_state.publish_generator_input(old_root, old_attempt, "plan", dict(old_plan))
    assert _shape(root) == _shape(old_root)
    assert plan["header_digest"] == header["digest"] == old_plan["header_digest"] and planned["result"]["plan"]["digest"] == plan["digest"]
    assert [row["owned_source_requirement_ids"] for row in plan["batches"]] == [row["owned_source_requirement_ids"] for row in old_plan["batches"]]
    assert plan["source_requirements"] == old_plan["source_requirements"]
    assert planned["result"]["notes"] == list(old_plan.notes)  # what the plan says about the split


def test_the_pause_after_the_marker_stops_the_plan_before_it_publishes_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _paused_run(tmp_path, warnings=[GAP])
    before = journal_minus_refusals(root)
    code, refused = _submit_plan(capsys, root)
    assert code == 2 and refused["code"] == "SCOPE_PENDING", refused
    assert journal_minus_refusals(root) == before
    assert_no_refusal_event(root, before)  # the pause of the human is routine (tools.api._record_refusal): a SCOPE_PENDING stop is no refusal and adds no event
    from tests.test_scope_pause import _submit_scope

    assert _submit_scope(capsys, root, {"decision": "continue"})[0] == 0  # the human answers on the stage `scope`, then the plan goes
    code, answered = _submit_plan(capsys, root)
    assert code == 0, answered
