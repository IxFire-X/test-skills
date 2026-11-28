"""Campaign v0.8 C1, finding 2: the variants of a param_group may differ in the json type of a literal (null and "" of one field) —
the value is the variant, its type follows it (SEMANTIC_TYPE_MISMATCH binds the two in each case); no loop of two fixes."""
from __future__ import annotations

import copy
import json

from tests.test_automation_revision_budget import automated_document
from tests.test_param_groups import _codes


def _field_group(first: object, second: object) -> dict:
    """Two cases of one param_group that differ only in one body field, as in C1 (PG-B1-FIRST-NULL-EMPTY: /firstName null and ""); the
    semantic_type of each input follows its literal, as the code fills it in a fragment."""
    document = automated_document()
    document["schema_version"] = "1.1.0"
    case = document["test_cases"][0]
    step = case["steps"][0]
    step["operation"].update(method="POST", path="/owners")
    requirement = next(item for item in document["requirements"] if item["requirement_id"] == case["requirement_ids"][0])
    requirement["design"] = {"parameters": [{"id": "P-first", "name": "имя", "valid": [], "invalid": [{"id": "C-null"}, {"id": "C-empty"}]}]}
    cases = [case, copy.deepcopy(case)]
    for index, (member, value) in enumerate(zip(cases, (first, second))):
        suffix = f"-V{index}" if index else ""
        member["case_id"] += suffix
        member["param_group"] = "PG-first-null-empty"
        member["design_refs"] = [{"parameter": "P-first", "class": ("C-null", "C-empty")[index]}]
        member_step = member["steps"][0]
        member_step["step_id"] += suffix
        for expectation in member_step["expectations"]:
            expectation["expectation_id"] += suffix
            for assertion in expectation["assertions"]:
                assertion["assertion_id"] += suffix
        kind = "null" if value is None else "string"
        member_step["test_data"] = json.dumps({"firstName": value}, indent=2)  # the human body the body inputs follow
        member_step["inputs"] = [{"input_id": f"INPUT-first{suffix}", "display_order": 1, "target": {"location": "body", "pointer": "/firstName", "sensitive": False},
                                  "semantic_type": {"kind": "json", "type": kind}, "source": {"kind": "literal", "value": value}}]
    document["test_cases"] = cases + document["test_cases"][1:]
    for order, member in enumerate(document["test_cases"], 1):
        member["display_order"] = order
    return document


def test_a_group_of_one_field_null_and_empty_has_no_diagnostic() -> None:
    assert _codes(_field_group(None, "")) == []


def test_a_group_whose_field_type_does_not_follow_its_literal_is_refused() -> None:
    document = _field_group(None, "")
    document["test_cases"][0]["steps"][0]["inputs"][0]["semantic_type"]["type"] = "string"  # null declared a string: the fix of round 1 in C1
    assert "SEMANTIC_TYPE_MISMATCH" in {code for _path, code in _codes(document)}


def test_the_fix_of_a_refused_fragment_names_the_retry_once() -> None:
    from tools.model_steps import _fragment_fix

    retry = "отдай исправленный фрагмент единственным повтором стадии (--transport-attempts 2), ничего не записано"
    own = "исправь все 6 диагностик из diagnostics за один заход (у каждой свой fix) и подай фрагмент единственным повтором стадии"
    said = _fragment_fix(own, retry)
    assert said.count("повтором стадии") == 1 and "--transport-attempts 2" in said and said.endswith("ничего не записано"), said


def test_a_fix_without_the_retry_gets_the_way_to_it() -> None:
    from tools.model_steps import _fragment_fix

    retry = "отдай исправленный фрагмент единственным повтором стадии (--transport-attempts 2), ничего не записано"
    assert _fragment_fix("поставь Name", retry) == "поставь Name; " + retry
