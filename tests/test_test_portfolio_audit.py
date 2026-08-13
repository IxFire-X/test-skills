from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "tests" / "fixtures" / "stages" / "v1"
V5 = ROOT / "tests" / "fixtures" / "stages" / "v5"
CANONICAL = ROOT / "tests" / "fixtures" / "canonical" / "valid" / "full-http.json"
PROJECT = ROOT / "tests" / "fixtures" / "test-classification" / "project"
sys.path.insert(0, str(ROOT))

from tools.audit_test_portfolio import AuditError, main  # noqa: E402
from tools.schema_validation import load_json_strict  # noqa: E402


def stable_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class TestPortfolioAuditTests(unittest.TestCase):
    """The audit must compose existing validators without owning their joins."""

    def args(self, output: Path, *, inventory: Path = V1 / "source-inventory.json", classification: Path = V1 / "test-classifier.json", review: Path = V1 / "test-classifier-reviewer-accepted.json", context: Path = V5 / "context-marker.json", receipt: Path = V5 / "receipt.json", plan: Path = V5 / "plan.json", canonical: Path = CANONICAL) -> list[str]:
        return [
            "phase1", "--project", str(PROJECT), "--inventory", str(inventory),
            "--classification", str(classification), "--review", str(review),
            "--context", str(context), "--receipt", str(receipt), "--plan", str(plan), "--skillsrc", str(PROJECT / ".skillsrc"), "--canonical-document", str(canonical),
            "--output", str(output),
        ]

    def invoke(self, args: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def write_json(self, directory: Path, name: str, value: object) -> Path:
        path = directory / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_phase1_writes_exact_compact_summary_from_checked_in_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            code, stdout, stderr = self.invoke(self.args(output))
            self.assertEqual(0, code, stderr)
            self.assertEqual("", stdout)
            self.assertEqual("", stderr)
            self.assertEqual(
                {
                    "status": "PASS",
                    "inventory_pair_count": 2,
                    "classification_pair_count": 2,
                    "reviewed_pair_count": 2,
                    "scope_counts": {"unit": 0, "integration": 2, "e2e": 0, "unknown": 0},
                    "requirement_count": 1,
                    "case_count": 1,
                    "uncovered_requirement_ids": [],
                    "diagnostics": [],
                },
                json.loads(output.read_text(encoding="utf-8")),
            )
            self.assertEqual(json.dumps(json.loads(output.read_text(encoding="utf-8")), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", output.read_text(encoding="utf-8"))

    def test_phase1_rejects_mismatched_classification_pairs_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            classification = load_json_strict(V1 / "test-classifier.json")
            classification["artifacts"]["classification"]["classifications"].pop()
            output = temporary / "summary.json"
            code, _, stderr = self.invoke(self.args(output, classification=self.write_json(temporary, "classification.json", classification)))
            self.assertEqual(2, code)
            self.assertFalse(output.exists())
            self.assertEqual("CLASSIFICATION_PAIR_COVERAGE", json.loads(stderr)["diagnostics"][0]["code"])

    def test_phase1_rejects_test_evidence_in_managed_context_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            inventory = load_json_strict(V1 / "source-inventory.json")
            context = load_json_strict(V5 / "context-marker.json")
            sources = inventory["artifacts"]["authorized_behavior_sources"]
            test_source = {
                "source_id": "SOURCE-test-file",
                "kind": "product_file",
                "path": "tests/test_sample.py",
                "content_digest": "sha256:" + hashlib.sha256((PROJECT / "tests" / "test_sample.py").read_bytes()).hexdigest(),
            }
            sources["sources"].append(test_source)
            context_rows = context["artifacts"]["managed_behavior_context"]
            context_rows["authorized_behavior_sources_sha256"] = stable_digest(sources)
            context_rows["product_sources"][0].update(test_source)
            context_rows["product_sources"][0]["summary"] = "Test-only evidence."
            context_rows["requirement_sources"][0]["source_ids"][-1] = test_source["source_id"]
            output = temporary / "summary.json"
            code, _, stderr = self.invoke(self.args(
                output,
                inventory=self.write_json(temporary, "inventory.json", inventory),
                context=self.write_json(temporary, "context.json", context),
            ))
            self.assertEqual(2, code)
            self.assertFalse(output.exists())
            self.assertEqual("BEHAVIOR_TEST_SOURCE_FORBIDDEN", json.loads(stderr)["diagnostics"][0]["code"])

    def test_phase1_rejects_uncovered_canonical_requirements_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            canonical = load_json_strict(CANONICAL)
            canonical["requirements"].append({
                "requirement_id": "REQ-unused",
                "display_order": 2,
                "text": "A documented behavior still needs a case.",
                "provenance": ["https://example.invalid/orders#unused"],
            })
            output = temporary / "summary.json"
            code, _, stderr = self.invoke(self.args(output, canonical=self.write_json(temporary, "canonical.json", canonical)))
            self.assertEqual(2, code, stderr)
            self.assertFalse(output.exists())
            self.assertEqual("AUDIT_CANONICAL_DOCUMENT", json.loads(stderr)["diagnostics"][0]["code"])

    def test_phase1_refuses_unsafe_output_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            output.write_text("do not replace", encoding="utf-8")
            code, _, stderr = self.invoke(self.args(output))
            self.assertEqual(2, code)
            self.assertEqual("do not replace", output.read_text(encoding="utf-8"))
            self.assertEqual("AUDIT_OUTPUT_EXISTS", json.loads(stderr)["diagnostics"][0]["code"])

    def test_phase1_returns_safe_cli_error_for_unreadable_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            malformed = temporary / "malformed.json"
            malformed.write_text('{"TOPSECRET":"must not appear"', encoding="utf-8")
            output = temporary / "summary.json"
            code, _, stderr = self.invoke(self.args(output, inventory=malformed))
            self.assertEqual(2, code)
            self.assertFalse(output.exists())
            error = json.loads(stderr)
            self.assertEqual("error", error["status"])
            self.assertEqual("AUDIT_INPUT", error["diagnostics"][0]["code"])
            self.assertNotIn("TOPSECRET", stderr)

    def test_direct_script_returns_one_safe_json_error_without_traceback_or_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            malformed = temporary / "malformed.json"
            malformed.write_text('{"TOPSECRET":"must not appear"', encoding="utf-8")
            output = temporary / "summary.json"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "audit_test_portfolio.py"), *self.args(output, inventory=malformed)],
                cwd=temporary,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertEqual("", completed.stdout)
            self.assertFalse(output.exists())
            self.assertNotIn("Traceback", completed.stderr)
            self.assertNotIn("TOPSECRET", completed.stderr)
            self.assertEqual("AUDIT_INPUT", json.loads(completed.stderr)["diagnostics"][0]["code"])

    def test_audit_error_diagnostics_are_immutable(self) -> None:
        error = AuditError("AUDIT_INPUT", "/input", "Audit input artifacts are invalid.")
        with self.assertRaises(TypeError):
            error.diagnostics[0]["code"] = "MUTATED"


if __name__ == "__main__":
    unittest.main()
