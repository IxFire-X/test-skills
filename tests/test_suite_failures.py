"""Wave 3 F: failure triage of a suite run (W3-Р4, W3-Р12) with a fake runner."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_suite_manifest import GENERATED, _manifest, _write, step5  # noqa: F401  (fixture)
from tools import suite_run
from tools.suite_failures import analyst_question, bug_report, classify


@pytest.mark.parametrize("runs,kwargs,expected", [
    (["passed"], {}, ("PASS", None)),
    (["passed"], {"quarantined": True}, ("FIXED", None)),
    (["failed", "failed", "failed"], {}, ("QUARANTINE", "BEHAVIOR_CHANGED_WITHOUT_SPEC")),
    (["failed", "failed", "failed"], {"requirement_changed": True}, ("UPDATE", "REQUIREMENT_CHANGED")),
    (["broken", "broken", "broken"], {}, ("REPAIR", "TEST_CODE_ERROR")),
    (["broken", "broken", "broken"], {"requirement_changed": True}, ("UPDATE", "REQUIREMENT_CHANGED")),
    (["failed", "passed"], {}, ("QUARANTINE", "FLAKY")),
    (["passed", "failed", "passed"], {}, ("QUARANTINE", "FLAKY")),
    ([], {"compile_error": True}, ("REPAIR", "TEST_DOES_NOT_COMPILE")),
    (["skipped"], {}, ("NOT_RUN", None)),
])
def test_each_outcome_has_one_decision(runs, kwargs, expected) -> None:
    result = classify(runs, **kwargs)
    assert (result["outcome"], result["reason"]) == expected and result["proposal_only"] is False


def test_a_person_edited_method_only_gets_a_proposal() -> None:
    assert classify(["failed", "failed", "failed"], edited_by_person=True) == {"outcome": "QUARANTINE", "reason": "BEHAVIOR_CHANGED_WITHOUT_SPEC", "proposal_only": True}
    assert classify(["passed"], edited_by_person=True)["proposal_only"] is False


def test_bug_report_and_question_come_from_the_case(step5: dict) -> None:
    case = step5["document"]["test_cases"][9]
    text = bug_report(case, locator="pkg.Test#deleteStudent", failure="expected: <Student Successfully Deleted!> but was: <Student removed>", run_id="r" * 32)
    assert case["case_id"] in text and "Student removed" in text and "**Шаги**" in text and case["steps"][0]["action"].strip() in text
    assert case["case_id"] in analyst_question(case, failure="expected X but was Y")


def _surefire(directory: Path, owner: str, statuses: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cases = []
    for name, status in statuses.items():
        inner = {"failed": '<failure message="expected 200 but was 500">AssertionFailedError</failure>', "broken": '<error message="NoSuchMethodError"/>',
                 "skipped": "<skipped/>"}.get(status, "")
        cases.append(f'<testcase classname="{owner}" name="{name}">{inner}</testcase>')
    (directory / f"TEST-{owner}.xml").write_text(f'<testsuite name="{owner}">{"".join(cases)}</testsuite>', encoding="utf-8")


def _module(project: Path) -> dict:
    return {"id": "root", "root": ".", "test": {"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": "mvnw.cmd",
                                                "build_profile": "default", "adapter_parameters": {}}}


def test_a_stable_failure_is_not_flaky_and_a_changing_one_is(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    locators = [case["methods"][0]["locator"] for case in manifest["cases"]]
    owner = locators[0].rsplit("#", 1)[0]
    stable, flaky = locators[0].rsplit("#", 1)[1], locators[1].rsplit("#", 1)[1]
    script = iter([{stable: "failed", flaky: "failed"}, {stable: "failed", flaky: "passed"}, {stable: "failed"}])
    calls = []

    def fake(argv, **_kwargs):
        calls.append(list(argv))
        statuses = next(script)
        everything = {locator.rsplit("#", 1)[1]: "passed" for locator in locators} if len(calls) == 1 else {}
        _surefire(project / "target" / "surefire-reports", owner, {**everything, **statuses})
        return SimpleNamespace(returncode=1, stdout="Tests run: 12, Failures: 2", stderr="")

    monkeypatch.setattr(suite_run, "RUNNER", fake)
    result = suite_run.run_suite(project, manifest, _module(project), repeats=2)
    by = {row["locator"]: row for row in result["methods"]}
    assert by[locators[0]]["runs"] == ["failed", "failed", "failed"] and by[locators[1]]["runs"] == ["failed", "passed"]
    assert classify(by[locators[0]]["runs"])["reason"] == "BEHAVIOR_CHANGED_WITHOUT_SPEC"
    assert classify(by[locators[1]]["runs"])["reason"] == "FLAKY"
    assert all(row["runs"] == ["passed"] for locator, row in by.items() if locator not in locators[:2])
    assert len(calls) == 3 and "-Dtest=" + ",".join(sorted([locators[0], locators[1]])) in calls[1]  # repeats run only the failed ones
    assert by[locators[0]]["failure"] == "expected 200 but was 500"


def _compile_failure(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch, where) -> tuple[dict, dict, Path]:
    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    path, line = where(project, manifest)
    output = f"[ERROR] COMPILATION ERROR :\n[ERROR] {project.as_posix()}/{path}:[{line},17] cannot find symbol\n"
    monkeypatch.setattr(suite_run, "RUNNER", lambda argv, **_kwargs: SimpleNamespace(returncode=1, stdout=output, stderr=""))
    return suite_run.run_suite(project, manifest, _module(project)), manifest, project


def _line_of(project: Path, text: str) -> int:
    return next(number for number, line in enumerate((project / GENERATED).read_text(encoding="utf-8").splitlines(), start=1) if text in line)


def test_a_compile_error_inside_a_method_sends_only_that_method_to_repair(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def inside(project, manifest):
        name = manifest["cases"][0]["methods"][0]["locator"].rsplit("#", 1)[1]
        return GENERATED, _line_of(project, f" {name}(") + 2

    result, manifest, _project = _compile_failure(tmp_path, step5, monkeypatch, inside)
    broken = [row["locator"] for row in result["methods"] if row["compile_error"]]
    assert broken == [manifest["cases"][0]["methods"][0]["locator"]] and len(result["commands"]) == 1
    assert result["build_broken"] == [] and suite_run.not_run_reason(result) is None
    assert {classify(row["runs"], compile_error=row["compile_error"])["outcome"] for row in result["methods"]} == {"REPAIR", "NOT_RUN"}


@pytest.mark.parametrize("where", ["import", "product", "no_line"])
def test_a_compile_error_outside_every_method_stops_the_run_for_a_person(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    """Review 2.1 item 4: an error in imports, SUPPORT or the product is no method's to repair or quarantine."""
    def locate(project, _manifest):
        if where == "import":
            return GENERATED, _line_of(project, "import ")
        if where == "product":
            return "src/main/java/net/javaguides/springboot/controller/StudentController.java", 12
        return GENERATED, "x"

    result, _manifest, _project = _compile_failure(tmp_path, step5, monkeypatch, locate)
    assert not any(row["compile_error"] for row in result["methods"])
    assert result["build_broken"]
    reason, message = suite_run.not_run_reason(result)
    assert reason == "SUITE_BUILD_BROKEN" and "outside" in message


def test_gradle_compile_errors_name_their_lines() -> None:
    output = "> Task :compileTestJava FAILED\nC:\\p\\src\\test\\java\\a\\ApiTest.java:42: error: cannot find symbol\n"
    assert suite_run.compile_lines_of(output) == [("/p/src/test/java/a/ApiTest.java", 42)]


_PRODUCT_TRACE = """jakarta.servlet.ServletException: Request processing failed: java.lang.IllegalStateException: storage unavailable
	at org.springframework.web.servlet.FrameworkServlet.processRequest(FrameworkServlet.java:1022)
	at org.springframework.test.web.servlet.MockMvc.perform(MockMvc.java:201)
	at net.javaguides.springboot.controller.StudentControllerPipelineTest.exchange(StudentControllerPipelineTest.java:40)
Caused by: java.lang.IllegalStateException: storage unavailable
	at net.javaguides.springboot.controller.StudentController.deleteStudent(StudentController.java:79)
"""
_TEST_TRACE = """java.lang.NullPointerException: Cannot invoke "String.length()" because "body" is null
	at net.javaguides.springboot.controller.StudentControllerPipelineTest.text(StudentControllerPipelineTest.java:61)
	at net.javaguides.springboot.controller.StudentControllerPipelineTest.tcB1001(StudentControllerPipelineTest.java:90)
"""


@pytest.mark.parametrize("trace,origin,outcome", [
    (_PRODUCT_TRACE, "PRODUCT", ("QUARANTINE", "BEHAVIOR_CHANGED_WITHOUT_SPEC")),
    (_TEST_TRACE, "TEST", ("REPAIR", "TEST_CODE_ERROR")),
])
def test_an_exception_from_the_product_is_a_behaviour_failure_not_a_repair(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch,
                                                                         trace: str, origin: str, outcome: tuple) -> None:
    """Review 2.1 item 11: JUnit <error> is REPAIR only when the test's own code broke."""
    from xml.sax.saxutils import escape

    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    locators = [case["methods"][0]["locator"] for case in manifest["cases"]]
    owner, name = locators[0].rsplit("#", 1)

    def fake(argv, **_kwargs):
        directory = project / "target" / "surefire-reports"
        directory.mkdir(parents=True, exist_ok=True)
        cases = [f'<testcase classname="{owner}" name="{locator.rsplit("#", 1)[1]}"/>' for locator in locators[1:]]
        message = escape(trace.splitlines()[0], {'"': "&quot;"})
        cases.append(f'<testcase classname="{owner}" name="{name}"><error message="{message}" type="x">{escape(trace)}</error></testcase>')
        (directory / f"TEST-{owner}.xml").write_text(f'<testsuite name="{owner}">{"".join(cases)}</testsuite>', encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="Tests run: 12, Errors: 1", stderr="")

    monkeypatch.setattr(suite_run, "RUNNER", fake)
    result = suite_run.run_suite(project, manifest, _module(project), repeats=1)
    row = next(item for item in result["methods"] if item["locator"] == locators[0])
    assert row["runs"] == ["broken", "broken"] and row["error_origin"] == origin
    decision = classify(row["runs"], product_error=row["error_origin"] == "PRODUCT")
    assert (decision["outcome"], decision["reason"]) == outcome


def test_a_parameterized_method_with_one_failing_invocation_is_not_flaky(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Independent review 2.3: the invocations of one parameterized method in one run are one outcome (the worst), not repeats."""
    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    locators = [case["methods"][0]["locator"] for case in manifest["cases"]]
    owner, name = locators[0].rsplit("#", 1)

    def fake(argv, **_kwargs):
        directory = project / "target" / "surefire-reports"
        directory.mkdir(parents=True, exist_ok=True)
        cases = [f'<testcase classname="{owner}" name="{locator.rsplit("#", 1)[1]}"/>' for locator in locators[1:]]
        cases += [f'<testcase classname="{owner}" name="{name}(String)[1]"/>',
                  f'<testcase classname="{owner}" name="{name}(String)[2]"><failure message="expected 200 but was 500"/></testcase>',
                  f'<testcase classname="{owner}" name="{name}(String)[3]"/>']
        (directory / f"TEST-{owner}.xml").write_text(f'<testsuite name="{owner}">{"".join(cases)}</testsuite>', encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="Tests run: 14, Failures: 1", stderr="")

    monkeypatch.setattr(suite_run, "RUNNER", fake)
    result = suite_run.run_suite(project, manifest, _module(project), repeats=2)
    row = next(item for item in result["methods"] if item["locator"] == locators[0])
    assert row["runs"] == ["failed", "failed", "failed"]
    assert classify(row["runs"])["reason"] == "BEHAVIOR_CHANGED_WITHOUT_SPEC"
    assert all(item["runs"] == ["passed"] for item in result["methods"] if item["locator"] != locators[0])


def test_quarantined_methods_run_explicitly(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    manifest["cases"][0]["status"] = "QUARANTINED"
    manifest["cases"][0]["quarantine"] = {"reason": "BEHAVIOR_CHANGED_WITHOUT_SPEC", "ref": "run x", "since_run": "a" * 32}
    _write(project, step5, manifest)
    locators = [case["methods"][0]["locator"] for case in manifest["cases"]]
    seen = []

    def fake(argv, **_kwargs):
        seen.append(list(argv))
        _surefire(project / "target" / "surefire-reports", locators[0].rsplit("#", 1)[0], {locator.rsplit("#", 1)[1]: "passed" for locator in locators})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(suite_run, "RUNNER", fake)
    result = suite_run.run_suite(project, manifest, _module(project))
    assert "-Djunit.jupiter.conditions.deactivate=org.junit.*DisabledCondition" in seen[0]
    quarantined = next(row for row in result["methods"] if row["locator"] == locators[0])
    assert quarantined["quarantined"] is True and classify(quarantined["runs"], quarantined=True)["outcome"] == "FIXED"
    json.dumps(result)  # the result is plain data
