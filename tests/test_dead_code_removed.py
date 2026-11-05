"""A4-1: code that no entry point of the package reaches does not come back to ``tools/``."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

TOOLS = Path(__file__).resolve().parents[1] / "tools"
REMOVED = frozenset((
    "render_zephyr_xml",  # the XML export is off; the CSV one is the export
    "atomic_write_confined_bytes",  # only the ``_at_root`` writer is used
    "generator_input_digest",  # the public wrapper had no caller; ``_generator_input_digest`` stays
    "run_phase_one_spine", "resume_phase_one_spine", "finalize_phase_one_spine",  # the tests build a run through tests/spine_helpers.py
))


def test_removed_functions_are_not_defined_in_tools() -> None:
    found = []
    for path in sorted(TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (node.name in REMOVED or (path.name == "test_case_projections.py" and node.name.startswith("_xml_"))):
                found.append(f"{path.name}:{node.name}")
    assert not found
