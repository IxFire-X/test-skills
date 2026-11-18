"""q11: the requirements of the marker of an OpenSpec run are the composition of the files, and code answers the step; a part takes the requirements from code."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_openspec_selection import _entries, _project, _scan
from tests.test_task_docs_context import _write
from tools import api, pilot_state
from tools.build_context import build_context
from tools.context_marker_merge import with_requirements
from tools.context_marker_parts import plan

BASE, ADD = "openspec/specs/owners/spec.md", "openspec/changes/add-search/specs/owners/spec.md"
DOCS = [BASE, "openspec/changes/add-search"]


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str] = DOCS) -> tuple[Path, Path, str]:
    project = _project(tmp_path)
    _write(project, "docs/feature.md", "REQ-1. The page shows owners.\n")
    scan = _scan(project, monkeypatch, docs)
    return project, Path(scan["run_root"]), scan["attempt_id"]


def _prepare(root: Path, stage: str, capsys: pytest.CaptureFixture[str]) -> dict:
    assert api.main(["prepare", "--run-root", str(root), "--stage", stage]) == 0
    return json.loads(capsys.readouterr().out)["result"]


def _answer_of(root: Path, attempt_id: str) -> dict:
    answered = [event for event in pilot_state.derive_state(root)["events"] if event.get("stage_instance_id") == "context-marker:requirements" and event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    return pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answered[-1]["artifact_digest"])["artifact"]


def _model_of(root: Path, attempt_id: str, stage: str) -> str:
    asked = [event for event in pilot_state.derive_state(root)["events"] if event.get("stage_instance_id") == stage and event["event_type"] == "MODEL_REQUESTED"]
    return pilot_state.read_model_request(root, attempt_id, stage, asked[-1]["artifact_digest"])["model_id"]


def test_an_openspec_run_goes_in_parts_even_with_one_batch_and_its_requirements_step_is_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, _attempt = _run(tmp_path, monkeypatch)
    steps = plan(root)
    assert steps["mode"] == "parts" and [part["stage"] for part in steps["parts"]] == ["context-marker:requirements", "context-marker:batch-001"]
    assert steps["parts"][0]["by_code"] is True and "by_code" not in steps["parts"][1]


def test_code_answers_the_requirements_step_with_the_rows_of_the_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project, root, attempt_id = _run(tmp_path, monkeypatch)
    _prepare(root, "context-marker:requirements", capsys)
    artifact = _answer_of(root, attempt_id)
    expected = build_context(project, docs_snapshot=_entries(project, [BASE, ADD]))["artifacts"]["analytics_documentation"]["requirements"]
    assert artifact["artifacts"]["analytics_documentation"]["requirements"] == expected
    assert _model_of(root, attempt_id, "context-marker:requirements") == "controller:openspec-marker"


def test_a_removed_name_the_specification_lacks_is_one_warning_of_the_marker_the_code_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    _write(project, "openspec/changes/drop-fax/specs/owners/spec.md", "## REMOVED Requirements\n### Requirement: Fax machine\n")
    scan = _scan(project, monkeypatch, [BASE, "openspec/changes/drop-fax"])
    _prepare(Path(scan["run_root"]), "context-marker:requirements", capsys)
    lines = [warning for warning in _answer_of(Path(scan["run_root"]), scan["attempt_id"])["warnings"] if " — REMOVED: " in warning]
    assert len(lines) == 1 and "Fax machine" in lines[0]


def test_asking_the_requirements_step_again_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _project_dir, root, _attempt = _run(tmp_path, monkeypatch)
    _prepare(root, "context-marker:requirements", capsys)
    _prepare(root, "context-marker:requirements", capsys)
    kinds = [event["event_type"] for event in pilot_state.derive_state(root)["events"] if event.get("stage_instance_id") == "context-marker:requirements"]
    assert kinds.count("MODEL_REQUESTED") == kinds.count("MODEL_RESPONSE_RECEIVED") == 1


def test_a_plain_document_run_keeps_the_single_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, _attempt = _run(tmp_path, monkeypatch, ["docs/feature.md"])
    steps = plan(root)
    assert steps["mode"] == "single" and [part["stage"] for part in steps["parts"]] == ["context-marker:baseline"]


def test_a_run_of_openspec_and_a_plain_document_is_answered_by_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, _attempt = _run(tmp_path, monkeypatch, [BASE, "openspec/changes/add-search", "docs/feature.md"])
    assert [part["stage"] for part in plan(root)["parts"]] == ["context-marker:baseline"]


def _part(sources: list[str], requirements: list | None = None) -> dict:
    documentation = {} if requirements is None else {"analytics_documentation": {"requirements": requirements}}
    return {"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {**documentation, "source_code_and_diff": {"sources": sources}}, "warnings": []}


def test_a_part_without_requirements_is_taken_and_the_merged_marker_has_the_requirements_of_the_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project, root, attempt_id = _run(tmp_path, monkeypatch)
    _prepare(root, "context-marker:requirements", capsys)
    slot = Path(_prepare(root, "context-marker:batch-001", capsys)["answer_slot"])
    slot.write_text(json.dumps(_part(["src/a.py — reads owners"])), encoding="utf-8")
    assert api.main(["submit", "--run-root", str(root), "--stage", "context-marker:batch-001"]) == 0
    merged = json.loads(capsys.readouterr().out)["result"]["merged"]
    artifact = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", merged["content_digest"])["artifact"]
    expected = build_context(project, docs_snapshot=_entries(project, [BASE, ADD]))["artifacts"]["analytics_documentation"]["requirements"]
    assert artifact["artifacts"]["analytics_documentation"]["requirements"] == expected


def test_a_part_with_other_requirements_is_not_refused_the_step_requirements_stand() -> None:
    answer = _part(["шаг требований — код не анализировался"], [{"source_requirement_id": "SREQ-0001", "text": "one"}])
    part = with_requirements(_part(["src/a.py — x"], [{"source_requirement_id": "SREQ-0001", "text": "rewritten"}]), answer)
    assert part["artifacts"]["analytics_documentation"]["requirements"] == [{"source_requirement_id": "SREQ-0001", "text": "one"}]
    assert part["artifacts"]["source_code_and_diff"]["sources"] == ["src/a.py — x"]


def test_a_part_of_a_plain_document_run_without_requirements_gets_the_requirements_of_the_step() -> None:
    answer = _part(["шаг требований — код не анализировался"], [{"source_requirement_id": "SREQ-0001", "text": "D01. The page shows owners."}])
    assert with_requirements(_part(["src/a.py — x"]), answer)["artifacts"]["analytics_documentation"]["requirements"] == answer["artifacts"]["analytics_documentation"]["requirements"]
