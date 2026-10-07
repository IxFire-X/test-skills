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


def test_a_compile_error_sends_the_file_to_repair(tmp_path: Path, step5: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    import shutil

    shutil.copytree(step5["project"], project, ignore=shutil.ignore_patterns(".pilot-runs"))
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    output = f"[ERROR] COMPILATION ERROR :\n[ERROR] {project.as_posix()}/{GENERATED}:[42,17] cannot find symbol\n"
    monkeypatch.setattr(suite_run, "RUNNER", lambda argv, **_kwargs: SimpleNamespace(returncode=1, stdout=output, stderr=""))
    result = suite_run.run_suite(project, manifest, _module(project))
    assert all(row["compile_error"] for row in result["methods"]) and len(result["commands"]) == 1
    assert {classify(row["runs"], compile_error=True)["outcome"] for row in result["methods"]} == {"REPAIR"}


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
