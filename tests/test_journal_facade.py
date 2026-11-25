"""Р2 fix: the facade of pilot_state takes a loaded journal of mode B from sys.modules — a miss asks no finder."""
from __future__ import annotations

import pytest

from tools import execution_gate, pilot_state


def test_a_miss_of_the_facade_with_the_journal_loaded_asks_no_finder(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import execution_journal

    asked: list[int] = []
    monkeypatch.setattr(execution_gate, "mode_b_available", lambda: asked.append(1) or True)
    assert pilot_state._check_execution_receipt is execution_journal._check_execution_receipt
    assert asked == []
