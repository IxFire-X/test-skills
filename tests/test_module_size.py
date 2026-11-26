"""The size of a module of ``tools/`` (W2.4 of the external review): at most ``LIMIT`` lines; the files already above it are listed with
their length, and a number here only goes down.

A package that grows a listed file either moves code out or raises its number with the lead's OK, a line in its delivery
«ceiling X: N → M, because …».  A package that shrinks a file by more than ``SLACK`` lines lowers its number; a file at or below
``LIMIT`` leaves the list.  Quick to run before a delivery: ``python -m pytest -q tests/test_module_size.py``."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
TOOLS = Path(__file__).resolve().parents[1] / "tools"

LIMIT = 1_200
SLACK = 50  # how far a number may stay above its file before the package that shrank the file lowers it
CEILINGS = {  # the files above LIMIT on 07018d64, each at its length then (lines, not newlines: batch_assembly has no last one)
    "orchestrate_test_case_revision.py": 1753, "run_report.py": 1725, "project_inventory.py": 1644, "model_inputs.py": 1634,
    "run_tests.py": 1626, "finalize_attempt.py": 1455, "batch_assembly.py": 1414, "run_journal.py": 1363, "run_scan.py": 1299,
    "execution_journal.py": 1304,
}
FACADE = {"pilot_state.py": 400}  # W2: the facade of the journal stays small


def _lengths() -> dict[str, int]:
    return {path.name: len(path.read_text(encoding="utf-8").splitlines()) for path in sorted(TOOLS.glob("*.py"))}


def test_a_module_is_at_most_the_limit() -> None:
    assert {name: size for name, size in _lengths().items() if name not in CEILINGS and size > LIMIT} == {}


def test_a_listed_module_does_not_grow() -> None:
    lengths = _lengths()
    assert {name: (lengths[name], ceiling) for name, ceiling in CEILINGS.items() if lengths.get(name, 0) > ceiling} == {}


def test_a_ceiling_follows_the_module_down() -> None:
    lengths = _lengths()
    assert {name: (lengths.get(name, 0), ceiling) for name, ceiling in CEILINGS.items() if ceiling - lengths.get(name, 0) > SLACK} == {}


def test_a_module_at_the_limit_leaves_the_list() -> None:
    lengths = _lengths()
    assert [name for name in CEILINGS if lengths.get(name, 0) <= LIMIT] == []


def test_the_facade_stays_small() -> None:
    lengths = _lengths()
    assert {name: lengths[name] for name, ceiling in FACADE.items() if lengths[name] > ceiling} == {}
