"""q7: a refusal the tester may be shown carries one phrase for him, ``human``, beside the agent's ``fix`` — what happened, one action."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import run_pipeline
from tools.fixes import HUMAN, OPENSPEC_REASONS, human_for
from tools.ready import _question_text

# words of the package's inside: the tester does not know them
INTERNAL = ("батч", "пачк", "квитанц", "digest", "канон", "стади", "журнал", "слот", "p7", "sreq", "creq", "описи")


TEXTS = sorted({*HUMAN.values(), *(human for _, _, human in OPENSPEC_REASONS if human)})  # of a code, and of a cause of OPENSPEC_SOURCE_CONFLICT


@pytest.mark.parametrize("text", TEXTS)
def test_a_phrase_for_the_tester_has_no_internal_word(text: str) -> None:
    assert not [word for word in INTERNAL if word in text.lower()], text


@pytest.mark.parametrize("text", TEXTS)
def test_a_phrase_for_the_tester_is_short_and_has_no_command_flag_or_code(text: str) -> None:
    assert len(text) <= 240 and not any(mark in text for mark in ("`", "--", "/")), text
    assert re.search(r"\b[A-Z]{2,}_[A-Z_]+\b", text) is None, text


def test_every_code_of_the_phrases_is_a_code_of_the_package() -> None:
    tools = Path(__file__).resolve().parents[1] / "tools"
    others = "\n".join(path.read_text(encoding="utf-8") for path in tools.glob("*.py") if path.name != "fixes.py")
    assert [code for code in HUMAN if code not in others] == []


def _api(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception) -> dict:
    from tools import api, stage_steps

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(stage_steps, "prepare", refuse)
    assert api.main(["prepare", "--run-root", "x", "--stage", "tc-generator:b1"]) == 2
    return json.loads(capsys.readouterr().out)


def test_an_api_refusal_of_a_listed_code_carries_the_phrase_beside_code_and_fix(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    output = _api(monkeypatch, capsys, ValueError("MODEL_STAGE_ARTIFACT_INVALID: /status: лишнее поле; fix: убери поле"))
    assert output["human"] == human_for("MODEL_STAGE_ARTIFACT_INVALID") and output["fix"] == "убери поле"
    assert output["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and output["message"].startswith("MODEL_STAGE_ARTIFACT_INVALID: /status")


def test_a_refusal_of_a_code_without_a_phrase_carries_no_human_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert "human" not in _api(monkeypatch, capsys, ValueError("UNLISTED_REFUSAL: text; fix: do"))


def test_a_run_pipeline_refusal_of_a_listed_code_carries_the_phrase(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(_args):
        raise run_pipeline.HostStop("SCAN_RUN_IN_PROGRESS", "busy", "продолжите его")

    monkeypatch.setattr(run_pipeline, "cmd_status", refuse)
    assert run_pipeline.main(["status", "--project", ".", "--run", "a" * 32]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "SCAN_RUN_IN_PROGRESS" and output["fix"] == "продолжите его" and output["human"] == human_for("SCAN_RUN_IN_PROGRESS")


def test_ready_names_the_test_question_of_the_skillsrc_in_words_not_as_a_dump() -> None:
    question = {"id": "module:gson:test.framework", "field": "modules.gson.test.framework", "options": [{"id": "junit5", "value": "junit5"}]}
    text = _question_text([question])
    assert "модуля gson" in text and "junit5" in text and "{" not in text and "'" not in text


def test_ready_names_the_source_question_of_the_skillsrc_in_words_too() -> None:
    text = _question_text([{"id": "module:examples:paths.source", "field": "modules.examples.paths.source", "options": []}])
    assert "модуля examples" in text and "исходный код" in text and "paths" not in text
