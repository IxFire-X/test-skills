from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_cases_import as ci

# FaT-R3: a first line longer than the csv field limit (131 072 B) without «;» made csv.reader raise _csv.Error —
# not a ValueError, so scan ended in a traceback and left an orphan run.
LONG = "x" * 150_000


def _runs(project: Path) -> list[Path]:
    return sorted((project / ".pilot-runs").glob("*")) if (project / ".pilot-runs").is_dir() else []


def test_a_minified_json_in_docs_is_a_document_not_a_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = ci._project(tmp_path)
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "api.json").write_text(json.dumps({"description": LONG}), encoding="utf-8")
    code, scan = ci._scan(project, monkeypatch, cases=None, docs=["docs/api.json"], profile="cases-only-v1")
    assert code == 0 and scan.get("status") != "error", scan


def test_a_csv_with_a_long_first_line_in_cases_is_a_json_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = ci._project(tmp_path, LONG + "\r\n")
    code, refused = ci._scan(project, monkeypatch)
    assert code == 2 and refused["reason"] == "ZEPHYR_CSV_NOT_CSV", refused
    assert _runs(project) == []


def test_a_long_first_line_in_reference_is_refused_before_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline

    project = ci._project(tmp_path)
    (project / "cases" / "reference.csv").write_text(LONG + "\r\n", encoding="utf-8")
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "req.md").write_text("# Склейка\n\ncombine соединяет значения через двоеточие.\n", encoding="utf-8")
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    code = run_pipeline.main(["scan", "--project", str(project), "--module", "python-pytest", "--docs", "docs/req.md",
                              "--reference", "cases/reference.csv", "--profile", "cases-only-v1", "--new-run"])
    assert code == 2 and printed[-1]["reason"] == "ZEPHYR_CSV_NOT_CSV", printed
    assert _runs(project) == []


def test_an_unexpected_error_in_a_command_is_a_json_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    monkeypatch.setattr(run_pipeline, "cmd_status", lambda _args: (_ for _ in ()).throw(ZeroDivisionError("boom")))
    code = run_pipeline.main(["status", "--project", ".", "--run", "0" * 32])
    assert code == 2 and printed[-1]["status"] == "error" and printed[-1]["reason"] == "INTERNAL", printed
    assert "ZeroDivisionError" in printed[-1]["message"], printed


def test_the_internal_answer_is_cut_to_500_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    monkeypatch.setattr(run_pipeline, "cmd_status", lambda _args: (_ for _ in ()).throw(ZeroDivisionError("y" * 5000)))
    assert run_pipeline.main(["status", "--project", ".", "--run", "0" * 32]) == 2
    assert printed[-1]["reason"] == "INTERNAL" and len(printed[-1]["message"]) <= len("ZeroDivisionError: ") + 500, printed[-1]
