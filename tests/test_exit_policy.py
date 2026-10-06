import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools import pilot_state
from tools.pilot_state import exit_code, publish_terminal_result, terminal_result
from helpers import phase_two_baseline


RUN = "a" * 32
ATTEMPT = "b" * 32


def _facts(**changes: object) -> dict:
    facts = {
        "run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "TERMINAL", "completion": "COMPLETE",
        "verification": "NOT_APPLICABLE", "coverage": "MANUAL_ONLY", "reason_code": None,
        "canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True,
        "reviewer_session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1,
        "reviewer_pre_verdict_abort": False,
        "reviewer_isolation_state": "verified", "blocker_count": 0, "trace_valid": True,
        "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "NOT_APPLICABLE", "execution_applicability": "NOT_APPLICABLE",
        "automation_accepted": False, "generated_required_count": 0, "generated_materialized_count": 0,
        "generated_retained_count": 0, "exact_target_pass": False, "mixed_manual_traceable": False,
        "operational_reliable": True, "prior_stage_cause": "CANONICAL_COMPLETE",
    }
    facts.update(changes)
    return facts


def _local(**changes: object) -> dict:
    values = {
        "verification": "PASS", "coverage": "FULL", "materialization_applicability": "REQUIRED",
        "execution_applicability": "REQUIRED", "automation_accepted": True, "generated_required_count": 2,
        "generated_materialized_count": 2, "generated_retained_count": 2, "exact_target_pass": True,
    }
    values.update(changes)
    return _facts(**values)


def test_result_fact_shape_is_closed_and_rejects_aggregate_substitutes() -> None:
    with pytest.raises(ValueError): terminal_result({"attempt_state": "TERMINAL"}, "cases-only-v1")
    for key in ("canonical_valid", "review_accepted", "retained_required", "extra"):
        with pytest.raises(ValueError): terminal_result(_facts(**{key: True}), "cases-only-v1")
    for key, value in (("blocker_count", True), ("trace_valid", "true"), ("authoritative_verdict_count", "1")):
        with pytest.raises(ValueError): terminal_result(_facts(**{key: value}), "cases-only-v1")


def test_waiting_results_normalize_absent_and_null_axes() -> None:
    waiting = {"run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None}
    absent = {"run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "WAITING_FOR_MODEL"}
    assert terminal_result(waiting, "cases-only-v1") == terminal_result(absent, "cases-only-v1")
    assert exit_code(terminal_result(waiting, "cases-only-v1")) == 3


def test_cases_only_valid_terminal_results_are_draft_artifacts_only() -> None:
    for facts in (
        _facts(),
        _facts(finalization_valid=False),
        _facts(trace_valid=False),
        _facts(operational_reliable=False),
        _facts(
            completion="FATAL", coverage=None, reason_code="BASELINE_INCOMPLETE",
            reviewer_session_complete=False, reviewer_pre_verdict_abort=True,
            authoritative_verdict=None, authoritative_verdict_count=0,
        ),
    ):
        result = terminal_result(facts, "cases-only-v1")
        assert result["accepted"] is False
        assert result["verification"] == "NOT_APPLICABLE"
        # Review 2026-10-05 (decision 12): only a valid terminal is exit 1; a broken run is exit 2.
        assert exit_code(result) == (1 if facts == _facts() else 2)


def test_local_acceptance_requires_each_predicate_and_file_cardinality() -> None:
    assert terminal_result(_local(), "local-pilot-v1")["accepted"] is True
    assert terminal_result(_local(coverage="MIXED", mixed_manual_traceable=True), "local-pilot-v1")["accepted"] is True
    for key, value in (("canonical_schema_valid", False), ("canonical_semantics_valid", False), ("canonical_provenance_valid", False), ("authoritative_verdict", "REJECTED"), ("reviewer_isolation_state", "independence_unverified"), ("blocker_count", 1), ("trace_valid", False), ("finalization_valid", False), ("operational_reliable", False), ("automation_accepted", False), ("exact_target_pass", False), ("generated_retained_count", 1), ("completion", "PARTIAL"), ("verification", "FAIL"), ("coverage", "MANUAL_ONLY")):
        assert terminal_result(_local(**{key: value}), "local-pilot-v1")["accepted"] is False
    assert terminal_result(_local(materialization_applicability="NOT_APPLICABLE"), "local-pilot-v1")["accepted"] is False
    for key, value in (("generated_required_count", 0), ("generated_required_count", 3), ("generated_materialized_count", 1), ("generated_materialized_count", 3), ("generated_retained_count", 3), ("execution_applicability", "NOT_APPLICABLE")):
        with pytest.raises(ValueError):
            terminal_result(_local(**{key: value}), "local-pilot-v1")
    assert terminal_result(_local(coverage="MIXED", mixed_manual_traceable=False), "local-pilot-v1")["accepted"] is False
    for key in ("generated_required_count", "generated_materialized_count", "generated_retained_count"):
        with pytest.raises(ValueError):
            terminal_result(_local(**{key: True}), "local-pilot-v1")


def test_local_partial_materialization_remains_a_valid_unaccepted_branch() -> None:
    partial = _local(
        completion="PARTIAL", verification="NOT_APPLICABLE", coverage="MIXED",
        execution_applicability="NOT_APPLICABLE", exact_target_pass=False,
        generated_required_count=2, generated_materialized_count=1, generated_retained_count=0,
        reason_code="MATERIALIZATION_INCOMPLETE", prior_stage_cause="MATERIALIZATION_INCOMPLETE",
    )
    result = terminal_result(partial, "local-pilot-v1")
    assert result["accepted"] is False and result["reason_code"] == "MATERIALIZATION_INCOMPLETE" and exit_code(result) == 1
    with pytest.raises(ValueError):
        terminal_result({key: value for key, value in partial.items() if key != "reason_code"}, "local-pilot-v1")
    with pytest.raises(ValueError):
        terminal_result(_local(reason_code="MATERIALIZATION_INCOMPLETE"), "local-pilot-v1")
    for changes in (
        {"authoritative_verdict": "REJECTED"}, {"automation_accepted": False},
        {"generated_retained_count": 1},
    ):
        with pytest.raises(ValueError):
            terminal_result({**partial, **changes}, "local-pilot-v1")
    finalization_invalid = {**partial, "reason_code": None, "prior_stage_cause": "MATERIALIZATION_INCOMPLETE", "finalization_valid": False}
    result = terminal_result(finalization_invalid, "local-pilot-v1")
    assert result["reason_code"] == "FINALIZATION_INVALID" and finalization_invalid["prior_stage_cause"] == "MATERIALIZATION_INCOMPLETE"
    for changes in (
        {"authoritative_verdict": "REJECTED"}, {"automation_accepted": False},
        {"generated_retained_count": 1},
        {"execution_applicability": "REQUIRED"},
        {"exact_target_pass": True},
    ):
        with pytest.raises(ValueError):
            terminal_result({**finalization_invalid, **changes}, "local-pilot-v1")
    with pytest.raises(ValueError):
        terminal_result({**finalization_invalid, "prior_stage_cause": "EXECUTION_FAIL"}, "local-pilot-v1")
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    for changes in (
        {"authoritative_verdict": "REJECTED"}, {"automation_accepted": False},
        {"generated_retained_count": 1},
        {"exact_target_pass": True},
    ):
        forged = json.loads(json.dumps(result))
        forged["evidence"].update(changes)
        forged = pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"})
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(forged)


def test_cross_profile_vectors() -> None:
    with pytest.raises(ValueError):
        terminal_result(_facts(), "local-pilot-v1")
    with pytest.raises(ValueError):
        terminal_result(_local(), "cases-only-v1")


def test_cases_only_rejects_non_draft_terminal_verification_in_projection_and_schema() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    draft = terminal_result(_facts(), "cases-only-v1")

    for verification in ("PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE"):
        with pytest.raises(ValueError):
            terminal_result(_facts(verification=verification), "cases-only-v1")
        forged = copy.deepcopy(draft)
        forged["verification"] = verification
        forged = pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"})
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(forged)
        assert exit_code(forged) == 2


def test_reviewer_and_terminal_finalization_invariants_are_closed() -> None:
    for changes in (
        {"reviewer_session_complete": False},
        {"reviewer_pre_verdict_abort": True},
        {"authoritative_verdict_count": 2},
        {"finalization_completed": False},
        {"finalization_read_back": False},
    ):
        with pytest.raises(ValueError):
            terminal_result(_facts(**changes), "cases-only-v1")
    abort = _facts(
        completion="PARTIAL", verification="NOT_APPLICABLE", reason_code="REVIEW_CONTEXT_LIMIT",
        reviewer_session_complete=False, reviewer_pre_verdict_abort=True,
        authoritative_verdict=None, authoritative_verdict_count=0,
    )
    assert terminal_result(abort, "cases-only-v1")["accepted"] is False
    with pytest.raises(ValueError):
        terminal_result(_facts(reviewer_session_complete=False, reviewer_pre_verdict_abort=True, authoritative_verdict=None, authoritative_verdict_count=0), "cases-only-v1")


def test_abort_and_rework_reasons_bind_to_reviewer_evidence() -> None:
    abort = _facts(
        completion="PARTIAL", verification="NOT_APPLICABLE", reason_code="BATCH_INPUT_BLOCKED",
        reviewer_session_complete=False, reviewer_pre_verdict_abort=True,
        authoritative_verdict=None, authoritative_verdict_count=0,
    )
    assert terminal_result(abort, "cases-only-v1")["accepted"] is False
    for reason in ("REWORK", "EXECUTION_UNKNOWN", "MATERIALIZATION_INCOMPLETE"):
        with pytest.raises(ValueError):
            terminal_result({**abort, "reason_code": reason}, "cases-only-v1")
    invalid_abort = {**abort, "reason_code": None, "prior_stage_cause": "BATCH_INPUT_BLOCKED", "finalization_valid": False}
    assert terminal_result(invalid_abort, "cases-only-v1")["reason_code"] == "FINALIZATION_INVALID"
    for cause in ("REWORK", "EXECUTION_UNKNOWN", "MATERIALIZATION_INCOMPLETE"):
        with pytest.raises(ValueError):
            terminal_result({**invalid_abort, "prior_stage_cause": cause}, "cases-only-v1")
    rework = _facts(
        completion="PARTIAL", verification="NOT_APPLICABLE", reason_code="REWORK",
        authoritative_verdict="REJECTED", authoritative_verdict_count=1,
    )
    assert terminal_result(rework, "cases-only-v1")["accepted"] is False
    with pytest.raises(ValueError):
        terminal_result({**rework, "authoritative_verdict": "ACCEPTED"}, "cases-only-v1")


def test_schema_rejects_accepted_false_evidence_and_unknown_inverse() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    accepted = terminal_result(_local(), "local-pilot-v1")
    for mutate in (
        {"canonical_schema_valid": False}, {"trace_valid": False}, {"finalization_valid": False},
        {"operational_reliable": False},
    ):
        forged = json.loads(json.dumps(accepted))
        forged["evidence"].update(mutate)
        if "trace_valid" in mutate:
            forged["trace_valid"] = False
        if "finalization_valid" in mutate:
            forged["finalization_valid"] = False
        if "operational_reliable" in mutate:
            forged["operational_reliable"] = False
        forged = pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"})
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(forged)
    with pytest.raises(ValueError):
        terminal_result(_facts(completion="PARTIAL", verification="UNKNOWN"), "cases-only-v1")


def test_schema_binds_local_mixed_and_duplicate_terminal_evidence() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    local = terminal_result(_local(coverage="MIXED", mixed_manual_traceable=True), "local-pilot-v1")
    forged = json.loads(json.dumps(local))
    forged["evidence"]["mixed_manual_traceable"] = False
    forged = pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"})
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(forged)
    rejected = terminal_result(_facts(canonical_schema_valid=False), "cases-only-v1")
    for field in ("trace_valid", "finalization_valid", "operational_reliable"):
        forged = json.loads(json.dumps(rejected))
        forged[field] = not forged["evidence"][field]
        forged = pilot_state._sealed({key: value for key, value in forged.items() if key != "digest"})
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(forged)


def test_invalid_finalization_preserves_verification_coverage_and_external_stage_cause() -> None:
    facts = _local(verification="FAIL", coverage="MIXED", finalization_valid=False, prior_stage_cause="EXECUTION_FAIL")
    result = terminal_result(facts, "local-pilot-v1")
    assert result["reason_code"] == "FINALIZATION_INVALID" and result["verification"] == "FAIL" and result["coverage"] == "MIXED"
    assert "prior_stage_cause" not in result and facts["prior_stage_cause"] == "EXECUTION_FAIL"
    assert exit_code(result) == 2
    for field in ("finalization_completed", "finalization_read_back"):
        with pytest.raises(ValueError):
            terminal_result(_facts(**{field: False}), "cases-only-v1")


def test_active_nonterminal_is_schema_valid_but_conservatively_exits_two() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    active = terminal_result({"run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "ACTIVE"}, "cases-only-v1")
    Draft202012Validator(schema).validate(active)
    assert exit_code(active) == 2


def test_terminal_result_cannot_be_persisted_before_finalization_closure(tmp_path: Path) -> None:
    run = pilot_state.create_run(tmp_path, "local-pilot-v1", {"request_id": "request-1", "execution_requested": True})
    root = Path(run["run_root"])
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    attempt = pilot_state.create_attempt(root, {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "local-pilot-v1"}, phase_two_baseline(root, tmp_path, {"module": "."}))
    facts = _local(
        run_id=run["manifest"]["run_id"], attempt_id=attempt["attempt_id"], verification="FAIL",
        coverage="MIXED", finalization_valid=False, prior_stage_cause="EXECUTION_FAIL",
    )
    with pytest.raises(ValueError, match="terminal result"):
        pilot_state.append_event(root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest="sha256:" + "a" * 64)
    with pytest.raises(ValueError, match="terminal result binding"):
        publish_terminal_result(root, facts, "local-pilot-v1")
    assert not (root / "terminal-results").exists()


def test_exit_code_rejects_untrustworthy_results_before_priority() -> None:
    result = terminal_result(_facts(canonical_schema_valid=False), "cases-only-v1")
    for field, value in (("accepted", True), ("trace_valid", False), ("operational_reliable", False), ("digest", "sha256:" + "0" * 64), ("run_id", "not-an-id")):
        forged = copy.deepcopy(result); forged[field] = value
        if field != "digest":
            forged = pilot_state._sealed({key: item for key, item in forged.items() if key != "digest"})
        assert exit_code(forged) == 2


def test_resealed_reason_tuples_cannot_bypass_terminal_projection() -> None:
    invalid_finalization = terminal_result(_facts(finalization_valid=False), "cases-only-v1")
    forged = pilot_state._sealed({**{key: value for key, value in invalid_finalization.items() if key != "digest"}, "reason_code": "REWORK"})
    assert exit_code(forged) == 2
    with pytest.raises(ValueError):
        terminal_result(_facts(reason_code="REWORK"), "cases-only-v1")
    for reason, changes in (
        ("REWORK", {"completion": "COMPLETE"}),
        ("EXECUTION_UNKNOWN", {"completion": "PARTIAL", "verification": "FAIL"}),
        ("REVIEW_CONTEXT_LIMIT", {"completion": "PARTIAL", "verification": "NOT_APPLICABLE"}),
        ("FINALIZATION_INVALID", {"finalization_valid": True}),
    ):
        facts = _facts(reason_code=reason, **changes)
        with pytest.raises(ValueError):
            terminal_result(facts, "cases-only-v1")
    accepted = terminal_result(_facts(), "cases-only-v1")
    forged = pilot_state._sealed({**{key: item for key, item in accepted.items() if key != "digest"}, "policy_profile": "local-pilot-v1"})
    assert exit_code(forged) == 2
    waiting = terminal_result({"run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "WAITING_FOR_MODEL"}, "cases-only-v1")
    for field, value in (("accepted", True), ("completion", "COMPLETE"), ("reason_code", "REWORK")):
        forged = pilot_state._sealed({**{key: item for key, item in waiting.items() if key != "digest"}, field: value})
        assert exit_code(forged) == 2


def test_real_waiting_and_terminal_projection_validate_against_registered_schema() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "terminal-result.schema.json").read_text(encoding="utf-8"))
    waiting = terminal_result({"run_id": RUN, "attempt_id": ATTEMPT, "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None}, "cases-only-v1")
    Draft202012Validator(schema).validate(waiting)
    Draft202012Validator(schema).validate(terminal_result(_facts(), "cases-only-v1"))


def test_readiness_promotes_state_and_finalization_contracts(pack_root: Path) -> None:
    contract = json.loads((pack_root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    assert next(row for row in contract["artifact_registry"] if row["id"] == "structured_result")["semantic_ready"] is True
    assert next(row for row in contract["schema_registry"] if row["id"] == "terminal-result.schema.json")["semantic_ready"] is True
    assert next(row for row in contract["artifact_registry"] if row["id"] == "finalization_receipt")["semantic_ready"] is True
