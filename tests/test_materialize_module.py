"""q17: materialize in a project of several modules — the module is the attempt's own when --module is left out, a wrong one stops before anything is written,
and the refusals that leave a dead attempt say the way out."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.helpers import MODULE_PYTHON
from tools import exec_commands
from tools.run_pipeline import HostStop


def _project(tmp_path: Path) -> Path:
    """Two python modules, `a` and `b`; the attempt of the tests below is the one of `b`."""
    modules = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        modules.append({"id": name, "root": name, "stack": {"language": "python", "build_tool": "pip"}, "paths": {"source": ["src"], "tests": ["tests"]},
                        "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
                        "detected_from": ["pyproject.toml"]})
    (tmp_path / ".skillsrc").write_text(json.dumps({"schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
                                                    "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": modules}), encoding="utf-8")
    return tmp_path


def _coordinates(project: Path) -> dict[str, Any]:
    return {"manifest": {"scan_request": {"module": "b"}}, "run_root": project / "run", "module_root": project / "b", "attempt": {"attempt_id": "A", "module": "b"}}


def _args(module: str | None) -> SimpleNamespace:
    return SimpleNamespace(module=module, language=None, restore=False)


def test_the_module_of_the_attempt_is_taken_when_module_is_left_out(tmp_path: Path) -> None:
    project = _project(tmp_path)
    root, _language, module = exec_commands._attempt_module(project, _coordinates(project), _args(None))
    assert root == (project / "b").resolve() and module["id"] == "b"


def test_a_module_that_is_not_the_attempts_is_refused_with_the_way_out(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(HostStop) as stop:
        exec_commands._attempt_module(project, _coordinates(project), _args("a"))
    assert stop.value.code == "MODULE_MISMATCH" and stop.value.fix


def test_an_unknown_module_is_refused_with_an_upper_case_code_and_the_way_out(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(HostStop) as stop:
        exec_commands._attempt_module(project, _coordinates(project), _args("nope"))
    assert stop.value.code == "MODULE_UNKNOWN" and stop.value.fix


def test_materialize_settles_the_module_before_it_writes_the_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)

    def written(*_args: object) -> list[str]:
        raise AssertionError("the tests were written before the module was settled")

    monkeypatch.setattr(exec_commands, "_validated_execution_coordinates", lambda *_: _coordinates(project))
    monkeypatch.setattr(exec_commands, "_terminal_answer", lambda *_: None)
    monkeypatch.setattr(exec_commands, "_read_printed", lambda *_: None)
    monkeypatch.setattr(exec_commands, "_prepare_execution", written)
    args = SimpleNamespace(project=str(project), run="R", module="nope", language=None, restore=False)
    code = exec_commands.cmd_materialize(args)
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "MODULE_UNKNOWN" and output["fix"]


def test_a_rejection_that_cannot_be_persisted_says_to_start_a_new_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import project_inventory

    def unreadable(*_args: object) -> None:
        raise ValueError("no baseline")

    monkeypatch.setattr(project_inventory, "read_execution_baseline", unreadable)
    attempt = {"attempt_id": "A", "project": str(tmp_path), "module": "m", "baseline_digest": "sha256:b"}
    (tmp_path / "m").mkdir()
    assert exec_commands._reject_prestart_execution({"run_root": tmp_path / "run", "attempt": attempt}, "CODE", "message") == 2
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "EXECUTION_RECEIPT" and "--new-run" in output["fix"]
