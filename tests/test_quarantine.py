"""Wave 3 F: quarantine marks on single methods (JUnit 5 @Disabled, pytest xfail strict)."""
from __future__ import annotations

import ast

import pytest

from tests.live_step5 import outputs
from tools.quarantine import marked, quarantine, release
from tools.suite_manifest import method_digests

PYTEST_FILE = '''"""Generated tests."""
from __future__ import annotations


def helper():
    return 1


def test_first():
    assert helper() == 1


@pytest.mark.parametrize("value", [1, 2])
def test_second(value):
    assert value


class TestGroup:
    def test_inner(self):
        assert True
'''


@pytest.fixture(scope="module")
def java() -> dict:
    answer = next(value for label, value in outputs("9340016c").items() if label.startswith("tc-to-autotest."))
    [generated] = answer["generated_files"]
    symbols = [row for row in answer["generated_symbols"] if row["file_id"] == generated["file_id"]]
    return {"path": generated["path"], "content": generated["content"], "symbols": symbols}


def test_java_methods_get_one_disabled_line_and_nothing_else_changes(java: dict) -> None:
    first, second, *others = java["symbols"]
    locators = [first["locator"], second["locator"]]
    changed = quarantine(java["path"], java["content"], [(first["locator"], "ASSERTION_FAILED", "run 9340016c TC-B1-001"),
                                                          (second["locator"], "FLAKY", "run 9340016c TC-B1-002")])
    added = [line for line in changed.splitlines() if line not in java["content"].splitlines()]
    assert added == ['    @org.junit.jupiter.api.Disabled("test-skills quarantine: ASSERTION_FAILED run 9340016c TC-B1-001")',
                     '    @org.junit.jupiter.api.Disabled("test-skills quarantine: FLAKY run 9340016c TC-B1-002")']
    assert marked(java["path"], changed, locators) == [True, True]
    before, support_before = method_digests(java["path"], "F", java["content"], java["symbols"])
    after, support_after = method_digests(java["path"], "F", changed, java["symbols"])
    assert {key for key in before if before[key] != after[key]} == {first["symbol_id"], second["symbol_id"]} and support_before == support_after
    assert quarantine(java["path"], changed, [(first["locator"], "ASSERTION_FAILED", "again")]) == changed  # already marked
    assert release(java["path"], changed, locators) == java["content"]


def test_crlf_files_keep_their_line_endings(java: dict) -> None:
    crlf = java["content"].replace("\n", "\r\n")
    locator = java["symbols"][0]["locator"]
    changed = quarantine(java["path"], crlf, [(locator, "BEHAVIOR_CHANGED_WITHOUT_SPEC", "ref")])
    assert "\n" not in changed.replace("\r\n", "") and release(java["path"], changed, [locator]) == crlf


def test_unsafe_reason_text_cannot_break_the_string(java: dict) -> None:
    locator = java["symbols"][0]["locator"]
    changed = quarantine(java["path"], java["content"], [(locator, 'X") ; System.exit(1); //', "ref\\n")])
    [line] = [line for line in changed.splitlines() if "test-skills quarantine" in line]
    assert line.count('"') == 2 and "System.exit(1);" not in line


def test_pytest_functions_get_xfail_strict_and_an_import_when_missing() -> None:
    locators = [{"kind": "python_module_function", "function_name": "test_second"}, {"kind": "python_class_method", "qualified_class_name": "TestGroup", "method_name": "test_inner"}]
    changed = quarantine("tests/test_generated.py", PYTEST_FILE, [(locators[0], "ASSERTION_FAILED", "r1"), (locators[1], "FLAKY", "r2")])
    ast.parse(changed)
    lines = changed.splitlines()
    assert lines[2] == "import pytest" and lines[1] == "from __future__ import annotations"
    assert '@pytest.mark.xfail(strict=True, reason="test-skills quarantine: ASSERTION_FAILED r1")' in lines
    assert '    @pytest.mark.xfail(strict=True, reason="test-skills quarantine: FLAKY r2")' in lines
    # The mark goes above the existing decorators of the function.
    index = lines.index('@pytest.mark.xfail(strict=True, reason="test-skills quarantine: ASSERTION_FAILED r1")')
    assert lines[index + 1].startswith("@pytest.mark.parametrize")
    assert marked("tests/test_generated.py", changed, locators) == [True, True]
    released = release("tests/test_generated.py", changed, locators)
    assert released == PYTEST_FILE.replace('from __future__ import annotations\n', 'from __future__ import annotations\nimport pytest\n')
