"""q3f: an outcome the document does not describe is a question for the analyst in ``analyst_questions`` of the fragment, not a case."""
from __future__ import annotations

import pytest

from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh
from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
from tools.run_report import analyst_questions


def _ready():
    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    return header, context, plan, _fragment(plan, context, 0)


def test_a_case_with_the_label_of_an_observation_is_refused() -> None:
    header, context, plan, fragment = _ready()
    fragment["test_cases"][0].setdefault("management", {})["labels"] = ["наблюдение"]
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(fragment, plan, header, context)
    assert "SEMANTIC_OBSERVATION_CASE" in [row["code"] for row in refused.value.diagnostics]


def test_a_question_of_the_generator_is_no_case_and_no_part_of_the_document() -> None:
    header, context, plan, fragment = _ready()
    fragment["analyst_questions"] = [{"question": "Что вернуть при отмене чужой записи?", "requirement_ids": [fragment["requirements"][0]["requirement_id"]]}]
    _refresh(fragment)
    validate_fragment(fragment, plan, header, context)
    candidate, _receipt = assemble_candidate(header, plan, [fragment, _fragment(plan, context, 1)])
    assert len(candidate["test_cases"]) == 2 and "отмене чужой" not in str(candidate)


def test_a_question_that_names_a_requirement_of_another_batch_is_refused() -> None:
    header, context, plan, fragment = _ready()
    fragment["analyst_questions"] = [{"question": "Что вернуть?", "requirement_ids": ["CREQ-B9-nope"]}]
    _refresh(fragment)
    with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
        validate_fragment(fragment, plan, header, context)


def test_the_questions_file_holds_the_questions_of_the_generator() -> None:
    text = analyst_questions([], generated=[{"requirement_ids": ["CREQ-B1-005"], "question": "Что вернуть при отмене чужой записи?"}])
    assert "## Вопросы генератора" in text and "- CREQ-B1-005: Что вернуть при отмене чужой записи?" in text
