"""tools.digests — the one owner of the sha256 digests and of the canonical JSON without a newline (С5: Б1, Б2)."""
from __future__ import annotations

from tools import run_store
from tools.digests import digest_bytes, json_bytes, json_digest

VALUE = {"б": [1, "ё"], "a": {"z": None}}


def test_digest_of_empty_bytes():
    assert digest_bytes(b"") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_json_bytes_is_run_store_without_newline():
    assert json_bytes(VALUE) + b"\n" == run_store.canonical_bytes(VALUE)


def test_json_digest_is_digest_of_json_bytes():
    assert json_digest(VALUE) == digest_bytes(json_bytes(VALUE))
