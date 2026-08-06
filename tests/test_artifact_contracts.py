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
    digest = "sha256:" + "a" * 64
    test_method = {"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_submit_order", "content_digest": digest}
    return {
        "context-marker-output.schema.json": _envelope("context-marker", {"analytics_documentation": {"requirements": [requirement]}, "source_code_and_diff": {"sources": ["src/orders.py"]}}),
        "tc-generator-output.schema.json": _envelope("tc-generator", {"generated_test_cases": {"requirements": [requirement], "test_cases": [test_case], "coverage": [{"requirement_id": "REQ-1", "test_case_ids": ["TC-1"]}]}}),
        "tc-reviewer-output.schema.json": _envelope("tc-reviewer", {"validation_report": {"verdict": "ПРИНЯТО", "reviewed_test_case_ids": ["TC-1"], "findings": [], "corrections": []}, "corrected_test_cases": []}),
        "tc-to-autotest-output.schema.json": _envelope("tc-to-autotest", {"automation_matrix": [{"test_case_id": "TC-1", "generated_file_ids": ["FILE-1"], "generated_method_ids": ["METHOD-1"]}], "generated_test_files": [{"id": "FILE-1", "path": "tests/test_orders.py", "language": "python", "framework": "pytest", "content_digest": digest}], "generated_test_methods": [test_method]}),
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


def test_tc_reviewer_accepts_complete_auto_fix_case(valid_artifacts):
    """Catches AUTO_FIX_APPLIED outputs that cannot forward a usable corrected case."""
    valid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    valid["artifacts"]["validation_report"].update({"verdict": "AUTO_FIX_APPLIED", "corrections": [valid_artifacts["correction"]]})
    valid["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
    assert not _errors("tc-reviewer-output.schema.json", valid)


def test_tc_reviewer_rejects_id_only_corrected_case(valid_artifacts):
    """Catches correction branches that discard executable test-case details."""
    invalid = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    invalid["artifacts"]["validation_report"].update({"verdict": "AUTO_FIX_APPLIED", "corrections": [valid_artifacts["correction"]]})
    invalid["artifacts"]["corrected_test_cases"] = [{"id": "TC-1"}]
    assert _errors("tc-reviewer-output.schema.json", invalid)


def test_review_auto_fix_rejects_blocking_finding(valid_artifacts):
    """Catches AUTO_FIX_APPLIED coexisting with a blocking unresolved finding."""
    for schema_name, report_key in (("tc-reviewer-output.schema.json", "validation_report"), ("autotest-reviewer-output.schema.json", "autotest_review")):
        invalid = copy.deepcopy(valid_artifacts[schema_name])
        invalid["artifacts"][report_key].update({"verdict": "AUTO_FIX_APPLIED", "findings": [valid_artifacts["finding"]], "corrections": [valid_artifacts["correction"]]})
        if schema_name.startswith("tc-"):
            invalid["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
        assert _errors(schema_name, invalid)


def test_trace_mapping_rejects_partial_structure(valid_artifacts):
    """Catches trace audit mappings that provide no requirement-to-evidence chain."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["trace_audit"]["mappings"] = [{}]
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_rejects_pass_nonzero_exit(valid_artifacts):
    """Catches PASS verdicts that retain a failing process exit code."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["run_tests_verdict"]["exit_code"] = 1
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_rejects_fail_without_failed_evidence(valid_artifacts):
    """Catches FAIL execution evidence that only records passing methods."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["run_tests_verdict"].update({"verdict": "FAIL", "exit_code": 1})
    invalid["artifacts"]["trace_audit"].update({"verdict": "FAIL", "errors": ["failure"]})
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_rejects_not_runnable_metadata(valid_artifacts):
    """Catches NOT_RUNNABLE claims that carry a contradictory runner execution record."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"].update({"run_tests_verdict": {"verdict": "NOT_RUNNABLE", "reason": "missing runner", "command": "pytest"}, "execution_evidence": [], "trace_audit": {"verdict": "FAIL", "mappings": [], "errors": ["missing runner"]}})
    assert _errors("orchestrator-output.schema.json", invalid)


def test_generated_digest_requires_full_sha256(valid_artifacts):
    """Catches digest prefixes that cannot identify generated source content."""
    invalid = copy.deepcopy(valid_artifacts["tc-to-autotest-output.schema.json"])
    invalid["artifacts"]["generated_test_files"][0]["content_digest"] = "sha256:abc"
    assert _errors("tc-to-autotest-output.schema.json", invalid)


def test_generator_rejects_bare_tc_id(valid_artifacts):
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["id"] = "TC-"
    assert _errors("tc-generator-output.schema.json", invalid)


def test_generator_rejects_empty_requirement_id(valid_artifacts):
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["requirements"][0]["id"] = ""
    assert _errors("tc-generator-output.schema.json", invalid)


def test_generator_rejects_duplicate_requirement_ids(valid_artifacts):
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["test_cases"][0]["requirement_ids"] = ["REQ-1", "REQ-1"]
    assert _errors("tc-generator-output.schema.json", invalid)


def test_generator_rejects_empty_warning(valid_artifacts):
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["warnings"] = [""]
    assert _errors("tc-generator-output.schema.json", invalid)


def test_corrected_case_is_generator_compatible(valid_artifacts):
    case = valid_artifacts["test_case"]
    wrapper = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    wrapper["artifacts"]["generated_test_cases"]["test_cases"] = [case]
    assert not _errors("tc-generator-output.schema.json", wrapper)


def _tc_auto_fix_artifact(valid_artifacts):
    artifact = copy.deepcopy(valid_artifacts["tc-reviewer-output.schema.json"])
    artifact["artifacts"]["validation_report"].update(
        {"verdict": "AUTO_FIX_APPLIED", "corrections": [valid_artifacts["correction"]]}
    )
    artifact["artifacts"]["corrected_test_cases"] = [valid_artifacts["test_case"]]
    return artifact


def _autotest_auto_fix_artifact(valid_artifacts):
    artifact = copy.deepcopy(valid_artifacts["autotest-reviewer-output.schema.json"])
    artifact["artifacts"]["autotest_review"].update(
        {"verdict": "AUTO_FIX_APPLIED", "corrections": [valid_artifacts["correction"]]}
    )
    return artifact


def test_same_requirement_is_accepted_by_context_marker_and_generator(valid_artifacts):
    """Keeps the producer requirement object portable across its first two stages."""
    requirement = valid_artifacts["context-marker-output.schema.json"]["artifacts"][
        "analytics_documentation"
    ]["requirements"][0]
    generated = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    generated["artifacts"]["generated_test_cases"]["requirements"] = [requirement]
    assert not _errors("context-marker-output.schema.json", valid_artifacts["context-marker-output.schema.json"])
    assert not _errors("tc-generator-output.schema.json", generated)


@pytest.mark.parametrize(
    ("schema_name", "path"),
    [
        pytest.param(
            "context-marker-output.schema.json",
            ["artifacts", "analytics_documentation", "requirements", 0, "id"],
            id="context-marker-arbitrary-requirement-id",
        ),
        pytest.param(
            "tc-generator-output.schema.json",
            ["artifacts", "generated_test_cases", "requirements", 0, "id"],
            id="generator-bare-requirement-id",
        ),
    ],
)
def test_requirement_records_reject_nonportable_requirement_ids(valid_artifacts, schema_name, path):
    """Requirement records use the same meaningful REQ identifier across stages."""
    invalid = copy.deepcopy(valid_artifacts[schema_name])
    target = invalid
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "X" if schema_name.startswith("context") else "REQ-"
    assert _errors(schema_name, invalid)


def test_same_generator_case_is_accepted_by_reviewer_auto_fix(valid_artifacts):
    """Keeps a complete generator case usable unchanged in AUTO_FIX_APPLIED output."""
    reviewer = _tc_auto_fix_artifact(valid_artifacts)
    reviewer["artifacts"]["corrected_test_cases"] = [
        valid_artifacts["tc-generator-output.schema.json"]["artifacts"]["generated_test_cases"][
            "test_cases"
        ][0]
    ]
    assert not _errors("tc-generator-output.schema.json", valid_artifacts["tc-generator-output.schema.json"])
    assert not _errors("tc-reviewer-output.schema.json", reviewer)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        pytest.param("requirement_id", "", id="empty-requirement-id"),
        pytest.param("requirement_id", "REQ-", id="bare-requirement-id"),
        pytest.param("categories", ["positive", "positive"], id="duplicate-categories"),
        pytest.param("preconditions", [""], id="empty-precondition-item"),
        pytest.param("test_data", [""], id="empty-test-data-item"),
    ],
)
def test_shared_manual_case_mutation_is_rejected_by_generator_and_reviewer(
    valid_artifacts, mutation, value
):
    """Pins the manual-case contract shared by generator and reviewer."""
    generated = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    reviewer = _tc_auto_fix_artifact(valid_artifacts)
    generated_case = generated["artifacts"]["generated_test_cases"]["test_cases"][0]
    reviewed_case = reviewer["artifacts"]["corrected_test_cases"][0]
    if mutation == "requirement_id":
        generated_case["requirement_ids"] = [value]
        reviewed_case["requirement_ids"] = [value]
    else:
        generated_case[mutation] = value
        reviewed_case[mutation] = value
    assert _errors("tc-generator-output.schema.json", generated)
    assert _errors("tc-reviewer-output.schema.json", reviewer)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        pytest.param("requirement_id", "", id="coverage-empty-requirement-id"),
        pytest.param("requirement_id", "REQ-", id="coverage-bare-requirement-id"),
        pytest.param("test_case_ids", ["TC-"], id="coverage-bare-test-case-id"),
        pytest.param("test_case_ids", ["TC-1", "TC-1"], id="coverage-duplicate-test-case-id"),
    ],
)
def test_generator_rejects_invalid_coverage_references(valid_artifacts, mutation, value):
    """Rejects one malformed coverage reference at a time."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    invalid["artifacts"]["generated_test_cases"]["coverage"][0][mutation] = value
    assert _errors("tc-generator-output.schema.json", invalid)


@pytest.mark.parametrize("collection", ["requirements", "test_cases", "coverage"])
def test_generator_rejects_exact_duplicate_artifact_objects(valid_artifacts, collection):
    """Rejects duplicate generator records without imposing cross-array membership."""
    invalid = copy.deepcopy(valid_artifacts["tc-generator-output.schema.json"])
    records = invalid["artifacts"]["generated_test_cases"][collection]
    records.append(copy.deepcopy(records[0]))
    assert _errors("tc-generator-output.schema.json", invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key", "record_kind", "field", "value"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "evidence", [""], id="tc-finding-empty-evidence"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "evidence", ["line", "line"], id="tc-finding-duplicate-evidence"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "related_ids", [""], id="tc-finding-empty-related-id"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "related_ids", ["TC-1", "TC-1"], id="tc-finding-duplicate-related-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "evidence", [""], id="autotest-finding-empty-evidence"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "evidence", ["line", "line"], id="autotest-finding-duplicate-evidence"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "related_ids", [""], id="autotest-finding-empty-related-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "related_ids", ["METHOD-1", "METHOD-1"], id="autotest-finding-duplicate-related-id"),
    ],
)
def test_reviewers_reject_inactionable_finding_fields(
    valid_artifacts, schema_name, report_key, record_kind, field, value
):
    """Requires nonempty, de-duplicated evidence and references for each finding."""
    invalid = copy.deepcopy(valid_artifacts[schema_name])
    report = invalid["artifacts"][report_key]
    report.update({"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "findings": [valid_artifacts["finding"]]})
    report["findings"][0][field] = value
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key", "field", "value"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "evidence", [""], id="tc-correction-empty-evidence"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "evidence", ["diff", "diff"], id="tc-correction-duplicate-evidence"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "related_ids", [""], id="tc-correction-empty-related-id"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "related_ids", ["TC-1", "TC-1"], id="tc-correction-duplicate-related-id"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "id", "FIX-", id="tc-correction-bare-fix-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "evidence", [""], id="autotest-correction-empty-evidence"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "evidence", ["diff", "diff"], id="autotest-correction-duplicate-evidence"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "related_ids", [""], id="autotest-correction-empty-related-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "related_ids", ["METHOD-1", "METHOD-1"], id="autotest-correction-duplicate-related-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "id", "FIX-", id="autotest-correction-bare-fix-id"),
    ],
)
def test_reviewers_reject_inactionable_correction_fields(
    valid_artifacts, schema_name, report_key, field, value
):
    """Requires nonempty, de-duplicated evidence and references for each correction."""
    invalid = _tc_auto_fix_artifact(valid_artifacts) if schema_name.startswith("tc-") else _autotest_auto_fix_artifact(valid_artifacts)
    invalid["artifacts"][report_key]["corrections"][0][field] = value
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", id="tc-duplicate-fix"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", id="autotest-duplicate-fix"),
    ],
)
def test_reviewers_reject_exact_duplicate_corrections(valid_artifacts, schema_name, report_key):
    """Rejects an exact duplicate FIX record instead of reporting one change twice."""
    invalid = _tc_auto_fix_artifact(valid_artifacts) if schema_name.startswith("tc-") else _autotest_auto_fix_artifact(valid_artifacts)
    corrections = invalid["artifacts"][report_key]["corrections"]
    corrections.append(copy.deepcopy(corrections[0]))
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key", "record_kind", "field"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "code", id="tc-empty-finding-code"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "finding", "message", id="tc-empty-finding-message"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "correction", "description", id="tc-empty-correction-description"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "code", id="autotest-empty-finding-code"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "finding", "message", id="autotest-empty-finding-message"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "correction", "description", id="autotest-empty-correction-description"),
    ],
)
def test_reviewers_reject_empty_actionability_text(
    valid_artifacts, schema_name, report_key, record_kind, field
):
    """Keeps each actionable finding and correction explanation nonempty."""
    if record_kind == "finding":
        invalid = copy.deepcopy(valid_artifacts[schema_name])
        report = invalid["artifacts"][report_key]
        report.update({"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "findings": [valid_artifacts["finding"]]})
        report["findings"][0][field] = ""
    else:
        invalid = _tc_auto_fix_artifact(valid_artifacts) if schema_name.startswith("tc-") else _autotest_auto_fix_artifact(valid_artifacts)
        invalid["artifacts"][report_key]["corrections"][0][field] = ""
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key", "field", "value"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "reviewed_test_case_ids", ["TC-"], id="tc-bare-reviewed-case"),
        pytest.param("tc-reviewer-output.schema.json", "validation_report", "reviewed_test_case_ids", ["TC-1", "TC-1"], id="tc-duplicate-reviewed-case"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "reviewed_file_ids", ["FILE-"], id="autotest-bare-file-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "reviewed_file_ids", ["FILE-1", "FILE-1"], id="autotest-duplicate-file-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "reviewed_method_ids", ["METHOD-"], id="autotest-bare-method-id"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", "reviewed_method_ids", ["METHOD-1", "METHOD-1"], id="autotest-duplicate-method-id"),
    ],
)
def test_reviewers_reject_nonunique_or_bare_reviewed_ids(
    valid_artifacts, schema_name, report_key, field, value
):
    """Keeps review targets meaningful and one-to-one within every review record."""
    invalid = copy.deepcopy(valid_artifacts[schema_name])
    invalid["artifacts"][report_key][field] = value
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize(
    "schema_name",
    [
        "context-marker-output.schema.json",
        "tc-generator-output.schema.json",
        "tc-reviewer-output.schema.json",
        "tc-to-autotest-output.schema.json",
        "autotest-reviewer-output.schema.json",
        "orchestrator-output.schema.json",
    ],
)
def test_stage_rejects_one_empty_warning(valid_artifacts, schema_name):
    """Rejects a single empty warning for each independently versioned stage."""
    invalid = copy.deepcopy(valid_artifacts[schema_name])
    invalid["warnings"] = [""]
    assert _errors(schema_name, invalid)


@pytest.mark.parametrize("collection", ["execution_evidence", "mappings", "errors"])
def test_orchestrator_rejects_exact_duplicate_runtime_records(valid_artifacts, collection):
    """Rejects one exact duplicate where a runtime record would be ambiguous."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    target = invalid["artifacts"]["trace_audit"][collection] if collection != "execution_evidence" else invalid["artifacts"][collection]
    if collection == "errors":
        valid = copy.deepcopy(invalid)
        valid["artifacts"]["run_tests_verdict"].update({"verdict": "FAIL", "exit_code": 1})
        valid["artifacts"]["execution_evidence"] = []
        valid["artifacts"]["trace_audit"].update({"verdict": "FAIL", "mappings": [], "errors": ["failure"]})
        valid["artifacts"]["trace_audit"]["errors"].append("failure")
        assert _errors("orchestrator-output.schema.json", valid)
        return
    target.append(copy.deepcopy(target[0]))
    assert _errors("orchestrator-output.schema.json", invalid)


def test_orchestrator_rejects_extra_trace_mapping_field(valid_artifacts):
    """Keeps trace mappings closed to the contract-defined evidence chain."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"]["trace_audit"]["mappings"][0]["extra"] = "x"
    assert _errors("orchestrator-output.schema.json", invalid)


@pytest.mark.parametrize(
    ("verdict", "field", "value"),
    [
        pytest.param("PASS", "command", "", id="pass-empty-command"),
        pytest.param("PASS", "runner", "", id="pass-empty-runner"),
        pytest.param("FAIL", "exit_code", 0, id="fail-zero-exit-code"),
    ],
)
def test_orchestrator_rejects_invalid_executed_run_metadata(valid_artifacts, verdict, field, value):
    """Rejects one contradictory metadata field for an executed run."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    run = invalid["artifacts"]["run_tests_verdict"]
    run["verdict"] = verdict
    if verdict == "FAIL":
        invalid["artifacts"]["execution_evidence"][0]["verdict"] = "FAIL"
        invalid["artifacts"]["trace_audit"].update({"verdict": "FAIL", "errors": ["failure"]})
    run[field] = value
    assert _errors("orchestrator-output.schema.json", invalid)


@pytest.mark.parametrize("field", ["command", "runner", "exit_code"])
def test_orchestrator_rejects_one_not_runnable_execution_field(valid_artifacts, field):
    """NOT_RUNNABLE must not claim one fragment of an execution that did not occur."""
    invalid = copy.deepcopy(valid_artifacts["orchestrator-output.schema.json"])
    invalid["artifacts"].update(
        {
            "run_tests_verdict": {"verdict": "NOT_RUNNABLE", "reason": "runner unavailable", field: "pytest" if field != "exit_code" else 1},
            "execution_evidence": [],
            "trace_audit": {"verdict": "FAIL", "mappings": [], "errors": ["runner unavailable"]},
        }
    )
    assert _errors("orchestrator-output.schema.json", invalid)


def test_generated_method_digest_requires_full_sha256(valid_artifacts):
    """Catches a shortened digest on a generated method independently of file output."""
    invalid = copy.deepcopy(valid_artifacts["tc-to-autotest-output.schema.json"])
    invalid["artifacts"]["generated_test_methods"][0]["content_digest"] = "sha256:abc"
    assert _errors("tc-to-autotest-output.schema.json", invalid)


@pytest.mark.parametrize(
    ("schema_name", "report_key"),
    [
        pytest.param("tc-reviewer-output.schema.json", "validation_report", id="tc-reviewer"),
        pytest.param("autotest-reviewer-output.schema.json", "autotest_review", id="autotest-reviewer"),
    ],
)
def test_reviewer_accepts_honest_rework_with_blocking_finding(valid_artifacts, schema_name, report_key):
    """Allows rework only when its blocking finding is present and no correction leaks."""
    valid = copy.deepcopy(valid_artifacts[schema_name])
    valid["artifacts"][report_key].update(
        {"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "findings": [valid_artifacts["finding"]], "corrections": []}
    )
    assert not _errors(schema_name, valid)
