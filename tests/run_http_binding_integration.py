"""Public optional gate for the loopback HTTP binding reference adapters.

The runner owns exactly one fixture lifetime.  Both adapters receive its same
loopback origin; the mandatory conformance suite never imports this module.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from http_binding_integration import LocalLoopbackServer, run_python_integration
from run_java_contract import run as run_java_integration


_JAVA_CORPORA = (
    {"corpus": "portable-regex-v1", "count": 64, "status": "PASS"},
    {"corpus": "assertion-v1", "count": 23, "status": "PASS"},
    {"corpus": "http-request-v1", "count": 131, "status": "PASS"},
    {"corpus": "http-response-v1", "count": 47, "status": "PASS"},
    {"corpus": "http-phase-v1", "count": 40, "status": "PASS"},
)
_JAVA_CASES = (
    {"case_id": "echo", "status": "PASS"},
    {"case_id": "redirect", "status": "PASS"},
    {"case_id": "retryable", "status": "PASS"},
    {"case_id": "gzip", "status": "PASS"},
    {"case_id": "close", "status": "PASS"},
)
_APPLICATION_HEADERS = {
    "x-user": "alpha",
    "accept-encoding": "identity",
    "user-agent": "test-skills-http-binding-v1",
}
_JAVA_REQUESTS = {
    "/echo?first=1&second=two": ("POST", b'{"name":"value"}'),
    "/redirect": ("GET", b""),
    "/retryable": ("GET", b""),
    "/retryable?status=429": ("GET", b""),
    "/gzip": ("GET", b""),
    "/close": ("POST", b""),
}
# HTTP/1.1 framing and a possible cleartext HTTP/2 upgrade are protocol metadata,
# not application defaults.  All other observed fields are application headers.
_TRANSPORT_HEADERS = {
    "host",
    "content-length",
    "connection",
    "upgrade",
    "http2-settings",
    "transfer-encoding",
}


def _without_adapter(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "adapter"}


def _failure(adapter: str) -> dict[str, str]:
    return {"status": "FAIL", "code": adapter + "_integration_error"}


def _records_since(snapshot: tuple[Any, ...], records: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return an append-only fixture delta, rejecting a non-atomic observation."""
    if len(records) < len(snapshot) or records[: len(snapshot)] != snapshot:
        raise ValueError("fixture_records_changed_before_java_delta")
    return records[len(snapshot) :]


def _header_values(record: Any, name: str) -> tuple[str, ...]:
    lowered = name.lower()
    return tuple(value for header, value in record.headers if header.lower() == lowered)


def _validate_java_delta(records: tuple[Any, ...]) -> str | None:
    """Return the deterministic contract error for Java's fixture delta, if any."""
    expected_targets = set(_JAVA_REQUESTS)
    if len(records) != len(expected_targets):
        return "java_server_delta_request_count"

    targets = tuple(record.target for record in records)
    if set(targets) != expected_targets or len(set(targets)) != len(expected_targets):
        return "java_server_delta_targets"

    for record in records:
        endpoint = urlsplit(record.target).path
        if endpoint not in {"/echo", "/redirect", "/retryable", "/gzip", "/close"}:
            return "java_server_delta_endpoint"
        if (record.method, record.body) != _JAVA_REQUESTS[record.target]:
            return "java_server_delta_method_body"
        names = {name.lower() for name, _ in record.headers}
        if {"cookie", "authorization", "proxy-authorization"} & names:
            return "java_server_delta_sensitive_header"
        expected_headers = dict(_APPLICATION_HEADERS)
        if endpoint == "/echo":
            expected_headers["content-type"] = "application/json"
        application_items = tuple(
            (name.lower(), value)
            for name, value in record.headers
            if name.lower() not in _TRANSPORT_HEADERS
        )
        if len(application_items) != len(expected_headers) or dict(application_items) != expected_headers:
            return "java_server_delta_application_headers"

    echo = next(record for record in records if record.target == "/echo?first=1&second=two")
    required_headers = {**_APPLICATION_HEADERS, "content-type": "application/json"}
    for name, value in required_headers.items():
        if _header_values(echo, name) != (value,):
            return "java_server_delta_echo_headers"
    return None


def _validate_java_pass_report(report: dict[str, Any]) -> str | None:
    """Require the fixed Java corpus attestation retained by the master report."""
    expected = {
        "cases": list(_JAVA_CASES),
        "corpora": list(_JAVA_CORPORA),
        "protocol_headers": ["content-length"],
        "status": "PASS",
    }
    if report != expected:
        return "java_pass_report_contract"
    return None


def _java_adapter_report(server: Any) -> dict[str, Any]:
    """Run Java once and attest only to its append-only fixture activity."""
    try:
        snapshot = tuple(server.recorded_requests)
    except (AttributeError, TypeError):
        return {"status": "FAIL", "code": "java_server_delta_unavailable"}
    try:
        java_report, _ = run_java_integration(server.origin, False)
    except Exception:
        return _failure("java")
    try:
        delta = _records_since(snapshot, tuple(server.recorded_requests))
    except (AttributeError, TypeError, ValueError):
        return {"status": "FAIL", "code": "java_server_delta_unavailable"}

    if not isinstance(java_report, dict):
        return _failure("java")
    report = _without_adapter(java_report)
    if report.get("status") == "PASS":
        try:
            error = _validate_java_pass_report(report) or _validate_java_delta(delta)
        except (AttributeError, TypeError, ValueError):
            return {"status": "FAIL", "code": "java_server_delta_invalid"}
        if error is not None:
            return {"status": "FAIL", "code": error}
    elif report.get("status") == "SKIPPED" and delta:
        return {"status": "FAIL", "code": "java_server_delta_on_skip"}
    return report


def _result(adapters: dict[str, dict[str, Any]], require_integration: bool) -> tuple[dict[str, Any], int]:
    statuses = [report["status"] for report in adapters.values()]
    if any(status == "FAIL" for status in statuses):
        status = "FAIL"
    elif all(status == "PASS" for status in statuses):
        status = "PASS"
    else:
        status = "SKIPPED"

    report: dict[str, Any] = {"adapters": adapters, "status": status}
    if require_integration and status != "PASS":
        report["status"] = "FAIL"
        if any(value == "SKIPPED" for value in statuses):
            report["code"] = "INTEGRATION_REQUIRED"
        return report, 1
    return report, 1 if status == "FAIL" else 0


def run(require_integration: bool = False, python_only: bool = False) -> tuple[dict[str, Any], int]:
    """Run the adapters against one fixture and return the deterministic report."""
    server = LocalLoopbackServer()
    try:
        server.__enter__()
    except OSError:
        adapters: dict[str, dict[str, Any]] = {
            "python": {"status": "SKIPPED", "reason": "loopback_bind_unavailable"},
        }
        if not python_only:
            adapters["java"] = {"status": "SKIPPED", "reason": "loopback_bind_unavailable"}
        return _result(adapters, require_integration)
    except Exception:
        adapters = {"python": _failure("fixture")}
        if not python_only:
            adapters["java"] = _failure("fixture")
        return _result(adapters, require_integration)

    try:
        try:
            adapters = {"python": _without_adapter(run_python_integration(server))}
        except Exception:
            adapters = {"python": _failure("python")}
        if not python_only:
            adapters["java"] = _java_adapter_report(server)
        elif require_integration:
            adapters["java"] = {"status": "SKIPPED", "reason": "python_only"}
        return _result(adapters, require_integration)
    finally:
        server.__exit__(None, None, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-integration", action="store_true")
    parser.add_argument("--python-only", action="store_true")
    args = parser.parse_args(argv)
    report, code = run(args.require_integration, args.python_only)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
