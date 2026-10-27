"""FaT-E: after the accepted static review the agent calls exec; exec prepares its own inputs (materialization)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_finish import _args, _module_python
from tests.test_project_native_pytest import _reviewed_inputs
from tests.helpers import MODULE_PYTHON


def _project(tmp_path: Path, **options) -> tuple[Path, Path, str]:
    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    _document, _artifact, _review, _boundary, run_root, attempt_id, _authorization, delta = _reviewed_inputs(project, materialize=False, **options)
    assert delta is None
    return project, run_root, attempt_id


def _has(run_root: Path, attempt_id: str, kind: str) -> bool:
    from tools.pilot_state import read_attempt_receipt

    try:
        read_attempt_receipt(run_root, attempt_id, kind, "ARTIFACT_READ_BACK")
    except (KeyError, ValueError, OSError):
        return False
    return True


def test_exec_after_the_static_review_prepares_its_inputs_and_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import read_terminal_result
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id = _project(tmp_path)
    assert not _has(run_root, attempt_id, "execution-inputs") and not _has(run_root, attempt_id, "generated-delta")
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["verdict"] == "PASS", output
    assert _has(run_root, attempt_id, "execution-inputs") and _has(run_root, attempt_id, "generated-delta")
    assert read_terminal_result(run_root, attempt_id)["accepted"] is True
    again = cmd_exec(_args(project, run_root))
    repeated = json.loads(capsys.readouterr().out)
    assert again == code and repeated["verdict"] == "PASS" and repeated["finalization"] == "complete"


def test_exec_finishes_a_half_done_preparation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import derive_state, publish_execution_inputs, read_model_stage_artifact
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id = _project(tmp_path)
    events = [event for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("attempt_id") == attempt_id]
    artifact = lambda stage: next(read_model_stage_artifact(run_root, attempt_id, stage, e["artifact_digest"])["artifact"]  # noqa: E731
                                  for e in events if e["stage_instance_id"] == stage)
    publish_execution_inputs(run_root, attempt_id, artifact("tc-to-autotest:r1"), artifact("autotest-reviewer:r1"))
    assert _has(run_root, attempt_id, "execution-inputs") and not _has(run_root, attempt_id, "generated-delta")
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["verdict"] == "PASS", output


def test_exec_without_an_accepted_static_review_names_the_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id = _project(tmp_path, publish_static_review=False)
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "EXECUTION_NOT_PREPARED", output
    assert output["fix"] and "ревью" in output["fix"]
    assert not _has(run_root, attempt_id, "generated-delta")


def test_exec_after_a_static_review_that_is_not_accepted_materializes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id = _project(tmp_path, static_verdict="ТРЕБУЕТ ДОРАБОТКИ")
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "EXECUTION_NOT_PREPARED", output
    assert not _has(run_root, attempt_id, "generated-delta") and not _has(run_root, attempt_id, "execution-inputs")
    after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    assert after == before


def test_the_skill_says_exec_right_after_the_static_review() -> None:
    text = Path("skills/orchestrate/SKILL.md").read_text(encoding="utf-8")
    assert "exec сам" in text
