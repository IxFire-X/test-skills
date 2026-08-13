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
from types import MappingProxyType


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "tests" / "fixtures" / "stages" / "v1"
V5 = ROOT / "tests" / "fixtures" / "stages" / "v5"
sys.path.insert(0, str(ROOT))

from tools.schema_validation import load_json_strict  # noqa: E402
from tools.test_classification import (  # noqa: E402
    TestClassificationError,
    main,
    select_effective_technical_evidence,
    validate_technical_test_evidence,
)
from tools.behavior_context_planning import ValidatedBehaviorContext  # noqa: E402


def stable_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class TechnicalTestClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / "fixtures" / "test-classification" / "project"
        self.requirements = (
            {"requirement_id": "REQ-a", "display_order": 1},
            {"requirement_id": "REQ-b", "display_order": 2},
        )

    def inventory(self) -> dict:
        return copy.deepcopy(load_json_strict(V1 / "source-inventory.json"))

    def context(self) -> ValidatedBehaviorContext:
        return ValidatedBehaviorContext(tuple(self.requirements), "sha256:" + "0" * 64, "sha256:" + "0" * 64)

    def classification(self, scope: str = "integration") -> dict:
        result = copy.deepcopy(load_json_strict(V1 / "test-classifier.json"))
        for row in result["artifacts"]["classification"]["classifications"]:
            row["test_scope"] = scope
        return result

    def review(self, candidate: dict, verdict: str = "ПРИНЯТО") -> dict:
        result = copy.deepcopy(load_json_strict(V1 / "test-classifier-reviewer-accepted.json"))
        bare = result["artifacts"]["classification_review"]
        bare["classification_sha256"] = stable_digest(candidate["artifacts"]["classification"])
        bare["verdict"] = verdict
        bare["findings"] = [] if verdict == "ПРИНЯТО" else [{
            "path": "/artifacts/classification/classifications/0/test_scope",
            "code": "SCOPE_REWORK",
            "message": "The independently reviewed scope needs rework.",
            "file_id": bare["reviewed_symbol_pairs"][0]["file_id"],
            "symbol_id": bare["reviewed_symbol_pairs"][0]["symbol_id"],
        }]
        return result

    def diagnostics(self, candidate: dict, review: dict) -> tuple[dict, ...]:
        return validate_technical_test_evidence(self.inventory(), candidate, review, self.requirements, self.root)

    def assert_diagnostic(self, code: str, path: str, candidate: dict, review: dict) -> None:
        rows = self.diagnostics(candidate, review)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual(code, rows[0]["code"])
        self.assertEqual(path, rows[0]["path"])

    def test_all_closed_scope_values_are_preserved(self) -> None:
        for scope in ("unit", "integration", "e2e", "unknown"):
            with self.subTest(scope=scope):
                candidate = self.classification(scope)
                review = self.review(candidate)
                self.assertEqual((), self.diagnostics(candidate, review))
                selected = select_effective_technical_evidence(self.inventory(), candidate, review, self.context(), self.root)
                self.assertEqual([scope, scope], [row["test_scope"] for row in selected["classifications"]])

    def test_unknown_is_valid_but_cannot_be_upgraded(self) -> None:
        candidate = self.classification(scope="unknown")
        review = self.review(candidate)
        self.assertEqual((), self.diagnostics(candidate, review))
        selected = select_effective_technical_evidence(self.inventory(), candidate, review, self.context(), self.root)
        self.assertEqual("unknown", selected["classifications"][0]["test_scope"])

    def test_classification_pairs_must_exactly_match_canonical_inventory_order(self) -> None:
        cases = []
        missing = self.classification(); missing["artifacts"]["classification"]["classifications"].pop(); cases.append(("missing", missing))
        extra = self.classification(); extra["artifacts"]["classification"]["classifications"].append(copy.deepcopy(extra["artifacts"]["classification"]["classifications"][0])); extra["artifacts"]["classification"]["classifications"][-1]["symbol_id"] = "SYMBOL-" + "a" * 64; cases.append(("extra", extra))
        duplicate = self.classification(); duplicate["artifacts"]["classification"]["classifications"][1] = copy.deepcopy(duplicate["artifacts"]["classification"]["classifications"][0]); cases.append(("duplicate", duplicate))
        reordered = self.classification(); reordered["artifacts"]["classification"]["classifications"].reverse(); cases.append(("reordered", reordered))
        for name, candidate in cases:
            with self.subTest(name=name):
                self.assert_diagnostic("CLASSIFICATION_PAIR_COVERAGE", "/artifacts/classification/classifications", candidate, self.review(candidate))

    def test_requirement_links_must_be_known_unique_and_requirements_ordered(self) -> None:
        cases = []
        foreign = self.classification(); foreign["artifacts"]["classification"]["classifications"][0]["requirement_ids"] = ["REQ-foreign"]; cases.append(("foreign", foreign, "CLASSIFICATION_REQUIREMENT_LINK", "/artifacts/classification/classifications/0/requirement_ids/0"))
        duplicate = self.classification(); duplicate["artifacts"]["classification"]["classifications"][0]["requirement_ids"] = ["REQ-a", "REQ-a"]; cases.append(("duplicate", duplicate, "CLASSIFICATION_REQUIREMENT_LINK", "/artifacts/classification/classifications/0/requirement_ids"))
        unsorted = self.classification(); unsorted["artifacts"]["classification"]["classifications"][0]["requirement_ids"] = ["REQ-b", "REQ-a"]; cases.append(("unsorted", unsorted, "CLASSIFICATION_REQUIREMENT_ORDER", "/artifacts/classification/classifications/0/requirement_ids"))
        for name, candidate, code, path in cases:
            with self.subTest(name=name):
                self.assert_diagnostic(code, path, candidate, self.review(candidate))

    def test_provenance_spans_are_owned_unique_ordered_and_physical(self) -> None:
        cases = []
        foreign = self.classification(); foreign["artifacts"]["classification"]["classifications"][0]["provenance"][0]["file_id"] = "FILE-" + "a" * 64; cases.append(("foreign", foreign, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance/0/file_id"))
        zero = self.classification(); zero["artifacts"]["classification"]["classifications"][0]["provenance"][0]["start_line"] = 0; cases.append(("zero", zero, "CLASSIFICATION_SCHEMA", "/artifacts/classification/classifications/0/provenance/0/start_line"))
        reversed_span = self.classification(); reversed_span["artifacts"]["classification"]["classifications"][0]["provenance"][0].update({"start_line": 3, "end_line": 2}); cases.append(("reversed", reversed_span, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance/0"))
        out_of_range = self.classification(); out_of_range["artifacts"]["classification"]["classifications"][0]["provenance"][0]["end_line"] = 99; cases.append(("out_of_range", out_of_range, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance/0/end_line"))
        boolean = self.classification(); boolean["artifacts"]["classification"]["classifications"][0]["provenance"][0]["start_line"] = True; cases.append(("boolean", boolean, "CLASSIFICATION_SCHEMA", "/artifacts/classification/classifications/0/provenance/0/start_line"))
        floating = self.classification(); floating["artifacts"]["classification"]["classifications"][0]["provenance"][0]["end_line"] = 1.0; cases.append(("floating", floating, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance/0/end_line"))
        duplicate = self.classification(); span = copy.deepcopy(duplicate["artifacts"]["classification"]["classifications"][0]["provenance"][0]); duplicate["artifacts"]["classification"]["classifications"][0]["provenance"].append(span); cases.append(("duplicate", duplicate, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance"))
        reordered = self.classification(); reordered["artifacts"]["classification"]["classifications"][0]["provenance"].append({"file_id": reordered["artifacts"]["classification"]["classifications"][0]["file_id"], "start_line": 2, "end_line": 2}); reordered["artifacts"]["classification"]["classifications"][0]["provenance"].reverse(); cases.append(("reordered", reordered, "CLASSIFICATION_PROVENANCE", "/artifacts/classification/classifications/0/provenance"))
        for name, candidate, code, path in cases:
            with self.subTest(name=name):
                self.assert_diagnostic(code, path, candidate, self.review(candidate))

    def test_declared_inventory_and_classification_digests_must_match_bare_sources(self) -> None:
        candidate = self.classification(); candidate["artifacts"]["classification"]["technical_test_inventory_sha256"] = "sha256:" + "a" * 64
        self.assert_diagnostic("CLASSIFICATION_INVENTORY_DIGEST", "/artifacts/classification/technical_test_inventory_sha256", candidate, self.review(candidate))
        candidate = self.classification(); review = self.review(candidate); review["artifacts"]["classification_review"]["classification_sha256"] = "sha256:" + "a" * 64
        self.assert_diagnostic("CLASSIFICATION_REVIEW_DIGEST", "/artifacts/classification_review/classification_sha256", candidate, review)

    def test_inventory_file_drift_is_rejected(self) -> None:
        candidate = self.classification(); review = self.review(candidate)
        target = self.root / "tests" / "test_sample.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            self.assert_diagnostic("CLASSIFICATION_FILE_DRIFT", "/artifacts/technical_test_inventory/files/0/content_digest", candidate, review)
        finally:
            target.write_bytes(original)

    def test_acceptance_requires_empty_findings_and_rework_requires_findings(self) -> None:
        candidate = self.classification()
        accepted = self.review(candidate); accepted["artifacts"]["classification_review"]["findings"] = [{"path": "/x", "code": "X", "message": "x"}]
        self.assert_diagnostic("CLASSIFICATION_REVIEW_VERDICT", "/artifacts/classification_review/findings", candidate, accepted)
        rework = self.review(candidate, "ТРЕБУЕТ ДОРАБОТКИ"); rework["artifacts"]["classification_review"]["findings"] = []
        self.assert_diagnostic("CLASSIFICATION_REVIEW_VERDICT", "/artifacts/classification_review/findings", candidate, rework)

    def test_acceptance_requires_every_pair_in_canonical_order(self) -> None:
        candidate = self.classification()
        for name, change in (
            ("missing", lambda rows: rows.pop()),
            ("extra", lambda rows: rows.append({"file_id": rows[0]["file_id"], "symbol_id": "SYMBOL-" + "a" * 64})),
            ("duplicate", lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0]))),
            ("reordered", lambda rows: rows.reverse()),
        ):
            with self.subTest(name=name):
                review = self.review(candidate)
                change(review["artifacts"]["classification_review"]["reviewed_symbol_pairs"])
                self.assert_diagnostic("CLASSIFICATION_REVIEW_COVERAGE", "/artifacts/classification_review/reviewed_symbol_pairs", candidate, review)

    def test_result_and_error_diagnostics_are_recursively_immutable(self) -> None:
        candidate = self.classification(); review = self.review(candidate)
        selected = select_effective_technical_evidence(self.inventory(), candidate, review, self.context(), self.root)
        self.assertIsInstance(selected, MappingProxyType)
        with self.assertRaises(TypeError): selected["files"][0]["path"] = "mutated.py"
        malformed = self.classification(); malformed["artifacts"]["classification"]["classifications"].pop()
        with self.assertRaises(TestClassificationError) as raised:
            select_effective_technical_evidence(self.inventory(), malformed, self.review(malformed), self.context(), self.root)
        self.assertIsInstance(raised.exception.diagnostics[0], MappingProxyType)
        with self.assertRaises(TypeError): raised.exception.diagnostics[0]["code"] = "MUTATED"

    def test_public_diagnostics_are_recursively_immutable_and_never_echo_invalid_values(self) -> None:
        candidate = self.classification()
        candidate["artifacts"]["classification"]["TOPSECRET"] = "invalid"
        rows = self.diagnostics(candidate, self.review(candidate))
        self.assertIsInstance(rows[0], MappingProxyType)
        with self.assertRaises(TypeError): rows[0]["message"] = "MUTATED"
        self.assertNotIn("TOPSECRET", rows[0]["message"])

    def test_self_consistent_noncanonical_inventory_symbol_order_is_rejected(self) -> None:
        inventory = self.inventory()
        symbols = inventory["artifacts"]["technical_test_inventory"]["symbols"]
        symbols.reverse()
        inventory["artifacts"]["technical_test_inventory_sha256"] = stable_digest(inventory["artifacts"]["technical_test_inventory"])
        candidate = self.classification()
        candidate_rows = candidate["artifacts"]["classification"]["classifications"]
        candidate_rows.reverse()
        candidate["artifacts"]["classification"]["technical_test_inventory_sha256"] = inventory["artifacts"]["technical_test_inventory_sha256"]
        review = self.review(candidate)
        review_rows = review["artifacts"]["classification_review"]
        review_rows["technical_test_inventory_sha256"] = inventory["artifacts"]["technical_test_inventory_sha256"]
        review_rows["reviewed_symbol_pairs"].reverse()
        rows = validate_technical_test_evidence(inventory, candidate, review, self.requirements, self.root)
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("INVENTORY_SYMBOL_ORDER", rows[0]["code"])
        self.assertEqual("/artifacts/technical_test_inventory/symbols", rows[0]["path"])

    def test_pair_and_provenance_errors_precede_file_drift(self) -> None:
        candidate = self.classification()
        candidate["artifacts"]["classification"]["classifications"].pop()
        candidate["artifacts"]["classification"]["classifications"][0]["provenance"][0]["file_id"] = "FILE-" + "a" * 64
        review = self.review(candidate)
        target = self.root / "tests" / "test_sample.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            self.assert_diagnostic("CLASSIFICATION_PAIR_COVERAGE", "/artifacts/classification/classifications", candidate, review)
        finally:
            target.write_bytes(original)

    def test_legacy_envelopes_are_rejected_before_artifact_extraction(self) -> None:
        legacy = self.classification(); legacy["schema_version"] = "3.0.0"
        self.assert_diagnostic("CLASSIFICATION_SCHEMA", "/schema_version", legacy, self.review(legacy))

    def test_cli_select_is_create_only_and_rework_creates_no_carrier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            output = temporary / "effective.json"
            args = ["select", "--project", str(self.root), "--inventory", str(V1 / "source-inventory.json"), "--classification", str(V1 / "test-classifier.json"), "--review", str(V1 / "test-classifier-reviewer-accepted.json"), "--context", str(V5 / "context-marker.json"), "--receipt", str(V5 / "receipt.json"), "--output", str(output)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(0, main(args))
                self.assertEqual(2, main(args))
            self.assertTrue(output.exists())
            rework_output = temporary / "rework.json"
            rework_args = list(args)
            rework_args[rework_args.index("--review") + 1] = str(V1 / "test-classifier-reviewer-rework.json")
            rework_args[rework_args.index("--output") + 1] = str(rework_output)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(rework_args))
            self.assertFalse(rework_output.exists())
            legacy_context = temporary / "legacy.json"; legacy_context.write_text(json.dumps({"schema_version": "3.0.0", "stage": "context-marker", "warnings": [], "artifacts": {}}), encoding="utf-8")
            legacy_args = list(args)
            legacy_args[legacy_args.index("--context") + 1] = str(legacy_context)
            legacy_args[legacy_args.index("--output") + 1] = str(temporary / "legacy-effective.json")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(2, main(legacy_args))
            self.assertIn("BEHAVIOR_ACCOUNTING_SHAPE", stderr.getvalue())
