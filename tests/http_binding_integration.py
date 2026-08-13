"""Optional local-loopback checks for the Python ``http-binding-v1`` adapter.

This module is intentionally not named ``test_*.py``: the mandatory conformance
suite must remain socket-free.  Importing it has no side effects; callers invoke
``run_python_integration`` explicitly.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.http_binding_v1 import AbstractRequest, ExecutionError, execute_once, observe_response


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load test module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_server_module = _load_module(
    "http_binding_v1_local_server",
    _ROOT / "fixtures" / "http-binding-v1" / "local_server.py",
)
_adapter_module = _load_module(
    "http_binding_v1_python_reference",
    _ROOT / "reference_adapters" / "python" / "http_binding_v1_reference.py",
)

LocalLoopbackServer = _server_module.LocalLoopbackServer
HttpBindingV1ReferenceTransport = _adapter_module.HttpBindingV1ReferenceTransport


def _request(origin: str, target: str, *, method: str = "GET", body: bytes | None = None) -> AbstractRequest:
    headers: list[tuple[str, str]] = [
        ("x-user", "alpha"),
        ("accept-encoding", "identity"),
        ("user-agent", "test-skills-http-binding-v1"),
    ]
    if body is not None:
        headers.append(("content-type", "application/json"))
    return AbstractRequest(method, origin + target, tuple(headers), body)


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _run_cases(server: Any) -> dict[str, Any]:
    transport = HttpBindingV1ReferenceTransport()
    body = b'{"name":"value"}'
    response = execute_once(_request(server.origin, "/echo?first=1&second=two", method="POST", body=body), transport, 1)
    _assert_equal(response.status, 200, "echo status")
    echo = server.records_for("/echo")
    _assert_equal(len(echo), 1, "echo attempts")
    record = echo[0]
    _assert_equal(record.method, "POST", "echo method")
    _assert_equal(record.target, "/echo?first=1&second=two", "echo target")
    _assert_equal(
        record.headers,
        (
            ("Host", server.authority),
            ("x-user", "alpha"),
            ("accept-encoding", "identity"),
            ("user-agent", "test-skills-http-binding-v1"),
            ("content-type", "application/json"),
            ("Content-Length", str(len(body))),
        ),
        "echo headers",
    )
    _assert_equal(record.body, body, "echo body")

    redirect = execute_once(_request(server.origin, "/redirect"), transport, 1)
    _assert_equal(redirect.status, 302, "redirect status")
    _assert_equal(server.count("/redirect"), 1, "redirect attempts")
    _assert_equal(server.count("/final"), 0, "redirect follow")
    _assert_equal(redirect.ordered_headers, (("Location", "/final"), ("Content-Length", "0")), "redirect headers")

    retry_503 = execute_once(_request(server.origin, "/retryable"), transport, 1)
    retry_429 = execute_once(_request(server.origin, "/retryable?status=429"), transport, 1)
    _assert_equal(retry_503.status, 503, "503 status")
    _assert_equal(retry_429.status, 429, "429 status")
    retryable = server.records_for("/retryable")
    _assert_equal(sum(item.target == "/retryable" for item in retryable), 1, "503 attempts")
    _assert_equal(sum(item.target == "/retryable?status=429" for item in retryable), 1, "429 attempts")

    gzip_response = execute_once(_request(server.origin, "/gzip"), transport, 1)
    _assert_equal(gzip_response.status, 200, "gzip status")
    _assert_equal(gzip_response.ordered_headers, (("Content-Encoding", "gzip"), ("Content-Length", str(len(gzip_response.body_bytes)))), "gzip headers")
    if gzip_response.body_bytes[:2] != b"\x1f\x8b":
        raise AssertionError("gzip body was decoded or is not raw gzip")
    try:
        observe_response(gzip_response, {"kind": "http_body", "pointer": ""})
    except ExecutionError as error:
        _assert_equal(error.code, "UNSUPPORTED_CONTENT_ENCODING", "gzip observation error")
    else:
        raise AssertionError("gzip observation unexpectedly succeeded")

    try:
        execute_once(_request(server.origin, "/close"), transport, 1)
    except ExecutionError as error:
        _assert_equal(error.code, "TRANSPORT_ERROR", "close transport error")
    else:
        raise AssertionError("forced close unexpectedly produced a response")
    _assert_equal(server.count("/close"), 1, "close attempts")

    for record in server.recorded_requests:
        names = {name.lower() for name, _ in record.headers}
        if "cookie" in names or "authorization" in names:
            raise AssertionError("adapter leaked cookie or authorization headers")
    return {"status": "PASS", "adapter": "python", "cases": 6}


def run_python_integration(server: Any | None = None) -> dict[str, Any]:
    """Run optional adapter checks, optionally against a caller-owned fixture."""
    if server is not None:
        try:
            return _run_cases(server)
        except AssertionError as error:
            return {"status": "FAIL", "adapter": "python", "reason": str(error)}

    server = LocalLoopbackServer()
    try:
        server.__enter__()
    except OSError:
        return {"status": "SKIPPED", "adapter": "python", "reason": "loopback_bind_unavailable"}
    try:
        try:
            return _run_cases(server)
        except AssertionError as error:
            return {"status": "FAIL", "adapter": "python", "reason": str(error)}
    finally:
        server.__exit__(None, None, None)


if __name__ == "__main__":
    result = run_python_integration()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if result["status"] == "FAIL":
        raise SystemExit(1)
