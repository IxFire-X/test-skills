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
        "complete": True,
        "independence_proven": True,
        "groups": [],
    }
    result.update(overrides)
    return result


def test_plan_batches_defaults_to_one_stable_complete_batch_when_independence_is_unproved():
    from tools.batch_assembly import plan_batches

    plan = plan_batches(list(reversed(_requirements())), _evidence(independence_proven=False))

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


def test_plan_batches_splits_only_a_complete_explicit_non_overlapping_partition():
    from tools.batch_assembly import BatchAssemblyError, plan_batches

    evidence = _evidence(groups=[
        {"group_id": "refund", "source_requirement_ids": ["SREQ-refund"], "independence_evidence": ["target:refund"]},
        {"group_id": "payment", "source_requirement_ids": ["SREQ-payment"], "independence_evidence": ["target:payment"]},
    ])

    plan = plan_batches(_requirements(), evidence)
    assert [(item["ordinal"], item["namespace"], item["owned_source_requirement_ids"]) for item in plan["batches"]] == [
        (1, "B1", ["SREQ-payment"]),
        (2, "B2", ["SREQ-refund"]),
    ]

    incomplete = _evidence(groups=[
        {"group_id": "payment", "source_requirement_ids": ["SREQ-payment"], "independence_evidence": ["target:payment"]},
    ])
    with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
        plan_batches(_requirements(), incomplete)
    evidence["groups"][0]["source_requirement_ids"] = ["SREQ-unknown"]
    with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
        plan_batches(_requirements(), evidence)


def test_plan_uses_contiguous_physical_display_order_not_lexicographic_source_ids():
    from tools.batch_assembly import plan_batches

    requirements = [
        {"source_requirement_id": "SREQ-10", "display_order": 2, "text": "Десятый", "provenance": ["source:10"], "digest": "sha256:" + "a" * 64},
        {"source_requirement_id": "SREQ-2", "display_order": 1, "text": "Второй", "provenance": ["source:2"], "digest": "sha256:" + "b" * 64},
        {"source_requirement_id": "SREQ-20", "display_order": 3, "text": "Двадцатый", "provenance": ["source:20"], "digest": "sha256:" + "c" * 64},
    ]
    plan = plan_batches(requirements, _evidence(groups=[
        {"group_id": "late", "source_requirement_ids": ["SREQ-20", "SREQ-10"], "independence_evidence": ["target:late"]},
        {"group_id": "early", "source_requirement_ids": ["SREQ-2"], "independence_evidence": ["target:early"]},
    ]))

    assert [item["source_requirement_id"] for item in plan["source_requirements"]] == ["SREQ-2", "SREQ-10", "SREQ-20"]
    assert [item["owned_source_requirement_ids"] for item in plan["batches"]] == [["SREQ-2"], ["SREQ-10", "SREQ-20"]]


@pytest.mark.parametrize("bad", [
    {"source_requirement_id": "SREQ-1", "display_order": 1, "text": "   ", "provenance": ["source:one"], "digest": "sha256:" + "a" * 64},
    {"source_requirement_id": "SREQ-1", "display_order": 1, "text": "Требование", "provenance": [{"path": "source:one"}], "digest": "sha256:" + "a" * 64},
])
def test_plan_rejects_noncanonical_source_requirement_content(bad):
    from tools.batch_assembly import BatchAssemblyError, plan_batches

    with pytest.raises(BatchAssemblyError, match="BATCH_SEMANTIC_CONFLICT"):
        plan_batches([bad], _evidence(independence_proven=False))
