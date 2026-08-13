from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "stages" / "v3"
AUTOMATION_SCHEMA = ROOT / "schemas" / "tc-to-autotest-output.schema.json"
REVIEW_SCHEMA = ROOT / "schemas" / "autotest-reviewer-output.schema.json"
sys.path.insert(0, str(ROOT))

from tests.fixture_factory import canonical_document  # noqa: E402
from tools.automation_validation import (  # noqa: E402
    AutomationArtifactError,
    required_symbol_pairs,
    validate_automation_artifact,
)
from tools.canonical_document import document_sha256, validate_canonical_document  # noqa: E402
from tools.schema_validation import load_json_strict, schema_diagnostics  # noqa: E402


def fixture(name: str) -> dict:
    return load_json_strict(FIXTURES / name)


def source_for(document: dict) -> dict[str, object]:
    return {
        "document_id": document["document_id"],
        "revision": document["revision"],
        "source_digest": document_sha256(document),
    }


def generated(document: dict | None = None) -> dict:
    artifact = fixture("tc-to-autotest-generated.json")
    artifact["artifacts"]["source"] = source_for(document or canonical_document())
    return artifact


def manual_document() -> dict:
    document = canonical_document()
    step = document["test_cases"][0]["steps"][0]
    step.update({
        "manual_only": True,
        "manual_reason": "Physical confirmation is required.",
        "operation": None,
        "inputs": [],
        "outputs": [],
    })
    step["expectations"][0]["assertions"] = []
    assert validate_canonical_document(document) == []
    return document


def blocked_document() -> dict:
    document = canonical_document()
    step = document["test_cases"][0]["steps"][0]
    step.update({"operation": None, "inputs": [], "outputs": []})
    step["automation_blockers"] = [{
        "blocker_id": "BLOCK-operation",
        "code": "UNRESOLVED_OPERATION",
        "field_path": "/operation",
        "reason": "Operation contract is unavailable.",
        "provenance": ["https://example.invalid/context"],
    }]
    assert validate_canonical_document(document) == []
    return document


def manualize(step: dict) -> None:
    step.update({
        "manual_only": True,
        "manual_reason": "Physical confirmation is required.",
        "operation": None,
        "inputs": [],
        "outputs": [],
    })
    step["expectations"][0]["assertions"] = []


class AutomationRelationTests(unittest.TestCase):
    def assert_diagnostic(self, artifact: dict, document: dict, code: str, path: str) -> None:
        self.assertIn(
            (code, path),
            {(item["code"], item["path"]) for item in validate_automation_artifact(artifact, document)},
        )

    def test_three_branch_fixtures_validate_and_v21_is_exact(self) -> None:
        """Changing branch carriers must not leave the legacy matrix route alive."""
        cases = (
            ("tc-to-autotest-generated.json", canonical_document()),
            ("tc-to-autotest-manual-only.json", manual_document()),
            ("tc-to-autotest-blocked.json", blocked_document()),
        )
        for name, document in cases:
            with self.subTest(name=name):
                artifact = fixture(name)
                artifact["artifacts"]["source"] = source_for(document)
                self.assertEqual([], validate_automation_artifact(artifact, document))
        legacy = {"schema_version": "2.1.0"}
        self.assertEqual([{
            "path": "/schema_version", "code": "V2_1_BREAKING_CHANGE",
            "message": "schema version 2.1.0 is incompatible with canonical model 3.0.0",
        }], validate_automation_artifact(legacy, canonical_document()))

    def test_source_and_closed_shape_table(self) -> None:
        """An artifact must name exact bare canonical bytes and contain no transport extensions."""
        document = canonical_document()
        cases = (
            ("document", lambda value: value["artifacts"]["source"].__setitem__("document_id", "TCDOC-other"), "AUTOMATION_SOURCE_DOCUMENT", "/artifacts/source/document_id"),
            ("revision", lambda value: value["artifacts"]["source"].__setitem__("revision", True), "SCHEMA_TYPE", "/artifacts/source/revision"),
            ("bare digest", lambda value: value["artifacts"]["source"].__setitem__("source_digest", "sha256:" + "b" * 64), "AUTOMATION_SOURCE_DIGEST", "/artifacts/source/source_digest"),
            ("envelope digest", lambda value: value["artifacts"]["source"].__setitem__("source_digest", document_sha256({"document": document})), "AUTOMATION_SOURCE_DIGEST", "/artifacts/source/source_digest"),
            ("uppercase digest", lambda value: value["artifacts"]["generated_files"][0].__setitem__("content_digest", "sha256:" + "A" * 64), "SCHEMA_PATTERN", "/artifacts/generated_files/0/content_digest"),
            ("extra root", lambda value: value.__setitem__("extra", True), "SCHEMA_ADDITIONAL_PROPERTIES", "/extra"),
            ("extra artifact", lambda value: value["artifacts"].__setitem__("extra", True), "SCHEMA_ADDITIONAL_PROPERTIES", "/artifacts/extra"),
        )
        for name, mutate, code, path in cases:
            with self.subTest(name=name):
                artifact = generated(document)
                mutate(artifact)
                self.assert_diagnostic(artifact, document, code, path)

    def test_file_symbol_registry_and_portable_path_table(self) -> None:
        """Registry identity must not collapse files, local symbols, or unsafe portable paths."""
        document = canonical_document()
        cases = []
        duplicate_file = generated(document)
        duplicate_file["artifacts"]["generated_files"].append(copy.deepcopy(duplicate_file["artifacts"]["generated_files"][0]))
        cases.append((duplicate_file, "AUTOMATION_DUPLICATE_FILE", "/artifacts/generated_files/1/file_id"))
        duplicate_pair = generated(document)
        duplicate_pair["artifacts"]["generated_symbols"].append(copy.deepcopy(duplicate_pair["artifacts"]["generated_symbols"][0]))
        cases.append((duplicate_pair, "AUTOMATION_DUPLICATE_SYMBOL", "/artifacts/generated_symbols/1/symbol_id"))
        unknown_file = generated(document)
        unknown_file["artifacts"]["generated_symbols"][0]["file_id"] = "FILE-missing"
        cases.append((unknown_file, "AUTOMATION_UNKNOWN_FILE", "/artifacts/generated_symbols/0/file_id"))
        duplicate_locator = generated(document)
        duplicate_locator["artifacts"]["generated_symbols"].append({"file_id": "FILE-orders", "symbol_id": "SYMBOL-other", "locator": copy.deepcopy(duplicate_locator["artifacts"]["generated_symbols"][0]["locator"])})
        cases.append((duplicate_locator, "AUTOMATION_DUPLICATE_LOCATOR", "/artifacts/generated_symbols/1/locator"))
        orphan_symbol = generated(document)
        orphan_symbol["artifacts"]["implementation_relations"] = []
        cases.append((orphan_symbol, "AUTOMATION_ORPHAN_SYMBOL", "/artifacts/generated_symbols/0"))
        duplicate_path = generated(document)
        duplicate_path["artifacts"]["generated_files"].append({"file_id": "FILE-second", "path": "tests/test_orders.py", "language": "python", "framework": "unittest", "content_digest": "sha256:" + "b" * 64})
        cases.append((duplicate_path, "AUTOMATION_DUPLICATE_PATH", "/artifacts/generated_files/1/path"))
        mixed_language = generated(document)
        mixed_language["artifacts"]["generated_files"].append({"file_id": "FILE-java", "path": "tests/OrderTest.java", "language": "java", "framework": "JUnit", "content_digest": "sha256:" + "b" * 64})
        cases.append((mixed_language, "AUTOMATION_MIXED_LANGUAGE", "/artifacts/generated_files/1/language"))
        for unsafe in ("/root.py", "dir\\root.py", "C:root.py", "a//b.py", "a/./b.py", "a/../b.py", "a/b. ", "a/b.\u0000py", "a/b\u001f.py", "a/b\u007f.py"):
            candidate = generated(document)
            candidate["artifacts"]["generated_files"][0]["path"] = unsafe
            cases.append((candidate, "AUTOMATION_PORTABLE_PATH", "/artifacts/generated_files/0/path"))
        for artifact, code, path in cases:
            with self.subTest(code=code, path=path):
                self.assert_diagnostic(artifact, document, code, path)

        shared = generated(document)
        shared["artifacts"]["generated_files"].append({"file_id": "FILE-second", "path": "tests/test_second.py", "language": "python", "framework": "pytest", "content_digest": "sha256:" + "b" * 64})
        shared["artifacts"]["generated_symbols"].append({"file_id": "FILE-second", "symbol_id": "SYMBOL-get-order", "locator": {"kind": "python_module_function", "function_name": "test_second"}})
        second_operation = copy.deepcopy(shared["artifacts"]["implementation_relations"][0])
        second_assertion = copy.deepcopy(shared["artifacts"]["implementation_relations"][1])
        second_operation.update({"file_id": "FILE-second", "symbol_id": "SYMBOL-get-order"})
        second_assertion.update({"file_id": "FILE-second", "symbol_id": "SYMBOL-get-order"})
        shared["artifacts"]["implementation_relations"] = [
            shared["artifacts"]["implementation_relations"][0], second_operation,
            shared["artifacts"]["implementation_relations"][1], second_assertion,
        ]
        self.assertEqual({("FILE-orders", "SYMBOL-get-order"), ("FILE-second", "SYMBOL-get-order")}, required_symbol_pairs(shared, document))

    def test_locator_variants_and_rejections_table(self) -> None:
        """A locator must be one executable, language-matched identifier variant."""
        document = canonical_document()
        python_class = generated(document)
        python_class["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "python_class_method", "qualified_class_name": "TestOrders.Nested", "method_name": "test_get_order"}
        java = generated(document)
        java["artifacts"]["generated_files"][0].update({"path": "tests/OrderTest.java", "language": "java", "framework": "JUnit"})
        java["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "java_class_method", "class_fqn": "com.example.OrderTest", "method_name": "getOrder"}
        for artifact in (generated(document), python_class, java):
            self.assertEqual([], validate_automation_artifact(artifact, document))
        cases = (
            (lambda value: value["artifacts"]["generated_symbols"][0]["locator"].pop("function_name"), "SCHEMA_ONE_OF", "/artifacts/generated_symbols/0/locator"),
            (lambda value: value["artifacts"]["generated_symbols"][0]["locator"].__setitem__("method_name", "extra"), "SCHEMA_ONE_OF", "/artifacts/generated_symbols/0/locator"),
            (lambda value: value["artifacts"]["generated_symbols"][0]["locator"].__setitem__("function_name", "class"), "AUTOMATION_INVALID_PYTHON_IDENTIFIER", "/artifacts/generated_symbols/0/locator/function_name"),
            (lambda value: value["artifacts"]["generated_symbols"][0]["locator"].__setitem__("function_name", "not-valid"), "AUTOMATION_INVALID_PYTHON_IDENTIFIER", "/artifacts/generated_symbols/0/locator/function_name"),
        )
        for mutate, code, path in cases:
            with self.subTest(code=code):
                artifact = generated(document)
                mutate(artifact)
                self.assert_diagnostic(artifact, document, code, path)
        cross_language = generated(document)
        cross_language["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "java_class_method", "class_fqn": "com.example.OrderTest", "method_name": "getOrder"}
        self.assert_diagnostic(cross_language, document, "AUTOMATION_LOCATOR_LANGUAGE", "/artifacts/generated_symbols/0/locator/kind")
        java_keyword = copy.deepcopy(java)
        java_keyword["artifacts"]["generated_symbols"][0]["locator"]["class_fqn"] = "com.class.OrderTest"
        self.assert_diagnostic(java_keyword, document, "AUTOMATION_INVALID_JAVA_IDENTIFIER", "/artifacts/generated_symbols/0/locator/class_fqn")

    def test_relation_parent_order_and_coverage_table(self) -> None:
        """Relations are ordered atomic parent chains, not case-level Cartesian coverage."""
        document = canonical_document(2)
        artifact = generated(document)
        artifact["artifacts"]["implementation_relations"] = [
            {"kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "file_id": "FILE-orders", "symbol_id": "SYMBOL-get-order"},
            {"kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "expectation_id": "EXP-1", "assertion_id": "ASSERT-1", "file_id": "FILE-orders", "symbol_id": "SYMBOL-get-order"},
            {"kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-2", "file_id": "FILE-orders", "symbol_id": "SYMBOL-get-order"},
            {"kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-2", "expectation_id": "EXP-2", "assertion_id": "ASSERT-2", "file_id": "FILE-orders", "symbol_id": "SYMBOL-get-order"},
        ]
        self.assertEqual([], validate_automation_artifact(artifact, document))
        cases = []
        foreign_step = copy.deepcopy(artifact)
        foreign_step["artifacts"]["implementation_relations"][0]["step_id"] = "STEP-unknown"
        cases.append((foreign_step, "AUTOMATION_UNKNOWN_STEP", "/artifacts/implementation_relations/0/step_id"))
        foreign_expectation = copy.deepcopy(artifact)
        foreign_expectation["artifacts"]["implementation_relations"][1]["expectation_id"] = "EXP-2"
        cases.append((foreign_expectation, "AUTOMATION_UNKNOWN_EXPECTATION", "/artifacts/implementation_relations/1/expectation_id"))
        unknown_pair = copy.deepcopy(artifact)
        unknown_pair["artifacts"]["implementation_relations"][0]["symbol_id"] = "SYMBOL-unknown"
        cases.append((unknown_pair, "AUTOMATION_UNKNOWN_SYMBOL", "/artifacts/implementation_relations/0/symbol_id"))
        duplicate = copy.deepcopy(artifact)
        duplicate["artifacts"]["implementation_relations"].append(copy.deepcopy(duplicate["artifacts"]["implementation_relations"][-1]))
        cases.append((duplicate, "AUTOMATION_DUPLICATE_RELATION", "/artifacts/implementation_relations/4"))
        reversed_rows = copy.deepcopy(artifact)
        reversed_rows["artifacts"]["implementation_relations"][0:2] = reversed_rows["artifacts"]["implementation_relations"][0:2][::-1]
        cases.append((reversed_rows, "AUTOMATION_RELATION_ORDER", "/artifacts/implementation_relations"))
        missing_operation = copy.deepcopy(artifact)
        del missing_operation["artifacts"]["implementation_relations"][2]
        cases.append((missing_operation, "AUTOMATION_MISSING_OPERATION_COVERAGE", "/test_cases/0/steps/1"))
        missing_assertion = copy.deepcopy(artifact)
        del missing_assertion["artifacts"]["implementation_relations"][3]
        cases.append((missing_assertion, "AUTOMATION_MISSING_ASSERTION_COVERAGE", "/test_cases/0/steps/1/expectations/0/assertions/0"))
        for candidate, code, path in cases:
            with self.subTest(code=code):
                self.assert_diagnostic(candidate, document, code, path)

        for name, candidate, expected in (
            ("unknown runtime pair", unknown_pair, {
                ("AUTOMATION_UNKNOWN_SYMBOL", "/artifacts/implementation_relations/0/symbol_id"),
                ("AUTOMATION_MISSING_OPERATION_COVERAGE", "/test_cases/0/steps/0"),
            }),
            ("foreign parent", foreign_step, {
                ("AUTOMATION_UNKNOWN_STEP", "/artifacts/implementation_relations/0/step_id"),
                ("AUTOMATION_MISSING_OPERATION_COVERAGE", "/test_cases/0/steps/0"),
            }),
        ):
            with self.subTest(name=name):
                actual = {(item["code"], item["path"]) for item in validate_automation_artifact(candidate, document)}
                self.assertTrue(expected <= actual)

        and_artifact = generated(canonical_document())
        and_artifact["artifacts"]["generated_files"].append({"file_id": "FILE-second", "path": "tests/test_second.py", "language": "python", "framework": "pytest", "content_digest": "sha256:" + "b" * 64})
        and_artifact["artifacts"]["generated_symbols"].append({"file_id": "FILE-second", "symbol_id": "SYMBOL-second", "locator": {"kind": "python_module_function", "function_name": "test_second"}})
        and_artifact["artifacts"]["implementation_relations"] = [
            and_artifact["artifacts"]["implementation_relations"][0],
            {"kind": "operation", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
            and_artifact["artifacts"]["implementation_relations"][1],
            {"kind": "assertion", "case_id": "TC-semantic-fixture", "step_id": "STEP-1", "expectation_id": "EXP-1", "assertion_id": "ASSERT-1", "file_id": "FILE-second", "symbol_id": "SYMBOL-second"},
        ]
        self.assertEqual({("FILE-orders", "SYMBOL-get-order"), ("FILE-second", "SYMBOL-second")}, required_symbol_pairs(and_artifact, canonical_document()))

    def test_manual_and_status_branch_table(self) -> None:
        """Manual disposition and blocker status are mutually exclusive complete branches."""
        manual = manual_document()
        artifact = fixture("tc-to-autotest-manual-only.json")
        artifact["artifacts"]["source"] = source_for(manual)
        self.assertEqual([], validate_automation_artifact(artifact, manual))
        cases = []
        missing = copy.deepcopy(artifact)
        missing["artifacts"]["manual_dispositions"] = []
        cases.append((missing, manual, "AUTOMATION_MISSING_MANUAL_DISPOSITION", "/test_cases/0/steps/0"))
        duplicate = copy.deepcopy(artifact)
        duplicate["artifacts"]["manual_dispositions"].append(copy.deepcopy(duplicate["artifacts"]["manual_dispositions"][0]))
        cases.append((duplicate, manual, "AUTOMATION_DUPLICATE_MANUAL_DISPOSITION", "/artifacts/manual_dispositions/1"))
        copied_reason = copy.deepcopy(artifact)
        copied_reason["artifacts"]["manual_dispositions"][0]["reason"] = "copied"
        cases.append((copied_reason, manual, "SCHEMA_ADDITIONAL_PROPERTIES", "/artifacts/manual_dispositions/0/reason"))
        ready = generated(canonical_document())
        ready["artifacts"]["manual_dispositions"] = [{"case_id": "TC-semantic-fixture", "step_id": "STEP-1"}]
        cases.append((ready, canonical_document(), "AUTOMATION_MANUAL_DISPOSITION_READY", "/artifacts/manual_dispositions/0"))
        blocked = blocked_document()
        blocked_artifact = fixture("tc-to-autotest-blocked.json")
        blocked_artifact["artifacts"]["source"] = source_for(blocked)
        cases.append((blocked_artifact, canonical_document(), "AUTOMATION_BLOCKED_WITHOUT_BLOCKER", "/artifacts/automation_status"))
        generated_diagnostic = generated(canonical_document())
        generated_diagnostic["artifacts"]["diagnostics"] = [{"path": "/x", "code": "X", "message": "x"}]
        cases.append((generated_diagnostic, canonical_document(), "AUTOMATION_GENERATED_DIAGNOSTICS", "/artifacts/diagnostics"))
        for candidate, document, code, path in cases:
            with self.subTest(code=code):
                self.assert_diagnostic(candidate, document, code, path)
        blocked_artifact = fixture("tc-to-autotest-blocked.json")
        blocked_artifact["artifacts"]["source"] = source_for(blocked)
        self.assertEqual([], validate_automation_artifact(blocked_artifact, blocked))
        self.assertEqual(set(), required_symbol_pairs(artifact, manual))
        self.assertEqual(set(), required_symbol_pairs(blocked_artifact, blocked))
        with self.assertRaises(AutomationArtifactError) as raised:
            required_symbol_pairs(missing, manual)
        self.assertIsInstance(raised.exception.diagnostics, tuple)
        with self.assertRaises(TypeError):
            raised.exception.diagnostics[0]["code"] = "changed"
        with self.assertRaises(AttributeError):
            raised.exception.diagnostics = ()

    def test_reviewer_v3_schema_preserves_pair_identity_and_verdicts(self) -> None:
        """Reviewer transport must use pairs, not independent file or global method arrays."""
        review = {
            "schema_version": "3.0.0", "stage": "autotest-reviewer", "warnings": [],
            "artifacts": {"autotest_review": {
                "source": source_for(canonical_document()), "verdict": "ПРИНЯТО",
                "reviewed_symbol_pairs": [{"file_id": "FILE-orders", "symbol_id": "SYMBOL-get-order"}],
                "findings": [], "corrections": [],
            }},
        }
        self.assertEqual([], schema_diagnostics(review, REVIEW_SCHEMA, ROOT))
        cases = (
            (lambda value: value["artifacts"]["autotest_review"].__setitem__("reviewed_method_ids", ["METHOD-old"]), "/artifacts/autotest_review/reviewed_method_ids"),
            (lambda value: value["artifacts"]["autotest_review"]["reviewed_symbol_pairs"].__setitem__(0, {"symbol_id": "SYMBOL-get-order"}), "/artifacts/autotest_review/reviewed_symbol_pairs/0/file_id"),
            (lambda value: value["artifacts"]["autotest_review"].__setitem__("findings", [{"severity": "INFO", "code": "X", "message": "x", "evidence": ["e"], "related_ids": ["FILE-orders"]}]), "/artifacts/autotest_review/findings"),
        )
        for mutate, path in cases:
            with self.subTest(path=path):
                candidate = copy.deepcopy(review)
                mutate(candidate)
                self.assertTrue(any(item["path"] == path for item in schema_diagnostics(candidate, REVIEW_SCHEMA, ROOT)))
        auto_fix = copy.deepcopy(review)
        auto_fix["artifacts"]["autotest_review"].update({"verdict": "AUTO_FIX_APPLIED", "corrections": [{"id": "FIX-one", "related_ids": ["FILE-orders"], "description": "Fix", "evidence": ["e"]}]})
        self.assertEqual([], schema_diagnostics(auto_fix, REVIEW_SCHEMA, ROOT))
        rework = copy.deepcopy(review)
        rework["artifacts"]["autotest_review"].update({"verdict": "ТРЕБУЕТ ДОРАБОТКИ", "findings": [{"severity": "BLOCKING", "code": "X", "message": "x", "evidence": ["e"], "related_ids": ["FILE-orders"]}]})
        self.assertEqual([], schema_diagnostics(rework, REVIEW_SCHEMA, ROOT))


if __name__ == "__main__":
    unittest.main()
