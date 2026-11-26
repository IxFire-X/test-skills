"""A step of the model (v0.8, the cut of the core): the request (``publish_model_request``), the answer (``publish_model_stage_artifact``),
the selection of the context, the code step of the OpenSpec marker, a run left before the review.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v1b.tsv``);
what they read of the journal and of ``model_inputs`` they take by public names, and ``pilot_state`` still serves their names to its
callers (the facade of the cut).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools.confined_output import OutputConfinementError, create_confined_bytes_exclusive, read_confined_bytes
from tools.fixes import RETRY_SPENT, REVIEW_CHAIN, delete_field_fix, human_for, spent_abandon
from tools.project_inventory import ContextSelectionError, freeze_context_receipt, validate_context_receipt_binding
from tools.schema_validation import StrictJsonError, loads_json_strict
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    DIGEST as _DIGEST,
    LEFT_MARK,
    MODEL_STAGE_INSTANCE as _MODEL_STAGE_INSTANCE,
    MODEL_STAGE_REGISTRY as _MODEL_STAGE_REGISTRY,
    REWORK_REVIEW_STAGE as _REWORK_REVIEW_STAGE,
    REWORK_STAGE as _REWORK_STAGE,
    append_event,
    append_journal_event as _append_event,
    canonical_bytes as _canonical_bytes,
    closure_cached,
    derive_state,
    frozen_baseline,
    frozen_inventory as _frozen_inventory,
    parse_json_bytes as _parse_json_bytes,
    read_run,
    run_root_of as _run_root,
    safe_model_label as _safe_model_label,
    sealed as _sealed,
    self_review_accepted_at as _self_review_accepted_at,
    validate_schema as _validate_schema,
)
from tools.model_records import (  # the names of the records of a step these bodies read (П1 of the cut of v0.8)
    context_marker_parts as _context_marker_parts,
    context_marker_receipt_digests as _context_marker_receipt_digests,
    context_marker_snapshots as _context_marker_snapshots,
    context_selection_target as _context_selection_target,
    model_request_target as _model_request_target,
    model_stage_artifact_bytes as _model_stage_artifact_bytes,
    model_stage_artifact_target as _model_stage_artifact_target,
    named_docs as _named_docs,
    published_context_receipts as _published_context_receipts,
    read_context_selection,
    read_model_request_with_state as _read_model_request_with_state,
    validate_context_marker_sources as _validate_context_marker_sources,
    validate_generator_artifact_binding as _validate_generator_artifact_binding,
    validate_model_request_predecessors as _validate_model_request_predecessors,
    validate_model_stage_artifact as _validate_model_stage_artifact,
)
from tools.model_inputs import (  # the names of the inputs of a step these bodies read
    ModelRequestDuplicate,
    ModelRequestInvalid,
    ModelResponseDigestMismatch,
    ModelReuseMismatch,
    ModelReuseRefused,
    OPENSPEC_MODEL_ID,
    REUSED_AUTOMATION as _REUSED_AUTOMATION,
    STAGE_EXAMPLES as _STAGE_EXAMPLES,
    accepted_earlier_fragments as _accepted_earlier_fragments,
    attempt_of as _attempt_of,
    automation_inputs as _automation_inputs,
    codeword_lost_note as _codeword_lost_note,
    expected_model_inputs as _expected_model_inputs,
    fill_response_bindings as _fill_response_bindings,
    generator_input as _generator_input,
    imported_for_answer as _imported_for_answer,
    inputs_mismatch as _inputs_mismatch,
    is_source_answer as _is_source_answer,
    label_found as _label_found,
    named_sections as _named_sections,
    named_texts as _named_texts,
    new_run_fix as _new_run_fix,
    published_generator_inputs as _published_generator_inputs,
    read_model_stage_artifact,
    rebound as _rebound,
    require_next_automation_stage as _require_next_automation_stage,
    requirement_order_rows as _requirement_order_rows,
    response_digest_rows as _response_digest_rows,
    reuse_family,
    reuse_source as _reuse_source,
    skill_pack_digest,
)


def _outside_sections(project: Path, root: Path, attempt: Mapping[str, Any], receipts: Sequence[str], rows: Sequence[Any]) -> list[str]:
    """The ids of the requirements whose provenance ``path:line`` is a line outside the sections ``--docs`` names of that file (the rest of the file is context)."""
    from tools.doc_sections import find_section

    named = {path: headings for path, headings in _named_sections(project, root).items() if headings}
    texts = _named_texts(project, root, attempt, receipts) if named else {}
    ranges = {path: [(item.first, item.last) for item in (find_section(texts[path], heading, path) for heading in headings)] for path, headings in named.items() if path in texts}
    outside = []
    for row in rows:
        for reference in (row.get("provenance") or []) if isinstance(row, Mapping) else []:
            found = re.match(r"(.+?):(\d+)", str(reference))  # provenance is a free string: the path and the first line stand, the tail (a range, a note) is ignored
            if found and found.group(1) in ranges and not any(first <= int(found.group(2)) <= last for first, last in ranges[found.group(1)]):
                outside.append(str(row.get("source_requirement_id")))
                break
    return outside


def _check_requirement_labels(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], receipts: Sequence[str], value: Mapping[str, Any]) -> str | None:
    """tails13: the requirements of a plain document are its numbered statements — an answer of fewer than half as many rows as the labels is refused, of fewer rows than labels
    gives the warning (returned).  Not for a project with ``requirements.id_pattern``, not for OpenSpec."""
    from tools.build_context import label_refusal, label_warning

    rows = (((value.get("artifacts") or {}).get("analytics_documentation") or {}).get("requirements"))
    if not isinstance(rows, list):
        return None
    outside = _outside_sections(project, root, attempt, receipts, rows)
    if outside:
        raise ValueError(f"CONTEXT_MARKER_REQUIREMENT_OUTSIDE_SECTION: требования {', '.join(outside[:5])} взяты со строк вне разделов, которые назвал --docs; fix: требования прогона — только "
                         "из названных разделов (остальной документ — контекст); отдай исправленный ответ единственным повтором стадии (--transport-attempts 2), ничего не записано")
    found = _label_found(project, root, attempt, receipts)
    why = label_refusal(len(rows), found)
    if why:
        raise ValueError(f"CONTEXT_MARKER_REQUIREMENT_LABELS: {why}; отдай исправленный ответ единственным повтором стадии (--transport-attempts 2), ничего не записано")
    return label_warning(len(rows), found)


def requirement_label_counts(run_root: Path, attempt_id: str, requirements: int) -> dict[str, Any] | None:
    """For the report: ``{"documents": [{"path", "labels"}], "requirements": n}`` where the marker has fewer requirements than its documents have labels (the warning of the submit), else None."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    found = _label_found(project, root, _attempt_of(state, attempt_id), _published_context_receipts(state, attempt_id, "context-marker:baseline"))
    if sum(len(labels) for labels in found.values()) <= requirements:
        return None
    return {"documents": [{"path": path, "labels": len(labels)} for path, labels in sorted(found.items())], "requirements": requirements}


def openspec_marker_by_code(run_root: Path) -> bool:
    """The requirements of the marker are the composition of OpenSpec files alone: ``--docs`` names files under ``openspec/`` (one of them a spec or a delta),
    and no ``--cases`` (an update of cases needs the links the model gives) — code answers ``context-marker:requirements``."""
    from tools.build_context import OPENSPEC_PATH
    from tools.cases_import import cases_path

    project, root = _run_root(run_root)
    named = _named_docs(project, root) if cases_path(root) is None else []
    return bool(named) and all(path.startswith("openspec/") for path in named) and any(OPENSPEC_PATH.fullmatch(path) for path in named)


def openspec_marker_requirements(run_root: Path, attempt_id: str) -> Mapping[str, Any]:
    """Answer ``context-marker:requirements`` of such a run by code: the rows ``build_context`` composes from the receipts, no model call; again on an answered step changes nothing."""
    from tools.build_context import build_context
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    project, root = _run_root(run_root)
    state, stage = derive_state(root), "context-marker:requirements"
    answered = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == stage]
    if answered:
        return read_model_stage_artifact(root, attempt_id, stage, str(answered[-1]["artifact_digest"]))
    attempt = _attempt_of(state, attempt_id)
    baseline = frozen_baseline(root, attempt)
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"]]
    built = build_context(project, docs_snapshot=_context_marker_snapshots(project, root, state, attempt, stage, head, _published_context_receipts(state, attempt_id, stage)))
    artifact = {"schema_version": built["schema_version"], "stage": "context-marker", "warnings": built["warnings"],
                "artifacts": {"analytics_documentation": built["artifacts"]["analytics_documentation"], "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}}}
    check_model_stage_artifact(stage, artifact)  # before the request is written: a refusal leaves no orphan request behind
    publish_code_step_request(run_root, attempt_id, stage, model_id=OPENSPEC_MODEL_ID, invocation_id=f"openspec-{attempt_id[:12]}", input_digests=head)
    return publish_model_stage_artifact(run_root, attempt_id, stage, artifact)


def _reconcile_requirements_openspec(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], request: Mapping[str, Any], value: Mapping[str, Any],
) -> None:
    """The merged marker keeps the requirements of this answer and adds the part warnings, so its OpenSpec
    reconciliation is decided here, against every receipt of the attempt — before the write, while the stage keeps its retry.
    Write time only: a journal written before reads as before; the merge keeps its own check."""
    from tools.build_context import openspec_diagnostics

    from tools.cases_import import is_code_requirements

    stage_instance_id = str(request["stage_instance_id"])
    if is_code_requirements(root, attempt, value):  # the requirements of an import are its cases, by the CSV bytes
        return
    receipts = _published_context_receipts(state, str(attempt["attempt_id"]), stage_instance_id)
    snapshots = _context_marker_snapshots(project, root, state, attempt, stage_instance_id, request["input_digests"], receipts)
    diagnostics = openspec_diagnostics(dict(value), snapshots) if snapshots else []
    if diagnostics:
        raise ValueError(f"CONTEXT_MARKER_OPENSPEC: context-marker OpenSpec reconciliation failed: {stage_instance_id}: "  # a code
                         + json.dumps(diagnostics, ensure_ascii=False)
                         + "; fix: требования ответа — по OpenSpec из квитанций контекста; отдай исправленный ответ единственным "
                         "повтором стадии (--transport-attempts 2), ничего не записано")


LEFT_BATCH = "left-v1"  # the events of the mark carry it


def left_attempt(run_root: Path) -> str | None:
    """The attempt the run was left at before the canonical review — only while it is still the run's last
    attempt (a later attempt is never hidden by an old mark); the mark is read confined. The mark is a decision of the
    run, so it counts only with its ARTIFACT_READ_BACK in the journal (``bind_bytes``, batch ``left-v1``) — a file an agent put there
    leaves the run as it was."""
    project, root = _run_root(run_root)
    try:
        data = read_confined_bytes(project, root, root / LEFT_MARK)
        value = json.loads(data) if data is not None else None
    except (OutputConfinementError, ValueError):
        return None
    state = derive_state(root)
    attempts = state["attempts"]
    if (
        not isinstance(value, Mapping) or value.get("reason") != "LEFT_BEFORE_REVIEW" or not attempts
        or value.get("attempt_id") != attempts[-1]["attempt_id"]
    ):
        return None
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if not any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == value["attempt_id"] and event.get("batch_id") == LEFT_BATCH
               and event.get("artifact_digest") == digest for event in state["events"]):
        return None
    return str(value["attempt_id"])


def _refuse_left(root: Path, attempt_id: str) -> None:
    """No model step on a left attempt — the plain scan of the request starts a new run."""
    if left_attempt(root) == attempt_id:
        raise ValueError("RUN_LEFT: прогон оставлен до ревью кейсов (finish --abandon LEFT_BEFORE_REVIEW); "
                         "fix: простой scan того же запроса начнёт новый")


@closure_cached
def publish_model_request(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    *,
    model_id: str | None,
    invocation_id: str,
    input_digests: Sequence[str],
    reuse_from: str | None = None,
) -> Mapping[str, Any]:
    """Persist and read back the complete model invocation envelope before its event.

    A context-marker request records the skill_pack_digest of the package; ``reuse_from`` names an earlier run of the
    project whose answer for the same stage and the very same inputs is reused — checked here, recorded as ``reuse``.
    """
    project, root = _run_root(run_root)
    _refuse_left(root, attempt_id)
    state = derive_state(root)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    stage = stage_instance_id.split(":", 1)[0]
    if attempt is not None and attempt["policy_profile"] == "cases-only-v1" and stage in {"tc-to-autotest", "autotest-reviewer"}:
        raise ValueError(f"MODEL_STAGE_NOT_IN_PROFILE: {stage_instance_id} is a stage of the automation, and this run is cases-only-v1; fix: прогон только кейсов — "
                         "автотесты строит новый прогон local-pilot-v1 (scan без --profile cases-only-v1) в поставке с режимом Б")
    declaration = _MODEL_STAGE_REGISTRY.get(stage)
    inputs = list(input_digests) if not isinstance(input_digests, (str, bytes)) else []
    journal_inputs = (not inputs and attempt is not None and attempt["state"] != "TERMINAL" and declaration is not None
                      and _MODEL_STAGE_INSTANCE.fullmatch(stage_instance_id) is not None)
    if journal_inputs:
        # without --input-digest the inputs the journal fixes; its refusals about an unpublished
        # predecessor stay as they are
        known = _expected_model_inputs(project, root, state, attempt, stage_instance_id)
        if known is None and stage in {"tc-to-autotest", "autotest-reviewer"}:
            known = _automation_inputs(project, root, state, attempt_id, stage_instance_id)
        inputs = [digest for digest, _source in known or ()]
    label_fix = ("метка из букв, цифр и . _ : / - (до 256 знаков), например model-v4 или context-1; "
                 "без ключей, токенов и паролей")
    broken = next(((reason, fix) for failed, reason, fix in (
        (attempt is None, f"no attempt {attempt_id} in this run", "--attempt — attempt_id этого прогона из run_pipeline status"),
        (attempt is not None and attempt["state"] == "TERMINAL", "the attempt is closed (TERMINAL); a new request needs a new run",
         lambda: _new_run_fix(root)),  # only on this refusal: it reads the run manifest
        (_MODEL_STAGE_INSTANCE.fullmatch(stage_instance_id) is None, f"stage {stage_instance_id!r} is not <stage>:<instance>",
         "--stage <stage>:<instance>, например " + _STAGE_EXAMPLES),
        (declaration is None, f"unknown stage {stage!r}; known: {', '.join(sorted(_MODEL_STAGE_REGISTRY))}",
         "--stage одной из стадий " + ", ".join(sorted(_MODEL_STAGE_REGISTRY)) + ", например " + _STAGE_EXAMPLES),
        (model_id is not None and not _safe_model_label(model_id), "--model-id is not a safe label (letters, digits, . _ -, no secret-like value)",
         "--model-id — " + label_fix),
        (not _safe_model_label(invocation_id), "--invocation-id is not a safe label (letters, digits, . _ -, no secret-like value)",
         "--invocation-id — " + label_fix),
        (not inputs, f"MODEL_INPUTS_REQUIRED: the journal does not fix the inputs of {stage_instance_id}, no --input-digest given",
         "--input-digest на каждый вход стадии, в её порядке (для context-marker — вход строит `prepare`; при неверном входе "
         "отказ MODEL_INPUTS_MISMATCH называет верный digest и его источник)"),
        (len(inputs) != len(set(inputs)), "an --input-digest is repeated", "каждый вход один раз, в порядке стадии"),
        (any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in inputs), "an --input-digest is not sha256:<64 hex>",
         "digest целиком, как его отдала команда публикации входа (sha256:<64 hex>, поле digest или content_digest её result)"),
    ) if failed), None)
    if broken is not None:  # the condition, not only «invalid model request»; and the way out
        fix = broken[1]() if callable(broken[1]) else broken[1]
        raise ModelRequestInvalid(f"invalid model request: {broken[0]}; fix: {fix}")
    if any(event["event_type"] == "MODEL_REQUESTED" and event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id
           for event in state["events"]):
        raise ModelRequestDuplicate(
            f"duplicate model request: {stage_instance_id} already has its request;{_codeword_lost_note(root, state, attempt_id, stage_instance_id)} "
            "fix: a repeated prepare gives the same request — the answer goes to submit, its one retry is submit --transport-attempts 2"
        )
    if reuse_from is not None and reuse_family(stage_instance_id) in (None, "plan"):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {stage_instance_id} не переиспользуется; fix: prepare без --reuse-from")
    if stage == "context-marker":
        _context_marker_snapshots(project, root, state, attempt, stage_instance_id, inputs)
    # the inputs the journal fixes, each wrong position named with its right digest and source; publish time only,
    # so a journal written before the generator inputs were published still replays
    expected = _expected_model_inputs(project, root, state, attempt, stage_instance_id)
    if expected is not None and inputs != [digest for digest, _source in expected]:
        raise _inputs_mismatch(stage_instance_id, inputs, expected)
    _validate_model_request_predecessors(project, root, state, attempt, stage_instance_id, invocation_id, inputs)
    if stage in {"tc-to-autotest", "autotest-reviewer"}:
        _require_next_automation_stage(run_root, attempt_id, stage_instance_id)  # publish time only
        if expected is None:  # the inputs mismatch above is named when the expected inputs are known; a predecessor it could not read is refused by that read
            pairs = _automation_inputs(project, root, state, attempt_id, stage_instance_id)
            if inputs != [digest for digest, _source in pairs]:
                raise _inputs_mismatch(stage_instance_id, inputs, pairs)
    run_id = read_run(root)["manifest"]["run_id"]
    role, role_policy = declaration
    extra: dict[str, Any] = {}
    if stage == "context-marker" or reuse_from is not None:  # the package the answer is made with, so a reuse can require the same package
        pack = skill_pack_digest()
        if pack is not None:
            extra["skill_pack_digest"] = pack
        if reuse_from is not None:
            extra["reuse"] = _reuse_source(project, root, state, attempt, stage_instance_id, inputs, role_policy, reuse_from)
    nonce = None
    if stage_instance_id.startswith("tc-generator:BATCH-"):  # the control word of the generator's task — the run keeps its sha256 only
        from tools.review_canaries import nonce_digest

        nonce = secrets.token_hex(6)
        extra["nonce_sha256"] = nonce_digest(nonce)
    receipt = _sealed({
        "schema_version": "1.1.0" if extra else "1.0.0",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stage_instance_id": stage_instance_id,
        "stage": stage,
        "role": role,
        "role_policy": role_policy,
        "model_id": model_id,
        "invocation_id": invocation_id,
        "input_digests": inputs,
        **extra,
    })
    _validate_schema("model-request.schema.json", receipt)
    data = _canonical_bytes(receipt)
    target = _model_request_target(root, attempt_id, receipt["digest"])
    try:
        create_confined_bytes_exclusive(project, root, target, data)
        raw = read_confined_bytes(project, root, target)
        readback = _parse_json_bytes(raw, "model request") if raw is not None else None
    except (OutputConfinementError, OSError, ValueError) as error:
        raise ValueError("model request readback failed") from error
    if raw != data or readback != receipt:
        raise ValueError("model request readback mismatch")
    batch_id = stage_instance_id.split(":", 1)[1] if stage == "tc-generator" else None
    _append_event(
        project, root, "MODEL_REQUESTED", actor="controller", attempt_id=attempt_id,
        batch_id=batch_id, stage_instance_id=stage_instance_id,
        artifact_digest=receipt["digest"],
    )
    published = dict(read_model_request(root, attempt_id, stage_instance_id, receipt["digest"]))
    if nonce is not None:  # only in this output, never in a file: the agent puts it at the head of the generator's task
        published["nonce"] = nonce
        published["nonce_note"] = (f"вставь строку «Контрольное слово: {nonce}» в начало задачи генератору; если кейсы пишешь ты сам, вставлять некуда — слово просто не используй; "
                                   "в ответ генератора и в файлы прогона слово не копируй — прогон хранит только его sha256; вывод этой команды слово показывает один раз")
    if not str(model_id or "").startswith("controller:"):  # the merge by the code is no model step
        warning, measure_error = _record_step_size(project, root, run_id, stage_instance_id)
        if warning is not None:  # a warning, never a refusal
            published["step_warning"] = warning
        if measure_error is not None:  # nor is a measurement that failed silent
            published["step_measure_error"] = measure_error
    return published


def publish_code_step_request(run_root: Path, attempt_id: str, stage_instance_id: str, *, model_id: str, invocation_id: str,
                              input_digests: Sequence[str]) -> Mapping[str, Any]:
    """The request of a step the code answers itself (the merge of the marker parts, the import of cases).  A command cut
    between this request and the answer is run again and goes on from the request it wrote — the same model, invocation and inputs,
    no answer yet; any other request of the stage (or an answered stage) is what ``publish_model_request`` makes of it,
    MODEL_REQUEST_DUPLICATE."""
    project, root = _run_root(run_root)
    events = [event for event in derive_state(root)["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id]
    requests = [event for event in events if event["event_type"] == "MODEL_REQUESTED"]
    if len(requests) == 1 and not any(event["event_type"] == "MODEL_RESPONSE_RECEIVED" for event in events):
        request = read_model_request(root, attempt_id, stage_instance_id, str(requests[0]["artifact_digest"]))
        if (request.get("model_id") == model_id and request.get("invocation_id") == invocation_id
                and list(request.get("input_digests") or []) == list(input_digests)):
            return request
    return publish_model_request(run_root, attempt_id, stage_instance_id, model_id=model_id, invocation_id=invocation_id,
                                 input_digests=input_digests)


def _record_step_size(project: Path, root: Path, run_id: str, stage_instance_id: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """The measurement of the step the request has just fixed, appended to ``step-sizes.jsonl`` (the report's section) —
    the same measure ``tools.step_size --run --stage`` makes, by the code at the one place every request passes, for every
    stage whose inputs the journal resolves.  Returns ``(warning, error)``: STEP_OVER_BUDGET is a warning and a measurement
    that cannot be taken or written is named (class of the error), neither stops the request."""
    from tools import step_size

    stage = stage_instance_id.split(":", 1)[0]
    try:
        found = step_size.request_files(root, stage_instance_id) if stage in step_size._REQUEST_STAGES else step_size.stage_inputs(root, stage_instance_id)
        state = derive_state(root)
        limits, _note = step_size.run_limits(project, root, state, str(state["attempts"][-1]["attempt_id"]))  # the limits the run froze
        report = step_size.measure(project, [Path(row["path"]) for row in found["files"]], stage, budget=step_size.step_budget(project, limits),
                                   extra_bytes=sum(row["bytes"] for row in found["bindings"]))
        step_size.record(project, run_id, stage_instance_id, report)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return None, {"code": "STEP_MEASURE_FAILED", "error": type(error).__name__}
    if report["fits"]:
        return None, None
    return {"code": step_size.OVER_BUDGET, "bytes": report["bytes"], "budget_bytes": report["budget_bytes"], "hint": report["hint"]}, None


def read_model_request(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    request_digest: str,
) -> Mapping[str, Any]:
    """Read one exact request envelope and verify its journal binding."""
    project, root = _run_root(run_root)
    return _read_model_request_with_state(
        project, root, derive_state(root), attempt_id, stage_instance_id, request_digest,
    )


def check_merged_marker(run_root: Path, attempt_id: str, input_digests: Sequence[str], value: Mapping[str, Any]) -> None:
    """Every check the write of the merged ``context-marker:baseline`` makes that needs no write — the schema and
    order, the inputs, the merge of the parts and OpenSpec — run by ``merge`` before it writes the request."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = _attempt_of(state, attempt_id)
    check_model_stage_artifact("context-marker:baseline", value)
    _validate_context_marker_sources(project, root, state, attempt, {"stage_instance_id": "context-marker:baseline",
                                                                     "input_digests": list(input_digests)}, value)


def check_model_stage_artifact(stage_instance_id: str, value: Mapping[str, Any]) -> None:
    """The checks of an answer that need no journal — the requirement order and the schema of the stage — for code
    that builds an answer itself (the import of cases), before it writes the request."""
    misplaced = _requirement_order_rows(stage_instance_id, value)
    if misplaced:
        raise ValueError(f"SOURCE_REQUIREMENT_ORDER: {stage_instance_id}: {misplaced}")
    _validate_model_stage_artifact(stage_instance_id, value)


_RETRY_EXHAUSTED_FIX = ("fix: python -m tools.run_pipeline finish --project <project> --run <run_id> назовёт выход — "
                        "для стадии автоматизации finish --abandon STAGE_RETRY_EXHAUSTED, до ревью кейсов — scan --new-run")


def _validate_review_decision(root: Path, state: Mapping[str, Any], attempt_id: str, value: Mapping[str, Any]) -> None:
    """The reviewer's decision against the requested candidate before the answer is written — select would refuse
    it after the ledger is closed, with no way out; here the stage keeps its one retry.  A run whose candidate the journal
    cannot assemble (in a run of an earlier version) is left to select as before."""
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import OrchestrationError, _run_imported, journal_assembly, review_decision

    requested = next((str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id
                      and event["event_type"] == "REVIEW_REQUESTED" and event.get("stage_instance_id") == "tc-reviewer:canonical"), None)
    try:
        candidate = journal_assembly(root, attempt_id)[0]
    except (KeyError, OSError, TypeError, ValueError):
        return
    if requested is None or document_sha256(candidate) != requested:
        return
    from tools import review_canaries

    canaries = review_canaries.verify(root, attempt_id)  # a map that does not match is a refusal, before anything is written
    if review_canaries.codeword_state(root, attempt_id, value) == "named" and not _self_review_accepted_at(_run_root(root)[0], root):
        raise ValueError("REVIEW_CODEWORD_NAMED: ревьюер назвал контрольное слово генератора — он видел задачу генератора, это ревью в той же "
                         "сессии; fix: проведи ревью в новой сессии (/clear или новое окно: «проведи ревью test-skills <run_id>») и подай её "
                         "ответ как есть; ничего не записано. Ревью в той же сессии допустимо, только если человек согласен: prepare с --fresh-context no --accept-self-review")
    try:
        from tools.orchestrate_test_case_revision import reject_share

        review_decision(candidate, value, imported=_run_imported(root, attempt_id), canaries=canaries, share=reject_share(root, attempt_id))
    except OrchestrationError as error:
        rows = "; ".join(f"{row.get('path') or '/'} {row.get('code')}" for row in error.diagnostics[:8])
        raise ValueError(f"REVIEW_DECISION_INVALID: {rows}; fix: это ошибка ответа ревьюера — исправь решение и отдай его "
                         "единственным повтором стадии (--transport-attempts 2), ничего не записано") from None


def _spent_fix(run_root: Path, stage_instance_id: str) -> str:
    """The fix of a stage whose one retry is spent: one command that leaves the run (it prints the next one), or the general words where no command is promised."""
    from tools.run_pipeline import printed

    cause = spent_abandon(stage_instance_id)
    if cause is None:
        return RETRY_SPENT + _RETRY_EXHAUSTED_FIX.removeprefix("fix: ")
    root = Path(run_root).resolve()
    return (RETRY_SPENT + f"закрой прогон одной командой: {printed(sys.executable)} -m tools.run_pipeline finish --project {printed(root.parents[1])} "
            f"--run {printed(root.name)} --abandon {cause} — она напечатает next_run.command; выполни его")


@closure_cached
def publish_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    artifact: Mapping[str, Any],
    *,
    transport_attempts: int = 1,
    raw_bytes: int | None = None,
    dry_run: bool = False,
    before_write: Callable[[Mapping[str, Any]], None] | None = None,
) -> Mapping[str, Any]:
    """Persist a requested model output, then bind its response/candidate events.

    ``before_write(answer)`` runs after every check, with the answer as it would be stored, and before the write: a refusal of
    it leaves the stage as it was (the last batch of the generator is assembled there).

    Audit 2: a refused answer given as the one retry (--transport-attempts 2) never advises that retry again — its
    fix is the way out of the stage.  The code does not count refused answers yet (AB2, after the release).

    ``dry_run`` runs every check that precedes the write and returns ``{"dry_run": True, "artifact": <the answer as it would
    be stored>, "content_digest", "filled", "normalized"}`` — nothing is written, no event is appended."""
    try:
        return _publish_model_stage_artifact(run_root, attempt_id, stage_instance_id, artifact, transport_attempts=transport_attempts,
                                             raw_bytes=raw_bytes, dry_run=dry_run, before_write=before_write)
    except Exception as error:
        if transport_attempts == 2 and "--transport-attempts 2" in str(error):
            spent = _spent_fix(run_root, stage_instance_id)
            error.args = tuple(_retry_spent(arg, spent) if isinstance(arg, str) and "--transport-attempts 2" in arg else arg for arg in error.args)
            if isinstance(getattr(error, "fix", None), str):
                error.fix = spent
        raise


def _retry_spent(text: str, spent: str) -> str:
    return text.split("; fix: ", 1)[0] + "; fix: " + spent


def _publish_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    artifact: Mapping[str, Any],
    *,
    transport_attempts: int = 1,
    raw_bytes: int | None = None,
    dry_run: bool = False,
    before_write: Callable[[Mapping[str, Any]], None] | None = None,
) -> Mapping[str, Any]:
    """Persist a requested model output, then bind its response/candidate events.

    ``raw_bytes`` — the size of the answer as the model gave it (the ``--artifact`` file); the stored artifact is
    compact JSON, so without it the measurement falls back to the stored bytes.
    """
    project, root = _run_root(run_root)
    _refuse_left(root, attempt_id)
    state = derive_state(root)
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id), None,
    )
    value = dict(artifact)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("unknown model stage artifact attempt")
    # one answer per stage, at most the one retry — --transport-attempts above 2 is refused before anything is
    # written (and so is replacing a recorded answer); refused answers are not counted yet, the agent keeps the limit
    if type(transport_attempts) is int and transport_attempts > 2:
        raise ValueError(f"MODEL_STAGE_RETRY_EXHAUSTED: {stage_instance_id}: у стадии один ответ и единственный повтор "
                         f"(--transport-attempts 1 или 2): --transport-attempts больше 2 отказан; ничего не записано; " + _RETRY_EXHAUSTED_FIX)
    requests = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("event_type") == "MODEL_REQUESTED"
    ]
    filled: list[str] = []
    normalized: list[str] = []
    mechanics: list[str] = []
    if stage_instance_id.startswith("tc-generator:BATCH-"):  # the order of requirement_ids and categories is the code's work, as in the check of a fragment
        from tools.batch_assembly import fill_fragment_mechanics, normalize_fragment

        value, normalized = normalize_fragment(value)
        value, mechanics = fill_fragment_mechanics(value)  # ids, display_order, defaults, the types the code can take
    if stage_instance_id == _REWORK_STAGE:  # a case of the rework is written like a case of a batch: the ids and orders of its steps are the code's
        from tools.batch_assembly import fill_rework_mechanics

        value, mechanics = fill_rework_mechanics(value)
    if stage_instance_id in ("tc-reviewer:canonical", _REWORK_REVIEW_STAGE):  # the control word the reviewer names is kept as its sha256 only
        from tools.review_canaries import hash_codeword

        value = hash_codeword(value)
    if len(requests) == 1:  # the digest fields the journal fixes and the answer leaves out — put by the code
        prior = read_model_request(root, attempt_id, stage_instance_id, str(requests[0].get("artifact_digest", "")))
        reused = prior.get("reuse")
        if isinstance(reused, Mapping) and stage_instance_id == _REUSED_AUTOMATION:  # but for the one field that holds the path of the run, which is put again like any binding
            value, reused = _rebound(value, None), None
        if not isinstance(reused, Mapping):  # a reused answer is passed as it is
            if (stage_instance_id.startswith("tc-generator:BATCH-") and isinstance(value, dict) and value.get("status") == "COMPLETE"
                    and value.get("context_receipt") in (None, "")):
                # the context receipt of the batch — by the digest the published plan binds to this batch (never the digest the answer
                # names: a different one is the refusal of the digest binding)
                try:
                    batch = next(row for row in _generator_input(project, root, state, attempt_id, "plan")["batches"]
                                 if row.get("batch_id") == stage_instance_id.partition(":")[2])
                    receipt = read_context_selection(root, attempt_id, str(batch["context_receipt_digest"]))
                    value, mechanics = {**value, "context_receipt": dict(receipt)}, [*mechanics, "/context_receipt"]
                except (KeyError, StopIteration, OSError, TypeError, ValueError):
                    pass  # no plan, no such batch or no receipt: the checks name it
            value, filled = _fill_response_bindings(project, root, state, attempt_id, stage_instance_id, prior, value)
    data = _model_stage_artifact_bytes(value)
    content_digest = "sha256:" + hashlib.sha256(data).hexdigest()
    responses = [
        event for event in state["events"]
        if event.get("attempt_id") == attempt_id
        and event.get("stage_instance_id") == stage_instance_id
        and event.get("event_type") == "MODEL_RESPONSE_RECEIVED"
    ]
    if (
        len(requests) != 1
        or len(responses) > 1
        or responses and (
            responses[0].get("artifact_digest") != content_digest
            or responses[0].get("transport_attempts") != transport_attempts
        )
    ):
        if len(requests) == 1 and len(responses) == 1:
            raise ValueError(f"MODEL_STAGE_ANSWERED: {stage_instance_id}: ответ стадии уже записан, другой ответ его не заменит; "
                             "ничего не записано; " + _RETRY_EXHAUSTED_FIX)
        if not requests:
            if stage_instance_id == "tc-reviewer:canonical":  # the request of the reviewer is the work of prepare
                raise ValueError(f"MODEL_STAGE_NOT_REQUESTED: {stage_instance_id}: model stage artifact requires its exact prior request — "
                                 "the stage has none; fix: запрос ревьюера пишет `prepare --stage tc-reviewer:canonical` (повтори его с тем же --invocation-id, если он уже был), "
                                 "руками запрос не нужен — " + REVIEW_CHAIN)
            raise ValueError(f"MODEL_STAGE_NOT_REQUESTED: {stage_instance_id}: model stage artifact requires its exact prior request — "
                             "the stage has none; fix: prepare for this stage first, then submit")
        raise ValueError("model stage artifact requires its exact prior request")
    request = read_model_request(
        root, attempt_id, stage_instance_id, str(requests[0].get("artifact_digest", "")),
    )
    reuse = request.get("reuse")
    if isinstance(reuse, Mapping) and not _is_source_answer(root, reuse, stage_instance_id, value, content_digest):  # a reused answer is not changed
        from tools.run_scan import printed_or

        source = _model_stage_artifact_target(_run_root(Path(root).parent / str(reuse["run_id"]))[1], str(reuse["attempt_id"]), str(reuse["response_digest"]))
        raise ModelReuseMismatch(
            f"MODEL_REUSE_MISMATCH: {stage_instance_id}: ответ {content_digest}, а запрос переиспользует ответ {reuse['response_digest']} "
            f"прогона {reuse['run_id']}; fix: ответ этой стадии берёт код из источника — повтори prepare --stage <стадия> --reuse-from <run_id>, "
            "а не ответ из слота; ничего не записано")
    # digest fields of the answer against the journal before anything is written; the stage keeps its one retry
    mismatches = _response_digest_rows(project, root, state, attempt_id, stage_instance_id, request, value)
    if mismatches:
        raise ModelResponseDigestMismatch(
            f"MODEL_RESPONSE_DIGEST_MISMATCH: {stage_instance_id}: "
            + "; ".join(f"{pointer}: дан {given}, ожидается {expected} — {source}" for pointer, given, expected, source in mismatches)
            + "; fix: " + delete_field_fix(", ".join(pointer for pointer, *_rest in mismatches))
            + "; это ошибка ответа — отдай его единственным повтором стадии (--transport-attempts 2), ничего не записано"
        )
    # source requirements follow their documents; only a new answer is checked, journals already written read as before
    misplaced = _requirement_order_rows(stage_instance_id, value)
    if misplaced:
        raise ValueError(
            f"SOURCE_REQUIREMENT_ORDER: {stage_instance_id}: {misplaced}; fix: display_order и номера SREQ — требования одного "
            "документа подряд, внутри документа по номеру строки как числу (…md:4 раньше …md:12); отдай исправленный ответ единственным повтором стадии (--transport-attempts 2), "
            "ничего не записано")
    _validate_model_stage_artifact(stage_instance_id, value)
    if stage_instance_id == _REWORK_STAGE:
        from tools.orchestrate_test_case_revision import check_rework_answer

        check_rework_answer(root, attempt_id, value)  # the cases are exactly the rejected ones, r3 is valid: a refusal leaves the answer unwritten and the one retry
    if stage_instance_id.startswith("tc-generator:BATCH-"):
        from tools.batch_assembly import BatchAssemblyError, require_batch_coverage, validate_fragment

        try:
            published = _published_generator_inputs(project, root, state, attempt_id)
            if published is None:
                # reachable only for a request published by an earlier version: a new request without the published plan and
                # header is refused by _expected_model_inputs (audit 1, 5g); such a run keeps the coverage rule
                require_batch_coverage(value)
            else:  # the whole fragment check (coverage inside) against the journal, before the write
                validate_fragment(value, dict(published[0]), dict(published[1]), dict(read_context_selection(root, attempt_id, str(request["input_digests"][1]))),
                                  _accepted_earlier_fragments(project, root, state, attempt_id, published[0], stage_instance_id.split(":", 1)[1]),
                                  imported=_imported_for_answer(project, root, state, attempt_id, stage_instance_id.split(":", 1)[1], request))
        except BatchAssemblyError as error:
            retry = "отдай исправленный фрагмент единственным повтором стадии (--transport-attempts 2), ничего не записано"
            rows = getattr(error, "diagnostics", ())  # the whole list, each row with its fix
            raise BatchAssemblyError(error.code, error.detail + "; fix: исправь фрагмент и " + retry, rows,
                                     (getattr(error, "fix", None) + "; " if getattr(error, "fix", None) else "исправь фрагмент и ") + retry) from error
    if stage_instance_id.startswith("context-marker:"):
        _validate_context_marker_sources(project, root, state, attempt, request, value)
    labels_note = None
    from tools.cases_import import is_code_requirements

    if stage_instance_id == "context-marker:requirements" or (stage_instance_id == "context-marker:baseline" and not _context_marker_parts(state, attempt_id)[1]):  # one context batch: the marker is one answer; at its write only, never at the read of a written one
        if not is_code_requirements(root, attempt, value):  # the requirements of an import are its cases, no numbers of a document
            labels_note = _check_requirement_labels(project, root, state, attempt, _published_context_receipts(state, attempt_id, stage_instance_id) if stage_instance_id.endswith("requirements")
                                                    else _context_marker_receipt_digests(project, root, state, attempt_id, stage_instance_id, request["input_digests"]), value)
    if stage_instance_id == "context-marker:requirements":
        _reconcile_requirements_openspec(project, root, state, attempt, request, value)
        from tools.cases_import import check_requirements_answer

        from tools.cases_scope import scope

        check_requirements_answer(root, attempt, value, lambda: scope(  # the links of an import with documents (by the run, not a label), judged by the delta
            _context_marker_snapshots(project, root, state, attempt, stage_instance_id, request["input_digests"],
                                      _published_context_receipts(state, attempt_id, stage_instance_id)), _named_docs(project, root)))
    if stage_instance_id == "tc-reviewer:canonical":
        _validate_review_decision(root, state, attempt_id, value)
    if stage_instance_id == _REWORK_REVIEW_STAGE:
        from tools.orchestrate_test_case_revision import check_rework_review

        check_rework_review(root, attempt_id, value)  # the review of the reworked cases only: its form and the set it gives, before the answer is written
    _validate_generator_artifact_binding(root, attempt_id, request, value)
    if before_write is not None:
        before_write(value)
    if dry_run:  # every check above ran; nothing below is written
        return {"dry_run": True, "artifact": value, "content_digest": content_digest, "filled": [*mechanics, *filled], "normalized": normalized}
    target = _model_stage_artifact_target(root, attempt_id, content_digest)
    try:
        create_confined_bytes_exclusive(project, root, target, data)
        raw = read_confined_bytes(project, root, target)
        readback = loads_json_strict(raw.decode("utf-8")) if raw is not None else None
    except (OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("model stage artifact readback failed") from error
    if raw != data or readback != value:
        raise ValueError("model stage artifact readback mismatch")
    batch_id = value.get("batch_id") if stage_instance_id.startswith("tc-generator:") else None
    append_event(
        root, "MODEL_RESPONSE_RECEIVED", actor="controller", attempt_id=attempt_id,
        batch_id=batch_id, stage_instance_id=stage_instance_id,
        artifact_digest=content_digest, transport_attempts=transport_attempts,
    )
    if stage_instance_id.startswith(("tc-generator:", "tc-to-autotest:")):
        append_event(
            root, "CANDIDATE_PUBLISHED", actor="controller", attempt_id=attempt_id,
            batch_id=str(batch_id) if batch_id is not None else None,
            stage_instance_id=stage_instance_id,
            artifact_digest=content_digest,
        )
    published = dict(read_model_stage_artifact(root, attempt_id, stage_instance_id, content_digest))
    # the answer size of every call, and a warning (never a stop) when it is large
    from tools.step_size import output_warning, record_output, run_limits

    limits, _limits_note = run_limits(project, root, state, attempt_id)  # the output limit of the .skillsrc the run froze
    measured = int(raw_bytes) if raw_bytes is not None else published["byte_count"]
    if raw_bytes is not None:
        record_output(project, root, stage_instance_id, content_digest, measured)
    published.update(output_bytes=measured, output_basis="raw" if raw_bytes is not None else "compact",
                     warning=output_warning(project, measured, limits))
    if human := human_for(published["warning"]):  # the warning in the words of the tester
        published["human"] = human
    if mechanics or filled:  # what the code put into the answer — the mechanics by name and count, then the digest fields
        published["filled"] = [*mechanics, *filled]
    if normalized:  # what the code put in order
        published["normalized"] = normalized
    if labels_note:  # fewer requirements than numbers in the documents: the agent shows it to the person
        published["requirement_labels"] = labels_note
    return published


@closure_cached
def publish_context_selection(run_root: Path, attempt_id: str, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist/read back one inventory-owned exact C-lite byte-set receipt."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    value = dict(receipt)
    if attempt is None or attempt["state"] == "TERMINAL":
        raise ValueError("unknown context selection attempt")
    inventory = _frozen_inventory(project, root, str(value.get("inventory_digest", "")))
    try:
        validate_context_receipt_binding(inventory, project, value)
        target = _context_selection_target(root, attempt_id, str(value.get("digest", "")))
        raw = read_confined_bytes(project, root, target)
        if raw is not None:
            readback = loads_json_strict(raw.decode("utf-8"))
            if readback != value:
                raise ValueError("context selection readback mismatch")
        else:
            readback = freeze_context_receipt(project, root, target, value)
    except (ContextSelectionError, OutputConfinementError, OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ValueError("invalid context selection receipt") from error
    batch_id = f"review-{readback['batch_index']:04d}"
    append_event(root, "CONTEXT_SELECTED", actor="controller", attempt_id=attempt_id, batch_id=batch_id, artifact_digest=readback["digest"])
    return read_context_selection(root, attempt_id, readback["digest"])
