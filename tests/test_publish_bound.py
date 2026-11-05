"""J1: one contract for «a file and its pair of events» — ``publish_bound`` (a fixed receipt) and ``bind_bytes`` (any content-addressed file).

No file → written, then ARTIFACT_PUBLISHED and ARTIFACT_READ_BACK; the same bytes already there → only the missing events; other
bytes there → RECEIPT_FILE_UNBOUND and nothing written.  The bytes are the caller's: the file on the disk is only compared with them.
The families the review of the architecture found (fixed receipts, the ledger, canaries, the «naive» four) all go through it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_cut_review import _crash_event
from tests.test_review_requested import _generated_run, _journal


def _target(run: dict, name: str = "x.bin") -> Path:
    return run["run_root"] / "artifacts" / run["attempt_id"] / name


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _events(run: dict, digest: str) -> list[tuple]:
    rows = [json.loads(line) for line in _journal(run["run_root"]).decode("utf-8").splitlines()]
    return [(row["event_type"], row.get("batch_id")) for row in rows if row.get("artifact_digest") == digest]


def _bind(run: dict, data: bytes, **kwargs):
    from tools import pilot_state

    return pilot_state.bind_bytes(run["run_root"], run["attempt_id"], _target(run), data, digest=_digest(data), label="test artifact", **kwargs)


def test_the_contract_is_in_the_docstrings() -> None:
    from tools import pilot_state

    bound, raw = " ".join(pilot_state.publish_bound.__doc__.split()), " ".join(pilot_state.bind_bytes.__doc__.split())
    assert "other bytes" in bound and "never read as their source" in bound and "RECEIPT_FILE_UNBOUND" in bound
    assert "never the source" in raw and "ReceiptFileUnbound" in raw


def test_no_file_is_written_and_bound_in_order(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    data = b"first bytes\n"
    bound = _bind(run, data, batch_id="test-family-v1")
    assert bound["created"] is True and _target(run).read_bytes() == data
    assert _events(run, _digest(data)) == [("ARTIFACT_PUBLISHED", "test-family-v1"), ("ARTIFACT_READ_BACK", "test-family-v1")]


def test_the_same_bytes_again_change_nothing(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    data = b"same bytes\n"
    _bind(run, data)
    before = _journal(run["run_root"])
    again = _bind(run, data)
    assert again["created"] is False and _journal(run["run_root"]) == before


@pytest.mark.parametrize("cut", ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"])
def test_a_cut_between_the_file_and_its_events_is_finished_by_the_same_call(tmp_path: Path, monkeypatch, cut: str) -> None:
    run = _generated_run(tmp_path)
    data = f"cut at {cut}\n".encode()
    with monkeypatch.context() as broken:
        _crash_event(broken, cut, "test-family-v1")
        with pytest.raises(OSError):
            _bind(run, data, batch_id="test-family-v1")
    assert _target(run).read_bytes() == data
    again = _bind(run, data, batch_id="test-family-v1")
    assert again["created"] is False
    assert _events(run, _digest(data)) == [("ARTIFACT_PUBLISHED", "test-family-v1"), ("ARTIFACT_READ_BACK", "test-family-v1")]


def test_other_bytes_on_the_disk_are_refused_with_the_code_and_nothing_is_written(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    _target(run).parent.mkdir(parents=True, exist_ok=True)
    _target(run).write_bytes(b"what an agent left here\n")
    before = _journal(run["run_root"])
    with pytest.raises(pilot_state.ReceiptFileUnbound) as refused:
        _bind(run, b"what the code computed\n")
    assert refused.value.code == "RECEIPT_FILE_UNBOUND" and "--new-run" in str(refused.value)
    assert _journal(run["run_root"]) == before and _target(run).read_bytes() == b"what an agent left here\n"  # the file is not the source


def test_bytes_and_a_digest_are_required(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    for data, digest in (("text", _digest(b"x")), (b"x", "not-a-digest")):
        with pytest.raises(ValueError, match="required"):
            pilot_state.bind_bytes(run["run_root"], run["attempt_id"], _target(run), data, digest=digest, label="test artifact")
    assert not _target(run).exists()


def test_the_other_events_of_the_digest_in_another_family_are_not_taken_for_this_one(tmp_path: Path) -> None:
    """The events of a family carry its batch_id: the pair of the same bytes under another batch_id is a pair of its own."""
    run = _generated_run(tmp_path)
    data = b"two families\n"
    _bind(run, data, batch_id="family-a-v1")
    _bind(run, data, batch_id="family-b-v1")
    assert _events(run, _digest(data)) == [("ARTIFACT_PUBLISHED", "family-a-v1"), ("ARTIFACT_READ_BACK", "family-a-v1"),
                                           ("ARTIFACT_PUBLISHED", "family-b-v1"), ("ARTIFACT_READ_BACK", "family-b-v1")]


# --------------------------------------------------------------------------------------
# the families go through publish_bound (the wiring; their behaviour is in their own tests)
# --------------------------------------------------------------------------------------

class _Reached(Exception):
    pass


def test_the_naive_four_the_effective_canonical_and_the_canaries_publish_through_publish_bound(tmp_path: Path, monkeypatch) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    seen: list[tuple] = []

    def spy(run_root, attempt_id, kind, facts, **kwargs):
        seen.append((kind, kwargs.get("batch_id")))
        raise _Reached(kind)

    monkeypatch.setattr(pilot_state, "publish_bound", spy)
    root, attempt = run["run_root"], run["attempt_id"]
    calls = [
        lambda: pilot_state.publish_generated_delta(root, attempt, {"files": []}),
        lambda: pilot_state.publish_phase5_receipt(root, attempt, "disposition-plan", {"files": []}),
        lambda: pilot_state.open_automation_review_boundary(root, attempt, {"automation_revision": 1}),
        lambda: pilot_state.publish_review_canaries(root, attempt, {"x": 1}),
        lambda: pilot_state.publish_effective_canonical(root, attempt, {"document_id": "D", "revision": 1}, {"document_id": "D"}, "sha256:" + "1" * 64),
    ]
    for call in calls:
        with pytest.raises(_Reached):
            call()
    kinds = [kind for kind, _batch in seen]
    assert kinds == ["generated-delta", "disposition-plan", "automation-review-boundary-r1", "review-canaries", "effective-canonical"], seen
    assert seen[3][1] is not None  # the canaries keep their batch_id


def test_the_old_private_name_is_the_same_function() -> None:
    from tools import pilot_state

    assert pilot_state._publish_bound_attempt_receipt is pilot_state.publish_bound
