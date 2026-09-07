import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from jsonschema import Draft202012Validator, ValidationError


CRITICAL_SCENARIOS = (
    "fresh-non-git",
    "dirty-git-preservation",
    "nested-module",
    "secret-exclusion",
    "waiting-for-model-resume",
    "one-user-question-resume",
    "reviewer-rejection",
    "review-context-limit",
    "materialization-failure",
    "execution-interruption-unknown",
    "terminal-idempotent-retry",
    "child-execution-retry",
    "retained-native-rerun",
)


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sealed(value):
    result = dict(value)
    result["digest"] = _digest(result)
    return result


def _event(sequence, event_type, *, attempt_id=None, artifact_digest=None):
    value = {
        "schema_version": "1.0.0",
        "seq": sequence,
        "event_type": event_type,
        "run_id": "b" * 32,
        "actor": "controller",
    }
    if attempt_id is not None:
        value["attempt_id"] = attempt_id
    if artifact_digest is not None:
        value["artifact_digest"] = artifact_digest
    return _sealed(value)


def _suite(critical_scenarios=("pilot-critical-v1",)):
    scenario_contracts = {
        "smoke": {"accepted": True, "verification": "PASS", "reason_code": None, "check_id": "accepted-terminal", "execution_required": True},
    }
    scenario_contracts.update({
        scenario: {"accepted": True, "verification": "PASS", "reason_code": None, "check_id": "accepted-terminal", "execution_required": True}
        for scenario in critical_scenarios
    })
    value = {
        "schema_version": "1.0.0",
        "suite_id": "pilot-critical-v1",
        "policy": "adaptive-1-3-5-v1",
        "smoke_scenario": "smoke",
        "critical_scenarios": list(critical_scenarios),
        "scenario_contracts": scenario_contracts,
        "stable_repetitions": 3,
        "escalated_repetitions": 5,
        "escalate_on": ["instability", "protocol_violation"],
        "protocol_violation_blocks_readiness": True,
    }
    value["digest"] = _digest(value)
    return value


def _asserted_record(sequence, *, campaign_id="campaign-1", scenario_id=None, kind=None, unstable=False, violations=(), execution="PASS", tuple_value=None, suite=None):
    suite = suite or _suite()
    kind = kind or ("smoke" if sequence == 1 else "critical")
    scenario_id = scenario_id or ("smoke" if kind == "smoke" else "pilot-critical-v1")
    tuple_value = tuple_value or {
        "skill_pack_version": "0.8.0", "skill_pack_digest": "sha256:" + "e" * 64,
        "compatibility_contract_version": "portable-cli-v1", "execution_profile_version": "v1",
        "cli_host": "host-a", "cli_runtime": "compatible-cli-1.0", "role_policy": "local-pilot-v1",
        "generator_model": "model-generator", "reviewer_model": "model-reviewer", "os": "windows",
        "language_runtime": "python-3.12", "framework": "pytest", "build_tool": "pytest",
        "execution_adapter": "pytest:selected-symbols-v1",
        "project_snapshot_digest": "sha256:" + "a" * 64,
        "module": "module-a",
        "policy_profile": "local-pilot-v1",
    }
    value = {
        "schema_version": "1.0.0", "campaign_id": campaign_id, "suite_id": suite["suite_id"],
        "suite_digest": suite["digest"], "scenario_id": scenario_id, "run_id": f"run-{sequence}",
        "sequence": sequence, "kind": kind, "tuple": tuple_value,
        "compatible": True, "verified": True, "independence": "verified",
        "protocol_violations": list(violations), "unstable": unstable, "execution": execution,
        "real_execution": True, "reviewer_session_digest": _digest({"reviewer_session": sequence}),
        "generator_invocation_id": f"generator-{sequence}", "reviewer_invocation_id": f"reviewer-{sequence}",
        "input_digest": "sha256:" + "c" * 64, "output_digest": "sha256:" + "d" * 64,
    }
    value["digest"] = _digest(value)
    return value


def _record(
    sequence,
    *,
    campaign_id="campaign-1",
    scenario_id=None,
    kind=None,
    unstable=False,
    violations=(),
    execution="PASS",
    tuple_value=None,
    suite=None,
    real_execution=True,
    independence="verified",
    model_stage_models=None,
    model_stage_invocations=None,
    model_stage_evidence_complete=True,
    tuple_complete=True,
):
    from evals.release_eval import _ValidatedRun

    asserted = _asserted_record(
        sequence,
        campaign_id=campaign_id,
        scenario_id=scenario_id,
        kind=kind,
        unstable=unstable,
        violations=violations,
        execution=execution,
        tuple_value=tuple_value,
        suite=suite,
    )
    outcome = ("TERMINAL", "COMPLETE", "FAIL" if unstable else execution, None)
    return _ValidatedRun(
        campaign_id=asserted["campaign_id"],
        suite_id=asserted["suite_id"],
        suite_digest=asserted["suite_digest"],
        scenario_id=asserted["scenario_id"],
        run_id=asserted["run_id"],
        attempt_id=f"{sequence:032x}",
        sequence=asserted["sequence"],
        kind=asserted["kind"],
        tuple_value=asserted["tuple"],
        tuple_complete=tuple_complete,
        compatible=True,
        independence=independence,
        protocol_violations=tuple(asserted["protocol_violations"]),
        outcome=outcome,
        scenario_passed=True,
        real_execution=real_execution,
        reviewer_session_digest=asserted["reviewer_session_digest"],
        model_stage_models=model_stage_models or {
            "context-marker": "model-context",
            "tc-generator": "model-generator",
            "tc-reviewer": "model-reviewer",
            "tc-to-autotest": "model-automation",
            "autotest-reviewer": "model-automation-reviewer",
        },
        model_stage_invocations=model_stage_invocations or tuple(
            f"{stage}-{sequence}"
            for stage in (
                "context-marker", "tc-generator", "tc-reviewer",
                "tc-to-autotest", "autotest-reviewer",
            )
        ),
        model_stage_evidence_complete=model_stage_evidence_complete,
        digest=asserted["digest"],
    )


def test_scenario_suite_is_digest_bound_and_counts_each_critical_scenario(pack_root: Path):
    from evals import release_eval

    assert hasattr(release_eval, "_load_scenario_suite")
    suite = release_eval._load_scenario_suite(pack_root / "evals" / "scenarios" / "pilot-critical.json")
    assert tuple(suite["critical_scenarios"]) == CRITICAL_SCENARIOS
    assert suite["scenario_contracts"]["execution-interruption-unknown"] == {
        "accepted": False,
        "verification": "UNKNOWN",
        "reason_code": "EXECUTION_UNKNOWN",
        "check_id": "execution-unknown",
        "execution_required": True,
    }

    sequence = 1
    records = [_record(sequence, scenario_id="smoke", kind="smoke", suite=suite)]
    for scenario_id in CRITICAL_SCENARIOS:
        for _ in range(3):
            sequence += 1
            records.append(_record(sequence, scenario_id=scenario_id, kind="critical", suite=suite))
    receipt = release_eval._evaluate_records(
        records,
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    assert receipt["scenario_suite"] == {"suite_id": "pilot-critical-v1", "digest": suite["digest"]}
    assert receipt["scenario_counts"] == {scenario_id: 3 for scenario_id in CRITICAL_SCENARIOS}
    assert receipt["ready"] is True


def test_policy_aggregator_rejects_self_declared_green_json():
    from evals import release_eval

    assert hasattr(release_eval, "_ValidatedRun")
    with pytest.raises(TypeError, match="validated run evidence"):
        release_eval._evaluate_records([_asserted_record(1)], suite=_suite())


def test_release_run_schema_accepts_one_check_and_only_durable_observation_identity(pack_root: Path):
    schema = json.loads((pack_root / "schemas" / "release-eval-run.schema.json").read_text(encoding="utf-8"))
    suite = _suite()
    record = {
        "schema_version": "1.0.0",
        "campaign_id": "campaign-1",
        "suite_id": suite["suite_id"],
        "suite_digest": suite["digest"],
        "scenario_id": "smoke",
        "run_id": "0" * 32,
        "attempt_id": "1" * 32,
        "sequence": 1,
        "kind": "smoke",
        "compatibility_evidence": {"path": "evidence/compatibility.json", "digest": "sha256:" + "2" * 64},
        "scenario_checks": [{"check_id": "accepted-terminal", "evidence_digests": ["sha256:" + "3" * 64]}],
    }
    record["digest"] = _digest(record)
    Draft202012Validator(schema).validate(record)
    record["scenario_checks"].append({"check_id": "accepted-terminal", "evidence_digests": ["sha256:" + "4" * 64]})
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(record)

    critical = {**record, "scenario_id": "terminal-idempotent-retry", "kind": "critical"}
    critical["scenario_checks"] = critical["scenario_checks"][:1]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(critical)
    critical["scenario_observation_receipt_digest"] = "sha256:" + "4" * 64
    Draft202012Validator(schema).validate(critical)
    critical["scenario_observation"] = {"digest": "sha256:" + "5" * 64}
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(critical)


def test_adaptive_policy_requires_one_smoke_then_three_critical_runs(pack_root):
    from evals.release_eval import _evaluate_records

    suite = _suite()
    pending = _evaluate_records([_record(1, suite=suite)], suite=suite)
    assert pending["required_critical_runs"] == 3 and pending["ready"] is False
    ready = _evaluate_records(
        [_record(index, suite=suite) for index in range(1, 5)],
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    assert ready["critical_runs"] == 3 and ready["ready"] is True
    assert "protocol_permanence" not in ready
    assert ready["tuple"]["model_stages"] == {
        "context-marker": "model-context",
        "tc-generator": "model-generator",
        "tc-reviewer": "model-reviewer",
        "tc-to-autotest": "model-automation",
        "autotest-reviewer": "model-automation-reviewer",
    }
    schema = json.loads((pack_root / "schemas" / "release-eval-receipt.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(ready)
    invalid = json.loads(json.dumps(ready))
    invalid["tuple"]["cli_host"] = "host with spaces"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
    invalid["tuple"]["cli_host"] = "unverified-host"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
    invalid = json.loads(json.dumps(ready))
    invalid["tuple"]["model_stages"]["context-marker"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
    branch_na = json.loads(json.dumps(ready))
    branch_na["tuple"]["model_stages"]["tc-to-autotest"] = None
    branch_na["tuple"]["model_stages"]["autotest-reviewer"] = None
    Draft202012Validator(schema).validate(branch_na)
    for mutate in (
        lambda value: value["protocol_violations"].append("FORGED_PROTOCOL_VIOLATION"),
        lambda value: value.update(require_real_execution=False),
        lambda value: value["scenario_counts"].update({"pilot-critical-v1": 0}),
    ):
        invalid = json.loads(json.dumps(ready))
        mutate(invalid)
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(invalid)


def test_ready_never_ignores_real_execution_or_reviewer_independence_flags():
    from evals.release_eval import _evaluate_records

    suite = _suite()
    no_real_execution = [_record(index, suite=suite) for index in range(1, 5)]
    no_real_execution[1] = _record(2, suite=suite, real_execution=False)
    assert _evaluate_records(no_real_execution, suite=suite)["ready"] is False

    unverified_review = [_record(index, suite=suite) for index in range(1, 5)]
    unverified_review[2] = _record(3, suite=suite, independence="independence_unverified")
    assert _evaluate_records(unverified_review, suite=suite)["ready"] is False

    incomplete_model_stage = [_record(index, suite=suite) for index in range(1, 5)]
    incomplete_model_stage[3] = _record(4, suite=suite, model_stage_evidence_complete=False)
    assert _evaluate_records(incomplete_model_stage, suite=suite)["ready"] is False

    incomplete_tuple = [_record(index, suite=suite) for index in range(1, 5)]
    incomplete_tuple[3] = _record(4, suite=suite, tuple_complete=False)
    assert _evaluate_records(incomplete_tuple, suite=suite)["ready"] is False

def test_all_model_stages_are_tuple_bound_and_invocation_reuse_is_a_protocol_violation():
    from evals.release_eval import ReleaseEvalError, _evaluate_records

    suite = _suite()
    changed_models = {
        "context-marker": "another-context-model",
        "tc-generator": "model-generator",
        "tc-reviewer": "model-reviewer",
        "tc-to-autotest": "model-automation",
        "autotest-reviewer": "model-automation-reviewer",
    }
    with pytest.raises(ReleaseEvalError, match="model stage"):
        _evaluate_records(
            [_record(1, suite=suite), _record(2, suite=suite, model_stage_models=changed_models)],
            suite=suite,
        )

    records = [_record(index, suite=suite) for index in range(1, 7)]
    records[1] = _record(
        2,
        suite=suite,
        model_stage_invocations=records[0].model_stage_invocations,
    )
    receipt = _evaluate_records(records, suite=suite)
    assert receipt["required_critical_runs"] == 5
    assert receipt["protocol_violations"] == ["CAMPAIGN_MODEL_INVOCATION_REUSED"]
    assert receipt["ready"] is False


def test_secret_context_proof_uses_declared_and_reviewer_provided_receipts():
    from evals.release_eval import _required_context_receipt_digests

    ledger = {
        "package_binding": {"context_receipt_digests": ["sha256:" + "e" * 64]},
        "events": [{"event_type": "EVIDENCE_PROVIDED", "provided_digest": "sha256:" + "f" * 64}],
    }
    assert _required_context_receipt_digests("smoke", ledger) == ()
    assert _required_context_receipt_digests("secret-exclusion", ledger) == (
        "sha256:" + "e" * 64, "sha256:" + "f" * 64,
    )


def _terminal_retry_observation(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    second = finalize_durable_execution_attempt(run_root, attempt_id)
    assert first["result"] == second["result"] and second["idempotent"] is True
    return (
        run_root, attempt_id,
        second["scenario_observation_receipts"]["terminal-idempotent-retry"],
    )


def test_fresh_non_git_observation_is_produced_by_public_finalizer(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    assert not (tmp_path / "project" / ".git").exists()
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["fresh-non-git"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "fresh-non-git", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "NON_GIT_PROJECT"
    assert loaded["record"]["action"]["kind"] == "RUN_PIPELINE"


def test_dirty_git_observation_proves_initial_user_bytes_are_preserved(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    project = tmp_path / "project"
    project.mkdir()
    user_file = project / "user-note.txt"
    user_file.write_text("keep this dirty byte sequence\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    expected = user_file.read_bytes()
    run_root, attempt_id, report, request, _durable = _execution_facts(
        tmp_path, project=project,
    )
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["dirty-git-preservation"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "dirty-git-preservation", published["digest"],
    )

    assert user_file.read_bytes() == expected
    assert loaded["verified"] is True
    assert loaded["record"]["before"] == loaded["record"]["after"]
    assert any(row["path"] == "user-note.txt" for row in loaded["record"]["before"]["files"])


def test_waiting_for_model_resume_observation_is_produced_after_real_model_pair(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["waiting-for-model-resume"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "waiting-for-model-resume", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "MODEL_WAITING"
    assert loaded["record"]["action"]["kind"] == "RESUME_MODEL_ATTEMPT"
    assert [row["event_type"] for row in loaded["events"]] == [
        "WAITING_FOR_MODEL", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "ATTEMPT_TERMINAL",
    ]


def test_one_user_question_resume_observation_is_produced_after_answered_model_pair(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, report, request, _durable = _execution_facts(
        tmp_path, wait_for_input=True,
    )
    _publish_execution(run_root, attempt_id, report, request=request)

    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["one-user-question-resume"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "one-user-question-resume", published["digest"],
    )

    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "USER_QUESTION_PENDING"
    assert loaded["record"]["action"]["kind"] == "RESUME_AFTER_USER_ANSWER"
    assert len(loaded["record"]["before"]["question_digests"]) == 1
    assert loaded["record"]["before"]["answer_digests"] == []
    assert len(loaded["record"]["after"]["answer_digests"]) == 1


def test_retained_native_rerun_observation_is_produced_by_public_controller(tmp_path: Path):
    import shutil

    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_project_native_pytest import _module_python, _request, _reviewed_inputs
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import claim_execution_start, derive_state, read_scenario_observation
    from tools.run_pipeline import rerun_retained_tests
    from tools.run_tests import run_tests_v5

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    document, automation, review, evidence, run_root, attempt_id, authorization, delta = (
        _reviewed_inputs(project)
    )
    request = _request(project, document, automation)
    report = run_tests_v5(
        request, authorization, document, automation, review,
        host_isolation_receipt=evidence,
        generated_delta_receipt=delta,
        run_root=run_root,
        attempt_id=attempt_id,
        on_execution_start=lambda value: claim_execution_start(
            run_root, attempt_id, request_digest(value),
        ),
    )
    assert report["verdict"] == "PASS"
    _publish_execution(run_root, attempt_id, report)
    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    assert finalized["result"]["accepted"] is True

    rerun = rerun_retained_tests(run_root, attempt_id)
    published = rerun["scenario_observation_receipts"]["retained-native-rerun"]
    loaded = read_scenario_observation(
        run_root, attempt_id, "retained-native-rerun", published["digest"],
    )
    state = derive_state(run_root)

    assert rerun["rerun_receipt"]["record"]["outcome"] == {
        "kind": "EXIT", "exit_code": 0,
    }
    assert loaded["verified"] is True
    assert loaded["record"]["precondition"]["kind"] == "RETAINED_FILE_SET"
    assert loaded["record"]["action"]["kind"] == "PROJECT_NATIVE_RERUN"
    assert loaded["record"]["before"] == loaded["record"]["after"]
    assert [row["event_type"] for row in loaded["events"]] == [
        "EXECUTION_STARTED", "ATTEMPT_TERMINAL", "RETAINED_NATIVE_RERUN",
    ]
    assert sum(
        row.get("event_type") == "EXECUTION_STARTED"
        and row.get("attempt_id") == attempt_id
        for row in state["events"]
    ) == 1
    assert sum(
        row.get("event_type") == "ATTEMPT_TERMINAL"
        and row.get("attempt_id") == attempt_id
        for row in state["events"]
    ) == 1
    repeated = rerun_retained_tests(run_root, attempt_id)
    repeated_state = derive_state(run_root)
    assert repeated["idempotent"] is True
    assert repeated["rerun_receipt"]["digest"] == rerun["rerun_receipt"]["digest"]
    assert repeated["scenario_observation_receipts"]["retained-native-rerun"]["digest"] == published["digest"]
    assert sum(
        row.get("event_type") == "RETAINED_NATIVE_RERUN"
        and row.get("attempt_id") == attempt_id
        for row in repeated_state["events"]
    ) == 1


def test_retained_native_rerun_event_cannot_be_appended_directly(tmp_path: Path):
    from tools.pilot_state import append_event

    with pytest.raises(ValueError, match="retained-rerun controller"):
        append_event(
            tmp_path, "RETAINED_NATIVE_RERUN", actor="controller",
            attempt_id="a" * 32, artifact_digest="sha256:" + "b" * 64,
        )


def test_retained_native_rerun_receipt_cannot_be_published_without_process_invocation(
    tmp_path: Path,
):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.run_tests import (
        DURABLE_NATIVE_REPORT_NORMALIZATION,
        _normalize_durable_junit_report,
    )

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    assert finalized["result"]["accepted"] is True
    context = pilot_state._retained_rerun_context(run_root, attempt_id)
    durable_report = _normalize_durable_junit_report(
        b"<testsuite tests='1'><testcase classname='tests.test_generated' "
        b"name='test_selected'/></testsuite>"
    )
    source_digest = "sha256:" + hashlib.sha256(durable_report).hexdigest()
    published_report = pilot_state.publish_run_artifact_bytes(
        run_root, attempt_id, "retained-rerun/reports/pytest.xml", durable_report,
    )
    fingerprint = {
        "mtime_ns": 1, "size": len(durable_report), "digest": source_digest,
    }
    value = {
        "schema_version": "1.0.0", "kind": "retained-native-rerun",
        "run_id": context["attempt"]["run_id"], "attempt_id": attempt_id,
        "policy_profile": context["attempt"]["policy_profile"],
        "terminal_result_digest": context["terminal"]["digest"],
        "execution_receipt_digest": context["execution_receipt"]["digest"],
        "execution_request_digest": context["execution_receipt"]["execution_request_digest"],
        "disposition_receipt_digest": context["disposition_receipt"]["digest"],
        "before": context["snapshot"], "after": context["snapshot"],
        "outcome": {"kind": "EXIT", "exit_code": 0},
        "reports": [{
            "source_path": "test-results/pytest.xml",
            "source_digest": source_digest,
            "before_fingerprint": None,
            "after_fingerprint": fingerprint,
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": published_report["path"],
            "digest": published_report["digest"],
        }],
    }
    before_events = pilot_state.derive_state(run_root)["events"]

    with pytest.raises((AttributeError, ValueError)):
        pilot_state._publish_retained_native_rerun(run_root, attempt_id, value)

    assert pilot_state.derive_state(run_root)["events"] == before_events
    assert not pilot_state._retained_native_rerun_target(run_root, attempt_id).exists()


def test_retained_native_rerun_rejects_passing_report_for_unreviewed_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.run_pipeline import rerun_retained_tests
    from tools.run_tests import ProcessOutcome

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    finalized = finalize_durable_execution_attempt(run_root, attempt_id)
    assert finalized["result"]["accepted"] is True
    executable = Path(request.executable)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"MZ")
    executable.chmod(0o755)

    def invoke_foreign_report(_request, _invoke):
        report_path = Path(request.cwd) / request.report_paths[0]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(
            b"<testsuite tests='1'><testcase classname='tests.test_foreign' "
            b"name='test_foreign'/></testsuite>"
        )
        return ProcessOutcome(0, "", "")

    monkeypatch.setattr("tools.execution_adapters.invoke_request", invoke_foreign_report)

    with pytest.raises(ValueError, match="reviewed targets"):
        rerun_retained_tests(run_root, attempt_id)

    assert not pilot_state._retained_native_rerun_target(run_root, attempt_id).exists()
    assert not any(
        row.get("event_type") == "RETAINED_NATIVE_RERUN"
        for row in pilot_state.derive_state(run_root)["events"]
    )


def test_terminal_retry_scenario_uses_controller_owned_durable_observation(tmp_path: Path):
    from evals.release_eval import _scenario_proof
    from tools.pilot_state import (
        derive_state, read_reviewer_session_ledger, read_scenario_observation,
        read_terminal_result, terminal_reviewer_evidence,
    )

    run_root, attempt_id, published = _terminal_retry_observation(tmp_path)
    loaded = read_scenario_observation(
        run_root, attempt_id, "terminal-idempotent-retry", published["digest"],
    )
    state = derive_state(run_root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    terminal = read_terminal_result(run_root, attempt_id)
    assert loaded.get("verified") is True
    binding = [
        row for row in state["events"]
        if row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
        and row.get("artifact_digest") == loaded["digest"]
        and row.get("attempt_id") == attempt_id
    ]
    assert binding == [loaded["binding_event"]]
    assert loaded["record"]["before"] == loaded["record"]["after"]
    assert loaded["record"]["before"]["execution_start_count"] == 1
    assert len(loaded["record"]["before"]["terminal_artifact_digests"]) == 4
    assert _scenario_proof("terminal-idempotent-retry", {
        "attempt": attempt, "attempts": state["attempts"], "events": state["events"],
        "exclusions": [], "reviewer": terminal_reviewer_evidence(run_root, attempt_id),
        "ledger": read_reviewer_session_ledger(run_root, attempt_id), "terminal": terminal,
        "available_receipt_digests": [terminal["digest"]],
        "scenario_observation": loaded["record"],
        "scenario_observation_events": loaded["events"],
        "scenario_observation_verified": True,
    }) is True


def test_terminal_retry_observation_cannot_be_published_outside_second_finalize(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    before = pilot_state.derive_state(run_root)["events"]

    with pytest.raises((AttributeError, ValueError)):
        pilot_state._publish_terminal_retry_observation(
            run_root,
            attempt_id,
            first["result"],
            pilot_state._terminal_retry_snapshot(run_root, attempt_id),
        )

    assert pilot_state.derive_state(run_root)["events"] == before
    assert not (run_root / "scenario-observations" / f"{attempt_id}.terminal-idempotent-retry.json").exists()


def test_release_scenario_observation_tamper_fails_closed(tmp_path: Path):
    from tools.pilot_state import read_scenario_observation

    run_root, attempt_id, published = _terminal_retry_observation(tmp_path)
    target = run_root / published["path"]
    target.write_bytes(published["bytes"].replace(b'"terminal_event_count":1', b'"terminal_event_count":2'))
    with pytest.raises(ValueError, match="scenario observation"):
        read_scenario_observation(
            run_root, attempt_id, "terminal-idempotent-retry", published["digest"],
        )



def test_hand_placed_terminal_retry_observation_without_journal_is_rejected(tmp_path: Path):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import (
        _canonical_bytes, _sealed, _terminal_retry_snapshot, derive_state,
        read_scenario_observation, read_terminal_result,
    )

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    _publish_execution(run_root, attempt_id, report, request=request)
    first = finalize_durable_execution_attempt(run_root, attempt_id)
    assert first.get("idempotent") is not True
    assert "terminal-idempotent-retry" not in first.get("scenario_observation_receipts", {})
    state = derive_state(run_root)
    attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
    terminal = read_terminal_result(run_root, attempt_id)
    terminal_events = [
        row for row in state["events"]
        if row.get("attempt_id") == attempt_id and row.get("event_type") == "ATTEMPT_TERMINAL"
    ]
    snapshot = _terminal_retry_snapshot(run_root, attempt_id)
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"],
        "scenario_id": "terminal-idempotent-retry",
        "precondition": {"kind": "TERMINAL_ATTEMPT", "snapshot_digest": snapshot["digest"]},
        "action": {
            "kind": "RETRY_TERMINAL_ATTEMPT",
            "before_digest": snapshot["digest"], "after_digest": snapshot["digest"],
        },
        "before": snapshot, "after": snapshot,
        "event_digests": [terminal_events[0]["digest"]],
        "receipt_digests": snapshot["terminal_artifact_digests"],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    record = _sealed(value)
    target = run_root / "scenario-observations" / f"{attempt_id}.terminal-idempotent-retry.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_canonical_bytes(record))
    with pytest.raises(ValueError, match="scenario observation|terminal retry"):
        read_scenario_observation(
            run_root, attempt_id, "terminal-idempotent-retry", record["digest"],
        )


def test_instability_disqualifies_current_campaign_and_next_clean_campaign_requires_five(
    tmp_path: Path, pack_root: Path,
):
    from evals.release_eval import _evaluate_records, _evaluate_with_history

    suite = _suite()
    project = tmp_path / "project"
    project.mkdir()
    values = [_record(index, unstable=index == 2, suite=suite) for index in range(1, 5)]
    receipt = _evaluate_records(values, suite=suite)
    assert receipt["required_critical_runs"] == 5 and receipt["ready"] is False
    values.extend([_record(5, suite=suite), _record(6, suite=suite)])
    failed_campaign = _evaluate_with_history(
        values,
        project=project,
        pack_root=pack_root,
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    assert failed_campaign["ready"] is False

    corrected_tuple = dict(values[0].tuple_value)
    corrected_tuple["skill_pack_version"] = "0.8.1"
    corrected_tuple["skill_pack_digest"] = "sha256:" + "d" * 64
    corrected_tuple["project_snapshot_digest"] = "sha256:" + "b" * 64
    clean = [
        _record(index, campaign_id="campaign-2", tuple_value=corrected_tuple, suite=suite)
        for index in range(1, 5)
    ]
    pending = _evaluate_with_history(
        clean,
        project=project,
        pack_root=pack_root,
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    assert pending["required_critical_runs"] == 5 and pending["ready"] is False
    clean.extend([
        _record(5, campaign_id="campaign-2", tuple_value=corrected_tuple, suite=suite),
        _record(6, campaign_id="campaign-2", tuple_value=corrected_tuple, suite=suite),
    ])
    recovered = _evaluate_with_history(
        clean,
        project=project,
        pack_root=pack_root,
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    assert recovered["ready"] is True
    assert recovered["predecessor_evaluation_digest"] == failed_campaign["digest"]


def test_protocol_violation_escalates_to_five_and_permanently_blocks_readiness():
    from evals.release_eval import _evaluate_records

    suite = _suite()
    values = [_record(index, violations=("SECOND_AUTHORITATIVE_VERDICT",) if index == 2 else (), suite=suite) for index in range(1, 7)]
    receipt = _evaluate_records(values, suite=suite)
    assert receipt["required_critical_runs"] == 5
    assert receipt["protocol_violations"] == ["SECOND_AUTHORITATIVE_VERDICT"]
    assert receipt["ready"] is False


def test_evidence_cannot_transfer_trust_across_tuple_or_skip_sequence():
    from evals.release_eval import ReleaseEvalError, _evaluate_records, _valid_tuple

    suite = _suite()
    changed = dict(_record(2, suite=suite).tuple_value)
    changed["module"] = "module-b"
    with pytest.raises(ReleaseEvalError, match="tuples"):
        _evaluate_records([_record(1, suite=suite), _record(2, tuple_value=changed, suite=suite)], suite=suite)
    with pytest.raises(ReleaseEvalError, match="contiguous"):
        _evaluate_records([_record(1, suite=suite), _record(3, suite=suite)], suite=suite)
    incomplete = dict(_record(1, suite=suite).tuple_value)
    incomplete["cli_host"] = "unverified-host"
    assert _valid_tuple(incomplete) is False
    root_module = dict(_record(1, suite=suite).tuple_value)
    root_module["module"] = "."
    assert _valid_tuple(root_module) is True


def test_external_gate_requires_real_project_module_and_policy(tmp_path: Path):
    from evals.release_eval import main

    with pytest.raises(SystemExit) as error:
        main(["--campaign-dir", str(tmp_path)])
    assert error.value.code == 2


def test_previous_good_approval_binds_the_exact_immutable_eval_result(tmp_path: Path):
    from evals.release_eval import ReleaseEvalError, _evaluate_records, _human_approval_digest

    suite = _suite()
    evaluated = _evaluate_records(
        [_record(index, suite=suite) for index in range(1, 5)],
        suite=suite,
        require_real_execution=True,
        require_independent_review=True,
    )
    approval = {
        "schema_version": "1.0.0",
        "approval": "APPROVED",
        "approver": "qa-lead",
        "campaign_id": evaluated["campaign_id"],
        "release_eval_digest": evaluated["digest"],
    }
    approval["digest"] = _digest(approval)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval), encoding="utf-8")

    assert _human_approval_digest(path, evaluated) == approval["digest"]
    with pytest.raises(ReleaseEvalError, match="validated release evaluation"):
        _human_approval_digest(path, json.loads(json.dumps(evaluated)))
    other = {**evaluated, "campaign_id": "campaign-2"}
    other["digest"] = _digest({key: value for key, value in other.items() if key != "digest"})
    with pytest.raises(ReleaseEvalError, match="validated release evaluation"):
        _human_approval_digest(path, other)

    relaxed = _evaluate_records(
        [_record(index, suite=suite) for index in range(1, 5)],
        suite=suite,
    )
    approval["campaign_id"] = relaxed["campaign_id"]
    approval["release_eval_digest"] = relaxed["digest"]
    approval["digest"] = _digest({key: value for key, value in approval.items() if key != "digest"})
    path.write_text(json.dumps(approval), encoding="utf-8")
    with pytest.raises(ReleaseEvalError, match="release evaluation is invalid"):
        _human_approval_digest(path, relaxed)


def test_campaign_loader_rejects_duplicate_json_keys(tmp_path: Path):
    from evals.release_eval import ReleaseEvalError, _load_campaign

    record = {"schema_version": "1.0.0"}
    record["digest"] = _digest(record)
    (tmp_path / "records").mkdir()
    (tmp_path / "records" / "run.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "0001.json").write_text(
        '{"path":"records/run.json","path":"records/run.json","digest":"' + record["digest"] + '"}',
        encoding="utf-8",
    )

    with pytest.raises(ReleaseEvalError, match="unreadable"):
        _load_campaign(tmp_path)


def test_campaign_api_translates_missing_durable_run_into_release_eval_error(
    tmp_path: Path, pack_root: Path, monkeypatch: pytest.MonkeyPatch,
):
    from evals.release_eval import ReleaseEvalError, _load_scenario_suite, evaluate_campaign
    from tools.release_manifest import build_release_manifest

    project = tmp_path / "project"
    project.mkdir()
    campaign = tmp_path / "campaign"
    (campaign / "records").mkdir(parents=True)
    manifest = build_release_manifest(pack_root)
    monkeypatch.setattr("tools.release_manifest.load_release_manifest", lambda _root: manifest)
    suite = _load_scenario_suite(pack_root / "evals" / "scenarios" / "pilot-critical.json")
    record = {
        "schema_version": "1.0.0",
        "campaign_id": "campaign-1",
        "suite_id": suite["suite_id"],
        "suite_digest": suite["digest"],
        "scenario_id": "smoke",
        "run_id": "0" * 32,
        "attempt_id": "1" * 32,
        "sequence": 1,
        "kind": "smoke",
        "compatibility_evidence": {"path": "evidence/missing.json", "digest": "sha256:" + "2" * 64},
        "scenario_checks": [{"check_id": "accepted-terminal", "evidence_digests": ["sha256:" + "3" * 64]}],
    }
    record["digest"] = _digest(record)
    record_path = campaign / "records" / "smoke.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    reference = {"path": "records/smoke.json", "digest": record["digest"]}
    (campaign / "0001.json").write_text(json.dumps(reference), encoding="utf-8")

    with pytest.raises(ReleaseEvalError, match="durable evidence"):
        evaluate_campaign(campaign, project, pack_root=pack_root)


def test_campaign_api_translates_release_manifest_failure(
    tmp_path: Path, pack_root: Path, monkeypatch: pytest.MonkeyPatch,
):
    from evals.release_eval import ReleaseEvalError, evaluate_campaign

    campaign = tmp_path / "campaign"
    project = tmp_path / "project"
    campaign.mkdir()
    project.mkdir()

    def fail_manifest(_root):
        raise ValueError("invalid manifest")

    monkeypatch.setattr("tools.release_manifest.load_release_manifest", fail_manifest)
    with pytest.raises(ReleaseEvalError, match="release manifest"):
        evaluate_campaign(campaign, project, pack_root=pack_root)


@pytest.mark.parametrize("condition", ("missing", "stale", "tampered"))
def test_campaign_requires_current_persisted_release_manifest(
    condition: str, tmp_path: Path, pack_root: Path,
):
    from evals.release_eval import ReleaseEvalError, evaluate_campaign
    from tests.test_release_manifest import _copy_runtime
    from tools.release_manifest import build_release_manifest

    runtime = _copy_runtime(pack_root, tmp_path / "pack")
    campaign = tmp_path / "campaign"
    project = tmp_path / "project"
    campaign.mkdir()
    project.mkdir()
    if condition != "missing":
        manifest = build_release_manifest(runtime)
        (runtime / "release").mkdir()
        if condition == "tampered":
            manifest["package_version"] = "forged"
        (runtime / "release" / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        if condition == "stale":
            skill = runtime / "skills" / "tc-generator" / "SKILL.md"
            skill.write_bytes(skill.read_bytes() + b"\n")

    with pytest.raises(ReleaseEvalError, match="release manifest"):
        evaluate_campaign(campaign, project, pack_root=runtime)


def test_campaign_loader_rejects_escape_and_previous_good_requires_human_receipt(tmp_path: Path):
    from evals.release_eval import ReleaseEvalError, _load_campaign, main

    (tmp_path / "reference.json").write_text(json.dumps({"path": "../escape.json", "digest": "sha256:" + "a" * 64}), encoding="utf-8")
    with pytest.raises(ReleaseEvalError, match="escapes"):
        _load_campaign(tmp_path)
    assert main(["--campaign-dir", str(tmp_path), "--project", str(tmp_path), "--module", "module-a", "--policy", "local-pilot-v1", "--previous-known-good"]) == 2
