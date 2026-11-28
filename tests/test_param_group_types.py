"""Campaign v0.8 C1, finding 2: the variants of a param_group may differ in the json type of a literal (null and "" of one field) —
the value is the variant, its type follows it (SEMANTIC_TYPE_MISMATCH binds the two in each case); no loop of two fixes."""
from __future__ import annotations

from tests.test_param_groups import _codes, grouped


def _literal_of_another_type(value: object, type_name: str) -> dict:
    document = grouped(("Anna", "Boris"))
    second = next(case for case in document["test_cases"] if case["case_id"].endswith("-V1"))
    item = next(item for step in second["steps"] for item in step["inputs"] if item["source"]["kind"] == "literal"
                and isinstance(item["source"]["value"], dict) and item["source"]["value"].get("firstName") == "Boris")
    item["source"]["value"], item["semantic_type"] = value, {"kind": "json", "type": type_name}
    return document


def test_a_variant_whose_literal_has_another_type_stays_in_its_group() -> None:
    codes = {code for _path, code in _codes(_literal_of_another_type(None, "null"))}
    assert not codes & {"SEMANTIC_PARAM_GROUP_MISMATCH", "SEMANTIC_TYPE_MISMATCH"}, codes


def test_a_variant_whose_type_does_not_follow_its_literal_is_still_refused() -> None:
    codes = {code for _path, code in _codes(_literal_of_another_type(None, "string"))}
    assert codes & {"SEMANTIC_TYPE_MISMATCH", "SEMANTIC_CAPABILITY_TYPE_MISMATCH"}, codes  # the input of the example feeds a capability argument


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
