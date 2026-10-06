"""Interpreter-independent HTTP reason phrases for human-readable expectations.

``http.HTTPStatus`` changed its wording between Python 3.12 and 3.13, so a
document that validated on one interpreter was rejected on another.  The
validator owns this table instead; statuses renamed by RFC 9110 accept both
the current and the legacy wording.
"""
from __future__ import annotations

_PRIMARY: dict[int, str] = {
    100: "Continue", 101: "Switching Protocols", 102: "Processing", 103: "Early Hints",
    200: "OK", 201: "Created", 202: "Accepted", 203: "Non-Authoritative Information",
    204: "No Content", 205: "Reset Content", 206: "Partial Content", 207: "Multi-Status",
    208: "Already Reported", 226: "IM Used",
    300: "Multiple Choices", 301: "Moved Permanently", 302: "Found", 303: "See Other",
    304: "Not Modified", 305: "Use Proxy", 307: "Temporary Redirect", 308: "Permanent Redirect",
    400: "Bad Request", 401: "Unauthorized", 402: "Payment Required", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 406: "Not Acceptable",
    407: "Proxy Authentication Required", 408: "Request Timeout", 409: "Conflict", 410: "Gone",
    411: "Length Required", 412: "Precondition Failed", 413: "Content Too Large",
    414: "URI Too Long", 415: "Unsupported Media Type", 416: "Range Not Satisfiable",
    417: "Expectation Failed", 418: "I'm a Teapot", 421: "Misdirected Request",
    422: "Unprocessable Content", 423: "Locked", 424: "Failed Dependency", 425: "Too Early",
    426: "Upgrade Required", 428: "Precondition Required", 429: "Too Many Requests",
    431: "Request Header Fields Too Large", 451: "Unavailable For Legal Reasons",
    500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway",
    503: "Service Unavailable", 504: "Gateway Timeout", 505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates", 507: "Insufficient Storage", 508: "Loop Detected",
    510: "Not Extended", 511: "Network Authentication Required",
}
_LEGACY: dict[int, tuple[str, ...]] = {
    413: ("Request Entity Too Large", "Payload Too Large"),
    414: ("Request-URI Too Long",),
    416: ("Requested Range Not Satisfiable",),
    422: ("Unprocessable Entity",),
}


def accepted_reason_phrases(status: int) -> tuple[str, ...]:
    """Return every accepted phrase, preferred wording first; empty for an unknown status."""
    primary = _PRIMARY.get(status)
    return () if primary is None else (primary, *_LEGACY.get(status, ()))


def reason_phrase(status: int) -> str | None:
    return _PRIMARY.get(status)
