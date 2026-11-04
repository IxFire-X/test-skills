"""P8a-1: the process launch of the generated-test runner — the subprocess primitive, the Windows Job Object, the process-tree
stop and ``run_tests_v5`` that drives them.  The evidence, the receipts and the checks stay in ``run_tests``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from tools.run_tests import (
    PROCESS_OUTPUT_TAIL_BYTES,
    _PROCESS_STOP_ATTESTATION,
    _TIMEOUT,
    DurableNativeReportError,
    ProcessOutcome,
    RunnerInputError,
    _attempt_execution_scope,
    _authorization_allows_execution,
    _changed_file_ids,
    _diag,
    _durable_delta_allows_execution,
    _durable_execution_artifacts,
    _failed_methods,
    _outcome,
    _post_run_file_digests,
    _process_row,
    _records_from_snapshots,
    _report,
    _report_digest_evidence,
    _report_inventory,
    _report_snapshots,
    _request_is_attempt_local,
    _request_report_path,
    classify_evidence,
    validate_artifact_runner_compatibility,
)


def run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None = None, *, timeout: int = _TIMEOUT) -> ProcessOutcome:
    # ponytail: temporary files bound RAM; add a disk quota if noisy runners require it.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        return _run_subprocess(command, project, environment, timeout, stdout_file, stderr_file)


def _output_tail(stream: Any) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - PROCESS_OUTPUT_TAIL_BYTES))
    data = stream.read(PROCESS_OUTPUT_TAIL_BYTES)
    if size > PROCESS_OUTPUT_TAIL_BYTES:
        # Never publish the suffix of a truncated credential-bearing line.
        data = data.partition(b"\n")[2]
    return data.decode("utf-8", errors="replace")


def _run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None, timeout: int, stdout_file: Any, stderr_file: Any) -> ProcessOutcome:
    kwargs: dict[str, Any] = {
        "cwd": project,
        "stdout": stdout_file,
        "stderr": stderr_file,
        "env": dict(environment) if environment is not None else dict(os.environ),
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError:
        return ProcessOutcome(127, "", "", "OS_ERROR")
    windows_job = _assign_windows_kill_job(process) if os.name == "nt" else None
    try:
        process.wait(timeout=timeout)
        return ProcessOutcome(process.returncode, _output_tail(stdout_file), _output_tail(stderr_file))
    except subprocess.TimeoutExpired:
        stop_proof = _terminate_process_tree(process, windows_job=windows_job)
        windows_job = None
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stop_proof = None
        if stop_proof == "POSIX_PROCESS_GROUP":
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            except OSError:
                stop_proof = None
            else:
                stop_proof = None
        return ProcessOutcome(
            124, _output_tail(stdout_file), _output_tail(stderr_file), "TIMEOUT",
            process_scope_stopped=stop_proof is not None, stop_proof=stop_proof,
            _stop_attestation=_PROCESS_STOP_ATTESTATION if stop_proof is not None else None,
        )
    finally:
        if windows_job is not None:
            _close_windows_handle(windows_job)


def _close_windows_handle(handle: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    return bool(kernel32.CloseHandle(ctypes.c_void_p(handle)))


def _assign_windows_kill_job(process: subprocess.Popen[str]) -> int | None:
    """Put one trusted-project process tree in a kill-on-close Windows job."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(job, int(process._handle))
    if not assigned:
        _close_windows_handle(int(job))
        return None
    return int(job)


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    windows_job: int | None = None,
) -> Literal["WINDOWS_JOB_OBJECT", "WINDOWS_TASKKILL_TREE", "POSIX_PROCESS_GROUP"] | None:
    if os.name != "nt":
        try:
            os.killpg(process.pid, 9)
            return "POSIX_PROCESS_GROUP"
        except OSError:
            pass
    else:
        if windows_job is not None and _close_windows_handle(windows_job):
            # Popen cannot assign the Job Object before the process starts;
            # cleanup is useful, but not a proof that no child escaped first.
            return None
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
    return None


def _invoke_closed_request(argv: tuple[str, ...], *, cwd: str, timeout: int, shell: bool) -> ProcessOutcome:
    """Bridge the adapter's immutable launch shape to the local subprocess primitive."""
    if shell is not False:
        raise RunnerInputError("RUNNER_REQUEST", [_diag("/request", "RUNNER_REQUEST", "Shell execution is forbidden.")])
    return run_subprocess(list(argv), Path(cwd), timeout=timeout)


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


def run_tests_v5(request: Any, authorization_receipt: Mapping[str, Any], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], autotest_review_artifact: Mapping[str, Any], *, host_isolation_receipt: Mapping[str, Any] | None = None, generated_delta_receipt: Mapping[str, Any] | None = None, run_root: Path | None = None, attempt_id: str | None = None, on_execution_start: Callable[[Any], None] | None = None, post_execution_check: Callable[[], str | None] | None = None) -> dict[str, Any]:
    """Execute exactly one closed project-native adapter request after authorization."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256, validate_accepted_autotest_review
    from tools.execution_adapters import (
        GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, classify_zero_report, invoke_request, is_zero_test_report,
    )

    artifacts = automation_artifact.get("artifacts", {}) if isinstance(automation_artifact, Mapping) else {}
    source = artifacts.get("source", {"document_id": canonical_document.get("document_id", ""), "revision": canonical_document.get("revision", 1), "source_digest": "sha256:" + "0" * 64})
    automation_digest = automation_sha256(automation_artifact) if isinstance(automation_artifact, Mapping) else "sha256:" + "0" * 64
    review_digest = autotest_review_sha256(autotest_review_artifact) if isinstance(autotest_review_artifact, Mapping) else "sha256:" + "0" * 64
    adapter_id = getattr(request, "adapter_id", None)
    language = "java" if adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} else "python"
    target = {
        PYTEST: ("pytest", PYTEST), MAVEN: ("maven", MAVEN), SYSTEM_MAVEN: ("maven", SYSTEM_MAVEN), GRADLE: ("gradle", GRADLE),
    }.get(adapter_id, ("not_applicable", None))
    scope = _attempt_execution_scope(run_root, attempt_id)
    attempt_project = scope[0] if scope is not None else Path(getattr(request, "cwd", Path.cwd()))
    project = scope[1] if scope is not None else attempt_project
    if scope is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/attempt", "RUNNER_ATTEMPT_SCOPE", "Execution requires one durable attempt scope.")], request=request)
    if not _request_is_attempt_local(request, scope[0], scope[1]):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/request", "RUNNER_REQUEST_PROVENANCE", "Execution request must use the exact attempt module and declared closed runtime.")])
    report_path = _request_report_path(request)
    if report_path is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/request", "RUNNER_REQUEST", "Execution request is not a closed project-native adapter request.")])
    if not _authorization_allows_execution(authorization_receipt, run_root):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/authorization", "RUNNER_AUTHORIZATION", "Execution requires one explicit full-pipeline authorization receipt.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if not _durable_delta_allows_execution(
        generated_delta_receipt, attempt_project, run_root, attempt_id, automation_digest, review_digest, automation_artifact,
    ):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/generated_delta", "RUNNER_GENERATED_DELTA", "Execution requires the exact durable materialization readback.")], request=request, run_root=run_root, attempt_id=attempt_id)
    review_rows = validate_accepted_autotest_review(
        autotest_review_artifact, automation_artifact, dict(canonical_document),
        host_isolation_receipt=host_isolation_receipt, run_root=run_root, attempt_id=attempt_id,
    )
    if review_rows:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, review_rows, request=request, run_root=run_root, attempt_id=attempt_id)
    compatibility = validate_artifact_runner_compatibility(scope[1], language, canonical_document, automation_artifact)
    if compatibility.status != "READY":
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, compatibility.diagnostics, request=request, run_root=run_root, attempt_id=attempt_id)
    expected_selectors = {
        str(binding["relative_path"]) + "::" + str(binding["node"])
        if language == "python" else str(binding["locator"]["class_fqn"]) + "#" + str(binding["locator"]["method_name"])
        for binding in compatibility.bindings.values()
    }
    if set(request.selectors) != expected_selectors:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/selectors", "RUNNER_SELECTORS", "Request selectors must equal exact reviewed node IDs.")], request=request, run_root=run_root, attempt_id=attempt_id)

    if on_execution_start is None:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution requires the controller's atomic execution-start gate.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if _durable_execution_start_events(run_root, attempt_id):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution was already started and cannot be replayed automatically.")], request=request, run_root=run_root, attempt_id=attempt_id)
    try:
        on_execution_start(request)
    except Exception:
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim was not durably recorded.")], request=request, run_root=run_root, attempt_id=attempt_id)
    if not _has_one_new_durable_execution_start(run_root, attempt_id, request):
        return _report("NOT_RUNNABLE", project, language, source, automation_digest, review_digest, [_diag("/execution", "RUNNER_EXECUTION_GATE", "Execution-start claim is absent, ambiguous, or bound to another request.")], request=request, run_root=run_root, attempt_id=attempt_id)
    run_id, started = "RUN-" + uuid.uuid4().hex, time.monotonic()
    before = _report_inventory(report_path)
    outcome = _outcome(invoke_request(request, _invoke_closed_request))
    duration_sec = time.monotonic() - started
    after = _report_inventory(report_path)
    fresh = {path for path, fingerprint in after.items() if before.get(path) != fingerprint}
    snapshots = _report_snapshots(tuple(fresh))
    evidence, match_errors = _records_from_snapshots(snapshots, compatibility, run_id, source, java=language == "java")
    zero_report = bool(snapshots) and all(
        is_zero_test_report(content) for content in snapshots.values()
    )
    if zero_report:
        report_bytes = next(iter(snapshots.values()))
        classified = classify_zero_report(
            controller_timed_out=outcome.kind == "TIMEOUT",
            process_returncode=outcome.exit_code if outcome.kind == "EXIT" else None,
        )
    elif len(evidence) == len(compatibility.required_pairs) and not match_errors:
        classified = classify_evidence(evidence, controller_timed_out=outcome.kind == "TIMEOUT")
        report_bytes = b"framework-evidence"
    else:
        classified, report_bytes = "UNKNOWN", None
    post_execution_reason = None
    if post_execution_check is not None:
        try:
            post_execution_reason = post_execution_check()
        except Exception:
            post_execution_reason = "BASELINE_DRIFT"
    if outcome.kind == "OS_ERROR" or outcome.exit_code < 0 or (match_errors and not zero_report) or post_execution_reason:
        classified = "UNKNOWN"
    if outcome.kind == "EXIT" and outcome.exit_code != 0 and classified == "PASS":
        classified = "UNKNOWN"
    process_evidence: list[dict[str, Any]] = []
    if outcome.kind == "TIMEOUT" and classified == "UNKNOWN":
        process_evidence.append(_process_row("TIMEOUT", outcome, run_id, source, target[1], duration_sec))
    elif outcome.kind == "OS_ERROR":
        process_evidence.append(_process_row("OS_ERROR", outcome, run_id, source, target[1], duration_sec))
    elif post_execution_reason:
        process_evidence.append(_process_row("BASELINE_DRIFT", outcome, run_id, source, target[1], duration_sec))
    elif match_errors and not zero_report:
        process_evidence.append(_process_row("JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec))
    post_run_digests = _post_run_file_digests(compatibility)
    changed_file_ids = _changed_file_ids(compatibility, post_run_digests)
    if changed_file_ids:
        evidence = [row for row in evidence if row["file_id"] not in changed_file_ids]
        process_evidence.append(_process_row("SOURCE_CHANGED", outcome, run_id, source, target[1], duration_sec, changed_file_ids))
        classified = "UNKNOWN"
    zero_collect = (
        classified == "FAIL"
        and not evidence
        and zero_report
        and outcome.kind == "EXIT"
    )
    if zero_collect and not process_evidence:
        process_evidence.append(_process_row("NO_TESTS_COLLECTED", outcome, run_id, source, target[1], duration_sec))
    authoritative = (
        classified in {"PASS", "FAIL"}
        and (not match_errors or zero_collect)
        and not changed_file_ids
        and (
            len(evidence) == len(compatibility.required_pairs)
            or zero_collect
        )
    )
    if classified == "UNKNOWN" and not process_evidence:
        process_evidence.append(_process_row("JUNIT_MISSING" if report_bytes is None else "JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec))
    try:
        report_evidence, artifact_evidence = _durable_execution_artifacts(
            snapshots, scope[1], generated_delta_receipt, outcome, run_root, attempt_id,
        )
    except DurableNativeReportError:
        report_evidence = _report_digest_evidence(snapshots, scope[1])
        _unused, artifact_evidence = _durable_execution_artifacts(
            {}, scope[1], generated_delta_receipt, outcome, run_root, attempt_id,
        )
        classified, evidence, authoritative = "UNKNOWN", [], False
        process_evidence = [
            row for row in process_evidence if row.get("kind") == "SOURCE_CHANGED"
        ]
        process_evidence.append(
            _process_row("JUNIT_INVALID", outcome, run_id, source, target[1], duration_sec)
        )
    failed_methods = _failed_methods(snapshots, compatibility, evidence, java=language == "java") if classified == "FAIL" and authoritative else None
    return _report(classified, project, language, source, automation_digest, review_digest, (), run_id, evidence, process_evidence, authoritative, outcome.exit_code, target[0], request.executable, target[1], duration_sec, request.environment_labels, request=request, report_evidence=report_evidence, artifact_evidence=artifact_evidence, run_root=run_root, attempt_id=attempt_id, failed_methods=failed_methods)
