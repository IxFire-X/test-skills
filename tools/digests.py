"""Digests of the package: sha256 of raw bytes, and of the canonical JSON *without* a trailing newline.

``run_store.canonical_bytes`` ends with a newline: a digest of it is ``digest_bytes(canonical_bytes(value))``, not ``json_digest(value)``.
"""
from __future__ import annotations

import hashlib
import json


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def json_bytes(value: object) -> bytes:
    """Canonical JSON: sorted keys, no spaces, UTF-8 as is, no newline at the end."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_digest(value: object) -> str:
    return digest_bytes(json_bytes(value))
