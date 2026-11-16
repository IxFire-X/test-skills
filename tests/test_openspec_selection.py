"""os1: the files named in --docs select one OpenSpec change; the other live changes and the archive stay out of the model context
(not out of the inventory), the composition check is as strict as before, and two named changes stop the scan before any run exists."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_task_docs_context import _context_paths, _write
from tools import api, run_pipeline
from tools.build_context import build_context

BASE = "openspec/specs/owners/spec.md"
ADD = "openspec/changes/add-search/specs/owners/spec.md"
DROP = "openspec/changes/drop-fax/specs/owners/spec.md"
ARCHIVE = "openspec/changes/archive/2026-10-01-init/specs/owners/spec.md"
PROPOSAL = "openspec/changes/add-search/proposal.md"
SPEC = "## Requirements\n### Requirement: Create\nCreate an owner.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n### Requirement: Fax\nKeep a fax.\n#### Scenario: Ok\n- WHEN saved\n- THEN shown\n"
ADDED = "## ADDED Requirements\n### Requirement: Search\nFind an owner.\n#### Scenario: Ok\n- WHEN typed\n- THEN listed\n"
REMOVED = "## REMOVED Requirements\n### Requirement: Fax\n"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    for path, text in ((BASE, SPEC), (ADD, ADDED), (DROP, REMOVED), (ARCHIVE, ADDED), (PROPOSAL, "# Proposal\n\nREQ-9 SHALL search.\n"),
                       ("openspec/changes/drop-fax/proposal.md", "# Proposal\n\nDrop the fax.\n")):
        _write(project, path, text)
    return project


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str], expect: int = 0) -> dict:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = SimpleNamespace(project=str(project), module=None, target=None, docs=docs, profile="cases-only-v1", new_run=True)
    assert run_pipeline.cmd_scan(args) == expect, payloads[-1]
    return payloads[-1]


def _entries(project: Path, docs: list[str]) -> list[dict[str, str]]:
    data = [(doc, (project / doc).read_bytes()) for doc in docs]
    return [{"path": doc, "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")} for doc, raw in data]


def _answer(project: Path, scan: dict, rows_of: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    """The marker step by its public commands, the answer being the requirements of ``rows_of`` composed by ``build_context``."""
    run_root = str(scan["run_root"])
    assert api.main(["prepare", "--run-root", run_root, "--stage", "context-marker:baseline"]) == 0
    slot = Path(json.loads(capsys.readouterr().out)["result"]["answer_slot"])
    rows = build_context(project, docs_snapshot=_entries(project, rows_of))["artifacts"]["analytics_documentation"]["requirements"]
    slot.write_text(json.dumps({"schema_version": "5.0.0", "stage": "context-marker", "warnings": [],
                                "artifacts": {"analytics_documentation": {"requirements": rows}, "source_code_and_diff": {"sources": [BASE + " — spec"]}}}), encoding="utf-8")
    code = api.main(["submit", "--run-root", run_root, "--stage", "context-marker:baseline"])
    return code, json.loads(capsys.readouterr().out)


def test_the_whole_specification_is_accepted_beside_live_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, [BASE])
    code, out = _answer(project, scan, [BASE], capsys)
    assert code == 0, out


def test_a_selected_change_is_accepted_beside_another_live_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, [BASE, ADD])
    code, out = _answer(project, scan, [BASE, ADD], capsys)
    assert code == 0, out


def test_the_other_changes_and_the_archive_leave_the_context_but_not_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = _scan(_project(tmp_path), monkeypatch, [BASE, ADD])
    context = _context_paths(scan)
    assert {BASE, ADD, PROPOSAL} <= context  # the selected change keeps its proposal as context
    assert not {DROP, ARCHIVE, "openspec/changes/drop-fax/proposal.md"} & context
    inventory = json.loads(next((Path(scan["run_root"]) / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    assert {DROP, ARCHIVE} <= {item["project_path"] for item in inventory["files"]}


def test_scan_names_what_it_does_not_apply_in_one_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = _scan(_project(tmp_path), monkeypatch, [BASE, ADD])
    [line] = [item for item in scan["warnings"] if "OpenSpec" in item]
    assert "drop-fax" in line and "архив" in line and "add-search" not in line


def test_two_named_changes_stop_the_scan_before_a_run_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, [BASE, ADD, DROP], expect=2)
    assert scan["reason"] == "OPENSPEC_SOURCE_CONFLICT" and scan["fix"]
    assert not (project / ".pilot-runs").exists()


def test_a_file_of_a_change_in_docs_selects_the_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context_paths(_scan(_project(tmp_path), monkeypatch, [BASE, PROPOSAL]))
    assert PROPOSAL in context and "openspec/changes/drop-fax/proposal.md" not in context


def test_a_named_archive_file_stays_as_data_and_selects_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    other = "openspec/changes/archive/2026-10-02-other/specs/owners/spec.md"
    _write(project, other, ADDED)
    context = _context_paths(_scan(project, monkeypatch, [BASE, ADD, ARCHIVE]))  # two changes named would refuse: the archive is no change
    assert ARCHIVE in context and other not in context
    rows = lambda docs: build_context(project, docs_snapshot=_entries(project, docs))["artifacts"]["analytics_documentation"]["requirements"]
    assert rows([BASE, ARCHIVE]) == rows([BASE])


def test_a_proposal_in_docs_gives_no_requirement_rows(tmp_path: Path) -> None:
    project = _project(tmp_path)
    envelope = build_context(project, docs_snapshot=_entries(project, [PROPOSAL]))
    assert envelope["artifacts"]["analytics_documentation"]["requirements"] == []
    assert any(PROPOSAL in item and "контекст" in item for item in envelope["warnings"])


def test_without_docs_two_live_changes_are_still_refused_with_a_way_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    code, out = _answer(project, _scan(project, monkeypatch, []), [BASE], capsys)
    assert code == 2 and out["code"] == "OPENSPEC_SOURCE_CONFLICT" and "--docs" in out["fix"], out


def test_a_row_of_an_unselected_change_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, [BASE])
    code, out = _answer(project, scan, [BASE, ADD], capsys)
    assert code == 2 and "OPENSPEC_EXTRA" in out["message"], out


def test_a_lost_requirement_of_the_baseline_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, [BASE, ADD])
    code, out = _answer(project, scan, [ADD], capsys)  # the answer composes the delta without the baseline: Create and Fax are lost
    assert code == 2 and "OPENSPEC_MISSING" in out["message"], out
