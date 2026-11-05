"""JSON Schema Draft 2020-12 validator on the standard library only.

It replaces ``jsonschema`` for the schemas of this package (``schemas/*.json``): no third-party code at runtime. It knows
exactly the keywords those schemas use and refuses every other keyword when a schema is loaded (fail closed), so a new
keyword in a schema cannot be silently ignored. The error object has the attributes of ``jsonschema.ValidationError`` that
the package reads (``message``, ``path``, ``absolute_path``, ``schema_path``, ``validator``, ``validator_value``,
``instance``, ``schema``, ``context``, ``parent``), and the messages, the order of the errors and the paths are the ones of
jsonschema 4.26: ``tests/test_schema_engine_differential.py`` compares both on every schema.

Formats repeat what ``jsonschema.FormatChecker()`` really checks in this environment (it has no rfc3339-validator and the
like): ``date`` is checked, ``date-time`` and every other format is accepted as an annotation. The engine is not stricter.
"""

from __future__ import annotations

import datetime
import re
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.parse import unquote, urljoin

__all__ = ["Draft202012Validator", "FormatChecker", "SchemaError", "ValidationError"]

_UNSET: Any = object()
_TYPE_NAMES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
# Keywords that carry no assertion of their own or are read by a sibling keyword (then/else by if, min/maxContains by contains).
# Formats the schemas of the package use. date is checked, date-time is an annotation; any other name is refused when the schema
# is loaded, because jsonschema may check it and the engine would silently not.
_FORMATS = frozenset({"date", "date-time"})
_ANNOTATIONS = frozenset({"$schema", "$id", "$defs", "title", "description", "then", "else", "minContains", "maxContains"})


class SchemaError(Exception):
    """The schema itself is not usable: unknown keyword, malformed value, reference that does not resolve."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(Exception):
    """One violation of a schema by an instance."""

    def __init__(self, message: str, validator: Any = _UNSET, path: Any = (), cause: Exception | None = None,
                 context: Any = (), validator_value: Any = _UNSET, instance: Any = _UNSET, schema: Any = _UNSET,
                 schema_path: Any = ()) -> None:
        super().__init__(message)
        self.message = message
        self.path = self.relative_path = deque(path)
        self.schema_path = self.relative_schema_path = deque(schema_path)
        self.context = list(context)
        self.cause = cause
        self.validator = validator
        self.validator_value = validator_value
        self.instance = instance
        self.schema = schema
        self.parent: ValidationError | None = None
        for child in self.context:
            child.parent = self

    def _set(self, **details: Any) -> None:
        for name, value in details.items():
            if getattr(self, name) is _UNSET:
                setattr(self, name, value)

    @property
    def absolute_path(self) -> deque:
        if self.parent is None:
            return self.relative_path
        path = deque(self.relative_path)
        path.extendleft(reversed(self.parent.absolute_path))
        return path

    @property
    def absolute_schema_path(self) -> deque:
        if self.parent is None:
            return self.relative_schema_path
        path = deque(self.relative_schema_path)
        path.extendleft(reversed(self.parent.absolute_schema_path))
        return path

    def __str__(self) -> str:
        return self.message


# --------------------------------------------------------------------------------------
# formats
# --------------------------------------------------------------------------------------

_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)


def _is_date(instance: Any) -> bool:
    if not isinstance(instance, str):
        return True
    try:
        return bool(_RE_DATE.fullmatch(instance) and datetime.date.fromisoformat(instance))
    except ValueError:
        return False


class FormatChecker:
    """The formats ``jsonschema.FormatChecker()`` checks here. Every other format name conforms."""

    checkers: Mapping[str, Callable[[Any], bool]] = {"date": _is_date}

    def conforms(self, instance: Any, format: str) -> bool:
        checker = self.checkers.get(format)
        return True if checker is None else checker(instance)


# --------------------------------------------------------------------------------------
# JSON value semantics
# --------------------------------------------------------------------------------------

def _is_type(instance: Any, name: str) -> bool:
    if name == "string":
        return isinstance(instance, str)
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "null":
        return instance is None
    if isinstance(instance, bool):
        return False
    if name == "number":
        return isinstance(instance, (int, float))
    return isinstance(instance, int) or (isinstance(instance, float) and instance.is_integer())


def _equal(one: Any, two: Any) -> bool:
    """JSON equality: ``True`` is not ``1``, and 1 equals 1.0, recursively."""
    if one is two:
        return True
    if isinstance(one, str) or isinstance(two, str):
        return one == two
    if isinstance(one, list) and isinstance(two, list):
        return len(one) == len(two) and all(_equal(left, right) for left, right in zip(one, two))
    if isinstance(one, dict) and isinstance(two, dict):
        return len(one) == len(two) and all(key in two and _equal(value, two[key]) for key, value in one.items())
    if isinstance(one, bool) or isinstance(two, bool):
        return isinstance(one, bool) and isinstance(two, bool) and one == two
    return bool(one == two)


def _identity(value: Any) -> Any:
    """A hashable stand-in: two JSON values are ``_equal`` exactly when their identities are equal."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, (int, float)):
        return ("num", value)
    if isinstance(value, list):
        return ("list", tuple(_identity(item) for item in value))
    if isinstance(value, dict):
        return ("dict", frozenset((key, _identity(item)) for key, item in value.items()))
    raise TypeError(type(value).__name__)


def _unique(items: list) -> bool:
    try:
        return len({_identity(item) for item in items}) == len(items)
    except TypeError:
        seen: list = []
        for item in items:
            if any(_equal(known, item) for known in seen):
                return False
            seen.append(item)
        return True


def _extras_message(extras: list) -> tuple[str, str]:
    return ", ".join(repr(extra) for extra in extras), "was" if len(extras) == 1 else "were"


# --------------------------------------------------------------------------------------
# compiled schema
# --------------------------------------------------------------------------------------

class _Node:
    """One schema (a mapping or a boolean) with its rules in the order of the schema, and the subschemas already built."""

    __slots__ = ("raw", "rules")

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.rules: list[tuple[str, Any, Callable[..., Any], Any]] = []


def _check(condition: bool, keyword: str, message: str) -> None:
    if not condition:
        raise SchemaError(f"keyword {keyword!r}: {message}")


def _is_count(value: Any) -> bool:
    return not isinstance(value, bool) and (isinstance(value, int) or (isinstance(value, float) and value.is_integer())) and value >= 0


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


class _Compiler:
    """Builds the nodes of one root schema, resolving ``$ref`` against the registry by ``$id``."""

    def __init__(self, root: Any, registry: Mapping[str, Any] | None, link: bool) -> None:
        self.link = link
        self.documents: dict[str, Any] = {}
        for uri, contents in (registry or {}).items():
            self.documents[self._document_uri(uri)] = contents
        self.root_base = ""
        if isinstance(root, dict) and isinstance(root.get("$id"), str):
            self.root_base = self._document_uri(root["$id"])
        self.documents[self.root_base] = root
        self.nodes: dict[int, _Node] = {}
        self.keep: list[Any] = []  # keeps every compiled mapping alive, so id() stays unique
        self.root = self.node(root, self.root_base)

    @staticmethod
    def _document_uri(uri: str) -> str:
        return uri.partition("#")[0]

    def node(self, raw: Any, base: str) -> _Node:
        if isinstance(raw, bool):
            return _Node(raw)
        if not isinstance(raw, dict):
            raise SchemaError(f"a schema must be an object or a boolean, not {type(raw).__name__}")
        found = self.nodes.get(id(raw))
        if found is not None:
            return found
        node = self.nodes[id(raw)] = _Node(raw)
        self.keep.append(raw)
        for keyword, value in raw.items():
            if keyword in _ANNOTATIONS:
                self._annotation(keyword, value, base, raw)
                continue
            builder = _BUILDERS.get(keyword)
            if builder is None:
                raise SchemaError(f"unknown keyword {keyword!r}: the engine does not know it and refuses to ignore it")
            node.rules.append((keyword, value, _VALIDATORS[keyword], builder(self, value, raw, base)))
        return node

    def _annotation(self, keyword: str, value: Any, base: str, raw: dict) -> None:
        if keyword in {"$id", "$schema"} and not any(raw is document for document in self.documents.values()):
            raise SchemaError(f"keyword {keyword!r} is only supported at the root of a schema document")
        if keyword == "$defs":
            _check(isinstance(value, dict), keyword, "must be an object")
            for child in value.values():
                self.node(child, base)
        elif keyword in {"then", "else"}:
            self.node(value, base)
        elif keyword in {"minContains", "maxContains"}:
            _check(_is_count(value), keyword, "must be a non-negative integer")
        elif keyword in {"$id", "$schema", "title", "description"}:
            _check(isinstance(value, str), keyword, "must be a string")

    def resolve(self, ref: str, base: str) -> _Node:
        uri = urljoin(base, ref)
        document_uri, _, fragment = uri.partition("#")
        if document_uri not in self.documents:
            raise SchemaError(f"unresolvable $ref {ref!r}: no schema with $id {document_uri!r}")
        target = self.documents[document_uri]
        fragment = unquote(fragment)
        if fragment:
            if not fragment.startswith("/"):
                raise SchemaError(f"unresolvable $ref {ref!r}: only JSON pointer fragments are supported")
            for token in fragment[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                try:
                    target = target[int(token)] if isinstance(target, list) else target[token]
                except (KeyError, IndexError, ValueError, TypeError):
                    raise SchemaError(f"unresolvable $ref {ref!r}: {fragment!r} does not exist") from None
        return self.node(target, document_uri)


def _b_schema(compiler: _Compiler, value: Any, raw: dict, base: str) -> _Node:
    return compiler.node(value, base)


def _b_schema_list(keyword: str):
    def build(compiler: _Compiler, value: Any, raw: dict, base: str) -> list[_Node]:
        _check(isinstance(value, list) and bool(value), keyword, "must be a non-empty array of schemas")
        return [compiler.node(item, base) for item in value]
    return build


def _b_properties(compiler: _Compiler, value: Any, raw: dict, base: str) -> list[tuple[str, _Node]]:
    _check(isinstance(value, dict), "properties", "must be an object")
    return [(name, compiler.node(child, base)) for name, child in value.items()]


def _b_additional(compiler: _Compiler, value: Any, raw: dict, base: str) -> tuple[Any, frozenset]:
    _check(isinstance(value, (bool, dict)), "additionalProperties", "must be a boolean or a schema")
    properties = raw.get("properties", {})
    known = frozenset(properties) if isinstance(properties, dict) else frozenset()
    return (compiler.node(value, base) if isinstance(value, dict) else value), known


def _b_items(compiler: _Compiler, value: Any, raw: dict, base: str) -> tuple[_Node, int]:
    _check(isinstance(value, (bool, dict)), "items", "must be a boolean or a schema")
    prefix = raw.get("prefixItems", [])
    return compiler.node(value, base), len(prefix) if isinstance(prefix, list) else 0


def _b_ref(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
    _check(isinstance(value, str), "$ref", "must be a string")
    return compiler.resolve(value, base) if compiler.link else None


def _b_if(compiler: _Compiler, value: Any, raw: dict, base: str) -> tuple[_Node, Any, Any]:
    then, otherwise = raw.get("then"), raw.get("else")
    return (compiler.node(value, base),
            compiler.node(then, base) if "then" in raw else None,
            compiler.node(otherwise, base) if "else" in raw else None)


def _b_contains(compiler: _Compiler, value: Any, raw: dict, base: str) -> tuple[_Node, Any, Any]:
    return compiler.node(value, base), raw.get("minContains", 1), raw.get("maxContains")


def _b_type(compiler: _Compiler, value: Any, raw: dict, base: str) -> list[str]:
    names = [value] if isinstance(value, str) else value
    _check(isinstance(names, list) and bool(names) and all(isinstance(name, str) and name in _TYPE_NAMES for name in names),
           "type", f"must be one of {sorted(_TYPE_NAMES)} or a list of them")
    _check(len(set(names)) == len(names), "type", "must not name a type twice")
    return names


def _b_required(compiler: _Compiler, value: Any, raw: dict, base: str) -> list[str]:
    _check(isinstance(value, list) and all(isinstance(name, str) for name in value), "required", "must be an array of strings")
    _check(len(set(value)) == len(value), "required", "must not name a property twice")
    return value


def _b_enum(compiler: _Compiler, value: Any, raw: dict, base: str) -> frozenset:
    _check(isinstance(value, list), "enum", "must be an array")
    return frozenset(item for item in value if isinstance(item, str))


def _b_any(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
    return value


def _b_count(keyword: str):
    def build(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
        _check(_is_count(value), keyword, "must be a non-negative integer")
        return value
    return build


def _b_number(keyword: str):
    def build(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
        _check(_is_number(value), keyword, "must be a number")
        return value
    return build


_NOT_A_LINE_TERMINATOR = r"[^\n\r\u2028\u2029]"  # the "." of ECMA 262 (Python's also matches \r, \u2028 and \u2029)


def _ecma_pattern(pattern: str) -> str:
    r"""*pattern* in the dialect of ECMA 262 (JSON Schema), written for Python ``re``: every ``$`` anchor as ``\Z`` and every ``.`` outside a class as
    any character but a line terminator.  ECMA has no flag ``m`` here, so ``$`` is the end of the string; Python's ``$`` also matches before a final
    newline, which would let ``a.py`` + newline through every path pattern, and Python's ``.`` lets ``\r``, ``\u2028`` and ``\u2029`` through.
    A backslash takes the next character (``\$`` is a dollar sign, ``\.`` a dot, ``\\$`` a backslash and the anchor); in a class both are characters.
    ``\d \D \w \W`` and the word boundaries ``\b \B`` (outside a class) are Unicode in Python and ASCII in ECMA; no schema of the package uses them, so they are
    refused instead of translated.  ``\s`` and ``\S`` stay: both dialects are Unicode there and differ only in a few characters.
    Python-only syntax that would change what ``$`` means or hide it — inline flags, ``(?#`` comments — is refused as well."""
    out: list[str] = []
    index, size = 0, len(pattern)
    in_class = False
    while index < size:
        char = pattern[index]
        if char == "\\":
            escaped = pattern[index + 1:index + 2]
            if escaped in _UNICODE_CLASSES or (escaped in _WORD_BOUNDARIES and not in_class):  # in a class "\b" is a backspace in both dialects
                hint = ("write the boundary out with lookarounds over [A-Za-z0-9_]" if escaped in _WORD_BOUNDARIES
                        else "write the class out ([0-9], [A-Za-z0-9_])")
                raise SchemaError(f"keyword 'pattern': {pattern!r} uses \\{escaped}, which is Unicode in Python and ASCII in ECMA 262; {hint}")
            out.append(pattern[index:index + 2])
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
            out.append(char)
            index += 1
            for first in ("^", "]"):  # Python: a "]" right after "[" or "[^" is a member of the class
                if pattern.startswith(first, index):
                    out.append(first)
                    index += 1
            continue
        elif pattern.startswith("(?", index) and pattern[index + 2:index + 3] in _INLINE_FLAG_START:
            raise SchemaError(f"keyword 'pattern': {pattern!r} uses an inline flag or comment, which ECMA 262 does not have")
        elif char == "$":
            out.append("\\Z")
            index += 1
            continue
        elif char == ".":
            out.append(_NOT_A_LINE_TERMINATOR)
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


_INLINE_FLAG_START = frozenset("aiLmsux-#")
_UNICODE_CLASSES = frozenset("dDwW")  # frozensets, not strings: the empty slice after a lone final backslash must not be "in" them
_WORD_BOUNDARIES = frozenset("bB")  # a boundary between \w and \W: as Unicode-dependent as they are


def _b_pattern(compiler: _Compiler, value: Any, raw: dict, base: str) -> tuple[re.Pattern, str]:
    _check(isinstance(value, str), "pattern", "must be a string")
    try:
        return re.compile(_ecma_pattern(value)), value
    except re.error as error:
        raise SchemaError(f"keyword 'pattern': {value!r} is not a regular expression: {error}") from None


def _b_unique(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
    _check(isinstance(value, bool), "uniqueItems", "must be a boolean")
    return value


def _b_format(compiler: _Compiler, value: Any, raw: dict, base: str) -> Any:
    _check(isinstance(value, str), "format", "must be a string")
    _check(value in _FORMATS, "format", f"{value!r} is not a format the engine knows ({', '.join(sorted(_FORMATS))})")
    return value


# --------------------------------------------------------------------------------------
# keyword validators: (validator, argument, instance, raw schema) -> errors or None
# --------------------------------------------------------------------------------------

def _v_type(validator, names, instance, raw):
    if not any(_is_type(instance, name) for name in names):
        return [ValidationError(f"{instance!r} is not of type {', '.join(repr(name) for name in names)}")]


def _v_required(validator, names, instance, raw):
    if isinstance(instance, dict):
        return [ValidationError(f"{name!r} is a required property") for name in names if name not in instance] or None


def _v_properties(validator, children, instance, raw):
    if isinstance(instance, dict):
        return _properties(validator, children, instance)


def _properties(validator, children, instance):
    for name, child in children:
        if name in instance:
            yield from validator.descend(instance[name], child, path=name, schema_path=name)


def _v_additional(validator, argument, instance, raw):
    if not isinstance(instance, dict):
        return None
    allowed, known = argument
    extras = [name for name in instance if name not in known]
    if isinstance(allowed, _Node):
        return _additional_schema(validator, allowed, instance, extras)
    if not allowed and extras:
        text, verb = _extras_message(sorted(extras, key=str))
        return [ValidationError(f"Additional properties are not allowed ({text} {verb} unexpected)")]


def _additional_schema(validator, node, instance, extras):
    for name in extras:
        yield from validator.descend(instance[name], node, path=name)


def _v_property_names(validator, node, instance, raw):
    if isinstance(instance, dict):
        return _property_names(validator, node, instance)


def _property_names(validator, node, instance):
    for name in instance:
        yield from validator.descend(name, node)


def _v_items(validator, argument, instance, raw):
    if not isinstance(instance, list):
        return None
    node, prefix = argument
    total = len(instance)
    extra = total - prefix
    if extra <= 0:
        return None
    if node.raw is False:
        rest = instance[prefix:] if extra != 1 else instance[prefix]
        noun = "items" if prefix != 1 else "item"
        return [ValidationError(f"Expected at most {prefix} {noun} but found {extra} extra: {rest!r}")]
    if node.raw is True:
        return None
    return _items(validator, node, instance, prefix)


def _items(validator, node, instance, prefix):
    for index in range(prefix, len(instance)):
        yield from validator.descend(instance[index], node, path=index)


def _v_prefix_items(validator, nodes, instance, raw):
    if isinstance(instance, list):
        return _prefix_items(validator, nodes, instance)


def _prefix_items(validator, nodes, instance):
    for index, (item, node) in enumerate(zip(instance, nodes)):
        yield from validator.descend(item, node, path=index, schema_path=index)


def _v_contains(validator, argument, instance, raw):
    if not isinstance(instance, list):
        return None
    node, minimum, maximum = argument
    maximum = len(instance) if maximum is None else maximum
    matches = 0
    for item in instance:
        if validator.is_valid_node(node, item):
            matches += 1
            if matches > maximum:
                return [ValidationError(f"Too many items match the given schema (expected at most {maximum})",
                                        validator="maxContains", validator_value=maximum)]
    if matches < minimum:
        if not matches:
            return [ValidationError(f"{instance!r} does not contain items matching the given schema")]
        return [ValidationError(f"Too few items match the given schema (expected at least {minimum} but only {matches} matched)",
                                validator="minContains", validator_value=minimum)]


def _v_unique(validator, flag, instance, raw):
    if flag and isinstance(instance, list) and not _unique(instance):
        return [ValidationError(f"{instance!r} has non-unique elements")]


def _v_enum(validator, strings, instance, raw):
    if isinstance(instance, str):
        if instance in strings:
            return None
    elif any(_equal(each, instance) for each in raw["enum"]):
        return None
    return [ValidationError(f"{instance!r} is not one of {raw['enum']!r}")]


def _v_const(validator, expected, instance, raw):
    if not _equal(instance, expected):
        return [ValidationError(f"{expected!r} was expected")]


def _v_pattern(validator, built, instance, raw):
    compiled, source = built
    if isinstance(instance, str) and not compiled.search(instance):
        return [ValidationError(f"{instance!r} does not match {source!r}")]


def _v_format(validator, name, instance, raw):
    checker = validator.format_checker
    if checker is not None and not checker.conforms(instance, name):
        return [ValidationError(f"{instance!r} is not a {name!r}")]


def _v_min_length(validator, bound, instance, raw):
    if isinstance(instance, str) and len(instance) < bound:
        return [ValidationError(f"{instance!r} {'should be non-empty' if bound == 1 else 'is too short'}")]


def _v_max_length(validator, bound, instance, raw):
    if isinstance(instance, str) and len(instance) > bound:
        return [ValidationError(f"{instance!r} {'is expected to be empty' if bound == 0 else 'is too long'}")]


def _v_min_items(validator, bound, instance, raw):
    if isinstance(instance, list) and len(instance) < bound:
        return [ValidationError(f"{instance!r} {'should be non-empty' if bound == 1 else 'is too short'}")]


def _v_max_items(validator, bound, instance, raw):
    if isinstance(instance, list) and len(instance) > bound:
        return [ValidationError(f"{instance!r} {'is expected to be empty' if bound == 0 else 'is too long'}")]


def _v_min_properties(validator, bound, instance, raw):
    if isinstance(instance, dict) and len(instance) < bound:
        return [ValidationError(f"{instance!r} {'should be non-empty' if bound == 1 else 'does not have enough properties'}")]


def _v_max_properties(validator, bound, instance, raw):
    if isinstance(instance, dict) and len(instance) > bound:
        return [ValidationError(f"{instance!r} {'is expected to be empty' if bound == 0 else 'has too many properties'}")]


def _v_minimum(validator, bound, instance, raw):
    if _is_number(instance) and instance < bound:
        return [ValidationError(f"{instance!r} is less than the minimum of {bound!r}")]


def _v_maximum(validator, bound, instance, raw):
    if _is_number(instance) and instance > bound:
        return [ValidationError(f"{instance!r} is greater than the maximum of {bound!r}")]


def _v_exclusive_minimum(validator, bound, instance, raw):
    if _is_number(instance) and instance <= bound:
        return [ValidationError(f"{instance!r} is less than or equal to the minimum of {bound!r}")]


def _v_all_of(validator, nodes, instance, raw):
    return _all_of(validator, nodes, instance)


def _all_of(validator, nodes, instance):
    for index, node in enumerate(nodes):
        yield from validator.descend(instance, node, schema_path=index)


def _v_any_of(validator, nodes, instance, raw):
    if any(validator.is_valid_node(node, instance) for node in nodes):  # probe the branches first: the error objects are built only when none is valid
        return None
    collected: list[ValidationError] = []
    for index, node in enumerate(nodes):
        collected.extend(validator.descend(instance, node, schema_path=index))
    return [ValidationError(f"{instance!r} is not valid under any of the given schemas", context=collected)]


def _v_one_of(validator, nodes, instance, raw):
    collected: list[ValidationError] = []
    result: list[ValidationError] = []
    first_valid = None
    position = 0
    for position, node in enumerate(nodes, 1):
        if validator.is_valid_node(node, instance):  # probe; the errors of the failing branches are built only when none is valid
            first_valid = raw["oneOf"][position - 1]
            break
    else:
        for index, node in enumerate(nodes):
            collected.extend(validator.descend(instance, node, schema_path=index))
        result.append(ValidationError(f"{instance!r} is not valid under any of the given schemas", context=collected))
        position = len(nodes)
    more_valid = [raw["oneOf"][index] for index in range(position, len(nodes)) if validator.is_valid_node(nodes[index], instance)]
    if more_valid:
        more_valid.append(first_valid)
        result.append(ValidationError(f"{instance!r} is valid under each of {', '.join(repr(each) for each in more_valid)}"))
    return result or None


def _v_not(validator, node, instance, raw):
    if validator.is_valid_node(node, instance):
        return [ValidationError(f"{instance!r} should not be valid under {raw['not']!r}")]


def _v_if(validator, argument, instance, raw):
    condition, then, otherwise = argument
    if validator.is_valid_node(condition, instance):
        if then is not None:
            return validator.descend(instance, then, schema_path="then")
    elif otherwise is not None:
        return validator.descend(instance, otherwise, schema_path="else")


def _v_ref(validator, target, instance, raw):
    return validator.descend(instance, target)


_BUILDERS: dict[str, Callable[..., Any]] = {
    "$ref": _b_ref, "type": _b_type, "enum": _b_enum, "const": _b_any, "properties": _b_properties, "required": _b_required,
    "additionalProperties": _b_additional, "items": _b_items, "prefixItems": _b_schema_list("prefixItems"),
    "contains": _b_contains, "uniqueItems": _b_unique, "minItems": _b_count("minItems"), "maxItems": _b_count("maxItems"),
    "minLength": _b_count("minLength"), "maxLength": _b_count("maxLength"), "pattern": _b_pattern,
    "minimum": _b_number("minimum"), "maximum": _b_number("maximum"), "exclusiveMinimum": _b_number("exclusiveMinimum"),
    "minProperties": _b_count("minProperties"), "maxProperties": _b_count("maxProperties"), "propertyNames": _b_schema,
    "allOf": _b_schema_list("allOf"), "anyOf": _b_schema_list("anyOf"), "oneOf": _b_schema_list("oneOf"), "not": _b_schema,
    "if": _b_if, "format": _b_format,
}
_VALIDATORS: dict[str, Callable[..., Any]] = {
    "$ref": _v_ref, "type": _v_type, "enum": _v_enum, "const": _v_const, "properties": _v_properties, "required": _v_required,
    "additionalProperties": _v_additional, "items": _v_items, "prefixItems": _v_prefix_items, "contains": _v_contains,
    "uniqueItems": _v_unique, "minItems": _v_min_items, "maxItems": _v_max_items, "minLength": _v_min_length,
    "maxLength": _v_max_length, "pattern": _v_pattern, "minimum": _v_minimum, "maximum": _v_maximum,
    "exclusiveMinimum": _v_exclusive_minimum, "minProperties": _v_min_properties, "maxProperties": _v_max_properties,
    "propertyNames": _v_property_names, "allOf": _v_all_of, "anyOf": _v_any_of, "oneOf": _v_one_of, "not": _v_not, "if": _v_if,
    "format": _v_format,
}
assert _BUILDERS.keys() == _VALIDATORS.keys()


# The boolean twin of `descend`: the same rules, no error objects, the first failing keyword ends the walk.  `is_valid` and the probes of anyOf, oneOf,
# not, if and contains use it, so a valid instance (the usual case) builds no ValidationError at all.  Every rule of _VALIDATORS has its twin in _CHECKS
# (asserted below); tests/test_schema_engine*.py hold the two walks to the same verdict.
def _ok(validator, node: _Node, instance: Any) -> bool:
    raw = node.raw
    if raw is True:
        return True
    if raw is False:
        return False
    for keyword, value, _function, argument in node.rules:
        if not _CHECKS[keyword](validator, argument, instance, raw):
            return False
    return True


def _c_ref(v, target, instance, raw):
    return _ok(v, target, instance)


def _c_type(v, names, instance, raw):
    return any(_is_type(instance, name) for name in names)


def _c_enum(v, strings, instance, raw):
    if isinstance(instance, str):
        return instance in strings
    return any(_equal(each, instance) for each in raw["enum"])


def _c_const(v, expected, instance, raw):
    return _equal(instance, expected)


def _c_properties(v, children, instance, raw):
    if not isinstance(instance, dict):
        return True
    for name, child in children:
        if name in instance and not _ok(v, child, instance[name]):
            return False
    return True


def _c_required(v, names, instance, raw):
    return not isinstance(instance, dict) or all(name in instance for name in names)


def _c_additional(v, argument, instance, raw):
    if not isinstance(instance, dict):
        return True
    allowed, known = argument
    if isinstance(allowed, _Node):
        return all(_ok(v, allowed, item) for name, item in instance.items() if name not in known)
    return bool(allowed) or all(name in known for name in instance)


def _c_items(v, argument, instance, raw):
    if not isinstance(instance, list):
        return True
    node, prefix = argument
    if len(instance) <= prefix:
        return True
    if node.raw is False:
        return False
    if node.raw is True:
        return True
    return all(_ok(v, node, instance[index]) for index in range(prefix, len(instance)))


def _c_prefix_items(v, nodes, instance, raw):
    return not isinstance(instance, list) or all(_ok(v, node, item) for item, node in zip(instance, nodes))


def _c_contains(v, argument, instance, raw):
    if not isinstance(instance, list):
        return True
    node, minimum, maximum = argument
    maximum = len(instance) if maximum is None else maximum
    matches = 0
    for item in instance:
        if _ok(v, node, item):
            matches += 1
            if matches > maximum:
                return False
    return matches >= minimum


def _c_unique(v, flag, instance, raw):
    return not (flag and isinstance(instance, list)) or _unique(instance)


def _c_pattern(v, built, instance, raw):
    return not isinstance(instance, str) or built[0].search(instance) is not None


def _c_format(v, name, instance, raw):
    checker = v.format_checker
    return checker is None or checker.conforms(instance, name)


def _c_min_length(v, bound, instance, raw):
    return not isinstance(instance, str) or len(instance) >= bound


def _c_max_length(v, bound, instance, raw):
    return not isinstance(instance, str) or len(instance) <= bound


def _c_min_items(v, bound, instance, raw):
    return not isinstance(instance, list) or len(instance) >= bound


def _c_max_items(v, bound, instance, raw):
    return not isinstance(instance, list) or len(instance) <= bound


def _c_min_properties(v, bound, instance, raw):
    return not isinstance(instance, dict) or len(instance) >= bound


def _c_max_properties(v, bound, instance, raw):
    return not isinstance(instance, dict) or len(instance) <= bound


def _c_minimum(v, bound, instance, raw):
    return not _is_number(instance) or instance >= bound


def _c_maximum(v, bound, instance, raw):
    return not _is_number(instance) or instance <= bound


def _c_exclusive_minimum(v, bound, instance, raw):
    return not _is_number(instance) or instance > bound


def _c_property_names(v, node, instance, raw):
    return not isinstance(instance, dict) or all(_ok(v, node, name) for name in instance)


def _c_all_of(v, nodes, instance, raw):
    return all(_ok(v, node, instance) for node in nodes)


def _c_any_of(v, nodes, instance, raw):
    return any(_ok(v, node, instance) for node in nodes)


def _c_one_of(v, nodes, instance, raw):
    return sum(1 for node in nodes if _ok(v, node, instance)) == 1


def _c_not(v, node, instance, raw):
    return not _ok(v, node, instance)


def _c_if(v, argument, instance, raw):
    condition, then, otherwise = argument
    if _ok(v, condition, instance):
        return then is None or _ok(v, then, instance)
    return otherwise is None or _ok(v, otherwise, instance)


_CHECKS: dict[str, Callable[..., bool]] = {
    "$ref": _c_ref, "type": _c_type, "enum": _c_enum, "const": _c_const, "properties": _c_properties, "required": _c_required,
    "additionalProperties": _c_additional, "items": _c_items, "prefixItems": _c_prefix_items, "contains": _c_contains,
    "uniqueItems": _c_unique, "minItems": _c_min_items, "maxItems": _c_max_items, "minLength": _c_min_length,
    "maxLength": _c_max_length, "pattern": _c_pattern, "minimum": _c_minimum, "maximum": _c_maximum,
    "exclusiveMinimum": _c_exclusive_minimum, "minProperties": _c_min_properties, "maxProperties": _c_max_properties,
    "propertyNames": _c_property_names, "allOf": _c_all_of, "anyOf": _c_any_of, "oneOf": _c_one_of, "not": _c_not, "if": _c_if,
    "format": _c_format,
}
assert _CHECKS.keys() == _VALIDATORS.keys()


def _compile(schema: Any, registry: Mapping[str, Any] | None, link: bool) -> _Node:
    """Build the root node; whatever the standard library raises on a hostile schema (a broken URI, an absurd nesting) is a SchemaError."""
    try:
        return _Compiler(schema, registry, link).root
    except SchemaError:
        raise
    except (ValueError, OverflowError, RecursionError, re.error) as error:
        raise SchemaError(f"the schema cannot be loaded: {type(error).__name__}: {error}") from error


class Draft202012Validator:
    """``Draft202012Validator(schema, registry=None, format_checker=None)`` with the calls the package uses.

    *registry* maps the ``$id`` of every other schema to its contents; ``$ref`` resolves against it by ``$id`` and fails
    when the schema is loaded, not when the reference is first reached.
    """

    def __init__(self, schema: Any, registry: Mapping[str, Any] | None = None, format_checker: FormatChecker | None = None) -> None:
        self.schema = schema
        self.format_checker = format_checker
        self._root = _compile(schema, registry, link=True)

    @classmethod
    def check_schema(cls, schema: Any) -> None:
        """Raise ``SchemaError`` for an unknown keyword or a malformed keyword value. References are not resolved."""
        _compile(schema, None, link=False)

    def iter_errors(self, instance: Any) -> Iterator[ValidationError]:
        return self.descend(instance, self._root)

    def is_valid(self, instance: Any) -> bool:
        return self.is_valid_node(self._root, instance)

    def validate(self, instance: Any) -> None:
        for error in self.iter_errors(instance):
            raise error

    def is_valid_node(self, node: _Node, instance: Any) -> bool:
        return _ok(self, node, instance)

    def descend(self, instance: Any, node: _Node, path: Any = None, schema_path: Any = None) -> Iterator[ValidationError]:
        raw = node.raw
        if raw is True:
            return
        if raw is False:
            yield ValidationError(f"False schema does not allow {instance!r}", validator=None, validator_value=None,
                                  instance=instance, schema=raw)
            return
        for keyword, value, function, argument in node.rules:
            errors = function(self, argument, instance, raw)
            if not errors:
                continue
            for error in errors:
                error._set(validator=keyword, validator_value=value, instance=instance, schema=raw)
                if keyword not in ("if", "$ref"):
                    error.schema_path.appendleft(keyword)
                if path is not None:
                    error.path.appendleft(path)
                if schema_path is not None:
                    error.schema_path.appendleft(schema_path)
                yield error
