"""The shared Java lexer: a text block is one literal, whatever it holds."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.java_text import mask_java_noncode

BLOCK = 'String s = """\n  { } // /* @Test\n  more\n  """;\n'


def test_a_text_block_is_masked_with_its_lines_kept() -> None:
    masked = mask_java_noncode(BLOCK)
    assert len(masked) == len(BLOCK) and masked.count("\n") == BLOCK.count("\n")
    assert not any(char in masked for char in "{}/@") and masked.rstrip().endswith(";")


def test_a_text_block_keeps_its_quotes_when_asked() -> None:
    assert mask_java_noncode(BLOCK, keep_quotes=True).count('"""') == 2


def test_an_escaped_delimiter_does_not_close_the_block() -> None:
    text = 'String s = """\n  a \\""" } b\n  """; int x = 1;\n'
    masked = mask_java_noncode(text)
    assert "}" not in masked and masked.endswith("int x = 1;\n")


def test_code_after_the_block_is_code_again() -> None:
    assert mask_java_noncode(BLOCK + "class A { }\n").endswith("class A { }\n")


@pytest.mark.parametrize("inside", ["}", "{ }", "// c", "/* c", "@Test"])
def test_the_class_and_the_test_after_a_text_block_are_found(inside: str) -> None:
    from tools.run_tests import _java_class, _java_node

    text = f'package p;\nclass T {{\n  String s = """\n  {inside}\n  """;\n  @Test void one() {{}}\n}}\n'
    parsed = _java_class(Path("T.java"), text)
    assert parsed is not None and _java_node(parsed, {"kind": "java_class_method", "class_fqn": "p.T", "method_name": "one"}) == "one"
