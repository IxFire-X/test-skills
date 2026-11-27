"""A setup helper (a ``setup`` relation, canonical 1.2.0) is no test: it is never selected for the run (``-Dtest``, the pytest nodes) and the
reports need not carry it — the live run of the stand once stopped on INGEST_REPORT_MISMATCH «reports carry 42 of the 45 reviewed tests»."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tests.test_setup_mode_b import setup_automation, setup_document
from tools.exec_runner import report_set_problems
from tools.run_tests import validate_artifact_runner_compatibility
from tools.trace_check import check

TEST = ("FILE-products", "SYMBOL-products")
REPORT = b'<testsuite tests="1"><testcase classname="tests.test_products" name="test_products"/></testsuite>'


def _compatibility(tmp_path: Path):
    document = setup_document()
    generated = setup_automation(document)
    content = "def prepare_products():\n    pass\n\n\ndef test_products():\n    prepare_products()\n"
    generated["artifacts"]["generated_files"][0].update(content=content, content_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    return validate_artifact_runner_compatibility(tmp_path, "python", document, generated, materialized=False), generated


def test_the_run_selects_the_test_not_the_setup_helper(tmp_path: Path) -> None:
    compatibility, _generated = _compatibility(tmp_path)
    assert compatibility.status == "READY" and compatibility.required_pairs == (TEST,)


def test_reports_without_the_setup_helper_are_the_reviewed_tests(tmp_path: Path) -> None:
    compatibility, generated = _compatibility(tmp_path)
    assert report_set_problems({tmp_path / "TEST-products.xml": REPORT}, compatibility, generated["artifacts"]["source"], "python") == []


def test_the_trace_awaits_no_evidence_of_the_setup_helper() -> None:
    from tools.build_trace_document import _construct

    document = setup_document()
    audit = check(_construct(document, setup_automation(document), {}, None))["trace_audit"]
    assert audit["required_symbol_pairs"] == [{"file_id": TEST[0], "symbol_id": TEST[1]}]
