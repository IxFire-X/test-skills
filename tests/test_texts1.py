"""texts1: the texts of the automation roles tell where the framework imports come from, and the guide tells what a quarantine takes."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["tc-to-autotest", "autotest-reviewer"])
def test_a_role_checks_framework_imports_against_the_build_file_by_reading_only(role: str) -> None:
    text = _text(f"skills/{role}/SKILL.md")
    assert "сборки модуля" in text and "ничего вне проекта" in text and "команд" in text


def test_the_reviewer_makes_an_import_that_differs_from_the_build_file_a_blocking_finding() -> None:
    assert "ошибка компиляции: `BLOCKING`" in _text("skills/autotest-reviewer/SKILL.md")


def test_the_guide_says_a_failed_row_of_a_parameterized_test_takes_the_whole_method_into_quarantine() -> None:
    assert "весь метод" in _text("HOW-TO-USE-MODE-B.md").split("@ParameterizedTest")[-1]
