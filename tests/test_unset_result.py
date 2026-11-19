"""q3d: a requirement the document marks as having no expected result gets no case — and keeps its question for the analyst."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_scope_pause import _published_plan_of, _submit_plan
from tests.test_unclear_questions import FIRST, _marked_run
from tools import scope_pause

TBD = "REQ-1 Размер страницы — TBD."
DETAIL = "REQ-1 Отказ даёт 400. Неподтверждённое условие: код отказа не описан."
ROWS = [{"source_requirement_id": "SREQ-1", "text": TBD}, {"source_requirement_id": "SREQ-2", "text": "Оплата картой."}]


def test_a_requirement_marked_unset_is_left_out_of_the_plan_and_keeps_its_question(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _marked_run(tmp_path, TBD)
    assert _submit_plan(capsys, root)[0] == 0
    plan = _published_plan_of(root, attempt_id)
    assert plan["out_of_scope"] == [{"source_requirement_id": FIRST, "reason": scope_pause.UNCLEAR_REASON}]
    assert FIRST not in [item for batch in plan["batches"] for item in batch["owned_source_requirement_ids"]]
    assert [(row["source_requirement_id"], row.get("no_case")) for row in scope_pause.unclear_rows(root, attempt_id)] == [(FIRST, True)]


def test_a_mark_of_a_detail_of_a_set_behaviour_keeps_the_case(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _marked_run(tmp_path, DETAIL)
    assert _submit_plan(capsys, root)[0] == 0
    assert "out_of_scope" not in _published_plan_of(root, attempt_id)


def test_a_requirement_with_the_verb_to_refine_keeps_its_case() -> None:
    assert scope_pause.cut_rows([dict(ROWS[0], text="REQ-1 Пользователь может уточнить поиск фильтрами."), ROWS[1]], set()) == []


def test_a_requirement_with_a_case_of_the_human_is_not_cut() -> None:
    assert scope_pause.cut_rows(ROWS, set(), {"cases": [{"source_requirement_ids": ["SREQ-1"]}]}) == []


def test_a_document_marked_unset_in_every_requirement_is_not_cut() -> None:
    assert scope_pause.cut_rows([dict(row, text=TBD) for row in ROWS], set()) == []


def test_the_report_says_in_one_line_how_many_requirements_have_no_case_and_why(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import write_report

    root, _attempt_id = _marked_run(tmp_path, TBD)
    assert _submit_plan(capsys, root)[0] == 0
    write_report(root.parent.parent, root.name)
    line = f"- без кейсов: 1 требование ({FIRST}) — ожидаемый результат не задан, в документе помечено неясным; вопросы аналитику — questions-for-analyst.md; прогон не останавливался"
    assert line in (root / "report.md").read_text(encoding="utf-8")
    assert json.loads((root / "report.json").read_text(encoding="utf-8"))["unconfirmed"]["without_case"] == [FIRST]


def test_a_mark_the_marker_worded_away_is_found_in_the_line_of_the_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_openspec_selection import _scan
    from tests.test_requirement_labels import DOC, _marker, _project
    from tests.test_sections import _plan

    scan = _scan(_project(tmp_path, "1.1.1. Оплата картой.\n2.1.1. Размер страницы — TBD.\n"), monkeypatch, [DOC])
    assert _marker(tmp_path / "project", scan, 2, capsys)[0] == 0  # the rows say «Требование 2» without the mark
    assert _plan(scan, capsys)[0] == 0
    plan = _published_plan_of(Path(scan["run_root"]), scan["attempt_id"])
    assert plan["out_of_scope"] == [{"source_requirement_id": "SREQ-0002", "reason": scope_pause.UNCLEAR_REASON}]
