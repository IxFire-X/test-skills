"""V6v: what the tester reads in the ``.html`` and ``.md`` of a set — the table of cases, the priority and requirements of a case, the common
preconditions, no empty noise, whole bracket pairs in a parameter group.  Pure functions over the example document; CSV and JSON stay as they were."""
from __future__ import annotations

import hashlib
import json
import re

from tests.test_partial_acceptance import _finding, r1_document
from tests.test_param_groups import EXAMPLE, grouped

PROFILE = "zephyr-scale-step-row-24-v5"
ESCAPES = "<b>x</b> | y"


def _doc(count: int = 4) -> dict:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["test_cases"] = document["test_cases"][:count]
    return document


def _html(document: dict, extras: dict | None = None) -> str:
    from tools.test_case_projections import _render_html_preview_validated

    return _render_html_preview_validated(document, PROFILE, extras).payload.decode("utf-8")


def _md(document: dict, extras: dict | None = None) -> str:
    from tools.test_case_projections import _render_markdown_validated

    return _render_markdown_validated(document, PROFILE, extras).payload.decode("utf-8")


def _table_rows(text: str) -> list[list[str]]:
    """The cells of the table «Кейсы набора» of a Markdown file."""
    block = text[text.index("**Кейсы набора"):].split("\n\n")[1].splitlines()
    return [re.split(r"(?<!\\) \| ", line[2:-2]) for line in block[2:]]


def _bracketed() -> dict:
    return grouped(("Anna", "1 (int)", "Long.valueOf(10)", "true (boolean)"))


# --- Q1: a bracket pair is not torn -----------------------------------------------------------------

def test_the_common_text_of_variants_does_not_tear_a_bracket_or_a_quote() -> None:
    from tools.test_case_projections import SLOT, template_texts

    texts = ["Значение: 1 (int)", 'Значение: "abcd" (String)', "Значение: Long.valueOf(10)", "Значение: true (boolean)"]
    template, middles = template_texts(texts)
    assert template == "Значение: " + SLOT and middles == [text.removeprefix("Значение: ") for text in texts]


def test_a_variant_keeps_its_closing_bracket_in_the_html_table() -> None:
    page = _html(_bracketed())
    assert "<td>1 (int)</td>" in page and "<td>Long.valueOf(10)</td>" in page and "<td>true (boolean)</td>" in page


def test_a_variant_keeps_its_closing_bracket_in_the_markdown_table() -> None:
    rows = [line for line in _md(_bracketed()).splitlines() if line.startswith("| ") and "TC-B1-001" in line]
    assert [row.split(" | ")[1] for row in rows] == ["Anna", "1 (int)", "Long.valueOf(10)", "true (boolean)"]


def test_the_reviewer_view_shows_the_variants_with_whole_pairs_too() -> None:
    from tools.review_view import render

    view = render(_bracketed()).decode("utf-8")
    assert "| 1 (int) |" in view and "| Long.valueOf(10) |" in view and "| true (boolean) |" in view


def test_the_reviewer_view_of_a_set_without_groups_keeps_its_bytes() -> None:
    from tools.review_view import render

    assert hashlib.sha256(render(r1_document())).hexdigest() == "f30cc0b0216e501dac59893bae2d47fea6bfc29c54ac6fc0a1d18d536d53fcf8"


# --- Q2, Q10: no line that says nothing ------------------------------------------------------------

def test_the_technique_line_is_not_printed_for_another_technique() -> None:
    document = _doc(2)
    document["test_cases"][0]["technique"], document["test_cases"][1]["technique"] = "OTHER", "BVA"
    for text in (_html(document), _md(document)):
        assert "Техника: Граничные значения" in text and "Другая техника" not in text


def test_the_author_is_not_printed() -> None:
    document = _doc(2)
    assert document["metadata"]["author"] not in _html(document) + _md(document) and "Автор" not in _html(document) + _md(document)


# --- Q7: the table of cases and the line of a case ---------------------------------------------------

def test_the_markdown_starts_with_a_table_of_its_cases_in_their_order() -> None:
    document = _doc(4)
    rows = _table_rows(_md(document))
    assert [row[0] for row in rows] == ["ТК-1", "ТК-2", "ТК-3", "ТК-4"]
    assert [row[1] for row in rows] == [case["title"] for case in document["test_cases"]]
    assert _md(document).index("**Кейсы набора") < _md(document).index("## ТК-1.")


def test_the_html_starts_with_a_table_of_its_cases() -> None:
    page = _html(_doc(3))
    head = "<th>№</th><th>Название</th><th>Папка</th><th>Приоритет</th><th>Требования</th>"
    assert "<h2>Кейсы набора</h2>" in page and head in page and page.index(head) < page.index("<section>")
    assert page.count("<tr><td>ТК-") == 3


def test_the_priority_is_said_in_the_words_of_the_csv() -> None:
    document = _doc(3)
    for case, value in zip(document["test_cases"], ("CRITICAL", "MEDIUM", "LOW")):
        case["priority"] = value
    assert [row[3] for row in _table_rows(_md(document))] == ["High", "Normal", "Low"]


def test_the_requirements_of_a_case_stand_in_the_table_and_under_its_title() -> None:
    document = _doc(2)
    document["test_cases"][0]["requirement_ids"] = ["CREQ-B1-001", "CREQ-B1-002"]
    text = _md(document)
    assert _table_rows(text)[0][4] == "CREQ-B1-001, CREQ-B1-002"
    assert "## ТК-1. " in text and "\n\n**Приоритет:** High. **Требования:** CREQ-B1-001, CREQ-B1-002\n\n" in text
    assert "<p><strong>Приоритет:</strong> High. <strong>Требования:</strong> CREQ-B1-001, CREQ-B1-002</p>" in _html(document)


def test_the_folder_is_a_column_of_the_table_and_a_missing_folder_is_a_dash() -> None:
    document = _doc(2)
    document["test_cases"][0]["management"]["folder"], document["test_cases"][1]["management"]["folder"] = "Владельцы", None
    assert [row[2] for row in _table_rows(_md(document))] == ["Владельцы", "—"]
    assert "<td>Владельцы</td>" in _html(document)


def test_without_a_folder_in_any_case_there_is_no_folder_column() -> None:
    document = _doc(2)
    for case in document["test_cases"]:
        case["management"]["folder"] = None
    assert "| № | Название | Приоритет | Требования |" in _md(document) and "Папка" not in _md(document) and "<th>Папка</th>" not in _html(document)


def test_a_group_is_one_row_of_the_table_with_the_requirements_of_all_its_cases() -> None:
    document = grouped()
    document["test_cases"][1]["requirement_ids"] = ["CREQ-B1-002"]
    rows = _table_rows(_md(document))
    assert len(rows) == 64 and rows[0][0] == "ТК-1" and "CREQ-B1-002" in rows[0][4] and rows[1][0] == "ТК-2"


def test_a_subtraction_keeps_the_numbers_of_the_set_the_reviewer_read_in_its_table() -> None:
    from tests.test_partial_publication import _markdown, _partial

    document = r1_document()
    effective, partial = _partial(document, [_finding("BLOCKING", [document["test_cases"][0]["case_id"]])])
    text = _markdown(effective, partial)
    rows = _table_rows(text)
    assert rows[0][0] == "ТК-2" and len(rows) == 63 and text.index("Принят частично") < text.index("**Кейсы набора")


def test_the_table_of_a_partial_html_stands_below_its_header() -> None:
    from tools.publish_test_case_bundle import build_bundle
    from tests.test_partial_publication import _partial

    document = r1_document()
    effective, partial = _partial(document, [_finding("BLOCKING", [document["test_cases"][0]["case_id"]])])
    page = build_bundle(effective, PROFILE, partial=partial).preview_bytes.decode("utf-8")
    assert page.index("Принят частично") < page.index("<h2>Кейсы набора</h2>") < page.index("<section>") < page.index("<h2>Пробелы покрытия</h2>")


def test_the_status_header_stays_the_first_thing_in_both_files() -> None:
    from tools.publish_test_case_bundle import status_payloads

    files = dict(status_payloads(_doc(3), PROFILE, "REJECTED", ["нет"]))
    assert files["html"].decode("utf-8").index("ОТКЛОНЁН") < files["html"].decode("utf-8").index("<h1>")
    assert files["md"].decode("utf-8").startswith("> **ОТКЛОНЁН РЕВЬЮЕРОМ") and b"**\xd0\x9a\xd0\xb5\xd0\xb9\xd1\x81\xd1\x8b \xd0\xbd\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x80\xd0\xb0" in files["md"]


def test_the_rejected_cases_say_their_requirements_once() -> None:
    from tests.test_partial_publication import _rejected_files

    document = r1_document()
    text = _rejected_files(document, [_finding("BLOCKING", [document["test_cases"][2]["case_id"]])])["md"]
    assert text.count("**Требования:**") == 1 and "**Без другого принятого кейса:** CREQ-B1-011" in text


# --- P1, Q4: no repeated or empty text -------------------------------------------------------------

def _with_preconditions(*per_case: list[str]) -> dict:
    document = _doc(len(per_case))
    for case, own in zip(document["test_cases"], per_case):
        case["preconditions"] = own
    return document


def test_a_precondition_common_to_three_cases_stands_once_in_the_header() -> None:
    document = _with_preconditions(["Общее"], ["Общее"], ["Общее"])
    for text in (_html(document), _md(document)):
        assert text.count("Общее") == 1 and "Общие предусловия" in text and text.index("Общие предусловия") < text.index("Кейсы набора")
    assert "Предусловия</h3>" not in _html(document) and "**Предусловия:**" not in _md(document)


def test_a_case_keeps_its_own_precondition_beside_the_common_one() -> None:
    text = _md(_with_preconditions(["Общее", "Своё"], ["Общее"], ["Общее"]))
    assert text.count("Общее") == 1 and text.count("**Предусловия:**") == 1 and "- Своё" in text


def test_preconditions_that_differ_stay_in_their_cases() -> None:
    text = _md(_with_preconditions(["Первое"], ["Второе"], ["Первое"]))
    assert "Общие предусловия" not in text and text.count("- Первое") == 2


def test_two_cases_have_no_common_preconditions() -> None:
    text = _md(_with_preconditions(["Общее"], ["Общее"]))
    assert "Общие предусловия" not in text and text.count("- Общее") == 2


def test_a_case_without_preconditions_makes_nothing_common() -> None:
    text = _md(_with_preconditions(["Общее"], ["Общее"], []))
    assert "Общие предусловия" not in text and "- Не требуются." in text


def test_a_step_without_data_has_a_dash_not_a_sentence_in_the_html() -> None:
    from tools.test_case_projections import _html_test_data

    step = {"operation": {"kind": "http"}, "inputs": [], "test_data": "Тело запроса отсутствует."}
    assert _html_test_data(step) == "—"
    step["test_data"] = ""
    assert _html_test_data(step) == "—"


def test_the_markdown_keeps_the_sentence_of_the_generator() -> None:
    document = _doc(1)
    document["test_cases"][0]["steps"][0]["test_data"] = "Тело запроса отсутствует."
    assert "Тело запроса отсутствует." in _md(document)


# --- the model's text is escaped in the new places ---------------------------------------------------

def test_the_text_of_the_model_is_escaped_in_the_table_the_folders_and_the_common_preconditions() -> None:
    document = _with_preconditions([ESCAPES], [ESCAPES], [ESCAPES])
    for case in document["test_cases"]:
        case["title"], case["management"]["folder"] = ESCAPES, ESCAPES
    page, text = _html(document), _md(document)
    assert "<b>" not in page and "<b>" not in text and page.count("&lt;b&gt;x&lt;/b&gt; | y") >= 10
    assert [row[1:3] for row in _table_rows(text)][0] == [r"\<b\>x\</b\> \| y"] * 2
    assert text[text.index("**Общие предусловия"):].startswith("**Общие предусловия:**\n- \\<b\\>x\\</b\\> \\| y")


# --- CSV and JSON are as they were ---------------------------------------------------------------------

def test_the_csv_and_the_json_keep_their_bytes() -> None:
    from tools.publish_test_case_bundle import build_bundle

    pins = {"r1": (r1_document(), "c83b5492ae57e52035da3139a15648e55189c56b7e2ab5e06655e466694a3538", "a2c0228dee0585e891cb5c7dd5b3cc15e936e712f51bdbc48d8c51d6a2632b78"),
            "grouped": (grouped(), "52a41186010541ce6bbf05e9196fa574de75e724e0ffa02a69d61ea6aa259694", "eeab6190d5d6e446b918ce9709aa086f04e903704c72acaa4cfbb515ea81a606")}
    for document, json_sha, csv_sha in pins.values():
        bundle = build_bundle(document, PROFILE)
        assert hashlib.sha256(bundle.json_bytes).hexdigest() == json_sha and hashlib.sha256(bundle.csv_bytes).hexdigest() == csv_sha
