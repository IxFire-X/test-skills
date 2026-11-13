"""S1: the human's consent to a review in the same session (SELF) is given at the step of the review — ``prepare … --fresh-context no --accept-self-review`` —
not by a repeated ``scan``.  The consent is one mark of the run (``self-review-accepted.json`` and its pair of events), written before the review it belongs to."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_autotest_cycle import GENERATOR, REVIEWER, autotest_run, boundary_of, run_generator
from tests.helpers import prepare_stage
from tests.test_profile_flows import mode_b_pack  # noqa: F401  (the fixture: a copy of the delivery of mode B)
from tests.test_review_stage import STAGE, _answer, _prepare, _run, _submit
from tests.test_stage_steps import _call, _events, _status
from tools import pilot_state

FLAG = "--accept-self-review"
SAME_SESSION = ("--fresh-context", "no")
MARK = "self-review-accepted.json"


def _mark_events(root: Path) -> list[str]:
    return [row["event_type"] for row in pilot_state.derive_state(root)["events"] if row.get("batch_id") == "self-review-v1"]


def _journal(root: Path) -> list[str]:
    return [f"{row['event_type']}:{row.get('stage_instance_id')}:{row.get('batch_id')}" for row in pilot_state.derive_state(root)["events"] if row["event_type"] != "COMMAND_REFUSED"]


# ---- the review of the cases ----

def test_the_same_session_without_the_flag_is_refused_and_the_fix_names_prepare_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    root, before = run["run_root"], _journal(run["run_root"])
    code, out = _prepare(capsys, run, *SAME_SESSION)
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and _journal(root) == before and not (root / MARK).exists()
    assert FLAG in out["fix"] and (out["fix"].find("scan") == -1 or out["fix"].find(FLAG) < out["fix"].find("scan")), out["fix"]


def test_with_the_flag_the_review_opens_in_the_same_session_and_the_consent_is_one_mark_of_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    root = run["run_root"]
    code, out = _prepare(capsys, run, *SAME_SESSION, FLAG)
    assert code == 0 and out["result"]["answer_slot"], out
    ledger = pilot_state.read_reviewer_session_ledger(root, run["attempt_id"])
    assert ledger["host_isolation"]["fresh_context"] is False and ledger["host_isolation"]["distinct_invocations"] is False
    assert (root / MARK).is_file() and _mark_events(root) == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
    assert pilot_state.self_review_accepted(root) is True and _status(root)["self_review_accepted"] is True


def test_the_consent_comes_before_the_review_it_belongs_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    _prepare(capsys, run, *SAME_SESSION, FLAG)
    events = pilot_state.derive_state(run["run_root"])["events"]
    first = next(row["seq"] for row in events if row.get("batch_id") == "self-review-v1")
    assert first < next(row["seq"] for row in events if row["event_type"] == "REVIEW_REQUESTED" and row["stage_instance_id"] == STAGE)


def test_a_repeat_with_and_without_the_flag_writes_nothing_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    _prepare(capsys, run, *SAME_SESSION, FLAG)
    before = _journal(run["run_root"])
    assert _prepare(capsys, run, *SAME_SESSION, FLAG)[0] == 0 and _prepare(capsys, run, *SAME_SESSION)[0] == 0
    assert _journal(run["run_root"]) == before


@pytest.mark.parametrize("flags", [("--fresh-context", "yes", "--invocation-id", "reviewer-subagent-1"), ()])
def test_the_flag_needs_the_statement_no_and_writes_nothing_otherwise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, flags: tuple) -> None:
    run = _run(tmp_path, monkeypatch)
    before = _journal(run["run_root"])
    code, out = _prepare(capsys, run, *flags, FLAG)
    assert code == 2 and out["code"] == "INVALID_ARGUMENT" and out["fix"] and _journal(run["run_root"]) == before and not (run["run_root"] / MARK).exists()


def test_the_flag_on_a_stage_that_opens_no_review_is_refused_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    before = _journal(run["run_root"])
    code, out = _call(capsys, "prepare", run["run_root"], "plan", *SAME_SESSION, FLAG)
    assert code == 2 and out["code"] == "INVALID_ARGUMENT" and _journal(run["run_root"]) == before and not (run["run_root"] / MARK).exists()


def test_a_mark_file_put_by_hand_without_its_events_is_no_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    root = run["run_root"]
    (root / MARK).write_text(json.dumps({"attempt_id": run["attempt_id"], "reason": "SELF_REVIEW_ACCEPTED"}, sort_keys=True), encoding="utf-8")
    assert pilot_state.self_review_accepted(root) is False
    assert _prepare(capsys, run, *SAME_SESSION)[1]["code"] == "INDEPENDENCE_UNVERIFIED"


def test_the_review_in_the_same_session_is_named_self_and_never_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    _prepare(capsys, run, *SAME_SESSION, FLAG)
    code, out = _submit(capsys, run, _answer(run))
    assert code == 0 and out["result"]["independence"] == "independence_unverified"
    evidence = pilot_state.terminal_reviewer_evidence(run["run_root"], run["attempt_id"])
    assert evidence["isolation"] == "independence_unverified" and evidence["self_accepted"] is True


def test_an_independent_review_opened_before_is_not_changed_by_a_later_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    assert _prepare(capsys, run)[0] == 0
    _submit(capsys, run, _answer(run))
    evidence = pilot_state.terminal_reviewer_evidence(run["run_root"], run["attempt_id"])
    assert evidence["isolation"] == "verified" and "self_accepted" not in evidence


# ---- the second round ----

def test_the_second_round_goes_in_the_same_session_without_the_flag_after_the_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tests.test_rework_review import REVIEW, _answered, _open
    from tests.test_review_canaries import _boundary_and_removed, _finding, _ids
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    run = _run(tmp_path, monkeypatch)
    assert _prepare(capsys, run, *SAME_SESSION, FLAG)[0] == 0
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [_ids(run)[0]["case_id"]], "Нет проверки состояния.")]
    assert _submit(capsys, run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))[1]["result"]["status"] == "REWORK_PENDING"
    _answered(run, capsys)
    before = _mark_events(run["run_root"])
    code, out = _open(run, capsys, *SAME_SESSION)
    assert code == 0 and out["result"]["answer_slot"].endswith("tc-reviewer-rework-1.json") and _mark_events(run["run_root"]) == before
    assert REVIEW in _events(run["run_root"])[-1]


# ---- the review of the automation ----

def test_the_automation_review_needs_the_consent_too_and_a_refusal_writes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _journal(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, *SAME_SESSION)
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and _journal(run["root"]) == before
    code, out = prepare_stage(run["root"], REVIEWER, *SAME_SESSION, FLAG)
    assert code == 0 and not boundary_of(run)["host_isolation"]["fresh_context"] and (run["root"] / MARK).is_file()


def test_the_flag_before_the_generator_has_answered_is_refused_before_the_consent_is_written(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    code, out = prepare_stage(run["root"], REVIEWER, *SAME_SESSION, FLAG)
    assert code == 2 and out["code"] in ("STAGE_WAITS", "STAGE_UNKNOWN") and not (run["root"] / MARK).exists() and _mark_events(run["root"]) == []


# ---- the whole path ----

def test_a_run_gives_the_consent_at_the_cases_review_and_goes_to_the_report_without_a_refusal(tmp_path: Path, mode_b_pack: Path) -> None:
    from tests import test_profile_flows as flows
    from tests.test_autotest_cycle import GENERATOR, REVIEWER
    from tests.test_automation_revision_budget import review
    from tests.test_review_two_commands import _verdict
    from tools.run_pipeline import _durable_status

    pack, front = mode_b_pack, flows._front(mode_b_pack, tmp_path, "local-pilot-v1")
    root, attempt, project = front["run_root"], front["attempt_id"], front["project"]
    assert not pilot_state.self_review_accepted(root)

    def step(command: str, stage: str, *flags: str) -> dict:
        code, out = flows._api(pack, command, "--run-root", str(root), "--stage", stage, *flags)
        assert code == 0, (command, stage, out)
        return out["result"]

    plan = json.loads(Path(front["plan"]).read_bytes())
    for batch, fragment in zip(plan["batches"], front["fragments"]):
        stage = f"tc-generator:{batch['batch_id']}"
        Path(step("prepare", stage, "--model-id", "model-generator")["answer_slot"]).write_bytes(Path(fragment).read_bytes())
        step("submit", stage)
    Path(step("prepare", "tc-reviewer:canonical", *SAME_SESSION, FLAG)["answer_slot"]).write_bytes(Path(_verdict(front)).read_bytes())
    step("submit", "tc-reviewer:canonical")
    effective = pilot_state.read_effective_canonical(root, attempt)
    artifact = flows._automation_answer(json.loads((front["tmp"] / "candidate.json").read_bytes()), effective)
    Path(step("prepare", GENERATOR, "--model-id", "model-automation")["answer_slot"]).write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    step("submit", GENERATOR)
    slot = step("prepare", REVIEWER, *SAME_SESSION)["answer_slot"]  # the consent is given: no flag again
    record = dict(pilot_state.read_attempt_receipt(root, attempt, "automation-review-boundary-r1", "ARTIFACT_READ_BACK")["record"])
    Path(slot).write_text(json.dumps(review(effective["document"], artifact, record, "ПРИНЯТО", session_id=record["reviewer_session_id"]), ensure_ascii=False), encoding="utf-8")
    step("submit", REVIEWER)
    assert _durable_status(project, root, stage="status")["next"]["verb"] == "materialize"
    code, _report = flows._cli(pack, "tools.run_pipeline", "report", "--project", str(project), "--run", root.name)
    shown = json.loads((root / "report.json").read_text(encoding="utf-8"))["review"]
    assert code == 0 and "автотесты проверены в той же сессии" in shown and "независимость не засчитана" in shown
    assert not [row for row in pilot_state.derive_state(root)["events"] if row["event_type"] == "COMMAND_REFUSED"]
