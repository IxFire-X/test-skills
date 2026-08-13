"""Synthetic canonical V3 fixtures and mutation helpers shared by later tests."""

from __future__ import annotations

import copy
from typing import Any, Iterable


def _type(kind: str = "string") -> dict[str, str]:
    return {"kind": "json", "type": kind}


def _management() -> dict[str, Any]:
    return {
        "status": None, "folder": None, "components": [], "labels": [],
        "owner": None, "estimated_time": None, "external_keys": {},
        "external_links": {"issues": [], "pages": []}, "custom_fields": {},
    }


def _step(index: int) -> dict[str, Any]:
    token = str(index)
    return {
        "step_id": f"STEP-{token}",
        "display_order": index,
        "action": f"Read synthetic item {token}.",
        "manual_only": False,
        "manual_reason": None,
        "automation_blockers": [],
        "operation": {
            "kind": "http",
            "binding_profile": "http-binding-v1",
            "base_url_source": {
                "kind": "environment", "name": "API_BASE_URL",
                "provenance": ["https://example.invalid/runtime"],
            },
            "method": "GET",
            "path": "/items/{item_id}",
        },
        "inputs": [{
            "input_id": f"INPUT-{token}",
            "display_order": 1,
            "target": {"location": "path", "name": "item_id", "sensitive": False},
            "source": {"kind": "literal", "value": token},
            "semantic_type": _type(),
        }],
        "outputs": [{
            "output_id": "status",
            "display_order": 1,
            "source": {"kind": "http_status"},
            "semantic_type": _type("integer"),
        }],
        "expectations": [{
            "expectation_id": f"EXP-{token}",
            "display_order": 1,
            "text": "The service returns 200.",
            "assertions": [{
                "assertion_id": f"ASSERT-{token}",
                "display_order": 1,
                "actual": {"kind": "http_status"},
                "operator": "equals",
                "expected": {"kind": "literal", "value": 200},
            }],
        }],
    }


def canonical_document(step_count: int = 1) -> dict[str, Any]:
    """Return a structurally and semantically valid synthetic V3 document."""
    return {
        "document_id": "TCDOC-semantic-fixture",
        "revision": 1,
        "parent_sha256": None,
        "metadata": {
            "subject": {"kind": "http_endpoint", "method": "GET", "path": "/items/{item_id}"},
            "documentation": ["https://example.invalid/spec"],
            "project": "EXAMPLE", "author": "TEST_AUTHOR", "date": "2026-08-12",
        },
        "operation_capabilities": [],
        "requirements": [{
            "requirement_id": "REQ-semantic-fixture",
            "display_order": 1, "text": "Items can be read.",
            "provenance": ["https://example.invalid/spec#items"],
        }],
        "test_cases": [{
            "case_id": "TC-semantic-fixture",
            "display_order": 1,
            "requirement_ids": ["REQ-semantic-fixture"],
            "title": "Read an item", "objective": "Verify each synthetic read.",
            "categories": ["positive", "functional"], "priority": "HIGH",
            "preconditions": [], "management": _management(),
            "steps": [_step(index) for index in range(1, step_count + 1)],
        }],
    }


def accepted_report_for(document: dict[str, Any]) -> dict[str, Any]:
    return {"verdict": "ПРИНЯТО", "document_id": document["document_id"], "revision": document["revision"]}


def rework_report_for(document: dict[str, Any], diagnostic: str = "needs rework") -> dict[str, Any]:
    return {"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "document_id": document["document_id"], "revision": document["revision"], "diagnostic": diagnostic}


def valid_successor_of(document: dict[str, Any]) -> dict[str, Any]:
    successor = copy.deepcopy(document)
    successor["revision"] += 1
    successor["parent_sha256"] = "sha256:" + "0" * 64
    return successor


def delete_input(document: dict[str, Any], step_index: int = 0, input_index: int = 0) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    del candidate["test_cases"][0]["steps"][step_index]["inputs"][input_index]
    return candidate


def rename_capability_argument(document: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    for argument in candidate["operation_capabilities"][0]["arguments"]:
        if argument["name"] == old:
            argument["name"] = new
            return candidate
    raise KeyError(old)


def reparent_assertion(document: dict[str, Any], source_expectation: int = 0, destination_expectation: int = 1) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    expectations = candidate["test_cases"][0]["steps"][0]["expectations"]
    expectations[destination_expectation]["assertions"].append(expectations[source_expectation]["assertions"].pop())
    return candidate


def edit_text_and_order_without_changing_ids(document: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    candidate["test_cases"][0]["title"] += " revised"
    candidate["test_cases"][0]["steps"].reverse()
    for index, step in enumerate(candidate["test_cases"][0]["steps"], 1):
        step["display_order"] = index
    return candidate


def diagnostic_codes(diagnostics: Iterable[dict[str, str]]) -> set[str]:
    return {diagnostic["code"] for diagnostic in diagnostics}
