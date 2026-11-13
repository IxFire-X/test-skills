"""V8 (P10): the orchestrate SKILL is a map of the stages — the arguments are in ``--help`` and the next step in ``state`` and ``next`` of ``status``, the detail is in references.

The map stays short, carries no ``FaT-`` marks, names every command of the stage steps; the
commands and flags it gives are checked against the real parsers by ``test_texts_cases_only``."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import stages


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate" / "SKILL.md"
MAP_LINES = 181  # V8: 419 lines / 52 KB before; the limit only goes down
REFERENCES = ("openspec.md", "review.md", "import.md")


def _text() -> str:
    return SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_skill_is_a_map_not_a_manual() -> None:
    assert len(_text().splitlines()) <= MAP_LINES


def test_the_texts_carry_no_fat_marks() -> None:
    offenders = [path.name for path in (ROOT / "skills" / "orchestrate").rglob("*.md") if path.name != "mode-b.md" and "FaT-" in path.read_text(encoding="utf-8")]
    assert offenders == []


@pytest.mark.parametrize("name", REFERENCES)
def test_every_reference_of_the_map_is_named_by_the_map(name: str) -> None:
    assert f"(references/{name}" in _text()
    assert (ROOT / "skills" / "orchestrate" / "references" / name).is_file()


def test_every_cases_only_verb_of_next_is_named_in_the_map() -> None:
    verbs = {"prepare", "submit", "finish", "report", "scan"}
    text = _text()
    assert verbs <= set(stages.VERBS) and all(f"`{verb}" in text or f" {verb}" in text or f"{verb}`" in text for verb in verbs)


def test_the_arguments_are_left_to_help_and_the_state_of_status() -> None:
    text = " ".join(_text().split())
    assert "`python -m tools.api <команда> --help`" in text and "`state`" in text and "`next`" in text and "`next_step`" not in text.replace("`next_step` вывода", "")
