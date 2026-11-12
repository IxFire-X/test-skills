"""V1: the machine of stages — ``next_action(facts)`` names the state, who waits and the one next verb of the run.

Expectations are written by scenario (what the agent sees at that point of the run), not derived from a table of the code."""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from tools import stages

pytestmark = pytest.mark.contract

OPEN = {"attempt_id": "a1"}


def _next(expected: object, **facts: object) -> dict:
    return stages.next_action({**OPEN, "next_expected_artifact": expected, **facts})


def _shape(result: dict) -> tuple:
    return result["state"], result["waiting_for"], result["next"]["verb"], result["next"]["stage"]


@pytest.mark.parametrize(("name", "expected", "facts", "shape"), [
    ("after the scan the marker is next", "context-marker response", {}, ("marker", "agent", "prepare", "context-marker")),
    ("marker in parts: the next part is asked", "context-marker parts merge", {}, ("marker", "agent", "prepare", "context-marker")),
    ("the marker answered: the plan of the batches", "generator fragments", {}, ("generator", "agent", "plan", "tc-generator")),
    ("the marker has gaps: the human answers", "generator fragments", {"scope_pause": True}, ("scope_pause", "human", "prepare", "scope")),
    ("the human's answer lies in the slot", "generator fragments", {"scope_pause": True, "scope_answer": True}, ("scope_pause", "agent", "submit", "scope")),
    ("a batch answered, not the last", "remaining fragments or assembled candidate", {}, ("generator", "agent", "prepare", "tc-generator")),
    ("the candidate is built: the review opens", "canonical reviewer package and boundary", {}, ("review", "agent", "review-open", "tc-reviewer")),
    ("the reviewer is awaited", "reviewer evidence or verdict", {}, ("review", "agent", "review-select", "tc-reviewer")),
    ("cases-only: the set is accepted, the run closes", "pre-finalization trace", {}, ("closure", "agent", "finish", None)),
    ("with autotests: the set is accepted, the automation", "automation artifact", {}, ("automation", "agent", "model-request", "tc-to-autotest")),
    ("the set is rejected", "negative branch trace and finalization", {}, ("closure", "agent", "finish", None)),
    ("the autotests are written: their review", "static automation review", {}, ("automation_review", "agent", "open-automation-boundary", "autotest-reviewer")),
    ("the autotest review accepted: written into the project", "review disposition and materialization", {}, ("materialize", "agent", "materialize", None)),
    ("the autotest review asks a revision", "automation revision", {}, ("automation", "agent", "model-request", "tc-to-autotest")),
    ("the autotest review refused", "automation refused", {}, ("closure", "agent", "finish", None)),
    ("written into the project: the human runs it", "human run report", {}, ("human_run", "human", "ingest", None)),
    ("the reports are taken in", "execution trace and dispositions", {}, ("closure", "agent", "finish", None)),
    ("the run is closed: the report", None, {}, ("done", "agent", "report", None)),
    ("the files changed since the scan", "validated inputs or new scan", {}, ("drift", "human", "scan", None)),
    ("a run file is damaged", "validated inputs or new scan", {"stop_reason": "EVIDENCE_UNVERIFIED"}, ("drift", "human", "scan", None)),
    ("an incomplete scan is repeated", "context-marker response", {"scan_cut": "1 из 3"}, ("scan", "agent", "scan", None)),
    ("a left run starts anew", "generator fragments", {"left": True}, ("left", "agent", "scan", None)),
])
def test_a_transition(name: str, expected: object, facts: dict, shape: tuple) -> None:
    assert _shape(_next(expected, **facts)) == shape, name


def test_a_run_without_an_attempt_waits_for_the_scan() -> None:
    assert _shape(stages.next_action({"attempt_id": None})) == ("scan", "agent", "scan", None)


@pytest.mark.parametrize(("stage_id", "state", "role"), [
    ("context-marker:baseline", "marker", "context-marker"), ("tc-generator:BATCH-1", "generator", "tc-generator"),
    ("tc-reviewer:canonical", "review", "tc-reviewer"), ("tc-to-autotest:r1", "automation", "tc-to-autotest"),
    ("autotest-reviewer:r1", "automation_review", "autotest-reviewer"),
])
def test_a_pending_request_waits_for_the_model(stage_id: str, state: str, role: str) -> None:
    result = _next(f"{stage_id} response", pending_request_path="x")
    assert _shape(result) == (state, "model", "submit" if state == "marker" else "model-response", role)


def test_an_exact_value_is_never_taken_for_a_model_response() -> None:
    assert _next("context-marker response")["waiting_for"] == "agent"


def test_a_closed_run_wins_over_the_drift_of_the_project() -> None:
    assert _shape(_next(None, drift={"changed": ["a"]})) == ("done", "agent", "report", None)


def test_a_left_run_wins_over_the_drift_of_the_project() -> None:
    assert _next("generator fragments", left=True, drift={"changed": ["a"]})["state"] == "left"


def test_the_drift_of_the_project_makes_the_state() -> None:
    assert _next("remaining fragments or assembled candidate", drift={"changed": ["a"]})["state"] == "drift"


def test_the_two_causes_of_drift_have_each_its_own_recovery() -> None:
    files = _next("validated inputs or new scan", drift={"changed": ["a"]})["recovery"]
    run = _next("validated inputs or new scan", stop_reason="EVIDENCE_UNVERIFIED")["recovery"]
    assert "файлы проекта" in files and "evidence_errors" not in files
    assert "evidence_errors" in run and files != run


def test_a_changed_file_of_the_project_is_named_even_when_the_evidence_fails_with_it() -> None:
    both = _next("validated inputs or new scan", drift={"changed": ["a"]}, stop_reason="EVIDENCE_UNVERIFIED")
    assert "файлы проекта" in both["recovery"]


def test_the_drift_of_the_project_says_so_without_the_old_value() -> None:
    assert "recovery" in _next("remaining fragments or assembled candidate", drift={"changed": ["a"]})


def test_an_unknown_value_is_a_state_with_a_recovery_not_an_exception() -> None:
    result = _next("something the code never sets")
    assert _shape(result) == ("unknown", "human", None, None) and result["recovery"]


def test_an_unknown_stage_of_a_response_is_unknown() -> None:
    assert _next("mystery:1 response", pending_request_path="x")["state"] == "unknown"


def _artifacts_set_by_the_status() -> set[str]:
    """Every literal ``next_expected_artifact`` that ``_durable_status_facts`` can set, read from its source."""
    from tools import run_pipeline

    tree = ast.parse(textwrap.dedent(inspect.getsource(run_pipeline._durable_status_facts)))
    found: set[str] = set()

    def strings(node: ast.AST | None) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.IfExp):
            strings(node.body), strings(node.orelse)
        elif isinstance(node, ast.Dict):
            for value in node.values:
                strings(value)
        elif isinstance(node, ast.Subscript):
            strings(node.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "next_expected_artifact":
            strings(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "next_expected_artifact":
                    strings(value)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "next_artifact" for target in node.targets):
            strings(node.value)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and any(isinstance(item, ast.Name) and item.id == "next_artifact" for item in node.target.elts):
            index = [getattr(item, "id", None) for item in node.target.elts].index("next_artifact")
            for row in node.iter.elts:
                strings(row.elts[index])
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "record" and len(node.args) >= 3:
            strings(node.args[2])
    return found


def test_every_value_the_status_can_set_has_a_known_state() -> None:
    artifacts = _artifacts_set_by_the_status()
    assert {"authorized context", "context-marker response", "generator fragments", "static automation review"} <= artifacts
    assert len(artifacts) >= 24
    for expected in sorted(artifacts):
        assert _next(expected)["state"] not in {"unknown"}, expected


def test_the_answers_come_from_the_closed_lists() -> None:
    for expected in [*sorted(_artifacts_set_by_the_status()), None]:
        result = _next(expected)
        assert result["state"] in stages.STAGES and result["waiting_for"] in {"agent", "model", "human"}
        assert result["next"]["verb"] in stages.VERBS and result["next"]["stage"] in {None, "scope", *stages.ROLES}
