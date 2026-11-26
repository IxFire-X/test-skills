"""The inputs of a step of the model (v0.8, the cut of the core): the inputs a stage expects and the bindings of its answer, the input of
the generator and the plan of the batches, the receipts of the cases and the checks of their kinds (``RECEIPT_CHECKS``, ``RECEIPT_FAMILIES``:
``pilot_state._validate_factual_receipt`` takes them from here), the reuse of a former run (``--reuse-from``), the labels of the requirements.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v1b.tsv``);
what they read of the journal they take by its public names, and ``pilot_state`` still serves their names to its callers (the facade of
the cut).
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from tools import execution_gate
from tools.confined_output import OutputConfinementError, read_confined_bytes
from tools.fixes import CLOSE_RUN_FIX
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    DIGEST as _DIGEST,
    PRIOR_KIND as _PRIOR_KIND,
    REWORK_BATCH,
    REWORK_REVIEW_STAGE as _REWORK_REVIEW_STAGE,
    REWORK_STAGE as _REWORK_STAGE,
    SKELETON_KIND as _SKELETON_KIND,
    canonical_bytes as _canonical_bytes,
    canonical_digest as _digest,
    closure_cache,
    closure_cached,
    context_selection_target as _context_selection_target,
    derive_state,
    frozen_baseline,
    frozen_inventory as _frozen_inventory,
    model_stage_artifact_bytes as _model_stage_artifact_bytes,
    model_stage_artifact_digest as _model_stage_artifact_digest,
    named_docs as _named_docs,
    publish_bound,
    read_attempt_receipt,
    read_attempt_receipt_with_state as _read_attempt_receipt_with_state,
    read_context_selection,
    read_effective_canonical_if_present,
    read_effective_canonical_with_state as _read_effective_canonical_with_state,
    read_model_request_with_state as _read_model_request_with_state,
    read_model_stage_artifact_with_state as _read_model_stage_artifact_with_state,
    read_reviewer_session_ledger,
    read_run,
    receipt_target as _receipt_target,
    run_root_of as _run_root,
)


def _named_sections(project: Path, root: Path) -> dict[str, list[str]]:
    """path → the headings ``--docs`` names of the file (none: the whole file), the paths as ``_named_docs`` gives them."""
    from tools.doc_sections import resolve_spec
    from tools.run_pipeline import relative_to_project

    named: dict[str, list[str]] = {}
    for doc in (read_run(root)["manifest"].get("scan_request") or {}).get("docs") or []:
        path, heading = resolve_spec(project, doc)
        headings = named.setdefault(relative_to_project(project.resolve(), path), [])
        if heading and heading not in headings:
            headings.append(heading)
    return named


def _label_texts(project: Path, root: Path, attempt: Mapping[str, Any], receipts: Sequence[str]) -> dict[str, str]:
    """The texts the labels are counted in: the plain documents ``--docs`` names (read where the receipts hold them, by their digests) — the named sections of a file, not the whole file."""
    from tools.doc_sections import find_section, view

    texts = _named_texts(project, root, attempt, receipts)
    for path, headings in _named_sections(project, root).items():
        if headings and path in texts:
            texts[path] = view(texts[path], [find_section(texts[path], heading, path) for heading in headings], path, note=False)
    return texts


def _label_found(project: Path, root: Path, attempt: Mapping[str, Any], receipts: Sequence[str]) -> dict[str, list[str]]:
    """The labels of the plain documents ``--docs`` names (``tools.build_context.document_label_counts``) — of the named sections of a file, not of the whole file."""
    from tools.build_context import document_label_counts

    return document_label_counts(_label_texts(project, root, attempt, receipts), project)


def _named_texts(project: Path, root: Path, attempt: Mapping[str, Any], receipts: Sequence[str]) -> dict[str, str]:
    """The plain documents ``--docs`` names (whole files), read where the receipts hold them, by the digest."""
    from tools.output_guard import OutputRefused, project_file

    named, texts = set(_named_docs(project, root)), {}
    for digest in receipts:
        for item in read_context_selection(root, str(attempt["attempt_id"]), digest)["files"]:
            path = item["project_path"].replace("\\", "/")
            if path in named:
                try:
                    raw = project_file(project, path).read_bytes()
                except (OSError, OutputRefused):  # a document that is gone or is no plain file is no check here: its drift stops the plan stage (_context_documents)
                    continue
                if "sha256:" + hashlib.sha256(raw).hexdigest() == item["content_digest"]:
                    texts[path] = raw.decode("utf-8", errors="replace")
    return texts


OPENSPEC_MODEL_ID = "controller:openspec-marker"


def _codeword_lost_note(root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str) -> str:
    """A generator request carries the sha256 of its control word and shows the word once, in the output of the command that
    wrote it.  A repeat cannot show it again: when the request already holds a word, the refusal says so (CODEWORD_LOST) — the generator
    that did not get the word works without it, and the report calls such a stage's word lost."""
    if not stage_instance_id.startswith("tc-generator:"):
        return ""
    digest = next((event.get("artifact_digest") for event in state["events"] if event["event_type"] == "MODEL_REQUESTED"
                   and event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_instance_id), None)
    try:
        held = bool(digest and _read_model_request_with_state(_run_root(root)[0], root, state, attempt_id, stage_instance_id, str(digest)).get("nonce_sha256"))
    except (OSError, ValueError):
        held = False
    return (" CODEWORD_LOST: the control word of that request was shown once, in the output of the command that wrote it, and the run keeps "
            "only its sha256 — if that output was lost the generator works without the word (the report says so);") if held else ""


def _attempt_of(state: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any]:
    """The attempt of the journal, or a refusal: an id the journal does not know is no ``StopIteration``."""
    attempt = next((item for item in state["attempts"] if item["attempt_id"] == attempt_id), None)
    if attempt is None:
        raise ValueError("unknown attempt")
    return attempt


def read_model_stage_artifact(
    run_root: Path,
    attempt_id: str,
    stage_instance_id: str,
    content_digest: str,
) -> Mapping[str, Any]:
    """Read exact content-addressed model output bytes and revalidate their stage identity."""
    project, root = _run_root(run_root)
    return _read_model_stage_artifact_with_state(
        project, root, derive_state(root), attempt_id, stage_instance_id, content_digest,
    )


def context_progress(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The context batches the scan has written for the attempt against the count its receipts carry (``batch_count``,
    bound by the journal through the receipt digest): ``{"have", "total"}``.  ``total`` is None before the first batch and for a
    run of an earlier version (no count) — the set is then taken as whole.  Only reads the receipt files and the journal."""
    from tools.project_inventory import _digest
    from tools.schema_validation import StrictJsonError, loads_json_strict

    project, root = _run_root(run_root)
    state = state if state is not None else derive_state(root)
    digests = list(dict.fromkeys(str(event["artifact_digest"]) for event in state["events"]
                                 if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"))
    total = None
    for digest in digests:
        try:
            receipt = loads_json_strict((read_confined_bytes(project, root, _context_selection_target(root, attempt_id, digest)) or b"").decode("utf-8"))
        except (OSError, UnicodeDecodeError, StrictJsonError, ValueError):
            continue  # a receipt file that cannot be read is the business of the read that needs it (read_context_selection)
        if isinstance(receipt, dict) and receipt.get("digest") == digest and _digest({k: v for k, v in receipt.items() if k != "digest"}) == digest:
            count = receipt.get("batch_count")
            if type(count) is int and (total is None or count > total):
                total = count
    return {"have": len(digests), "total": total}


def context_incomplete(run_root: Path, attempt_id: str, state: Mapping[str, Any] | None = None) -> tuple[int, int] | None:
    """``(have, total)`` when the scan of the attempt stopped before its last context batch, else None."""
    progress = context_progress(run_root, attempt_id, state)
    return (progress["have"], progress["total"]) if progress["total"] is not None and progress["have"] < progress["total"] else None


def scan_incomplete_message(have: int, total: int) -> str:
    return (f"SCAN_INCOMPLETE: scan записал {have} из {total} пачек контекста — разметка и план по неполному контексту не строятся; "
            "fix: повтори тот же scan (те же --module, --docs, --profile) — он достроит недостающие пачки в этом прогоне")


def _check_reference_cases(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the parsed reference CSV of scan --reference, a hint source only
    reference = receipt.get("reference")
    if (
        set(receipt) != common | {"reference"}
        or not identity_matches
        or not isinstance(reference, Mapping)
        or set(reference) != {"path", "sha256", "cases"}
        or not isinstance(reference.get("path"), str)
        or not _DIGEST.fullmatch(str(reference.get("sha256", "")))
        or not isinstance(reference.get("cases"), list)
    ):
        raise ValueError("invalid reference-cases receipt")


def _check_rework_round(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the first review sent cases back and the code waits for their rework: bound to the candidate and the answer of the reviewer
    digests = ("candidate_digest", "report_digest")
    if (
        set(receipt) != common | {*digests, "rounds"}
        or not identity_matches
        or receipt.get("rounds") != 1
        or any(not _DIGEST.fullmatch(str(receipt.get(name, ""))) for name in digests)
    ):
        raise ValueError("invalid rework-round receipt")


def _check_generator_input(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the plan or the header with its one self-digest
    field = next(name for name, value in _GENERATOR_INPUT_KINDS.items() if value == kind)
    value = receipt.get(field)
    if (
        set(receipt) != common | {field}
        or not identity_matches
        or not isinstance(value, Mapping)
        or value.get("digest") != _generator_input_digest(value)
        or field == "plan" and not _plan_batches_listed(value)
    ):
        raise ValueError(f"invalid {kind} receipt")


def _check_imported_cases(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the skeleton is the one the CSV bound by the inventory gives (and the links)
    from tools.cases_import import run_skeleton

    found = run_skeleton(root, attempt)
    if set(receipt) != common | {"imported"} or not identity_matches or found is None or receipt.get("imported") != found:
        raise ValueError("invalid imported-cases receipt")


def _check_batch_skeleton(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the slice the whole skeleton and the published plan give, nothing else
    if (
        set(receipt) != common | {"imported"}
        or not identity_matches
        or receipt.get("imported") != _batch_skeleton_value(project, root, state, str(attempt["attempt_id"]), kind[len(_SKELETON_KIND):])
    ):
        raise ValueError(f"invalid {kind} receipt")


def _check_prior_capabilities(project: Path, root: Path, attempt: Mapping[str, Any], kind: str, receipt: Mapping[str, Any], state: Mapping[str, Any], common: set[str], identity_matches: bool) -> None:  # the value is the one the journal gives, nothing else
    if (
        set(receipt) != common | {"prior_capabilities"}
        or not identity_matches
        or receipt.get("prior_capabilities") != _prior_capabilities_value(project, root, state, str(attempt["attempt_id"]), kind[len(_PRIOR_KIND):])
    ):
        raise ValueError(f"invalid {kind} receipt")


_GENERATOR_INPUT_KINDS = {"plan": "batch-plan", "header": "generator-header"}


def publish_reference_cases(run_root: Path, attempt_id: str, path: str, data: bytes, cases: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The reference cases of ``scan --reference`` (parsed by ``zephyr_csv``) as a receipt of the attempt; they
    never enter the context, the generator input or the canon."""
    facts = {"reference": {"path": path, "sha256": "sha256:" + hashlib.sha256(bytes(data)).hexdigest(), "cases": [dict(case) for case in cases]}}
    return publish_bound(run_root, attempt_id, "reference-cases", facts)


def read_reference_cases(run_root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    """The reference of the attempt, None when the scan had no ``--reference``."""
    try:
        return read_attempt_receipt(run_root, attempt_id, "reference-cases", "ARTIFACT_READ_BACK")["record"]["reference"]
    except (ValueError, OSError, KeyError):
        return None


def publish_rework_round(run_root: Path, attempt_id: str, candidate_digest: str, report_digest: str) -> Mapping[str, Any]:
    """The first review sent cases back and ``limits.rework_rounds`` is 1: the one fact that the set waits for the rework (and so is not published yet).
    Written once, after the ledger is complete; the live ``.skillsrc`` is not read again."""
    return publish_bound(run_root, attempt_id, "rework-round", {"rounds": 1, "candidate_digest": candidate_digest, "report_digest": report_digest}, batch_id=REWORK_BATCH)


def rework_round_bound(state: Mapping[str, Any], attempt_id: str) -> bool:
    """Whether the journal binds a receipt ``rework-round`` of the attempt: a file of the run that it does not bind is no receipt and no damage."""
    return any(event["event_type"] == "ARTIFACT_READ_BACK" and event.get("attempt_id") == attempt_id and event.get("batch_id") == REWORK_BATCH for event in state["events"])


def rework_round_if_present(run_root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    """The receipt ``rework-round`` read back, or None where the review did not send cases back for a rework."""
    project, root = _run_root(run_root)
    if not rework_round_bound(derive_state(root), attempt_id):
        return None  # a file the journal does not bind is no receipt (the repeat of a cut submit writes it again through publish_bound): not read, nothing written
    target = _receipt_target(root, attempt_id, "rework-round")
    try:
        raw = read_confined_bytes(project, root, target)
    except OutputConfinementError as error:
        raise ValueError("unsafe rework-round path") from error
    return None if raw is None else read_attempt_receipt(root, attempt_id, "rework-round", "ARTIFACT_READ_BACK")["record"]


_RECEIPT_FAMILIES = ((_SKELETON_KIND, _check_batch_skeleton), (_PRIOR_KIND, _check_prior_capabilities))  # imported-cases-<batch>, prior-capabilities-<batch>


def _batch_ordinal(batch: Mapping[str, Any]) -> int:
    """The ordinal of a published plan batch; a fixture plan without one is a first batch (four inputs)."""
    return batch["ordinal"] if type(batch.get("ordinal")) is int else 1


def _accepted_earlier_fragments(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, plan: Mapping[str, Any],
                                batch_id: str) -> list[dict[str, Any]]:
    """The accepted fragments (submit) of the batches before *batch_id*; one missing is refused by name."""
    rows = [row for row in plan.get("batches") or [] if isinstance(row, Mapping)]
    current = next((row for row in rows if row.get("batch_id") == batch_id), None)
    if current is None:
        return []
    fragments: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in sorted((row for row in rows if _batch_ordinal(row) < _batch_ordinal(current)), key=_batch_ordinal):
        stage = f"tc-generator:{row['batch_id']}"
        answers = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == stage]
        if not answers:
            missing.append(f"{row.get('namespace', '')} {row['batch_id']}".strip())
            continue
        fragments.append(dict(_read_model_stage_artifact_with_state(project, root, state, attempt_id, stage, str(answers[-1]["artifact_digest"]))["artifact"]))
    if missing:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: пачки идут по очереди — сначала принятый ответ (submit) пачки "
                                  f"{', '.join(missing)}, потом {current.get('namespace', '')} {batch_id}")
    return fragments


def _prior_capabilities_value(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str) -> Mapping[str, Any]:
    """The capabilities of the accepted batches before *batch_id*, built by code from the published plan and the journal."""
    from tools.batch_assembly import BatchAssemblyError, prior_capabilities

    plan = _generator_input(project, root, state, attempt_id, "plan")
    batch = next((row for row in plan["batches"] if row.get("batch_id") == batch_id), None)
    if batch is None:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {batch_id} is not a batch of the published plan {plan['digest']}")
    if _batch_ordinal(batch) == 1:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {batch_id} — первая пачка, у неё четыре входа, capabilities прошлых пачек нет")
    fragments = _accepted_earlier_fragments(project, root, state, attempt_id, plan, batch_id)
    try:
        return prior_capabilities(dict(plan), batch_id, fragments)
    except BatchAssemblyError as error:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {error}") from error


def _imported_cases(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any] | None:
    """The published skeleton of an import run; None for a run without --cases; refused when not yet published."""
    from tools.cases_import import cases_path

    if cases_path(root) is None:
        return None
    try:
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, "imported-cases", "ARTIFACT_READ_BACK")["record"]["imported"]
    except (ValueError, OSError, KeyError) as error:
        raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: the cases of the import are not published; fix: prepare --stage "
                                  "context-marker:requirements") from error


def _batch_skeleton_value(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str) -> Mapping[str, Any]:
    """The skeleton of *batch_id* as the whole skeleton and the published plan give it."""
    from tools.cases_import import slice_value

    whole = _imported_cases(project, root, state, attempt_id)
    if whole is None:
        raise ValueError("IMPORT_NOT_CASES_RUN: the run has no --cases")
    return slice_value(whole, _generator_input(project, root, state, attempt_id, "plan"), batch_id)


def _publish_batch_skeletons(run_root: Path, attempt_id: str) -> dict[str, str]:
    """Publish the skeleton of every batch of the published plan — bookkeeping inside the plan publication the
    agent called; the same plan again changes nothing.  ``{batch_id: digest}``; empty for a run without --cases."""
    from tools.cases_import import cases_path

    project, root = _run_root(run_root)
    if cases_path(root) is None:
        return {}
    state = derive_state(root)
    plan = _generator_input(project, root, state, attempt_id, "plan")
    published: dict[str, str] = {}
    for row in plan["batches"]:
        kind = _SKELETON_KIND + row["batch_id"]
        value = _batch_skeleton_value(project, root, state, attempt_id, row["batch_id"])
        prior = None
        if _receipt_target(root, attempt_id, kind).exists():
            try:  # a file without its events is finished below, not a dead end
                prior = _read_attempt_receipt_with_state(project, root, derive_state(root), attempt_id, kind, "ARTIFACT_READ_BACK")["record"]["imported"]
            except (ValueError, OSError, KeyError):
                prior = None
            if prior is not None and prior != value:
                raise ValueError(f"GENERATOR_INPUT_CONFLICT: the batch {row['batch_id']} already has another skeleton")
        if prior is None:
            publish_bound(run_root, attempt_id, kind, {"imported": dict(value)})
        published[row["batch_id"]] = str(value["digest"])
    return published


def _imported_for_answer(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, batch_id: str,
                         request: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """What submit checks the human fields against — the skeleton the request took as its last input:
    the batch's own one, or the whole one for a request published by an earlier version.  None for a run without --cases."""
    whole = _imported_cases(project, root, state, attempt_id)
    if whole is None or (request.get("input_digests") or [None])[-1] == whole["digest"]:
        return whole
    return _read_attempt_receipt_with_state(project, root, state, attempt_id, _SKELETON_KIND + batch_id, "ARTIFACT_READ_BACK")["record"]["imported"]


@closure_cached
def publish_imported_cases(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """Publish the skeleton of an import run from its CSV; the same value again changes nothing."""
    from tools.cases_import import run_skeleton

    project, root = _run_root(run_root)
    attempt = _attempt_of(derive_state(root), attempt_id)
    value = run_skeleton(root, attempt)
    if value is None:
        raise ValueError("IMPORT_NOT_CASES_RUN: the run has no --cases")
    publish_bound(run_root, attempt_id, "imported-cases", {"imported": value})
    target = _receipt_target(root, attempt_id, "imported-cases")
    return {"digest": value["digest"], "cases": len(value["cases"]), "path": target.relative_to(root).as_posix()}


@closure_cached
def publish_prior_capabilities(run_root: Path, attempt_id: str, batch_id: str) -> dict[str, Any]:
    """Publish the fifth tc-generator input of batch k>=2 — built from the journal, one receipt per batch; the same value
    again changes nothing, another one is refused."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    value = _prior_capabilities_value(project, root, state, attempt_id, batch_id)
    kind = _PRIOR_KIND + batch_id
    target = _receipt_target(root, attempt_id, kind)
    if target.exists():
        try:
            prior = _read_attempt_receipt_with_state(project, root, state, attempt_id, kind, "ARTIFACT_READ_BACK")["record"]["prior_capabilities"]
        except (ValueError, OSError, KeyError):
            prior = None  # the file is there, its events are not — below it is finished (same bytes) or refused (other bytes)
        if prior is not None and prior != value:
            raise ValueError(f"GENERATOR_INPUT_CONFLICT: the batch {batch_id} already has other prior capabilities; another one needs "
                             "a new run (scan --new-run)")
    publish_bound(run_root, attempt_id, kind, {"prior_capabilities": dict(value)})
    ids = [str(row.get("capability_id")) for row in value["operation_capabilities"]]
    # PT2: the composition in a line — the ids (their names), the body in the file: record.prior_capabilities.operation_capabilities
    return {"batch_id": batch_id, "digest": value["digest"], "capabilities": len(ids), "capability_ids": ids[:60],
            **({"capability_ids_more": len(ids) - 60} if len(ids) > 60 else {}),
            "path": target.relative_to(root).as_posix(), "body_at": "record.prior_capabilities.operation_capabilities"}


_STAGE_EXAMPLES = "context-marker:baseline, tc-generator:BATCH-<id>, tc-reviewer:canonical, tc-to-autotest:r1, autotest-reviewer:r1"


def _new_run_fix(root: Path) -> str:
    """The scan of this run's request with --new-run, as other refusals of a closed attempt give it."""
    try:
        from tools.run_pipeline import _scan_command

        request = read_run(root)["manifest"].get("scan_request")
        if isinstance(request, Mapping):
            return "новый прогон того же запроса: " + _scan_command(root.parents[1], request, extra=("--new-run",))
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        pass
    return "новый прогон: scan с теми же --module, --docs и --profile, что у этого прогона, и --new-run"


class ModelRequestInvalid(ValueError):
    """A model request that breaks one of its basic conditions, named in the message."""

    code = "MODEL_REQUEST_INVALID"


class ModelRequestDuplicate(ValueError):
    """A second request of one stage of the attempt."""

    code = "MODEL_REQUEST_DUPLICATE"


class ModelInputsMismatch(ValueError):
    """A model request whose input digests are not the ones the journal fixes."""

    code = "MODEL_INPUTS_MISMATCH"


class ModelRequestOutOfOrder(ValueError):
    """A request of the automation chain that is not its next stage (an orphan r2 before r1)."""

    code = "MODEL_REQUEST_OUT_OF_ORDER"


def _require_next_automation_stage(run_root: Path, attempt_id: str, stage_instance_id: str) -> None:
    """The chain's next stage is what finish reads (automation_next); a final chain is left to the existing checks."""
    from tools.finalize_attempt import automation_next

    try:
        outcome, expected = automation_next(run_root, attempt_id)
    except (KeyError, OSError, TypeError, ValueError):
        return
    if outcome == "PENDING" and expected != stage_instance_id:
        raise ModelRequestOutOfOrder(f"MODEL_REQUEST_OUT_OF_ORDER: {stage_instance_id} — следующая стадия цепочки автоматизации {expected}; "
                                     f"fix: prepare --stage {expected}, ничего не записано")


class ModelReuseRefused(ValueError):
    """The named run is no reuse source for this request (the reason says why)."""

    code = "MODEL_REUSE_REFUSED"


class ModelReuseMismatch(ValueError):
    """The answer to a reuse request is not the source answer byte for byte."""

    code = "MODEL_REUSE_MISMATCH"


class ReuseSourceInvalid(ValueError):
    """A file of the reuse source does not read as the journal of that run binds it (the message names the file); nothing is written to the new run."""

    code = "REUSE_SOURCE_INVALID"


# the stages ``--reuse-from`` takes — exact patterns, not a prefix (tc-generator:rework-1 and the rounds of the rework are not among them) — and the family
# whose checks each has (``_checked_source``): a marker needs an accepted source, the plan, the batches and the review the cases accepted whole, the autotests
# the same project and execution and the review of revision 1 that accepted them, the rework and its second round the cases the first review accepted in part
REUSE_STAGES = {"context-marker:*": "marker", "plan": "plan", "tc-generator:BATCH-*": "batch", "tc-reviewer:canonical": "review", "tc-to-autotest:r1": "automation",
                _REWORK_STAGE: "rework", _REWORK_REVIEW_STAGE: "rework_review"}


def reuse_family(stage: str) -> str | None:
    return next((family for pattern, family in REUSE_STAGES.items() if fnmatch.fnmatchcase(stage, pattern)), None)


# a marker is reused only from a run that has a review of its cases, whatever its outcome (a rejected set too: the marker does not depend on it; the cases of such a run are not taken); AUTO_FIX_APPLIED fixes human fields only, not the marker
ACCEPTED_REVIEW = frozenset({"ПРИНЯТО", "AUTO_FIX_APPLIED"})


_REUSE_FREE = "нет прошлого прогона с разметкой"


def skill_pack_digest() -> str | None:
    """The skill_pack_digest of this package's release/manifest.json — the one fingerprint of the package (audit U9)."""
    try:
        value = json.loads((Path(__file__).resolve().parents[1] / "release" / "manifest.json").read_text(encoding="utf-8")).get("skill_pack_digest")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and _DIGEST.fullmatch(value) else None


def _review_verdict(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> str | None:
    events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
              and event.get("stage_instance_id") == "tc-reviewer:canonical"]
    if not events:
        return None
    value = _read_model_stage_artifact_with_state(project, root, state, attempt_id, "tc-reviewer:canonical", str(events[-1]["artifact_digest"]))
    return str(value["artifact"]["artifacts"]["validation_report"]["verdict"])


def _has_drifted(root: Path, attempt_id: str) -> bool:
    return _receipt_target(root, attempt_id, "resume-validation").is_file()


def _strict(run_id: str, name: str, read: Callable[[], Any]) -> Any:
    """``read()`` of a file of the source: one that does not read as the journal binds it is REUSE_SOURCE_INVALID with its name, never the ordinary way."""
    try:
        return read()
    except ModelReuseRefused:
        raise
    except (ValueError, OSError, KeyError, TypeError, StopIteration, OutputConfinementError) as error:
        raise ReuseSourceInvalid(f"REUSE_SOURCE_INVALID: {name} прогона {run_id}: {str(error)[:200]}") from error


def _run_source(project: Path, run_id: str, whole: bool = True) -> dict[str, Any]:
    """The earlier run with a recorded context marker: attempt, verdict, drift and every model stage it answered (request, answer digest), the whole journal
    and every answer read strictly (``whole``; the scan looks at the markers of every earlier run and reads the rest of one).  Reads only: a run with pending
    journal events is refused, so nothing is recovered or written in the source run."""
    root = project / ".pilot-runs" / run_id
    if not re.fullmatch(r"[0-9a-f]{32}", run_id) or not (root / "events.jsonl").is_file():
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: нет прогона {run_id} в {project / '.pilot-runs'}")
    if (root / "pending-events").is_dir() and any((root / "pending-events").glob("*.json")):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: журнал прогона {run_id} не закрыт")
    _project, root = _strict(run_id, "run-manifest.json", lambda: _run_root(root))
    state = _strict(run_id, "events.jsonl", lambda: derive_state(root))
    for attempt in reversed(state["attempts"]):
        attempt_id = str(attempt["attempt_id"])
        stages: dict[str, tuple[Mapping[str, Any], str]] = {}
        for event in state["events"]:
            stage = str(event.get("stage_instance_id") or "")
            if event.get("attempt_id") != attempt_id or not stage or event["event_type"] != "MODEL_RESPONSE_RECEIVED" or not (whole or stage.startswith("context-marker:")):
                continue
            asked = next((item for item in state["events"] if item.get("attempt_id") == attempt_id and item["event_type"] == "MODEL_REQUESTED"
                          and item.get("stage_instance_id") == stage), None)
            stages[stage] = (_strict(run_id, f"model-requests ({stage})", lambda: _read_model_request_with_state(project, root, state, attempt_id, stage, str(asked["artifact_digest"]))),
                             str(event["artifact_digest"]))
        if "context-marker:baseline" in stages:
            for stage, (_request, response) in (stages.items() if whole else ()):  # bytes = digest, every answer of the source
                _strict(run_id, f"model-stage-artifacts ({stage})", lambda: _read_model_stage_artifact_with_state(project, root, state, attempt_id, stage, response))
            return {"run_id": run_id, "root": root, "state": state, "attempt": attempt, "stages": stages, "pack": stages["context-marker:baseline"][0].get("skill_pack_digest"),
                    "verdict": _strict(run_id, "model-stage-artifacts (tc-reviewer:canonical)", lambda: _review_verdict(project, root, state, attempt_id)),
                    "drift": _has_drifted(root, attempt_id)}
    raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {_REUSE_FREE} — в прогоне {run_id} нет записанной разметки")


def _source_refusal(source: Mapping[str, Any], module: str, family: str = "marker") -> str | None:
    if source["attempt"].get("module") != module:
        return f"другой модуль ({source['attempt'].get('module')})"
    if family != "automation" and source["verdict"] not in (*ACCEPTED_REVIEW, "ТРЕБУЕТ ДОРАБОТКИ"):  # the autotests are bound to the cases by the input of the request, not by the verdict
        return f"ревью кейсов не принято ({source['verdict'] or 'ревью нет'})"
    if source["drift"]:
        return "дрейф: после исполнения источника baseline разошёлся"
    return None


def _outcome_refusal(source: Mapping[str, Any], family: str = "marker") -> str | None:
    """The last of the cheap refusals, for a source otherwise good: a ТРЕБУЕТ ДОРАБОТКИ review accepted the cases whole by its outcome (the control errors it caught reject nothing) or by the rework."""
    if family in ("automation", "marker") or source["verdict"] != "ТРЕБУЕТ ДОРАБОТКИ":  # the marker does not depend on the outcome of the review of the cases (tails13: it is taken from a rejected set too)
        return None
    from tools.orchestrate_test_case_revision import acceptance_summary, rework_state
    from tools.review_canaries import recorded_answer

    root, attempt_id = source["root"], str(source["attempt"]["attempt_id"])
    state = _strict(source["run_id"], "rework-round", lambda: rework_state(root, attempt_id))
    outcome = None if state == "reviewed" else (_strict(source["run_id"], "review-outcome", lambda: acceptance_summary(root, attempt_id, recorded_answer(root, attempt_id))) or {}).get("outcome")
    if state != "reviewed" and outcome != "ACCEPTED_WHOLE":  # the rework answered twice decides, the outcome is worked out only without it
        return f"ревью кейсов не принято (ТРЕБУЕТ ДОРАБОТКИ{', исход ' + outcome if outcome else ''})" + ("; доработка не завершена" if state else "")
    return None


_BASELINE_INPUTS = {"inventory": lambda b: b["inventory_digest"], "requirements": lambda b: b["requirements"]["digest"],
                    ".skillsrc": lambda b: (b.get("skillsrc_authority") or {}).get("skillsrc_digest"), "профиль": lambda b: b["policy_profile"], "модуль": lambda b: b["module"]}


def _input_differences(source: Mapping[str, Any], root: Path, attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What the new run froze at its scan that the source froze otherwise: the project (its inventory), the requirements, the .skillsrc, the profile, the module."""
    mine = frozen_baseline(root, attempt)
    theirs = _strict(source["run_id"], "baselines", lambda: frozen_baseline(source["root"], source["attempt"]))
    rows = [{"input": name, "given": get(mine), "source": get(theirs)} for name, get in _BASELINE_INPUTS.items() if get(mine) != get(theirs)]
    same = lambda base, baseline: _comparable(root.parents[1], _frozen_inventory(root.parents[1], base, str(baseline["inventory_digest"])))  # noqa: E731  (the project: the run root is project/.pilot-runs/<id>)
    try:
        by_composition = any(row["input"] == "inventory" for row in rows) and same(root, mine) == same(source["root"], theirs)
    except (ValueError, OSError, KeyError, TypeError):
        by_composition = False
    return [{**row, "input": _SAME_COMPOSITION if by_composition and row["input"] == "inventory" else row["input"]} for row in rows]


_SAME_COMPOSITION = ("inventory (состав проекта тот же: в нём тесты прошлого прогона или каталоги сборки, а план и пачки привязаны к квитанциям контекста с описью целиком — "
                     "они делаются заново; разметка берётся)")


def module_test_roots(project: Path, inventory: Mapping[str, Any]) -> tuple[str, ...]:
    """The test roots the human's ``.skillsrc`` names for the module of the inventory, as project paths with a trailing «/» — none when it does not read."""
    from tools.skillsrc_manifest import SkillsrcError, normalize_skillsrc, parse_skillsrc_bytes

    held = next((row.get("content_digest") for row in inventory["files"] if row["project_path"] == ".skillsrc"), None)  # the one of the project root, as the scan reads it: a nested one may be a file of an agent
    try:
        raw = (project / ".skillsrc").read_bytes()
        if held is None or held != "sha256:" + hashlib.sha256(raw).hexdigest():  # a file changed since the scan names no root
            return ()
        modules = normalize_skillsrc(parse_skillsrc_bytes(raw, project=project))["modules"]  # the bytes whose digest was checked, not a second read
    except (OSError, SkillsrcError, KeyError, TypeError, ValueError):
        return ()
    prefix = "" if inventory["module"] == "." else str(inventory["module"]).rstrip("/") + "/"
    return tuple(prefix + root.rstrip("/") + "/" for item in modules if isinstance(item, Mapping) and item.get("root") == inventory["module"]
                 for root in (item.get("paths") or {}).get("tests") or [] if isinstance(root, str))


def _comparable(project: Path, inventory: Mapping[str, Any]) -> dict[str, Any]:
    """The inventory as «the same project» reads it — the whole body but its digest, without the files the package wrote in earlier runs (``own_files``)
    and the build directories (BUILD_OUTPUT: not walked, the model never sees them).  What else the inventory says (path, bytes, kind, language, origin, the other exclusions) stays.
    An own file is left out only when it is a test (kind) under a test root of the module by the ``.skillsrc`` — the root of the receipt is not trusted here."""
    from tools.existing_tests import own_tests

    own = own_tests(project, inventory)[0]
    return {**{key: value for key, value in inventory.items() if key != "digest"},
            "files": [row for row in inventory["files"] if row["project_path"] not in own],
            "exclusions": [row for row in inventory["exclusions"] if row["reason_code"] != "BUILD_OUTPUT"]}


def _tokens(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], live: bool = True) -> dict[str, str]:
    """``{digest: token}`` of the inventory and of each context receipt of the attempt: what it says of the project.  The inventory by ``_comparable``; a receipt by its body
    but its digests (they hold the inventory's) and the own tests it skipped.  Two digests of one token are one project to a reuse; a digest not here is compared as it is."""
    attempt_id = str(attempt["attempt_id"])
    inventory = _frozen_inventory(project, root, str(frozen_baseline(root, attempt)["inventory_digest"]))
    seal = lambda body: "same-project:" + hashlib.sha256(_canonical_bytes(body)).hexdigest()  # noqa: E731
    tokens = {inventory["digest"]: seal(_comparable(project, inventory))}
    for digest in dict.fromkeys(str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"):
        receipt = read_context_selection(root, attempt_id, digest, live=live)
        body = {key: value for key, value in receipt.items() if key not in ("digest", "inventory_digest", "skipped")}
        tokens[digest] = seal({**body, "skipped": [row for row in receipt.get("skipped", []) if row["reason"] != "CONTEXT_OWN_TEST"]})
    return tokens


def _marker_differences(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], source: Mapping[str, Any],
                        mine: Sequence[str], theirs: Sequence[str]) -> list[dict[str, Any]]:
    """The inputs of a marker (this run's ``mine``, the source's ``theirs``: requirements, inventory, the context receipts…) that differ, the projects compared by composition
    (``_tokens``) — the one place the scan's hint and ``prepare --reuse-from`` decide it.  Rows as ``_input_differences``; the receipts of the source are read as recorded."""
    here = _tokens(project, root, state, attempt)
    there = _strict(source["run_id"], "context-selections", lambda: _tokens(project, source["root"], source["state"], source["attempt"], live=False))
    names = ["requirements", "inventory", *(f"квитанция контекста {index}" for index in range(1, max(len(mine), len(theirs))))]
    rows = [{"input": name, "given": given, "source": other} for name, given, other in zip(names, mine, theirs) if here.get(given, given) != there.get(other, other)]
    return rows + ([{"input": "число квитанций контекста", "given": len(mine) - 2, "source": len(theirs) - 2}] if len(mine) != len(theirs) else [])


def _cases_refusal(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, source: dict[str, Any], family: str) -> str | None:
    """The cases of the source accepted whole: not reworked, the review session completed without evidence, the set published, and the word of the generator not named
    by a reviewer that said fresh (the word of a new run is another one: the answer would look independent there).  For the review also: the new run holds the very
    same answers, plan and candidate — the one input of the request that cannot be compared whole is ``package_digest`` (it holds the path of the published set)."""
    run_id, sroot, satt = source["run_id"], source["root"], str(source["attempt"]["attempt_id"])
    from tools.review_canaries import codeword_state

    ledger = _strict(run_id, "reviewer-session-ledgers", lambda: read_reviewer_session_ledger(sroot, satt))
    boundary = _strict(run_id, "reviewer-session-boundaries", lambda: dict(read_attempt_receipt(sroot, satt, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]))
    effective = _strict(run_id, "effective-canonicals", lambda: read_effective_canonical_if_present(sroot, satt))
    if ledger.get("status") != "COMPLETED" or effective is None:
        return "ревью источника не закрыто принятым набором"
    if any(str(row.get("event_type", "")).startswith("EVIDENCE") for row in ledger["events"]):
        return "ревью источника запрашивало файлы (evidence) — такой набор не берётся"
    source["boundary"] = boundary
    if boundary["host_isolation"]["fresh_context"]:
        answer = _strict(run_id, "model-stage-artifacts (tc-reviewer:canonical)", lambda: _read_model_stage_artifact_with_state(
            project, sroot, source["state"], satt, "tc-reviewer:canonical", source["stages"]["tc-reviewer:canonical"][1])["artifact"])
        if codeword_state(sroot, satt, answer) == "named":
            return "ревьюер источника заявил свежий контекст, но назвал слово генератора — такой набор не берётся"
    if family == "review":
        mine = {row["stage_instance_id"]: row["artifact_digest"] for row in state["events"] if row.get("attempt_id") == attempt_id and row["event_type"] == "MODEL_RESPONSE_RECEIVED"}
        for stage, (_request, response) in source["stages"].items():
            if reuse_family(stage) in ("marker", "batch") and mine.get(stage) != response:
                return f"ответ {stage} нового прогона не тот, что у источника"
        for kind in ("plan", "header"):
            theirs = _strict(run_id, "generator-headers / batch-plans", lambda: _generator_input(project, sroot, source["state"], satt, kind))["digest"]
            try:
                same = _generator_input(project, root, state, attempt_id, kind)["digest"] == theirs
            except ModelInputsMismatch:
                same = False
            if not same:
                return f"{kind} нового прогона иной, чем у источника"
        from tools.canonical_document import document_sha256
        from tools.orchestrate_test_case_revision import journal_assembly

        try:
            candidate = document_sha256(journal_assembly(root, attempt_id)[0])
        except (KeyError, OSError, TypeError, ValueError):
            return "пачки нового прогона приняты не все — ревью берётся после них"
        if candidate != boundary["canonical_branch_digest"]:
            return "кандидат нового прогона иной, чем у источника"
    return None


_AUTOMATION_REVIEW = "autotest-reviewer:r1"


def _automation_refusal(project: Path, source: Mapping[str, Any], attempt: Mapping[str, Any]) -> str | None:
    """The autotests of the source for a new run of the same project and execution (the baseline of the attempts says both): the review of revision 1 accepted them;
    a set accepted on revision 2 answers the digests of the source's own revision 1 and is not taken."""
    if source["attempt"].get("baseline_digest") != attempt.get("baseline_digest"):
        return "исполнение или проект иные: baseline прогона не тот"
    if "tc-to-autotest:r1" not in source["stages"]:
        return "автотесты в прогоне не писались"
    reviewed = source["stages"].get(_AUTOMATION_REVIEW)
    verdict = _strict(source["run_id"], f"model-stage-artifacts ({_AUTOMATION_REVIEW})", lambda: _read_model_stage_artifact_with_state(
        project, source["root"], source["state"], str(source["attempt"]["attempt_id"]), _AUTOMATION_REVIEW, reviewed[1])["artifact"]["artifacts"]["autotest_review"]["verdict"]) if reviewed else None
    if verdict == "ПРИНЯТО":
        return None
    return "автотесты принимались на ревизии 2 — берутся только принятые на ревизии 1" if "autotest-reviewer:r2" in source["stages"] else f"ревью автотестов r1 не принято ({verdict or 'ревью нет'})"


def _other_pack(source: Mapping[str, Any], stage: str, pack: str | None) -> str | None:
    """Why the package differs: the fingerprint of the request of ``stage`` itself, the one of the source's marker where the request has none (the requests before V5b-1)."""
    have = (source["stages"].get(stage) or ({},))[0].get("skill_pack_digest") or source["pack"]
    return None if pack is not None and have == pack else f"пакет иной: у источника skill_pack_digest {have or 'не записан (прогон до FaT-CR)'}, здесь {pack or 'не прочитан'}"


def _checked_source(project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage: str, run_id: str) -> tuple[dict[str, Any], str]:
    """The source of ``stage`` with every condition on it checked — reads only, so the caller writes nothing to the new run before they all hold."""
    family = reuse_family(stage)
    if family is None:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {stage} не переиспользуется — --reuse-from берёт разметку (context-marker:*), plan, пачки генератора "
                                "(tc-generator:BATCH-*), tc-reviewer:canonical, доработку (tc-generator:rework-1) и её ревью (tc-reviewer:rework-1) и tc-to-autotest:r1; fix: prepare без --reuse-from")
    if run_id == root.name:
        raise ModelReuseRefused("MODEL_REUSE_REFUSED: тот же прогон — переиспользуется только прошлый прогон")
    source = _run_source(project, run_id, whole=False)  # the journal, the markers and the verdict: what the refusals that need no answer read
    pack = skill_pack_digest()
    refusal = _source_refusal(source, str(attempt.get("module")), family)
    differences: list[dict[str, Any]] = []
    if refusal is None and family == "marker":  # its own inputs only, before its answer is read strictly: a changed file of the context is «входы иные», not a source that does not read
        differences = _marker_differences(project, root, state, attempt, source, _marker_inputs(root, state, attempt), _marker_base(source))
    elif refusal is None and family != "automation":
        differences = _input_differences(source, root, attempt)
    if refusal is None and differences:
        refusal = "входы иные: " + ", ".join(row["input"] for row in differences)
    refusal = refusal or _other_pack(source, stage, pack)  # any change of the package cancels the reuse: coarse, but one fingerprint and no second mechanism
    refusal = refusal or _outcome_refusal(source, family)  # the last of the cheap ones: it builds the candidate of the source
    if refusal is None and family == "marker" and stage in source["stages"]:  # its own answer, strictly: the marker of a source whose later answers are damaged is still taken
        _strict(run_id, f"model-stage-artifacts ({stage})", lambda: _read_model_stage_artifact_with_state(project, source["root"], source["state"], str(source["attempt"]["attempt_id"]), stage, source["stages"][stage][1]))
    elif refusal is None and family != "marker":
        source = _run_source(project, run_id)  # the whole journal and every answer, strictly — after the refusals that need none
        refusal = _other_pack(source, stage, pack)
    if refusal is None and family == "automation":
        refusal = _automation_refusal(project, source, attempt)
    elif refusal is None and family != "marker":
        refusal = _cases_refusal(project, root, state, str(attempt["attempt_id"]), source, family)
    if refusal is not None:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: прогон {run_id}: {refusal}; fix: prepare без --reuse-from")
    if family != "plan" and stage not in source["stages"]:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: в прогоне {run_id} нет ответа {stage}; fix: prepare без --reuse-from")
    return source, family


def check_reuse(run_root: Path, attempt_id: str, stage: str, run_id: str) -> dict[str, Any]:
    """Every check of the source for ``stage`` before anything is written to the new run (reads only), and what the source says of the label and the statement of the
    session: the label of its request, and for the review the ``fresh_context`` and the label of its boundary (a SELF review takes the label of the new generator)."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    source, family = _checked_source(project, root, state, _attempt_of(state, attempt_id), stage, run_id)
    asked = source["stages"].get(stage)
    told: dict[str, Any] = {"invocation_id": str(asked[0]["invocation_id"]) if asked else None, "fresh_context": None}
    if family == "review":
        isolation = source["boundary"]["host_isolation"]
        told.update(fresh_context=bool(isolation["fresh_context"]), invocation_id=str(source["boundary"]["reviewer_invocation_id"]) if isolation["fresh_context"] else None)
    elif family == "rework_review":  # the second round has no boundary: its independence is the labels of its request and of the rework's
        told["fresh_context"] = told["invocation_id"] != str(source["stages"][_REWORK_STAGE][0]["invocation_id"])
    return told


def _reuse_source(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage_instance_id: str, inputs: Sequence[str], role_policy: str, run_id: str,
) -> dict[str, str]:
    """The reuse record of a request — only when every condition on the source holds and the inputs are the source's."""
    source, family = _checked_source(project, root, state, attempt, stage_instance_id, run_id)
    request, response = source["stages"][stage_instance_id]
    if request.get("role_policy") != role_policy:
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: роль иная: у источника {request.get('role_policy')}, здесь {role_policy}; fix: prepare без --reuse-from")
    given, here = list(request["input_digests"]), list(inputs)
    if family in ("review", "automation"):  # the second input holds the path of the published set (package_digest; the receipt of the bundle): compared whole above, or put again
        given, here = given[:1], here[:1]
    if _marker_differences(project, root, state, attempt, source, here, given) if family == "marker" else given != here:  # a marker: the project by its composition
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: входы иные: у источника {given}, здесь {here}; fix: prepare без --reuse-from")
    if str(request.get("model_id") or "").startswith("controller:"):
        raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: {stage_instance_id} источника сделан кодом (merge) — сделай его тем же merge")
    return {"run_id": run_id, "attempt_id": str(source["attempt"]["attempt_id"]), "request_digest": str(request["digest"]), "response_digest": response}


def _marker_inputs(root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any]) -> list[str]:
    """requirements, inventory and the context receipts of the new run — the inputs a marker of it binds, in order."""
    baseline = frozen_baseline(root, attempt)
    published = dict.fromkeys(str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "CONTEXT_SELECTED")
    return [baseline["requirements"]["digest"], baseline["inventory_digest"], *published]


def _marker_stage_order(stage: str) -> tuple[int, int]:
    """The order the journal accepts — requirements, the parts by number, then the baseline."""
    if stage == "context-marker:requirements":
        return 0, 0
    if stage.startswith("context-marker:batch-"):
        return 1, int(stage.rsplit("-", 1)[1])
    return 2, 0


def _marker_base(source: Mapping[str, Any]) -> list[str]:
    """requirements, inventory and the context receipts a marker of the source bound, in order."""
    stages = source["stages"]
    baseline = list(stages["context-marker:baseline"][0]["input_digests"])
    parts = sorted(stage for stage in stages if stage.startswith("context-marker:batch-"))
    receipts = [str(stages[stage][0]["input_digests"][-2]) for stage in parts] if parts else baseline[2:]
    return [*baseline[:2], *receipts]


def _cases_rows(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, source: dict[str, Any], command: Callable[[str], str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The rows the scan adds for the cases of the source — the plan, the batches in the order of the source, the review, the autotests — or why they are not offered."""
    attempt = _attempt_of(state, attempt_id)
    try:
        changed = _input_differences(source, root, attempt)  # the cases ask the baseline too: the .skillsrc, the profile, the module
        reason = "входы иные: " + ", ".join(row["input"] for row in changed) if changed else _outcome_refusal(source, "plan") or _cases_refusal(project, root, state, attempt_id, source, "plan")
    except ReuseSourceInvalid as error:
        reason = str(error)
    if reason is not None:
        return [], {"cases": {"reused": False, "reason": reason}}

    def row(stage: str, **more: Any) -> dict[str, Any]:
        return {"stage": stage, **more, "command": command(stage)}

    self_review = not source["boundary"]["host_isolation"]["fresh_context"]
    consent = {"needs": "согласие человека на SELF: scan --accept-self-review, review.self в .skillsrc или --accept-self-review на этом шаге"}
    rows = [row("plan"), *(row(stage, response_digest=source["stages"][stage][1]) for stage in source["stages"] if reuse_family(stage) == "batch"),
            row("tc-reviewer:canonical", response_digest=source["stages"]["tc-reviewer:canonical"][1], review="SELF" if self_review else "independent", **(consent if self_review else {}))]
    stages = source["stages"]
    if _REWORK_REVIEW_STAGE in stages:  # the rework and its second round (a source with the rework unfinished is refused above); the independence is the labels of the source
        same = stages[_REWORK_REVIEW_STAGE][0]["invocation_id"] == stages[_REWORK_STAGE][0]["invocation_id"]
        rows += [row(_REWORK_STAGE, response_digest=stages[_REWORK_STAGE][1]), row(_REWORK_REVIEW_STAGE, response_digest=stages[_REWORK_REVIEW_STAGE][1], review="SELF" if same else "independent", **(consent if same else {}))]
    if why := next((why for item in rows if (why := _other_pack(source, item["stage"], skill_pack_digest()))), None):  # the package of each stage, rework included, as ``prepare`` asks
        return [], {"cases": {"reused": False, "reason": why}}
    if "tc-to-autotest:r1" not in source["stages"]:
        return rows, {}
    try:  # the autotests, after the cases: the same project and execution, accepted by the review of revision 1
        skipped = _automation_refusal(project, source, attempt) or _other_pack(source, "tc-to-autotest:r1", skill_pack_digest())
    except ReuseSourceInvalid as error:
        skipped = str(error)
    if skipped is not None:
        return rows, {"automation": {"reused": False, "reason": skipped}}
    return [*rows, row("tc-to-autotest:r1", response_digest=source["stages"]["tc-to-autotest:r1"][1])], {}


def _changed_files(project: Path, root: Path, attempt: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The files of the project that differ between the inventory of this run and the source's — paths and counts (at most 10 paths of each kind), and the words of it."""
    inventory = lambda base, owner: _comparable(project, _frozen_inventory(project, base, str(frozen_baseline(base, owner)["inventory_digest"])))  # noqa: E731
    try:
        inventories = inventory(root, attempt), inventory(source["root"], source["attempt"])
        mine, theirs = ({row["project_path"]: row["content_digest"] for row in item["files"]} for item in inventories)
        left, right = ({(row["project_path"], row["reason_code"]) for row in item["exclusions"]} for item in inventories)
    except (ValueError, OSError, KeyError, TypeError):
        return "", None
    rows = (("added", "появились", sorted(mine.keys() - theirs.keys())), ("removed", "пропали", sorted(theirs.keys() - mine.keys())),
            ("changed", "изменились", sorted(path for path in mine.keys() & theirs.keys() if mine[path] != theirs[path])))
    found = {kind: {"count": len(paths), "paths": paths[:10]} for kind, _word, paths in rows if paths}
    said = [f"{word} {len(paths)}: {', '.join(paths[:10])}{' …' if len(paths) > 10 else ''}" for _kind, word, paths in rows if paths]
    for kind, word, pairs in (("excluded_added", "появились", sorted(left - right)), ("excluded_removed", "пропали", sorted(right - left))):  # the build directories are no difference (``_comparable``)
        other = [f"{path} ({reason})" for path, reason in pairs]
        said += [f"{word} исключённые пути: {', '.join(other[:10])}"] if other else []
        found.update({kind: {"count": len(pairs), "paths": [f"{path} ({reason})" for path, reason in pairs[:10]]}} if pairs else {})
    return (": в проекте " + ("изменились файлы — " if found.keys() & {"added", "removed", "changed"} else "") + "; ".join(said)) if said else "", found or None


def marker_reuse_suggestion(run_root: Path, attempt_id: str) -> dict[str, Any]:
    with closure_cache():  # the journals of the earlier runs are read many times: once each
        return _marker_reuse_suggestion(run_root, attempt_id)


def _marker_reuse_suggestion(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """An earlier run of the project whose context marker — and, when its review accepted the cases whole, the plan, the batches and the review — this attempt may reuse,
    with the commands — or why not.

    A suggestion only: the agent decides; every reused answer still goes through prepare --reuse-from and the same checks as a fresh one."""
    from tools.project_inventory import read_execution_baseline

    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = _attempt_of(state, attempt_id)
    current = _marker_inputs(root, state, attempt)
    pack = skill_pack_digest()
    first_refusal: dict[str, Any] | None = None
    runs = [path for path in (project / ".pilot-runs").iterdir() if path.name != root.name and (path / "events.jsonl").is_file()]
    for path in sorted(runs, key=lambda item: (item / "events.jsonl").stat().st_mtime, reverse=True):
        try:
            source = _run_source(project, path.name, whole=False)
            refusal = _source_refusal(source, str(attempt.get("module")))  # a run whose files do not read is skipped, the search goes on
        except (ValueError, OSError, KeyError, TypeError, StopIteration):
            continue
        differences = []
        if refusal is None:
            try:  # the same decision as ``prepare --reuse-from``: the project by its composition, a receipt of the source that does not read is a refusal with its name
                differences = _marker_differences(project, root, state, attempt, source, current, _marker_base(source))
            except ReuseSourceInvalid as error:
                refusal = str(error)
        model_stages = [stage for stage in sorted(source["stages"], key=_marker_stage_order)
                        if reuse_family(stage) == "marker" and not str(source["stages"][stage][0].get("model_id") or "").startswith("controller:")]
        if refusal is None and differences:
            refusal = "входы иные"
        refusal = refusal or next((why for stage in model_stages or ["context-marker:baseline"] if (why := _other_pack(source, stage, pack))), None)  # the package of each stage, as ``prepare`` asks
        if refusal is not None:
            said, files = _changed_files(project, root, attempt, source) if first_refusal is None and refusal == "входы иные" and any(row["input"] == "inventory" for row in differences) else ("", None)
            first_refusal = first_refusal or {"run_id": None, "source_run_id": path.name, "reason": refusal + said,
                                              **({"differences": differences, **({"files": files} if files else {})} if refusal == "входы иные" else {})}
            continue
        from tools.run_pipeline import HostStop, printed

        try:  # a source whose answer file lies where no shell takes the path is not offered: the commands would not be run as they are
            rows = []
            made_by = {stage: str(source["stages"][stage][0].get("model_id") or "") for stage in source["stages"]}
            if made_by.get("context-marker:requirements") in ("controller:cases-import", OPENSPEC_MODEL_ID):  # an import, or OpenSpec files alone, make it by code
                rows.append({"stage": "context-marker:requirements", "by": "code",
                             "command": f"python -m tools.api prepare --run-root {printed(root)} --stage context-marker:requirements"})
            for stage in model_stages:  # the reuse answers each stage from the source run itself; the merge of the parts follows by code
                rows.append({"stage": stage, "response_digest": source["stages"][stage][1],
                             "command": f"python -m tools.api prepare --run-root {printed(root)} --stage {stage} --reuse-from {path.name}"})
            try:
                whole = _run_source(project, path.name)  # the one source offered is read whole, for its cases
            except ValueError as error:
                more, flag = [], {"cases": {"reused": False, "reason": str(error)}}
            else:
                more, flag = _cases_rows(project, root, state, attempt_id, whole,
                                         lambda stage: f"python -m tools.api prepare --run-root {printed(root)} --stage {stage} --reuse-from {path.name}")
            return {"run_id": path.name, "stages": [*rows, *more], **flag}
        except HostStop:
            first_refusal = first_refusal or {"run_id": None, "source_run_id": path.name, "reason": "путь файла ответа источника нельзя записать в команде"}
            continue
    return first_refusal or {"run_id": None, "reason": _REUSE_FREE}


class ModelResponseDigestMismatch(ValueError):
    """A digest field of a model answer that does not match the journal; nothing is written."""

    code = "MODEL_RESPONSE_DIGEST_MISMATCH"


def _generator_input_digest(value: Mapping[str, Any]) -> str:
    from tools.batch_assembly import with_digest

    return with_digest({key: item for key, item in value.items() if key != "digest"})["digest"]


def _plan_contexts(plan: Mapping[str, Any]) -> list[str]:
    """The context receipts every generator batch of the plan reads, in order (one before the list)."""
    from tools.batch_assembly import context_digests

    if isinstance(plan.get("context_receipt_digests"), list) or "context_receipt_digest" in plan:
        return context_digests(plan)
    batches = plan.get("batches") or []
    return [str(batches[0]["context_receipt_digest"])] if batches and isinstance(batches[0], Mapping) else []


def _plan_batches_listed(plan: Mapping[str, Any]) -> bool:
    batches = plan.get("batches")
    return isinstance(batches, list) and bool(batches) and all(
        isinstance(row, Mapping) and isinstance(row.get("batch_id"), str)
        and _DIGEST.fullmatch(str(row.get("context_receipt_digest", ""))) is not None
        and _DIGEST.fullmatch(str(row.get("header_digest", ""))) is not None
        for row in batches
    )


def context_receipts(root: Path, attempt_id: str, state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The context receipts of the attempt in batch_index order."""
    digests = dict.fromkeys(str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED")
    try:
        return sorted((read_context_selection(root, attempt_id, digest) for digest in digests), key=lambda receipt: int(receipt["batch_index"]))
    except ValueError as error:  # the receipts are the attempt's: one that does not read back is a changed file of the context
        raise ValueError("CONTEXT_DRIFTED: a context receipt of the attempt does not match the files of the context any more; fix: `run_pipeline status` shows "
                         "drift.changed — restore those files, or start the run again with `scan --new-run`") from error


@closure_cached
def _check_plan_against(root: Path, attempt_id: str, item: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    """The checks a plan must pass before it is published — its batches bind context receipts of the attempt, the receipts are a prefix in
    batch_index order.  They write nothing, so ``publish_plan`` can run them before it publishes the header and a refusal leaves the journal as it was."""
    unfinished = context_incomplete(root, attempt_id, state)
    if unfinished is not None:
        raise ValueError(scan_incomplete_message(*unfinished))
    for row in item["batches"]:
        for context in dict.fromkeys([str(row["context_receipt_digest"]), *_plan_contexts(item)]):  # and the list
            try:
                read_context_selection(root, attempt_id, context)
            except ValueError as error:
                selected = sorted({str(event["artifact_digest"]) for event in state["events"]
                                   if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"})
                if context in selected:  # the receipt is the attempt's, its bytes no longer match the project
                    raise ValueError(f"CONTEXT_DRIFTED: the context receipt {context} of batch {row['batch_id']} is the attempt's, but the "
                                     "files of the context changed after the scan; fix: `run_pipeline status` shows drift.changed — "
                                     "restore those files, or start the run again with `scan --new-run`") from error
                raise ValueError(f"GENERATOR_INPUT_CONTEXT: batch {row['batch_id']} binds the context {context}, which "
                                 f"is not a context receipt of this attempt; the attempt has: {', '.join(selected) or 'none'}") from error
    # the plan reads a prefix, in batch_index order, of every context receipt of the attempt — a skipped
    # receipt (1 and 3 without 2) is one the generator would never read while the note says it saw them all
    ordered = context_receipts(root, attempt_id, state)
    contexts, prefix = _plan_contexts(item), [str(receipt["digest"]) for receipt in ordered]
    if contexts != prefix[:len(contexts)]:
        index = next(index for index, (given, wanted) in enumerate(zip(contexts, prefix)) if given != wanted)
        raise ValueError(
            f"GENERATOR_INPUT_CONTEXT: the plan reads the context receipts {', '.join(contexts)}; position {index + 1} must be "
            f"{prefix[index]} (batch_index {ordered[index]['batch_index']}) — the plan takes the receipts of the attempt in batch_index "
            f"order from the first, none skipped: {', '.join(prefix)}")
    held = {row["project_path"] for receipt in ordered[:len(contexts)] for row in receipt["files"]}  # the files the plan names are files of the receipts it reads
    if item.get("context_files") and (lost := [path for path in item["context_files"] if path not in held]):
        raise ValueError(f"GENERATOR_INPUT_CONTEXT: context_files names files that no context receipt of the plan holds: {', '.join(lost[:3])}")


def publish_generator_input(run_root: Path, attempt_id: str, kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Publish the batch plan or the generator header of the attempt with its one digest — the self-digest.  One plan and one header per
    attempt; the same value again changes nothing.  A plan the generator request would refuse is refused before anything is written."""
    item = {**value, "digest": _generator_input_digest(value)}
    receipt_kind = _GENERATOR_INPUT_KINDS[kind]
    project, root = _run_root(run_root)
    if kind == "plan":
        state = derive_state(root)
        from tools.scope_pause import check_plan

        check_plan(root, attempt_id, item, state)  # the pause after the marker — the plan carries the human's answer
        _check_plan_against(root, attempt_id, item, state)
    try:
        prior = _read_attempt_receipt_with_state(project, root, derive_state(root), attempt_id, receipt_kind, "ARTIFACT_READ_BACK")["record"][kind]
    except (ValueError, OSError, KeyError):
        prior = None
    if prior is not None and prior != item:
        raise ValueError(f"GENERATOR_INPUT_CONFLICT: this attempt already has the {kind} {prior.get('digest')}; "
                         "another one needs a new run (scan --new-run)")
    publish_bound(run_root, attempt_id, receipt_kind, {kind: item})
    skeletons = _publish_batch_skeletons(run_root, attempt_id) if kind == "plan" else {}  # the last input of each batch
    context = None
    if kind == "plan":  # which context batches of the attempt the generator sees — the line of the reviewer view
        from tools.review_view import run_context_note

        try:  # a hint, never a stop — as in the reviewer view
            context = run_context_note(root, attempt_id)
        except (KeyError, OSError, ValueError, TypeError):
            pass
    return {"kind": kind, "digest": item["digest"],
            "path": str(_receipt_target(root, attempt_id, receipt_kind).relative_to(root)).replace("\\", "/"),
            **({"batch_skeletons": skeletons} if skeletons else {}), **({"context": context} if context else {})}


PLAN_FORM = {"subject": "<название предмета проверки по-русски>"}


def header_document_id(docs: Sequence[str], module: str) -> str:
    """``TCDOC-`` and the slug of the name of the first document (without its section and extension), else of the module, else ``run``:
    lower case, ``[a-z0-9_.-]``, at most 80 signs — a name in Cyrillic has no slug of its own."""
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9_.-]+", "-", text.lower()).strip("-._")[:80].strip("-._")

    first = PurePosixPath(str(docs[0]).split("#", 1)[0].replace("\\", "/")).stem if docs else ""
    return "TCDOC-" + (slug(first) or slug(module) or "run")


def document_texts(root: Path, attempt_id: str, state: Mapping[str, Any]) -> dict[str, str]:
    """The plain documents ``--docs`` names as the context receipts of the attempt hold them (path -> text, read by the digests of the receipts)."""
    return _named_texts(root.parents[1], root, _attempt_of(state, attempt_id), [str(item["digest"]) for item in context_receipts(root, attempt_id, state)])


def label_texts(root: Path, attempt_id: str, state: Mapping[str, Any]) -> dict[str, str]:
    """The texts the labels of the documents are counted in (path -> text) as the context receipts of the attempt hold them: the step of the marker reads the numbers of these."""
    return _label_texts(root.parents[1], root, _attempt_of(state, attempt_id), [str(item["digest"]) for item in context_receipts(root, attempt_id, state)])


def plan_inputs(root: Path, attempt_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
    """What the plan is built from — the journal's: the requirements of the answered marker (and the file of that answer, measured for the batch
    window), the context receipts, the exclusions the human recorded."""
    from tools.cases_import import run_skeleton
    from tools.scope_pause import cut_rows, document_texts as scope_texts, out_of_scope, scope_state

    receipts = context_receipts(root, attempt_id, state)  # first: a changed file of the context makes the answer of the marker unreadable too
    answer = next(event for event in reversed(state["events"]) if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                  and event.get("stage_instance_id") == "context-marker:baseline")
    read = read_model_stage_artifact(root, attempt_id, "context-marker:baseline", str(answer["artifact_digest"]))
    requirements, human = read["artifact"]["artifacts"]["analytics_documentation"]["requirements"], out_of_scope(scope_state(root, attempt_id, state)["decision"])
    left = human + cut_rows(requirements, {row["source_requirement_id"] for row in human}, run_skeleton(root, _attempt_of(state, attempt_id)),
                                                                                 scope_texts(root, attempt_id, state))  # a requirement with no expected result gets no case
    return {"requirements": requirements, "answer": root / read["path"], "receipts": receipts,
            "sources": read["artifact"]["artifacts"]["source_code_and_diff"].get("sources") or [], "out_of_scope": left or None}


def plan_view(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {"digest": plan["digest"], "batches": [{"stage": f"tc-generator:{row['batch_id']}", "batch_id": row["batch_id"], "namespace": row["namespace"],
                                                  "requirements": len(row["owned_source_requirement_ids"])} for row in plan["batches"]]}


def plan_ask(root: Path, attempt_id: str, done: bool) -> dict[str, Any]:
    """``prepare --stage plan``: the plan that stands, or the form of the one answer and what the plan will be built from."""
    state = derive_state(root)
    if done:
        return {"answered": True, "plan": plan_view(_generator_input(root.parents[1], root, state, attempt_id, "plan"))}
    found = plan_inputs(root, attempt_id, state)
    return {"answer": PLAN_FORM, "requirements": len(found["requirements"]), "out_of_scope": len(found["out_of_scope"] or []), "context_receipts": len(found["receipts"])}


def publish_plan(run_root: Path, attempt_id: str, answer: Any, dry_run: bool = False, header_date: str | None = None) -> dict[str, Any]:
    """``submit --stage plan``: the header and the plan of the batches built from the journal and the subject the agent named, every check before
    anything is written (``dry_run`` stops there), then the skeleton of an import, the header, the plan and the skeletons of its batches."""
    from tools.batch_assembly import plan_batches_for_project
    from tools.cases_import import cases_path, run_skeleton
    from tools.doc_sections import resolve_spec
    from tools.run_pipeline import relative_to_project
    from tools.scope_pause import check_plan
    from tools.step_size import require_generator_context_all

    subject = answer.get("subject") if isinstance(answer, Mapping) and set(answer) == {"subject"} else None
    if not (isinstance(subject, str) and subject.strip() and len(subject) <= 200 and not re.search(r"[\x00-\x1f\x7f-\x9f]", subject)):
        raise ValueError("PLAN_ANSWER_INVALID: ответ — один ключ subject, непустая строка до 200 знаков без управляющих символов")
    project, root = _run_root(run_root)
    require_generator_context_all(project)  # limits.generator_context: own is not implemented yet
    state = derive_state(root)
    unfinished = context_incomplete(root, attempt_id, state)
    if unfinished is not None:
        raise ValueError(scan_incomplete_message(*unfinished))
    attempt = _attempt_of(state, attempt_id)
    found = plan_inputs(root, attempt_id, state)
    request = read_run(root)["manifest"].get("scan_request") or {}  # the paths as typed: the header names them as the scan does, inside the project (``relative_to_project``)
    docs = list(dict.fromkeys(relative_to_project(project.resolve(), resolve_spec(project, doc)[0]) for doc in request.get("docs") or []))
    docs = docs or ([relative_to_project(project.resolve(), request["cases"])] if request.get("cases") else [])  # an import without documents: its CSV is the document of the run
    header = {"schema_version": "1.1.0", "document_id": header_document_id(docs, str(attempt.get("module"))), "revision": 1, "parent_sha256": None, "content_locale": "ru-RU",
              "metadata": {"subject": {"kind": "generic", "name": subject.strip()}, "documentation": docs, "project": project.name, "author": "controller",
                           "date": header_date or str(state["events"][0]["observed_at"])[:10]}}  # the date of the run: the journal's, so a repeat gives the same bytes; a reuse takes the source's
    header_digest = _generator_input_digest(header)
    receipts = [str(receipt["digest"]) for receipt in found["receipts"]]
    plan = plan_batches_for_project(found["requirements"], {"header_digest": header_digest, "context_receipt_digest": receipts[0], "context_receipt_digests": receipts},
                                    project, [found["answer"]], found["receipts"], run_skeleton(root, attempt), found["out_of_scope"], found["sources"])
    item_plan = {**plan, "digest": _generator_input_digest(plan)}
    check_plan(root, attempt_id, item_plan, state)  # the pause after the marker, then the bindings: before anything is written
    _check_plan_against(root, attempt_id, item_plan, state)
    from tools.build_context import NOT_DIVISIBLE_FIX, bloated_marker, csv_import_size, large_document, say

    named = _named_sections(project, root)  # the plan knows the requirements of the marker, whatever the document is: their number is the estimate (``ready`` has the numbers of the document only)
    count = len(found["requirements"]) - len(found["out_of_scope"] or [])
    texts, automation, openspec = _named_texts(project, root, attempt, receipts), attempt.get("policy_profile") == "local-pilot-v1", any(path.startswith("openspec/") for path in named)
    size = (csv_import_size(count) if not request.get("docs") and request.get("cases") else  # an import without documents: its requirements are the cases of the CSV, a warning only — the step budget is the guard
            large_document(project, texts, named, automation, count, openspec))
    bloat = None
    if size:  # more rows than the document has numbers, and its numbers fit: not a document too large, a marker that took paragraphs for requirements
        found_labels = _label_found(project, root, attempt, receipts)
        by_numbers = large_document(project, texts, named, automation, sum(len(labels) for labels in found_labels.values()), openspec)
        bloat = None if by_numbers and by_numbers[0] else bloated_marker(count, found_labels)
    if size and size[0]:  # the review of this set cannot be held: before the generator writes a byte, the sections `ready` names — or, where nothing tells them apart, the honest way out
        if bloat:  # no fix tail: the fix of this cause is in tools.fixes
            raise ValueError(f"PLAN_REVIEW_TOO_LARGE: {bloat}")
        raise ValueError(f"PLAN_REVIEW_TOO_LARGE: {size[1]}; fix: {size[2] or NOT_DIVISIBLE_FIX}; {CLOSE_RUN_FIX}")
    shown = {"plan": plan_view(item_plan), "header": {"digest": header_digest}, "notes": [*plan.notes, *([f"ВНИМАНИЕ: {bloat or say(size[1], size[2])}"] if size else [])]}
    if dry_run:
        return shown
    if cases_path(root):  # the skeleton of an import with documents is published by the plan
        publish_imported_cases(run_root, attempt_id)
    publish_generator_input(run_root, attempt_id, "header", header)
    published = publish_generator_input(run_root, attempt_id, "plan", dict(plan))
    return {**shown, **({"context": published["context"]} if "context" in published else {})}


def reuse_plan(run_root: Path, attempt_id: str, run_id: str) -> dict[str, Any]:
    """``prepare --stage plan --reuse-from``: the header and the plan of the source — its subject and its date, so the batches keep their ids and the document its digest —
    built here by the code of the new run and compared with the source's before anything is written."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = _attempt_of(state, attempt_id)
    source, _family = _checked_source(project, root, state, attempt, "plan", run_id)
    satt = str(source["attempt"]["attempt_id"])
    header = _strict(run_id, "generator-headers", lambda: _generator_input(project, source["root"], source["state"], satt, "header"))
    plan = _strict(run_id, "batch-plans", lambda: _generator_input(project, source["root"], source["state"], satt, "plan"))
    answer, date = {"subject": header["metadata"]["subject"]["name"]}, header["metadata"]["date"]
    shown = publish_plan(run_root, attempt_id, answer, dry_run=True, header_date=date)
    for name, built, kept in (("заголовок", shown["header"]["digest"], header["digest"]), ("план", shown["plan"]["digest"], plan["digest"])):
        if built != kept:
            raise ModelReuseRefused(f"MODEL_REUSE_REFUSED: прогон {run_id}: {name} нового прогона иной, чем у источника ({built}, у источника {kept}); fix: prepare без --reuse-from")
    published = publish_plan(run_root, attempt_id, answer, header_date=date)
    return {"answered": True, "model_call": False, "reused_from": run_id, "plan": published["plan"]}


def _published_generator_inputs(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The published (plan, header) of the attempt, or None when the run published neither (in a run of an earlier version)."""
    try:
        return _generator_input(project, root, state, attempt_id, "plan"), _generator_input(project, root, state, attempt_id, "header")
    except ModelInputsMismatch:
        return None


def _generator_input(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, kind: str) -> Mapping[str, Any]:
    try:
        return _read_attempt_receipt_with_state(project, root, state, attempt_id, _GENERATOR_INPUT_KINDS[kind], "ARTIFACT_READ_BACK")["record"][kind]
    except (ValueError, OSError, KeyError) as error:
        raise ModelInputsMismatch(
            f"MODEL_INPUTS_MISMATCH: the {kind} of this attempt is not published; fix: submit --stage plan "
            "(prepare --stage plan names the answer)"
        ) from error


def _expected_model_inputs(
    project: Path, root: Path, state: Mapping[str, Any], attempt: Mapping[str, Any], stage_instance_id: str,
) -> list[tuple[str, str]] | None:
    """The ordered input digests the journal fixes for a model request, each with where it comes from.

    None — the journal does not fix them here (context-marker, or a predecessor is missing: the existing checks name it)."""
    stage, _, instance = stage_instance_id.partition(":")
    attempt_id = str(attempt["attempt_id"])
    if stage_instance_id == _REWORK_STAGE:  # the answer of the reviewer, r1 and the context receipts of the batches that hold the rejected cases: all from the journal
        from tools.orchestrate_test_case_revision import rework_basis

        try:
            basis = rework_basis(root, attempt_id)
        except (KeyError, StopIteration, TypeError, ValueError, OSError) as error:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id} is asked for only after the first review sent cases back for a rework ({error}); fix: `status` names the next step") from error
        from tools.canonical_document import document_sha256

        plan = _generator_input(project, root, state, attempt_id, "plan")
        contexts = list(dict.fromkeys([*basis["contexts"], *(_plan_contexts(plan) if plan.get("context_files") else [])]))  # the files the plan names lie in all its receipts
        return [(basis["answer_digest"], "ответ tc-reviewer:canonical журнала"), (document_sha256(basis["candidate"]), "кандидат r1 журнала"),
                *((digest, "context_receipt_digest пачки с отклонённым кейсом") for digest in contexts)]
    if stage_instance_id == _REWORK_REVIEW_STAGE:  # the answer of the generator of the rework and r3 with its cases: from the journal
        from tools.orchestrate_test_case_revision import rework_round_data

        try:
            data = rework_round_data(root, attempt_id, need_answer=True)
        except (KeyError, StopIteration, TypeError, ValueError, OSError) as error:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id} is asked for only after the rework is answered ({error}); fix: `status` names the next step") from error
        return [(data["answer_digest"], "ответ tc-generator:rework-1 журнала"), (data["r3_digest"], "r3: r2 и кейсы ответа доработки")]
    if stage == "tc-generator":
        markers = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "context-marker:baseline"]
        if len(markers) != 1:
            return None
        plan, header = _generator_input(project, root, state, attempt_id, "plan"), _generator_input(project, root, state, attempt_id, "header")
        batch = next((row for row in plan["batches"] if row.get("batch_id") == instance), None)
        if batch is None:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {instance} is not a batch of the published plan {plan['digest']}")
        if batch["header_digest"] != header["digest"]:
            raise ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: the plan binds the header {batch['header_digest']}, the published header is "
                                      f"{header['digest']}; the plan is built by submit --stage plan with the header digest")
        expected = [
            (str(markers[0]["artifact_digest"]), "content_digest ответа context-marker:baseline (submit)"),
            (str(batch["context_receipt_digest"]), f"context_receipt_digest батча {instance} в опубликованном плане"),
            # the further context receipts of the plan, in its order
            *((context, f"context_receipt_digests[{index}] опубликованного плана") for index, context in enumerate(_plan_contexts(plan)) if index),
            (str(plan["digest"]), "digest плана (submit --stage plan)"),
            (str(header["digest"]), "digest header (submit --stage plan)"),
        ]
        if _batch_ordinal(batch) > 1:  # from batch 2 the capabilities of the accepted earlier batches
            prior = _prior_capabilities_value(project, root, state, attempt_id, instance)
            try:
                _read_attempt_receipt_with_state(project, root, state, attempt_id, _PRIOR_KIND + instance, "ARTIFACT_READ_BACK")
            except (ValueError, OSError, KeyError) as error:
                raise ModelInputsMismatch(
                    f"MODEL_INPUTS_MISMATCH: capabilities прошлых пачек для {instance} не опубликованы; fix: их публикует prepare --stage tc-generator:{instance}"
                ) from error
            expected.append((str(prior["digest"]), f"digest capabilities прошлых пачек для {instance} (их публикует prepare)"))
        if _imported_cases(project, root, state, attempt_id) is not None:  # the skeleton of this batch is its last input
            try:
                skeleton = _read_attempt_receipt_with_state(project, root, state, attempt_id, _SKELETON_KIND + instance, "ARTIFACT_READ_BACK")["record"]["imported"]
            except (ValueError, OSError, KeyError) as error:
                raise ModelInputsMismatch(
                    f"MODEL_INPUTS_MISMATCH: скелет пачки {instance} не опубликован; fix: повтори submit --stage plan — он опубликует скелеты пачек"
                ) from error
            expected.append((str(skeleton["digest"]), f"скелет пачки {instance} (submit --stage plan)"))
        return expected
    try:
        if stage == "tc-reviewer":
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
            return [(str(boundary["canonical_branch_digest"]), "boundary.canonical_branch_digest (квитанция reviewer-session-boundary)"),
                    (str(boundary["package_digest"]), "boundary.package_digest (квитанция reviewer-session-boundary)")]
        if stage in {"tc-to-autotest", "autotest-reviewer"}:
            return _automation_inputs(project, root, state, attempt_id, stage_instance_id)
    except (ValueError, OSError, KeyError, TypeError):
        return None
    return None


def _automation_inputs(project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str) -> list[tuple[str, str]]:
    """The two inputs of tc-to-autotest / autotest-reviewer read from the journal; an unreadable predecessor raises its read error."""
    stage, _, instance = stage_instance_id.partition(":")
    if stage == "tc-to-autotest":
        effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
        return [(str(effective["document_digest"]), "effective.document_digest (эффективный канон)"),
                (str(effective["effective_bundle_receipt_digest"]), "effective.effective_bundle_receipt_digest (эффективный канон)")]
    boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, f"automation-review-boundary-{instance}", "ARTIFACT_READ_BACK")["record"]
    return [(str(boundary["automation_digest"]), f"boundary.automation_digest (квитанция automation-review-boundary-{instance})"),
            (str(boundary["digest"]), f"boundary.digest (квитанция automation-review-boundary-{instance})")]


def _inputs_mismatch(stage_instance_id: str, inputs: Sequence[str], expected: Sequence[tuple[str, str]]) -> ModelInputsMismatch:
    rows = [f"вход {index}: дан {inputs[index - 1] if index <= len(inputs) else 'нет'}, ожидается {digest} — {source}"
            for index, (digest, source) in enumerate(expected, start=1) if index > len(inputs) or inputs[index - 1] != digest]
    if len(inputs) > len(expected):
        rows.append(f"лишние входы: {', '.join(inputs[len(expected):])}")
    if stage_instance_id.startswith("tc-reviewer:"):  # the reviewer's request is the work of prepare, one fix for the whole chain
        from tools.fixes import REVIEW_CHAIN

        return ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id}: " + "; ".join(rows) + "; fix: запрос ревьюера кейсов пишет `prepare --stage tc-reviewer:canonical` "
                                   "(он знает оба digest сам), руками он не нужен — " + REVIEW_CHAIN)
    return ModelInputsMismatch(f"MODEL_INPUTS_MISMATCH: {stage_instance_id}: " + "; ".join(rows) + "; fix: повтори prepare этой стадии (входы он берёт из журнала сам)")


_PROVENANCE_LINE = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+)(?!\d)")


def _requirement_order_rows(stage_instance_id: str, value: Mapping[str, Any]) -> str | None:
    """The first source requirement out of document order, or None.  In display_order the requirements of one
    document come together (the documents in the answer's own order — of --docs or the task first, never forced by path)
    and its lines grow as numbers (`…md:4` before `…md:12`), by the first provenance `path:line` of each; the SREQ numbers
    follow display_order.  A row whose first reference names no line leaves the whole order to the model."""
    if not stage_instance_id.startswith("context-marker"):
        return None
    artifacts = value.get("artifacts") if isinstance(value, Mapping) else None
    documentation = artifacts.get("analytics_documentation") if isinstance(artifacts, Mapping) else None
    rows = documentation.get("requirements") if isinstance(documentation, Mapping) else None
    if not isinstance(rows, list) or not rows:
        return None
    keyed = []
    for row in rows:
        provenance = row.get("provenance") if isinstance(row, Mapping) else None
        found = _PROVENANCE_LINE.match(provenance[0]) if isinstance(provenance, list) and provenance and isinstance(provenance[0], str) else None
        if found is None or not isinstance(row.get("display_order"), int):
            return None  # without a line reference the order is the model's
        keyed.append(((found.group("path"), int(found.group("line"))), row))
    by_display = sorted(keyed, key=lambda item: item[1]["display_order"])
    finished: set[str] = set()
    for (previous, _), ((path, line), row) in zip(by_display, by_display[1:]):
        name = f"{row.get('source_requirement_id')} ({path}:{line}, display_order {row['display_order']})"
        if path != previous[0]:
            finished.add(previous[0])
            if path in finished:
                return f"{name}: требования документа {path} идут не подряд — после строк {previous[0]}"
        elif line < previous[1]:
            return f"{name} стоит после строки {previous[0]}:{previous[1]} того же документа"
    # the number at the end of each ID as a number (SREQ-9 before SREQ-10); an ID without one leaves the numbering alone
    numbers = [re.search(r"(\d+)$", str(row.get("source_requirement_id") or "")) for _key, row in by_display]
    if all(numbers) and [int(found.group(1)) for found in numbers] != sorted(int(found.group(1)) for found in numbers):
        return "номера SREQ идут не в порядке display_order"
    return None


_SELF_DIGEST = "самодайджест фрагмента (без поля digest)"


def _response_bindings(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: Mapping[str, Any],
) -> list[tuple[str, str | None, str]]:
    """The digest fields of a model answer the journal fixes — (pointer, expected, source).  The one list
    both for the check (``_response_digest_rows``) and for the fill (``_fill_response_bindings``); the expected value of the
    fragment self-digest is None — it is the digest of the answer itself, taken last.

    A shape the schema refuses yields what it could read; the schema and the later checks stay the backstop."""
    stage, _, instance = stage_instance_id.partition(":")
    rows: list[tuple[str, str | None, str]] = []

    def add(pointer: str, expected: str | None, source: str) -> None:
        rows.append((pointer, expected, source))

    def answer(stage_id: str, event_type: str) -> Mapping[str, Any]:
        events = [event for event in state["events"] if event.get("attempt_id") == attempt_id and event.get("stage_instance_id") == stage_id
                  and event["event_type"] == event_type]
        return _read_model_stage_artifact_with_state(project, root, state, attempt_id, stage_id, str(events[-1]["artifact_digest"]))["artifact"]

    try:
        if stage_instance_id == _REWORK_STAGE:
            pass  # the answer holds cases only: nothing in it is a digest the journal fixes
        elif stage_instance_id == _REWORK_REVIEW_STAGE:
            add("/artifacts/validation_report/candidate/document_sha256", str(request["input_digests"][1]), "r3 (вход 2 запроса)")
            if value["artifacts"]["validation_report"]["verdict"] == "ПРИНЯТО" and isinstance(value["artifacts"]["validation_report"].get("effective"), Mapping):
                add("/artifacts/validation_report/effective/document_sha256", str(request["input_digests"][1]), "ПРИНЯТО принимает r3 (вход 2 запроса)")
        elif stage == "tc-generator":
            inputs = request["input_digests"]
            try:  # further context receipts of the published plan shift plan and header
                more = len(_plan_contexts(_generator_input(project, root, state, attempt_id, "plan"))) - 1
            except ModelInputsMismatch:
                more = 0
            for pointer, index, name in (("/context_receipt_digest", 1, "context"), ("/plan_digest", 2 + more, "plan"), ("/header_digest", 3 + more, "header")):
                add(pointer, str(inputs[index]), f"вход {index + 1} запроса ({name})")
            add("/digest", None, _SELF_DIGEST)
        elif stage == "tc-reviewer":
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
            report = value["artifacts"]["validation_report"]
            candidate = str(boundary["canonical_branch_digest"])
            add("/artifacts/validation_report/candidate/document_sha256", candidate, "boundary.canonical_branch_digest")
            if report["verdict"] == "ПРИНЯТО" and isinstance(report.get("effective"), Mapping):
                add("/artifacts/validation_report/effective/document_sha256", candidate, "ПРИНЯТО выбирает кандидата: boundary.canonical_branch_digest")
        elif stage == "tc-to-autotest":
            effective = _read_effective_canonical_with_state(project, root, state, attempt_id)
            artifacts = value["artifacts"]
            add("/artifacts/source/source_digest", str(effective["document_digest"]), "effective.document_digest (эффективный канон)")
            add("/artifacts/source/effective_bundle_receipt_digest", str(effective["effective_bundle_receipt_digest"]),
                "effective.effective_bundle_receipt_digest (эффективный канон)")
            for index, row in enumerate(artifacts["generated_files"]):
                add(f"/artifacts/generated_files/{index}/content_digest", "sha256:" + hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest(),
                    "sha256 поля content этого файла (UTF-8)")
            if instance == "r2":
                automation_sha256, autotest_review_sha256 = execution_gate.from_mode_b("automation_validation", "automation_sha256", "autotest_review_sha256")
                add("/artifacts/predecessor_automation_sha256", automation_sha256(answer("tc-to-autotest:r1", "CANDIDATE_PUBLISHED")),
                    "digest ответа tc-to-autotest:r1")
                add("/artifacts/correction_review_sha256", autotest_review_sha256(answer("autotest-reviewer:r1", "MODEL_RESPONSE_RECEIVED")),
                    "digest ответа autotest-reviewer:r1")
        elif stage == "autotest-reviewer":
            automation_sha256, implementation_relations_sha256 = execution_gate.from_mode_b(
                "automation_validation", "automation_sha256", "implementation_relations_sha256")
            review = value["artifacts"]["autotest_review"]
            automation = answer(f"tc-to-autotest:{instance}", "CANDIDATE_PUBLISHED")
            boundary = _read_attempt_receipt_with_state(project, root, state, attempt_id, f"automation-review-boundary-{instance}", "ARTIFACT_READ_BACK")["record"]
            add("/artifacts/autotest_review/automation_sha256", automation_sha256(automation), f"digest ответа tc-to-autotest:{instance}")
            add("/artifacts/autotest_review/reviewed_relations_sha256", implementation_relations_sha256(automation["artifacts"]["implementation_relations"]),
                f"digest массива implementation_relations ответа tc-to-autotest:{instance} (не digest артефакта)")
            add("/artifacts/autotest_review/host_isolation_sha256", str(boundary["digest"]), f"boundary.digest (квитанция automation-review-boundary-{instance})")
            for key in ("source_digest", "effective_bundle_receipt_digest"):
                add(f"/artifacts/autotest_review/source/{key}", str(automation["artifacts"]["source"][key]), f"source.{key} ответа tc-to-autotest:{instance}")
            files = {row["file_id"]: row["content_digest"] for row in automation["artifacts"]["generated_files"]}
            for index, row in enumerate(review["reviewed_files"]):
                if row["file_id"] in files:
                    add(f"/artifacts/autotest_review/reviewed_files/{index}/content_digest", files[row["file_id"]],
                        f"content_digest файла {row['file_id']} в ответе tc-to-autotest:{instance}")
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return rows
    return rows


def _pointer_tokens(pointer: str) -> list[str | int]:
    return [int(token) if token.isdigit() else token for token in pointer.strip("/").split("/")]


def _pointer_get(value: Any, pointer: str) -> Any:
    for token in _pointer_tokens(pointer):
        if isinstance(token, int) and isinstance(value, list) and token < len(value):
            value = value[token]
        elif isinstance(token, str) and isinstance(value, Mapping):
            value = value.get(token)
        else:
            return None
    return value


def _self_digest(value: Mapping[str, Any]) -> str:
    return _model_stage_artifact_digest({key: item for key, item in value.items() if key != "digest"})


def _response_digest_rows(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: Mapping[str, Any],
) -> list[tuple[str, Any, str, str]]:
    """The digest fields of a model answer against the journal — (pointer, given, expected, source) per mismatch."""
    rows = []
    for pointer, expected, source in _response_bindings(project, root, state, attempt_id, stage_instance_id, request, value):
        expected = _self_digest(value) if expected is None else expected
        given = _pointer_get(value, pointer)
        if given != expected:
            rows.append((pointer, given, expected, source))
    return rows


_REUSED_AUTOMATION = "tc-to-autotest:r1"


_REUSE_REBOUND = "effective_bundle_receipt_digest"  # the one field of a reused answer that holds the path of the run (the digest of the bundle published in it)


def _rebound(value: Mapping[str, Any], to: Any) -> dict[str, Any]:
    """A copy of the autotest answer with the field ``_REUSE_REBOUND`` of its ``source`` set to ``to``."""
    copy = json.loads(json.dumps(value))
    if isinstance(copy.get("artifacts"), dict) and isinstance(copy["artifacts"].get("source"), dict):
        copy["artifacts"]["source"][_REUSE_REBOUND] = to
    return copy


def _is_source_answer(root: Path, reuse: Mapping[str, Any], stage_instance_id: str, value: Mapping[str, Any], content_digest: str) -> bool:
    """The answer of a reuse request is the source answer byte for byte — the autotests but for ``_REUSE_REBOUND``, which is the new run's."""
    if content_digest == reuse["response_digest"]:
        return True
    if stage_instance_id != _REUSED_AUTOMATION:
        return False
    source = read_model_stage_artifact(Path(root).parent / str(reuse["run_id"]), str(reuse["attempt_id"]), stage_instance_id, str(reuse["response_digest"]))["artifact"]
    return _model_stage_artifact_bytes(_rebound(source, None)) == _model_stage_artifact_bytes(_rebound(value, None))


def _fill_response_bindings(
    project: Path, root: Path, state: Mapping[str, Any], attempt_id: str, stage_instance_id: str,
    request: Mapping[str, Any], value: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Audit U2: the digest fields of ``_response_bindings`` the answer leaves out (no key, null or "") — the code puts the
    journal value there, the fragment self-digest last.  Only these fields; a field given with another value is left as it
    is for the refusal (MODEL_RESPONSE_DIGEST_MISMATCH) — never mended silently."""
    filled: list[str] = []
    value = json.loads(json.dumps(value, ensure_ascii=False))
    self_digest = None
    for pointer, expected, _source in _response_bindings(project, root, state, attempt_id, stage_instance_id, request, value):
        if _pointer_get(value, pointer) not in (None, ""):
            continue
        if expected is None:
            self_digest = pointer
            continue
        tokens = _pointer_tokens(pointer)
        parent: Any = value
        for token in tokens[:-1]:
            if isinstance(token, int):
                if not isinstance(parent, list) or token >= len(parent):
                    parent = None
                    break
                parent = parent[token]
            else:
                if not isinstance(parent, dict):
                    parent = None
                    break
                parent = parent.setdefault(token, {})
        if isinstance(parent, dict):
            parent[str(tokens[-1])] = expected
            filled.append(pointer)
    if self_digest is not None:
        value["digest"] = _self_digest(value)
        filled.append(self_digest)
    return value, filled


generator_input = _generator_input  # the published plan or header of the attempt (step_size, for the projection of the plan)


# what ``tools.stage_steps`` reads of the generator's journal (a public alias is no private use of another module)
published_generator_inputs, batch_ordinal, accepted_earlier_fragments, codeword_lost_note = (
    _published_generator_inputs, _batch_ordinal, _accepted_earlier_fragments, _codeword_lost_note)

# a kind of cases -> its check (``pilot_state._validate_factual_receipt`` takes it from here); a kind not here is looked up by
# the prefix of its family, ``RECEIPT_FAMILIES``
RECEIPT_CHECKS = {
    "reference-cases": _check_reference_cases, "rework-round": _check_rework_round,
    **{kind: _check_generator_input for kind in _GENERATOR_INPUT_KINDS.values()}, "imported-cases": _check_imported_cases,
}


# public names of the same objects: what ``model_steps`` and ``tools.stage_steps`` take from here (П1 of the cut of v0.8)
RECEIPT_FAMILIES = _RECEIPT_FAMILIES
REUSED_AUTOMATION = _REUSED_AUTOMATION
STAGE_EXAMPLES = _STAGE_EXAMPLES
attempt_of = _attempt_of
automation_inputs = _automation_inputs
expected_model_inputs = _expected_model_inputs
fill_response_bindings = _fill_response_bindings
imported_for_answer = _imported_for_answer
inputs_mismatch = _inputs_mismatch
is_source_answer = _is_source_answer
label_found = _label_found
named_sections = _named_sections
named_texts = _named_texts
new_run_fix = _new_run_fix
rebound = _rebound
require_next_automation_stage = _require_next_automation_stage
requirement_order_rows = _requirement_order_rows
response_digest_rows = _response_digest_rows
reuse_source = _reuse_source
