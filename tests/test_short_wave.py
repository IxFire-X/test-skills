"""The short wave of the v0.8 campaign (handoff/v08/short-wave-fixes-plan.md): a CSV with no case says why and how to get one; a --project
that is no folder is named first, not as a missing document."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cases_import import HEADER, _project, _row, _scan
from tools import ready
from tools.fixes import FIXES


def test_scan_of_a_csv_without_a_case_has_the_fix_and_the_human_of_the_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _envelope

    code, scan = _scan(_project(tmp_path, HEADER + "\r\n"), monkeypatch)
    said = _envelope(scan)
    assert code == 2 and said["fix"] != FIXES["ZEPHYR_CSV_ROWS"] and "Zephyr заново" in said["fix"] and "Zephyr заново" in said["human"], said


def test_ready_of_a_csv_without_a_case_leads_with_the_human(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, HEADER + "\r\n"), [], "cases-only-v1", cases="cases/zephyr.csv")
    row = next(item for item in report["checks"] if item["id"] == "cases")
    assert row["message"].startswith("В файле кейсов нет ни одного кейса") and "Zephyr заново" in row["fix"], row


def test_an_empty_cell_keeps_the_fix_of_the_cells_and_no_human(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, _row(expected="")), [], "cases-only-v1", cases="cases/zephyr.csv")
    row = next(item for item in report["checks"] if item["id"] == "cases")
    assert row["message"].startswith("ZEPHYR_CSV_ROWS: ") and row["fix"] == FIXES["ZEPHYR_CSV_ROWS"], row


@pytest.mark.parametrize("docs", [None, ["openspec/changes/x"]])
def test_scan_of_a_project_that_is_not_there_is_project_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs) -> None:
    from tools import run_pipeline

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    args = run_pipeline.build_parser().parse_args(["scan", "--project", str(tmp_path / "absent"), *[f"--docs={path}" for path in docs or []]])
    assert run_pipeline.cmd_scan(args) == 2 and printed[-1]["reason"] == "PROJECT_NOT_FOUND", printed


def test_ready_of_a_project_that_is_not_there_is_one_row(tmp_path: Path) -> None:
    report = ready.check(tmp_path / "absent", ["docs/a.md"], "cases-only-v1")
    assert [row["id"] for row in report["checks"] if row["level"] == "STOP"] == ["project"] and report["status"] == "BLOCKED", report


def test_status_keeps_the_lines_not_applied_to_the_end_of_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """2а: a change only — «Не применено» in status after the requirements step and, once, after the parts are merged into the baseline."""
    import json

    from tests.test_openspec_marker import _part, _prepare
    from tests.test_openspec_selection import ADD, ADDED, _project, _scan as _scan_docs
    from tests.test_task_docs_context import _write
    from tools import api
    from tools.run_scan import durable_status

    project = _project(tmp_path)
    _write(project, ADD, ADDED + "### Requirement: Create\nCreate two owners.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n")
    root = Path(_scan_docs(project, monkeypatch, ["openspec/changes/add-search"])["run_root"])
    not_applied = lambda: [line for line in durable_status(project.resolve(), root, stage="status")["warnings"] if line.startswith("Не применено:")]  # noqa: E731
    _prepare(root, "context-marker:requirements", capsys)
    assert len(not_applied()) == 1
    slot = Path(_prepare(root, "context-marker:batch-001", capsys)["answer_slot"])
    slot.write_text(json.dumps(_part(["openspec/specs/owners/spec.md — the base of the change"])), encoding="utf-8")
    assert api.main(["submit", "--run-root", str(root), "--stage", "context-marker:batch-001"]) == 0, capsys.readouterr().out
    assert len(not_applied()) == 1


# the words the user approved (texts-approval-20261127 p. 13-15, «Утверждаю»), word for word
EMPTY_HUMAN = ("В файле кейсов нет ни одного кейса — в нём только заголовки колонок. Выгрузите кейсы из Zephyr заново "
               "(проверьте папку и фильтр выгрузки) и скажите агенту, когда файл будет готов.")
ABSENT_HUMAN = "Папки проекта по этому пути нет. Проверьте путь к проекту и скажите агенту правильный."


def test_the_approved_words_of_an_empty_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _envelope

    project = _project(tmp_path, HEADER + "\r\n")
    row = next(item for item in ready.check(project, [], "cases-only-v1", cases="cases/zephyr.csv")["checks"] if item["id"] == "cases")
    assert row["message"] == EMPTY_HUMAN + " (ZEPHYR_CSV_ROWS: в CSV нет ни одного кейса: нет строки с заполненным Name)"
    assert _envelope(_scan(project, monkeypatch)[1])["human"] == EMPTY_HUMAN


def test_the_approved_words_of_a_project_that_is_not_there(tmp_path: Path) -> None:
    from tools.fixes import human_for

    row = ready.check(tmp_path / "absent", [], "cases-only-v1")["checks"][-1]
    assert row["message"] == f"{ABSENT_HUMAN} (PROJECT_NOT_FOUND: проект не найден: {(tmp_path / 'absent').resolve()} — такой папки нет)"
    assert human_for("PROJECT_NOT_FOUND") == ABSENT_HUMAN


def test_the_approved_tail_of_an_incomplete_scope() -> None:
    from tools.openspec_scope import lines

    said = lines("c", ["owners"], [("ADDED", "owners", "A", "a")], [("MODIFIED", "owners", "B", "b", "d.md", 1, "x")], [], "")
    assert next(line for line in said if line.startswith("Объём неполный:")) == (
        "Объём неполный: применено 1 из 2 требований изменения c; не взято 1 — причины в строках «Не применено» (scan, status, report.md); "
        "вопросы аналитику — в questions-for-analyst.md после report")
