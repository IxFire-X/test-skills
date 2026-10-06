from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest


def _controller_inputs(tmp_path: Path) -> tuple[Path, dict, dict, dict, dict, dict, dict]:
    """Create one real active attempt with a reviewed, materialized delta."""
    import shutil

    from tests.test_project_native_pytest import _module_python, _reviewed_inputs
    from tools.pilot_state import derive_state
    from tools.run_tests import resolve_execution_context

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    document, automation, review, boundary, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    execution_root, language, module = resolve_execution_context(project, project / ".skillsrc", "python-pytest", None)
    coordinates = {
        "run_root": run_root,
        "manifest": {"project": str(project.resolve()), "run_id": run_root.name},
        "authorization": authorization,
        "attempt": next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id),
        "module_root": execution_root,
        "module": module,
    }
    return project, coordinates, document, automation, review, authorization, {"boundary": boundary, "delta": delta, "language": language}


def _resume_cli_context(tmp_path: Path, run_root: Path, attempt_id: str, durable: dict):
    from tools import pilot_state

    run = pilot_state.read_run(run_root)
    attempt = next(
        row for row in pilot_state.derive_state(run_root)["attempts"]
        if row["attempt_id"] == attempt_id
    )
    inputs = tmp_path / "resume-inputs"
    inputs.mkdir()
    carriers = {
        "canonical_document": durable["trace_inputs"]["canonical_document"],
        "automation_artifact": durable["trace_inputs"]["automation_artifact"],
        "autotest_review": durable["trace_inputs"]["autotest_review"],
        "authorization_receipt": run["authorization"],
        "host_isolation_receipt": {},
        "generated_delta_receipt": durable["delta"],
    }
    paths = {}
    for name, value in carriers.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths[name] = str(path)
    args = SimpleNamespace(
        project=attempt["project"], run=run["manifest"]["run_id"],
        module=None, language=None, target=None, executor="local", **paths,
    )
    coordinates = {
        "run_root": run_root, "manifest": run["manifest"], "authorization": run["authorization"],
        "attempt": attempt, "module_root": Path(attempt["project"]), "module": {"root": "."},
    }
    return args, coordinates, carriers, inputs


def test_runner_v5_environment_receipt_has_safe_labels_never_values() -> None:
    from tools.run_tests import _report
    from tools.schema_validation import schema_diagnostics

    report = _report(
        "NOT_RUNNABLE", Path.cwd(), "python",
        {"document_id": "TCDOC-run", "revision": 1, "source_digest": "sha256:" + "a" * 64, "effective_bundle_receipt_digest": "sha256:" + "d" * 64},
        "sha256:" + "b" * 64, "sha256:" + "c" * 64,
        diagnostics=[{"path": "/authorization", "code": "RUNNER_AUTHORIZATION", "message": "Execution was not authorized."}],
    )

    assert report["schema_version"] == "5.0.0"
    assert report["environment"]["safe_key_labels"] == []
    assert schema_diagnostics(report, Path("schemas/run-tests-output.schema.json"), Path.cwd()) == []


def test_runner_v5_unknown_preserves_non_authoritative_timeout_receipt() -> None:
    from tools.run_tests import ProcessOutcome, _process_row, _report, validate_execution_evidence

    source = {"document_id": "TCDOC-run", "revision": 1, "source_digest": "sha256:" + "a" * 64, "effective_bundle_receipt_digest": "sha256:" + "d" * 64}
    timeout = ProcessOutcome(124, "", "", "TIMEOUT")
    report = _report(
        "UNKNOWN", Path.cwd(), "python", source, "sha256:" + "b" * 64, "sha256:" + "c" * 64,
        run_id="RUN-timeout", process_evidence=[_process_row("TIMEOUT", timeout, "RUN-timeout", source, "pytest:selected-symbols-v1", 1.0)],
        authoritative=False, exit_code=124, runner="pytest", interpreter="python", command_profile="pytest:selected-symbols-v1", duration_sec=1.0,
    )

    assert report["verdict"] == "UNKNOWN"
    assert report["evidence_authoritative"] is False
    assert validate_execution_evidence(
        report["verdict"], report["run_id"], report["source"],
        report["execution_evidence"], report["evidence_authoritative"],
        [("FILE-generated", "SYMBOL-generated")],
        {"FILE-generated": "sha256:" + "e" * 64},
        report["exit_code"], report["stats"], report["diagnostics"],
        report["process_evidence"], report["target"],
    ) == []


def test_subprocess_preserves_non_locale_output_with_bounded_memory_tail(tmp_path: Path) -> None:
    import os
    import sys
    from tools.run_tests import PROCESS_OUTPUT_TAIL_BYTES, run_subprocess

    outcome = run_subprocess(
        [sys.executable, "-c", "import os; os.write(1, b'x' * 70000 + b'\\n\\x98done\\n'); os.write(2, 'Привет'.encode('utf-8'))"],
        tmp_path, timeout=10,
    )
    assert outcome.kind == "EXIT" and outcome.exit_code == 0
    if os.name == "nt":
        # Review decision 16: non-UTF-8 output is decoded with the Windows console code page.
        assert outcome.stdout.endswith("done\n") and len(outcome.stdout) == 6
    else:
        assert outcome.stdout == "\ufffddone\n"
    assert outcome.stderr == "Привет"
    assert len(outcome.stdout.encode()) <= PROCESS_OUTPUT_TAIL_BYTES


def test_controller_timeout_records_os_process_scope_stop(tmp_path: Path) -> None:
    import os
    import sys

    from tools.run_tests import run_subprocess

    outcome = run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        timeout=1,
    )

    assert outcome.kind == "TIMEOUT"
    if os.name == "nt":
        # Review decision 13: suspended start + Job Object gives a real proof on Windows.
        assert (outcome.process_scope_stopped, outcome.stop_proof) == (True, "WINDOWS_JOB_TERMINATED")
    else:
        assert (outcome.process_scope_stopped, outcome.stop_proof) == (True, "POSIX_PROCESS_GROUP")


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows containment only")
def test_windows_job_assignment_failure_is_not_process_stop_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from tools import run_tests

    monkeypatch.setattr(run_tests, "_assign_windows_kill_job", lambda _process: None)
    outcome = run_tests.run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=1,
    )

    assert (outcome.kind, outcome.process_scope_stopped, outcome.stop_proof) == (
        "TIMEOUT", False, None,
    )


def test_doctor_marks_company_runner_non_core_and_no_language_broadly_verified() -> None:
    from tools.doctor import inspect_environment

    report = inspect_environment(Path.cwd())

    assert report["adapters"]["company_runner"]["core"] is False
    assert all(language["verified"] is False for language in report["languages"].values())
    assert "invalid:contracts/pipeline.json" not in report["integrity"]["missing"]
    assert "invalid:release/manifest.json" not in report["integrity"]["missing"]
    assert report["status_scope"] == "pack_integrity"
    assert report["qualification"] == {"state": "implemented_unverified", "ready_tuple": None}


def test_local_core_closure_has_no_company_runner_import_dependency() -> None:
    for relative in (
        "tools/run_pipeline.py",
        "tools/build_trace_document.py",
        "tools/orchestrate_test_case_revision.py",
    ):
        assert "tools.company_runner" not in Path(relative).read_text(encoding="utf-8")


def test_controller_exec_is_local_only_and_requires_sealed_run_coordinates() -> None:
    from tools.run_pipeline import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "exec", "--project", "project", "--run", "a" * 32,
        "--canonical-document", "canonical.json",
        "--automation-artifact", "automation.json",
        "--autotest-review", "review.json",
        "--authorization-receipt", "authorization.json",
        "--host-isolation-receipt", "isolation.json",
        "--generated-delta-receipt", "delta.json",
    ])

    assert args.executor == "local"
    assert not hasattr(args, "run_root") and not hasattr(args, "attempt_id")
    assert not any(action.dest == "executor" and "company" in action.choices for action in parser._subparsers._group_actions[0].choices["exec"]._actions if action.choices)


def test_execution_coordinates_reject_a_legacy_or_foreign_run_label_before_artifact_loading(tmp_path: Path) -> None:
    """A caller cannot combine an arbitrary legacy --run with a durable pilot root."""
    from tools.pilot_state import create_run
    from tools.run_pipeline import HostStop, _validated_execution_coordinates

    create_run(
        tmp_path,
        "local-pilot-v1",
        {"request_id": "coordinate-check", "execution_requested": True},
    )

    with pytest.raises(HostStop) as error:
        _validated_execution_coordinates(tmp_path, "legacy-slug/legacy-attempt")
    assert error.value.code == "EXECUTION_COORDINATES"


def test_cmd_exec_rejects_unbound_coordinates_before_reading_caller_artifacts(tmp_path: Path, capsys) -> None:
    from tools.pilot_state import create_run
    from tools.run_pipeline import cmd_exec

    create_run(
        tmp_path,
        "local-pilot-v1",
        {"request_id": "coordinate-command", "execution_requested": True},
    )
    args = SimpleNamespace(
        project=str(tmp_path), run="legacy-slug/legacy-attempt",
        module=None, language=None, target=None, executor="local",
        canonical_document=str(tmp_path / "missing-canonical.json"),
        automation_artifact=str(tmp_path / "missing-automation.json"),
        autotest_review=str(tmp_path / "missing-review.json"),
        authorization_receipt=str(tmp_path / "missing-authorization.json"),
        host_isolation_receipt=str(tmp_path / "missing-isolation.json"),
        generated_delta_receipt=str(tmp_path / "missing-delta.json"),
    )

    assert cmd_exec(args) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "EXECUTION_COORDINATES"


def test_controller_claims_execution_only_from_v5_admission_and_never_legacy_closes(tmp_path: Path, monkeypatch) -> None:
    from tools import run_pipeline, run_tests
    from tools.execution_adapters import ExecutionRequest, PYTEST

    project, run_root = tmp_path / "project", tmp_path / "pilot-run"
    project.mkdir(); run_root.mkdir()
    module = {
        "id": "module", "root": ".", "stack": {"language": "python", "framework": "pytest"},
        "test": {"framework": "pytest", "adapter_id": PYTEST, "interpreter": "runtime/python", "build_profile": "default", "adapter_parameters": {}},
    }
    request = ExecutionRequest(PYTEST, "runtime/python", ("runtime/python", "-m", "pytest", "--junitxml", "test-results/pytest.xml", "tests/test_one.py::test_one"), str(project), ("tests/test_one.py::test_one",), 600, ("test-results/pytest.xml",), ())
    document, automation, review = {"document_id": "TCDOC-x"}, {"artifacts": {}}, {"artifacts": {}}
    captured = {}
    coordinates = {
        "run_root": run_root,
        "manifest": {"project": str(project), "run_id": "b" * 32},
        "authorization": {"execution_requested": True, "policy_profile": "local-pilot-v1"},
        "attempt": {"attempt_id": "a" * 32, "module": "."},
        "module_root": project,
        "module": module,
    }

    baseline_checks = []
    monkeypatch.setattr(run_pipeline, "_revalidate_execution_baseline", lambda *_args: baseline_checks.append(_args))
    monkeypatch.setattr(run_tests, "build_closed_execution_request", lambda *_args: (request, object()))
    monkeypatch.setattr(run_tests, "validate_execution_eligibility", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("tools.pilot_state.claim_execution_start", lambda *args: captured.update(claim=args))
    published = {}

    def publish(run_root_value, attempt_id, kind, facts):
        published.update(run_root=run_root_value, attempt_id=attempt_id, kind=kind, facts=facts)
        return {"digest": "sha256:" + "b" * 64, "record": {"payload": facts["payload"]}}

    def readback(run_root_value, attempt_id, kind, event_type):
        assert (run_root_value, attempt_id, kind, event_type) == (run_root, "a" * 32, "execution-receipt", "ARTIFACT_READ_BACK")
        return {"record": {"payload": {"verdict": "PASS"}}}

    monkeypatch.setattr("tools.pilot_state.publish_attempt_receipt", publish)
    monkeypatch.setattr("tools.pilot_state.read_attempt_receipt", readback)
    monkeypatch.setattr(
        "tools.pilot_state.recover_execution_receipt_events",
        lambda *_args: {"digest": "sha256:" + "b" * 64, "record": {"payload": {"verdict": "PASS"}}},
    )

    def v5(request_value, authorization, *_args, **kwargs):
        captured.update(request=request_value, authorization=authorization, kwargs=kwargs)
        kwargs["on_execution_start"](request_value)
        assert kwargs["post_execution_check"]() is None
        return {"verdict": "PASS"}

    monkeypatch.setattr(run_tests, "run_tests_v5", v5)
    closure_result = {
        "result": {
            "verification": "PASS", "accepted": True, "finalization_valid": True,
            "digest": "sha256:" + "c" * 64,
        },
        "exit_code": 0,
    }

    def close_execution(run_root_value, attempt_id):
        captured.update(closure=(run_root_value, attempt_id))
        return closure_result

    monkeypatch.setattr("tools.finalize_attempt.finalize_durable_execution_attempt", close_execution)
    result = run_pipeline._execute(
        project, coordinates, project, "python", "local", document, automation, review,
        {"sealed": "authorization"}, {"controller": "boundary"}, {"durable": "delta"},
    )

    assert result == 0
    assert captured["request"] is request
    assert captured["claim"][:2] == (run_root, "a" * 32)
    assert captured["kwargs"]["host_isolation_receipt"] == {"controller": "boundary"}
    assert captured["kwargs"]["generated_delta_receipt"] == {"durable": "delta"}
    assert captured["kwargs"]["run_root"] == run_root
    assert captured["kwargs"]["attempt_id"] == "a" * 32
    assert callable(captured["kwargs"]["on_execution_start"])
    assert callable(captured["kwargs"]["post_execution_check"])
    assert captured["closure"] == (run_root, "a" * 32)
    assert len(baseline_checks) == 3  # preflight, exact start gate, post-process
    assert published == {
        "run_root": run_root,
        "attempt_id": "a" * 32,
        "kind": "execution-receipt",
        "facts": {"payload": {"verdict": "PASS"}},
    }


def test_controller_rechecks_durable_delta_before_claim_and_never_starts_mutated_file(tmp_path: Path, monkeypatch) -> None:
    from tools import run_pipeline, run_tests
    from tools.pilot_state import derive_state, read_attempt_receipt

    project, coordinates, document, automation, review, authorization, facts = _controller_inputs(tmp_path)
    started = []
    original_v5 = run_tests.run_tests_v5

    def mutate_at_controller_gate(*args, **kwargs):
        original_gate = kwargs["on_execution_start"]

        def mutate_then_gate(request):
            (project / "tests" / "test_generated.py").write_text("def test_selected(): assert False\n", encoding="utf-8")
            return original_gate(request)

        return original_v5(*args, **(kwargs | {"on_execution_start": mutate_then_gate}))

    monkeypatch.setattr(run_tests, "run_tests_v5", mutate_at_controller_gate)
    monkeypatch.setattr("tools.run_tests._invoke_closed_request", lambda *_args, **_kwargs: started.append(True))

    result = run_pipeline._execute(
        project, coordinates, project, facts["language"], "local", document, automation, review,
        authorization, facts["boundary"], facts["delta"],
    )

    receipt = read_attempt_receipt(
        coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert result == 2
    assert started == []
    assert receipt["verdict"] == "NOT_RUNNABLE"
    assert receipt["execution"]["request_digest"] is not None
    assert not [event for event in derive_state(coordinates["run_root"])["events"] if event["event_type"] == "EXECUTION_STARTED"]


def test_controller_publishes_durable_not_runnable_receipt_for_request_construction_rejection(tmp_path: Path, monkeypatch) -> None:
    from tools import run_pipeline, run_tests
    from tools.pilot_state import derive_state, read_attempt_receipt, read_terminal_result
    from tools.run_tests import RunnerInputError

    project, coordinates, document, automation, review, authorization, facts = _controller_inputs(tmp_path)
    monkeypatch.setattr(
        run_tests,
        "build_closed_execution_request",
        lambda *_args: (_ for _ in ()).throw(RunnerInputError("RUNNER_REQUEST", [])),
    )

    result = run_pipeline._execute(
        project, coordinates, project, facts["language"], "local", document, automation, review,
        authorization, facts["boundary"], facts["delta"],
    )

    receipt = read_attempt_receipt(
        coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert result == 2
    assert receipt["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_REQUEST" for row in receipt["diagnostics"])
    assert not [event for event in derive_state(coordinates["run_root"])["events"] if event["event_type"] == "EXECUTION_STARTED"]
    terminal = read_terminal_result(coordinates["run_root"], coordinates["attempt"]["attempt_id"])
    assert terminal["verification"] == "NOT_RUNNABLE" and terminal["accepted"] is False
    dispositions = read_attempt_receipt(
        coordinates["run_root"], coordinates["attempt"]["attempt_id"], "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert [row["disposition"] for row in dispositions["files"]] == ["CLEANED"]


def test_controller_baseline_drift_preserves_exact_closed_request_in_not_runnable_receipt(tmp_path: Path) -> None:
    from tools import run_pipeline, run_tests
    from tools.execution_adapters import request_digest
    from tools.pilot_state import read_attempt_receipt

    project, coordinates, document, automation, review, authorization, facts = _controller_inputs(tmp_path)
    request, _compatibility = run_tests.build_closed_execution_request(
        project, facts["language"], coordinates["module"], document, automation,
    )
    (project / "src" / "sample.py").write_text("def combine(left, right): return left + right\n", encoding="utf-8")

    result = run_pipeline._execute(
        project, coordinates, project, facts["language"], "local", document, automation, review,
        authorization, facts["boundary"], facts["delta"],
    )

    receipt = read_attempt_receipt(
        coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert result == 2
    assert receipt["verdict"] == "NOT_RUNNABLE"
    assert receipt["execution"]["request_digest"] == request_digest(request)
    assert receipt["execution"]["baseline_digest"] == coordinates["attempt"]["baseline_digest"]


def test_cmd_exec_missing_carrier_is_controller_error_and_leaves_attempt_resumable(tmp_path: Path, capsys) -> None:
    from tools.pilot_state import derive_state, read_attempt_receipt
    from tools.run_pipeline import cmd_exec

    project, coordinates, _document, _automation, _review, _authorization, _facts = _controller_inputs(tmp_path)
    args = SimpleNamespace(
        project=str(project), run=coordinates["manifest"]["run_id"], run_root=coordinates["run_root"],
        attempt_id=coordinates["attempt"]["attempt_id"], module="python-pytest", language=None,
        target=None, executor="local",
        canonical_document=str(project / "missing-canonical.json"),
        automation_artifact=str(project / "missing-automation.json"),
        autotest_review=str(project / "missing-review.json"),
        authorization_receipt=str(project / "missing-authorization.json"),
        host_isolation_receipt=str(project / "missing-isolation.json"),
        generated_delta_receipt=str(project / "missing-delta.json"),
    )

    result = cmd_exec(args)

    assert result == 2
    capsys.readouterr()
    from tools.run_pipeline import build_parser

    omitted = build_parser().parse_args(["exec", "--project", str(project), "--run", args.run])
    before = derive_state(coordinates["run_root"])
    assert cmd_exec(omitted) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["reason"] == "RUNNER_INPUT"
    assert error["message"] == (
        "initial execution requires: --canonical-document, --automation-artifact, --autotest-review, "
        "--authorization-receipt, --host-isolation-receipt, --generated-delta-receipt"
    )
    assert derive_state(coordinates["run_root"]) == before
    with pytest.raises(ValueError, match="missing execution-receipt"):
        read_attempt_receipt(
            coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK",
        )
    attempt = next(
        row for row in derive_state(coordinates["run_root"])["attempts"]
        if row["attempt_id"] == coordinates["attempt"]["attempt_id"]
    )
    assert attempt["state"] != "TERMINAL"


def test_cmd_exec_resumes_read_back_execution_receipt_without_second_executor_call(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest

    run_root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    args, coordinates, carriers, inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    attempt = coordinates["attempt"]
    run = {"manifest": coordinates["manifest"], "authorization": coordinates["authorization"]}
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    run_pipeline._publish_execution_receipt(coordinates, report)
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("executor must not run on closure resume")),
    )

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 0
    assert output["finalization"] == "complete" and output["accepted"] is True
    assert terminal["verification"] == "PASS" and terminal["accepted"] is True
    assert sum(event["event_type"] == "EXECUTION_STARTED" for event in pilot_state.derive_state(run_root)["events"]) == 1

    before = pilot_state.derive_state(run_root)
    missing = {
        name: str(inputs / f"missing-{name}.json")
        for name in carriers
    }
    terminal_args = SimpleNamespace(
        project=attempt["project"], run=run["manifest"]["run_id"],
        module=None, language=None, target=None, executor="local", **missing,
    )

    second_code = run_pipeline.cmd_exec(terminal_args)

    second_output = json.loads(capsys.readouterr().out)
    assert second_code == 0
    assert second_output["verdict"] == "PASS" and second_output["accepted"] is True
    assert pilot_state.derive_state(run_root) == before


def test_cmd_exec_recovers_installed_execution_receipt_binding_without_rerun(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest

    run_root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    args, coordinates, carriers, inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    original_append = pilot_state.append_event

    def interrupt_execution_readback(root, event_type, **kwargs):
        slot = Path(root) / "execution-receipts" / f"{attempt_id}.json"
        if event_type == "ARTIFACT_READ_BACK" and slot.exists():
            receipt = json.loads(slot.read_text(encoding="utf-8"))
            if kwargs.get("artifact_digest") == receipt.get("digest"):
                raise RuntimeError("interrupt before execution readback")
        return original_append(root, event_type, **kwargs)

    monkeypatch.setattr(pilot_state, "append_event", interrupt_execution_readback)
    with pytest.raises(RuntimeError, match="interrupt before execution readback"):
        run_pipeline._publish_execution_receipt(coordinates, report)
    monkeypatch.setattr(pilot_state, "append_event", original_append)
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("executor must not rerun")),
    )
    for name in carriers:
        setattr(args, name, str(inputs / f"missing-{name}.json"))

    code = run_pipeline.main([
        "exec",
        "--project", str(coordinates["attempt"]["project"]),
        "--run", str(coordinates["manifest"]["run_id"]),
    ])

    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["accepted"] is True, output
    assert pilot_state.read_terminal_result(run_root, attempt_id)["verification"] == "PASS"
    receipt = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )
    assert receipt["record"]["payload"] == report


def test_cmd_exec_turns_started_execution_without_receipt_into_unknown_without_rerun(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest

    run_root, attempt_id, _report, request, durable = _execution_facts(tmp_path)
    args, _coordinates, carriers, inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("interrupted execution must not rerun")),
    )
    for name in carriers:
        setattr(args, name, str(inputs / f"missing-{name}.json"))

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    try:
        terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    except KeyError:
        pytest.fail(f"execution did not terminalize: {output}")
    execution = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    dispositions = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert code == 2 and output["verdict"] == "UNKNOWN"
    assert (terminal["completion"], terminal["verification"], terminal["reason_code"]) == (
        "PARTIAL", "UNKNOWN", "EXECUTION_UNKNOWN",
    )
    assert execution["payload"]["verdict"] == "UNKNOWN"
    assert execution["payload"]["diagnostics"][0]["code"] == "EXECUTION_RESULT_LOST"
    assert [row["disposition"] for row in dispositions["files"]] == ["PRESERVED_EXECUTION_UNKNOWN"]
    assert sum(
        event["event_type"] == "EXECUTION_STARTED"
        for event in pilot_state.derive_state(run_root)["events"]
    ) == 1


def test_started_resume_never_falls_back_from_corrupt_durable_execution_inputs(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest

    run_root, attempt_id, _report, request, durable = _execution_facts(tmp_path)
    args, _coordinates, _carriers, _inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    project_file = Path(durable["trace_inputs"]["automation_artifact"]["artifacts"]["generated_files"][0]["path"])
    receipt_path = run_root / "execution-inputs" / f"{attempt_id}.json"
    receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("corrupt durable carriers must not execute")),
    )

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["reason"] == "FINALIZATION"
    with pytest.raises(KeyError):
        pilot_state.read_terminal_result(run_root, attempt_id)
    assert not (run_root / "disposition-receipts" / f"{attempt_id}.json").exists()
    assert (Path(args.project) / project_file).is_file()


def test_cmd_exec_resumes_prestart_not_runnable_receipt_without_inventing_execution_start(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import pilot_state, run_pipeline, run_tests
    from tools.finalize_attempt import finalize_durable_execution_attempt

    project, coordinates, document, automation, review, _authorization, facts = _controller_inputs(tmp_path)
    run_root = coordinates["run_root"]
    attempt_id = coordinates["attempt"]["attempt_id"]
    durable = {
        "trace_inputs": {
            "canonical_document": document,
            "automation_artifact": automation,
            "autotest_review": review,
        },
        "delta": facts["delta"],
    }
    args, _resume_coordinates, _carriers, _inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    monkeypatch.setattr(
        "tools.finalize_attempt.finalize_durable_execution_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt before prestart finalization")),
    )
    assert run_pipeline._reject_prestart_execution(
        coordinates, "RUNNER_REQUEST", "request cannot be built",
        automation_artifact=automation, autotest_review=review,
    ) == 2
    assert not any(
        event["event_type"] == "EXECUTION_STARTED"
        for event in pilot_state.derive_state(run_root)["events"]
    )
    monkeypatch.setattr(
        "tools.finalize_attempt.finalize_durable_execution_attempt",
        finalize_durable_execution_attempt,
    )
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("prestart receipt must not execute")),
    )
    capsys.readouterr()

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 2 and output["verdict"] == "NOT_RUNNABLE"
    assert terminal["verification"] == "NOT_RUNNABLE" and terminal["accepted"] is False
    assert not any(
        event["event_type"] == "EXECUTION_STARTED"
        for event in pilot_state.derive_state(run_root)["events"]
    )


def test_cmd_exec_terminalizes_read_back_pass_as_unaccepted_when_resume_baseline_drifted(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest

    run_root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    args, coordinates, _carriers, _inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    run_pipeline._publish_execution_receipt(coordinates, report)
    project = Path(coordinates["attempt"]["project"])
    (project / ".skillsrc").write_text(
        (project / ".skillsrc").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("drifted resume must not rerun")),
    )

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    try:
        terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    except KeyError:
        pytest.fail(f"drifted receipt did not terminalize: {output}")
    dispositions = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert code == 2 and output["accepted"] is False
    assert terminal["verification"] == "PASS" and terminal["operational_reliable"] is False
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert [row["disposition"] for row in dispositions["files"]] == ["CLEANED"]


def test_late_resume_drift_preserves_an_already_frozen_retained_disposition(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    args, coordinates, _carriers, _inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    run_pipeline._publish_execution_receipt(coordinates, report)
    original_publish_closure = pilot_state.publish_closure_artifact

    def interrupt_before_pretrace(root, selected_attempt_id, kind, artifact):
        if kind == "pre_finalization_trace":
            raise RuntimeError("interrupt after dispositions")
        return original_publish_closure(root, selected_attempt_id, kind, artifact)

    monkeypatch.setattr(pilot_state, "publish_closure_artifact", interrupt_before_pretrace)
    with pytest.raises(RuntimeError, match="interrupt after dispositions"):
        finalize_durable_execution_attempt(run_root, attempt_id)
    first_disposition = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    assert [row["disposition"] for row in first_disposition["payload"]["files"]] == ["RETAINED"]

    project = Path(coordinates["attempt"]["project"])
    generated = project / durable["delta"]["files"][0]["path"]
    assert generated.is_file()
    (project / ".skillsrc").write_text(
        (project / ".skillsrc").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pilot_state, "publish_closure_artifact", original_publish_closure)
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resume must not rerun")),
    )
    capsys.readouterr()

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    final_disposition = pilot_state.read_attempt_receipt(
        run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    assert code == 2 and output["accepted"] is False
    assert terminal["verification"] == "PASS" and terminal["operational_reliable"] is False
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert final_disposition["digest"] == first_disposition["digest"]
    assert [row["disposition"] for row in final_disposition["payload"]["files"]] == ["RETAINED"]
    assert generated.is_file()


def test_resume_after_pretrace_records_late_drift_without_rewriting_closure(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from tests.test_execution_receipt import _execution_facts
    from tools import finalize_attempt, pilot_state, run_pipeline, run_tests
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt

    run_root, attempt_id, report, request, durable = _execution_facts(tmp_path)
    args, coordinates, _carriers, _inputs = _resume_cli_context(
        tmp_path, run_root, attempt_id, durable,
    )
    pilot_state.claim_execution_start(run_root, attempt_id, request_digest(request))
    run_pipeline._publish_execution_receipt(coordinates, report)
    original_verify = finalize_attempt.verify_finalization
    monkeypatch.setattr(
        finalize_attempt,
        "verify_finalization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt after pretrace")),
    )
    with pytest.raises(RuntimeError, match="interrupt after pretrace"):
        finalize_durable_execution_attempt(run_root, attempt_id)
    pretrace = pilot_state.read_closure_artifact_if_present(
        run_root, attempt_id, "pre_finalization_trace",
    )
    assert pretrace is not None and pretrace["resume_validation_digest"] is None

    project = Path(coordinates["attempt"]["project"])
    (project / ".skillsrc").write_text(
        (project / ".skillsrc").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(finalize_attempt, "verify_finalization", original_verify)
    monkeypatch.setattr(
        run_tests, "run_tests_v5",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resume must not rerun")),
    )
    capsys.readouterr()

    code = run_pipeline.cmd_exec(args)

    output = json.loads(capsys.readouterr().out)
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 2 and output["accepted"] is False
    assert terminal["accepted"] is False and terminal["verification"] == "PASS"
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert terminal["finalization_valid"] is True
    assert (run_root / "resume-validations" / f"{attempt_id}.json").is_file()
    assert pilot_state.read_closure_artifact_if_present(
        run_root, attempt_id, "pre_finalization_trace",
    )["digest"] == pretrace["digest"]


@pytest.mark.parametrize(("finalization_installed", "must_revalidate"), [(False, True), (True, False)])
def test_cmd_exec_uses_immutable_finalization_installation_as_validation_frontier(
    tmp_path: Path, monkeypatch, capsys, finalization_installed: bool, must_revalidate: bool,
) -> None:
    from tools import pilot_state, run_pipeline

    attempt_id = "a" * 32
    run_root = tmp_path / "run"
    (run_root / "execution-receipts").mkdir(parents=True)
    (run_root / "execution-receipts" / f"{attempt_id}.json").write_text("{}", encoding="utf-8")
    coordinates = {
        "run_root": run_root,
        "manifest": {"run_id": "b" * 32},
        "authorization": {},
        "attempt": {"attempt_id": attempt_id},
        "module_root": tmp_path,
    }
    args = SimpleNamespace(
        project=str(tmp_path), run="b" * 32,
    )
    monkeypatch.setattr(run_pipeline, "_validated_execution_coordinates", lambda *_args: coordinates)
    monkeypatch.setattr(pilot_state, "read_terminal_result", lambda *_args: (_ for _ in ()).throw(KeyError()))
    monkeypatch.setattr(pilot_state, "derive_state", lambda *_args: {"events": []})
    monkeypatch.setattr(pilot_state, "read_effective_canonical", lambda *_args: {"document": {}})
    monkeypatch.setattr(
        pilot_state, "read_execution_inputs",
        lambda *_args: {"automation_artifact": {}, "autotest_review": {}},
    )
    monkeypatch.setattr(
        pilot_state, "recover_execution_receipt_events",
        lambda *_args: {"record": {"payload": {"verdict": "PASS"}}, "digest": "sha256:" + "c" * 64},
    )
    monkeypatch.setattr(
        pilot_state, "read_closure_artifact_if_present",
        lambda *_args: {"digest": "sha256:" + "d" * 64} if finalization_installed else None,
    )
    monkeypatch.setattr(pilot_state, "read_resume_validation_if_present", lambda *_args: None)
    monkeypatch.setattr(run_pipeline, "_reconstruct_started_request", lambda *_args: (object(), "python"))
    validations = []
    publications = []

    def validate(*_args):
        validations.append(True)
        raise run_pipeline.HostStop("BASELINE_DRIFT", "changed")

    monkeypatch.setattr(run_pipeline, "_revalidate_execution_baseline", validate)
    monkeypatch.setattr(
        pilot_state, "publish_resume_validation",
        lambda *_args: publications.append(_args[-1]),
    )
    monkeypatch.setattr(
        "tools.finalize_attempt.finalize_durable_execution_attempt",
        lambda *_args: {
            "result": {"verification": "PASS", "accepted": finalization_installed},
            "exit_code": 0 if finalization_installed else 2,
        },
    )

    code = run_pipeline.cmd_exec(args)

    assert bool(validations) is must_revalidate
    assert publications == (["BASELINE_DRIFT"] if must_revalidate else [])
    assert code == (2 if must_revalidate else 0)
    assert json.loads(capsys.readouterr().out)["accepted"] is finalization_installed
