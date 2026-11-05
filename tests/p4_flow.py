"""P4b: the one way the tests that used to call ``exec`` get a result — the flow of a human (``materialize`` → the printed command → ``ingest``).

``exec`` started the build itself; the package starts nothing now.  A test that needs the result of a real run of the generated tests does
what the person does: ``materialize`` prints the command, the command is run as printed (a real pytest of the fixture project — the test
may start a process, the package may not), ``ingest`` takes the reports.  A test that needs a fixture report instead of a run writes it
(``junit``) and calls ``ingest`` itself.  The helpers are those of ``tests/test_p4_ingest.py``, under public names.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_p4_ingest import _human_run as human_run  # noqa: F401
from tests.test_p4_ingest import _ingest as ingest  # noqa: F401
from tests.test_p4_ingest import _junit as junit  # noqa: F401
from tests.test_p4_ingest import _materialize as materialize  # noqa: F401
from tests.test_p4_ingest import _word as word  # noqa: F401
from tests.test_p4_ingest import _write as write_report  # noqa: F401


def flow_args(project: Path, run_root: Path, **extra) -> SimpleNamespace:
    """The arguments of ``materialize`` and ``ingest`` for the module of the fixture project (the ones ``exec`` took are not needed)."""
    values = dict(project=str(project), run=run_root.name, module="python-pytest", language=None, target=None,
                  reports=None, no_reports=False, reason=None, restore=False)
    values.update(extra)
    return SimpleNamespace(**values)


def run_the_build(project: Path, run_root: Path, capsys: pytest.CaptureFixture[str], **extra) -> tuple[int, dict]:
    """``materialize`` → the printed command, run as printed → ``ingest``: what ``exec`` did in one command; ``(exit code, ingest answer)``."""
    from tools import exec_commands

    capsys.readouterr()
    code = exec_commands.cmd_materialize(flow_args(project, run_root, **extra))
    printed = _json_of(capsys)
    assert code == 0 and printed["stage"] == "materialize", printed
    completed = human_run(project, printed)
    assert completed.returncode in (0, 1), completed.stdout + completed.stderr  # 1: the tests ran and some failed
    code = exec_commands.cmd_ingest(flow_args(project, run_root, **extra))
    return code, _json_of(capsys)


def _json_of(capsys: pytest.CaptureFixture[str]) -> dict:
    import json

    return json.loads(capsys.readouterr().out)
