"""FaT-HARD: no silent narrowing — a new generator request without the published plan/header is refused (audit 1, 5g), and
a failed drift check of status is printed, not swallowed (F2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import pilot_state


@pytest.mark.parametrize("given", [True, False], ids=["with --input-digest", "without --input-digest"])
def test_a_new_run_without_publish_generator_input_is_refused_at_model_request(tmp_path: Path, given: bool) -> None:
    """Audit 1, 5g: the coverage-only check of model-response is reachable only for a request from before FaT-DG — a new
    generator request without the published plan and header is refused, with the fix, before anything is written."""
    from tests.test_step_inputs import _generator_run

    root, attempt_id, _baseline, receipts, marker, plan, header, _plan_path, _header_path = _generator_run(tmp_path)
    inputs = [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]] if given else []
    before = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
    with pytest.raises(pilot_state.ModelInputsMismatch, match="MODEL_INPUTS_MISMATCH.*fix: tools.api publish-generator-input"):
        pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="g", invocation_id="gen", input_digests=inputs)
    assert sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file()) == before


def test_a_failed_drift_check_is_printed_with_its_code_and_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_scan_guard import _python_project, _scan, _status
    from tools import run_pipeline

    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, scan

    def broken(*_args, **_kwargs):
        raise ValueError("INVENTORY_INVALID: the baseline cannot be rebuilt")

    monkeypatch.setattr(run_pipeline, "_baseline_check", broken)
    status = _status(project, scan["run_id"], monkeypatch)
    assert status["status"] == "ok" and "drift" not in status, status
    error = status["drift_check_error"]
    assert error["code"] == "INVENTORY_INVALID" and "fix" in error and "status" in error["fix"], error
