"""Wave 2 opt-in options: run-scoped consents, `.skillsrc` sections, optional result keys (W2-Р5, Р11, Р13)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from tools import pilot_state

ROOT = Path(__file__).resolve().parents[1]
STEP5_SKILLSRC = ROOT / "tests" / "fixtures" / "live-step5-20261006" / "project" / ".skillsrc"


def _schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8")))


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "README.md").write_text("x", encoding="utf-8")
    return project


def test_authorization_without_options_is_unchanged(tmp_path: Path) -> None:
    run = pilot_state.create_run(_project(tmp_path), "local-pilot-v1", {"request_id": "r1", "execution_requested": True})
    receipt = run["authorization"]
    assert receipt["schema_version"] == "1.0.0" and "mutation_requested" not in receipt and "require_driver_isolation" not in receipt


def test_mutation_consent_is_recorded_for_local_runs_only(tmp_path: Path) -> None:
    run = pilot_state.create_run(_project(tmp_path), "local-pilot-v1", {"request_id": "r1", "execution_requested": True, "mutation_requested": True})
    receipt = run["authorization"]
    assert (receipt["schema_version"], receipt["mutation_requested"]) == ("1.0.0", True)
    assert pilot_state.read_run(Path(run["run_root"]))["authorization"] == receipt
    with pytest.raises(ValueError):
        pilot_state.create_run(_project(tmp_path / "b"), "cases-only-v1", {"request_id": "r1", "execution_requested": False, "mutation_requested": True})
    with pytest.raises(ValueError):
        pilot_state.create_run(_project(tmp_path / "c"), "local-pilot-v1", {"request_id": "r1", "execution_requested": True, "mutation_requested": False})
    validator = _schema("run-authorization-receipt.schema.json")
    assert not list(validator.iter_errors(receipt))
    assert list(validator.iter_errors({**receipt, "mutation_requested": False}))  # a consent is an explicit true


def test_scan_rejects_mutation_consent_for_cases_only(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from tools import run_pipeline

    project = _project(tmp_path)
    with pytest.raises(run_pipeline.HostStop) as stop:
        run_pipeline._init_skillsrc_run(project, SimpleNamespace(profile="cases-only-v1", mutation=True))
    assert stop.value.code == "MUTATION_PROFILE"


def test_skillsrc_sections_are_optional_and_closed() -> None:
    validator = _schema("skillsrc.schema.json")
    document = yaml.safe_load(STEP5_SKILLSRC.read_text(encoding="utf-8"))
    assert not list(validator.iter_errors(document))  # existing 5.0.0 files stay valid
    extended = {**document, "schema_version": "5.1.0", "mutation": {"enabled": True, "threads": 2, "target_classes": ["net.javaguides.springboot.*"]},
                "review_runner": {"preset": "claude", "models": {"tc-reviewer": "claude-sonnet-5-5"}, "max_parallel": 4, "timeout_seconds": 900}}
    assert not list(validator.iter_errors(extended))
    for bad in (
        {**document, "review_runner": {"preset": "bash"}},  # unknown preset
        {**document, "review_runner": {"preset": "claude", "command": ["sh", "-c", "rm -rf /"]}},  # a command from a project file
        {**document, "review_runner": {"preset": "claude", "template": "{cli} -p"}},
        {**document, "mutation": {"enabled": True, "mutators": "ALL"}},
        {**document, "mutation": {"enabled": True, "target_classes": ["a.b; rm"]}},
        {**document, "mutation": {"threads": 2}},  # enabled is required
    ):
        assert list(validator.iter_errors(bad)), bad


def _facts(**extra):
    facts = {
        "run_id": "a" * 32, "attempt_id": "b" * 32, "attempt_state": "TERMINAL", "completion": "COMPLETE", "verification": "PASS", "coverage": "FULL",
        "reason_code": None, "prior_stage_cause": "EXECUTION_COMPLETE",
        "canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True, "reviewer_session_complete": True,
        "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1, "reviewer_pre_verdict_abort": False, "reviewer_isolation_state": "verified",
        "blocker_count": 0, "trace_valid": True, "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "REQUIRED", "execution_applicability": "REQUIRED", "automation_accepted": True, "generated_required_count": 1,
        "generated_materialized_count": 1, "generated_retained_count": 1, "exact_target_pass": True, "mixed_manual_traceable": False, "operational_reliable": True,
        "review_independence": "ISOLATED", "self_review_accepted": False,
    }
    facts.update(extra)
    return facts


def test_terminal_result_carries_test_strength_without_changing_acceptance() -> None:
    plain = pilot_state.terminal_result(_facts(), "local-pilot-v1")
    assert plain["schema_version"] == "1.0.0" and "test_strength" not in plain and plain["accepted"] is True
    for status in ("MEASURED", "NOT_RUNNABLE", "NOT_APPLICABLE"):
        result = pilot_state.terminal_result(_facts(test_strength=status), "local-pilot-v1")
        assert (result["schema_version"], result["test_strength"], result["evidence"]["test_strength"], result["accepted"]) == ("1.0.0", status, status, True)
        assert pilot_state._validate_result_record(result)
        assert not list(_schema("terminal-result.schema.json").iter_errors(dict(result)))
    with pytest.raises(ValueError):
        pilot_state.terminal_result(_facts(test_strength="FAST"), "local-pilot-v1")


def test_require_driver_isolation_rejects_host_declared_review() -> None:
    accepted = pilot_state.terminal_result(_facts(isolation_evidence="DRIVER_PROCESS", driver_isolation_required=True), "local-pilot-v1")
    assert accepted["accepted"] is True and accepted["isolation_evidence"] == "DRIVER_PROCESS"
    for level in ("HOST_DECLARED", "NONE"):
        result = pilot_state.terminal_result(_facts(isolation_evidence=level, driver_isolation_required=True), "local-pilot-v1")
        assert (result["accepted"], result["reason_code"]) == (False, "REVIEW_ISOLATION_UNVERIFIED")
        assert pilot_state._validate_result_record(result)
    # Without the run flag the evidence level is reported and acceptance is unchanged.
    reported = pilot_state.terminal_result(_facts(isolation_evidence="HOST_DECLARED", driver_isolation_required=False), "local-pilot-v1")
    assert (reported["accepted"], reported["isolation_evidence"]) == (True, "HOST_DECLARED")
    with pytest.raises(ValueError):
        pilot_state.terminal_result(_facts(isolation_evidence="HOST_DECLARED"), "local-pilot-v1")  # the pair is complete or absent


def _python_run(tmp_path: Path, *, mutation_section: bool, consent: bool) -> tuple[Path, dict]:
    from tests.test_review_fixes_driver import SavedModel, _drive, _project, _start

    project = _project(tmp_path, local=True)
    if mutation_section:
        skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
        skillsrc["mutation"] = {"enabled": True}
        (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    options = {"mutation": True} if consent else {}
    return project, _drive(project, _start(project, "local-pilot-v1", **options), SavedModel("local-pilot-v1"))


def test_without_consent_the_stage_never_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import mutation

    monkeypatch.setattr(mutation, "measure", lambda *_args, **_kwargs: pytest.fail("no consent: nothing may run"))
    project, done = _python_run(tmp_path, mutation_section=True, consent=False)
    result = done["result"]
    assert (result["verification"], result["accepted"], result["schema_version"]) == ("PASS", True, "1.0.0")
    assert "test_strength" not in result
    run_root = project / ".pilot-runs" / done["run_id"]
    assert not (run_root / "mutation-receipts").exists() and not run_root.with_name(run_root.name + ".driver").joinpath("mutation").exists()
    terminal = pilot_state.read_terminal_result(run_root, result["attempt_id"])
    assert "test_strength" not in terminal and "test_strength" not in terminal["evidence"]


def test_a_python_project_gets_not_applicable_without_any_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import mutation

    monkeypatch.setattr(mutation, "_RUNNER", lambda *_args, **_kwargs: pytest.fail("Python: no build tool or PIT process"))
    project, done = _python_run(tmp_path, mutation_section=True, consent=True)
    result = done["result"]
    assert (result["verification"], result["accepted"]) == ("PASS", True)  # the separate axis never changes acceptance
    assert (result["test_strength"]["status"], result["test_strength"]["reason_code"]) == ("NOT_APPLICABLE", "MUTATION_LANGUAGE_UNSUPPORTED")
    terminal = pilot_state.read_terminal_result(project / ".pilot-runs" / done["run_id"], result["attempt_id"])
    assert terminal["test_strength"] == "NOT_APPLICABLE"
    assert Path(result["paths"]["test_strength_markdown"]).is_file()


def test_consent_without_the_skillsrc_section_is_not_applicable(tmp_path: Path) -> None:
    project, done = _python_run(tmp_path, mutation_section=False, consent=True)
    assert (done["result"]["test_strength"]["status"], done["result"]["test_strength"]["reason_code"]) == ("NOT_APPLICABLE", "MUTATION_NOT_ENABLED")
    assert done["result"]["accepted"] is True
