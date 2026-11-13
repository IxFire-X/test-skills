"""V0b/C19: the printed build command is measured before it is printed; too long, its tests are listed by class, and still too long is refused.

realworld-b: 74 ``--tests`` flags made 9011 characters; ``gradlew.bat`` goes through cmd.exe (8191) and the tests never started, while
materialize said ok.  Only the printed form is shortened: the closed request, its digest and the reports it is held against stay per method."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import exec_commands
from tools.execution_adapters import GRADLE, MAVEN, PYTEST, ExecutionRequest, command_for
from tools.run_pipeline import HostStop


PROJECT = Path("/p")
CLASS = "io.github.demo.requirements.RealApiRequirementsTest"
LONG = "tcB1_{:03d}_registrationReturnsUserWithTokenAndTheRestOfAVeryLongName"


def _java_artifacts(classes: dict[str, int], *, nested: str = "") -> dict:
    files, symbols = [], []
    for index, (fqn, count) in enumerate(classes.items(), 1):
        body = "".join(f"@Test void {LONG.format(n)}() {{}}\n" for n in range(count))
        inner = {"": "", "helper": "private static final class Step { int n; }\n", "tests": "@Nested class Inner { @Test void x() {} }\n"}[nested]
        files.append({"file_id": f"FILE-{index}", "path": fqn.replace(".", "/") + ".java",
                      "content": f"package x;\npublic class {fqn.rsplit('.', 1)[1]} {{\n{body}{inner}}}\n"})
        symbols += [{"file_id": f"FILE-{index}", "symbol_id": f"S-{index}-{n}",
                     "locator": {"kind": "java_class_method", "class_fqn": fqn, "method_name": LONG.format(n)}} for n in range(count)]
    return {"generated_files": files, "generated_symbols": symbols}


def _request(adapter: str, selectors: tuple[str, ...], executable: str) -> ExecutionRequest:
    reports, argv = command_for(adapter, executable, "default", selectors)
    return ExecutionRequest(adapter, executable, argv, "/p", selectors, 600, reports, ("PROJECT_NATIVE_ENV",), "default", ())


def _selectors(artifacts: dict) -> tuple[str, ...]:
    rows = [row["locator"] for row in artifacts["generated_symbols"]]
    return tuple(f"{loc['class_fqn']}#{loc['method_name']}" for loc in rows)


def _fit(request: ExecutionRequest, artifacts: dict) -> list[str]:
    challenge = exec_commands._challenge(request, "0123456789abcdef")
    working, command = exec_commands._fit_command(PROJECT, Path("/p"), ".", request, challenge, artifacts)
    assert all(len(form) <= exec_commands._LINE_LIMIT for form in exec_commands.printed_forms(PROJECT, working, command))
    return command


def test_74_methods_of_one_class_are_printed_as_the_class() -> None:
    artifacts = _java_artifacts({CLASS: 74})
    request = _request(GRADLE, _selectors(artifacts), "/p/gradlew")
    assert len(exec_commands.printed_forms(PROJECT, ".", exec_commands._human_command(PROJECT, Path("/p"), ".", request, {})[1])[0]) > exec_commands._LINE_LIMIT
    command = _fit(request, artifacts)
    assert [command[i + 1] for i, token in enumerate(command) if token == "--tests"] == [CLASS]


def test_two_classes_are_two_class_selectors_in_a_maven_command() -> None:
    other = "io.github.demo.requirements.OtherRequirementsTest"
    artifacts = _java_artifacts({CLASS: 45, other: 45})
    command = _fit(_request(MAVEN, _selectors(artifacts), "/p/mvnw"), artifacts)
    assert f"-Dtest={CLASS},{other}" in command


def test_a_short_command_keeps_its_methods() -> None:
    artifacts = _java_artifacts({CLASS: 3})
    request = _request(GRADLE, _selectors(artifacts), "/p/gradlew")
    assert _fit(request, artifacts) == exec_commands._human_command(PROJECT, Path("/p"), ".", request, {})[1]


def test_a_class_with_some_of_its_tests_unselected_is_not_shortened_and_is_refused() -> None:
    artifacts = _java_artifacts({CLASS: 74})
    request = _request(GRADLE, _selectors(artifacts)[:-1], "/p/gradlew")  # one generated test is not selected: the class would run it too
    with pytest.raises(HostStop) as refused:
        _fit(request, artifacts)
    assert refused.value.code == "COMMAND_NOT_PRINTABLE" and str(exec_commands._LINE_LIMIT) in str(refused.value) and "знак" in str(refused.value)
    assert refused.value.fix and "materialize" in refused.value.fix


def test_a_class_with_a_helper_class_inside_is_still_listed_by_class() -> None:
    artifacts = _java_artifacts({CLASS: 74}, nested="helper")  # the shape of the realworld-b file: private static helper classes, no tests in them
    command = _fit(_request(GRADLE, _selectors(artifacts), "/p/gradlew"), artifacts)
    assert [command[i + 1] for i, token in enumerate(command) if token == "--tests"] == [CLASS]


def test_a_class_with_a_nested_class_that_holds_a_test_is_not_shortened() -> None:
    artifacts = _java_artifacts({CLASS: 74}, nested="tests")
    with pytest.raises(HostStop) as refused:
        _fit(_request(GRADLE, _selectors(artifacts), "/p/gradlew"), artifacts)
    assert refused.value.code == "COMMAND_NOT_PRINTABLE"


def test_the_methods_of_a_nested_class_are_never_generated_symbols(tmp_path: Path) -> None:
    from tools.run_tests import _java_class, _java_node

    text = _java_artifacts({CLASS: 1}, nested="tests")["generated_files"][0]["content"]
    parsed = _java_class(Path("RealApiRequirementsTest.java"), text)
    assert parsed is not None
    assert _java_node(parsed, {"kind": "java_class_method", "class_fqn": CLASS + "$Inner", "method_name": "x"}) is None
    assert _java_node(parsed, {"kind": "java_class_method", "class_fqn": CLASS, "method_name": "x"}) is None


def test_pytest_tests_of_one_file_are_printed_as_the_file() -> None:
    names = [f"test_a_long_scenario_name_number_{n:03d}_for_the_listing_of_pytest_node_ids" for n in range(110)]
    path = "tests/test_generated_requirements_of_the_service.py"
    artifacts = {"generated_files": [{"file_id": "F", "path": path, "content": "".join(f"def {name}():\n    pass\n" for name in names)}],
                 "generated_symbols": []}
    request = _request(PYTEST, tuple(f"{path}::{name}" for name in names), "/p/.venv/bin/python")
    assert len(exec_commands._human_command(PROJECT, Path("/p"), ".", request, {"value": "x"})[1]) > 100
    command = _fit(request, artifacts)
    assert command[-1] == path and command.count(path) == 1


def test_materialize_refuses_a_command_that_cannot_be_listed_shorter(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from tests.p4_flow import flow_args
    from tests.test_exec_prepares import _project
    from tools.pilot_state import read_attempt_receipt

    project, run_root, attempt_id = _project(tmp_path)
    monkeypatch.setattr(exec_commands, "_LINE_LIMIT", 10)
    code = exec_commands.cmd_materialize(flow_args(project, run_root))
    refused = json.loads(capsys.readouterr().out)
    assert code == 2 and refused["status"] == "error" and refused["reason"] == "COMMAND_NOT_PRINTABLE", refused
    with pytest.raises((KeyError, ValueError, OSError)):  # nothing was printed: no command receipt
        read_attempt_receipt(run_root, attempt_id, "materialization-command", "ARTIFACT_READ_BACK")
