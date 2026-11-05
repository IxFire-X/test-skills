"""V0: no role grows past its budget unseen (the table is ``tests/contract/role_bytes.py``)."""
from __future__ import annotations

import pytest

from tests.contract.role_bytes import BUDGET, LIMIT

pytestmark = pytest.mark.contract
ROLES = ("autotest-reviewer", "context-marker", "orchestrate", "tc-generator", "tc-reviewer", "tc-to-autotest")


def _files(role: str) -> list[tuple[str, int]]:
    from tools.step_size import PACK, role_files

    return [(path.relative_to(PACK).as_posix(), len(path.read_bytes().replace(b"\r\n", b"\n"))) for path in role_files(role)]


def test_the_table_has_the_six_roles_and_a_margin_of_the_smaller_of_five_percent_and_three_kb() -> None:
    assert tuple(sorted(BUDGET)) == ROLES
    for role, (measured, limit) in BUDGET.items():
        assert limit - measured == min(measured * 5 // 100, 3000), (role, measured, limit)


@pytest.mark.parametrize("role", ROLES)
def test_role_bytes(role: str) -> None:
    files = _files(role)
    total = sum(size for _name, size in files)
    over = total - LIMIT[role]
    listing = "\n".join(f"  {size:>7}  {name}" for name, size in sorted(files, key=lambda row: -row[1]))
    assert over <= 0, (
        f"role {role}: {total} bytes, over its budget {LIMIT[role]} by {over}.\n{listing}\n"
        f"Make the texts of the role shorter, or raise BUDGET[{role!r}] in tests/contract/role_bytes.py and say in the commit why the role needs the bytes.")
