"""Wave 3 I: requirement keys and text digests (W3-Р1)."""
from __future__ import annotations

import gzip
import hashlib
import json
import random
from pathlib import Path

import pytest

from tools.build_context import build_context
from tools.requirement_identity import identify, rename_pairs, text_digest

ROOT = Path(__file__).resolve().parents[1]
PETCLINIC = ROOT / "evals" / "review-scaling" / "data" / "petclinic-requirements.md.gz"
PETCLINIC_PATTERN = r"[OPVX]\d\d\."


def entry(path: str, content: str) -> dict:
    return {"path": path, "content": content, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()}


def keyed(rows) -> dict:
    return {row["key"]: row for row in rows}


SECTIONS = {
    "login": "## Вход\nПользователь входит по паролю.\n",
    "logout": "## Выход\nКнопка выхода завершает сессию.\n",
    "req7": "REQ-7 Пароль не короче 8 символов.\n",
    "limits": "## Ограничения\n### Частота\nНе больше 5 попыток в минуту.\n",
}


def document(order) -> str:
    return "# Учётная запись\n\n" + "\n".join(SECTIONS[name] for name in order)


def test_identities_follow_the_sreq_rows_of_build_context() -> None:
    entries = [entry("docs/a.md", document(["login", "logout", "req7", "limits"])), entry("docs/b.md", "## Отчёт\nОтчёт строится за день.\n")]
    context = build_context(Path.cwd(), docs_snapshot=entries)
    rows = identify(entries)
    requirements = context["artifacts"]["analytics_documentation"]["requirements"]
    assert [row["source_requirement_id"] for row in rows] == [item["source_requirement_id"] for item in requirements]
    assert [row["text"] for row in rows] == [item["text"] for item in requirements]
    assert [row["key"] for row in rows] == [
        "md:docs/a.md#Учётная запись > Вход", "md:docs/a.md#Учётная запись > Выход", "md:docs/a.md#REQ-7",
        "md:docs/a.md#Учётная запись > Ограничения > Частота", "md:docs/b.md#Отчёт"]


@pytest.mark.parametrize("seed", range(5))
def test_insertion_and_permutation_keep_every_other_key_and_digest(seed: int) -> None:
    base = keyed(identify([entry("docs/a.md", document(["login", "logout", "req7", "limits"]))]))
    order = ["login", "logout", "req7", "limits"]
    random.Random(seed).shuffle(order)
    SECTIONS["new"] = "## Восстановление\nСсылка для сброса пароля живёт 1 час.\n"
    try:
        position = random.Random(seed + 100).randrange(len(order) + 1)
        changed = keyed(identify([entry("docs/a.md", document(order[:position] + ["new"] + order[position:]))]))
    finally:
        del SECTIONS["new"]
    assert set(changed) - set(base) == {"md:docs/a.md#Учётная запись > Восстановление"}
    for key, row in base.items():
        assert changed[key]["text_digest"] == row["text_digest"]


def test_renaming_a_heading_changes_only_its_key_and_keeps_its_digest() -> None:
    before = identify([entry("docs/a.md", document(["login", "logout", "req7"]))])
    renamed = document(["login", "logout", "req7"]).replace("## Выход", "## Завершение сеанса")
    after = identify([entry("docs/a.md", renamed)])
    assert set(keyed(before)) ^ set(keyed(after)) == {"md:docs/a.md#Учётная запись > Выход", "md:docs/a.md#Учётная запись > Завершение сеанса"}
    assert rename_pairs(before, after) == [("md:docs/a.md#Учётная запись > Выход", "md:docs/a.md#Учётная запись > Завершение сеанса")]


def test_editing_text_changes_only_its_own_digest() -> None:
    before = keyed(identify([entry("docs/a.md", document(["login", "logout", "req7", "limits"]))]))
    edited = document(["login", "logout", "req7", "limits"]).replace("8 символов", "10 символов")
    after = keyed(identify([entry("docs/a.md", edited)]))
    assert set(before) == set(after)
    assert [key for key in before if before[key]["text_digest"] != after[key]["text_digest"]] == ["md:docs/a.md#REQ-7"]
    # Whitespace and Unicode normalization do not count as an edit.
    assert text_digest("Пароль  не\nкороче") == text_digest("Пароль не короче")


def test_duplicate_heading_chains_get_ordinals() -> None:
    rows = identify([entry("docs/a.md", "# A\n## Шаг\nпервый\n## Шаг\nвторой\n")])
    assert [row["key"] for row in rows] == ["md:docs/a.md#A > Шаг", "md:docs/a.md#A > Шаг~2"]


def test_openspec_keys_follow_renamed_requirements() -> None:
    spec = "## Requirements\n\n### Requirement: Login\nThe system SHALL log in.\n\n#### Scenario: ok\n- WHEN x\n- THEN y\n"
    delta = "## RENAMED Requirements\n- FROM: `### Requirement: Login`\n- TO: `### Requirement: Sign in`\n"
    before = identify([entry("openspec/specs/auth/spec.md", spec)])
    after = identify([entry("openspec/specs/auth/spec.md", spec), entry("openspec/changes/rename/specs/auth/spec.md", delta)])
    assert [row["key"] for row in before] == ["openspec:auth#Login"]
    assert [row["key"] for row in after] == ["openspec:auth#Sign in"]
    assert after[0]["renamed_from"] == ["openspec:auth#Login"]
    assert rename_pairs(before, after) == [("openspec:auth#Login", "openspec:auth#Sign in")]


def test_project_id_pattern_cuts_petclinic_into_29_requirements(tmp_path: Path) -> None:
    content = gzip.decompress(PETCLINIC.read_bytes()).decode("utf-8")
    entries = [entry("docs/portable-owner-lifecycle-requirements.md", content)]
    assert len(build_context(tmp_path, docs_snapshot=entries)["artifacts"]["analytics_documentation"]["requirements"]) == 7
    (tmp_path / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.2.0", "version": "3.0", "project": {"name": "spring-petclinic"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "root", "root": ".", "stack": {"language": "java", "framework": "spring-boot", "build_tool": "maven"}, "detected_from": ["pom.xml"],
                     "paths": {"source": ["src/main/java"], "tests": ["src/test/java"], "resources": ["src/main/resources"]},
                     "test": {"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": "mvnw.cmd", "build_profile": "default", "adapter_parameters": {}}}],
        "requirements": {"id_pattern": PETCLINIC_PATTERN}}), encoding="utf-8")
    requirements = build_context(tmp_path, docs_snapshot=entries)["artifacts"]["analytics_documentation"]["requirements"]
    rows = identify(entries, id_pattern=PETCLINIC_PATTERN)
    assert len(requirements) == len(rows) == 29
    keys = [row["key"].split("#", 1)[1] for row in rows]
    explicit = [key for key in keys if key[:1] in "OPVX" and key[1:3].isdigit()]
    assert len(explicit) == 26 and explicit[0] == "O01" and explicit[-1] == "X02"
    assert [key for key in keys if key not in explicit] == [
        "Полный цикл работы с владельцем, питомцами и визитами > Назначение и границы",
        "Полный цикл работы с владельцем, питомцами и визитами > Данные",
        "Полный цикл работы с владельцем, питомцами и визитами > Условия работы над тестами (не отдельные пользовательские сценарии)"]
    assert [row["text"] for row in rows] == [item["text"] for item in requirements]


@pytest.mark.parametrize("pattern", ["", "(", ".*", "a|"])
def test_unusable_id_patterns_are_rejected(pattern: str) -> None:
    with pytest.raises(ValueError):
        identify([entry("docs/a.md", "## A\nтекст\n")], id_pattern=pattern)



def test_a_hash_line_inside_a_code_block_is_no_heading_of_the_chain() -> None:
    """Independent review 2.3: `# comment` in a fenced block must not reset the heading chain of the sections after it."""
    import hashlib

    from tools.requirement_identity import identify

    text = ("# API\n\n## Users\n\n### Create\nPOST /users creates a user.\n\n```bash\n# create a user\ncurl -X POST /users\n```\n\n"
            "### Delete\nDELETE /users/{id} deletes the user.\n")
    entry = {"path": "docs/api.md", "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), "content": text}
    keys = [row["key"] for row in identify([entry])]
    assert "md:docs/api.md#API > Users > Delete" in keys, keys
    assert "md:docs/api.md#API > Users > Create" in keys
