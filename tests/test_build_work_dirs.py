"""V0b/C20: the work directories of a build are no input of the baseline, and a change of a source or a build file still is.

realworld-b: Gradle rewrote three files of ``.gradle/`` while the printed command ran, and ``ingest`` refused with BASELINE_DRIFT.  ``build``,
``target``, ``__pycache__``, ``.pytest_cache`` were already left out by name (``scan_rules``); ``.gradle`` was in no list."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_first_run import _python_project, _write
from tests.test_scan_guard import _scan, _status
from tools.scan_rules import directory_reason


MODULE = "services/api"
WORK_FILES = (
    ".gradle/9.5.0/gc.properties", ".gradle/buildOutputCleanup/cache.properties", ".gradle/vcs-1/gc.properties",
    "build/test-results/test/TEST-x.xml", "target/surefire-reports/TEST-x.xml", "test-results/pytest.xml",
    ".pytest_cache/v/cache/lastfailed", "src/pkg/__pycache__/x.txt",
)


@pytest.mark.parametrize("name", [".gradle", "build", "target", "__pycache__", ".pytest_cache"])
def test_the_closed_set_of_build_work_directories_is_left_out(name: str) -> None:
    assert directory_reason((), name) is not None


def test_a_gradle_cache_is_left_out_inside_src_too() -> None:
    assert directory_reason(("src", "main"), ".gradle") == ("BUILD_OUTPUT", "output-directory-v1")


def test_files_that_a_build_writes_between_scan_and_ingest_are_no_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=True)
    _write(project, f"{MODULE}/.gradle/9.5.0/gc.properties", "gc=1\n")  # there when the scan ran: Gradle rewrites it
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    for name in WORK_FILES:
        _write(project, f"{MODULE}/{name}", "written by the build\n")
    assert "drift" not in _status(project, scan["run_id"], monkeypatch)


@pytest.mark.parametrize("change", [("src/app.py", "X = 2\n"), ("pyproject.toml", "[project]\nname = 'other'\n"), ("build.gradle", "plugins { id 'java' }\n")])
def test_a_source_or_a_build_file_still_is_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: tuple[str, str]) -> None:
    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    _write(project, f"{MODULE}/.gradle/9.5.0/gc.properties", "gc=1\n")
    _write(project, f"{MODULE}/{change[0]}", change[1])
    assert _status(project, scan["run_id"], monkeypatch)["drift"]["changed"] == [f"{MODULE}/{change[0]}"]
