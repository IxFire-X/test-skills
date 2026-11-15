"""fix1: the bad inputs of the automation give a refusal with a hint, not INTERNAL — names of the devices of Windows, a bare @CsvSource, a file the parser cannot take, a literal as a method name."""
from __future__ import annotations

import ast
import hashlib

import pytest

from tests.test_generated_delta import _inputs
from tests.test_project_native_pytest import automated_document, automation
from tools.automation_validation import _java_name, validate_automation_artifact


@pytest.mark.parametrize("segment", ["con", "CON.py", "nul", "Aux.txt", "prn", "com1", "COM9.py", "lpt3"])
def test_a_path_with_the_name_of_a_windows_device_is_refused_and_names_the_segment(tmp_path, segment: str) -> None:
    inputs = _inputs(tmp_path, mutate=lambda artifact: artifact["artifacts"]["generated_files"][0].__setitem__("path", f"tests/{segment}"))
    _baseline, artifact, _review, document, _run_root, _attempt_id = inputs
    rows = [row for row in validate_automation_artifact(artifact, document) if row["code"] == "AUTOMATION_PORTABLE_PATH"]
    assert len(rows) == 1 and f"сегмент {segment} — имя устройства Windows" in rows[0]["message"]


@pytest.mark.parametrize("segment", ["console.py", "conn", "com0", "com10", "lpt", "auxiliary.py"])
def test_a_name_that_only_starts_like_a_device_is_not_refused(tmp_path, segment: str) -> None:
    inputs = _inputs(tmp_path, mutate=lambda artifact: artifact["artifacts"]["generated_files"][0].__setitem__("path", f"tests/{segment}"))
    _baseline, artifact, _review, document, _run_root, _attempt_id = inputs
    assert not [row for row in validate_automation_artifact(artifact, document) if row["code"] == "AUTOMATION_PORTABLE_PATH"]


@pytest.mark.parametrize("name", ["true", "false", "null"])
def test_the_literals_of_java_are_no_method_names(name: str) -> None:
    assert not _java_name(name) and _java_name("truest")


def test_a_bare_csv_source_is_a_group_without_readable_rows_not_an_error() -> None:
    from tools.run_tests import _param_group_rows

    text = 'class T {\n  @ParameterizedTest(name = "[{index}] {0}")\n  @CsvSource\n  void m(String id) {}\n}\n'
    assert _param_group_rows(text, "m") is None


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_a_python_file_the_parser_cannot_take_is_no_test_file(monkeypatch, error) -> None:
    from tools import exec_commands

    def broken(*_args, **_kwargs):
        raise error("x")

    with monkeypatch.context() as patched:  # undone before pytest reports: it parses sources too
        patched.setattr(ast, "parse", broken)
        found = exec_commands._python_tests("def test_a(): pass")
    assert found == set()


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_a_python_test_file_the_parser_cannot_take_is_refused_by_the_review_of_the_runner(tmp_path, monkeypatch, error) -> None:
    from tools import run_tests
    from tools.run_tests import validate_artifact_runner_compatibility

    document = automated_document()
    artifact = automation(document)

    def broken(*_args, **_kwargs):
        raise error("x")

    with monkeypatch.context() as patched:  # undone before pytest reports: it parses sources too
        patched.setattr(run_tests.ast, "parse", broken)
        result = validate_artifact_runner_compatibility(tmp_path, "python", document, artifact, materialized=False)
    assert result.status == "NOT_RUNNABLE" and any(row["code"] == "RUNNER_LOCATOR" for row in result.diagnostics)
