"""Framework-neutral runtime evaluator for the canonical assertion DSL.

This module deliberately implements ``portable-regex-v1`` itself.  Host regular
expression engines are not part of the cross-language contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any, Callable, Literal, Mapping

from jsonschema import Draft202012Validator, SchemaError, ValidationError


class _MissingSentinel:
    """An intentionally non-JSON-serializable marker for an absent observation."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


_MISSING = _MissingSentinel()


@dataclass(frozen=True)
class ValueState:
    """A tagged runtime value; ``data`` is meaningful only when not missing."""

    is_missing: bool
    data: Any

    def __post_init__(self) -> None:
        if not isinstance(self.is_missing, bool):
            raise ValueError("ValueState tag must be boolean.")
        if self.is_missing != (self.data is _MISSING):
            raise ValueError("ValueState missing tag and data are inconsistent.")

    @classmethod
    def value(cls, value: Any) -> "ValueState":
        return cls(False, value)

    @classmethod
    def missing(cls) -> "ValueState":
        return cls(True, _MISSING)


@dataclass(frozen=True)
class EvaluationContext:
    """Local schema resolver.  It is supplied by the caller, never by the network."""

    resolve_schema: Callable[[str], bytes]


@dataclass(frozen=True)
class AssertionResult:
    status: Literal["PASSED", "FAILED", "ERROR"]
    code: str | None
    message: str | None


@dataclass(frozen=True)
class _Atom:
    kind: Literal["literal", "dot", "class"]
    value: Any


@dataclass(frozen=True)
class _Group:
    alternatives: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class _Repeat:
    atom: _Atom
    minimum: int
    maximum: int | None


class _RegexParser:
    _META = frozenset(".|()[]?*+{}")
    _OUTSIDE_ESCAPES = _META | frozenset("\\nrt")
    _CLASS_ESCAPES = frozenset("]\\-nrt")

    def __init__(self, pattern: str) -> None:
        if not isinstance(pattern, str) or not (1 <= len(pattern) <= 512):
            raise ValueError("portable-regex-v1 pattern length is invalid")
        self._validate_scalars(pattern, pattern=True)
        self.pattern = pattern
        self.index = 0

    @staticmethod
    def _validate_scalars(value: str, *, pattern: bool) -> None:
        for character in value:
            if 0xD800 <= ord(character) <= 0xDFFF:
                raise ValueError("unpaired surrogate is not a Unicode scalar")
            if pattern and character in "\r\n\t":
                raise ValueError("raw control whitespace is forbidden in a pattern")

    def parse(self) -> tuple[tuple[Any, ...], ...]:
        result = self._parse_pattern(in_group=False)
        if self.index != len(self.pattern):
            raise ValueError("unexpected portable-regex-v1 token")
        return result

    def _parse_pattern(self, *, in_group: bool) -> tuple[tuple[Any, ...], ...]:
        alternatives = [self._parse_alternative()]
        while self._peek() == "|":
            self.index += 1
            alternatives.append(self._parse_alternative())
        if in_group:
            if self._peek() != ")":
                raise ValueError("unclosed group")
            self.index += 1
        return tuple(alternatives)

    def _parse_alternative(self) -> tuple[Any, ...]:
        pieces: list[Any] = []
        while self.index < len(self.pattern) and self._peek() not in "|)":
            pieces.append(self._parse_piece())
        if not pieces:
            raise ValueError("empty alternatives are forbidden")
        return tuple(pieces)

    def _parse_piece(self) -> Any:
        if self._peek() == "(":
            self.index += 1
            # A group deliberately cannot be quantified in V1.
            return _Group(self._parse_pattern(in_group=True))
        atom = self._parse_scalar_atom()
        minimum, maximum = 1, 1
        marker = self._peek()
        if marker == "?":
            self.index += 1
            minimum, maximum = 0, 1
        elif marker == "*":
            self.index += 1
            minimum, maximum = 0, None
        elif marker == "+":
            self.index += 1
            minimum, maximum = 1, None
        elif marker == "{":
            minimum, maximum = self._parse_bound()
        return _Repeat(atom, minimum, maximum)

    def _parse_scalar_atom(self) -> _Atom:
        character = self._peek()
        if character is None or character in "|)]?*+{}":
            raise ValueError("expected scalar atom")
        if character == ".":
            self.index += 1
            return _Atom("dot", None)
        if character == "[":
            return _Atom("class", self._parse_class())
        if character == "\\":
            self.index += 1
            return _Atom("literal", self._parse_escape(self._OUTSIDE_ESCAPES))
        self.index += 1
        return _Atom("literal", character)

    def _parse_escape(self, allowed: frozenset[str]) -> str:
        character = self._peek()
        if character is None or character not in allowed:
            raise ValueError("unsupported escape")
        self.index += 1
        return {"n": "\n", "r": "\r", "t": "\t"}.get(character, character)

    def _parse_class(self) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
        self.index += 1  # [
        if self._peek() == "^":
            # A leading caret would be the usual negated-class spelling; V1 has no
            # negated classes, even though caret remains a literal outside classes.
            raise ValueError("negated character classes are forbidden")
        literals: set[str] = set()
        ranges: list[tuple[str, str]] = []
        count = 0
        while True:
            if self._peek() is None:
                raise ValueError("unclosed character class")
            if self._peek() == "]":
                if count == 0:
                    raise ValueError("empty character class")
                self.index += 1
                return frozenset(literals), tuple(ranges)
            first, first_escaped = self._parse_class_character()
            if self._peek() == "-":
                if first_escaped:
                    raise ValueError("escaped character cannot be a range endpoint")
                self.index += 1
                if self._peek() in (None, "]"):
                    raise ValueError("invalid character range")
                second, second_escaped = self._parse_class_character()
                if (first_escaped or second_escaped or not self._is_printable_ascii(first)
                        or not self._is_printable_ascii(second) or ord(first) >= ord(second)):
                    raise ValueError("invalid character range")
                ranges.append((first, second))
            else:
                literals.add(first)
            count += 1

    def _parse_class_character(self) -> tuple[str, bool]:
        character = self._peek()
        if character is None or character in "]-":
            raise ValueError("invalid class item")
        if character == "\\":
            self.index += 1
            return self._parse_escape(self._CLASS_ESCAPES), True
        self.index += 1
        return character, False

    @staticmethod
    def _is_printable_ascii(character: str) -> bool:
        return len(character) == 1 and 0x20 <= ord(character) <= 0x7E

    def _parse_bound(self) -> tuple[int, int]:
        self.index += 1  # {
        minimum = self._parse_uint()
        if self._peek() == "}":
            self.index += 1
            return minimum, minimum
        if self._peek() != ",":
            raise ValueError("invalid quantifier")
        self.index += 1
        maximum = self._parse_uint()
        if self._peek() != "}":
            raise ValueError("invalid quantifier")
        self.index += 1
        if minimum > maximum or maximum > 1000:
            raise ValueError("quantifier bounds are invalid")
        return minimum, maximum

    def _parse_uint(self) -> int:
        start = self.index
        while self._peek() is not None and self._peek().isdigit() and self._peek().isascii():
            self.index += 1
        token = self.pattern[start:self.index]
        if not token or (len(token) > 1 and token[0] == "0"):
            raise ValueError("invalid decimal bound")
        value = int(token)
        if value > 1000:
            raise ValueError("quantifier bound exceeds 1000")
        return value

    def _peek(self) -> str | None:
        return self.pattern[self.index] if self.index < len(self.pattern) else None


@dataclass
class _NfaState:
    epsilon: set[int]
    transitions: list[tuple[_Atom, int]]
    accepting: bool = False


class _NfaCompiler:
    def __init__(self) -> None:
        self.states: list[_NfaState] = []

    def state(self) -> int:
        self.states.append(_NfaState(set(), []))
        return len(self.states) - 1

    def compile(self, alternatives: tuple[tuple[Any, ...], ...]) -> tuple[list[_NfaState], int]:
        start, end = self._compile_alternatives(alternatives)
        self.states[end].accepting = True
        return self.states, start

    def _compile_alternatives(self, alternatives: tuple[tuple[Any, ...], ...]) -> tuple[int, int]:
        start, end = self.state(), self.state()
        for alternative in alternatives:
            branch_start, branch_end = self._compile_sequence(alternative)
            self.states[start].epsilon.add(branch_start)
            self.states[branch_end].epsilon.add(end)
        return start, end

    def _compile_sequence(self, nodes: tuple[Any, ...]) -> tuple[int, int]:
        start = self.state()
        cursor = start
        for node in nodes:
            part_start, part_end = self._compile_node(node)
            self.states[cursor].epsilon.add(part_start)
            cursor = part_end
        return start, cursor

    def _compile_node(self, node: Any) -> tuple[int, int]:
        if isinstance(node, _Group):
            return self._compile_alternatives(node.alternatives)
        if not isinstance(node, _Repeat):
            raise AssertionError("unknown regex AST node")
        start = self.state()
        cursor = start
        for _ in range(node.minimum):
            part_start, part_end = self._compile_atom(node.atom)
            self.states[cursor].epsilon.add(part_start)
            cursor = part_end
        end = self.state()
        if node.maximum is None:
            loop_start, loop_end = self._compile_atom(node.atom)
            self.states[cursor].epsilon.update({end, loop_start})
            self.states[loop_end].epsilon.update({end, loop_start})
        else:
            for _ in range(node.maximum - node.minimum):
                part_start, part_end = self._compile_atom(node.atom)
                self.states[cursor].epsilon.update({end, part_start})
                cursor = part_end
            self.states[cursor].epsilon.add(end)
        return start, end

    def _compile_atom(self, atom: _Atom) -> tuple[int, int]:
        start, end = self.state(), self.state()
        self.states[start].transitions.append((atom, end))
        return start, end


def portable_fullmatch(pattern: str, value: str) -> bool:
    """Return whether *value* matches the exact V1 grammar over Unicode scalars."""
    return _portable_fullmatch_metrics(pattern, value)[0]


def _portable_fullmatch_metrics(pattern: str, value: str) -> tuple[bool, dict[str, int]]:
    """Test seam: NFA work metrics make ambiguity bounds deterministic."""
    if not isinstance(value, str):
        raise ValueError("portable-regex-v1 value must be a string")
    _RegexParser._validate_scalars(value, pattern=False)
    states, start = _NfaCompiler().compile(_RegexParser(pattern).parse())
    active = _epsilon_closure(states, {start})
    max_active = len(active)
    transition_steps = 0
    for character in value:
        next_states: set[int] = set()
        for state_id in active:
            for atom, destination in states[state_id].transitions:
                transition_steps += 1
                if _atom_matches(atom, character):
                    next_states.add(destination)
        active = _epsilon_closure(states, next_states)
        max_active = max(max_active, len(active))
        if not active:
            break
    return any(states[state_id].accepting for state_id in active), {
        "state_count": len(states), "transition_steps": transition_steps, "max_active_states": max_active,
    }


def _epsilon_closure(states: list[_NfaState], initial: set[int]) -> set[int]:
    closure = set(initial)
    stack = list(initial)
    while stack:
        state_id = stack.pop()
        for destination in states[state_id].epsilon:
            if destination not in closure:
                closure.add(destination)
                stack.append(destination)
    return closure


def _atom_matches(atom: _Atom, character: str) -> bool:
    if atom.kind == "literal":
        return character == atom.value
    if atom.kind == "dot":
        return character != "\n"
    literals, ranges = atom.value
    return character in literals or any(start <= character <= end for start, end in ranges)


_OPERATORS = frozenset({
    "equals", "not_equals", "exists", "not_exists", "contains", "matches",
    "greater_than", "greater_or_equal", "less_than", "less_or_equal", "length_equals", "schema_matches",
})


def evaluate_assertion(
    assertion: Mapping[str, Any],
    resolve_actual: Callable[[Mapping[str, Any]], ValueState],
    resolve_expected: Callable[[Mapping[str, Any]], ValueState],
    context: EvaluationContext,
) -> AssertionResult:
    """Evaluate one already schema-valid assertion without framework-specific behavior."""
    if not isinstance(assertion, Mapping):
        return _error("INVALID_ASSERTION", "Assertion must be an object.")
    operator = assertion.get("operator")
    if operator not in _OPERATORS:
        return _error("INVALID_ASSERTION", "Unsupported assertion operator.")
    has_expected = "expected" in assertion
    if operator in {"exists", "not_exists"} and has_expected:
        return _error("INVALID_ASSERTION", "Expected operand is forbidden.")
    if operator not in {"exists", "not_exists"} and not has_expected:
        return _error("INVALID_ASSERTION", "Expected operand is required.")
    try:
        actual_state = _resolved_state(resolve_actual, assertion.get("actual"))
    except Exception:
        return _error("ACTUAL_RESOLUTION_ERROR", "Could not resolve actual value.")

    if actual_state.is_missing:
        if operator == "not_exists":
            return _passed()
        return _failed("ASSERTION_MISSING", "Actual value is MISSING.")
    if operator == "exists":
        return _passed()
    if operator == "not_exists":
        return _failed()

    expected_source = assertion.get("expected")
    if operator == "matches":
        return _evaluate_match(actual_state.data, expected_source)
    if operator == "schema_matches":
        return _evaluate_schema_match(actual_state.data, expected_source, context)
    try:
        expected_state = _resolved_state(resolve_expected, expected_source)
    except Exception:
        return _error("EXPECTED_RESOLUTION_ERROR", "Could not resolve expected value.")
    if expected_state.is_missing:
        return _failed("ASSERTION_MISSING", "Expected value is MISSING.")
    return _evaluate_values(operator, actual_state.data, expected_state.data)


def _resolved_state(resolver: Callable[[Mapping[str, Any]], ValueState], source: Any) -> ValueState:
    state = resolver(source)
    if not isinstance(state, ValueState):
        raise TypeError("resolver must return ValueState")
    return state


def _evaluate_match(actual: Any, expected: Any) -> AssertionResult:
    if not isinstance(actual, str) or not isinstance(expected, Mapping) or expected.get("kind") != "regex" or expected.get("dialect") != "portable-regex-v1" or not isinstance(expected.get("pattern"), str):
        return _error("INVALID_OPERAND", "matches requires a string and portable-regex-v1 operand.")
    try:
        return _passed() if portable_fullmatch(expected["pattern"], actual) else _failed()
    except ValueError:
        return _error("INVALID_PORTABLE_REGEX", "Pattern is not portable-regex-v1.")


def _evaluate_values(operator: str, actual: Any, expected: Any) -> AssertionResult:
    if operator in {"equals", "not_equals"}:
        equal = _json_equal(actual, expected)
        return _verdict(equal if operator == "equals" else not equal)
    if operator == "contains":
        if not isinstance(actual, str) or not isinstance(expected, str):
            return _error("INVALID_OPERAND", "contains requires string operands.")
        return _verdict(expected in actual)
    if operator in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}:
        if not (_is_number(actual) and _is_number(expected)):
            return _error("INVALID_OPERAND", "Comparison requires finite numeric operands.")
        comparisons = {
            "greater_than": actual > expected,
            "greater_or_equal": actual >= expected,
            "less_than": actual < expected,
            "less_or_equal": actual <= expected,
        }
        return _verdict(comparisons[operator])
    if operator == "length_equals":
        if not isinstance(actual, (str, list, dict)) or isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            return _error("INVALID_OPERAND", "length_equals requires a collection and non-negative integer.")
        return _verdict(len(actual) == expected)
    return _error("INVALID_ASSERTION", "Unsupported assertion operator.")


def _json_equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and (not isinstance(value, float) or math.isfinite(value))


def _evaluate_schema_match(actual: Any, expected: Any, context: EvaluationContext) -> AssertionResult:
    if not isinstance(actual, (dict, list)) or not isinstance(expected, Mapping) or expected.get("kind") != "schema_ref":
        return _error("INVALID_OPERAND", "schema_matches requires object/array and schema_ref operands.")
    if expected.get("draft") != "2020-12":
        return _error("INVALID_SCHEMA_DRAFT", "schema_matches requires Draft 2020-12.")
    uri, pin = expected.get("uri"), expected.get("sha256")
    if not isinstance(uri, str) or not isinstance(pin, str) or not _is_lowercase_sha256_pin(pin):
        return _error("INVALID_SCHEMA_PIN", "Schema SHA-256 pin must be lowercase.")
    try:
        raw_schema = context.resolve_schema(uri)
    except Exception:
        return _error("SCHEMA_RESOLUTION_ERROR", "Could not resolve authorized schema bytes.")
    if not isinstance(raw_schema, bytes):
        return _error("SCHEMA_RESOLUTION_ERROR", "Could not resolve authorized schema bytes.")
    if sha256(raw_schema).hexdigest() != pin.removeprefix("sha256:"):
        return _error("SCHEMA_DIGEST_MISMATCH", "Schema bytes do not match the declared SHA-256 pin.")
    try:
        schema = _strict_json_object(raw_schema)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return _error("INVALID_SCHEMA", "Schema bytes are not strict JSON.")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        return _error("INVALID_SCHEMA_DRAFT", "schema_matches requires Draft 2020-12.")
    if _has_nonlocal_reference(schema):
        return _error("INVALID_SCHEMA", "Schema contains a non-local reference.")
    try:
        validator = Draft202012Validator(schema)
        validator.check_schema(schema)
    except SchemaError:
        return _error("INVALID_SCHEMA", "Schema is not a valid Draft 2020-12 schema.")
    try:
        validator.validate(actual)
    except ValidationError:
        return _failed()
    except Exception:
        return _error("INVALID_SCHEMA", "Schema evaluation failed.")
    return _passed()


def _is_lowercase_sha256_pin(pin: str) -> bool:
    return len(pin) == 71 and pin.startswith("sha256:") and all(character in "0123456789abcdef" for character in pin[7:])


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON constant: {constant}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is forbidden")
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError("schema root must be an object")
    return parsed


def _has_nonlocal_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key in {"$ref", "$dynamicRef"} and isinstance(item, str) and not item.startswith("#"))
            or _has_nonlocal_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_nonlocal_reference(item) for item in value)
    return False


def _passed() -> AssertionResult:
    return AssertionResult("PASSED", None, None)


def _failed(code: str = "ASSERTION_FAILED", message: str = "Assertion evaluated to false.") -> AssertionResult:
    return AssertionResult("FAILED", code, message)


def _verdict(matches: bool) -> AssertionResult:
    return _passed() if matches else _failed()


def _error(code: str, message: str) -> AssertionResult:
    return AssertionResult("ERROR", code, message)
