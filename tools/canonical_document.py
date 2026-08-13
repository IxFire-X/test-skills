"""Canonical test-document validation, JSON bytes, and revision identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from pathlib import Path
from typing import Any, Sequence

from tools.schema_validation import load_json_strict, schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL_DOCUMENT_SCHEMA = _ROOT / "schemas" / "canonical-test-document.schema.json"


class CanonicalDocumentError(ValueError):
    """Raised when a canonical document is structurally or semantically invalid."""

    def __init__(self, diagnostics: Sequence[dict[str, str]]) -> None:
        rendered = json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))
        self.diagnostics = tuple(MappingProxyType(dict(item)) for item in diagnostics)
        super().__init__(rendered)


def _pointer(*parts: object) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _diagnostic(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _json_type(value: Any) -> dict[str, str]:
    if value is None:
        name = "null"
    elif isinstance(value, bool):
        name = "boolean"
    elif isinstance(value, int):
        name = "integer"
    elif isinstance(value, float):
        name = "number"
    elif isinstance(value, str):
        name = "string"
    elif isinstance(value, list):
        name = "array"
    else:
        name = "object"
    return {"kind": "json", "type": name}


def _compatible(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Return whether an actual descriptor can be used where expected is declared."""
    if actual.get("kind") == expected.get("kind") == "named":
        return actual.get("name") == expected.get("name") and actual.get("representation") == expected.get("representation")
    if actual.get("kind") != "json" or expected.get("kind") != "json":
        return False
    return actual.get("type") == expected.get("type") or (
        actual.get("type") == "integer" and expected.get("type") == "number"
    )


def _storage_equal(actual: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Stored source descriptors are exact, unlike operator numeric compatibility."""
    return actual == stored


def _representation(descriptor: dict[str, Any]) -> str:
    return descriptor.get("type") if descriptor.get("kind") == "json" else descriptor.get("representation")


def _rfc6901_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/"))


_BLOCKER_PATHS = MappingProxyType({
    "UNRESOLVED_OPERATION": frozenset({"/operation"}),
    "UNSUPPORTED_AUTOMATION_CAPABILITY": frozenset({"/operation"}),
    "UNRESOLVED_INPUT_BINDING": frozenset({"/inputs"}),
    "UNRESOLVED_OUTPUT_BINDING": frozenset({"/outputs"}),
    "UNRESOLVED_ASSERTION": frozenset(),
    "UNSUPPORTED_ASSERTION_DIALECT": frozenset(),
    "UNCONFIRMED_TYPE": frozenset({"/inputs", "/outputs"}),
})
_VALUE_SOURCE_KINDS = frozenset({"literal", "step_output", "fixture", "environment", "secret_handle"})


@dataclass(frozen=True)
class _CaseIndex:
    steps: MappingProxyType
    outputs: MappingProxyType


@dataclass(frozen=True)
class _DocumentIndex:
    requirements: MappingProxyType
    capabilities: MappingProxyType
    cases: tuple[dict[str, Any], ...]
    case_indexes: tuple[_CaseIndex, ...]


def _unique_items(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item[key]] = counts.get(item[key], 0) + 1
    return {item[key]: item for item in items if counts[item[key]] == 1}


def _duplicate_ids(items: list[dict[str, Any]], key: str, path: tuple[object, ...], diagnostics: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        value = item[key]
        if value in seen:
            diagnostics.append(_diagnostic(_pointer(*path, index, key), "SEMANTIC_DUPLICATE_ID", f"duplicate {key}: {value}"))
        seen.add(value)


def _build_index(document: dict[str, Any], diagnostics: list[dict[str, str]]) -> _DocumentIndex:
    capabilities, requirements, cases = document["operation_capabilities"], document["requirements"], document["test_cases"]
    _duplicate_ids(capabilities, "capability_id", ("operation_capabilities",), diagnostics)
    _duplicate_ids(requirements, "requirement_id", ("requirements",), diagnostics)
    _duplicate_ids(cases, "case_id", ("test_cases",), diagnostics)
    capability_actions: set[tuple[str, str]] = set()
    global_steps: set[str] = set()
    global_expectations: set[str] = set()
    global_assertions: set[str] = set()
    case_indexes: list[_CaseIndex] = []
    for cap_index, capability in enumerate(capabilities):
        pair = (capability["adapter"], capability["action"])
        if pair in capability_actions:
            diagnostics.append(_diagnostic(_pointer("operation_capabilities", cap_index), "SEMANTIC_DUPLICATE_CAPABILITY_ACTION", "adapter/action pair must be unique"))
        capability_actions.add(pair)
        for role in ("arguments", "results"):
            _duplicate_ids(capability[role], "name", ("operation_capabilities", cap_index, role), diagnostics)
    for case_index, case in enumerate(cases):
        steps = case["steps"]
        _duplicate_ids(steps, "step_id", ("test_cases", case_index, "steps"), diagnostics)
        for step_index, step in enumerate(steps):
            step_path = ("test_cases", case_index, "steps", step_index)
            if step["step_id"] in global_steps:
                diagnostics.append(_diagnostic(_pointer(*step_path, "step_id"), "SEMANTIC_DUPLICATE_ID", "step_id must be document-global"))
            global_steps.add(step["step_id"])
            _duplicate_ids(step["inputs"], "input_id", (*step_path, "inputs"), diagnostics)
            _duplicate_ids(step["outputs"], "output_id", (*step_path, "outputs"), diagnostics)
            _duplicate_ids(step["automation_blockers"], "blocker_id", (*step_path, "automation_blockers"), diagnostics)
            _duplicate_ids(step["expectations"], "expectation_id", (*step_path, "expectations"), diagnostics)
            for expectation_index, expectation in enumerate(step["expectations"]):
                expectation_path = (*step_path, "expectations", expectation_index)
                if expectation["expectation_id"] in global_expectations:
                    diagnostics.append(_diagnostic(_pointer(*expectation_path, "expectation_id"), "SEMANTIC_DUPLICATE_ID", "expectation_id must be document-global"))
                global_expectations.add(expectation["expectation_id"])
                _duplicate_ids(expectation["assertions"], "assertion_id", (*expectation_path, "assertions"), diagnostics)
                for assertion_index, assertion in enumerate(expectation["assertions"]):
                    if assertion["assertion_id"] in global_assertions:
                        diagnostics.append(_diagnostic(_pointer(*expectation_path, "assertions", assertion_index, "assertion_id"), "SEMANTIC_DUPLICATE_ID", "assertion_id must be document-global"))
                    global_assertions.add(assertion["assertion_id"])
        unique_steps = _unique_items(steps, "step_id")
        outputs = {
            step_id: MappingProxyType(_unique_items(step["outputs"], "output_id"))
            for step_id, step in unique_steps.items()
        }
        indexed_steps = {
            step["step_id"]: (step_index, step)
            for step_index, step in enumerate(steps)
            if step["step_id"] in unique_steps and unique_steps[step["step_id"]] is step
        }
        case_indexes.append(_CaseIndex(MappingProxyType(indexed_steps), MappingProxyType(outputs)))
    return _DocumentIndex(MappingProxyType(_unique_items(requirements, "requirement_id")), MappingProxyType(_unique_items(capabilities, "capability_id")), tuple(cases), tuple(case_indexes))


def _requirement_coverage(index: _DocumentIndex, document: dict[str, Any], diagnostics: list[dict[str, str]]) -> None:
    covered: set[str] = set()
    for case_index, case in enumerate(index.cases):
        requirement_ids = case["requirement_ids"]
        expected = [item["requirement_id"] for item in document["requirements"] if item["requirement_id"] in requirement_ids]
        if requirement_ids != expected:
            diagnostics.append(_diagnostic(_pointer("test_cases", case_index, "requirement_ids"), "CANONICAL_REQUIREMENT_ID_ORDER", "requirement_ids must follow referenced requirement display_order"))
        for requirement_index, requirement_id in enumerate(requirement_ids):
            if requirement_id not in index.requirements:
                diagnostics.append(_diagnostic(_pointer("test_cases", case_index, "requirement_ids", requirement_index), "SEMANTIC_UNKNOWN_REQUIREMENT", f"unknown requirement_id: {requirement_id}"))
            else:
                covered.add(requirement_id)
    for requirement_index, requirement in enumerate(document["requirements"]):
        if requirement["requirement_id"] in index.requirements and requirement["requirement_id"] not in covered:
            diagnostics.append(_diagnostic(_pointer("requirements", requirement_index, "requirement_id"), "SEMANTIC_UNCOVERED_REQUIREMENT", "every requirement must be covered by a test case"))


def _resolve_step_output(index: _DocumentIndex, case_index: int, step_index: int, source: dict[str, Any], source_path: tuple[object, ...], diagnostics: list[dict[str, str]]) -> dict[str, Any] | None:
    case = index.case_indexes[case_index]
    referenced = source["step_id"]
    referenced_step = case.steps.get(referenced)
    output = case.outputs.get(referenced, {}).get(source["output_id"])
    if referenced_step is None or output is None:
        diagnostics.append(_diagnostic(_pointer(*source_path), "SEMANTIC_UNKNOWN_STEP_OUTPUT", "step_output must reference an existing output in this case"))
        return None
    if referenced_step[0] >= step_index:
        diagnostics.append(_diagnostic(_pointer(*source_path), "SEMANTIC_FORWARD_STEP_OUTPUT", "step_output must reference a strictly earlier step"))
        return None
    consumer = next((step for index_, step in case.steps.values() if index_ == step_index), None)
    if referenced_step[1]["manual_only"] and consumer is not None and not consumer["manual_only"]:
        diagnostics.append(_diagnostic(_pointer(*source_path), "SEMANTIC_MANUAL_STEP_OUTPUT", "automatable step cannot consume manual-only step output"))
        return None
    return output["semantic_type"]


def _source_type(index: _DocumentIndex, case_index: int, step_index: int, source: dict[str, Any], source_path: tuple[object, ...], diagnostics: list[dict[str, str]]) -> dict[str, Any] | None:
    if source["kind"] == "literal":
        return _json_type(source["value"])
    if source["kind"] == "step_output":
        return _resolve_step_output(index, case_index, step_index, source, source_path, diagnostics)
    return source.get("semantic_type")


def _has_blocker_backed_http_input_omission(step: dict[str, Any], path_names: set[str] | None = None) -> bool:
    operation = step["operation"]
    if (
        operation is None
        or operation.get("kind") != "http"
        or not any(item["field_path"] == "/inputs" for item in step["automation_blockers"])
    ):
        return False
    placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}", operation["path"]))
    if path_names is None:
        path_names = {item["target"]["name"] for item in step["inputs"] if item["target"]["location"] == "path"}
    return path_names < placeholders


def _blocker_and_readiness(step: dict[str, Any], step_path: tuple[object, ...], diagnostics: list[dict[str, str]]) -> None:
    blockers = step["automation_blockers"]
    if [item["blocker_id"] for item in blockers] != sorted(item["blocker_id"] for item in blockers):
        diagnostics.append(_diagnostic(_pointer(*step_path, "automation_blockers"), "CANONICAL_BLOCKER_ORDER", "blocker_id values must use ascending Unicode code-point order"))
    for blocker_index, blocker in enumerate(blockers):
        field_path, code = blocker["field_path"], blocker["code"]
        match = re.fullmatch(r"/expectations/(0|[1-9][0-9]*)/assertions", field_path)
        valid = field_path in _BLOCKER_PATHS[code]
        if match:
            valid = int(match.group(1)) < len(step["expectations"]) and code in {"UNRESOLVED_ASSERTION", "UNSUPPORTED_ASSERTION_DIALECT", "UNCONFIRMED_TYPE"}
        if not valid:
            diagnostics.append(_diagnostic(_pointer(*step_path, "automation_blockers", blocker_index, "field_path"), "SEMANTIC_BLOCKER_PATH", "blocker field_path/code pair is invalid"))
    if step["operation"] is None and not step["manual_only"] and not any(item["field_path"] == "/operation" for item in blockers):
        diagnostics.append(_diagnostic(_pointer(*step_path, "operation"), "SEMANTIC_MISSING_OPERATION_BLOCKER", "blocked null operation requires an /operation blocker"))
    if not step["manual_only"] and blockers:
        for expectation_index, expectation in enumerate(step["expectations"]):
            if not expectation["assertions"] and not any(item["field_path"] == f"/expectations/{expectation_index}/assertions" for item in blockers):
                diagnostics.append(_diagnostic(_pointer(*step_path, "expectations", expectation_index, "assertions"), "SEMANTIC_MISSING_ASSERTION_BLOCKER", "empty blocked expectation requires its exact assertion blocker"))
        blocker_paths = {item["field_path"] for item in blockers}
        input_binding_is_omitted = _has_blocker_backed_http_input_omission(step)
        output_binding_is_omitted = "/outputs" in blocker_paths
        if (
            step["operation"] is not None
            and all(item["assertions"] for item in step["expectations"])
            and not input_binding_is_omitted
            and not output_binding_is_omitted
        ):
            diagnostics.append(_diagnostic(_pointer(*step_path, "automation_blockers"), "SEMANTIC_SPURIOUS_BLOCKER", "blockers must identify an absent or unconfirmed technical element"))


def _data_flow_targets_and_contracts(index: _DocumentIndex, case_index: int, step_index: int, step: dict[str, Any], diagnostics: list[dict[str, str]]) -> dict[str, Any] | None:
    step_path = ("test_cases", case_index, "steps", step_index)
    operation = step["operation"]
    operation_kind = operation.get("kind") if operation else None
    capability = index.capabilities.get(operation["capability_id"]) if operation_kind == "project_action" else None
    if operation_kind == "project_action" and capability is None:
        diagnostics.append(_diagnostic(_pointer(*step_path, "operation", "capability_id"), "SEMANTIC_UNKNOWN_CAPABILITY", "project_action must reference a declared capability"))
    effective_targets: set[tuple[str, str]] = set()
    body_targets: list[tuple[str, ...]] = []
    path_inputs: set[str] = set()
    arguments = _unique_items(capability["arguments"], "name") if capability else {}
    for input_index, binding in enumerate(step["inputs"]):
        input_path = (*step_path, "inputs", input_index)
        target, stored = binding["target"], binding["semantic_type"]
        resolved = _source_type(index, case_index, step_index, binding["source"], (*input_path, "source"), diagnostics)
        if resolved is not None and not _storage_equal(resolved, stored):
            diagnostics.append(_diagnostic(_pointer(*input_path, "semantic_type"), "SEMANTIC_TYPE_MISMATCH", "input semantic_type is incompatible with its resolved source"))
        location = target["location"]
        if operation_kind == "http":
            if location == "arg":
                diagnostics.append(_diagnostic(_pointer(*input_path, "target", "location"), "SEMANTIC_OPERATION_TARGET_KIND", "HTTP operation cannot bind capability arguments"))
            if location in {"path", "query", "header"}:
                key = (location, target["name"])
                if key in effective_targets:
                    diagnostics.append(_diagnostic(_pointer(*input_path, "target"), "SEMANTIC_DUPLICATE_TARGET", "each HTTP target has one binding"))
                effective_targets.add(key)
            if location == "path":
                path_inputs.add(target["name"])
            if location in {"path", "header"} and _representation(stored) != "string":
                diagnostics.append(_diagnostic(_pointer(*input_path, "semantic_type"), "SEMANTIC_TARGET_TYPE", f"{location} target requires string type"))
            if location == "query" and _representation(stored) not in {"null", "boolean", "integer", "number", "string"}:
                diagnostics.append(_diagnostic(_pointer(*input_path, "semantic_type"), "SEMANTIC_TARGET_TYPE", "query target requires a scalar type"))
            if location == "body":
                tokens = _rfc6901_tokens(target["pointer"])
                if any(tokens == prior or tokens[:len(prior)] == prior or prior[:len(tokens)] == tokens for prior in body_targets):
                    diagnostics.append(_diagnostic(_pointer(*input_path, "target", "pointer"), "SEMANTIC_BODY_TARGET_OVERLAP", "body pointers must not be equal or ancestor/descendant"))
                body_targets.append(tokens)
        elif operation_kind == "project_action":
            if location != "arg":
                diagnostics.append(_diagnostic(_pointer(*input_path, "target", "location"), "SEMANTIC_OPERATION_TARGET_KIND", "project_action can bind only capability arguments"))
            elif capability is not None:
                argument = arguments.get(target["name"])
                if argument is None:
                    diagnostics.append(_diagnostic(_pointer(*input_path, "target", "name"), "SEMANTIC_UNDECLARED_CAPABILITY_ARGUMENT", "input target is not a declared capability argument"))
                elif not _compatible(resolved or stored, argument["semantic_type"]):
                    diagnostics.append(_diagnostic(_pointer(*input_path, "semantic_type"), "SEMANTIC_CAPABILITY_TYPE_MISMATCH", "input type is incompatible with capability argument"))
    if operation_kind == "http" and not _has_blocker_backed_http_input_omission(step, path_inputs):
        placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}", operation["path"]))
        if placeholders != path_inputs:
            diagnostics.append(_diagnostic(_pointer(*step_path, "operation", "path"), "SEMANTIC_HTTP_PATH_BINDINGS", "path placeholders must exactly match path input names"))
    if operation_kind == "project_action" and capability is not None:
        bound = [item["target"]["name"] for item in step["inputs"] if item["target"]["location"] == "arg"]
        for argument in capability["arguments"]:
            if argument["required"] and bound.count(argument["name"]) != 1:
                diagnostics.append(_diagnostic(_pointer(*step_path, "inputs"), "SEMANTIC_REQUIRED_CAPABILITY_ARGUMENT", "each required capability argument needs exactly one binding"))
        if len(bound) != len(set(bound)):
            diagnostics.append(_diagnostic(_pointer(*step_path, "inputs"), "SEMANTIC_DUPLICATE_CAPABILITY_ARGUMENT", "capability argument may have at most one binding"))
    for output_index, output in enumerate(step["outputs"]):
        output_path, source, descriptor = (*step_path, "outputs", output_index), output["source"], output["semantic_type"]
        if operation_kind == "http":
            if source["kind"] == "project_result":
                diagnostics.append(_diagnostic(_pointer(*output_path, "source", "kind"), "SEMANTIC_OPERATION_OUTPUT_KIND", "HTTP operation cannot declare project_result"))
            elif source["kind"] == "http_status" and _representation(descriptor) != "integer":
                diagnostics.append(_diagnostic(_pointer(*output_path, "semantic_type"), "SEMANTIC_OUTPUT_TYPE", "http_status output requires integer type"))
            elif source["kind"] == "http_header" and _representation(descriptor) != "string":
                diagnostics.append(_diagnostic(_pointer(*output_path, "semantic_type"), "SEMANTIC_OUTPUT_TYPE", "http_header output requires string type"))
        elif operation_kind == "project_action":
            if source["kind"] != "project_result":
                diagnostics.append(_diagnostic(_pointer(*output_path, "source", "kind"), "SEMANTIC_OPERATION_OUTPUT_KIND", "project_action can declare only project_result"))
            elif capability is not None:
                result = _unique_items(capability["results"], "name").get(source["name"])
                if result is None:
                    diagnostics.append(_diagnostic(_pointer(*output_path, "source", "name"), "SEMANTIC_UNDECLARED_CAPABILITY_RESULT", "output source is not a declared capability result"))
                elif descriptor != result["semantic_type"]:
                    diagnostics.append(_diagnostic(_pointer(*output_path, "semantic_type"), "SEMANTIC_CAPABILITY_TYPE_MISMATCH", "output type must exactly match capability result"))
    return capability


def _assertion_matrix(index: _DocumentIndex, case_index: int, step_index: int, step: dict[str, Any], capability: dict[str, Any] | None, diagnostics: list[dict[str, str]]) -> None:
    step_path, operation = ("test_cases", case_index, "steps", step_index), step["operation"]
    operation_kind = operation.get("kind") if operation else None
    results = _unique_items(capability["results"], "name") if capability else {}
    for expectation_index, expectation in enumerate(step["expectations"]):
        for assertion_index, assertion in enumerate(expectation["assertions"]):
            assertion_path, actual, operator = (*step_path, "expectations", expectation_index, "assertions", assertion_index), assertion["actual"], assertion["operator"]
            kind = actual["kind"]
            if kind == "step_output":
                actual_type = _resolve_step_output(index, case_index, step_index, actual, (*assertion_path, "actual"), diagnostics)
            elif kind == "http_status":
                actual_type = _json_type(0)
            elif kind == "http_header":
                actual_type = _json_type("")
            elif kind == "http_body":
                actual_type = actual["semantic_type"]
            else:
                result = results.get(actual["name"])
                if result is None:
                    diagnostics.append(_diagnostic(_pointer(*assertion_path, "actual", "name"), "SEMANTIC_UNDECLARED_CAPABILITY_RESULT", "assertion actual is not a declared capability result"))
                    actual_type = None
                else:
                    actual_type = result["semantic_type"]
            if operation_kind == "http" and kind == "project_result":
                diagnostics.append(_diagnostic(_pointer(*assertion_path, "actual", "kind"), "SEMANTIC_OPERATION_ASSERTION_KIND", "HTTP operation cannot assert project_result"))
            if operation_kind == "project_action" and kind in {"http_status", "http_header", "http_body"}:
                diagnostics.append(_diagnostic(_pointer(*assertion_path, "actual", "kind"), "SEMANTIC_OPERATION_ASSERTION_KIND", "project_action cannot assert HTTP values"))
            expected = assertion.get("expected")
            expected_type = _source_type(index, case_index, step_index, expected, (*assertion_path, "expected"), diagnostics) if expected and expected["kind"] not in {"regex", "schema_ref"} else None
            actual_representation, expected_representation = _representation(actual_type or {}), _representation(expected_type or {})
            invalid = False
            if operator in {"equals", "not_equals"}:
                invalid = expected is None or expected["kind"] not in _VALUE_SOURCE_KINDS or not _compatible(expected_type or {}, actual_type or {})
            elif operator == "contains":
                invalid = actual_representation != "string" or expected is None or expected["kind"] not in _VALUE_SOURCE_KINDS or expected_representation != "string"
            elif operator == "matches":
                invalid = actual_representation != "string" or expected is None or expected["kind"] != "regex"
            elif operator in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}:
                invalid = actual_representation not in {"integer", "number"} or expected is None or expected["kind"] not in _VALUE_SOURCE_KINDS or expected_representation not in {"integer", "number"} or not _compatible(expected_type or {}, actual_type or {})
            elif operator == "length_equals":
                invalid = actual_representation not in {"string", "array", "object"} or expected is None or expected["kind"] != "literal" or _json_type(expected["value"]).get("type") != "integer" or expected["value"] < 0
            elif operator == "schema_matches":
                invalid = actual_representation not in {"object", "array"} or expected is None or expected["kind"] != "schema_ref"
            if invalid:
                diagnostics.append(_diagnostic(_pointer(*assertion_path), "SEMANTIC_OPERATOR_TYPE", "operator operands are incompatible"))


def _semantic_diagnostics(document: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    index = _build_index(document, diagnostics)
    _requirement_coverage(index, document, diagnostics)
    for case_index, case in enumerate(index.cases):
        for step_index, step in enumerate(case["steps"]):
            step_path = ("test_cases", case_index, "steps", step_index)
            _blocker_and_readiness(step, step_path, diagnostics)
            capability = _data_flow_targets_and_contracts(index, case_index, step_index, step, diagnostics)
            _assertion_matrix(index, case_index, step_index, step, capability, diagnostics)
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))


def validate_canonical_document(document: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic schema followed by relational diagnostics for V3 document."""
    diagnostics = schema_diagnostics(document, _CANONICAL_DOCUMENT_SCHEMA, _ROOT)
    if diagnostics:
        return diagnostics
    return _semantic_diagnostics(document)


def require_valid_canonical_document(document: dict[str, Any]) -> None:
    """Raise a stable diagnostic exception unless *document* is canonical and ready."""
    diagnostics = validate_canonical_document(document)
    if diagnostics:
        raise CanonicalDocumentError(diagnostics)


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """Serialize a bare document with the V3 canonical JSON representation."""
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def document_sha256(document: dict[str, Any]) -> str:
    """Return the prefixed SHA-256 identity of bare canonical-document bytes."""
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def load_canonical_document(path: Path) -> dict[str, Any]:
    """Strictly load and validate a structurally and physically canonical document."""
    document = load_json_strict(path)
    if not isinstance(document, dict):
        raise ValueError("canonical document must be a JSON object")
    diagnostics = validate_canonical_document(document)
    if diagnostics:
        first = diagnostics[0]
        raise ValueError(f"canonical document validation failed at {first['path']}: {first['code']}: {first['message']}")
    return document
