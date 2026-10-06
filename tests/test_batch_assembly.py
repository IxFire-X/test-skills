import hashlib
import json
from pathlib import Path

import pytest


def _digest(letter: str) -> str:
    return "sha256:" + letter * 64


def _header_digest(header: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _self_digest(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _context_receipt() -> dict:
    receipt = {
        "schema_version": "1.0.0", "inventory_digest": _digest("e"), "batch_index": 1,
        "files": [], "byte_count": 0, "byte_set_digest": _digest("f"),
    }
    receipt["digest"] = _self_digest(receipt)
    return receipt


def _header():
    return {
        "schema_version": "1.0.0",
        "document_id": "TCDOC-pilot",
        "revision": 1,
        "parent_sha256": None,
        "content_locale": "ru-RU",
        "metadata": {"subject": {"kind": "generic", "name": "Платежи"}, "documentation": ["source"], "project": "pilot", "author": "controller", "date": "2026-08-26"},
    }


def _requirements():
    return [
        {"source_requirement_id": "SREQ-payment", "display_order": 1, "text": "Оплата", "provenance": ["source:payment"], "digest": _digest("a")},
        {"source_requirement_id": "SREQ-refund", "display_order": 2, "text": "Возврат", "provenance": ["source:refund"], "digest": _digest("b")},
    ]


def _plan(header, context):
    from tools.batch_assembly import plan_batches

    return plan_batches(_requirements(), {
        "header_digest": _header_digest(header), "context_receipt_digest": context["digest"],
        "complete": True, "independence_proven": True,
        "groups": [
            {"group_id": "payment", "source_requirement_ids": ["SREQ-payment"], "independence_evidence": ["target:payment"]},
            {"group_id": "refund", "source_requirement_ids": ["SREQ-refund"], "independence_evidence": ["target:refund"]},
        ],
    })


def _fragment(plan, context, batch_index: int):
    batch = plan["batches"][batch_index]
    source_id = batch["owned_source_requirement_ids"][0]
    suffix = source_id.removeprefix("SREQ-")
    namespace = batch["namespace"]
    canonical_id = f"CREQ-{namespace}-{suffix}"
    fragment = {
        "schema_version": "1.0.0",
        "status": "COMPLETE",
        "batch_id": batch["batch_id"],
        "namespace": batch["namespace"],
        "plan_digest": plan["digest"],
        "header_digest": plan["header_digest"],
        "context_receipt_digest": context["digest"],
        "context_receipt": context,
        "owned_source_requirement_ids": [source_id],
        "requirements": [{"requirement_id": canonical_id, "display_order": 99, "text": "Требование " + suffix, "provenance": ["source:" + suffix]}],
        "source_to_canonical_mappings": [{"source_requirement_id": source_id, "canonical_requirement_ids": [canonical_id]}],
        "operation_capabilities": [{"capability_id": "CAP-shared", "adapter": "native", "action": "call", "arguments": [], "results": [], "provenance": ["source:cap"]}],
        "test_cases": [{"case_id": f"TC-{namespace}-{suffix}", "display_order": 99, "requirement_ids": [canonical_id], "title": "Проверка", "objective": "Проверить сценарий", "categories": ["functional"], "priority": "MEDIUM", "preconditions": [], "management": {"status": None, "folder": None, "components": [], "labels": [], "owner": None, "estimated_time": None, "external_keys": {}, "external_links": {"issues": [], "pages": []}, "custom_fields": {}}, "steps": [{"step_id": f"STEP-{namespace}-{suffix}", "display_order": 1, "action": "Выполнить действие", "test_data": "Данные", "manual_only": True, "manual_reason": "Ручная проверка", "automation_blockers": [], "operation": None, "inputs": [], "outputs": [], "expectations": [{"expectation_id": f"EXP-{namespace}-{suffix}", "display_order": 1, "text": "Результат", "assertions": []}]}]}],
        "diagnostics": [],
    }
    fragment["digest"] = _self_digest(fragment)
    return fragment


def _refresh(fragment: dict) -> None:
    fragment["digest"] = _self_digest(fragment)


def test_generator_envelope_validates_nested_context_receipt(pack_root: Path):
    from tools.schema_validation import schema_diagnostics

    context = _context_receipt()
    fragment = _fragment(_plan(_header(), context), context, 0)
    output = {"schema_version": "5.0.0", "stage": "tc-generator",
              "artifacts": {"candidate_fragment": fragment}, "warnings": []}
    schema = pack_root / "schemas/tc-generator-output.schema.json"
    assert schema_diagnostics(output, schema, pack_root) == []
    context["byte_count"] = "invalid"
    diagnostics = schema_diagnostics(output, schema, pack_root)
    assert any(row["path"] == "/artifacts/candidate_fragment/context_receipt/byte_count"
               and row["code"] == "SCHEMA_TYPE" for row in diagnostics)


def test_assemble_candidate_is_byte_identical_for_shuffled_fragments_and_keeps_duplicate_warning():
    from tools.batch_assembly import assemble_candidate, canonical_bytes, validate_fragment
    from tools.canonical_document import validate_canonical_document

    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    for fragment in fragments:
        validate_fragment(fragment, plan, header, context)

    first, first_receipt = assemble_candidate(header, plan, fragments)
    second, second_receipt = assemble_candidate(header, plan, list(reversed(fragments)))

    assert canonical_bytes(first) == canonical_bytes(second)
    assert canonical_bytes(first_receipt) == canonical_bytes(second_receipt)
    assert validate_canonical_document(first) == []
    assert [case["display_order"] for case in first["test_cases"]] == [1, 2]
    assert [item["code"] for item in first_receipt["warnings"]] == ["POSSIBLE_DUPLICATE"]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda fragment: fragment.update({"batch_id": "BATCH-foreign"}), "BATCH_ID_CONFLICT"),
        (lambda fragment: fragment["test_cases"][0].update({"requirement_ids": ["CREQ-refund"]}), "BATCH_PARTITION_CONFLICT"),
    ],
)
def test_validate_fragment_rejects_identity_and_cross_batch_requirement_conflicts(mutation, code):
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragment = _fragment(plan, context, 0)
    mutation(fragment)
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match=code):
        validate_fragment(fragment, plan, header, context)


def test_assemble_candidate_rejects_same_capability_id_with_different_semantics():
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    fragments[1]["operation_capabilities"][0]["action"] = "different"
    _refresh(fragments[1])
    with pytest.raises(BatchAssemblyError, match="BATCH_CAPABILITY_CONFLICT"):
        assemble_candidate(header, plan, fragments)


def test_assemble_candidate_rejects_a_canonical_requirement_reused_across_batches():
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate

    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    fragments[1]["requirements"][0]["requirement_id"] = "CREQ-B1-payment"
    fragments[1]["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B1-payment"]
    fragments[1]["test_cases"][0]["requirement_ids"] = ["CREQ-B1-payment"]
    _refresh(fragments[1])

    with pytest.raises(BatchAssemblyError, match="BATCH_ID_CONFLICT"):
        assemble_candidate(header, plan, fragments)


def test_validate_fragment_fails_closed_for_schema_drift_namespace_and_tampered_context_receipt():
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragment = _fragment(plan, context, 0)
    fragment["requirements"][0]["unexpected"] = True
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(fragment, plan, header, context)

    fragment = _fragment(plan, context, 0)
    fragment["requirements"][0]["requirement_id"] = "CREQ-B2-payment"
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B2-payment"]
    fragment["test_cases"][0]["requirement_ids"] = ["CREQ-B2-payment"]
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match="BATCH_ID_CONFLICT"):
        validate_fragment(fragment, plan, header, context)

    fragment = _fragment(plan, context, 0)
    tampered = dict(context)
    tampered["byte_count"] = 1
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(fragment, plan, header, tampered)


def test_duplicate_mapping_is_partition_conflict_and_failed_fragment_publication_is_immutable(tmp_path: Path):
    from tools.batch_assembly import BatchAssemblyError, publish_fragment, validate_fragment

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    duplicate = _fragment(plan, context, 0)
    duplicate["source_to_canonical_mappings"].append(dict(duplicate["source_to_canonical_mappings"][0]))
    _refresh(duplicate)
    with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
        validate_fragment(duplicate, plan, header, context)

    conflicting = _fragment(plan, context, 0)
    conflicting["source_to_canonical_mappings"][0]["canonical_requirement_ids"] = ["CREQ-B1-unknown"]
    _refresh(conflicting)
    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        validate_fragment(conflicting, plan, header, context)

    failed = _fragment(plan, context, 0)
    failed["status"] = "FAILED"
    model_receipt = {"schema_version": "1.0.0", "status": "FAILED", "diagnostics": [{"code": "MODEL_TRANSPORT_INVALID", "message": "response was not a complete JSON artifact"}]}
    model_receipt["digest"] = _self_digest(model_receipt)
    failed["failure"] = {"reason_code": "MODEL_TRANSPORT_INVALID", "evidence_digest": model_receipt["digest"], "model_receipt": model_receipt}
    failed["requirements"] = []
    failed["source_to_canonical_mappings"] = []
    failed["operation_capabilities"] = []
    failed["test_cases"] = []
    _refresh(failed)
    published = publish_fragment(tmp_path, failed)
    assert published["readback"] == failed
    assert published["bytes"] == (tmp_path / "fragments" / f"{failed['batch_id']}.json").read_bytes()
    assert published["byte_digest"] != published["digest"]
    assert published["model_receipt"]["readback"] == model_receipt
    assert published["model_receipt"]["bytes"] == (tmp_path / "model-receipts" / f"{failed['batch_id']}.json").read_bytes()
    with pytest.raises(BatchAssemblyError, match="BATCH_ID_CONFLICT"):
        publish_fragment(tmp_path, failed)
    validate_fragment(_fragment(plan, context, 1), plan, header, context)
