"""Shared, socket-free conformance consumer for ``http-binding-v1`` corpora."""

from __future__ import annotations

import json
import math
from pathlib import Path
import unittest
from typing import Any, Mapping

from tools.assertion_dsl import ValueState
from tools.http_binding_v1 import (
    AbstractRequest,
    ExecutionError,
    RawResponse,
    build_request,
    execute_once,
    observe_response,
    validate_preflight_base_url,
    validate_preflight_input_value,
    validate_preflight_typed_value,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "http-binding-v1"


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_corpus(name: str, kind: str) -> Mapping[str, Any]:
    corpus = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates)
    required = {"contract", "corpus_version", "kind", "cases"}
    optional = {"value_encodings"}
    if set(corpus) - optional != required or corpus["contract"] != "http-binding-v1" or corpus["corpus_version"] != "1.0.0" or corpus["kind"] != kind:
        raise AssertionError(f"{name} is not the exact {kind} corpus envelope")
    case_ids = [case.get("case_id") for case in corpus["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError(f"{name} has duplicate case_id values")
    return corpus


def decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$corpus_value"}:
        return {
            "nan": math.nan,
            "positive_infinity": math.inf,
            "negative_infinity": -math.inf,
        }[value["$corpus_value"]]
    return {key: decode_value(item) for key, item in value.items()}


def decode_bytes(descriptor: Any) -> bytes | None:
    if descriptor is None:
        return None
    if not isinstance(descriptor, Mapping) or set(descriptor) != {"encoding", "data"}:
        raise AssertionError("corpus byte descriptor is invalid")
    if descriptor["encoding"] == "utf8" and isinstance(descriptor["data"], str):
        return descriptor["data"].encode("utf-8")
    if descriptor["encoding"] == "hex" and isinstance(descriptor["data"], str):
        return bytes.fromhex(descriptor["data"])
    raise AssertionError("corpus byte descriptor has an unsupported encoding")


def request_from_corpus(value: Mapping[str, Any]) -> AbstractRequest:
    return AbstractRequest(
        value["method"],
        value["absolute_url"],
        tuple(tuple(pair) for pair in value["ordered_headers"]),
        decode_bytes(value["body_bytes"]),
    )


def response_from_corpus(value: Mapping[str, Any], replacement: Mapping[str, Any] | None = None) -> RawResponse:
    fields: dict[str, Any] = {
        "status": value["status"],
        "ordered_headers": tuple(tuple(pair) for pair in value["ordered_headers"]),
        "body_bytes": decode_bytes(value["body_bytes"]),
    }
    if replacement is not None:
        fields.update(replacement)
    return RawResponse(**fields)


class RecordingFakeTransport:
    """The only transport seam used by the shared corpora; it never opens a socket."""

    def __init__(self, transport: Mapping[str, Any]):
        self._transport = transport
        self.calls: list[tuple[AbstractRequest, float]] = []

    def send_once(self, request: AbstractRequest, timeout_seconds: float) -> RawResponse:
        self.calls.append((request, timeout_seconds))
        outcome = self._transport["outcome"]
        if outcome == "response":
            replacement = self._transport.get("raw_response_encoding", {}).get("fields")
            return response_from_corpus(self._transport["response"], replacement)
        if outcome == "invalid_response":
            return object()  # type: ignore[return-value]
        if outcome in {"error", "injected_execution_error"}:
            raise ExecutionError("SYNTHETIC", "/synthetic", "synthetic")
        raise AssertionError(f"unsupported corpus transport outcome: {outcome}")


def providers_from_corpus(rows: list[Mapping[str, Any]]) -> tuple[dict[tuple[str, str], Any], list[dict[str, str]]]:
    providers: dict[tuple[str, str], Any] = {}
    calls: list[dict[str, str]] = []
    for row in rows:
        key = (row["kind"], row["key"])
        if key in providers:
            raise AssertionError("corpus provider key is duplicated")

        def resolve(row: Mapping[str, Any] = row) -> Any:
            calls.append({"kind": row["kind"], "key": row["key"]})
            if row["state"] == "VALUE":
                return decode_value(row.get("value"))
            if row["state"] == "MISSING":
                return ValueState.missing()
            if row["state"] in {"ERROR", "DENIED"}:
                raise RuntimeError("synthetic provider failure")
            raise AssertionError(f"unsupported corpus provider state: {row['state']}")

        providers[key] = resolve
    return providers, calls


def step_outputs_from_corpus(rows: list[Mapping[str, Any]]) -> dict[tuple[str, str], Any]:
    outputs: dict[tuple[str, str], Any] = {}
    for row in rows:
        key = (row["step_id"], row["output_id"])
        if key in outputs:
            raise AssertionError("corpus step-output key is duplicated")
        if row["state"] == "VALUE":
            outputs[key] = decode_value(row.get("value"))
        elif row["state"] == "MISSING":
            outputs[key] = ValueState.missing()
        else:
            raise AssertionError(f"unsupported corpus step-output state: {row['state']}")
    return outputs


def timeout_from_corpus(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    if set(value) == {"literal_encoding"}:
        return decode_value({"$corpus_value": value["literal_encoding"]})
    raise AssertionError("unsupported corpus timeout encoding")


class SharedCorpusTests(unittest.TestCase):
    def assert_error(self, expected: Mapping[str, Any], caught: ExecutionError) -> None:
        self.assertEqual(
            (expected["code"], expected["path"], expected["message"]),
            (caught.code, caught.path, caught.message),
        )

    def assert_request(self, expected: Mapping[str, Any], actual: AbstractRequest) -> None:
        self.assertEqual(request_from_corpus(expected), actual)

    def assert_transport_calls(self, expected: Mapping[str, Any], transport: RecordingFakeTransport) -> None:
        self.assertEqual(expected["transport_calls"], len(transport.calls))

    def run_binding_case(self, row: Mapping[str, Any]) -> None:
        providers, provider_calls = providers_from_corpus(row["providers"])
        transport = RecordingFakeTransport(row["transport"])
        expected = row["expected"]
        caught_error: ExecutionError | None = None
        try:
            request = build_request(decode_value(row["operation"]), decode_value(row["inputs"]), providers, step_outputs_from_corpus(row["step_outputs"]))
            if expected["kind"] == "error":
                response = execute_once(request, transport, timeout_from_corpus(row["timeout_seconds"]))
                self.fail(f"{row['case_id']} expected error, got {response!r}")
            if expected["kind"] == "request":
                self.assert_request(expected["request"], request)
            response = execute_once(request, transport, timeout_from_corpus(row["timeout_seconds"]))
            if expected["kind"] == "response":
                self.assertEqual(response_from_corpus(expected["response"]), response)
        except ExecutionError as error:
            caught_error = error
        if caught_error is not None:
            if expected["kind"] != "error":
                self.fail(f"{row['case_id']} unexpectedly raised {caught_error.code} at {caught_error.path}: {caught_error.message}")
            self.assert_error(expected, caught_error)
        self.assert_transport_calls(expected, transport)
        if "provider_calls" in expected:
            self.assertEqual(expected["provider_calls"], provider_calls)

    def test_request_corpus(self) -> None:
        corpus = load_corpus("request-cases.json", "request")
        self.assertEqual(131, len(corpus["cases"]))
        for row in corpus["cases"]:
            with self.subTest(case_id=row["case_id"]):
                self.run_binding_case(row)

    def test_phase_corpus(self) -> None:
        corpus = load_corpus("phase-cases.json", "phase")
        self.assertEqual(40, len(corpus["cases"]))
        for row in corpus["cases"]:
            with self.subTest(case_id=row["case_id"]):
                self.run_binding_case(row)

    def test_response_corpus(self) -> None:
        corpus = load_corpus("response-cases.json", "response")
        self.assertEqual(47, len(corpus["cases"]))
        for row in corpus["cases"]:
            with self.subTest(case_id=row["case_id"]):
                transport = RecordingFakeTransport(row["transport"])
                expected = row["expected"]
                caught_error: ExecutionError | None = None
                try:
                    response = execute_once(request_from_corpus(row["request"]), transport, 1.0)
                    state = observe_response(response, row["source"])
                    if expected["kind"] == "error":
                        self.fail(f"{row['case_id']} expected error, got {state!r}")
                    if expected["state"] == "MISSING":
                        self.assertTrue(state.is_missing)
                    else:
                        self.assertEqual(ValueState.value(expected["value"]), state)
                except ExecutionError as error:
                    caught_error = error
                if caught_error is not None:
                    if expected["kind"] != "error":
                        self.fail(f"{row['case_id']} unexpectedly raised {caught_error.code} at {caught_error.path}: {caught_error.message}")
                    self.assert_error(expected, caught_error)
                self.assert_transport_calls(expected, transport)

    def test_empty_target_names_are_static_and_provider_free(self) -> None:
        """Empty path/query/header names fail before even the base provider callback."""
        rows = (
            ("path", "/{id}", "INVALID_TARGET", "Target name is invalid."),
            ("query", "/x", "INVALID_TARGET", "Target name is invalid."),
            ("header", "/x", "INVALID_HEADER_NAME", "Header name must be lowercase, non-reserved RFC 9110 tchar."),
        )
        for location, path, code, message in rows:
            with self.subTest(location=location):
                calls: list[str] = []
                operation = {
                    "kind": "http", "binding_profile": "http-binding-v1",
                    "base_url_source": {"kind": "environment", "name": "base", "provenance": ["test"]},
                    "method": "GET", "path": path,
                }
                input_row = {
                    "input_id": "INPUT-empty", "display_order": 1,
                    "target": {"location": location, "name": "", "sensitive": False},
                    "source": {"kind": "literal", "value": "x"},
                    "semantic_type": {"kind": "json", "type": "string"},
                }
                with self.assertRaises(ExecutionError) as caught:
                    build_request(operation, [input_row], {("environment", "base"): lambda: calls.append("base")}, {})
                self.assertEqual((code, "/inputs/0/target/name", message), (caught.exception.code, caught.exception.path, caught.exception.message))
                self.assertEqual([], calls)


class PreflightValidationWrapperTests(unittest.TestCase):
    def test_wrappers_preserve_http_binding_v1_validation(self) -> None:
        with self.assertRaises(ExecutionError) as base_error:
            validate_preflight_base_url("https://API.example")
        self.assertEqual((base_error.exception.code, base_error.exception.path), ("INVALID_BASE_URL", "/base_url_source/name"))

        item = {
            "source": {"kind": "environment", "name": "ITEM"},
            "semantic_type": {"kind": "json", "type": "string"},
            "target": {"location": "header", "name": "x-item", "sensitive": False},
        }
        with self.assertRaises(ExecutionError) as input_error:
            validate_preflight_input_value("\n", item, 3)
        self.assertEqual(input_error.exception.code, "PREFLIGHT_PROVIDER_TARGET_ERROR")

        with self.assertRaises(ExecutionError) as typed_error:
            validate_preflight_typed_value("wrong", {"kind": "json", "type": "integer"}, "/expected")
        self.assertEqual((typed_error.exception.code, typed_error.exception.path), ("PREFLIGHT_PROVIDER_TYPE_MISMATCH", "/expected"))


if __name__ == "__main__":
    unittest.main()
