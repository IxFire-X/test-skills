"""Deterministic Phase 7 terminal-closure primitives.

The controller owns durable storage and event publication.  This module owns the
facts which must be established *before* that terminal transition: a complete
generated-file disposition, a pre-finalization trace, and a finalization receipt.
It deliberately does not run an executor or mutate prior receipts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.generated_delta import GeneratedDeltaError, apply_dispositions, inspect_attempt_delta, resolve_disposition_policy
from tools import pilot_state


class FinalizationError(ValueError):
    """A closure input is incomplete, contradictory, or not safely readable."""


_DIGEST = "sha256:" + "0" * 64
_VERIFICATIONS = {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"}
_DISPOSITIONS = {
    "RETAINED", "CLEANED", "NOT_MATERIALIZED", "PRESERVED_EXECUTION_UNKNOWN",
    "PRESERVED_CONTENT_CONFLICT", "PRESERVED_CLEANUP_CONFLICT",
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _digest(value: Mapping[str, Any]) -> str:
    data = value if isinstance(value, bytes) else _canonical(value)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("digest", None)
    result["digest"] = _digest(result)
    return result


def _valid_digest(value: Mapping[str, Any]) -> bool:
    digest = value.get("digest")
    return isinstance(digest, str) and _seal(value).get("digest") == digest


def _files(delta: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = delta.get("files")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise FinalizationError("generated delta lacks a complete file set")
    paths = [row.get("path") for row in rows]
    if any(not isinstance(path, str) or not path for path in paths) or len(set(paths)) != len(paths):
        raise FinalizationError("generated delta has invalid file identities")
    return rows


def decide_dispositions(
    project: Path,
    delta: Mapping[str, Any],
    verification: str,
    *,
    pre_trace_valid: bool,
    run_root: Path,
    attempt_id: str,
    execution_unknown_evidence_digest: str | None = None,
) -> Mapping[str, Any]:
    """Apply the frozen complete-file disposition matrix without executing code.

    The returned delta is resealed.  For UNKNOWN the current bytes are inspected
    solely to distinguish preservation with no drift from content conflict; no
    removal function is called on that path.
    """
    if verification not in _VERIFICATIONS:
        raise FinalizationError("unknown verification")
    inspection = inspect_attempt_delta(project, delta, run_root=run_root, attempt_id=attempt_id)
    if not inspection.get("valid"):
        raise FinalizationError("generated delta digest does not verify")
    states = {row.get("path"): row.get("state") for row in inspection.get("files", []) if isinstance(row, Mapping)}
    rows = _files(delta)
    partial = delta.get("facts", {}).get("completion") == "PARTIAL"
    if verification == "UNKNOWN" and not (
        isinstance(execution_unknown_evidence_digest, str)
        and execution_unknown_evidence_digest.startswith("sha256:")
        and len(execution_unknown_evidence_digest) == 71
    ):
        raise FinalizationError("UNKNOWN disposition requires execution-unknown evidence")
    if verification != "UNKNOWN" and execution_unknown_evidence_digest is not None:
        raise FinalizationError("execution-unknown evidence is only valid for UNKNOWN")
    requested: dict[str, str] = {}
    for row in rows:
        path = str(row["path"])
        materialization = str(row.get("materialization"))
        if verification == "UNKNOWN" and materialization == "MATERIALIZED":
            state = states.get(path)
            if state not in {"UNCHANGED_OWNED", "CONTENT_DRIFT", "OWNERSHIP_CONFLICT"}:
                raise FinalizationError("UNKNOWN closure requires Phase5 per-file public state")
        if verification == "NOT_APPLICABLE" and materialization == "MATERIALIZED" and not partial:
            raise FinalizationError("unsupported generated-delta disposition branch")
        try:
            _operation, requested[path], _outcomes = resolve_disposition_policy(
                verification,
                materialization,
                retain_pass=verification == "PASS" and pre_trace_valid,
            )
        except GeneratedDeltaError as error:
            raise FinalizationError(str(error)) from error
    try:
        result = dict(
            apply_dispositions(
                Path(project),
                delta,
                requested,
                run_root=run_root,
                attempt_id=attempt_id,
                verification=verification,
                execution_unknown_evidence_digest=execution_unknown_evidence_digest,
            )
        )
    except GeneratedDeltaError as error:
        raise FinalizationError(str(error)) from error
    # `apply_dispositions` returns a resealed Phase5 artifact.  Phase7 never
    # reseals or enriches it; the authoritative reason is Phase5-owned.
    return result


def disposition_receipt(delta: Mapping[str, Any] | None, *, verification: str) -> Mapping[str, Any]:
    """Return a sealed trace projection of one complete generated-file disposition.

    Durable local-pilot closure uses the exact Phase 5 receipt published by
    ``apply_dispositions``.  The compact projection remains useful for pure
    validation and cases-only branches, but is never published as a second
    disposition authority.
    """
    if delta is None:
        return _seal({"schema_version": "1.0.0", "stage": "dispositions", "generated_delta_digest": None, "verification": "NOT_APPLICABLE", "files": []})
    if delta.get("stage") == "dispositions":
        if not _valid_digest(delta) or delta.get("verification") != verification:
            raise FinalizationError("durable disposition receipt does not verify")
        rows = delta.get("files")
        if not isinstance(rows, list) or not rows or any(
            not isinstance(row, Mapping) or row.get("disposition") not in _DISPOSITIONS
            for row in rows
        ):
            raise FinalizationError("disposition is incomplete")
        return dict(delta)
    rows = _files(delta)
    if any(row.get("disposition") not in _DISPOSITIONS for row in rows):
        raise FinalizationError("disposition is incomplete")
    return _seal({
        "schema_version": "1.0.0", "stage": "dispositions", "generated_delta_digest": delta.get("digest"),
        "verification": verification,
        "files": [{key: row[key] for key in ("file_id", "path", "content_digest", "ownership_digest", "baseline_absent", "materialization", "disposition") if key in row} | ({"reason_code": row["reason_code"]} if "reason_code" in row else {}) for row in rows],
    })


def build_pre_finalization_trace(branch: Mapping[str, Any], dispositions: Mapping[str, Any]) -> Mapping[str, Any]:
    """Build the immutable trace consumed by finalization (never its result)."""
    if not _valid_digest(dispositions) or dispositions.get("stage") != "dispositions":
        raise FinalizationError("disposition receipt does not verify")
    profile = branch.get("policy_profile")
    if profile not in {"cases-only-v1", "local-pilot-v1"}:
        raise FinalizationError("unknown policy profile")
    canonical = branch.get("canonical_digest")
    effective = branch.get("effective_canonical_digest")
    reviewer = branch.get("reviewer")
    if not isinstance(canonical, str) or not canonical.startswith("sha256:") or (
        effective is not None
        and (not isinstance(effective, str) or not effective.startswith("sha256:"))
    ):
        raise FinalizationError("canonical/effective lineage is invalid")
    reviewer_keys = {
        "digest", "session_complete", "authoritative_verdict",
        "authoritative_verdict_count", "isolation",
    }
    if not isinstance(reviewer, Mapping) or frozenset(reviewer) not in {
        frozenset(reviewer_keys), frozenset(reviewer_keys | {"pre_verdict_abort"}),
    }:
        raise FinalizationError("reviewer session evidence is incomplete")
    normalized_reviewer = dict(reviewer)
    normalized_reviewer.setdefault("pre_verdict_abort", False)
    complete_reviewer = (
        normalized_reviewer.get("session_complete") is True
        and normalized_reviewer.get("authoritative_verdict") in {"ACCEPTED", "REJECTED"}
        and normalized_reviewer.get("authoritative_verdict_count") == 1
        and normalized_reviewer.get("pre_verdict_abort") is False
    )
    aborted_reviewer = (
        normalized_reviewer.get("session_complete") is False
        and normalized_reviewer.get("authoritative_verdict") is None
        and normalized_reviewer.get("authoritative_verdict_count") == 0
        and normalized_reviewer.get("pre_verdict_abort") is True
    )
    if not isinstance(normalized_reviewer.get("digest"), str) or not (complete_reviewer or aborted_reviewer) or normalized_reviewer.get("isolation") not in {"verified", "independence_unverified"}:
        raise FinalizationError("reviewer session evidence is invalid")
    effective_required = (
        complete_reviewer
        and normalized_reviewer.get("authoritative_verdict") == "ACCEPTED"
        and normalized_reviewer.get("isolation") == "verified"
    )
    if (effective is not None) != effective_required:
        raise FinalizationError("effective canonical does not match reviewer branch")
    execution = dict(branch.get("execution", {}))
    rows = dispositions.get("files")
    if not isinstance(rows, list):
        raise FinalizationError("disposition receipt file set is invalid")
    evidence = dict(branch.get("evidence", {}))
    if profile == "cases-only-v1":
        evidence.setdefault("materialization", "NOT_APPLICABLE")
        evidence.setdefault("execution", "NOT_APPLICABLE")
        evidence.setdefault("dispositions", "NOT_APPLICABLE")
    elif not rows:
        evidence.setdefault("materialization", "NOT_APPLICABLE")
        evidence.setdefault("execution", "NOT_APPLICABLE")
        evidence.setdefault("dispositions", "NOT_APPLICABLE")
    else:
        evidence.setdefault("materialization", "PRESENT")
        evidence.setdefault(
            "execution",
            "NOT_APPLICABLE" if execution.get("verification") == "NOT_APPLICABLE" else "PRESENT",
        )
        evidence.setdefault("dispositions", "PRESENT")
    if any(evidence.get(key) not in {"REQUIRED", "NOT_APPLICABLE", "PRESENT"} for key in ("materialization", "execution", "dispositions")):
        raise FinalizationError("invalid applicability evidence")
    no_execution_trace = {
        "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
        "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
    }
    supplied_trace = branch.get("execution_trace")
    resume_validation_digest = branch.get("resume_validation_digest")
    if resume_validation_digest is not None and (
        not isinstance(resume_validation_digest, str)
        or not resume_validation_digest.startswith("sha256:")
    ):
        raise FinalizationError("resume validation binding is invalid")
    if profile == "cases-only-v1" or not rows or execution.get("verification") == "NOT_APPLICABLE":
        execution_trace = no_execution_trace
    else:
        required_trace_keys = set(no_execution_trace)
        if (
            not isinstance(supplied_trace, Mapping)
            or set(supplied_trace) != required_trace_keys
            or supplied_trace.get("applicability") != "PRESENT"
            or any(
                not isinstance(supplied_trace.get(key), str)
                or not supplied_trace[key].startswith("sha256:")
                for key in ("trace_receipt_digest", "trace_sha256", "audit_receipt_digest")
            )
            or not isinstance(supplied_trace.get("audit_valid"), bool)
        ):
            raise FinalizationError("execution trace receipt pair is incomplete")
        execution_trace = dict(supplied_trace)
    return _seal({
        "schema_version": "1.0.0", "stage": "pre_finalization_trace", "policy_profile": profile,
        "run_id": branch.get("run_id"), "attempt_id": branch.get("attempt_id"),
        "canonical_digest": canonical, "effective_canonical_digest": effective,
        "reviewer": normalized_reviewer, "execution_trace": execution_trace,
        "execution": execution, "evidence": evidence,
        "resume_validation_digest": resume_validation_digest,
        "generated_delta_digest": dispositions.get("generated_delta_digest"),
        "disposition_receipt_digest": dispositions["digest"] if rows else None,
        "disposition_verification": dispositions.get("verification") if rows else None,
        "dispositions": rows,
        "stage_causes": list(branch.get("stage_causes", [])),
    })


def _required_trace_artifacts(pre_trace: Mapping[str, Any]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    candidates = {
        "canonical_digest": pre_trace.get("canonical_digest"),
        "effective_canonical_digest": pre_trace.get("effective_canonical_digest"),
        "reviewer_session_digest": pre_trace.get("reviewer", {}).get("digest") if isinstance(pre_trace.get("reviewer"), Mapping) else None,
        "generated_delta_digest": pre_trace.get("generated_delta_digest"),
        "disposition_receipt_digest": pre_trace.get("disposition_receipt_digest"),
        "resume_validation_digest": pre_trace.get("resume_validation_digest"),
    }
    execution_trace = pre_trace.get("execution_trace")
    if isinstance(execution_trace, Mapping):
        candidates.update({
            "execution_trace_receipt_digest": execution_trace.get("trace_receipt_digest"),
            "execution_trace_sha256": execution_trace.get("trace_sha256"),
            "trace_audit_receipt_digest": execution_trace.get("audit_receipt_digest"),
        })
    execution = pre_trace.get("execution")
    if isinstance(execution, Mapping):
        candidates.update({
            key: execution.get(key)
            for key in (
                "execution_receipt_digest", "framework_evidence_digest",
                "environment_receipt_digest",
            )
        })
    for key, value in candidates.items():
        if isinstance(value, str):
            bindings[key] = value
    return bindings


def _finalization_verifier_inputs(
    pre_trace: Mapping[str, Any],
    current_resume_validation: object,
) -> dict[str, Any]:
    required = _required_trace_artifacts(pre_trace)
    inputs: dict[str, Any] = {
        "run_id": pre_trace["run_id"],
        "attempt_id": pre_trace["attempt_id"],
        "policy_profile": pre_trace["policy_profile"],
        "pre_finalization_trace_digest": pre_trace["digest"],
        "required_artifacts": required,
    }
    if (
        isinstance(current_resume_validation, str)
        and current_resume_validation != pre_trace.get("resume_validation_digest")
    ):
        inputs["post_pretrace_validation_digest"] = current_resume_validation
        required["post_pretrace_validation_digest"] = current_resume_validation
    return inputs


def verify_finalization(inputs: Mapping[str, Any], pre_trace: Mapping[str, Any]) -> Mapping[str, Any]:
    """Verify pre-finalization evidence and return a complete, sealed receipt.

    A false validity is a factual receipt, not an exception: the caller must still
    read it back and terminalize with FINALIZATION_INVALID.
    """
    errors: list[str] = []
    if not _valid_digest(pre_trace) or pre_trace.get("stage") != "pre_finalization_trace":
        errors.append("PRE_TRACE_INVALID")
    expected = inputs.get("pre_finalization_trace_digest")
    if expected != pre_trace.get("digest"):
        errors.append("PRE_TRACE_DIGEST_MISMATCH")
    for key in ("run_id", "attempt_id", "policy_profile"):
        if inputs.get(key) != pre_trace.get(key):
            errors.append(f"IDENTITY_MISMATCH:{key}")
    required = inputs.get("required_artifacts")
    expected_required = _required_trace_artifacts(pre_trace)
    post_pretrace_validation = inputs.get("post_pretrace_validation_digest")
    if isinstance(post_pretrace_validation, str):
        expected_required["post_pretrace_validation_digest"] = post_pretrace_validation
    elif post_pretrace_validation is not None:
        errors.append("POST_PRETRACE_VALIDATION_INVALID")
    if not isinstance(required, Mapping):
        errors.append("REQUIRED_ARTIFACTS_INVALID")
    elif dict(required) != expected_required:
        errors.append("REQUIRED_ARTIFACTS_MISMATCH")
    rows = pre_trace.get("dispositions")
    if not isinstance(rows, list):
        errors.append("DISPOSITIONS_MISSING")
    elif any(not isinstance(row, Mapping) or row.get("disposition") not in _DISPOSITIONS for row in rows):
        errors.append("DISPOSITIONS_INCOMPLETE")
    else:
        profile = pre_trace.get("policy_profile")
        evidence = pre_trace.get("evidence")
        reviewer = pre_trace.get("reviewer")
        complete_reviewer = isinstance(reviewer, Mapping) and reviewer.get("session_complete") is True and reviewer.get("authoritative_verdict_count") == 1 and reviewer.get("authoritative_verdict") in {"ACCEPTED", "REJECTED"} and reviewer.get("pre_verdict_abort") is False
        aborted_reviewer = isinstance(reviewer, Mapping) and reviewer.get("session_complete") is False and reviewer.get("authoritative_verdict_count") == 0 and reviewer.get("authoritative_verdict") is None and reviewer.get("pre_verdict_abort") is True
        if not (complete_reviewer or aborted_reviewer) or reviewer.get("isolation") not in {"verified", "independence_unverified"}:
            errors.append("REVIEWER_SESSION_INVALID")
        else:
            effective_required = (
                complete_reviewer
                and reviewer.get("authoritative_verdict") == "ACCEPTED"
                and reviewer.get("isolation") == "verified"
            )
            if not isinstance(pre_trace.get("canonical_digest"), str) or (
                (pre_trace.get("effective_canonical_digest") is not None) != effective_required
            ):
                errors.append("CANONICAL_LINEAGE_INVALID")
        execution_trace = pre_trace.get("execution_trace")
        if not isinstance(execution_trace, Mapping):
            errors.append("EXECUTION_TRACE_BINDING_MISSING")
        if not isinstance(evidence, Mapping):
            errors.append("APPLICABILITY_MISSING")
        elif profile == "cases-only-v1":
            if any(evidence.get(key) != "NOT_APPLICABLE" for key in ("materialization", "execution", "dispositions")) or rows or pre_trace.get("generated_delta_digest") is not None or pre_trace.get("disposition_receipt_digest") is not None or pre_trace.get("disposition_verification") is not None:
                errors.append("CASES_ONLY_APPLICABILITY_INVALID")
            if not isinstance(execution_trace, Mapping) or execution_trace != {
                "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
                "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
            }:
                errors.append("CASES_ONLY_TRACE_APPLICABILITY_INVALID")
        elif profile == "local-pilot-v1":
            applicability = tuple(evidence.get(key) for key in ("materialization", "execution", "dispositions"))
            if applicability == ("NOT_APPLICABLE", "NOT_APPLICABLE", "NOT_APPLICABLE"):
                if rows or pre_trace.get("generated_delta_digest") is not None or pre_trace.get("disposition_receipt_digest") is not None or pre_trace.get("disposition_verification") is not None:
                    errors.append("LOCAL_PREEXECUTION_APPLICABILITY_INVALID")
                if not isinstance(execution_trace, Mapping) or execution_trace != {
                    "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
                    "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
                }:
                    errors.append("LOCAL_PREEXECUTION_TRACE_INVALID")
            elif applicability[0] in {"REQUIRED", "PRESENT"} and applicability[2] in {"REQUIRED", "PRESENT"} and applicability[1] in {"NOT_APPLICABLE", "REQUIRED", "PRESENT"}:
                if not rows or not isinstance(pre_trace.get("generated_delta_digest"), str) or not isinstance(pre_trace.get("disposition_receipt_digest"), str):
                    errors.append("LOCAL_APPLICABILITY_INVALID")
                execution = pre_trace.get("execution")
                disposition_verification = pre_trace.get("disposition_verification")
                if applicability[1] == "NOT_APPLICABLE":
                    if not isinstance(execution, Mapping) or execution.get("verification") != "NOT_APPLICABLE" or disposition_verification != "NOT_APPLICABLE":
                        errors.append("LOCAL_EXECUTION_APPLICABILITY_INVALID")
                elif not isinstance(execution, Mapping) or execution.get("verification") not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE"} or not isinstance(execution.get("execution_receipt_digest"), str) or disposition_verification != execution.get("verification"):
                    errors.append("LOCAL_EXECUTION_EVIDENCE_INVALID")
                if applicability[1] != "NOT_APPLICABLE":
                    if (
                        not isinstance(execution_trace, Mapping)
                        or execution_trace.get("applicability") != "PRESENT"
                        or any(
                            not isinstance(execution_trace.get(key), str)
                            for key in ("trace_receipt_digest", "trace_sha256", "audit_receipt_digest")
                        )
                    ):
                        errors.append("LOCAL_EXECUTION_TRACE_INVALID")
                    elif execution_trace.get("audit_valid") is not True:
                        errors.append("TRACE_AUDIT_INVALID")
                for row in rows:
                    if not row.get("baseline_absent") or not isinstance(row.get("ownership_digest"), str) or not isinstance(row.get("content_digest"), str):
                        errors.append("GENERATED_OWNERSHIP_INCOMPLETE")
                        break
            else:
                errors.append("LOCAL_APPLICABILITY_INVALID")
        else:
            errors.append("PROFILE_INVALID")
    return _seal({
        "schema_version": "1.0.0", "stage": "finalization",
        "run_id": pre_trace.get("run_id"), "attempt_id": pre_trace.get("attempt_id"),
        "policy_profile": pre_trace.get("policy_profile"),
        "pre_finalization_trace_digest": pre_trace.get("digest"),
        "valid": not errors, "errors": errors, "checked_artifacts": dict(required) if isinstance(required, Mapping) else {},
    })


def publish_terminal_result(finalization_receipt: Mapping[str, Any], facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Project terminal facts via the single policy-derived pilot-state projection."""
    if not _valid_digest(finalization_receipt) or finalization_receipt.get("stage") != "finalization":
        raise FinalizationError("finalization receipt does not verify")
    if "accepted" in facts:
        raise FinalizationError("accepted is policy-derived and cannot be supplied")
    profile = facts.get("policy_profile")
    if profile not in {"cases-only-v1", "local-pilot-v1"}:
        raise FinalizationError("terminal facts lack policy profile")
    if any(
        finalization_receipt.get(key) != facts.get(key)
        for key in ("run_id", "attempt_id", "policy_profile")
    ):
        raise FinalizationError("finalization receipt identity does not match terminal facts")
    result = {key: value for key, value in facts.items() if key != "policy_profile"}
    result["attempt_state"] = "TERMINAL"
    result["finalization_completed"] = True
    result["finalization_read_back"] = True
    result["finalization_valid"] = bool(finalization_receipt.get("valid"))
    try:
        return dict(pilot_state.terminal_result(result, profile))
    except ValueError as error:
        raise FinalizationError("terminal facts violate policy projection") from error


def derive_terminal_trace(pre_trace: Mapping[str, Any], finalization_receipt: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Publish a derived revision after finalization, avoiding a trace/receipt cycle."""
    if not _valid_digest(pre_trace) or not _valid_digest(finalization_receipt) or not _valid_digest(result):
        raise FinalizationError("terminal trace input does not verify")
    if any(
        pre_trace.get(key) != finalization_receipt.get(key) or pre_trace.get(key) != result.get(key)
        for key in ("run_id", "attempt_id", "policy_profile")
    ) or finalization_receipt.get("pre_finalization_trace_digest") != pre_trace.get("digest"):
        raise FinalizationError("terminal trace identities do not bind")
    return _seal({
        "schema_version": "1.0.0", "stage": "terminal_trace", "pre_finalization_trace_digest": pre_trace["digest"],
        "finalization_receipt_digest": finalization_receipt["digest"], "terminal_result_digest": result["digest"],
        "run_id": result.get("run_id"), "attempt_id": result.get("attempt_id"),
        "policy_profile": result.get("policy_profile"),
    })


def _durable_branch(
    run_root: Path,
    attempt_id: str,
    supplied: Mapping[str, Any],
    delta: Mapping[str, Any] | None,
    verification: str,
    facts: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Reconstruct authoritative branch facts from immutable attempt artifacts."""
    try:
        state = pilot_state.derive_state(run_root)
        attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
        reviewer = pilot_state.terminal_reviewer_evidence(
            run_root, attempt_id,
        )
        effective = pilot_state.read_effective_canonical_if_present(
            run_root, attempt_id,
        )
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise FinalizationError("durable canonical/reviewer lineage is unavailable") from error
    if (
        attempt.get("run_id") != facts.get("run_id")
        or attempt.get("policy_profile") != facts.get("policy_profile")
        or facts.get("attempt_id") != attempt_id
    ):
        raise FinalizationError("terminal facts do not bind the durable attempt")
    reviewer_fact_bindings = {
        "reviewer_session_complete": reviewer["session_complete"],
        "authoritative_verdict": reviewer["authoritative_verdict"],
        "authoritative_verdict_count": reviewer["authoritative_verdict_count"],
        "reviewer_pre_verdict_abort": reviewer["pre_verdict_abort"],
        "reviewer_isolation_state": reviewer["isolation"],
    }
    if any(facts.get(key) != value for key, value in reviewer_fact_bindings.items()):
        raise FinalizationError("terminal reviewer facts do not bind the durable ledger")
    verdict = reviewer["authoritative_verdict"]
    if effective is not None:
        if verdict != "ACCEPTED" or effective.get("reviewer_session_digest") != reviewer["digest"]:
            raise FinalizationError("effective canonical does not bind the reviewer verdict")
    elif verdict == "ACCEPTED" and reviewer["isolation"] == "verified":
        raise FinalizationError("accepted verified reviewer branch lacks effective canonical")
    branch_cause = "REWORK" if verdict == "REJECTED" else reviewer.get("abort_reason")
    if branch_cause is not None and (
        facts.get("prior_stage_cause") != branch_cause
        or facts.get("reason_code") not in {None, branch_cause}
    ):
        raise FinalizationError("terminal cause does not bind reviewer evidence")
    profile = str(attempt["policy_profile"])
    execution: dict[str, Any] = {"verification": "NOT_APPLICABLE"}
    execution_trace: dict[str, Any] = {
        "applicability": "NOT_APPLICABLE", "trace_receipt_digest": None,
        "trace_sha256": None, "audit_receipt_digest": None, "audit_valid": None,
    }
    unknown_evidence: str | None = None
    resume_validation: Mapping[str, Any] | None = None
    if facts.get("execution_applicability") == "NOT_APPLICABLE":
        try:
            pilot_state._validate_execution_event_branch(
                state["events"], attempt_id, verification, None,
            )
        except ValueError as error:
            raise FinalizationError(str(error)) from error
    if facts.get("execution_applicability") == "REQUIRED":
        if effective is None or verdict != "ACCEPTED":
            raise FinalizationError("execution requires an accepted effective canonical")
        try:
            execution_record = pilot_state.read_attempt_receipt(
                run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
            )["record"]
            report = execution_record["payload"]
            resume_validation = pilot_state.read_resume_validation_if_present(
                run_root, attempt_id,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FinalizationError("authoritative execution receipt is unavailable") from error
        operational_reason = (
            resume_validation.get("reason_code")
            if isinstance(resume_validation, Mapping)
            and resume_validation.get("status") == "DRIFTED"
            else None
        )
        if (
            not isinstance(report, Mapping)
            or report.get("verdict") != verification
            or delta is None
            or execution_record.get("generated_delta_digest") != delta.get("digest")
        ):
            raise FinalizationError("execution result does not bind the generated delta")
        try:
            if verification == "UNKNOWN":
                pilot_state.record_execution_unknown(run_root, attempt_id, str(execution_record["digest"]))
            pilot_state._validate_execution_event_branch(
                pilot_state.derive_state(run_root)["events"], attempt_id, verification,
                str(execution_record["digest"]),
            )
        except ValueError as error:
            raise FinalizationError(str(error)) from error
        execution = {
            "verification": verification,
            "execution_receipt_digest": execution_record["digest"],
        }
        automation = supplied.get("automation_artifact")
        autotest_review = supplied.get("autotest_review")
        if not isinstance(automation, Mapping) or not isinstance(autotest_review, Mapping):
            raise FinalizationError("durable execution trace sources are unavailable")
        try:
            trace_pair = pilot_state.publish_execution_trace_pair(
                run_root, attempt_id, automation, autotest_review,
            )
            trace_record = trace_pair["trace"]["record"]
            audit_record = trace_pair["audit"]["record"]
            audit_payload = audit_record["payload"]
        except (KeyError, TypeError, ValueError) as error:
            raise FinalizationError("durable execution trace pair is unavailable") from error
        execution_trace = {
            "applicability": "PRESENT",
            "trace_receipt_digest": trace_record["digest"],
            "trace_sha256": trace_record["trace_sha256"],
            "audit_receipt_digest": audit_record["digest"],
            "audit_valid": audit_payload.get("valid"),
        }
        if verification == "UNKNOWN":
            unknown_evidence = str(execution_record["digest"])
        steps = [
            step
            for case in effective["document"].get("test_cases", [])
            if isinstance(case, Mapping)
            for step in case.get("steps", [])
            if isinstance(step, Mapping)
        ]
        manual_count = sum(step.get("manual_only") is True for step in steps)
        coverage = "MANUAL_ONLY" if steps and manual_count == len(steps) else "MIXED" if manual_count else "FULL"
        blocker_count = sum(
            len(step.get("automation_blockers", []))
            for step in steps
            if isinstance(step.get("automation_blockers"), list)
        )
        derived_acceptance_facts = {
            "canonical_schema_valid": True,
            "canonical_semantics_valid": True,
            "canonical_provenance_valid": True,
            "blocker_count": blocker_count,
            "trace_valid": audit_payload.get("valid") is True,
            "automation_accepted": True,
            "exact_target_pass": verification == "PASS" and report.get("evidence_authoritative") is True,
            "mixed_manual_traceable": coverage == "MIXED" and audit_payload.get("valid") is True,
            "operational_reliable": operational_reason is None,
        }
        if (
            facts.get("coverage") != coverage
            or (operational_reason is not None and facts.get("prior_stage_cause") != operational_reason)
            or any(
            facts.get(key) != value for key, value in derived_acceptance_facts.items()
            )
        ):
            raise FinalizationError("terminal acceptance facts do not bind durable trace evidence")
    elif facts.get("execution_applicability") != "NOT_APPLICABLE":
        raise FinalizationError("execution applicability is invalid")
    elif verification != "NOT_APPLICABLE":
        raise FinalizationError("pre-execution branch has an execution verdict")
    if effective is None and (
        delta is not None
        or facts.get("materialization_applicability") != "NOT_APPLICABLE"
        or facts.get("execution_applicability") != "NOT_APPLICABLE"
    ):
        raise FinalizationError("pre-effective branch cannot claim generated or execution evidence")
    stage_causes = list(supplied.get("stage_causes", []))
    prior_cause = facts.get("prior_stage_cause")
    if isinstance(prior_cause, str) and prior_cause not in stage_causes:
        stage_causes.append(prior_cause)
    if branch_cause is not None:
        stage_causes = [str(branch_cause)]
    branch = {
        "policy_profile": profile,
        "run_id": attempt["run_id"],
        "attempt_id": attempt_id,
        "canonical_digest": reviewer["canonical_digest"],
        "effective_canonical_digest": effective["document_digest"] if effective is not None else None,
        "reviewer": {
            key: reviewer[key]
            for key in (
                "digest", "session_complete", "authoritative_verdict",
                "authoritative_verdict_count", "pre_verdict_abort", "isolation",
            )
        },
        "execution_trace": execution_trace,
        "execution": execution,
        "resume_validation_digest": (
            resume_validation.get("digest") if isinstance(resume_validation, Mapping) else None
        ),
        "evidence": dict(supplied.get("evidence", {})),
        "stage_causes": stage_causes,
    }
    if isinstance(supplied.get("finalization_inputs"), Mapping):
        branch["finalization_inputs"] = dict(supplied["finalization_inputs"])
    return branch, unknown_evidence


def finalize_attempt(
    branch: Mapping[str, Any], delta: Mapping[str, Any] | None, *, project: Path, verification: str,
    facts: Mapping[str, Any], publish: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    read: Callable[[str, str], Mapping[str, Any]] | None = None,
    run_root: Path | None = None,
) -> Mapping[str, Any]:
    """Close an attempt through injected durable callbacks, without executing tests.

    `publish` and `read` are controller seams.  A durable terminal retry returns
    the same result and records only its controller-owned idempotence proof.
    """
    if run_root is not None:
        try:
            existing = pilot_state.read_terminal_result(run_root, str(branch.get("attempt_id")))
        except KeyError:
            existing = None
        if existing is not None:
            attempt_id = str(branch.get("attempt_id"))
            state = pilot_state.derive_state(run_root)
            retry_events = [
                row for row in state["events"]
                if row.get("attempt_id") == attempt_id
                and row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
            ]
            if retry_events:
                if len(retry_events) != 1:
                    raise FinalizationError("terminal retry observation is ambiguous")
                observation = pilot_state.read_scenario_observation(
                    run_root,
                    attempt_id,
                    "terminal-idempotent-retry",
                    str(retry_events[0]["artifact_digest"]),
                )
                return {
                    "result": existing,
                    "idempotent": True,
                    "scenario_observation_receipts": {
                        "terminal-idempotent-retry": observation,
                    },
                }
            before_snapshot = pilot_state._terminal_retry_snapshot(run_root, attempt_id)
            attempt = next(
                (row for row in state["attempts"] if row["attempt_id"] == attempt_id),
                None,
            )
            after_terminal = pilot_state.read_terminal_result(run_root, attempt_id)
            after_snapshot = pilot_state._terminal_retry_snapshot(run_root, attempt_id)
            terminal_events = [
                row for row in state["events"]
                if row.get("attempt_id") == attempt_id
                and row.get("event_type") == "ATTEMPT_TERMINAL"
            ]
            later_execution = any(
                row.get("attempt_id") == attempt_id
                and row.get("event_type") == "EXECUTION_STARTED"
                and row.get("seq", 0) > terminal_events[0]["seq"]
                for row in state["events"]
            ) if terminal_events else True
            if (
                attempt is None
                or attempt.get("state") != "TERMINAL"
                or dict(existing) != dict(after_terminal)
                or dict(before_snapshot) != after_snapshot
                or len(terminal_events) != 1
                or later_execution
            ):
                raise FinalizationError("terminal retry observation precondition is invalid")
            value = {
                "schema_version": "1.0.0", "kind": "scenario-observation",
                "run_id": attempt["run_id"], "attempt_id": attempt_id,
                "policy_profile": attempt["policy_profile"],
                "scenario_id": "terminal-idempotent-retry",
                "precondition": {
                    "kind": "TERMINAL_ATTEMPT",
                    "snapshot_digest": before_snapshot["digest"],
                },
                "action": {
                    "kind": "RETRY_TERMINAL_ATTEMPT",
                    "before_digest": before_snapshot["digest"],
                    "after_digest": after_snapshot["digest"],
                },
                "before": dict(before_snapshot), "after": after_snapshot,
                "event_digests": [terminal_events[0]["digest"]],
                "receipt_digests": after_snapshot["terminal_artifact_digests"],
                "result": {"kind": "TERMINAL_RESULT", "digest": after_terminal["digest"]},
            }
            target = pilot_state._scenario_observation_target(
                Path(run_root), attempt_id, "terminal-idempotent-retry",
            )
            project_root, root = pilot_state._run_root(run_root)
            record, created, identity = pilot_state._publish(
                project_root, root, target, value, "scenario observation",
                return_created=True,
            )
            pilot_state.append_event(
                root, "ARTIFACT_PUBLISHED", actor="controller",
                artifact_digest=str(record["digest"]),
            )
            pilot_state.append_event(
                root, "ARTIFACT_READ_BACK", actor="controller",
                artifact_digest=str(record["digest"]),
            )
            pilot_state._append_event(
                project_root, root, "TERMINAL_RETRY_OBSERVED", actor="controller",
                attempt_id=attempt_id, batch_id=None,
                artifact_digest=str(record["digest"]),
            )
            observation = dict(pilot_state.read_scenario_observation(
                root, attempt_id, "terminal-idempotent-retry", str(record["digest"]),
            ))
            observation.update({"created": created, "installed_identity": identity})
            return {
                "result": existing,
                "idempotent": True,
                "scenario_observation_receipts": {
                    "terminal-idempotent-retry": observation,
                },
            }
    elif read is not None:
        try:
            existing = read("terminal_result", str(branch.get("attempt_id")))
        except (KeyError, FileNotFoundError):
            existing = None
        if isinstance(existing, Mapping):
            return {"result": existing, "idempotent": True}
    unknown_evidence: str | None = None
    if run_root is not None:
        branch, unknown_evidence = _durable_branch(
            run_root, str(branch.get("attempt_id")), branch, delta, verification, facts
        )
    if delta is not None and run_root is None:
        raise FinalizationError("generated-delta closure requires a durable attempt")
    if delta is None and branch.get("policy_profile") == "local-pilot-v1" and not (
        verification == "NOT_APPLICABLE"
        and facts.get("materialization_applicability") == "NOT_APPLICABLE"
        and facts.get("execution_applicability") == "NOT_APPLICABLE"
        and facts.get("generated_required_count") == 0
    ):
        raise FinalizationError("local-pilot generated delta is missing outside a pre-execution branch")
    trace_binding = branch.get("execution_trace")
    trace_audit_valid = (
        verification != "PASS"
        or isinstance(trace_binding, Mapping) and trace_binding.get("audit_valid") is True
    )
    durable_dispositions = None
    if delta is not None and run_root is not None:
        disposition_path = run_root / "disposition-receipts" / f"{branch['attempt_id']}.json"
        if disposition_path.exists():
            try:
                durable_dispositions = pilot_state.read_attempt_receipt(
                    run_root, str(branch["attempt_id"]), "disposition-receipt", "ARTIFACT_READ_BACK"
                )["record"]["payload"]
            except (KeyError, TypeError, ValueError) as error:
                raise FinalizationError("authoritative disposition receipt is unavailable") from error
    provisional = durable_dispositions
    if delta is not None and provisional is None:
        provisional = decide_dispositions(
            project,
            delta,
            verification,
            pre_trace_valid=trace_audit_valid,
            run_root=run_root,
            attempt_id=str(branch["attempt_id"]),
            execution_unknown_evidence_digest=unknown_evidence,
        )
    if run_root is not None and provisional is not None:
        if durable_dispositions is None:
            try:
                durable_dispositions = pilot_state.read_attempt_receipt(
                    run_root, str(branch["attempt_id"]), "disposition-receipt", "ARTIFACT_READ_BACK"
                )["record"]["payload"]
            except (KeyError, TypeError, ValueError) as error:
                raise FinalizationError("authoritative disposition receipt is unavailable") from error
        dispositions = disposition_receipt(durable_dispositions, verification=verification)
    else:
        dispositions = disposition_receipt(provisional, verification=verification)
    proposed_pre_trace = build_pre_finalization_trace(branch, dispositions)
    if run_root is not None:
        pre_trace = pilot_state.read_closure_artifact_if_present(
            run_root, str(branch["attempt_id"]), "pre_finalization_trace",
        )
        if pre_trace is None:
            pre_trace = pilot_state.publish_closure_artifact(
                run_root, str(branch["attempt_id"]), "pre_finalization_trace", proposed_pre_trace,
            )["record"]
    else:
        pre_trace = proposed_pre_trace
    verifier_inputs = _finalization_verifier_inputs(
        pre_trace, branch.get("resume_validation_digest"),
    )
    if isinstance(branch.get("finalization_inputs"), Mapping):
        verifier_inputs.update(dict(branch["finalization_inputs"]))
    receipt = verify_finalization(verifier_inputs, pre_trace)
    if run_root is not None:
        existing_receipt = pilot_state.read_closure_artifact_if_present(
            run_root, str(branch["attempt_id"]), "finalization_receipt",
        )
        if existing_receipt is None:
            receipt = pilot_state.publish_closure_artifact(run_root, str(branch["attempt_id"]), "finalization_receipt", receipt)["record"]
        else:
            receipt = existing_receipt
    elif publish is not None:
        receipt = publish("finalization_receipt", receipt)
        if read is not None:
            receipt = read("finalization_receipt", str(receipt["digest"]))
    if facts.get("policy_profile") == "local-pilot-v1" and provisional is not None:
        rows = _files(provisional)
        materialized = sum(row.get("materialization") == "MATERIALIZED" for row in rows)
        retained = sum(row.get("disposition") == "RETAINED" for row in rows)
        if (facts.get("generated_required_count"), facts.get("generated_materialized_count"), facts.get("generated_retained_count")) != (len(rows), materialized, retained):
            raise FinalizationError("terminal generated-file counts do not bind dispositions")
    terminal_facts = dict(facts)
    if receipt.get("valid") is not True and terminal_facts.get("reason_code") not in {None, "FINALIZATION_INVALID"}:
        terminal_facts["reason_code"] = None
    result = publish_terminal_result(receipt, terminal_facts)
    terminal_trace = derive_terminal_trace(pre_trace, receipt, result)
    if run_root is not None:
        result = pilot_state.publish_terminal_result(run_root, {key: value for key, value in terminal_facts.items() if key != "policy_profile"} | {"attempt_state": "TERMINAL", "finalization_completed": True, "finalization_read_back": True, "finalization_valid": bool(receipt["valid"])}, str(terminal_facts["policy_profile"]), finalization_receipt=receipt)["record"]
        terminal_trace = derive_terminal_trace(pre_trace, receipt, result)
        terminal_trace = pilot_state.publish_closure_artifact(run_root, str(branch["attempt_id"]), "terminal_trace", terminal_trace)["record"]
        pilot_state.append_event(run_root, "ATTEMPT_TERMINAL", actor="controller", attempt_id=str(branch["attempt_id"]), artifact_digest=result["digest"])
    elif publish is not None:
        result = publish("terminal_result", result)
        terminal_trace = publish("terminal_trace", terminal_trace)
        publish("terminal_event", _seal({"schema_version": "1.0.0", "attempt_id": result.get("attempt_id"), "terminal_result_digest": result["digest"]}))
    outcome = {"dispositions": dispositions, "pre_finalization_trace": pre_trace, "finalization_receipt": receipt, "result": result, "terminal_trace": terminal_trace, "idempotent": False}
    if run_root is not None:
        observations: dict[str, Mapping[str, Any]] = {}
        observation = pilot_state.publish_project_state_observation(
            run_root, str(branch["attempt_id"]),
        )
        if observation is not None:
            observations[str(observation["record"]["scenario_id"])] = observation
        observations.update(pilot_state.publish_resume_observations(
            run_root, str(branch["attempt_id"]),
        ))
        if observations:
            outcome["scenario_observation_receipts"] = observations
    return outcome


def finalize_durable_execution_attempt(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any]:
    """Close the public local-execution branch from durable evidence only."""
    try:
        existing = pilot_state.read_terminal_result(run_root, attempt_id)
    except KeyError:
        existing = None
    if existing is not None:
        closed = finalize_attempt(
            {"attempt_id": attempt_id}, None,
            project=Path(run_root), verification=str(existing.get("verification")),
            facts={}, run_root=run_root,
        )
        return {**closed, "exit_code": pilot_state.exit_code(existing)}
    try:
        state = pilot_state.derive_state(run_root)
        attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
        if attempt.get("policy_profile") != "local-pilot-v1":
            raise ValueError("public execution closure requires local-pilot-v1")
        reviewer = pilot_state.terminal_reviewer_evidence(
            run_root, attempt_id,
        )
        effective = pilot_state.read_effective_canonical(
            run_root, attempt_id,
        )
        delta = pilot_state.read_attempt_receipt(
            run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK",
        )["record"]["delta"]
        execution_record = pilot_state.read_attempt_receipt(
            run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        execution_inputs = pilot_state.read_execution_inputs(
            run_root, attempt_id,
        )
        automation_artifact = execution_inputs["automation_artifact"]
        autotest_review = execution_inputs["autotest_review"]
        resume_validation = pilot_state.read_resume_validation_if_present(
            run_root, attempt_id,
        )
        operational_reason_code = (
            resume_validation.get("reason_code")
            if isinstance(resume_validation, Mapping)
            and resume_validation.get("status") == "DRIFTED"
            else None
        )
        report = execution_record["payload"]
        verification = report["verdict"]
        if verification not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE"}:
            raise ValueError("execution receipt has no terminal verification")
        if verification == "UNKNOWN":
            pilot_state.record_execution_unknown(run_root, attempt_id, str(execution_record["digest"]))
        pilot_state._validate_execution_event_branch(
            pilot_state.derive_state(run_root)["events"], attempt_id, verification,
            str(execution_record["digest"]),
        )
        trace_pair = pilot_state.publish_execution_trace_pair(
            run_root, attempt_id, automation_artifact, autotest_review,
        )
        audit = trace_pair["audit"]["record"]["payload"]
        trace_valid = audit.get("valid") is True
        unknown_digest = str(execution_record["digest"]) if verification == "UNKNOWN" else None
        existing_plan = None
        plan_path = Path(run_root) / "disposition-plans" / f"{attempt_id}.json"
        if plan_path.exists():
            try:
                existing_plan = pilot_state.read_attempt_receipt(
                    run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK",
                )["record"]["payload"]
            except ValueError:
                existing_plan = pilot_state.recover_phase5_receipt_events(
                    run_root, attempt_id, "disposition-plan",
                )["record"]["payload"]
        if isinstance(existing_plan, Mapping):
            plan_rows = existing_plan.get("files")
            if not isinstance(plan_rows, list) or any(
                not isinstance(row, Mapping)
                or not isinstance(row.get("path"), str)
                or row.get("requested_disposition") not in _DISPOSITIONS
                for row in plan_rows
            ):
                raise ValueError("durable disposition plan is invalid")
            disposed = apply_dispositions(
                Path(attempt["project"]), delta,
                {str(row["path"]): str(row["requested_disposition"]) for row in plan_rows},
                run_root=run_root, attempt_id=attempt_id, verification=verification,
                execution_unknown_evidence_digest=unknown_digest,
            )
        else:
            disposed = decide_dispositions(
                Path(attempt["project"]), delta, verification,
                pre_trace_valid=trace_valid and operational_reason_code is None,
                run_root=run_root, attempt_id=attempt_id,
                execution_unknown_evidence_digest=unknown_digest,
            )
    except (KeyError, StopIteration, TypeError, ValueError, OSError) as error:
        raise FinalizationError("durable execution closure evidence is unavailable") from error

    steps = [
        step
        for case in effective["document"].get("test_cases", [])
        if isinstance(case, Mapping)
        for step in case.get("steps", [])
        if isinstance(step, Mapping)
    ]
    manual_count = sum(step.get("manual_only") is True for step in steps)
    coverage = "MANUAL_ONLY" if steps and manual_count == len(steps) else "MIXED" if manual_count else "FULL"
    blocker_count = sum(
        len(step.get("automation_blockers", []))
        for step in steps
        if isinstance(step.get("automation_blockers"), list)
    )
    files = _files(disposed)
    materialized_count = sum(row.get("materialization") == "MATERIALIZED" for row in files)
    retained_count = sum(row.get("disposition") == "RETAINED" for row in files)
    diagnostic_codes = [
        str(row["code"])
        for row in report.get("diagnostics", [])
        if isinstance(row, Mapping) and isinstance(row.get("code"), str)
    ]
    execution_stage_cause = {
        "PASS": "EXECUTION_COMPLETE",
        "FAIL": "EXECUTION_FAIL",
        "UNKNOWN": "EXECUTION_UNKNOWN",
        "NOT_RUNNABLE": diagnostic_codes[0] if diagnostic_codes else "EXECUTION_NOT_RUNNABLE",
    }[verification]
    prior_stage_cause = operational_reason_code or execution_stage_cause
    zero_collect = any(
        isinstance(row, Mapping) and row.get("kind") == "NO_TESTS_COLLECTED"
        for row in report.get("process_evidence", ())
    )
    reason_code = (
        "EXECUTION_UNKNOWN" if verification == "UNKNOWN"
        else "NO_TESTS_COLLECTED" if verification == "FAIL" and zero_collect
        else "BASELINE_INCOMPLETE" if "BASELINE_INCOMPLETE" in diagnostic_codes
        else operational_reason_code
    )
    facts = {
        "run_id": attempt["run_id"], "attempt_id": attempt_id, "attempt_state": "TERMINAL",
        "completion": "PARTIAL" if verification == "UNKNOWN" else "COMPLETE",
        "verification": verification, "coverage": coverage, "reason_code": reason_code,
        "canonical_schema_valid": True, "canonical_semantics_valid": True,
        "canonical_provenance_valid": True,
        "reviewer_session_complete": reviewer["session_complete"],
        "authoritative_verdict": reviewer["authoritative_verdict"],
        "authoritative_verdict_count": reviewer["authoritative_verdict_count"],
        "reviewer_pre_verdict_abort": reviewer["pre_verdict_abort"],
        "reviewer_isolation_state": reviewer["isolation"], "blocker_count": blocker_count,
        "trace_valid": trace_valid, "finalization_completed": True,
        "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "REQUIRED", "execution_applicability": "REQUIRED",
        "automation_accepted": True, "generated_required_count": len(files),
        "generated_materialized_count": materialized_count,
        "generated_retained_count": retained_count,
        "exact_target_pass": verification == "PASS" and report.get("evidence_authoritative") is True,
        "mixed_manual_traceable": coverage == "MIXED" and trace_valid,
        "operational_reliable": operational_reason_code is None,
        "prior_stage_cause": prior_stage_cause,
        "policy_profile": attempt["policy_profile"],
    }
    branch = {
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"],
        "automation_artifact": dict(automation_artifact),
        "autotest_review": dict(autotest_review),
        "canonical_document": dict(effective["document"]),
        "stage_causes": [execution_stage_cause] + (
            [operational_reason_code]
            if operational_reason_code is not None and operational_reason_code != execution_stage_cause
            else []
        ),
        "evidence": {},
    }
    closed = finalize_attempt(
        branch, delta, project=Path(attempt["project"]), verification=verification,
        facts=facts, run_root=run_root,
    )
    result = closed["result"]
    return {**closed, "exit_code": pilot_state.exit_code(result)}
