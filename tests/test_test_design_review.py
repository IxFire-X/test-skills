"""D1b step 8: the case reviewer checks the cases against the test design.

In a design run the compact review snapshot carries the design (with its combinations); each part shows the design of
its own cases with ``[EQ-…]``/``[DR-…]`` anchors, so a finding can name a design element.  Without a design the
snapshot, the part texts and the area fingerprints are exactly as before (pinned on the Petclinic record).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests import replay_petclinic as replay
from tests.review_scaling_helpers import clean_compact_answer, part_text
from tests.test_review_fixes_driver import _drive, _project, _run_root, _start
from tests.test_test_design_generation import Generation
from tests.test_test_design_stage import _design_setting
from tools import pipeline_driver as driver
from tools import test_design as design_tools
from tools.review_compact import build_compact_plan, known_ids
from tools.review_parts import review_digest

ROOT = Path(__file__).resolve().parents[1]
PETCLINIC_DESIGN = json.loads((ROOT / "tests" / "fixtures" / "test-design" / "petclinic-20261008-design.json").read_text(encoding="utf-8"))
# The plan of the recorded Petclinic snapshot before D1b: 38 parts, their texts and area fingerprints.
PINNED_PARTS = "sha256:b772abdb28c12d638d6a843e0ffba314d6bbb063a0cc1b77b3e47216602ecced"
PINNED_PLAN = "sha256:9c7c3570d0ee74799f30a4cd6b2a83675650a5895894619eef2df5ab516ed12c"


def _parts_digest(plan: dict[str, Any]) -> str:
    return review_digest([(part["part_id"], part["text"], [area["fingerprint"] for area in part["areas"]]) for part in plan["parts"]])


def _petclinic_plan(design: dict[str, Any] | None) -> dict[str, Any]:
    recorded = replay.snapshot("canonical")
    specification = {name: value for name, value in recorded["specification"].items() if name != "projection_digest"}
    payload = copy.deepcopy(recorded["payload"])
    if design is not None:
        payload["design"] = design
    return build_compact_plan(specification, payload, input_byte_budget=recorded["input_byte_budget"])


def _petclinic_design() -> dict[str, Any]:
    return design_tools.with_combinations(PETCLINIC_DESIGN, design_tools.combinations(PETCLINIC_DESIGN, 16))


# --- without a design: nothing changes ----------------------------------------------------------------------------


def test_without_a_design_the_petclinic_plan_is_byte_identical() -> None:
    plan = _petclinic_plan(None)
    assert len(plan["parts"]) == 38
    assert (_parts_digest(plan), plan["digest"]) == (PINNED_PARTS, PINNED_PLAN)
    assert not any("Тест-дизайн" in part["text"] for part in plan["parts"])


# --- with a design: each part shows the design of its cases -------------------------------------------------------


def test_every_case_part_shows_the_design_of_its_cases_within_the_budget() -> None:
    design = _petclinic_design()
    plan = _petclinic_plan(design)
    document = replay.payload("canonical")["document"]
    sources = {}
    for row in document["source_to_canonical_mappings"]:
        for creq in row["canonical_requirement_ids"]:
            sources.setdefault(creq, set()).add(row["source_requirement_id"])
    case_parts = [part for part in plan["parts"] if part["case_ids"]]
    assert case_parts and all(part["blocked_reason"] is None for part in plan["parts"])
    for part in case_parts:
        own = {source for case in document["test_cases"] if case["case_id"] in part["case_ids"] for creq in case["requirement_ids"] for source in sources.get(creq, ())}
        expected = design_tools.design_slice(PETCLINIC_DESIGN, own, design["artifacts"]["combinations"])
        for name in ("partitions", "rules", "combinations"):
            for row in expected["artifacts"][name]:
                assert f"[{row['id']}]" in part["text"], (part["part_id"], row["id"])
    # A design element is a known ID of the review: a finding may name it.
    assert {row["id"] for row in design["artifacts"]["rules"]} <= known_ids(plan, document)


def test_a_changed_design_changes_the_fingerprints_of_the_cases_it_touches() -> None:
    design = _petclinic_design()
    changed = copy.deepcopy(design)
    rule = next(row for row in changed["artifacts"]["rules"] if "SREQ-0008" in row["requirement_ids"])
    rule["expected"] = "другой ожидаемый результат"
    before = {area["area_id"]: area["fingerprint"] for part in _petclinic_plan(design)["parts"] for area in part["areas"]}
    after = {area["area_id"]: area["fingerprint"] for part in _petclinic_plan(changed)["parts"] for area in part["areas"]}
    differs = {area for area in before if area in after and before[area] != after[area]}
    assert differs and all(area.startswith(("local-", "cross-")) for area in differs)


# --- the run ------------------------------------------------------------------------------------------------------


class Reviewed(Generation):
    """A design run reviewed in compact-v1: clean answers, or one BLOCKING design finding on the first review part."""

    def __init__(self, profile: str, *, finding: dict[str, Any] | None = None) -> None:
        super().__init__(profile)
        self.finding = finding
        self.review_texts: list[str] = []

    def answer(self, task: dict[str, Any]) -> Any:
        if task.get("review_mode") == "compact-v1" and task["stage"].startswith("tc-reviewer:"):
            self.stages.append(task["stage"])
            text = part_text(task)
            self.review_texts.append(text)
            value = clean_compact_answer(text)
            if self.finding is not None and len(self.review_texts) == 1:
                value["findings"].append(dict(self.finding))
            return value
        return super().answer(task)


def test_the_review_part_of_a_design_run_shows_the_design(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    model = Reviewed("cases-only-v1")
    done = _drive(project, _start(project, "cases-only-v1", review_mode="compact-v1"), model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    [text] = model.review_texts
    assert "## Тест-дизайн" in text and "[DR-001]" in text and "[DR-002]" in text
    assert "открыт список товаров" in text  # the expected outcome of the rule is in front of the reviewer


def test_a_design_finding_names_a_rule_and_blocks_the_review(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    finding = {"severity": "BLOCKING", "code": "DESIGN_EXPECTED_MISMATCH", "related_ids": ["DR-002"],
               "message": "Ожидаемый результат кейса не совпадает с expected правила DR-002."}
    model = Reviewed("cases-only-v1", finding=finding)
    task = _drive(project, _start(project, "cases-only-v1", review_mode="compact-v1"), model,
                  until=lambda task: len(model.review_texts) >= 1 and task.get("stage", "").startswith("tc-generator:"))
    # The BLOCKING finding was accepted as an answer and rejected the review: the run reworks the cases.
    assert task["stage"].startswith("tc-generator:") and task.get("rework"), task
    from tools.pilot_state import derive_state

    attempts = derive_state(_run_root(project, task))["attempts"]
    assert len(attempts) == 2 and attempts[1]["retry_reason"] == "CANONICAL_REWORK"


def test_the_journal_refuses_a_review_snapshot_without_or_with_another_design(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "without", local=False)
    _design_setting(project, True)
    original = driver._review_payload

    def without(*arguments: Any, **options: Any) -> dict[str, Any]:
        payload = original(*arguments, **options)
        payload.pop("design", None)
        return payload

    monkeypatch.setattr(driver, "_review_payload", without)
    with pytest.raises(ValueError, match="design"):
        _drive(project, _start(project, "cases-only-v1", review_mode="compact-v1"), Reviewed("cases-only-v1"))

    project = _project(tmp_path / "other", local=False)
    _design_setting(project, True)

    def other(*arguments: Any, **options: Any) -> dict[str, Any]:
        payload = original(*arguments, **options)
        if "design" in payload:
            payload["design"]["artifacts"]["rules"][0]["expected"] = "подменённый результат"
        return payload

    monkeypatch.setattr(driver, "_review_payload", other)
    with pytest.raises(ValueError, match="design"):
        _drive(project, _start(project, "cases-only-v1", review_mode="compact-v1"), Reviewed("cases-only-v1"))
