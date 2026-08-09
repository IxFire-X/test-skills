"""Atomically complete the one terminal tc-generator campaign repetition."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _recorder():
    path = Path(__file__).with_name("record_successful_run.py")
    spec = importlib.util.spec_from_file_location("tc_generator_run_recorder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load successful-run recorder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recorder = _recorder()
InvocationError = recorder.InvocationError
Refusal = recorder.Refusal
_atomic_create = recorder._atomic_create
_atomic_replace = recorder._atomic_replace


def _compact(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return recorder._read_json(path, label)


def _scorecard(scenario: dict[str, Any], phase: str, *, complete: bool) -> bytes:
    final_phase = scenario.get("effective_final_phase", recorder.DEFAULT_FINAL_PHASE)
    initial_phase = scenario.get("effective_green_initial_phase", recorder.DEFAULT_INITIAL_GREEN_PHASE)
    red_phase = scenario.get("effective_red_phase", recorder.DEFAULT_RED_PHASE)
    output_phase = {"red": red_phase, "green-initial": initial_phase, "green-final": final_phase}[phase]
    repetitions = [f"rep-{number:02d}" for number in range(1, 6)]
    passed = phase != "red"
    payload = {
        "artifact_type": "scorecard",
        "skill_id": "tc-generator",
        "phase": phase,
        "status": "complete" if complete else "pending",
        "rubric_ids": scenario["rubric_ids"],
        "results": (
            {repetition: {rubric: passed for rubric in scenario["rubric_ids"]} for repetition in repetitions}
            if complete else {}
        ),
        "evidence_files": [f"{output_phase}/{repetition}.md" for repetition in repetitions] if complete else [],
        "all_passed": passed if complete else False,
        "no_gap": False,
        "no_edit_reason": None,
    }
    return _compact(payload)


def _attest_saved_results(campaign: Path, phase: str, repetition: str) -> None:
    root, _required = recorder._expected_output_paths(phase, repetition)
    output = campaign / root / "tc-generator-output.json"
    validation = campaign / root / "validation-result.json"
    semantic = campaign / root / "semantic-result.json"
    if not output.is_file() or not validation.is_file() or not semantic.is_file():
        raise Refusal("terminal validation or semantic evidence is missing")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if _read_json(validation, validation.name) != {"status": "valid", "errors": []}:
        raise Refusal("terminal validation or semantic result does not attest output bytes")
    semantic_payload = _read_json(semantic, semantic.name)
    if (
        semantic_payload.get("status") != "pass"
        or semantic_payload.get("errors") != []
        or semantic_payload.get("artifact_sha256") != digest
    ):
        raise Refusal("terminal validation or semantic result does not attest output bytes")


def _assert_pending_scorecards(campaign: Path, scenario: dict[str, Any]) -> tuple[Path, bytes, bytes]:
    directory = campaign / "05-scorecards"
    red_path = directory / "red.json"
    initial_path = directory / "green-initial.json"
    final_path = directory / "green-final.json"
    try:
        red_bytes = red_path.read_bytes()
        initial_bytes = initial_path.read_bytes()
        final_bytes = final_path.read_bytes()
    except OSError as error:
        raise InvocationError("cannot read campaign scorecards") from error
    red_complete = recorder._effective_red_repetitions(scenario) == 5
    if _read_json(red_path, "red scorecard") != json.loads(_scorecard(scenario, "red", complete=red_complete)):
        raise Refusal("red scorecard drifted from the bounded campaign state")
    if _read_json(initial_path, "initial green scorecard") != json.loads(_scorecard(scenario, "green-initial", complete=True)):
        raise Refusal("initial green scorecard drifted from the bounded campaign state")
    complete_final = _scorecard(scenario, "green-final", complete=True)
    if (
        _read_json(final_path, "final green scorecard")
        != json.loads(_scorecard(scenario, "green-final", complete=False))
        and final_bytes != complete_final
    ):
        raise Refusal("final green scorecard drifted from the bounded campaign state")
    return final_path, final_bytes, complete_final


def _assert_complete_runs(campaign: Path, scenario: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    required_metadata = {"artifact_type", "skill_id", "status", "evaluator", "host", "model", "fork_turns", "runs"}
    optional_metadata = {"historical_runs", "invalidated_attempts", "effective_red_repetitions"}
    if not required_metadata <= set(metadata) or set(metadata) - required_metadata - optional_metadata:
        raise InvocationError("completed metadata has an unsupported shape")
    if metadata["artifact_type"] != "run-metadata" or metadata["skill_id"] != "tc-generator" or metadata["skill_id"] != scenario.get("skill_id"):
        raise Refusal("completed metadata is not a tc-generator ledger")
    effective_red_repetitions = recorder._effective_red_repetitions(scenario)
    metadata_red_repetitions = metadata.get("effective_red_repetitions")
    if metadata_red_repetitions is not None and (
        type(metadata_red_repetitions) is not int
        or metadata_red_repetitions not in recorder.SUPPORTED_EFFECTIVE_RED_REPETITIONS
    ):
        raise InvocationError("completed metadata has an invalid effective red repetition count")
    if "effective_red_repetitions" in scenario and metadata_red_repetitions != effective_red_repetitions:
        raise Refusal("completed metadata effective red repetition count does not match scenario")
    if metadata_red_repetitions is not None and metadata_red_repetitions != effective_red_repetitions:
        raise Refusal("completed metadata effective red repetition count does not match scenario")
    keys = recorder._phase_keys(scenario)
    if (
        metadata["status"] != "complete"
        or metadata["fork_turns"] != "none"
        or not isinstance(metadata["runs"], list)
        or len(metadata["runs"]) != len(keys)
        or not all(isinstance(metadata.get(field), str) and metadata[field].strip() for field in ("evaluator", "host", "model"))
    ):
        raise Refusal("metadata is neither a finalizable pending prefix nor a completed campaign")
    invalidated = metadata.get("invalidated_attempts", [])
    if not isinstance(invalidated, list):
        raise InvocationError("invalidated attempts must be a list")
    historical_identity, reserved_historical_keys, historical_paths = recorder._historical_identity(
        campaign, scenario, metadata, invalidated
    )
    identity = (metadata["evaluator"], metadata["host"], metadata["model"])
    if historical_identity is not None and historical_identity != identity:
        raise Refusal("completed metadata evaluator identity does not match historical evidence")
    active_paths: set[Path] = set()
    observed_keys: list[tuple[str, str]] = []
    for run in metadata["runs"]:
        key, run_identity, run_paths = recorder._validate_immutable_run(
            campaign, scenario, run, label="completed active run", require_observation=True
        )
        if key in reserved_historical_keys:
            raise Refusal("completed active run key is reserved by historical or invalidated evidence")
        if run_identity != identity:
            raise Refusal("completed metadata evaluator identity does not match active run")
        if any(
            recorder._reuses_evidence(active_paths, path)
            or recorder._reuses_evidence(historical_paths, path)
            for path in run_paths
        ):
            raise Refusal("completed active run reuses evidence")
        active_paths.update(run_paths)
        observed_keys.append(key)
    if observed_keys != keys:
        raise Refusal("completed metadata runs are not the campaign order")
    return metadata["runs"][-1]


def _assert_draft_matches_terminal(draft: dict[str, Any], terminal: dict[str, Any], protocol: dict[str, Any]) -> None:
    recorder._require_keys(
        draft,
        {"phase", "repetition", "skill_present", "prompt_sha256", "prompt_snapshot", "started_at", "finished_at", "evaluator", "task", "observation", "output_paths", "commands"},
        "draft",
    )
    expected = {
        "phase": terminal["phase"],
        "repetition": terminal["repetition"],
        "skill_present": terminal["skill_present"],
        "prompt_sha256": terminal["prompt_sha256"],
        "prompt_snapshot": terminal["prompt_snapshot"],
        "started_at": terminal["started_at"],
        "finished_at": terminal["finished_at"],
        "evaluator": protocol["evaluator"],
        "task": protocol["task"],
        "observation": {"path": terminal["observation"]["path"]},
        "output_paths": [output["path"] for output in terminal["outputs"]],
        "commands": terminal["commands"],
    }
    if draft != expected:
        raise Refusal("draft does not exactly match the completed terminal run")


def _assert_already_complete(campaign: Path, scenario: dict[str, Any], metadata: dict[str, Any], draft: dict[str, Any]) -> None:
    terminal = _assert_complete_runs(campaign, scenario, metadata)
    _assert_pending_scorecards(campaign, scenario)
    if (campaign / "05-scorecards/green-final.json").read_bytes() != _scorecard(scenario, "green-final", complete=True):
        raise Refusal("completed final scorecard drifted")
    protocol = _read_json(campaign / terminal["protocol_snapshot"]["path"], "terminal run protocol")
    _assert_draft_matches_terminal(draft, terminal, protocol)
    _attest_saved_results(campaign, terminal["phase"], terminal["repetition"])


def _plan(campaign: Path, draft: dict[str, Any]) -> tuple[Path, bytes, bytes, Path, bytes, bytes, bool]:
    scenario = _read_json(campaign / "00-scenario.json", "scenario")
    metadata_path = campaign / recorder.METADATA_NAME
    metadata = _read_json(metadata_path, "metadata")
    if metadata.get("status") == "complete":
        _assert_already_complete(campaign, scenario, metadata, draft)
        raise Refusal("campaign is already complete")
    final_path, original_scorecard, complete_scorecard = _assert_pending_scorecards(campaign, scenario)
    protocol_path, protocol_bytes, pending_metadata, key = recorder._plan(campaign, draft, allow_terminal=True)
    keys = recorder._phase_keys(scenario)
    if key != keys[-1] or len(metadata.get("runs", [])) != len(keys) - 1:
        raise Refusal("draft is not the sole terminal campaign key")
    _attest_saved_results(campaign, key[0], key[1])
    updated = json.loads(pending_metadata)
    updated["status"] = "complete"
    return protocol_path, protocol_bytes, _compact(updated), final_path, original_scorecard, complete_scorecard, protocol_path.exists()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise InvocationError("usage: finalize_campaign.py ABSOLUTE_CAMPAIGN_ROOT ABSOLUTE_DRAFT_JSON")
    campaign, draft_path = Path(argv[1]), Path(argv[2])
    if not campaign.is_absolute() or not draft_path.is_absolute() or not campaign.is_dir():
        raise InvocationError("campaign root and draft path must be absolute")
    campaign, draft_path = campaign.resolve(), draft_path.resolve()
    try:
        draft_path.relative_to(campaign)
    except ValueError as error:
        raise InvocationError("draft must live within the campaign") from error
    draft = _read_json(draft_path, "draft")
    metadata_path = campaign / recorder.METADATA_NAME
    lock = recorder._lock(metadata_path)
    try:
        scenario = _read_json(campaign / "00-scenario.json", "scenario")
        metadata = _read_json(metadata_path, "metadata")
        if metadata.get("status") == "complete":
            _assert_already_complete(campaign, scenario, metadata, draft)
            print(json.dumps({"status": "already-complete"}, separators=(",", ":")))
            return 0
        original_metadata = metadata_path.read_bytes()
        protocol_path, protocol_bytes, complete_metadata, scorecard_path, original_scorecard, complete_scorecard, protocol_exists = _plan(campaign, draft)
        if protocol_exists and protocol_path.read_bytes() != protocol_bytes:
            raise Refusal("terminal run protocol target conflicts with the planned bytes")
        if original_scorecard not in {_scorecard(scenario, "green-final", complete=False), complete_scorecard}:
            raise Refusal("final green scorecard drifted from the planned bytes")
        created_protocol = False
        try:
            if not protocol_exists:
                _atomic_create(protocol_path, protocol_bytes)
                created_protocol = True
            if original_scorecard != complete_scorecard:
                _atomic_replace(scorecard_path, complete_scorecard)
            _atomic_replace(metadata_path, complete_metadata)
        except OSError:
            if scorecard_path.read_bytes() != original_scorecard:
                _atomic_replace(scorecard_path, original_scorecard)
            if metadata_path.read_bytes() != original_metadata:
                _atomic_replace(metadata_path, original_metadata)
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
