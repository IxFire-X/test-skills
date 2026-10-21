"""Build and verify the deterministic portable skill-pack release manifest and list the files of a delivery.

Two delivery profiles share one manifest.  ``universal`` is the repository build: the runtime registry, plus the
maintainer registry (``evals/``, the release evaluation).  ``host`` (H1) is the build for a company whose CLI session
leads the run: it leaves out the process launch of builds and tests, the model process runner, the CI gate and every
maintainer file.  A copy is one profile or the other by its exact set of runtime files; ``--list <profile>`` prints the
files a kit copies (``git archive`` of that list; ``--commit <rev>`` lists that commit), never an untracked file and never
an archive for ``host``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.json_cli import JsonArgumentParser
from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics


PACKAGE_VERSION = "0.5.0-pilot"
COMPATIBILITY_VERSION = "portable-cli-v1"
EXECUTION_PROFILE_VERSION = "v1"
RUNTIME_ROOTS = ("contracts", "schemas", "skills", "tools")
MAINTAINER_ROOTS = ("evals",)
RUNTIME_SUFFIXES = {".json", ".md", ".py", ".gradle"}
PROFILES = ("universal", "host")
HOST_EXCLUDED = (
    "tools/ci_gate.py", "tools/company_runner.py", "tools/model_runner.py", "tools/pipeline_driver_run.py",
    "tools/pipeline_driver_runner.py", "tools/process_launch.py",
    "schemas/company-execution-receipt.schema.json",  # read only by company_runner
)
HOST_EXCLUDED_PREFIXES = ("evals/", "docs/research/", "docs/review/", "docs/superpowers/")
# No delivery carries them (the sparse list of USAGE.md): the package's own tests and development files, slides.
DELIVERY_EXCLUDED_PREFIXES = (".claude/", ".github/", "tests/")
DELIVERY_EXCLUDED_FILES = ("CLAUDE.md",)
DELIVERY_EXCLUDED_DOC_SUFFIXES = (".pptx",)
ARCHIVE_SUFFIXES = (".7z", ".bz2", ".gz", ".jar", ".rar", ".tar", ".tgz", ".xz", ".zip")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _manifest_digest(value: Mapping[str, Any]) -> str:
    return _digest_bytes(_canonical({key: item for key, item in value.items() if key != "digest"}))


def _runtime_paths(root: Path, roots: tuple[str, ...] = RUNTIME_ROOTS) -> list[Path]:
    paths: list[Path] = []
    for directory in roots:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"release runtime root is missing: {directory}")
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in RUNTIME_SUFFIXES and "__pycache__" not in path.parts:
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise ValueError(f"release runtime path escapes pack root: {path.relative_to(root).as_posix()}")
                paths.append(path)
    return paths


def _runtime_registry(root: Path, roots: tuple[str, ...] = RUNTIME_ROOTS) -> list[dict[str, str]]:
    rows = [{"path": path.relative_to(root).as_posix(), "digest": _digest_bytes(path.read_bytes())} for path in _runtime_paths(root, roots)]
    rows.sort(key=lambda row: row["path"])
    return rows


def _host_rows(registry: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in registry if row["path"] not in HOST_EXCLUDED]


def _checked_contract(root: Path) -> Mapping[str, Any]:
    try:
        contract = load_json_strict(root / "contracts" / "pipeline.json")
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("release runtime metadata is unreadable") from error
    if not isinstance(contract, Mapping):
        raise ValueError("release runtime metadata is invalid")
    if schema_diagnostics(contract, root / "schemas" / "pipeline.schema.json", root):
        raise ValueError("pipeline contract schema is invalid")
    from tools.contract_check import validate_pipeline_contract

    if validate_pipeline_contract(contract, root, check_drift=False)["status"] != "passed":
        raise ValueError("pipeline contract semantics are invalid")
    return contract


def delivery_profile_of(root: Path, manifest: Mapping[str, Any]) -> str:
    """``universal`` or ``host``: the runtime files on disk must be exactly one profile's registry."""
    root = root.resolve()
    present = {path.relative_to(root).as_posix() for path in _runtime_paths(root)}
    universal = {row["path"] for row in manifest.get("registry", [])}
    host = universal - set(HOST_EXCLUDED)
    if present == universal:
        return "universal"
    if present == host:
        return "host"
    nearest = host if len(present ^ host) < len(present ^ universal) else universal
    extra, missing = sorted(present - nearest), sorted(nearest - present)
    raise ValueError(f"release runtime files match no delivery profile: extra {extra[:5]}, missing {missing[:5]}")


def delivery_files(root: Path, profile: str, commit: str | None = None) -> list[str]:
    """The files a kit copies for ``profile``: Git's index (or ``commit``, read without the working tree) minus the exclusions.

    Never an untracked file: a draft lying in the tree (a review with personal e-mails) must not reach a delivery, and the
    list must be exactly what ``git archive`` of the kit's commit carries.
    """
    from tools.git_command import run_git

    if profile not in PROFILES:
        raise ValueError(f"unknown delivery profile: {profile}")
    if commit is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/~^-]{0,199}", commit):
        raise ValueError(f"invalid commit: {commit}")
    root = root.resolve()
    if commit is None:
        arguments = ["-c", "core.quotepath=off", "ls-files", "-z", "--cached"]
    else:
        arguments = ["-c", "core.quotepath=off", "ls-tree", "-r", "-z", "--name-only", "--end-of-options", commit]
    completed = run_git(arguments, cwd=root, timeout=60)
    if completed.returncode != 0:
        raise ValueError("git failed: the delivery list is built from a Git checkout of the package (and an existing commit)")
    files: list[str] = []
    for raw in completed.stdout.split(b"\0"):
        path = raw.decode("utf-8")
        if not path:
            continue
        if path.startswith(DELIVERY_EXCLUDED_PREFIXES) or path in DELIVERY_EXCLUDED_FILES:
            continue
        if path.startswith("docs/") and path.lower().endswith(DELIVERY_EXCLUDED_DOC_SUFFIXES):
            continue
        if profile == "host" and (path in HOST_EXCLUDED or path.startswith(HOST_EXCLUDED_PREFIXES)):
            continue
        files.append(path)
    files = sorted(set(files))
    if profile == "host":
        archives = [path for path in files if path.lower().endswith(ARCHIVE_SUFFIXES)]
        if archives:
            raise ValueError(f"the host delivery may carry no archive (company policy): {archives[:5]}")
    return files


def build_release_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = _runtime_registry(root)
    by_path = {row["path"]: row["digest"] for row in registry}
    contract = _checked_contract(root)
    try:
        suite = load_json_strict(root / "evals" / "scenarios" / "pilot-critical.json")
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("release runtime metadata is unreadable") from error
    if not isinstance(suite, Mapping):
        raise ValueError("release runtime metadata is invalid")
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
        "maintainer_registry": _runtime_registry(root, MAINTAINER_ROOTS),
        "delivery_profiles": [{
            "id": "host", "excluded": [path for path in HOST_EXCLUDED if path in by_path], "excluded_prefixes": list(HOST_EXCLUDED_PREFIXES),
            "skill_pack_digest": _digest_bytes(_canonical(_host_rows(registry))),
        }],
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
    if delivery_profile_of(root, value) == "host":
        # The host copy carries no maintainer files: maintainer_registry and release_eval stay bound by the digest.
        rows = _runtime_registry(root.resolve())
        host = {row["id"]: row for row in value.get("delivery_profiles", [])}.get("host", {})
        if rows != _host_rows(list(value.get("registry", []))) or host.get("skill_pack_digest") != _digest_bytes(_canonical(rows)):
            raise ValueError("release manifest registry does not match runtime bytes")
        if value.get("pipeline_contract", {}).get("digest") != {row["path"]: row["digest"] for row in rows}.get("contracts/pipeline.json"):
            raise ValueError("release manifest metadata does not match runtime")
        _checked_contract(root.resolve())
        return value
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
    parser.add_argument("--list", choices=PROFILES, help="Print the files of this delivery profile, one per line (Git's index).")
    parser.add_argument("--commit", help="With --list: the files of this commit instead of the index (the kit's git archive).")
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    try:
        if arguments.list:
            sys.stdout.buffer.write("".join(path + "\n" for path in delivery_files(root, arguments.list, arguments.commit)).encode("utf-8"))
        elif arguments.check:
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
