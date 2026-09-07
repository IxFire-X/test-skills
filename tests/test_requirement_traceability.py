from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from tools.build_context import build_context
from tools.canonical_document import validate_canonical_document
from tools.schema_validation import schema_diagnostics


def canonical_fixture() -> dict:
    source = [
        {"source_requirement_id": "SREQ-batch-a-001", "display_order": 1, "text": "Пользователь видит карточку товара.", "provenance": ["docs/feature.md — sha256:" + "a" * 64], "digest": "sha256:" + "a" * 64},
        {"source_requirement_id": "SREQ-batch-a-002", "display_order": 2, "text": "Пользователь может открыть список товаров.", "provenance": ["docs/feature.md — sha256:" + "b" * 64], "digest": "sha256:" + "b" * 64},
    ]
    requirements = [
        {"requirement_id": "CREQ-batch-a-001", "display_order": 1, "text": "Карточка товара отображается пользователю.", "provenance": ["SREQ-batch-a-001"]},
        {"requirement_id": "CREQ-batch-a-002", "display_order": 2, "text": "Список товаров доступен пользователю.", "provenance": ["SREQ-batch-a-002"]},
    ]
    return {
        "schema_version": "1.0.0",
        "content_locale": "ru-RU",
        "document_id": "TCDOC-batch-a",
        "revision": 1,
        "parent_sha256": None,
        "metadata": {"subject": {"kind": "generic", "name": "Каталог"}, "documentation": ["docs/feature.md"], "project": "Демонстрация", "author": "pipeline", "date": "2026-08-26"},
        "operation_capabilities": [],
        "source_requirements": source,
        "requirements": requirements,
        "source_to_canonical_mappings": [
            {"source_requirement_id": "SREQ-batch-a-001", "canonical_requirement_ids": ["CREQ-batch-a-001", "CREQ-batch-a-002"]},
            {"source_requirement_id": "SREQ-batch-a-002", "canonical_requirement_ids": ["CREQ-batch-a-002"]},
        ],
        "test_cases": [
            {"case_id": "TC-batch-a-001", "display_order": 1, "requirement_ids": ["CREQ-batch-a-001", "CREQ-batch-a-002"], "title": "Проверка отображения каталога", "objective": "Подтвердить доступность карточек товара.", "categories": ["functional"], "priority": "MEDIUM", "preconditions": ["Пользователь имеет доступ к каталогу."], "management": {"status": None, "folder": None, "components": [], "labels": [], "owner": None, "estimated_time": None, "external_keys": {}, "external_links": {"issues": [], "pages": []}, "custom_fields": {}}, "steps": [{"step_id": "STEP-batch-a-001", "display_order": 1, "action": "Открыть каталог товаров.", "test_data": "—", "manual_only": True, "manual_reason": "Автоматизация не входит в этот сценарий.", "automation_blockers": [], "operation": None, "inputs": [], "outputs": [], "expectations": [{"expectation_id": "EXP-batch-a-001", "display_order": 1, "text": "Отображается список доступных товаров.", "assertions": []}]}]},
        ],
    }


def test_canonical_document_keeps_distinct_many_to_many_source_traceability() -> None:
    assert validate_canonical_document(canonical_fixture()) == []


def test_canonical_document_rejects_case_with_unmapped_canonical_requirement() -> None:
    document = deepcopy(canonical_fixture())
    document["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-batch-a-001"]
    document["source_to_canonical_mappings"][1]["canonical_requirement_ids"] = ["CREQ-batch-a-001"]
    diagnostics = validate_canonical_document(document)
    assert any(item["code"] == "SEMANTIC_UNMAPPED_CANONICAL_REQUIREMENT" for item in diagnostics)


def test_context_emits_distinct_source_requirement_identity_with_digest_provenance(tmp_path: Path) -> None:
    document = tmp_path / "requirements.md"
    document.write_text("## REQ-12\nПоказать карточку товара.\n", encoding="utf-8")
    context = build_context(tmp_path, docs=[document])
    requirement = context["artifacts"]["analytics_documentation"]["requirements"][0]
    assert "Показать карточку товара." in requirement["text"]
    assert context["warnings"] == []
    assert context["schema_version"] == "5.0.0"
    assert requirement["source_requirement_id"].startswith("SREQ-")
    assert "requirement_id" not in requirement
    assert any("sha256:" in row for row in requirement["provenance"])
    assert schema_diagnostics({key: value for key, value in context.items() if key != "status"}, Path("schemas/context-marker-output.schema.json"), Path.cwd()) == []


def test_context_assigns_source_ids_after_stable_snapshot_sorting() -> None:
    def snapshot(path: str, content: str) -> dict[str, str]:
        return {"path": path, "content": content, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()}

    entries = [
        snapshot("docs/z.md", "## Отображение товара\nКарточка доступна пользователю.\n"),
        snapshot("docs/a.md", "## Вход пользователя\nПользователь входит в систему.\n"),
    ]
    first = build_context(Path.cwd(), docs_snapshot=entries)
    second = build_context(Path.cwd(), docs_snapshot=list(reversed(entries)))
    first_requirements = first["artifacts"]["analytics_documentation"]["requirements"]
    second_requirements = second["artifacts"]["analytics_documentation"]["requirements"]
    assert first_requirements == second_requirements
    assert [item["source_requirement_id"] for item in first_requirements] == ["SREQ-0001", "SREQ-0002"]
    assert [item["display_order"] for item in first_requirements] == [1, 2]


def test_context_keeps_plain_prose_and_multiline_requirements_with_mixed_headings() -> None:
    from tools.build_context import extract_inventory, grounding_diagnostics

    sections = [
        "Пользователь может отменить заказ. Вернуть точное значение `a  b`.",
        "## REQ-12\nПоказать карточку товара.\nПри отсутствии вернуть 404.",
        "### Ограничения\nЧужие товары недоступны. См. REQ-12.",
        "### Ограничения\nПустой идентификатор отклоняется.",
    ]
    rows = extract_inventory("\n\n".join(sections))
    assert [row["text"] for row in rows] == sections
    assert extract_inventory(sections[0])[0]["text"] == sections[0]
    content = "\n\n".join(sections)
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    context = build_context(Path.cwd(), docs_snapshot=[{"path": "docs/requirements.md", "content": content, "sha256": digest}])
    assert {row["text"] for row in context["artifacts"]["analytics_documentation"]["requirements"]} == set(sections)
    canonical = canonical_fixture()
    canonical["source_requirements"][0]["text"] = sections[0]
    source_context = {"artifacts": {"analytics_documentation": {"requirements": deepcopy(canonical["source_requirements"])}}}
    assert grounding_diagnostics(source_context, canonical) == []
    canonical["source_requirements"][0]["text"] = sections[0].replace("a  b", "a b")
    assert any(row["code"] == "UNGROUNDED" for row in grounding_diagnostics(source_context, canonical))


def test_context_uses_sequential_source_ids_even_when_evidence_has_req_labels() -> None:
    def snapshot(path: str, content: str) -> dict[str, str]:
        return {"path": path, "content": content, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()}

    context = build_context(Path.cwd(), docs_snapshot=[
        snapshot("docs/z.md", "## REQ-10\nВторое требование.\n"),
        snapshot("docs/a.md", "## REQ-2\nПервое требование.\n"),
    ])
    requirements = context["artifacts"]["analytics_documentation"]["requirements"]
    assert [item["source_requirement_id"] for item in requirements] == ["SREQ-0001", "SREQ-0002"]
    assert all(not item["source_requirement_id"].startswith("SREQ-REQ-") for item in requirements)


def test_canonical_document_rejects_source_requirements_out_of_physical_order() -> None:
    document = canonical_fixture()
    document["source_requirements"] = list(reversed(document["source_requirements"]))
    diagnostics = validate_canonical_document(document)
    assert any(item["code"] == "CANONICAL_SOURCE_REQUIREMENT_ORDER" for item in diagnostics)


def test_context_allows_more_than_two_hundred_normalized_source_requirements() -> None:
    content = "\n".join(f"## Поведение {number}\nОписание поведения {number}." for number in range(1, 202))
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    context = build_context(Path.cwd(), docs_snapshot=[{"path": "docs/all.md", "content": content, "sha256": digest}])
    requirements = context["artifacts"]["analytics_documentation"]["requirements"]
    assert len(requirements) == 201
    assert requirements[-1]["source_requirement_id"] == "SREQ-0201"
