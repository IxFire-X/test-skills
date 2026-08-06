#!/usr/bin/env python3
"""Validate a JSON artifact against a Draft 2020-12 JSON Schema."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, SchemaError
except ImportError as error:  # pragma: no cover
    Draft202012Validator = None
    SchemaError = Exception
    _IMPORT_ERROR = error
else:
    _IMPORT_ERROR = None


def _pointer(parts: Iterable[object]) -> str:
    encoded = "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return f"/{encoded}" if encoded else ""


def _error_path(error: Any) -> str:
    path = list(error.absolute_path)
    if error.validator == "required":
        path.append(error.message.removeprefix("'").split("'", 1)[0])
    elif error.validator == "additionalProperties":
        path.append(error.message.removeprefix("Additional properties are not allowed ('").split("'", 1)[0])
    return _pointer(path)


def _report_error(message: str) -> tuple[int, dict[str, Any]]:
    return 2, {"status": "error", "errors": [{"path": "", "message": message}]}


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(schema_path: str, artifact_path: str) -> tuple[int, dict[str, Any]]:
    """Return (fixed exit code, deterministic report) for a schema/artifact pair."""
    if _IMPORT_ERROR is not None:
        return _report_error(f"missing dependency: {_IMPORT_ERROR}")
    try:
        schema = _read_json(schema_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _report_error(f"schema unreadable: {error}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return _report_error(f"schema invalid: {error}")
    try:
        artifact = _read_json(artifact_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _report_error(f"artifact unreadable: {error}")
    try:
        errors = sorted(Draft202012Validator(schema).iter_errors(artifact), key=lambda error: (_error_path(error), error.message))
    except Exception as error:  # noqa: BLE001  # pragma: no cover
        return _report_error(f"runtime error: {error}")
    if errors:
        return 1, {"status": "invalid", "errors": [{"path": _error_path(error), "message": error.message} for error in errors]}
    return 0, {"status": "valid", "errors": []}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit({"status": "error", "errors": [{"path": "", "message": f"argument error: {message}"}]})
        raise SystemExit(2)


def _emit(report: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
