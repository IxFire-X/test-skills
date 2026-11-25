"""v0.8 «текстовые контрольные ошибки» (R&D-13): steps without machine checks get control errors tied to the requirement text.

Five operators (outcome, state, literal, number, data) on expectations without assertions, as many harmless twins on other cases;
a text error counts only when a BLOCKING finding stands on its place (or an edit of its text); a BLOCKING on a twin is a false alarm
and rejects no case; missed text errors make a review WEAK, never UNRELIABLE (v0.8: not the acceptance).  The exact lines for the
tester wait for the user's approval and are not pinned here."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import review_canaries as rc

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
REQ = ("Владелец создан, если телефон из 10 цифр; при ошибке форма создания остаётся открытой и показано «Неверный телефон». "
       "Кнопка «Сохранить». Длина имени до 30 символов.")
DIGEST = "sha256:" + "0" * 64


def _case(n: int, text: str, data: str = "—", assertions: list | None = None) -> dict:
    return {"case_id": f"TC-{n}", "display_order": n, "requirement_ids": ["CREQ-1"],
            "steps": [{"step_id": f"STEP-{n}", "display_order": 1, "test_data": data,
                       "expectations": [{"expectation_id": f"EXP-{n}", "display_order": 1, "text": text, "assertions": assertions or []}]}]}


def _doc(*cases: dict, requirement: str = REQ) -> dict:
    return {"requirements": [{"requirement_id": "CREQ-1", "text": requirement}], "test_cases": list(cases)}


def _made(cls: str, case: dict, requirement: str = REQ) -> list[str]:
    texts, literals = rc._linked(_doc(case, requirement=requirement))
    return [row["mutated"] for row, _apply in rc._text_options(cls, case, DIGEST, texts, literals)]


def test_number_shifts_only_a_number_the_requirement_has() -> None:
    assert _made("number", _case(1, "Принят телефон из 10 цифр, код 77.")) == ["Принят телефон из 11 цифр, код 77."]


def test_literal_takes_another_literal_of_the_requirements() -> None:
    assert _made("literal", _case(1, "Показано «Неверный телефон».")) == ["Показано «Сохранить»."]


def test_outcome_flips_and_keeps_the_capital_letter() -> None:
    assert _made("outcome", _case(1, "Создан владелец Anna.")) == ["Не создан владелец Anna."]


def test_outcome_leaves_a_verb_before_without() -> None:
    assert _made("outcome", _case(1, "Владелец создан без ошибки формы.")) == []


def test_state_drops_the_clause_the_requirement_names() -> None:
    assert _made("state", _case(1, "Показано «Неверный телефон»; форма создания остаётся открытой.")) == ["Показано «Неверный телефон»."]


def test_state_keeps_a_clause_the_requirement_does_not_name() -> None:
    assert _made("state", _case(1, "Показано «Неверный телефон»; форма создания остаётся открытой."), requirement="Телефон из 10 цифр.") == []


def test_data_shifts_a_number_of_the_test_data_of_a_manual_step() -> None:
    assert _made("data", _case(1, "Имя принято.", data="имя из 30 букв A")) == ["имя из 31 букв A"]


def test_an_expectation_with_checks_gets_no_text_error() -> None:
    checked = _case(1, "Владелец создан, телефон из 10 цифр.", assertions=[{"assertion_id": "ASSERT-1"}])
    assert all(_made(cls, checked) == [] for cls in rc.TEXT_CLASSES)


def _manual_set() -> dict:
    return _doc(_case(1, "Владелец создан; телефон из 10 цифр сохранён в карточке."),
                _case(2, "Показано «Неверный телефон»; форма создания остаётся открытой, владелец не создан."),
                _case(3, "Карточка показана с телефоном из 10 цифр."),
                _case(4, "Показана карточка владельца; телефон в ней совпадает с введённым."),
                _case(5, "Владелец Anna создан; имя из 30 символов сохранено полностью."))


def test_a_manual_set_gets_text_errors_and_as_many_twins_on_other_cases() -> None:
    _work, rows = rc.seed(_manual_set())
    errors = [row for row in rows if row["class"] != rc.TWIN]
    twins = [row for row in rows if row["class"] == rc.TWIN]
    assert errors and {row["class"] for row in errors} <= set(rc.TEXT_CLASSES)
    assert 0 < len(twins) <= len(errors) and not {row["case_id"] for row in twins} & {row["case_id"] for row in errors}
    assert rows.wanted == rc.canary_count(5)


def test_the_same_candidate_gives_the_same_rows() -> None:
    assert rc.seed(_manual_set())[1] == rc.seed(_manual_set())[1]


def test_the_machine_example_gets_no_text_error_and_no_twin() -> None:
    _work, rows = rc.seed(json.loads((ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json").read_text(encoding="utf-8")))
    assert {row["class"] for row in rows} <= set(rc.CLASSES)


def test_the_map_keeps_twins_apart_and_validates() -> None:
    from tools.schema_validation import schema_diagnostics

    value = json.loads(rc.map_bytes(DIGEST, rc.seed(_manual_set())[1]))
    assert value["twins"] and all(row["class"] == rc.TWIN for row in value["twins"]) and all(row["class"] != rc.TWIN for row in value["canaries"])
    assert schema_diagnostics(value, ROOT / "schemas" / "review-canaries.schema.json", ROOT) == []


def test_a_map_of_1_0_0_still_validates() -> None:
    from tools.schema_validation import schema_diagnostics

    value = {"schema_version": "1.0.0", "candidate_digest": DIGEST, "canaries": []}
    assert schema_diagnostics(value, ROOT / "schemas" / "review-canaries.schema.json", ROOT) == []


def _row(cls: str, n: int, canary_id: str | None = None) -> dict:
    return {"canary_id": canary_id or f"CAN-{n}", "class": cls, "case_id": f"TC-{n}", "step_id": f"STEP-{n}", "expectation_id": f"EXP-{n}",
            "assertion_id": None, "field": "expectation", "original": "Создан.", "mutated": "Не создан.", "markers": []}


def _answer(findings=(), edits=None) -> dict:
    artifacts = {"validation_report": {"findings": list(findings)}}
    if edits is not None:
        artifacts["successor_edits"] = edits
    return {"artifacts": artifacts}


def _on(n: int, severity: str) -> dict:
    return {"severity": severity, "related_ids": [f"TC-{n}", f"EXP-{n}"], "message": "Ожидание противоречит требованию."}


def test_a_warning_on_a_text_error_is_a_miss() -> None:
    assert rc.outcome([_row("outcome", 1)], _answer([_on(1, "WARNING")]))["caught"] == 0


def test_a_blocking_on_a_text_error_is_a_catch() -> None:
    assert rc.outcome([_row("outcome", 1)], _answer([_on(1, "BLOCKING")]))["caught"] == 1


def test_an_edit_of_a_text_error_is_a_catch() -> None:
    edit = {"case_id": "TC-1", "field": "expectation", "step_id": "STEP-1", "expectation_id": "EXP-1", "old": "Не создан.", "new": "Создан."}
    assert rc.outcome([_row("state", 1)], _answer(edits=[edit]))["caught"] == 1


def test_missed_text_errors_make_the_review_weak_not_unreliable() -> None:
    found = rc.outcome([_row("outcome", n) for n in (1, 2, 3)], _answer())
    assert (found["status"], found["warning"]) == ("WEAK", "REVIEW_WEAK")


def test_a_blocking_on_a_twin_is_a_false_alarm_and_rejects_no_case() -> None:
    rows = [_row("number", 1), _row(rc.TWIN, 2, "TWIN-1")]
    finding = _on(2, "BLOCKING")
    assert rc.outcome(rows, _answer([finding]))["twins"] == {"seeded": 1, "blocking": 1}
    assert rc.canary_finding(rows, finding, {"TC-2"})


def test_an_edit_of_a_twin_is_dropped() -> None:
    edit = {"case_id": "TC-2", "field": "expectation", "step_id": "STEP-2", "expectation_id": "EXP-2", "old": "Не создан.", "new": "Создан."}
    assert rc.filter_edits([_row(rc.TWIN, 2, "TWIN-1")], {"successor_edits": [edit]}) == ({}, ["TWIN-1"])


def test_fewer_errors_than_the_size_asks_are_told() -> None:
    rows = rc.Seeded([_row("outcome", 1)])
    rows.wanted = 3
    assert rc.outcome(rows, _answer())["wanted"] == 3
