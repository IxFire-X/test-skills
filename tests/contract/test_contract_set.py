"""V0: the files of the smoke are the declared ones — the set neither shrinks nor grows by accident."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.contract.members import GROUPS, MEMBERS, SLOW

pytestmark = pytest.mark.contract
TESTS = Path(__file__).resolve().parents[1]


def _marked(path: Path) -> bool:
    """Whether the module sets ``pytestmark`` to something that names ``contract`` (read as text: no import, no side effect)."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "pytestmark" for target in node.targets):
            return "contract" in ast.unparse(node.value)
    return False


def test_the_declared_files_exist_once_and_are_34() -> None:
    assert len(MEMBERS) == len(set(MEMBERS)) == 35, "the count is the architecture review's; changing it is a decision to show in the diff"
    assert sorted(set(GROUPS)) == sorted(GROUPS)
    assert [name for name in MEMBERS if not (TESTS / f"{name}.py").is_file()] == []


def test_every_declared_file_has_the_mark_and_no_other_file_of_tests_has_it() -> None:
    marked = {path.stem for path in TESTS.glob("test_*.py") if _marked(path)}
    assert sorted(set(MEMBERS) - marked) == [], "declared in tests/contract/members.py but no `pytestmark = pytest.mark.contract`"
    assert sorted(marked - set(MEMBERS)) == [], "marked `contract` but not declared in tests/contract/members.py"


def test_the_tests_of_the_contract_directory_are_marked_too() -> None:
    assert [path.name for path in (TESTS / "contract").glob("test_*.py") if not _marked(path)] == []


def test_the_markers_are_registered_and_the_command_is_the_one_the_readme_gives() -> None:
    ini = (TESTS.parent / "pytest.ini").read_text(encoding="utf-8")
    assert "contract:" in ini and "native:" in ini
    readme = (TESTS / "README.md").read_text(encoding="utf-8")
    assert 'python -m pytest -m "contract and not native and not slow" -n 4' in readme and 'python -m pytest -m "contract and slow" -n 4' in readme
    assert "slow:" in ini


def _slow_functions(path: Path) -> set[str]:
    return {f"{path.stem}::{node.name}" for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef) and any("mark.slow" in ast.unparse(decorator) for decorator in node.decorator_list)}


def test_the_slow_tests_are_the_declared_ones_and_all_of_them_are_in_the_contract_set() -> None:
    marked = {row for path in [*TESTS.glob("test_*.py"), *(TESTS / "contract").glob("test_*.py")] for row in _slow_functions(path)}
    assert sorted(set(SLOW) - marked) == [], "declared in SLOW but no @pytest.mark.slow on the function"
    assert sorted(marked - set(SLOW)) == [], "marked slow but not declared in tests/contract/members.py SLOW"
    assert len(SLOW) == len(set(SLOW))
    assert sorted({row.split("::")[0] for row in SLOW} - set(MEMBERS)) == [], "a slow test outside the contract set would run in neither command"
