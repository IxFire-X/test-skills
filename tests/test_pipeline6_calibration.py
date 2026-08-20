from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tools.schema_validation import schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals" / "change-scoped-semantic-evidence" / "scenarios.json"
SCHEMA = ROOT / "schemas" / "pipeline6-calibration-report.schema.json"
PHASES = ["scope_selection", "scope_false_inclusion", "scope_omission", "candidate_extraction", "batch_false_claim", "batch_omission", "promotion"]
CATEGORIES = ["registered_route", "public_contract", "runtime_config", "authorization_behavior", "domain_validation", "state_transition", "serialization_api", "task_event", "import_export_report", "public_plugin_seam", "no_fact", "boundary_spanning"]


def digest(char: str) -> str:
    return "sha256:" + char * 64


def valid_report(controls: list[dict[str, object]]) -> dict[str, object]:
    phase_rows = [{"phase": phase, "expected_verdict": "PASS", "actual_verdict": "PASS", "matches_expected": True, "artifact_sha256s": [digest("a")]} for phase in PHASES]
    return {
        "schema_version": "1.0.0", "artifact": "pipeline6-calibration-report",
        "suite": "change-scoped-semantic-evidence", "suite_sha256": digest("b"),
        "review_mode": "SEQUENTIAL", "target": {"commit": "c" * 40, "tree": "d" * 40, "clean": True},
        "attempt_root_sha256": digest("e"),
        "controls": [{"control_id": row["id"], "phases": copy.deepcopy(phase_rows), "mismatch_count": 0, "status": "PASS"} for row in controls],
        "terminal_receipt_sha256": digest("f"), "baseline_receipt_sha256": digest("0"),
        "mismatch_count": 0, "overall": "PASS",
    }


def report_errors(report: dict[str, object], controls: list[dict[str, object]]) -> list[object]:
    errors: list[object] = list(schema_diagnostics(report, SCHEMA, ROOT))
    expected_ids = [row["id"] for row in controls]
    actual_ids = [row.get("control_id") for row in report.get("controls", []) if isinstance(row, dict)]
    if actual_ids != expected_ids:
        errors.append("control IDs must exactly match the fixed scenario order")
    control_mismatches: list[int] = []
    for control in report.get("controls", []):
        if not isinstance(control, dict):
            continue
        phases = control.get("phases")
        if not isinstance(phases, list):
            continue
        if [row.get("phase") for row in phases if isinstance(row, dict)] != PHASES:
            errors.append("phase names must exactly match the fixed phase order")
        mismatches = 0
        for row in phases:
            if not isinstance(row, dict):
                continue
            if row.get("expected_verdict") != "PASS":
                errors.append("fixed phase expectation must be PASS")
            matches = row.get("matches_expected")
            if matches is not (row.get("expected_verdict") == row.get("actual_verdict")):
                errors.append("matches_expected must equal verdict equality")
            if matches is False:
                mismatches += 1
        if control.get("mismatch_count") != mismatches:
            errors.append("control mismatch_count must equal false phase count")
        if control.get("status") != ("PASS" if mismatches == 0 else "FAIL"):
            errors.append("control status must match its mismatch count")
        control_mismatches.append(mismatches)
    if len(control_mismatches) != 24 or len(report.get("controls", [])) != 24:
        errors.append("report must contain exactly 24 controls")
    total = sum(control_mismatches)
    if report.get("mismatch_count") != total:
        errors.append("overall mismatch_count must equal control mismatch sum")
    if report.get("overall") != ("PASS" if total == 0 else "FAIL"):
        errors.append("overall status must match aggregate mismatches")
    return errors


class Pipeline6CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = json.loads(SUITE.read_text(encoding="utf-8"))
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_fixed_suite_has_exact_12_by_2_controls_and_regressions(self) -> None:
        self.assertEqual("change-scoped-semantic-evidence", self.suite["suite"])
        self.assertEqual("SEQUENTIAL", self.suite["controller"])
        self.assertEqual(PHASES, self.suite["phases"])
        self.assertEqual(CATEGORIES, self.suite["categories"])
        controls = self.suite["controls"]
        self.assertEqual(24, len(controls))
        self.assertEqual(
            [f"{category}.{variant}" for category in CATEGORIES for variant in ("supported", "deceptive_or_no_fact")],
            [row["id"] for row in controls],
        )
        self.assertTrue(all(row["synthetic"] is True for row in controls))
        tags = {tag for row in controls for tag in row["coverage_tags"]}
        self.assertTrue({"url-path-helper-false-route", "public-readme-false-no-fact", "runtime-config-omission"}.issubset(tags))
        serialized = json.dumps(controls, ensure_ascii=False).lower()
        self.assertNotIn("inventree", serialized)
        self.assertNotIn("d:\\\\", serialized)

    def test_rubric_names_every_phase_in_order_and_forbids_raw_evidence(self) -> None:
        rubric = (SUITE.parent / "rubric.md").read_text(encoding="utf-8")
        positions = [rubric.index(f"`{phase}`") for phase in PHASES]
        self.assertEqual(sorted(positions), positions)
        for phrase in ("raw source", "prompts", "model reasoning", "secrets", "absolute artifact paths"):
            self.assertIn(phrase, rubric)

    def test_calibration_report_is_closed_and_pass_requires_zero_mismatches(self) -> None:
        report = valid_report(self.suite["controls"])
        self.assertEqual([], report_errors(report, self.suite["controls"]))
        failed = copy.deepcopy(report)
        failed["controls"][0]["phases"][0].update({"actual_verdict": "FAIL", "matches_expected": False})
        failed["controls"][0].update({"mismatch_count": 1, "status": "FAIL"})
        failed.update({"mismatch_count": 1, "overall": "FAIL"})
        self.assertEqual([], report_errors(failed, self.suite["controls"]))
        mutations = []
        extra = copy.deepcopy(report); extra["raw_source"] = "secret"; mutations.append(extra)
        path = copy.deepcopy(report); path["absolute_path"] = "D:/private/run"; mutations.append(path)
        failed_zero = copy.deepcopy(report); failed_zero["overall"] = "FAIL"; mutations.append(failed_zero)
        pass_nonzero = copy.deepcopy(report); pass_nonzero["mismatch_count"] = 1; mutations.append(pass_nonzero)
        control_nonzero = copy.deepcopy(report); control_nonzero["controls"][0]["mismatch_count"] = 1; mutations.append(control_nonzero)
        hidden_mismatch = copy.deepcopy(report); hidden_mismatch["controls"][0]["phases"][0]["matches_expected"] = False; mutations.append(hidden_mismatch)
        inverted_match = copy.deepcopy(report); inverted_match["controls"][0]["phases"][0]["actual_verdict"] = "FAIL"; mutations.append(inverted_match)
        matched_fail = copy.deepcopy(report); matched_fail["controls"][0]["phases"][0].update({"expected_verdict": "FAIL", "actual_verdict": "FAIL", "matches_expected": True}); mutations.append(matched_fail)
        reordered = copy.deepcopy(report); reordered["controls"][0]["phases"].reverse(); mutations.append(reordered)
        short = copy.deepcopy(report); short["controls"].pop(); mutations.append(short)
        duplicate = copy.deepcopy(report); duplicate["controls"][-1]["control_id"] = duplicate["controls"][0]["control_id"]; mutations.append(duplicate)
        non_sequential = copy.deepcopy(report); non_sequential["review_mode"] = "INDEPENDENT"; mutations.append(non_sequential)
        wrong_sum = copy.deepcopy(failed); wrong_sum["mismatch_count"] = 2; mutations.append(wrong_sum)
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                self.assertNotEqual([], report_errors(candidate, self.suite["controls"]))


if __name__ == "__main__":
    unittest.main()
