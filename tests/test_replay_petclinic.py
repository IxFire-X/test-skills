"""Planner decisions of the live Petclinic runs of 2026-10-08, replayed offline on their recorded answers.

No driver, no model: the plan is built by the current code from the recorded snapshot and the real answers are
bound to it (``tests/replay_petclinic.py``).  Every live-run planning bug gets its test here, on real data.
"""
from __future__ import annotations

import copy

import replay_petclinic as rp
from tools import review_compact as rc


def _plan(key: str):
    """The plan run d's answers of review ``key`` were given for, built by the current code."""
    return rp.plan(key, run_lint=key != "canonical")


def _additions(key: str, results):
    plan = _plan(key)
    return plan, rc.additional_parts(plan, rp.payload(key), results)


def _bound(key: str, **options):
    plan = _plan(key)
    return [rp.bind(plan, answer) for answer in rp.answers(key, **options)]


def test_the_base_case_review_parts_are_those_run_d_answered() -> None:
    """Answers of earlier runs can be carried into a new one only while the base parts stay byte for byte the same."""
    recorded = rp.snapshot("canonical")
    plan = rp.plan("canonical")
    assert len(plan["parts"]) == 38
    assert [part["text"] for part in plan["parts"]] == recorded["base_part_texts"]


def test_every_recorded_answer_is_valid_for_the_current_plan() -> None:
    for key, part_2 in (("canonical", "d"), ("canonical", "b"), ("r1", "d")):
        plan = _plan(key)
        payload = rp.payload(key)
        parts = {part["part_id"]: part for part in plan["parts"]}
        for result in _bound(key, part_2=part_2):
            assert rc.validate_answer(plan, parts[result["part_id"]], result, payload["document"], payload.get("automation")) == [], (key, part_2, result["part_id"])


def test_case_review_additions_fit_their_limit_and_budget() -> None:
    for part_2 in ("d", "b"):
        plan, additions = _additions("canonical", _bound("canonical", part_2=part_2))
        assert 0 < len(additions) <= min(rc.check_limit(plan), 9), part_2
        assert all(part["blocked_reason"] is None for part in additions), part_2
        checks = rc.required_checks(plan, rp.payload("canonical"), _bound("canonical", part_2=part_2))
        assert len({rc.review_digest(check) for check in checks}) == len(checks)


def test_an_info_correction_is_never_rechecked() -> None:
    plan = rp.plan("canonical")
    results = _bound("canonical")
    info = [item for result in results for item in result["corrections"] if rc._correction_level(result, item) == "INFO"]
    assert info, "the recorded answers carry INFO corrections"
    rechecked = [item for check in rc.required_checks(plan, rp.payload("canonical"), results) for item in check.get("corrections", [])]
    assert not [item for item in info if item in rechecked]


def test_a_check_part_naming_source_requirements_carries_their_text() -> None:
    plan, additions = _additions("canonical", _bound("canonical"))
    payload = rp.payload("canonical")
    sreq = {item["source_requirement_id"]: item for item in payload["document"]["source_requirements"]}
    named = [part for part in additions if "## SREQ" in part["text"]]
    assert named
    for part in named:
        assert any(f"[{identifier}]" in part["text"] for identifier in sreq)


def test_a_check_of_every_source_requirement_is_too_broad_not_81_cases() -> None:
    """Run b: the source continuation part asked to check every SREQ — all 81 cases, a 577 KB part, the run PARTIAL.
    A check whose requirement cases do not fit one check part checks only the cases it names; with none it is
    reported to the analyst and does not count against the addition limit."""
    plan = rp.plan("canonical")
    payload = rp.payload("canonical")
    results = _bound("canonical", part_2="b")
    request = next(check for result in results if result["part_id"] == "part-000002" for check in result["required_checks"]
                   if len(check.get("requirement_ids") or []) == 29)
    checks = rc.required_checks(plan, payload, results)
    assert all("часть 1 проверки" not in check["reason"] for check in checks)  # never split into pieces
    assert not [check for check in checks if request["reason"] in check["reason"]]
    aggregate = rc.aggregate(plan, payload, results)
    broad = [row for row in aggregate.get("too_broad", []) if row["part_id"] == "part-000002"]
    assert broad and broad[0]["question"] == request["reason"] and broad[0]["requirement_ids"] == request["requirement_ids"]
    assert any(item["code"] == rc.TOO_BROAD_REASON and item["severity"] == "WARNING" for item in aggregate["findings"])
    assert not any(rc.TOO_BROAD_REASON in row["reason"] or rc.CHECK_LIMIT_REASON in row["reason"] for row in aggregate["unchecked"])
    from tools.analyst_report import too_broad_items

    asked = too_broad_items("canonical", aggregate)
    assert [item["question"] for item in asked if item["source"]["ref"].startswith("canonical:part-000002:")] == [request["reason"]]


def test_a_broad_requirement_with_named_cases_checks_only_those_cases() -> None:
    """Run d, part 2: two named cases and CREQ-B1-T-ISOLATION (linked to all 81 cases) — nine check parts in run d."""
    plan = rp.plan("canonical")
    payload = rp.payload("canonical")
    results = _bound("canonical")
    request = next(check for result in results if result["part_id"] == "part-000002" for check in result["required_checks"]
                   if "CREQ-B1-T-ISOLATION" in (check.get("requirement_ids") or []))
    checks = rc.required_checks(plan, payload, results)
    holder = [check for check in checks if request["reason"] in check["reason"]]
    assert len(holder) == 1 and set(request["case_ids"]) <= set(holder[0]["case_ids"]) and len(holder[0]["case_ids"]) < 81
    assert all("часть 1 проверки" not in check["reason"] for check in checks)


def test_a_too_broad_check_never_closes_an_unchecked_area() -> None:
    """Independent review of F1: a part that left areas UNCHECKED and named only a broad requirement would have been
    closed whole.  The area stays unchecked (the review incomplete); the broad check itself is never unchecked."""
    plan = rp.plan("canonical")
    payload = rp.payload("canonical")
    results = _bound("canonical", part_2="b")
    # Run b's part 2: its source area UNCHECKED, a too broad check and an ordinary one — it waits for the latter.
    aggregate = rc.aggregate(plan, payload, results)
    assert "source-000002" in {row["scope_id"] for row in aggregate["unchecked"]}
    alone = copy.deepcopy(results)
    part = next(result for result in alone if result["part_id"] == "part-000002")
    part["required_checks"] = [check for check in part["required_checks"] if len(check.get("requirement_ids") or []) == 29]
    aggregate = rc.aggregate(plan, payload, alone)
    assert "source-000002" in {row["scope_id"] for row in aggregate["unchecked"]} and not aggregate["complete"]
    assert not aggregate.get("resolved_unchecked") and aggregate["too_broad"]


def test_a_too_broad_check_of_a_checked_part_leaves_the_review_complete() -> None:
    """Run d, autotest review part 4: every area CHECKED and a request to check CREQ-B1-T-ISOLATION — the review is
    complete, with a WARNING and a question for the analyst (no PARTIAL)."""
    plan = _plan("r1")
    payload = rp.payload("r1")
    results = _bound("r1")
    part = next(result for result in results if result["part_id"] == "part-000004")
    part["required_checks"] = [check for check in part["required_checks"] if check.get("requirement_ids") == ["CREQ-B1-T-ISOLATION"]]
    assert part["required_checks"] and rc.additional_parts(plan, payload, results) == []
    aggregate = rc.aggregate(plan, payload, results)
    assert aggregate["complete"] and aggregate["unchecked"] == [] and aggregate["diagnostics"] == []
    assert [row["requirement_ids"] for row in aggregate["too_broad"]] == [["CREQ-B1-T-ISOLATION"]]
    assert any(item["code"] == rc.TOO_BROAD_REASON and item["severity"] == "WARNING" for item in aggregate["findings"])
    from tools.analyst_report import too_broad_items

    assert [item["question"] for item in too_broad_items("r1", aggregate)] == [part["required_checks"][0]["reason"]]


def test_the_automation_review_follows_the_check_policy() -> None:
    """Run d, autotest review part 4 asked to check CREQ-B1-T-ISOLATION (all 81 cases; the reviewer needed schema.sql):
    the automation plan had no check policy — one 559 KB part, REVIEW_CONTEXT_LIMIT.  The automation review follows the
    case review's policy, and its check parts carry the code of their cases."""
    plan = _plan("r1")
    payload = rp.payload("r1")
    results = _bound("r1")
    assert len(plan["parts"]) == 10 and plan.get("check_policy") == rc.CHECK_POLICY and rc.check_limit(plan) == 2
    additions = rc.additional_parts(plan, payload, results)
    assert 0 < len(additions) <= rc.check_limit(plan)
    assert all(part["blocked_reason"] is None and len(part["case_ids"]) < 81 for part in additions)
    for part in additions:
        for case_id in part["case_ids"]:
            assert f"[{case_id}]" in part["text"]
        assert "## Кейсы и их методы" in part["text"] and part["code_ranges"]
    aggregate = rc.aggregate(plan, payload, results)
    assert [row["requirement_ids"] for row in aggregate.get("too_broad", [])] == [["CREQ-B1-T-ISOLATION"]]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:end if end >= 0 else len(text)]


def test_a_check_part_with_source_requirements_lists_only_its_own_requirements() -> None:
    """Run d: a check part carrying SREQ listed every CREQ of the document (nine case-review check parts instead of a few)."""
    import re

    plan, additions = _additions("canonical", _bound("canonical"))
    document = rp.payload("canonical")["document"]
    mapped = {row["source_requirement_id"]: set(row["canonical_requirement_ids"]) for row in document["source_to_canonical_mappings"]}
    linked = {case["case_id"]: set(case["requirement_ids"]) for case in document["test_cases"]}
    named = [part for part in additions if "## SREQ" in part["text"]]
    assert named
    for part in named:
        sreq = set(re.findall(r"^\[(SREQ-\d{4})\]", _section(part["text"], "## SREQ"), re.M))
        listed = set(re.findall(r"^\[(CREQ-[^\]]+)\]", _section(part["text"], "## Требования CREQ"), re.M))
        expected = {item for case_id in part["case_ids"] for item in linked[case_id]} | {item for source in sreq for item in mapped[source]}
        assert sreq and listed == expected, (part["part_id"], sorted(listed - expected)[:5])
    assert len(additions) <= min(rc.check_limit(plan), 9)
    assert all(part["blocked_reason"] is None for part in additions)


def test_an_assert_id_inside_a_string_literal_counts_as_present() -> None:
    """Run d: the code labels checks as .as(x.label("ASSERT-B1-0042 response_status")); the linter looked for exactly
    "ASSERT-B1-0042" and raised 1285 false suspicions over ten parts, which the reviewers dismissed by script."""
    from tools.review_lint import names_id

    assert names_id('.as(label("ASSERT-B1-0042 response_status"))', "ASSERT-B1-0042")
    assert names_id("x.as('ASSERT-B1-0042')", "ASSERT-B1-0042")
    assert names_id('assertThat(a).as("поле ASSERT-B1-0042: имя").isEqualTo(b)', "ASSERT-B1-0042")
    assert not names_id('.as("ASSERT-B1-0010 поле")', "ASSERT-B1-001")
    assert not names_id('.as("ASSERT-B1-00420")', "ASSERT-B1-0042")
    assert not names_id("// ASSERT-B1-0042 in a comment", "ASSERT-B1-0042")
    current = rp.plan("r1")
    # Without the false suspicions the same cases and code fit eight parts instead of ten.
    assert len(current["parts"]) == 8 and all(part["blocked_reason"] is None for part in current["parts"])
    lint = current["lint"]
    missing = [row for row in lint if row["rule"] == "assert-id-missing"]
    code = rp.payload("r1")["automation"]["artifacts"]["generated_files"][0]["content"]
    assert len(missing) < 10, len(missing)
    assert all(not names_id(code, row["related_ids"][0]) for row in missing)


def test_a_symbol_id_of_a_case_of_the_part_is_a_valid_ref() -> None:
    """Run d, autotest review part 5: the reviewer cited the method's SYMBOL ID, shown in the part text without
    brackets; the answer was rejected with REVIEW_REF_UNKNOWN.  The IDs of the part's own symbols and files are refs,
    a symbol of a case outside the part is not."""
    plan = _plan("r1")
    payload = rp.payload("r1")
    relations = payload["automation"]["artifacts"]["implementation_relations"]
    result = next(row for row in _bound("r1") if row["part_id"] == "part-000005")
    part = next(item for item in plan["parts"] if item["part_id"] == "part-000005")
    index, row = next((index, row) for index, row in enumerate(result["coverage"]) if row["area_id"].startswith("local-TC-"))
    case_id = row["area_id"][len("local-"):]
    own = next(item for item in relations if item["case_id"] == case_id)
    foreign = next(item for item in relations if item["case_id"] not in part["case_ids"])
    assert own["symbol_id"] in part["text"] and f"[{own['symbol_id']}]" not in part["text"]
    for refs, codes in (([own["symbol_id"]], []), ([own["file_id"], own["symbol_id"]], []), ([foreign["symbol_id"]], ["REVIEW_REF_UNKNOWN"])):
        answer = copy.deepcopy(result)
        answer["coverage"][index]["refs"] = refs
        found = [item["code"] for item in rc.validate_answer(plan, part, answer, payload["document"], payload["automation"])]
        assert found == codes, (refs, found)
