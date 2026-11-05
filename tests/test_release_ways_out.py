"""FaT-O12 (arch-audit 2, before the release): the last ways out and the texts the agent reads.

- O1: a run stopped before the canonical review no longer blocks the plain scan of its request — `finish --abandon
  LEFT_BEFORE_REVIEW` marks it «оставлен» (the attempt is not closed: FaT-AB2), the scan skips it and names it;
- O2: a stand of a tests-only module (FaT-2R) that cannot be brought up — `finish --abandon STAND_UNAVAILABLE`, named in
  exec's fix and refused while the stand answers;
- N3/N4: the partial automation is in the orchestrate SKILL and USAGE; the generator role no longer asks for assemble."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from tools import exec_commands, exec_stand

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]


def _argv(command: str) -> list[str]:
    return shlex.split(command[command.index(" -m tools.run_pipeline ") + len(" -m tools.run_pipeline "):], posix=True)


def _main(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict]:
    from tools.run_pipeline import main

    capsys.readouterr()
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


_DOWN = {"code": "SERVICE_UNREACHABLE", "reason": "SERVICE_UNREACHABLE", "message": "стенд не ответил",
         "fix": "поднимите стенд приложения по адресу из переменной APP_URL и запустите exec снова", "service_url_env": "APP_URL"}


def test_o2_a_stand_that_cannot_be_brought_up_closes_by_the_abandon_exec_names(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_finish import _automation_chain
    from tools import run_pipeline
    from tools.pilot_state import derive_state

    root, _attempt = _automation_chain(tmp_path, ["ПРИНЯТО"])
    monkeypatch.setattr(exec_stand, "_service_stop", lambda project, module: None)  # the stand answers (finish)
    code, refused = _main(capsys, ["finish", "--project", str(tmp_path), "--run", root.name, "--abandon", "STAND_UNAVAILABLE"])
    assert code == 2 and derive_state(root)["attempts"][-1]["state"] != "TERMINAL", refused
    monkeypatch.setattr(exec_stand, "_service_stop", lambda project, module: dict(_DOWN))
    monkeypatch.setattr(exec_commands, "_service_stop", lambda project, module: dict(_DOWN))  # exec
    code, stopped = _main(capsys, ["exec", "--project", str(tmp_path), "--run", root.name])
    assert code == 2 and stopped["code"] == "SERVICE_UNREACHABLE" and "--abandon STAND_UNAVAILABLE" in stopped["fix"], stopped
    command = stopped["fix"].split("стенд не поднять — ", 1)[1]
    code, closed = _main(capsys, _argv(command))
    assert closed["status"] == "ok" and closed["reason_code"] == "STAND_UNAVAILABLE" and closed["accepted"] is False, closed
    assert derive_state(root)["attempts"][-1]["state"] == "TERMINAL"


def test_o1_a_run_stopped_before_the_review_is_left_and_the_plain_scan_starts_anew(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _runs, _scan
    from tools import run_pipeline

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    run_pipeline.cmd_finish(SimpleNamespace(project=str(project), run=first["run_id"], abandon=None))
    monkeypatch.undo()
    assert "--abandon LEFT_BEFORE_REVIEW" in printed[-1]["fix"], printed[-1]
    command = printed[-1]["fix"].split("если нет — ", 1)[1].split(" оставит прогон", 1)[0]
    code, left = _main(capsys, _argv(command))
    assert code == 0 and left["status"] == "ok" and left["left"]["reason"] == "LEFT_BEFORE_REVIEW", left
    # a clean ok: no refusal fields that would send the agent round the same command again
    assert "reason" not in left and "fix" not in left and " scan " in left["next_run"]["command"], left
    code, again = _main(capsys, _argv(command))  # once: a repeat reads the same mark
    assert code == 0 and again["left"] == left["left"]
    code, second = _scan(project, monkeypatch)  # the plain scan, no --new-run
    assert code == 0 and second["run_id"] != first["run_id"] and len(_runs(project)) == 2, second
    assert any("оставлен" in row for row in second.get("abandoned_runs", [])), second


def test_o1_left_is_refused_after_the_review_started(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _automation_chain

    root, _attempt = _automation_chain(tmp_path, [None])
    code, refused = _main(capsys, ["finish", "--project", str(tmp_path), "--run", root.name, "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 2 and refused["code"] == "ABANDON_NOT_NEEDED" and not (root / "left-before-review.json").exists(), refused


def test_n3_n4_the_texts_the_agent_reads() -> None:
    skill = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    mode_b = (ROOT / "skills" / "orchestrate" / "references" / "mode-b.md").read_text(encoding="utf-8")  # PT: mode B text of the development build
    assert "automation_split" in mode_b and "`PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`" in mode_b and "automation_split" not in skill
    usage = (ROOT / "USAGE.md").read_text(encoding="utf-8")
    assert "`AUTOMATION_PARTIAL` — `TERMINAL + PARTIAL + PASS + accepted=false`" in usage
    generator = (ROOT / "skills" / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
    assert "`assemble` and `validate-canonical` before `model-response`" not in generator
    assert "`assemble` runs only after the last batch" in generator


# --------------------------------------------------------------------------------------
# FaT-O12b (bug-hunt 5): a left run is closed for the work everywhere; one envelope for a bare ValueError
# --------------------------------------------------------------------------------------


def test_o12b_a_left_run_takes_no_model_step_and_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug-hunt 5 probe by the new expectation: status, model steps and the report of a left run."""
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan
    from tools import pilot_state
    from tools.project_inventory import read_execution_baseline

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    code, left = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 0, left
    code, status = _main(capsys, ["status", "--project", str(project), "--run", first["run_id"]])
    step = status["next_step"]
    assert step["code"] == "RUN_LEFT" and step["why"] == "прогон оставлен до ревью кейсов", step
    assert " scan " in step["command"] and "--new-run" not in step["command"], step
    root, attempt_id = Path(first["run_root"]), first["attempt_id"]
    state = pilot_state.derive_state(root)
    baseline = read_execution_baseline(root / "baselines" / (state["attempts"][-1]["baseline_digest"].removeprefix("sha256:") + ".json"))
    contexts = list(dict.fromkeys(e["artifact_digest"] for e in state["events"] if e["event_type"] == "CONTEXT_SELECTED"))
    journal = (root / "events.jsonl").read_bytes()
    with pytest.raises(ValueError) as refused:
        pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="after-left",
                                          input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], *contexts])
    assert str(refused.value).startswith("RUN_LEFT") and "простой scan" in str(refused.value)
    with pytest.raises(ValueError) as answered:
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", {"schema_version": "5.0.0"})
    assert str(answered.value).startswith("RUN_LEFT")
    assert (root / "events.jsonl").read_bytes() == journal
    code, report = _main(capsys, ["report", "--project", str(project), "--run", first["run_id"]])
    shown = json.dumps(report, ensure_ascii=False) + (root / "report.md").read_text(encoding="utf-8")
    assert "оставлен до ревью кейсов" in shown, report


def test_o12b_the_mark_hides_only_its_own_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan
    from tools.pilot_state import LEFT_MARK, left_attempt
    from tools.run_pipeline import _left_mark

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    root = Path(first["run_root"])
    (root / LEFT_MARK).write_text(json.dumps({"attempt_id": "0" * 32, "reason": "LEFT_BEFORE_REVIEW"}), encoding="utf-8")
    assert left_attempt(root) is None and _left_mark(root) is None  # a mark of another attempt hides nothing
    (root / LEFT_MARK).write_text(json.dumps({"attempt_id": first["attempt_id"], "reason": "LEFT_BEFORE_REVIEW"}), encoding="utf-8")
    assert left_attempt(root) is None and _left_mark(root) is None  # J1 (A1-5): a file without its events in the journal is no decision of the run
    (root / LEFT_MARK).unlink()
    from tools.run_pipeline import _leave

    _leave(Path(first["project"]) if "project" in first else project, root, first["attempt_id"])  # the one writer: the file and its pair of events
    assert left_attempt(root) == first["attempt_id"] and _left_mark(root)


def test_o12b_a_bare_value_error_has_its_code_and_a_document_not_in_utf8_says_how(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_first_run import _python_project

    project = _python_project(tmp_path, runtime=False)
    docs = project / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "req.md").write_bytes("Требование: владелец виден.".encode("cp1251"))
    code, refused = _main(capsys, ["scan", "--project", str(project), "--module", "nested", "--docs", "docs/req.md"])
    assert code == 2 and refused["code"] == "DOCS_ENCODING" == refused["reason"], refused
    assert "UTF-8" in refused["fix"] and set(refused) >= {"status", "code", "message", "fix", "next"}, refused


def test_o12b_the_texts_name_run_left() -> None:
    skill = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    resume = skill[skill.index("## Resume и дрейф"):]
    assert "RUN_LEFT" in resume and "LEFT_BEFORE_REVIEW" in resume
    assert "LEFT_BEFORE_REVIEW" in (ROOT / "USAGE.md").read_text(encoding="utf-8")
