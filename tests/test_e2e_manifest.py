import json
from pathlib import Path, PurePosixPath

RUN_IDS = {"java-step5", "python-inventree", "not-runnable-subscription"}
SKILL_PACK_COMMIT = "6f83f8e57ac6fa5f24ea40f40126430f0346df0b"


def test_e2e_manifest_declares_reproducible_non_mutating_runs(root):
    manifest = json.loads(
        (root / "docs/to_do/e2e/manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == "1.0"
    assert manifest["skill_pack_commit"] == SKILL_PACK_COMMIT
    assert {run["id"] for run in manifest["runs"]} == RUN_IDS

    for run in manifest["runs"]:
        artifact_root = PurePosixPath(run["artifact_root"])
        assert artifact_root.parts[:3] == ("docs", "to_do", "e2e")
        assert (root / Path(*artifact_root.parts)).is_dir()
        assert run["fixture"].startswith("D:/AI-Projects/")
        assert len(run["source_revision"]) == 40
        assert run["mutation_policy"] == "fixture-read-only"
        assert run["commands"]
        assert all(command["argv"] and Path(command["cwd"]).is_absolute() for command in run["commands"])

        ledger = run.get("command_ledger")
        if run["state"] == "accepted-existing-evidence":
            assert ledger
            assert (root / Path(*PurePosixPath(ledger).parts)).is_file()

    for section in ("java", "python", "not-runnable"):
        readme = (root / "docs/to_do/e2e" / section / "README.md").read_text(
            encoding="utf-8"
        )
        assert "не измен" in readme.casefold()
