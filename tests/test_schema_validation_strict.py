"""PH: ``loads_json_strict`` / ``load_json_strict`` refuse with the module's own ``StrictJsonError`` — whatever the standard library raises."""
from __future__ import annotations

import re
import sys
from json import dumps as json_dumps
from pathlib import Path

import pytest

from tools.schema_validation import MAX_JSON_DEPTH, StrictJsonError, load_json_strict, loads_json_strict


def test_a_valid_value_is_loaded() -> None:
    assert loads_json_strict('{"a": [1, 2.5, null, "x"]}') == {"a": [1, 2.5, None, "x"]}
    assert loads_json_strict("1" * 4300) == int("1" * 4300)  # the longest integer the interpreter converts


@pytest.mark.parametrize("digits", [4301, 5000, 100_000])
def test_an_integer_longer_than_the_interpreter_limit_is_a_strict_json_error(digits: int) -> None:
    text = "1" * digits
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)
    with pytest.raises(StrictJsonError):
        loads_json_strict(f'{{"n": {text}}}')
    with pytest.raises(StrictJsonError):
        loads_json_strict(f"[{text}]")


def test_the_same_refusal_comes_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_text('{"n": ' + "9" * 5000 + "}", encoding="utf-8")
    with pytest.raises(StrictJsonError):
        load_json_strict(path)


def test_a_deep_nesting_is_a_strict_json_error_not_a_recursion_error() -> None:
    depth = sys.getrecursionlimit() * 3
    for text in ("[" * depth + "]" * depth, '{"a":' * depth + "1" + "}" * depth):
        with pytest.raises(StrictJsonError):
            loads_json_strict(text)


# --- PH3: the depth limit is the module's own, the same on every platform --------------------------------------------------------------
# The standard library's C scanner stops at a platform-dependent depth (about the recursion limit on Windows, far beyond it on Linux), so a refusal that
# waits for RecursionError is a refusal on one platform only.

def _nested(depth: int, kind: str = "array") -> str:
    if kind == "array":
        return "[" * depth + "]" * depth
    if kind == "object":
        return '{"a":' * depth + "1" + "}" * depth
    return "".join("[" if index % 2 else '{"a":' for index in range(depth)) + "1" + "".join("]" if index % 2 else "}" for index in reversed(range(depth)))


@pytest.mark.parametrize("kind", ["array", "object", "mixed"])
def test_a_nesting_of_the_limit_loads_and_one_level_more_is_refused(kind: str) -> None:
    assert MAX_JSON_DEPTH == 256
    loads_json_strict(_nested(MAX_JSON_DEPTH, kind))
    with pytest.raises(StrictJsonError) as caught:
        loads_json_strict(_nested(MAX_JSON_DEPTH + 1, kind))
    assert str(MAX_JSON_DEPTH) in str(caught.value) and "RecursionError" not in str(caught.value)


@pytest.mark.parametrize("kind", ["array", "object", "mixed"])
def test_a_huge_nesting_is_refused_by_the_limit_not_by_the_interpreter(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("the text reached json.loads")

    monkeypatch.setattr(json, "loads", never)
    for depth in (MAX_JSON_DEPTH + 1, 5000, 1_000_000):
        with pytest.raises(StrictJsonError) as caught:
            loads_json_strict(_nested(depth, kind))
        assert "RecursionError" not in str(caught.value)


def test_brackets_inside_strings_are_not_nesting() -> None:
    brackets = "[" * 1000 + "{" * 1000
    assert loads_json_strict(json_dumps({"a": brackets, "b": ["]" * 1000]})) == {"a": brackets, "b": ["]" * 1000]}
    assert loads_json_strict('{"a": "\\"[[[[[[[[", "b": "\\\\"}') == {"a": '"[[[[[[[[', "b": "\\"}  # an escaped quote does not end a string; an escaped backslash does not escape
    assert loads_json_strict('["\\\\", ' + _nested(MAX_JSON_DEPTH - 1) + "]")[0] == "\\"
    # a string with an escaped quote and brackets, then real nesting right after it
    with pytest.raises(StrictJsonError):
        loads_json_strict('["\\"[", ' + _nested(MAX_JSON_DEPTH) + "]")


def test_many_values_side_by_side_are_not_nesting() -> None:
    assert len(loads_json_strict("[" + ",".join(["[]"] * 20000) + "]")) == 20000
    assert len(loads_json_strict("[" + ",".join(['{"a":[1,{"b":[]}]}'] * 5000) + "]")) == 5000


def test_the_limit_applies_to_a_file_too(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text(_nested(MAX_JSON_DEPTH + 1), encoding="utf-8")
    with pytest.raises(StrictJsonError):
        load_json_strict(path)
    path.write_text(_nested(MAX_JSON_DEPTH), encoding="utf-8")
    load_json_strict(path)


def test_the_recursion_error_of_the_interpreter_stays_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    def too_deep(*args: object, **kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(json, "loads", too_deep)
    with pytest.raises(StrictJsonError):
        loads_json_strict("[[1]]")


def test_an_unterminated_string_after_deep_nesting_is_still_a_strict_json_error() -> None:
    for text in ('"' + "[" * 1000, "[" * 1000 + '"abc', '{"a": "' + "{" * 1000):
        with pytest.raises(StrictJsonError):
            loads_json_strict(text)


_UNTERMINATED = {  # ~200 KB each: a response cut off in the middle of a string
    "escaped-quotes": lambda: '"' + '\\"' * 100_000,
    "escaped-quotes-then-brackets": lambda: '"' + '\\"' * 100_000 + '[' * 300,
    "brackets-then-letters-and-escaped-quotes": lambda: '[' * 300 + '"' + 'a\\"' * 70_000,
    "escaped-backslashes": lambda: '{"a": "' + '\\\\' * 100_000,
    "lone-backslash-at-the-end": lambda: '"' + '\\"' * 100_000 + '\\',
    "escaped-quotes-between-strings": lambda: '"x" ' + '"\\" ' * 50_000,
}


@pytest.mark.parametrize("kind", sorted(_UNTERMINATED))
def test_an_unterminated_string_of_escaped_quotes_is_refused_in_linear_time(kind: str) -> None:
    """A response cut off in the middle of a string with escaped quotes: the depth check takes the rest of the text as the string in one pass.
    (With a mandatory closing quote every quote of the rest was tried as the start of a string, each walking to the end: 32 KB took 5.6 s, 800 KB about an hour.)"""
    import time

    text = _UNTERMINATED[kind]()

    start = time.perf_counter()
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)
    assert time.perf_counter() - start < 1.0


def _reference_exceeds(text: str, limit: int) -> bool:
    """The plain definition: a bracket outside a string takes a level; the text exceeds the limit if some prefix has more levels than it."""
    depth, index = 0, 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index += 1
            while index < len(text) and text[index] != '"':  # an unterminated string takes the rest of the text
                index += 2 if text[index] == "\\" else 1
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}":
            depth -= 1
        index += 1
    return False


def test_the_fast_check_agrees_with_the_plain_definition_on_random_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    from tools import schema_validation

    pieces = ["[", "]", "{", "}", '"a"', '"["', '"]"', '"{"', '"\\""', '"\\\\"', '"\\\\["', ",", "1", ":", '"', "\\"]  # a lone quote opens a string that may stay unterminated; a lone backslash escapes what follows it inside one
    randomizer = random.Random(20261104)
    refused = accepted = 0
    for limit in (1, 2, 3, 5, 8):
        monkeypatch.setattr(schema_validation, "MAX_JSON_DEPTH", limit)
        for _ in range(1500):
            text = "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, 40)))
            expected = _reference_exceeds(text, limit)
            try:
                schema_validation._check_json_depth(text)
                actual = False
            except StrictJsonError:
                actual = True
            assert actual == expected, (limit, text)
            refused += expected
            accepted += not expected
    assert refused > 500 and accepted > 500, (refused, accepted)  # both outcomes are exercised


def test_the_documents_of_the_package_are_far_below_the_limit() -> None:
    """The deepest JSON of the package (the schemas, the examples, the fixtures) is 14 levels; the limit leaves them a wide margin."""
    root = Path(__file__).resolve().parents[1]
    deepest = 0
    for path in list((root / "schemas").glob("*.json")) + list((root / "docs").rglob("*.json")) + list((root / "tests" / "fixtures").rglob("*.json")):
        depth = peak = 0
        text = path.read_text(encoding="utf-8")
        for token in re.findall(r'"(?:[^"\\]|\\.)*"|[\[{\]}]', text):
            if token in "[{":
                depth += 1
                peak = max(peak, depth)
            elif token in "]}":
                depth -= 1
        deepest = max(deepest, peak)
        loads_json_strict(text)
    assert 5 <= deepest <= MAX_JSON_DEPTH // 8, deepest


@pytest.mark.parametrize("text", ["", "{", "[1,]", "NaN", "Infinity", '{"a": 1, "a": 2}', "﻿{}", "1 2"])
def test_the_refusals_it_always_gave_are_still_strict_json_errors(text: str) -> None:
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)
