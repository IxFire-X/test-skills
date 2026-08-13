"""Finite V5 behavior-source-accounting regression matrix."""

from __future__ import annotations

import unittest
import copy
import hashlib
import json
import tempfile
import subprocess
import sys
from pathlib import Path

import tools.behavior_context_planning as behavior_context_planning
from tools.behavior_context_planning import build_context_plan, build_context_receipt, stable_fragment_id, validate_batch_result
from tools.schema_validation import load_json_strict
from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc, select_module
from tools.test_classification import SuppliedInput, _digest, _plain, build_source_inventories, load_validated_behavior_context
from tools.test_classification import select_effective_technical_evidence

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "tests" / "fixtures" / "stages" / "v1"
V5 = ROOT / "tests" / "fixtures" / "stages" / "v5"
PROJECT = ROOT / "tests" / "fixtures" / "test-classification" / "project"


class BehaviorSourceAccountingV5RedMatrix(unittest.TestCase):
    """The production seam is deliberately imported only after this RED file exists."""

    def test_public_protocol_surface_exists(self) -> None:
        from tools.behavior_context_planning import (  # noqa: PLC0415
            ValidatedBehaviorContext,
            build_context_plan,
            build_context_receipt,
            validate_batch_result,
        )

        self.assertTrue(ValidatedBehaviorContext)
        self.assertTrue(build_context_plan)
        self.assertTrue(build_context_receipt)
        self.assertTrue(validate_batch_result)
        self.assertFalse(hasattr(behavior_context_planning, "validate_context_envelope"))

class PlannerReceiptV5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = load_json_strict(V1 / "source-inventory.json")
        self.context = load_json_strict(V5 / "context-marker.json")
        self.receipt = load_json_strict(V5 / "receipt.json")
        self.fixture_plan = load_json_strict(V5 / "plan.json")
        self.supplied = {"REQ-synthetic": (ROOT / "tests" / "fixtures" / "test-classification" / "requirement.txt").read_bytes()}
        self.module = dict(select_module(normalize_skillsrc(load_skillsrc(PROJECT / ".skillsrc")), None))
        self.plan = build_context_plan(PROJECT, self.module, self.inventory, self.supplied)

    def load_public(self, context: dict | None = None, receipt: dict | None = None):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            context_path = temporary / "context.json"
            receipt_path = temporary / "receipt.json"
            context_path.write_text(json.dumps(self.context if context is None else context), encoding="utf-8")
            receipt_path.write_text(json.dumps(self.receipt if receipt is None else receipt), encoding="utf-8")
            return load_validated_behavior_context(
                context_path, receipt_path, self.inventory, PROJECT, PROJECT / ".skillsrc",
                supplied_inputs=(SuppliedInput("REQ-synthetic", self.supplied["REQ-synthetic"]),),
            )

    def result_rows(self) -> list[dict]:
        return self.results_for_plan(self.plan)

    @staticmethod
    def results_for_plan(plan: dict) -> list[dict]:
        rows = []
        for batch in plan["batches"]:
            item = batch["items"][0]; start, end = item["accounted_range"]["start"], item["accounted_range"]["end"]
            fragment = {"actor":"user","operation":"observe","conditions":[],"outcomes":["result"],"anchor_byte":start,"evidence_ranges":[{"start":start,"end":end}]}
            fragment["fragment_id"] = stable_fragment_id(item, fragment)
            rows.append({"schema_version":"1.0.0","plan_sha256":_digest(_plain(plan)),"batch_id":batch["batch_id"],"items":[{"item_id":item["item_id"],"outcome":"behavior_fragments","behavior_fragments":[fragment]}]})
        return rows

    def test_over_budget_supplied_receipt_binds_real_plan_and_public_loader_revalidates_input(self) -> None:
        """The receipt/load round trip must retain every real supplied-source range."""
        payload = b"x" * 70000
        inventory = copy.deepcopy(self.inventory)
        authorized = inventory["artifacts"]["authorized_behavior_sources"]
        authorized["sources"] = [{
            "source_id": "REQ-synthetic",
            "kind": "supplied_requirement",
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }]
        inventory["artifacts"]["authorized_behavior_sources_sha256"] = _digest(authorized)
        supplied = {"REQ-synthetic": payload}
        plan = build_context_plan(PROJECT, self.module, inventory, supplied)
        self.assertEqual(2, len(plan["batches"]))
        receipt = _plain(build_context_receipt(PROJECT, self.module, inventory, plan, self.results_for_plan(plan), supplied))
        self.assertEqual(_digest(_plain(plan)), receipt["context_plan_sha256"])

        context = copy.deepcopy(self.context)
        managed = context["artifacts"]["managed_behavior_context"]
        managed["authorized_behavior_sources_sha256"] = _digest(authorized)
        managed["product_sources"] = []
        for row in managed["requirement_sources"]:
            row["source_ids"] = ["REQ-synthetic"]
        accounting = context["artifacts"]["behavior_source_accounting"]
        accounting["authorized_behavior_sources_sha256"] = _digest(authorized)
        accounting["context_receipt_sha256"] = _digest(receipt)
        accounting["source_dispositions"] = accounting["source_dispositions"][:1]
        accounting["behavior_fragment_groups"][0]["fragment_ids"] = [row["fragment_id"] for row in receipt["fragment_registry"]]

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            context_path = temporary / "context.json"
            receipt_path = temporary / "receipt.json"
            inventory_path = temporary / "inventory.json"
            plan_path = temporary / "plan.json"
            supplied_path = temporary / "controller-only-requirement.txt"
            cli_receipt_path = temporary / "cli-receipt.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            plan_path.write_text(json.dumps(_plain(plan)), encoding="utf-8")
            supplied_path.write_bytes(payload)
            command = [
                sys.executable, str(ROOT / "tools" / "test_classification.py"), "context-receipt",
                "--project", str(PROJECT), "--skillsrc", str(PROJECT / ".skillsrc"),
                "--inventory", str(inventory_path), "--plan", str(plan_path),
                "--supplied-input", f"REQ-synthetic={supplied_path}", "--output", str(cli_receipt_path),
            ]
            for index, result in enumerate(self.results_for_plan(plan), 1):
                result_path = temporary / f"result-{index}.json"
                result_path.write_text(json.dumps(result), encoding="utf-8")
                command.extend(("--batch-result", str(result_path)))
            completed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(0, completed.returncode, completed.stderr)
            cli_receipt_text = cli_receipt_path.read_text(encoding="utf-8")
            self.assertEqual(receipt, json.loads(cli_receipt_text))
            self.assertNotIn(str(supplied_path), cli_receipt_text)
            validated = load_validated_behavior_context(
                context_path, receipt_path, inventory, PROJECT, PROJECT / ".skillsrc",
                supplied_inputs=(SuppliedInput("REQ-synthetic", payload),),
            )
            self.assertEqual(("REQ-a", "REQ-b"), tuple(row["requirement_id"] for row in validated.requirements))
            for supplied_inputs in (
                (),
                (SuppliedInput("REQ-synthetic", payload + b"drift"),),
                (SuppliedInput("REQ-other", payload),),
            ):
                with self.subTest(supplied_inputs=supplied_inputs), self.assertRaises(Exception) as error:
                    load_validated_behavior_context(
                        context_path, receipt_path, inventory, PROJECT, PROJECT / ".skillsrc",
                        supplied_inputs=supplied_inputs,
                    )
                self.assertEqual("BEHAVIOR_SUPPLIED_INPUT", error.exception.diagnostics[0]["code"])

    def test_fabricated_same_id_module_mapping_cannot_enter_public_loader(self) -> None:
        """A caller cannot replace the manifest-selected module with a coherent same-ID mapping."""
        self.assertFalse(hasattr(behavior_context_planning, "validate_context_envelope"))
        fabricated = dict(self.module)
        fabricated["paths"] = {"source": ["other"]}
        fabricated_plan = build_context_plan(PROJECT, fabricated, self.inventory, self.supplied)
        fabricated_receipt = _plain(build_context_receipt(
            PROJECT, fabricated, self.inventory, fabricated_plan, self.results_for_plan(fabricated_plan), self.supplied,
        ))
        context = copy.deepcopy(self.context)
        accounting = context["artifacts"]["behavior_source_accounting"]
        accounting["context_receipt_sha256"] = _digest(fabricated_receipt)
        accounting["behavior_fragment_groups"][0]["fragment_ids"] = [row["fragment_id"] for row in fabricated_receipt["fragment_registry"]]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            context_path = temporary / "context.json"
            receipt_path = temporary / "receipt.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            receipt_path.write_text(json.dumps(fabricated_receipt), encoding="utf-8")
            with self.assertRaises(Exception) as error:
                load_validated_behavior_context(
                    context_path, receipt_path, self.inventory, PROJECT, PROJECT / ".skillsrc",
                    supplied_inputs=(SuppliedInput("REQ-synthetic", self.supplied["REQ-synthetic"]),),
                )
            self.assertEqual("BEHAVIOR_RECEIPT", error.exception.diagnostics[0]["code"])

    def test_public_loader_requires_supplied_arguments_only_for_supplied_rows(self) -> None:
        inventory = copy.deepcopy(self.inventory)
        authorized = inventory["artifacts"]["authorized_behavior_sources"]
        authorized["sources"] = authorized["sources"][1:]
        inventory["artifacts"]["authorized_behavior_sources_sha256"] = _digest(authorized)
        plan = build_context_plan(PROJECT, self.module, inventory)
        receipt = _plain(build_context_receipt(PROJECT, self.module, inventory, plan, self.results_for_plan(plan)))
        context = copy.deepcopy(self.context)
        managed = context["artifacts"]["managed_behavior_context"]
        managed["authorized_behavior_sources_sha256"] = _digest(authorized)
        for row in managed["requirement_sources"]:
            row["source_ids"] = [authorized["sources"][0]["source_id"]]
        accounting = context["artifacts"]["behavior_source_accounting"]
        accounting["authorized_behavior_sources_sha256"] = _digest(authorized)
        accounting["context_receipt_sha256"] = _digest(receipt)
        accounting["source_dispositions"] = accounting["source_dispositions"][1:]
        accounting["behavior_fragment_groups"][0]["fragment_ids"] = [row["fragment_id"] for row in receipt["fragment_registry"]]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            context_path, receipt_path = temporary / "context.json", temporary / "receipt.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertTrue(load_validated_behavior_context(context_path, receipt_path, inventory, PROJECT, PROJECT / ".skillsrc"))
            with self.assertRaises(Exception) as error:
                load_validated_behavior_context(
                    context_path, receipt_path, inventory, PROJECT, PROJECT / ".skillsrc",
                    supplied_inputs=(SuppliedInput("REQ-foreign", b"not authorized"),),
                )
            self.assertEqual("BEHAVIOR_SUPPLIED_INPUT", error.exception.diagnostics[0]["code"])

    def test_plan_has_exact_ranges_and_mechanical_domain_keys(self) -> None:
        rows = [item for batch in self.plan["batches"] for item in batch["items"]]
        self.assertEqual(["_supplied", "_root"], [row["domain_key"] for row in rows])
        self.assertEqual([(0, 39), (0, 41)], [(row["accounted_range"]["start"], row["accounted_range"]["end"]) for row in rows])
        self.assertEqual([(0, 39), (0, 41)], [(row["read_range"]["start"], row["read_range"]["end"]) for row in rows])
        with self.assertRaises(TypeError):
            self.plan["batches"] = ()

    def test_supplied_input_missing_duplicate_and_foreign_are_rejected(self) -> None:
        cases = ({}, {"foreign": b"x"}, {"REQ-synthetic": b""})
        for supplied in cases:
            with self.subTest(supplied=supplied):
                with self.assertRaises(Exception) as error:
                    build_context_plan(PROJECT, self.module, self.inventory, supplied)
                self.assertIn(error.exception.diagnostics[0]["code"], {"BEHAVIOR_SUPPLIED_INPUT"})

    def test_batch_result_requires_exact_binding_anchor_and_evidence_ranges(self) -> None:
        valid = self.result_rows()[0]
        self.assertEqual((), validate_batch_result(self.plan, valid))
        cases = []
        wrong = copy.deepcopy(valid); wrong["plan_sha256"] = "sha256:" + "a" * 64; cases.append(wrong)
        wrong = copy.deepcopy(valid); wrong["items"][0]["item_id"] = "ITEM-999999"; cases.append(wrong)
        wrong = copy.deepcopy(valid); wrong["items"][0]["behavior_fragments"][0]["anchor_byte"] = valid["items"][0]["behavior_fragments"][0]["evidence_ranges"][0]["end"]; cases.append(wrong)
        for value in cases:
            with self.subTest(value=value): self.assertEqual("BEHAVIOR_BATCH_RESULT", validate_batch_result(self.plan, value)[0]["code"])

    def test_fragment_ids_are_deterministic_and_spoofing_is_rejected(self) -> None:
        first, second = self.result_rows()[0], self.result_rows()[0]
        self.assertEqual(first["items"][0]["behavior_fragments"][0]["fragment_id"], second["items"][0]["behavior_fragments"][0]["fragment_id"])
        first["items"][0]["behavior_fragments"][0]["fragment_id"] = "FRAGMENT-" + "a" * 64
        self.assertEqual("BEHAVIOR_BATCH_RESULT", validate_batch_result(self.plan, first)[0]["code"])

    def test_module_identity_and_longest_nested_root_are_enforced(self) -> None:
        nested = {"id":"backend","_resolved_root": PROJECT / "component", "paths":{"source":["src","src/feature/deep"]}}
        source = {"source_id":"SOURCE-" + "b" * 64,"kind":"product_file","path":"component/src/feature/deep/a/b/file.py","content_digest":"sha256:" + "0" * 64}
        from tools.behavior_context_planning import derive_domain_key
        self.assertEqual("a/b", derive_domain_key(source, nested, PROJECT))
        wrong = dict(self.module); wrong["id"] = "wrong"
        with self.assertRaises(Exception) as error: build_context_plan(PROJECT, wrong, self.inventory, self.supplied)
        self.assertEqual("BEHAVIOR_PLAN", error.exception.diagnostics[0]["code"])

    def test_receipt_requires_one_result_per_batch_and_closed_fragments(self) -> None:
        rows = self.result_rows()
        receipt = build_context_receipt(PROJECT, self.module, self.inventory, self.plan, rows, self.supplied)
        self.assertEqual(2, len(receipt["fragment_registry"]))
        with self.assertRaises(Exception) as error:
            build_context_receipt(PROJECT, self.module, self.inventory, self.plan, rows[:1], self.supplied)
        self.assertEqual("BEHAVIOR_RECEIPT", error.exception.diagnostics[0]["code"])
        reversed_rows = list(reversed(rows))
        reversed_receipt = build_context_receipt(PROJECT, self.module, self.inventory, self.plan, reversed_rows, self.supplied)
        self.assertEqual(list(receipt["batch_result_sha256s"]), list(reversed_receipt["batch_result_sha256s"]))
        duplicate = copy.deepcopy(rows); duplicate[1]["items"][0]["behavior_fragments"][0]["fragment_id"] = duplicate[0]["items"][0]["behavior_fragments"][0]["fragment_id"]
        with self.assertRaises(Exception) as error:
            build_context_receipt(PROJECT, self.module, self.inventory, self.plan, duplicate, self.supplied)
        self.assertEqual("BEHAVIOR_BATCH_RESULT", error.exception.diagnostics[0]["code"])

    def test_three_way_accounting_link_and_fragment_coverage_mutations_fail_immutably(self) -> None:
        for mutate in (
            lambda value: value["artifacts"]["behavior_source_accounting"]["behavior_fragment_groups"][0]["fragment_ids"].pop(),
            lambda value: value["artifacts"]["behavior_source_accounting"]["behavior_fragment_groups"][0].update({"fragment_ids":["FRAGMENT-" + "f" * 64]}),
            lambda value: value["artifacts"]["behavior_source_accounting"]["source_dispositions"][0].update({"requirement_ids":["REQ-a"]}),
        ):
            value = copy.deepcopy(self.context); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(Exception) as error:
                self.load_public(value, self.receipt)
            diagnostic = error.exception.diagnostics[0]
            self.assertIn(diagnostic["code"], {"BEHAVIOR_ACCOUNTING_COVERAGE", "BEHAVIOR_ACCOUNTING_LINK"})
            with self.assertRaises(TypeError): diagnostic["code"] = "changed"

    def test_noncontiguous_group_membership_is_allowed_but_exact_coverage_is_required(self) -> None:
        value = copy.deepcopy(self.context)
        groups = value["artifacts"]["behavior_source_accounting"]["behavior_fragment_groups"]
        fragments = groups[0]["fragment_ids"]
        groups[:] = [{"group_id":"GROUP-a","fragment_ids":[fragments[1]],"requirement_ids":["REQ-a","REQ-b"]},{"group_id":"GROUP-b","fragment_ids":[fragments[0]],"requirement_ids":["REQ-a","REQ-b"]}]
        self.assertTrue(self.load_public(value, self.receipt))
        groups.pop()
        with self.assertRaises(Exception) as error: self.load_public(value, self.receipt)
        self.assertEqual("BEHAVIOR_ACCOUNTING_COVERAGE", error.exception.diagnostics[0]["code"])

    def test_receipt_schema_and_spoofed_domain_are_rejected_at_shared_seam(self) -> None:
        for mutate in (
            lambda value: value.update({"spoof": True}),
            lambda value: value["source_outcomes"][1].update({"domain_key": "spoof"}),
            lambda value: value["fragment_registry"][0].update({"source_id": "SOURCE-" + "a" * 64}),
        ):
            receipt = copy.deepcopy(self.receipt); mutate(receipt)
            context = copy.deepcopy(self.context)
            context["artifacts"]["behavior_source_accounting"]["context_receipt_sha256"] = _digest(receipt)
            with self.subTest(mutate=mutate), self.assertRaises(Exception) as error:
                self.load_public(context, receipt)
            self.assertIn(error.exception.diagnostics[0]["code"], {"BEHAVIOR_RECEIPT", "BEHAVIOR_ACCOUNTING_SHAPE"})

    def test_public_plan_binding_rejects_spoofed_item_ownership(self) -> None:
        receipt = copy.deepcopy(self.receipt); receipt["fragment_registry"][0]["item_id"] = "ITEM-999999"
        context = copy.deepcopy(self.context); context["artifacts"]["behavior_source_accounting"]["context_receipt_sha256"] = _digest(receipt)
        with self.assertRaises(Exception) as error:
            self.load_public(context, receipt)
        self.assertEqual("BEHAVIOR_RECEIPT", error.exception.diagnostics[0]["code"])

    def test_shared_seam_rejects_jointly_rebound_receipt_plan_digest(self) -> None:
        """A receipt cannot choose its own plan digest after context receipt rebinding."""
        receipt = copy.deepcopy(self.receipt)
        receipt["context_plan_sha256"] = "sha256:" + "a" * 64
        context = copy.deepcopy(self.context)
        context["artifacts"]["behavior_source_accounting"]["context_receipt_sha256"] = _digest(receipt)
        with self.assertRaises(Exception) as error:
            self.load_public(context, receipt)
        self.assertEqual("BEHAVIOR_RECEIPT", error.exception.diagnostics[0]["code"])

    def test_shared_seam_requires_normalized_module_and_no_downstream_plan(self) -> None:
        self.assertFalse(hasattr(behavior_context_planning, "validate_context_envelope"))
        for command in ("select", "validate-context"):
            completed = subprocess.run([sys.executable, str(ROOT / "tools" / "test_classification.py"), command, "--help"], text=True, capture_output=True)
            self.assertEqual(0, completed.returncode); self.assertNotIn("--plan", completed.stdout); self.assertIn("--module", completed.stdout); self.assertIn("--supplied-input", completed.stdout)
        completed = subprocess.run([sys.executable, str(ROOT / "tools" / "audit_test_portfolio.py"), "phase1", "--help"], text=True, capture_output=True)
        self.assertEqual(0, completed.returncode); self.assertNotIn("--plan", completed.stdout); self.assertIn("--module", completed.stdout); self.assertIn("--supplied-input", completed.stdout)

    def test_public_loader_uses_nested_module_root_and_rejects_wrong_receipt_module(self) -> None:
        """The public seam resolves a receipt module from its manifest, never a fallback."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "component" / "src" / "feature" / "deep" / "a" / "b" / "service.py"
            source.parent.mkdir(parents=True)
            source.write_bytes((PROJECT / "src" / "service.py").read_bytes())
            tests = project / "component" / "tests"; tests.mkdir()
            (tests / "test_sample.py").write_bytes((PROJECT / "tests" / "test_sample.py").read_bytes())
            skillsrc = project / ".skillsrc"
            manifest = (PROJECT / ".skillsrc").read_text(encoding="utf-8")
            manifest = manifest.replace("root: .", "root: component").replace("        - src\n      tests:", "        - src\n        - src/feature/deep\n      tests:")
            skillsrc.write_text(manifest, encoding="utf-8")
            supplied = (ROOT / "tests" / "fixtures" / "test-classification" / "requirement.txt").read_bytes()
            generated = build_source_inventories(project, load_skillsrc(skillsrc), "backend", (SuppliedInput("REQ-synthetic", supplied),))
            inventory = {"stage": "source-inventory", "artifacts": {"authorized_behavior_sources": _plain(generated.authorized_behavior_sources), "technical_test_inventory": _plain(generated.technical_test_inventory)}}
            module = dict(select_module(normalize_skillsrc(load_skillsrc(skillsrc)), "backend")); module["_resolved_root"] = project / "component"
            plan = build_context_plan(project, module, inventory, {"REQ-synthetic": supplied})
            results = []
            for batch in plan["batches"]:
                item = batch["items"][0]; start, end = item["accounted_range"]["start"], item["accounted_range"]["end"]
                fragment = {"actor":"user","operation":"observe","conditions":[],"outcomes":["result"],"anchor_byte":start,"evidence_ranges":[{"start":start,"end":end}]}
                fragment["fragment_id"] = stable_fragment_id(item, fragment)
                results.append({"schema_version":"1.0.0","plan_sha256":_digest(_plain(plan)),"batch_id":batch["batch_id"],"items":[{"item_id":item["item_id"],"outcome":"behavior_fragments","behavior_fragments":[fragment]}]})
            receipt = _plain(build_context_receipt(project, module, inventory, plan, results, {"REQ-synthetic": supplied}))
            context = copy.deepcopy(self.context)
            authorized = inventory["artifacts"]["authorized_behavior_sources"]
            product = next(row for row in authorized["sources"] if row["kind"] == "product_file")
            context["artifacts"]["managed_behavior_context"]["authorized_behavior_sources_sha256"] = _digest(authorized)
            context["artifacts"]["managed_behavior_context"]["product_sources"][0].update({**product, "summary":"nested source"})
            for row in context["artifacts"]["managed_behavior_context"]["requirement_sources"]:
                row["source_ids"] = ["REQ-synthetic", product["source_id"]]
            accounting = context["artifacts"]["behavior_source_accounting"]
            accounting["authorized_behavior_sources_sha256"] = _digest(authorized)
            accounting["context_receipt_sha256"] = _digest(receipt)
            for row, source_row in zip(accounting["source_dispositions"], authorized["sources"]): row["source_id"] = source_row["source_id"]
            accounting["behavior_fragment_groups"][0]["fragment_ids"] = [row["fragment_id"] for row in receipt["fragment_registry"]]
            context_path, receipt_path = project / "context.json", project / "receipt.json"
            context_path.write_text(json.dumps(context), encoding="utf-8"); receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            validated = load_validated_behavior_context(context_path, receipt_path, inventory, project, skillsrc, "backend", (SuppliedInput("REQ-synthetic", supplied),))
            self.assertEqual(("REQ-a", "REQ-b"), tuple(row["requirement_id"] for row in validated.requirements))
            self.assertEqual("a/b", receipt["source_outcomes"][1]["domain_key"])
            receipt["selected_module"] = "wrong"; accounting["context_receipt_sha256"] = _digest(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8"); context_path.write_text(json.dumps(context), encoding="utf-8")
            with self.assertRaises(Exception) as error:
                load_validated_behavior_context(context_path, receipt_path, inventory, project, skillsrc, "backend", (SuppliedInput("REQ-synthetic", supplied),))
            self.assertEqual("BEHAVIOR_RECEIPT", error.exception.diagnostics[0]["code"])

    def test_repeated_euro_read_overlap_never_exceeds_fixed_budget(self) -> None:
        from tools.behavior_context_planning import _ranges
        payload = "€".encode("utf-8") * 30000
        for start, end, read_start, read_end in _ranges(payload):
            self.assertLessEqual(start - read_start, 16384)
            self.assertLessEqual(read_end - end, 16384)
            payload[read_start:read_end].decode("utf-8")

    def test_context_receipt_cli_rejects_array_result_safely_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory); plan = temporary / "plan.json"; bad = temporary / "bad.json"; output = temporary / "receipt.json"
            plan.write_text(json.dumps(_plain(self.plan)), encoding="utf-8"); bad.write_text("[]", encoding="utf-8")
            completed = subprocess.run([sys.executable, str(ROOT / "tools" / "test_classification.py"), "context-receipt", "--project", str(PROJECT), "--skillsrc", str(PROJECT / ".skillsrc"), "--inventory", str(V1 / "source-inventory.json"), "--plan", str(plan), "--batch-result", str(bad), "--supplied-input", f"REQ-synthetic={ROOT / 'tests' / 'fixtures' / 'test-classification' / 'requirement.txt'}", "--output", str(output)], text=True, capture_output=True)
            self.assertEqual(2, completed.returncode); self.assertIn("BEHAVIOR_BATCH_RESULT", completed.stderr); self.assertNotIn("Traceback", completed.stderr); self.assertFalse(output.exists())

    def test_validate_context_cli_revalidates_supplied_paths_with_safe_errors(self) -> None:
        requirement = ROOT / "tests" / "fixtures" / "test-classification" / "requirement.txt"
        base = [
            sys.executable, str(ROOT / "tools" / "test_classification.py"), "validate-context",
            "--project", str(PROJECT), "--skillsrc", str(PROJECT / ".skillsrc"),
            "--inventory", str(V1 / "source-inventory.json"), "--context", str(V5 / "context-marker.json"),
            "--receipt", str(V5 / "receipt.json"),
        ]
        accepted = subprocess.run([*base, "--supplied-input", f"REQ-synthetic={requirement}"], text=True, capture_output=True)
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            drift = temporary / "controller-input.txt"
            drift.write_bytes(requirement.read_bytes() + b"drift")
            for suffix, forbidden in (
                ([], (str(requirement), "REQ-synthetic")),
                (["--supplied-input", f"REQ-synthetic={drift}"], (str(drift), "REQ-synthetic")),
                (["--supplied-input", f"REQ-other={requirement}"], (str(requirement), "REQ-other")),
                (["--supplied-input", f"REQ-synthetic={temporary / 'missing.txt'}"], (str(temporary / "missing.txt"), "REQ-synthetic")),
            ):
                completed = subprocess.run([*base, *suffix], text=True, capture_output=True)
                self.assertEqual(2, completed.returncode)
                self.assertEqual("", completed.stdout)
                self.assertNotIn("Traceback", completed.stderr)
                diagnostic = json.loads(completed.stderr)
                self.assertEqual(1, len(diagnostic["diagnostics"]))
                self.assertEqual("BEHAVIOR_SUPPLIED_INPUT", diagnostic["diagnostics"][0]["code"])
                for value in forbidden:
                    self.assertNotIn(value, completed.stderr)

    def test_invalid_utf8_and_product_supplied_key_are_rejected(self) -> None:
        source = self.inventory["artifacts"]["authorized_behavior_sources"]["sources"][1]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); (project / "src").mkdir(); (project / "src" / "service.py").write_bytes(b"\xff")
            with self.assertRaises(Exception) as error:
                build_context_plan(project, self.module, self.inventory, self.supplied)
            self.assertEqual("BEHAVIOR_PLAN", error.exception.diagnostics[0]["code"])
        with self.assertRaises(Exception) as error:
            build_context_plan(PROJECT, self.module, self.inventory, {**self.supplied, source["source_id"]: b"x"})
        self.assertEqual("BEHAVIOR_SUPPLIED_INPUT", error.exception.diagnostics[0]["code"])

    def test_public_selector_rejects_raw_requirements(self) -> None:
        candidate = load_json_strict(V1 / "test-classifier.json")
        review = load_json_strict(V1 / "test-classifier-reviewer-accepted.json")
        with self.assertRaises(TypeError):
            select_effective_technical_evidence(self.inventory, candidate, review, self.context["artifacts"]["managed_behavior_context"]["requirements"], PROJECT)


if __name__ == "__main__":
    unittest.main()
