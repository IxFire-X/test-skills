import json
import os
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from tools import pilot_state
from tools import confined_output
from tools.confined_output import OutputConfinementError, create_confined_bytes_exclusive
from tools.pilot_state import append_event, create_run, derive_state
from helpers import phase_two_baseline


AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _sealed(value: dict) -> dict:
    value = dict(value)
    value["digest"] = pilot_state._digest(value)
    return value


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _module_digest(root: Path) -> str:
    return pilot_state.module_selection_digest(root.parent.parent, ".")


def _baseline(root: Path) -> dict:
    return phase_two_baseline(root, root.parent.parent, {"module": "."})


def _receipt_schema() -> dict:
    return json.loads((Path(__file__).parents[1] / "schemas" / "run-authorization-receipt.schema.json").read_text(encoding="utf-8"))


def _append_synthetic_terminal(
    root: Path, attempt_id: str, digest: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test-only seam for PROCESS_STOPPED behaviour, independent of Phase 7 closure."""
    with monkeypatch.context() as gate:
        gate.setattr(pilot_state, "_validate_terminal_transition", lambda *_args: None)
        append_event(root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=attempt_id, artifact_digest=digest)


def _append_synthetic_unknown(root: Path, attempt_id: str, digest: str) -> None:
    """Test only the reducer's post-terminal recovery rule, not UNKNOWN admission."""
    project, resolved = pilot_state._run_root(root)
    pilot_state._append_event(
        project, resolved, "EXECUTION_UNKNOWN", actor="controller", attempt_id=attempt_id,
        batch_id=None, artifact_digest=digest,
    )


def test_run_publishes_schema_valid_bound_artifacts_and_event_counterparts(tmp_path: Path) -> None:
    run = create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((root / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    event_bytes = (root / "events" / "0000000001.json").read_bytes()
    assert manifest["run_id"] == receipt["run_id"] == root.name
    assert manifest["authorization_digest"] == receipt["digest"]
    assert event_bytes == (root / "events.jsonl").read_bytes()
    for name, value in (("run-manifest.schema.json", manifest), ("run-authorization-receipt.schema.json", receipt)):
        schema = json.loads((Path(__file__).parents[1] / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(value)


def test_authorization_rejects_unsafe_or_wrong_policy_before_run_directory_mutation(tmp_path: Path) -> None:
    invalid = [
        {"request_id": "", "execution_requested": False},
        {"request_id": "has whitespace", "execution_requested": False},
        {"request_id": "token-secret", "execution_requested": False},
        {"request_id": "request-1", "execution_requested": False, "prompt": "secret"},
    ]
    for authorization in invalid:
        with pytest.raises(ValueError, match="unsafe authorization"):
            create_run(tmp_path, "cases-only-v1", authorization)
        assert not (tmp_path / ".pilot-runs").exists()
    with pytest.raises(ValueError, match="policy"):
        create_run(tmp_path, "local-pilot-v1", AUTHORIZATION)
    assert not (tmp_path / ".pilot-runs").exists()


def test_authorization_rejects_missing_nonstring_multiline_overlong_and_host_token_values(tmp_path: Path) -> None:
    invalid = [
        {}, {"request_id": 1, "execution_requested": False}, {"request_id": "line\nbreak", "execution_requested": False},
        {"request_id": "x" * 65, "execution_requested": False}, {"request_id": "ghp_abcdefghijklmnopqrstuvwxyz", "execution_requested": False}, {"request_id": "request-1", "execution_requested": False, "host_id": "bearer-value"},
    ]
    for authorization in invalid:
        with pytest.raises(ValueError, match="unsafe authorization"):
            create_run(tmp_path, "cases-only-v1", authorization)
        assert not (tmp_path / ".pilot-runs").exists()
    assert create_run(tmp_path, "local-pilot-v1", {"request_id": "request-2", "host_id": "host-1", "execution_requested": True})["authorization"]["execution_requested"] is True


def test_event_journal_requires_exact_counterparts_and_detects_truncation_or_rewrite(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    event = append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    assert append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root)) == event
    assert append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=DIGEST_B)["seq"] == 3
    journal = root / "events.jsonl"
    journal.write_bytes(journal.read_bytes().rsplit(b"\n", 2)[0] + b"\n")
    event_path = root / "events" / "0000000003.json"
    event_path.write_bytes(event_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)


def test_event_file_rewrite_and_duplicate_key_fail_closed(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    event_path = root / "events" / "0000000001.json"
    event_path.write_bytes(event_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)


def test_event_counterpart_deletion_rewrite_and_rejected_transition_allocate_nothing(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    assert append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=DIGEST_A)["seq"] == 3
    assert append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=DIGEST_B)["seq"] == 4
    before = (root / "events.jsonl").read_bytes()
    with pytest.raises(ValueError, match="invalid event"):
        append_event(root, "INVENTED", actor="controller", artifact_digest=DIGEST_A)
    assert (root / "events.jsonl").read_bytes() == before
    (root / "events" / "0000000004.json").unlink()
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)


def test_run_boundary_replay_rejects_resealed_receipt_and_wrong_attempt_reference(tmp_path: Path) -> None:
    run = create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    receipt = json.loads((root / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    receipt["execution_requested"] = True
    receipt["digest"] = pilot_state._digest({key: value for key, value in receipt.items() if key != "digest"})
    (root / "run-authorization-receipt.json").write_bytes(pilot_state._canonical_bytes(receipt))
    with pytest.raises(ValueError, match="schema validation|run boundary"):
        derive_state(root)


def test_publication_failure_leaves_no_partial_run_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> bool:
        raise OSError("simulated")

    monkeypatch.setattr(pilot_state, "create_confined_bytes_exclusive", fail)
    with pytest.raises(OSError):
        create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    assert not list(tmp_path.glob(".pilot-runs/*/run-manifest.json"))


def test_authorization_runtime_and_schema_reject_token_shapes_before_mutation(tmp_path: Path) -> None:
    schema = _receipt_schema()
    labels = [
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        "contains-secret-marker",
        "Az1Bc2Cd3De4Ef5Fg6Gh7Hi8Ij9Jk0Lm",
    ]
    for label in labels:
        receipt = _sealed({"schema_version": "1.0.0", "run_id": "a" * 32, "request_id": label, "policy_profile": "cases-only-v1", "execution_requested": False})
        assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(receipt))
        with pytest.raises(ValueError, match="unsafe authorization"):
            create_run(tmp_path, "cases-only-v1", {"request_id": label, "execution_requested": False})
        assert not (tmp_path / ".pilot-runs").exists()


def test_replay_revalidates_all_run_boundary_bindings(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    for filename in ("run-manifest.json", "run-authorization-receipt.json"):
        moved = root / filename
        moved.rename(root / f"{filename}.gone")
        with pytest.raises(ValueError, match="missing|boundary"):
            derive_state(root)
        (root / f"{filename}.gone").rename(moved)
    receipt_path = root / "run-authorization-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["request_id"] = "request-2"
    receipt = _sealed({key: value for key, value in receipt.items() if key != "digest"})
    receipt_path.write_bytes(pilot_state._canonical_bytes(receipt))
    with pytest.raises(ValueError, match="boundary"):
        derive_state(root)
    second_project = tmp_path / "second"
    second_project.mkdir()
    second = Path(create_run(second_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    manifest_path = second / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy_profile"] = "local-pilot-v1"
    manifest_path.write_bytes(pilot_state._canonical_bytes(_sealed({key: value for key, value in manifest.items() if key != "digest"})))
    with pytest.raises(ValueError, match="boundary"):
        derive_state(second)
    copied_project = tmp_path / "copied"
    copied_root = copied_project / ".pilot-runs" / root.name
    copied_root.parent.mkdir(parents=True)
    shutil.copytree(root, copied_root)
    with pytest.raises(ValueError, match="boundary"):
        derive_state(copied_root)
    third_project = tmp_path / "third"
    third_project.mkdir()
    third = Path(create_run(third_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    moved = third.parent / ("f" * 32)
    third.rename(moved)
    with pytest.raises(ValueError, match="boundary"):
        derive_state(moved)
    fourth_project = tmp_path / "fourth"
    fourth_project.mkdir()
    fourth = Path(create_run(fourth_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    event_path = fourth / "events" / "0000000001.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["artifact_digest"] = DIGEST_A
    event = _sealed({key: value for key, value in event.items() if key != "digest"})
    event_path.write_bytes(pilot_state._canonical_bytes(event))
    (fourth / "events.jsonl").write_bytes(event_path.read_bytes())
    with pytest.raises(ValueError, match="event journal"):
        derive_state(fourth)


def test_event_append_rejects_fabricated_and_out_of_order_attempt_evidence_without_allocation(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    attempt = _sealed({"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "a" * 32, "project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": DIGEST_A})
    (root / "attempts").mkdir()
    path = root / "attempts" / f"{attempt['attempt_id']}.json"
    path.write_bytes(pilot_state._canonical_bytes(attempt))
    before = _snapshot(root)
    for event_type, attempt_id in (("WAITING_FOR_MODEL", None), ("ATTEMPT_TERMINAL", "b" * 32), ("ATTEMPT_CREATED", attempt["attempt_id"])):
        with pytest.raises(ValueError):
            append_event(root, event_type, actor="controller", attempt_id=attempt_id, artifact_digest=attempt["digest"])
        assert _snapshot(root) == before


def test_model_lifecycle_events_are_stage_bound_and_ordered(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    attempt = pilot_state.create_attempt(
        root,
        {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
        _baseline(root),
    )
    attempt_id = attempt["attempt_id"]
    append_event(
        root, "CONTEXT_SELECTED", actor="controller", attempt_id=attempt_id,
        batch_id="review-0001", artifact_digest=DIGEST_A,
    )
    before = _snapshot(root)
    with pytest.raises(ValueError, match="publish_model_request"):
        append_event(
            root, "MODEL_REQUESTED", actor="controller", attempt_id=attempt_id,
            batch_id="BATCH-fixture", stage_instance_id="tc-generator:BATCH-fixture",
            artifact_digest=DIGEST_A,
        )
    assert _snapshot(root) == before
    with pytest.raises(ValueError, match="model response"):
        append_event(
            root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
            batch_id="BATCH-fixture", stage_instance_id="tc-generator:BATCH-fixture",
            artifact_digest=DIGEST_B,
        )
    assert _snapshot(root) == before

    for model_id, invocation_id in (
        ("sk-live-secret", "invoke-generator"),
        ("api-key-live", "invoke-generator"),
        ("model-generator", "AKIAIOSFODNN7EXAMPLE"),
    ):
        with pytest.raises(ValueError, match="invalid model request"):
            pilot_state.publish_model_request(
                root, attempt_id, "tc-generator:BATCH-fixture",
                model_id=model_id, invocation_id=invocation_id,
                input_digests=[DIGEST_A],
            )
        assert _snapshot(root) == before

    pilot_state.publish_model_request(
        root, attempt_id, "context-marker:baseline",
        model_id="model-context", invocation_id="invoke-context",
        input_digests=[DIGEST_A],
    )
    append_event(
        root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
        stage_instance_id="context-marker:baseline", artifact_digest=DIGEST_A,
    )
    pilot_state.publish_model_request(
        root, attempt_id, "tc-generator:BATCH-fixture",
        model_id="model-generator", invocation_id="invoke-generator",
        input_digests=[DIGEST_A],
    )
    append_event(
        root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
        batch_id="BATCH-fixture", stage_instance_id="tc-generator:BATCH-fixture",
        artifact_digest=DIGEST_B, transport_attempts=2,
    )
    append_event(
        root, "CANDIDATE_PUBLISHED", actor="controller", attempt_id=attempt_id,
        batch_id="BATCH-fixture", stage_instance_id="tc-generator:BATCH-fixture",
        artifact_digest=DIGEST_B,
    )
    append_event(
        root, "CANDIDATE_PUBLISHED", actor="controller", attempt_id=attempt_id,
        stage_instance_id="assembly", artifact_digest=DIGEST_A,
    )
    append_event(
        root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id,
        stage_instance_id="tc-reviewer:canonical", artifact_digest=DIGEST_A,
    )
    pilot_state.publish_model_request(
        root, attempt_id, "tc-reviewer:canonical",
        model_id="model-reviewer", invocation_id="invoke-reviewer",
        input_digests=[DIGEST_A],
    )
    append_event(
        root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
        stage_instance_id="tc-reviewer:canonical", artifact_digest=DIGEST_B,
    )
    assert [
        event["event_type"] for event in derive_state(root)["events"][-7:]
    ] == [
        "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED",
        "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED", "MODEL_REQUESTED",
        "MODEL_RESPONSE_RECEIVED",
    ]
    assert derive_state(root)["events"][-6]["transport_attempts"] == 2


def test_direct_terminal_event_requires_completed_phase_seven_closure(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    attempt = pilot_state.create_attempt(
        root,
        {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
        _baseline(root),
    )
    with pytest.raises(ValueError, match="terminal result"):
        append_event(root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST_A)


def test_process_stopped_requires_verified_recovery_without_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    attempt = create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(root))
    _append_synthetic_unknown(root, attempt["attempt_id"], DIGEST_A)
    _append_synthetic_terminal(root, attempt["attempt_id"], DIGEST_A, monkeypatch)
    before = _snapshot(root)
    with pytest.raises(ValueError, match="verified process-scope"):
        append_event(root, "PROCESS_STOPPED", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST_A)
    assert _snapshot(root) == before


def test_ordered_state_reducer_uses_latest_valid_transition(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    attempt = create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(root))
    append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST_A)
    append_event(root, "WAITING_FOR_INPUT", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=DIGEST_A)
    assert derive_state(root)["attempts"][0]["state"] == "WAITING_FOR_INPUT"


def test_event_pair_failure_rolls_back_and_retry_reuses_next_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    before = _snapshot(root)
    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", lambda *_args: (_ for _ in ()).throw(OSError("journal")))
    with pytest.raises(ValueError, match="event publication"):
        append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    assert _snapshot(root) == before
    monkeypatch.undo()
    assert append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))["seq"] == 2
    previous = json.loads((root / "events" / "0000000002.json").read_text(encoding="utf-8"))
    orphan = _sealed({"schema_version": "1.0.0", "seq": 3, "event_type": "ARTIFACT_PUBLISHED", "run_id": previous["run_id"], "actor": "controller", "observed_at": "2026-08-26T00:00:00.000000Z", "prev_digest": previous["digest"], "artifact_digest": DIGEST_B})
    orphan_path = root / "events" / "0000000003.json"
    orphan_data = pilot_state._canonical_bytes(orphan)
    orphan_path.write_bytes(orphan_data)
    marker_path = root / "pending-events" / "0000000003.json"
    marker_path.parent.mkdir(exist_ok=True)
    marker_path.write_bytes(pilot_state._canonical_bytes(pilot_state._pending_event_value(orphan, orphan_data, (root / "events.jsonl").read_bytes())))
    derive_state(root)
    assert not orphan_path.exists()
    assert not marker_path.exists()


def test_failed_run_creation_leaves_no_accepted_partial_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", lambda *_args: (_ for _ in ()).throw(OSError("journal")))
    with pytest.raises(ValueError, match="event publication"):
        create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    assert not list(tmp_path.rglob("run-manifest.json"))
    assert not list(tmp_path.rglob("run-authorization-receipt.json"))
    assert not list(tmp_path.rglob("events.jsonl"))


def test_immutable_publish_is_temp_fsync_then_atomic_no_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "value.json"
    observed: list[bool] = []
    real_fsync = os.fsync

    def observe(descriptor: int) -> None:
        observed.append(target.exists())
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", observe)
    assert create_confined_bytes_exclusive(tmp_path, root, target, b"one\n") is True
    assert observed == [False]
    assert create_confined_bytes_exclusive(tmp_path, root, target, b"one\n") is False
    with pytest.raises(Exception):
        create_confined_bytes_exclusive(tmp_path, root, target, b"two\n")
    assert target.read_bytes() == b"one\n"


def test_run_and_module_reparse_components_never_escape(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt

    unsafe_runs = tmp_path / ".pilot-runs"
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        subprocess = __import__("subprocess")
        junction = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(unsafe_runs), str(outside)], check=False, capture_output=True, text=True)
        assert junction.returncode == 0, junction.stdout + junction.stderr
    else:
        unsafe_runs.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="unsafe run root"):
            create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
        assert not list(outside.iterdir())
    finally:
        if os.name == "nt":
            os.rmdir(unsafe_runs)
        else:
            unsafe_runs.unlink()
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    (tmp_path / "real").mkdir()
    link = tmp_path / "link"
    if os.name == "nt":
        subprocess = __import__("subprocess")
        junction = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(tmp_path / "real")], check=False, capture_output=True, text=True)
        assert junction.returncode == 0, junction.stdout + junction.stderr
    else:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="module"):
            create_attempt(root, {"project": str(tmp_path.resolve()), "module": "link", "policy_profile": "cases-only-v1"}, {"digest": DIGEST_A})
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()
    (tmp_path / "not-a-module").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="module"):
        create_attempt(root, {"project": str(tmp_path.resolve()), "module": "not-a-module", "policy_profile": "cases-only-v1"}, {"digest": DIGEST_A})


def test_duplicate_key_documents_really_fail_strict_replay(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    event = root / "events" / "0000000001.json"
    event.write_bytes(event.read_bytes().replace(b'"seq":1', b'"seq":1,"seq":1'))
    (root / "events.jsonl").write_bytes(event.read_bytes())
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    from tools.pilot_state import create_attempt

    second_project = tmp_path / "second"
    second_project.mkdir()
    second = Path(create_run(second_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(second, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(second))
    attempt = create_attempt(second, {"project": str(second_project.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(second))
    attempt_path = second / "attempts" / f"{attempt['attempt_id']}.json"
    attempt_path.write_bytes(attempt_path.read_bytes().replace(b'"module":"."', b'"module":".","module":"."'))
    with pytest.raises(ValueError, match="attempt"):
        derive_state(second)


def test_real_artifacts_validate_with_format_checker(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(root))
    names = {"run-manifest.json": "run-manifest.schema.json", "run-authorization-receipt.json": "run-authorization-receipt.schema.json"}
    for filename, schema_name in names.items():
        value = json.loads((root / filename).read_text(encoding="utf-8"))
        schema = json.loads((Path(__file__).parents[1] / "schemas" / schema_name).read_text(encoding="utf-8"))
        assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "event.schema.json").read_text(encoding="utf-8"))
    for event_path in sorted((root / "events").glob("*.json")):
        event = json.loads(event_path.read_text(encoding="utf-8"))
        assert not list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event))
    event = json.loads((root / "events" / "0000000001.json").read_text(encoding="utf-8"))
    event["observed_at"] = "not-a-date"
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event))
    event = json.loads((root / "events" / "0000000002.json").read_text(encoding="utf-8"))
    event["prev_digest"] = None
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event))
    attempt_event = next(path for path in sorted((root / "events").glob("*.json")) if json.loads(path.read_text(encoding="utf-8"))["event_type"] == "ATTEMPT_CREATED")
    event = json.loads(attempt_event.read_text(encoding="utf-8"))
    event.pop("attempt_id")
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event))
    attempt_schema = json.loads((Path(__file__).parents[1] / "schemas" / "attempt.schema.json").read_text(encoding="utf-8"))
    for attempt_path in (root / "attempts").glob("*.json"):
        assert not list(Draft202012Validator(attempt_schema, format_checker=FormatChecker()).iter_errors(json.loads(attempt_path.read_text(encoding="utf-8"))))


def test_event_authority_rejects_second_active_attempt_and_invalid_lineage(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    first = create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(root))
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    candidate = pilot_state._publish(tmp_path, root, root / "attempts" / f"{'b' * 32}.json", {"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "b" * 32, "project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": DIGEST_A}, "attempt")
    before = _snapshot(root)
    with pytest.raises(ValueError):
        append_event(root, "ATTEMPT_CREATED", actor="controller", attempt_id=candidate["attempt_id"], artifact_digest=candidate["digest"])
    assert _snapshot(root) == before
    assert first["attempt_id"] != candidate["attempt_id"]


def test_replay_rejects_resealed_attempt_identity_and_two_nonterminal_attempts(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    first = create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, _baseline(root))
    manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
    second = pilot_state._publish(tmp_path, root, root / "attempts" / f"{'b' * 32}.json", {"schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "b" * 32, "project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1", "baseline_digest": DIGEST_A, "parent_attempt_id": first["attempt_id"], "retry_reason": "retry-1"}, "attempt")
    prior = json.loads((root / "events" / "0000000003.json").read_text(encoding="utf-8"))
    event = _sealed({"schema_version": "1.0.0", "seq": 4, "event_type": "ATTEMPT_CREATED", "run_id": manifest["run_id"], "actor": "controller", "observed_at": "2026-08-26T00:00:00.000000Z", "prev_digest": prior["digest"], "attempt_id": second["attempt_id"], "artifact_digest": second["digest"]})
    data = pilot_state._canonical_bytes(event)
    (root / "events" / "0000000004.json").write_bytes(data)
    (root / "events.jsonl").write_bytes((root / "events.jsonl").read_bytes() + data)
    with pytest.raises(ValueError):
        derive_state(root)
    assert first["attempt_id"] != second["attempt_id"]


def test_uncommitted_attempt_recovery_preserves_every_conflict(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt
    cases = (
        "filename", "attempt_id", "module", "baseline", "project", "policy",
        "parent", "retry", "unsafe_retry", "raw_bytes", "digest", "multiple",
    )
    for case in cases:
        project = tmp_path / case
        project.mkdir()
        (project / "other").mkdir()
        root = Path(create_run(project, "cases-only-v1", AUTHORIZATION)["run_root"])
        append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
        manifest = json.loads((root / "run-manifest.json").read_text(encoding="utf-8"))
        target = root / "attempts" / f"{'b' * 32}.json"
        orphan = pilot_state._publish(project, root, target, {
            "schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "b" * 32,
            "project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1",
            "baseline_digest": DIGEST_A,
        }, "attempt")
        if case == "filename":
            target.rename(root / "attempts" / f"{'c' * 32}.json")
        elif case == "raw_bytes":
            target.write_bytes(target.read_bytes() + b" ")
        elif case == "digest":
            target.write_bytes(target.read_bytes().replace(b'"digest":"sha256:', b'"digest":"sha256:0', 1))
        elif case == "multiple":
            pilot_state._publish(project, root, root / "attempts" / f"{'c' * 32}.json", {
                "schema_version": "1.0.0", "run_id": manifest["run_id"], "attempt_id": "c" * 32,
                "project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1",
                "baseline_digest": DIGEST_A,
            }, "attempt")
        else:
            changed = json.loads(target.read_text(encoding="utf-8"))
            changed.pop("digest")
            changes = {
                "attempt_id": {"attempt_id": "c" * 32}, "module": {"module": "other"},
                "baseline": {"baseline_digest": DIGEST_B}, "project": {"project": str(tmp_path.resolve())},
                "policy": {"policy_profile": "local-pilot-v1"},
                "parent": {"parent_attempt_id": "a" * 32, "retry_reason": "retry-1"},
                "retry": {"parent_attempt_id": "a" * 32, "retry_reason": "retry-2"},
                "unsafe_retry": {"parent_attempt_id": "a" * 32, "retry_reason": "token-value"},
            }
            changed.update(changes[case])
            target.write_bytes(pilot_state._canonical_bytes(_sealed(changed)))
        before = _snapshot(root)
        with pytest.raises(ValueError):
            create_attempt(root, {"project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, {"digest": DIGEST_A})
        assert _snapshot(root) == before, case
        assert orphan["attempt_id"] == "b" * 32


def test_recovery_distinguishes_pending_publication_from_committed_truncation(tmp_path: Path) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    journal = root / "events.jsonl"
    counterpart = root / "events" / "0000000002.json"
    journal.write_bytes(journal.read_bytes().splitlines(keepends=True)[0])
    before = _snapshot(root)
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    assert _snapshot(root) == before
    assert counterpart.exists()
    before_project = tmp_path / "before-no-counterpart"
    before_project.mkdir()
    before_root = Path(create_run(before_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    prior = (before_root / "events.jsonl").read_bytes()
    first = json.loads((before_root / "events" / "0000000001.json").read_text(encoding="utf-8"))
    future = _sealed({"schema_version": "1.0.0", "seq": 2, "event_type": "MODULE_SELECTED", "run_id": first["run_id"], "actor": "controller", "observed_at": "2026-08-26T00:00:00.000000Z", "prev_digest": first["digest"], "artifact_digest": DIGEST_A})
    future_data = pilot_state._canonical_bytes(future)
    marker = before_root / "pending-events" / "0000000002.json"
    marker.parent.mkdir(exist_ok=True)
    marker.write_bytes(pilot_state._canonical_bytes(pilot_state._pending_event_value(future, future_data, prior)))
    assert derive_state(before_root)["events"][-1]["seq"] == 1
    assert not marker.exists()
    after_project = tmp_path / "after-counterpart"
    after_project.mkdir()
    after_root = Path(create_run(after_project, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(after_root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(after_root))
    after_event = json.loads((after_root / "events" / "0000000002.json").read_text(encoding="utf-8"))
    after_data = pilot_state._canonical_bytes(after_event)
    after_marker = after_root / "pending-events" / "0000000002.json"
    after_marker.write_bytes(pilot_state._canonical_bytes(pilot_state._pending_event_value(after_event, after_data, (after_root / "events.jsonl").read_bytes().rsplit(after_data, 1)[0])))
    assert derive_state(after_root)["events"][-1]["seq"] == 2
    assert not after_marker.exists()
    mismatches = {
        "run_id": "0" * 32,
        "seq": 3,
        "prior_digest": DIGEST_A,
        "prior_bytes_digest": DIGEST_A,
        "prior_bytes_length": 1,
        "event_path": "events/0000000009.json",
        "event_digest": DIGEST_B,
        "bytes_digest": DIGEST_B,
        "bytes_length": 1,
    }
    for field, value in mismatches.items():
        project = tmp_path / f"marker-{field}"
        project.mkdir()
        marker_root = Path(create_run(project, "cases-only-v1", AUTHORIZATION)["run_root"])
        future, future_data = _next_module_event(marker_root)
        marker, _marker_data = _pending_marker(marker_root, future, (marker_root / "events.jsonl").read_bytes())
        (marker_root / "events" / "0000000002.json").write_bytes(future_data)
        changed = json.loads(marker.read_text(encoding="utf-8"))
        changed.pop("digest")
        changed[field] = value
        marker.write_bytes(pilot_state._canonical_bytes(_sealed(changed)))
        before = _snapshot(marker_root)
        try:
            derive_state(marker_root)
        except ValueError as error:
            assert "event journal" in str(error), field
        else:
            pytest.fail(f"marker mismatch accepted: {field}")
        assert _snapshot(marker_root) == before, field
    for label, mutate in (("multiple", "multiple"), ("counterpart", "counterpart"), ("other", "other"), ("ahead", "ahead")):
        project = tmp_path / f"marker-{label}"
        project.mkdir()
        marker_root = Path(create_run(project, "cases-only-v1", AUTHORIZATION)["run_root"])
        future, future_data = _next_module_event(marker_root)
        marker, marker_data = _pending_marker(marker_root, future, (marker_root / "events.jsonl").read_bytes())
        if mutate == "multiple":
            (marker.parent / "0000000003.json").write_bytes(marker_data)
        elif mutate == "counterpart":
            (marker_root / "events" / "0000000002.json").write_bytes(b"mismatch\n")
        elif mutate == "other":
            (marker_root / "events.jsonl").write_bytes(b"other\n")
        else:
            append_event(marker_root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(marker_root))
            append_event(marker_root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=DIGEST_B)
            marker, marker_data = _pending_marker(
                marker_root,
                future,
                (marker_root / "events.jsonl").read_bytes().splitlines(keepends=True)[0],
            )
        before = _snapshot(marker_root)
        try:
            derive_state(marker_root)
        except ValueError as error:
            assert "event journal" in str(error), label
        else:
            pytest.fail(f"marker anomaly accepted: {label}")
        assert _snapshot(marker_root) == before, label


def test_idempotent_event_revalidates_bound_artifact(tmp_path: Path) -> None:
    from tools.pilot_state import create_attempt
    for case in ("intact", "missing", "mutated", "resealed", "wrong_path"):
        project = tmp_path / case
        project.mkdir()
        root = Path(create_run(project, "cases-only-v1", AUTHORIZATION)["run_root"])
        append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
        attempt = create_attempt(root, {"project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1"}, phase_two_baseline(root, project, {"module": "."}))
        target = root / "attempts" / f"{attempt['attempt_id']}.json"
        if case == "missing":
            target.unlink()
        elif case == "mutated":
            target.write_bytes(target.read_bytes() + b" ")
        elif case == "resealed":
            changed = json.loads(target.read_text(encoding="utf-8"))
            changed.pop("digest")
            changed["baseline_digest"] = DIGEST_B
            target.write_bytes(pilot_state._canonical_bytes(_sealed(changed)))
        elif case == "wrong_path":
            target.rename(root / "attempts" / f"{'c' * 32}.json")
        before = _snapshot(root)
        if case == "intact":
            assert append_event(root, "ATTEMPT_CREATED", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=attempt["digest"])["seq"] == 6
            assert _snapshot(root) == before
        else:
            with pytest.raises(ValueError):
                append_event(root, "ATTEMPT_CREATED", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=attempt["digest"])
            assert _snapshot(root) == before, case


def test_atomic_no_replace_postinstall_failure_removes_only_owned_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "final.json"
    original = confined_output._read_regular_confined
    calls = 0

    def fail_postinstall(record: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            return None
        return original(record, **kwargs)

    monkeypatch.setattr(confined_output, "_read_regular_confined", fail_postinstall)
    with pytest.raises(Exception):
        create_confined_bytes_exclusive(tmp_path, root, target, b"owned\n")
    assert not target.exists()
    assert not list(root.glob("*.tmp"))


def _pending_marker(root: Path, event: dict, prior: bytes) -> tuple[Path, bytes]:
    """Install an exact sealed pending marker for recovery-window tests."""
    data = pilot_state._canonical_bytes(event)
    marker = root / "pending-events" / f"{event['seq']:010d}.json"
    marker.parent.mkdir(exist_ok=True)
    marker_data = pilot_state._canonical_bytes(pilot_state._pending_event_value(event, data, prior))
    marker.write_bytes(marker_data)
    return marker, marker_data


def _next_module_event(root: Path) -> tuple[dict, bytes]:
    first = json.loads((root / "events" / "0000000001.json").read_text(encoding="utf-8"))
    event = _sealed({
        "schema_version": "1.0.0",
        "seq": 2,
        "event_type": "MODULE_SELECTED",
        "run_id": first["run_id"],
        "actor": "controller",
        "observed_at": "2026-08-26T00:00:00.000000Z",
        "prev_digest": first["digest"],
        "artifact_digest": DIGEST_A,
    })
    return event, pilot_state._canonical_bytes(event)


def test_round4_marker_recovery_keeps_marker_last_across_cleanup_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable marker stays until its owned counterpart is gone."""
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    event, data = _next_module_event(root)
    marker, marker_data = _pending_marker(root, event, (root / "events.jsonl").read_bytes())
    counterpart = root / "events" / "0000000002.json"
    counterpart.write_bytes(data)
    original = pilot_state.remove_confined_bytes_if_equal

    def fail_counterpart(*args: object, **kwargs: object) -> bool:
        if Path(args[2]) == counterpart:
            return False
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "remove_confined_bytes_if_equal", fail_counterpart)
    before = _snapshot(root)
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    assert _snapshot(root) == before
    assert marker.read_bytes() == marker_data and counterpart.read_bytes() == data

    monkeypatch.setattr(pilot_state, "remove_confined_bytes_if_equal", original)

    def fail_marker(*args: object, **kwargs: object) -> bool:
        if Path(args[2]) == marker:
            return False
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "remove_confined_bytes_if_equal", fail_marker)
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    assert not counterpart.exists()
    assert marker.read_bytes() == marker_data
    monkeypatch.setattr(pilot_state, "remove_confined_bytes_if_equal", original)
    assert derive_state(root)["events"][-1]["seq"] == 1
    assert not marker.exists()


def test_round4_postwrite_commit_classification_preserves_attempt_and_run_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception after an exact journal commit is never an outer rollback cue."""
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    _baseline(root)
    original_writer = pilot_state.atomic_write_confined_bytes_at_root

    def commit_then_raise(*args: object, **kwargs: object) -> None:
        original_writer(*args, **kwargs)
        raise OSError("crash after journal replacement")

    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", commit_then_raise)
    attempt = create_attempt(
        root,
        {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
        _baseline(root),
    )
    assert (root / "attempts" / f"{attempt['attempt_id']}.json").exists()
    assert derive_state(root)["attempts"][0]["attempt_id"] == attempt["attempt_id"]

    project = tmp_path / "run-crash"
    project.mkdir()
    run = create_run(project, "cases-only-v1", AUTHORIZATION)
    run_dirs = list((project / ".pilot-runs").glob("*"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "run-manifest.json").exists()
    assert Path(run["run_root"]) == run_dirs[0]


def test_round4_unknown_postwrite_outcomes_preserve_all_available_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing counterpart and OTHER journal outcomes stay durable-unknown."""
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    baseline = _baseline(root)
    original_writer = pilot_state.atomic_write_confined_bytes_at_root

    def commit_mutate_then_raise(project: Path, confined: Path, target: Path, data: bytes) -> None:
        original_writer(project, confined, target, data)
        (root / "events" / "0000000006.json").write_bytes(b"replaced\n")
        raise OSError("counterpart replaced after journal commit")

    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", commit_mutate_then_raise)
    with pytest.raises(pilot_state._PublicationUnknown):
        create_attempt(
            root,
            {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
            baseline,
        )
    assert list((root / "attempts").glob("*.json"))
    assert (root / "events.jsonl").read_bytes().count(b"ATTEMPT_CREATED") == 1

    project = tmp_path / "other-journal"
    project.mkdir()

    def other_then_raise(project_root: Path, confined: Path, target: Path, data: bytes) -> None:
        original_writer(project_root, confined, target, b"other\n")
        raise OSError("unknown replacement")

    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", other_then_raise)
    with pytest.raises(pilot_state._PublicationUnknown):
        create_run(project, "cases-only-v1", AUTHORIZATION)
    run_dirs = list((project / ".pilot-runs").glob("*"))
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "run-manifest.json").exists()


def test_round4_create_once_counterpart_and_marker_races_do_not_mutate_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    original = pilot_state.create_confined_bytes_exclusive
    journal_before = (root / "events.jsonl").read_bytes()

    def marker_race(*args: object, **kwargs: object) -> bool:
        target = Path(args[2])
        if target.parent.name == "pending-events":
            return False
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "create_confined_bytes_exclusive", marker_race)
    with pytest.raises(ValueError, match="publication failed"):
        append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    assert (root / "events.jsonl").read_bytes() == journal_before

    monkeypatch.setattr(pilot_state, "create_confined_bytes_exclusive", original)

    def counterpart_race(*args: object, **kwargs: object) -> bool:
        target = Path(args[2])
        if target.parent.name == "events":
            return False
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "create_confined_bytes_exclusive", counterpart_race)
    with pytest.raises(ValueError, match="publication failed"):
        append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    assert (root / "events.jsonl").read_bytes() == journal_before


def test_round4_unreadable_final_counterpart_is_publication_unknown_not_attempt_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read fault after journal replacement cannot authorize deleting an attempt."""
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    original = pilot_state.read_confined_bytes
    target = root / "events" / "0000000006.json"

    def unreadable_event(*args: object, **kwargs: object) -> bytes | None:
        if Path(args[2]) == target:
            raise OutputConfinementError("injected final read fault")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "read_confined_bytes", unreadable_event)
    with pytest.raises(pilot_state._PublicationUnknown):
        create_attempt(
            root,
            {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
            _baseline(root),
        )
    assert list((root / "attempts").glob("*.json"))
    assert b"ATTEMPT_CREATED" in (root / "events.jsonl").read_bytes()


def test_round5_final_counterpart_failure_preserves_pending_marker_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The marker remains the recovery proof until both final reads are exact."""
    from tools.pilot_state import create_attempt

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=_module_digest(root))
    marker_snapshot: dict[str, tuple[bytes, int]] = {}
    original_create = pilot_state.create_confined_bytes_exclusive
    original_read = pilot_state.read_confined_bytes
    event_path = root / "events" / "0000000006.json"

    def record_marker(*args: object, **kwargs: object) -> bool:
        created = original_create(*args, **kwargs)
        target = Path(args[2])
        if target.parent.name == "pending-events" and created:
            marker_snapshot["marker"] = (target.read_bytes(), target.stat().st_mtime_ns)
        return created

    def fail_final_counterpart(*args: object, **kwargs: object) -> bytes | None:
        if Path(args[2]) == event_path:
            raise OutputConfinementError("injected final counterpart read failure")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "create_confined_bytes_exclusive", record_marker)
    monkeypatch.setattr(pilot_state, "read_confined_bytes", fail_final_counterpart)
    with pytest.raises(pilot_state._PublicationUnknown):
        create_attempt(
            root,
            {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"},
            _baseline(root),
        )
    marker = root / "pending-events" / "0000000006.json"
    assert marker_snapshot and marker.exists()
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == marker_snapshot["marker"]
    assert list((root / "attempts").glob("*.json"))
    assert b"ATTEMPT_CREATED" in (root / "events.jsonl").read_bytes()


def test_round5_temporary_replacement_is_preserved_and_fails_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathname swap after temp observation cannot be deleted as ours."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "final.json"
    original_stat = confined_output.os.stat
    swapped: dict[str, Path] = {}

    def swap_after_stat(path: object, *args: object, **kwargs: object):
        result = original_stat(path, *args, **kwargs)
        candidate = Path(path)
        if not swapped and candidate.name.endswith(".tmp"):
            if kwargs.get("dir_fd") is not None:
                candidate = root / candidate
            candidate.unlink()
            candidate.write_bytes(b"foreign\n")
            swapped["path"] = candidate
        return result

    monkeypatch.setattr(confined_output.os, "stat", swap_after_stat)
    with pytest.raises(OutputConfinementError, match="temporary cleanup"):
        create_confined_bytes_exclusive(tmp_path, root, target, b"owned\n")
    assert swapped["path"].read_bytes() == b"foreign\n"
    assert not target.exists()
