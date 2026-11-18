"""q15: the diagnostics of the pair «type of the capability ↔ type of the source» and of the operator name the way out, and following a hint gives no new diagnostic."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_automation_revision_budget import automated_document
from tools.canonical_document import validate_canonical_document_all

ROOT = Path(__file__).resolve().parents[1]
NAMED = {"kind": "named", "name": "json_text", "representation": "string"}
JSON_STRING = {"kind": "json", "type": "string"}
REGEX = {"kind": "regex", "dialect": "portable-regex-v1", "pattern": "^x$"}


def _assertion(operator: str, expected: dict[str, Any]) -> dict[str, Any]:
    return {"assertion_id": "ASSERT-a-1", "display_order": 1, "actual": {"kind": "project_result", "name": "out"}, "operator": operator, "expected": expected}


def _document(source: dict[str, Any], stored: dict[str, Any], assertion: dict[str, Any] | None = None) -> dict[str, Any]:
    """One project_action step: the capability takes `message` of the named type json_text and returns `out` of the same type."""
    document = automated_document()
    document["operation_capabilities"] = [{"capability_id": "CAP-a-1", "adapter": "pytest:x", "action": "f.deserialize", "provenance": ["p"],
                                           "arguments": [{"name": "message", "semantic_type": NAMED, "required": True}], "results": [{"name": "out", "semantic_type": NAMED}]}]
    step = document["test_cases"][0]["steps"][0]
    step.update(operation={"kind": "project_action", "capability_id": "CAP-a-1"}, outputs=[], test_data="x")
    step["inputs"] = [{"input_id": "INPUT-a-1", "display_order": 1, "target": {"location": "arg", "name": "message", "sensitive": False}, "source": source, "semantic_type": stored,
                       **({"type_provenance": ["docs/x.md:1"]} if source["kind"] == "fixture" else {})}]
    step["expectations"][0].update(text="Ответ.", assertions=[assertion or _assertion("contains", {"kind": "literal", "value": "x"})])
    return document


def _rows(document: dict[str, Any]) -> list[dict[str, str]]:
    return validate_canonical_document_all(document)


def _codes(document: dict[str, Any]) -> list[str]:
    return [row["code"] for row in _rows(document)]


def test_a_literal_string_into_a_named_argument_gives_one_diagnostic() -> None:
    assert _codes(_document({"kind": "literal", "value": "{}"}, JSON_STRING)) == ["SEMANTIC_CAPABILITY_TYPE_MISMATCH"]


def test_the_type_of_the_argument_written_over_a_literal_does_not_give_a_second_diagnostic() -> None:
    assert _codes(_document({"kind": "literal", "value": "{}"}, NAMED)) == ["SEMANTIC_CAPABILITY_TYPE_MISMATCH"]


def test_the_capability_message_names_the_type_of_the_source_and_of_the_argument() -> None:
    [row] = _rows(_document({"kind": "literal", "value": "{}"}, JSON_STRING))
    assert '"type":"string"' in row["message"] and '"name":"json_text"' in row["message"]


def test_a_fixture_of_the_type_of_the_argument_gives_no_diagnostic() -> None:
    assert _codes(_document({"kind": "fixture", "name": "message"}, NAMED)) == []


def test_a_type_that_differs_from_the_source_without_a_capability_clash_is_still_a_mismatch() -> None:
    document = _document({"kind": "literal", "value": "{}"}, {"kind": "json", "type": "object"})
    document["operation_capabilities"][0]["arguments"][0]["semantic_type"] = JSON_STRING
    assert _codes(document) == ["SEMANTIC_TYPE_MISMATCH"]


def test_the_operator_message_names_the_operator_and_both_types() -> None:
    [row] = _rows(_document({"kind": "fixture", "name": "message"}, NAMED, _assertion("equals", {"kind": "literal", "value": "x"})))
    assert row["code"] == "SEMANTIC_OPERATOR_TYPE" and "operator equals" in row["message"] and "json_text" in row["message"]


@pytest.mark.parametrize("operator, expected", [("contains", {"kind": "literal", "value": "x"}), ("matches", REGEX)])
def test_the_operators_the_fix_names_for_a_json_text_pass(operator: str, expected: dict[str, Any]) -> None:
    assert _codes(_document({"kind": "fixture", "name": "message"}, NAMED, _assertion(operator, expected))) == []


def test_a_regex_is_no_literal_for_matches() -> None:
    assert _codes(_document({"kind": "fixture", "name": "message"}, NAMED, _assertion("matches", {"kind": "literal", "value": "^x$"}))) == ["SEMANTIC_OPERATOR_TYPE"]


def test_the_generator_role_says_one_capability_per_adapter_action_pair() -> None:
    assert "One adapter+action pair = one capability per batch." in (ROOT / "skills" / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
