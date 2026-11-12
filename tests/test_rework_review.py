"""V7b: the second round — ``prepare`` / ``submit --stage tc-reviewer:rework-1`` over the reworked cases only, the set published once after it.

The first review and the rework are the commands of V4a and V7a (``test_rework_stage`` helpers); ``limits.rework_rounds`` is set by ``monkeypatch`` as there."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_rework_stage import STAGE, _events, _finding, _finish, _ids, _published, _put, _reviewed, _reworked, _status
from tests.test_stage_steps import _call
from tools import pilot_state

REVIEW = "tc-reviewer:rework-1"


def _answered(run: dict, capsys: pytest.CaptureFixture[str]) -> None:
    """The generator of the rework has answered (a rework with one reworked case)."""
    _call(capsys, "prepare", run["run_root"], STAGE)
    _put(run["run_root"], STAGE, _reworked(run))
    assert _call(capsys, "submit", run["run_root"], STAGE)[0] == 0


def _open(run: dict, capsys: pytest.CaptureFixture[str], *flags: str) -> tuple[int, dict]:
    return _call(capsys, "prepare", run["run_root"], REVIEW, *(flags or ("--invocation-id", "reviewer-2", "--fresh-context", "yes")))


def _answer(run: dict, verdict: str = "ПРИНЯТО", *findings: dict, **artifacts) -> dict:
    from tools.orchestrate_test_case_revision import rework_round_data

    data = rework_round_data(run["run_root"], run["attempt_id"], need_answer=True)
    ref = {"document_id": data["r3"]["document_id"], "revision": data["r3"]["revision"], "document_sha256": data["r3_digest"]}
    report = {"verdict": verdict, "candidate": ref, "effective": ref if verdict == "ПРИНЯТО" else None, "findings": list(findings), "corrections": [],
              "reviewed_case_ids": [case["case_id"] for case in data["answer"]["test_cases"]]}
    return {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report, **artifacts}, "warnings": []}


def _ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:
    run, _done = _reviewed(tmp_path, monkeypatch)
    _answered(run, capsys)
    return run


def test_status_names_the_review_of_the_rework_after_the_answer_and_finish_after_the_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    assert _status(run["run_root"])["next"] == {"verb": "prepare", "stage": REVIEW}
    code, out = _open(run, capsys)
    assert code == 0, out
    assert _status(run["run_root"])["next"] == {"verb": "submit", "stage": REVIEW}


def test_prepare_requests_the_review_of_the_answer_and_shows_the_reworked_cases_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    code, out = _open(run, capsys)
    assert code == 0 and _events(root)[-2:] == [f"REVIEW_REQUESTED:{REVIEW}", f"MODEL_REQUESTED:{REVIEW}"] and out["result"]["answer_slot"].endswith("tc-reviewer-rework-1.json")
    view = json.loads(Path(out["result"]["reviewer_view"]).read_text(encoding="utf-8"))
    one, two = _ids(run)
    assert [case["case_id"] for case in view["cases"]] == [one["case_id"]] == view["reviewed_case_ids"] and two["case_id"] not in json.dumps(view, ensure_ascii=False)
    assert "Нет проверки состояния." in json.dumps(view["first_round"], ensure_ascii=False) and view["candidate"]["revision"] == 3
    listed = json.loads(Path(out["result"]["read"]["list_path"]).read_text(encoding="utf-8"))["files"]
    assert any(row["path"].endswith("skills\\tc-reviewer\\rework.md") or row["path"].endswith("skills/tc-reviewer/rework.md") for row in listed)
    before = _events(root)
    assert _open(run, capsys)[0] == 0 and _events(root) == before  # the repeat changes nothing


def test_every_reworked_case_accepted_is_the_whole_set_r3_without_a_file_of_the_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run))
    code, out = _call(capsys, "submit", root, REVIEW)
    assert code == 0 and out["result"]["outcome"] == "ACCEPTED_WHOLE" and out["result"]["independence"] == "verified"
    names = _published(run)
    assert names and all(".r3." in name for name in names) and not any("REJECTED" in name for name in names)
    effective = pilot_state.read_effective_canonical(root, run["attempt_id"])["document"]
    assert (effective["revision"], len(effective["test_cases"])) == (3, 2) and effective["test_cases"][1] == _ids(run)[1]  # the case of the first round is r1's, byte for byte
    from tools.run_report import build_report

    acceptance = build_report(run["project"], root.name)[0]["acceptance"]
    assert (acceptance["outcome"], acceptance["rework"]["state"], acceptance["rework"]["accepted"], acceptance["generated"], acceptance["accepted"]) == ("ACCEPTED_WHOLE", "reviewed", 1, 2, 2)
    assert _status(root)["next"]["verb"] != "prepare"


def test_a_reworked_case_not_accepted_stays_out_with_the_findings_of_both_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root, one = run["run_root"], _ids(run)[0]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [one["case_id"]], "Снова нет проверки.", code="STILL")))
    code, out = _call(capsys, "submit", root, REVIEW)
    assert code == 0 and out["result"]["outcome"] == "PARTIAL"
    rejected = next(path for path in (root / "published").glob("*.REJECTED-CASES.md")).read_text(encoding="utf-8")
    assert "круг 1, блокирующее" in rejected and "круг 2, блокирующее, STILL" in rejected and "после одного круга доработки" in rejected and "Доработанный кейс" in rejected
    assert pilot_state.read_effective_canonical(root, run["attempt_id"])["document"]["revision"] == 2
    from tools.run_report import build_report

    acceptance = build_report(run["project"], root.name)[0]["acceptance"]
    assert (acceptance["outcome"], acceptance["rework"]["state"], acceptance["rework"]["accepted"]) == ("PARTIAL", "reviewed", 0) and "Снова нет проверки" not in json.dumps(acceptance, ensure_ascii=False)


def test_a_blocking_finding_without_a_case_accepts_none_and_one_on_a_case_of_the_first_round_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root, two = run["run_root"], _ids(run)[1]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [two["case_id"]], "Про принятый кейс."), _finding("BLOCKING", ["CREQ-OTHER"], "Про набор.")))
    out = _call(capsys, "submit", root, REVIEW)[1]["result"]
    assert out["outcome"] == "PARTIAL" and out["rework"] == {"accepted": 0, "not_accepted": 1, "ignored": 1}


@pytest.mark.parametrize("extra", [{"successor_edits": []}, {"successor_document": {"revision": 2}}])
def test_a_fix_or_a_successor_in_the_second_round_is_refused_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra: dict) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run, **extra))
    before = _events(root)
    code, out = _call(capsys, "submit", root, REVIEW)
    assert code == 2 and out["code"] in ("REWORK_REVIEW_FIX", "MODEL_STAGE_ARTIFACT_INVALID") and _events(root) == before and _published(run) == []


def test_an_auto_fix_verdict_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    _open(run, capsys)
    answer = _answer(run, "AUTO_FIX_APPLIED")
    _put(run["run_root"], REVIEW, answer)
    assert _call(capsys, "submit", run["run_root"], REVIEW)[1]["code"] in ("REWORK_REVIEW_FIX", "SCHEMA_VALIDATION", "MODEL_STAGE_ARTIFACT_INVALID")


def test_a_report_that_names_other_cases_than_the_reworked_ones_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    _open(run, capsys)
    answer = _answer(run)
    answer["artifacts"]["validation_report"]["reviewed_case_ids"] = [case["case_id"] for case in (_ids(run))]
    _put(run["run_root"], REVIEW, answer)
    assert _call(capsys, "submit", run["run_root"], REVIEW)[1]["code"] == "REWORK_REVIEW_CASES"


def test_a_dry_run_says_the_set_and_writes_nothing_and_the_repeat_after_the_write_is_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run))
    before = _events(root)
    code, out = _call(capsys, "submit", root, REVIEW, "--dry-run")
    assert code == 0 and out["result"]["would_publish"] == {"revision": 3, "cases": 2} and _events(root) == before and _published(run) == []
    assert _call(capsys, "submit", root, REVIEW)[0] == 0
    again = _call(capsys, "submit", root, REVIEW)[1]
    assert "result" in again and again["result"]["answered"] is True, again


def test_abort_closes_the_second_round_as_a_rework_nobody_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    _open(run, capsys)
    code, out = _call(capsys, "submit", root, REVIEW, "--abort", "REVIEW_UNUSABLE")
    assert code == 0 and out["result"]["outcome"] == "ABORTED"
    assert "доработка не завершена" in next(path for path in (root / "published").glob("*.REJECTED-CASES.md")).read_text(encoding="utf-8")
    assert _status(root)["next"]["verb"] == "finish"


def test_a_finish_between_the_answer_and_its_review_closes_as_before_the_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    _open(run, capsys)
    assert _finish(run, capsys)[1]["reason_code"] == "PARTIAL_ACCEPTED"
    assert "доработка не завершена" in next(path for path in (run["run_root"] / "published").glob("*.REJECTED-CASES.md")).read_text(encoding="utf-8")


# ---- the label of the host carries its statement (no boundary is stored in the second round) ------------------------------------------------

def test_a_new_session_is_independent_and_the_same_session_is_self_and_refused_where_the_run_does_not_admit_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    code, out = _open(run, capsys, "--fresh-context", "no")  # no label: the generator's of the rework, the same session
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and not any(row.startswith("REVIEW_REQUESTED:" + REVIEW) for row in _events(root))
    code, out = _open(run, capsys, "--fresh-context", "no", "--invocation-id", "reviewer-2")  # «the same session» with a label of another one: no
    assert code == 2 and out["code"] == "REVIEW_FRESH_CONTEXT_MISMATCH" and "tc-generator-rework-1" in out["fix"] and "--fresh-context yes --invocation-id" in out["fix"], out
    code, out = _open(run, capsys, "--fresh-context", "yes")
    assert code == 2 and out["code"] == "REVIEW_INVOCATION_REQUIRED"
    assert _open(run, capsys, "--fresh-context", "yes", "--invocation-id", "reviewer-2")[0] == 0
    assert _open(run, capsys, "--fresh-context", "yes", "--invocation-id", "reviewer-3")[1]["code"] == "REVIEW_ALREADY_OPEN"


def test_the_report_does_not_call_a_review_independent_when_the_second_round_was_self(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_rework_publication import _acc, _lines

    acc = _acc("reviewed")
    assert "Ревью: независимое (INDEPENDENT), 2 круга" in _lines({**acc, "rework": {**acc["rework"], "independent": True}})
    assert "Ревью: в той же сессии (SELF), 2 круга" in _lines({**acc, "rework": {**acc["rework"], "independent": False}})


def test_a_second_round_of_the_review_is_a_stage_the_code_does_not_know(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    assert _call(capsys, "prepare", run["run_root"], "tc-reviewer:rework-2")[1]["code"] == "STAGE_UNKNOWN"


def test_the_review_of_the_rework_is_not_asked_for_before_the_rework_is_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    assert _call(capsys, "prepare", run["run_root"], REVIEW, "--invocation-id", "reviewer-2", "--fresh-context", "yes")[1]["code"] == "STAGE_UNKNOWN"
    with pytest.raises(ValueError):
        pilot_state.request_review(run["run_root"], run["attempt_id"], REVIEW, "sha256:" + "1" * 64)


def test_an_answer_put_in_the_slot_after_the_second_round_was_aborted_is_refused_before_it_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    _open(run, capsys)
    assert _call(capsys, "submit", root, REVIEW, "--abort", "REVIEW_UNUSABLE")[0] == 0
    _put(root, REVIEW, _answer(run))
    before, files = _events(root), _published(run)
    code, out = _call(capsys, "submit", root, REVIEW)
    assert code == 2 and out["code"] == "STAGE_UNKNOWN" and _events(root) == before and _published(run) == files  # the step is gone with the published set: nothing is recorded


# ---- a run that accepted SELF runs the second round in the same session and says so; a cut end of the second round is finished by the repeat ----

def _self_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:
    """``_reviewed`` over a run started with ``scan --accept-self-review``, the rework answered."""
    from tests.test_review_canaries import _answer as first_answer, _boundary_and_removed, _finding as blocking, _generated_run, _ids as ids, _open as open_first, _select
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    run = _generated_run(tmp_path, self_review=True)
    open_first(run, monkeypatch)
    findings = [*_boundary_and_removed(run), blocking("BLOCKING", [ids(run)[0]["case_id"]], "Нет проверки состояния.")]
    assert _select(run, first_answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))["status"] == "REWORK_PENDING"
    _answered(run, capsys)
    return run


def test_a_run_that_accepted_self_passes_the_second_round_in_the_same_session_and_the_report_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import build_report, render_markdown

    run = _self_run(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    code, out = _open(run, capsys, "--fresh-context", "no")  # no label: the one of the generator of the rework, the same session
    assert code == 0, out
    _put(root, REVIEW, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [_ids(run)[0]["case_id"]], "Снова нет проверки.")))  # the set accepted in part: the report has its table
    code, done = _call(capsys, "submit", root, REVIEW)
    assert code == 0 and done["result"]["independence"] == "independence_unverified", done
    assert pilot_state.read_effective_canonical_if_present(root, run["attempt_id"]) is not None
    report, _warnings = build_report(run["project"], root.name)
    assert "Ревью: в той же сессии (SELF), 2 круга" in render_markdown(report)


def test_a_second_round_cut_between_the_set_and_the_effective_canonical_is_finished_by_the_repeat_and_the_set_is_not_written_twice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_review_open_recovery import _crash_once

    run = _ready(tmp_path, monkeypatch, capsys)
    root, attempt = run["run_root"], run["attempt_id"]
    _open(run, capsys)
    _put(root, REVIEW, _answer(run))
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        assert _call(capsys, "submit", root, REVIEW)[0] == 2
    files = _published(run)
    assert files and pilot_state.read_effective_canonical_if_present(root, attempt) is None  # the set is on the disk, the result is not bound yet
    code, done = _call(capsys, "submit", root, REVIEW)
    assert code == 0 and pilot_state.read_effective_canonical_if_present(root, attempt) is not None, done
    assert _published(run) == files and _events(root).count(f"MODEL_RESPONSE_RECEIVED:{REVIEW}") == 1



def test_a_request_of_the_second_round_without_an_answer_and_without_the_result_stops_the_start_of_the_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """The exception of the journal for a rework nobody finished is for the attempt whose result is published (the automation follows it): not for one that has none."""
    run = _ready(tmp_path, monkeypatch, capsys)
    root = run["run_root"]
    assert _open(run, capsys)[0] == 0
    with pytest.raises(ValueError, match="invalid event journal"):
        pilot_state._append_event(run["project"], root, "EXECUTION_STARTED", actor="controller", attempt_id=run["attempt_id"], batch_id=None, artifact_digest="sha256:" + "1" * 64)
        pilot_state.derive_state(root)


def test_a_set_accepted_whole_after_a_second_round_in_the_same_session_is_not_called_independent_by_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import build_report, render_markdown

    run = _self_run(tmp_path, monkeypatch, capsys)  # the first round in a fresh session (verified), the second one in the same
    root = run["run_root"]
    assert _open(run, capsys, "--fresh-context", "no")[0] == 0
    _put(root, REVIEW, _answer(run))  # every reworked case accepted: the whole set
    code, done = _call(capsys, "submit", root, REVIEW)
    assert code == 0 and done["result"]["outcome"] == "ACCEPTED_WHOLE", done
    _finish(run, capsys)
    report, _warnings = build_report(run["project"], root.name)
    text = render_markdown(report)
    assert report["reviewer_isolation_state"] == "verified" and "SELF" in report["review"] and "Ревью: в той же сессии (SELF), 2 круга" in text and "INDEPENDENT" not in text
