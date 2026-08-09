"""Recover one recorded no-write v1 finalizer refusal without broadening the campaign."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PINNED_METADATA_SHA256 = "1bf993170df33333104fd85e5d4dbd3d0cfa813b5a448d38a100dd5d2539e1eb"
PINNED_PENDING_SCORECARD_SHA256 = "8557b4baa91dbb3c136f2cf345e9955b9299e6197fa0ed269d321afdf7f94ee1"
FAILED_ATTEMPT_RELATIVE_PATH = "artifacts/controller/finalization-v1-refusal.json"
FAILED_ATTEMPT_SHA256 = "0b97b108fa390a3cde91e27da759a48c2392fa5330d1b8fdef17a7812f3ef057"
AMENDMENT_RELATIVE_PATH = "10-finalization-v2-amendment.md"
AMENDMENT_SHA256 = "6ce080de16c73e12763d09d1e08df13841ffec3d9bae9fa083931651ead87f09"
SUCCESS_RECORD_RELATIVE_PATH = "artifacts/controller/finalization-v2-success.json"


def _v1():
    path = Path(__file__).with_name("finalize_campaign.py")
    spec = importlib.util.spec_from_file_location("tc_generator_campaign_finalizer_v1_for_v2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load v1 campaign finalizer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v1 = _v1()
recorder = v1.recorder
InvocationError = v1.InvocationError
Refusal = v1.Refusal
_atomic_create = v1._atomic_create
_atomic_replace = v1._atomic_replace


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _after_metadata_commit() -> None:
    """Test seam marking the point after the durable completion transition."""


def _inside(campaign: Path, relative: str, label: str) -> Path:
    path = (campaign / relative).resolve()
    try:
        path.relative_to(campaign)
    except ValueError as error:
        raise InvocationError(f"{label} path escapes the campaign") from error
    return path


def _pinned_evidence(campaign: Path, relative: str, digest: str, label: str) -> dict[str, str]:
    path = _inside(campaign, relative, label)
    if not path.is_file() or _sha256(path) != digest:
        raise Refusal(f"{label} does not match the pinned recovery evidence")
    return {"path": relative, "sha256": digest}


def _assert_pinned_pre_state(metadata: Path, scorecard: Path, complete_scorecard: bytes) -> None:
    if _sha256(metadata) != PINNED_METADATA_SHA256:
        raise Refusal("pinned pre-state metadata hash does not match")
    scorecard_hash = _sha256(scorecard)
    if scorecard_hash not in {PINNED_PENDING_SCORECARD_SHA256, hashlib.sha256(complete_scorecard).hexdigest()}:
        raise Refusal("pinned pre-state final scorecard hash does not match")


def _success_record(
    campaign: Path,
    argv: list[str],
    failed_attempt: dict[str, str],
    amendment: dict[str, str],
    *,
    started_at: str,
    finished_at: str,
    pre_metadata_sha256: str,
    pre_scorecard_sha256: str,
    post_metadata_sha256: str,
    post_scorecard_sha256: str,
    post_protocol_sha256: str,
    attestation_recovered: bool,
    completion_preexisted: bool,
) -> bytes:
    payload = {
        "artifact_type": "controller-finalization-v2",
        "version": 2,
        "status": "completed",
        "campaign": {"phase": "04-green-final", "repetition": "rep-05"},
        "failed_attempt": failed_attempt,
        "amendment": amendment,
        "controller": {
            "argv": [sys.executable, *argv],
            "cwd": str(campaign),
            "script": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
        },
        "started_at": started_at,
        "finished_at": finished_at,
        "pre": {
            "metadata_sha256": pre_metadata_sha256,
            "scorecard_sha256": pre_scorecard_sha256,
            "protocol_sha256": None,
        },
        "post": {
            "metadata_sha256": post_metadata_sha256,
            "scorecard_sha256": post_scorecard_sha256,
            "protocol_sha256": post_protocol_sha256,
        },
        "writes_committed": True,
        "attestation_recovered": attestation_recovered,
        "completion_preexisted": completion_preexisted,
    }
    return v1._compact(payload)


def _assert_success_record(
    path: Path,
    campaign: Path,
    argv: list[str],
    failed_attempt: dict[str, str],
    amendment: dict[str, str],
    *,
    pre_metadata_sha256: str,
    pre_scorecard_sha256: str,
    post_metadata_sha256: str,
    post_scorecard_sha256: str,
    post_protocol_sha256: str,
    allowed_attestations: set[tuple[bool, bool]],
) -> None:
    payload = v1._read_json(path, "controller success record")
    required = {
        "artifact_type", "version", "status", "campaign", "failed_attempt", "amendment", "controller",
        "started_at", "finished_at", "pre", "post", "writes_committed", "attestation_recovered", "completion_preexisted",
    }
    if set(payload) != required:
        raise Refusal("controller success record has an unsupported shape")
    if (
        payload["artifact_type"] != "controller-finalization-v2"
        or payload["version"] != 2
        or payload["status"] != "completed"
        or payload["campaign"] != {"phase": "04-green-final", "repetition": "rep-05"}
        or payload["failed_attempt"] != failed_attempt
        or payload["amendment"] != amendment
        or payload["writes_committed"] is not True
        or (payload["attestation_recovered"], payload["completion_preexisted"]) not in allowed_attestations
    ):
        raise Refusal("controller success record does not attest this recovery")
    controller = payload["controller"]
    if not isinstance(controller, dict) or controller != {
        "argv": [sys.executable, *argv],
        "cwd": str(campaign),
        "script": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__).resolve())},
    }:
        raise Refusal("controller success record does not attest this invocation")
    if not isinstance(payload["started_at"], str) or not isinstance(payload["finished_at"], str):
        raise Refusal("controller success record has invalid timestamps")
    recorder._timestamps(payload["started_at"], payload["finished_at"])
    if payload["pre"] != {
        "metadata_sha256": pre_metadata_sha256,
        "scorecard_sha256": pre_scorecard_sha256,
        "protocol_sha256": None,
    } or payload["post"] != {
        "metadata_sha256": post_metadata_sha256,
        "scorecard_sha256": post_scorecard_sha256,
        "protocol_sha256": post_protocol_sha256,
    }:
        raise Refusal("controller success record hash chain does not match")


def _plan(campaign: Path, draft: dict[str, Any]):
    scenario = v1._read_json(campaign / "00-scenario.json", "scenario")
    metadata_path = campaign / recorder.METADATA_NAME
    metadata = v1._read_json(metadata_path, "metadata")
    if metadata.get("status") == "complete":
        return scenario, metadata_path, metadata, None
    final_path, original_scorecard, complete_scorecard = v1._assert_pending_scorecards(campaign, scenario)
    _assert_pinned_pre_state(metadata_path, final_path, complete_scorecard)
    protocol_path, protocol_bytes, complete_metadata, _final_path, _original, _complete, protocol_exists = v1._plan(campaign, draft)
    if protocol_exists and protocol_path.read_bytes() != protocol_bytes:
        raise Refusal("terminal run protocol target conflicts with the planned bytes")
    return scenario, metadata_path, metadata, (
        final_path, original_scorecard, complete_scorecard, protocol_path, protocol_bytes, complete_metadata, protocol_exists
    )


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise InvocationError("usage: finalize_campaign_v2.py ABSOLUTE_CAMPAIGN_ROOT ABSOLUTE_DRAFT_JSON")
    campaign, draft_path = Path(argv[1]), Path(argv[2])
    if not campaign.is_absolute() or not draft_path.is_absolute() or not campaign.is_dir():
        raise InvocationError("campaign root and draft path must be absolute")
    campaign, draft_path = campaign.resolve(), draft_path.resolve()
    try:
        draft_path.relative_to(campaign)
    except ValueError as error:
        raise InvocationError("draft must live within the campaign") from error
    draft = v1._read_json(draft_path, "draft")
    metadata_path = campaign / recorder.METADATA_NAME
    lock = recorder._lock(metadata_path)
    try:
        failed_attempt = _pinned_evidence(campaign, FAILED_ATTEMPT_RELATIVE_PATH, FAILED_ATTEMPT_SHA256, "failed attempt")
        amendment = _pinned_evidence(campaign, AMENDMENT_RELATIVE_PATH, AMENDMENT_SHA256, "amendment")
        scenario, metadata_path, metadata, pending = _plan(campaign, draft)
        success_path = _inside(campaign, SUCCESS_RECORD_RELATIVE_PATH, "controller success record")
        if pending is None:
            v1._assert_already_complete(campaign, scenario, metadata, draft)
            protocol_path = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
            hashes = {
                "pre_metadata_sha256": PINNED_METADATA_SHA256,
                "pre_scorecard_sha256": PINNED_PENDING_SCORECARD_SHA256,
                "post_metadata_sha256": _sha256(metadata_path),
                "post_scorecard_sha256": _sha256(campaign / "05-scorecards/green-final.json"),
                "post_protocol_sha256": _sha256(protocol_path),
            }
            if success_path.exists():
                _assert_success_record(
                    success_path, campaign, argv, failed_attempt, amendment,
                    allowed_attestations={(False, False), (True, True)}, **hashes,
                )
            else:
                started_at = _now()
                success_bytes = _success_record(
                    campaign, argv, failed_attempt, amendment,
                    started_at=started_at,
                    finished_at=_now(),
                    attestation_recovered=True,
                    completion_preexisted=True,
                    **hashes,
                )
                _atomic_create(success_path, success_bytes)
            print(json.dumps({"status": "already-complete"}, separators=(",", ":")))
            return 0
        final_path, original_scorecard, complete_scorecard, protocol_path, protocol_bytes, complete_metadata, protocol_exists = pending
        original_metadata = metadata_path.read_bytes()
        complete_metadata_sha256 = hashlib.sha256(complete_metadata).hexdigest()
        complete_scorecard_sha256 = hashlib.sha256(complete_scorecard).hexdigest()
        protocol_sha256 = hashlib.sha256(protocol_bytes).hexdigest()
        if success_path.exists():
            raise Refusal("controller success record precedes metadata completion")
        started_at = _now()
        created_protocol = False
        try:
            if not protocol_exists:
                _atomic_create(protocol_path, protocol_bytes)
                created_protocol = True
            if original_scorecard != complete_scorecard:
                _atomic_replace(final_path, complete_scorecard)
            _atomic_replace(metadata_path, complete_metadata)
            _after_metadata_commit()
            success_bytes = _success_record(
                campaign, argv, failed_attempt, amendment,
                started_at=started_at,
                finished_at=_now(),
                pre_metadata_sha256=PINNED_METADATA_SHA256,
                pre_scorecard_sha256=PINNED_PENDING_SCORECARD_SHA256,
                post_metadata_sha256=complete_metadata_sha256,
                post_scorecard_sha256=complete_scorecard_sha256,
                post_protocol_sha256=protocol_sha256,
                attestation_recovered=False,
                completion_preexisted=False,
            )
            _atomic_create(success_path, success_bytes)
        except BaseException:
            if final_path.read_bytes() != original_scorecard:
                _atomic_replace(final_path, original_scorecard)
            if metadata_path.read_bytes() != original_metadata:
                _atomic_replace(metadata_path, original_metadata)
            if success_path.exists():
                success_path.unlink(missing_ok=True)
            if created_protocol:
                protocol_path.unlink(missing_ok=True)
            raise
    finally:
        lock.unlink(missing_ok=True)
    print(json.dumps({"status": "completed"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Refusal as error:
        print(json.dumps({"status": "refused", "error": str(error)}, separators=(",", ":")))
        raise SystemExit(1)
    except (InvocationError, OSError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, separators=(",", ":")))
        raise SystemExit(2)
