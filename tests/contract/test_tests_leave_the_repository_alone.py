"""V0 (A6-2): a test that damages a file damages a copy in ``tmp_path`` — never the repository.

Two tests of ``test_contract_registry`` wrote into the repository and put it back; under ``-n`` another worker read the damaged file.  The
guard reads every test file as text (no import) and refuses a function that writes, unlinks or creates something under a path built
from the root of the package (``pack_root``, ``ROOT``, ``PACK_ROOT``) — directly or through a name assigned from such a path."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
TESTS = Path(__file__).resolve().parents[1]
ROOT_NAMES = {"pack_root", "ROOT", "PACK_ROOT"}
WRITING = {"write_text", "write_bytes", "unlink", "mkdir", "touch", "rename", "replace", "rmdir"}


def _from_the_root(node: ast.AST, derived: set[str]) -> bool:
    while True:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"resolve", "with_name", "with_suffix"}:
            node = node.func.value
        elif isinstance(node, ast.Attribute) and node.attr in {"parent"}:
            node = node.value
        else:
            break
    return isinstance(node, ast.Name) and (node.id in ROOT_NAMES or node.id in derived)


def offenders(path: Path) -> list[str]:
    found = []
    for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        derived: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and _from_the_root(node.value, derived):
                derived.update(target.id for target in node.targets if isinstance(target, ast.Name))
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITING and _from_the_root(node.func.value, derived):
                found.append(f"{path.name}::{function.name}: .{node.func.attr}() on a path under the package root (line {node.lineno})")
    return found


def test_no_test_writes_under_the_root_of_the_package() -> None:
    problems = [row for path in sorted(TESTS.glob("test_*.py")) for row in offenders(path)]
    assert problems == [], "write into a copy in tmp_path (see _copy_of_the_pack in test_contract_registry):\n" + "\n".join(problems)


def test_the_guard_sees_the_shapes_it_was_written_for(tmp_path: Path) -> None:
    """A name assigned from ``pack_root / …`` that is written, a write straight on such a path — and a write into a copy, which is fine."""
    source = "".join(f"{line}" + chr(10) for line in (
        "def a(pack_root):", "    target = pack_root / 'x'", "    target.write_text('s')",
        "def b(pack_root):", "    (pack_root / 'y').write_bytes(b'')",
        "def c(pack_root, tmp_path):", "    copy = tmp_path / 'p'", "    (copy / 'x').write_text('s')"))
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    assert [row.split("::")[1].split(":")[0] for row in offenders(probe)] == ["a", "b"]
