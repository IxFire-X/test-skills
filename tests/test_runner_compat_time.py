"""FaT-RC: runner-compat stays linear on its Java inputs — petclinic-rest 523ec561 spun for 2 h (7657 s CPU) in it.

Two quadratic paths: the method-declaration pattern backtracked over every token of a long line of words, once per
generated method; the import check ``^\\s*import`` ran over a whole blank masked tail (an unclosed comment) from every line
start.  The limits are generous — the old code took several times longer on the same small inputs."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

LIMIT = 2.0


def test_a_long_line_of_words_does_not_stall_the_method_lookup() -> None:
    from tools.run_tests import _java_class, _java_node

    text = "package p;\nclass T {\n  void a() {\n" + "a " * 12000 + "\n  }\n  @Test void one() {}\n  @Test void two() {}\n  @Test void three() {}\n}\n"
    parsed = _java_class(Path("T.java"), text)
    started = time.perf_counter()
    found = [_java_node(parsed, {"kind": "java_class_method", "class_fqn": "p.T", "method_name": name}) for name in ("one", "two", "three")]
    assert time.perf_counter() - started < LIMIT
    assert found == ["one", "two", "three"]


def test_an_unclosed_comment_does_not_stall_the_api_check() -> None:
    from tools.run_tests import _java_api_level_diagnostics

    text = ("package p;\nimport java.util.List;\nclass T {\n  void a() { List<String> x = List.of(\"a\"); \"b\".repeat(2); }\n"
            "  /* never closed\n" + "\n" * 20000 + "}\n")
    started = time.perf_counter()
    rows = _java_api_level_diagnostics("/f", text, {"level": 8, "evidence": ["pom.xml"]})
    assert time.perf_counter() - started < LIMIT
    assert {row["message"].split(" — ", 1)[0] for row in rows} == {"List.of", "String.repeat"}


# The method-declaration pattern before FaT-RC: the new lookup must find exactly the same declarations.
_OLD = r"\b(?=\w)(?:public|protected|private)?\s*(?:static\s+)?[\w<>\[\]., ?]+\s+{name}\s*\([^;{{}}]*\)\s*(?:throws[^\{{]+)?\{{"


@pytest.mark.parametrize("body", [
    "  @Test void target() {}\n",
    "  @Test\n  public void target() throws Exception {}\n",
    "  public static <T> List<T> target(Map<String, List<T>> in, int[] n) {}\n",
    "  @Test void target() {}\n  void target(int x) {}\n",  # two declarations: ambiguous
    "  void other() { target(); }\n",  # a call, not a declaration
    "  void other() { this.target(1); }\n",
    "  void other() { return target(x) {} }\n",
    "  void other() { x = target() {}; }\n",
    "  Object o = new Object() ; void targetX() {}\n",
    "  String[] target() {}\n",
    "  void x() { a b c d e f target() {} }\n",
])
def test_the_lookup_finds_the_same_declarations_as_before(body: str) -> None:
    from tools.run_tests import _java_class, _java_node

    text = "package p;\nclass T {\n" + body + "}\n"
    parsed = _java_class(Path("T.java"), text)
    old = "target" if len(re.findall(_OLD.format(name=re.escape("target")), parsed[1])) == 1 else None
    assert _java_node(parsed, {"kind": "java_class_method", "class_fqn": "p.T", "method_name": "target"}) == old


def test_too_much_generated_code_is_refused_before_any_parsing(tmp_path: Path) -> None:
    """FaT-AU4 (audit F6): one runner-compat call reads at most RUNNER_INPUT_MAX_BYTES of generated code.  The canonical
    `{}` is invalid: the one diagnostic being RUNNER_INPUT_TOO_LARGE shows nothing was parsed before the limit."""
    from tools import run_tests

    big = "x" * (run_tests.RUNNER_INPUT_MAX_BYTES + 1)
    artifact = {"artifacts": {"generated_files": [{"path": "src/test/java/p/T.java", "content": big}]}}
    result = run_tests.validate_artifact_runner_compatibility(tmp_path, "java", {}, artifact, materialized=False)
    assert result.status == "NOT_RUNNABLE"
    [row] = result.diagnostics
    assert row["code"] == "RUNNER_INPUT_TOO_LARGE" and "fix:" in row["message"] and str(run_tests.RUNNER_INPUT_MAX_BYTES) in row["message"]
    (tmp_path / "src/test/java/p").mkdir(parents=True)
    (tmp_path / "src/test/java/p/T.java").write_text(big, encoding="utf-8")  # on disk, as exec checks it
    artifact["artifacts"]["generated_files"][0].pop("content")
    assert run_tests.validate_artifact_runner_compatibility(tmp_path, "java", {}, artifact).diagnostics[0]["code"] == "RUNNER_INPUT_TOO_LARGE"


def test_the_campaign_size_is_well_under_the_limit() -> None:
    from tools.run_tests import RUNNER_INPUT_MAX_BYTES

    assert RUNNER_INPUT_MAX_BYTES >= 5 * 184 * 1000  # the largest automation of the campaign: 184 KB of code
