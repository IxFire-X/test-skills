"""Closed Pipeline 6 registry, fingerprint, and branch contracts."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.contract_check import ARTIFACT_IDS, CORE_SKILLS, STAGE_LAYOUT, materialize_fingerprint_registries, validate_pipeline_contract


ROOT = Path(__file__).resolve().parents[1]


class PipelineV6Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))

    def test_exact_30_carriers_18_stages_complements_and_closed_branches(self) -> None:
        self.assertEqual("6.0", self.contract["version"])
        self.assertEqual(ARTIFACT_IDS, [row["id"] for row in self.contract["artifacts"]])
        self.assertEqual(30, len(ARTIFACT_IDS))
        self.assertEqual([(name, kind) for name, kind, *_ in STAGE_LAYOUT], [(row["id"], row["kind"]) for row in self.contract["steps"]])
        self.assertEqual(18, len(self.contract["steps"]))
        for row in self.contract["steps"]:
            with self.subTest(stage=row["id"]):
                self.assertEqual([item for item in ARTIFACT_IDS if item not in row["accepts"]], row["rejects"])
        branches = {row["id"]: row["branches"] for row in self.contract["steps"] if "branches" in row}
        self.assertEqual({"tc-generator", "apply-document-delta", "advance-baseline"}, set(branches))
        for stage, rows in branches.items():
            with self.subTest(stage=stage):
                self.assertEqual(list(dict.fromkeys(item for branch in rows for item in branch["produces"])), next(row["produces"] for row in self.contract["steps"] if row["id"] == stage))

    def test_changed_context_is_the_only_generator_semantic_input_and_classification_is_isolated(self) -> None:
        rows = {row["id"]: row for row in self.contract["steps"]}
        self.assertEqual(["changed_behavior_context"], rows["tc-generator"]["accepts"])
        self.assertEqual(["changed_behavior_context"], rows["test-classifier-reviewer"]["forwards"])
        self.assertTrue({"managed_behavior_context", "technical_test_classification", "classification_review", "effective_technical_evidence"}.issubset(rows["tc-generator"]["rejects"]))

    def test_checker_rejects_carrier_branch_and_historical_registry_drift(self) -> None:
        mutations = (
            lambda value: value["steps"][5]["rejects"].pop(),
            lambda value: value["steps"][6]["branches"].reverse(),
            lambda value: value["historical_rejections"].pop(),
            lambda value: value.__setitem__("version", "5.0"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(self.contract); mutate(changed)
                self.assertEqual("failed", validate_pipeline_contract(changed, ROOT)["status"])

    def test_final_fingerprint_registries_are_closed_current_and_readback_sensitive(self) -> None:
        first = materialize_fingerprint_registries(ROOT, self.contract)
        self.assertEqual(first, materialize_fingerprint_registries(ROOT, self.contract))
        self.assertEqual(("pipeline_contract", "policy_bundle", "tool_bundle", "schema_bundle"), tuple(first))
        for row in first.values():
            self.assertEqual({"sha256", "files"}, set(row))
            self.assertEqual([item["path"] for item in row["files"]], sorted(item["path"] for item in row["files"]))
        with tempfile.TemporaryDirectory() as directory:
            copied_root = Path(directory)
            for registry in first.values():
                for item in registry["files"]:
                    target = copied_root / item["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / item["path"], target)
            copied = materialize_fingerprint_registries(copied_root, self.contract)
            self.assertEqual(first, copied)
            runtime = copied_root / "tools" / "run_tests.py"
            runtime.write_bytes(runtime.read_bytes() + b"\n# fingerprint sensitivity\n")
            changed = materialize_fingerprint_registries(copied_root, self.contract)
            self.assertNotEqual(copied["tool_bundle"]["sha256"], changed["tool_bundle"]["sha256"])
            self.assertEqual(copied["schema_bundle"], changed["schema_bundle"])

    def test_core_skill_registry_includes_change_scope(self) -> None:
        self.assertEqual(CORE_SKILLS, self.contract["core_skills"])
        self.assertEqual("skills/change-scope/SKILL.md", self.contract["skill_files"]["change-scope"])


if __name__ == "__main__":
    unittest.main()
