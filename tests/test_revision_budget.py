from copy import deepcopy

from tests.test_requirement_traceability import canonical_fixture


def test_only_r1_to_complete_r2_successor_is_allowed_and_traceability_is_preserved():
    from tools.canonical_document import document_sha256
    from tools.revision_selection import validate_successor

    candidate = canonical_fixture()
    successor = deepcopy(candidate)
    successor["revision"] = 2
    successor["parent_sha256"] = document_sha256(candidate)
    successor["test_cases"][0]["title"] = "Уточнённая проверка отображения каталога"

    assert validate_successor(candidate, successor) == []

    third = deepcopy(successor)
    third["revision"] = 3
    third["parent_sha256"] = document_sha256(successor)
    assert any(item["code"] == "REVISION_BUDGET" for item in validate_successor(successor, third))

    changed_mapping = deepcopy(successor)
    changed_mapping["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-batch-a-001"]
    assert any(item["code"] == "TRACEABILITY_CHANGED" for item in validate_successor(candidate, changed_mapping))


def test_successor_cannot_swap_case_requirements_or_rewrite_confirmed_machine_semantics():
    from tools.canonical_document import document_sha256
    from tools.revision_selection import validate_successor

    candidate = canonical_fixture()
    successor = deepcopy(candidate)
    successor["revision"] = 2
    successor["parent_sha256"] = document_sha256(candidate)

    swapped = deepcopy(successor)
    swapped["test_cases"][0]["requirement_ids"] = ["CREQ-batch-a-002"]
    assert {row["code"] for row in validate_successor(candidate, swapped)} == {"TRACEABILITY_CHANGED"}

    branch_drift = deepcopy(successor)
    branch_drift["test_cases"][0]["steps"][0]["manual_only"] = False
    assert {row["code"] for row in validate_successor(candidate, branch_drift)} == {"MACHINE_SEMANTICS_CHANGED"}

    capability_drift = deepcopy(successor)
    capability_drift["operation_capabilities"] = [{
        "capability_id": "CAP-added",
        "adapter": "project",
        "action": "delete",
        "arguments": [],
        "results": [],
        "provenance": ["reviewer"],
    }]
    assert {row["code"] for row in validate_successor(candidate, capability_drift)} == {"MACHINE_SEMANTICS_CHANGED"}
