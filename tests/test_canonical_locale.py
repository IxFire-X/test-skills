from __future__ import annotations

from copy import deepcopy

from tests.test_requirement_traceability import canonical_fixture
from tools.canonical_document import validate_canonical_document


def test_canonical_document_requires_exact_v1_and_russian_human_corpus() -> None:
    document = canonical_fixture()
    assert validate_canonical_document(document) == []

    invalid_date = deepcopy(document)
    invalid_date["metadata"]["date"] = "2026-99-99"
    assert any(item["code"] == "SCHEMA_FORMAT" for item in validate_canonical_document(invalid_date))

    legacy = deepcopy(document)
    legacy["schema_version"] = "3.0.0"
    assert any(item["code"] == "SCHEMA_CONST" for item in validate_canonical_document(legacy))

    english = deepcopy(document)
    english["metadata"]["subject"]["name"] = "Catalog"
    english["metadata"]["project"] = "Demo"
    english["source_requirements"][0]["text"] = "User sees a product card."
    english["source_requirements"][1]["text"] = "User opens the product list."
    for requirement in english["requirements"]:
        requirement["text"] = "Catalog requirement"
    case = english["test_cases"][0]
    case["title"] = "Catalog check"
    case["objective"] = "Confirm catalog access."
    case["preconditions"] = ["User has catalog access."]
    step = case["steps"][0]
    step["action"] = "Open catalog."
    step["test_data"] = "None"
    step["manual_reason"] = "Automation is outside this scenario."
    step["expectations"][0]["text"] = "Available products are displayed."
    assert any(item["code"] == "SEMANTIC_RU_RU_HUMAN_FIELDS" for item in validate_canonical_document(english))


def test_canonical_document_has_no_fixed_case_count_ceiling() -> None:
    document = canonical_fixture()
    template = document["test_cases"][0]
    document["test_cases"] = []
    for number in range(201):
        case = deepcopy(template)
        case["case_id"] = f"TC-batch-a-{number + 1:03d}"
        case["display_order"] = number + 1
        case["steps"][0]["step_id"] = f"STEP-batch-a-{number + 1:03d}"
        case["steps"][0]["expectations"][0]["expectation_id"] = f"EXP-batch-a-{number + 1:03d}"
        document["test_cases"].append(case)
    assert validate_canonical_document(document) == []
