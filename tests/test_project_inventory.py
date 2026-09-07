import hashlib
from pathlib import Path

import pytest

from tools.project_inventory import (
    InventoryError,
    build_exclusion_receipt,
    build_inventory,
    freeze_exclusion_receipt,
    freeze_inventory_receipt,
    read_exclusion_receipt,
    read_inventory_receipt,
)


def test_shell_alternate_value_is_not_a_password_assignment():
    from tools.project_inventory import token_signature_rule

    assert token_signature_rule(b'echo "${MVNW_PASSWORD:+has-password}"') is None
    assert token_signature_rule(b'echo "${READY:+PASSWORD=sensitive-value}"') == "token-assignment-v1"


def test_inventory_is_metadata_only_complete_and_excludes_secrets(tmp_path: Path) -> None:
    module = tmp_path / "project" / "service"
    module.mkdir(parents=True)
    for number in range(105):
        (module / "src").mkdir(exist_ok=True)
        (module / "src" / f"unit_{number}.py").write_text(f"VALUE = {number}\n", encoding="utf-8")
    (module / "pyproject.toml").write_text("[project]\nname = 'service'\n", encoding="utf-8")
    (module / ".env").write_text("API_TOKEN=ultra-secret-value\n", encoding="utf-8")
    (module / "target").mkdir()
    (module / "target" / "compiled.class").write_bytes(b"\0binary")

    inventory = build_inventory(tmp_path / "project", module)

    assert len(inventory["files"]) == 106
    assert {entry["project_path"] for entry in inventory["files"]} >= {
        "service/pyproject.toml", "service/src/unit_0.py", "service/src/unit_104.py"
    }
    assert all("content" not in entry for entry in inventory["files"])
    assert all(entry["opaque_id"].startswith("file-") for entry in inventory["files"])
    secret = next(item for item in inventory["exclusions"] if item["project_path"] == "service/.env")
    assert secret == {
        "project_path": "service/.env",
        "reason_code": "SECRET_SUSPECTED",
        "scanner_rule_id": "filename-dotenv-v1",
        "safe_label": "dotenv",
    }
    serialized = repr(inventory)
    assert "ultra-secret-value" not in serialized
    assert hashlib.sha256(b"API_TOKEN=ultra-secret-value\n").hexdigest() not in serialized
    frozen_inventory = freeze_inventory_receipt(tmp_path / "inventory.json", inventory)
    assert read_inventory_receipt(tmp_path / "inventory.json") == frozen_inventory
    (tmp_path / "inventory.json").write_bytes((tmp_path / "inventory.json").read_bytes() + b" ")
    with pytest.raises(InventoryError, match="canonical"):
        read_inventory_receipt(tmp_path / "inventory.json")
    exclusion_receipt = build_exclusion_receipt(inventory)
    frozen_exclusions = freeze_exclusion_receipt(tmp_path / "exclusions.json", exclusion_receipt)
    assert read_exclusion_receipt(tmp_path / "exclusions.json") == frozen_exclusions


def test_inventory_excludes_skill_pack_and_reparse_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "nested"
    skill_pack = module / "pipeline"
    module.mkdir(parents=True)
    skill_pack.mkdir()
    (module / "app.py").write_text("pass\n", encoding="utf-8")
    (skill_pack / "SKILL.md").write_text("not project source\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    link = module / "outside-link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pass

    inventory = build_inventory(project, module, skill_pack_root=skill_pack)

    assert [item["project_path"] for item in inventory["files"]] == ["nested/app.py"]
    reasons = {item["reason_code"] for item in inventory["exclusions"]}
    assert "SKILL_PACK" in reasons
    if link.exists() or link.is_symlink():
        assert any(item["project_path"] == "nested/outside-link.py" and item["reason_code"] == "REPARSE_ESCAPE" for item in inventory["exclusions"])


def test_inventory_rejects_a_reparse_module_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "module"
    project.mkdir()
    module.mkdir()
    link = project / "linked-module"
    try:
        link.symlink_to(module, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(InventoryError, match="symlink or reparse"):
        build_inventory(project, link)


def test_nested_inventory_keeps_project_root_skillsrc_as_a_baseline_candidate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "parent" / "child"
    module.mkdir(parents=True)
    (project / ".skillsrc").write_text('{"version":"5"}\n', encoding="utf-8")
    (module / "app.py").write_text("pass\n", encoding="utf-8")

    inventory = build_inventory(project, module)

    root_skillsrc = next(item for item in inventory["files"] if item["project_path"] == ".skillsrc")
    assert root_skillsrc["kind"] == "config"
    assert inventory["project_identity"].startswith("project-")


def test_inventory_does_not_drop_a_user_requirements_to_do_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    requirements = project / "to_do"
    requirements.mkdir(parents=True)
    (requirements / "feature.md").write_text("Требование пользователя\n", encoding="utf-8")

    inventory = build_inventory(project, project)

    assert [item["project_path"] for item in inventory["files"]] == ["to_do/feature.md"]
