"""FaT-Z5: the default Zephyr CSV is v5 of main a5c735a — only the human scenario of a step: the action with its resolved
URL, the authored test data and the expectation prose; v4 stays byte for byte for old runs; one parse turns a v5 CSV
back into the human fields (the shared reader of FaT-IMP)."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
V4 = "zephyr-scale-step-row-24-v4"
V5 = "zephyr-scale-step-row-24-v5"
V4_FIXTURE_SHA256 = "35465fd6ac1a04124093affcce6ad7f4ae8ca7e39a3d2aa385ac6e04def3e610"  # develop d6deda0, before FaT-Z5
DATA, EXPECTED, STEP = ("Test Script (Step-by-Step) - Test Data", "Test Script (Step-by-Step) - Expected Result",
                        "Test Script (Step-by-Step) - Step")


def _rows(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_v4_bytes_are_unchanged_for_old_runs() -> None:
    from tools.test_case_projections import render_zephyr_csv

    assert hashlib.sha256(render_zephyr_csv(canonical_fixture(), V4).payload).hexdigest() == V4_FIXTURE_SHA256


def test_v5_is_the_default_bundle_profile(tmp_path: Path) -> None:
    from tools.publish_test_case_bundle import build_bundle, publish_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = _example()
    assert render_zephyr_csv(document).payload == render_zephyr_csv(document, V5).payload == build_bundle(document).csv_bytes
    receipt = publish_bundle(document, tmp_path)
    assert receipt.csv_profile == V5 and receipt.preview_path.endswith(".html")
    assert publish_bundle(document, tmp_path / "v4", V4).csv_profile == V4  # old runs keep publishing v4


def test_v5_bytes_of_one_step() -> None:
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    header, row = _rows(render_zephyr_csv(document).payload)
    step = document["test_cases"][0]["steps"][0]
    assert (row[header.index(STEP)], row[header.index(DATA)], row[header.index(EXPECTED)]) == (
        step["action"], step["test_data"], step["expectations"][0]["text"])


def test_v5_has_no_machine_lines_and_resolves_the_http_url_in_the_step() -> None:
    from tools.test_case_projections import _human_action, render_zephyr_csv

    document = _example()
    payload = render_zephyr_csv(document).payload.decode("utf-8-sig")
    for marker in ("arg:", "Проверка:", "Выход:", "CAP-", "Действие проекта:", "Операция:", "body:/", "http_body:"):
        assert marker not in payload, marker
    rows = _rows(render_zephyr_csv(document).payload)
    header, steps = rows[0], [step for case in document["test_cases"] for step in case["steps"]]
    for row, step in zip(rows[1:], steps):
        orders = next({item["step_id"]: item["display_order"] for item in case["steps"]} for case in document["test_cases"] if step in case["steps"])
        assert row[header.index(STEP)].lstrip("'") == _human_action(step, orders).lstrip("'")
        assert row[header.index(DATA)].lstrip("'") == step["test_data"].lstrip("'")
        assert row[header.index(EXPECTED)].lstrip("'") == "\n\n".join(item["text"] for item in step["expectations"]).lstrip("'")


def test_a_dynamic_url_value_names_the_output_of_the_earlier_step_and_a_secret_only_its_label() -> None:
    from tools.test_case_projections import _human_action

    step = {"action": "Получить владельца: GET /owners/{ownerId}.", "operation": {"kind": "http", "method": "GET", "path": "/owners/{ownerId}"},
            "inputs": [
                {"display_order": 1, "target": {"location": "path", "name": "ownerId"},
                 "source": {"kind": "step_output", "step_id": "STEP-1", "output_id": "ownerId"}},
                {"display_order": 2, "target": {"location": "query", "name": "token", "sensitive": True},
                 "source": {"kind": "secret_handle", "safe_label": "токен API", "handle": "vault://never-printed"}},
            ]}
    action = _human_action(step, {"STEP-1": 1})
    assert action == "Получить владельца: GET /owners/<ownerId из шага 1>?token=<токен API>."
    assert "vault://" not in action


def _tricky(document: dict) -> dict:
    document = copy.deepcopy(document)
    case = document["test_cases"][0]
    case["title"] = "=СУММ(A1) — формула в названии"
    case["preconditions"] = ["Первое условие.", "Второе условие;\nс переносом"]
    case["steps"][0]["test_data"] = "+7 999 строка\n\nвторой абзац"
    case["steps"][0]["expectations"][0]["text"] = "-1 на счёте"
    return document


def test_the_parse_of_a_v5_csv_gives_back_what_the_human_sees() -> None:
    from tools.canonical_document import validate_canonical_document
    from tools.test_case_projections import _human_action, render_zephyr_csv
    from tools.zephyr_csv import parse_zephyr_csv

    for document in (_example(), _tricky(_example()), canonical_fixture()):
        assert validate_canonical_document(document) == []
        cases = parse_zephyr_csv(render_zephyr_csv(document).payload)
        assert [case["title"] for case in cases] == [case["title"] for case in document["test_cases"]]
        for parsed, case in zip(cases, document["test_cases"]):
            orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
            assert (parsed["objective"], parsed["preconditions"]) == (case["objective"], "\n".join(case["preconditions"]))
            assert [step["action"] for step in parsed["steps"]] == [_human_action(step, orders) for step in case["steps"]]
            assert [step["test_data"] for step in parsed["steps"]] == [step["test_data"] for step in case["steps"]]
            assert [step["expected"].split("\n\n") for step in parsed["steps"]] == [
                [item["text"] for item in step["expectations"]] for step in case["steps"]]


def test_a_plain_zephyr_export_is_read_as_human_text() -> None:
    from tools.zephyr_csv import parse_zephyr_csv

    text = "Name;Objective;Precondition;Priority;Test Script (Step-by-Step) - Step;Test Script (Step-by-Step) - Test Data;Test Script (Step-by-Step) - Expected Result\r\n" \
           "Вход;Проверить вход;Есть учётная запись;High;Открыть форму;логин anna;Форма открыта\r\n" \
           ";;;;Ввести пароль;'=secret;Вход выполнен\r\n"
    [case] = parse_zephyr_csv(text.encode("utf-8"))
    assert case["title"] == "Вход" and case["priority"] == "HIGH" and case["row"] == 2
    assert [(step["action"], step["test_data"], step["expected"], step["row"]) for step in case["steps"]] == [
        ("Открыть форму", "логин anna", "Форма открыта", 2), ("Ввести пароль", "=secret", "Вход выполнен", 3)]


@pytest.mark.parametrize(("text", "code", "named"), [
    ("Name,Objective,Колонка X,Test Script (Step-by-Step) - Step\r\nА,Б,В,Г\r\n", "ZEPHYR_CSV_COLUMNS", "Колонка X"),
    ("Name,Test Script (Step-by-Step) - Step\r\n,шаг без кейса\r\nКейс,шаг\r\n", "ZEPHYR_CSV_ROWS", "2"),
    ("Name,Test Script (Step-by-Step) - Step\r\nКейс без шагов,\r\n", "ZEPHYR_CSV_ROWS", "2"),
])
def test_unknown_columns_and_rows_are_refused_by_name(text: str, code: str, named: str) -> None:
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    with pytest.raises(ZephyrCsvError) as refused:
        parse_zephyr_csv(text.encode("utf-8"))
    assert refused.value.code == code and named in str(refused.value)
