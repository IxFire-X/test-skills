"""FaT-MD: a paired backtick span in a human field stays code in the Markdown projection; JSON and CSV bytes do not change."""
from __future__ import annotations

import copy

import pytest


@pytest.mark.parametrize(("given", "expected"), [
    ("Вызвать `Range.of` с null", "Вызвать `Range.of` с null"),
    ("x `a\\b` y", "x `a\\b` y"),  # a backslash inside code is literal
    ("одна ` кавычка", "одна \\` кавычка"),  # an unpaired backtick is still escaped
    ("`a|b` и *x*", "`a\\|b` и \\*x\\*"),  # a pipe stays escaped for a table cell, the rest as before
    ("a\\b", "a\\\\b"),
])
def test_escape_inline_keeps_paired_code_spans(given: str, expected: str) -> None:
    from tools.test_case_projections import escape_inline

    assert escape_inline(given) == expected


def test_a_case_with_code_in_its_text_shows_it_without_backslashes_and_other_bytes_stay() -> None:
    from tests.test_review_view import _example
    from tools.test_case_projections import render_markdown

    document = _example()
    changed = copy.deepcopy(document)
    changed["test_cases"][0]["steps"][0]["action"] = "Вызвать `Range.of` для `x`"
    preview = render_markdown(changed).payload.decode("utf-8")
    assert "`Range.of`" in preview and "\\`Range.of\\`" not in preview and "`x`" in preview
