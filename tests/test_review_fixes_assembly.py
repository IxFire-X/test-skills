"""Regression tests for the 2026-10-05 pipeline review: batch assembly (B6, M22)."""
from __future__ import annotations

import pytest

from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh


def _two_fragments():
    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    return header, plan, [_fragment(plan, context, 0), _fragment(plan, context, 1)]


def _capability(capability_id: str, provenance: list[str], action: str = "call") -> dict:
    return {"capability_id": capability_id, "adapter": "native", "action": action, "arguments": [], "results": [], "provenance": provenance}


def test_b6_capabilities_from_independent_batches_are_sorted_by_code_point() -> None:
    from tools.batch_assembly import assemble_candidate, canonical_bytes
    from tools.canonical_document import validate_canonical_document

    header, plan, fragments = _two_fragments()
    fragments[0]["operation_capabilities"] = [_capability("CAP-z-payment", ["source:payment"], action="pay")]
    fragments[1]["operation_capabilities"] = [_capability("CAP-a-refund", ["source:refund"], action="refund")]
    for fragment in fragments:
        _refresh(fragment)

    document, receipt = assemble_candidate(header, plan, fragments)
    shuffled, shuffled_receipt = assemble_candidate(header, plan, list(reversed(fragments)))

    assert [item["capability_id"] for item in document["operation_capabilities"]] == ["CAP-a-refund", "CAP-z-payment"]
    assert validate_canonical_document(document) == []
    assert canonical_bytes(document) == canonical_bytes(shuffled)
    assert canonical_bytes(receipt) == canonical_bytes(shuffled_receipt)


def test_b6_shared_capability_with_different_provenance_text_is_merged() -> None:
    from tools.batch_assembly import assemble_candidate

    header, plan, fragments = _two_fragments()
    fragments[0]["operation_capabilities"] = [_capability("CAP-shared", ["source:payment", "source:common"])]
    fragments[1]["operation_capabilities"] = [_capability("CAP-shared", ["source:common", "source:refund"])]
    for fragment in fragments:
        _refresh(fragment)

    document, _receipt = assemble_candidate(header, plan, fragments)

    assert document["operation_capabilities"] == [_capability("CAP-shared", ["source:payment", "source:common", "source:refund"])]


def test_b6_shared_capability_with_different_semantics_names_both_batches() -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate

    header, plan, fragments = _two_fragments()
    fragments[0]["operation_capabilities"] = [_capability("CAP-shared", ["source:payment"], action="call")]
    fragments[1]["operation_capabilities"] = [_capability("CAP-shared", ["source:refund"], action="cancel")]
    for fragment in fragments:
        _refresh(fragment)

    with pytest.raises(BatchAssemblyError) as caught:
        assemble_candidate(header, plan, fragments)

    assert caught.value.code == "BATCH_CAPABILITY_CONFLICT"
    message = str(caught.value)
    assert fragments[0]["batch_id"] in message and fragments[1]["batch_id"] in message and "CAP-shared" in message
    assert caught.value.diagnostics[0]["path"] == "/operation_capabilities/0"


def test_m22_assembly_audit_reports_every_diagnostic_with_json_pointer() -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate

    header, plan, fragments = _two_fragments()
    # Two independent semantic defects in two different batches.
    fragments[0]["test_cases"][0]["steps"][0]["expectations"][0]["display_order"] = 5
    fragments[1]["test_cases"][0]["steps"][0]["expectations"][0]["display_order"] = 5
    for fragment in fragments:
        _refresh(fragment)

    with pytest.raises(BatchAssemblyError) as caught:
        assemble_candidate(header, plan, fragments)

    diagnostics = caught.value.diagnostics
    assert len(diagnostics) >= 2
    assert all(item["path"].startswith("/") and item["code"] and item["message"] for item in diagnostics)
    assert {item["path"].split("/")[2] for item in diagnostics if item["path"].startswith("/test_cases/")} == {"0", "1"}
    for item in diagnostics:
        assert item["path"] in str(caught.value) and item["code"] in str(caught.value)


def test_m22_fragment_schema_failure_reports_every_diagnostic_with_json_pointer() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    header = _header()
    context = _context_receipt()
    plan = _plan(header, context)
    fragment = _fragment(plan, context, 0)
    fragment["test_cases"][0]["priority"] = "URGENT"
    fragment["requirements"][0]["text"] = 7
    _refresh(fragment)

    with pytest.raises(BatchAssemblyError) as caught:
        validate_fragment(fragment, plan, header, context)

    paths = {item["path"] for item in caught.value.diagnostics}
    assert "/test_cases/0/priority" in paths and "/requirements/0/text" in paths
