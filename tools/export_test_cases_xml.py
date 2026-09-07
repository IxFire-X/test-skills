"""Write a deterministic, opt-in Zephyr XML candidate from canonical JSON."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from tools.canonical_document import CanonicalDocumentError
from tools.json_cli import JsonArgumentParser
from tools.schema_validation import StrictJsonError, load_json_strict
from tools.test_case_projections import _XML_PROFILE, render_zephyr_xml


def _receipt(status: str, output: Path, profile: str, payload: bytes, warnings: tuple[str, ...], errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(output),
        "profile": profile,
        "profile_status": "observed_unverified",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "status": status,
        "warnings": list(warnings),
    }
    if errors:
        result["errors"] = errors
    return result


def _emit(receipt: dict[str, Any]) -> None:
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _existing_status(output: Path, payload: bytes, verify_only: bool, profile: str, warnings: tuple[str, ...]) -> int | None:
    try:
        exists = os.path.lexists(output)
        existing = output.read_bytes() if exists else None
    except OSError:
        _emit(_receipt("error", output, profile, payload, warnings, [{"path": "output", "message": "target could not be read"}]))
        return 2
    if existing is None:
        if verify_only:
            _emit(_receipt("missing", output, profile, payload, warnings, [{"path": "output", "message": "target does not exist"}]))
            return 1
        return None
    if existing == payload:
        _emit(_receipt("verified" if verify_only else "idempotent", output, profile, payload, warnings))
        return 0
    _emit(_receipt("mismatch", output, profile, payload, warnings, [{"path": "output", "message": "target bytes differ"}]))
    return 1


def _install_complete_file(output: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=output.parent, prefix=".zephyr-xml-") as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
        os.link(temporary, output)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", default=_XML_PROFILE)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = load_json_strict(args.input)
        projection = render_zephyr_xml(document, args.profile)
    except (CanonicalDocumentError, StrictJsonError, ValueError):
        _emit({"errors":[{"message":"invalid canonical JSON or XML profile","path":"input"}],"status":"error"})
        return 2
    existing = _existing_status(args.output, projection.payload, args.verify_only, args.profile, projection.warnings)
    if existing is not None:
        return existing
    try:
        _install_complete_file(args.output, projection.payload)
    except FileExistsError:
        existing = _existing_status(args.output, projection.payload, False, args.profile, projection.warnings)
        if existing is not None:
            return existing
        _emit(_receipt("error", args.output, args.profile, projection.payload, projection.warnings, [{"path":"output","message":"target changed during publication"}]))
        return 2
    except OSError:
        _emit(_receipt("error", args.output, args.profile, projection.payload, projection.warnings, [{"path":"output","message":"target could not be created"}]))
        return 2
    _emit(_receipt("published", args.output, args.profile, projection.payload, projection.warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
