"""Black-box tests for the tc-generator successful-run evidence recorder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

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
    output_root = "artifacts/outputs/03-pressure/pressure" if phase == "pressure" else f"artifacts/outputs/{phase}/{repetition}"
    prompt = campaign / protocol_root / "prompt.txt"
    observation = campaign / protocol_root / "observation.json"
    evaluator_output = campaign / ("03-pressure.md" if phase == "pressure" else f"{phase}/{repetition}.md")
    checked_output = campaign / output_root / "tc-generator-output.json"
    validation = campaign / output_root / "validation-result.json"
    semantic = campaign / output_root / "semantic-result.json"
    prompt_bytes = b"pressure prompt" if phase == "pressure" else (b"red-control prompt\n" if phase == "01-red-control" else b"canonical prompt")
    for path, content in ((prompt, prompt_bytes), (observation, b"observation"), (evaluator_output, b"evaluator output"), (checked_output, b"checked output"), (validation, b"validation result"), (semantic, b"semantic result")):
        _write(path, content)
    validate = [str(Path(sys.executable).resolve()), str((campaign.parents[3] / "tools/validate_artifact.py").resolve()), str((campaign.parents[3] / "schemas/tc-generator-output.schema.json").resolve()), str(checked_output.resolve())]
    mode = "pressure" if phase == "pressure" else ("red-control" if phase == "01-red-control" else "canonical")
    semantic_argv = [str(Path(sys.executable).resolve()), str((campaign / "check_output.py").resolve()), "--input", str((campaign / "artifacts/inputs/context-marker-output.json").resolve()), "--output", str(checked_output.resolve()), "--mode", mode]
    draft = {
        "phase": phase,
        "repetition": repetition,
        "skill_present": phase != "01-red-control",
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
    _write(campaign / "06-run-metadata.json", b'{"artifact_type":"run-metadata","skill_id":"tc-generator","status":"pending","evaluator":null,"host":null,"model":null,"fork_turns":"none","runs":[]}')
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


def test_recorded_run_validates_against_schema_and_authoritative_pending_semantics(tmp_path: Path) -> None:
    campaign = _integration_campaign(tmp_path)
    draft = _draft(campaign)
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
