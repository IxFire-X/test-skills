from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema.exceptions import _WrappedReferencingError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "canonical"
SCHEMA = ROOT / "schemas" / "canonical-test-document.schema.json"
sys.path.insert(0, str(ROOT))

from tools.schema_validation import (  # noqa: E402
    SchemaRegistryError,
    StrictJsonError,
    classify_version,
    load_json_strict,
    loads_json_strict,
    schema_diagnostics,
    validator_for,
)
from tools.validate_artifact import validate  # noqa: E402


class CanonicalSchemaTests(unittest.TestCase):
    def test_strict_loader_rejects_bom_and_trailing_data(self):
        for text in ('\ufeff{"value": 1}', '{"value": 1} trailing'):
            with self.subTest(text=text), self.assertRaises(StrictJsonError):
                loads_json_strict(text)

    def test_strict_loader_accepts_json_whitespace_before_the_value(self):
        self.assertEqual({"value": 1}, loads_json_strict(" \t\r\n{\"value\": 1}"))

    def test_strict_file_loader_rejects_utf8_bom(self):
        path = FIXTURES / "invalid" / "utf8-bom.json"
        with self.assertRaises(StrictJsonError):
            load_json_strict(path)

    def test_registry_rejects_schemas_outside_repository_schemas(self):
        with self.assertRaisesRegex(SchemaRegistryError, "repository-local schema"):
            validator_for(Path("../README.md"), ROOT)

    def test_common_schema_accepts_all_three_valid_variants(self):
        for name in ("minimal-manual.json", "full-http.json", "project-action.json"):
            document = load_json_strict(FIXTURES / "valid" / name)
            self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT), name)

    def test_duplicate_json_key_is_rejected(self):
        with self.assertRaisesRegex(StrictJsonError, "duplicate object key: document_id"):
            loads_json_strict('{"document_id":"a","document_id":"b"}')

    def test_nan_and_infinity_are_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(StrictJsonError):
                loads_json_strict('{"value":' + token + '}')

    def test_legacy_v21_gets_breaking_change_diagnostic(self):
        legacy = load_json_strict(FIXTURES / "invalid" / "legacy-v2.1.json")
        self.assertEqual("V2_1_BREAKING_CHANGE", classify_version(legacy)["code"])

    def test_schema_diagnostics_have_deterministic_rfc6901_shape(self):
        document = load_json_strict(FIXTURES / "valid" / "minimal-manual.json")
        document["unexpected"] = True
        self.assertEqual(
            [{
                "path": "/unexpected",
                "code": "SCHEMA_ADDITIONAL_PROPERTIES",
                "message": "Additional properties are not allowed ('unexpected' was unexpected)",
            }],
            schema_diagnostics(document, SCHEMA, ROOT),
        )

    def test_required_diagnostics_identify_each_missing_property(self):
        document = load_json_strict(FIXTURES / "valid" / "minimal-manual.json")
        del document["metadata"]
        del document["requirements"]
        diagnostics = schema_diagnostics(document, SCHEMA, ROOT)
        self.assertEqual(
            ["/metadata", "/requirements"],
            [item["path"] for item in diagnostics],
        )
        self.assertEqual(
            ["'metadata' is a required property", "'requirements' is a required property"],
            [item["message"] for item in diagnostics],
        )

    def test_custom_fields_reject_null_scalars_and_null_array_members(self):
        for value in (None, ["safe", None]):
            with self.subTest(value=value):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["test_cases"][0]["management"]["custom_fields"] = {"Example": value}
                self.assertNotEqual([], schema_diagnostics(document, SCHEMA, ROOT))

    def test_custom_fields_reject_empty_present_values(self):
        for value in ("", " \t", []):
            with self.subTest(value=value):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["test_cases"][0]["management"]["custom_fields"] = {"Example": value}
                diagnostics = schema_diagnostics(document, SCHEMA, ROOT)
                self.assertEqual("/test_cases/0/management/custom_fields/Example", diagnostics[0]["path"])
                self.assertEqual("SCHEMA_ONE_OF", diagnostics[0]["code"])

    def test_custom_fields_accept_finite_present_values(self):
        for value in ("present", 0, -1.25, True, ["first", 0, False]):
            with self.subTest(value=value):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["test_cases"][0]["management"]["custom_fields"] = {"Example": value}
                self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT))

    def test_programmatic_nonfinite_numbers_have_exact_canonical_diagnostics(self):
        mutations = [
            ("custom scalar", ["test_cases", 0, "management", "custom_fields"], {"Example": float("nan")}, "/test_cases/0/management/custom_fields/Example"),
            ("custom array member", ["test_cases", 0, "management", "custom_fields"], {"Example": ["safe", float("inf")]}, "/test_cases/0/management/custom_fields/Example/1"),
            ("literal input", ["test_cases", 0, "steps", 0, "inputs", 0, "source"], {"kind": "literal", "value": float("-inf")}, "/test_cases/0/steps/0/inputs/0/source/value"),
        ]
        for name, path, replacement, expected_path in mutations:
            with self.subTest(name=name):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                target = document
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = replacement
                self.assertEqual(
                    [{
                        "path": expected_path,
                        "code": "CANONICAL_NONFINITE_NUMBER",
                        "message": "numbers must be finite in canonical documents",
                    }],
                    schema_diagnostics(document, SCHEMA, ROOT),
                )

    def test_canonical_revision_requires_an_exact_python_int(self):
        for revision in (1.0, True):
            with self.subTest(revision=revision):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["revision"] = revision
                self.assertEqual(
                    [{
                        "path": "/revision",
                        "code": "CANONICAL_REVISION_TYPE",
                        "message": "revision must be an exact Python int",
                    }],
                    schema_diagnostics(document, SCHEMA, ROOT),
                )

        document = load_json_strict(FIXTURES / "valid" / "full-http.json")
        document["revision"] = int(1)
        self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT))

    def test_display_order_requires_an_exact_python_int_in_every_collection_family(self):
        families = [
            ("requirements", ["requirements", 0, "display_order"]),
            ("test_cases", ["test_cases", 0, "display_order"]),
            ("steps", ["test_cases", 0, "steps", 0, "display_order"]),
            ("inputs", ["test_cases", 0, "steps", 0, "inputs", 0, "display_order"]),
            ("outputs", ["test_cases", 0, "steps", 0, "outputs", 0, "display_order"]),
            ("expectations", ["test_cases", 0, "steps", 0, "expectations", 0, "display_order"]),
            ("assertions", ["test_cases", 0, "steps", 0, "expectations", 0, "assertions", 0, "display_order"]),
        ]
        for family, path in families:
            for value in (1.0, True):
                with self.subTest(family=family, value=value):
                    document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                    target = document
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = value
                    self.assertEqual(
                        [{
                            "path": "/" + "/".join(str(part) for part in path),
                            "code": "CANONICAL_DISPLAY_ORDER",
                            "message": "display_order must be 1 to preserve physical array order",
                        }],
                        schema_diagnostics(document, SCHEMA, ROOT),
                    )

    def test_document_id_requires_a_full_safe_identifier_match(self):
        for document_id in ("TCDOC-safe\n", "TCDOC-safe\r"):
            with self.subTest(document_id=repr(document_id)):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["document_id"] = document_id
                self.assertEqual(
                    [{
                        "path": "/document_id",
                        "code": "CANONICAL_DOCUMENT_ID",
                        "message": "document_id must fully match the canonical safe identifier grammar",
                    }],
                    schema_diagnostics(document, SCHEMA, ROOT),
                )

        for document_id in ("TCDOC-a", "TCDOC-a_", "TCDOC-a.z-9_"):
            with self.subTest(document_id=document_id):
                document = load_json_strict(FIXTURES / "valid" / "full-http.json")
                document["document_id"] = document_id
                self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT))

    def test_manual_step_requires_reason_and_null_operation(self):
        document = load_json_strict(FIXTURES / "valid" / "minimal-manual.json")
        step = document["test_cases"][0]["steps"][0]
        step["manual_reason"] = None
        diagnostics = schema_diagnostics(document, SCHEMA, ROOT)
        self.assertTrue(any(item["path"] == "/test_cases/0/steps/0/manual_reason" for item in diagnostics))

    def test_cli_reports_strict_json_errors(self):
        exit_code, report = validate(str(SCHEMA), str(FIXTURES / "invalid" / "utf8-bom.json"))
        self.assertEqual(2, exit_code)
        self.assertEqual("error", report["status"])
        self.assertIn("UTF-8 BOM is not permitted", report["errors"][0]["message"])

    def test_categories_must_keep_canonical_physical_order(self):
        document = load_json_strict(FIXTURES / "valid" / "full-http.json")
        document["test_cases"][0]["categories"] = ["functional", "positive"]
        self.assertEqual(
            "CANONICAL_CATEGORY_ORDER",
            schema_diagnostics(document, SCHEMA, ROOT)[0]["code"],
        )

    def test_task_one_keeps_relational_blocker_state_checks_out_of_diagnostics(self):
        document = load_json_strict(FIXTURES / "valid" / "minimal-manual.json")
        document["test_cases"][0]["steps"][0]["inputs"] = [{
            "input_id": "INPUT-structural-only",
            "display_order": 1,
            "target": {"location": "path", "name": "item", "sensitive": False},
            "source": {"kind": "literal", "value": "item-1"},
            "semantic_type": {"kind": "json", "type": "string"},
        }]
        self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT))

    def test_registry_never_retrieves_remote_refs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            schemas = root / "schemas"
            schemas.mkdir()
            schema = schemas / "remote-ref.schema.json"
            schema.write_text(json.dumps({
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "schemas/remote-ref.schema.json",
                "$ref": "https://example.invalid/never-fetched.schema.json",
            }), encoding="utf-8")
            validator = validator_for(schema, root)
            with self.assertRaises(_WrappedReferencingError) as raised:
                list(validator.iter_errors({"synthetic": True}))
        self.assertIn("https://example.invalid/never-fetched.schema.json", str(raised.exception))

    def test_inline_management_shapes_reject_missing_and_extra_fields(self):
        document = load_json_strict(FIXTURES / "valid" / "full-http.json")
        management = document["test_cases"][0]["management"]
        mutations = [
            ("external_links missing issues", management["external_links"], "issues", None),
            ("external_links extra", management["external_links"], None, "unexpected"),
            ("external_keys extra", management["external_keys"], None, "unexpected"),
        ]
        for name, target, missing, extra in mutations:
            with self.subTest(name=name):
                candidate = copy.deepcopy(document)
                copied_target = candidate["test_cases"][0]["management"]["external_links" if target is management["external_links"] else "external_keys"]
                if missing:
                    del copied_target[missing]
                else:
                    copied_target[extra] = True
                self.assertNotEqual([], schema_diagnostics(candidate, SCHEMA, ROOT))

    def test_every_represented_nested_shape_rejects_missing_and_extra_fields(self):
        document = load_json_strict(FIXTURES / "valid" / "full-http.json")
        step = document["test_cases"][0]["steps"][0]
        type_json = {"kind": "json", "type": "string"}
        type_named = {"kind": "named", "name": "order_id", "representation": "string"}
        provenance = ["https://example.invalid/source"]
        samples = [
            ("type_descriptor", type_json, "type"), ("type_descriptor", type_named, "name"),
            ("metadata", document["metadata"], "project"),
            ("subject", document["metadata"]["subject"], "path"),
            ("subject", {"kind": "generic", "name": "Synthetic"}, "name"),
            ("capability_member", {"name": "result", "semantic_type": type_json}, "name"),
            ("capability_argument", {"name": "argument", "semantic_type": type_json, "required": True}, "required"),
            ("capability", {"capability_id": "CAP-synthetic", "adapter": "example.adapter", "action": "run", "arguments": [], "results": [], "provenance": provenance}, "action"),
            ("requirement", document["requirements"][0], "text"),
            ("management", document["test_cases"][0]["management"], "owner"),
            ("operation", step["operation"], "method"),
            ("operation", {"kind": "project_action", "capability_id": "CAP-synthetic"}, "capability_id"),
            ("base_url_source", step["operation"]["base_url_source"], "name"),
            ("input_target", {"location": "path", "name": "item", "sensitive": False}, "name"),
            ("input_target", {"location": "query", "name": "item", "sensitive": False}, "name"),
            ("input_target", {"location": "header", "name": "x-item", "sensitive": False}, "name"),
            ("input_target", {"location": "body", "pointer": "/item", "sensitive": False}, "pointer"),
            ("input_target", {"location": "arg", "name": "item", "sensitive": False}, "name"),
            ("value_source", {"kind": "literal", "value": "item"}, "value"),
            ("value_source", {"kind": "step_output", "step_id": "STEP-earlier", "output_id": "item"}, "output_id"),
            ("value_source", {"kind": "fixture", "name": "item"}, "name"),
            ("value_source", {"kind": "environment", "name": "ITEM"}, "name"),
            ("value_source", {"kind": "secret_handle", "handle": "secret/item", "safe_label": "item secret"}, "safe_label"),
            ("input", step["inputs"][0], "source"),
            ("output_source", {"kind": "http_status"}, "kind"),
            ("output_source", {"kind": "http_header", "name": "etag"}, "name"),
            ("output_source", {"kind": "http_body", "pointer": "/item"}, "pointer"),
            ("output_source", {"kind": "project_result", "name": "item"}, "name"),
            ("output", step["outputs"][0], "semantic_type"),
            ("assertion_actual", {"kind": "http_status"}, "kind"),
            ("assertion_actual", {"kind": "http_header", "name": "etag"}, "name"),
            ("assertion_actual", {"kind": "http_body", "pointer": "/item", "semantic_type": type_json, "type_provenance": provenance}, "pointer"),
            ("assertion_actual", {"kind": "project_result", "name": "item"}, "name"),
            ("assertion_actual", {"kind": "step_output", "step_id": "STEP-earlier", "output_id": "item"}, "output_id"),
            ("assertion_expected", {"kind": "literal", "value": "item"}, "value"),
            ("assertion_expected", {"kind": "step_output", "step_id": "STEP-earlier", "output_id": "item"}, "output_id"),
            ("assertion_expected", {"kind": "fixture", "name": "item", "semantic_type": type_json, "type_provenance": provenance}, "name"),
            ("assertion_expected", {"kind": "environment", "name": "ITEM", "semantic_type": type_json, "type_provenance": provenance}, "name"),
            ("assertion_expected", {"kind": "secret_handle", "handle": "secret/item", "safe_label": "item secret", "semantic_type": type_json, "type_provenance": provenance}, "handle"),
            ("assertion_expected", {"kind": "regex", "dialect": "portable-regex-v1", "pattern": "item"}, "pattern"),
            ("assertion_expected", {"kind": "schema_ref", "uri": "https://example.invalid/schema", "sha256": "sha256:" + "0" * 64, "draft": "2020-12", "provenance": provenance}, "uri"),
            ("assertion", step["expectations"][0]["assertions"][0], "actual"),
            ("expectation", step["expectations"][0], "text"),
            ("blocker", {"blocker_id": "BLOCK-synthetic", "code": "UNRESOLVED_OPERATION", "field_path": "/operation", "reason": "Missing adapter context.", "provenance": provenance}, "reason"),
            ("step", step, "action"),
            ("test_case", document["test_cases"][0], "title"),
        ]
        base = validator_for(SCHEMA, ROOT)
        for definition, valid, required in samples:
            with self.subTest(definition=definition, mutation="missing", required=required):
                validator = base.evolve(schema={"$ref": f"schemas/canonical-test-document.schema.json#/$defs/{definition}"})
                missing = copy.deepcopy(valid)
                del missing[required]
                self.assertNotEqual([], list(validator.iter_errors(missing)))
            with self.subTest(definition=definition, mutation="extra"):
                validator = base.evolve(schema={"$ref": f"schemas/canonical-test-document.schema.json#/$defs/{definition}"})
                extra = copy.deepcopy(valid)
                extra["unexpected"] = True
                self.assertNotEqual([], list(validator.iter_errors(extra)))
