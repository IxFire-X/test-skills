#!/usr/bin/env python3
"""Publish and select one reviewed canonical document revision for the V5 integrity chain."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from tools.canonical_document import CanonicalDocumentError, canonical_bytes, document_sha256, require_valid_canonical_document
from tools.publish_test_case_bundle import Receipt, partial_context, planned_receipt, publish_bundle, rejected_cases, status_payloads, verify_bundle
from tools.revision_selection import (DEFAULT_REJECT_SHARE, DEFAULT_REWORK_ROUNDS, REWORK_REVIEW_STAGE, REWORK_STAGE, SelectionError, candidate_rows, check_rework,
                                      review_outcome, reviewer_verdict_is_bound, rework_final, select_effective_document, validate_review_decision)
from tools.review_edits import reviewer_successor
from tools.schema_validation import classify_version, schema_diagnostics
from tools.pilot_state import closure_cached as _closure_cached


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SCHEMA = ROOT / "schemas" / "tc-reviewer-output.schema.json"
REVIEWER_SESSION_SCHEMA = ROOT / "schemas" / "reviewer-session.schema.json"
CONTEXT_RECEIPT_SCHEMA = ROOT / "schemas" / "context-selection-receipt.schema.json"
ASSEMBLY_RECEIPT_SCHEMA = ROOT / "schemas" / "assembly-receipt.schema.json"
INVENTORY_RECEIPT_SCHEMA = ROOT / "schemas" / "inventory-receipt.schema.json"
AUTOMATION_REVIEW_SCHEMA = ROOT / "schemas" / "autotest-reviewer-output.schema.json"
TRACE_AUDIT_SCHEMA = ROOT / "schemas" / "trace-audit-output.schema.json"
_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE_V5 = "zephyr-scale-step-row-24-v5"
_PROFILE = _PROFILE_V5
_PROFILES = frozenset({_PROFILE_V4, _PROFILE_V5})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _diag(path: str, code: str, message: str = "Orchestration failed.") -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _safe(rows: Sequence[Mapping[str, Any]], default: str) -> tuple[Mapping[str, str], ...]:
    return tuple(_freeze(_diag(str(row.get("path", "")), str(row.get("code", default)))) for row in rows)


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
    if value is None or isinstance(value, (str, int, float, bool)):  # a scalar cannot be changed in place — no copy
        return value
    return copy.deepcopy(value)


def _receipt_copy(value: Receipt) -> Receipt:
    return Receipt(value.document_id, value.revision, value.csv_profile, value.json_path, value.preview_path, value.csv_path, value.document_sha256, value.preview_sha256, value.csv_sha256)


@dataclass(frozen=True)
class OrchestrationResult:
    candidate_document: Mapping[str, Any]
    candidate_bundle_receipt: Receipt
    candidate_publication_status: Literal["UNREVIEWED"]
    successor_document: Mapping[str, Any] | None
    successor_bundle_receipt: Receipt | None
    effective_document: Mapping[str, Any] | None
    effective_bundle_receipt: Receipt | None
    status: str
    facts: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, str], ...]


# contextlib (closure_cache) sets __traceback__ on the way out; the error's own fields stay immutable.
_EXCEPTION_SLOTS = frozenset({"__traceback__", "__cause__", "__context__", "__suppress_context__", "__notes__"})


class OrchestrationError(ValueError):
    """Immutable, value-redacting error with recoverable bundle receipts."""

    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]], candidate_bundle_receipt: Receipt | None = None, successor_bundle_receipt: Receipt | None = None) -> None:
        object.__setattr__(self, "code", str(code))
        object.__setattr__(self, "diagnostics", tuple(_freeze(dict(row)) for row in sorted(diagnostics, key=lambda row: (str(row.get("path", "")), str(row.get("code", "")), str(row.get("message", ""))))))
        object.__setattr__(self, "candidate_bundle_receipt", None if candidate_bundle_receipt is None else _receipt_copy(candidate_bundle_receipt))
        object.__setattr__(self, "successor_bundle_receipt", None if successor_bundle_receipt is None else _receipt_copy(successor_bundle_receipt))
        ValueError.__init__(self, json.dumps({"code": self.code, "diagnostics": [dict(row) for row in self.diagnostics]}, ensure_ascii=False, separators=(",", ":")))

    def __setattr__(self, name: str, value: object) -> None:
        if name not in _EXCEPTION_SLOTS and hasattr(self, "code"):
            raise AttributeError("OrchestrationError is immutable")
        object.__setattr__(self, name, value)


def _expected_paths(document: Mapping[str, Any], output_dir: str | os.PathLike[str], profile: str) -> tuple[str, str, str]:
    base = Path(output_dir).resolve() / f"{document['document_id']}.r{document['revision']}"
    return str(base) + ".json", str(base) + (".html" if profile in _PROFILES else ".md"), str(base) + ".zephyr-scale.csv"


def _receipt_rows(receipt: Any, document: Mapping[str, Any], output_dir: str | os.PathLike[str] | None, profile: str) -> list[dict[str, str]]:
    from tools.run_pipeline import printed_or

    if type(receipt) is not Receipt:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    expected_paths = _expected_paths(document, output_dir, profile) if output_dir is not None else None
    expected = {
        "document_id": document["document_id"], "revision": document["revision"], "csv_profile": profile,
        "document_sha256": document_sha256(dict(document)),
    }
    fields = ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")
    for field in fields:
        value = getattr(receipt, field)
        if field in expected and value != expected[field]:
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
        if field in {"preview_sha256", "csv_sha256"} and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
            return [_diag(f"/receipt/{field}", "ORCHESTRATION_RECEIPT")]
    if expected_paths is not None and (receipt.json_path, receipt.preview_path, receipt.csv_path) != expected_paths:
        # select got another --output-dir than publish — name both directories; same directory, other files: as before
        published = Path(str(receipt.json_path)).parent
        if Path(output_dir).resolve() == published:
            return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
        return [_diag("/receipt", "ORCHESTRATION_OUTPUT_DIR",
                      f"--output-dir {printed_or(Path(output_dir).resolve(), '<каталог select>')} не совпадает с каталогом publish {published}; "
                      f"fix: повтори с --output-dir {printed_or(published, '<каталог publish>')}")]
    if expected_paths is None and any(not isinstance(getattr(receipt, field), str) or not getattr(receipt, field) for field in ("json_path", "preview_path", "csv_path")):
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    return []


def _review_rows(review: Any) -> list[dict[str, str]]:
    version = classify_version(review)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        return [_diag("/schema_version", version["code"], version["message"])]
    rows = schema_diagnostics(review, REVIEW_SCHEMA, ROOT)
    if rows:
        return [_diag(str(row.get("path", "")), str(row.get("code", "ORCHESTRATION_REVIEW"))) for row in rows]
    return []


def _validate_profile(profile: str) -> None:
    if profile not in _PROFILES:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/csv_profile", "ORCHESTRATION_PROFILE")])


def _session_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _host_isolation_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_digest"}
    return "sha256:" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt_value(receipt: Receipt) -> dict[str, Any]:
    return {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")}


def _exact_receipt(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    plain = _plain(value)
    if not isinstance(plain, dict) or plain.get("digest") != _session_digest(plain):
        raise ValueError(f"REVIEWER_PROTOCOL: invalid {label} receipt")
    return plain


def _marker_requirements(rows: Any, run_root: Path, attempt_id: str) -> Any:
    """The requirements the candidate document holds — those of the marker without the ones the human excluded."""
    from tools.scope_pause import marker_requirements_in_scope

    return marker_requirements_in_scope(rows, Path(run_root), attempt_id)


def reviewer_package_binding(
    candidate: Mapping[str, Any],
    candidate_receipt: Receipt,
    assembly_receipt: Mapping[str, Any],
    inventory_receipt: Mapping[str, Any],
    context_receipts: Sequence[Mapping[str, Any]],
    *,
    context_marker_output: Mapping[str, Any],
    generator_fragments: Sequence[Mapping[str, Any]],
    run_root: Path,
    attempt_id: str,
) -> dict[str, Any]:
    """Bind reviewer input to exact canonical and controller receipt bytes."""
    candidate_value = _plain(candidate)
    require_valid_canonical_document(candidate_value)
    if candidate_rows(candidate_value):
        raise ValueError("REVIEWER_PROTOCOL: a candidate is revision 1 without a parent")
    if _receipt_rows(candidate_receipt, candidate_value, None, candidate_receipt.csv_profile) or _readback_rows(candidate_receipt, candidate_value):
        raise ValueError("REVIEWER_PROTOCOL: candidate receipt is invalid")
    assembly, inventory = _exact_receipt(assembly_receipt, "assembly"), _exact_receipt(inventory_receipt, "inventory")
    if schema_diagnostics(assembly, ASSEMBLY_RECEIPT_SCHEMA, ROOT) or assembly.get("document_digest") != document_sha256(candidate_value):
        raise ValueError("REVIEWER_PROTOCOL: assembly receipt is invalid")
    if schema_diagnostics(inventory, INVENTORY_RECEIPT_SCHEMA, ROOT):
        raise ValueError("REVIEWER_PROTOCOL: inventory receipt is invalid")
    from tools.pilot_state import derive_state, read_context_selection, read_model_stage_artifact

    contexts = [_plain(item) for item in context_receipts]
    if not contexts or len({item.get("digest") for item in contexts}) != len(contexts):
        raise ValueError("REVIEWER_PROTOCOL: context receipts are missing or duplicated")
    for item in contexts:
        try:
            readback = read_context_selection(Path(run_root), attempt_id, str(item.get("digest", "")))
        except ValueError:
            readback = None
        if schema_diagnostics(item, CONTEXT_RECEIPT_SCHEMA, ROOT) or item.get("digest") != _session_digest(item) or item.get("inventory_digest") != inventory["digest"] or readback != item:
            raise ValueError("REVIEWER_PROTOCOL: context receipt is invalid")
    marker = _plain(context_marker_output)
    marker_artifacts = marker.get("artifacts") if isinstance(marker, dict) else None
    marker_analytics = marker_artifacts.get("analytics_documentation") if isinstance(marker_artifacts, dict) else None
    if (
        not isinstance(marker, dict)
        or not isinstance(marker_analytics, dict)
        or _marker_requirements(marker_analytics.get("requirements"), run_root, attempt_id) != candidate_value.get("source_requirements")
    ):
        raise ValueError("REVIEWER_PROTOCOL: context-marker output is invalid")
    marker_digest = "sha256:" + hashlib.sha256(canonical_bytes(marker)).hexdigest()
    marker_publication = read_model_stage_artifact(
        Path(run_root), attempt_id, "context-marker:baseline", marker_digest,
    )
    if marker_publication.get("artifact") != marker:
        raise ValueError("REVIEWER_PROTOCOL: context-marker output readback differs")
    fragments = [_plain(item) for item in generator_fragments]
    fragments_by_batch = {
        item.get("batch_id"): item for item in fragments if isinstance(item, dict)
    }
    assembly_batches = assembly.get("fragment_digests")
    if (
        not fragments
        or len(fragments_by_batch) != len(fragments)
        or not isinstance(assembly_batches, list)
        or set(fragments_by_batch) != {item.get("batch_id") for item in assembly_batches}
    ):
        raise ValueError("REVIEWER_PROTOCOL: generator fragments are invalid")
    contexts_by_digest = {item["digest"]: item for item in contexts}
    generator_batches = []
    model_bytes = marker_publication["byte_count"]
    for expected in assembly_batches:
        fragment = fragments_by_batch.get(expected.get("batch_id"))
        context_digest = fragment.get("context_receipt_digest") if isinstance(fragment, dict) else None
        if (
            not isinstance(fragment, dict)
            or fragment.get("status") != "COMPLETE"
            or context_digest not in contexts_by_digest
            or fragment.get("context_receipt") != contexts_by_digest[context_digest]
        ):
            raise ValueError("REVIEWER_PROTOCOL: generator fragment is invalid")
        publication = read_model_stage_artifact(
            Path(run_root), attempt_id, f"tc-generator:{fragment['batch_id']}",
            str(expected.get("digest", "")),
        )
        if publication["content_digest"] != expected.get("digest") or publication.get("artifact") != fragment:
            raise ValueError("REVIEWER_PROTOCOL: generator fragment does not match assembly")
        model_bytes += publication["byte_count"]
        generator_batches.append({
            "batch_id": fragment["batch_id"],
            "digest": publication["content_digest"],
            "context_receipt_digest": context_digest,
        })
    assembly_events = [
        event for event in derive_state(Path(run_root))["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("event_type") == "CANDIDATE_PUBLISHED"
        and event.get("stage_instance_id") == "assembly"
        and event.get("artifact_digest") == document_sha256(candidate_value)
    ]
    if len(assembly_events) != 1:
        raise ValueError("REVIEWER_PROTOCOL: assembled candidate publication is unbound")
    raw = [canonical_bytes(candidate_value), canonical_bytes(_receipt_value(candidate_receipt)), canonical_bytes(assembly), canonical_bytes(inventory), *(canonical_bytes(item) for item in contexts)]
    binding = {"candidate_digest": document_sha256(candidate_value), "candidate_receipt_digest": "sha256:" + hashlib.sha256(raw[1]).hexdigest(), "assembly_digest": assembly["digest"], "context_marker_output_digest": marker_publication["content_digest"], "generator_batches": generator_batches, "inventory_digest": inventory["digest"], "context_receipt_digests": [item["digest"] for item in contexts], "base_package_byte_count": sum(len(item) for item in raw) + model_bytes + sum(item["byte_count"] for item in contexts)}
    binding["package_digest"] = "sha256:" + hashlib.sha256(canonical_bytes(binding)).hexdigest()
    return binding


@_closure_cached
def reviewer_session_start(
    run_root: Path,
    attempt_id: str,
    *,
    candidate: Mapping[str, Any],
    candidate_receipt: Receipt,
    assembly_receipt: Mapping[str, Any],
    inventory_receipt: Mapping[str, Any],
    context_receipts: Sequence[Mapping[str, Any]],
    context_marker_output: Mapping[str, Any],
    generator_fragments: Sequence[Mapping[str, Any]],
    fresh_context: bool,
    reviewer_invocation_id: str,
) -> dict[str, Any]:
    """The session start of the reviewer, from the review inputs of the journal; writes nothing.

    Budget rule: ``context_budget_bytes = base_package_byte_count + .skillsrc limits.context_batch_bytes`` (256 KiB by
    default) — the whole review package plus room for C-lite evidence of one context batch. ``fresh_context`` and the
    reviewer invocation are the host's statement; the generator invocation is the one of the first batch's
    ``MODEL_REQUESTED``; the code computes ``distinct_invocations``, the evidence digest and the budget."""
    from tools.pilot_state import derive_state, read_model_request, require_fresh_context_statement
    from tools.run_pipeline import _scan_context_budget

    require_fresh_context_statement(Path(run_root), fresh_context)
    package = reviewer_package_binding(
        candidate, candidate_receipt,
        assembly_receipt, inventory_receipt, context_receipts,
        context_marker_output=context_marker_output, generator_fragments=generator_fragments,
        run_root=Path(run_root), attempt_id=attempt_id,
    )
    stage = f"tc-generator:{package['generator_batches'][0]['batch_id']}"
    requested = [event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                 and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    if len(requested) != 1:
        raise ValueError(f"REVIEWER_PROTOCOL: {stage} has no single model request")
    generator = str(read_model_request(Path(run_root), attempt_id, stage, str(requested[0]["artifact_digest"]))["invocation_id"])
    reviewer = str(reviewer_invocation_id)
    isolation = {"fresh_context": fresh_context, "distinct_invocations": generator != reviewer,
                 "role_policy": "canonical-reviewer-v1"}
    isolation["evidence_digest"] = _host_isolation_digest(isolation)
    reserve = _scan_context_budget({}, Path(run_root).resolve().parent.parent)
    return {
        "session_id": f"review-{attempt_id}", "generator_role": "generator", "reviewer_role": "canonical-reviewer",
        "generator_invocation_id": generator, "reviewer_invocation_id": reviewer, "host_isolation": isolation,
        "context_budget_bytes": package["base_package_byte_count"] + reserve,
    }


@_closure_cached
def open_reviewer_session(run_root: Path, attempt_id: str, package_binding: Mapping[str, Any], session_start: Mapping[str, Any]) -> dict[str, Any]:
    """Reserve the only canonical reviewer session for an active attempt before invocation."""
    package = _plain(package_binding)
    start = _plain(session_start)
    required = {"session_id", "generator_role", "reviewer_role", "generator_invocation_id", "reviewer_invocation_id", "host_isolation", "context_budget_bytes"}
    isolation = start.get("host_isolation")
    if (
        set(start) != required
        or package.get("package_digest") != "sha256:" + hashlib.sha256(canonical_bytes({key: item for key, item in package.items() if key != "package_digest"})).hexdigest()
        or not isinstance(isolation, dict)
        or isolation.get("evidence_digest") != _host_isolation_digest(isolation)
    ):
        raise ValueError("REVIEWER_PROTOCOL: invalid reviewer session start")
    budget = start["context_budget_bytes"]
    if type(budget) is not int or budget < package["base_package_byte_count"]:  # refuse now, not after the review
        raise ValueError(
            f"REVIEW_CONTEXT_LIMIT: context_budget_bytes {budget} is smaller than the review package "
            f"({package['base_package_byte_count']} bytes); open the review with tools.api prepare --stage tc-reviewer:canonical (budget = the package + .skillsrc limits.context_batch_bytes)"
        )
    facts = {
        **start,
        "canonical_branch_digest": package["candidate_digest"],
        "package_digest": package["package_digest"],
    }
    from tools.pilot_state import publish_bound

    # the one way a fixed receipt is published and bound — an opening cut short is opened again, no event is written twice
    bound = publish_bound(Path(run_root), attempt_id, "reviewer-session-boundary", facts)
    boundary = dict(bound["record"])
    created = {
        "schema_version": "1.0.0",
        "session_id": start["session_id"],
        "boundary_digest": boundary["digest"],
        "package_binding": package,
        "context_budget_bytes": start["context_budget_bytes"],
        "generator_role": start["generator_role"],
        "reviewer_role": start["reviewer_role"],
        "generator_invocation_id": start["generator_invocation_id"],
        "reviewer_invocation_id": start["reviewer_invocation_id"],
        "host_isolation": start["host_isolation"],
        "events": [{"ordinal": 1, "event_type": "REVIEW_SESSION_STARTED"}],
        "status": "WAITING",
    }
    created["digest"] = _session_digest(created)
    _publish_reviewer_ledger(Path(run_root), attempt_id, created)
    return boundary


def _read_reviewer_boundary(run_root: Path, attempt_id: str, package: Mapping[str, Any], session: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import read_attempt_receipt

    record = dict(read_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"])
    expected = {
        "session_id": session.get("session_id"),
        "canonical_branch_digest": package.get("candidate_digest"),
        "package_digest": package.get("package_digest"),
        "generator_role": session.get("generator_role"),
        "reviewer_role": session.get("reviewer_role"),
        "generator_invocation_id": session.get("generator_invocation_id"),
        "reviewer_invocation_id": session.get("reviewer_invocation_id"),
        "host_isolation": session.get("host_isolation"),
        "context_budget_bytes": session.get("context_budget_bytes"),
    }
    if session.get("boundary_digest") != record.get("digest") or any(record.get(key) != item for key, item in expected.items()):
        raise ValueError("REVIEWER_PROTOCOL: reviewer session does not match its immutable attempt boundary")
    return record


def reject_share(run_root: Path, attempt_id: str) -> int:
    """``.skillsrc`` ``limits.review_reject_share`` (percent, 0..50) of the limits the run froze at scan; 20 when none."""
    from tools.pilot_state import derive_state, run_root_of
    from tools.step_size import run_limits, whole_limit

    project, root = run_root_of(Path(run_root))
    value = whole_limit(run_limits(project, root, derive_state(root), attempt_id)[0].get("review_reject_share"))
    return DEFAULT_REJECT_SHARE if value is None else min(max(value, 0), 50)


def answer_outcome(run_root: Path, attempt_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
    """The outcome of a ``ТРЕБУЕТ ДОРАБОТКИ`` report over the candidate and the canaries of the journal."""
    from tools.review_canaries import verify

    return review_outcome(journal_assembly(Path(run_root), attempt_id)[0], _plain(report), verify(run_root, attempt_id) or (), reject_share(run_root, attempt_id))


def acceptance_summary(run_root: Path, attempt_id: str, answer: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What the report says of the review's outcome — the verdict as the reviewer gave it, the outcome the code drew from it and the numbers; a function of
    the recorded answer, the candidate and the canaries of the journal (nothing is stored)."""
    from tools.pilot_state import read_effective_canonical_if_present, terminal_reviewer_evidence
    from tools.review_canaries import verify

    report = ((answer or {}).get("artifacts") or {}).get("validation_report")
    if not isinstance(report, Mapping):
        return None
    try:  # the answer is recorded before select: no outcome until the ledger has its verdict and an accepting one has its effective canonical
        ledger = terminal_reviewer_evidence(run_root, attempt_id)
        closed = ledger["session_complete"] and ledger["authoritative_verdict_count"] == 1 and (
            ledger["authoritative_verdict"] == "REJECTED" or read_effective_canonical_if_present(run_root, attempt_id) is not None)
    except (KeyError, OSError, TypeError, ValueError):
        closed = False
    if not closed:
        return None
    candidate = journal_assembly(Path(run_root), attempt_id)[0]
    total = len(candidate["test_cases"])
    summary: dict[str, Any] = {"verdict_given": report.get("verdict"), "outcome": "ACCEPTED_WHOLE", "reason": None, "generated": total, "accepted": total, "removed": 0,
                               "rejected_by_findings": 0, "rejected_with_group": 0, "coverage_gaps": [], "rejected_cases": [], "files": None, "rework": None}
    if report.get("verdict") != "ТРЕБУЕТ ДОРАБОТКИ":
        return summary
    share = reject_share(run_root, attempt_id)
    outcome = review_outcome(candidate, _plain(report), verify(run_root, attempt_id) or (), share)
    if (ledger["authoritative_verdict"] == "ACCEPTED") != (outcome["kind"] != "REWORK"):
        return None  # the limit changed since the select: the report names no outcome the ledger does not
    summary.update(outcome=outcome["kind"], reason=outcome["reason"], share=share, removed=outcome["removed"], rejected_by_findings=len(outcome["rejected"]),
                   rejected_with_group=len(outcome["with_group"]), accepted=0 if outcome["kind"] == "REWORK" else total - outcome["removed"])
    if outcome["kind"] == "PARTIAL":
        state, final = rework_state(run_root, attempt_id), None
        if state == "reviewed":  # the second round: the set is what rework_final makes of the answer and its review
            data = rework_round_data(run_root, attempt_id)
            final = rework_final(data["candidate"], data["outcome"], data["answer"], data["report2"])
        partial = partial_context(candidate, outcome, {"state": state, **({"final": final} if final else {})} if state else None)
        name = f"published/{candidate['document_id']}.r{partial['revision']}"
        if state:  # the first review sent cases back for a rework: what became of it
            summary["rework"] = {"state": state, "sent": len(outcome["rejected"]), "accepted": len(final["accepted_after"]) if final else 0, "accepted_first": total - outcome["removed"],
                                 **({"ignored": final["ignored"], "independent": rework_independent(run_root, attempt_id)} if final else {})}
        if final:
            left = len(final["rejected"]) + len(final["with_group"])
            summary.update(accepted=len(final["effective"]["test_cases"]), removed=left, rejected_with_group=len(final["with_group"]))
            if final["kind"] == "ALL":  # every case came back: a whole set, no file of the rejected
                summary.update(outcome="ACCEPTED_WHOLE", removed=0, rejected_by_findings=0, rejected_with_group=0)
                return summary
        codes = lambda rows: sorted({code for row in rows for code, _text in row["said"]})  # noqa: E731  (the report carries codes, never the reviewer's texts: they are in the set's files)
        summary.update(coverage_gaps=[{"requirement": gap["requirement"], "labels": gap["labels"], "codes": codes(row for row in partial["rejected"] if gap["requirement"] in row["requirements"])}
                                      for gap in partial["gaps"]],
                       rejected_cases=[{"case_id": row["case_id"], "label": row["label"], "with_group": row["with_group"], "codes": codes([row])} for row in partial["rejected"]],
                       files={"csv": f"{name}.zephyr-scale.csv", "set": f"{name}.html, .md", "rejected": f"{name}.REJECTED-CASES.html, .md"})
    return summary


def rework_rounds(run_root: Path, attempt_id: str) -> int:
    """``.skillsrc`` ``limits.rework_rounds`` (0 or 1) of the limits the run froze at scan; ``DEFAULT_REWORK_ROUNDS`` when none.  Read at the first select only:
    what it decided stands in the journal (the receipt ``rework-round``), a later edit of the file changes nothing."""
    from tools.pilot_state import derive_state, run_root_of
    from tools.step_size import run_limits, whole_limit

    project, root = run_root_of(Path(run_root))
    value = whole_limit(run_limits(project, root, derive_state(root), attempt_id)[0].get("rework_rounds"))
    return DEFAULT_REWORK_ROUNDS if value is None else min(max(value, 0), 1)


def rework_basis(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """What the rework of the attempt is made of, from the journal: r1, the recorded answer of the reviewer and its digest, the first outcome (``PARTIAL`` — nothing
    here depends on the live ``limits``) and the context receipts of the batches that hold the rejected cases.  ``REWORK_NOT_PENDING`` where the first review sent nothing back."""
    from tools.pilot_state import derive_state, rework_round_if_present
    from tools.review_canaries import recorded_answer, verify

    receipt = rework_round_if_present(Path(run_root), attempt_id)
    candidate, _assembly, fragments = journal_assembly(Path(run_root), attempt_id)
    report = (((recorded_answer(run_root, attempt_id) or {}).get("artifacts")) or {}).get("validation_report")
    outcome = review_outcome(candidate, _plain(report), verify(run_root, attempt_id) or (), 100) if isinstance(report, Mapping) else None
    if (receipt is None or outcome is None or outcome["kind"] != "PARTIAL" or receipt["candidate_digest"] != document_sha256(candidate)
            or receipt["report_digest"] != "sha256:" + hashlib.sha256(canonical_bytes(_plain(report))).hexdigest()):  # the receipt is of this candidate and of this answer
        raise ValueError("REWORK_NOT_PENDING: the first review of this attempt did not send cases back for a rework; fix: `status` names the next step")
    digest = next(str(event["artifact_digest"]) for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                  and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == REVIEW_STAGE)
    contexts = list(dict.fromkeys(str(item["context_receipt_digest"]) for item in fragments if any(case["case_id"] in outcome["rejected"] for case in item["test_cases"])))
    return {"candidate": candidate, "report": _plain(report), "outcome": outcome, "answer_digest": digest, "contexts": contexts}


def check_rework_answer(run_root: Path, attempt_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """The check of the generator's answer of the rework before it is written: ``check_rework`` over the journal, a refusal with its code and the way out."""
    from tools.batch_assembly import BatchAssemblyError

    basis = rework_basis(run_root, attempt_id)
    try:
        r3 = check_rework(basis["candidate"], basis["outcome"], _plain(value))
    except SelectionError as error:
        rows = [{"path": row["path"], "code": row["code"], "message": row["message"], "fix": REWORK_FIX} for row in error.diagnostics]
        raise BatchAssemblyError(rows[0]["code"], rows[0]["message"], rows, REWORK_FIX) from None
    return {"rework": {"cases": len(value["test_cases"]), "set": len(r3["test_cases"])}}


REWORK_FIX = ("ответ — {schema_version: \"1.0.0\", status: \"COMPLETE\", batch_id: \"rework-1\", test_cases: [...]} с ровно отклонёнными кейсами; у каждого те же case_id, "
              "requirement_ids, display_order и param_group, что в виде; исправь ответ и отдай единственным повтором стадии (--transport-attempts 2), ничего не записано")


def rework_asked(run_root: Path, attempt_id: str) -> bool:
    """Whether the request of the rework is in the journal (the generator was asked: whatever became of the answer)."""
    from tools.pilot_state import derive_state

    return any(event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == REWORK_STAGE
               for event in derive_state(Path(run_root))["events"])


def rework_state(run_root: Path, attempt_id: str) -> str | None:
    """What became of the rework of a run whose first review sent cases back: ``None`` (it did not), ``not_started`` (nobody asked the generator),
    ``unfinished`` (asked: answered or not, nobody reviewed it) or ``reviewed`` (the second round has its answer)."""
    from tools.pilot_state import rework_round_if_present

    if rework_round_if_present(Path(run_root), attempt_id) is None:
        return None
    return "reviewed" if REWORK_REVIEW_STAGE in _rework_responses(run_root, attempt_id) else "unfinished" if rework_asked(run_root, attempt_id) else "not_started"


def _rework_responses(run_root: Path, attempt_id: str) -> dict[str, str]:
    """The digests of the recorded answers of the rework (the generator's) and of its review, by stage."""
    from tools.pilot_state import derive_state

    return {str(event["stage_instance_id"]): str(event["artifact_digest"]) for event in derive_state(Path(run_root))["events"]
            if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") in (REWORK_STAGE, REWORK_REVIEW_STAGE)}


def rework_round_data(run_root: Path, attempt_id: str, *, need_answer: bool = False) -> dict[str, Any]:
    """``rework_basis`` with what the rework has made: the answer of the generator, r3 with all its cases and its digest, the review of it (``report2``) — from the
    journal.  ``need_answer``: ``REWORK_NOT_ANSWERED`` while the generator has not answered."""
    from tools.pilot_state import read_model_stage_artifact

    data, got = rework_basis(run_root, attempt_id), _rework_responses(run_root, attempt_id)
    data.update(answer=None, answer_digest=got.get(REWORK_STAGE), r3=None, r3_digest=None, report2=None, review_digest=got.get(REWORK_REVIEW_STAGE))
    if data["answer_digest"]:
        data["answer"] = _plain(read_model_stage_artifact(Path(run_root), attempt_id, REWORK_STAGE, data["answer_digest"])["artifact"])
        data["r3"] = check_rework(data["candidate"], data["outcome"], data["answer"])
        data["r3_digest"] = document_sha256(data["r3"])
    elif need_answer:
        raise ValueError("REWORK_NOT_ANSWERED: the generator has not answered the rework yet; fix: `status` names the next step")
    if data["review_digest"]:
        data["report2"] = _plain(read_model_stage_artifact(Path(run_root), attempt_id, REWORK_REVIEW_STAGE, data["review_digest"])["artifact"])["artifacts"]["validation_report"]
    return data


def rework_artifacts(run_root: Path, attempt_id: str) -> tuple[Any, Any] | None:
    """``(answer, report)`` of the rework and of its review where the journal has both, else ``None`` — what the binding of the effective set is checked against."""
    try:
        data = rework_round_data(run_root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return None
    return None if data["answer"] is None or data["report2"] is None else (data["answer"], data["report2"])


REWORK_REVIEW_FIX = ("ответ — отчёт ревьюера по доработанным кейсам: verdict ПРИНЯТО или ТРЕБУЕТ ДОРАБОТКИ, candidate — ссылка из вида, reviewed_case_ids — доработанные кейсы, "
                     "блокирующие замечания называют кейсы в related_ids; без правок и преемника; исправь ответ и отдай единственным повтором стадии (--transport-attempts 2), ничего не записано")


def check_rework_review(run_root: Path, attempt_id: str, value: Mapping[str, Any]) -> None:
    """The check of the answer of the second round before it is written: the report names r3 and the reworked cases only, accepts (ПРИНЯТО) or rejects them,
    holds no fix and no successor, and ``rework_final`` makes a valid set of it; a refusal leaves the answer unwritten and the one retry."""
    from tools.batch_assembly import BatchAssemblyError

    data = rework_round_data(run_root, attempt_id, need_answer=True)
    artifacts = _plain(value)["artifacts"]
    report, r3, ids = artifacts["validation_report"], data["r3"], [case["case_id"] for case in data["answer"]["test_cases"]]
    ref = {"document_id": r3["document_id"], "revision": r3["revision"], "document_sha256": data["r3_digest"]}
    problem = None
    if report.get("verdict") == "AUTO_FIX_APPLIED" or artifacts.get("successor_document") is not None or artifacts.get("successor_edits") is not None:
        problem = ("REWORK_REVIEW_FIX", "the review of the reworked cases accepts or rejects them: no AUTO_FIX_APPLIED, no successor, no edits")
    elif report.get("reviewed_case_ids") != ids:
        problem = ("REWORK_REVIEW_CASES", f"reviewed_case_ids must be exactly the reworked cases {ids}")
    elif report.get("candidate") != ref or report.get("effective") != (ref if report.get("verdict") == "ПРИНЯТО" else None):
        problem = ("REWORK_REVIEW_CANDIDATE", f"candidate must be {ref}; effective must be it for ПРИНЯТО and null for ТРЕБУЕТ ДОРАБОТКИ")
    else:
        try:
            rework_final(data["candidate"], data["outcome"], data["answer"], report)
        except SelectionError as error:
            problem = (error.diagnostics[0]["code"], error.diagnostics[0]["message"])
    if problem is not None:
        raise BatchAssemblyError(problem[0], problem[1], [{"path": "/artifacts/validation_report", "code": problem[0], "message": problem[1], "fix": REWORK_REVIEW_FIX}], REWORK_REVIEW_FIX)


def rework_label(run_root: Path, attempt_id: str, stage: str) -> str | None:
    """The label of the session the request of ``stage`` names (the host's, or the code's where the host gave none), or None where there is no request."""
    from tools.pilot_state import derive_state, read_model_request

    event = next((row for row in derive_state(Path(run_root))["events"] if row.get("attempt_id") == attempt_id and row["event_type"] == "MODEL_REQUESTED"
                  and row.get("stage_instance_id") == stage), None)
    return None if event is None else str(read_model_request(Path(run_root), attempt_id, stage, str(event["artifact_digest"]))["invocation_id"])


def rework_independent(run_root: Path, attempt_id: str) -> bool:
    """The second round is independent of the generator of the rework: the two requests name different sessions.  By the rule of ``open_rework_review`` that is the host's
    statement of a new session (the same one is SELF, a different label with ``no`` is refused)."""
    reviewer, generator = (rework_label(run_root, attempt_id, stage) for stage in (REWORK_REVIEW_STAGE, REWORK_STAGE))
    return reviewer is not None and generator is not None and reviewer != generator


def open_rework_review(run_root: Path, attempt_id: str, *, model_id: str | None, invocation_id: str | None, fresh_context: bool | None) -> dict[str, Any]:
    """``prepare --stage tc-reviewer:rework-1``: the label is the host's and carries its statement, there being no stored boundary: ``--fresh-context yes`` names a session
    other than the generator's of the rework; ``no`` is that same session (a label of its own with ``no`` is refused, no label is the generator's label); the same
    label is SELF, refused where the run does not admit it.  The review of the answer is requested here; the request of the model follows (``prepare``)."""
    from tools.pilot_state import IndependenceUnverified, request_review, self_review_accepted
    from tools.stage_steps import slot
    from tools.step_size import rework_review_view_path

    with _closure_ctx():
        _require_open_attempt(run_root, attempt_id)
        data = rework_round_data(run_root, attempt_id, need_answer=True)
        generator = rework_label(run_root, attempt_id, REWORK_STAGE)
        mismatch = ("REVIEW_FRESH_CONTEXT_MISMATCH", "--fresh-context no говорит «та же сессия, что у генератора доработки», а --invocation-id называет другую; "
                    f"fix: либо --fresh-context yes --invocation-id <метка новой сессии>, либо --fresh-context no без метки (метка генератора доработки — {generator})")
        if fresh_context is False and invocation_id not in (None, generator):  # said before the rule of the run about a statement of «no»: the label is the host's own word
            raise ReviewRefused(*mismatch)
        fresh = _resolve_fresh_context(run_root, fresh_context)
        if fresh and invocation_id is None:
            raise ReviewRefused("REVIEW_INVOCATION_REQUIRED", "--fresh-context yes без --invocation-id: независимость заявляет хост, назвав сессию ревьюера; "
                                "fix: --invocation-id <метка сессии ревьюера> (не метка генератора доработки) или --fresh-context no, если ревью в той же сессии")
        if not fresh and invocation_id not in (None, generator):
            raise ReviewRefused(*mismatch)
        label = invocation_id or generator
        if label == generator and not self_review_accepted(Path(run_root)):
            raise IndependenceUnverified("INDEPENDENCE_UNVERIFIED: метка ревьюера та же, что у генератора доработки, а прогон не принял SELF; fix: ревью доработки — отдельной "
                                         "сессией: --fresh-context yes --invocation-id <метка новой сессии>; только если человек согласен на ревью в той же сессии, повтори scan с --accept-self-review")
        prior = rework_label(run_root, attempt_id, REWORK_REVIEW_STAGE)
        if prior is not None and prior != label:
            raise ReviewRefused("REVIEW_ALREADY_OPEN", f"ревью доработки уже открыто с ревьюером {prior}; второй сессии нет; fix: повтори prepare с тем же --invocation-id")
        request_review(Path(run_root), attempt_id, REWORK_REVIEW_STAGE, data["answer_digest"])
        view = rework_review_view_path(Path(run_root), data["answer_digest"])
        where = f"вид доработанных кейсов {view}, проверь только их (названное замечанием должно быть исправлено) и запиши ответ в {slot(Path(run_root), REWORK_REVIEW_STAGE)}"
        return {"reviewer_view": str(view), "label": label,
                "for_human": (f"Ревью доработки в этой же сессии (SELF; результат не называется независимым): прочти {where}. Затем `submit` этой стадии." if label == generator else
                              f"Ревью доработки в новой сессии: открой её (/clear или новое окно), в ней — «проведи ревью доработки test-skills {Path(run_root).name}»: она прочтёт {where}. "
                              "Вернись в рабочую сессию — `submit` этой стадии.")}


def _closure_ctx():
    from contextlib import ExitStack

    from tools.pilot_state import closure_cache
    from tools.project_inventory import inventory_cache

    stack = ExitStack()
    stack.enter_context(closure_cache())
    stack.enter_context(inventory_cache())
    return stack


def _publish_final(run_root: Path, attempt_id: str, data: Mapping[str, Any], final: Mapping[str, Any] | None) -> list[str]:
    """The set of the rework's end, once: ``final`` is ``rework_final`` of the answer and its review, or ``None`` for the rework nobody reviewed (r2, the rejected cases as r1 had
    them).  Every case came back: the set alone (r3), no file of the rejected; else the set, ``REJECTED-CASES`` and the effective canonical."""
    from tools.pilot_state import publish_effective_canonical, read_reviewer_session_ledger

    candidate, outcome, published = data["candidate"], data["outcome"], Path(run_root) / "published"
    effective = final["effective"] if final else outcome["effective"]
    partial = None if final and final["kind"] == "ALL" else partial_context(candidate, outcome, {"state": "reviewed", "final": final} if final else {"state": rework_state(run_root, attempt_id)})
    receipt = publish_bundle(effective, published, _PROFILE, partial=partial)
    if verify_bundle(effective, published, _PROFILE, partial=partial) != receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    files = []
    if partial is not None:
        shown, extras, intro = rejected_cases(candidate, partial, Path(run_root).name)
        files = write_status_files(run_root, shown, published, _PROFILE, "REJECTED-CASES", extras=extras, intro=intro)
    publish_effective_canonical(Path(run_root), attempt_id, effective, _receipt_value(receipt), str(read_reviewer_session_ledger(Path(run_root), attempt_id)["digest"]))
    return files


def close_pending_rework(run_root: Path, attempt_id: str) -> list[str]:
    """``finish`` of a run whose first review sent cases back and whose rework did not come to the end (not asked, refused twice, answered, nobody reviewed it): the set is r2,
    the rejected cases are in ``REJECTED-CASES`` as r1 had them with the reviewer's words — a rework nobody reviewed is not shown — and the effective canonical is published, once.
    Where the second round has its answer the set is what ``rework_final`` makes of it.  Nothing happens for a run that is not waiting for the rework (or already published)."""
    from tools.pilot_state import read_effective_canonical_if_present, rework_round_if_present

    if rework_round_if_present(Path(run_root), attempt_id) is None or read_effective_canonical_if_present(Path(run_root), attempt_id) is not None:
        return []
    data = rework_round_data(run_root, attempt_id)
    return _publish_final(run_root, attempt_id, data, None if data["report2"] is None else rework_final(data["candidate"], data["outcome"], data["answer"], data["report2"]))


def select_rework_review(run_root: Path, attempt_id: str, *, review: Mapping[str, Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    """``submit``'s end of the second round: the set ``rework_final`` makes of the answer of the rework and its review (``review``: the answer of a dry run, not recorded yet),
    published once; what the agent is told of it.  The same call again finishes what a refusal left."""
    from tools.pilot_state import read_effective_canonical_if_present

    with _closure_ctx():
        _require_open_attempt(run_root, attempt_id)
        data = rework_round_data(run_root, attempt_id, need_answer=True)
        report = _plain(review)["artifacts"]["validation_report"] if review is not None else data["report2"]
        final = rework_final(data["candidate"], data["outcome"], data["answer"], report)
        told = {"outcome": "ACCEPTED_WHOLE" if final["kind"] == "ALL" else "PARTIAL",
                "rework": {"accepted": len(final["accepted_after"]), "not_accepted": len(final["rejected"]) + len(final["with_group"]), "ignored": final["ignored"]},
                "independence": "verified" if rework_independent(run_root, attempt_id) else "independence_unverified"}
        if dry_run:
            return {**told, "would_publish": {"revision": final["effective"]["revision"], "cases": len(final["effective"]["test_cases"])}}
        if read_effective_canonical_if_present(Path(run_root), attempt_id) is None:
            _publish_final(run_root, attempt_id, data, final)
        published = Path(run_root) / "published"
        return {**told, "files": sorted(path.name for path in published.iterdir()) if published.is_dir() else []}


def abort_rework_review(run_root: Path, attempt_id: str, reason: str, dry_run: bool) -> dict[str, Any]:
    """``submit --abort`` of the second round: no verdict is coming; the set closes as for a rework nobody finished («Доработка не завершена.»); ``finish`` follows."""
    if reason not in ("REVIEW_CONTEXT_LIMIT", "REVIEW_UNUSABLE"):
        raise ValueError(f"REVIEWER_ABORT_REASON_INVALID: {reason}; fix: --abort REVIEW_CONTEXT_LIMIT или REVIEW_UNUSABLE")
    data = rework_round_data(run_root, attempt_id, need_answer=True)
    if data["report2"] is not None:
        raise ValueError("REVIEW_CLOSED: the review of the rework has its answer; fix: submit без --abort довершает итог")
    if not dry_run:
        close_pending_rework(run_root, attempt_id)
    return {"outcome": "ABORTED", "independence": "independence_unverified",
            "rework": {"accepted": 0, "not_accepted": len(data["outcome"]["rejected"]) + len(data["outcome"]["with_group"]), "ignored": 0}}


_VERDICT_EVENT = {"ПРИНЯТО": "ACCEPTED", "AUTO_FIX_APPLIED": "ACCEPTED", "ТРЕБУЕТ ДОРАБОТКИ": "REJECTED"}


@_closure_cached
def complete_reviewer_session(run_root: Path, attempt_id: str, *, review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The completed reviewer session for `select`, built from the published ledger and the recorded review.

    The session keeps the ledger events, then adds the one AUTHORITATIVE_VERDICT bound to the recorded
    tc-reviewer:canonical answer and REVIEW_SESSION_COMPLETED. Nothing is written: `select` validates and publishes it.
    ``review`` is the answer of a dry run, not recorded yet — the session is built from it.
    """
    from tools.pilot_state import derive_state, read_model_stage_artifact, read_reviewer_session_ledger, reviewer_lifecycle_projection

    if review is None:
        responses = [event for event in derive_state(Path(run_root))["events"]
                     if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                     and event.get("stage_instance_id") == "tc-reviewer:canonical"]
        if len(responses) != 1:
            raise ValueError("REVIEWER_RESPONSE_MISSING: the tc-reviewer:canonical answer is not recorded; fix: положи ответ ревьюера в "
                             "incoming/tc-reviewer-canonical.json и повтори tools.api submit --stage tc-reviewer:canonical")
        review = read_model_stage_artifact(Path(run_root), attempt_id, "tc-reviewer:canonical", str(responses[0]["artifact_digest"]))["artifact"]
    report = _plain(review).get("artifacts", {}).get("validation_report")
    verdict = _VERDICT_EVENT.get(report.get("verdict")) if isinstance(report, dict) else None
    if verdict is None:
        raise ValueError("REVIEWER_VERDICT_INVALID: the recorded review has no ПРИНЯТО / AUTO_FIX_APPLIED / ТРЕБУЕТ ДОРАБОТКИ verdict")
    if verdict == "REJECTED" and answer_outcome(run_root, attempt_id, report)["kind"] != "REWORK":
        verdict = "ACCEPTED"  # sent back, and the code took the rejected cases out (or only canaries blocked): the review accepts a set
    session = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id, unconfirmed_ok=True)))
    lifecycle = reviewer_lifecycle_projection(session)
    events = list(session["events"])
    bound = {
        "event_type": "AUTHORITATIVE_VERDICT", "verdict": verdict,
        "report_digest": "sha256:" + hashlib.sha256(canonical_bytes(report)).hexdigest(),
        "verdict_digest": "sha256:" + hashlib.sha256(canonical_bytes({"verdict": report.get("verdict"), "effective": report.get("effective")})).hexdigest(),
    }
    if not lifecycle["waiting"]:
        recorded = next((event for event in events if event.get("event_type") == "AUTHORITATIVE_VERDICT"), None)
        if session.get("status") == "COMPLETED" and recorded is not None and {key: recorded.get(key) for key in bound} == bound:
            return session  # already completed with this review: the same session again
        raise ValueError("REVIEWER_SESSION_CLOSED: the published reviewer session is already closed with another outcome")
    events.append({"ordinal": len(events) + 1, **bound})
    events.append({"ordinal": len(events) + 1, "event_type": "REVIEW_SESSION_COMPLETED"})
    session.update(events=events, status="COMPLETED")
    try:
        reviewer_lifecycle_projection(session)
    except ValueError as error:
        raise ValueError("REVIEWER_SESSION_OPEN_EVIDENCE: an evidence request has no answer yet") from error
    session["digest"] = _session_digest(session)
    return session


def _waiting_reviewer_session(run_root: Path, attempt_id: str) -> dict[str, Any]:
    from tools.pilot_state import read_reviewer_session_ledger, reviewer_lifecycle_projection

    session = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id, unconfirmed_ok=True)))
    if not reviewer_lifecycle_projection(session)["waiting"]:
        raise ValueError("REVIEWER_SESSION_CLOSED: the published reviewer session is already closed")
    return session


@_closure_cached
def provide_reviewer_evidence(run_root: Path, attempt_id: str, project_paths: Sequence[str], *, dry_run: bool = False, again_ok: bool = False) -> dict[str, Any]:
    """One C-lite evidence pair for the open reviewer session.

    The named project files of the attempt inventory become one context receipt (the one way to publish evidence now that
    publish-evidence is removed);
    the ledger gets EVIDENCE_REQUESTED (the request is the sorted paths and the remaining budget) and EVIDENCE_PROVIDED
    with the cumulative package digest, and the WAITING ledger is published again.
    ``dry_run`` checks and writes nothing; ``again_ok``: the same files given before are no duplicate (a repeated ``submit``).
    """
    from tools.pilot_state import derive_state, publish_context_selection, read_reviewer_session_ledger
    from tools.project_inventory import ContextSelectionError, read_inventory_receipt, select_context_batches

    session = _waiting_reviewer_session(run_root, attempt_id)
    package = session["package_binding"]
    events = list(session["events"])
    provided = [event for event in events if event.get("event_type") == "EVIDENCE_PROVIDED"]
    try:  # the latest revision published but not read back — the evidence of a call cut short, finished by the same call
        read_reviewer_session_ledger(Path(run_root), attempt_id)
        pending = None
    except ValueError:
        pending = provided[-1] if provided and events[-1].get("event_type") == "EVIDENCE_PROVIDED" else None
    if pending is not None:
        provided = provided[:-1]
    remaining = session["context_budget_bytes"] - package["base_package_byte_count"] - sum(event["byte_count"] for event in provided)
    if remaining <= 0:
        raise ValueError("REVIEW_CONTEXT_LIMIT: no context budget is left for evidence; abort the session with REVIEW_CONTEXT_LIMIT")
    attempt = next(row for row in derive_state(Path(run_root))["attempts"] if row["attempt_id"] == attempt_id)
    inventory = read_inventory_receipt(Path(run_root) / "inventories" / (package["inventory_digest"].removeprefix("sha256:") + ".json"))
    by_path = {item["project_path"]: item["opaque_id"] for item in inventory["files"]}
    paths = sorted(set(project_paths))
    unknown = [path for path in paths if path not in by_path]
    if not paths or unknown:
        raise ValueError(f"REVIEWER_EVIDENCE_PATH_UNKNOWN: not in the attempt inventory: {unknown}")
    try:
        batches = select_context_batches(inventory, Path(attempt["project"]), [by_path[path] for path in paths],
                                         byte_budget=remaining, include_closed_manifests=False)
    except ContextSelectionError as error:
        raise ValueError(f"REVIEW_CONTEXT_LIMIT: the evidence does not fit the remaining {remaining} bytes ({error})") from error
    if len(batches) != 1:
        raise ValueError(f"REVIEW_CONTEXT_LIMIT: the evidence does not fit the remaining {remaining} bytes in one receipt")
    selected = batches[0]["receipt"]
    digests = [event["provided_digest"] for event in provided]
    if pending is not None:
        if dry_run:
            return {}
        if selected["digest"] != pending["provided_digest"]:
            from tools.pilot_state import ReviewLedgerUnbound

            raise ReviewLedgerUnbound("REVIEW_LEDGER_UNBOUND: прошлый вызов `submit` с ключом evidence оборвался до записи события ревизии леджера; ничего не записано; "
                                      "fix: повтори `submit --stage tc-reviewer:canonical` с теми же путями в evidence, что в прошлый раз (он допишет события); иные файлы — после этого")
        receipt = publish_context_selection(Path(run_root), attempt_id, selected)  # the same receipt again: nothing new
        return {"evidence_receipt": receipt, "reviewer_session_digest": _publish_reviewer_ledger(Path(run_root), attempt_id, session)}
    if again_ok and selected["digest"] in digests:
        return {}
    if selected["digest"] in package["context_receipt_digests"] or selected["digest"] in digests:
        raise ValueError("REVIEWER_EVIDENCE_DUPLICATE: these files were already given to the reviewer")
    if dry_run:
        return {}
    receipt = publish_context_selection(Path(run_root), attempt_id, selected)
    request_digest = "sha256:" + hashlib.sha256(canonical_bytes({"project_paths": paths, "byte_budget": remaining})).hexdigest()
    digests.append(receipt["digest"])
    events.append({"ordinal": len(events) + 1, "event_type": "EVIDENCE_REQUESTED", "request_digest": request_digest, "byte_count": remaining})
    events.append({
        "ordinal": len(events) + 1, "event_type": "EVIDENCE_PROVIDED", "request_digest": request_digest,
        "provided_digest": receipt["digest"], "byte_count": receipt["byte_count"],
        "cumulative_package_digest": "sha256:" + hashlib.sha256(canonical_bytes({"package_digest": package["package_digest"], "evidence_digests": digests})).hexdigest(),
    })
    session["events"] = events
    session["digest"] = _session_digest(session)
    return {"evidence_receipt": receipt, "reviewer_session_digest": _publish_reviewer_ledger(Path(run_root), attempt_id, session)}


# the pre-verdict aborts: the context ceiling, and a reviewer answer that twice failed its check or a select
# refusal, so the session cannot end in a verdict
_ABORT_REASONS = ("REVIEW_CONTEXT_LIMIT", "REVIEW_UNUSABLE")


@_closure_cached
def abort_reviewer_session(run_root: Path, attempt_id: str, reason_code: str) -> dict[str, Any]:
    """The aborted reviewer session for `select` (no verdict). Nothing is written: `select` validates and publishes it."""
    from tools.pilot_state import read_reviewer_session_ledger, reviewer_lifecycle_projection

    if reason_code not in _ABORT_REASONS:
        raise ValueError(f"REVIEWER_ABORT_REASON_INVALID: one of {list(_ABORT_REASONS)}")
    last = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id, unconfirmed_ok=True)))
    if last.get("status") == "ABORTED" and last["events"][-1].get("reason_code") == reason_code:
        return last  # the same abort again: `select` finishes what a cut left, nothing is written twice; another reason is refused below
    session = _waiting_reviewer_session(run_root, attempt_id)
    events = list(session["events"])
    events.append({"ordinal": len(events) + 1, "event_type": "REVIEW_SESSION_ABORTED", "reason_code": reason_code})
    session.update(events=events, status="ABORTED")
    reviewer_lifecycle_projection(session)
    session["digest"] = _session_digest(session)
    return session


def _publish_reviewer_ledger(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> str:
    from tools.pilot_state import publish_reviewer_session_ledger

    receipt = publish_reviewer_session_ledger(Path(run_root), attempt_id, session)
    if receipt.get("record") != dict(session) or receipt.get("digest") != session.get("digest"):
        raise ValueError("REVIEWER_PROTOCOL: reviewer session ledger readback failed")
    return str(receipt["digest"])


def validate_reviewer_session(session: Mapping[str, Any], package_binding: Mapping[str, Any], review_artifact: Mapping[str, Any] | None = None, *, run_root: Path, attempt_id: str, evidence_receipts: Sequence[Mapping[str, Any]] = (), dry_run: bool = False, effective_canonical: bool = False) -> dict[str, Any]:
    """Validate the single fresh reviewer ledger and its bounded evidence sequence."""
    value = _plain(session)
    rows = schema_diagnostics(value, REVIEWER_SESSION_SCHEMA, ROOT)
    if rows or value.get("digest") != _session_digest(value):
        raise ValueError("REVIEWER_PROTOCOL: invalid reviewer session receipt")
    package = _plain(package_binding)
    if value["package_binding"] != package or package.get("package_digest") != "sha256:" + hashlib.sha256(canonical_bytes({key: item for key, item in package.items() if key != "package_digest"})).hexdigest():
        raise ValueError("REVIEWER_PROTOCOL: package binding is invalid")
    _read_reviewer_boundary(Path(run_root), attempt_id, package, value)
    events = value["events"]
    from tools.pilot_state import reviewer_lifecycle_projection

    try:
        lifecycle = reviewer_lifecycle_projection(value)
    except ValueError as error:
        raise ValueError("REVIEWER_PROTOCOL: event order is invalid") from error
    evidence = [_plain(item) for item in evidence_receipts]
    if len({item.get("digest") for item in evidence}) != len(evidence):
        raise ValueError("REVIEWER_PROTOCOL: evidence receipts are duplicated")
    for item in evidence:
        try:
            from tools.pilot_state import read_context_selection
            readback = read_context_selection(Path(run_root), attempt_id, str(item.get("digest", "")))
        except ValueError:
            readback = None
        if schema_diagnostics(item, CONTEXT_RECEIPT_SCHEMA, ROOT) or item.get("digest") != _session_digest(item) or item.get("inventory_digest") != package["inventory_digest"] or item.get("digest") in package["context_receipt_digests"] or readback != item:
            raise ValueError("REVIEWER_PROTOCOL: evidence receipt is invalid")
    pending: tuple[str, int] | None = None
    evidence_digests: list[str] = []
    evidence_bytes = package["base_package_byte_count"]
    evidence_index = 0
    for event in events[1:]:
        kind = event["event_type"]
        if kind == "EVIDENCE_REQUESTED":
            if pending is not None or not isinstance(event.get("request_digest"), str) or type(event.get("byte_count")) is not int or event["byte_count"] < 0:
                raise ValueError("REVIEWER_PROTOCOL: invalid evidence request")
            pending = (event["request_digest"], event["byte_count"])
        elif kind == "EVIDENCE_PROVIDED":
            if pending is None or event.get("request_digest") != pending[0] or evidence_index >= len(evidence):
                raise ValueError("REVIEWER_PROTOCOL: invalid evidence response")
            receipt = evidence[evidence_index]
            if event.get("provided_digest") != receipt["digest"] or event.get("byte_count") != receipt["byte_count"] or receipt["byte_count"] > pending[1]:
                raise ValueError("REVIEWER_PROTOCOL: evidence response is not bound to exact receipt bytes")
            evidence_bytes += receipt["byte_count"]
            evidence_digests.append(receipt["digest"])
            expected_cumulative = "sha256:" + hashlib.sha256(canonical_bytes({"package_digest": package["package_digest"], "evidence_digests": evidence_digests})).hexdigest()
            if evidence_bytes > value["context_budget_bytes"]:
                raise ValueError("REVIEWER_PROTOCOL: evidence was transferred beyond the context ceiling")
            if event.get("cumulative_package_digest") != expected_cumulative:
                raise ValueError("REVIEWER_PROTOCOL: cumulative evidence package digest is invalid")
            pending = None
            evidence_index += 1
        elif kind in {"AUTHORITATIVE_VERDICT", "REVIEW_SESSION_COMPLETED", "REVIEW_SESSION_ABORTED"}:
            continue
    if lifecycle["waiting"]:
        ledger_digest = _publish_reviewer_ledger(Path(run_root), attempt_id, value)
        return {"attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None, "independence": "independence_unverified", "acceptance_eligible": False, "verdict_count": 0, "reviewer_session_digest": ledger_digest}
    if evidence_index != len(evidence):
        raise ValueError("REVIEWER_PROTOCOL: evidence receipt was not transferred")
    if effective_canonical and lifecycle["authoritative_verdict_count"] != 1:
        raise ValueError("REVIEWER_PROTOCOL: terminal verdict invariant is invalid")
    isolation = value["host_isolation"]
    invocation_distinct = value["generator_invocation_id"] != value["reviewer_invocation_id"]
    if isolation["distinct_invocations"] != invocation_distinct or isolation.get("evidence_digest") != _host_isolation_digest(isolation):
        raise ValueError("REVIEWER_PROTOCOL: contradictory invocation isolation evidence")
    verified = bool(isolation["fresh_context"] and isolation["distinct_invocations"] and isolation["role_policy"] == "canonical-reviewer-v1")
    if verified and review_artifact is not None:  # a reviewer who names the generator's control word saw its task — SELF, whatever was declared
        from tools.review_canaries import codeword_state

        verified = codeword_state(run_root, attempt_id, _plain(review_artifact)) != "named"
    if lifecycle["pre_verdict_abort"]:
        if review_artifact is not None:
            raise ValueError("REVIEWER_PROTOCOL: aborted session cannot have review artifact")
        reason = lifecycle["abort_reason"]
        if evidence_bytes > value["context_budget_bytes"] and reason != "REVIEW_CONTEXT_LIMIT":
            raise ValueError("REVIEWER_PROTOCOL: base package exceeds the context ceiling")
        ledger_digest = str(value["digest"]) if dry_run else _publish_reviewer_ledger(Path(run_root), attempt_id, value)  # a dry run publishes nothing
        return {"attempt_state": "TERMINAL", "coverage": None, "independence": "independence_unverified", "acceptance_eligible": False, "verdict_count": 0, "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "accepted": False, "reason_code": reason, "reviewer_session_digest": ledger_digest}
    if evidence_bytes > value["context_budget_bytes"]:
        raise ValueError("REVIEW_CONTEXT_LIMIT")
    if review_artifact is None:
        raise ValueError("REVIEWER_PROTOCOL: completed session needs review artifact")
    if _review_rows(review_artifact):
        raise ValueError("REVIEWER_PROTOCOL: completed session review artifact is invalid")
    report = _plain(review_artifact).get("artifacts", {}).get("validation_report")
    verdict_event = next(event for event in events if event["event_type"] == "AUTHORITATIVE_VERDICT")
    if not reviewer_verdict_is_bound(report, verdict_event):
        raise ValueError("REVIEWER_PROTOCOL: verdict is not bound to review report")
    if report.get("verdict") == "ТРЕБУЕТ ДОРАБОТКИ" and verdict_event.get("verdict") != ("ACCEPTED" if effective_canonical else "REJECTED"):
        raise ValueError("REVIEWER_PROTOCOL: verdict is not bound to the outcome of the report")
    ledger_digest = str(value["digest"]) if dry_run else _publish_reviewer_ledger(Path(run_root), attempt_id, value)
    verdict_count = lifecycle["authoritative_verdict_count"]
    from tools.pilot_state import self_review_accepted

    # an unisolated review is eligible only in a run started with `scan --accept-self-review`.
    admitted = verified or self_review_accepted(Path(run_root))
    return {"independence": "verified" if verified else "independence_unverified", "acceptance_eligible": admitted and verdict_count == 1, "verdict_count": verdict_count, "reviewer_session_digest": ledger_digest}


@_closure_cached
def publish_unreviewed_candidate(
    candidate: Mapping[str, Any],
    output_dir: str | os.PathLike[str],
    csv_profile: str = _PROFILE,
    *,
    run_root: Path,
    attempt_id: str,
) -> Receipt:
    """Record the candidate and give its receipt; no file is written — ``published/`` holds only the outcome of the review."""
    value = _plain(candidate); require_valid_canonical_document(value)
    if candidate_rows(value):
        raise ValueError("CANDIDATE_REVISION: a candidate is revision 1 without a parent")
    from tools.pilot_state import append_event, derive_state

    # the same candidate again (after a lost stdout) gives the same receipt and writes no event; another one is refused as before
    digest = document_sha256(value)
    recorded = [str(event.get("artifact_digest")) for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                and event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"]
    if recorded and recorded[0] != digest:
        raise ValueError(f"assembled candidate already published: {recorded[0]}, not {digest}")
    receipt = planned_receipt(value, output_dir, csv_profile)
    if _receipt_rows(receipt, value, output_dir, csv_profile):
        raise OrchestrationError("ORCHESTRATION_RECEIPT", [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    if not recorded:
        append_event(
            run_root, "CANDIDATE_PUBLISHED", actor="controller",
            attempt_id=attempt_id, stage_instance_id="assembly",
            artifact_digest=digest,
        )
    return _receipt_copy(receipt)


def write_status_files(run_root: Path, document: Mapping[str, Any], output_dir: str | os.PathLike[str], csv_profile: str, status: str,
                       notes: Sequence[str] = (), **shown: Any) -> list[str]:
    """``<doc>.rN.REJECTED|UNREVIEWED.html/.md`` — a set that is not accepted, with its header, never a CSV; created once below the run,
    the same bytes again are accepted, nothing is replaced or removed."""
    from tools.confined_output import create_confined_bytes_exclusive

    root = Path(run_root).resolve(strict=True)  # <project>/.pilot-runs/<run>; the confinement refuses a target outside it
    project = root.parent.parent
    base = Path(output_dir).resolve() / f"{document['document_id']}.r{document['revision']}.{status}"
    paths = []
    for suffix, data in status_payloads(dict(document), csv_profile, status, notes, **shown):
        create_confined_bytes_exclusive(project, root, base.with_name(f"{base.name}.{suffix}"), data)
        paths.append(str(base.with_name(f"{base.name}.{suffix}")))
    return paths


def close_without_verdict(run_root: Path, attempt_id: str) -> list[str]:
    """A run closed before the reviewer's verdict (``submit --abort``, an answer that could not be used): the status file UNREVIEWED
    in ``<run>/published``, unless the review ended in an effective document or a REJECTED set.  Called by ``finish``."""
    from tools.pilot_state import read_effective_canonical_if_present

    published = Path(run_root) / "published"
    try:
        candidate = journal_assembly(Path(run_root), attempt_id)[0]
    except (KeyError, OSError, TypeError, ValueError):
        return []  # nothing was assembled: there is no set to mark
    name = f"{candidate['document_id']}.r{candidate['revision']}.REJECTED.md"
    if read_effective_canonical_if_present(Path(run_root), attempt_id) or any(published.rglob(name)):
        return []
    return write_status_files(run_root, candidate, published, _PROFILE, "UNREVIEWED")


def review_decision(candidate: Mapping[str, Any], review_value: Mapping[str, Any], *, imported: bool = False,
                    candidate_receipt: Receipt | None = None, canaries: Sequence[Mapping[str, Any]] | None = None,
                    share: int = DEFAULT_REJECT_SHARE) -> tuple[Any, Any]:
    """``(successor, effective)`` of a reviewer answer, or OrchestrationError — every deterministic check of the
    decision, writing nothing: the review shape, the successor edits, the decision against the candidate, the successor
    document and the human fields of an import.  select runs it before it publishes the ledger, model-response
    of tc-reviewer before it writes the answer, so a wrong answer keeps the stage's one retry."""
    candidate_value, review_value = _plain(candidate), _plain(review_value)
    rows = _review_rows(review_value)
    if rows:
        raise OrchestrationError("ORCHESTRATION_INPUT", rows, candidate_receipt) from None
    report = review_value["artifacts"]["validation_report"]
    artifacts = review_value["artifacts"]
    if canaries:  # an edit of a canary text has the mutated text as ``old`` — the canon holds the original, so it never applies
        from tools import review_canaries

        artifacts, dropped = review_canaries.filter_edits(canaries, artifacts)
        review_canaries.refuse_if_only_canaries(canaries, review_value, dropped)
    try:
        successor = reviewer_successor(candidate_value, artifacts)  # a document or exact edits
    except SelectionError as error:
        raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    decision_rows = validate_review_decision(candidate_value, report, successor)
    if decision_rows:
        raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(decision_rows, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    verdict = report["verdict"]
    if verdict == "AUTO_FIX_APPLIED":
        try:
            require_valid_canonical_document(successor)
        except CanonicalDocumentError as error:
            raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_SUCCESSOR"), candidate_receipt) from None
        changed = _imported_human_changes(candidate_value, _plain(successor)) if imported else []
        if changed:
            raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag(path, "IMPORT_HUMAN_FIELD_CHANGED") for path in changed[:12]],
                                     candidate_receipt) from None
    elif successor is not None:
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/successor_document", "ORCHESTRATION_SUCCESSOR")], candidate_receipt) from None
    effective = None
    if verdict != "ТРЕБУЕТ ДОРАБОТКИ":
        try:
            effective = select_effective_document(candidate_value, _plain(report), _plain(successor) if verdict == "AUTO_FIX_APPLIED" else None)
        except SelectionError as error:
            raise OrchestrationError("ORCHESTRATION_SELECTION", _safe(error.diagnostics, "ORCHESTRATION_SELECTION"), candidate_receipt) from None
    else:  # sent back: the blocking findings of real cases reject them, the code subtracts; the whole set only for a finding without a case or over the share
        effective = review_outcome(candidate_value, _plain(report), canaries or (), share)["effective"]
        try:
            require_valid_canonical_document(effective) if effective is not None else None
        except CanonicalDocumentError as error:
            raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_SUBTRACTION"), candidate_receipt) from None
    return successor, effective


_IMPORTED_CASE_FIELDS = ("title", "objective", "preconditions", "priority")


def _imported_human_changes(candidate: Mapping[str, Any], successor: Mapping[str, Any]) -> list[str]:
    """In an import the reviewer's fix may not touch the human fields the import fixed (the fields
    batch_assembly.require_imported_human_fields keeps): case title/objective/preconditions/priority, step action,
    test data and expectation texts — the machine part only."""
    before = {case["case_id"]: case for case in candidate.get("test_cases", [])}
    changed: list[str] = []
    for index, case in enumerate(successor.get("test_cases", [])):
        source = before.get(case.get("case_id"))
        if source is None:
            changed.append(f"/test_cases/{index}")
            continue
        changed += [f"/test_cases/{index}/{field}" for field in _IMPORTED_CASE_FIELDS if case.get(field) != source.get(field)]
        steps, original = case.get("steps") or [], source.get("steps") or []
        if len(steps) != len(original):
            changed.append(f"/test_cases/{index}/steps")
            continue
        for number, (step, row) in enumerate(zip(steps, original)):
            changed += [f"/test_cases/{index}/steps/{number}/{field}" for field in ("action", "test_data") if step.get(field) != row.get(field)]
            if [item.get("text") for item in step.get("expectations") or []] != [item.get("text") for item in row.get("expectations") or []]:
                changed.append(f"/test_cases/{index}/steps/{number}/expectations")
    if len(successor.get("test_cases", [])) != len(before):
        changed.append("/test_cases")
    return changed


def _run_imported(run_root: Path, attempt_id: str) -> bool:
    """Whether the attempt is an import of cases: its published cases are the human part."""
    from tools.pilot_state import _imported_cases, _run_root, derive_state

    try:
        project, root = _run_root(Path(run_root))
        return _imported_cases(project, root, derive_state(root), attempt_id) is not None
    except (KeyError, OSError, TypeError, ValueError):
        return False


@_closure_cached
def orchestrate_revision(candidate: Mapping[str, Any], candidate_receipt: Receipt, package_binding: Mapping[str, Any], reviewer_session: Mapping[str, Any], review_artifact: Mapping[str, Any] | None, output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, *, run_root: Path, attempt_id: str, reviewer_evidence_receipts: Sequence[Mapping[str, Any]] = (), publisher=publish_bundle, verifier=verify_bundle, dry_run: bool = False) -> OrchestrationResult:
    """Select the published r1 and publish reviewer-produced r2 only when needed.

    ``dry_run`` runs every check of the decision and of the session and publishes nothing — no ledger, no r2 bundle, no
    effective canonical; the result is the selection the real call would make (receipts of what is not published are None)."""
    _validate_profile(csv_profile)
    candidate_value, review_value = _plain(candidate), None if review_artifact is None else _plain(review_artifact)
    try:
        require_valid_canonical_document(candidate_value)
    except CanonicalDocumentError as error:
        raise OrchestrationError("ORCHESTRATION_INPUT", _safe(error.diagnostics, "ORCHESTRATION_CANDIDATE")) from None
    if candidate_rows(candidate_value):
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/revision", "CANDIDATE_REVISION")])
    rows = _receipt_rows(candidate_receipt, candidate_value, output_dir, csv_profile)
    if rows or planned_receipt(_plain(candidate_value), output_dir, csv_profile) != candidate_receipt:  # no file before the verdict
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")])
    candidate_receipt = _receipt_copy(candidate_receipt)
    if review_value is None:
        facts = validate_reviewer_session(reviewer_session, package_binding, None, run_root=run_root, attempt_id=attempt_id, evidence_receipts=reviewer_evidence_receipts)
        if facts.get("attempt_state") == "WAITING_FOR_MODEL":
            return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "WAITING_FOR_MODEL", _freeze(facts), ())
        if facts.get("completion") == "PARTIAL":  # closed without a verdict: ``finish`` leaves the UNREVIEWED file
            return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, str(facts.get("reason_code", "REWORK")), _freeze(facts), ())
        raise OrchestrationError("ORCHESTRATION_INPUT", [_diag("/review_artifact", "REVIEWER_PROTOCOL")], candidate_receipt)
    # every deterministic check of the decision before validate_reviewer_session publishes the COMPLETED ledger
    from tools.review_canaries import verify as _verify_canaries

    canaries, share = _verify_canaries(run_root, attempt_id), reject_share(run_root, attempt_id)
    successor, effective = review_decision(candidate_value, review_value, imported=_run_imported(run_root, attempt_id), candidate_receipt=candidate_receipt,
                                           canaries=canaries, share=share)
    report = review_value["artifacts"]["validation_report"]
    verdict = report["verdict"]
    second = effective is not None and _plain(effective)["revision"] > 1  # AUTO_FIX or a subtraction: a second bundle beside the candidate's
    # a subtraction: its files say so, number the cases as the reviewer read them and say which requirements are left without a case
    extra = {"partial": partial_context(candidate_value, review_outcome(candidate_value, _plain(report), canaries or (), share))} if second and verdict == "ТРЕБУЕТ ДОРАБОТКИ" else {}
    facts = validate_reviewer_session(
        reviewer_session, package_binding, review_value,
        run_root=run_root, attempt_id=attempt_id,
        evidence_receipts=reviewer_evidence_receipts,
        effective_canonical=effective is not None, dry_run=dry_run,
    )
    if not facts["acceptance_eligible"]:
        raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag("/reviewer_session", "INDEPENDENCE_UNVERIFIED")], candidate_receipt)
    if second and verdict == "ТРЕБУЕТ ДОРАБОТКИ" and rework_rounds(run_root, attempt_id) == 1:  # the rejected cases go back once: the set is published when the rework is over
        if not dry_run:
            from tools.pilot_state import publish_rework_round

            publish_rework_round(Path(run_root), attempt_id, document_sha256(candidate_value), "sha256:" + hashlib.sha256(canonical_bytes(report)).hexdigest())
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "REWORK_PENDING",
                                   _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())
    successor_receipt = None
    if effective is not None and not dry_run:  # the accepted set — the files of the bundle, the import CSV among them — is written now
        try:
            published_set = publisher(_plain(effective), output_dir, csv_profile, **extra)
        except Exception:
            raise OrchestrationError("ORCHESTRATION_PUBLICATION", [_diag("", "ORCHESTRATION_PUBLICATION")], candidate_receipt) from None
        if second:
            successor_receipt = _receipt_copy(published_set) if type(published_set) is Receipt else None
        rows = _receipt_rows(published_set, effective, output_dir, csv_profile)
        if rows:
            raise OrchestrationError("ORCHESTRATION_RECEIPT", rows, candidate_receipt, successor_receipt) from None
        if extra:  # the cases taken out, whole, with what the reviewer said of them — never a CSV
            shown, extras, intro = rejected_cases(candidate_value, extra["partial"], Path(run_root).name)
            write_status_files(run_root, shown, output_dir, csv_profile, "REJECTED-CASES", extras=extras, intro=intro)
    if effective is None:
        if not dry_run:  # the rejected set with the blocking findings beside it — what the person has after a rejection
            blocking = [f"{row.get('code', '—')} ({', '.join(map(str, row.get('related_ids') or [])) or '—'}): {row.get('message', '')}"
                        for row in report.get("findings") or [] if row.get("severity") == "BLOCKING"]
            write_status_files(run_root, candidate_value, output_dir, csv_profile, "REJECTED", blocking)
        rework_facts = {"attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": None, "reason_code": "REWORK", "accepted": False, "reviewer_session_digest": facts["reviewer_session_digest"]}
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None, None, None, None, "REWORK", _freeze(rework_facts), ())
    if dry_run and second:  # the successor is not published: its bundle is checked when it is
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None if successor is None else _freeze(successor), None, _freeze(effective), None, "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())
    try:
        verified = planned_receipt(_plain(effective), output_dir, csv_profile, **extra) if dry_run else verifier(_plain(effective), output_dir, csv_profile, **extra)  # a dry run has no files to read
    except Exception:
        raise OrchestrationError("ORCHESTRATION_VERIFICATION", [_diag("", "ORCHESTRATION_VERIFICATION")], candidate_receipt, successor_receipt) from None
    rows = _receipt_rows(verified, effective, output_dir, csv_profile)
    publication_receipt = successor_receipt if successor_receipt is not None else candidate_receipt
    if rows or verified != publication_receipt:
        raise OrchestrationError("ORCHESTRATION_RECEIPT", rows or [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")], candidate_receipt, successor_receipt) from None
    if dry_run:  # the effective canonical is not published
        return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None if successor is None else _freeze(successor), None, _freeze(effective), _receipt_copy(publication_receipt), "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())
    try:
        from tools.pilot_state import publish_effective_canonical

        published_effective = publish_effective_canonical(
            Path(run_root), attempt_id, _plain(effective), _receipt_value(publication_receipt), str(facts["reviewer_session_digest"]),
        )
        if published_effective["record"].get("document") != _plain(effective):
            raise ValueError("effective canonical read-back mismatch")
    except (KeyError, TypeError, ValueError) as error:
        raise OrchestrationError("ORCHESTRATION_SELECTION", [_diag("/effective_canonical", "ORCHESTRATION_EFFECTIVE_RECEIPT")], candidate_receipt, successor_receipt) from error
    return OrchestrationResult(_freeze(candidate_value), candidate_receipt, "UNREVIEWED", None if successor is None else _freeze(successor), successor_receipt, _freeze(effective), _receipt_copy(publication_receipt), "EFFECTIVE_SELECTED", _freeze({"reviewer_session_digest": facts["reviewer_session_digest"]}), ())


def _source(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"document_id": document["document_id"], "revision": document["revision"], "source_digest": document_sha256(_plain(document))}


def _source_rows(value: Any, expected: Mapping[str, Any], path: str) -> list[dict[str, str]]:
    return [] if isinstance(value, Mapping) and dict(value) == dict(expected) else [_diag(path, "ORCHESTRATION_SOURCE")]


def _readback_rows(receipt: Any, document: Mapping[str, Any]) -> list[dict[str, str]]:
    rows = _receipt_rows(receipt, document, None, receipt.csv_profile if type(receipt) is Receipt else "")
    if rows:
        return rows
    paths = (Path(receipt.json_path), Path(receipt.preview_path), Path(receipt.csv_path))
    if len({path.parent.resolve() for path in paths}) != 1:
        return [_diag("/receipt", "ORCHESTRATION_RECEIPT")]
    try:
        verified = planned_receipt(dict(document), paths[0].parent, receipt.csv_profile)
    except Exception:
        return [_diag("/receipt", "ORCHESTRATION_PUBLICATION_READBACK")]
    return [] if verified == receipt else [_diag("/receipt", "ORCHESTRATION_VERIFY_RECEIPT")]


def _receipt_json(receipt: Receipt | None) -> dict[str, Any] | None:
    return None if receipt is None else {field: getattr(receipt, field) for field in ("document_id", "revision", "csv_profile", "json_path", "preview_path", "csv_path", "document_sha256", "preview_sha256", "csv_sha256")}


def publish_command(candidate: Mapping[str, Any], output_dir: Path, csv_profile: str, *, run_root: Path, attempt_id: str) -> dict[str, Any]:
    """The candidate r1 as UNREVIEWED, the analyst questions beside it; the printed fields."""
    from tools.pilot_state import derive_state
    from tools.run_report import write_questions_next_to

    again = any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("attempt_id") == attempt_id
                and event.get("stage_instance_id") == "assembly" for event in derive_state(run_root)["events"])
    receipt = publish_unreviewed_candidate(candidate, output_dir, csv_profile, run_root=run_root, attempt_id=attempt_id)
    # the analyst questions beside the case set; a failed write only warns, the set is published.
    questions, code = write_questions_next_to(output_dir, run_root)
    return {"candidate_bundle_receipt": _receipt_json(receipt), "candidate_publication_status": "UNREVIEWED", "status": "published",
            "questions_for_analyst": questions, "warnings": [{"code": code}] if code else [], "idempotent": again}


def review_package(run_root: Path, attempt_id: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The reviewer package binding of ``review_inputs``."""
    return reviewer_package_binding(
        inputs["candidate"], inputs["candidate_receipt"], inputs["assembly_receipt"], inputs["inventory_receipt"],
        inputs["context_receipts"], context_marker_output=inputs["context_marker_output"],
        generator_fragments=inputs["generator_fragments"], run_root=Path(run_root), attempt_id=attempt_id)


def journal_review_artifact(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The tc-reviewer answer of a completed session, from the journal; None for an aborted or waiting one."""
    from tools.pilot_state import derive_state, read_model_stage_artifact

    if _plain(session).get("status") != "COMPLETED":
        return None
    digest = next((str(event["artifact_digest"]) for event in derive_state(Path(run_root))["events"]
                   if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "tc-reviewer:canonical"), None)
    return None if digest is None else read_model_stage_artifact(Path(run_root), attempt_id, "tc-reviewer:canonical", digest)["artifact"]


def journal_evidence_receipts(run_root: Path, attempt_id: str, session: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The C-lite evidence receipts the session's ledger records as provided, in order."""
    from tools.pilot_state import read_context_selection

    return [read_context_selection(Path(run_root), attempt_id, str(event["provided_digest"]))
            for event in _plain(session).get("events", []) if event.get("event_type") == "EVIDENCE_PROVIDED"]


def select_output(result: OrchestrationResult) -> dict[str, Any]:
    return {"candidate_bundle_receipt": _receipt_json(result.candidate_bundle_receipt), "diagnostics": [],
            "effective_bundle_receipt": _receipt_json(result.effective_bundle_receipt),
            "effective_source": None if result.effective_document is None else _source(result.effective_document),
            "facts": _plain(result.facts), "status": result.status, "successor_bundle_receipt": _receipt_json(result.successor_bundle_receipt)}


REVIEW_STAGE = "tc-reviewer:canonical"


def _require_open_attempt(run_root: Path, attempt_id: str) -> None:
    """A review command on a closed attempt answers ATTEMPT_TERMINAL at once (the refusal envelope), before it assembles anything."""
    from tools.pilot_state import derive_state

    attempt = next((row for row in derive_state(Path(run_root))["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is not None and attempt["state"] == "TERMINAL":
        raise ValueError(f"ATTEMPT_TERMINAL: the attempt {attempt_id} is closed; its review is over")


def review_step(run_root: Path, attempt_id: str, *, output_dir: Path, **values: Any) -> Any:
    """The selection of ``submit`` over the journal inputs (the receipts and the candidate come from the journal; the agent passes no file).

    The whole command runs in one ``closure_cache()`` and ``inventory_cache()`` — the journal, the receipts and the canon
    are read and checked once per process, not once per function; ``dry_run=True`` publishes nothing."""
    from tools.pilot_state import closure_cache
    from tools.project_inventory import inventory_cache

    with closure_cache(), inventory_cache():
        _require_open_attempt(run_root, attempt_id)
        inputs = review_inputs(run_root, attempt_id, output_dir=output_dir)
        package = review_package(run_root, attempt_id, inputs)
        session = values["reviewer_session"]
        review = values.get("review")
        review = review if review is not None else journal_review_artifact(run_root, attempt_id, session)
        return select_output(orchestrate_revision(
            inputs["candidate"], inputs["candidate_receipt"], package, session, review, output_dir, _PROFILE,
            run_root=run_root, attempt_id=attempt_id, reviewer_evidence_receipts=journal_evidence_receipts(run_root, attempt_id, session),
            dry_run=bool(values.get("dry_run"))))


class ReviewRefused(ValueError):
    """A refusal of the two-command review with its own code; the fix is the tail of the message, the table has the same."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _resolve_fresh_context(run_root: Path, fresh_context: bool | None) -> bool:
    """``fresh_context`` is the host's statement.  A run that admitted SELF at scan (``--accept-self-review``,
    ``.skillsrc`` ``review.self``) needs none — the code says ``no`` itself; any other run must state it."""
    from tools.pilot_state import require_fresh_context_statement, self_review_accepted

    if fresh_context is None:
        if not self_review_accepted(Path(run_root)):
            raise ReviewRefused("FRESH_CONTEXT_REQUIRED", "prepare без --fresh-context: прогон не принимал ревью в той же сессии; "
                                "fix: --fresh-context yes — ревьюер отдельный субагент или вызов без истории генерации; ревью в той же "
                                "сессии — прогон должен начинаться с scan --accept-self-review (или review.self: true в .skillsrc)")
        fresh_context = False
    require_fresh_context_statement(Path(run_root), fresh_context)
    return fresh_context


def review_open_step(run_root: Path, attempt_id: str, *, output_dir: Path, model_id: str | None = None, invocation_id: str | None = None,
                     fresh_context: bool | None = None) -> dict[str, Any]:
    """Everything before the reviewer's call (``prepare --stage tc-reviewer:canonical``): the candidate published (assemble → validate-canonical, when it
    is not yet), the session start, the package and the boundary, REVIEW_REQUESTED, MODEL_REQUESTED.  It records and computes; the
    model is called by the agent afterwards.  The same call again changes nothing; another reviewer is REVIEW_ALREADY_OPEN."""
    from tools.pilot_state import (closure_cache, derive_state, publish_model_request, read_attempt_receipt, read_model_request,
                                   read_reviewer_session_ledger, request_review)
    from tools.project_inventory import inventory_cache
    with closure_cache(), inventory_cache():
        _require_open_attempt(run_root, attempt_id)
        fresh = _resolve_fresh_context(run_root, fresh_context)
        events = [event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id]
        published = None
        if not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly" for event in events):
            published = publish_command(journal_assembly(run_root, attempt_id)[0], output_dir, _PROFILE, run_root=run_root, attempt_id=attempt_id)
        from tools.pilot_state import _run_root as _pilot_run_root
        from tools.review_view import ensure_reviewer_view

        view = ensure_reviewer_view(*_pilot_run_root(Path(run_root)), journal_assembly(run_root, attempt_id)[0])  # the reviewer's view with canaries
        try:
            boundary = dict(read_attempt_receipt(Path(run_root), attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"])
        except (KeyError, TypeError, ValueError, OSError):
            boundary = None
        if boundary is not None:
            isolation = boundary.get("host_isolation", {})
            if boundary.get("reviewer_invocation_id") != invocation_id or (fresh_context is not None and isolation.get("fresh_context") != fresh):
                raise ReviewRefused("REVIEW_ALREADY_OPEN", f"ревью уже открыто с ревьюером {boundary.get('reviewer_invocation_id')} "
                                    f"(fresh_context={isolation.get('fresh_context')}); второй сессии нет; "
                                    "fix: повтори prepare с теми же --invocation-id и --fresh-context или иди дальше: ответ в слот и submit")
            try:  # an opening cut short between the boundary and the ledger: the boundary is there, the ledger is not — open it again
                package = _plain(read_reviewer_session_ledger(Path(run_root), attempt_id)["package_binding"])  # strict: a ledger still waiting for its READ_BACK is finished by the opening below
            except (KeyError, TypeError, ValueError, OSError):
                boundary = None
        if boundary is None:
            inputs = review_inputs(run_root, attempt_id, output_dir=output_dir)
            start = reviewer_session_start(
                run_root, attempt_id, candidate=inputs["candidate"], candidate_receipt=inputs["candidate_receipt"],
                assembly_receipt=inputs["assembly_receipt"], inventory_receipt=inputs["inventory_receipt"],
                context_receipts=inputs["context_receipts"], context_marker_output=inputs["context_marker_output"],
                generator_fragments=inputs["generator_fragments"], fresh_context=fresh, reviewer_invocation_id=invocation_id)
            package = review_package(run_root, attempt_id, inputs)
            boundary = open_reviewer_session(run_root, attempt_id, package, start)
        requested = request_review(Path(run_root), attempt_id, REVIEW_STAGE, str(boundary["canonical_branch_digest"]))
        recorded = next((event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id
                         and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == REVIEW_STAGE), None)
        if recorded is None:
            request = publish_model_request(Path(run_root), attempt_id, REVIEW_STAGE, model_id=model_id, invocation_id=invocation_id,
                                            input_digests=[str(boundary["canonical_branch_digest"]), str(boundary["package_digest"])])
        else:
            request = read_model_request(Path(run_root), attempt_id, REVIEW_STAGE, str(recorded["artifact_digest"]))
            if request.get("invocation_id") != invocation_id or request.get("model_id") != model_id:
                raise ReviewRefused("REVIEW_ALREADY_OPEN", f"запрос ревьюера уже записан (модель {request.get('model_id')}, вызов "
                                    f"{request.get('invocation_id')}); fix: повтори prepare с теми же --model-id и --invocation-id")
        result: dict[str, Any] = {
            "package_binding": package, "reviewer_session_boundary": boundary, "review_requested": dict(requested),
            "model_request": dict(request), "reviewer_view": str(view["path"]),
            "canaries": {"seeded": view["canaries"], "note": "канарейки — сигнал качества ревью, не защита; классы и кейсы здесь не печатаются"},
            "for_human": review_for_human(run_root, view["path"], fresh)}
        if published is not None:
            result.update(candidate_bundle_receipt=published["candidate_bundle_receipt"], questions_for_analyst=published["questions_for_analyst"],
                          warnings=published["warnings"])
        return result


def _generator_label(run_root: Path, attempt_id: str) -> str:
    """The label of the generator's session: the request of its first batch — what the reviewer's label is compared with."""
    from tools.pilot_state import derive_state, read_model_request

    first = next((event for event in derive_state(Path(run_root))["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_REQUESTED"
                  and str(event.get("stage_instance_id")).startswith("tc-generator:BATCH-")), None)
    if first is None:
        raise ReviewRefused("REVIEWER_PROTOCOL", "у генератора нет запроса; fix: сначала `status` — стадии генератора")
    return str(read_model_request(Path(run_root), attempt_id, first["stage_instance_id"], str(first["artifact_digest"]))["invocation_id"])


def open_review(run_root: Path, attempt_id: str, *, model_id: str | None, invocation_id: str | None, fresh_context: bool | None) -> dict[str, Any]:
    """``prepare --stage tc-reviewer:canonical``: the whole opening of the review (``review_open_step``) into ``<run>/published``.  The label is the
    host's: ``--fresh-context yes`` names the reviewer's session or is refused; a session that says «the same» (``no``) takes the generator's label —
    that is the truth, and the comparison of the labels counts it as SELF.  SELF, where the run did not admit it, is refused before anything is written."""
    from tools.pilot_state import IndependenceUnverified, self_review_accepted

    fresh = _resolve_fresh_context(run_root, fresh_context)
    if fresh and invocation_id is None:
        raise ReviewRefused("REVIEW_INVOCATION_REQUIRED", "--fresh-context yes без --invocation-id: независимость заявляет хост, назвав сессию ревьюера; "
                            "fix: --invocation-id <метка сессии ревьюера> (не метка генератора) или --fresh-context no, если ревью в той же сессии")
    generator = _generator_label(run_root, attempt_id)
    label = invocation_id or generator
    if not (fresh and label != generator) and not self_review_accepted(Path(run_root)):
        raise IndependenceUnverified("INDEPENDENCE_UNVERIFIED: метка ревьюера та же, что у генератора, а прогон не принял SELF; fix: вызови ревьюера отдельной сессией "
                                     "и назови её --invocation-id; только если человек согласен на ревью в той же сессии, повтори scan с --accept-self-review")
    return review_open_step(run_root, attempt_id, output_dir=Path(run_root) / "published", model_id=model_id, invocation_id=label, fresh_context=fresh)


def review_for_human(run_root: Path, view: Path | str, fresh: bool) -> str:
    """What a person does to have the review made: INDEPENDENT (``fresh``) in a new session, SELF in this one; the answer goes to the slot.  Printed text only."""
    from tools.stage_steps import slot

    answer, run_id = slot(Path(run_root), REVIEW_STAGE), Path(run_root).name
    codeword = "В ответе поле codeword_seen: контрольное слово из задачи генератора, если оно есть в контексте сессии, иначе null."
    if not fresh:
        return (f"Ревью кейсов в этой же сессии (SELF; результат не называется независимым): прочти вид кейсов {view}, проверь кейсы по требованиям "
                f"и запиши ответ в {answer}. {codeword} Затем `submit` этой стадии.")
    return ("Ревью кейсов в свежей сессии (руками): открой новую сессию GigaCode или выполни /clear — в ней не должно быть истории генерации; "
            f"вставь «проведи ревью test-skills {run_id}»: модель прочтёт вид кейсов {view} и запишет ответ в {answer}. {codeword} "
            "Вернись в рабочую сессию — `submit` этой стадии.")


def select_review(run_root: Path, attempt_id: str, *, review: Mapping[str, Any] | None = None, session: Mapping[str, Any] | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    """``submit``'s selection: the session closed with the recorded answer (``review``: the answer of a dry run, not recorded yet; ``session``: an aborted one),
    the ledger, the set in ``<run>/published``; what the agent is told of it.  The same call again finishes what a refusal left (nothing is written twice)."""
    from tools.pilot_state import closure_cache
    from tools.project_inventory import inventory_cache

    with closure_cache(), inventory_cache():
        _require_open_attempt(run_root, attempt_id)
        session = session if session is not None else _last_reviewer_session(run_root, attempt_id, review)
        published = Path(run_root) / "published"
        selected = review_step(run_root, attempt_id, output_dir=published, reviewer_session=session, review=review, dry_run=dry_run)
        selected = {**selected, **_canary_report(run_root, attempt_id, review)}
        answer = review if review is not None else journal_review_artifact(run_root, attempt_id, session)
        report = (_plain(answer or {}).get("artifacts") or {}).get("validation_report") or {}
        outcome = "ABORTED" if not report else answer_outcome(run_root, attempt_id, report)["kind"] if report.get("verdict") == "ТРЕБУЕТ ДОРАБОТКИ" else "ACCEPTED_WHOLE"
        isolation = _plain(session)["host_isolation"]
        told = {key: selected[key] for key in ("status", "effective_source", "review_canaries", "codeword", "review_warnings")}
        told.update(outcome=outcome, independence="verified" if isolation["fresh_context"] and isolation["distinct_invocations"] and selected["codeword"]["state"] != "named"
                    else "independence_unverified")
        if dry_run:
            return {**told, "would_publish": {"reviewer_ledger": {"status": _plain(session).get("status")}, "effective_source": selected["effective_source"]}}
        return {**told, "files": sorted(path.name for path in published.iterdir()) if published.is_dir() else []}


def abort_review(run_root: Path, attempt_id: str, reason: str, dry_run: bool) -> dict[str, Any]:
    """``submit --abort``: the review that cannot end in a verdict is closed; ``finish`` follows."""
    return select_review(run_root, attempt_id, session=abort_reviewer_session(run_root, attempt_id, reason), dry_run=dry_run)


def _last_reviewer_session(run_root: Path, attempt_id: str, review: Mapping[str, Any] | None) -> dict[str, Any]:
    """The session ``select`` works on when the agent names none — the last revision of the attempt's ledger: an aborted or
    completed one as it is, a waiting one completed with the recorded answer (or the answer of a dry run)."""
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        ledger = dict(_plain(read_reviewer_session_ledger(Path(run_root), attempt_id, unconfirmed_ok=True)))
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ReviewRefused("REVIEW_NOT_OPEN", "у попытки нет журнала сессии ревьюера; fix: сначала tools.api prepare --stage tc-reviewer:canonical "
                            "--fresh-context yes|no") from error
    if ledger.get("status") in {"ABORTED", "COMPLETED"}:
        return ledger
    return complete_reviewer_session(run_root, attempt_id, review=review)


def _ledger_status(run_root: Path, attempt_id: str) -> str | None:
    """The status of the last revision of the attempt's reviewer ledger, or None when there is none to read."""
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        return _plain(read_reviewer_session_ledger(Path(run_root), attempt_id, unconfirmed_ok=True)).get("status")
    except (KeyError, TypeError, ValueError, OSError):
        return None


def refuse_closed_review(run_root: Path, attempt_id: str) -> None:
    """``submit`` with an answer of the review: a closed attempt is ATTEMPT_TERMINAL, a session that ``--abort`` (or a verdict) ended takes no answer — REVIEW_CLOSED; both before any write."""
    _require_open_attempt(run_root, attempt_id)
    if _ledger_status(run_root, attempt_id) in {"ABORTED", "COMPLETED"}:
        raise ReviewRefused("REVIEW_CLOSED", "сессия ревьюера уже закрыта (прервана или завершена), ответ в неё не добавить; ничего не записано; "
                            "fix: ревью закрыто — закрой попытку командой finish (она напечатает next_run); новое ревью — новый прогон (scan --new-run)")


def _canary_report(run_root: Path, attempt_id: str, pending: Mapping[str, Any] | None) -> dict[str, Any]:
    """What ``submit`` of the review says of the canaries (the outcome of the answer) and of the control word."""
    from tools import review_canaries

    summary = review_canaries.summary(run_root, attempt_id, pending)
    answer = pending if pending is not None else review_canaries.recorded_answer(run_root, attempt_id)
    state = review_canaries.codeword_state(run_root, attempt_id, answer)
    warnings = [code for code in (summary.get("warning"), "REVIEWER_NAMED_CODEWORD" if state == "named" else None) if code]
    return {"review_canaries": summary, "codeword": {"state": state, "note": review_canaries.CODEWORD_NOTES[state]}, "review_warnings": warnings}


REVIEW_INPUT_FIELDS = ("candidate", "candidate_receipt", "assembly_receipt", "inventory_receipt", "context_receipts",
                       "context_marker_output", "generator_fragments")


def review_inputs(run_root: Path, attempt_id: str, *, output_dir: Path | None = None, csv_profile: str = _PROFILE,
                  given: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The seven reviewer-package inputs of the attempt, read from the journal (only reads).

    candidate and assembly receipt — ``assemble_candidate`` over the published plan, header and the tc-generator answers
    (the bytes the last ``submit`` of the generator checked); candidate receipt — ``planned_receipt`` of that candidate for ``output_dir``;
    inventory — the attempt baseline's; context receipts — the package's when a session is open, else every receipt the
    attempt published; marker — the context-marker:baseline answer.  A value in ``given`` (a file the agent passed) is
    compared, never trusted: a difference is REVIEW_INPUT_MISMATCH naming the field and its source; a value the journal
    cannot give (a run of an earlier version) comes from ``given`` as before, else REVIEW_INPUTS_UNAVAILABLE."""
    from tools.batch_assembly import BatchAssemblyError
    from tools.pilot_state import derive_state, frozen_baseline, read_context_selection, read_model_stage_artifact, read_reviewer_session_ledger, _run_root
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt
    given = {key: value for key, value in (given or {}).items() if value not in (None, [])}
    root = Path(run_root)
    project, resolved = _run_root(root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: no attempt {attempt_id} in the run")
    answers = {str(event.get("stage_instance_id")): str(event["artifact_digest"]) for event in state["events"]
               if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"}
    journal: dict[str, Any] = {}
    sources: dict[str, str] = {}
    missing: dict[str, str] = {}
    try:
        candidate, assembly, fragments = journal_assembly(root, attempt_id)
        journal.update(candidate=candidate, assembly_receipt=assembly, generator_fragments=fragments)
        sources.update(candidate="сборка над plan, header и ответами tc-generator журнала", assembly_receipt="сборка той же",
                       generator_fragments="ответы tc-generator:<batch> журнала по порядку плана")
    except (ValueError, KeyError, BatchAssemblyError, TypeError) as error:
        for key in ("candidate", "assembly_receipt", "generator_fragments"):
            missing[key] = str(error).removeprefix("MODEL_INPUTS_MISMATCH: ").removeprefix("REVIEW_INPUTS_UNAVAILABLE: candidate: ")
    baseline = frozen_baseline(resolved, attempt)
    journal["inventory_receipt"] = read_inventory_receipt(resolved / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    sources["inventory_receipt"] = "опись baseline попытки"
    try:
        digests = list(read_reviewer_session_ledger(root, attempt_id, unconfirmed_ok=True)["package_binding"]["context_receipt_digests"])
        sources["context_receipts"] = "квитанции пакета открытой сессии ревьюера"
    except (KeyError, TypeError, ValueError):
        digests = list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                                     if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"))
        sources["context_receipts"] = "квитанции контекста, опубликованные попыткой"
    journal["context_receipts"] = [read_context_selection(root, attempt_id, digest) for digest in digests]
    if "context-marker:baseline" in answers:
        journal["context_marker_output"] = read_model_stage_artifact(root, attempt_id, "context-marker:baseline", answers["context-marker:baseline"])["artifact"]
        sources["context_marker_output"] = "ответ context-marker:baseline журнала"
    else:
        missing["context_marker_output"] = "нет ответа context-marker:baseline"
    candidate = journal.get("candidate", given.get("candidate"))
    if output_dir is not None and candidate is not None:
        try:
            journal["candidate_receipt"] = planned_receipt(_plain(candidate), output_dir, csv_profile)
            sources["candidate_receipt"] = f"расчёт bundle кандидата для {output_dir} (файлов до вердикта нет)"
        except (OSError, ValueError) as error:
            missing["candidate_receipt"] = f"bundle кандидата в {output_dir} не проверен ({error})"
    else:
        missing["candidate_receipt"] = "нужен каталог публикации — <run>/published"
    result: dict[str, Any] = {}
    for field in REVIEW_INPUT_FIELDS:
        if field in journal:
            if field in given and _review_key(field, given[field]) != _review_key(field, journal[field]):
                raise ValueError(f"REVIEW_INPUT_MISMATCH: {field}: переданный файл не совпадает с журналом "
                                 f"({sources[field]}); fix: не передавай {field} — код возьмёт его из журнала")
            result[field] = journal[field]
        elif field in given:
            result[field] = given[field]
        else:
            raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: {field}: {missing.get(field, 'нет в журнале')}; fix: сначала этот шаг"
                             " (или передай файл явно, для прогона до FaT-DG)")
    return result


def journal_assembly(run_root: Path, attempt_id: str) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    """``(candidate, assembly receipt, fragments)`` — ``assemble_candidate`` over the published plan and header
    and the tc-generator answers of the journal in plan order: the bytes the last ``submit`` of the generator checked (only reads)."""
    from tools.batch_assembly import assemble_candidate
    from tools.pilot_state import _generator_input, _run_root, derive_state, read_model_stage_artifact

    project, root = _run_root(Path(run_root))
    state = derive_state(root)
    plan, header = (_generator_input(project, root, state, attempt_id, kind) for kind in ("plan", "header"))
    answers = {str(event.get("stage_instance_id")): str(event["artifact_digest"]) for event in state["events"]
               if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"}
    stages = [f"tc-generator:{batch['batch_id']}" for batch in plan["batches"]]
    absent = [stage for stage in stages if stage not in answers]
    if absent:
        raise ValueError(f"REVIEW_INPUTS_UNAVAILABLE: candidate: нет ответа {absent[0]}; fix: сначала model-response этой пачки")
    # the canon is assembled once per process — the plan, the header and the answers are append-only facts of the journal, so
    # one command (publish, package, the decision of the verdict) asks for the same candidate several times and gets one assembly
    from tools.pilot_state import _CLOSURE_CACHE

    kept = _CLOSURE_CACHE.get()
    key = ("journal_assembly", str(root), attempt_id, str(plan["digest"]), str(header["digest"]), tuple(answers[stage] for stage in stages))
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    fragments = [read_model_stage_artifact(root, attempt_id, stage, answers[stage])["artifact"] for stage in stages]
    candidate, assembly = assemble_candidate(_plain(header), _plain(plan), [_plain(item) for item in fragments])
    if kept is not None:
        kept[key] = copy.deepcopy((candidate, assembly, fragments))
    return candidate, assembly, fragments


def _review_key(field: str, value: Any) -> bytes:
    if field == "context_receipts":  # a context file may be the receipt or its digest pointer: the digests decide
        value = [_plain(item).get("digest") if isinstance(_plain(item), dict) else None for item in value]
    return _review_bytes(value)


def _review_bytes(value: Any) -> bytes:
    if isinstance(value, Receipt):
        value = _receipt_json(value)
    elif isinstance(value, (list, tuple)):
        value = [_plain(item) for item in value]
    return canonical_bytes(_plain(value))
