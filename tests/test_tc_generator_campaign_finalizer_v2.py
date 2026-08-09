"""Focused tests for the bounded v2 terminal-controller recovery."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
V1_TESTS = ROOT / "tests/test_tc_generator_campaign_finalizer.py"
V2 = ROOT / "docs/to_do/skill-tests/tc-generator/finalize_campaign_v2.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _v1_tests():
    return _load_module("tc_generator_campaign_finalizer_v1_tests_for_v2", V1_TESTS)


def _v2():
    return _load_module("tc_generator_campaign_finalizer_v2", V2)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fixture(tmp_path: Path, monkeypatch):
    v1_tests = _v1_tests()
    _v1, _evidence, campaign, _scenario, draft = v1_tests._campaign(tmp_path)
    scorecard = campaign / "05-scorecards/green-final.json"
    _write(scorecard, scorecard.read_bytes() + b"\n")
    failed = campaign / "artifacts/controller/finalization-v1-refusal.json"
    amendment = campaign / "10-finalization-v2-amendment.md"
    _write(failed, b'{"status":"refused"}\n')
    _write(amendment, b"# bounded recovery\n")
    return campaign, draft, failed, amendment


def _configure(v2, monkeypatch, campaign: Path, failed: Path, amendment: Path) -> Path:
    metadata = campaign / "06-run-metadata.json"
    scorecard = campaign / "05-scorecards/green-final.json"
    monkeypatch.setattr(v2, "PINNED_METADATA_SHA256", hashlib.sha256(metadata.read_bytes()).hexdigest())
    monkeypatch.setattr(v2, "PINNED_PENDING_SCORECARD_SHA256", hashlib.sha256(scorecard.read_bytes()).hexdigest())
    monkeypatch.setattr(v2, "FAILED_ATTEMPT_RELATIVE_PATH", failed.relative_to(campaign).as_posix())
    monkeypatch.setattr(v2, "FAILED_ATTEMPT_SHA256", hashlib.sha256(failed.read_bytes()).hexdigest())
    monkeypatch.setattr(v2, "AMENDMENT_RELATIVE_PATH", amendment.relative_to(campaign).as_posix())
    monkeypatch.setattr(v2, "AMENDMENT_SHA256", hashlib.sha256(amendment.read_bytes()).hexdigest())
    return campaign / v2.SUCCESS_RECORD_RELATIVE_PATH


def _argv(campaign: Path, draft: Path) -> list[str]:
    return [str(V2), str(campaign.resolve()), str(draft.resolve())]


def test_v2_completes_pinned_trailing_newline_pending_scorecard(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    timestamps = iter(["2026-08-10T10:00:00.000Z", "2026-08-10T10:00:02.000Z"])
    committed_at: list[str] = []
    monkeypatch.setattr(v2, "_now", lambda: next(timestamps))
    monkeypatch.setattr(v2, "_after_metadata_commit", lambda: committed_at.append("2026-08-10T10:00:01.000Z"))

    assert v2.main(_argv(campaign, draft)) == 0

    payload = json.loads(success.read_text(encoding="utf-8"))
    assert payload["writes_committed"] is True
    assert payload["failed_attempt"]["sha256"] == hashlib.sha256(failed.read_bytes()).hexdigest()
    assert payload["amendment"]["sha256"] == hashlib.sha256(amendment.read_bytes()).hexdigest()
    assert json.loads((campaign / "06-run-metadata.json").read_text(encoding="utf-8"))["status"] == "complete"
    assert committed_at == ["2026-08-10T10:00:01.000Z"]
    assert payload["finished_at"] >= committed_at[0]


def test_v2_refuses_semantic_pending_scorecard_drift_without_writes(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    scorecard = campaign / "05-scorecards/green-final.json"
    payload = json.loads(scorecard.read_text(encoding="utf-8"))
    payload["all_passed"] = True
    _write(scorecard, json.dumps(payload, separators=(",", ":")).encode())
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    before = {path: path.read_bytes() for path in (campaign / "06-run-metadata.json", scorecard)}

    with pytest.raises(v2.Refusal, match="scorecard drifted"):
        v2.main(_argv(campaign, draft))

    assert {path: path.read_bytes() for path in before} == before
    assert not success.exists()


def test_v2_refuses_pinned_hash_mismatch_without_writes(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    metadata = campaign / "06-run-metadata.json"
    _write(metadata, metadata.read_bytes() + b"\n")
    before = {path: path.read_bytes() for path in (metadata, campaign / "05-scorecards/green-final.json")}

    with pytest.raises(v2.Refusal, match="pinned pre-state"):
        v2.main(_argv(campaign, draft))

    assert {path: path.read_bytes() for path in before} == before
    assert not success.exists()


def test_v2_rolls_back_when_success_record_creation_fails(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    metadata = campaign / "06-run-metadata.json"
    scorecard = campaign / "05-scorecards/green-final.json"
    protocol = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
    before = {path: path.read_bytes() for path in (metadata, scorecard)}
    create = v2._atomic_create

    def fail_success_record(path: Path, content: bytes) -> None:
        if path == success:
            raise OSError("success record failure")
        create(path, content)

    monkeypatch.setattr(v2, "_atomic_create", fail_success_record)
    with pytest.raises(OSError, match="success record failure"):
        v2.main(_argv(campaign, draft))

    assert {path: path.read_bytes() for path in before} == before
    assert not protocol.exists()
    assert not success.exists()


def test_v2_rolls_back_keyboard_interrupt_after_metadata_commit(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    metadata = campaign / "06-run-metadata.json"
    scorecard = campaign / "05-scorecards/green-final.json"
    protocol = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
    before = {path: path.read_bytes() for path in (metadata, scorecard)}
    monkeypatch.setattr(v2, "_after_metadata_commit", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        v2.main(_argv(campaign, draft))

    assert {path: path.read_bytes() for path in before} == before
    assert not protocol.exists()
    assert not success.exists()


def test_v2_recovers_missing_success_record_after_preexisting_completion(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    _scenario, _metadata_path, _metadata, pending = v2._plan(campaign, json.loads(draft.read_text(encoding="utf-8")))
    assert pending is not None
    final_path, _original_scorecard, complete_scorecard, protocol_path, protocol_bytes, complete_metadata, _protocol_exists = pending
    v2._atomic_create(protocol_path, protocol_bytes)
    v2._atomic_replace(final_path, complete_scorecard)
    v2._atomic_replace(campaign / "06-run-metadata.json", complete_metadata)
    assert not success.exists()

    assert v2.main(_argv(campaign, draft)) == 0
    payload = json.loads(success.read_text(encoding="utf-8"))
    assert payload["attestation_recovered"] is True
    assert payload["completion_preexisted"] is True
    before = {path: path.read_bytes() for path in (campaign / "06-run-metadata.json", final_path, protocol_path, success)}

    assert v2.main(_argv(campaign, draft)) == 0

    assert {path: path.read_bytes() for path in before} == before


def test_v2_requires_verified_manual_clearance_of_stale_lock_before_recovery(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    _scenario, _metadata_path, _metadata, pending = v2._plan(campaign, json.loads(draft.read_text(encoding="utf-8")))
    assert pending is not None
    final_path, _original_scorecard, complete_scorecard, protocol_path, protocol_bytes, complete_metadata, _protocol_exists = pending
    metadata_path = campaign / "06-run-metadata.json"
    v2._atomic_create(protocol_path, protocol_bytes)
    v2._atomic_replace(final_path, complete_scorecard)
    v2._atomic_replace(metadata_path, complete_metadata)
    lock = metadata_path.with_name(f".{metadata_path.name}.record.lock")
    lock.write_bytes(b"")
    before = {path: path.read_bytes() for path in (metadata_path, final_path, protocol_path, lock)}

    with pytest.raises(v2.Refusal, match="another recorder transaction is active"):
        v2.main(_argv(campaign, draft))

    assert {path: path.read_bytes() for path in before} == before
    assert not success.exists()

    lock.unlink()
    assert v2.main(_argv(campaign, draft)) == 0
    payload = json.loads(success.read_text(encoding="utf-8"))
    assert payload["attestation_recovered"] is True
    assert payload["completion_preexisted"] is True


def test_v2_already_complete_validates_success_record(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, failed, amendment = _fixture(tmp_path, monkeypatch)
    v2 = _v2()
    success = _configure(v2, monkeypatch, campaign, failed, amendment)
    assert v2.main(_argv(campaign, draft)) == 0
    payload = json.loads(success.read_text(encoding="utf-8"))
    payload["writes_committed"] = False
    _write(success, json.dumps(payload, separators=(",", ":")).encode())

    with pytest.raises(v2.Refusal, match="controller success record"):
        v2.main(_argv(campaign, draft))


def test_v1_still_refuses_trailing_newline_pending_scorecard(tmp_path: Path, monkeypatch) -> None:
    campaign, draft, _failed, _amendment = _fixture(tmp_path, monkeypatch)
    v1 = _v1_tests()._finalizer()

    with pytest.raises(v1.Refusal, match="planned bytes"):
        v1.main([str(ROOT / "docs/to_do/skill-tests/tc-generator/finalize_campaign.py"), str(campaign.resolve()), str(draft.resolve())])
