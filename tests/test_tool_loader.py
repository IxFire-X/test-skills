"""Regression coverage for dynamic tool imports outside the pack cwd."""

import sys
import types
from pathlib import Path

from conftest import ROOT, load_tool


def test_load_tool_scopes_local_helpers_without_leaking_import_state(monkeypatch):
    """Catches parent-cwd imports resolving a preloaded host json_cli or mutating sys.path."""
    sentinel = type("SentinelParser", (), {})
    fake = types.ModuleType("json_cli")
    fake.JsonArgumentParser = sentinel
    monkeypatch.chdir(ROOT.parent)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and Path(entry).resolve() not in {ROOT.resolve(), (ROOT / "tools").resolve()}])
    monkeypatch.setitem(sys.modules, "json_cli", fake)
    before_path = list(sys.path)

    module = load_tool("contract_check")

    assert module.__name__ == "contract_check"
    assert module.JsonArgumentParser is not sentinel
    assert sys.path == before_path
    assert sys.modules["json_cli"] is fake
