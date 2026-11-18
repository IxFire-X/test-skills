"""q14: a marker with far more requirements than its document has numbers is no document too large — the refusal and the note say so, the run is closed by one command,
and the step of the marker reads the numbers of the document as the code counts them."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_openspec_selection import _scan
from tests.test_requirement_labels import DOC, _marker, _project
from tests.test_sections import _doc, _plan
from tools import run_pipeline, step_size
from tools.build_context import numbering_view
from tools.pilot_state import left_attempt


def _refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], numbers: int, rows: int) -> tuple[dict, dict]:
    project = _project(tmp_path, _doc(numbers))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, rows, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 2 and out["code"] == "PLAN_REVIEW_TOO_LARGE", out
    return scan, out


def test_a_marker_with_far_more_rows_than_numbers_refuses_the_plan_and_names_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _scan_, out = _refused(tmp_path, monkeypatch, capsys, 35, 62)
    assert "разметка дала 62 требований при 35 номерах" in out["message"] and out["human"].startswith("Разметка требований получилась раздутой")
    assert "--abandon LEFT_BEFORE_REVIEW" in out["fix"] and "--docs" not in out["fix"]


def test_the_run_after_that_refusal_is_closed_by_the_command_the_fix_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    scan, _out = _refused(tmp_path, monkeypatch, capsys, 35, 62)
    assert run_pipeline.main(["finish", "--project", str(tmp_path / "project"), "--run", scan["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"]) == 0
    assert left_attempt(Path(scan["run_root"]))  # the run is left: a plain scan of the request starts a new one


def test_a_document_that_is_really_too_large_keeps_its_text_and_gets_the_closing_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(25, 25, 20))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 70, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 2 and "разметка дала" not in out["message"] and out["human"].startswith("Документ слишком большой")
    assert f'--docs "{DOC}#' in out["fix"] and "--abandon LEFT_BEFORE_REVIEW" in out["fix"]


def test_rows_under_one_and_a_fifth_of_the_numbers_are_not_a_bloated_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _scan_, out = _refused(tmp_path, monkeypatch, capsys, 52, 61)  # 61 < 1.2 × 52, and over the budget of the step
    assert "разметка дала" not in out["message"]


def test_a_marker_over_the_checked_size_with_extra_rows_says_so_in_the_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(35))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 42, capsys)[0] == 0
    code, out = _plan(scan, capsys)
    assert code == 0 and "ВНИМАНИЕ: разметка дала 42 требований при 35 номерах" in json.dumps(out, ensure_ascii=False)


def test_the_numbering_view_lists_the_numbers_and_the_lines_without_one(tmp_path: Path) -> None:
    view = numbering_view({DOC: _doc(10)}, _project(tmp_path, _doc(10)))
    assert view and "пунктов с номерами 10: 1.1.1…1.1.10" in view and view.endswith("строки без номера: 3\n") and len(view.encode("utf-8")) < 1024


def test_there_is_no_numbering_view_below_eight_numbers_nor_for_openspec(tmp_path: Path) -> None:
    project = _project(tmp_path, _doc(7))
    assert numbering_view({DOC: _doc(7)}, project) is None
    assert numbering_view({"openspec/specs/a/spec.md": _doc(10)}, project) is None


def test_the_step_of_the_marker_reads_the_numbering_view_among_its_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, _doc(10))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 10, capsys)[0] == 0
    [view] = [row for row in step_size.stage_inputs(Path(scan["run_root"]), "context-marker:baseline")["files"] if row.get("basis") == "номера"]
    assert view["path"].endswith(".md") and view["bytes"] < 1024
