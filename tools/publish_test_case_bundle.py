"""Publish immutable canonical JSON, human-preview, and Zephyr CSV revision bundles."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.canonical_document import CanonicalDocumentError, canonical_bytes, require_valid_canonical_document
from tools.json_cli import JsonArgumentParser
from tools.schema_validation import StrictJsonError, classify_version, load_json_strict
from tools.test_case_projections import (
    _reject_unknown_profile as _reject_projection_profile,
    _render_html_preview_validated,
    _render_markdown_validated,
    _render_zephyr_csv_validated,
)


_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE = _PROFILE_V4
_ORDER = ("json", "preview", "csv")
_MESSAGES = {
    "OUTPUT_DIRECTORY_ERROR": "Output directory is unavailable.",
    "HARDLINK_UNAVAILABLE": "Atomic hard-link publication is unavailable.",
    "TEMPORARY_WRITE_FAILED": "Temporary bundle payload could not be written.",
    "TARGET_INSTALL_FAILED": "Bundle target could not be installed.",
    "TARGET_READ_FAILED": "Bundle target could not be read.",
    "INPUT_JSON_UNREADABLE": "Input must be strict UTF-8 canonical JSON.",
}


@dataclass(frozen=True)
class BundlePayloads:
    json_bytes: bytes
    preview_bytes: bytes
    csv_bytes: bytes
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Receipt:
    document_id: str
    revision: int
    csv_profile: str
    json_path: str
    preview_path: str
    csv_path: str
    document_sha256: str
    preview_sha256: str
    csv_sha256: str


class BundleMismatchError(ValueError):
    """One or more bundle targets are not precisely the expected payload."""

    def __init__(self, mismatches: tuple[str, ...] | list[str]) -> None:
        self._mismatches = tuple(mismatches)
        super().__init__("bundle targets do not match expected payloads: " + ", ".join(self._mismatches))

    @property
    def mismatches(self) -> tuple[str, ...]:
        return self._mismatches


class BundlePublicationError(OSError):
    """A filesystem primitive failed without exposing filesystem details."""

    def __init__(self, code: str) -> None:
        self._code = code
        self._safe_message = _MESSAGES[code]
        super().__init__(self._safe_message)

    @property
    def code(self) -> str:
        return self._code

    @property
    def safe_message(self) -> str:
        return self._safe_message


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _reject_unknown_profile(csv_profile: str, *, allow_historical: bool = False) -> None:
    _reject_projection_profile(csv_profile)
    if not allow_historical and csv_profile != _PROFILE_V4:
        raise ValueError("historical Zephyr profiles are verification-only and require an explicit receipt profile")


def _validate_once(document: dict[str, Any], csv_profile: str, *, allow_historical: bool = False) -> None:
    _reject_unknown_profile(csv_profile, allow_historical=allow_historical)
    version = classify_version(document)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        raise CanonicalDocumentError(({
            "path": "/schema_version",
            "code": "V2_1_BREAKING_CHANGE",
            "message": version["message"],
        },))
    require_valid_canonical_document(document)


def build_bundle(document: dict[str, Any], csv_profile: str = _PROFILE, *, _allow_historical: bool = False) -> BundlePayloads:
    """Build all exact bytes after one canonical validation pass."""
    _validate_once(document, csv_profile, allow_historical=_allow_historical)
    preview = _render_html_preview_validated(document, csv_profile) if csv_profile == _PROFILE_V4 else _render_markdown_validated(document, csv_profile)
    csv = _render_zephyr_csv_validated(document, csv_profile)
    return BundlePayloads(canonical_bytes(document), preview.payload, csv.payload, preview.warnings + csv.warnings)


def _resolved_output_directory(output_dir: str | os.PathLike[str], create: bool) -> Path:
    try:
        directory = Path(output_dir).resolve()
        if directory.exists():
            if not directory.is_dir():
                raise OSError
        elif create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory
    except OSError:
        raise BundlePublicationError("OUTPUT_DIRECTORY_ERROR") from None


def _targets(document: dict[str, Any], directory: Path, csv_profile: str = _PROFILE) -> tuple[tuple[str, Path], ...]:
    prefix = f"{document['document_id']}.r{document['revision']}"
    return (
        ("json", directory / f"{prefix}.json"),
        ("preview", directory / f"{prefix}.{'html' if csv_profile == _PROFILE_V4 else 'md'}"),
        ("csv", directory / f"{prefix}.zephyr-scale.csv"),
    )


def _payloads(bundle: BundlePayloads) -> tuple[tuple[str, bytes], ...]:
    return tuple(zip(_ORDER, (bundle.json_bytes, bundle.preview_bytes, bundle.csv_bytes)))


def _preflight(targets: tuple[tuple[str, Path], ...], bundle: BundlePayloads) -> tuple[str, ...]:
    missing: list[str] = []
    mismatches: list[str] = []
    expected = dict(_payloads(bundle))
    for label, target in targets:
        try:
            if not os.path.lexists(target):
                missing.append(label)
            elif not target.is_file() or target.read_bytes() != expected[label]:
                mismatches.append(label)
        except OSError:
            mismatches.append(label)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return tuple(missing)


def _unlink_current(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _probe_hardlink(directory: Path) -> None:
    source: Path | None = None
    destination: Path | None = None
    failed_cleanup = False
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as temporary:
            source = Path(temporary.name)
            temporary.write(b"probe")
            temporary.flush()
        destination = directory / f".bundle-link-probe-{uuid.uuid4().hex}"
        os.link(source, destination)
    except OSError:
        raise BundlePublicationError("HARDLINK_UNAVAILABLE") from None
    finally:
        if destination is not None:
            failed_cleanup = not _unlink_current(destination) or failed_cleanup
        if source is not None:
            failed_cleanup = not _unlink_current(source) or failed_cleanup
        if failed_cleanup:
            raise BundlePublicationError("HARDLINK_UNAVAILABLE") from None


def _write_temps(directory: Path, missing: tuple[str, ...], bundle: BundlePayloads) -> dict[str, Path]:
    expected = dict(_payloads(bundle))
    temporary_paths: dict[str, Path] = {}
    try:
        for label in missing:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as temporary:
                temporary_paths[label] = Path(temporary.name)
                temporary.write(expected[label])
                temporary.flush()
        return temporary_paths
    except OSError:
        for path in temporary_paths.values():
            _unlink_current(path)
        raise BundlePublicationError("TEMPORARY_WRITE_FAILED") from None


def _read_final(targets: tuple[tuple[str, Path], ...], bundle: BundlePayloads) -> tuple[bytes, bytes, bytes]:
    expected = dict(_payloads(bundle))
    values: list[bytes] = []
    mismatches: list[str] = []
    for label, target in targets:
        try:
            payload = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            mismatches.append(label)
            continue
        except OSError:
            raise BundlePublicationError("TARGET_READ_FAILED") from None
        if payload != expected[label]:
            mismatches.append(label)
        values.append(payload)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return tuple(values)  # type: ignore[return-value]


def _receipt(document: dict[str, Any], csv_profile: str, targets: tuple[tuple[str, Path], ...], payloads: tuple[bytes, bytes, bytes]) -> Receipt:
    return Receipt(
        document_id=document["document_id"], revision=document["revision"], csv_profile=csv_profile,
        json_path=str(targets[0][1]), preview_path=str(targets[1][1]), csv_path=str(targets[2][1]),
        document_sha256=_digest(payloads[0]), preview_sha256=_digest(payloads[1]), csv_sha256=_digest(payloads[2]),
    )


def publish_bundle(document: dict[str, Any], output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE) -> Receipt:
    """Recoverably publish a bundle by hard-linking complete local temporary files."""
    bundle = build_bundle(document, csv_profile)
    directory = _resolved_output_directory(output_dir, create=True)
    targets = _targets(document, directory, csv_profile)
    missing = _preflight(targets, bundle)
    if not missing:
        return _receipt(document, csv_profile, targets, _read_final(targets, bundle))
    _probe_hardlink(directory)
    temporary_paths = _write_temps(directory, missing, bundle)
    expected = dict(_payloads(bundle))
    try:
        for label, target in targets:
            if label not in temporary_paths:
                continue
            temporary = temporary_paths[label]
            try:
                os.link(temporary, target)
            except FileExistsError:
                try:
                    installed = target.read_bytes()
                except OSError:
                    raise BundleMismatchError((label,)) from None
                if installed != expected[label]:
                    raise BundleMismatchError((label,))
            except OSError:
                raise BundlePublicationError("TARGET_INSTALL_FAILED") from None
            else:
                if not _unlink_current(temporary):
                    raise BundlePublicationError("TARGET_INSTALL_FAILED")
                temporary_paths.pop(label, None)
        return _receipt(document, csv_profile, targets, _read_final(targets, bundle))
    finally:
        cleanup_failed = False
        for temporary in temporary_paths.values():
            cleanup_failed = not _unlink_current(temporary) or cleanup_failed
        if cleanup_failed and sys.exc_info()[0] is None:
            raise BundlePublicationError("TARGET_INSTALL_FAILED") from None


def verify_bundle(document: dict[str, Any], output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE) -> Receipt:
    """Read and verify the immutable bundle without creating or modifying anything."""
    bundle = build_bundle(document, csv_profile, _allow_historical=True)
    directory = _resolved_output_directory(output_dir, create=False)
    targets = _targets(document, directory, csv_profile)
    expected = dict(_payloads(bundle))
    values: list[bytes] = []
    mismatches: list[str] = []
    for label, target in targets:
        try:
            if not target.is_file():
                mismatches.append(label)
                continue
            payload = target.read_bytes()
        except OSError:
            mismatches.append(label)
            continue
        if payload != expected[label]:
            mismatches.append(label)
        values.append(payload)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return _receipt(document, csv_profile, targets, tuple(values))  # type: ignore[arg-type]


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _invalid_payload(error: CanonicalDocumentError) -> dict[str, Any]:
    errors = []
    for diagnostic in error.diagnostics:
        message = diagnostic["message"] if diagnostic["code"] == "V2_1_BREAKING_CHANGE" else "Canonical document is invalid."
        errors.append({"path": diagnostic["path"], "code": diagnostic["code"], "message": message})
    return {"status": "invalid", "errors": errors}


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--csv-profile", default=_PROFILE)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the publisher CLI and emit exactly one compact JSON response."""
    args = _parser().parse_args(argv)
    try:
        _reject_unknown_profile(args.csv_profile, allow_historical=args.verify_only)
    except ValueError:
        _emit({"status": "error", "errors": [{"path": "", "code": "TARGET_INSTALL_FAILED", "message": _MESSAGES["TARGET_INSTALL_FAILED"]}]})
        return 2
    try:
        document = load_json_strict(args.input)
    except StrictJsonError:
        _emit({"status": "error", "errors": [{"path": "", "code": "INPUT_JSON_UNREADABLE", "message": _MESSAGES["INPUT_JSON_UNREADABLE"]}]})
        return 2
    try:
        receipt = verify_bundle(document, args.output_dir, args.csv_profile) if args.verify_only else publish_bundle(document, args.output_dir, args.csv_profile)
    except CanonicalDocumentError as error:
        _emit(_invalid_payload(error))
        return 1
    except BundleMismatchError as error:
        _emit({"status": "mismatch", "mismatches": list(error.mismatches)})
        return 1
    except BundlePublicationError as error:
        _emit({"status": "error", "errors": [{"path": "", "code": error.code, "message": error.safe_message}]})
        return 2
    except (OSError, ValueError):
        _emit({"status": "error", "errors": [{"path": "", "code": "TARGET_INSTALL_FAILED", "message": _MESSAGES["TARGET_INSTALL_FAILED"]}]})
        return 2
    _emit({field: getattr(receipt, field) for field in Receipt.__dataclass_fields__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
