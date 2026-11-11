"""F2 (A2-1, arch review 3): the review commands cut between two writes — the file of the ledger (or of the effective canonical) is on disk, its pair of events is not.

Every test asserts the CORRECT behaviour of house rule 3 — the same command again finishes what the cut left — so a
same command again gives the result of the uncut one.  The cut is a monkeypatch: the k-th write passes, the (k+1)-th raises OSError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_open_recovery import _crash_once, _main, _open_argv
from tests.test_review_requested import _generated_run, _journal
from tests.journal_helpers import assert_refused_once, files_minus_journal, refusal_log
from tests.test_review_two_commands import _autofix, _bundles, _flags, _shape, _verdict

LEDGER = "reviewer-ledger-v1"


def _crash_nth(monkeypatch, owner, name: str, when=lambda *a, **k: True, nth: int = 1) -> None:
    """The nth call of ``owner.name`` that satisfies ``when`` raises OSError once; every other call runs the real function."""
    real = getattr(owner, name)
    state = {"seen": 0, "done": False}

    def crash(*args, **kwargs):
        if not state["done"] and when(*args, **kwargs):
            state["seen"] += 1
            if state["seen"] == nth:
                state["done"] = True
                raise OSError(f"simulated crash in {name} (match #{nth})")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, crash)


def _crash_event(monkeypatch, event_type: str, batch_id, nth: int = 1) -> None:
    """The nth ``append_event`` of this type and batch fails (the file before it is already written)."""
    from tools import pilot_state

    _crash_nth(monkeypatch, pilot_state, "append_event", lambda run_root, et, *a, **k: et == event_type and k.get("batch_id") == batch_id, nth)


def _events(run_root: Path) -> list[dict]:
    return [json.loads(line) for line in _journal(run_root).decode("utf-8").splitlines()]


def _select_argv(run: dict, verdict: str, *extra: str) -> list[str]:
    return ["review-select", *_flags(run), "--output-dir", _bundles(run), "--verdict", verdict, *extra]


def _evidence_argv(run: dict, *paths: str) -> list[str]:
    return ["review-evidence", *_flags(run), *[flag for path in paths for flag in ("--path", path)]]


def _opened(tmp_path: Path, capsys) -> dict:
    run = _generated_run(tmp_path)
    code, opened = _main(capsys, _open_argv(run))
    assert code == 0, opened
    return run


# --------------------------------------------------------------------------------------
# review-open: the ledger file is written, its event pair is not (one or both)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_review_open_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _main(capsys, _open_argv(run))
        assert code == 2, first
    ledgers = run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]
    assert len(list(ledgers.glob("*.json"))) == 1  # the cut left the file
    code, again = _main(capsys, _open_argv(run))
    assert code == 0 and again["result"]["model_request"]["stage"] == "tc-reviewer", f"{point}: {again}"
    other = _generated_run(tmp_path / "plain")
    assert _main(capsys, _open_argv(other))[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"]), point  # the shape leaves COMMAND_REFUSED out; the cut call's refusal is stated here
    assert refusal_log(run["run_root"]) == [("review-open", first["code"])] and refusal_log(other["run_root"]) == [], refusal_log(run["run_root"])
    code, selected = _main(capsys, _select_argv(run, _verdict(run)))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected


# --------------------------------------------------------------------------------------
# review-select --verdict: answer file + events, the COMPLETED ledger file + events, the effective canonical + events
# --------------------------------------------------------------------------------------

def test_review_select_cut_after_the_answer_before_the_completed_ledger(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import orchestrate_test_case_revision as otcr

    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, otcr, "_publish_reviewer_ledger")
        code, first = _main(capsys, _select_argv(run, verdict))
        assert code == 2, first
    assert any(row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == "tc-reviewer:canonical" for row in _events(run["run_root"]))
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again


@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_review_select_cut_between_the_completed_ledger_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _main(capsys, _select_argv(run, verdict))
        assert code == 2, first
    ledgers = run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]
    assert len(list(ledgers.glob("*.json"))) == 2  # WAITING and COMPLETED are both on disk
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", f"{point}: {again}"
    # the same selection without --verdict (the recorded answer) works too
    code, third = _main(capsys, ["review-select", *_flags(run), "--output-dir", _bundles(run)])
    assert code == 0 and third["result"]["status"] == "EFFECTIVE_SELECTED", third


def test_review_select_cut_before_the_effective_canonical(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import pilot_state

    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        code, first = _main(capsys, _select_argv(run, verdict))
        assert code == 2, first
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective


@pytest.mark.parametrize("point", ["effective-file-written", "effective-published"])
def test_review_select_cut_between_the_effective_canonical_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        # the ledger events carry batch_id reviewer-ledger-v1; the effective canonical's pair carries none
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "effective-file-written" else "ARTIFACT_READ_BACK", None)
        code, first = _main(capsys, _select_argv(run, verdict))
        assert code == 2, first
    assert (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").exists(), "the probe expects the effective canonical file on disk"
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", f"{point}: {again}"
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective


# --------------------------------------------------------------------------------------
# review-evidence: the context receipt (+ CONTEXT_SELECTED), then a new ledger revision (+ its pair)
# --------------------------------------------------------------------------------------

def test_review_evidence_cut_after_the_receipt_before_the_ledger(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import orchestrate_test_case_revision as otcr

    run = _opened(tmp_path, capsys)
    with monkeypatch.context() as broken:
        _crash_once(broken, otcr, "_publish_reviewer_ledger")
        code, first = _main(capsys, _evidence_argv(run, "src/extra.py"))
        assert code == 2, first
    assert sum(row["event_type"] == "CONTEXT_SELECTED" for row in _events(run["run_root"])) == 2  # the receipt is there
    code, again = _main(capsys, _evidence_argv(run, "src/extra.py"))
    assert code == 0, again
    code, ledger = _main(capsys, ["read-ledger", *_flags(run)])
    assert code == 0 and [row["event_type"] for row in ledger["result"]["events"]] == [
        "REVIEW_SESSION_STARTED", "EVIDENCE_REQUESTED", "EVIDENCE_PROVIDED"], ledger


@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_review_evidence_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _opened(tmp_path, capsys)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _main(capsys, _evidence_argv(run, "src/extra.py"))
        assert code == 2, first
    code, again = _main(capsys, _evidence_argv(run, "src/extra.py"))
    assert code == 0, f"{point}: {again}"
    code, ledger = _main(capsys, ["read-ledger", *_flags(run)])
    assert code == 0 and [row["event_type"] for row in ledger["result"]["events"]] == [
        "REVIEW_SESSION_STARTED", "EVIDENCE_REQUESTED", "EVIDENCE_PROVIDED"], ledger


# --------------------------------------------------------------------------------------
# review-abort computes only; review-select --reviewer-session publishes the ABORTED ledger (file + pair)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_select_of_an_aborted_session_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _opened(tmp_path, capsys)
    session = str(run["tmp"] / "session.json")
    code, aborted = _main(capsys, ["review-abort", *_flags(run), "--reason", "REVIEW_UNUSABLE", "--out", session])
    assert code == 0, aborted
    argv = ["review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", session]
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _main(capsys, argv)
        assert code == 2, first
    code, again = _main(capsys, argv)
    assert code == 0 and again["result"]["facts"]["reason_code"] == "REVIEW_UNUSABLE", f"{point}: {again}"


# --------------------------------------------------------------------------------------
# a ledger file that is not the one published: refused with its code, nothing written
# --------------------------------------------------------------------------------------

def test_a_ledger_file_without_events_and_with_other_bytes_is_refused_with_its_code(tmp_path: Path, monkeypatch, capsys) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED", LEDGER)
        assert _main(capsys, _open_argv(run))[0] == 2
    ledgers = run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]
    [stray] = list(ledgers.glob("*.json"))
    stray_value = json.loads(stray.read_bytes())
    from tools.orchestrate_test_case_revision import _session_digest

    stray_value["context_budget_bytes"] += 1  # another ledger, itself valid (its own digest): not the one the call would publish
    stray_value["digest"] = _session_digest(stray_value)
    stray.unlink()
    (ledgers / (stray_value["digest"].removeprefix("sha256:") + ".json")).write_bytes(
        json.dumps(stray_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    before = _journal(run["run_root"])
    code, refused = _main(capsys, _open_argv(run))
    assert code == 2 and refused["code"] == "REVIEW_LEDGER_UNBOUND" and "scan --new-run" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added


def test_after_a_cut_between_published_and_read_back_no_command_falls_back_to_the_older_revision(tmp_path: Path, monkeypatch, capsys) -> None:
    """The COMPLETED revision is published, its READ_BACK is not: the next other command (read-ledger, complete-review) sees COMPLETED,
    never the WAITING one before it; the second select finishes it.  The strict reader (evidence consumers) refuses, not rolls back."""
    from tools import pilot_state

    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_READ_BACK", LEDGER)
        assert _main(capsys, _select_argv(run, verdict))[0] == 2
    code, ledger = _main(capsys, ["read-ledger", *_flags(run)])
    assert code == 0 and ledger["result"]["status"] == "COMPLETED", ledger
    code, completed = _main(capsys, ["complete-review", *_flags(run)])
    assert code == 0 and completed["result"]["status"] == "COMPLETED", completed
    with pytest.raises(ValueError, match="unbound"):
        pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])  # the strict reading: no older revision in its place
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    assert pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["status"] == "COMPLETED"  # both events now


def test_a_ledger_file_with_no_events_at_all_is_not_in_the_journal_yet(tmp_path: Path, monkeypatch, capsys) -> None:
    """File written, not even ARTIFACT_PUBLISHED: the journal knows the revision before it — read-ledger says WAITING, and the select finishes."""
    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED", LEDGER)
        assert _main(capsys, _select_argv(run, verdict))[0] == 2
    code, ledger = _main(capsys, ["read-ledger", *_flags(run)])
    assert code == 0 and ledger["result"]["status"] == "WAITING", ledger
    code, again = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again


# --------------------------------------------------------------------------------------
# the protection of the write: a stale reading leads to a repeat or a refusal, never to a second decision
# --------------------------------------------------------------------------------------

def _cut_after_published(monkeypatch, capsys, tmp_path: Path) -> tuple[dict, str]:
    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_READ_BACK", LEDGER)
        assert _main(capsys, _select_argv(run, verdict))[0] == 2
    return run, verdict


def test_another_verdict_after_the_cut_is_refused_and_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    run, _verdict_given = _cut_after_published(monkeypatch, capsys, tmp_path)
    other = _verdict(run, "ТРЕБУЕТ ДОРАБОТКИ")
    before = _journal(run["run_root"])
    def files():  # refusals.jsonl and the COMMAND_REFUSED event are what a refusal adds by design (P1, J1: the code of the refusal, no content)
        return files_minus_journal(run["run_root"])

    tree = files()
    code, refused = _main(capsys, _select_argv(run, other))
    assert code == 2 and refused["code"] == "MODEL_STAGE_ANSWERED", refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, "MODEL_STAGE_ANSWERED", "review-select")
    assert files() == tree
    events = _events(run["run_root"])
    assert sum(row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == "tc-reviewer:canonical" for row in events) == 1
    assert not (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").exists()  # no effective canonical was published


def test_status_after_the_cut_leads_to_the_select_and_the_repeat_closes_the_review(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools.run_pipeline import main

    run, verdict = _cut_after_published(monkeypatch, capsys, tmp_path)
    capsys.readouterr()
    code = main(["status", "--project", str(run["project"]), "--run", run["run_root"].name])
    out = capsys.readouterr().out
    assert code in {0, 1, 3}, out
    status = json.loads(out[out.index("{"):])
    assert status["state"] == "review" and status["next"]["verb"] == "review-select", status
    code, closed = _main(capsys, _select_argv(run, verdict))
    assert code == 0 and closed["result"]["status"] == "EFFECTIVE_SELECTED", closed
    other = _generated_run(tmp_path / "uncut")
    assert _main(capsys, _open_argv(other))[0] == 0
    assert _main(capsys, _select_argv(other, _verdict(other)))[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"])  # the form of the journal is the uncut one


# --------------------------------------------------------------------------------------
# status where the effective canonical is «not yet» (the writes of review-select go ledger COMPLETED first, effective after) — and where it is lost
# --------------------------------------------------------------------------------------

def _status(capsys, run: dict) -> dict:
    from tools.run_pipeline import main

    capsys.readouterr()
    main(["status", "--project", str(run["project"]), "--run", run["run_root"].name])
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):])


def test_status_between_the_recorded_answer_and_the_select_says_review_select(tmp_path: Path, capsys) -> None:
    run = _opened(tmp_path, capsys)
    code, answered = _main(capsys, ["model-response", *_flags(run), "--stage", "tc-reviewer:canonical", "--artifact", _verdict(run)])
    assert code == 0, answered
    status = _status(capsys, run)
    assert not any("effective-canonicals" in str(row.get("path")) for row in status.get("evidence_errors") or []), status.get("evidence_errors")
    assert status.get("stop_reason") != "EVIDENCE_UNVERIFIED" and status["state"] == "review" and status["next"]["verb"] == "review-select", status
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["effective"] is None and "review-select" in effective["result"]["reason"], effective


def test_a_closed_ledger_cut_before_the_effective_canonical_says_not_yet(tmp_path: Path, monkeypatch, capsys) -> None:
    """Ledger COMPLETED and read back, the effective canonical not published (the cut is between the two writes of select)."""
    from tools import pilot_state

    run = _opened(tmp_path, capsys)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        assert _main(capsys, _select_argv(run, verdict))[0] == 2
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["effective"] is None and "review-select" in effective["result"]["reason"], effective
    status = _status(capsys, run)
    assert status.get("stop_reason") != "EVIDENCE_UNVERIFIED" and status["state"] == "review" and status["next"]["verb"] == "review-select", status


def test_an_auto_fix_cut_between_the_r2_bundle_and_the_effective_canonical_says_not_yet_and_the_repeat_closes_it(tmp_path: Path, monkeypatch, capsys) -> None:
    """J1 (backlog of F2): the write order of AUTO_FIX_APPLIED is the COMPLETED ledger, the r2 bundle, then the effective canonical.  A cut after
    the bundle is «ещё нет» for status and read-effective (not a refusal, not a new run), and the same review-select writes the rest: the
    publisher of the r2 bundle is run again on the very same document and the effective canonical is published once."""
    from tools import pilot_state

    run = _opened(tmp_path, capsys)
    answer = _autofix(run)
    bundles = list(Path(_bundles(run)).rglob("*"))
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        code, first = _main(capsys, _select_argv(run, answer))
        assert code == 2, first
    written = sorted(str(path.relative_to(_bundles(run))) for path in Path(_bundles(run)).rglob("*") if path.is_file())
    assert len(list(Path(_bundles(run)).rglob("*"))) > len(bundles)  # the r2 bundle is on the disk: the cut is after it
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["effective"] is None and "review-select" in effective["result"]["reason"], effective
    status = _status(capsys, run)
    assert status.get("stop_reason") != "EVIDENCE_UNVERIFIED" and status["state"] == "review" and status["next"]["verb"] == "review-select", status
    code, again = _main(capsys, _select_argv(run, answer))
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    assert sorted(str(path.relative_to(_bundles(run))) for path in Path(_bundles(run)).rglob("*") if path.is_file()) == written  # no second bundle
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective
    assert sum(row["event_type"] == "ARTIFACT_READ_BACK" and row.get("batch_id") is None and row.get("artifact_digest") == effective["result"]["digest"]
               for row in _events(run["run_root"])) == 1  # the effective canonical is bound once


def test_a_fully_closed_review_whose_effective_file_is_gone_is_still_a_refusal(tmp_path: Path, capsys) -> None:
    """The guard of PT2/F1: ACCEPTED, the ledger paired, the effective canonical published — and the file deleted."""
    from tools.run_report import build_report

    run = _opened(tmp_path, capsys)
    assert _main(capsys, _select_argv(run, _verdict(run)))[0] == 0
    (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").unlink()
    status = _status(capsys, run)
    assert status.get("stop_reason") == "EVIDENCE_UNVERIFIED", status
    assert any("effective-canonicals" in str(row.get("path")) for row in status.get("evidence_errors") or []), status.get("evidence_errors")
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 2 and effective["status"] == "error", effective
    report = build_report(run["project"], run["run_root"].name)[0]
    assert any("EFFECTIVE_CANON_UNREADABLE" in str(item) for item in report["warnings"]), report["warnings"]
