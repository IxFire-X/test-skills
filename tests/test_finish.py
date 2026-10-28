"""FaT-4a: exec without the six carrier paths, and one closing command — run_pipeline finish."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import MODULE_PYTHON
from tests.test_project_native_pytest import _module_python, _reviewed_inputs

CARRIERS = ("canonical_document", "automation_artifact", "autotest_review", "authorization_receipt", "host_isolation_receipt", "generated_delta_receipt")


def _project(tmp_path: Path) -> tuple[Path, Path, str, dict]:
    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    values = {"canonical_document": document, "automation_artifact": artifact, "autotest_review": static_review,
              "authorization_receipt": authorization, "host_isolation_receipt": evidence, "generated_delta_receipt": delta}
    return project, run_root, attempt_id, values


def _args(project: Path, run_root: Path, **paths: str) -> SimpleNamespace:
    return SimpleNamespace(project=str(project), run=run_root.name, module="python-pytest", language=None, target=None, executor="local",
                           **{name: paths.get(name) for name in CARRIERS})


def test_exec_takes_the_carriers_from_the_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import read_terminal_result
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id, _values = _project(tmp_path)
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["verdict"] == "PASS" and output["finalization"] == "complete", output
    assert read_terminal_result(run_root, attempt_id)["accepted"] is True


def test_an_explicit_carrier_is_checked_against_the_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import cmd_exec

    project, run_root, _attempt_id, values = _project(tmp_path)
    forged = dict(values["automation_artifact"])
    forged["schema_version"] = "9.9.9"
    path = tmp_path / "automation.json"
    path.write_text(json.dumps(forged, ensure_ascii=False), encoding="utf-8")
    from tools.pilot_state import derive_state

    before = derive_state(run_root)
    code = cmd_exec(_args(project, run_root, automation_artifact=str(path)))
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["status"] == "error", output
    assert derive_state(run_root) == before


def test_exec_with_and_without_paths_gives_the_same_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import read_terminal_result
    from tools.run_pipeline import cmd_exec

    results = []
    for name, with_paths in (("a", True), ("b", False)):
        project, run_root, attempt_id, values = _project(tmp_path / name)
        paths = {}
        if with_paths:
            for key, value in values.items():
                target = tmp_path / name / f"{key}.json"
                target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                paths[key] = str(target)
        assert cmd_exec(_args(project, run_root, **paths)) == 0
        capsys.readouterr()
        terminal = read_terminal_result(run_root, attempt_id)
        results.append({key: terminal.get(key) for key in ("completion", "verification", "coverage", "accepted", "reason_code")})
    assert results[0] == results[1]


# --------------------------------------------------------------------------------------
# finish
# --------------------------------------------------------------------------------------

AXES = ("completion", "verification", "coverage", "accepted", "reason_code")


def _finish(project: Path, run_root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from tools.run_pipeline import cmd_finish

    code = cmd_finish(SimpleNamespace(project=str(project), run=run_root.name))
    return code, json.loads(capsys.readouterr().out)


def _axes(result: dict) -> dict:
    return {key: result.get(key) for key in AXES}


@pytest.mark.parametrize("verdict", ["PASS", "NOT_RUNNABLE", "UNKNOWN"])
def test_finish_closes_an_executed_attempt_like_the_manual_sequence(tmp_path: Path, capsys: pytest.CaptureFixture[str], verdict: str) -> None:
    from tests import test_durable_execution_trace as trace
    from tools.pilot_state import read_attempt_receipt
    from tools.run_pipeline import finalize_phase_one_spine

    outcomes = []
    for name in ("manual", "finish"):
        (tmp_path / name).mkdir()
        root, attempt_id, report, request, durable = trace._execution_facts(tmp_path / name)
        if verdict == "PASS":
            trace._publish_execution(root, attempt_id, report, request=request)
            facts = trace._facts(root, attempt_id, verification="PASS", retained=1, exact_target_pass=True)
        elif verdict == "NOT_RUNNABLE":
            trace._publish_execution(root, attempt_id, trace._prestart_report(root, durable))
            facts = trace._facts(root, attempt_id, verification="NOT_RUNNABLE")
        else:
            trace._publish_execution(root, attempt_id, trace._unknown_report(root, attempt_id, durable, request), request=request)
            facts = trace._facts(root, attempt_id, verification="UNKNOWN", completion="PARTIAL", reason_code="EXECUTION_UNKNOWN",
                                 prior_stage_cause="EXECUTION_UNKNOWN")
        if name == "manual":
            result = finalize_phase_one_spine(root, trace._branch(root, attempt_id, durable), durable["delta"], verification=verdict, facts=facts)["result"]
        else:
            code, output = _finish(tmp_path / name / "project", root, capsys)
            assert output["branch"] == "execution" and output["verification"] == verdict, output
            result = {key: output[key] for key in AXES}
        dispositions = read_attempt_receipt(root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        outcomes.append((_axes(result), [row["disposition"] for row in dispositions["files"]]))
    assert outcomes[0] == outcomes[1]


def _cases_only_effective(tmp_path: Path, *, lowered: bool = False) -> tuple[Path, str]:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _effective_bundle, _published_reviewer_ledger
    from tests.test_reviewer_protocol import _new_run
    from tools import pilot_state

    if lowered:
        real = pilot_state.create_run
        pilot_state.create_run = lambda project, profile, authorization: pilot_state.create_lowered_run(
            project, authorization, {"reason": "интерпретатор модуля не найден", "fix": "создайте окружение модуля"})
        try:
            run_root, attempt_id = _new_run(tmp_path)
        finally:
            pilot_state.create_run = real
    else:
        run_root, attempt_id = _new_run(tmp_path)
    document = automated_document()
    pilot_state.publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document),
                                            _published_reviewer_ledger(run_root, attempt_id, document))
    return run_root, attempt_id


def test_finish_closes_an_accepted_cases_only_attempt_like_the_manual_sequence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exit_policy import _facts
    from tools import pilot_state
    from tools.finalize_attempt import finalize_attempt

    root, attempt_id = _cases_only_effective(tmp_path / "manual")
    effective = pilot_state.read_effective_canonical(root, attempt_id)
    run_id = pilot_state.read_run(root)["manifest"]["run_id"]
    manual = finalize_attempt(
        {"run_id": run_id, "attempt_id": attempt_id, "policy_profile": "cases-only-v1", "canonical_document": effective["document"],
         "stage_causes": ["CANONICAL_COMPLETE"], "evidence": {}},
        None, project=tmp_path / "manual", verification="NOT_APPLICABLE",
        facts=_facts(run_id=run_id, attempt_id=attempt_id, coverage="FULL") | {"policy_profile": "cases-only-v1"}, run_root=root,
    )["result"]
    root, attempt_id = _cases_only_effective(tmp_path / "finish")
    code, output = _finish(tmp_path / "finish", root, capsys)
    assert output["branch"] == "no-execution" and _axes(output) == _axes(manual), output
    assert output["accepted"] is False and output["verification"] == "NOT_APPLICABLE"
    again_code, again = _finish(tmp_path / "finish", root, capsys)
    assert again["idempotent"] is True and _axes(again) == _axes(output) and again_code == code


def test_finish_closes_a_rework_branch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_reviewer_protocol import _bind_session, _completed, _session, _started, _verdict
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    session, binding, review = _session(_started(), _verdict(), _completed(), review_verdict="ТРЕБУЕТ ДОРАБОТКИ")
    run_root, attempt_id = _bind_session(tmp_path, session, binding, "cases-only-v1")
    validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id)
    code, output = _finish(tmp_path, run_root, capsys)
    assert output["reason_code"] == "REWORK" and output["accepted"] is False and output["completion"] == "PARTIAL", output


def test_finish_on_a_lowered_run_closes_cases_only_and_names_both_profiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _cases_only_effective(tmp_path, lowered=True)
    code, output = _finish(tmp_path, root, capsys)
    assert output["policy_profile"] == "cases-only-v1" and output["requested_policy_profile"] == "local-pilot-v1"
    assert output["execution_skipped"]["reason"] and output["verification"] == "NOT_APPLICABLE"


def test_finish_before_the_review_names_what_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_reviewer_protocol import _new_run
    from tools.run_pipeline import cmd_finish

    run_root, _attempt_id = _new_run(tmp_path)
    capsys.readouterr()
    # FaT-AB tier C: the refusal is printed with its next run (a HostStop could not carry next_run)
    assert cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name)) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["reason"] == "FINISH_FACT_UNDETERMINED" and "--new-run" in printed["next_run"]["command"], printed


def test_finish_closes_a_reviewer_abort_branch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_reviewer_protocol import _bind_session, _package, _session, _started
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    aborted = {"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REVIEW_CONTEXT_LIMIT"}
    session, binding, _review = _session(_started(), aborted, status="ABORTED", package=_package(1000))  # FaT-10d: over the budget open-session refuses
    run_root, attempt_id = _bind_session(tmp_path, session, binding, "cases-only-v1")
    validate_reviewer_session(session, binding, run_root=run_root, attempt_id=attempt_id)
    code, output = _finish(tmp_path, run_root, capsys)
    assert output["reason_code"] == "REVIEW_CONTEXT_LIMIT" and output["completion"] == "PARTIAL" and output["accepted"] is False, output


# --------------------------------------------------------------------------------------
# local-pilot: automation ended in a refusal
# --------------------------------------------------------------------------------------


def _blocked_document() -> dict:
    from tests.test_automation_revision_budget import automated_document

    document = automated_document()
    document["test_cases"][0]["steps"][0]["operation"] = None
    document["test_cases"][0]["steps"][0]["automation_blockers"] = [{
        "blocker_id": "BLOCK-products", "code": "UNRESOLVED_OPERATION", "field_path": "/operation",
        "reason": "Адрес сервиса не подтверждён.", "provenance": ["test"]}]
    return document


def _automation_chain(tmp_path: Path, verdicts: list[str], *, blocked: bool = False, document: dict | None = None,
                      edit_review=None) -> tuple[Path, str]:
    from tests.test_automation_revision_budget import automated_document, automation, durable_boundary, host_evidence, review
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.pilot_state import append_event, publish_model_request, publish_model_stage_artifact, read_attempt_receipt

    from tests.test_generated_delta import _effective_bundle_digest

    tmp_path.mkdir(parents=True, exist_ok=True)
    document = document or automated_document()
    bundle_digest = _effective_bundle_digest(document)
    run_root = attempt_id = None
    previous = previous_review = None
    for index, verdict in enumerate(verdicts, start=1):
        artifact = automation(document, index, predecessor=automation_sha256(previous) if previous else None,
                              correction_review=autotest_review_sha256(previous_review) if previous_review else None,
                              effective_bundle_receipt_digest=bundle_digest)
        if blocked:
            artifact["artifacts"].update(automation_status="BLOCKED", generated_files=[], generated_symbols=[],
                                         implementation_relations=[], manual_dispositions=[],
                                         diagnostics=[{"path": "/artifacts", "code": "AUTOMATION_BLOCKED", "message": "Шаг заблокирован."}])
        evidence = host_evidence(f"session-{index}", generator=f"generator-{index}", reviewer=f"reviewer-{index}")
        run_root, attempt_id, boundary = durable_boundary(
            tmp_path, artifact, f"session-{index}", evidence, run_root=run_root, attempt_id=attempt_id,
            generator=evidence["generator_invocation_id"], reviewer=evidence["reviewer_invocation_id"],
            policy_profile="local-pilot-v1", document=document)
        if verdict is None:
            return run_root, attempt_id
        reviewed = review(document, artifact, boundary, verdict, session_id=f"session-{index}")
        if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
            reviewed["artifacts"]["autotest_review"].update(corrections=[], findings=[{
                "severity": "BLOCKING", "code": "ASSERTION_MISSING", "message": "Проверка шага не реализована.",
                "evidence": ["relation"], "related_ids": ["FILE-products"]}])
        if edit_review is not None:
            edit_review(reviewed)
        stage = f"autotest-reviewer:r{index}"
        append_event(run_root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id, stage_instance_id=stage,
                     artifact_digest=automation_sha256(artifact))
        receipt = read_attempt_receipt(run_root, attempt_id, f"automation-review-boundary-r{index}", "ARTIFACT_READ_BACK")
        publish_model_request(run_root, attempt_id, stage, model_id="model-automation-reviewer",
                              invocation_id=evidence["reviewer_invocation_id"], input_digests=[automation_sha256(artifact), receipt["digest"]])
        publish_model_stage_artifact(run_root, attempt_id, stage, reviewed)
        previous, previous_review = artifact, reviewed
    return run_root, attempt_id


def _manual_refusal(run_root: Path, attempt_id: str, cause: str, *, blockers: int = 0) -> dict:
    from tools import pilot_state
    from tools.finalize_attempt import finalize_attempt

    reviewer = pilot_state.terminal_reviewer_evidence(run_root, attempt_id)
    effective = pilot_state.read_effective_canonical(run_root, attempt_id)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    facts = {
        "run_id": run_id, "attempt_id": attempt_id, "attempt_state": "TERMINAL", "policy_profile": "local-pilot-v1",
        "canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True,
        "reviewer_session_complete": reviewer["session_complete"], "authoritative_verdict": reviewer["authoritative_verdict"],
        "authoritative_verdict_count": reviewer["authoritative_verdict_count"],
        "reviewer_pre_verdict_abort": reviewer["pre_verdict_abort"], "reviewer_isolation_state": reviewer["isolation"],
        "trace_valid": True, "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "NOT_APPLICABLE", "execution_applicability": "NOT_APPLICABLE",
        "automation_accepted": False, "generated_required_count": 0, "generated_materialized_count": 0,
        "generated_retained_count": 0, "exact_target_pass": False, "operational_reliable": True,
        "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": "FULL", "reason_code": cause,
        "blocker_count": blockers, "mixed_manual_traceable": False, "prior_stage_cause": cause,
    }
    branch = {"run_id": run_id, "attempt_id": attempt_id, "policy_profile": "local-pilot-v1",
              "canonical_document": dict(effective["document"]), "stage_causes": [cause], "evidence": {}}
    return dict(finalize_attempt(branch, None, project=run_root, verification="NOT_APPLICABLE", facts=facts, run_root=run_root)["result"])


@pytest.mark.parametrize(("verdicts", "blocked", "canonical_blocker", "cause"), [
    (["AUTO_FIX_APPLIED", "ТРЕБУЕТ ДОРАБОТКИ"], False, False, "AUTOMATION_REJECTED"),
    ([None], True, True, "AUTOMATION_BLOCKED"),
    (["ПРИНЯТО"], True, False, "AUTOMATION_BLOCKED"),
])
def test_finish_closes_a_refused_automation_like_the_manual_sequence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], verdicts: list, blocked: bool, canonical_blocker: bool, cause: str,
) -> None:
    document = _blocked_document if canonical_blocker else (lambda: None)
    root, attempt_id = _automation_chain(tmp_path / "manual", verdicts, blocked=blocked, document=document())
    manual = _manual_refusal(root, attempt_id, cause, blockers=int(canonical_blocker))
    root, attempt_id = _automation_chain(tmp_path / "finish", verdicts, blocked=blocked, document=document())
    code, output = _finish(tmp_path / "finish", root, capsys)
    assert output["branch"] == "no-execution" and output["reason_code"] == cause and _axes(output) == _axes(manual), output
    assert output["accepted"] is False and code != 0


@pytest.mark.parametrize(("verdicts", "blocked", "revision"), [
    (["AUTO_FIX_APPLIED"], False, "r2"),
    ([None], True, "r1"),
    (["AUTO_FIX_APPLIED"], True, "r2"),
])
def test_finish_with_a_correction_still_possible_names_the_next_revision(
    tmp_path: Path, verdicts: list, blocked: bool, revision: str,
) -> None:
    from tools.run_pipeline import HostStop, cmd_finish

    root, _attempt_id = _automation_chain(tmp_path, verdicts, blocked=blocked)
    with pytest.raises(HostStop) as stop:
        cmd_finish(SimpleNamespace(project=str(tmp_path), run=root.name))
    assert stop.value.code == "FINISH_FACT_UNDETERMINED" and revision in str(stop.value)

