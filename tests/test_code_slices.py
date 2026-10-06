"""Р8: exact slices of generated test symbols (Java and Python).

Each symbol maps to exactly one slice, the slice is an exact run of whole lines of the
file, called helpers are found transitively, and brace-like characters inside strings,
character literals, text blocks and comments never move a boundary.  Annotations with
arguments, nested classes and lambdas stay inside their member.  Python decorators and
nested functions are handled by ``ast``.  A file that cannot be sliced raises
``SliceError`` (the plan then sends the whole file or blocks the part).
"""
from __future__ import annotations

import pytest

from tests.review_scaling_helpers import eval_data
from tests.live_step5 import review_state
from tools.code_slices import SliceError, slice_file

JAVA = '''package demo;

import java.util.List;

/** A class doc with a brace } and a quote " inside. */
@SomeConfig(name = "x{y}", values = {"a}", "b{"})
class TrickyTest {

    private static final String BRACES = "}}}{{{";   // a comment with {
    private static int counter = 0;
    private final List<String> seen = new java.util.ArrayList<>() {{ add("init}"); }};

    /* block comment { not a body } */
    private static char brace() { return '}'; }

    private String block() {
        return """
            text block with } and { and \\""" escaped
            """;
    }

    static class Nested {
        void inner() { int x = 1; }
    }

    private int helper(int value) {
        Runnable lambda = () -> { counter++; };
        lambda.run();
        return deeper(value) + "}".length();
    }

    private int deeper(int value) {
        return value + brace();
    }

    @Test
    @DisplayName("TC-1 {weird} name")
    void caseOne() {
        assertThat(helper(1)).as("ASSERT-1").isEqualTo(3);
    }

    @ParameterizedTest(name = "{0}")
    @ValueSource(strings = {"}", "{"})
    void caseTwo(String value) {
        assertThat(block()).as("ASSERT-2").contains(value);
        seen.add(value);
    }
}
'''

PYTHON = '''import functools

COUNTER = []


def decorate(function):
    @functools.wraps(function)
    def wrapper(*args):
        return function(*args)
    return wrapper


def helper(value):
    def nested(inner):
        return inner + 1
    COUNTER.append(value)
    return nested(value)


@decorate
def test_case_one():
    assert helper(1) == 2, "ASSERT-1"


class TestThings:
    @staticmethod
    def build():
        return {"}": "{"}

    def test_case_two(self):
        assert self.build() == {"}": "{"}, "ASSERT-2"
'''


def _java_symbols(*methods: str) -> list[dict]:
    return [{"file_id": "F", "symbol_id": f"S-{name}", "locator": {"kind": "java_class_method", "class_fqn": "demo.TrickyTest", "method_name": name}}
            for name in methods]


def _check_exact(slices, source: str) -> None:
    lines = source.split("\n")
    for symbol_id, member in slices.symbols.items():
        text = "\n".join(lines[member.start - 1:member.end])
        assert text in source and text.strip()
    case_lines = [line for member in slices.symbols.values() for line in range(member.start, member.end + 1)]
    assert len(case_lines) == len(set(case_lines)), "case slices never overlap"
    support = {line for start, end in slices.support_ranges() for line in range(start, end + 1)}
    assert support.isdisjoint(case_lines) and support | set(case_lines) == set(range(1, len(slices.lines) + 1))


def test_java_tricky_constructs_slice_exactly() -> None:
    file = {"file_id": "F", "path": "src/test/java/demo/TrickyTest.java", "language": "java", "content": JAVA}
    slices = slice_file(file, _java_symbols("caseOne", "caseTwo"))
    lines = JAVA.split("\n")
    one, two = slices.symbols["S-caseOne"], slices.symbols["S-caseTwo"]
    assert lines[one.start - 1].strip() == "@Test" and lines[one.end - 1].strip() == "}" and "void caseOne()" in lines[one.start + 1]
    assert lines[two.start - 1].strip().startswith("@ParameterizedTest") and "seen.add" in lines[two.end - 2]
    assert [helper.name for helper in slices.helpers["S-caseOne"]] == ["brace", "helper", "deeper"]
    assert [helper.name for helper in slices.helpers["S-caseTwo"]] == ["block"]
    names = {member.name: member for member in slices.members}
    assert names["inner"].owner == "TrickyTest.Nested" and names["Nested"].kind == "type"
    state = {row["name"]: row for row in slices.shared_state}
    assert "counter" in state and "helper" in state["counter"]["written_by"] and "BRACES" not in state
    _check_exact(slices, JAVA)


def test_python_decorators_and_nested_functions() -> None:
    file = {"file_id": "P", "path": "tests/test_things.py", "language": "python", "content": PYTHON}
    symbols = [{"file_id": "P", "symbol_id": "S1", "locator": {"kind": "python_module_function", "function_name": "test_case_one"}},
               {"file_id": "P", "symbol_id": "S2", "locator": {"kind": "python_class_method", "qualified_class_name": "TestThings", "method_name": "test_case_two"}}]
    slices = slice_file(file, symbols)
    lines = PYTHON.split("\n")
    assert lines[slices.symbols["S1"].start - 1] == "@decorate"
    assert [helper.name for helper in slices.helpers["S1"]] == ["decorate", "helper"]
    assert [helper.name for helper in slices.helpers["S2"]] == ["build"]
    assert {row["name"] for row in slices.shared_state} == {"COUNTER"}
    assert "helper" in next(row for row in slices.shared_state if row["name"] == "COUNTER")["written_by"]
    _check_exact(slices, PYTHON)


@pytest.mark.parametrize("broken", [JAVA.replace("void caseOne() {", "void caseOne() {{"), JAVA.replace('"}}}{{{"', '"}}}{{{'),
                                    JAVA.replace("/* block comment { not a body } */", "/* never closed { not a body }")])
def test_a_file_that_cannot_be_sliced_raises(broken: str) -> None:
    with pytest.raises(SliceError):
        slice_file({"file_id": "F", "path": "x.java", "language": "java", "content": broken}, _java_symbols("caseOne"))


def test_a_missing_or_duplicated_symbol_raises() -> None:
    file = {"file_id": "F", "path": "x.java", "language": "java", "content": JAVA}
    with pytest.raises(SliceError):
        slice_file(file, _java_symbols("noSuchMethod"))
    twice = _java_symbols("caseOne", "caseOne")
    twice[1]["symbol_id"] = "S-again"
    with pytest.raises(SliceError):
        slice_file(file, twice)


@pytest.mark.parametrize("name", ["step5", "petclinic"])
def test_real_generated_classes_slice_every_symbol_once(name: str) -> None:
    if name == "step5":
        artifacts = review_state("9340016c", "review-snapshot-r1")["payload"]["automation"]["artifacts"]
    else:
        artifacts = eval_data.petclinic_automation_snapshot()["automation"]["artifacts"]
    file = artifacts["generated_files"][0]
    symbols = [row for row in artifacts["generated_symbols"] if row["file_id"] == file["file_id"]]
    slices = slice_file(file, symbols)
    assert len(slices.symbols) == len(symbols)
    for symbol in symbols:
        member = slices.symbols[symbol["symbol_id"]]
        assert member.name == symbol["locator"]["method_name"]
        head = "\n".join(slices.lines[member.start - 1:member.start + 3])
        assert "@Test" in head and f"void {member.name}(" in head
        assert slices.helpers[symbol["symbol_id"]], "every case method reaches the application through helpers"
    _check_exact(slices, file["content"])
