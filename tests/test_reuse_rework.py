"""V5b-2: a set the first review accepted in part, the generator reworked and the second round judged is reused with its verdict: ``prepare --reuse-from`` takes
``tc-reviewer:canonical``, ``tc-generator:rework-1`` and ``tc-reviewer:rework-1`` of an earlier run, so the new run has the very same set and the same accepted and rejected
cases with no call of a model.  (The cases accepted whole: ``test_reuse_cases.py``; the helpers are theirs and those of the tests of the rework.)"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_review_canaries import _fake_seed
from tests.test_review_open_recovery import _crash_once
from tests.test_reuse_cases import REVIEW, _answers, _follow, _journal, _reuse
from tests.test_rework_review import REVIEW as SECOND, _answer as second_answer, _open as open_second, _ready, _self_run
from tests.test_rework_stage import STAGE as REWORK, _finding, _ids, _reviewed
from tests.test_stage_steps import _call, _put
from tools import orchestrate_test_case_revision as orchestrate, pilot_state, review_canaries


@pytest.fixture(autouse=True)
def _rework_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the tests of the rework set for every run of their own: one round of the rework, half of the cases may be sent back, no real canaries."""
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    monkeypatch.setattr(review_canaries, "seed", _fake_seed)


class _Out:
    """The ``capsys`` of the helpers for a fixture of the module: the commands print into it."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def readouterr(self) -> SimpleNamespace:
        text = self.buffer.getvalue()
        self.buffer.seek(0)
        self.buffer.truncate()
        return SimpleNamespace(out=text, err="")


def _shared(tmp_path_factory: pytest.TempPathFactory, name: str, **kwargs) -> dict:
    """A source for the tests of the module that only read it (building one takes minutes)."""
    out, patch = _Out(), pytest.MonkeyPatch()
    with contextlib.redirect_stdout(out.buffer):
        run = _source(tmp_path_factory.mktemp(name), patch, out, **kwargs)
    patch.undo()
    return run


@pytest.fixture(scope="module")
def accepted(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _shared(tmp_path_factory, "accepted")


@pytest.fixture(scope="module")
def rejected(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _shared(tmp_path_factory, "rejected", verdict="ТРЕБУЕТ ДОРАБОТКИ")


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], verdict: str = "ПРИНЯТО", mixed: bool = False) -> dict:
    """A run whose first review sent a case back, the generator reworked it and the second round answered (``verdict``).
    ``mixed``: the run admitted SELF, the first review was independent, the second round was the same session."""
    run = _self_run(tmp_path, monkeypatch, capsys) if mixed else _ready(tmp_path, monkeypatch, capsys)
    assert open_second(run, capsys, *(("--fresh-context", "no") if mixed else ()))[0] == 0
    findings = [_finding("BLOCKING", [_ids(run)[0]["case_id"]], "Снова нет проверки.")] if verdict != "ПРИНЯТО" else []
    _put(run["run_root"], SECOND, second_answer(run, verdict, *findings))
    assert _call(capsys, "submit", run["run_root"], SECOND)[0] == 0
    return run


def _take(run: dict, *more: str) -> list[dict]:
    """The batches and the first review of the source by ``--reuse-from``, then the stages named."""
    return [_reuse(run, stage) for stage in (*run["stages"], REVIEW, *more)]


def _all(run: dict) -> dict:
    return _take(run, REWORK, SECOND)[-1]


# ---- the answers are taken as they are: the mechanics the code puts in the answer of the rework and the word of the reviewer are put once ------------------------

def test_the_recorded_answers_of_the_rework_are_what_the_code_would_make_of_them_again(accepted: dict) -> None:
    from tools.batch_assembly import fill_rework_mechanics
    from tools.review_canaries import hash_codeword

    src = accepted
    answers = {stage: pilot_state.read_model_stage_artifact(src["run_root"], src["attempt_id"], stage, digest)["artifact"] for stage, digest in _answers(src["run_root"]).items()}
    assert fill_rework_mechanics(answers[REWORK])[0] == answers[REWORK] and hash_codeword(answers[SECOND]) == answers[SECOND]


@pytest.mark.parametrize("name", ["accepted", "rejected"])
def test_the_new_run_has_the_set_and_the_verdict_of_the_source_and_calls_no_model(request: pytest.FixtureRequest, name: str) -> None:
    src = request.getfixturevalue(name)
    run = _follow(src)
    done = _take(run, REWORK, SECOND)
    assert all(row["model_call"] is False for row in done) and done[-1]["reused_from"] == run["source"] and done[-1]["independence"] == "verified"
    assert _answers(run["run_root"]) == _answers(src["run_root"])  # byte for byte
    effective = [dict(pilot_state.read_effective_canonical(row["run_root"], row["attempt_id"])) for row in (src, run)]
    assert effective[0]["document_digest"] == effective[1]["document_digest"] and effective[0]["document"] == effective[1]["document"]
    assert sorted(path.name for path in (run["run_root"] / "published").iterdir()) == sorted(path.name for path in (src["run_root"] / "published").iterdir())


def test_the_requests_of_the_rework_and_of_its_review_hold_digests_of_content_the_new_run_has_too(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    _all(run)
    for stage in (REWORK, SECOND):
        asked = [pilot_state.read_model_request(row["run_root"], row["attempt_id"], stage, next(
            event["artifact_digest"] for event in pilot_state.derive_state(row["run_root"])["events"] if event["event_type"] == "MODEL_REQUESTED" and event["stage_instance_id"] == stage))
            for row in (src, run)]
        assert asked[0]["input_digests"] == asked[1]["input_digests"] and asked[1]["reuse"]["run_id"] == run["source"]


# ---- what is not taken ------------------------------------------------------------------------------------------------------------------

def _not_taken(src: dict) -> None:
    with pytest.raises(pilot_state.ModelReuseRefused, match="доработка не завершена"):
        _follow(src)


def test_a_rework_nobody_reviewed_and_a_second_round_closed_with_abort_are_not_taken_and_the_reason_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    src = _ready(tmp_path, monkeypatch, capsys)  # the rework is answered, the second round is not
    _not_taken(src)
    assert open_second(src, capsys)[0] == 0
    assert _call(capsys, "submit", src["run_root"], SECOND, "--abort", "REVIEW_UNUSABLE")[0] == 0
    _not_taken(src)


def test_a_rework_given_up_is_not_taken_and_the_reason_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    src, _done = _reviewed(tmp_path, monkeypatch)
    assert _call(capsys, "submit", src["run_root"], REWORK, "--abort", "REWORK_UNUSABLE")[0] == 0
    _not_taken(src)


def test_a_damaged_answer_of_the_second_round_is_refused_and_the_journal_of_the_new_run_is_as_it_was(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    _take(run)
    target = pilot_state._model_stage_artifact_target(src["run_root"], src["attempt_id"], _answers(src["run_root"])[SECOND])
    data, before = target.read_bytes(), _journal(run["run_root"])
    target.write_bytes(data.replace(b"reviewed_case_ids", b"reviewed_case_idx", 1))
    try:
        with pytest.raises(pilot_state.ReuseSourceInvalid, match="model-stage-artifacts"):
            _reuse(run, REWORK)
        assert _journal(run["run_root"]) == before
    finally:
        target.write_bytes(data)


def test_a_damaged_receipt_of_the_rework_makes_the_source_invalid_and_the_scan_goes_on_without_it(accepted: dict) -> None:
    from tests.test_reuse_cases import _new

    src = accepted
    target = pilot_state._receipt_target(src["run_root"], src["attempt_id"], "rework-round")
    data = target.read_bytes()
    target.write_bytes(data[:-5])
    try:
        with pytest.raises(pilot_state.ReuseSourceInvalid, match="rework-round"):
            _follow(src)
        offered = pilot_state.marker_reuse_suggestion(*_new(src["project"]))  # the search does not fail for one damaged run: the source is skipped
        assert offered["run_id"] != src["run_root"].name
    finally:
        target.write_bytes(data)


# ---- the independence of the two rounds is the source's, the same session is SELF and needs the consent of the human ---------------------------------------

def test_a_second_round_of_the_same_session_is_not_taken_without_the_consent_and_is_self_to_the_report_with_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import build_report, render_markdown

    src = _source(tmp_path, monkeypatch, capsys, mixed=True)
    run = _follow(src)  # the first round was independent: no consent is needed for it
    _take(run, REWORK)
    before = _journal(run["run_root"])
    with pytest.raises(pilot_state.IndependenceUnverified):
        _reuse(run, SECOND)
    assert _journal(run["run_root"]) == before
    consenting = _follow(src, consent=True)
    done = _all(consenting)
    assert done["independence"] == "independence_unverified"
    report, _warnings = build_report(consenting["project"], consenting["run_root"].name)
    assert "Ревью: в той же сессии (SELF), 2 круга" in render_markdown(report)


# ---- a prepare cut short is finished by the repeat -----------------------------------------------------------------------------------------------------

def test_a_repeat_of_a_cut_prepare_of_the_second_round_finishes_the_reuse_and_publishes_the_set_once(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    src = accepted
    run = _follow(src)
    _take(run, REWORK)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        with pytest.raises(OSError):
            _reuse(run, SECOND)
    assert pilot_state.read_effective_canonical_if_present(run["run_root"], run["attempt_id"]) is None
    done = _reuse(run, SECOND)
    assert done["answered"] is True and done["reused_from"] == run["source"]
    assert pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])["document_digest"] == pilot_state.read_effective_canonical(src["run_root"], src["attempt_id"])["document_digest"]
