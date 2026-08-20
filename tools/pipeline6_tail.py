"""Small, immutable Pipeline 6 technical-tail facade.

The semantic prefix remains authoritative in :mod:`tools.feature_flow`; this
module only turns its closed handoff into the existing review/automation tail.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.flow_artifacts import FlowError, StoredArtifact, artifact_sha256, canonical_bytes, write_create_only
from tools.baseline_lifecycle import advance_baseline, build_terminal_run_receipt, validate_baseline_receipt
from tools.automation_validation import required_symbol_pairs
from tools.build_trace_document import build_trace
from tools.orchestrate_test_case_revision import finalize_orchestration, orchestrate_revision
from tools.publish_test_case_bundle import Receipt
from tools.run_tests import run_tests_v3
from tools.schema_validation import schema_diagnostics
from tools.skillsrc_manifest import SkillsrcError, load_skillsrc
from tools.test_classification import build_source_inventories, validate_technical_test_evidence
from tools.trace_check import check as check_trace
from tools.json_cli import JsonArgumentParser
from tools.feature_flow import _git, _git_blob_reader, _git_provenance


_ROOT = Path(__file__).resolve().parents[1]
_KINDS = frozenset({
    "CLASSIFY_TECHNICAL_TESTS", "REVIEW_TECHNICAL_CLASSIFICATION", "REVIEW_CANDIDATE_DOCUMENT",
    "GENERATE_AUTOMATION", "REVIEW_AUTOMATION", "COMPLETE", "PROVISIONAL", "BLOCKED",
})
_RECORD = re.compile(r"^(?P<n>[0-9]{6})-(?P<label>[a-z0-9-]+)\.json$")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class PipelineTailAction:
    """The closed public tail control surface; values are recursive snapshots."""

    kind: str
    artifact: Mapping[str, Any] | None
    record_path: Path | None
    diagnostics: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("PipelineTailAction kind is outside the closed public enum.")
        object.__setattr__(self, "artifact", None if self.artifact is None else _freeze(_plain(self.artifact)))
        object.__setattr__(self, "diagnostics", tuple(_freeze(_plain(row)) for row in self.diagnostics))


def _blocked(code: str, path: str, message: str) -> PipelineTailAction:
    return PipelineTailAction("BLOCKED", None, None, ({"code": code, "path": path, "message": message},))


def _load(path: Path, code: str, pointer: str) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FlowError(code, pointer, "Immutable JSON readback is unavailable.") from None
    if not isinstance(value, Mapping) or raw != canonical_bytes(value):
        raise FlowError(code, pointer, "Immutable JSON is not canonical.")
    return value


def _binding(run_root: Path, row: Any, pointer: str) -> tuple[Mapping[str, Any], StoredArtifact]:
    if not isinstance(row, Mapping) or set(row) != {"path", "sha256"} or not isinstance(row.get("path"), str) or not isinstance(row.get("sha256"), str):
        raise FlowError("FEATURE_FLOW_INPUT", pointer, "A closed artifact binding is required.")
    candidate = PurePosixPath(row["path"])
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in row["path"]:
        raise FlowError("FEATURE_FLOW_INPUT", pointer, "Artifact binding path is unsafe.")
    target = (run_root / Path(*candidate.parts)).resolve(strict=True)
    target.relative_to(run_root.resolve(strict=True))
    value = _load(target, "FLOW_ATOMIC_WRITE", pointer)
    actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != row["sha256"] or artifact_sha256(value) != actual:
        raise FlowError("BASELINE_BINDING", pointer, "Artifact binding does not match readback.")
    return value, StoredArtifact(target, target.read_bytes(), actual)


def _store(run_root: Path, value: Mapping[str, Any]) -> StoredArtifact:
    digest = artifact_sha256(value)
    return write_create_only(run_root, PurePosixPath("artifacts") / f"{digest[7:]}.json", value)


def _stored_tail(run_root: Path, value: Mapping[str, Any]) -> StoredArtifact:
    """Persist the safe deterministic projection of the legacy runner envelope."""
    projection = _plain(value)
    environment = projection.get("environment")
    if isinstance(environment, Mapping):
        projection["environment"] = {"status": environment.get("status"), "interpreter": None,
            "interpreter_path": None, "working_dir": ".", "missing": environment.get("missing")}
    projection["raw_output_excerpt"] = None
    _schema(projection, "run-tests-output.schema.json", "/run_result")
    payload = canonical_bytes(projection)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    target = run_root / "artifacts" / f"{digest[7:]}.json"
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, target)
    except FileExistsError:
        if target.read_bytes() != payload:
            raise FlowError("FLOW_CONFLICT", "/run_result", "A different immutable runner projection already exists.") from None
    except OSError:
        raise FlowError("FLOW_ATOMIC_WRITE", "/run_result", "Runner projection could not be persisted.") from None
    finally:
        temporary.unlink(missing_ok=True)
    if target.read_bytes() != payload:
        raise FlowError("FLOW_ATOMIC_WRITE", "/run_result", "Runner projection readback failed.")
    return StoredArtifact(target.resolve(), payload, digest)


def _effective_evidence(source: Mapping[str, Any], classification: Mapping[str, Any], review: Mapping[str, Any]) -> Mapping[str, Any]:
    inventory = source["artifacts"]["technical_test_inventory"]
    candidate = classification["artifacts"]["classification"]
    accepted = review["artifacts"]["classification_review"]
    value = {"technical_test_inventory_sha256": source["artifacts"]["technical_test_inventory_sha256"],
        "technical_test_classification_sha256": artifact_sha256(candidate), "technical_test_review_sha256": artifact_sha256(accepted),
        "files": _plain(inventory["files"]), "symbols": _plain(inventory["symbols"]), "classifications": _plain(candidate["classifications"])}
    value["effective_technical_evidence_sha256"] = artifact_sha256(value)
    _schema(value, "effective-technical-evidence.schema.json", "/effective_technical_evidence")
    return value


def _fingerprint_projection(registries: Mapping[str, Any]) -> Mapping[str, str]:
    names = ("pipeline_contract", "policy_bundle", "tool_bundle", "schema_bundle")
    if set(registries) != set(names):
        raise FlowError("BASELINE_FINGERPRINT", "/fingerprints", "Final fingerprint registries are not closed.")
    result: dict[str, str] = {}
    for name in names:
        row = registries[name]
        if not isinstance(row, Mapping) or set(row) != {"sha256", "files"} or row.get("sha256") != artifact_sha256({"files": row.get("files")}):
            raise FlowError("BASELINE_FINGERPRINT", "/fingerprints", "Final fingerprint registry is not canonical.")
        result[name + "_sha256"] = row["sha256"]
    return result


def _current_fingerprint_registries() -> Mapping[str, Any]:
    """Materialize the only fingerprints this tail is allowed to accept."""
    from tools.contract_check import materialize_fingerprint_registries
    try:
        contract = json.loads((_ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FlowError("BASELINE_FINGERPRINT", "/contracts/pipeline.json", "Pipeline contract is unavailable.") from None
    if not isinstance(contract, dict):
        raise FlowError("BASELINE_FINGERPRINT", "/contracts/pipeline.json", "Pipeline contract is invalid.")
    return materialize_fingerprint_registries(_ROOT, contract)


def _terminal_change_input(change: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drop source-bearing change rows before durable terminal construction."""
    kind = change.get("input_kind")
    repository = change.get("repository_id")
    if kind == "git_range":
        base, target = change.get("base"), change.get("target")
        if not isinstance(base, Mapping) or not isinstance(target, Mapping):
            raise FlowError("FEATURE_FLOW_INPUT", "/prefix/change_input", "Git range identity is unavailable.")
        return {"input_kind": kind, "repository_id": repository,
                "base": {"commit": base.get("commit"), "tree": base.get("tree")},
                "target": {"commit": target.get("commit"), "tree": target.get("tree")}}
    if kind == "git_worktree":
        base, target = change.get("base"), change.get("target")
        if not isinstance(base, Mapping) or not isinstance(target, Mapping):
            raise FlowError("FEATURE_FLOW_INPUT", "/prefix/change_input", "Worktree identity is unavailable.")
        return {"input_kind": kind, "repository_id": repository,
                "base": {"commit": base.get("commit"), "tree": base.get("tree")},
                "target_snapshot_sha256": target.get("snapshot_sha256")}
    if kind == "patch_manifest":
        base, target = change.get("base"), change.get("target")
        if not isinstance(base, Mapping) or not isinstance(target, Mapping):
            raise FlowError("FEATURE_FLOW_INPUT", "/prefix/change_input", "Patch identity is unavailable.")
        return {"input_kind": kind, "repository_id": repository,
                "base_snapshot_sha256": base.get("snapshot_sha256"), "target_snapshot_sha256": target.get("snapshot_sha256")}
    raise FlowError("FEATURE_FLOW_INPUT", "/prefix/change_input", "Change input kind is outside the terminal union.")


def _verify_full_source_snapshot(project: Path, flow: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    """Require every FULL tail to use its exact frozen source projection."""
    try:
        skillsrc_path = project / ".skillsrc"
        skillsrc_bytes = skillsrc_path.read_bytes()
        repository = flow.get("repository_id")
        revision = flow.get("source_revision")
        reader = None
        if repository is not None:
            provenance = _git_provenance(project)
            if not isinstance(provenance, Mapping) or provenance.get("repository_id") != repository:
                raise ValueError()
            if not isinstance(revision, Mapping) or not isinstance(revision.get("commit"), str) or not isinstance(revision.get("tree"), str):
                raise ValueError()
            if _git(project, "rev-parse", f"{revision['commit']}^{{tree}}") != revision["tree"]:
                raise ValueError()
            if provenance.get("commit") != revision["commit"] or provenance.get("tree") != revision["tree"]:
                raise ValueError()
            if revision.get("durability") == "DURABLE":
                if provenance.get("status"):
                    raise ValueError()
                reader = _git_blob_reader(project, revision["commit"])
            elif revision.get("durability") != "PROVISIONAL":
                raise ValueError()
        inventories = build_source_inventories(project, load_skillsrc(skillsrc_path), flow.get("selected_module"), (), snapshot_reader=reader)
    except (OSError, SkillsrcError, TypeError, ValueError):
        raise FlowError("CHANGE_SOURCE_DRIFT", "/project", "FULL source snapshot could not be rebuilt.") from None
    envelope = {
        "schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {
            "technical_test_inventory": _plain(inventories.technical_test_inventory), "technical_test_inventory_sha256": inventories.technical_test_inventory_sha256,
            "authorized_behavior_sources": _plain(inventories.authorized_behavior_sources), "authorized_behavior_sources_sha256": inventories.authorized_behavior_sources_sha256,
        }, "warnings": [],
    }
    snapshot = artifact_sha256({
        "selected_module": flow.get("selected_module"), "skillsrc_sha256": "sha256:" + hashlib.sha256(skillsrc_bytes).hexdigest(),
        "authorized_behavior_sources_sha256": inventories.authorized_behavior_sources_sha256,
        "technical_test_inventory_sha256": inventories.technical_test_inventory_sha256,
    })
    if flow.get("skillsrc_sha256") != "sha256:" + hashlib.sha256(skillsrc_bytes).hexdigest() or flow.get("source_snapshot_sha256") != snapshot or _plain(source) != envelope:
        raise FlowError("CHANGE_SOURCE_DRIFT", "/project", "FULL source snapshot no longer matches the frozen prefix.")


def _records(run_root: Path) -> list[tuple[Path, Mapping[str, Any]]]:
    root = run_root / "pipeline6-tail" / "records"
    if not root.exists():
        return []
    result: list[tuple[Path, Mapping[str, Any]]] = []
    for path in sorted(root.glob("*.json")):
        match = _RECORD.fullmatch(path.name)
        if match is None:
            raise FlowError("FEATURE_FLOW_INPUT", "/records", "Tail record name is not canonical.")
        result.append((path.resolve(), _load(path, "FEATURE_FLOW_INPUT", "/records")))
    if [int(_RECORD.fullmatch(path.name).group("n")) for path, _ in result] != list(range(1, len(result) + 1)):
        raise FlowError("FEATURE_FLOW_INPUT", "/records", "Tail records have a gap or branch.")
    return result


def _record_path(run_root: Path, ordinal: int, kind: str) -> Path:
    return run_root / "pipeline6-tail" / "records" / f"{ordinal:06d}-{kind.lower().replace('_', '-')}.json"


def _action(run_root: Path, kind: str, ordinal: int, prompt: Mapping[str, Any]) -> PipelineTailAction:
    return PipelineTailAction(kind, {"schema_version": "1.0.0", "artifact": "pipeline6-tail-action", "kind": kind, "prompt": prompt}, _record_path(run_root, ordinal, kind))


def _need_record(run_root: Path, records: list[tuple[Path, Mapping[str, Any]]], kind: str, prompt: Mapping[str, Any], supplied: Path | None) -> tuple[PipelineTailAction | None, Mapping[str, Any] | None]:
    ordinal = len(records) + 1
    expected = _record_path(run_root, ordinal, kind).resolve()
    if supplied is not None:
        if supplied.resolve() != expected or not expected.is_file():
            raise FlowError("FEATURE_FLOW_INPUT", "/recorded_artifact", "Recorded artifact is not the exact next tail record path.")
        return None, _load(expected, "FEATURE_FLOW_INPUT", "/recorded_artifact")
    return _action(run_root, kind, ordinal, prompt), None


def _schema(value: Mapping[str, Any], name: str, pointer: str) -> None:
    if schema_diagnostics(value, _ROOT / "schemas" / name, _ROOT):
        raise FlowError("FEATURE_FLOW_INPUT", pointer, "Recorded artifact does not satisfy its closed schema.")


def _receipt(value: Mapping[str, Any]) -> Receipt:
    fields = ("document_id", "revision", "csv_profile", "json_path", "markdown_path", "csv_path", "document_sha256", "markdown_sha256", "csv_sha256")
    if set(value) != set(fields):
        raise FlowError("BASELINE_BINDING", "/effective_bundle_receipt", "Effective bundle receipt is not closed.")
    return Receipt(**{name: value[name] for name in fields})


def _manifest(run_root: Path, prefix: Mapping[str, Any], records: list[tuple[Path, Mapping[str, Any]]], bindings: Mapping[str, Any], classification: Mapping[str, Any], review: Mapping[str, Any], evidence: Mapping[str, Any], validation: Mapping[str, Any], fingerprints: Mapping[str, Any]) -> StoredArtifact:
    flow, _ = _binding(run_root, bindings.get("flow_input"), "/prefix/flow_input")
    source, source_stored = _binding(run_root, bindings.get("source_inventory"), "/prefix/source_inventory")
    scope, scope_stored = _binding(run_root, bindings.get("change_scope_receipt"), "/prefix/change_scope_receipt")
    context, context_stored = _binding(run_root, bindings.get("context_envelope"), "/prefix/context_envelope")
    changed, changed_stored = _binding(run_root, bindings.get("changed_behavior_context"), "/prefix/changed_behavior_context")
    context_receipt, receipt_stored = _binding(run_root, bindings.get("behavior_context_receipt"), "/prefix/behavior_context_receipt")
    mode, repository = flow.get("run_mode"), flow.get("repository_id")
    snapshot = flow.get("source_snapshot_sha256")
    if mode not in {"FULL", "CHANGE_SET"} or (repository is not None and not isinstance(repository, str)) or not isinstance(snapshot, str):
        raise FlowError("FEATURE_FLOW_INPUT", "/prefix/flow_input", "Flow input does not bind run metadata.")
    if mode == "FULL":
        full_change = bindings.get("change_input")
        if isinstance(full_change, Mapping):
            raw_change, _ = _binding(run_root, full_change, "/prefix/change_input")
            if raw_change.get("input_kind") in {"git_worktree", "patch_manifest"}:
                change = _terminal_change_input(raw_change)
            else:
                full_change = None
        if repository is None:
            if isinstance(full_change, Mapping):
                raise FlowError("FEATURE_FLOW_INPUT", "/prefix/change_input", "Local FULL flow cannot bind a change input.")
            change = None
        elif not isinstance(full_change, Mapping):
            revision = flow.get("source_revision")
            if not isinstance(revision, Mapping):
                raise FlowError("FEATURE_FLOW_INPUT", "/prefix/flow_input", "FULL flow input lacks committed target identity.")
            change = {"input_kind": "git_head", "repository_id": repository, "target": {"commit": revision.get("commit"), "tree": revision.get("tree")}}
    else:
        raw_change, _ = _binding(run_root, bindings.get("change_input"), "/prefix/change_input")
        change = _terminal_change_input(raw_change)
    rows = {
        "technical_test_inventory": source_stored, "authorized_behavior_sources": source_stored,
        "change_scope_receipt": scope_stored, "managed_behavior_context": context_stored,
        "changed_behavior_context": changed_stored, "behavior_source_accounting": context_stored,
        "behavior_context_receipt": receipt_stored, "technical_test_classification": _store(run_root, classification),
        "classification_review": _store(run_root, review), "effective_technical_evidence": _store(run_root, evidence),
        "validation_report": _store(run_root, validation),
    }
    ledger = {"schema_version": "1.0.0", "artifact": "prefix-ledger", "feature_flow_prefix_sha256": artifact_sha256(prefix), "tail_record_sha256s": [artifact_sha256(value) for _, value in records], "repository_id": repository,
        "selected_module": flow.get("selected_module"), "run_mode": mode, "change_input": change,
        "analytics_sha256": flow.get("analytics_sha256"), "source_snapshot_sha256": snapshot, "source_drift": False, "fingerprints": fingerprints,
        "artifacts": {name: {"path": stored.path.relative_to(run_root).as_posix(), "sha256": stored.sha256} for name, stored in rows.items()}}
    return write_create_only(run_root, PurePosixPath("manifest") / "prefix-ledger.json", ledger)


def _receipt_named(run_root: Path, digest: Any, pointer: str) -> StoredArtifact:
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise FlowError("BASELINE_BINDING", pointer, "Receipt-named artifact digest is invalid.")
    try:
        root = run_root.resolve(strict=True)
        path = (root / "artifacts" / f"{digest[7:]}.json").resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError):
        raise FlowError("BASELINE_BINDING", pointer, "Receipt-named artifact is unavailable.") from None
    value = _load(path, "BASELINE_BINDING", pointer)
    if artifact_sha256(value) != digest:
        raise FlowError("BASELINE_BINDING", pointer, "Receipt-named artifact does not match its digest.")
    return StoredArtifact(path, path.read_bytes(), digest)


def _resume_terminal(run_root: Path, prefix: Mapping[str, Any], records: list[tuple[Path, Mapping[str, Any]]], terminal: Mapping[str, Any], terminal_stored: StoredArtifact) -> None:
    """Re-derive the complete terminal closure from this run's manifest and payloads."""
    try:
        manifest_path = (run_root / "manifest" / "prefix-ledger.json").resolve(strict=True)
        manifest_path.relative_to(run_root.resolve(strict=True))
    except (OSError, ValueError):
        raise FlowError("BASELINE_BINDING", "/completion/manifest", "Current run manifest is unavailable.") from None
    manifest = _load(manifest_path, "BASELINE_BINDING", "/completion/manifest")
    manifest_stored = StoredArtifact(manifest_path, manifest_path.read_bytes(), artifact_sha256(manifest))
    if manifest.get("feature_flow_prefix_sha256") != artifact_sha256(prefix) or manifest.get("tail_record_sha256s") != [artifact_sha256(row) for _, row in records]:
        raise FlowError("BASELINE_BINDING", "/completion/manifest", "Manifest does not bind the current feature-flow prefix and ordered tail records.")
    if terminal.get("prefix_ledger_sha256") != manifest_stored.sha256:
        raise FlowError("BASELINE_BINDING", "/completion/terminal_run_receipt", "Terminal receipt does not bind the current manifest.")
    tail_names = ("effective_document", "effective_bundle_receipt", "automation_artifact", "autotest_review", "run_result", "trace_document", "trace_audit", "orchestrator_output")
    artifacts = terminal.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise FlowError("BASELINE_BINDING", "/completion/terminal_run_receipt", "Terminal artifacts are unavailable.")
    tails = {name: None if artifacts.get(name + "_sha256") is None else _receipt_named(run_root, artifacts.get(name + "_sha256"), f"/completion/terminal_run_receipt/artifacts/{name}") for name in tail_names}
    try:
        rebuilt = build_terminal_run_receipt(manifest_stored, tails)
    except FlowError:
        raise
    if _plain(rebuilt) != _plain(terminal) or terminal_stored.sha256 != artifact_sha256(terminal):
        raise FlowError("BASELINE_BINDING", "/completion/terminal_run_receipt", "Terminal receipt does not bind this run's closed manifest and payloads.")


def _completed(run_root: Path, baseline_root: Path, prefix: Mapping[str, Any], records: list[tuple[Path, Mapping[str, Any]]], fingerprints: Mapping[str, Any]) -> PipelineTailAction | None:
    """Reopen a complete immutable tail instead of running nondeterministic execution again."""
    path = run_root / "pipeline6-tail" / "completion.json"
    if not path.exists():
        return None
    value = _load(path, "FEATURE_FLOW_INPUT", "/completion")
    required = {"schema_version", "artifact", "status", "prefix_ledger_sha256", "record_sha256s", "terminal_run_receipt", "baseline_advancement"}
    if set(value) != required or value.get("schema_version") != "1.0.0" or value.get("artifact") != "pipeline6-tail-completion" or value.get("status") not in {"COMPLETE", "PROVISIONAL"}:
        raise FlowError("FEATURE_FLOW_INPUT", "/completion", "Tail completion is not a closed complete record.")
    if value.get("prefix_ledger_sha256") != artifact_sha256(prefix) or value.get("record_sha256s") != [artifact_sha256(row) for _, row in records]:
        raise FlowError("FEATURE_FLOW_INPUT", "/completion", "Tail completion does not bind the current prefix and records.")
    terminal_row = value.get("terminal_run_receipt")
    terminal, terminal_stored = _binding(run_root, terminal_row, "/completion/terminal_run_receipt")
    _schema(terminal, "terminal-run-receipt.schema.json", "/completion/terminal_run_receipt")
    projection = _fingerprint_projection(fingerprints)
    manifest_path = run_root / "manifest" / "prefix-ledger.json"
    manifest = _load(manifest_path, "BASELINE_BINDING", "/completion/manifest")
    if _plain(manifest.get("fingerprints")) != _plain(fingerprints) or _plain(terminal.get("fingerprints")) != _plain(projection):
        raise FlowError("BASELINE_FINGERPRINT", "/completion", "Completion fingerprints do not bind current Pipeline 6 bytes.")
    advancement = value.get("baseline_advancement")
    if not isinstance(advancement, Mapping):
        raise FlowError("FEATURE_FLOW_INPUT", "/completion/baseline_advancement", "Tail completion lacks a durable baseline advancement.")
    _schema(advancement, "baseline-advancement.schema.json", "/completion/baseline_advancement")
    if value["status"] == "PROVISIONAL":
        if advancement.get("status") != "PROVISIONAL" or advancement.get("successor_baseline_sha256") is not None or advancement.get("successor_baseline_receipt") is not None:
            raise FlowError("FEATURE_FLOW_INPUT", "/completion/baseline_advancement", "Provisional completion cannot claim a successor baseline.")
        bindings = prefix.get("artifacts")
        if not isinstance(bindings, Mapping):
            raise FlowError("FEATURE_FLOW_INPUT", "/feature-flow/prefix-ledger/artifacts", "Provisional completion lacks flow input binding.")
        flow, _ = _binding(run_root, bindings.get("flow_input"), "/prefix/flow_input")
        expected = flow.get("baseline_receipt_sha256")
        if (flow.get("run_mode") == "FULL" and advancement.get("predecessor_baseline_sha256") is not None) or (
            flow.get("run_mode") == "CHANGE_SET" and advancement.get("predecessor_baseline_sha256") != expected
        ):
            raise FlowError("BASELINE_BINDING", "/completion/baseline_advancement/predecessor_baseline_sha256", "Provisional predecessor does not bind the frozen flow input.")
        _resume_terminal(run_root, prefix, records, terminal, terminal_stored)
        completion_stored = StoredArtifact(path.resolve(), path.read_bytes(), artifact_sha256(value))
        return PipelineTailAction("PROVISIONAL", {"completion": {"path": completion_stored.path.relative_to(run_root.resolve()).as_posix(), "sha256": completion_stored.sha256}, "terminal_run_receipt": {"path": terminal_stored.path.relative_to(run_root.resolve()).as_posix(), "sha256": terminal_stored.sha256}, "baseline_advancement": _plain(advancement)}, None)
    if advancement.get("status") not in {"ADVANCED", "IDEMPOTENT"}:
        raise FlowError("FEATURE_FLOW_INPUT", "/completion/baseline_advancement", "Tail completion lacks a durable baseline advancement.")
    successor = advancement.get("successor_baseline_sha256")
    predecessor = advancement.get("predecessor_baseline_sha256")
    receipt_value = advancement.get("successor_baseline_receipt")
    try:
        baseline_base = baseline_root.resolve(strict=True)
        receipt = (baseline_base / "receipts" / f"{successor[7:]}.json").resolve(strict=True)
        receipt.relative_to(baseline_base)
    except (OSError, ValueError):
        raise FlowError("BASELINE_BINDING", "/completion/successor_baseline", "Successor baseline receipt is unavailable.") from None
    baseline_value = _load(receipt, "BASELINE_BINDING", "/completion/successor_baseline")
    _schema(baseline_value, "feature-baseline-receipt.schema.json", "/completion/successor_baseline")
    if artifact_sha256(baseline_value) != successor:
        raise FlowError("BASELINE_BINDING", "/completion/successor_baseline", "Successor baseline receipt does not match completion.")
    if _plain(baseline_value.get("fingerprints")) != _plain(projection):
        raise FlowError("BASELINE_FINGERPRINT", "/completion/successor_baseline", "Successor baseline fingerprints do not bind current Pipeline 6 bytes.")
    if _plain(baseline_value) != _plain(receipt_value) or baseline_value.get("predecessor_baseline_sha256") != predecessor or baseline_value.get("artifacts", {}).get("terminal_run_receipt_sha256") != terminal_stored.sha256:
        raise FlowError("BASELINE_BINDING", "/completion/successor_baseline", "Successor baseline receipt does not bind completion identity.")
    if predecessor is None:
        key = hashlib.sha256(canonical_bytes({"repository_id": terminal["repository_id"], "selected_module": terminal["selected_module"]})).hexdigest()
        relative = PurePosixPath("links") / "initial" / f"{key}.json"
    else:
        relative = PurePosixPath("links") / "predecessors" / f"{predecessor[7:]}.json"
    try:
        link = (baseline_base / Path(*relative.parts)).resolve(strict=True)
        link.relative_to(baseline_base)
    except (OSError, ValueError):
        raise FlowError("BASELINE_BINDING", "/completion/baseline_link", "Baseline link is unavailable.") from None
    edge = _load(link, "BASELINE_BINDING", "/completion/baseline_link")
    if set(edge) != {"schema_version", "artifact", "predecessor_baseline_sha256", "successor_baseline_sha256"} or edge.get("schema_version") != "1.0.0" or edge.get("artifact") != "baseline-predecessor-link" or edge.get("predecessor_baseline_sha256") != predecessor or edge.get("successor_baseline_sha256") != successor:
        raise FlowError("BASELINE_BINDING", "/completion/baseline_link", "Baseline link does not match completion identity.")
    _resume_terminal(run_root, prefix, records, terminal, terminal_stored)
    completion_stored = StoredArtifact(path.resolve(), path.read_bytes(), artifact_sha256(value))
    return PipelineTailAction("COMPLETE", {"completion": {"path": completion_stored.path.relative_to(run_root.resolve()).as_posix(), "sha256": completion_stored.sha256}, "terminal_run_receipt": {"path": terminal_stored.path.relative_to(run_root.resolve()).as_posix(), "sha256": terminal_stored.sha256}, "baseline_advancement": _plain(advancement)}, None)


def advance_pipeline6_tail(
    project: Path, run_root: Path, baseline_root: Path, baseline_receipt: Path | None = None,
    recorded_artifact: Path | None = None, provider_resolver: Any | None = None,
    adapter_registry: Any | None = None, *, fingerprint_registries: Mapping[str, Any] | None = None,
) -> PipelineTailAction:
    """Return the one next public action, using only immutable prefix readback."""
    try:
        if not all(isinstance(path, Path) for path in (project, run_root, baseline_root)):
            raise FlowError("FEATURE_FLOW_INPUT", "/input", "Project, run root, and baseline root must be paths.")
        current_fingerprints = _current_fingerprint_registries()
        if fingerprint_registries is None or _plain(fingerprint_registries) != _plain(current_fingerprints):
            return _blocked("BASELINE_FINGERPRINT", "/fingerprint_registries", "Final fingerprint registries do not match current Pipeline 6 bytes.")
        prefix = _load(run_root / "feature-flow" / "prefix-ledger.json", "FEATURE_FLOW_INPUT", "/feature-flow/prefix-ledger")
        if prefix.get("artifact") != "feature-flow-prefix-ledger" or prefix.get("status") != "READY_FOR_PIPELINE_TAIL":
            raise FlowError("FEATURE_FLOW_INPUT", "/feature-flow/prefix-ledger", "A ready semantic-prefix ledger is required.")
        bindings = prefix.get("artifacts")
        if not isinstance(bindings, Mapping):
            raise FlowError("FEATURE_FLOW_INPUT", "/feature-flow/prefix-ledger/artifacts", "Prefix bindings are required.")
        flow, _ = _binding(run_root, bindings.get("flow_input"), "/prefix/flow_input")
        if flow.get("run_mode") == "FULL":
            source, _ = _binding(run_root, bindings.get("source_inventory"), "/prefix/source_inventory")
            _verify_full_source_snapshot(project, flow, source)
        handoff, _ = _binding(run_root, bindings.get("delta_handoff"), "/prefix/delta_handoff")
        records = _records(run_root)
        names = [path.name for path, _ in records]
        for ordinal, name in enumerate(names, 1):
            if not name.startswith(f"{ordinal:06d}-"):
                raise FlowError("FEATURE_FLOW_INPUT", "/records", "Tail record order is not canonical.")
        completed = _completed(run_root, baseline_root, prefix, records, current_fingerprints)
        if completed is not None:
            return completed

        classification = records[0][1] if len(records) > 0 else None
        if classification is None:
            action, classification = _need_record(run_root, records, "CLASSIFY_TECHNICAL_TESTS", {"technical_test_inventory": bindings.get("source_inventory"), "changed_behavior_context": bindings.get("changed_behavior_context")}, recorded_artifact)
            if action is not None: return action
            _store(run_root, classification)
            return advance_pipeline6_tail(project, run_root, baseline_root, baseline_receipt, None, provider_resolver, adapter_registry, fingerprint_registries=fingerprint_registries)
        _schema(classification, "test-classifier-output.schema.json", "/records/1")
        review = records[1][1] if len(records) > 1 else None
        if review is None:
            action, review = _need_record(run_root, records, "REVIEW_TECHNICAL_CLASSIFICATION", {"classification_sha256": artifact_sha256(classification)}, recorded_artifact)
            if action is not None: return action
            _store(run_root, review)
            return advance_pipeline6_tail(project, run_root, baseline_root, baseline_receipt, None, provider_resolver, adapter_registry, fingerprint_registries=fingerprint_registries)
        _schema(review, "test-classifier-reviewer-output.schema.json", "/records/2")
        source, _ = _binding(run_root, bindings.get("source_inventory"), "/prefix/source_inventory")
        context, _ = _binding(run_root, bindings.get("context_envelope"), "/prefix/context_envelope")
        context_artifacts = context.get("artifacts")
        requirements = context_artifacts.get("managed_behavior_context", {}).get("requirements") if isinstance(context_artifacts, Mapping) and isinstance(context_artifacts.get("managed_behavior_context"), Mapping) else None
        if not isinstance(requirements, list) or validate_technical_test_evidence(source, classification, review, requirements, project):
            raise FlowError("BASELINE_BINDING", "/records", "Technical classification evidence is not accepted and complete.")
        unchanged = handoff.get("unchanged_document_selection")
        candidate = handoff.get("candidate_document")
        offset = 2
        validation: Mapping[str, Any] | None = None
        if candidate is not None:
            validation = records[offset][1] if len(records) > offset else None
            if validation is None:
                action, validation = _need_record(run_root, records, "REVIEW_CANDIDATE_DOCUMENT", {"candidate_document": candidate}, recorded_artifact)
                if action is not None: return action
                _store(run_root, validation)
                return advance_pipeline6_tail(project, run_root, baseline_root, baseline_receipt, None, provider_resolver, adapter_registry, fingerprint_registries=fingerprint_registries)
            _schema(validation, "tc-reviewer-output.schema.json", "/records/candidate-review")
            offset += 1
        elif not isinstance(unchanged, Mapping):
            raise FlowError("DOCUMENT_DELTA_BINDING", "/prefix/delta_handoff", "Tail handoff has neither a candidate nor an unchanged selection.")
        automation = records[offset][1] if len(records) > offset else None
        if automation is None:
            prompt = {"effective_document": handoff.get("effective_baseline_document") if candidate is None else candidate}
            action, automation = _need_record(run_root, records, "GENERATE_AUTOMATION", prompt, recorded_artifact)
            if action is not None: return action
            _store(run_root, automation)
            return advance_pipeline6_tail(project, run_root, baseline_root, baseline_receipt, None, provider_resolver, adapter_registry, fingerprint_registries=fingerprint_registries)
        _schema(automation, "tc-to-autotest-output.schema.json", "/records/automation")
        offset += 1
        automation_review = records[offset][1] if len(records) > offset else None
        if automation_review is None:
            action, automation_review = _need_record(run_root, records, "REVIEW_AUTOMATION", {"automation_artifact_sha256": artifact_sha256(automation)}, recorded_artifact)
            if action is not None: return action
            _store(run_root, automation_review)
            return advance_pipeline6_tail(project, run_root, baseline_root, baseline_receipt, None, provider_resolver, adapter_registry, fingerprint_registries=fingerprint_registries)
        _schema(automation_review, "autotest-reviewer-output.schema.json", "/records/automation-review")
        fingerprints = current_fingerprints
        # Run/trace/finalization are deterministic adapter seams.  They are not
        # public actions, because no LLM is permitted to invent their evidence.
        evidence = _effective_evidence(source, classification, review)
        if candidate is not None:
            candidate_value, _ = _binding(run_root, candidate, "/prefix/candidate_document")
            selected = orchestrate_revision(candidate_value, validation, run_root / "pipeline6-tail" / "published", "zephyr-scale-step-row-24-v1")
            if selected.status != "EFFECTIVE_SELECTED" or selected.effective_document is None or selected.effective_bundle_receipt is None:
                return _blocked("FEATURE_FLOW_INPUT", "/records/candidate-review", "Candidate review did not select an effective document.")
            document, bundle, validation_branch = _plain(selected.effective_document), asdict(selected.effective_bundle_receipt), validation
        else:
            document, _ = _binding(run_root, handoff.get("effective_baseline_document"), "/prefix/effective_baseline_document")
            bundle, _ = _binding(run_root, handoff.get("effective_baseline_bundle_receipt"), "/prefix/effective_baseline_bundle_receipt")
            delta, _ = _binding(run_root, handoff.get("delta_application_receipt"), "/prefix/delta_application_receipt")
            selection, _ = _binding(run_root, handoff.get("unchanged_document_selection"), "/prefix/unchanged_document_selection")
            validation_branch = {"schema_version": "1.0.0", "artifact": "unchanged-baseline-tail-branch", "delta_application_receipt": delta, "unchanged_document_selection": selection}
        no_run = automation.get("artifacts", {}).get("automation_status") == "BLOCKED" or not required_symbol_pairs(automation, document)
        if no_run:
            run = None
        else:
            generated = automation.get("artifacts", {}).get("generated_files") if isinstance(automation.get("artifacts"), Mapping) else None
            language = generated[0].get("language") if isinstance(generated, list) and generated else None
            if language not in {"python", "java"}:
                return _blocked("FEATURE_FLOW_INPUT", "/automation", "Generated automation has no supported single language.")
            if adapter_registry is not None and not callable(getattr(adapter_registry, "require", None)):
                return _blocked("FEATURE_FLOW_INPUT", "/adapter_registry", "Execution adapter registry must expose require().")
            run = run_tests_v3(project, language, document, automation, provider_resolver, adapter_registry)
            _schema(run, "run-tests-output.schema.json", "/run_result")
        trace = build_trace(document, automation, run)
        audit = check_trace(trace, document, automation, run)
        orchestration = finalize_orchestration(document, _receipt(bundle), automation, automation_review, run, trace)
        manifest = _manifest(run_root, prefix, records, bindings, classification, review, evidence, validation_branch, fingerprints)
        tails = {"effective_document": _store(run_root, document), "effective_bundle_receipt": _store(run_root, bundle), "automation_artifact": _store(run_root, automation), "autotest_review": _store(run_root, automation_review), "run_result": None if run is None else _stored_tail(run_root, run), "trace_document": _store(run_root, trace), "trace_audit": _store(run_root, audit), "orchestrator_output": _store(run_root, orchestration)}
        terminal = write_create_only(run_root, PurePosixPath("receipts") / "terminal-run-receipt.json", build_terminal_run_receipt(manifest, tails))
        predecessor = None
        if handoff.get("run_mode") == "CHANGE_SET":
            flow, _ = _binding(run_root, bindings.get("flow_input"), "/prefix/flow_input")
            expected = flow.get("baseline_receipt_sha256")
            if not isinstance(baseline_receipt, Path) or not isinstance(expected, str):
                return _blocked("BASELINE_BINDING", "/baseline_receipt", "CHANGE_SET requires the exact predecessor baseline receipt path.")
            expected_path = baseline_root / "receipts" / f"{expected[7:]}.json"
            if baseline_receipt.resolve() != expected_path.resolve():
                return _blocked("BASELINE_BINDING", "/baseline_receipt", "CHANGE_SET receipt path is not the exact baseline receipt path.")
            receipt_value = _load(expected_path, "BASELINE_BINDING", "/baseline_receipt")
            if artifact_sha256(receipt_value) != expected:
                return _blocked("BASELINE_BINDING", "/baseline_receipt", "Predecessor baseline receipt does not match frozen flow identity.")
            predecessor = validate_baseline_receipt(receipt_value, project, str(flow.get("selected_module")), _fingerprint_projection(fingerprints))
        advancement = advance_baseline({"project": project, "baseline_root": baseline_root}, terminal, predecessor)
        if advancement.get("status") not in {"ADVANCED", "IDEMPOTENT", "PROVISIONAL"}:
            return _blocked("BASELINE_BINDING", "/baseline_advancement", "Terminal receipt was not durably advanced as a baseline.")
        status = "PROVISIONAL" if advancement.get("status") == "PROVISIONAL" else "COMPLETE"
        completion = {"schema_version": "1.0.0", "artifact": "pipeline6-tail-completion", "status": status, "prefix_ledger_sha256": artifact_sha256(prefix), "record_sha256s": [artifact_sha256(value) for _, value in records], "terminal_run_receipt": {"path": terminal.path.relative_to(run_root).as_posix(), "sha256": terminal.sha256}, "baseline_advancement": _plain(advancement)}
        stored = write_create_only(run_root, PurePosixPath("pipeline6-tail") / "completion.json", completion)
        return PipelineTailAction(status, {"completion": {"path": stored.path.relative_to(run_root).as_posix(), "sha256": stored.sha256}, "terminal_run_receipt": {"path": terminal.path.relative_to(run_root).as_posix(), "sha256": terminal.sha256}, "baseline_advancement": _plain(advancement)}, None)
    except FlowError as error:
        return _blocked(error.code, error.path, error.message)
    except (OSError, ValueError):
        return _blocked("FEATURE_FLOW_INPUT", "/input", "Pipeline tail input is invalid.")


def _cli_envelope(action: PipelineTailAction) -> Mapping[str, Any]:
    return {"kind": action.kind, "artifact": _plain(action.artifact),
            "record_path": None if action.record_path is None else action.record_path.as_posix(),
            "diagnostics": _plain(action.diagnostics)}


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Advance one immutable Pipeline 6 tail action.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--baseline-receipt")
    parser.add_argument("--record")
    args = parser.parse_args(argv)
    try:
        registries = _current_fingerprint_registries()
        action = advance_pipeline6_tail(Path(args.project), Path(args.run_root), Path(args.baseline_root),
                                        None if args.baseline_receipt is None else Path(args.baseline_receipt),
                                        None if args.record is None else Path(args.record), fingerprint_registries=registries)
    except (OSError, ValueError, FlowError):
        action = _blocked("FEATURE_FLOW_INPUT", "/input", "Pipeline tail input is invalid.")
    stream = sys.stderr if action.kind == "BLOCKED" else sys.stdout
    print(json.dumps(_cli_envelope(action), ensure_ascii=False, sort_keys=True), file=stream)
    return 2 if action.kind == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
