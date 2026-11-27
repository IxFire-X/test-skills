"""A CSV of ``--cases`` with no case at all (only the header, or no row with a Name) is refused before a run — ZEPHYR_CSV_ROWS by scan and
by ready, with ``--docs`` or without: with no case the run has no requirement.  IMPORT_REQUIREMENTS_INVALID is then a defect of the package only."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cases_import import HEADER, _project, _scan
from tools import ready


def _runs(project: Path) -> list[Path]:
    runs = project / ".pilot-runs"
    return [path for path in runs.iterdir() if path.is_dir()] if runs.is_dir() else []


@pytest.mark.parametrize("docs", [None, ["docs/req.md"]])
def test_scan_refuses_a_csv_without_a_case_and_makes_no_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs) -> None:
    project = _project(tmp_path, HEADER + "\r\n")
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "req.md").write_text("# Требования\n\n1. combine склеивает строки.\n", encoding="utf-8")
    code, scan = _scan(project, monkeypatch, docs=docs)
    assert code == 2 and scan["reason"] == "ZEPHYR_CSV_ROWS" and _runs(project) == [], scan


def test_ready_refuses_a_csv_without_a_case(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, HEADER + "\r\n"), [], "cases-only-v1", cases="cases/zephyr.csv")
    row = next(item for item in report["checks"] if item["id"] == "cases")
    assert report["status"] == "BLOCKED" and row["level"] == "STOP" and row["message"].startswith("ZEPHYR_CSV_ROWS"), row


def test_the_import_requirements_refusal_names_a_defect_of_the_package() -> None:
    from tools.fixes import fix_for

    assert "дефект пакета" in fix_for("IMPORT_REQUIREMENTS_INVALID") and "ячейки CSV" not in fix_for("IMPORT_REQUIREMENTS_INVALID")


def test_the_empty_csv_is_named_by_the_approved_phrase(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, HEADER + "\r\n"), [], "cases-only-v1", cases="cases/zephyr.csv")
    row = next(item for item in report["checks"] if item["id"] == "cases")
    assert "в CSV нет ни одного кейса: нет строки с заполненным Name" in row["message"]  # approved by the user 27.11, word for word
