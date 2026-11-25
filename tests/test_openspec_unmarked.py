"""t4: a baseline of an area written in prose (no «### Requirement:») — a MODIFIED gets its own cause, and a change that only adds gets a warning: no regression of the area."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_openspec_applied import ADD, BASE, DELTA, DOCS, MODIFY, SEARCH, _entries, _project, _scan
from tools import ready, run_pipeline
from tools.build_context import RequirementConflict, openspec_applied
from tools.fixes import human_for

PROSE = "Owners are searched by last name.\n"
CONFLICT = "OPENSPEC_SOURCE_CONFLICT"


def _said(entries: list[dict]) -> str:
    with pytest.raises(RequirementConflict) as stop:
        openspec_applied(entries)
    return str(stop.value)


def test_a_modified_on_a_baseline_without_requirements_has_its_own_cause_naming_the_area(tmp_path: Path) -> None:
    project = _project(tmp_path, PROSE, MODIFY + SEARCH)
    said = _said(_entries(project, [BASE, DELTA]))
    assert said.startswith("MODIFIED on an unmarked baseline") and "owners" in said


def test_a_modified_in_an_area_without_a_spec_keeps_the_old_cause(tmp_path: Path) -> None:
    project = _project(tmp_path, PROSE, MODIFY + SEARCH)
    assert _said(_entries(project, [DELTA])).startswith("MODIFIED is not in the baseline")


def test_the_scan_stop_says_the_baseline_is_not_marked_not_to_check_the_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, PROSE, MODIFY + SEARCH)
    assert run_pipeline.main(["scan", "--project", str(project), "--docs", DOCS[0], "--docs", DOCS[1], "--profile", "cases-only-v1"]) == 2
    stop = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]
    assert stop["human"] == human_for(CONFLICT, stop["message"]) and stop["human"].startswith("В базе области") and "сверь имя" not in stop["fix"], stop


def test_ready_says_the_same_in_its_row(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, PROSE, MODIFY + SEARCH), DOCS, "cases-only-v1", None)
    [row] = [row for row in report["checks"] if row["id"] == "context:openspec"]
    assert row["level"] == "STOP" and row["message"].startswith("В базе области")


def test_a_change_that_only_adds_to_a_prose_baseline_warns_in_the_scan_that_the_area_has_no_regression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = _scan(_project(tmp_path, PROSE, ADD + SEARCH), monkeypatch, DOCS)["warnings"]
    assert [warning for warning in warnings if "в базе области owners нет размеченных требований" in warning]


def test_ready_warns_in_its_own_row_too(tmp_path: Path) -> None:
    report = ready.check(_project(tmp_path, PROSE, ADD + SEARCH), DOCS, "cases-only-v1", None)
    [row] = [row for row in report["checks"] if row["id"] == "openspec:unmarked"]
    assert row["level"] == "WARN" and "owners" in row["message"]
