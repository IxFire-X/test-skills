"""V8-W4: the diagnoses of the route — a journal without a published plan, a marker answer that cannot be read, the help of ``prepare`` and ``submit``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _generated_run
from tests.test_stage_steps import _call, _events, _status
from tools import api, pilot_state


def _old_run(tmp_path: Path) -> dict:
    """A run whose request of a batch was published by an earlier version: the request is in the journal, the plan and the header never were."""
    run = _generated_run(tmp_path, generated=False)
    fragment = json.loads(Path(run["fragments"][0]).read_bytes())
    plan = json.loads(Path(run["plan"]).read_bytes())
    original = pilot_state._expected_model_inputs
    pilot_state._expected_model_inputs = lambda *_args: None  # the code before FaT-DG
    try:
        pilot_state.publish_model_request(run["run_root"], run["attempt_id"], f"tc-generator:{fragment['batch_id']}", model_id="m", invocation_id="old",
                                          input_digests=[run["marker_digest"], json.loads(Path(run["context"]).read_bytes())["digest"], plan["digest"], plan["header_digest"]])
    finally:
        pilot_state._expected_model_inputs = original
    run["stage"] = f"tc-generator:{fragment['batch_id']}"
    return run


@pytest.mark.parametrize("command", ["prepare", "submit"])
def test_a_batch_of_a_journal_without_a_plan_is_refused_with_stage_no_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str) -> None:
    run = _old_run(tmp_path)
    before = _events(run["run_root"])
    code, out = _call(capsys, command, run["run_root"], run["stage"])
    assert code == 2 and out["code"] == "STAGE_NO_PLAN" and "--new-run" in out["fix"] and _events(run["run_root"]) == before, out


def test_the_review_of_a_journal_without_a_plan_is_refused_with_stage_no_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _old_run(tmp_path)
    assert _call(capsys, "prepare", run["run_root"], "tc-reviewer:canonical")[1]["code"] == "STAGE_NO_PLAN"


def test_status_of_a_journal_without_a_plan_does_not_lead_to_the_plan(tmp_path: Path) -> None:
    run = _old_run(tmp_path)
    result = _status(run["run_root"])
    assert result["next"]["stage"] != "plan" and result["waiting_for"] == "human" and "STAGE_NO_PLAN" in result["recovery"], result


def test_status_names_no_family_of_the_stages_as_the_stage(tmp_path: Path) -> None:
    run = _old_run(tmp_path)
    assert _status(run["run_root"])["next"]["stage"] is None


def test_an_unreadable_marker_answer_is_refused_with_its_cause(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _paused_run

    root, attempt_id, *_ = _paused_run(tmp_path)
    for artifact in (root / "model-stage-artifacts" / attempt_id).iterdir():
        artifact.write_bytes(b"{")  # the context drifted: the file of the answer is no longer what the journal names
    code, out = _call(capsys, "prepare", root, "scope")
    assert code == 2 and out["code"] == "SCOPE_MARKER_UNREADABLE" and "--new-run" in out["fix"], out


@pytest.mark.parametrize("command", ["prepare", "submit"])
def test_every_flag_of_the_command_has_a_description(command: str) -> None:
    sub = next(action for action in api._parser()._actions if getattr(action, "choices", None) and command in action.choices).choices[command]
    assert [action.option_strings[0] for action in sub._actions if action.option_strings != ["-h", "--help"] and not action.help] == []


def test_the_help_is_printed_in_utf_8_through_a_pipe() -> None:
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "tools.api", "submit", "--help"], cwd=Path(__file__).resolve().parents[1], capture_output=True, check=True).stdout
    assert "папка прогона".encode("utf-8") in out


def _batch_two_without_the_capability_of_batch_one(tmp_path: Path) -> tuple[Path, str, dict]:
    """The form of the live case (N3): batch 2 uses a capability of batch 1 and carries no copy of it."""
    from tests.helpers import prepare_stage, submit_stage
    from tests.test_digest_traps import _publish_inputs

    run = _generated_run(tmp_path, generated=False)
    _publish_inputs(run)
    first, second = (json.loads(Path(path).read_bytes()) for path in run["fragments"])
    assert prepare_stage(run["run_root"], f"tc-generator:{first['batch_id']}")[0] == 0 and submit_stage(run["run_root"], f"tc-generator:{first['batch_id']}", first)[0] == 0
    step = second["test_cases"][0]["steps"][0]
    step["operation"] = {"kind": "project_action", "capability_id": "CAP-shared"}
    step["outputs"] = [{"source": {"kind": "project_result", "name": "id"}}]
    second["operation_capabilities"] = []
    second.pop("digest")
    stage = f"tc-generator:{second['batch_id']}"
    assert prepare_stage(run["run_root"], stage)[0] == 0
    return run["run_root"], stage, second


def test_batch_two_without_the_copy_is_refused_with_the_capability_and_the_prior_input(tmp_path: Path) -> None:
    from tests.helpers import submit_stage

    root, stage, answer = _batch_two_without_the_capability_of_batch_one(tmp_path)
    before = _events(root)
    code, out = submit_stage(root, stage, answer)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and "CAP-shared" in out["fix"] and "prior_capabilities" in out["fix"], out
    assert _events(root)[: len(before)] == before


def test_the_orphan_file_refusal_says_what_the_file_lacks_and_where_helpers_go() -> None:
    from tools.automation_validation import _MESSAGES
    from tools.fixes import FIXES

    assert "implementation_relations" in _MESSAGES["AUTOMATION_ORPHAN_FILE"]
    assert "тестового класса" in FIXES["AUTOMATION_ORPHAN_FILE"] and "generated_files" in FIXES["AUTOMATION_ORPHAN_FILE"]


def test_the_control_word_note_says_what_to_do_when_the_session_writes_the_cases_itself(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_stage_steps import _generator

    run = _generator(tmp_path)
    note = _call(capsys, "prepare", run["root"], run["stages"][0])[1]["result"]["nonce_note"]
    assert "пишешь ты сам" in note and "вставлять некуда" in note


def test_prepare_of_the_automation_prints_the_java_level_and_the_junit_params(tmp_path: Path) -> None:
    from tests.helpers import prepare_stage
    from tests.test_autotest_cycle import GENERATOR, autotest_run
    from tests.test_java_api_level import JSOUP_POM

    run = autotest_run(tmp_path)
    (run["root"].parents[1] / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    code, out = prepare_stage(run["root"], GENERATOR)
    assert code == 0, out
    read = out["result"]["read"]
    assert read["java_level"]["level"] == 8 and read["java_level"]["rule"].startswith("только API Java 8"), read
    assert "available" in read["junit_params"] and read["junit_params"]["rule"], read
    assert Path(read["role_files"][0]).as_posix().endswith("skills/tc-to-autotest/SKILL.md"), read


def _scan_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change) -> dict:
    """``scan`` of a module with one source file that ``change`` takes away between the inventory and the filter of the context."""
    from tests.test_phase2_scan import _args, _project
    from tools import run_pipeline

    project, module = _project(tmp_path)
    (module / "src" / "unit.py").write_text("VALUE = 1\n", encoding="utf-8")
    original = run_pipeline.context_filter
    monkeypatch.setattr(run_pipeline, "context_filter", lambda *args, **kwargs: (change(module / "src" / "unit.py"), original(*args, **kwargs))[1])
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project, "src/unit.py")) == 2
    return payloads[-1]


def _link_in_place_of(path: Path) -> None:
    from tests.test_stage_steps import _junction

    path.unlink()
    (path.parent / "elsewhere").mkdir()
    _junction(path, path.parent / "elsewhere")


def test_scan_names_the_file_that_vanished_after_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _scan_after(tmp_path, monkeypatch, lambda path: path.unlink())
    assert out["reason"] == "CONTEXT_SELECTION_INVALID" and "services/api/src/unit.py" in out["detail"] and "исчез" in out["detail"] and out["fix"].startswith("покажи человеку") and "повтори scan" in out["fix"], out


def test_scan_names_the_file_that_became_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _scan_after(tmp_path, monkeypatch, _link_in_place_of)
    assert "services/api/src/unit.py" in out["detail"] and "reparse" in out["detail"] and "paths.exclude" in out["fix"] and "локальный диск" in out["fix"] and out["fix"].startswith("покажи человеку"), out


def test_a_file_the_system_will_not_stat_is_named_with_the_error_of_the_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from tools import project_inventory

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Отказано в доступе")

    monkeypatch.setattr(os, "stat", denied)
    refusal = project_inventory._context_file_refusal(tmp_path / "unit.py", "src/unit.py")
    assert "src/unit.py не читается: Отказано в доступе" in str(refusal) and "paths.exclude" in refusal.fix


def test_prepare_of_batch_two_prints_the_prior_capabilities_again_on_a_repeat(tmp_path: Path) -> None:
    from tests.helpers import prepare_stage

    root, stage, _answer = _batch_two_without_the_capability_of_batch_one(tmp_path)  # prepared once there
    before = _events(root)
    code, again = prepare_stage(root, stage)
    prior = again["result"]["prior_capabilities"]
    assert code == 0 and prior["capability_ids"] == ["CAP-shared"] and prior["body_at"] and _events(root) == before, again


def test_prepare_names_the_files_of_the_role_of_the_stage(tmp_path: Path) -> None:
    from tests.helpers import prepare_stage

    root, stage, _answer = _batch_two_without_the_capability_of_batch_one(tmp_path)
    role_files = prepare_stage(root, stage)[1]["result"]["read"]["role_files"]
    assert Path(role_files[0]).as_posix().endswith("skills/tc-generator/SKILL.md") and all(Path(path).is_file() for path in role_files), role_files


def test_prepare_of_the_automation_has_no_prior_capabilities(tmp_path: Path) -> None:
    from tests.helpers import prepare_stage
    from tests.test_autotest_cycle import GENERATOR, autotest_run

    run = autotest_run(tmp_path)
    code, out = prepare_stage(run["root"], GENERATOR)
    assert code == 0 and "prior_capabilities" not in out["result"], sorted(out["result"].get("prior_capabilities", {}))


def test_the_report_of_a_run_with_an_unreadable_marker_answer_names_the_cause(tmp_path: Path) -> None:
    from tests.test_scope_pause import _paused_run
    from tools.run_report import build_report

    root, attempt_id, *_ = _paused_run(tmp_path)
    for artifact in (root / "model-stage-artifacts" / attempt_id).iterdir():
        artifact.write_bytes(b"{")
    report, _warnings = build_report(root.parent.parent, root.name)
    assert report["scope"]["refused"]["code"] == "SCOPE_MARKER_UNREADABLE" and report["scope"]["refused"]["fix"], report["scope"]
