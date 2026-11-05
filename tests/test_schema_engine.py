from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = sorted((ROOT / "schemas").glob("*.json"))
# Formats the current jsonschema FormatChecker really checks in our environment (no rfc3339-validator and the like):
# "date" is checked, "date-time" is accepted as is. The engine repeats exactly that and is not stricter.
KNOWN_FORMATS = {"date", "date-time"}


def _registry() -> dict[str, dict]:
    result = {}
    for path in SCHEMAS:
        contents = json.loads(path.read_text(encoding="utf-8"))
        result[contents["$id"]] = contents
    return result


def _validator(schema, **kwargs):
    from tools.schema_engine import Draft202012Validator

    return Draft202012Validator(schema, **kwargs)


def _errors(schema, instance, **kwargs):
    return list(_validator(schema, **kwargs).iter_errors(instance))


def test_engine_compiles_every_schema_of_the_package() -> None:
    from tests.contract.schema_registry import assert_every_schema_is_in_the_contract_or_the_list

    assert_every_schema_is_in_the_contract_or_the_list({path.name for path in SCHEMAS})  # V0: was `== 44`
    registry = _registry()
    for contents in registry.values():
        _validator(contents, registry=registry)


def test_schemas_use_only_the_formats_the_engine_knows() -> None:
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("format"), str):
                found.add(node["format"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for contents in _registry().values():
        walk(contents)
    assert found <= KNOWN_FORMATS, found


@pytest.mark.parametrize("keyword,value", [
    ("patternProperties", {"^a": {}}), ("dependentRequired", {"a": ["b"]}), ("dependentSchemas", {"a": {}}),
    ("unevaluatedProperties", False), ("unevaluatedItems", False), ("multipleOf", 2), ("exclusiveMaximum", 3),
    ("$dynamicRef", "#x"), ("$anchor", "x"), ("madeUp", 1),
])
def test_an_unknown_keyword_is_refused_when_the_schema_is_loaded(keyword: str, value) -> None:
    from tools.schema_engine import SchemaError

    with pytest.raises(SchemaError) as caught:
        _validator({"type": "object", keyword: value})
    assert keyword in str(caught.value)
    with pytest.raises(SchemaError):
        _validator({"type": "object", "properties": {"a": {"items": {keyword: value}}}})
    with pytest.raises(SchemaError):
        _validator({"$defs": {"unused": {keyword: value}}, "type": "object"})


def test_a_property_called_like_a_keyword_is_not_a_keyword() -> None:
    schema = {"type": "object", "properties": {"patternProperties": {"type": "string"}, "multipleOf": {"type": "integer"}}}
    assert _errors(schema, {"patternProperties": "x", "multipleOf": 2}) == []
    assert [e.validator for e in _errors(schema, {"multipleOf": "x"})] == ["type"]


@pytest.mark.parametrize("schema", [
    {"type": "nope"}, {"type": 5}, {"required": "name"}, {"required": [1]}, {"minItems": -1}, {"minItems": True},
    {"pattern": "("}, {"pattern": 3}, {"enum": "ab"}, {"additionalProperties": "no"}, {"uniqueItems": "yes"},
    {"properties": []}, {"allOf": {}}, {"items": 3}, {"minimum": "1"}, {"$ref": 3}, {"format": 5},
])
def test_a_malformed_keyword_value_is_a_schema_error(schema) -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    with pytest.raises(SchemaError):
        Draft202012Validator(schema)
    with pytest.raises(SchemaError):
        Draft202012Validator.check_schema(schema)


def test_an_unresolvable_reference_is_a_schema_error() -> None:
    from tools.schema_engine import SchemaError

    for ref in ("#/$defs/missing", "other.schema.json", "other.schema.json#/$defs/x", "#anchor"):
        with pytest.raises(SchemaError):
            _validator({"$id": "schemas/a.json", "properties": {"x": {"$ref": ref}}})


def test_references_resolve_by_id_between_documents_and_recursively() -> None:
    other = {"$id": "schemas/other.json", "$defs": {"name": {"type": "string", "minLength": 2}}, "type": "object"}
    schema = {"$id": "schemas/main.json", "$defs": {"node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/node"}, "n": {"$ref": "other.json#/$defs/name"}}}},
              "properties": {"root": {"$ref": "#/$defs/node"}, "whole": {"$ref": "other.json"}}}
    registry = {"schemas/other.json": other, "schemas/main.json": schema}
    validator = _validator(schema, registry=registry)
    assert validator.is_valid({"root": {"next": {"next": {"n": "ab"}}}, "whole": {}})
    errors = list(validator.iter_errors({"root": {"next": {"next": {"n": "a"}}}, "whole": []}))
    assert sorted((tuple(e.absolute_path), e.validator) for e in errors) == [
        (("root", "next", "next", "n"), "minLength"), (("whole",), "type")]


def test_the_error_object_is_the_one_of_jsonschema() -> None:
    schema = {"type": "object", "required": ["a"], "additionalProperties": False,
              "properties": {"a": {"type": "array", "items": {"type": "integer", "minimum": 3}}}}
    errors = _errors(schema, {"a": [5, 1, "x"], "b": 1})
    by_validator = {e.validator: e for e in errors}
    minimum = next(e for e in errors if e.validator == "minimum")
    assert list(minimum.path) == ["a", 1] and list(minimum.absolute_path) == ["a", 1]
    assert list(minimum.schema_path) == ["properties", "a", "items", "minimum"]
    assert minimum.validator_value == 3 and minimum.instance == 1 and minimum.message == "1 is less than the minimum of 3"
    assert minimum.schema == {"type": "integer", "minimum": 3}
    extra = by_validator["additionalProperties"]
    assert extra.schema is schema and extra.instance == {"a": [5, 1, "x"], "b": 1}
    assert extra.message == "Additional properties are not allowed ('b' was unexpected)"
    missing = _errors({"required": ["x", "y"]}, {"x": 1})[0]
    assert missing.message == "'y' is a required property" and missing.validator == "required" and missing.validator_value == ["x", "y"]


def test_error_context_and_parent_of_any_of_and_one_of() -> None:
    schema = {"properties": {"v": {"anyOf": [{"type": "string"}, {"type": "integer", "minimum": 5}]}}}
    error = _errors(schema, {"v": 1})[0]
    assert error.validator == "anyOf" and list(error.path) == ["v"]
    assert sorted(e.validator for e in error.context) == ["minimum", "type"]
    assert all(e.parent is error for e in error.context)
    inner = next(e for e in error.context if e.validator == "minimum")
    assert list(inner.absolute_path) == ["v"] and list(inner.path) == []
    both = _errors({"oneOf": [{"type": "integer"}, {"minimum": 0}]}, 3)[0]
    assert both.validator == "oneOf" and both.message.startswith("3 is valid under each of")
    none = _errors({"oneOf": [{"type": "string"}, {"type": "null"}]}, 3)[0]
    assert none.message == "3 is not valid under any of the given schemas" and len(none.context) == 2


def test_if_then_else_do_not_put_if_into_the_schema_path() -> None:
    schema = {"if": {"properties": {"k": {"const": 1}}, "required": ["k"]}, "then": {"required": ["a"]}, "else": {"required": ["b"]}}
    then_error = _errors(schema, {"k": 1})[0]
    else_error = _errors(schema, {"k": 2})[0]
    assert list(then_error.schema_path) == ["then", "required"] and then_error.message == "'a' is a required property"
    assert list(else_error.schema_path) == ["else", "required"]
    assert _errors(schema, {"k": 1, "a": 0}) == [] and _errors(schema, {"k": 2, "b": 0}) == []


def test_ref_is_not_in_the_schema_path_and_boolean_schemas_work() -> None:
    schema = {"$defs": {"d": {"type": "integer"}}, "properties": {"x": {"$ref": "#/$defs/d"}, "never": False, "ok": True}}
    assert list(_errors(schema, {"x": "s"})[0].schema_path) == ["properties", "x", "type"]
    never = _errors(schema, {"never": 1})[0]
    assert never.message == "False schema does not allow 1" and never.validator is None
    assert _errors(schema, {"ok": 1}) == []


def test_types_follow_json_not_python() -> None:
    assert _errors({"type": "integer"}, True) and _errors({"type": "number"}, False) and _errors({"type": "integer"}, 1.5)
    assert not _errors({"type": "integer"}, 1.0) and not _errors({"type": "number"}, 2) and not _errors({"type": ["string", "null"]}, None)
    assert not _errors({"type": "boolean"}, False) and _errors({"type": "boolean"}, 0)
    message = _errors({"type": ["string", "null"]}, 3)[0].message
    assert message == "3 is not of type 'string', 'null'"
    assert _errors({"minimum": 3}, True) == [] and _errors({"minLength": 5}, 3) == []


def test_enum_const_and_unique_items_do_not_confuse_bool_and_int() -> None:
    assert _errors({"enum": [1, 2]}, True) and _errors({"const": 1}, True) and _errors({"const": True}, 1)
    assert not _errors({"const": 1}, 1.0) and _errors({"const": [1, {"a": True}]}, [1, {"a": 1}])
    assert not _errors({"const": [1, {"a": True}]}, [1, {"a": True}])
    assert _errors({"uniqueItems": True}, [1, 1.0]) and not _errors({"uniqueItems": True}, [1, True, 0, False])
    assert _errors({"uniqueItems": True}, [{"a": [1, 2]}, {"a": [1, 2]}]) and not _errors({"uniqueItems": True}, [{"a": [1, 2]}, {"a": [2, 1]}])
    assert not _errors({"uniqueItems": False}, [1, 1]) and not _errors({"uniqueItems": True}, "aa")
    assert _errors({"enum": ["a", "b"]}, "c")[0].message == "'c' is not one of ['a', 'b']"


def test_bounds_lengths_and_patterns() -> None:
    assert [e.message for e in _errors({"exclusiveMinimum": 0}, 0)] == ["0 is less than or equal to the minimum of 0"]
    assert [e.message for e in _errors({"maximum": 2}, 3)] == ["3 is greater than the maximum of 2"]
    assert _errors({"minLength": 1}, "")[0].message == "'' should be non-empty"
    assert _errors({"maxLength": 2}, "abc")[0].message == "'abc' is too long"
    assert _errors({"minItems": 2}, [1])[0].message == "[1] is too short" and _errors({"minItems": 1}, [])[0].message == "[] should be non-empty"
    assert _errors({"maxItems": 0}, [1])[0].message == "[1] is expected to be empty"
    assert _errors({"minProperties": 1}, {})[0].message == "{} should be non-empty"
    assert _errors({"maxProperties": 0}, {"a": 1})[0].message == "{'a': 1} is expected to be empty"
    assert _errors({"pattern": "^a"}, "ba")[0].message == "'ba' does not match '^a'"
    assert not _errors({"pattern": "b"}, "abc") and not _errors({"pattern": "^a$"}, "a")
    assert [e.message for e in _errors({"pattern": "^a$"}, "a\n")] == ["'a\\n' does not match '^a$'"]  # ECMA 262: "$" is the end of the string
    assert not _errors({"pattern": "^a"}, 5)


def test_contains_with_min_and_max_contains() -> None:
    schema = {"contains": {"type": "integer"}, "minContains": 2, "maxContains": 3}
    assert not _errors(schema, ["a", 1, 2])
    few = _errors(schema, ["a", 1])[0]
    assert few.validator == "minContains" and few.validator_value == 2 and few.message == "Too few items match the given schema (expected at least 2 but only 1 matched)"
    many = _errors(schema, [1, 2, 3, 4])[0]
    assert many.validator == "maxContains" and many.message == "Too many items match the given schema (expected at most 3)"
    none = _errors({"contains": {"type": "integer"}}, ["a"])[0]
    assert none.validator == "contains" and none.message == "['a'] does not contain items matching the given schema"
    assert not _errors({"contains": {"type": "integer"}, "minContains": 0}, ["a"])


def test_prefix_items_items_and_property_names() -> None:
    schema = {"prefixItems": [{"type": "string"}, {"type": "integer"}], "items": False}
    assert not _errors(schema, ["a", 1])
    assert _errors(schema, ["a", 1, 2])[0].message == "Expected at most 2 items but found 1 extra: 2"
    assert list(_errors(schema, [1, 1])[0].path) == [0]
    names = {"propertyNames": {"pattern": "^[a-z]+$"}}
    assert not _errors(names, {"ab": 1}) and [e.validator for e in _errors(names, {"A": 1})] == ["pattern"]
    assert list(_errors(names, {"A": 1})[0].path) == []


def test_additional_properties_as_a_schema_and_the_not_keyword() -> None:
    schema = {"properties": {"a": {}}, "additionalProperties": {"type": "integer"}}
    assert not _errors(schema, {"a": "x", "b": 1}) and list(_errors(schema, {"b": "x"})[0].path) == ["b"]
    assert _errors({"not": {"type": "string"}}, "x")[0].message == "'x' should not be valid under {'type': 'string'}"
    assert not _errors({"not": {"type": "string"}}, 1)


def test_format_date_is_checked_and_date_time_is_not() -> None:
    from tools.schema_engine import FormatChecker, SchemaError

    schema_date, schema_time = {"format": "date"}, {"format": "date-time"}
    checker = FormatChecker()
    assert not _errors(schema_date, "2026-11-03", format_checker=checker)
    for bad in ("2026-02-30", "2026-11-3", "20261103", "x", "2026-11-03T10:00:00Z", "٢٠٢٦-١١-٠٣", "2026-11-03\n"):
        error = _errors(schema_date, bad, format_checker=checker)
        assert [e.validator for e in error] == ["format"], bad
        assert error[0].message == f"{bad!r} is not a 'date'"
    assert not _errors(schema_date, 5, format_checker=checker)
    for odd in ("x", "2026-02-30", "", "2026-11-03 10:00"):
        assert not _errors(schema_time, odd, format_checker=checker)
    assert not _errors(schema_date, "x")  # no format checker: formats are annotations only
    with pytest.raises(SchemaError) as caught:  # fail closed: jsonschema may check a format the engine does not
        _validator({"format": "made-up"})
    assert "made-up" in str(caught.value)
    for name in ("uuid", "email", "ipv4", "regex", "time", "uri"):
        with pytest.raises(SchemaError):
            _validator({"properties": {"a": {"format": name}}})
    assert checker.conforms("2026-11-03", "date") and not checker.conforms("nope", "date") and checker.conforms("nope", "date-time")


def test_iter_errors_is_lazy_and_validate_raises_the_first_error() -> None:
    from tools.schema_engine import ValidationError

    validator = _validator({"required": ["a", "b"]})
    assert validator.is_valid({"a": 1, "b": 2}) and not validator.is_valid({})
    with pytest.raises(ValidationError) as caught:
        validator.validate({})
    assert caught.value.message == "'a' is a required property"


def test_the_schema_directory_loads_through_the_package_registry() -> None:
    from tools.schema_validation import validator_for

    validator = validator_for(Path("schemas") / "candidate-fragment.schema.json", ROOT)
    errors = list(validator.iter_errors({}))
    assert errors and all(hasattr(e, "absolute_path") for e in errors)


def test_no_tool_imports_a_third_party_package() -> None:
    """The runtime is the standard library plus the package itself: no jsonschema, yaml, referencing, rpds, attrs."""
    stdlib = set(__import__("sys").stdlib_module_names)
    local = {path.stem for path in (ROOT / "tools").glob("*.py")} | {"tools"}
    offenders = []
    for path in sorted((ROOT / "tools").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in stdlib and name not in local and name != "__future__":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
    assert not offenders, offenders


def test_requirements_txt_is_only_a_comment_and_dev_requirements_hold_the_oracle() -> None:
    runtime = [line for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert runtime == []
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert re.search(r"^jsonschema==", dev, re.M) and re.search(r"^referencing==", dev, re.M) and "-r requirements.txt" not in dev


def test_what_the_standard_library_raises_on_a_hostile_schema_is_a_schema_error() -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    broken_uri = {"$id": "https://[", "$defs": {"x": {}}, "properties": {"a": {"$ref": "#/$defs/x"}}}
    with pytest.raises(SchemaError):
        Draft202012Validator(broken_uri)
    deep: dict = {"type": "object"}
    for _ in range(20000):
        deep = {"properties": {"a": deep}}
    with pytest.raises(SchemaError):
        Draft202012Validator(deep)
    with pytest.raises(SchemaError):
        Draft202012Validator.check_schema(deep)


# --- PH: "$" is the end of the string (ECMA 262), not "before a final newline" (Python re) ---------------------------------------

@pytest.mark.parametrize("pattern,accepted,refused", [
    ("^a$", ["a"], ["a\n", "a\r\n", "a\r"]),
    (r"^a\$", ["a$", "a$\n"], ["a"]),  # backslash-dollar is a literal dollar, not an anchor: nothing after it is anchored
    (r"^a\$$", ["a$"], ["a$\n", "a$\r\n"]),  # a literal dollar and then the anchor
    (r"^a\\$", ["a\\"], ["a\\\n"]),  # two backslashes are one literal backslash and the $ after them is the anchor
    (r"^a\\\$$", ["a\\$"], ["a\\$\n"]),  # a literal backslash, a literal dollar, the anchor
    ("^[$]+$", ["$$"], ["$$\n"]),  # a dollar inside a class is a plain character
    ("^[a$]$", ["$", "a"], ["a\n", "$\n"]),
    ("^[^$]$", ["a"], ["$", "a\n"]),
    ("^[]$]+$", ["$]"], ["$]\n"]),  # Python: a bracket right after "[" belongs to the class
    ("^a$|^b$", ["a", "b"], ["a\n", "b\n"]),
    ("^(?=.{2}$)ab$", ["ab"], ["ab\n"]),  # in a lookahead too
    (r"^(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9./]+$", ["a/b", "a/.b"], ["a/b\n", "a/..\n", "a/..", "a/b\r\n"]),
    ("a$", ["xa"], ["xa\n"]),
])
def test_a_dollar_in_a_pattern_is_the_end_of_the_string_only(pattern: str, accepted: list[str], refused: list[str]) -> None:
    schema = {"type": "string", "pattern": pattern}
    for text in accepted:
        assert _errors(schema, text) == [], (pattern, text)
    for text in refused:
        assert [e.validator for e in _errors(schema, text)] == ["pattern"], (pattern, text)
        assert _errors(schema, text)[0].message == f"{text!r} does not match {pattern!r}"  # the message carries the source pattern


@pytest.mark.parametrize("pattern", ["(?i)abc", "(?m)^a$", "(?s)a.b", "(?i:abc)", "(?-i:abc)", "(?x) a", r"(?a)\w", "(?#comment $)a", "a(?m:$)"])
def test_an_inline_flag_or_comment_in_a_pattern_is_a_schema_error(pattern: str) -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    for schema in ({"pattern": pattern}, {"properties": {"x": {"items": {"pattern": pattern}}}}):
        with pytest.raises(SchemaError) as caught:
            Draft202012Validator(schema)
        assert "pattern" in str(caught.value) and repr(pattern) in str(caught.value)
        with pytest.raises(SchemaError):
            Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("pattern", ["(?:a)", "(?=a)", "(?!a)", "(?<=a)b", "(?<!a)b", "(?P<n>a)", "[(?i)]"])
def test_a_group_that_is_not_a_flag_is_not_refused(pattern: str) -> None:
    _validator({"pattern": pattern})


# --- PH2: "." is any character but a line terminator of ECMA 262 (\n \r \u2028 \u2029), not "but \n" (Python re) -----------------

@pytest.mark.parametrize("pattern,accepted,refused", [
    ("^a.b$", ["axb", "a b", "a.b", "a\x85b", "aéb"], ["a\nb", "a\rb", "a\u2028b", "a\u2029b"]),  # \x85 is no line terminator in ECMA
    (r"^a\.b$", ["a.b"], ["axb"]),  # an escaped dot is a dot
    ("^a[.]b$", ["a.b"], ["axb", "a\rb"]),  # in a class a dot is a character
    ("^[^.]b$", ["xb", "\rb", "\nb"], [".b"]),
    ("^[].]+$", ["].", "."], ["\r", "a"]),  # a bracket right after "[" belongs to the class
    ("^[^].]+$", ["a\r"], ["]", "."]),
    (r"^a\\.b$", ["a\\xb"], ["a\\\rb", "a\\\nb"]),  # two backslashes are one backslash: the dot after them is not escaped
    (r"^a\\\.b$", ["a\\.b"], ["a\\xb"]),  # a backslash and an escaped dot
    ("^.+ — .+$", ["a — b"], ["a\r — b", "a — b\r", "a\u2028 — b", "a — b\u2029"]),
    ("^.*$", ["", "abc"], ["\r", "a\u2028", "a\n"]),
    ("^.{3}$", ["abc"], ["a\rb"]),
    ("^.+?$", ["abc"], ["a\rb"]),  # lazy
    ("^(?!.*x).+$", ["abc", "a\x85"], ["abx", "a\rb"]),  # in a lookahead too
    (r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$)).+$", ["a/b", "a/.b", "a b"], ["/a", "a/..", "a/../b", "a\rb", "a\u2028b"]),  # the path pattern of the schemas
    (r"^.*\S.*$", ["a", " a "], ["a\rb"]),
    ("(?:a|.)b", ["xb"], ["\rb"]),
])
def test_a_dot_in_a_pattern_is_not_a_line_terminator(pattern: str, accepted: list[str], refused: list[str]) -> None:
    schema = {"type": "string", "pattern": pattern}
    for text in accepted:
        assert _errors(schema, text) == [], (pattern, text)
    for text in refused:
        assert [e.validator for e in _errors(schema, text)] == ["pattern"], (pattern, text)
        assert _errors(schema, text)[0].message == f"{text!r} does not match {pattern!r}"


# \d \w \D \W are Unicode in Python and ASCII in ECMA 262; no schema of the package uses them, so the engine refuses them rather than translate them.
@pytest.mark.parametrize("pattern", [r"\d", r"^\d+$", r"\w", r"^\w+$", r"\D", r"\W", r"[\d]", r"[a\w]", r"[^\W]", r"[\d-]", r"x\Dy", r"^(?=\w)a$", r"a\\\d", r"\bfoo", r"foo\b", r"\B", r"^(?:\b|a)$", r"a\\\b"])
def test_a_digit_or_word_class_in_a_pattern_is_a_schema_error(pattern: str) -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    for schema in ({"pattern": pattern}, {"properties": {"x": {"items": {"pattern": pattern}}}}):
        with pytest.raises(SchemaError) as caught:
            Draft202012Validator(schema)
        assert "pattern" in str(caught.value) and repr(pattern) in str(caught.value) and "ASCII" in str(caught.value)
        with pytest.raises(SchemaError):
            Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("pattern", [r"\bfoo", r"foo\b", r"\B", r"^(?:\b|a)$"])
def test_a_word_boundary_in_a_pattern_is_a_schema_error_that_does_not_advise_a_digit_class(pattern: str) -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    with pytest.raises(SchemaError) as caught:
        Draft202012Validator({"pattern": pattern})
    assert repr(pattern) in str(caught.value) and "boundary" in str(caught.value) and "[0-9]" not in str(caught.value)


@pytest.mark.parametrize("pattern,accepted", [
    (r"^a\\d$", ["a\\d"]),  # a backslash and the letter d
    (r"^a\\w$", ["a\\w"]),
    (r"^[\\d]$", ["\\", "d"]),  # in a class: a backslash, then d
    (r"^\S+$", ["abc"]),  # \s stays as it is (both dialects know the Unicode spaces)
    (r"^[0-9]+\.[a-z_]+$", ["1.a"]),
    (r"^[\b]$", ["\b"]),  # a backspace in a class, the same in both dialects
    (r"^a\\b$", ["a\\b"]),  # a backslash and the letter b, not a boundary
    (r"^a\\\\b$", ["a\\\\b"]),
    (r"^a\$d$", ["a$d"]),
])
def test_what_looks_like_a_digit_or_word_class_but_is_not_is_not_refused(pattern: str, accepted: list[str]) -> None:
    schema = {"type": "string", "pattern": pattern}
    for text in accepted:
        assert _errors(schema, text) == [], (pattern, text)


def _patterns_of_the_schemas() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(node, name: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "pattern" and isinstance(value, str):
                    found.append((name, value))
                else:
                    walk(value, name)
        elif isinstance(node, list):
            for value in node:
                walk(value, name)

    for path in SCHEMAS:
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    return found


# strings that the path / id / version patterns of the schemas accept
_ACCEPTED = ["a.py", "src/a.py", "src/main/java/A.java", ".", "docs/a.md", "a.json", "BATCH-a", "SREQ-001", "EXP-a.b", "CAP-x:y", "P-1", "FIX-1",
             "project-" + "a" * 24, "artifacts/" + "0" * 32 + "/generated/a.json", "artifacts/" + "0" * 32 + "/retained-rerun/reports/r.json",
             "tc-generator:BATCH-a", "mvnw", "gradlew", "gradlew.bat", "x/gradlew", "TC_CODE", "a-v1", "case.schema.json", "1.0.0", "4.0",
             "/operation", "/inputs", "/expectations/0/assertions", "Aa1" + "x" * 30, "ghp_x", "sk-x", "token"]
# Patterns whose body takes a newline as an ordinary character, so ECMA 262 accepts the string with a final newline: the two globs of .skillsrc exclude
# only "/", a backslash and ":" and then everything else; a JSON pointer token (canonical-test-document) is any character except "~" and "/".
_NEWLINE_IN_THE_BODY = {("canonical-test-document.schema.json", r"^(?:|/(?:[^~/]|~[01])*)*$"), ("skillsrc.schema.json", r"^(?![/\\])(?![A-Za-z]:)(?!.*(?:^|/)\.\.(?:/|$))[^\\:]+$")}


def test_a_final_newline_is_refused_by_every_pattern_of_the_schemas_that_ends_on_a_dollar() -> None:
    """Every (pattern, string) pair where the pattern accepts the string: the string with "\n" or "\r\n" after it is refused,
    except for the patterns whose body takes a newline as an ordinary character."""
    pairs = [(name, pattern, text) for name, pattern in sorted(set(_patterns_of_the_schemas())) for text in _ACCEPTED
             if pattern.endswith("$") and re.search(pattern, text)]
    assert len({(name, pattern) for name, pattern, _ in pairs}) >= 25 and len(pairs) >= 100
    tolerant: set[tuple[str, str]] = set()
    for name, pattern, text in pairs:
        validator = _validator({"type": "string", "pattern": pattern})
        assert validator.is_valid(text), (pattern, text)
        for tail in ("\n", "\r\n"):
            if validator.is_valid(text + tail):
                tolerant.add((name, pattern))
    assert tolerant <= _NEWLINE_IN_THE_BODY, sorted(tolerant - _NEWLINE_IN_THE_BODY)


_PATH_FIELDS = [  # (schema, path of the field inside it): the path fields that carry a pattern
    ("release-manifest.schema.json", "$defs/path"),  # V4a: run-manifest $defs/file went with the GIT_TREE state
    ("compatibility-evidence.schema.json", "$defs/file/properties/path"), ("inventory-receipt.schema.json", "properties/files/items/properties/project_path"),
    ("exclusion-receipt.schema.json", "$defs/exclusion/properties/project_path"), ("execution-baseline.schema.json", "properties/test_root"),
    ("release-eval-run.schema.json", "$defs/ref/properties/path"), ("retained-native-rerun-receipt.schema.json", "$defs/relative_path"),
    ("run-tests-output.schema.json", "$defs/artifact_evidence/oneOf/0/properties/source_path"),
    ("run-tests-output.schema.json", "$defs/artifact_evidence/oneOf/1/properties/source_path"),
]


@pytest.mark.parametrize("name,pointer", _PATH_FIELDS)
def test_a_path_with_a_final_newline_is_refused_by_the_path_fields(name: str, pointer: str) -> None:
    node = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    for step in pointer.split("/"):
        node = node[int(step)] if isinstance(node, list) else node[step]
    assert node.get("pattern", "").endswith("$"), (name, pointer)
    field = _validator(node)
    accepted = [path for path in ("a.json", "src/a.json", "src/a.py") if field.is_valid(path)]
    assert accepted, (name, pointer)
    for path in accepted:
        for tail in ("\n", "\r\n"):
            assert not field.is_valid(path + tail), (name, pointer, path, tail)


def test_the_path_fields_listed_are_all_the_path_fields_with_a_pattern() -> None:
    """A new path pattern in a schema must be listed above, so that it is tested for the final newline too."""
    listed = {(name, pointer) for name, pointer in _PATH_FIELDS}
    found = set()

    def walk(node, trail, name) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "pattern" and isinstance(value, str) and "(?!/)" in value:
                    found.add((name, "/".join(trail)))
                else:
                    walk(value, trail + [key], name)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, trail + [str(index)], name)

    for path in SCHEMAS:
        walk(json.loads(path.read_text(encoding="utf-8")), [], path.name)
    assert found == listed, sorted(found ^ listed)


# --- PH: the cosmetics of check_schema ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("schema", [
    {"type": ["string", "string"]}, {"type": ["string", "null", "string"]}, {"required": ["a", "a"]}, {"required": ["a", "b", "a"]},
    {"prefixItems": []}, {"properties": {"x": {"type": ["integer", "integer"]}}}, {"items": {"required": ["a", "a"]}},
    {"properties": {"x": {"prefixItems": []}}}, {"$defs": {"unused": {"type": ["null", "null"]}}}, {"type": []},
])
def test_a_duplicate_in_type_or_required_and_an_empty_prefix_items_are_schema_errors(schema) -> None:
    from tools.schema_engine import Draft202012Validator, SchemaError

    with pytest.raises(SchemaError):
        Draft202012Validator(schema)
    with pytest.raises(SchemaError):
        Draft202012Validator.check_schema(schema)


def test_the_distinct_forms_of_type_required_and_prefix_items_still_load() -> None:
    _validator({"type": ["string", "null"], "required": ["a", "b"], "prefixItems": [{"type": "string"}]})
    _validator({"type": "string", "required": []})


# --- F8: the boolean walk (is_valid) is the same verdict as the error walk, and a valid instance builds no error object -------------------------------

_EDGE_SCHEMAS = [  # (schema, instances); every keyword of the engine appears in at least one
    ({"type": "string"}, ["a", 1, None, True]),
    ({"type": ["integer", "null"]}, [1, None, 1.5, "x", True]),
    ({"enum": ["a", "b", 1, True, None]}, ["a", "c", 1, 1.0, True, None, 0, False]),
    ({"const": {"a": [1, 2]}}, [{"a": [1, 2]}, {"a": [2, 1]}, {"a": [1.0, 2]}, 1]),
    ({"properties": {"a": {"type": "integer"}}}, [{"a": 1}, {"a": "x"}, {}, [], "x"]),
    ({"required": ["a", "b"]}, [{"a": 1, "b": 2}, {"a": 1}, {}, [], 1]),
    ({"properties": {"a": {}}, "additionalProperties": False}, [{"a": 1}, {"a": 1, "b": 2}, {}, 1]),
    ({"properties": {"a": {}}, "additionalProperties": {"type": "integer"}}, [{"a": "x", "b": 1}, {"b": "x"}, {}, 1]),
    ({"items": {"type": "integer"}}, [[1, 2], [1, "x"], [], "x"]),
    ({"items": False}, [[], [1], 1]),
    ({"prefixItems": [{"type": "integer"}, {"type": "string"}]}, [[1, "a"], ["a", 1], [1], [], 1]),
    ({"prefixItems": [{"type": "integer"}], "items": {"type": "string"}}, [[1, "a"], [1, 2], [], ["a"]]),
    ({"contains": {"type": "integer"}}, [[1], ["a"], [], [1, 2], 1]),
    ({"contains": {"type": "integer"}, "minContains": 2, "maxContains": 3}, [[1], [1, 2], [1, 2, 3], [1, 2, 3, 4], ["a", 1, 2]]),
    ({"uniqueItems": True}, [[1, 2], [1, 1], [1, True], [{"a": 1}, {"a": 1}], [], "x"]),
    ({"minItems": 1, "maxItems": 2}, [[], [1], [1, 2], [1, 2, 3], "x"]),
    ({"minLength": 1, "maxLength": 2}, ["", "a", "ab", "abc", 1]),
    ({"pattern": "^a.c$"}, ["abc", "ab", "a\nc", "abc\n", 1]),
    ({"minimum": 1, "maximum": 3, "exclusiveMinimum": 1}, [1, 2, 3, 4, 0, 1.5, "x", True]),
    ({"minProperties": 1, "maxProperties": 2}, [{}, {"a": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}, []]),
    ({"propertyNames": {"pattern": "^[a-z]+$"}}, [{"a": 1}, {"A": 1}, {}, 1]),
    ({"allOf": [{"type": "integer"}, {"minimum": 2}]}, [1, 2, 2.5, "x"]),
    ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, ["a", 1, 1.5, None]),
    ({"oneOf": [{"type": "integer"}, {"minimum": 0}]}, [5, 1.5, -1, "x", 0]),
    ({"oneOf": [{"type": "string"}, {"minimum": 0}, {"maximum": 10}]}, [5, "a", -1, 11, 1.5]),
    ({"not": {"type": "string"}}, ["a", 1, None]),
    ({"if": {"type": "integer"}, "then": {"minimum": 0}, "else": {"type": "string"}}, [1, -1, "a", None]),
    ({"if": {"type": "integer"}, "then": {"minimum": 0}}, [1, -1, "a"]),
    ({"if": {"type": "integer"}, "else": {"type": "string"}}, [1, "a", None]),
    ({"if": {"type": "integer"}}, [1, "a"]),
    ({"format": "date"}, ["2026-11-05", "2026-02-30", "x", 1]),
    ({"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}}}, [{"a": 1}, {"a": "x"}, {}]),
    ({"$defs": {"tree": {"properties": {"c": {"items": {"$ref": "#/$defs/tree"}}, "v": {"type": "integer"}}}}, "$ref": "#/$defs/tree"},
     [{"v": 1, "c": [{"v": 2}, {"c": [{"v": 3}]}]}, {"v": 1, "c": [{"v": "x"}]}, {"c": [{"c": [{"v": "x"}]}]}]),
    ({"anyOf": [{"oneOf": [{"type": "integer"}, {"type": "string"}]}, {"not": {"type": "null"}}]}, [1, "a", None, 1.5, True]),
    (True, [1, None]),
    (False, [1, None]),
    ({"properties": {"a": True, "b": False}}, [{"a": 1}, {"b": 1}, {}]),
]


@pytest.mark.parametrize("schema,instances", _EDGE_SCHEMAS, ids=lambda value: repr(value)[:60] if isinstance(value, (dict, bool)) else "instances")
def test_is_valid_is_the_verdict_of_the_error_walk(schema, instances) -> None:
    from tools.schema_engine import FormatChecker

    validator = _validator(schema, format_checker=FormatChecker())
    for instance in instances:
        assert validator.is_valid(instance) == (next(validator.iter_errors(instance), None) is None), (schema, instance)


def test_every_keyword_of_the_engine_is_in_the_table_of_edge_cases() -> None:
    from tools.schema_engine import _VALIDATORS

    mentioned: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            mentioned.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk([schema for schema, _ in _EDGE_SCHEMAS])
    assert set(_VALIDATORS) - mentioned == set()


def test_the_boolean_walk_and_the_error_walk_have_a_rule_for_the_same_keywords() -> None:
    from tools.schema_engine import _CHECKS, _VALIDATORS

    assert _CHECKS.keys() == _VALIDATORS.keys()


@pytest.mark.parametrize("schema,instance", [
    ({"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "object", "required": ["a"]}]}, 1),
    ({"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "object", "required": ["a"]}]}, {"a": 1}),
    ({"properties": {"a": {"anyOf": [{"type": "string"}, {"items": {"type": "integer"}}]}}}, {"a": [1, 2, 3]}),
    ({"not": {"type": "string"}}, 1),
    ({"if": {"type": "integer"}, "then": {"minimum": 0}, "else": {"type": "string"}}, "a"),
    ({"contains": {"type": "integer"}}, ["a", "b", 1]),
])
def test_a_valid_instance_builds_not_one_error_object(monkeypatch: pytest.MonkeyPatch, schema, instance) -> None:
    from tools import schema_engine

    built = []
    original = schema_engine.ValidationError.__init__

    def counting(self, *args, **kwargs):
        built.append(args[:1])
        original(self, *args, **kwargs)

    monkeypatch.setattr(schema_engine.ValidationError, "__init__", counting)
    validator = _validator(schema)
    assert validator.is_valid(instance)
    assert built == [], built
    assert list(validator.iter_errors(instance)) == []
    assert built == [], built  # the walk that reports errors makes none for a valid instance either


def test_the_errors_of_an_any_of_and_a_one_of_that_no_branch_satisfies_are_unchanged() -> None:
    any_of = _errors({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 1.5)
    assert [e.message for e in any_of] == ["1.5 is not valid under any of the given schemas"]
    assert [c.message for c in any_of[0].context] == ["1.5 is not of type 'string'", "1.5 is not of type 'integer'"]
    one_of = _errors({"oneOf": [{"type": "string"}, {"type": "integer"}]}, 1.5)
    assert [e.message for e in one_of] == ["1.5 is not valid under any of the given schemas"]
    assert [c.message for c in one_of[0].context] == ["1.5 is not of type 'string'", "1.5 is not of type 'integer'"]


@pytest.mark.parametrize("schema,instance,message", [
    ({"oneOf": [{"type": "integer"}, {"minimum": 0}]}, 5, "5 is valid under each of {'minimum': 0}, {'type': 'integer'}"),
    ({"oneOf": [{"minimum": 0}, {"type": "integer"}, {"maximum": 10}]}, 5, "5 is valid under each of {'type': 'integer'}, {'maximum': 10}, {'minimum': 0}"),
    ({"oneOf": [{"type": "string"}, {"minimum": 0}, {"maximum": 10}]}, 5, "5 is valid under each of {'maximum': 10}, {'minimum': 0}"),
])
def test_the_error_of_a_one_of_that_several_branches_satisfy_is_unchanged(schema, instance, message) -> None:
    assert [e.message for e in _errors(schema, instance)] == [message]
