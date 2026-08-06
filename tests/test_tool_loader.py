"""Regression coverage for isolated dynamic tool imports."""

import importlib
import importlib.machinery
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from conftest import ROOT, TOOL_NAMESPACE, TOOL_ROOT, load_tool


def _namespace_snapshot():
    prefix = f"{TOOL_NAMESPACE}."
    return {name: module for name, module in sys.modules.items() if name == TOOL_NAMESPACE or name.startswith(prefix)}


def _local_source(value):
    return Path(value.__code__.co_filename).resolve()


def _host_modules(monkeypatch):
    fake_json_cli = types.ModuleType("json_cli")
    fake_json_cli.JsonArgumentParser = type("HostParser", (), {})
    fake_contract_check = types.ModuleType("contract_check")
    fake_contract_check.validate_pipeline_contract = lambda *_args, **_kwargs: {"status": "failed"}
    fake_run_tests = types.ModuleType("run_tests")
    fake_run_tests.validate_execution_evidence = lambda *_args: []
    fake_tools = types.ModuleType("tools")
    for name, module in {
        "json_cli": fake_json_cli,
        "contract_check": fake_contract_check,
        "run_tests": fake_run_tests,
        "tools": fake_tools,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return fake_json_cli, fake_contract_check, fake_run_tests, fake_tools


def test_load_tool_uses_private_local_namespace_without_host_leaks(monkeypatch):
    """Catches host sibling modules or path mutation influencing a dynamic load."""
    host = _host_modules(monkeypatch)
    monkeypatch.chdir(ROOT.parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and Path(entry).resolve() not in {ROOT.resolve(), TOOL_ROOT.resolve()}])
    before_path = list(sys.path)

    module = load_tool("contract_check")

    assert module.__name__ == f"{TOOL_NAMESPACE}.contract_check"
    assert _local_source(module.JsonArgumentParser.error) == TOOL_ROOT / "json_cli.py"
    assert sys.path == before_path
    assert tuple(sys.modules[name] for name in ("json_cli", "contract_check", "run_tests", "tools")) == host
    assert set(_namespace_snapshot()) == {TOOL_NAMESPACE, f"{TOOL_NAMESPACE}.contract_check", f"{TOOL_NAMESPACE}.json_cli"}


def test_deferred_sibling_imports_bind_local_modules_after_load(monkeypatch, tmp_path):
    """Catches lazy doctor and validator imports resolving host modules after loading."""
    host = _host_modules(monkeypatch)
    monkeypatch.chdir(ROOT.parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and Path(entry).resolve() not in {ROOT.resolve(), TOOL_ROOT.resolve()}])
    before_path = list(sys.path)

    doctor = load_tool("doctor")
    assert f"{TOOL_NAMESPACE}.contract_check" not in sys.modules
    assert doctor.inspect_environment(ROOT)["status"] == "PASS"
    contract_check = sys.modules[f"{TOOL_NAMESPACE}.contract_check"]
    assert _local_source(contract_check.validate_pipeline_contract) == TOOL_ROOT / "contract_check.py"

    validator = load_tool("validate_artifact")
    assert f"{TOOL_NAMESPACE}.run_tests" not in sys.modules
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
    runner = sys.modules[f"{TOOL_NAMESPACE}.run_tests"]
    assert _local_source(runner.validate_execution_evidence) == TOOL_ROOT / "run_tests.py"
    assert sys.path == before_path
    assert tuple(sys.modules[name] for name in ("json_cli", "contract_check", "run_tests", "tools")) == host


def test_doctor_reports_missing_jsonschema_without_raising(monkeypatch):
    """Catches a lazy contract dependency becoming an unreportable doctor crash."""
    doctor = load_tool("doctor")
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    report = doctor.inspect_environment(ROOT)

    assert report["status"] == "NOT_RUNNABLE"
    assert "invalid:pipeline_contract" in report["integrity"]["missing"]


def test_load_tool_freshly_reloads_private_namespace():
    """Catches one dynamically loaded tool leaking mutated globals into its next load."""
    first = load_tool("contract_check")
    first.JsonArgumentParser = type("StaleParser", (), {})

    second = load_tool("contract_check")

    assert second is not first
    assert second.JsonArgumentParser is not first.JsonArgumentParser
    assert sys.modules[f"{TOOL_NAMESPACE}.contract_check"] is second


def test_load_tool_restores_namespace_snapshot_after_partial_sibling_failure(monkeypatch):
    """Catches failed target execution leaking a freshly imported local sibling."""
    load_tool("contract_check")
    snapshot = _namespace_snapshot()

    class FailingLoader:
        def create_module(self, _spec):
            return None

        def exec_module(self, module):
            importlib.import_module(f"{module.__package__}.json_cli")
            raise RuntimeError("forced failure after sibling import")

    monkeypatch.setattr(
        "conftest.importlib.util.spec_from_file_location",
        lambda name, _path: importlib.machinery.ModuleSpec(name, FailingLoader()),
    )

    with pytest.raises(RuntimeError, match="forced failure"):
        load_tool("doctor")

    assert _namespace_snapshot() == snapshot
    assert all(_namespace_snapshot()[name] is module for name, module in snapshot.items())


def test_load_tool_rejects_hostile_namespace_path_without_mutating_it(monkeypatch, tmp_path):
    """Catches a preloaded private namespace pointing at a foreign directory."""
    hostile = types.ModuleType(TOOL_NAMESPACE)
    hostile.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, TOOL_NAMESPACE, hostile)
    before_path = list(sys.path)

    with pytest.raises(ImportError, match="hostile"):
        load_tool("contract_check")

    assert sys.modules[TOOL_NAMESPACE] is hostile
    assert sys.path == before_path


@pytest.mark.parametrize("tool", ["contract_check.py", "doctor.py"])
def test_representative_direct_clis_remain_supported(tool):
    """Catches package-relative sibling imports breaking direct script execution."""
    completed = subprocess.run(
        [sys.executable, str(TOOL_ROOT / tool), "--root", str(ROOT)],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] in {"PASS", "passed"}
