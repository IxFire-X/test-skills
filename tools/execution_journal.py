"""The journal of mode B: the checks and the publications of the receipts of the generation and run of automated tests (v0.8, the cut of the core).

A delivery of cases does not carry this module: the core reaches it only through ``execution_gate`` — ``_validate_factual_receipt``
of ``pilot_state`` takes the check of a kind of ``_MODE_B_KINDS`` from ``RECEIPT_CHECKS`` here; ``finalize_attempt`` and ``stage_steps``
take the publications with ``execution_gate.from_mode_b``, the files of mode B import them.  The bodies are the ones of
``pilot_state`` before the cut as they were (``handoff/v08/moves/r2.tsv``, ``r3.tsv``); what they read of the journal they take by
its public names.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from tools import execution_gate
from tools.confined_output import OutputConfinementError, create_confined_bytes_exclusive, read_confined_bytes
from tools.digests import json_digest
from tools.project_inventory import URL_CREDENTIALS_TEXT, token_signature_rule
from tools.schema_validation import schema_diagnostics
from tools.pilot_state import (  # noqa: F401  (a name per line of the journal this module reads)
    DIGEST as _DIGEST,
    PACK_ROOT as _PACK_ROOT,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    disposition_policy_at as _disposition_policy_at,
    model_lifecycle_projection_with_state as _model_lifecycle_projection_with_state,
    phase5_generated_delta as _phase5_generated_delta,
    read_artifact as _read_artifact,
    read_attempt_receipt_with_state as _read_attempt_receipt_with_state,
    safe_label as _safe_label,
    sealed as _sealed,
    self_review_accepted_at as _self_review_accepted_at,
    validate_schema as _validate_schema,
    append_journal_event as _append_event,
    credential_like as _credential_like,
    execution_inputs_frontier_closed as _execution_inputs_frontier_closed,
    receipt_target as _receipt_target,
    run_boundary as _run_boundary,
    closure_cached,
    derive_state,
    publish_bound,
    read_attempt_receipt,
    read_resume_validation_if_present,
    read_run,
    recover_execution_inputs_events,
    require_fresh_context_statement,
    canonical_digest as _digest,
    run_root_of as _run_root,
)
from tools.model_records import (  # noqa: F401  (a name per line of the journal this module reads)
    read_model_request_with_state as _read_model_request_with_state,
    read_model_stage_artifact_with_state as _read_model_stage_artifact_with_state,
)
from tools.effective_canonical import (  # noqa: F401  (a name per line of the journal this module reads)
    read_effective_canonical_with_state as _read_effective_canonical_with_state,
)
from tools.run_closure import (  # noqa: F401  (a name per line of the journal this module reads)
    read_closure_artifact_if_present,
)


def _validate_automation_model_lifecycle(
    run_root: Path,
    attempt_id: str,
    automation: Mapping[str, Any],
    review: Mapping[str, Any],
    boundary: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Require the exact automation generator/reviewer lifecycle and readbacks."""
    automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")

    try:
        final_revision = automation["artifacts"]["automation_revision"]
        lifecycle = _model_lifecycle_projection_with_state(state, attempt_id)["stages"]
        prior_seq = 0
        for revision in range(1, final_revision + 1):
            generator_stage = f"tc-to-autotest:r{revision}"
            reviewer_stage = f"autotest-reviewer:r{revision}"
            generator_events = lifecycle[generator_stage]
            reviewer_events = lifecycle[reviewer_stage]
            if set(generator_events) != {
                "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED",
            } or set(reviewer_events) != {
                "REVIEW_REQUESTED", "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED",
            }:
                raise ValueError
            generator_digest = str(generator_events["MODEL_RESPONSE_RECEIVED"]["artifact_digest"])
            reviewer_digest = str(reviewer_events["MODEL_RESPONSE_RECEIVED"]["artifact_digest"])
            project, root = _run_root(run_root)
            generator_request = _read_model_request_with_state(
                project, root, state, attempt_id, generator_stage,
                str(generator_events["MODEL_REQUESTED"]["artifact_digest"]),
            )
            generated = _read_model_stage_artifact_with_state(
                project, root, state, attempt_id, generator_stage, generator_digest,
            )["artifact"]
            reviewer_request = _read_model_request_with_state(
                project, root, state, attempt_id, reviewer_stage,
                str(reviewer_events["MODEL_REQUESTED"]["artifact_digest"]),
            )
            reviewed = _read_model_stage_artifact_with_state(
                project, root, state, attempt_id, reviewer_stage, reviewer_digest,
            )["artifact"]
            revision_boundary = boundary if revision == final_revision else _read_attempt_receipt_with_state(
                project, root, state, attempt_id,
                f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK",
            )["record"]
            boundary_events = [
                event for event in state["events"]
                if event.get("attempt_id") == attempt_id
                and event.get("artifact_digest") == revision_boundary.get("digest")
                and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
            ]
            ordered = [
                generator_events["MODEL_REQUESTED"],
                generator_events["MODEL_RESPONSE_RECEIVED"],
                generator_events["CANDIDATE_PUBLISHED"],
                *boundary_events,
                reviewer_events["REVIEW_REQUESTED"],
                reviewer_events["MODEL_REQUESTED"],
                reviewer_events["MODEL_RESPONSE_RECEIVED"],
            ]
            generated_digest = automation_sha256(generated)
            reviewed_digest = autotest_review_sha256(reviewed)
            source = generated["artifacts"]["source"]
            if (
                [event["event_type"] for event in boundary_events]
                != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
                or ordered[0]["seq"] <= prior_seq
                or [event["seq"] for event in ordered] != sorted(event["seq"] for event in ordered)
                or revision_boundary.get("automation_digest") != generated_digest
                or revision_boundary.get("automation_revision") != revision
                or revision_boundary.get("effective_canonical_digest") != source["source_digest"]
                or revision_boundary.get("effective_bundle_receipt_digest") != source["effective_bundle_receipt_digest"]
                or generator_digest != generated_digest
                or generator_events["CANDIDATE_PUBLISHED"].get("artifact_digest") != generated_digest
                or generator_request.get("invocation_id") != revision_boundary.get("generator_invocation_id")
                or generator_request.get("input_digests") != [
                    source["source_digest"], source["effective_bundle_receipt_digest"],
                ]
                or reviewer_events["REVIEW_REQUESTED"].get("artifact_digest") != generated_digest
                or reviewer_digest != reviewed_digest
                or reviewed["artifacts"]["autotest_review"].get("automation_sha256") != generated_digest
                or reviewer_request.get("invocation_id") != revision_boundary.get("reviewer_invocation_id")
                or reviewer_request.get("input_digests") != [generated_digest, revision_boundary.get("digest")]
                or revision == final_revision and (
                    generated != dict(automation) or reviewed != dict(review)
                )
            ):
                raise ValueError
            prior_seq = ordered[-1]["seq"]
        if final_revision not in {1, 2}:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("automation model lifecycle is absent or mismatched") from error


def _phase5_disposition_rows(delta: Mapping[str, Any]) -> list[dict[str, Any]]:
    files = delta.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("invalid generated delta receipt")
    rows: list[dict[str, Any]] = []
    for row in files:
        if not isinstance(row, Mapping):
            raise ValueError("invalid generated delta receipt")
        try:
            rows.append({key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")})
        except KeyError as error:
            raise ValueError("invalid generated delta receipt") from error
    return rows


def _phase5_delta_digest(delta: Mapping[str, Any]) -> str:
    body = {key: value for key, value in delta.items() if key != "digest"}
    return json_digest(body)


def _validate_generated_delta_ownership(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt: Mapping[str, Any],
    delta: Mapping[str, Any],
) -> None:
    try:
        ownership = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]), "materialization-ownership",
            "ARTIFACT_READ_BACK",
        )["record"]["payload"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid generated delta receipt") from error
    if not isinstance(ownership, Mapping) or delta.get("digest") != _phase5_delta_digest(delta) or any(delta.get(key) != ownership.get(key) for key in ("module", "test_root", "baseline_digest", "automation_digest", "review_digest", "effective_canonical_digest", "effective_bundle_receipt_digest")):
        raise ValueError("invalid generated delta receipt")
    files, ownership_files = delta.get("files"), ownership.get("files")
    if not isinstance(files, list) or not isinstance(ownership_files, list) or len(files) != len(ownership_files):
        raise ValueError("invalid generated delta receipt")
    file_ids: set[str] = set()
    folded_paths: set[str] = set()
    prefix = str(delta.get("test_root", "")).rstrip("/") + "/"
    for row, owned in zip(files, ownership_files):
        if not isinstance(row, Mapping) or not isinstance(owned, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
            raise ValueError("invalid generated delta receipt")
        path = row["path"].replace("\\", "/")
        expected_ownership = json_digest({"baseline": delta.get("baseline_digest"), "automation": delta.get("automation_digest"), "review": delta.get("review_digest"), "path": row["path"]})
        if row["file_id"] in file_ids or path.casefold() in folded_paths or not path.startswith(prefix) or Path(path).is_absolute() or ".." in Path(path).parts or row.get("ownership_digest") != expected_ownership or any(row.get(key) != owned.get(key) for key in ("file_id", "path", "content_digest", "baseline_absent", "ownership_digest")) or any(row.get(key) != delta.get(key) for key in ("baseline_digest", "automation_digest", "review_digest")):
            raise ValueError("invalid generated delta receipt")
        file_ids.add(row["file_id"])
        folded_paths.add(path.casefold())


def _execution_request_from_payload(payload: Mapping[str, Any]) -> Any | None:
    """Reconstruct only the closed, process-significant request recorded by V5."""
    (ExecutionRequest,) = execution_gate.from_mode_b("execution_adapters", "ExecutionRequest")

    execution = payload.get("execution")
    environment = payload.get("environment")
    if not isinstance(execution, Mapping) or not isinstance(environment, Mapping):
        raise ValueError("invalid execution receipt")
    request_digest_value = execution.get("request_digest")
    if request_digest_value is None:
        empty = {
            "adapter_id": None, "executable_path": None, "cwd": None, "build_profile": None,
            "typed_parameters": {}, "argv": [], "selectors": [], "timeout_seconds": None,
            "report_paths": [], "request_digest": None, "report_evidence": [],
            "artifact_evidence": [], "run_id": None,
            "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None,
        }
        if any(execution.get(key) != value for key, value in empty.items()):
            raise ValueError("invalid execution receipt")
        return None
    typed = execution.get("typed_parameters")
    labels = environment.get("safe_key_labels")
    if not isinstance(typed, Mapping) or not isinstance(labels, list):
        raise ValueError("invalid execution receipt")
    try:
        return ExecutionRequest(
            adapter_id=execution["adapter_id"],
            executable=execution["executable_path"],
            argv=tuple(execution["argv"]),
            cwd=execution["cwd"],
            selectors=tuple(execution["selectors"]),
            timeout_seconds=execution["timeout_seconds"],
            report_paths=tuple(execution["report_paths"]),
            environment_labels=tuple(labels),
            build_profile=execution["build_profile"],
            typed_parameters=tuple(sorted((str(key), str(value)) for key, value in typed.items())),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid execution receipt") from error


def _validate_durable_execution_artifacts(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    payload: Mapping[str, Any],
    delta: Mapping[str, Any],
) -> None:
    """Verify the run-scoped bytes already bound into the execution receipt."""
    (is_zero_test_report,) = execution_gate.from_mode_b("execution_adapters", "is_zero_test_report")
    DURABLE_NATIVE_REPORT_MAX_BYTES, DURABLE_NATIVE_REPORT_NORMALIZATION, DurableNativeReportError, _normalize_durable_junit_report = execution_gate.from_mode_b(
        "run_tests", "DURABLE_NATIVE_REPORT_MAX_BYTES", "DURABLE_NATIVE_REPORT_NORMALIZATION", "DurableNativeReportError", "_normalize_durable_junit_report",
    )

    execution = payload["execution"]
    rows = execution.get("artifact_evidence")
    if not isinstance(rows, list):
        raise ValueError("invalid execution receipt")
    if payload.get("run_id") is None:
        if rows:
            raise ValueError("invalid execution receipt")
        return
    attempt_id = str(attempt["attempt_id"])
    prefix = f"artifacts/{attempt_id}/"
    observed: list[tuple[Mapping[str, Any], bytes]] = []
    paths: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError("invalid execution receipt")
        relative = Path(row["path"])
        if (
            row["path"] in paths
            or "\\" in row["path"]
            or relative.is_absolute()
            or ".." in relative.parts
            or not row["path"].startswith(prefix)
        ):
            raise ValueError("invalid execution receipt")
        data = read_confined_bytes(project, root, root / relative)
        digest = "sha256:" + hashlib.sha256(data).hexdigest() if data is not None else None
        if digest != row.get("digest"):
            raise ValueError("invalid execution receipt")
        paths.add(row["path"])
        observed.append((row, data))
    reports = {
        (row.get("path"), row.get("digest"))
        for row in execution.get("report_evidence", ())
        if isinstance(row, Mapping)
    }
    native = {
        (row.get("source_path"), row.get("source_digest"))
        for row, _data in observed if row.get("kind") == "native_report"
    }
    expected_generated = {
        (row.get("path"), row.get("content_digest"))
        for row in delta.get("files", ())
        if isinstance(row, Mapping) and row.get("materialization") == "MATERIALIZED"
    }
    generated = {
        (row.get("source_path"), row.get("digest"))
        for row, _data in observed if row.get("kind") == "generated_test"
    }
    runner_outputs = [row for row, _data in observed if row.get("kind") == "runner_output"]
    for row, data in observed:
        kind = row.get("kind")
        if kind == "native_report":
            try:
                canonical = _normalize_durable_junit_report(data)
            except DurableNativeReportError as error:
                raise ValueError("invalid execution receipt") from error
            if (
                not row["path"].startswith(prefix + "reports/")
                or row.get("normalization") != DURABLE_NATIVE_REPORT_NORMALIZATION
                or len(data) > DURABLE_NATIVE_REPORT_MAX_BYTES
                or data != canonical
            ):
                raise ValueError("invalid execution receipt")
        if kind == "generated_test" and row.get("path") != prefix + "generated/" + str(row.get("source_path")):
            raise ValueError("invalid execution receipt")
        if kind == "runner_output":
            try:
                output = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("invalid execution receipt") from error
            if row["path"] != prefix + "runner-output.txt" or _canonical_runner_output(output) != data:
                raise ValueError("invalid execution receipt")
    if payload.get("verdict") in {"PASS", "FAIL"} and (
        not reports or native != reports or generated != expected_generated
    ):
        raise ValueError("invalid execution receipt")
    if payload.get("verdict") == "FAIL" and len(runner_outputs) != 1:
        raise ValueError("invalid execution receipt")
    zero_collection = any(
        isinstance(row, Mapping) and row.get("kind") == "NO_TESTS_COLLECTED"
        for row in payload.get("process_evidence", ())
    )
    if zero_collection and (
        payload.get("verdict") != "FAIL"
        or not native
        or not all(
            is_zero_test_report(data)
            for row, data in observed if row.get("kind") == "native_report"
        )
    ):
        raise ValueError("invalid execution receipt")


def _validate_closed_execution_request(
    project: Path,
    root: Path,
    attempt: Mapping[str, Any],
    payload: Mapping[str, Any],
    request: Any,
    delta: Mapping[str, Any],
) -> None:
    GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, SAFE_ENVIRONMENT_LABELS, command_for, request_digest = execution_gate.from_mode_b(
        "execution_adapters", "GRADLE", "MAVEN", "SYSTEM_MAVEN", "PYTEST", "SAFE_ENVIRONMENT_LABELS", "command_for", "request_digest",
    )
    from tools.project_inventory import module_runtime_path, read_execution_baseline, system_maven_path

    execution = payload["execution"]
    environment = payload["environment"]
    target = payload["target"]
    module = project if attempt["module"] == "." else project.joinpath(*str(attempt["module"]).split("/"))
    try:
        module = module.resolve()
        baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        runtime_relative = baseline["interpreter_path"] if request.adapter_id == PYTEST else baseline["executable_path"] if request.adapter_id == SYSTEM_MAVEN else baseline["wrapper_path"]
        expected_executable = system_maven_path(runtime_relative) if request.adapter_id == SYSTEM_MAVEN else module_runtime_path(module, runtime_relative)
        executable = system_maven_path(request.executable) if request.adapter_id == SYSTEM_MAVEN else module_runtime_path(module, Path(request.executable).relative_to(module).as_posix())
        cwd = Path(request.cwd).resolve()
        for report_path in request.report_paths:
            raw = Path(report_path)
            if raw.is_absolute() or ".." in raw.parts or "\x00" in report_path:
                raise ValueError("invalid execution receipt")
            (module / raw).resolve().relative_to(module)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("invalid execution receipt") from error
    if (
        request.adapter_id != baseline.get("adapter_id")
        or request.build_profile != baseline.get("build_profile")
        or dict(request.typed_parameters) != baseline.get("adapter_parameters")
        or executable != expected_executable
        or cwd != module
        or request.timeout_seconds != 600
        or not request.selectors
        or len(request.selectors) != len(set(request.selectors))
        or set(request.environment_labels) - SAFE_ENVIRONMENT_LABELS
        or tuple(request.environment_labels) != ("PROJECT_NATIVE_ENV",)
        or request_digest(request) != execution.get("request_digest")
        or execution.get("run_id") != attempt["run_id"]
        or execution.get("attempt_id") != attempt["attempt_id"]
        or execution.get("baseline_digest") != attempt["baseline_digest"]
        or execution.get("generated_delta_digest") != delta.get("digest")
        or environment.get("working_dir") != str(module)
        or environment.get("interpreter_path") != str(expected_executable)
        or environment.get("interpreter") != str(expected_executable)
        or environment.get("status") != "ready"
        or environment.get("missing") is not None
        or target.get("command") != request.adapter_id
    ):
        raise ValueError("invalid execution receipt")
    expected_reports, expected_argv = command_for(request.adapter_id, request.executable, request.build_profile, request.selectors)
    expected_target = {
        PYTEST: ("python", "pytest", "pytest"),
        MAVEN: ("java", "junit5", "maven"),
        SYSTEM_MAVEN: ("java", "junit5", "maven"),
        GRADLE: ("java", "junit5", "gradle"),
    }
    if request.report_paths != expected_reports or (
        target.get("language"), target.get("framework"), target.get("runner")
    ) != expected_target.get(request.adapter_id):
        raise ValueError("invalid execution receipt")
    if request.argv != expected_argv:
        raise ValueError("invalid execution receipt")
    for row in execution.get("report_evidence", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError("invalid execution receipt")
        evidence_path = Path(row["path"])
        if evidence_path.is_absolute() or ".." in evidence_path.parts or "\x00" in row["path"]:
            raise ValueError("invalid execution receipt")
        normalized = row["path"].replace("\\", "/")
        roots = tuple(value.rstrip("/") for value in request.report_paths)
        if not any(normalized == value or normalized.startswith(value + "/") for value in roots):
            raise ValueError("invalid execution receipt")
    _validate_durable_execution_artifacts(project, root, attempt, payload, delta)


def _check_automation_review_boundary(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    expected_keys = common | {
        "automation_digest", "automation_revision", "reviewer_session_id", "generator_invocation_id",
        "reviewer_invocation_id", "role_policy", "host_isolation", "effective_canonical_digest", "effective_bundle_receipt_digest",
    }
    isolation = receipt.get("host_isolation")
    isolation_body = {key: value for key, value in isolation.items() if key != "evidence_digest"} if isinstance(isolation, Mapping) else {}
    isolation_digest = json_digest(isolation_body)
    try:
        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
    except (KeyError, TypeError, ValueError):
        effective = None
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or not _DIGEST.fullmatch(str(receipt.get("automation_digest", "")))
        or type(receipt.get("automation_revision")) is not int
        or receipt.get("automation_revision") != int(kind[-1])
        or not all(_safe_label(receipt.get(key)) for key in ("reviewer_session_id", "generator_invocation_id", "reviewer_invocation_id"))
        or receipt.get("generator_invocation_id") == receipt.get("reviewer_invocation_id")
        or receipt.get("role_policy") != "autotest-static-reviewer-v1"
        or not _DIGEST.fullmatch(str(receipt.get("effective_canonical_digest", "")))
        or not _DIGEST.fullmatch(str(receipt.get("effective_bundle_receipt_digest", "")))
        or not isinstance(effective, Mapping)
        or receipt.get("effective_canonical_digest") != effective.get("document_digest")
        or receipt.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
        or not isinstance(isolation, Mapping)
        or set(isolation) != {"fresh_context", "distinct_invocations", "evidence_digest"}
        or not isinstance(isolation.get("fresh_context"), bool)
        or not isinstance(isolation.get("distinct_invocations"), bool)
        or not (isolation["fresh_context"] and isolation["distinct_invocations"] or _self_review_accepted_at(project, root, state))
        or not _DIGEST.fullmatch(str(isolation.get("evidence_digest", "")))
        or isolation.get("evidence_digest") != isolation_digest
    ):
        raise ValueError("invalid automation review boundary")


def _check_execution_inputs(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    automation_sha256, validate_accepted_autotest_review = execution_gate.from_mode_b(
        "automation_validation", "automation_sha256", "validate_accepted_autotest_review",
    )

    automation = receipt.get("automation_artifact")
    review = receipt.get("autotest_review")
    _validate_schema("execution-inputs-receipt.schema.json", receipt)
    if (
        set(receipt) != common | {"automation_artifact", "autotest_review"}
        or not identity_matches
        or not isinstance(automation, Mapping)
        or not isinstance(review, Mapping)
        or schema_diagnostics(dict(automation), _SCHEMA_ROOT / "tc-to-autotest-output.schema.json", _PACK_ROOT)
        or schema_diagnostics(dict(review), _SCHEMA_ROOT / "autotest-reviewer-output.schema.json", _PACK_ROOT)
    ):
        raise ValueError("invalid execution inputs receipt")
    try:
        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        revision = review["artifacts"]["autotest_review"]["automation_revision"]
        boundary = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid execution inputs receipt") from error
    _validate_automation_model_lifecycle(
        root, str(attempt["attempt_id"]), automation, review, boundary, state,
    )
    if (
        automation_sha256(automation) != review["artifacts"]["autotest_review"].get("automation_sha256")
        or automation.get("artifacts", {}).get("source", {}).get("source_digest") != effective.get("document_digest")
        or validate_accepted_autotest_review(
            review, automation, effective["document"], host_isolation_receipt=boundary,
            run_root=root, attempt_id=str(attempt["attempt_id"]),
        )
    ):
        raise ValueError("invalid execution inputs receipt")


def _check_generated_delta(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    delta = receipt.get("delta")

    if (
        set(receipt) != common | {"delta"}
        or not identity_matches
        or not isinstance(delta, Mapping)
        or schema_diagnostics(dict(delta), _SCHEMA_ROOT / "generated-delta.schema.json", _PACK_ROOT)
        or delta.get("baseline_digest") != attempt.get("baseline_digest")
        or delta.get("module") != attempt.get("module")
    ):
        raise ValueError("invalid generated delta receipt")
    try:
        automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b(
            "automation_validation", "automation_sha256", "autotest_review_sha256",
        )

        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        execution_inputs = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-inputs", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid generated delta receipt") from error
    if (
        delta.get("effective_canonical_digest") != effective.get("document_digest")
        or delta.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
        or automation_sha256(execution_inputs["automation_artifact"]) != delta.get("automation_digest")
        or autotest_review_sha256(execution_inputs["autotest_review"]) != delta.get("review_digest")
    ):
        raise ValueError("invalid generated delta receipt")
    _validate_generated_delta_ownership(project, root, state, attempt, delta)


def _check_execution_receipt(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    (request_digest,) = execution_gate.from_mode_b("execution_adapters", "request_digest")

    payload = receipt.get("payload")
    expected_keys = common | {
        "baseline_digest", "generated_delta_digest", "execution_request_digest", "payload",
    }
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or receipt.get("baseline_digest") != attempt.get("baseline_digest")
        or not isinstance(payload, Mapping)
        or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "run-tests-output.schema.json", _PACK_ROOT)
    ):
        raise ValueError("invalid execution receipt")
    try:
        delta = _phase5_generated_delta(project, root, state, attempt)
        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        source = payload["source"]
        execution = payload["execution"]
        document = effective["document"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid execution receipt") from error
    if (
        receipt.get("generated_delta_digest") != delta.get("digest")
        or receipt.get("execution_request_digest") != execution.get("request_digest")
        or payload.get("automation_sha256") != delta.get("automation_digest")
        or payload.get("autotest_review_sha256") != delta.get("review_digest")
        or source.get("document_id") != document.get("document_id")
        or source.get("revision") != document.get("revision")
        or source.get("source_digest") != effective.get("document_digest")
        or source.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
        or any(row.get("source_digest") != source.get("source_digest") for row in payload.get("execution_evidence", []))
    ):
        raise ValueError("invalid execution receipt")
    request = _execution_request_from_payload(payload)
    if isinstance(payload.get("execution_source"), Mapping):  # the command the human was given is the one the receipt names
        try:
            printed = _read_attempt_receipt_with_state(project, root, state, str(attempt["attempt_id"]), "materialization-command", "ARTIFACT_READ_BACK")["record"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid execution receipt") from error
        if (
            payload["execution_source"].get("human_command_digest") != printed.get("command_digest")
            or (payload.get("verdict") != "NOT_RUNNABLE" and printed.get("execution_request_digest") != execution.get("request_digest"))
        ):
            raise ValueError("invalid execution receipt")
    starts = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt["attempt_id"] and event.get("event_type") == "EXECUTION_STARTED"
    ]
    prestart = payload.get("verdict") == "NOT_RUNNABLE" and payload.get("run_id") is None
    if prestart:
        if starts:
            raise ValueError("invalid execution receipt")
        if request is not None:
            _validate_closed_execution_request(project, root, attempt, payload, request, delta)
    else:
        if payload.get("verdict") not in {"PASS", "FAIL", "UNKNOWN"} or request is None:
            raise ValueError("invalid execution receipt")
        _validate_closed_execution_request(project, root, attempt, payload, request, delta)
        if len(starts) != 1 or starts[0].get("artifact_digest") != request_digest(request):
            raise ValueError("invalid execution receipt")


def _check_materialization_command(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    # the command printed to the human who runs the build — and the facts a report is later held against
    _validate_schema("materialization-command-receipt.schema.json", receipt)
    try:
        delta = _phase5_generated_delta(project, root, state, attempt)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid materialization command receipt") from error
    materialized = {str(row.get("path")): row.get("content_digest") for row in delta.get("files", ()) if isinstance(row, Mapping) and row.get("materialization") == "MATERIALIZED"}
    if (
        not identity_matches
        or receipt.get("baseline_digest") != attempt.get("baseline_digest")
        or receipt.get("generated_delta_digest") != delta.get("digest")
        or receipt.get("command_digest") != _digest({"working_directory": receipt.get("working_directory"), "command": receipt.get("command")})
        or {row["path"]: row["content_digest"] for row in receipt.get("files", ())} != materialized
    ):
        raise ValueError("invalid materialization command receipt")


def _check_resume_validation(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    _validate_schema("resume-validation-receipt.schema.json", receipt)
    expected_keys = common | {
        "baseline_digest", "execution_request_digest", "execution_receipt_digest",
        "pre_finalization_trace_digest", "status", "reason_code",
    }
    try:
        execution_record = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        starts = [
            event for event in state["events"]
            if event.get("attempt_id") == attempt["attempt_id"]
            and event.get("event_type") == "EXECUTION_STARTED"
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid resume validation receipt") from error
    reason_code = receipt.get("reason_code")
    pretrace_digest = receipt.get("pre_finalization_trace_digest")
    pretrace_valid = pretrace_digest is None
    if isinstance(pretrace_digest, str):
        try:
            pretrace = _read_artifact(
                project,
                root,
                root / "closure" / str(attempt["attempt_id"]) / "pre_finalization_trace.json",
                "pre finalization trace",
            )
            pretrace_events = [
                event["event_type"]
                for event in state["events"]
                if event.get("attempt_id") == attempt["attempt_id"]
                and event.get("artifact_digest") == pretrace_digest
                and event.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
            ]
            pretrace_valid = (
                pretrace.get("digest") == pretrace_digest
                and pretrace.get("resume_validation_digest") is None
                and pretrace_events == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
            )
        except (KeyError, TypeError, ValueError, OSError):
            pretrace_valid = False
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or receipt.get("baseline_digest") != attempt.get("baseline_digest")
        or receipt.get("execution_request_digest") != execution_record.get("execution_request_digest")
        or receipt.get("execution_receipt_digest") != execution_record.get("digest")
        or not pretrace_valid
        or execution_record.get("payload", {}).get("verdict") == "NOT_RUNNABLE"
        or len(starts) != 1
        or starts[0].get("artifact_digest") != execution_record.get("execution_request_digest")
        or receipt.get("status") != "DRIFTED"
        or reason_code not in {"BASELINE_DRIFT", "BASELINE_INCOMPLETE", "SKILLSRC_DRIFT"}
    ):
        raise ValueError("invalid resume validation receipt")


def _check_execution_trace(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")
    TraceBuildError, build_trace, trace_sha256 = execution_gate.from_mode_b(
        "build_trace_document", "TraceBuildError", "build_trace", "trace_sha256",
    )

    payload = receipt.get("payload")
    automation = receipt.get("automation_artifact")
    review = receipt.get("autotest_review")
    expected_keys = common | {
        "effective_canonical_digest", "generated_delta_digest", "execution_receipt_digest",
        "automation_artifact", "autotest_review", "trace_sha256", "payload",
    }
    try:
        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        delta = _phase5_generated_delta(project, root, state, attempt)
        execution_record = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        report = execution_record["payload"]
        review_revision = review["artifacts"]["autotest_review"]["automation_revision"]
        review_boundary = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            f"automation-review-boundary-r{review_revision}",
            "ARTIFACT_READ_BACK",
        )["record"]
        expected_trace = build_trace(
            effective["document"], automation, review, report,
            host_isolation_receipt=review_boundary, run_root=root,
            attempt_id=str(attempt["attempt_id"]),
        )
    except (KeyError, TypeError, ValueError, TraceBuildError) as error:
        raise ValueError("invalid execution trace receipt") from error
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or not isinstance(automation, Mapping)
        or not isinstance(review, Mapping)
        or not isinstance(payload, Mapping)
        or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "trace-document.schema.json", _PACK_ROOT)
        or dict(payload) != expected_trace
        or receipt.get("trace_sha256") != trace_sha256(payload)
        or receipt.get("effective_canonical_digest") != effective.get("document_digest")
        or receipt.get("generated_delta_digest") != delta.get("digest")
        or receipt.get("execution_receipt_digest") != execution_record.get("digest")
        or automation_sha256(automation) != delta.get("automation_digest")
        or autotest_review_sha256(review) != delta.get("review_digest")
    ):
        raise ValueError("invalid execution trace receipt")


def _check_trace_audit(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    (check,) = execution_gate.from_mode_b("trace_check", "check")

    payload = receipt.get("payload")
    expected_keys = common | {"execution_trace_receipt_digest", "trace_sha256", "payload"}
    try:
        trace_record = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-trace", "ARTIFACT_READ_BACK",
        )["record"]
        execution_record = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        expected_audit = check(trace_record["payload"], require_execution=True, canonical=effective["document"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid trace audit receipt") from error
    if (
        set(receipt) != expected_keys
        or not identity_matches
        or not isinstance(payload, Mapping)
        or schema_diagnostics(dict(payload), _SCHEMA_ROOT / "trace-audit-output.schema.json", _PACK_ROOT)
        or dict(payload) != expected_audit
        or receipt.get("execution_trace_receipt_digest") != trace_record.get("digest")
        or receipt.get("trace_sha256") != trace_record.get("trace_sha256")
    ):
        raise ValueError("invalid trace audit receipt")


def _check_phase5_receipt(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:
    payload = receipt.get("payload")
    if set(receipt) != common | {"payload"} or not identity_matches or not isinstance(payload, Mapping):
        raise ValueError(f"invalid {kind} receipt")
    if kind == "materialization-ownership":
        _check_ownership(project, root, attempt, kind, receipt, state, payload, schema_diagnostics)
    else:
        _check_dispositions(project, root, attempt, kind, receipt, state, payload, schema_diagnostics)


def _check_ownership(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], payload: Mapping[str, Any], schema_diagnostics: Any) -> None:  # materialization-ownership: the files the package wrote, each with its ownership digest
    files = payload.get("files")
    test_root = payload.get("test_root")
    seen: set[tuple[str, str]] = set()
    invalid_file = not isinstance(files, list) or not isinstance(test_root, str)
    if not invalid_file:
        prefix = test_root.rstrip("/") + "/"
        for row in files:
            if not isinstance(row, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
                invalid_file = True
                break
            path = row["path"].replace("\\", "/")
            identity = (row["file_id"], path.casefold())
            expected_ownership = json_digest({"baseline": payload.get("baseline_digest"), "automation": payload.get("automation_digest"), "review": payload.get("review_digest"), "path": row["path"]})
            if identity in seen or not path.startswith(prefix) or Path(path).is_absolute() or ".." in Path(path).parts or row.get("ownership_digest") != expected_ownership:
                invalid_file = True
                break
            seen.add(identity)
    try:
        automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b(
            "automation_validation", "automation_sha256", "autotest_review_sha256",
        )

        effective = _read_effective_canonical_with_state(
            project, root, state, str(attempt["attempt_id"]),
        )
        execution_inputs = _read_attempt_receipt_with_state(
            project, root, state, str(attempt["attempt_id"]),
            "execution-inputs", "ARTIFACT_READ_BACK",
        )["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid materialization ownership receipt") from error
    if schema_diagnostics(dict(payload), _SCHEMA_ROOT / "materialization-receipt.schema.json", _PACK_ROOT) or payload.get("baseline_digest") != attempt.get("baseline_digest") or payload.get("module") != attempt.get("module") or payload.get("effective_canonical_digest") != effective.get("document_digest") or payload.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest") or automation_sha256(execution_inputs["automation_artifact"]) != payload.get("automation_digest") or autotest_review_sha256(execution_inputs["autotest_review"]) != payload.get("review_digest") or invalid_file:
        raise ValueError("invalid materialization ownership receipt")


def _check_dispositions(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], payload: Mapping[str, Any], schema_diagnostics: Any) -> None:  # disposition-plan, disposition-receipt: the plan of the dispositions and what was done
    files = payload.get("files")
    file_ids: set[str] = set()
    folded_paths: set[str] = set()
    invalid_file = not isinstance(files, list)
    if not invalid_file:
        for row in files:
            if not isinstance(row, Mapping) or not isinstance(row.get("file_id"), str) or not isinstance(row.get("path"), str):
                invalid_file = True
                break
            path = row["path"].replace("\\", "/")
            if row["file_id"] in file_ids or path.casefold() in folded_paths or Path(path).is_absolute() or ".." in Path(path).parts:
                invalid_file = True
                break
            file_ids.add(row["file_id"])
            folded_paths.add(path.casefold())
    expected_payload = _sealed({key: value for key, value in payload.items() if key != "digest"})
    if schema_diagnostics(dict(payload), _SCHEMA_ROOT / "disposition-receipt.schema.json", _PACK_ROOT) or invalid_file or expected_payload != payload:
        raise ValueError(f"invalid {kind} receipt")
    delta = _phase5_generated_delta(project, root, state, attempt)
    expected_rows = _phase5_disposition_rows(delta)
    verification = payload.get("verification")
    evidence = payload.get("execution_unknown_evidence_digest")
    if payload.get("generated_delta_digest") != delta.get("digest") or payload.get("files") is None or verification not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"}:
        raise ValueError(f"invalid {kind} receipt")
    if (verification == "UNKNOWN") != isinstance(evidence, str) or (isinstance(evidence, str) and not _DIGEST.fullmatch(evidence)):
        raise ValueError(f"invalid {kind} receipt")
    rows = payload["files"]
    GeneratedDeltaError, resolve_disposition_policy = execution_gate.from_mode_b(
        "generated_delta", "GeneratedDeltaError", "resolve_disposition_policy",
    )

    quarantine = verification == "FAIL" and _disposition_policy_at(project, root) == "quarantine"

    if kind == "disposition-plan":
        if payload.get("stage") != "disposition-plan" or len(rows) != len(expected_rows):
            raise ValueError("invalid disposition-plan receipt")
        for actual, expected in zip(rows, expected_rows):
            if not isinstance(actual, Mapping):
                raise ValueError("invalid disposition-plan receipt")
            try:
                operation, requested, _outcomes = resolve_disposition_policy(
                    str(verification),
                    str(expected.get("materialization")),
                    retain_pass=verification == "PASS" and actual.get("requested_disposition") == "RETAINED",
                    quarantine=quarantine,
                )
            except GeneratedDeltaError as error:
                raise ValueError("invalid disposition-plan receipt") from error
            relation = expected | {"operation": operation, "requested_disposition": requested}
            quarantine_keys = {key for key in ("quarantine", "quarantined_digest", "quarantine_unresolved") if key in actual}
            if (
                any(actual.get(key) != relation.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent", "operation", "requested_disposition"))
                or actual.get("pre_effect_state") not in ({"NOT_APPLICABLE"} if operation == "NO_FILE" else {"EXACT", "MISSING", "DRIFT", "UNSAFE"})
                # a quarantine row is either unresolved or names its methods and their marked digest.
                or operation != "QUARANTINE_IF_EXACT" and quarantine_keys
                or operation == "QUARANTINE_IF_EXACT" and quarantine_keys not in ({"quarantine_unresolved"}, {"quarantine", "quarantined_digest"})
                or operation == "QUARANTINE_IF_EXACT" and "quarantine" in actual and (actual["quarantine"] == []) != (actual["quarantined_digest"] is None)
            ):
                raise ValueError("invalid disposition-plan receipt")
    else:
        if payload.get("stage") != "dispositions":
            raise ValueError("invalid disposition-receipt receipt")
        try:
            plan = _read_attempt_receipt_with_state(
                project, root, state, str(attempt["attempt_id"]), "disposition-plan",
                "ARTIFACT_READ_BACK",
            )["record"]["payload"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid disposition-receipt receipt") from error
        if not isinstance(plan, Mapping) or payload.get("disposition_plan_digest") != plan.get("digest") or payload.get("generated_delta_digest") != plan.get("generated_delta_digest") or verification != plan.get("verification") or evidence != plan.get("execution_unknown_evidence_digest"):
            raise ValueError("invalid disposition-receipt receipt")
        plan_rows = plan.get("files")
        if not isinstance(plan_rows, list) or len(rows) != len(plan_rows) or [
            {key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")}
            for row in rows
        ] != expected_rows:
            raise ValueError("invalid disposition-receipt receipt")
        for final_row, plan_row in zip(rows, plan_rows):
            if not isinstance(plan_row, Mapping) or any(final_row.get(key) != plan_row.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")):
                raise ValueError("invalid disposition-receipt receipt")
            try:
                operation, requested, outcomes = resolve_disposition_policy(
                    str(verification), str(plan_row.get("materialization")),
                    retain_pass=verification == "PASS" and plan_row.get("requested_disposition") == "RETAINED",
                    quarantine=quarantine,
                )
            except GeneratedDeltaError as error:
                raise ValueError("invalid disposition-receipt receipt") from error
            if plan_row.get("operation") != operation or plan_row.get("requested_disposition") != requested:
                raise ValueError("invalid disposition-receipt receipt")
            legal = outcomes.get(str(plan_row.get("pre_effect_state")), frozenset())
            if (final_row.get("disposition"), final_row.get("reason_code")) not in legal:
                raise ValueError("invalid disposition-receipt receipt")
            if operation == "QUARANTINE_IF_EXACT" and not {
                "QUARANTINED": plan_row.get("quarantine") not in (None, [])
                and final_row.get("quarantine") == plan_row.get("quarantine")
                and final_row.get("quarantined_digest") == plan_row.get("quarantined_digest"),
                "RETAINED": plan_row.get("quarantine") == [],
                "CLEANED": plan_row.get("quarantine_unresolved") is True,
            }.get(str(final_row.get("disposition")), True):
                raise ValueError("invalid disposition-receipt receipt")


# a kind of mode B -> its check (``pilot_state._validate_factual_receipt`` through the gate)
RECEIPT_CHECKS = {
    "automation-review-boundary-r1": _check_automation_review_boundary,
    "automation-review-boundary-r2": _check_automation_review_boundary,
    "execution-inputs": _check_execution_inputs,
    "generated-delta": _check_generated_delta,
    "execution-receipt": _check_execution_receipt,
    "materialization-command": _check_materialization_command,
    "resume-validation": _check_resume_validation,
    "execution-trace": _check_execution_trace,
    "trace-audit": _check_trace_audit,
    "materialization-ownership": _check_phase5_receipt,
    "disposition-plan": _check_phase5_receipt,
    "disposition-receipt": _check_phase5_receipt,
}


# The publications of mode B: the files of mode B import them, the core takes them through ``execution_gate.from_mode_b``
def _canonical_runner_output(text: str, *, limit: int = 2000) -> bytes:
    """Return the only publishable UTF-8 runner-output representation."""
    if not isinstance(text, str) or not text:
        return b""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.splitlines(keepends=True):
        content = line[:-1] if line.endswith("\n") else line
        if token_signature_rule(content.encode("utf-8", errors="replace")) or _credential_like(content) or any(_credential_like(fragment) for fragment in content.split()) or URL_CREDENTIALS_TEXT.search(
            content,
        ):
            line = "<redacted>" + ("\n" if line.endswith("\n") else "")
        lines.append(line)
    encoded = "".join(lines).encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return encoded
    return encoded[-limit:].decode("utf-8", errors="ignore").encode("utf-8")


canonical_runner_output = _canonical_runner_output  # the public name for the other modules of the package


def claim_execution_start(run_root: Path, attempt_id: str, request_digest: str) -> Mapping[str, Any]:
    """Atomically reserve the one permitted project execution for an attempt."""
    project, root = _run_root(run_root)
    if not isinstance(request_digest, str) or not _DIGEST.fullmatch(request_digest):
        raise ValueError("invalid execution request digest")
    _manifest, authorization = _run_boundary(project, root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("invalid execution attempt")
    if attempt["policy_profile"] != "local-pilot-v1" or authorization.get("execution_requested") is not True:
        raise ValueError("execution authorization is invalid")
    if any(event["event_type"] == "EXECUTION_STARTED" and event.get("attempt_id") == attempt_id for event in state["events"]):
        raise ValueError("execution already started")
    return _append_event(
        project,
        root,
        "EXECUTION_STARTED",
        actor="controller",
        attempt_id=attempt_id,
        batch_id=None,
        artifact_digest=request_digest,
    )


def record_execution_unknown(run_root: Path, attempt_id: str, execution_receipt_digest: str) -> Mapping[str, Any]:
    """Bind the sole UNKNOWN marker to a read-back execution receipt and start."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or not _DIGEST.fullmatch(str(execution_receipt_digest)):
        raise ValueError("invalid execution-unknown attempt")
    try:
        receipt = read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("execution-unknown receipt is unavailable") from error
    starts = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_STARTED"
    ]
    unknowns = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id and event.get("event_type") == "EXECUTION_UNKNOWN"
    ]
    if (
        receipt.get("digest") != execution_receipt_digest
        or receipt.get("payload", {}).get("verdict") != "UNKNOWN"
        or len(starts) != 1
        or starts[0].get("artifact_digest") != receipt.get("execution_request_digest")
        or len(unknowns) > 1
        or unknowns and unknowns[0].get("artifact_digest") != execution_receipt_digest
    ):
        raise ValueError("execution-unknown evidence is invalid")
    return _append_event(
        project, root, "EXECUTION_UNKNOWN", actor="controller", attempt_id=attempt_id,
        batch_id=None, artifact_digest=execution_receipt_digest,
    )


def publish_execution_trace_pair(
    run_root: Path,
    attempt_id: str,
    automation_artifact: Mapping[str, Any],
    autotest_review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Build, persist, and read back the sole V5 execution trace and audit pair."""
    automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")
    TraceBuildError, build_trace, trace_sha256 = execution_gate.from_mode_b("build_trace_document", "TraceBuildError", "build_trace", "trace_sha256")
    (check,) = execution_gate.from_mode_b("trace_check", "check")

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None or attempt["state"] == "TERMINAL" or attempt.get("policy_profile") != "local-pilot-v1":
        raise ValueError("invalid execution trace attempt")
    try:
        existing_trace = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "execution-trace", "ARTIFACT_READ_BACK",
        )
        existing_audit = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "trace-audit", "ARTIFACT_READ_BACK",
        )
    except (KeyError, TypeError, ValueError):
        existing_trace = existing_audit = None
    if existing_trace is not None and existing_audit is not None:
        return {"trace": existing_trace, "audit": existing_audit}
    for later_kind in ("disposition-plan", "disposition-receipt"):
        try:
            later_bytes = read_confined_bytes(project, root, _receipt_target(root, attempt_id, later_kind))
        except OutputConfinementError as error:
            raise ValueError("unsafe execution trace lifecycle path") from error
        if later_bytes is not None:
            raise ValueError("execution trace must precede dispositions")
    try:
        effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
        delta = _phase5_generated_delta(project, root, state, attempt)
        execution_record = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
        )["record"]
        report = execution_record["payload"]
        if (
            automation_sha256(automation_artifact) != delta.get("automation_digest")
            or autotest_review_sha256(autotest_review) != delta.get("review_digest")
        ):
            raise ValueError("trace sources do not bind the generated delta")
        review_revision = autotest_review["artifacts"]["autotest_review"]["automation_revision"]
        review_boundary = _read_attempt_receipt_with_state(
            project, root, state, attempt_id, f"automation-review-boundary-r{review_revision}",
            "ARTIFACT_READ_BACK",
        )["record"]
        trace = build_trace(
            effective["document"], automation_artifact, autotest_review, report,
            host_isolation_receipt=review_boundary, run_root=root, attempt_id=attempt_id,
        )
    except (KeyError, TypeError, ValueError, TraceBuildError) as error:
        raise ValueError("authoritative execution trace cannot be built") from error
    trace_receipt = publish_bound(root, attempt_id, "execution-trace", {
        "effective_canonical_digest": effective["document_digest"],
        "generated_delta_digest": delta["digest"],
        "execution_receipt_digest": execution_record["digest"],
        "automation_artifact": dict(automation_artifact),
        "autotest_review": dict(autotest_review),
        "trace_sha256": trace_sha256(trace),
        "payload": trace,
    })
    audit = check(trace, require_execution=True, canonical=effective["document"])
    audit_receipt = publish_bound(root, attempt_id, "trace-audit", {
        "execution_trace_receipt_digest": trace_receipt["digest"],
        "trace_sha256": trace_receipt["record"]["trace_sha256"],
        "payload": audit,
    })
    return {"trace": trace_receipt, "audit": audit_receipt}


def publish_execution_inputs(
    run_root: Path,
    attempt_id: str,
    automation_artifact: Mapping[str, Any],
    autotest_review: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Persist exact reviewed execution/trace carriers before materialization."""
    expected = {
        "automation_artifact": dict(automation_artifact),
        "autotest_review": dict(autotest_review),
    }
    try:
        existing = read_attempt_receipt(
            run_root, attempt_id, "execution-inputs", "ARTIFACT_READ_BACK",
        )
    except (KeyError, TypeError, ValueError):
        existing = None
    if existing is not None:
        if any(existing["record"].get(key) != value for key, value in expected.items()):
            raise ValueError("conflicting execution inputs receipt")
        return {**existing, "created": False, "installed_identity": None}
    project, root = _run_root(run_root)
    try:
        installed = read_confined_bytes(
            project, root, _receipt_target(root, attempt_id, "execution-inputs"),
        )
    except OutputConfinementError as error:
        raise ValueError("unsafe execution-inputs receipt path") from error
    if installed is not None:
        recovered = recover_execution_inputs_events(root, attempt_id)
        if any(recovered["record"].get(key) != value for key, value in expected.items()):
            raise ValueError("conflicting execution inputs receipt")
        return {**recovered, "created": False, "installed_identity": None}
    if _execution_inputs_frontier_closed(run_root, attempt_id):
        raise ValueError("execution-inputs frontier is closed")
    return publish_bound(
        run_root,
        attempt_id,
        "execution-inputs",
        expected,
    )


def publish_resume_validation(
    run_root: Path,
    attempt_id: str,
    reason_code: str,
) -> Mapping[str, Any]:
    """Freeze one controller-detected drift before terminal finalization."""
    if reason_code not in {"BASELINE_DRIFT", "BASELINE_INCOMPLETE", "SKILLSRC_DRIFT"}:
        raise ValueError("invalid resume validation reason")
    _project, root = _run_root(run_root)
    # ponytail: one controller owns a run; add run-wide arbitration only if
    # concurrent CLI resume is ever made a supported product mode.
    existing = read_resume_validation_if_present(root, attempt_id)
    if existing is not None:
        if existing.get("reason_code") != reason_code:
            raise ValueError("conflicting resume validation reason")
        return existing
    if read_closure_artifact_if_present(
        root, attempt_id, "finalization_receipt",
    ) is not None:
        raise ValueError("finalization receipt already froze resume validation")
    pretrace = read_closure_artifact_if_present(
        root, attempt_id, "pre_finalization_trace",
    )
    execution = read_attempt_receipt(
        root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]
    return publish_bound(
        root,
        attempt_id,
        "resume-validation",
        {
            "baseline_digest": execution["baseline_digest"],
            "execution_request_digest": execution["execution_request_digest"],
            "execution_receipt_digest": execution["digest"],
            "pre_finalization_trace_digest": (
                pretrace.get("digest") if isinstance(pretrace, Mapping) else None
            ),
            "status": "DRIFTED",
            "reason_code": reason_code,
        },
    )


@closure_cached
def open_automation_review_boundary(run_root: Path, attempt_id: str, facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back the controller-owned isolation boundary for one V5 revision."""
    revision = facts.get("automation_revision")
    if type(revision) is not int or revision not in {1, 2}:
        raise ValueError("invalid automation review boundary revision")
    state = derive_state(_run_root(run_root)[1])
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    run = read_run(run_root)
    if attempt is None:
        raise ValueError("unknown automation review boundary attempt")
    if revision == 2:
        # Revision two is a single pre-execution correction.  Durable ownership
        # is the first materialization boundary, before a final delta can exist.
        if _receipt_target(_run_root(run_root)[1], attempt_id, "materialization-ownership").exists():  # the file decides, not its events: a cut materialize leaves it without them
            raise ValueError("automation revision two is forbidden after materialization")
        if attempt["policy_profile"] != "local-pilot-v1" or run["authorization"].get("execution_requested") is not True:
            raise ValueError("automation review boundary lacks local execution authorization")
    kind = f"automation-review-boundary-r{revision}"
    published = publish_bound(run_root, attempt_id, kind, facts)
    return {key: item for key, item in published.items() if key != "installed_identity"}


@closure_cached
def automation_boundary_facts(run_root: Path, attempt_id: str, revision: int, *, fresh_context: bool, reviewer_invocation_id: str) -> dict[str, Any]:
    """The facts of the boundary of the automation review for revision N from the journal; writes nothing.

    The automation digest is the CANDIDATE_PUBLISHED of ``tc-to-autotest:rN``, the generator invocation its
    MODEL_REQUESTED, the effective pair the read-back effective canonical. ``fresh_context`` and the reviewer
    invocation are the host's statement; the code computes only ``distinct_invocations`` and the digest."""
    if type(revision) is not int or revision not in {1, 2}:
        raise ValueError("AUTOMATION_REVISION_INVALID: the automation revision is 1 or 2")
    require_fresh_context_statement(run_root, fresh_context)
    project, root = _run_root(run_root)
    state = derive_state(root)
    stage = f"tc-to-autotest:r{revision}"
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage]
    requested = [event for event in events if event["event_type"] == "MODEL_REQUESTED"]
    published = [event for event in events if event["event_type"] == "CANDIDATE_PUBLISHED"]
    if len(requested) != 1 or len(published) != 1:
        raise ValueError(f"AUTOMATION_RESPONSE_MISSING: {stage} has no recorded answer yet: prepare and submit --stage {stage} first")
    generator = str(_read_model_request_with_state(project, root, state, attempt_id, stage, str(requested[0]["artifact_digest"]))["invocation_id"])
    effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
    reviewer = str(reviewer_invocation_id)
    isolation = {"fresh_context": fresh_context, "distinct_invocations": fresh_context and generator != reviewer}  # a session that is not fresh is no independent one, whatever its label
    isolation["evidence_digest"] = json_digest(isolation)
    return {
        "automation_digest": str(published[0]["artifact_digest"]), "automation_revision": revision,
        "reviewer_session_id": f"autotest-review-{attempt_id}-r{revision}", "generator_invocation_id": generator,
        "reviewer_invocation_id": reviewer, "role_policy": "autotest-static-reviewer-v1", "host_isolation": isolation,
        "effective_canonical_digest": effective["document_digest"], "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
    }


def publish_generated_delta(run_root: Path, attempt_id: str, delta: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bind one complete materialization receipt to the active attempt and read it back."""
    publish_bound(run_root, attempt_id, "generated-delta", {"delta": dict(delta)})
    return read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")


def publish_phase5_receipt(run_root: Path, attempt_id: str, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back one fixed-slot Phase 5 receipt before its project effect."""
    if kind not in {"materialization-ownership", "disposition-plan", "disposition-receipt"}:
        raise ValueError("invalid phase5 receipt kind")
    return publish_bound(run_root, attempt_id, kind, {"payload": dict(payload)})


def publish_run_artifact_bytes(run_root: Path, attempt_id: str, relative_name: str, data: bytes) -> Mapping[str, Any]:
    """Write/read back one run-scoped durable byte artifact under artifacts/<attempt>/."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("run artifact bytes are required")
    if not isinstance(relative_name, str) or not relative_name or "\x00" in relative_name:
        raise ValueError("invalid run artifact path")
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("invalid run artifact path")
    project, root = _run_root(run_root)
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("invalid run artifact attempt")
    target = root / "artifacts" / attempt_id / relative
    created = create_confined_bytes_exclusive(project, root, target, bytes(data))
    readback = read_confined_bytes(project, root, target)
    if readback != bytes(data):
        raise ValueError("run artifact read-back mismatch")
    digest = "sha256:" + hashlib.sha256(bytes(data)).hexdigest()
    return {
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "digest": digest,
        "created": bool(created),
    }


def execution_receipt_facts(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """The facts of an ``execution-receipt`` as the journal keeps them (``pilot_state.publish_attempt_receipt`` through the gate): the
    payload bound to the baseline of the attempt, its generated delta and the request of the execution."""
    if set(facts) != {"payload"} or not isinstance(facts.get("payload"), Mapping):
        raise ValueError("invalid execution receipt")
    try:
        delta = _phase5_generated_delta(project, root, state, attempt)
        execution = facts["payload"]["execution"]
        request_digest_value = execution["request_digest"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid execution receipt") from error
    return {
        "baseline_digest": attempt["baseline_digest"],
        "generated_delta_digest": delta["digest"],
        "execution_request_digest": request_digest_value,
        "payload": dict(facts["payload"]),
    }
