"""q13c: a file row of the step inputs carries the sha256 of the file, beside the digest of the input of the request the file stands for (an agent took that one for the source digest)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_openspec_selection import _scan
from tests.test_requirement_labels import DOC, _marker, _project
from tests.test_sections import _doc
from tools import step_size


def test_a_file_row_of_the_step_inputs_carries_the_sha256_of_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(10))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 10, capsys)[0] == 0
    rows = [row for row in step_size.stage_inputs(Path(scan["run_root"]), "context-marker:baseline")["files"] if not row.get("basis")]  # the document (v0.8: the code of the receipt comes as its map)
    assert rows and all(row.get("sha256") == "sha256:" + hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() for row in rows)
