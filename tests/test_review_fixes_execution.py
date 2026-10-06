"""Regression tests for the execution findings of the 2026-10-05 review.

Covered IDs: B3 (post-run drift), B4 (compile/collect gate), R13/M11 (process
stop proof), M09, M10, M12, M13, M14, M15, M16, M18, M19.

Tests that build a durable run are slow; they are kept to one per behaviour.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import MODULE_PYTHON

SELECTOR = "tests/test_generated.py::test_selected"
PASS_REPORT = b"<testsuite tests='1'><testcase classname='tests.test_generated' name='test_selected'/></testsuite>"
ZERO_REPORT = b"<testsuite tests='0' errors='0' failures='0' skipped='0'></testsuite>"


# --- helpers -----------------------------------------------------------------


class _LightRun:
    """One durable local-pilot attempt with a materialized generated test and a fake runtime."""

    def __init__(self, tmp_path: Path) -> None:
        from tests.test_project_native_pytest import _reviewed_inputs
        from tools.execution_adapters import PYTEST, ExecutionRequest, command_for

        self.project = (tmp_path / "project").resolve()
        self.project.mkdir()
        (self.project / "tests").mkdir()
        (
            self.document, self.automation, self.review, self.boundary, self.run_root,
            self.attempt_id, self.authorization, self.delta,
        ) = _reviewed_inputs(self.project)
        executable = str(self.project / MODULE_PYTHON)
        reports, argv = command_for(PYTEST, executable, "default", (SELECTOR,))
        self.request = ExecutionRequest(
            PYTEST, executable, argv, str(self.project), (SELECTOR,), 600, reports,
            ("PROJECT_NATIVE_ENV",), "default",
        )
        self.generated = self.project / "tests" / "test_generated.py"

    def gate_request(self):
        from tools.run_tests import build_closed_gate_request

        return build_closed_gate_request(self.request)

    def write_report(self, content: bytes) -> None:
        target = self.project / "test-results" / "pytest.xml"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)

    def run(self, monkeypatch: pytest.MonkeyPatch, fake, **extra):
        from tools.execution_adapters import request_digest
        from tools.pilot_state import claim_execution_start
        from tools.run_tests import run_tests_v5

        monkeypatch.setattr("tools.execution_adapters.invoke_request", fake)
        return run_tests_v5(
            self.request, self.authorization, self.document, self.automation, self.review,
            host_isolation_receipt=self.boundary, generated_delta_receipt=self.delta,
            run_root=self.run_root, attempt_id=self.attempt_id,
            on_execution_start=lambda value: claim_execution_start(
                self.run_root, self.attempt_id, request_digest(value),
            ),
            **extra,
        )

    def finalize(self, report: dict) -> dict:
        from tools.finalize_attempt import finalize_durable_execution_attempt
        from tools.run_pipeline import _publish_execution_receipt

        _publish_execution_receipt(
            {"run_root": self.run_root, "attempt": {"attempt_id": self.attempt_id}}, report,
        )
        return finalize_durable_execution_attempt(self.run_root, self.attempt_id)

    def dispositions(self) -> list[dict]:
        from tools.pilot_state import read_attempt_receipt

        return read_attempt_receipt(
            self.run_root, self.attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
        )["record"]["payload"]["files"]

    def create_child(self, retry_reason: str) -> dict:
        from tests.helpers import phase_two_baseline
        from tools.pilot_state import create_attempt

        identity = {
            "project": str(self.project), "module": ".", "policy_profile": "local-pilot-v1",
            "parent_attempt_id": self.attempt_id, "retry_reason": retry_reason,
        }
        return dict(create_attempt(self.run_root, identity, phase_two_baseline(self.run_root, self.project, identity)))


def _kinds(report: dict) -> list[str]:
    return [row["kind"] for row in report["process_evidence"]]


def _classify(**overrides):
    from tools.execution_adapters import PYTEST
    from tools.run_tests import classify_process_result

    facts = {
        "adapter_id": PYTEST, "outcome_kind": "EXIT", "exit_code": 0, "required_count": 1,
        "evidence_statuses": ["PASSED"], "match_errors": [], "has_report": True, "zero_report": False,
    }
    facts.update(overrides)
    return classify_process_result(**facts)


# --- M12: launch failure -------------------------------------------------------


def test_m12_launch_oserror_is_not_runnable_launch_failed_and_cleans_up(tmp_path, monkeypatch):
    """A process that cannot be launched never ran the tests: NOT_RUNNABLE, not UNKNOWN."""
    from tools.run_tests import ProcessOutcome, run_subprocess

    missing = run_subprocess([str(tmp_path / "no-such-launcher")], tmp_path, timeout=5)
    assert (missing.kind, missing.exit_code) == ("OS_ERROR", 127)

    run = _LightRun(tmp_path)
    report = run.run(monkeypatch, lambda *_args: ProcessOutcome(127, "", "", "OS_ERROR"))

    assert report["verdict"] == "NOT_RUNNABLE"
    assert report["run_id"] is not None
    assert _kinds(report) == ["LAUNCH_FAILED"]
    assert [row["code"] for row in report["diagnostics"]] == ["LAUNCH_FAILED"]
    assert report["evidence_authoritative"] is False

    finalized = run.finalize(report)
    assert finalized["result"]["verification"] == "NOT_RUNNABLE"
    assert finalized["result"]["reason_code"] == "LAUNCH_FAILED"
    assert finalized["result"]["trace_valid"] is True
    assert [row["disposition"] for row in run.dispositions()] == ["CLEANED"]
    assert not run.generated.exists()
    assert run.create_child("launch-fixed")["parent_attempt_id"] == run.attempt_id


# --- M14: distinct reason codes --------------------------------------------------


def test_m14_nonzero_exit_with_green_report_has_its_own_reason(tmp_path, monkeypatch):
    from tools.run_tests import ProcessOutcome

    run = _LightRun(tmp_path)

    def green_report_but_failed_build(*_args):
        run.write_report(PASS_REPORT)
        return ProcessOutcome(1, "coverage gate failed", "")

    report = run.run(monkeypatch, green_report_but_failed_build)

    assert report["verdict"] == "UNKNOWN"
    assert _kinds(report) == ["NONZERO_EXIT_GREEN_REPORT"]


def test_m14_reason_matrix_separates_missing_invalid_and_nonzero_exit():
    assert _classify(exit_code=1) == ("UNKNOWN", "NONZERO_EXIT_GREEN_REPORT")
    # No report at all is JUNIT_MISSING (it used to be reported as JUNIT_INVALID).
    assert _classify(exit_code=1, evidence_statuses=[], match_errors=["missing required pair"], has_report=False) == ("UNKNOWN", "JUNIT_MISSING")
    assert _classify(exit_code=0, evidence_statuses=[], match_errors=["missing required pair"], has_report=False) == ("UNKNOWN", "JUNIT_MISSING")
    assert _classify(exit_code=1, evidence_statuses=[], match_errors=["malformed JUnit testcase"]) == ("UNKNOWN", "JUNIT_INVALID")
    assert _classify() == ("PASS", None)
    # A failed product check stays FAIL, with or without a timeout.
    assert _classify(exit_code=1, evidence_statuses=["FAILED"]) == ("FAIL", None)
    assert _classify(outcome_kind="TIMEOUT", exit_code=124, evidence_statuses=["FAILED"]) == ("FAIL", None)
    assert _classify(outcome_kind="TIMEOUT", exit_code=124) == ("UNKNOWN", "TIMEOUT")
    assert _classify(post_execution_reason="BASELINE_DRIFT") == ("UNKNOWN", "BASELINE_DRIFT")


def test_m14_validator_accepts_each_new_process_kind_with_its_exit_rule():
    from tools.execution_adapters import PYTEST
    from tools.run_tests import validate_process_evidence

    source = {"source_digest": "sha256:" + "a" * 64}
    target = {"runner": "pytest", "command": PYTEST}

    def row(kind, error_class, cause):
        return {
            "run_id": "RUN-1", "source_digest": source["source_digest"], "kind": kind, "error_class": error_class,
            "exit_cause": cause, "command_profile": PYTEST, "duration_sec": 1.0, "stdout_tail": "", "stderr_tail": "",
        }

    valid = [
        (row("LAUNCH_FAILED", "LaunchFailed", "OS_ERROR"), 127),
        (row("GENERATED_TEST_INVALID", "GeneratedTestInvalid", "NONZERO_EXIT"), 2),
        (row("TESTS_DESELECTED", "TestsDeselected", "NONZERO_EXIT"), 5),
        (row("TESTS_DESELECTED", "TestsDeselected", "ZERO_EXIT"), 0),
        (row("NONZERO_EXIT_GREEN_REPORT", "NonzeroExitGreenReport", "NONZERO_EXIT"), 1),
        (row("ARTIFACT_PERSISTENCE_FAILED", "ArtifactPersistenceFailed", "ZERO_EXIT"), 0),
    ]
    for evidence, exit_code in valid:
        assert validate_process_evidence([evidence], "RUN-1", source, exit_code, 1.0, target) == [], evidence["kind"]
    invalid = [
        (row("LAUNCH_FAILED", "LaunchFailed", "NONZERO_EXIT"), 1),
        (row("GENERATED_TEST_INVALID", "GeneratedTestInvalid", "ZERO_EXIT"), 0),
        (row("NONZERO_EXIT_GREEN_REPORT", "NonzeroExitGreenReport", "ZERO_EXIT"), 0),
    ]
    for evidence, exit_code in invalid:
        assert validate_process_evidence([evidence], "RUN-1", source, exit_code, 1.0, target), evidence["kind"]


def test_new_reason_codes_are_listed_in_both_process_evidence_schemas():
    from tools.run_tests import _PROCESS_ERROR_CLASSES, STOP_PROOFS

    root = Path(__file__).resolve().parents[1] / "schemas"
    for name in ("run-tests-output.schema.json", "trace-document.schema.json"):
        definition = json.loads((root / name).read_text(encoding="utf-8"))["$defs"]["process_evidence"]["properties"]
        assert set(definition["kind"]["enum"]) == set(_PROCESS_ERROR_CLASSES), name
        assert set(definition["error_class"]["enum"]) == set(_PROCESS_ERROR_CLASSES.values()), name
        assert set(definition["stop_proof"]["enum"]) == set(STOP_PROOFS), name
    assert "WINDOWS_JOB_TERMINATED" in STOP_PROOFS


# --- M13: artifact persistence failure -------------------------------------------


def test_m13_artifact_persistence_failure_keeps_the_result(tmp_path, monkeypatch):
    """An exception while copying artifacts after the run must not lose the started execution."""
    from tools.run_tests import ProcessOutcome

    run = _LightRun(tmp_path)

    def passing(*_args):
        run.write_report(PASS_REPORT)
        return ProcessOutcome(0, "ok", "")

    def disk_full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("tools.pilot_state.publish_run_artifact_bytes", disk_full)
    report = run.run(monkeypatch, passing)

    assert report["verdict"] == "UNKNOWN"
    assert _kinds(report) == ["ARTIFACT_PERSISTENCE_FAILED"]
    assert report["execution"]["artifact_evidence"] == []
    assert report["execution_evidence"] == []


# --- M10: deselected tests ---------------------------------------------------------


def test_m10_deselected_explicit_nodeid_is_not_runnable_and_the_test_is_kept(tmp_path, monkeypatch):
    """`addopts = -m smoke` / PYTEST_ADDOPTS must not turn a valid test into FAIL and delete it."""
    from tools.run_tests import ProcessOutcome

    run = _LightRun(tmp_path)
    content = run.generated.read_bytes()

    def nothing_collected(*_args):
        run.write_report(ZERO_REPORT)
        return ProcessOutcome(5, "1 deselected", "")

    report = run.run(monkeypatch, nothing_collected)

    assert report["verdict"] == "NOT_RUNNABLE"
    assert _kinds(report) == ["TESTS_DESELECTED"]
    assert [row["code"] for row in report["diagnostics"]] == ["TESTS_DESELECTED"]

    finalized = run.finalize(report)
    assert finalized["result"]["verification"] == "NOT_RUNNABLE"
    assert finalized["result"]["reason_code"] == "TESTS_DESELECTED"
    assert finalized["result"]["accepted"] is False
    rows = run.dispositions()
    assert [(row["disposition"], row.get("reason_code")) for row in rows] == [("RETAINED", "TESTS_DESELECTED")]
    assert run.generated.read_bytes() == content


def test_m10_zero_collection_matrix_is_adapter_specific():
    from tools.execution_adapters import GRADLE, MAVEN

    zero = {"evidence_statuses": [], "match_errors": ["missing required pair"], "zero_report": True}
    assert _classify(exit_code=5, **zero) == ("NOT_RUNNABLE", "TESTS_DESELECTED")
    assert _classify(exit_code=0, **zero) == ("NOT_RUNNABLE", "TESTS_DESELECTED")
    # pytest usage error 4 with an empty report: the node ID itself was not found.
    assert _classify(exit_code=4, **zero) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    for adapter in (MAVEN, GRADLE):
        assert _classify(adapter_id=adapter, exit_code=0, **zero) == ("FAIL", "NO_TESTS_COLLECTED")


def test_m10_keep_policy_is_only_legal_for_recorded_deselection():
    from tools.generated_delta import GeneratedDeltaError, resolve_disposition_policy

    operation, requested, outcomes = resolve_disposition_policy("NOT_RUNNABLE", "MATERIALIZED", retain_pass=False, keep_deselected=True)
    assert (operation, requested) == ("RETAIN_IF_EXACT", "RETAINED")
    assert ("RETAINED", "TESTS_DESELECTED") in outcomes["EXACT"]
    # Any other NOT_RUNNABLE (gate failure, launch failure) still cleans the owned file.
    assert resolve_disposition_policy("NOT_RUNNABLE", "MATERIALIZED", retain_pass=False)[:2] == ("CLEAN_IF_EXACT", "CLEANED")
    assert resolve_disposition_policy("FAIL", "MATERIALIZED", retain_pass=False)[:2] == ("CLEAN_IF_EXACT", "CLEANED")
    with pytest.raises(GeneratedDeltaError):
        resolve_disposition_policy("FAIL", "MATERIALIZED", retain_pass=False, keep_deselected=True)


# --- M09: pytest rootdir -----------------------------------------------------------


def _python_compatibility(module_root: Path):
    from tools.run_tests import RunnerCompatibility

    pair = ("FILE-generated", "SYMBOL-generated")
    binding = {
        "path": module_root / "tests" / "test_generated.py", "relative_path": "tests/test_generated.py",
        "locator": {"kind": "python_module_function", "function_name": "test_selected"}, "node": "test_selected",
    }
    return RunnerCompatibility("READY", (pair,), {pair: binding}, {"FILE-generated": "sha256:" + "e" * 64}, ())


def test_m09_nested_module_with_parent_rootdir_is_matched(tmp_path):
    """pytest names the class relative to its rootdir, which may be a parent of the module."""
    from tools.run_tests import match_junit_cases, parse_junit_bytes

    module_root = tmp_path / "mono" / "services" / "child"
    compatibility = _python_compatibility(module_root)
    source = {"source_digest": "sha256:" + "a" * 64}

    def statuses(classname: str, file: str | None = None):
        attribute = "" if file is None else f" file='{file}'"
        report = f"<testsuite tests='1'><testcase classname='{classname}' name='test_selected'{attribute}/></testsuite>"
        rows, errors = match_junit_cases(parse_junit_bytes(report.encode()), compatibility, "RUN-1", source, strict=True)
        return [row["status"] for row in rows], errors

    assert statuses("tests.test_generated") == (["PASSED"], [])
    assert statuses("child.tests.test_generated") == (["PASSED"], [])
    assert statuses("services.child.tests.test_generated") == (["PASSED"], [])
    assert statuses("services.child.tests.test_generated", "services/child/tests/test_generated.py") == (["PASSED"], [])
    # A class outside any ancestor of the module is still foreign.
    assert statuses("other.child.tests.test_generated")[0] == []
    assert statuses("services.child.tests.test_generated", "tests/test_generated.py")[0] == []


def test_m09_real_pytest_with_config_in_parent_pyproject(tmp_path):
    from tools.run_tests import match_junit_cases, parse_junit_bytes

    module_root = tmp_path / "mono" / "services" / "child"
    (module_root / "tests").mkdir(parents=True)
    (tmp_path / "mono" / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = \"\"\n", encoding="utf-8")
    (module_root / "tests" / "test_generated.py").write_text("def test_selected():\n    assert True\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--junitxml", "test-results/pytest.xml", SELECTOR],
        cwd=module_root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")
    report = (module_root / "test-results" / "pytest.xml").read_bytes()
    assert b"services.child.tests.test_generated" in report

    rows, errors = match_junit_cases(
        parse_junit_bytes(report), _python_compatibility(module_root), "RUN-1",
        {"source_digest": "sha256:" + "a" * 64}, strict=True,
    )
    assert errors == []
    assert [row["status"] for row in rows] == ["PASSED"]


# --- B4: compile/collect gate --------------------------------------------------------


def test_b4_gate_failure_is_not_runnable_cleans_file_keeps_output_and_allows_child(tmp_path, monkeypatch):
    from tools.run_tests import ProcessOutcome

    run = _LightRun(tmp_path)
    calls: list[tuple[str, ...]] = []

    def failing_gate(request, _runner):
        calls.append(request.argv)
        assert "--collect-only" in request.argv, "the reviewed tests must not start after a failed gate"
        return ProcessOutcome(2, "ERROR collecting tests/test_generated.py\nE   ModuleNotFoundError: No module named 'missing'", "")

    report = run.run(monkeypatch, failing_gate, gate_request=run.gate_request())

    assert len(calls) == 1
    assert report["verdict"] == "NOT_RUNNABLE"
    assert _kinds(report) == ["GENERATED_TEST_INVALID"]
    assert report["exit_code"] == 2
    output = next(row for row in report["execution"]["artifact_evidence"] if row["kind"] == "runner_output")
    assert b"ModuleNotFoundError" in (run.run_root / output["path"]).read_bytes()

    finalized = run.finalize(report)
    assert finalized["result"]["verification"] == "NOT_RUNNABLE"
    assert finalized["result"]["reason_code"] == "GENERATED_TEST_INVALID"
    assert finalized["result"]["trace_valid"] is True
    assert [row["disposition"] for row in run.dispositions()] == ["CLEANED"]
    assert not run.generated.exists()
    assert (run.run_root / output["path"]).is_file()
    # NOT_RUNNABLE is not UNKNOWN: a child attempt (automation revision) is allowed.
    assert run.create_child("GENERATED_TEST_INVALID")["parent_attempt_id"] == run.attempt_id


def test_b4_gate_matrix_and_compile_errors_that_reach_the_main_run():
    from tools.execution_adapters import GRADLE, MAVEN, PYTEST
    from tools.run_tests import ProcessOutcome, gate_failure_kind

    assert gate_failure_kind(PYTEST, ProcessOutcome(0, "", "")) is None
    assert gate_failure_kind(PYTEST, ProcessOutcome(2, "", "")) == "GENERATED_TEST_INVALID"
    assert gate_failure_kind(PYTEST, ProcessOutcome(5, "", "")) == "TESTS_DESELECTED"
    assert gate_failure_kind(MAVEN, ProcessOutcome(1, "", "")) == "GENERATED_TEST_INVALID"
    assert gate_failure_kind(GRADLE, ProcessOutcome(5, "", "")) == "GENERATED_TEST_INVALID"
    assert gate_failure_kind(MAVEN, ProcessOutcome(127, "", "", "OS_ERROR")) == "LAUNCH_FAILED"
    assert gate_failure_kind(MAVEN, ProcessOutcome(124, "", "", "TIMEOUT")) == "TIMEOUT"

    assert _classify(gate_kind="GENERATED_TEST_INVALID", exit_code=1, evidence_statuses=[], has_report=False) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    assert _classify(gate_kind="TESTS_DESELECTED", exit_code=5, evidence_statuses=[], has_report=False) == ("NOT_RUNNABLE", "TESTS_DESELECTED")
    assert _classify(gate_kind="TIMEOUT", outcome_kind="TIMEOUT", exit_code=124, evidence_statuses=[], has_report=False) == ("UNKNOWN", "TIMEOUT")

    missing = {"evidence_statuses": [], "match_errors": ["missing required pair"], "has_report": False}
    # A compile or import error that slipped past the gate is NOT_RUNNABLE, not UNKNOWN.
    assert _classify(exit_code=4, collection_error=True, evidence_statuses=[], match_errors=["unmatched JUnit testcase"]) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    assert _classify(exit_code=2, output="ImportError while loading conftest '/p/conftest.py'.", **missing) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    assert _classify(adapter_id=MAVEN, exit_code=1, output="[ERROR] COMPILATION ERROR :\n[ERROR] FooTest.java:[3,8] cannot find symbol", **missing) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    assert _classify(adapter_id=GRADLE, exit_code=1, output="Execution failed for task ':app:compileTestJava'.", **missing) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID")
    # A build that failed for another reason stays ambiguous.
    assert _classify(adapter_id=MAVEN, exit_code=1, output="[ERROR] Could not resolve dependencies", **missing) == ("UNKNOWN", "JUNIT_MISSING")
    assert _classify(exit_code=3, output="INTERNALERROR> boom", **missing) == ("UNKNOWN", "JUNIT_MISSING")


def test_b4_gate_request_is_built_by_the_adapter_and_cannot_carry_foreign_argv(tmp_path):
    from dataclasses import replace

    from tools.execution_adapters import PYTEST, build_gate_command, build_request
    from tools.run_tests import RunnerInputError, build_closed_gate_request, gate_request_is_closed

    runtime = tmp_path / MODULE_PYTHON
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"fixture-runtime")
    runtime.chmod(0o755)
    module = {
        "id": "fixture", "root": ".",
        "test": {
            "framework": "pytest", "adapter_id": PYTEST, "interpreter": MODULE_PYTHON,
            "build_profile": "default", "adapter_parameters": {},
        },
    }
    closed = dict(module, module_root=str(tmp_path.resolve()), test={key: value for key, value in module["test"].items() if key != "framework"})
    request = build_request(PYTEST, closed, [{"selector": SELECTOR}])

    gate = build_closed_gate_request(request)

    assert gate == build_gate_command(PYTEST, closed, [{"selector": SELECTOR}])
    assert "--collect-only" in gate.argv and SELECTOR in gate.argv
    assert (gate.cwd, gate.executable, gate.timeout_seconds, gate.report_paths) == (request.cwd, request.executable, request.timeout_seconds, ())
    assert gate_request_is_closed(gate, request)
    assert not gate_request_is_closed(replace(gate, argv=(*gate.argv, "--pdb")), request)
    assert not gate_request_is_closed(replace(gate, cwd=str(tmp_path.parent)), request)
    assert not gate_request_is_closed(request, request)
    with pytest.raises(RunnerInputError):
        build_closed_gate_request({"argv": ["python", "-m", "pytest"]})


def _exec_project(tmp_path: Path, content: str, *, conftest_extra: str = ""):
    """A real pytest fixture project prepared up to (not including) `run_pipeline exec`."""
    from tests.test_project_native_pytest import _module_python, _reviewed_inputs

    project = tmp_path / "project"
    shutil.copytree(Path(__file__).parent / "fixtures" / "projects" / "python-pytest", project)
    if conftest_extra:
        with (project / "conftest.py").open("a", encoding="utf-8") as stream:
            stream.write(conftest_extra)
    _module_python(project)
    document, artifact, review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(
        project, generated_content=content,
    )
    inputs = run_root / "inputs"
    inputs.mkdir()
    values = {
        "canonical_document": document, "automation_artifact": artifact, "autotest_review": review,
        "authorization_receipt": authorization, "host_isolation_receipt": evidence, "generated_delta_receipt": delta,
    }
    paths = {}
    for key, value in values.items():
        paths[key] = str(inputs / f"{key}.json")
        Path(paths[key]).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    arguments = SimpleNamespace(
        project=str(project), run=run_root.name, module="python-pytest", language=None, target=None,
        executor="local", **paths,
    )
    return project, run_root, attempt_id, arguments


def test_b4_exec_runs_the_gate_and_an_import_error_never_stays_in_the_project(tmp_path, capsys, monkeypatch):
    """End to end: an uncollectable generated test is NOT_RUNNABLE and is removed from the project."""
    from tools.pilot_state import read_attempt_receipt, read_terminal_result
    from tools.run_pipeline import cmd_exec

    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    content = "import module_that_does_not_exist_anywhere\n\n\ndef test_selected():\n    assert True\n"
    project, run_root, attempt_id, arguments = _exec_project(tmp_path, content)

    code = cmd_exec(arguments)

    output = json.loads(capsys.readouterr().out)
    terminal = read_terminal_result(run_root, attempt_id)
    receipt = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert output["verdict"] == "NOT_RUNNABLE", output
    assert output["reason"] == "GENERATED_TEST_INVALID"
    assert output["automation_revision_allowed"] is True
    assert code == 2
    assert (terminal["verification"], terminal["reason_code"], terminal["accepted"]) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID", False)
    assert _kinds(receipt) == ["GENERATED_TEST_INVALID"]
    assert "--collect-only" not in receipt["execution"]["argv"]
    assert not (project / "tests" / "test_generated.py").exists()
    saved = next(row for row in receipt["execution"]["artifact_evidence"] if row["kind"] == "runner_output")
    assert b"module_that_does_not_exist_anywhere" in (run_root / saved["path"]).read_bytes()


# --- B3: drift after the run -----------------------------------------------------------


def test_b3_build_side_files_do_not_turn_pass_into_unknown(tmp_path, capsys, monkeypatch):
    """coverage.xml, logs, `.gradle/*` and IDE state created by the run are not baseline drift."""
    from tools.pilot_state import read_terminal_result
    from tools.run_pipeline import cmd_exec

    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    side_effects = '''

def pytest_sessionfinish(session):
    from pathlib import Path
    root = Path(__file__).parent
    (root / "coverage.xml").write_text("<coverage/>", encoding="utf-8")
    (root / "build-output.log").write_text("log", encoding="utf-8")
    for name in (".gradle", ".idea", "reports"):
        (root / name).mkdir(exist_ok=True)
    (root / ".gradle" / "file-system.probe").write_text("probe", encoding="utf-8")
    (root / ".idea" / "workspace.xml").write_text("<project/>", encoding="utf-8")
    (root / "reports" / "summary.txt").write_text("1 passed", encoding="utf-8")
'''
    content = "def test_selected():\n    assert True\n"
    project, run_root, attempt_id, arguments = _exec_project(tmp_path, content, conftest_extra=side_effects)

    code = cmd_exec(arguments)

    output = json.loads(capsys.readouterr().out)
    assert (project / "coverage.xml").is_file() and (project / "reports" / "summary.txt").is_file()
    assert output["verdict"] == "PASS", output
    assert code == 0
    assert read_terminal_result(run_root, attempt_id)["accepted"] is True


def test_b3_post_start_check_still_reports_changed_or_removed_baseline_inputs():
    from tools.project_inventory import _digest, validate_execution_baseline
    from tools.run_pipeline import _restrict_inventory_to_baseline_inputs

    def sealed(body: dict) -> dict:
        return {**body, "digest": _digest(body)}

    def inventory(files: list[dict]) -> dict:
        return sealed({"schema_version": "1.0.0", "project": "p", "project_identity": "sha256:" + "1" * 64, "module": ".", "files": files})

    def item(name: str, content: str) -> dict:
        return {"opaque_id": "id-" + name, "project_path": name, "content_digest": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}

    frozen = [item("pom.xml", "v1"), item("src/App.java", "v1")]
    baseline = sealed({
        "policy_profile": "cases-only-v1", "project": "p", "project_identity": "sha256:" + "1" * 64, "module": ".",
        "inputs": frozen, "requirements": {}, "skillsrc_file_id": "id-pom.xml", "inventory_digest": "sha256:" + "2" * 64,
        "schema_version": "1.0.0",
    })

    def status(current_files: list[dict]) -> str:
        current = inventory(current_files)
        restricted = _restrict_inventory_to_baseline_inputs(current, baseline)
        assert {row["opaque_id"] for row in restricted["files"]} <= {row["opaque_id"] for row in frozen}
        return validate_execution_baseline(baseline, restricted, policy_profile="cases-only-v1")["status"]

    # Without the restriction a new file is drift (the pre-start rule is unchanged).
    assert validate_execution_baseline(
        baseline, inventory([*frozen, item("coverage.xml", "new")]), policy_profile="cases-only-v1",
    )["status"] == "DECLARED_INPUT_DRIFT"

    side_files = [item("coverage.xml", "new"), item(".gradle/8.5/fileHashes.bin", "new"), item("app.log", "new")]
    assert status([*frozen, *side_files]) == "UNCHANGED"
    assert status([item("pom.xml", "v2"), frozen[1], *side_files]) == "DECLARED_INPUT_DRIFT"
    assert status([frozen[0], *side_files]) == "DECLARED_INPUT_DRIFT"


# --- R13 / M11: process-stop proof --------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] not in {"Z", "X"}


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="needs a Linux process table")
def test_r13_posix_proof_covers_a_daemon_that_left_the_process_group(tmp_path):
    """A setsid daemon (the Gradle daemon pattern) used to survive behind a 'proved' stop."""
    from tools.run_tests import run_subprocess

    pid_file = tmp_path / "daemon.pid"
    daemon = (
        "import os, sys, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    if os.fork() == 0:\n"
        f"        open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "        time.sleep(120)\n"
        "    os._exit(0)\n"
        "time.sleep(120)\n"
    )
    outcome = run_subprocess([sys.executable, "-c", daemon], tmp_path, timeout=2)
    try:
        assert outcome.kind == "TIMEOUT"
        assert pid_file.is_file(), "the daemon must have started before the timeout"
        daemon_pid = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while _pid_alive(daemon_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(daemon_pid), "a process of the launched scope outlived the stop"
        assert (outcome.process_scope_stopped, outcome.stop_proof) == (True, "POSIX_PROCESS_GROUP")
    finally:
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


def _windows_pid_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: alive but foreign; anything else: gone
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object stop path")
def test_r13_windows_job_stop_covers_a_detached_grandchild(tmp_path):
    """A detached grandchild (the Gradle daemon pattern on Windows) dies with the job."""
    from tools.run_tests import run_subprocess

    pid_file = tmp_path / "grandchild.pid"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n", encoding="utf-8",
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}], "
        "creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    outcome = run_subprocess([sys.executable, str(child)], tmp_path, timeout=3)
    try:
        assert outcome.kind == "TIMEOUT"
        assert pid_file.is_file(), "the grandchild must have started before the timeout"
        grandchild_pid = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while _windows_pid_alive(grandchild_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _windows_pid_alive(grandchild_pid), "a process of the launched scope outlived the stop"
        assert (outcome.process_scope_stopped, outcome.stop_proof) == (True, "WINDOWS_JOB_TERMINATED")
    finally:
        if pid_file.is_file():
            subprocess.run(
                ["taskkill", "/PID", pid_file.read_text(), "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )


@pytest.mark.skipif(os.name == "nt", reason="POSIX stop path")
def test_r13_posix_proof_is_withheld_when_the_scope_cannot_be_verified(tmp_path, monkeypatch):
    from tools import run_tests

    monkeypatch.setattr(run_tests, "_STOP_WAIT_SECONDS", 0.3)
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]

    monkeypatch.setattr(run_tests, "_posix_process_table", lambda _marker=None: None)
    unknown = run_tests.run_subprocess(sleeper, tmp_path, timeout=1)
    assert (unknown.kind, unknown.process_scope_stopped, unknown.stop_proof) == ("TIMEOUT", False, None)

    # A marked process that stays alive (it ignores the kill in this fake table) blocks the proof.
    survivor = 2 ** 22 + 7
    monkeypatch.setattr(run_tests, "_posix_process_table", lambda _marker=None: [(survivor, 1, survivor, True)])
    escaped = run_tests.run_subprocess(sleeper, tmp_path, timeout=1)
    assert (escaped.kind, escaped.process_scope_stopped, escaped.stop_proof) == ("TIMEOUT", False, None)


def test_r13_posix_scope_contains_group_marked_and_descendant_processes():
    from tools.run_tests import _posix_scope_pids

    table = [
        (100, 1, 100, False),    # the launched group leader
        (101, 100, 100, False),  # same group
        (200, 1, 200, True),     # left the group with setsid, still carries the marker
        (201, 200, 200, False),  # child of the escaped daemon, environment scrubbed
        (300, 1, 300, False),    # unrelated
    ]
    assert _posix_scope_pids(table, 100) == {100, 101, 200, 201}
    assert _posix_scope_pids([(300, 1, 300, False)], 100) == set()
    assert _posix_scope_pids([(300, 1, 300, False)], 100, known=(300,)) == {300}


class _FakeProcess:
    def __init__(self) -> None:
        self.pid, self._handle, self.killed, self.waits = 4242, 77, False, 0

    def wait(self, timeout=None):
        self.waits += 1
        return 1

    def kill(self) -> None:
        self.killed = True

    def terminate(self) -> None:
        self.killed = True


def _fake_windows_api(monkeypatch, *, assign=True, resume=True, terminate=True, active=(0,)):
    """Replace every kernel call; return the ordered call log."""
    from tools import run_tests

    log: list[object] = []
    remaining = list(active)

    def popen(command, **kwargs):
        log.append(("popen", kwargs.get("creationflags")))
        return _FakeProcess()

    monkeypatch.setattr(run_tests.subprocess, "Popen", popen)
    monkeypatch.setattr(run_tests, "_windows_create_kill_job", lambda: log.append("create") or 900)
    monkeypatch.setattr(run_tests, "_windows_assign_job", lambda job, process: log.append("assign") or assign)
    monkeypatch.setattr(run_tests, "_windows_resume_process", lambda process: log.append("resume") or resume)
    monkeypatch.setattr(run_tests, "_windows_terminate_job", lambda job: log.append("terminate") or terminate)
    monkeypatch.setattr(run_tests, "_windows_wait_process", lambda process, milliseconds: log.append("wait") or True)
    monkeypatch.setattr(run_tests, "_windows_job_active_processes", lambda job: (log.append("query"), remaining.pop(0) if len(remaining) > 1 else remaining[0])[1])
    monkeypatch.setattr(run_tests, "_close_windows_handle", lambda handle: log.append(("close", handle)) or True)
    monkeypatch.setattr(run_tests, "_terminate_process_tree", lambda process: log.append("taskkill"))
    monkeypatch.setattr(run_tests, "_STOP_WAIT_SECONDS", 0.2)
    return log


def test_r13_windows_process_is_assigned_to_the_job_before_it_is_resumed(monkeypatch):
    """Windows branch on fakes (the real kernel32 calls cannot run on this host)."""
    from tools import run_tests

    log = _fake_windows_api(monkeypatch)
    process, job = run_tests._start_windows_scoped_process(["tool"], {"cwd": "."})

    assert job == 900
    creationflags = log[0][1]
    assert creationflags & 0x00000004, "CREATE_SUSPENDED: no child may exist before the job assignment"
    assert log[1:] == ["create", "assign", "resume"]

    assert run_tests._stop_windows_scope(process, job) == "WINDOWS_JOB_TERMINATED"
    assert log[4:] == ["terminate", "wait", "query", ("close", 900)]


def test_r13_windows_proof_requires_assignment_termination_and_an_empty_job(monkeypatch):
    from tools import run_tests

    # Assignment failed: the process still runs, but no proof can ever be issued.
    log = _fake_windows_api(monkeypatch, assign=False)
    process, job = run_tests._start_windows_scoped_process(["tool"], {})
    assert job is None and ("close", 900) in log and "resume" in log
    assert run_tests._stop_windows_scope(process, job) is None
    assert log[-1] == "taskkill"

    # TerminateJobObject failed.
    log = _fake_windows_api(monkeypatch, terminate=False)
    process, job = run_tests._start_windows_scoped_process(["tool"], {})
    assert run_tests._stop_windows_scope(process, job) is None
    assert ("close", 900) in log

    # The job still reports a live process after the wait.
    log = _fake_windows_api(monkeypatch, active=(2,))
    process, job = run_tests._start_windows_scoped_process(["tool"], {})
    assert run_tests._stop_windows_scope(process, job) is None

    # The accounting query is unavailable.
    log = _fake_windows_api(monkeypatch, active=(None,))
    process, job = run_tests._start_windows_scoped_process(["tool"], {})
    assert run_tests._stop_windows_scope(process, job) is None

    # Processes drain from the job shortly after termination.
    log = _fake_windows_api(monkeypatch, active=(1, 0))
    process, job = run_tests._start_windows_scoped_process(["tool"], {})
    assert run_tests._stop_windows_scope(process, job) == "WINDOWS_JOB_TERMINATED"


def test_r13_windows_process_that_cannot_be_resumed_is_a_launch_failure(monkeypatch):
    from tools import run_tests

    log = _fake_windows_api(monkeypatch, resume=False)
    with pytest.raises(OSError):
        run_tests._start_windows_scoped_process(["tool"], {})
    assert "terminate" in log and ("close", 900) in log


def test_r13_windows_job_proof_is_a_valid_controller_stop_proof():
    from tools.execution_adapters import PYTEST
    from tools.pilot_state import _process_scope_stop_is_proved
    from tools.run_tests import (
        _PROCESS_STOP_ATTESTATION, ProcessOutcome, _process_row, consume_controller_process_stop_proof,
        validate_process_evidence,
    )

    source = {"source_digest": "sha256:" + "a" * 64}
    outcome = ProcessOutcome(
        124, "", "", "TIMEOUT", process_scope_stopped=True, stop_proof="WINDOWS_JOB_TERMINATED",
        _stop_attestation=_PROCESS_STOP_ATTESTATION,
    )
    row = _process_row("TIMEOUT", outcome, "RUN-1", source, PYTEST, 1.0)
    target = {"runner": "pytest", "command": PYTEST}

    assert row["stop_proof"] == "WINDOWS_JOB_TERMINATED"
    assert validate_process_evidence([row], "RUN-1", source, 124, 1.0, target) == []
    payload = {
        "verdict": "UNKNOWN", "process_evidence": [row], "run_id": "RUN-1", "source": source, "exit_code": 124,
        "stats": {"duration_sec": 1.0}, "target": target,
    }
    assert _process_scope_stop_is_proved(payload)
    assert consume_controller_process_stop_proof(payload) is True
    # A schema-shaped copy without the in-process attestation unlocks nothing.
    assert consume_controller_process_stop_proof(payload) is False


HOST_STOP_PROOF = "WINDOWS_JOB_TERMINATED" if os.name == "nt" else "POSIX_PROCESS_GROUP"


def test_r13_unknown_with_valid_proof_finalizes_and_allows_a_child_attempt(tmp_path, monkeypatch):
    from tools.pilot_state import derive_state
    from tools.run_tests import run_subprocess

    run = _LightRun(tmp_path)

    def hang(*_args):
        return run_subprocess([sys.executable, "-c", "import time; time.sleep(60)"], run.project, timeout=1)

    report = run.run(monkeypatch, hang)

    assert report["verdict"] == "UNKNOWN"
    assert _kinds(report) == ["TIMEOUT"]
    assert report["process_evidence"][0]["stop_proof"] == HOST_STOP_PROOF
    finalized = run.finalize(report)
    events = [row["event_type"] for row in derive_state(run.run_root)["events"] if row.get("attempt_id") == run.attempt_id]
    assert "PROCESS_STOPPED" in events and events[-1] == "ATTEMPT_TERMINAL"
    assert finalized["result"]["verification"] == "UNKNOWN"
    assert finalized["result"]["trace_valid"] is True
    assert run.generated.exists()
    assert run.create_child("timeout-retry")["parent_attempt_id"] == run.attempt_id


# --- M15: output tail --------------------------------------------------------------------


def test_m15_output_tail_keeps_64_kib_and_decodes_the_windows_console_code_page(tmp_path):
    from tools.pilot_state import _canonical_runner_output
    from tools.run_tests import PROCESS_OUTPUT_TAIL_BYTES, _decode_process_output, run_subprocess

    assert PROCESS_OUTPUT_TAIL_BYTES == 64 * 1024
    text = "Ошибка компиляции: не найден символ"
    assert _decode_process_output(text.encode("utf-8"), ("cp866",)) == text
    assert _decode_process_output(text.encode("cp866"), ("cp866",)) == text
    assert _decode_process_output(text.encode("cp1251"), ("cp1251",)) == text
    assert "�" in _decode_process_output(b"\xff\xfe broken", ())

    lines = "".join(f"line {index:05d} of the build output\n" for index in range(1500))
    assert 40_000 < len(lines) < PROCESS_OUTPUT_TAIL_BYTES
    # The text goes through a script file: a 48 KB ``-c`` argument exceeds the Windows
    # command-line limit and the launch fails before anything is written.
    script = tmp_path / "emit.py"
    # Bytes, not text: a text-mode stdout on Windows would turn every LF into CRLF.
    script.write_text(f"import sys; sys.stdout.buffer.write({lines.encode('utf-8')!r})", encoding="utf-8")
    outcome = run_subprocess([sys.executable, str(script)], tmp_path, timeout=30)
    assert (outcome.kind, outcome.exit_code) == ("EXIT", 0), (outcome.kind, outcome.exit_code, outcome.stderr)
    assert outcome.stdout == lines
    published = _canonical_runner_output(lines)
    assert published == lines.encode("utf-8")
    assert len(_canonical_runner_output(lines * 3)) <= 64 * 1024


# --- M16: launch environment ----------------------------------------------------------------


def test_m16_launch_environment_records_names_and_digests_never_values(tmp_path, monkeypatch):
    from tools.run_tests import LAUNCH_ENVIRONMENT_NAMES, ProcessOutcome, launch_environment_inputs

    assert set(LAUNCH_ENVIRONMENT_NAMES) == {
        "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "MAVEN_OPTS", "MAVEN_ARGS", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
        "GRADLE_OPTS", "JAVA_HOME",
    }
    secret = "-Dhttp.proxyPassword=hunter2-very-secret"
    rows = launch_environment_inputs({"MAVEN_OPTS": secret, "PATH": "/usr/bin", "API_KEY": "k", "JAVA_HOME": "/jdk"})
    assert rows == [
        {"name": "JAVA_HOME", "value_digest": "sha256:" + hashlib.sha256(b"/jdk").hexdigest()},
        {"name": "MAVEN_OPTS", "value_digest": "sha256:" + hashlib.sha256(secret.encode()).hexdigest()},
    ]

    for name in LAUNCH_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-m smoke-marker-value")
    seen: dict[str, str] = {}
    run = _LightRun(tmp_path)

    def passing(*_args):
        seen.update(os.environ)
        run.write_report(PASS_REPORT)
        return ProcessOutcome(0, "ok", "")

    report = run.run(monkeypatch, passing)

    assert report["verdict"] == "PASS"
    assert report["execution"]["environment_inputs"] == [
        {"name": "PYTEST_ADDOPTS", "value_digest": "sha256:" + hashlib.sha256(b"-m smoke-marker-value").hexdigest()},
    ]
    assert "smoke-marker-value" not in json.dumps(report)
    assert "PATH" in seen  # the inherited PATH is not stripped
    run.finalize(report)  # the receipt with environment inputs is a valid durable receipt


def test_m16_child_process_inherits_path_and_launch_variables_unchanged(tmp_path, monkeypatch):
    from tools.run_tests import run_subprocess

    monkeypatch.setenv("MAVEN_OPTS", "-Xmx64m")
    outcome = run_subprocess(
        [sys.executable, "-c", "import os; print(os.environ['MAVEN_OPTS']); print(bool(os.environ.get('PATH')))"],
        tmp_path, timeout=30,
    )
    assert outcome.stdout.split() == ["-Xmx64m", "True"]


# --- M18: direct runner CLI -----------------------------------------------------------------


def test_m18_direct_runner_cli_publishes_the_receipt_of_the_run_it_consumed(tmp_path, monkeypatch, capsys):
    from tools import run_tests
    from tools.pilot_state import derive_state, read_attempt_receipt

    run = _LightRun(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    values = {
        "canonical-document": run.document, "automation-artifact": run.automation, "autotest-review": run.review,
        "authorization-receipt": run.authorization, "host-isolation-receipt": run.boundary,
        "generated-delta-receipt": run.delta,
    }
    arguments = ["--project", str(run.project), "--run-root", str(run.run_root), "--attempt-id", run.attempt_id]
    for name, value in values.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        arguments += [f"--{name}", str(path)]
    launched: list[tuple[str, ...]] = []

    def passing(request, _runner):
        launched.append(request.argv)
        if "--collect-only" not in request.argv:
            run.write_report(PASS_REPORT)
        return run_tests.ProcessOutcome(0, "ok", "")

    monkeypatch.setattr("tools.execution_adapters.invoke_request", passing)

    code = run_tests.main(arguments)

    report = json.loads(capsys.readouterr().out)
    assert report.get("verdict") == "PASS", report
    assert code == 0
    assert ["--collect-only" in argv for argv in launched] == [True, False]
    durable = read_attempt_receipt(run.run_root, run.attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    assert durable["payload"] == report
    events = [row["event_type"] for row in derive_state(run.run_root)["events"] if row.get("attempt_id") == run.attempt_id]
    assert events.count("EXECUTION_STARTED") == 1


# --- M19: company runner ---------------------------------------------------------------------


def _company_inputs(tmp_path: Path, monkeypatch):
    from tests.test_automation_revision_budget import automated_document, automation

    document = automated_document()
    artifact = automation(document)
    generated = artifact["artifacts"]["generated_files"][0]
    target = tmp_path / generated["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(generated["content"].encode("utf-8"))
    # The static review needs a durable run; this module is exercised below that boundary.
    monkeypatch.setattr("tools.automation_validation.validate_accepted_autotest_review", lambda *_args, **_kwargs: [])
    review = {"schema_version": "6.0.0", "stage": "autotest-reviewer", "artifacts": {}}
    return document, artifact, review


class _ScriptedCompanyRunner:
    def __init__(self, **overrides) -> None:
        self.overrides = overrides

    def submit(self, request):
        from tools.company_runner import matching_result

        return matching_result(request, **self.overrides)

    def read_junit(self, result):  # pragma: no cover - not reached for process outcomes
        raise AssertionError("no JUnit for a process outcome")


def test_m19_company_runner_uses_validator_profiles_and_status_mapping(tmp_path, monkeypatch):
    from tools.company_runner import CompanyRunnerError, execute_company_details
    from tools.execution_adapters import MAVEN, PYTEST
    from tools.run_tests import validate_execution_evidence

    document, artifact, review = _company_inputs(tmp_path, monkeypatch)
    digest = "sha256:" + "c" * 64
    timeout = _ScriptedCompanyRunner(exit_code=124, exit_cause="TIMEOUT", junit_digest=None)

    report, request, _result = execute_company_details(
        tmp_path, "python", document, artifact, review, digest, digest, runner_profile=PYTEST, runner=timeout,
    )

    # A process outcome proves neither success nor an exact failure: UNKNOWN, as the validator derives.
    assert report["verdict"] == "UNKNOWN"
    assert report["target"] == {"language": "python", "framework": "pytest", "runner": "pytest", "command": PYTEST}
    assert request.runner_profile == PYTEST
    files = {row["file_id"]: row["content_digest"] for row in artifact["artifacts"]["generated_files"]}
    assert validate_execution_evidence(
        report["verdict"], report["run_id"], report["source"], report["execution_evidence"],
        report["evidence_authoritative"], request.pairs, files, report["exit_code"], report["stats"],
        report["diagnostics"], report["process_evidence"], report["target"],
    ) == []

    # Only the closed adapter profile names of the validator are accepted, per language.
    for language, profile in (("python", "pytest:selected-symbols"), ("python", MAVEN), ("java", PYTEST), ("java", "maven:selected-symbols")):
        with pytest.raises(CompanyRunnerError) as error:
            execute_company_details(tmp_path, language, document, artifact, review, digest, digest, runner_profile=profile, runner=timeout)
        assert error.value.code == "runner_profile_invalid", (language, profile)


def test_m19_company_receipt_runner_label_follows_the_adapter_profile():
    from tools.company_runner import _profile_runner
    from tools.execution_adapters import GRADLE, MAVEN, PYTEST, SYSTEM_MAVEN

    assert [_profile_runner(profile) for profile in (PYTEST, MAVEN, SYSTEM_MAVEN, GRADLE)] == ["pytest", "maven", "maven", "gradle"]
    assert _profile_runner("pytest:selected-symbols") is None


def test_b4_gate_failure_allows_one_child_attempt_for_the_corrected_revision(tmp_path, capsys, monkeypatch):
    """Decision 3: after GENERATED_TEST_INVALID automation r2 is generated in a child attempt, once."""
    from tools.pilot_state import create_attempt, derive_state, read_execution_baseline_for_attempt
    from tools.run_pipeline import cmd_exec

    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    content = "import module_that_does_not_exist_anywhere\n\n\ndef test_selected():\n    assert True\n"
    project, run_root, attempt_id, arguments = _exec_project(tmp_path, content)
    assert cmd_exec(arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["regeneration"] == {"allowed": True, "retry_reason": "GENERATED_TEST_INVALID", "automation_revision": 1}
    assert not (project / "tests" / "test_generated.py").exists()

    parent = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    identity = {"project": parent["project"], "module": parent["module"], "policy_profile": parent["policy_profile"], "parent_attempt_id": attempt_id}
    baseline = read_execution_baseline_for_attempt(run_root, attempt_id)
    with pytest.raises(ValueError, match="GENERATED_TEST_INVALID"):
        create_attempt(run_root, {**identity, "retry_reason": "operator-retry"}, baseline)
    child = create_attempt(run_root, {**identity, "retry_reason": "GENERATED_TEST_INVALID"}, baseline)
    assert child["parent_attempt_id"] == attempt_id and child["retry_reason"] == "GENERATED_TEST_INVALID"
    assert [row["state"] for row in derive_state(run_root)["attempts"]] == ["TERMINAL", "ACTIVE"]
