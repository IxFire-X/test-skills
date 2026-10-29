"""FaT-RQ: a model request of the automation chain must be the next stage of the chain (automation_next), before any write.

Found in run-two-repos: tc-to-autotest:r2 was requested before r1 and the journal took it — an orphan request.  The check
is made at publication only, so an old journal with such an orphan still reads.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _selected(tmp_path: Path) -> tuple[Path, str, list[str]]:
    from tests.test_self_review import _reviewed
    from tools.pilot_state import read_effective_canonical

    run_root, attempt_id, _document, _selected = _reviewed(tmp_path, "cases-only-v1", flag=False, fresh_context=True)
    effective = read_effective_canonical(run_root, attempt_id)
    return run_root, attempt_id, [effective["document_digest"], effective["effective_bundle_receipt_digest"]]


def _request(run_root: Path, attempt_id: str, stage: str, inputs: list[str]):
    from tools.pilot_state import publish_model_request

    return publish_model_request(run_root, attempt_id, stage, model_id="model-automation", invocation_id="automation-1", input_digests=inputs)


def _requests(run_root: Path) -> list[str]:
    from tools.pilot_state import derive_state

    return [event["stage_instance_id"] for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_REQUESTED"
            and str(event.get("stage_instance_id")).startswith("tc-to-autotest:")]


def test_r2_before_r1_is_refused_with_the_right_stage_and_nothing_is_written(tmp_path: Path) -> None:
    from tools.pilot_state import ModelRequestOutOfOrder

    run_root, attempt_id, inputs = _selected(tmp_path)
    with pytest.raises(ModelRequestOutOfOrder) as refused:
        _request(run_root, attempt_id, "tc-to-autotest:r2", inputs)
    assert refused.value.code == "MODEL_REQUEST_OUT_OF_ORDER"
    assert "tc-to-autotest:r1" in str(refused.value) and "--stage tc-to-autotest:r1" in str(refused.value)
    assert _requests(run_root) == []
    _request(run_root, attempt_id, "tc-to-autotest:r1", inputs)
    assert _requests(run_root) == ["tc-to-autotest:r1"]


def test_an_old_journal_with_an_orphan_request_still_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    run_root, attempt_id, inputs = _selected(tmp_path)
    monkeypatch.setattr(pilot_state, "_require_next_automation_stage", lambda *args: None)  # a journal written before FaT-RQ
    orphan = _request(run_root, attempt_id, "tc-to-autotest:r2", inputs)
    monkeypatch.undo()
    assert pilot_state.read_model_request(run_root, attempt_id, "tc-to-autotest:r2", orphan["digest"])["stage_instance_id"] == "tc-to-autotest:r2"
    assert _requests(run_root) == ["tc-to-autotest:r2"]
