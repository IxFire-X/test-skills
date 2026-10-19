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


# --------------------------------------------------------------------------------------
# step (a): the case limit, balancing, the part brief, the part answer and the merge
# --------------------------------------------------------------------------------------

def _budget() -> int:
    return 1_000_000 - PETCLINIC_CONTEXT_BYTES - 4_096


def test_petclinic_splits_into_balanced_parts_of_at_most_twelve_cases() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, _budget(), cases_per_part=12)
    sizes = [len(part["case_ids"]) for part in plan]
    assert len(plan) == 7 and max(sizes) <= 12 and max(sizes) - min(sizes) <= 1, sizes  # 81 cases: 7 parts of 11-12
    assert [case_id for part in plan for case_id in part["case_ids"]] == [case["case_id"] for case in document["test_cases"]]
    assert all(len(parts.part_document_bytes(document, part["case_ids"])) <= _budget() for part in plan)


def test_balancing_adds_parts_until_every_part_fits_its_bytes() -> None:
    """Even counts are not even sizes: a part of equal count may exceed the budget, then k grows and the split repeats."""
    big = "x" * 4_000
    cases = [{"case_id": f"TC-{index:02d}", "title": big if index < 3 else "s"} for index in range(12)]
    document = {"document_id": "D", "test_cases": cases}
    budget = len(parts.part_document_bytes(document, ["TC-00", "TC-01"])) + 50  # two big cases fit, three do not
    plan = parts.plan_parts(document, budget, cases_per_part=6)
    assert all(len(parts.part_document_bytes(document, part["case_ids"])) <= budget for part in plan)
    assert len(plan) >= 3 and [case_id for part in plan for case_id in part["case_ids"]] == [case["case_id"] for case in cases]
    assert parts.plan_parts(document, budget, cases_per_part=6) == plan


def test_no_case_limit_with_zero_and_one_part_under_both_limits() -> None:
    document = _petclinic_document()
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=0)) == 1
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=81)) == 1
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=40)) == 3


def test_a_repeated_case_id_is_refused_before_the_split() -> None:
    document = {"document_id": "D", "test_cases": [{"case_id": "TC-1"}, {"case_id": "TC-2"}, {"case_id": "TC-1"}]}
    with pytest.raises(parts.PartPlanError) as raised:
        parts.plan_parts(document, 10_000_000)
    assert raised.value.code == "AUTOMATION_PARTS_INVALID"


def test_the_plan_receipt_states_the_cost_of_the_split() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, _budget(), cases_per_part=12)
    receipt = parts.plan_receipt(document, plan, fixed_bytes=PETCLINIC_CONTEXT_BYTES + 4_096, budget=_budget(), cases_per_part=12,
                                 single_task_input_bytes=2_345_593)  # measured by the driver for the live Petclinic run
    assert receipt["parts"] == len(plan) and [row["part_id"] for row in receipt["rows"]] == [part["part_id"] for part in plan]
    assert receipt["total_input_bytes"] == sum(row["input_bytes"] for row in receipt["rows"])
    assert all(row["input_bytes"] == PETCLINIC_CONTEXT_BYTES + 4_096 + len(parts.part_document_bytes(document, row["case_ids"])) for row in receipt["rows"])
    assert receipt["single_task_input_bytes"] == 2_345_593


def _part_answer(part_id: str, case_ids: list[str], *, path: str | None = None, extra_case: str | None = None) -> dict:
    marker = parts.part_marker(part_id)
    file_id = parts.file_id_prefix(part_id) + "1"
    relations = [{"case_id": case_id, "step_id": f"STEP-{case_id}", "kind": "operation", "file_id": file_id, "symbol_id": f"SYM-{case_id}"}
                 for case_id in [*case_ids, *([extra_case] if extra_case else [])]]
    return {"automation_status": "GENERATED",
            "generated_files": [{"file_id": file_id, "path": path or f"src/test/java/x/Pet{marker}Test.java", "language": "java", "framework": "junit5",
                                 "content": f"class Pet{marker}Test {{}}"}],
            "generated_symbols": [{"file_id": file_id, "symbol_id": f"SYM-{case_id}", "locator": {"method_name": f"m{case_id}"}} for case_id in case_ids],
            "implementation_relations": relations, "manual_dispositions": [], "diagnostics": [], "warnings": [f"{part_id} — ok", "общее — ok"]}


def test_a_part_answer_keeps_to_its_prefix_marker_and_cases() -> None:
    good = _part_answer("part-002", ["TC-3", "TC-4"])
    assert parts.part_answer_problems("part-002", ["TC-3", "TC-4"], good) == []
    codes = lambda value: {row["code"] for row in parts.part_answer_problems("part-002", ["TC-3", "TC-4"], value)}
    assert "AUTOMATION_PART_PATH" in codes(_part_answer("part-002", ["TC-3", "TC-4"], path="src/test/java/x/PetTest.java"))
    assert "AUTOMATION_PART_CASE" in codes(_part_answer("part-002", ["TC-3", "TC-4"], extra_case="TC-9"))
    wrong = _part_answer("part-002", ["TC-3", "TC-4"])
    wrong["generated_files"][0]["file_id"] = "FILE-1"
    assert "AUTOMATION_PART_FILE_ID" in codes(wrong)
    assert "AUTOMATION_PART_COVERAGE" in codes(_part_answer("part-002", ["TC-3"]))


def test_the_merge_unites_the_parts_in_order() -> None:
    first, second = _part_answer("part-001", ["TC-1", "TC-2"]), _part_answer("part-002", ["TC-3"])
    second["automation_status"] = "BLOCKED"
    second["diagnostics"] = [{"code": "X", "message": "m"}]
    merged = parts.merge_part_answers([first, second])
    assert [row["file_id"] for row in merged["generated_files"]] == [first["generated_files"][0]["file_id"], second["generated_files"][0]["file_id"]]
    assert len(merged["generated_symbols"]) == 3 and len(merged["implementation_relations"]) == 3
    assert merged["automation_status"] == "BLOCKED" and merged["diagnostics"] == [{"code": "X", "message": "m"}]
    assert merged["warnings"] == ["part-001 — ok", "общее — ok", "part-002 — ok"]
    assert parts.merge_part_answers([first, second]) == merged
    clash = _part_answer("part-003", ["TC-4"], path=first["generated_files"][0]["path"])
    with pytest.raises(parts.PartPlanError):
        parts.merge_part_answers([first, clash])


def test_the_part_brief_names_its_cases_prefix_and_marker() -> None:
    brief = parts.part_brief({"part_id": "part-003", "case_ids": ["TC-7", "TC-8"]}, count=7)
    assert brief["owned_case_ids"] == ["TC-7", "TC-8"] and brief["file_id_prefix"] == "FILE-P003-" and brief["name_marker"] == "Part003"
    assert "3 из 7" in brief["instructions"] and "Part003" in brief["instructions"]
