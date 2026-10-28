"""FaT-G: a repeated scan never silently opens a second run, drift names its files, and a second pass starts cleanly.

- an unfinished run of the same request (module, docs, requested profile) refuses a new scan with its run_id;
  ``--new-run`` is the explicit way past it; closed, failed and pre-FaT-G runs never block;
- ``run_id: <id>`` is the last line of the scan output;
- after a project file changes, status and exec name the changed paths (never bytes) and give the real way out;
- no output mentions a child attempt any more;
- after ТРЕБУЕТ ДОРАБОТКИ or a refused automation, finish and status print the scan command of the same request, and that
  command, run verbatim, starts the next run — the code only prints it.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_first_run import _python_project, _write
from tests.test_phase2_scan import _args
from tools import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
DOCS = "services/api/docs/feature.md"


def _request(project: Path, **extra) -> SimpleNamespace:
    args = _args(project)
    args.docs = [DOCS]
    for key, value in extra.items():
        setattr(args, key, value)
    return args


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, **extra) -> tuple[int, dict]:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    code = run_pipeline.cmd_scan(_request(project, **extra))
    monkeypatch.undo()
    return code, payloads[-1]


def _runs(project: Path) -> list[str]:
    return sorted(path.name for path in (project / ".pilot-runs").iterdir() if path.is_dir())


def _status(project: Path, run_id: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    run_pipeline.cmd_status(SimpleNamespace(project=str(project), run=run_id))
    monkeypatch.undo()
    return payloads[-1]


def _run_command(command: str) -> subprocess.CompletedProcess:
    argv = command if os.name == "nt" else shlex.split(command)
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})


# --------------------------------------------------------------------------------------
# a repeated scan
# --------------------------------------------------------------------------------------

def test_a_repeated_scan_is_refused_with_the_open_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    manifest = json.loads((Path(first["run_root"]) / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["scan_request"] == {"module": "nested", "docs": [DOCS], "target": None, "profile": "local-pilot-v1",
                                        "accept_self_review": False, "disposition_policy": None}
    code, again = _scan(project, monkeypatch)
    assert code == 2 and again["reason"] == "SCAN_RUN_IN_PROGRESS" and again["run_id"] == first["run_id"], again
    assert "status" in again["fix"] and "--new-run" in again["fix"] and again.get("next_step")
    assert "finish" not in again["fix"]  # FaT-H2b 2.2: finish would not close a fresh attempt — never offered as a dead end
    assert _runs(project) == [first["run_id"]]
    code, forced = _scan(project, monkeypatch, new_run=True)
    assert code == 0 and forced["run_id"] != first["run_id"] and len(_runs(project)) == 2


def test_another_request_a_failed_scan_and_an_old_run_do_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_reviewer_protocol import _new_run

    project = _python_project(tmp_path, runtime=False)
    _new_run(project)  # a run without scan_request (before FaT-G) is never compared
    code, failed = _scan(project, monkeypatch, target="missing.py")
    assert code == 2 and failed["reason"] == "SOURCE_TARGET_INVALID", failed
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    _write(project, "services/api/docs/other.md", "Другая функция.\n")
    code, other = _scan(project, monkeypatch, docs=["services/api/docs/other.md"])
    assert code == 0 and other["run_id"] != first["run_id"], other
    code, cases = _scan(project, monkeypatch, profile="cases-only-v1")
    assert code == 0, cases


def test_run_id_is_the_last_line_of_the_scan_output(tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=False)
    command = [sys.executable, "-m", "tools.run_pipeline", "scan", "--project", str(project), "--module", "nested", "--docs", DOCS]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    payload, _end = json.JSONDecoder().raw_decode(completed.stdout)
    assert completed.returncode == 0 and completed.stdout.rstrip().splitlines()[-1] == f"run_id: {payload['run_id']}", completed.stdout[-500:]
    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert again.returncode == 2 and again.stdout.rstrip().splitlines()[-1] == f"run_id: {payload['run_id']}", again.stdout[-500:]


# --------------------------------------------------------------------------------------
# drift names its files and the way out
# --------------------------------------------------------------------------------------

def test_status_names_the_changed_files_and_the_new_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch, accept_self_review=True, disposition_policy="cleanup")
    assert code == 0 and scan["policy_profile"] == "local-pilot-v1", scan
    assert "drift" not in _status(project, scan["run_id"], monkeypatch)
    _write(project, "services/api/src/app.py", "X = 2\n")
    _write(project, "services/api/src/added.py", "Y = 1\n")
    status = _status(project, scan["run_id"], monkeypatch)
    drift = status["drift"]
    assert drift["changed"] == ["services/api/src/added.py", "services/api/src/app.py"], drift
    command = drift["command"]
    assert command.startswith(f'"{sys.executable}" -m tools.run_pipeline scan') and f'--docs "{DOCS}"' in command
    assert "--accept-self-review" in command and "--disposition-policy cleanup" in command and "--new-run" in command
    assert "верните" in drift["fix"] and "X = 2" not in json.dumps(status, ensure_ascii=False)
    assert "дочерн" not in json.dumps(status, ensure_ascii=False) and "child attempt" not in json.dumps(status)


def test_a_changed_secret_file_is_named_by_path_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_secret_inputs import SECRET, SECRET_PATH, _secret_project

    project, _digest = _secret_project(tmp_path)
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    _write(project, SECRET_PATH, "db:\n  password: changed-not-real\n")
    status = _status(project, scan["run_id"], monkeypatch)
    assert status["drift"]["changed"] == [SECRET_PATH], status.get("drift")
    text = json.dumps(status, ensure_ascii=False)
    assert SECRET not in text and "changed-not-real" not in text


def test_exec_names_the_changed_files_instead_of_a_child_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _args as exec_args, _project

    project, run_root, _attempt_id, _values = _project(tmp_path)
    (project / "src" / "sample.py").write_text((project / "src" / "sample.py").read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    code = run_pipeline.cmd_exec(exec_args(project, run_root))
    output = capsys.readouterr().out
    payload, _end = json.JSONDecoder().raw_decode(output)
    assert code != 0 and "src/sample.py" in json.dumps(payload, ensure_ascii=False), payload
    assert "child attempt" not in output and "дочерн" not in output


def test_no_next_step_mentions_a_child_attempt() -> None:
    text = json.dumps(run_pipeline._NEXT_STEPS, ensure_ascii=False)
    assert "child attempt" not in text and "дочерн" not in text


def test_ready_says_not_to_change_the_project_after_the_scan(tmp_path: Path) -> None:
    from tools.ready import _table, check

    report = check(_python_project(tmp_path, runtime=False), [DOCS], "cases-only-v1", "nested")
    row = next(item for item in report["checks"] if item["id"] == "run:freeze")
    assert row["level"] == "OK" and ".skillsrc" in row["message"] and row["message"] in _table(report)


# --------------------------------------------------------------------------------------
# the second pass, end to end
# --------------------------------------------------------------------------------------

def _rework(project: Path, scan: dict) -> None:
    from tests.test_reviewer_protocol import _completed, _digest, _session, _started, _verdict
    from tools.orchestrate_test_case_revision import open_reviewer_session, validate_reviewer_session

    run_root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    session, binding, review = _session(_started(), _verdict(), _completed(), review_verdict="ТРЕБУЕТ ДОРАБОТКИ")
    start = {key: session[key] for key in ("session_id", "generator_role", "reviewer_role", "generator_invocation_id",
                                           "reviewer_invocation_id", "host_isolation", "context_budget_bytes")}
    session["boundary_digest"] = open_reviewer_session(run_root, attempt_id, binding, start)["digest"]
    session["digest"] = _digest({key: value for key, value in session.items() if key != "digest"})
    validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id)


def _refused_automation(project: Path, scan: dict) -> None:
    from tests.test_automation_revision_budget import automation, durable_boundary, host_evidence
    from tests.test_finish import _blocked_document
    from tests.test_generated_delta import _effective_bundle_digest

    document = _blocked_document()  # a canonical blocker: the automation ends BLOCKED without a reviewer round
    artifact = automation(document, 1, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    artifact["artifacts"].update(automation_status="BLOCKED", generated_files=[], generated_symbols=[], implementation_relations=[],
                                 manual_dispositions=[], diagnostics=[{"path": "/artifacts", "code": "AUTOMATION_BLOCKED", "message": "Шаг заблокирован."}])
    evidence = host_evidence("session-1", generator="generator-1", reviewer="reviewer-1")
    durable_boundary(project, artifact, "session-1", evidence, run_root=Path(scan["run_root"]), attempt_id=scan["attempt_id"],
                     generator=evidence["generator_invocation_id"], reviewer=evidence["reviewer_invocation_id"],
                     policy_profile="local-pilot-v1", document=document)


@pytest.mark.parametrize(("runtime", "close", "reason"), [(False, _rework, "REWORK"), (True, _refused_automation, "AUTOMATION_BLOCKED")])
def test_the_second_pass_starts_from_the_printed_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime: bool, close, reason: str) -> None:
    project = _python_project(tmp_path, runtime=runtime)
    code, scan = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, scan
    close(project, scan)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    run_pipeline.cmd_finish(SimpleNamespace(project=str(project), run=scan["run_id"]))
    monkeypatch.undo()
    finished = payloads[-1]
    assert finished["reason_code"] == reason and finished["next_run"]["command"], finished
    command = finished["next_run"]["command"]
    assert "--accept-self-review" in command and "--new-run" not in command and "--profile local-pilot-v1" in command
    assert _status(project, scan["run_id"], monkeypatch)["next_run"] == finished["next_run"]
    completed = _run_command(command)
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
    payload, _end = json.JSONDecoder().raw_decode(completed.stdout)
    assert payload["run_id"] != scan["run_id"] and completed.stdout.rstrip().endswith(f"run_id: {payload['run_id']}")
    first = json.loads((Path(scan["run_root"]) / "run-manifest.json").read_text(encoding="utf-8"))["scan_request"]
    second = json.loads((Path(payload["run_root"]) / "run-manifest.json").read_text(encoding="utf-8"))["scan_request"]
    assert first == second


def test_exec_never_trusts_the_profile_written_in_the_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exec checks against local-pilot-v1 with the runtime identity, always; the baseline's own profile is status-only."""
    from tools import pilot_state, project_inventory

    project = _python_project(tmp_path, runtime=True)
    _write(project, "services/api/.venv/Scripts/python.exe", "fixture-runtime-v1")
    code, scan = _scan(project, monkeypatch, profile="cases-only-v1")
    assert code == 0 and scan["policy_profile"] == "cases-only-v1", scan
    run_root = Path(scan["run_root"])
    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == scan["attempt_id"])
    module_root = project / "services" / "api"
    calls: list[dict] = []
    real = project_inventory.validate_execution_baseline
    monkeypatch.setattr(project_inventory, "validate_execution_baseline", lambda *args, **kwargs: calls.append(kwargs) or real(*args, **kwargs))
    assert run_pipeline._baseline_check(project, run_root, attempt, module_root)["status"] == "UNCHANGED"
    assert calls[-1]["policy_profile"] == "cases-only-v1"  # status: the baseline's own profile
    monkeypatch.setattr(pilot_state, "read_attempt_receipt", lambda *_args: {"record": {"delta": {"files": []}}})
    request = SimpleNamespace(adapter_id="pytest:selected-symbols-v1", build_profile="default", typed_parameters=(), executable="unused")
    coordinates = {"manifest": {"project": str(project)}, "run_root": run_root, "attempt": attempt, "module_root": module_root}
    with pytest.raises(run_pipeline.HostStop) as stop:  # the runtime identity is always computed: none here — fail closed
        run_pipeline._revalidate_execution_baseline(coordinates, request)
    assert stop.value.code == "BASELINE_INCOMPLETE", (stop.value.code, str(stop.value))
