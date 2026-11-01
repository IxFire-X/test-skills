"""Audit D5: next_step of status names the code of the state and the command of the step — checked against the real parsers
of the tools (their ``--help``), never a list kept in the test.  The command is a hint: status runs nothing."""
from __future__ import annotations

import contextlib
import importlib
import io
import re

import pytest

from tools.run_pipeline import _MODEL_RESPONSE, _NEXT_STEPS, _TERMINAL, _next_step


def _commands() -> list[str]:
    return sorted({row[4] for row in _NEXT_STEPS.values()} | {_MODEL_RESPONSE[3], _TERMINAL[4]})


@pytest.mark.parametrize("command", _commands())
def test_every_command_is_a_real_subcommand_with_real_flags(command: str) -> None:
    words = command.split()
    assert words[:2] == ["python", "-m"], command
    module = importlib.import_module(words[2])
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stopped:
        module.main([words[3], "--help"])
    assert stopped.value.code == 0, (command, out.getvalue())
    for flag in (word for word in words[4:] if word.startswith("--")):
        assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", out.getvalue()), (command, flag)


def test_a_wrong_subcommand_is_refused_by_the_same_check() -> None:
    from tools import api

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stopped:
        api.main(["no-such-command", "--help"])
    assert stopped.value.code != 0


def test_every_step_has_a_code_and_a_command_and_the_terminal_is_section_6() -> None:
    from tests.test_fat3 import _next_artifacts

    for expected in [*sorted(_next_artifacts()), "tc-generator:BATCH-1 response", None]:
        step = _next_step({"next_expected_artifact": expected})
        assert step["code"] != "UNKNOWN" and step["command"].startswith("python -m tools."), (expected, step)
    terminal = _next_step({"next_expected_artifact": None})
    assert terminal["code"] == "TERMINAL" and terminal["skill_section"] == "6", terminal
    response = _next_step({"next_expected_artifact": "tc-generator:BATCH-1 response"})
    assert response["stage"] == "tc-generator:BATCH-1" and "--stage tc-generator:BATCH-1" in response["command"], response


def test_the_context_selection_step_names_its_command() -> None:
    step = _next_step({"next_expected_artifact": "authorized context"})
    assert step["code"] == "CONTEXT_SELECTION" and step["command"].startswith("python -m tools.run_pipeline scan"), step


def test_an_exact_value_never_falls_into_the_model_response_row() -> None:
    """Before D5 a substring search sent «context-marker response» to the right row only by the order of the rows."""
    assert _next_step({"next_expected_artifact": "context-marker response"})["code"] == "MARKER"
    assert _next_step({"next_expected_artifact": "context-marker:baseline response"})["code"] == "MODEL_RESPONSE"
