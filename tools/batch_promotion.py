"""Immutable dual-audit authority for semantic batch results."""

from __future__ import annotations

import hashlib
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes
from tools.schema_validation import schema_diagnostics


_ROOT = Path(__file__).resolve().parents[1]
_MODE_VERSION = {"FULL": "1.0.0", "CHANGE_SET": "2.0.0"}


def _error(code: str, path: str, message: str) -> FlowError:
    return FlowError(code, path, message)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _schema(value: Any, name: str, path: str) -> None:
    diagnostics = schema_diagnostics(_plain(value), _ROOT / "schemas" / name, _ROOT)
    if diagnostics:
        raise _error("BATCH_PROMOTION_SHAPE", path + diagnostics[0]["path"], "Artifact violates its closed schema.")


@dataclass(frozen=True, eq=False)
class PromotionSnapshot:
    inputs: Mapping[str, Any]
    batch_index: int = 0
    generation: int = 1
    candidate: Mapping[str, Any] | None = None
    false_audit: Mapping[str, Any] | None = None
    omission_audit: Mapping[str, Any] | None = None
    promotions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class PromotionAction:
    kind: str
    artifact: Mapping[str, Any] | None
    next_snapshot: PromotionSnapshot | None = None
    diagnostics: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class PromotionEvidence:
    """Persisted generation records plus their final promotion authority."""

    records: tuple[Mapping[str, Any], ...]
    promotion: Mapping[str, Any]
    controller: "ReviewController | None" = None


@dataclass(frozen=True)
class ValidatedPromotion:
    promotion: Mapping[str, Any]
    result: Mapping[str, Any]
    rework_count: int


class IndependentReviewCapability:
    """Opaque controller-minted proof; serialized values cannot reproduce it."""


class ReviewController:
    """Trusted host adapter for optional fresh-context assurance."""

    @classmethod
    def independent(
        cls,
        *,
        producer_context_id: str,
        false_claim_context_id: str,
        omission_context_id: str,
        candidate: Mapping[str, Any],
        false_audit: Mapping[str, Any],
        omission_audit: Mapping[str, Any],
        fresh: bool,
        blind: bool,
    ) -> "ReviewController":
        identifiers = (producer_context_id, false_claim_context_id, omission_context_id)
        if not fresh or not blind or any(not isinstance(value, str) or not value for value in identifiers) or len(set(identifiers)) != 3:
            raise _error("PROMOTION_ASSURANCE", "/controller", "Independent review requires distinct fresh blind contexts.")
        record = {
            "candidate_sha256": artifact_sha256(_plain(candidate)),
            "false_claim_sha256": artifact_sha256(_plain(false_audit)),
            "omission_sha256": artifact_sha256(_plain(omission_audit)),
            "context_ids": list(identifiers),
        }
        capability = IndependentReviewCapability()
        _CAPABILITIES[capability] = _freeze(record)
        controller = cls()
        _CONTROLLERS[controller] = capability
        return controller


_GUARDS: weakref.WeakKeyDictionary[PromotionSnapshot, tuple[str, str]] = weakref.WeakKeyDictionary()
_CAPABILITIES: weakref.WeakKeyDictionary[IndependentReviewCapability, Mapping[str, Any]] = weakref.WeakKeyDictionary()
_CONTROLLERS: weakref.WeakKeyDictionary[ReviewController, IndependentReviewCapability] = weakref.WeakKeyDictionary()


def _guard(snapshot: PromotionSnapshot) -> None:
    expected = _GUARDS.get(snapshot)
    if expected is None or expected != (snapshot.inputs["scope_receipt_sha256"], snapshot.inputs["plan_sha256"]):
        raise _error("BATCH_PROMOTION_BINDING", "/snapshot", "Promotion snapshot was not minted by this state machine.")


def _next(snapshot: PromotionSnapshot, **changes: Any) -> PromotionSnapshot:
    values = {
        "inputs": snapshot.inputs,
        "batch_index": snapshot.batch_index,
        "generation": snapshot.generation,
        "candidate": snapshot.candidate,
        "false_audit": snapshot.false_audit,
        "omission_audit": snapshot.omission_audit,
        "promotions": snapshot.promotions,
    }
    values.update(changes)
    result = PromotionSnapshot(**values)
    _GUARDS[result] = _GUARDS[snapshot]
    return result


def _validate_plan(scope_receipt: Mapping[str, Any], plan: Mapping[str, Any]) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    _schema(scope_receipt, "change-scope-receipt.schema.json", "/scope_receipt")
    _schema(plan, "behavior-context-plan.schema.json", "/plan")
    mode = scope_receipt["run_mode"]
    version = _MODE_VERSION[mode]
    if plan.get("schema_version") != version:
        raise _error("BATCH_PROMOTION_MODE", "/plan/schema_version", "Run mode and plan version must use the closed FULL/V1 or CHANGE_SET/V2 conjunction.")
    if mode == "CHANGE_SET" and plan.get("scope_receipt_sha256") != artifact_sha256(_plain(scope_receipt)):
        raise _error("BATCH_PROMOTION_BINDING", "/plan/scope_receipt_sha256", "Change plan must bind the exact promoted scope receipt.")
    batches = tuple(plan["batches"])
    if [batch["batch_id"] for batch in batches] != [f"BATCH-{index:06d}" for index in range(1, len(batches) + 1)]:
        raise _error("BATCH_PROMOTION_ORDER", "/plan/batches", "Plan batches must use canonical physical order.")
    return mode, batches


def start_promotion(scope_receipt: Mapping[str, Any], plan: Mapping[str, Any]) -> PromotionSnapshot:
    mode, batches = _validate_plan(scope_receipt, plan)
    state = _freeze({
        "run_mode": mode,
        "result_schema_version": _MODE_VERSION[mode],
        "scope_receipt": _plain(scope_receipt),
        "scope_receipt_sha256": artifact_sha256(_plain(scope_receipt)),
        "plan": _plain(plan),
        "plan_sha256": artifact_sha256(_plain(plan)),
        "batches": _plain(batches),
    })
    result = PromotionSnapshot(state)
    _GUARDS[result] = (state["scope_receipt_sha256"], state["plan_sha256"])
    return result


def _current_batch(snapshot: PromotionSnapshot) -> Mapping[str, Any]:
    if snapshot.batch_index >= len(snapshot.inputs["batches"]):
        raise _error("BATCH_PROMOTION_ORDER", "/batch", "All planned batches are already promoted.")
    return snapshot.inputs["batches"][snapshot.batch_index]


def _result(candidate: Mapping[str, Any], snapshot: PromotionSnapshot) -> None:
    result = candidate["result"]
    _schema(result, "behavior-context-batch-result.schema.json", "/candidate/result")
    version = snapshot.inputs["result_schema_version"]
    if candidate["result_schema_version"] != version or result.get("schema_version") != version:
        raise _error("BATCH_PROMOTION_MODE", "/candidate/result_schema_version", "Candidate result version must match its run mode.")
    batch = _current_batch(snapshot)
    if result.get("batch_id") != batch["batch_id"]:
        raise _error("BATCH_PROMOTION_BINDING", "/candidate/result/batch_id", "Nested result must bind the current planned batch.")
    if version == "1.0.0":
        if result.get("plan_sha256") != snapshot.inputs["plan_sha256"]:
            raise _error("BATCH_PROMOTION_BINDING", "/candidate/result/plan_sha256", "FULL result must bind the exact V1 plan.")
    elif result.get("scope_receipt_sha256") != snapshot.inputs["scope_receipt_sha256"] or result.get("plan_sha256") != snapshot.inputs["plan_sha256"]:
        raise _error("BATCH_PROMOTION_BINDING", "/candidate/result", "CHANGE_SET result must bind the exact scope receipt and V2 plan.")
    expected_items = [row["item_id"] for row in batch["items"]]
    if [row.get("item_id") for row in result.get("items", ())] != expected_items:
        raise _error("BATCH_PROMOTION_BINDING", "/candidate/result/items", "Nested result must cover the planned batch items exactly in order.")


def _candidate(value: Mapping[str, Any], snapshot: PromotionSnapshot) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("run_mode") != snapshot.inputs["run_mode"] or value.get("result_schema_version") != snapshot.inputs["result_schema_version"] or not isinstance(value.get("result"), Mapping) or value["result"].get("schema_version") != snapshot.inputs["result_schema_version"]:
        raise _error("BATCH_PROMOTION_MODE", "/candidate", "Candidate must use the exact FULL/V1 or CHANGE_SET/V2 result conjunction.")
    _schema(value, "semantic-batch-candidate.schema.json", "/candidate")
    batch = _current_batch(snapshot)
    if value["run_mode"] != snapshot.inputs["run_mode"] or value["scope_receipt_sha256"] != snapshot.inputs["scope_receipt_sha256"] or value["plan_sha256"] != snapshot.inputs["plan_sha256"] or value["batch_id"] != batch["batch_id"]:
        raise _error("BATCH_PROMOTION_BINDING", "/candidate", "Candidate must bind the exact mode, scope, plan, and current batch.")
    rejected = snapshot.false_audit if snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK" else snapshot.omission_audit if snapshot.omission_audit is not None and snapshot.omission_audit["verdict"] == "REWORK" else None
    expected_generation = snapshot.generation + 1 if snapshot.candidate is not None else snapshot.generation
    expected_parent = artifact_sha256(snapshot.candidate) if snapshot.candidate is not None else None
    expected_triggers = [artifact_sha256(rejected)] if rejected is not None else []
    if value["generation"] != expected_generation or value["parent_candidate_sha256"] != expected_parent or list(value["triggering_audit_sha256s"]) != expected_triggers:
        raise _error("BATCH_PROMOTION_ORDER", "/candidate/generation", "Candidate must descend only from the immediately rejected generation.")
    _result(value, snapshot)
    return _freeze(_plain(value))


def _audit(value: Mapping[str, Any], candidate: Mapping[str, Any], expected_kind: str) -> Mapping[str, Any]:
    _schema(value, "semantic-batch-audit.schema.json", "/audit")
    if value["audit_kind"] != expected_kind or value["candidate_sha256"] != artifact_sha256(candidate) or value["batch_id"] != candidate["batch_id"] or value["generation"] != candidate["generation"]:
        raise _error("BATCH_PROMOTION_BINDING", "/audit", "Audit must bind the exact candidate generation and expected audit kind.")
    identifiers: set[str] = set()
    pointers: set[str] = set()
    for index, finding in enumerate(value["findings"]):
        pointer = finding["candidate_pointer"]
        parts = pointer.split("/")
        item_index = int(parts[3])
        items = candidate["result"]["items"]
        if item_index >= len(items) or finding["item_id"] != items[item_index]["item_id"]:
            raise _error("BATCH_PROMOTION_BINDING", f"/audit/findings/{index}/candidate_pointer", "Finding must point to its exact candidate result item.")
        identity = _plain(finding); identity.pop("finding_id")
        expected_id = "FINDING-" + hashlib.sha256(canonical_bytes(identity)).hexdigest()
        if finding["finding_id"] != expected_id or finding["finding_id"] in identifiers or pointer in pointers:
            raise _error("BATCH_PROMOTION_SHAPE", f"/audit/findings/{index}", "Findings require deterministic unique identities and pointers.")
        identifiers.add(finding["finding_id"]); pointers.add(pointer)
    return _freeze(_plain(value))


def record_promotion(snapshot: PromotionSnapshot, candidate_or_audit: Mapping[str, Any]) -> PromotionSnapshot:
    if not isinstance(snapshot, PromotionSnapshot) or not isinstance(candidate_or_audit, Mapping):
        raise _error("BATCH_PROMOTION_SHAPE", "/record", "A minted snapshot and closed artifact are required.")
    _guard(snapshot)
    needs_candidate = snapshot.candidate is None or (snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK") or (snapshot.omission_audit is not None and snapshot.omission_audit["verdict"] == "REWORK")
    if needs_candidate:
        candidate = _candidate(candidate_or_audit, snapshot)
        return _next(snapshot, candidate=candidate, false_audit=None, omission_audit=None, generation=candidate["generation"])
    if snapshot.false_audit is None:
        audit = _audit(candidate_or_audit, snapshot.candidate, "false_claim")
        return _next(snapshot, false_audit=audit)
    if snapshot.false_audit["verdict"] == "REWORK":
        raise _error("BATCH_PROMOTION_REWORK", "/record", "Rejected generation requires a successor candidate.")
    if snapshot.omission_audit is None:
        audit = _audit(candidate_or_audit, snapshot.candidate, "omission")
        return _next(snapshot, omission_audit=audit)
    raise _error("BATCH_PROMOTION_ORDER", "/record", "Current batch generation is already sealed.")


def _request(snapshot: PromotionSnapshot) -> Mapping[str, Any]:
    rejected = snapshot.false_audit if snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK" else snapshot.omission_audit if snapshot.omission_audit is not None and snapshot.omission_audit["verdict"] == "REWORK" else None
    return _freeze({
        "schema_version": "1.0.0", "artifact": "semantic-batch-candidate-request",
        "run_mode": snapshot.inputs["run_mode"], "scope_receipt_sha256": snapshot.inputs["scope_receipt_sha256"],
        "plan_sha256": snapshot.inputs["plan_sha256"], "batch_id": _current_batch(snapshot)["batch_id"],
        "generation": snapshot.generation + 1 if snapshot.candidate is not None else snapshot.generation,
        "parent_candidate_sha256": artifact_sha256(snapshot.candidate) if snapshot.candidate is not None else None,
        "triggering_audit_sha256s": [artifact_sha256(rejected)] if rejected is not None else [],
        "result_schema_version": snapshot.inputs["result_schema_version"],
    })


def _assurance(controller: ReviewController | None, candidate: Mapping[str, Any], false_audit: Mapping[str, Any], omission_audit: Mapping[str, Any]) -> tuple[str, str | None]:
    if controller is None:
        return "SEQUENTIAL", None
    if not isinstance(controller, ReviewController) or controller not in _CONTROLLERS:
        raise _error("PROMOTION_ASSURANCE", "/controller", "Independent assurance requires a controller-minted capability.")
    record = _CAPABILITIES.get(_CONTROLLERS[controller])
    expected = (artifact_sha256(candidate), artifact_sha256(false_audit), artifact_sha256(omission_audit))
    if record is None or (record["candidate_sha256"], record["false_claim_sha256"], record["omission_sha256"]) != expected:
        raise _error("PROMOTION_ASSURANCE", "/controller", "Independent assurance does not bind the exact candidate and audits.")
    attestation = artifact_sha256({"candidate_sha256": expected[0], "audit_sha256s": {"false_claim": expected[1], "omission": expected[2]}, "context_ids": _plain(record["context_ids"])})
    return "INDEPENDENT", attestation


def _promotion(snapshot: PromotionSnapshot, controller: ReviewController | None) -> Mapping[str, Any]:
    candidate, false_audit, omission_audit = snapshot.candidate, snapshot.false_audit, snapshot.omission_audit
    assert candidate is not None and false_audit is not None and omission_audit is not None
    if false_audit["verdict"] != "ACCEPT" or omission_audit["verdict"] != "ACCEPT":
        raise _error("BATCH_PROMOTION_REWORK", "/audits", "Both audits must accept the same generation before promotion.")
    _result(candidate, snapshot)
    review_mode, attestation = _assurance(controller, candidate, false_audit, omission_audit)
    value = {
        "schema_version":"1.0.0", "artifact":"semantic-batch-promotion", "run_mode":candidate["run_mode"],
        "scope_receipt_sha256":candidate["scope_receipt_sha256"], "plan_sha256":candidate["plan_sha256"],
        "batch_id":candidate["batch_id"], "generation":candidate["generation"], "candidate_sha256":artifact_sha256(candidate),
        "result_schema_version":candidate["result_schema_version"], "result_sha256":artifact_sha256(candidate["result"]),
        "audit_sha256s":{"false_claim":artifact_sha256(false_audit), "omission":artifact_sha256(omission_audit)},
        "review_mode":review_mode, "independence_attestation_sha256":attestation,
    }
    _schema(value, "semantic-batch-promotion.schema.json", "/promotion")
    return _freeze(value)


def advance_promotion(snapshot: PromotionSnapshot, controller: ReviewController | None = None) -> PromotionAction:
    if not isinstance(snapshot, PromotionSnapshot):
        raise _error("BATCH_PROMOTION_SHAPE", "/snapshot", "A minted promotion snapshot is required.")
    _guard(snapshot)
    if snapshot.batch_index == len(snapshot.inputs["batches"]):
        return PromotionAction("COMPLETE", _freeze({"promotions": snapshot.promotions}))
    if snapshot.candidate is None or (snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK") or (snapshot.omission_audit is not None and snapshot.omission_audit["verdict"] == "REWORK"):
        return PromotionAction("PRODUCE_BATCH_CANDIDATE", _request(snapshot))
    if snapshot.false_audit is None:
        return PromotionAction("RUN_BATCH_FALSE_CLAIM_AUDIT", snapshot.candidate)
    if snapshot.omission_audit is None:
        return PromotionAction("RUN_BATCH_OMISSION_AUDIT", snapshot.candidate)
    promotion = _promotion(snapshot, controller)
    next_snapshot = _next(snapshot, batch_index=snapshot.batch_index + 1, generation=1, candidate=None, false_audit=None, omission_audit=None, promotions=(*snapshot.promotions, promotion))
    return PromotionAction("PROMOTE_BATCH", promotion, next_snapshot)


def replay_promotion_ledger(
    scope_receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    evidence: tuple[PromotionEvidence, ...],
) -> tuple[ValidatedPromotion, ...]:
    """Replay every planned batch in order without resetting state between batches."""
    snapshot = start_promotion(scope_receipt, plan)
    if len(evidence) != len(snapshot.inputs["batches"]):
        raise _error("PROMOTION_COVERAGE", "/promotion_evidence", "Promotion evidence must cover every planned batch exactly once.")
    result: list[ValidatedPromotion] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, PromotionEvidence) or not item.records:
            raise _error("BATCH_PROMOTION_SHAPE", f"/promotion_evidence/{index}", "Every batch requires persisted generation records.")
        for record in item.records:
            snapshot = record_promotion(snapshot, record)
        action = advance_promotion(snapshot, item.controller)
        if action.kind != "PROMOTE_BATCH" or _plain(action.artifact) != _plain(item.promotion) or action.next_snapshot is None:
            raise _error("BATCH_PROMOTION_BINDING", f"/promotion_evidence/{index}/promotion", "Persisted records do not reproduce the exact promotion.")
        assert snapshot.candidate is not None
        result.append(ValidatedPromotion(
            _freeze(_plain(item.promotion)), _freeze(_plain(snapshot.candidate["result"])),
            snapshot.candidate["generation"] - 1,
        ))
        snapshot = action.next_snapshot
    complete = advance_promotion(snapshot)
    if complete.kind != "COMPLETE":
        raise _error("PROMOTION_COVERAGE", "/promotion_evidence", "Promotion ledger did not reach exact completion.")
    return tuple(result)
