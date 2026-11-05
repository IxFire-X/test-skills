"""V5 (8.D6-6, 8.D6-7; probes of bug hunt 8, direction 6): the scans of generated test files (and loops built like them) that read model, agent or project text and are SUPERLINEAR.

Each probe runs the production call in a child interpreter on an adversarial text of 100 KB (a few cases: 20-60 KB, the text is named in the test), and
asserts that it finishes inside a budget.  A linear pass over 100 KB takes 5-100 ms here; the budgets are 20-100 times that, so a failure is not a slow machine
but a growth of n squared (the ratio 10 KB -> 100 KB is 50-100 times in every failing probe) or worse.  A child that outlives ``HARD_LIMIT`` is killed, so a
failing probe costs at most that.  Tests named ``control`` run the neighbouring linear case and are expected to pass.

Run (in the copy of the package): python -m pytest tests/test_v5_scan_time.py -q -n 2
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARD_LIMIT = 8.0  # seconds of wall time before the child is killed


def _time(body: str, budget: float) -> None:
    """Run *body* (statements, inside a function with ``ROOT`` importable) in a child, and fail if it takes more than *budget* seconds of its own clock."""
    program = ("import sys, time\nsys.path.insert(0, %r)\n" % str(ROOT)) + textwrap.dedent(body) + \
        "\nstarted = time.perf_counter()\nrun()\nprint(time.perf_counter() - started)\n"
    try:
        done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=HARD_LIMIT,
                              env={"PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": __import__("os").environ.get("PATH", "")})
    except subprocess.TimeoutExpired:
        pytest.fail(f"did not finish in {HARD_LIMIT:.0f} s (budget {budget} s): superlinear")
    assert done.returncode == 0, done.stderr[-600:]
    elapsed = float(done.stdout.strip().splitlines()[-1])
    assert elapsed < budget, f"{elapsed:.2f} s for the adversarial text (budget {budget} s): superlinear"



# ----------------------------------------------------------------------------------------------------------------------------------------------------
# agent text: the generated test files read by autotest_hints (Java, Python)
# ----------------------------------------------------------------------------------------------------------------------------------------------------

_HINTS = """
        from tools.autotest_hints import autotest_hints
        def hints(language, content):
            autotest_hints({"artifacts": {"generated_files": [{"file_id": "FILE-1", "language": language, "content": content}]}})
"""  # indented like the bodies it is concatenated with (one margin for textwrap.dedent)


def test_hints_java_test_annotation_repeated_without_a_closing_brace_is_not_cubic() -> None:
    """_JAVA_TEST = ``@Test\\b[^{;]*?\\b\\w+\\s*\\([^)]*\\)\\s*...\\{``: the lazy ``[^{;]*?`` is tried at every offset and each try runs ``[^)]*`` to the end
    when there is no ``)``.  A cut-off answer that repeats ``@Test a(`` 12 500 times (100 KB): 10 KB takes 2 s, 100 KB does not finish."""
    _time(_HINTS + """
        TEXT = "@Test a(" * 12_500
        def run(): hints("java", TEXT)
    """, 2.0)


def test_hints_java_test_annotation_repeated_without_anything_else_is_not_quadratic() -> None:
    _time(_HINTS + """
        TEXT = "@Test " * 16_000
        def run(): hints("java", TEXT)
    """, 2.0)


def test_hints_java_assert_without_a_semicolon_repeated_is_not_quadratic() -> None:
    """_JAVA_GE_ZERO = ``\\bassert\\w*\\s*\\([^;]*?>=\\s*0\\s*\\)``: ``[^;]*?`` runs to the end of the text for every ``assertX(`` when there is no ``;``."""
    _time(_HINTS + """
        TEXT = "assertX(" * 12_500
        def run(): hints("java", TEXT)
    """, 2.0)


def test_hints_java_contains_call_repeated_without_a_semicolon_is_not_quadratic() -> None:
    """_JAVA_PATH = ``\\b(?:contains\\w*|startsWith|endsWith)\\s*\\((?:[^;]*?,\\s*)?\\"(/[^\\"'\\s]*/)\\"\\s*\\)``: the optional lazy ``[^;]*?,`` scans to the end for every ``contains(``."""
    _time(_HINTS + """
        TEXT = "contains(" * 11_000
        def run(): hints("java", TEXT)
    """, 2.0)


def test_hints_java_unclosed_test_methods_repeated_are_not_quadratic() -> None:
    """Every ``@Test void a() {`` without its closing brace makes ``_block`` walk (in Python, character by character) to the end of the file: 6 000
    methods of a truncated file = 6 000 walks of 100 KB."""
    _time(_HINTS + """
        TEXT = "@Test void a() {\\n" * 6_000
        def run(): hints("java", TEXT)
    """, 2.0)


def test_hints_python_except_followed_by_20k_blanks_is_not_cubic() -> None:
    """_PY_EXCEPT = ``^(\\s*)except\\b\\s*\\(?\\s*([\\w.,\\s]*?)\\s*\\)?\\s*(?:as\\s+\\w+\\s*)?:(.*)$``: the lazy ``[\\w.,\\s]*?`` and the three ``\\s*`` around it all match
    a blank, so a blank run of n costs n^3 on a line that has no colon.  10 KB of blanks does not finish in 12 s."""
    _time(_HINTS + """
        TEXT = "try:\\n    pass\\nexcept " + " " * 20_000 + "x\\n"
        def run(): hints("python", TEXT)
    """, 2.0)


def test_control_hints_of_a_valid_300kb_java_and_python_file_are_linear() -> None:
    _time(_HINTS + """
        JAVA = "@Test\\nvoid t() {\\n  assertEquals(1, f());\\n  assertTrue(x >= 1);\\n}\\n" * 6_000
        PY = "def test_a():\\n    x = f()\\n    assert x == 1\\n    assert x >= 0\\n" * 6_000
        def run():
            hints("java", JAVA); hints("python", PY)
    """, 3.0)


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# agent text: generated Java read by run_tests (the class and the method of a locator)
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_java_class_with_many_nested_classes_is_not_quadratic() -> None:
    """run_tests._java_class: ``nested.search("".join(body), search_at)`` — the body is joined into a new string for every nested class found.  A VALID file
    with 7 500 empty nested classes (150 KB) takes 4 s, 15 000 (300 KB) 16 s."""
    _time("""
        from pathlib import Path
        from tools.run_tests import _java_class
        TEXT = "class A {\\n" + "  static class B { }\\n" * 7_500 + "}\\n"
        def run(): _java_class(Path("A.java"), TEXT)
    """, 2.0)


def test_java_node_with_a_method_called_25k_times_without_a_semicolon_is_not_quadratic() -> None:
    """_java_node: ``\\bfoo\\s*\\([^;{}]*\\)\\s*(?:throws[^\\{]+)?\\{`` — for each ``foo(`` the greedy ``[^;{}]*`` runs to the end of the body (no ``;``, ``{``, ``}``)."""
    _time("""
        from tools.run_tests import _java_node
        BODY = "x" + "foo(" * 25_000
        LOCATOR = {"kind": "java_class_method", "class_fqn": "p.A", "method_name": "foo"}
        def run(): _java_node(("p.A", BODY), LOCATOR)
    """, 2.0)


def test_control_java_class_of_a_valid_300kb_file_is_linear() -> None:
    _time("""
        from pathlib import Path
        from tools.run_tests import _java_class
        TEXT = "class A {\\n" + "  void m() { f(); }\\n" * 15_000 + "}\\n"
        def run(): _java_class(Path("A.java"), TEXT)
    """, 2.0)


