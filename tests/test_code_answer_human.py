"""An answer the code of the package built that fails the check says so to the tester — the approved words (27.11, «На всё согласен»):
a repeat of a model does not mend it.  An answer of a model keeps the words of before."""
from __future__ import annotations

from tools.fixes import CODE_ANSWER_FIX
from tools.json_cli import error_envelope

WORDS = ("Требования собрал код пакета, и они не прошли проверку формата — это ошибка пакета, а не модели. "
         "Покажите это сообщение разработчикам пакета.")


def test_an_answer_of_the_code_says_it_is_the_packages_error() -> None:
    assert error_envelope(ValueError("MODEL_STAGE_ARTIFACT_INVALID: rows; fix: " + CODE_ANSWER_FIX))["human"] == WORDS
    assert error_envelope(ValueError("IMPORT_REQUIREMENTS_INVALID: the CSV rows"))["human"] == WORDS
    assert error_envelope(ValueError("MODEL_STAGE_ARTIFACT_INVALID: rows; fix: поправь поле"))["human"].startswith("Ответ модели не подошёл")
