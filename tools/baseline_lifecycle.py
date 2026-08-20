"""Immutable terminal receipts and durable feature-baseline lineage."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Mapping

from tools.canonical_document import CanonicalDocumentError, document_sha256, require_valid_canonical_document
from tools.flow_artifacts import FlowError, StoredArtifact, artifact_sha256, canonical_bytes, write_create_only
from tools.publish_test_case_bundle import Receipt
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


def _change_input(value: Any, repository: str, run_mode: str) -> Mapping[str, Any]:
    path = "/prefix_ledger/change_input"
    source = _mapping(value, path)
    kind = source.get("input_kind")
    shapes = {
        "git_head": {"input_kind", "repository_id", "target"},
        "git_range": {"input_kind", "repository_id", "base", "target"},
        "git_worktree": {"input_kind", "repository_id", "base", "target_snapshot_sha256"},
        "patch_manifest": {"input_kind", "repository_id", "base_snapshot_sha256", "target_snapshot_sha256"},
    }
    if kind not in shapes:
        raise _error("FEATURE_FLOW_INPUT", path + "/input_kind", "Change input kind is outside the closed union.")
    closed = _closed(source, shapes[kind], path)
    if closed.get("repository_id") != repository:
        raise _error("BASELINE_BINDING", path + "/repository_id", "Change input repository does not bind the prefix ledger.")
    if (kind == "git_head" and run_mode != "FULL") or (kind == "git_range" and run_mode != "CHANGE_SET"):
        raise _error("FEATURE_FLOW_INPUT", path + "/input_kind", "Change input kind is incompatible with run mode.")
    if kind in {"git_head", "git_range"}:
        target = _closed(closed.get("target"), {"commit", "tree"}, path + "/target")
        _git_id(target.get("commit"), path + "/target/commit")
        _git_id(target.get("tree"), path + "/target/tree")
    if kind in {"git_range", "git_worktree"}:
        base = _closed(closed.get("base"), {"commit", "tree"}, path + "/base")
        _git_id(base.get("commit"), path + "/base/commit")
        _git_id(base.get("tree"), path + "/base/tree")
    for name in ("base_snapshot_sha256", "target_snapshot_sha256"):
        if name in closed:
            _digest(closed[name], path + "/" + name)
    return closed


def _sorted_unique_strings(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value) or value != sorted(set(value)):
        raise _error("FEATURE_FLOW_INPUT", path, "Values must be unique nonempty strings in strict sorted order.")
    return value


def _validate_stored_context_relations(
    managed: Mapping[str, Any], accounting: Mapping[str, Any], receipt: Mapping[str, Any],
    authorized: Mapping[str, Any], inventory: Mapping[str, Any], changed: Mapping[str, Any] | None = None,
) -> None:
    """Recheck pure joins between canonical stored context carriers.

    Current bytes, plan derivation, and supplied-input contents remain upstream
    receipt authority because terminal construction has none of those inputs.
    """
    sources = authorized.get("sources")
    inventory_files = inventory.get("files")
    requirements = managed.get("requirements")
    requirement_sources = managed.get("requirement_sources")
    product_sources = managed.get("product_sources")
    if not all(isinstance(value, list) for value in (sources, inventory_files, requirements, requirement_sources, product_sources)):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context", "Stored behavior-context arrays are invalid.")
    source_ids = [row.get("source_id") if isinstance(row, Mapping) else None for row in sources]
    if any(not isinstance(value, str) or not value for value in source_ids) or len(source_ids) != len(set(source_ids)):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/authorized_behavior_sources", "Authorized source IDs must be unique nonempty strings.")
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise _error("BASELINE_BINDING", f"/prefix_ledger/artifacts/authorized_behavior_sources/sources/{index}", "Authorized source must be an object.")
        if source.get("kind") == "product_file":
            try:
                _safe_relative(source.get("path"), f"/prefix_ledger/artifacts/authorized_behavior_sources/sources/{index}/path")
            except FlowError:
                raise _error("BASELINE_BINDING", f"/prefix_ledger/artifacts/authorized_behavior_sources/sources/{index}/path", "Authorized product path must be safe.") from None
    source_by_id = {row["source_id"]: row for row in sources}
    requirement_ids = [row.get("requirement_id") if isinstance(row, Mapping) else None for row in requirements]
    display_orders = [row.get("display_order") if isinstance(row, Mapping) else None for row in requirements]
    if (
        not requirement_ids or any(not isinstance(value, str) or not value for value in requirement_ids)
        or len(requirement_ids) != len(set(requirement_ids))
        or any(type(value) is not int for value in display_orders)
        or display_orders != list(range(1, len(requirements) + 1))
    ):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context/requirements", "Managed requirements must retain unique IDs and exact canonical display order.")
    linked_requirement_ids = [row.get("requirement_id") if isinstance(row, Mapping) else None for row in requirement_sources]
    if linked_requirement_ids != requirement_ids:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context/requirement_sources", "Requirement-source rows must cover requirements in canonical order.")
    inverse: dict[str, list[str]] = {source_id: [] for source_id in source_ids}
    for index, row in enumerate(requirement_sources):
        links = row.get("source_ids") if isinstance(row, Mapping) else None
        if not isinstance(links, list) or not links or links != sorted(set(links)) or any(source_id not in source_by_id for source_id in links):
            raise _error("BASELINE_BINDING", f"/prefix_ledger/artifacts/managed_behavior_context/requirement_sources/{index}/source_ids", "Requirement sources must be authorized unique IDs in canonical order.")
        for source_id in links:
            inverse[source_id].append(row["requirement_id"])

    test_paths = {row.get("path") for row in inventory_files if isinstance(row, Mapping)}
    product_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(product_sources):
        source_id = row.get("source_id") if isinstance(row, Mapping) else None
        source = source_by_id.get(source_id)
        if (
            not isinstance(row, Mapping) or source_id in product_by_id or not isinstance(source, Mapping)
            or source.get("kind") != "product_file"
            or any(row.get(field) != source.get(field) for field in ("source_id", "kind", "path", "content_digest"))
            or not isinstance(row.get("path"), str) or not row["path"] or "\\" in row["path"]
            or PurePosixPath(row["path"]).is_absolute() or ".." in PurePosixPath(row["path"]).parts
            or row.get("path") in test_paths
        ):
            raise _error("BASELINE_BINDING", f"/prefix_ledger/artifacts/managed_behavior_context/product_sources/{index}", "Managed product sources must exactly project authorized non-test sources.")
        product_by_id[source_id] = row
    for source_id, linked in inverse.items():
        if linked and source_by_id[source_id].get("kind") == "product_file" and source_id not in product_by_id:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context/product_sources", "Every linked product source must have a managed projection.")

    if receipt.get("schema_version") == "2.0.0":
        if not isinstance(changed, Mapping):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "V6 context requires its changed projection.")
        dispositions = accounting.get("source_dispositions")
        outcomes = receipt.get("source_outcomes")
        registry = receipt.get("fragment_registry")
        groups = accounting.get("behavior_fragment_groups")
        if not all(isinstance(value, list) for value in (dispositions, outcomes, registry, groups)):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting", "Stored V6 accounting and receipt arrays are invalid.")
        if [row.get("source_id") if isinstance(row, Mapping) else None for row in dispositions] != source_ids or [row.get("source_id") if isinstance(row, Mapping) else None for row in outcomes] != source_ids:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/source_outcomes", "V6 dispositions and outcomes must cover current sources in inventory order.")
        outcome_by_id = {row["source_id"]: row for row in outcomes}
        registry_by_id: dict[str, Mapping[str, Any]] = {}
        for row in registry:
            fragment_id = row.get("fragment_id") if isinstance(row, Mapping) else None
            source_id = row.get("source_id") if isinstance(row, Mapping) else None
            if not isinstance(fragment_id, str) or fragment_id in registry_by_id or row.get("effect") != "retired" and source_id not in source_by_id:
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/fragment_registry", "V6 receipt fragments require unique current or retired source ownership.")
            registry_by_id[fragment_id] = row
        grouped_ids: list[str] = []
        for group in groups:
            fragments = group.get("fragment_ids") if isinstance(group, Mapping) else None
            linked = group.get("requirement_ids") if isinstance(group, Mapping) else None
            if not isinstance(fragments, list) or not fragments or len(fragments) != len(set(fragments)) or not isinstance(linked, list) or not linked or linked != [value for value in requirement_ids if value in linked]:
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/behavior_fragment_groups", "V6 groups require unique promoted fragments and canonical current requirements.")
            for fragment_id in fragments:
                fragment = registry_by_id.get(fragment_id)
                if fragment is None or fragment.get("effect") == "retired" or not set(linked) <= set(inverse.get(fragment.get("source_id"), ())):
                    raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/behavior_fragment_groups", "V6 groups must bind current fragments to exact managed source links.")
            grouped_ids.extend(fragments)
        changed_requirements = changed.get("requirements")
        changed_sources = changed.get("requirement_sources")
        stable_links = changed.get("stable_requirement_links")
        retired = changed.get("retired_requirements")
        if not all(isinstance(value, list) for value in (changed_requirements, changed_sources, stable_links, retired)):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "V6 changed projections must use closed arrays.")
        changed_ids = [row.get("requirement_id") if isinstance(row, Mapping) else None for row in changed_requirements]
        managed_by_id = {row["requirement_id"]: row for row in requirements}
        managed_links = {row["requirement_id"]: row["source_ids"] for row in requirement_sources}
        if (receipt.get("changed_requirement_ids") != changed_ids
                or [row.get("requirement_id") if isinstance(row, Mapping) else None for row in changed_sources] != changed_ids
                or any(_plain(row) != _plain(managed_by_id.get(row.get("requirement_id"))) for row in changed_requirements)
                or any(row.get("source_ids") != managed_links.get(row.get("requirement_id")) for row in changed_sources)):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "V6 changed requirements must be byte-identical managed projections.")
        stable_ids = [row.get("requirement_id") if isinstance(row, Mapping) else None for row in stable_links]
        retired_ids = [row.get("object_id") if isinstance(row, Mapping) else None for row in retired]
        if len(set(changed_ids + stable_ids + retired_ids)) != len(changed_ids + stable_ids + retired_ids) or set(changed_ids + stable_ids) != set(requirement_ids) or _plain(receipt.get("retired_requirements")) != _plain(retired):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "V6 requirement partitions must be disjoint, complete, and receipt-bound.")
        retirement_fragments = [fragment_id for row in retired for fragment_id in row["support_evidence"]["retirement_fragment_ids"]]
        current_fragments = {key for key, row in registry_by_id.items() if row.get("effect") != "retired"}
        retired_fragments = {key for key, row in registry_by_id.items() if row.get("effect") == "retired"}
        if len(grouped_ids) != len(set(grouped_ids)) or set(grouped_ids) != current_fragments or len(retirement_fragments) != len(set(retirement_fragments)) or set(retirement_fragments) != retired_fragments:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/fragment_registry", "V6 groups and tombstones must consume every promoted fragment exactly once.")
        for source, disposition in zip(sources, dispositions):
            linked = inverse[source["source_id"]]
            outcome = outcome_by_id[source["source_id"]].get("outcome")
            if disposition.get("disposition") == "represented":
                if disposition.get("requirement_ids") != linked or not linked or outcome not in {"behavior_fragments", "changed_behavior_fragments", "preserved_behavior"}:
                    raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "V6 represented sources must retain exact managed links and supported outcomes.")
            elif disposition.get("disposition") == "no_supported_observable_fact":
                if linked or source.get("kind") != "product_file" or source["source_id"] in product_by_id or outcome != "no_supported_observable_fact":
                    raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "V6 no-fact sources cannot retain managed behavior.")
            else:
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "V6 disposition uses an unsupported variant.")
        bindings = receipt.get("unchanged_source_bindings")
        if not isinstance(bindings, list):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/unchanged_source_bindings", "V6 unchanged bindings must be an array.")
        seen_bindings: set[str] = set()
        for row in bindings:
            source = source_by_id.get(row.get("source_id")) if isinstance(row, Mapping) else None
            if source is None or row["source_id"] in seen_bindings or row.get("baseline_content_digest") != row.get("current_content_digest") or row.get("current_content_digest") != source.get("content_digest"):
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/unchanged_source_bindings", "V6 unchanged bindings require unique exact digest equalities.")
            seen_bindings.add(row["source_id"])
        assurance = receipt.get("assurance")
        if not isinstance(assurance, Mapping) or assurance.get("independent_promotion_count", 0) + assurance.get("sequential_promotion_count", 0) != len(receipt.get("promotion_sha256s", ())):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/assurance", "V6 assurance counts must cover every promotion.")
        if receipt.get("run_mode") == "FULL" and (stable_links or retired or changed_ids != requirement_ids):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "FULL V6 changed context must equal all managed requirements.")
        return

    dispositions = accounting.get("source_dispositions")
    outcomes = receipt.get("source_outcomes")
    registry = receipt.get("fragment_registry")
    groups = accounting.get("behavior_fragment_groups")
    if not all(isinstance(value, list) for value in (dispositions, outcomes, registry, groups)):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting", "Stored accounting and receipt arrays are invalid.")
    if [row.get("source_id") if isinstance(row, Mapping) else None for row in dispositions] != source_ids:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "Dispositions must exactly cover authorized sources in inventory order.")
    if [row.get("source_id") if isinstance(row, Mapping) else None for row in outcomes] != source_ids:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/source_outcomes", "Receipt outcomes must exactly cover authorized sources in inventory order.")
    outcome_by_id = {row["source_id"]: row for row in outcomes}
    registry_by_id: dict[str, Mapping[str, Any]] = {}
    registry_by_source: dict[str, list[str]] = {source_id: [] for source_id in source_ids}
    for row in registry:
        fragment_id = row.get("fragment_id") if isinstance(row, Mapping) else None
        source_id = row.get("source_id") if isinstance(row, Mapping) else None
        if fragment_id in registry_by_id or source_id not in source_by_id or outcome_by_id[source_id].get("outcome") != "behavior_fragments":
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt/fragment_registry", "Receipt fragments must be unique and owned by represented authorized sources.")
        registry_by_id[fragment_id] = row
        registry_by_source[source_id].append(fragment_id)
    grouped_ids: list[str] = []
    group_requirements: dict[str, set[str]] = {source_id: set() for source_id in source_ids}
    for group in groups:
        fragments = group.get("fragment_ids") if isinstance(group, Mapping) else None
        linked = group.get("requirement_ids") if isinstance(group, Mapping) else None
        if (
            not isinstance(fragments, list) or not fragments or len(fragments) != len(set(fragments))
            or not isinstance(linked, list) or not linked
            or linked != [requirement_id for requirement_id in requirement_ids if requirement_id in linked]
        ):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/behavior_fragment_groups", "Fragment groups must use unique receipt fragments and canonical requirements.")
        grouped_ids.extend(fragments)
        for fragment_id in fragments:
            fragment = registry_by_id.get(fragment_id)
            if fragment is None:
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/behavior_fragment_groups", "Accounting groups cannot reference foreign receipt fragments.")
            group_requirements[fragment["source_id"]].update(linked)
    if len(grouped_ids) != len(set(grouped_ids)) or set(grouped_ids) != set(registry_by_id):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/behavior_fragment_groups", "Accounting groups must cover every receipt fragment exactly once.")
    for source_id, source, disposition in zip(source_ids, sources, dispositions):
        linked = inverse[source_id]
        outcome = outcome_by_id[source_id].get("outcome")
        if disposition.get("disposition") == "represented":
            if (
                set(disposition) != {"source_id", "disposition", "requirement_ids"}
                or disposition.get("requirement_ids") != linked or not linked
                or set(linked) != group_requirements[source_id] or outcome != "behavior_fragments"
                or not registry_by_source[source_id]
            ):
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "Represented dispositions must equal inverse requirement, fragment-group, and receipt outcome joins.")
        elif disposition.get("disposition") == "no_supported_observable_fact":
            if (
                set(disposition) != {"source_id", "disposition", "reason"} or linked
                or group_requirements[source_id] or registry_by_source[source_id]
                or source.get("kind") != "product_file" or source_id in product_by_id
                or outcome != "no_supported_observable_fact"
            ):
                raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "No-fact dispositions must have no managed, requirement, fragment, or represented-outcome projection.")
        else:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting/source_dispositions", "Accounting dispositions must use a supported closed variant.")


def _validate_prefix_evidence(
    values: Mapping[str, Mapping[str, Any]], digests: Mapping[str, str | None],
    repository: str, selected_module: str, run_mode: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    inventory_envelope = values["technical_test_inventory"]
    sources_envelope = values["authorized_behavior_sources"]
    if inventory_envelope != sources_envelope:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/authorized_behavior_sources", "Source inventory projections must come from one exact envelope.")
    source_artifacts = _mapping(inventory_envelope.get("artifacts"), "/prefix_ledger/artifacts/technical_test_inventory/artifacts")
    inventory = _mapping(source_artifacts.get("technical_test_inventory"), "/prefix_ledger/artifacts/technical_test_inventory/artifacts/technical_test_inventory")
    authorized = _mapping(source_artifacts.get("authorized_behavior_sources"), "/prefix_ledger/artifacts/authorized_behavior_sources/artifacts/authorized_behavior_sources")
    inventory_sha256 = artifact_sha256(inventory)
    authorized_sha256 = artifact_sha256(authorized)
    if source_artifacts.get("technical_test_inventory_sha256") != inventory_sha256 or source_artifacts.get("authorized_behavior_sources_sha256") != authorized_sha256:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/technical_test_inventory", "Source inventory internal digests do not bind their bare artifacts.")
    if inventory.get("module_id") != selected_module or authorized.get("module_id") != selected_module:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/technical_test_inventory", "Source inventories must bind the selected module.")

    receipt = values["behavior_context_receipt"]
    _validate_schema(receipt, "behavior-context-receipt.schema.json", "/prefix_ledger/artifacts/behavior_context_receipt")
    receipt_sha256 = artifact_sha256(receipt)
    if receipt.get("selected_module") != selected_module or receipt.get("authorized_behavior_sources_sha256") != authorized_sha256:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_context_receipt", "Behavior context receipt does not bind module and authorized sources.")
    scope = values["change_scope_receipt"]
    if receipt.get("schema_version") == "2.0.0":
        _validate_schema(scope, "change-scope-receipt.schema.json", "/prefix_ledger/artifacts/change_scope_receipt")
        if (scope.get("run_mode") != run_mode or receipt.get("run_mode") != run_mode
                or receipt.get("scope_receipt_sha256") != artifact_sha256(scope)
                or receipt.get("baseline_receipt_sha256") != scope.get("baseline_receipt_sha256")):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/change_scope_receipt", "Pipeline 6 context receipt must bind the exact promoted scope receipt.")
    else:
        scope = _closed(scope, {"schema_version", "artifact", "source", "payload_sha256"}, "/prefix_ledger/artifacts/change_scope_receipt")
        scope_source = _closed(scope.get("source"), {"repository_id", "selected_module", "run_mode"}, "/prefix_ledger/artifacts/change_scope_receipt/source")
        if scope.get("schema_version") != "1.0.0" or scope.get("artifact") != "change_scope_receipt" or scope_source != {"repository_id": repository, "selected_module": selected_module, "run_mode": run_mode}:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/change_scope_receipt", "Change-scope sidecar does not bind the run source.")
        _digest(scope.get("payload_sha256"), "/prefix_ledger/artifacts/change_scope_receipt/payload_sha256")
    managed_envelope = values["managed_behavior_context"]
    accounting_envelope = values["behavior_source_accounting"]
    _validate_schema(managed_envelope, "context-marker-output.schema.json", "/prefix_ledger/artifacts/managed_behavior_context")
    _validate_schema(accounting_envelope, "context-marker-output.schema.json", "/prefix_ledger/artifacts/behavior_source_accounting")
    if managed_envelope != accounting_envelope:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/behavior_source_accounting", "Managed context projections must come from one exact envelope.")
    if (managed_envelope.get("schema_version"), receipt.get("schema_version")) not in {("5.0.0", "1.0.0"), ("6.0.0", "2.0.0")}:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context", "Context and receipt versions must use an exact supported pair.")
    context_artifacts = _mapping(managed_envelope.get("artifacts"), "/prefix_ledger/artifacts/managed_behavior_context/artifacts")
    managed = _mapping(context_artifacts.get("managed_behavior_context"), "/prefix_ledger/artifacts/managed_behavior_context/artifacts/managed_behavior_context")
    accounting = _mapping(context_artifacts.get("behavior_source_accounting"), "/prefix_ledger/artifacts/behavior_source_accounting/artifacts/behavior_source_accounting")
    context_changed = context_artifacts.get("changed_behavior_context")
    if managed.get("authorized_behavior_sources_sha256") != authorized_sha256 or accounting.get("authorized_behavior_sources_sha256") != authorized_sha256 or accounting.get("context_receipt_sha256") != receipt_sha256:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/managed_behavior_context", "Context, accounting, receipt, and authorized-source digests disagree.")
    _validate_stored_context_relations(managed, accounting, receipt, authorized, inventory, context_changed if isinstance(context_changed, Mapping) else None)

    if receipt.get("schema_version") == "2.0.0":
        changed = _mapping(values["changed_behavior_context"], "/prefix_ledger/artifacts/changed_behavior_context")
        _validate_schema(changed, "changed-behavior-context.schema.json", "/prefix_ledger/artifacts/changed_behavior_context")
        if not isinstance(context_changed, Mapping) or _plain(changed) != _plain(context_changed):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "Changed context must equal the exact projection inside the V6 envelope.")
    else:
        changed = _closed(values["changed_behavior_context"], {"schema_version", "artifact", "source", "requirement_ids", "retired_requirement_ids"}, "/prefix_ledger/artifacts/changed_behavior_context")
        changed_source = _closed(changed.get("source"), {"behavior_context_receipt_sha256"}, "/prefix_ledger/artifacts/changed_behavior_context/source")
        if changed.get("schema_version") != "1.0.0" or changed.get("artifact") != "changed-behavior-context" or changed_source.get("behavior_context_receipt_sha256") != receipt_sha256:
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/changed_behavior_context", "Changed context does not bind the behavior context receipt.")
        requirement_ids = _sorted_unique_strings(changed.get("requirement_ids"), "/prefix_ledger/artifacts/changed_behavior_context/requirement_ids")
        retired_ids = _sorted_unique_strings(changed.get("retired_requirement_ids"), "/prefix_ledger/artifacts/changed_behavior_context/retired_requirement_ids")
        if set(requirement_ids) & set(retired_ids):
            raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger/artifacts/changed_behavior_context", "Changed and retired requirement IDs must be disjoint.")

    classification_envelope = values["technical_test_classification"]
    classification = _mapping(classification_envelope["artifacts"]["classification"], "/prefix_ledger/artifacts/technical_test_classification/artifacts/classification")
    classification_sha256 = artifact_sha256(classification)
    if classification.get("technical_test_inventory_sha256") != inventory_sha256:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/technical_test_classification", "Classification does not bind the bare technical inventory.")
    expected_pairs = [(row.get("file_id"), row.get("symbol_id")) for row in inventory.get("symbols", [])]
    classification_pairs = [(row.get("file_id"), row.get("symbol_id")) for row in classification.get("classifications", [])]
    if classification_pairs != expected_pairs:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/technical_test_classification", "Classification coverage does not equal inventory symbol order.")
    review = _mapping(values["classification_review"]["artifacts"]["classification_review"], "/prefix_ledger/artifacts/classification_review/artifacts/classification_review")
    review_sha256 = artifact_sha256(review)
    review_pairs = [(row.get("file_id"), row.get("symbol_id")) for row in review.get("reviewed_symbol_pairs", [])]
    if review.get("technical_test_inventory_sha256") != inventory_sha256 or review.get("classification_sha256") != classification_sha256 or review_pairs != expected_pairs:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/classification_review", "Classification review does not bind inventory, classification, and ordered pairs.")
    evidence = values["effective_technical_evidence"]
    evidence_without_digest = dict(evidence); evidence_digest = evidence_without_digest.pop("effective_technical_evidence_sha256", None)
    if (
        evidence.get("technical_test_inventory_sha256") != inventory_sha256
        or evidence.get("technical_test_classification_sha256") != classification_sha256
        or evidence.get("technical_test_review_sha256") != review_sha256
        or evidence.get("files") != inventory.get("files") or evidence.get("symbols") != inventory.get("symbols")
        or evidence.get("classifications") != classification.get("classifications")
        or evidence_digest != artifact_sha256(evidence_without_digest)
    ):
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/effective_technical_evidence", "Effective technical evidence does not bind selected inventory, classification, and review.")
    return inventory, classification, review


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
        "schema_version", "artifact", "feature_flow_prefix_sha256", "tail_record_sha256s", "repository_id", "selected_module", "run_mode", "change_input",
        "analytics_sha256", "source_drift", "fingerprints", "artifacts",
    }, "/prefix_ledger")
    if ledger.get("schema_version") != "1.0.0" or ledger.get("artifact") != "prefix-ledger":
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "Prefix ledger must use closed V1 identity.")
    repository = _digest(ledger.get("repository_id"), "/prefix_ledger/repository_id")
    _digest(ledger.get("feature_flow_prefix_sha256"), "/prefix_ledger/feature_flow_prefix_sha256")
    records = ledger.get("tail_record_sha256s")
    if not isinstance(records, list) or any(not isinstance(row, str) or _DIGEST_RE.fullmatch(row) is None for row in records):
        raise _error("BASELINE_BINDING", "/prefix_ledger/tail_record_sha256s", "Tail record digest list is invalid.")
    if not isinstance(ledger.get("selected_module"), str) or not ledger["selected_module"] or ledger.get("run_mode") not in {"FULL", "CHANGE_SET"} or type(ledger.get("source_drift")) is not bool:
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger", "Prefix ledger run metadata is invalid.")
    change_input = _change_input(ledger.get("change_input"), repository, ledger["run_mode"])
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

    _, _, classification_review = _validate_prefix_evidence(
        prefix_values, artifact_digests, repository, ledger["selected_module"], ledger["run_mode"],
    )

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

    classification = classification_review
    classification_verdict = classification.get("verdict")
    findings = classification.get("findings")
    if not isinstance(findings, list) or (classification_verdict == "ПРИНЯТО") != (findings == []):
        raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger/artifacts/classification_review", "Classification verdict and findings disagree.")

    validation_envelope = prefix_values["validation_report"]
    report: Mapping[str, Any] | None = None
    delta_application_sha256: str | None = None
    unchanged_selection_sha256: str | None = None
    if validation_envelope.get("stage") == "tc-reviewer":
        _validate_schema(validation_envelope, "tc-reviewer-output.schema.json", "/prefix_ledger/artifacts/validation_report")
        report = _mapping(validation_envelope["artifacts"]["validation_report"], "/prefix_ledger/artifacts/validation_report")
        test_case_verdict = report.get("verdict")
    else:
        branch = _closed(validation_envelope, {"schema_version", "artifact", "delta_application_receipt", "unchanged_document_selection"}, "/prefix_ledger/artifacts/validation_report")
        if branch.get("schema_version") != "1.0.0" or branch.get("artifact") != "unchanged-baseline-tail-branch":
            raise _error("FEATURE_FLOW_INPUT", "/prefix_ledger/artifacts/validation_report", "Tail branch is not a closed unchanged-baseline selection.")
        delta = _mapping(branch.get("delta_application_receipt"), "/prefix_ledger/artifacts/validation_report/delta_application_receipt")
        selection = _mapping(branch.get("unchanged_document_selection"), "/prefix_ledger/artifacts/validation_report/unchanged_document_selection")
        _validate_schema(delta, "delta-application-receipt.schema.json", "/prefix_ledger/artifacts/validation_report/delta_application_receipt")
        _validate_schema(selection, "unchanged-document-selection.schema.json", "/prefix_ledger/artifacts/validation_report/unchanged_document_selection")
        if delta.get("status") != "UNCHANGED" or delta.get("publication_required") is not False or selection.get("delta_application_receipt_sha256") != artifact_sha256(delta):
            raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/validation_report", "Unchanged selection does not bind an exact zero-op receipt.")
        test_case_verdict = "UNCHANGED_BASELINE"
        delta_application_sha256 = artifact_sha256(delta)
        unchanged_selection_sha256 = artifact_sha256(selection)

    document = _mapping(tail_values["effective_document"], "/tail_artifacts/effective_document")
    expected_source = {
        "document_id": document.get("document_id"), "revision": document.get("revision"),
        "source_digest": artifact_digests["effective_document_sha256"],
    }
    bundle = _mapping(tail_values["effective_bundle_receipt"], "/tail_artifacts/effective_bundle_receipt")
    if bundle.get("document_id") != expected_source["document_id"] or bundle.get("revision") != expected_source["revision"] or bundle.get("document_sha256") != expected_source["source_digest"]:
        raise _error("BASELINE_BINDING", "/tail_artifacts/effective_bundle_receipt", "Effective bundle does not bind the effective document.")
    if test_case_verdict == "ПРИНЯТО" and report is not None and report.get("candidate", {}).get("document_sha256") != expected_source["source_digest"]:
        raise _error("BASELINE_BINDING", "/prefix_ledger/artifacts/validation_report", "Accepted validation report does not bind the effective document.")
    if test_case_verdict == "AUTO_FIX_APPLIED" and report is not None:
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

    artifact_digests["delta_application_receipt_sha256"] = delta_application_sha256
    artifact_digests["unchanged_document_selection_sha256"] = unchanged_selection_sha256
    artifact_digests["validation_report_sha256"] = None if test_case_verdict == "UNCHANGED_BASELINE" else artifact_digests["validation_report_sha256"]
    receipt = {
        "schema_version": "1.0.0", "artifact": "terminal-run-receipt", "prefix_ledger_sha256": prefix_ledger.sha256, "repository_id": repository,
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


@dataclass(frozen=True, eq=False)
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


_VALIDATED_BASELINES: weakref.WeakKeyDictionary[ValidatedBaseline, object] = weakref.WeakKeyDictionary()


class ValidatedEffectiveBaseline:
    """Opaque document-and-bundle capability issued from a validated baseline only."""
    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("ValidatedEffectiveBaseline is issued only by bind_effective_baseline.")


_EFFECTIVE_BASELINES: weakref.WeakKeyDictionary[ValidatedEffectiveBaseline, Mapping[str, Any]] = weakref.WeakKeyDictionary()


def bind_effective_baseline(
    baseline: ValidatedBaseline, effective_document: Mapping[str, Any], effective_bundle_receipt: Receipt,
) -> ValidatedEffectiveBaseline:
    """Bind exact readback document and bundle receipt to a genuinely validated baseline."""
    if not isinstance(baseline, ValidatedBaseline) or baseline not in _VALIDATED_BASELINES:
        raise _error("BASELINE_BINDING", "/baseline", "A genuinely validated baseline is required.")
    if not isinstance(effective_document, Mapping) or type(effective_bundle_receipt) is not Receipt:
        raise _error("BASELINE_BINDING", "/effective", "Exact canonical document and bundle receipt are required.")
    document = _plain(effective_document)
    try:
        require_valid_canonical_document(document)
    except CanonicalDocumentError:
        raise _error("BASELINE_BINDING", "/effective_document", "Effective document is not canonical.") from None
    receipt = Receipt(**asdict(effective_bundle_receipt))
    if (
        artifact_sha256(document) != baseline.document_sha256
        or artifact_sha256(asdict(receipt)) != baseline.bundle_sha256
        or (receipt.document_id, receipt.revision, receipt.document_sha256)
        != (document["document_id"], document["revision"], document_sha256(document))
    ):
        raise _error("BASELINE_BINDING", "/effective", "Effective document or bundle receipt does not bind the baseline.")
    result = object.__new__(ValidatedEffectiveBaseline)
    _EFFECTIVE_BASELINES[result] = _freeze({"document": document, "receipt": receipt, "baseline_receipt_sha256": baseline.receipt_sha256})
    return result


def effective_baseline_projection(value: ValidatedEffectiveBaseline) -> tuple[Mapping[str, Any], Receipt]:
    """Return exact frozen predecessor payloads only for an issued capability."""
    if not isinstance(value, ValidatedEffectiveBaseline) or value not in _EFFECTIVE_BASELINES:
        raise _error("BASELINE_BINDING", "/baseline", "An issued effective baseline is required.")
    row = _EFFECTIVE_BASELINES[value]
    receipt = row["receipt"]
    return row["document"], Receipt(**asdict(receipt))


def effective_baseline_receipt_sha256(value: ValidatedEffectiveBaseline) -> str:
    """Return the predecessor receipt digest only for an issued effective capability."""
    if not isinstance(value, ValidatedEffectiveBaseline) or value not in _EFFECTIVE_BASELINES:
        raise _error("BASELINE_BINDING", "/baseline", "An issued effective baseline is required.")
    return str(_EFFECTIVE_BASELINES[value]["baseline_receipt_sha256"])


class ScopePredecessor:
    """Opaque, immutable projection of a fully validated predecessor."""
    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("ScopePredecessor is issued only by bind_scope_predecessor.")


_SCOPE_PREDECESSORS: weakref.WeakKeyDictionary[ScopePredecessor, Mapping[str, Any]] = weakref.WeakKeyDictionary()


def bind_scope_predecessor(
    baseline: ValidatedBaseline, source_inventory_envelope: Mapping[str, Any],
    context_envelope: Mapping[str, Any], behavior_context_receipt: Mapping[str, Any],
) -> ScopePredecessor:
    """Issue the narrow predecessor capability after rechecking stored joins."""
    if not isinstance(baseline, ValidatedBaseline) or baseline not in _VALIDATED_BASELINES:
        raise _error("BASELINE_BINDING", "/baseline", "A genuinely validated baseline is required.")
    try:
        if artifact_sha256(baseline.receipt) != baseline.receipt_sha256:
            raise ValueError
        artifacts = _closed(baseline.receipt.get("artifacts"), set(_BASELINE_ARTIFACT_KEYS), "/receipt/artifacts", "BASELINE_BINDING")
        _validate_schema(source_inventory_envelope, "source-inventory-output.schema.json", "/predecessor/source_inventory")
        _validate_schema(context_envelope, "context-marker-output.schema.json", "/predecessor/context")
        _validate_schema(behavior_context_receipt, "behavior-context-receipt.schema.json", "/predecessor/receipt")
        source_artifacts = _mapping(source_inventory_envelope.get("artifacts"), "/predecessor/source_inventory/artifacts")
        context_artifacts = _mapping(context_envelope.get("artifacts"), "/predecessor/context/artifacts")
        authorized = _mapping(source_artifacts.get("authorized_behavior_sources"), "/predecessor/sources")
        inventory = _mapping(source_artifacts.get("technical_test_inventory"), "/predecessor/inventory")
        managed = _mapping(context_artifacts.get("managed_behavior_context"), "/predecessor/managed")
        accounting = _mapping(context_artifacts.get("behavior_source_accounting"), "/predecessor/accounting")
        context_changed = context_artifacts.get("changed_behavior_context")
        authorized_digest, inventory_digest = artifact_sha256(authorized), artifact_sha256(inventory)
        source_envelope_digest, context_envelope_digest = artifact_sha256(source_inventory_envelope), artifact_sha256(context_envelope)
        if (source_artifacts.get("authorized_behavior_sources_sha256") != authorized_digest
            or source_artifacts.get("technical_test_inventory_sha256") != inventory_digest
            or artifacts["authorized_behavior_sources_sha256"] != source_envelope_digest
            or artifacts["technical_test_inventory_sha256"] != source_envelope_digest
            or artifacts["managed_behavior_context_sha256"] != context_envelope_digest
            or artifacts["behavior_source_accounting_sha256"] != context_envelope_digest
            or artifacts["behavior_context_receipt_sha256"] != artifact_sha256(behavior_context_receipt)
            or behavior_context_receipt.get("selected_module") != baseline.selected_module
            or behavior_context_receipt.get("authorized_behavior_sources_sha256") != authorized_digest
            or authorized.get("module_id") != baseline.selected_module or inventory.get("module_id") != baseline.selected_module
            or managed.get("authorized_behavior_sources_sha256") != authorized_digest
            or accounting.get("authorized_behavior_sources_sha256") != authorized_digest
            or accounting.get("context_receipt_sha256") != artifact_sha256(behavior_context_receipt)
            or (context_envelope.get("schema_version"), behavior_context_receipt.get("schema_version")) not in {("5.0.0", "1.0.0"), ("6.0.0", "2.0.0")}
            or behavior_context_receipt.get("schema_version") == "2.0.0" and (
                not isinstance(context_changed, Mapping)
            )):
            raise ValueError
        _validate_stored_context_relations(managed, accounting, behavior_context_receipt, authorized, inventory, context_changed if isinstance(context_changed, Mapping) else None)
    except (FlowError, ValueError, TypeError, KeyError):
        raise _error("BASELINE_BINDING", "/predecessor", "Predecessor carriers do not bind validated baseline authority.") from None
    result = object.__new__(ScopePredecessor)
    _SCOPE_PREDECESSORS[result] = _freeze({
        "receipt_sha256": baseline.receipt_sha256, "repository_id": baseline.repository_id,
        "target_commit": baseline.target_commit, "target_tree": baseline.target_tree,
        "selected_module": baseline.selected_module,
        "context_envelope_sha256": context_envelope_digest,
        "behavior_context_receipt_sha256": artifact_sha256(behavior_context_receipt),
        "sources": tuple(_freeze(_plain(row)) for row in authorized["sources"]),
        "requirement_ids": tuple(row["requirement_id"] for row in managed["requirements"]),
    })
    return result


def scope_predecessor_projection(predecessor: ScopePredecessor) -> Mapping[str, Any]:
    """Return only the safe data needed by downstream change scoping."""
    if not isinstance(predecessor, ScopePredecessor) or predecessor not in _SCOPE_PREDECESSORS:
        raise _error("BASELINE_BINDING", "/predecessor", "An issued scope predecessor is required.")
    return _SCOPE_PREDECESSORS[predecessor]


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


def _is_ancestor(project: Path, base: str, head: str) -> bool:
    try:
        completed = subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=project, capture_output=True, check=False)
    except OSError:
        raise _error("FEATURE_FLOW_INPUT", "/project", "Git ancestry proof is unavailable.") from None
    if completed.returncode not in {0, 1}:
        raise _error("FEATURE_FLOW_INPUT", "/project", "Git ancestry proof could not be established.")
    return completed.returncode == 0


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
    result = ValidatedBaseline(
        repository, commit, tree, selected_module,
        artifacts["technical_test_inventory_sha256"], artifacts["managed_behavior_context_sha256"],
        artifacts["effective_document_sha256"], artifacts["effective_bundle_receipt_sha256"],
        _freeze(stored_fingerprints), artifact_sha256(value), frozen,
    )
    _VALIDATED_BASELINES[result] = object()
    return result


def choose_run_mode(target: Mapping[str, Any], baseline: ValidatedBaseline | None = None) -> Literal["FULL", "CHANGE_SET"]:
    if not isinstance(baseline, ValidatedBaseline):
        return "FULL"
    try:
        value = _mapping(target, "/target")
        base = _mapping(value.get("base"), "/target/base")
        kind = value.get("input_kind")
        shared = (
            kind in {"git_range", "git_worktree", "patch_manifest"}
            and value.get("repository_id") == baseline.repository_id
            and value.get("selected_module") == baseline.selected_module
            and _fingerprint_digests(value.get("fingerprints"), "/target/fingerprints") == dict(baseline.fingerprints)
        )
        if kind in {"git_range", "git_worktree"}:
            compatible = shared and base.get("commit") == baseline.target_commit and base.get("tree") == baseline.target_tree
        else:
            compatible = shared and base.get("snapshot_sha256") == artifact_sha256({"repository_id": baseline.repository_id, "tree": baseline.target_tree})
    except FlowError:
        return "FULL"
    return "CHANGE_SET" if compatible else "FULL"


def _install_link(root: Path, relative: PurePosixPath, value: Mapping[str, Any]) -> bool:
    if not isinstance(relative, PurePosixPath) or relative.is_absolute() or not relative.parts or ".." in relative.parts or "\\" in str(relative):
        raise _error("FLOW_ATOMIC_WRITE", "/storage", "Predecessor link path is not safe and relative.")
    payload = canonical_bytes(value)
    resolved_root = root.resolve()
    target = (resolved_root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        raise _error("FLOW_ATOMIC_WRITE", "/storage", "Predecessor link path escapes baseline storage.") from None
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (resolved_root, *target.parents)):
            raise _error("FLOW_ATOMIC_WRITE", "/storage", "Predecessor link path may not traverse a symbolic link.")
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
        and acceptance.get("test_case_review_verdict") in {"ПРИНЯТО", "AUTO_FIX_APPLIED", "UNCHANGED_BASELINE"}
        and acceptance.get("autotest_review_verdict") == "ПРИНЯТО"
        and acceptance.get("trace_verdict") == "PASS"
        and acceptance.get("final_status") in ELIGIBLE_FINAL_STATUSES
        and acceptance.get("source_drift") is False
    )


def _copy_baseline_payloads(run_root: Path, baseline_root: Path, digests: tuple[str, ...]) -> None:
    """Copy only receipt-named, canonical run payloads before a lineage edge exists."""
    for digest in digests:
        if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
            raise _error("BASELINE_BINDING", "/terminal_receipt/artifacts", "Baseline payload digest is invalid.")
        relative = PurePosixPath("artifacts") / f"{digest[7:]}.json"
        try:
            source = (run_root / Path(*relative.parts)).resolve(strict=True)
            source.relative_to(run_root.resolve(strict=True))
            raw = source.read_bytes()
        except (OSError, ValueError):
            raise _error("FLOW_ATOMIC_WRITE", "/terminal_receipt/artifacts", "Receipt-named run payload is unavailable.") from None
        value = _read_json_bytes(raw, "/terminal_receipt/artifacts")
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest or artifact_sha256(value) != digest:
            raise _error("BASELINE_BINDING", "/terminal_receipt/artifacts", "Receipt-named run payload does not match its digest.")
        stored = write_create_only(baseline_root, PurePosixPath("payloads") / "sha256" / f"{digest[7:]}.json", value)
        if stored.sha256 != digest:
            raise _error("FLOW_ATOMIC_WRITE", "/baseline_payloads", "Baseline payload readback did not retain its digest.")


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
    terminal_repository = _digest(terminal.get("repository_id"), "/terminal_receipt/repository_id")
    change = _change_input(terminal.get("change_input"), terminal_repository, terminal.get("run_mode"))
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
                and _is_ancestor(project, predecessor.target_commit, target_commit)
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

    terminal_artifacts = _closed(terminal.get("artifacts"), {name + "_sha256" for name in (*_PREFIX_KEYS, *_TAIL_KEYS)} | {"delta_application_receipt_sha256", "unchanged_document_selection_sha256"}, "/terminal_receipt/artifacts", "BASELINE_BINDING")
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
    try:
        run_root = terminal_receipt.path.parent.parent.resolve(strict=True)
    except OSError:
        raise _error("FLOW_ATOMIC_WRITE", "/terminal_receipt", "Terminal receipt run root is unavailable.") from None
    # The terminal itself is receipt-named too; materialize it in the same
    # immutable run namespace before copying the complete successor closure.
    write_create_only(run_root, PurePosixPath("artifacts") / f"{terminal_receipt.sha256[7:]}.json", terminal)
    _copy_baseline_payloads(
        run_root,
        baseline_root,
        tuple(baseline["artifacts"][name] for name in _BASELINE_ARTIFACT_KEYS),
    )
    write_create_only(baseline_root, PurePosixPath("receipts") / f"{successor_digest[7:]}.json", baseline)
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
        created = _install_link(baseline_root, link_path, edge)
    except FlowError as caught:
        if caught.code != "FLOW_CONFLICT":
            raise
        return _advancement("BASELINE_CONFLICT", predecessor_digest)
    return _advancement("ADVANCED" if created else "IDEMPOTENT", predecessor_digest, successor_digest, baseline)
