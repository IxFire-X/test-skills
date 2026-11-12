"""F2 (A2-1, arch review 3): ``prepare`` and ``submit`` of the review cut between two writes — the file of the ledger (or of the effective canonical) is on disk, its pair of events is not.

Every test asserts the CORRECT behaviour of house rule 3 — the same command again finishes what the cut left — so a
same command again gives the result of the uncut one.  The cut is a monkeypatch: the k-th write passes, the (k+1)-th raises OSError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_open_recovery import _crash_once, _main
from tests.test_review_requested import _generated_run, _journal, _put
from tests.journal_helpers import assert_refused_once, refusal_log
from tests.test_review_two_commands import _autofix, _cycle, _flags, _open, _select_verdict, _shape, _verdict

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


def _evidence_verdict(run: dict, *paths: str) -> str:
    """The answer of the reviewer with the ``evidence`` key of the slot."""
    answer = json.loads(Path(_verdict(run)).read_bytes())
    return _put(run["tmp"] / "review-evidence.json", {**answer, "evidence": list(paths)})


def _ledger_events(run: dict) -> list[str]:
    from tools import pilot_state

    return [row["event_type"] for row in pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["events"]]


def _opened(tmp_path: Path) -> dict:
    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0, opened
    return run


# --------------------------------------------------------------------------------------
# prepare: the ledger file is written, its event pair is not (one or both)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_prepare_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, point: str) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _open(run)
        assert code == 2, first
    ledgers = run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]
    assert len(list(ledgers.glob("*.json"))) == 1  # the cut left the file
    code, again = _open(run)
    assert code == 0 and again["result"]["answer_slot"], f"{point}: {again}"
    other = _generated_run(tmp_path / "plain")
    assert _open(other)[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"]), point  # the shape leaves COMMAND_REFUSED out; the cut call's refusal is stated here
    assert refusal_log(run["run_root"]) == [("prepare", first["code"])] and refusal_log(other["run_root"]) == [], refusal_log(run["run_root"])
    code, selected = _select_verdict(run, _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", selected


# --------------------------------------------------------------------------------------
# submit: answer + events, the COMPLETED ledger file + events, the effective canonical + events
# --------------------------------------------------------------------------------------

def test_submit_cut_after_the_answer_before_the_completed_ledger(tmp_path: Path, monkeypatch) -> None:
    from tools import orchestrate_test_case_revision as otcr

    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, otcr, "_publish_reviewer_ledger")
        code, first = _select_verdict(run, verdict)
        assert code == 2, first
    assert any(row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == "tc-reviewer:canonical" for row in _events(run["run_root"]))
    code, again = _select_verdict(run, verdict)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again


@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_submit_cut_between_the_completed_ledger_file_and_its_events(tmp_path: Path, monkeypatch, point: str) -> None:
    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _select_verdict(run, verdict)
        assert code == 2, first
    ledgers = run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]
    assert len(list(ledgers.glob("*.json"))) == 2  # WAITING and COMPLETED are both on disk
    code, again = _select_verdict(run, verdict)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", f"{point}: {again}"
    code, third = _cycle("submit", run)  # the same selection from the recorded answer: the slot is not read
    assert code == 0 and third["result"]["status"] == "EFFECTIVE_SELECTED", third


def test_submit_cut_before_the_effective_canonical(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import pilot_state

    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        code, first = _select_verdict(run, verdict)
        assert code == 2, first
    code, again = _select_verdict(run, verdict)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective


@pytest.mark.parametrize("point", ["effective-file-written", "effective-published"])
def test_submit_cut_between_the_effective_canonical_file_and_its_events(tmp_path: Path, monkeypatch, capsys, point: str) -> None:
    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        # the ledger events carry batch_id reviewer-ledger-v1; the effective canonical's pair carries none
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "effective-file-written" else "ARTIFACT_READ_BACK", None)
        code, first = _select_verdict(run, verdict)
        assert code == 2, first
    assert (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").exists(), "the probe expects the effective canonical file on disk"
    code, again = _select_verdict(run, verdict)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", f"{point}: {again}"
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective


# --------------------------------------------------------------------------------------
# the evidence of the slot: the context receipt (+ CONTEXT_SELECTED), then a new ledger revision (+ its pair), then the answer
# --------------------------------------------------------------------------------------

EVIDENCE_LEDGER = ["REVIEW_SESSION_STARTED", "EVIDENCE_REQUESTED", "EVIDENCE_PROVIDED", "AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED"]


def test_submit_with_evidence_cut_after_the_receipt_before_the_ledger(tmp_path: Path, monkeypatch) -> None:
    from tools import orchestrate_test_case_revision as otcr

    run = _opened(tmp_path)
    answer = _evidence_verdict(run, "src/extra.py")
    with monkeypatch.context() as broken:
        _crash_once(broken, otcr, "_publish_reviewer_ledger")
        code, first = _select_verdict(run, answer)
        assert code == 2, first
    assert sum(row["event_type"] == "CONTEXT_SELECTED" for row in _events(run["run_root"])) == 2  # the receipt is there
    code, again = _select_verdict(run, answer)
    assert code == 0, again
    assert _ledger_events(run) == EVIDENCE_LEDGER


@pytest.mark.parametrize("point", ["ledger-file-written", "ledger-published"])
def test_submit_with_evidence_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, point: str) -> None:
    run = _opened(tmp_path)
    answer = _evidence_verdict(run, "src/extra.py")
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _select_verdict(run, answer)
        assert code == 2, first
    code, again = _select_verdict(run, answer)
    assert code == 0, f"{point}: {again}"
    assert _ledger_events(run) == EVIDENCE_LEDGER


# --------------------------------------------------------------------------------------
# submit --abort writes the ABORTED ledger (file + pair)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["ledger-file-written", pytest.param("ledger-published", marks=pytest.mark.xfail(
    strict=True, reason="V4a: the repeat of submit --abort after a cut between published and read-back is REVIEWER_SESSION_CLOSED; the old review-select finished it (code in V4b-C or later)"))])
def test_abort_cut_between_the_ledger_file_and_its_events(tmp_path: Path, monkeypatch, point: str) -> None:
    run = _opened(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED" if point == "ledger-file-written" else "ARTIFACT_READ_BACK", LEDGER)
        code, first = _cycle("submit", run, "--abort", "REVIEW_UNUSABLE")
        assert code == 2, first
    code, again = _cycle("submit", run, "--abort", "REVIEW_UNUSABLE")
    assert code == 0 and again["result"]["outcome"] == "ABORTED" and again["result"]["status"] == "REVIEW_UNUSABLE", f"{point}: {again}"


# --------------------------------------------------------------------------------------
# a ledger file that is not the one published: refused with its code, nothing written
# --------------------------------------------------------------------------------------

def test_a_ledger_file_without_events_and_with_other_bytes_is_refused_with_its_code(tmp_path: Path, monkeypatch) -> None:
    run = _generated_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_PUBLISHED", LEDGER)
        assert _open(run)[0] == 2
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
    code, refused = _open(run)
    assert code == 2 and refused["code"] == "REVIEW_LEDGER_UNBOUND" and "scan --new-run" in refused["fix"], refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added


def test_after_a_cut_between_published_and_read_back_the_repeat_finishes_and_the_strict_reader_refuses(tmp_path: Path, monkeypatch) -> None:
    """The COMPLETED revision is published, its READ_BACK is not: the strict reader (evidence consumers) refuses, not rolls back to the WAITING
    one; the second submit finishes it."""
    from tools import pilot_state

    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_READ_BACK", LEDGER)
        assert _select_verdict(run, verdict)[0] == 2
    with pytest.raises(ValueError, match="unbound"):
        pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])  # the strict reading: no older revision in its place
    code, again = _select_verdict(run, verdict)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    assert pilot_state.read_reviewer_session_ledger(run["run_root"], run["attempt_id"])["status"] == "COMPLETED"  # both events now


# --------------------------------------------------------------------------------------
# the protection of the write: a stale reading leads to a repeat or a refusal, never to a second decision
# --------------------------------------------------------------------------------------

def _cut_after_published(monkeypatch, tmp_path: Path) -> tuple[dict, str]:
    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_event(broken, "ARTIFACT_READ_BACK", LEDGER)
        assert _select_verdict(run, verdict)[0] == 2
    return run, verdict


def test_another_verdict_after_the_cut_does_not_become_a_second_decision(tmp_path: Path, monkeypatch) -> None:
    """The recorded answer stands: the repeat finishes it and does not read the slot (before V4b the other verdict was MODEL_STAGE_ANSWERED)."""
    run, _verdict_given = _cut_after_published(monkeypatch, tmp_path)
    other = _verdict(run, "ТРЕБУЕТ ДОРАБОТКИ")
    code, again = _select_verdict(run, other)
    assert code == 0 and again["result"]["answered"] is True and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    events = _events(run["run_root"])
    assert sum(row["event_type"] == "MODEL_RESPONSE_RECEIVED" and row.get("stage_instance_id") == "tc-reviewer:canonical" for row in events) == 1
    assert (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").exists()  # the first verdict's canonical, not the rejection


def test_status_after_the_cut_leads_to_the_submit_and_the_repeat_closes_the_review(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools.run_pipeline import main

    run, verdict = _cut_after_published(monkeypatch, tmp_path)
    capsys.readouterr()
    code = main(["status", "--project", str(run["project"]), "--run", run["run_root"].name])
    out = capsys.readouterr().out
    assert code in {0, 1, 3}, out
    status = json.loads(out[out.index("{"):])
    assert status["state"] == "review" and status["next"]["verb"] == "submit", status
    code, closed = _select_verdict(run, verdict)
    assert code == 0 and closed["result"]["status"] == "EFFECTIVE_SELECTED", closed
    other = _generated_run(tmp_path / "uncut")
    assert _open(other)[0] == 0
    assert _select_verdict(other, _verdict(other))[0] == 0
    assert _shape(run["run_root"]) == _shape(other["run_root"])  # the form of the journal is the uncut one


# --------------------------------------------------------------------------------------
# status where the effective canonical is «not yet» (the writes of submit go ledger COMPLETED first, effective after) — and where it is lost
# --------------------------------------------------------------------------------------

def _status(capsys, run: dict) -> dict:
    from tools.run_pipeline import main

    capsys.readouterr()
    main(["status", "--project", str(run["project"]), "--run", run["run_root"].name])
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):])


def test_a_closed_ledger_cut_before_the_effective_canonical_says_not_yet(tmp_path: Path, monkeypatch, capsys) -> None:
    """Ledger COMPLETED and read back, the effective canonical not published (the cut is between the two writes of the selection)."""
    from tools import pilot_state

    run = _opened(tmp_path)
    verdict = _verdict(run)
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        assert _select_verdict(run, verdict)[0] == 2
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["effective"] is None, effective
    status = _status(capsys, run)
    assert status.get("stop_reason") != "EVIDENCE_UNVERIFIED" and status["state"] == "review" and status["next"]["verb"] == "submit", status


def test_an_auto_fix_cut_between_the_r2_bundle_and_the_effective_canonical_says_not_yet_and_the_repeat_closes_it(tmp_path: Path, monkeypatch, capsys) -> None:
    """J1 (backlog of F2): the write order of AUTO_FIX_APPLIED is the COMPLETED ledger, the r2 bundle, then the effective canonical.  A cut after
    the bundle is «ещё нет» for status and read-effective (not a refusal, not a new run), and the same submit writes the rest: the
    publisher of the r2 bundle is run again on the very same document and the effective canonical is published once."""
    from tools import pilot_state

    run = _opened(tmp_path)
    answer = _autofix(run)
    published = run["run_root"] / "published"
    before = list(published.rglob("*"))
    with monkeypatch.context() as broken:
        _crash_once(broken, pilot_state, "publish_effective_canonical")
        code, first = _select_verdict(run, answer)
        assert code == 2, first
    written = sorted(str(path.relative_to(published)) for path in published.rglob("*") if path.is_file())
    assert len(list(published.rglob("*"))) > len(before)  # the r2 bundle is on the disk: the cut is after it
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["effective"] is None, effective
    status = _status(capsys, run)
    assert status.get("stop_reason") != "EVIDENCE_UNVERIFIED" and status["state"] == "review" and status["next"]["verb"] == "submit", status
    code, again = _select_verdict(run, answer)
    assert code == 0 and again["result"]["status"] == "EFFECTIVE_SELECTED", again
    assert sorted(str(path.relative_to(published)) for path in published.rglob("*") if path.is_file()) == written  # no second bundle
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 0 and effective["result"]["document_digest"].startswith("sha256:"), effective
    assert sum(row["event_type"] == "ARTIFACT_READ_BACK" and row.get("batch_id") is None and row.get("artifact_digest") == effective["result"]["digest"]
               for row in _events(run["run_root"])) == 1  # the effective canonical is bound once


def test_a_fully_closed_review_whose_effective_file_is_gone_is_still_a_refusal(tmp_path: Path, capsys) -> None:
    """The guard of PT2/F1: ACCEPTED, the ledger paired, the effective canonical published — and the file deleted."""
    from tools.run_report import build_report

    run = _opened(tmp_path)
    assert _select_verdict(run, _verdict(run))[0] == 0
    (run["run_root"] / "effective-canonicals" / f"{run['attempt_id']}.json").unlink()
    status = _status(capsys, run)
    assert status.get("stop_reason") == "EVIDENCE_UNVERIFIED", status
    assert any("effective-canonicals" in str(row.get("path")) for row in status.get("evidence_errors") or []), status.get("evidence_errors")
    code, effective = _main(capsys, ["read-effective", *_flags(run)])
    assert code == 2 and effective["status"] == "error", effective
    report = build_report(run["project"], run["run_root"].name)[0]
    assert any("EFFECTIVE_CANON_UNREADABLE" in str(item) for item in report["warnings"]), report["warnings"]
