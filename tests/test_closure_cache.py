"""FaT-9: one closure remembers what it has read and validated, keyed by content; the checks stay the same."""
from __future__ import annotations

import collections
import functools
from pathlib import Path

import pytest

# About 1.5x the measured closure after FaT-9: 12 state derivations and 75 validations (before: 189 and 3593).
DERIVE_STATE_LIMIT = 18
VALIDATION_LIMIT = 113


def _passed(tmp_path: Path):
    from tests import test_durable_execution_trace as trace

    root, attempt_id, report, request, _durable = trace._execution_facts(tmp_path)
    trace._publish_execution(root, attempt_id, report, request=request)
    return root, attempt_id


def _count(monkeypatch: pytest.MonkeyPatch, counts: collections.Counter, name: str) -> None:
    from tools import pilot_state

    original = getattr(pilot_state, name)

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        counts[name] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, name, wrapper)


def test_one_closure_derives_the_state_and_validates_receipts_far_less_often(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.finalize_attempt import finalize_durable_execution_attempt

    root, attempt_id = _passed(tmp_path)
    counts: collections.Counter = collections.Counter()
    for name in ("_derive_state", "_validate_factual_receipt"):
        _count(monkeypatch, counts, name)
    closed = finalize_durable_execution_attempt(root, attempt_id)
    assert closed["result"]["verification"] == "PASS"
    assert counts["_derive_state"] <= DERIVE_STATE_LIMIT, counts
    assert counts["_validate_factual_receipt"] <= VALIDATION_LIMIT, counts


def test_a_write_inside_the_closure_is_seen_by_the_next_read(tmp_path: Path) -> None:
    from tests import test_durable_execution_trace as trace
    from tools import pilot_state

    root, attempt_id, report, request, _durable = trace._execution_facts(tmp_path)
    with pilot_state.closure_cache():
        before = pilot_state.derive_state(root)
        with pytest.raises(ValueError):
            pilot_state.read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")
        trace._publish_execution(root, attempt_id, report, request=request)
        after = pilot_state.derive_state(root)
        assert len(after["events"]) > len(before["events"])
        receipt = pilot_state.read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")
        assert receipt["record"]["payload"]["verdict"] == "PASS"


def test_a_forged_receipt_is_refused_even_after_a_cached_read(tmp_path: Path) -> None:
    from tools import pilot_state

    root, attempt_id = _passed(tmp_path)
    with pilot_state.closure_cache():
        first = pilot_state.read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
        assert any(key[0] == "receipt" and key[2] == "generated-delta" for key in pilot_state._CLOSURE_CACHE.get())
        target = root / first["path"]
        data = first["bytes"]
        at = data.index(b"sha256:") + len(b"sha256:")
        target.write_bytes(data[:at] + (b"0" if data[at:at + 1] != b"0" else b"1") + data[at + 1:])
        with pytest.raises(ValueError):
            pilot_state.read_attempt_receipt(root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")


def test_a_nested_execution_receipt_is_validated_on_every_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, attempt_id = _passed(tmp_path)
    from tools.finalize_attempt import finalize_durable_execution_attempt

    finalize_durable_execution_attempt(root, attempt_id)
    counts: collections.Counter = collections.Counter()
    _count(monkeypatch, counts, "_validate_closed_execution_request")
    pilot_state.read_attempt_receipt(root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")
    pilot_state.read_attempt_receipt(root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")
    without = counts["_validate_closed_execution_request"]
    counts.clear()
    with pilot_state.closure_cache():
        pilot_state.read_attempt_receipt(root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")
        pilot_state.read_attempt_receipt(root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")
    assert without >= 2 and counts["_validate_closed_execution_request"] == without


def test_a_pending_marker_bypasses_the_cached_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, _attempt_id = _passed(tmp_path)
    counts: collections.Counter = collections.Counter()
    monkeypatch.setattr(pilot_state, "_derive_state", lambda run_root: counts.update(["_derive_state"]) or {"events": [], "attempts": []})
    (root / "pending-events").mkdir(exist_ok=True)
    (root / "pending-events" / "0000000999.json").write_text("{}", encoding="utf-8")
    with pilot_state.closure_cache():
        pilot_state.derive_state(root)
        pilot_state.derive_state(root)
    assert counts["_derive_state"] == 2


def test_a_cached_state_still_checks_the_module_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, _attempt_id = _passed(tmp_path)
    counts: collections.Counter = collections.Counter()
    with pilot_state.closure_cache():
        pilot_state.derive_state(root)
        _count(monkeypatch, counts, "_module_selection_is_bound")
        _count(monkeypatch, counts, "_derive_state")
        pilot_state.derive_state(root)
    assert counts["_derive_state"] == 0 and counts["_module_selection_is_bound"] >= 1


def test_a_cached_state_is_a_copy(tmp_path: Path) -> None:
    from tools import pilot_state

    root, _attempt_id = _passed(tmp_path)
    with pilot_state.closure_cache():
        state = pilot_state.derive_state(root)
        state["events"].clear()
        assert pilot_state.derive_state(root)["events"]


def test_without_the_block_nothing_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    root, _attempt_id = _passed(tmp_path)
    counts: collections.Counter = collections.Counter()
    _count(monkeypatch, counts, "_events")
    pilot_state.derive_state(root)
    pilot_state.derive_state(root)
    assert counts["_events"] == 2
    counts.clear()
    with pilot_state.closure_cache():
        pilot_state.derive_state(root)
        pilot_state.derive_state(root)
    assert counts["_events"] == 1
    assert pilot_state._CLOSURE_CACHE.get() is None
