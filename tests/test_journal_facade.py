"""The facade of pilot_state: a miss takes a loaded journal of mode B from sys.modules and asks no finder (Р2 fix); a patch through
it reaches every module that took the code of the journal (В1)."""
from __future__ import annotations

import pytest

from tools import execution_gate, pilot_state


def test_a_miss_of_the_facade_with_the_journal_loaded_asks_no_finder(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import execution_journal

    asked: list[int] = []
    monkeypatch.setattr(execution_gate, "mode_b_available", lambda: asked.append(1) or True)
    assert pilot_state._check_execution_receipt is execution_journal._check_execution_receipt
    assert asked == []


def test_a_patch_through_pilot_state_reaches_every_module_of_the_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import model_inputs, model_steps, scenario_observations

    spy = object()
    monkeypatch.setattr(pilot_state, "read_run", spy)
    assert model_inputs.read_run is model_steps.read_run is scenario_observations.read_run is spy
