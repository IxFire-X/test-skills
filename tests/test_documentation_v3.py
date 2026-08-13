"""Bounded regression checks for the public V3 documentation projections."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.render_contract_docs import _rendered_files, render_contracts, render_pipeline


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (ROOT / "README.md", ROOT / "USAGE.md", ROOT / "HOW-IT-WORKS.md")
GENERATED_DOCS = (ROOT / "CONTRACTS.md", ROOT / "PIPELINE.md")
FIXTURES = ROOT / "skills" / "orchestrate" / "assets" / "orchestration-fixtures"


class DocumentationV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.docs = {path.name: path.read_text(encoding="utf-8") for path in PUBLIC_DOCS}
        self.contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))

    def test_checked_in_document_inventory_is_exact(self) -> None:
        self.assertEqual(
            {"README.md", "USAGE.md", "HOW-IT-WORKS.md", "CONTRACTS.md", "PIPELINE.md"},
            {path.name for path in (*PUBLIC_DOCS, *GENERATED_DOCS)},
        )

    def test_projection_byte_goldens_are_checkout_stable(self) -> None:
        """Removing binary attributes would let autocrlf corrupt locked bytes."""
        paths = (
            "tests/fixtures/projections/full-http.markdown.bin",
            "tests/fixtures/projections/full-http.zephyr-scale.csv.bin",
        )
        result = subprocess.run(
            ["git", "check-attr", "text", "--", *paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            [f"{path}: text: unset" for path in paths],
            result.stdout.splitlines(),
        )

    def test_each_public_guide_describes_the_v3_model(self) -> None:
        required = (
            "bare canonical JSON",
            "Действие",
            "Ожидаемый результат",
            "zephyr-scale-step-row-24-v1",
            "24",
            "effective revision",
            "(file_id, symbol_id)",
            "requirement -> case -> step -> expectation -> assertion -> file -> symbol -> current-run evidence",
            "PASS_WITH_MANUAL_REMAINDER",
            "MANUAL_ONLY",
            "BLOCKED",
            "NOT_RUNNABLE",
            "tenant import round trip remains unverified",
        )
        for name, text in self.docs.items():
            with self.subTest(document=name):
                for phrase in required:
                    self.assertIn(phrase, text)

    def test_public_guides_reject_v21_without_actionable_legacy_lifecycle(self) -> None:
        stale_tokens = (
            "corrected_test_cases",
            "automation_matrix",
            "generated_test_methods",
            "expected_outcome",
            "--orchestrator-artifact",
            "--requirements",
            "--test-cases",
            "export_test_cases_csv.py",
        )
        for name, text in self.docs.items():
            with self.subTest(document=name):
                self.assertIn("V2.1 is unsupported", text)
                actionable_text = text.split("## V2.1 is unsupported", 1)[0]
                for token in stale_tokens:
                    self.assertNotIn(token, actionable_text)

    def test_documented_module_commands_match_current_parser_flags(self) -> None:
        command_expectations = {
            "tools.publish_test_case_bundle": ("--input", "--output-dir", "--csv-profile", "--verify-only"),
            "tools.orchestrate_test_case_revision": ("--candidate", "--review", "--output-dir", "--csv-profile"),
            "tools.run_tests": ("--project", "--language", "--canonical-document", "--automation-artifact"),
            "tools.build_trace_document": ("--canonical-document", "--automation-artifact", "--run-result", "--output"),
            "tools.trace_check": ("--require-execution",),
        }
        combined = "\n".join(self.docs.values())
        for module, flags in command_expectations.items():
            with self.subTest(module=module):
                self.assertIn(f"python -m {module}", combined)
                completed = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                for flag in flags:
                    self.assertIn(flag, completed.stdout)

    def test_renderer_is_deterministic_and_preserves_all_registry_information(self) -> None:
        rendered = _rendered_files(self.contract)
        self.assertEqual(rendered, _rendered_files(self.contract))
        pipeline = render_pipeline(self.contract)
        contracts = render_contracts(self.contract)
        self.assertIn("test-pipeline", pipeline)
        self.assertIn("2.0", pipeline)
        for step in self.contract["steps"]:
            self.assertIn(f"`{step['id']}`", pipeline)
            self.assertIn(f"`{step['kind']}`", pipeline)
            for field in ("accepts", "forwards", "produces"):
                self.assertIn(field, pipeline)
                for artifact in step[field]:
                    self.assertIn(f"`{artifact}`", pipeline)
        for artifact in self.contract["artifacts"]:
            self.assertIn(f"`{artifact['id']}`", contracts)
            self.assertIn(artifact["description"], contracts)
        for transition in self.contract["transitions"]:
            self.assertIn(f"`{transition['from']}`", contracts)
            self.assertIn(f"`{transition['transform']}`", contracts)
            for predicate, value in transition["when"].items():
                self.assertIn(f"`{predicate}`", contracts)
                self.assertIn(f"`{value}`", contracts)
        for capability in self.contract["capabilities"]:
            self.assertIn(capability["language"], contracts)
        self.assertIn("Artifact policy", contracts)
        expected_traceability = [
            "requirement",
            "case",
            "step",
            "expectation",
            "assertion",
            "file",
            "symbol",
            "current_run_evidence",
        ]
        self.assertEqual(expected_traceability, self.contract["traceability"])
        self.assertIn(" -> ".join(expected_traceability), contracts)

    def test_renderer_check_detects_missing_and_mutated_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            (temporary_root / "contracts").mkdir()
            shutil.copy2(ROOT / "contracts" / "pipeline.json", temporary_root / "contracts" / "pipeline.json")
            renderer = ROOT / "tools" / "render_contract_docs.py"
            write = subprocess.run([sys.executable, str(renderer), "--root", str(temporary_root)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, write.returncode, write.stderr)
            check = subprocess.run([sys.executable, str(renderer), "--root", str(temporary_root), "--check"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, check.returncode, check.stdout + check.stderr)
            (temporary_root / "PIPELINE.md").write_text("mutated\n", encoding="utf-8", newline="\n")
            drift = subprocess.run([sys.executable, str(renderer), "--root", str(temporary_root), "--check"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(1, drift.returncode)
            self.assertIn("PIPELINE.md", drift.stdout)

    def test_v3_orchestration_and_trace_fixtures_validate_without_byte_changes(self) -> None:
        validator = ROOT / "tools" / "validate_artifact.py"
        schema = ROOT / "schemas" / "orchestrator-output.schema.json"
        for name in ("accepted-orchestrator-output.json", "not-runnable-orchestrator-output.json"):
            with self.subTest(fixture=name):
                completed = subprocess.run([sys.executable, str(validator), str(schema), str(FIXTURES / name)], cwd=ROOT, text=True, capture_output=True, check=False)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        trace = FIXTURES / "accepted-trace-document.json"
        completed = subprocess.run([sys.executable, "-m", "tools.trace_check", str(trace), "--require-execution"], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_discovery_and_secret_guidance_remain_visible(self) -> None:
        protected = {
            "README.md": (".skillsrc", "docs/to_do", "учётные данные"),
            "USAGE.md": ("schemas/skillsrc.schema.json", "scan_project.py", "docs/to_do", "bearer-токены"),
            "HOW-IT-WORKS.md": (".skillsrc", "scan_project.py", "изолированном рабочем пространстве", "секреты"),
        }
        for name, phrases in protected.items():
            with self.subTest(document=name):
                for phrase in phrases:
                    self.assertIn(phrase, self.docs[name])


if __name__ == "__main__":
    unittest.main()
