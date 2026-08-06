#!/usr/bin/env python3
"""Validate a JSON artifact against a Draft 2020-12 JSON Schema."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, SchemaError
except ImportError as error:  # pragma: no cover - depends on installation
    Draft202012Validator = None
    SchemaError = Exception
    _IMPORT_ERROR = error
else:
    _IMPORT_ERROR = None


def _pointer(parts: Iterable[object]) -> str:
    """Return an RFC-6901 JSON Pointer for an error path."""
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _error_path(error: Any) -> str:
    """Point at the implicated property when jsonschema exposes its name."""
    path = list(error.absolute_path)
    if error.validator == "required":
        missing = error.message.removeprefix("'").split("'", 1)[0]
        if missing:
            path.append(missing)
    elif error.validator == "additionalProperties":
        unexpected = error.message.removeprefix("Additional properties are not allowed ('").split("'", 1)[0]
        if unexpected:
            path.append(unexpected)
    return _pointer(path)


def _report_error(message: str) -> tuple[int, dict[str, Any]]:
    return 2, {"status": "error", "errors": [{"path": "", "message": message}]}


def validate(schema_path: str, artifact_path: str) -> tuple[int, dict[str, Any]]:
    """Return (exit_code, deterministic report) for a schema/artifact pair."""
    if _IMPORT_ERROR is not None:
        return _report_error(f"missing dependency: {_IMPORT_ERROR}")
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return _report_error(f"input unreadable: {error}")
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(artifact), key=lambda error: (_error_path(error), error.message))
    except (SchemaError, TypeError, ValueError) as error:
        return _report_error(f"schema invalid: {error}")
    except Exception as error:  # noqa: BLE001  # pragma: no cover - defensive CLI boundary
        return _report_error(f"runtime error: {error}")
    if errors:
        return 1, {"status": "invalid", "errors": [{"path": _error_path(error), "message": error.message} for error in errors]}
    return 0, {"status": "valid", "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema_path")
    parser.add_argument("artifact_path")
    args = parser.parse_args()
    exit_code, report = validate(args.schema_path, args.artifact_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
