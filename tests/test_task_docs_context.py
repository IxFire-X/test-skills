"""FaT-3 item 5: the task document of --docs always reaches the model, the documents of other tasks in to_do do not.

Facts of 455ef01 before FaT-3 (handoff/fat-3-plan-20261026.md): without --target every task of to_do reached the model;
with --target, and for a document outside the module, the task document did not reach it at all.  Now the document of
--docs is added to the selection whatever --target says; a document outside the module's inventory stops the scan.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_phase2_scan import _args, _project
from tools import run_pipeline


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _context_paths(payload: dict) -> set[str]:
    run_root = Path(payload["run_root"])
    inventory = json.loads(next((run_root / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    by_id = {item["opaque_id"]: item["project_path"] for item in inventory["files"]}
    selected: set[str] = set()
    for path in (run_root / "context-selections" / payload["attempt_id"]).glob("*.json"):
        text = path.read_text(encoding="utf-8")
        selected |= {project_path for opaque, project_path in by_id.items() if opaque in text}
    return selected


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str] | None, target: str | None = None, expect: int = 0) -> dict:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project, target)
    args.docs = docs
    args.profile = "cases-only-v1"
    assert run_pipeline.cmd_scan(args) == expect, payloads[-1]
    return payloads[-1]


def _two_tasks(tmp_path: Path) -> tuple[Path, Path]:
    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(module, "docs/to_do/T1/spec.md", "Задача 1.\n")
    _write(module, "docs/to_do/T1/notes.md", "Заметки к задаче 1.\n")
    _write(module, "docs/to_do/T2/spec.md", "Задача 2.\n")
    return project, module


def test_the_task_document_goes_and_other_tasks_stay_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module = _two_tasks(tmp_path)
    scan = _scan(project, monkeypatch, ["services/api/docs/to_do/T1/spec.md"])
    context = _context_paths(scan)
    assert {"services/api/src/app.py", "services/api/docs/to_do/T1/spec.md"} <= context
    assert not {"services/api/docs/to_do/T2/spec.md", "services/api/docs/to_do/T1/notes.md"} & context
    summary = scan["context_summary"]
    assert summary["task_documents"] == ["services/api/docs/to_do/T1/spec.md"] and summary["other_tasks_skipped"] == 2
    assert summary["code_files"] >= 1


def test_with_target_the_task_document_still_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module = _two_tasks(tmp_path)
    context = _context_paths(_scan(project, monkeypatch, ["services/api/docs/to_do/T1/spec.md"], target="src"))
    assert {"services/api/src/app.py", "services/api/docs/to_do/T1/spec.md"} <= context
    assert "services/api/docs/to_do/T2/spec.md" not in context


def test_a_task_document_outside_the_module_stops_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(project, "docs/to_do/T1/spec.md", "Задача 1.\n")
    scan = _scan(project, monkeypatch, ["docs/to_do/T1/spec.md"], expect=2)
    assert scan["status"] == "error" and scan["reason"] == "TASK_DOC_OUTSIDE_CONTEXT"
    assert "docs/to_do/T1/spec.md" in scan["detail"] and "--module" in scan["fix"]


def test_ready_stops_on_a_task_document_outside_the_module(tmp_path: Path) -> None:
    from tools.ready import check

    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(project, "docs/to_do/T1/spec.md", "Задача 1.\n")
    report = check(project, ["docs/to_do/T1/spec.md"], profile="cases-only-v1", module="nested")
    row = next(item for item in report["checks"] if item["id"] == "docs:docs/to_do/T1/spec.md")
    assert row["level"] == "STOP" and "--module" in row["fix"] and report["status"] == "BLOCKED"


def test_a_secret_task_document_is_out_of_the_inventory_and_stops_with_its_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(module, "docs/to_do/T1/spec.md", 'Задача. password="secret123456"\n')
    scan = _scan(project, monkeypatch, ["services/api/docs/to_do/T1/spec.md"], expect=2)
    assert scan["reason"] == "TASK_DOC_OUTSIDE_CONTEXT" and "token-assignment-v1" in scan["detail"]


def test_without_docs_the_selection_is_unchanged_and_warns_about_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module = _two_tasks(tmp_path)
    scan = _scan(project, monkeypatch, None)
    context = _context_paths(scan)
    assert {"services/api/docs/to_do/T1/spec.md", "services/api/docs/to_do/T2/spec.md"} <= context
    warning = next(item for item in scan["warnings"] if "to_do" in str(item))
    assert "3 файл" in str(warning) and "--docs" in str(warning)
