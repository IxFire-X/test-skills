"""q8: a change of OpenSpec that the specification already has written is no stop; a real difference stops the scan before any run, with its own fix."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_openspec_selection import _entries, _scan
from tests.test_task_docs_context import _write
from tools import api, ready, run_pipeline, stage_steps
from tools.build_context import RequirementConflict, build_context, openspec_applied
from tools.fixes import OPENSPEC_REASONS, fix_for, human_for

BASE, DELTA, DOCS = "openspec/specs/owners/spec.md", "openspec/changes/add-search/specs/owners/spec.md", ["openspec/specs/owners/spec.md", "openspec/changes/add-search"]
CREATE = "### Requirement: Create\nCreate an owner.\n#### Scenario: Ok\n- WHEN saved\n- THEN stored\n"
SEARCH = "### Requirement: Search\nFind an owner.\n#### Scenario: Ok\n- WHEN typed\n- THEN listed\n"
ADD, MODIFY, REMOVE = "## ADDED Requirements\n", "## MODIFIED Requirements\n", "## REMOVED Requirements\n### Requirement: Search\n"
RENAME = "## RENAMED Requirements\n- FROM: `### Requirement: Lookup`\n- TO: `### Requirement: Search`\n"
CONFLICT = "OPENSPEC_SOURCE_CONFLICT"


def _project(tmp_path: Path, spec: str, delta: str) -> Path:
    project = tmp_path / "project"
    _write(project, BASE, "## Requirements\n" + spec)
    _write(project, DELTA, delta)
    return project


def _applied(project: Path) -> dict[str, int]:
    return openspec_applied(_entries(project, [BASE, DELTA]))


def test_an_added_requirement_the_specification_already_has_is_taken_from_the_change(tmp_path: Path) -> None:
    project = _project(tmp_path, CREATE + SEARCH, ADD + SEARCH)
    rows = build_context(project, docs_snapshot=_entries(project, [BASE, DELTA]))["artifacts"]["analytics_documentation"]["requirements"]
    [search] = [row for row in rows if "Find an owner" in row["text"]]
    assert len(rows) == 2 and any(DELTA in mark for mark in search["provenance"])


def test_the_same_text_with_other_line_ends_spaces_and_breaks_is_the_same(tmp_path: Path) -> None:
    delta = ADD + "### Requirement: Search\r\nFind  an\r\n owner.   \r\n#### Scenario: Ok\r\n- WHEN typed\r\n\r\n- THEN listed\r\n"
    assert _applied(_project(tmp_path, CREATE + SEARCH, delta)) == {"add-search": 1}


def test_a_modified_requirement_that_already_has_the_new_text_is_counted(tmp_path: Path) -> None:
    assert _applied(_project(tmp_path, CREATE + SEARCH, MODIFY + SEARCH)) == {"add-search": 1}


def test_a_removed_requirement_the_specification_no_longer_has_is_counted(tmp_path: Path) -> None:
    assert _applied(_project(tmp_path, CREATE, REMOVE)) == {"add-search": 1}


def test_a_renamed_requirement_the_specification_already_has_under_the_new_name_is_counted(tmp_path: Path) -> None:
    assert _applied(_project(tmp_path, CREATE + SEARCH, RENAME)) == {"add-search": 1}


def test_a_requirement_the_change_really_adds_is_not_counted(tmp_path: Path) -> None:
    assert _applied(_project(tmp_path, CREATE, ADD + SEARCH)) == {}


OTHER = SEARCH.replace("Find an owner.", "Find an owner by name.")


@pytest.mark.parametrize(("spec", "delta", "said", "phrase"), [
    (CREATE + SEARCH, ADD + OTHER, "ADDED has another text", "Требование из раздела ADDED"),
    (CREATE, MODIFY + SEARCH, "MODIFIED is not in the baseline", "Изменение правит требование"),
    (CREATE, RENAME, "rename does not match baseline", "Переименование в изменении"),
    (CREATE, ADD + SEARCH + SEARCH, "repeats a requirement", "В файле изменения или спецификации"),
], ids=["added-other-text", "modified-absent", "rename-neither", "repeated"])
def test_a_real_difference_stops_the_scan_before_any_run_with_its_phrase(tmp_path: Path, capsys: pytest.CaptureFixture[str], spec: str, delta: str, said: str, phrase: str) -> None:
    project = _project(tmp_path, spec, delta)
    assert run_pipeline.main(["scan", "--project", str(project), "--docs", DOCS[0], "--docs", DOCS[1], "--profile", "cases-only-v1"]) == 2
    stop = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]  # the run id follows the JSON on the next line
    assert stop["reason"] == CONFLICT and said in stop["detail"] and stop["human"].startswith(phrase), stop
    assert stop["attempt_id"] is None and stop["fix"] != fix_for(CONFLICT)  # no attempt: the model was not asked


def _said(project: Path) -> str:
    with pytest.raises(RequirementConflict) as stop:
        _applied(project)
    return str(stop.value)


def _named(*names: str) -> str:
    return "".join(f"### Requirement: {name}\nText.\n#### Scenario: Ok\n- WHEN a\n- THEN b\n" for name in names)


NEAR = "ближайшие имена в spec.md области: "


def test_a_modified_name_the_specification_lacks_names_the_three_nearest(tmp_path: Path) -> None:
    spec = _named("Search owners", "Search owner list", "Search owner by name", "Search owner by city", "Visits")
    near = _said(_project(tmp_path, spec, MODIFY + _named("Search owner"))).split(NEAR)[1].split(", ")
    assert len(near) == 3 and "Visits" not in near and "Search owners" in near


def test_a_rename_that_fits_nothing_names_the_nearest_old_name(tmp_path: Path) -> None:
    assert NEAR + "Lookups" in _said(_project(tmp_path, _named("Lookups", "Create"), RENAME))


def test_a_name_with_nothing_near_it_adds_no_names(tmp_path: Path) -> None:
    assert NEAR not in _said(_project(tmp_path, _named("Create"), MODIFY + SEARCH))


def test_the_scan_names_the_requirements_already_written_in_one_line_per_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = _scan(_project(tmp_path, CREATE + SEARCH, ADD + SEARCH), monkeypatch, DOCS)["warnings"]
    [line] = [warning for warning in warnings if "уже вписано" in warning]
    assert "add-search" in line and "1" in line and "Find an owner" not in line


def test_a_refusal_of_the_composition_carries_the_fix_of_its_cause_and_its_phrase(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    said = "MODIFIED is not in the baseline: x/spec.md:3 — Search"

    def refuse(*_args, **_kwargs):
        raise RequirementConflict(CONFLICT, said)

    monkeypatch.setattr(stage_steps, "prepare", refuse)
    assert api.main(["prepare", "--run-root", "x", "--stage", "tc-generator:b1"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["fix"] == fix_for(CONFLICT, said) != fix_for(CONFLICT) and out["human"] == human_for(CONFLICT, said)


def test_ready_says_the_phrase_first_and_the_old_text_in_brackets(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, CREATE + SEARCH, ADD + OTHER), DOCS, "cases-only-v1", None)
    [row] = [row for row in report["checks"] if row["id"] == "context:openspec"]
    assert row["level"] == "STOP" and row["message"].startswith("Требование из раздела ADDED") and f"({CONFLICT}: ADDED has another text" in row["message"], row


def test_every_cause_of_the_table_starts_a_message_the_package_raises() -> None:
    tools = Path(__file__).resolve().parents[1] / "tools"
    others = "\n".join(path.read_text(encoding="utf-8") for path in tools.glob("*.py") if path.name != "fixes.py")
    assert [prefix for prefix, _fix, _human in OPENSPEC_REASONS if prefix not in others] == []


def test_every_cause_of_the_composition_has_a_fix_of_its_own() -> None:
    fixes = [fix for prefix, fix, _human in OPENSPEC_REASONS if prefix.isascii()]  # the messages of build_context are English, those of the scan Russian
    assert all(fixes) and len(set(fixes)) == len(fixes)
