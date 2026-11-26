"""v0.8 failure groups (handoff/v08/failure-groups-design.md): the failed methods of the findings grouped by cause.

The text of a failure keeps the lines of its first check («expected: 201 but was: 403»); the causes are the code of the answer, the
class of an error, a text without the literals of the test data (the report leaves the company) or «no text»; the line «one cause» of
«Итог» stands on a code or a class only.  The lines for the tester (4.1–4.5 of the design) are approved by the user (26.11) and pinned
at the end; the mark of 4.4 is pinned in test_quarantine."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.automation_validation import automation_sha256
from tools.quarantine import failure_cause, failure_groups, failure_line, failure_texts, one_cause, status_checks
from tools.run_tests import _failure_message

pytestmark = pytest.mark.contract
STATUS_CHECK = {"ASSERT-1": True}  # the check ASSERT-1 of the canon reads the code of the answer
R6 = "[ASSERT-1] expected: 201 but was: 403"  # AssertJ as(…) leaves the label only, no word of the status


def _row(method: str, text: str, kind: str = "assertion_failed") -> dict:
    return {"file": "src/test/java/ApiTest.java", "method": method, "ref": "ASSERT-1", "kind": kind, "message": text}


def _grouped(*texts: str, kind: str = "assertion_failed", checks: dict | None = None) -> list[dict]:
    """Groups of methods m0, m1, … whose texts before the note are *texts*."""
    rows = [_row(f"m{index}", "", kind) for index in range(len(texts))]
    return failure_groups(rows, {(row["file"], row["method"]): text for row, text in zip(rows, texts)}, checks)


def test_status_groups_by_actual_code() -> None:
    groups = _grouped("[ASSERT-1] expected: 200 but was: 403", "[ASSERT-2] expected: 201 but was: 403", "[ASSERT-3] expected: 404 but was: 500",
                      checks=STATUS_CHECK)
    assert [(group["rule"], group["count"], group["methods"]) for group in groups] == [("status", 2, ["m0", "m1"]), ("status", 1, ["m2"])]
    assert "403" in groups[0]["cause"] and "500" in groups[1]["cause"]


def test_status_of_mockmvc_restassured_and_pytest() -> None:
    for text in ("Status expected:<201> but was:<403>", "1 expectation failed. Expected status code <201> but was <403>.",
                 "ASSERT-1 assert 403 == 201 +  where 403 = <Response [403]>.status_code"):
        assert failure_cause("assertion_failed", text, True) == ("status", "403", "201"), text


def test_without_canon_three_digits_without_a_word_of_the_status_are_no_status() -> None:
    assert failure_cause("assertion_failed", R6, True)[0] == "text"


def _canon(actual: dict) -> dict:
    assertion = {"assertion_id": "ASSERT-1", "actual": actual}
    return {"test_cases": [{"steps": [{"expectations": [{"assertions": [assertion]}]}]}]}


def _cause_by_canon(actual: dict) -> str:
    return failure_groups([_row("m0", "")], {(_row("m0", "")["file"], "m0"): R6}, status_checks(_canon(actual)))[0]["rule"]


def test_an_http_status_check_of_the_canon_is_a_status() -> None:
    assert _cause_by_canon({"kind": "http_status"}) == "status"


def test_a_project_result_named_status_is_a_status() -> None:  # R5–R8 read the code so: {"kind": "project_result", "name": "status"}
    assert _cause_by_canon({"kind": "project_result", "name": "status"}) == "status"


def test_the_same_numbers_of_another_check_are_no_status() -> None:
    assert _cause_by_canon({"kind": "project_result", "name": "ownerCount"}) == "text"


def test_error_groups_by_exception_class() -> None:
    [group] = _grouped("Request processing failed: org.springframework.dao.DataIntegrityViolationException: could not execute", kind="error")
    assert group["rule"] == "exception" and "DataIntegrityViolationException" in group["cause"]


def test_a_label_alone_joins_the_lines_of_its_check() -> None:
    item = ET.fromstring('<testcase name="t"><failure message="[ASSERT-1] &#10;expected: 201&#10; but was: 403"/></testcase>')
    assert failure_groups([_row("m0", "")], {(_row("m0", "")["file"], "m0"): _failure_message(item)}, STATUS_CHECK)[0]["rule"] == "status"


def test_text_drops_literals() -> None:
    [group] = _grouped('[ASSERT-1] expected: "jake_1" but was: null', '[ASSERT-2] expected: "anna_2" but was: null')
    assert group["rule"] == "text" and group["count"] == 2 and "jake" not in group["cause"]


def test_junit_and_pytest_literals_are_dropped_too() -> None:
    assert "jake" not in failure_cause("assertion_failed", "expected: <jake> but was: <null>", True)[1]
    assert "jake" not in failure_cause("assertion_failed", "assert 'jake' == 'anna'", True)[1]


def test_a_date_literal_stays_a_date() -> None:
    [group] = _grouped('expected: "2019-03-04" but was: "2019-03-03"', 'expected: "2021-02-03" but was: "2021-02-02"')
    assert group["count"] == 2 and "<дата>" in group["cause"] and "2019" not in group["cause"]


def test_the_place_of_the_check_is_cut() -> None:
    [group] = _grouped("json can not be null at ApiTest.a(ApiTest.java:10)", "json can not be null at ApiTest.b(ApiTest.java:20)")
    assert group["count"] == 2 and " at " not in group["cause"]


def test_a_text_of_many_lines_gives_its_first_line() -> None:
    text = "expected: <2> but was: <3>\n\tat org.example.OwnerTest.findsOwner"
    assert failure_cause("assertion_failed", text, True) == ("text", "expected: <…> but was: <…>", None)


def test_old_head_is_unknown() -> None:
    assert failure_cause("assertion_failed", "Multiple Failures (2 failures)", True)[0] == "unknown"
    assert failure_cause("assertion_failed", "Multiple Failures _2 failures_", False)[0] == "unknown"


def test_a_note_without_its_text_gives_no_text() -> None:
    [group] = failure_groups([_row("m0", "expected: _jake_1_ but was: null")])
    assert group["rule"] == "unknown" and "jake" not in group["cause"]


def test_a_note_without_its_text_still_gives_the_code() -> None:
    [group] = failure_groups([_row("m0", "expected: 201 but was: 403")], checks=STATUS_CHECK)
    assert group["rule"] == "status"


def test_six_of_eight_by_code_is_one_cause() -> None:
    groups = _grouped(*["expected: 201 but was: 403"] * 6, "boom", "bang", checks=STATUS_CHECK)
    assert one_cause(groups, 8)["count"] == 6


def test_a_text_or_no_text_is_never_one_cause() -> None:
    assert one_cause(_grouped(*['expected: "a" but was: "b"'] * 6, "boom", "bang"), 8) is None
    assert one_cause(_grouped(*["Multiple Failures (2 failures)"] * 6, "boom", "bang"), 8) is None


def test_four_by_code_is_not_one_cause() -> None:
    assert one_cause(_grouped(*["expected: 201 but was: 403"] * 4, checks=STATUS_CHECK), 4) is None


def test_the_frame_after_a_check_is_not_in_the_line() -> None:
    text = "Multiple Failures (2 failures)\n-- failure 1 --\n[ASSERT-1] \nexpected: 1\n but was: 2\nat ApiTest.a(ApiTest.java:10)\n-- failure 2 --"
    assert failure_line(text) == "[ASSERT-1] expected: 1 but was: 2"


def _automation() -> dict:
    return {"artifacts": {"generated_files": [{"file_id": "FILE-1", "path": "src/test/java/ApiTest.java"}],
                          "generated_symbols": [{"file_id": "FILE-1", "symbol_id": "SYMBOL-1",
                                                 "locator": {"kind": "java_class_method", "class_fqn": "p.ApiTest", "method_name": "a"}}]}}


def test_failure_texts_ties_the_text_to_the_method() -> None:
    execution = {"automation_sha256": automation_sha256(_automation()), "failed_methods": [{"nodeid": "FILE-1::SYMBOL-1", "message": 'expected: "x" but was: "y"'}]}
    assert failure_texts(execution, _automation()) == {("src/test/java/ApiTest.java", "ApiTest.a"): 'expected: "x" but was: "y"'}


def test_failure_texts_of_another_automation_are_none() -> None:
    execution = {"automation_sha256": "sha256:" + "0" * 64, "failed_methods": [{"nodeid": "FILE-1::SYMBOL-1", "message": "boom"}]}
    assert failure_texts(execution, _automation()) == {}


# --- the lines for the tester, approved by the user on 26.11 (4.1–4.3, 4.5 of handoff/v08/failure-groups-design.md) ----------------

def _report(groups: list[dict], failed: int) -> dict:
    """The fields of a report that the lines of the causes read; the rest as an empty report has them."""
    rows = [{"file": "src/test/java/ApiTest.java", "method": f"m{index}", "ref": "ASSERT-1", "kind": "assertion_failed"} for index in range(failed)]
    return {"failure_groups": groups, "quarantine": rows}


def test_the_words_of_a_status_and_of_an_exception() -> None:
    groups = _grouped(*["expected: 200 but was: 403"] * 2, "expected: 201 but was: 403", checks=STATUS_CHECK)
    groups += _grouped("boom: java.lang.IllegalStateException: x", kind="error")
    assert [group["cause"] for group in groups] == ["ответ 403 вместо ожидаемого 200 (2), 201 (1)", "исключение IllegalStateException"]


def test_the_words_of_no_text() -> None:
    cause = failure_groups([_row("m0", "Multiple Failures _2 failures_")])[0]["cause"]
    assert cause == "текст падения не сохранён (прогон старой версии пакета или проверка без текста)"


def test_the_block_of_the_causes() -> None:
    from tools.run_report import _cause_lines

    groups = _grouped(*["expected: 201 but was: 403"] * 5, checks=STATUS_CHECK)
    assert _cause_lines(_report(groups, 5)) == ["Причины:", "- 5 — ответ 403 вместо ожидаемого 201 (5); тесты: m0, m1, m2 и ещё 2", ""]


@pytest.mark.slow
def test_the_summary_tells_the_causes(tmp_path: Path) -> None:
    from tests.test_run_report import _finished_run
    from tools.run_report import build_report, render_markdown

    report = build_report(tmp_path, _finished_run(tmp_path).name)[0]
    groups = _grouped(*["expected: 201 but was: 403"] * 6, "boom", "bang", checks=STATUS_CHECK)
    summary = render_markdown({**report, **_report(groups, 8)}).split("## Итог", 1)[1].split("\n## ", 1)[0]
    assert ("**Одна причина у 6 из 8 упавших автотестов: ответ 403 вместо ожидаемого 201 (6).** Скорее всего это одно расхождение "
            "окружения или адреса, а не 6 дефектов — разберите её первой.") in summary.splitlines()
    assert ", причин: 3" in summary


@pytest.mark.slow
def test_report_keeps_the_method_lines(tmp_path: Path) -> None:
    import difflib

    from tests.test_run_report import _finished_run
    from tools.run_report import build_report, render_markdown

    report = build_report(tmp_path, _finished_run(tmp_path).name)[0]
    before = render_markdown({key: value for key, value in report.items() if key != "failure_groups"}).splitlines()
    after = render_markdown({**report, "failure_groups": failure_groups(report["quarantine"])}).splitlines()
    method = after.index(next(line for line in before if " :: " in line))
    changes = [op for op in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes() if op[0] != "equal"]
    assert changes and all(tag == "insert" and end <= method for tag, _i1, _i2, _start, end in changes)


@pytest.mark.slow
def test_the_report_groups_a_failure_by_its_text_and_the_check_of_the_canon(tmp_path: Path) -> None:
    from tests.test_quarantine import THREE_FUNCTIONS, _fail
    from tools.finalize_attempt import decide_dispositions
    from tools.run_report import build_report

    # the label is the check ASSERT-batch-a-001 of the canon of the fixture, an http_status; the text has no word of the status
    run_root, attempt_id, delta = _fail(tmp_path, content=THREE_FUNCTIONS, message="ASSERT-batch-a-001 expected: <200> but was: <403>")
    decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    [group] = build_report(tmp_path, run_root.name)[0]["failure_groups"]
    assert (group["rule"], group["methods"]) == ("status", ["test_selected"]) and "403" in group["cause"]
