"""Build and verify the deterministic portable skill-pack release manifest, and list the files of a delivery (development tool).

The registry, the profiles and the check of a copy live in ``tools.pack_registry`` (every delivery carries it, ``doctor`` uses
it).  This file is the builder: it validates the contract, writes ``release/manifest.json`` and, for a
kit, prints the files of a delivery profile from Git's index.  It runs ``git`` and writes the manifest, so a delivery of
cases does not carry it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from tools import pack_registry
from tools.json_cli import JsonArgumentParser
from tools.pack_registry import ARCHIVE_SUFFIXES, DELIVERY_FILES, PROFILES, ROOT_FILES, RUNTIME_ROOTS, RUNTIME_SUFFIXES
from tools.pack_registry import canonical as _canonical
from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics


def _runtime_registry(root: Path) -> list[dict[str, str]]:
    return pack_registry.registry_rows(root)


def build_release_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = _runtime_registry(root)
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
    return pack_registry.assemble_manifest(registry, contract)


def verify_release_manifest(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The check of ``doctor`` (digest, schema, exact files of one profile, bytes) and, for the repository, a full rebuild."""
    value = dict(manifest) if isinstance(manifest, Mapping) else {}
    profile, value = pack_registry.verify_pack(root, value)
    if profile == "universal":
        expected = build_release_manifest(root)
        if value != expected:
            raise ValueError("release manifest metadata does not match runtime")
    return value


def load_release_manifest(root: Path) -> dict[str, Any]:
    """Read and verify the persisted machine-authoritative release manifest."""
    root = root.resolve()
    return verify_release_manifest(root, dict(pack_registry.load_manifest(root)))


_COMMIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/~^-]{0,199}")


def _git_paths(root: Path, commit: str | None) -> list[str]:
    command = ["git", "-c", "core.fsmonitor=false", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    command += ["ls-tree", "-r", "-z", "--name-only", commit] if commit else ["ls-files", "-z"]
    try:
        done = subprocess.run(command, capture_output=True, check=False)
    except OSError as error:
        raise ValueError(f"git is not available: {error}") from error
    if done.returncode:
        raise ValueError("git failed: the delivery list is built from a Git checkout of the package (and an existing commit)")
    return sorted(item.decode("utf-8") for item in done.stdout.split(b"\0") if item)


def delivery_files(root: Path, profile: str, commit: str | None = None) -> list[str]:
    """The files a kit copies for ``profile``: Git's index (or ``commit``, read without the working tree), registry files of the
    profile and the files of ``DELIVERY_FILES``.  Never an untracked file: a draft lying in the tree must not reach a delivery,
    and the list is exactly what ``git archive`` of the kit's commit carries."""
    root = Path(root).resolve()
    if profile not in PROFILES:
        raise ValueError(f"unknown delivery profile: {profile}")
    if commit is not None and not _COMMIT.fullmatch(commit):
        raise ValueError(f"invalid commit: {commit}")
    tracked = _git_paths(root, commit)
    registry, stray = [], []
    for path in tracked:
        if path in ROOT_FILES:
            registry.append(path)
        elif path.split("/", 1)[0] in RUNTIME_ROOTS:
            (registry if Path(path).suffix in RUNTIME_SUFFIXES and "__pycache__" not in path else stray).append(path)
    if stray:
        raise ValueError(f"tracked files of the registry roots that no pack may carry: {stray[:5]}")
    files = sorted({*pack_registry.profile_paths(registry, profile), *(path for path in DELIVERY_FILES if path in tracked)})
    archives = [path for path in files if path.lower().endswith(ARCHIVE_SUFFIXES)]
    if archives:
        raise ValueError(f"a delivery may carry no archive (company policy): {archives[:5]}")
    return files


def main() -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", choices=PROFILES, help="Print the files of this delivery profile, one per line (Git's index).")
    parser.add_argument("--commit", help="With --list: the files of this commit instead of the index.")
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
