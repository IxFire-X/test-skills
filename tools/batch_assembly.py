"""Controller-owned deterministic batch planning and candidate assembly."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from tools.canonical_document import validate_canonical_document
from tools.schema_validation import loads_json_strict, schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_PLAN_SCHEMA = _ROOT / "schemas" / "batch-plan.schema.json"
_FRAGMENT_SCHEMA = _ROOT / "schemas" / "candidate-fragment.schema.json"
_ASSEMBLY_SCHEMA = _ROOT / "schemas" / "assembly-receipt.schema.json"
_CONTEXT_SCHEMA = _ROOT / "schemas" / "context-selection-receipt.schema.json"


class BatchAssemblyError(ValueError):
    """A stable controller-contract failure which must not invoke review."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _with_digest(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["digest"] = _digest(result)
    return result


def _schema_valid(value: dict[str, Any], schema: Path, label: str) -> None:
    diagnostics = schema_diagnostics(value, schema, _ROOT)
    if diagnostics:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} schema is invalid: {diagnostics[0]['code']}")


def _self_digest_valid(value: dict[str, Any], label: str) -> None:
    if value.get("digest") != _digest({key: item for key, item in value.items() if key != "digest"}):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} digest is invalid")


def _digest_value(value: Any, field: str = "digest") -> str:
    if isinstance(value, dict) and isinstance(value.get(field), str):
        return value[field]
    raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"missing {field}")


def _source_requirements(source_requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(source_requirements, list) or not source_requirements:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "source requirements must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in source_requirements:
        source_id = item.get("source_requirement_id") if isinstance(item, dict) else None
        if not isinstance(source_id, str) or not source_id.startswith("SREQ-") or source_id in by_id:
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "source requirement IDs must be unique SREQ IDs")
        display_order = item.get("display_order")
        if type(display_order) is not int or display_order < 1 or not isinstance(item.get("text"), str) or not item["text"].strip() or not isinstance(item.get("provenance"), list) or not item["provenance"] or not all(isinstance(value, str) and value.strip() for value in item["provenance"]) or not isinstance(item.get("digest"), str):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"source requirement {source_id} lacks exact provenance/digest")
        by_id[source_id] = deepcopy(item)
    physical = sorted(by_id.values(), key=lambda item: item["display_order"])
    if [item["display_order"] for item in physical] != list(range(1, len(physical) + 1)):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "source requirement display_order must be contiguous 1..N")
    return physical


def _evidence_digests(grouping_evidence: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(grouping_evidence, dict):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "grouping evidence must be an object")
    return _digest_value(grouping_evidence, "header_digest"), _digest_value(grouping_evidence, "context_receipt_digest")


def _split_groups(ids: set[str], order: dict[str, int], evidence: dict[str, Any]) -> list[list[str]] | None:
    groups = evidence.get("groups")
    if not (evidence.get("complete") is True and evidence.get("independence_proven") is True and isinstance(groups, list) and groups):
        return None
    partitions: list[tuple[tuple[str, ...], list[str]]] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str) or not group["group_id"]:
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "each split group needs a stable group_id")
        members = group.get("source_requirement_ids")
        proof = group.get("independence_evidence")
        if not isinstance(members, list) or not members or not isinstance(proof, list) or not proof:
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "split group lacks complete explicit independence evidence")
        if any(not isinstance(member, str) or member not in ids for member in members):
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "split group names an unknown source requirement")
        normalized = sorted(members, key=order.__getitem__)
        if len(set(normalized)) != len(normalized) or seen.intersection(normalized):
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "split groups overlap or name an unknown source requirement")
        seen.update(normalized)
        partitions.append((tuple(order[member] for member in normalized), normalized))
    if seen != ids:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "split groups do not completely partition source requirements")
    return [members for _sort, members in sorted(partitions)]


def plan_batches(source_requirements: list[dict[str, Any]], grouping_evidence: dict[str, Any]) -> dict[str, Any]:
    """Create one complete partition; split only from complete explicit evidence."""
    source = _source_requirements(source_requirements)
    header_digest, context_digest = _evidence_digests(grouping_evidence)
    ids = {item["source_requirement_id"] for item in source}
    physical_order = {item["source_requirement_id"]: item["display_order"] for item in source}
    groups = _split_groups(ids, physical_order, grouping_evidence) or [[item["source_requirement_id"] for item in source]]
    batches = []
    for ordinal, members in enumerate(groups, start=1):
        batch_seed = {"ordinal": ordinal, "owned_source_requirement_ids": members, "header_digest": header_digest, "context_receipt_digest": context_digest}
        batches.append({
            "batch_id": "BATCH-" + hashlib.sha256(canonical_bytes(batch_seed)).hexdigest()[:16],
            "ordinal": ordinal,
            "namespace": f"B{ordinal}",
            "owned_source_requirement_ids": members,
            "header_digest": header_digest,
            "context_receipt_digest": context_digest,
            "completeness": "COMPLETE",
        })
    plan = _with_digest({"schema_version": "1.0.0", "source_requirements": source, "header_digest": header_digest, "context_receipt_digest": context_digest, "batches": batches})
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan")
    return plan


def _plan_batches(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "plan must be an object")
    _schema_valid(plan, _PLAN_SCHEMA, "batch plan")
    _self_digest_valid(plan, "plan")
    batches = plan.get("batches")
    if not isinstance(batches, list) or not batches:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "plan has no batches")
    by_id = {item.get("batch_id"): item for item in batches if isinstance(item, dict)}
    if len(by_id) != len(batches) or None in by_id:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "batch IDs must be unique")
    owned = [source_id for batch in batches for source_id in batch.get("owned_source_requirement_ids", [])]
    expected = [item.get("source_requirement_id") for item in plan.get("source_requirements", [])]
    if sorted(owned) != sorted(expected) or len(set(owned)) != len(owned):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "plan ownership is not an exact partition")
    return by_id


def _fragment_requirements(fragment: dict[str, Any]) -> set[str]:
    items = fragment.get("requirements")
    if not isinstance(items, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment requirements must be a list")
    ids = [item.get("requirement_id") for item in items if isinstance(item, dict)]
    if len(ids) != len(items) or any(not isinstance(item, str) or not item.startswith("CREQ-") for item in ids) or len(set(ids)) != len(ids):
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "canonical requirement IDs must be unique CREQ IDs")
    return set(ids)


def _validate_context_receipt(receipt: dict[str, Any], expected_digest: str) -> None:
    if not isinstance(receipt, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "context receipt must be an object")
    _schema_valid(receipt, _CONTEXT_SCHEMA, "context receipt")
    _self_digest_valid(receipt, "context receipt")
    if receipt["digest"] != expected_digest:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "context receipt does not match batch binding")


def _require_namespaced_identifiers(fragment: dict[str, Any]) -> None:
    namespace = fragment["namespace"]
    prefixes = {
        "requirement_id": f"CREQ-{namespace}-", "case_id": f"TC-{namespace}-", "step_id": f"STEP-{namespace}-",
        "input_id": f"INPUT-{namespace}-", "expectation_id": f"EXP-{namespace}-", "assertion_id": f"ASSERT-{namespace}-", "blocker_id": f"BLOCK-{namespace}-",
    }
    def require(item: dict[str, Any], key: str) -> None:
        value = item.get(key)
        if not isinstance(value, str) or not value.startswith(prefixes[key]):
            raise BatchAssemblyError("BATCH_ID_CONFLICT", f"{key} does not use namespace {namespace}")
    for requirement in fragment["requirements"]:
        require(requirement, "requirement_id")
    for case in fragment["test_cases"]:
        require(case, "case_id")
        for step in case["steps"]:
            require(step, "step_id")
            for item in step["inputs"]:
                require(item, "input_id")
            for blocker in step["automation_blockers"]:
                require(blocker, "blocker_id")
            for expectation in step["expectations"]:
                require(expectation, "expectation_id")
                for assertion in expectation["assertions"]:
                    require(assertion, "assertion_id")


def validate_fragment(fragment: dict[str, Any], plan: dict[str, Any], header: dict[str, Any], context_receipt: dict[str, Any]) -> None:
    """Validate a complete or failed fragment against controller-owned bindings."""
    batches = _plan_batches(plan)
    if not isinstance(fragment, dict):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment must be an object")
    _schema_valid(fragment, _FRAGMENT_SCHEMA, "candidate fragment")
    _self_digest_valid(fragment, "candidate fragment")
    batch = batches.get(fragment.get("batch_id"))
    if batch is None:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment batch_id is not in plan")
    if fragment.get("namespace") != batch["namespace"]:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", "fragment namespace does not match plan")
    if fragment.get("plan_digest") != plan["digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment plan digest does not match plan")
    header_bytes = {key: value for key, value in header.items() if key != "digest"}
    if fragment.get("header_digest") != batch["header_digest"] or _digest(header_bytes) != batch["header_digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment/header digest binding is invalid")
    if fragment.get("context_receipt_digest") != batch["context_receipt_digest"]:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment/context digest binding is invalid")
    _validate_context_receipt(context_receipt, batch["context_receipt_digest"])
    _validate_context_receipt(fragment["context_receipt"], batch["context_receipt_digest"])
    if fragment["context_receipt"] != context_receipt:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment context receipt differs from verified receipt")
    if fragment.get("owned_source_requirement_ids") != batch["owned_source_requirement_ids"]:
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "fragment ownership differs from plan")
    if fragment.get("status") == "FAILED":
        if not isinstance(fragment.get("failure"), dict):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failed fragment lacks immutable failure evidence")
        return
    if fragment.get("status") != "COMPLETE":
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment status is invalid")
    _require_namespaced_identifiers(fragment)
    requirement_ids = _fragment_requirements(fragment)
    mappings = fragment.get("source_to_canonical_mappings")
    if not isinstance(mappings, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment mappings must be a list")
    mapping_sources = [item.get("source_requirement_id") for item in mappings if isinstance(item, dict)]
    if sorted(mapping_sources) != sorted(batch["owned_source_requirement_ids"]) or len(set(mapping_sources)) != len(mapping_sources):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "mappings must cover exactly the owned source requirements")
    mapped_canonical: set[str] = set()
    for mapping in mappings:
        targets = mapping.get("canonical_requirement_ids") if isinstance(mapping, dict) else None
        if not isinstance(targets, list) or not targets or any(target not in requirement_ids for target in targets):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "mapping references an unknown canonical requirement")
        mapped_canonical.update(targets)
    if mapped_canonical != requirement_ids:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "every canonical requirement must have source provenance")
    cases = fragment.get("test_cases")
    if not isinstance(cases, list):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "fragment cases must be a list")
    for case in cases:
        references = case.get("requirement_ids") if isinstance(case, dict) else None
        if not isinstance(references, list) or not references or any(item not in requirement_ids for item in references):
            raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "case references a canonical requirement outside its batch")


def _unique_by_id(items: list[dict[str, Any]], key: str, code: str, *, permit_equal: bool = False) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item.get(key)
        if not isinstance(item_id, str):
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"missing {key}")
        existing = result.get(item_id)
        if existing is not None:
            if canonical_bytes(existing) != canonical_bytes(item):
                raise BatchAssemblyError(code, f"{key} {item_id} has different bytes")
            if permit_equal:
                continue
            raise BatchAssemblyError("BATCH_ID_CONFLICT", f"duplicate {key} {item_id}")
        result[item_id] = deepcopy(item)
    return result


def _case_fingerprint(case: dict[str, Any]) -> str:
    def without_identity(value: Any) -> Any:
        if isinstance(value, list):
            return [without_identity(item) for item in value]
        if isinstance(value, dict):
            return {key: without_identity(item) for key, item in value.items() if key != "display_order" and key != "requirement_ids" and not key.endswith("_id")}
        return value
    return _digest(without_identity(case))


def assemble_candidate(header: dict[str, Any], plan: dict[str, Any], fragments: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble complete ordered fragments without semantic mutation."""
    batches = _plan_batches(plan)
    if len(fragments) != len(batches):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "there must be exactly one fragment per batch")
    by_batch: dict[str, dict[str, Any]] = {}
    for fragment in fragments:
        validate_fragment(fragment, plan, header, fragment.get("context_receipt"))
        batch_id = fragment["batch_id"]
        if batch_id in by_batch:
            raise BatchAssemblyError("BATCH_ID_CONFLICT", "multiple fragments for one batch")
        by_batch[batch_id] = fragment
    if set(by_batch) != set(batches):
        raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "fragments do not cover the complete plan")
    ordered = [by_batch[item["batch_id"]] for item in sorted(batches.values(), key=lambda item: item["ordinal"])]
    if any(fragment["status"] == "FAILED" for fragment in ordered):
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failed fragment cannot assemble a canonical candidate")
    capabilities = _unique_by_id([item for fragment in ordered for item in fragment["operation_capabilities"]], "capability_id", "BATCH_ID_CONFLICT", permit_equal=True)
    requirements = _unique_by_id([item for fragment in ordered for item in fragment["requirements"]], "requirement_id", "BATCH_ID_CONFLICT")
    cases = _unique_by_id([item for fragment in ordered for item in fragment["test_cases"]], "case_id", "BATCH_ID_CONFLICT")
    canonical_owners: dict[str, str] = {}
    for fragment in ordered:
        for mapping in fragment["source_to_canonical_mappings"]:
            for canonical_id in mapping["canonical_requirement_ids"]:
                owner = canonical_owners.setdefault(canonical_id, fragment["batch_id"])
                if owner != fragment["batch_id"]:
                    raise BatchAssemblyError("BATCH_PARTITION_CONFLICT", "one canonical requirement cannot span batches")
    mappings = [deepcopy(item) for fragment in ordered for item in fragment["source_to_canonical_mappings"]]
    document = {key: deepcopy(value) for key, value in header.items() if key != "digest"}
    document.update({
        "source_requirements": deepcopy(plan["source_requirements"]),
        "source_to_canonical_mappings": mappings,
        "operation_capabilities": list(capabilities.values()),
        "requirements": list(requirements.values()),
        "test_cases": list(cases.values()),
    })
    for name in ("requirements", "test_cases"):
        for index, item in enumerate(document[name], start=1):
            item["display_order"] = index
    diagnostics = validate_canonical_document(document)
    if diagnostics:
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"canonical pre-review audit failed: {diagnostics[0]['code']}")
    fingerprints: dict[str, list[str]] = defaultdict(list)
    for case in document["test_cases"]:
        fingerprints[_case_fingerprint(case)].append(case["case_id"])
    warnings = [{"code": "POSSIBLE_DUPLICATE", "fingerprint": fingerprint, "case_ids": sorted(case_ids)} for fingerprint, case_ids in sorted(fingerprints.items()) if len(case_ids) > 1]
    receipt = _with_digest({
        "schema_version": "1.0.0", "plan_digest": plan["digest"], "header_digest": plan["header_digest"],
        "fragment_digests": [{"batch_id": fragment["batch_id"], "digest": _digest(fragment)} for fragment in ordered],
        "document_digest": _digest(document), "pre_review_audit": {"schema": "PASS", "semantic": "PASS", "coverage": "PASS", "provenance": "PASS"}, "warnings": warnings,
    })
    _schema_valid(receipt, _ASSEMBLY_SCHEMA, "assembly receipt")
    _self_digest_valid(receipt, "assembly receipt")
    return document, receipt


def _publish_immutable_json(root: Path, directory_name: str, batch_id: str, value: dict[str, Any], label: str) -> dict[str, Any]:
    directory = root / directory_name
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} directory is unsafe")
    target = directory / f"{batch_id}.json"
    payload = canonical_bytes(value)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as error:
        raise BatchAssemblyError("BATCH_ID_CONFLICT", f"immutable {label} already exists") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        raw = target.read_bytes()
        readback = loads_json_strict(raw.decode("utf-8"))
        if raw != payload or readback != value:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", f"{label} readback differs from published bytes")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path": str(target), "digest": value["digest"], "byte_digest": "sha256:" + hashlib.sha256(payload).hexdigest(), "bytes": payload, "readback": readback}


def publish_fragment(attempt_artifact_dir: Path, fragment: dict[str, Any]) -> dict[str, Any]:
    """Create/read back a fragment and its failed-model receipt under a caller-owned attempt root."""
    _schema_valid(fragment, _FRAGMENT_SCHEMA, "candidate fragment")
    _self_digest_valid(fragment, "candidate fragment")
    root = Path(attempt_artifact_dir).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "attempt artifact directory is unsafe")
    model_publication = None
    if fragment["status"] == "FAILED":
        failure = fragment["failure"]
        model_receipt = failure["model_receipt"]
        _self_digest_valid(model_receipt, "model receipt")
        if failure["evidence_digest"] != model_receipt["digest"]:
            raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "failure evidence digest does not bind model receipt")
        model_publication = _publish_immutable_json(root, "model-receipts", fragment["batch_id"], model_receipt, "model receipt")
    fragment_publication = _publish_immutable_json(root, "fragments", fragment["batch_id"], fragment, "fragment")
    if model_publication is not None:
        fragment_publication["model_receipt"] = model_publication
    return fragment_publication
