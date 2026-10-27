"""FaT-B2: ready and scan agree on a task document larger than a context batch; the ready table carries every command.

- a task document larger than a batch stops scan and ready with one code and a fix (the document is never cut);
- the self-review scan command is in the table and uses the package's own interpreter;
- the rows of the execution block never read STOP: cases do not wait for them;
- ``ready --target`` previews the narrowed context the scan would build.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_context_filter import BIG, DOCS, MODULE, _project, _scan
from tests.test_first_run import _write

ROOT = Path(__file__).resolve().parents[1]


def _big_task_document(project: Path) -> None:
    _write(project, DOCS, "Требование: пользователь видит список.\n" * (BIG // 40 + 1))


def test_scan_stops_on_a_task_document_larger_than_a_batch_with_a_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _big_task_document(project)
    code, scan = _scan(project, monkeypatch)
    assert code == 2 and scan["reason"] == "CONTEXT_TASK_DOCUMENT_TOO_LARGE", scan
    assert DOCS in scan["detail"] and "limits.context_batch_bytes" in scan["fix"] and "limits.step_input_bytes" in scan["fix"]
    assert "разбейте" in scan["fix"]


def test_ready_shows_the_same_stop_before_the_scan(tmp_path: Path) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    _big_task_document(project)
    report = check(project, [DOCS], profile="cases-only-v1")
    stops = [row for row in report["checks"] if row["level"] == "STOP"]
    assert report["status"] == "BLOCKED" and [row["id"] for row in stops] == ["context:task-document-too-large"], stops
    assert "CONTEXT_TASK_DOCUMENT_TOO_LARGE" in stops[0]["message"] and "limits.context_batch_bytes" in stops[0]["fix"]


def test_the_self_review_command_is_in_the_table_with_the_package_interpreter(tmp_path: Path) -> None:
    from tests.test_first_run import _python_project
    from tools.ready import _table, check

    report = check(_python_project(tmp_path, runtime=False), ["docs/task.md"], "cases-only-v1", "nested")
    fix = next(row for row in report["checks"] if row["id"] == "review:isolation")["fix"]
    assert fix.startswith(f'"{sys.executable}" -m tools.run_pipeline scan') and fix.endswith("--accept-self-review")
    assert fix in _table(report)


def test_execution_rows_are_warnings_never_stops(tmp_path: Path) -> None:
    from tests.test_secret_inputs import _two_test_roots
    from tools.ready import _table, check

    report = check(_two_test_roots(tmp_path), ["services/api/docs/feature.md"], profile="local-pilot-v1")
    execution = [row for row in report["checks"] if row["block"] == "execution"]
    baseline = [row for row in execution if row["id"].startswith("execution:baseline:")]
    assert baseline and all(row["level"] == "WARN" and row["fix"] for row in baseline), execution
    assert not [row for row in execution if row["level"] == "STOP"] and report["status"] == "READY"
    table = _table(report)
    assert "Нужно только для исполнения" in table and "СТОП" not in table.split("Нужно только для исполнения")[1]


def test_ready_target_previews_the_narrowed_context(tmp_path: Path) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    _write(project, f"{MODULE}/src/other/Big.java", "class Big {\n" + "  int a = 1;\n" * (BIG // 13 + 1) + "}\n")
    wide = check(project, [DOCS], profile="cases-only-v1")
    assert [row["id"] for row in wide["checks"] if row["level"] == "STOP"] == ["context:too-large"]
    narrow = check(project, [DOCS], profile="cases-only-v1", target="src/app.py")
    assert not [row for row in narrow["checks"] if row["level"] == "STOP"], narrow["checks"]
    fix = next(row for row in narrow["checks"] if row["id"] == "review:isolation")["fix"]
    assert '--target "src/app.py"' in fix and fix.endswith("--accept-self-review")
    completed = subprocess.run([sys.executable, "-m", "tools.ready", "--project", str(project), "--docs", DOCS, "--profile", "cases-only-v1",
                                "--target", "src/app.py"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert completed.returncode == 0 and json.loads(completed.stdout)["status"] == "READY", completed.stderr[-2000:]
