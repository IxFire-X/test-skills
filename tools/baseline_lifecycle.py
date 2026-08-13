"""Immutable terminal receipts and durable feature-baseline lineage."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Mapping

from tools.flow_artifacts import FlowError, StoredArtifact, artifact_sha256, canonical_bytes
from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics


ELIGIBLE_FINAL_STATUSES = frozenset({"PASS", "PASS_WITH_MANUAL_REMAINDER", "MANUAL_ONLY"})
INELIGIBLE_FINAL_STATUSES = frozenset({"FAIL", "NOT_RUNNABLE", "BLOCKED"})

_ROOT = Path(__file__).resolve().parents[1]
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_FINGERPRINT_REGISTRIES = ("pipeline_contract", "policy_bundle", "tool_bundle", "schema_bundle")
_FINGERPRINT_DIGESTS = tuple(name + "_sha256" for name in _FINGERPRINT_REGISTRIES)
_PREFIX_KEYS = (
    "technical_test_inventory", "authorized_behavior_sources", "change_scope_receipt",
    "managed_behavior_context", "changed_behavior_context", "behavior_source_accounting",
    "behavior_context_receipt", "technical_test_classification", "classification_review",
    "effective_technical_evidence", "validation_report",
)
_TAIL_KEYS = (
    "effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review",
    "run_result", "trace_document", "trace_audit", "orchestrator_output",
)
_BASELINE_ARTIFACT_KEYS = (
    "technical_test_inventory_sha256", "authorized_behavior_sources_sha256",
    "managed_behavior_context_sha256", "behavior_source_accounting_sha256",
    "behavior_context_receipt_sha256", "effective_technical_evidence_sha256",
    "effective_document_sha256", "effective_bundle_receipt_sha256", "trace_document_sha256",
    "trace_audit_sha256", "orchestrator_output_sha256", "terminal_run_receipt_sha256",
)
_SCHEMAS = {
    "effective_document": "canonical-test-document.schema.json",
    "automation_artifact": "tc-to-autotest-output.schema.json",
    "autotest_review": "autotest-reviewer-output.schema.json",
    "run_result": "run-tests-output.schema.json",
    "trace_document": "trace-document.schema.json",
    "orchestrator_output": "orchestrator-output.schema.json",
    "technical_test_inventory": "source-inventory-output.schema.json",
    "authorized_behavior_sources": "source-inventory-output.schema.json",
    "technical_test_classification": "test-classifier-output.schema.json",
    "classification_review": "test-classifier-reviewer-output.schema.json",
    "effective_technical_evidence": "effective-technical-evidence.schema.json",
    "validation_report": "tc-reviewer-output.schema.json",
}


def _error(code: str, path: str, message: str) -> FlowError:
    return FlowError(code, path, message)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _mapping(value: Any, path: str, code: str = "FEATURE_FLOW_INPUT") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(code, path, "A closed object is required.")
    return value


def _closed(value: Any, keys: tuple[str, ...] | set[str], path: str, code: str = "FEATURE_FLOW_INPUT") -> Mapping[str, Any]:
    result = _mapping(value, path, code)
    if set(result) != set(keys):
        raise _error(code, path, "The object does not use the required closed shape.")
    return result


def _digest(value: Any, path: str, code: str = "BASELINE_BINDING") -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise _error(code, path, "A SHA-256 digest is required.")
    return value


def _git_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or _GIT_RE.fullmatch(value) is None:
        raise _error("BASELINE_BINDING", path, "A Git object identity is required.")
    return value


def _safe_relative(value: Any, path: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise _error("FEATURE_FLOW_INPUT", path, "Artifact path must be a relative canonical POSIX path.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts) or candidate.as_posix() != value:
        raise _error("FEATURE_FLOW_INPUT", path, "Artifact path must be a relative canonical POSIX path.")
    return candidate


def _read_json_bytes(raw: bytes, path: str) -> Mapping[str, Any]:
    try:
        value = loads_json_strict(raw.decode("utf-8"))
    except (StrictJsonError, UnicodeDecodeError):
        raise _error("FEATURE_FLOW_INPUT", path, "Stored artifact is not strict UTF-8 JSON.") from None
    result = _mapping(value, path)
    if raw != canonical_bytes(result):
        raise _error("BASELINE_BINDING", path, "Stored artifact bytes are not canonical.")
    return result


def _read_stored(stored: Any, path: str, root: Path | None = None) -> Mapping[str, Any]:
    if not isinstance(stored, StoredArtifact):
        raise _error("FEATURE_FLOW_INPUT", path, "A stored artifact is required.")
    try:
        target = stored.path.resolve(strict=True)
        if root is not None:
            target.relative_to(root.resolve(strict=True))
        raw = target.read_bytes()
    except ValueError:
        raise _error("FLOW_ATOMIC_WRITE", path, "Stored artifact is outside the authoritative run root.") from None
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", path, "Stored artifact could not be read back.") from None
    value = _read_json_bytes(raw, path)
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if raw != stored.payload or stored.sha256 != actual or artifact_sha256(value) != actual:
        raise _error("BASELINE_BINDING", path, "Stored artifact identity does not match readback.")
    return value


def _read_binding(root: Path, binding: Any, path: str) -> tuple[Mapping[str, Any], str]:
    row = _closed(binding, {"path", "sha256"}, path)
    relative = _safe_relative(row.get("path"), path + "/path")
    expected = _digest(row.get("sha256"), path + "/sha256")
    try:
        target = (root / Path(*relative.parts)).resolve(strict=True)
        target.relative_to(root)
        raw = target.read_bytes()
    except ValueError:
        raise _error("FEATURE_FLOW_INPUT", path, "Bound artifact path escapes the run root.") from None
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", path, "Bound artifact could not be read back.") from None
    value = _read_json_bytes(raw, path)
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != expected or artifact_sha256(value) != expected:
        raise _error("BASELINE_BINDING", path, "Bound artifact digest does not match readback.")
    return value, expected


def _validate_schema(value: Mapping[str, Any], schema_name: str, path: str) -> None:
    if schema_diagnostics(value, _ROOT / "schemas" / schema_name, _ROOT):
        raise _error("FEATURE_FLOW_INPUT", path, "Stored artifact does not satisfy its closed local schema.")


def _fingerprint_registries(value: Any) -> dict[str, str]:
    registries = _closed(value, set(_FINGERPRINT_REGISTRIES), "/prefix_ledger/fingerprints")
    projected: dict[str, str] = {}
    for name in _FINGERPRINT_REGISTRIES:
        path = f"/prefix_ledger/fingerprints/{name}"
        registry = _closed(registries[name], {"sha256", "files"}, path, "BASELINE_FINGERPRINT")
        files = registry.get("files")
        if not isinstance(files, list) or not files:
            raise _error("BASELINE_FINGERPRINT", path + "/files", "Fingerprint file registry must be nonempty.")
        normalized: list[dict[str, str]] = []
        previous: str | None = None
        for index, item in enumerate(files):
            row_path = f"{path}/files/{index}"
            row = _closed(item, {"path", "sha256"}, row_path, "BASELINE_FINGERPRINT")
            try:
                file_path = _safe_relative(row.get("path"), row_path + "/path").as_posix()
            except FlowError as caught:
                raise _error("BASELINE_FINGERPRINT", row_path + "/path", "Fingerprint path is not safe and canonical.") from caught
            if previous is not None and file_path <= previous:
                raise _error("BASELINE_FINGERPRINT", path + "/files", "Fingerprint files must be unique and strictly path-sorted.")
            previous = file_path
            normalized.append({"path": file_path, "sha256": _digest(row.get("sha256"), row_path + "/sha256", "BASELINE_FINGERPRINT")})
        aggregate = artifact_sha256({"files": normalized})
        if registry.get("sha256") != aggregate:
            raise _error("BASELINE_FINGERPRINT", path + "/sha256", "Fingerprint aggregate does not bind its file registry.")
        projected[name + "_sha256"] = aggregate
    return projected


def _fingerprint_digests(value: Any, path: str = "/fingerprints") -> dict[str, str]:
    source = _closed(value, set(_FINGERPRINT_DIGESTS), path, "BASELINE_FINGERPRINT")
    return {name: _digest(source[name], path + "/" + name, "BASELINE_FINGERPRINT") for name in _FINGERPRINT_DIGESTS}


def _source(value: Any, path: str) -> Mapping[str, Any]:
    source = _closed(value, {"document_id", "revision", "source_digest"}, path, "BASELINE_BINDING")
    if not isinstance(source.get("document_id"), str) or not source["document_id"] or type(source.get("revision")) is not int or source["revision"] < 1:
        raise _error("BASELINE_BINDING", path, "Document source identity is invalid.")
    _digest(source.get("source_digest"), path + "/source_digest")
    return source


def _validate_trace_audit(audit: Mapping[str, Any], trace: Mapping[str, Any]) -> str:
    top = _closed(audit, {"valid", "trace_audit", "errors", "warnings", "summary"}, "/tail_artifacts/trace_audit")
    nested = _closed(top.get("trace_audit"), {"verdict", "source_digest", "final_verdict", "required_symbol_pairs", "relation_count", "errors"}, "/tail_artifacts/trace_audit/trace_audit")
    errors = top.get("errors")
    nested_errors = nested.get("errors")
    if type(top.get("valid")) is not bool or not isinstance(errors, list) or not isinstance(top.get("warnings"), list) or not isinstance(nested_errors, list):
        raise _error("FEATURE_FLOW_INPUT", "/tail_artifacts/trace_audit", "Trace audit shape is invalid.")
    expected_verdict = "PASS" if top["valid"] else "FAIL"
    if nested.get("verdict") != expected_verdict or bool(errors) == top["valid"] or bool(nested_errors) == top["valid"]:
        raise _error("FEATURE_FLOW_INPUT", "/tail_artifacts/trace_audit", "Trace audit validity, verdict, and errors disagree.")
    trace_source = _source(trace.get("source"), "/tail_artifacts/trace_document/source")
    if nested.get("source_digest") != trace_source["source_digest"] or nested.get("final_verdict") != trace.get("final_verdict"):
        raise _error("BASELINE_BINDING", "/tail_artifacts/trace_audit", "Trace audit does not bind the trace source and final verdict.")
    return expected_verdict


def build_terminal_run_receipt(
    prefix_ledger: StoredArtifact,
    tail_artifacts: Mapping[str, StoredArtifact | None],
) -> Mapping[str, Any]:
    """Reopen the authoritative prefix ledger and tail, then derive acceptance."""
    if not isinstance(prefix_ledger, StoredArtifact):
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "A stored prefix ledger is required.")
    if prefix_ledger.path.name != "prefix-ledger.json" or prefix_ledger.path.parent.name != "manifest":
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "Prefix ledger must be manifest/prefix-ledger.json.")
    try:
        run_root = prefix_ledger.path.parent.parent.resolve(strict=True)
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", "/prefix_ledger", "Prefix ledger run root is unavailable.") from None
    ledger = _read_stored(prefix_ledger, "/prefix_ledger", run_root)
    _closed(ledger, {
        "schema_version", "artifact", "repository_id", "selected_module", "run_mode", "change_input",
        "analytics_sha256", "source_drift", "fingerprints", "artifacts",
    }, "/prefix_ledger")
    if ledger.get("schema_version") != "1.0.0" or ledger.get("artifact") != "prefix-ledger":
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "Prefix ledger must use closed V1 identity.")
    repository = _digest(ledger.get("repository_id"), "/prefix_ledger/repository_id")
    if not isinstance(ledger.get("selected_module"), str) or not ledger["selected_module"] or ledger.get("run_mode") not in {"FULL", "CHANGE_SET"} or type(ledger.get("source_drift")) is not bool:
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "Prefix ledger run metadata is invalid.")
    change_input = _mapping(ledger.get("change_input"), "/prefix_ledger/change_input")
    analytics = _digest(ledger.get("analytics_sha256"), "/prefix_ledger/analytics_sha256")
    fingerprints = _fingerprint_registries(ledger.get("fingerprints"))
    bindings = _closed(ledger.get("artifacts"), set(_PREFIX_KEYS), "/prefix_ledger/artifacts")
    prefix_values: dict[str, Mapping[str, Any]] = {}
    artifact_digests: dict[str, str | None] = {}
    for name in _PREFIX_KEYS:
        value, sha256 = _read_binding(run_root, bindings[name], f"/prefix_ledger/artifacts/{name}")
        prefix_values[name] = value
        artifact_digests[name + "_sha256"] = sha256
        schema = _SCHEMAS.get(name)
        if schema is not None:
            _validate_schema(value, schema, f"/prefix_ledger/artifacts/{name}")

    if not isinstance(tail_artifacts, Mapping) or set(tail_artifacts) != set(_TAIL_KEYS):
        raise _error("FEATURE_FLOW_INPUT", "/tail_artifacts", "Tail artifacts must use the exact closed key set.")
    tail_values: dict[str, Mapping[str, Any] | None] = {}
    for name in _TAIL_KEYS:
        stored = tail_artifacts[name]
        if stored is None:
            if name != "run_result":
                raise _error("FEATURE_FLOW_INPUT", f"/tail_artifacts/{name}", "Only run_result may be null.")
            tail_values[name] = None
            artifact_digests[name + "_sha256"] = None
            continue
        value = _read_stored(stored, f"/tail_artifacts/{name}", run_root)
        tail_values[name] = value
        artifact_digests[name + "_sha256"] = stored.sha256
        schema = _SCHEMAS.get(name)
        if schema is not None:
            _validate_schema(value, schema, f"/tail_artifacts/{name}")

    classification = _mapping(prefix_values["classification_review"]["artifacts"]["classification_review"], "/prefix_ledger/artifacts/classification_review")
    classification_verdict = classification.get("verdict")
    findings = classification.get("findings")
    if classification.get("technical_test_inventory_sha256") != artifact_digests["technical_test_inventory_sha256"] or classification.get("classification_sha256") != artifact_digests["technical_test_classification_sha256"]:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/classification_review", "Classification review bindings do not agree with the ledger.")
    if not isinstance(findings, list) or (classification_verdict == "ПРИНЯТО") != (findings == []):
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger/artifacts/classification_review", "Classification verdict and findings disagree.")

    validation_envelope = prefix_values["validation_report"]
    report = _mapping(validation_envelope["artifacts"]["validation_report"], "/prefix_ledger/artifacts/validation_report")
    test_case_verdict = report.get("verdict")

    document = _mapping(tail_values["effective_document"], "/tail_artifacts/effective_document")
    expected_source = {
        "document_id": document.get("document_id"), "revision": document.get("revision"),
        "source_digest": artifact_digests["effective_document_sha256"],
    }
    bundle = _mapping(tail_values["effective_bundle_receipt"], "/tail_artifacts/effective_bundle_receipt")
    if bundle.get("document_id") != expected_source["document_id"] or bundle.get("revision") != expected_source["revision"] or bundle.get("document_sha256") != expected_source["source_digest"]:
        raise _error("BASELINE_BINDING", "/tail_artifacts/effective_bundle_receipt", "Effective bundle does not bind the effective document.")
    if test_case_verdict == "ПРИНЯТО" and report.get("candidate", {}).get("document_sha256") != expected_source["source_digest"]:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/validation_report", "Accepted validation report does not bind the effective document.")
    if test_case_verdict == "AUTO_FIX_APPLIED":
        successor = validation_envelope["artifacts"].get("successor_document")
        if not isinstance(successor, Mapping) or artifact_sha256(successor) != expected_source["source_digest"]:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/validation_report", "Auto-fixed successor is not the effective document.")

    automation_envelope = _mapping(tail_values["automation_artifact"], "/tail_artifacts/automation_artifact")
    automation = _mapping(automation_envelope.get("artifacts"), "/tail_artifacts/automation_artifact/artifacts")
    auto_source = _source(automation.get("source"), "/tail_artifacts/automation_artifact/artifacts/source")
    review_envelope = _mapping(tail_values["autotest_review"], "/tail_artifacts/autotest_review")
    review = _mapping(review_envelope["artifacts"]["autotest_review"], "/tail_artifacts/autotest_review/artifacts/autotest_review")
    review_source = _source(review.get("source"), "/tail_artifacts/autotest_review/artifacts/autotest_review/source")
    trace = _mapping(tail_values["trace_document"], "/tail_artifacts/trace_document")
    trace_source = _source(trace.get("source"), "/tail_artifacts/trace_document/source")
    if dict(auto_source) != expected_source or dict(review_source) != expected_source or dict(trace_source) != expected_source:
        raise _error("BASELINE_BINDING", "/tail_artifacts", "Tail document sources do not bind the effective document.")

    run = tail_values["run_result"]
    run_source: Mapping[str, Any] | None = None
    run_verdict: str | None = None
    if run is not None:
        run_source = _source(run.get("source"), "/tail_artifacts/run_result/source")
        run_verdict = run.get("verdict") if isinstance(run.get("verdict"), str) else None
        if dict(run_source) != expected_source:
            raise _error("BASELINE_BINDING", "/tail_artifacts/run_result", "Run result source does not bind the effective document.")

    audit = _mapping(tail_values["trace_audit"], "/tail_artifacts/trace_audit")
    trace_verdict = _validate_trace_audit(audit, trace)
    orchestration_envelope = _mapping(tail_values["orchestrator_output"], "/tail_artifacts/orchestrator_output")
    orchestration = _mapping(orchestration_envelope["artifacts"]["orchestration_result"], "/tail_artifacts/orchestrator_output/artifacts/orchestration_result")
    final_status = orchestration.get("final_status")
    if final_status not in ELIGIBLE_FINAL_STATUSES | INELIGIBLE_FINAL_STATUSES:
        raise _error("FEATURE_FLOW_INPUT", "/tail_artifacts/orchestrator_output", "Final status is outside the closed union.")
    expected_run_source = None if run_source is None else dict(run_source)
    source_fields = ("effective_source", "automation_source", "autotest_review_source", "trace_source")
    if any(orchestration.get(name) != expected_source for name in source_fields) or orchestration.get("run_source") != expected_run_source:
        raise _error("BASELINE_BINDING", "/tail_artifacts/orchestrator_output", "Orchestrator sources do not agree with accepted tail artifacts.")
    if orchestration.get("effective_bundle_receipt") != bundle or orchestration.get("automation_status") != automation.get("automation_status") or orchestration.get("autotest_review_verdict") != review.get("verdict") or orchestration.get("run_verdict") != run_verdict or final_status != trace.get("final_verdict"):
        raise _error("BASELINE_BINDING", "/tail_artifacts/orchestrator_output", "Orchestrator verdicts do not agree with accepted tail artifacts.")
    execution = trace.get("execution")
    if run is None:
        valid_no_run = execution is None and (
            (final_status == "MANUAL_ONLY" and automation.get("automation_status") == "GENERATED")
            or (final_status == "BLOCKED" and automation.get("automation_status") == "BLOCKED")
        )
        if not valid_no_run:
            raise _error("BASELINE_BINDING", "/tail_artifacts/run_result", "Null run_result is not a validated no-run branch.")
    elif not isinstance(execution, Mapping) or execution.get("verdict") != run_verdict:
        raise _error("BASELINE_BINDING", "/tail_artifacts/trace_document/execution", "Trace execution does not agree with run_result.")

    receipt = {
        "schema_version": "1.0.0", "artifact": "terminal-run-receipt", "repository_id": repository,
        "selected_module": ledger["selected_module"], "run_mode": ledger["run_mode"],
        "change_input": _plain(change_input), "analytics_sha256": analytics, "fingerprints": fingerprints,
        "artifacts": artifact_digests,
        "acceptance": {
            "classification_verdict": classification_verdict,
            "test_case_review_verdict": test_case_verdict,
            "autotest_review_verdict": review.get("verdict"),
            "trace_verdict": trace_verdict,
            "final_status": final_status,
            "source_drift": ledger["source_drift"],
        },
    }
    _validate_schema(receipt, "terminal-run-receipt.schema.json", "/terminal_receipt")
    return _freeze(receipt)


@dataclass(frozen=True)
class ValidatedBaseline:
    repository_id: str
    target_commit: str
    target_tree: str
    selected_module: str
    inventory_sha256: str
    context_sha256: str
    document_sha256: str
    bundle_sha256: str
    fingerprints: Mapping[str, str]
    receipt_sha256: str
    receipt: Mapping[str, Any]


def _git(project: Path, *args: str) -> str:
    try:
        completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    except OSError:
        raise _error("FEATURE_FLOW_INPUT", "/project", "Git proof is unavailable.") from None
    if completed.returncode:
        raise _error("FEATURE_FLOW_INPUT", "/project", "Git proof could not be established.")
    try:
        return completed.stdout.decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        raise _error("FEATURE_FLOW_INPUT", "/project", "Git proof is not canonical text.") from None


def _repository_id(project: Path) -> str:
    common = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if not common.is_absolute():
        common = project / common
    resolved = common.resolve(strict=True)
    return "sha256:" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


def _object_tree(project: Path, commit: str) -> str:
    return _git(project, "rev-parse", "--verify", f"{commit}^{{tree}}")


def validate_baseline_receipt(
    receipt: Mapping[str, Any],
    project: Path,
    selected_module: str,
    fingerprints: Mapping[str, Any],
) -> ValidatedBaseline:
    value = _mapping(_plain(receipt), "/receipt")
    _validate_schema(value, "feature-baseline-receipt.schema.json", "/receipt")
    if not isinstance(project, Path) or not isinstance(selected_module, str) or not selected_module:
        raise _error("FEATURE_FLOW_INPUT", "/receipt", "Project and selected module are required.")
    repository = _digest(value.get("repository_id"), "/receipt/repository_id")
    if repository != _repository_id(project) or value.get("selected_module") != selected_module:
        raise _error("BASELINE_BINDING", "/receipt", "Baseline repository or module is incompatible.")
    commit = _git_id(value.get("target_commit"), "/receipt/target_commit")
    tree = _git_id(value.get("target_tree"), "/receipt/target_tree")
    if _object_tree(project, commit) != tree:
        raise _error("BASELINE_BINDING", "/receipt/target_tree", "Baseline target is not the committed tree.")
    artifact_values = _closed(value.get("artifacts"), set(_BASELINE_ARTIFACT_KEYS), "/receipt/artifacts", "BASELINE_BINDING")
    artifacts = {name: _digest(artifact_values[name], "/receipt/artifacts/" + name) for name in _BASELINE_ARTIFACT_KEYS}
    stored_fingerprints = _fingerprint_digests(value.get("fingerprints"), "/receipt/fingerprints")
    current_fingerprints = _fingerprint_digests(fingerprints, "/fingerprints")
    if stored_fingerprints != current_fingerprints:
        raise _error("BASELINE_FINGERPRINT", "/receipt/fingerprints", "Baseline fingerprints are incompatible.")
    frozen = _freeze(_plain(value))
    return ValidatedBaseline(
        repository, commit, tree, selected_module,
        artifacts["technical_test_inventory_sha256"], artifacts["managed_behavior_context_sha256"],
        artifacts["effective_document_sha256"], artifacts["effective_bundle_receipt_sha256"],
        _freeze(stored_fingerprints), artifact_sha256(value), frozen,
    )


def choose_run_mode(target: Mapping[str, Any], baseline: ValidatedBaseline | None = None) -> Literal["FULL", "CHANGE_SET"]:
    if not isinstance(baseline, ValidatedBaseline):
        return "FULL"
    try:
        value = _mapping(target, "/target")
        base = _mapping(value.get("base"), "/target/base")
        compatible = (
            value.get("input_kind") == "git_range"
            and value.get("repository_id") == baseline.repository_id
            and value.get("selected_module") == baseline.selected_module
            and base.get("commit") == baseline.target_commit
            and base.get("tree") == baseline.target_tree
            and _fingerprint_digests(value.get("fingerprints"), "/target/fingerprints") == dict(baseline.fingerprints)
        )
    except FlowError:
        return "FULL"
    return "CHANGE_SET" if compatible else "FULL"


def _install(root: Path, relative: PurePosixPath, value: Mapping[str, Any]) -> bool:
    payload = canonical_bytes(value)
    target = root / Path(*relative.parts)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        created = True
    except FileExistsError:
        created = False
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", "/storage", "Baseline artifact could not be created.") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        actual = target.read_bytes()
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", "/storage", "Baseline artifact could not be read back.") from None
    if actual != payload:
        raise _error("FLOW_CONFLICT", "/storage", "A different immutable baseline artifact already exists.")
    return created


def _advancement(
    status: str,
    predecessor: str | None,
    successor: str | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    value = {
        "schema_version": "1.0.0", "artifact": "baseline-advancement", "status": status,
        "predecessor_baseline_sha256": predecessor, "successor_baseline_sha256": successor,
        "successor_baseline_receipt": None if receipt is None else _plain(receipt),
    }
    _validate_schema(value, "baseline-advancement.schema.json", "/baseline_advancement")
    return _freeze(value)


def _eligible(acceptance: Mapping[str, Any]) -> bool:
    return (
        acceptance.get("classification_verdict") == "ПРИНЯТО"
        and acceptance.get("test_case_review_verdict") in {"ПРИНЯТО", "AUTO_FIX_APPLIED"}
        and acceptance.get("autotest_review_verdict") == "ПРИНЯТО"
        and acceptance.get("trace_verdict") == "PASS"
        and acceptance.get("final_status") in ELIGIBLE_FINAL_STATUSES
        and acceptance.get("source_drift") is False
    )


def advance_baseline(
    run: Mapping[str, Any],
    terminal_receipt: StoredArtifact,
    predecessor: ValidatedBaseline | None = None,
) -> Mapping[str, Any]:
    terminal = _read_stored(terminal_receipt, "/terminal_receipt")
    _validate_schema(terminal, "terminal-run-receipt.schema.json", "/terminal_receipt")
    acceptance = _mapping(terminal.get("acceptance"), "/terminal_receipt/acceptance")
    predecessor_digest = predecessor.receipt_sha256 if isinstance(predecessor, ValidatedBaseline) else None
    if acceptance.get("source_drift") is True:
        return _advancement("INELIGIBLE", predecessor_digest)
    if not _eligible(acceptance):
        return _advancement("INELIGIBLE", predecessor_digest)
    run_value = _closed(run, {"project", "baseline_root"}, "/run")
    project = run_value.get("project")
    baseline_root = run_value.get("baseline_root")
    if not isinstance(project, Path) or not isinstance(baseline_root, Path):
        raise _error("FEATURE_FLOW_INPUT", "/run", "Run must contain Path project and baseline_root values.")
    change = _mapping(terminal.get("change_input"), "/terminal_receipt/change_input")
    input_kind = change.get("input_kind")
    if input_kind in {"git_worktree", "patch_manifest"}:
        return _advancement("PROVISIONAL", predecessor_digest)
    try:
        repository = _repository_id(project)
    except FlowError:
        return _advancement("PROVISIONAL", predecessor_digest)
    if terminal.get("repository_id") != repository:
        return _advancement("INELIGIBLE", predecessor_digest)
    target = change.get("target")
    if not isinstance(target, Mapping):
        return _advancement("INELIGIBLE", predecessor_digest)
    try:
        target_commit = _git_id(target.get("commit"), "/terminal_receipt/change_input/target/commit")
        target_tree = _git_id(target.get("tree"), "/terminal_receipt/change_input/target/tree")
        if _object_tree(project, target_commit) != target_tree:
            return _advancement("INELIGIBLE", predecessor_digest)
    except FlowError:
        return _advancement("INELIGIBLE", predecessor_digest)

    run_mode = terminal.get("run_mode")
    if run_mode == "FULL":
        if predecessor is not None or input_kind != "git_head":
            return _advancement("INELIGIBLE", predecessor_digest)
        try:
            exact = (
                _git(project, "status", "--porcelain=v1", "-z") == ""
                and _git(project, "rev-parse", "HEAD") == target_commit
                and _git(project, "rev-parse", "HEAD^{tree}") == target_tree
            )
        except FlowError:
            exact = False
        if not exact:
            return _advancement("PROVISIONAL", predecessor_digest)
    elif run_mode == "CHANGE_SET":
        if not isinstance(predecessor, ValidatedBaseline) or input_kind != "git_range":
            return _advancement("INELIGIBLE", predecessor_digest)
        try:
            terminal_fingerprints = _fingerprint_digests(terminal.get("fingerprints"), "/terminal_receipt/fingerprints")
        except FlowError:
            return _advancement("INELIGIBLE", predecessor_digest)
        if predecessor.repository_id != repository or predecessor.selected_module != terminal.get("selected_module") or dict(predecessor.fingerprints) != terminal_fingerprints:
            return _advancement("INELIGIBLE", predecessor_digest)
        base = change.get("base")
        if not isinstance(base, Mapping) or base.get("commit") != predecessor.target_commit or base.get("tree") != predecessor.target_tree:
            return _advancement("INELIGIBLE", predecessor_digest)
        try:
            exact = (
                _object_tree(project, predecessor.target_commit) == predecessor.target_tree
                and _git(project, "status", "--porcelain=v1", "-z") == ""
                and _git(project, "rev-parse", "HEAD") == target_commit
                and _git(project, "rev-parse", "HEAD^{tree}") == target_tree
            )
        except FlowError:
            exact = False
        if not exact:
            return _advancement("INELIGIBLE", predecessor_digest)
    else:
        return _advancement("INELIGIBLE", predecessor_digest)

    terminal_artifacts = _closed(terminal.get("artifacts"), {name + "_sha256" for name in (*_PREFIX_KEYS, *_TAIL_KEYS)}, "/terminal_receipt/artifacts", "BASELINE_BINDING")
    baseline = {
        "schema_version": "1.0.0", "artifact": "feature-baseline-receipt",
        "predecessor_baseline_sha256": predecessor_digest, "run_mode": run_mode,
        "repository_id": repository, "target_commit": target_commit, "target_tree": target_tree,
        "selected_module": terminal["selected_module"], "analytics_sha256": terminal["analytics_sha256"],
        "artifacts": {
            name: terminal_receipt.sha256 if name == "terminal_run_receipt_sha256" else terminal_artifacts[name]
            for name in _BASELINE_ARTIFACT_KEYS
        },
        "fingerprints": _fingerprint_digests(terminal.get("fingerprints"), "/terminal_receipt/fingerprints"),
    }
    _validate_schema(baseline, "feature-baseline-receipt.schema.json", "/successor_baseline_receipt")
    successor_digest = artifact_sha256(baseline)
    _install(baseline_root, PurePosixPath("receipts") / f"{successor_digest[7:]}.json", baseline)
    if predecessor_digest is None:
        key = hashlib.sha256(canonical_bytes({"repository_id": repository, "selected_module": terminal["selected_module"]})).hexdigest()
        link_path = PurePosixPath("links/initial") / f"{key}.json"
    else:
        link_path = PurePosixPath("links/predecessors") / f"{predecessor_digest[7:]}.json"
    edge = {
        "schema_version": "1.0.0", "artifact": "baseline-predecessor-link",
        "predecessor_baseline_sha256": predecessor_digest, "successor_baseline_sha256": successor_digest,
    }
    try:
        created = _install(baseline_root, link_path, edge)
    except FlowError as caught:
        if caught.code != "FLOW_CONFLICT":
            raise
        return _advancement("BASELINE_CONFLICT", predecessor_digest)
    return _advancement("ADVANCED" if created else "IDEMPOTENT", predecessor_digest, successor_digest, baseline)
