"""Append one fully evidenced successful tc-generator run to a pending campaign."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_FINAL_PHASE = "04-green-final"
VERSIONED_FINAL_PHASE = re.compile(r"^(?:04-green-final|(?:0[5-9]|[1-9][0-9]+)-green-final-v[1-9][0-9]*)$")
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
METADATA_NAME = "06-run-metadata.json"


class InvocationError(Exception):
    """The CLI arguments or JSON documents cannot be read as required."""


class Refusal(Exception):
    """Supplied evidence cannot safely become a successful recorded run."""


def _compact(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvocationError(f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise InvocationError(f"{label} must be a JSON object")
    return value


def _require_keys(value: dict[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        raise InvocationError(f"{label} has an unsupported shape")


def _relative_file(campaign: Path, value: Any, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        raise Refusal(f"{label} must be a nonempty relative path")
    if "\\" in value:
        raise Refusal(f"{label} must use a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts):
        raise Refusal(f"{label} escapes or hides evidence")
    resolved = (campaign / Path(*path.parts)).resolve()
    try:
        resolved.relative_to(campaign)
    except ValueError as error:
        raise Refusal(f"{label} escapes campaign") from error
    if not resolved.is_file():
        raise Refusal(f"{label} is missing")
    return path.as_posix(), resolved


def _evidence(campaign: Path, value: Any, label: str, *, expected_path: str | None = None, expected_hash: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        raise InvocationError(f"{label} must be an object")
    _require_keys(value, {"path", "sha256"}, label)
    path, resolved = _relative_file(campaign, value["path"], label)
    if expected_path is not None and path != expected_path:
        raise Refusal(f"{label} has the wrong reserved path")
    actual_hash = _sha256(resolved)
    if value["sha256"] != actual_hash:
        raise Refusal(f"{label} hash does not match bytes")
    if expected_hash is not None and actual_hash != expected_hash:
        raise Refusal(f"{label} hash does not match scenario")
    return {"path": path, "sha256": actual_hash}


def _path_evidence(campaign: Path, value: Any, label: str, *, expected_path: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise InvocationError(f"{label} must be an object")
    _require_keys(value, {"path"}, label)
    path, resolved = _relative_file(campaign, value["path"], label)
    if path != expected_path:
        raise Refusal(f"{label} has the wrong reserved path")
    return {"path": path, "sha256": _sha256(resolved)}


def _expected_prompt_hash(scenario: dict[str, Any], phase: str) -> str:
    if phase == "pressure":
        hashes = scenario.get("prompt_sha256")
        expected = hashes.get("pressure") if isinstance(hashes, dict) else None
    else:
        hashes = scenario.get("phase_prompt_sha256")
        expected = hashes.get(phase) if isinstance(hashes, dict) else None
        if expected is None:
            prompt_hashes = scenario.get("prompt_sha256")
            expected = prompt_hashes.get("canonical") if isinstance(prompt_hashes, dict) else None
    if not isinstance(expected, str) or len(expected) != 64:
        raise InvocationError("scenario has no usable phase prompt hash")
    return expected


def _phase_keys(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    final_phase = scenario.get("effective_final_phase", DEFAULT_FINAL_PHASE)
    if not isinstance(final_phase, str) or not VERSIONED_FINAL_PHASE.fullmatch(final_phase):
        raise InvocationError("scenario has an invalid effective final phase")
    repetitions = [f"rep-{number:02d}" for number in range(1, 6)]
    return [
        *(("01-red-control", repetition) for repetition in repetitions),
        *(("02-green-initial", repetition) for repetition in repetitions),
        ("pressure", "pressure"),
        *((final_phase, repetition) for repetition in repetitions),
    ]


def _timestamps(started_at: str, finished_at: str) -> None:
    if not UTC_Z.fullmatch(started_at) or not UTC_Z.fullmatch(finished_at):
        raise Refusal("timestamps must be UTC-Z datetimes")
    try:
        started = datetime.fromisoformat(started_at[:-1] + "+00:00")
        finished = datetime.fromisoformat(finished_at[:-1] + "+00:00")
    except ValueError as error:
        raise Refusal("timestamps must be real UTC-Z datetimes") from error
    if started.tzinfo != timezone.utc or finished.tzinfo != timezone.utc or finished < started:
        raise Refusal("finished timestamp precedes start")


def _expected_output_paths(phase: str, repetition: str) -> tuple[str, list[str]]:
    root = "artifacts/outputs/03-pressure/pressure" if phase == "pressure" else f"artifacts/outputs/{phase}/{repetition}"
    report = "03-pressure.md" if phase == "pressure" else f"{phase}/{repetition}.md"
    return root, [report, f"{root}/tc-generator-output.json", f"{root}/validation-result.json", f"{root}/semantic-result.json"]


def _validate_commands(campaign: Path, phase: str, repetition: str, commands: Any) -> list[dict[str, Any]]:
    if not isinstance(commands, list) or len(commands) < 3:
        raise Refusal("commands must include evaluator, validator, and semantic evidence")
    normalized: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise InvocationError(f"command {index} must be an object")
        _require_keys(command, {"id", "argv", "cwd", "exit_code"}, f"command {index}")
        if not isinstance(command["id"], str) or not command["id"]:
            raise InvocationError(f"command {index} has no id")
        if not isinstance(command["argv"], list) or not command["argv"] or not all(isinstance(arg, str) and arg for arg in command["argv"]):
            raise InvocationError(f"command {index} has no literal argv")
        if command["exit_code"] != 0:
            raise Refusal(f"command {index} did not succeed")
        if not isinstance(command["cwd"], str) or not Path(command["cwd"]).is_absolute() or Path(command["cwd"]).resolve() != campaign:
            raise Refusal(f"command {index} has the wrong campaign cwd")
        normalized.append({"id": command["id"], "argv": list(command["argv"]), "cwd": command["cwd"], "exit_code": 0})
    if [command["id"] for command in normalized].count("validate-artifact") != 1 or [command["id"] for command in normalized].count("semantic-check") != 1:
        raise Refusal("validator and semantic checker must each occur exactly once")
    validator, semantic = normalized[-2:]
    if validator["id"] != "validate-artifact" or semantic["id"] != "semantic-check":
        raise Refusal("validator must be penultimate and semantic checker final")
    output_root, expected_outputs = _expected_output_paths(phase, repetition)
    checked_output = str((campaign / expected_outputs[1]).resolve())
    repository = campaign.parents[3]
    expected_validator = [
        validator["argv"][0],
        str((repository / "tools/validate_artifact.py").resolve()),
        str((repository / "schemas/tc-generator-output.schema.json").resolve()),
        checked_output,
    ]
    if len(validator["argv"]) != 4 or validator["argv"] != expected_validator or not Path(validator["argv"][0]).is_absolute():
        raise Refusal("validator argv is not the exact canonical command")
    mode = "pressure" if phase == "pressure" else ("red-control" if phase == "01-red-control" else "canonical")
    expected_semantic = [
        semantic["argv"][0], str((campaign / "check_output.py").resolve()), "--input",
        str((campaign / "artifacts/inputs/context-marker-output.json").resolve()), "--output", checked_output, "--mode", mode,
    ]
    if len(semantic["argv"]) != 8 or semantic["argv"] != expected_semantic or semantic["argv"][0] != validator["argv"][0] or not Path(semantic["argv"][0]).is_absolute():
        raise Refusal("semantic argv is not the exact canonical command")
    return normalized


def _plan(campaign: Path, draft: dict[str, Any]) -> tuple[Path, bytes, bytes, tuple[str, str]]:
    _require_keys(draft, {"phase", "repetition", "skill_present", "prompt_sha256", "prompt_snapshot", "started_at", "finished_at", "evaluator", "task", "observation", "output_paths", "commands"}, "draft")
    phase, repetition = draft["phase"], draft["repetition"]
    if not isinstance(draft["skill_present"], bool) or not isinstance(draft["started_at"], str) or not isinstance(draft["finished_at"], str) or not isinstance(draft["task"], str) or not draft["task"].strip():
        raise InvocationError("draft has an unsupported shape")
    scenario = _read_json(campaign / "00-scenario.json", "scenario")
    if scenario.get("artifact_type") != "scenario" or scenario.get("skill_id") != "tc-generator" or scenario.get("protocol_contract_version") != 1:
        raise InvocationError("scenario is not tc-generator protocol v1")
    phase_keys = _phase_keys(scenario)
    if not isinstance(phase, str) or not isinstance(repetition, str) or (phase, repetition) not in phase_keys:
        raise Refusal("draft has an unsupported campaign key")
    if draft["skill_present"] is (phase == "01-red-control"):
        raise Refusal("skill presence does not match campaign phase")
    _timestamps(draft["started_at"], draft["finished_at"])
    metadata_path = campaign / METADATA_NAME
    metadata = _read_json(metadata_path, "metadata")
    required_metadata = {"artifact_type", "skill_id", "status", "evaluator", "host", "model", "fork_turns", "runs"}
    if not required_metadata <= set(metadata) or set(metadata) - required_metadata - {"invalidated_attempts"}:
        raise InvocationError("metadata has an unsupported shape")
    if metadata["artifact_type"] != "run-metadata" or metadata["skill_id"] != "tc-generator" or metadata["status"] != "pending" or metadata["fork_turns"] != "none" or not isinstance(metadata["runs"], list):
        raise Refusal("metadata is not a pending tc-generator ledger")
    existing_keys: list[tuple[str, str]] = []
    for run in metadata["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("phase"), str) or not isinstance(run.get("repetition"), str):
            raise InvocationError("metadata run has an unsupported shape")
        existing_keys.append((run["phase"], run["repetition"]))
    if existing_keys != phase_keys[:len(existing_keys)] or len(existing_keys) >= len(phase_keys):
        raise Refusal("metadata runs are not a contiguous campaign prefix")
    key = (phase, repetition)
    invalidated_attempts = metadata.get("invalidated_attempts", [])
    if not isinstance(invalidated_attempts, list):
        raise InvocationError("invalidated attempts must be a list")
    invalidated_keys = set()
    for attempt in invalidated_attempts:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("phase"), str) or not isinstance(attempt.get("repetition"), str):
            raise InvocationError("invalidated attempt has an unsupported shape")
        invalidated_keys.add((attempt["phase"], attempt["repetition"]))
    if key in existing_keys or key != phase_keys[len(existing_keys)] or key in invalidated_keys:
        raise Refusal("draft key is not the next campaign key")
    evaluator = draft["evaluator"]
    if not isinstance(evaluator, dict):
        raise InvocationError("evaluator must be an object")
    _require_keys(evaluator, {"name", "host", "model"}, "evaluator")
    if not all(isinstance(evaluator[field], str) and evaluator[field].strip() for field in evaluator):
        raise InvocationError("evaluator has an unsupported shape")
    globals_ = (metadata["evaluator"], metadata["host"], metadata["model"])
    if any(value is not None for value in globals_) and globals_ != (evaluator["name"], evaluator["host"], evaluator["model"]):
        raise Refusal("evaluator identity does not match metadata")
    if not existing_keys and any(value is not None for value in globals_) and not all(isinstance(value, str) and value for value in globals_):
        raise Refusal("metadata has a partial evaluator identity")
    expected_prompt = _expected_prompt_hash(scenario, phase)
    protocol_root = f"artifacts/protocol/{phase}/{repetition}"
    prompt = _evidence(campaign, draft["prompt_snapshot"], "prompt snapshot", expected_path=f"{protocol_root}/prompt.txt", expected_hash=expected_prompt)
    if draft["prompt_sha256"] != prompt["sha256"]:
        raise Refusal("draft prompt hash does not match prompt bytes")
    observation = _path_evidence(campaign, draft["observation"], "observation", expected_path=f"{protocol_root}/observation.json")
    if not isinstance(draft["output_paths"], list) or not all(isinstance(path, str) for path in draft["output_paths"]):
        raise InvocationError("output paths must be a list of paths")
    output_root, required_outputs = _expected_output_paths(phase, repetition)
    if len(set(draft["output_paths"])) != len(draft["output_paths"]) or not set(required_outputs).issubset(draft["output_paths"]):
        raise Refusal("required successful-run outputs are missing")
    outputs = []
    for index, path in enumerate(draft["output_paths"]):
        relative, resolved = _relative_file(campaign, path, f"output {index}")
        if relative == prompt["path"] or relative == observation["path"] or not (relative == required_outputs[0] or relative.startswith(f"{output_root}/")):
            raise Refusal("output path is outside this run's reserved evidence")
        outputs.append({"path": relative, "sha256": _sha256(resolved)})
    commands = _validate_commands(campaign, phase, repetition, draft["commands"])
    protocol_path = campaign / protocol_root / "run-protocol.json"
    if protocol_path.exists():
        raise Refusal("run protocol target already exists")
    protocol = {
        "artifact_type": "run-protocol", "skill_id": "tc-generator", "phase": phase, "repetition": repetition,
        "application_prompt_sha256": prompt["sha256"], "started_at": draft["started_at"], "finished_at": draft["finished_at"],
        "evaluator": {"name": evaluator["name"], "host": evaluator["host"], "model": evaluator["model"]}, "task": draft["task"],
        "observation_path": observation["path"], "output_paths": [output["path"] for output in outputs], "commands": commands,
    }
    protocol_bytes = _compact(protocol)
    protocol_snapshot = {"path": f"{protocol_root}/run-protocol.json", "sha256": hashlib.sha256(protocol_bytes).hexdigest()}
    run = {
        "phase": phase, "repetition": repetition, "skill_present": draft["skill_present"], "prompt_sha256": prompt["sha256"], "prompt_snapshot": prompt,
        "protocol_snapshot": protocol_snapshot, "raw_input_allowlist": scenario["raw_input_allowlist"], "started_at": draft["started_at"], "finished_at": draft["finished_at"], "outputs": outputs, "commands": commands,
    }
    updated = dict(metadata)
    updated["evaluator"], updated["host"], updated["model"] = evaluator["name"], evaluator["host"], evaluator["model"]
    updated["runs"] = [*metadata["runs"], run]
    return protocol_path, protocol_bytes, _compact(updated), key


def _atomic_create(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_replace(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _lock(path: Path) -> Path:
    lock = path.with_name(f".{path.name}.record.lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise Refusal("another recorder transaction is active") from error
    os.close(descriptor)
    return lock


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise InvocationError("usage: record_successful_run.py ABSOLUTE_CAMPAIGN_ROOT ABSOLUTE_DRAFT_JSON")
    campaign, draft_path = Path(argv[1]), Path(argv[2])
    if not campaign.is_absolute() or not draft_path.is_absolute() or not campaign.is_dir():
        raise InvocationError("campaign root and draft path must be absolute")
    campaign = campaign.resolve()
    draft_path = draft_path.resolve()
    try:
        draft_path.relative_to(campaign)
    except ValueError as error:
        raise InvocationError("draft must live within the campaign") from error
    draft = _read_json(draft_path, "draft")
    metadata_path = campaign / METADATA_NAME
    lock = _lock(metadata_path)
    protocol_path: Path | None = None
    try:
        protocol_path, protocol_bytes, metadata_bytes, key = _plan(campaign, draft)
        _atomic_create(protocol_path, protocol_bytes)
        try:
            _atomic_replace(metadata_path, metadata_bytes)
        except OSError:
            protocol_path.unlink(missing_ok=True)
            raise
    finally:
        lock.unlink(missing_ok=True)
    print(json.dumps({"status": "recorded", "phase": key[0], "repetition": key[1]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Refusal as error:
        print(json.dumps({"status": "refused", "error": str(error)}, separators=(",", ":")))
        raise SystemExit(1)
    except (InvocationError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, separators=(",", ":")))
        raise SystemExit(2)
