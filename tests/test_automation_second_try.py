"""V7e: the review of the automation that says ``ТРЕБУЕТ ДОРАБОТКИ`` leaves the agent one correction (r2), as ``AUTO_FIX_APPLIED`` does — and no more.

``finish`` instead of r2 closes the run as before (``AUTOMATION_REJECTED``); a third revision does not exist; r2 is not offered after ``ПРИНЯТО``."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import prepare_stage
from tests.test_autotest_cycle import GENERATOR, INDEPENDENT, REVIEWER, autotest_run, next_of, run_generator, run_reviewer
from tests.test_profile_flows import mode_b_pack  # noqa: F401  (the fixture: a copy of the delivery of mode B)
from tests.test_self_consent import FLAG, SAME_SESSION
from tools import pilot_state

REWORK = "ТРЕБУЕТ ДОРАБОТКИ"
SECOND = ("--invocation-id", "autotest-reviewer-subagent-2", "--fresh-context", "yes")  # one session reviews one revision


def _finish(run: dict, capsys, *flags: str) -> dict:
    from tools.run_pipeline import HostStop, cmd_finish

    try:
        cmd_finish(SimpleNamespace(project=str(run["root"].parents[1]), run=run["root"].name, abandon=flags[0] if flags else None))
    except HostStop as stop:
        capsys.readouterr()
        return {"code": stop.code}
    return json.loads(capsys.readouterr().out)


def test_a_review_that_requires_rework_offers_the_second_revision(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    assert next_of(run) == ("automation", "prepare", "tc-to-autotest:r2")


def test_the_second_revision_accepted_after_a_rework_verdict_leads_to_materialize(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    assert run_generator(run, 2)[0] == 0 and next_of(run) == ("automation_review", "prepare", "autotest-reviewer:r2")
    assert run_reviewer(run, "ПРИНЯТО", 2, *SECOND)[0] == 0
    assert next_of(run)[:2] == ("materialize", "materialize")


def test_the_second_revision_that_the_review_rejects_is_the_last_one(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    run_generator(run, 2)
    run_reviewer(run, REWORK, 2, *SECOND)
    assert next_of(run)[:2] == ("closure", "finish")
    assert prepare_stage(run["root"], "tc-to-autotest:r3")[1]["code"] == "STAGE_UNKNOWN"


def test_finish_instead_of_the_second_revision_closes_as_before(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    closed = _finish(run, capsys)
    assert closed["reason_code"] == "AUTOMATION_REJECTED" and closed["accepted"] is False, closed


def test_after_finish_the_second_revision_is_offered_no_more_and_is_refused_without_a_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    assert _finish(run, capsys)["reason_code"] == "AUTOMATION_REJECTED"
    assert next_of(run)[1:] != ("prepare", "tc-to-autotest:r2")
    def written() -> list:  # the refusal itself is journaled (COMMAND_REFUSED); nothing else is
        return [row for row in pilot_state.derive_state(run["root"])["events"] if row["event_type"] != "COMMAND_REFUSED"]

    before = written()
    code, out = prepare_stage(run["root"], "tc-to-autotest:r2")
    assert code == 2 and out["code"] == "ATTEMPT_TERMINAL" and written() == before, out


def test_a_second_revision_that_was_taken_is_finished_or_given_up_with_the_code_of_the_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    assert prepare_stage(run["root"], "tc-to-autotest:r2")[0] == 0
    assert _finish(run, capsys)["code"] == "FINISH_FACT_UNDETERMINED"
    assert _finish(run, capsys, "STAGE_RETRY_EXHAUSTED")["reason_code"] == "STAGE_RETRY_EXHAUSTED"


def test_a_repeated_prepare_of_the_second_revision_writes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    prepare_stage(run["root"], "tc-to-autotest:r2")
    before = pilot_state.derive_state(run["root"])["events"]
    code, _out = prepare_stage(run["root"], "tc-to-autotest:r2")
    assert code == 0 and pilot_state.derive_state(run["root"])["events"] == before


def test_the_second_revision_over_the_budget_of_the_step_is_a_measurement_not_a_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import step_size

    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    monkeypatch.setattr(step_size, "step_budget", lambda project: 1000)
    code, out = prepare_stage(run["root"], "tc-to-autotest:r2")
    budget = out["result"]["budget"]
    assert code == 0 and budget["fits"] is False and budget["bytes"] > 1000 and {"r1", "ревью r1"} <= set(out["result"]["read"]["by_basis"])


def test_materialize_after_a_rework_verdict_names_the_second_revision_not_a_dead_end(tmp_path: Path) -> None:
    from tools.exec_commands import _not_prepared_fix

    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, REWORK)
    assert "tc-to-autotest:r2" in _not_prepared_fix(run["root"], run["attempt"])


def test_an_accepted_review_does_not_offer_the_second_revision(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "ПРИНЯТО")
    assert prepare_stage(run["root"], "tc-to-autotest:r2")[1]["code"] == "STAGE_UNKNOWN"


def test_the_chain_of_a_rework_verdict_and_a_correction_is_valid_and_of_an_acceptance_and_a_correction_is_not() -> None:
    from tests.test_automation_revision_budget import automation, automated_document, codes, host_evidence, review
    from tools.automation_validation import automation_sha256, autotest_review_sha256, validate_automation_revision_chain

    document = automated_document()
    first, first_evidence, second_evidence = automation(document), host_evidence("session-1"), host_evidence("session-2", reviewer="reviewer-2")
    for verdict, expected in ((REWORK, False), ("ПРИНЯТО", True)):
        first_review = review(document, first, first_evidence, verdict, session_id="session-1")
        second = automation(document, 2, predecessor=automation_sha256(first), correction_review=autotest_review_sha256(first_review))
        second_review = review(document, second, second_evidence, "ПРИНЯТО", session_id="session-2")
        found = codes(validate_automation_revision_chain([first, second], [first_review, second_review], document, host_isolation_receipts=[first_evidence, second_evidence]))
        assert ("AUTOMATION_CORRECTION_VERDICT" in found) is expected, (verdict, found)


def test_a_run_that_is_all_one_session_goes_from_the_rework_verdict_through_the_second_revision_to_the_report(tmp_path: Path, mode_b_pack: Path) -> None:
    """SELF all the way: the consent at the review of the cases, r1 «ТРЕБУЕТ ДОРАБОТКИ», r2, its review ``ПРИНЯТО`` without the flag, ``materialize``, ``report``."""
    from tests import test_profile_flows as flows
    from tests.test_automation_revision_budget import review
    from tests.test_review_two_commands import _verdict
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.run_pipeline import _durable_status

    pack, front = mode_b_pack, flows._front(mode_b_pack, tmp_path, "local-pilot-v1")
    root, attempt, project = front["run_root"], front["attempt_id"], front["project"]

    def step(command: str, stage: str, *flags: str) -> dict:
        code, out = flows._api(pack, command, "--run-root", str(root), "--stage", stage, *flags)
        assert code == 0, (command, stage, out)
        return out["result"]

    def verdict_of(artifact: dict, verdict: str) -> dict:
        revision = artifact["artifacts"]["automation_revision"]
        record = dict(pilot_state.read_attempt_receipt(root, attempt, f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"])
        answer = review(effective["document"], artifact, record, verdict, session_id=record["reviewer_session_id"])
        if verdict == REWORK:
            answer["artifacts"]["autotest_review"].update(corrections=[], findings=[{
                "severity": "BLOCKING", "code": "COMPILE_UNDEFINED_SYMBOL", "message": "Символ не определён.", "evidence": ["file"], "related_ids": ["FILE-products"]}])
        return answer

    plan = json.loads(Path(front["plan"]).read_bytes())
    for batch, fragment in zip(plan["batches"], front["fragments"]):
        stage = f"tc-generator:{batch['batch_id']}"
        Path(step("prepare", stage, "--model-id", "model-generator")["answer_slot"]).write_bytes(Path(fragment).read_bytes())
        step("submit", stage)
    Path(step("prepare", "tc-reviewer:canonical", *SAME_SESSION, FLAG)["answer_slot"]).write_bytes(Path(_verdict(front)).read_bytes())
    step("submit", "tc-reviewer:canonical")
    effective = pilot_state.read_effective_canonical(root, attempt)
    first = flows._automation_answer(json.loads((front["tmp"] / "candidate.json").read_bytes()), effective)
    Path(step("prepare", GENERATOR, "--model-id", "model-automation")["answer_slot"]).write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    step("submit", GENERATOR)
    slot = step("prepare", REVIEWER, *SAME_SESSION)["answer_slot"]
    first_review = verdict_of(first, REWORK)
    Path(slot).write_text(json.dumps(first_review, ensure_ascii=False), encoding="utf-8")
    step("submit", REVIEWER)
    assert _durable_status(project, root, stage="status")["next"] == {"verb": "prepare", "stage": "tc-to-autotest:r2"}
    second = json.loads(json.dumps(first))
    second["artifacts"].update(automation_revision=2, predecessor_automation_sha256=automation_sha256(first), correction_review_sha256=autotest_review_sha256(first_review))
    Path(step("prepare", "tc-to-autotest:r2", "--model-id", "model-automation")["answer_slot"]).write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
    step("submit", "tc-to-autotest:r2")
    slot = step("prepare", "autotest-reviewer:r2", *SAME_SESSION)["answer_slot"]
    Path(slot).write_text(json.dumps(verdict_of(second, "ПРИНЯТО"), ensure_ascii=False), encoding="utf-8")
    step("submit", "autotest-reviewer:r2")
    assert _durable_status(project, root, stage="status")["next"]["verb"] == "materialize"
    shared = ("--project", str(project), "--run", root.name, "--module", "python-pytest")
    code, printed = flows._cli(pack, "tools.run_pipeline", "materialize", *shared)
    assert code == 0 and printed["stage"] == "materialize", printed
    code, _report = flows._cli(pack, "tools.run_pipeline", "report", "--project", str(project), "--run", root.name)
    shown = json.loads((root / "report.json").read_text(encoding="utf-8"))["review"]
    assert code == 0 and "автотесты проверены в той же сессии" in shown and "независимость не засчитана" in shown
    assert not [row for row in pilot_state.derive_state(root)["events"] if row["event_type"] == "COMMAND_REFUSED"]
