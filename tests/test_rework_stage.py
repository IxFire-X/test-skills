"""V7a: the rework of the rejected cases through the commands of the agent — ``status`` names ``prepare tc-generator:rework-1``, ``prepare`` publishes the
request and the view, the answer goes to its slot, ``submit`` checks it before the write; ``finish`` closes a rework nobody reviewed.

The first review is the old commands (V4 moves them); ``limits.rework_rounds`` is set the way ``review_reject_share`` is in the tests of V6."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_review_canaries import _answer, _boundary_and_removed, _candidate, _finding, _generated_run, _ids, _open, _select
from tests.test_stage_steps import _call, _events, _put, _status
from tools import pilot_state, stage_steps

STAGE = "tc-generator:rework-1"


def _reviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rounds: int = 1, findings=None, verdict: str = "ТРЕБУЕТ ДОРАБОТКИ") -> tuple[dict, dict]:
    """The first review of the two-case fixture: the first case rejected by a blocking finding beside the canaries; the select's printed answer."""
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: rounds)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [one["case_id"]], "Нет проверки состояния.")] if findings is None else findings(run)
    return run, _select(run, _answer(run, verdict, findings=findings))


def _reworked(run: dict, **change) -> dict:
    case = copy.deepcopy(_ids(run)[0])
    return {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": [{**case, "title": "Доработанный кейс", **change}]}


def _published(run: dict) -> list[str]:
    folder = run["run_root"] / "published"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def _finish(run: dict, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from tools.run_pipeline import cmd_finish

    capsys.readouterr()
    code = cmd_finish(SimpleNamespace(project=str(run["project"]), run=run["run_root"].name))
    return code, json.loads(capsys.readouterr().out)


def test_the_first_select_does_not_publish_and_status_names_the_rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, done = _reviewed(tmp_path, monkeypatch)
    assert done["status"] == "REWORK_PENDING" and done["effective_source"] is None and _published(run) == []
    assert pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is None
    status = _status(run["run_root"])
    assert (status["state"], status["next"]) == ("generator", {"verb": "prepare", "stage": STAGE})


def test_prepare_publishes_the_request_and_the_view_of_the_rejected_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    code, out = _call(capsys, "prepare", root, STAGE)
    assert code == 0 and out["result"]["answer_slot"] == str(stage_steps.slot(root, STAGE)) and "nonce" not in out["result"]
    assert _events(root)[-1] == f"MODEL_REQUESTED:{STAGE}"
    view = next((root / "review-views").glob("*.rework-view.json"))
    value = json.loads(view.read_text(encoding="utf-8"))
    one = _ids(run)[0]
    assert [case["case_id"] for case in value["cases"]] == [one["case_id"]] and value["findings"][one["case_id"]][0]["message"] == "Нет проверки состояния."
    assert _status(root)["next"] == {"verb": "submit", "stage": STAGE}
    listed = json.loads(Path(out["result"]["read"]["list_path"]).read_text(encoding="utf-8"))["files"]
    assert [row["basis"] for row in listed if row["path"].endswith("rework.md")] == ["роль доработки"] and any(row["path"].endswith(".rework-view.json") for row in listed)


def test_the_answer_is_recorded_once_and_status_then_names_the_review_of_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _reworked(run))
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 0 and _events(root)[-2:] == [f"MODEL_RESPONSE_RECEIVED:{STAGE}", f"CANDIDATE_PUBLISHED:{STAGE}"]
    before = _events(root)
    code, again = _call(capsys, "submit", root, STAGE)
    assert code == 0 and again["result"]["answered"] is True and _events(root) == before
    assert _status(root)["next"] == {"verb": "prepare", "stage": "tc-reviewer:rework-1"}  # V7b: the second round; finish before it closes the rework unfinished


@pytest.mark.parametrize("change, code", [
    ({"requirement_ids": ["CREQ-X"]}, "REWORK_CASE_IDENTITY"), ({"display_order": 9}, "REWORK_CASE_IDENTITY"), ({"case_id": "TC-ELSE"}, "REWORK_CASE_SET"),
])
def test_an_answer_that_changes_the_identity_of_the_case_is_refused_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                         capsys: pytest.CaptureFixture[str], change: dict, code: str) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _reworked(run, **change))
    before = _events(root)
    refused, out = _call(capsys, "submit", root, STAGE)
    assert refused == 2 and out["code"] == code and out["fix"] and _events(root) == before


def test_an_answer_that_holds_the_case_the_review_accepted_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    accepted = copy.deepcopy(_ids(run)[1])
    answer = _reworked(run)
    answer["test_cases"].append({**accepted, "title": "Чужой кейс изменён"})
    _put(root, STAGE, answer)
    before = _events(root)
    refused, out = _call(capsys, "submit", root, STAGE)
    assert refused == 2 and out["code"] == "REWORK_FOREIGN_CASE_CHANGED" and _events(root) == before


def test_an_answer_without_the_rejected_case_is_refused_and_the_one_retry_takes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, {**_reworked(run), "test_cases": []})
    assert _call(capsys, "submit", root, STAGE)[1]["code"] == "REWORK_CASE_SET"
    _put(root, STAGE, _reworked(run))
    assert _call(capsys, "submit", root, STAGE, "--transport-attempts", "2")[0] == 0


def test_a_dry_run_checks_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _reworked(run))
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE, "--dry-run")
    assert code == 0 and out["result"]["dry_run"] is True and _events(root) == before


def test_a_slot_that_is_a_link_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(_reworked(run)), encoding="utf-8")
    try:
        stage_steps.slot(root, STAGE).symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _events(root) == before


def test_an_answer_in_the_slot_of_another_stage_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, "tc-generator:BATCH-other", _reworked(run))  # a slot of a batch, not of the rework
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 2 and out["code"] == "ANSWER_FILE_UNREADABLE" and _events(root) == before


def test_a_second_round_of_the_rework_is_a_stage_the_code_does_not_know(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    code, out = _call(capsys, "prepare", run["run_root"], "tc-generator:rework-2")
    assert code == 2 and out["code"] == "STAGE_UNKNOWN"


def _whole(run: dict) -> list[dict]:
    return _boundary_and_removed(run)  # only the canaries block: the whole set stands


def _no_case(run: dict) -> list[dict]:
    return [_finding("BLOCKING", ["CREQ-OTHER"], "Про набор в целом.")]


@pytest.mark.parametrize("rounds, findings, verdict", [
    (1, _whole, "ТРЕБУЕТ ДОРАБОТКИ"), (1, _no_case, "ТРЕБУЕТ ДОРАБОТКИ"), (0, None, "ТРЕБУЕТ ДОРАБОТКИ"), (1, lambda run: [], "ПРИНЯТО"),
], ids=["accepted-whole", "whole-set-rework", "rounds-zero", "accepted"])
def test_the_rework_is_not_asked_for_where_the_review_sent_no_case_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                                      rounds: int, findings, verdict: str) -> None:
    run, done = _reviewed(tmp_path, monkeypatch, rounds, findings, verdict)
    root = run["run_root"]
    assert done["status"] != "REWORK_PENDING" and _call(capsys, "prepare", root, STAGE)[1]["code"] == "STAGE_UNKNOWN"
    with pytest.raises(ValueError, match="MODEL_INPUTS_MISMATCH|REWORK|request"):
        pilot_state.publish_model_request(root, run["attempt_id"], STAGE, model_id=None, invocation_id="x", input_digests=[])


def test_the_rework_is_not_asked_for_before_the_review_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    assert _call(capsys, "prepare", run["run_root"], STAGE)[1]["code"] == "STAGE_UNKNOWN"
    with pytest.raises(ValueError):
        pilot_state.publish_model_request(run["run_root"], run["attempt_id"], STAGE, model_id=None, invocation_id="x", input_digests=[])


def test_a_live_edit_of_the_limit_after_the_first_select_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import orchestrate_test_case_revision as orchestrate

    run, _done = _reviewed(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)  # the file says 0 now (or says nothing: a drift reads as the default)
    assert _status(run["run_root"])["next"] == {"verb": "prepare", "stage": STAGE} and _call(capsys, "prepare", run["run_root"], STAGE)[0] == 0


def test_the_rounds_are_read_from_the_limits_the_run_froze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import step_size
    from tools.orchestrate_test_case_revision import rework_rounds

    run = _generated_run(tmp_path)
    args = (run["run_root"], run["attempt_id"])
    for limits, wanted in (({"rework_rounds": 1}, 1), ({"rework_rounds": 0}, 0), ({}, 1), ({"rework_rounds": 7}, 1)):
        monkeypatch.setattr(step_size, "run_limits", lambda *_args, limits=limits: (limits, None))
        assert rework_rounds(*args) == wanted


# ---- the close: the first review sent cases back and nobody reviewed the rework ---------------------------------------------------

def _closed(run: dict, capsys: pytest.CaptureFixture[str]) -> tuple[dict, str]:
    _code, out = _finish(run, capsys)
    assert out["reason_code"] == "PARTIAL_ACCEPTED", out
    published = run["run_root"] / "published"
    name = next(path for path in published.glob("*.r2.REJECTED-CASES.md"))
    return out, name.read_text(encoding="utf-8")


def test_finish_without_a_rework_publishes_r2_and_the_rejected_case_as_r1_had_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    _out, rejected = _closed(run, capsys)
    assert "доработка не запускалась" in rejected and "Нет проверки состояния." in rejected
    assert len(_published(run)) == 6 and pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])["document"]["revision"] == 2


def test_finish_after_a_request_without_an_answer_says_the_rework_was_not_finished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    _call(capsys, "prepare", run["run_root"], STAGE)
    _out, rejected = _closed(run, capsys)
    assert "доработка не завершена" in rejected and "доработка не запускалась" not in rejected


def test_finish_after_an_answer_nobody_reviewed_shows_the_case_of_r1_not_the_reworked_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _reworked(run))
    _call(capsys, "submit", root, STAGE)
    _out, rejected = _closed(run, capsys)
    assert "доработка не завершена" in rejected and "Доработанный кейс" not in rejected and _candidate(run)["test_cases"][0]["title"] in rejected


def test_a_repeated_finish_publishes_nothing_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.orchestrate_test_case_revision import close_pending_rework

    run, _done = _reviewed(tmp_path, monkeypatch)
    _closed(run, capsys)
    files, effective = {path.name: path.read_bytes() for path in (run["run_root"] / "published").iterdir()}, pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])
    before = _events(run["run_root"])
    assert close_pending_rework(run["run_root"], run["attempt_id"]) == [] and _events(run["run_root"]) == before  # the close itself has nothing left to write
    assert _finish(run, capsys)[1]["idempotent"] is True  # the retry of a closed attempt writes its own observation, as for every finish
    assert {path.name: path.read_bytes() for path in (run["run_root"] / "published").iterdir()} == files
    assert pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"]) == effective


def test_finish_closes_a_run_without_a_rework_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, done = _reviewed(tmp_path, monkeypatch, rounds=0)
    assert done["status"] == "EFFECTIVE_SELECTED" and len(_published(run)) == 6  # V6: r2 at the select, the words «не запускалась»
    before = _published(run)
    _finish(run, capsys)
    assert _published(run) == before


# ---- the limit in a real .skillsrc, and what an agent can plant ---------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 1])
def test_the_limit_of_a_real_skillsrc_is_what_the_scan_froze(tmp_path: Path, value: int) -> None:
    from tests.test_marker_parts import _run
    from tools.orchestrate_test_case_revision import rework_rounds

    (tmp_path / "a").mkdir()
    _run(tmp_path / "a", files=1)
    content = json.loads((tmp_path / "a" / ".skillsrc").read_text(encoding="utf-8"))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / ".skillsrc").write_text(json.dumps({**content, "limits": {"rework_rounds": value}}), encoding="utf-8")
    root, attempt_id, *_rest = _run(tmp_path / "b", files=1)
    assert rework_rounds(root, attempt_id) == value
    (tmp_path / "b" / ".skillsrc").write_text(json.dumps({**content, "limits": {"rework_rounds": 1 - value}}), encoding="utf-8")  # edited after the scan: the file is no longer the frozen one
    assert rework_rounds(root, attempt_id) == 1  # the default of the code: V7b — the rejected cases go back


def test_the_schema_of_the_skillsrc_refuses_more_than_one_round(tmp_path: Path) -> None:
    from tests.test_marker_parts import _run
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    _run(tmp_path, files=1)
    content = json.loads((tmp_path / ".skillsrc").read_text(encoding="utf-8"))
    for value in (2, -1, "1"):
        with pytest.raises(ValueError):
            parse_skillsrc_bytes(json.dumps({**content, "limits": {"rework_rounds": value}}).encode("utf-8"), project=tmp_path)


def test_a_receipt_of_another_answer_of_the_reviewer_is_no_rework_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import rework_basis

    run, _done = _reviewed(tmp_path, monkeypatch, rounds=0)  # the review is closed and its set published; the receipt is planted
    pilot_state.publish_rework_round(run["run_root"], run["attempt_id"], document_sha256(_candidate(run)), "sha256:" + "2" * 64)
    with pytest.raises(ValueError, match="REWORK_NOT_PENDING"):
        rework_basis(run["run_root"], run["attempt_id"])


def test_a_planted_receipt_file_and_a_forged_event_open_no_rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch, rounds=0)
    root = run["run_root"]
    (root / "rework-rounds").mkdir()
    (root / "rework-rounds" / f"{run['attempt_id']}.json").write_text(json.dumps({"rounds": 1}), encoding="utf-8")  # a file the agent can write: no event binds it
    before = _events(root)
    assert _call(capsys, "prepare", root, STAGE)[0] == 2 and _events(root) == before
    (root / "rework-rounds" / f"{run['attempt_id']}.json").unlink()
    try:  # the event alone, written by code that is not this one, opens only the request: the inputs still come from a valid receipt
        pilot_state.append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=run["attempt_id"], batch_id=pilot_state.REWORK_BATCH, artifact_digest="sha256:" + "1" * 64)
    except ValueError:
        return  # refused outright
    with pytest.raises(ValueError):
        pilot_state.publish_model_request(root, run["attempt_id"], STAGE, model_id=None, invocation_id="x", input_digests=[])
    assert not any(row.startswith("MODEL_REQUESTED:tc-generator:rework") for row in _events(root))


# ---- V7c: the case of the rework is written like a case of a batch — the code puts the mechanics, the identity of the case stays the model's ----

_MECHANICS = frozenset({"step_id", "input_id", "output_id", "expectation_id", "assertion_id", "display_order", "semantic_type", "sensitive", "automation_blockers"})


def _thin(value):
    """A step as the model writes it: without the ids, orders and types the code puts (``manual_only`` is the model's word, as in a batch)."""
    if isinstance(value, dict):
        return {key: _thin(item) for key, item in value.items() if key not in _MECHANICS}
    return [_thin(item) for item in value] if isinstance(value, list) else value


def _thin_answer(run: dict, new_steps: int = 2) -> dict:
    """The case as the reviewer saw it (its steps with their ids stay) and ``new_steps`` steps added without any service field."""
    case = copy.deepcopy(_ids(run)[0])
    case["steps"] += [_thin(case["steps"][0]) for _ in range(new_steps)]
    return {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": [case]}


def test_a_reworked_case_with_new_steps_and_no_service_fields_is_accepted_and_recorded_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                            capsys: pytest.CaptureFixture[str]) -> None:
    from tools.orchestrate_test_case_revision import rework_round_data

    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _thin_answer(run))
    code, out = _call(capsys, "submit", root, STAGE, "--dry-run")
    assert code == 0, [row["message"] for row in out.get("diagnostics", [])]
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 0, out
    data = rework_round_data(root, run["attempt_id"], need_answer=True)
    case = data["r3"]["test_cases"][0]
    steps = [step for case in data["r3"]["test_cases"] if case["case_id"] == _ids(run)[0]["case_id"] for step in case["steps"]]
    assert [step["display_order"] for step in steps] == list(range(1, len(steps) + 1)) and len({step["step_id"] for step in steps}) == len(steps) == 3
    other = _ids(run)[1]  # the case the review accepted stays as it was, byte for byte
    assert next(item for item in data["r3"]["test_cases"] if item["case_id"] == other["case_id"]) == other and case
    from tests.test_rework_review import _open as open_second

    code, opened = open_second(run, capsys)  # the second round sees the case with the fields the code put
    shown = json.loads(Path(opened["result"]["reviewer_view"]).read_text(encoding="utf-8"))["cases"][0]["steps"]
    assert code == 0 and [step["step_id"] for step in shown] == [step["step_id"] for step in steps]


def test_the_full_form_of_the_answer_of_the_rework_is_left_as_it_is_and_the_thin_one_becomes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.batch_assembly import fill_rework_mechanics

    run, _done = _reviewed(tmp_path, monkeypatch)
    filled, summary = fill_rework_mechanics(_thin_answer(run))
    assert summary and set(filled) == {"schema_version", "status", "batch_id", "test_cases"}  # no ``diagnostics`` of a batch in the answer
    assert fill_rework_mechanics(filled) == (filled, [])  # written in full, the answer is the same one


def test_a_reworked_case_without_its_own_display_order_is_refused_as_a_change_of_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    answer = _thin_answer(run)
    del answer["test_cases"][0]["display_order"]  # the code puts the order of the steps, not the place of the case
    _put(root, STAGE, answer)
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 2 and out["code"] == "REWORK_CASE_IDENTITY" and _events(root) == before


# ---- V7d: the agent gives the rework up (submit --abort): the set is published as for a rework nobody finished and the attempt stays open ----

ABORT = ("--abort", "REWORK_UNUSABLE")


def _unfinished_text(run: dict) -> str:
    return next(path for path in (run["run_root"] / "published").glob("*.REJECTED-CASES.md")).read_text(encoding="utf-8")


@pytest.mark.parametrize("before", ["no_request", "request_only", "refused_answer"])
def test_the_rework_given_up_publishes_the_set_as_for_an_unfinished_rework_and_the_attempt_stays_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                               capsys: pytest.CaptureFixture[str], before: str) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    if before != "no_request":
        _call(capsys, "prepare", root, STAGE)
    if before == "refused_answer":
        _put(root, STAGE, _reworked(run, requirement_ids=["CREQ-X"]))
        assert _call(capsys, "submit", root, STAGE)[0] == 2
    code, out = _call(capsys, "submit", root, STAGE, "--dry-run", *ABORT)
    assert code == 0 and out["result"]["outcome"] == "ABORTED" and _published(run) == [] and pilot_state.read_effective_canonical_if_present(root, run["attempt_id"]) is None
    code, out = _call(capsys, "submit", root, STAGE, *ABORT)
    assert code == 0 and out["result"]["outcome"] == "ABORTED", out
    assert ("доработка не запускалась" if before == "no_request" else "доработка не завершена") in _unfinished_text(run) and pilot_state.read_effective_canonical_if_present(root, run["attempt_id"]) is not None
    assert _status(root)["next"]["verb"] == "finish"  # the attempt is open: the cases mode names finish (the mode with autotests names the automation: test_partial_acceptance_run)
    files, before_events = _published(run), _events(root)
    assert _call(capsys, "submit", root, STAGE, *ABORT)[0] == 2 and _published(run) == files and _events(root)[: len(before_events)] == before_events  # the step is gone: nothing is written


def test_the_rework_given_up_after_its_answer_is_recorded_is_refused_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    _call(capsys, "prepare", root, STAGE)
    _put(root, STAGE, _reworked(run))
    assert _call(capsys, "submit", root, STAGE)[0] == 0
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE, *ABORT)
    assert code == 2 and out["code"] == "REWORK_ANSWERED" and "tc-reviewer:rework-1" in out["fix"] and _events(root) == before and _published(run) == []


def test_an_answer_put_in_the_slot_after_the_rework_was_given_up_is_refused_before_it_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    assert _call(capsys, "submit", root, STAGE, *ABORT)[0] == 0
    _put(root, STAGE, _reworked(run))
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE)
    assert code == 2 and out["code"] == "STAGE_UNKNOWN" and _events(root) == before


def test_the_rework_given_up_in_a_closed_attempt_is_refused_and_a_reason_outside_the_list_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    root = run["run_root"]
    code, out = _call(capsys, "submit", root, STAGE, "--abort", "WHATEVER")
    assert code == 2 and out["code"] == "REVIEWER_ABORT_REASON_INVALID" and "REWORK_UNUSABLE" in out["fix"] and _published(run) == []
    _finish(run, capsys)  # the attempt is closed by the real finish
    before = _events(root)
    code, out = _call(capsys, "submit", root, STAGE, *ABORT)
    assert code == 2 and out["code"] in {"ATTEMPT_TERMINAL", "STAGE_UNKNOWN"} and _events(root) == before


def test_a_request_of_the_rework_without_an_answer_and_without_the_result_stops_the_start_of_the_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run, _done = _reviewed(tmp_path, monkeypatch)
    assert _call(capsys, "prepare", run["run_root"], STAGE)[0] == 0
    with pytest.raises(ValueError, match="invalid event journal"):
        pilot_state._append_event(run["project"], run["run_root"], "EXECUTION_STARTED", actor="controller", attempt_id=run["attempt_id"], batch_id=None, artifact_digest="sha256:" + "1" * 64)
        pilot_state.derive_state(run["run_root"])
