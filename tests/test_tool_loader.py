"""Regression coverage for dynamic tool imports outside the pack cwd."""

import sys
from pathlib import Path

from conftest import ROOT, load_tool


def test_load_tool_uses_pack_location_not_current_working_directory(monkeypatch):
    """Catches tools/json_cli imports failing when pytest starts one directory above pack."""
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry and ROOT.resolve() != Path(entry).resolve()])

    module = load_tool("contract_check")

    assert module.__name__ == "contract_check"
