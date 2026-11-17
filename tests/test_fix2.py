"""fix2 (v0.6): the report and the hints of the state of a run say what is true — a closed attempt loses no receipt, a rework in progress is no «never», the scan names the consent."""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_f1 import _opened, _pipeline
from tests.test_review_two_commands import _select_verdict, _verdict


def _closed(tmp_path: Path, capsys) -> dict:
    """A cases-only attempt closed by ``finish`` after an accepting review."""
    run = _opened(tmp_path)
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0, selected
    code, closed = _pipeline(capsys, ["finish", "--project", str(run["project"]), "--run", Path(run["run_root"]).name])
    assert closed.get("status") == "ok", closed
    return run


# ---- 1: the files of the closure and the result are no lost receipts ----------------------------------------------------------------

def test_a_closed_attempt_with_all_its_files_has_no_lost_receipt(tmp_path: Path, capsys) -> None:
    from tools import pilot_state

    run = _closed(tmp_path, capsys)
    root = Path(run["run_root"])
    assert pilot_state.derive_state(root)["attempts"][0]["state"] == "TERMINAL"
    assert pilot_state.lost_attempt_receipts(root, pilot_state.derive_state(root), run["attempt_id"]) == []


# ---- 4: the scan names the consent for the review of a rework made in the session of the rework ------------------------------------------------

def test_the_scan_names_the_consent_for_a_second_round_of_the_same_session(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_reuse_cases import _scanned
    from tests.test_review_canaries import _fake_seed
    from tests.test_reuse_rework import SECOND, _source
    from tools import orchestrate_test_case_revision as orchestrate, pilot_state, review_canaries

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)  # what the tests of the rework set for every run of theirs
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    src = _source(tmp_path, monkeypatch, capsys, mixed=True)
    run_root, attempt_id = _scanned(src)
    rows = pilot_state.marker_reuse_suggestion(run_root, attempt_id)["stages"]
    second = next(row for row in rows if row["stage"] == SECOND)
    assert second["review"] == "SELF" and "согласие" in second.get("needs", ""), second


# ---- 5: the file of the rework round without its events (a submit cut after the file) is finished by the repeat of the submit ---------------------------

def _cut_rework(tmp_path: Path, monkeypatch) -> tuple[dict, dict]:
    """The first review sent a case back for the rework; ``submit`` is cut after the file ``rework-round`` was written, before its events.  The answer is the one to be repeated."""
    from tests.test_review_canaries import _answer, _boundary_and_removed, _finding, _ids, _open, _select
    from tests.test_review_open_recovery import _crash_once
    from tests.test_review_requested import _generated_run
    from tools import orchestrate_test_case_revision as orchestrate, pilot_state

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    answer = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[*_boundary_and_removed(run), _finding("BLOCKING", [_ids(run)[0]["case_id"]], "Нет проверки состояния.")])
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "_bind_events", when=lambda root, attempt, digest, batch_id=None: batch_id == pilot_state.REWORK_BATCH)
        try:
            _select(run, answer)
        except OSError:
            pass
    assert (Path(run["run_root"]) / "rework-rounds" / f"{run['attempt_id']}.json").is_file()  # the file is there, the events are not
    return run, answer


def test_the_repeat_of_a_submit_cut_after_the_rework_file_goes_on(tmp_path: Path, monkeypatch) -> None:
    from tests.test_review_canaries import _select
    from tools import orchestrate_test_case_revision as orchestrate, pilot_state

    run, answer = _cut_rework(tmp_path, monkeypatch)
    _select(run, answer)  # not «unbound factual receipt»
    state = pilot_state.derive_state(run["run_root"])
    assert orchestrate.rework_state(run["run_root"], run["attempt_id"]) == "not_started"
    assert [row["event_type"] for row in state["events"] if row.get("batch_id") == pilot_state.REWORK_BATCH] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]


def test_a_rework_file_laid_beside_the_journal_does_not_become_a_receipt_by_a_read(tmp_path: Path, monkeypatch) -> None:
    """A file the journal does not bind (a cut submit leaves the same one, an agent can lay one): the readers say «no rework» and write nothing."""
    from tools import orchestrate_test_case_revision as orchestrate, pilot_state

    run, _answer = _cut_rework(tmp_path, monkeypatch)
    root, journal = Path(run["run_root"]), Path(run["run_root"]) / "events.jsonl"
    before = journal.read_bytes()
    assert pilot_state.rework_round_if_present(root, run["attempt_id"]) is None and orchestrate.rework_state(root, run["attempt_id"]) is None
    assert journal.read_bytes() == before


def test_the_scan_writes_nothing_into_an_earlier_run_whose_rework_file_lacks_its_events(tmp_path: Path, monkeypatch) -> None:
    from tests.test_reuse_cases import _scanned
    from tools import pilot_state

    src, _answer = _cut_rework(tmp_path, monkeypatch)
    run_root, attempt_id = _scanned(src)
    before = (Path(src["run_root"]) / "events.jsonl").read_bytes()
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] == Path(src["run_root"]).name and offered["cases"]["reused"] is False, offered  # a run whose review is not selected gives its marker (tails13), not its cases
    assert (Path(src["run_root"]) / "events.jsonl").read_bytes() == before  # and it was only read


# ---- 6: the file of the materialization ownership without its events still forbids the revision two -------------------------------------------------

def test_a_cut_materialize_that_left_the_ownership_file_without_events_forbids_revision_two(tmp_path: Path, monkeypatch) -> None:
    """The sibling test of ``test_materialization_failure`` cuts after the events; here the cut is between the file and its events (the level of those tests: the chain of the local-pilot mode takes the same call)."""
    import pytest

    from tests.test_generated_delta import _inputs
    from tests.test_review_open_recovery import _crash_once
    from tools import pilot_state
    from tools.generated_delta import materialize_delta

    baseline, automation, review, document, run_root, attempt_id = _inputs(tmp_path)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "_bind_events", when=lambda root, attempt, digest, batch_id=None: (Path(root) / "materialization-ownership").exists())
        with pytest.raises(Exception):  # noqa: B017 - the simulated crash, in whatever envelope the command gives it
            materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)
    assert (run_root / "materialization-ownership" / f"{attempt_id}.json").is_file()
    with pytest.raises(ValueError, match="automation revision two is forbidden after materialization"):
        pilot_state.open_automation_review_boundary(run_root, attempt_id, {"automation_revision": 2})
    delta = materialize_delta(tmp_path, tmp_path, baseline, automation, review, canonical_document=document, run_root=run_root, attempt_id=attempt_id)  # the honest way: the repeat finishes
    assert delta["facts"]["completion"] == "COMPLETE"


# ---- 7: a file the agent could write is a refusal, never a crash ----------------------------------------------------------------------------------

def test_a_boundary_receipt_whose_digest_field_is_a_number_is_refused_with_a_value_error(tmp_path: Path) -> None:
    """The receipt is sealed again after the edit: only the check of the field itself can refuse it."""
    import pytest

    from tests.test_finish import _automation_chain
    from tools import pilot_state

    root, attempt_id = _automation_chain(tmp_path, ["ПРИНЯТО"])
    path = pilot_state._receipt_target(root, attempt_id, "automation-review-boundary-r1")
    body = {key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items() if key != "digest"}
    body["automation_digest"] = 1
    path.write_bytes(pilot_state._canonical_bytes(pilot_state._sealed(body)))
    with pytest.raises(ValueError, match="invalid automation review boundary"):
        pilot_state.read_attempt_receipt(root, attempt_id, "automation-review-boundary-r1", "ARTIFACT_READ_BACK")
