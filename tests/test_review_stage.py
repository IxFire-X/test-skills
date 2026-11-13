"""V4a: the review of the cases through the commands of the agent — ``status`` → ``prepare --stage tc-reviewer:canonical`` (the label of the reviewer's session and
``--fresh-context``) → the answer in its slot → ``submit`` (the check before the write, the selection, the set in ``<run>/published``) → ``status``.

The old commands of the review stay until V4b: these tests use none of them."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_review_canaries import _answer, _boundary_and_removed, _fake_seed, _finding, _generated_run, _ids
from tests.test_stage_steps import _call, _events, _put, _status
from tools import pilot_state, review_canaries, stage_steps

STAGE = "tc-reviewer:canonical"
INDEPENDENT = ("--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes")
CODE = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> dict:
    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    return _generated_run(tmp_path, **kwargs)


def _prepare(capsys, run: dict, *flags: str) -> tuple[int, dict]:
    return _call(capsys, "prepare", run["run_root"], STAGE, *(flags or INDEPENDENT))


def _submit(capsys, run: dict, answer: object, *flags: str) -> tuple[int, dict]:
    _put(run["run_root"], STAGE, answer)
    return _call(capsys, "submit", run["run_root"], STAGE, *flags)


def _opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, **kwargs) -> dict:
    run = _run(tmp_path, monkeypatch, **kwargs)
    assert _prepare(capsys, run, *(INDEPENDENT if not kwargs.get("self_review") else ("--fresh-context", "no")))[0] == 0
    return run


def _published(run: dict) -> list[str]:
    folder = run["run_root"] / "published"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def test_status_names_prepare_for_the_review_and_prepare_opens_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    status = _status(run["run_root"])
    assert (status["state"], status["next"]) == ("review", {"verb": "prepare", "stage": STAGE})
    code, out = _prepare(capsys, run)
    result = out["result"]
    assert code == 0 and result["answer_slot"] == str(stage_steps.slot(run["run_root"], STAGE)) and Path(result["reviewer_view"]).is_file()
    assert result["canaries"]["seeded"] == 2 and result["budget"]["fits"] and isinstance(result["for_human"], str)
    assert _events(run["run_root"])[-1] == f"MODEL_REQUESTED:{STAGE}"
    assert _status(run["run_root"])["next"] == {"verb": "submit", "stage": STAGE}


def test_the_first_prepare_of_the_review_names_no_analyst_questions_and_leaves_published_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    code, out = _prepare(capsys, run)
    assert code == 0 and "ANALYST_QUESTIONS" not in str(out) and "warnings" not in out["result"] and "questions_for_analyst" not in out["result"]
    assert not _published(run)  # nothing is in published/ after the first prepare


def test_prepare_again_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _prepare(capsys, run)
    assert code == 0 and out["result"]["answer_slot"] and _events(run["run_root"]) == before


def test_another_reviewer_is_refused_once_the_review_is_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _prepare(capsys, run, "--invocation-id", "reviewer-subagent-2", "--fresh-context", "yes")
    assert code == 2 and out["code"] == "REVIEW_ALREADY_OPEN" and out["fix"] and _events(run["run_root"]) == before


def test_a_fresh_context_without_a_label_is_refused_before_anything_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    before = _events(run["run_root"])
    code, out = _prepare(capsys, run, "--fresh-context", "yes")
    assert code == 2 and out["code"] == "REVIEW_INVOCATION_REQUIRED" and out["fix"] and _events(run["run_root"]) == before


def test_the_same_session_without_a_label_is_self_and_refused_where_self_is_not_admitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    before = _events(run["run_root"])
    code, out = _prepare(capsys, run, "--fresh-context", "no")
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and out["fix"] and _events(run["run_root"]) == before


def test_the_label_of_the_generator_is_no_independence_and_is_refused_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    before = _events(run["run_root"])
    generator = next(row for row in pilot_state.derive_state(run["run_root"])["events"] if row["event_type"] == "MODEL_REQUESTED" and row["stage_instance_id"].startswith("tc-generator:BATCH-"))
    label = pilot_state.read_model_request(run["run_root"], run["attempt_id"], generator["stage_instance_id"], str(generator["artifact_digest"]))["invocation_id"]
    code, out = _prepare(capsys, run, "--invocation-id", label, "--fresh-context", "yes")
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and _events(run["run_root"]) == before


def test_self_in_a_run_that_admitted_it_opens_with_the_label_of_the_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys, self_review=True)
    ledger = pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])
    assert ledger["host_isolation"]["fresh_context"] is False and ledger["host_isolation"]["distinct_invocations"] is False
    assert ledger["reviewer_invocation_id"] == ledger["generator_invocation_id"]
    code, out = _submit(capsys, run, _answer(run))
    assert code == 0 and out["result"]["status"] == "EFFECTIVE_SELECTED" and out["result"]["independence"] == "independence_unverified"


def test_an_accepted_set_is_selected_and_published_and_status_names_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    code, out = _submit(capsys, run, _answer(run))
    assert code == 0 and out["result"]["status"] == "EFFECTIVE_SELECTED" and out["result"]["independence"] == "verified" and out["result"]["outcome"] == "ACCEPTED_WHOLE"
    assert pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is not None
    assert any(name.endswith(".csv") for name in _published(run)) and _status(run["run_root"])["next"]["verb"] == "finish"


def test_a_partial_acceptance_goes_back_once_and_names_the_rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    run = _opened(tmp_path, monkeypatch, capsys)
    answer = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[*_boundary_and_removed(run), _finding("BLOCKING", [_ids(run)[0]["case_id"]], "Нет проверки состояния.")])
    code, out = _submit(capsys, run, answer)
    assert code == 0 and out["result"]["status"] == "REWORK_PENDING" and out["result"]["outcome"] == "PARTIAL" and not any(name.endswith(".csv") for name in _published(run))
    assert _status(run["run_root"])["next"] == {"verb": "prepare", "stage": "tc-generator:rework-1"}


def test_a_set_sent_back_whole_is_closed_without_an_effective_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    code, out = _submit(capsys, run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", ["CREQ-batch-a-001"], "Требование не покрыто.")]))
    assert code == 0 and out["result"]["status"] == "REWORK" and out["result"]["outcome"] == "REWORK"
    assert pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is None and _status(run["run_root"])["next"]["verb"] == "finish"


def test_an_answer_of_a_wrong_form_is_refused_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _submit(capsys, run, {"stage": "tc-reviewer"})
    assert code == 2 and out["fix"] and _events(run["run_root"]) == before


def test_submit_without_prepare_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    code, out = _submit(capsys, run, {})
    assert code == 2 and out["code"] == "STAGE_NOT_PREPARED"


def test_a_dry_run_checks_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _submit(capsys, run, _answer(run), "--dry-run")
    assert code == 0 and out["dry_run"] is True and out["result"]["would_publish"] and _events(run["run_root"]) == before
    assert pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is None


def test_the_recorded_answer_stands_and_the_slot_is_not_read_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    assert _submit(capsys, run, _answer(run))[0] == 0
    before = _events(run["run_root"])
    stage_steps.slot(run["run_root"], STAGE).write_text("not json", encoding="utf-8")
    code, out = _call(capsys, "submit", run["run_root"], STAGE)
    assert code == 0 and out["result"]["answered"] is True and out["result"]["status"] == "EFFECTIVE_SELECTED" and _events(run["run_root"]) == before


def test_a_selection_that_failed_after_the_answer_was_written_is_finished_by_the_repeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    real = pilot_state.publish_effective_canonical
    calls: list[int] = []

    def refuse_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("the disk is full")
        return real(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "publish_effective_canonical", refuse_once)
    assert _submit(capsys, run, _answer(run))[0] == 2 and pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is None
    code, out = _call(capsys, "submit", run["run_root"], STAGE)
    assert code == 0 and out["result"]["status"] == "EFFECTIVE_SELECTED" and pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is not None


def test_a_reviewer_who_names_the_codeword_is_self_and_refused_before_the_answer_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(review_canaries, "codeword_state", lambda *_args: "named")
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _submit(capsys, run, _answer(run, codeword="WORD"))
    assert code == 2 and out["code"] == "REVIEW_CODEWORD_NAMED" and _events(run["run_root"]) == before


def test_the_evidence_of_the_slot_is_recorded_with_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    code, out = _submit(capsys, run, {**_answer(run), "evidence": ["src/extra.py"]})
    assert code == 0 and out["result"]["status"] == "EFFECTIVE_SELECTED"
    kinds = [event["event_type"] for event in pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["events"]]
    assert kinds.count("EVIDENCE_PROVIDED") == 1


def test_evidence_that_is_not_a_file_of_the_project_leaves_the_answer_unwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _submit(capsys, run, {**_answer(run), "evidence": ["../outside.txt"]})
    assert code == 2 and out["code"] == "REVIEWER_EVIDENCE_PATH_UNKNOWN" and _events(run["run_root"]) == before


def test_a_review_that_cannot_end_in_a_verdict_is_aborted_by_submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    code, out = _call(capsys, "submit", run["run_root"], STAGE, "--abort", "REVIEW_UNUSABLE")
    assert code == 0 and out["result"]["outcome"] == "ABORTED"
    assert pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["status"] == "ABORTED"
    assert _status(run["run_root"])["next"]["verb"] == "finish"


def test_an_unknown_reason_to_abort_is_refused_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    code, out = _call(capsys, "submit", run["run_root"], STAGE, "--abort", "WHATEVER")
    assert code == 2 and out["code"] == "REVIEWER_ABORT_REASON_INVALID" and out["fix"] and _events(run["run_root"]) == before


def test_a_stage_that_cannot_be_aborted_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    before = _events(run["run_root"])
    batch = next(row["stage_instance_id"] for row in pilot_state.derive_state(run["run_root"])["events"] if str(row.get("stage_instance_id")).startswith("tc-generator:BATCH-"))
    code, out = _call(capsys, "submit", run["run_root"], batch, "--abort", "REVIEW_UNUSABLE")
    assert code == 2 and out["code"] == "STAGE_NO_ABORT" and out["fix"] and _events(run["run_root"]) == before


def test_the_text_for_the_human_leaves_the_process_as_utf8_whatever_the_console_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _run(tmp_path, monkeypatch)
    done = subprocess.run([sys.executable, "-m", "tools.api", "prepare", "--run-root", str(run["run_root"]), "--stage", STAGE, *INDEPENDENT], cwd=CODE, capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "cp1251", "PYTHONUTF8": "0"}, timeout=120)
    result = json.loads(done.stdout.decode("utf-8"))["result"]
    assert done.returncode == 0 and "Ревью" in result["for_human"]
