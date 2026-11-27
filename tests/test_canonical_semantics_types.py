"""Review 9b2e8a65 C1: the semantic checks of a canonical document read a model's answer of any shape — the schema names what is wrong, never a TypeError."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "canonical-test-document.schema.json"


def test_a_revision_as_a_string_is_named() -> None:
    found = schema_diagnostics({"revision": "2", "parent_sha256": "x", "test_cases": []}, SCHEMA, ROOT)
    assert [row["code"] for row in found] == ["CANONICAL_REVISION_TYPE"]


@pytest.mark.parametrize("document", [{"operation_capabilities": None}, {"test_cases": None}, {"test_cases": [{"categories": [["positive"]]}]}])
def test_a_field_of_another_type_gives_diagnostics(document: dict) -> None:
    assert schema_diagnostics(document, SCHEMA, ROOT)
