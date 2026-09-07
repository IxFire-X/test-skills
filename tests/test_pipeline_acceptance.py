import json
from pathlib import Path


def test_release_loader_uses_controller_owned_terminal_retry_receipt(
    tmp_path: Path, pack_root: Path,
):
    from evals.release_eval import _ValidatedRun, _derive_record, _digest, _load_scenario_suite
    from tests.test_compatibility_contract import _durable_evidence
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.compatibility_contract import evidence_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import (
        derive_state, read_attempt_receipt, read_closure_artifact_if_present,
        read_run, read_terminal_result,
    )
    from tools.release_manifest import build_release_manifest

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    manifest = build_release_manifest(pack_root)
    assert manifest["qualification"] == {"state": "implemented_unverified", "ready_tuple": None}
    compatibility = _durable_evidence(manifest, run_root, attempt_id)
    lifecycle_digests = [
        event["digest"] for event in derive_state(run_root)["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") in {
            "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED",
            "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED",
        }
    ]
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    retry = finalize_durable_execution_attempt(run_root, attempt_id)
    observation = retry["scenario_observation_receipts"]["terminal-idempotent-retry"]
    assert retry["idempotent"] is True
    assert retry["result"] == first["result"]
    events = derive_state(run_root)["events"]
    assert sum(1 for row in events if row.get("attempt_id") == attempt_id and row.get("event_type") == "EXECUTION_STARTED") == 1
    assert sum(1 for row in events if row.get("attempt_id") == attempt_id and row.get("event_type") == "ATTEMPT_TERMINAL") == 1
    binding = [
        row for row in events
        if row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
        and row.get("artifact_digest") == observation["digest"]
        and row.get("attempt_id") == attempt_id
    ]
    assert binding == [observation["binding_event"]]

    compatibility["digest"] = evidence_digest(compatibility)
    campaign = tmp_path / "campaign"
    (campaign / "evidence").mkdir(parents=True)
    (campaign / "evidence" / "compatibility.json").write_text(
        json.dumps(compatibility, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    terminal = read_terminal_result(run_root, attempt_id)
    finalization = read_closure_artifact_if_present(run_root, attempt_id, "finalization_receipt")
    execution = read_attempt_receipt(
        run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    snapshot = next(
        event for event in derive_state(run_root)["events"]
        if event["event_type"] == "SNAPSHOT_BOUND"
    )
    suite = _load_scenario_suite(pack_root / "evals" / "scenarios" / "pilot-critical.json")
    record = {
        "schema_version": "1.0.0",
        "campaign_id": "campaign-1",
        "suite_id": suite["suite_id"],
        "suite_digest": suite["digest"],
        "scenario_id": "terminal-idempotent-retry",
        "run_id": read_run(run_root)["manifest"]["run_id"],
        "attempt_id": attempt_id,
        "sequence": 1,
        "kind": "critical",
        "compatibility_evidence": {
            "path": "evidence/compatibility.json", "digest": compatibility["digest"],
        },
        "scenario_observation_receipt_digest": observation["digest"],
        "scenario_checks": [{
            "check_id": "terminal-retry-noop",
            "evidence_digests": list(dict.fromkeys([
                terminal["digest"], finalization["digest"], execution["digest"],
                snapshot["digest"], *lifecycle_digests, observation["digest"],
                observation["binding_event"]["digest"],
                *observation["record"]["event_digests"],
                *observation["record"]["receipt_digests"],
            ])),
        }],
    }
    record["digest"] = _digest(record)

    derived = _derive_record(
        record, campaign_dir=campaign, project=tmp_path / "project",
        manifest=manifest, suite=suite,
    )
    assert isinstance(derived, _ValidatedRun)
    assert derived.scenario_passed is True
    assert derived.independence == "verified"
    assert derived.tuple_complete is True
    assert derived.model_stage_evidence_complete is True
    assert derived.model_stage_models == {
        "context-marker": "model-context",
        "tc-generator": "model-generator",
        "tc-reviewer": "model-reviewer",
        "tc-to-autotest": "model-automation",
        "autotest-reviewer": "model-automation-reviewer",
    }
