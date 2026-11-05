"""V8 (P10): the orchestrate SKILL is a map of the stages — the arguments are in ``--help`` and in ``next_step``, the detail is in references.

The map stays short, carries no ``FaT-`` marks, names every section ``next_step`` points to and every command of the stage steps; the
commands and flags it gives are checked against the real parsers by ``test_texts_cases_only``."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.run_pipeline import _MODEL_RESPONSE, _NEXT_STEPS, _TERMINAL

pytestmark = pytest.mark.contract

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


def test_every_section_of_next_step_is_a_section_of_the_map() -> None:
    sections = {row[3] for row in _NEXT_STEPS.values()} | {_MODEL_RESPONSE[2], _TERMINAL[3]}
    headings = re.findall(r"^## (.+)$", _text(), re.M)
    missing = [section for section in sorted(sections)
               if not any(heading.startswith(f"{section}.") or heading == section for heading in headings)]
    assert missing == []


def test_every_cases_only_step_of_next_step_is_named_in_the_map() -> None:
    steps = {"plan-batches", "model-request", "model-response", "review-open", "review-select", "finish", "report", "merge", "scan"}
    commands = {row[4] for row in _NEXT_STEPS.values()} | {_MODEL_RESPONSE[3], _TERMINAL[4]}
    named = {word for command in commands for word in command.split() if word in steps}
    text = _text()
    assert named and all(f"`{step}" in text or f" {step}" in text or f"{step}`" in text for step in named)


def test_the_arguments_are_left_to_help_and_next_step() -> None:
    text = " ".join(_text().split())
    assert "`python -m tools.api <команда> --help`" in text and "`next_step`" in text
