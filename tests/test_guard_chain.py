"""The guard of confined_output, faster (v0.8): one stat gives the link flag and the identity of a directory (Ш1).  A link at any place
of the chain and a project root swapped between two reads of one command are refused as before."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, read_confined_bytes
from tools.run_journal import closure_cache


def _link(link: Path, target: Path, kind: str) -> None:
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("a junction is Windows only")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        link.symlink_to(target, target_is_directory=target.is_dir())


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "root" / "mid").mkdir(parents=True)
    (project / "root" / "mid" / "leaf.json").write_bytes(b"{}")
    return project


def _leaf(project: Path) -> Path:
    return project / "root" / "mid" / "leaf.json"


@pytest.mark.parametrize("kind", ["junction", "symlink"])
@pytest.mark.parametrize("place", ["root", "root/mid"])
def test_a_link_at_a_directory_of_the_chain_is_refused_on_read_and_write(tmp_path: Path, place: str, kind: str) -> None:
    project = _project(tmp_path)
    os.rename(project / place, tmp_path / "real")
    _link(project / place, tmp_path / "real", kind)
    with pytest.raises(OutputConfinementError):
        read_confined_bytes(project, project / "root", _leaf(project))
    with pytest.raises(OutputConfinementError):
        atomic_write_confined_bytes_at_root(project, project / "root", _leaf(project), b"[]")


def test_a_leaf_that_is_a_link_is_refused_on_read_and_write(tmp_path: Path) -> None:
    project = _project(tmp_path)
    os.rename(_leaf(project), tmp_path / "real.json")
    _link(_leaf(project), tmp_path / "real.json", "symlink")
    with pytest.raises(OutputConfinementError):
        read_confined_bytes(project, project / "root", _leaf(project))
    with pytest.raises(OutputConfinementError):
        atomic_write_confined_bytes_at_root(project, project / "root", _leaf(project), b"[]")
    assert (tmp_path / "real.json").read_bytes() == b"{}"


def test_a_project_root_swapped_for_a_junction_between_two_reads_of_one_command_is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with closure_cache():
        assert read_confined_bytes(project, project / "root", _leaf(project)) == b"{}"
        os.rename(project, tmp_path / "old")
        _link(project, tmp_path / "old", "junction" if os.name == "nt" else "symlink")
        with pytest.raises(OutputConfinementError):
            read_confined_bytes(project, project / "root", _leaf(project))

