"""impin: the generator reads no CSV of cases and no copy of the markup rows in the plan view; the over-budget hint says what to do."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_cases_import import CSV, _project, _scan

CSV_PATH = "cases/zephyr.csv"


def _context_paths(root: Path, attempt_id: str) -> set[str]:
    from tools import pilot_state

    return {row["project_path"] for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "CONTEXT_SELECTED"
            for row in pilot_state.read_context_selection(root, attempt_id, event["artifact_digest"])["files"]}


@pytest.mark.parametrize("docs", [None, ["docs/req.md"]])
def test_the_csv_of_cases_is_no_context_of_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str] | None) -> None:
    code, scan = _scan(_project(tmp_path), monkeypatch, docs=docs)
    assert code == 0, scan
    paths = _context_paths(Path(scan["run_root"]), scan["attempt_id"])
    assert CSV_PATH not in paths and "src/sample.py" in paths, paths


def test_the_csv_stays_in_the_inventory_and_the_run_reads_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.cases_import import run_csv

    code, scan = _scan(_project(tmp_path), monkeypatch)
    assert code == 0, scan
    root = Path(scan["run_root"])
    found = run_csv(root, pilot_state.derive_state(root)["attempts"][-1])
    assert found is not None and found[0] == CSV_PATH and found[1] == CSV.encode("utf-8")


def test_the_requirements_step_of_an_import_with_documents_still_reads_the_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.step_size import journal_files

    project = _project(tmp_path)
    code, scan = _scan(project, monkeypatch, docs=["docs/req.md"])
    assert code == 0, scan
    files = journal_files(project, Path(scan["run_root"]).name, "context-marker:requirements", None)
    assert CSV_PATH in [path.relative_to(project).as_posix() for path in files]


def test_ready_leaves_the_csv_of_cases_out_of_the_context_as_scan_does(tmp_path: Path) -> None:
    from tools.project_inventory import build_inventory
    from tools.run_pipeline import context_preview

    project = _project(tmp_path)
    inventory = build_inventory(project, project)
    by_id = {str(row["opaque_id"]): row["project_path"] for row in inventory["files"]}
    kept, skipped, _budget = context_preview(inventory, project, project, None, None, CSV_PATH)
    assert CSV_PATH in by_id.values() and CSV_PATH not in [by_id[opaque] for opaque in kept] and CSV_PATH not in [row["project_path"] for row in skipped]


def test_a_scan_without_cases_keeps_the_csv_of_the_project_in_its_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, scan = _scan(_project(tmp_path), monkeypatch, cases=None, docs=["docs/req.md"])
    assert code == 0, scan
    assert CSV_PATH in _context_paths(Path(scan["run_root"]), scan["attempt_id"])


def test_the_plan_view_row_is_the_id_and_the_order() -> None:
    from tools.step_size import plan_view_bytes

    row = {"source_requirement_id": "SREQ-0001", "display_order": 1, "text": "t", "provenance": ["p"], "digest": "sha256:" + "0" * 64}
    view = json.loads(plan_view_bytes({"batches": [], "source_requirements": [row]}))
    assert view["source_requirements"] == [{"source_requirement_id": "SREQ-0001", "display_order": 1}]
    assert "provenance" in view["texts"] and "digest" in view["texts"]


def test_the_over_budget_hint_names_the_target_and_the_exclude() -> None:
    from tools.step_size import measure

    hint = measure(Path("."), [], "tc-generator", budget=1)["hint"]
    assert "--target" in hint and "paths.exclude" in hint and hint.count("\n") <= 2
