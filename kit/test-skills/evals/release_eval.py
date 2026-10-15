"""Aggregate immutable compatible-CLI evidence with adaptive 1 -> 3 -> 5 policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_POLICY = "adaptive-1-3-5-v1"
_SUITE_PATH = Path(__file__).resolve().parent / "scenarios" / "pilot-critical.json"
_MODEL_STAGES = (
    "context-marker",
    "tc-generator",
    "tc-reviewer",
    "tc-to-autotest",
    "autotest-reviewer",
)
_COMPATIBILITY_PROTOCOL_CODES = frozenset({
    "AUTOMATION_DURABLE_EVIDENCE_INVALID",
    "AUTOMATION_REVIEW_BINDING_INVALID",
    "BRANCH_EVIDENCE_INVALID",
    "BRANCH_INVALID",
    "DURABLE_RESUME_INVALID",
    "INVOCATION_REUSED",
    "MODEL_EVENT_EVIDENCE_INVALID",
    "REQUIRED_STAGE_NOT_EXECUTED",
    "REVIEWER_BINDING_INVALID",
    "REVIEWER_ISOLATION_INVALID",
    "STAGE_DECLARATION_INVALID",
    "STAGE_EVIDENCE_INVALID",
    "STAGE_MODEL_INVALID",
    "STAGE_READBACK_INVALID",
    "STAGE_REGISTRY_MISMATCH",
    "STAGE_TOOLS_INVALID",
    "STAGES_INVALID",
    "UNEXPECTED_STAGE_EXECUTION",
})
_REQUIRED_TUPLE_KEYS = {
    "skill_pack_version", "skill_pack_digest", "compatibility_contract_version",
    "execution_profile_version", "cli_host", "cli_runtime", "role_policy",
    "generator_model", "reviewer_model", "os", "language_runtime", "framework",
    "build_tool", "execution_adapter",
    "project_snapshot_digest",
    "module",
    "policy_profile",
}
_ESCALATION_SCOPE_KEYS = _REQUIRED_TUPLE_KEYS - {
    "skill_pack_version", "skill_pack_digest", "compatibility_contract_version",
    "project_snapshot_digest",
}
_OBSERVED_TUPLE_LABELS = {
    "cli_host", "cli_runtime", "generator_model", "reviewer_model", "os",
    "language_runtime",
}
_OBSERVATION_SCENARIOS = frozenset({
    "fresh-non-git",
    "dirty-git-preservation",
    "waiting-for-model-resume",
    "one-user-question-resume",
    "terminal-idempotent-retry",
    "retained-native-rerun",
})
class ReleaseEvalError(ValueError):
    """Campaign evidence is malformed, mixed, or incomplete."""


_EVALUATION_TOKEN = object()


class _ValidatedEvaluation(dict[str, Any]):
    """Detached evaluator output whose approval readback cannot be caller-replaced."""

    def __init__(self, receipt: Mapping[str, Any], *, token: object) -> None:
        if token is not _EVALUATION_TOKEN:
            raise TypeError("validated release evaluations are evaluator-owned")
        canonical = _canonical(receipt)
        self.__readback_bytes = canonical
        super().__init__(json.loads(canonical.decode("utf-8")))

    def _readback(self) -> dict[str, Any]:
        return json.loads(self.__readback_bytes.decode("utf-8"))


@dataclass(frozen=True)
class _ValidatedRun:
    """Facts derived from durable receipts; campaign JSON cannot construct this path."""

    campaign_id: str
    suite_id: str
    suite_digest: str
    scenario_id: str
    run_id: str
    attempt_id: str
    sequence: int
    kind: str
    tuple_value: Mapping[str, Any]
    tuple_complete: bool
    compatible: bool
    independence: str
    protocol_violations: tuple[str, ...]
    outcome: tuple[str | None, ...]
    scenario_passed: bool
    real_execution: bool
    reviewer_session_digest: str
    model_stage_models: Mapping[str, str | None]
    model_stage_invocations: tuple[str, ...]
    model_stage_evidence_complete: bool
    digest: str


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _escalation_scope(tuple_value: Mapping[str, Any]) -> bytes:
    return _canonical({key: tuple_value.get(key) for key in sorted(_ESCALATION_SCOPE_KEYS)})


def _is_escalation_predecessor(
    value: Mapping[str, Any], tuple_value: Mapping[str, Any], suite: Mapping[str, Any],
    *, campaign_id: str | None = None,
) -> bool:
    """Apply the one predecessor/scope rule used by evaluation and history."""
    body = {key: item for key, item in value.items() if key != "digest"}
    predecessor_tuple = value.get("tuple")
    return bool(
        value.get("digest") == _digest(body)
        and value.get("policy") == _POLICY
        and (campaign_id is None or value.get("campaign_id") != campaign_id)
        and isinstance(predecessor_tuple, Mapping)
        and _escalation_scope(predecessor_tuple) == _escalation_scope(tuple_value)
        and value.get("scenario_suite") == {"suite_id": suite.get("suite_id"), "digest": suite.get("digest")}
        and value.get("ready") is False
        and value.get("required_critical_runs") == suite.get("escalated_repetitions")
        and (value.get("instability_observed") is True or bool(value.get("protocol_violations")))
    )


def _load_scenario_suite(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseEvalError("release scenario suite is unreadable") from error
    required = {
        "schema_version", "suite_id", "policy", "smoke_scenario", "critical_scenarios", "scenario_contracts",
        "stable_repetitions", "escalated_repetitions", "escalate_on",
        "protocol_violation_blocks_readiness", "digest",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ReleaseEvalError("release scenario suite has a non-closed shape")
    suite = dict(value)
    body = {key: item for key, item in suite.items() if key != "digest"}
    scenarios = suite.get("critical_scenarios")
    contracts = suite.get("scenario_contracts")
    contract_keys = {"accepted", "verification", "reason_code", "check_id", "execution_required"}
    if (
        suite.get("schema_version") != "1.0.0"
        or suite.get("suite_id") != "pilot-critical-v1"
        or suite.get("policy") != _POLICY
        or suite.get("smoke_scenario") != "smoke"
        or not isinstance(scenarios, list)
        or not scenarios
        or any(not isinstance(item, str) or not _LABEL.fullmatch(item) for item in scenarios)
        or len(set(scenarios)) != len(scenarios)
        or not isinstance(contracts, Mapping)
        or set(contracts) != {"smoke", *scenarios}
        or any(
            not isinstance(contract, Mapping)
            or set(contract) != contract_keys
            or type(contract.get("accepted")) is not bool
            or contract.get("verification") not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"}
            or contract.get("reason_code") is not None and not _LABEL.fullmatch(str(contract.get("reason_code")))
            or not _LABEL.fullmatch(str(contract.get("check_id", "")))
            or type(contract.get("execution_required")) is not bool
            for contract in contracts.values()
        )
        or suite.get("stable_repetitions") != 3
        or suite.get("escalated_repetitions") != 5
        or suite.get("escalate_on") != ["instability", "protocol_violation"]
        or suite.get("protocol_violation_blocks_readiness") is not True
        or suite.get("digest") != _digest(body)
    ):
        raise ReleaseEvalError("release scenario suite is invalid")
    return suite


def _valid_tuple(value: Any, *, allow_unobserved: bool = False) -> bool:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_TUPLE_KEYS:
        return False
    if not all(_DIGEST.fullmatch(str(value.get(key, ""))) for key in ("project_snapshot_digest", "skill_pack_digest")):
        return False
    module = value.get("module")
    if module != "." and (not isinstance(module, str) or _LABEL.fullmatch(module) is None):
        return False
    labels = _REQUIRED_TUPLE_KEYS - {"project_snapshot_digest", "skill_pack_digest", "module"}
    return all(
        (
            allow_unobserved
            and key in _OBSERVED_TUPLE_LABELS
            and value.get(key) is None
        )
        or (
            isinstance(value.get(key), str)
            and _LABEL.fullmatch(value[key])
            and not value[key].startswith("unverified-")
        )
        for key in labels
    )


def _required_context_receipt_digests(
    scenario_id: str, reviewer_ledger: Mapping[str, Any],
) -> tuple[str, ...]:
    """Bind secret-exclusion proof to every exact C-lite source-byte receipt."""
    if scenario_id != "secret-exclusion":
        return ()
    package = reviewer_ledger.get("package_binding")
    digests = package.get("context_receipt_digests") if isinstance(package, Mapping) else None
    if not isinstance(digests, list) or not digests or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in digests):
        return ()
    provided = [
        event.get("provided_digest")
        for event in reviewer_ledger.get("events", ())
        if isinstance(event, Mapping) and event.get("event_type") == "EVIDENCE_PROVIDED"
    ]
    if any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in provided):
        return ()
    return tuple(dict.fromkeys([*digests, *provided]))


def _scenario_proof(scenario_id: str, evidence: Mapping[str, Any]) -> bool:
    """Classify only scenarios whose identity is proved by typed durable artifacts."""
    attempt = evidence.get("attempt")
    attempts = evidence.get("attempts")
    events = evidence.get("events")
    exclusions = evidence.get("exclusions")
    reviewer = evidence.get("reviewer")
    ledger = evidence.get("ledger")
    contexts = evidence.get("contexts", ())
    execution = evidence.get("execution")
    generated_delta = evidence.get("generated_delta")
    dispositions = evidence.get("dispositions")
    if (
        not isinstance(attempt, Mapping)
        or not isinstance(attempts, list)
        or not isinstance(events, list)
        or not isinstance(exclusions, list)
        or not isinstance(reviewer, Mapping)
        or not isinstance(ledger, Mapping)
    ):
        return False
    attempt_id = attempt.get("attempt_id")
    attempt_events = [
        row for row in events
        if isinstance(row, Mapping) and row.get("attempt_id") == attempt_id
    ]
    event_types = [row.get("event_type") for row in attempt_events]
    real_execution = evidence.get("real_execution") is True

    if scenario_id == "smoke":
        return True
    if scenario_id in _OBSERVATION_SCENARIOS:
        observation = evidence.get("scenario_observation")
        # The authoritative loader already verified the receipt schema, immutable
        # bytes, event order, read-back bindings, and scenario-specific semantics.
        return (
            isinstance(observation, Mapping)
            and observation.get("scenario_id") == scenario_id
            and evidence.get("scenario_observation_verified") is True
        )
    if scenario_id == "nested-module":
        module = attempt.get("module")
        baseline = evidence.get("baseline")
        execution_payload = execution.get("payload") if isinstance(execution, Mapping) else None
        execution_facts = execution_payload.get("execution") if isinstance(execution_payload, Mapping) else None
        project = evidence.get("project")
        if (
            not isinstance(module, str)
            or module == "."
            or not isinstance(baseline, Mapping)
            or baseline.get("module") != module
            or not isinstance(execution_facts, Mapping)
            or not isinstance(execution_facts.get("cwd"), str)
            or not isinstance(project, (str, Path))
        ):
            return False
        return (
            Path(execution_facts["cwd"]).resolve()
            == (Path(project).resolve() / module).resolve()
            and real_execution
        )
    if scenario_id == "secret-exclusion":
        excluded_paths = {
            row.get("project_path") for row in exclusions
            if isinstance(row, Mapping) and row.get("reason_code") == "SECRET_SUSPECTED"
        }
        context_paths = {
            row.get("project_path")
            for receipt in contexts if isinstance(receipt, Mapping)
            for row in receipt.get("files", ()) if isinstance(row, Mapping)
        }
        return bool(excluded_paths) and bool(contexts) and excluded_paths.isdisjoint(context_paths) and evidence.get("model_stage_evidence_complete") is True
    if scenario_id == "reviewer-rejection":
        return (
            reviewer.get("session_complete") is True
            and reviewer.get("authoritative_verdict") == "REJECTED"
            and ledger.get("status") == "COMPLETED"
            and "EXECUTION_STARTED" not in event_types
        )
    if scenario_id == "review-context-limit":
        return (
            reviewer.get("pre_verdict_abort") is True
            and reviewer.get("abort_reason") == "REVIEW_CONTEXT_LIMIT"
            and ledger.get("status") == "ABORTED"
            and "EXECUTION_STARTED" not in event_types
        )
    if scenario_id == "materialization-failure":
        facts = generated_delta.get("facts") if isinstance(generated_delta, Mapping) else None
        files = generated_delta.get("files") if isinstance(generated_delta, Mapping) else None
        disposition_files = dispositions.get("files") if isinstance(dispositions, Mapping) else None
        return (
            isinstance(facts, Mapping)
            and facts.get("reason_code") == "MATERIALIZATION_INCOMPLETE"
            and isinstance(files, list)
            and any(isinstance(row, Mapping) and row.get("materialization") == "NOT_MATERIALIZED" for row in files)
            and isinstance(disposition_files, list)
            and {row.get("disposition") for row in disposition_files if isinstance(row, Mapping)} <= {"CLEANED", "NOT_MATERIALIZED"}
            and any(isinstance(row, Mapping) and row.get("disposition") == "NOT_MATERIALIZED" for row in disposition_files)
            and dispositions.get("verification") == "NOT_APPLICABLE"
            and "EXECUTION_STARTED" not in event_types
        )
    if scenario_id == "execution-interruption-unknown":
        payload = execution.get("payload") if isinstance(execution, Mapping) else None
        disposition_files = dispositions.get("files") if isinstance(dispositions, Mapping) else None
        allowed = {"PRESERVED_EXECUTION_UNKNOWN", "PRESERVED_CONTENT_CONFLICT"}
        return (
            isinstance(payload, Mapping)
            and payload.get("verdict") == "UNKNOWN"
            and "EXECUTION_STARTED" in event_types
            and "EXECUTION_UNKNOWN" in event_types
            and isinstance(disposition_files, list)
            and bool(disposition_files)
            and all(isinstance(row, Mapping) and row.get("disposition") in allowed for row in disposition_files)
            and dispositions.get("verification") == "UNKNOWN"
        )
    if scenario_id == "child-execution-retry":
        parent_id = attempt.get("parent_attempt_id")
        parent = next(
            (
                row for row in attempts
                if isinstance(row, Mapping) and row.get("attempt_id") == parent_id
            ),
            None,
        )
        parent_types = {
            row.get("event_type")
            for row in events
            if isinstance(row, Mapping) and row.get("attempt_id") == parent_id
        }
        return (
            isinstance(parent, Mapping)
            and isinstance(attempt.get("retry_reason"), str)
            and {"EXECUTION_UNKNOWN", "PROCESS_STOPPED", "ATTEMPT_TERMINAL"} <= parent_types
            and real_execution
        )

    # These identities need a controller/eval observation receipt that the pilot does
    # not currently publish. Labels, current filesystem state, and no-op absence are
    # not durable proof of the precondition/action that the scenario names.
    return False


def _load_ref(base: Path, reference: Any) -> dict[str, Any]:
    from tools.schema_validation import StrictJsonError, load_json_strict

    if not isinstance(reference, Mapping) or set(reference) != {"path", "digest"} or not isinstance(reference.get("path"), str) or not _DIGEST.fullmatch(str(reference.get("digest", ""))):
        raise ReleaseEvalError("release evidence reference is invalid")
    relative = Path(reference["path"])
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".json":
        raise ReleaseEvalError("release evidence path escapes campaign directory")
    root = base.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ReleaseEvalError("release evidence path escapes campaign directory")
    try:
        value = load_json_strict(path)
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ReleaseEvalError("release evidence is unreadable") from error
    if not isinstance(value, Mapping) or value.get("digest") != reference["digest"] or _digest({key: item for key, item in value.items() if key != "digest"}) != reference["digest"]:
        raise ReleaseEvalError("release evidence digest is invalid")
    return dict(value)


def _real_execution(project: Path, state: Mapping[str, Any], attempt_id: str, receipt: Mapping[str, Any] | None) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    payload = receipt.get("payload")
    execution = payload.get("execution") if isinstance(payload, Mapping) else None
    if (
        not isinstance(execution, Mapping)
        or execution.get("attempt_id") != attempt_id
        or not _LABEL.fullmatch(str(execution.get("adapter_id", "")))
        or not _DIGEST.fullmatch(str(execution.get("request_digest", "")))
        or not any(event.get("event_type") == "EXECUTION_STARTED" and event.get("attempt_id") == attempt_id and event.get("artifact_digest") == execution["request_digest"] for event in state.get("events", []))
    ):
        return False
    artifact_evidence = execution.get("artifact_evidence")
    if payload.get("verdict") in {"PASS", "FAIL"}:
        return isinstance(artifact_evidence, list) and any(
            isinstance(row, Mapping) and row.get("kind") == "native_report"
            for row in artifact_evidence
        )
    process_evidence = payload.get("process_evidence")
    return payload.get("verdict") == "UNKNOWN" and isinstance(process_evidence, list) and bool(process_evidence)


def _derive_record(
    record: Mapping[str, Any],
    *,
    campaign_dir: Path,
    project: Path,
    manifest: Mapping[str, Any],
    suite: Mapping[str, Any],
) -> _ValidatedRun:
    """Derive one campaign fact row from the immutable Phase 1-7 artifact chain."""
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.pilot_state import (
        derive_state,
        read_attempt_receipt,
        read_closure_artifact_if_present,
        read_context_selection,
        read_reviewer_session_ledger,
        read_run,
        read_scenario_observation,
        read_terminal_result,
        terminal_reviewer_evidence,
    )
    from tools.project_inventory import (
        build_exclusion_receipt,
        read_exclusion_receipt,
        read_execution_baseline,
        read_inventory_receipt,
    )
    from tools.schema_validation import schema_diagnostics

    value = dict(record) if isinstance(record, Mapping) else {}
    diagnostics = schema_diagnostics(value, Path(__file__).resolve().parents[1] / "schemas" / "release-eval-run.schema.json", Path(__file__).resolve().parents[1])
    if diagnostics or value.get("digest") != _digest({key: item for key, item in value.items() if key != "digest"}):
        raise ReleaseEvalError("release run record is invalid")
    if value.get("suite_id") != suite.get("suite_id") or value.get("suite_digest") != suite.get("digest"):
        raise ReleaseEvalError("release run is bound to another scenario suite")
    expectation = suite.get("scenario_contracts", {}).get(str(value.get("scenario_id")))
    if expectation is None or (value.get("kind") == "smoke") != (value.get("scenario_id") == "smoke"):
        raise ReleaseEvalError("release run scenario is invalid")

    project = project.resolve()
    run_root = project / ".pilot-runs" / str(value["run_id"])
    run = read_run(run_root)
    state = derive_state(run_root)
    attempt = next((item for item in state["attempts"] if item.get("attempt_id") == value["attempt_id"]), None)
    if run["manifest"].get("run_id") != value["run_id"] or run["manifest"].get("project") != str(project) or not isinstance(attempt, Mapping):
        raise ReleaseEvalError("release run identity is invalid")

    compatibility = _load_ref(campaign_dir, value["compatibility_evidence"])
    compatibility_result = validate_compatibility_evidence(
        compatibility, manifest, run_root=run_root,
    )
    compatibility_errors = {
        str(code) for code in compatibility_result.get("errors", ())
        if isinstance(code, str)
    }
    non_protocol_errors = compatibility_errors - _COMPATIBILITY_PROTOCOL_CODES
    if (
        non_protocol_errors
        or compatibility.get("profile") != attempt.get("policy_profile")
        or compatibility.get("resume", {}).get("attempt_id") != value["attempt_id"]
    ):
        raise ReleaseEvalError("release run compatibility evidence is invalid")

    terminal = read_terminal_result(run_root, str(value["attempt_id"]))
    finalization = read_closure_artifact_if_present(run_root, str(value["attempt_id"]), "finalization_receipt")
    if not isinstance(finalization, Mapping):
        raise ReleaseEvalError("release run finalization evidence is unavailable")
    reviewer = terminal_reviewer_evidence(run_root, str(value["attempt_id"]))
    ledger = read_reviewer_session_ledger(run_root, str(value["attempt_id"]))
    expected_model_stages = tuple(
        row.get("stage")
        for row in manifest.get("stage_registry", ())
        if isinstance(row, Mapping) and row.get("role") != "controller"
    )
    if set(expected_model_stages) != set(_MODEL_STAGES):
        raise ReleaseEvalError("release run model stage registry is invalid")
    # Model identity is observable only to the compatible host. The controller
    # accepts it only in the same digest-bound stage row whose exact I/O and
    # durable artifact readback were verified by the compatibility contract.
    model_stage_models: dict[str, str | None] = {}
    model_stage_invocations_list: list[str] = []
    model_stage_evidence_complete = True
    for stage in _MODEL_STAGES:
        rows = [row for row in compatibility["stages"] if row.get("stage") == stage]
        if stage == "tc-generator":
            executed = [row for row in rows if row.get("status") == "EXECUTED"]
            models = {row.get("model_id") for row in executed}
            complete = (
                bool(executed)
                and len(executed) == len(rows)
                and len(models) == 1
                and all(
                    isinstance(row.get("model_id"), str) and _LABEL.fullmatch(row["model_id"]) is not None
                    and isinstance(row.get("invocation_id"), str) and _LABEL.fullmatch(row["invocation_id"]) is not None
                    and isinstance(row.get("input_digests"), list) and bool(row["input_digests"])
                    and isinstance(row.get("output_digests"), list) and bool(row["output_digests"])
                    for row in executed
                )
            )
            model_stage_models[stage] = next(iter(models)) if complete else None
            model_stage_evidence_complete = model_stage_evidence_complete and complete
            if complete:
                model_stage_invocations_list.extend(str(row["invocation_id"]) for row in executed)
            continue
        if len(rows) != 1:
            model_stage_models[stage] = None
            model_stage_evidence_complete = False
            continue
        row = rows[0]
        if row.get("status") == "NOT_APPLICABLE":
            model_stage_models[stage] = None
            continue
        model_id, invocation_id = row.get("model_id"), row.get("invocation_id")
        inputs, outputs = row.get("input_digests"), row.get("output_digests")
        complete = (
            isinstance(model_id, str) and _LABEL.fullmatch(model_id) is not None
            and isinstance(invocation_id, str) and _LABEL.fullmatch(invocation_id) is not None
            and isinstance(inputs, list) and bool(inputs)
            and isinstance(outputs, list) and bool(outputs)
        )
        model_stage_models[stage] = model_id if complete else None
        model_stage_evidence_complete = model_stage_evidence_complete and complete
        if complete:
            model_stage_invocations_list.append(invocation_id)
    model_stage_invocations = tuple(model_stage_invocations_list)
    tc_generator_rows = [row for row in compatibility["stages"] if row.get("stage") == "tc-generator" and row.get("status") == "EXECUTED"]
    tc_reviewer_rows = [row for row in compatibility["stages"] if row.get("stage") == "tc-reviewer" and row.get("status") == "EXECUTED"]
    reviewer_binding_invalid = (
        len(tc_reviewer_rows) != 1
        or tc_reviewer_rows[0].get("invocation_id") != ledger.get("reviewer_invocation_id")
        or ledger.get("generator_invocation_id") not in {row.get("invocation_id") for row in tc_generator_rows}
    )
    if reviewer_binding_invalid:
        compatibility_errors.add("REVIEWER_BINDING_INVALID")

    execution_receipt: Mapping[str, Any] | None
    try:
        execution_receipt = read_attempt_receipt(run_root, str(value["attempt_id"]), "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, ValueError):
        execution_receipt = None
    execution_payload = execution_receipt.get("payload") if isinstance(execution_receipt, Mapping) else None
    execution = execution_payload.get("execution") if isinstance(execution_payload, Mapping) else None
    baseline_path = run_root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
    baseline = read_execution_baseline(baseline_path)
    inventory_path = run_root / "inventories" / (
        str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"
    )
    inventory = read_inventory_receipt(inventory_path)
    expected_exclusions = build_exclusion_receipt(inventory)
    exclusion_path = run_root / "exclusions" / (
        str(expected_exclusions["digest"]).removeprefix("sha256:") + ".json"
    )
    exclusions = read_exclusion_receipt(exclusion_path)
    if exclusions != expected_exclusions:
        raise ReleaseEvalError("release run exclusion evidence is invalid")
    context_digests = _required_context_receipt_digests(str(value["scenario_id"]), ledger)
    contexts = tuple(
        read_context_selection(run_root, str(value["attempt_id"]), digest)
        for digest in context_digests
    )

    optional_receipts: dict[str, Mapping[str, Any] | None] = {}
    for kind in ("generated-delta", "disposition-receipt"):
        try:
            optional_receipts[kind] = read_attempt_receipt(
                run_root, str(value["attempt_id"]), kind, "ARTIFACT_READ_BACK",
            )["record"]
        except (KeyError, ValueError):
            optional_receipts[kind] = None
    generated_delta_receipt = optional_receipts["generated-delta"]
    disposition_receipt = optional_receipts["disposition-receipt"]
    generated_delta = (
        generated_delta_receipt.get("delta")
        if isinstance(generated_delta_receipt, Mapping)
        else None
    )
    dispositions = (
        disposition_receipt.get("payload")
        if isinstance(disposition_receipt, Mapping)
        else None
    )
    real_execution = _real_execution(
        project, state, str(value["attempt_id"]), execution_receipt,
    )
    adapter_id = execution.get("adapter_id") if isinstance(execution, Mapping) else None
    adapter_id = adapter_id or baseline.get("adapter_id") or "not-applicable"
    adapter_parts = str(adapter_id).split(":", 1)
    framework = execution_payload.get("target", {}).get("framework") if isinstance(execution_payload, Mapping) else None
    build_tool = execution_payload.get("target", {}).get("runner") if isinstance(execution_payload, Mapping) else None
    if framework is None:
        framework = "pytest" if adapter_parts[0] == "pytest" else "junit5" if adapter_parts[0] in {"maven-wrapper", "gradle-wrapper"} else "not-applicable"
    if build_tool is None:
        build_tool = "pytest" if adapter_parts[0] == "pytest" else "maven" if adapter_parts[0] == "maven-wrapper" else "gradle" if adapter_parts[0] == "gradle-wrapper" else "not-applicable"

    checks = value["scenario_checks"]
    check_ids = {row["check_id"] for row in checks}
    evidence_digests = {digest for row in checks for digest in row["evidence_digests"]}
    required_digests = {terminal["digest"], finalization["digest"]}
    missing_required_evidence = False
    if expectation["execution_required"]:
        if execution_receipt is None:
            missing_required_evidence = True
        else:
            required_digests.add(execution_receipt["digest"])

    scenario_id = str(value["scenario_id"])
    observation: Mapping[str, Any] | None = None
    observation_events: list[Mapping[str, Any]] = []
    observation_binding_event: Mapping[str, Any] | None = None
    observation_verified = False
    if scenario_id in _OBSERVATION_SCENARIOS:
        try:
            loaded_observation = read_scenario_observation(
                run_root,
                str(value["attempt_id"]),
                scenario_id,
                str(value["scenario_observation_receipt_digest"]),
            )
            observation = loaded_observation["record"]
            observation_events = list(loaded_observation["events"])
            observation_binding_event = loaded_observation.get("binding_event")
            observation_verified = loaded_observation.get("verified") is True
        except (KeyError, TypeError, ValueError) as error:
            raise ReleaseEvalError("release run scenario observation is invalid") from error
    attempt_events = [
        row for row in state["events"]
        if row.get("attempt_id") == value["attempt_id"]
    ]
    lifecycle_events = [
        row for row in attempt_events
        if row.get("event_type") in {
            "MODEL_REQUESTED", "MODEL_RESPONSE_RECEIVED",
            "CANDIDATE_PUBLISHED", "REVIEW_REQUESTED",
        }
    ]
    snapshot_events = [
        row for row in state["events"]
        if row.get("event_type") == "SNAPSHOT_BOUND"
        and row.get("artifact_digest") == attempt["baseline_digest"]
    ]
    if len(snapshot_events) != 1 or not lifecycle_events:
        missing_required_evidence = True
    else:
        required_digests.add(snapshot_events[0]["digest"])
        required_digests.update(row["digest"] for row in lifecycle_events)
    if scenario_id in {"fresh-non-git", "dirty-git-preservation", "secret-exclusion"}:
        required_digests.add(inventory["digest"])
    if scenario_id == "secret-exclusion":
        required_digests.update(receipt["digest"] for receipt in contexts)
    if scenario_id == "nested-module":
        required_digests.update({attempt["digest"], baseline["digest"]})
    if scenario_id in {"reviewer-rejection", "review-context-limit"}:
        required_digests.add(ledger["digest"])
    if scenario_id == "materialization-failure":
        if not isinstance(generated_delta_receipt, Mapping) or not isinstance(disposition_receipt, Mapping):
            missing_required_evidence = True
        else:
            required_digests.update({generated_delta_receipt["digest"], disposition_receipt["digest"]})
    if scenario_id == "execution-interruption-unknown":
        unknown_events = [
            row for row in attempt_events
            if row.get("event_type") in {"EXECUTION_STARTED", "EXECUTION_UNKNOWN"}
        ]
        if not isinstance(disposition_receipt, Mapping) or len(unknown_events) != 2:
            missing_required_evidence = True
        else:
            required_digests.add(disposition_receipt["digest"])
            required_digests.update(row["digest"] for row in unknown_events)
    if scenario_id == "child-execution-retry":
        parent_id = attempt.get("parent_attempt_id")
        parent = next(
            (row for row in state["attempts"] if row.get("attempt_id") == parent_id),
            None,
        )
        parent_events = [
            row for row in state["events"]
            if row.get("attempt_id") == parent_id
            and row.get("event_type") in {"EXECUTION_UNKNOWN", "PROCESS_STOPPED", "ATTEMPT_TERMINAL"}
        ]
        if not isinstance(parent, Mapping) or len(parent_events) != 3:
            missing_required_evidence = True
        else:
            required_digests.update({attempt["digest"], parent["digest"]})
            required_digests.update(row["digest"] for row in parent_events)
    if scenario_id in _OBSERVATION_SCENARIOS:
        required_digests.add(str(observation["digest"]))
        required_digests.update(str(item) for item in observation["event_digests"])
        required_digests.update(str(item) for item in observation["receipt_digests"])
        if isinstance(observation_binding_event, Mapping):
            required_digests.add(str(observation_binding_event["digest"]))

    available_receipt_digests = {
        row.get("digest")
        for row in (
            terminal, finalization, compatibility, ledger, baseline, inventory,
            execution_receipt, generated_delta_receipt, disposition_receipt, *contexts,
        )
        if isinstance(row, Mapping) and _DIGEST.fullmatch(str(row.get("digest", ""))) is not None
    }

    scenario_evidence = {
        "attempt": attempt,
        "attempts": state["attempts"],
        "events": state["events"],
        "exclusions": exclusions["exclusions"],
        "contexts": contexts,
        "reviewer": reviewer,
        "ledger": ledger,
        "compatibility_resume": compatibility.get("resume"),
        "execution": execution_receipt,
        "baseline": baseline,
        "project": project,
        "generated_delta": generated_delta,
        "generated_delta_receipt": generated_delta_receipt,
        "dispositions": dispositions,
        "disposition_receipt": disposition_receipt,
        "terminal": terminal,
        "finalization": finalization,
        "inventory": inventory,
        "available_receipt_digests": available_receipt_digests,
        "scenario_observation": observation,
        "scenario_observation_events": observation_events,
        "scenario_observation_verified": observation_verified,
        "real_execution": real_execution,
        "model_stage_evidence_complete": model_stage_evidence_complete,
    }
    scenario_passed = (
        check_ids == {expectation["check_id"]}
        and not missing_required_evidence
        and required_digests == evidence_digests
        and _scenario_proof(scenario_id, scenario_evidence)
        and terminal.get("accepted") is expectation["accepted"]
        and terminal.get("verification") == expectation["verification"]
        and terminal.get("reason_code") == expectation["reason_code"]
        and terminal.get("attempt_state") == "TERMINAL"
    )

    host = compatibility["host"]
    tuple_value = {
        "skill_pack_version": manifest["package_version"],
        "skill_pack_digest": manifest["skill_pack_digest"],
        "compatibility_contract_version": manifest["compatibility_contract_version"],
        "execution_profile_version": manifest["execution_profile_version"],
        "cli_host": host.get("host_id"),
        "cli_runtime": host.get("runtime_version"),
        "role_policy": compatibility["role_policy"],
        "generator_model": model_stage_models["tc-generator"],
        "reviewer_model": model_stage_models["tc-reviewer"],
        "os": host.get("os"),
        "language_runtime": host.get("language_runtime"),
        "framework": str(framework),
        "build_tool": str(build_tool),
        "execution_adapter": str(adapter_id),
        "project_snapshot_digest": str(attempt["baseline_digest"]),
        "module": str(attempt["module"]),
        "policy_profile": str(attempt["policy_profile"]),
    }
    if not _valid_tuple(tuple_value, allow_unobserved=True):
        raise ReleaseEvalError("release run tuple is invalid")
    tuple_complete = _valid_tuple(tuple_value)
    derived_digest = _digest({
        "record": value["digest"],
        "compatibility": compatibility["digest"],
        "terminal": terminal["digest"],
        "finalization": finalization["digest"],
        "reviewer": ledger["digest"],
        "execution": execution_receipt.get("digest") if isinstance(execution_receipt, Mapping) else None,
    })
    return _ValidatedRun(
        campaign_id=str(value["campaign_id"]),
        suite_id=str(value["suite_id"]),
        suite_digest=str(value["suite_digest"]),
        scenario_id=str(value["scenario_id"]),
        run_id=str(value["run_id"]),
        attempt_id=str(value["attempt_id"]),
        sequence=int(value["sequence"]),
        kind=str(value["kind"]),
        tuple_value=tuple_value,
        tuple_complete=tuple_complete,
        compatible=not compatibility_errors,
        independence="verified" if compatibility_result["independence"] == reviewer.get("isolation") == "verified" else "independence_unverified",
        protocol_violations=tuple(sorted(compatibility_errors)),
        outcome=(terminal.get("attempt_state"), terminal.get("completion"), terminal.get("verification"), terminal.get("coverage"), terminal.get("reason_code"), terminal.get("accepted")),
        scenario_passed=scenario_passed,
        real_execution=real_execution,
        reviewer_session_digest=str(ledger["digest"]),
        model_stage_models=model_stage_models,
        model_stage_invocations=model_stage_invocations,
        model_stage_evidence_complete=model_stage_evidence_complete,
        digest=derived_digest,
    )


def _evaluate_records(
    records: Sequence[_ValidatedRun],
    *,
    suite: Mapping[str, Any] | None = None,
    require_real_execution: bool = False,
    require_independent_review: bool = False,
    predecessor_evaluation: _ValidatedEvaluation | None = None,
) -> dict[str, Any]:
    """Aggregate only records obtained from the confined immutable loader."""
    selected_suite = dict(suite) if suite is not None else _load_scenario_suite(_SUITE_PATH)
    suite_body = {key: item for key, item in selected_suite.items() if key != "digest"}
    if selected_suite.get("digest") != _digest(suite_body):
        raise ReleaseEvalError("release scenario suite digest is invalid")
    suite_id = selected_suite.get("suite_id")
    suite_digest = selected_suite.get("digest")
    smoke_scenario = selected_suite.get("smoke_scenario")
    critical_scenarios = tuple(selected_suite.get("critical_scenarios", ()))
    scenario_contracts = selected_suite.get("scenario_contracts", {})
    if (
        not isinstance(suite_id, str)
        or not _DIGEST.fullmatch(str(suite_digest))
        or not isinstance(smoke_scenario, str)
        or not critical_scenarios
        or any(not isinstance(item, str) for item in critical_scenarios)
        or selected_suite.get("stable_repetitions") != 3
        or selected_suite.get("escalated_repetitions") != 5
    ):
        raise ReleaseEvalError("release scenario suite is invalid")

    if any(not isinstance(item, _ValidatedRun) for item in records):
        raise TypeError("release policy accepts only validated run evidence")
    values = list(records)
    if not values:
        raise ReleaseEvalError("campaign contains no evidence")
    values.sort(key=lambda item: item.sequence)
    if [item.sequence for item in values] != list(range(1, len(values) + 1)):
        raise ReleaseEvalError("campaign evidence must be append-only and contiguous")
    if len({item.run_id for item in values}) != len(values):
        raise ReleaseEvalError("campaign run evidence is duplicated")
    campaigns = {item.campaign_id for item in values}
    tuples = {_canonical(item.tuple_value) for item in values}
    if len(campaigns) != 1 or len(tuples) != 1:
        raise ReleaseEvalError("campaign cannot transfer trust across identities or tuples")
    models_by_stage: dict[str, set[str]] = {stage: set() for stage in _MODEL_STAGES}
    for item in values:
        models = item.model_stage_models
        if not isinstance(models, Mapping) or set(models) != set(_MODEL_STAGES):
            raise ReleaseEvalError("campaign model stage evidence is incomplete")
        for stage, model_id in models.items():
            if model_id is None:
                continue
            if (
                not isinstance(model_id, str)
                or _LABEL.fullmatch(model_id) is None
                or model_id.startswith("unverified-")
            ):
                raise ReleaseEvalError("campaign model stage evidence is incomplete")
            models_by_stage[stage].add(model_id)
    if any(len(models) > 1 for models in models_by_stage.values()):
        raise ReleaseEvalError("campaign cannot transfer trust across model stage identities")
    model_stages = {
        stage: next(iter(models)) if models else None
        for stage, models in models_by_stage.items()
    }
    if (
        model_stages["tc-generator"] not in {None, values[0].tuple_value.get("generator_model")}
        or model_stages["tc-reviewer"] not in {None, values[0].tuple_value.get("reviewer_model")}
    ):
        raise ReleaseEvalError("campaign model stage identity contradicts exact tuple")
    if any(item.suite_id != suite_id or item.suite_digest != suite_digest for item in values):
        raise ReleaseEvalError("campaign evidence is bound to another scenario suite")
    smoke = [item for item in values if item.kind == "smoke"]
    critical = [item for item in values if item.kind == "critical"]
    if len(smoke) != 1 or smoke[0] is not values[0] or smoke[0].scenario_id != smoke_scenario:
        raise ReleaseEvalError("campaign requires exactly one leading smoke run")
    allowed_critical = set(critical_scenarios)
    if any(item.scenario_id not in allowed_critical for item in critical):
        raise ReleaseEvalError("campaign contains an unknown critical scenario")

    violations = {code for item in values for code in item.protocol_violations}
    reviewer_sessions = [item.reviewer_session_digest for item in values]
    model_invocations = [
        invocation
        for item in values
        for invocation in item.model_stage_invocations
    ]
    if len(set(reviewer_sessions)) != len(reviewer_sessions):
        violations.add("CAMPAIGN_REVIEWER_SESSION_REUSED")
    if len(set(model_invocations)) != len(model_invocations):
        violations.add("CAMPAIGN_MODEL_INVOCATION_REUSED")
    violations = sorted(violations)
    observed_by_scenario = {
        scenario_id: {item.outcome for item in critical if item.scenario_id == scenario_id}
        for scenario_id in critical_scenarios
    }
    instability = any(len(observed) > 1 for observed in observed_by_scenario.values())
    predecessor = None
    if predecessor_evaluation is not None:
        if not isinstance(predecessor_evaluation, _ValidatedEvaluation):
            raise TypeError("predecessor evaluation must be evaluator-owned")
        predecessor = predecessor_evaluation._readback()
    required_critical_runs = (
        selected_suite["escalated_repetitions"]
        if instability or violations or predecessor is not None
        else selected_suite["stable_repetitions"]
    )
    scenario_counts = {
        scenario_id: sum(1 for item in critical if item.scenario_id == scenario_id)
        for scenario_id in critical_scenarios
    }
    def qualifies(item: _ValidatedRun) -> bool:
        if not item.compatible or not item.scenario_passed:
            return False
        if scenario_contracts[item.scenario_id]["execution_required"] and not item.real_execution:
            return False
        if item.independence != "verified":
            return False
        return True

    smoke_passed = qualifies(smoke[0])
    exact_tuple = dict(values[0].tuple_value)
    exact_tuple["model_stages"] = model_stages
    campaign_id = next(iter(campaigns))
    if predecessor is not None:
        if not _is_escalation_predecessor(predecessor, exact_tuple, selected_suite, campaign_id=campaign_id):
            raise ReleaseEvalError("predecessor evaluation does not prove an escalation cause for this tuple")
    ready = (
        smoke_passed
        and all(count >= required_critical_runs for count in scenario_counts.values())
        and all(qualifies(item) for item in critical)
        and not instability
        and not violations
        and require_real_execution
        and require_independent_review
        and all(item.tuple_complete for item in values)
        and all(item.model_stage_evidence_complete for item in values)
    )
    body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "policy": _POLICY,
        "campaign_id": campaign_id,
        "tuple": exact_tuple,
        "scenario_suite": {"suite_id": suite_id, "digest": suite_digest},
        "scenario_counts": scenario_counts,
        "smoke_runs": 1,
        "critical_runs": len(critical),
        "required_critical_runs": required_critical_runs,
        "instability_observed": instability,
        "protocol_violations": violations,
        "require_real_execution": require_real_execution,
        "require_independent_review": require_independent_review,
        "ready": ready,
        "predecessor_evaluation_digest": predecessor["digest"] if predecessor is not None else None,
        "evaluated_run_digests": [item.digest for item in values],
    }
    body["digest"] = _digest(body)
    return _ValidatedEvaluation(body, token=_EVALUATION_TOKEN)


def _load_campaign(path: Path) -> list[Mapping[str, Any]]:
    from tools.schema_validation import StrictJsonError, load_json_strict

    if not path.is_dir():
        raise ReleaseEvalError("campaign directory is unavailable")
    records: list[Mapping[str, Any]] = []
    for candidate in sorted(path.glob("*.json")):
        try:
            reference = load_json_strict(candidate)
        except (OSError, UnicodeDecodeError, StrictJsonError) as error:
            raise ReleaseEvalError("campaign evidence is unreadable") from error
        records.append(_load_ref(path, reference))
    return records


def _load_predecessor_evaluation(path: Path, root: Path) -> _ValidatedEvaluation:
    from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics

    try:
        value = load_json_strict(path)
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ReleaseEvalError("predecessor evaluation is unreadable") from error
    if not isinstance(value, Mapping):
        raise ReleaseEvalError("predecessor evaluation is invalid")
    body = {key: item for key, item in value.items() if key != "digest"}
    if (
        value.get("digest") != _digest(body)
        or schema_diagnostics(value, root / "schemas" / "release-eval-receipt.schema.json", root)
    ):
        raise ReleaseEvalError("predecessor evaluation is invalid")
    return _ValidatedEvaluation(value, token=_EVALUATION_TOKEN)


def _load_evaluation_history(project: Path, root: Path) -> list[_ValidatedEvaluation]:
    from tools.confined_output import OutputConfinementError, read_confined_bytes
    from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics

    ledger = project / ".pilot-runs" / "release-eval-ledger"
    if not ledger.exists():
        return []
    if not ledger.is_dir() or ledger.is_symlink():
        raise ReleaseEvalError("release evaluation history is invalid")
    values: list[_ValidatedEvaluation] = []
    for sequence, path in enumerate(sorted(ledger.iterdir()), start=1):
        match = re.fullmatch(r"([0-9]{10})-([0-9a-f]{64})\.json", path.name)
        if match is None or int(match.group(1)) != sequence:
            raise ReleaseEvalError("release evaluation history is invalid")
        try:
            raw = read_confined_bytes(project, project / ".pilot-runs", path)
            value = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
        except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
            raise ReleaseEvalError("release evaluation history is unreadable") from error
        body = {key: item for key, item in value.items() if key != "digest"} if isinstance(value, Mapping) else {}
        if (
            not isinstance(value, Mapping)
            or value.get("digest") != _digest(body)
            or value.get("digest") != "sha256:" + match.group(2)
            or schema_diagnostics(value, root / "schemas" / "release-eval-receipt.schema.json", root)
        ):
            raise ReleaseEvalError("release evaluation history is invalid")
        values.append(_ValidatedEvaluation(value, token=_EVALUATION_TOKEN))
    return values


def _pending_escalation(
    history: Sequence[_ValidatedEvaluation], tuple_value: Mapping[str, Any], suite: Mapping[str, Any],
) -> _ValidatedEvaluation | None:
    pending: dict[bytes, _ValidatedEvaluation] = {}
    for evaluation in history:
        value = evaluation._readback()
        scope = _escalation_scope(value["tuple"])
        if _is_escalation_predecessor(value, value["tuple"], suite):
            pending[scope] = evaluation
        elif (
            value.get("ready") is True
            and value.get("required_critical_runs") == 5
            and scope in pending
            and value.get("predecessor_evaluation_digest") == pending[scope].get("digest")
        ):
            del pending[scope]
    return pending.get(_escalation_scope(tuple_value))


def _append_evaluation_history(
    project: Path, root: Path, evaluation: Mapping[str, Any],
) -> None:
    from tools.confined_output import (
        OutputConfinementError, create_confined_bytes_exclusive,
        ensure_project_child_directory, read_confined_bytes,
    )

    history = _load_evaluation_history(project, root)
    if any(item.get("digest") == evaluation.get("digest") for item in history):
        return
    digest = str(evaluation.get("digest", ""))
    if _DIGEST.fullmatch(digest) is None:
        raise ReleaseEvalError("release evaluation is invalid")
    data = _canonical(evaluation)
    target = project / ".pilot-runs" / "release-eval-ledger" / (
        f"{len(history) + 1:010d}-{digest.removeprefix('sha256:')}.json"
    )
    try:
        ensure_project_child_directory(project, project / ".pilot-runs")
        create_confined_bytes_exclusive(project, project / ".pilot-runs", target, data)
        readback = read_confined_bytes(project, project / ".pilot-runs", target)
    except (OutputConfinementError, OSError, ValueError) as error:
        raise ReleaseEvalError("release evaluation history publication failed") from error
    if readback != data:
        raise ReleaseEvalError("release evaluation history read-back failed")


def _evaluate_with_history(
    records: Sequence[_ValidatedRun],
    *,
    project: Path,
    pack_root: Path,
    suite: Mapping[str, Any],
    require_real_execution: bool = False,
    require_independent_review: bool = False,
    predecessor_evaluation: _ValidatedEvaluation | None = None,
) -> _ValidatedEvaluation:
    values = list(records)
    if not values:
        raise ReleaseEvalError("campaign contains no evidence")
    resolved_project = project.resolve(strict=True)
    history = _load_evaluation_history(resolved_project, pack_root)
    pending = _pending_escalation(history, values[0].tuple_value, suite)
    if predecessor_evaluation is not None:
        predecessor = predecessor_evaluation._readback()
        if pending is not None and pending.get("digest") != predecessor.get("digest"):
            raise ReleaseEvalError("predecessor evaluation is not the active escalation lineage")
        if pending is None:
            if not _is_escalation_predecessor(predecessor, values[0].tuple_value, suite):
                raise ReleaseEvalError("predecessor evaluation is not the active escalation lineage")
            _append_evaluation_history(resolved_project, pack_root, predecessor_evaluation)
            pending = predecessor_evaluation
    evaluation = _evaluate_records(
        values,
        suite=suite,
        require_real_execution=require_real_execution,
        require_independent_review=require_independent_review,
        predecessor_evaluation=pending,
    )
    _append_evaluation_history(resolved_project, pack_root, evaluation)
    return evaluation


def evaluate_campaign(
    campaign_dir: Path,
    project: Path,
    *,
    pack_root: Path | None = None,
    require_real_execution: bool = False,
    require_independent_review: bool = False,
    predecessor_evaluation: Path | None = None,
) -> dict[str, Any]:
    """Evaluate one campaign directory; callers cannot submit asserted facts."""
    from tools.release_manifest import load_release_manifest

    if not isinstance(campaign_dir, Path) or not isinstance(project, Path):
        raise ReleaseEvalError("campaign evaluation requires campaign and project directories")
    root = (pack_root or Path(__file__).resolve().parents[1]).resolve()
    suite = _load_scenario_suite(root / "evals" / "scenarios" / "pilot-critical.json")
    try:
        manifest = load_release_manifest(root)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ReleaseEvalError("release manifest is invalid") from error
    records = _load_campaign(campaign_dir)
    predecessor = (
        _load_predecessor_evaluation(predecessor_evaluation, root)
        if predecessor_evaluation is not None
        else None
    )
    try:
        derived = [
            _derive_record(
                record,
                campaign_dir=campaign_dir,
                project=project,
                manifest=manifest,
                suite=suite,
            )
            for record in records
        ]
    except ReleaseEvalError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise ReleaseEvalError("campaign durable evidence is invalid") from error
    return _evaluate_with_history(
        derived,
        project=project,
        pack_root=root,
        suite=suite,
        require_real_execution=require_real_execution,
        require_independent_review=require_independent_review,
        predecessor_evaluation=predecessor,
    )


def _human_approval_digest(path: Path, release_eval_receipt: Mapping[str, Any]) -> str:
    from tools.schema_validation import StrictJsonError, load_json_strict, schema_diagnostics

    try:
        receipt = load_json_strict(path)
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ReleaseEvalError("previous-known-good approval receipt is unavailable") from error
    if not isinstance(release_eval_receipt, _ValidatedEvaluation):
        raise ReleaseEvalError("validated release evaluation is required for approval")
    evaluated = release_eval_receipt._readback()
    evaluated_body = {key: item for key, item in evaluated.items() if key != "digest"}
    if (
        evaluated.get("digest") != _digest(evaluated_body)
        or schema_diagnostics(
            evaluated,
            Path(__file__).resolve().parents[1] / "schemas" / "release-eval-receipt.schema.json",
            Path(__file__).resolve().parents[1],
        )
        or evaluated.get("ready") is not True
        or evaluated.get("require_real_execution") is not True
        or evaluated.get("require_independent_review") is not True
        or not isinstance(evaluated.get("campaign_id"), str)
        or not _LABEL.fullmatch(evaluated["campaign_id"])
    ):
        raise ReleaseEvalError("previous-known-good release evaluation is invalid")
    required = {
        "schema_version", "approval", "approver", "campaign_id",
        "release_eval_digest", "digest",
    }
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != required
        or receipt.get("schema_version") != "1.0.0"
        or receipt.get("approval") != "APPROVED"
        or not isinstance(receipt.get("approver"), str)
        or not _LABEL.fullmatch(receipt["approver"])
    ):
        raise ReleaseEvalError("previous-known-good approval receipt is invalid")
    digest = _digest({key: item for key, item in receipt.items() if key != "digest"})
    if receipt.get("digest") != digest:
        raise ReleaseEvalError("previous-known-good approval receipt digest is invalid")
    if (
        receipt.get("campaign_id") != evaluated["campaign_id"]
        or receipt.get("release_eval_digest") != evaluated["digest"]
    ):
        raise ReleaseEvalError("previous-known-good approval does not bind this release evaluation")
    return digest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--module", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--require-real-execution", action="store_true")
    parser.add_argument("--require-independent-review", action="store_true")
    parser.add_argument("--predecessor-evaluation", type=Path)
    parser.add_argument("--previous-known-good", action="store_true")
    parser.add_argument("--approval-receipt", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.previous_known_good and args.approval_receipt is None:
            raise ReleaseEvalError("previous-known-good promotion requires explicit human approval")
        if args.approval_receipt is not None and not args.previous_known_good:
            raise ReleaseEvalError("approval receipt requires previous-known-good promotion")
        receipt = evaluate_campaign(
            args.campaign_dir,
            args.project.resolve(),
            require_real_execution=args.require_real_execution,
            require_independent_review=args.require_independent_review,
            predecessor_evaluation=args.predecessor_evaluation,
        )
        expected = receipt["tuple"]
        if expected["module"] != args.module:
            raise ReleaseEvalError("requested module does not match campaign")
        if expected["policy_profile"] != args.policy:
            raise ReleaseEvalError("requested policy does not match campaign")
        if args.previous_known_good:
            _human_approval_digest(args.approval_receipt, receipt)
    except ReleaseEvalError as error:
        print(json.dumps({"status": "error", "code": "RELEASE_EVAL_INVALID", "message": str(error)}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
