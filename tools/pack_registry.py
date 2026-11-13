"""The registry of the pack: which files it is made of, their SHA-256, and the check of a copy against ``release/manifest.json``.

A pack is one of three builds, told apart by the exact set of its registry files (never by a flag):

* ``universal`` — the repository: every runtime file, the maintainer registry ``evals/`` and the tools of development;
* ``mode-b`` — the delivery with automated tests: the files of mode B (``materialize``/``ingest``, the adapters, the roles) but
  no tool of development (``ci_gate`` and ``release_manifest`` run ``git``) and no maintainer file; no file of it starts a process;
* ``cases-only`` — the delivery for cases without a project run: no ``tools/exec_*.py`` (the commands of mode B), no file of mode B,
  no maintainer files and no tool of development.

``doctor`` (in every delivery) and ``release_manifest`` (development only: it builds the manifest and lists a delivery)
share this module.  It only reads: no process, no network, no write.
"""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import marshal
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics

RUNTIME_ROOTS = ("contracts", "schemas", "skills", "tools")
MAINTAINER_ROOTS = ("evals",)
REGISTRY_ROOTS = RUNTIME_ROOTS + MAINTAINER_ROOTS
# The files of the root a pack carries, so that the check covers them: the two documents generated from the contract, the texts a
# human follows (README, HOW-TO-USE), what ``pip`` would read (``requirements*.txt``) and the example of ``.skillsrc``.
ROOT_FILES = ("CONTRACTS.md", "PIPELINE.md", "README.md", "HOW-TO-USE.md", "HOW-TO-USE-MODE-B.md", "requirements.txt", "requirements-dev.txt",
              ".skillsrc.example")
RUNTIME_SUFFIXES = {".json", ".md", ".py"}
PROFILES = ("universal", "mode-b", "cases-only")

# What a cases-only delivery does not carry, and why: development tools (checks of the contract, its rendering, the gate, the
# builder of this manifest), the schemas read only by the release evaluation, and everything that starts or measures a run.
CASES_ONLY_EXCLUDED_FILES = (
    "tools/ci_gate.py", "tools/compatibility_contract.py", "tools/contract_check.py", "tools/release_manifest.py",
    "tools/render_contract_docs.py", "skills/orchestrate/references/mode-b.md",
    "schemas/compatibility-evidence.schema.json", "schemas/release-eval-receipt.schema.json",
    "schemas/release-eval-run.schema.json",
    "requirements-dev.txt", "HOW-TO-USE-MODE-B.md",  # the text of mode B for the human: only the delivery of mode B
    # mode B (generation and run of automated tests): the core reaches these only through ``execution_gate.load_mode_b``
    "tools/run_tests.py", "tools/generated_delta.py", "tools/automation_validation.py", "tools/execution_adapters.py",
    "tools/autotest_hints.py", "tools/trace_check.py", "tools/build_trace_document.py", "tools/quarantine.py", "tools/code_slices.py",
    "tools/execution_core.py",  # the interface of the core for the execution files
)
CASES_ONLY_EXCLUDED_PATTERNS = ("tools/exec_*.py",)  # launch of builds and tests, the stand, the probe
# The two roles of mode B (generation and review of automated tests) and the text of mode B for the controller: texts of what a
# delivery of cases does not carry (PT).
CASES_ONLY_EXCLUDED_PREFIXES = ("evals/", "skills/tc-to-autotest/", "skills/autotest-reviewer/")

# What the delivery of mode B leaves out: the tools of development and the maintainer files.  ``ci_gate`` and ``release_manifest``
# run ``git`` and write the manifest; the others check the contract, render its documents and keep the evidence of the release
# evaluation.  ``scenario-observation-receipt`` stays, in the delivery of cases too: a second ``finish`` writes it in either.
MODE_B_EXCLUDED_FILES = (
    "tools/ci_gate.py", "tools/release_manifest.py", "tools/compatibility_contract.py", "tools/contract_check.py",
    "tools/render_contract_docs.py", "requirements-dev.txt",
    "schemas/compatibility-evidence.schema.json", "schemas/release-eval-receipt.schema.json", "schemas/release-eval-run.schema.json",
)
MODE_B_EXCLUDED_PATTERNS: tuple[str, ...] = ()
MODE_B_EXCLUDED_PREFIXES = ("evals/",)

# The one file of a delivery that is not in the registry: it holds the digests of the others.
DELIVERY_FILES = ("release/manifest.json",)
ARCHIVE_SUFFIXES = (".7z", ".bz2", ".gz", ".jar", ".rar", ".tar", ".tgz", ".xz", ".zip")

_PYC = re.compile(r"^(?P<module>[A-Za-z0-9_]+)\.(?P<tag>cpython-3\d+)(?:\.opt-(?P<opt>[12]))?\.pyc$")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def manifest_digest(value: Mapping[str, Any]) -> str:
    return digest_bytes(canonical({key: item for key, item in value.items() if key != "digest"}))


def package_version(contract: Mapping[str, Any]) -> str:
    """The one place the version of the pack is written: ``release_qualification.package_version`` of the contract."""
    return str(contract["release_qualification"]["package_version"])


def is_cases_only_excluded(path: str) -> bool:
    return (path in CASES_ONLY_EXCLUDED_FILES or path.startswith(CASES_ONLY_EXCLUDED_PREFIXES)
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in CASES_ONLY_EXCLUDED_PATTERNS))


def is_mode_b_excluded(path: str) -> bool:
    return (path in MODE_B_EXCLUDED_FILES or path.startswith(MODE_B_EXCLUDED_PREFIXES)
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in MODE_B_EXCLUDED_PATTERNS))


def profile_paths(paths: Any, profile: str) -> list[str]:
    if profile not in PROFILES:
        raise ValueError(f"unknown delivery profile: {profile}")
    left_out = {"universal": lambda path: False, "mode-b": is_mode_b_excluded, "cases-only": is_cases_only_excluded}[profile]
    return sorted(path for path in paths if not left_out(path))


def _under(root: Path, path: Path) -> str:
    if root not in path.resolve().parents:
        raise ValueError(f"release runtime path escapes pack root: {path.relative_to(root).as_posix()}")
    return path.relative_to(root).as_posix()


def pyc_matches_source(pyc: Path, source: Path, optimize: int) -> bool:
    """True when the code in ``pyc`` is the code of ``source`` (compiled the way the import system does).  Python runs a ``.pyc`` whose
    header says "made from this source" without reading the source, so a planted one would run in place of the file the SHA-256
    covers; the header can be copied, the code cannot.  Equal code objects do not depend on ``co_filename``."""
    try:
        data = pyc.read_bytes()
        if data[:4] != importlib.util.MAGIC_NUMBER:
            return False
        return bool(marshal.loads(data[16:]) == compile(source.read_bytes(), str(source), "exec", dont_inherit=True, optimize=optimize))
    except (OSError, ValueError, EOFError, TypeError, SyntaxError, RecursionError, MemoryError):
        return False


def scan_disk(root: Path) -> tuple[list[str], list[str]]:
    """``(registry files, stray files)`` under the registry roots of ``root``.

    A stray file is anything a pack does not carry: a file of another suffix, a ``.pyc`` outside ``__pycache__`` (a sourceless
    ``tools/x.pyc`` imports as ``tools.x`` with no source), anything but a ``*.cpython-3XX.pyc`` in a ``__pycache__``.  Python makes
    those ``.pyc`` itself when ``PYTHONDONTWRITEBYTECODE`` is not set, so they are neither registry nor stray — but only while each
    one of this Python's own tag holds exactly the code of its source (``pyc_matches_source``); one that does not, or has no source,
    is stray.  A ``.pyc`` of another tag is never read by this Python and is left alone.  A missing root is not an error here; the
    profile check says what is missing."""
    registry: list[str] = []
    stray: list[str] = []
    for directory in REGISTRY_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = _under(root, path)
            if path.parent.name == "__pycache__":
                found = _PYC.fullmatch(path.name)
                if not found:
                    stray.append(relative)
                elif found["tag"] == sys.implementation.cache_tag and not pyc_matches_source(
                        path, path.parent.parent / f"{found['module']}.py", int(found["opt"] or 0)):
                    stray.append(relative)
            elif "__pycache__" in path.parts or path.suffix not in RUNTIME_SUFFIXES:
                stray.append(relative)
            else:
                registry.append(relative)
    for name in ROOT_FILES:
        if (root / name).is_file():
            registry.append(name)
    return sorted(registry), sorted(stray)


def registry_rows(root: Path) -> list[dict[str, str]]:
    root = Path(root).resolve()
    for directory in RUNTIME_ROOTS:
        if not (root / directory).is_dir():
            raise ValueError(f"release runtime root is missing: {directory}")
    paths, _stray = scan_disk(root)
    rows = [{"path": path, "digest": digest_bytes((root / path).read_bytes())} for path in paths]
    rows.sort(key=lambda row: row["path"])
    return rows


def delivery_profiles(registry: list[Mapping[str, str]]) -> list[dict[str, Any]]:
    """The ``delivery_profiles`` section of the manifest: what each delivery profile leaves out of the registry and the digest of the rest."""
    paths = [row["path"] for row in registry]
    rules = (("mode-b", MODE_B_EXCLUDED_FILES, MODE_B_EXCLUDED_PATTERNS, MODE_B_EXCLUDED_PREFIXES),
             ("cases-only", CASES_ONLY_EXCLUDED_FILES, CASES_ONLY_EXCLUDED_PATTERNS, CASES_ONLY_EXCLUDED_PREFIXES))
    result = []
    for profile, files, patterns, prefixes in rules:
        kept = set(profile_paths(paths, profile))
        result.append({
            "id": profile,
            "excluded_files": sorted(path for path in paths if path in files),
            "excluded_patterns": list(patterns),
            "excluded_prefixes": list(prefixes),
            "skill_pack_digest": digest_bytes(canonical([row for row in registry if row["path"] in kept])),
        })
    return result


def assemble_manifest(registry: list[dict[str, str]], contract: Mapping[str, Any], suite: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest from its facts.  ``contract`` and ``suite`` are validated by the caller (development); this only arranges them."""
    by_path = {row["path"]: row["digest"] for row in registry}
    qualification = contract["release_qualification"]
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "package_version": package_version(contract),
        "skill_pack_digest": digest_bytes(canonical(registry)),
        "pipeline_contract": {"version": contract.get("version"), "path": "contracts/pipeline.json", "digest": by_path["contracts/pipeline.json"]},
        "compatibility_contract_version": qualification["compatibility_contract_version"],
        "execution_profile_version": qualification["execution_profile_version"],
        "policy_profiles": [{"id": row["id"], "version": row["version"]} for row in contract.get("policy_profiles", [])],
        "adapter_registry": [{"id": row["id"], "version": row["version"]} for row in contract.get("adapter_registry", [])],
        "stage_registry": [dict(row) for row in contract.get("stage_registry", [])],
        "projection_profiles": [dict(row) for row in contract.get("projection_profiles", [])],
        "release_eval": {"suite_id": suite["suite_id"], "suite_digest": suite["digest"], "policy": suite["policy"]},
        "delivery_profiles": delivery_profiles(registry),
        "registry": registry,
        "qualification": {"state": "implemented_unverified", "ready_tuple": None},
    }
    manifest["digest"] = manifest_digest(manifest)
    return manifest


def load_manifest(root: Path) -> Mapping[str, Any]:
    try:
        value = load_json_strict(Path(root).resolve() / "release" / "manifest.json")
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("release manifest is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError("release manifest is invalid")
    return value


def profile_diff(root: Path, manifest: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """``(nearest profile, extra files, missing files)`` of ``root`` — all three empty-handed only for a pack that is exactly a profile."""
    paths, stray = scan_disk(Path(root).resolve())
    present = set(paths)
    rows = manifest.get("registry")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) and isinstance(row.get("path"), str) for row in rows):
        raise ValueError("release manifest schema is invalid")
    universal = {row["path"] for row in rows}
    sets = {"universal": universal, "mode-b": set(profile_paths(universal, "mode-b")), "cases-only": set(profile_paths(universal, "cases-only"))}
    name = min(PROFILES, key=lambda profile: len(present ^ sets[profile]))  # a tie goes to the first of PROFILES
    nearest = sets[name]
    return name, sorted((present - nearest) | set(stray)), sorted(nearest - present)


def profile_of(root: Path, manifest: Mapping[str, Any]) -> str:
    """``universal``, ``mode-b`` or ``cases-only``: the registry files on disk must be exactly one profile's registry — never the nearest."""
    name, extra, missing = profile_diff(root, manifest)
    if not extra and not missing:
        return name
    raise ValueError(f"release runtime files match no delivery profile: extra {extra[:5]}, missing {missing[:5]}")


def verify_pack(root: Path, manifest: Mapping[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """``(profile, manifest)`` of a pack that is whole: the manifest's own digest and schema, the exact files of one profile,
    their bytes, and what the manifest says about the contract.  Reads only; nothing it learns is kept."""
    root = Path(root).resolve()
    value = dict(load_manifest(root) if manifest is None else manifest)
    if value.get("digest") != manifest_digest(value):
        raise ValueError("release manifest digest is invalid")
    profile = profile_of(root, value)  # the files first: an extra file may be the reason the schemas cannot be read
    if schema_diagnostics(value, root / "schemas" / "release-manifest.schema.json", root):
        raise ValueError("release manifest schema is invalid")
    rows = registry_rows(root)
    stored = list(value["registry"])
    wanted = set(profile_paths([row["path"] for row in stored], profile))
    if rows != [row for row in stored if row["path"] in wanted]:
        raise ValueError("release manifest registry does not match runtime bytes")
    try:
        contract = load_json_strict(root / "contracts" / "pipeline.json")
        suite_path = root / "evals" / "scenarios" / "pilot-critical.json"
        suite = load_json_strict(suite_path) if suite_path.is_file() else None
        expected = assemble_manifest(stored, contract, suite if isinstance(suite, Mapping) else {
            "suite_id": value["release_eval"]["suite_id"], "digest": value["release_eval"]["suite_digest"], "policy": value["release_eval"]["policy"]})
    except (OSError, UnicodeDecodeError, StrictJsonError, KeyError, TypeError) as error:
        raise ValueError("release runtime metadata is unreadable") from error
    if value != expected:
        raise ValueError("release manifest metadata does not match runtime")
    return profile, value


def crlf_converted(root: Path) -> list[str]:
    """The registry files whose bytes differ from the manifest only by line endings — the same bytes after CRLF -> LF.

    git with autocrlf (the default on Windows) turns LF into CRLF at checkout.  The byte check itself stays as it is; this
    only tells a changed line ending from a changed file, for the person who reads the answer of ``doctor``."""
    root = Path(root).resolve()
    try:
        registry = load_manifest(root).get("registry")
    except ValueError:
        return []
    found = []
    for row in registry if isinstance(registry, list) else []:
        try:
            data = (root / str(row["path"])).read_bytes()
            expected = row["digest"]
        except (OSError, KeyError, TypeError):
            continue
        if digest_bytes(data) != expected and digest_bytes(data.replace(b"\r\n", b"\n")) == expected:
            found.append(str(row["path"]))
    return sorted(found)
