"""Safe, canonical, create-only storage for Pipeline 6 flow artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class FlowError(ValueError):
    """A closed, safe diagnostic that never interpolates untrusted input."""

    def __init__(self, code: str, path: str, message: str):
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code}: {path}: {message}")


@dataclass(frozen=True)
class StoredArtifact:
    path: Path
    payload: bytes
    sha256: str


_FORBIDDEN_KEY = re.compile(r"(?:raw_source|source_code|raw_diff|^diff$|prompt|environment|secret|token|password|credential|^raw$)", re.IGNORECASE)
_FORBIDDEN_TEXT = re.compile(r"(?:secret|token|password|credential)", re.IGNORECASE)


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    """Return compact, deterministic UTF-8 JSON with no transport markers."""
    try:
        return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise FlowError("FLOW_SAFE_TEXT", "", "Artifact must be finite JSON data.") from None


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def artifact_sha256(value: Mapping[str, Any]) -> str:
    """Return the content-addressed identity of canonical artifact bytes."""
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _safe(value: Any, pointer: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _FORBIDDEN_KEY.search(key):
                raise FlowError("FLOW_SAFE_TEXT", pointer, "Artifact contains a prohibited durable field.")
            _safe(item, f"{pointer}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _safe(item, f"{pointer}/{index}")
    elif isinstance(value, str):
        if len(value) > 512 or "\x00" in value or "\r" in value or "\n" in value or _FORBIDDEN_TEXT.search(value):
            raise FlowError("FLOW_SAFE_TEXT", pointer, "Artifact contains unsafe durable text.")
    elif value is None or isinstance(value, bool) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return
    else:
        raise FlowError("FLOW_SAFE_TEXT", pointer, "Artifact contains a non-JSON value.")


def write_create_only(run_root: Path, relative_path: PurePosixPath, value: Mapping[str, Any]) -> StoredArtifact:
    """Atomically write a validated artifact, or prove an identical prior write."""
    if not isinstance(relative_path, PurePosixPath) or relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts or "\\" in str(relative_path):
        raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact path must be a safe relative POSIX path.")
    _safe(value)
    payload = canonical_bytes(value)
    root = run_root.resolve()
    target = (root / Path(*relative_path.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact path escapes the run root.") from None
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (root, *target.parents)):
            raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact path may not traverse a symbolic link.")
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    except FileExistsError:
        try:
            existing = target.read_bytes()
        except OSError:
            raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Existing artifact could not be read back.") from None
        if existing != payload:
            raise FlowError("FLOW_CONFLICT", "/relative_path", "A different immutable artifact already exists.")
    except OSError:
        raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact could not be created.") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        readback = target.read_bytes()
    except OSError:
        raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact could not be read back.") from None
    if readback != payload:
        raise FlowError("FLOW_ATOMIC_WRITE", "/relative_path", "Artifact readback did not match the validated bytes.")
    return StoredArtifact(path=target, payload=readback, sha256="sha256:" + hashlib.sha256(readback).hexdigest())
