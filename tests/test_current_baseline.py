import json
from pathlib import Path

from helpers import run_module


def test_current_contract_checker_catches_baseline_registry_regressions(pack_root: Path) -> None:
    result = run_module(pack_root, "tools.contract_check", "--root", str(pack_root), "--full")

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout) == {"status": "passed", "errors": []}


def test_registered_skills_exist_for_the_current_contract(pack_root: Path) -> None:
    contract = json.loads((pack_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))

    missing = [
        relative_path
        for relative_path in contract["skill_files"].values()
        if not (pack_root / relative_path).is_file()
    ]

    assert missing == [], f"current contract registers missing Skills: {missing}"


def test_release_qualification_remains_implemented_unverified_with_external_n_a(pack_root: Path) -> None:
    contract = json.loads((pack_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))

    assert contract["release_qualification"]["component_state"] == "implemented_unverified"
    assert contract["release_qualification"]["ready_tuple"] is None
    assert contract["release_qualification"]["not_applicable"] == [
        "company_runner", "zephyr_tenant", "production_rollback",
    ]
    assert all(
        row["implementation_status"] == "IMPLEMENTED" and row["semantic_ready"] is True
        for registry in ("artifact_registry", "schema_registry")
        for row in contract[registry]
        if row["phase"] == 8
    )
