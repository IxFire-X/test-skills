"""Closed canonical-document delta application for Pipeline 6 change sets."""

from __future__ import annotations

import copy
import json
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from tools.canonical_document import CanonicalDocumentError, canonical_bytes, document_sha256, require_valid_canonical_document
from tools.baseline_lifecycle import ValidatedEffectiveBaseline, effective_baseline_projection, effective_baseline_receipt_sha256
from tools.flow_artifacts import artifact_sha256
from tools.schema_validation import schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]
DELTA_SCHEMA = ROOT / "schemas" / "canonical-document-delta.schema.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "delta-application-receipt.schema.json"
SELECTION_SCHEMA = ROOT / "schemas" / "unchanged-document-selection.schema.json"
BEHAVIOR_RECEIPT_SCHEMA = ROOT / "schemas" / "behavior-context-receipt.schema.json"
CHANGED_CONTEXT_SCHEMA = ROOT / "schemas" / "changed-behavior-context.schema.json"


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _diagnostic(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


class DocumentDeltaError(ValueError):
    """Raised for a structurally valid but unsafe or unbound document delta."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]) -> None:
        rows = sorted((dict(row) for row in diagnostics), key=lambda row: (row["path"], row["code"], row["message"]))
        self.diagnostics = tuple(MappingProxyType(row) for row in rows)
        super().__init__(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True, eq=False)
class AppliedDocumentDelta:
    status: Literal["CHANGED", "UNCHANGED"]
    candidate_document: Mapping[str, Any]
    publication_required: bool
    delta_sha256: str
    baseline_document_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_document": _plain(self.candidate_document),
            "publication_required": self.publication_required,
            "delta_sha256": self.delta_sha256,
            "baseline_document_sha256": self.baseline_document_sha256,
        }


_APPLIED: weakref.WeakKeyDictionary[AppliedDocumentDelta, object] = weakref.WeakKeyDictionary()


def _object_sha256(value: Mapping[str, Any]) -> str:
    return artifact_sha256(value)


def _schema(value: Any, schema: Path, path: str) -> list[dict[str, str]]:
    return [_diagnostic(path + str(row["path"]), "DOCUMENT_DELTA_SHAPE", "Artifact does not match its closed schema.") for row in schema_diagnostics(value, schema, ROOT)]


def _collection(
    baseline: list[dict[str, Any]], part: Mapping[str, Any], *, key: str, collection: str,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, str]]]:
    """Apply one exact identity partition, preserving baseline slots and delta add order."""
    diagnostics: list[dict[str, str]] = []
    baseline_by_id = {row[key]: row for row in baseline}
    if len(baseline_by_id) != len(baseline):
        diagnostics.append(_diagnostic(f"/{collection}", "DOCUMENT_DELTA_IDENTITY", "Baseline identity must be unique."))
        return [], False, diagnostics
    reused = list(part["reused_ids"])
    modified = list(part["modified"])
    retired = list(part["retired"])
    added = list(part["added"])
    modified_by_id = {row[key]: row for row in modified}
    retired_by_id = {row["object_id"]: row for row in retired}
    identities = reused + [row[key] for row in modified] + [row["object_id"] for row in retired]
    if len(set(identities)) != len(identities) or set(identities) != set(baseline_by_id):
        diagnostics.append(_diagnostic(f"/{collection}", "DOCUMENT_DELTA_IDENTITY", "Every baseline identity must appear exactly once across reused, modified, and retired."))
    if len(modified_by_id) != len(modified) or len(retired_by_id) != len(retired):
        diagnostics.append(_diagnostic(f"/{collection}", "DOCUMENT_DELTA_IDENTITY", "Modified and retired identities must be unique."))
    added_ids = [row[key] for row in added]
    if len(set(added_ids)) != len(added_ids) or set(added_ids) & set(baseline_by_id):
        diagnostics.append(_diagnostic(f"/{collection}/added", "DOCUMENT_DELTA_IDENTITY", "Added identities must be unique and absent from baseline."))
    for object_id, change in modified_by_id.items():
        if object_id not in baseline_by_id or change["replacement"].get(key) != object_id or change["baseline_object_sha256"] != _object_sha256(baseline_by_id[object_id]):
            diagnostics.append(_diagnostic(f"/{collection}/modified", "DOCUMENT_DELTA_BINDING", "Replacement must retain identity and bind the exact baseline object."))
    for object_id, tombstone in retired_by_id.items():
        if object_id not in baseline_by_id or tombstone["baseline_object_sha256"] != _object_sha256(baseline_by_id[object_id]):
            diagnostics.append(_diagnostic(f"/{collection}/retired", "DOCUMENT_DELTA_BINDING", "Tombstone must bind the exact baseline object."))
    if diagnostics:
        return [], False, diagnostics
    result = [
        _plain(modified_by_id[row[key]]["replacement"]) if row[key] in modified_by_id else _plain(row)
        for row in baseline if row[key] not in retired_by_id
    ]
    result.extend(_plain(row) for row in added)
    return result, bool(modified or retired or added), diagnostics


def _rewrite_display_order(document: dict[str, Any]) -> None:
    for index, row in enumerate(document["requirements"], 1):
        row["display_order"] = index
    for index, case in enumerate(document["test_cases"], 1):
        case["display_order"] = index
        for step_index, step in enumerate(case["steps"], 1):
            step["display_order"] = step_index
            for field in ("inputs", "outputs", "expectations"):
                for child_index, child in enumerate(step[field], 1):
                    child["display_order"] = child_index
                    if field == "expectations":
                        for assertion_index, assertion in enumerate(child["assertions"], 1):
                            assertion["display_order"] = assertion_index


def _retirement_diagnostics(document: Mapping[str, Any], delta: Mapping[str, Any]) -> list[dict[str, str]]:
    """Reject surviving references after a tombstone removes its canonical object."""
    rows: list[dict[str, str]] = []
    retired_requirements = {item["object_id"] for item in delta["requirements"]["retired"]}
    retired_capabilities = {item["object_id"] for item in delta["operation_capabilities"]["retired"]}
    for case_index, case in enumerate(document["test_cases"]):
        for requirement_id in case["requirement_ids"]:
            if requirement_id in retired_requirements:
                rows.append(_diagnostic(f"/test_cases/{case_index}/requirement_ids", "DOCUMENT_DELTA_IDENTITY", "A retired requirement still has a target case reference."))
        for step_index, step in enumerate(case["steps"]):
            operation = step["operation"]
            if operation and operation.get("kind") == "project_action" and operation.get("capability_id") in retired_capabilities:
                rows.append(_diagnostic(f"/test_cases/{case_index}/steps/{step_index}/operation/capability_id", "DOCUMENT_DELTA_IDENTITY", "A retired capability still has a target operation reference."))
    return rows


def apply_document_delta(
    baseline_document: Mapping[str, Any],
    delta: Mapping[str, Any],
    behavior_context_receipt: Mapping[str, Any],
    changed_behavior_context: Mapping[str, Any],
) -> AppliedDocumentDelta:
    """Materialize one complete candidate or prove a byte-identical no-op."""
    baseline, value = _plain(baseline_document), _plain(delta)
    receipt_value, context_value = _plain(behavior_context_receipt), _plain(changed_behavior_context)
    diagnostics = _schema(value, DELTA_SCHEMA, "")
    diagnostics.extend(_schema(receipt_value, BEHAVIOR_RECEIPT_SCHEMA, "/behavior_context_receipt"))
    diagnostics.extend(_schema(context_value, CHANGED_CONTEXT_SCHEMA, "/changed_behavior_context"))
    try:
        require_valid_canonical_document(baseline)
    except CanonicalDocumentError as error:
        diagnostics.extend(_diagnostic(str(row["path"]), "DOCUMENT_DELTA_SHAPE", "Baseline document is invalid.") for row in error.diagnostics)
    if diagnostics:
        raise DocumentDeltaError(diagnostics)
    baseline_digest = document_sha256(baseline)
    source = value["source"]
    if source["behavior_context_receipt_sha256"] != artifact_sha256(receipt_value):
        diagnostics.append(_diagnostic("/source/behavior_context_receipt_sha256", "DOCUMENT_DELTA_BINDING", "Receipt digest does not bind the supplied receipt."))
    if source["changed_behavior_context_sha256"] != artifact_sha256(context_value) or context_value["source"]["behavior_context_receipt_sha256"] != artifact_sha256(receipt_value):
        diagnostics.append(_diagnostic("/source/changed_behavior_context_sha256", "DOCUMENT_DELTA_BINDING", "Changed context does not bind the supplied receipt."))
    if receipt_value["schema_version"] != "2.0.0" or receipt_value["run_mode"] != "CHANGE_SET" or context_value["run_mode"] != "CHANGE_SET":
        diagnostics.append(_diagnostic("/source", "DOCUMENT_DELTA_BINDING", "Canonical document deltas require V2 CHANGE_SET semantic evidence."))
    if value["document_id"] != baseline["document_id"] or value["baseline_document_sha256"] != baseline_digest or value["baseline_revision"] != baseline["revision"]:
        diagnostics.append(_diagnostic("", "DOCUMENT_DELTA_BINDING", "Delta document, digest, or revision does not bind the baseline."))
    if value["requirements"]["retired"] != context_value["retired_requirements"] or value["requirements"]["retired"] != receipt_value["retired_requirements"]:
        diagnostics.append(_diagnostic("/requirements/retired", "DOCUMENT_DELTA_BINDING", "Requirement retirements must exactly match the changed context and receipt in physical order."))
    for tombstone in value["requirements"]["retired"]:
        if tombstone not in context_value["retired_requirements"] or tombstone not in receipt_value["retired_requirements"]:
            diagnostics.append(_diagnostic("/requirements/retired", "DOCUMENT_DELTA_BINDING", "Requirement retirement must be copied byte-for-byte from receipt and changed context."))
        known_fragments = {row["fragment_id"] for row in receipt_value["fragment_registry"] if row.get("effect") == "retired"}
        if not set(tombstone["support_evidence"]["retirement_fragment_ids"]).issubset(known_fragments):
            diagnostics.append(_diagnostic("/requirements/retired", "DOCUMENT_DELTA_BINDING", "Requirement retirement fragments must be promoted retired fragments."))
    changed_requirements = {row["requirement_id"]: row for row in context_value["requirements"]}
    delta_changed_requirements = {
        row["requirement_id"]: row for row in value["requirements"]["added"]
    } | {
        row["requirement_id"]: row["replacement"] for row in value["requirements"]["modified"]
    }
    if delta_changed_requirements != changed_requirements or set(receipt_value["changed_requirement_ids"]) != set(changed_requirements):
        diagnostics.append(_diagnostic("/requirements", "DOCUMENT_DELTA_BINDING", "Added and modified requirements must exactly equal the changed-context projection."))
    collections = (("operation_capabilities", "capability_id"), ("requirements", "requirement_id"), ("test_cases", "case_id"))
    materialized: dict[str, list[dict[str, Any]]] = {}
    changed = False
    for collection, key in collections:
        result, collection_changed, rows = _collection(baseline[collection], value[collection], key=key, collection=collection)
        materialized[collection] = result
        changed = changed or collection_changed
        diagnostics.extend(rows)
    if diagnostics:
        raise DocumentDeltaError(diagnostics)
    metadata_changed = value["metadata"] != baseline["metadata"]
    if not changed and not metadata_changed:
        if value["target_revision"] != baseline["revision"] or value["metadata"] != baseline["metadata"]:
            raise DocumentDeltaError([_diagnostic("", "DOCUMENT_DELTA_NOOP", "A zero-op must retain metadata and revision exactly.")])
        result = AppliedDocumentDelta("UNCHANGED", _freeze(baseline), False, artifact_sha256(value), baseline_digest)
    else:
        if value["target_revision"] != baseline["revision"] + 1:
            raise DocumentDeltaError([_diagnostic("/target_revision", "DOCUMENT_DELTA_BINDING", "A semantic delta advances exactly one revision.")])
        candidate = {
            "document_id": baseline["document_id"], "revision": value["target_revision"], "parent_sha256": baseline_digest,
            "metadata": _plain(value["metadata"]), **materialized,
        }
        _rewrite_display_order(candidate)
        diagnostics.extend(_retirement_diagnostics(candidate, value))
        try:
            require_valid_canonical_document(candidate)
        except CanonicalDocumentError as error:
            diagnostics.extend(_diagnostic(str(row["path"]), "DOCUMENT_DELTA_SHAPE", "Materialized candidate is invalid.") for row in error.diagnostics)
        if diagnostics:
            raise DocumentDeltaError(diagnostics)
        result = AppliedDocumentDelta("CHANGED", _freeze(candidate), True, artifact_sha256(value), baseline_digest)
    _APPLIED[result] = object()
    if _schema(_plain(delta_application_receipt(result)), RECEIPT_SCHEMA, ""):
        raise AssertionError("delta receipt schema drift")
    return result


def delta_application_receipt(applied: AppliedDocumentDelta) -> Mapping[str, Any]:
    """Return the closed origin receipt for a genuinely applied delta only."""
    if not isinstance(applied, AppliedDocumentDelta) or applied not in _APPLIED:
        raise DocumentDeltaError([_diagnostic("/applied", "DOCUMENT_DELTA_BINDING", "An applied delta capability is required.")])
    receipt = {
        "schema_version": "1.0.0", "artifact": "delta-application-receipt", "status": applied.status,
        "delta_sha256": applied.delta_sha256, "baseline_document_sha256": applied.baseline_document_sha256,
        "candidate_document_sha256": document_sha256(_plain(applied.candidate_document)), "publication_required": applied.publication_required,
    }
    return receipt


def unchanged_document_selection(applied: AppliedDocumentDelta, effective_baseline: ValidatedEffectiveBaseline) -> Mapping[str, Any]:
    """Build the closed zero-op carrier without exposing predecessor payloads."""
    if not isinstance(applied, AppliedDocumentDelta) or applied not in _APPLIED or applied.status != "UNCHANGED" or applied.publication_required:
        raise DocumentDeltaError([_diagnostic("/applied", "DOCUMENT_DELTA_NOOP", "Only a genuine no-op may select an unchanged document.")])
    try:
        document, receipt = effective_baseline_projection(effective_baseline)
        baseline_receipt_sha256 = effective_baseline_receipt_sha256(effective_baseline)
    except ValueError:
        raise DocumentDeltaError([_diagnostic("/baseline", "DOCUMENT_DELTA_BINDING", "An issued effective baseline is required.")]) from None
    if document_sha256(_plain(document)) != applied.baseline_document_sha256:
        raise DocumentDeltaError([_diagnostic("/baseline", "DOCUMENT_DELTA_BINDING", "Baseline document does not bind the no-op.")])
    selection = {
        "schema_version": "1.0.0", "artifact": "unchanged-document-selection",
        "baseline_receipt_sha256": baseline_receipt_sha256,
        "delta_application_receipt_sha256": artifact_sha256(delta_application_receipt(applied)),
        "effective_document_sha256": document_sha256(_plain(document)),
        "effective_bundle_receipt_sha256": artifact_sha256(asdict(receipt)),
    }
    if _schema(selection, SELECTION_SCHEMA, ""):
        raise AssertionError("unchanged selection schema drift")
    return selection
