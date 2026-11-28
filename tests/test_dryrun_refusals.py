"""Campaign v0.8 C1, finding 5: a refusal of ``submit --dry-run`` is a line of ``dry-run-refusals.jsonl`` — no event, no line of
refusals.jsonl — and report.json counts it apart (``refusals.dry_run``); a run with no such refusal keeps its report."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_run_lock import _run
from tools import api, pilot_state
from tools.json_cli import record_refusal
from tools.run_report import refusals


def _events(root: Path) -> list[str]:
    return [event["event_type"] for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "COMMAND_REFUSED"]


def test_a_dry_run_refusal_is_a_line_without_an_event(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "BATCH_SEMANTIC_CONFLICT", "tc-generator:B1", codes=["SEMANTIC_PARAM_GROUP_MISMATCH"], dry_run=True)
    line = json.loads((root / "dry-run-refusals.jsonl").read_bytes())
    assert line["codes"] == ["SEMANTIC_PARAM_GROUP_MISMATCH"] and _events(root) == [] and not (root / "refusals.jsonl").exists()


def test_a_dry_run_line_spends_no_retry(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "MODEL_STAGE_ARTIFACT_INVALID", "plan", spent=True, dry_run=True)
    assert "spent" not in json.loads((root / "dry-run-refusals.jsonl").read_bytes())


def test_submit_dry_run_writes_its_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tools.model_records import ModelStageArtifactInvalid

    def refuse(_args):
        raise ModelStageArtifactInvalid("invalid model stage artifact: x", row_codes=["SCHEMA_REQUIRED"])

    root = _run(tmp_path)
    monkeypatch.setitem(api.COMMANDS, "submit", (refuse, *api.COMMANDS["submit"][1:]))
    assert api.main(["submit", "--run-root", str(root), "--stage", "plan", "--dry-run"]) == 2
    capsys.readouterr()
    assert json.loads((root / "dry-run-refusals.jsonl").read_bytes())["codes"] == ["SCHEMA_REQUIRED"] and _events(root) == []


def test_the_report_counts_the_dry_run_refusals_apart(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "BATCH_SEMANTIC_CONFLICT", codes=["SEMANTIC_TYPE_MISMATCH"], dry_run=True)
    record_refusal(root, "submit", "BATCH_SEMANTIC_CONFLICT", codes=["SEMANTIC_TYPE_MISMATCH", "SEMANTIC_PARAM_GROUP_MISMATCH"], dry_run=True)
    summary = refusals(root)
    assert summary["total"] == 0 and summary["mismatch"] is None
    assert summary["dry_run"] == {"total": 2, "by_code": {"BATCH_SEMANTIC_CONFLICT": 2},
                                  "row_codes": {"SEMANTIC_PARAM_GROUP_MISMATCH": 1, "SEMANTIC_TYPE_MISMATCH": 2}}


def test_a_report_without_dry_run_refusals_has_no_such_key(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "INVALID_ARGUMENT")
    assert "dry_run" not in refusals(root)
