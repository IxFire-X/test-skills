from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.fixture_factory import canonical_document
from tools.canonical_document import document_sha256
from tools.publish_test_case_bundle import Receipt
from tools.schema_validation import schema_diagnostics


def review_for(document: dict, verdict: str = "ПРИНЯТО", successor: dict | None = None) -> dict:
    report = {
        "verdict": verdict,
        "candidate": {"document_id": document["document_id"], "revision": document["revision"], "document_sha256": document_sha256(document)},
        "reviewed_case_ids": [row["case_id"] for row in document["test_cases"]],
        "findings": [], "corrections": [],
    }
    if verdict == "AUTO_FIX_APPLIED":
        report["corrections"] = [{"id": "FIX-1", "related_ids": [document["test_cases"][0]["case_id"]], "description": "Corrected revision.", "evidence": ["review"]}]
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        report["findings"] = [{"severity": "BLOCKING", "code": "REWORK", "message": "Rework is required.", "evidence": ["review"], "related_ids": [document["test_cases"][0]["case_id"]]}]
    return {"schema_version": "3.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report, **({"successor_document": successor} if successor is not None else {})}, "warnings": []}


class OrchestrationV3Tests(unittest.TestCase):
    """The facade must preserve published audit state while selecting one revision."""

    def test_accepted_publishes_selects_then_verifies_candidate(self) -> None:
        # Break caught: selecting before publication, or returning a receipt that was not verified.
        from tools.orchestrate_test_case_revision import orchestrate_revision

        document = canonical_document()
        calls: list[str] = []

        def publisher(value: dict, directory: str, profile: str) -> Receipt:
            calls.append("publish")
            return Receipt(value["document_id"], value["revision"], profile, str(Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}.json"), str(Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}.md"), str(Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}.zephyr-scale.csv"), document_sha256(value), "sha256:" + "a" * 64, "sha256:" + "b" * 64)

        def verifier(value: dict, directory: str, profile: str) -> Receipt:
            calls.append("verify")
            return publisher(value, directory, profile)

        with tempfile.TemporaryDirectory() as directory:
            result = orchestrate_revision(document, review_for(document), directory, "zephyr-scale-step-row-24-v1", publisher=publisher, verifier=verifier)

        self.assertEqual("EFFECTIVE_SELECTED", result.status)
        self.assertEqual(document_sha256(document), result.effective_bundle_receipt.document_sha256)
        self.assertEqual(["publish", "verify", "publish"], calls)

    def test_auto_fix_keeps_candidate_bundle_and_selects_successor(self) -> None:
        # Break caught: overwriting the candidate bundle or selecting before full successor validation.
        from tools.orchestrate_test_case_revision import orchestrate_revision

        candidate = canonical_document()
        successor = copy.deepcopy(candidate)
        successor["revision"] += 1
        successor["parent_sha256"] = document_sha256(candidate)
        published: list[str] = []

        def receipt(value: dict, directory: str, profile: str) -> Receipt:
            published.append(f"{value['document_id']}:{value['revision']}")
            base = Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}"
            return Receipt(value["document_id"], value["revision"], profile, str(base) + ".json", str(base) + ".md", str(base) + ".zephyr-scale.csv", document_sha256(value), "sha256:" + "a" * 64, "sha256:" + "b" * 64)

        with tempfile.TemporaryDirectory() as directory:
            result = orchestrate_revision(candidate, review_for(candidate, "AUTO_FIX_APPLIED", successor), directory, "zephyr-scale-step-row-24-v1", publisher=receipt, verifier=receipt)

        self.assertEqual("EFFECTIVE_SELECTED", result.status)
        self.assertEqual(2, result.effective_document["revision"])
        self.assertEqual([f"{candidate['document_id']}:1", f"{candidate['document_id']}:2", f"{candidate['document_id']}:2"], published)

    def test_rework_keeps_candidate_receipt_without_selecting_or_verifying(self) -> None:
        # Break caught: allowing a rework review to reach downstream selection or verification.
        from tools.orchestrate_test_case_revision import orchestrate_revision

        document = canonical_document()
        calls: list[str] = []

        def publisher(value: dict, directory: str, profile: str) -> Receipt:
            calls.append("publish")
            base = Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}"
            return Receipt(value["document_id"], value["revision"], profile, str(base) + ".json", str(base) + ".md", str(base) + ".zephyr-scale.csv", document_sha256(value), "sha256:" + "a" * 64, "sha256:" + "b" * 64)

        with tempfile.TemporaryDirectory() as directory:
            result = orchestrate_revision(document, review_for(document, "ТРЕБУЕТ ДОРАБОТКИ"), directory, "zephyr-scale-step-row-24-v1", publisher=publisher, verifier=lambda *_: self.fail("rework must not verify"))

        self.assertEqual("REWORK", result.status)
        self.assertIsNone(result.effective_document)
        self.assertEqual(["publish"], calls)

    def test_real_auto_fix_publishes_six_immutable_projection_files(self) -> None:
        # Break caught: successor publication overwrites candidate or returns an unchecked effective digest.
        from tools.orchestrate_test_case_revision import orchestrate_revision

        candidate = canonical_document()
        successor = copy.deepcopy(candidate)
        successor["revision"] = 2
        successor["parent_sha256"] = document_sha256(candidate)
        with tempfile.TemporaryDirectory() as directory:
            result = orchestrate_revision(candidate, review_for(candidate, "AUTO_FIX_APPLIED", successor), directory, "zephyr-scale-step-row-24-v1")
            paths = sorted(path.name for path in Path(directory).iterdir())
        self.assertEqual(6, len(paths))
        self.assertEqual(document_sha256(successor), result.effective_bundle_receipt.document_sha256)

    def test_receipt_spoof_and_verification_mismatch_retain_candidate_receipt(self) -> None:
        # Break caught: accepting a lookalike receipt or losing the durable candidate audit receipt on later failure.
        from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

        document = canonical_document()
        def good(value: dict, directory: str, profile: str) -> Receipt:
            base = Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}"
            return Receipt(value["document_id"], value["revision"], profile, str(base) + ".json", str(base) + ".md", str(base) + ".zephyr-scale.csv", document_sha256(value), "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OrchestrationError) as caught:
                orchestrate_revision(document, review_for(document), directory, "zephyr-scale-step-row-24-v1", publisher=good, verifier=lambda *_: Receipt(document["document_id"], 1, "zephyr-scale-step-row-24-v1", "x", "y", "z", document_sha256(document), "sha256:" + "a" * 64, "sha256:" + "b" * 64))
        self.assertIsNotNone(caught.exception.candidate_bundle_receipt)
        self.assertEqual("ORCHESTRATION_RECEIPT", caught.exception.code)

    def test_v3_output_schema_is_closed_and_fixtures_validate(self) -> None:
        # Break caught: accepting V2 carrier fields or an unclosed final artifact.
        root = Path(__file__).resolve().parents[1]
        schema = root / "schemas" / "orchestrator-output.schema.json"
        for name in ("accepted-orchestrator-output.json", "not-runnable-orchestrator-output.json"):
            value = __import__("json").loads((root / "skills" / "orchestrate" / "assets" / "orchestration-fixtures" / name).read_text(encoding="utf-8"))
            self.assertEqual([], schema_diagnostics(value, schema, root))
            value["artifacts"]["orchestration_result"]["method_id"] = "METHOD-v2"
            self.assertTrue(schema_diagnostics(value, schema, root))

    def test_finalizer_uses_validated_trace_as_authoritative_status(self) -> None:
        # Break caught: finalizing a source-mismatched review or inventing status instead of using Task 11 trace.
        from tests.test_build_trace_v3 import generated, run_result
        from tools.build_trace_document import build_trace
        from tools.orchestrate_test_case_revision import OrchestrationError, finalize_orchestration

        document = canonical_document(); automation = generated(document); run = run_result(document, automation)
        trace = build_trace(document, automation, run); source = {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(document)}
        review = {"schema_version":"3.0.0","stage":"autotest-reviewer","warnings":[],"artifacts":{"autotest_review":{"source":source,"verdict":"ПРИНЯТО","reviewed_symbol_pairs":[{"file_id":"FILE-item","symbol_id":"SYMBOL-item"}],"findings":[],"corrections":[]}}}
        receipt = Receipt(document["document_id"], 1, "zephyr-scale-step-row-24-v1", "one.json", "one.md", "one.csv", document_sha256(document), "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        result = finalize_orchestration(document, receipt, automation, review, run, trace)
        self.assertEqual("PASS", result["artifacts"]["orchestration_result"]["final_status"])
        review["artifacts"]["autotest_review"]["source"]["revision"] = 2
        with self.assertRaises(OrchestrationError):
            finalize_orchestration(document, receipt, automation, review, run, trace)

    def test_contract_checker_rejects_exact_v3_route_and_step_mutations(self) -> None:
        # Break caught: an exact V3 branch or stage carrier silently drifts while IDs still exist.
        from tools.contract_check import validate_pipeline_contract

        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
        mutations = (
            (lambda value: value["transitions"].__setitem__(3, {"from":"autotest-reviewer","when":{"review_verdict":"ПРИНЯТО","automation_status":"BLOCKED"},"transform":"run_tests"})),
            (lambda value: value["transitions"].__setitem__(8, {"from":"run-tests","when":{"execution_verdict":"FAIL"},"transform":"stop_failed"})),
            (lambda value: value["steps"][-1].__setitem__("accepts", ["raw_content"])),
        )
        for mutate in mutations:
            changed = copy.deepcopy(contract); mutate(changed)
            self.assertEqual("failed", validate_pipeline_contract(changed, root)["status"])

    def test_pipeline_traceability_is_the_exact_atomic_chain(self) -> None:
        # Break caught: generated docs cannot recover the full V3 trace when the registry stores a shorthand chain.
        from tools.contract_check import validate_pipeline_contract

        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
        expected = ["requirement", "case", "step", "expectation", "assertion", "file", "symbol", "current_run_evidence"]
        self.assertEqual(expected, contract["traceability"])
        changed = copy.deepcopy(contract)
        changed["traceability"] = ["requirement", "atomic_symbol_pair", "runtime_evidence"]
        self.assertEqual("failed", validate_pipeline_contract(changed, root)["status"])

    def test_output_schema_rejects_inconsistent_run_and_final_status_branches(self) -> None:
        # Break caught: V3 consumers accept a run/null/final-status combination no authoritative trace can own.
        root = Path(__file__).resolve().parents[1]
        schema = root / "schemas" / "orchestrator-output.schema.json"
        fixture = json.loads((root / "skills" / "orchestrate" / "assets" / "orchestration-fixtures" / "accepted-orchestrator-output.json").read_text(encoding="utf-8"))
        rows = fixture["artifacts"]["orchestration_result"]
        mutations = (
            lambda value: value.__setitem__("final_status", "NOT_RUNNABLE"),
            lambda value: (value.__setitem__("run_source", None), value.__setitem__("run_verdict", None)),
            lambda value: (value.__setitem__("run_verdict", "FAIL"), value.__setitem__("final_status", "PASS")),
            lambda value: (value.__setitem__("automation_status", "GENERATED"), value.__setitem__("run_source", None), value.__setitem__("run_verdict", None), value.__setitem__("final_status", "BLOCKED")),
        )
        for mutate in mutations:
            changed = copy.deepcopy(fixture); mutate(changed["artifacts"]["orchestration_result"])
            self.assertTrue(schema_diagnostics(changed, schema, root))

    def test_malformed_successor_receipt_is_retained_for_recovery(self) -> None:
        # Break caught: a durable successor publication is erased from recovery state merely because its receipt mismatches.
        from tools.orchestrate_test_case_revision import OrchestrationError, orchestrate_revision

        candidate = canonical_document(); successor = copy.deepcopy(candidate)
        successor["revision"] = 2; successor["parent_sha256"] = document_sha256(candidate)
        returned: Receipt | None = None

        def publisher(value: dict, directory: str, profile: str) -> Receipt:
            nonlocal returned
            base = Path(directory).resolve() / f"{value['document_id']}.r{value['revision']}"
            receipt = Receipt(value["document_id"], value["revision"], profile, str(base) + ".json", str(base) + ".md", str(base) + ".zephyr-scale.csv", document_sha256(value), "sha256:" + "a" * 64, "sha256:" + "b" * 64)
            if value["revision"] == 2:
                returned = Receipt(receipt.document_id, 99, receipt.csv_profile, receipt.json_path, receipt.markdown_path, receipt.csv_path, receipt.document_sha256, receipt.markdown_sha256, receipt.csv_sha256)
                return returned
            return receipt

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OrchestrationError) as caught:
                orchestrate_revision(candidate, review_for(candidate, "AUTO_FIX_APPLIED", successor), directory, "zephyr-scale-step-row-24-v1", publisher=publisher)
        self.assertEqual(returned, caught.exception.successor_bundle_receipt)
        self.assertIsNot(returned, caught.exception.successor_bundle_receipt)
        with self.assertRaises(AttributeError):
            caught.exception.successor_bundle_receipt.revision = 3
