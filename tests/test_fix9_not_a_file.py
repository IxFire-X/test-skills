"""fix9: a file of the project that is no regular file (the device ``nul`` on Windows, a FIFO on Linux) is left out of the inventory unread; ``scan`` goes on."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_context_filter import MODULE, _project, _receipts, _scan, _selected_paths


@pytest.fixture
def not_a_file(tmp_path: Path) -> Iterator[str]:
    """The project of the fixture with a file that is no regular file beside its sources; the path of it, relative to the project."""
    project = _project(tmp_path)
    name = f"{MODULE}/src/nul" if os.name == "nt" else f"{MODULE}/src/pipe"
    target = project / name
    if os.name == "nt":
        with open("\\\\?\\" + str(target), "wb"):  # without the prefix the name is the device
            pass
    elif hasattr(os, "mkfifo"):
        os.mkfifo(target)
    else:
        pytest.skip("neither the device nul nor a FIFO can be made here")
    yield name
    if os.name == "nt":
        os.unlink("\\\\?\\" + str(target))  # a plain unlink does not find it


def test_scan_goes_on_and_leaves_the_file_out_of_the_inventory_and_the_model_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, not_a_file: str) -> None:
    code, scan = _scan(tmp_path / "project", monkeypatch)
    assert code == 0, scan
    inventory = json.loads(next((Path(scan["run_root"]) / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    assert not_a_file not in {row["project_path"] for row in inventory["files"]}
    assert not_a_file not in _selected_paths(_receipts(scan))


def test_the_file_is_among_the_exclusions_with_its_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, not_a_file: str) -> None:
    code, scan = _scan(tmp_path / "project", monkeypatch)
    inventory = json.loads(next((Path(scan["run_root"]) / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    excluded = {row["project_path"]: row["reason_code"] for row in inventory["exclusions"]}
    assert code == 0 and excluded.get(not_a_file) == "UNREADABLE", excluded
