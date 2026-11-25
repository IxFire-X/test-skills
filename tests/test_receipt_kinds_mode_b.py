"""Р2, Р3: a receipt of a kind of mode B in a delivery without the module of mode B is refused with MODE_B_NOT_IN_PACKAGE."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools import execution_gate, pilot_state


@pytest.mark.parametrize("kind", ["automation-review-boundary-r1", "materialization-command", "resume-validation"])
def test_a_kind_of_mode_b_without_its_module_is_mode_b_not_in_package(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "tools.execution_journal", None)  # as a delivery of cases
    attempt = {"run_id": "r", "attempt_id": "a", "policy_profile": "cases-only-v1"}
    with pytest.raises(execution_gate.ModeBMissing) as refused:
        pilot_state._validate_factual_receipt(Path("."), Path("."), attempt, kind, {"kind": kind}, {"events": [], "attempts": []})
    assert refused.value.code == "MODE_B_NOT_IN_PACKAGE"


def test_an_execution_receipt_without_its_module_is_mode_b_not_in_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_execution_start_gate import _attempt

    run_root, attempt = _attempt(tmp_path, "cases-only-v1", False)
    monkeypatch.setitem(sys.modules, "tools.execution_journal", None)  # as a delivery of cases
    with pytest.raises(execution_gate.ModeBMissing) as refused:
        pilot_state.publish_attempt_receipt(run_root, attempt["attempt_id"], "execution-receipt", {"payload": {}})
    assert refused.value.code == "MODE_B_NOT_IN_PACKAGE"
