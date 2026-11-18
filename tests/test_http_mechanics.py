"""q2: what the code lays out for an HTTP step — the response template, the type of a path value — and what it still leaves to the check."""
from __future__ import annotations

from typing import Any

import pytest

from tests.test_automation_revision_budget import automated_document
from tools.batch_assembly import fill_fragment_mechanics
from tools.canonical_document import _human_http_alignment, validate_canonical_document

_BASE = {"kind": "environment", "name": "BASE_URL", "provenance": ["test"]}
_STATUS = {"operator": "equals", "actual": {"kind": "http_status"}, "expected": {"kind": "literal", "value": 200}}


def _body(pointer: str, operator: str = "exists", expected: Any = None) -> dict:
    row = {"operator": operator, "actual": {"kind": "http_body", "pointer": pointer, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["test"]}}
    return {**row, "expected": {"kind": "literal", "value": expected}} if operator == "equals" else row


def _step(path: str, *assertions: dict, inputs: list | None = None, outputs: list | None = None, text: str = "Ответ.\n\nHTTP 200 OK") -> dict:
    return {"action": f"GET {path}", "test_data": "Тело запроса отсутствует.",
            "operation": {"kind": "http", "binding_profile": "http-binding-v1", "base_url_source": _BASE, "method": "GET", "path": path},
            "inputs": inputs or [], "outputs": outputs or [], "expectations": [{"text": text, "assertions": [dict(_STATUS), *assertions]}]}


def _path_input(source: dict) -> list:
    return [{"target": {"location": "path", "name": "ownerId"}, "source": source}]


def _laid(*steps: dict, key: str = "") -> tuple[dict, list[str]]:
    """A document whose one case has the lean steps, laid out by the code as submit does."""
    document = automated_document()
    case = document["test_cases"][0]
    case["steps"] = list(steps)
    case["management"]["external_keys"] = {"zephyr_scale": key} if key else {}
    laid, summary = fill_fragment_mechanics({"status": "COMPLETE", "requirements": [], "test_cases": [case]})
    document["test_cases"][0] = laid["test_cases"][0]
    return document, summary


def _text(document: dict) -> str:
    return document["test_cases"][0]["steps"][0]["expectations"][0]["text"]


def _codes(document: dict) -> list[str]:
    return [item["code"] for item in validate_canonical_document(document)]


def test_an_integer_literal_may_stand_in_a_path() -> None:
    document, _summary = _laid(_step("/owners/{ownerId}", inputs=_path_input({"kind": "literal", "value": 7})))
    assert _codes(document) == []


def test_an_integer_output_of_an_earlier_step_may_stand_in_a_path_with_its_type_unchanged() -> None:
    created = {"output_id": "ownerId", "source": {"kind": "http_body", "pointer": "/id"}, "semantic_type": {"kind": "json", "type": "integer"}, "type_provenance": ["test"]}
    document, _summary = _laid(_step("/owners", outputs=[created]),
                               _step("/owners/{ownerId}", inputs=_path_input({"kind": "step_output", "step_id": "STEP-batch-a-001-01", "output_id": "ownerId"})))
    assert _codes(document) == []
    assert document["test_cases"][0]["steps"][1]["inputs"][0]["semantic_type"] == {"kind": "json", "type": "integer"}


def test_an_object_may_not_stand_in_a_path() -> None:
    document, _summary = _laid(_step("/owners/{ownerId}", inputs=_path_input({"kind": "literal", "value": {"id": 7}})))
    assert _codes(document) == ["SEMANTIC_TARGET_TYPE"]


def test_the_code_lays_out_the_response_template_from_the_assertions() -> None:
    document, summary = _laid(_step("/owners", _body("/lastName", "equals", "Davis"), _body("/id")))
    assert "http_body_template ×1" in summary
    assert _text(document) == 'Ответ.\n\nHTTP 200 OK\n\nПример тела:\n{\n  "lastName": "Davis",\n  "id": "<значение присутствующего поля>"\n}'
    assert _codes(document) == []


def test_an_array_of_the_assertions_becomes_a_json_array() -> None:
    document, _summary = _laid(_step("/owners", _body("/items/0/name"), _body("/items/1/name")))
    assert _text(document).endswith('"items": [\n    {\n      "name": "<значение присутствующего поля>"\n    },\n    {\n      "name": "<значение присутствующего поля>"\n    }\n  ]\n}')
    assert _codes(document) == []


def test_the_block_the_model_wrote_is_replaced_by_the_one_of_the_checks() -> None:
    written = 'Ответ.\n\nHTTP 200 OK\n{\n  "id": "<идентификатор>",\n  "name": "Davis",\n  "city": "Madison"\n}'
    document, summary = _laid(_step("/owners", _body("/name", "equals", "Davis"), text=written))
    assert _text(document) == 'Ответ.\n\nHTTP 200 OK\n\nПример тела:\n{\n  "name": "Davis"\n}' and "http_body_replaced ×1" in summary
    assert _codes(document) == []


def test_an_expectation_with_no_check_of_the_body_has_no_block() -> None:
    document, summary = _laid(_step("/owners", text='Владелец удалён.\n\nHTTP 200 OK\n{\n  "id": "<идентификатор>"\n}'))
    assert _text(document) == "Владелец удалён.\n\nHTTP 200 OK" and "http_body_removed ×1" in summary


def test_two_blocks_the_model_wrote_become_the_one_of_the_checks() -> None:
    block = 'Пример тела:\n{\n  "id": "<значение присутствующего поля>"\n}'
    document, _summary = _laid(_step("/owners", _body("/id"), text=f"Ответ.\n\nHTTP 200 OK\n\n{block}\n\n{block}"))
    assert _text(document) == f"Ответ.\n\nHTTP 200 OK\n\n{block}" and _codes(document) == []


def test_the_label_of_the_exact_body_goes_with_the_block_the_model_wrote() -> None:
    written = 'Ответ.\n\nHTTP 200 OK\n\nОжидаемое тело:\n{\n  "id": "<идентификатор>"\n}'
    document, _summary = _laid(_step("/owners", _body("/id"), text=written))
    assert _text(document) == 'Ответ.\n\nHTTP 200 OK\n\nПример тела:\n{\n  "id": "<значение присутствующего поля>"\n}'


def test_a_pointer_the_step_takes_into_an_output_stays_in_the_block() -> None:
    taken = {"output_id": "ownerId", "source": {"kind": "http_body", "pointer": "/id"}, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["test"]}
    document, _summary = _laid(_step("/owners", _body("/name", "equals", "Davis"), outputs=[taken], text='Ответ.\n\nHTTP 200 OK\n{"name": "Davis", "city": "Madison"}'))
    assert _text(document).endswith('{\n  "name": "Davis",\n  "id": "<значение присутствующего поля>"\n}')


def test_a_row_the_descendants_imply_is_not_shown() -> None:
    from tools.batch_assembly import _body_rows, _http_body_template

    length = {"operator": "length_equals", "actual": {"kind": "http_body", "pointer": "/items"}, "expected": {"kind": "literal", "value": 2}}
    template = _http_body_template(_body_rows({}, [_body("/items", "exists"), length, _body("/items/0/name"), _body("/items/1/name")]))
    assert template is not None and template.count("<значение присутствующего поля>") == 2


def test_a_length_the_indexes_below_do_not_give_is_left_to_the_check() -> None:
    from tools.batch_assembly import _body_rows, _http_body_template

    length = {"operator": "length_equals", "actual": {"kind": "http_body", "pointer": "/items"}, "expected": {"kind": "literal", "value": 3}}
    assert _http_body_template(_body_rows({}, [length, _body("/items/0/name"), _body("/items/1/name")])) is None


def test_the_text_the_code_cannot_lay_out_stays_as_the_model_wrote_it() -> None:
    written = 'Ответ.\n\nHTTP 200 OK\n[\n  "a"\n]'
    document, summary = _laid(_step("/owners", _body("", "exists"), text=written))
    assert _text(document) == written and not any("http_body" in name for name in summary)


def test_a_bracket_line_that_is_no_json_leaves_the_text_alone() -> None:
    written = 'Ответ.\n\nHTTP 200 OK\n{ "id": 1'
    document, summary = _laid(_step("/owners", _body("/id"), text=written))
    assert _text(document) == written and not any("http_body" in name for name in summary)


def test_the_example_of_a_text_the_code_laid_out_comes_again_the_same() -> None:
    first, _summary = _laid(_step("/owners", _body("/lastName", "equals", "Davis"), _body("/id")))
    again, summary = _laid(first["test_cases"][0]["steps"][0])
    assert _text(again) == _text(first) and not any("http_body" in name for name in summary)


@pytest.mark.parametrize("pointers", [[""], ["/items/1"]])
def test_a_shape_that_is_not_the_only_one_is_left_to_the_check(pointers: list[str]) -> None:
    document, summary = _laid(_step("/owners", *[_body(pointer) for pointer in pointers]))
    assert _text(document) == "Ответ.\n\nHTTP 200 OK" and "http_body_template ×1" not in summary
    assert _codes(document) == ["SEMANTIC_HUMAN_EXPECTATION_BODY_MISSING"]


def test_the_text_of_an_imported_case_stays_as_the_human_wrote_it() -> None:
    written = 'Ответ.\n\nHTTP 200 OK\n{\n  "id": 1,\n  "city": "Madison"\n}'
    document, summary = _laid(_step("/owners", _body("/id"), text=written), key="PC-T7")
    assert _text(document) == written and not any("http_body" in name for name in summary)


def test_a_second_json_block_in_an_http_expectation_is_refused() -> None:
    block = 'Пример тела:\n{\n  "id": "<значение присутствующего поля>"\n}'
    step = {"operation": {"kind": "http"}, "manual_only": False, "inputs": [], "automation_blockers": [], "test_data": "Тело запроса отсутствует.",
            "expectations": [{"text": f"Ответ.\n\n{block}\n\n{block}", "assertions": [_body("/id")]}]}
    diagnostics: list[dict] = []
    _human_http_alignment(step, (), diagnostics)
    assert [item["code"] for item in diagnostics] == ["SEMANTIC_HUMAN_EXPECTATION_BODY_DUPLICATE"]
