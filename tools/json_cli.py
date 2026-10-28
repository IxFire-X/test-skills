"""Small shared argparse adapter for deterministic JSON command-line tools."""

from __future__ import annotations

import argparse
import json
import re
import sys


_CODE_PREFIX = re.compile(r"^([A-Z][A-Z0-9_]+)(?::|$)")
_FIX_TAIL = "; fix: "


def error_code_prefix(message: str) -> str | None:
    """The refusal code a message starts with (``CODE: …``), or None."""
    found = _CODE_PREFIX.match(message)
    return found.group(1) if found else None


def error_fields(error: BaseException) -> dict[str, str]:
    """FaT-U6: ``{code, message[, fix]}`` of a refusal — the error's own code, else the ``CODE:`` its message starts with,
    else its type; the fix is the error's own, else the ``; fix: …`` tail of the message.  The message stays whole: its
    readers look for the fix text in it, the separate field is for the code.  An OSError names its file — the agent's own
    input, not a secret (FaT-F1)."""
    message = str(error)
    if isinstance(error, OSError):
        message = f"{type(error).__name__}: {error.strerror or 'input/output error'}" + (f": {error.filename}" if error.filename else "")
    fix = getattr(error, "fix", None) or (message.split(_FIX_TAIL, 1)[1] if _FIX_TAIL in message else None)
    code = getattr(error, "code", None) or error_code_prefix(message) or type(error).__name__
    return {"code": str(code), "message": message, **({"fix": str(fix)} if fix else {})}


def emit_error(message: str) -> None:
    """Emit the one machine-readable response allowed for usage/input errors."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"status": "error", "errors": [{"path": "", "message": message}]}, ensure_ascii=False, sort_keys=True))


class JsonArgumentParser(argparse.ArgumentParser):
    """Argparse parser that never writes prose usage as the only stdout result."""

    def error(self, message: str) -> None:
        emit_error(f"argument error: {message}")
        raise SystemExit(2)
