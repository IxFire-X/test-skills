"""confined_output.is_reparse is the one check «a link or a reparse point» of the readers; project_inventory keeps its stricter own."""
from __future__ import annotations

import os
from types import SimpleNamespace

from tools import confined_output, project_inventory


def test_a_missing_path_is_no_link(tmp_path):
    assert confined_output.is_reparse(tmp_path / "missing") is False


def test_a_file_is_no_link(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    assert confined_output.is_reparse(path) is False


def test_the_reparse_bit_is_a_link(tmp_path, monkeypatch):
    details = os.stat(tmp_path, follow_symlinks=False)
    monkeypatch.setattr(os, "stat", lambda path, **_: SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400))
    assert confined_output.is_reparse(tmp_path) is True


def test_project_inventory_refuses_a_missing_path(tmp_path):
    assert project_inventory._is_reparse(tmp_path / "missing") is True
