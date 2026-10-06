"""R3.1, Р10: the review linter raises suspicions only, and only where something is off.

* the four live step5 candidates and both Petclinic documents raise no suspicion;
* the seeded defects of the 2026-10-06 experiment are flagged by the rule of their class
  (literal ≠ text, same call with different expectations, a list that grows after a
  mutation, a requirement without a case); defects that need the source (changed text
  of a response, a wrong component) are left to the reviewer;
* a result the capability does not return is flagged;
* a suspicion never becomes a finding by itself (the aggregate takes findings from
  answers only).
"""
from __future__ import annotations

import copy
import json

import pytest

from tests.live_step5 import RUNS, review_state
from tests.review_scaling_helpers import eval_data
from tools.review_lint import lint_document


def _step5(run: str = "9340016c") -> dict:
    return copy.deepcopy(review_state(run, "review-snapshot-canonical")["payload"]["document"])


@pytest.mark.parametrize("run", sorted(RUNS))
def test_live_step5_candidates_raise_no_suspicion(run: str) -> None:
    assert lint_document(_step5(run)) == []


def test_petclinic_documents_raise_no_suspicion() -> None:
    assert lint_document(eval_data.petclinic_document()) == []
    assert lint_document(eval_data.petclinic_b7733d39_canonical()) == []


def _cases(document: dict) -> dict:
    return {case["case_id"]: case for case in document["test_cases"]}


def _rules(document: dict) -> set[tuple[str, tuple[str, ...]]]:
    return {(row["rule"], tuple(row["case_ids"])) for row in lint_document(document)}


def test_assertion_literal_differing_from_the_expectation_text() -> None:
    document = _step5()
    _cases(document)["TC-B1-005"]["steps"][0]["expectations"][0]["assertions"][1]["expected"]["value"]["id"] = 8
    assert ("expectation-text-differs", ("TC-B1-005",)) in _rules(document)
    document = _step5()
    _cases(document)["TC-B1-006"]["steps"][0]["expectations"][0]["assertions"][0]["expected"]["value"] = 404
    assert ("expectation-text-differs", ("TC-B1-006",)) in _rules(document)


def test_same_call_with_a_different_expectation() -> None:
    document = _step5()
    expectation = _cases(document)["TC-B1-004"]["steps"][0]["expectations"][0]
    expectation["text"] = expectation["text"].replace("Ramsesh", "Ramesh")
    expectation["assertions"][1]["expected"]["value"]["firstName"] = "Ramesh"
    assert ("same-call-different-expectations", ("TC-B1-003", "TC-B1-004")) in _rules(document)


def test_a_list_that_grows_after_a_mutation_in_another_case() -> None:
    document = _step5()
    case = _cases(document)["TC-B1-012"]
    five = [{"firstName": "Ramesh", "id": 1, "lastName": "Mishra"}, {"firstName": "Umesh", "id": 2, "lastName": "Mishra"},
            {"firstName": "Ram", "id": 3, "lastName": "Mishra"}, {"firstName": "Sanjay", "id": 4, "lastName": "Mishra"},
            {"firstName": "Pavel", "id": 9, "lastName": "Orlov"}]
    for step in case["steps"][1:]:
        expectation = step["expectations"][0]
        expectation["text"] = "В списке пять студентов.\n\nHTTP 200 OK\n\n" + json.dumps(five, ensure_ascii=False, indent=2)
        expectation["assertions"][1]["expected"]["value"] = five
    rules = _rules(document)
    assert ("count-of-shared-resource", ("TC-B1-002", "TC-B1-012")) in rules


def test_a_requirement_left_without_a_case() -> None:
    document = _step5()
    document["test_cases"] = [case for case in document["test_cases"] if case["case_id"] != "TC-B1-011"]
    rows = [row for row in lint_document(document) if row["rule"] == "requirement-without-case"]
    assert rows and all(row["case_ids"] == [] for row in rows)


def test_a_result_the_capability_does_not_return() -> None:
    document = _step5()
    _cases(document)["TC-B1-001"]["steps"][0]["expectations"][0]["assertions"][0]["actual"]["name"] = "statusCode"
    assert ("result-not-in-capability", ("TC-B1-001",)) in _rules(document)


def test_text_only_and_component_defects_are_left_to_the_reviewer() -> None:
    document = _step5()
    expectation = _cases(document)["TC-B1-010"]["steps"][0]["expectations"][0]
    expectation["text"] = expectation["text"].replace("Student Successfully Deleted!", "Student Deleted!")
    expectation["assertions"][1]["expected"]["value"] = "Student Deleted!"
    _cases(document)["TC-B1-010"]["management"]["components"] = ["HelloWorldController"]
    assert lint_document(document) == []


def test_suspicion_ids_are_stable_when_another_case_changes() -> None:
    document = _step5()
    _cases(document)["TC-B1-005"]["steps"][0]["expectations"][0]["assertions"][1]["expected"]["value"]["id"] = 8
    before = {row["lint_id"] for row in lint_document(document)}
    _cases(document)["TC-B1-001"]["title"] += " (уточнено)"
    assert {row["lint_id"] for row in lint_document(document)} == before
