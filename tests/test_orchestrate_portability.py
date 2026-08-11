import json
import subprocess
import sys

from jsonschema import Draft202012Validator


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_orchestrate_package_has_only_portable_skill_resources(root):
    package = root / "skills" / "orchestrate"

    assert {path.name for path in package.iterdir()} == {"SKILL.md", "references", "assets"}
    assert (package / "references" / "orchestration-contract.md").is_file()
    assert {path.name for path in (package / "assets" / "orchestration-fixtures").iterdir()} == {
        "accepted-orchestrator-output.json",
        "accepted-trace-document.json",
        "not-runnable-orchestrator-output.json",
    }


def test_accepted_orchestration_fixture_passes_schema_and_trace_cross_check(root):
    fixture_root = root / "skills" / "orchestrate" / "assets" / "orchestration-fixtures"
    trace_path = fixture_root / "accepted-trace-document.json"
    orchestrator_path = fixture_root / "accepted-orchestrator-output.json"
    schema = _read_json(root / "schemas" / "orchestrator-output.schema.json")
    artifact = _read_json(orchestrator_path)

    assert list(Draft202012Validator(schema).iter_errors(artifact)) == []
    result = subprocess.run(
        [
            sys.executable,
            root / "tools" / "trace_check.py",
            trace_path,
            "--orchestrator-artifact",
            orchestrator_path,
            "--require-execution",
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_not_runnable_fixture_is_schema_valid_but_cannot_claim_acceptance(root):
    fixture = _read_json(
        root
        / "skills"
        / "orchestrate"
        / "assets"
        / "orchestration-fixtures"
        / "not-runnable-orchestrator-output.json"
    )
    schema = _read_json(root / "schemas" / "orchestrator-output.schema.json")

    assert list(Draft202012Validator(schema).iter_errors(fixture)) == []
    assert fixture["artifacts"]["run_tests_verdict"]["verdict"] == "NOT_RUNNABLE"
    assert fixture["artifacts"]["execution_evidence"] == []
    assert fixture["artifacts"]["trace_audit"]["verdict"] == "FAIL"
