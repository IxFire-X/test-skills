"""Focused tests for the bounded tc-generator campaign finalizer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import load_tool

ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "docs/to_do/skill-tests/tc-generator/finalize_campaign.py"
RECORDER = ROOT / "docs/to_do/skill-tests/tc-generator/record_successful_run.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _finalizer():
    return _load_module("tc_generator_campaign_finalizer", FINALIZER)


def _recorder():
    return _load_module("tc_generator_run_recorder_for_finalizer", RECORDER)


def _evidence():
    return _load_module("skill_test_evidence_for_finalizer", ROOT / "tests/test_skill_test_evidence.py")


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _terminal_draft(campaign: Path, scenario: dict, phase: str, repetition: str) -> Path:
    protocol_root = f"artifacts/protocol/{phase}/{repetition}"
    output_root = f"artifacts/outputs/{phase}/{repetition}"
    prompt = campaign / protocol_root / "prompt.txt"
    observation = campaign / protocol_root / "observation.json"
    report = campaign / phase / f"{repetition}.md"
    output = campaign / output_root / "tc-generator-output.json"
    validation = campaign / output_root / "validation-result.json"
    semantic = campaign / output_root / "semantic-result.json"
    _write(prompt, scenario["canonical_prompt"].encode())
    _write(observation, b"terminal observation\n")
    _write(report, b"terminal scored evidence\n")
    _write(output, b'{"artifact_type":"tc-generator-output"}\n')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    _write(validation, json.dumps({"status": "valid", "errors": []}).encode())
    _write(semantic, json.dumps({"status": "pass", "errors": [], "artifact_sha256": digest}).encode())
    evidence = _evidence()
    draft = {
        "phase": phase,
        "repetition": repetition,
        "skill_present": True,
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "prompt_snapshot": {"path": f"{protocol_root}/prompt.txt", "sha256": hashlib.sha256(prompt.read_bytes()).hexdigest()},
        "started_at": "2026-08-10T10:00:00Z",
        "finished_at": "2026-08-10T10:01:00Z",
        "evaluator": {"name": "evidence-runner", "host": "ci", "model": "test-model"},
        "task": "skill-evaluation",
        "observation": {"path": f"{protocol_root}/observation.json"},
        "output_paths": [f"{phase}/{repetition}.md", f"{output_root}/tc-generator-output.json", f"{output_root}/validation-result.json", f"{output_root}/semantic-result.json"],
        "commands": evidence._captured_protocol_v1_final_commands(campaign, "tc-generator", phase, repetition),
    }
    draft_path = campaign / "terminal-draft.json"
    _write(draft_path, json.dumps(draft, ensure_ascii=False).encode())
    return draft_path


def _campaign(tmp_path: Path, *, successful_count: int = 13):
    finalizer = _finalizer()
    evidence = _evidence()
    campaign, scenario, _scorecards, metadata = evidence._pending_tc_generator_campaign(
        tmp_path, ROOT, successful_count=successful_count
    )
    # The shared fixture emits the unversioned active pressure key; retain that
    # key in this isolated terminal-controller fixture so recorder validation is
    # exercising a coherent campaign prefix rather than a production revision.
    scenario["effective_pressure_phase"] = "pressure"
    scenario["pressure_output_path"] = "artifacts/outputs/03-pressure/pressure"
    scenario["phase_prompt_sha256"]["pressure"] = hashlib.sha256(
        scenario["pressure_prompt"].encode()
    ).hexdigest()
    metadata["effective_red_repetitions"] = 3
    _write(campaign / "00-scenario.json", json.dumps(scenario, ensure_ascii=False).encode())
    _write(campaign / "06-run-metadata.json", json.dumps(metadata, ensure_ascii=False).encode())
    _write(campaign / "05-scorecards/red.json", finalizer._scorecard(scenario, "red", complete=False))
    _write(campaign / "05-scorecards/green-initial.json", finalizer._scorecard(scenario, "green-initial", complete=True))
    _write(campaign / "05-scorecards/green-final.json", finalizer._scorecard(scenario, "green-final", complete=False))
    phase, repetition = evidence._tc_generator_execution_order(
        evidence._effective_final_phase(scenario), evidence._effective_red_phase(scenario), 3,
        evidence._effective_green_initial_phase(scenario), evidence._effective_pressure_phase(scenario),
    )[successful_count]
    return finalizer, evidence, campaign, scenario, _terminal_draft(campaign, scenario, phase, repetition)


def test_finalizer_completes_shortened_prefix_and_preserves_authoritative_semantics(tmp_path: Path) -> None:
    finalizer, evidence, campaign, scenario, draft = _campaign(tmp_path)
    red_before = (campaign / "05-scorecards/red.json").read_bytes()
    initial_before = (campaign / "05-scorecards/green-initial.json").read_bytes()

    assert finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())]) == 0

    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    assert metadata["status"] == "complete"
    assert len(metadata["runs"]) == 14
    assert (campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json").is_file()
    assert (campaign / "05-scorecards/green-final.json").read_bytes() == finalizer._scorecard(scenario, "green-final", complete=True)
    assert (campaign / "05-scorecards/red.json").read_bytes() == red_before
    assert (campaign / "05-scorecards/green-initial.json").read_bytes() == initial_before
    assert load_tool("validate_artifact").validate(str(ROOT / "schemas/skill-test-evidence.schema.json"), str(metadata_path))[0] == 0
    scorecards = [_load(campaign / "05-scorecards" / f"{phase}.json") for phase in evidence.SCORECARD_PHASES]
    evidence._assert_metadata_semantics(metadata, campaign, scenario, scorecards)


def test_normal_recorder_still_refuses_terminal_key(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)

    with pytest.raises(finalizer.recorder.Refusal, match="atomic campaign completion"):
        finalizer.recorder.main(["record_successful_run.py", str(campaign.resolve()), str(draft.resolve())])


def test_finalizer_refuses_nonterminal_without_writes(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path, successful_count=12)
    before = {path: path.read_bytes() for path in (campaign / "06-run-metadata.json", campaign / "05-scorecards/green-final.json")}

    with pytest.raises(finalizer.Refusal, match="sole terminal"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in before} == before
    assert not (campaign / "artifacts/protocol/04-green-final/rep-04/run-protocol.json").exists()


@pytest.mark.parametrize("failure", ["protocol", "scorecard", "metadata"])
def test_write_failures_restore_pending_bytes_and_remove_new_protocol(tmp_path: Path, monkeypatch, failure: str) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    scorecard_path = campaign / "05-scorecards/green-final.json"
    protocol_path = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
    before = {metadata_path: metadata_path.read_bytes(), scorecard_path: scorecard_path.read_bytes()}
    replace = finalizer._atomic_replace
    create = finalizer._atomic_create

    if failure == "protocol":
        monkeypatch.setattr(finalizer, "_atomic_create", lambda path, content: (_ for _ in ()).throw(OSError("protocol failure")))
    else:
        def fail_selected(path: Path, content: bytes) -> None:
            if path == (scorecard_path if failure == "scorecard" else metadata_path):
                raise OSError(f"{failure} failure")
            replace(path, content)
        monkeypatch.setattr(finalizer, "_atomic_replace", fail_selected)

    with pytest.raises(OSError, match=f"{failure} failure"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in before} == before
    assert not protocol_path.exists()
    assert create is not None


def test_scorecard_or_protocol_drift_refuses_without_overwrite(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    scorecard_path = campaign / "05-scorecards/green-final.json"
    protocol_path = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
    _write(protocol_path, b"conflicting protocol")
    before = {metadata_path: metadata_path.read_bytes(), scorecard_path: scorecard_path.read_bytes(), protocol_path: protocol_path.read_bytes()}

    with pytest.raises(finalizer.Refusal, match="conflicts"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in before} == before


def test_scorecard_drift_refuses_without_writes(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    scorecard_path = campaign / "05-scorecards/green-final.json"
    scorecard = _load(scorecard_path)
    scorecard["all_passed"] = True
    _write(scorecard_path, json.dumps(scorecard, separators=(",", ":")).encode())
    before = {metadata_path: metadata_path.read_bytes(), scorecard_path: scorecard_path.read_bytes()}

    with pytest.raises(finalizer.Refusal, match="scorecard drifted"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in before} == before
    assert not (campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json").exists()


def test_validation_errors_refuse_before_writes(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    validation = campaign / "artifacts/outputs/04-green-final/rep-05/validation-result.json"
    _write(validation, json.dumps({"status": "valid", "errors": ["schema failure"]}).encode())
    paths = [campaign / "06-run-metadata.json", campaign / "05-scorecards/green-final.json", validation]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(finalizer.Refusal, match="validation or semantic result"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("artifact_sha256"),
        lambda payload: payload.update(artifact_sha256="0" * 64),
        lambda payload: payload.update(errors=["semantic failure"]),
    ],
)
def test_semantic_attestation_defects_refuse_before_writes(tmp_path: Path, mutate) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    semantic = campaign / "artifacts/outputs/04-green-final/rep-05/semantic-result.json"
    payload = _load(semantic)
    mutate(payload)
    _write(semantic, json.dumps(payload).encode())
    paths = [campaign / "06-run-metadata.json", campaign / "05-scorecards/green-final.json", semantic]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(finalizer.Refusal, match="validation or semantic result"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in paths} == before


def test_exact_partial_protocol_is_recovered_once(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    protocol_path, protocol_bytes, _metadata, _scorecard, _original, _complete, exists = finalizer._plan(
        campaign, _load(draft)
    )
    assert not exists
    _write(protocol_path, protocol_bytes)

    assert finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())]) == 0
    assert protocol_path.read_bytes() == protocol_bytes


def test_successful_completion_is_idempotent_without_byte_changes(tmp_path: Path) -> None:
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    assert finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())]) == 0
    paths = [campaign / "06-run-metadata.json", campaign / "05-scorecards/green-final.json", campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"]
    before = {path: path.read_bytes() for path in paths}

    assert finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())]) == 0

    assert {path: path.read_bytes() for path in paths} == before


def test_completed_active_key_reserved_by_distinct_invalidated_evidence_refuses_without_writes(tmp_path: Path) -> None:
    finalizer, campaign, draft = _complete_campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    invalidated_prompt = campaign / "artifacts/invalidated/04-green-final/rep-05/prompt.txt"
    _write(invalidated_prompt, b"separate invalidated evidence\n")
    metadata["invalidated_attempts"] = [{
        "phase": "04-green-final",
        "repetition": "rep-05",
        "prompt_snapshot": {"path": invalidated_prompt.relative_to(campaign).as_posix(), "sha256": hashlib.sha256(invalidated_prompt.read_bytes()).hexdigest()},
    }]
    _write(metadata_path, json.dumps(metadata).encode())
    paths = [metadata_path, draft, campaign / "05-scorecards/green-final.json", campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(finalizer.Refusal, match="reserved"):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda metadata: metadata.update(artifact_type="other"), "Refusal"),
        (lambda metadata: metadata.update(skill_id="other"), "Refusal"),
        (lambda metadata: metadata.update(unexpected=True), "InvocationError"),
        (lambda metadata: metadata.pop("host"), "InvocationError"),
        (lambda metadata: metadata.update(effective_red_repetitions=5), "Refusal"),
        (lambda metadata: metadata.pop("effective_red_repetitions"), "Refusal"),
    ],
)
def test_completed_metadata_top_level_drift_refuses_without_writes(tmp_path: Path, mutate, error: str) -> None:
    finalizer, campaign, draft = _complete_campaign(tmp_path)
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    mutate(metadata)
    _write(metadata_path, json.dumps(metadata).encode())
    paths = [metadata_path, draft, campaign / "05-scorecards/green-final.json", campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(getattr(finalizer, error)):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in paths} == before


def _complete_campaign(tmp_path: Path):
    finalizer, _evidence, campaign, _scenario, draft = _campaign(tmp_path)
    assert finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())]) == 0
    return finalizer, campaign, draft


def _sync_terminal_output_hash(campaign: Path, path: Path) -> None:
    metadata_path = campaign / "06-run-metadata.json"
    metadata = _load(metadata_path)
    relative = path.relative_to(campaign).as_posix()
    output = next(item for item in metadata["runs"][-1]["outputs"] if item["path"] == relative)
    output["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write(metadata_path, json.dumps(metadata, ensure_ascii=False).encode())


@pytest.mark.parametrize("target", ["draft", "protocol", "output", "validation", "semantic", "metadata"])
def test_already_complete_tampering_refuses_without_new_writes(tmp_path: Path, target: str) -> None:
    finalizer, campaign, draft = _complete_campaign(tmp_path)
    protocol = campaign / "artifacts/protocol/04-green-final/rep-05/run-protocol.json"
    output = campaign / "artifacts/outputs/04-green-final/rep-05/tc-generator-output.json"
    validation = campaign / "artifacts/outputs/04-green-final/rep-05/validation-result.json"
    semantic = campaign / "artifacts/outputs/04-green-final/rep-05/semantic-result.json"
    metadata = campaign / "06-run-metadata.json"
    if target == "draft":
        payload = _load(draft)
        payload["task"] = "different task"
        _write(draft, json.dumps(payload).encode())
    elif target == "protocol":
        payload = _load(protocol)
        payload["task"] = "different task"
        _write(protocol, json.dumps(payload).encode())
    elif target == "output":
        _write(output, b"tampered output\n")
    elif target == "validation":
        _write(validation, json.dumps({"status": "valid", "errors": ["schema failure"]}).encode())
        _sync_terminal_output_hash(campaign, validation)
    elif target == "semantic":
        payload = _load(semantic)
        payload["errors"] = ["semantic failure"]
        _write(semantic, json.dumps(payload).encode())
        _sync_terminal_output_hash(campaign, semantic)
    else:
        payload = _load(metadata)
        payload["runs"][-1]["skill_present"] = False
        _write(metadata, json.dumps(payload).encode())
    paths = [draft, protocol, output, validation, semantic, metadata, campaign / "05-scorecards/green-final.json"]
    before = {path: path.read_bytes() for path in paths}

    with pytest.raises(finalizer.Refusal):
        finalizer.main(["finalize_campaign.py", str(campaign.resolve()), str(draft.resolve())])

    assert {path: path.read_bytes() for path in paths} == before
