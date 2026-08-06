"""Portable subprocess smoke coverage for every deterministic runtime CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _run_cli(root: Path, tool: str, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run a runtime CLI through the current interpreter with bounded output."""
    return subprocess.run(
        [sys.executable, str(root / "tools" / tool), *arguments],
        capture_output=True,
        text=False,
        check=False,
        timeout=30,
    )


def _stdout_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    """Decode contract-bearing stdout strictly so malformed JSON bytes cannot be repaired."""
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        error.add_note("CLI stdout must be strict UTF-8 JSON; stderr is diagnostic-only.")
        raise


def _stderr_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    """Decode non-contract diagnostic stderr without masking stdout contract failures."""
    return completed.stderr.decode("utf-8", errors="replace")


def _json_output(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    return json.loads(_stdout_text(completed))


def _project_tree_snapshot(root: Path) -> tuple[tuple[str, ...], tuple[tuple[str, bytes], ...]]:
    """Return every relative directory and every relative file with its exact bytes."""
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    directories = tuple(path.relative_to(root).as_posix() for path in paths if path.is_dir())
    files = tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in paths
        if path.is_file()
    )
    return directories, files


def _schema(root: Path, name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((root / "schemas" / name).read_text(encoding="utf-8")))


def _valid_trace() -> dict[str, object]:
    return {
        "schema_version": "2.1.0",
        "requirements": [{"id": "REQ-1", "provenance": ["acceptance smoke"]}],
        "test_cases": [{"id": "TC-1", "requirement_ids": ["REQ-1"]}],
        "generated_files": [{"id": "FILE-1", "path": "tests/test_demo.py"}],
        "methods": [{"id": "METHOD-1", "file_id": "FILE-1", "name": "test_demo", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"]}],
        "trace_map": [{"requirement_id": "REQ-1", "test_case_id": "TC-1", "file_id": "FILE-1", "method_id": "METHOD-1"}],
        "execution_required": True,
        "execution": {
            "verdict": "PASS",
            "reason": "acceptance test passed",
            "command": "pytest -q",
            "runner": "pytest",
            "exit_code": 0,
            "evidence": [{"run_id": "RUN-1", "method_id": "METHOD-1", "status": "passed"}],
            "allowed_skips": [],
        },
        "final_verdict": "PASS",
    }


def test_run_cli_keeps_stdout_as_raw_bytes(root):
    """Catches replacement decoding before smoke assertions can validate CLI stdout."""
    completed = _run_cli(root, "doctor.py", "--root", str(root))

    assert isinstance(completed.stdout, bytes)
    assert isinstance(completed.stderr, bytes)


def test_strict_stdout_rejects_invalid_utf8_bytes():
    """Catches malformed stdout being repaired before its JSON contract is checked."""
    completed = subprocess.CompletedProcess(
        args=["fake-cli"], returncode=0, stdout=b'{"value":"\xff"}', stderr=b"",
    )

    with pytest.raises(UnicodeDecodeError, match="invalid start byte"):
        _stdout_text(completed)


def test_project_tree_snapshot_detects_a_single_file_mutation(tmp_path):
    """Catches a snapshot implementation that ignores changed fixture file bytes."""
    source = tmp_path / "src" / "api.py"
    source.parent.mkdir()
    source.write_bytes(b"before")
    before = _project_tree_snapshot(tmp_path)

    source.write_bytes(b"after")

    assert before != _project_tree_snapshot(tmp_path)


def test_doctor_and_contract_tools_report_ready_portable_pack(root):
    """Catches runtime CLIs that stop returning successful machine-readable pack checks."""
    doctor = _run_cli(root, "doctor.py", "--root", str(root))
    contract = _run_cli(root, "contract_check.py", "--root", str(root), "--full")
    rendered = _run_cli(root, "render_contract_docs.py", "--root", str(root), "--check")

    assert doctor.returncode == 0
    assert _json_output(doctor)["status"] == "PASS"
    assert contract.returncode == 0
    assert _json_output(contract)["status"] == "passed"
    assert rendered.returncode == 0
    assert _stdout_text(rendered) == ""
    assert _stderr_text(rendered) == ""


def test_scan_project_emits_schema_valid_json_without_persistent_project_writes(root, tmp_path):
    """Catches a read-only scan leaking files outside its explicit confined output mode."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'smoke-demo'\ndependencies = ['fastapi']\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    source = tmp_path / "src" / "api.py"
    source.parent.mkdir()
    source.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    before = _project_tree_snapshot(tmp_path)

    completed = _run_cli(root, "scan_project.py", "--project", str(tmp_path), "--target", "src/api.py")
    report = _json_output(completed)

    assert completed.returncode == 0
    assert list(_schema(root, "scan-project-output.schema.json").iter_errors(report)) == []
    assert report["status"] == "success"
    assert report["files_extracted"] == ["src/api.py"]
    assert not (tmp_path / ".skillsrc").exists()
    assert _project_tree_snapshot(tmp_path) == before


def test_run_tests_executes_a_real_pytest_case_and_reports_its_count(root, tmp_path):
    """Catches a PASS report that does not represent an actually discovered pytest test."""
    test_file = tmp_path / "test_smoke.py"
    test_file.write_text("def test_smoke():\n    assert 2 + 2 == 4\n", encoding="utf-8")

    completed = _run_cli(
        root,
        "run_tests.py",
        "--project",
        str(tmp_path),
        "--language",
        "python",
        "--python-executable",
        sys.executable,
        "--pytest-target",
        str(test_file),
    )
    report = _json_output(completed)

    assert completed.returncode == 0
    assert list(_schema(root, "run-tests-output.schema.json").iter_errors(report)) == []
    assert report["verdict"] == "PASS"
    assert report["stats"]["total"] == 1
    assert report["stats"]["passed"] == 1


def test_run_tests_emits_non_ascii_not_runnable_json_as_utf8(root, tmp_path):
    """Catches Windows console-code-page bytes in runner JSON error output."""
    completed = _run_cli(root, "run_tests.py", "--project", str(tmp_path), "--language", "go")
    report = _json_output(completed)

    assert completed.returncode == 2
    assert report["verdict"] == "NOT_RUNNABLE"
    assert "Раннер" in report["root_cause"][0]


def test_validate_artifact_reports_valid_and_invalid_json(root, tmp_path):
    """Catches artifact validation that loses its 0/1 validity distinction."""
    schema = tmp_path / "artifact.schema.json"
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    schema.write_text('{"type":"object","required":["id"],"properties":{"id":{"type":"string"}}}', encoding="utf-8")
    valid.write_text('{"id":"artifact-1"}', encoding="utf-8")
    invalid.write_text("{}", encoding="utf-8")

    accepted = _run_cli(root, "validate_artifact.py", str(schema), str(valid))
    rejected = _run_cli(root, "validate_artifact.py", str(schema), str(invalid))

    assert accepted.returncode == 0
    assert _json_output(accepted)["status"] == "valid"
    assert rejected.returncode == 1
    assert _json_output(rejected)["status"] == "invalid"


def test_trace_check_accepts_a_real_schema_valid_execution_trace(root, tmp_path):
    """Catches trace validation losing the executable PASS path for a complete document."""
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps(_valid_trace()), encoding="utf-8")

    completed = _run_cli(root, "trace_check.py", str(trace), "--require-execution")
    report = _json_output(completed)

    assert completed.returncode == 0
    assert report["valid"] is True
    assert report["trace_audit"]["verdict"] == "PASS"


def test_validate_artifact_uses_canonical_run_output_schema_id_after_rename(root, tmp_path):
    """Catches semantic run-output validation being accidentally coupled to the schema filename."""
    renamed = tmp_path / "renamed.schema.json"
    renamed.write_text((root / "schemas" / "run-tests-output.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    artifact = tmp_path / "run.json"
    artifact.write_text(json.dumps({
        "verdict": "PASS", "target": {"language": "python", "framework": "pytest", "runner": "pytest", "command": "pytest"},
        "environment": {"status": "ready"}, "stats": {"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "duration_sec": 0},
        "failed_methods": None, "root_cause": None, "raw_output_excerpt": None, "ran_at": "2026-01-01T00:00:00Z", "exit_code": 0,
        "run_id": "RUN-1", "evidence_authoritative": True,
        "execution_evidence": [{"run_id": "RUN-other", "method_id": "METHOD-1", "status": "passed"}],
    }), encoding="utf-8")

    completed = _run_cli(root, "validate_artifact.py", str(renamed), str(artifact))

    assert completed.returncode == 1
    assert _json_output(completed)["status"] == "invalid"


@pytest.mark.parametrize("tool", [
    "doctor.py", "contract_check.py", "render_contract_docs.py", "scan_project.py",
    "run_tests.py", "validate_artifact.py", "trace_check.py",
])
def test_every_runtime_cli_emits_utf8_json_for_invalid_arguments(root, tool):
    """Catches argparse prose becoming the only response for machine callers."""
    completed = _run_cli(root, tool, "--definitely-invalid")

    assert completed.returncode == 2
    payload = _json_output(completed)
    assert isinstance(payload, dict)
