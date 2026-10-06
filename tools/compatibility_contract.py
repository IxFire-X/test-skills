"""Verify portable CLI evidence against one durable pilot attempt.

Compatibility is a read-back claim, not a JSON-shape claim. The caller supplies
the declaration and the local controller supplies the immutable run root;
neither alone can earn a compatible classification.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.release_manifest import verify_release_manifest
from tools.schema_validation import schema_diagnostics


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_ROLE_POLICY = "portable-pilot-roles-v1"
_STAGES = (
    ("orchestrate", "controller", "orchestrate-v1"),
    ("context-marker", "generator", "context-marker-v1"),
    ("tc-generator", "generator", "tc-generator-v1"),
    ("tc-reviewer", "canonical-reviewer", "canonical-reviewer-v2"),
    ("tc-to-autotest", "automation-generator", "tc-to-autotest-v1"),
    ("autotest-reviewer", "automation-reviewer", "autotest-static-reviewer-v2"),
)
_STAGE_REGISTRY = {stage: (role, policy) for stage, role, policy in _STAGES}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def _label(value: Any) -> bool:
    return isinstance(value, str) and _LABEL.fullmatch(value) is not None


def _error(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _receipt_ref(row: Mapping[str, Any], *, key: str = "artifact_ref", kind: str, digest: str) -> bool:
    ref = row.get(key)
    return isinstance(ref, Mapping) and set(ref) == {"kind", "digest"} and ref.get("kind") == kind and ref.get("digest") == digest


def _durable(
    evidence: Mapping[str, Any], run_root: Path | None, errors: list[str],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Read the existing Phase 1-7 chain only; this function never repairs it."""
    if run_root is None:
        _error(errors, "DURABLE_CONTEXT_REQUIRED")
        return None, None, None, None
    context = evidence.get("durable_context")
    if not isinstance(context, Mapping):
        _error(errors, "DURABLE_CONTEXT_INVALID")
        return None, None, None, None
    try:
        from tools.pilot_state import (
            derive_state,
            model_lifecycle_projection,
            read_attempt_receipt,
            read_context_selection,
            read_effective_canonical,
            read_execution_inputs,
            read_model_request,
            read_model_stage_artifact,
            read_reviewer_session_ledger,
            read_review_aggregate,
            read_review_plan,
            read_resume_validation_if_present,
            read_run,
            terminal_reviewer_evidence,
        )

        root = Path(run_root).resolve()
        run = read_run(root)
        state = derive_state(root)
        attempt = next((row for row in state["attempts"] if row.get("attempt_id") == context.get("attempt_id")), None)
        if (
            attempt is None
            or run["manifest"].get("run_id") != context.get("run_id")
            or run["manifest"].get("project") != context.get("project")
            or any(context.get(key) != attempt.get(key) for key in ("module", "policy_profile", "baseline_digest"))
        ):
            raise ValueError("attempt identity mismatch")
        attempt_id = str(attempt["attempt_id"])
        ledger = read_reviewer_session_ledger(root, attempt_id, str(context.get("reviewer_session_digest", "")))
        return attempt, ledger, {
            "root": root,
            "aggregate": read_review_aggregate(root, attempt_id),
            "snapshot": read_attempt_receipt(root, attempt_id, "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"],
            "read_aggregate": read_review_aggregate,
            "read_plan": read_review_plan,
            "read_ledger": read_reviewer_session_ledger,
            "state": state,
            "model_lifecycle": model_lifecycle_projection(root, attempt_id),
            "boundary": read_attempt_receipt(root, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK"),
            "effective": read_effective_canonical(root, attempt_id),
            "resume": read_resume_validation_if_present(root, attempt_id),
            "reviewer": terminal_reviewer_evidence(root, attempt_id),
            "read_receipt": read_attempt_receipt,
            "read_context_selection": read_context_selection,
            "read_execution_inputs": read_execution_inputs,
            "read_model_request": read_model_request,
            "read_model_stage_artifact": read_model_stage_artifact,
        }, context
    except (KeyError, OSError, TypeError, ValueError):
        _error(errors, "DURABLE_CONTEXT_INVALID")
        return None, None, None, None


def _model_event_errors(
    stages: Sequence[Mapping[str, Any]],
    attempt: Mapping[str, Any],
    durable: Mapping[str, Any],
    baseline: Mapping[str, Any],
    package: Mapping[str, Any],
    errors: list[str],
) -> None:
    """Bind exact model-stage evidence to the append-only controller journal."""
    events = list(durable["state"]["events"])
    attempt_id = str(attempt["attempt_id"])
    creation = [
        event for event in events
        if event.get("event_type") == "ATTEMPT_CREATED"
        and event.get("attempt_id") == attempt_id
    ]
    if len(creation) != 1:
        _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
        return
    if not any(
        event.get("event_type") == "SNAPSHOT_BOUND"
        and event.get("artifact_digest") == attempt.get("baseline_digest")
        for event in events
    ) or not any(
        event.get("event_type") == "INVENTORY_READY"
        and event.get("artifact_digest") == baseline.get("inventory_digest")
        for event in events
    ):
        _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")

    executed = [
        row for row in stages
        if isinstance(row, Mapping)
        and row.get("status") == "EXECUTED"
        and row.get("stage") != "orchestrate"
    ]
    instances = {str(row.get("stage_instance_id")) for row in executed}
    lifecycle = durable["model_lifecycle"].get("stages", {})
    if not isinstance(lifecycle, Mapping) or any(stage not in instances | {"assembly"} for stage in lifecycle):
        _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")

    def one(event_type: str, stage_instance_id: str) -> tuple[int, Mapping[str, Any]] | None:
        stage = lifecycle.get(stage_instance_id)
        event = stage.get(event_type) if isinstance(stage, Mapping) else None
        return (int(event["seq"]), event) if isinstance(event, Mapping) else None

    def one_artifact_event(
        event_type: str, artifact_digest: Any, *, batch_id: str | None = None,
    ) -> tuple[int, Mapping[str, Any]] | None:
        matches = [
            (int(event["seq"]), event) for event in events
            if event.get("attempt_id") == attempt_id
            and event.get("event_type") == event_type
            and event.get("artifact_digest") == artifact_digest
            and (batch_id is None or event.get("batch_id") == batch_id)
        ]
        return matches[0] if len(matches) == 1 else None

    generator_candidates: list[int] = []
    automation_candidates: dict[str, tuple[int, str]] = {}
    for row in executed:
        instance = str(row.get("stage_instance_id"))
        inputs, outputs = row.get("input_digests"), row.get("output_digests")
        request = one("MODEL_REQUESTED", instance)
        response = one("MODEL_RESPONSE_RECEIVED", instance)
        request_receipt = None
        if request is not None:
            try:
                request_receipt = durable["read_model_request"](
                    durable["root"], attempt_id, instance,
                    str(request[1].get("artifact_digest", "")),
                )
            except (KeyError, TypeError, ValueError):
                request_receipt = None
        if (
            not isinstance(inputs, list) or not inputs
            or not isinstance(outputs, list) or not outputs
            or request is None or response is None
            or not isinstance(request_receipt, Mapping)
            or request_receipt.get("stage_instance_id") != instance
            or request_receipt.get("stage") != row.get("stage")
            or request_receipt.get("role") != row.get("role")
            or request_receipt.get("role_policy") != row.get("role_policy")
            or request_receipt.get("model_id") != row.get("model_id")
            or request_receipt.get("invocation_id") != row.get("invocation_id")
            or request_receipt.get("input_digests") != inputs
            or response[1].get("artifact_digest") != outputs[0]
            or response[1].get("transport_attempts") not in {1, 2, 3}
        ):
            _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
            continue
        if row.get("stage") == "tc-generator":
            candidate = one("CANDIDATE_PUBLISHED", instance)
            batch_id = instance.split(":", 1)[1] if ":" in instance else ""
            batch = next(
                (
                    item for item in package.get("generator_batches", ())
                    if isinstance(item, Mapping) and item.get("batch_id") == batch_id
                ),
                None,
            )
            context_digest = batch.get("context_receipt_digest") if isinstance(batch, Mapping) else None
            if (
                candidate is None
                or candidate[1].get("artifact_digest") != outputs[0]
                or not isinstance(context_digest, str)
                or not any(
                    event.get("event_type") == "CONTEXT_SELECTED"
                    and event.get("attempt_id") == attempt_id
                    and event.get("artifact_digest") == context_digest
                    and int(event["seq"]) < request[0]
                    for event in events
                )
            ):
                _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
            else:
                generator_candidates.append(candidate[0])
        elif row.get("stage") == "tc-to-autotest":
            candidate = one("CANDIDATE_PUBLISHED", instance)
            if candidate is None or candidate[1].get("artifact_digest") != outputs[0]:
                _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
            else:
                automation_candidates[instance] = (candidate[0], outputs[0])

    assembly = one("CANDIDATE_PUBLISHED", "assembly")
    if assembly is None or not generator_candidates or assembly[1].get("artifact_digest") != package.get("candidate_digest"):
        _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
    review_keys = {"canonical"}
    for row in executed:
        if row.get("stage") == "autotest-reviewer":
            match = re.fullmatch(r"autotest-reviewer:(r[12]):part-[0-9]{6}(?:-try[23])?", str(row.get("stage_instance_id")))
            if match is None:
                _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
            else:
                review_keys.add(match[1])
    for key in sorted(review_keys):
        try:
            aggregate = durable["read_aggregate"](durable["root"], attempt_id, key)
            publish = one_artifact_event("ARTIFACT_PUBLISHED", aggregate["digest"])
            readback = one_artifact_event("ARTIFACT_READ_BACK", aggregate["digest"])
            session = durable["read_ledger"](durable["root"], attempt_id, review_key=key)
            ledger_publish = one_artifact_event("ARTIFACT_PUBLISHED", session["digest"], batch_id=f"reviewer-ledger-v2-{key}")
            responses = [one("MODEL_RESPONSE_RECEIVED", str(row["stage_instance_id"])) for row in executed if str(row["stage_instance_id"]).startswith(f"{'tc-reviewer' if key == 'canonical' else 'autotest-reviewer'}:{key}:")]
            if (publish is None or readback is None or ledger_publish is None or not responses
                    or any(response is None or response[0] >= publish[0] for response in responses)
                    or readback[0] <= publish[0] or ledger_publish[0] <= readback[0]):
                _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")
        except (KeyError, TypeError, ValueError):
            _error(errors, "MODEL_EVENT_EVIDENCE_INVALID")



def _stage_errors(evidence: Mapping[str, Any], attempt: Mapping[str, Any], ledger: Mapping[str, Any], durable: Mapping[str, Any], errors: list[str]) -> str:
    branch, stages = evidence.get("branch"), evidence.get("stages")
    if not isinstance(branch, Mapping) or not isinstance(stages, list):
        _error(errors, "STAGE_EVIDENCE_INVALID")
        return "independence_unverified"
    profile = attempt.get("policy_profile")
    if (
        set(branch) != {"canonical_status", "batch_ids", "automation_eligible", "automation_revisions"}
        or branch.get("canonical_status") != "EFFECTIVE"
        or not isinstance(branch.get("batch_ids"), list)
        or not branch.get("batch_ids")
        or len(branch["batch_ids"]) != len(set(branch["batch_ids"]))
        or any(not isinstance(batch_id, str) or re.fullmatch(r"BATCH-[a-z0-9-]+", batch_id) is None for batch_id in branch["batch_ids"])
        or type(branch.get("automation_eligible")) is not bool
        or branch.get("automation_revisions") not in ([], [1], [1, 2])
        or evidence.get("profile") != profile
        or (profile == "cases-only-v1" and (branch.get("automation_eligible") or branch.get("automation_revisions")))
        or (not branch.get("automation_eligible") and branch.get("automation_revisions"))
        or (branch.get("automation_eligible") and not branch.get("automation_revisions"))
    ):
        _error(errors, "BRANCH_EVIDENCE_INVALID")
        return "independence_unverified"
    grouped: dict[str, list[Mapping[str, Any]]] = {name: [] for name in _STAGE_REGISTRY}
    instances: set[str] = set()
    for row in stages:
        if not isinstance(row, Mapping) or not _label(row.get("stage_instance_id")) or row["stage_instance_id"] in instances:
            _error(errors, "STAGE_EVIDENCE_INVALID")
            continue
        instances.add(row["stage_instance_id"])
        stage = row.get("stage")
        if stage not in grouped:
            _error(errors, "STAGE_EVIDENCE_INVALID")
            continue
        grouped[stage].append(row)
        role, policy = _STAGE_REGISTRY[stage]
        if row.get("status") == "EXECUTED" and (row.get("role") != role or row.get("role_policy") != policy):
            _error(errors, "STAGE_REGISTRY_MISMATCH")
    if any(len(grouped[stage]) != 1 for stage in ("orchestrate", "context-marker")) or any(not grouped[stage] for stage in _STAGE_REGISTRY):
        _error(errors, "STAGE_EVIDENCE_INVALID")
        return "independence_unverified"

    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.project_inventory import read_execution_baseline

    boundary, effective = durable["boundary"], durable["effective"]
    baseline = read_execution_baseline(
        durable["root"] / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
    )
    package = durable["snapshot"].get("package_binding")
    if not isinstance(package, Mapping):
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    context_digests = package.get("context_receipt_digests")
    if not isinstance(context_digests, list) or not context_digests:
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    try:
        contexts = [
            durable["read_context_selection"](
                durable["root"], str(attempt["attempt_id"]), str(digest),
            )
            for digest in context_digests
        ]
    except (KeyError, TypeError, ValueError):
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    if [row.get("digest") for row in contexts] != context_digests:
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    context_marker_digest = package.get("context_marker_output_digest")
    try:
        context_marker = durable["read_model_stage_artifact"](
            durable["root"], str(attempt["attempt_id"]),
            "context-marker:baseline", str(context_marker_digest),
        )
        marker_request_event = durable["model_lifecycle"]["stages"]["context-marker:baseline"]["MODEL_REQUESTED"]
        marker_request = durable["read_model_request"](
            durable["root"], str(attempt["attempt_id"]), "context-marker:baseline",
            marker_request_event["artifact_digest"],
        )
    except (KeyError, TypeError, ValueError):
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    marker_artifact = context_marker.get("artifact")
    effective_document = effective.get("document")
    if (
        context_marker.get("content_digest") != context_marker_digest
        or not isinstance(marker_artifact, Mapping)
        or not isinstance(effective_document, Mapping)
        or durable["aggregate"].get("aggregate", {}).get("complete") is not True
        or marker_artifact.get("artifacts", {}).get("analytics_documentation", {}).get("requirements")
        != effective_document.get("source_requirements")
    ):
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    generator_batches = package.get("generator_batches")
    if (
        not isinstance(generator_batches, list)
        or not generator_batches
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"batch_id", "digest", "context_receipt_digest"}
            or not isinstance(item.get("batch_id"), str)
            or re.fullmatch(r"BATCH-[a-z0-9-]+", item["batch_id"]) is None
            or not isinstance(item.get("digest"), str)
            or _DIGEST.fullmatch(item["digest"]) is None
            or item.get("context_receipt_digest") not in context_digests
            for item in generator_batches
        )
        or branch["batch_ids"] != [item["batch_id"] for item in generator_batches]
        or len(grouped["tc-generator"]) != len(generator_batches)
    ):
        _error(errors, "BRANCH_EVIDENCE_INVALID")
        return "independence_unverified"
    contexts_by_digest = {row["digest"]: row for row in contexts}
    try:
        fragments = [
            durable["read_model_stage_artifact"](
                durable["root"], str(attempt["attempt_id"]),
                f"tc-generator:{item['batch_id']}", item["digest"],
            )
            for item in generator_batches
        ]
    except (KeyError, TypeError, ValueError):
        _error(errors, "STAGE_READBACK_INVALID")
        return "independence_unverified"
    for batch, publication in zip(generator_batches, fragments):
        fragment = publication.get("artifact")
        context_digest = batch["context_receipt_digest"]
        if (
            publication.get("content_digest") != batch["digest"]
            or not isinstance(fragment, Mapping)
            or fragment.get("status") != "COMPLETE"
            or fragment.get("batch_id") != batch["batch_id"]
            or fragment.get("context_receipt_digest") != context_digest
            or fragment.get("context_receipt") != contexts_by_digest[context_digest]
        ):
            _error(errors, "STAGE_READBACK_INVALID")
    expected = {
        "orchestrate": (
            "orchestrate:attempt", "attempt", attempt["digest"], None,
            [evidence["release_manifest_digest"]], [attempt["digest"]],
        ),
        "context-marker": (
            "context-marker:baseline", "model-stage-artifact", context_marker_digest, None,
            marker_request["input_digests"], [context_marker_digest],
        ),
    }
    exact_keys = {"stage_instance_id", "stage", "status", "role", "role_policy", "model_id", "invocation_id", "input_digests", "output_digests", "artifact_ref"}
    model_invocations: list[str] = []
    for stage, (instance, kind, digest, invocation, input_digests, output_digests) in expected.items():
        row = grouped[stage][0]
        if (
            set(row) != exact_keys or row.get("status") != "EXECUTED" or row.get("stage_instance_id") != instance
            or not _receipt_ref(row, kind=kind, digest=digest)
            or row.get("input_digests") != input_digests
            or row.get("output_digests") != output_digests
        ):
            _error(errors, "STAGE_READBACK_INVALID")
        if stage == "orchestrate":
            if row.get("model_id") is not None or row.get("invocation_id") is not None:
                _error(errors, "STAGE_EVIDENCE_INVALID")
        else:
            if not _label(row.get("model_id")):
                _error(errors, "STAGE_MODEL_INVALID")
            if not _label(row.get("invocation_id")) or invocation is not None and row.get("invocation_id") != invocation:
                _error(errors, "STAGE_READBACK_INVALID")
            elif isinstance(row.get("invocation_id"), str):
                model_invocations.append(row["invocation_id"])

    generator_by_instance = {row.get("stage_instance_id"): row for row in grouped["tc-generator"]}
    generator_instances = [f"tc-generator:{item['batch_id']}" for item in generator_batches]
    for index, (instance, batch, publication) in enumerate(zip(generator_instances, generator_batches, fragments)):
        row = generator_by_instance.get(instance)
        fragment = publication.get("artifact") if isinstance(publication, Mapping) else None
        expected_invocation = None
        if (
            not isinstance(row, Mapping)
            or not isinstance(fragment, Mapping)
            or set(row) != exact_keys
            or row.get("status") != "EXECUTED"
            or not _receipt_ref(row, kind="model-stage-artifact", digest=batch["digest"])
            or row.get("input_digests") != [
                context_marker_digest, batch["context_receipt_digest"],
                fragment.get("plan_digest"), fragment.get("header_digest"),
            ]
            or row.get("output_digests") != [batch["digest"]]
            or not _label(row.get("model_id"))
            or not _label(row.get("invocation_id"))
            or expected_invocation is not None and row.get("invocation_id") != expected_invocation
        ):
            _error(errors, "STAGE_READBACK_INVALID")
        elif isinstance(row.get("invocation_id"), str):
            model_invocations.append(row["invocation_id"])
    if len({row.get("model_id") for row in grouped["tc-generator"]}) != 1:
        _error(errors, "STAGE_MODEL_INVALID")

    automation, revisions = bool(branch.get("automation_eligible")), list(branch.get("automation_revisions", []))
    if not automation:
        if any(len(grouped[stage]) != 1 for stage in ("tc-to-autotest", "autotest-reviewer")):
            _error(errors, "BRANCH_EVIDENCE_INVALID")
        for stage in ("tc-to-autotest", "autotest-reviewer"):
            row = grouped[stage][0]
            if (
                set(row) != {"stage_instance_id", "stage", "status", "reason_code", "branch_evidence_ref"}
                or row.get("status") != "NOT_APPLICABLE"
                or row.get("reason_code") not in {"PROFILE_CASES_ONLY", "CANONICAL_MANUAL_ONLY", "PRE_AUTOMATION_TERMINAL", "UNSUPPORTED_TUPLE"}
                or not _receipt_ref(row, key="branch_evidence_ref", kind="effective-canonical", digest=effective["digest"])
            ):
                _error(errors, "BRANCH_EVIDENCE_INVALID")
    else:
        try:
            inputs = durable["read_execution_inputs"](durable["root"], str(attempt["attempt_id"]))
            revision = inputs["autotest_review"]["artifacts"]["autotest_review"]["automation_revision"]
            kind = f"automation-review-boundary-r{revision}"
            auto_boundary = durable["read_receipt"](durable["root"], str(attempt["attempt_id"]), kind, "ARTIFACT_READ_BACK")
        except (KeyError, TypeError, ValueError):
            _error(errors, "AUTOMATION_DURABLE_EVIDENCE_INVALID")
            return "independence_unverified"
        if revision != revisions[-1]:
            _error(errors, "AUTOMATION_DURABLE_EVIDENCE_INVALID")
        for revision in revisions:
            instance = f"tc-to-autotest:r{revision}"
            try:
                request_event = durable["model_lifecycle"]["stages"][instance]["MODEL_REQUESTED"]
                auto_request = durable["read_model_request"](durable["root"], str(attempt["attempt_id"]), instance, request_event["artifact_digest"])
                aggregate = durable["read_aggregate"](durable["root"], str(attempt["attempt_id"]), f"r{revision}")
                snapshot = durable["read_receipt"](durable["root"], str(attempt["attempt_id"]), f"review-snapshot-r{revision}", "ARTIFACT_READ_BACK")["record"]["payload"]
                automation_digest = automation_sha256(snapshot["automation"])
                generated = durable["read_model_stage_artifact"](durable["root"], str(attempt["attempt_id"]), instance, automation_digest)
                row = next(item for item in grouped["tc-to-autotest"] if item.get("stage_instance_id") == instance)
                if (set(row) != exact_keys or row.get("status") != "EXECUTED"
                        or row.get("invocation_id") != auto_request["invocation_id"] or row.get("model_id") != auto_request["model_id"]
                        or not _label(row.get("model_id"))
                        or not _receipt_ref(row, kind="model-stage-artifact", digest=automation_digest)
                        or row.get("input_digests") != auto_request["input_digests"] or row.get("output_digests") != [automation_digest]
                        or generated["artifact"] != snapshot["automation"]):
                    raise ValueError("automation generator readback differs")
                model_invocations.append(row["invocation_id"])
            except (KeyError, StopIteration, TypeError, ValueError):
                _error(errors, "AUTOMATION_DURABLE_EVIDENCE_INVALID")

    def review_parts(key: str, stage: str) -> tuple[list[str], Mapping[str, Any]]:
        try:
            aggregate = durable["read_aggregate"](durable["root"], str(attempt["attempt_id"]), key)
            plan = durable["read_plan"](durable["root"], str(attempt["attempt_id"]), key)
            session = durable["read_ledger"](durable["root"], str(attempt["attempt_id"]), review_key=key)
            additions = [event["part"] for event in session["events"] if event["event_type"] == "REVIEW_CHECK_ADDED"]
            parts = [*plan["parts"], *additions]
            report = aggregate["output"]["artifacts"]["validation_report" if key == "canonical" else "autotest_review"]
            allowed_verdicts = {"ПРИНЯТО", "AUTO_FIX_APPLIED"} if key == "canonical" else {"ПРИНЯТО"} if int(key[1]) == revisions[-1] else {"AUTO_FIX_APPLIED"}
            if report.get("verdict") not in allowed_verdicts:
                raise ValueError("review verdict does not authorize selected branch")
            def answered_instance(part_id: str) -> str:
                # A part whose invocation failed is re-invoked as ``<part>-try2`` / ``-try3``;
                # the evidence names the try that produced the assessment (the latest one).
                tries = [f"{stage}:{key}:{part_id}", f"{stage}:{key}:{part_id}-try2", f"{stage}:{key}:{part_id}-try3"]
                recorded = [instance for instance in tries if "MODEL_RESPONSE_RECEIVED" in durable["model_lifecycle"]["stages"].get(instance, {})]
                return recorded[-1] if recorded else tries[0]

            planned = [answered_instance(part["part_id"]) for part in parts]
            selected = [row for row in grouped[stage] if str(row.get("stage_instance_id", "")).startswith(f"{stage}:{key}:")]
            if ([row.get("stage_instance_id") for row in selected] != planned
                    or aggregate["aggregate"].get("complete") is not True or session.get("status") != "COMPLETED"):
                raise ValueError("review part coverage differs")
            for row, part in zip(selected, parts):
                instance = row["stage_instance_id"]
                boundary = durable["read_receipt"](durable["root"], str(attempt["attempt_id"]), f"review-part-boundary-{key}-{instance.rsplit(':', 1)[1]}", "ARTIFACT_READ_BACK")["record"]
                event = durable["model_lifecycle"]["stages"][instance]["MODEL_RESPONSE_RECEIVED"]
                output = durable["read_model_stage_artifact"](durable["root"], str(attempt["attempt_id"]), instance, event["artifact_digest"])
                if (set(row) != exact_keys or row.get("status") != "EXECUTED"
                        or row.get("invocation_id") != boundary["reviewer_invocation_id"]
                        or row.get("model_id") != boundary["model_id"] or not _label(row.get("model_id"))
                        or not _receipt_ref(row, kind="model-stage-artifact", digest=output["content_digest"])
                        or row.get("output_digests") != [output["content_digest"]]):
                    raise ValueError("review part readback differs")
                model_invocations.append(row["invocation_id"])
            return planned, aggregate
        except (KeyError, TypeError, ValueError):
            _error(errors, "STAGE_READBACK_INVALID")
            return [], {}

    canonical_instances, canonical_aggregate = review_parts("canonical", "tc-reviewer")
    if len(grouped["tc-reviewer"]) != len(canonical_instances):
        _error(errors, "STAGE_EVIDENCE_INVALID")
    bindings = evidence.get("review_bindings")
    expected_canonical = {
        "kind": "canonical", "generator_stage_instance_ids": generator_instances,
        "reviewer_stage_instance_ids": canonical_instances, "reviewer_session_digest": ledger["digest"],
        "reviewer_boundary_digest": boundary["digest"], "review_aggregate_receipt_digest": canonical_aggregate.get("digest"),
    }
    canonical = [row for row in bindings if isinstance(row, Mapping) and row.get("kind") == "canonical"] if isinstance(bindings, list) else []
    if canonical != [expected_canonical]:
        _error(errors, "REVIEWER_BINDING_INVALID")
    automation_bindings = [row for row in bindings if isinstance(row, Mapping) and row.get("kind") == "automation"] if isinstance(bindings, list) else []
    expected_bindings = []
    planned_automation = []
    if automation:
        for revision in revisions:
            key = f"r{revision}"
            planned, aggregate = review_parts(key, "autotest-reviewer")
            planned_automation.extend(planned)
            boundary_receipt = durable["read_receipt"](durable["root"], str(attempt["attempt_id"]), f"automation-review-boundary-{key}", "ARTIFACT_READ_BACK")
            expected_bindings.append({"kind": "automation", "revision": revision,
                "generator_stage_instance_id": f"tc-to-autotest:{key}", "reviewer_stage_instance_ids": planned,
                "reviewer_boundary_digest": boundary_receipt["digest"], "review_aggregate_receipt_digest": aggregate.get("digest")})
        if len(grouped["autotest-reviewer"]) != len(planned_automation) or len(grouped["tc-to-autotest"]) != len(revisions):
            _error(errors, "STAGE_EVIDENCE_INVALID")
    if automation_bindings != expected_bindings:
        _error(errors, "AUTOMATION_REVIEW_BINDING_INVALID")
    if len(model_invocations) != len(set(model_invocations)):
        _error(errors, "INVOCATION_REUSED")
    if all(isinstance(row, Mapping) for row in stages):
        _model_event_errors(stages, attempt, durable, baseline, package, errors)
    return "verified" if canonical_instances and not errors else "independence_unverified"



def validate_compatibility_evidence(
    evidence: Mapping[str, Any], release_manifest: Mapping[str, Any],
    release_eval_receipt: Mapping[str, Any] | None = None, *, pack_root: Path | None = None,
    run_root: Path | None = None,
) -> dict[str, Any]:
    """Fail closed; only a read-back pilot attempt can be `compatible`.

    `verified` is deliberately unavailable here: an in-memory release-eval JSON
    is not authoritative campaign readback. The release evaluator must project
    that stronger level from its own confined/recomputed receipt.
    """
    errors: list[str] = []
    value = dict(evidence) if isinstance(evidence, Mapping) else {}
    root = (pack_root or Path(__file__).resolve().parents[1]).resolve()
    if schema_diagnostics(value, root / "schemas" / "compatibility-evidence.schema.json", root):
        _error(errors, "EVIDENCE_SCHEMA_INVALID")
    try:
        manifest = verify_release_manifest(root, dict(release_manifest))
    except (OSError, TypeError, ValueError):
        manifest = {}
        _error(errors, "RELEASE_MANIFEST_INVALID")
    required = {"schema_version", "compatibility_version", "release_manifest_digest", "host", "role_policy", "profile", "branch", "registry", "durable_context", "stages", "resume", "review_bindings", "digest"}
    if set(value) != required or value.get("digest") != evidence_digest(value):
        _error(errors, "EVIDENCE_ENVELOPE_INVALID")
    if value.get("compatibility_version") != manifest.get("compatibility_contract_version") or value.get("release_manifest_digest") != manifest.get("digest") or value.get("registry") != manifest.get("registry") or value.get("role_policy") != _ROLE_POLICY:
        _error(errors, "RELEASE_MANIFEST_INVALID")
    host = value.get("host")
    if not isinstance(host, Mapping) or set(host) != {"host_id", "runtime_version", "os", "language_runtime"} or not all(item is None or _label(item) for item in host.values()):
        _error(errors, "HOST_IDENTITY_INVALID")
    attempt, ledger, durable, _context = _durable(value, run_root, errors)
    independence = "independence_unverified"
    if attempt is not None and ledger is not None and durable is not None:
        resume, actual_resume = value.get("resume"), durable["resume"]
        if not isinstance(resume, Mapping) or set(resume) != {"attempt_id", "resume_validation_digest"} or resume.get("attempt_id") != attempt.get("attempt_id") or resume.get("resume_validation_digest") != (actual_resume.get("digest") if isinstance(actual_resume, Mapping) else None):
            _error(errors, "DURABLE_RESUME_INVALID")
        independence = _stage_errors(value, attempt, ledger, durable, errors)
    if release_eval_receipt is not None:
        _error(errors, "RELEASE_EVAL_READBACK_REQUIRED")
    errors.sort()
    return {"level": "compatible" if not errors else "incompatible", "independence": independence, "errors": errors}
