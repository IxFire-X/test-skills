"""Small shared argparse adapter for deterministic JSON command-line tools."""

from __future__ import annotations

import argparse
import json
import sys


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
