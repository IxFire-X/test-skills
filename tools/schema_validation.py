"""Strict JSON loading and repository-local Draft 2020-12 validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class StrictJsonError(ValueError):
    """Raised when JSON bytes are not one strict, complete JSON value."""


class SchemaRegistryError(ValueError):
    """Raised when a schema is not a repository-local schema resource."""


def _reject_constant(token: str) -> None:
    raise StrictJsonError(f"non-finite JSON constant: {token}")


def _reject_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate object key: {key}")
        result[key] = value
    return result


def loads_json_strict(text: str) -> Any:
    """Load precisely one JSON value without BOM, duplicate keys, or non-finites."""
    if text.startswith("\ufeff"):
        raise StrictJsonError("UTF-8 BOM is not permitted")
    try:
        return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, TypeError) as error:
        raise StrictJsonError(str(error)) from error


def load_json_strict(path: Path) -> Any:
    """Read strict UTF-8 JSON from *path*."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise StrictJsonError(f"JSON unreadable: {error}") from error
    if raw.startswith(b"\xef\xbb\xbf"):
        raise StrictJsonError("UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise StrictJsonError(f"JSON is not UTF-8: {error}") from error
    return loads_json_strict(text)


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


def _local_registry(root: Path) -> Registry:
    schemas = _schema_directory(root)
    registry = Registry()
    for path in sorted(schemas.glob("*.json"), key=lambda item: item.name):
        contents = load_json_strict(path)
        if not isinstance(contents, dict):
            raise SchemaRegistryError(f"repository-local schema is not an object: {path.name}")
        resource = Resource.from_contents(contents)
        identifier = contents.get("$id")
        if not isinstance(identifier, str) or not identifier:
            raise SchemaRegistryError(f"repository-local schema has no $id: {path.name}")
        registry = registry.with_resource(identifier, resource)
    return registry


def validator_for(schema_path: Path, root: Path) -> Draft202012Validator:
    """Build a validator whose registry contains only ``root/schemas/*.json``."""
    checked = _checked_schema_path(schema_path, root)
    schema = load_json_strict(checked)
    if not isinstance(schema, dict):
        raise SchemaRegistryError("repository-local schema is not an object")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=_local_registry(root))


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
    precedence = ["positive", "negative", "boundary", "authorization", "security", "concurrency", "idempotency", "observability", "functional"]
    rank = {category: index for index, category in enumerate(precedence)}

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
    errors = sorted(
        validator_for(schema_path, root).iter_errors(instance),
        key=lambda error: (_error_path(error), _error_code(error), error.message),
    )
    diagnostics = [
        {"path": _error_path(error), "code": _error_code(error), "message": error.message}
        for error in errors
    ]
    if not diagnostics and canonical:
        diagnostics.extend(semantic_diagnostics)
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))
