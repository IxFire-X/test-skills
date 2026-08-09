import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCALE_DIRECTORY = ROOT / "docs" / "to_do" / "skill-tests" / "tc-generator" / "08-scale-acceptance"
INPUT_PATH = SCALE_DIRECTORY / "input" / "context-marker-output.json"
MATRIX_PATH = SCALE_DIRECTORY / "expected-matrix.json"
CHECKER_PATH = SCALE_DIRECTORY / "check_output.py"
EXPECTED_WARNING = "analytics/bulk-shipment.md — No authorization policy is supplied for claims_adjuster."
EXPECTED_PROVENANCE = {
    **{f"REQ-{number:04d}": [f"analytics/bulk-shipment.md#/operations/{name}"] for number, name in (
        (1001, "create-shipment"), (1002, "add-package"), (1003, "schedule-pickup"),
        (1004, "cancel-pickup"), (1005, "generate-label"), (1006, "close-manifest"),
    )},
    **{f"REQ-{number:04d}": [f"analytics/bulk-shipment.md#/validation/{field}"] for number, field in (
        (1011, "package_count"), (1012, "weight_grams"), (1013, "length_cm"),
        (1014, "width_cm"), (1015, "height_cm"), (1016, "declared_value_cents"),
    )},
    **{f"REQ-{number:04d}": [f"analytics/bulk-shipment.md#/authorization/{policy}"] for number, policy in (
        (1021, "viewer-create-shipment"), (1022, "auditor-add-package"), (1023, "picker-schedule-pickup"),
        (1024, "viewer-cancel-pickup"), (1025, "guest-generate-label"), (1026, "picker-close-manifest"),
    )},
}
OPERATION_ORACLES = {
    "REQ-1001": ("POST", "/api/v1/shipments", 201, "SHIPMENT_CREATED"),
    "REQ-1002": ("POST", "/api/v1/shipments/{shipment_id}/packages", 201, "PACKAGE_ADDED"),
    "REQ-1003": ("POST", "/api/v1/shipments/{shipment_id}/pickup", 202, "PICKUP_SCHEDULED"),
    "REQ-1004": ("DELETE", "/api/v1/shipments/{shipment_id}/pickup", 200, "PICKUP_CANCELLED"),
    "REQ-1005": ("POST", "/api/v1/shipments/{shipment_id}/label", 201, "LABEL_CREATED"),
    "REQ-1006": ("POST", "/api/v1/manifests/{manifest_id}/close", 200, "MANIFEST_CLOSED"),
    "REQ-1011": ("POST", "/api/v1/shipments", 201, "SHIPMENT_CREATED"),
    **{f"REQ-{number:04d}": ("POST", "/api/v1/shipments/{shipment_id}/packages", 201, "PACKAGE_ADDED") for number in range(1012, 1017)},
}
RANGE_ERRORS = {
    "REQ-1011": "PACKAGE_COUNT_OUT_OF_RANGE", "REQ-1012": "WEIGHT_GRAMS_OUT_OF_RANGE",
    "REQ-1013": "LENGTH_CM_OUT_OF_RANGE", "REQ-1014": "WIDTH_CM_OUT_OF_RANGE",
    "REQ-1015": "HEIGHT_CM_OUT_OF_RANGE", "REQ-1016": "DECLARED_VALUE_CENTS_OUT_OF_RANGE",
}
DENIAL_ORACLES = {
    "REQ-1021": ("viewer", "POST", "/api/v1/shipments"),
    "REQ-1022": ("auditor", "POST", "/api/v1/shipments/{shipment_id}/packages"),
    "REQ-1023": ("picker", "POST", "/api/v1/shipments/{shipment_id}/pickup"),
    "REQ-1024": ("viewer", "DELETE", "/api/v1/shipments/{shipment_id}/pickup"),
    "REQ-1025": ("guest", "POST", "/api/v1/shipments/{shipment_id}/label"),
    "REQ-1026": ("picker", "POST", "/api/v1/manifests/{manifest_id}/close"),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scale_fixture_has_exact_counts_order_categories_and_warning() -> None:
    context = load_json(INPUT_PATH)
    matrix = load_json(MATRIX_PATH)

    requirements = context["artifacts"]["analytics_documentation"]["requirements"]
    requirement_ids = [requirement["id"] for requirement in requirements]
    assert len(requirement_ids) == 18
    assert len(set(requirement_ids)) == 18
    assert requirement_ids == [
        *(f"REQ-{number:04d}" for number in range(1001, 1007)),
        *(f"REQ-{number:04d}" for number in range(1011, 1017)),
        *(f"REQ-{number:04d}" for number in range(1021, 1027)),
    ]
    assert {requirement["id"]: requirement["provenance"] for requirement in requirements} == EXPECTED_PROVENANCE

    groups = ("happy", "positive_boundary", "negative_boundary", "authorization")
    expected_totals = (6, 12, 12, 6)
    atoms = [atom for group in groups for atom in matrix[group]]
    case_ids = [atom["id"] for atom in atoms]
    assert tuple(len(matrix[group]) for group in groups) == expected_totals
    assert matrix["expected_total"] == 36
    assert len(case_ids) == 36
    assert len(set(case_ids)) == 36
    assert case_ids == [f"TC-{number:04d}" for number in range(1, 37)]
    assert [atom["requirement_id"] for atom in matrix["happy"]] == [f"REQ-{number:04d}" for number in range(1001, 1007)]
    assert [atom["requirement_id"] for atom in matrix["positive_boundary"]] == [requirement_id for requirement_id in requirement_ids[6:12] for _ in range(2)]
    assert [atom["requirement_id"] for atom in matrix["negative_boundary"]] == [requirement_id for requirement_id in requirement_ids[6:12] for _ in range(2)]
    assert [atom["requirement_id"] for atom in matrix["authorization"]] == [f"REQ-{number:04d}" for number in range(1021, 1027)]

    required_atom_fields = {"id", "requirement_id", "kind", "categories", "title", "preconditions", "test_data", "step", "expected_outcome", "priority", "method", "path", "expected_status", "expected_code", "role"}
    for group in groups:
        for atom in matrix[group]:
            assert required_atom_fields <= atom.keys()
            assert atom["requirement_id"] in requirement_ids
            assert atom["step"] == {"action": f"{atom['method']} {atom['path']}", "expected_result": f"HTTP {atom['expected_status']} {atom['expected_code']}"}
            assert atom["expected_outcome"] == f"HTTP {atom['expected_status']} with code {atom['expected_code']}."
            assert "claims_adjuster" not in json.dumps(atom, ensure_ascii=False, sort_keys=True)
            if group == "authorization":
                role, method, path = DENIAL_ORACLES[atom["requirement_id"]]
                assert (atom["role"], atom["method"], atom["path"], atom["expected_status"], atom["expected_code"]) == (role, method, path, 403, "FORBIDDEN")
            else:
                method, path, status, code = OPERATION_ORACLES[atom["requirement_id"]]
                if group == "negative_boundary":
                    status, code = 422, RANGE_ERRORS[atom["requirement_id"]]
                assert (atom["role"], atom["method"], atom["path"], atom["expected_status"], atom["expected_code"]) == (None, method, path, status, code)

    assert context["warnings"] == [EXPECTED_WARNING]
    assert matrix["warnings"] == [EXPECTED_WARNING]


def _canonical_output() -> dict:
    context = load_json(INPUT_PATH)
    matrix = load_json(MATRIX_PATH)
    atoms = [atom for group in ("happy", "positive_boundary", "negative_boundary", "authorization") for atom in matrix[group]]
    requirements = context["artifacts"]["analytics_documentation"]["requirements"]
    return {
        "schema_version": "2.1.0",
        "stage": "tc-generator",
        "artifacts": {
            "generated_test_cases": {
                "requirements": requirements,
                "test_cases": [
                    {
                        "id": atom["id"],
                        "requirement_ids": [atom["requirement_id"]],
                        "title": atom["title"],
                        "categories": atom["categories"],
                        "priority": atom["priority"],
                        "preconditions": atom["preconditions"],
                        "test_data": [f"{field}: {value}" for field, value in atom["test_data"].items()],
                        "steps": [{"order": 1, "action": f"{atom['method']} {atom['path']}", "expected_result": f"HTTP {atom['expected_status']} {atom['expected_code']}"}],
                        "expected_outcome": atom["expected_outcome"],
                    }
                    for atom in atoms
                ],
                "coverage": [
                    {"requirement_id": requirement["id"], "test_case_ids": [atom["id"] for atom in atoms if atom["requirement_id"] == requirement["id"]]}
                    for requirement in requirements
                ],
            }
        },
        "warnings": context["warnings"],
    }


def _run_checker(tmp_path: Path, output: object) -> tuple[int, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "output.json"
    output_path.write_text(json.dumps(output), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--input", str(INPUT_PATH), "--matrix", str(MATRIX_PATH), "--output", str(output_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_scale_checker_accepts_canonical_synthetic_output(tmp_path: Path) -> None:
    exit_code, result = _run_checker(tmp_path, _canonical_output())
    assert exit_code == 0
    assert result == {
        "artifact_sha256": result["artifact_sha256"],
        "errors": [],
        "requirement_count": 18,
        "status": "pass",
        "test_case_count": 36,
    }
    assert len(result["artifact_sha256"]) == 64


def test_scale_checker_rejects_semantic_mutations(tmp_path: Path) -> None:
    mutations = {
        "missing boundary": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"].pop(17),
        "duplicate identifier": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"].__setitem__(1, {**output["artifacts"]["generated_test_cases"]["test_cases"][1], "id": "TC-0001"}),
        "wrong coverage": lambda output: output["artifacts"]["generated_test_cases"]["coverage"].__setitem__(6, {"requirement_id": "REQ-1011", "test_case_ids": ["TC-0007"]}),
        "wrong oracle": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][20]["steps"].__setitem__(0, {**output["artifacts"]["generated_test_cases"]["test_cases"][20]["steps"][0], "expected_result": "HTTP 200 OK"}),
        "invented role": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][30].__setitem__("title", "claims_adjuster creates a shipment"),
        "warning drift": lambda output: output.__setitem__("warnings", ["claims_adjuster policy is missing"]),
        "appended title behavior": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "create a valid shipment and publish an audit log"),
        "appended precondition behavior": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("preconditions", ["A valid shipment creation payload is available.", "An audit log is configured."]),
    }
    for name, mutate in mutations.items():
        output = copy.deepcopy(_canonical_output())
        mutate(output)
        exit_code, result = _run_checker(tmp_path / name.replace(" ", "_"), output)
        assert exit_code == 1, name
        assert result["status"] == "fail", name
        assert result["errors"], name


def test_scale_checker_rejects_missing_or_wrong_authorization_role(tmp_path: Path) -> None:
    mutations = {
        "role absent": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][30].update(title="Equivalent denial wording", preconditions=["A supported precondition is documented."]),
        "wrong role": lambda output: output["artifacts"]["generated_test_cases"]["test_cases"][30].update(title="auditor is denied", preconditions=["Authenticated as auditor."]),
    }
    for name, mutate in mutations.items():
        output = copy.deepcopy(_canonical_output())
        mutate(output)
        exit_code, result = _run_checker(tmp_path / name.replace(" ", "_"), output)
        assert exit_code == 1, name
        assert result["status"] == "fail", name
        assert any("exact denied role 'viewer'" in error for error in result["errors"]), name


def test_scale_checker_reports_shape_errors_separately(tmp_path: Path) -> None:
    output = _canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["requirement_ids"] = "REQ-1001"
    exit_code, result = _run_checker(tmp_path, output)
    assert exit_code == 2
    assert result["status"] == "error"
    assert result["errors"] == ["output test case 0.requirement_ids must be a list"]
