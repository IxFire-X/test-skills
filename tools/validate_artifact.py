#!/usr/bin/env python3
"""Validate a JSON artifact against a Draft 2020-12 JSON Schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import SchemaError
except ImportError as error:  # pragma: no cover
    SchemaError = Exception
    _IMPORT_ERROR = error
else:
    _IMPORT_ERROR = None

try:
    if __package__:
        from .schema_validation import SchemaRegistryError, StrictJsonError, classify_version, load_json_strict, schema_diagnostics, validator_for
    else:  # direct CLI execution
        from schema_validation import SchemaRegistryError, StrictJsonError, classify_version, load_json_strict, schema_diagnostics, validator_for
except ImportError as error:  # pragma: no cover
    SchemaRegistryError = StrictJsonError = Exception
    _SCHEMA_VALIDATION_IMPORT_ERROR = error
else:
    _SCHEMA_VALIDATION_IMPORT_ERROR = None


def _report_error(message: str) -> tuple[int, dict[str, Any]]:
    return 2, {"status": "error", "errors": [{"path": "", "message": message}]}


def _root_for_schema(schema_path: Path) -> Path:
    return schema_path.resolve().parent.parent


def validate(schema_path: str, artifact_path: str) -> tuple[int, dict[str, Any]]:
    """Validate one artifact; cross-artifact execution evidence belongs to V3 workflow tools."""
    if _IMPORT_ERROR is not None:
        return _report_error(f"missing dependency: {_IMPORT_ERROR}")
    if _SCHEMA_VALIDATION_IMPORT_ERROR is not None:
        return _report_error(f"schema validation unavailable: {_SCHEMA_VALIDATION_IMPORT_ERROR}")
    schema_file = Path(schema_path)
    root = _root_for_schema(schema_file)
    try:
        validator_for(schema_file, root)
    except (SchemaRegistryError, StrictJsonError, SchemaError) as error:
        return _report_error(f"schema unreadable: {error}")
    try:
        artifact = load_json_strict(Path(artifact_path))
    except StrictJsonError as error:
        return _report_error(f"artifact unreadable: {error}")
    try:
        schema = load_json_strict(schema_file)
    except StrictJsonError as error:
        return _report_error(f"schema unreadable: {error}")
    if (
        isinstance(schema, dict)
        and schema.get("properties", {}).get("schema_version", {}).get("const") in {"3.0.0", "4.0.0"}
        and classify_version(artifact)["code"] == "V2_1_BREAKING_CHANGE"
    ):
        return 1, {
            "status": "invalid",
            "errors": [{
                "path": "/schema_version",
                "code": "V2_1_BREAKING_CHANGE",
                "message": "schema version 2.1.0 is incompatible with canonical model 3.0.0",
            }],
        }
    try:
        errors = schema_diagnostics(artifact, schema_file, root)
    except Exception as error:  # noqa: BLE001  # pragma: no cover
        return _report_error(f"runtime error: {error}")
    if errors:
        return 1, {"status": "invalid", "errors": errors}
    return 0, {"status": "valid", "errors": []}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit({"status": "error", "errors": [{"path": "", "message": f"argument error: {message}"}]})
        raise SystemExit(2)


def _emit(report: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import json

    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument("schema_path")
    parser.add_argument("artifact_path")
    args = parser.parse_args()
    exit_code, report = validate(args.schema_path, args.artifact_path)
    _emit(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
