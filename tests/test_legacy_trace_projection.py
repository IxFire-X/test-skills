from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation
from tools.schema_validation import schema_diagnostics

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in


def _timeout_trace() -> dict:
    from tools.automation_validation import automation_sha256
    from tools.build_trace_document import _construct
    from tools.canonical_document import document_sha256

    document = automated_document()
    generated = automation(document)
    source = {
        "document_id": document["document_id"],
        "revision": document["revision"],
        "source_digest": document_sha256(document),
        "effective_bundle_receipt_digest": "sha256:" + "0" * 64,
    }
    run = {
        "verdict": "UNKNOWN",
        "run_id": "RUN-timeout-1",
        "evidence_authoritative": False,
        "execution_evidence": [],
        "process_evidence": [{
            "run_id": "RUN-timeout-1", "source_digest": source["source_digest"],
            "kind": "TIMEOUT", "error_class": "TimeoutExpired", "exit_cause": "TIMEOUT",
            "command_profile": "pytest:selected-symbols-v1", "duration_sec": 1.0,
            "stdout_tail": "", "stderr_tail": "",
        }],
        "exit_code": 124,
        "stats": {"duration_sec": 1.0},
        "target": {"runner": "pytest", "command": "pytest:selected-symbols-v1"},
        "source": source,
        "automation_sha256": automation_sha256(generated),
        "autotest_review_sha256": automation_sha256({}),
    }

    return _construct(document, generated, {}, run)


def test_trace_v5_projects_unknown_pre_finalization_verification_without_final_authority() -> None:
    from tools.trace_check import check

    trace = _timeout_trace()
    report = check(trace)

    assert trace["schema_version"] == "5.0.0"
    assert trace["lifecycle"] == {"projection": "PRE_FINALIZATION", "verification": "UNKNOWN"}
    assert "final_verdict" not in trace and "company_execution_sha256" not in trace["execution"]
    assert schema_diagnostics(trace, (PACK_ROOT / "schemas/trace-document.schema.json"), PACK_ROOT) == []
    assert report["valid"] is True
    assert report["schema_version"] == "5.0.0"
    assert report["trace_audit"]["lifecycle"] == trace["lifecycle"]
    assert "final_verdict" not in report["trace_audit"]


def test_an_evidence_row_of_another_run_is_stale_in_the_trace() -> None:
    from tools.trace_check import check

    trace = _timeout_trace()
    row = {"run_id": "RUN-other", "source_digest": trace["source"]["source_digest"], "file_id": "FILE-x", "symbol_id": "SYMBOL-x",
           "file_digest": "sha256:" + "0" * 64, "status": "PASSED"}
    trace["execution"]["evidence"] = [row]
    assert "TRACE_STALE_EVIDENCE" in check(trace)["trace_audit"]["errors"]


def _ownership_rows(orders: list[int], revision: int, **audit) -> list[str]:
    from tools.trace_check import check

    trace = _timeout_trace()
    first = trace["test_cases"][0]
    trace["test_cases"] = [{**first, "case_id": f"TC-batch-a-00{number}", "display_order": order} for number, order in enumerate(orders, 1)]
    trace["source"]["revision"] = revision
    return [row["path"] for row in check(trace, **audit)["errors"] if row["code"] == "TRACE_CASE_OWNERSHIP"]


def test_a_subtraction_successor_keeps_the_holes_of_its_case_order_in_the_trace() -> None:
    assert _ownership_rows([1, 3, 4], 2) == []
    assert _ownership_rows([1, 3, 4], 2, canonical={"parent_sha256": "sha256:" + "1" * 64}) == []


@pytest.mark.parametrize(("orders", "revision", "audit"), [
    ([1, 3, 4], 1, {}),  # the first revision is contiguous
    ([1, 3, 4], 2, {"canonical": {"parent_sha256": None}}),  # r2 without a parent is no subtraction
    ([1, 3, 3], 2, {}), ([2, 1], 2, {}),  # strictly increasing
])
def test_the_case_order_of_the_trace_is_contiguous_unless_it_is_a_subtraction(orders: list[int], revision: int, audit: dict) -> None:
    assert _ownership_rows(orders, revision, **audit)
