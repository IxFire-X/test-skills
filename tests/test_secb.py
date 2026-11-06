"""SEC-B: what mode B reads from `.skillsrc` and from the build never reaches a host path, a secret or a network share unchecked."""
from __future__ import annotations

import json
import pathlib
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNC = [r"\\host\share\mvn.cmd", "//host/share/mvn.cmd", r"\\?\C:\tools\mvn.cmd", r"\\.\pipe\mvn.cmd"]


@pytest.mark.parametrize("executable", UNC)
def test_the_system_maven_path_refuses_a_share_or_a_device_before_any_io(executable: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project_inventory import InventoryError, system_maven_path

    def touched(*_args, **_kwargs):
        raise AssertionError("the path was used before it was refused")

    with monkeypatch.context() as patched:  # only around the call: pytest itself needs the file system to report
        for owner, name in ((shutil, "which"), (pathlib.Path, "resolve"), (pathlib.Path, "is_file"), (pathlib.Path, "exists")):
            patched.setattr(owner, name, touched)
        with pytest.raises(InventoryError, match="local"):
            system_maven_path(executable)


def _skillsrc(executable: str) -> bytes:
    return json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "x"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "m", "root": ".", "stack": {"language": "java", "build_tool": "maven"}, "paths": {"source": ["src/main"], "tests": ["src/test"]},
                     "test": {"framework": "junit5", "adapter_id": "maven:selected-symbols-v1", "executable": executable, "build_profile": "default",
                              "adapter_parameters": {}}, "detected_from": ["pom.xml"]}],
    }).encode("utf-8")


def test_the_schema_takes_the_launcher_by_name_or_by_a_local_path_and_not_a_share() -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    for fine in ("mvn", r"C:\tools\maven\mvn.cmd", "/opt/maven/bin/mvn"):
        parse_skillsrc_bytes(_skillsrc(fine))
    for bad in UNC:
        with pytest.raises(SkillsrcError):
            parse_skillsrc_bytes(_skillsrc(bad))


# ---- a secret in the text of a failure reaches neither the receipt nor the annotation (SEC-B Б4) --------------------------

SECRET = "ghp_" + "a1B2c3D4" * 5


def test_a_secret_in_a_junit_failure_is_masked_in_the_message_the_receipt_keeps() -> None:
    import xml.etree.ElementTree as ET

    from tools.run_tests import _failure_message

    item = ET.fromstring(f'<testcase name="t"><failure message="expected 200 but was 401 for token {SECRET}"/></testcase>')
    assert SECRET not in _failure_message(item) and _failure_message(item) == "<redacted>"
    plain = ET.fromstring('<testcase name="t"><failure message="expected: _2_ but was: _3_"/></testcase>')
    assert _failure_message(plain) == "expected: _2_ but was: _3_"


def test_a_secret_in_a_failure_does_not_reach_the_annotation_of_the_quarantined_method() -> None:
    from tools.quarantine import failure_note

    ref, note = failure_note(f"ASSERT-a-001 password was {SECRET}")
    assert ref == "ASSERT-a-001" and SECRET not in note and "password" not in note.lower()
    assert failure_note("ASSERT-a-001 expected: _2_ but was: _3_") == ("ASSERT-a-001", "expected: _2_ but was: _3_")


def test_the_human_text_of_mode_b_is_in_the_delivery_of_mode_b_only_and_the_notice_points_to_it() -> None:
    from tools import pack_registry
    from tools.exec_commands import _REVIEW_NOTICE

    name = "HOW-TO-USE-MODE-B.md"
    paths = [row["path"] for row in pack_registry.registry_rows(ROOT)]
    assert name in pack_registry.profile_paths(paths, "mode-b") and name not in pack_registry.profile_paths(paths, "cases-only")
    assert name in _REVIEW_NOTICE
    text = (ROOT / name).read_text(encoding="utf-8")
    for word in ("files_to_review", "Первый токен", "Учётные данные", "service_url_env", "--restore"):
        assert word in text
