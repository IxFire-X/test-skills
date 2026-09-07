"""Fail-closed company execution request/result. No local fallback."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class CompanyRunnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CompanyExecutionError(CompanyRunnerError):
    """Company execution failure with the durable request/result identity reached so far."""

    def __init__(
        self,
        code: str,
        message: str,
        request: "CompanyRunRequest | None" = None,
        result: "CompanyRunResult | None" = None,
    ):
        super().__init__(code, message)
        self.request = request
        self.result = result


@dataclass(frozen=True)
class CompanyRunRequest:
    run_id: str
    source_digest: str
    input_snapshot_digest: str
    autotest_commit_digest: str
    automation_digest: str
    review_digest: str
    pairs: tuple[tuple[str, str], ...]
    runner_profile: str

    def digest(self) -> str:
        payload = {
            "run_id": self.run_id,
            "source_digest": self.source_digest,
            "input_snapshot_digest": self.input_snapshot_digest,
            "autotest_commit_digest": self.autotest_commit_digest,
            "automation_digest": self.automation_digest,
            "review_digest": self.review_digest,
            "pairs": [list(item) for item in self.pairs],
            "runner_profile": self.runner_profile,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class CompanyRunResult:
    run_id: str
    request_digest: str
    source_digest: str
    environment: str
    exit_code: int
    exit_cause: str
    duration_sec: float
    junit_digest: str | None
    job_id: str


def validate_company_result(request: CompanyRunRequest, result: CompanyRunResult) -> None:
    expected = request.digest()
    if result.run_id != request.run_id:
        raise CompanyRunnerError("stale_run", "company result run_id does not match request")
    if result.request_digest != expected:
        raise CompanyRunnerError("stale_request", "company result request digest does not match")
    if result.source_digest != request.source_digest:
        raise CompanyRunnerError("source_mismatch", "company result source digest does not match")
    if not result.job_id or not result.environment:
        raise CompanyRunnerError("incomplete_result", "company result is missing job or environment identity")
    if result.exit_cause in {"TIMEOUT", "OS_ERROR"}:
        if result.junit_digest is not None:
            raise CompanyRunnerError("invalid_result", "TIMEOUT/OS_ERROR must not carry a JUnit digest")
    elif not _digest_ok(result.junit_digest or ""):
        raise CompanyRunnerError("changed_result", "company result junit digest is missing or malformed")
    if result.exit_cause == "TIMEOUT" and result.exit_code != 124:
        raise CompanyRunnerError("invalid_result", "TIMEOUT must use exit code 124")
    if result.exit_cause == "OS_ERROR" and result.exit_code != 127:
        raise CompanyRunnerError("invalid_result", "OS_ERROR must use exit code 127")
    if result.exit_cause == "ZERO_EXIT" and result.exit_code != 0:
        raise CompanyRunnerError("invalid_result", "ZERO_EXIT requires a zero exit code")
    if result.exit_cause == "NONZERO_EXIT" and result.exit_code == 0:
        raise CompanyRunnerError("invalid_result", "NONZERO_EXIT requires a non-zero exit code")
    if result.exit_cause not in {"TIMEOUT", "OS_ERROR", "ZERO_EXIT", "NONZERO_EXIT"}:
        raise CompanyRunnerError("invalid_result", "company result exit cause is unknown")


def _digest_ok(value: str) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71 and all(
        char in "0123456789abcdef" for char in value[7:]
    )


def execution_report_digest(report: Mapping[str, Any]) -> str:
    """Return the receipt-bound identity of one complete execution report."""
    body = {key: value for key, value in report.items() if key != "company_execution_sha256"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_company_execution_receipt(
    receipt_bytes: bytes | None,
    run_result: Mapping[str, Any] | None,
    canonical_document: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
    autotest_review_artifact: Mapping[str, Any],
    *,
    expected_input_snapshot_digest: str | None = None,
    expected_autotest_commit_digest: str | None = None,
    expected_runner_profile: str | None = None,
) -> Mapping[str, Any] | None:
    """Validate the exact company receipt bytes bound to a company run report."""
    if run_result is not None and not isinstance(run_result, Mapping):
        raise CompanyRunnerError("company_receipt", "company run result is invalid")
    digest = None if run_result is None else run_result.get("company_execution_sha256")
    if digest is None:
        if receipt_bytes is not None:
            raise CompanyRunnerError("company_receipt", "company receipt is present without a bound run digest")
        return None
    if run_result is None or not _digest_ok(digest) or not isinstance(receipt_bytes, bytes):
        raise CompanyRunnerError("company_receipt", "company run requires valid receipt bytes and digest")
    if "company_execution_sha256" not in run_result or "run_id" not in run_result or "source" not in run_result:
        raise CompanyRunnerError("company_receipt", "company run identity is incomplete")
    if "sha256:" + hashlib.sha256(receipt_bytes).hexdigest() != digest:
        raise CompanyRunnerError("company_receipt", "company receipt bytes changed")
    from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

    try:
        receipt = loads_json_strict(receipt_bytes.decode("utf-8"))
    except (UnicodeError, StrictJsonError) as error:
        raise CompanyRunnerError("company_receipt", "company receipt is unreadable") from error

    schema = Path(__file__).resolve().parents[1] / "schemas" / "company-execution-receipt.schema.json"
    if not isinstance(receipt, dict) or schema_diagnostics(receipt, schema, schema.parents[1]):
        raise CompanyRunnerError("company_receipt", "company receipt is invalid")
    result = receipt.get("result")
    if receipt.get("status") != "completed" or not isinstance(result, Mapping):
        raise CompanyRunnerError("company_receipt", "company receipt is not a completed result")
    source = run_result.get("source")
    if not isinstance(source, Mapping) or not isinstance(run_result.get("run_id"), str):
        raise CompanyRunnerError("company_receipt", "company run identity is invalid")
    from tools.automation_validation import automation_sha256, required_symbol_pairs

    input_snapshot_digest = expected_input_snapshot_digest or receipt["input_snapshot_digest"]
    autotest_commit_digest = expected_autotest_commit_digest or receipt["autotest_commit_digest"]
    runner_profile = expected_runner_profile or receipt["runner_profile"]
    try:
        request = request_from_artifacts(
            run_result["run_id"], str(source["source_digest"]), automation_sha256(automation_artifact),
            automation_sha256(autotest_review_artifact), required_symbol_pairs(automation_artifact, dict(canonical_document)),
            input_snapshot_digest=input_snapshot_digest, autotest_commit_digest=autotest_commit_digest,
            runner_profile=runner_profile,
        )
        reconstructed_result = CompanyRunResult(**dict(result))
        validate_company_result(request, reconstructed_result)
    except (CompanyRunnerError, KeyError, TypeError, ValueError) as error:
        raise CompanyRunnerError("company_receipt", "company receipt does not match reconstructed request") from error
    target = run_result.get("target")
    stats = run_result.get("stats")
    if (
        receipt.get("input_snapshot_digest") != input_snapshot_digest
        or receipt.get("autotest_commit_digest") != autotest_commit_digest
        or receipt.get("source_digest") != request.source_digest
        or receipt.get("automation_digest") != request.automation_digest
        or receipt.get("review_digest") != request.review_digest
        or receipt.get("runner_profile") != runner_profile
        or receipt.get("request_digest") != request.digest()
        or receipt.get("execution_report_digest") != execution_report_digest(run_result)
        or not isinstance(target, Mapping)
        or target.get("runner") != runner_profile.split(":", 1)[0]
        or target.get("command") != runner_profile
        or run_result.get("exit_code") != reconstructed_result.exit_code
        or not isinstance(stats, Mapping)
        or stats.get("duration_sec") != reconstructed_result.duration_sec
    ):
        raise CompanyRunnerError("company_receipt", "company receipt does not match run result")
    return receipt


def _receipt_safe_result(result: CompanyRunResult | None) -> bool:
    """Whether the host can write this result into its strict JSON receipt."""
    return (
        isinstance(result, CompanyRunResult)
        and isinstance(result.run_id, str)
        and _digest_ok(result.request_digest)
        and _digest_ok(result.source_digest)
        and isinstance(result.environment, str)
        and isinstance(result.exit_code, int)
        and not isinstance(result.exit_code, bool)
        and isinstance(result.exit_cause, str)
        and isinstance(result.duration_sec, (int, float))
        and not isinstance(result.duration_sec, bool)
        and math.isfinite(result.duration_sec)
        and result.duration_sec >= 0
        and (result.junit_digest is None or isinstance(result.junit_digest, str))
        and isinstance(result.job_id, str)
    )


class CompanyRunner(Protocol):
    def submit(self, request: CompanyRunRequest) -> CompanyRunResult: ...
    def read_junit(self, result: CompanyRunResult) -> bytes: ...


class UnavailableCompanyRunner:
    def submit(self, request: CompanyRunRequest) -> CompanyRunResult:
        raise CompanyRunnerError("unavailable", "company runner is unavailable")

    def read_junit(self, result: CompanyRunResult) -> bytes:
        raise CompanyRunnerError("unavailable", "company runner is unavailable")


def resolve_company_runner() -> CompanyRunner:
    """Return the fail-closed placeholder until an approved live adapter exists."""
    return UnavailableCompanyRunner()


class ScriptedCompanyRunner:
    """Test double / staging fake. Never executes project tests locally."""

    def __init__(
        self,
        result: CompanyRunResult | None = None,
        error: CompanyRunnerError | None = None,
        junit_bytes: bytes | None = None,
    ):
        self.result = result
        self.error = error
        self.junit_bytes = junit_bytes
        self.requests: list[CompanyRunRequest] = []

    def submit(self, request: CompanyRunRequest) -> CompanyRunResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise CompanyRunnerError("unavailable", "company runner is unavailable")
        result = self.result
        if self.junit_bytes is not None:
            actual = "sha256:" + hashlib.sha256(self.junit_bytes).hexdigest()
            if actual != result.junit_digest:
                raise CompanyRunnerError("changed_result", "company result junit artifact bytes changed")
        validate_company_result(request, result)
        return result

    def read_junit(self, result: CompanyRunResult) -> bytes:
        if self.junit_bytes is None:
            raise CompanyRunnerError("changed_result", "company JUnit bytes are missing")
        return self.junit_bytes


def request_from_artifacts(
    run_id: str,
    source_digest: str,
    automation_digest: str,
    review_digest: str,
    pairs: Sequence[tuple[str, str]],
    *,
    input_snapshot_digest: str,
    autotest_commit_digest: str,
    runner_profile: str,
) -> CompanyRunRequest:
    return CompanyRunRequest(
        run_id=run_id,
        source_digest=source_digest,
        input_snapshot_digest=input_snapshot_digest,
        autotest_commit_digest=autotest_commit_digest,
        automation_digest=automation_digest,
        review_digest=review_digest,
        pairs=tuple(sorted(pairs)),
        runner_profile=runner_profile,
    )


def result_as_dict(result: CompanyRunResult) -> dict[str, Any]:
    return asdict(result)


def matching_result(request: CompanyRunRequest, **overrides: Any) -> CompanyRunResult:
    payload = {
        "run_id": request.run_id,
        "request_digest": request.digest(),
        "source_digest": request.source_digest,
        "environment": "company-staging:pytest-3.11",
        "exit_code": 0,
        "exit_cause": "ZERO_EXIT",
        "duration_sec": 0.2,
        "junit_digest": "sha256:" + "a" * 64,
        "job_id": "JOB-scripted",
    }
    payload.update(overrides)
    return CompanyRunResult(**payload)


def execute_company_details(
    project: Path,
    language: str,
    canonical_document: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
    autotest_review_artifact: Mapping[str, Any],
    input_snapshot_digest: str,
    autotest_commit_digest: str,
    *,
    runner_profile: str,
    runner: Any | None = None,
) -> tuple[dict[str, Any], CompanyRunRequest, CompanyRunResult]:
    """Submit accepted automation to the company runner. Never falls back to local pytest."""
    from tools.automation_validation import (
        automation_sha256,
        required_symbol_pairs,
        validate_accepted_autotest_review,
        validate_automation_artifact,
    )
    from tools.run_tests import (
        ProcessOutcome,
        _process_row,
        _report,
        _stats,
        match_junit_cases,
        parse_junit_bytes,
        validate_artifact_runner_compatibility,
        validate_execution_evidence,
    )

    semantic = validate_automation_artifact(dict(automation_artifact), dict(canonical_document))
    if semantic:
        raise CompanyRunnerError("review_not_accepted", semantic[0]["message"])
    rows = validate_accepted_autotest_review(
        autotest_review_artifact, automation_artifact, dict(canonical_document)
    )
    if rows:
        raise CompanyRunnerError("review_not_accepted", rows[0]["message"])
    artifacts = automation_artifact.get("artifacts") or {}
    source = artifacts.get("source") or {}
    if not isinstance(source, Mapping) or not source.get("source_digest"):
        raise CompanyRunnerError("review_not_accepted", "automation source digest is missing")
    try:
        pairs = tuple(sorted(required_symbol_pairs(automation_artifact, dict(canonical_document))))
    except Exception as error:
        raise CompanyRunnerError("review_not_accepted", str(error)[:400]) from error
    if not pairs:
        raise CompanyRunnerError("review_not_accepted", "accepted automation has no required symbol pairs")
    allowed_profiles = {
        "python": {"pytest:selected-symbols"},
        "java": {"maven:selected-symbols", "gradle:selected-symbols"},
    }
    if language not in allowed_profiles:
        raise CompanyRunnerError("invalid_result", "unsupported company runner language")
    if runner_profile not in allowed_profiles[language]:
        raise CompanyRunnerError("runner_profile_invalid", "runner profile does not match language")
    compatibility = validate_artifact_runner_compatibility(
        project, language, dict(canonical_document), dict(automation_artifact)
    )
    if compatibility.status != "READY":
        message = (
            compatibility.diagnostics[0]["message"]
            if compatibility.diagnostics
            else "generated files failed runner compatibility"
        )
        raise CompanyRunnerError("invalid_result", message)
    file_digests = dict(compatibility.verified_file_digests)
    pairs = compatibility.required_pairs
    run_id = "RUN-" + uuid.uuid4().hex
    request = request_from_artifacts(
        run_id,
        str(source["source_digest"]),
        automation_sha256(automation_artifact),
        automation_sha256(autotest_review_artifact),
        pairs,
        input_snapshot_digest=input_snapshot_digest,
        autotest_commit_digest=autotest_commit_digest,
        runner_profile=runner_profile,
    )
    result: CompanyRunResult | None = None
    try:
        active_runner = resolve_company_runner() if runner is None else runner
        submitted = active_runner.submit(request)
        if not isinstance(submitted, CompanyRunResult):
            raise CompanyRunnerError("invalid_result", "company runner returned an invalid result")
        result = submitted
        if not _receipt_safe_result(result):
            raise CompanyRunnerError("invalid_result", "company runner returned a malformed result")
        validate_company_result(request, result)
        evidence: list[dict[str, Any]] = []
        process_evidence: list[dict[str, Any]] = []
        if result.exit_cause == "TIMEOUT":
            process_evidence.append(
                _process_row("TIMEOUT", ProcessOutcome(124, "", "", "TIMEOUT"), run_id, source, runner_profile, result.duration_sec)
            )
        elif result.exit_cause == "OS_ERROR":
            process_evidence.append(
                _process_row("OS_ERROR", ProcessOutcome(127, "", "", "OS_ERROR"), run_id, source, runner_profile, result.duration_sec)
            )
        else:
            raw = active_runner.read_junit(result)
            if not isinstance(raw, bytes):
                raise CompanyRunnerError("invalid_result", "company runner returned an invalid JUnit payload")
            actual_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            if actual_digest != result.junit_digest:
                raise CompanyRunnerError("changed_result", "company result junit artifact bytes changed")
            parsed = parse_junit_bytes(raw)
            evidence, errors = match_junit_cases(
                parsed,
                compatibility,
                run_id,
                source,
                java=language == "java",
                strict=True,
            )
            if errors:
                raise CompanyRunnerError("invalid_result", errors[0])
        statuses = {(row["file_id"], row["symbol_id"]): row["status"] for row in evidence}
        stats = _stats(evidence, result.duration_sec)
        if process_evidence or result.exit_code != 0 or any(value in {"FAILED", "ERROR"} for value in statuses.values()):
            verdict = "FAIL"
        elif len(statuses) != len(pairs) or any(value == "SKIPPED" for value in statuses.values()):
            verdict = "NOT_RUNNABLE"
        else:
            verdict = "PASS"
        authoritative = not process_evidence and len(statuses) == len(pairs) and set(statuses) == set(pairs)
        target = {"runner": runner_profile.split(":", 1)[0], "command": runner_profile}
        diagnostics = validate_execution_evidence(
            verdict,
            run_id,
            source,
            evidence,
            authoritative,
            pairs,
            file_digests,
            result.exit_code,
            stats,
            None,
            process_evidence,
            target,
        )
        if diagnostics:
            raise CompanyRunnerError("invalid_result", diagnostics[0]["message"])
        report = _report(
            verdict,
            project,
            language,
            source,
            request.automation_digest,
            request.review_digest,
            (),
            run_id,
            evidence,
            process_evidence,
            authoritative,
            result.exit_code,
            target["runner"],
            None,
            runner_profile,
            result.duration_sec,
        )
        return report, request, result
    except CompanyRunnerError as error:
        safe_result = result if _receipt_safe_result(result) else None
        raise CompanyExecutionError(error.code, str(error), request, safe_result) from error
    except Exception as error:
        safe_result = result if _receipt_safe_result(result) else None
        raise CompanyExecutionError("runner_error", "company runner failed", request, safe_result) from error


def execute_company(
    project: Path,
    language: str,
    canonical_document: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
    autotest_review_artifact: Mapping[str, Any],
    input_snapshot_digest: str,
    autotest_commit_digest: str,
    *,
    runner_profile: str,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Compatibility surface returning the schema-valid generic run report."""
    report, _request, _result = execute_company_details(
        project,
        language,
        canonical_document,
        automation_artifact,
        autotest_review_artifact,
        input_snapshot_digest,
        autotest_commit_digest,
        runner_profile=runner_profile,
        runner=runner,
    )
    return report
