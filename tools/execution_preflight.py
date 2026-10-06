"""Socket- and subprocess-free global readiness validation for canonical execution."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from tools.assertion_dsl import ValueState
from tools.canonical_document import validate_canonical_document
from tools.http_binding_v1 import ExecutionError, validate_preflight_base_url, validate_preflight_input_value, validate_preflight_typed_value


class ProviderResolver(Protocol):
    def resolve(self, kind: Literal["fixture", "environment", "secret_handle"], name: str) -> Any: ...


class AdapterRegistry(Protocol):
    def require(self, adapter: str, action: str) -> None: ...


@dataclass(frozen=True)
class PreflightResult:
    status: Literal["READY", "NOT_RUNNABLE"]
    resolved_values: Mapping[tuple[str, str], Any]
    diagnostics: tuple[Mapping[str, str], ...]


class EnvironmentOnlyProviderResolver:
    def __init__(self, environment: Mapping[str, str]):
        self._environment = environment

    def resolve(self, kind: Literal["fixture", "environment", "secret_handle"], name: str) -> Any:
        if kind != "environment" or name not in self._environment:
            raise LookupError()
        return self._environment[name]


class RejectingAdapterRegistry:
    def require(self, adapter: str, action: str) -> None:
        raise LookupError()


@dataclass(frozen=True)
class ExactAdapterRegistry:
    """Immutable, case-sensitive local allowlist for reviewed project actions."""

    allowed_pairs: frozenset[tuple[str, str]]

    def __init__(self, allowed_pairs: Any = ()) -> None:
        object.__setattr__(self, "allowed_pairs", frozenset(allowed_pairs))

    def require(self, adapter: str, action: str) -> None:
        if (adapter, action) not in self.allowed_pairs:
            raise LookupError()


class _ReviewedExactAdapterRegistry(ExactAdapterRegistry):
    """Private execution marker issued only after the full reviewed-source check."""

    def __init__(self, allowed_pairs: Any = (), *, _marker: object) -> None:
        if _marker is not _REVIEWED_REGISTRY_MARKER:
            raise ValueError("REVIEWED_ADAPTER_REGISTRY")
        super().__init__(allowed_pairs)


_REVIEWED_REGISTRY_MARKER = object()


def is_reviewed_adapter_registry(value: Any) -> bool:
    """True only for the immutable capability returned by the review factory."""
    return isinstance(value, _ReviewedExactAdapterRegistry)


def reviewed_adapter_registry(document: Mapping[str, Any], automation: Mapping[str, Any], review: Mapping[str, Any], *, host_isolation_receipt: Mapping[str, Any] | None = None) -> ExactAdapterRegistry:
    """Authorize only reviewed symbols that completely implement project actions."""
    from tools.automation_validation import validate_accepted_autotest_review

    diagnostics = validate_accepted_autotest_review(review, automation, dict(document), host_isolation_receipt=host_isolation_receipt)
    if diagnostics:
        raise ValueError("REVIEWED_ADAPTER_REGISTRY")
    reviewed_pairs = {
        (row["file_id"], row["symbol_id"])
        for row in review["artifacts"]["autotest_review"]["reviewed_symbol_pairs"]
    }
    # A blocker blocks its whole case, so ready steps of a blocked case are not automated either.
    operations = {
        (case["case_id"], step["step_id"]): step["operation"]
        for case in document["test_cases"]
        if not any(row["automation_blockers"] for row in case["steps"])
        for step in case["steps"]
        if not step["manual_only"] and step["operation"]["kind"] == "project_action"
    }
    covered = {
        (row["case_id"], row["step_id"])
        for row in automation["artifacts"]["implementation_relations"]
        if row["kind"] == "operation"
        and (row["case_id"], row["step_id"]) in operations
        and (row["file_id"], row["symbol_id"]) in reviewed_pairs
    }
    if covered != set(operations):
        raise ValueError("REVIEWED_ADAPTER_REGISTRY")
    capabilities = {row["capability_id"]: row for row in document["operation_capabilities"]}
    return _ReviewedExactAdapterRegistry(
        (
            (capabilities[operation["capability_id"]]["adapter"], capabilities[operation["capability_id"]]["action"])
            for operation in operations.values()
        ),
        _marker=_REVIEWED_REGISTRY_MARKER,
    )


@dataclass(frozen=True)
class _ProviderUse:
    key: tuple[str, str]
    path: str
    kind: Literal["base", "input", "expected"]
    item: Mapping[str, Any] | None = None
    input_index: int | None = None
    semantic_type: Mapping[str, Any] | None = None


_UNRESOLVED = object()


def preflight_execution(document: Any, provider_resolver: ProviderResolver, adapter_registry: AdapterRegistry) -> PreflightResult:
    """Resolve external readiness once, validate every physical use, and never execute."""
    canonical_diagnostics = validate_canonical_document(document)
    if canonical_diagnostics:
        return _result("NOT_RUNNABLE", (), _safe_canonical_diagnostics(canonical_diagnostics))
    provider_uses, adapter_uses = _collect_uses(document)
    values: dict[tuple[str, str], Any] = {}
    diagnostics: list[dict[str, str]] = []
    for key, path in _first_paths(provider_uses).items():
        value = _resolve_provider(provider_resolver, key, path, diagnostics)
        if value is not _UNRESOLVED:
            values[key] = value
    for pair, path in _first_paths(adapter_uses).items():
        _require_adapter(adapter_registry, pair, path, diagnostics)
    for use in provider_uses:
        if use.key not in values:
            continue
        try:
            if use.kind == "base":
                validate_preflight_base_url(values[use.key])
            elif use.kind == "input":
                assert use.item is not None and use.input_index is not None
                validate_preflight_input_value(values[use.key], use.item, use.input_index)
            else:
                assert use.semantic_type is not None
                validate_preflight_typed_value(values[use.key], use.semantic_type, use.path)
        except ExecutionError as error:
            diagnostics.append({"path": _rebase(error.path, use), "code": error.code, "message": error.message})
    return _result("NOT_RUNNABLE", (), diagnostics) if diagnostics else _result("READY", values.items(), ())


def _collect_uses(document: Mapping[str, Any]) -> tuple[list[_ProviderUse], list[tuple[tuple[str, str], str]]]:
    providers: list[_ProviderUse] = []
    adapters: list[tuple[tuple[str, str], str]] = []
    capabilities = {capability["capability_id"]: capability for capability in document["operation_capabilities"]}
    for case_index, case in enumerate(document["test_cases"]):
        if any(step["automation_blockers"] for step in case["steps"]):
            continue  # a blocked case is not automated, so none of its steps needs providers
        for step_index, step in enumerate(case["steps"]):
            if step["manual_only"]:
                continue
            step_path = f"/test_cases/{case_index}/steps/{step_index}"
            operation = step["operation"]
            if operation["kind"] == "http":
                source = operation["base_url_source"]
                providers.append(_ProviderUse(("environment", source["name"]), f"{step_path}/operation/base_url_source", "base"))
            else:
                capability = capabilities[operation["capability_id"]]
                adapters.append(((capability["adapter"], capability["action"]), f"{step_path}/operation"))
            for input_index, item in enumerate(step["inputs"]):
                key = _provider_key(item["source"])
                if key is not None:
                    providers.append(_ProviderUse(key, f"{step_path}/inputs/{input_index}/source", "input", item, input_index))
            for expectation_index, expectation in enumerate(step["expectations"]):
                for assertion_index, assertion in enumerate(expectation["assertions"]):
                    expected = assertion.get("expected")
                    if isinstance(expected, Mapping):
                        key = _provider_key(expected)
                        if key is not None:
                            providers.append(_ProviderUse(key, f"{step_path}/expectations/{expectation_index}/assertions/{assertion_index}/expected", "expected", semantic_type=expected["semantic_type"]))
    return providers, adapters


def _provider_key(source: Mapping[str, Any]) -> tuple[str, str] | None:
    kind = source["kind"]
    if kind not in {"fixture", "environment", "secret_handle"}:
        return None
    return kind, source["handle"] if kind == "secret_handle" else source["name"]


def _first_paths(uses: list[Any]) -> dict[Any, str]:
    result: dict[Any, str] = {}
    for use in uses:
        key, path = (use.key, use.path) if isinstance(use, _ProviderUse) else use
        result.setdefault(key, path)
    return result


def _resolve_provider(resolver: ProviderResolver, key: tuple[str, str], path: str, diagnostics: list[dict[str, str]]) -> Any:
    try:
        value = resolver.resolve(key[0], key[1])
    except LookupError:
        diagnostics.append({"path": path, "code": "PREFLIGHT_PROVIDER_MISSING", "message": "Required provider value is missing."})
        return _UNRESOLVED
    except PermissionError:
        diagnostics.append({"path": path, "code": "PREFLIGHT_PROVIDER_INACCESSIBLE", "message": "Required provider value is inaccessible."})
        return _UNRESOLVED
    except Exception:
        diagnostics.append({"path": path, "code": "PREFLIGHT_PROVIDER_ERROR", "message": "Required provider could not be resolved."})
        return _UNRESOLVED
    if isinstance(value, ValueState):
        if value.is_missing:
            diagnostics.append({"path": path, "code": "PREFLIGHT_PROVIDER_MISSING", "message": "Required provider value is missing."})
            return _UNRESOLVED
        value = value.data
    if isinstance(value, str) and value == "":
        diagnostics.append({"path": path, "code": "PREFLIGHT_PROVIDER_EMPTY", "message": "Required provider value is empty."})
        return _UNRESOLVED
    return value


def _require_adapter(registry: AdapterRegistry, pair: tuple[str, str], path: str, diagnostics: list[dict[str, str]]) -> None:
    try:
        registry.require(*pair)
    except LookupError:
        diagnostics.append({"path": path, "code": "PREFLIGHT_ADAPTER_UNAVAILABLE", "message": "Required project adapter is unavailable."})
    except PermissionError:
        diagnostics.append({"path": path, "code": "PREFLIGHT_ADAPTER_INACCESSIBLE", "message": "Required project adapter is inaccessible."})
    except Exception:
        diagnostics.append({"path": path, "code": "PREFLIGHT_ADAPTER_ERROR", "message": "Required project adapter could not be checked."})


def _rebase(local_path: str, use: _ProviderUse) -> str:
    return use.path


def _safe_canonical_diagnostics(diagnostics: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"path": row["path"], "code": row["code"], "message": "Canonical document is invalid."} for row in diagnostics]


def _result(status: Literal["READY", "NOT_RUNNABLE"], values: Any, diagnostics: Any) -> PreflightResult:
    rows = tuple(MappingProxyType(dict(row)) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"])))
    return PreflightResult(status, MappingProxyType(dict(values)), rows)
