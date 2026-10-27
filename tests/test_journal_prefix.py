"""FaT-9b: inside closure_cache() a write checks only the new journal lines; the rest stays checked once."""
from __future__ import annotations

import collections
import functools
from pathlib import Path

import pytest

ENTRY_POINTS = [
    ("tools.pilot_state", "append_event"),
    ("tools.pilot_state", "publish_model_request"),
    ("tools.pilot_state", "publish_model_stage_artifact"),
    ("tools.pilot_state", "publish_context_selection"),
    ("tools.pilot_state", "read_effective_canonical"),
    ("tools.pilot_state", "open_automation_review_boundary"),
    ("tools.batch_assembly", "assemble_candidate"),
    ("tools.orchestrate_test_case_revision", "publish_unreviewed_candidate"),
    ("tools.orchestrate_test_case_revision", "open_reviewer_session"),
    ("tools.orchestrate_test_case_revision", "orchestrate_revision"),
    ("tools.generated_delta", "materialize_delta"),
    ("tools.finalize_attempt", "finalize_attempt"),
]


def _facts(tmp_path: Path):
    from tests import test_durable_execution_trace as trace

    return trace._execution_facts(tmp_path)


def _count_event_copy_reads(monkeypatch: pytest.MonkeyPatch, counts: collections.Counter) -> None:
    from tools import pilot_state

    original = pilot_state.read_confined_bytes

    @functools.wraps(original)
    def wrapper(project, root, target, *args, **kwargs):
        if Path(target).parent.name == "events":
            counts["copies"] += 1
        return original(project, root, target, *args, **kwargs)

    monkeypatch.setattr(pilot_state, "read_confined_bytes", wrapper)


def test_a_write_inside_the_block_reads_only_the_new_event_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import test_durable_execution_trace as trace
    from tools import pilot_state

    root, attempt_id, report, request, _durable = _facts(tmp_path)
    counts: collections.Counter = collections.Counter()
    _count_event_copy_reads(monkeypatch, counts)
    with pilot_state.closure_cache():
        before = len(pilot_state.derive_state(root)["events"])
        counts.clear()
        trace._publish_execution(root, attempt_id, report, request=request)
        added = len(pilot_state.derive_state(root)["events"]) - before
        inside = counts["copies"]
    assert added > 0
    # each new event: the counterpart read-back of the write and one check of the new line; never the old copies
    assert inside <= 3 * added, (inside, added, before)


def test_a_forged_early_copy_inside_the_block_fails_on_exit(tmp_path: Path) -> None:
    from tools import pilot_state

    root, *_rest = _facts(tmp_path)
    copy = root / "events" / "0000000002.json"
    original = copy.read_bytes()
    with pytest.raises(ValueError, match="invalid event journal"):
        with pilot_state.closure_cache():
            pilot_state.derive_state(root)
            copy.write_bytes(original.replace(b'"seq":2', b'"seq":2 '))
            pilot_state.derive_state(root)


def test_a_truncated_journal_takes_the_full_path_with_the_same_error(tmp_path: Path) -> None:
    from tools import pilot_state

    root, *_rest = _facts(tmp_path)
    journal = root / "events.jsonl"
    data = journal.read_bytes()
    with pilot_state.closure_cache():
        pilot_state.derive_state(root)
        journal.write_bytes(data[: data.rstrip(b"\n").rfind(b"\n") + 1])
        with pytest.raises(ValueError) as cached:
            pilot_state.derive_state(root)
        journal.write_bytes(data)
    journal.write_bytes(data[: data.rstrip(b"\n").rfind(b"\n") + 1])
    with pytest.raises(ValueError) as plain:
        pilot_state.derive_state(root)
    assert str(cached.value) == str(plain.value)


def test_a_pending_marker_takes_the_full_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, *_rest = _facts(tmp_path)
    counts: collections.Counter = collections.Counter()
    original = pilot_state._events_full

    def full(*args, **kwargs):
        counts["full"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "_events_full", full)
    with pilot_state.closure_cache():
        pilot_state._events(root.parents[1], root)
        pilot_state._events(root.parents[1], root)
        (root / "pending-events").mkdir(exist_ok=True)
        (root / "pending-events" / "0000009999.json").write_text("{}", encoding="utf-8")
        counts.clear()
        with pytest.raises(ValueError):
            pilot_state._events(root.parents[1], root)
        inside = counts["full"]
        (root / "pending-events" / "0000009999.json").unlink()
    assert inside == 1


def test_outside_the_block_every_read_checks_the_whole_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, *_rest = _facts(tmp_path)
    counts: collections.Counter = collections.Counter()
    _count_event_copy_reads(monkeypatch, counts)
    total = len(pilot_state.derive_state(root)["events"])
    counts.clear()
    pilot_state.derive_state(root)
    assert counts["copies"] == total


@pytest.mark.parametrize(("module", "name"), ENTRY_POINTS)
def test_public_entry_points_open_a_closure_cache(module: str, name: str) -> None:
    import importlib

    function = getattr(importlib.import_module(module), name)
    assert getattr(function, "__closure_cached__", False) is True, f"{module}.{name}"


@pytest.mark.parametrize("module", ["tools.run_pipeline", "tools.orchestrate_test_case_revision", "tools.context_marker_parts", "tools.zephyr_template_export"])
def test_a_cli_main_is_not_one_block(module: str) -> None:
    """The final full check must run before a command prints its answer, so main() opens no block of its own."""
    import importlib

    assert not getattr(importlib.import_module(module).main, "__closure_cached__", False)


def test_a_forged_copy_during_finish_gives_one_error_json_and_code_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from tests import test_durable_execution_trace as trace
    from tools import pilot_state
    from tools.run_pipeline import main

    root, attempt_id, report, request, _durable = trace._execution_facts(tmp_path)
    trace._publish_execution(root, attempt_id, report, request=request)
    copy = root / "events" / "0000000002.json"
    original_derive = pilot_state._derive_state
    forged = {"done": False}

    def derive_then_forge(run_root):
        state = original_derive(run_root)
        if not forged["done"] and pilot_state._CLOSURE_CACHE.get() is not None:
            forged["done"] = True
            copy.write_bytes(copy.read_bytes().replace(b'"seq":2', b'"seq":2 '))
        return state

    monkeypatch.setattr(pilot_state, "_derive_state", derive_then_forge)
    capsys.readouterr()
    code = main(["finish", "--project", str(tmp_path / "project"), "--run", root.name])
    out = capsys.readouterr()
    assert forged["done"] and code == 2
    assert "Traceback" not in out.out + out.err
    lines = [line for line in out.out.splitlines() if line.strip()]
    answer = json.loads(out.out)
    assert answer["status"] == "error" and out.out.count('"status"') == 1, lines
