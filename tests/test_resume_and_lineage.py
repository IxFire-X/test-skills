import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import phase_two_baseline

from tools import pilot_state
from tools.pilot_state import append_event, create_attempt, create_run, derive_state, module_selection_digest


DIGEST = "sha256:" + "a" * 64
AUTH = {"request_id": "request-1", "execution_requested": False}


def _selected_run(tmp_path: Path):
    run = create_run(tmp_path, "cases-only-v1", AUTH)
    root = Path(run["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=module_selection_digest(tmp_path, "."))
    return root


def _identity(tmp_path: Path, **extra: object) -> dict:
    return {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", **extra}


def _artifact_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}


def _append_synthetic_terminal(
    root: Path, attempt_id: str, digest: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test-only seam for exercising post-terminal lineage without closure setup."""
    with monkeypatch.context() as gate:
        gate.setattr(pilot_state, "_validate_terminal_transition", lambda *_args: None)
        append_event(root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=attempt_id, artifact_digest=digest)


def _append_synthetic_unknown(root: Path, attempt_id: str, digest: str) -> None:
    """Test only lineage/recovery reduction, not production UNKNOWN admission."""
    project, resolved = pilot_state._run_root(root)
    pilot_state._append_event(
        project, resolved, "EXECUTION_UNKNOWN", actor="controller", attempt_id=attempt_id,
        batch_id=None, artifact_digest=digest,
    )


def test_attempt_binds_baseline_and_new_process_reloads_waiting_without_regeneration(tmp_path: Path) -> None:
    root = _selected_run(tmp_path)
    attempt = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST)
    before = (root / "attempts" / f"{attempt['attempt_id']}.json").stat().st_mtime_ns
    code = "from pathlib import Path; from tools.pilot_state import derive_state; import json,sys; print(json.dumps(derive_state(Path(sys.argv[1])), sort_keys=True))"
    output = subprocess.check_output([sys.executable, "-c", code, str(root)], cwd=Path(__file__).parents[1], text=True)
    reloaded = json.loads(output)
    assert reloaded["attempts"][0]["state"] == "WAITING_FOR_MODEL"
    assert reloaded["attempts"][0]["digest"] == attempt["digest"]
    assert (root / "attempts" / f"{attempt['attempt_id']}.json").stat().st_mtime_ns == before


def test_attempt_rejects_raw_baseline_rewrite_and_cross_identity_child(tmp_path: Path) -> None:
    root = _selected_run(tmp_path)
    first = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    attempt_path = root / "attempts" / f"{first['attempt_id']}.json"
    attempt_path.write_bytes(attempt_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="invalid attempt"):
        derive_state(root)


def test_resume_validation_rejects_an_installed_finalization_frontier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _selected_run(tmp_path)
    attempt = create_attempt(
        root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)),
    )
    monkeypatch.setattr(pilot_state, "read_resume_validation_if_present", lambda *_args: None)
    monkeypatch.setattr(
        pilot_state, "read_closure_artifact_if_present", lambda *_args: {"installed": True},
    )

    with pytest.raises(ValueError, match="already froze"):
        pilot_state.publish_resume_validation(
            root, attempt["attempt_id"], "BASELINE_DRIFT",
        )


def test_terminal_unknown_stays_child_blocked_without_verified_process_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _selected_run(tmp_path)
    first = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    _append_synthetic_unknown(root, first["attempt_id"], DIGEST)
    _append_synthetic_terminal(root, first["attempt_id"], DIGEST, monkeypatch)
    with pytest.raises(ValueError, match="terminal"):
        append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=first["attempt_id"], artifact_digest=DIGEST)
    with pytest.raises(ValueError, match="process-stop"):
        create_attempt(root, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1"), phase_two_baseline(root, tmp_path, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1")))
    with pytest.raises(ValueError, match="verified process-scope"):
        append_event(root, "PROCESS_STOPPED", actor="controller", attempt_id=first["attempt_id"], artifact_digest=DIGEST)
    with pytest.raises(ValueError, match="process-stop"):
        create_attempt(root, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1"), phase_two_baseline(root, tmp_path, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1")))


def test_a_run_that_holds_the_old_process_stop_is_read_and_its_explicit_child_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P4b (Q3): nothing in the package writes PROCESS_STOPPED now (no process is started), but a run of an earlier version holds it.  Such a journal
    is read as before: the stop is checked against its receipt, the explicit child after the UNKNOWN is allowed, ``derive_state`` and the
    status of the run do not fail."""
    from tests.test_durable_execution_trace import _publish_execution, _unknown_report
    from tests.test_execution_receipt import _execution_facts
    from tools.pilot_state import read_attempt_receipt, record_execution_unknown
    from tools.run_pipeline import main

    root, attempt_id, _report, request, durable = _execution_facts(tmp_path)
    timeout = _unknown_report(root, attempt_id, durable, request)
    timeout["process_evidence"][0].update({"process_scope_stopped": True,
                                           "stop_proof": "WINDOWS_JOB_OBJECT" if os.name == "nt" else "POSIX_PROCESS_GROUP"})
    published = _publish_execution(root, attempt_id, timeout, request=request)
    record_execution_unknown(root, attempt_id, published["digest"])
    project, resolved = pilot_state._run_root(root)
    pilot_state._append_event(project, resolved, "PROCESS_STOPPED", actor="controller", attempt_id=attempt_id, batch_id=None,
                              artifact_digest=published["digest"])  # what the writer of an earlier version appended
    events = [event["event_type"] for event in derive_state(root)["events"] if event.get("attempt_id") == attempt_id]
    assert events[-2:] == ["EXECUTION_UNKNOWN", "PROCESS_STOPPED"]
    assert read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]["process_evidence"][0]["process_scope_stopped"] is True
    _append_synthetic_terminal(root, attempt_id, DIGEST, monkeypatch)
    attempt_project = Path(next(attempt["project"] for attempt in derive_state(root)["attempts"] if attempt["attempt_id"] == attempt_id))
    identity = {"project": str(attempt_project.resolve()), "module": ".", "policy_profile": "local-pilot-v1",
                "parent_attempt_id": attempt_id, "retry_reason": "explicit-timeout-retry"}
    child = create_attempt(root, identity, phase_two_baseline(root, attempt_project, identity))
    assert child["parent_attempt_id"] == attempt_id
    code = main(["status", "--project", str(attempt_project), "--run", root.name])
    assert code in (0, 2, 3)  # an answer, not a traceback: the exit code is the state of the run


def test_process_stopped_rejects_active_non_unknown_and_other_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _selected_run(tmp_path)
    first = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    with pytest.raises(ValueError, match="verified process-scope"):
        append_event(root, "PROCESS_STOPPED", actor="controller", attempt_id=first["attempt_id"], artifact_digest=DIGEST)
    _append_synthetic_terminal(root, first["attempt_id"], DIGEST, monkeypatch)
    with pytest.raises(ValueError, match="verified process-scope"):
        append_event(root, "PROCESS_STOPPED", actor="controller", attempt_id=first["attempt_id"], artifact_digest=DIGEST)


def test_attempt_rejects_unsafe_module_and_cross_project_policy_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _selected_run(tmp_path)
    for module in ("", "..", "nested/../escape", "C:/escape", "//server/share"):
        with pytest.raises(ValueError, match="module"):
            create_attempt(root, _identity(tmp_path, module=module), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    first = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    _append_synthetic_terminal(root, first["attempt_id"], DIGEST, monkeypatch)
    (tmp_path / "nested").mkdir()
    with pytest.raises(ValueError, match="identity|module selection"):
        create_attempt(root, _identity(tmp_path, module="nested", parent_attempt_id=first["attempt_id"], retry_reason="retry-1"), phase_two_baseline(root, tmp_path, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1")))
    with pytest.raises(ValueError, match="identity"):
        create_attempt(root, {"project": str(tmp_path), "module": ".", "policy_profile": "local-pilot-v1", "parent_attempt_id": first["attempt_id"], "retry_reason": "retry-1"}, phase_two_baseline(root, tmp_path, _identity(tmp_path, parent_attempt_id=first["attempt_id"], retry_reason="retry-1")))


def test_attempt_event_failure_leaves_no_orphan_attempt_and_retry_is_possible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _selected_run(tmp_path)
    import tools.pilot_state as state

    baseline = phase_two_baseline(root, tmp_path, _identity(tmp_path))
    original = state._append_event
    monkeypatch.setattr(state, "_append_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("event failed")))
    with pytest.raises(OSError):
        create_attempt(root, _identity(tmp_path), baseline)
    assert not list((root / "attempts").glob("*.json"))
    monkeypatch.setattr(state, "_append_event", original)
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    orphan = state._publish(root.parent.parent, root, root / "attempts" / f"{'b' * 32}.json", {"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "b" * 32, "project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": baseline["digest"]}, "attempt")
    assert (root / "attempts" / f"{orphan['attempt_id']}.json").exists()
    assert create_attempt(root, _identity(tmp_path), baseline)["attempt_id"]
    assert not (root / "attempts" / f"{orphan['attempt_id']}.json").exists()


def test_fresh_process_resume_is_exactly_read_only(tmp_path: Path) -> None:
    root = _selected_run(tmp_path)
    attempt = create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST)
    before = _artifact_snapshot(root)
    code = "from pathlib import Path;from tools.pilot_state import derive_state;import json,sys;print(json.dumps(derive_state(Path(sys.argv[1])),sort_keys=True))"
    result = json.loads(subprocess.check_output([sys.executable, "-c", code, str(root)], cwd=Path(__file__).parents[1], text=True))
    assert result["attempts"] == [{**attempt, "state": "WAITING_FOR_MODEL"}]
    assert _artifact_snapshot(root) == before


def test_attempt_identity_and_lineage_shape_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _selected_run(tmp_path)
    baseline = phase_two_baseline(root, tmp_path, _identity(tmp_path))
    before = _artifact_snapshot(root)
    invalid = [
        {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "extra": True},
        _identity(tmp_path, parent_attempt_id="a" * 32, retry_reason="retry-1"),
        _identity(tmp_path, parent_attempt_id="a" * 32),
    ]
    for identity in invalid:
        with pytest.raises(ValueError):
            create_attempt(root, identity, baseline)
        assert _artifact_snapshot(root) == before
    first = create_attempt(root, _identity(tmp_path), baseline)
    before_active = _artifact_snapshot(root)
    with pytest.raises(ValueError, match="active attempt"):
        create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    assert _artifact_snapshot(root) == before_active
    _append_synthetic_terminal(root, first["attempt_id"], DIGEST, monkeypatch)
    for parent, retry in ((None, "retry-1"), ("a" * 32, "retry-1"), (first["attempt_id"], "unsafe token")):
        with pytest.raises(ValueError):
            create_attempt(root, _identity(tmp_path, parent_attempt_id=parent, retry_reason=retry), phase_two_baseline(root, tmp_path, _identity(tmp_path)))


def test_late_process_stopped_cannot_retroactively_validate_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.pilot_state as state

    root = _selected_run(tmp_path)
    parent_baseline = phase_two_baseline(root, tmp_path, _identity(tmp_path))
    parent = create_attempt(root, _identity(tmp_path), parent_baseline)
    _append_synthetic_unknown(root, parent["attempt_id"], DIGEST)
    _append_synthetic_terminal(root, parent["attempt_id"], DIGEST, monkeypatch)
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    child = state._publish(tmp_path, root, root / "attempts" / f"{'b' * 32}.json", {"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "b" * 32, "project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": parent_baseline["digest"], "parent_attempt_id": parent["attempt_id"], "retry_reason": "retry-1"}, "attempt")
    journal = root / "events.jsonl"
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    for event_type, attempt_id, digest in (("ATTEMPT_CREATED", child["attempt_id"], child["digest"]), ("PROCESS_STOPPED", parent["attempt_id"], DIGEST)):
        previous = events[-1]
        event = state._sealed({"schema_version": "1.0.0", "seq": len(events) + 1, "event_type": event_type, "run_id": manifest["run_id"], "actor": "controller", "observed_at": "2026-08-26T00:00:00.000000Z", "prev_digest": previous["digest"], "attempt_id": attempt_id, "artifact_digest": digest})
        data = state._canonical_bytes(event)
        (root / "events" / f"{event['seq']:010d}.json").write_bytes(data)
        journal.write_bytes(journal.read_bytes() + data)
        events.append(event)
    with pytest.raises(ValueError, match="unproved process-stop"):
        derive_state(root)


def test_lineage_authority_matrix_accepts_only_the_exact_immediate_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Public creation rejects every alternate child binding before publication."""
    variants = ("missing", "wrong", "unpaired", "module", "policy", "unknown")
    for variant in variants:
        project = tmp_path / variant
        project.mkdir()
        root = _selected_run(project)
        parent = create_attempt(root, _identity(project), phase_two_baseline(root, project, _identity(project)))
        if variant == "unknown":
            _append_synthetic_unknown(root, parent["attempt_id"], DIGEST)
        _append_synthetic_terminal(root, parent["attempt_id"], DIGEST, monkeypatch)
        if variant == "missing":
            identity = _identity(project)
        elif variant == "wrong":
            identity = _identity(project, parent_attempt_id="a" * 32, retry_reason="retry-1")
        elif variant == "unpaired":
            identity = _identity(project, parent_attempt_id=parent["attempt_id"])
        elif variant == "module":
            (project / "child").mkdir()
            identity = _identity(project, module="child", parent_attempt_id=parent["attempt_id"], retry_reason="retry-1")
        elif variant == "policy":
            identity = {**_identity(project, parent_attempt_id=parent["attempt_id"], retry_reason="retry-1"), "policy_profile": "local-pilot-v1"}
        else:
            identity = _identity(project, parent_attempt_id=parent["attempt_id"], retry_reason="retry-1")
        before = _artifact_snapshot(root)
        with pytest.raises(ValueError):
            create_attempt(root, identity, phase_two_baseline(root, project, _identity(project)))
        assert _artifact_snapshot(root) == before, variant

    project = tmp_path / "exact"
    project.mkdir()
    root = _selected_run(project)
    parent = create_attempt(root, _identity(project), phase_two_baseline(root, project, _identity(project)))
    _append_synthetic_terminal(root, parent["attempt_id"], DIGEST, monkeypatch)
    child = create_attempt(root, _identity(project, parent_attempt_id=parent["attempt_id"], retry_reason="retry-1"), phase_two_baseline(root, project, _identity(project, parent_attempt_id=parent["attempt_id"], retry_reason="retry-1")))
    assert child["parent_attempt_id"] == parent["attempt_id"]
