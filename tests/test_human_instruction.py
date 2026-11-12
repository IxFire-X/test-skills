"""RV: the instruction `prepare` of the review gives the person has two branches — INDEPENDENT asks for a fresh session, SELF does not."""

from __future__ import annotations

from pathlib import Path

from tests.test_review_requested import _generated_run
from tests.test_review_two_commands import _open


def test_for_human_of_an_independent_review_asks_for_a_fresh_session(tmp_path: Path) -> None:
    code, opened = _open(_generated_run(tmp_path))
    assert code == 0 and "/clear" in opened["result"]["for_human"], opened


def test_for_human_of_a_self_review_asks_for_no_new_session(tmp_path: Path) -> None:
    code, opened = _open(_generated_run(tmp_path, self_review=True), fresh=None)
    text = opened["result"]["for_human"]
    assert code == 0 and "SELF" in text and "/clear" not in text and "новую сессию" not in text, opened
