"""Socket-free, language-neutral ``http-binding-v1`` request semantics.

This module deliberately has no HTTP client or networking imports.  A caller supplies
the one-attempt transport; consequently request construction and observation remain
fully testable on a workstation where opening a socket is prohibited.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping, Protocol, Sequence

from tools.assertion_dsl import ValueState


_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9a-z-]+$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_RESERVED_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "content-type", "accept-encoding", "user-agent"})
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class AbstractRequest:
    method: str
    absolute_url: str
    ordered_headers: tuple[tuple[str, str], ...]
    body_bytes: bytes | None


@dataclass(frozen=True)
class RawResponse:
    status: int
    ordered_headers: tuple[tuple[str, str], ...]
    body_bytes: bytes


@dataclass(frozen=True)
class ExecutionError(Exception):
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.code} at {self.path}: {self.message}")


class Transport(Protocol):
    def send_once(self, request: AbstractRequest, timeout_seconds: float) -> RawResponse:
        """Perform exactly one already-constructed request attempt."""


def build_request(
    operation: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
    providers: Any,
    step_outputs: Any,
) -> AbstractRequest:
    """Build the exact abstract tuple, before any client call is possible."""
    _validate_static_contract(operation, inputs)
    _validate_static_literals(inputs)
    path_template = operation.get("path")
    base_source = operation.get("base_url_source")
    resolved_providers: dict[tuple[str, str], Any] = {}
    base = _resolve_provider(base_source, providers, "/base_url_source", resolved_providers)
    if not isinstance(base, str) or not base:
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be a non-empty ASCII origin.")
    _validate_base_url(base)

    # All external sources resolve before any step_output is read, regardless of
    # their physical input order.  This preserves the normative preflight phase.
    external_values: dict[int, Any] = {}
    for index, item in enumerate(inputs):
        source = item["source"]
        if source["kind"] in {"fixture", "environment", "secret_handle"}:
            external_values[index] = _resolve_provider(source, providers, f"/inputs/{index}/source", resolved_providers)
            _validate_source_value(external_values[index], item, index, preflight=True)
    path_values: dict[str, tuple[str, str]] = {}
    query_rows: list[tuple[str, Any]] = []
    headers: list[tuple[str, str]] = []
    body_rows: list[tuple[tuple[str, ...], Any, str]] = []
    seen_targets: set[tuple[str, str]] = set()
    for index, item in enumerate(inputs):
        item_path = f"/inputs/{index}"
        target, source = item["target"], item["source"]
        location = target.get("location")
        value = external_values[index] if index in external_values else _resolve_source(source, providers, step_outputs, item_path, resolved_providers)
        if index not in external_values:
            _validate_source_value(value, item, index, preflight=False)
        if location == "path":
            name = _target_name(target, item_path)
            _distinct_target(seen_targets, location, name, item_path)
            if not isinstance(value, str):
                _error("RUNTIME_TYPE_MISMATCH", f"{item_path}/source", "path target requires a string value.")
            path_values[name] = (value, item_path)
        elif location == "query":
            name = _target_name(target, item_path)
            _distinct_target(seen_targets, location, name, item_path)
            if not _is_scalar(value):
                _error("RUNTIME_TYPE_MISMATCH", f"{item_path}/source", "query target requires a JSON scalar value.")
            query_rows.append((name, value))
        elif location == "header":
            name = _target_name(target, item_path)
            _distinct_target(seen_targets, location, name, item_path)
            _validate_header_name(name, f"{item_path}/target/name")
            if not isinstance(value, str):
                _error("RUNTIME_TYPE_MISMATCH", f"{item_path}/source", "header target requires a string value.")
            _validate_header_value(value, f"{item_path}/source")
            headers.append((name, value))
        else:
            pointer = target.get("pointer")
            tokens = _pointer_tokens(pointer, f"{item_path}/target/pointer")
            body_rows.append((tokens, value, item_path))

    names_in_path = set(_PLACEHOLDER.findall(path_template))
    if names_in_path != set(path_values):
        _error("INVALID_PATH_BINDINGS", "/path", "Path placeholders must exactly match path input names.")
    rendered_path = _render_path(path_template, path_values)
    body = _build_body(body_rows)
    headers.extend((("accept-encoding", "identity"), ("user-agent", "test-skills-http-binding-v1")))
    if body is not None:
        headers.append(("content-type", "application/json"))
    query = "&".join(f"{_percent_encode(name)}={_percent_encode(_query_lexeme(value))}" for name, value in query_rows)
    return AbstractRequest(operation["method"], base + rendered_path + ("?" + query if query else ""), tuple(headers), body)


def execute_once(request: AbstractRequest, transport: Transport, timeout_seconds: float) -> RawResponse:
    """Cross the only transport seam once; no retries, redirects, or decoding exist here."""
    if not isinstance(request, AbstractRequest):
        _error("INVALID_REQUEST", "/request", "request must be an AbstractRequest.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        _error("INVALID_TIMEOUT", "/timeout_seconds", "timeout_seconds must be a finite positive number.")
    try:
        response = transport.send_once(request, float(timeout_seconds))
    except Exception:
        _error("TRANSPORT_ERROR", "/transport", "Transport did not produce a response.")
    if not isinstance(response, RawResponse):
        _error("INVALID_TRANSPORT_RESPONSE", "/transport", "Transport must return RawResponse.")
    return response


def observe_response(response: RawResponse, source: Mapping[str, Any]) -> ValueState:
    """Observe one raw response deterministically, preserving MISSING versus JSON null."""
    if not isinstance(response, RawResponse):
        _error("INVALID_RESPONSE", "/response", "response must be RawResponse.")
    if isinstance(response.status, bool) or not isinstance(response.status, int):
        _error("INVALID_RESPONSE", "/status", "Response status must be an integer.")
    if not isinstance(source, Mapping):
        _error("INVALID_RESPONSE_SOURCE", "/source", "Response source must be an object.")
    kind = source.get("kind")
    expected = {"kind"} if kind == "http_status" else {"kind", "name"} if kind == "http_header" else {"kind", "pointer"} if kind == "http_body" else set()
    if not expected or set(source) != expected:
        _error("INVALID_RESPONSE_SOURCE", "/source", "Response source must be an exact canonical object.")
    if kind == "http_status":
        return ValueState.value(response.status)
    if kind == "http_header":
        name = source.get("name")
        if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name):
            _error("INVALID_RESPONSE_SOURCE", "/source/name", "Header source name must be canonical lowercase RFC 9110 tchar.")
        fields = _header_fields(response, name)
        if not fields:
            return ValueState.missing()
        if len(fields) != 1:
            _error("DUPLICATE_RESPONSE_HEADER", f"/headers/{name.lower()}", "Response contains duplicate header fields.")
        return ValueState.value(fields[0].strip(" \t"))
    if kind != "http_body":
        _error("INVALID_RESPONSE_SOURCE", "/source/kind", "Response source kind is invalid.")
    pointer = source.get("pointer")
    tokens = _pointer_tokens(pointer, "/source/pointer")
    coding = _header_fields(response, "content-encoding")
    if coding and (len(coding) != 1 or coding[0].strip(" \t").lower() != "identity"):
        _error("UNSUPPORTED_CONTENT_ENCODING", "/headers/content-encoding", "Response body content encoding must be identity.")
    if not response.body_bytes:
        return ValueState.missing()
    value = _strict_response_json(response.body_bytes)
    for token in tokens:
        if isinstance(value, dict):
            if token not in value:
                return ValueState.missing()
            value = value[token]
        elif isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return ValueState.missing()
            number = int(token)
            if number >= len(value):
                return ValueState.missing()
            value = value[number]
        else:
            return ValueState.missing()
    return ValueState.value(value)


def validate_preflight_base_url(value: Any) -> None:
    """Validate one already-resolved HTTP origin without provider access."""
    if not isinstance(value, str) or not value:
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be a non-empty ASCII origin.")
    _validate_base_url(value)


def validate_preflight_input_value(value: Any, item: Mapping[str, Any], index: int) -> None:
    """Apply existing provider type and HTTP-target checks to one input."""
    _validate_source_value(value, item, index, preflight=True)


def validate_preflight_typed_value(value: Any, semantic_type: Mapping[str, Any], path: str) -> None:
    """Validate one provider expected value against its canonical descriptor."""
    _validate_descriptor(semantic_type, path)
    _validate_json_value(value, path)
    representation = semantic_type.get("type", semantic_type.get("representation"))
    actual = _representation(value)
    if actual != representation and not (actual == "integer" and representation == "number"):
        _error("PREFLIGHT_PROVIDER_TYPE_MISMATCH", path, "Resolved value does not match semantic_type.")


def _require_http_operation(operation: Mapping[str, Any]) -> None:
    if not isinstance(operation, Mapping) or operation.get("kind") != "http" or operation.get("binding_profile") != "http-binding-v1":
        _error("INVALID_HTTP_PROFILE", "/operation", "Operation must use http-binding-v1.")
    method = operation.get("method")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]*", method):
        _error("INVALID_METHOD", "/method", "HTTP method is invalid.")


def _validate_static_contract(operation: Any, inputs: Any) -> None:
    """Reject every non-canonical shape before provider or output access."""
    if not isinstance(operation, Mapping) or set(operation) != {"kind", "binding_profile", "base_url_source", "method", "path"}:
        _error("INVALID_OPERATION_SHAPE", "/operation", "Operation must be the exact canonical HTTP object.")
    _require_http_operation(operation)
    base = operation["base_url_source"]
    if not isinstance(base, Mapping) or set(base) != {"kind", "name", "provenance"} or base.get("kind") != "environment" or not _identifier(base.get("name")) or not _provenance(base.get("provenance")):
        _error("INVALID_BASE_URL_SOURCE", "/base_url_source", "base_url_source must be the exact canonical object.")
    if not isinstance(operation["path"], str):
        _error("INVALID_PATH_TEMPLATE", "/path", "HTTP path must be a string.")
    _validate_path_template(operation["path"])
    if not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)):
        _error("INVALID_INPUT", "/inputs", "inputs must be an ordered array.")
    path_names: set[str] = set()
    seen: set[tuple[str, str]] = set()
    body_paths: list[tuple[tuple[str, ...], int]] = []
    for index, item in enumerate(inputs):
        path = f"/inputs/{index}"
        if not isinstance(item, Mapping) or set(item) not in ({"input_id", "display_order", "target", "source", "semantic_type"}, {"input_id", "display_order", "target", "source", "semantic_type", "type_provenance"}):
            _error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.")
        if not isinstance(item.get("input_id"), str) or not re.fullmatch(r"INPUT-[A-Za-z0-9_.:-]+", item["input_id"]) or isinstance(item.get("display_order"), bool) or not isinstance(item.get("display_order"), int) or item["display_order"] != index + 1:
            _error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.")
        _validate_descriptor(item.get("semantic_type"), f"{path}/semantic_type")
        target, source = item.get("target"), item.get("source")
        if not isinstance(target, Mapping) or "sensitive" not in target:
            _error("INVALID_INPUT_SHAPE", path, "Input must be an exact canonical object.")
        _validate_target(target, f"{path}/target")
        _validate_source_shape(source, f"{path}/source")
        external = source["kind"] in {"fixture", "environment", "secret_handle"}
        if external != ("type_provenance" in item) or (external and not _provenance(item["type_provenance"])):
            _error("INVALID_INPUT_SHAPE", path, "type_provenance has the wrong conditional presence.")
        if target["sensitive"] != (source["kind"] == "secret_handle"):
            _error("INVALID_TARGET", f"{path}/target/sensitive", "sensitive must match source kind.")
        location = target["location"]
        if location == "arg":
            _error("INVALID_TARGET", f"{path}/target/location", "HTTP input cannot target a capability argument.")
        if location in {"path", "query", "header"}:
            name = target["name"]
            if (location, name) in seen:
                _error("DUPLICATE_TARGET", path, "Input target is duplicated.")
            seen.add((location, name))
            if location == "path":
                path_names.add(name)
            if location == "header":
                _validate_header_name(name, f"{path}/target/name")
        else:
            tokens = _pointer_tokens(target["pointer"], f"{path}/target/pointer")
            if any(tokens == prior or tokens[:len(prior)] == prior or prior[:len(tokens)] == tokens for prior, _ in body_paths):
                _error("OVERLAPPING_BODY_POINTER", f"{path}/target/pointer", "Body pointers may not overlap.")
            body_paths.append((tokens, index))
    if set(_PLACEHOLDER.findall(operation["path"])) != path_names:
        _error("INVALID_PATH_BINDINGS", "/path", "Path placeholders must exactly match path input names.")


def _validate_static_literals(inputs: Sequence[Mapping[str, Any]]) -> None:
    """Validate literal bindings before preflight callbacks can be observed."""
    for index, item in enumerate(inputs):
        source = item["source"]
        if source["kind"] != "literal":
            continue
        path = f"/inputs/{index}/source/value"
        _validate_json_value(source["value"], path)
        if item["target"]["location"] == "path" and source["value"] in {".", ".."}:
            _error("INVALID_PATH_VALUE", f"/inputs/{index}", "Rendered path must not contain dot segments.")
        if item["target"]["location"] == "header":
            value = source["value"]
            if not isinstance(value, str):
                _error("RUNTIME_TYPE_MISMATCH", f"/inputs/{index}/source", "header target requires a string value.")
            _validate_header_value(value, f"/inputs/{index}/source")
        _validate_source_value(source["value"], item, index, preflight=True)


def _validate_target(target: Any, path: str) -> None:
    if not isinstance(target, Mapping) or not isinstance(target.get("location"), str):
        _error("INVALID_TARGET", path, "Target must be a canonical object.")
    location = target["location"]
    expected = {"location", "name", "sensitive"} if location in {"path", "query", "header", "arg"} else {"location", "pointer", "sensitive"} if location == "body" else set()
    if not expected or set(target) != expected or not isinstance(target.get("sensitive"), bool):
        _error("INVALID_TARGET", path, "Target must be an exact canonical object.")
    if location == "body":
        return
    if location == "header":
        _validate_header_name(target["name"], f"{path}/name")
    valid_name = _identifier(target.get("name")) if location == "arg" else _nonempty(target.get("name"))
    if not valid_name:
        _error("INVALID_TARGET", f"{path}/name", "Target name is invalid.")


def _validate_source_shape(source: Any, path: str) -> None:
    if not isinstance(source, Mapping) or not isinstance(source.get("kind"), str):
        _error("INVALID_SOURCE", path, "Source must be a canonical object.")
    kind = source["kind"]
    expected = {"kind", "value"} if kind == "literal" else {"kind", "step_id", "output_id"} if kind == "step_output" else {"kind", "name"} if kind in {"fixture", "environment"} else {"kind", "handle", "safe_label"} if kind == "secret_handle" else set()
    if not expected or set(source) != expected:
        _error("INVALID_SOURCE", path, "Source must be an exact canonical object.")
    if kind == "step_output" and (not isinstance(source["step_id"], str) or not re.fullmatch(r"STEP-[A-Za-z0-9_.:-]+", source["step_id"]) or not _identifier(source["output_id"])):
        _error("INVALID_SOURCE", path, "step_output identifiers are invalid.")
    if kind in {"fixture", "environment"} and not _nonempty(source["name"]):
        _error("INVALID_SOURCE", path, "Provider name is invalid.")
    if kind == "secret_handle" and (not _nonempty(source["handle"]) or not _nonempty(source["safe_label"])):
        _error("INVALID_SOURCE", path, "Secret provider fields are invalid.")


def _validate_descriptor(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        _error("INVALID_SEMANTIC_TYPE", path, "semantic_type must be an exact descriptor.")
    if set(value) == {"kind", "type"} and value.get("kind") == "json" and value.get("type") in {"null", "boolean", "integer", "number", "string", "array", "object"}:
        return
    if set(value) == {"kind", "name", "representation"} and value.get("kind") == "named" and _identifier(value.get("name")) and value.get("representation") in {"null", "boolean", "integer", "number", "string", "array", "object"}:
        return
    _error("INVALID_SEMANTIC_TYPE", path, "semantic_type must be an exact descriptor.")


def _validate_source_value(value: Any, item: Mapping[str, Any], index: int, *, preflight: bool) -> None:
    path = f"/inputs/{index}/source"
    _validate_json_value(value, path)
    descriptor = item["semantic_type"]
    representation = descriptor.get("type", descriptor.get("representation"))
    actual = _representation(value)
    if actual != representation and not (actual == "integer" and representation == "number"):
        _error("PREFLIGHT_PROVIDER_TYPE_MISMATCH" if preflight and item["source"]["kind"] != "literal" else "RUNTIME_TYPE_MISMATCH", path, "Resolved value does not match semantic_type.")
    target = item["target"]
    target_code = "PREFLIGHT_PROVIDER_TARGET_ERROR" if preflight and item["source"]["kind"] != "literal" else "RUNTIME_STEP_OUTPUT_TARGET_ERROR" if not preflight and item["source"]["kind"] == "step_output" else "RUNTIME_TYPE_MISMATCH"
    target_message = "Resolved provider value is invalid for its HTTP target." if target_code == "PREFLIGHT_PROVIDER_TARGET_ERROR" else "Resolved step output is invalid for its HTTP target." if target_code == "RUNTIME_STEP_OUTPUT_TARGET_ERROR" else None
    if target["location"] == "path":
        if not isinstance(value, str): _error(target_code, path, target_message or "path target requires a string value.")
        if value in {".", ".."}:
            _error(target_code, path, target_message or "Rendered path must not contain dot segments.")
    elif target["location"] == "query" and not _is_scalar(value):
        _error(target_code, path, target_message or "query target requires a JSON scalar value.")
    elif target["location"] == "header":
        if not isinstance(value, str): _error(target_code, path, target_message or "header target requires a string value.")
        try:
            _validate_header_value(value, path)
        except ExecutionError:
            _error(target_code, path, target_message or "Header value must be ASCII without controls or outer space.")


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value))


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _provenance(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def _representation(value: Any) -> str:
    if value is None: return "null"
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, float): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, list): return "array"
    return "object"


def _resolve_source(source: Mapping[str, Any], providers: Any, step_outputs: Any, item_path: str, memo: dict[tuple[str, str], Any]) -> Any:
    kind = source.get("kind")
    if kind == "literal":
        if "value" not in source:
            _error("INVALID_LITERAL", f"{item_path}/source", "literal source requires value.")
        return source["value"]
    if kind == "step_output":
        step_id, output_id = source.get("step_id"), source.get("output_id")
        if not isinstance(step_id, str) or not isinstance(output_id, str):
            _error("INVALID_STEP_OUTPUT", f"{item_path}/source", "step_output requires step_id and output_id.")
        try:
            value = _step_output(step_outputs, step_id, output_id)
        except LookupError:
            _error("RUNTIME_STEP_OUTPUT_MISSING", f"{item_path}/source", "Referenced step output is missing.")
        if isinstance(value, ValueState):
            if value.is_missing:
                _error("RUNTIME_STEP_OUTPUT_MISSING", f"{item_path}/source", "Referenced step output is missing.")
            return value.data
        return value
    if kind in {"fixture", "environment", "secret_handle"}:
        return _resolve_provider(source, providers, f"{item_path}/source", memo)
    _error("INVALID_SOURCE", f"{item_path}/source/kind", "Input source kind is invalid.")


def _resolve_provider(source: Mapping[str, Any], providers: Any, path: str, memo: dict[tuple[str, str], Any]) -> Any:
    kind = source.get("kind")
    identifier = source.get("handle") if kind == "secret_handle" else source.get("name")
    if not isinstance(kind, str) or not isinstance(identifier, str) or not identifier:
        _error("INVALID_PROVIDER_SOURCE", path, "Provider source is invalid.")
    key = (kind, identifier)
    if key in memo:
        return memo[key]
    try:
        if not isinstance(providers, Mapping) or key not in providers:
            raise LookupError(identifier)
        value = providers[key]
        if callable(value):
            value = value()
    except LookupError:
        _error("PREFLIGHT_PROVIDER_MISSING", f"{path}/name" if "name" in source else path, "Required provider value is missing.")
    except Exception:
        _error("PREFLIGHT_PROVIDER_ERROR", path, "Required provider could not be resolved.")
    if isinstance(value, ValueState) and value.is_missing:
        _error("PREFLIGHT_PROVIDER_MISSING", f"{path}/name" if "name" in source else path, "Required provider value is missing.")
    value = value.data if isinstance(value, ValueState) else value
    memo[key] = value
    return value


def _step_output(outputs: Any, step_id: str, output_id: str) -> Any:
    if not isinstance(outputs, Mapping) or (step_id, output_id) not in outputs:
        raise LookupError()
    return outputs[(step_id, output_id)]


def _validate_base_url(value: str) -> None:
    if not value.isascii() or not re.fullmatch(r"https?://[^/?#\s]+", value):
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be an exact ASCII origin.")
    scheme, authority = value.split("://", 1)
    if scheme not in {"http", "https"} or "@" in authority or authority.count(":") > 1:
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL must be an exact ASCII origin.")
    host, sep, port = authority.partition(":")
    if not host or (sep and (not re.fullmatch(r"[1-9][0-9]{0,4}", port) or not 1 <= int(port) <= 65535)):
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL host or port is invalid.")
    labels = host.split(".")
    if len(labels) == 4 and all(label.isdecimal() for label in labels):
        if any(not re.fullmatch(r"0|[1-9][0-9]{0,2}", label) or int(label) > 255 for label in labels):
            _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL host or port is invalid.")
        return
    if len(host) > 253 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        _error("INVALID_BASE_URL", "/base_url_source/name", "Base URL DNS host is invalid.")


def _validate_path_template(path: str) -> None:
    if not path.startswith("/") or any(ord(char) < 0x21 or ord(char) == 0x7F or not char.isascii() or char == "\\" for char in path) or "?" in path or "#" in path:
        _error("INVALID_PATH_TEMPLATE", "/path", "Path template is invalid.")
    without_placeholders = _PLACEHOLDER.sub("", path)
    if "{" in without_placeholders or "}" in without_placeholders:
        _error("INVALID_PATH_TEMPLATE", "/path", "Path placeholder is invalid.")
    cursor = 0
    for match in _PLACEHOLDER.finditer(path):
        _validate_path_literal(path[cursor:match.start()])
        cursor = match.end()
    _validate_path_literal(path[cursor:])
    for segment in path.split("/"):
        if "{" not in segment and segment in {".", ".."}:
            _error("INVALID_PATH_TEMPLATE", "/path", "Literal dot path segments are forbidden.")


def _validate_path_literal(literal: str) -> None:
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@/%")
    if any(char not in allowed for char in literal) or re.search(r"%(?![0-9A-F]{2})", literal):
        _error("INVALID_PATH_TEMPLATE", "/path", "Path contains an invalid literal character.")


def _render_path(template: str, values: Mapping[str, tuple[str, str]]) -> str:
    first_owner: dict[str, str] = {}
    def replacement(match: re.Match[str]) -> str:
        value, owner = values[match.group(1)]
        first_owner.setdefault(match.group(1), owner)
        return _percent_encode(value)
    rendered = _PLACEHOLDER.sub(replacement, template)
    if any(segment in {".", ".."} for segment in rendered.split("/")):
        for name in _PLACEHOLDER.findall(template):
            value, owner = values[name]
            if value in {".", ".."}:
                _error("INVALID_PATH_VALUE", owner, "Rendered path must not contain dot segments.")
        _error("INVALID_PATH_VALUE", "/path", "Rendered path must not contain dot segments.")
    return rendered


def _target_name(target: Mapping[str, Any], item_path: str) -> str:
    name = target.get("name")
    if not isinstance(name, str) or not name:
        _error("INVALID_TARGET", f"{item_path}/target/name", "Target requires a non-empty name.")
    return name


def _distinct_target(seen: set[tuple[str, str]], location: str, name: str, item_path: str) -> None:
    key = (location, name)
    if key in seen:
        _error("DUPLICATE_TARGET", item_path, "Input target is duplicated.")
    seen.add(key)


def _validate_header_name(name: str, path: str) -> None:
    if not _HEADER_NAME.fullmatch(name) or name in _RESERVED_HEADERS:
        _error("INVALID_HEADER_NAME", path, "Header name must be lowercase, non-reserved RFC 9110 tchar.")


def _validate_header_value(value: str, path: str) -> None:
    if not value.isascii() or value != value.strip(" ") or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _error("INVALID_HEADER_VALUE", path, "Header value must be ASCII without controls or outer space.")


def _pointer_tokens(pointer: Any, path: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        _error("INVALID_JSON_POINTER", path, "JSON Pointer must be root or start with '/'.")
    if pointer == "":
        return ()
    tokens = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", raw):
            _error("INVALID_JSON_POINTER", path, "JSON Pointer has an invalid escape.")
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


def _build_body(rows: list[tuple[tuple[str, ...], Any, str]]) -> bytes | None:
    if not rows:
        return None
    roots = [row for row in rows if not row[0]]
    if roots and len(rows) != 1:
        _error("OVERLAPPING_BODY_POINTER", roots[0][2], "Root body pointer cannot overlap another body pointer.")
    if roots:
        value = roots[0][1]
    else:
        paths = [row[0] for row in rows]
        for index, path in enumerate(paths):
            if any(path == prior or path[:len(prior)] == prior or prior[:len(path)] == path for prior in paths[:index]):
                _error("OVERLAPPING_BODY_POINTER", rows[index][2], "Body pointers may not overlap.")
        value: dict[str, Any] = {}
        for tokens, resolved, _ in rows:
            current = value
            for token in tokens[:-1]:
                current = current.setdefault(token, {})
            current[tokens[-1]] = resolved
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        _error("INVALID_BODY_VALUE", "/inputs", "Body value is not canonical JSON.")


def _percent_encode(value: str) -> str:
    if not isinstance(value, str):
        _error("INVALID_STRING", "/inputs", "Percent-encoded value must be a string.")
    result = []
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _error("INVALID_JSON_VALUE", "/inputs", "String contains an unpaired surrogate.")
    for byte in value.encode("utf-8"):
        result.append(chr(byte) if byte in _UNRESERVED else f"%{byte:02X}")
    return "".join(result)


def _query_lexeme(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        _error("INVALID_QUERY_VALUE", "/inputs", "Query value is not a canonical JSON scalar.")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool, int, float)) and not isinstance(value, complex)


def _validate_json_value(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _error("INVALID_JSON_VALUE", path, "JSON number must be finite.")
    if isinstance(value, str) and any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _error("INVALID_JSON_VALUE", path, "String contains an unpaired surrogate.")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}/{index}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_value(item, f"{path}/{key}")
        if any(isinstance(key, str) and any(0xD800 <= ord(character) <= 0xDFFF for character in key) for key in value):
            _error("INVALID_JSON_VALUE", path, "String contains an unpaired surrogate.")
        if not all(isinstance(key, str) for key in value):
            _error("INVALID_JSON_VALUE", path, "JSON object keys must be strings.")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        _error("INVALID_JSON_VALUE", path, "Value is not JSON data.")


def _header_fields(response: RawResponse, name: str) -> list[str]:
    if not isinstance(response.ordered_headers, tuple):
        _error("INVALID_RESPONSE", "/headers", "Response ordered_headers must be a tuple of name/value pairs.")
    result = []
    for item in response.ordered_headers:
        if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(value, str) for value in item):
            _error("INVALID_RESPONSE", "/headers", "Response ordered_headers must be a tuple of name/value pairs.")
        if item[0].lower() == name.lower():
            result.append(item[1])
    return result


def _strict_response_json(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or raw.startswith(b"\xef\xbb\xbf"):
        _error("INVALID_RESPONSE_JSON", "/body", "Response body must be strict UTF-8 JSON.")
    def reject_constant(_: str) -> None:
        raise ValueError("non-finite JSON constant")
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
        _validate_finite_response_numbers(value)
        return value
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _error("INVALID_RESPONSE_JSON", "/body", "Response body must be strict UTF-8 JSON.")


def _validate_finite_response_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite response number")
    if isinstance(value, list):
        for item in value:
            _validate_finite_response_numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_finite_response_numbers(item)


def _error(code: str, path: str, message: str) -> None:
    raise ExecutionError(code, path, message)
