"""Regression coverage for dynamic tool imports outside the pack cwd."""

import json
import sys
import types
from pathlib import Path

import pytest
from conftest import ROOT, load_tool


def test_load_tool_scopes_local_helpers_without_leaking_import_state(monkeypatch):
    """Catches parent-cwd imports resolving a preloaded host json_cli or mutating sys.path."""
    sentinel = type("SentinelParser", (), {})
    fake = types.ModuleType("json_cli")
    fake.JsonArgumentParser = sentinel
    monkeypatch.chdir(ROOT.parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and Path(entry).resolve() not in {ROOT.resolve(), (ROOT / "tools").resolve()}])
    monkeypatch.setitem(sys.modules, "json_cli", fake)
    before_path = list(sys.path)

    module = load_tool("contract_check")

    assert module.__name__ == "contract_check"
    assert module.JsonArgumentParser is not sentinel
    assert sys.path == before_path
    assert sys.modules["json_cli"] is fake


def test_load_tool_binds_local_deferred_siblings_without_leaking_import_state(monkeypatch, tmp_path):
    """Catches post-load calls resolving preloaded host contract and runner modules."""
    fake_contract_check = types.ModuleType("contract_check")
    fake_contract_check.validate_pipeline_contract = lambda *_args, **_kwargs: {"status": "failed"}
    fake_run_tests = types.ModuleType("run_tests")
    fake_run_tests.validate_execution_evidence = lambda *_args: []
    fake_json_cli = types.ModuleType("json_cli")
    fake_json_cli.JsonArgumentParser = type("SentinelParser", (), {})
    monkeypatch.chdir(ROOT.parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and Path(entry).resolve() not in {ROOT.resolve(), (ROOT / "tools").resolve()}])
    monkeypatch.setitem(sys.modules, "contract_check", fake_contract_check)
    monkeypatch.setitem(sys.modules, "run_tests", fake_run_tests)
    monkeypatch.setitem(sys.modules, "json_cli", fake_json_cli)
    before_path = list(sys.path)

    doctor = load_tool("doctor")
    validator = load_tool("validate_artifact")

    assert Path(doctor.JsonArgumentParser.error.__code__.co_filename).resolve() == ROOT / "tools" / "json_cli.py"
    assert Path(doctor.validate_pipeline_contract.__code__.co_filename).resolve() == ROOT / "tools" / "contract_check.py"
    assert Path(validator.validate_execution_evidence.__code__.co_filename).resolve() == ROOT / "tools" / "run_tests.py"
    assert doctor.inspect_environment(ROOT)["status"] == "PASS"

    schema = tmp_path / "renamed.schema.json"
    schema.write_text((ROOT / "schemas" / "run-tests-output.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    artifact = tmp_path / "invalid-run.json"
    artifact.write_text(json.dumps({
        "verdict": "PASS", "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": "pytest"},
        "environment": {"status": "ready"}, "stats": {"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0},
        "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": "2026-01-01T00:00:00Z", "exit_code": 0,
        "run_id": "RUN-1", "evidence_authoritative": True,
        "execution_evidence": [{"run_id": "RUN-other", "method_id": "METHOD-1", "status": "passed"}],
    }), encoding="utf-8")
    code, report = validator.validate(str(schema), str(artifact))

    assert code == 1
    assert report["status"] == "invalid"
    assert "run_id" in report["errors"][0]["message"]
    assert sys.path == before_path
    assert sys.modules["json_cli"] is fake_json_cli
    assert sys.modules["contract_check"] is fake_contract_check
    assert sys.modules["run_tests"] is fake_run_tests


def test_load_tool_restores_host_import_state_after_execution_error(monkeypatch):
    """Catches a failed dynamic import leaking temporary module or path state."""
    fake = types.ModuleType("contract_check")
    monkeypatch.setitem(sys.modules, "contract_check", fake)
    before_path = list(sys.path)

    with pytest.raises(FileNotFoundError):
        load_tool("missing_tool")

    assert sys.path == before_path
    assert sys.modules["contract_check"] is fake
