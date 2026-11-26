"""fix5 (v0.6): a changed file of the context after the scan is a refusal with its code and the way out, before anything is written; ``status`` after a cut submit points to the repeat of it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import pilot_state, stage_steps, run_pipeline
from tools.json_cli import error_envelope
from tests.test_request_order import _selected
from tests.test_review_requested import _generated_run
from tests.test_fix2 import _cut_rework


def _refused(root: Path, stage: str, **flags) -> dict:
    """What ``prepare`` of ``stage`` answers with the files of the context changed: its envelope; the journal is the same bytes."""
    before = (root / "events.jsonl").read_bytes()
    with pytest.raises(Exception) as refused:  # noqa: B017 - whatever the command refuses with: its envelope is the point
        stage_steps.prepare(root, stage, **flags)
    assert (root / "events.jsonl").read_bytes() == before
    return error_envelope(refused.value)


def _says_drift(envelope: dict) -> None:
    assert envelope["code"] == "CONTEXT_DRIFTED" and "scan --new-run" in str(envelope["fix"]), envelope


def test_the_request_of_the_automation_is_not_published_when_a_file_of_the_context_changed(tmp_path: Path) -> None:
    root, _attempt, _inputs = _selected(tmp_path)
    skillsrc = root.parent.parent / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8") + "\n# changed after the scan\n", encoding="utf-8")
    _says_drift(_refused(root, "tc-to-autotest:r1"))


def _generated(tmp_path: Path, **kwargs) -> dict:
    run = _generated_run(tmp_path, **kwargs)
    (run["project"] / "src" / "evidence.py").write_text("value = 2\n", encoding="utf-8")
    return run


def test_the_request_of_a_batch_names_the_drift_of_the_context(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)
    root = Path(run["run_root"])
    for kind in ("header", "plan"):
        pilot_state.publish_generator_input(root, run["attempt_id"], kind, json.loads(Path(run[kind]).read_bytes()))
    stage = "tc-generator:" + json.loads(Path(run["plan"]).read_bytes())["batches"][0]["batch_id"]
    (run["project"] / "src" / "evidence.py").write_text("value = 2\n", encoding="utf-8")
    _says_drift(_refused(root, stage))


def test_the_request_of_the_review_is_not_published_when_a_file_of_the_context_changed(tmp_path: Path) -> None:
    run = _generated(tmp_path)
    _says_drift(_refused(Path(run["run_root"]), "tc-reviewer:canonical", invocation_id="reviewer-subagent-1", fresh_context=True))


def test_status_after_a_submit_cut_after_the_rework_file_points_to_the_repeat_of_the_submit(tmp_path: Path, monkeypatch, capsys) -> None:
    run, _answer = _cut_rework(tmp_path, monkeypatch)
    capsys.readouterr()
    run_pipeline.main(["status", "--project", str(run["project"]), "--run", Path(run["run_root"]).name])
    status = json.loads(capsys.readouterr().out)
    assert status["next"] == {"verb": "submit", "stage": "tc-reviewer:canonical"} and not status.get("evidence_errors"), {key: status.get(key) for key in ("next", "evidence_errors")}
