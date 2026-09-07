from pathlib import Path


def test_controller_timeout_without_authoritative_framework_report_is_unknown():
    from tools.execution_adapters import classify_execution

    assert classify_execution(controller_timed_out=True, process_returncode=None, report_bytes=None, selectors=("tests/test_a.py::test_a",)) == "UNKNOWN"


def test_exact_framework_timeout_and_failure_are_fail_from_authoritative_reports():
    from tools.execution_adapters import classify_execution

    root = Path(__file__).parent / "fixtures" / "reports"
    assert classify_execution(controller_timed_out=True, process_returncode=1, report_bytes=(root / "pytest-timeout.xml").read_bytes(), selectors=("tests/test_a.py::test_a",)) == "FAIL"
    assert classify_execution(controller_timed_out=False, process_returncode=1, report_bytes=(root / "junit5-timeout.xml").read_bytes(), selectors=("pkg.SampleTest#works",)) == "FAIL"


def test_clean_authoritative_report_is_pass_but_crash_or_lost_report_is_unknown():
    from tools.execution_adapters import classify_execution

    root = Path(__file__).parent / "fixtures" / "reports"
    assert classify_execution(controller_timed_out=False, process_returncode=0, report_bytes=(root / "pytest-pass.xml").read_bytes(), selectors=("tests/test_a.py::test_a",)) == "PASS"
    assert classify_execution(controller_timed_out=False, process_returncode=-9, report_bytes=None, selectors=("tests/test_a.py::test_a",)) == "UNKNOWN"
    assert classify_execution(controller_timed_out=False, process_returncode=-9, report_bytes=(root / "pytest-pass.xml").read_bytes(), selectors=("tests/test_a.py::test_a",)) == "UNKNOWN"


def test_controller_timeout_cannot_be_overridden_by_a_clean_report():
    """After the controller terminates a process, PASS is not authoritative."""
    from tools.execution_adapters import classify_execution

    root = Path(__file__).parent / "fixtures" / "reports"
    assert classify_execution(
        controller_timed_out=True,
        process_returncode=None,
        report_bytes=(root / "pytest-pass.xml").read_bytes(),
        selectors=("tests/test_a.py::test_a",),
    ) == "UNKNOWN"


def test_skipped_exact_selector_is_not_a_successful_execution():
    """A runner must not turn an xfail/skip into a passing reviewed test."""
    from tools.execution_adapters import classify_execution

    report = b'''<?xml version="1.0"?><testsuite tests="1" skipped="1"><testcase classname="tests.test_a" name="test_a"><skipped/></testcase></testsuite>'''
    assert classify_execution(
        controller_timed_out=False,
        process_returncode=0,
        report_bytes=report,
        selectors=("tests/test_a.py::test_a",),
    ) == "UNKNOWN"


def test_pytest_class_method_nodeid_is_matched_without_java_style_path_rewrite():
    """pytest emits module.Class for a method; it must match module.py::Class::method."""
    from tools.execution_adapters import classify_execution

    report = b'''<?xml version="1.0"?><testsuite tests="1"><testcase classname="tests.test_class.TestThing" name="works"/></testsuite>'''
    assert classify_execution(
        controller_timed_out=False,
        process_returncode=0,
        report_bytes=report,
        selectors=("tests/test_class.py::TestThing::works",),
    ) == "PASS"

def test_java_zero_report_with_negative_exit_is_unknown(tmp_path, monkeypatch):
    from tests.test_project_native_pytest import _reviewed_inputs
    from tools.execution_adapters import ExecutionRequest, MAVEN, request_digest
    from tools.pilot_state import claim_execution_start
    from tools.run_tests import ProcessOutcome, RunnerCompatibility, run_tests_v5

    project = tmp_path / "project"
    project.mkdir()
    (project / "tests").mkdir()
    document, automation, review, boundary, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    wrapper = project / "mvnw.cmd"
    wrapper.write_text("synthetic wrapper placeholder", encoding="utf-8")
    generated = automation["artifacts"]["generated_files"][0]
    symbol = automation["artifacts"]["generated_symbols"][0]
    pair = (generated["file_id"], symbol["symbol_id"])
    selector = "synthetic.SampleTest#selected"
    compatibility = RunnerCompatibility(
        "READY",
        (pair,),
        {pair: {
            "path": project / generated["path"],
            "relative_path": generated["path"],
            "locator": {"class_fqn": "synthetic.SampleTest", "method_name": "selected"},
        }},
        {generated["file_id"]: generated["content_digest"]},
        (),
    )
    request = ExecutionRequest(
        MAVEN,
        str(wrapper.resolve()),
        (str(wrapper.resolve()), "-Pdefault", f"-Dtest={selector}", "test"),
        str(project.resolve()),
        (selector,),
        600,
        ("target/surefire-reports",),
        ("PROJECT_NATIVE_ENV",),
        "default",
    )

    monkeypatch.setattr("tools.run_tests.validate_artifact_runner_compatibility", lambda *_args: compatibility)

    def negative_exit_with_zero_report(*_args):
        reports = project / "target" / "surefire-reports"
        reports.mkdir(parents=True)
        (reports / "TEST-synthetic.xml").write_bytes(
            b'<testsuite tests="0" errors="0" failures="0" skipped="0"></testsuite>'
        )
        return ProcessOutcome(-9, "", "")

    monkeypatch.setattr("tools.execution_adapters.invoke_request", negative_exit_with_zero_report)

    result = run_tests_v5(
        request, authorization, document, automation, review,
        host_isolation_receipt=boundary,
        generated_delta_receipt=delta,
        run_root=run_root,
        attempt_id=attempt_id,
        on_execution_start=lambda value: claim_execution_start(
            run_root, attempt_id, request_digest(value),
        ),
    )

    assert result["verdict"] == "UNKNOWN"
    assert result["evidence_authoritative"] is False
    assert all(row["kind"] != "NO_TESTS_COLLECTED" for row in result["process_evidence"])
