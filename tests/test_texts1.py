"""texts1: the texts of the automation roles tell where the framework imports come from, and the guide tells what a quarantine takes."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.run_report import analyst_questions
from tools.schema_validation import schema_diagnostics
from tools.scope_pause import gap_rows

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["tc-to-autotest", "autotest-reviewer"])
def test_a_role_checks_framework_imports_against_the_build_file_by_reading_only(role: str) -> None:
    text = _text(f"skills/{role}/SKILL.md")
    assert "сборки модуля" in text and "ничего вне проекта" in text and "команд" in text


def test_the_reviewer_makes_an_import_that_differs_from_the_build_file_a_blocking_finding() -> None:
    assert "ошибка компиляции: `BLOCKING`" in _text("skills/autotest-reviewer/SKILL.md")


def test_the_generator_changes_the_code_of_r1_by_corrections_and_blocking_findings_only() -> None:
    assert "`WARNING` and `INFO` are notes for the person" in _text("skills/tc-to-autotest/SKILL.md")


def test_the_guide_says_a_failed_row_of_a_parameterized_test_takes_the_whole_method_into_quarantine() -> None:
    assert "весь метод" in _text("HOW-TO-USE-MODE-B.md").split("@ParameterizedTest")[-1]


def test_the_sample_of_an_automation_answer_passes_the_schema_with_only_the_digests_the_code_puts_in_r1() -> None:

    block = re.search(r"```json\n(.*?)\n```", _text("skills/tc-to-autotest/references/artifact-sample.md"), re.S).group(1)
    answer = json.loads(re.sub(r"<[^>]*>", "x", block))
    artifacts = answer["artifacts"]
    artifacts["source"].update(source_digest="sha256:" + "0" * 64, effective_bundle_receipt_digest="sha256:" + "0" * 64)
    artifacts["source"]["document_id"] = "TCDOC-x"
    for row in artifacts["generated_files"]:
        row["content_digest"] = "sha256:" + "0" * 64
    assert schema_diagnostics(answer, ROOT / "schemas" / "tc-to-autotest-output.schema.json", ROOT) == []


def test_a_free_sentence_of_disagreement_reaches_the_analyst_as_other_and_makes_no_pause() -> None:
    sentence = "SREQ-0003: код отвечает 200 — требование ждёт 201"
    marker = {"warnings": [sentence], "artifacts": {"analytics_documentation": {"requirements": [{"source_requirement_id": "SREQ-0003", "text": "x"}]}}}
    assert gap_rows(marker) == []
    assert "## Прочее\n\n- " + sentence in analyst_questions(marker["warnings"])
