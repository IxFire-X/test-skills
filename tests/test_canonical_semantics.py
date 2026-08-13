from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixture_factory import canonical_document, diagnostic_codes  # noqa: E402
from tools.canonical_document import (  # noqa: E402
    CanonicalDocumentError,
    require_valid_canonical_document,
    validate_canonical_document,
)
from tools.schema_validation import load_json_strict  # noqa: E402


def _named(name: str, representation: str = "string") -> dict[str, str]:
    return {"kind": "named", "name": name, "representation": representation}


def _step_output(step_id: str, output_id: str = "status") -> dict[str, str]:
    return {"kind": "step_output", "step_id": step_id, "output_id": output_id}


def _project_document() -> dict:
    document = canonical_document()
    document["operation_capabilities"] = [{
        "capability_id": "CAP-read", "adapter": "fixture", "action": "read",
        "arguments": [], "results": [{"name": "value", "semantic_type": {"kind": "json", "type": "string"}}],
        "provenance": ["https://example.invalid/capability"],
    }]
    step = document["test_cases"][0]["steps"][0]
    step["operation"] = {"kind": "project_action", "capability_id": "CAP-read"}
    step["inputs"] = []
    step["outputs"] = []
    assertion = step["expectations"][0]["assertions"][0]
    assertion.update({"actual": {"kind": "project_result", "name": "value"}, "operator": "exists"})
    assertion.pop("expected")
    return document


def _second_case(document: dict) -> dict:
    case = copy.deepcopy(document["test_cases"][0])
    case["case_id"] = "TC-semantic-second"
    case["display_order"] = 2
    for step_index, step in enumerate(case["steps"], 1):
        step["step_id"] = f"STEP-second-{step_index}"
        for input_index, binding in enumerate(step["inputs"], 1):
            binding["input_id"] = f"INPUT-second-{step_index}-{input_index}"
        for output_index, output in enumerate(step["outputs"], 1):
            output["output_id"] = f"second_{step_index}_{output_index}"
        for expectation_index, expectation in enumerate(step["expectations"], 1):
            expectation["expectation_id"] = f"EXP-second-{step_index}-{expectation_index}"
            for assertion_index, assertion in enumerate(expectation["assertions"], 1):
                assertion["assertion_id"] = f"ASSERT-second-{step_index}-{expectation_index}-{assertion_index}"
    return case


class CanonicalSemanticTests(unittest.TestCase):
    def assert_code(self, document: dict, code: str) -> None:
        self.assertIn(code, diagnostic_codes(validate_canonical_document(document)))

    def assert_diagnostic(self, document: dict, code: str, path: str) -> None:
        self.assertIn(
            (code, path),
            {(item["code"], item["path"]) for item in validate_canonical_document(document)},
        )

    def test_valid_documents_scale_from_one_to_one_hundred_steps(self) -> None:
        fixtures = ROOT / "tests" / "fixtures" / "canonical" / "valid"
        documents = [
            canonical_document(1),
            load_json_strict(fixtures / "eleven-steps.json"),
            load_json_strict(fixtures / "hundred-steps.json"),
        ]
        self.assertEqual([[], [], []], [validate_canonical_document(document) for document in documents])

    def test_schema_diagnostics_are_preserved_and_facade_error_is_immutable_json(self) -> None:
        document = canonical_document()
        del document["metadata"]
        diagnostics = validate_canonical_document(document)
        self.assertEqual("SCHEMA_REQUIRED", diagnostics[0]["code"])
        with self.assertRaises(CanonicalDocumentError) as raised:
            require_valid_canonical_document(document)
        self.assertIsInstance(raised.exception.diagnostics, tuple)
        with self.assertRaises(TypeError):
            raised.exception.diagnostics[0]["code"] = "changed"
        self.assertEqual(json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")), str(raised.exception))

    def test_identity_requirement_and_all_physical_orders_are_checked(self) -> None:
        mutations = []
        duplicate_requirement = canonical_document()
        duplicate_requirement["requirements"].append(copy.deepcopy(duplicate_requirement["requirements"][0]))
        duplicate_requirement["requirements"][1]["display_order"] = 2
        mutations.append((duplicate_requirement, "SEMANTIC_DUPLICATE_ID"))
        foreign_requirement = canonical_document()
        foreign_requirement["test_cases"][0]["requirement_ids"] = ["REQ-foreign"]
        mutations.append((foreign_requirement, "SEMANTIC_UNKNOWN_REQUIREMENT"))
        requirement_order = canonical_document()
        requirement_order["requirements"].append({"requirement_id": "REQ-second", "display_order": 1, "text": "Second.", "provenance": ["https://example.invalid/second"]})
        mutations.append((requirement_order, "CANONICAL_DISPLAY_ORDER"))
        for document, code in mutations:
            with self.subTest(code=code):
                self.assert_code(document, code)

    def test_data_flow_types_http_targets_and_capability_contract_are_checked(self) -> None:
        missing = canonical_document(2)
        source = missing["test_cases"][0]["steps"][1]["inputs"][0]["source"]
        source.update({"kind": "step_output", "step_id": "STEP-missing", "output_id": "status"})
        source.pop("value")
        self.assert_code(missing, "SEMANTIC_UNKNOWN_STEP_OUTPUT")
        forward = canonical_document(2)
        source = forward["test_cases"][0]["steps"][0]["inputs"][0]["source"]
        source.update({"kind": "step_output", "step_id": "STEP-2", "output_id": "status"})
        source.pop("value")
        self.assert_code(forward, "SEMANTIC_FORWARD_STEP_OUTPUT")
        conflict = canonical_document()
        duplicate = copy.deepcopy(conflict["test_cases"][0]["steps"][0]["inputs"][0])
        duplicate["input_id"] = "INPUT-duplicate"
        duplicate["display_order"] = 2
        conflict["test_cases"][0]["steps"][0]["inputs"].append(duplicate)
        self.assert_code(conflict, "SEMANTIC_DUPLICATE_TARGET")
        overlap = canonical_document()
        binding = overlap["test_cases"][0]["steps"][0]["inputs"][0]
        binding["target"] = {"location": "body", "pointer": "/a", "sensitive": False}
        extra = copy.deepcopy(binding)
        extra["input_id"], extra["display_order"] = "INPUT-body-child", 2
        extra["target"]["pointer"] = "/a/b"
        overlap["test_cases"][0]["steps"][0]["inputs"].append(extra)
        self.assert_code(overlap, "SEMANTIC_BODY_TARGET_OVERLAP")

    def test_types_operators_readiness_blockers_and_secret_handles_are_checked(self) -> None:
        mismatched = canonical_document(2)
        producer = mismatched["test_cases"][0]["steps"][0]
        consumer = mismatched["test_cases"][0]["steps"][1]["inputs"][0]
        consumer["source"] = {"kind": "step_output", "step_id": producer["step_id"], "output_id": "status"}
        consumer["semantic_type"] = {"kind": "json", "type": "string"}
        self.assert_code(mismatched, "SEMANTIC_TYPE_MISMATCH")
        bad_operator = canonical_document()
        assertion = bad_operator["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
        assertion["operator"] = "contains"
        assertion["expected"]["value"] = 200
        self.assert_code(bad_operator, "SEMANTIC_OPERATOR_TYPE")
        manual = canonical_document()
        step = manual["test_cases"][0]["steps"][0]
        step["manual_only"], step["manual_reason"] = True, None
        self.assert_code(manual, "SCHEMA_TYPE")
        blocked = canonical_document()
        step = blocked["test_cases"][0]["steps"][0]
        step["automation_blockers"] = [{"blocker_id": "BLOCK-bad", "code": "UNRESOLVED_INPUT_BINDING", "field_path": "/operation", "reason": "Missing input.", "provenance": ["https://example.invalid/context"]}]
        self.assert_code(blocked, "SEMANTIC_BLOCKER_PATH")

    def test_collision_exact_storage_and_manual_producer_mutations_are_rejected(self) -> None:
        document = canonical_document(2)
        document["test_cases"][0]["steps"][1]["expectations"][0]["assertions"][0]["assertion_id"] = document["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]["assertion_id"]
        self.assert_code(document, "SEMANTIC_DUPLICATE_ID")
        exact = canonical_document(2)
        exact["test_cases"][0]["steps"][1]["inputs"][0]["source"] = {"kind": "step_output", "step_id": "STEP-1", "output_id": "status"}
        exact["test_cases"][0]["steps"][1]["inputs"][0]["semantic_type"] = {"kind": "json", "type": "number"}
        self.assert_code(exact, "SEMANTIC_TYPE_MISMATCH")
        manual = canonical_document(2)
        manual["test_cases"][0]["steps"][0]["manual_only"] = True
        manual["test_cases"][0]["steps"][0]["manual_reason"] = "Physical only."
        manual["test_cases"][0]["steps"][0]["operation"] = None
        manual["test_cases"][0]["steps"][0]["expectations"][0]["assertions"] = []
        consumer = manual["test_cases"][0]["steps"][1]["inputs"][0]
        consumer["source"] = {"kind": "step_output", "step_id": "STEP-1", "output_id": "status"}
        consumer["semantic_type"] = {"kind": "json", "type": "integer"}
        self.assert_code(manual, "SEMANTIC_MANUAL_STEP_OUTPUT")

    def test_collision_first_identity_matrix_names_exact_code_and_rfc6901_path(self) -> None:
        """Ambiguous registry keys are diagnosed and cannot resolve references."""
        capability_collision = canonical_document()
        capability = {
            "capability_id": "CAP-read-item", "adapter": "fixture", "action": "read",
            "arguments": [], "results": [], "provenance": ["https://example.invalid/capability"],
        }
        capability_collision["operation_capabilities"] = [copy.deepcopy(capability), capability]
        step = capability_collision["test_cases"][0]["steps"][0]
        step["operation"] = {"kind": "project_action", "capability_id": "CAP-read-item"}
        step["inputs"] = []
        step["outputs"] = []
        assertion = step["expectations"][0]["assertions"][0]
        assertion["actual"] = {"kind": "project_result", "name": "value"}
        assertion["operator"] = "exists"
        del assertion["expected"]

        requirement_collision = canonical_document()
        duplicate_requirement = copy.deepcopy(requirement_collision["requirements"][0])
        duplicate_requirement["display_order"] = 2
        requirement_collision["requirements"].append(duplicate_requirement)

        matrices = [
            ("duplicate-capability-id", capability_collision, "SEMANTIC_DUPLICATE_ID", "/operation_capabilities/1/capability_id"),
            ("ambiguous-capability-is-unresolved", capability_collision, "SEMANTIC_UNKNOWN_CAPABILITY", "/test_cases/0/steps/0/operation/capability_id"),
            ("duplicate-requirement-id", requirement_collision, "SEMANTIC_DUPLICATE_ID", "/requirements/1/requirement_id"),
            ("ambiguous-requirement-is-unknown", requirement_collision, "SEMANTIC_UNKNOWN_REQUIREMENT", "/test_cases/0/requirement_ids/0"),
        ]
        for name, document, code, path in matrices:
            with self.subTest(name=name):
                self.assert_diagnostic(document, code, path)

    def test_named_order_target_reference_and_readiness_matrix(self) -> None:
        """Every semantic family below asserts one stable code at one RFC 6901 path."""
        cases: list[tuple[str, dict, str, str]] = []
        order = canonical_document()
        order["test_cases"][0]["steps"][0]["inputs"][0]["display_order"] = 2
        cases.append(("input-display-order", order, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/inputs/0/display_order"))
        capability_order = canonical_document()
        capability_order["operation_capabilities"] = [
            {"capability_id": "CAP-z", "adapter": "fixture", "action": "z", "arguments": [], "results": [], "provenance": ["https://example.invalid/z"]},
            {"capability_id": "CAP-a", "adapter": "fixture", "action": "a", "arguments": [], "results": [], "provenance": ["https://example.invalid/a"]},
        ]
        cases.append(("capability-canonical-order", capability_order, "CANONICAL_CAPABILITY_ORDER", "/operation_capabilities"))
        missing = canonical_document(2)
        missing_source = missing["test_cases"][0]["steps"][1]["inputs"][0]["source"]
        missing_source.update({"kind": "step_output", "step_id": "STEP-missing", "output_id": "status"})
        missing_source.pop("value")
        cases.append(("missing-step-output", missing, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/1/inputs/0/source"))
        forward = canonical_document(2)
        forward_source = forward["test_cases"][0]["steps"][0]["inputs"][0]["source"]
        forward_source.update({"kind": "step_output", "step_id": "STEP-2", "output_id": "status"})
        forward_source.pop("value")
        cases.append(("forward-step-output", forward, "SEMANTIC_FORWARD_STEP_OUTPUT", "/test_cases/0/steps/0/inputs/0/source"))
        body = canonical_document()
        first = body["test_cases"][0]["steps"][0]["inputs"][0]
        first["target"] = {"location": "body", "pointer": "/a~1b", "sensitive": False}
        second = copy.deepcopy(first)
        second["input_id"], second["display_order"] = "INPUT-body", 2
        second["target"]["pointer"] = "/a~1b/c"
        body["test_cases"][0]["steps"][0]["inputs"].append(second)
        cases.append(("decoded-body-ancestor", body, "SEMANTIC_BODY_TARGET_OVERLAP", "/test_cases/0/steps/0/inputs/1/target/pointer"))
        http_arg = canonical_document()
        http_arg["test_cases"][0]["steps"][0]["inputs"][0]["target"] = {"location": "arg", "name": "item", "sensitive": False}
        cases.append(("http-arg-target", http_arg, "SEMANTIC_OPERATION_TARGET_KIND", "/test_cases/0/steps/0/inputs/0/target/location"))
        blocked = canonical_document()
        step = blocked["test_cases"][0]["steps"][0]
        step["automation_blockers"] = [{"blocker_id": "BLOCK-a", "code": "UNRESOLVED_INPUT_BINDING", "field_path": "/operation", "reason": "input contract absent", "provenance": ["https://example.invalid/context"]}]
        cases.append(("blocker-code-path", blocked, "SEMANTIC_BLOCKER_PATH", "/test_cases/0/steps/0/automation_blockers/0/field_path"))
        for name, document, code, path in cases:
            with self.subTest(name=name):
                self.assert_diagnostic(document, code, path)

    def test_table_driven_identity_and_every_display_order_collection(self) -> None:
        """Global/local IDs and every physical display_order collection are explicit."""
        document = canonical_document(2)
        step = document["test_cases"][0]["steps"][0]
        expectation = step["expectations"][0]
        duplicate_cases = copy.deepcopy(document["test_cases"])
        duplicate_cases.append(copy.deepcopy(duplicate_cases[0]))
        duplicate_cases[1]["display_order"] = 2
        documents: list[tuple[str, dict, str, str]] = []
        for name, collection, key, path in [
            ("capability", "operation_capabilities", "capability_id", "/operation_capabilities/1/capability_id"),
            ("requirement", "requirements", "requirement_id", "/requirements/1/requirement_id"),
        ]:
            candidate = canonical_document()
            first = copy.deepcopy(candidate[collection][0]) if candidate[collection] else {"capability_id": "CAP-a", "adapter": "x", "action": "x", "arguments": [], "results": [], "provenance": ["https://example.invalid"]}
            if not candidate[collection]:
                candidate[collection].append(copy.deepcopy(first))
            candidate[collection].append(first)
            if collection == "requirements":
                candidate[collection][1]["display_order"] = 2
            documents.append((f"duplicate-{name}", candidate, "SEMANTIC_DUPLICATE_ID", path))
        documents.append(("duplicate-case", {**document, "test_cases": duplicate_cases}, "SEMANTIC_DUPLICATE_ID", "/test_cases/1/case_id"))
        for name, parent, key, path in [
            ("step", document["test_cases"][0]["steps"], "step_id", "/test_cases/0/steps/1/step_id"),
            ("input", step["inputs"], "input_id", "/test_cases/0/steps/0/inputs/1/input_id"),
            ("output", step["outputs"], "output_id", "/test_cases/0/steps/0/outputs/1/output_id"),
            ("expectation", step["expectations"], "expectation_id", "/test_cases/0/steps/0/expectations/1/expectation_id"),
            ("assertion", expectation["assertions"], "assertion_id", "/test_cases/0/steps/0/expectations/0/assertions/1/assertion_id"),
        ]:
            candidate = canonical_document()
            target = candidate["test_cases"][0]["steps"][0]
            target_parent = target["expectations"][0]["assertions"] if name == "assertion" else target[name + "s"] if name in {"input", "output", "expectation"} else candidate["test_cases"][0]["steps"]
            duplicate = copy.deepcopy(target_parent[0])
            if name == "step":
                duplicate["step_id"] = target_parent[0]["step_id"]
                duplicate["display_order"] = 2
            else:
                duplicate["display_order"] = 2
            target_parent.append(duplicate)
            documents.append((f"duplicate-local-{name}", candidate, "SEMANTIC_DUPLICATE_ID", path))
        for name, mutate, path in [
            ("requirement", lambda d: d["requirements"][0].__setitem__("display_order", 2), "/requirements/0/display_order"),
            ("case", lambda d: d["test_cases"][0].__setitem__("display_order", 2), "/test_cases/0/display_order"),
            ("step", lambda d: d["test_cases"][0]["steps"][0].__setitem__("display_order", 2), "/test_cases/0/steps/0/display_order"),
            ("input", lambda d: d["test_cases"][0]["steps"][0]["inputs"][0].__setitem__("display_order", 2), "/test_cases/0/steps/0/inputs/0/display_order"),
            ("output", lambda d: d["test_cases"][0]["steps"][0]["outputs"][0].__setitem__("display_order", 2), "/test_cases/0/steps/0/outputs/0/display_order"),
            ("expectation", lambda d: d["test_cases"][0]["steps"][0]["expectations"][0].__setitem__("display_order", 2), "/test_cases/0/steps/0/expectations/0/display_order"),
            ("assertion", lambda d: d["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].__setitem__("display_order", 2), "/test_cases/0/steps/0/expectations/0/assertions/0/display_order"),
        ]:
            candidate = canonical_document()
            mutate(candidate)
            documents.append((f"display-order-{name}", candidate, "CANONICAL_DISPLAY_ORDER", path))
        for name, candidate, code, path in documents:
            with self.subTest(name=name):
                self.assert_diagnostic(candidate, code, path)

    def test_table_driven_assertion_operator_matrix_and_manual_readiness(self) -> None:
        """Each closed operator pairing and each readiness state has an exact result."""
        valid_operators = {
            "equals": ("http_status", {"kind": "literal", "value": 200}),
            "not_equals": ("http_status", {"kind": "literal", "value": 201}),
            "exists": ("http_status", None),
            "not_exists": ("http_status", None),
            "contains": ("http_header", {"kind": "literal", "value": "ok"}),
            "matches": ("http_header", {"kind": "regex", "dialect": "portable-regex-v1", "pattern": "ok"}),
            "greater_than": ("http_status", {"kind": "literal", "value": 199}),
            "greater_or_equal": ("http_status", {"kind": "literal", "value": 200}),
            "less_than": ("http_status", {"kind": "literal", "value": 201}),
            "less_or_equal": ("http_status", {"kind": "literal", "value": 200}),
            "length_equals": ("http_header", {"kind": "literal", "value": 2}),
            "schema_matches": ("http_body", {"kind": "schema_ref", "uri": "https://example.invalid/schema", "sha256": "sha256:" + "0" * 64, "draft": "2020-12", "provenance": ["https://example.invalid/schema"]}),
        }
        for operator, (actual_kind, expected) in valid_operators.items():
            with self.subTest(operator=operator):
                document = canonical_document()
                assertion = document["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
                assertion["operator"] = operator
                if actual_kind == "http_header":
                    assertion["actual"] = {"kind": "http_header", "name": "x-result"}
                elif actual_kind == "http_body":
                    assertion["actual"] = {"kind": "http_body", "pointer": "", "semantic_type": {"kind": "json", "type": "object"}, "type_provenance": ["https://example.invalid/body"]}
                if expected is None:
                    assertion.pop("expected")
                else:
                    assertion["expected"] = expected
                self.assertEqual([], validate_canonical_document(document))
        invalid = canonical_document()
        assertion = invalid["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
        assertion["operator"] = "contains"
        assertion["expected"] = {"kind": "literal", "value": 200}
        self.assert_diagnostic(invalid, "SEMANTIC_OPERATOR_TYPE", "/test_cases/0/steps/0/expectations/0/assertions/0")
        manual = canonical_document()
        step = manual["test_cases"][0]["steps"][0]
        step.update({"manual_only": True, "manual_reason": "Physical-only verification.", "operation": None, "inputs": [], "outputs": []})
        step["expectations"][0]["assertions"] = []
        self.assertEqual([], validate_canonical_document(manual))
        blocked = canonical_document()
        step = blocked["test_cases"][0]["steps"][0]
        step.update({"operation": None, "inputs": [], "outputs": [], "automation_blockers": [{"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Contract absent.", "provenance": ["https://example.invalid/context"]}]})
        step["expectations"][0]["assertions"] = []
        step["automation_blockers"].insert(0, {"blocker_id": "BLOCK-assertion", "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions", "reason": "Assertion absent.", "provenance": ["https://example.invalid/context"]})
        self.assertEqual([], validate_canonical_document(blocked))

    def test_residual_rows_1_through_50_exact_code_and_path_matrix(self) -> None:
        """Residual checklist rows 1–50: one named mutation per stable diagnostic."""
        rows: list[tuple[str, dict, str, str]] = []

        capability = {
            "capability_id": "CAP-ordered", "adapter": "fixture", "action": "ordered",
            "arguments": [
                {"name": "alpha", "semantic_type": {"kind": "json", "type": "string"}, "required": False},
                {"name": "beta", "semantic_type": {"kind": "json", "type": "string"}, "required": False},
            ],
            "results": [
                {"name": "alpha", "semantic_type": {"kind": "json", "type": "string"}},
                {"name": "beta", "semantic_type": {"kind": "json", "type": "string"}},
            ],
            "provenance": ["https://example.invalid/capability"],
        }
        argument_duplicate = canonical_document()
        argument_duplicate["operation_capabilities"] = [copy.deepcopy(capability)]
        argument_duplicate["operation_capabilities"][0]["arguments"][1]["name"] = "alpha"
        rows.append(("01-duplicate-capability-argument", argument_duplicate, "SEMANTIC_DUPLICATE_ID", "/operation_capabilities/0/arguments/1/name"))
        result_duplicate = canonical_document()
        result_duplicate["operation_capabilities"] = [copy.deepcopy(capability)]
        result_duplicate["operation_capabilities"][0]["results"][1]["name"] = "alpha"
        rows.append(("02-duplicate-capability-result", result_duplicate, "SEMANTIC_DUPLICATE_ID", "/operation_capabilities/0/results/1/name"))
        blocker_duplicate = canonical_document()
        blocked_step = blocker_duplicate["test_cases"][0]["steps"][0]
        blocked_step.update({"operation": None, "inputs": [], "outputs": []})
        blocked_step["expectations"][0]["assertions"] = []
        blocked_step["automation_blockers"] = [
            {"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Operation contract absent.", "provenance": ["https://example.invalid/context"]},
            {"blocker_id": "BLOCK-operation", "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions", "reason": "Assertion contract absent.", "provenance": ["https://example.invalid/context"]},
        ]
        rows.append(("03-duplicate-blocker", blocker_duplicate, "SEMANTIC_DUPLICATE_ID", "/test_cases/0/steps/0/automation_blockers/1/blocker_id"))
        second_case_step = canonical_document()
        foreign_case = _second_case(second_case_step)
        foreign_case["steps"][0]["step_id"] = second_case_step["test_cases"][0]["steps"][0]["step_id"]
        second_case_step["test_cases"].append(foreign_case)
        rows.append(("04-document-global-step", second_case_step, "SEMANTIC_DUPLICATE_ID", "/test_cases/1/steps/0/step_id"))
        duplicate_expectation = canonical_document(2)
        duplicate_expectation["test_cases"][0]["steps"][1]["expectations"][0]["expectation_id"] = duplicate_expectation["test_cases"][0]["steps"][0]["expectations"][0]["expectation_id"]
        rows.append(("05-document-global-expectation", duplicate_expectation, "SEMANTIC_DUPLICATE_ID", "/test_cases/0/steps/1/expectations/0/expectation_id"))
        uncovered_requirement = canonical_document()
        uncovered_requirement["requirements"].append({"requirement_id": "REQ-uncovered", "display_order": 2, "text": "Second requirement.", "provenance": ["https://example.invalid/requirement"]})
        rows.append(("06-uncovered-requirement", uncovered_requirement, "SEMANTIC_UNCOVERED_REQUIREMENT", "/requirements/1/requirement_id"))
        duplicate_link = canonical_document()
        duplicate_link["test_cases"][0]["requirement_ids"].append("REQ-semantic-fixture")
        rows.append(("07-schema-duplicate-requirement-link", duplicate_link, "SCHEMA_UNIQUE_ITEMS", "/test_cases/0/requirement_ids"))

        requirements_reversed = canonical_document()
        requirements_reversed["requirements"].append({"requirement_id": "REQ-second", "display_order": 2, "text": "Second requirement.", "provenance": ["https://example.invalid/requirement"]})
        requirements_reversed["test_cases"][0]["requirement_ids"].append("REQ-second")
        requirements_reversed["requirements"].reverse()
        rows.append(("08-reversed-requirements", requirements_reversed, "CANONICAL_DISPLAY_ORDER", "/requirements/0/display_order"))
        cases_reversed = canonical_document()
        cases_reversed["test_cases"].append(_second_case(cases_reversed))
        cases_reversed["test_cases"].reverse()
        rows.append(("09-reversed-cases", cases_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/display_order"))
        steps_reversed = canonical_document(2)
        steps_reversed["test_cases"][0]["steps"].reverse()
        rows.append(("10-reversed-steps", steps_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/display_order"))
        inputs_reversed = canonical_document()
        input_copy = copy.deepcopy(inputs_reversed["test_cases"][0]["steps"][0]["inputs"][0])
        input_copy.update({"input_id": "INPUT-query", "display_order": 2, "target": {"location": "query", "name": "q", "sensitive": False}})
        inputs_reversed["test_cases"][0]["steps"][0]["inputs"].append(input_copy)
        inputs_reversed["test_cases"][0]["steps"][0]["inputs"].reverse()
        rows.append(("11-reversed-inputs", inputs_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/inputs/0/display_order"))
        outputs_reversed = canonical_document()
        output_copy = copy.deepcopy(outputs_reversed["test_cases"][0]["steps"][0]["outputs"][0])
        output_copy.update({"output_id": "header", "display_order": 2, "source": {"kind": "http_header", "name": "x-result"}, "semantic_type": {"kind": "json", "type": "string"}})
        outputs_reversed["test_cases"][0]["steps"][0]["outputs"].append(output_copy)
        outputs_reversed["test_cases"][0]["steps"][0]["outputs"].reverse()
        rows.append(("12-reversed-outputs", outputs_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/outputs/0/display_order"))
        expectations_reversed = canonical_document()
        expectation_copy = copy.deepcopy(expectations_reversed["test_cases"][0]["steps"][0]["expectations"][0])
        expectation_copy.update({"expectation_id": "EXP-second", "display_order": 2})
        expectation_copy["assertions"][0]["assertion_id"] = "ASSERT-second"
        expectations_reversed["test_cases"][0]["steps"][0]["expectations"].append(expectation_copy)
        expectations_reversed["test_cases"][0]["steps"][0]["expectations"].reverse()
        rows.append(("13-reversed-expectations", expectations_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/expectations/0/display_order"))
        assertions_reversed = canonical_document()
        assertion_copy = copy.deepcopy(assertions_reversed["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0])
        assertion_copy.update({"assertion_id": "ASSERT-second", "display_order": 2})
        assertions_reversed["test_cases"][0]["steps"][0]["expectations"][0]["assertions"].append(assertion_copy)
        assertions_reversed["test_cases"][0]["steps"][0]["expectations"][0]["assertions"].reverse()
        rows.append(("14-reversed-assertions", assertions_reversed, "CANONICAL_DISPLAY_ORDER", "/test_cases/0/steps/0/expectations/0/assertions/0/display_order"))
        arguments_reversed = canonical_document()
        arguments_reversed["operation_capabilities"] = [copy.deepcopy(capability)]
        arguments_reversed["operation_capabilities"][0]["arguments"].reverse()
        rows.append(("15-reversed-capability-arguments", arguments_reversed, "CANONICAL_ARGUMENT_ORDER", "/operation_capabilities/0/arguments"))
        results_reversed = canonical_document()
        results_reversed["operation_capabilities"] = [copy.deepcopy(capability)]
        results_reversed["operation_capabilities"][0]["results"].reverse()
        rows.append(("16-reversed-capability-results", results_reversed, "CANONICAL_RESULT_ORDER", "/operation_capabilities/0/results"))
        blockers_reversed = canonical_document()
        blocked_step = blockers_reversed["test_cases"][0]["steps"][0]
        blocked_step.update({"operation": None, "inputs": [], "outputs": []})
        blocked_step["expectations"][0]["assertions"] = []
        blocked_step["automation_blockers"] = [
            {"blocker_id": "BLOCK-z", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Operation contract absent.", "provenance": ["https://example.invalid/context"]},
            {"blocker_id": "BLOCK-a", "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions", "reason": "Assertion contract absent.", "provenance": ["https://example.invalid/context"]},
        ]
        rows.append(("17-reversed-blockers", blockers_reversed, "CANONICAL_BLOCKER_ORDER", "/test_cases/0/steps/0/automation_blockers"))
        links_reversed = canonical_document()
        links_reversed["requirements"].append({"requirement_id": "REQ-second", "display_order": 2, "text": "Second requirement.", "provenance": ["https://example.invalid/requirement"]})
        links_reversed["test_cases"][0]["requirement_ids"].append("REQ-second")
        links_reversed["test_cases"][0]["requirement_ids"].reverse()
        rows.append(("18-reversed-requirement-links", links_reversed, "CANONICAL_REQUIREMENT_ID_ORDER", "/test_cases/0/requirement_ids"))
        categories_reversed = canonical_document()
        categories_reversed["test_cases"][0]["categories"].reverse()
        rows.append(("19-reversed-categories", categories_reversed, "CANONICAL_CATEGORY_ORDER", "/test_cases/0/categories"))

        prior_unknown = canonical_document(2)
        consumer = prior_unknown["test_cases"][0]["steps"][1]["inputs"][0]
        consumer["source"] = _step_output("STEP-1", "missing")
        consumer["semantic_type"] = {"kind": "json", "type": "integer"}
        rows.append(("21-prior-step-missing-output", prior_unknown, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/1/inputs/0/source"))
        cross_case = canonical_document()
        cross_case["test_cases"].append(_second_case(cross_case))
        cross_case["test_cases"][1]["steps"][0]["inputs"][0].update({"source": _step_output("STEP-1"), "semantic_type": {"kind": "json", "type": "integer"}})
        rows.append(("22-cross-case-step-output", cross_case, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/1/steps/0/inputs/0/source"))
        two_step_cycle = canonical_document(2)
        for index, source in enumerate((_step_output("STEP-2"), _step_output("STEP-1"))):
            two_step_cycle["test_cases"][0]["steps"][index]["inputs"][0].update({"source": source, "semantic_type": {"kind": "json", "type": "integer"}})
        rows.append(("23-two-step-cycle", two_step_cycle, "SEMANTIC_FORWARD_STEP_OUTPUT", "/test_cases/0/steps/0/inputs/0/source"))
        duplicated_step_source = canonical_document(3)
        duplicated_step_source["test_cases"][0]["steps"][1]["step_id"] = "STEP-duplicate"
        duplicated_step_source["test_cases"][0]["steps"][0]["step_id"] = "STEP-duplicate"
        duplicated_step_source["test_cases"][0]["steps"][2]["inputs"][0].update({"source": _step_output("STEP-duplicate"), "semantic_type": {"kind": "json", "type": "integer"}})
        rows.append(("24-ambiguous-step-output", duplicated_step_source, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/2/inputs/0/source"))
        duplicated_output_source = canonical_document(2)
        duplicated_output_source["test_cases"][0]["steps"][0]["outputs"].append(copy.deepcopy(duplicated_output_source["test_cases"][0]["steps"][0]["outputs"][0]))
        duplicated_output_source["test_cases"][0]["steps"][0]["outputs"][1]["display_order"] = 2
        duplicated_output_source["test_cases"][0]["steps"][1]["inputs"][0].update({"source": _step_output("STEP-1"), "semantic_type": {"kind": "json", "type": "integer"}})
        rows.append(("25-ambiguous-producer-output", duplicated_output_source, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/1/inputs/0/source"))
        for number, (label, value, stored) in enumerate([
            ("null", None, "string"), ("boolean", True, "string"), ("integer", 1, "string"),
            ("number", 1.5, "integer"), ("string", "one", "integer"), ("array", [], "object"), ("object", {}, "array"),
        ], 26):
            literal_type = canonical_document()
            literal_type["test_cases"][0]["steps"][0]["inputs"][0]["source"] = {"kind": "literal", "value": value}
            literal_type["test_cases"][0]["steps"][0]["inputs"][0]["semantic_type"] = {"kind": "json", "type": stored}
            rows.append((f"{number:02d}-literal-{label}-storage-mismatch", literal_type, "SEMANTIC_TYPE_MISMATCH", "/test_cases/0/steps/0/inputs/0/semantic_type"))

        for label, descriptor in [("name", _named("Other")), ("representation", _named("Token", "integer"))]:
            named_mismatch = canonical_document(2)
            named_mismatch["test_cases"][0]["steps"][0]["outputs"][0]["semantic_type"] = _named("Token")
            named_mismatch["test_cases"][0]["steps"][1]["inputs"][0].update({"source": _step_output("STEP-1"), "semantic_type": descriptor})
            rows.append((f"{33 if label == 'name' else 34:02d}-named-step-output-{label}-mismatch", named_mismatch, "SEMANTIC_TYPE_MISMATCH", "/test_cases/0/steps/1/inputs/0/semantic_type"))
        actual_step_output = canonical_document(2)
        actual_step_output["test_cases"][0]["steps"][1]["expectations"][0]["assertions"][0]["actual"] = _step_output("STEP-1")
        valid_documents = [("20-documentation-order-significant", canonical_document()), ("35-earlier-step-output-actual", actual_step_output)]
        bad_actual_step_output = copy.deepcopy(actual_step_output)
        bad_actual_step_output["test_cases"][0]["steps"][1]["expectations"][0]["assertions"][0]["actual"] = _step_output("STEP-1", "missing")
        rows.append(("35-invalid-earlier-step-output-actual", bad_actual_step_output, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/1/expectations/0/assertions/0/actual"))
        expected_step_output = canonical_document(2)
        expected_step_output["test_cases"][0]["steps"][1]["expectations"][0]["assertions"][0]["expected"] = _step_output("STEP-1")
        valid_documents.append(("36-earlier-step-output-expected", expected_step_output))
        bad_expected_step_output = copy.deepcopy(expected_step_output)
        bad_expected_step_output["test_cases"][0]["steps"][1]["expectations"][0]["assertions"][0]["expected"] = _step_output("STEP-1", "missing")
        rows.append(("36-invalid-earlier-step-output-expected", bad_expected_step_output, "SEMANTIC_UNKNOWN_STEP_OUTPUT", "/test_cases/0/steps/1/expectations/0/assertions/0/expected"))

        for number, location, first_target, second_target, code, path in [
            (37, "query", {"location": "query", "name": "q", "sensitive": False}, {"location": "query", "name": "q", "sensitive": False}, "SEMANTIC_DUPLICATE_TARGET", "/test_cases/0/steps/0/inputs/1/target"),
            (38, "header", {"location": "header", "name": "x-result", "sensitive": False}, {"location": "header", "name": "x-result", "sensitive": False}, "SEMANTIC_DUPLICATE_TARGET", "/test_cases/0/steps/0/inputs/1/target"),
            (39, "body", {"location": "body", "pointer": "/a~1b", "sensitive": False}, {"location": "body", "pointer": "/a~1b", "sensitive": False}, "SEMANTIC_BODY_TARGET_OVERLAP", "/test_cases/0/steps/0/inputs/1/target/pointer"),
            (40, "body", {"location": "body", "pointer": "", "sensitive": False}, {"location": "body", "pointer": "/a", "sensitive": False}, "SEMANTIC_BODY_TARGET_OVERLAP", "/test_cases/0/steps/0/inputs/1/target/pointer"),
        ]:
            duplicate_target = canonical_document()
            step = duplicate_target["test_cases"][0]["steps"][0]
            step["operation"]["path"] = "/items"
            first = step["inputs"][0]
            first.update({"target": first_target, "source": {"kind": "literal", "value": "value"}, "semantic_type": {"kind": "json", "type": "string"}})
            second = copy.deepcopy(first)
            second.update({"input_id": f"INPUT-{location}-{number}", "display_order": 2, "target": second_target})
            step["inputs"].append(second)
            rows.append((f"{number:02d}-{location}-target", duplicate_target, code, path))
        nonoverlapping_body = canonical_document()
        step = nonoverlapping_body["test_cases"][0]["steps"][0]
        step["operation"]["path"] = "/items"
        first = step["inputs"][0]
        first.update({"target": {"location": "body", "pointer": "/a", "sensitive": False}, "source": {"kind": "literal", "value": "value"}, "semantic_type": {"kind": "json", "type": "string"}})
        second = copy.deepcopy(first)
        second.update({"input_id": "INPUT-body-ab", "display_order": 2, "target": {"location": "body", "pointer": "/ab", "sensitive": False}})
        step["inputs"].append(second)
        valid_documents.append(("41-nonoverlapping-body-pointers", nonoverlapping_body))

        project_path_target = _project_document()
        path_binding = copy.deepcopy(canonical_document()["test_cases"][0]["steps"][0]["inputs"][0])
        project_path_target["test_cases"][0]["steps"][0]["inputs"] = [path_binding]
        rows.append(("42-project-action-path-target", project_path_target, "SEMANTIC_OPERATION_TARGET_KIND", "/test_cases/0/steps/0/inputs/0/target/location"))
        http_project_output = canonical_document()
        http_project_output["test_cases"][0]["steps"][0]["outputs"][0]["source"] = {"kind": "project_result", "name": "value"}
        rows.append(("43-http-project-result-output", http_project_output, "SEMANTIC_OPERATION_OUTPUT_KIND", "/test_cases/0/steps/0/outputs/0/source/kind"))
        for number, source, descriptor in [
            (44, {"kind": "http_status"}, {"kind": "json", "type": "integer"}),
            (45, {"kind": "http_header", "name": "x-result"}, {"kind": "json", "type": "string"}),
            (46, {"kind": "http_body", "pointer": ""}, {"kind": "json", "type": "object"}),
        ]:
            project_output = _project_document()
            output = copy.deepcopy(canonical_document()["test_cases"][0]["steps"][0]["outputs"][0])
            output.update({"source": source, "semantic_type": descriptor})
            if source["kind"] == "http_body":
                output["type_provenance"] = ["https://example.invalid/body"]
            project_output["test_cases"][0]["steps"][0]["outputs"] = [output]
            rows.append((f"{number:02d}-project-http-output", project_output, "SEMANTIC_OPERATION_OUTPUT_KIND", "/test_cases/0/steps/0/outputs/0/source/kind"))
        http_project_assertion = canonical_document()
        assertion = http_project_assertion["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
        assertion.update({"actual": {"kind": "project_result", "name": "value"}, "operator": "exists"})
        assertion.pop("expected")
        rows.append(("47-http-project-result-assertion", http_project_assertion, "SEMANTIC_OPERATION_ASSERTION_KIND", "/test_cases/0/steps/0/expectations/0/assertions/0/actual/kind"))
        for number, actual, expected in [
            (48, {"kind": "http_status"}, {"kind": "literal", "value": 200}),
            (49, {"kind": "http_header", "name": "x-result"}, {"kind": "literal", "value": "ok"}),
            (50, {"kind": "http_body", "pointer": "", "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["https://example.invalid/body"]}, {"kind": "literal", "value": "ok"}),
        ]:
            project_assertion = _project_document()
            project_assertion["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].update({"actual": actual, "operator": "equals", "expected": expected})
            rows.append((f"{number:02d}-project-http-assertion", project_assertion, "SEMANTIC_OPERATION_ASSERTION_KIND", "/test_cases/0/steps/0/expectations/0/assertions/0/actual/kind"))

        for name, document, code, path in rows:
            with self.subTest(row=name):
                self.assert_diagnostic(document, code, path)
        for name, document in valid_documents:
            with self.subTest(row=name):
                self.assertEqual([], validate_canonical_document(document))
        documentation_order = canonical_document()
        documentation_order["metadata"]["documentation"].append("https://example.invalid/second")
        reversed_documentation = copy.deepcopy(documentation_order)
        reversed_documentation["metadata"]["documentation"].reverse()
        from tools.canonical_document import canonical_bytes
        self.assertEqual([], validate_canonical_document(documentation_order))
        self.assertEqual([], validate_canonical_document(reversed_documentation))
        self.assertNotEqual(canonical_bytes(documentation_order), canonical_bytes(reversed_documentation))

    def test_residual_rows_51_through_76_capability_and_operator_matrix(self) -> None:
        """Residual checklist rows 51–76: capability ownership and operator types."""
        assertion_path = "/test_cases/0/steps/0/expectations/0/assertions/0"

        def capability_document() -> dict:
            document = _project_document()
            document["operation_capabilities"][0].update({
                "arguments": [
                    {"name": "optional_text", "semantic_type": {"kind": "json", "type": "string"}, "required": False},
                    {"name": "required_count", "semantic_type": {"kind": "json", "type": "number"}, "required": True},
                ],
                "results": [
                    {"name": "count", "semantic_type": {"kind": "json", "type": "integer"}},
                    {"name": "value", "semantic_type": {"kind": "json", "type": "string"}},
                ],
            })
            return document

        def argument_binding(name: str, value: object, semantic_type: dict | None = None) -> dict:
            return {
                "input_id": f"INPUT-{name}", "display_order": 1,
                "target": {"location": "arg", "name": name, "sensitive": False},
                "source": {"kind": "literal", "value": value},
                "semantic_type": semantic_type or {"kind": "json", "type": "string"},
            }

        def expected_source(kind: str, semantic_type: dict) -> dict:
            source: dict = {"kind": kind, "semantic_type": semantic_type, "type_provenance": ["https://example.invalid/expected"]}
            if kind == "secret_handle":
                source.update({"handle": "vault://example/secret", "safe_label": "fixture-secret"})
            else:
                source["name"] = f"{kind.upper()}_VALUE"
            return source

        rows: list[tuple[str, dict, str, str]] = []
        valid_rows: list[tuple[str, dict]] = []

        valid_rows.append(("51-valid-project-action-fixture", _project_document()))

        unknown_capability = _project_document()
        unknown_capability["test_cases"][0]["steps"][0]["operation"]["capability_id"] = "CAP-missing"
        rows.append(("52-unknown-capability", unknown_capability, "SEMANTIC_UNKNOWN_CAPABILITY", "/test_cases/0/steps/0/operation/capability_id"))

        duplicate_action = _project_document()
        duplicate = copy.deepcopy(duplicate_action["operation_capabilities"][0])
        duplicate["capability_id"] = "CAP-read-duplicate"
        duplicate_action["operation_capabilities"].append(duplicate)
        rows.append(("53-duplicate-adapter-action", duplicate_action, "SEMANTIC_DUPLICATE_CAPABILITY_ACTION", "/operation_capabilities/1"))

        undeclared_argument = capability_document()
        undeclared_argument["test_cases"][0]["steps"][0]["inputs"] = [argument_binding("undeclared", "value")]
        rows.append(("54-undeclared-capability-argument", undeclared_argument, "SEMANTIC_UNDECLARED_CAPABILITY_ARGUMENT", "/test_cases/0/steps/0/inputs/0/target/name"))

        missing_required = capability_document()
        missing_required["test_cases"][0]["steps"][0]["inputs"] = []
        rows.append(("55-missing-required-capability-argument", missing_required, "SEMANTIC_REQUIRED_CAPABILITY_ARGUMENT", "/test_cases/0/steps/0/inputs"))

        duplicate_binding = capability_document()
        first_binding = argument_binding("required_count", 1, {"kind": "json", "type": "integer"})
        second_binding = copy.deepcopy(first_binding)
        second_binding.update({"input_id": "INPUT-required-count-duplicate", "display_order": 2})
        duplicate_binding["test_cases"][0]["steps"][0]["inputs"] = [first_binding, second_binding]
        rows.append(("56-duplicate-effective-capability-argument", duplicate_binding, "SEMANTIC_DUPLICATE_CAPABILITY_ARGUMENT", "/test_cases/0/steps/0/inputs"))

        argument_type_mismatch = capability_document()
        argument_type_mismatch["test_cases"][0]["steps"][0]["inputs"] = [argument_binding("required_count", "one")]
        rows.append(("57-capability-argument-type-mismatch", argument_type_mismatch, "SEMANTIC_CAPABILITY_TYPE_MISMATCH", "/test_cases/0/steps/0/inputs/0/semantic_type"))

        optional_omitted = capability_document()
        optional_omitted["test_cases"][0]["steps"][0]["inputs"] = [argument_binding("required_count", 1, {"kind": "json", "type": "integer"})]
        valid_rows.append(("58-optional-capability-argument-omitted", optional_omitted))

        undeclared_output = capability_document()
        undeclared_output["test_cases"][0]["steps"][0]["outputs"] = [{
            "output_id": "unknown", "display_order": 1,
            "source": {"kind": "project_result", "name": "missing"},
            "semantic_type": {"kind": "json", "type": "string"},
        }]
        rows.append(("59-undeclared-capability-result-output", undeclared_output, "SEMANTIC_UNDECLARED_CAPABILITY_RESULT", "/test_cases/0/steps/0/outputs/0/source/name"))

        output_type_mismatch = capability_document()
        output_type_mismatch["test_cases"][0]["steps"][0]["outputs"] = [{
            "output_id": "count", "display_order": 1,
            "source": {"kind": "project_result", "name": "count"},
            "semantic_type": {"kind": "json", "type": "number"},
        }]
        rows.append(("60-capability-result-output-type-mismatch", output_type_mismatch, "SEMANTIC_CAPABILITY_TYPE_MISMATCH", "/test_cases/0/steps/0/outputs/0/semantic_type"))

        undeclared_actual = capability_document()
        undeclared_actual["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].update({"actual": {"kind": "project_result", "name": "missing"}, "operator": "exists"})
        rows.append(("61-undeclared-capability-result-assertion", undeclared_actual, "SEMANTIC_UNDECLARED_CAPABILITY_RESULT", f"{assertion_path}/actual/name"))

        for row_number, kind in ((62, "fixture"), (63, "environment"), (64, "secret_handle")):
            valid_expected = _project_document()
            assertion = valid_expected["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
            assertion.update({"operator": "equals", "expected": expected_source(kind, {"kind": "json", "type": "string"})})
            valid_rows.append((f"{row_number}-valid-{kind}-expected-source", valid_expected))
            incompatible_expected = copy.deepcopy(valid_expected)
            incompatible_expected["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]["expected"]["semantic_type"] = {"kind": "json", "type": "integer"}
            rows.append((f"{row_number}-incompatible-{kind}-expected-source", incompatible_expected, "SEMANTIC_OPERATOR_TYPE", assertion_path))

        for row_number, operator, actual, expected in [
            (65, "equals", {"kind": "http_status"}, {"kind": "literal", "value": "200"}),
            (66, "not_equals", {"kind": "http_status"}, {"kind": "literal", "value": "200"}),
            (67, "contains", {"kind": "http_body", "pointer": "", "semantic_type": {"kind": "json", "type": "array"}, "type_provenance": ["https://example.invalid/body"]}, {"kind": "literal", "value": "value"}),
            (68, "matches", {"kind": "http_header", "name": "x-result"}, {"kind": "literal", "value": "value"}),
            (69, "greater_than", {"kind": "http_header", "name": "x-result"}, {"kind": "literal", "value": 1}),
            (70, "greater_or_equal", {"kind": "http_status"}, {"kind": "literal", "value": "1"}),
            (71, "less_than", {"kind": "http_header", "name": "x-result"}, {"kind": "literal", "value": 1}),
            (72, "less_or_equal", {"kind": "http_status"}, {"kind": "literal", "value": "1"}),
            (73, "length_equals", {"kind": "http_header", "name": "x-result"}, {"kind": "literal", "value": -1}),
            (74, "schema_matches", {"kind": "http_header", "name": "x-result"}, {"kind": "schema_ref", "uri": "https://example.invalid/schema", "sha256": "sha256:" + "0" * 64, "draft": "2020-12", "provenance": ["https://example.invalid/schema"]}),
        ]:
            document = canonical_document()
            document["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].update({"operator": operator, "actual": actual, "expected": expected})
            rows.append((f"{row_number}-{operator}-invalid-operands", document, "SEMANTIC_OPERATOR_TYPE", assertion_path))

        for row_number, operator in ((75, "exists"), (76, "not_exists")):
            document = canonical_document()
            document["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].update({"operator": operator, "expected": {"kind": "literal", "value": 200}})
            rows.append((f"{row_number}-{operator}-forbids-expected", document, "SCHEMA_NOT", assertion_path))

        for name, document, code, path in rows:
            with self.subTest(row=name):
                self.assert_diagnostic(document, code, path)
        for name, document in valid_rows:
            with self.subTest(row=name):
                self.assertEqual([], validate_canonical_document(document))

    def test_residual_i1_i2_readiness_edges(self) -> None:
        """I1 rejects false blockers with empty technical arrays; I2 permits manual consumers."""
        spurious = canonical_document()
        step = spurious["test_cases"][0]["steps"][0]
        step["inputs"] = []
        step["outputs"] = []
        step["operation"]["path"] = "/items"
        step["automation_blockers"] = [{"blocker_id": "BLOCK-input", "code": "UNRESOLVED_INPUT_BINDING", "field_path": "/inputs", "reason": "Incorrect blocker.", "provenance": ["https://example.invalid/context"]}]
        self.assert_diagnostic(spurious, "SEMANTIC_SPURIOUS_BLOCKER", "/test_cases/0/steps/0/automation_blockers")

        manual_consumer = canonical_document(2)
        producer = manual_consumer["test_cases"][0]["steps"][0]
        producer.update({"manual_only": True, "manual_reason": "Physical verification.", "operation": None, "inputs": []})
        producer["expectations"][0]["assertions"] = []
        consumer = manual_consumer["test_cases"][0]["steps"][1]
        consumer.update({"manual_only": True, "manual_reason": "Physical verification.", "operation": None, "outputs": []})
        consumer["inputs"][0].update({"source": _step_output("STEP-1"), "semantic_type": {"kind": "json", "type": "integer"}})
        consumer["expectations"][0]["assertions"] = []
        self.assertEqual([], validate_canonical_document(manual_consumer))

    def test_residual_rows_77_through_106_blockers_schema_preservation_and_secrets(self) -> None:
        """Residual checklist rows 77–106 preserve readiness, schema, and secret boundaries."""
        step_path = "/test_cases/0/steps/0"

        def blocker(blocker_id: str, code: str, field_path: str) -> dict:
            return {
                "blocker_id": blocker_id,
                "code": code,
                "field_path": field_path,
                "reason": "Synthetic contract context is incomplete.",
                "provenance": ["https://example.invalid/context"],
            }

        def blocked_omitting(field_path: str, code: str) -> dict:
            document = canonical_document()
            step = document["test_cases"][0]["steps"][0]
            step["automation_blockers"] = [blocker("BLOCK-omitted", code, field_path)]
            if field_path == "/operation":
                step["operation"] = None
            elif field_path == "/inputs":
                step["inputs"] = []
            elif field_path == "/outputs":
                step["outputs"] = []
            else:
                step["expectations"][0]["assertions"] = []
            return document

        rows: list[tuple[str, dict, str, str]] = []
        valid_rows: list[tuple[str, dict]] = []

        ready_null_operation = canonical_document()
        ready_null_operation["test_cases"][0]["steps"][0]["operation"] = None
        rows.append(("77-ready-null-operation", ready_null_operation, "SCHEMA_NOT", f"{step_path}/operation"))

        missing_operation_blocker = canonical_document()
        missing_operation_step = missing_operation_blocker["test_cases"][0]["steps"][0]
        missing_operation_step["operation"] = None
        missing_operation_step["automation_blockers"] = [blocker("BLOCK-assertion", "UNRESOLVED_ASSERTION", "/expectations/0/assertions")]
        rows.append(("78-blocked-null-operation-needs-operation-blocker", missing_operation_blocker, "SEMANTIC_MISSING_OPERATION_BLOCKER", f"{step_path}/operation"))

        ready_empty_assertions = canonical_document()
        ready_empty_assertions["test_cases"][0]["steps"][0]["expectations"][0]["assertions"] = []
        rows.append(("79-ready-expectation-needs-assertion", ready_empty_assertions, "SCHEMA_MIN_ITEMS", f"{step_path}/expectations/0/assertions"))

        missing_second_assertion_blocker = canonical_document()
        missing_second_step = missing_second_assertion_blocker["test_cases"][0]["steps"][0]
        second_expectation = copy.deepcopy(missing_second_step["expectations"][0])
        second_expectation.update({"expectation_id": "EXP-2", "display_order": 2, "assertions": []})
        missing_second_step["expectations"][0]["assertions"] = []
        missing_second_step["expectations"].append(second_expectation)
        missing_second_step["automation_blockers"] = [blocker("BLOCK-first", "UNRESOLVED_ASSERTION", "/expectations/0/assertions")]
        rows.append(("80-each-empty-blocked-expectation-needs-own-blocker", missing_second_assertion_blocker, "SEMANTIC_MISSING_ASSERTION_BLOCKER", f"{step_path}/expectations/1/assertions"))

        manual_with_blocker = canonical_document()
        manual_step = manual_with_blocker["test_cases"][0]["steps"][0]
        manual_step.update({"manual_only": True, "manual_reason": "Synthetic physical confirmation.", "operation": None, "inputs": [], "outputs": []})
        manual_step["expectations"][0]["assertions"] = []
        manual_step["automation_blockers"] = [blocker("BLOCK-manual", "UNRESOLVED_OPERATION", "/operation")]
        rows.append(("81-manual-only-forbids-blocker", manual_with_blocker, "SCHEMA_MAX_ITEMS", f"{step_path}/automation_blockers"))

        for number, code, field_path in (
            (82, "UNSUPPORTED_AUTOMATION_CAPABILITY", "/operation"),
            (83, "UNRESOLVED_INPUT_BINDING", "/inputs"),
            (84, "UNRESOLVED_OUTPUT_BINDING", "/outputs"),
            (85, "UNSUPPORTED_ASSERTION_DIALECT", "/expectations/0/assertions"),
            (86, "UNCONFIRMED_TYPE", "/inputs"),
            (87, "UNCONFIRMED_TYPE", "/outputs"),
            (88, "UNCONFIRMED_TYPE", "/expectations/0/assertions"),
        ):
            valid_rows.append((f"{number}-{code.lower()}-{field_path.rsplit('/', 1)[-1]}", blocked_omitting(field_path, code)))

        noncanonical_index = blocked_omitting("/expectations/0/assertions", "UNRESOLVED_ASSERTION")
        noncanonical_index["test_cases"][0]["steps"][0]["automation_blockers"][0]["field_path"] = "/expectations/01/assertions"
        rows.append(("89-blocker-expectation-index-is-canonical-decimal", noncanonical_index, "SCHEMA_PATTERN", f"{step_path}/automation_blockers/0/field_path"))

        nonexistent_index = blocked_omitting("/expectations/0/assertions", "UNRESOLVED_ASSERTION")
        nonexistent_index["test_cases"][0]["steps"][0]["automation_blockers"][0]["field_path"] = "/expectations/1/assertions"
        rows.append(("90-blocker-expectation-index-must-exist", nonexistent_index, "SEMANTIC_BLOCKER_PATH", f"{step_path}/automation_blockers/0/field_path"))

        empty_reason = blocked_omitting("/operation", "UNRESOLVED_OPERATION")
        empty_reason["test_cases"][0]["steps"][0]["automation_blockers"][0]["reason"] = ""
        rows.append(("91-blocker-reason-is-nonempty", empty_reason, "SCHEMA_MIN_LENGTH", f"{step_path}/automation_blockers/0/reason"))

        empty_provenance = blocked_omitting("/operation", "UNRESOLVED_OPERATION")
        empty_provenance["test_cases"][0]["steps"][0]["automation_blockers"][0]["provenance"] = []
        rows.append(("92-blocker-provenance-is-nonempty", empty_provenance, "SCHEMA_MIN_ITEMS", f"{step_path}/automation_blockers/0/provenance"))

        i1_spurious = canonical_document()
        i1_step = i1_spurious["test_cases"][0]["steps"][0]
        i1_step["inputs"] = []
        i1_step["outputs"] = []
        i1_step["operation"]["path"] = "/items"
        i1_step["automation_blockers"] = [blocker("BLOCK-i1", "UNRESOLVED_INPUT_BINDING", "/inputs")]
        rows.append(("93-i1-false-blocker-with-legitimate-empty-arrays", i1_spurious, "SEMANTIC_SPURIOUS_BLOCKER", f"{step_path}/automation_blockers"))

        partial_documents: list[tuple[str, dict, str, str]] = []
        partial_operation = canonical_document()
        partial_operation["test_cases"][0]["steps"][0]["operation"].pop("path")
        partial_documents.append(("94-http-operation-missing-path", partial_operation, "SCHEMA_ONE_OF", f"{step_path}/operation"))

        partial_input = canonical_document()
        partial_input["test_cases"][0]["steps"][0]["inputs"][0].pop("source")
        partial_documents.append(("95-input-missing-source", partial_input, "SCHEMA_REQUIRED", f"{step_path}/inputs/0/source"))

        partial_output = canonical_document()
        partial_output["test_cases"][0]["steps"][0]["outputs"][0].pop("source")
        partial_documents.append(("96-output-missing-source", partial_output, "SCHEMA_REQUIRED", f"{step_path}/outputs/0/source"))

        partial_assertion = canonical_document()
        partial_assertion["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0].pop("actual")
        partial_documents.append(("97-assertion-missing-actual", partial_assertion, "SCHEMA_REQUIRED", f"{step_path}/expectations/0/assertions/0/actual"))

        partial_body_output = canonical_document()
        body_output = partial_body_output["test_cases"][0]["steps"][0]["outputs"][0]
        body_output.update({"source": {"kind": "http_body", "pointer": "/item"}, "type_provenance": ["https://example.invalid/type"]})
        body_output.pop("type_provenance")
        partial_documents.append(("98-http-body-output-needs-type-provenance", partial_body_output, "SCHEMA_REQUIRED", f"{step_path}/outputs/0/type_provenance"))

        partial_body_actual = canonical_document()
        body_actual = partial_body_actual["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]["actual"]
        body_actual.update({"kind": "http_body", "pointer": "/item", "semantic_type": {"kind": "json", "type": "object"}})
        partial_documents.append(("99-http-body-actual-needs-type-provenance", partial_body_actual, "SCHEMA_ONE_OF", f"{step_path}/expectations/0/assertions/0/actual"))
        rows.extend(partial_documents)

        secret_input = canonical_document()
        secret_binding = secret_input["test_cases"][0]["steps"][0]["inputs"][0]
        secret_binding.update({
            "source": {"kind": "secret_handle", "handle": "opaque://synthetic/input-101", "safe_label": "synthetic-input",},
            "target": {"location": "path", "name": "item_id", "sensitive": True},
            "type_provenance": ["https://example.invalid/type"],
        })
        valid_rows.append(("101-secret-handle-input-is-safe-and-typed", secret_input))

        literal_sensitive = canonical_document()
        literal_sensitive["test_cases"][0]["steps"][0]["inputs"][0]["target"]["sensitive"] = True
        rows.append(("102-literal-input-cannot-be-sensitive", literal_sensitive, "SCHEMA_CONST", f"{step_path}/inputs/0/target/sensitive"))

        secret_not_sensitive = copy.deepcopy(secret_input)
        secret_not_sensitive["test_cases"][0]["steps"][0]["inputs"][0]["target"]["sensitive"] = False
        rows.append(("103-secret-handle-input-must-be-sensitive", secret_not_sensitive, "SCHEMA_CONST", f"{step_path}/inputs/0/target/sensitive"))

        missing_safe_label = copy.deepcopy(secret_input)
        missing_safe_label["test_cases"][0]["steps"][0]["inputs"][0]["source"].pop("safe_label")
        rows.append(("104-secret-handle-needs-safe-label", missing_safe_label, "SCHEMA_ONE_OF", f"{step_path}/inputs/0/source"))

        missing_secret_provenance = copy.deepcopy(secret_input)
        missing_secret_provenance["test_cases"][0]["steps"][0]["inputs"][0].pop("type_provenance")
        rows.append(("105-secret-input-needs-type-provenance", missing_secret_provenance, "SCHEMA_REQUIRED", f"{step_path}/inputs/0/type_provenance"))

        secret_expected = canonical_document()
        secret_assertion = secret_expected["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
        secret_assertion["expected"] = {
            "kind": "secret_handle", "handle": "opaque://synthetic/expected-106", "safe_label": "synthetic-expected",
            "semantic_type": {"kind": "json", "type": "integer"}, "type_provenance": ["https://example.invalid/type"],
        }
        valid_rows.append(("106-secret-handle-expected-is-safe-and-typed", secret_expected))

        missing_expected_provenance = copy.deepcopy(secret_expected)
        missing_expected_provenance["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]["expected"].pop("type_provenance")
        rows.append(("106-secret-expected-needs-type-provenance", missing_expected_provenance, "SCHEMA_ONE_OF", f"{step_path}/expectations/0/assertions/0/expected"))

        for name, document, code, path in rows:
            with self.subTest(row=name):
                self.assert_diagnostic(document, code, path)
        for name, document in valid_rows:
            with self.subTest(row=name):
                self.assertEqual([], validate_canonical_document(document))
        for name, document, _, _ in partial_documents:
            with self.subTest(row=f"100-schema-only-{name}"):
                diagnostics = validate_canonical_document(document)
                self.assertTrue(diagnostics)
                self.assertTrue(all(item["code"].startswith("SCHEMA_") for item in diagnostics), diagnostics)

    def test_fix_round_2_inputs_blocker_does_not_hide_extra_http_path_target(self) -> None:
        """An input blocker suppresses HTTP binding checks only for an actual missing binding."""
        document = canonical_document()
        step = document["test_cases"][0]["steps"][0]
        step["automation_blockers"] = [{
            "blocker_id": "BLOCK-inputs",
            "code": "UNRESOLVED_INPUT_BINDING",
            "field_path": "/inputs",
            "reason": "One synthetic binding is unresolved.",
            "provenance": ["https://example.invalid/context"],
        }]
        extra_path_input = copy.deepcopy(step["inputs"][0])
        extra_path_input.update({
            "input_id": "INPUT-extra-path",
            "display_order": 2,
            "target": {"location": "path", "name": "unrelated", "sensitive": False},
            "source": {"kind": "literal", "value": "extra"},
        })
        step["inputs"].append(extra_path_input)

        self.assert_diagnostic(document, "SEMANTIC_HTTP_PATH_BINDINGS", "/test_cases/0/steps/0/operation/path")
        self.assert_diagnostic(document, "SEMANTIC_SPURIOUS_BLOCKER", "/test_cases/0/steps/0/automation_blockers")


if __name__ == "__main__":
    unittest.main()
