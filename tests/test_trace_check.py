import copy
import json
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator


def _codes(result):
    return {error["code"] for error in result["errors"]}


def _orchestrator_artifact(document, trace_audit):
    execution = document.get("execution")
    if execution is None:
        evidence = []
        run = {"verdict": "NOT_RUNNABLE", "reason": "topology-only trace cannot be finalized"}
    else:
        status_to_verdict = {"passed": "PASS", "failed": "FAIL", "error": "FAIL", "skipped": "SKIPPED"}
        rules = {rule["method_id"]: rule for rule in execution["allowed_skips"]}
        evidence = []
        for item in execution["evidence"]:
            normalized = {"run_id": item["run_id"], "method_id": item["method_id"], "verdict": status_to_verdict[item["status"]]}
            if item["status"] == "skipped" and item["method_id"] in rules:
                normalized.update({"reason": rules[item["method_id"]]["reason"], "policy_ref": rules[item["method_id"]]["policy_ref"]})
            evidence.append(normalized)
        run = {"verdict": execution["verdict"], "reason": "runner result"}
        if execution["verdict"] != "NOT_RUNNABLE":
            run.update({"command": "pytest", "runner": "pytest", "exit_code": 0 if execution["verdict"] == "PASS" else 1})
    return {"schema_version": "2.1.0", "stage": "orchestrate", "warnings": [], "artifacts": {"run_tests_verdict": run, "execution_evidence": evidence, "trace_audit": copy.deepcopy(trace_audit)}}


@pytest.fixture
def valid_trace():
    return {
        "schema_version": "2.1.0",
        "requirements": [{"id": "REQ-1", "provenance": ["spec section 1"]}],
        "test_cases": [{"id": "TC-1", "requirement_ids": ["REQ-1"]}],
        "generated_files": [{"id": "FILE-1", "path": "tests/test_api.py"}],
        "methods": [
            {
                "id": "METHOD-1",
                "file_id": "FILE-1",
                "name": "test_tc_1",
                "test_case_ids": ["TC-1"],
                "requirement_ids": ["REQ-1"],
            }
        ],
        "trace_map": [
            {
                "requirement_id": "REQ-1",
                "test_case_id": "TC-1",
                "file_id": "FILE-1",
                "method_id": "METHOD-1",
            }
        ],
        "execution_required": True,
        "execution": {
            "verdict": "PASS",
            "evidence": [{"run_id": "RUN-1", "method_id": "METHOD-1", "status": "passed"}],
            "allowed_skips": [],
        },
        "final_verdict": "PASS",
    }


def test_full_trace_with_passed_execution_is_valid(trace_check, valid_trace):
    result = trace_check.check(valid_trace, require_execution=True)
    assert result["valid"] is True
    assert result["trace_audit"] == {
        "verdict": "PASS",
        "mappings": [
            {
                "requirement_id": "REQ-1",
                "test_case_id": "TC-1",
                "file_id": "FILE-1",
                "method_id": "METHOD-1",
                "evidence_ids": ["RUN-1"],
            }
        ],
        "errors": [],
    }


def test_topology_only_trace_is_valid_when_execution_is_not_required(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution_required"] = False
    document.pop("execution")
    result = trace_check.check(document)
    assert result["valid"] is True
    assert result["trace_audit"]["mappings"][0]["evidence_ids"] == []


@pytest.mark.parametrize("path", ["tests/./test_api.py", "tests//test_api.py", "tests/test_api.py/", "tests/../test_api.py", "C:/tests/test_api.py", "/tests/test_api.py", "tests\\test_api.py"])
def test_schema_rejects_noncanonical_portable_file_path(trace_check, valid_trace, path):
    document = copy.deepcopy(valid_trace)
    document["generated_files"][0]["path"] = path
    assert "invalid_input_schema" in _codes(trace_check.check(document))


@pytest.mark.parametrize("path", ["tests/.fixtures/test_api.py", "src/test/java/com/acme/ApiTest.java"])
def test_schema_accepts_canonical_portable_file_path(trace_check, valid_trace, path):
    document = copy.deepcopy(valid_trace)
    document["generated_files"][0]["path"] = path
    assert trace_check.check(document)["valid"] is True


def test_casefold_equivalent_file_paths_are_duplicate_physical_identity(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["generated_files"].append({"id": "FILE-2", "path": "tests/TEST_API.py"})
    document["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_second", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"]})
    document["trace_map"].append({"requirement_id": "REQ-1", "test_case_id": "TC-1", "file_id": "FILE-2", "method_id": "METHOD-2"})
    document["execution"]["evidence"].append({"run_id": "RUN-2", "method_id": "METHOD-2", "status": "passed"})
    assert "DUPLICATE_PATH" in _codes(trace_check.check(document))


@pytest.mark.parametrize("field", ["requirements", "test_cases", "generated_files", "methods", "trace_map"])
def test_schema_rejects_vacuous_topology_arrays(trace_check, valid_trace, field):
    document = copy.deepcopy(valid_trace)
    document[field] = []
    assert "invalid_input_schema" in _codes(trace_check.check(document))


def test_required_execution_pass_with_empty_evidence_is_invalid(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["evidence"] = []
    assert "MISSING_EXECUTION" in _codes(trace_check.check(document))


@pytest.mark.parametrize("verdict", ["FAIL", "NOT_RUNNABLE"])
def test_optional_supplied_non_passing_execution_is_never_a_valid_topology(trace_check, valid_trace, verdict):
    document = copy.deepcopy(valid_trace)
    document["execution_required"] = False
    document["execution"].update({"verdict": verdict, "evidence": [], "allowed_skips": []})
    document["final_verdict"] = verdict
    result = trace_check.check(document)
    assert result["valid"] is False
    assert "EXECUTION_GATE" in _codes(result)
    assert "VERDICT_MISMATCH" not in _codes(result)


def test_execution_verdict_must_match_failed_or_passing_evidence(trace_check, valid_trace):
    pass_with_failure = copy.deepcopy(valid_trace)
    pass_with_failure["execution"]["evidence"][0]["status"] = "failed"
    assert {"EXECUTION_FAILURE", "EXECUTION_VERDICT_MISMATCH"} <= _codes(trace_check.check(pass_with_failure))
    fail_with_pass = copy.deepcopy(valid_trace)
    fail_with_pass["execution"]["verdict"] = "FAIL"
    fail_with_pass["final_verdict"] = "FAIL"
    result = trace_check.check(fail_with_pass)
    assert "EXECUTION_VERDICT_MISMATCH" in _codes(result)
    assert "VERDICT_MISMATCH" not in _codes(result)


def test_not_runnable_execution_requires_empty_records_and_honest_final_verdict(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["verdict"] = "NOT_RUNNABLE"
    document["final_verdict"] = "NOT_RUNNABLE"
    result = trace_check.check(document)
    assert {"EXECUTION_GATE", "EXECUTION_VERDICT_MISMATCH"} <= _codes(result)
    assert "VERDICT_MISMATCH" not in _codes(result)
    assert result["trace_audit"]["mappings"] == []


def test_reverse_mapping_coverage_catches_method_and_case_declarations(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["requirements"].append({"id": "REQ-2", "provenance": ["spec section 2"]})
    document["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-2"]})
    document["generated_files"].append({"id": "FILE-2", "path": "tests/test_other.py"})
    document["methods"][0].update(test_case_ids=["TC-1", "TC-2"], requirement_ids=["REQ-1", "REQ-2"])
    document["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_other", "test_case_ids": ["TC-2"], "requirement_ids": ["REQ-2"]})
    document["trace_map"].append({"requirement_id": "REQ-2", "test_case_id": "TC-2", "file_id": "FILE-2", "method_id": "METHOD-2"})
    errors = trace_check.check(document)["errors"]
    missing = [error["message"] for error in errors if error["code"] == "MISSING_MAPPING"]
    assert any("TC-2" in message and "METHOD-1" in message for message in missing)
    assert any("REQ-2" in message and "METHOD-1" in message for message in missing)


def test_reverse_case_requirement_pair_coverage_is_not_global_only(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["requirements"].append({"id": "REQ-2", "provenance": ["spec section 2"]})
    document["test_cases"][0]["requirement_ids"].append("REQ-2")
    document["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-2"]})
    document["generated_files"].append({"id": "FILE-2", "path": "tests/test_other.py"})
    document["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_other", "test_case_ids": ["TC-2"], "requirement_ids": ["REQ-2"]})
    document["trace_map"].append({"requirement_id": "REQ-2", "test_case_id": "TC-2", "file_id": "FILE-2", "method_id": "METHOD-2"})
    messages = [error["message"] for error in trace_check.check(document)["errors"] if error["code"] == "MISSING_MAPPING"]
    assert any("TC-1" in message and "REQ-2" in message for message in messages)


def test_duplicate_file_path_and_method_locator_are_rejected(trace_check, valid_trace):
    duplicate_path = copy.deepcopy(valid_trace)
    duplicate_path["generated_files"].append({"id": "FILE-2", "path": "tests/test_api.py"})
    duplicate_path["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_second", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"]})
    duplicate_path["trace_map"].append({"requirement_id": "REQ-1", "test_case_id": "TC-1", "file_id": "FILE-2", "method_id": "METHOD-2"})
    assert "DUPLICATE_PATH" in _codes(trace_check.check(duplicate_path))
    duplicate_locator = copy.deepcopy(valid_trace)
    duplicate_locator["methods"].append(copy.deepcopy(duplicate_locator["methods"][0]) | {"id": "METHOD-2"})
    duplicate_locator["trace_map"].append({"requirement_id": "REQ-1", "test_case_id": "TC-1", "file_id": "FILE-1", "method_id": "METHOD-2"})
    assert "DUPLICATE_METHOD_LOCATOR" in _codes(trace_check.check(duplicate_locator))


def test_same_method_name_in_different_file_is_valid_many_to_many(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["requirements"].append({"id": "REQ-2", "provenance": ["spec section 2"]})
    document["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-2"]})
    document["generated_files"].append({"id": "FILE-2", "path": "tests/test_other.py"})
    document["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_tc_1", "test_case_ids": ["TC-2"], "requirement_ids": ["REQ-2"]})
    document["trace_map"].append({"requirement_id": "REQ-2", "test_case_id": "TC-2", "file_id": "FILE-2", "method_id": "METHOD-2"})
    document["execution"]["evidence"].append({"run_id": "RUN-2", "method_id": "METHOD-2", "status": "passed"})
    assert trace_check.check(document)["valid"] is True


def test_missing_requirement_and_case_mappings_are_independently_reported(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["requirements"].append({"id": "REQ-2", "provenance": ["spec section 2"]})
    document["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-1"]})
    errors = trace_check.check(document)["errors"]
    assert any(error["code"] == "MISSING_MAPPING" and "REQ-2" in error["message"] for error in errors)
    assert any(error["code"] == "MISSING_MAPPING" and "TC-2" in error["message"] for error in errors)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda d: d["requirements"].append({"id": "REQ-2", "provenance": ["source"]}), "MISSING_MAPPING"),
        (lambda d: d["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-1"]}), "MISSING_MAPPING"),
        (lambda d: d["methods"].append(copy.deepcopy(d["methods"][0]) | {"id": "METHOD-2"}), "ORPHAN_METHOD"),
        (lambda d: d["generated_files"].append({"id": "FILE-2", "path": "tests/orphan.py"}), "ORPHAN_FILE"),
        (lambda d: d["requirements"].append({"id": "REQ-1", "provenance": ["another source"]}), "DUPLICATE_ID"),
        (lambda d: d["trace_map"].append(copy.deepcopy(d["trace_map"][0])), "DUPLICATE_MAPPING"),
    ],
)
def test_topology_invariants_are_enforced(trace_check, valid_trace, mutation, code):
    document = copy.deepcopy(valid_trace)
    mutation(document)
    assert code in _codes(trace_check.check(document))


@pytest.mark.parametrize(
    ("section", "field", "value", "code"),
    [
        ("test_cases", "requirement_ids", ["REQ-404"], "UNKNOWN_REQUIREMENT"),
        ("methods", "test_case_ids", ["TC-404"], "UNKNOWN_TEST_CASE"),
        ("methods", "file_id", "FILE-404", "UNKNOWN_FILE"),
        ("trace_map", "method_id", "METHOD-404", "UNKNOWN_METHOD"),
    ],
)
def test_every_reference_type_is_checked(trace_check, valid_trace, section, field, value, code):
    document = copy.deepcopy(valid_trace)
    document[section][0][field] = value
    assert code in _codes(trace_check.check(document))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requirement_id", "REQ-404"),
        ("test_case_id", "TC-404"),
        ("file_id", "FILE-404"),
        ("method_id", "METHOD-404"),
    ],
)
def test_unknown_mapping_references_are_checked(trace_check, valid_trace, field, value):
    document = copy.deepcopy(valid_trace)
    document["trace_map"][0][field] = value
    expected = {
        "requirement_id": "UNKNOWN_REQUIREMENT",
        "test_case_id": "UNKNOWN_TEST_CASE",
        "file_id": "UNKNOWN_FILE",
        "method_id": "UNKNOWN_METHOD",
    }[field]
    assert expected in _codes(trace_check.check(document))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: (d["requirements"].append({"id": "REQ-2", "provenance": ["source"]}), d["test_cases"][0].update(requirement_ids=["REQ-2"])),
        lambda d: (d["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-1"]}), d["methods"][0].update(test_case_ids=["TC-2"])),
        lambda d: (d["requirements"].append({"id": "REQ-2", "provenance": ["source"]}), d["methods"][0].update(requirement_ids=["REQ-2"])),
        lambda d: (d["generated_files"].append({"id": "FILE-2", "path": "tests/second.py"}), d["methods"][0].update(file_id="FILE-2")),
    ],
)
def test_mapping_relationships_must_match_declared_ownership(trace_check, valid_trace, mutation):
    document = copy.deepcopy(valid_trace)
    mutation(document)
    assert "MAPPING_MISMATCH" in _codes(trace_check.check(document))


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda d: d["execution"]["evidence"][0].update(method_id="METHOD-404"), "UNKNOWN_METHOD"),
        (lambda d: d["execution"]["evidence"].append({"run_id": "RUN-1", "method_id": "METHOD-1", "status": "passed"}), "DUPLICATE_ID"),
        (lambda d: d["execution"]["evidence"].append(copy.deepcopy(d["execution"]["evidence"][0])), "DUPLICATE_MAPPING"),
        (lambda d: d["execution"]["evidence"].clear(), "MISSING_EXECUTION"),
        (lambda d: d["execution"]["evidence"][0].update(status="failed"), "EXECUTION_FAILURE"),
        (lambda d: d["execution"]["evidence"][0].update(status="error"), "EXECUTION_FAILURE"),
        (lambda d: d["execution"]["evidence"][0].update(status="skipped"), "DISALLOWED_SKIP"),
    ],
)
def test_execution_evidence_is_method_level_and_honest(trace_check, valid_trace, mutation, code):
    document = copy.deepcopy(valid_trace)
    mutation(document)
    assert code in _codes(trace_check.check(document, require_execution=True))


def test_allowed_skip_accepts_only_the_matching_skipped_method(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["evidence"][0]["status"] = "skipped"
    document["execution"]["allowed_skips"] = [
        {"method_id": "METHOD-1", "reason": "environment policy", "policy_ref": "POLICY-1"}
    ]
    assert trace_check.check(document, require_execution=True)["valid"] is True


def test_unused_skip_rule_is_rejected(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["allowed_skips"] = [
        {"method_id": "METHOD-1", "reason": "environment policy", "policy_ref": "POLICY-1"}
    ]
    assert "UNUSED_SKIP_RULE" in _codes(trace_check.check(document, require_execution=True))


def test_unknown_and_duplicate_skip_rules_are_rejected(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["evidence"][0]["status"] = "skipped"
    document["execution"]["allowed_skips"] = [
        {"method_id": "METHOD-1", "reason": "policy", "policy_ref": "P-1"},
        {"method_id": "METHOD-1", "reason": "policy", "policy_ref": "P-2"},
        {"method_id": "METHOD-404", "reason": "policy", "policy_ref": "P-3"},
    ]
    codes = _codes(trace_check.check(document, require_execution=True))
    assert {"DUPLICATE_SKIP_RULE", "UNKNOWN_METHOD", "UNUSED_SKIP_RULE", "DISALLOWED_SKIP"} <= codes


@pytest.mark.parametrize("verdict", ["FAIL", "NOT_RUNNABLE"])
def test_execution_gate_rejects_non_passing_execution(trace_check, valid_trace, verdict):
    document = copy.deepcopy(valid_trace)
    document["execution"]["verdict"] = verdict
    assert "EXECUTION_GATE" in _codes(trace_check.check(document, require_execution=True))


def test_execution_argument_overrides_optional_document_execution(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution_required"] = False
    document.pop("execution")
    assert trace_check.check(document)["valid"] is True
    assert "EXECUTION_GATE" in _codes(trace_check.check(document, require_execution=True))


def test_document_execution_gate_requires_execution(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document.pop("execution")
    assert "EXECUTION_GATE" in _codes(trace_check.check(document))


def test_final_verdict_claims_must_be_honest(trace_check, valid_trace):
    broken = copy.deepcopy(valid_trace)
    broken["requirements"].append({"id": "REQ-2", "provenance": ["source"]})
    assert "VERDICT_MISMATCH" in _codes(trace_check.check(broken))
    for false_claim in ("FAIL", "NOT_RUNNABLE"):
        document = copy.deepcopy(valid_trace)
        document["final_verdict"] = false_claim
        assert "VERDICT_MISMATCH" in _codes(trace_check.check(document))


def test_schema_rejects_malformed_envelope_extra_properties_and_bare_ids(trace_check, valid_trace):
    for mutation, path in (
        (lambda d: d.pop("requirements"), "/requirements"),
        (lambda d: d.update(unexpected=True), "/unexpected"),
        (lambda d: d["requirements"][0].update(id="REQ-"), "/requirements/0/id"),
    ):
        document = copy.deepcopy(valid_trace)
        mutation(document)
        result = trace_check.check(document)
        assert result["errors"][0]["code"] == "invalid_input_schema"
        assert any(error["path"] == path for error in result["errors"])


def test_schema_validation_precedes_semantics(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["trace_map"].clear()
    document["requirements"][0]["id"] = "REQ-"
    result = trace_check.check(document)
    assert _codes(result) == {"invalid_input_schema"}


def test_reports_are_deterministic_pointer_sorted_and_do_not_mutate_input(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["trace_map"].clear()
    before = copy.deepcopy(document)
    first = trace_check.check(document)
    second = trace_check.check(document)
    assert document == before
    assert first == second
    assert first["errors"] == sorted(first["errors"], key=lambda error: (error["path"], error["code"], error["message"]))


def test_trace_audit_embeds_in_existing_orchestrator_artifact(trace_check, valid_trace, root):
    trace = trace_check.check(valid_trace, require_execution=True)["trace_audit"]
    schema = json.loads((root / "schemas" / "orchestrator-output.schema.json").read_text(encoding="utf-8"))
    artifact = {
        "schema_version": "2.1.0",
        "stage": "orchestrate",
        "warnings": [],
        "artifacts": {
            "run_tests_verdict": {"verdict": "PASS", "reason": "passed", "command": "pytest", "runner": "pytest", "exit_code": 0},
            "execution_evidence": [{"method_id": "METHOD-1", "verdict": "PASS", "run_id": "RUN-1"}],
            "trace_audit": trace,
        },
    }
    assert list(Draft202012Validator(schema).iter_errors(artifact)) == []


def test_authoritative_orchestrator_cross_check_accepts_exact_full_pass(trace_check, valid_trace):
    trace = trace_check.check(valid_trace, require_execution=True)["trace_audit"]
    artifact = _orchestrator_artifact(valid_trace, trace)
    result = trace_check.check_orchestrator(artifact, valid_trace)
    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda a: a["artifacts"]["execution_evidence"][0].update(run_id="RUN-foreign"), "ORCHESTRATOR_EXECUTION_MISMATCH"),
        (lambda a: a["artifacts"]["execution_evidence"][0].update(method_id="METHOD-foreign"), "ORCHESTRATOR_EXECUTION_MISMATCH"),
        (lambda a: a["artifacts"]["trace_audit"]["mappings"][0].update(file_id="FILE-foreign"), "ORCHESTRATOR_TRACE_MISMATCH"),
        (lambda a: a["artifacts"]["trace_audit"].update(verdict="FAIL", errors=["changed trace error"]), "ORCHESTRATOR_TRACE_MISMATCH"),
        (lambda a: a["artifacts"]["execution_evidence"].clear(), "ORCHESTRATOR_EXECUTION_MISMATCH"),
    ],
)
def test_authoritative_orchestrator_cross_check_rejects_one_changed_fact(trace_check, valid_trace, mutation, code):
    trace = trace_check.check(valid_trace, require_execution=True)["trace_audit"]
    artifact = _orchestrator_artifact(valid_trace, trace)
    mutation(artifact)
    assert code in _codes(trace_check.check_orchestrator(artifact, valid_trace))


def test_authoritative_cross_check_preserves_allowed_skip_metadata(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["execution"]["evidence"][0]["status"] = "skipped"
    document["execution"]["allowed_skips"] = [{"method_id": "METHOD-1", "reason": "policy reason", "policy_ref": "POLICY-1"}]
    trace = trace_check.check(document, require_execution=True)["trace_audit"]
    artifact = _orchestrator_artifact(document, trace)
    assert trace_check.check_orchestrator(artifact, document)["valid"] is True
    artifact["artifacts"]["execution_evidence"][0]["reason"] = "foreign reason"
    assert "ORCHESTRATOR_EXECUTION_MISMATCH" in _codes(trace_check.check_orchestrator(artifact, document))


def test_authoritative_cross_check_rejects_topology_only_final_envelope(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document.update(execution_required=False)
    document.pop("execution")
    artifact = _orchestrator_artifact(document, trace_check.check(document)["trace_audit"])
    artifact["artifacts"]["trace_audit"] = {"verdict": "FAIL", "mappings": [], "errors": ["not executed"]}
    assert "ORCHESTRATOR_EXECUTION_MISMATCH" in _codes(trace_check.check_orchestrator(artifact, document))


@pytest.mark.parametrize("verdict", ["FAIL", "NOT_RUNNABLE"])
def test_authoritative_cross_check_preserves_honest_nonpassing_pipeline_stop(trace_check, valid_trace, verdict):
    document = copy.deepcopy(valid_trace)
    document["execution"]["verdict"] = verdict
    document["final_verdict"] = verdict
    if verdict == "FAIL":
        document["execution"]["evidence"][0]["status"] = "failed"
    else:
        document["execution"].update(evidence=[], allowed_skips=[])
    artifact = _orchestrator_artifact(document, trace_check.check(document, require_execution=True)["trace_audit"])
    result = trace_check.check_orchestrator(artifact, document)
    assert result["valid"] is False
    assert not ({"ORCHESTRATOR_EXECUTION_MISMATCH", "ORCHESTRATOR_TRACE_MISMATCH"} & _codes(result))


def test_authoritative_cross_check_accepts_order_only_differences(trace_check, valid_trace):
    document = copy.deepcopy(valid_trace)
    document["requirements"].append({"id": "REQ-2", "provenance": ["spec 2"]})
    document["test_cases"].append({"id": "TC-2", "requirement_ids": ["REQ-2"]})
    document["generated_files"].append({"id": "FILE-2", "path": "tests/test_second.py"})
    document["methods"].append({"id": "METHOD-2", "file_id": "FILE-2", "name": "test_second", "test_case_ids": ["TC-2"], "requirement_ids": ["REQ-2"]})
    document["trace_map"].append({"requirement_id": "REQ-2", "test_case_id": "TC-2", "file_id": "FILE-2", "method_id": "METHOD-2"})
    document["execution"]["evidence"].append({"run_id": "RUN-2", "method_id": "METHOD-2", "status": "passed"})
    artifact = _orchestrator_artifact(document, trace_check.check(document, require_execution=True)["trace_audit"])
    artifact["artifacts"]["execution_evidence"].reverse()
    artifact["artifacts"]["trace_audit"]["mappings"].reverse()
    assert trace_check.check_orchestrator(artifact, document)["valid"] is True


def test_authoritative_cross_check_is_deterministic_and_does_not_mutate_inputs(trace_check, valid_trace):
    trace = trace_check.check(valid_trace, require_execution=True)["trace_audit"]
    artifact = _orchestrator_artifact(valid_trace, trace)
    artifact["artifacts"]["trace_audit"]["mappings"].reverse()
    before_artifact = copy.deepcopy(artifact)
    before_document = copy.deepcopy(valid_trace)
    first = trace_check.check_orchestrator(artifact, valid_trace)
    second = trace_check.check_orchestrator(artifact, valid_trace)
    assert first == second
    assert first["valid"] is True
    assert artifact == before_artifact
    assert valid_trace == before_document


def test_cli_reports_json_exit_codes_and_non_ascii(tmp_path, valid_trace, root):
    document = tmp_path / "трасса.json"
    document.write_text(json.dumps(valid_trace, ensure_ascii=False), encoding="utf-8")
    command = [sys.executable, root / "tools" / "trace_check.py", str(document), "--require-execution"]
    passed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", check=False)
    assert passed.returncode == 0
    assert json.loads(passed.stdout)["valid"] is True
    invalid = copy.deepcopy(valid_trace)
    invalid["requirements"].append({"id": "REQ-2", "provenance": ["source"]})
    document.write_text(json.dumps(invalid), encoding="utf-8")
    failed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", check=False)
    assert failed.returncode == 1
    assert "MISSING_MAPPING" in _codes(json.loads(failed.stdout))


def test_cli_orchestrator_cross_check_has_success_semantic_and_schema_exits(tmp_path, valid_trace, trace_check, root):
    document_path = tmp_path / "trace.json"
    document_path.write_text(json.dumps(valid_trace), encoding="utf-8")
    artifact = _orchestrator_artifact(valid_trace, trace_check.check(valid_trace, require_execution=True)["trace_audit"])
    artifact_path = tmp_path / "orchestrator.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    command = [sys.executable, root / "tools" / "trace_check.py", str(document_path), "--orchestrator-artifact", str(artifact_path)]
    assert subprocess.run(command, text=True, capture_output=True, encoding="utf-8", check=False).returncode == 0
    artifact["artifacts"]["execution_evidence"][0]["run_id"] = "RUN-foreign"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    semantic = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", check=False)
    assert semantic.returncode == 1
    assert "ORCHESTRATOR_EXECUTION_MISMATCH" in _codes(json.loads(semantic.stdout))
    artifact["unexpected"] = True
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    invalid = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", check=False)
    assert invalid.returncode == 2
    assert "invalid_orchestrator_schema" in _codes(json.loads(invalid.stdout))


def test_cli_input_and_schema_errors_are_json_exit_two(tmp_path, valid_trace, root):
    tool = root / "tools" / "trace_check.py"
    bad_json = tmp_path / "bad.json"
    bad_json.write_bytes(b"\\xff")
    unreadable = subprocess.run([sys.executable, tool, str(bad_json)], text=True, capture_output=True, check=False)
    assert unreadable.returncode == 2
    assert json.loads(unreadable.stdout)["errors"]
    missing = subprocess.run([sys.executable, tool, str(tmp_path / "absent.json")], text=True, capture_output=True, check=False)
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["errors"]
    invalid_schema = tmp_path / "schema.json"
    invalid_schema.write_text('{"type":"not-a-type"}', encoding="utf-8")
    document = tmp_path / "document.json"
    document.write_text(json.dumps(valid_trace), encoding="utf-8")
    schema = subprocess.run([sys.executable, tool, str(document), "--schema", str(invalid_schema)], text=True, capture_output=True, check=False)
    assert schema.returncode == 2
    assert json.loads(schema.stdout)["errors"]
    arguments = subprocess.run([sys.executable, tool], text=True, capture_output=True, check=False)
    assert arguments.returncode == 2
    assert json.loads(arguments.stdout)["errors"]


def test_cli_runtime_exception_is_json_exit_two_without_traceback(root):
    result = subprocess.run(
        [sys.executable, root / "tools" / "trace_check.py", root / "contracts" / "pipeline.json", "--schema", root / "schemas" / "pipeline.schema.json"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 2
    assert report["errors"] == [{"code": "runtime_error", "path": "", "message": "runtime error during trace check"}]
    assert result.stderr == ""
