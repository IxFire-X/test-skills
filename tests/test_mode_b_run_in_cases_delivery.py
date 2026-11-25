"""Р2 fix: a run of mode B (with receipts of mode B) opened by a delivery of cases — status, report and finish refuse with
MODE_B_NOT_IN_PACKAGE and its fix at once, before any receipt of mode B is read; no command falls with an exception."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from tools import execution_gate, run_pipeline


@pytest.mark.parametrize("command", ["status", "report", "finish"])
def test_a_run_of_mode_b_in_a_delivery_of_cases_is_refused_with_its_code(command: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_execution_receipt import _execution_facts

    project = tmp_path / "project"
    run_root, *_rest = _execution_facts(tmp_path, project=project)
    monkeypatch.setitem(sys.modules, "tools.execution_journal", None)  # as a delivery of cases
    loaded: list[str] = []
    monkeypatch.setattr(execution_gate, "load_mode_b", lambda name: loaded.append(name) or pytest.fail(f"load_mode_b({name})"))
    out = io.StringIO()
    with redirect_stdout(out):
        code = run_pipeline.main([command, "--project", str(project), "--run", run_root.name])
    answer = json.loads(out.getvalue())
    assert (code, answer["code"]) == (2, "MODE_B_NOT_IN_PACKAGE"), answer
    assert answer.get("fix") and loaded == [], (answer, loaded)
