"""C1 (Б6, Б9): one print of a JSON answer and one reading of a JSONL file of the run, in ``tools.json_cli``."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from tools import json_cli


class _Gone(io.StringIO):
    def write(self, text: str) -> int:
        raise BrokenPipeError


def test_a_reader_that_went_away_fails_no_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", _Gone())
    json_cli.emit_json({"status": "ok"})


def test_a_damaged_line_never_gives_a_spent_retry_back(tmp_path: Path) -> None:
    (tmp_path / "refusals.jsonl").write_bytes(b'{"stage": "tc-reviewer:canonical", "spent": true}\n{broken\n')
    assert json_cli.spent_stages(tmp_path) == {"tc-reviewer:canonical"}
