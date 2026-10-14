"""B5 (finding 6): the survivor triage sees the mutated class whole and its callers, inside the part budget.

Live run g (42951bc1, attempt 30cbe3b3): 26 triage tasks of one group each showed only ±4 product lines, so
EQUIVALENT decisions leaned on knowledge of the project outside the input; and 13 of them were over the run's
200 000-byte review budget (up to 759 KB: MUT-0001 NamedEntity#toString links 50 cases).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tools import mutation_triage as mt

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(gzip.open(ROOT / "tests" / "fixtures" / "live-g-triage" / "triage-g.json.gz", "rt", encoding="utf-8").read())
SOURCES: dict[str, str] = FIXTURE["sources"]


def _lines(path: str) -> list[str] | None:
    return SOURCES[path].split("\n") if path in SOURCES else None


def _tasks(budget: int, groups: list[str] | None = None) -> list[dict[str, Any]]:
    receipt = FIXTURE["receipt"]
    if groups is not None:
        receipt = {**receipt, "survivor_groups": [group for group in receipt["survivor_groups"] if group["group_id"] in groups]}
    return mt.build_tasks(receipt, FIXTURE["document"], FIXTURE["automation"], FIXTURE["marker_requirements"], _lines,
                          budget=budget, mutants=FIXTURE["mutants"], product_paths=sorted(SOURCES))


def _group(group_id: str) -> dict[str, Any]:
    return next(group for group in FIXTURE["receipt"]["survivor_groups"] if group["group_id"] == group_id)


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _classes(task: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(mt.product_path(_group(group_id)) for group_id in task["group_ids"]))


def test_g_every_task_carries_its_mutated_classes_whole() -> None:
    tasks = _tasks(200_000)
    assert sorted(group_id for task in tasks for group_id in task["group_ids"]) == [f"MUT-{index:04d}" for index in range(1, 27)]
    for task in tasks:
        for number, path in enumerate(_classes(task), start=1):
            assert f"### [F{number}] {path} — класс целиком" in task["text"], task["label"]
            anchors = [anchor for anchor in mt.anchors(task["text"]) if anchor.startswith(f"F{number}:L")]
            assert len(anchors) == len(SOURCES[path].split("\n")), (task["label"], path)


@pytest.mark.parametrize("budget", [200_000, 100_000])
def test_g_tasks_fit_the_budget_beyond_their_classes(budget: int) -> None:
    for task in _tasks(budget):
        allowance = sum(_size(SOURCES[path]) for path in _classes(task))
        assert _size(task["text"]) <= budget + allowance, (task["label"], _size(task["text"]))


def test_g_mut_0001_with_50_cases_is_cut_to_the_budget() -> None:
    (task,) = _tasks(100_000, ["MUT-0001"])
    named = SOURCES["src/main/java/org/springframework/samples/petclinic/model/NamedEntity.java"]
    assert _size(task["text"]) <= 100_000 + _size(named)
    assert "Кейсы не вошли по бюджету: TC-" in task["text"]
    assert "Срезы методов не вошли по бюджету: org.springframework.samples.petclinic" in task["text"]
    assert any(anchor.startswith("TC-") for anchor in mt.anchors(task["text"]))
    assert any(anchor.startswith("T:L") for anchor in mt.anchors(task["text"]))
    # The group's own line is still an anchor: an answer can cite it.
    assert "MUT-0001:L48" in mt.anchors(task["text"])


def test_g_requirements_of_cases_cut_by_the_budget_stay_anchors() -> None:
    """B5b: a SPEC_GAP naming a requirement whose cases did not fit is accepted, not TRIAGE_REF_UNKNOWN."""
    (task,) = _tasks(100_000, ["MUT-0001"])
    group = _group("MUT-0001")
    defined = set(mt.anchors(task["text"]))
    assert {*group["requirement_ids"], *group["source_requirement_ids"]} <= defined
    included = {anchor for anchor in defined if anchor.startswith("TC-")}
    cases = {case["case_id"]: case for case in FIXTURE["document"]["test_cases"]}
    cut = sorted(set(group["requirement_ids"]) - {requirement for case in included & set(cases) for requirement in cases[case]["requirement_ids"]})
    assert cut, "the budget leaves some requirements without their cases"
    answer = {"groups": [{"group_id": "MUT-0001", "decision": "SPEC_GAP", "refs": ["MUT-0001:L48", cut[0]], "rationale": "Требование не задаёт формат.",
                          "question": "Каким должен быть toString?", "requirement_id": cut[0]}]}
    assert mt.validate(task, answer, FIXTURE["receipt"], FIXTURE["document"]) == []


def test_g_callers_come_in_when_the_budget_allows() -> None:
    # MUT-0002 Owner#addPet: PetController creates and updates pets through it.
    (task,) = _tasks(10_000_000, ["MUT-0002"])
    assert "не вошли по бюджету" not in task["text"]
    assert "### [F2] src/main/java/org/springframework/samples/petclinic/owner/PetController.java — вызыватели" in task["text"]
    assert "owner.addPet(pet);" in task["text"]
    (small,) = _tasks(1, ["MUT-0002"])
    assert "Вызыватели не вошли по бюджету:" in small["text"] and "— вызыватели" not in small["text"]
    assert "класс целиком" in small["text"] and any(anchor.startswith("TC-") for anchor in mt.anchors(small["text"]))


def test_the_triage_brief_says_the_class_is_given_whole() -> None:
    assert "Код мутируемого класса дан целиком" in mt.INSTRUCTIONS
    assert "знания проекта вне входа не используй" in mt.INSTRUCTIONS
    skill = (ROOT / "skills" / "mutation-triage" / "SKILL.md").read_text(encoding="utf-8")
    assert "the mutated class whole" in skill and "не вошли по бюджету" in skill


@pytest.mark.parametrize(("config", "budget"), [({"review_input_bytes": 200_000}, 200_000), ({}, mt.TASK_BUDGET_BYTES)])
def test_the_triage_budget_is_the_review_input_budget(config: dict[str, Any], budget: int) -> None:
    from tools.pipeline_driver_strength import triage_budget

    assert triage_budget(config) == budget
