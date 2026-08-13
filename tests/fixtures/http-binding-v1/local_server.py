"""Deterministic loopback-only HTTP fixture for optional transport checks.

It is deliberately test code, never a development server.  The listener is pinned
to IPv4 loopback and selects an ephemeral port, so its routes cannot exercise an
external host.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Callable
from urllib.parse import urlsplit


LOOPBACK_HOST = "127.0.0.1"
_GZIP_BODY = gzip.compress(b'{"compressed":true}', mtime=0)


@dataclass(frozen=True)
class RecordedRequest:
    """One exact request received by the local fixture."""

    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


class _State:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: dict[str, int] = {}
        self._records: list[tuple[str, RecordedRequest]] = []

    def record(self, endpoint: str, request: RecordedRequest) -> None:
        with self._lock:
            self._counts[endpoint] = self._counts.get(endpoint, 0) + 1
            self._records.append((endpoint, request))

    def count(self, endpoint: str) -> int:
        with self._lock:
            return self._counts.get(endpoint, 0)

    def records_for(self, endpoint: str) -> tuple[RecordedRequest, ...]:
        with self._lock:
            return tuple(record for route, record in self._records if route == endpoint)

    def records(self) -> tuple[RecordedRequest, ...]:
        with self._lock:
            return tuple(record for _, record in self._records)


class LocalLoopbackServer(AbstractContextManager["LocalLoopbackServer"]):
    """Context-managed IPv4 loopback fixture with atomic observations."""

    def __init__(self, host: str = LOOPBACK_HOST) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("LocalLoopbackServer only permits 127.0.0.1")
        self._host = host
        self._state = _State()
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def origin(self) -> str:
        return "http://" + self.authority

    @property
    def authority(self) -> str:
        server = self._require_server()
        host, port = server.server_address[:2]
        if host != LOOPBACK_HOST:
            raise RuntimeError("fixture server is not bound to IPv4 loopback")
        return f"{host}:{port}"

    @property
    def recorded_requests(self) -> tuple[RecordedRequest, ...]:
        return self._state.records()

    @property
    def endpoint_counts(self) -> dict[str, int]:
        return {endpoint: self._state.count(endpoint) for endpoint in ("/echo", "/redirect", "/retryable", "/gzip", "/close", "/final")}

    def count(self, endpoint: str) -> int:
        return self._state.count(endpoint)

    def records_for(self, endpoint: str) -> tuple[RecordedRequest, ...]:
        return self._state.records_for(endpoint)

    def __enter__(self) -> "LocalLoopbackServer":
        if self._server is not None:
            raise RuntimeError("loopback fixture is already running")
        handler = _handler_for(self._state)
        server = ThreadingHTTPServer((self._host, 0), handler)
        if server.server_address[0] != LOOPBACK_HOST:
            server.server_close()
            raise RuntimeError("loopback fixture server bound to an unexpected host")
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self._server = server
        self._thread = thread
        thread.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    def _require_server(self) -> ThreadingHTTPServer:
        if self._server is None:
            raise RuntimeError("loopback fixture is not running")
        return self._server


def _handler_for(state: _State) -> type[BaseHTTPRequestHandler]:
    class LoopbackHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            """Keep optional test runs deterministic and silent."""

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

        def do_PATCH(self) -> None:
            self._handle()

        def do_DELETE(self) -> None:
            self._handle()

        def do_HEAD(self) -> None:
            self._handle()

        def _handle(self) -> None:
            target = self.path
            endpoint = urlsplit(target).path
            content_length = self.headers.get("Content-Length")
            length = int(content_length) if content_length is not None else 0
            request = RecordedRequest(
                method=self.command,
                target=target,
                headers=tuple(self.headers.raw_items()),
                body=self.rfile.read(length),
            )
            state.record(endpoint, request)
            if endpoint == "/close":
                self.close_connection = True
                return
            if endpoint == "/redirect":
                self._send(302, (("Location", "/final"),), b"")
                return
            if endpoint == "/retryable":
                status = 429 if urlsplit(target).query == "status=429" else 503
                self._send(status, (), b"")
                return
            if endpoint == "/gzip":
                self._send(200, (("Content-Encoding", "gzip"),), _GZIP_BODY)
                return
            if endpoint == "/final":
                self._send(200, (("Content-Type", "application/json"),), b'{"final":true}')
                return
            if endpoint == "/echo":
                self._send(200, (("Content-Type", "application/json"),), b'{"echo":true}')
                return
            self._send(404, (), b"")

        def _send(self, status: int, headers: tuple[tuple[str, str], ...], body: bytes) -> None:
            self.wfile.write(f"HTTP/1.1 {status} {_reason(status)}\r\n".encode("ascii"))
            for name, value in headers:
                self.wfile.write(f"{name}: {value}\r\n".encode("ascii"))
            self.wfile.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
            if self.command != "HEAD":
                self.wfile.write(body)

    return LoopbackHandler


def _reason(status: int) -> str:
    return {200: "OK", 302: "Found", 404: "Not Found", 429: "Too Many Requests", 503: "Service Unavailable"}[status]
