"""V7a: the files and the report of a set after the rework — the cases not accepted in the last reworked version with the findings of both rounds, ``r3`` names, the
rows of the table «Итог»; functions of ``publish_test_case_bundle`` and ``run_report`` over the result of ``rework_final``."""
from __future__ import annotations

import copy

import pytest

from tests.test_partial_acceptance import _finding, _outcome, _real, _report, r1_document
from tools.publish_test_case_bundle import build_bundle, gap_rows, partial_context, rejected_cases
from tools.revision_selection import rework_final


def _after(*passed_numbers: int):
    """r1 with cases 1 and 2 rejected; the reworked ones in a changed title; the second review rejects what is not in ``passed_numbers``."""
    document = r1_document()
    outcome = _outcome(document, _real(document, 1, 2))
    answer = {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1",
              "test_cases": [{**copy.deepcopy(document["test_cases"][number - 1]), "title": f"Доработан {number}"} for number in (1, 2)]}
    rejected = [document["test_cases"][number - 1]["case_id"] for number in (1, 2) if number not in passed_numbers]
    report = _report(document, *[_finding("BLOCKING", [case_id], "Снова не то.") for case_id in rejected]) | {"verdict": "ТРЕБУЕТ ДОРАБОТКИ" if rejected else "ПРИНЯТО"}
    return document, outcome, rework_final(document, outcome, answer, report)


def test_the_context_shows_the_reworked_version_and_both_rounds_and_says_twice() -> None:
    document, outcome, final = _after(2)
    partial = partial_context(document, outcome, {"state": "reviewed", "final": final})
    assert partial["file"].endswith(".r3.REJECTED-CASES.md") and partial["kept"] == 63 and partial["rework"] == "reviewed"
    row = partial["rejected"][0]
    assert (row["label"], row["said"][0][0], row["said2"][0][1]) == (1, "REVIEW_NOTE", "Снова не то.")
    assert partial["gaps"] and all("отклонён дважды" in cell for _req, cell, _said in gap_rows(partial))


def test_the_file_of_the_rejected_shows_the_last_version_the_two_rounds_and_the_words_after_a_round() -> None:
    document, outcome, final = _after(2)
    partial = partial_context(document, outcome, {"state": "reviewed", "final": final})
    shown, extras, intro = rejected_cases(document, partial, "9a610ada0000")
    assert [case["title"] for case in shown["test_cases"]] == ["Доработан 1"] and shown["revision"] == 3
    notes = [label for label, _text in extras["notes"]["TC-B1-001"]]
    assert "Замечание ревьюера (круг 1, блокирующее, REVIEW_NOTE):" in notes and "Замечание ревьюера (круг 2, блокирующее, REVIEW_NOTE):" in notes
    assert "после одного круга доработки" in intro


def test_the_set_with_a_case_that_passed_is_r3_and_its_csv_holds_the_case() -> None:
    document, outcome, final = _after(2)
    partial = partial_context(document, outcome, {"state": "reviewed", "final": final})
    bundle = build_bundle(final["effective"], partial=partial)
    assert "Доработан 2".encode("utf-8") in bundle.csv_bytes and "Доработан 1".encode("utf-8") not in bundle.csv_bytes
    assert "Принят частично: 63 из 64 кейсов.".encode("utf-8") in bundle.markdown_bytes


def test_all_reworked_cases_passed_is_the_whole_set_as_r3_without_a_partial_header() -> None:
    document, _outcome, final = _after(1, 2)
    assert (final["kind"], final["effective"]["revision"], len(final["effective"]["test_cases"])) == ("ALL", 3, 64)
    bundle = build_bundle(final["effective"])
    assert "Принят частично" not in bundle.markdown_bytes.decode("utf-8") and "Доработан 1".encode("utf-8") in bundle.csv_bytes


def _acc(state: str, sent: int = 1, accepted: int = 0, outcome: str = "PARTIAL") -> dict:
    total, removed = 66, 1
    return {"verdict_given": "ТРЕБУЕТ ДОРАБОТКИ", "outcome": outcome, "reason": None, "generated": total, "accepted": total - removed + accepted, "removed": removed, "share": 20,
            "rejected_by_findings": 1, "rejected_with_group": 0, "coverage_gaps": [], "rejected_cases": [], "files": {"csv": "c", "set": "s", "rejected": "r"},
            "rework": {"state": state, "sent": sent, "accepted": accepted, "accepted_first": total - removed}}


def _lines(acc: dict) -> str:
    from tools.run_report import _acceptance_lines

    return "\n".join(_acceptance_lines({"acceptance": acc, "reviewer_isolation_state": "verified", "review_canaries": {}}))


def test_the_report_of_a_rework_nobody_reviewed_says_it_was_not_finished_and_has_the_two_rows() -> None:
    text = _lines(_acc("unfinished"))
    assert "ПРИНЯТ ЧАСТИЧНО: 65 из 66 кейсов.** Доработка 1 кейса не завершена." in text
    assert "| Отклонено и отправлено на доработку | 1 |" in text and "| Принято после доработки | 0 |" in text and "1 круг: весь набор." in text


def test_the_report_of_a_reviewed_rework_has_two_rounds_and_the_words_of_the_mock() -> None:
    text = _lines(_acc("reviewed"))
    assert "1 кейс не прошёл ревью после доработки и в набор не вошёл." in text and "2 круга: весь набор, затем только доработанный кейс." in text


def test_the_report_of_a_rework_that_passed_says_how_many_cases_came_after_it() -> None:
    text = _lines({**_acc("reviewed", accepted=1, outcome="ACCEPTED_WHOLE"), "accepted": 66, "removed": 0})
    assert "**ПРИНЯТ: 66 из 66 кейсов (1 — после доработки).**" in text


@pytest.mark.parametrize("left, words", [(1, "1 кейса"), (2, "2 кейсов"), (5, "5 кейсов"), (11, "11 кейсов"), (21, "21 кейса")])
def test_the_line_of_an_unfinished_rework_declines_the_cases_in_the_genitive(left: int, words: str) -> None:
    acc = _acc("unfinished")
    assert f"Доработка {words} не завершена." in _lines({**acc, "removed": left, "accepted": 66 - left})


def test_the_report_with_nothing_sent_back_keeps_the_three_rows_of_v6() -> None:
    for state in (None, "not_started"):
        acc = _acc("not_started")
        text = _lines({**acc, "rework": None if state is None else acc["rework"]})
        assert "Отклонено и отправлено на доработку" not in text and "Принято после доработки" not in text and "| Не принято (вынесено отдельно) | 1 |" in text
        assert "1 кейс не дорабатывался" in text


# ---- the warning of a set that is left with more out than the limit allows is by the cases left out AFTER the rework ------------------

def _after_rework_lines(monkeypatch: pytest.MonkeyPatch, rejected_first: int, rejected_after: int) -> str:
    """The «Итог» block of a 64-case set: the first review rejects ``rejected_first`` cases (the limit of the first round is 50, the run's own 20), the second round rejects ``rejected_after`` of them."""
    from tools import orchestrate_test_case_revision as orchestrate
    from tools import pilot_state, review_canaries

    document = r1_document()
    outcome = _outcome(document, _real(document, *range(1, rejected_first + 1)), share=50)
    answer = {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1",
              "test_cases": [{**copy.deepcopy(document["test_cases"][number - 1]), "title": f"Доработан {number}"} for number in range(1, rejected_first + 1)]}
    again = [_finding("BLOCKING", [document["test_cases"][number - 1]["case_id"]]) for number in range(1, rejected_after + 1)]
    final = rework_final(document, outcome, answer, _report(document, *again) | {"verdict": "ТРЕБУЕТ ДОРАБОТКИ" if again else "ПРИНЯТО"})
    partial = partial_context(document, outcome, {"state": "reviewed", "final": final})
    monkeypatch.setattr(pilot_state, "terminal_reviewer_evidence", lambda *_args: {"session_complete": True, "authoritative_verdict_count": 1, "authoritative_verdict": "ACCEPTED"})  # a partial set is accepted by the ledger
    monkeypatch.setattr(pilot_state, "read_effective_canonical_if_present", lambda *_args: {"document": document})
    monkeypatch.setattr(review_canaries, "verify", lambda *_args: ())
    monkeypatch.setattr(orchestrate, "journal_assembly", lambda *_args: (document, None, None))
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "configured_share", lambda *_args: 20)
    monkeypatch.setattr(orchestrate, "rework_independent", lambda *_args: True)
    monkeypatch.setattr(orchestrate, "_partial_of", lambda *_args: ("reviewed", final, partial))
    summary = orchestrate.acceptance_summary("run", "attempt", {"artifacts": {"validation_report": _report(document, *_real(document, *range(1, rejected_first + 1)))}})
    assert summary is not None
    return _lines(summary)


WARNING = "Не вошло больше допустимого"


def test_no_warning_when_the_rework_took_back_every_case_the_review_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    assert WARNING not in _after_rework_lines(monkeypatch, 26, 0)  # 26 of 64 (40 %) went back, all of them passed


def test_no_warning_when_the_cases_left_out_after_the_rework_are_within_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert WARNING not in _after_rework_lines(monkeypatch, 26, 12)  # 40 % rejected first, 12 of 64 (18,8 %) are out after the rework


def test_the_warning_counts_the_cases_left_out_after_the_rework(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Не вошло больше допустимого: 19 из 64 (предел 20 %)" in _after_rework_lines(monkeypatch, 26, 19)  # not 26, the rejected of the first round
