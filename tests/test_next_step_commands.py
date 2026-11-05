"""Audit D5: next_step of status names the code of the state and the command of the step — checked against the real parsers
of the tools (their ``--help``), never a list kept in the test.  The command is a hint: status runs nothing."""
from __future__ import annotations

import contextlib
import importlib
import io
import re

import pytest

from tools.run_pipeline import _MODEL_RESPONSE, _NEXT_STEPS, _TERMINAL, _next_step

pytestmark = pytest.mark.contract


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


def _check_with_parser(command: str) -> None:
    """The full command of a step: its subcommand and every flag in the real --help of its tool."""
    import shlex

    words = shlex.split(command, posix=False)
    assert words[1:3] == ["-m", "tools.run_pipeline"], command
    module = importlib.import_module(words[2])
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stopped:
        module.main([words[3], "--help"])
    assert stopped.value.code == 0, command
    for flag in (word for word in words[4:] if word.startswith("--")):
        assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", out.getvalue()), (command, flag)


def test_on_drift_the_next_step_is_the_new_scan_of_drift_command(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hunt 4, 4.7 (FaT-D5b): the new scan of next_step keeps --docs, --profile and the other flags of the request."""
    from tests.test_scan_guard import DOCS, _python_project, _scan, _status, _write

    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch, accept_self_review=True, disposition_policy="cleanup")
    assert code == 0, scan
    _write(project, "services/api/src/app.py", "X = 2\n")
    status = _status(project, scan["run_id"], monkeypatch)
    step = status["next_step"]
    assert step["code"] == "DRIFT" and step["command"] == status["drift"]["command"], (step, status["drift"])
    assert f'--docs "{DOCS}"' in step["command"] and "--new-run" in step["command"]
    _check_with_parser(step["command"])


def test_without_a_scan_request_the_new_scan_stays_a_template_and_names_drift_command() -> None:
    step = _next_step({"next_expected_artifact": "validated inputs or new scan"})
    assert step["command"].startswith("python -m tools.run_pipeline scan") and "drift.command" in step["action"], step
