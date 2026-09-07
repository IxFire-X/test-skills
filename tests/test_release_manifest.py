import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _copy_runtime(pack_root: Path, destination: Path) -> Path:
    for relative in ("contracts", "schemas", "skills", "tools", "evals"):
        shutil.copytree(pack_root / relative, destination / relative)
    return destination


def test_release_manifest_binds_exact_runtime_bytes_and_stays_unverified(pack_root: Path, tmp_path: Path):
    from tools.release_manifest import build_release_manifest, load_release_manifest, verify_release_manifest

    runtime = _copy_runtime(pack_root, tmp_path / "pack")
    manifest = build_release_manifest(runtime)

    schema = json.loads((runtime / "schemas" / "release-manifest.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(manifest)
    contract = json.loads((runtime / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    contract_row = next(row for row in manifest["registry"] if row["path"] == "contracts/pipeline.json")
    assert manifest["package_version"] == "0.5.0-pilot"
    assert manifest["pipeline_contract"] == {"version": "4.0", "path": "contracts/pipeline.json", "digest": contract_row["digest"]}
    assert manifest["stage_registry"] == contract["stage_registry"]
    assert manifest["projection_profiles"] == contract["projection_profiles"]
    assert manifest["compatibility_contract_version"] == "portable-cli-v1"
    assert manifest["execution_profile_version"] == "v1"
    assert manifest["release_eval"]["policy"] == "adaptive-1-3-5-v1"
    assert manifest["qualification"] == {"state": "implemented_unverified", "ready_tuple": None}
    paths = [row["path"] for row in manifest["registry"]]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))
    assert "release/manifest.json" not in paths
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in paths)
    assert verify_release_manifest(runtime, manifest) == manifest
    (runtime / "release").mkdir()
    (runtime / "release" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    assert load_release_manifest(runtime) == manifest

    skill = runtime / "skills" / "tc-generator" / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="registry"):
        verify_release_manifest(runtime, manifest)


def test_release_manifest_is_deterministic_and_rejects_self_tampering(pack_root: Path):
    from tools.release_manifest import build_release_manifest, verify_release_manifest

    first = build_release_manifest(pack_root)
    second = build_release_manifest(pack_root)
    assert second == first

    tampered = dict(first)
    tampered["package_version"] = "forged"
    with pytest.raises(ValueError, match="digest"):
        verify_release_manifest(pack_root, tampered)
