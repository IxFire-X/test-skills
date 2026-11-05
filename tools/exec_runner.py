"""P4b: what the runner takes from a human's run of the build — ``ingest_reports`` builds the report of the closed request from the reports'
bytes alone.  The package starts no process; the evidence, the receipts and the checks stay in ``run_tests``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from tools.run_tests import (
    DurableNativeReportError,
    ProcessOutcome,
    classify_evidence,
    validate_artifact_runner_compatibility,
)
from tools.execution_core import (
    attempt_execution_scope as _attempt_execution_scope,
    authorization_allows_execution as _authorization_allows_execution,
    changed_file_ids as _changed_file_ids,
    runner_diag as _diag,
    durable_delta_allows_execution as _durable_delta_allows_execution,
    durable_execution_artifacts as _durable_execution_artifacts,
    failed_methods as _failed_methods,
    post_run_file_digests as _post_run_file_digests,
    process_evidence_row as _process_row,
    records_from_snapshots as _records_from_snapshots,
    runner_report as _report,
    report_digest_evidence as _report_digest_evidence,
    request_is_attempt_local as _request_is_attempt_local,
    request_report_path as _request_report_path,
)


def _durable_execution_start_events(run_root: Path | None, attempt_id: str | None) -> tuple[Mapping[str, Any], ...] | None:
    """Read the current attempt's durable execution-start events, if available."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return None
    try:
        from tools.pilot_state import derive_state

        return tuple(
            event for event in derive_state(run_root).get("events", [])
            if event.get("event_type") == "EXECUTION_STARTED"
            and event.get("attempt_id") == attempt_id
        )
    except (OSError, TypeError, ValueError):
        return None


def _has_one_new_durable_execution_start(run_root: Path | None, attempt_id: str | None, request: Any) -> bool:
    """Return whether the callback created one request-bound execution start.

    A callback is only the controller's opportunity to make the atomic claim.  It
    is not proof that the claim happened: the runner must read the append-only
    state itself immediately before it can invoke an adapter.  A pre-existing
    start is an interrupted execution, never authorization to replay it.
    """
    try:
        from tools.execution_adapters import request_digest

        expected = request_digest(request)
    except (OSError, TypeError, ValueError):
        return False
    starts = _durable_execution_start_events(run_root, attempt_id)
    return starts is not None and len(starts) == 1 and starts[0].get("artifact_digest") == expected


def report_set_problems(snapshots: Mapping[Any, bytes], compatibility: Any, source: Mapping[str, Any], language: str) -> list[str]:
    """P4: what is wrong with a set of reports a human brought, against the tests of the attempt — extra, missing, duplicate,
    malformed; empty when the set is exactly the reviewed tests (or a report that proves zero collected tests)."""
    from tools.execution_adapters import is_zero_test_report

    if snapshots and all(is_zero_test_report(content) for content in snapshots.values()):
        return []
    evidence, errors = _records_from_snapshots(dict(snapshots), compatibility, "RUN-check", source, java=language == "java")
    problems = list(errors)
    if len(evidence) != len(compatibility.required_pairs):
        problems.append(f"reports carry {len(evidence)} of the {len(compatibility.required_pairs)} reviewed tests")
    return problems


def ingest_reports(request: Any, authorization_receipt: Mapping[str, Any], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], *, host_isolation_receipt: Mapping[str, Any] | None, generated_delta_receipt: Mapping[str, Any] | None, run_root: Path, attempt_id: str, snapshots: Mapping[Path, bytes], execution_source: Mapping[str, Any], run_id: str, ran_at: str, duration_sec: float, on_execution_start: Callable[[Any], None], post_execution_check: Callable[[], str | None] | None = None) -> dict[str, Any]:
    """P4: the result of a run of the closed request that a human made — the report ``run_tests_v5`` would build from the run, built from the
    reports' bytes alone: no process, no clock, no environment.  The same checks of the attempt first; the execution-start claim is made here once
    (a claim found for this very request, left by a cut ingest, is the same one)."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256, validate_accepted_autotest_review
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, is_zero_test_report

    artifacts = automation_artifact.get("artifacts", {}) if isinstance(automation_artifact, Mapping) else {}
    source = artifacts.get("source", {"document_id": canonical_document.get("document_id", ""), "revision": canonical_document.get("revision", 1), "source_digest": "sha256:" + "0" * 64})
    automation_digest = automation_sha256(automation_artifact)
    review_digest = autotest_review_sha256(autotest_review_artifact)
    language = "java" if request.adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} else "python"
    target = {
        PYTEST: ("pytest", PYTEST), MAVEN: ("maven", MAVEN), SYSTEM_MAVEN: ("maven", SYSTEM_MAVEN), GRADLE: ("gradle", GRADLE),
    }[request.adapter_id]
    scope = _attempt_execution_scope(run_root, attempt_id)
    if scope is None:
        return _report("NOT_RUNNABLE", Path(request.cwd), language, source, automation_digest, review_digest, [_diag("/attempt", "RUNNER_ATTEMPT_SCOPE", "Execution requires one durable attempt scope.")], request=request)
    attempt_project, project = scope[0], scope[1]

    def not_runnable(diagnostics: Any) -> dict[str, Any]:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, diagnostics, request=request, run_root=run_root, attempt_id=attempt_id)

    if not _request_is_attempt_local(request, scope[0], scope[1]) or _request_report_path(request) is None:
        # a request that is not the module's own is not bound into the receipt: its digest would name something that was never admitted
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/request", "RUNNER_REQUEST_PROVENANCE", "Execution request must use the exact attempt module and declared closed runtime.")])
    if not _authorization_allows_execution(authorization_receipt, run_root):
        return not_runnable([_diag("/authorization", "RUNNER_AUTHORIZATION", "Execution requires one explicit full-pipeline authorization receipt.")])
    if not _durable_delta_allows_execution(generated_delta_receipt, attempt_project, run_root, attempt_id, automation_digest, review_digest, automation_artifact):
        return not_runnable([_diag("/generated_delta", "RUNNER_GENERATED_DELTA", "Execution requires the exact durable materialization readback.")])
    review_rows = validate_accepted_autotest_review(
        autotest_review_artifact, automation_artifact, dict(canonical_document),
        host_isolation_receipt=host_isolation_receipt, run_root=run_root, attempt_id=attempt_id,
    )
    if review_rows:
        return not_runnable(review_rows)
    compatibility = validate_artifact_runner_compatibility(scope[1], language, canonical_document, automation_artifact)
    if compatibility.status != "READY":
        return not_runnable(compatibility.diagnostics)
    expected_selectors = {
        str(binding["relative_path"]) + "::" + str(binding["node"])
        if language == "python" else str(binding["locator"]["class_fqn"]) + "#" + str(binding["locator"]["method_name"])
        for binding in compatibility.bindings.values()
    }
    if set(request.selectors) != expected_selectors:
        return not_runnable([_diag("/selectors", "RUNNER_SELECTORS", "Request selectors must equal exact reviewed node IDs.")])
    if not _durable_execution_start_events(run_root, attempt_id):
        try:
            on_execution_start(request)
        except Exception:
            return not_runnable([_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim was not durably recorded.")])
    if not _has_one_new_durable_execution_start(run_root, attempt_id, request):
        return not_runnable([_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim is absent, ambiguous, or bound to another request.")])
    outcome = ProcessOutcome(0, "", "", "NOT_OBSERVED")
    evidence, match_errors = _records_from_snapshots(dict(snapshots), compatibility, run_id, source, java=language == "java")
    zero_report = bool(snapshots) and all(is_zero_test_report(content) for content in snapshots.values())
    if zero_report:
        classified, report_bytes = "FAIL", next(iter(snapshots.values()))
    elif len(evidence) == len(compatibility.required_pairs) and not match_errors:
        classified, report_bytes = classify_evidence(evidence, controller_timed_out=False), b"framework-evidence"
    else:
        classified, report_bytes = "UNKNOWN", None
    post_execution_reason = None
    if post_execution_check is not None:
        try:
            post_execution_reason = post_execution_check()
        except Exception:
            post_execution_reason = "BASELINE_DRIFT"
    if (match_errors and not zero_report) or post_execution_reason:
        classified = "UNKNOWN"
    process_evidence: list[dict[str, Any]] = []
    if post_execution_reason:
        process_evidence.append(_process_row("BASELINE_DRIFT", outcome, run_id, source, target[1], duration_sec))
    elif match_errors and not zero_report:
        process_evidence.append(_process_row("JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec))
    changed_file_ids = _changed_file_ids(compatibility, _post_run_file_digests(compatibility))
    if changed_file_ids:
        evidence = [row for row in evidence if row["file_id"] not in changed_file_ids]
        process_evidence.append(_process_row("SOURCE_CHANGED", outcome, run_id, source, target[1], duration_sec, changed_file_ids))
        classified = "UNKNOWN"
    zero_collect = classified == "FAIL" and not evidence and zero_report
    if zero_collect and not process_evidence:
        process_evidence.append(_process_row("NO_TESTS_COLLECTED", outcome, run_id, source, target[1], duration_sec))
    authoritative = (
        classified in {"PASS", "FAIL"} and (not match_errors or zero_collect) and not changed_file_ids
        and (len(evidence) == len(compatibility.required_pairs) or zero_collect)
    )
    if classified == "UNKNOWN" and not process_evidence:
        process_evidence.append(_process_row("JUNIT_MISSING" if report_bytes is None else "JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec))
    try:
        report_evidence, artifact_evidence = _durable_execution_artifacts(snapshots, scope[1], generated_delta_receipt, outcome, run_root, attempt_id)
    except DurableNativeReportError:
        report_evidence = _report_digest_evidence(snapshots, scope[1])
        _unused, artifact_evidence = _durable_execution_artifacts({}, scope[1], generated_delta_receipt, outcome, run_root, attempt_id)
        classified, evidence, authoritative = "UNKNOWN", [], False
        process_evidence = [row for row in process_evidence if row.get("kind") == "SOURCE_CHANGED"]
        process_evidence.append(_process_row("JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec))
    failed_methods = _failed_methods(dict(snapshots), compatibility, evidence, java=language == "java") if classified == "FAIL" and authoritative else None
    return _report(classified, project, language, source, automation_digest, review_digest, (), run_id, evidence, process_evidence, authoritative, None, target[0], request.executable, target[1], duration_sec, request.environment_labels, request=request, report_evidence=report_evidence, artifact_evidence=artifact_evidence, run_root=run_root, attempt_id=attempt_id, failed_methods=failed_methods, execution_source=execution_source, ran_at=ran_at)

