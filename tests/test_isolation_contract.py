"""P2 (item 7, Б-13): the contract says what the code does about the independence of the reviewer.

The code accepts a review whose isolation is verified *or* admitted as SELF at scan (``--accept-self-review``, ``.skillsrc``
``review.self``); the contract named only ``reviewer_isolation_verified`` for both profiles.  One name for the rule, in the
contract, in the checker that compares it, in the rendered document, and the behaviour it names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
PREDICATE = "reviewer_isolation_verified_or_self_admitted"
OLD = "reviewer_isolation_verified"


def test_the_contract_names_the_isolation_rule_the_code_applies() -> None:
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    for profile, names in contract["acceptance_predicates"].items():
        assert PREDICATE in names and OLD not in names, (profile, names)


def test_the_checker_expects_the_same_name() -> None:
    from tools import contract_check

    for profile, names in contract_check.EXPECTED_ACCEPTANCE.items():
        assert PREDICATE in names and OLD not in names, (profile, names)


def test_the_rendered_document_says_it_too() -> None:
    text = (ROOT / "PIPELINE.md").read_text(encoding="utf-8")
    assert PREDICATE in text and f"{OLD}," not in text and f"{OLD}`" not in text


def test_the_code_accepts_verified_or_self_admitted_and_no_other_isolation() -> None:
    """The behaviour the predicate states, by the result facts of local-pilot-v1: verified → accepted; unverified → not accepted;
    unverified but SELF-admitted → accepted, and marked SELF (a verified review never carries the mark)."""
    from tests.test_exit_policy import _local
    from tools.pilot_state import terminal_result

    base = _local()
    unverified = {**base, "reviewer_isolation_state": "independence_unverified"}
    admitted = {**unverified, "review_independence": "SELF"}
    assert terminal_result(base, "local-pilot-v1")["accepted"] is True
    assert terminal_result(unverified, "local-pilot-v1")["accepted"] is False
    marked = terminal_result(admitted, "local-pilot-v1")
    assert marked["accepted"] is True and marked["review_independence"] == "SELF"
    with pytest.raises(ValueError):
        terminal_result({**base, "review_independence": "SELF"}, "local-pilot-v1")


# --------------------------------------------------------------------------------------
# .skillsrc review.self: true — the project answers the SELF question once
# --------------------------------------------------------------------------------------

def _with_review_self(project: Path, value: object) -> None:
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["review"] = {"self": value}
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def test_the_skillsrc_review_self_admits_the_review_like_the_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_phase2_scan import _args
    from tools import run_pipeline
    from tools.skillsrc_manifest import skillsrc_review_self

    project = _python_project(tmp_path, runtime=False)
    _with_review_self(project, True)
    assert skillsrc_review_self(project) is True
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project)) == 0, payloads[-1]  # no --accept-self-review
    scan = payloads[-1]
    assert scan["self_review_accepted"] is True
    authorization = json.loads((Path(scan["run_root"]) / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    assert authorization["accept_self_review"] is True


def test_the_skillsrc_without_the_key_or_with_false_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_phase2_scan import _args
    from tools import run_pipeline
    from tools.skillsrc_manifest import skillsrc_review_self

    project = _python_project(tmp_path, runtime=False)
    assert skillsrc_review_self(project) is False
    _with_review_self(project, False)
    assert skillsrc_review_self(project) is False
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project)) == 0, payloads[-1]
    assert "self_review_accepted" not in payloads[-1]


def test_the_schema_takes_review_self_and_nothing_else_under_review(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc

    project = _python_project(tmp_path, runtime=False)
    _with_review_self(project, True)
    assert load_skillsrc(project / ".skillsrc")["review"] == {"self": True}
    for bad in ({"self": "yes"}, {"other": True}):
        path = project / ".skillsrc"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["review"] = bad
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(SkillsrcError):
            load_skillsrc(path)


def test_ready_says_the_question_is_answered_when_the_skillsrc_answers_it(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools import ready

    project = _python_project(tmp_path, runtime=False)
    plain = next(row for row in ready.check(project, [], "cases-only-v1")["checks"] if row["id"] == "review:isolation")
    assert "accept-self-review" in plain["details"]["self_review_scan"] and "fix" not in plain  # PT: the row is OK, nothing to fix
    _with_review_self(project, True)
    answered = next(row for row in ready.check(project, [], "cases-only-v1")["checks"] if row["id"] == "review:isolation")
    assert "review.self" in answered["message"] and "fix" not in answered and "details" not in answered


def test_a_second_scan_with_review_self_behaves_as_without_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The key changes who answers the SELF question, not what a repeated scan does: the same request again gives what it gives
    without the key (here the run in progress), and the first run stays the one that admitted SELF."""
    from tests.test_first_run import _python_project
    from tests.test_phase2_scan import _args
    from tools import run_pipeline

    def twice(root: Path, with_key: bool) -> tuple[dict, dict]:
        project = _python_project(root, runtime=False)
        if with_key:
            _with_review_self(project, True)
        payloads: list[dict] = []
        monkeypatch.setattr(run_pipeline, "_print", payloads.append)
        run_pipeline.cmd_scan(_args(project))
        first = payloads[-1]
        run_pipeline.cmd_scan(_args(project))
        return first, payloads[-1]

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first_plain, again_plain = twice(tmp_path / "a", False)
    first_key, again_key = twice(tmp_path / "b", True)
    assert first_key["self_review_accepted"] is True and "self_review_accepted" not in first_plain
    assert again_key.get("reason") == again_plain.get("reason") and again_key.get("status") == again_plain.get("status")
    if again_key.get("run_id"):
        assert again_key["run_id"] == first_key["run_id"]
