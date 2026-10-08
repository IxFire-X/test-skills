"""Build and verify the deterministic portable skill-pack release manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.json_cli import JsonArgumentParser
from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics


PACKAGE_VERSION = "0.5.0-pilot"
COMPATIBILITY_VERSION = "portable-cli-v1"
EXECUTION_PROFILE_VERSION = "v1"
RUNTIME_ROOTS = ("contracts", "schemas", "skills", "tools", "evals")
RUNTIME_SUFFIXES = {".json", ".md", ".py", ".gradle"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _manifest_digest(value: Mapping[str, Any]) -> str:
    return _digest_bytes(_canonical({key: item for key, item in value.items() if key != "digest"}))


def _runtime_registry(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for directory in RUNTIME_ROOTS:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"release runtime root is missing: {directory}")
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in RUNTIME_SUFFIXES and "__pycache__" not in path.parts:
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise ValueError(f"release runtime path escapes pack root: {path.relative_to(root).as_posix()}")
                rows.append({"path": path.relative_to(root).as_posix(), "digest": _digest_bytes(path.read_bytes())})
    rows.sort(key=lambda row: row["path"])
    return rows


def build_release_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = _runtime_registry(root)
    by_path = {row["path"]: row["digest"] for row in registry}
    try:
        contract = load_json_strict(root / "contracts" / "pipeline.json")
        suite = load_json_strict(root / "evals" / "scenarios" / "pilot-critical.json")
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("release runtime metadata is unreadable") from error
    if not isinstance(contract, Mapping) or not isinstance(suite, Mapping):
        raise ValueError("release runtime metadata is invalid")
    if schema_diagnostics(contract, root / "schemas" / "pipeline.schema.json", root):
        raise ValueError("pipeline contract schema is invalid")
    from tools.contract_check import validate_pipeline_contract

    if validate_pipeline_contract(contract, root, check_drift=False)["status"] != "passed":
        raise ValueError("pipeline contract semantics are invalid")
    suite_body = {key: item for key, item in suite.items() if key != "digest"}
    if suite.get("digest") != _digest_bytes(_canonical(suite_body)):
        raise ValueError("release scenario suite digest is invalid")
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "package_version": PACKAGE_VERSION,
        "skill_pack_digest": _digest_bytes(_canonical(registry)),
        "pipeline_contract": {
            "version": contract.get("version"),
            "path": "contracts/pipeline.json",
            "digest": by_path["contracts/pipeline.json"],
        },
        "compatibility_contract_version": COMPATIBILITY_VERSION,
        "execution_profile_version": EXECUTION_PROFILE_VERSION,
        "policy_profiles": [{"id": row["id"], "version": row["version"]} for row in contract.get("policy_profiles", [])],
        "adapter_registry": [{"id": row["id"], "version": row["version"]} for row in contract.get("adapter_registry", [])],
        "stage_registry": [dict(row) for row in contract.get("stage_registry", [])],
        "projection_profiles": [dict(row) for row in contract.get("projection_profiles", [])],
        "release_eval": {"suite_id": suite["suite_id"], "suite_digest": suite["digest"], "policy": suite["policy"]},
        "registry": registry,
        "qualification": {"state": "implemented_unverified", "ready_tuple": None},
    }
    manifest["digest"] = _manifest_digest(manifest)
    return manifest


def verify_release_manifest(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(manifest) if isinstance(manifest, Mapping) else {}
    if value.get("digest") != _manifest_digest(value):
        raise ValueError("release manifest digest is invalid")
    diagnostics = schema_diagnostics(value, root.resolve() / "schemas" / "release-manifest.schema.json", root.resolve())
    if diagnostics:
        raise ValueError("release manifest schema is invalid")
    expected = build_release_manifest(root)
    if value.get("registry") != expected["registry"] or value.get("skill_pack_digest") != expected["skill_pack_digest"]:
        raise ValueError("release manifest registry does not match runtime bytes")
    if value != expected:
        raise ValueError("release manifest metadata does not match runtime")
    return value


def load_release_manifest(root: Path) -> dict[str, Any]:
    """Read and verify the persisted machine-authoritative release manifest."""
    root = root.resolve()
    try:
        value = load_json_strict(root / "release" / "manifest.json")
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("release manifest is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError("release manifest is invalid")
    return verify_release_manifest(root, value)


def main() -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    try:
        if arguments.check:
            load_release_manifest(root)
        else:
            path = root / "release" / "manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_canonical(build_release_manifest(root)) + b"\n")
    except ValueError as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
