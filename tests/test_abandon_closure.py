"""FaT-AB: every closing dead end goes by its fix to the end — the dead end, the command the fix names, the closed attempt.

Tier A: an automation chain that cannot go on (the one retry spent, a revision that does not assemble) closes with
`finish --abandon <code>`.  Tier B (D2): a canonical review that cannot end in a verdict closes with
`review-abort --reason REVIEW_UNUSABLE` and select.  D3: exec tells a repeatable I/O failure of the preparation from a
final one.  `--abandon` is refused wherever another command closes the attempt (ABANDON_NOT_NEEDED)."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from tests.test_closure_deadlocks import _run
from tests.test_finish import _automation_chain


def _command(fix: str, needle: str) -> list[str]:
    """The one command of the fix that contains ``needle``, as argv after ``-m <module>``."""
    for part in re.split(r"[;—]", fix):
        if needle in part and " -m tools." in part:
            tail = part[part.index(" -m tools.") + len(" -m "):].strip().rstrip(".,")
            return shlex.split(tail.split(", затем")[0], posix=True)
    raise AssertionError(f"no command with {needle!r} in fix: {fix}")


def _main(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict]:
    import tools.api
    import tools.run_pipeline

    capsys.readouterr()
    module = {"tools.run_pipeline": tools.run_pipeline, "tools.api": tools.api}[argv[0]]
    code = module.main(argv[1:])
    return code, json.loads(capsys.readouterr().out)


def _scan_not_blocked(root: Path) -> None:
    """_open_run_of blocks a scan only by a selected attempt that is not TERMINAL."""
    from tools.pilot_state import derive_state

    assert derive_state(root)["attempts"][-1]["state"] == "TERMINAL"


# --------------------------------------------------------------------------------------
# tier A: the automation chain cannot go on
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("code_name", ["STAGE_RETRY_EXHAUSTED", "ASSEMBLY_INVALID"])
def test_a_pending_automation_is_closed_by_the_abandon_its_fix_names(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                     code_name: str) -> None:
    root, attempt_id = _automation_chain(tmp_path, [None])  # r1 generated, its review never comes
    code, refused = _run(capsys, "finish", tmp_path, root)
    assert code == 2 and refused["reason"] == "FINISH_FACT_UNDETERMINED", refused
    argv = _command(refused["fix"], f"--abandon {code_name}")
    assert f"--run {root.name}" in " ".join(argv) and "<" not in " ".join(argv), argv

    code, closed = _main(capsys, argv)
    assert closed["status"] == "ok" and closed["branch"] == "no-execution", closed
    assert closed["reason_code"] == code_name and closed["accepted"] is False and code != 0, closed
    assert closed["completion"] == "PARTIAL" and closed["verification"] == "NOT_APPLICABLE", closed
    _scan_not_blocked(root)
    # closed once: the plain finish answers the same result
    _code, again = _run(capsys, "finish", tmp_path, root)
    assert again["reason_code"] == code_name and again["idempotent"] is True, again


def test_abandon_is_refused_where_finish_or_exec_closes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import main

    rejected, _attempt = _automation_chain(tmp_path / "rejected", ["ТРЕБУЕТ ДОРАБОТКИ", "ТРЕБУЕТ ДОРАБОТКИ"])
    capsys.readouterr()
    code = main(["finish", "--project", str(tmp_path / "rejected"), "--run", rejected.name, "--abandon", "STAGE_RETRY_EXHAUSTED"])
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and refused["reason"] == "ABANDON_NOT_NEEDED" and "--abandon" not in refused["fix"], refused
    assert _command(refused["fix"], "finish")[1] == "finish"

    accepted, _attempt = _automation_chain(tmp_path / "accepted", ["ПРИНЯТО"])
    capsys.readouterr()
    code = main(["finish", "--project", str(tmp_path / "accepted"), "--run", accepted.name, "--abandon", "AUTOMATION_NOT_MATERIALIZABLE"])
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and refused["reason"] == "FINISH_FACT_UNDETERMINED" and _command(refused["fix"], "exec")[1] == "exec", refused
    from tools.pilot_state import derive_state

    assert derive_state(accepted)["attempts"][-1]["state"] != "TERMINAL"


# --------------------------------------------------------------------------------------
# tier B (D2): a canonical review that cannot end in a verdict
# --------------------------------------------------------------------------------------


def test_b_an_unusable_review_is_closed_by_the_abort_its_fix_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_review_requested import _generated_run, _open_session, _review, _select

    run = _generated_run(tmp_path)
    candidate, _boundary = _open_session(run)
    assert _review(run, candidate)[0] == 0  # the request is out; the answer twice failed its check
    code, refused = _run(capsys, "finish", run["project"], run["run_root"])
    assert code == 2 and refused["reason"] == "FINISH_FACT_UNDETERMINED", refused
    assert "<код>" not in refused["fix"] and "REVIEW_CONTEXT_LIMIT" in refused["fix"], refused
    argv = _command(refused["fix"], "--reason REVIEW_UNUSABLE")
    assert run["attempt_id"] in argv and "<" not in " ".join(argv), argv

    code, aborted = _main(capsys, argv)
    assert code == 0, aborted
    code, selected = _select(run, None)
    assert code == 0 and selected["facts"]["attempt_state"] == "TERMINAL", selected
    assert selected["facts"]["reason_code"] == "REVIEW_UNUSABLE" and selected["facts"]["accepted"] is False, selected
    _code, closed = _run(capsys, "finish", run["project"], run["run_root"])  # the fix ends with finish
    assert closed["status"] == "ok" and closed["reason_code"] == "REVIEW_UNUSABLE" and closed["accepted"] is False, closed
    _scan_not_blocked(run["run_root"])


# --------------------------------------------------------------------------------------
# D3: the accepted automation passed the precheck, its preparation failed
# --------------------------------------------------------------------------------------


def _failing_ownership(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    from tools import pilot_state

    original = pilot_state.publish_phase5_receipt

    def publish(run_root, attempt_id, kind, payload):
        if kind == "materialization-ownership":
            raise error
        return original(run_root, attempt_id, kind, payload)

    monkeypatch.setattr(pilot_state, "publish_phase5_receipt", publish)


def test_d3_an_io_failure_of_the_preparation_says_repeat_exec(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    root, _attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])
    with monkeypatch.context() as broken:
        _failing_ownership(broken, OSError("disk is busy"))
        code, refused = _run(capsys, "exec", tmp_path, root)
    assert code == 2 and refused["reason"] == "EXECUTION_NOT_PREPARED", refused
    assert "снова вызови exec" in refused["fix"] and "--abandon" not in refused["fix"], refused
    _code, finish = _run(capsys, "finish", tmp_path, root)
    assert finish["reason"] == "FINISH_FACT_UNDETERMINED" and _command(finish["fix"], "exec")[1] == "exec", finish


def test_d3_a_final_failure_of_the_preparation_is_closed_by_the_finish_its_fix_names(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    root, _attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])
    with monkeypatch.context() as broken:
        _failing_ownership(broken, ValueError("ownership receipt differs"))
        code, refused = _run(capsys, "exec", tmp_path, root)
    assert code == 2 and refused["reason"] == "EXECUTION_NOT_PREPARED", refused
    argv = _command(refused["fix"], "--abandon AUTOMATION_NOT_MATERIALIZABLE")

    code, closed = _main(capsys, argv)
    assert closed["status"] == "ok" and closed["reason_code"] == "AUTOMATION_NOT_MATERIALIZABLE", closed
    assert closed["accepted"] is False and closed["verification"] == "NOT_APPLICABLE" and code != 0, closed
    assert not any((tmp_path / "tests").rglob("test_products.py"))
    _scan_not_blocked(root)


# --------------------------------------------------------------------------------------
# tier C (until FaT-AB2): before the canonical review nothing closes — finish and scan name the same next run
# --------------------------------------------------------------------------------------


def test_c_before_the_review_finish_and_scan_name_the_same_new_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _runs, _scan
    from tools import run_pipeline

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    code = run_pipeline.cmd_finish(type("Args", (), {"project": str(project), "run": first["run_id"], "abandon": None})())
    monkeypatch.undo()
    refused = payloads[-1]
    assert code == 2 and refused["reason"] == "FINISH_FACT_UNDETERMINED" and "до ревью кейсов" in refused["message"], refused
    command = refused["next_run"]["command"]
    assert command.endswith("--new-run") and " scan " in command and command in refused["fix"], refused
    assert "<" not in command and "review-abort" not in refused["fix"], refused

    code, blocked = _scan(project, monkeypatch)  # the block stays (FaT-G); its fix names the same command
    assert code == 2 and blocked["reason"] == "SCAN_RUN_IN_PROGRESS" and command in blocked["fix"], blocked
    code, second = _scan(project, monkeypatch, new_run=True)  # what the command does
    assert code == 0 and second["run_id"] != first["run_id"] and len(_runs(project)) == 2, second


# --------------------------------------------------------------------------------------
# C1: the SKILL order of a batched generator spends no retry on a correct answer
# --------------------------------------------------------------------------------------


def test_c1_two_batches_in_skill_order_spend_no_retry_on_batch_one() -> None:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.canonical_document import validate_canonical_document

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    assert len(plan["batches"]) == 2
    first, second = _fragment(plan, context, 0), _fragment(plan, context, 1)
    validate_fragment(first, plan, header, context)  # before model-response of batch 1: the check the SKILL names
    with pytest.raises(BatchAssemblyError) as partial:  # the check the SKILL used to name: it always refused here
        assemble_candidate(header, plan, [first])
    assert partial.value.code == "BATCH_PARTITION_CONFLICT"
    validate_fragment(second, plan, header, context, [first])
    candidate, _receipt = assemble_candidate(header, plan, [first, second])  # after the last batch, on all fragments
    assert validate_canonical_document(candidate) == []

    skill = (Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    before, _, after = skill.partition("До `model-response` пачки — только")
    assert after and "`assemble` и `validate-canonical` из п.4 —\n   после ответа последней пачки" in after
    assert "затем `assemble` и\n   `validate-canonical` из п.4 на этом же файле" not in skill


# --------------------------------------------------------------------------------------
# C5: there is no third answer, by code
# --------------------------------------------------------------------------------------


def test_c5_a_third_answer_and_a_replacing_answer_are_refused_by_name_and_write_nothing(tmp_path: Path) -> None:
    from tools.pilot_state import derive_state, publish_model_stage_artifact, read_model_stage_artifact

    root, attempt_id = _automation_chain(tmp_path, [None])
    stage = "tc-to-autotest:r1"
    event = next(row for row in derive_state(root)["events"]
                 if row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == stage)
    answer = dict(read_model_stage_artifact(root, attempt_id, stage, str(event["artifact_digest"]))["artifact"])
    journal = (root / "events.jsonl").read_bytes()
    with pytest.raises(ValueError) as third:
        publish_model_stage_artifact(root, attempt_id, stage, answer, transport_attempts=3)
    assert str(third.value).startswith("MODEL_STAGE_RETRY_EXHAUSTED") and "finish" in str(third.value), third.value
    other = {**answer, "artifacts": {**answer["artifacts"], "diagnostics": [{"path": "/artifacts", "code": "X", "message": "другой"}]}}
    with pytest.raises(ValueError) as replaced:
        publish_model_stage_artifact(root, attempt_id, stage, other, transport_attempts=2)
    assert str(replaced.value).startswith("MODEL_STAGE_ANSWERED") and "STAGE_RETRY_EXHAUSTED" in str(replaced.value), replaced.value
    assert (root / "events.jsonl").read_bytes() == journal
