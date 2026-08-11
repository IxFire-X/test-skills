import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "build_trace_document.py"
TRACE_CHECK = ROOT / "tools" / "trace_check.py"
REAL_CHAIN = ROOT / "docs" / "to_do" / "real-chains" / "flask" / "chain-01" / "attempt-01"


def _load(name: str) -> dict:
    return json.loads((REAL_CHAIN / name).read_text(encoding="utf-8"))


@pytest.fixture
def inputs(tmp_path):
    requirements = _load("01-context-marker-output.json")
    test_cases = _load("02-tc-generator-output.json")
    automation = _load("04-tc-to-autotest-output.json")
    methods = automation["artifacts"]["generated_test_methods"]
    run_result = {
        "verdict": "PASS",
        "target": {
            "language": "python",
            "framework": "pytest+flask-test-client",
            "runner": "pytest",
            "command": "pytest -q tests/test_json_real_chain.py",
        },
        "environment": {
            "status": "ready",
            "interpreter": "python 3.11",
            "interpreter_path": str(Path(sys.executable).resolve()),
            "working_dir": "D:\\AI-Projects\\real-chain-projects\\flask",
            "missing": None,
        },
        "stats": {"total": 8, "passed": 8, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0.1},
        "failed_methods": [],
        "root_cause": [],
        "raw_output_excerpt": "8 passed",
        "ran_at": "2026-08-11T00:00:00Z",
        "exit_code": 0,
        "run_id": "RUN-FLASK-REAL-CHAIN",
        "execution_evidence": [
            {"run_id": "RUN-FLASK-REAL-CHAIN", "method_id": method["id"], "status": "passed"}
            for method in methods
        ],
        "evidence_authoritative": True,
    }

    paths = {}
    for name, payload in (
        ("requirements", requirements),
        ("test_cases", test_cases),
        ("automation", automation),
        ("run_result", run_result),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    paths["output"] = tmp_path / "trace-document.json"
    return paths, run_result


def _run(paths):
    return subprocess.run(
        [
            sys.executable,
            TOOL,
            "--requirements",
            paths["requirements"],
            "--test-cases",
            paths["test_cases"],
            "--automation-artifact",
            paths["automation"],
            "--run-result",
            paths["run_result"],
            "--output",
            paths["output"],
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_builds_schema_valid_execution_trace_from_existing_pipeline_artifacts(inputs):
    paths, run_result = inputs
    result = _run(paths)

    assert result.returncode == 0, result.stderr or result.stdout
    trace = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert [item["id"] for item in trace["requirements"]] == [f"REQ-{number:04d}" for number in range(1, 9)]
    assert [item["id"] for item in trace["test_cases"]] == [f"TC-{number:04d}" for number in range(1, 9)]
    assert len(trace["generated_files"]) == 1
    assert len(trace["methods"]) == 8
    assert len(trace["trace_map"]) == 8
    assert trace["execution"]["evidence"] == run_result["execution_evidence"]
    assert trace["final_verdict"] == "PASS"

    checked = subprocess.run(
        [sys.executable, TRACE_CHECK, paths["output"], "--require-execution"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert checked.returncode == 0, checked.stdout


@pytest.mark.parametrize("mutation", ["missing", "foreign"])
def test_rejects_incomplete_or_fabricated_runner_evidence_without_writing_output(inputs, mutation):
    paths, run_result = inputs
    broken = copy.deepcopy(run_result)
    if mutation == "missing":
        broken["execution_evidence"].pop()
    else:
        broken["execution_evidence"][0]["method_id"] = "METHOD-FOREIGN"
    paths["run_result"].write_text(json.dumps(broken), encoding="utf-8")

    result = _run(paths)

    assert result.returncode == 1
    assert not paths["output"].exists()
    report = json.loads(result.stdout)
    assert report["status"] == "invalid"
    assert report["errors"]


def test_rejects_requirement_drift_between_context_and_test_cases(inputs):
    paths, _ = inputs
    document = json.loads(paths["test_cases"].read_text(encoding="utf-8"))
    document["artifacts"]["generated_test_cases"]["requirements"][0]["provenance"] = ["foreign:1"]
    paths["test_cases"].write_text(json.dumps(document), encoding="utf-8")

    result = _run(paths)

    assert result.returncode == 1
    assert not paths["output"].exists()
    assert "requirements drift" in result.stdout
