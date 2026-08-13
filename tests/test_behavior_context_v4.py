from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "tests" / "fixtures" / "stages" / "v1"
V4 = ROOT / "tests" / "fixtures" / "stages" / "v4"
V5 = ROOT / "tests" / "fixtures" / "stages" / "v5"
sys.path.insert(0, str(ROOT))

from tools.schema_validation import load_json_strict, schema_diagnostics  # noqa: E402
from tools.test_classification import main, validate_managed_behavior_context  # noqa: E402
from tools.behavior_context_planning import validate_context_envelope  # noqa: E402


def digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class ManagedBehaviorContextV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / "fixtures" / "test-classification" / "project"
        self.inventory_envelope = load_json_strict(V1 / "source-inventory.json")
        self.context_envelope = load_json_strict(V5 / "context-marker.json")
        self.receipt = load_json_strict(V5 / "receipt.json")

    def behavior_context(self, source_id: str) -> dict[str, object]:
        context = copy.deepcopy(self.context_envelope["artifacts"]["managed_behavior_context"])
        context["requirements"] = context["requirements"][:1]
        context["requirement_sources"] = context["requirement_sources"][:1]
        context["product_sources"][0]["source_id"] = source_id
        return context

    def authorized_sources(self) -> dict[str, object]:
        return copy.deepcopy(self.inventory_envelope["artifacts"]["authorized_behavior_sources"])

    def inventory_with_unit_symbols(self, count: int) -> dict[str, object]:
        inventory = copy.deepcopy(self.inventory_envelope["artifacts"]["technical_test_inventory"])
        symbol = inventory["symbols"][0]
        inventory["symbols"] = [
            {**symbol, "symbol_id": "SYMBOL-" + f"{index:064x}"}
            for index in range(count)
        ]
        return inventory

    def validate(self, context: dict[str, object], inventory: dict[str, object] | None = None) -> tuple[dict[str, str], ...]:
        return validate_managed_behavior_context(
            context,
            self.authorized_sources(),
            self.inventory_with_unit_symbols(1) if inventory is None else inventory,
            self.root,
        )

    def test_checked_in_v5_fixture_is_schema_and_semantically_valid(self) -> None:
        self.assertEqual(
            [],
            schema_diagnostics(self.context_envelope, ROOT / "schemas" / "context-marker-output.schema.json", ROOT),
        )
        module = {"id":"backend","paths":{"source":["src"]},"_resolved_root":self.root}
        value = validate_context_envelope(self.context_envelope, self.receipt, self.authorized_sources(), self.inventory_with_unit_symbols(1), self.root, module)
        self.assertEqual(["REQ-a", "REQ-b"], [row["requirement_id"] for row in value.requirements])

    def test_test_only_fact_cannot_originate_requirement(self) -> None:
        context = self.behavior_context(source_id="SOURCE-test-file")
        sources = self.authorized_sources()
        sources["sources"].append({
            "source_id": "SOURCE-test-file",
            "kind": "product_file",
            "path": "tests/test_sample.py",
            "content_digest": "sha256:" + hashlib.sha256((self.root / "tests" / "test_sample.py").read_bytes()).hexdigest(),
        })
        context["product_sources"][0].update({
            "path": "tests/test_sample.py",
            "content_digest": sources["sources"][-1]["content_digest"],
        })
        context["requirement_sources"][0]["source_ids"][-1] = "SOURCE-test-file"
        context["authorized_behavior_sources_sha256"] = digest(sources)
        self.assertEqual(
            "BEHAVIOR_TEST_SOURCE_FORBIDDEN",
            validate_managed_behavior_context(context, sources, self.inventory_with_unit_symbols(1), self.root)[0]["code"],
        )

    def test_ten_unit_symbols_do_not_multiply_one_behavior(self) -> None:
        context = self.behavior_context(source_id="SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        inventory = self.inventory_with_unit_symbols(10)
        self.assertEqual(1, len(context["requirements"]))
        self.assertEqual((), self.validate(context, inventory))

    def test_invented_product_path_is_rejected(self) -> None:
        context = self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        context["product_sources"][0]["path"] = "src/invented.py"
        self.assertEqual("BEHAVIOR_SOURCE_MATCH", self.validate(context)[0]["code"])

    def test_relabeled_test_path_is_rejected(self) -> None:
        context = self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        context["product_sources"][0]["path"] = "tests/test_sample.py"
        self.assertEqual("BEHAVIOR_SOURCE_MATCH", self.validate(context)[0]["code"])

    def test_wrong_supplied_input_digest_is_rejected_by_snapshot_identity(self) -> None:
        context = self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        sources = self.authorized_sources()
        sources["sources"][0]["content_digest"] = "sha256:" + "a" * 64
        self.assertEqual(
            "BEHAVIOR_AUTHORIZED_DIGEST",
            validate_managed_behavior_context(context, sources, self.inventory_with_unit_symbols(1), self.root)[0]["code"],
        )

    def test_product_file_byte_drift_is_rejected(self) -> None:
        target = self.root / "src" / "service.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            self.assertEqual("BEHAVIOR_SOURCE_DRIFT", self.validate(self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75"))[0]["code"])
        finally:
            target.write_bytes(original)

    def test_requirement_source_links_must_be_complete_known_and_canonical(self) -> None:
        context = self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        missing = copy.deepcopy(context); missing["requirement_sources"] = []
        self.assertEqual("BEHAVIOR_REQUIREMENT_SOURCE_COVERAGE", self.validate(missing)[0]["code"])
        foreign = copy.deepcopy(context); foreign["requirement_sources"][0]["source_ids"] = ["SOURCE-foreign"]
        self.assertEqual("BEHAVIOR_REQUIREMENT_SOURCE_LINK", self.validate(foreign)[0]["code"])
        reordered = copy.deepcopy(context)
        reordered["requirements"].append({
            "requirement_id": "REQ-second",
            "display_order": 2,
            "text": "A second independently supported requirement.",
            "provenance": ["REQ-synthetic"],
        })
        reordered["requirement_sources"].append({"requirement_id": "REQ-second", "source_ids": ["REQ-synthetic"]})
        reordered["requirement_sources"].reverse()
        self.assertEqual("BEHAVIOR_REQUIREMENT_SOURCE_ORDER", self.validate(reordered)[0]["code"])

    def test_generator_contract_allows_only_managed_behavior_context(self) -> None:
        text = (ROOT / "skills" / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Consume only artifacts.managed_behavior_context.", text)
        self.assertIn(
            "Reject raw_content, source_code_and_diff, technical_test_inventory, authorized_behavior_sources, behavior_source_accounting, behavior_context_receipt, test source text, technical_test_classification, classification_review, and effective_technical_evidence as generator inputs.",
            text,
        )

    def test_validate_context_cli_rejects_v3_and_does_not_write(self) -> None:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            code = main([
                "validate-context", "--project", str(self.root), "--inventory", str(V1 / "source-inventory.json"),
                "--context", str(ROOT / "tests" / "fixtures" / "stages" / "v3" / "context-marker.json"), "--receipt", str(V5 / "receipt.json"), "--skillsrc", str(self.root / ".skillsrc"),
            ])
        self.assertEqual(2, code)
        self.assertIn("BEHAVIOR_ACCOUNTING_SHAPE", stderr.getvalue())

    def test_public_validator_rejects_recomputed_unauthorized_source_kind(self) -> None:
        context = self.behavior_context("SOURCE-fd794e4081e27177d36a68cf917f712c6981d175c3bf3d45f890367a92645c75")
        sources = self.authorized_sources()
        sources["sources"].append({"source_id": "SOURCE-" + "a" * 64, "kind": "technical_test", "content_digest": "sha256:" + "a" * 64})
        context["authorized_behavior_sources_sha256"] = digest(sources)
        diagnostics = validate_managed_behavior_context(context, sources, self.inventory_with_unit_symbols(1), self.root)
        self.assertEqual("BEHAVIOR_AUTHORIZED_SOURCES", diagnostics[0]["code"])
        with self.assertRaises(TypeError):
            diagnostics[0]["code"] = "changed"

    def test_validate_context_cli_rejects_malformed_inventory_envelope_without_raw_value(self) -> None:
        malformed = copy.deepcopy(self.inventory_envelope)
        malformed["artifacts"]["authorized_behavior_sources"]["sources"].append({"source_id": "SOURCE-" + "a" * 64, "kind": "technical_test", "content_digest": "sha256:" + "a" * 64, "raw_value": "do-not-leak"})
        with tempfile.TemporaryDirectory() as temporary:
            inventory_path = Path(temporary) / "inventory.json"
            inventory_path.write_text(json.dumps(malformed), encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                code = main(["validate-context", "--project", str(self.root), "--inventory", str(inventory_path), "--context", str(V5 / "context-marker.json"), "--receipt", str(V5 / "receipt.json"), "--skillsrc", str(self.root / ".skillsrc")])
        self.assertEqual(2, code)
        self.assertIn("BEHAVIOR_INVENTORY", stderr.getvalue())
        self.assertNotIn("do-not-leak", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
