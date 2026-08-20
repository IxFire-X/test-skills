"""Focused authority tests for the portable semantic promotion ledger."""

from __future__ import annotations

import copy
import hashlib
import unittest

from tools.batch_promotion import PromotionSnapshot, ReviewController, advance_promotion, record_promotion, start_promotion
from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def scope(mode: str) -> dict:
    return {
        "schema_version":"1.0.0", "artifact":"change-scope-receipt", "run_mode":mode,
        "baseline_receipt_sha256":digest("baseline") if mode == "CHANGE_SET" else None,
        "candidate_sha256":digest("scope-candidate"),
        "audit_sha256s":{"false_inclusion":digest("scope-false"), "omission":digest("scope-omission")},
        "change_input_sha256":digest("change") if mode == "CHANGE_SET" else None,
        "analytics_sha256":digest("analytics"), "included_source_ids":[], "included_test_symbol_pairs":[],
        "review_mode":"SEQUENTIAL", "independence_attestation_sha256":None,
    }


def plan(receipt: dict) -> dict:
    if receipt["run_mode"] == "FULL":
        return {"schema_version":"1.0.0", "selected_module":"root", "authorized_behavior_sources_sha256":digest("sources"), "batches":[{"batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "source_id":"REQ-one", "domain_key":"_supplied", "accounted_range":{"start":0,"end":1}, "read_range":{"start":0,"end":1}}]}]}
    return {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(receipt), "scope_candidate_sha256":digest("scope-candidate"), "selected_module":"root", "baseline_inventory_sha256":digest("baseline-inventory"), "current_inventory_sha256":digest("current-inventory"), "batches":[{"batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "change_id":None, "change_kind":"context", "baseline_source_id":None, "current_source_id":"REQ-one", "domain_key":"_supplied", "evidence_sides":[{"side":"after", "content_sha256":digest("source"), "accounted_range":{"start":0,"end":1}, "read_range":{"start":0,"end":1}}]}]}]}


def result(receipt: dict, value: dict) -> dict:
    if receipt["run_mode"] == "FULL":
        return {"schema_version":"1.0.0", "plan_sha256":artifact_sha256(value), "batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "outcome":"no_supported_observable_fact"}]}
    return {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(receipt), "plan_sha256":artifact_sha256(value), "batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "outcome":"supporting_context"}]}


def candidate(snapshot, nested: dict, request=None) -> dict:
    request = request or advance_promotion(snapshot).artifact
    return {
        "schema_version":"1.0.0", "artifact":"semantic-batch-candidate",
        "run_mode":request["run_mode"], "scope_receipt_sha256":request["scope_receipt_sha256"],
        "plan_sha256":request["plan_sha256"], "batch_id":request["batch_id"], "generation":request["generation"],
        "parent_candidate_sha256":request["parent_candidate_sha256"], "triggering_audit_sha256s":list(request["triggering_audit_sha256s"]),
        "result_schema_version":request["result_schema_version"], "result":nested,
    }


def audit(candidate_value: dict, kind: str, verdict: str = "ACCEPT", code: str | None = None) -> dict:
    findings = []
    if verdict == "REWORK":
        finding = {"code":code or ("BATCH_UNSUPPORTED_ROUTE" if kind == "false_claim" else "BATCH_PUBLIC_CONTRACT_OMITTED"), "candidate_pointer":"/result/items/0", "item_id":"ITEM-000001", "summary":"Supported evidence requires correction."}
        finding["finding_id"] = "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest()
        findings.append(finding)
    return {"schema_version":"1.0.0", "artifact":"semantic-batch-audit", "audit_kind":kind, "candidate_sha256":artifact_sha256(candidate_value), "batch_id":candidate_value["batch_id"], "generation":candidate_value["generation"], "verdict":verdict, "findings":findings}


class BatchPromotionTests(unittest.TestCase):
    def _accepted(self, mode="CHANGE_SET"):
        receipt = scope(mode); value = plan(receipt); snapshot = start_promotion(receipt, value)
        candidate_value = candidate(snapshot, result(receipt, value))
        snapshot = record_promotion(snapshot, candidate_value)
        false = audit(candidate_value, "false_claim")
        snapshot = record_promotion(snapshot, false)
        omission = audit(candidate_value, "omission")
        snapshot = record_promotion(snapshot, omission)
        return receipt, value, snapshot, candidate_value, false, omission

    def test_full_and_change_set_require_exact_nested_versions(self) -> None:
        for mode in ("FULL", "CHANGE_SET"):
            with self.subTest(mode=mode):
                receipt = scope(mode); value = plan(receipt); snapshot = start_promotion(receipt, value)
                nested = result(receipt, value)
                wrong = copy.deepcopy(nested); wrong["schema_version"] = "2.0.0" if mode == "FULL" else "1.0.0"
                with self.assertRaisesRegex(FlowError, "BATCH_PROMOTION_MODE"):
                    record_promotion(snapshot, candidate(snapshot, wrong))
                accepted = record_promotion(snapshot, candidate(snapshot, nested))
                self.assertEqual("RUN_BATCH_FALSE_CLAIM_AUDIT", advance_promotion(accepted).kind)

    def test_both_accepting_audits_promote_same_generation(self) -> None:
        _, _, snapshot, _, _, _ = self._accepted()
        action = advance_promotion(snapshot)
        self.assertEqual(("PROMOTE_BATCH", "SEQUENTIAL", None), (action.kind, action.artifact["review_mode"], action.artifact["independence_attestation_sha256"]))
        self.assertEqual("COMPLETE", advance_promotion(action.next_snapshot).kind)

    def test_false_route_and_omitted_public_contract_rework_seal_generation(self) -> None:
        for kind, code in (("false_claim", "BATCH_UNSUPPORTED_ROUTE"), ("omission", "BATCH_PUBLIC_CONTRACT_OMITTED")):
            with self.subTest(kind=kind):
                receipt = scope("CHANGE_SET"); value = plan(receipt); snapshot = start_promotion(receipt, value)
                first = candidate(snapshot, result(receipt, value)); snapshot = record_promotion(snapshot, first)
                if kind == "omission": snapshot = record_promotion(snapshot, audit(first, "false_claim"))
                snapshot = record_promotion(snapshot, audit(first, kind, "REWORK", code))
                request = advance_promotion(snapshot)
                self.assertEqual(("PRODUCE_BATCH_CANDIDATE", 2, artifact_sha256(first)), (request.kind, request.artifact["generation"], request.artifact["parent_candidate_sha256"]))
                with self.assertRaisesRegex(FlowError, "BATCH_PROMOTION_ORDER"):
                    record_promotion(snapshot, first)

    def test_audit_order_binding_and_forged_snapshot_are_rejected(self) -> None:
        receipt = scope("CHANGE_SET"); value = plan(receipt); snapshot = start_promotion(receipt, value)
        first = candidate(snapshot, result(receipt, value)); snapshot = record_promotion(snapshot, first)
        with self.assertRaisesRegex(FlowError, "BATCH_PROMOTION_BINDING"):
            record_promotion(snapshot, audit(first, "omission"))
        with self.assertRaisesRegex(FlowError, "BATCH_PROMOTION_BINDING"):
            advance_promotion(PromotionSnapshot(snapshot.inputs))

    def test_independent_assurance_requires_controller_minted_exact_proof(self) -> None:
        _, _, snapshot, first, false, omission = self._accepted()
        with self.assertRaisesRegex(FlowError, "PROMOTION_ASSURANCE"):
            advance_promotion(snapshot, ReviewController())
        controller = ReviewController.independent(producer_context_id="producer", false_claim_context_id="false", omission_context_id="omission", candidate=first, false_audit=false, omission_audit=omission, fresh=True, blind=True)
        promotion = advance_promotion(snapshot, controller).artifact
        self.assertEqual("INDEPENDENT", promotion["review_mode"])
        self.assertTrue(promotion["independence_attestation_sha256"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
