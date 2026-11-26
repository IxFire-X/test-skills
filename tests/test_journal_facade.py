"""The facade of pilot_state: a miss takes a loaded journal of mode B from sys.modules and asks no finder (Р2 fix); a patch through
it reaches every module that took the code of the journal (В1), the files of a run (Р4), the journal of events (Р5), the receipts and the
ledger of the reviewer (Р6) below it too;
every such module imports first in a fresh interpreter: none takes a moved name through the facade at its top (В2)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools import execution_gate, pilot_state

ROOT = Path(__file__).resolve().parents[1]


def test_a_miss_of_the_facade_with_the_journal_loaded_asks_no_finder(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import execution_journal

    asked: list[int] = []
    monkeypatch.setattr(execution_gate, "mode_b_available", lambda: asked.append(1) or True)
    assert pilot_state._check_execution_receipt is execution_journal._check_execution_receipt
    assert asked == []


def test_a_patch_through_pilot_state_reaches_every_module_of_the_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import effective_canonical, model_inputs, model_records, model_steps, run_closure, scenario_observations

    spy = object()
    monkeypatch.setattr(pilot_state, "derive_state", spy)
    assert model_records.derive_state is effective_canonical.derive_state is run_closure.derive_state is spy
    assert model_inputs.derive_state is model_steps.derive_state is scenario_observations.derive_state is spy



def test_a_patch_of_input_and_output_through_pilot_state_reaches_the_files_of_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_store

    spy = object()
    monkeypatch.setattr(pilot_state, "read_confined_bytes", spy)
    assert run_store.read_confined_bytes is spy


def test_a_patch_through_pilot_state_reaches_the_journal_of_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_journal

    spy = object()
    monkeypatch.setattr(pilot_state, "_derive_state", spy)
    assert run_journal._derive_state is spy


def test_a_patch_through_pilot_state_reaches_the_receipts_and_the_ledger_of_the_reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_ledger, run_receipts

    spy = object()
    monkeypatch.setattr(pilot_state, "bind_bytes", spy)
    assert run_receipts.bind_bytes is review_ledger.bind_bytes is spy


@pytest.mark.parametrize("module", ["run_store", "run_journal", "run_receipts", "review_ledger", "model_records", "effective_canonical", "run_closure", "model_inputs", "model_steps",
                                    "scenario_observations", "execution_journal"])
def test_a_module_of_the_journal_imports_first_in_a_fresh_interpreter(module: str) -> None:
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", f"import tools.{module}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
