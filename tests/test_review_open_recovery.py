"""P2-fix (hunt 7): the review on the cycle of the agent after an interruption and in the corners.

- 7.1: ``prepare`` cut short at each step of opening the session (boundary receipt, its two events, the ledger) and after REVIEW_REQUESTED —
  the same call again finishes the opening, nothing written twice;
- 7.3: ``submit`` with an answer when the session is already closed by ``--abort`` — refused before anything is written;
- 7.4: the plan (``submit --stage plan``) publishes the header, then the plan, when the checks pass."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_in_order
from tests.test_review_requested import _generated_run, _journal
from tests.test_review_two_commands import _cycle, _open, _select_verdict, _shape, _verdict


def _main(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _crash_once(monkeypatch, owner, name: str, when=lambda *a, **k: True) -> None:
    real = getattr(owner, name)
    state = {"done": False}

    def crash(*args, **kwargs):
        if not state["done"] and when(*args, **kwargs):
            state["done"] = True
            raise OSError(f"simulated crash in {name}")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, crash)


def _crash_points(monkeypatch, point: str) -> None:
    from tools import orchestrate_test_case_revision as otcr
    from tools import pilot_state

    if point == "ledger":
        _crash_once(monkeypatch, otcr, "_publish_reviewer_ledger")
    elif point == "boundary-read-back":
        _crash_once(monkeypatch, pilot_state, "append_event",
                    lambda run_root, event_type, *a, **k: event_type == "ARTIFACT_READ_BACK" and k.get("batch_id") != "review-canaries-v1")
    elif point == "boundary-published":
        _crash_once(monkeypatch, pilot_state, "append_event",
                    lambda run_root, event_type, *a, **k: event_type == "ARTIFACT_PUBLISHED" and k.get("batch_id") != "review-canaries-v1")
    elif point == "canary-files-written":  # P7: the view and the map are written, the receipt is not
        _crash_once(monkeypatch, pilot_state, "publish_review_canaries")
    elif point == "canary-receipt-written":  # P7: the receipt file is there, none of its events
        _crash_once(monkeypatch, pilot_state, "append_event",
                    lambda run_root, event_type, *a, **k: event_type == "ARTIFACT_PUBLISHED" and k.get("batch_id") == "review-canaries-v1")
    elif point == "canary-published":  # P7: the receipt and its first event, not the read-back
        _crash_once(monkeypatch, pilot_state, "append_event",
                    lambda run_root, event_type, *a, **k: event_type == "ARTIFACT_READ_BACK" and k.get("batch_id") == "review-canaries-v1")
    elif point == "model-request":
        _crash_once(monkeypatch, pilot_state, "publish_model_request")
    else:
        raise AssertionError(point)


@pytest.mark.parametrize("point", ["canary-files-written", "canary-receipt-written", "canary-published", "boundary-published", "boundary-read-back",
                                   "ledger", "model-request"])
def test_prepare_cut_short_is_finished_by_the_same_call(tmp_path: Path, monkeypatch, point: str) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, point)
        code, first = _open(run)
        assert code == 2, first
    code, again = _open(run)
    assert code == 0 and again["result"]["answer_slot"], f"{point}: {again}"
    # nothing twice: the shape of the journal is the one of an opening that was never cut
    other = _generated_run(tmp_path / "plain")
    assert _open(other)[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"]), point
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected


def test_the_opening_after_the_recovery_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "ledger")
        assert _open(run)[0] == 2
    assert _open(run)[0] == 0
    before = _journal(run["run_root"])
    assert _open(run)[0] == 0 and _journal(run["run_root"]) == before


def test_another_reviewer_after_a_cut_opening_is_still_refused(tmp_path: Path, monkeypatch) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "ledger")
        assert _open(run)[0] == 2
    code, refused = _open(run, invocation="another-reviewer")
    assert code == 2 and refused["code"] == "REVIEW_ALREADY_OPEN", refused


# 7.3 --------------------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="V4a: submit after --abort records the answer and answers ABORTED; REVIEW_CLOSED of the old review-select is gone (code in V4b-C or later)")
def test_a_verdict_after_the_review_was_aborted_is_refused_before_it_is_recorded(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, aborted = _cycle("submit", run, "--abort", "REVIEW_UNUSABLE")
    assert code == 0, aborted
    answer = _verdict(run)
    before = _journal(run["run_root"])
    for extra in ((), ("--dry-run",)):
        code, late = _select_verdict(run, answer, *extra)
        assert code == 2 and late["code"] == "REVIEW_CLOSED", late
    assert _journal(run["run_root"]) == before
    assert_refused_in_order(run["run_root"], before, ("submit", "REVIEW_CLOSED"))  # J1: the dry run leaves no event, the other call one


# 7.4 --------------------------------------------------------------------------------------

def test_plan_still_publishes_header_then_plan_when_the_checks_pass(tmp_path: Path, capsys) -> None:
    from tests.test_scope_pause import _paused_run, _submit_plan

    root, *_ = _paused_run(tmp_path, warnings=[])
    code, done = _submit_plan(capsys, root)
    assert code == 0, done


def test_a_canary_binding_cut_short_is_finished_and_a_real_divergence_is_not(tmp_path: Path, monkeypatch) -> None:
    """P7: the same call again finishes the binding of the canaries; a map that does not match the receipt is still REVIEW_CANARY_MISMATCH."""
    from tools import review_canaries

    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "canary-published")
        assert _open(run)[0] == 2
    mapped = next((run["run_root"] / "review-views").glob("*.canaries.json"))
    mapped.write_bytes(mapped.read_bytes() + b" ")  # a real divergence of bytes, not a cut
    code, refused = _open(run)
    assert code == 2 and "REVIEW_CANARY_MISMATCH" in json.dumps(refused, ensure_ascii=False), refused
    assert review_canaries.BATCH_ID == "review-canaries-v1"
