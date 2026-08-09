"""Black-box tests for the tc-generator successful-run evidence recorder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import load_tool

ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "docs/to_do/skill-tests/tc-generator/record_successful_run.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "docs/to_do/skill-tests/tc-generator"
    prompt = b"red-control prompt\n"
    scenario = {
        "artifact_type": "scenario",
        "skill_id": "tc-generator",
        "canonical_prompt": "canonical prompt",
        "pressure_prompt": "pressure prompt",
        "raw_input_allowlist": ["artifacts/inputs/context-marker-output.json"],
        "rubric_ids": ["traceable-requirements"],
        "repetitions": 5,
        "fork_turns": "none",
        "output_paths": [
            *(f"artifacts/outputs/{phase}/rep-{number:02d}" for phase in ("01-red-control", "02-green-initial", "04-green-final") for number in range(1, 6)),
        ],
        "pressure_output_path": "artifacts/outputs/03-pressure/pressure",
        "prompt_sha256": {"canonical": hashlib.sha256(b"canonical prompt").hexdigest(), "pressure": hashlib.sha256(b"pressure prompt").hexdigest()},
        "phase_prompt_sha256": {
            "01-red-control": hashlib.sha256(prompt).hexdigest(),
            "02-green-initial": hashlib.sha256(b"canonical prompt").hexdigest(),
            "04-green-final": hashlib.sha256(b"canonical prompt").hexdigest(),
        },
        "skill_input_base": "repository-root",
        "required_skill_inputs": [
            {"path": "skills/tc-generator/SKILL.md", "sha256": "0" * 64},
            {"path": "skills/tc-generator/references/case-generation-contract.md", "sha256": "0" * 64},
            {"path": "schemas/tc-generator-output.schema.json", "sha256": "0" * 64},
        ],
        "protocol_contract_version": 1,
    }
    _write(campaign / "00-scenario.json", json.dumps(scenario).encode())
    _write(campaign / "06-run-metadata.json", b'{"artifact_type":"run-metadata","skill_id":"tc-generator","status":"pending","evaluator":null,"host":null,"model":null,"fork_turns":"none","runs":[]}')
    _write(campaign / "check_output.py", b"# recorded command target\n")
    _write(tmp_path / "tools/validate_artifact.py", b"# recorded command target\n")
    _write(tmp_path / "schemas/tc-generator-output.schema.json", b"{}")
    return campaign


def _draft(campaign: Path, phase: str = "01-red-control", repetition: str = "rep-01") -> Path:
    protocol_root = f"artifacts/protocol/{phase}/{repetition}"
    pressure_phase = phase == "pressure" or phase.startswith("03-pressure-v")
    output_root = (
        "artifacts/outputs/03-pressure/pressure" if phase == "pressure"
        else f"artifacts/outputs/{phase}/pressure" if pressure_phase
        else f"artifacts/outputs/{phase}/{repetition}"
    )
    prompt = campaign / protocol_root / "prompt.txt"
    observation = campaign / protocol_root / "observation.json"
    evaluator_output = campaign / ("03-pressure.md" if phase == "pressure" else f"{phase}.md" if pressure_phase else f"{phase}/{repetition}.md")
    checked_output = campaign / output_root / "tc-generator-output.json"
    validation = campaign / output_root / "validation-result.json"
    semantic = campaign / output_root / "semantic-result.json"
    prompt_bytes = (
        _load(campaign / "00-scenario.json")["pressure_prompt"].encode("utf-8")
        if pressure_phase else (b"red-control prompt\n" if phase.startswith("01-red-control") else b"canonical prompt")
    )
    for path, content in ((prompt, prompt_bytes), (observation, b"observation"), (evaluator_output, b"evaluator output"), (checked_output, b"checked output"), (validation, b"validation result"), (semantic, b"semantic result")):
        _write(path, content)
    validate = [str(Path(sys.executable).resolve()), str((campaign.parents[3] / "tools/validate_artifact.py").resolve()), str((campaign.parents[3] / "schemas/tc-generator-output.schema.json").resolve()), str(checked_output.resolve())]
    mode = "pressure" if pressure_phase else ("red-control" if phase.startswith("01-red-control") else "canonical")
    semantic_argv = [str(Path(sys.executable).resolve()), str((campaign / "check_output.py").resolve()), "--input", str((campaign / "artifacts/inputs/context-marker-output.json").resolve()), "--output", str(checked_output.resolve()), "--mode", mode]
    draft = {
        "phase": phase,
        "repetition": repetition,
        "skill_present": not phase.startswith("01-red-control"),
        "prompt_sha256": _sha256(prompt),
        "prompt_snapshot": {"path": str(prompt.relative_to(campaign)).replace("\\", "/"), "sha256": _sha256(prompt)},
        "started_at": "2026-08-09T10:00:00Z",
        "finished_at": "2026-08-09T10:01:00Z",
        "evaluator": {"name": "evidence-runner", "host": "test-host", "model": "test-model"},
        "task": "skill-evaluation",
        "observation": {"path": str(observation.relative_to(campaign)).replace("\\", "/")},
        "output_paths": [str(path.relative_to(campaign)).replace("\\", "/") for path in (evaluator_output, checked_output, validation, semantic)],
        "commands": [
            {"id": "evaluator", "argv": [str(Path(sys.executable).resolve()), "-V"], "cwd": str(campaign.resolve()), "exit_code": 0},
            {"id": "validate-artifact", "argv": validate, "cwd": str(campaign.resolve()), "exit_code": 0},
            {"id": "semantic-check", "argv": semantic_argv, "cwd": str(campaign.resolve()), "exit_code": 0},
        ],
    }
    path = campaign / "draft.json"
    _write(path, json.dumps(draft).encode())
    return path


def _run(campaign: Path, draft: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(RECORDER), str(campaign.resolve()), str(draft.resolve())], text=True, capture_output=True, check=False)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _module():
    spec = importlib.util.spec_from_file_location("tc_generator_run_recorder", RECORDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence_module():
    path = ROOT / "tests/test_skill_test_evidence.py"
    spec = importlib.util.spec_from_file_location("skill_test_evidence", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _integration_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "docs/to_do/skill-tests/tc-generator"
    source = ROOT / "docs/to_do/skill-tests/tc-generator"
    _write(campaign / "00-scenario.json", (source / "00-scenario.json").read_bytes())
    _write(campaign / "06-run-metadata.json", b'{"artifact_type":"run-metadata","skill_id":"tc-generator","status":"pending","evaluator":null,"host":null,"model":null,"effective_red_repetitions":3,"fork_turns":"none","runs":[]}')
    _write(campaign / "check_output.py", b"# semantic command target\n")
    _write(campaign / "artifacts/inputs/context-marker-output.json", b"{}")
    _write(tmp_path / "tools/validate_artifact.py", b"# validator command target\n")
    _write(tmp_path / "schemas/tc-generator-output.schema.json", b"{}")
    return campaign


def _set_draft_prompt(campaign: Path, draft_path: Path, content: bytes) -> None:
    draft = _load(draft_path)
    prompt = campaign / draft["prompt_snapshot"]["path"]
    _write(prompt, content)
    prompt_hash = _sha256(prompt)
    draft["prompt_sha256"] = prompt_hash
    draft["prompt_snapshot"]["sha256"] = prompt_hash
    _write(draft_path, json.dumps(draft).encode())


def test_records_first_successful_run_with_hashed_protocol_and_metadata(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    result = _run(campaign, draft)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "recorded"
    metadata = _load(campaign / "06-run-metadata.json")
    run = metadata["runs"][0]
    protocol = campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json"
    assert metadata["evaluator"] == "evidence-runner"
    assert metadata["host"] == "test-host"
    assert metadata["model"] == "test-model"
    assert run["protocol_snapshot"] == {"path": "artifacts/protocol/01-red-control/rep-01/run-protocol.json", "sha256": _sha256(protocol)}
    assert run["observation"] == {
        "path": "artifacts/protocol/01-red-control/rep-01/observation.json",
        "sha256": _sha256(campaign / "artifacts/protocol/01-red-control/rep-01/observation.json"),
    }
    assert run["outputs"] == [{"path": path, "sha256": _sha256(campaign / path)} for path in _load(draft)["output_paths"]]
    assert _load(protocol)["commands"] == _load(draft)["commands"]


def test_refuses_duplicate_key_without_overwriting_existing_evidence(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    assert _run(campaign, draft).returncode == 0
    before_metadata = (campaign / "06-run-metadata.json").read_bytes()
    before_protocol = (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").read_bytes()
    result = _run(campaign, draft)
    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before_metadata
    assert (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").read_bytes() == before_protocol


def test_refuses_append_when_active_observation_bytes_were_tampered(tmp_path: Path) -> None:
    """Catches an existing run losing the immutable observation bytes that its ledger attests."""
    campaign = _campaign(tmp_path)
    assert _run(campaign, _draft(campaign)).returncode == 0
    observation = campaign / "artifacts/protocol/01-red-control/rep-01/observation.json"
    observation.write_bytes(b"tampered observation")
    before = (campaign / "06-run-metadata.json").read_bytes()

    result = _run(campaign, _draft(campaign, "01-red-control", "rep-02"))

    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-02/run-protocol.json").exists()


def test_requires_next_contiguous_campaign_key(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign, "02-green-initial", "rep-01")
    result = _run(campaign, draft)
    assert result.returncode == 1
    assert not (campaign / "artifacts/protocol/02-green-initial/rep-01/run-protocol.json").exists()
    assert _load(campaign / "06-run-metadata.json")["runs"] == []


def test_refuses_nonzero_command_and_preserves_both_targets(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    data = _load(draft)
    data["commands"][0]["exit_code"] = 7
    _write(draft, json.dumps(data).encode())
    before = (campaign / "06-run-metadata.json").read_bytes()
    result = _run(campaign, draft)
    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").exists()


def test_requires_validator_penultimate_and_semantic_final(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    data = _load(draft)
    data["commands"][-2], data["commands"][-1] = data["commands"][-1], data["commands"][-2]
    _write(draft, json.dumps(data).encode())
    assert _run(campaign, draft).returncode == 1
    assert _load(campaign / "06-run-metadata.json")["runs"] == []


def test_refuses_missing_tampered_or_escaped_evidence(tmp_path: Path) -> None:
    for mutation in ("missing", "tampered", "escape"):
        campaign = _campaign(tmp_path / mutation)
        draft = _draft(campaign)
        data = _load(draft)
        if mutation == "missing":
            (campaign / data["output_paths"][2]).unlink()
        elif mutation == "tampered":
            (campaign / data["prompt_snapshot"]["path"]).write_bytes(b"changed")
        else:
            data["observation"]["path"] = "../outside.json"
            _write(draft, json.dumps(data).encode())
        result = _run(campaign, draft)
        assert result.returncode == 1, mutation
        assert _load(campaign / "06-run-metadata.json")["runs"] == [], mutation
        assert not (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").exists(), mutation


def test_refuses_existing_protocol_target_without_metadata_update(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    protocol = campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json"
    _write(protocol, b"existing immutable protocol")
    before = (campaign / "06-run-metadata.json").read_bytes()
    assert _run(campaign, draft).returncode == 1
    assert protocol.read_bytes() == b"existing immutable protocol"
    assert (campaign / "06-run-metadata.json").read_bytes() == before


def test_leaves_targets_unchanged_when_evaluator_identity_mismatches(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    metadata = _load(campaign / "06-run-metadata.json")
    metadata.update({"evaluator": "other", "host": "test-host", "model": "test-model"})
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    before = (campaign / "06-run-metadata.json").read_bytes()
    result = _run(campaign, draft)
    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").exists()


def test_rolls_back_new_protocol_when_atomic_metadata_replace_fails(tmp_path: Path, monkeypatch) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    module = _module()
    before = (campaign / "06-run-metadata.json").read_bytes()

    def fail_replace(path: Path, content: bytes) -> None:
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(module, "_atomic_replace", fail_replace)
    try:
        module.main([str(RECORDER), str(campaign.resolve()), str(draft.resolve())])
    except OSError as error:
        assert "simulated metadata write failure" in str(error)
    else:
        raise AssertionError("expected metadata replacement failure")
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").exists()


def test_refuses_wrong_skill_present_without_writing(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    payload = _load(draft)
    payload["skill_present"] = True
    _write(draft, json.dumps(payload).encode())
    assert _run(campaign, draft).returncode == 1
    assert _load(campaign / "06-run-metadata.json")["runs"] == []


def test_refuses_malformed_or_reversed_utc_timestamps_without_writing(tmp_path: Path) -> None:
    for mutation in ("malformed", "reversed"):
        campaign = _campaign(tmp_path / mutation)
        draft = _draft(campaign)
        payload = _load(draft)
        if mutation == "malformed":
            payload["started_at"] = "2026-13-09T10:00:00Z"
        else:
            payload["finished_at"] = "2026-08-09T09:59:59Z"
        _write(draft, json.dumps(payload).encode())
        assert _run(campaign, draft).returncode == 1, mutation
        assert _load(campaign / "06-run-metadata.json")["runs"] == [], mutation


def test_records_versioned_final_and_preserves_later_invalidated_attempt(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    scenario = _load(campaign / "00-scenario.json")
    final_phase = "05-green-final-v1"
    scenario["effective_final_phase"] = final_phase
    scenario["output_paths"] = [
        *(f"artifacts/outputs/{phase}/rep-{number:02d}" for phase in ("01-red-control", "02-green-initial", final_phase) for number in range(1, 6)),
    ]
    _write(campaign / "00-scenario.json", json.dumps(scenario).encode())
    assert load_tool("validate_artifact").validate(
        str(ROOT / "schemas/skill-test-evidence.schema.json"), str(campaign / "00-scenario.json")
    )[0] == 0
    order = [
        *( ("01-red-control", f"rep-{number:02d}") for number in range(1, 6) ),
        *( ("02-green-initial", f"rep-{number:02d}") for number in range(1, 6) ),
        ("pressure", "pressure"),
    ]
    for phase, repetition in order:
        draft = _draft(campaign, phase, repetition)
        assert _run(campaign, draft).returncode == 0
    metadata = _load(campaign / "06-run-metadata.json")
    invalidated = [{"phase": final_phase, "repetition": "rep-02", "reason": "preserved"}]
    metadata["invalidated_attempts"] = invalidated
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    draft = _draft(campaign, final_phase, "rep-01")
    assert _run(campaign, draft).returncode == 0
    updated = _load(campaign / "06-run-metadata.json")
    assert updated["runs"][-1]["phase"] == final_phase
    assert updated["invalidated_attempts"] == invalidated


def test_refuses_next_key_already_preserved_as_invalidated(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    draft = _draft(campaign)
    metadata = _load(campaign / "06-run-metadata.json")
    metadata["invalidated_attempts"] = [{"phase": "01-red-control", "repetition": "rep-01", "reason": "failed"}]
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    before = (campaign / "06-run-metadata.json").read_bytes()
    assert _run(campaign, draft).returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-01/run-protocol.json").exists()


def test_refuses_existing_active_key_also_reserved_by_distinct_invalidated_evidence(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    assert _run(campaign, _draft(campaign)).returncode == 0
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    invalidated_prompt = campaign / "artifacts/invalidated/01-red-control/rep-01/prompt.txt"
    _write(invalidated_prompt, b"separate invalidated evidence\n")
    metadata["invalidated_attempts"] = [{
        "phase": "01-red-control",
        "repetition": "rep-01",
        "prompt_snapshot": {"path": str(invalidated_prompt.relative_to(campaign)).replace("\\", "/"), "sha256": _sha256(invalidated_prompt)},
    }]
    _write(metadata_path, json.dumps(metadata).encode())
    before = metadata_path.read_bytes()

    assert _run(campaign, _draft(campaign, "01-red-control", "rep-02")).returncode == 1

    assert metadata_path.read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control/rep-02/run-protocol.json").exists()


def test_recorded_run_validates_against_schema_and_authoritative_pending_semantics(tmp_path: Path) -> None:
    campaign = _integration_campaign(tmp_path)
    draft = _draft(campaign, "01-red-control-v2", "rep-01")
    _set_draft_prompt(campaign, draft, (ROOT / "docs/to_do/skill-tests/tc-generator/artifacts/prompts/01-red-control.txt").read_bytes())
    result = _run(campaign, draft)
    assert result.returncode == 0, result.stdout + result.stderr
    metadata_path = campaign / "06-run-metadata.json"
    validator = load_tool("validate_artifact")
    assert validator.validate(str(ROOT / "schemas/skill-test-evidence.schema.json"), str(metadata_path))[0] == 0
    evidence = _evidence_module()
    scenario = _load(campaign / "00-scenario.json")
    scorecards = [evidence._pending_scorecard(scenario, phase) for phase in evidence.SCORECARD_PHASES]
    evidence._assert_metadata_semantics(_load(metadata_path), campaign, scenario, scorecards)


def _versioned_red_campaign(tmp_path: Path) -> Path:
    campaign = _campaign(tmp_path)
    for repetition in ("rep-01", "rep-02"):
        assert _run(campaign, _draft(campaign, "01-red-control", repetition)).returncode == 0
    metadata = _load(campaign / "06-run-metadata.json")
    historical_runs = metadata.pop("runs")
    scenario = _load(campaign / "00-scenario.json")
    effective_red_phase = "01-red-control-v2"
    scenario["effective_red_phase"] = effective_red_phase
    scenario["output_paths"] = [
        *(f"artifacts/outputs/{phase}/rep-{number:02d}" for phase in (effective_red_phase, "02-green-initial", "04-green-final") for number in range(1, 6)),
    ]
    scenario["phase_prompt_sha256"][effective_red_phase] = scenario["phase_prompt_sha256"]["01-red-control"]
    _write(campaign / "00-scenario.json", json.dumps(scenario).encode())
    metadata.update(evaluator=None, host=None, model=None, runs=[], historical_runs=historical_runs)
    draft = _load(_draft(campaign, "01-red-control", "rep-03"))
    commands = draft["commands"]
    commands[-1] = {**commands[-1], "id": "validate-artifact", "exit_code": 1}
    commands.pop()
    attempt_root = "artifacts/invalidated/01-red-control/rep-03/stopped"
    _write_evidence = lambda path, content: (_write(campaign / path, content), {"path": path, "sha256": hashlib.sha256(content).hexdigest()})[1]
    diagnostic = _write_evidence(f"{attempt_root}/validation-result.json", b"invalid schema\n")
    archive = _write_evidence(f"{attempt_root}/tc-generator-output.json", b"checked output")
    reserved = campaign / "artifacts/outputs/01-red-control/rep-03/tc-generator-output.json"
    _write(reserved, b"checked output")
    observation = _write_evidence(f"{attempt_root}/observation.json", b"observation")
    prompt_snapshot = _write_evidence(f"{attempt_root}/prompt.txt", b"red-control prompt\n")
    evaluator = {"name": "evidence-runner", "host": "test-host", "model": "test-model"}
    protocol = {"artifact_type": "run-protocol", "skill_id": "tc-generator", "phase": "01-red-control", "repetition": "rep-03", "application_prompt_sha256": prompt_snapshot["sha256"], "started_at": "2026-08-09T10:00:00Z", "finished_at": "2026-08-09T10:01:00Z", "evaluator": evaluator, "task": "skill-evaluation", "observation_path": observation["path"], "output_paths": [diagnostic["path"], "artifacts/outputs/01-red-control/rep-03/tc-generator-output.json"], "commands": commands}
    protocol_snapshot = _write_evidence(f"{attempt_root}/run-protocol.json", json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode())
    metadata["invalidated_attempts"] = [{"attempt_id": "stopped", "classification": "protocol-invalid", "phase": "01-red-control", "repetition": "rep-03", "reason_codes": ["schema-validation-failed"], "excluded_from_score": True, "observed_prompt_sha256": prompt_snapshot["sha256"], "raw_input_allowlist": scenario["raw_input_allowlist"], "prompt_snapshot": prompt_snapshot, "observation": observation, "outputs": [diagnostic], "started_at": "2026-08-09T10:00:00Z", "finished_at": "2026-08-09T10:01:00Z", "evaluator": evaluator, "task": "skill-evaluation", "protocol_snapshot": protocol_snapshot, "commands": commands, "checked_output": archive}]
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    return campaign


def _shortened_versioned_red_campaign(tmp_path: Path) -> Path:
    """Build the one sanctioned v2 ledger: two historical invalidations plus a 3-run RED."""
    campaign = _versioned_red_campaign(tmp_path)
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    scenario["effective_red_repetitions"] = 3
    metadata["effective_red_repetitions"] = 3
    _write(campaign / "00-scenario.json", json.dumps(scenario).encode())
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())

    for repetition in ("rep-01", "rep-02", "rep-03"):
        assert _run(campaign, _draft(campaign, "01-red-control-v2", repetition)).returncode == 0

    attempt_root = "artifacts/invalidated/01-red-control-v2/rep-04/stopped-v2"
    draft = _load(_draft(campaign, "01-red-control-v2", "rep-04"))
    commands = draft["commands"][:-1]
    commands[-1] = {**commands[-1], "exit_code": 1}
    write_evidence = lambda path, content: (
        _write(campaign / path, content), {"path": path, "sha256": hashlib.sha256(content).hexdigest()},
    )[1]
    diagnostic = write_evidence(f"{attempt_root}/validation-result.json", b"invalid schema\n")
    reserved = campaign / "artifacts/outputs/01-red-control-v2/rep-04/tc-generator-output.json"
    archive = write_evidence(f"{attempt_root}/tc-generator-output.json", reserved.read_bytes())
    observation = write_evidence(f"{attempt_root}/observation.json", b"observation\n")
    prompt = write_evidence(f"{attempt_root}/prompt.txt", b"red-control prompt\n")
    evaluator = draft["evaluator"]
    protocol = {
        "artifact_type": "run-protocol", "skill_id": "tc-generator", "phase": "01-red-control-v2",
        "repetition": "rep-04", "application_prompt_sha256": prompt["sha256"],
        "started_at": draft["started_at"], "finished_at": draft["finished_at"],
        "evaluator": evaluator, "task": draft["task"], "observation_path": observation["path"],
        "output_paths": [diagnostic["path"], "artifacts/outputs/01-red-control-v2/rep-04/tc-generator-output.json"],
        "commands": commands,
    }
    protocol_snapshot = write_evidence(
        f"{attempt_root}/run-protocol.json", json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode()
    )
    metadata = _load(campaign / "06-run-metadata.json")
    metadata["invalidated_attempts"].append({
        "attempt_id": "stopped-v2", "classification": "protocol-invalid", "phase": "01-red-control-v2",
        "repetition": "rep-04", "reason_codes": ["schema-validation-failed"], "excluded_from_score": True,
        "observed_prompt_sha256": prompt["sha256"], "raw_input_allowlist": scenario["raw_input_allowlist"],
        "prompt_snapshot": prompt, "observation": observation, "outputs": [diagnostic],
        "started_at": draft["started_at"], "finished_at": draft["finished_at"], "evaluator": evaluator,
        "task": draft["task"], "protocol_snapshot": protocol_snapshot, "commands": commands,
        "checked_output": archive,
    })
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    return campaign


def test_shortened_v2_red_allows_green_rep_01_after_the_terminal_invalidated_rep_04(tmp_path: Path) -> None:
    """Catches a terminal unscored RED-v2 rep-04 blocking the next GREEN key."""
    campaign = _shortened_versioned_red_campaign(tmp_path)

    result = _run(campaign, _draft(campaign, "02-green-initial", "rep-01"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert [(run["phase"], run["repetition"]) for run in _load(campaign / "06-run-metadata.json")["runs"]] == [
        ("01-red-control-v2", "rep-01"), ("01-red-control-v2", "rep-02"),
        ("01-red-control-v2", "rep-03"), ("02-green-initial", "rep-01"),
    ]


@pytest.mark.parametrize("repetition", ["rep-04", "rep-05"])
def test_shortened_v2_red_refuses_further_red_drafts(tmp_path: Path, repetition: str) -> None:
    """Catches either the terminal failed RED key or a prohibited fifth RED key becoming runnable."""
    campaign = _shortened_versioned_red_campaign(tmp_path)
    before = (campaign / "06-run-metadata.json").read_bytes()

    result = _run(campaign, _draft(campaign, "01-red-control-v2", repetition))

    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / f"artifacts/protocol/01-red-control-v2/{repetition}/run-protocol.json").exists()


def test_versioned_red_first_append_requires_historical_evaluator_identity(tmp_path: Path) -> None:
    campaign = _versioned_red_campaign(tmp_path)
    draft = _draft(campaign, "01-red-control-v2", "rep-01")
    payload = _load(draft)
    payload["evaluator"]["model"] = "other-model"
    _write(draft, json.dumps(payload).encode())

    before = (campaign / "06-run-metadata.json").read_bytes()
    result = _run(campaign, draft)

    assert result.returncode == 1
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/01-red-control-v2/rep-01/run-protocol.json").exists()


def test_versioned_red_first_append_preserves_stopped_ledgers_and_uses_red_semantics(tmp_path: Path) -> None:
    campaign = _versioned_red_campaign(tmp_path)
    draft = _draft(campaign, "01-red-control-v2", "rep-01")
    before = _load(campaign / "06-run-metadata.json")

    result = _run(campaign, draft)

    assert result.returncode == 0, result.stderr
    updated = _load(campaign / "06-run-metadata.json")
    assert updated["runs"][0]["phase"] == "01-red-control-v2"
    assert updated["runs"][0]["skill_present"] is False
    assert updated["evaluator"] == "evidence-runner"
    assert updated["historical_runs"] == before["historical_runs"]
    assert updated["invalidated_attempts"] == before["invalidated_attempts"]
    protocol = _load(campaign / "artifacts/protocol/01-red-control-v2/rep-01/run-protocol.json")
    assert protocol["commands"][-1]["argv"][-1] == "red-control"


def test_real_stopped_red_history_accepts_the_first_active_v2_append(tmp_path: Path) -> None:
    campaign = tmp_path / "docs/to_do/skill-tests/tc-generator"
    shutil.copytree(ROOT / "docs/to_do/skill-tests/tc-generator", campaign)
    _write(tmp_path / "tools/validate_artifact.py", b"# validator command target\n")
    _write(tmp_path / "schemas/tc-generator-output.schema.json", b"{}")
    metadata = _load(campaign / "06-run-metadata.json")
    for run in metadata["runs"]:
        (campaign / run["protocol_snapshot"]["path"]).unlink()
    metadata["status"] = "pending"
    metadata["runs"] = []
    metadata["evaluator"] = None
    metadata["host"] = None
    metadata["model"] = None
    metadata["invalidated_attempts"] = [
        attempt for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("01-red-control", "rep-03")
    ]
    scenario = _load(campaign / "00-scenario.json")
    scenario["effective_pressure_phase"] = "03-pressure-v2"
    scenario["pressure_output_path"] = "artifacts/outputs/03-pressure-v2/pressure"
    _write(campaign / "00-scenario.json", json.dumps(scenario).encode())
    for run in metadata["historical_runs"]:
        protocol_path = campaign / run["protocol_snapshot"]["path"]
        protocol = _load(protocol_path)
        template = _load(_draft(campaign, "01-red-control-v2", run["repetition"]))["commands"]
        for command in template:
            command["argv"] = [argument.replace("01-red-control-v2", run["phase"]) for argument in command["argv"]]
        protocol["commands"] = [{**protocol["commands"][0], "cwd": str(campaign.resolve())}, *template[-2:]]
        run["commands"] = protocol["commands"]
        _write(protocol_path, json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode())
        run["protocol_snapshot"]["sha256"] = _sha256(protocol_path)
    attempt = next(
        attempt for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("01-red-control", "rep-03")
    )
    attempt_protocol_path = campaign / attempt["protocol_snapshot"]["path"]
    attempt_protocol = _load(attempt_protocol_path)
    source_root = str(ROOT)
    target_root = str(tmp_path)
    attempt["commands"] = [
        {**command, "argv": [argument.replace(source_root, target_root) for argument in command["argv"]], "cwd": str(campaign.resolve())}
        for command in attempt["commands"]
    ]
    attempt_protocol["commands"] = attempt["commands"]
    _write(attempt_protocol_path, json.dumps(attempt_protocol, sort_keys=True, separators=(",", ":")).encode())
    attempt["protocol_snapshot"]["sha256"] = _sha256(attempt_protocol_path)
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    historical_protocol = _load(campaign / metadata["historical_runs"][0]["protocol_snapshot"]["path"])
    draft = _draft(campaign, "01-red-control-v2", "rep-01")
    _set_draft_prompt(campaign, draft, (campaign / "artifacts/prompts/01-red-control.txt").read_bytes())
    payload = _load(draft)
    payload["evaluator"] = historical_protocol["evaluator"]
    _write(draft, json.dumps(payload).encode())

    result = _run(campaign, draft)

    assert result.returncode == 0, result.stdout + result.stderr
    updated = _load(campaign / "06-run-metadata.json")
    assert load_tool("validate_artifact").validate(
        str(ROOT / "schemas/skill-test-evidence.schema.json"), str(campaign / "06-run-metadata.json")
    )[0] == 0
    evidence = _evidence_module()
    scenario = _load(campaign / "00-scenario.json")
    scorecards = [evidence._pending_scorecard(scenario, phase) for phase in evidence.SCORECARD_PHASES]
    evidence._assert_metadata_semantics(updated, campaign, scenario, scorecards)


def test_refuses_hardlinked_v2_output_aliasing_historical_evidence(tmp_path: Path) -> None:
    campaign = _versioned_red_campaign(tmp_path)
    draft = _draft(campaign, "01-red-control-v2", "rep-01")
    payload = _load(draft)
    alias = campaign / payload["output_paths"][1]
    source = campaign / _load(campaign / "06-run-metadata.json")["historical_runs"][0]["outputs"][1]["path"]
    alias.unlink()
    try:
        alias.hardlink_to(source)
    except OSError as error:
        pytest.skip(f"filesystem cannot create hard links: {error}")

    result = _run(campaign, draft)

    assert result.returncode == 1
    assert "draft output reuses evidence" in result.stdout


def test_real_stopped_history_rejects_tampered_archived_checked_output_hash() -> None:
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    attempt = deepcopy(next(
        attempt for attempt in _load(campaign / "06-run-metadata.json")["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("01-red-control", "rep-03")
    ))
    attempt["checked_output"]["sha256"] = "0" * 64
    module = _module()

    with pytest.raises(module.Refusal, match="checked output"):
        module._invalidated_protocol_identity(campaign, scenario, attempt)


def test_real_initial_green_invalidations_reserve_unscored_versions_and_route_to_v3() -> None:
    """Keeps both immutable initial-GREEN failures excluded before the fresh v3 key."""
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    prompt_mismatch = next(
        attempt
        for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("02-green-initial", "rep-01")
    )
    schema_failure = next(
        attempt
        for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("02-green-initial-v2", "rep-01")
    )

    module = _module()
    _identity, reserved_keys, _paths = module._historical_identity(
        campaign, scenario, metadata, metadata["invalidated_attempts"]
    )

    assert prompt_mismatch["reason_codes"] == ["prompt-mismatch"]
    assert "evaluator" not in prompt_mismatch
    assert "task" not in prompt_mismatch
    assert schema_failure["reason_codes"] == ["schema-validation-failed"]
    assert schema_failure["excluded_from_score"] is True
    assert ("02-green-initial", "rep-01") in reserved_keys
    assert ("02-green-initial-v2", "rep-01") in reserved_keys
    assert metadata["status"] == "complete"
    assert module._phase_keys(scenario) == [
        (run["phase"], run["repetition"]) for run in metadata["runs"]
    ]


def test_pressure_recovery_routes_only_explicit_v3_and_preserves_legacy_pressure_key() -> None:
    """Catches pressure-v2 spawn failure being reused by v3 or later versions."""
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    module = _module()

    phase_keys = module._phase_keys(scenario)
    assert metadata["status"] == "complete"
    assert phase_keys == [(run["phase"], run["repetition"]) for run in metadata["runs"]]
    assert module._expected_output_paths("pressure", "pressure")[0] == "artifacts/outputs/03-pressure/pressure"
    assert module._expected_output_paths("pressure", "pressure")[1][0] == "03-pressure.md"
    assert module._expected_output_paths("03-pressure-v3", "pressure")[0] == "artifacts/outputs/03-pressure-v3/pressure"
    assert module._expected_output_paths("03-pressure-v3", "pressure")[1][0] == "03-pressure-v3.md"
    legacy = deepcopy(scenario)
    legacy.pop("effective_pressure_phase")
    assert module._phase_keys(legacy)[phase_keys.index(("03-pressure-v4", "pressure"))] == ("pressure", "pressure")
    v2 = deepcopy(scenario)
    v2["effective_pressure_phase"] = "03-pressure-v2"
    with pytest.raises(module.Refusal):
        module._historical_identity(campaign, v2, metadata, metadata["invalidated_attempts"])
    v5 = deepcopy(scenario)
    v5["effective_pressure_phase"] = "03-pressure-v5"
    with pytest.raises(module.Refusal):
        module._historical_identity(campaign, v5, metadata, metadata["invalidated_attempts"])
    assert _evidence_module()._tc_generator_semantic_mode("pressure") == "pressure"
    assert _evidence_module()._tc_generator_semantic_mode("03-pressure-v3") == "pressure"


@pytest.mark.parametrize("phase", ["pressure", "03-pressure-v2"])
def test_pressure_v3_refuses_a_broken_historical_invalidation_chain(phase: str) -> None:
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    metadata["invalidated_attempts"] = [
        attempt for attempt in metadata["invalidated_attempts"]
        if attempt["phase"] != phase
    ]

    module = _module()
    with pytest.raises(module.Refusal, match="pressure-v4 requires"):
        module._historical_identity(campaign, scenario, metadata, metadata["invalidated_attempts"])


def _rebase_pressure_v2_archive_for_integration(campaign: Path) -> None:
    """Keep copied archive semantics while replacing only absolute local test paths."""
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    repository = campaign.parents[3]
    cwd = str(campaign.resolve())
    for run in [*metadata.get("historical_runs", []), *metadata["runs"]]:
        phase, repetition = run["phase"], run["repetition"]
        _output_root, outputs = _module()._expected_output_paths(phase, repetition)
        validator = run["commands"][-2]
        semantic = run["commands"][-1]
        validator["argv"] = [
            validator["argv"][0], str((repository / "tools/validate_artifact.py").resolve()),
            str((repository / "schemas/tc-generator-output.schema.json").resolve()), str((campaign / outputs[1]).resolve()),
        ]
        semantic["argv"] = [
            validator["argv"][0], str((campaign / "check_output.py").resolve()), "--input",
            str((campaign / "artifacts/inputs/context-marker-output.json").resolve()), "--output",
            str((campaign / outputs[1]).resolve()), "--mode",
            "pressure" if phase.startswith("03-pressure-v") or phase == "pressure" else "red-control" if phase.startswith("01-red-control") else "canonical",
        ]
        for command in run["commands"]:
            command["cwd"] = cwd
        protocol_path = campaign / run["protocol_snapshot"]["path"]
        protocol = _load(protocol_path)
        protocol["commands"] = run["commands"]
        _write(protocol_path, json.dumps(protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        run["protocol_snapshot"]["sha256"] = _sha256(protocol_path)
    predelegation = next(
        item for item in metadata["invalidated_attempts"]
        if item["attempt_id"] == "green-initial-rep-01-prompt-mismatch"
    )
    predelegation["commands"][0]["cwd"] = cwd
    predelegation["commands"][1]["cwd"] = cwd
    predelegation["commands"][1]["argv"][-2] = str((campaign / "artifacts/protocol/02-green-initial/rep-01/prompt.txt").resolve())
    predelegation_protocol_path = campaign / predelegation["protocol_snapshot"]["path"]
    predelegation_protocol = _load(predelegation_protocol_path)
    predelegation_protocol["commands"] = predelegation["commands"]
    _write(predelegation_protocol_path, json.dumps(predelegation_protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    predelegation["protocol_snapshot"]["sha256"] = _sha256(predelegation_protocol_path)
    initial_schema_failure = next(
        item for item in metadata["invalidated_attempts"]
        if (item["phase"], item["repetition"]) == ("02-green-initial-v2", "rep-01")
    )
    validator = initial_schema_failure["commands"][-1]
    validator["argv"] = [
        validator["argv"][0], str((repository / "tools/validate_artifact.py").resolve()),
        str((repository / "schemas/tc-generator-output.schema.json").resolve()),
        str((campaign / "artifacts/outputs/02-green-initial-v2/rep-01/tc-generator-output.json").resolve()),
    ]
    for command in initial_schema_failure["commands"]:
        command["cwd"] = cwd
    initial_schema_protocol_path = campaign / initial_schema_failure["protocol_snapshot"]["path"]
    initial_schema_protocol = _load(initial_schema_protocol_path)
    initial_schema_protocol["commands"] = initial_schema_failure["commands"]
    _write(initial_schema_protocol_path, json.dumps(initial_schema_protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    initial_schema_failure["protocol_snapshot"]["sha256"] = _sha256(initial_schema_protocol_path)
    attempt = next(
        item for item in metadata["invalidated_attempts"]
        if (item["phase"], item["repetition"]) == ("03-pressure-v2", "pressure")
    )
    archive_root = campaign / "artifacts/invalidated/03-pressure-v2/pressure/pressure-v2-evaluator-spawn-failed"
    protocol_path = archive_root / "run-protocol.json"
    diagnostic_path = archive_root / "diagnostic.json"
    protocol = _load(protocol_path)
    python = "D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe"
    prompt = str((campaign / "artifacts/protocol/03-pressure-v2/pressure/prompt.txt").resolve())
    commands = protocol["commands"]
    commands[1]["argv"][3:5] = [str((campaign / "00-scenario.json").resolve()), prompt]
    commands[2]["argv"][-2] = prompt
    commands[3]["argv"] = [
        python, commands[3]["argv"][1], commands[3]["argv"][2],
        str((repository / "skills/tc-generator/SKILL.md").resolve()), commands[3]["argv"][4],
        str((repository / "skills/tc-generator/references/case-generation-contract.md").resolve()), commands[3]["argv"][6],
        str((repository / "schemas/tc-generator-output.schema.json").resolve()), commands[3]["argv"][8],
    ]
    for command in commands:
        command["cwd"] = cwd
    diagnostic = _load(diagnostic_path)
    diagnostic["argv"][-1] = prompt
    diagnostic["cwd"] = cwd
    _write(diagnostic_path, json.dumps(diagnostic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    _write(protocol_path, json.dumps(protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    attempt["commands"] = commands
    attempt["outputs"][0]["sha256"] = _sha256(diagnostic_path)
    attempt["protocol_snapshot"]["sha256"] = _sha256(protocol_path)
    _write(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _pre_pressure_campaign(tmp_path: Path) -> Path:
    """Copy the real campaign through the initial-GREEN prefix, before pressure-v4 is recorded."""
    source = ROOT / "docs/to_do/skill-tests/tc-generator"
    campaign = tmp_path / "docs/to_do/skill-tests/tc-generator"
    shutil.copytree(source, campaign)
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    pressure_key = ("03-pressure-v4", "pressure")
    pressure_index = next(
        index
        for index, run in enumerate(metadata["runs"])
        if (run["phase"], run["repetition"]) == pressure_key
    )
    metadata["status"] = "pending"
    metadata["runs"] = metadata["runs"][:pressure_index]
    _write(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    shutil.rmtree(campaign / "artifacts/protocol/03-pressure-v4")
    shutil.rmtree(campaign / "artifacts/outputs/03-pressure-v4")
    (campaign / "03-pressure-v4.md").unlink()
    _rebase_pressure_v2_archive_for_integration(campaign)
    return campaign


def test_valid_pressure_v4_draft_records_once_and_preserves_archived_pressure_chain(tmp_path: Path) -> None:
    campaign = _pre_pressure_campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    before = metadata_path.read_bytes()
    before_invalidated = deepcopy(_load(metadata_path)["invalidated_attempts"])
    archive_paths = [
        campaign / attempt[field]["path"]
        for attempt in _load(metadata_path)["invalidated_attempts"]
        if attempt["repetition"] == "pressure" and attempt["phase"] in {"pressure", "03-pressure-v2", "03-pressure-v3"}
        for field in ("prompt_snapshot", "observation", "protocol_snapshot")
    ]
    archive_paths.extend(
        campaign / output["path"]
        for attempt in _load(metadata_path)["invalidated_attempts"]
        if attempt["repetition"] == "pressure" and attempt["phase"] in {"pressure", "03-pressure-v2", "03-pressure-v3"}
        for output in attempt["outputs"]
    )
    archive_bytes = {path: path.read_bytes() for path in archive_paths}
    draft = _draft(campaign, "03-pressure-v4", "pressure")
    payload = _load(draft)
    payload["evaluator"] = {"name": "sol_advisor_terra_implementer", "host": "native-subagent-v2", "model": "gpt-5.6-terra/high (role-pinned)"}
    _write(draft, json.dumps(payload).encode("utf-8"))

    result = _run(campaign, draft)

    assert result.returncode == 0, result.stdout
    updated = _load(metadata_path)
    assert len(updated["runs"]) == 9
    assert [(run["phase"], run["repetition"]) for run in updated["runs"][-1:]] == [("03-pressure-v4", "pressure")]
    assert (campaign / "artifacts/protocol/03-pressure-v4/pressure/run-protocol.json").is_file()
    assert all(path.read_bytes() == content for path, content in archive_bytes.items())
    assert updated["invalidated_attempts"] == before_invalidated
    assert _module()._phase_keys(_load(campaign / "00-scenario.json"))[len(updated["runs"])] == ("04-green-final", "rep-01")
    assert before != metadata_path.read_bytes()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda attempt: attempt["outputs"][0].__setitem__("path", attempt["outputs"][0]["path"].replace("diagnostic.json", "other.json")),
        lambda attempt: attempt.__setitem__("checked_output", attempt["outputs"][0]),
        lambda attempt: attempt["commands"].append({"id": "read-prompt", "argv": ["D:\\python.exe"], "cwd": str(ROOT), "exit_code": 0}),
        lambda attempt: attempt["commands"].append({"id": "validate-artifact", "argv": ["D:\\python.exe"], "cwd": str(ROOT), "exit_code": 0}),
        lambda attempt: attempt["outputs"].append({"path": "artifacts/outputs/03-pressure-v2/pressure/tc-generator-output.json", "sha256": "0" * 64}),
        lambda attempt: attempt["evaluator"].__setitem__("host", "wrong-host"),
        lambda attempt: attempt.__setitem__("started_at", "2026-08-09T20:20:18.805Z"),
    ],
)
def test_pressure_v2_spawn_failure_rejects_relaxed_archive_contract(mutate) -> None:
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    attempt = deepcopy(next(
        attempt for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("03-pressure-v2", "pressure")
    ))
    mutate(attempt)

    module = _module()
    with pytest.raises((module.Refusal, module.InvocationError)):
        module._invalidated_protocol_identity(campaign, scenario, attempt)


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_created", True),
        ("windows_api", "CreateProcessW"),
        ("error", "different error"),
    ],
)
def test_pressure_v2_spawn_failure_rejects_mutated_process_diagnostic(tmp_path: Path, field: str, value: object) -> None:
    source = ROOT / "docs/to_do/skill-tests/tc-generator"
    campaign = tmp_path / "tc-generator"
    shutil.copytree(source, campaign)
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    attempt = next(
        item for item in metadata["invalidated_attempts"]
        if (item["phase"], item["repetition"]) == ("03-pressure-v2", "pressure")
    )
    diagnostic_path = campaign / attempt["outputs"][0]["path"]
    diagnostic = _load(diagnostic_path)
    diagnostic[field] = value
    _write(diagnostic_path, json.dumps(diagnostic, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    module = _module()
    with pytest.raises(module.Refusal):
        module._validate_pressure_v2_evaluator_spawn_failure(campaign, scenario, attempt)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda attempt: attempt.pop("checked_output"),
        lambda attempt: attempt["commands"][-1].update(exit_code=0),
        lambda attempt: attempt["commands"][-1]["argv"].__setitem__(-1, "D:\\other-output.json"),
        lambda attempt: attempt["commands"].append({"id": "semantic-check", "argv": ["D:\\python.exe"], "cwd": attempt["commands"][-1]["cwd"], "exit_code": 0}),
    ],
)
def test_real_initial_green_schema_failure_rejects_relaxed_validator_contract(mutate) -> None:
    campaign = ROOT / "docs/to_do/skill-tests/tc-generator"
    scenario = _load(campaign / "00-scenario.json")
    metadata = _load(campaign / "06-run-metadata.json")
    attempt = deepcopy(next(
        attempt for attempt in metadata["invalidated_attempts"]
        if (attempt["phase"], attempt["repetition"]) == ("02-green-initial-v2", "rep-01")
    ))
    mutate(attempt)
    module = _module()

    with pytest.raises(module.Refusal):
        module._validate_archived_initial_green_schema_failure(campaign, scenario, attempt)


def test_historical_invalidation_without_protocol_snapshot_refuses_before_write(tmp_path: Path) -> None:
    campaign = _versioned_red_campaign(tmp_path)
    metadata = _load(campaign / "06-run-metadata.json")
    metadata["invalidated_attempts"][0].pop("protocol_snapshot")
    _write(campaign / "06-run-metadata.json", json.dumps(metadata).encode())
    before = (campaign / "06-run-metadata.json").read_bytes()
    result = _run(campaign, _draft(campaign, "01-red-control-v2", "rep-01"))
    assert result.returncode == 2
    assert (campaign / "06-run-metadata.json").read_bytes() == before


def test_refuses_sixteenth_run_without_creating_final_protocol(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    order = [(phase, repetition) for phase in ("01-red-control", "02-green-initial") for repetition in (f"rep-{number:02d}" for number in range(1, 6))] + [("pressure", "pressure")] + [("04-green-final", f"rep-{number:02d}") for number in range(1, 6)]
    for phase, repetition in order[:15]:
        assert _run(campaign, _draft(campaign, phase, repetition)).returncode == 0
    before = (campaign / "06-run-metadata.json").read_bytes()
    result = _run(campaign, _draft(campaign, *order[15]))
    assert result.returncode == 1
    assert "final repetition requires atomic campaign completion" in result.stdout
    assert (campaign / "06-run-metadata.json").read_bytes() == before
    assert not (campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json").exists()
