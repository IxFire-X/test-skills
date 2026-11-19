"""q3g: what the tester reads says which step the model did not take."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_cases_import import _project, _scan
from tools import api
from tools.run_report import build_report, render_markdown

STAGE = "context-marker:requirements"


def _scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project = _project(tmp_path)
    return project, Path(_scan(project, monkeypatch)[1]["run_root"])


def test_prepare_of_a_step_the_code_answers_says_no_model_is_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _project_dir, run_root = _scanned(tmp_path, monkeypatch)
    capsys.readouterr()
    assert api.main(["prepare", "--run-root", str(run_root), "--stage", STAGE]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["model_call"] is False and result["note"] == "ответа модели не нужно: требования собрал код"


def test_the_table_of_calls_marks_a_step_of_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, run_root = _scanned(tmp_path, monkeypatch)
    assert api.main(["prepare", "--run-root", str(run_root), "--stage", STAGE]) == 0
    report, _rows = build_report(project, run_root.name)
    assert f"| {STAGE} (кодом) |" in render_markdown(report)
