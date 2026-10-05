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


def test_short_native_case_remains_valid_and_renders_observable_expected() -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.human_scenario import check_canonical
    from tools.publish_test_case_bundle import build_bundle

    assert validate_canonical_document(automated_document()) == []
    document = canonical_fixture()
    document["operation_capabilities"] = [{
        "capability_id": "CAP-catalog", "adapter": "native", "action": "list_products",
        "arguments": [], "results": [{"name": "available", "semantic_type": {"kind": "json", "type": "boolean"}}],
        "provenance": ["fixture — catalog application boundary"],
    }]
    case = document["test_cases"][0]
    case["title"] = "Каталог"
    step = case["steps"][0]
    step.update({"manual_only": False, "manual_reason": None, "operation": {"kind": "project_action", "capability_id": "CAP-catalog"}, "action": "Получить список товаров через GET /products."})
    expected = "Приложение возвращает список товаров, включая «Набор для автотеста»."
    step["expectations"][0].update({"text": expected, "assertions": [{
        "assertion_id": "ASSERT-catalog", "display_order": 1, "actual": {"kind": "project_result", "name": "available"},
        "operator": "equals", "expected": {"kind": "literal", "value": True},
    }]})
    assert validate_canonical_document(document) == []
    assert check_canonical(document)["status"] == "ok"
    bundle = build_bundle(document)
    assert expected in bundle.preview_bytes.decode("utf-8")
    assert step["action"] in bundle.csv_bytes.decode("utf-8")


def test_service_only_expected_in_last_step_is_rejected_before_publication() -> None:
    import pytest

    from tools.canonical_document import CanonicalDocumentError
    from tools.publish_test_case_bundle import build_bundle

    document = canonical_fixture()
    steps = document["test_cases"][0]["steps"]
    last = deepcopy(steps[0])
    last.update({"step_id": "STEP-last", "display_order": 2})
    last["expectations"][0].update({"expectation_id": "EXP-last", "text": "Проверено автотестом"})
    steps.append(last)
    diagnostic = next(item for item in validate_canonical_document(document) if item["code"] == "SEMANTIC_HUMAN_EXPECTED_PLACEHOLDER")
    assert diagnostic["path"] == "/test_cases/0/steps/1/expectations/0/text"
    assert "TC-batch-a-001 / STEP-last" in diagnostic["message"]
    with pytest.raises(CanonicalDocumentError, match="SEMANTIC_HUMAN_EXPECTED_PLACEHOLDER"):
        build_bundle(document)
    from tests.test_automation_revision_budget import automated_document

    http_document = automated_document()
    http_document["test_cases"][0]["steps"][0]["expectations"][0]["text"] = "Проверено автотестом\n\nHTTP 200 OK"
    with pytest.raises(CanonicalDocumentError, match="SEMANTIC_HUMAN_EXPECTED_PLACEHOLDER"):
        build_bundle(http_document)
