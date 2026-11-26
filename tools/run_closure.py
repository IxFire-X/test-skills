"""The closure of an attempt (v0.8, the cut of the core): the artifacts of the closure and their check, the terminal result, the check
of the kind ``review-canaries`` (``RECEIPT_CHECKS``: ``pilot_state._validate_factual_receipt`` takes it from here).  The check of a
terminal transition and the reading of a closure artifact stay with the journal: ``append_event`` checks a transition with them.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v2a.tsv``);
what they read of the journal and of ``effective_canonical`` they take by public names, and ``pilot_state`` still serves their names to
its callers (the facade of the cut).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from tools.confined_output import OutputConfinementError, read_confined_bytes
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    CLOSURE_KINDS as _CLOSURE_KINDS,
    DIGEST as _DIGEST,
    append_event,
    bind_events as _bind_events,
    canonical_bytes as _canonical_bytes,
    closure_target as _closure_target,
    derive_state,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    read_attempt_receipt,
    read_closure_artifact,
    read_resume_validation_if_present,
    run_root_of as _run_root,
    sealed as _sealed,
    terminal_result,
    terminal_reviewer_evidence,
    validate_execution_event_branch as _validate_execution_event_branch,
    validate_result_record as _validate_result_record,
    validate_schema as _validate_schema,
)
from tools.effective_canonical import (  # the names of the effective canonical set these bodies read (П1 of the cut of v0.8)
    read_effective_canonical,
    read_effective_canonical_if_present,
)


def _check_review_canaries(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the digests of the canary map and of the reviewer's view, bound to the assembled candidate
    from tools.review_canaries import MAX_CANARIES

    digests = ("candidate_digest", "view_digest", "canary_map_digest")
    if (
        set(receipt) != common | {*digests, "canary_count"}
        or not identity_matches
        or any(not _DIGEST.fullmatch(str(receipt.get(name, ""))) for name in digests)
        or type(receipt.get("canary_count")) is not int
        or not 0 <= receipt["canary_count"] <= MAX_CANARIES
        or not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("attempt_id") == attempt["attempt_id"]
                   and event.get("stage_instance_id") == "assembly" and event.get("artifact_digest") == receipt["candidate_digest"]
                   for event in state["events"])
    ):
        raise ValueError("invalid review-canaries receipt")


def publish_closure_artifact(
    run_root: Path,
    attempt_id: str,
    kind: str,
    artifact: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Publish/read back one immutable pre-terminal closure artifact."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or kind not in _CLOSURE_KINDS:
        raise ValueError("invalid closure artifact attempt")
    value = dict(artifact)
    digest = value.get("digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or _sealed({key: item for key, item in value.items() if key != "digest"}) != value:
        raise ValueError("invalid closure artifact digest")
    if any(value.get(key) != attempt.get(key) for key in ("run_id", "attempt_id", "policy_profile")):
        raise ValueError("closure artifact identity is invalid")
    schema_name = {
        "pre_finalization_trace": "pre-finalization-trace.schema.json",
        "finalization_receipt": "finalization-receipt.schema.json",
        "terminal_trace": "derived-terminal-trace.schema.json",
    }[kind]
    _validate_schema(schema_name, value)
    if kind == "pre_finalization_trace":
        try:
            terminal_reviewer = terminal_reviewer_evidence(
                root, attempt_id,
            )
            effective = read_effective_canonical_if_present(
                root, attempt_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("pre-finalization canonical lineage is invalid") from error
        reviewer = value.get("reviewer")
        expected_reviewer = {
            key: terminal_reviewer[key]
            for key in (
                "digest", "session_complete", "authoritative_verdict",
                "authoritative_verdict_count", "pre_verdict_abort", "isolation", "self_accepted",
            )
            if key in terminal_reviewer
        }
        expected_effective = effective.get("document_digest") if effective is not None else None
        admitted = terminal_reviewer["isolation"] == "verified" or terminal_reviewer.get("self_accepted") is True
        if terminal_reviewer["authoritative_verdict"] == "ACCEPTED" and admitted and effective is None:
            raise ValueError("pre-finalization effective canonical is unavailable")
        if (
            value.get("effective_canonical_digest") != expected_effective
            or value.get("canonical_digest") != terminal_reviewer.get("canonical_digest")
            or not isinstance(reviewer, Mapping)
            or dict(reviewer) != expected_reviewer
        ):
            raise ValueError("pre-finalization canonical lineage is invalid")
        disposition_digest = value.get("disposition_receipt_digest")
        disposition_event_digest = None
        if disposition_digest is not None:
            try:
                disposition_record = read_attempt_receipt(
                    root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK",
                )["record"]
                disposition = disposition_record["payload"]
                disposition_event_digest = disposition_record["digest"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization disposition binding is invalid") from error
            if (
                disposition.get("digest") != disposition_digest
                or value.get("generated_delta_digest") != disposition.get("generated_delta_digest")
                or value.get("disposition_verification") != disposition.get("verification")
                or value.get("dispositions") != disposition.get("files")
            ):
                raise ValueError("pre-finalization disposition binding is invalid")
        execution = value.get("execution")
        execution_digest = execution.get("execution_receipt_digest") if isinstance(execution, Mapping) else None
        _validate_execution_event_branch(
            state["events"], attempt_id,
            str(execution.get("verification")) if isinstance(execution, Mapping) else "",
            execution_digest if isinstance(execution_digest, str) else None,
        )
        if execution_digest is not None:
            try:
                execution_record = read_attempt_receipt(
                    root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
                )["record"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization execution binding is invalid") from error
            if execution_record.get("digest") != execution_digest or execution_record.get("payload", {}).get("verdict") != execution.get("verification"):
                raise ValueError("pre-finalization execution binding is invalid")
        try:
            resume_validation = read_resume_validation_if_present(
                root, attempt_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("pre-finalization resume validation binding is invalid") from error
        resume_digest = value.get("resume_validation_digest")
        if isinstance(resume_validation, Mapping) and (
            resume_validation.get("execution_receipt_digest") != execution_digest
        ):
            raise ValueError("pre-finalization resume validation binding is invalid")
        if resume_digest is not None:
            if (
                not isinstance(resume_validation, Mapping)
                or resume_validation.get("digest") != resume_digest
                or resume_validation.get("pre_finalization_trace_digest") is not None
            ):
                raise ValueError("pre-finalization resume validation binding is invalid")
        elif isinstance(resume_validation, Mapping):
            if resume_validation.get("pre_finalization_trace_digest") != value.get("digest"):
                raise ValueError("pre-finalization late validation binding is invalid")
            ordered = [
                (index, event.get("event_type"), event.get("artifact_digest"))
                for index, event in enumerate(state["events"])
                if event.get("attempt_id") == attempt_id
                and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
                and event.get("artifact_digest") in {value.get("digest"), resume_validation.get("digest")}
            ]
            pretrace_readbacks = [
                index for index, event_type, digest in ordered
                if event_type == "ARTIFACT_READ_BACK" and digest == value.get("digest")
            ]
            late_events = [
                (index, event_type) for index, event_type, digest in ordered
                if digest == resume_validation.get("digest")
            ]
            if (
                len(pretrace_readbacks) != 1
                or [event_type for _index, event_type in late_events]
                != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
                or pretrace_readbacks[0] >= late_events[0][0]
            ):
                raise ValueError("pre-finalization late validation order is invalid")
        trace_binding = value.get("execution_trace")
        if not isinstance(trace_binding, Mapping):
            raise ValueError("pre-finalization execution trace binding is invalid")
        if trace_binding.get("applicability") == "PRESENT":
            trace_digest = trace_binding.get("trace_receipt_digest")
            audit_digest = trace_binding.get("audit_receipt_digest")
            try:
                trace_record = read_attempt_receipt(
                    root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
                )["record"]
                audit_record = read_attempt_receipt(
                    root, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
                )["record"]
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("pre-finalization execution trace binding is invalid") from error
            audit_payload = audit_record.get("payload")
            audit_lifecycle = audit_payload.get("trace_audit", {}).get("lifecycle") if isinstance(audit_payload, Mapping) else None
            if (
                trace_record.get("digest") != trace_digest
                or trace_record.get("trace_sha256") != trace_binding.get("trace_sha256")
                or audit_record.get("digest") != audit_digest
                or audit_record.get("execution_trace_receipt_digest") != trace_digest
                or audit_record.get("trace_sha256") != trace_binding.get("trace_sha256")
                or not isinstance(audit_payload, Mapping)
                or trace_binding.get("audit_valid") is not audit_payload.get("valid")
                or not isinstance(audit_lifecycle, Mapping)
                or audit_lifecycle.get("verification") != execution.get("verification")
            ):
                raise ValueError("pre-finalization execution trace binding is invalid")
            readback_positions = {
                event_digest: index
                for index, event in enumerate(state["events"])
                if event.get("attempt_id") == attempt_id
                and event.get("event_type") == "ARTIFACT_READ_BACK"
                and (event_digest := event.get("artifact_digest")) in {trace_digest, audit_digest, disposition_event_digest}
            }
            if (
                set(readback_positions) != {trace_digest, audit_digest, disposition_event_digest}
                or not (readback_positions[trace_digest] < readback_positions[audit_digest] < readback_positions[disposition_event_digest])
            ):
                raise ValueError("pre-finalization lifecycle order is invalid")
        elif trace_binding != {
            "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
            "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
        }:
            raise ValueError("pre-finalization execution trace binding is invalid")
    elif kind == "finalization_receipt":
        pre_digest = value.get("pre_finalization_trace_digest")
        if not isinstance(pre_digest, str):
            raise ValueError("finalization receipt pre-trace binding is invalid")
        pre_trace = read_closure_artifact(
            root, attempt_id, "pre_finalization_trace", pre_digest,
        )
        try:
            from tools.finalize_attempt import verify_finalization

            resume_validation = read_resume_validation_if_present(
                root, attempt_id,
            )
            post_pretrace_digest = (
                resume_validation.get("digest")
                if isinstance(resume_validation, Mapping)
                and resume_validation.get("pre_finalization_trace_digest") == pre_digest
                else None
            )
            verifier_inputs = {
                "run_id": pre_trace["run_id"],
                "attempt_id": pre_trace["attempt_id"],
                "policy_profile": pre_trace["policy_profile"],
                "pre_finalization_trace_digest": pre_digest,
                "required_artifacts": value.get("checked_artifacts"),
            }
            if post_pretrace_digest is not None:
                verifier_inputs["post_pretrace_validation_digest"] = post_pretrace_digest
            expected = verify_finalization(verifier_inputs, pre_trace)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("finalization receipt verification is invalid") from error
        if expected != value:
            raise ValueError("finalization receipt verification is invalid")
    elif kind == "terminal_trace":
        pre_digest = value.get("pre_finalization_trace_digest")
        finalization_digest = value.get("finalization_receipt_digest")
        if (
            not isinstance(pre_digest, str)
            or not isinstance(finalization_digest, str)
            or read_closure_artifact(
                root, attempt_id, "pre_finalization_trace", pre_digest,
            ).get("digest") != pre_digest
            or read_closure_artifact(
                root, attempt_id, "finalization_receipt", finalization_digest,
            ).get("digest") != finalization_digest
        ):
            raise ValueError("terminal trace closure binding is invalid")
        result_digest = value.get("terminal_result_digest")
        try:
            result = _read_artifact(project, root, root / "terminal-results" / f"{attempt_id}.json", "terminal result")
        except (OSError, ValueError) as error:
            raise ValueError("terminal trace result binding is invalid") from error
        result_events = [
            item["event_type"] for item in state["events"]
            if item.get("attempt_id") == attempt_id and item.get("artifact_digest") == result_digest
            and item.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        ]
        if result.get("digest") != result_digest or result_events != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
            raise ValueError("terminal trace result binding is invalid")
    target = _closure_target(root, attempt_id, kind, digest)
    record, created, identity = _publish(project, root, target, {key: item for key, item in value.items() if key != "digest"}, kind.replace("_", " "), return_created=True)
    if record != value:
        raise ValueError("closure artifact read-back mismatch")
    relevant = [
        event["event_type"] for event in derive_state(root)["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("artifact_digest") == digest
        and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
    ]
    if relevant == []:
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    elif relevant not in (["ARTIFACT_PUBLISHED"], ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]):
        raise ValueError("invalid closure artifact event order")
    if read_confined_bytes(project, root, target) != _canonical_bytes(record):
        raise ValueError("closure artifact lost after publication binding")
    if relevant != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]:
        append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=digest)
    if read_confined_bytes(project, root, target) != _canonical_bytes(record):
        raise ValueError("closure artifact lost after read-back binding")
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": _canonical_bytes(record), "record": record, "created": created, "installed_identity": identity}


def read_closure_artifact_if_present(
    run_root: Path,
    attempt_id: str,
    kind: str,
) -> Mapping[str, Any] | None:
    """Read or finish binding the one immutable closure slot already installed."""
    project, root = _run_root(run_root)
    if kind not in _CLOSURE_KINDS or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid closure artifact identity")
    target = root / "closure" / attempt_id / f"{kind}.json"
    try:
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError("unsafe closure artifact path") from error
    if raw is None:
        return None
    value = _read_artifact(project, root, target, kind.replace("_", " "))
    digest = value.get("digest")
    if not isinstance(digest, str):
        raise ValueError("closure artifact digest is unavailable")
    try:
        return read_closure_artifact(
            root, attempt_id, kind, digest,
        )
    except ValueError:
        # Installation precedes journal binding.  A controller interruption may
        # therefore leave the exact fixed-slot bytes with no READ_BACK event.
        # Republishing the same sealed value is non-mutating and lets the normal
        # publisher recover only the missing ordered binding events; conflicting
        # bytes or event orders still fail closed there.
        return publish_closure_artifact(root, attempt_id, kind, value)["record"]


def _validate_accepted_terminal_closure(
    root: Path,
    result: Mapping[str, Any],
    finalization_receipt: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Prevent a low-level caller from projecting acceptance beyond durable trace facts."""
    if result.get("accepted") is not True:
        return
    attempt_id = str(result["attempt_id"])
    pre_digest = finalization_receipt.get("pre_finalization_trace_digest")
    if finalization_receipt.get("valid") is not True or not isinstance(pre_digest, str):
        raise ValueError("accepted terminal result lacks valid pre-finalization evidence")
    pre_trace = read_closure_artifact(
        root, attempt_id, "pre_finalization_trace", pre_digest,
    )
    reviewer = pre_trace.get("reviewer")
    evidence = result.get("evidence")
    applicability = pre_trace.get("evidence")
    execution = pre_trace.get("execution")
    rows = pre_trace.get("dispositions")
    trace_binding = pre_trace.get("execution_trace")
    if not all(isinstance(value, Mapping) for value in (reviewer, evidence, applicability, execution, trace_binding)) or not isinstance(rows, list):
        raise ValueError("accepted terminal result lacks valid pre-finalization evidence")
    reviewer_bindings = {
        "reviewer_session_complete": reviewer.get("session_complete"),
        "authoritative_verdict": reviewer.get("authoritative_verdict"),
        "authoritative_verdict_count": reviewer.get("authoritative_verdict_count"),
        "reviewer_pre_verdict_abort": reviewer.get("pre_verdict_abort"),
        "reviewer_isolation_state": reviewer.get("isolation"),
    }
    if any(evidence.get(key) != value for key, value in reviewer_bindings.items()) or result.get("review_independence") != (
        "SELF" if reviewer.get("self_accepted") is True else None
    ):
        raise ValueError("accepted terminal result contradicts pre-finalization reviewer evidence")
    try:
        effective = read_effective_canonical(root, attempt_id)
        document = effective["document"]
        steps = [
            (case["case_id"], step)
            for case in document["test_cases"]
            for step in case["steps"]
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("accepted terminal result lacks an effective canonical") from error
    from tools.canonical_document import blocker_count as canonical_blocker_count

    blocker_count = canonical_blocker_count(document)
    manual_steps = sum(step["manual_only"] is True for _case_id, step in steps)
    coverage = "MANUAL_ONLY" if manual_steps == len(steps) else "MIXED" if manual_steps else "FULL"
    if (
        blocker_count != 0
        or evidence.get("blocker_count") != blocker_count
        or result.get("coverage") != coverage
        or pre_trace.get("canonical_digest") is None
        or pre_trace.get("effective_canonical_digest") != effective.get("document_digest")
    ):
        raise ValueError("accepted terminal result contradicts canonical coverage")
    profile = result.get("policy_profile")
    if profile == "cases-only-v1":
        if (
            any(applicability.get(key) != "NOT_APPLICABLE" for key in ("materialization", "execution", "dispositions"))
            or execution.get("verification") != "NOT_APPLICABLE"
            or rows
            or pre_trace.get("generated_delta_digest") is not None
            or pre_trace.get("disposition_receipt_digest") is not None
            or trace_binding != {
                "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
                "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
            }
            or evidence.get("materialization_applicability") != "NOT_APPLICABLE"
            or evidence.get("execution_applicability") != "NOT_APPLICABLE"
            or any(evidence.get(key) != 0 for key in (
                "generated_required_count", "generated_materialized_count", "generated_retained_count",
            ))
        ):
            raise ValueError("accepted cases-only result contradicts pre-finalization evidence")
        return
    if profile != "local-pilot-v1":
        raise ValueError("accepted terminal result has unknown policy profile")
    try:
        trace_record = read_attempt_receipt(
            root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
        )["record"]
        audit_record = read_attempt_receipt(
            root, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
        )["record"]
        trace_document = trace_record["payload"]
        audit_document = audit_record["payload"]
        relations = trace_document["implementation_relations"]
        trace_files = trace_document["files"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("accepted local result lacks durable trace evidence") from error
    if (
        any(applicability.get(key) != "PRESENT" for key in ("materialization", "execution", "dispositions"))
        or execution.get("verification") != "PASS"
        or not isinstance(execution.get("execution_receipt_digest"), str)
        or not isinstance(pre_trace.get("generated_delta_digest"), str)
        or not isinstance(pre_trace.get("disposition_receipt_digest"), str)
        or trace_binding.get("applicability") != "PRESENT"
        or trace_binding.get("trace_receipt_digest") != trace_record.get("digest")
        or trace_binding.get("trace_sha256") != trace_record.get("trace_sha256")
        or trace_binding.get("audit_receipt_digest") != audit_record.get("digest")
        or trace_binding.get("audit_valid") is not True
        or audit_document.get("valid") is not True
        or audit_record.get("execution_trace_receipt_digest") != trace_record.get("digest")
        or not isinstance(relations, list)
        or not isinstance(trace_files, list)
        or not rows
        or any(row.get("materialization") != "MATERIALIZED" or row.get("disposition") != "RETAINED" for row in rows if isinstance(row, Mapping))
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise ValueError("accepted local result lacks execution and retained dispositions")
    required_count = len(rows)
    if (
        evidence.get("materialization_applicability") != "REQUIRED"
        or evidence.get("execution_applicability") != "REQUIRED"
        or evidence.get("generated_required_count") != required_count
        or evidence.get("generated_materialized_count") != required_count
        or evidence.get("generated_retained_count") != required_count
        or evidence.get("automation_accepted") is not True
        or evidence.get("exact_target_pass") is not True
        or evidence.get("trace_valid") is not True
        or evidence.get("operational_reliable") is not True
        or evidence.get("mixed_manual_traceable") is not (coverage == "MIXED")
    ):
        raise ValueError("accepted local result contradicts generated-file evidence")
    actual_operations = {
        (row.get("case_id"), row.get("step_id"))
        for row in relations
        if isinstance(row, Mapping) and row.get("kind") == "operation"
    }
    actual_assertions = {
        (row.get("case_id"), row.get("step_id"), row.get("expectation_id"), row.get("assertion_id"))
        for row in relations
        if isinstance(row, Mapping) and row.get("kind") == "assertion"
    }
    expected_operations = {
        (case_id, step["step_id"])
        for case_id, step in steps
        if not step["manual_only"]
    }
    expected_assertions = {
        (case_id, step["step_id"], expectation["expectation_id"], assertion["assertion_id"])
        for case_id, step in steps
        if not step["manual_only"]
        for expectation in step["expectations"]
        for assertion in expectation["assertions"]
    }
    related_files = {row.get("file_id") for row in trace_files if isinstance(row, Mapping)}
    disposition_files = {row["file_id"] for row in rows}
    if not expected_operations <= actual_operations or not expected_assertions <= actual_assertions or related_files != disposition_files:
        raise ValueError("accepted local result has incomplete trace relations")


def publish_terminal_result(run_root: Path, facts: Mapping[str, Any], policy_profile: str, *, finalization_receipt: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    result = terminal_result(facts, policy_profile)
    if result["attempt_state"] != "TERMINAL" or not _validate_result_record(result):
        raise ValueError("terminal result is invalid")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == result["attempt_id"]), None)
    if attempt is None or attempt["run_id"] != result["run_id"] or attempt["policy_profile"] != policy_profile:
        raise ValueError("terminal result binding is invalid")
    if finalization_receipt is None:
        if attempt["state"] != "TERMINAL":
            raise ValueError("terminal result binding is invalid")
    else:
        if attempt["state"] == "TERMINAL":
            raise ValueError("terminal attempt is immutable")
        digest = finalization_receipt.get("digest")
        if not isinstance(digest, str) or read_closure_artifact(
            root, result["attempt_id"], "finalization_receipt", digest,
        ) != finalization_receipt:
            raise ValueError("terminal finalization receipt binding is invalid")
        if (
            finalization_receipt.get("stage") != "finalization"
            or result["finalization_valid"] is not bool(finalization_receipt.get("valid"))
            or any(finalization_receipt.get(key) != result.get(key) for key in ("run_id", "attempt_id", "policy_profile"))
        ):
            raise ValueError("terminal finalization receipt binding is invalid")
        _validate_accepted_terminal_closure(root, result, finalization_receipt, state)
    target = root / "terminal-results" / f"{result['attempt_id']}.json"
    record, created, identity = _publish(project, root, target, {key: value for key, value in result.items() if key != "digest"}, "terminal result", return_created=True)
    if finalization_receipt is not None:
        _bind_events(root, result["attempt_id"], record["digest"])
    return {"path": str(target.relative_to(root)).replace("\\", "/"), "bytes": _canonical_bytes(record), "record": record, "created": created, "installed_identity": identity}


def read_terminal_result(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Return only a result already bound by the immutable terminal event."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] != "TERMINAL":
        raise KeyError(attempt_id)
    result = _read_artifact(project, root, root / "terminal-results" / f"{attempt_id}.json", "terminal result")
    terminal = next((item for item in state["events"] if item.get("attempt_id") == attempt_id and item["event_type"] == "ATTEMPT_TERMINAL"), None)
    if terminal is None or terminal.get("artifact_digest") != result.get("digest"):
        raise ValueError("terminal result binding is invalid")
    return result

# a kind -> its check (``pilot_state._validate_factual_receipt`` takes it from here)
RECEIPT_CHECKS = {"review-canaries": _check_review_canaries}
