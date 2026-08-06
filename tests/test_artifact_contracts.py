"""Contract tests for versioned portable pipeline artifacts."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"
VALIDATOR_PATH = ROOT / "tools" / "validate_artifact.py"


def _load_validator_module():
    spec = importlib.util.spec_from_file_location("validate_artifact", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _errors(schema_name: str, artifact: dict) -> list:
    return list(Draft202012Validator(_schema(schema_name)).iter_errors(artifact))


def _envelope(stage: str, artifacts: dict) -> dict:
    return {"schema_version": "2.1.0", "stage": stage, "artifacts": artifacts, "warnings": []}


@pytest.fixture
def valid_artifacts():
    requirement = {"id": "REQ-1", "text": "Клиент оформляет заказ.", "provenance": ["requirements.md#order"]}
    test_case = {
        "id": "TC-1", "requirement_ids": ["REQ-1"], "title": "Оформление заказа",
        "categories": ["positive", "functional"], "priority": "HIGH",
        "preconditions": [], "test_data": [],
        "steps": [{"order": 1, "action": "Отправить заказ", "expected_result": "Заказ принят"}],
        "expected_outcome": "Заказ создан с идентификатором.",
    }
    finding = {"severity": "BLOCKING", "code": "ASSERTION_MISSING", "message": "Нет проверки статуса", "evidence": ["TC-2 step 1"], "related_ids": ["TC-2"]}
    correction = {"id": "FIX-1", "related_ids": ["TC-1"], "description": "Добавлена проверка статуса", "evidence": ["diff:1"]}
    test_method = {"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_submit_order", "content_digest": "sha256:abc"}
    return {
        "context-marker-output.schema.json": _envelope("context-marker", {"analytics_documentation": {"requirements": [requirement]}, "source_code_and_diff": {"sources": ["src/orders.py"]}}),
        "tc-generator-output.schema.json": _envelope("tc-generator", {"generated_test_cases": {"requirements": [requirement], "test_cases": [test_case], "coverage": [{"requirement_id": "REQ-1", "test_case_ids": ["TC-1"]}]}}),
        "tc-reviewer-output.schema.json": _envelope("tc-reviewer", {"validation_report": {"verdict": "ПРИНЯТО", "reviewed_test_case_ids": ["TC-1"], "findings": [], "corrections": []}, "corrected_test_cases": []}),
        "tc-to-autotest-output.schema.json": _envelope("tc-to-autotest", {"automation_matrix": [{"test_case_id": "TC-1", "generated_file_ids": ["FILE-1"], "generated_method_ids": ["METHOD-1"]}], "generated_test_files": [{"id": "FILE-1", "path": "tests/test_orders.py", "language": "python", "framework": "pytest", "content_digest": "sha256:def"}], "generated_test_methods": [test_method]}),
        "autotest-reviewer-output.schema.json": _envelope("autotest-reviewer", {"autotest_review": {"verdict": "ПРИНЯТО", "reviewed_file_ids": ["FILE-1"], "reviewed_method_ids": ["METHOD-1"], "findings": [], "corrections": []}}),
        "orchestrator-output.schema.json": _envelope("orchestrate", {"run_tests_verdict": {"verdict": "PASS", "reason": "All tests passed", "command": "pytest", "runner": "pytest", "exit_code": 0}, "execution_evidence": [{"method_id": "METHOD-1", "verdict": "PASS", "run_id": "RUN-1"}], "trace_audit": {"verdict": "PASS", "mappings": [{"requirement_id": "REQ-1", "test_case_id": "TC-1", "method_id": "METHOD-1", "evidence_ids": ["RUN-1"]}], "errors": []}}),
        "finding": finding,
        "correction": correction,
        "test_case": test_case,
    }


@pytest.mark.parametrize("schema_path", sorted(SCHEMA_DIR.glob("*.schema.json")))
def test_schema_is_valid_draft_2020_12(schema_path):
    """Catches schema files that are not valid Draft 2020-12 schemas."""
    Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("schema_name", ["context-marker-output.schema.json", "tc-generator-output.schema.json", "tc-reviewer-output.schema.json", "tc-to-autotest-output.schema.json", "autotest-reviewer-output.schema.json", "orchestrator-output.schema.json"])
def test_stage_artifact_uses_versioned_envelope(schema_name, valid_artifacts):
    """Catches a stage schema that rejects its complete versioned envelope."""
    assert not _errors(schema_name, valid_artifacts[schema_name])


def test_tc_generator_uses_registered_generated_cases_key(valid_artifacts):
    """Catches a tc-generator payload split into unregistered top-level artifacts."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    payload = invalid["artifacts"].pop("generated_test_cases")
    invalid["artifacts"].update(payload)
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_rejects_title_only_case(valid_artifacts):
    """Catches manual cases that have identity/title but no executable specification."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"] = [{"id": "TC-1", "requirement_ids": ["REQ-1"], "title": "Only a title"}]
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_requires_nonempty_category(valid_artifacts):
    """Catches a manual case without an operational category."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["categories"] = []
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_requires_explicit_preconditions_field(valid_artifacts):
    """Catches omitted preconditions instead of an explicit empty list."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    del invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["preconditions"]
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_requires_explicit_test_data_field(valid_artifacts):
    """Catches omitted test data instead of an explicit empty list."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    del invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["test_data"]
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_requires_ordered_steps(valid_artifacts):
    """Catches a manual case with no operational actions."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["steps"] = []
    assert _errors("tc-generator-output.schema.json", invalid)


def test_manual_case_requires_nonempty_oracle(valid_artifacts):
    """Catches an executable-looking case without an outcome oracle."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["expected_outcome"] = ""
    assert _errors("tc-generator-output.schema.json", invalid)


def test_tc_reviewer_rejects_accepted_case_artifact(valid_artifacts):
    """Catches renaming forwarded input cases into an unregistered output artifact."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["accepted_test_cases"] = [valid_artifacts["test_case"]]
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_tc_reviewer_pass_rejects_corrected_case_leakage(valid_artifacts):
    """Catches corrected outputs leaking into a ПРИНЯТО branch."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_tc_reviewer_auto_fix_requires_correction_evidence(valid_artifacts):
    """Catches AUTO_FIX_APPLIED without an actionable correction record."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["validation_report"]["verdict"] = "AUTO_FIX_APPLIED"
    invalid["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_tc_reviewer_rework_requires_blocking_finding(valid_artifacts):
    """Catches ТРЕБУЕТ ДОРАБОТКИ without actionable blocking evidence."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["validation_report"]["verdict"] = "ТРЕБУЕТ ДОРАБОТКИ"
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_tc_reviewer_rework_rejects_correction_leakage(valid_artifacts):
    """Catches corrections leaking into a manual rework branch."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["validation_report"].update({"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "findings": [valid_artifacts["finding"]], "corrections": []})
    invalid["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_autotest_reviewer_rejects_unregistered_corrected_methods(valid_artifacts):
    """Catches renamed corrected-method artifacts outside pipeline.json."""
    invalid = copy.deepcopy(valid_artifacts["autotest-reviewer-output.schema.json"])
    invalid["artifacts"]["corrected_test_methods"] = []
    assert _errors("autotest-reviewer-output.schema.json", invalid)


def test_autotest_reviewer_auto_fix_requires_correction_evidence(valid_artifacts):
    """Catches automated review fixes without a structured change record."""
    invalid = copy.deepcopy(valid_artifacts["autotest-reviewer-output.schema.json"])
    invalid["artifacts"]["autotest_review"]["verdict"] = "AUTO_FIX_APPLIED"
    assert _errors("autotest-reviewer-output.schema.json", invalid)


def test_autotest_reviewer_rework_requires_blocking_finding(valid_artifacts):
    """Catches an autotest rework verdict without a blocking finding."""
    invalid = copy.deepcopy(valid_artifacts["autotest-reviewer-output.schema.json"])
    invalid["artifacts"]["autotest_review"]["verdict"] = "ТРЕБУЕТ ДОРАБОТКИ"
    assert _errors("autotest-reviewer-output.schema.json", invalid)


def test_automation_output_requires_generated_test_methods(valid_artifacts):
    """Catches an automation matrix that omits its required generated-method evidence."""
    invalid = copy.deepcopy(valid_artifacts["tc-to-autotest-output.schema.json"])
    del invalid["artifacts"]["generated_test_methods"]
    assert _errors("tc-to-autotest-output.schema.json", invalid)


def test_orchestrator_rejects_pass_with_failed_method(valid_artifacts):
    """Catches a contradictory PASS verdict with failed method evidence."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["execution_evidence"][0]["verdict"] = "FAIL"
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_rejects_pass_with_trace_failure(valid_artifacts):
    """Catches a contradictory PASS verdict with failed trace audit."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["trace_audit"]["verdict"] = "FAIL"
    invalid["artifacts"]["trace_audit"]["errors"] = ["trace mismatch"]
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_accepts_early_fail_without_method_evidence(valid_artifacts):
    """Allows compile/import failure before a generated method can execute."""
    valid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    valid["artifacts"].update({"run_tests_verdict": {"verdict": "FAIL", "reason": "ImportError", "command": "pytest", "runner": "pytest", "exit_code": 2}, "execution_evidence": [], "trace_audit": {"verdict": "FAIL", "mappings": [], "errors": ["ImportError"]}})
    assert not _errors("orchestrator-output.schema.json", valid)


def test_orchestrator_accepts_not_runnable_without_method_evidence(valid_artifacts):
    """Allows an honest unavailable-runner verdict with no execution mappings."""
    valid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    valid["artifacts"].update({"run_tests_verdict": {"verdict": "NOT_RUNNABLE", "reason": "pytest unavailable"}, "execution_evidence": [], "trace_audit": {"verdict": "FAIL", "mappings": [], "errors": ["runner unavailable"]}})
    assert not _errors("orchestrator-output.schema.json", valid)


def test_validator_reports_root_pointer_as_empty_string(tmp_path):
    """Catches root diagnostics encoded as slash instead of the RFC-6901 empty pointer."""
    validator = _load_validator_module()
    schema_path = tmp_path / "schema.json"
    artifact_path = tmp_path / "artifact.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    artifact_path.write_text("[]", encoding="utf-8")
    exit_code, report = validator.validate(str(schema_path), str(artifact_path))
    assert exit_code == 1
    assert report["errors"][0]["path"] == ""


def test_validator_validates_schema_before_missing_artifact(tmp_path):
    """Catches an absent artifact masking an invalid schema as the primary failure."""
    validator = _load_validator_module()
    schema_path = tmp_path / "invalid-schema.json"
    schema_path.write_text('{"type":"not-a-json-schema-type"}', encoding="utf-8")
    exit_code, report = validator.validate(str(schema_path), str(tmp_path / "missing.json"))
    assert exit_code == 2
    assert report["errors"][0]["message"].startswith("schema invalid:")


def test_validator_reports_invalid_utf8_input_as_exit_two(tmp_path):
    """Catches undecodable JSON files escaping the stable error protocol."""
    validator = _load_validator_module()
    schema_path = tmp_path / "schema.json"
    artifact_path = tmp_path / "artifact.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    artifact_path.write_bytes(b"\xff")
    exit_code, report = validator.validate(str(schema_path), str(artifact_path))
    assert exit_code == 2
    assert report["status"] == "error"


def test_validator_cli_missing_arguments_returns_json_and_exit_two():
    """Catches argparse usage text bypassing the JSON diagnostic contract."""
    completed = subprocess.run([sys.executable, str(VALIDATOR_PATH)], capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "error"


def test_validator_cli_preserves_non_ascii_json(tmp_path, valid_artifacts):
    """Catches locale-dependent CLI output for valid Unicode artifact content."""
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(valid_artifacts["tc-generator-output.schema.json"], ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(VALIDATOR_PATH), str(SCHEMA_DIR / "tc-generator-output.schema.json"), str(artifact_path)], capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"errors": [], "status": "valid"}
