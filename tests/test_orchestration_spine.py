import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools import pilot_state
from tools.run_pipeline import resume_phase_one_spine, run_phase_one_spine
from helpers import build_phase_two_baseline, phase_two_baseline


DIGEST = "sha256:" + "a" * 64
AUTH = {"request_id": "request-1", "execution_requested": False}


def _identity(project: Path, **extra: object) -> dict:
    return {"project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1", **extra}


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}


def _run(project: Path) -> dict:
    identity = _identity(project)
    return dict(run_phase_one_spine(project, "cases-only-v1", AUTH, identity, build_phase_two_baseline(project, identity, skill_pack_root=project / ".pilot-runs"), {}))


def test_spine_persists_truthful_waiting_result_and_exact_event_order(tmp_path: Path) -> None:
    result = _run(tmp_path)
    events = result["state"]["events"]
    assert [event["event_type"] for event in events] == ["RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED", "ARTIFACT_READ_BACK", "WAITING_FOR_MODEL"]
    waiting = result["result"]["record"]
    assert waiting["attempt_state"] == "WAITING_FOR_MODEL"
    assert waiting["completion"] is waiting["verification"] is waiting["coverage"] is None
    assert "accepted" not in waiting and "reason_code" not in waiting
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(waiting)
    assert pilot_state.exit_code(waiting) == result["exit_code"] == 3
    assert result["artifact"]["digest"] == events[6]["artifact_digest"]
    assert result["result"]["digest"] == events[7]["artifact_digest"]
    assert result["exit_code"] == 3


def test_spine_rejects_supplied_terminal_facts_before_run_creation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="terminal facts"):
        run_phase_one_spine(tmp_path, "cases-only-v1", AUTH, _identity(tmp_path), {"digest": DIGEST}, {"attempt_state": "TERMINAL"})
    assert not (tmp_path / ".pilot-runs").exists()


def test_status_preserves_requirement_gaps_and_reports_stale_evidence_without_writes(tmp_path: Path) -> None:
    from tools.run_pipeline import _durable_status
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches
    from tests.test_requirement_traceability import canonical_fixture

    run = _run(tmp_path)
    root = Path(run["run"]["run_root"])
    attempt = run["state"]["attempts"][0]
    baseline = read_execution_baseline(root / "baselines" / (attempt["baseline_digest"][7:] + ".json"))
    inventory = read_inventory_receipt(root / "inventories" / (baseline["inventory_digest"][7:] + ".json"))
    context = pilot_state.publish_context_selection(root, attempt["attempt_id"], select_context_batches(inventory, tmp_path, [], byte_budget=65536)[0]["receipt"])
    gap = "spec.md:3 — missing: refusal outcome; blocks: negative case; question: which result?"
    marker = {"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {
        "analytics_documentation": {"requirements": canonical_fixture()["source_requirements"]},
        "source_code_and_diff": {"sources": ["fixture — authorized source"]},
    }, "warnings": [gap]}
    pilot_state.publish_model_request(root, attempt["attempt_id"], "context-marker:baseline", model_id="model-context", invocation_id="context-status", input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], context["digest"]])
    published = pilot_state.publish_model_stage_artifact(root, attempt["attempt_id"], "context-marker:baseline", marker)
    before = _tree(root)
    status = _durable_status(tmp_path, root, stage="status")
    assert status["last_confirmed_stage"] == "context-marker:baseline"
    assert status["next_expected_artifact"] == "generator fragments"
    assert status["warnings"] == [gap]
    assert status["warning_evidence_path"] == str(root / published["path"])
    assert _tree(root) == before

    (tmp_path / ".skillsrc").write_bytes((tmp_path / ".skillsrc").read_bytes() + b"\n")
    before = _tree(root)
    status = _durable_status(tmp_path, root, stage="status")
    assert status["stop_reason"] == "EVIDENCE_UNVERIFIED"
    assert status["evidence_errors"] and status["warnings"] == []
    assert status["next_expected_artifact"] == "validated inputs or child attempt"
    assert _tree(root) == before


def test_module_selected_is_bound_to_normalized_attempt_identity(tmp_path: Path) -> None:
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTH)
    root = Path(run["run_root"])
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest="sha256:" + "b" * 64)
    before = _tree(root)
    with pytest.raises(ValueError, match="module selection"):
        pilot_state.create_attempt(root, _identity(tmp_path), {"digest": DIGEST})
    assert _tree(root) == before


def test_spine_result_is_bound_to_run_attempt_and_profile(tmp_path: Path) -> None:
    result = _run(tmp_path)
    attempt = result["state"]["attempts"][0]
    record = result["result"]["record"]
    assert record["run_id"] == result["run"]["manifest"]["run_id"]
    assert record["attempt_id"] == attempt["attempt_id"]
    assert record["policy_profile"] == "cases-only-v1"
    assert result["result"]["bytes"] == (Path(result["run"]["run_root"]) / result["result"]["path"]).read_bytes()
    root = Path(result["run"]["run_root"])
    replay = pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    assert replay["created"] is False and replay["bytes"] == result["result"]["bytes"]
    with pytest.raises(ValueError):
        pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
            "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": "FULL",
        })


def test_spine_uses_confined_publishers_and_never_invokes_model_executor_or_project_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.run_pipeline as pipeline

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden dependency invoked")

    monkeypatch.setattr(pipeline, "_execute", forbidden)
    result = _run(tmp_path)
    assert result["exit_code"] == 3
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    spine = source[source.index("def run_phase_one_spine"):source.index("def build_parser")]
    assert ".write_bytes(" not in spine


def test_waiting_event_failure_leaves_no_false_waiting_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = pilot_state.append_event

    def fail_waiting(run_root: Path, event_type: str, **kwargs: object):
        if event_type == "WAITING_FOR_MODEL":
            raise OSError("injected waiting event failure")
        return original(run_root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", fail_waiting)
    with pytest.raises(OSError, match="waiting event"):
        _run(tmp_path)
    root = next((tmp_path / ".pilot-runs").glob("*"))
    assert not list((root / "structured-results").glob("*.json"))

    unknown_project = tmp_path / "unknown"
    unknown_project.mkdir()

    def unknown_waiting(run_root: Path, event_type: str, **kwargs: object):
        if event_type == "WAITING_FOR_MODEL":
            raise pilot_state._PublicationUnknown("injected unknown event state")
        return original(run_root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", unknown_waiting)
    with pytest.raises(pilot_state._PublicationUnknown):
        _run(unknown_project)
    unknown_root = next((unknown_project / ".pilot-runs").glob("*"))
    assert list((unknown_root / "structured-results").glob("*.json"))


def test_fresh_process_resume_is_recursive_byte_and_mtime_noop(tmp_path: Path) -> None:
    result = _run(tmp_path)
    root = Path(result["run"]["run_root"])
    before = _tree(root)
    code = (
        "from pathlib import Path; import json,sys; from tools.run_pipeline import resume_phase_one_spine; "
        "r=resume_phase_one_spine(Path(sys.argv[1])); print(json.dumps({'artifact':r['artifact']['digest'],'result':r['result']['digest'],'exit':r['exit_code']},sort_keys=True))"
    )
    output = subprocess.check_output([sys.executable, "-c", code, str(root)], cwd=Path(__file__).parents[1], text=True)
    assert json.loads(output) == {"artifact": result["artifact"]["digest"], "result": result["result"]["digest"], "exit": 3}
    assert _tree(root) == before


def test_resume_revalidates_declared_baseline_inputs(tmp_path: Path) -> None:
    result = _run(tmp_path)
    root = Path(result["run"]["run_root"])
    (tmp_path / ".skillsrc").write_text('{"version":"changed"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="execution baseline revalidation failed"):
        resume_phase_one_spine(root)


def test_nested_module_waiting_spine_resumes_from_the_exact_module(tmp_path: Path) -> None:
    module = tmp_path / "services" / "api"
    module.mkdir(parents=True)
    identity = {"project": str(tmp_path.resolve()), "module": "services/api", "policy_profile": "cases-only-v1"}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")

    result = run_phase_one_spine(tmp_path, "cases-only-v1", AUTH, identity, baseline, {})
    resumed = resume_phase_one_spine(Path(result["run"]["run_root"]))

    assert resumed["state"]["attempts"][0]["module"] == "services/api"
    assert resumed["artifact_digest"] == result["artifact_digest"]


def test_resume_rejects_mutated_artifact_result_or_binding(tmp_path: Path) -> None:
    for kind in ("artifact", "result", "binding"):
        project = tmp_path / kind
        project.mkdir()
        result = _run(project)
        root = Path(result["run"]["run_root"])
        if kind == "artifact":
            target = root / result["artifact"]["path"]
            target.write_bytes(target.read_bytes() + b" ")
        elif kind == "result":
            target = root / result["result"]["path"]
            target.write_bytes(target.read_bytes() + b" ")
        else:
            event_path = root / "events" / "0000000006.json"
            event = json.loads(event_path.read_text(encoding="utf-8"))
            event["artifact_digest"] = "sha256:" + "b" * 64
            event.pop("digest")
            event["digest"] = pilot_state._digest(event)
            data = pilot_state._canonical_bytes(event)
            event_path.write_bytes(data)
            lines = (root / "events.jsonl").read_bytes().splitlines(keepends=True)
            lines[5] = data
            (root / "events.jsonl").write_bytes(b"".join(lines))
        with pytest.raises(ValueError):
            resume_phase_one_spine(root)


def test_resume_rejects_resealed_phase1_artifact_semantic_mismatches_without_mutation(tmp_path: Path) -> None:
    for field, value in (("baseline_digest", "sha256:" + "b" * 64), ("module_selection_digest", "sha256:" + "c" * 64)):
        project = tmp_path / field
        project.mkdir()
        result = _run(project)
        root = Path(result["run"]["run_root"])
        artifact_path = root / result["artifact"]["path"]
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact.pop("digest")
        artifact[field] = value
        artifact["digest"] = pilot_state._digest(artifact)
        artifact_data = pilot_state._canonical_bytes(artifact)
        artifact_path.write_bytes(artifact_data)
        events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        artifact_event_index = next(index for index, event in enumerate(events) if event["event_type"] == "ARTIFACT_READ_BACK")
        waiting_event_index = next(index for index, event in enumerate(events) if event["event_type"] == "WAITING_FOR_MODEL")
        events[artifact_event_index]["artifact_digest"] = artifact["digest"]
        events[artifact_event_index].pop("digest")
        events[artifact_event_index]["digest"] = pilot_state._digest(events[artifact_event_index])
        events[waiting_event_index]["prev_digest"] = events[artifact_event_index]["digest"]
        events[waiting_event_index].pop("digest")
        events[waiting_event_index]["digest"] = pilot_state._digest(events[waiting_event_index])
        event_data = [pilot_state._canonical_bytes(event) for event in events]
        for index in (artifact_event_index, waiting_event_index):
            (root / "events" / f"{index + 1:010d}.json").write_bytes(event_data[index])
        (root / "events.jsonl").write_bytes(b"".join(event_data))
        before = _tree(root)
        with pytest.raises(ValueError, match="phase1 artifact"):
            resume_phase_one_spine(root)
        assert _tree(root) == before, field


def test_factual_receipt_semantic_rejection_and_public_readback_never_leave_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTH)
    root = Path(run["run_root"])
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    attempt = pilot_state.create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    artifact_target = root / "phase1-artifacts" / f"{attempt['attempt_id']}.json"
    with pytest.raises(ValueError, match="phase1 artifact"):
        pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "phase1-artifact", {
            "module_selection_digest": pilot_state.module_selection_digest(tmp_path, "."),
            "baseline_digest": "sha256:" + "b" * 64,
        })
    assert not artifact_target.exists()

    target = root / "structured-results" / f"{attempt['attempt_id']}.json"
    original_read = pilot_state.read_confined_bytes
    reads = 0

    def fail_redundant_public_read(*args: object, **kwargs: object) -> bytes | None:
        nonlocal reads
        if Path(args[2]) == target:
            reads += 1
            if reads > 1:
                return None
        return original_read(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "read_confined_bytes", fail_redundant_public_read)
    created = pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    assert reads == 1 and target.exists() and created["created"] is True

    monkeypatch.setattr(pilot_state, "read_confined_bytes", original_read)
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    replay = pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    assert replay["created"] is False
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    with pytest.raises(ValueError):
        pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
            "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": "FULL",
        })
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_factual_receipt_postcreate_readback_rollback_is_identity_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTH)
    root = Path(run["run_root"])
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    attempt = pilot_state.create_attempt(root, _identity(tmp_path), phase_two_baseline(root, tmp_path, _identity(tmp_path)))
    target = root / "structured-results" / f"{attempt['attempt_id']}.json"
    original_read = pilot_state.read_confined_bytes

    def fail_target(*args: object, **kwargs: object) -> bytes | None:
        if Path(args[2]) == target:
            return None
        return original_read(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "read_confined_bytes", fail_target)
    with pytest.raises(ValueError, match="read-back"):
        pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
            "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
        })
    assert not target.exists()

    monkeypatch.setattr(pilot_state, "read_confined_bytes", original_read)
    existing = pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    monkeypatch.setattr(pilot_state, "read_confined_bytes", fail_target)
    with pytest.raises(ValueError, match="read-back"):
        pilot_state.publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
            "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
        })
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert existing["created"] is True


def test_waiting_cleanup_drift_projects_bound_controller_error_without_waiting_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = pilot_state.append_event

    def drift_then_fail(run_root: Path, event_type: str, **kwargs: object):
        if event_type == "WAITING_FOR_MODEL":
            root = Path(run_root)
            attempt_id = str(kwargs["attempt_id"])
            target = root / "structured-results" / f"{attempt_id}.json"
            target.write_bytes(target.read_bytes() + b" ")
            raise OSError("injected ordinary waiting failure")
        return original(run_root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", drift_then_fail)
    outcome = _run(tmp_path)
    root = Path(outcome["run"]["run_root"])
    assert outcome["exit_code"] == 2
    assert outcome["result"] is None
    assert outcome["controller_error"]["run_id"] == outcome["run"]["manifest"]["run_id"]
    assert outcome["controller_error"]["attempt_id"] == outcome["state"]["attempts"][0]["attempt_id"]
    assert not any(event["event_type"] == "WAITING_FOR_MODEL" for event in outcome["state"]["events"])
    assert list((root / "structured-results").glob("*.json"))
