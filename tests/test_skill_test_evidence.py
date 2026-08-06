import hashlib
import json
from pathlib import Path

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


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_output_paths():
    return [f"artifacts/outputs/{phase}/{rep}" for phase in PHASES for rep in REPETITIONS]


def _assert_scorecard_semantics(scorecard):
    if scorecard["status"] == "pending":
        assert scorecard["results"] == {}
        assert scorecard["evidence_files"] == []
        assert scorecard["all_passed"] is False
        assert scorecard["no_gap"] is False
        assert scorecard["no_edit_reason"] is None
        return

    assert set(scorecard["results"]) == set(REPETITIONS)
    assert len(scorecard["evidence_files"]) == 5
    assert all(set(result) == set(scorecard["rubric_ids"]) for result in scorecard["results"].values())
    values = [value for result in scorecard["results"].values() for value in result.values()]
    if scorecard["phase"] == "red":
        assert False in values or (scorecard["no_gap"] and scorecard["no_edit_reason"])
    else:
        assert all(values)
        assert scorecard["all_passed"] is True


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
        assert validate_artifact.validate(str(schema_path), str(scenario_path))[0] == 0
        assert validate_artifact.validate(str(schema_path), str(metadata_path))[0] == 0

        for phase in PHASES:
            for repetition in REPETITIONS:
                assert (campaign / "artifacts/outputs" / phase / repetition / ".gitkeep").is_file()
        scorecards = []
        for phase in SCORECARD_PHASES:
            scorecard_path = campaign / "05-scorecards" / f"{phase}.json"
            assert scorecard_path.is_file()
            assert validate_artifact.validate(str(schema_path), str(scorecard_path))[0] == 0
            scorecard = _read_json(scorecard_path)
            _assert_scorecard_semantics(scorecard)
            scorecards.append(scorecard)
        assert len({scorecard["phase"] for scorecard in scorecards}) == 3
        assert campaign.resolve().is_relative_to((root / "docs/to_do").resolve())
