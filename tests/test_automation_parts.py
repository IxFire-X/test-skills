"""P2: the automation generator works part by part when the whole document does not fit the task input budget.

The part plan is a pure function of the effective document and the byte budget left after the context and the brief:
whole cases in document order, every case exactly once, every part within the budget.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tools import automation_parts as parts

ROOT = Path(__file__).resolve().parents[1]
PETCLINIC = ROOT / "tests" / "fixtures" / "replay-petclinic-20261008"
PETCLINIC_CONTEXT_BYTES = 405_624  # inputs/context of the live Petclinic run d (113 files)


def _petclinic_document() -> dict:
    return json.loads(gzip.open(PETCLINIC / "canonical-snapshot.json.gz").read())["payload"]["document"]


def test_petclinic_splits_into_parts_that_fit_the_default_budget() -> None:
    document = _petclinic_document()
    budget = 1_000_000 - PETCLINIC_CONTEXT_BYTES - 4_096  # context and brief travel with every part
    plan = parts.plan_parts(document, budget)
    assert len(plan) >= 3, len(plan)
    ids = [case_id for part in plan for case_id in part["case_ids"]]
    assert ids == [case["case_id"] for case in document["test_cases"]]  # document order, every case exactly once
    for part in plan:
        assert len(parts.part_document_bytes(document, part["case_ids"])) <= budget, part["part_id"]
    assert [part["part_id"] for part in plan] == [f"part-{index:03d}" for index in range(1, len(plan) + 1)]
    assert parts.plan_parts(document, budget) == plan  # deterministic


def test_one_part_when_the_document_fits() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, 10_000_000)
    assert len(plan) == 1 and plan[0]["case_ids"] == [case["case_id"] for case in document["test_cases"]]


def test_a_case_larger_than_the_budget_is_refused() -> None:
    document = _petclinic_document()
    largest = max(len(parts.part_document_bytes(document, [case["case_id"]])) for case in document["test_cases"])
    with pytest.raises(parts.PartPlanError):
        parts.plan_parts(document, largest - 1)


def test_the_part_document_keeps_the_header_and_only_its_cases() -> None:
    document = _petclinic_document()
    chosen = [document["test_cases"][1]["case_id"], document["test_cases"][3]["case_id"]]
    subset = json.loads(parts.part_document_bytes(document, chosen))
    assert [case["case_id"] for case in subset["test_cases"]] == chosen
    assert {key: value for key, value in subset.items() if key != "test_cases"} == {key: value for key, value in document.items() if key != "test_cases"}
