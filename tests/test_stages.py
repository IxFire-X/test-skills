"""V1: the machine of stages — ``next_action(facts)`` names the state, who waits and the one next verb of the run.

Expectations are written by scenario (what the agent sees at that point of the run), not derived from a table of the code."""
from __future__ import annotations

import pytest

from tools import stages

pytestmark = pytest.mark.contract

OPEN = {"attempt_id": "a1"}
STEPS = (stages.SCAN, stages.MARKER, stages.GENERATOR, stages.REVIEW, stages.REVIEW_ANSWER, stages.AUTOMATION, stages.AUTOMATION_REVIEW,
         stages.MATERIALIZE, stages.HUMAN_RUN, stages.CLOSURE, stages.DRIFT, stages.UNKNOWN)


def _next(step: object, **facts: object) -> dict:
    return stages.next_action({**OPEN, "next_step": step, **facts})


def _shape(result: dict) -> tuple:
    return result["state"], result["waiting_for"], result["next"]["verb"], result["next"]["stage"]


@pytest.mark.parametrize(("name", "step", "facts", "shape"), [
    ("after the scan the marker is next", stages.MARKER, {}, ("marker", "agent", "prepare", "context-marker")),
    ("the marker answered: the plan of the batches", stages.GENERATOR, {"after_marker": True}, ("generator", "agent", "prepare", "tc-generator")),
    ("the marker has gaps: the human answers", stages.GENERATOR, {"after_marker": True, "scope_pause": True}, ("scope_pause", "human", "prepare", "scope")),
    ("the human's answer lies in the slot", stages.GENERATOR, {"after_marker": True, "scope_pause": True, "scope_answer": True}, ("scope_pause", "agent", "submit", "scope")),
    ("a batch answered, not the last", stages.GENERATOR, {}, ("generator", "agent", "prepare", "tc-generator")),
    ("the candidate is built: the review opens", stages.REVIEW, {}, ("review", "agent", "prepare", "tc-reviewer")),
    ("the reviewer is awaited", stages.REVIEW_ANSWER, {}, ("review", "model", "submit", "tc-reviewer")),
    ("the set is accepted or rejected: the run closes", stages.CLOSURE, {}, ("closure", "agent", "finish", None)),
    ("with autotests: the set is accepted, the automation", stages.AUTOMATION, {}, ("automation", "agent", "prepare", "tc-to-autotest")),
    ("the autotests are written: their review", stages.AUTOMATION_REVIEW, {}, ("automation_review", "agent", "prepare", "autotest-reviewer")),
    ("the autotest review accepted: written into the project", stages.MATERIALIZE, {}, ("materialize", "agent", "materialize", None)),
    ("written into the project: the human runs it", stages.HUMAN_RUN, {}, ("human_run", "human", "ingest", None)),
    ("the run is closed: the report", None, {}, ("done", "agent", "report", None)),
    ("the files changed since the scan", stages.DRIFT, {}, ("drift", "human", "scan", None)),
    ("a run file is damaged", stages.DRIFT, {"stop_reason": "EVIDENCE_UNVERIFIED"}, ("drift", "human", "scan", None)),
    ("an incomplete scan is repeated", stages.MARKER, {"scan_cut": "1 из 3"}, ("scan", "agent", "scan", None)),
    ("a left run starts anew", stages.GENERATOR, {"left": True}, ("left", "agent", "scan", None)),
])
def test_a_transition(name: str, step: object, facts: dict, shape: tuple) -> None:
    assert _shape(_next(step, **facts)) == shape, name


def test_a_run_without_an_attempt_waits_for_the_scan() -> None:
    assert _shape(stages.next_action({"attempt_id": None})) == ("scan", "agent", "scan", None)


@pytest.mark.parametrize(("stage_id", "state", "role"), [
    ("context-marker:baseline", "marker", "context-marker"), ("tc-generator:BATCH-1", "generator", "tc-generator"),
    ("tc-reviewer:canonical", "review", "tc-reviewer"), ("tc-to-autotest:r1", "automation", "tc-to-autotest"),
    ("autotest-reviewer:r1", "automation_review", "autotest-reviewer"),
])
def test_a_pending_request_waits_for_the_model(stage_id: str, state: str, role: str) -> None:
    result = _next(stages.answer_of(stage_id), pending_request_path="x")
    assert _shape(result) == (state, "model", "submit", role)


def test_a_closed_run_wins_over_the_drift_of_the_project() -> None:
    assert _shape(_next(None, drift={"changed": ["a"]})) == ("done", "agent", "report", None)


def test_a_left_run_wins_over_the_drift_of_the_project() -> None:
    assert _next(stages.GENERATOR, left=True, drift={"changed": ["a"]})["state"] == "left"


def test_the_drift_of_the_project_makes_the_state() -> None:
    assert _next(stages.GENERATOR, drift={"changed": ["a"]})["state"] == "drift"


def test_the_two_causes_of_drift_have_each_its_own_recovery() -> None:
    files = _next(stages.DRIFT, drift={"changed": ["a"]})["recovery"]
    run = _next(stages.DRIFT, stop_reason="EVIDENCE_UNVERIFIED")["recovery"]
    assert "файлы проекта" in files and "evidence_errors" not in files
    assert "evidence_errors" in run and files != run


def test_a_changed_file_of_the_project_is_named_even_when_the_evidence_fails_with_it() -> None:
    both = _next(stages.DRIFT, drift={"changed": ["a"]}, stop_reason="EVIDENCE_UNVERIFIED")
    assert "файлы проекта" in both["recovery"]


def test_the_drift_of_the_project_says_so_without_the_old_value() -> None:
    assert "recovery" in _next(stages.GENERATOR, drift={"changed": ["a"]})


def test_an_unknown_stage_of_a_response_is_unknown() -> None:
    result = _next(stages.answer_of("mystery:1"), pending_request_path="x")
    assert _shape(result) == ("unknown", "human", None, None) and result["recovery"]


def test_the_answers_come_from_the_closed_lists() -> None:
    for step in [*STEPS, None]:
        result = _next(step)
        assert result["state"] in stages.STAGES and result["waiting_for"] in {"agent", "model", "human"}
        assert result["next"]["verb"] in stages.VERBS and result["next"]["stage"] in {None, "scope", *stages.ROLES}
