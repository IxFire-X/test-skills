"""upd-b2 part 2: a case with the verdict ``rewrite`` is rewritten to the current requirement — its text is free, its Key, ``priority`` and
trace stay the human's; any other imported case stays byte for byte.  The report names the six groups by rows, the texts go to the analyst."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_cases_import_docs import LINKS, _changed, _fragment, _to_plan
from tests.test_cases_import_new import _autofix, _receipt, _rework, _submit

REWRITE = [{"row": 2, "source_requirement_ids": ["SREQ-0001", "SREQ-0003"], "verdict": "rewrite", "reason": "документ требует двоеточие"}, LINKS[1]]
FITS_WITH_DOUBT = [{**LINKS[0], "reason": "не уверена, что склейка — то же требование"}, LINKS[1]]


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links=REWRITE) -> dict:
    return _to_plan(tmp_path, monkeypatch, links=links)


def test_the_skeleton_keeps_the_verdict_of_a_case_and_its_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, other = _receipt(_plan(tmp_path, monkeypatch))["cases"]
    assert (case["verdict"], case["verdict_reason"]) == ("rewrite", "документ требует двоеточие") and other["verdict"] == "fits"


def test_a_skeleton_without_verdicts_has_no_verdict_keys() -> None:
    from tests.test_cases_import import CSV
    from tools.cases_import import imported_value

    links = [{"row": 2, "source_requirement_ids": ["SREQ-0001"]}, {"row": 3, "source_requirement_ids": []}]
    cases = imported_value("cases/zephyr.csv", CSV.encode("utf-8"), links, [{"source_requirement_id": "SREQ-0001", "text": "x"}])["cases"]
    assert not [key for case in cases for key in case if key.startswith(("verdict", "removed"))]


def test_a_rewrite_may_change_its_title_and_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _plan(tmp_path, monkeypatch)

    def rewrite(value: dict) -> None:
        case = value["test_cases"][0]
        case["title"], case["objective"] = "Склейка через двоеточие", "Новая цель"
        case["steps"][0]["action"] = "Вызвать combine"
        case["steps"][0]["expectations"][0]["text"] = "Возвращена «a:b»"

    code, answered = _submit(run, _changed(_fragment(run), rewrite))
    assert code == 0, answered.get("message")


@pytest.mark.parametrize("field", ["priority", "key"])
def test_a_rewrite_keeps_its_priority_and_its_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    run = _plan(tmp_path, monkeypatch)

    def change(value: dict) -> None:
        case = value["test_cases"][0]
        case["priority"] = "LOW" if field == "priority" else case["priority"]
        case["management"]["external_keys"] = {"zephyr_scale": "TC-9"} if field == "key" else case["management"]["external_keys"]

    code, refused = _submit(run, _changed(_fragment(run), change))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED", refused


def test_a_case_that_fits_stays_byte_for_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _plan(tmp_path, monkeypatch)
    code, refused = _submit(run, _changed(_fragment(run), lambda value: value["test_cases"][1].update(title="Другое")))
    assert code == 2 and "/test_cases/1/title" in refused["message"], refused


def test_a_rewrite_keeps_its_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _plan(tmp_path, monkeypatch)
    code, refused = _submit(run, _changed(_fragment(run), lambda value: value["test_cases"][0].update(requirement_ids=value["test_cases"][1]["requirement_ids"][:1])))
    assert code == 2 and refused["code"] == "IMPORT_TRACE_CHANGED", refused


def test_the_review_may_fix_the_text_of_a_rewritten_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, answered = _autofix(_plan(tmp_path, monkeypatch), 1)
    assert code == 0, answered


def test_the_review_may_not_fix_the_text_of_a_case_that_fits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, refused = _autofix(_plan(tmp_path, monkeypatch), 2)
    assert code == 2 and "IMPORT_HUMAN_FIELD_CHANGED" in refused["message"], refused


def test_the_review_and_the_rework_keep_the_priority_of_a_rewritten_case() -> None:
    from tools.orchestrate_test_case_revision import _imported_human_changes

    case = {"case_id": "TC-1", "requirement_ids": ["CREQ-1"], "title": "a", "objective": "b", "preconditions": [], "priority": "HIGH", "steps": []}
    mappings = [{"source_requirement_id": "SREQ-0001", "canonical_requirement_ids": ["CREQ-1"]}]
    imported = {"cases": [{"source_requirement_ids": ["SREQ-0001"], "verdict": "rewrite"}], "gaps": []}
    candidate = {"test_cases": [case], "source_to_canonical_mappings": mappings}
    assert _imported_human_changes(candidate, {"test_cases": [{**case, "title": "new"}]}, imported) == []
    assert _imported_human_changes(candidate, {"test_cases": [{**case, "priority": "LOW"}]}, imported) == ["/test_cases/0/priority"]


def test_a_rework_may_rewrite_the_text_of_a_rewritten_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, answered = _rework(tmp_path, monkeypatch, capsys, 1, links=REWRITE)
    assert code == 0, answered


@pytest.mark.parametrize("number", [1, 2])  # a rewritten case and one that fits
def test_a_rework_keeps_the_key_of_an_imported_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], number: int) -> None:
    code, refused = _rework(tmp_path, monkeypatch, capsys, number, links=REWRITE, edit=lambda case: case["management"].update(external_keys={"zephyr_scale": "TC-999"}))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/0/management" in refused["message"], refused


def _keyed(monkeypatch: pytest.MonkeyPatch, keys: tuple[str, str]) -> None:
    """The CSV of the helpers with a Key column: the Keys of its two cases."""
    from tests import test_cases_import_docs

    lines = test_cases_import_docs.CSV.split("\r\n")
    prefixed = ["Key," + lines[0], keys[0] + "," + lines[1], keys[1] + "," + lines[2], "," + lines[3]]
    monkeypatch.setattr(test_cases_import_docs, "CSV", "\r\n".join(prefixed))


TWO_ON_ONE = [{"row": 2, "source_requirement_ids": ["SREQ-0001"], "verdict": "rewrite", "reason": "документ требует двоеточие"},
              {"row": 3, "source_requirement_ids": ["SREQ-0001"], "verdict": "fits"}]  # a positive and a negative case of one requirement


def test_the_review_and_the_rework_know_a_rewritten_case_when_two_cases_stand_for_one_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _keyed(monkeypatch, ("ABC-T1", "ABC-T2"))
    code, answered = _autofix(_plan(tmp_path / "a", monkeypatch, TWO_ON_ONE), 1)
    assert code == 0, answered
    code, refused = _autofix(_plan(tmp_path / "b", monkeypatch, TWO_ON_ONE), 2)
    assert code == 2 and "IMPORT_HUMAN_FIELD_CHANGED" in refused["message"], refused  # the case that fits stays byte for byte
    code, answered = _rework(tmp_path / "c", monkeypatch, capsys, 1, links=TWO_ON_ONE)
    assert code == 0, answered


def test_the_report_names_a_key_of_the_form_of_zephyr_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _keyed(monkeypatch, ("Проверка входа админом", "ABC-T2"))
    report, _markdown, _questions = _report(tmp_path, monkeypatch, TWO_ON_ONE)
    assert [row["key"] for row in report["import"]["verdicts"]["rewritten"] + report["import"]["verdicts"]["unchanged"]] == [None, "ABC-T2"]


def test_a_line_break_in_a_reason_does_not_make_a_heading_of_the_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    links = [{**REWRITE[0], "reason": "документ требует двоеточие\n## Подделка"}, REWRITE[1]]
    _report_json, _markdown, questions = _report(tmp_path, monkeypatch, links)
    assert "\n## Подделка" not in questions and "документ требует двоеточие ## Подделка" in questions


def _report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links) -> tuple[dict, str, str]:
    from tools.run_report import write_report

    run = _plan(tmp_path, monkeypatch, links)
    write_report(run["project"], run["root"].name)
    read = lambda name: (run["root"] / name).read_text(encoding="utf-8")  # noqa: E731
    return json.loads(read("report.json")), read("report.md"), read("questions-for-analyst.md")


def test_the_report_names_the_groups_by_rows_and_the_analyst_gets_the_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report, markdown, questions = _report(tmp_path, monkeypatch, REWRITE)
    assert report["import"]["judged"] == 2 and [row["row"] for row in report["import"]["verdicts"]["rewritten"]] == [2]
    assert "переписаны: 1 (строки 2)" in markdown and "вне документов: 1 (строки 3)" in markdown and "без изменений: 0" in markdown
    assert "## Проверьте: кейсы после обновления" in questions and "переписан: «Склейка двух значений»" in questions and "документ требует двоеточие" in questions
    assert "Склейка двух значений" not in json.dumps(report, ensure_ascii=False) + markdown and "документ требует двоеточие" not in json.dumps(report, ensure_ascii=False) + markdown


def test_a_case_that_fits_with_a_doubt_is_to_check_by_hand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report, markdown, questions = _report(tmp_path, monkeypatch, FITS_WITH_DOUBT)
    assert [row["row"] for row in report["import"]["verdicts"]["manual"]] == [2] and "проверить вручную: 1 (строки 2)" in markdown
    assert "сомнение: «Склейка двух значений»" in questions and "не уверена" in questions



def test_an_obsolete_case_is_not_asked_about_as_a_case_outside_the_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import cases_import

    monkeypatch.setattr(cases_import, "check_requirements_answer", lambda *_args, **_kwargs: None)  # a delta that removes a requirement is no part of this fixture
    gone = {"row": 3, "source_requirement_ids": [], "verdict": "obsolete", "reason": "требование удалено", "removed": {"capability": "catalog", "name": "Refuse"}}
    report, markdown, questions = _report(tmp_path, monkeypatch, [LINKS[0], gone])
    assert report["import"]["outside"] == [] and [row["row"] for row in report["import"]["verdicts"]["obsolete"]] == [3]
    assert "устарел: «" in questions and "Кейсы CSV вне документов" not in questions


def test_the_rows_of_a_group_are_written_as_ranges() -> None:
    from tools.run_report import row_ranges

    assert row_ranges([2, 3, 4, 12, 15, 16, 117]) == "2–4, 12, 15–16, 117" and row_ranges([]) == ""


def test_the_report_names_the_rows_left_as_they_are_and_the_analyst_is_told_where_they_stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _report_json, markdown, questions = _report(tmp_path, monkeypatch, TWO_ON_ONE)
    assert "без изменений: 1 (строки 3)" in markdown
    assert "- без изменений: 1 — строки 3; вне документов: 0\n" in questions  # the closing line of «Проверьте», rows and counts only


def test_the_closing_line_of_the_checks_points_to_the_cases_outside_the_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _report_json, _markdown, questions = _report(tmp_path, monkeypatch, REWRITE)
    assert "- без изменений: 0; вне документов: 1 — см. раздел ниже\n" in questions and "## Кейсы CSV вне документов" in questions


# ---- the label of an obsolete case: code puts it into the skeleton, the model copies it ----------------------------------------------------

GONE = {"row": 3, "source_requirement_ids": [], "verdict": "obsolete", "reason": "требование удалено", "removed": {"capability": "catalog", "name": "Refuse"}}


def _obsolete_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from tools import cases_import

    monkeypatch.setattr(cases_import, "check_requirements_answer", lambda *_args, **_kwargs: None)  # a delta that removes a requirement is no part of this fixture
    return _plan(tmp_path, monkeypatch, [LINKS[0], GONE])


def test_code_puts_the_label_of_an_obsolete_case_into_its_skeleton_row_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.cases_import import OBSOLETE_LABEL

    fits, gone = _receipt(_obsolete_plan(tmp_path, monkeypatch))["cases"]
    assert gone["management"]["labels"] == [OBSOLETE_LABEL] and fits["management"]["labels"] == []


def test_a_skeleton_differs_from_the_one_without_the_verdict_in_the_label_of_the_obsolete_row_only() -> None:
    from tests.test_cases_import import CSV
    from tools.cases_import import imported_value

    requirements = [{"source_requirement_id": "SREQ-0001", "text": "x"}]
    plain = imported_value("cases/zephyr.csv", CSV.encode("utf-8"), [LINKS[0], {"row": 3, "source_requirement_ids": []}], requirements)["cases"]
    judged = imported_value("cases/zephyr.csv", CSV.encode("utf-8"), [LINKS[0], GONE], requirements)["cases"]
    assert plain[0] == judged[0] and {key: value for key, value in judged[1].items() if key not in {"management", "verdict", "verdict_reason", "removed"}} == {
        key: value for key, value in plain[1].items() if key != "management"}
    assert plain[1]["management"]["labels"] == [] and judged[1]["management"] == {**plain[1]["management"], "labels": ["устарел"]}


def test_the_label_is_not_put_twice_into_a_case_that_has_it() -> None:
    from tests.test_cases_import import CSV
    from tools.cases_import import imported_value

    lines = CSV.split("\r\n")
    data = "\r\n".join([lines[0] + ",Labels", lines[1] + ",", lines[2] + ',"smoke, Устарел"', lines[3] + ","]).encode("utf-8")
    cases = imported_value("cases/zephyr.csv", data, [LINKS[0], GONE], [{"source_requirement_id": "SREQ-0001", "text": "x"}])["cases"]
    assert cases[1]["management"]["labels"] == ["smoke", "Устарел"]


def _label_submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change) -> tuple[int, dict]:
    run = _obsolete_plan(tmp_path, monkeypatch)
    return _submit(run, _changed(_fragment(run), change))


def test_the_fragment_that_copies_the_label_of_the_skeleton_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, answered = _label_submit(tmp_path, monkeypatch, lambda value: None)
    assert code == 0, answered


def test_the_model_may_not_take_the_label_off_an_obsolete_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, refused = _label_submit(tmp_path, monkeypatch, lambda value: value["test_cases"][1]["management"].update(labels=[]))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/1/management" in refused["message"], refused


def test_the_model_may_not_put_the_label_on_a_case_that_fits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, refused = _label_submit(tmp_path, monkeypatch, lambda value: value["test_cases"][0]["management"].update(labels=["устарел"]))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/0/management" in refused["message"], refused


def test_the_report_has_no_label_text_besides_the_name_of_the_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import cases_import

    monkeypatch.setattr(cases_import, "check_requirements_answer", lambda *_args, **_kwargs: None)
    report, markdown, _questions = _report(tmp_path, monkeypatch, [LINKS[0], GONE])
    assert markdown.replace("устарели", "").count("устарел") == 0 and "устарел" not in json.dumps({**report, "import": {**report["import"], "verdicts": {}}}, ensure_ascii=False)


def test_the_texts_name_the_label_of_an_obsolete_case_as_the_constant_has_it() -> None:
    from tools.cases_import import OBSOLETE_LABEL

    root = Path(__file__).resolve().parents[1]
    how_to = " ".join((root / "HOW-TO-USE.md").read_text(encoding="utf-8").split())
    import_md = (root / "skills" / "orchestrate" / "references" / "import.md").read_text(encoding="utf-8")
    assert f"метка `{OBSOLETE_LABEL}` в колонке Labels; снять её — вручную" in how_to and f"the label `{OBSOLETE_LABEL}` into `labels`" in import_md
