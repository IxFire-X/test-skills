"""Regression tests for the 2026-10-05 pipeline review: durable state core."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tools import confined_output, pilot_state
from tools.pilot_state import append_event, create_run, derive_state

AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}
PACK_ROOT = Path(__file__).resolve().parents[1]


def _digest(number: int) -> str:
    return "sha256:" + f"{number:064x}"


def _run(tmp_path: Path) -> Path:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    return root


def _markers(root: Path) -> list[Path]:
    return sorted((root / "pending-events").glob("*.json")) if (root / "pending-events").exists() else []


def _script(body: str, *args: str, **popen: object) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(PACK_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(body), *args], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen)


# --- B1 ------------------------------------------------------------------------------------------

def test_b1_status_read_between_event_file_and_journal_commit_does_not_destroy_the_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    original = pilot_state.atomic_write_confined_bytes_at_root
    observed: list[int] = []

    def status_poll_then_commit(*args: object, **kwargs: object) -> None:
        # The pending marker and events/NNN.json exist, events.jsonl is not replaced yet.
        observed.append(len(derive_state(root)["events"]))
        original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", status_poll_then_commit)
    event = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))
    monkeypatch.undo()

    assert observed == [2]
    state = derive_state(root)
    assert state["events"][-1] == event and len(state["events"]) == 3
    assert (root / "events" / "0000000003.json").exists()
    assert _markers(root) == []


def test_b1_reader_never_deletes_a_crashed_writers_marker_and_the_next_writer_recovers(tmp_path: Path) -> None:
    root = _run(tmp_path)
    previous = json.loads((root / "events" / "0000000002.json").read_text(encoding="utf-8"))
    orphan = pilot_state._sealed({"schema_version": "2.0.0", "seq": 3, "event_type": "ARTIFACT_PUBLISHED", "run_id": previous["run_id"], "actor": "controller",
                                  "observed_at": "2026-08-26T00:00:00.000000Z", "prev_digest": previous["digest"], "artifact_digest": _digest(7)})
    orphan_data = pilot_state._canonical_bytes(orphan)
    orphan_path = root / "events" / "0000000003.json"
    orphan_path.write_bytes(orphan_data)
    marker = root / "pending-events" / "0000000003.json"
    marker.parent.mkdir(exist_ok=True)
    marker.write_bytes(pilot_state._canonical_bytes(pilot_state._pending_event_value(orphan, orphan_data, (root / "events.jsonl").read_bytes())))

    for _ in range(3):
        assert len(derive_state(root)["events"]) == 2
    assert marker.exists() and orphan_path.read_bytes() == orphan_data

    event = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(8))
    assert event["seq"] == 3 and event["artifact_digest"] == _digest(8)
    assert _markers(root) == []
    assert derive_state(root)["events"][-1] == event


def test_b1_run_lock_file_serializes_a_second_process(tmp_path: Path) -> None:
    root = _run(tmp_path)
    holder = _script("""
        import sys, time
        from pathlib import Path
        from tools import pilot_state
        root = Path(sys.argv[1])
        with pilot_state.run_lock(root):
            print("locked", flush=True)
            time.sleep(1.5)
    """, str(root))
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        state = derive_state(root)
        waited = time.monotonic() - started
    finally:
        holder.wait(timeout=30)
    assert (root / ".lock").is_file()
    assert len(state["events"]) == 2
    assert waited >= 0.8, waited


def test_b1_lock_wait_is_bounded_and_reports_a_busy_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    holder = _script("""
        import sys, time
        from pathlib import Path
        from tools import pilot_state
        with pilot_state.run_lock(Path(sys.argv[1])):
            print("locked", flush=True)
            sys.stdin.readline()
    """, str(root), stdin=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(pilot_state, "_RUN_LOCK_TIMEOUT_SECONDS", 0.3)
        with pytest.raises(ValueError, match="run is locked"):
            derive_state(root)
    finally:
        holder.stdin.write("\n"); holder.stdin.flush()
        holder.wait(timeout=30)
    assert len(derive_state(root)["events"]) == 2


def test_b1_a_held_run_lock_leaves_the_lock_file_readable_to_the_projects_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Live run e (2026-10-09): Petclinic's nohttp checkstyle reads every file under the project, the driver held the
    # Windows byte lock on .pilot-runs/<run>/.lock during the Maven gate, the read failed and the gate removed the tests.
    root = _run(tmp_path)
    holder = _script("""
        import sys
        from pathlib import Path
        from tools import pilot_state
        with pilot_state.run_lock(Path(sys.argv[1])):
            print("locked", flush=True)
            sys.stdin.readline()
    """, str(root), stdin=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert (root / ".lock").read_bytes() == b""
        monkeypatch.setattr(pilot_state, "_RUN_LOCK_TIMEOUT_SECONDS", 0.3)
        with pytest.raises(ValueError, match="run is locked"):
            derive_state(root)
    finally:
        holder.stdin.write("\n"); holder.stdin.flush()
        holder.wait(timeout=30)


def test_b1_a_held_review_runner_lock_leaves_its_file_readable(tmp_path: Path) -> None:
    from tools import model_runner

    path = tmp_path / "lock"
    held = model_runner._lock(path, blocking=False)
    assert held is not None
    try:
        assert path.read_bytes() == b""
        assert model_runner._lock(path, blocking=False) is None
    finally:
        held.close()


def test_b1_concurrent_status_polling_never_corrupts_the_journal(tmp_path: Path) -> None:
    root = _run(tmp_path)
    reader = _script("""
        import sys
        from pathlib import Path
        from tools.pilot_state import derive_state
        root = Path(sys.argv[1]); count = 0
        while not (root.parent / "stop").exists():
            derive_state(root); count += 1
        print(count)
    """, str(root))
    try:
        for number in range(25):
            append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(100 + number))
    finally:
        (root.parent / "stop").write_text("", encoding="utf-8")
        out, err = reader.communicate(timeout=60)
    assert reader.returncode == 0, err
    assert int(out.strip()) >= 1
    assert len(derive_state(root)["events"]) == 27
    assert _markers(root) == []


# --- B2 ------------------------------------------------------------------------------------------

def test_b2_stale_writer_removes_its_own_marker_and_the_run_stays_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    original_marker = pilot_state._pending_event_value
    raced: list[dict] = []

    def other_writer_commits_first(event: dict, data: bytes, prior: bytes) -> dict:
        if not raced:
            # Another writer commits seq 3 after this writer has read the journal bytes it will extend.
            raced.append({})
            raced[0] = pilot_state._append_event(tmp_path.resolve(), root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=None, batch_id=None, artifact_digest=_digest(1))
        return original_marker(event, data, prior)

    monkeypatch.setattr(pilot_state, "_pending_event_value", other_writer_commits_first)
    with pytest.raises(ValueError):
        pilot_state._append_event(tmp_path.resolve(), root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=None, batch_id=None, artifact_digest=_digest(2))
    monkeypatch.undo()

    assert _markers(root) == []
    state = derive_state(root)
    assert [event["seq"] for event in state["events"]] == [1, 2, 3]
    assert state["events"][-1] == raced[0]
    retry = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(2))
    assert retry["seq"] == 4 and derive_state(root)["events"][-1] == retry


# --- M04 / M05 -----------------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="directory fsync is a POSIX durability step")
def test_m04_event_commit_syncs_the_directories_it_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stat as stat_module

    root = _run(tmp_path)
    synced: set[str] = set()
    original = os.fsync

    def recording_fsync(descriptor: int) -> None:
        if stat_module.S_ISDIR(os.fstat(descriptor).st_mode):
            synced.add(os.path.basename(os.readlink(f"/proc/self/fd/{descriptor}")) if os.path.exists("/proc/self/fd") else "dir")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))
    if os.path.exists("/proc/self/fd"):
        assert {"events", "pending-events", root.name} <= synced, synced
    else:
        assert synced


def test_m05_journal_replace_retries_a_transient_windows_sharing_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    original = confined_output._replace_output_once
    failures = {"left": 2}
    sleeps: list[float] = []

    def flaky(target: object, temporary: object, handle: object) -> None:
        if getattr(target, "name", "") == "events.jsonl" and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(13, "sharing violation")
        original(target, temporary, handle)

    monkeypatch.setattr(confined_output, "_replace_output_once", flaky)
    monkeypatch.setattr(confined_output, "_REPLACE_RETRY_ALWAYS", True)
    monkeypatch.setattr(confined_output.time, "sleep", sleeps.append)
    event = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))

    assert failures["left"] == 0 and len(sleeps) == 2
    assert derive_state(root)["events"][-1] == event


def test_m05_replace_retry_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    before = (root / "events.jsonl").read_bytes()
    calls: list[int] = []

    def always_denied(target: object, temporary: object, handle: object) -> None:
        calls.append(1)
        raise PermissionError(13, "sharing violation")

    monkeypatch.setattr(confined_output, "_replace_output_once", always_denied)
    monkeypatch.setattr(confined_output, "_REPLACE_RETRY_ALWAYS", True)
    monkeypatch.setattr(confined_output.time, "sleep", lambda _seconds: None)
    with pytest.raises(ValueError, match="event publication failed"):
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))
    monkeypatch.undo()

    assert 2 <= len(calls) <= 10
    assert (root / "events.jsonl").read_bytes() == before and _markers(root) == []
    assert len(derive_state(root)["events"]) == 2


def test_m05_temporary_names_never_make_the_path_longer_than_the_final_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    seen: list[str] = []
    original_open = os.open

    def recording_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith(".tmp"):
            seen.append(os.path.basename(str(path)))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))
    if os.name != "nt":
        assert seen
    assert all(len(name) <= len("events.jsonl") + 8 for name in seen), seen


# --- M01 -----------------------------------------------------------------------------------------

def _attempt(tmp_path: Path, root: Path) -> dict:
    from helpers import phase_two_baseline
    from tools.pilot_state import create_attempt

    baseline = phase_two_baseline(root, tmp_path, {"module": "."})
    return dict(create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, baseline))


def test_m01_attempt_returns_to_active_when_work_continues_after_waiting(tmp_path: Path) -> None:
    root = _run(tmp_path)
    attempt = _attempt(tmp_path, root)
    attempt_id = attempt["attempt_id"]
    assert derive_state(root)["attempts"][0]["state"] == "ACTIVE"
    append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(1))
    assert derive_state(root)["attempts"][0]["state"] == "WAITING_FOR_MODEL"
    # Publishing or reading back an artifact is bookkeeping, not progress.
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(2))
    assert derive_state(root)["attempts"][0]["state"] == "WAITING_FOR_MODEL"
    assert pilot_state._state_for(attempt_id, [
        *derive_state(root)["events"],
        {"event_type": "MODEL_RESPONSE_RECEIVED", "attempt_id": attempt_id},
    ]) == "ACTIVE"
    assert pilot_state._state_for(attempt_id, [
        {"event_type": "WAITING_FOR_INPUT", "attempt_id": attempt_id},
        {"event_type": "CONTEXT_SELECTED", "attempt_id": attempt_id},
    ]) == "ACTIVE"
    assert pilot_state._state_for(attempt_id, [
        {"event_type": "WAITING_FOR_MODEL", "attempt_id": attempt_id},
        {"event_type": "MODEL_REQUESTED", "attempt_id": "another"},
    ]) == "WAITING_FOR_MODEL"
    assert pilot_state._state_for(attempt_id, [
        {"event_type": "ATTEMPT_TERMINAL", "attempt_id": attempt_id},
        {"event_type": "TERMINAL_RETRY_OBSERVED", "attempt_id": attempt_id},
    ]) == "TERMINAL"


# --- M02 -----------------------------------------------------------------------------------------

def test_m02_interrupted_attempt_creation_rolls_back_and_reads_keep_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    original = pilot_state._append_event

    def interrupt(project: Path, run_root: Path, event_type: str, **kwargs: object) -> dict:
        if event_type == "ATTEMPT_CREATED":
            raise KeyboardInterrupt
        return original(project, run_root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "_append_event", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _attempt(tmp_path, root)
    monkeypatch.undo()

    assert list((root / "attempts").glob("*.json")) == []
    assert derive_state(root)["attempts"] == []
    assert derive_state(root)["attempts"] == [] and _attempt(tmp_path, root)["attempt_id"]


def test_m02_reader_tolerates_one_uncommitted_attempt_left_by_a_killed_process(tmp_path: Path) -> None:
    from helpers import phase_two_baseline
    from tools.pilot_state import create_attempt, read_run

    root = _run(tmp_path)
    baseline = phase_two_baseline(root, tmp_path, {"module": "."})
    manifest = read_run(root)["manifest"]
    orphan_id = "c" * 32
    orphan = root / "attempts" / f"{orphan_id}.json"
    pilot_state._publish(tmp_path.resolve(), root, orphan, {
        "schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": orphan_id, "project": manifest["project"],
        "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": baseline["digest"],
    }, "attempt")
    before = orphan.read_bytes()

    state = derive_state(root)
    assert state["attempts"] == [] and orphan.read_bytes() == before
    event = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(5))
    assert derive_state(root)["events"][-1] == event

    attempt = create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, baseline)
    assert not orphan.exists()
    assert [item["attempt_id"] for item in derive_state(root)["attempts"]] == [attempt["attempt_id"]]


def test_m02_two_uncommitted_or_unsealed_attempt_files_still_fail_closed(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "attempts").mkdir(exist_ok=True)
    (root / "attempts" / ("d" * 32 + ".json")).write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="invalid attempt"):
        derive_state(root)


# --- M06 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("authorization", [
    {"request_id": "request-1", "host_id": "host-1"},
    {"execution_requested": False, "host_id": "host-1"},
    {"host_id": "host-1"},
    {},
    ["request_id", "execution_requested"],
    None,
])
def test_m06_incomplete_authorization_is_a_value_error_not_a_key_error(tmp_path: Path, authorization: object) -> None:
    with pytest.raises(ValueError, match="unsafe authorization"):
        create_run(tmp_path, "cases-only-v1", authorization)  # type: ignore[arg-type]
    assert not (tmp_path / ".pilot-runs").exists()


def test_m06_content_conflict_is_not_reported_as_an_unsafe_path(tmp_path: Path) -> None:
    root = _run(tmp_path)
    target = root / "attempts" / ("e" * 32 + ".json")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"{\"other\":true}\n")
    with pytest.raises(ValueError) as caught:
        pilot_state._publish(tmp_path.resolve(), root, target, {"schema_version": "1.0.0"}, "probe")
    assert "already exists with different content" in str(caught.value)
    assert "unsafe" not in str(caught.value)
    with pytest.raises(ValueError, match="unsafe probe path"):
        pilot_state._publish(tmp_path.resolve(), root, tmp_path / "outside.json", {"schema_version": "1.0.0"}, "probe")


# --- M07 -----------------------------------------------------------------------------------------

def _git_project(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "notes.txt").write_text("dirty\n", encoding="utf-8")
    return project


def test_m07_missing_git_executable_is_a_clear_value_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _git_project(tmp_path)

    def no_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(pilot_state.subprocess, "run", no_git)
    with pytest.raises(ValueError, match="project Git state is unavailable"):
        create_run(project, "cases-only-v1", AUTHORIZATION)
    assert not (project / ".pilot-runs").exists()


def test_m07_git_runs_with_a_timeout_and_without_overriding_safe_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _git_project(tmp_path)
    calls: list[tuple[list[str], dict]] = []
    original = subprocess.run

    def recording(argv: list[str], **kwargs: object):
        calls.append((list(argv), dict(kwargs)))
        return original(argv, **kwargs)

    monkeypatch.setattr(pilot_state.subprocess, "run", recording)
    run = create_run(project, "cases-only-v1", AUTHORIZATION)
    assert run["manifest"]["project_state"]["kind"] == "GIT_TREE"
    git_calls = [(argv, kwargs) for argv, kwargs in calls if argv and argv[0] == "git"]
    assert git_calls
    for argv, kwargs in git_calls:
        assert not any("safe.directory" in str(part) for part in argv), argv
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0

    def hang(argv: list[str], **kwargs: object):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    monkeypatch.setattr(pilot_state.subprocess, "run", hang)
    with pytest.raises(ValueError, match="project Git state is unavailable"):
        create_run(project, "cases-only-v1", AUTHORIZATION)


# --- M08 -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["terminal result", "finalization receipt", "pre finalization trace", "terminal trace", "disposition receipt"])
def test_m08_reread_validates_terminal_closure_and_disposition_against_their_schemas(tmp_path: Path, label: str) -> None:
    root = _run(tmp_path)
    target = root / "probe.json"
    # Correctly sealed and canonical, but not a valid document of its kind.
    target.write_bytes(pilot_state._canonical_bytes(pilot_state._sealed({"schema_version": "1.0.0", "unexpected": True})))
    with pytest.raises(ValueError, match="schema validation failed"):
        pilot_state._read_artifact(tmp_path.resolve(), root, target, label)


# --- M03 -----------------------------------------------------------------------------------------

def test_m03_second_legitimate_wait_after_progress_is_recorded_not_swallowed(tmp_path: Path) -> None:
    root = _run(tmp_path)
    attempt_id = _attempt(tmp_path, root)["attempt_id"]
    first = append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(1))
    # An immediate retry of the same append is still idempotent.
    assert append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(1)) == first
    append_event(root, "WAITING_FOR_INPUT", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(2))
    assert derive_state(root)["attempts"][0]["state"] == "WAITING_FOR_INPUT"

    second = append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(1))

    assert second["seq"] > first["seq"]
    assert derive_state(root)["attempts"][0]["state"] == "WAITING_FOR_MODEL"
    assert append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt_id, artifact_digest=_digest(1)) == second


# --- R12: exit codes -----------------------------------------------------------------------------

def test_r12_cases_only_exit_code_separates_a_valid_terminal_from_a_broken_run() -> None:
    from tests.test_exit_policy import _facts
    from tools.pilot_state import exit_code, terminal_result

    valid = terminal_result(_facts(), "cases-only-v1")
    assert valid["accepted"] is False and exit_code(valid) == 1
    rework = terminal_result(_facts(completion="PARTIAL", reason_code="REWORK", authoritative_verdict="REJECTED", authoritative_verdict_count=1), "cases-only-v1")
    assert rework["accepted"] is False and exit_code(rework) == 1

    fatal = _facts(completion="FATAL", coverage=None, reason_code="BASELINE_INCOMPLETE", reviewer_session_complete=False,
                   reviewer_pre_verdict_abort=True, authoritative_verdict=None, authoritative_verdict_count=0)
    for facts in (fatal, _facts(finalization_valid=False), _facts(trace_valid=False), _facts(operational_reliable=False)):
        result = terminal_result(facts, "cases-only-v1")
        assert result["accepted"] is False
        assert exit_code(result) == 2, facts
    assert terminal_result(_facts(finalization_valid=False), "cases-only-v1")["reason_code"] == "FINALIZATION_INVALID"


def test_r12_controller_error_parameter_follows_the_contract(pack_root: Path) -> None:
    from tests.test_exit_policy import _facts
    from tools.pilot_state import exit_code, terminal_result

    valid = terminal_result(_facts(), "cases-only-v1")
    assert exit_code(valid, controller_error=True) == 2
    assert exit_code(None) == 2
    contract = json.loads((pack_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    rules = [rule["when"] for rule in contract["exit_priority"]]
    assert rules.index("cases_only_fatal_invalid_closure_or_unreliable_evidence") < rules.index("valid_terminal_cases_only_v1")
    assert {rule["when"]: rule["code"] for rule in contract["exit_priority"]}["cases_only_fatal_invalid_closure_or_unreliable_evidence"] == 2


# --- B4: automation revision r2 after a failed compile/collect gate --------------------------------

def test_b4_regeneration_budget_allows_exactly_one_corrected_revision() -> None:
    check = pilot_state._regeneration_budget_error
    parent = {"attempt_id": "p" * 32}
    invalid = {"verification": "NOT_RUNNABLE", "reason_code": "GENERATED_TEST_INVALID"}
    r1_only = [{"event_type": "MODEL_REQUESTED", "attempt_id": "p" * 32, "stage_instance_id": "tc-to-autotest:r1"}]
    r1_and_r2 = [*r1_only, {"event_type": "MODEL_REQUESTED", "attempt_id": "p" * 32, "stage_instance_id": "tc-to-autotest:r2"}]

    assert check(parent, invalid, r1_only, "GENERATED_TEST_INVALID") is None
    # r1+r2 already spent by the static review of the parent attempt.
    assert "budget" in check(parent, invalid, r1_and_r2, "GENERATED_TEST_INVALID")
    # The parent is itself the corrected revision: no third generation.
    assert "budget" in check({**parent, "retry_reason": "GENERATED_TEST_INVALID"}, invalid, r1_only, "GENERATED_TEST_INVALID")
    # A failed product check is never regenerated.
    assert check(parent, {"verification": "FAIL", "reason_code": None}, r1_only, "GENERATED_TEST_INVALID") is not None
    assert check(parent, {"verification": "NOT_RUNNABLE", "reason_code": "LAUNCH_FAILED"}, r1_only, "GENERATED_TEST_INVALID") is not None
    assert check(parent, None, r1_only, "GENERATED_TEST_INVALID") is not None
    # The budget cannot be bypassed by naming another reason, and other retries are unaffected.
    assert check(parent, invalid, r1_only, "operator-retry") is not None
    assert check(parent, {"verification": "FAIL", "reason_code": None}, r1_only, "operator-retry") is None
    assert check(parent, None, r1_only, "operator-retry") is None
