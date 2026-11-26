"""A receipt on the disk that does not read (handoff/v08/golden-mode-b-gap.md): status and report say so in one line, and «another
version» only when the fingerprint of the run's package is not this one's.  The words are approved by the user (26.11) and pinned.
The run is built once a module (about a minute) and laid back at its own path before each test."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_finish import _cases_only_effective
from tests.test_quarantine import THREE_FUNCTIONS, _fail
from tools import pilot_state
from tools.run_pipeline import receipts_warning


@pytest.fixture(scope="module")
def frozen(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, str, str]:
    at = tmp_path_factory.mktemp("receipts") / "project"
    at.mkdir()
    run_root, attempt_id, _delta = _fail(at, content=THREE_FUNCTIONS)
    shutil.copytree(at, at.parent / "frozen")
    return at, at.parent / "frozen", run_root.name, attempt_id


@pytest.fixture
def run(frozen: tuple[Path, Path, str, str]) -> tuple[Path, Path, str]:
    """The run at the path it was made at (a copy elsewhere is a moved project)."""
    at, copy, run_id, attempt_id = frozen
    shutil.rmtree(at)
    shutil.copytree(copy, at)
    return at, at / ".pilot-runs" / run_id, attempt_id


def _break(root: Path, attempt_id: str) -> None:
    target = pilot_state.receipt_target(root, attempt_id, "execution-receipt")
    receipt = json.loads(target.read_text(encoding="utf-8"))
    receipt["payload"]["verdict"] = "PASS"
    target.write_text(json.dumps(receipt), encoding="utf-8")


def test_a_receipt_that_does_not_read_is_told_without_a_version(run: tuple[Path, Path, str]) -> None:
    project, root, attempt_id = run
    _break(root, attempt_id)
    assert receipts_warning(project, root, attempt_id) == "RECEIPTS_UNREADABLE"


def test_another_version_only_by_another_fingerprint(run: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    project, root, attempt_id = run
    _break(root, attempt_id)
    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "0" * 64)
    assert receipts_warning(project, root, attempt_id) == "RUN_OF_OTHER_VERSION"


def test_readable_receipts_are_no_line(run: tuple[Path, Path, str]) -> None:
    assert receipts_warning(*run) is None


def test_no_receipt_file_is_no_line(run: tuple[Path, Path, str]) -> None:
    project, root, attempt_id = run
    pilot_state.receipt_target(root, attempt_id, "execution-receipt").unlink()
    assert receipts_warning(project, root, attempt_id) is None


def test_a_moved_project_is_told_by_project_moved_alone(run: tuple[Path, Path, str], tmp_path: Path) -> None:
    project, root, attempt_id = run
    _break(root, attempt_id)
    moved = shutil.copytree(project, tmp_path / "moved")
    assert receipts_warning(moved, moved / ".pilot-runs" / root.name, attempt_id) is None


UNREADABLE = "RECEIPTS_UNREADABLE: квитанции прогона не прочитаны — часть итогов в отчёте может быть пустой"
OTHER_VERSION = "RUN_OF_OTHER_VERSION: прогон сделан другой версией, квитанции не прочитаны — часть итогов в отчёте может быть пустой"


def test_the_line_of_status_and_of_the_report(run: tuple[Path, Path, str]) -> None:
    from tools.run_pipeline import _status_result
    from tools.run_report import build_report, render_markdown

    project, root, attempt_id = run
    _break(root, attempt_id)
    assert UNREADABLE in _status_result(project, root.name)["warnings"]
    assert f"- {UNREADABLE}" in render_markdown(build_report(project, root.name)[0]).splitlines()


def test_the_line_of_another_version(run: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _status_result
    from tools.run_report import _warning_line

    project, root, attempt_id = run
    _break(root, attempt_id)
    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "0" * 64)
    assert OTHER_VERSION in _status_result(project, root.name)["warnings"]
    assert _warning_line("RUN_OF_OTHER_VERSION", {}) == f"- {OTHER_VERSION}"


def test_an_accepted_set_of_cases_only_that_does_not_read_is_told(tmp_path: Path) -> None:
    root, attempt_id = _cases_only_effective(tmp_path)
    target = pilot_state.receipt_target(root, attempt_id, "effective-canonical")
    receipt = json.loads(target.read_text(encoding="utf-8"))
    receipt["document"]["test_cases"] = []
    target.write_text(json.dumps(receipt), encoding="utf-8")
    assert receipts_warning(root.parent.parent, root, attempt_id) == "RECEIPTS_UNREADABLE"
