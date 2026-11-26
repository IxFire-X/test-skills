"""The records of a step of the model (v0.8, the cut of the core): where the request, the answer and the selection of the context of a
stage lie and how they are read back with their checks, the parts of the context marker and the check of its sources (OpenSpec among
them), the exception of a step.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v2b.tsv``);
what they read of the journal they take by its public names, and ``pilot_state`` still serves their names to its callers (the facade of
the cut).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.confined_output import OutputConfinementError, read_confined_bytes
from tools.project_inventory import ContextSelectionError, validate_context_receipt_binding
from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    CLOSURE_CACHE as _CLOSURE_CACHE,
    DIGEST as _DIGEST,
    MODEL_STAGE_REGISTRY as _MODEL_STAGE_REGISTRY,
    PACK_ROOT as _PACK_ROOT,
    REWORK_BATCH,
    REWORK_REVIEW_STAGE as _REWORK_REVIEW_STAGE,
    REWORK_STAGE as _REWORK_STAGE,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    check_sealed_value as _check_sealed,
    derive_state,
    frozen_baseline,
    frozen_inventory as _frozen_inventory,
    parse_json_bytes as _parse_json_bytes,
    read_attempt_receipt_with_state as _read_attempt_receipt_with_state,
    read_reviewer_session_ledger_with_state as _read_reviewer_session_ledger_with_state,
    read_run,
    run_root_of as _run_root,
    validate_schema as _validate_schema,
)


def _context_selection_target(root: Path, attempt_id: str, receipt_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or not _DIGEST.fullmatch(receipt_digest):
        raise ValueError("invalid context selection identity")
    return root / "context-selections" / attempt_id / (receipt_digest.removeprefix("sha256:") + ".json")


def _model_stage_artifact_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("invalid model stage artifact") from error


def _model_stage_artifact_digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_model_stage_artifact_bytes(value)).hexdigest()


def _model_request_target(root: Path, attempt_id: str, request_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or not _DIGEST.fullmatch(request_digest):
        raise ValueError("invalid model request identity")
    return root / "model-requests" / attempt_id / (
        request_digest.removeprefix("sha256:") + ".json"
    )


def _context_marker_parts(state: Mapping[str, Any], attempt_id: str) -> tuple[tuple[str, str | None] | None, list[tuple[str, str, str | None]]]:
    """``(requirements (request, response) or None, [(batch stage, request, response)…] in batch order)``."""
    requests: dict[str, str] = {}
    responses: dict[str, str] = {}
    for event in state["events"]:
        stage = str(event.get("stage_instance_id") or "")
        if event.get("attempt_id") != attempt_id or not stage.startswith("context-marker:") or stage == "context-marker:baseline":
            continue
        if event["event_type"] == "MODEL_REQUESTED":
            requests[stage] = str(event.get("artifact_digest"))
        elif event["event_type"] == "MODEL_RESPONSE_RECEIVED":
            responses[stage] = str(event.get("artifact_digest"))
    requirements = requests.get("context-marker:requirements")
    batches = [(stage, requests[stage], responses.get(stage)) for stage in sorted(requests) if stage.startswith("context-marker:batch-")]
    return (None if requirements is None else (requirements, responses.get("context-marker:requirements"))), batches


def _published_context_receipts(state: Mapping[str, Any], attempt_id: str, stage_instance_id: str) -> list[str]:
    """Every context receipt the attempt published, once each, in publication order: batch-NNN is the N-th of them.

    A recorded request binds the receipts published before it; reviewer evidence published later is not its input."""
    cutoff = next((event["seq"] for event in state["events"] if event.get("attempt_id") == attempt_id
                   and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage_instance_id), None)
    return list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                              if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"
                              and (cutoff is None or event["seq"] < cutoff)))


def _context_marker_receipt_digests(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str, inputs: Sequence[str],
) -> list[str]:
    """The context receipts a marker request binds after requirements and inventory.

    The requirements step binds none (documents only).  A part binds exactly one receipt and then the requirements
    answer.  The merged ``context-marker:baseline`` of a marker in parts binds every part's receipt in part order, the
    requirements answer and every part answer in the same order (an identical answer once); a single marker binds the
    receipts only, as before.
    """
    requirements, batches = _context_marker_parts(state, attempt_id)
    published = _published_context_receipts(state, attempt_id, stage_instance_id)
    if stage_instance_id == "context-marker:requirements":
        if len(inputs) != 2:
            raise ValueError("context-marker requirements step binds the requirements and the inventory only")
        return []
    if stage_instance_id.startswith("context-marker:batch-"):
        # [inventory, receipt, requirements answer]; the runs of an earlier version also bound the documents digest first
        if len(inputs) not in (3, 4) or requirements is None or requirements[1] is None or inputs[-1] != requirements[1]:
            raise ValueError("context-marker part must bind one context receipt and the requirements answer")
        number = int(stage_instance_id.rsplit("-", 1)[1])
        if not 1 <= number <= len(published) or inputs[-2] != published[number - 1]:
            raise ValueError(f"{stage_instance_id} must bind context receipt {number} of the attempt (batch-NNN is the N-th published receipt)")
        return [inputs[-2]]
    if not batches:
        if list(inputs[2:]) != published:
            raise ValueError("context-marker:baseline must bind every context receipt of the attempt, once, in publication order")
        return list(inputs[2:])
    if requirements is None or requirements[1] is None or any(response is None for _stage, _request, response in batches):
        raise ValueError("merged context-marker request requires every part answer")
    receipts = [_read_model_request_with_state(project, root, state, attempt_id, stage, request)["input_digests"][-2] for stage, request, _response in batches]
    if receipts != published:
        missing = [f"batch-{index:03d}" for index, digest in enumerate(published, start=1) if digest not in receipts]
        raise ValueError(f"CONTEXT_MARKER_PARTS_INCOMPLETE: the parts must cover every context receipt of the attempt once, in order; missing {missing}")
    if list(inputs[2:]) != [*receipts, requirements[1], *dict.fromkeys(str(response) for _stage, _request, response in batches)]:
        raise ValueError("merged context-marker request must bind every part receipt, the requirements answer and every part answer in order")
    return receipts


def _context_marker_snapshots(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage_instance_id: str, inputs: Sequence[str],
    receipts: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """The OpenSpec sources of the receipts the request binds (or of ``receipts``), each checked against its bytes."""
    from tools.build_context import _OPENSPEC_PATH
    from tools.project_inventory import read_execution_baseline

    baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    minimum = 2 if stage_instance_id == "context-marker:requirements" else 3
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    if stage_instance_id.startswith("context-marker:batch-") and len(inputs) == 3:
        head = head[1:]  # a part binds the inventory, not the documents again (the answer of the requirements step does)
    if baseline["digest"] != attempt["baseline_digest"] or list(inputs[:len(head)]) != head or len(inputs) < minimum:
        raise ValueError("context-marker inputs must bind baseline requirements, inventory, and authorized context receipts")
    snapshots: dict[str, dict[str, str]] = {}
    if receipts is None:
        receipts = _context_marker_receipt_digests(project, root, state, str(attempt["attempt_id"]), stage_instance_id, inputs)
    items: list[Mapping[str, Any]] = []
    for digest in receipts:
        receipt = read_context_selection(root, str(attempt["attempt_id"]), digest)
        if receipt["inventory_digest"] != baseline["inventory_digest"]:
            raise ValueError("context-marker receipt must bind the attempt inventory")
        items += receipt["files"]
    from tools.build_context import openspec_change_only, openspec_selected_changes, openspec_unnamed_baseline
    from tools.cases_import import cases_path

    named = _named_docs(project, root)
    base = openspec_change_only(named, [str(item["project_path"]).replace("\\", "/") for item in items], cases_path(root) is not None)
    proposals = {f"openspec/changes/{name}/proposal.md" for name in openspec_selected_changes(named)} if base else set()  # the «Impact» of a change only
    for item in items:
        path = item["project_path"].replace("\\", "/")
        if not _OPENSPEC_PATH.fullmatch(path) and not path.startswith("openspec/changes/archive/") and path not in proposals:
            continue
        raw = read_confined_bytes(project, project / "openspec", project / path)  # the project itself is no confined root
        if raw is None or "sha256:" + hashlib.sha256(raw).hexdigest() != item["content_digest"]:
            raise ValueError("context-marker OpenSpec source bytes drifted")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("context-marker OpenSpec source is not UTF-8") from error
        snapshots[path] = {"path": path, "sha256": item["content_digest"], "content": content}
    if not base:
        return [row for row in snapshots.values() if not (named and openspec_unnamed_baseline(row["path"], named))]  # the areas --docs does not name are context, no requirements
    # change only (decision of 25.11): the spec.md of an area of the change is the base of the composition, the other spec.md and the
    # proposal give the neighbours — both marked, neither gives a requirement of the run; no mark at all in any other run
    return [{**row, "base": True} if row["path"] in base else
            {**row, "context": True} if row["path"] in proposals or openspec_unnamed_baseline(row["path"], named) else row for row in snapshots.values()]


def openspec_snapshots(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The OpenSpec sources of the context receipts of ``attempt``, checked against their bytes, as the requirements step of the marker
    composes them (the view «было → стало» of the generator reads the same)."""
    baseline, stage = frozen_baseline(root, attempt), "context-marker:requirements"
    return _context_marker_snapshots(project, root, state, attempt, stage, [baseline["requirements"]["digest"], baseline["inventory_digest"]],
                                     _published_context_receipts(state, str(attempt["attempt_id"]), stage))


def _named_docs(project: Path, root: Path) -> list[str]:
    """The files ``--docs`` named, as the project's own paths (from the root, forward slashes) however they were typed."""
    from tools.doc_sections import resolve_spec
    from tools.run_scan import relative_to_project

    return [relative_to_project(project.resolve(), resolve_spec(project, doc)[0]) for doc in (read_run(root)["manifest"].get("scan_request") or {}).get("docs") or []]


def _validate_context_marker_sources(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], request: Mapping[str, Any], value: Mapping[str, Any],
) -> None:
    from tools.build_context import openspec_diagnostics
    from tools.context_marker_merge import merge_marker_artifacts, requirements_of

    stage_instance_id = str(request["stage_instance_id"])
    attempt_id = str(attempt["attempt_id"])
    snapshots = _context_marker_snapshots(project, root, state, attempt, stage_instance_id, request["input_digests"])
    requirements, batches = _context_marker_parts(state, attempt_id)
    if stage_instance_id.startswith("context-marker:batch-"):
        answer = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:requirements", str(requirements[1]))["artifact"]
        if requirements_of(value) != requirements_of(answer):
            raise ValueError("CONTEXT_MARKER_REQUIREMENTS_CHANGED: a part may add code observations only, not change the requirements")
        return
    if stage_instance_id != "context-marker:baseline":
        return  # the requirements step: OpenSpec is reconciled on its write (_reconcile_requirements_openspec) and on the merged marker
    if batches:
        # The merged marker is a pure function of the recorded answers: it is recomputed on every write and read.
        answer = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:requirements", str(requirements[1]))["artifact"]
        parts = [_read_model_stage_artifact_with_state(project, root, state, attempt_id, stage, str(response))["artifact"]
                 for stage, _request, response in batches]
        from tools.cases_import import run_marker_requirements

        derived = run_marker_requirements(root, attempt, answer)  # an import with documents
        if dict(value) != merge_marker_artifacts(answer, parts, derived):
            raise ValueError("merged context-marker artifact differs from the merge of its parts")
        from tools.cases_import import is_code_requirements

        if is_code_requirements(root, attempt, answer):
            return  # the requirements of an import without documents are its cases, not an OpenSpec reading
    if snapshots:
        diagnostics = openspec_diagnostics(dict(value), snapshots)
        if diagnostics:
            raise ValueError("CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: " + json.dumps(diagnostics, ensure_ascii=False))


def _validate_model_request_predecessors(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any],
    stage_instance_id: str, invocation_id: str, inputs: Sequence[str],
    request_seq: int | None = None,
) -> None:
    """Bind generator/reviewer requests to their exact durable predecessors."""
    stage = stage_instance_id.split(":", 1)[0]
    if stage not in {"tc-generator", "tc-reviewer"}:
        return
    attempt_id = str(attempt["attempt_id"])
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id]
    cutoff = request_seq if request_seq is not None else state["events"][-1]["seq"] + 1
    if stage_instance_id == _REWORK_STAGE:  # the rework is asked for after the first review sent cases back (its receipt is in the journal before the request)
        if not any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("batch_id") == REWORK_BATCH and event["seq"] < cutoff for event in events):
            raise ValueError("tc-generator:rework-1 is requested only after the first review sent cases back")
    elif stage_instance_id == _REWORK_REVIEW_STAGE:  # the review of the rework: asked for on the answer of the generator of the rework
        if not any(event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id and event["seq"] < cutoff
                   and event.get("artifact_digest") == inputs[0] for event in events):
            raise ValueError("tc-reviewer:rework-1 is requested only on the review request of the answer of the rework")
    elif stage == "tc-generator":
        from tools.project_inventory import read_execution_baseline

        markers = [event for event in events if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED"]
        # from batch 2 a fifth input — the capabilities of the earlier batches (its receipt)
        # the exact list (M context receipts) is checked at publish time by _expected_model_inputs
        if len(inputs) < 4 or len(markers) != 1 or markers[0]["seq"] >= cutoff or inputs[0] != markers[0].get("artifact_digest"):
            raise ValueError("tc-generator input digests must bind its prior marker, context, plan, and header")
        _read_model_stage_artifact_with_state(project, root, state, attempt_id, "context-marker:baseline", inputs[0])
        context = read_context_selection(root, attempt_id, inputs[1])
        baseline = read_execution_baseline(project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
        selected = [event for event in events if event["event_type"] == "CONTEXT_SELECTED" and event.get("artifact_digest") == inputs[1] and event["seq"] < cutoff]
        if baseline["digest"] != attempt["baseline_digest"] or context["inventory_digest"] != baseline["inventory_digest"] or not selected:
            raise ValueError("tc-generator context must bind its prior attempt inventory selection")
    else:
        boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
        # only the package binding is wanted (the same in every revision), so a latest revision still waiting for its READ_BACK will do
        ledger = _read_reviewer_session_ledger_with_state(project, root, state, attempt_id, unconfirmed_ok=True)
        package = ledger["package_binding"]
        reviews = [event for event in events if event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == stage_instance_id]
        prior_boundary = any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("artifact_digest") == boundary["digest"] and event["seq"] < cutoff for event in events)
        prior_ledger = any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("batch_id") == "reviewer-ledger-v1" and event["seq"] < cutoff for event in events)
        if (
            list(inputs) != [boundary["canonical_branch_digest"], boundary["package_digest"]]
            or invocation_id != boundary["reviewer_invocation_id"]
            or package.get("candidate_digest") != boundary["canonical_branch_digest"]
            or package.get("package_digest") != boundary["package_digest"]
            or len(reviews) != 1
            or reviews[0].get("artifact_digest") != boundary["canonical_branch_digest"]
            or reviews[0]["seq"] >= cutoff
            or not prior_boundary or not prior_ledger
        ):
            raise ValueError("tc-reviewer input digests and invocation must bind its prior candidate and reviewer boundary")


def _is_context_selection(root: Path, attempt_id: str, digest: str) -> bool:
    try:
        read_context_selection(root, attempt_id, digest)
    except (ValueError, OSError):
        return False
    return True


def _validate_generator_artifact_binding(root: Path, attempt_id: str, request: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    if request["stage"] != "tc-generator" or request.get("stage_instance_id") == _REWORK_STAGE:
        return
    inputs = request["input_digests"]
    context = read_context_selection(root, attempt_id, inputs[1])
    # plan and header follow the context receipts of the plan — one, or its list
    after = inputs.index(value.get("plan_digest"), 2) if value.get("plan_digest") in inputs[2:] else 2
    if (
        [value.get("context_receipt_digest"), value.get("plan_digest"), value.get("header_digest")] != [inputs[1], *inputs[after:after + 2]]
        or any(not _is_context_selection(root, attempt_id, digest) for digest in inputs[2:after])
        or value.get("context_receipt") != context
    ):
        raise ValueError("generator fragment must match its request and stored context receipt")


def _read_model_request_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    request_digest: str,
) -> Mapping[str, Any]:
    target = _model_request_target(root, attempt_id, request_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        value = _parse_json_bytes(raw, "model request") if raw is not None else None
    except (OutputConfinementError, OSError, ValueError) as error:
        raise ValueError("model request is unreadable") from error
    stage = stage_instance_id.split(":", 1)[0]
    declaration = _MODEL_STAGE_REGISTRY.get(stage)
    if not isinstance(value, Mapping) or declaration is None:
        raise ValueError("model request is unbound")
    _check_sealed(value, "model request")
    _validate_schema("model-request.schema.json", value)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    run_id = attempt.get("run_id") if isinstance(attempt, Mapping) else None
    role, role_policy = declaration
    events = [
        event for event in state["events"]
        if event.get("event_type") == "MODEL_REQUESTED"
        and event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("artifact_digest") == request_digest
    ]
    expected_batch = stage_instance_id.split(":", 1)[1] if stage == "tc-generator" else None
    if (
        attempt is None
        or value.get("digest") != request_digest
        or value.get("run_id") != run_id
        or value.get("attempt_id") != attempt_id
        or value.get("stage_instance_id") != stage_instance_id
        or value.get("stage") != stage
        or value.get("role") != role
        or value.get("role_policy") != role_policy
        or len(events) != 1
        or events[0].get("batch_id") != expected_batch
    ):
        raise ValueError("model request is unbound")
    _validate_model_request_predecessors(
        project, root, state, attempt, stage_instance_id, value["invocation_id"],
        value["input_digests"], events[0]["seq"],
    )
    return dict(value)


def _undeclared_capabilities(fragment: Mapping[str, Any]) -> list[str]:
    """The capabilities the steps of a fragment use and its ``operation_capabilities`` do not declare (those of an earlier batch, not copied)."""
    declared = {row.get("capability_id") for row in fragment.get("operation_capabilities") or [] if isinstance(row, Mapping)}
    used = {step["operation"]["capability_id"] for case in fragment.get("test_cases") or [] if isinstance(case, Mapping) for step in case.get("steps") or []
            if isinstance(step, Mapping) and isinstance(step.get("operation"), Mapping) and step["operation"].get("kind") == "project_action"
            and isinstance(step["operation"].get("capability_id"), str)}
    return sorted(used - declared)


def _validate_model_stage_artifact(stage_instance_id: str, value: Mapping[str, Any]) -> None:
    if stage_instance_id.startswith("context-marker:"):
        schema_name = "context-marker-output.schema.json"
        if value.get("stage") != "context-marker":
            raise ValueError("invalid context-marker artifact")
    elif stage_instance_id == _REWORK_STAGE:
        return  # its form and its cases are checked against the journal (``check_rework_answer``): no schema stands for the cases alone
    elif stage_instance_id.startswith("tc-generator:"):
        schema_name = "candidate-fragment.schema.json"
        batch_id = stage_instance_id.split(":", 1)[1]
        body = {key: item for key, item in value.items() if key != "digest"}
        context = value.get("context_receipt")
        context_body = {
            key: item for key, item in context.items() if key != "digest"
        } if isinstance(context, Mapping) else {}
        if value.get("batch_id") != batch_id:
            raise ValueError(f"MODEL_STAGE_ARTIFACT_INVALID: {stage_instance_id}: в слоте лежит фрагмент пачки {value.get('batch_id')}, а ответ этой стадии — фрагмент "
                             f"пачки {batch_id}; fix: положи в слот фрагмент пачки {batch_id} (batch_id и namespace — из плана); ничего не записано, "
                             "отдай его единственным повтором стадии (--transport-attempts 2)")
        if (
            value.get("digest") != _model_stage_artifact_digest(body)
            or not isinstance(context, Mapping)
            or context.get("digest") != _model_stage_artifact_digest(context_body)
        ):
            raise ValueError("invalid generator fragment artifact")
    elif stage_instance_id in ("tc-reviewer:canonical", _REWORK_REVIEW_STAGE):
        schema_name = "tc-reviewer-output.schema.json"
    elif stage_instance_id.startswith("tc-to-autotest:"):
        schema_name = "tc-to-autotest-output.schema.json"
        revision = value.get("artifacts", {}).get("automation_revision")
        if stage_instance_id != f"tc-to-autotest:r{revision}":
            raise ValueError("invalid automation generator artifact")
    elif stage_instance_id.startswith("autotest-reviewer:"):
        schema_name = "autotest-reviewer-output.schema.json"
        revision = value.get("artifacts", {}).get("autotest_review", {}).get("automation_revision")
        if stage_instance_id != f"autotest-reviewer:r{revision}":
            raise ValueError("invalid automation reviewer artifact")
    else:
        raise ValueError("unsupported model stage artifact")
    diagnostics = schema_diagnostics(dict(value), _SCHEMA_ROOT / schema_name, _PACK_ROOT)
    if diagnostics:  # the same retry_prompt validate_artifact gives, so the one retry needs no second command
        from tools.validate_artifact import retry_prompt

        missing = _undeclared_capabilities(value) if stage_instance_id.startswith("tc-generator:BATCH-") and any(str(row.get("path") or "").endswith("/semantic_type") for row in diagnostics) else []
        cause = (f"возможная причина semantic_type: capability {', '.join(missing[:5])}{' …' if len(missing) > 5 else ''} нет в operation_capabilities этого фрагмента, а тип берётся из неё; "
                 "скопируй её слово в слово из входа prior_capabilities (capabilities прошлых пачек) в operation_capabilities этого фрагмента; ") if missing else ""
        raise ModelStageArtifactInvalid(
            f"invalid model stage artifact: {stage_instance_id} does not pass {schema_name}: "
            + "; ".join(f"{row['path'] or '/'} {row['code']}" for row in diagnostics[:3])
            + (f" and {len(diagnostics) - 3} more" if len(diagnostics) > 3 else "")
            + "; fix: " + cause + "give the role its original input and this retry_prompt, then submit --transport-attempts 2 — "
            "nothing is written; retry_prompt:\n"
            + retry_prompt(dict(value), diagnostics, "python -m tools.validate_artifact schemas/" + schema_name + " <файл ответа>")  # the name is one of the closed list above
        )


def _model_stage_artifact_target(root: Path, attempt_id: str, content_digest: str) -> Path:
    if (
        not re.fullmatch(r"[0-9a-f]{32}", attempt_id)
        or not _DIGEST.fullmatch(content_digest)
    ):
        raise ValueError("invalid model stage artifact identity")
    return root / "model-stage-artifacts" / attempt_id / (
        content_digest.removeprefix("sha256:") + ".json"
    )


def _read_model_stage_artifact_with_state(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    """Inside one closure a stage answer is read and checked once — its bytes are content-addressed and its request and
    predecessors are earlier events of an append-only journal, so a second read of the same digest checks the same things."""
    kept = _CLOSURE_CACHE.get()
    key = ("stage-artifact", str(root), attempt_id, stage_instance_id, content_digest)
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    result = _read_model_stage_artifact_checked(project, root, state, attempt_id, stage_instance_id, content_digest)
    if kept is not None:
        kept[key] = copy.deepcopy(result)
    return result


def _read_model_stage_artifact_checked(
    project: Path,
    root: Path,
    state: Mapping[str, Any],
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    target = _model_stage_artifact_target(root, attempt_id, content_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        value = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("model stage artifact is unreadable") from error
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    events = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
    ]
    requests = [event for event in events if event.get("event_type") == "MODEL_REQUESTED"]
    responses = [
        event for event in events
        if event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
        and event.get("artifact_digest") == content_digest
    ]
    candidates = [
        event for event in events
        if event.get("event_type") == "CANDIDATE_PUBLISHED"
        and event.get("artifact_digest") == content_digest
    ]
    event_binding_valid = (
        len(requests) == 1
        and len(responses) == 1
        and requests[0]["seq"] < responses[0]["seq"]
        and (
            len(candidates) == 1 and responses[0]["seq"] < candidates[0]["seq"]
            if stage_instance_id.startswith(("tc-generator:", "tc-to-autotest:"))
            else not candidates
        )
    )
    if (
        attempt is None
        or not isinstance(value, Mapping)
        or raw != _model_stage_artifact_bytes(value)
        or "sha256:" + hashlib.sha256(raw).hexdigest() != content_digest
        or not event_binding_valid
    ):
        raise ValueError("model stage artifact is unbound")
    request = _read_model_request_with_state(
        project, root, state, attempt_id, stage_instance_id,
        str(requests[0].get("artifact_digest", "")),
    )
    _validate_model_stage_artifact(stage_instance_id, value)
    if stage_instance_id.startswith("context-marker:"):
        _validate_context_marker_sources(project, root, state, attempt, request, value)
    _validate_generator_artifact_binding(root, attempt_id, request, value)
    return {
        "artifact": dict(value),
        "byte_count": len(raw),
        "content_digest": content_digest,
        "path": str(target.relative_to(root)).replace("\\", "/"),
    }


def read_context_selection(run_root: Path, attempt_id: str, receipt_digest: str, *, live: bool = True) -> Mapping[str, Any]:
    """Read a C-lite receipt only when immutable bytes and attempt event both bind it. Once per closure — the receipt is
    content-addressed and its event is in the journal for good.  ``live=False`` (the receipt of a reuse source, compared as it was recorded) does not
    read the files of the project again: the digest of the body, the frozen inventory and the event still bind it."""
    kept = _CLOSURE_CACHE.get()
    key = ("context-selection", str(run_root), attempt_id, receipt_digest, live)
    if kept is not None and key in kept:
        return copy.deepcopy(kept[key])
    receipt = _read_context_selection_checked(run_root, attempt_id, receipt_digest, live)
    if kept is not None:
        kept[key] = copy.deepcopy(receipt)
    return receipt


def _read_context_selection_checked(run_root: Path, attempt_id: str, receipt_digest: str, live: bool = True) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    target = _context_selection_target(root, attempt_id, receipt_digest)
    try:
        raw = read_confined_bytes(project, root, target)
        if raw is None:
            raise FileNotFoundError(str(target.name))
        receipt = loads_json_strict(raw.decode("utf-8"))
        if not isinstance(receipt, dict):
            raise ContextSelectionError("context receipt is not an object")
        inventory = _frozen_inventory(project, root, str(receipt.get("inventory_digest", "")))
        if live:
            validate_context_receipt_binding(inventory, project, receipt)
        else:
            body = json.dumps({key: value for key, value in receipt.items() if key != "digest"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")  # as the scan sealed it
            if receipt.get("digest") != "sha256:" + hashlib.sha256(body).hexdigest() or receipt.get("inventory_digest") != inventory.get("digest"):
                raise ContextSelectionError("context receipt digest or inventory binding is invalid")
    except (OSError, UnicodeDecodeError, StrictJsonError, ContextSelectionError, OutputConfinementError) as error:
        raise ValueError("invalid context selection readback") from error
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    batch_id = f"review-{receipt['batch_index']:04d}"
    event = next((item for item in state["events"] if item["event_type"] == "CONTEXT_SELECTED" and item.get("attempt_id") == attempt_id and item.get("batch_id") == batch_id and item.get("artifact_digest") == receipt_digest), None)
    if attempt is None or event is None or receipt.get("digest") != receipt_digest:
        raise ValueError("unbound context selection receipt")
    return receipt


class ModelStageArtifactInvalid(ValueError):
    """A model answer that does not pass its schema; the message carries the retry_prompt."""

    code = "MODEL_STAGE_ARTIFACT_INVALID"


# public names of the same objects: what the modules above and ``pilot_state``'s callers take from here (П1 of the cut
# of v0.8)
context_marker_parts = _context_marker_parts
context_marker_receipt_digests = _context_marker_receipt_digests
context_marker_snapshots = _context_marker_snapshots
context_selection_target = _context_selection_target
model_request_target = _model_request_target
model_stage_artifact_bytes = _model_stage_artifact_bytes
model_stage_artifact_digest = _model_stage_artifact_digest
model_stage_artifact_target = _model_stage_artifact_target
named_docs = _named_docs
published_context_receipts = _published_context_receipts
read_model_request_with_state = _read_model_request_with_state
read_model_stage_artifact_with_state = _read_model_stage_artifact_with_state
validate_context_marker_sources = _validate_context_marker_sources
validate_generator_artifact_binding = _validate_generator_artifact_binding
validate_model_request_predecessors = _validate_model_request_predecessors
validate_model_stage_artifact = _validate_model_stage_artifact
