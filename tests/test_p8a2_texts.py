"""P8a-2 (security review 79d4052): the permissions each SKILL declares, the paragraph about outside documentation, the
placeholder text for a secret in a CSV, one Java project in the example ``.skillsrc``.  Declarations are evidence, not a grant:
the checks below tie them to what the code of the package can do."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
PERMISSION_KEYS = ["read", "write", "network", "external_delivery", "dependencies", "rollback"]
_SCALAR = re.compile(r'^(?:"([^"\]*)"|([^"\s#:][^#]*?))$')


def frontmatter(path: Path) -> dict:
    """A strict reader for the one shape the SKILLs use: ``name`` and ``description`` first, then one ``permissions:`` block of
    two-space ``key: "value"`` lines.  Anything else — a tab, a repeated key, a stray line — is an error."""
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    result: dict = {}
    block: dict | None = None
    for line in lines[1:end]:
        assert "\t" not in line and line == line.rstrip(), (path, line)
        if line.startswith("  "):
            assert block is not None, (path, line)
            key, _, value = line[2:].partition(": ")
            assert re.fullmatch(r"[a-z_]+", key) and key not in block and _SCALAR.match(value), (path, line)
            block[key] = value.strip('"')
            continue
        key, _, value = line.partition(":")
        assert re.fullmatch(r"[a-z_]+", key) and key not in result, (path, line)
        if value == "":
            block = result[key] = {}
        else:
            block = None
            result[key] = value.strip()
    return result


@pytest.mark.parametrize("path", SKILLS, ids=lambda path: path.parent.name)
def test_every_skill_declares_its_permissions_in_one_key_after_name_and_description(path: Path) -> None:
    data = frontmatter(path)
    assert list(data) == ["name", "description", "permissions"], path
    assert data["name"] == path.parent.name and data["description"]
    assert list(data["permissions"]) == PERMISSION_KEYS and all(data["permissions"].values())
    assert len(SKILLS) == 6


def test_the_declared_network_of_every_skill_is_none() -> None:
    for path in SKILLS:
        permissions = frontmatter(path)["permissions"]
        if path.parent.name != "orchestrate":
            assert permissions["network"] == "none" and permissions["external_delivery"] == "none" and permissions["dependencies"] == "none", path
        else:
            assert permissions["network"] == "нет" and permissions["external_delivery"] == "нет" and "pip агент не запускает" in permissions["dependencies"]
            assert "каталог публикации: <run>/published" in permissions["write"]


ROLE_BLOCK_LIMIT = 230  # bytes: a role file is counted whole into the step input (step_size), the campaign budget test has ~300 B of slack


def _block_bytes(path: Path) -> int:
    raw = path.read_bytes().replace(b""+bytes([13,10]), bytes([10]))
    start = raw.index(b"permissions:")
    return len(raw[start:raw.index(bytes([10]) + b"---", start)])


@pytest.mark.parametrize("path", [path for path in SKILLS if path.parent.name != "orchestrate"], ids=lambda path: path.parent.name)
def test_the_permissions_block_of_a_role_stays_small_because_the_whole_file_is_model_input(path: Path) -> None:
    assert _block_bytes(path) <= ROLE_BLOCK_LIMIT, (path, _block_bytes(path))


def test_the_skill_does_not_send_the_agent_for_outside_documentation() -> None:
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "retrieval" not in text and "выдержки" not in text and "узкие выдержк" not in text
    assert "Документацию внешних зависимостей сам не ищи и в дерево проекта не сохраняй" in text


def test_the_skill_says_run_root_goes_with_out() -> None:
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "вместе с `--run-root <run>`" in text


def test_a_secret_in_a_csv_gets_a_placeholder_not_an_environment_variable() -> None:
    from tools.fixes import FIXES

    fix = FIXES["IMPORT_CSV_SECRET"]
    assert "плейсхолдером" in fix and "<" in fix and "переменн" not in fix and "окружения" not in fix


def test_the_example_skillsrc_is_one_java_project_and_valid() -> None:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    text = (ROOT / ".skillsrc.example").read_text(encoding="utf-8")
    document = parse_skillsrc_bytes(text.encode("utf-8"))
    assert [module["stack"]["language"] for module in document["modules"]] == ["java"]
    assert "typescript" not in text.lower() and "npm" not in text.lower() and "frontend" not in text.lower()
