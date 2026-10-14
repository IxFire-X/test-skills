"""B1 (finding 2): a project build that fails before the tests is not a broken generated test.

Live run e96638e8 (attempt 151aae31): ``test-compile`` failed in ``validate`` — nohttp
checkstyle could not read the run lock under ``.pilot-runs`` — and the gate was classified
``GENERATED_TEST_INVALID`` with an offer to regenerate the (correct) tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import execution_adapters, pipeline_driver as driver, run_tests
from tools.execution_adapters import GRADLE, MAVEN, PYTEST
from tools.run_tests import ProcessOutcome, gate_failure_kind
from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]
RECORDED = (ROOT / "tests" / "fixtures" / "live-e-build-failure" / "runner-output.txt").read_text(encoding="utf-8")
NOHTTP = "maven-checkstyle-plugin:3.6.0:check (nohttp-checkstyle-validation)"

_MAVEN_TEST_COMPILE = """[INFO] --- compiler:3.14.0:testCompile (default-testCompile) @ spring-petclinic ---
[ERROR] COMPILATION ERROR :
[ERROR] /p/src/test/java/org/x/OwnerGeneratedTest.java:[12,8] cannot find symbol
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:testCompile (default-testCompile) on project spring-petclinic: Compilation failure
"""


def test_e96638e8_validate_failure_is_build_failed_before_tests() -> None:
    from tools.run_tests import build_failure_before_tests

    assert gate_failure_kind(MAVEN, ProcessOutcome(1, RECORDED, "")) == "BUILD_FAILED_BEFORE_TESTS"
    assert build_failure_before_tests(MAVEN, RECORDED) == NOHTTP


def test_a_generated_test_class_that_does_not_compile_stays_generated_test_invalid() -> None:
    from tools.run_tests import build_failure_before_tests

    assert build_failure_before_tests(MAVEN, _MAVEN_TEST_COMPILE) is None
    assert gate_failure_kind(MAVEN, ProcessOutcome(1, _MAVEN_TEST_COMPILE, "")) == "GENERATED_TEST_INVALID"
    gradle_test = "> Task :compileTestJava FAILED\nFAILURE: Build failed with an exception.\n* What went wrong:\nExecution failed for task ':app:compileTestJava'.\n"
    assert gate_failure_kind(GRADLE, ProcessOutcome(1, gradle_test, "")) == "GENERATED_TEST_INVALID"
    # A Gradle task that runs tests (no report) is the tests' own failure, not the build before them.
    for task in ("integrationTest", "functionalTest", "check", "test"):
        assert build_failure_before_tests(GRADLE, f"Execution failed for task ':app:{task}'.\n") is None, task
    # Nothing recognisable in the output: the old classification, unchanged.
    assert gate_failure_kind(MAVEN, ProcessOutcome(1, "", "")) == "GENERATED_TEST_INVALID"
    assert gate_failure_kind(PYTEST, ProcessOutcome(2, RECORDED, "")) == "GENERATED_TEST_INVALID"
    # The main sources (not the test class) not compiling is the project's build.
    gradle_main = "* What went wrong:\nExecution failed for task ':app:compileJava'.\n"
    assert gate_failure_kind(GRADLE, ProcessOutcome(1, gradle_main, "")) == "BUILD_FAILED_BEFORE_TESTS"
    assert build_failure_before_tests(GRADLE, gradle_main) == "task ':app:compileJava'"


def test_e96638e8_driver_reports_the_build_and_does_not_offer_regeneration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """The recorded gate output through the driver: no regenerate question; summary and log carry the tail."""
    real_kind, real_invoke = run_tests.gate_failure_kind, execution_adapters.invoke_request

    def invoke(request, runner):  # the gate (collect-only) prints the recorded Maven build
        if "--collect-only" in request.argv:
            return ProcessOutcome(1, RECORDED, "")
        return real_invoke(request, runner)

    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(execution_adapters, "invoke_request", invoke)
    monkeypatch.setattr(run_tests, "gate_failure_kind", lambda adapter_id, outcome: real_kind(MAVEN, outcome))
    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1"), SavedModel("local-pilot-v1"),
                  until=lambda task: task["action"] == "ask_user")
    assert done["action"] == "done", done  # no ask.regenerate-after-gate
    result = done["result"]
    assert (result["verification"], result["reason_code"], result["accepted"]) == ("NOT_RUNNABLE", "BUILD_FAILED_BEFORE_TESTS", False)
    assert "regeneration" not in result["exec"]
    assert schema_diagnostics(result, ROOT / "schemas" / "driver-summary.schema.json", ROOT) == []
    failure = result["build_failure"]
    assert failure["message"] == ("сборка проекта упала до запуска тестов: " + NOHTTP
                                  + ". Исправьте окружение проекта и начните новый прогон; перегенерация тестов не нужна.")
    assert failure["output_tail"][-1].endswith("MojoFailureException")
    assert any("BUILD FAILURE" in line for line in failure["output_tail"])
    run_root = _run_root(project, done)
    capsys.readouterr()
    assert driver.main(["next", "--project", str(project), "--run", done["run_id"]]) == result["exit_code"]
    assert json.loads(capsys.readouterr().out)["result"]["build_failure"] == failure
    log = [json.loads(line) for line in (driver.work_dir(run_root) / "driver-log.jsonl").read_text(encoding="utf-8").splitlines()]
    logged = [row["result"]["build_failure"] for row in log if "build_failure" in row.get("result", {})]
    assert logged and logged[-1] == failure
