import json
import shutil
import subprocess
import sys

import pytest

CANONICAL_SKILLS = (
    "context-marker",
    "tc-generator",
    "tc-reviewer",
    "tc-to-autotest",
    "autotest-reviewer",
    "orchestrate",
)
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _is_transient(path):
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store"


def _tree_snapshot(path):
    if not path.exists():
        return None
    snapshot = {}
    for item in sorted(path.rglob("*")):
        if _is_transient(item):
            continue
        relative = item.relative_to(path).as_posix()
        stat = item.stat()
        snapshot[relative] = (
            "directory" if item.is_dir() else "file",
            None if item.is_dir() else item.read_bytes(),
            stat.st_mtime_ns,
        )
    return snapshot


def _source_snapshot(root):
    pipeline = root / "contracts" / "pipeline.json"
    return {
        "skills": _tree_snapshot(root / "skills"),
        "pipeline_bytes": pipeline.read_bytes(),
        "pipeline_mtime_ns": pipeline.stat().st_mtime_ns,
    }


def _assert_install_matches_source(root, destination):
    expected_files = {
        path.relative_to(root / "skills").as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for skill in CANONICAL_SKILLS
        for path in (root / "skills" / skill).rglob("*")
        if path.is_file() and not _is_transient(path)
    }
    actual_files = {
        path.relative_to(destination).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in destination.rglob("*")
        if path.is_file() and not _is_transient(path)
    }
    assert actual_files == expected_files


def _run_generic(root, destination, *extra):
    return subprocess.run(
        [
            sys.executable,
            root / "adapters" / "generic" / "install_skills.py",
            "--source",
            root / "skills",
            "--destination",
            destination,
            *extra,
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _run_windows(root, destination, *extra):
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-File",
            root / "adapters" / "windows" / "install.ps1",
            "-SkillPackRoot",
            root,
            "-Destination",
            destination,
            *extra,
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_generic_install_copies_canonical_bytes(root, tmp_path):
    destination = tmp_path / "generic-install"
    before = _source_snapshot(root)

    result = _run_generic(root, destination)

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_install_matches_source(root, destination)
    assert _source_snapshot(root) == before


def test_generic_second_install_same_destination_is_unchanged(root, tmp_path):
    destination = tmp_path / "generic-idempotent"
    before = _source_snapshot(root)
    first = _run_generic(root, destination)
    assert first.returncode == 0, first.stdout + first.stderr
    installed = _tree_snapshot(destination)

    second = _run_generic(root, destination)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _tree_snapshot(destination) == installed
    assert _source_snapshot(root) == before


def test_generic_dry_run_creates_no_destination(root, tmp_path):
    destination = tmp_path / "generic-dry-run"
    before = _source_snapshot(root)

    result = _run_generic(root, destination, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not destination.exists()
    assert _source_snapshot(root) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_windows_whatif_creates_no_destination(root, tmp_path):
    destination = tmp_path / "windows-whatif"
    before = _source_snapshot(root)

    result = _run_windows(root, destination, "-WhatIf")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not destination.exists()
    assert _source_snapshot(root) == before


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_windows_install_copies_canonical_bytes(root, tmp_path):
    destination = tmp_path / "windows-install"
    before = _source_snapshot(root)
    first = _run_windows(root, destination)
    assert first.returncode == 0, first.stdout + first.stderr
    _assert_install_matches_source(root, destination)
    installed = _tree_snapshot(destination)

    second = _run_windows(root, destination)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _tree_snapshot(destination) == installed
    assert _source_snapshot(root) == before


def test_direct_generic_install_requires_no_host_adapter(root, tmp_path):
    portable_root = tmp_path / "portable-pack"
    before = _source_snapshot(root)
    (portable_root / "contracts").mkdir(parents=True)
    shutil.copy2(root / "contracts" / "pipeline.json", portable_root / "contracts" / "pipeline.json")
    for skill in CANONICAL_SKILLS:
        shutil.copytree(
            root / "skills" / skill,
            portable_root / "skills" / skill,
            copy_function=shutil.copy2,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
        )

    assert not (portable_root / "adapters").exists()
    contract = json.loads((portable_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    assert tuple(contract["skill_files"]) == CANONICAL_SKILLS
    for relative_path in contract["skill_files"].values():
        copied = portable_root / relative_path
        source = root / relative_path
        assert copied.read_bytes() == source.read_bytes()
        assert copied.stat().st_mtime_ns == source.stat().st_mtime_ns
    assert _source_snapshot(root) == before
