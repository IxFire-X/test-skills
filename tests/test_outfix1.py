"""outfix1: the places where the output of the package misled an agent in the two live runs of 16.11 (one behaviour per test)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_param_groups import EXAMPLE, grouped
from tests.test_review_stage import _answer as _review_answer, _opened, _submit
from tests.test_rework_selection import _answer, _first, r1_document
from tools.quarantine import failure_note
from tools.review_view import render
from tools.revision_selection import SelectionError, check_rework
from tools.step_size import plan_view_bytes

BLANKS = ("Anna", "", "   ")


def _manual_document() -> tuple[dict, str]:
    """The example whose last step of the first case is manual: no operation, inputs, outputs or assertions."""
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    step = document["test_cases"][0]["steps"][-1]
    step.update(manual_only=True, manual_reason="Проверяется глазами.", operation=None, inputs=[], outputs=[])
    for expectation in step["expectations"]:
        expectation["assertions"] = []
    return document, step["step_id"]


def _lines_after(view: str, step_id: str) -> str:
    lines = view.splitlines()
    start = next(index for index, text in enumerate(lines) if f"[{step_id}]" in text)
    return lines[start + 1]


@pytest.mark.parametrize("profile", ["autotest", "autotest-review"])
def test_a_manual_step_shows_a_dash_as_its_operation_not_null(profile: str) -> None:
    document, step_id = _manual_document()
    assert _lines_after(render(document, profile=profile).decode("utf-8"), step_id) == "   Операция: —"


def test_the_legend_of_the_reviewer_view_names_the_operation_of_a_manual_step() -> None:
    document, _step_id = _manual_document()
    legend = next(line for line in render(document, profile="autotest-review").decode("utf-8").splitlines() if line.startswith("- Вид для ревьюера"))
    assert "`Операция: —`" in legend


def _group_rows(variants: tuple[str, ...]) -> list[str]:
    view = render(grouped(variants)).decode("utf-8")
    block = view[view.index("### PG-first-name"):]
    return [line for line in block.split("\n### ")[0].splitlines() if line.startswith("| ") and "TC-B1-001" in line]


def test_an_empty_literal_of_a_group_is_shown_in_quotes() -> None:
    assert '| "" |' in _group_rows(BLANKS)[1]


def test_a_blank_literal_of_a_group_is_shown_in_quotes_with_its_spaces() -> None:
    assert '| "   " |' in _group_rows(BLANKS)[2]


def test_a_group_case_of_the_autotest_view_names_its_param_group() -> None:
    view = render(grouped(), profile="autotest").decode("utf-8")
    assert view.count("- param_group: PG-first-name") == 3


def test_a_case_without_a_group_has_no_param_group_line() -> None:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert "param_group" not in render(document, profile="autotest").decode("utf-8")


def test_the_refusal_of_a_rework_names_the_place_of_the_error() -> None:
    document = r1_document()

    with pytest.raises(SelectionError) as raised:
        check_rework(document, _first(document, 3), _answer(document, 3, steps=[]))
    row = raised.value.diagnostics[0]
    assert row["code"] == "REWORK_INVALID" and row["message"].startswith("/test_cases/2/steps (TC-B1-003): ")


def test_an_empty_evidence_list_is_refused_and_the_fix_says_to_remove_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    run = _opened(tmp_path, monkeypatch, capsys)
    code, out = _submit(capsys, run, {**_review_answer(run), "evidence": []})
    assert code == 2 and out["code"] == "REVIEWER_EVIDENCE_PATH_UNKNOWN" and "пустой `evidence` — убери ключ" in out["fix"]


def test_the_plan_view_does_not_tell_the_generator_to_copy_a_key_the_fragment_has_not() -> None:
    text = json.loads(plan_view_bytes({"source_requirements": [{"source_requirement_id": "SREQ-1", "text": "t"}]}))["texts"]
    assert "source_requirements фрагмента" not in text and "context-marker" in text


def test_the_fragment_schema_has_no_source_requirements_key() -> None:
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "candidate-fragment.schema.json").read_text(encoding="utf-8"))
    assert "source_requirements" not in schema["properties"] and schema["additionalProperties"] is False


def test_an_assertj_label_in_brackets_is_the_ref_and_not_the_message() -> None:
    assert failure_note("[ASSERT-B1-001-02-1-1] \nExpecting:\n <1>\nto be equal to:\n <2>") == ("ASSERT-B1-001-02-1-1", "")


def test_an_assertj_label_in_brackets_keeps_the_text_after_it() -> None:
    assert failure_note("[ASSERT-a-1] expected: <2> but was: <3>") == ("ASSERT-a-1", "expected: _2_ but was: _3_")
