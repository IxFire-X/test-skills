"""S2a (security review note 1): `.skillsrc` `modules[].test.offline: true` keeps the build and PIT off the network.

Maven gets ``-o``, Gradle ``--offline`` in every command of the module (tests, compile gate, suite run, the
mutation classpath and the PIT resolver).  The flag lives only in the module's closed configuration: a
recorded request proves it by its own argv.  Without the key every command is byte-for-byte as before.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_review_fixes_adapters import _executable, _host, _module, _reactor
from tools.execution_adapters import GRADLE, MAVEN, PYTEST, SYSTEM_MAVEN, AdapterRequestError, build_request, command_for_request, launch_layout


def _offline(module: dict) -> dict:
    return {**module, "test": {**module["test"], "offline": True}}


def test_maven_offline_adds_minus_o_to_the_tests_and_the_gate(tmp_path: Path) -> None:
    from tools.run_tests import build_closed_gate_request, gate_request_is_closed

    wrapper = _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _offline(_module(tmp_path, MAVEN, "mvnw")), [{"selector": "pkg.ATest#a"}])
    assert request.argv == (str(wrapper.resolve()), "-Dtest=pkg.ATest#a", "-o", "-B", "-ntp", "test")
    assert command_for_request(request) == (request.report_paths, request.argv)  # a receipt proves offline by its argv
    gate = build_closed_gate_request(request)
    assert gate.argv == (str(wrapper.resolve()), "-o", "-B", "-ntp", "test-compile") and gate_request_is_closed(gate, request)


def test_gradle_offline_adds_the_flag_to_the_tests_and_the_gate(tmp_path: Path) -> None:
    from tools.run_tests import build_closed_gate_request, gate_request_is_closed

    project, _module_dir, module = _reactor(tmp_path, GRADLE)
    request = build_request(GRADLE, _offline(module), [{"selector": "pkg.ATest#a"}])
    assert "--offline" in request.argv and request.argv.index("--offline") < request.argv.index("--init-script")
    assert command_for_request(request) == (request.report_paths, request.argv)
    gate = build_closed_gate_request(request)
    assert gate.argv[-1] == "--offline" and gate_request_is_closed(gate, request)


def test_without_the_key_the_commands_are_unchanged(tmp_path: Path) -> None:
    wrapper = _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _module(tmp_path, MAVEN, "mvnw"), [{"selector": "pkg.ATest#a"}])
    assert request.argv == (str(wrapper.resolve()), "-Dtest=pkg.ATest#a", "-B", "-ntp", "test")
    assert launch_layout(_module(tmp_path, MAVEN, "mvnw")).offline is False


@pytest.mark.parametrize("adapter_id,value", [(MAVEN, "yes"), (MAVEN, 1), (PYTEST, True)])
def test_offline_is_a_boolean_of_a_maven_or_gradle_module(tmp_path: Path, adapter_id: str, value) -> None:
    module = _module(tmp_path, adapter_id, "mvnw" if adapter_id == MAVEN else ".venv/bin/python")
    module["test"]["offline"] = value
    with pytest.raises(AdapterRequestError):
        launch_layout(module)


def test_the_skillsrc_schema_accepts_offline_for_build_tools_only() -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    source = (Path(__file__).resolve().parent / "fixtures" / "live-step5-20261006" / "project" / ".skillsrc").read_bytes().decode("utf-8")
    offline = source.replace("    adapter_parameters: {}", "    adapter_parameters: {}\n    offline: true", 1)
    assert parse_skillsrc_bytes(offline.encode("utf-8"))["modules"][0]["test"]["offline"] is True
    with pytest.raises(SkillsrcError):
        parse_skillsrc_bytes(source.replace("    adapter_parameters: {}", "    adapter_parameters: {}\n    offline: 'yes'", 1).encode("utf-8"))


def test_the_suite_run_builds_offline_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import suite_run

    _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    seen = []
    monkeypatch.setattr(suite_run, "RUNNER", lambda argv, **_kwargs: seen.append(list(argv)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    module = {"id": "root", "root": ".", "test": {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default",
                                                  "adapter_parameters": {}, "offline": True}}
    suite_run.run_once(tmp_path, module, ["pkg.ATest#a"], explicit=False, timeout=60)
    assert "-o" in seen[0]


# ------------------------------------------------------------------------------- mutations

def _execution(tmp_path: Path, adapter_id: str, offline: bool) -> dict:
    executable = str(tmp_path / ("mvnw" if adapter_id != GRADLE else "gradlew"))
    if adapter_id == GRADLE:
        argv = [executable, "cleanTest", "test", "--tests", "pkg.ATest.a", "--no-daemon", *(["--offline"] if offline else []), "--init-script", "x"]
    else:
        argv = [executable, "-Dtest=pkg.ATest#a", *(["-o"] if offline else []), "-B", "-ntp", "test"]
    return {"adapter_id": adapter_id, "argv": argv, "cwd": str(tmp_path), "executable_path": executable, "build_profile": "default"}


@pytest.mark.parametrize("adapter_id", [MAVEN, GRADLE])
def test_the_mutation_build_tool_inherits_offline_from_the_test_run(tmp_path: Path, adapter_id: str) -> None:
    from tools.mutation import BuildTool

    assert BuildTool.from_execution(_execution(tmp_path, adapter_id, True)).offline is True
    assert BuildTool.from_execution(_execution(tmp_path, adapter_id, False)).offline is False


def test_offline_classpath_and_resolver_run_with_minus_o_and_a_missing_artifact_is_a_tool_missing_stop(tmp_path: Path) -> None:
    from tools.mutation import BuildTool, MutationStop, load_pins, project_classpath, resolve_tool

    pins = load_pins()
    build = BuildTool.from_execution(_execution(tmp_path, MAVEN, True))
    calls = []

    def failing(argv, cwd, environment, timeout):
        calls.append(list(argv))
        return SimpleNamespace(exit_code=1, stdout="[ERROR] Cannot access central in offline mode and the artifact org.pitest:pitest:jar has not been downloaded from it before.", stderr="")

    for stage in (lambda: project_classpath(build, pins, tmp_path / "work", run=failing, timeout=60),
                  lambda: resolve_tool(build, pins, tmp_path / "work", "1.10.0", run=failing, timeout=60)):
        with pytest.raises(MutationStop) as stopped:
            stage()
        assert "-o" in calls[-1]
        assert (stopped.value.status, stopped.value.code) == ("NOT_RUNNABLE", "MUTATION_TOOL_MISSING")
        assert "offline: артефакт не найден в локальном репозитории" in str(stopped.value)


def test_offline_gradle_classpath_runs_with_the_offline_flag(tmp_path: Path) -> None:
    from tools.mutation import BuildTool, MutationStop, load_pins, project_classpath

    build = BuildTool.from_execution(_execution(tmp_path, GRADLE, True))
    (tmp_path / "work").mkdir()  # measure() creates the stage's work directory
    calls = []

    def failing(argv, cwd, environment, timeout):
        calls.append(list(argv))
        return SimpleNamespace(exit_code=1, stdout="No cached version of org.junit:junit-bom:5.10.0 available for offline mode.", stderr="")

    with pytest.raises(MutationStop) as stopped:
        project_classpath(build, load_pins(), tmp_path / "work", run=failing, timeout=60)
    assert "--offline" in calls[-1] and stopped.value.code == "MUTATION_TOOL_MISSING"


def test_online_resolver_failure_keeps_its_reason(tmp_path: Path) -> None:
    from tools.mutation import BuildTool, MutationStop, load_pins, resolve_tool

    build = BuildTool.from_execution(_execution(tmp_path, MAVEN, False))
    calls = []

    def failing(argv, cwd, environment, timeout):
        calls.append(list(argv))
        return SimpleNamespace(exit_code=1, stdout="network down", stderr="")

    with pytest.raises(MutationStop) as resolver:
        resolve_tool(build, load_pins(), tmp_path / "work", "1.10.0", run=failing, timeout=60)
    assert "-o" not in calls[-1] and resolver.value.code == "MUTATION_TOOL_RESOLUTION_FAILED"


# ------------------------------------------------------------------------------- the build stops honestly

MAVEN_OFFLINE = ("[ERROR] Failed to execute goal on project demo: Could not resolve dependencies for project g:demo:jar:1: "
                 "Cannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact "
                 "org.junit.jupiter:junit-jupiter:jar:5.10.0 has not been downloaded from it before. -> [Help 1]")
MAVEN_OFFLINE_PLUGIN = ("[ERROR] Plugin org.apache.maven.plugins:maven-surefire-plugin:3.2.5 or one of its dependencies could not be resolved: "
                        "Cannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact "
                        "org.apache.maven.plugins:maven-surefire-plugin:jar:3.2.5 has not been downloaded from it before. -> [Help 1]")
GRADLE_OFFLINE = ("> Could not resolve all files for configuration ':testCompileClasspath'.\n"
                  "   > Could not resolve org.junit:junit-bom:5.10.0.\n     > No cached version of org.junit:junit-bom:5.10.0 available for offline mode.")


@pytest.mark.parametrize("adapter_id,output,artifact", [
    (MAVEN, MAVEN_OFFLINE, "org.junit.jupiter:junit-jupiter:jar:5.10.0"),
    (SYSTEM_MAVEN, MAVEN_OFFLINE_PLUGIN, "org.apache.maven.plugins:maven-surefire-plugin:jar:3.2.5"),
    (GRADLE, GRADLE_OFFLINE, "org.junit:junit-bom:5.10.0"),
])
def test_an_artifact_missing_offline_is_a_build_failure_before_the_tests_not_a_test_defect(adapter_id: str, output: str, artifact: str) -> None:
    from tools.run_tests import ProcessOutcome, build_failure_before_tests, gate_failure_kind

    step = build_failure_before_tests(adapter_id, output)
    assert step == f"offline: артефакт не найден в локальном репозитории ({artifact})"
    assert gate_failure_kind(adapter_id, ProcessOutcome(1, output, "")) == "BUILD_FAILED_BEFORE_TESTS"


def test_the_build_failure_summary_says_offline_and_never_regenerate(tmp_path: Path) -> None:
    from tools import pipeline_driver as driver

    run_root = tmp_path / "run"
    (run_root / "artifacts" / "a").mkdir(parents=True)
    attempt = {"attempt_id": "a"}
    (run_root / "artifacts" / "a" / "runner-output.txt").write_text(MAVEN_OFFLINE + "\n", encoding="utf-8")
    message = driver._build_failure(run_root, attempt)["message"]
    assert "offline: артефакт не найден в локальном репозитории" in message and "перегенерация тестов не нужна" in message
