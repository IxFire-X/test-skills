"""os1: the files named in --docs select one OpenSpec change; the other live changes and the archive stay out of the model context
(not out of the inventory), the composition check is as strict as before, and two named changes stop the scan before any run exists."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_task_docs_context import _context_paths, _write
from tools import api, run_pipeline
from tools.build_context import build_context
from tools.pilot_state import read_run

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


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str], expect: int = 0, new_run: bool = True) -> dict:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = SimpleNamespace(project=str(project), module=None, target=None, docs=docs, profile="cases-only-v1", new_run=new_run)
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


def test_without_docs_two_live_changes_stop_the_scan_and_the_next_scan_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    stop = _scan(project, monkeypatch, [], expect=2)
    assert stop["reason"] == "OPENSPEC_SOURCE_CONFLICT" and "--docs" in stop["fix"]
    _scan(project, monkeypatch, [BASE, ADD], new_run=False)  # the dead run does not block the corrected scan


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


VISITS = "openspec/specs/visits/spec.md"
ARCHIVE_VISITS = "openspec/changes/archive/2026-10-01-init/specs/visits/spec.md"


def _docs_of(scan: dict) -> list[str]:
    return list(read_run(Path(scan["run_root"]))["manifest"]["scan_request"]["docs"])


def test_a_folder_in_docs_is_the_spec_files_under_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, VISITS, SPEC)
    assert _docs_of(_scan(project, monkeypatch, ["openspec/specs"])) == [BASE, VISITS]
    assert _docs_of(_scan(project, monkeypatch, ["openspec/specs/visits"])) == [VISITS]


def test_a_change_folder_is_its_deltas_and_its_proposal_and_selects_the_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = _scan(_project(tmp_path), monkeypatch, [BASE, "openspec/changes/add-search"])
    assert _docs_of(scan) == [BASE, PROPOSAL, ADD]
    assert DROP not in _context_paths(scan)


def test_the_folder_of_an_archived_change_is_all_its_deltas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, ARCHIVE_VISITS, ADDED)
    _write(project, "openspec/changes/archive/2026-10-01-init/proposal.md", "# Proposal\n")
    scan = _scan(project, monkeypatch, [BASE, "openspec/changes/archive/2026-10-01-init"])
    assert _docs_of(scan) == [BASE, ARCHIVE, ARCHIVE_VISITS]


@pytest.mark.parametrize("folder", ["openspec", "openspec/changes", "openspec/changes/archive", "docs", "openspec/changes/empty"])
def test_a_folder_the_code_does_not_open_stops_the_scan_with_a_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str) -> None:
    project = _project(tmp_path)
    _write(project, "docs/a.md", "text\n")
    (project / "openspec" / "changes" / "empty").mkdir()
    stop = _scan(project, monkeypatch, [folder], expect=2)
    assert stop["reason"] == "SNAPSHOT_DOCS" and stop["fix"] and not (project / ".pilot-runs").exists()


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_a_link_inside_a_folder_is_not_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    project = _project(tmp_path)
    _write(tmp_path, "outside/spec.md", SPEC)
    link = project / "openspec" / "specs" / "linked"
    if kind == "junction" and os.name == "nt":  # no rights needed
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp_path / "outside")], capture_output=True, check=True)
    else:
        try:
            link.symlink_to(tmp_path / "outside", target_is_directory=True)
        except OSError:
            pytest.skip("no symbolic links here")
    assert _docs_of(_scan(project, monkeypatch, ["openspec/specs"])) == [BASE]


def test_a_folder_over_the_documents_limit_stops_the_scan_before_a_run_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    for area in "abcde":
        _write(project, f"openspec/specs/{area}/spec.md", SPEC + "x" * 230_000)
    stop = _scan(project, monkeypatch, ["openspec/specs"], expect=2)
    assert stop["reason"] == "NEED_DOCS_LIMIT" and stop["fix"] and not (project / ".pilot-runs").exists()


def test_the_areas_not_named_are_context_and_leave_the_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    _write(project, VISITS, SPEC)
    code, out = _answer(project, _scan(project, monkeypatch, [BASE]), [BASE], capsys)
    assert code == 0, out


def test_a_row_of_an_area_not_named_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    _write(project, VISITS, SPEC)
    code, out = _answer(project, _scan(project, monkeypatch, [BASE]), [BASE, VISITS], capsys)
    assert code == 2 and "OPENSPEC_EXTRA" in out["message"], out


def test_a_change_of_an_area_whose_spec_is_not_named_stops_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = _scan(_project(tmp_path), monkeypatch, ["openspec/changes/add-search"], expect=2)
    assert stop["reason"] == "OPENSPEC_SOURCE_CONFLICT" and BASE in stop["fix"]


def test_a_delta_of_a_second_area_of_the_selected_change_stops_the_scan_before_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, VISITS, SPEC)
    _write(project, "openspec/changes/add-search/specs/visits/spec.md", REMOVED)
    stop = _scan(project, monkeypatch, [BASE, ADD], expect=2)  # one delta named, the change touches visits too
    assert stop["reason"] == "OPENSPEC_SOURCE_CONFLICT" and VISITS in stop["fix"]


def test_an_excluded_change_is_no_live_change_without_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import load_skillsrc

    project = _project(tmp_path)
    _scan(project, monkeypatch, [BASE, ADD])  # writes the .skillsrc
    document = load_skillsrc(project / ".skillsrc")
    document["modules"][0].setdefault("paths", {})["exclude"] = ["openspec/changes/drop-fax/**"]
    (project / ".skillsrc").write_bytes(_payload(document))
    _scan(project, monkeypatch, [])  # the model would get one change: add-search


def test_the_folders_of_docs_are_over_the_limit_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    for area in "ab":
        for part in "xyz":
            _write(project, f"openspec/specs/{area}/{part}/spec.md", SPEC + "x" * 200_000)  # 600 KB a folder: each is under 1 MiB, both are not
    stop = _scan(project, monkeypatch, ["openspec/specs/a", "openspec/specs/b"], expect=2)
    assert stop["reason"] == "NEED_DOCS_LIMIT" and not (project / ".pilot-runs").exists()


def test_the_named_files_count_in_the_total_beside_a_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    for part in "xyz":
        _write(project, f"openspec/specs/a/{part}/spec.md", SPEC + "x" * 230_000)
    for name in ("one", "two"):
        _write(project, f"docs/{name}.md", "REQ-1 SHALL work.\n" + "x" * 230_000)
    stop = _scan(project, monkeypatch, ["openspec/specs/a", "docs/one.md", "docs/two.md"], expect=2)
    assert stop["reason"] == "NEED_DOCS_LIMIT"


def test_the_root_of_the_project_as_a_folder_is_refused_without_a_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(run_pipeline.os, "walk", lambda *_args, **_kw: pytest.fail("the project is walked"))
    stop = _scan(project, monkeypatch, ["."], expect=2)
    assert stop["reason"] == "SNAPSHOT_DOCS"


def test_a_file_of_a_folder_out_of_the_inventory_names_the_way_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, VISITS, "## Requirements\n### Requirement: Login\npassword = 'hunter2hunter2'\n")  # a suspected secret: not a file of the inventory
    stop = _scan(project, monkeypatch, ["openspec/specs"], expect=2)
    assert stop["reason"] == "TASK_DOC_OUTSIDE_CONTEXT" and "файлами" in stop["fix"], stop


def test_ready_stops_with_the_exact_fix_where_scan_stops_on_an_area_not_named(tmp_path: Path) -> None:
    from tools import ready

    report = ready.check(_project(tmp_path), ["openspec/changes/add-search"], "cases-only-v1", None)
    [row] = [row for row in report["checks"] if row["id"].startswith("context") and row["level"] == "STOP"]  # was a WARN «контекст не посчитан»: the report said READY
    assert report["status"] == "BLOCKED" and "OPENSPEC_SOURCE_CONFLICT" in row["message"] and BASE in row["fix"], row


def test_a_change_of_a_new_area_needs_no_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, "openspec/changes/add-pets/specs/pets/spec.md", ADDED)
    _scan(project, monkeypatch, ["openspec/changes/add-pets"])


def test_openspec_files_are_no_files_of_other_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _scan(_project(tmp_path), monkeypatch, [BASE, ADD])["context_summary"]["other_tasks_skipped"] == 0


def test_scan_says_that_a_named_proposal_is_context_not_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = _scan(_project(tmp_path), monkeypatch, [BASE, PROPOSAL])
    assert any(PROPOSAL in item and "не требования" in item for item in scan["warnings"])


def test_a_file_under_openspec_that_is_no_proposal_is_an_ordinary_document(tmp_path: Path) -> None:
    project = _project(tmp_path)
    notes = "openspec/specs/owners/notes.md"
    _write(project, notes, "REQ-7 Owners SHALL be listed.\n")
    envelope = build_context(project, docs_snapshot=_entries(project, [notes]))
    assert envelope["artifacts"]["analytics_documentation"]["requirements"]


def test_ready_takes_a_folder_in_docs(tmp_path: Path) -> None:
    from tools import ready

    project = _project(tmp_path)
    report = ready.check(project, ["openspec/specs"], "cases-only-v1", None)
    assert not [row for row in report["checks"] if row["id"].startswith("docs") and row["level"] == "STOP"], report
