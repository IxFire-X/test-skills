"""Regression tests for the 2026-10-05 pipeline review: case model and projections."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_automation_revision_budget import automated_document
from tools.canonical_document import validate_canonical_document


def _with_status(code: int, reason: str) -> dict:
    document = deepcopy(automated_document())
    step = document["test_cases"][0]["steps"][0]
    for assertion in step["expectations"][0]["assertions"]:
        if assertion["actual"]["kind"] == "http_status":
            assertion["expected"]["value"] = code
    step["expectations"][0]["text"] = f"Отображается список доступных товаров.\n\nHTTP {code} {reason}"
    return document


def _codes(document: dict) -> set[str]:
    return {item["code"] for item in validate_canonical_document(document)}


# B12: the accepted reason phrase must not depend on the interpreter's http.HTTPStatus table.
@pytest.mark.parametrize("code, reason", [
    (200, "OK"),
    (413, "Request Entity Too Large"), (413, "Content Too Large"),
    (414, "Request-URI Too Long"), (414, "URI Too Long"),
    (416, "Requested Range Not Satisfiable"), (416, "Range Not Satisfiable"),
    (422, "Unprocessable Entity"), (422, "Unprocessable Content"),
])
def test_b12_reason_phrase_accepts_rfc9110_and_legacy_wording(code: int, reason: str) -> None:
    assert "SEMANTIC_HUMAN_HTTP_RESULT_FORMAT" not in _codes(_with_status(code, reason))


@pytest.mark.parametrize("code, reason", [(422, "Unprocessable"), (200, "Created"), (404, "Missing")])
def test_b12_wrong_reason_phrase_is_still_rejected(code: int, reason: str) -> None:
    assert "SEMANTIC_HUMAN_HTTP_RESULT_FORMAT" in _codes(_with_status(code, reason))


def test_b12_validator_does_not_import_http_status() -> None:
    import tools.canonical_document as module

    assert not hasattr(module, "HTTPStatus")
