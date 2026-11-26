"""q13d: the numbers of the documents that the step of the marker reads are counted in the documents as the receipts hold them (by their digests, never through a link); a file name with a control character is no text of the report."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_openspec_selection import _scan
from tests.test_partial_report import _acceptance
from tests.test_requirement_labels import DOC, NUMBERED, _marker, _project
from tests.test_sections import _doc
from tests.test_stage_steps import _junction
from tools import pilot_state, step_size
from tools.run_report import _acceptance_lines, build_report


def _numbers(scan: dict) -> list[dict]:
    return [row for row in step_size.stage_inputs(Path(scan["run_root"]), "context-marker:baseline")["files"] if row.get("basis") == "номера"]


def _after_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[Path, dict]:
    """A marker answered; the receipts are then read as recorded, so that a change of the document after the scan is met by the read of the document itself."""
    project = _project(tmp_path, _doc(10))
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 10, capsys)[0] == 0 and _numbers(scan)
    read = pilot_state.read_context_selection
    monkeypatch.setattr(pilot_state, "read_context_selection", lambda root, attempt, digest, *, live=True: read(root, attempt, digest, live=False))
    return project, scan


def test_a_document_changed_after_the_scan_gets_no_numbering_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project, scan = _after_the_scan(tmp_path, monkeypatch, capsys)
    (project / DOC).write_text(_doc(12), encoding="utf-8")
    assert _numbers(scan) == []


def test_a_document_that_became_a_link_after_the_scan_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project, scan = _after_the_scan(tmp_path, monkeypatch, capsys)
    (project / DOC).unlink()
    (project / "elsewhere").mkdir()
    _junction(project / DOC, project / "elsewhere")
    assert _numbers(scan) == []


def test_a_document_name_with_a_line_break_is_no_path_of_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, NUMBERED)
    scan = _scan(project, monkeypatch, [DOC])
    assert _marker(project, scan, 9, capsys)[0] == 0
    monkeypatch.setattr(pilot_state, "requirement_label_counts", lambda *args: {"documents": [{"path": "docs/a.md\n# Fake", "labels": 12}, {"path": DOC, "labels": 3}], "requirements": 9})
    report, _warnings = build_report(project, scan["run_id"])
    assert report["requirement_labels"]["documents"] == [{"path": None, "labels": 12}, {"path": DOC, "labels": 3}]


def test_a_document_name_with_a_line_break_makes_one_line_of_the_warning() -> None:
    report = {"acceptance": _acceptance(), "review_canaries": None, "reviewer_isolation_state": "verified", "quarantine": [], "counts": {"automation": False},
              "requirement_labels": {"documents": [{"path": "docs/a.md\n# Fake", "labels": 12}], "requirements": 9}}
    text = "\n".join(_acceptance_lines(report))
    assert "docs/a.md # Fake номеров 12" in text and "\n# Fake" not in text
