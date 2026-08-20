"""Reviewer successor lineage checks and effective canonical-document selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tools.canonical_document import document_sha256
from tools.baseline_lifecycle import ValidatedEffectiveBaseline, effective_baseline_projection
from tools.document_delta import AppliedDocumentDelta
from tools.flow_artifacts import artifact_sha256
from tools.publish_test_case_bundle import Receipt


def _diagnostic(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _sorted(diagnostics: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))


class SelectionError(ValueError):
    """Raised when a reviewer report cannot select an effective document."""

    def __init__(self, diagnostics: Sequence[dict[str, str]]) -> None:
        ordered = _sorted([dict(item) for item in diagnostics])
        self.diagnostics = tuple(MappingProxyType(item) for item in ordered)
        super().__init__(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class _Identity:
    key: tuple[str, ...]
    family: str
    local: str
    parent: tuple[str, ...] | None
    collection_path: str
    object_path: str


def _identity_graph(document: dict[str, Any]) -> dict[tuple[str, ...], _Identity]:
    """Extract stable identities independently from physical array positions."""
    nodes: dict[tuple[str, ...], _Identity] = {}

    def add(key: tuple[str, ...], family: str, local: str, parent: tuple[str, ...] | None, path: str, object_path: str) -> None:
        nodes[key] = _Identity(key, family, local, parent, path, object_path)

    for capability_index, capability in enumerate(document["operation_capabilities"]):
        capability_key = ("capability", capability["capability_id"])
        base = f"/operation_capabilities/{capability_index}"
        add(capability_key, "capability", capability["capability_id"], None, "/operation_capabilities", base)
        for role in ("arguments", "results"):
            for member_index, member in enumerate(capability[role]):
                add(
                    ("capability_member", capability["capability_id"], role, member["name"]),
                    "capability_member", member["name"], capability_key, f"{base}/{role}", f"{base}/{role}/{member_index}",
                )
    for requirement_index, requirement in enumerate(document["requirements"]):
        add(("requirement", requirement["requirement_id"]), "requirement", requirement["requirement_id"], None, "/requirements", f"/requirements/{requirement_index}")
    for case_index, case in enumerate(document["test_cases"]):
        case_key = ("case", case["case_id"])
        case_path = f"/test_cases/{case_index}"
        add(case_key, "case", case["case_id"], None, "/test_cases", case_path)
        for step_index, step in enumerate(case["steps"]):
            step_key = ("step", case["case_id"], step["step_id"])
            step_path = f"{case_path}/steps/{step_index}"
            add(step_key, "step", step["step_id"], case_key, f"{case_path}/steps", step_path)
            for input_index, input_ in enumerate(step["inputs"]):
                add(("input", case["case_id"], step["step_id"], input_["input_id"]), "input", input_["input_id"], step_key, f"{step_path}/inputs", f"{step_path}/inputs/{input_index}")
            for output_index, output in enumerate(step["outputs"]):
                add(("output", case["case_id"], step["step_id"], output["output_id"]), "output", output["output_id"], step_key, f"{step_path}/outputs", f"{step_path}/outputs/{output_index}")
            for blocker_index, blocker in enumerate(step["automation_blockers"]):
                add(("blocker", case["case_id"], step["step_id"], blocker["blocker_id"]), "blocker", blocker["blocker_id"], step_key, f"{step_path}/automation_blockers", f"{step_path}/automation_blockers/{blocker_index}")
            for expectation_index, expectation in enumerate(step["expectations"]):
                expectation_key = ("expectation", case["case_id"], step["step_id"], expectation["expectation_id"])
                expectation_path = f"{step_path}/expectations/{expectation_index}"
                add(expectation_key, "expectation", expectation["expectation_id"], step_key, f"{step_path}/expectations", expectation_path)
                for assertion_index, assertion in enumerate(expectation["assertions"]):
                    add(("assertion", case["case_id"], step["step_id"], expectation["expectation_id"], assertion["assertion_id"]), "assertion", assertion["assertion_id"], expectation_key, f"{expectation_path}/assertions", f"{expectation_path}/assertions/{assertion_index}")
    return nodes


def _successor_diagnostics(candidate: dict[str, Any], successor: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    candidate_digest = document_sha256(candidate)
    if successor["document_id"] != candidate["document_id"]:
        diagnostics.append(_diagnostic("/document_id", "SUCCESSOR_DOCUMENT_ID", "successor document_id must equal candidate document_id"))
    if successor["revision"] != candidate["revision"] + 1:
        diagnostics.append(_diagnostic("/revision", "SUCCESSOR_REVISION", "successor revision must equal candidate revision plus one"))
    if successor["parent_sha256"] != candidate_digest:
        diagnostics.append(_diagnostic("/parent_sha256", "SUCCESSOR_PARENT_SHA256", "successor parent_sha256 must equal the bare candidate document digest"))
    if diagnostics:
        return diagnostics

    source, revised = _identity_graph(candidate), _identity_graph(successor)
    revised_by_local: dict[tuple[str, str], list[_Identity]] = {}
    revised_children: dict[tuple[str, ...] | None, list[_Identity]] = {}
    source_children: dict[tuple[str, ...] | None, list[_Identity]] = {}
    for node in revised.values():
        revised_by_local.setdefault((node.family, node.local), []).append(node)
        revised_children.setdefault(node.parent, []).append(node)
    for node in source.values():
        source_children.setdefault(node.parent, []).append(node)

    missing: set[tuple[str, ...]] = set()
    for key, node in source.items():
        if key in revised:
            continue
        if node.parent is not None and node.parent in missing:
            missing.add(key)
            continue
        missing.add(key)
        moved = any(
            other.parent != node.parent
            or (node.family == "capability_member" and other.key[2] != node.key[2])
            for other in revised_by_local.get((node.family, node.local), [])
        )
        source_siblings = [other for other in source_children.get(node.parent, []) if other.family == node.family]
        revised_siblings = [other for other in revised_children.get(node.parent, []) if other.family == node.family]
        replaced = len(revised_siblings) >= len(source_siblings)
        code = "IDENTITY_GRAPH_CHANGED" if moved or replaced else "IDENTITY_REMOVED"
        message = "stable identity changed parent, role, or sibling key" if code == "IDENTITY_GRAPH_CHANGED" else "stable identity was removed"
        if node.parent is None:
            path = node.collection_path
        else:
            parent = revised[node.parent]
            path = f"{parent.object_path}/{node.collection_path.rsplit('/', 1)[-1]}"
        diagnostics.append(_diagnostic(path, code, message))
    return diagnostics


def validate_successor(candidate: dict[str, Any], successor: dict[str, Any]) -> list[dict[str, str]]:
    """Return deterministic lineage and identity-preservation diagnostics."""
    return _sorted(_successor_diagnostics(candidate, successor))


def _report_diagnostics(candidate: dict[str, Any], report: Any) -> tuple[list[dict[str, str]], Any]:
    report_object = report if isinstance(report, dict) else {}
    report_candidate = report_object.get("candidate")
    report_candidate = report_candidate if isinstance(report_candidate, dict) else {}
    diagnostics: list[dict[str, str]] = []
    if report_candidate.get("document_id") != candidate["document_id"]:
        diagnostics.append(_diagnostic("/candidate/document_id", "REVIEW_CANDIDATE_DOCUMENT_ID", "review report candidate document_id does not match"))
    if type(report_candidate.get("revision")) is not int or report_candidate["revision"] != candidate["revision"]:
        diagnostics.append(_diagnostic("/candidate/revision", "REVIEW_CANDIDATE_REVISION", "review report candidate revision does not match"))
    if report_candidate.get("document_sha256") != document_sha256(candidate):
        diagnostics.append(_diagnostic("/candidate/document_sha256", "REVIEW_CANDIDATE_DOCUMENT_SHA256", "review report candidate digest must be the exact bare-document digest"))
    expected_case_ids = [case["case_id"] for case in candidate["test_cases"]]
    if report_object.get("reviewed_case_ids") != expected_case_ids:
        diagnostics.append(_diagnostic("/reviewed_case_ids", "REVIEWED_CASE_IDS_MISMATCH", "reviewed_case_ids must exactly match candidate case IDs in canonical physical order"))
    verdict = report_object.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"ПРИНЯТО", "AUTO_FIX_APPLIED", "ТРЕБУЕТ ДОРАБОТКИ"}:
        diagnostics.append(_diagnostic("/verdict", "REVIEW_VERDICT", "review verdict is unknown"))
    return diagnostics, verdict


def select_effective_document(candidate: dict[str, Any], validation_report: dict[str, Any], successor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Select the exact reviewed candidate or valid successor; never merge documents."""
    diagnostics, verdict = _report_diagnostics(candidate, validation_report)
    if diagnostics:
        raise SelectionError(diagnostics)
    if verdict == "ПРИНЯТО":
        if successor is not None:
            raise SelectionError([_diagnostic("/successor_document", "SUCCESSOR_FORBIDDEN", "successor is forbidden for an accepted review")])
        return candidate
    if verdict == "AUTO_FIX_APPLIED":
        if successor is None:
            raise SelectionError([_diagnostic("/successor_document", "SUCCESSOR_REQUIRED", "successor is required for AUTO_FIX_APPLIED")])
        diagnostics = validate_successor(candidate, successor)
        if diagnostics:
            raise SelectionError(diagnostics)
        return successor
    if successor is not None:
        raise SelectionError([_diagnostic("/successor_document", "SUCCESSOR_FORBIDDEN", "successor is forbidden for a rework review")])
    raise SelectionError([_diagnostic("/verdict", "NO_EFFECTIVE_DOCUMENT", "rework review has no effective document")])


def select_unchanged_document(applied: AppliedDocumentDelta, validated_baseline: ValidatedEffectiveBaseline) -> tuple[Mapping[str, Any], Receipt]:
    """Return the exact predecessor only for a minted no-op/baseline pair."""
    if not isinstance(applied, AppliedDocumentDelta) or applied.status != "UNCHANGED" or applied.publication_required:
        raise SelectionError([_diagnostic("/applied", "DOCUMENT_DELTA_NOOP", "Only a no-op application can bypass publication.")])
    try:
        from tools.document_delta import unchanged_document_selection
        unchanged_document_selection(applied, validated_baseline)
        document, receipt = effective_baseline_projection(validated_baseline)
    except ValueError:
        raise SelectionError([_diagnostic("/baseline", "DOCUMENT_DELTA_BINDING", "An issued effective baseline is required.")]) from None
    if artifact_sha256(document) != applied.baseline_document_sha256 or artifact_sha256(document) != artifact_sha256(applied.candidate_document):
        raise SelectionError([_diagnostic("/baseline", "DOCUMENT_DELTA_BINDING", "Baseline bytes do not match the no-op application.")])
    return document, receipt
