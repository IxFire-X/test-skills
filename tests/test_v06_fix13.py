"""v0.6 fix13: the core writes one file in the root of a project — ``.skillsrc`` — and nothing else; ``init`` writes it through the core."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import confined_output
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_in_project as write_in_project


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_it_writes_a_new_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_in_project(project, ".skillsrc", b"one")
    assert (project / ".skillsrc").read_bytes() == b"one" and [path.name for path in project.iterdir()] == [".skillsrc"]


def test_it_replaces_the_file_that_lies_there(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".skillsrc").write_bytes(b"old")
    write_in_project(project, ".skillsrc", b"new")
    assert (project / ".skillsrc").read_bytes() == b"new" and [path.name for path in project.iterdir()] == [".skillsrc"]


@pytest.mark.parametrize("name", ["evil.py", "sub/.skillsrc", "..", ""])
def test_a_name_that_is_not_on_the_list_is_refused(tmp_path: Path, name: str) -> None:
    project = _project(tmp_path)
    with pytest.raises(OutputConfinementError):
        write_in_project(project, name, b"x")
    assert list(project.iterdir()) == []


@pytest.mark.parametrize("name", ["sub/x", "sub\\x"])
def test_a_name_with_a_separator_is_refused_even_when_the_list_names_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(confined_output, "_PROJECT_ROOT_FILES", frozenset({name}))
    with pytest.raises(OutputConfinementError):
        write_in_project(project, name, b"x")
    assert list(project.iterdir()) == []


def test_a_directory_as_the_target_is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".skillsrc").mkdir()
    with pytest.raises(OutputConfinementError):
        write_in_project(project, ".skillsrc", b"x")
    assert [path.name for path in project.iterdir()] == [".skillsrc"] and (project / ".skillsrc").is_dir()


def _link(link: Path, target: Path) -> None:
    """``link`` leads to ``target``: a symbolic link, or a junction for a directory on Windows."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if os.name != "nt" or not target.is_dir():
            pytest.skip(f"no link can be made here: {error}")
        from tests.test_stage_steps import _junction

        _junction(link, target)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_a_link_as_the_target_is_refused_and_leaves_what_it_leads_to(tmp_path: Path, kind: str) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f").write_bytes(b"keep")
    _link(project / ".skillsrc", outside / "f" if kind == "file" else outside)
    with pytest.raises(OutputConfinementError):
        write_in_project(project, ".skillsrc", b"x")
    assert (outside / "f").read_bytes() == b"keep" and [path.name for path in outside.iterdir()] == ["f"]


def test_a_failed_write_leaves_no_temporary_file_and_the_file_that_lies_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    (project / ".skillsrc").write_bytes(b"old")

    def no_space(_descriptor: int) -> None:
        raise OSError("no space left")

    monkeypatch.setattr(os, "fsync", no_space)
    with pytest.raises(OSError):
        write_in_project(project, ".skillsrc", b"new")
    assert (project / ".skillsrc").read_bytes() == b"old" and [path.name for path in project.iterdir()] == [".skillsrc"]


# ---- on the public path: init over a .skillsrc that is a link -------------------------------------------------------------------------------------

def test_init_does_not_write_over_a_skillsrc_that_is_a_link(tmp_path: Path) -> None:
    from tests.test_skillsrc_human_keys import _init_cli, _project as _configured_project
    from tools.init_skillsrc import ensure_skillsrc

    project = _configured_project(tmp_path)
    assert ensure_skillsrc(project, {"module:root:test.framework": "pytest"}, write=True)["status"] == "created"
    document = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    document["modules"][0]["test"]["interpreter"] = "tools/other/python.exe"  # a derived key gone stale: init proposes a replacement
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    (project / ".skillsrc").unlink()
    _link(project / ".skillsrc", outside)
    before = outside.read_bytes()
    proposal = _init_cli(project, "--write")[1]["proposal"]
    (project / "evidence").mkdir()
    approval = project / "approval.json"
    approval.write_text(json.dumps({"approval_id": "a1", "original_digest": proposal["original_digest"], "proposed_digest": proposal["proposed_digest"]}), encoding="utf-8")
    code, report = _init_cli(project, "--write", "--replacement-approval", str(approval), "--immutable-evidence-dir", str(project / "evidence"))
    assert (code, report["status"], report["errors"]) == (1, "error", ["write_error"]) and outside.read_bytes() == before and (project / ".skillsrc").is_symlink(), report
    assert "не пишет поверх ссылок" in report["message"] and "target is not a regular file" in report["message"] and "обычным файлом" in report["fix"], report  # told why, in words and by the refusal of the core


def test_a_document_that_is_no_skillsrc_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    from tests.test_skillsrc_human_keys import _project as _configured_project
    from tools.init_skillsrc import atomic_write_skillsrc
    from tools.skillsrc_manifest import SkillsrcError

    project = _configured_project(tmp_path)
    (project / ".skillsrc").write_bytes(b"old")
    with pytest.raises(SkillsrcError):
        atomic_write_skillsrc(project, project / ".skillsrc", {"not": "a skillsrc"}, "fingerprint", b"old")
    assert (project / ".skillsrc").read_bytes() == b"old" and not [path for path in project.iterdir() if path.name.endswith(".tmp")]
