"""P4a: a human runs the build — ``materialize`` prints the command, ``ingest`` takes the reports and holds them against the attempt.

The table of the threat model, a test per row: a report of another module, an old report that lay there before ``materialize``, a report
with extra or missing tests, a test file changed after ``materialize``, ``ingest`` without ``materialize``, ``ingest`` twice, a cut between the
receipt and its events, a link or a path outside the module, a word of the command missing or of another run — and the honest part: what a
report written by hand can pass for.  The run of the command is real (pytest of the fixture project), the reports of the refusals are written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_exec_prepares import _has, _project
from tools import exec_commands

REPORT = "test-results/pytest.xml"


def _args(project: Path, run_root: Path, **extra) -> SimpleNamespace:
    return SimpleNamespace(project=str(project), run=run_root.name, module="python-pytest", language=None, target=None,
                           reports=None, no_reports=False, reason=None, **extra)


def _out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _materialize(project: Path, run_root: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    code = exec_commands.cmd_materialize(_args(project, run_root))
    output = _out(capsys)
    assert code == 0 and output["stage"] == "materialize", output
    return output


def _junit(cases: list[tuple[str, str]], suite: str = "pytest") -> str:
    body = "".join(f'<testcase classname="{classname}" name="{name}" time="0.01"/>' for classname, name in cases)
    return (f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="{suite}" errors="0" failures="0" skipped="0" tests="{len(cases)}" '
            f'time="0.01" timestamp="2026-11-05T10:00:00.000000+00:00" hostname="h">{body}</testsuite></testsuites>')


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _human_run(project: Path, printed: dict, *, drop_challenge: bool = False, suite: str | None = None) -> subprocess.CompletedProcess[str]:
    """The person at the terminal: the printed command, as printed (the module runtime is in the project, so the path is the project's)."""
    command = list(printed["command"])
    if drop_challenge:
        index = command.index("-o")
        del command[index:index + 2]
    if suite is not None:
        command = [f"junit_suite_name={suite}" if token.startswith("junit_suite_name=") else token for token in command]
    cwd = (project / printed["working_directory"]).resolve()
    return subprocess.run([str(cwd / command[0]), *command[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def _ingest(project: Path, run_root: Path, capsys: pytest.CaptureFixture[str], **extra) -> tuple[int, dict]:
    args = _args(project, run_root)
    for key, value in extra.items():
        setattr(args, key, value)
    code = exec_commands.cmd_ingest(args)
    return code, _out(capsys)


def _terminal(run_root: Path, attempt_id: str):
    from tools.pilot_state import read_terminal_result

    try:
        return read_terminal_result(run_root, attempt_id)
    except KeyError:
        return None


@pytest.fixture()
def attempt(tmp_path: Path):
    project, run_root, attempt_id = _project(tmp_path)
    return project, run_root, attempt_id


def test_the_command_of_a_maven_module_of_a_reactor_names_the_module_and_carries_the_word() -> None:
    from tools.execution_adapters import MAVEN, ExecutionRequest, command_for

    selectors = ("org.demo.AppTest#works", "org.demo.AppTest#fails")
    reports, argv = command_for(MAVEN, "/p/mvnw", "default", selectors)
    request = ExecutionRequest(MAVEN, "/p/mvnw", argv, "/p/services/api", selectors, 600, reports, ("PROJECT_NATIVE_ENV",), "default", ())
    challenge = exec_commands._challenge(request, "0123456789abcdef")
    working, command = exec_commands._human_command(Path("/p"), Path("/p/services/api"), "services/api", request, challenge)
    assert working == "." and command[0] == "mvnw"
    assert command[command.index("-pl") + 1] == "services/api" and "-am" in command
    assert "-Dpilot.run=0123456789abcdef" in command and command[-1] == "test"
    assert "-Dsurefire.failIfNoSpecifiedTests=false" in command
    assert challenge == {"kind": "system-property", "name": "pilot.run", "value": "0123456789abcdef"}


def test_gradle_has_no_word_to_carry_and_says_so() -> None:
    from tools.execution_adapters import GRADLE, ExecutionRequest, command_for

    reports, argv = command_for(GRADLE, "/p/gradlew", "default", ("a.B#c",))
    request = ExecutionRequest(GRADLE, "/p/gradlew", argv, "/p", ("a.B#c",), 600, reports, ("PROJECT_NATIVE_ENV",), "default", ())
    assert exec_commands._challenge(request, "x") == {"kind": "not_available", "name": None, "value": None}


# --- one attempt per story: the project of an attempt takes minutes to build, the refusals change nothing, so they share it ----------------

def _word(printed: dict) -> str:
    return next(token.split("=", 1)[1] for token in printed["command"] if token.startswith("junit_suite_name="))


def _reason(code_output: tuple[int, dict]) -> str:
    code, output = code_output
    assert code == 2, output
    return output["reason"]


def _no_process(started: list):
    return lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("a process was started"))


def test_story_materialize_refusals_then_a_real_run_cut_planted_and_repeated(tmp_path: Path, capsys, monkeypatch) -> None:
    project, run_root, attempt_id = _project(tmp_path)
    started: list = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", _no_process(started))

    # row: materialize puts the tests into the project, prints the command and the files to look at, starts nothing
    printed = _materialize(project, run_root, capsys)
    assert started == [] and _has(run_root, attempt_id, "generated-delta") and _has(run_root, attempt_id, "materialization-command")
    assert printed["files_to_review"] == ["tests/test_generated.py"] and printed["review_required"] is True and printed["repeated"] is False
    assert "не правьте и не форматируйте" in printed["note"] and "SOURCE_CHANGED" in printed["note"]
    assert printed["challenge"] == "suite-name" and any(token.startswith("junit_suite_name=PILOT-") for token in printed["command"])
    assert "tests/test_generated.py::test_selected" in printed["command"] and _terminal(run_root, attempt_id) is None and not (project / REPORT).exists()
    # row: materialize twice — the same command, nothing more written
    before = {path.name: path.read_bytes() for path in (run_root / "materialization-commands").glob("*.json")}
    again = _materialize(project, run_root, capsys)
    assert again["command"] == printed["command"] and again["repeated"] is True
    assert {path.name: path.read_bytes() for path in (run_root / "materialization-commands").glob("*.json")} == before

    suite = _word(printed)
    # row: no report at all — a refusal with both ways out
    code, output = _ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] == "INGEST_REPORT_MISSING" and "--no-reports" in output["fix"] and "materialize" in output["fix"], output
    assert _terminal(run_root, attempt_id) is None and not (run_root / "execution-receipts").exists()
    # row: the report of another module, extra tests, a duplicate in two fresh files
    _write(project / REPORT, _junit([("other.module.tests", "test_x")], suite=suite))
    assert _reason(_ingest(project, run_root, capsys)) == "INGEST_REPORT_MISMATCH"
    _write(project / REPORT, _junit([("tests.test_generated", "test_selected"), ("tests.test_extra", "test_more")], suite=suite))
    assert _reason(_ingest(project, run_root, capsys)) == "INGEST_REPORT_MISMATCH"
    (project / REPORT).unlink()
    for name in ("a.xml", "b.xml"):
        _write(project / "test-results" / name, _junit([("tests.test_generated", "test_selected")], suite=suite))
    assert _reason(_ingest(project, run_root, capsys, reports=str(project / "test-results"))) == "INGEST_REPORT_MISMATCH"
    shutil.rmtree(project / "test-results")
    # row: a path outside the module, a link
    outside = tmp_path / "elsewhere"
    _write(outside / "pytest.xml", _junit([("tests.test_generated", "test_selected")], suite=suite))
    assert _reason(_ingest(project, run_root, capsys, reports=str(outside))) == "INGEST_REPORT_OUTSIDE_MODULE"
    try:
        os.symlink(outside, project / "linked-reports", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        assert _reason(_ingest(project, run_root, capsys, reports=str(project / "linked-reports"))) == "INGEST_REPORT_OUTSIDE_MODULE"
        os.rmdir(project / "linked-reports")
    # row: a run of the command without its word, and with the word of another run
    monkeypatch.setattr(subprocess, "Popen", real_popen)
    assert _human_run(project, printed, drop_challenge=True).returncode == 0
    assert _reason(_ingest(project, run_root, capsys)) == "INGEST_CHALLENGE_MISSING"
    assert _human_run(project, printed, suite="PILOT-ffffffffffffffff").returncode == 0
    assert _reason(_ingest(project, run_root, capsys)) == "INGEST_CHALLENGE_MISMATCH"
    # row: the real run of the printed command
    completed = _human_run(project, printed)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # row: a result file planted before the ingest is refused, not bound (and left as it lies)
    planted = run_root / "execution-receipts" / f"{attempt_id}.json"
    _write(planted, '{"planted":true}\n')
    assert _reason(_ingest(project, run_root, capsys)) != "INGEST_REPORT_MISSING"
    assert planted.read_text(encoding="utf-8") == '{"planted":true}\n' and _terminal(run_root, attempt_id) is None
    planted.unlink()
    # row: a cut after the receipt — the same reports finish it, other bytes are refused
    real_finish = exec_commands._finish_ingest
    monkeypatch.setattr(exec_commands, "_finish_ingest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cut")))
    with pytest.raises(RuntimeError):
        exec_commands.cmd_ingest(_args(project, run_root))
    capsys.readouterr()
    assert planted.exists() and _terminal(run_root, attempt_id) is None
    keep = (project / REPORT).read_text(encoding="utf-8")
    (project / REPORT).write_text(keep + "<!-- touched -->", encoding="utf-8")
    assert _reason(_ingest(project, run_root, capsys)) == "INGEST_REPORT_CHANGED"
    (project / REPORT).write_text(keep, encoding="utf-8")
    monkeypatch.setattr(exec_commands, "_finish_ingest", real_finish)
    monkeypatch.setattr(subprocess, "Popen", _no_process(started))
    code, output = _ingest(project, run_root, capsys)
    assert started == [] and code == 0 and output["verdict"] == "PASS" and output["accepted"] is True, output
    # row: the result is marked as the human's
    assert output["execution_source"]["run_by"] == "human" and output["execution_source"]["statement"] == "reports"
    assert output["execution_source"]["challenge"] == "ok" and "пакет его не воспроизводил" in output["execution_source"]["note"]
    from tools.pilot_state import read_attempt_receipt

    payload = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    command_digest = json.loads(next((run_root / "materialization-commands").glob("*.json")).read_text(encoding="utf-8"))["command_digest"]
    assert payload["execution_source"]["human_command_digest"] == command_digest and payload["exit_code"] is None
    # row: ingest again on the closed attempt — the same answer
    code, repeated = _ingest(project, run_root, capsys)
    assert code == 0 and repeated["verdict"] == "PASS" and repeated["finalization"] == "complete"


def test_story_an_old_report_a_stale_one_beside_a_fresh_one_the_flags_and_a_build_that_failed(tmp_path: Path, capsys) -> None:
    project, run_root, attempt_id = _project(tmp_path)
    # row: ingest without materialize — materialize is named, nothing is written
    code, output = _ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] == "INGEST_WITHOUT_MATERIALIZE" and "materialize" in output["fix"], output
    assert not _has(run_root, attempt_id, "generated-delta")
    # row: an old report that lay there before materialize
    _write(project / "test-results" / "old.xml", _junit([("tests.test_generated", "test_selected")]))
    printed = _materialize(project, run_root, capsys)
    assert _reason(_ingest(project, run_root, capsys, reports=str(project / "test-results"))) == "INGEST_REPORT_STALE"
    # row: the old report has the needed test, the fresh one does not — the old one does not make up for it
    _write(project / "test-results" / "fresh.xml", _junit([("tests.test_other", "test_unrelated")], suite=_word(printed)))
    assert _reason(_ingest(project, run_root, capsys, reports=str(project / "test-results"))) == "INGEST_REPORT_MISMATCH"
    # the flags of a statement
    assert _ingest(project, run_root, capsys, no_reports=True, reason="BUILD_FAILED", reports=str(project / "x"))[1]["reason"] == "INGEST_REASON_WITH_REPORTS"
    assert _ingest(project, run_root, capsys, no_reports=True)[1]["reason"] == "INGEST_REASON_UNKNOWN"
    assert _ingest(project, run_root, capsys, reason="BUILD_FAILED")[1]["reason"] == "INGEST_REASON_UNKNOWN"
    assert _terminal(run_root, attempt_id) is None
    # row: the build failed — a negative result on the human's word, the files taken out again
    code, output = _ingest(project, run_root, capsys, no_reports=True, reason="BUILD_FAILED")
    assert output["verdict"] == "NOT_RUNNABLE" and output["accepted"] is False and code != 0, output
    assert output["execution_source"]["statement"] == "BUILD_FAILED" and "пакет его не воспроизводил" in output["execution_source"]["note"]
    assert not (project / "tests" / "test_generated.py").exists() and _terminal(run_root, attempt_id)["accepted"] is False


@pytest.mark.parametrize("reason,verdict,kept", [("TESTS_NOT_STARTED", "NOT_RUNNABLE", False), ("RESULT_UNAVAILABLE", "UNKNOWN", True)])
def test_story_the_other_statements(tmp_path: Path, capsys, reason, verdict, kept) -> None:
    project, run_root, attempt_id = _project(tmp_path)
    _materialize(project, run_root, capsys)
    if reason == "RESULT_UNAVAILABLE":  # the statement does not depend on the test files, which the person may have touched
        target = project / "tests" / "test_generated.py"
        target.write_bytes(target.read_bytes() + b"\n# touched\n")
    code, output = _ingest(project, run_root, capsys, no_reports=True, reason=reason)
    assert output["verdict"] == verdict and output["accepted"] is False and code != 0, output
    assert output["execution_source"]["statement"] == reason
    assert (project / "tests" / "test_generated.py").exists() is kept and _terminal(run_root, attempt_id)["accepted"] is False


def test_story_a_test_file_changed_after_materialize_is_refused_by_name_and_restored_by_one_command(tmp_path: Path, capsys) -> None:
    project, run_root, attempt_id = _project(tmp_path)
    printed = _materialize(project, run_root, capsys)
    target = project / "tests" / "test_generated.py"
    original = target.read_bytes()
    expected = json.loads(next((run_root / "materialization-commands").glob("*.json")).read_text(encoding="utf-8"))["files"][0]["content_digest"]
    target.write_bytes(original + b"\n# formatted by hand\n")
    assert _human_run(project, printed).returncode == 0
    # a report of code that is not the reviewed code is not evidence of the reviewed code: refused, nothing recorded; the file and its digest are named
    code, output = _ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] == "INGEST_TESTS_CHANGED", output
    assert "tests/test_generated.py" in output["message"] and expected in output["message"] and output["files_changed"] == ["tests/test_generated.py"]
    assert "materialize" in output["fix"] and "--restore" in output["fix"] and "--no-reports --reason RESULT_UNAVAILABLE" in output["fix"]
    assert _terminal(run_root, attempt_id) is None and not (run_root / "execution-receipts").exists()
    # the command of the fix puts the reviewed bytes back
    code = exec_commands.cmd_materialize(_with_restore(_args(project, run_root)))
    restored = _out(capsys)
    assert code == 0 and restored["files_restored"] == ["tests/test_generated.py"] and target.read_bytes() == original, restored
    # a new file in the test folder is a change of the project too: refused, and named
    extra = project / "tests" / "test_after_materialize.py"
    extra.write_text("def test_late():\n    assert True\n", encoding="utf-8")
    code, output = _ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] in {"INGEST_REQUEST_CHANGED", "INGEST_TESTS_CHANGED"} and "test_after_materialize.py" in json.dumps(output, ensure_ascii=False), output
    extra.unlink()
    # the person runs the command again; the ingest passes
    shutil.rmtree(project / "test-results", ignore_errors=True)
    assert _human_run(project, printed).returncode == 0
    code, output = _ingest(project, run_root, capsys)
    assert code == 0 and output["verdict"] == "PASS" and output["accepted"] is True, output


def _with_restore(args: SimpleNamespace) -> SimpleNamespace:
    args.restore = True
    return args


def test_story_the_report_written_by_hand_with_the_right_word_passes_for_a_run_and_says_whose_it_is(tmp_path: Path, capsys) -> None:
    """The honest case: a file written by hand with the right tests and the right word cannot be told from a run.  What is proved is its fit
    to this attempt; what is said is that a human supplied it (``execution_source``) and that the package did not reproduce it."""
    project, run_root, _attempt_id = _project(tmp_path)
    printed = _materialize(project, run_root, capsys)
    _write(project / REPORT, _junit([("tests.test_generated", "test_selected")], suite=_word(printed)))
    code, output = _ingest(project, run_root, capsys)
    assert code == 0 and output["execution_source"]["run_by"] == "human" and "не воспроизводил" in output["execution_source"]["note"]


def test_the_commands_are_in_the_parser_and_exec_still_parses_so_that_it_can_answer(tmp_path: Path) -> None:
    from tools.run_pipeline import build_parser

    parser = build_parser()
    for command in ("materialize", "ingest", "exec"):
        assert parser.parse_args([command, "--project", str(tmp_path), "--run", "RUN-x"]).command == command
    assert parser.parse_args(["ingest", "--project", str(tmp_path), "--run", "r", "--no-reports", "--reason", "BUILD_FAILED"]).reason == "BUILD_FAILED"
