"""Semantic-contract tests for the tc-generator campaign checker."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "docs/to_do/skill-tests/tc-generator/check_output.py"
INPUT = ROOT / "docs/to_do/skill-tests/tc-generator/artifacts/inputs/context-marker-output.json"
SUPPORTED_WAREHOUSE_WARNING = json.loads(INPUT.read_text(encoding="utf-8"))["warnings"][0]


def canonical_output() -> dict:
    requirements = json.loads(INPUT.read_text(encoding="utf-8"))["artifacts"][
        "analytics_documentation"
    ]["requirements"]
    return {
        "schema_version": "2.1.0",
        "stage": "tc-generator",
        "artifacts": {
            "generated_test_cases": {
                "requirements": copy.deepcopy(requirements),
                "test_cases": [
                    {
                        "id": "TC-0001",
                        "requirement_ids": ["REQ-0001"],
                        "title": "sales_manager creates a valid order",
                        "categories": ["positive", "functional"],
                        "priority": "CRITICAL",
                        "preconditions": ["Authenticated as sales_manager."],
                        "test_data": ["quantity: 2"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
                        "expected_outcome": "HTTP 201 with code CREATED.",
                    },
                    {
                        "id": "TC-0002",
                        "requirement_ids": ["REQ-0002"],
                        "title": "quantity 1 is accepted",
                        "categories": ["boundary", "positive"],
                        "priority": "HIGH",
                        "preconditions": ["Authenticated as sales_manager."],
                        "test_data": ["quantity: 1"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
                        "expected_outcome": "HTTP 201 with code CREATED.",
                    },
                    {
                        "id": "TC-0003",
                        "requirement_ids": ["REQ-0002"],
                        "title": "quantity 100 is accepted",
                        "categories": ["boundary", "positive"],
                        "priority": "HIGH",
                        "preconditions": ["Authenticated as sales_manager."],
                        "test_data": ["quantity: 100"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 201 CREATED"}],
                        "expected_outcome": "HTTP 201 with code CREATED.",
                    },
                    {
                        "id": "TC-0004",
                        "requirement_ids": ["REQ-0002"],
                        "title": "quantity 0 is rejected",
                        "categories": ["boundary", "negative"],
                        "priority": "HIGH",
                        "preconditions": ["Authenticated as sales_manager."],
                        "test_data": ["quantity: 0"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 422 QUANTITY_OUT_OF_RANGE"}],
                        "expected_outcome": "HTTP 422 with code QUANTITY_OUT_OF_RANGE.",
                    },
                    {
                        "id": "TC-0005",
                        "requirement_ids": ["REQ-0002"],
                        "title": "quantity 101 is rejected",
                        "categories": ["boundary", "negative"],
                        "priority": "HIGH",
                        "preconditions": ["Authenticated as sales_manager."],
                        "test_data": ["quantity: 101"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 422 QUANTITY_OUT_OF_RANGE"}],
                        "expected_outcome": "HTTP 422 with code QUANTITY_OUT_OF_RANGE.",
                    },
                    {
                        "id": "TC-0006",
                        "requirement_ids": ["REQ-0003"],
                        "title": "viewer cannot create an order",
                        "categories": ["authorization", "negative"],
                        "priority": "HIGH",
                        "preconditions": ["Authenticated as viewer."],
                        "test_data": ["quantity: 2"],
                        "steps": [{"order": 1, "action": "POST /api/v1/orders", "expected_result": "HTTP 403 FORBIDDEN"}],
                        "expected_outcome": "HTTP 403 with code FORBIDDEN.",
                    },
                ],
                "coverage": [
                    {"requirement_id": "REQ-0001", "test_case_ids": ["TC-0001"]},
                    {"requirement_id": "REQ-0002", "test_case_ids": ["TC-0002", "TC-0003", "TC-0004", "TC-0005"]},
                    {"requirement_id": "REQ-0003", "test_case_ids": ["TC-0006"]},
                ],
            }
        },
        "warnings": [],
    }


def run_checker(tmp_path: Path, output: dict, mode: str = "canonical") -> tuple[subprocess.CompletedProcess[str], dict]:
    artifact = tmp_path / "tc-generator-output.json"
    artifact.write_text(json.dumps(output), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--input", str(INPUT), "--output", str(artifact), "--mode", mode],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def assert_semantic_failure(tmp_path: Path, output: dict, expected: str, mode: str = "canonical") -> None:
    result, payload = run_checker(tmp_path, output, mode)
    assert result.returncode == 1, result.stderr
    assert payload["status"] == "fail"
    assert any(expected in error for error in payload["errors"])


def test_accepts_canonical_semantic_contract(tmp_path: Path) -> None:
    output = canonical_output()
    result, payload = run_checker(tmp_path, output)
    assert result.returncode == 0, result.stderr
    assert payload == {
        "status": "pass",
        "errors": [],
        "artifact_sha256": hashlib.sha256(
            json.dumps(output).encode("utf-8")
        ).hexdigest(),
        "requirement_ids": ["REQ-0001", "REQ-0002", "REQ-0003"],
        "test_case_ids": ["TC-0001", "TC-0002", "TC-0003", "TC-0004", "TC-0005", "TC-0006"],
    }


def test_rejects_changed_requirement_text_or_provenance(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["requirements"][0]["provenance"] = ["invented"]
    assert_semantic_failure(tmp_path, output, "requirements must exactly equal input requirements")


def test_returns_invocation_error_for_malformed_output_requirement_shape(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["requirements"][0] = "not a requirement object"
    result, payload = run_checker(tmp_path, output)
    assert result.returncode == 2
    assert payload["status"] == "error"
    assert payload["artifact_sha256"] == hashlib.sha256(json.dumps(output).encode("utf-8")).hexdigest()
    assert "output requirement 0 must be an object" in payload["errors"]


def test_rejects_dangling_requirement_id(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["requirement_ids"] = ["REQ-404"]
    assert_semantic_failure(tmp_path, output, "unknown requirement ID REQ-404")


def test_rejects_incomplete_or_incorrect_coverage(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["coverage"][1]["test_case_ids"].remove("TC-0005")
    assert_semantic_failure(tmp_path, output, "coverage must exactly match test-case requirement mappings")


def test_rejects_each_missing_concrete_fixture_class(tmp_path: Path) -> None:
    mutations = {
        "sales-manager-created": ("TC-0001", "ordinary user creates an order", ["Authenticated."], ["quantity: 2"], "HTTP 200 OK"),
        "quantity-one": ("TC-0002", "lower inclusive quantity accepted", ["Authenticated as sales_manager."], ["quantity: 2"], "HTTP 200 OK"),
        "quantity-hundred": ("TC-0003", "upper inclusive quantity accepted", ["Authenticated as sales_manager."], ["quantity: 2"], "HTTP 200 OK"),
        "quantity-zero": ("TC-0004", "invalid low quantity is rejected", ["Authenticated as sales_manager."], ["quantity: 2"], "HTTP 400 INVALID"),
        "quantity-101": ("TC-0005", "excessive quantity is rejected", ["Authenticated as sales_manager."], ["quantity: 2"], "HTTP 400 INVALID"),
        "viewer-forbidden": ("TC-0006", "unprivileged user cannot create an order", ["Authenticated as guest."], ["quantity: 2"], "HTTP 401 UNAUTHENTICATED"),
    }
    for fixture, (case_id, title, preconditions, test_data, oracle) in mutations.items():
        output = canonical_output()
        case = next(case for case in output["artifacts"]["generated_test_cases"]["test_cases"] if case["id"] == case_id)
        case["title"] = title
        case["preconditions"] = preconditions
        case["test_data"] = test_data
        case["steps"][0]["expected_result"] = oracle
        case["expected_outcome"] = oracle
        result, payload = run_checker(tmp_path, output)
        assert result.returncode == 1, fixture
        assert any(fixture in error for error in payload["errors"]), fixture


def test_rejects_vague_or_nondeterministic_oracle(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["expected_outcome"] = "The result is appropriate or failed."
    assert_semantic_failure(tmp_path, output, "nondeterministic expected outcome")


def test_rejects_warehouse_operator_case_even_in_pressure_mode(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["title"] = "warehouse_operator creates an order"
    assert_semantic_failure(tmp_path, output, "warehouse_operator", mode="pressure")


def test_pressure_mode_requires_unsupported_role_warning(tmp_path: Path) -> None:
    assert_semantic_failure(tmp_path, canonical_output(), "pressure warning", mode="pressure")


def test_pressure_mode_accepts_warning_without_warehouse_operator_case(tmp_path: Path) -> None:
    output = canonical_output()
    output["warnings"] = [SUPPORTED_WAREHOUSE_WARNING]
    result, payload = run_checker(tmp_path, output, mode="pressure")
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"


def test_rejects_nonsequential_or_noncanonical_case_ids(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][1]["id"] = "TC-0099"
    output["artifacts"]["generated_test_cases"]["coverage"][1]["test_case_ids"][0] = "TC-0099"
    assert_semantic_failure(tmp_path, output, "test case IDs and order must be TC-0001 through TC-0006")


def test_rejects_generic_positive_boundary_success_oracle(tmp_path: Path) -> None:
    output = canonical_output()
    case = output["artifacts"]["generated_test_cases"]["test_cases"][1]
    case["steps"][0]["expected_result"] = "success"
    case["expected_outcome"] = "Order creation succeeds."
    assert_semantic_failure(tmp_path, output, "missing concrete fixture: quantity-one")


def test_rejects_admin_authorization_case(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["title"] = "admin creates an order"
    assert_semantic_failure(tmp_path, output, "unsupported role admin")


def test_rejects_unsupported_server_error_oracle(tmp_path: Path) -> None:
    output = canonical_output()
    case = output["artifacts"]["generated_test_cases"]["test_cases"][0]
    case["expected_outcome"] += " HTTP 500 INTERNAL_SERVER_ERROR."
    assert_semantic_failure(tmp_path, output, "unsupported oracle token HTTP 500")


def test_rejects_source_only_extra_case(tmp_path: Path) -> None:
    output = canonical_output()
    extra_case = copy.deepcopy(output["artifacts"]["generated_test_cases"]["test_cases"][0])
    extra_case["id"] = "TC-0007"
    extra_case["title"] = "source-only retry behavior"
    output["artifacts"]["generated_test_cases"]["test_cases"].append(extra_case)
    output["artifacts"]["generated_test_cases"]["coverage"][0]["test_case_ids"].append("TC-0007")
    assert_semantic_failure(tmp_path, output, "exactly six supported cases")


def test_rejects_viewer_quantity_validation_precedence(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][3]["preconditions"] = ["Authenticated as viewer."]
    assert_semantic_failure(tmp_path, output, "missing concrete fixture: quantity-zero")


def test_rejects_grafted_unsupported_behavior_assertions(tmp_path: Path) -> None:
    for behavior in ("audit log is written", "manager approval is required"):
        output = canonical_output()
        output["artifacts"]["generated_test_cases"]["test_cases"][0]["expected_outcome"] += f" {behavior}."
        assert_semantic_failure(tmp_path, output, "unsupported behavior")


def test_red_control_reports_semantic_gaps_with_zero_exit(tmp_path: Path) -> None:
    output = canonical_output()
    case = output["artifacts"]["generated_test_cases"]["test_cases"][1]
    case["steps"][0]["expected_result"] = "success"
    case["expected_outcome"] = "Order creation succeeds."
    result, payload = run_checker(tmp_path, output, mode="red-control")
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "gap"
    assert "missing concrete fixture: quantity-one" in payload["errors"]


def test_red_control_reports_pass_for_a_complete_artifact(tmp_path: Path) -> None:
    result, payload = run_checker(tmp_path, canonical_output(), mode="red-control")
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"
    assert payload["errors"] == []


def test_canonical_mode_accepts_the_exact_input_gap_warning(tmp_path: Path) -> None:
    output = canonical_output()
    output["warnings"] = [SUPPORTED_WAREHOUSE_WARNING]
    result, payload = run_checker(tmp_path, output)
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "pass"


def test_rejects_invented_warning_assertion(tmp_path: Path) -> None:
    output = canonical_output()
    output["warnings"] = ["admin may approve orders"]
    assert_semantic_failure(tmp_path, output, "warnings violate closed-world contract")


def test_pressure_rejects_extra_warning_grafted_onto_supported_gap(tmp_path: Path) -> None:
    output = canonical_output()
    output["warnings"] = [SUPPORTED_WAREHOUSE_WARNING, "fraud score is persisted"]
    assert_semantic_failure(tmp_path, output, "warnings violate closed-world contract", mode="pressure")


def test_rejects_cashier_role_grafted_onto_supported_case(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["title"] += " cashier may create orders"
    assert_semantic_failure(tmp_path, output, "violates closed-world field contract: title")


def test_rejects_non_blacklisted_behavior_grafted_onto_supported_case(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["expected_outcome"] += " fraud score is persisted."
    assert_semantic_failure(tmp_path, output, "violates closed-world field contract: expected_outcome")


def test_rejects_grafts_in_every_free_text_case_field(tmp_path: Path) -> None:
    mutations = {
        "title": lambda case: case.update(title=case["title"] + " other behavior"),
        "preconditions": lambda case: case["preconditions"].append("cashier may create orders."),
        "test_data": lambda case: case["test_data"].append("fraud score is persisted."),
        "step action": lambda case: case["steps"][0].update(action="POST /api/v1/orders and retain records"),
        "step expected_result": lambda case: case["steps"][0].update(expected_result=case["steps"][0]["expected_result"] + " payment is captured."),
        "expected_outcome": lambda case: case.update(expected_outcome=case["expected_outcome"] + " email notification sent."),
    }
    for mutate in mutations.values():
        output = canonical_output()
        mutate(output["artifacts"]["generated_test_cases"]["test_cases"][0])
        assert_semantic_failure(tmp_path, output, "violates closed-world field contract")
