"""Exact Pipeline 4.0 routing and generated-projection contract tests."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.contract_check import validate_pipeline_contract
from tools.doctor import inspect_environment


ROOT = Path(__file__).resolve().parents[1]


class PipelineV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))

    def test_pipeline_v4_has_the_exact_classification_prefix_and_v3_tail(self) -> None:
        expected_stages = [
            ("source-inventory", "tool"),
            ("context-marker", "skill"),
            ("test-classifier", "skill"),
            ("test-classifier-reviewer", "skill"),
            ("tc-generator", "skill"),
            ("publish-candidate", "tool"),
            ("tc-reviewer", "skill"),
            ("revision-orchestrator", "tool"),
            ("tc-to-autotest", "skill"),
            ("autotest-reviewer", "skill"),
            ("run-tests", "tool"),
            ("build-trace", "tool"),
            ("trace-check", "tool"),
            ("finalize-orchestration", "tool"),
        ]
        self.assertEqual("5.0", self.contract["version"])
        self.assertEqual(expected_stages, [(row["id"], row["kind"]) for row in self.contract["steps"]])

    def test_generator_accepts_only_managed_behavior_context(self) -> None:
        step = next(row for row in self.contract["steps"] if row["id"] == "tc-generator")
        self.assertEqual(["managed_behavior_context"], step["accepts"])
        self.assertEqual(["managed_behavior_context"], step["forwards"])
        self.assertFalse(
            {"raw_content", "technical_test_inventory", "authorized_behavior_sources", "technical_test_classification", "effective_technical_evidence"}
            & set(step["accepts"] + step["forwards"])
        )

    def test_new_stage_carriers_are_exact_and_contract_checker_rejects_each_carrier_mutation(self) -> None:
        expected = {
            "source-inventory": {
                "accepts": ["raw_content"],
                "forwards": ["raw_content"],
                "produces": ["technical_test_inventory", "authorized_behavior_sources"],
                "rejects": [],
            },
            "context-marker": {
                "accepts": ["raw_content", "technical_test_inventory", "authorized_behavior_sources"],
                "forwards": ["technical_test_inventory", "authorized_behavior_sources"],
                "produces": ["managed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"],
                "rejects": [],
            },
            "test-classifier": {
                "accepts": ["technical_test_inventory", "authorized_behavior_sources", "managed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"],
                "forwards": ["technical_test_inventory", "authorized_behavior_sources", "managed_behavior_context", "behavior_source_accounting", "behavior_context_receipt"],
                "produces": ["technical_test_classification"],
                "rejects": [],
            },
            "test-classifier-reviewer": {
                "accepts": ["technical_test_inventory", "authorized_behavior_sources", "managed_behavior_context", "behavior_source_accounting", "behavior_context_receipt", "technical_test_classification"],
                "forwards": ["managed_behavior_context"],
                "produces": ["classification_review", "effective_technical_evidence"],
                "rejects": [],
            },
            "tc-generator": {
                "accepts": ["managed_behavior_context"],
                "forwards": ["managed_behavior_context"],
                "produces": ["candidate_document"],
                "rejects": ["raw_content", "technical_test_inventory", "authorized_behavior_sources", "behavior_source_accounting", "behavior_context_receipt", "technical_test_classification", "classification_review", "effective_technical_evidence"],
            },
        }
        stage_by_id = {row["id"]: row for row in self.contract["steps"]}
        if not set(expected).issubset(stage_by_id):
            self.fail("Pipeline 4.0 classification stages are unavailable")
        for stage_id, carriers in expected.items():
            with self.subTest(stage=stage_id):
                self.assertEqual(carriers, {field: stage_by_id[stage_id][field] for field in carriers})

        for artifact_id in (
            "technical_test_inventory",
            "authorized_behavior_sources",
            "managed_behavior_context",
            "behavior_source_accounting",
            "behavior_context_receipt",
            "technical_test_classification",
            "classification_review",
            "effective_technical_evidence",
        ):
            with self.subTest(mutated_carrier=artifact_id):
                changed = copy.deepcopy(self.contract)
                changed["artifacts"] = [row for row in changed["artifacts"] if row["id"] != artifact_id]
                self.assertEqual("failed", validate_pipeline_contract(changed, ROOT)["status"])

    def test_contract_checker_rejects_every_prefix_carrier_array_mutation(self) -> None:
        prefix_stages = {
            "source-inventory",
            "context-marker",
            "test-classifier",
            "test-classifier-reviewer",
            "tc-generator",
        }
        for stage in self.contract["steps"]:
            if stage["id"] not in prefix_stages:
                continue
            for field in ("accepts", "forwards", "produces", "rejects"):
                with self.subTest(stage=stage["id"], field=field):
                    changed = copy.deepcopy(self.contract)
                    target = next(row for row in changed["steps"] if row["id"] == stage["id"])
                    target[field].append("raw_content")
                    self.assertEqual("failed", validate_pipeline_contract(changed, ROOT)["status"])

    def test_classification_runtime_tool_is_required_by_doctor_and_contract_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory) / "pack"
            shutil.copytree(
                ROOT,
                temporary_root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
            )
            (temporary_root / "tools" / "test_classification.py").unlink()
            doctor_report = inspect_environment(temporary_root)
            contract = json.loads((temporary_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
            contract_report = validate_pipeline_contract(contract, temporary_root)
            self.assertEqual("NOT_RUNNABLE", doctor_report["status"])
            self.assertIn("tools/test_classification.py", doctor_report["integrity"]["missing"])
            self.assertEqual("failed", contract_report["status"])
            self.assertIn("missing runtime tool: tools/test_classification.py", contract_report["errors"])

    def test_contract_checker_rejects_classification_route_mutations(self) -> None:
        if "classification" not in self.contract["verdicts"]:
            self.fail("Pipeline 4.0 classification verdicts are unavailable")
        mutations = (
            lambda value: value["steps"].__setitem__(1, value["steps"][2]),
            lambda value: value["verdicts"]["classification"].append("AUTO_FIX_APPLIED"),
            lambda value: value["transitions"].insert(2, {"from": "test-classifier-reviewer", "when": {"classification_verdict": "AUTO_FIX_APPLIED"}, "transform": "auto_fix_classification"}),
            lambda value: next(row for row in value["steps"] if row["id"] == "tc-to-autotest")["accepts"].append("technical_test_classification"),
            lambda value: value.__setitem__("version", "3.0"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(self.contract)
            mutate(changed)
            self.assertEqual("failed", validate_pipeline_contract(changed, ROOT)["status"])

    def test_classifier_reviewer_has_exactly_two_no_autofix_transitions(self) -> None:
        transitions = [row for row in self.contract["transitions"] if row["from"] == "test-classifier-reviewer"]
        self.assertEqual(
            [
                {
                    "from": "test-classifier-reviewer",
                    "when": {"classification_verdict": "ПРИНЯТО"},
                    "transform": "select_effective_technical_evidence",
                },
                {
                    "from": "test-classifier-reviewer",
                    "when": {"classification_verdict": "ТРЕБУЕТ ДОРАБОТКИ"},
                    "transform": "stop_classification_rework",
                },
            ],
            transitions,
        )

    def test_full_contract_check_detects_generated_projection_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            (temporary_root / "contracts").mkdir()
            shutil.copytree(ROOT / "schemas", temporary_root / "schemas")
            (temporary_root / "tools").mkdir()
            shutil.copy2(ROOT / "tools" / "test_classification.py", temporary_root / "tools" / "test_classification.py")
            shutil.copy2(ROOT / "tools" / "behavior_context_planning.py", temporary_root / "tools" / "behavior_context_planning.py")
            for relative in ("contracts/pipeline.json", "CONTRACTS.md", "PIPELINE.md"):
                target = temporary_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            checker = ROOT / "tools" / "contract_check.py"
            clean = subprocess.run([sys.executable, str(checker), "--root", str(temporary_root), "--full"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, clean.returncode, clean.stdout + clean.stderr)
            (temporary_root / "PIPELINE.md").write_text("drift\n", encoding="utf-8", newline="\n")
            drift = subprocess.run([sys.executable, str(checker), "--root", str(temporary_root), "--full"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertNotEqual(0, drift.returncode)
            self.assertIn("PIPELINE.md", drift.stdout)


if __name__ == "__main__":
    unittest.main()
