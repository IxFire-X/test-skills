import copy
import hashlib
import json
import re
from datetime import datetime, timezone
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
PRESSURE_OUTPUT_PATH = "artifacts/outputs/03-pressure/pressure"
UTC_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_output_paths():
    return [f"artifacts/outputs/{phase}/{rep}" for phase in PHASES for rep in REPETITIONS]


def _assert_schema_valid(validate_artifact, schema_path, document_path):
    assert validate_artifact.validate(str(schema_path), str(document_path))[0] == 0


def _score_evidence_path(phase, repetition):
    return f"{SCORECARD_OUTPUT_PHASES[phase]}/{repetition}.md"


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
        _score_evidence_path(scorecard["phase"], repetition)
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


def _parse_utc_z(timestamp):
    assert UTC_Z_PATTERN.fullmatch(timestamp)
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssertionError(f"invalid UTC timestamp: {timestamp}") from error
    assert value.tzinfo == timezone.utc
    return value


def _assert_metadata_semantics(metadata, campaign, scenario, scorecards):
    assert metadata["skill_id"] == scenario["skill_id"]
    for scorecard in scorecards:
        assert scorecard["skill_id"] == scenario["skill_id"]
        assert scorecard["rubric_ids"] == scenario["rubric_ids"]
        if scorecard["status"] == "complete":
            assert all(
                set(result) == set(scenario["rubric_ids"])
                for result in scorecard["results"].values()
            )
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
    campaign_root = campaign.resolve()
    output_paths = set()
    for run in metadata["runs"]:
        phase, repetition = run["phase"], run["repetition"]
        assert run["raw_input_allowlist"] == scenario["raw_input_allowlist"]
        started_at = _parse_utc_z(run["started_at"])
        finished_at = _parse_utc_z(run["finished_at"])
        assert finished_at >= started_at
        if phase == "pressure":
            assert repetition == "pressure"
            assert run["skill_present"] is True
            assert run["prompt_sha256"] == scenario["prompt_sha256"]["pressure"]
            required_output = "03-pressure.md"
            output_prefix = "artifacts/outputs/03-pressure/pressure/"
        else:
            assert repetition in REPETITIONS
            assert run["skill_present"] is (phase != "01-red-control")
            assert run["prompt_sha256"] == scenario["prompt_sha256"]["canonical"]
            required_output = f"{phase}/{repetition}.md"
            output_prefix = f"artifacts/outputs/{phase}/{repetition}/"
        assert run["outputs"]
        assert run["commands"]
        assert len({output["path"] for output in run["outputs"]}) == len(run["outputs"])
        assert len({command["id"] for command in run["commands"]}) == len(run["commands"])
        for output in run["outputs"]:
            output_path = Path(output["path"])
            assert not output_path.is_absolute()
            assert ".." not in output_path.parts
            assert all(not part.startswith(".") for part in output_path.parts)
            assert output["path"] == required_output or output["path"].startswith(output_prefix or "")
            resolved_output = (campaign / output_path).resolve()
            assert resolved_output.is_relative_to(campaign_root)
            assert resolved_output.is_file()
            assert hashlib.sha256(resolved_output.read_bytes()).hexdigest() == output["sha256"]
            assert resolved_output not in output_paths
            output_paths.add(resolved_output)
        assert required_output in {output["path"] for output in run["outputs"]}
        companion_prefix = (
            f"{scenario['pressure_output_path']}/"
            if phase == "pressure"
            else f"artifacts/outputs/{phase}/{repetition}/"
        )
        assert any(output["path"].startswith(companion_prefix) for output in run["outputs"])

    metadata_output_paths = {str(path.relative_to(campaign_root)).replace("\\", "/") for path in output_paths}
    for scorecard in scorecards:
        _assert_scorecard_semantics(scorecard)
        if scorecard["status"] == "complete":
            for evidence_file in scorecard["evidence_files"]:
                resolved_evidence = (campaign / evidence_file).resolve()
                assert resolved_evidence.is_relative_to(campaign_root)
                assert resolved_evidence.is_file()
                assert evidence_file in metadata_output_paths


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
            _score_evidence_path(phase, repetition)
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
                    "outputs": [{"path": f"{phase}/{repetition}.md", "sha256": "a" * 64}],
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
            "outputs": [{"path": "03-pressure.md", "sha256": "b" * 64}],
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


@pytest.fixture
def complete_campaign(tmp_path, root):
    """A complete campaign whose evidence files and digest records are independently real."""
    campaign = tmp_path / "context-marker"
    scenario = _read_json(root / "docs/to_do/skill-tests/context-marker/00-scenario.json")
    scorecards = [_complete_scorecard(scenario, phase) for phase in SCORECARD_PHASES]
    metadata = _complete_metadata(scenario)
    for run in metadata["runs"]:
        phase, repetition = run["phase"], run["repetition"]
        if phase == "pressure":
            relative_path = "03-pressure.md"
        else:
            relative_path = f"{phase}/{repetition}.md"
        content = f"{phase} {repetition} evidence\n".encode()
        evidence_path = campaign / relative_path
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(content)
        companion_path = (
            f"{scenario['pressure_output_path']}/companion.json"
            if phase == "pressure"
            else f"artifacts/outputs/{phase}/{repetition}/companion.json"
        )
        companion_content = f"{phase} {repetition} companion\n".encode()
        companion_file = campaign / companion_path
        companion_file.parent.mkdir(parents=True, exist_ok=True)
        companion_file.write_bytes(companion_content)
        run["outputs"] = [
            {"path": relative_path, "sha256": hashlib.sha256(content).hexdigest()},
            {"path": companion_path, "sha256": hashlib.sha256(companion_content).hexdigest()},
        ]
    return campaign, scenario, scorecards, metadata


def _assert_schema_instance_valid(validate_artifact, schema_path, document):
    validator = validate_artifact.Draft202012Validator(_read_json(schema_path))
    assert not list(validator.iter_errors(document))


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
        assert scenario["pressure_output_path"] == PRESSURE_OUTPUT_PATH
        assert len(set(scenario["output_paths"])) == len(scenario["output_paths"])
        assert all(not Path(output_path).is_absolute() and ".." not in Path(output_path).parts for output_path in scenario["output_paths"])
        assert hashlib.sha256(scenario["canonical_prompt"].encode("utf-8")).hexdigest() == scenario["prompt_sha256"]["canonical"]
        assert hashlib.sha256(scenario["pressure_prompt"].encode("utf-8")).hexdigest() == scenario["prompt_sha256"]["pressure"]
        _assert_schema_valid(validate_artifact, schema_path, scenario_path)
        _assert_schema_valid(validate_artifact, schema_path, metadata_path)
        metadata = _read_json(metadata_path)

        for phase in PHASES:
            for repetition in REPETITIONS:
                assert (campaign / "artifacts/outputs" / phase / repetition / ".gitkeep").is_file()
        assert (campaign / PRESSURE_OUTPUT_PATH / ".gitkeep").is_file()
        scorecards = []
        for phase in SCORECARD_PHASES:
            scorecard_path = campaign / "05-scorecards" / f"{phase}.json"
            assert scorecard_path.is_file()
            _assert_schema_valid(validate_artifact, schema_path, scorecard_path)
            scorecard = _read_json(scorecard_path)
            _assert_scorecard_semantics(scorecard)
            scorecards.append(scorecard)
        assert len({scorecard["phase"] for scorecard in scorecards}) == 3
        _assert_metadata_semantics(metadata, campaign, scenario, scorecards)
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
def test_run_metadata_semantics_reject_bypass_fixtures(complete_campaign, name, mutate):
    """Catches aggregate metadata that omits, aliases, or weakens individual campaign runs."""
    campaign, scenario, scorecards, metadata = complete_campaign
    mutate(metadata)
    with pytest.raises(AssertionError):
        _assert_metadata_semantics(metadata, campaign, scenario, scorecards)


def test_complete_campaign_evidence_is_schema_valid_and_matches_real_files(root, complete_campaign):
    """Catches completed evidence whose declared files or digests do not match disk."""
    campaign, scenario, scorecards, metadata = complete_campaign
    schema_path = root / "schemas/skill-test-evidence.schema.json"
    validate_artifact = load_tool("validate_artifact")
    for document in [scenario, *scorecards, metadata]:
        _assert_schema_instance_valid(validate_artifact, schema_path, document)
    _assert_metadata_semantics(metadata, campaign, scenario, scorecards)


def _remove_first_output(campaign, scenario, scorecards, metadata):
    (campaign / metadata["runs"][0]["outputs"][0]["path"]).unlink()


def _tamper_first_output(campaign, scenario, scorecards, metadata):
    (campaign / metadata["runs"][0]["outputs"][0]["path"]).write_text("tampered\n", encoding="utf-8")


def _remove_first_score_evidence(campaign, scenario, scorecards, metadata):
    (campaign / scorecards[0]["evidence_files"][0]).unlink()


def _unlink_scorecard_from_metadata(campaign, scenario, scorecards, metadata):
    replacement_path = "artifacts/outputs/01-red-control/rep-01/other.json"
    replacement = campaign / replacement_path
    content = b"replacement output\n"
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(content)
    metadata["runs"][0]["outputs"] = [
        {"path": replacement_path, "sha256": hashlib.sha256(content).hexdigest()}
    ]


def _duplicate_output_path(campaign, scenario, scorecards, metadata):
    metadata["runs"][-1]["outputs"] = [copy.deepcopy(metadata["runs"][0]["outputs"][0])]


def _invalid_calendar_timestamp(campaign, scenario, scorecards, metadata):
    metadata["runs"][0]["started_at"] = "2026-02-30T12:00:00Z"


def _reverse_timestamps(campaign, scenario, scorecards, metadata):
    metadata["runs"][0]["finished_at"] = "2026-08-07T11:59:59Z"


def _duplicate_command_id(campaign, scenario, scorecards, metadata):
    metadata["runs"][0]["commands"].append({"id": "evaluate", "exit_code": 1})


def _remove_canonical_companion(campaign, scenario, scorecards, metadata):
    metadata["runs"][0]["outputs"] = metadata["runs"][0]["outputs"][:1]


def _remove_pressure_companion(campaign, scenario, scorecards, metadata):
    metadata["runs"][-1]["outputs"] = metadata["runs"][-1]["outputs"][:1]


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("missing referenced output", _remove_first_output),
        ("tampered output bytes", _tamper_first_output),
        ("missing score evidence", _remove_first_score_evidence),
        ("score evidence absent from metadata", _unlink_scorecard_from_metadata),
        ("duplicate path across runs", _duplicate_output_path),
        ("invalid calendar date", _invalid_calendar_timestamp),
        ("reversed timestamps", _reverse_timestamps),
        ("duplicate command ID", _duplicate_command_id),
        ("missing canonical companion", _remove_canonical_companion),
        ("missing pressure companion", _remove_pressure_companion),
    ],
)
def test_complete_campaign_evidence_rejects_file_backed_bypasses(complete_campaign, name, mutate):
    """Catches complete campaigns that cite missing, overwritten, or chronologically invalid evidence."""
    campaign, scenario, scorecards, metadata = complete_campaign
    mutate(campaign, scenario, scorecards, metadata)
    with pytest.raises(AssertionError):
        _assert_metadata_semantics(metadata, campaign, scenario, scorecards)


def _switch_metadata_skill(campaign, scenario, scorecards, metadata):
    metadata["skill_id"] = "tc-generator"


def _switch_scorecard_skills(campaign, scenario, scorecards, metadata):
    for scorecard in scorecards:
        scorecard["skill_id"] = "tc-generator"


def _switch_scorecard_rubrics(campaign, scenario, scorecards, metadata):
    replacement_rubrics = ["alternate-rubric"]
    for scorecard in scorecards:
        scorecard["rubric_ids"] = replacement_rubrics
        value = scorecard["phase"] != "red"
        scorecard["results"] = {
            repetition: {"alternate-rubric": value} for repetition in REPETITIONS
        }


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("metadata skill differs from scenario", _switch_metadata_skill),
        ("scorecard skills differ from scenario", _switch_scorecard_skills),
        ("scorecard rubrics differ from scenario", _switch_scorecard_rubrics),
    ],
)
def test_campaign_semantics_reject_documents_not_bound_to_scenario(complete_campaign, name, mutate):
    """Catches individually valid evidence documents that belong to another scenario identity."""
    campaign, scenario, scorecards, metadata = complete_campaign
    mutate(campaign, scenario, scorecards, metadata)
    with pytest.raises(AssertionError):
        _assert_metadata_semantics(metadata, campaign, scenario, scorecards)


def _replace_companions_with_placeholders(campaign, scenario, scorecards, metadata):
    for run in metadata["runs"]:
        companion = run["outputs"][1]
        placeholder = campaign / Path(companion["path"]).parent / ".gitkeep"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        placeholder.write_bytes(b"placeholder\n")
        companion.update(
            path=str(placeholder.relative_to(campaign)).replace("\\", "/"),
            sha256=hashlib.sha256(placeholder.read_bytes()).hexdigest(),
        )


def _replace_companion_with_hidden_segment(campaign, scenario, scorecards, metadata):
    run = metadata["runs"][0]["outputs"][1]
    hidden_file = campaign / "artifacts/outputs/01-red-control/rep-01/.reserved/result.json"
    hidden_file.parent.mkdir(parents=True, exist_ok=True)
    hidden_file.write_bytes(b"hidden\n")
    run.update(
        path=str(hidden_file.relative_to(campaign)).replace("\\", "/"),
        sha256=hashlib.sha256(hidden_file.read_bytes()).hexdigest(),
    )


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("placeholder companions", _replace_companions_with_placeholders),
        ("hidden companion segment", _replace_companion_with_hidden_segment),
    ],
)
def test_complete_campaign_evidence_rejects_reserved_placeholder_paths(complete_campaign, name, mutate):
    """Catches evidence records that point to tracked placeholders or hidden reserved paths."""
    campaign, scenario, scorecards, metadata = complete_campaign
    mutate(campaign, scenario, scorecards, metadata)
    with pytest.raises(AssertionError):
        _assert_metadata_semantics(metadata, campaign, scenario, scorecards)
