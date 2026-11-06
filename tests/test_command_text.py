"""V4b: the command ``materialize`` prints is the one a human pastes into cmd.exe, PowerShell or sh.

Threat model: the paths in it come from the file system (the name of a directory of a cloned project, of the user, of the interpreter), the
selectors from the model.  A ``%``, ``^``, ``!`` or ``"`` in such a name changes what cmd.exe runs, ``$`` and the back quote what sh and
PowerShell run.  One form serves the three: a plain token as it is, a token with a space or a bracket in double quotes, and a token with
any character that quotes cannot hold is not printed - ``COMMAND_NOT_PRINTABLE``, before anything is written into the project."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.run_pipeline import HostStop

PLAIN = ["mvnw", "-Dtest=pkg.A#m,pkg.B#n", "-Pdefault", "tests/test_a.py::TestA::test_a", "./mvnw", "a_b-c.d:e+f", "junit_suite_name=PILOT-0123abcd",
         "-Dfoo.bar=baz", "-Dpilot.run=0123abcd", "-Dcheckstyle.skip=true"]
QUOTED = [
    ("a b", '"a b"'),
    ("C:\\Program Files (x86)\\Maven\\bin\\mvn.cmd", '"C:\\Program Files (x86)\\Maven\\bin\\mvn.cmd"'),
    ("C:\\Users\\Иван Петров\\проект", '"C:\\Users\\Иван Петров\\проект"'),
    ("x@y", '"x@y"'),
    ("[1-b]", '"[1-b]"'),
    ("{x}~", '"{x}~"'),
    ("", '""'),
]
REFUSED = ["a%b", "%PATH%", "a^b", 'a"b', "a!b", "$HOME", "a`b", "a&b", "a|b", "a<b", "a>b", "a;b", "a'b", "a*b", "a?b", "a\nb", "a\rb", "a\x00b",
           "C:\\dir\\", "\\\\server\\share", "a\\\\b"]


@pytest.mark.parametrize("token", PLAIN)
def test_a_plain_token_is_printed_as_it_is(token: str) -> None:
    from tools.exec_commands import command_token

    assert command_token(token) == token


@pytest.mark.parametrize(("token", "printed"), QUOTED)
def test_a_token_with_a_space_a_bracket_or_a_backslash_is_in_double_quotes(token: str, printed: str) -> None:
    from tools.exec_commands import command_token

    assert command_token(token) == printed


@pytest.mark.parametrize("token", REFUSED)
def test_a_token_that_quotes_cannot_hold_is_not_printed_and_the_fix_names_the_character(token: str) -> None:
    from tools.exec_commands import command_token

    with pytest.raises(HostStop) as stopped:
        command_token(token)
    assert stopped.value.code == "COMMAND_NOT_PRINTABLE" and stopped.value.fix and "переимен" in stopped.value.fix


def test_the_text_of_a_command_joins_its_tokens() -> None:
    from tools.exec_commands import command_text

    assert command_text(["mvnw", "-B", "C:\\Program Files (x86)\\x"]) == 'mvnw -B "C:\\Program Files (x86)\\x"'
    with pytest.raises(HostStop):
        command_text(["mvnw", "%X%"])


def test_the_printed_text_reaches_the_program_as_the_tokens_through_the_shell_of_the_machine(tmp_path: Path) -> None:
    """One short run through cmd.exe (Windows) or sh: the program gets exactly the tokens.  The test starts the process, not the package."""
    from tools.exec_commands import command_text

    script = tmp_path / "argv.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    tokens = [*PLAIN, *(token for token, _printed in QUOTED)]
    done = subprocess.run(command_text([sys.executable, str(script), *tokens]), shell=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout) == tokens


def test_materialize_in_a_project_whose_path_cannot_be_printed_refuses_before_it_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exec_prepares import _has, _project
    from tests.test_p4_ingest import _args, _out
    from tools import exec_commands

    project, run_root, attempt_id = _project(tmp_path / "100%done")
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file())
    code = exec_commands.cmd_materialize(_args(project, run_root))
    output = _out(capsys)
    assert code == 2 and output["reason"] == "COMMAND_NOT_PRINTABLE" and "%" in output["fix"], output
    assert sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()) == before
    assert not _has(run_root, attempt_id, "materialization-command")


def test_the_next_line_of_materialize_quotes_the_project_like_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exec_prepares import _project
    from tests.test_p4_ingest import _args, _out
    from tools import exec_commands

    project, run_root, _attempt_id = _project(tmp_path / "Иван Петров")
    code = exec_commands.cmd_materialize(_args(project, run_root))
    output = _out(capsys)
    assert code == 0, output
    assert f"--project {exec_commands.command_token(str(project))}" in output["next"] and output["next"].startswith(exec_commands.command_token(sys.executable))
    assert (output["command_text"], output["command_text_powershell"]) == exec_commands.printed_forms(project, output["working_directory"], output["command"])
    assert output["command_text_powershell"].startswith('& "') and output["next_powershell"].startswith('& "') and not output["next"].startswith("&")
    assert f'"--project" {exec_commands.command_text_powershell([str(project)])[2:]}' in output["next_powershell"]


def test_the_powershell_form_starts_with_the_call_operator_and_quotes_every_token() -> None:
    from tools.exec_commands import command_text_powershell

    assert command_text_powershell(["C:\\Program Files (x86)\\Maven\\mvn.cmd", "-Dfoo.bar=baz", "-Dtest=A#m,B", "x"]) == (
        '& "C:\\Program Files (x86)\\Maven\\mvn.cmd" "-Dfoo.bar=baz" "-Dtest=A#m,B" "x"')
    for token in REFUSED:
        with pytest.raises(HostStop):
            command_text_powershell(["mvnw", token])


@pytest.mark.skipif(shutil.which("powershell") is None and shutil.which("pwsh") is None, reason="no PowerShell here")
def test_the_powershell_form_reaches_the_program_as_the_tokens_through_powershell(tmp_path: Path) -> None:
    """One short run through PowerShell (-EncodedCommand carries the text unchanged).  An empty token is left out: PowerShell before 7.3 drops it."""
    from tools.exec_commands import command_text_powershell

    script = tmp_path / "argv.py"
    script.write_text("import json, sys" + chr(10) + "print(json.dumps(sys.argv[1:]))" + chr(10), encoding="utf-8")
    tokens = [*PLAIN, *(token for token, _printed in QUOTED if token)]
    text = command_text_powershell([sys.executable, str(script), *tokens])
    encoded = base64.b64encode(text.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([shutil.which("powershell") or str(shutil.which("pwsh")), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout) == tokens


# --- the wrapper of the build is an absolute path in the printed forms (MB2 №11) ----------------------------------------

def _wrapper_in(module: Path) -> tuple[str, list[str]]:
    """A module directory with a wrapper that only prints its arguments, and the argv ``materialize`` keeps for it."""
    if sys.platform == "win32":
        (module / "mvnw.cmd").write_text("@echo off\r\necho ARGS %*\r\n", encoding="utf-8")
        return "mvnw.cmd", ["mvnw.cmd", "-B", "test"]
    (module / "mvnw").write_text('#!/bin/sh\necho ARGS "$@"\n', encoding="utf-8")
    (module / "mvnw").chmod(0o755)
    return "mvnw", ["mvnw", "-B", "test"]


def test_the_printed_forms_name_the_wrapper_by_its_absolute_path_and_the_array_keeps_it_relative(tmp_path: Path) -> None:
    from tools.exec_commands import printed_forms

    module = tmp_path / "svc"
    module.mkdir()
    name, command = _wrapper_in(module)
    shown, powershell = printed_forms(tmp_path, "svc", command)
    absolute = str(module / name)
    assert shown.startswith(f'"{absolute}"' if "\\" in absolute or " " in absolute else absolute) and powershell.startswith(f'& "{absolute}"')
    assert command[0] == name


@pytest.mark.skipif(shutil.which("powershell") is None, reason="no PowerShell here")
def test_the_powershell_form_starts_the_wrapper_of_the_module_from_its_directory(tmp_path: Path) -> None:
    from tools.exec_commands import printed_forms

    _name, command = _wrapper_in(tmp_path)
    done = subprocess.run([shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", printed_forms(tmp_path.parent, tmp_path.name, command)[1]],
                          cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0 and "ARGS -B test" in done.stdout, (done.stdout, done.stderr)


def test_the_plain_form_starts_the_wrapper_of_the_module_where_the_current_directory_is_not_searched(tmp_path: Path) -> None:
    """cmd.exe with NoDefaultCurrentDirectoryInExePath, or sh: a bare name is not found in the current directory."""
    from tools.exec_commands import printed_forms

    _name, command = _wrapper_in(tmp_path)
    env = {**os.environ, "NoDefaultCurrentDirectoryInExePath": "1"}
    done = subprocess.run(printed_forms(tmp_path.parent, tmp_path.name, command)[0], shell=True, cwd=tmp_path, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 0 and "ARGS -B test" in done.stdout, (done.stdout, done.stderr)
