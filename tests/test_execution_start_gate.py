from pathlib import Path
from tests.helpers import MODULE_PYTHON
import shutil

import pytest

from helpers import build_phase_two_baseline
from tools import pilot_state


def _attempt(project: Path, policy_profile: str, execution_requested: bool) -> tuple[Path, dict]:
    project.mkdir(parents=True, exist_ok=True)
    identity = {
        "project": str(project.resolve()),
        "module": ".",
        "policy_profile": policy_profile,
    }
    baseline = build_phase_two_baseline(project, identity, skill_pack_root=project / ".pilot-runs")
    run = pilot_state.create_run(
        project,
        policy_profile,
        {"request_id": f"execution-{policy_profile}", "execution_requested": execution_requested},
    )
    run_root = Path(run["run_root"])
    pilot_state.append_event(
        run_root,
        "MODULE_SELECTED",
        actor="controller",
        artifact_digest=pilot_state.module_selection_digest(project, "."),
    )
    frozen = pilot_state.freeze_phase_two_inputs(run_root, project, project, baseline)
    return run_root, dict(pilot_state.create_attempt(run_root, identity, frozen["baseline"]))


def test_execution_start_is_one_atomic_attempt_owned_claim(tmp_path: Path) -> None:
    run_root, attempt = _attempt(tmp_path, "local-pilot-v1", True)
    request_digest = "sha256:" + "a" * 64

    event = pilot_state.claim_execution_start(run_root, attempt["attempt_id"], request_digest)

    assert event["event_type"] == "EXECUTION_STARTED"
    assert event["attempt_id"] == attempt["attempt_id"]
    assert event["artifact_digest"] == request_digest
    assert [
        row for row in pilot_state.derive_state(run_root)["events"]
        if row["event_type"] == "EXECUTION_STARTED"
    ] == [event]
    with pytest.raises(ValueError, match="execution already started"):
        pilot_state.claim_execution_start(run_root, attempt["attempt_id"], request_digest)
    with pytest.raises(ValueError, match="execution already started"):
        pilot_state.claim_execution_start(run_root, attempt["attempt_id"], "sha256:" + "b" * 64)


def test_execution_start_rejects_cases_only_and_invalid_request_digest(tmp_path: Path) -> None:
    cases_root, cases_attempt = _attempt(tmp_path / "cases", "cases-only-v1", False)

    with pytest.raises(ValueError, match="authorization"):
        pilot_state.claim_execution_start(cases_root, cases_attempt["attempt_id"], "sha256:" + "a" * 64)

    local_root, local_attempt = _attempt(tmp_path / "local", "local-pilot-v1", True)
    with pytest.raises(ValueError, match="request digest"):
        pilot_state.claim_execution_start(local_root, local_attempt["attempt_id"], "not-a-digest")
    assert not any(
        row["event_type"] == "EXECUTION_STARTED"
        for row in pilot_state.derive_state(local_root)["events"]
    )


def test_generic_event_api_cannot_forge_execution_start(tmp_path: Path) -> None:
    run_root, attempt = _attempt(tmp_path, "local-pilot-v1", True)

    with pytest.raises(ValueError, match="claim_execution_start"):
        pilot_state.append_event(
            run_root,
            "EXECUTION_STARTED",
            actor="controller",
            attempt_id=attempt["attempt_id"],
            artifact_digest="sha256:" + "a" * 64,
        )

    assert not any(
        row["event_type"] == "EXECUTION_STARTED"
        for row in pilot_state.derive_state(run_root)["events"]
    )


def test_runner_refuses_noop_or_foreign_start_callback_before_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The callback cannot itself be treated as proof that execution was claimed."""
    from tests.test_project_native_pytest import _request, _reviewed_inputs
    from tools.execution_adapters import request_digest
    from tools.run_tests import run_tests_v5

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    runtime = project / MODULE_PYTHON
    runtime.parent.mkdir(parents=True)
    runtime.write_text("not started", encoding="utf-8")
    runtime.chmod(0o755)
    document, artifact, review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    request = _request(project, document, artifact)
    calls: list[object] = []

    monkeypatch.setattr(
        "tools.execution_adapters.invoke_request",
        lambda *_args, **_kwargs: calls.append("subprocess") or pytest.fail("project process must not start"),
    )
    noop = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id, on_execution_start=lambda _request: None,
    )
    from tools.pilot_state import publish_attempt_receipt
    from tools.schema_validation import schema_diagnostics

    assert noop["execution"]["request_digest"] == request_digest(request)
    assert noop["target"] == {
        "language": "python", "framework": "pytest",
        "runner": "pytest", "command": "pytest:selected-symbols-v1",
    }
    assert noop["environment"] == {
        "status": "ready", "interpreter": request.executable,
        "interpreter_path": request.executable, "working_dir": str(project.resolve()),
        "missing": None, "safe_key_labels": ["PROJECT_NATIVE_ENV"],
    }
    assert schema_diagnostics(noop, Path("schemas/run-tests-output.schema.json"), Path.cwd()) == []
    prestart_receipt = publish_attempt_receipt(
        run_root, attempt_id, "execution-receipt", {"payload": noop}
    )
    assert prestart_receipt["record"]["execution_request_digest"] == request_digest(request)

    foreign_root, foreign_attempt = _attempt(tmp_path / "foreign", "local-pilot-v1", True)
    forged = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id,
        on_execution_start=lambda value: pilot_state.claim_execution_start(
            foreign_root, foreign_attempt["attempt_id"], request_digest(value)
        ),
    )
    for result in (noop, forged):
        assert result["verdict"] == "NOT_RUNNABLE"
        assert any(row["code"] == "RUNNER_EXECUTION_GATE" for row in result["diagnostics"])
    assert calls == []
    starts = [
        row for row in pilot_state.derive_state(run_root)["events"]
        if row["event_type"] == "EXECUTION_STARTED"
    ]
    assert starts == []


def test_runner_refuses_replay_after_preexisting_execution_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_project_native_pytest import _request, _reviewed_inputs
    from tools.execution_adapters import request_digest
    from tools.run_tests import run_tests_v5

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    runtime = project / MODULE_PYTHON
    runtime.parent.mkdir(parents=True)
    runtime.write_text("not started", encoding="utf-8")
    runtime.chmod(0o755)
    document, artifact, review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    request = _request(project, document, artifact)
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    monkeypatch.setattr(
        "tools.execution_adapters.invoke_request",
        lambda *_args, **_kwargs: pytest.fail("interrupted execution must not replay"),
    )

    result = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id, on_execution_start=lambda _request: None,
    )

    assert result["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_EXECUTION_GATE" for row in result["diagnostics"])


def test_runner_preserves_closed_request_for_admission_and_isolation_rejections(tmp_path: Path) -> None:
    """Admission failures remain publishable, request-bound prestart evidence."""
    from dataclasses import replace
    from tests.test_project_native_pytest import _request, _reviewed_inputs
    from tools.execution_adapters import request_digest
    from tools.pilot_state import publish_attempt_receipt
    from tools.run_tests import run_tests_v5
    from tools.schema_validation import schema_diagnostics

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    runtime = project / MODULE_PYTHON
    runtime.parent.mkdir(parents=True)
    runtime.write_text("not started", encoding="utf-8")
    runtime.chmod(0o755)
    document, artifact, review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    request = _request(project, document, artifact)

    rejected_authorization = run_tests_v5(
        request, {**authorization, "execution_requested": False}, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id,
    )
    rejected_delta = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=None,
        run_root=run_root, attempt_id=attempt_id,
    )
    rejected_isolation = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt={**evidence, "reviewer_invocation_id": "forged-reviewer"},
        generated_delta_receipt=delta, run_root=run_root, attempt_id=attempt_id,
    )

    for result, code in (
        (rejected_authorization, "RUNNER_AUTHORIZATION"),
        (rejected_delta, "RUNNER_GENERATED_DELTA"),
        (rejected_isolation, "AUTOTEST_REVIEW_ISOLATION"),
    ):
        assert result["execution"]["request_digest"] == request_digest(request)
        assert result["execution"]["run_id"] == authorization["run_id"]
        assert result["execution"]["attempt_id"] == attempt_id
        assert result["execution"]["baseline_digest"] == delta["delta"]["baseline_digest"]
        assert result["execution"]["generated_delta_digest"] == delta["delta"]["digest"]
        assert result["target"] == {
            "language": "python", "framework": "pytest",
            "runner": "pytest", "command": "pytest:selected-symbols-v1",
        }
        assert result["environment"] == {
            "status": "ready", "interpreter": request.executable,
            "interpreter_path": request.executable, "working_dir": str(project.resolve()),
            "missing": None, "safe_key_labels": ["PROJECT_NATIVE_ENV"],
        }
        assert any(row["code"] == code for row in result["diagnostics"])
        assert schema_diagnostics(result, Path("schemas/run-tests-output.schema.json"), Path.cwd()) == []

    published = publish_attempt_receipt(
        run_root, attempt_id, "execution-receipt", {"payload": rejected_authorization}
    )
    assert published["record"]["execution_request_digest"] == request_digest(request)

    invalid_request = run_tests_v5(
        replace(request, cwd=str(tmp_path.resolve())), authorization, document, artifact, review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id,
    )
    assert invalid_request["execution"]["request_digest"] is None
    assert invalid_request["target"]["runner"] == "not_applicable"
    assert invalid_request["environment"]["status"] == "partial"
    assert any(row["code"] == "RUNNER_REQUEST_PROVENANCE" for row in invalid_request["diagnostics"])
