from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from tests.fixture_factory import canonical_document
from tools.baseline_lifecycle import advance_baseline, bind_effective_baseline, validate_baseline_receipt
from tools.document_delta import (
    DocumentDeltaError, apply_document_delta, delta_application_receipt, unchanged_document_selection,
)
from tools.flow_artifacts import artifact_sha256
from tools.publish_test_case_bundle import Receipt
from tools.revision_selection import SelectionError, select_unchanged_document
from tools.schema_validation import schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]


def _receipt_and_context(*, retired: list[dict] | None = None) -> tuple[dict, dict]:
    retired = retired or []
    change = True
    fragment_id = "FRAGMENT-" + "a" * 64
    receipt = {
        "schema_version": "2.0.0", "artifact": "behavior-context-receipt", "run_mode": "CHANGE_SET",
        "selected_module": "root", "baseline_receipt_sha256": "sha256:" + "b" * 64,
        "scope_receipt_sha256": "sha256:" + "c" * 64, "authorized_behavior_sources_sha256": "sha256:" + "d" * 64,
        "context_plan_sha256": "sha256:" + "e" * 64, "promotion_sha256s": [], "unchanged_source_bindings": [],
        "fragment_registry": ([{"fragment_id": fragment_id, "source_id": "SOURCE-retired", "item_id": "ITEM-000001", "effect": "retired"}] if change else []),
        "source_outcomes": [], "changed_requirement_ids": [], "retired_requirements": retired,
        "assurance": {"review_mode": "SEQUENTIAL", "independent_promotion_count": 0, "sequential_promotion_count": 0, "reworked_batch_count": 0},
    }
    context = {
        "schema_version": "1.0.0", "artifact": "changed-behavior-context", "run_mode": "CHANGE_SET",
        "source": {"behavior_context_receipt_sha256": artifact_sha256(receipt), "baseline_context_sha256": "sha256:" + "f" * 64},
        "requirements": [], "requirement_sources": [], "stable_requirement_links": [], "retired_requirements": retired,
    }
    return receipt, context


def _delta(document: dict, receipt: dict, context: dict) -> dict:
    return {
        "schema_version": "1.0.0", "artifact": "canonical-document-delta", "document_id": document["document_id"],
        "baseline_document_sha256": artifact_sha256(document), "baseline_revision": document["revision"], "target_revision": document["revision"],
        "source": {"behavior_context_receipt_sha256": artifact_sha256(receipt), "changed_behavior_context_sha256": artifact_sha256(context)},
        "metadata": copy.deepcopy(document["metadata"]),
        "operation_capabilities": {"reused_ids": [row["capability_id"] for row in document["operation_capabilities"]], "added": [], "modified": [], "retired": []},
        "requirements": {"reused_ids": [row["requirement_id"] for row in document["requirements"]], "added": [], "modified": [], "retired": []},
        "test_cases": {"reused_ids": [row["case_id"] for row in document["test_cases"]], "added": [], "modified": [], "retired": []},
    }


def _two_case_document() -> dict:
    document = canonical_document()
    requirement = copy.deepcopy(document["requirements"][0]); requirement.update({"requirement_id": "REQ-second", "display_order": 2, "text": "Second items can be read."})
    case = copy.deepcopy(document["test_cases"][0]); case.update({"case_id": "TC-second", "display_order": 2, "requirement_ids": ["REQ-second"], "title": "Read a second item", "objective": "Verify the second synthetic read."})
    step = case["steps"][0]; step.update({"step_id": "STEP-second"})
    step["inputs"][0]["input_id"] = "INPUT-second"
    step["expectations"][0]["expectation_id"] = "EXP-second"
    step["expectations"][0]["assertions"][0]["assertion_id"] = "ASSERT-second"
    document["requirements"].append(requirement); document["test_cases"].append(case)
    return document


class DocumentDeltaTests(unittest.TestCase):
    def test_delta_schema_closes_source_baseline_revision_and_collections(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        self.assertEqual([], schema_diagnostics(delta, ROOT / "schemas" / "canonical-document-delta.schema.json", ROOT))
        for mutate in (
            lambda value: value.__setitem__("extra", True),
            lambda value: value["source"].__setitem__("extra", True),
            lambda value: value["requirements"].__setitem__("extra", True),
        ):
            changed = copy.deepcopy(delta); mutate(changed)
            self.assertTrue(schema_diagnostics(changed, ROOT / "schemas" / "canonical-document-delta.schema.json", ROOT))

    def test_apply_delta_partitions_every_baseline_identity_once(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        delta["requirements"]["reused_ids"] = []
        with self.assertRaises(DocumentDeltaError) as caught:
            apply_document_delta(document, delta, receipt, context)
        self.assertIn("DOCUMENT_DELTA_IDENTITY", {row["code"] for row in caught.exception.diagnostics})

    def test_apply_delta_reuses_ids_and_materializes_full_document(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        replacement = copy.deepcopy(document["requirements"][0]); replacement["text"] = "Items can be read after a scoped change."
        context["requirements"] = [copy.deepcopy(replacement)]; context["requirement_sources"] = [{"requirement_id": replacement["requirement_id"], "source_ids": ["SOURCE-change"]}]
        receipt["changed_requirement_ids"] = [replacement["requirement_id"]]
        context["source"]["behavior_context_receipt_sha256"] = artifact_sha256(receipt)
        delta["source"] = {"behavior_context_receipt_sha256": artifact_sha256(receipt), "changed_behavior_context_sha256": artifact_sha256(context)}
        delta["requirements"]["reused_ids"] = []
        delta["requirements"]["modified"] = [{"requirement_id": replacement["requirement_id"], "baseline_object_sha256": artifact_sha256(document["requirements"][0]), "replacement": replacement}]
        delta["target_revision"] += 1
        applied = apply_document_delta(document, delta, receipt, context)
        self.assertEqual("CHANGED", applied.status)
        self.assertTrue(applied.publication_required)
        self.assertEqual(document["revision"] + 1, applied.candidate_document["revision"])
        self.assertEqual(document["requirements"][0]["requirement_id"], applied.candidate_document["requirements"][0]["requirement_id"])

    def test_every_capability_requirement_and_case_retirement_is_tombstoned(self) -> None:
        document = _two_case_document(); req = document["requirements"][1]; case = document["test_cases"][1]
        support = {"status": "NO_SURVIVING_SOURCE_SUPPORT", "surviving_source_ids": [], "retirement_fragment_ids": ["FRAGMENT-" + "a" * 64]}
        requirement_tombstone = {"object_kind": "requirement", "object_id": req["requirement_id"], "baseline_object_sha256": artifact_sha256(req), "reason": "REQUIREMENT_NO_LONGER_OBSERVABLE", "support_evidence": support}
        receipt, context = _receipt_and_context(retired=[requirement_tombstone]); delta = _delta(document, receipt, context)
        delta["requirements"] = {"reused_ids": [document["requirements"][0]["requirement_id"]], "added": [], "modified": [], "retired": [requirement_tombstone]}
        delta["test_cases"] = {"reused_ids": [document["test_cases"][0]["case_id"]], "added": [], "modified": [], "retired": [{"object_kind": "test_case", "object_id": case["case_id"], "baseline_object_sha256": artifact_sha256(case), "reason": "CASE_REQUIREMENTS_RETIRED", "support_evidence": {"status": "DEPENDENTS_RETIRED", "surviving_object_ids": []}}]}
        delta["target_revision"] += 1
        self.assertEqual("CHANGED", apply_document_delta(document, delta, receipt, context).status)
        invalid = copy.deepcopy(delta); invalid["requirements"]["retired"][0]["support_evidence"].pop("retirement_fragment_ids")
        self.assertTrue(schema_diagnostics(invalid, ROOT / "schemas" / "canonical-document-delta.schema.json", ROOT))

    def test_semantic_retirement_cannot_be_omitted_or_reused_by_delta(self) -> None:
        document = _two_case_document(); requirement = document["requirements"][1]
        tombstone = {
            "object_kind": "requirement", "object_id": requirement["requirement_id"],
            "baseline_object_sha256": artifact_sha256(requirement), "reason": "REQUIREMENT_NO_LONGER_OBSERVABLE",
            "support_evidence": {"status": "NO_SURVIVING_SOURCE_SUPPORT", "surviving_source_ids": [], "retirement_fragment_ids": ["FRAGMENT-" + "a" * 64]},
        }
        receipt, context = _receipt_and_context(retired=[tombstone]); delta = _delta(document, receipt, context)
        with self.assertRaises(DocumentDeltaError) as caught:
            apply_document_delta(document, delta, receipt, context)
        self.assertIn("DOCUMENT_DELTA_BINDING", {row["code"] for row in caught.exception.diagnostics})

    def test_zero_op_delta_returns_exact_baseline_without_publication(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        applied = apply_document_delta(document, delta, receipt, context)
        self.assertEqual("UNCHANGED", applied.status)
        self.assertFalse(applied.publication_required)
        self.assertEqual(artifact_sha256(document), artifact_sha256(applied.candidate_document))
        self.assertEqual("UNCHANGED", delta_application_receipt(applied)["status"])

    def test_metadata_only_delta_is_nonzero_and_sources_are_bound(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        delta["metadata"]["author"] = "SCOPED_AUTHOR"; delta["target_revision"] += 1
        self.assertTrue(apply_document_delta(document, delta, receipt, context).publication_required)
        broken = copy.deepcopy(delta); broken["source"]["changed_behavior_context_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(DocumentDeltaError) as caught:
            apply_document_delta(document, broken, receipt, context)
        self.assertIn("DOCUMENT_DELTA_BINDING", {row["code"] for row in caught.exception.diagnostics})

    def test_zero_op_selection_requires_minted_effective_baseline_and_emits_carrier(self) -> None:
        from tests import test_baseline_lifecycle as lifecycle

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = lifecycle.make_project(root)
            fixture = lifecycle.build_run(root / "run", identity, head, tree)
            advanced = advance_baseline({"project": project, "baseline_root": root / "baselines"}, lifecycle.persist_terminal(root / "run", fixture))
            baseline = validate_baseline_receipt(advanced["successor_baseline_receipt"], project, "root", fixture["projected"])
            effective = bind_effective_baseline(baseline, fixture["document"], lifecycle.bundle_for(fixture["document"]))
            receipt, context = _receipt_and_context(); applied = apply_document_delta(fixture["document"], _delta(fixture["document"], receipt, context), receipt, context)
            selected_document, selected_receipt = select_unchanged_document(applied, effective)
            self.assertEqual(artifact_sha256(fixture["document"]), artifact_sha256(selected_document))
            self.assertIsInstance(selected_receipt, Receipt)
            carrier = unchanged_document_selection(applied, effective)
            self.assertEqual([], schema_diagnostics(carrier, ROOT / "schemas" / "unchanged-document-selection.schema.json", ROOT))
            with self.assertRaises(SelectionError):
                select_unchanged_document(applied, {"effective_document": fixture["document"]})  # type: ignore[arg-type]
            for forged in (object.__new__(type(effective)), copy.copy(effective)):
                with self.subTest(forged=type(forged).__name__), self.assertRaises(SelectionError):
                    select_unchanged_document(applied, forged)

    def test_generator_v4_binds_run_mode_to_one_closed_branch(self) -> None:
        document = canonical_document(); receipt, context = _receipt_and_context(); delta = _delta(document, receipt, context)
        full = {"schema_version": "4.0.0", "stage": "tc-generator", "run_mode": "FULL", "artifacts": {"canonical_document": document}, "warnings": []}
        changed = {"schema_version": "4.0.0", "stage": "tc-generator", "run_mode": "CHANGE_SET", "artifacts": {"canonical_document_delta": delta}, "warnings": []}
        schema = ROOT / "schemas" / "tc-generator-output.schema.json"
        self.assertEqual([], schema_diagnostics(full, schema, ROOT)); self.assertEqual([], schema_diagnostics(changed, schema, ROOT))
        full["artifacts"]["canonical_document_delta"] = delta
        self.assertTrue(schema_diagnostics(full, schema, ROOT))


if __name__ == "__main__":
    unittest.main()
