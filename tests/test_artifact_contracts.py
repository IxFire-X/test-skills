"""Contract tests for versioned portable pipeline artifacts."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


def _load_validator_module():
    path = ROOT / "tools" / "validate_artifact.py"
    spec = importlib.util.spec_from_file_location("validate_artifact", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _envelope(stage: str, artifacts: dict) -> dict:
    return {
        "schema_version": "2.1.0",
        "stage": stage,
        "artifacts": artifacts,
        "warnings": [],
    }


@pytest.fixture
def valid_artifacts():
    requirement = {
        "id": "REQ-1",
        "text": "A customer can submit an order.",
        "provenance": ["requirements.md#order-submission"],
    }
    test_case = {"id": "TC-1", "requirement_ids": ["REQ-1"], "title": "Submit order"}
    test_method = {
        "id": "METHOD-1",
        "file_id": "FILE-1",
        "test_case_ids": ["TC-1"],
        "requirement_ids": ["REQ-1"],
        "name": "test_submit_order",
    }
    return {
        "context-marker-output.schema.json": _envelope(
            "context-marker",
            {
                "analytics_documentation": {"requirements": [requirement]},
                "source_code_and_diff": {"sources": ["src/orders.py"]},
            },
        ),
        "tc-generator-output.schema.json": _envelope(
            "tc-generator",
            {
                "requirements": [requirement],
                "test_cases": [test_case],
                "coverage": [{"requirement_id": "REQ-1", "test_case_ids": ["TC-1"]}],
            },
        ),
        "tc-reviewer-output.schema.json": _envelope(
            "tc-reviewer",
            {
                "validation_report": {"verdict": "ПРИНЯТО", "reviewed_test_case_ids": ["TC-1"]},
                "accepted_test_cases": [test_case],
                "corrected_test_cases": [],
            },
        ),
        "tc-to-autotest-output.schema.json": _envelope(
            "tc-to-autotest",
            {
                "automation_matrix": [
                    {"test_case_id": "TC-1", "generated_file_ids": ["FILE-1"], "generated_method_ids": ["METHOD-1"]}
                ],
                "generated_test_files": [{"id": "FILE-1", "path": "tests/test_orders.py"}],
                "generated_test_methods": [test_method],
            },
        ),
        "autotest-reviewer-output.schema.json": _envelope(
            "autotest-reviewer",
            {
                "autotest_review": {"verdict": "ПРИНЯТО", "reviewed_method_ids": ["METHOD-1"]},
                "accepted_test_methods": [test_method],
                "corrected_test_methods": [],
            },
        ),
        "orchestrator-output.schema.json": _envelope(
            "orchestrate",
            {
                "run_tests_verdict": {"verdict": "PASS"},
                "execution_evidence": [{"method_id": "METHOD-1", "verdict": "PASS", "run_id": "RUN-1"}],
                "trace_audit": {"verdict": "PASS", "mappings": [{"requirement_id": "REQ-1", "test_case_id": "TC-1", "method_id": "METHOD-1", "evidence_ids": ["RUN-1"]}]},
            },
        ),
    }


@pytest.mark.parametrize("schema_path", sorted(SCHEMA_DIR.glob("*.schema.json")))
def test_schema_is_valid_draft_2020_12(schema_path):
    """Catches schema files that are not valid Draft 2020-12 schemas."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("schema_name", [
    "context-marker-output.schema.json",
    "tc-generator-output.schema.json",
    "tc-reviewer-output.schema.json",
    "tc-to-autotest-output.schema.json",
    "autotest-reviewer-output.schema.json",
    "orchestrator-output.schema.json",
])
def test_stage_artifact_uses_versioned_envelope(schema_name, valid_artifacts):
    """Catches a stage schema that still accepts its legacy top-level output."""
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    assert not list(Draft202012Validator(schema).iter_errors(valid_artifacts[schema_name]))


def test_tc_generator_requires_requirement_provenance(valid_artifacts):
    """Catches successful test-case generation without source requirement provenance."""
    schema = json.loads((SCHEMA_DIR / "tc-generator-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["requirements"][0]["provenance"] = []
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_tc_generator_requires_requirement_coverage(valid_artifacts):
    """Catches a successful generation claim without any requirement coverage mapping."""
    schema = json.loads((SCHEMA_DIR / "tc-generator-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["coverage"] = []
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_review_rejects_parallel_english_verdict(valid_artifacts):
    """Catches replacing the normative Russian review verdict with an English alias."""
    schema = json.loads((SCHEMA_DIR / "tc-reviewer-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["validation_report"]["verdict"] = "ACCEPTED"
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_automation_output_requires_generated_test_methods(valid_artifacts):
    """Catches an automation matrix that omits its required generated-method evidence."""
    schema = json.loads((SCHEMA_DIR / "tc-to-autotest-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["tc-to-autotest-output.schema.json"])
    del invalid["artifacts"]["generated_test_methods"]
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_orchestrator_requires_execution_evidence(valid_artifacts):
    """Catches a trace-audit success claim without per-method execution evidence."""
    schema = json.loads((SCHEMA_DIR / "orchestrator-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    del invalid["artifacts"]["execution_evidence"]
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_envelope_rejects_unknown_top_level_property(valid_artifacts):
    """Catches unstable top-level extension fields on stage artifacts."""
    schema = json.loads((SCHEMA_DIR / "context-marker-output.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(valid_artifacts["context-marker-output.schema.json"])
    invalid["legacy_summary"] = "unsupported"
    assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_validator_reports_json_pointer_for_missing_property(tmp_path, valid_artifacts):
    """Catches diagnostics that expose Python paths instead of RFC-6901 pointers."""
    validator = _load_validator_module()
    schema_path = SCHEMA_DIR / "tc-generator-output.schema.json"
    artifact = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    del artifact["artifacts"]["test_cases"][0]["id"]
    artifact_path = tmp_path / "invalid.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    exit_code, report = validator.validate(str(schema_path), str(artifact_path))

    assert exit_code == 1
    assert any(error["path"] == "/artifacts/test_cases/0/id" for error in report["errors"])


def test_validator_reports_schema_errors_as_exit_two(tmp_path):
    """Catches invalid schemas being misclassified as invalid user artifacts."""
    validator = _load_validator_module()
    schema_path = tmp_path / "invalid-schema.json"
    artifact_path = tmp_path / "artifact.json"
    schema_path.write_text('{"type": "not-a-json-schema-type"}', encoding="utf-8")
    artifact_path.write_text("{}", encoding="utf-8")

    exit_code, report = validator.validate(str(schema_path), str(artifact_path))

    assert exit_code == 2
    assert report["status"] == "error"
