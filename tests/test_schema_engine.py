from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

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
    assert len(SCHEMAS) == 43
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
    assert not _errors({"pattern": "b"}, "abc") and _errors({"pattern": "^a$"}, "a\n") == []  # re.search, "$" before a final newline
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
    from tools.schema_engine import FormatChecker

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
    assert not _errors({"format": "made-up"}, "x", format_checker=checker)
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
