"""Focused V2 side-authority tests."""
from __future__ import annotations

import hashlib
import json
import copy
import unittest
from pathlib import Path

from tools.behavior_context_planning import build_change_context_plan, stable_change_fragment_id, validate_change_batch_result
from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes
from tools.schema_validation import schema_diagnostics
from tools.test_classification import TestClassificationError


ROOT = Path(__file__).resolve().parents[1]


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def source(path: str, value: bytes) -> dict[str, str]:
    return {"source_id": "SOURCE-" + hashlib.sha256(b"product_file\0" + path.encode()).hexdigest(), "kind": "product_file", "path": path, "content_digest": digest(value)}


def change_side(path: str, value: bytes) -> dict[str, object]:
    return {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": path, "content_sha256": digest(value)})).hexdigest(), "content_sha256": digest(value), "size_bytes": len(value), "text": True}


class ChangeContextV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.before, self.after = b"before\n", b"after\n"
        self.base, self.current = source("src/a.py", self.before), source("src/a.py", self.after)
        change = {"kind": "modified", "path": "src/a.py", "before": change_side("src/a.py", self.before), "after": change_side("src/a.py", self.after)}
        change["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(change)).hexdigest()
        self.candidate = {"schema_version":"1.0.0", "artifact":"change-scope-candidate", "run_mode":"CHANGE_SET", "selected_module":"root", "baseline_receipt_sha256":digest(b"baseline"), "analytics_sha256":digest(b"analytics"), "change_input_sha256":digest(b"input"), "current_source_inventory_sha256":artifact_sha256({"module_id":"root", "sources":[self.current]}), "current_test_inventory_sha256":digest(b"tests"), "generation":1, "parent_candidate_sha256":None, "triggering_audit_sha256s":[], "changes":[change], "included_sources":[{"source_id":self.current["source_id"], "reason":"DIRECT_TEXT_CHANGE", "relation_ids":[]}], "included_test_symbols":[], "relations":[], "baseline_requirement_ids":[], "widening_level":"symbol"}
        self.receipt = {"schema_version":"1.0.0", "artifact":"change-scope-receipt", "run_mode":"CHANGE_SET", "baseline_receipt_sha256":digest(b"baseline"), "candidate_sha256":artifact_sha256(self.candidate), "audit_sha256s":{"false_inclusion":digest(b"false"), "omission":digest(b"omission")}, "change_input_sha256":digest(b"input"), "analytics_sha256":digest(b"analytics"), "included_source_ids":[self.current["source_id"]], "included_test_symbol_pairs":[], "review_mode":"SEQUENTIAL", "independence_attestation_sha256":None}
        self.resolver = lambda side, row: self.before if side == "before" else self.after

    def _plan(self, candidate=None, receipt=None, baseline=None, current=None, resolver=None):
        return build_change_context_plan(
            Path("."), {"id":"root", "paths":{"source":["src"]}},
            receipt or self.receipt, candidate or self.candidate,
            {"module_id":"root", "sources":baseline if baseline is not None else [self.base]},
            {"module_id":"root", "sources":current if current is not None else [self.current]},
            resolver or self.resolver,
        )

    def test_change_plan_v2_side_identity_range_and_order(self) -> None:
        plan = build_change_context_plan(Path("."), {"id":"root", "paths":{"source":["src"]}}, self.receipt, self.candidate, {"module_id":"root", "sources":[self.base]}, {"module_id":"root", "sources":[self.current]}, self.resolver)
        item = plan["batches"][0]["items"][0]
        self.assertEqual("2.0.0", plan["schema_version"])
        self.assertEqual(["before", "after"], [side["side"] for side in item["evidence_sides"]])
        self.assertEqual(self.candidate["changes"][0]["change_id"], item["change_id"])

    def test_change_result_v2_effect_requires_correct_evidence_sides(self) -> None:
        plan = build_change_context_plan(Path("."), {"id":"root", "paths":{"source":["src"]}}, self.receipt, self.candidate, {"module_id":"root", "sources":[self.base]}, {"module_id":"root", "sources":[self.current]}, self.resolver)
        item = plan["batches"][0]["items"][0]
        locators = [{"side":side["side"], "content_sha256":side["content_sha256"], "start_byte":side["accounted_range"]["start"], "end_byte":side["accounted_range"]["end"]} for side in item["evidence_sides"]]
        fragment = {"effect":"modified", "anchor_byte":0, "evidence_locators":locators}; fragment["fragment_id"] = stable_change_fragment_id(item, fragment)
        result = {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(self.receipt), "plan_sha256":artifact_sha256(plan), "batch_id":plan["batches"][0]["batch_id"], "items":[{"item_id":item["item_id"], "outcome":"changed_behavior_fragments", "behavior_fragments":[fragment]}]}
        self.assertEqual((), validate_change_batch_result(self.receipt, plan, result, self.resolver))
        result["items"][0]["behavior_fragments"][0]["evidence_locators"] = locators[:1]
        self.assertEqual("CHANGE_RESULT_SHAPE", validate_change_batch_result(self.receipt, plan, result, self.resolver)[0]["code"])

    def test_added_deleted_renamed_binary_and_context_are_closed_variants(self) -> None:
        """All change variants retain only their authoritative side(s)."""
        cases = (("added", None, self.current), ("deleted", self.base, None), ("renamed", self.base, source("src/b.py", self.after)), ("binary", None, source("src/a.py", b"\x00bin")))
        for kind, before, after in cases:
            with self.subTest(kind=kind):
                path = "src/b.py" if kind == "renamed" else "src/a.py"
                change = {"kind": kind, "path": path}
                if kind == "renamed": change = {"kind":"renamed","old_path":"src/a.py","new_path":"src/b.py","similarity_basis":"git"}
                if kind == "binary": change.update({"binary_change":"added", "before":None, "after":{"source_id":change_side(path, b"\x00bin")["source_id"],"content_sha256":digest(b"\x00bin"),"size_bytes":4,"text":False}})
                else:
                    if before: change["before"] = change_side("src/a.py", self.before)
                    if after: change["after"] = change_side(after["path"], self.after)
                change["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(change)).hexdigest()
                candidate = copy.deepcopy(self.candidate); candidate["changes"] = [change]
                selected = after or before; candidate["included_sources"] = [{"source_id":selected["source_id"],"reason":"ADDED_SOURCE" if kind == "added" else "DELETED_SOURCE" if kind == "deleted" else "RENAMED_SOURCE" if kind == "renamed" else "DIRECT_BINARY_CHANGE","relation_ids":[]}]
                current = [after] if after else [] ; baseline = [before] if before else []
                candidate["current_source_inventory_sha256"] = artifact_sha256({"module_id":"root","sources":current})
                receipt = copy.deepcopy(self.receipt); receipt["candidate_sha256"] = artifact_sha256(candidate); receipt["included_source_ids"] = [selected["source_id"]]
                resolver = lambda side, row, before=self.before, after=self.after: before if side == "before" else (b"\x00bin" if kind == "binary" else after)
                plan = build_change_context_plan(Path("."), {"id":"root","paths":{"source":["src"]}}, receipt, candidate, {"module_id":"root","sources":baseline}, {"module_id":"root","sources":current}, resolver)
                item = plan["batches"][0]["items"][0]
                self.assertEqual(kind, item["change_kind"])
                self.assertEqual([side for side in ("before","after") if (before if side == "before" else after)], [row["side"] for row in item["evidence_sides"]])

    def test_unequal_utf8_sides_are_independently_chunked_and_drift_rejects(self) -> None:
        """No zip can drop a chunk or hide a resolver-side digest mismatch."""
        self.before, self.after = "€".encode() * 25000, "€".encode() * 50000
        self.base, self.current = source("src/a.py", self.before), source("src/a.py", self.after)
        change = {"kind":"modified","path":"src/a.py","before":change_side("src/a.py",self.before),"after":change_side("src/a.py",self.after)}; change["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(change)).hexdigest()
        self.candidate["changes"]=[change]; self.candidate["current_source_inventory_sha256"] = artifact_sha256({"module_id":"root","sources":[self.current]}); self.candidate["included_sources"]=[{"source_id":self.current["source_id"],"reason":"DIRECT_TEXT_CHANGE","relation_ids":[]}]; self.receipt["candidate_sha256"]=artifact_sha256(self.candidate); self.receipt["included_source_ids"]=[self.current["source_id"]]
        plan = build_change_context_plan(Path("."), {"id":"root","paths":{"source":["src"]}}, self.receipt, self.candidate, {"module_id":"root","sources":[self.base]}, {"module_id":"root","sources":[self.current]}, self.resolver)
        sides = plan["batches"][0]["items"][0]["evidence_sides"]
        self.assertGreater(len([row for row in sides if row["side"] == "after"]), len([row for row in sides if row["side"] == "before"]))
        for row in sides:
            self.assertLessEqual(row["accounted_range"]["end"] - row["accounted_range"]["start"], 64 * 1024)
        with self.assertRaises(Exception):
            build_change_context_plan(Path("."), {"id":"root","paths":{"source":["src"]}}, self.receipt, self.candidate, {"module_id":"root","sources":[self.base]}, {"module_id":"root","sources":[self.current]}, lambda side, row: b"wrong")

    def test_context_source_is_deduplicated_and_cannot_promote_changes(self) -> None:
        context = source("src/context.py", self.before)
        candidate = copy.deepcopy(self.candidate)
        candidate["included_sources"].append({"source_id":context["source_id"], "reason":"DOMAIN_WIDENING", "relation_ids":[]})
        current = [self.current, context]
        candidate["current_source_inventory_sha256"] = artifact_sha256({"module_id":"root", "sources":current})
        receipt = copy.deepcopy(self.receipt)
        receipt["candidate_sha256"] = artifact_sha256(candidate)
        receipt["included_source_ids"] = [row["source_id"] for row in candidate["included_sources"]]
        resolver = lambda side, row: self.before if row["source_id"] == context["source_id"] or side == "before" else self.after
        plan = self._plan(candidate, receipt, [self.base, context], current, resolver)
        context_items = [batch["items"][0] for batch in plan["batches"] if batch["items"][0]["change_kind"] == "context"]
        self.assertEqual(1, len(context_items))
        item = context_items[0]
        result = {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(receipt), "plan_sha256":artifact_sha256(plan), "batch_id":plan["batches"][1]["batch_id"], "items":[{"item_id":item["item_id"], "outcome":"supporting_context"}]}
        self.assertEqual((), validate_change_batch_result(receipt, plan, result, resolver))
        result["items"][0]["outcome"] = "changed_behavior_fragments"
        result["items"][0]["behavior_fragments"] = [{"fragment_id":"FRAGMENT-" + "0" * 64, "effect":"modified", "anchor_byte":0, "evidence_locators":[]}]
        self.assertEqual("CHANGE_RESULT_SHAPE", validate_change_batch_result(receipt, plan, result, resolver)[0]["code"])

    def test_scope_candidate_and_included_order_are_exact_bindings(self) -> None:
        candidate = copy.deepcopy(self.candidate)
        candidate["analytics_sha256"] = digest(b"different")
        with self.assertRaises(TestClassificationError):
            self._plan(candidate=candidate)
        receipt = copy.deepcopy(self.receipt)
        receipt["included_source_ids"] = [self.current["source_id"], self.current["source_id"]]
        with self.assertRaises(TestClassificationError):
            self._plan(receipt=receipt)

    def test_result_rereads_all_chunks_and_preserves_drift_code(self) -> None:
        self.before, self.after = "€".encode() * 25000, "€".encode() * 50000
        self.base, self.current = source("src/a.py", self.before), source("src/a.py", self.after)
        change = {"kind":"modified", "path":"src/a.py", "before":change_side("src/a.py", self.before), "after":change_side("src/a.py", self.after)}
        change["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(change)).hexdigest()
        self.candidate["changes"] = [change]
        self.candidate["current_source_inventory_sha256"] = artifact_sha256({"module_id":"root", "sources":[self.current]})
        self.candidate["included_sources"] = [{"source_id":self.current["source_id"], "reason":"DIRECT_TEXT_CHANGE", "relation_ids":[]}]
        self.receipt["candidate_sha256"] = artifact_sha256(self.candidate)
        self.receipt["included_source_ids"] = [self.current["source_id"]]
        plan = self._plan()
        item = plan["batches"][0]["items"][0]
        locators = []
        for side_name in ("before", "after"):
            side = next(row for row in item["evidence_sides"] if row["side"] == side_name)
            locators.append({"side":side_name, "content_sha256":side["content_sha256"], "start_byte":side["accounted_range"]["start"], "end_byte":side["accounted_range"]["end"]})
        fragment = {"effect":"modified", "anchor_byte":locators[1]["start_byte"], "evidence_locators":locators}
        fragment["fragment_id"] = stable_change_fragment_id(item, fragment)
        result = {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(self.receipt), "plan_sha256":artifact_sha256(plan), "batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "outcome":"changed_behavior_fragments", "behavior_fragments":[fragment]}]}
        self.assertEqual((), validate_change_batch_result(self.receipt, plan, result, self.resolver))

        def drift(side, row):
            raise FlowError("CHANGE_SOURCE_DRIFT", "/target", "changed")
        self.assertEqual("CHANGE_SOURCE_DRIFT", validate_change_batch_result(self.receipt, plan, result, drift)[0]["code"])

    def test_v1_strictness_and_v2_kind_effect_anchor_rules(self) -> None:
        bad_plan = {"schema_version":"1.0.0", "selected_module":"root", "authorized_behavior_sources_sha256":digest(b"sources"), "batches":[{"evil":True}]}
        self.assertTrue(schema_diagnostics(bad_plan, ROOT / "schemas" / "behavior-context-plan.schema.json", ROOT))
        bad_result = {"schema_version":"1.0.0", "plan_sha256":digest(b"plan"), "batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "outcome":"behavior_fragments", "behavior_fragments":[{"fragment_id":"FRAGMENT-" + "0" * 64, "actor":"   ", "operation":"op", "conditions":[], "outcomes":["ok"], "anchor_byte":0, "evidence_ranges":[{"start":0,"end":1}]}]}]}
        self.assertTrue(schema_diagnostics(bad_result, ROOT / "schemas" / "behavior-context-batch-result.schema.json", ROOT))

        plan = self._plan()
        item = plan["batches"][0]["items"][0]
        locators = []
        for side_name in ("before", "after"):
            side = next(row for row in item["evidence_sides"] if row["side"] == side_name)
            locators.append({"side":side_name, "content_sha256":side["content_sha256"], "start_byte":side["accounted_range"]["start"], "end_byte":side["accounted_range"]["end"]})
        fragment = {"effect":"modified", "anchor_byte":999, "evidence_locators":locators}
        fragment["fragment_id"] = stable_change_fragment_id(item, fragment)
        result = {"schema_version":"2.0.0", "scope_receipt_sha256":artifact_sha256(self.receipt), "plan_sha256":artifact_sha256(plan), "batch_id":"BATCH-000001", "items":[{"item_id":"ITEM-000001", "outcome":"changed_behavior_fragments", "behavior_fragments":[fragment]}]}
        self.assertEqual("CHANGE_SIDE_BINDING", validate_change_batch_result(self.receipt, plan, result, self.resolver)[0]["code"])
        v1_result = {"schema_version":"1.0.0", "plan_sha256":digest(b"plan"), "batch_id":"BATCH-000001", "items":[]}
        self.assertEqual("CHANGE_RESULT_SHAPE", validate_change_batch_result(self.receipt, plan, v1_result, self.resolver)[0]["code"])

    def test_supplied_requirement_is_a_valid_context_source(self) -> None:
        supplied = {"source_id":"REQ-analytics", "kind":"supplied_requirement", "content_digest":digest(b"requirement")}
        candidate = copy.deepcopy(self.candidate)
        candidate["changes"] = []
        candidate["included_sources"] = [{"source_id":supplied["source_id"], "reason":"DOMAIN_WIDENING", "relation_ids":[]}]
        candidate["current_source_inventory_sha256"] = artifact_sha256({"module_id":"root", "sources":[supplied]})
        receipt = copy.deepcopy(self.receipt)
        receipt["candidate_sha256"] = artifact_sha256(candidate)
        receipt["included_source_ids"] = [supplied["source_id"]]
        plan = self._plan(candidate, receipt, [supplied], [supplied], lambda side, row: b"requirement")
        item = plan["batches"][0]["items"][0]
        self.assertEqual(("context", "_supplied", supplied["source_id"]), (item["change_kind"], item["domain_key"], item["current_source_id"]))


if __name__ == "__main__":
    unittest.main()
