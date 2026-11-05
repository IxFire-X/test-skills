"""V5 (after the review of 4ea4d17): the patterns and loops that PQ4 added read adversarial texts in linear time, and say what the one-pattern forms said.

Three kinds of test:

* growth — the production call on a text of 10 KB, 100 KB and 1 MB (blank runs, a fragment repeated without its end, many initialisers and many assertions, test
  methods opened again and again); in a child interpreter that is killed after ``HARD_LIMIT`` seconds.  A linear scan takes ~2 s for 1 MB here; the budget is six times
  that; a scan of n squared takes a minute or more.
* equivalence — the hand-written readers (``_is_call``, ``_py_equality``, ``_py_not_null``, ``_py_failure``) against the patterns they replaced, on random lines;
* the window — how far back a variable is followed from its initialiser to the assertion.

Run: python -m pytest tests/test_v5_pq4_linear.py -q -n 4
"""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from tools import autotest_hints as hints_module
from tools.autotest_hints import autotest_hints

ROOT = Path(__file__).resolve().parents[1]
HARD_LIMIT = 60.0
SIZES = (10_000, 100_000, 1_000_000)
BUDGET_1MB = 12.0
BUDGET_100KB = 3.0

JAVA_HEAD, JAVA_TAIL = "@Test void a() {\n", "\n}\n"
PY_HEAD = "def test_a():\n    "

# name -> (language, text of about n characters)
CASES: dict[str, tuple[str, Callable[[int], str]]] = {
    "java-assertEquals-opened-and-cut-at-each-semicolon": ("java", lambda n: JAVA_HEAD + "assertEquals(a;" * (n // 15) + JAVA_TAIL),
    "java-isEqualTo-opened-and-cut-at-each-semicolon": ("java", lambda n: JAVA_HEAD + "assertThat(x).isEqualTo(a;" * (n // 26) + JAVA_TAIL),
    "java-call-followed-by-many-bracket-pairs": ("java", lambda n: JAVA_HEAD + "assertEquals(f" + "(a)" * (n // 6) + "x, f" + "(a)" * (n // 6) + "x);" + JAVA_TAIL),
    "java-many-initialisers-and-assertions-of-an-expected-variable": ("java", lambda n: JAVA_HEAD + "int expected = service.total();\n" + "int a1 = 1;\n" * (n // 24) + "assertEquals(expected, service.total());\n" * (n // 80) + JAVA_TAIL),
    "java-test-methods-opened-again-and-again-and-closed": ("java", lambda n: JAVA_HEAD * (n // 17) + "}" * (n // 17)),
    "java-nested-methods-with-assertions": ("java", lambda n: (JAVA_HEAD + "assertEquals(f(x), f(x));\n") * (n // 43) + "}" * (n // 43)),
    "python-assert-and-a-run-of-blanks": ("python", lambda n: PY_HEAD + "assert x" + " " * n + "y\n"),
    "python-assert-equal-and-two-runs-of-blanks": ("python", lambda n: PY_HEAD + "assert x" + " " * n + "== y" + " " * n + "\n"),
    "python-dotted-call": ("python", lambda n: PY_HEAD + "assert " + "a." * (n // 2) + "b == c\n"),
    "python-call-followed-by-many-bracket-pairs": ("python", lambda n: PY_HEAD + "assert f" + "(a)" * (n // 6) + "x == f" + "(a)" * (n // 6) + "x\n"),
    "python-many-expected-variables-and-assertions": ("python", lambda n: PY_HEAD + "expected = f(x)\n    " * (n // 20) + "assert f(x) == expected\n    " * (n // 20) + "\n"),
    "python-status-code-and-a-run-of-blanks": ("python", lambda n: PY_HEAD + "client.post('/a')\n    assert r.status_code" + " " * n + "x\n"),
    "python-status-code-equal-and-a-run-of-blanks": ("python", lambda n: PY_HEAD + "client.post('/a')\n    assert r.status_code ==" + " " * n + "x\n"),
    "python-assert-called-once-with-repeated": ("python", lambda n: PY_HEAD + "client.post('/a')\n    " + "m.assert_called_once_with(" * (n // 26) + "\n"),
}


def _program(name: str) -> str:
    return (f"import sys, time\nsys.path.insert(0, {str(ROOT)!r})\n"
            "from tests.test_v5_pq4_linear import CASES, SIZES\nfrom tools.autotest_hints import autotest_hints\n"
            f"language, make = CASES[{name!r}]\n"
            "for size in SIZES:\n"
            "    text = make(size)\n"
            "    started = time.perf_counter()\n"
            "    autotest_hints({'artifacts': {'generated_files': [{'file_id': 'F', 'language': language, 'content': text}]}})\n"
            "    print(size, time.perf_counter() - started, flush=True)\n")


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_scan_grows_linearly_with_the_text(name: str) -> None:
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.environ.get("PATH", "")}
    try:
        done = subprocess.run([sys.executable, "-c", _program(name)], capture_output=True, text=True, timeout=HARD_LIMIT, env=environment, cwd=str(ROOT))
    except subprocess.TimeoutExpired as expired:
        output = (expired.stdout or b"").decode() if isinstance(expired.stdout, bytes) else (expired.stdout or "")
        pytest.fail(f"{name}: not finished in {HARD_LIMIT:.0f} s (the lines so far: {output.strip()!r}): superlinear")
    assert done.returncode == 0, done.stderr[-800:]
    seconds = {int(size): float(took) for size, took in (line.split() for line in done.stdout.strip().splitlines())}
    assert seconds[1_000_000] < BUDGET_1MB, f"{name}: {seconds[1_000_000]:.1f} s for 1 MB ({seconds[100_000]:.2f} s for 100 KB, {seconds[10_000]:.3f} s for 10 KB): superlinear"
    assert seconds[100_000] < BUDGET_100KB, f"{name}: {seconds[100_000]:.1f} s for 100 KB: superlinear"


# --- the readers against the patterns they replaced --------------------------------------------------------------------------------------------------

OLD_JAVA_CALL = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\([^()]*\))*\(.*\)", re.DOTALL)
OLD_PY_CALL = re.compile(r"[A-Za-z_][\w.]*(?:\([^()]*\)|\.[A-Za-z_]\w*)*\(.*\)")
OLD_PY_EQUALITY = re.compile(r"^\s*assert\s+(.+?)\s*==\s*(.+?)\s*(?:,.*)?$|^\s*self\.assertEqual\(\s*(.+?)\s*,\s*(.+?)\s*(?:,\s*[\"'].*)?\)\s*$")
OLD_PY_NOT_NULL = re.compile(r"^\s*assert\s+.+\s+is\s+not\s+None\s*(?:,.*)?$")
OLD_PY_FAILURE = re.compile(
    r"\bpytest\.raises\(|\bassertRaises\(|\bstatus_code\s*(?:==|>=|>|in)\s*\(?\s*[45]\d\d\b|\bstatus_code\s*\)?\s*(?:==|in)\s*(?:HTTPStatus|status)\.\w*(?:BAD_REQUEST|UNAUTHORIZED|FORBIDDEN|NOT_FOUND|CONFLICT|UNPROCESSABLE)"
    r"|\bassert_called_once_with\(.*\b(?:error|exception)\b")


def _lines(seed: int, count: int, pieces: list[str], longest: int):
    randomizer = random.Random(seed)
    for _ in range(count):
        yield "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(1, longest)))


def test_is_call_says_what_the_one_pattern_said_for_java_and_python() -> None:
    pieces = ["a", "b1", "$", "_", ".", ".", "(", "(", ")", ")", "()", "(a)", "f(", "x.y", "1", ",", "->", "\"\"", "new", "[", "]"]
    java_true = python_true = 0
    for text in _lines(20261301, 60000, pieces, 9):
        expected = OLD_JAVA_CALL.fullmatch(text) is not None
        assert hints_module._is_call(text) == expected, repr(text)
        java_true += expected
        expected = OLD_PY_CALL.fullmatch(text) is not None
        assert hints_module._is_call(text, hints_module._PY_CALL_HEAD) == expected, repr(text)
        python_true += expected
    assert java_true > 1000 and python_true > 1000, (java_true, python_true)


def test_py_not_null_says_what_the_one_pattern_said() -> None:
    pieces = ["assert", " ", "  ", "\t", "x", "y", "is", "not", "None", "Nonex", ",", "(", ")", "is not None", "  is  not  None  ", "# c"]
    found = 0
    for number, text in enumerate(_lines(20261302, 80000, pieces, 10)):
        text = ("assert " if number % 2 else "") + text
        expected = OLD_PY_NOT_NULL.match(text) is not None
        assert hints_module._py_not_null(text) == expected, repr(text)
        found += expected
    assert found > 1500, found


def test_py_failure_says_what_the_one_pattern_said() -> None:
    pieces = ["status_code", " ", "  ", "==", ">=", ">", "in", "(", ")", "404", "500", "200", "4xx", "HTTPStatus.", "status.", "BAD_REQUEST", "NOT_FOUND", "OK", "pytest.raises(", "assertRaises(",
              "assert_called_once_with(", "error", "exception", "errors", "x", ",", "r."]
    found = 0
    for text in _lines(20261303, 80000, pieces, 9):
        expected = OLD_PY_FAILURE.search(text) is not None
        assert hints_module._py_failure(text) == expected, repr(text)
        found += expected
    assert found > 1500, found


def test_py_equality_says_what_the_pattern_said_where_the_line_has_no_brackets_or_strings() -> None:
    def old_sides(line: str):
        found = OLD_PY_EQUALITY.match(line)
        if not found:
            return None
        left, right = (found.group(1), found.group(2)) if found.group(1) is not None else (found.group(3), found.group(4))
        return re.sub(r"\s+", "", left), re.sub(r"\s+", "", right)

    def new_sides(line: str):
        found = hints_module._py_equality(line)
        return None if found is None else (re.sub(r"\s+", "", found[0]), re.sub(r"\s+", "", found[1]))

    compared = 0
    for text in _lines(20261304, 60000, ["assert", " ", "  ", "x", "f", "y.z", "==", ",", "a", "b"], 9):
        if text.lstrip().startswith("assert ") and not re.search(r"===|==\s*,|,\s*,|==\s*$|^\s*assert\s+==", text):
            assert new_sides(text) == old_sides(text), repr(text)
            compared += old_sides(text) is not None
    for text in _lines(20261305, 40000, ["a", "b", "f", " ", ",", ", ", "x.y"], 7):
        line = "self.assertEqual(" + text + ")"
        if not re.search(r",\s*,|\(\s*,|,\s*\)$", line):
            assert new_sides(line) == old_sides(line), repr(line)
            compared += old_sides(line) is not None
    assert compared > 3000, compared


@pytest.mark.parametrize("line, sides", [
    ("assert f(a == b) == c, 'm'", ("f(a == b)", "c")),   # the first == outside the brackets (the pattern took the one inside)
    ("assert 'a == b' == y", ("'a == b'", "y")),
    ("assert f(x, y) == g(x, y), 'm, n'", ("f(x, y)", "g(x, y)")),
    ("assert a != b", None),
    ("assert a >= b", None),
    ("self.assertEqual(f(x, 1), g(x), 'msg')", ("f(x, 1)", "g(x)")),
    ("self.assertEqual(f(x), [1, 2])", ("f(x)", "[1, 2]")),
])
def test_py_equality_reads_the_brackets_and_the_strings(line: str, sides) -> None:
    found = hints_module._py_equality(line)
    assert (None if found is None else tuple(found)) == sides


# --- the window of «before and after» -------------------------------------------------------------------------------------------------------------------

def _hints(text: str, language: str):
    found = autotest_hints({"artifacts": {"generated_files": [{"file_id": "F", "language": language, "content": text}]}})
    return [hint["rule"] for hint in found if hint["code"] == "AT_SELF_COMPUTED"]


def _java_between(count: int, statement: str) -> str:
    return "@Test void a() {\n  int expected = service.total();\n" + ("  " + statement + "\n") * count + "  assertEquals(expected, service.total());\n}\n"


def _python_between(count: int, statement: str) -> str:
    return "def test_a():\n    expected = service.total()\n" + ("    " + statement + "\n") * count + "    assert service.total() == expected\n"


@pytest.mark.parametrize("language, build", [("java", _java_between), ("python", _python_between)])
def test_a_variable_is_followed_back_over_a_bounded_number_of_statements(language: str, build) -> None:
    quiet = "int a1 = 1;" if language == "java" else "a1 = 1"
    assert _hints(build(3, quiet), language) == ["same-call"]  # nothing is done in between: the expected value is the call itself
    assert _hints(build(200, quiet), language) == ["same-call"]  # the window is 200 statements
    assert _hints(build(201, quiet), language) == []  # further back it is taken for «something happened»: no finding, never a false one


@pytest.mark.parametrize("language, build", [("java", _java_between), ("python", _python_between)])
def test_a_variable_is_followed_back_over_a_bounded_number_of_characters(language: str, build) -> None:
    short = "int a1 = 1;" if language == "java" else "a1 = 1"
    long_one = ("int a1 = " + "1 + " * 40 + "1;") if language == "java" else ("a1 = " + "1 + " * 40 + "1")  # ~170 characters
    assert _hints(build(10, long_one), language) == ["same-call"]
    assert _hints(build(40, long_one), language) == []  # ~7 000 characters back
    assert _hints(build(40, short), language) == ["same-call"]


@pytest.mark.parametrize("language, build", [("java", _java_between), ("python", _python_between)])
def test_a_call_in_between_still_makes_it_a_before_and_an_after(language: str, build) -> None:
    doing = "service.apply(3);" if language == "java" else "service.apply(3)"
    assert _hints(build(1, doing), language) == []


def test_a_test_method_inside_another_one_is_read_to_a_short_bound() -> None:
    """The second deliberate bound (with _UNCLOSED_BODY_LIMIT): a test method found inside the body of another one is read to _NESTED_BODY_LIMIT characters."""
    padding = "    int unused = compute(1, 2, 3, 4, 5, 6, 7, 8, 9);\n" * 20  # ~1 KB
    outer_check = "  assertEquals(1, f());\n"  # the outer one is not «not-null only», so only the inner one can give the hint
    near = "@Test void outer() {\n" + outer_check + "  Runnable r = new Runnable() {\n    @Test void inner() {\n      assertNotNull(x);\n    }\n  };\n" + padding + "}\n"
    far = "@Test void outer() {\n" + outer_check + "  Runnable r = new Runnable() {\n    @Test void inner() {\n" + padding + "      assertNotNull(x);\n    }\n  };\n}\n"

    def rules(text: str) -> list[str]:
        found = autotest_hints({"artifacts": {"generated_files": [{"file_id": "F", "language": "java", "content": text}]}})
        return [hint["rule"] for hint in found if hint["code"] == "AT_ALWAYS_TRUE"]

    assert rules(near) == ["not-null-only"]  # the inner one is short: judged as before
    assert rules(far) == []  # the inner one is long: its body is cut before the assertion
