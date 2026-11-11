"""V0b/C1: a build tool or module runtime that is missing now is a fixable refusal of materialize, not the end of the run.

pq1-b1: no Maven on PATH made materialize answer «baseline is invalid or unreadable» and point at finish, which closed the
accepted cases and tests negatively.  A damaged baseline keeps its closed refusal."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_closure_deadlocks import _run, _status
from tests.test_exec_prepares import _project



def _venv(project: Path, present: bool) -> None:
    (project / (".venv.off" if present else ".venv")).rename(project / (".venv" if present else ".venv.off"))


def test_a_missing_module_python_names_the_cause_and_leaves_the_run_open(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root, _attempt = _project(tmp_path)
    _venv(project, present=False)
    code, refused = _run(capsys, "materialize", project, root)
    assert code == 2 and refused["reason"] == "EXECUTION_NOT_PREPARED", refused
    assert "EXECUTION_RUNTIME_UNAVAILABLE" in refused["message"] and ".venv" in refused["message"], refused
    assert "AUTOMATION_NOT_MATERIALIZABLE" not in refused["fix"] and "materialize" in refused["fix"] and "--new-run" in refused["fix"], refused
    assert _status(project, root, capsys)["next_step"]["stage"] == "materialize"
    code, finish = _run(capsys, "finish", project, root)
    assert code == 2 and finish["reason"] == "FINISH_FACT_UNDETERMINED" and "--new-run" in finish["fix"], finish
    _venv(project, present=True)
    code, printed = _run(capsys, "materialize", project, root)
    assert code == 0 and printed["stage"] == "materialize", printed


def test_a_system_maven_missing_from_path_reaches_the_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import project_inventory
    from tools.project_inventory import RuntimeUnavailableError

    def gone(*_args, **_kwargs):
        raise RuntimeUnavailableError("system Maven is unavailable on PATH")

    project, root, _attempt = _project(tmp_path)
    monkeypatch.setattr(project_inventory, "validate_execution_baseline_binding", gone)
    code, refused = _run(capsys, "materialize", project, root)
    assert code == 2 and "system Maven is unavailable on PATH" in refused["message"], refused
    assert _status(project, root, capsys)["next_step"]["stage"] == "materialize"


def test_a_damaged_baseline_still_closes_the_attempt_and_says_why(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import project_inventory
    from tools.project_inventory import InventoryError

    def damaged(*_args, **_kwargs):
        raise InventoryError("execution baseline readback is invalid")

    project, root, _attempt = _project(tmp_path)
    monkeypatch.setattr(project_inventory, "validate_execution_baseline_binding", damaged)
    code, refused = _run(capsys, "materialize", project, root)
    assert code == 2 and "invalid or unreadable: execution baseline readback is invalid" in refused["message"], refused
    code, closed = _run(capsys, "finish", project, root)
    assert closed["reason_code"] == "AUTOMATION_NOT_MATERIALIZABLE" and "readback is invalid" in closed["materialization_refusal"], closed
    assert "\n" not in closed["materialization_refusal"] and len(closed["materialization_refusal"]) < 400


def test_the_closed_tool_paths_report_a_missing_runtime_as_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from tools.project_inventory import InventoryError, RuntimeUnavailableError, module_runtime_path, system_maven_path

    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeUnavailableError, match="system Maven is unavailable on PATH"):
        system_maven_path("mvn")
    for wrapper in ("gradlew", "gradlew.bat", ".venv/bin/python"):
        with pytest.raises(RuntimeUnavailableError):
            module_runtime_path(tmp_path, wrapper)
    with pytest.raises(InventoryError) as escaped:  # a path out of the project stays a plain, closed refusal
        module_runtime_path(tmp_path, "../gradlew")
    assert not isinstance(escaped.value, RuntimeUnavailableError)


def test_a_new_run_starts_from_the_state_the_fix_describes(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepted automation, materialize refused, no runtime: the second way out the fix names is a new run, and it starts."""
    from tests.test_scan_guard import _runs, _scan

    project, root, _attempt = _project(tmp_path)
    (project / "README.md").write_text("Пользователь видит список.\n", encoding="utf-8")
    _venv(project, present=False)
    assert _run(capsys, "materialize", project, root)[1]["reason"] == "EXECUTION_NOT_PREPARED"
    runs = len(_runs(project))
    code, started = _scan(project, monkeypatch, module="python-pytest", docs=["README.md"], new_run=True)
    assert code == 0 and started["run_id"] != root.name and len(_runs(project)) == runs + 1, started


def test_the_fix_tells_the_agent_to_ask_a_person_and_install_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, root, _attempt = _project(tmp_path)
    _venv(project, present=False)
    _code, refused = _run(capsys, "materialize", project, root)
    assert "человека" in refused["fix"] and "не устанавливай и не скачивай" in refused["fix"], refused
    _code, finish = _run(capsys, "finish", project, root)
    assert "человек" in finish["fix"] and "агент ничего не ставит" in finish["fix"], finish
