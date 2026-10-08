"""Survivor triage (wave 2, T): task inputs, reference checks, decisions, driver integration."""
from __future__ import annotations

import copy
import gzip
from pathlib import Path

import pytest

from tests.live_step5 import LIVE, outputs, review_state
from tools import mutation, mutation_triage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mutation"


def _inputs() -> tuple[dict, dict, dict, list]:
    payload = review_state("9340016c", "review-snapshot-r1")["payload"]
    automation, document = payload["automation"], payload["document"]
    mutants = mutation.parse_report(gzip.decompress((FIXTURES / "step5-9340016c-mutations.xml.gz").read_bytes()))
    receipt = {"status": "MEASURED", **mutation.attribute(mutants, automation, document)}
    marker = outputs("9340016c")["context-marker"]["artifacts"]["analytics_documentation"]["requirements"]
    return receipt, document, automation, marker


def _lines(path: str):
    target = LIVE / "project" / path
    return target.read_text(encoding="utf-8").split("\n") if target.is_file() else None


def _tasks(**kwargs) -> tuple[list, dict, dict]:
    receipt, document, automation, marker = _inputs()
    return mutation_triage.build_tasks(receipt, document, automation, marker, _lines, **kwargs), receipt, document


def _answer(task: dict, receipt: dict, decision: str = "EQUIVALENT") -> dict:
    groups = {group["group_id"]: group for group in receipt["survivor_groups"]}
    rows = []
    for group_id in task["group_ids"]:
        group = groups[group_id]
        row = {"group_id": group_id, "decision": decision, "refs": [f"{group_id}:L{group['line']}"], "rationale": "Удалён отладочный вывод в stdout."}
        if decision == "TEST_GAP":
            row["proposal"] = {"case_id": group["case_ids"][0], "text": "Добавить проверку."}
        if decision == "SPEC_GAP":
            row.update(question="Нужно ли что-то печатать?", requirement_id=group["requirement_ids"][0])
        rows.append(row)
    return {"groups": rows}


def test_tasks_carry_every_pending_group_once_with_its_anchors() -> None:
    tasks, receipt, _document = _tasks()
    pending = [group["group_id"] for group in receipt["survivor_groups"] if group["triage"]]
    assert [group for task in tasks for group in task["group_ids"]] == pending and pending
    text = tasks[0]["text"]
    defined = mutation_triage.anchors(text)
    assert len(defined) == len(set(defined))
    for group in receipt["survivor_groups"]:
        assert f"{group['group_id']}:L{group['line']}" in defined  # the mutated product line is shown
        assert f"[{group['group_id']}:L{group['line']}] >" in text
        assert all(case in defined for case in group["case_ids"]) and all(req in defined for req in group["requirement_ids"])
        assert all(source in defined for source in group["source_requirement_ids"])
    assert any(anchor.startswith("T:L") for anchor in defined)  # slices of the covering methods
    assert mutation_triage.build_tasks(*_inputs(), _lines) == tasks  # deterministic


def test_a_small_budget_splits_groups_into_several_tasks() -> None:
    tasks, receipt, _document = _tasks(budget=1)
    assert [task["group_ids"] for task in tasks] == [[group["group_id"]] for group in receipt["survivor_groups"] if group["triage"]]


@pytest.mark.parametrize("decision", mutation_triage.DECISIONS)
def test_each_decision_is_accepted_with_its_fields(decision: str) -> None:
    tasks, receipt, document = _tasks()
    assert mutation_triage.validate(tasks[0], _answer(tasks[0], receipt, decision), receipt, document) == []


def test_answers_that_do_not_look_or_point_outside_are_rejected() -> None:
    tasks, receipt, document = _tasks()
    task = tasks[0]

    def codes(answer: dict) -> set[str]:
        return {row["code"] for row in mutation_triage.validate(task, answer, receipt, document)}

    answer = _answer(task, receipt)
    unknown = copy.deepcopy(answer)
    unknown["groups"][0]["refs"] = ["TC-B1-999"]
    assert "TRIAGE_REF_UNKNOWN" in codes(unknown)
    blind = copy.deepcopy(answer)
    blind["groups"][0]["refs"] = [receipt["survivor_groups"][0]["case_ids"][0]]  # an anchor, but not the group's own line
    assert codes(blind) == {"TRIAGE_REF_FOREIGN"}
    assert len(task["group_ids"]) > 1  # the task really has a group to leave out
    missing = copy.deepcopy(answer)
    missing["groups"] = missing["groups"][1:]
    assert "TRIAGE_GROUPS" in codes(missing)
    reordered = copy.deepcopy(answer)
    reordered["groups"] = reordered["groups"][::-1]
    assert "TRIAGE_GROUPS" in codes(reordered)
    none = copy.deepcopy(answer)
    none["groups"] = []
    assert codes(none)
    foreign = _answer(task, receipt, "TEST_GAP")
    foreign["groups"][0]["proposal"]["case_id"] = "TC-B1-002"
    assert "TRIAGE_CASE_FOREIGN" in codes(foreign)
    step = _answer(task, receipt, "TEST_GAP")
    step["groups"][0]["proposal"]["step_id"] = "STEP-B1-002-01"
    assert codes(step) == {"TRIAGE_STEP_FOREIGN"}
    question = _answer(task, receipt, "SPEC_GAP")
    question["groups"][0]["requirement_id"] = "CREQ-B1-999"
    assert codes(question) == {"TRIAGE_REQUIREMENT_FOREIGN"}
    shape = _answer(task, receipt, "SPEC_GAP")
    del shape["groups"][0]["question"]
    assert codes(shape)  # schema: SPEC_GAP needs a question


def test_decisions_feed_proposals_and_counts() -> None:
    tasks, receipt, _document = _tasks()
    rows = mutation_triage.decision_rows([_answer(tasks[0], receipt, "TEST_GAP")])
    assert mutation_triage.counts(rows)["TEST_GAP"] == len(tasks[0]["group_ids"])
    proposals = mutation_triage.proposals(rows)
    assert {row["case_id"] for row in proposals} <= {case for group in receipt["survivor_groups"] for case in group["case_ids"]}
