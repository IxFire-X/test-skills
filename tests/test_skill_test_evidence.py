import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
from conftest import load_tool

SKILL_IDS = (
    "context-marker",
    "tc-generator",
    "tc-reviewer",
    "tc-to-autotest",
    "autotest-reviewer",
    "orchestrate",
)
PHASES = ("01-red-control", "02-green-initial", "04-green-final")
REPETITIONS = tuple(f"rep-{number:02d}" for number in range(1, 6))
SCORECARD_PHASES = ("red", "green-initial", "green-final")
SCORECARD_OUTPUT_PHASES = dict(zip(SCORECARD_PHASES, PHASES, strict=True))
UTC_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_output_paths():
    return [f"artifacts/outputs/{phase}/{rep}" for phase in PHASES for rep in REPETITIONS]


def _assert_schema_valid(validate_artifact, schema_path, document_path):
    assert validate_artifact.validate(str(schema_path), str(document_path))[0] == 0


def _assert_scorecard_semantics(scorecard):
    if scorecard["status"] == "pending":
        assert scorecard["results"] == {}
        assert scorecard["evidence_files"] == []
        assert scorecard["all_passed"] is False
        assert scorecard["no_gap"] is False
        assert scorecard["no_edit_reason"] is None
        return

    assert set(scorecard["results"]) == set(REPETITIONS)
    assert all(
        set(result) == set(scorecard["rubric_ids"])
        for result in scorecard["results"].values()
    )
    expected_evidence = {
        f"{SCORECARD_OUTPUT_PHASES[scorecard['phase']]}/{repetition}.md"
        for repetition in REPETITIONS
    }
    assert set(scorecard["evidence_files"]) == expected_evidence
    values = [
        value
        for result in scorecard["results"].values()
        for value in result.values()
    ]
    assert scorecard["all_passed"] is all(values)
    if scorecard["phase"] == "red":
        if False in values:
            assert scorecard["all_passed"] is False
            assert scorecard["no_gap"] is False
            assert scorecard["no_edit_reason"] is None
        else:
            assert scorecard["all_passed"] is True
            assert scorecard["no_gap"] is True
            assert isinstance(scorecard["no_edit_reason"], str)
            assert scorecard["no_edit_reason"].strip()
    else:
        assert all(values)
        assert scorecard["all_passed"] is True
        assert scorecard["no_gap"] is False
        assert scorecard["no_edit_reason"] is None


def _expected_run_keys():
    return {(phase, repetition) for phase in PHASES for repetition in REPETITIONS} | {
        ("pressure", "pressure")
    }


def _assert_metadata_semantics(metadata, scenario):
    if metadata["status"] == "pending":
        assert metadata["evaluator"] is None
        assert metadata["host"] is None
        assert metadata["model"] is None
        assert metadata["runs"] == []
        return

    assert all(metadata[field].strip() for field in ("evaluator", "host", "model"))
    assert metadata["fork_turns"] == "none"
    assert len(metadata["runs"]) == 16
    assert {(run["phase"], run["repetition"]) for run in metadata["runs"]} == _expected_run_keys()
    assert len({(run["phase"], run["repetition"]) for run in metadata["runs"]}) == 16
    for run in metadata["runs"]:
        phase, repetition = run["phase"], run["repetition"]
        assert run["raw_input_allowlist"] == scenario["raw_input_allowlist"]
        assert UTC_Z_PATTERN.fullmatch(run["started_at"])
        assert UTC_Z_PATTERN.fullmatch(run["finished_at"])
        if phase == "pressure":
            assert repetition == "pressure"
            assert run["skill_present"] is True
            assert run["prompt_sha256"] == scenario["prompt_sha256"]["pressure"]
            output_prefix = "artifacts/outputs/03-pressure/pressure/"
        else:
            assert repetition in REPETITIONS
            assert run["skill_present"] is (phase != "01-red-control")
            assert run["prompt_sha256"] == scenario["prompt_sha256"]["canonical"]
            output_prefix = f"artifacts/outputs/{phase}/{repetition}/"
        assert run["outputs"]
        assert run["commands"]
        assert len({output["path"] for output in run["outputs"]}) == len(run["outputs"])
        for output in run["outputs"]:
            output_path = Path(output["path"])
            assert not output_path.is_absolute()
            assert ".." not in output_path.parts
            assert output["path"].startswith(output_prefix)


def _complete_scorecard(scenario, phase="red"):
    result_value = phase != "red"
    return {
        "artifact_type": "scorecard",
        "skill_id": scenario["skill_id"],
        "phase": phase,
        "status": "complete",
        "rubric_ids": scenario["rubric_ids"],
        "results": {
            repetition: {rubric: result_value for rubric in scenario["rubric_ids"]}
            for repetition in REPETITIONS
        },
        "evidence_files": [
            f"{SCORECARD_OUTPUT_PHASES[phase]}/{repetition}.md"
            for repetition in REPETITIONS
        ],
        "all_passed": result_value,
        "no_gap": False,
        "no_edit_reason": None,
    }


def _complete_metadata(scenario):
    runs = []
    for phase in PHASES:
        for repetition in REPETITIONS:
            runs.append(
                {
                    "phase": phase,
                    "repetition": repetition,
                    "skill_present": phase != "01-red-control",
                    "prompt_sha256": scenario["prompt_sha256"]["canonical"],
                    "raw_input_allowlist": scenario["raw_input_allowlist"],
                    "started_at": "2026-08-07T12:00:00Z",
                    "finished_at": "2026-08-07T12:00:01Z",
                    "outputs": [{"path": f"artifacts/outputs/{phase}/{repetition}/result.md", "sha256": "a" * 64}],
                    "commands": [{"id": "evaluate", "exit_code": 0}],
                }
            )
    runs.append(
        {
            "phase": "pressure",
            "repetition": "pressure",
            "skill_present": True,
            "prompt_sha256": scenario["prompt_sha256"]["pressure"],
            "raw_input_allowlist": scenario["raw_input_allowlist"],
            "started_at": "2026-08-07T12:00:00Z",
            "finished_at": "2026-08-07T12:00:01Z",
            "outputs": [{"path": "artifacts/outputs/03-pressure/pressure/result.md", "sha256": "b" * 64}],
            "commands": [{"id": "evaluate-pressure", "exit_code": 0}],
        }
    )
    return {
        "artifact_type": "run-metadata",
        "skill_id": scenario["skill_id"],
        "status": "complete",
        "evaluator": "evidence-runner",
        "host": "ci",
        "model": "test-model",
        "fork_turns": "none",
        "runs": runs,
    }


def test_skill_test_scaffolds_are_complete_schema_valid_and_confined(root):
    """Catches missing immutable campaign scaffolds before any evaluator can write evidence."""
    contract = _read_json(root / "contracts/pipeline.json")
    assert tuple(contract["skill_files"]) == SKILL_IDS

    schema_path = root / "schemas/skill-test-evidence.schema.json"
    validate_artifact = load_tool("validate_artifact")
    campaign_root = root / "docs/to_do/skill-tests"
    assert campaign_root.is_dir()
    assert {path.name for path in campaign_root.iterdir() if path.is_dir()} == set(SKILL_IDS)

    for skill_id in SKILL_IDS:
        campaign = campaign_root / skill_id
        scenario_path = campaign / "00-scenario.json"
        pressure_path = campaign / "03-pressure.md"
        metadata_path = campaign / "06-run-metadata.json"
        assert scenario_path.is_file()
        assert pressure_path.is_file()
        assert metadata_path.is_file()
        assert (campaign / "artifacts/inputs/.gitkeep").is_file()
        scenario = _read_json(scenario_path)
        assert scenario["skill_id"] == skill_id
        assert scenario["repetitions"] == 5
        assert scenario["fork_turns"] == "none"
        assert scenario["output_paths"] == _expected_output_paths()
        assert len(set(scenario["output_paths"])) == len(scenario["output_paths"])
        assert all(not Path(output_path).is_absolute() and ".." not in Path(output_path).parts for output_path in scenario["output_paths"])
        assert hashlib.sha256(scenario["canonical_prompt"].encode("utf-8")).hexdigest() == scenario["prompt_sha256"]["canonical"]
        assert hashlib.sha256(scenario["pressure_prompt"].encode("utf-8")).hexdigest() == scenario["prompt_sha256"]["pressure"]
        _assert_schema_valid(validate_artifact, schema_path, scenario_path)
        _assert_schema_valid(validate_artifact, schema_path, metadata_path)
        _assert_metadata_semantics(_read_json(metadata_path), scenario)

        for phase in PHASES:
            for repetition in REPETITIONS:
                assert (campaign / "artifacts/outputs" / phase / repetition / ".gitkeep").is_file()
        scorecards = []
        for phase in SCORECARD_PHASES:
            scorecard_path = campaign / "05-scorecards" / f"{phase}.json"
            assert scorecard_path.is_file()
            _assert_schema_valid(validate_artifact, schema_path, scorecard_path)
            scorecard = _read_json(scorecard_path)
            _assert_scorecard_semantics(scorecard)
            scorecards.append(scorecard)
        assert len({scorecard["phase"] for scorecard in scorecards}) == 3
        assert campaign.resolve().is_relative_to((root / "docs/to_do").resolve())


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "red contradiction",
            lambda scorecard: scorecard.update(all_passed=True, no_gap=True, no_edit_reason="claims no gap"),
        ),
        (
            "green gap claim",
            lambda scorecard: scorecard.update(no_gap=True, no_edit_reason="not allowed"),
        ),
        (
            "cross phase evidence",
            lambda scorecard: scorecard["evidence_files"].__setitem__(0, "01-red-control/rep-01.md"),
        ),
    ],
)
def test_scorecard_semantics_reject_bypass_fixtures(root, name, mutate):
    """Catches scorecards that misreport results or borrow another phase's evidence."""
    scenario = _read_json(root / "docs/to_do/skill-tests/context-marker/00-scenario.json")
    phase = "green-initial" if name in {"green gap claim", "cross phase evidence"} else "red"
    scorecard = _complete_scorecard(scenario, phase)
    mutate(scorecard)
    with pytest.raises(AssertionError):
        _assert_scorecard_semantics(scorecard)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "allowlist escape",
            lambda metadata: metadata["runs"][0].update(raw_input_allowlist=["artifacts/inputs/../escape"]),
        ),
        (
            "wrong prompt digest",
            lambda metadata: metadata["runs"][0].update(prompt_sha256="0" * 64),
        ),
        ("missing run", lambda metadata: metadata["runs"].pop()),
        ("duplicate run", lambda metadata: metadata["runs"].__setitem__(1, copy.deepcopy(metadata["runs"][0]))),
        (
            "wrong red skill presence",
            lambda metadata: metadata["runs"][0].update(skill_present=True),
        ),
        (
            "missing per-run outputs and commands",
            lambda metadata: metadata["runs"][0].update(outputs=[], commands=[]),
        ),
        (
            "timestamp with offset",
            lambda metadata: metadata["runs"][0].update(started_at="2026-08-07T17:00:00+05:00"),
        ),
    ],
)
def test_run_metadata_semantics_reject_bypass_fixtures(root, name, mutate):
    """Catches aggregate metadata that omits, aliases, or weakens individual campaign runs."""
    scenario = _read_json(root / "docs/to_do/skill-tests/context-marker/00-scenario.json")
    metadata = _complete_metadata(scenario)
    mutate(metadata)
    with pytest.raises(AssertionError):
        _assert_metadata_semantics(metadata, scenario)
