"""J1 (A1-1 / 8.4): a refusal is an event of the journal.

``COMMAND_REFUSED`` — a run-level event (no attempt, no batch, no stage) whose ``artifact_digest`` is the sha256 of the canonical bytes
of the line the refusal wrote to ``<run>/refusals.jsonl``; the line keeps the detail ({at, command, code, stage, batch}), the event is the
fact.  The report counts the events; a line without its event is not counted and is named in ``refusals.mismatch``; an event without its
line is counted, its detail lost.  The event is written through the ordinary ``_append_event`` only — a journal that does not pass its
checks takes no event.  No reader of the order of the events looks at it."""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run


def _events(run_root: Path) -> list[dict]:
    """The raw journal: this file is about the COMMAND_REFUSED events the shared ``_journal`` leaves out."""
    return [json.loads(line) for line in (run_root / "events.jsonl").read_bytes().decode("utf-8").splitlines()]


def _raw(run_root: Path) -> bytes:
    """events.jsonl byte for byte — a refusal that must add no event is compared against it, not against the journal minus refusals."""
    return (run_root / "events.jsonl").read_bytes()


def _refuse(run: dict, times: int = 1) -> None:
    for _ in range(times):
        code, refused = _api("read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "no-such-kind", "--event", "ARTIFACT_READ_BACK")
        assert code == 2, refused


def _rows(run_root: Path) -> list[bytes]:
    return [line for line in (run_root / "refusals.jsonl").read_bytes().split(b"\n") if line]


def _report(run: dict) -> dict:
    from tools.run_report import build_report

    return build_report(run["project"], run["run_root"].name)[0]


def test_a_refusal_is_an_event_bound_to_its_line(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _refuse(run)
    refused = [row for row in _events(run["run_root"]) if row["event_type"] == "COMMAND_REFUSED"]
    [row] = _rows(run["run_root"])
    assert len(refused) == 1 and refused[0]["artifact_digest"] == "sha256:" + hashlib.sha256(row).hexdigest()
    assert not {"attempt_id", "batch_id", "stage_instance_id"} & set(refused[0])  # run-level, the fields named and no others
    assert set(json.loads(row)) == {"at", "command", "code", "stage", "batch"}  # no message, no path, no content


def test_the_event_passes_the_schema_and_the_journal_is_still_valid(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    _refuse(run, 3)
    state = pilot_state.derive_state(run["run_root"])  # the chain, the copies, the schema of every event
    assert sum(row["event_type"] == "COMMAND_REFUSED" for row in state["events"]) == 3
    assert state["attempts"][0]["state"] != "TERMINAL"


def test_the_report_counts_the_events_and_not_an_editable_file(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _refuse(run, 3)
    assert _report(run)["refusals"]["total"] == 3
    (run["run_root"] / "refusals.jsonl").write_bytes(b"")  # the agent «cleans» its refusals
    refusals = _report(run)["refusals"]
    assert refusals["total"] == 3 and refusals["details_lost"] == 3, refusals
    assert refusals["mismatch"] and "refusals.mismatch" in json.dumps(_report(run)["warnings"], ensure_ascii=False)


def test_a_planted_line_without_an_event_is_not_counted_and_is_named(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _refuse(run)
    with (run["run_root"] / "refusals.jsonl").open("ab") as handle:
        handle.write(json.dumps({"at": "2026-01-01T00:00:00.000000Z", "command": "x", "code": "PLANTED", "stage": None, "batch": None},
                                sort_keys=True).encode("utf-8") + b"\n")
    refusals = _report(run)["refusals"]
    assert refusals["total"] == 1 and "PLANTED" not in refusals["by_code"], refusals
    assert refusals["mismatch"] == {"lines_without_event": 1, "events_without_line": 0}, refusals["mismatch"]


def test_the_basis_says_the_journal(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _refuse(run)
    report = _report(run)
    assert "журнал" in report["refusals"]["basis"] and "не подтверждено" not in report["refusals"]["basis"]
    from tools.run_report import render_markdown

    assert "по журналу" in render_markdown(report)


def test_a_journal_that_does_not_pass_its_checks_takes_no_event_and_the_line_stays(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    copy = run["run_root"] / "events" / "0000000003.json"
    copy.write_bytes(copy.read_bytes() + b" ")  # the journal no longer passes its checks
    before = _raw(run["run_root"])
    code, refused = _api("read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "x", "--event", "ARTIFACT_READ_BACK")
    assert code == 2
    assert _raw(run["run_root"]) == before and len(_rows(run["run_root"])) == 1  # the line is there, no event was written into a broken journal


def test_a_dry_run_refusal_leaves_no_line_and_no_event(tmp_path: Path) -> None:
    from tests.test_review_two_commands import _bundles, _flags

    run = _generated_run(tmp_path)
    before = _raw(run["run_root"])
    code, refused = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--dry-run")  # no session yet: a refusal
    assert code == 2
    assert _raw(run["run_root"]) == before and not (run["run_root"] / "refusals.jsonl").exists()


def test_the_events_between_the_others_change_no_reader_of_the_order(tmp_path: Path) -> None:
    """The same flow with a refusal at every stage and without: the same status, the same report outside the section of refusals."""
    from tests.test_review_two_commands import _bundles, _candidate_file, _flags, _open, _select_verdict, _verdict
    from tools.run_pipeline import main

    def flow(root: Path, refusals: bool) -> tuple[dict, dict, list]:
        run = _generated_run(root)
        if refusals:
            _refuse(run)
        assert _open(run)[0] == 0
        if refusals:
            _refuse(run)
        assert _select_verdict(run, _verdict(run))[0] == 0
        if refusals:
            _refuse(run)
        out = io.StringIO()
        with redirect_stdout(out):
            main(["status", "--project", str(run["project"]), "--run", run["run_root"].name])
        text = out.getvalue()
        status = json.loads(text[text.index("{"):])
        report = _report(run)
        shape = [row["event_type"] for row in _events(run["run_root"]) if row["event_type"] != "COMMAND_REFUSED"]
        return status, report, shape

    (tmp_path / "plain").mkdir()
    (tmp_path / "noisy").mkdir()
    plain_status, plain_report, plain_shape = flow(tmp_path / "plain", False)
    noisy_status, noisy_report, noisy_shape = flow(tmp_path / "noisy", True)
    assert noisy_shape == plain_shape
    assert noisy_status["next_step"]["code"] == plain_status["next_step"]["code"] and noisy_status.get("stop_reason") == plain_status.get("stop_reason")
    for key in ("counts", "canon", "phases"):
        assert json.dumps(noisy_report[key], sort_keys=True, default=str) == json.dumps(plain_report[key], sort_keys=True, default=str) or key == "phases", key
    assert noisy_report["refusals"]["total"] == 3
