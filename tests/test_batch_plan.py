import pytest


def _requirements():
    return [
        {"source_requirement_id": "SREQ-payment", "display_order": 1, "text": "Оплата", "provenance": ["source:payment"], "digest": "sha256:" + "a" * 64},
        {"source_requirement_id": "SREQ-refund", "display_order": 2, "text": "Возврат", "provenance": ["source:refund"], "digest": "sha256:" + "b" * 64},
    ]


def _evidence(**overrides):
    result = {
        "header_digest": "sha256:" + "c" * 64,
        "context_receipt_digest": "sha256:" + "d" * 64,
    }
    result.update(overrides)
    return result


def test_plan_batches_defaults_to_one_stable_complete_batch():
    from tools.batch_assembly import plan_batches

    plan = plan_batches(list(reversed(_requirements())), _evidence())

    assert plan["batches"] == [{
        "batch_id": plan["batches"][0]["batch_id"],
        "ordinal": 1,
        "namespace": "B1",
        "owned_source_requirement_ids": ["SREQ-payment", "SREQ-refund"],
        "header_digest": "sha256:" + "c" * 64,
        "context_receipt_digest": "sha256:" + "d" * 64,
        "completeness": "COMPLETE",
    }]
    assert plan["digest"].startswith("sha256:")


def test_plan_uses_contiguous_physical_display_order_not_lexicographic_source_ids():
    from tools.batch_assembly import plan_batches

    requirements = [
        {"source_requirement_id": "SREQ-10", "display_order": 2, "text": "B01 Десятый", "provenance": ["source:10"], "digest": "sha256:" + "a" * 64},
        {"source_requirement_id": "SREQ-2", "display_order": 1, "text": "A01 Второй", "provenance": ["source:2"], "digest": "sha256:" + "b" * 64},
        {"source_requirement_id": "SREQ-20", "display_order": 3, "text": "B02 Двадцатый", "provenance": ["source:20"], "digest": "sha256:" + "c" * 64},
    ]
    plan = plan_batches(requirements, _evidence(), window_bytes=10_000, input_bytes=6_000)

    assert [item["source_requirement_id"] for item in plan["source_requirements"]] == ["SREQ-2", "SREQ-10", "SREQ-20"]
    assert [item["owned_source_requirement_ids"] for item in plan["batches"]] == [["SREQ-2"], ["SREQ-10", "SREQ-20"]]


@pytest.mark.parametrize("bad", [
    {"source_requirement_id": "SREQ-1", "display_order": 1, "text": "   ", "provenance": ["source:one"], "digest": "sha256:" + "a" * 64},
    {"source_requirement_id": "SREQ-1", "display_order": 1, "text": "Требование", "provenance": [{"path": "source:one"}], "digest": "sha256:" + "a" * 64},
])
def test_plan_rejects_noncanonical_source_requirement_content(bad):
    from tools.batch_assembly import BatchAssemblyError, plan_batches

    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        plan_batches([bad], _evidence())
