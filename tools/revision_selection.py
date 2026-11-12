"""Reviewer successor lineage checks and effective canonical-document selection."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Sequence

from tools.canonical_document import canonical_bytes, document_sha256


def _diagnostic(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _sorted(diagnostics: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))


def reviewer_verdict_is_bound(report: Any, verdict_event: Any) -> bool:
    """Return whether one authoritative event names the exact reviewer report."""
    if not isinstance(report, dict) or not isinstance(verdict_event, dict):
        return False
    normalized = {
        "ПРИНЯТО": "ACCEPTED",
        "AUTO_FIX_APPLIED": "ACCEPTED",
        "ТРЕБУЕТ ДОРАБОТКИ": verdict_event.get("verdict") if verdict_event.get("verdict") in {"ACCEPTED", "REJECTED"} else None,  # which: the outcome, see partial_is_bound
    }.get(report.get("verdict"))
    report_digest = "sha256:" + hashlib.sha256(canonical_bytes(report)).hexdigest()
    verdict_digest = "sha256:" + hashlib.sha256(canonical_bytes({
        "verdict": report.get("verdict"), "effective": report.get("effective"),
    })).hexdigest()
    return normalized is not None and (
        verdict_event.get("verdict"), verdict_event.get("report_digest"),
        verdict_event.get("verdict_digest"),
    ) == (normalized, report_digest, verdict_digest)


def effective_review_is_bound(
    candidate_digest: Any,
    effective_document: Any,
    report: Any,
    successor: Any,
    verdict_event: Any,
    candidate: Any = None,
    canaries: Sequence[Any] = (),
) -> bool:
    """Bind one accepted durable effective document to the reviewer decision."""
    if (
        not isinstance(candidate_digest, str)
        or not isinstance(effective_document, dict)
        or not isinstance(report, dict)
        or not reviewer_verdict_is_bound(report, verdict_event)
    ):
        return False
    if report.get("verdict") == "ТРЕБУЕТ ДОРАБОТКИ":  # sent back and subtracted by the code: ACCEPTED in the ledger, and the set is exactly the reviewer's
        return (isinstance(candidate, dict) and document_sha256(candidate) == candidate_digest and verdict_event.get("verdict") == "ACCEPTED"
                and successor is None and partial_is_bound(candidate, report, canaries, effective_document))
    expected_effective = {
        "document_id": effective_document.get("document_id"),
        "revision": effective_document.get("revision"),
        "document_sha256": document_sha256(effective_document),
    }
    if (
        not isinstance(report.get("candidate"), dict)
        or report["candidate"].get("document_sha256") != candidate_digest
        or report.get("effective") != expected_effective
    ):
        return False
    return (
        report.get("verdict") == "ПРИНЯТО"
        and successor is None
        and candidate_digest == expected_effective["document_sha256"]
    ) or (
        report.get("verdict") == "AUTO_FIX_APPLIED"
        and isinstance(successor, dict)
        and successor == effective_document
    )


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


def _machine_contract(document: dict[str, Any]) -> dict[str, Any]:
    """Project confirmed trace/machine fields that a reviewer may not rewrite."""
    cases: dict[str, Any] = {}
    for case in document["test_cases"]:
        steps: dict[str, Any] = {}
        for step in case["steps"]:
            expectations = {
                expectation["expectation_id"]: {
                    assertion["assertion_id"]: {
                        key: assertion[key]
                        for key in ("actual", "operator", "expected")
                        if key in assertion
                    }
                    for assertion in expectation["assertions"]
                }
                for expectation in step["expectations"]
            }
            steps[step["step_id"]] = {
                "manual_only": step["manual_only"],
                "automation_blockers": step["automation_blockers"],
                "operation": step["operation"],
                "inputs": {
                    item["input_id"]: {
                        key: item[key]
                        for key in ("target", "source", "semantic_type", "type_provenance")
                        if key in item
                    }
                    for item in step["inputs"]
                },
                "outputs": {
                    item["output_id"]: {
                        key: item[key]
                        for key in ("source", "semantic_type", "type_provenance")
                        if key in item
                    }
                    for item in step["outputs"]
                },
                "assertions": expectations,
            }
        cases[case["case_id"]] = {"requirement_ids": case["requirement_ids"], "steps": steps}
    return {"operation_capabilities": document["operation_capabilities"], "cases": cases}


def _successor_diagnostics(candidate: dict[str, Any], successor: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    candidate_digest = document_sha256(candidate)
    if candidate["revision"] != 1:
        diagnostics.append(_diagnostic("/revision", "REVISION_BUDGET", "only an assembled revision 1 may produce the optional revision 2 successor"))
    if successor["document_id"] != candidate["document_id"]:
        diagnostics.append(_diagnostic("/document_id", "SUCCESSOR_DOCUMENT_ID", "successor document_id must equal candidate document_id"))
    if successor["revision"] != candidate["revision"] + 1:
        diagnostics.append(_diagnostic("/revision", "SUCCESSOR_REVISION", "successor revision must equal candidate revision plus one"))
    if successor["parent_sha256"] != candidate_digest:
        diagnostics.append(_diagnostic("/parent_sha256", "SUCCESSOR_PARENT_SHA256", "successor parent_sha256 must equal the bare candidate document digest"))
    if diagnostics:
        return diagnostics
    if successor.get("source_requirements") != candidate.get("source_requirements") or successor.get("source_to_canonical_mappings") != candidate.get("source_to_canonical_mappings"):
        return [_diagnostic("/source_to_canonical_mappings", "TRACEABILITY_CHANGED", "a reviewer successor must preserve source/canonical mappings exactly")]
    candidate_cases = {case["case_id"]: case["requirement_ids"] for case in candidate["test_cases"]}
    successor_cases = {case["case_id"]: case["requirement_ids"] for case in successor["test_cases"]}
    if successor_cases != candidate_cases or [item["requirement_id"] for item in successor["requirements"]] != [item["requirement_id"] for item in candidate["requirements"]] \
            or {case["case_id"]: case["display_order"] for case in successor["test_cases"]} != {case["case_id"]: case["display_order"] for case in candidate["test_cases"]}:
        return [_diagnostic("/test_cases", "TRACEABILITY_CHANGED", "a reviewer successor must preserve canonical requirement ownership and the order of every case")]
    if _machine_contract(successor) != _machine_contract(candidate):
        return [_diagnostic("/test_cases", "MACHINE_SEMANTICS_CHANGED", "a reviewer successor cannot rewrite confirmed operation, input, output, assertion, blocker, or literal semantics")]

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


def validate_review_decision(candidate: dict[str, Any], validation_report: dict[str, Any], successor: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Validate one full review decision before selecting or returning REWORK."""
    diagnostics, verdict = _report_diagnostics(candidate, validation_report)
    if diagnostics:
        return _sorted(diagnostics)
    effective = validation_report.get("effective")
    if verdict == "ПРИНЯТО":
        if successor is not None:
            return [_diagnostic("/successor_document", "SUCCESSOR_FORBIDDEN", "successor is forbidden for an accepted review")]
        if effective != {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": document_sha256(candidate)}:
            return [_diagnostic("/effective", "REVIEW_EFFECTIVE_MISMATCH", "accepted review effective reference must equal candidate")]
        return []
    if verdict == "AUTO_FIX_APPLIED":
        if successor is None:
            return [_diagnostic("/successor_document", "SUCCESSOR_REQUIRED", "successor is required for AUTO_FIX_APPLIED")]
        diagnostics = validate_successor(candidate, successor)
        if diagnostics:
            return diagnostics
        if any(item.get("correction_kind") != "MECHANICAL" for item in validation_report.get("corrections", [])):
            return [_diagnostic("/corrections", "REVIEW_REWORK_REQUIRED", "destructive, partial, or choice-bearing correction requires rework")]
        if effective != {"document_id": successor["document_id"], "revision": successor["revision"], "document_sha256": document_sha256(successor)}:
            return [_diagnostic("/effective", "REVIEW_EFFECTIVE_MISMATCH", "auto-fix review effective reference must equal successor")]
        return []
    if successor is not None:
        return [_diagnostic("/successor_document", "SUCCESSOR_FORBIDDEN", "successor is forbidden for a rework review")]
    if effective is not None:
        return [_diagnostic("/effective", "REVIEW_EFFECTIVE_FORBIDDEN", "rework review must not name an effective document")]
    return []


def select_effective_document(candidate: dict[str, Any], validation_report: dict[str, Any], successor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Select the exact reviewed candidate or valid successor; never merge documents."""
    diagnostics, verdict = _report_diagnostics(candidate, validation_report)
    if diagnostics:
        raise SelectionError(diagnostics)
    decision_rows = validate_review_decision(candidate, validation_report, successor)
    if decision_rows:
        raise SelectionError(decision_rows)
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


# --- partial acceptance: the reviewer's blocking findings reject their cases, the code only subtracts and checks ----------------------

DEFAULT_REJECT_SHARE = 20  # percent; ``.skillsrc`` limits.review_reject_share (0..50)


def candidate_rows(document: dict[str, Any]) -> list[dict[str, str]]:
    """What every agent-fed input (assemble, validate-canonical, review files) requires of a candidate: revision 1, no parent.  The two validator
    clauses of a subtraction successor are by content, so only a document the code built (``subtract``) may carry them."""
    if document.get("revision") != 1 or document.get("parent_sha256") is not None:
        return [_diagnostic("/revision", "CANDIDATE_REVISION", "a candidate is revision 1 without a parent; a later revision is built by the code from the reviewer's answer")]
    return []


def coverage_gaps(document: dict[str, Any]) -> list[str]:
    """The requirements of a subtraction successor that no case covers - computed, never stored (r1 was fully covered)."""
    from tools.canonical_document import uncovered_requirements

    return [document["requirements"][index]["requirement_id"] for index in uncovered_requirements(document["requirements"], document["test_cases"])]


def _owners(document: dict[str, Any]) -> dict[str, str]:
    """case, step, expectation and assertion IDs (document-global) -> the case that owns them."""
    owners: dict[str, str] = {}
    for case in document["test_cases"]:
        owners[case["case_id"]] = case["case_id"]
        for step in case["steps"]:
            owners[step["step_id"]] = case["case_id"]
            for expectation in step["expectations"]:
                owners[expectation["expectation_id"]] = case["case_id"]
                owners.update({item["assertion_id"]: case["case_id"] for item in expectation["assertions"]})
    return owners


def _rejection(candidate: dict[str, Any], report: dict[str, Any], canaries: Sequence[Any]) -> tuple[str | None, list[str], list[str], dict[str, list[Any]]]:
    """``(reason, rejected, with_group, said)`` without the share (``said``: the blocking findings of each rejected case): ``NO_BLOCKING`` - nothing blocks; ``NO_CASES`` - a blocking finding names no case (the whole set); else the cases the
    blocking findings name, canary findings excepted, and the cases of a group that is left with one variant."""
    from tools.review_canaries import canary_finding

    owners, rejected, said = _owners(candidate), set(), {}
    if not any(item.get("severity") == "BLOCKING" for item in report.get("findings") or []):
        return "NO_BLOCKING", [], [], {}  # sent back with nothing that blocks: the schema forbids it, and the code does not lean on that
    for finding in report.get("findings") or []:
        if finding.get("severity") != "BLOCKING":
            continue
        named = {owners[item] for item in finding.get("related_ids") or [] if item in owners}
        if canary_finding(canaries, finding, named):
            continue
        if not named:
            return "NO_CASES", [], [], {}
        rejected |= named
        for case_id in named:
            said.setdefault(case_id, []).append(finding)
    groups: dict[str, list[str]] = {}
    for case in candidate["test_cases"]:
        if "param_group" in case:
            groups.setdefault(case["param_group"], []).append(case["case_id"])
    dragged = {case_id for members in groups.values() if rejected & set(members) and len(set(members) - rejected) < 2 for case_id in members} - rejected
    order = [case["case_id"] for case in candidate["test_cases"]]
    return None, [item for item in order if item in rejected], [item for item in order if item in dragged], said


def subtract(candidate: dict[str, Any], removed: Sequence[str]) -> dict[str, Any]:
    """r2: the candidate without the removed cases; the kept cases are r1's (copied), nothing is renumbered."""
    successor = copy.deepcopy(candidate)
    successor["revision"], successor["parent_sha256"] = candidate["revision"] + 1, document_sha256(candidate)
    successor["test_cases"] = [case for case in successor["test_cases"] if case["case_id"] not in set(removed)]
    return successor


def check_subtraction(candidate: dict[str, Any], effective: dict[str, Any], removed: Sequence[str]) -> list[dict[str, str]]:
    """The lineage of r2 as the code checks it: only the cases, the revision and the parent differ from r1; every kept case is r1's byte for byte
    (canonical JSON), in r1's order; exactly the removed cases are gone."""
    def bare(document: dict[str, Any]) -> bytes:
        return canonical_bytes({key: value for key, value in document.items() if key not in {"test_cases", "revision", "parent_sha256"}})

    if candidate["revision"] != 1 or (effective["revision"], effective["parent_sha256"]) != (2, document_sha256(candidate)) or bare(candidate) != bare(effective):
        return [_diagnostic("/", "SUBTRACTION_DOCUMENT_CHANGED", "r2 may differ from r1 only in its cases, revision and parent")]
    before = {case["case_id"]: case for case in candidate["test_cases"]}
    kept = [case["case_id"] for case in effective["test_cases"]]
    if any(item not in before or canonical_bytes(case) != canonical_bytes(before[item]) for item, case in zip(kept, effective["test_cases"])):
        return [_diagnostic("/test_cases", "SUBTRACTION_CASE_CHANGED", "a kept case is not r1's byte for byte")]
    if kept != [item for item in before if item not in set(removed)] or not kept:
        return [_diagnostic("/test_cases", "SUBTRACTION_REMOVED_SET", "the cases taken out of r2 are not exactly the rejected ones")]
    return []


def review_outcome(candidate: dict[str, Any], report: dict[str, Any], canaries: Sequence[Any] = (), share: int = DEFAULT_REJECT_SHARE) -> dict[str, Any]:
    """The outcome of a ``ТРЕБУЕТ ДОРАБОТКИ`` answer: ``ACCEPTED_WHOLE`` (every blocking finding is a canary's: effective = r1), ``PARTIAL`` (effective = r2)
    or ``REWORK`` (a blocking finding without a case, or more than ``share`` percent of the cases removed)."""
    reason, rejected, dragged, said = _rejection(candidate, report, canaries)
    total, removed = len(candidate["test_cases"]), len(rejected) + len(dragged)
    base = {"rejected": rejected, "with_group": dragged, "removed": removed, "total": total, "share": share, "reason": reason, "effective": None, "said": said}
    if reason is None and not rejected:
        return {**base, "kind": "ACCEPTED_WHOLE", "effective": candidate}
    if reason is None and removed * 100 > share * total:
        reason = "OVER_SHARE"
    return {**base, "kind": "REWORK", "reason": reason} if reason else {**base, "kind": "PARTIAL", "effective": subtract(candidate, [*rejected, *dragged])}


def partial_is_bound(candidate: dict[str, Any], report: dict[str, Any], canaries: Sequence[Any], effective: Any) -> bool:
    """Whether *effective* is what the reviewer's report gives: r1 itself when only canaries were blocking, else a valid subtraction of exactly the
    cases the report rejects.  The share is not part of it (a limit edited since the selection must not unseal the run)."""
    reason, rejected, dragged, _said = _rejection(candidate, report, canaries)
    if reason is not None or not isinstance(effective, dict):
        return False
    return effective == candidate if not rejected else not check_subtraction(candidate, effective, [*rejected, *dragged])


# --- the rework: the cases of r1 the reviewer rejected go back once to the generator; the code builds r3 and checks it ---------------

DEFAULT_REWORK_ROUNDS = 0  # ``.skillsrc`` limits.rework_rounds (0 | 1): 0 is V6 (nothing goes back); the code knows no second round
REWORK_INSTANCE, REWORK_VIEW = "rework-1", "rework-view-v1"
REWORK_STAGE = "tc-generator:" + REWORK_INSTANCE
REWORK_TEXT_MAX, REWORK_FINDINGS_MAX = 2000, 10  # what one blocking finding of the reviewer may carry into the generator's view
_CASE_IDENTITY = ("case_id", "requirement_ids", "display_order", "param_group")


def rework_request(candidate: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """What the generator of the rework reads: the rejected cases as the reviewer saw them (r1) and the blocking findings that rejected each (code, text,
    the ids of that case) — a function of r1 and the outcome.  The texts are the reviewer's: a view is data, each finding cut at ``REWORK_TEXT_MAX``."""
    owners = _owners(candidate)
    return {"view": REWORK_VIEW, "cases": [case for case in candidate["test_cases"] if case["case_id"] in outcome["rejected"]],
            "findings": {case_id: [{"code": finding.get("code"), "message": str(finding.get("message"))[:REWORK_TEXT_MAX],
                                    "related_ids": [item for item in finding.get("related_ids") or [] if owners.get(item) == case_id]}
                                   for finding in findings[:REWORK_FINDINGS_MAX]] for case_id, findings in outcome["said"].items()}}


def _refuse(code: str, message: str) -> SelectionError:
    return SelectionError([_diagnostic("/test_cases", code, message)])


def _regroup(candidate: dict[str, Any], outcome: dict[str, Any], passed: Sequence[str]) -> tuple[set[str], set[str]]:
    """``(back, dropped)``: the one rule of the groups after the rework.  A group that left r2 whole (every variant rejected or taken out with them) is a group
    again only with two live variants — those of ``passed`` and the ones taken out with it; its variants come back (r1's bytes), else the passed ones with it stay out."""
    groups: dict[str, list[str]] = {}
    for case in candidate["test_cases"]:
        if "param_group" in case:
            groups.setdefault(case["param_group"], []).append(case["case_id"])
    kept = {case["case_id"] for case in outcome["effective"]["test_cases"]}
    back, dropped = set(), set()
    for members in groups.values():
        if not kept & set(members):
            alive = {item for item in members if item in passed or item in outcome["with_group"]}
            (back if len(alive) >= 2 else dropped).update(alive)
    return back, dropped


def check_rework(candidate: dict[str, Any], outcome: dict[str, Any], answer: Any) -> dict[str, Any]:
    """r3 = r2 plus the answer's cases at r1's places, or ``SelectionError``: the answer is exactly the rejected cases (no other, none missing, none twice), each
    keeps its ``case_id``, ``requirement_ids``, ``display_order`` and ``param_group``, every other case is r2's byte for byte, and r3 passes the validator."""
    from tools.canonical_document import validate_canonical_document

    r2, rejected = outcome["effective"], outcome["rejected"]
    if (not isinstance(answer, dict) or set(answer) != {"schema_version", "status", "batch_id", "test_cases"} or answer["schema_version"] != "1.0.0"
            or answer["status"] != "COMPLETE" or answer["batch_id"] != REWORK_INSTANCE or not isinstance(answer["test_cases"], list)
            or any(not isinstance(case, dict) or not isinstance(case.get("case_id"), str) for case in answer["test_cases"])):
        raise _refuse("REWORK_ANSWER_FORM", "the answer is {schema_version: 1.0.0, status: COMPLETE, batch_id: rework-1, test_cases: [the reworked cases]}")
    given = [case.get("case_id") for case in answer["test_cases"]]
    kept = {case["case_id"]: case for case in r2["test_cases"]}
    if set(kept) & set(given):
        raise _refuse("REWORK_FOREIGN_CASE_CHANGED", "the answer holds a case the review accepted: " + ", ".join(sorted(set(kept) & set(given))))
    if sorted(map(str, given)) != sorted(rejected):
        raise _refuse("REWORK_CASE_SET", f"the answer must hold exactly the rejected cases {rejected}, it holds {given}")
    before = {case["case_id"]: case for case in candidate["test_cases"]}
    for case in answer["test_cases"]:
        if any(case.get(key) != before[case["case_id"]].get(key) or (key in case) != (key in before[case["case_id"]]) for key in _CASE_IDENTITY):
            raise _refuse("REWORK_CASE_IDENTITY", f"{case['case_id']}: case_id, requirement_ids, display_order and param_group stay as the reviewer saw them")
    by_id = {case["case_id"]: case for case in answer["test_cases"]}
    back = _regroup(candidate, outcome, rejected)[0]  # the variants taken out with their group come back beside the reworked ones, as when every case passes
    r3 = copy.deepcopy(r2)
    r3["revision"], r3["parent_sha256"] = r2["revision"] + 1, document_sha256(r2)
    r3["test_cases"] = [copy.deepcopy(by_id[item] if item in by_id else kept[item] if item in kept else before[item]) for item in before if item in kept or item in by_id or item in back]
    if any(case["case_id"] not in by_id and case["case_id"] not in back and canonical_bytes(case) != canonical_bytes(kept[case["case_id"]]) for case in r3["test_cases"]):
        raise _refuse("REWORK_FOREIGN_CASE_CHANGED", "a case outside the answer is not r2's byte for byte")
    rows = validate_canonical_document(r3)
    if rows:
        raise SelectionError([_diagnostic(row["path"], "REWORK_INVALID", f"{row['code']}: {row['message']}") for row in rows[:5]])
    return r3


def _second_round(report: dict[str, Any], r3: dict[str, Any], reworked: set[str]) -> tuple[set[str], dict[str, list[Any]], int]:
    """``(rejected, said, ignored)`` of the review of the reworked cases only: a blocking finding rejects the reworked cases it names; one that names none
    rejects them all; one that names only other cases is ``ignored`` (what the first round accepted the second cannot reject)."""
    if report.get("verdict") == "ПРИНЯТО":
        return set(), {}, 0
    owners, rejected, said, ignored = _owners(r3), set(), {}, 0
    blocking = [finding for finding in report.get("findings") or [] if finding.get("severity") == "BLOCKING"]
    for finding in blocking:
        related = [item for item in finding.get("related_ids") or [] if item in owners]
        named = {owners[item] for item in related} & reworked
        if not named and related:
            ignored += 1
            continue
        for case_id in named or reworked:
            said.setdefault(case_id, []).append(finding)
        rejected |= named or reworked
    return (rejected, said, ignored) if blocking else (set(reworked), {}, 0)  # sent back with nothing that blocks: no case passes


def rework_final(candidate: dict[str, Any], outcome: dict[str, Any], answer: Any, report: dict[str, Any], successor: Any = None) -> dict[str, Any]:
    """The set after the second round, a function of r1, the first outcome (``PARTIAL``), the generator's answer and the review of the reworked cases only:
    r2 plus the reworked cases that passed and the variants of a group that came back with them (r1's bytes) — r3; r2 itself when none passed.
    ``SelectionError`` for an answer or a review the code refuses; the set is validated again here, before anyone publishes it."""
    from tools.canonical_document import validate_canonical_document

    if report.get("verdict") == "AUTO_FIX_APPLIED" or successor is not None:
        raise _refuse("REWORK_REVIEW_FIX", "the review of the reworked cases accepts or rejects them: no successor, no edits")
    r2, r3 = outcome["effective"], check_rework(candidate, outcome, answer)
    reworked = {case["case_id"]: case for case in answer["test_cases"]}
    out, said, ignored = _second_round(report, r3, set(reworked))
    passed = [item for item in reworked if item not in out]
    back, dropped = _regroup(candidate, outcome, passed)
    final = [item for item in passed if item not in dropped]
    out_with_group = [item for item in [*outcome["with_group"], *dropped] if item not in back]  # taken out with a group: nobody rejected them in the second round
    result = {"kind": "NONE" if not final else "ALL" if len(final) == len(reworked) and not out_with_group else "PARTIAL",
              "ignored": ignored, "said": said, "reworked": reworked, "accepted_after": final, "effective": r2,
              "rejected": [item for item in reworked if item not in passed], "with_group": [case["case_id"] for case in candidate["test_cases"] if case["case_id"] in out_with_group]}
    if final:
        pick = {case["case_id"]: reworked.get(case["case_id"], case) for case in candidate["test_cases"] if case["case_id"] in [*final, *back]}
        effective = copy.deepcopy(r2)
        effective["revision"], effective["parent_sha256"] = r2["revision"] + 1, document_sha256(r2)
        old = {case["case_id"]: case for case in r2["test_cases"]}
        effective["test_cases"] = [copy.deepcopy(pick.get(case["case_id"]) or old[case["case_id"]]) for case in candidate["test_cases"] if case["case_id"] in pick or case["case_id"] in old]
        rows = validate_canonical_document(effective)
        if rows:
            raise SelectionError([_diagnostic(row["path"], "REWORK_FINAL_INVALID", f"{row['code']}: {row['message']}") for row in rows[:5]])
        result["effective"] = effective
    return result
