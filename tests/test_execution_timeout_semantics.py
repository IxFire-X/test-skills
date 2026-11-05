"""The verdict rules of the result (P4b: the exit code of the build is not observed, so the zero-report-with-a-killed-process case is gone) — audit U4: the report rows come from ``run_tests.match_junit_cases`` alone; a report without
tests is ``classify_zero_report``, a report with tests ``classify_evidence``."""

from pathlib import Path


def _rows(*statuses: str) -> list[dict]:
    return [{"status": status} for status in statuses]


def test_a_report_without_tests_is_fail_unless_the_process_end_is_unknown():
    from tools.execution_adapters import classify_zero_report

    assert classify_zero_report(controller_timed_out=False, process_returncode=0) == "FAIL"
    assert classify_zero_report(controller_timed_out=False, process_returncode=1) == "FAIL"
    assert classify_zero_report(controller_timed_out=True, process_returncode=None) == "UNKNOWN"
    assert classify_zero_report(controller_timed_out=False, process_returncode=None) == "UNKNOWN"
    assert classify_zero_report(controller_timed_out=False, process_returncode=-9) == "UNKNOWN"


def test_exact_framework_timeout_and_failure_are_fail_from_authoritative_reports():
    from tools.run_tests import classify_evidence, match_junit_cases, parse_junit_bytes

    root = Path(__file__).parent / "fixtures" / "reports"
    for name in ("pytest-timeout.xml", "junit5-timeout.xml"):
        statuses = [case.status for case in parse_junit_bytes((root / name).read_bytes()).cases]
        assert "FAILED" in statuses or "ERROR" in statuses, (name, statuses)
        assert classify_evidence(_rows(*statuses), controller_timed_out=True) == "FAIL"
        assert classify_evidence(_rows(*statuses), controller_timed_out=False) == "FAIL"
    assert match_junit_cases  # the one matcher of the verdict


def test_clean_authoritative_report_is_pass():
    from tools.run_tests import classify_evidence, parse_junit_bytes

    root = Path(__file__).parent / "fixtures" / "reports"
    statuses = [case.status for case in parse_junit_bytes((root / "pytest-pass.xml").read_bytes()).cases]
    assert classify_evidence(_rows(*statuses), controller_timed_out=False) == "PASS"


def test_controller_timeout_cannot_be_overridden_by_a_clean_report():
    """After the controller terminates a process, PASS is not authoritative."""
    from tools.run_tests import classify_evidence

    assert classify_evidence(_rows("PASSED"), controller_timed_out=True) == "UNKNOWN"


def test_skipped_exact_selector_is_not_a_successful_execution():
    """A runner must not turn an xfail/skip into a passing reviewed test."""
    from tools.run_tests import classify_evidence

    assert classify_evidence(_rows("PASSED", "SKIPPED"), controller_timed_out=False) == "UNKNOWN"
    assert classify_evidence(_rows("SKIPPED", "FAILED"), controller_timed_out=False) == "FAIL"


def test_pytest_class_method_nodeid_is_matched_without_java_style_path_rewrite():
    """pytest emits module.Class for a method; it must match module.py::Class::method (moved from classify_execution)."""
    from tools.run_tests import RunnerCompatibility, classify_evidence, match_junit_cases, parse_junit_bytes

    pair = ("file-a", "symbol-a")
    compatibility = RunnerCompatibility("READY", (pair,), {pair: {
        "path": Path("tests/test_class.py"), "relative_path": "tests/test_class.py",
        "locator": {"kind": "python_class_method", "qualified_class_name": "TestThing", "method_name": "works"},
    }}, {"file-a": "sha256:" + "0" * 64}, ())
    report = b'''<?xml version="1.0"?><testsuite tests="1"><testcase classname="tests.test_class.TestThing" name="works"/></testsuite>'''
    rows, errors = match_junit_cases(parse_junit_bytes(report), compatibility, "run", {"document_id": "d", "revision": 1, "source_digest": "s"},
                                     strict=True)
    assert errors == [] and classify_evidence(rows, controller_timed_out=False) == "PASS", (rows, errors)
