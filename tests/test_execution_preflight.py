"""Socket-free readiness checks for canonical provider and adapter bindings."""

from __future__ import annotations

import copy
from types import MappingProxyType
import unittest
from typing import Any

from tests.fixture_factory import canonical_document
from tools.assertion_dsl import ValueState
from tools.canonical_document import validate_canonical_document
from tools.execution_preflight import (
    EnvironmentOnlyProviderResolver,
    RejectingAdapterRegistry,
    preflight_execution,
)


class RecordingResolver:
    def __init__(self, values: dict[tuple[str, str], Any]) -> None:
        self.values, self.calls = values, []

    def resolve(self, kind: str, name: str) -> Any:
        self.calls.append((kind, name))
        value = self.values[(kind, name)]
        if isinstance(value, BaseException):
            raise value
        return value


class RecordingRegistry:
    def __init__(self, outcomes: dict[tuple[str, str], Any] | None = None) -> None:
        self.outcomes, self.calls = outcomes or {}, []

    def require(self, adapter: str, action: str) -> None:
        self.calls.append((adapter, action))
        outcome = self.outcomes.get((adapter, action))
        if isinstance(outcome, BaseException):
            raise outcome


def _external_document() -> dict[str, Any]:
    document = canonical_document()
    step = document["test_cases"][0]["steps"][0]
    step["inputs"] = [
        {"input_id": "INPUT-item", "display_order": 1, "target": {"location": "path", "name": "item_id", "sensitive": False}, "source": {"kind": "environment", "name": "ITEM"}, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["test"]},
        {"input_id": "INPUT-filter", "display_order": 2, "target": {"location": "query", "name": "filter", "sensitive": False}, "source": {"kind": "fixture", "name": "FILTER"}, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["test"]},
        {"input_id": "INPUT-token", "display_order": 3, "target": {"location": "header", "name": "x-token", "sensitive": True}, "source": {"kind": "secret_handle", "handle": "HANDLE", "safe_label": "token"}, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["test"]},
    ]
    step["expectations"][0]["assertions"][0]["expected"] = {"kind": "environment", "name": "STATUS", "semantic_type": {"kind": "json", "type": "integer"}, "type_provenance": ["test"]}
    assert validate_canonical_document(document) == []
    return document


def _project_document() -> dict[str, Any]:
    document = canonical_document(2)
    document["operation_capabilities"] = [{"capability_id": "CAP-read", "adapter": "fixture", "action": "read", "arguments": [], "results": [{"name": "value", "semantic_type": {"kind": "json", "type": "string"}}], "provenance": ["test"]}]
    for step in document["test_cases"][0]["steps"]:
        step["operation"], step["inputs"], step["outputs"] = {"kind": "project_action", "capability_id": "CAP-read"}, [], []
        assertion = step["expectations"][0]["assertions"][0]
        assertion.update({"actual": {"kind": "project_result", "name": "value"}, "operator": "exists"})
        assertion.pop("expected")
    assert validate_canonical_document(document) == []
    return document


class ExecutionPreflightTests(unittest.TestCase):
    def test_ready_resolves_each_provider_once_and_returns_immutable_values(self) -> None:
        resolver = RecordingResolver({("environment", "API_BASE_URL"): "https://api.example", ("environment", "ITEM"): "42", ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): 200})
        result = preflight_execution(_external_document(), resolver, RecordingRegistry())
        self.assertEqual(result.status, "READY")
        self.assertEqual(resolver.calls, [("environment", "API_BASE_URL"), ("environment", "ITEM"), ("fixture", "FILTER"), ("secret_handle", "HANDLE"), ("environment", "STATUS")])
        self.assertEqual(dict(result.resolved_values), resolver.values)
        self.assertEqual(result.diagnostics, ())
        with self.assertRaises(TypeError): result.resolved_values[("fixture", "OTHER")] = "x"  # type: ignore[index]
        with self.assertRaises(Exception): result.status = "NOT_RUNNABLE"  # type: ignore[misc]

    def test_diagnostic_rows_are_immutable(self) -> None:
        values = {("environment", "API_BASE_URL"): "https://api.example", ("environment", "ITEM"): "42", ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): "200"}
        result = preflight_execution(_external_document(), RecordingResolver(values), RecordingRegistry())
        with self.assertRaises(TypeError): result.diagnostics[0]["message"] = "leak"  # type: ignore[index]

    def test_invalid_canonical_document_makes_no_callbacks_and_keeps_safe_diagnostics(self) -> None:
        document = _external_document()
        document["test_cases"][0]["steps"][0]["inputs"][0]["source"] = {"kind": "environment", "name": ""}
        resolver, registry = RecordingResolver({}), RecordingRegistry()
        result = preflight_execution(document, resolver, registry)
        self.assertEqual(result.status, "NOT_RUNNABLE")
        self.assertEqual([(row["path"], row["code"]) for row in result.diagnostics], [(row["path"], row["code"]) for row in validate_canonical_document(document)])
        self.assertEqual({row["message"] for row in result.diagnostics}, {"Canonical document is invalid."})
        self.assertEqual((resolver.calls, registry.calls, dict(result.resolved_values)), ([], [], {}))

    def test_invalid_canonical_diagnostic_does_not_echo_instance_value(self) -> None:
        document = _external_document()
        document["test_cases"][0]["title"] = ["TOPSECRET"]
        result = preflight_execution(document, RecordingResolver({}), RecordingRegistry())
        self.assertEqual(result.status, "NOT_RUNNABLE")
        self.assertNotIn("TOPSECRET", str(result.diagnostics))

    def test_provider_json_pointer_paths_do_not_echo_resolved_object_keys(self) -> None:
        document = _external_document()
        item = document["test_cases"][0]["steps"][0]["inputs"][0]
        document["test_cases"][0]["steps"][0]["operation"]["path"] = "/items"
        item.update({"target": {"location": "body", "pointer": "/item", "sensitive": False}, "semantic_type": {"kind": "json", "type": "object"}})
        assertion = document["test_cases"][0]["steps"][0]["expectations"][0]["assertions"][0]
        assertion["actual"] = {"kind": "http_body", "pointer": "", "semantic_type": {"kind": "json", "type": "object"}, "type_provenance": ["test"]}
        expected = assertion["expected"]
        expected["semantic_type"] = {"kind": "json", "type": "object"}
        assert validate_canonical_document(document) == []
        poisoned = {"TOPSECRET": float("nan")}
        values = {("environment", "API_BASE_URL"): "https://api.example", ("environment", "ITEM"): poisoned, ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): poisoned}
        result = preflight_execution(document, RecordingResolver(values), RecordingRegistry())
        paths = {row["path"] for row in result.diagnostics}
        self.assertIn("/test_cases/0/steps/0/inputs/0/source", paths)
        self.assertIn("/test_cases/0/steps/0/expectations/0/assertions/0/expected", paths)
        self.assertNotIn("TOPSECRET", str(result.diagnostics))

    def test_provider_failures_are_safe_and_empty_values_are_not_retained(self) -> None:
        cases = [(LookupError("ITEM"), "PREFLIGHT_PROVIDER_MISSING", "Required provider value is missing."), (PermissionError("ITEM"), "PREFLIGHT_PROVIDER_INACCESSIBLE", "Required provider value is inaccessible."), (RuntimeError("sensitive ITEM"), "PREFLIGHT_PROVIDER_ERROR", "Required provider could not be resolved."), ("", "PREFLIGHT_PROVIDER_EMPTY", "Required provider value is empty."), (ValueState.missing(), "PREFLIGHT_PROVIDER_MISSING", "Required provider value is missing.")]
        for outcome, code, message in cases:
            with self.subTest(code=code):
                values = {("environment", "API_BASE_URL"): "https://api.example", ("environment", "ITEM"): outcome, ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): 200}
                result = preflight_execution(_external_document(), RecordingResolver(values), RecordingRegistry())
                self.assertIn(("/test_cases/0/steps/0/inputs/0/source", code, message), {(row["path"], row["code"], row["message"]) for row in result.diagnostics})
                self.assertEqual(dict(result.resolved_values), {})
                self.assertTrue(all("ITEM" not in row["message"] and "secret" not in row["message"] for row in result.diagnostics))

    def test_reused_provider_is_validated_at_every_use(self) -> None:
        document = _external_document()
        item = document["test_cases"][0]["steps"][0]["inputs"][0]
        duplicate = copy.deepcopy(item)
        duplicate.update({"input_id": "INPUT-again", "display_order": 4, "target": {"location": "header", "name": "x-again", "sensitive": False}})
        document["test_cases"][0]["steps"][0]["inputs"].append(duplicate)
        assert validate_canonical_document(document) == []
        values = {("environment", "API_BASE_URL"): "https://api.example", ("environment", "ITEM"): "\n", ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): 200}
        resolver = RecordingResolver(values)
        result = preflight_execution(document, resolver, RecordingRegistry())
        self.assertEqual(resolver.calls.count(("environment", "ITEM")), 1)
        self.assertIn(("/test_cases/0/steps/0/inputs/3/source", "PREFLIGHT_PROVIDER_TARGET_ERROR"), {(row["path"], row["code"]) for row in result.diagnostics})

    def test_assertion_type_and_base_url_use_task4_validation(self) -> None:
        values = {("environment", "API_BASE_URL"): "https://API.example", ("environment", "ITEM"): "42", ("fixture", "FILTER"): "new", ("secret_handle", "HANDLE"): "secret", ("environment", "STATUS"): "200"}
        result = preflight_execution(_external_document(), RecordingResolver(values), RecordingRegistry())
        observed = {(row["path"], row["code"]) for row in result.diagnostics}
        self.assertIn(("/test_cases/0/steps/0/operation/base_url_source", "INVALID_BASE_URL"), observed)
        self.assertIn(("/test_cases/0/steps/0/expectations/0/assertions/0/expected", "PREFLIGHT_PROVIDER_TYPE_MISMATCH"), observed)

    def test_non_external_and_unready_steps_are_ignored(self) -> None:
        document = _external_document()
        step = document["test_cases"][0]["steps"][0]
        step["manual_only"], step["manual_reason"], step["operation"], step["automation_blockers"] = True, "manual", None, []
        step["expectations"][0]["assertions"] = []
        assert validate_canonical_document(document) == []
        resolver, registry = RecordingResolver({}), RecordingRegistry()
        result = preflight_execution(document, resolver, registry)
        self.assertEqual((result.status, resolver.calls, registry.calls), ("READY", [], []))

    def test_adapter_outcomes_are_safe_and_each_pair_is_checked_once(self) -> None:
        cases = [(None, "READY", None), (LookupError("fixture"), "NOT_RUNNABLE", "PREFLIGHT_ADAPTER_UNAVAILABLE"), (PermissionError("fixture"), "NOT_RUNNABLE", "PREFLIGHT_ADAPTER_INACCESSIBLE"), (RuntimeError("fixture"), "NOT_RUNNABLE", "PREFLIGHT_ADAPTER_ERROR")]
        for outcome, status, code in cases:
            with self.subTest(code=code):
                registry = RecordingRegistry({("fixture", "read"): outcome})
                result = preflight_execution(_project_document(), RecordingResolver({}), registry)
                self.assertEqual((result.status, registry.calls), (status, [("fixture", "read")]))
                if code is not None:
                    self.assertIn(("/test_cases/0/steps/0/operation", code), {(row["path"], row["code"]) for row in result.diagnostics})
                    self.assertEqual(dict(result.resolved_values), {})

    def test_environment_and_rejecting_defaults_are_exact(self) -> None:
        resolver = EnvironmentOnlyProviderResolver({"PRESENT": " exact "})
        self.assertEqual(resolver.resolve("environment", "PRESENT"), " exact ")
        for kind, name in (("fixture", "PRESENT"), ("environment", "MISSING")):
            with self.assertRaises(LookupError): resolver.resolve(kind, name)
        with self.assertRaises(LookupError): RejectingAdapterRegistry().require("adapter", "action")
