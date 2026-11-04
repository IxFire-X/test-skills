"""PH: ``loads_json_strict`` / ``load_json_strict`` refuse with the module's own ``StrictJsonError`` — whatever the standard library raises."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.schema_validation import StrictJsonError, load_json_strict, loads_json_strict


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


@pytest.mark.parametrize("text", ["", "{", "[1,]", "NaN", "Infinity", '{"a": 1, "a": 2}', "﻿{}", "1 2"])
def test_the_refusals_it_always_gave_are_still_strict_json_errors(text: str) -> None:
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)
