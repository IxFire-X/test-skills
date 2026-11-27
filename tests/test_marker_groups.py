"""The parts of the marker are receipts in a row whose worst case fits 0.85 of the model window; the journal takes every receipt once, in order."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_marker_parts import _marker, _publish_requirements, _run
from tools import pilot_state, step_size
from tools.context_marker_parts import merge_parts, plan
from tools.model_records import part_receipts


def _part(root: Path, attempt_id: str, baseline: dict, number: int, receipts: list[dict], requirements: dict) -> None:
    stage = f"context-marker:batch-{number:03d}"
    pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-context", invocation_id=f"part-{number}",
                                      input_digests=[baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts), requirements["content_digest"]])
    pilot_state.publish_model_stage_artifact(root, attempt_id, stage, _marker([f"src/part{number}.py — x"]))


def _groups(root: Path) -> list[list[str]]:
    return [part["input_digests"][1:-1] for part in plan(root)["parts"][1:]]


def test_small_receipts_are_one_part(tmp_path):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    assert _groups(root) == [[receipt["digest"] for receipt in receipts]]


def test_a_part_ends_where_its_worst_case_leaves_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(step_size, "DEFAULT_WINDOW", 240_000)  # 0.85 of it holds two receipts of 72 KB whole with the role, not three
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    assert _groups(root) == [[receipts[0]["digest"], receipts[1]["digest"]], [receipts[2]["digest"]]]


def test_a_part_asked_keeps_its_receipts(tmp_path):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    _part(root, attempt_id, baseline, 1, receipts[:1], requirements)
    assert _groups(root) == [[receipts[0]["digest"]], [receipts[1]["digest"], receipts[2]["digest"]]]


def test_a_part_before_fat_p3_binds_one_receipt():
    assert part_receipts(["docs", "inventory", "r1", "answer"], ["docs", "inventory"]) == ["r1"]
    assert part_receipts(["inventory", "r1", "r2", "answer"], ["docs", "inventory"]) == ["r1", "r2"]


def test_a_part_grows_only_while_its_maps_fit_the_step_budget(tmp_path):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    assert [len(part["input_digests"]) - 2 for part in plan(root, budget=1_000)["parts"][1:]] == [1, 1, 1]


def test_an_unpublished_digest_in_the_place_of_a_receipt_is_refused(tmp_path):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    _part(root, attempt_id, baseline, 1, receipts[:1], requirements)
    with pytest.raises(ValueError, match="must bind context receipts in a row"):
        pilot_state.publish_model_request(root, attempt_id, "context-marker:batch-002", model_id="model-context", invocation_id="part-x",
                                          input_digests=[baseline["inventory_digest"], "sha256:" + "0" * 64, receipts[1]["digest"], requirements["content_digest"]])


def test_a_merge_refuses_parts_that_miss_a_receipt(tmp_path):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    _part(root, attempt_id, baseline, 1, receipts[:2], requirements)
    with pytest.raises(ValueError, match="CONTEXT_MARKER_PARTS_INCOMPLETE"):
        merge_parts(root)
    answer = pilot_state.derive_state(root)["events"][-1]["artifact_digest"]
    inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"], receipts[1]["digest"], requirements["content_digest"], answer]
    with pytest.raises(ValueError, match="CONTEXT_MARKER_PARTS_INCOMPLETE"):
        pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="controller:context-marker-merge", invocation_id="partial", input_digests=inputs)


@pytest.mark.parametrize("order", [(0, 2), (1, 0)])
def test_a_part_with_a_gap_or_out_of_order_is_refused(tmp_path, order):
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    with pytest.raises(ValueError, match="must bind context receipts in a row"):
        _part(root, attempt_id, baseline, 1, [receipts[index] for index in order], requirements)


def test_the_inventory_view_counts_in_the_worst_case_of_a_part(tmp_path, monkeypatch):  # review 9b2e8a65 M1
    monkeypatch.setattr(step_size, "DEFAULT_WINDOW", 240_000)  # two receipts a part without the inventory view (the test above)
    monkeypatch.setattr(step_size, "inventory_view_bytes", lambda *_args: b"x" * 60_000)
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    assert _groups(root) == [[receipt["digest"]] for receipt in receipts]


def test_a_part_asked_past_a_regrouped_receipt_is_refused(tmp_path, monkeypatch):  # review 9b2e8a65 M5
    monkeypatch.setattr(step_size, "DEFAULT_WINDOW", 240_000)
    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    _part(root, attempt_id, baseline, 2, receipts[2:], requirements)  # part 001 would hold receipts 1 and 2
    monkeypatch.setattr(step_size, "DEFAULT_WINDOW", 0)  # now it holds receipt 1 alone: receipt 2 falls out
    with pytest.raises(ValueError) as refused:
        plan(root)
    assert str(refused.value) == ("CONTEXT_MARKER_PARTS_REGROUPED: часть context-marker:batch-002 спрошена с квитанциями batch-003, а по порядку ей идут "
                                  "batch-002: квитанции batch-002 не попали бы ни в одну часть (лимиты или роль изменились после запроса части); "
                                  "fix: начните разметку заново — scan --new-run")  # approved 27.11, texts p. 8
