"""V9b (the rest of the small finds of the hunt of 05.11): 8.12 a ``.pilot-runs`` that is a link, 8.15 a ``.skillsrc`` of JSON with ``//`` comments.

(The moved project and ``tools.api`` is in tests/test_p6a_portability.py; ``status_after_pause`` is gone from the report: tests/test_run_report_sections.py.)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_f5_first_run import _project, _ready, _row, _runs, _scan


def _link(link: Path, target: Path) -> None:
    if os.name == "nt":
        made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        assert made.returncode == 0, made.stdout + made.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink(link: Path) -> None:
    os.rmdir(link) if os.name == "nt" else link.unlink()


# --- 8.12: .pilot-runs is a link out of the project --------------------------------------------------------------------------------------------

def test_scan_refuses_a_pilot_runs_link_with_a_code_and_a_fix_and_writes_nothing_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(project / ".pilot-runs", outside)
    try:
        payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=2)
        assert payload["reason"] == "RUN_ROOT_UNSAFE" and payload["code"] == "RUN_ROOT_UNSAFE", payload
        assert ".pilot-runs" in payload["fix"] and ("junction" in payload["fix"] or "ссылк" in payload["fix"]), payload
        assert not list(outside.iterdir())
    finally:
        _unlink(project / ".pilot-runs")
    assert _runs(project) == []


def test_ready_stops_on_a_pilot_runs_link_and_says_what_to_do(tmp_path: Path) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(project / ".pilot-runs", outside)
    try:
        report = _ready(project, [doc], module_id)
        row = _row(report, "run_root")
        assert row["level"] == "STOP" and ".pilot-runs" in row["message"] and row["fix"], row
        assert report["status"] != "READY", report["status"]
    finally:
        _unlink(project / ".pilot-runs")
    clean = _ready(project, [doc], module_id)
    assert not [row for row in clean["checks"] if row["id"] == "run_root"]  # no link: no row


# --- 8.15: JSON with comments is no YAML --------------------------------------------------------------------------------------------------------

COMMENTED = ['// the settings of the project\n{"version": "3.0"}\n', '{\n  // the settings\n  "version": "3.0"\n}\n', '/* settings */ {"version": "3.0"}\n',
             '﻿// bom and a comment\n{"version": "3.0"}\n']


@pytest.mark.parametrize("text", COMMENTED)
def test_a_skillsrc_of_json_with_comments_is_no_yaml_and_the_fix_says_remove_the_comments(tmp_path: Path, text: str) -> None:
    from tools.ready import check_skillsrc, legacy_yaml_keys

    (tmp_path / ".skillsrc").write_text(text, encoding="utf-8", newline="")
    assert legacy_yaml_keys(tmp_path) is None
    [row] = check_skillsrc(tmp_path)[:1]
    assert row["level"] == "STOP" and "комментар" in row["message"] and "JSON" in row["message"], row
    assert "комментар" in row["fix"] and "//" in row["fix"] and ".yaml.bak" not in row["fix"], row


@pytest.mark.parametrize("text", COMMENTED)
def test_the_loader_says_the_same_for_a_skillsrc_with_comments(tmp_path: Path, text: str) -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    with pytest.raises(SkillsrcError) as stopped:
        parse_skillsrc_bytes(text.encode("utf-8"), project=tmp_path)
    assert stopped.value.code == "invalid_json" and "комментар" in stopped.value.fix and ".yaml.bak" not in stopped.value.fix, stopped.value.fix


def test_a_real_yaml_skillsrc_is_still_yaml_and_a_plain_invalid_json_is_not_comments(tmp_path: Path) -> None:
    from tools.ready import check_skillsrc, legacy_yaml_keys
    from tools.skillsrc_manifest import json_with_comments

    yaml = "version: 3.0\nlimits:\n  max: 1\n"
    (tmp_path / ".skillsrc").write_text(yaml, encoding="utf-8")
    assert legacy_yaml_keys(tmp_path) and "YAML" in check_skillsrc(tmp_path)[0]["message"]
    assert not json_with_comments(yaml)
    assert not json_with_comments('{"version": "3.0",}\n')  # a trailing comma is no comment
    assert not json_with_comments('{"url": "https://x//y", "note": "/* not a comment */"}')  # comment marks inside strings are text
    assert not json_with_comments("# a YAML comment\nversion: 3.0\n")
    assert json_with_comments('// c\n{"a": 1}') and json_with_comments('{"a": 1 /* c */}')


