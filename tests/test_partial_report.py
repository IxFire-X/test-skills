"""V6b: the «Итог» block of the report — the outcome of the review in a line, the warning for a missed control error, the numbers and the files of a
partial acceptance, the gaps by codes (the reviewer's texts stay in the files of the set, not in a report that leaves the perimeter)."""
from __future__ import annotations

import pytest


def _acceptance(**changes) -> dict:
    base = {"verdict_given": "ТРЕБУЕТ ДОРАБОТКИ", "outcome": "PARTIAL", "reason": None, "generated": 66, "accepted": 65, "removed": 1, "share": 20,
            "rejected_by_findings": 1, "rejected_with_group": 0,
            "coverage_gaps": [{"requirement": "CREQ-B1-001", "labels": [1], "codes": ["MISSING_STATE_CHECK"]}, {"requirement": "CREQ-B1-002", "labels": [1], "codes": ["MISSING_STATE_CHECK"]}],
            "rejected_cases": [{"case_id": "TC-B1-001", "label": 1, "with_group": False, "codes": ["MISSING_STATE_CHECK"]}],
            "files": {"csv": "published/TCDOC-x.r2.zephyr-scale.csv", "set": "published/TCDOC-x.r2.html, .md", "rejected": "published/TCDOC-x.r2.REJECTED-CASES.html, .md"}}
    return base | changes


def _lines(acceptance: dict | None, canaries: dict | None = None, isolation: str = "verified") -> str:
    from tools.run_report import _acceptance_lines

    return "\n".join(_acceptance_lines({"acceptance": acceptance, "review_canaries": canaries, "reviewer_isolation_state": isolation}))


def test_a_partial_acceptance_says_so_with_the_numbers_the_files_and_the_gaps() -> None:
    text = _lines(_acceptance(), {"status": "ALL_CAUGHT", "seeded": 3, "caught": 3})
    assert text.startswith("## Итог\n\n**ПРИНЯТ ЧАСТИЧНО: 65 из 66 кейсов.** 1 кейс не дорабатывался.\n\n| | Кейсов |\n|---|---|\n| Сгенерировано | 66 |\n"
                           "| Принято в первом круге ревью | 65 |\n| Не принято (вынесено отдельно) | 1 |\n")
    assert "Ревью: независимое (INDEPENDENT), 1 круг: весь набор." in text and "Внимание" not in text
    assert "Пробелы покрытия: 2 требования остались без принятого кейса" in text
    assert "- Набор для импорта в Zephyr: published/TCDOC-x.r2.zephyr-scale.csv (65 кейсов)" in text and "- Не принятые кейсы — не импортировать: published/TCDOC-x.r2.REJECTED-CASES.html, .md" in text
    assert "| CREQ-B1-001 | Его покрывали только отклонённые кейсы: ТК-1 | MISSING\\_STATE\\_CHECK (текст — в файле набора) |" in text


def test_the_number_of_cases_that_were_not_reworked_is_declined() -> None:
    assert "2 кейса не дорабатывались." in _lines(_acceptance(removed=2, accepted=64))
    assert "5 кейсов не дорабатывались." in _lines(_acceptance(removed=5, accepted=61))


def test_a_missed_control_error_is_a_warning_under_the_line_of_the_outcome() -> None:
    text = _lines(_acceptance(), {"status": "WEAK", "seeded": 3, "caught": 2})
    assert ("**ПРИНЯТ ЧАСТИЧНО: 65 из 66 кейсов.** 1 кейс не дорабатывался.\n\n> **Внимание: ревьюер пропустил 1 из 3 контрольных ошибок**, которые код подложил "
            "для проверки его внимательности.\n> Ревью могло пропустить и настоящие ошибки — просмотрите принятые кейсы внимательнее перед импортом.\n") in text
    assert "пропустил 3 из 3" in _lines(_acceptance(), {"status": "UNRELIABLE", "seeded": 3, "caught": 0})


def test_the_other_outcomes_are_a_line_each() -> None:
    whole = _acceptance(outcome="ACCEPTED_WHOLE", accepted=66, removed=0, verdict_given="ПРИНЯТО")
    assert _lines(whole) == "## Итог\n\n**ПРИНЯТ: 66 из 66 кейсов.**\n"
    assert _lines(_acceptance(outcome="REWORK", reason="NO_CASES", accepted=0)) == "## Итог\n\n**ТРЕБУЕТ ДОРАБОТКИ:** замечание ревьюера относится ко всему набору.\n"
    over = _lines(_acceptance(outcome="REWORK", reason="OVER_SHARE", accepted=0, removed=14))
    assert "**ТРЕБУЕТ ДОРАБОТКИ:** отклонено 14 из 66 кейсов — больше допустимого (20 %)." in over and "Файлы для работы" not in over


def test_a_review_in_the_same_session_is_named_so_and_a_run_without_an_answer_has_no_block() -> None:
    assert "Ревью: в той же сессии (SELF), 1 круг: весь набор." in _lines(_acceptance(), isolation="independence_unverified")
    assert _lines(None) == ""


def test_no_gap_line_says_there_is_none() -> None:
    text = _lines(_acceptance(coverage_gaps=[]))
    assert "Пробелов покрытия нет" in text and "## Пробелы покрытия" not in text
