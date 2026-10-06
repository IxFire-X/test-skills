import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from helpers import build_phase_two_baseline


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def test_bounded_parts_continue_after_finding_and_aggregate_exact_coverage():
    from tools.review_parts import build_review_plan, aggregate_review_parts, part_input, additional_review_parts

    snapshot = {"review_kind": "tc-reviewer", "revision": 1,
                "snapshot_digest": "sha256:" + "a" * 64,
                "instructions": "Review original evidence; report gaps.", "response_reserve_bytes": 100}
    scopes = [{"scope_id": kind, "kind": kind, "targets": [kind],
               "inputs": [{"artifact_digest": "sha256:" + "b" * 64,
                           "pointer": "/" + kind, "start": None, "end": None,
                           "content": "x" * 800}]} for kind in ("source", "local", "cross")]
    plan = build_review_plan(snapshot, scopes, input_byte_budget=2400)
    assert plan == build_review_plan(snapshot, scopes, input_byte_budget=2400)
    assert len(plan["parts"]) == 3
    results = []
    for part in plan["parts"]:
        assert len(json.dumps(part_input(plan, part), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) + 100 <= 2400
        results.append({"schema_version": "1.0.0", "plan_digest": plan["digest"],
                        "snapshot_digest": snapshot["snapshot_digest"], "part_id": part["part_id"],
                        "input_digest": _digest(part_input(plan, part)),
                        "coverage": [{"scope_id": scope["scope_id"], "status": "CHECKED", "evidence": ["/" + scope["scope_id"]], "assessment": "Compared original evidence."} for scope in part["scopes"]],
                        "findings": [], "corrections": [], "required_checks": []})
    results[0]["findings"] = [{"severity": "BLOCKING", "code": "SOURCE_OMISSION", "message": "Original requirement omitted.", "evidence": ["/source"], "related_ids": ["source"]}]
    aggregate = aggregate_review_parts(plan, results)
    assert aggregate["complete"] and not aggregate["eligible"]
    assert aggregate["findings"] == results[0]["findings"]
    assert aggregate["unchecked"] == []
    results[0]["findings"] = []
    assert aggregate_review_parts(plan, results)["eligible"]
    correction = {"id": "FIX-a", "correction_kind": "MECHANICAL", "path": "/cross/title",
                  "before": "Old", "after": "Clear", "related_ids": ["cross"],
                  "description": "Clarify wording.", "evidence": ["/cross"]}
    results[2]["corrections"] = [correction]
    assert not aggregate_review_parts(plan, results)["complete"]
    plan["additions"] = []
    for index in range(2):
        additions = additional_review_parts(plan, results)
        assert len(additions) == 1
        plan["additions"].extend(additions)
        part = additions[0]
        results.append({**deepcopy(results[-1]), "part_id": part["part_id"],
                        "input_digest": _digest(part_input(plan, part)),
                        "corrections": [correction | {"id": "FIX-b", "path": "/cross/objective"}] if index == 0 else [],
                        "coverage": [{"scope_id": part["scopes"][0]["scope_id"], "status": "CHECKED",
                                      "evidence": ["/cross"], "assessment": "Exact proposals checked together."}]})
    assert aggregate_review_parts(plan, results)["eligible"]


def test_incomplete_or_foreign_part_never_authorizes_acceptance():
    from tools.review_parts import build_review_plan, aggregate_review_parts

    snapshot = {"review_kind": "autotest-reviewer", "revision": 1,
                "snapshot_digest": "sha256:" + "a" * 64,
                "instructions": "Check source, local and cross evidence.", "response_reserve_bytes": 100}
    scopes = [{"scope_id": kind, "kind": kind, "targets": [kind],
               "inputs": [{"artifact_digest": "sha256:" + "b" * 64,
                           "pointer": "", "start": None, "end": None, "content": kind}]} for kind in ("source", "local", "cross")]
    plan = build_review_plan(snapshot, scopes, input_byte_budget=3000)
    result = aggregate_review_parts(plan, [])
    assert not result["complete"] and not result["eligible"]
    assert {row["scope_id"] for row in result["unchecked"]} == {"source", "local", "cross"}
    assert not aggregate_review_parts(plan, [{"part_id": "part-999999"}])["eligible"]


def _new_run(tmp_path: Path, profile: str = "cases-only-v1"):
    from tools.run_pipeline import run_phase_one_spine

    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": profile}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, profile, {"request_id": "review-1", "execution_requested": profile == "local-pilot-v1"}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    return run_root, attempt_id


def _prepared(tmp_path, *, budget=1000000):
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_requirement_traceability import canonical_fixture
    root, attempt = _new_run(tmp_path)
    session, package, _review, receipt = _reviewer_protocol_inputs(canonical_fixture(), root, attempt, complete=False, byte_budget=budget)
    return root, attempt, session, package, receipt


def _host(part_id="part-000001", *, invocation=None):
    isolation = {"fresh_context": True, "distinct_invocations": True, "role_policy": "canonical-reviewer-v2"}
    isolation["evidence_digest"] = _digest(isolation)
    return {"reviewer_invocation_id": invocation or "host-" + part_id, "model_id": "model-test", "host_isolation": isolation,
            "cli": "fixture", "cli_version": "1", "settings": "test-only"}


def test_completed_canonical_session_has_one_verdict_and_verified_same_model_isolation(tmp_path):
    from tests.helpers import complete_review_parts
    from tools.orchestrate_test_case_revision import validate_reviewer_session
    root, attempt, _session, package, _receipt = _prepared(tmp_path)
    completed = complete_review_parts(root, attempt)
    result = validate_reviewer_session(completed["session"], package, completed["output"], run_root=root, attempt_id=attempt)
    assert (result["independence"], result["acceptance_eligible"], result["verdict_count"]) == ("verified", True, 1)
    mutated = deepcopy(completed["session"])
    mutated["plan_digest"] = "sha256:" + "f" * 64
    mutated["digest"] = _digest({key: value for key, value in mutated.items() if key != "digest"})
    with pytest.raises(ValueError, match="immutable attempt boundary"):
        validate_reviewer_session(mutated, package, completed["output"], run_root=root, attempt_id=attempt)


def test_authoritative_verdict_records_normalized_semantics_and_binds_rejection(tmp_path):
    from tests.helpers import complete_review_parts
    from tools.orchestrate_test_case_revision import validate_reviewer_session
    root, attempt, _session, package, _receipt = _prepared(tmp_path)
    completed = complete_review_parts(root, attempt, findings=[{"severity": "BLOCKING", "code": "OMISSION", "message": "Missing original requirement.", "evidence": ["docs/feature.md"], "related_ids": ["TC-batch-a-001"]}])
    assert completed["session"]["events"][-2]["verdict"] == "REJECTED"
    contradictory = deepcopy(completed["output"])
    contradictory["artifacts"]["validation_report"]["verdict"] = "ПРИНЯТО"
    with pytest.raises(ValueError, match="aggregate output differs"):
        validate_reviewer_session(completed["session"], package, contradictory, run_root=root, attempt_id=attempt)


def test_auto_fix_successor_is_produced_from_exact_reviewer_corrections():
    from tests.test_requirement_traceability import canonical_fixture
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import _review_rows
    from tools.revision_selection import validate_review_decision
    from tools.review_parts import apply_review_corrections

    candidate = canonical_fixture()
    successor = deepcopy(candidate)
    successor["revision"] = 2
    successor["parent_sha256"] = document_sha256(candidate)
    successor["test_cases"][0]["title"] = "Уточнённая проверка отображения каталога"
    selected_candidate = {
        "document_id": candidate["document_id"], "revision": 1,
        "document_sha256": document_sha256(candidate),
    }
    selected_successor = {
        "document_id": successor["document_id"], "revision": 2,
        "document_sha256": document_sha256(successor),
    }
    corrections = [{
        "id": "FIX-title", "correction_kind": "MECHANICAL",
        "related_ids": [candidate["test_cases"][0]["case_id"]],
        "description": "Уточнить заголовок кейса.", "evidence": ["REQ-batch-a"],
    }]
    assert apply_review_corrections(candidate, [corrections[0] | {
        "path": "/test_cases/0/title", "before": candidate["test_cases"][0]["title"],
        "after": successor["test_cases"][0]["title"],
    }]) == successor
    report = {
        "verdict": "AUTO_FIX_APPLIED", "candidate": selected_candidate,
        "effective": selected_successor,
        "reviewed_case_ids": [item["case_id"] for item in candidate["test_cases"]],
        "findings": [], "corrections": corrections,
    }
    review = {
        "schema_version": "6.0.0", "stage": "tc-reviewer",
        "artifacts": {"validation_report": report, "successor_document": successor, "review_aggregate": {"plan_digest": "sha256:" + "a" * 64, "aggregate_digest": "sha256:" + "b" * 64}},
        "warnings": [],
    }
    assert _review_rows(review) == []
    assert validate_review_decision(candidate, report, successor) == []

    missing = deepcopy(review)
    del missing["artifacts"]["successor_document"]
    assert _review_rows(missing)


def test_unreviewed_r1_is_read_back_and_bound_before_session_reservation(tmp_path):
    from tools.orchestrate_test_case_revision import orchestrate_revision, OrchestrationError
    from tests.test_requirement_traceability import canonical_fixture
    root, attempt, session, package, receipt = _prepared(tmp_path)
    Path(receipt.json_path).write_bytes(Path(receipt.json_path).read_bytes() + b" ")
    with pytest.raises((ValueError, OrchestrationError)):
        orchestrate_revision(canonical_fixture(), receipt, package, session, None, Path(receipt.json_path).parent, run_root=root, attempt_id=attempt)


def test_evidence_retrieval_is_bounded_and_abort_is_only_zero_verdict_terminal_path(tmp_path):
    from tools.pilot_state import finish_review, next_review_part
    root, attempt, _session, _package, _receipt = _prepared(tmp_path, budget=1100)
    assert next_review_part(root, attempt) is None
    finished = finish_review(root, attempt)
    assert finished["session"]["status"] == "ABORTED"
    assert finished["aggregate"]["unchecked"]
    assert not finished["aggregate"]["eligible"]
    assert not any(event["event_type"] == "AUTHORITATIVE_VERDICT" for event in finished["session"]["events"])


@pytest.mark.parametrize("events", [
    [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}, {"ordinal": 2, "event_type": "REVIEW_SESSION_COMPLETED"}],
    [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}, {"ordinal": 2, "event_type": "AUTHORITATIVE_VERDICT", "verdict": "ACCEPTED"}, {"ordinal": 3, "event_type": "AUTHORITATIVE_VERDICT", "verdict": "ACCEPTED"}],
])
def test_second_verdict_or_invalid_terminal_sequence_is_forbidden(events):
    from tools.pilot_state import reviewer_lifecycle_projection
    with pytest.raises(ValueError):
        reviewer_lifecycle_projection({"events": events, "status": "COMPLETED"})


def test_missing_or_contradictory_isolation_evidence_is_not_accepted_or_is_invalid(tmp_path):
    from tools.pilot_state import open_review_part
    root, attempt, _session, _package, _receipt = _prepared(tmp_path)
    host = _host()
    host["host_isolation"]["fresh_context"] = False
    host["host_isolation"]["evidence_digest"] = _digest({key: value for key, value in host["host_isolation"].items() if key != "evidence_digest"})
    with pytest.raises(ValueError, match="isolation boundary"):
        open_review_part(root, attempt, "canonical", host)


def test_next_declared_part_is_allowed_but_duplicate_or_undeclared_invocation_is_rejected(tmp_path):
    from tools.pilot_state import next_review_part, open_review_part, submit_review_part, publish_model_request
    root, attempt, _session, _package, _receipt = _prepared(tmp_path, budget=7000)
    part = next_review_part(root, attempt)
    opened = open_review_part(root, attempt, "canonical", _host(part["part_id"]))
    with pytest.raises(ValueError, match="already requested"):
        open_review_part(root, attempt, "canonical", _host(part["part_id"]))
    assessment = {"coverage": [{"scope_id": scope["scope_id"], "status": "CHECKED", "evidence": ["fixture"], "assessment": "Fixture semantic assessment."} for scope in part["scopes"]], "findings": [], "corrections": [], "required_checks": []}
    submit_review_part(root, attempt, "canonical", part["part_id"], assessment)
    following = next_review_part(root, attempt)
    assert following is not None and following["part_id"] != part["part_id"]
    open_review_part(root, attempt, "canonical", _host(following["part_id"]))
    with pytest.raises(ValueError, match="undeclared"):
        publish_model_request(root, attempt, "tc-reviewer:canonical:part-999999", model_id="model-test", invocation_id="extra", input_digests=opened["request"]["input_digests"])


def test_waiting_session_is_nonterminal_and_exact_evidence_receipts_are_required(tmp_path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session
    from tools.pilot_state import open_review_part, submit_review_part
    root, attempt, session, package, _receipt = _prepared(tmp_path)
    assert not validate_reviewer_session(session, package, run_root=root, attempt_id=attempt)["acceptance_eligible"]
    opened = open_review_part(root, attempt, "canonical", _host())
    with pytest.raises(ValueError, match="invalid model stage artifact"):
        submit_review_part(root, attempt, "canonical", opened["input"]["part_id"], {"coverage": [], "findings": [], "corrections": [], "required_checks": []})


def test_reviewer_ledger_revisions_are_prefix_only_and_terminal_is_unique(tmp_path):
    from tools.pilot_state import block_review_part, next_review_part, publish_reviewer_session_ledger, read_reviewer_session_ledger
    from tests.helpers import complete_review_parts
    root, attempt, session, _package, _receipt = _prepared(tmp_path, budget=7000)
    part = next_review_part(root, attempt)
    block_review_part(root, attempt, "canonical", part["part_id"], "Required host context is unavailable.")
    rewritten = deepcopy(session)
    rewritten["events"].append({"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REWORK"})
    rewritten["status"] = "ABORTED"
    rewritten["digest"] = _digest({key: value for key, value in rewritten.items() if key != "digest"})
    with pytest.raises(ValueError, match="prefix-extend"):
        publish_reviewer_session_ledger(root, attempt, rewritten)
    completed = complete_review_parts(root, attempt)
    assert completed["aggregate"]["unchecked"] and not completed["aggregate"]["eligible"]
    assert read_reviewer_session_ledger(root, attempt)["status"] == "ABORTED"
    with pytest.raises(ValueError, match="already terminal"):
        publish_reviewer_session_ledger(root, attempt, rewritten)


def test_missing_published_ledger_file_blocks_every_later_revision(tmp_path):
    from tools.pilot_state import publish_reviewer_session_ledger
    root, attempt, session, _package, _receipt = _prepared(tmp_path)
    (root / "reviewer-session-ledgers" / attempt / "canonical" / (session["digest"][7:] + ".json")).unlink()
    with pytest.raises(ValueError, match="ledger history"):
        publish_reviewer_session_ledger(root, attempt, session)
