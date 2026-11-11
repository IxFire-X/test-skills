"""FaT-E, P4b: after the accepted static review the agent calls materialize; it prepares its own inputs and writes the tests, a human runs the build, ingest closes."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_finish import _args, _module_python
from tests.test_project_native_pytest import _reviewed_inputs
from tests.helpers import MODULE_PYTHON

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in

pytestmark = pytest.mark.contract


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


@pytest.mark.slow
def test_materialize_after_the_static_review_prepares_its_inputs_and_the_ingest_of_the_run_closes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import flow_args, run_the_build
    from tools import exec_commands
    from tools.pilot_state import read_terminal_result

    project, run_root, attempt_id = _project(tmp_path)
    assert not _has(run_root, attempt_id, "execution-inputs") and not _has(run_root, attempt_id, "generated-delta")
    code, output = run_the_build(project, run_root, capsys)
    assert code == 0 and output["verdict"] == "PASS", output
    assert _has(run_root, attempt_id, "execution-inputs") and _has(run_root, attempt_id, "generated-delta")
    assert read_terminal_result(run_root, attempt_id)["accepted"] is True
    again = exec_commands.cmd_ingest(flow_args(project, run_root))
    repeated = json.loads(capsys.readouterr().out)
    assert again == code and repeated["verdict"] == "PASS" and repeated["finalization"] == "complete"


@pytest.mark.slow
def test_materialize_finishes_a_half_done_preparation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import run_the_build
    from tools.pilot_state import derive_state, publish_execution_inputs, read_model_stage_artifact

    project, run_root, attempt_id = _project(tmp_path)
    events = [event for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("attempt_id") == attempt_id]
    artifact = lambda stage: next(read_model_stage_artifact(run_root, attempt_id, stage, e["artifact_digest"])["artifact"]  # noqa: E731
                                  for e in events if e["stage_instance_id"] == stage)
    publish_execution_inputs(run_root, attempt_id, artifact("tc-to-autotest:r1"), artifact("autotest-reviewer:r1"))
    assert _has(run_root, attempt_id, "execution-inputs") and not _has(run_root, attempt_id, "generated-delta")
    code, output = run_the_build(project, run_root, capsys)
    assert code == 0 and output["verdict"] == "PASS", output


def test_materialize_without_an_accepted_static_review_names_the_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import flow_args
    from tools.exec_commands import cmd_materialize

    project, run_root, attempt_id = _project(tmp_path, publish_static_review=False)
    code = cmd_materialize(flow_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "EXECUTION_NOT_PREPARED", output
    assert output["fix"] and "ревью" in output["fix"] and "materialize" in output["fix"]
    assert not _has(run_root, attempt_id, "generated-delta")


def test_materialize_after_a_static_review_that_is_not_accepted_materializes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import flow_args
    from tools.exec_commands import cmd_materialize

    project, run_root, attempt_id = _project(tmp_path, static_verdict="ТРЕБУЕТ ДОРАБОТКИ")
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    code = cmd_materialize(flow_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "EXECUTION_NOT_PREPARED", output
    assert not _has(run_root, attempt_id, "generated-delta") and not _has(run_root, attempt_id, "execution-inputs")
    after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    assert after == before


def test_the_text_of_mode_b_says_materialize_right_after_the_static_review() -> None:
    # PT: the text of mode B (materialize, ingest, automation) is in the development build's reference, not in the delivered SKILL.md
    text = (PACK_ROOT / "skills/orchestrate/references/mode-b.md").read_text(encoding="utf-8")
    assert "materialize" in text and "ingest" in text and "exec сам" not in text
    assert "exec сам" not in (PACK_ROOT / "skills/orchestrate/SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(("local", "has_report", "kept"), [(True, False, False), (False, True, False), (True, True, True)])
def test_a_prestart_rejection_keeps_the_request_only_when_it_is_local_and_has_a_report_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: bool, has_report: bool, kept: bool) -> None:
    from tools import exec_commands, execution_core, pilot_state, project_inventory
    from tools.execution_adapters import PYTEST

    taken: list = []

    def report(*_args, request=None, **_kwargs):
        taken.append(request)
        raise ValueError("stop here")  # the rejection is persisted by the caller of this report: not the matter of this test

    delta = {"baseline_digest": "sha256:b", "automation_digest": "sha256:a", "review_digest": "sha256:r"}
    monkeypatch.setattr(execution_core, "runner_report", report)
    monkeypatch.setattr(execution_core, "request_is_attempt_local", lambda *_: local)
    monkeypatch.setattr(execution_core, "request_report_path", lambda _request: Path("r.xml") if has_report else None)
    monkeypatch.setattr(project_inventory, "read_execution_baseline", lambda _path: {"adapter_id": PYTEST})
    monkeypatch.setattr(pilot_state, "read_attempt_receipt", lambda *_: {"record": {"delta": delta}})
    monkeypatch.setattr(pilot_state, "read_effective_canonical", lambda *_: {
        "document": {"document_id": "D", "revision": 1}, "document_digest": "sha256:d", "effective_bundle_receipt_digest": "sha256:e"})
    monkeypatch.setattr(exec_commands, "_attempt_time", lambda *_: "2026-01-01T00:00:00+00:00")
    (tmp_path / "m").mkdir()
    attempt = {"attempt_id": "A", "project": str(tmp_path), "module": "m", "baseline_digest": "sha256:b"}
    marker = object()
    exec_commands._reject_prestart_execution({"run_root": tmp_path / "run", "attempt": attempt}, "CODE", "message", request=marker)
    assert taken == [marker if kept else None]
