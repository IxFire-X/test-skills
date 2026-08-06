import json
import os
import subprocess
import sys
from hashlib import sha256

import pytest
from jsonschema import Draft202012Validator


def _validate_output(root, schema_name, completed):
    document = json.loads(completed.stdout)
    schema = json.loads((root / "schemas" / schema_name).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(document))
    assert errors == [], [(error.json_path, error.message) for error in errors]
    return document


def _run_runner(root, *arguments):
    return subprocess.run(
        [sys.executable, str(root / "tools" / "run_tests.py"), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_python_override_precedes_current_interpreter(runner, tmp_path):
    """Catches ignoring an explicit Python interpreter selected for the project."""
    executable = tmp_path / ("python.exe" if runner.os.name == "nt" else "python")
    executable.write_text("", encoding="utf-8")

    assert runner.select_python_interpreter(str(tmp_path), str(executable)) == str(executable)


def test_project_venv_precedes_host_interpreter(runner, tmp_path):
    """Catches running host pytest instead of the project's virtual environment."""
    venv_python = tmp_path / ".venv" / ("Scripts/python.exe" if runner.os.name == "nt" else "bin/python")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    assert runner.select_python_interpreter(str(tmp_path), None) == str(venv_python)


def test_python_cli_reports_selected_interpreter_and_nonzero_not_runnable(root, tmp_path):
    """Catches a missing selected interpreter being reported as a successful execution."""
    missing_python = tmp_path / "not-runnable-python.exe"
    missing_python.write_text("not an executable", encoding="utf-8")
    completed = _run_runner(
        root, "--project", str(tmp_path), "--language", "python",
        "--python-executable", str(missing_python),
    )

    report = _validate_output(root, "run-tests-output.schema.json", completed)
    assert completed.returncode != 0
    assert report["verdict"] == "NOT_RUNNABLE"
    assert report["environment"]["interpreter_path"] == str(missing_python)


def test_java_wrapper_precedes_system_maven(runner, tmp_path, monkeypatch):
    """Catches bypassing a project Maven wrapper in favor of a machine Maven."""
    wrapper = tmp_path / ("mvnw.cmd" if runner.os.name == "nt" else "mvnw")
    wrapper.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "C:/tools/mvn.cmd" if name == "mvn" else "C:/tools/java.exe")

    environment = runner.check_java_env(str(tmp_path))

    assert environment["status"] == "ready"
    assert environment["_runner"] == wrapper.name


def test_maven_uses_final_surefire_aggregate(runner, monkeypatch, tmp_path):
    """Catches reporting only the first suite instead of Maven's final aggregate."""
    output = (
        "Tests run: 13, Failures: 0, Errors: 0, Skipped: 0\n"
        "Tests run: 11, Failures: 0, Errors: 0, Skipped: 0\n"
        "Tests run: 24, Failures: 0, Errors: 0, Skipped: 0"
    )
    monkeypatch.setattr(runner, "run_subprocess", lambda cmd, cwd: (0, output, ""))

    result = runner.run_java(str(tmp_path), "mvnw.cmd", None)

    assert result["target"]["runner"] == "maven"
    assert result["stats"]["total"] == 24
    assert result["stats"]["passed"] == 24


@pytest.mark.skipif(os.name != "nt", reason="Windows command-wrapper behavior")
def test_windows_project_wrapper_is_resolved_from_project_directory(runner, tmp_path, monkeypatch):
    """Catches invoking mvnw.cmd by PATH instead of its project-local wrapper path."""
    wrapper = tmp_path / "mvnw.cmd"
    wrapper.write_text("@echo off\n", encoding="utf-8")
    observed = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kwargs: observed.setdefault("cmd", cmd) and Completed())
    runner.run_subprocess(["mvnw.cmd", "test"], str(tmp_path))

    assert observed["cmd"] == ["cmd", "/c", str(wrapper), "test"]


@pytest.mark.parametrize("wrapper", ["mvnw", "gradlew"])
def test_posix_project_wrapper_is_invoked_from_project_directory(runner, monkeypatch, tmp_path, wrapper):
    """Catches project wrappers being selected but lost because POSIX PATH omits dot."""
    observed = {}

    def fake_run(command, cwd):
        observed["command"] = command
        observed["cwd"] = cwd
        return 0, "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0", ""

    monkeypatch.setattr(runner, "run_subprocess", fake_run)
    monkeypatch.setattr(runner, "is_windows", lambda: False)

    result = runner.run_java(str(tmp_path), wrapper, None)

    assert result["verdict"] == "PASS"
    assert observed == {"command": [f"./{wrapper}", "test"], "cwd": str(tmp_path)}


def test_no_language_cli_is_nonzero_and_schema_valid(root, tmp_path):
    """Catches the no-.skillsrc NOT_RUNNABLE branch returning a success process code."""
    completed = _run_runner(root, "--project", str(tmp_path))

    report = _validate_output(root, "run-tests-output.schema.json", completed)
    assert completed.returncode == 2
    assert report["verdict"] == "NOT_RUNNABLE"


def test_python_pass_and_fail_cli_outputs_validate_against_schema(root, tmp_path):
    """Catches CLI PASS/FAIL documents drifting from the public output schema."""
    passing = tmp_path / "test_passing.py"
    passing.write_text("def test_passing():\n    assert True\n", encoding="utf-8")
    passed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--pytest-target", str(passing))
    pass_report = _validate_output(root, "run-tests-output.schema.json", passed)
    assert passed.returncode == 0
    assert pass_report["verdict"] == "PASS"

    passing.write_text("def test_failing():\n    assert False\n", encoding="utf-8")
    failed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--pytest-target", str(passing))
    fail_report = _validate_output(root, "run-tests-output.schema.json", failed)
    assert failed.returncode == 1
    assert fail_report["verdict"] == "FAIL"


def test_internal_error_report_is_schema_valid(runner, root):
    """Catches the top-level exception report drifting from the normal CLI contract."""
    report = runner.build_internal_error_report(RuntimeError("forced internal error"))
    schema = json.loads((root / "schemas" / "run-tests-output.schema.json").read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(report)) == []
    assert report["verdict"] == "NOT_RUNNABLE"


def _automation_artifact(file_path, methods):
    digest = "sha256:" + sha256(file_path.read_bytes()).hexdigest()
    return {
        "schema_version": "2.1.0", "stage": "tc-to-autotest", "warnings": [],
        "artifacts": {
            "automation_matrix": [
                {"test_case_id": method["test_case_ids"][0], "generated_file_ids": ["FILE-1"], "generated_method_ids": [method["id"]]}
                for method in methods
            ],
            "generated_test_files": [{"id": "FILE-1", "path": file_path.name, "language": "python", "framework": "pytest", "content_digest": digest}],
            "generated_test_methods": methods,
        },
    }


def test_python_artifact_binds_each_selected_method_to_real_pytest_outcome(root, tmp_path):
    """Catches a PASS that has no deterministic method evidence for generated tests."""
    test_file = tmp_path / "test_bound.py"
    test_file.write_text(
        "import pytest\n\ndef test_pass(): assert True\n\n@pytest.mark.skip(reason='policy')\ndef test_skip(): assert False\n\ndef test_fail(): assert False\n",
        encoding="utf-8",
    )
    methods = [
        {"id": "METHOD-pass", "file_id": "FILE-1", "test_case_ids": ["TC-pass"], "requirement_ids": ["REQ-pass"], "name": "test_pass", "content_digest": "sha256:" + "1" * 64},
        {"id": "METHOD-skip", "file_id": "FILE-1", "test_case_ids": ["TC-skip"], "requirement_ids": ["REQ-skip"], "name": "test_skip", "content_digest": "sha256:" + "2" * 64},
        {"id": "METHOD-fail", "file_id": "FILE-1", "test_case_ids": ["TC-fail"], "requirement_ids": ["REQ-fail"], "name": "test_fail", "content_digest": "sha256:" + "3" * 64},
    ]
    artifact = tmp_path / "automation.json"
    artifact.write_text(json.dumps(_automation_artifact(test_file, methods)), encoding="utf-8")

    completed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--python-executable", sys.executable, "--automation-artifact", str(artifact))
    report = _validate_output(root, "run-tests-output.schema.json", completed)

    assert completed.returncode == 1
    assert report["verdict"] == "FAIL"
    assert [(item["method_id"], item["status"]) for item in report["execution_evidence"]] == [
        ("METHOD-fail", "failed"), ("METHOD-pass", "passed"), ("METHOD-skip", "skipped"),
    ]
    assert report["evidence_authoritative"] is True
    assert report["run_id"].startswith("RUN-")


def test_zero_discovery_is_never_python_pass(root, tmp_path):
    """Catches pytest's zero exit status being promoted to PASS without discovered tests."""
    empty = tmp_path / "empty.py"
    empty.write_text("value = 1\n", encoding="utf-8")

    completed = _run_runner(root, "--project", str(tmp_path), "--language", "python", "--python-executable", sys.executable, "--pytest-target", str(empty))
    report = _validate_output(root, "run-tests-output.schema.json", completed)

    assert completed.returncode == 1
    assert report["verdict"] == "FAIL"
    assert report["stats"]["total"] == 0
    assert any("no_tests_discovered" in reason for reason in report["root_cause"])


def test_junit_xml_binds_pass_skip_and_failure_to_generated_methods(runner, tmp_path):
    """Catches Java XML reports being reduced to aggregate stats without method identities."""
    report_dir = tmp_path / "target" / "surefire-reports"
    report_dir.mkdir(parents=True)
    source = tmp_path / "DemoTest.java"
    source.write_text("package demo; class DemoTest {}", encoding="utf-8")
    (report_dir / "TEST-demo.xml").write_text(
        "<testsuite><testcase classname='demo.DemoTest' name='test_pass'/><testcase classname='demo.DemoTest' name='test_skip'><skipped/></testcase><testcase classname='demo.DemoTest' name='test_fail'><failure/></testcase></testsuite>",
        encoding="utf-8",
    )
    bindings = {"files": {"FILE-java": source}, "methods": {
        ("FILE-java", "test_pass"): "METHOD-java-pass", ("FILE-java", "test_skip"): "METHOD-java-skip", ("FILE-java", "test_fail"): "METHOD-java-fail",
    }}

    evidence, errors = runner.parse_junit_xml_evidence([report_dir], bindings, "RUN-java")

    assert errors == []
    assert [(item["method_id"], item["status"]) for item in evidence] == [
        ("METHOD-java-fail", "failed"), ("METHOD-java-pass", "passed"), ("METHOD-java-skip", "skipped"),
    ]


@pytest.mark.parametrize("mutate", [
    lambda artifact: artifact.pop("stage"),
    lambda artifact: artifact["artifacts"].pop("generated_test_files"),
    lambda artifact: artifact["artifacts"]["generated_test_files"][0].update({"id": "bad"}),
    lambda artifact: artifact["artifacts"]["generated_test_methods"][0].update({"file_id": "FILE-missing"}),
    lambda artifact: artifact["artifacts"]["automation_matrix"][0].update({"generated_method_ids": ["METHOD-missing"]}),
    lambda artifact: artifact["artifacts"]["generated_test_methods"][0].pop("requirement_ids"),
    lambda artifact: artifact["artifacts"]["generated_test_files"][0].update({"unexpected": True}),
])
def test_automation_artifact_schema_violations_never_become_bindings(runner, tmp_path, mutate):
    """Catches accepting malformed tc-to-autotest artifacts before any runner starts."""
    test_file = tmp_path / "test_bound.py"
    test_file.write_text("def test_bound(): assert True\n", encoding="utf-8")
    method = {"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_bound", "content_digest": "sha256:" + "1" * 64}
    artifact = _automation_artifact(test_file, [method])
    mutate(artifact)
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bindings, error = runner.load_automation_artifact(str(path), str(tmp_path))

    assert bindings is None
    assert error


def test_automation_artifact_rejects_stale_generated_file_digest(runner, tmp_path):
    """Catches a tampered generated source file being run under a stale artifact digest."""
    test_file = tmp_path / "test_bound.py"
    test_file.write_text("def test_bound(): assert True\n", encoding="utf-8")
    method = {"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_bound", "content_digest": "sha256:" + "1" * 64}
    artifact = _automation_artifact(test_file, [method])
    artifact["artifacts"]["generated_test_files"][0]["content_digest"] = "sha256:" + "0" * 64
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bindings, error = runner.load_automation_artifact(str(path), str(tmp_path))

    assert bindings is None
    assert "digest" in error


def test_junit_binding_requires_generated_class_identity(runner, tmp_path):
    """Catches OtherTest.test_same satisfying a generated test solely by method name."""
    report_dir = tmp_path / "target" / "surefire-reports"
    report_dir.mkdir(parents=True)
    generated = tmp_path / "src" / "test" / "java" / "demo" / "GeneratedTest.java"
    generated.parent.mkdir(parents=True)
    generated.write_text("package demo; class GeneratedTest {}", encoding="utf-8")
    (report_dir / "TEST-other.xml").write_text("<testsuite><testcase classname='demo.OtherTest' name='test_same'/></testsuite>", encoding="utf-8")
    bindings = {"files": {"FILE-java": generated}, "methods": {("FILE-java", "test_same"): "METHOD-1"}}

    evidence, errors = runner.parse_junit_xml_evidence([report_dir], bindings, "RUN-1")

    assert evidence == []
    assert errors == ["missing junit outcome binding for METHOD-1"]


def test_java_artifact_rejects_duplicate_fqn_in_executable_root(runner, tmp_path):
    """Catches another Java file with the same FQN certifying a declared generated test."""
    java_root = tmp_path / "src" / "test" / "java" / "demo"
    java_root.mkdir(parents=True)
    generated = java_root / "GeneratedTest.java"
    generated.write_text("package demo; class GeneratedTest {}", encoding="utf-8")
    duplicate = java_root / "other" / "GeneratedTest.java"
    duplicate.parent.mkdir()
    duplicate.write_text("package demo; class GeneratedTest {}", encoding="utf-8")
    digest = "sha256:" + sha256(generated.read_bytes()).hexdigest()
    artifact = {"schema_version": "2.1.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
        "automation_matrix": [{"test_case_id": "TC-1", "generated_file_ids": ["FILE-1"], "generated_method_ids": ["METHOD-1"]}],
        "generated_test_files": [{"id": "FILE-1", "path": "src/test/java/demo/GeneratedTest.java", "language": "java", "framework": "junit5", "content_digest": digest}],
        "generated_test_methods": [{"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_one", "content_digest": "sha256:" + "1" * 64}],
    }}
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bindings, error = runner.load_automation_artifact(str(path), str(tmp_path))

    assert bindings is None
    assert error


@pytest.mark.skipif(os.name != "nt", reason="This fixture exercises the Windows gradlew.cmd execution path")
def test_gradlew_cmd_cli_emits_fresh_authoritative_method_evidence(root, tmp_path):
    """Catches Gradle being advertised but unable to produce current XML-backed evidence."""
    source = tmp_path / "src" / "test" / "java" / "demo" / "GeneratedTest.java"
    source.parent.mkdir(parents=True)
    source.write_text("package demo; class GeneratedTest { void test_one() {} }", encoding="utf-8")
    (tmp_path / "settings.gradle").write_text("rootProject.name='demo'", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }", encoding="utf-8")
    (tmp_path / "gradlew.cmd").write_text(
        "@echo off\r\n"
        "if not exist build\\test-results mkdir build\\test-results\r\n"
        "> build\\test-results\\TEST-demo.xml echo ^<testsuite^>^<testcase classname=\"demo.GeneratedTest\" name=\"test_one\"/^>^</testsuite^>\r\n"
        "echo 1 test completed\r\nexit /b 0\r\n",
        encoding="utf-8",
    )
    digest = "sha256:" + sha256(source.read_bytes()).hexdigest()
    artifact = {"schema_version": "2.1.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
        "automation_matrix": [{"test_case_id": "TC-1", "generated_file_ids": ["FILE-1"], "generated_method_ids": ["METHOD-1"]}],
        "generated_test_files": [{"id": "FILE-1", "path": "src/test/java/demo/GeneratedTest.java", "language": "java", "framework": "junit5", "content_digest": digest}],
        "generated_test_methods": [{"id": "METHOD-1", "file_id": "FILE-1", "test_case_ids": ["TC-1"], "requirement_ids": ["REQ-1"], "name": "test_one", "content_digest": "sha256:" + "1" * 64}],
    }}
    artifact_path = tmp_path / "automation.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    environment = os.environ.copy()
    environment["JAVA_HOME"] = r"D:\AI-Projects\.tools\jdk-17"
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "run_tests.py"), "--project", str(tmp_path), "--language", "java", "--automation-artifact", str(artifact_path)],
        capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
    )
    report = _validate_output(root, "run-tests-output.schema.json", completed)

    assert completed.returncode == 0
    assert report["verdict"] == "PASS"
    assert report["target"]["runner"] == "gradle"
    assert report["stats"]["total"] == report["stats"]["passed"] == 1
    assert report["evidence_authoritative"] is True
    assert report["execution_evidence"] == [{"run_id": report["run_id"], "method_id": "METHOD-1", "status": "passed"}]


def test_artifact_runner_compatibility_rejects_python_file_for_java(runner, tmp_path):
    """Catches a schema-valid Python artifact reaching Java evidence binding."""
    source = tmp_path / "test_wrong.py"
    source.write_text("def test_one(): assert True\n", encoding="utf-8")
    bindings = {"file_specs": {"FILE-1": {"language": "python", "framework": "pytest"}}}

    assert runner.validate_artifact_runner_compatibility(bindings, "java")


def test_report_root_confinement_rejects_resolved_escape_and_keeps_in_root(runner, tmp_path):
    """Catches report XML discovery escaping via any resolved report-root path."""
    outside = tmp_path.parent / "outside-reports"
    outside.mkdir(exist_ok=True)
    project = tmp_path / "project"
    project.mkdir()

    assert runner.confined_report_dir(str(project), "build/test-results") == (project / "build" / "test-results").resolve()
    assert runner.confined_report_dir(str(project), "../outside-reports") is None


def test_report_root_symlink_escape_is_rejected_when_supported(runner, tmp_path):
    """Catches an in-project Gradle report path resolving into external XML evidence."""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = project / "build" / "test-results"
    target.parent.mkdir()
    try:
        target.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink privilege unavailable")

    assert runner.confined_report_dir(str(project), "build/test-results") is None


@pytest.mark.parametrize("run_id,evidence,authoritative", [
    (None, [{"run_id": "RUN-1", "method_id": "METHOD-1", "status": "passed"}], True),
    ("RUN-1", [], True),
    ("RUN-1", [], False),
    ("RUN-1", [{"run_id": "RUN-other", "method_id": "METHOD-1", "status": "passed"}], True),
    ("RUN-1", [{"run_id": "RUN-1", "method_id": "METHOD-1", "status": "passed"}, {"run_id": "RUN-1", "method_id": "METHOD-1", "status": "failed"}], True),
])
def test_execution_evidence_semantic_invariants(runner, run_id, evidence, authoritative):
    """Catches impossible authoritative/non-authoritative evidence combinations."""
    assert runner.validate_execution_evidence("PASS", run_id, evidence, authoritative)
