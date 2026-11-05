"""F9: the schema layer — NaN against a bound, a leading and trailing ``.*`` that a search does not need, the strict loader and the depth of a wrapped document."""
from __future__ import annotations

import math
import random
import re

import pytest

from tools import schema_engine
from tools.schema_engine import Draft202012Validator


@pytest.mark.parametrize("keyword,bound", [("minimum", 0), ("maximum", 0), ("exclusiveMinimum", 0), ("minimum", -1.5), ("maximum", 10)])
def test_nan_satisfies_no_bound_in_either_walk(keyword: str, bound: float) -> None:
    validator = Draft202012Validator({"type": "number", keyword: bound})
    assert not validator.is_valid(math.nan)
    assert [e.message for e in validator.iter_errors(math.nan)]  # the walk that reports errors says the same
    assert validator.is_valid(bound + 1 if keyword != "maximum" else bound - 1)


def test_a_number_that_is_a_bound_is_still_inside_it() -> None:
    assert Draft202012Validator({"minimum": 1}).is_valid(1) and not Draft202012Validator({"exclusiveMinimum": 1}).is_valid(1)
    assert Draft202012Validator({"maximum": 1}).is_valid(1.0) and not Draft202012Validator({"maximum": 1}).is_valid(1.0000001)
    assert Draft202012Validator({"minimum": 0}).is_valid(math.inf) and not Draft202012Validator({"maximum": 0}).is_valid(math.inf)
    assert not Draft202012Validator({"minimum": 0}).is_valid(-math.inf) and Draft202012Validator({"maximum": 0}).is_valid(-math.inf)


@pytest.mark.parametrize("pattern,core", [
    (r".*\S.*", r"\S"), (r".*a", "a"), (r"a.*", "a"), (r".*", ""), (r"^.*$", r"^.*$"), (r"a\.*", r"a\.*"), (r"a\\.*", r"a\\"), (r".*?a", r".*?a"), (r".*+a", r".*+a"),
    (r".*{2}a", r".*{2}a"), (r"[.*]", r"[.*]"), (r"(?:.*)", r"(?:.*)"), (r"..*", r"."), (r"a.*|b.*", r"a.*|b"),
])
def test_the_search_core_of_a_pattern(pattern: str, core: str) -> None:
    assert schema_engine._search_core(pattern) == core


def test_a_pattern_with_dot_star_at_its_edges_says_what_it_said() -> None:
    """The verdict of ``.*X.*`` over the engine equals the verdict of the same pattern compiled as written (with the ECMA dot), for random strings."""
    randomizer = random.Random(20261117)
    patterns = [r".*\S.*", r".*a", r"a.*", r".*ab.*", r".*(?:a|b)c.*", r".*[ab]", r"x.*y", r".*\d?z.*".replace(r"\d?", "1?"), r"a\.*", r".*\\.*"]
    pieces = ["a", "b", "c", "x", "y", "z", "1", " ", "\t", "\n", "\r", " ", ".", "\\", "ab"]
    for pattern in patterns:
        compiled = re.compile(schema_engine._ecma_pattern(pattern))
        validator = Draft202012Validator({"pattern": pattern})
        for _ in range(3000):
            text = "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, 8)))
            assert validator.is_valid(text) == (compiled.search(text) is not None), (pattern, text)


def test_the_message_of_a_pattern_error_names_the_pattern_as_written() -> None:
    validator = Draft202012Validator({"pattern": r".*\S.*"})
    assert [e.message for e in validator.iter_errors("   ")] == ["'   ' does not match '.*\\\\S.*'"]


def test_the_canonical_non_empty_text_is_a_pattern_that_search_reads_in_linear_time() -> None:
    import json
    from pathlib import Path

    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "canonical-test-document.schema.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["nonempty"]["pattern"] == "\\S"


@pytest.mark.parametrize("token", ["1e999", "-1e999", "1E400", "[1e999]", '{"a": {"b": [0.5, 2e308]}}'])
def test_the_strict_loader_refuses_a_number_beyond_the_range_of_a_float(token: str) -> None:
    from tools.schema_validation import StrictJsonError, loads_json_strict

    with pytest.raises(StrictJsonError) as caught:
        loads_json_strict(token)
    assert "non-finite" in str(caught.value)


@pytest.mark.parametrize("token,expected", [("1.5", 1.5), ("-0.0", -0.0), ("1e308", 1e308), ("[1E2, 2.5e-3]", [100.0, 0.0025]), ("123456789012345678901234567890", 123456789012345678901234567890)])
def test_the_strict_loader_still_reads_the_numbers_it_read(token: str, expected: object) -> None:
    from tools.schema_validation import loads_json_strict

    assert loads_json_strict(token) == expected


@pytest.mark.parametrize("text", ['"\\ud800"', '["\\udc00"]', '{"\\ud83d": 1}', '"\\ud83dx"', '"\\ud83d\\ud83d"', '"\\ude00\\ud83d"', '{"a": ["ok", "\\udfff"]}'])
def test_the_strict_loader_refuses_a_lone_surrogate_in_a_string_or_a_key(text: str) -> None:
    from tools.schema_validation import StrictJsonError, loads_json_strict

    with pytest.raises(StrictJsonError):
        loads_json_strict(text)


def test_the_strict_loader_refuses_the_surrogate_character_itself() -> None:
    from tools.schema_validation import StrictJsonError, loads_json_strict

    with pytest.raises(StrictJsonError):
        loads_json_strict('"' + chr(0xD800) + '"')


@pytest.mark.parametrize("text,expected", [('"\\ud83d\\ude00"', "\U0001F600"), ('["\\ud83d\\ude00x", "\\\\ud800"]', ["\U0001F600x", "\\ud800"]), ('"\\u00e9"', "é"), ('{"k\\ud83d\\ude00": 1}', {"k\U0001F600": 1})])
def test_the_strict_loader_reads_a_valid_pair_and_an_escaped_backslash(text: str, expected: object) -> None:
    from tools.schema_validation import loads_json_strict

    assert loads_json_strict(text) == expected


def test_a_document_one_level_deeper_than_the_loader_allows_still_gets_its_diagnostics() -> None:
    """8.D6-12: the schema layer validates the instance it is given; it does not read it back from a key through the strict loader (which refuses > 256 levels)."""
    from pathlib import Path

    from tools.schema_validation import MAX_JSON_DEPTH, schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    schema = root / "schemas" / "pipeline.schema.json"
    document: object = {}
    for _ in range(MAX_JSON_DEPTH + 1):
        document = {"k": document}
    rows = schema_diagnostics(document, schema, root)
    assert isinstance(rows, list) and rows  # diagnostics, not StrictJsonError


# --- the limits of .skillsrc -------------------------------------------------------------------------------------------------------------------------

def _limits_project(folder, limits: str):
    (folder / ".skillsrc").write_text('{"project":{"name":"p","language":"python"},"limits":' + limits + "}", encoding="utf-8")
    return folder


@pytest.mark.parametrize("whole,as_float", [("2000", "2000.0"), ("2000", "2e3"), ("262144", "262144.0"), ("128000", "128000.0"), ("1024", "1.024e3")])
def test_a_limit_written_as_a_whole_float_behaves_as_the_same_integer(tmp_path, whole: str, as_float: str) -> None:
    from tools import run_pipeline, step_size

    first, second = tmp_path / "int", tmp_path / "float"
    first.mkdir()
    second.mkdir()
    template = '{"step_window_bytes":%s,"step_input_bytes":%s,"context_batch_bytes":%s,"step_output_warn_bytes":%s}'
    _limits_project(first, template % ((whole,) * 4))
    _limits_project(second, template % ((as_float,) * 4))
    read = lambda project: (step_size.step_window(project), step_size.step_budget(project), run_pipeline._scan_context_budget({}, project),  # noqa: E731
                            step_size.output_warning(project, 10**9))
    assert read(first) == read(second)
    assert step_size.step_window(second) == int(whole)


def test_a_zero_window_is_the_same_as_an_integer_and_as_a_float(tmp_path) -> None:
    """0 turns the split of the generator off; 0.0 and 0e0 say the same."""
    from tools import step_size

    answers = []
    for index, zero in enumerate(("0", "0.0", "0e0", "-0.0")):
        folder = tmp_path / str(index)
        folder.mkdir()
        answers.append(step_size.step_window(_limits_project(folder, '{"step_window_bytes":%s}' % zero)))
    assert answers == [0, 0, 0, 0]


@pytest.mark.parametrize("value", [0, 5, 2000, 0.0, 2000.0, 2e3, 1.0])
def test_whole_limit_gives_an_int_for_a_whole_number(value) -> None:
    from tools.step_size import whole_limit

    assert whole_limit(value) == int(value) and type(whole_limit(value)) is int


@pytest.mark.parametrize("value", [True, False, 1.5, -0.5, 2000.25, float("nan"), float("inf"), "2000", None, [], {}])
def test_whole_limit_is_nothing_for_what_is_not_a_whole_number(value) -> None:
    from tools.step_size import whole_limit

    assert whole_limit(value) is None


@pytest.mark.parametrize("limits", ['{"step_input_bytes":1.5}', '{"step_input_bytes":true}', '{"step_window_bytes":"2000"}', '{"context_batch_bytes":2000.25}'])
def test_a_limit_that_is_not_a_whole_number_is_refused_by_the_skillsrc_not_replaced_by_the_default(tmp_path, limits: str) -> None:
    from tools import run_pipeline, step_size
    from tools.skillsrc_manifest import SkillsrcError

    project = _limits_project(tmp_path, limits)
    with pytest.raises(SkillsrcError) as caught:
        step_size.step_window(project), step_size.step_budget(project), run_pipeline._scan_context_budget({}, project)
    assert "schema validation" in str(caught.value)  # the key and the fix in this text are F5's (8.10), not this package's
