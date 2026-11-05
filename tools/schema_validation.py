"""Strict JSON loading and repository-local Draft 2020-12 validation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import re
import threading
from collections.abc import Iterable
from functools import lru_cache
from itertools import accumulate
from pathlib import Path
from typing import Any

if __package__:
    from .schema_engine import Draft202012Validator, FormatChecker
else:  # direct CLI execution (validate_artifact.py)
    from schema_engine import Draft202012Validator, FormatChecker


class StrictJsonError(ValueError):
    """Raised when JSON bytes are not one strict, complete JSON value."""


class SchemaRegistryError(ValueError):
    """Raised when a schema is not a repository-local schema resource."""


def _reject_constant(token: str) -> None:
    raise StrictJsonError(f"non-finite JSON constant: {token}")


def _reject_float(token: str) -> float:
    """``1e999`` is a valid JSON number that Python reads as infinity: refuse it as the literals NaN and Infinity are refused."""
    value = float(token)
    if not math.isfinite(value):
        raise StrictJsonError(f"non-finite JSON number: {token[:40]}")
    return value


# A text that can hold an unpaired surrogate: an escape of a surrogate (\ud800-\udfff) or the character itself.  Nearly no text does, so the walk below
# costs nothing on an ordinary response; a valid pair of escapes (an emoji) lets the walk run and passes it, because json joins a pair into one character.
_SURROGATE_HINT = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}|[\ud800-\udfff]")
_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _reject_surrogates(value: Any) -> None:
    """A string or a key with a lone surrogate cannot be written as UTF-8: the digests and the files of the package would fail on it far from here."""
    if isinstance(value, str):
        if _SURROGATE.search(value):
            raise StrictJsonError("unpaired UTF-16 surrogate in a string")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_surrogates(key)
            _reject_surrogates(item)
    elif isinstance(value, list):
        for item in value:
            _reject_surrogates(item)


def _reject_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate object key: {key}")
        result[key] = value
    return result


MAX_JSON_DEPTH = 256  # nesting levels of arrays and objects; the deepest document of the package has 14
# A quote closes a string unless a backslash escapes it.  The closing quote is optional: an unterminated string (a response cut off in the middle of one) takes
# the rest of the text in one pass; with a mandatory quote the search would try every quote of the rest as a start and walk to the end of the text each time.
_JSON_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"?', re.DOTALL)
_NOT_A_BRACKET = re.compile(r"[^\[\]{}]+")
_LEVEL = {"[": 1, "{": 1, "]": -1, "}": -1}


def _check_json_depth(text: str) -> None:
    """Refuse a nesting deeper than MAX_JSON_DEPTH before the parser sees it.  The standard library's scanner stops at a depth of its own (about the
    recursion limit on one platform, far beyond it on another), so leaving the refusal to it makes the policy depend on the platform.  Only the brackets
    outside strings count: take the strings and everything else out with two regular expressions, then the highest running sum of +1 (opening) and -1
    (closing) is the depth.  All of it runs at C speed, in one pass over the brackets; a text that is not JSON is left to the parser to refuse."""
    if text.count("[") + text.count("{") <= MAX_JSON_DEPTH:  # the depth cannot exceed the number of opening brackets
        return
    brackets = _NOT_A_BRACKET.sub("", _JSON_STRING.sub("", text))
    if max(accumulate(map(_LEVEL.__getitem__, brackets)), default=0) > MAX_JSON_DEPTH:
        raise StrictJsonError(f"JSON nesting is deeper than {MAX_JSON_DEPTH} levels")


def loads_json_strict(text: str) -> Any:
    """Load precisely one JSON value without BOM, duplicate keys, non-finites, or a nesting deeper than MAX_JSON_DEPTH."""
    if text.startswith("\ufeff"):
        raise StrictJsonError("UTF-8 BOM is not permitted")
    _check_json_depth(text)
    try:
        value = json.loads(text, parse_constant=_reject_constant, parse_float=_reject_float, object_pairs_hook=_reject_duplicates)
        if _SURROGATE_HINT.search(text):
            _reject_surrogates(value)
        return value
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise StrictJsonError(str(error)) from error
    except (ValueError, RecursionError) as error:  # an integer past the interpreter's digit limit; a nesting past its recursion limit
        raise StrictJsonError(f"{type(error).__name__}: {error}") from error


def load_json_strict(path: Path) -> Any:
    """Read strict UTF-8 JSON from *path*."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise StrictJsonError(f"JSON unreadable: {error}") from error
    return _loads_json_bytes_strict(raw)


def _loads_json_bytes_strict(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise StrictJsonError("UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StrictJsonError(f"JSON is not UTF-8: {error}") from error
    return loads_json_strict(text)


def _exact_json_text(value: Any) -> str | None:
    """The compact JSON text of *value* if it is made of exact JSON types only (never coerce Python tuples or keys), else None."""
    def exact(item: Any) -> bool:
        if type(item) is dict:
            return all(type(key) is str and exact(child) for key, child in item.items())
        if type(item) is list:
            return all(exact(child) for child in item)
        return type(item) in {str, int, bool, type(None)} or type(item) is float and math.isfinite(item)

    try:
        if not exact(value):
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (ValueError, RecursionError):
        return None


def _json_validation_key(value: Any) -> str | None:
    """Cache only bounded, exact JSON types: the JSON text of *value* when it is at most 64 KB (the callers parse it back), else None."""
    serialized = _exact_json_text(value)
    return serialized if serialized is not None and len(serialized) <= 65536 else None


def _instance_cache_key(value: Any) -> str | None:
    """The key under which the errors of *value* are remembered in this process: its JSON text up to 64 KB, above that the digest of that text (a JSON
    text never begins with "sha256:", so the two cannot meet).  None for what is not exact JSON: it is validated every time, as before.  Nothing of where
    the instance came from (a run, a path) is in the key: the same bytes are the same instance."""
    serialized = _exact_json_text(value)
    if serialized is None:
        return None
    return serialized if len(serialized) <= 65536 else "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def classify_version(instance: Any) -> dict[str, str]:
    """Classify artifacts without silently treating the incompatible V2.1 as V3."""
    if isinstance(instance, dict) and instance.get("schema_version") == "2.1.0":
        return {
            "code": "V2_1_BREAKING_CHANGE",
            "message": "schema version 2.1.0 is incompatible with canonical model 3.0.0",
        }
    return {"code": "V3_CANONICAL", "message": "canonical model 3.0.0"}


def _schema_directory(root: Path) -> Path:
    return (root.resolve() / "schemas").resolve()


def _checked_schema_path(schema_path: Path, root: Path) -> Path:
    schemas = _schema_directory(root)
    candidate = (root.resolve() / schema_path).resolve() if not schema_path.is_absolute() else schema_path.resolve()
    if candidate.parent != schemas or candidate.suffix != ".json":
        raise SchemaRegistryError("schema must be a repository-local schema under schemas/")
    if not candidate.is_file():
        raise SchemaRegistryError(f"repository-local schema does not exist: {candidate.name}")
    return candidate


_SNAPSHOTS: dict[tuple[str, tuple[tuple[str, int, int], ...]], tuple[tuple[str, bytes], ...]] = {}
_SNAPSHOTS_LIMIT = 8  # directories of schemas: the package's own and a few copies in tests
_SNAPSHOT_LOCK = threading.Lock()


def _read_schema_snapshot(schemas: Path) -> tuple[tuple[str, bytes], ...]:
    snapshot: list[tuple[str, bytes]] = []
    for path in sorted(schemas.glob("*.json"), key=lambda item: item.name):
        try:
            snapshot.append((path.name, path.read_bytes()))
        except OSError as error:
            raise StrictJsonError(f"JSON unreadable: {error}") from error
    return tuple(snapshot)


def _schema_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    """The schemas of ``root/schemas`` as (name, bytes), read once per process.  The key is the directory and the name, size and modification time of
    every ``*.json`` in it, so a changed, added or removed file is read again; the same tuple object comes back every time, which is what makes the
    caches keyed by it cheap to look up (bytes remember their hash).  Files of the package, never of a run."""
    schemas = _schema_directory(root)
    try:
        with os.scandir(schemas) as entries:
            stamp = []
            for entry in entries:
                if fnmatch.fnmatch(entry.name, "*.json"):
                    details = entry.stat()
                    stamp.append((entry.name, details.st_size, details.st_mtime_ns))
    except OSError:
        return _read_schema_snapshot(schemas)  # what cannot be listed or stat'ed is not remembered: read as before, fail as before
    key = (str(schemas), tuple(sorted(stamp)))
    found = _SNAPSHOTS.get(key)
    if found is not None:
        return found
    snapshot = _read_schema_snapshot(schemas)
    with _SNAPSHOT_LOCK:
        while len(_SNAPSHOTS) >= _SNAPSHOTS_LIMIT:
            _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
        _SNAPSHOTS[key] = snapshot
    return snapshot


@lru_cache(maxsize=64)
def _compiled_validator(schema_name: str, snapshot: tuple[tuple[str, bytes], ...]) -> Draft202012Validator:
    registry: dict[str, dict[str, Any]] = {}
    schema: dict[str, Any] | None = None
    for name, raw in snapshot:
        contents = _loads_json_bytes_strict(raw)
        if not isinstance(contents, dict):
            raise SchemaRegistryError(f"repository-local schema is not an object: {name}")
        identifier = contents.get("$id")
        if not isinstance(identifier, str) or not identifier:
            raise SchemaRegistryError(f"repository-local schema has no $id: {name}")
        registry[identifier] = contents
        if name == schema_name:
            schema = contents
    if schema is None:
        raise SchemaRegistryError(f"repository-local schema does not exist: {schema_name}")
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def validator_for(schema_path: Path, root: Path) -> Draft202012Validator:
    """Build a validator whose registry contains only ``root/schemas/*.json``."""
    checked = _checked_schema_path(schema_path, root)
    return _compiled_validator(checked.name, _schema_snapshot(root))


def _pointer(parts: Iterable[object]) -> str:
    encoded = "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return f"/{encoded}" if encoded else ""


def _error_path(error: Any) -> str:
    path = list(error.absolute_path)
    if error.validator == "required" and isinstance(error.instance, dict):
        match = re.match(r"^'([^']+)' is a required property$", error.message)
        if match:
            path.append(match.group(1))
    elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extras = sorted(set(error.instance) - set(error.schema.get("properties", {})))
        if extras:
            path.append(extras[0])
    return _pointer(path)


def _error_code(error: Any) -> str:
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", str(error.validator)).replace("-", "_")
    return "SCHEMA_" + name.upper()


_ERRORS: dict[tuple[str, tuple[tuple[str, bytes], ...], str], tuple[tuple[str, str, str], ...]] = {}
_ERRORS_LIMIT = 64
_ERRORS_LOCK = threading.Lock()


def _cached_schema_errors(schema_name: str, snapshot: tuple[tuple[str, bytes], ...], key: str, instance: Any) -> tuple[tuple[str, str, str], ...]:
    """The errors of one exact JSON instance under one snapshot of the schemas, found once per process.  The instance itself is validated (the key was
    made from it, so it is plain JSON, and the engine reads it and never changes it).  The cache key is the schemas as read, the schema name and the
    instance key, compared by content: a changed schema can never be answered with the errors of the old one."""
    cache_key = (schema_name, snapshot, key)
    found = _ERRORS.get(cache_key)
    if found is None:
        validator = _compiled_validator(schema_name, snapshot)
        found = tuple((_error_path(error), _error_code(error), error.message) for error in validator.iter_errors(instance))
        with _ERRORS_LOCK:
            while len(_ERRORS) >= _ERRORS_LIMIT:
                _ERRORS.pop(next(iter(_ERRORS)))
            _ERRORS[cache_key] = found
    return found


# V3 precedence of the categories of a case: the order the canon keeps them in (the code puts it)
CATEGORY_PRECEDENCE = ("positive", "negative", "boundary", "authorization", "security", "concurrency", "idempotency", "observability", "functional")


def _semantic_error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _display_order_diagnostics(items: Any, path: list[object]) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    diagnostics: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if isinstance(item, dict) and (type(item.get("display_order")) is not int or item["display_order"] != index + 1):
            diagnostics.append(_semantic_error(
                _pointer([*path, index, "display_order"]),
                "CANONICAL_DISPLAY_ORDER",
                f"display_order must be {index + 1} to preserve physical array order",
            ))
    return diagnostics


def _sorted_field_diagnostics(items: Any, path: list[object], field: str, code: str) -> list[dict[str, str]]:
    if not isinstance(items, list) or not all(isinstance(item, dict) and isinstance(item.get(field), str) for item in items):
        return []
    actual = [item[field] for item in items]
    if actual == sorted(actual):
        return []
    return [_semantic_error(_pointer(path), code, f"{field} values must use ascending Unicode code-point order")]


def _nonfinite_number_diagnostics(value: Any, path: list[object]) -> list[dict[str, str]]:
    if isinstance(value, float):
        return [] if math.isfinite(value) else [_semantic_error(
            _pointer(path), "CANONICAL_NONFINITE_NUMBER", "numbers must be finite in canonical documents",
        )]
    if isinstance(value, dict):
        return [diagnostic for key, item in value.items() for diagnostic in _nonfinite_number_diagnostics(item, [*path, key])]
    if isinstance(value, list):
        return [diagnostic for index, item in enumerate(value) for diagnostic in _nonfinite_number_diagnostics(item, [*path, index])]
    return []


def _canonical_semantic_diagnostics(instance: Any) -> list[dict[str, str]]:
    """Checks canonical array order and conditional ownership beyond JSON Schema."""
    if not isinstance(instance, dict):
        return []
    diagnostics: list[dict[str, str]] = []
    if "revision" in instance and type(instance["revision"]) is not int:
        diagnostics.append(_semantic_error("/revision", "CANONICAL_REVISION_TYPE", "revision must be an exact Python int"))
    document_id = instance.get("document_id")
    if isinstance(document_id, str) and re.fullmatch(r"TCDOC-[a-z0-9](?:[a-z0-9_.-]*[a-z0-9_-])?", document_id) is None:
        diagnostics.append(_semantic_error("/document_id", "CANONICAL_DOCUMENT_ID", "document_id must fully match the canonical safe identifier grammar"))
    diagnostics.extend(_nonfinite_number_diagnostics(instance, []))
    diagnostics.extend(_sorted_field_diagnostics(instance.get("operation_capabilities"), ["operation_capabilities"], "capability_id", "CANONICAL_CAPABILITY_ORDER"))
    diagnostics.extend(_display_order_diagnostics(instance.get("requirements"), ["requirements"]))
    diagnostics.extend(_display_order_diagnostics(instance.get("test_cases"), ["test_cases"]))
    rank = {category: index for index, category in enumerate(CATEGORY_PRECEDENCE)}

    for capability_index, capability in enumerate(instance.get("operation_capabilities", [])):
        if not isinstance(capability, dict):
            continue
        diagnostics.extend(_sorted_field_diagnostics(capability.get("arguments"), ["operation_capabilities", capability_index, "arguments"], "name", "CANONICAL_ARGUMENT_ORDER"))
        diagnostics.extend(_sorted_field_diagnostics(capability.get("results"), ["operation_capabilities", capability_index, "results"], "name", "CANONICAL_RESULT_ORDER"))

    for case_index, case in enumerate(instance.get("test_cases", [])):
        if not isinstance(case, dict):
            continue
        categories = case.get("categories")
        if isinstance(categories, list) and all(category in rank for category in categories):
            if categories != sorted(categories, key=rank.__getitem__):
                diagnostics.append(_semantic_error(_pointer(["test_cases", case_index, "categories"]), "CANONICAL_CATEGORY_ORDER", "categories must follow V3 precedence"))
        steps = case.get("steps")
        diagnostics.extend(_display_order_diagnostics(steps, ["test_cases", case_index, "steps"]))
        for step_index, step in enumerate(steps if isinstance(steps, list) else []):
            if not isinstance(step, dict):
                continue
            step_path = ["test_cases", case_index, "steps", step_index]
            diagnostics.extend(_display_order_diagnostics(step.get("inputs"), [*step_path, "inputs"]))
            diagnostics.extend(_display_order_diagnostics(step.get("outputs"), [*step_path, "outputs"]))
            expectations = step.get("expectations")
            diagnostics.extend(_display_order_diagnostics(expectations, [*step_path, "expectations"]))
            for expectation_index, expectation in enumerate(expectations if isinstance(expectations, list) else []):
                if isinstance(expectation, dict):
                    diagnostics.extend(_display_order_diagnostics(expectation.get("assertions"), [*step_path, "expectations", expectation_index, "assertions"]))
    return diagnostics


def schema_diagnostics(instance: Any, schema_path: Path, root: Path) -> list[dict[str, str]]:
    """Return deterministic RFC 6901 schema diagnostics without remote resolution."""
    canonical = schema_path.resolve().name == "canonical-test-document.schema.json"
    semantic_diagnostics = _canonical_semantic_diagnostics(instance) if canonical else []
    precondition_codes = {
        "CANONICAL_DOCUMENT_ID",
        "CANONICAL_DISPLAY_ORDER",
        "CANONICAL_NONFINITE_NUMBER",
        "CANONICAL_REVISION_TYPE",
    }
    precondition_diagnostics = [
        diagnostic for diagnostic in semantic_diagnostics if diagnostic["code"] in precondition_codes
    ]
    if precondition_diagnostics:
        return sorted(precondition_diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))
    checked = _checked_schema_path(schema_path, root)
    snapshot = _schema_snapshot(root)
    key = _instance_cache_key(instance)
    if key is None:
        errors = tuple((_error_path(error), _error_code(error), error.message) for error in _compiled_validator(checked.name, snapshot).iter_errors(instance))
    else:
        errors = _cached_schema_errors(checked.name, snapshot, key, instance)
    diagnostics = [
        {"path": path, "code": code, "message": message}
        for path, code, message in errors
    ]
    if not diagnostics and canonical:
        diagnostics.extend(semantic_diagnostics)
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))
