"""Release-only readback gate for one explicitly authorized Pipeline 6 attempt."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from tools.flow_artifacts import artifact_sha256, canonical_bytes
from tools.schema_validation import loads_json_strict, schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_SUITE = _ROOT / "evals" / "change-scoped-semantic-evidence" / "scenarios.json"
_PHASES = ["scope_selection", "scope_false_inclusion", "scope_omission", "candidate_extraction", "batch_false_claim", "batch_omission", "promotion"]


def strict_json(path: Path) -> Mapping[str, Any]:
    value = loads_json_strict(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or canonical_bytes(value) != path.read_bytes():
        raise AssertionError("artifact is not canonical JSON")
    return value


def canonical_inventory(root: Path) -> dict[str, list[Mapping[str, Any]]]:
    inventory: dict[str, list[Mapping[str, Any]]] = {}
    for path in root.rglob("*.json"):
        resolved = path.resolve(strict=True)
        if path.is_symlink() or resolved.parent != path.parent.resolve():
            raise AssertionError("attempt artifact may not traverse a symlink")
        resolved.relative_to(root)
        value = strict_json(resolved)
        digest = artifact_sha256(value)
        inventory.setdefault(digest, []).append(value)
    return inventory


class Attempt04AcceptanceTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("PIPELINE6_ATTEMPT_ROOT"), "PIPELINE6_ATTEMPT_ROOT authorizes the release-only acceptance root")
    def test_authorized_attempt_root_binds_calibration_terminal_and_baseline(self) -> None:
        root = Path(os.environ["PIPELINE6_ATTEMPT_ROOT"]).resolve()
        self.assertTrue(root.is_dir())
        manifest = strict_json(root / "attempt-root.json")
        self.assertEqual({"schema_version", "artifact", "suite", "root_name", "implementation_sha", "target", "review_mode"}, set(manifest))
        self.assertEqual("1.0.0", manifest["schema_version"])
        self.assertEqual("pipeline6-attempt-root", manifest["artifact"])
        self.assertEqual("change-scoped-semantic-evidence", manifest["suite"])
        self.assertEqual("SEQUENTIAL", manifest["review_mode"])
        self.assertEqual(root.name, manifest["root_name"])
        self.assertRegex(manifest["implementation_sha"], r"^[0-9a-f]{40}$")
        target = manifest["target"]
        self.assertIsInstance(target, Mapping)
        self.assertEqual({"repository_id", "commit", "tree", "module_id", "skillsrc_sha256"}, set(target))
        self.assertRegex(target["repository_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(target["commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(target["tree"], r"^[0-9a-f]{40}$")
        self.assertRegex(target["module_id"], r"^[^/\\]+$")
        self.assertRegex(target["skillsrc_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertFalse(any("/" in value or "\\" in value for value in (manifest["root_name"], manifest["implementation_sha"], *target.values()) if isinstance(value, str)))
        inventory = canonical_inventory(root)
        reports = [(digest, copies[0]) for digest, copies in inventory.items() if copies[0].get("artifact") == "pipeline6-calibration-report"]
        self.assertEqual(1, len(reports))
        report = reports[0][1]
        self.assertEqual([], schema_diagnostics(report, _ROOT / "schemas" / "pipeline6-calibration-report.schema.json", _ROOT))
        suite = loads_json_strict(_SUITE.read_text(encoding="utf-8"))
        self.assertIsInstance(suite, Mapping)
        self.assertEqual(artifact_sha256(suite), report["suite_sha256"])
        self.assertEqual([row["id"] for row in suite["controls"]], [row["control_id"] for row in report["controls"]])
        self.assertEqual(artifact_sha256(manifest), report["attempt_root_sha256"])
        self.assertEqual({"commit": target["commit"], "tree": target["tree"], "clean": True}, report["target"])
        self.assertEqual(24, len(report["controls"]))
        for control in report["controls"]:
            self.assertEqual(_PHASES, [phase["phase"] for phase in control["phases"]])
            self.assertEqual("PASS", control["status"])
            self.assertEqual(0, control["mismatch_count"])
            for phase in control["phases"]:
                self.assertTrue(phase["matches_expected"])
                for digest in phase["artifact_sha256s"]:
                    self.assertIn(digest, inventory)
        self.assertEqual("PASS", report["overall"])
        self.assertEqual(0, report["mismatch_count"])
        for name in ("terminal_receipt_sha256", "baseline_receipt_sha256"):
            self.assertIn(report[name], inventory)
        terminal = inventory[report["terminal_receipt_sha256"]][0]
        baseline = inventory[report["baseline_receipt_sha256"]][0]
        self.assertEqual([], schema_diagnostics(terminal, _ROOT / "schemas" / "terminal-run-receipt.schema.json", _ROOT))
        self.assertEqual([], schema_diagnostics(baseline, _ROOT / "schemas" / "feature-baseline-receipt.schema.json", _ROOT))
        self.assertEqual("FULL", terminal["run_mode"])
        self.assertEqual("FULL", baseline["run_mode"])
        self.assertEqual("git_head", terminal["change_input"]["input_kind"])
        self.assertIsNone(baseline["predecessor_baseline_sha256"])
        self.assertEqual(target["repository_id"], terminal["repository_id"])
        self.assertEqual(target["module_id"], terminal["selected_module"])
        self.assertEqual(target["repository_id"], baseline["repository_id"])
        self.assertEqual(target["module_id"], baseline["selected_module"])
        self.assertEqual(target["commit"], terminal["change_input"]["target"]["commit"])
        self.assertEqual(target["tree"], terminal["change_input"]["target"]["tree"])
        self.assertEqual(target["commit"], baseline["target_commit"])
        self.assertEqual(target["tree"], baseline["target_tree"])
        self.assertEqual(report["terminal_receipt_sha256"], baseline["artifacts"]["terminal_run_receipt_sha256"])
        self.assertEqual(report["baseline_receipt_sha256"], artifact_sha256(baseline))

    def test_canonical_inventory_groups_duplicate_report_copies_by_digest(self) -> None:
        report = {"schema_version": "1.0.0", "artifact": "pipeline6-calibration-report"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("one.json", "two.json"):
                (root / name).write_bytes(canonical_bytes(report))
            inventory = canonical_inventory(root)
        reports = [(digest, copies[0]) for digest, copies in inventory.items() if copies[0].get("artifact") == "pipeline6-calibration-report"]
        self.assertEqual(1, len(reports))
        self.assertEqual(2, len(inventory[reports[0][0]]))

    def test_terminal_and_baseline_schemas_reject_stub_json(self) -> None:
        self.assertTrue(schema_diagnostics({}, _ROOT / "schemas" / "terminal-run-receipt.schema.json", _ROOT))
        self.assertTrue(schema_diagnostics({}, _ROOT / "schemas" / "feature-baseline-receipt.schema.json", _ROOT))


if __name__ == "__main__":
    unittest.main()
