"""Focused V6 public-boundary regression checks."""

from __future__ import annotations

import inspect
import json
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from tools.batch_promotion import PromotionEvidence, advance_promotion, record_promotion, start_promotion
from tools.behavior_context_planning import (
    ComposedBehaviorContext,
    build_change_context_plan,
    build_context_plan,
    compose_behavior_context,
    stable_change_fragment_id,
    stable_fragment_id,
    validate_composed_behavior_context,
)
from tools.flow_artifacts import artifact_sha256
from tools.baseline_lifecycle import scope_predecessor_projection
from tools.schema_validation import schema_diagnostics
from tools.test_classification import TestClassificationError, load_validated_behavior_context


ROOT = Path(__file__).resolve().parents[1]
V6 = ROOT / "tests" / "fixtures" / "stages" / "v6"


def digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain(item) for item in value]
    return value


def scope(source_ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "artifact": "change-scope-receipt", "run_mode": "FULL",
        "baseline_receipt_sha256": None, "candidate_sha256": digest("scope-candidate"),
        "audit_sha256s": {"false_inclusion": digest("scope-false"), "omission": digest("scope-omission")},
        "change_input_sha256": None, "analytics_sha256": digest("analytics"),
        "included_source_ids": source_ids, "included_test_symbol_pairs": [],
        "review_mode": "SEQUENTIAL", "independence_attestation_sha256": None,
    }


def inventories(values: list[tuple[str, bytes]]) -> tuple[dict[str, Any], dict[str, bytes]]:
    sources = [{"source_id": source_id, "kind": "supplied_requirement", "content_digest": digest(content)} for source_id, content in values]
    authorized = {"module_id": "root", "sources": sources}
    technical = {"module_id": "root", "test_roots": [], "files": [], "symbols": []}
    return ({"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {
        "authorized_behavior_sources": authorized, "authorized_behavior_sources_sha256": artifact_sha256(authorized),
        "technical_test_inventory": technical, "technical_test_inventory_sha256": artifact_sha256(technical),
    }, "warnings": []}, dict(values))


def candidate(snapshot: Any, result: dict[str, Any]) -> dict[str, Any]:
    request = advance_promotion(snapshot).artifact
    return {
        "schema_version": "1.0.0", "artifact": "semantic-batch-candidate", "run_mode": request["run_mode"],
        "scope_receipt_sha256": request["scope_receipt_sha256"], "plan_sha256": request["plan_sha256"],
        "batch_id": request["batch_id"], "generation": request["generation"],
        "parent_candidate_sha256": request["parent_candidate_sha256"],
        "triggering_audit_sha256s": list(request["triggering_audit_sha256s"]),
        "result_schema_version": request["result_schema_version"], "result": result,
    }


def audit(value: dict[str, Any], kind: str) -> dict[str, Any]:
    return {"schema_version": "1.0.0", "artifact": "semantic-batch-audit", "audit_kind": kind,
            "candidate_sha256": artifact_sha256(value), "batch_id": value["batch_id"],
            "generation": value["generation"], "verdict": "ACCEPT", "findings": []}


def promoted_full(plan: dict[str, Any], receipt: dict[str, Any], semantics: list[dict[str, Any] | None]) -> tuple[PromotionEvidence, ...]:
    snapshot = start_promotion(receipt, plan)
    evidence: list[PromotionEvidence] = []
    for batch, semantic in zip(plan["batches"], semantics):
        item = batch["items"][0]
        row: dict[str, Any] = {"item_id": item["item_id"], "outcome": "no_supported_observable_fact"}
        if semantic is not None:
            evidence_range = {"start": item["accounted_range"]["start"], "end": item["accounted_range"]["end"]}
            fragment = {**semantic, "anchor_byte": item["accounted_range"]["start"], "evidence_ranges": [evidence_range]}
            fragment["fragment_id"] = stable_fragment_id(item, fragment)
            row = {"item_id": item["item_id"], "outcome": "behavior_fragments", "behavior_fragments": [fragment]}
        result = {"schema_version": "1.0.0", "plan_sha256": artifact_sha256(plan), "batch_id": batch["batch_id"], "items": [row]}
        proposed = candidate(snapshot, result)
        snapshot = record_promotion(snapshot, proposed)
        false = audit(proposed, "false_claim"); snapshot = record_promotion(snapshot, false)
        omission = audit(proposed, "omission"); snapshot = record_promotion(snapshot, omission)
        promoted = advance_promotion(snapshot)
        evidence.append(PromotionEvidence((proposed, false, omission), promoted.artifact))
        snapshot = promoted.next_snapshot
    return tuple(evidence)


def full_flow(values: list[tuple[str, bytes]], semantics: list[dict[str, Any] | None] | None = None):
    inventory, supplied = inventories(values)
    module = {"id": "root", "paths": {"source": []}}
    authorized = inventory["artifacts"]["authorized_behavior_sources"]
    plan = plain(build_context_plan(ROOT, module, authorized, supplied))
    receipt = scope([row["source_id"] for row in authorized["sources"]])
    semantics = semantics or [{"actor": "user", "operation": f"does {index}", "conditions": [], "outcomes": [f"result {index}"]} for index in range(1, len(plan["batches"]) + 1)]
    promotions = promoted_full(plan, receipt, semantics)
    resolver = lambda side, source: supplied[source["source_id"]]
    composed = compose_behavior_context(ROOT, module, inventory, receipt, plan, promotions, None, resolver)
    return module, inventory, supplied, receipt, plan, promotions, composed


def product_source(path: str, value: bytes) -> dict[str, str]:
    return {
        "source_id": "SOURCE-" + hashlib.sha256(b"product_file\0" + path.encode("utf-8")).hexdigest(),
        "kind": "product_file", "path": path, "content_digest": digest(value),
    }


def product_inventory(sources: list[dict[str, str]]) -> dict[str, Any]:
    authorized = {"module_id": "root", "sources": sources}
    technical = {"module_id": "root", "test_roots": [], "files": [], "symbols": []}
    return {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {
        "authorized_behavior_sources": authorized, "authorized_behavior_sources_sha256": artifact_sha256(authorized),
        "technical_test_inventory": technical, "technical_test_inventory_sha256": artifact_sha256(technical),
    }, "warnings": []}


def promoted_change(plan: dict[str, Any], receipt: dict[str, Any], semantics: list[dict[str, Any] | None], effect: str = "retired") -> tuple[PromotionEvidence, ...]:
    snapshot = start_promotion(receipt, plan)
    evidence: list[PromotionEvidence] = []
    for batch, semantic in zip(plan["batches"], semantics):
        item = batch["items"][0]
        row: dict[str, Any] = {"item_id": item["item_id"], "outcome": "no_changed_observable_fact"}
        if semantic is not None:
            locators = [{"side": side["side"], "content_sha256": side["content_sha256"], "start_byte": side["accounted_range"]["start"], "end_byte": side["accounted_range"]["end"]} for side in item["evidence_sides"] if effect != "retired" or side["side"] == "before"]
            fragment = {"effect": effect, **semantic, "anchor_byte": locators[-1]["start_byte"], "evidence_locators": locators}
            fragment["fragment_id"] = stable_change_fragment_id(item, fragment)
            row = {"item_id": item["item_id"], "outcome": "changed_behavior_fragments", "behavior_fragments": [fragment]}
        result = {"schema_version": "2.0.0", "scope_receipt_sha256": artifact_sha256(receipt), "plan_sha256": artifact_sha256(plan), "batch_id": batch["batch_id"], "items": [row]}
        proposed = candidate(snapshot, result)
        snapshot = record_promotion(snapshot, proposed)
        false = audit(proposed, "false_claim"); snapshot = record_promotion(snapshot, false)
        omission = audit(proposed, "omission"); snapshot = record_promotion(snapshot, omission)
        promoted = advance_promotion(snapshot)
        evidence.append(PromotionEvidence((proposed, false, omission), promoted.artifact))
        snapshot = promoted.next_snapshot
    return tuple(evidence)


def minted_change_predecessor(project: Path, source_values: list[tuple[str, bytes]], semantic: dict[str, Any] | list[dict[str, Any] | None] | None):
    """Reuse the lifecycle fixture to issue the real predecessor capability."""
    from tests import test_baseline_lifecycle as lifecycle
    from tools.baseline_lifecycle import advance_baseline, bind_scope_predecessor, validate_baseline_receipt

    sources = [product_source(path, value) for path, value in source_values]
    for path, value in source_values:
        target = project / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(value)
    lifecycle.git(project, "add", "src")
    lifecycle.git(project, "commit", "-m", "baseline behavior")
    inventory = product_inventory(sources)
    module = {"id": "root", "paths": {"source": ["src"]}}
    plan = plain(build_context_plan(project, module, inventory["artifacts"]["authorized_behavior_sources"]))
    full_receipt = scope([row["source_id"] for row in sources])
    promotions = promoted_full(plan, full_receipt, semantic if isinstance(semantic, list) else [semantic] * len(plan["batches"]))
    resolver = lambda side, row: next(value for path, value in source_values if row["path"] == path)
    composed = compose_behavior_context(project, module, inventory, full_receipt, plan, promotions, None, resolver)
    run_root = project.parent / "v6-run"
    fixture = lifecycle.build_run(run_root, lifecycle.repository_id(project), lifecycle.git(project, "rev-parse", "HEAD"), lifecycle.git(project, "rev-parse", "HEAD^{tree}"))
    case = lifecycle.BaselineLifecycleTests()
    def mutate(values: dict[str, dict[str, Any]]) -> None:
        values["technical_test_inventory"] = plain(inventory)
        values["authorized_behavior_sources"] = plain(inventory)
        values["change_scope_receipt"] = plain(full_receipt)
        values["managed_behavior_context"] = plain(composed.context)
        values["behavior_source_accounting"] = plain(composed.context)
        values["changed_behavior_context"] = plain(composed.changed_behavior_context)
        values["behavior_context_receipt"] = plain(composed.receipt)
    case.rewrite_prefix_graph(run_root, fixture, mutate)
    advanced = advance_baseline({"project": project, "baseline_root": project.parent / "v6-baselines"}, lifecycle.persist_terminal(run_root, fixture))
    baseline = validate_baseline_receipt(advanced["successor_baseline_receipt"], project, "root", fixture["projected"])
    predecessor = bind_scope_predecessor(baseline, inventory, plain(composed.context), plain(composed.receipt))
    return module, inventory, sources, composed, predecessor


def retired_change_flow(project: Path, source_values: list[tuple[str, bytes]], baseline_semantic: dict[str, Any], current_semantic: dict[str, Any] | None, effect: str = "retired", change_kind: str = "modified"):
    module, baseline_inventory, baseline_sources, baseline, predecessor = minted_change_predecessor(project, source_values, baseline_semantic)
    changed_path, old_value = source_values[0]
    new_value = old_value + b"changed\n"
    current_path = "src/renamed.py" if change_kind == "renamed" else changed_path
    (project / current_path).write_bytes(new_value)
    current_sources = [product_source(current_path if path == changed_path else path, new_value if path == changed_path else value) for path, value in source_values]
    current_inventory = product_inventory(current_sources)
    before = {"source_id": baseline_sources[0]["source_id"], "content_sha256": baseline_sources[0]["content_digest"], "size_bytes": len(old_value), "text": True}
    after = {"source_id": current_sources[0]["source_id"], "content_sha256": current_sources[0]["content_digest"], "size_bytes": len(new_value), "text": True}
    change = ({"kind": "renamed", "old_path": changed_path, "new_path": current_path, "similarity_basis": "git", "before": before, "after": after}
              if change_kind == "renamed" else {"kind": "modified", "path": changed_path, "before": before, "after": after})
    change["change_id"] = "CHANGE-" + hashlib.sha256(json.dumps(change, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    candidate_value = {"schema_version": "1.0.0", "artifact": "change-scope-candidate", "run_mode": "CHANGE_SET", "selected_module": "root", "baseline_receipt_sha256": scope_predecessor_projection(predecessor)["receipt_sha256"], "analytics_sha256": digest("analytics"), "change_input_sha256": digest("input"), "current_source_inventory_sha256": artifact_sha256({"module_id": "root", "sources": current_sources}), "current_test_inventory_sha256": digest("tests"), "generation": 1, "parent_candidate_sha256": None, "triggering_audit_sha256s": [], "changes": [change], "included_sources": [{"source_id": current_sources[0]["source_id"], "reason": "DIRECT_TEXT_CHANGE", "relation_ids": []}], "included_test_symbols": [], "relations": [], "baseline_requirement_ids": [row["requirement_id"] for row in baseline.managed_behavior_context["requirements"]], "widening_level": "symbol"}
    change_receipt = {"schema_version": "1.0.0", "artifact": "change-scope-receipt", "run_mode": "CHANGE_SET", "baseline_receipt_sha256": candidate_value["baseline_receipt_sha256"], "candidate_sha256": artifact_sha256(candidate_value), "audit_sha256s": {"false_inclusion": digest("false"), "omission": digest("omission")}, "change_input_sha256": digest("input"), "analytics_sha256": digest("analytics"), "included_source_ids": [current_sources[0]["source_id"]], "included_test_symbol_pairs": [], "review_mode": "SEQUENTIAL", "independence_attestation_sha256": None}
    resolver = lambda side, row: old_value if side == "before" else new_value if row["source_id"] == current_sources[0]["source_id"] else next(value for path, value in source_values if row["path"] == path)
    plan = plain(build_change_context_plan(project, module, change_receipt, candidate_value, {"module_id": "root", "sources": baseline_sources}, {"module_id": "root", "sources": current_sources}, resolver))
    promotions = promoted_change(plan, change_receipt, [current_semantic], effect)
    return module, current_inventory, change_receipt, plan, promotions, {"predecessor": predecessor, "context": baseline.context, "receipt": baseline.receipt}, resolver


class BehaviorContextV6Tests(unittest.TestCase):
    def test_public_composer_has_no_unaudited_context_proposal_parameter(self) -> None:
        from tools.behavior_context_planning import compose_behavior_context

        self.assertEqual(
            ("project", "module", "inventories", "scope_receipt", "plan", "promotions", "baseline_context", "byte_resolver"),
            tuple(inspect.signature(compose_behavior_context).parameters),
        )

    def test_v2_fragment_identity_covers_the_audited_semantic_claim(self) -> None:
        item = {"baseline_source_id": "SOURCE-before", "current_source_id": "SOURCE-after", "item_id": "ITEM-000001"}
        fragment = {"effect": "modified", "actor": "user", "operation": "updates", "conditions": [], "outcomes": ["saved"], "evidence_locators": [{"side": "before", "content_sha256": "sha256:" + "1" * 64, "start_byte": 0, "end_byte": 1}, {"side": "after", "content_sha256": "sha256:" + "2" * 64, "start_byte": 0, "end_byte": 1}]}
        original = stable_change_fragment_id(item, fragment)
        fragment["outcomes"] = ["rejected"]
        self.assertNotEqual(original, stable_change_fragment_id(item, fragment))

    def test_v6_fixtures_are_closed_schema_artifacts(self) -> None:
        context = json.loads((V6 / "context-marker.json").read_text(encoding="utf-8"))
        receipt = json.loads((V6 / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual([], schema_diagnostics(context, ROOT / "schemas" / "context-marker-output.schema.json", ROOT))
        self.assertEqual([], schema_diagnostics(receipt, ROOT / "schemas" / "behavior-context-receipt.schema.json", ROOT))

    def test_composed_context_cannot_be_fabricated_as_a_loose_object(self) -> None:
        with self.assertRaises(KeyError):
            _ = ComposedBehaviorContext().receipt

    def test_full_public_flow_composes_two_audited_batches_deterministically(self) -> None:
        """Changing an audited claim, disposition, group, or receipt binding breaks this public flow."""
        module, inventory, supplied, _, _, _, composed = full_flow([
            ("REQ-one", b"first source"), ("REQ-two", b"second source"),
        ])
        validated = validate_composed_behavior_context(
            composed, inventory["artifacts"]["authorized_behavior_sources"],
            inventory["artifacts"]["technical_test_inventory"], ROOT, module, supplied,
        )
        _, _, _, _, _, _, repeat = full_flow([("REQ-one", b"first source"), ("REQ-two", b"second source")])
        self.assertEqual(plain(composed.context), plain(repeat.context))
        self.assertEqual(["user does 1; outcomes: result 1.", "user does 2; outcomes: result 2."], sorted(row["text"] for row in validated.requirements))
        self.assertEqual(["represented", "represented"], [row["disposition"] for row in composed.behavior_source_accounting["source_dispositions"]])
        self.assertEqual(2, len(composed.behavior_source_accounting["behavior_fragment_groups"]))
        self.assertEqual("FULL", composed.receipt["run_mode"])
        self.assertEqual(list(validated.requirements), composed.changed_behavior_context["requirements"])

    def test_full_flow_rejects_missing_reversed_and_tampered_promotion_evidence(self) -> None:
        """Promotion replay must reject coverage/order drift and post-audit semantic or promotion edits."""
        _, _, _, receipt, plan, promotions, _ = full_flow([("REQ-one", b"one"), ("REQ-two", b"two")])
        inventory, supplied = inventories([("REQ-one", b"one"), ("REQ-two", b"two")])
        module = {"id": "root", "paths": {"source": []}}
        resolver = lambda side, source: supplied[source["source_id"]]
        cases: list[tuple[str, tuple[PromotionEvidence, ...]]] = [
            ("missing", promotions[:1]), ("reversed", tuple(reversed(promotions))),
        ]
        changed_candidate = plain(promotions[0].records[0]); changed_candidate["result"]["items"][0]["behavior_fragments"][0]["outcomes"] = ["tampered"]
        cases.append(("candidate", (PromotionEvidence((changed_candidate, *promotions[0].records[1:]), promotions[0].promotion), *promotions[1:])))
        changed_promotion = plain(promotions[0].promotion); changed_promotion["candidate_sha256"] = digest("tampered")
        cases.append(("promotion", (PromotionEvidence(promotions[0].records, changed_promotion), *promotions[1:])))
        for name, evidence in cases:
            with self.subTest(name=name), self.assertRaises(TestClassificationError):
                compose_behavior_context(ROOT, module, inventory, receipt, plan, evidence, None, resolver)

    def test_full_scope_source_order_duplicate_and_omission_are_exact_bindings(self) -> None:
        """FULL scope coverage must equal the current inventory's physical source order exactly."""
        module, inventory, supplied, receipt, plan, promotions, _ = full_flow([("REQ-one", b"one"), ("REQ-two", b"two")])
        resolver = lambda side, source: supplied[source["source_id"]]
        source_ids = receipt["included_source_ids"]
        for name, ids in (("reordered", list(reversed(source_ids))), ("duplicate", [source_ids[0], source_ids[0]]), ("omitted", source_ids[:1])):
            with self.subTest(name=name):
                altered = copy.deepcopy(receipt); altered["included_source_ids"] = ids
                with self.assertRaises(TestClassificationError):
                    compose_behavior_context(ROOT, module, inventory, altered, plan, promotions, None, resolver)

    def test_product_no_fact_composes_validates_and_mints_a_predecessor(self) -> None:
        """Only product evidence may conclude no supported observable fact through the complete V6 path."""
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
            module, inventory, _, composed, predecessor = minted_change_predecessor(project, [("src/a.py", b"fact\n"), ("src/b.py", b"none\n")], [semantic, None])
            validated = validate_composed_behavior_context(composed, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], project, module, {})
        self.assertEqual(1, len(validated.requirements))
        self.assertEqual("no_supported_observable_fact", composed.behavior_source_accounting["source_dispositions"][1]["disposition"])
        self.assertEqual("root", scope_predecessor_projection(predecessor)["selected_module"])

    def test_supplied_requirement_no_fact_rejects_at_composition(self) -> None:
        """Supplied requirements are authoritative analytics, not optional product observations."""
        with self.assertRaises(TestClassificationError) as caught:
            full_flow([("REQ-required", b"requirement")], [None])
        self.assertEqual("BEHAVIOR_RECEIPT", caught.exception.diagnostics[0]["code"])

    def test_change_set_retires_a_modified_sole_support_with_exact_tombstone(self) -> None:
        """A changed path may retire its old semantic tuple when it was the only former support."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old\n")], semantic, semantic)
            composed = compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
        old_id = "REQ-" + hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        retired = plain(composed.receipt["retired_requirements"])
        self.assertEqual([old_id], [row["object_id"] for row in retired])
        self.assertEqual(retired, plain(composed.changed_behavior_context["retired_requirements"]))
        self.assertEqual([], [row for row in composed.managed_behavior_context["requirements"] if row["requirement_id"] == old_id])
        self.assertEqual(1, len(retired[0]["support_evidence"]["retirement_fragment_ids"]))
        self.assertEqual(receipt["candidate_sha256"], plan["scope_candidate_sha256"])

    def test_change_set_rejects_retirement_when_another_former_source_still_supports_it(self) -> None:
        """One retired fragment cannot erase a tuple still supported by an unchanged source."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old a\n"), ("src/b.py", b"old b\n")], semantic, semantic)
            with self.assertRaises(TestClassificationError) as caught:
                compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
        self.assertEqual("BEHAVIOR_RETIREMENT", caught.exception.diagnostics[0]["code"])

    def test_change_set_retains_only_exact_unchanged_former_support(self) -> None:
        """A modified source without a current claim cannot retain its stale baseline support."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        current = {"actor": "user", "operation": "uses revised setting", "conditions": [], "outcomes": ["revised setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old a\n"), ("src/b.py", b"old b\n")], semantic, current, "modified")
            composed = compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
        requirements = {row["requirement_id"]: row["source_ids"] for row in composed.managed_behavior_context["requirement_sources"]}
        self.assertEqual([inventory["artifacts"]["authorized_behavior_sources"]["sources"][1]["source_id"]], requirements["REQ-" + hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()])
        self.assertEqual([inventory["artifacts"]["authorized_behavior_sources"]["sources"][0]["source_id"]], requirements["REQ-" + hashlib.sha256(json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()])
        self.assertEqual([], composed.receipt["retired_requirements"])

    def test_change_set_rejects_modified_sole_support_without_claim_or_retirement(self) -> None:
        """A changed source cannot silently preserve a baseline requirement after dropping its only support."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        current = {"actor": "user", "operation": "uses revised setting", "conditions": [], "outcomes": ["revised setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old\n")], semantic, current, "modified")
            with self.assertRaises(TestClassificationError) as caught:
                compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
        self.assertEqual("BEHAVIOR_RETIREMENT", caught.exception.diagnostics[0]["code"])

    def test_change_set_modified_no_op_preserves_semantic_id_through_guarded_validation(self) -> None:
        """A fully dual-audited modified no-op moves the stable projection to current source bytes."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old\n")], semantic, None)
            composed = compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
            validated = validate_composed_behavior_context(composed, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], project, module, {})
        source_id = inventory["artifacts"]["authorized_behavior_sources"]["sources"][0]["source_id"]
        baseline_digest = baseline["context"]["artifacts"]["managed_behavior_context"]["product_sources"][0]["content_digest"]
        self.assertEqual(baseline["context"]["artifacts"]["managed_behavior_context"]["requirements"][0]["requirement_id"], validated.requirements[0]["requirement_id"])
        self.assertEqual([source_id], validated.requirements[0]["provenance"])
        self.assertNotEqual(baseline_digest, composed.managed_behavior_context["product_sources"][0]["content_digest"])

    def test_change_set_renamed_no_op_preserves_id_and_moves_provenance_to_current_source(self) -> None:
        """A fully dual-audited renamed no-op retains its semantic requirement under the new source ID."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old\n")], semantic, None, change_kind="renamed")
            composed = compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
            validated = validate_composed_behavior_context(composed, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], project, module, {})
        current_id = inventory["artifacts"]["authorized_behavior_sources"]["sources"][0]["source_id"]
        prior_id = baseline["context"]["artifacts"]["managed_behavior_context"]["requirements"][0]["provenance"][0]
        self.assertEqual(baseline["context"]["artifacts"]["managed_behavior_context"]["requirements"][0]["requirement_id"], validated.requirements[0]["requirement_id"])
        self.assertEqual([current_id], validated.requirements[0]["provenance"])
        self.assertNotEqual(prior_id, current_id)

    def test_change_set_multichunk_no_op_requires_complete_no_changed_result(self) -> None:
        """Continuity spans every planned chunk of the same modified pair, not an inferred partial range."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"x" * 70000)], semantic, None)
            self.assertGreater(len(plan["batches"][0]["items"][0]["evidence_sides"]), 2)
            composed = compose_behavior_context(project, module, inventory, receipt, plan, promotions, baseline, resolver)
            validated = validate_composed_behavior_context(composed, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], project, module, {})
        self.assertEqual(1, len(validated.requirements))

    def test_change_set_plan_module_and_candidate_bindings_reject_at_composition(self) -> None:
        """CHANGE_SET composition does not accept a plan detached from its selected module or scope candidate."""
        semantic = {"actor": "user", "operation": "uses setting", "conditions": [], "outcomes": ["setting applies"]}
        with tempfile.TemporaryDirectory() as temp:
            from tests.test_baseline_lifecycle import make_project
            project, _, _, _ = make_project(Path(temp), "project")
            module, inventory, receipt, plan, promotions, baseline, resolver = retired_change_flow(project, [("src/a.py", b"old\n")], semantic, semantic)
            for name, mutate in (("module", lambda value: value.__setitem__("selected_module", "foreign")), ("candidate", lambda value: value.__setitem__("scope_candidate_sha256", digest("foreign")))):
                with self.subTest(name=name):
                    altered = copy.deepcopy(plan); mutate(altered)
                    with self.assertRaises(TestClassificationError):
                        compose_behavior_context(project, module, inventory, receipt, altered, promotions, baseline, resolver)

    def test_only_minted_v6_capability_validates_and_loose_json_loader_rejects(self) -> None:
        """Copying, allocating, or serializing V6 cannot mint validation authority."""
        module, inventory, supplied, _, _, _, composed = full_flow([("REQ-one", b"one")])
        valid = validate_composed_behavior_context(composed, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], ROOT, module, supplied)
        self.assertEqual(1, len(valid.requirements))
        for value in (copy.copy(composed), object.__new__(ComposedBehaviorContext), {"context": composed.context, "receipt": composed.receipt}):
            with self.subTest(value=type(value).__name__), self.assertRaises(TestClassificationError):
                validate_composed_behavior_context(value, inventory["artifacts"]["authorized_behavior_sources"], inventory["artifacts"]["technical_test_inventory"], ROOT, module, supplied)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context_path, receipt_path = root / "context.json", root / "receipt.json"
            context_path.write_text(json.dumps(plain(composed.context)), encoding="utf-8")
            receipt_path.write_text(json.dumps(plain(composed.receipt)), encoding="utf-8")
            skillsrc = root / ".skillsrc"
            skillsrc.write_text("version: '2.0'\nproject: {name: sample, language: python, framework: none, build_tool: pip}\npaths: {source: src, tests: tests}\ntest: {framework: unittest}\n", encoding="utf-8")
            with self.assertRaises(TestClassificationError):
                load_validated_behavior_context(context_path, receipt_path, inventory, root, skillsrc, "root", ())


if __name__ == "__main__":
    unittest.main()
