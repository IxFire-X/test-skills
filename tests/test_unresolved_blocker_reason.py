"""An automation blocker in the accepted canonical is named as the reason not to accept.

Wave-2 live step5 (``evals/test-strength/results/2026-10-07/live-step5``) finished
``COMPLETE + PASS`` with ``accepted=false`` and no ``reason_code``: three cases kept an
``UNRESOLVED_ASSERTION`` blocker (``blocker_count=3``), so ``no_unresolved_blocker`` failed
and nothing said so.  Such a local-pilot result now carries ``UNRESOLVED_AUTOMATION_BLOCKER``;
results written before it stay readable.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_exit_policy import _local
from tests.test_review_fixes_driver import SavedModel, _drive, _project, _start
from tools import pilot_state

ROOT = Path(__file__).resolve().parents[1]
LIVE_STEP5_RESULT = ROOT / "evals" / "test-strength" / "results" / "2026-10-07" / "live-step5" / "terminal-result.json.gz"


class BlockedModel(SavedModel):
    """The saved local-pilot answers plus one case whose assertion the requirements leave unresolved."""

    def answer(self, task: dict[str, Any]) -> Any:
        value = super().answer(task)
        stage = task["stage"]
        if stage.startswith("tc-generator:"):
            prefixes = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))["id_prefixes"]
            automatable = next(case for case in value["test_cases"] if not case["steps"][0]["manual_only"])
            blocked = json.loads(json.dumps(automatable))
            step = blocked["steps"][0]
            source = step["operation"]["base_url_source"]["provenance"][0]
            blocked.update(case_id=prefixes["case_id"] + "003", title="Сортировка списка товаров")
            step.update(step_id=prefixes["step_id"] + "003", automation_blockers=[{
                "blocker_id": prefixes["blocker_id"] + "001", "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions",
                "reason": "Требования не задают поле, по которому сортируется список.", "provenance": [source]}])
            step["expectations"][0].update(expectation_id=prefixes["expectation_id"] + "003", text="Список отсортирован.", assertions=[])
            value["test_cases"].append(blocked)
        elif stage.startswith("tc-to-autotest:"):
            document = json.loads(Path(task["inputs"][1]).read_text(encoding="utf-8"))
            blocked = next(case for case in document["test_cases"] if case["steps"][0]["automation_blockers"])
            value["manual_dispositions"].append({"case_id": blocked["case_id"], "step_id": blocked["steps"][0]["step_id"]})
            value["diagnostics"] = [{"path": "/test_cases/2", "code": "AUTOMATION_BLOCKED_CASE", "message": "Кейс заблокирован: проверка не определена."}]
        return value


def test_local_pilot_run_with_a_blocked_case_names_the_blocker(tmp_path: Path) -> None:
    from tools import run_pipeline

    project = _project(tmp_path, local=True)
    done = _drive(project, _start(project, "local-pilot-v1"), BlockedModel("local-pilot-v1"))
    result = done["result"]
    assert (result["completion"], result["verification"], result["coverage"]) == ("COMPLETE", "PASS", "MIXED")
    assert (result["accepted"], result["reason_code"], result["exit_code"]) == (False, "UNRESOLVED_AUTOMATION_BLOCKER", 1)
    run_root = project / ".pilot-runs" / done["run_id"]
    terminal = pilot_state.read_terminal_result(run_root, result["attempt_id"])
    assert terminal["evidence"]["blocker_count"] == 1 and terminal["reason_code"] == "UNRESOLVED_AUTOMATION_BLOCKER"
    assert run_pipeline._durable_status(project, run_root, stage="status")["stop_reason"] == "UNRESOLVED_AUTOMATION_BLOCKER"


def test_blocker_reason_is_derived_only_for_an_otherwise_unexplained_local_pass() -> None:
    blocked = pilot_state.terminal_result(_local(blocker_count=3), "local-pilot-v1")
    assert (blocked["accepted"], blocked["reason_code"]) == (False, "UNRESOLVED_AUTOMATION_BLOCKER")
    assert pilot_state._validate_result_record(blocked) and pilot_state.exit_code(blocked) == 1
    assert pilot_state.terminal_result(_local(blocker_count=3, reason_code="UNRESOLVED_AUTOMATION_BLOCKER"), "local-pilot-v1") == blocked
    # Runs without blockers are unchanged.
    assert "reason_code" not in pilot_state.terminal_result(_local(), "local-pilot-v1")
    assert "reason_code" not in pilot_state.terminal_result(_local(verification="FAIL", exact_target_pass=False), "local-pilot-v1")
    # The frozen complete_fail tuple keeps no reason, and the cases-only draft is never explained by blockers.
    failed = pilot_state.terminal_result(_local(verification="FAIL", exact_target_pass=False, blocker_count=3), "local-pilot-v1")
    assert (failed["accepted"], "reason_code" in failed) == (False, False)
    from tests.test_exit_policy import _facts
    assert "reason_code" not in pilot_state.terminal_result(_facts(blocker_count=2), "cases-only-v1")
    # An earlier stage cause or derived reason keeps precedence.
    self_review = pilot_state.terminal_result(_local(blocker_count=3, review_independence="SELF", self_review_accepted=False), "local-pilot-v1")
    assert self_review["reason_code"] == "REVIEW_NOT_INDEPENDENT"
    # The code is never claimed without the fact it names.
    for changes in ({}, {"blocker_count": 3, "verification": "FAIL", "exact_target_pass": False}):
        with pytest.raises(ValueError):
            pilot_state.terminal_result(_local(reason_code="UNRESOLVED_AUTOMATION_BLOCKER", **changes), "local-pilot-v1")
    with pytest.raises(ValueError):
        pilot_state.terminal_result(_facts(blocker_count=2, reason_code="UNRESOLVED_AUTOMATION_BLOCKER"), "cases-only-v1")
    forged = json.loads(json.dumps(blocked))
    forged["evidence"]["blocker_count"] = 0
    assert not pilot_state._validate_result_record(pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"}))


def test_contract_names_the_reason_of_the_blocker_predicate(pack_root: Path) -> None:
    from tools.contract_check import validate_pipeline_contract
    from tools.render_contract_docs import _rendered_files

    contract = json.loads((pack_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    [row] = contract["acceptance_reason_codes"]
    assert row["predicate"] in contract["acceptance_predicates"]["local-pilot-v1"] and row["reason_code"] == "UNRESOLVED_AUTOMATION_BLOCKER"
    assert all("`UNRESOLVED_AUTOMATION_BLOCKER`" in text for text in _rendered_files(contract).values())
    for change, expected in (
        (lambda c: c["acceptance_reason_codes"][0].__setitem__("reason_code", "BLOCKED"), "acceptance reason codes exact ordered registry mismatch"),
        (lambda c: c["acceptance_predicates"]["local-pilot-v1"].remove("no_unresolved_blocker"), "acceptance reason code names a predicate its profile does not have"),
    ):
        changed = json.loads(json.dumps(contract))
        change(changed)
        assert expected in validate_pipeline_contract(changed, pack_root)["errors"]


def test_terminal_result_written_before_the_reason_code_stays_readable() -> None:
    legacy = json.loads(gzip.decompress(LIVE_STEP5_RESULT.read_bytes()).decode("utf-8"))
    assert (legacy["verification"], legacy["accepted"], legacy["evidence"]["blocker_count"], "reason_code" in legacy) == ("PASS", False, 3, False)
    assert pilot_state._validate_result_record(legacy)
    assert pilot_state.exit_code(legacy) == 1
