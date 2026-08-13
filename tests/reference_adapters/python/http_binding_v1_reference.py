"""Test-only one-attempt ``http-binding-v1`` transport using :mod:`http.client`.

The adapter is limited to the fixture's literal IPv4 loopback origin.  It has no
cookie jar, authenticator, redirect policy, retry loop, or response decompressor.
Production modules must not import this file.
"""

from __future__ import annotations

import http.client
from typing import Final
from urllib.parse import urlsplit

from tools.http_binding_v1 import AbstractRequest, RawResponse


_LOOPBACK_HOST: Final = "127.0.0.1"
_FORBIDDEN_CLIENT_HEADERS: Final = frozenset({"host", "content-length", "transfer-encoding", "connection"})


class _Http11Connection(http.client.HTTPConnection):
    _http_vsn = 11
    _http_vsn_str = "HTTP/1.1"


class HttpBindingV1ReferenceTransport:
    """Perform exactly one raw HTTP/1.1 request to the loopback fixture."""

    def send_once(self, request: AbstractRequest, timeout_seconds: float) -> RawResponse:
        parsed = urlsplit(request.absolute_url)
        if parsed.scheme != "http" or parsed.hostname != _LOOPBACK_HOST or parsed.username is not None or parsed.password is not None:
            raise OSError("test-only reference transport permits only http://127.0.0.1")
        try:
            port = parsed.port
        except ValueError as error:
            raise OSError("invalid loopback port") from error
        if port is None or not 1 <= port <= 65535 or parsed.fragment:
            raise OSError("test-only reference transport requires an explicit loopback port without a fragment")
        if not isinstance(request.body_bytes, (bytes, type(None))):
            raise OSError("request body must be bytes or None")
        connection = _Http11Connection(_LOOPBACK_HOST, port=port, timeout=timeout_seconds)
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.putrequest(request.method, target, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", parsed.netloc)
            for name, value in request.ordered_headers:
                if not isinstance(name, str) or not isinstance(value, str):
                    raise OSError("request headers must be string pairs")
                if name.lower() in _FORBIDDEN_CLIENT_HEADERS:
                    raise OSError(f"request header is client-managed: {name}")
                connection.putheader(name, value)
            if request.body_bytes is not None:
                connection.putheader("Content-Length", str(len(request.body_bytes)))
            connection.endheaders(request.body_bytes)
            response = connection.getresponse()
            return RawResponse(response.status, tuple(response.getheaders()), response.read())
        finally:
            connection.close()
