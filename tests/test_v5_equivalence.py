"""V5: the linear scans of autotest_hints and run_tests (8.D6-6, 8.D6-7) give the answers the old ones gave.

The old modules are kept in tests/reference/ as they were before V5 (autotest_hints_before_v5.py, run_tests_before_v5.py).  Every text is read by both and the
answers are compared: on random texts built from the pieces that matter (the headers of test methods, the tails the patterns look for, brackets, semicolons,
comments, cut-off lines), on the examples of tests/test_quality_hints.py, and — when it is there — on the Java files of the live runs.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
from pathlib import Path
from typing import Any

import pytest

REFERENCE = Path(__file__).resolve().parent / "reference"


def _load(name: str) -> Any:
    import tools  # noqa: F401 - the old module is loaded as a member of the package, so that its relative imports resolve

    spec = importlib.util.spec_from_file_location(f"tools.{name}", REFERENCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


OLD_HINTS = _load("autotest_hints_before_v5")
OLD_RUN_TESTS = _load("run_tests_before_v5")

JAVA_PIECES = [
    "@Test", "@Test", "@ParameterizedTest", "@RepeatedTest(3)", "@DisplayName(\"t\")", " void t1() {", " void t2() throws Exception {", " void t3(int a, String b) {",
    " void t4(@Foo(\"x\") String s) {", "class A {", "}", "}", "{", ";", ";", "\n", "\n", " ", "  ", "(", ")", "()",
    "assertThat(x).isGreaterThanOrEqualTo(0)", "assertThat(x).isGreaterThanOrEqualTo(0L)", "assertTrue(a >= 0)", "assertTrue(a >= 0L)", "assertEquals(1, f())",
    "assertThat(a).isEqualTo(b)", "assertNotNull(x)", "assertThat(x).isNotNull()", "assertThat(x).as(\"ASSERT-1-1\").isNotNull()",
    "assertThatThrownBy(() -> f())", ".isInstanceOf(X.class)", ".hasMessage(\"m\")", "assertThrows(Exception.class, () -> f())", "assertThrows(IOException.class, g)",
    "catch (Exception e) {", "catch (IOException | RuntimeException e) {", "throw e", "try {", "// comment ; {", "/* block ( */", "\"str ; {\"",
    "contains(\"/a/\")", "contains(x, \"/b/\")", "startsWith(\"/c/\")", "endsWith(a, b, \"/d/\")", "containsString(\"/e/\")", "contains(\"x\")",
    "helper(a, 0)", "helper(a, 0L)", "int helper(int a, int b) {", "return a >= b", "isGreaterThanOrEqualTo(", "assertX(", "mockMvc.perform(get(\"/o\"))",
    ".andExpect(status().isOk())", "verify(r)", "throws Exception", " throws IOException ", "int x = 0", ",", "\"/z/\"", "x",
]

PY_PIECES = [
    "def test_a():", "    ", "        ", "\n", "try:", "except:", "except Exception:", "except (A, B):", "except (Exception, ValueError) as e:", "except ValueError as e:",
    "except  ", " as ", "e", "raise", "pass", "assert x >= 0", "assert True", "assert y is not None", "assert a == 1", "with pytest.raises(Exception):", "# c",
    "except Exception as err : raise", "x = 1", ":", "(", ")", "async def test_b():", "    except IOError:", "except ( A , B ) :", "except A.B:", "except A, B:",
]


OLD_CODES = ("AT_ANY_EXCEPTION", "AT_ALWAYS_TRUE", "AT_PARTIAL_MATCH")


def _java_hints(module: Any, text: str, language: str = "java") -> list[dict[str, Any]]:
    """The hints of the three codes that existed before V5 (the two codes of PQ4 are not what is compared here)."""
    hints = module.autotest_hints({"artifacts": {"generated_files": [{"file_id": "FILE-1", "language": language, "content": text}]}})
    return [hint for hint in hints if hint["code"] in OLD_CODES]


def _random_texts(seed: int, count: int, pieces: list[str], longest: int):
    randomizer = random.Random(seed)
    for _ in range(count):
        yield "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, longest)))


def test_the_java_hints_are_the_ones_found_before_on_random_texts() -> None:
    from tools import autotest_hints

    found = 0
    for text in _random_texts(20261201, 30000, JAVA_PIECES, 26):
        old = _java_hints(OLD_HINTS, text)
        assert _java_hints(autotest_hints, text) == old, repr(text)
        found += len(old)
    assert found > 8000, found  # not a comparison of two empty lists


def test_the_python_hints_are_the_ones_found_before_on_random_texts() -> None:
    from tools import autotest_hints

    found = 0
    for text in _random_texts(20261202, 20000, PY_PIECES, 16):
        old = _java_hints(OLD_HINTS, text, "python")
        assert _java_hints(autotest_hints, text, "python") == old, repr(text)
        found += len(old)
    assert found > 1500, found


def test_the_java_hints_of_the_examples_of_the_quality_hints_are_the_ones_found_before() -> None:
    from tools import autotest_hints
    from tests import test_quality_hints

    samples = [value for name, value in vars(test_quality_hints).items() if isinstance(value, str) and ("@Test" in value or "def test_" in value)]
    assert samples
    for text in samples:
        for language in ("java", "python"):
            assert _java_hints(autotest_hints, text, language) == _java_hints(OLD_HINTS, text, language)


LIVE = Path(os.environ.get("TRASSA_LIVE_DIR", r"D:\AI-Projects\live"))


@pytest.mark.skipif(not LIVE.is_dir(), reason="no live/ directory on this machine")
def test_the_java_files_of_the_live_runs_give_the_hints_they_gave() -> None:
    """The generated Java of every run in live/ (``generated_files[].content``), and the project tests next to them, read by the old and the new rules."""
    import json

    from tools import autotest_hints

    seen: set[str] = set()
    compared = 0
    for path in LIVE.rglob("*.json"):
        try:
            if not 500 < path.stat().st_size < 6_000_000:
                continue
            raw = path.read_bytes()
            if b"generated_files" not in raw:
                continue
            document = json.loads(raw)
        except (OSError, ValueError):
            continue
        artifact = document.get("artifacts") if isinstance(document, dict) and isinstance(document.get("artifacts"), dict) else document
        for item in (artifact.get("generated_files") or []) if isinstance(artifact, dict) else []:
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, str) and content not in seen:
                seen.add(content)
                language = str(item.get("language", "java"))
                assert _java_hints(autotest_hints, content, language) == _java_hints(OLD_HINTS, content, language), (str(path), item.get("file_id"))
                compared += 1
    if not compared:
        pytest.skip("live/ holds no generated files")


def test_an_unclosed_test_method_is_read_to_a_bound_and_a_closed_one_whole() -> None:
    """The one deliberate bound: a method that is never closed has the rest of the file for a body, so it is read up to _UNCLOSED_BODY_LIMIT characters."""
    from tools import autotest_hints

    padding = "        int unused = compute(1, 2, 3, 4, 5, 6, 7, 8, 9);\n" * 120  # ~6 KB
    closed = "@Test\nvoid t() {\n" + padding + "    assertNotNull(x);\n}\n"
    unclosed = "@Test\nvoid t() {\n" + padding + "    assertNotNull(x);\n"
    assert [hint["rule"] for hint in _java_hints(autotest_hints, closed)] == ["not-null-only"] == [hint["rule"] for hint in _java_hints(OLD_HINTS, closed)]
    assert _java_hints(OLD_HINTS, unclosed) and not _java_hints(autotest_hints, unclosed)  # the old read it whole; the bound ends the read before the assertion
    short = "@Test\nvoid t() {\n    assertNotNull(x);\n"  # an unclosed one that is short is read whole
    assert _java_hints(autotest_hints, short) == _java_hints(OLD_HINTS, short)


# --- run_tests ---------------------------------------------------------------------------------------------------------------------------------------

RUN_PIECES = ["class A {", "class B {", "static class N {", "interface I {", "enum E {", "record R(int a) {", "}", "}", "{", "\n", " ", ";", "void foo() {", "foo(",
              "foo(1)", "int foo(int a) throws X {", "String foo() {", "foo (", ")", "throws Y", "bar(", "x", "// c", "\"s\"", "public ", "static ", "int ", "void "]


def test_the_class_body_and_the_method_of_a_locator_are_the_ones_found_before() -> None:
    from tools import run_tests

    locator = {"kind": "java_class_method", "class_fqn": "A", "method_name": "foo"}
    answered = 0
    for text in _random_texts(20261203, 30000, RUN_PIECES, 22):
        path = Path("A.java")
        try:
            old_class = OLD_RUN_TESTS._java_class(path, text)
        except Exception as error:  # noqa: BLE001 - the same exception is expected of the new one
            with pytest.raises(type(error)):
                run_tests._java_class(path, text)
            continue
        new_class = run_tests._java_class(path, text)
        assert new_class == old_class, repr(text)
        if old_class is None:
            continue
        assert run_tests._java_node(new_class, locator) == OLD_RUN_TESTS._java_node(old_class, locator), repr(text)
        answered += 1
    assert answered > 500, answered


def test_the_hand_made_class_bodies_are_the_ones_found_before() -> None:
    from tools import run_tests

    texts = [
        "package p;\nclass A {\n  void foo() { }\n  static class B { void foo() { } }\n  interface I { void foo(); }\n}\n",
        "class A {\n  int foo(int a) throws E { return a; }\n  int bar() { return foo(1); }\n}\n",
        "class A {\n  void foo() {}\n  void foo(int a) {}\n}\n",
        "class A {\n  class B {\n    class C { }\n  }\n  void foo() {}\n}\n",
        "class A {\n  class B {\n  void foo() {}\n}\n",
    ]
    locator = {"kind": "java_class_method", "class_fqn": "A", "method_name": "foo"}
    for text in texts:
        old = OLD_RUN_TESTS._java_class(Path("A.java"), text)
        new = run_tests._java_class(Path("A.java"), text)
        assert new == old
        if old:
            assert run_tests._java_node(new, locator) == OLD_RUN_TESTS._java_node(old, locator)
