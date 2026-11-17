"""q3: a place the document marks unclear (or the marker asks about) is a question for the analyst — and the run does not stop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_marker_parts import _marker, _requirements, _run
from tools import pilot_state, scope_pause

FIRST = "SREQ-batch-a-001"
ASK = f"{FIRST} вопрос (docs/feature.md:3) — не сказано, какой код вернёт отказ — какой код ответа должен вернуть отказ?"
GAP = "REQ-1 сценарий (docs/feature.md#pay) — нет наблюдаемого канала — блокирует проверку оплаты — как проверить оплату?"


def _marked_run(tmp_path: Path, first: str, warnings: list[str] | None = None):
    """A run whose marker has two requirements, the first with the given text, and the given warnings."""
    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    rows = [dict(row, text=text) for row, text in zip(_requirements(), [first, "REQ-2 Возврат средств клиенту."])]
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker(["src/part0.py — x"], warnings or [], requirements=rows))
    return root, attempt_id


@pytest.mark.parametrize("text", [
    "REQ-1 Отказ даёт 400. Неподтверждённое условие: код отказа не описан.",
    "REQ-1 Размер страницы — TBD.",
    "REQ-1 Формат даты требует уточнения.",
    "REQ-1 Точный код контракт не задаёт.",
    "REQ-1 Лимит — открытый вопрос.",
])
def test_a_marked_requirement_is_a_question_and_the_run_does_not_stop(tmp_path: Path, text: str) -> None:
    root, attempt_id = _marked_run(tmp_path, text, warnings=[GAP])  # even with a gap that names it
    assert scope_pause.scope_state(root, attempt_id)["pending"] is False
    assert [row["source_requirement_id"] for row in scope_pause.unclear_rows(root, attempt_id)] == [FIRST]


def test_a_bare_not_set_is_no_mark(tmp_path: Path) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Поле, у которого не задан `dump_default`, в результате отсутствует.")
    assert scope_pause.unclear_rows(root, attempt_id) == []


def test_a_gap_on_an_unmarked_requirement_still_pauses(tmp_path: Path) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Оплата заказа картой.", warnings=[GAP])
    assert scope_pause.scope_state(root, attempt_id)["pending"] is True


def test_a_three_field_question_of_the_marker_asks_and_does_not_pause(tmp_path: Path) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Оплата заказа картой.", warnings=[ASK])
    assert scope_pause.scope_state(root, attempt_id)["pending"] is False
    assert [row["question"] for row in scope_pause.unclear_rows(root, attempt_id)] == ["какой код ответа должен вернуть отказ?"]


@pytest.mark.parametrize("warning", [
    "SREQ-nope вопрос (docs/feature.md:3) — не сказано — что вернуть?",  # the requirement is not in the marker
    f"{FIRST} вопрос (docs/feature.md:3) — не сказано — что вернуть",  # no question mark
    f"{FIRST} — не сказано — что вернуть?",  # no word «вопрос»
])
def test_a_three_field_warning_the_code_cannot_check_is_no_question(tmp_path: Path, warning: str) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Оплата заказа картой.", warnings=[warning])
    assert scope_pause.unclear_rows(root, attempt_id) == []


def test_the_question_of_the_marker_and_the_mark_on_one_requirement_are_one_block(tmp_path: Path) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Размер страницы — TBD.", warnings=[ASK])
    rows = scope_pause.unclear_rows(root, attempt_id)
    assert len(rows) == 1 and rows[0]["question"].endswith("отказ?")


def test_the_requirement_the_human_left_out_gets_no_question_of_its_own(tmp_path: Path) -> None:
    root, attempt_id = _marked_run(tmp_path, "REQ-1 Размер страницы — TBD.")
    answer = {"decision": "exclude", "excluded": [FIRST], "reason": "не в этом релизе"}
    scope_pause.commit_decision(root, attempt_id, scope_pause.prepare_decision(root, attempt_id, answer))
    assert scope_pause.unclear_rows(root, attempt_id) == []


def test_the_questions_file_holds_the_text_and_the_report_only_the_number(tmp_path: Path) -> None:
    from tools.run_report import write_report

    root, _attempt_id = _marked_run(tmp_path, "REQ-1 Размер страницы — TBD.")
    write_report(root.parent.parent, root.name)
    questions = (root / "questions-for-analyst.md").read_text(encoding="utf-8")
    assert "## Неясности в требованиях" in questions and "Размер страницы" in questions and "какое поведение требуется?" in questions
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert report["unconfirmed"] == {"count": 1, "requirements": [FIRST]}
    assert "Размер страницы" not in json.dumps(report, ensure_ascii=False) + (root / "report.md").read_text(encoding="utf-8")


def test_a_run_without_marks_has_no_unconfirmed_entry(tmp_path: Path) -> None:
    from tools.run_report import build_report

    root, _attempt_id = _marked_run(tmp_path, "REQ-1 Оплата заказа картой.")
    report, _warnings = build_report(root.parent.parent, root.name)
    assert "unconfirmed" not in report
