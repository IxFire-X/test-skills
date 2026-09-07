import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from helpers import build_phase_two_baseline


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _package(base_bytes=1):
    context_digest = "sha256:" + "e" * 64
    package = {"candidate_digest": "sha256:" + "a" * 64, "candidate_receipt_digest": "sha256:" + "b" * 64, "assembly_digest": "sha256:" + "c" * 64, "context_marker_output_digest": "sha256:" + "9" * 64, "generator_batches": [{"batch_id": "BATCH-fixture", "digest": "sha256:" + "f" * 64, "context_receipt_digest": context_digest}], "inventory_digest": "sha256:" + "d" * 64, "context_receipt_digests": [context_digest], "base_package_byte_count": base_bytes}
    package["package_digest"] = _digest(package)
    return package


def _session(*events, same_model=True, same_invocation=False, host=True, status="COMPLETED", package=None, review_verdict="ПРИНЯТО"):
    package = _package() if package is None else package
    candidate = {"document_id": "TCDOC-x", "revision": 1, "document_sha256": "sha256:" + "a" * 64}
    rejected = review_verdict == "ТРЕБУЕТ ДОРАБОТКИ"
    review = {
        "schema_version": "5.0.0",
        "stage": "tc-reviewer",
        "artifacts": {"validation_report": {
            "verdict": review_verdict,
            "candidate": candidate,
            "effective": None if rejected else candidate,
            "reviewed_case_ids": ["TC-x"],
            "findings": ([{
                "severity": "BLOCKING", "code": "REVIEW_REJECTED",
                "message": "candidate needs rework", "evidence": ["REQ-x"],
                "related_ids": ["TC-x"],
            }] if rejected else []),
            "corrections": [],
        }},
        "warnings": [],
    }
    event_rows = [dict(event) for event in events]
    provided = []
    for event in event_rows:
        if event["event_type"] == "EVIDENCE_PROVIDED":
            provided.append(event["provided_digest"])
            event["cumulative_package_digest"] = _digest({"package_digest": package["package_digest"], "evidence_digests": provided})
        if event["event_type"] == "AUTHORITATIVE_VERDICT":
            report = review["artifacts"]["validation_report"]
            event["report_digest"] = _digest(report)
            event["verdict_digest"] = _digest({"verdict": report["verdict"], "effective": report["effective"]})
            event["verdict"] = "REJECTED" if rejected else "ACCEPTED"
    isolation = {"fresh_context": host, "distinct_invocations": not same_invocation, "role_policy": "canonical-reviewer-v1"}
    isolation["evidence_digest"] = _digest(isolation)
    value = {
        "schema_version": "1.0.0", "session_id": "review-session-1",
        "package_binding": package, "context_budget_bytes": 1000, "generator_role": "generator", "reviewer_role": "canonical-reviewer",
        "generator_invocation_id": "invoke-1", "reviewer_invocation_id": "invoke-1" if same_invocation else "invoke-2",
        "host_isolation": isolation,
        "events": event_rows, "status": status,
    }
    value["digest"] = _digest(value)
    return value, package, review


def _new_run(tmp_path: Path, profile: str = "cases-only-v1"):
    from tools.run_pipeline import run_phase_one_spine

    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": profile}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = run_phase_one_spine(tmp_path, profile, {"request_id": "review-1", "execution_requested": profile == "local-pilot-v1"}, identity, baseline, {})
    run_root = Path(run["run"]["run_root"])
    attempt_id = run["state"]["attempts"][0]["attempt_id"]
    return run_root, attempt_id


def _bind_session(tmp_path: Path, session: dict, package: dict, profile: str = "cases-only-v1"):
    from tools.orchestrate_test_case_revision import open_reviewer_session

    run_root, attempt_id = _new_run(tmp_path, profile)
    start = {key: session[key] for key in ("session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes")}
    boundary = open_reviewer_session(run_root, attempt_id, package, start)
    session["boundary_digest"] = boundary["digest"]
    session["digest"] = _digest({key: value for key, value in session.items() if key != "digest"})
    return run_root, attempt_id


def _evidence_receipt(package: dict, byte_count: int = 100):
    value = {
        "schema_version": "1.0.0",
        "inventory_digest": package["inventory_digest"],
        "batch_index": 2,
        "files": [{"opaque_id": "file-" + "1" * 24, "project_path": "src/evidence.py", "content_digest": "sha256:" + "2" * 64, "size": byte_count}],
        "byte_count": byte_count,
        "byte_set_digest": "sha256:" + "3" * 64,
    }
    value["digest"] = _digest(value)
    return value


def _started():
    return {"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}


def _verdict():
    return {"ordinal": 2, "event_type": "AUTHORITATIVE_VERDICT", "verdict": "ACCEPTED", "verdict_digest": "sha256:" + "1" * 64}


def _completed():
    return {"ordinal": 3, "event_type": "REVIEW_SESSION_COMPLETED"}


def test_completed_canonical_session_has_one_verdict_and_verified_same_model_isolation(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    session, binding, review = _session(_started(), _verdict(), _completed())
    run_root, attempt_id = _bind_session(tmp_path, session, binding)
    result = validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id, effective_canonical=True)

    assert {key: result[key] for key in ("independence", "acceptance_eligible", "verdict_count")} == {"independence": "verified", "acceptance_eligible": True, "verdict_count": 1}
    ledger = run_root / "reviewer-session-ledgers" / attempt_id / (result["reviewer_session_digest"].removeprefix("sha256:") + ".json")
    assert json.loads(ledger.read_text(encoding="utf-8")) == session
    mutated = json.loads(json.dumps(session))
    mutated["context_budget_bytes"] = 999
    mutated["digest"] = _digest({key: value for key, value in mutated.items() if key != "digest"})
    with pytest.raises(ValueError, match="immutable attempt boundary"):
        validate_reviewer_session(mutated, binding, review, run_root=run_root, attempt_id=attempt_id)


def test_authoritative_verdict_records_normalized_semantics_and_binds_rejection(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session
    from tools.pilot_state import read_reviewer_session_ledger

    session, binding, review = _session(
        _started(), _verdict(), _completed(), review_verdict="ТРЕБУЕТ ДОРАБОТКИ",
    )
    run_root, attempt_id = _bind_session(tmp_path, session, binding)
    result = validate_reviewer_session(
        session, binding, review, run_root=run_root, attempt_id=attempt_id,
    )

    ledger = read_reviewer_session_ledger(run_root, attempt_id, result["reviewer_session_digest"])
    verdict = next(event for event in ledger["events"] if event["event_type"] == "AUTHORITATIVE_VERDICT")
    assert verdict["verdict"] == "REJECTED"

    contradictory = json.loads(json.dumps(session))
    next(event for event in contradictory["events"] if event["event_type"] == "AUTHORITATIVE_VERDICT")["verdict"] = "ACCEPTED"
    contradictory["digest"] = _digest({key: value for key, value in contradictory.items() if key != "digest"})
    other = tmp_path / "contradictory-verdict"
    other.mkdir()
    run_root, attempt_id = _bind_session(other, contradictory, binding)
    with pytest.raises(ValueError, match="verdict is not bound"):
        validate_reviewer_session(
            contradictory, binding, review, run_root=run_root, attempt_id=attempt_id,
        )

def test_auto_fix_successor_is_produced_by_the_reviewer_artifact():
    from tests.test_requirement_traceability import canonical_fixture
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import _review_rows
    from tools.revision_selection import validate_review_decision

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
    report = {
        "verdict": "AUTO_FIX_APPLIED", "candidate": selected_candidate,
        "effective": selected_successor,
        "reviewed_case_ids": [item["case_id"] for item in candidate["test_cases"]],
        "findings": [], "corrections": corrections,
    }
    review = {
        "schema_version": "5.0.0", "stage": "tc-reviewer",
        "artifacts": {"validation_report": report, "successor_document": successor},
        "warnings": [],
    }
    assert _review_rows(review) == []
    assert validate_review_decision(candidate, report, successor) == []

    missing = deepcopy(review)
    del missing["artifacts"]["successor_document"]
    assert _review_rows(missing)


def test_unreviewed_r1_is_read_back_and_bound_before_session_reservation(tmp_path: Path):
    from tests.test_batch_assembly import _fragment, _header, _plan
    from tools.batch_assembly import assemble_candidate
    from tools.orchestrate_test_case_revision import open_reviewer_session, publish_unreviewed_candidate, reviewer_package_binding
    from tools.pilot_state import (
        derive_state,
        publish_context_selection,
        publish_model_request,
        publish_model_stage_artifact,
    )
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    project = tmp_path / "project"
    bundle = project / "bundles"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "evidence.py").write_text("value = 1\n", encoding="utf-8")
    run_root, attempt_id = _new_run(project)
    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    selected = select_context_batches(inventory, project, [next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "src/evidence.py")], byte_budget=4096)[0]["receipt"]
    context = publish_context_selection(run_root, attempt_id, selected)
    header = _header()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    candidate, assembly = assemble_candidate(header, plan, fragments)
    marker = {
        "schema_version": "5.0.0", "stage": "context-marker",
        "artifacts": {
            "analytics_documentation": {"requirements": candidate["source_requirements"]},
            "source_code_and_diff": {"sources": ["src/evidence.py — immutable test source"]},
        },
        "warnings": [],
    }
    publish_model_request(
        run_root, attempt_id, "context-marker:baseline",
        model_id="model-context", invocation_id=f"context-{attempt_id}",
        input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"]],
    )
    marker_publication = publish_model_stage_artifact(
        run_root, attempt_id, "context-marker:baseline", marker,
    )
    for index, fragment in enumerate(fragments, start=1):
        batch_id = fragment["batch_id"]
        publish_model_request(
            run_root, attempt_id, f"tc-generator:{batch_id}",
            model_id="model-generator",
            invocation_id=("invoke-1" if index == 1 else f"invoke-1-{index}"),
            input_digests=[
                marker_publication["content_digest"], context["digest"],
                fragment["plan_digest"], fragment["header_digest"],
            ],
        )
        publish_model_stage_artifact(
            run_root, attempt_id, f"tc-generator:{batch_id}", fragment,
        )
    receipt = publish_unreviewed_candidate(
        candidate, bundle, run_root=run_root, attempt_id=attempt_id,
    )
    binding = reviewer_package_binding(candidate, receipt, assembly, inventory, [context], context_marker_output=marker, generator_fragments=fragments, run_root=run_root, attempt_id=attempt_id)
    fabricated = dict(context)
    fabricated["batch_index"] = 99
    fabricated["digest"] = _digest({key: value for key, value in fabricated.items() if key != "digest"})
    with pytest.raises(ValueError, match="context receipt"):
        reviewer_package_binding(candidate, receipt, assembly, inventory, [fabricated], context_marker_output=marker, generator_fragments=fragments, run_root=run_root, attempt_id=attempt_id)
    session, _unused, _review = _session(_started(), _verdict(), _completed(), package=binding)
    start = {key: session[key] for key in ("session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes")}
    boundary = open_reviewer_session(run_root, attempt_id, binding, start)
    session["boundary_digest"] = boundary["digest"]
    session["digest"] = _digest({key: value for key, value in session.items() if key != "digest"})

    assert Path(receipt.json_path).read_bytes()
    assert session["boundary_digest"]
    from tools.pilot_state import read_attempt_receipt
    boundary = read_attempt_receipt(run_root, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
    assert boundary["package_digest"] == binding["package_digest"]

    Path(receipt.json_path).write_bytes(Path(receipt.json_path).read_bytes() + b" ")
    with pytest.raises(ValueError, match="candidate receipt"):
        reviewer_package_binding(candidate, receipt, assembly, inventory, [context], context_marker_output=marker, generator_fragments=fragments, run_root=run_root, attempt_id=attempt_id)


def test_evidence_retrieval_is_bounded_and_abort_is_only_zero_verdict_terminal_path(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    binding = _package()
    evidence = _evidence_receipt(binding)
    requested = {"ordinal": 2, "event_type": "EVIDENCE_REQUESTED", "request_digest": "sha256:" + "2" * 64, "byte_count": 100}
    provided = {"ordinal": 3, "event_type": "EVIDENCE_PROVIDED", "request_digest": "sha256:" + "2" * 64, "provided_digest": evidence["digest"], "byte_count": 100}
    completed = {"ordinal": 5, "event_type": "REVIEW_SESSION_COMPLETED"}
    session, binding, review = _session(_started(), requested, provided, {**_verdict(), "ordinal": 4}, completed, package=binding)
    run_root, attempt_id = _bind_session(tmp_path, session, binding)
    # A schema-valid in-memory receipt is not evidence: it must first be
    # published/read back against this attempt's frozen inventory.
    with pytest.raises(ValueError, match="evidence receipt"):
        validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id, evidence_receipts=[evidence])

    project = tmp_path / "overflow"
    project.mkdir()
    large_binding = _package(1001)
    aborted = {"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REVIEW_CONTEXT_LIMIT"}
    too_large, large_binding, _review = _session(_started(), aborted, status="ABORTED", package=large_binding)
    run_root, attempt_id = _bind_session(project, too_large, large_binding)
    facts = validate_reviewer_session(too_large, large_binding, run_root=run_root, attempt_id=attempt_id)
    assert facts["reason_code"] == "REVIEW_CONTEXT_LIMIT" and facts["verification"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("events", [
    (_started(), _verdict(), _verdict(), _completed()),
    (_started(), _completed()),
    (_started(), _verdict(), {"ordinal": 3, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REVIEW_CONTEXT_LIMIT"}),
])
def test_second_verdict_or_invalid_terminal_sequence_is_forbidden(tmp_path: Path, events):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    with pytest.raises(ValueError, match="REVIEWER_PROTOCOL"):
        session, binding, review = _session(*events)
        run_root, attempt_id = _bind_session(tmp_path, session, binding)
        validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id)


def test_missing_or_contradictory_isolation_evidence_is_not_accepted_or_is_invalid(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    session, binding, review = _session(_started(), _verdict(), _completed(), host=False)
    run_root, attempt_id = _bind_session(tmp_path, session, binding)
    unverified = validate_reviewer_session(session, binding, review, run_root=run_root, attempt_id=attempt_id)
    assert {key: unverified[key] for key in ("independence", "acceptance_eligible", "verdict_count")} == {"independence": "independence_unverified", "acceptance_eligible": False, "verdict_count": 1}

    project = tmp_path / "contradictory"
    project.mkdir()
    contradictory, binding, review = _session(_started(), _verdict(), _completed(), same_invocation=True, host=True)
    contradictory["host_isolation"]["distinct_invocations"] = True
    contradictory["host_isolation"]["evidence_digest"] = _digest({key: value for key, value in contradictory["host_isolation"].items() if key != "evidence_digest"})
    contradictory["digest"] = _digest({key: value for key, value in contradictory.items() if key != "digest"})
    with pytest.raises(ValueError, match="reviewer session boundary"):
        _bind_session(project, contradictory, binding)


def test_second_session_for_same_attempt_is_rejected_before_reviewer_invocation(tmp_path: Path):
    from tools.orchestrate_test_case_revision import open_reviewer_session

    first, binding, _review = _session(_started(), _verdict(), _completed())
    run_root, attempt_id = _bind_session(tmp_path, first, binding)
    second, _binding, _review = _session(_started(), _verdict(), _completed(), package=binding)
    second["session_id"] = "review-session-2"
    start = {key: second[key] for key in ("session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes")}
    with pytest.raises(ValueError):
        open_reviewer_session(run_root, attempt_id, binding, start)


def test_waiting_session_is_nonterminal_and_exact_evidence_receipts_are_required(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    waiting, binding, _review = _session(_started(), status="WAITING")
    run_root, attempt_id = _bind_session(tmp_path, waiting, binding)
    assert validate_reviewer_session(waiting, binding, run_root=run_root, attempt_id=attempt_id)["acceptance_eligible"] is False

    project = tmp_path / "missing-evidence"
    project.mkdir()
    evidence = _evidence_receipt(binding)
    requested = {"ordinal": 2, "event_type": "EVIDENCE_REQUESTED", "request_digest": "sha256:" + "2" * 64, "byte_count": 100}
    provided = {"ordinal": 3, "event_type": "EVIDENCE_PROVIDED", "request_digest": requested["request_digest"], "provided_digest": evidence["digest"], "byte_count": 100}
    completed, binding, review = _session(_started(), requested, provided, {**_verdict(), "ordinal": 4}, {**_completed(), "ordinal": 5}, package=binding)
    run_root, attempt_id = _bind_session(project, completed, binding)
    with pytest.raises(ValueError, match="evidence response"):
        validate_reviewer_session(completed, binding, review, run_root=run_root, attempt_id=attempt_id)


def test_reviewer_ledger_revisions_are_prefix_only_and_terminal_is_unique(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    requested = {"ordinal": 2, "event_type": "EVIDENCE_REQUESTED", "request_digest": "sha256:" + "2" * 64, "byte_count": 100}
    waiting, binding, _review = _session(_started(), requested, status="WAITING")
    run_root, attempt_id = _bind_session(tmp_path, waiting, binding)
    validate_reviewer_session(waiting, binding, run_root=run_root, attempt_id=attempt_id)

    rewritten, _binding, review = _session(_started(), _verdict(), _completed(), package=binding)
    rewritten["boundary_digest"] = waiting["boundary_digest"]
    rewritten["digest"] = _digest({key: value for key, value in rewritten.items() if key != "digest"})
    with pytest.raises(ValueError, match="prefix-extend"):
        validate_reviewer_session(rewritten, binding, review, run_root=run_root, attempt_id=attempt_id)

    project = tmp_path / "valid-prefix"
    project.mkdir()
    waiting, binding, _review = _session(_started(), status="WAITING")
    run_root, attempt_id = _bind_session(project, waiting, binding)
    validate_reviewer_session(waiting, binding, run_root=run_root, attempt_id=attempt_id)
    completed, _binding, review = _session(_started(), _verdict(), _completed(), package=binding)
    completed["boundary_digest"] = waiting["boundary_digest"]
    completed["digest"] = _digest({key: value for key, value in completed.items() if key != "digest"})
    assert validate_reviewer_session(completed, binding, review, run_root=run_root, attempt_id=attempt_id)["acceptance_eligible"] is True

    alternate, _binding, _alternate_review = _session(
        _started(),
        {"ordinal": 2, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": "REVIEW_CONTEXT_LIMIT"},
        status="ABORTED",
        package=binding,
    )
    alternate["boundary_digest"] = waiting["boundary_digest"]
    alternate["digest"] = _digest({key: value for key, value in alternate.items() if key != "digest"})
    with pytest.raises(ValueError, match="already terminal"):
        validate_reviewer_session(alternate, binding, run_root=run_root, attempt_id=attempt_id)


def test_missing_published_ledger_file_blocks_every_later_revision(tmp_path: Path):
    from tools.orchestrate_test_case_revision import validate_reviewer_session

    waiting, binding, _review = _session(_started(), status="WAITING")
    run_root, attempt_id = _bind_session(tmp_path, waiting, binding)
    facts = validate_reviewer_session(waiting, binding, run_root=run_root, attempt_id=attempt_id)
    ledger = run_root / "reviewer-session-ledgers" / attempt_id / (facts["reviewer_session_digest"].removeprefix("sha256:") + ".json")
    ledger.unlink()

    completed, _binding, review = _session(_started(), _verdict(), _completed(), package=binding)
    completed["boundary_digest"] = waiting["boundary_digest"]
    completed["digest"] = _digest({key: value for key, value in completed.items() if key != "digest"})
    with pytest.raises(ValueError, match="ledger history"):
        validate_reviewer_session(completed, binding, review, run_root=run_root, attempt_id=attempt_id)
