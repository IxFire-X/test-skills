import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs" / "to_do" / "skill-tests" / "tc-generator" / "09-blind-scale-acceptance"
INPUT = BASE / "input" / "context-marker-output.json"
PROMPT = BASE / "evaluator-prompt.txt"
ORACLE = BASE / "oracle" / "expected-atoms.json"
CHECKER = BASE / "check_output.py"
SCHEMA = ROOT / "schemas" / "context-marker-output.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "tc-generator-output.schema.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checker_module():
    spec = importlib.util.spec_from_file_location("blind_scale_checker", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def canonical_output() -> dict:
    oracle = load(ORACLE)
    module = checker_module()
    atoms = module.atoms_from_oracle(oracle)
    requirements = load(INPUT)["artifacts"]["analytics_documentation"]["requirements"]
    cases = []
    for index, atom in enumerate(atoms, 1):
        role_text = f"The {atom.role} actor is authenticated."
        cases.append({
            "id": f"TC-{index:04d}", "requirement_ids": [atom.requirement_id],
            "title": f"Exercise documented behavior for {atom.requirement_id}.",
            "categories": list(atom.categories), "priority": "HIGH", "preconditions": [role_text],
            "test_data": list(atom.allowed_test_data[-1]),
            "steps": [{"order": 1, "action": f"{atom.method} {atom.path}", "expected_result": f"HTTP {atom.status} {atom.code}"}],
            "expected_outcome": f"The documented result is HTTP {atom.status} with code {atom.code}.",
        })
    return {"schema_version": "2.1.0", "stage": "tc-generator", "artifacts": {"generated_test_cases": {
        "requirements": requirements, "test_cases": cases,
        "coverage": [{"requirement_id": requirement["id"], "test_case_ids": [case["id"] for case in cases if case["requirement_ids"] == [requirement["id"]]]} for requirement in requirements],
    }}, "warnings": load(INPUT)["warnings"]}


def run_checker(tmp_path: Path, output: object) -> tuple[int, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "output.json"
    output_path.write_text(json.dumps(output), encoding="utf-8")
    result = subprocess.run([sys.executable, str(CHECKER), "--input", str(INPUT), "--oracle", str(ORACLE), "--output", str(output_path)], capture_output=True, text=True, check=False)
    return result.returncode, json.loads(result.stdout)


def assert_schema_valid(tmp_path: Path, output: object) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "schema-output.json"
    output_path.write_text(json.dumps(output), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_artifact.py"), str(OUTPUT_SCHEMA), str(output_path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout


def test_prompt_is_blind_and_never_names_oracle_surfaces() -> None:
    prompt = PROMPT.read_text(encoding="utf-8")
    forbidden = ("18", "36", "6/12/12/6", "TC-0036", "matrix", "checker", "tests", "expected-atoms")
    assert not any(token.lower() in prompt.lower() for token in forbidden)
    assert "Determine the necessary suite yourself" in prompt


def test_input_is_schema_valid_and_new_domain() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_artifact.py"), str(SCHEMA), str(INPUT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout
    input_data = load(INPUT)
    serialized = json.dumps(input_data, sort_keys=True)
    assert "bulk-shipment" not in serialized
    assert "subscription-invoicing" in serialized
    assert "expected_total" not in serialized


def test_hidden_oracle_has_expected_scale_but_prompt_does_not() -> None:
    oracle = load(ORACLE)
    module = checker_module()
    assert oracle["expected_total"] == 36
    assert len(module.atoms_from_oracle(oracle)) == 36


def test_canonical_output_uses_ordered_ids_and_representative_happy_and_denial_data() -> None:
    output = canonical_output()
    cases = output["artifacts"]["generated_test_cases"]["test_cases"]
    assert [case["id"] for case in cases] == [f"TC-{index:04d}" for index in range(1, 37)]
    assert [case["categories"] for case in cases] == [
        *([["positive", "functional"]] * 6),
        *([["positive", "boundary"]] * 12),
        *([["negative", "boundary"]] * 12),
        *([["negative", "authorization"]] * 6),
    ]
    assert cases[0]["test_data"] == ["seat_count: 2", "trial_days: 1"]
    assert cases[-1]["test_data"] == []


def test_checker_accepts_free_worded_canonical_output(tmp_path: Path) -> None:
    exit_code, result = run_checker(tmp_path, canonical_output())
    assert exit_code == 0, result
    assert result["status"] == "pass"
    assert result["errors"] == []


def test_title_paraphrase_remains_valid(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["title"] = "Create the subscription using an otherwise valid request."
    exit_code, result = run_checker(tmp_path, output)
    assert exit_code == 0
    assert result["status"] == "pass"


def test_checker_rejects_missing_duplicate_extra_coverage_oracle_and_roles(tmp_path: Path) -> None:
    mutations = {
        "missing atom": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"].pop(10),
        "duplicate atom": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"].__setitem__(1, copy.deepcopy(out["artifacts"]["generated_test_cases"]["test_cases"][0]) | {"id": "TC-0099"}),
        "extra atom": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"].append(copy.deepcopy(out["artifacts"]["generated_test_cases"]["test_cases"][0]) | {"id": "TC-0100"}),
        "wrong coverage": lambda out: out["artifacts"]["generated_test_cases"]["coverage"].__setitem__(0, {"requirement_id": "REQ-2001", "test_case_ids": ["TC-missing"]}),
        "wrong oracle": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0]["steps"].__setitem__(0, {"order": 1, "action": "POST /api/v2/subscriptions", "expected_result": "HTTP 200 PLAN_CHANGED"}),
        "empty happy data": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("test_data", []),
        "wrong representative": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("test_data", ["seat_count: 12", "trial_days: 14"]),
        "interleaved boundary order": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"].__setitem__(6, copy.deepcopy(out["artifacts"]["generated_test_cases"]["test_cases"][18]) | {"id": "TC-0007"}),
        "wrong role": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][-1].__setitem__("preconditions", ["The analyst actor is authenticated."]),
        "claims adjuster": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "claims_adjuster creates a subscription"),
        "foreign field": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "invoice_line_count is mentioned"),
        "foreign status": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "HTTP 403 is mentioned"),
        "foreign code": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "FORBIDDEN is mentioned"),
        "foreign path": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "Use /api/v2/invoices/{invoice_id}/void"),
        "novel role": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("preconditions", ["Authenticated as revenue_operator."]),
        "novel path": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "Inspect /api/v2/subscriptions/unlisted"),
        "novel field": lambda out: out["artifacts"]["generated_test_cases"]["test_cases"][0].__setitem__("title", "unlisted_count: 12 is supplied"),
    }
    for label, mutate in mutations.items():
        output = canonical_output()
        mutate(output)
        assert_schema_valid(tmp_path / ("schema_" + label.replace(" ", "_")), output)
        exit_code, result = run_checker(tmp_path / label.replace(" ", "_"), output)
        assert exit_code == 1, (label, result)
        assert result["status"] == "fail", (label, result)


def test_checker_reports_shape_with_exit_two(tmp_path: Path) -> None:
    output = canonical_output()
    output["artifacts"]["generated_test_cases"]["test_cases"][0]["requirement_ids"] = "REQ-2001"
    exit_code, result = run_checker(tmp_path, output)
    assert exit_code == 2
    assert result["status"] == "error"
    assert result["errors"] == ["output test case 0.requirement_ids must be a list"]
