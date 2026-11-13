from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools import exec_commands

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in


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


def test_runner_v5_environment_receipt_has_safe_labels_never_values() -> None:
    from tools.run_tests import _report
    from tools.schema_validation import schema_diagnostics

    report = _report(
        "NOT_RUNNABLE", PACK_ROOT, "python",
        {"document_id": "TCDOC-run", "revision": 1, "source_digest": "sha256:" + "a" * 64, "effective_bundle_receipt_digest": "sha256:" + "d" * 64},
        "sha256:" + "b" * 64, "sha256:" + "c" * 64,
        diagnostics=[{"path": "/authorization", "code": "RUNNER_AUTHORIZATION", "message": "Execution was not authorized."}],
    )

    assert report["schema_version"] == "5.0.0"
    assert report["environment"]["safe_key_labels"] == []
    assert schema_diagnostics(report, (PACK_ROOT / "schemas/run-tests-output.schema.json"), PACK_ROOT) == []


def test_runner_v5_unknown_preserves_non_authoritative_timeout_receipt() -> None:
    from tools.run_tests import ProcessOutcome, _process_row, _report, validate_execution_evidence

    source = {"document_id": "TCDOC-run", "revision": 1, "source_digest": "sha256:" + "a" * 64, "effective_bundle_receipt_digest": "sha256:" + "d" * 64}
    timeout = ProcessOutcome(124, "", "", "TIMEOUT")
    report = _report(
        "UNKNOWN", PACK_ROOT, "python", source, "sha256:" + "b" * 64, "sha256:" + "c" * 64,
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


def test_doctor_marks_company_runner_non_core_and_no_language_broadly_verified() -> None:
    from tools.doctor import inspect_environment

    report = inspect_environment(PACK_ROOT)

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
        assert "tools.company_runner" not in (PACK_ROOT / relative).read_text(encoding="utf-8")


def test_the_commands_are_local_only_and_take_the_run_not_its_coordinates() -> None:
    from tools.run_pipeline import build_parser

    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices
    for command in ("materialize", "ingest"):
        args = parser.parse_args([command, "--project", "project", "--run", "a" * 32])
        assert not hasattr(args, "run_root") and not hasattr(args, "attempt_id") and not hasattr(args, "executor"), command
        assert not any("company" in str(action.choices) for action in choices[command]._actions if action.choices)
    # the old command still parses, so that its answer (EXEC_REPLACED) can name the way out
    old = parser.parse_args(["exec", "--project", "project", "--run", "a" * 32, "--canonical-document", "canonical.json", "--generated-delta-receipt", "delta.json"])
    assert old.executor == "local"


def test_execution_coordinates_reject_a_legacy_or_foreign_run_label_before_artifact_loading(tmp_path: Path) -> None:
    """A caller cannot combine an arbitrary legacy --run with a durable pilot root."""
    from tools.pilot_state import create_run
    from tools.run_pipeline import HostStop
    from tools.exec_commands import _validated_execution_coordinates

    create_run(
        tmp_path,
        "local-pilot-v1",
        {"request_id": "coordinate-check", "execution_requested": True},
    )

    with pytest.raises(HostStop) as error:
        _validated_execution_coordinates(tmp_path, "legacy-slug/legacy-attempt")
    assert error.value.code == "EXECUTION_COORDINATES"


def test_materialize_and_ingest_reject_unbound_coordinates_before_reading_anything_else(tmp_path: Path, capsys) -> None:
    from tests.p4_flow import flow_args
    from tools.pilot_state import create_run

    create_run(
        tmp_path,
        "local-pilot-v1",
        {"request_id": "coordinate-command", "execution_requested": True},
    )
    for command in (exec_commands.cmd_materialize, exec_commands.cmd_ingest):
        args = flow_args(tmp_path, Path("legacy-slug/legacy-attempt"))
        args.run = "legacy-slug/legacy-attempt"
        assert command(args) == 2
        assert json.loads(capsys.readouterr().out)["reason"] == "EXECUTION_COORDINATES"


def _the_flow_up_to_the_report(tmp_path: Path, capsys):
    """A project with the static review accepted, ``materialize`` done and the printed command run by a person: only ``ingest`` is left."""
    from tests.p4_flow import human_run, materialize
    from tests.test_exec_prepares import _project

    project, run_root, attempt_id = _project(tmp_path)
    printed = materialize(project, run_root, capsys)
    assert human_run(project, printed).returncode == 0
    return project, run_root, attempt_id, printed


def _events(run_root: Path, attempt_id: str, event_type: str) -> list[dict]:
    from tools import pilot_state

    return [event for event in pilot_state.derive_state(run_root)["events"] if event["event_type"] == event_type and event.get("attempt_id") == attempt_id]


def test_ingest_claims_the_execution_once_from_the_admission_checks_the_baseline_three_times_and_publishes_one_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import ingest
    from tools import pilot_state
    from tools.execution_adapters import request_digest
    from tools.pilot_state import read_attempt_receipt

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    baseline_checks, claims, closures, published = [], [], [], []
    real_revalidate, real_claim, real_publish = exec_commands._revalidate_execution_baseline, pilot_state.claim_execution_start, exec_commands._publish_result_receipt
    monkeypatch.setattr(exec_commands, "_revalidate_execution_baseline", lambda *args: baseline_checks.append(args) or real_revalidate(*args))
    monkeypatch.setattr(pilot_state, "claim_execution_start", lambda *args: claims.append(args) or real_claim(*args))
    monkeypatch.setattr(exec_commands, "_publish_result_receipt", lambda coordinates, report: published.append(dict(report)) or real_publish(coordinates, report))
    import tools.finalize_attempt as finalize

    real_finalize = finalize.finalize_durable_execution_attempt
    monkeypatch.setattr(finalize, "finalize_durable_execution_attempt", lambda *args: closures.append(args) or real_finalize(*args))

    code, output = ingest(project, run_root, capsys)

    assert code == 0 and output["verdict"] == "PASS" and output["accepted"] is True, output
    assert len(baseline_checks) == 3  # preflight (the closed request), exact start gate, after the report was held against the attempt
    assert len(claims) == 1 and claims[0][:2] == (run_root, attempt_id)
    assert claims[0][2] == request_digest(baseline_checks[0][1])
    assert closures == [(run_root, attempt_id)]
    assert len(published) == 1 and published[0]["verdict"] == "PASS" and published[0]["execution_source"]["run_by"] == "human"
    assert read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"] == published[0]
    assert len(_events(run_root, attempt_id, "EXECUTION_STARTED")) == 1


def test_a_file_changed_between_the_checks_and_the_claim_is_never_taken_as_the_reviewed_code(tmp_path: Path, monkeypatch, capsys) -> None:
    """Formerly: the durable delta is checked again before the claim and a mutated file is never started.  Nothing is started now; the file that
    changes in the window of the claim makes the result UNKNOWN (SOURCE_CHANGED), never a PASS of code that was not reviewed."""
    from tests.p4_flow import ingest
    from tools import pilot_state
    from tools.pilot_state import read_attempt_receipt, read_terminal_result

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    real_claim = pilot_state.claim_execution_start

    def mutate_at_the_gate(*args):
        (project / "tests" / "test_generated.py").write_text("def test_selected(): assert False\n", encoding="utf-8")
        return real_claim(*args)

    monkeypatch.setattr(pilot_state, "claim_execution_start", mutate_at_the_gate)
    code, output = ingest(project, run_root, capsys)

    receipt = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert code == 2 and output["accepted"] is False, output
    assert receipt["verdict"] == "UNKNOWN" and "SOURCE_CHANGED" in {row["kind"] for row in receipt["process_evidence"]}
    assert read_terminal_result(run_root, attempt_id)["accepted"] is False
    assert (project / "tests" / "test_generated.py").read_text(encoding="utf-8") == "def test_selected(): assert False\n"  # preserved, not cleaned


def test_a_request_that_cannot_be_built_is_a_durable_not_runnable_receipt_and_a_terminal_result(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args
    from tools import run_tests
    from tools.pilot_state import read_attempt_receipt, read_terminal_result
    from tools.run_tests import RunnerInputError

    project, coordinates, _document, _automation, _review, _authorization, _facts = _controller_inputs(tmp_path)
    run_root, attempt_id = coordinates["run_root"], coordinates["attempt"]["attempt_id"]
    monkeypatch.setattr(run_tests, "build_closed_execution_request", lambda *_args: (_ for _ in ()).throw(RunnerInputError("RUNNER_REQUEST", [])))

    result = exec_commands.cmd_materialize(flow_args(project, run_root))

    receipt = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert result == 2
    assert receipt["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_REQUEST" for row in receipt["diagnostics"])
    assert not _events(run_root, attempt_id, "EXECUTION_STARTED")
    terminal = read_terminal_result(run_root, attempt_id)
    assert terminal["verification"] == "NOT_RUNNABLE" and terminal["accepted"] is False
    dispositions = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [row["disposition"] for row in dispositions["files"]] == ["CLEANED"]


def test_baseline_drift_before_materialize_keeps_the_exact_closed_request_in_the_not_runnable_receipt(tmp_path: Path, capsys) -> None:
    from tests.p4_flow import flow_args
    from tools import run_tests
    from tools.execution_adapters import request_digest
    from tools.pilot_state import read_attempt_receipt

    project, coordinates, document, automation, _review, _authorization, facts = _controller_inputs(tmp_path)
    request, _compatibility = run_tests.build_closed_execution_request(
        project, facts["language"], coordinates["module"], document, automation,
    )
    (project / "src" / "sample.py").write_text("def combine(left, right): return left + right\n", encoding="utf-8")

    result = exec_commands.cmd_materialize(flow_args(project, coordinates["run_root"]))

    receipt = read_attempt_receipt(
        coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert result == 2
    assert receipt["verdict"] == "NOT_RUNNABLE"
    assert receipt["execution"]["request_digest"] == request_digest(request)
    assert receipt["execution"]["baseline_digest"] == coordinates["attempt"]["baseline_digest"]


def test_ingest_without_materialize_is_a_controller_error_and_leaves_the_attempt_resumable(tmp_path: Path, capsys) -> None:
    from tests.p4_flow import ingest
    from tools.pilot_state import derive_state, read_attempt_receipt

    project, coordinates, _document, _automation, _review, _authorization, _facts = _controller_inputs(tmp_path)

    code, error = ingest(project, coordinates["run_root"], capsys)

    assert code == 2 and error["status"] == "error" and error["reason"] == "INGEST_WITHOUT_MATERIALIZE", error
    with pytest.raises(ValueError, match="missing execution-receipt"):
        read_attempt_receipt(coordinates["run_root"], coordinates["attempt"]["attempt_id"], "execution-receipt", "ARTIFACT_READ_BACK")
    attempt = next(row for row in derive_state(coordinates["run_root"])["attempts"] if row["attempt_id"] == coordinates["attempt"]["attempt_id"])
    assert attempt["state"] != "TERMINAL"


def test_an_ingest_cut_after_the_receipt_is_finished_by_the_next_one_without_a_second_claim(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args, ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    real_finish = exec_commands._finish_ingest
    monkeypatch.setattr(exec_commands, "_finish_ingest", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cut after the receipt")))
    with pytest.raises(RuntimeError, match="cut after the receipt"):
        exec_commands.cmd_ingest(flow_args(project, run_root))
    capsys.readouterr()
    monkeypatch.setattr(exec_commands, "_finish_ingest", real_finish)

    code, output = ingest(project, run_root, capsys)

    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 0 and output["finalization"] == "complete" and output["accepted"] is True
    assert terminal["verification"] == "PASS" and terminal["accepted"] is True
    assert len(_events(run_root, attempt_id, "EXECUTION_STARTED")) == 1
    before = pilot_state.derive_state(run_root)
    again_code, again = ingest(project, run_root, capsys, reports=str(tmp_path / "no-such-folder"))  # a closed attempt answers from the journal
    assert again_code == 0 and again["verdict"] == "PASS" and again["accepted"] is True
    assert pilot_state.derive_state(run_root) == before


def test_an_ingest_cut_between_the_receipt_and_its_readback_event_is_recovered_with_the_same_bytes(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args, ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
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
        exec_commands.cmd_ingest(flow_args(project, run_root))
    capsys.readouterr()
    monkeypatch.setattr(pilot_state, "append_event", original_append)
    slot = run_root / "execution-receipts" / f"{attempt_id}.json"
    installed = slot.read_bytes()

    code, output = ingest(project, run_root, capsys)

    assert code == 0 and output["accepted"] is True, output
    assert pilot_state.read_terminal_result(run_root, attempt_id)["verification"] == "PASS"
    assert slot.read_bytes() == installed
    assert len(_events(run_root, attempt_id, "EXECUTION_STARTED")) == 1


def test_a_started_ingest_without_a_receipt_is_closed_unknown_by_the_statement_without_a_second_claim(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args, ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    real_publish = exec_commands._publish_result_receipt
    monkeypatch.setattr(exec_commands, "_publish_result_receipt", lambda *_args: (_ for _ in ()).throw(RuntimeError("cut after the claim")))
    with pytest.raises(RuntimeError, match="cut after the claim"):
        exec_commands.cmd_ingest(flow_args(project, run_root))
    capsys.readouterr()
    monkeypatch.setattr(exec_commands, "_publish_result_receipt", real_publish)
    assert len(_events(run_root, attempt_id, "EXECUTION_STARTED")) == 1 and not (run_root / "execution-receipts" / f"{attempt_id}.json").exists()

    code, output = ingest(project, run_root, capsys, no_reports=True, reason="RESULT_UNAVAILABLE")

    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    execution = pilot_state.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    dispositions = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert code == 2 and output["verdict"] == "UNKNOWN"
    assert (terminal["completion"], terminal["verification"], terminal["reason_code"]) == ("PARTIAL", "UNKNOWN", "EXECUTION_UNKNOWN")
    assert execution["payload"]["verdict"] == "UNKNOWN"
    assert [row["disposition"] for row in dispositions["files"]] == ["PRESERVED_EXECUTION_UNKNOWN"]
    assert len(_events(run_root, attempt_id, "EXECUTION_STARTED")) == 1


def test_a_started_ingest_never_falls_back_from_corrupt_durable_execution_inputs(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args, ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    monkeypatch.setattr(exec_commands, "_publish_result_receipt", lambda *_args: (_ for _ in ()).throw(RuntimeError("cut after the claim")))
    with pytest.raises(RuntimeError):
        exec_commands.cmd_ingest(flow_args(project, run_root))
    capsys.readouterr()
    receipt_path = run_root / "execution-inputs" / f"{attempt_id}.json"
    receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
    monkeypatch.undo()

    code, output = ingest(project, run_root, capsys)

    assert code == 2 and output["status"] == "error", output
    with pytest.raises(KeyError):
        pilot_state.read_terminal_result(run_root, attempt_id)
    assert not (run_root / "disposition-receipts" / f"{attempt_id}.json").exists()
    assert (project / "tests" / "test_generated.py").is_file()


def test_a_prestart_rejection_cut_before_its_finalization_is_finished_by_the_next_command(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import flow_args
    from tools import run_tests
    from tools.pilot_state import read_terminal_result
    from tools.run_tests import RunnerInputError

    project, coordinates, _document, _automation, _review, _authorization, _facts = _controller_inputs(tmp_path)
    run_root, attempt_id = coordinates["run_root"], coordinates["attempt"]["attempt_id"]
    monkeypatch.setattr(run_tests, "build_closed_execution_request", lambda *_args: (_ for _ in ()).throw(RunnerInputError("RUNNER_REQUEST", [])))
    monkeypatch.setattr("tools.finalize_attempt.finalize_durable_execution_attempt",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt before prestart finalization")))
    assert exec_commands.cmd_materialize(flow_args(project, run_root)) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "FINALIZATION"
    assert (run_root / "execution-receipts" / f"{attempt_id}.json").is_file()
    with pytest.raises(KeyError):
        read_terminal_result(run_root, attempt_id)
    monkeypatch.undo()
    monkeypatch.setattr(run_tests, "build_closed_execution_request", lambda *_args: (_ for _ in ()).throw(RunnerInputError("RUNNER_REQUEST", [])))

    code = exec_commands.cmd_materialize(flow_args(project, run_root))

    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output.get("verdict") == "NOT_RUNNABLE", output
    terminal = read_terminal_result(run_root, attempt_id)
    assert terminal["verification"] == "NOT_RUNNABLE" and terminal["accepted"] is False
    assert not _events(run_root, attempt_id, "EXECUTION_STARTED")


def _cut_after_the_receipt(project, run_root, monkeypatch, capsys) -> None:
    """The ingest that installed its receipt and was cut before the attempt was closed."""
    from tests.p4_flow import flow_args

    real_finish = exec_commands._finish_ingest
    monkeypatch.setattr(exec_commands, "_finish_ingest", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cut after the receipt")))
    with pytest.raises(RuntimeError, match="cut after the receipt"):
        exec_commands.cmd_ingest(flow_args(project, run_root))
    capsys.readouterr()
    monkeypatch.setattr(exec_commands, "_finish_ingest", real_finish)


def _touch_skillsrc(project: Path) -> None:
    (project / ".skillsrc").write_text((project / ".skillsrc").read_text(encoding="utf-8") + "\n", encoding="utf-8")


def test_a_pass_read_back_before_the_close_is_unaccepted_when_the_baseline_drifted_meanwhile(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    _cut_after_the_receipt(project, run_root, monkeypatch, capsys)
    _touch_skillsrc(project)

    code, output = ingest(project, run_root, capsys)

    try:
        terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    except KeyError:
        pytest.fail(f"drifted receipt did not terminalize: {output}")
    dispositions = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert code == 2 and output["accepted"] is False
    assert terminal["verification"] == "PASS" and terminal["operational_reliable"] is False
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert [row["disposition"] for row in dispositions["files"]] == ["CLEANED"]


def test_a_test_file_changed_after_the_cut_closes_the_recorded_pass_as_not_accepted_and_is_not_a_changed_request(tmp_path: Path, monkeypatch, capsys) -> None:
    """The branch of a recorded result reads the journal and the baseline, never the test files: a file of the tests changed (or formatted) between
    the receipt and the next ingest must not turn that ingest into INGEST_REQUEST_CHANGED / INGEST_TESTS_CHANGED and leave the attempt open."""
    from tests.p4_flow import ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    _cut_after_the_receipt(project, run_root, monkeypatch, capsys)
    generated = project / "tests" / "test_generated.py"
    generated.write_text(generated.read_text(encoding="utf-8") + "\n# formatted by a person\n", encoding="utf-8")
    _touch_skillsrc(project)

    code, output = ingest(project, run_root, capsys)

    assert output.get("reason") not in {"INGEST_REQUEST_CHANGED", "INGEST_TESTS_CHANGED"}, output
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 2 and output["accepted"] is False and terminal["accepted"] is False
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}, terminal
    assert generated.is_file()  # the person's change is theirs: nothing is cleaned over it


def test_late_resume_drift_preserves_an_already_frozen_retained_disposition(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import ingest
    from tools import pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    original_publish_closure = pilot_state.publish_closure_artifact

    def interrupt_before_pretrace(root, selected_attempt_id, kind, artifact):
        if kind == "pre_finalization_trace":
            raise RuntimeError("interrupt after dispositions")
        return original_publish_closure(root, selected_attempt_id, kind, artifact)

    monkeypatch.setattr(pilot_state, "publish_closure_artifact", interrupt_before_pretrace)
    code, output = ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] == "FINALIZATION", output
    first_disposition = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]
    assert [row["disposition"] for row in first_disposition["payload"]["files"]] == ["RETAINED"]
    generated = project / "tests" / "test_generated.py"
    assert generated.is_file()
    _touch_skillsrc(project)
    monkeypatch.setattr(pilot_state, "publish_closure_artifact", original_publish_closure)

    code, output = ingest(project, run_root, capsys)

    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    final_disposition = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]
    assert code == 2 and output["accepted"] is False
    assert terminal["verification"] == "PASS" and terminal["operational_reliable"] is False
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert final_disposition["digest"] == first_disposition["digest"]
    assert [row["disposition"] for row in final_disposition["payload"]["files"]] == ["RETAINED"]
    assert generated.is_file()


def test_resume_after_pretrace_records_late_drift_without_rewriting_closure(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.p4_flow import ingest
    from tools import finalize_attempt, pilot_state

    project, run_root, attempt_id, _printed = _the_flow_up_to_the_report(tmp_path, capsys)
    original_verify = finalize_attempt.verify_finalization
    monkeypatch.setattr(finalize_attempt, "verify_finalization", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("interrupt after pretrace")))
    code, output = ingest(project, run_root, capsys)
    assert code == 2 and output["reason"] == "FINALIZATION", output
    pretrace = pilot_state.read_closure_artifact_if_present(run_root, attempt_id, "pre_finalization_trace")
    assert pretrace is not None and pretrace["resume_validation_digest"] is None
    _touch_skillsrc(project)
    monkeypatch.setattr(finalize_attempt, "verify_finalization", original_verify)

    code, output = ingest(project, run_root, capsys)

    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert code == 2 and output["accepted"] is False
    assert terminal["accepted"] is False and terminal["verification"] == "PASS"
    assert terminal["reason_code"] in {"BASELINE_DRIFT", "SKILLSRC_DRIFT"}
    assert terminal["finalization_valid"] is True
    assert (run_root / "resume-validations" / f"{attempt_id}.json").is_file()
    assert pilot_state.read_closure_artifact_if_present(run_root, attempt_id, "pre_finalization_trace")["digest"] == pretrace["digest"]


@pytest.mark.parametrize(("finalization_installed", "must_revalidate"), [(False, True), (True, False)])
def test_the_installation_of_the_finalization_is_the_frontier_of_the_late_drift_check(
    tmp_path: Path, monkeypatch, finalization_installed: bool, must_revalidate: bool,
) -> None:
    """The check of a result recorded before the close asks the baseline again only while the finalization is not installed, and says a drift once."""
    from tools import pilot_state, run_pipeline

    attempt_id = "a" * 32
    coordinates = {"run_root": tmp_path / "run", "manifest": {"run_id": "b" * 32}, "attempt": {"attempt_id": attempt_id}, "module_root": tmp_path}
    monkeypatch.setattr(pilot_state, "read_closure_artifact_if_present", lambda *_args: {"digest": "sha256:" + "d" * 64} if finalization_installed else None)
    monkeypatch.setattr(pilot_state, "read_resume_validation_if_present", lambda *_args: None)
    validations, publications = [], []

    def validate(*_args):
        validations.append(True)
        raise run_pipeline.HostStop("BASELINE_DRIFT", "changed")

    monkeypatch.setattr(exec_commands, "_revalidate_execution_baseline", validate)
    monkeypatch.setattr(pilot_state, "publish_resume_validation", lambda *_args: publications.append(_args[-1]))

    exec_commands._record_late_drift(coordinates, object(), {"verdict": "PASS"})
    assert bool(validations) is must_revalidate
    assert publications == (["BASELINE_DRIFT"] if must_revalidate else [])
    validations.clear()
    exec_commands._record_late_drift(coordinates, object(), {"verdict": "NOT_RUNNABLE"})  # a rejection before the start has nothing to hold
    assert validations == []
