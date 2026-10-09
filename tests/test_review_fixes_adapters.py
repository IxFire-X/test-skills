"""Regression tests for review items B5, M17, M33, P07, M35 and M36.

Launch adapters must start multi-module builds from the build root, keep
``.skillsrc`` free of host-specific paths, and the service CLIs must report
usage errors and missing runners truthfully.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools.execution_adapters import GRADLE, GRADLE_SELECTED_INIT, MAVEN, PYTEST, SYSTEM_MAVEN


POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>{name}</artifactId><version>1</version>{body}</project>"
JUNIT = "<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version></dependency></dependencies>"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("runtime", encoding="utf-8")
    path.chmod(0o755)
    return path


def _host(posix: str, windows: str) -> str:
    return windows if os.name == "nt" else posix


def _module(module_dir: Path, adapter_id: str, runtime: str, **extra) -> dict:
    key = "interpreter" if adapter_id == PYTEST else "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"
    module = {
        "module_root": str(module_dir),
        "test": {"adapter_id": adapter_id, key: runtime, "build_profile": "default", "adapter_parameters": {}},
    }
    for name in ("build_root", "timeout_seconds", "build_profile"):
        if name in extra:
            module["test"][name] = extra.pop(name)
    module.update(extra)
    return module


def _reactor(tmp_path: Path, adapter_id: str) -> tuple[Path, Path, dict]:
    """A build root with one nested module ``services/api`` and a root-level wrapper."""
    project = tmp_path / "project"
    module = project / "services" / "api"
    module.mkdir(parents=True)
    if adapter_id == GRADLE:
        (project / "settings.gradle").write_text("include 'services:api'\n", encoding="utf-8")
        _executable(project / _host("gradlew", "gradlew.bat"))
        runtime = "gradlew"
    else:
        (project / "pom.xml").write_text(POM.format(name="root", body="<packaging>pom</packaging><modules><module>services/api</module></modules>"), encoding="utf-8")
        (module / "pom.xml").write_text(POM.format(name="api", body=JUNIT), encoding="utf-8")
        _executable(project / _host("mvnw", "mvnw.cmd"))
        runtime = "mvnw"
    return project, module, _module(module, adapter_id, runtime, build_root=".", root="services/api")


# --- B5 / M17 / P07: argv ---------------------------------------------------


def test_maven_single_module_argv_has_batch_flags_and_no_undeclared_profile(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    wrapper = _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _module(tmp_path, MAVEN, "mvnw"), [{"selector": "pkg.ATest#a"}, {"selector": "pkg.BTest#b"}])

    assert request.argv == (str(wrapper.resolve()), "-Dtest=pkg.ATest#a,pkg.BTest#b", "-B", "-ntp", "test")
    assert not any(token.startswith("-P") for token in request.argv)
    assert request.cwd == str(tmp_path.resolve())
    assert request.report_paths == ("target/surefire-reports",)


# Live run f (2026-10-09): 81 methods of one generated class gave `-Dtest=<FQCN>#m1,<FQCN>#m2,…` of 8.4k characters;
# mvnw.cmd runs under cmd.exe, which refuses a line over 8191 characters, so no test ran and the verdict was UNKNOWN.
PETCLINIC_CLASS = "org.springframework.samples.petclinic.lifecycle.OwnerLifecycleFormTests"
PETCLINIC_METHODS = tuple(f"lifecycleCase{index:02d}ChecksOneObservableRule" for index in range(81))


def test_maven_selectors_of_one_class_share_one_surefire_pattern(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    wrapper = _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    targets = [{"selector": "pkg.ATest#a"}, {"selector": "pkg.BTest#b"}, {"selector": "pkg.ATest#c"}]
    request = build_request(MAVEN, _module(tmp_path, MAVEN, "mvnw"), targets)

    assert request.argv == (str(wrapper.resolve()), "-Dtest=pkg.ATest#a+c,pkg.BTest#b", "-B", "-ntp", "test")
    assert request.selectors == ("pkg.ATest#a", "pkg.BTest#b", "pkg.ATest#c")


def test_maven_command_for_the_petclinic_class_fits_a_cmd_exe_line(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    wrapper = tmp_path / "live" / "petclinic-20261009f" / "mvnw.cmd"
    wrapper.parent.mkdir(parents=True)
    _executable(wrapper)
    module = _module(wrapper.parent, MAVEN, "mvnw")
    request = build_request(MAVEN, module, [{"selector": f"{PETCLINIC_CLASS}#{name}"} for name in PETCLINIC_METHODS])

    assert len(subprocess.list2cmdline(request.argv)) < 8191 - 512  # mvnw.cmd and mvn.cmd add their own paths to the line
    assert request.argv[1] == f"-Dtest={PETCLINIC_CLASS}#" + "+".join(PETCLINIC_METHODS)


def test_a_receipt_recorded_with_one_pattern_per_method_still_verifies(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request, command_for_request

    _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _module(tmp_path, MAVEN, "mvnw"), [{"selector": "pkg.ATest#a"}, {"selector": "pkg.ATest#c"}])
    earlier = replace(request, argv=(request.argv[0], "-Dtest=pkg.ATest#a,pkg.ATest#c", *request.argv[2:]))

    assert command_for_request(request)[1] == request.argv
    assert command_for_request(earlier)[1] == earlier.argv


def test_maven_profile_declared_in_skillsrc_is_passed(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _module(tmp_path, MAVEN, "mvnw", build_profile="ci"), [{"selector": "pkg.ATest#a"}])

    assert request.argv[1:] == ("-Pci", "-Dtest=pkg.ATest#a", "-B", "-ntp", "test")


def test_maven_module_runs_from_the_reactor_root_with_pl_am(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request
    from tools.run_tests import _request_is_attempt_local, _request_report_path

    project, module, config = _reactor(tmp_path, MAVEN)
    request = build_request(MAVEN, config, [{"selector": "pkg.ATest#a"}])
    wrapper = (project / _host("mvnw", "mvnw.cmd")).resolve()

    assert request.cwd == str(project.resolve())
    assert request.executable == str(wrapper)
    assert request.argv == (
        str(wrapper), "-pl", "services/api", "-am", "-Dtest=pkg.ATest#a",
        "-Dsurefire.failIfNoSpecifiedTests=false", "-B", "-ntp", "test",
    )
    # Reports stay bound to the module, not to the launch directory.
    assert request.report_paths == ("target/surefire-reports",)
    assert _request_report_path(request) == module.resolve() / "target/surefire-reports"
    assert _request_is_attempt_local(request, project.resolve(), module.resolve())
    assert not _request_is_attempt_local(request, project.resolve(), (project / "services").resolve())


def test_system_maven_module_runs_from_the_reactor_root(tmp_path: Path, monkeypatch) -> None:
    from tools.execution_adapters import build_request

    project, module, config = _reactor(tmp_path, MAVEN)
    launcher = _executable(tmp_path / "bin" / _host("mvn", "mvn.cmd"))
    monkeypatch.setenv("PATH", str(launcher.parent))
    config["test"] = {"adapter_id": SYSTEM_MAVEN, "executable": "mvn", "build_profile": "default", "adapter_parameters": {}, "build_root": "."}

    request = build_request(SYSTEM_MAVEN, config, [{"selector": "pkg.ATest#a"}])

    assert request.cwd == str(project.resolve())
    assert request.argv[1:4] == ("-pl", "services/api", "-am")
    assert request.argv[-3:] == ("-B", "-ntp", "test")


def test_gradle_single_project_reruns_only_the_test_task(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    wrapper = _executable(tmp_path / _host("gradlew", "gradlew.bat"))
    request = build_request(GRADLE, _module(tmp_path, GRADLE, "gradlew"), [{"selector": "pkg.ATest#a"}, {"selector": "pkg.BTest#b"}])

    assert request.argv == (
        str(wrapper.resolve()), ":cleanTest", ":test", "--tests", "pkg.ATest.a", "--tests", "pkg.BTest.b", "--no-daemon", "--init-script", str(GRADLE_SELECTED_INIT),
    )
    assert "--rerun-tasks" not in request.argv


def test_gradle_subproject_runs_its_own_task_from_the_settings_root(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request
    from tools.run_tests import _request_report_path

    project, module, config = _reactor(tmp_path, GRADLE)
    config["test"]["build_profile"] = "ci"
    request = build_request(GRADLE, config, [{"selector": "pkg.ATest#a"}])
    wrapper = (project / _host("gradlew", "gradlew.bat")).resolve()

    assert request.cwd == str(project.resolve())
    assert request.argv == (
        str(wrapper), ":services:api:cleanTest", ":services:api:test", "--tests", "pkg.ATest.a", "-Pprofile=ci", "--no-daemon", "--init-script", str(GRADLE_SELECTED_INIT),
    )
    assert _request_report_path(request) == module.resolve() / "build/test-results/test"


@pytest.mark.parametrize("adapter_id", [MAVEN, GRADLE])
def test_build_root_must_be_an_ancestor_holding_the_root_manifest(tmp_path: Path, adapter_id: str) -> None:
    from tools.execution_adapters import AdapterRequestError, build_request, classify_prestart

    project, _module_dir, config = _reactor(tmp_path, adapter_id)
    selectors = [{"selector": "pkg.ATest#a"}]

    sibling = {**config, "test": {**config["test"], "build_root": "other"}}
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request(adapter_id, sibling, selectors)

    (project / ("settings.gradle" if adapter_id == GRADLE else "pom.xml")).unlink()
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request(adapter_id, config, selectors)
    assert classify_prestart(config) == "NOT_RUNNABLE"


def test_pytest_module_cannot_declare_a_build_root(tmp_path: Path) -> None:
    from tools.execution_adapters import AdapterRequestError, build_request

    _executable(tmp_path / "api" / ".venv" / "python")
    config = _module(tmp_path / "api", PYTEST, ".venv/python", build_root=".", root="api")
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request(PYTEST, config, [{"selector": "tests/test_a.py::test_a"}])


# --- compile/collect gate commands -------------------------------------------


def test_gate_command_for_pytest_collects_only_the_selected_nodes(tmp_path: Path) -> None:
    from tools.execution_adapters import build_gate_command, build_request

    interpreter = _executable(tmp_path / ".venv" / "python")
    module = _module(tmp_path, PYTEST, ".venv/python", timeout_seconds=90)
    selectors = [{"selector": "tests/test_a.py::test_a"}, {"selector": "tests/test_b.py::TestB::test_b"}]
    gate, run = build_gate_command(PYTEST, module, selectors), build_request(PYTEST, module, selectors)

    assert gate.argv == (str(interpreter), "-m", "pytest", "--collect-only", "-q", "tests/test_a.py::test_a", "tests/test_b.py::TestB::test_b")
    assert (gate.executable, gate.cwd, gate.selectors, gate.timeout_seconds) == (run.executable, run.cwd, run.selectors, 90)
    assert gate.report_paths == ()
    assert "--junitxml" not in gate.argv


def test_gate_command_for_maven_compiles_tests_single_and_reactor(tmp_path: Path) -> None:
    from tools.execution_adapters import build_gate_command

    single = tmp_path / "single"
    wrapper = _executable(single / _host("mvnw", "mvnw.cmd"))
    gate = build_gate_command(MAVEN, _module(single, MAVEN, "mvnw", build_profile="ci"), [{"selector": "pkg.ATest#a"}])
    assert gate.argv == (str(wrapper.resolve()), "-Pci", "-B", "-ntp", "test-compile")
    assert gate.cwd == str(single.resolve())

    project, _module_dir, config = _reactor(tmp_path, MAVEN)
    gate = build_gate_command(MAVEN, config, [{"selector": "pkg.ATest#a"}])
    assert gate.argv[1:] == ("-pl", "services/api", "-am", "-B", "-ntp", "test-compile")
    assert gate.cwd == str(project.resolve())
    assert not any(token.startswith("-Dtest=") for token in gate.argv)


def test_gate_command_for_gradle_builds_test_classes_without_daemon(tmp_path: Path) -> None:
    from tools.execution_adapters import build_gate_command

    single = tmp_path / "single"
    wrapper = _executable(single / _host("gradlew", "gradlew.bat"))
    gate = build_gate_command(GRADLE, _module(single, GRADLE, "gradlew"), [{"selector": "pkg.ATest#a"}])
    assert gate.argv == (str(wrapper.resolve()), ":testClasses", "--no-daemon")

    project, _module_dir, config = _reactor(tmp_path, GRADLE)
    gate = build_gate_command(GRADLE, config, [{"selector": "pkg.ATest#a"}])
    assert gate.argv[1:] == (":services:api:testClasses", "--no-daemon")
    assert gate.cwd == str(project.resolve())


def test_gate_command_rejects_what_the_run_command_rejects(tmp_path: Path) -> None:
    from tools.execution_adapters import AdapterRequestError, build_gate_command

    _executable(tmp_path / ".venv" / "python")
    module = _module(tmp_path, PYTEST, ".venv/python")
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_gate_command(PYTEST, module, [])
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_gate_command(PYTEST, module, [{"selector": "tests/test_a.py::test_a; rm -rf /"}])
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_gate_command("unknown", module, [{"selector": "tests/test_a.py::test_a"}])


# --- P07: timeout -------------------------------------------------------------


def test_timeout_comes_from_skillsrc_and_defaults_to_600(tmp_path: Path) -> None:
    from tools.execution_adapters import AdapterRequestError, build_request, invoke_request

    _executable(tmp_path / ".venv" / "python")
    selectors = [{"selector": "tests/test_a.py::test_a"}]

    assert build_request(PYTEST, _module(tmp_path, PYTEST, ".venv/python"), selectors).timeout_seconds == 600
    request = build_request(PYTEST, _module(tmp_path, PYTEST, ".venv/python", timeout_seconds=1800), selectors)
    assert request.timeout_seconds == 1800
    calls = []
    invoke_request(request, lambda *args, **kwargs: calls.append(kwargs))
    assert calls[0]["timeout"] == 1800
    for invalid in (0, -5, 86401, True, "600", 1.5):
        with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
            build_request(PYTEST, _module(tmp_path, PYTEST, ".venv/python", timeout_seconds=invalid), selectors)


# --- M33: logical runtime names ----------------------------------------------


def test_logical_runtime_names_map_to_the_host_layout() -> None:
    from tools.execution_adapters import runtime_candidates

    assert runtime_candidates(PYTEST, "python", os_name="posix")[0] == ".venv/bin/python"
    assert runtime_candidates(PYTEST, "python", os_name="nt")[0] == ".venv/Scripts/python.exe"
    assert runtime_candidates(MAVEN, "mvnw", os_name="posix") == ("mvnw",)
    assert runtime_candidates(MAVEN, "mvnw", os_name="nt") == ("mvnw.cmd",)
    assert runtime_candidates(GRADLE, "gradlew", os_name="nt") == ("gradlew.bat",)
    assert runtime_candidates(GRADLE, "gradlew", os_name="posix") == ("gradlew",)


def test_legacy_host_specific_names_are_tried_literally_then_translated() -> None:
    from tools.execution_adapters import runtime_candidates

    # A file committed on Linux and read on Windows, and the reverse.
    assert runtime_candidates(PYTEST, ".venv/bin/python", os_name="nt") == (".venv/bin/python", ".venv/Scripts/python.exe")
    assert runtime_candidates(PYTEST, ".venv/Scripts/python.exe", os_name="posix") == (".venv/Scripts/python.exe", ".venv/bin/python")
    assert runtime_candidates(PYTEST, "env/bin/python3.12", os_name="nt") == ("env/bin/python3.12", "env/Scripts/python.exe")
    assert runtime_candidates(MAVEN, "mvnw.cmd", os_name="posix") == ("mvnw",)
    assert runtime_candidates(GRADLE, "tools/gradlew.bat", os_name="posix") == ("tools/gradlew",)
    # Same host: exactly the old meaning.
    assert runtime_candidates(PYTEST, ".venv/bin/python", os_name="posix") == (".venv/bin/python",)
    assert runtime_candidates(PYTEST, "runtime/python", os_name="nt") == ("runtime/python",)
    assert runtime_candidates(MAVEN, "mvnw.cmd", os_name="nt") == ("mvnw.cmd",)


def test_logical_python_resolves_the_module_venv_of_this_host(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request, classify_prestart, resolve_module_runtime

    interpreter = _executable(tmp_path / _host(".venv/bin/python", ".venv/Scripts/python.exe"))
    module = _module(tmp_path, PYTEST, "python")

    request = build_request(PYTEST, module, [{"selector": "tests/test_a.py::test_a"}])
    assert request.executable == str(interpreter.resolve())
    assert classify_prestart(module) == "READY"
    module["test"]["framework"] = "pytest"
    assert resolve_module_runtime(module) == (tmp_path.resolve(), _host(".venv/bin/python", ".venv/Scripts/python.exe"))


def test_skillsrc_written_on_the_other_host_still_finds_the_runtime(tmp_path: Path) -> None:
    from tools.execution_adapters import build_request

    interpreter = _executable(tmp_path / _host(".venv/bin/python", ".venv/Scripts/python.exe"))
    foreign = _host(".venv/Scripts/python.exe", ".venv/bin/python")
    request = build_request(PYTEST, _module(tmp_path, PYTEST, foreign), [{"selector": "tests/test_a.py::test_a"}])
    assert request.executable == str(interpreter.resolve())

    wrapper = _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    request = build_request(MAVEN, _module(tmp_path, MAVEN, _host("mvnw.cmd", "mvnw")), [{"selector": "pkg.ATest#a"}])
    assert request.executable == str(wrapper.resolve())


def test_init_writes_logical_names_not_host_paths(tmp_path: Path, monkeypatch) -> None:
    from tools import init_skillsrc
    from tools.discover_project import discover_project
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    python_project = tmp_path / "py"
    (python_project / "src" / "app").mkdir(parents=True)
    (python_project / "src" / "app" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (python_project / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\ndependencies = ["pytest"]\n', encoding="utf-8")
    discovery = discover_project(python_project)
    answers = {question["id"]: question["options"][0]["id"] for question in discovery["questions"]}
    compiled = init_skillsrc.compile_skillsrc(discovery, answers, python_project)
    assert compiled["modules"][0]["test"]["interpreter"] == "python"

    java_project = tmp_path / "java"
    (java_project / "src" / "main" / "java").mkdir(parents=True)
    (java_project / "src" / "main" / "java" / "App.java").write_text("class App {}", encoding="utf-8")
    (java_project / "pom.xml").write_text(POM.format(name="app", body=JUNIT), encoding="utf-8")
    launcher = _executable(tmp_path / "bin" / _host("mvn", "mvn.cmd"))
    monkeypatch.setenv("PATH", str(launcher.parent))
    compiled = init_skillsrc.compile_skillsrc(discover_project(java_project), {}, java_project)
    assert compiled["modules"][0]["test"]["executable"] == "mvn"

    for name in ("mvnw", "mvnw.cmd"):
        _executable(java_project / name)
    compiled = init_skillsrc.compile_skillsrc(discover_project(java_project), {}, java_project)
    test = compiled["modules"][0]["test"]
    assert test["wrapper"] == "mvnw" and "build_root" not in test
    parse_skillsrc_bytes(init_skillsrc._payload(compiled))


def test_existing_skillsrc_with_legacy_host_paths_is_not_reported_as_drift(tmp_path: Path) -> None:
    from tools import init_skillsrc
    from tools.discover_project import discover_project

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1"\ndependencies = ["pytest"]\n', encoding="utf-8")
    discovery = discover_project(tmp_path)
    answers = {question["id"]: question["options"][0]["id"] for question in discovery["questions"]}
    compiled = init_skillsrc.compile_skillsrc(discovery, answers, tmp_path)
    for legacy in (".venv/bin/python", ".venv/Scripts/python.exe"):
        compiled["modules"][0]["test"]["interpreter"] = legacy
        (tmp_path / ".skillsrc").write_bytes(init_skillsrc._payload(compiled))
        receipt = init_skillsrc.ensure_skillsrc(tmp_path, answers, write=True)
        assert receipt["status"] == "unchanged", receipt
        assert legacy in (tmp_path / ".skillsrc").read_text(encoding="utf-8")

    compiled["modules"][0]["test"]["interpreter"] = "custom/python"
    (tmp_path / ".skillsrc").write_bytes(init_skillsrc._payload(compiled))
    assert init_skillsrc.ensure_skillsrc(tmp_path, answers, write=True)["status"] == "needs_input"


# --- B5: build root in .skillsrc ----------------------------------------------


def _java_source(module: Path) -> None:
    source = module / "src" / "main" / "java" / "App.java"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("class App {}", encoding="utf-8")


def test_init_detects_the_maven_reactor_root_for_a_submodule(tmp_path: Path, monkeypatch) -> None:
    from tools import init_skillsrc
    from tools.discover_project import discover_project
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "pom.xml").write_text(POM.format(name="root", body="<packaging>pom</packaging><modules><module>services</module></modules>"), encoding="utf-8")
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "pom.xml").write_text(POM.format(name="services", body="<packaging>pom</packaging><modules><module>api</module></modules>"), encoding="utf-8")
    api = tmp_path / "services" / "api"
    _java_source(api)
    (api / "pom.xml").write_text(POM.format(name="api", body=JUNIT), encoding="utf-8")
    for name in ("mvnw", "mvnw.cmd"):
        _executable(tmp_path / name)

    compiled = init_skillsrc.compile_skillsrc(discover_project(tmp_path), {}, tmp_path)
    tests = {module["root"]: module.get("test") for module in compiled["modules"]}

    assert tests["services/api"] == {
        "framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw",
        "build_profile": "default", "adapter_parameters": {}, "build_root": ".",
    }
    parse_skillsrc_bytes(init_skillsrc._payload(compiled))


def test_init_detects_the_gradle_settings_root_for_a_subproject(tmp_path: Path) -> None:
    from tools import init_skillsrc
    from tools.discover_project import discover_project

    (tmp_path / "settings.gradle").write_text("include 'app'\n", encoding="utf-8")
    app = tmp_path / "app"
    _java_source(app)
    (app / "build.gradle").write_text("dependencies { testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0' }\n", encoding="utf-8")
    for name in ("gradlew", "gradlew.bat"):
        _executable(tmp_path / name)

    compiled = init_skillsrc.compile_skillsrc(discover_project(tmp_path), {}, tmp_path)
    tests = {module["root"]: module.get("test") for module in compiled["modules"]}

    assert tests["app"]["wrapper"] == "gradlew"
    assert tests["app"]["build_root"] == "."
    assert tests["app"]["adapter_id"] == GRADLE


def _manifest(test: dict, root: str = "services/api", stack: dict | None = None) -> bytes:
    return json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "api", "root": root, "stack": stack or {"language": "java", "build_tool": "maven"}, "test": test, "detected_from": ["pom.xml"]}],
    }).encode("utf-8")


def test_skillsrc_schema_accepts_the_additive_optional_fields() -> None:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    maven = {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default", "adapter_parameters": {}}
    # Files written before the fields existed stay valid unchanged.
    assert parse_skillsrc_bytes(_manifest(maven))["schema_version"] == "5.0.0"
    parsed = parse_skillsrc_bytes(_manifest({**maven, "build_root": ".", "timeout_seconds": 1200}))
    assert parsed["modules"][0]["test"]["build_root"] == "."
    assert parsed["modules"][0]["test"]["timeout_seconds"] == 1200
    parse_skillsrc_bytes(_manifest({**maven, "build_root": "services"}))
    parse_skillsrc_bytes(_manifest({"framework": "junit5", "adapter_id": SYSTEM_MAVEN, "executable": "mvn", "build_profile": "default", "adapter_parameters": {}, "build_root": "."}))
    parse_skillsrc_bytes(_manifest(
        {"framework": "junit5", "adapter_id": GRADLE, "wrapper": "gradlew", "build_profile": "default", "adapter_parameters": {}, "build_root": ".", "timeout_seconds": 60},
        stack={"language": "java", "build_tool": "gradle"},
    ))
    parse_skillsrc_bytes(_manifest(
        {"framework": "pytest", "adapter_id": PYTEST, "interpreter": "python", "build_profile": "default", "adapter_parameters": {}, "timeout_seconds": 30},
        stack={"language": "python", "build_tool": "pip"},
    ))


@pytest.mark.parametrize("extra", [
    {"build_root": "other"},
    {"build_root": "services/api/deeper"},
    {"build_root": "../outside"},
    {"build_root": "/abs"},
    {"timeout_seconds": 0},
    {"timeout_seconds": 86401},
    {"timeout_seconds": "600"},
])
def test_skillsrc_rejects_unsafe_build_root_and_timeout(extra: dict) -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    maven = {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default", "adapter_parameters": {}}
    with pytest.raises(SkillsrcError):
        parse_skillsrc_bytes(_manifest({**maven, **extra}))


def test_skillsrc_rejects_build_root_for_a_pytest_module() -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    with pytest.raises(SkillsrcError):
        parse_skillsrc_bytes(_manifest(
            {"framework": "pytest", "adapter_id": PYTEST, "interpreter": "python", "build_profile": "default", "adapter_parameters": {}, "build_root": "."},
            stack={"language": "python", "build_tool": "pip"},
        ))


# --- B5: the recorded request stays verifiable --------------------------------


@pytest.mark.parametrize("adapter_id", [MAVEN, GRADLE])
def test_recorded_multi_module_request_is_closed_and_tamper_evident(tmp_path: Path, adapter_id: str) -> None:
    from tools.execution_adapters import build_request, command_for_request, request_launch_is_closed, request_module_root

    project, module, config = _reactor(tmp_path, adapter_id)
    config["test"]["timeout_seconds"] = 900
    request = build_request(adapter_id, config, [{"selector": "pkg.ATest#a"}])

    assert request_module_root(request) == module.resolve()
    assert command_for_request(request) == (request.report_paths, request.argv)
    assert request_launch_is_closed(request, project, module)
    # Another module, a cwd outside the project, an extra flag, or an open timeout are all rejected.
    assert not request_launch_is_closed(request, project, project / "services")
    assert not request_launch_is_closed(request, module, module)
    assert not request_launch_is_closed(replace(request, argv=(*request.argv, "-X")), project, module)
    assert not request_launch_is_closed(replace(request, timeout_seconds=0), project, module)
    assert not request_launch_is_closed(replace(request, cwd=str(module)), project, module)


def test_closed_request_built_by_the_runner_keeps_build_root_and_timeout(tmp_path: Path, monkeypatch) -> None:
    from tools import run_tests

    project, module, config = _reactor(tmp_path, MAVEN)
    config["test"].update(framework="junit5", timeout_seconds=1500)
    config.pop("module_root")
    pair = ("file-1", "symbol-1")
    compatibility = run_tests.RunnerCompatibility(
        "READY", (pair,),
        {pair: {"path": module / "src/test/java/pkg/ATest.java", "relative_path": "src/test/java/pkg/ATest.java", "locator": {"class_fqn": "pkg.ATest", "method_name": "a"}}},
        {"file-1": "sha256:" + "a" * 64}, (),
    )
    monkeypatch.setattr(run_tests, "validate_artifact_runner_compatibility", lambda *_args: compatibility)

    request, _compatibility = run_tests.build_closed_execution_request(module, "java", config, {}, {})

    assert request.cwd == str(project.resolve())
    assert request.timeout_seconds == 1500
    assert request.argv[1:4] == ("-pl", "services/api", "-am")


def test_baseline_binds_the_wrapper_at_the_build_root(tmp_path: Path) -> None:
    from tools.project_inventory import _runtime_base

    project, module, config = _reactor(tmp_path, MAVEN)
    document = {"modules": [{"id": "api", "root": "services/api", "test": {**config["test"], "framework": "junit5"}}]}

    assert _runtime_base(module.resolve(), document, {"module": "services/api"}) == project.resolve()
    document["modules"][0]["test"].pop("build_root")
    assert _runtime_base(module.resolve(), document, {"module": "services/api"}) == module.resolve()


def test_module_lookup_by_root_reads_the_authoritative_skillsrc(tmp_path: Path) -> None:
    from tools.skillsrc_manifest import SkillsrcError, load_module_by_root

    maven = {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default", "adapter_parameters": {}, "build_root": ".", "timeout_seconds": 700}
    (tmp_path / ".skillsrc").write_bytes(_manifest(maven))

    assert load_module_by_root(tmp_path, "services/api")["test"]["timeout_seconds"] == 700
    with pytest.raises(SkillsrcError):
        load_module_by_root(tmp_path, "services")


# --- JUnit report parsing for both build tools --------------------------------


SUREFIRE = b'<testsuite name="pkg.ATest" tests="2" errors="0" failures="0" skipped="0"><testcase name="a" classname="pkg.ATest" time="0.01"/><testcase name="b" classname="pkg.ATest" time="0.01"/></testsuite>'
GRADLE_XML = b'<?xml version="1.0" encoding="UTF-8"?><testsuite name="pkg.ATest" tests="1" skipped="0" failures="1" errors="0"><testcase name="a()" classname="pkg.ATest" time="0.02"><failure message="boom" type="org.opentest4j.AssertionFailedError">trace</failure></testcase><system-out><![CDATA[]]></system-out></testsuite>'


def test_surefire_and_gradle_reports_classify_by_exact_selector() -> None:
    from tools.execution_adapters import classify_execution, is_zero_test_report

    assert classify_execution(controller_timed_out=False, process_returncode=0, report_bytes=SUREFIRE, selectors=("pkg.ATest#a", "pkg.ATest#b")) == "PASS"
    # A selector missing from the report is never a pass.
    assert classify_execution(controller_timed_out=False, process_returncode=0, report_bytes=SUREFIRE, selectors=("pkg.ATest#a", "pkg.ATest#missing")) == "UNKNOWN"
    failed = GRADLE_XML.replace(b'name="a()"', b'name="a"')
    assert classify_execution(controller_timed_out=False, process_returncode=1, report_bytes=failed, selectors=("pkg.ATest#a",)) == "FAIL"
    assert classify_execution(controller_timed_out=True, process_returncode=None, report_bytes=SUREFIRE, selectors=("pkg.ATest#a", "pkg.ATest#b")) == "UNKNOWN"
    empty = b'<testsuite name="pkg.ATest" tests="0" errors="0" failures="0" skipped="0"></testsuite>'
    assert is_zero_test_report(empty) and not is_zero_test_report(SUREFIRE)


# --- M35: orchestration CLI usage errors --------------------------------------


@pytest.mark.parametrize(("argv", "needle"), [
    (["publish", "--run-root", "r", "--attempt-id", "a", "--candidate", "c", "--output-dir", "o", "--no-such-flag"], "--no-such-flag"),
    (["select", "--run-root", "r", "--attempt-id", "a", "--output-dir", "o"], "--candidate-receipt"),
    (["submit-part", "--run-root", "r", "--attempt-id", "a", "--part-id", "p", "--assessment", "x", "--transport-attempts", "many"], "--transport-attempts"),
    (["no-such-command"], "no-such-command"),
    ([], "command"),
])
def test_orchestration_cli_reports_the_argparse_message(argv: list[str], needle: str, capsys) -> None:
    from tools import orchestrate_test_case_revision as orchestration

    with pytest.raises(SystemExit) as exit_info:
        orchestration.main(argv)

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    payload = json.loads(captured.out)
    assert payload["status"] == "error" and payload["code"] == "ORCHESTRATION_INPUT"
    diagnostic = payload["diagnostics"][0]
    assert diagnostic["code"] == "ORCHESTRATION_ARGUMENT"
    assert needle in diagnostic["message"], diagnostic
    assert diagnostic["message"] != "Orchestration failed."
    assert needle in captured.err
    assert len(captured.out.strip().splitlines()) == 1


def test_orchestration_cli_usage_error_exit_code_in_a_real_process(pack_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.orchestrate_test_case_revision", "publish", "--bogus"],
        cwd=pack_root, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )

    assert result.returncode == 2
    assert "--run-root" in json.loads(result.stdout)["diagnostics"][0]["message"]
    assert "--run-root" in result.stderr


# --- M36: ci_gate --------------------------------------------------------------


def test_ci_gate_without_pytest_is_not_runnable(pack_root: Path, monkeypatch, capsys) -> None:
    from tools import ci_gate

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        # What ``python -m pytest`` really does when the package is absent.
        return subprocess.CompletedProcess(command, 1 if "pytest" in command else 0)

    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(ci_gate.subprocess, "run", run)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None if name == "pytest" else real_find_spec(name, *args))

    assert ci_gate.main(["--root", str(pack_root)]) == 2
    assert "NOT_RUNNABLE" in capsys.readouterr().err
    assert not any("pytest" in command for command in calls)


def test_ci_gate_children_run_with_a_timeout(pack_root: Path, monkeypatch) -> None:
    from tools import ci_gate

    seen = []

    def run(command, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(ci_gate.subprocess, "run", run)

    assert ci_gate.main(["--root", str(pack_root), "--check-timeout", "11", "--pytest-timeout", "22"]) == 0
    assert seen == [11, 11, 22]
    seen.clear()
    assert ci_gate.main(["--root", str(pack_root)]) == 0
    assert all(isinstance(value, int) and value > 0 for value in seen) and len(seen) == 3


def test_ci_gate_timeout_stops_the_gate_as_not_runnable(pack_root: Path, monkeypatch, capsys) -> None:
    from tools import ci_gate

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(ci_gate.subprocess, "run", run)

    assert ci_gate.main(["--root", str(pack_root)]) == 2
    assert len(calls) == 1
    error = capsys.readouterr().err
    assert "NOT_RUNNABLE" in error and "timed out" in error


def test_scan_baseline_binds_a_reactor_module_to_the_root_wrapper(tmp_path: Path) -> None:
    """The frozen baseline of a submodule names and hashes the wrapper in the build root."""
    import types

    from tools import run_pipeline
    from tools.project_inventory import build_inventory, runtime_identity, validate_execution_baseline_binding

    project, module, config = _reactor(tmp_path, MAVEN)
    (module / "src" / "test" / "java").mkdir(parents=True)
    declared = {
        "id": "api", "root": "services/api", "stack": {"language": "java", "build_tool": "maven"},
        "paths": {"source": ["src/main/java"], "tests": ["src/test/java"]},
        "test": {**config["test"], "framework": "junit5"}, "detected_from": ["services/api/pom.xml"],
    }
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": [declared],
    }), encoding="utf-8")
    inventory = build_inventory(project, module)
    wrapper = _host("mvnw", "mvnw.cmd")

    baseline = run_pipeline._scan_execution_baseline(
        project.resolve(), module.resolve(), declared, inventory,
        types.SimpleNamespace(profile="local-pilot-v1", target=None, docs=None),
    )

    assert baseline["wrapper_path"] == wrapper
    assert baseline["wrapper_identity"] == runtime_identity(project.resolve(), wrapper)
    validate_execution_baseline_binding(baseline, inventory, module.resolve())
