"""J1: the journal of a run as the tests that compare it «before / after a refusal» see it.

Every refusal of a command that names its run appends one run-level ``COMMAND_REFUSED`` event (``tools.json_cli.record_refusal``), so a
refusal that «writes nothing» no longer leaves the bytes of ``events.jsonl`` as they were.  A test of that kind compares the journal
WITHOUT those events (``journal_minus_refusals``) and then states the one event the refusal itself added (``assert_refused_once``) —
the check stays as strong as it was, and tests the new event besides.

A refusal that adds NO event (a broken journal, a run that does not exist yet, ``--dry-run``) is stated with ``assert_no_refusal_event``:
a missing event is a thing the test says, never a thing it skips."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class Journal(bytes):
    """The journal minus its COMMAND_REFUSED lines; ``.refusals`` is the list of ``(command, code)`` the events held when it was read
    (detail from the line of ``refusals.jsonl`` the event's digest names; ``("?", "?")`` for an event whose line is gone)."""

    refusals: list[tuple[str, str]]


def _lines(run_root: Path) -> list[bytes]:
    return [line for line in (Path(run_root) / "events.jsonl").read_bytes().split(b"\n") if line]


def refusal_log(run_root: Path) -> list[tuple[str, str]]:
    """``(command, code)`` of every COMMAND_REFUSED event of the run, in the order of the journal."""
    by_digest: dict[str, dict] = {}
    path = Path(run_root) / "refusals.jsonl"
    if path.is_file():
        for line in path.read_bytes().split(b"\n"):
            if line.strip():
                try:
                    by_digest["sha256:" + hashlib.sha256(line).hexdigest()] = json.loads(line)
                except ValueError:
                    continue
    events = [json.loads(line) for line in _lines(run_root)]
    return [(str(by_digest.get(event["artifact_digest"], {}).get("command", "?")), str(by_digest.get(event["artifact_digest"], {}).get("code", "?")))
            for event in events if event["event_type"] == "COMMAND_REFUSED"]


def journal_minus_refusals(run_root: Path) -> Journal:
    kept = [line for line in _lines(run_root) if json.loads(line)["event_type"] != "COMMAND_REFUSED"]
    value = Journal(b"\n".join(kept) + (b"\n" if kept else b""))
    value.refusals = refusal_log(run_root)
    return value


def _seen(before: "Journal | list") -> int:
    return len(before.refusals if isinstance(before, Journal) else before)


def state_minus_refusals(run_root: Path) -> dict:
    """``derive_state`` without the COMMAND_REFUSED events (for the tests that compare the whole state before and after a refusal)."""
    from tools import pilot_state

    state = dict(pilot_state.derive_state(Path(run_root)))
    state["events"] = [event for event in state["events"] if event["event_type"] != "COMMAND_REFUSED"]
    return state


def files_minus_journal(run_root: Path) -> dict:
    """Every file of the run but the journal (``events.jsonl``, ``events/``, ``pending-events/``) and the line file of the refusals."""
    root = Path(run_root)
    skipped = {"events", "pending-events"}
    return {path: path.read_bytes() for path in root.rglob("*")
            if path.is_file() and path.name not in {"refusals.jsonl", "events.jsonl"} and not skipped & set(path.relative_to(root).parts[:1])}


def assert_refused_once(run_root: Path, before: "Journal | list", code: str, command: str | None = None) -> None:
    """Exactly one COMMAND_REFUSED event was added since ``before`` (a Journal, or the ``refusal_log`` read earlier) and it is the refusal
    ``code`` (of ``command`` when given)."""
    added = refusal_log(run_root)[_seen(before):]
    assert len(added) == 1, f"expected one COMMAND_REFUSED event since the snapshot, got {added}"
    got_command, got_code = added[0]
    assert got_code == code, f"the refusal event says {got_code}, the test expects {code}"
    assert command is None or got_command == command, f"the refusal event says command {got_command}, the test expects {command}"


def assert_refused_in_order(run_root: Path, before: "Journal | list", *expected: tuple[str, str]) -> None:
    """The refusals added since ``before`` are exactly ``expected`` — ``(command, code)`` pairs, in the order of the journal (a test whose
    snapshot spans several refusals)."""
    assert refusal_log(run_root)[_seen(before):] == list(expected)


def assert_no_refusal_event(run_root: Path, before: "Journal | list") -> None:
    """The refusal added no event: the journal could not take it (broken), the run did not exist, or the call was a dry run."""
    assert refusal_log(run_root)[_seen(before):] == []
