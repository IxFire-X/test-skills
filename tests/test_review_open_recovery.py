"""P2-fix (hunt 7): the two-command review after an interruption and in the corners.

- 7.1: review-open cut short at each step of opening the session (boundary receipt, its two events, the ledger) and after REVIEW_REQUESTED —
  the same call again finishes the opening, nothing written twice;
- 7.3: review-select --verdict when the session is already closed (aborted or completed) and no answer is recorded — REVIEW_CLOSED before
  anything is written;
- 7.4: the plan (``submit --stage plan``) publishes the header, then the plan, when the checks pass;
- 7.5: the mixed path (old open, then review-select --verdict without a request) points to review-open."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_in_order, assert_refused_once, journal_minus_refusals
from tests.test_review_requested import _api, _generated_run, _journal
from tests.test_review_two_commands import _bundles, _flags, _open, _select_verdict, _shape, _verdict


def _main(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _open_argv(run: dict) -> list[str]:
    return ["review-open", *_flags(run), "--output-dir", _bundles(run), "--model-id", "model-reviewer",
            "--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes"]


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
def test_review_open_cut_short_is_finished_by_the_same_call(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, point)
        code, first = _main(capsys, _open_argv(run))
        assert code == 2, first
    code, again = _main(capsys, _open_argv(run))
    assert code == 0 and again["result"]["model_request"]["stage"] == "tc-reviewer", f"{point}: {again}"
    # nothing twice: the shape of the journal is the one of an opening that was never cut
    other = _generated_run(tmp_path / "plain")
    assert _open(other)[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"]), point
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected


def test_the_opening_after_the_recovery_is_idempotent(tmp_path: Path, monkeypatch, capsys) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "ledger")
        assert _main(capsys, _open_argv(run))[0] == 2
    assert _main(capsys, _open_argv(run))[0] == 0
    before = _journal(run["run_root"])
    assert _main(capsys, _open_argv(run))[0] == 0 and _journal(run["run_root"]) == before


def test_another_reviewer_after_a_cut_opening_is_still_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "ledger")
        assert _main(capsys, _open_argv(run))[0] == 2
    code, refused = _open(run, invocation="another-reviewer")
    assert code == 2 and refused["code"] == "REVIEW_ALREADY_OPEN", refused


# 7.3 --------------------------------------------------------------------------------------

def test_a_verdict_after_the_session_was_aborted_is_refused_before_it_is_recorded(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, aborted = _api("review-abort", *_flags(run), "--reason", "REVIEW_UNUSABLE", "--out", str(run["tmp"] / "session.json"))
    assert code == 0, aborted
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", str(run["tmp"] / "session.json"))
    assert code == 0, selected
    answer = _verdict(run)
    before = _journal(run["run_root"])
    for extra in ((), ("--dry-run",), ("--reviewer-session", str(run["tmp"] / "session.json"))):
        code, late = _select_verdict(run, answer, *extra)
        assert code == 2 and late["code"] == "REVIEW_CLOSED" and "scan --new-run" in late["fix"], late
    assert _journal(run["run_root"]) == before
    assert_refused_in_order(run["run_root"], before, ("review-select", "REVIEW_CLOSED"), ("review-select", "REVIEW_CLOSED"))  # J1: the dry run leaves no event, the other two one each


def test_the_closed_review_does_not_refuse_the_answer_it_already_has(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    answer = _verdict(run)
    assert _select_verdict(run, answer)[0] == 0  # completed
    code, again = _select_verdict(run, answer)  # the same recorded answer: the same selection, not REVIEW_CLOSED
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again


# 7.4 --------------------------------------------------------------------------------------

def test_plan_still_publishes_header_then_plan_when_the_checks_pass(tmp_path: Path, capsys) -> None:
    from tests.test_scope_pause import _paused_run, _submit_plan

    root, *_ = _paused_run(tmp_path, warnings=[])
    code, done = _submit_plan(capsys, root)
    assert code == 0, done


# 7.5 --------------------------------------------------------------------------------------

def test_the_mixed_path_verdict_without_a_request_points_to_review_open(tmp_path: Path, capsys) -> None:
    run = _generated_run(tmp_path)
    start = str(run["tmp"] / "start.json")
    assert _main(capsys, ["review-publish", *_flags(run), "--output-dir", _bundles(run)])[0] == 0
    code, started = _main(capsys, ["session-start", *_flags(run), "--output-dir", _bundles(run), "--fresh-context", "yes",
                                   "--reviewer-invocation-id", "reviewer-subagent-1", "--out", start])
    assert code == 0, started
    assert _main(capsys, ["review-open", *_flags(run), "--output-dir", _bundles(run), "--session-start", start])[0] == 0
    before = _journal(run["run_root"])
    code, refused = _main(capsys, ["review-select", *_flags(run), "--output-dir", _bundles(run), "--verdict", _verdict(run)])
    assert code == 2 and refused["code"] == "MODEL_STAGE_NOT_REQUESTED", refused
    assert "review-open" in refused["fix"] and "model-request" not in refused["fix"].split("review-open")[0], refused["fix"]
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, "MODEL_STAGE_NOT_REQUESTED", "review-select")  # J1: the one event the refusal itself added


def test_a_canary_binding_cut_short_is_finished_and_a_real_divergence_is_not(tmp_path: Path, monkeypatch, capsys) -> None:
    """P7: the same call again finishes the binding of the canaries; a map that does not match the receipt is still REVIEW_CANARY_MISMATCH."""
    from tools import review_canaries

    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_points(broken, "canary-published")
        assert _main(capsys, _open_argv(run))[0] == 2
    mapped = next((run["run_root"] / "review-views").glob("*.canaries.json"))
    mapped.write_bytes(mapped.read_bytes() + b" ")  # a real divergence of bytes, not a cut
    code, refused = _main(capsys, _open_argv(run))
    assert code == 2 and "REVIEW_CANARY_MISMATCH" in json.dumps(refused, ensure_ascii=False), refused
    assert review_canaries.BATCH_ID == "review-canaries-v1"
