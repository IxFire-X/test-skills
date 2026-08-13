from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "stages" / "v3"
SCHEMAS = {
    "context": ROOT / "schemas" / "context-marker-output.schema.json",
    "generator": ROOT / "schemas" / "tc-generator-output.schema.json",
    "reviewer": ROOT / "schemas" / "tc-reviewer-output.schema.json",
}
sys.path.insert(0, str(ROOT))

from tools.canonical_document import document_sha256  # noqa: E402
from tools.schema_validation import load_json_strict, schema_diagnostics  # noqa: E402
from tools.validate_artifact import validate  # noqa: E402


def fixture(name: str) -> dict[str, object]:
    return load_json_strict(FIXTURES / name)


def diagnostics(kind: str, artifact: dict[str, object]) -> list[dict[str, str]]:
    return schema_diagnostics(artifact, SCHEMAS[kind], ROOT)


class V3StageSchemaTests(unittest.TestCase):
    def assert_invalid(self, kind: str, artifact: dict[str, object], path: str) -> None:
        self.assertTrue(any(item["path"] == path for item in diagnostics(kind, artifact)))

    def test_all_five_synthetic_fixtures_validate_through_schema_and_cli(self) -> None:
        """A changed V3 envelope or shared ref must reject a complete valid carrier."""
        cases = (
            ("context", "context-marker.json"),
            ("generator", "tc-generator.json"),
            ("reviewer", "tc-reviewer-accepted.json"),
            ("reviewer", "tc-reviewer-auto-fix.json"),
            ("reviewer", "tc-reviewer-rework.json"),
        )
        for kind, name in cases:
            with self.subTest(name=name):
                self.assertEqual([], diagnostics(kind, fixture(name)))
                exit_code, report = validate(str(SCHEMAS[kind]), str(FIXTURES / name))
                self.assertEqual((0, {"status": "valid", "errors": []}), (exit_code, report))

    def test_every_envelope_is_closed_at_root_and_artifact_level(self) -> None:
        """Missing or extra carrier fields must not be silently accepted by a stage."""
        cases = (
            ("context", "context-marker.json", "analytics_documentation"),
            ("generator", "tc-generator.json", "canonical_document"),
            ("reviewer", "tc-reviewer-accepted.json", "validation_report"),
        )
        for kind, name, artifact_key in cases:
            with self.subTest(kind=kind, mutation="missing root"):
                candidate = fixture(name)
                del candidate["warnings"]
                self.assert_invalid(kind, candidate, "/warnings")
            with self.subTest(kind=kind, mutation="extra root"):
                candidate = fixture(name)
                candidate["extra"] = True
                self.assert_invalid(kind, candidate, "/extra")
            with self.subTest(kind=kind, mutation="missing artifact"):
                candidate = fixture(name)
                del candidate["artifacts"][artifact_key]
                self.assert_invalid(kind, candidate, f"/artifacts/{artifact_key}")
            with self.subTest(kind=kind, mutation="extra artifact"):
                candidate = fixture(name)
                candidate["artifacts"]["extra"] = True
                self.assert_invalid(kind, candidate, "/artifacts/extra")

    def test_context_uses_the_common_requirement_contract(self) -> None:
        """A copied legacy requirement model would permit missing V3 identity fields."""
        mutations = (
            ("legacy id", lambda r: (r.pop("requirement_id"), r.__setitem__("id", "REQ-manual-example"))),
            ("missing display order", lambda r: r.pop("display_order")),
            ("extra field", lambda r: r.__setitem__("extra", True)),
            ("blank canonical text", lambda r: r.__setitem__("text", "   ")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                candidate = fixture("context-marker.json")
                mutate(candidate["artifacts"]["analytics_documentation"]["requirements"][0])
                self.assertNotEqual([], diagnostics("context", candidate))

    def test_generator_rejects_legacy_or_invalid_embedded_document_shapes(self) -> None:
        """A legacy generated-test-cases carrier must not bypass canonical validation."""
        candidate = fixture("tc-generator.json")
        candidate["artifacts"] = {"generated_test_cases": {}}
        self.assert_invalid("generator", candidate, "/artifacts/generated_test_cases")
        candidate = fixture("tc-generator.json")
        candidate["artifacts"]["extra"] = True
        self.assert_invalid("generator", candidate, "/artifacts/extra")
        candidate = fixture("tc-generator.json")
        candidate["artifacts"]["canonical_document"]["test_cases"][0]["priority"] = "BLOCKER"
        self.assertNotEqual([], diagnostics("generator", candidate))

    def test_reviewer_verdict_conditionals_require_exact_successor_and_findings_shape(self) -> None:
        """A wrong verdict branch must not omit, retain, or partially replace a successor."""
        accepted = fixture("tc-reviewer-accepted.json")
        accepted["artifacts"]["successor_document"] = fixture("tc-generator.json")["artifacts"]["canonical_document"]
        self.assertNotEqual([], diagnostics("reviewer", accepted))
        accepted = fixture("tc-reviewer-accepted.json")
        accepted["artifacts"]["validation_report"]["findings"] = [{"severity": "INFO", "code": "X", "message": "x", "evidence": ["e"], "related_ids": ["TC-manual-example"]}]
        self.assertNotEqual([], diagnostics("reviewer", accepted))
        auto = fixture("tc-reviewer-auto-fix.json")
        del auto["artifacts"]["successor_document"]
        self.assert_invalid("reviewer", auto, "/artifacts/successor_document")
        auto = fixture("tc-reviewer-auto-fix.json")
        auto["artifacts"]["validation_report"]["findings"][0]["severity"] = "BLOCKING"
        self.assertNotEqual([], diagnostics("reviewer", auto))
        auto = fixture("tc-reviewer-auto-fix.json")
        auto["artifacts"]["successor_document"] = {"document_id": "TCDOC-manual-example"}
        self.assertNotEqual([], diagnostics("reviewer", auto))
        rework = fixture("tc-reviewer-rework.json")
        rework["artifacts"]["validation_report"]["findings"][0]["severity"] = "WARNING"
        self.assertNotEqual([], diagnostics("reviewer", rework))
        rework = fixture("tc-reviewer-rework.json")
        rework["artifacts"]["validation_report"]["corrections"] = fixture("tc-reviewer-auto-fix.json")["artifacts"]["validation_report"]["corrections"]
        self.assertNotEqual([], diagnostics("reviewer", rework))

    def test_reviewer_rejects_legacy_names_and_noncanonical_digest(self) -> None:
        """Legacy review coverage and partial corrected cases must not remain valid routes."""
        candidate = fixture("tc-reviewer-accepted.json")
        report = candidate["artifacts"]["validation_report"]
        report["reviewed_test_case_ids"] = report.pop("reviewed_case_ids")
        self.assertNotEqual([], diagnostics("reviewer", candidate))
        candidate = fixture("tc-reviewer-accepted.json")
        candidate["artifacts"]["corrected_test_cases"] = []
        self.assert_invalid("reviewer", candidate, "/artifacts/corrected_test_cases")
        candidate = fixture("tc-reviewer-accepted.json")
        candidate["artifacts"]["validation_report"]["candidate"]["document_sha256"] = "sha256:" + "A" * 64
        self.assertNotEqual([], diagnostics("reviewer", candidate))

    def test_reviewer_fixture_lineage_uses_bare_document_bytes(self) -> None:
        """Hashing an envelope would make the candidate/successor lineage dishonest."""
        document = fixture("tc-generator.json")["artifacts"]["canonical_document"]
        report = fixture("tc-reviewer-auto-fix.json")["artifacts"]["validation_report"]
        successor = fixture("tc-reviewer-auto-fix.json")["artifacts"]["successor_document"]
        self.assertEqual(document_sha256(document), report["candidate"]["document_sha256"])
        self.assertEqual(document_sha256(document), successor["parent_sha256"])

    def test_v21_is_a_single_breaking_diagnostic_for_each_converted_schema(self) -> None:
        """Generic V3 schema failures must not hide an explicit incompatible V2.1 input."""
        expected = {
            "status": "invalid",
            "errors": [{
                "path": "/schema_version", "code": "V2_1_BREAKING_CHANGE",
                "message": "schema version 2.1.0 is incompatible with canonical model 3.0.0",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            legacy = Path(temporary) / "legacy.json"
            legacy.write_text(json.dumps({"schema_version": "2.1.0"}), encoding="utf-8")
            for kind, schema in SCHEMAS.items():
                with self.subTest(kind=kind):
                    self.assertEqual((1, expected), validate(str(schema), str(legacy)))

    def test_unknown_v3_uses_schema_diagnostics_and_unreadable_json_remains_exit_two(self) -> None:
        """Only the known breaking version receives its special diagnostic."""
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            unknown = temporary_path / "unknown.json"
            unknown.write_text(json.dumps({"schema_version": "3.7.0"}), encoding="utf-8")
            exit_code, report = validate(str(SCHEMAS["generator"]), str(unknown))
            self.assertEqual(1, exit_code)
            self.assertEqual("invalid", report["status"])
            self.assertNotIn("V2_1_BREAKING_CHANGE", [item.get("code") for item in report["errors"]])
            unreadable = temporary_path / "unreadable.json"
            unreadable.write_text("{", encoding="utf-8")
            exit_code, report = validate(str(SCHEMAS["generator"]), str(unreadable))
            self.assertEqual(2, exit_code)
            self.assertEqual("error", report["status"])

    def test_generator_and_reviewer_reference_the_local_canonical_root_without_copied_cases(self) -> None:
        """A copied case definition could drift from the sole canonical model."""
        for kind in ("generator", "reviewer"):
            with self.subTest(kind=kind):
                schema = load_json_strict(SCHEMAS[kind])
                serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True)
                self.assertIn("schemas/canonical-test-document.schema.json", serialized)
                self.assertNotIn('"test_case"', json.dumps(schema.get("$defs", {}), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
