"""Bug hunt 8, direction 6 (05.11.2026): tools/schema_engine.py against jsonschema (Draft 2020-12) on edge cases the existing differential tests
do not name, and the strict JSON loader that feeds the engine.

Every test checks the RIGHT behaviour, so a failure is a defect.  ``control`` in the name: expected to pass, it shows the neighbouring behaviour that is right.

Run (in the copy of the package): python -m pytest tests/test_hunt8_d6_schema_engine.py -q
"""
from __future__ import annotations

import copy
import json
import math
import tempfile
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")

from tools import schema_engine  # noqa: E402
from tools.schema_validation import StrictJsonError, loads_json_strict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NAN, INF = float("nan"), float("inf")
BS = chr(92)  # a backslash, written this way so that no tool of the harness eats it


def _oracle(schema, store=None):
    registry = referencing.Registry()
    for contents in (store or {}).values():
        registry = registry.with_resource(contents["$id"], referencing.Resource.from_contents(contents))
    return jsonschema.Draft202012Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())


def _engine(schema, store=None):
    registry = {contents["$id"]: contents for contents in (store or {}).values()} or None
    return schema_engine.Draft202012Validator(schema, registry=registry, format_checker=schema_engine.FormatChecker())


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# $ref with JSON-pointer escapes
# ----------------------------------------------------------------------------------------------------------------------------------------------------

_KEYS = ["a/b", "a~b", "a%b", "a b", "~1", "~01", "", "a%2Fb", "x#y", "é", "10", "-", '"q"', "a?b", "a#b"]
_RESOLVING = ["#/$defs/a~1b", "#/$defs/a~0b", "#/$defs/a%25b", "#/$defs/a%20b", "#/$defs/~01", "#/$defs/%7E01", "#/$defs/",
              "#/$defs/%C3%A9", "#/$defs/é", "#/$defs/10", "#/$defs/-", "#/$defs/%22q%22", "#/$defs/a%3Fb", "#/$defs/a%23b",
              "#/$defs/x%23y", "#/%24defs/a~1b", "https://x.test/r.json#/$defs/a~1b", "r.json#/$defs/a~1b"]
_UNRESOLVING = ["#/$defs/a%2Fb", "#/$defs/~1", "#/$defs/%7E1", "#/$defs/%257E1", "#/$defs/a~2b", "#/$defs/a~", "#/$defs/~", "#/$defs/~0",
                "#/$defs/%", "#/$defs/%zz", "#/$defs/a%2", "#/$defs/a~1b/", "#$defs", "#/", "#/$defs//", "#//$defs/a~1b", "#/%2524defs/a~1b"]


def _pointer_schema(ref):
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://x.test/r.json", "$defs": {key: {"const": key} for key in _KEYS}, "$ref": ref}


@pytest.mark.parametrize("ref", _RESOLVING)
def test_control_ref_pointer_escapes_select_the_same_definition_as_jsonschema(ref: str) -> None:
    schema = _pointer_schema(ref)
    accepted_by_oracle = [key for key in _KEYS if _oracle(schema).is_valid(key)]
    accepted_by_engine = [key for key in _KEYS if _engine(schema).is_valid(key)]
    assert accepted_by_engine == accepted_by_oracle and len(accepted_by_oracle) == 1, (ref, accepted_by_oracle, accepted_by_engine)


@pytest.mark.parametrize("ref", _UNRESOLVING)
def test_control_ref_pointer_that_resolves_to_nothing_is_refused_by_both(ref: str) -> None:
    schema = _pointer_schema(ref)
    with pytest.raises(schema_engine.SchemaError):
        _engine(schema)
    with pytest.raises(Exception):
        _oracle(schema).is_valid("a/b")


@pytest.mark.parametrize("token", [" 1", "+1", "01", "-1", "-2", "١", "٠", "00", "1 ", "\t1"])
def test_ref_pointer_refuses_an_array_index_that_is_not_the_canonical_form_of_rfc_6901(token: str) -> None:
    """RFC 6901: an index is ``0`` or digits without a leading zero; ``-`` is past the end.  Python's ``int()`` takes a sign, spaces, a leading zero and
    non-ASCII digits, so the engine (and referencing, which jsonschema uses) resolves ``#/.../anyOf/-1`` to the LAST element and ``/01`` to the second.
    A pointer the standard calls unresolvable must be a SchemaError: fail closed."""
    schema = {"$id": "https://x.test/r.json", "$defs": {"h": {"anyOf": [{"const": "zero"}, {"const": "one"}]}}, "$ref": "#/$defs/h/anyOf/" + token}
    with pytest.raises(schema_engine.SchemaError):
        _engine(schema)


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# 1.0 vs 1, NaN / Infinity, code points, anchors, formats
# ----------------------------------------------------------------------------------------------------------------------------------------------------

_SAME_VERDICT = [
    ({"type": "integer"}, 1.0), ({"type": "integer"}, 1e300), ({"type": "integer"}, INF), ({"type": "integer"}, NAN), ({"type": "integer"}, True),
    ({"type": "integer"}, -0.0), ({"type": "integer"}, 2.5), ({"type": ["integer", "null"]}, 3.0), ({"type": "number"}, True),
    ({"type": "number"}, NAN), 
    ({"type": "number", "maximum": 0}, INF), ({"type": "number", "minimum": 0}, INF), 
    ({"type": "number", "minimum": 0}, -INF), ({"type": "number", "minimum": 0}, -0.0),
    ({"const": 1}, 1.0), ({"const": 1.0}, 1), ({"const": True}, 1), ({"const": 1}, True), ({"const": [1]}, [True]), ({"const": [1]}, [1.0]),
    ({"const": {"a": 1}}, {"a": 1.0}), ({"const": 0}, False), ({"const": 0}, -0.0), ({"const": 10 ** 30}, float(10 ** 30)), ({"const": 2 ** 53 + 1}, float(2 ** 53)),
    ({"enum": [1]}, 1.0), ({"enum": [1.0]}, 1), ({"enum": [True]}, 1), ({"enum": [[1]]}, [True]), ({"enum": [0]}, False), ({"enum": [False]}, 0),
    ({"enum": [None]}, 0), ({"enum": [""]}, None), ({"enum": ["1"]}, 1), ({"enum": [1, 2]}, 2.0),
    ({"uniqueItems": True}, [1, 1.0]), ({"uniqueItems": True}, [1, True]), ({"uniqueItems": True}, [[1], [True]]), ({"uniqueItems": True}, [{"a": 1}, {"a": 1.0}]),
    ({"uniqueItems": True}, [0, False, 0.0]), ({"uniqueItems": True}, [0, False]), ({"uniqueItems": True}, ["1", 1]), ({"uniqueItems": True}, [None, 0, ""]),
    ({"maxLength": 1}, "\U0001F600"), ({"minLength": 2}, "\U0001F600"), ({"maxLength": 2}, "é"), ({"maxLength": 1}, "\ud83d"), ({"minLength": 2}, "\ud83d"),
    ({"maxLength": 1}, "\U0001F600".encode("utf-16", "surrogatepass").decode("utf-16")), ({"maxLength": 3}, "a\U0001F600\U0001F600"),
    ({"pattern": "^.$"}, "\U0001F600"), ({"pattern": "^..$"}, "\U0001F600"), ({"pattern": "^[\U0001F600]$"}, "\U0001F600"),
    ({"pattern": "^a"}, "b\na"), ({"pattern": "a$"}, "a\nb"), ({"pattern": "^a$"}, "a\nb"), ({"pattern": "^b"}, "a\nb"), ({"pattern": "(?=a)^a"}, "a"),
    ({"pattern": "^a\nb$"}, "a\nb"), ({"pattern": "a\n$"}, "a\n"), ({"pattern": "^$"}, ""), ({"pattern": "^$"}, "x"), ({"pattern": "^[^$]+$"}, "ab"),
    ({"format": "date"}, "2024-01-01"), ({"format": "date"}, "20240101"), ({"format": "date"}, "2024-W01-1"), ({"format": "date"}, "2024-02-30"),
    ({"format": "date"}, "0000-01-01"), ({"format": "date"}, "２０２４-01-01"), ({"format": "date"}, "2024-01-01\n"), ({"format": "date"}, " 2024-01-01"),
    ({"format": "date"}, "2024-1-1"), ({"format": "date"}, "9999-12-31"), ({"format": "date"}, "2024-01-01T00:00:00"), ({"format": "date"}, 5),
    ({"format": "date"}, "1900-02-29"), ({"format": "date"}, "2000-02-29"), ({"format": "date"}, "2023-02-29"), ({"format": "date"}, "2024-00-10"),
    ({"format": "date"}, "2024-12-32"), ({"format": "date"}, "+2024-01-01"), ({"format": "date"}, "2024-01-01\u0000"), ({"format": "date"}, ""),
    ({"format": "date-time"}, "garbage"), ({"format": "date-time"}, "2024-01-01T00:00:00"), ({"format": "date-time"}, "2024-13-45T25:61:61Z"),
    ({"minItems": 1.0}, [1]), ({"maxItems": 2.0}, [1, 2, 3]), ({"minContains": 1.0, "contains": {"type": "integer"}}, [1.0]),
    ({"contains": {"const": 1}, "minContains": 0}, []), ({"contains": {"const": 1}, "maxContains": 0}, [1]),
]


def _label(case) -> str:
    schema, instance = case
    return f"{json.dumps(schema, default=str)[:70]} <- {instance!r:.40}"


@pytest.mark.parametrize("case", _SAME_VERDICT, ids=[_label(case) for case in _SAME_VERDICT])
def test_control_edge_value_gets_the_verdict_of_jsonschema(case) -> None:
    schema, instance = case
    assert _engine(schema).is_valid(instance) == _oracle(schema).is_valid(instance)


@pytest.mark.parametrize("keyword,value", [("multipleOf", 2), ("multipleOf", 0.5), ("exclusiveMaximum", 5), ("dependentRequired", {}), ("patternProperties", {}),
                                           ("unevaluatedProperties", False), ("$anchor", "x"), ("$dynamicRef", "#x"), ("maxContainsX", 1), ("contentEncoding", "base64")])
def test_control_a_keyword_the_engine_does_not_know_is_refused_when_the_schema_is_loaded(keyword: str, value) -> None:
    with pytest.raises(schema_engine.SchemaError):
        _engine({keyword: value})


def test_control_no_schema_of_the_package_uses_a_keyword_the_engine_cannot_check() -> None:
    for path in sorted((ROOT / "schemas").glob("*.json")):
        schema_engine.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_control_a_not_pattern_with_a_dollar_cannot_be_used_to_slip_a_trailing_newline_through_safe_label() -> None:
    """The engine reads ``$`` as the end of the string (ECMA 262); jsonschema (Python ``re``) lets it match before a final newline.  Inside ``not`` that makes the
    engine LENIENT where jsonschema refuses (``{"not": {"pattern": "^a$"}}`` accepts ``a<LF>``) — spec-correct, and shielded in the package by the positive
    pattern of ``safe_label``; this control pins the shield."""
    schema = json.loads((ROOT / "schemas" / "attempt.schema.json").read_text(encoding="utf-8"))
    label = schema["$defs"]["safe_label"]
    validator = _engine(label)
    for bad in ("a.b.c\n", "Ab1" + "x" * 30 + "\n", "token\n", "x\n", "a\r", "a "):
        assert not validator.is_valid(bad), bad
    assert validator.is_valid("plain-label")


def test_nan_does_not_satisfy_a_numeric_bound() -> None:
    """``nan < 0`` and ``nan > 0`` are both false, so NaN passes ``minimum``, ``maximum`` and ``exclusiveMinimum`` — in jsonschema as well.  The package's
    own rule is that numbers are finite (CANONICAL_NONFINITE_NUMBER) and the engine is meant to fail closed: a bound that NaN satisfies is no bound."""
    for schema in ({"type": "number", "minimum": 0}, {"type": "number", "maximum": 10}, {"type": "number", "exclusiveMinimum": 0}):
        assert not _engine(schema).is_valid(NAN), schema


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# strict loader: duplicate keys, non-finite numbers, unpaired surrogates
# ----------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"a":1,"a":2}', '{"a":1,"' + BS + 'u0061":2}', '{"a":{"b":1,"b":1}}', '[{"a":1,"a":1}]', '{"x":[{"k":1},{"k":1,"k":2}]}',
    '{"' + BS + 'ud83d' + BS + 'ude00":1,"\U0001F600":2}', '{"é":1,"' + BS + 'u00e9":2}', '{"a":1,"b":{"c":1},"a":3}',
])
def test_control_loader_refuses_a_duplicate_key_however_it_is_spelled(text: str) -> None:
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)


@pytest.mark.parametrize("text", ['{"a":1,"A":2}', '{"é":1,"é":2}', '[{"a":1},{"a":1}]', '{"a":{"a":1}}'])
def test_control_loader_keeps_keys_that_only_look_alike(text: str) -> None:
    assert loads_json_strict(text) is not None


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "[NaN]", '{"a":Infinity}', "-NaN"])
def test_control_loader_refuses_the_non_finite_constants(text: str) -> None:
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)


@pytest.mark.parametrize("text", ["1e999", "-1e999", "[1e999]", '{"a":1E400}', "1e400000000000", "[0.0, -1e999]", '{"duration_sec":1e999}'])
def test_loader_refuses_a_number_that_overflows_to_infinity(text: str) -> None:
    """loads_json_strict promises a value "without ... non-finites", but the standard parser turns ``1e999`` into ``inf`` without calling parse_constant
    (that hook only sees the literals NaN/Infinity/-Infinity).  The infinity then passes every ``type: number`` field and every ``minimum``, and the first
    ``json.dumps(allow_nan=False)`` of the digest code raises a bare ValueError (see the next test)."""
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)


@pytest.mark.parametrize("text", ["1e-999", "0e999999999", "-0", "-0.0", "1E2", "123456789012345678901234567890e-30", "1.7976931348623157e308"])
def test_control_loader_keeps_a_finite_number_with_a_big_exponent(text: str) -> None:
    assert math.isfinite(loads_json_strict(text))


@pytest.mark.parametrize("text", ['"' + BS + 'ud800"', '"' + BS + 'udc00"', '["' + BS + 'ud83d"]', '{"' + BS + 'ud800":1}', '"' + BS + 'ud83d' + BS + 'ud83d"'])
def test_loader_refuses_an_unpaired_surrogate(text: str) -> None:
    """A lone surrogate is not text: it cannot be encoded as UTF-8, so the digest code (``json.dumps(ensure_ascii=False).encode("utf-8")``) and every
    ``write_text`` of the value raise UnicodeEncodeError.  RFC 8259 §8.2 warns that such a string behaves unpredictably; the loader refuses a BOM and
    invalid UTF-8 already, this is the same family."""
    with pytest.raises(StrictJsonError):
        loads_json_strict(text)


@pytest.mark.parametrize("text", ['"' + BS + 'ud83d' + BS + 'ude00"', '"' + BS + 'ud83d' + BS + 'ude00' + BS + 'ud83d' + BS + 'ude00"', '"\U0001F600"'])
def test_control_loader_pairs_a_surrogate_escape_pair_into_one_code_point(text: str) -> None:
    value = loads_json_strict(text)
    assert all(not 0xD800 <= ord(char) <= 0xDFFF for char in value)


def _fragment_request(tmp: Path, tail: str):
    from tests.test_fragment_audit import _synthetic

    fragment, plan, header, context = _synthetic()
    fragment = copy.deepcopy(fragment)
    fragment.pop("digest", None)
    text = json.dumps(fragment, ensure_ascii=True)
    (tmp / "fragment.json").write_text(text[:-1] + tail + "}", encoding="utf-8")
    for name, value in (("plan", plan), ("header", header), ("context", context)):
        (tmp / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return ["validate-fragment", "--fragment", str(tmp / "fragment.json"), "--plan", str(tmp / "plan.json"), "--header", str(tmp / "header.json"),
            "--context-receipt", str(tmp / "context.json")]


def test_control_validate_fragment_with_a_finite_extra_value_is_refused_with_a_code_and_a_fix(capsys) -> None:
    from tools import api

    with tempfile.TemporaryDirectory() as folder:
        argv = _fragment_request(Path(folder), ', "zz_extra": 1')
        code = api.main(argv)
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 2 and printed["status"] == "error" and printed["code"] != "ValueError", printed


@pytest.mark.parametrize("tail,label", [(', "zz_extra": 1e999', "overflowing number"), (', "zz_extra": "' + BS + 'ud800"', "lone surrogate")])
def test_validate_fragment_answers_a_non_serialisable_model_value_with_a_refusal_the_agent_can_act_on(tail: str, label: str, capsys) -> None:
    """The model's fragment holds ``1e999`` / ``"\\ud800"``.  The strict loader lets it through, the digest of ``fill_fragment_bindings`` (before the schema is
    even asked) raises ValueError / UnicodeEncodeError, and the command prints ``{"code":"ValueError","fix":null,...}``: no schema path, no fix.  The same
    fragment with a duplicate key or a bad type is refused with a named code and a fix.  Right: the loader (or the schema check) refuses it first."""
    from tools import api

    with tempfile.TemporaryDirectory() as folder:
        argv = _fragment_request(Path(folder), tail)
        try:
            code = api.main(argv)
        except SystemExit as stop:
            code = stop.code  # the argument loader refused the file: also an answer
            printed = {"status": "error", "code": "ARGUMENT", "fix": "x"}
        else:
            printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and printed["status"] == "error", (label, printed)
    assert printed["code"] not in ("ValueError", "UnicodeEncodeError") and printed.get("fix"), (label, printed)


def _nest(depth: int):
    value = 1
    for _ in range(depth):
        value = {"c": value}
    return value


def test_control_schema_diagnostics_of_a_document_256_levels_deep_returns_diagnostics() -> None:
    from tools.schema_validation import schema_diagnostics

    assert isinstance(schema_diagnostics({"schema_version": "5.0.0", "x": _nest(255)}, ROOT / "schemas" / "tc-reviewer-output.schema.json", ROOT), list)


@pytest.mark.parametrize("depth", [256, 300])
def test_schema_diagnostics_of_a_wrapped_document_deeper_than_256_returns_diagnostics_not_an_exception(depth: int) -> None:
    """``schema_diagnostics`` caches by the JSON text of the instance and reads the text back with ``loads_json_strict`` — which refuses nesting deeper than 256.
    A document that was loaded at 256 levels and wrapped by one level of code (an envelope, a ``{"artifacts": ...}``) therefore RAISES StrictJsonError out of a
    function that promises a list of diagnostics; at a few hundred more levels the text cannot be dumped (the recursion limit of json.dumps), the cache is skipped, and the same call returns diagnostics."""
    from tools.schema_validation import schema_diagnostics

    result = schema_diagnostics({"schema_version": "5.0.0", "x": _nest(depth)}, ROOT / "schemas" / "tc-reviewer-output.schema.json", ROOT)
    assert isinstance(result, list) and result


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# 1.0 vs 1 where a consumer reads an integer the schema accepted as 1.0
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def _project_with_limits(folder: Path, limits: str) -> Path:
    (folder / ".skillsrc").write_text('{"project":{"name":"p","language":"python"},"limits":' + limits + "}", encoding="utf-8")
    return folder


def test_control_skillsrc_integer_limits_are_read() -> None:
    from tools import run_pipeline, step_size

    with tempfile.TemporaryDirectory() as folder:
        project = _project_with_limits(Path(folder), '{"step_window_bytes":0,"step_input_bytes":2000,"context_batch_bytes":3000}')
        assert (step_size.step_window(project), step_size.step_budget(project), run_pipeline._scan_context_budget({}, project)) == (0, 2000, 3000)


@pytest.mark.parametrize("limits,reader,expected", [
    ('{"step_window_bytes":0.0}', "step_window", 0), ('{"step_window_bytes":0e0}', "step_window", 0),
    ('{"step_input_bytes":2000.0}', "step_budget", 2000), ('{"step_input_bytes":2e3}', "step_budget", 2000),
])
def test_skillsrc_limit_written_as_a_float_is_honoured_or_refused_never_silently_replaced_by_the_default(limits: str, reader: str, expected: int) -> None:
    """``type: integer`` accepts ``0.0`` and ``2e3`` (a float that is whole), but step_size reads the limit with ``isinstance(value, int)`` and falls back to
    the DEFAULT without a word: ``step_window_bytes: 0.0`` ("0 turns the split off") leaves the split ON with a 1 000 000-byte window.  Right: the same value as
    the integer, or a schema/loader refusal."""
    from tools import step_size
    from tools.skillsrc_manifest import SkillsrcError

    with tempfile.TemporaryDirectory() as folder:
        project = _project_with_limits(Path(folder), limits)
        try:
            got = getattr(step_size, reader)(project)
        except SkillsrcError:
            return
        assert got == expected, (limits, got)


def test_skillsrc_context_batch_bytes_written_as_a_float_is_honoured_or_refused() -> None:
    from tools import run_pipeline
    from tools.skillsrc_manifest import SkillsrcError

    with tempfile.TemporaryDirectory() as folder:
        project = _project_with_limits(Path(folder), '{"context_batch_bytes":4096.0}')
        try:
            got = run_pipeline._scan_context_budget({}, project)
        except SkillsrcError:
            return
        assert got == 4096, got
