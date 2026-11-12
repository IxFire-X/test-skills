"""V7a: the rework of the rejected cases — the answer of the generator checked against r1 and r2 (r3), the set after the second round, the groups.

Pure functions of ``tools.revision_selection`` over the example document; the commands of the agent are in ``test_rework_stage``."""
from __future__ import annotations

import copy

import pytest

from tests.test_partial_acceptance import _finding, _grouped, _group_ids, _ids, _outcome, _real, _report, r1_document
from tools.revision_selection import SelectionError, check_rework, rework_final, rework_request


def _answer(document: dict, *numbers: int, **change) -> dict:
    cases = [{**copy.deepcopy(document["test_cases"][number - 1]), "title": "Доработанный кейс", **change} for number in numbers]
    return {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": cases}


def _first(document: dict, *numbers: int, extra=()) -> dict:
    return _outcome(document, [*_real(document, *numbers), *extra])


def _review(document: dict, verdict: str = "ТРЕБУЕТ ДОРАБОТКИ", *findings: dict) -> dict:
    return _report(document, *findings) | {"verdict": verdict}


def _code(call) -> str:
    with pytest.raises(SelectionError) as raised:
        call()
    return raised.value.diagnostics[0]["code"]


def test_the_view_holds_the_rejected_cases_and_their_blocking_findings_cut_to_the_limit() -> None:
    document = r1_document()
    outcome = _outcome(document, [_finding("BLOCKING", ["TC-B1-001"], "Ж" * 5000), _finding("WARNING", ["TC-B1-001"], "мелочь")])
    view = rework_request(document, outcome)
    assert [case["case_id"] for case in view["cases"]] == ["TC-B1-001"] and len(view["findings"]["TC-B1-001"]) == 1
    assert len(view["findings"]["TC-B1-001"][0]["message"]) == 2000


def test_r3_is_r2_with_the_reworked_case_at_its_place() -> None:
    document = r1_document()
    outcome = _first(document, 3)
    r3 = check_rework(document, outcome, _answer(document, 3))
    assert (r3["revision"], r3["parent_sha256"] is not None, len(r3["test_cases"])) == (3, True, 64)
    assert r3["test_cases"][2]["title"] == "Доработанный кейс" and r3["test_cases"][2]["display_order"] == 3


@pytest.mark.parametrize("answer, code", [
    (lambda d: _answer(d, 3, 4), "REWORK_FOREIGN_CASE_CHANGED"),
    (lambda d: _answer(d), "REWORK_CASE_SET"),
    (lambda d: {**_answer(d, 3), "test_cases": _answer(d, 3)["test_cases"] * 2}, "REWORK_CASE_SET"),
    (lambda d: _answer(d, 3, case_id="TC-B1-099"), "REWORK_CASE_SET"),
    (lambda d: _answer(d, 3, requirement_ids=["CREQ-B1-002"]), "REWORK_CASE_IDENTITY"),
    (lambda d: _answer(d, 3, display_order=70), "REWORK_CASE_IDENTITY"),
    (lambda d: {**_answer(d, 3), "extra": 1}, "REWORK_ANSWER_FORM"),
    (lambda d: {**_answer(d, 3), "batch_id": "rework-2"}, "REWORK_ANSWER_FORM"),
    (lambda d: _answer(d, 3, steps=[]), "REWORK_INVALID"),
    (lambda d: _answer(d, 3, case_id=["TC-B1-003"]), "REWORK_ANSWER_FORM"),
    (lambda d: _answer(d, 3, case_id={"id": 1}), "REWORK_ANSWER_FORM"),
    (lambda d: {**_answer(d, 3), "test_cases": [{}]}, "REWORK_ANSWER_FORM"),
])
def test_an_answer_that_is_not_exactly_the_rejected_cases_is_refused(answer, code: str) -> None:
    document = r1_document()
    assert _code(lambda: check_rework(document, _first(document, 3), answer(document))) == code


def test_a_reworked_case_cannot_leave_a_requirement_without_a_case() -> None:
    from tools.revision_selection import coverage_gaps

    document = r1_document()
    outcome = _first(document, 1)  # the three requirements only TC-B1-001 covered are gaps of r2
    r3 = check_rework(document, outcome, _answer(document, 1))
    assert coverage_gaps(r3) == [] and coverage_gaps(outcome["effective"]) != []


def test_the_reworked_case_that_passes_is_in_r3_and_the_rest_is_r2() -> None:
    document = r1_document()
    final = rework_final(document, _first(document, 3, 4), _answer(document, 3, 4), _review(document, "ПРИНЯТО"))
    assert (final["kind"], final["accepted_after"], final["rejected"]) == ("ALL", ["TC-B1-003", "TC-B1-004"], [])
    assert (final["effective"]["revision"], len(final["effective"]["test_cases"])) == (3, 64)


def test_one_of_two_reworked_cases_not_accepted_is_in_the_rejected_set_with_the_second_finding() -> None:
    document = r1_document()
    report = _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["TC-B1-004"], "Снова не то."))
    final = rework_final(document, _first(document, 3, 4), _answer(document, 3, 4), report)
    assert (final["kind"], final["accepted_after"], final["rejected"]) == ("PARTIAL", ["TC-B1-003"], ["TC-B1-004"])
    assert [item["message"] for item in final["said"]["TC-B1-004"]] == ["Снова не то."]
    assert [case["case_id"] for case in final["effective"]["test_cases"]].count("TC-B1-004") == 0 and len(final["effective"]["test_cases"]) == 63


def test_no_case_passing_leaves_r2_itself() -> None:
    document = r1_document()
    outcome = _first(document, 3)
    final = rework_final(document, outcome, _answer(document, 3), _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["TC-B1-003"])))
    assert (final["kind"], final["effective"]) == ("NONE", outcome["effective"])


def test_a_blocking_finding_without_a_case_rejects_every_reworked_case() -> None:
    document = r1_document()
    final = rework_final(document, _first(document, 3, 4), _answer(document, 3, 4), _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["CREQ-B1-002"])))
    assert (final["kind"], final["rejected"]) == ("NONE", ["TC-B1-003", "TC-B1-004"])


def test_a_blocking_finding_on_a_case_that_was_not_reworked_is_ignored_and_counted() -> None:
    document = r1_document()
    report = _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["TC-B1-010"]), _finding("BLOCKING", ["TC-B1-003"]))
    final = rework_final(document, _first(document, 3), _answer(document, 3), report)
    assert (final["ignored"], final["rejected"], len(final["effective"]["test_cases"])) == (1, ["TC-B1-003"], 63)
    only = rework_final(document, _first(document, 3), _answer(document, 3), _review(document, "ПРИНЯТО", _finding("BLOCKING", ["TC-B1-010"])))
    assert (only["kind"], only["ignored"]) == ("ALL", 0)  # ПРИНЯТО stands; the case the first round accepted is never a candidate for the second
    other = rework_final(document, _first(document, 3), _answer(document, 3), _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["TC-B1-003"]), _finding("BLOCKING", ["TC-B1-010"])))
    assert "TC-B1-010" in [case["case_id"] for case in other["effective"]["test_cases"]]  # the accepted case stays, r2's


def test_a_review_that_sends_back_with_nothing_blocking_passes_no_case() -> None:
    document = r1_document()
    final = rework_final(document, _first(document, 3), _answer(document, 3), _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("WARNING", ["TC-B1-003"])))
    assert final["kind"] == "NONE"


@pytest.mark.parametrize("verdict, successor", [("AUTO_FIX_APPLIED", None), ("ПРИНЯТО", {"revision": 2})])
def test_the_second_round_takes_no_fix_and_no_successor(verdict: str, successor) -> None:
    document = r1_document()
    assert _code(lambda: rework_final(document, _first(document, 3), _answer(document, 3), _review(document, verdict), successor)) == "REWORK_REVIEW_FIX"


def test_the_set_after_the_second_round_is_validated_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import canonical_document

    document, calls, real = r1_document(), [], canonical_document.validate_canonical_document

    def second_time_bad(value: dict) -> list:
        calls.append(1)
        return real(value) if len(calls) == 1 else [{"path": "/", "code": "X", "message": "y"}]  # r3 of the answer passes, the final set does not

    monkeypatch.setattr(canonical_document, "validate_canonical_document", second_time_bad)
    assert _code(lambda: rework_final(document, _first(document, 3), _answer(document, 3), _review(document, "ПРИНЯТО"))) == "REWORK_FINAL_INVALID"


def test_a_variant_of_a_group_that_left_with_its_sibling_comes_back_when_the_sibling_passes() -> None:
    document = _grouped()
    ids = _group_ids(document)
    outcome = _outcome(document, [_finding("BLOCKING", ids[:2])])  # three variants, two rejected: the third goes out with them
    answer = {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1",
              "test_cases": [{**copy.deepcopy(case), "title": case["title"] + " ."} for case in document["test_cases"] if case["case_id"] in ids[:2]]}
    one = rework_final(document, outcome, answer, _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [ids[1]])))
    assert (one["accepted_after"], one["with_group"]) == ([ids[0]], [])  # the first passes, the dragged third returns beside it
    assert ids[2] in [case["case_id"] for case in one["effective"]["test_cases"]]
    none = rework_final(document, outcome, answer, _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ids[:2])))
    assert (none["kind"], none["with_group"]) == ("NONE", [ids[2]])


def _pair() -> tuple[dict, list[str]]:
    """A group of two variants: the first is rejected, the second goes out with it."""
    from tests.test_param_groups import grouped

    document = grouped(names=("Иван", "Пётр"))
    document["revision"], document["parent_sha256"] = 1, None
    return document, _group_ids(document)


def _variants(document: dict, ids: list[str], *chosen: int) -> dict:
    return {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1",
            "test_cases": [{**copy.deepcopy(case), "title": case["title"] + " ."} for case in document["test_cases"] if case["case_id"] in [ids[number] for number in chosen]]}


def test_a_group_of_two_with_one_rejected_variant_can_be_reworked_and_r3_holds_both() -> None:
    document, ids = _pair()
    outcome = _outcome(document, [_finding("BLOCKING", [ids[0]])])
    assert (outcome["rejected"], outcome["with_group"]) == ([ids[0]], [ids[1]])
    r3 = check_rework(document, outcome, _variants(document, ids, 0))
    assert [case["case_id"] for case in r3["test_cases"] if case["case_id"] in ids] == ids


def test_a_group_of_two_whose_reworked_variant_passed_is_whole_again_and_the_set_is_all() -> None:
    document, ids = _pair()
    outcome = _outcome(document, [_finding("BLOCKING", [ids[0]])])
    final = rework_final(document, outcome, _variants(document, ids, 0), _review(document, "ПРИНЯТО"))
    assert (final["kind"], final["rejected"], final["with_group"]) == ("ALL", [], [])
    assert len(final["effective"]["test_cases"]) == len(document["test_cases"])


def test_a_variant_the_reviewer_passed_but_left_alone_in_its_group_is_out_with_the_group_not_rejected() -> None:
    document = _grouped()
    ids = _group_ids(document)
    outcome = _outcome(document, [_finding("BLOCKING", ids[:2])])
    answer = _variants(document, ids, 0, 1)
    lone = rework_final(document, outcome, answer, _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [ids[1]])))
    assert lone["accepted_after"] == [ids[0]] and lone["with_group"] == []  # the passed one and the dragged third: two live variants, the group is back
    both = rework_final(document, _outcome(document, [_finding("BLOCKING", ids[:2])]), answer, _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [ids[1], ids[0]])))
    assert both["kind"] == "NONE" and both["with_group"] == [ids[2]] and both["rejected"] == ids[:2]
    pair, pids = _pair()  # both variants rejected, the second one again in the second round: the passed first one is alone in its group
    outcome = _outcome(pair, [_finding("BLOCKING", pids)])
    alone = rework_final(pair, outcome, _variants(pair, pids, 0, 1), _review(pair, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", [pids[1]])))
    assert (alone["kind"], alone["rejected"], alone["with_group"], alone["accepted_after"]) == ("NONE", [pids[1]], [pids[0]], [])


def test_a_variant_taken_out_with_its_group_is_no_case_of_the_rework() -> None:
    document = _grouped()
    ids = _group_ids(document)
    outcome = _outcome(document, [_finding("BLOCKING", ids[:2])])  # the third variant is out with them but no finding names it
    answer = {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": [copy.deepcopy(case) for case in document["test_cases"] if case["case_id"] in ids]}
    assert _code(lambda: check_rework(document, outcome, answer)) == "REWORK_CASE_SET"


# ---- the second round: the view and the binding of the set to the two answers --------------------------------------------------------------

def test_the_view_of_the_second_round_holds_the_reworked_cases_the_first_findings_and_what_they_stand_on() -> None:
    from tools.revision_selection import rework_review_request

    document = r1_document()
    outcome = _outcome(document, [_finding("BLOCKING", ["TC-B1-003"], "Нет проверки состояния.")])
    answer = _answer(document, 3)
    view = rework_review_request(document, outcome, answer, check_rework(document, outcome, answer))
    assert view["reviewed_case_ids"] == ["TC-B1-003"] and [case["title"] for case in view["cases"]] == ["Доработанный кейс"] and view["candidate"]["revision"] == 3
    assert view["first_round"]["TC-B1-003"][0]["message"] == "Нет проверки состояния." and {row["requirement_id"] for row in view["requirements"]} == set(document["test_cases"][2]["requirement_ids"])
    assert "TC-B1-004" not in str(view["cases"])  # a case of the first round is not in the view


def test_the_set_is_bound_to_what_the_two_answers_give_and_to_nothing_else() -> None:
    from tools.revision_selection import partial_is_bound

    document = r1_document()
    findings = _real(document, 3, 4)
    outcome, report1 = _outcome(document, findings), _report(document, *findings)
    answer = _answer(document, 3, 4)
    passes, rejects = _review(document, "ПРИНЯТО"), _review(document, "ТРЕБУЕТ ДОРАБОТКИ", _finding("BLOCKING", ["TC-B1-004"]))
    whole = rework_final(document, outcome, answer, passes)["effective"]
    one = rework_final(document, outcome, answer, rejects)["effective"]
    assert partial_is_bound(document, report1, (), whole, (answer, passes)) and partial_is_bound(document, report1, (), one, (answer, rejects))
    assert not partial_is_bound(document, report1, (), whole, (answer, rejects))  # a case the second answer rejected is in the set
    assert not partial_is_bound(document, report1, (), one, (answer, passes))  # a case it accepted is not
    assert not partial_is_bound(document, report1, (), whole)  # a set of the rework with no record of the second round
    assert partial_is_bound(document, report1, (), outcome["effective"])  # r2 itself, as before the rework
