"""ARCHITECTURE.md §4 names every module of tools/ and only those: the map is edited in the commit that adds, moves or removes one."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[1]


def _on_the_map() -> set[str]:
    text = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    table = text.split("\n## 4.", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", table, re.M))


def _modules() -> set[str]:
    return {path.stem for path in (ROOT / "tools").glob("*.py")}


def test_every_module_is_on_the_map():
    assert sorted(_modules() - _on_the_map()) == [], "add a line to ARCHITECTURE.md §4"


def test_the_map_names_no_missing_module():
    assert sorted(_on_the_map() - _modules()) == [], "remove the line from ARCHITECTURE.md §4"
