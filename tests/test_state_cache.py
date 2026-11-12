"""P2 (item 10, reduced): ``derive_state`` inside ``closure_cache()`` is the journal's state, with and without the cache; a new event or an edit
of the journal is not hidden by it.  There is no cache file: ``tmp/`` is where the agent writes, a file there proves nothing about the journal
(the orchestrator's probe forged a state-cache.json and the code believed it), so nothing is read from or written to ``<run>/tmp`` for the state."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run, _journal


def test_the_state_in_a_closure_is_the_state_without_it(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    plain = pilot_state.derive_state(run["run_root"])
    with pilot_state.closure_cache():
        first = pilot_state.derive_state(run["run_root"])
        second = pilot_state.derive_state(run["run_root"])
    assert plain == first == second


def test_a_new_event_inside_a_closure_is_seen(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    from tests.test_review_two_commands import _open

    with pilot_state.closure_cache():
        before = pilot_state.derive_state(run["run_root"])
        code, opened = _open(run)  # another process appends events to the journal
        assert code == 0, opened
        after = pilot_state.derive_state(run["run_root"])
    assert len(after["events"]) > len(before["events"]) and after == pilot_state.derive_state(run["run_root"])


@pytest.mark.parametrize("target", ["copy", "journal"])
def test_an_edit_of_the_journal_files_is_not_hidden_by_the_closure(tmp_path: Path, target: str) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    path = run["run_root"] / ("events/0000000003.json" if target == "copy" else "events.jsonl")
    with pytest.raises(ValueError), pilot_state.closure_cache():
        pilot_state.derive_state(run["run_root"])
        data = bytearray(path.read_bytes())
        data[-3] = ord("0") if data[-3] != ord("0") else ord("1")  # one byte of the last field
        path.write_bytes(bytes(data))
        pilot_state.derive_state(run["run_root"])
        pilot_state.derive_state(run["run_root"])  # whichever call meets it, the closure ends with the full check


def test_no_command_leaves_a_state_file_in_tmp(tmp_path: Path) -> None:
    import json

    run = _generated_run(tmp_path)
    digest = json.loads(Path(run["context"]).read_bytes())["digest"]
    journal = _journal(run["run_root"])
    code, read = _api("read-receipt", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--kind", "batch-plan", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, read
    assert not (run["run_root"] / "tmp" / "state-cache.json").exists() and _journal(run["run_root"]) == journal


def test_a_forged_state_file_changes_nothing(tmp_path: Path) -> None:
    """The probe of the orchestrator: a state-cache.json with a recomputed digest — the state is the journal's."""
    import json

    from tools import pilot_state

    run = _generated_run(tmp_path)
    state = pilot_state.derive_state(run["run_root"])
    forged = json.loads(json.dumps(state))
    forged["attempts"][0]["state"] = "TERMINAL"
    (run["run_root"] / "tmp").mkdir(exist_ok=True)
    (run["run_root"] / "tmp" / "state-cache.json").write_text(json.dumps({"state": forged, "events": len(forged["events"])}), encoding="utf-8")
    assert pilot_state.derive_state(run["run_root"]) == state
    with pilot_state.closure_cache():
        assert pilot_state.derive_state(run["run_root"]) == state
