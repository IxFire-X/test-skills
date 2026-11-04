"""FaT-10: thin JSON commands over the package functions the Skills tell the agent to call.

One command calls exactly one function and prints what it returned. A refusal of the function is the
command's refusal (code 2) with the same code and message; a validator's diagnostics are ``invalid``
(code 1) with the list unchanged. No command decides a next step, loops or launches anything.

python -m tools.api <command> [--run-root <run> --attempt <id>] ... [--out <project>/.pilot-runs/<run_id>/tmp/<file>.json]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from tools import automation_validation, batch_assembly, canonical_document, cases_import, orchestrate_test_case_revision, pilot_state, run_tests, step_size
from tools.json_cli import JsonArgumentParser, error_envelope, record_refusal
from tools.schema_validation import StrictJsonError, load_json_strict, loads_json_strict


def plain(value: Any) -> Any:
    """JSON form of a function result without changing its meaning."""
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value), "sha256": "sha256:" + hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {key: plain(item) for key, item in value.items()}
        return [{"key": plain(key), "value": plain(item)} for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def _json(path: str) -> Any:
    try:
        return load_json_strict(Path(path))
    except Exception as error:
        raise argparse.ArgumentTypeError(f"{path}: {error}") from error


@dataclasses.dataclass(frozen=True)
class _Answer:
    value: Any
    raw_bytes: int


def _answer(path: str) -> _Answer:
    """FaT-O2: a model answer file — its strict JSON and its raw size; a JSON cut in the middle is MODEL_OUTPUT_TRUNCATED."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise argparse.ArgumentTypeError(f"{path}: {error}") from error
    try:
        return _Answer(loads_json_strict(raw.decode("utf-8")), len(raw))
    except (UnicodeDecodeError, StrictJsonError) as error:
        if step_size.looks_truncated(raw.decode("utf-8", "replace")):
            raise argparse.ArgumentTypeError(step_size.truncated_message(len(raw))) from error
        raise argparse.ArgumentTypeError(f"{path}: {error}") from error


def _yes_no(value: str) -> bool:
    """FaT-10d: the host's own statement (`--fresh-context yes|no`), never derived by the code."""
    if value not in {"yes", "no"}:
        raise argparse.ArgumentTypeError(f"{value}: expected yes or no")
    return value == "yes"


# --------------------------------------------------------------------------------------
# commands: one package function each
# --------------------------------------------------------------------------------------

def _cmd_model_request(a):
    return pilot_state.publish_model_request(a.run_root, a.attempt, a.stage, model_id=a.model_id, invocation_id=a.invocation_id,
                                             input_digests=a.input_digest, reuse_from=a.reuse_from)


def _cmd_model_response(a):
    return pilot_state.publish_model_stage_artifact(a.run_root, a.attempt, a.stage, a.artifact.value, transport_attempts=a.transport_attempts,
                                                    raw_bytes=a.artifact.raw_bytes)


def _cmd_review_requested(a):
    return pilot_state.request_review(a.run_root, a.attempt, a.stage, a.digest)


def _cmd_read_model_artifact(a):
    return pilot_state.read_model_stage_artifact(a.run_root, a.attempt, a.stage, a.digest)


def _cmd_read_context(a):
    return pilot_state.read_context_selection(a.run_root, a.attempt, a.digest)


def _cmd_cases_import(a):
    return cases_import.import_requirements(a.run_root, a.attempt)


def _cmd_plan_batches(a):
    imported = a.imported if isinstance(a.imported, dict) else None  # FaT-IMP: the receipt or its value; FaT-SK: its bytes
    # FaT-AU3: one --context-receipt per context batch of the attempt; one is the receipt itself, as before
    contexts = a.context_receipt[0] if len(a.context_receipt) == 1 else a.context_receipt
    out_of_scope = _scope_answer(a)
    return batch_assembly.plan_batches_for_project(a.requirements, a.grouping, a.project, a.input_file, contexts, imported, out_of_scope)


def _scope_answer(a):
    """FaT-P3: the pause after the marker. With --run-root and --attempt the plan is bound to the human's answer: --exclude with
    --reason or --continue records it (scope-decision.json), a recorded one is used as it is, none (and gaps in the marker) is
    SCOPE_PENDING. Without them nothing is checked here; publish-generator-input checks the plan against the answer anyway."""
    from tools import scope_pause

    step_size.require_generator_context_all(a.project)  # FaT-P3: limits.generator_context: own is not implemented yet
    excluded, proceed = list(a.exclude), bool(getattr(a, "continue"))
    if a.run_root is None or a.attempt is None:
        if excluded or proceed or a.reason:
            raise scope_pause.ScopeRefused("SCOPE_ANSWER_INVALID", "--exclude, --reason и --continue — с --run-root и --attempt прогона; "
                                           "fix: добавь их")
        return None
    root = Path(a.run_root).resolve()
    if excluded or proceed:
        scope_pause.record_decision(root, a.attempt, excluded=excluded, reason=a.reason, proceed=proceed)
    scope = scope_pause.scope_state(root, a.attempt)
    if scope["pending"]:
        scope_pause.write_draft(root, a.attempt)
        raise scope_pause.ScopePending(scope_pause.pending_message(root, a.attempt, scope["gaps"]))
    return scope_pause.out_of_scope(scope["decision"]) or None


def _cmd_plan(a):
    """P2: the header, the plan of the batches and their publication in one command (one package function, ``pilot_state.publish_plan``);
    the pause after the marker answers first, before anything is published."""
    contexts = a.context_receipt[0] if len(a.context_receipt) == 1 else a.context_receipt
    imported = a.imported if isinstance(a.imported, dict) else None
    return pilot_state.publish_plan(a.run_root, a.attempt, a.header, a.requirements, a.grouping, a.project, a.input_file, contexts, imported,
                                    _scope_answer(a))


def _cmd_validate_fragment(a):
    imported = a.imported.get("imported", a.imported) if isinstance(a.imported, dict) else None  # FaT-IMP: the receipt or its value
    return batch_assembly.validate_fragment(a.fragment, a.plan, a.header, a.context_receipt, a.prior_fragment, imported)


def _cmd_prior_capabilities(a):
    return pilot_state.publish_prior_capabilities(a.run_root, a.attempt, a.batch_id)


def _cmd_assemble(a):
    return batch_assembly.assemble_candidate(a.header, a.plan, a.fragment)


def _cmd_publish_generator_input(a):
    return pilot_state.publish_generator_input(a.run_root, a.attempt, a.kind, a.value)


def _cmd_document_digest(a):
    return canonical_document.document_sha256(a.document)


def _cmd_validate_canonical(a):
    return canonical_document.validate_canonical_document(a.document)


def _review_files(a):
    """FaT-U5: the reviewer-package files the agent passed — compared with the journal, never the source."""
    return {"candidate": a.candidate, "candidate_receipt": getattr(a, "candidate_receipt", None), "assembly_receipt": getattr(a, "assembly_receipt", None),
            "inventory_receipt": getattr(a, "inventory_receipt", None), "context_receipts": getattr(a, "context_receipt", None),
            "context_marker_output": getattr(a, "context_marker_output", None), "generator_fragments": getattr(a, "generator_fragment", None)}


def _cmd_session_start(a):
    return orchestrate_test_case_revision.review_step("session-start", a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir,
                                                      csv_profile=a.csv_profile, fresh_context=a.fresh_context,
                                                      reviewer_invocation_id=a.reviewer_invocation_id)


def _cmd_review_publish(a):
    return orchestrate_test_case_revision.review_step("publish", a.run_root, a.attempt, files={"candidate": a.candidate},
                                                      output_dir=a.output_dir, csv_profile=a.csv_profile)


def _cmd_review_open(a):
    """P2: without ``--session-start`` the whole opening of the review (publish, session start, boundary, request); with it the
    FaT-U5 command of the nine, as before — one package function either way."""
    return orchestrate_test_case_revision.review_open_step(
        a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir, csv_profile=a.csv_profile, model_id=a.model_id,
        invocation_id=a.invocation_id, fresh_context=a.fresh_context, session_start=a.session_start)


def _cmd_review_select(a):
    """P2: ``--verdict`` records the answer and selects; ``--reviewer-session`` (the nine) is optional — the last revision of the
    ledger when it is not given; ``--dry-run`` writes nothing."""
    verdict = a.verdict
    return orchestrate_test_case_revision.review_select_step(
        a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir, csv_profile=a.csv_profile,
        verdict=None if verdict is None else verdict.value, verdict_bytes=None if verdict is None else verdict.raw_bytes,
        transport_attempts=a.transport_attempts, reviewer_session=a.reviewer_session, review=a.review, dry_run=a.dry_run)


def _cmd_complete_review(a):
    return orchestrate_test_case_revision.complete_reviewer_session(a.run_root, a.attempt)


def _cmd_review_evidence(a):
    return orchestrate_test_case_revision.provide_reviewer_evidence(a.run_root, a.attempt, a.path)


def _cmd_review_abort(a):
    return orchestrate_test_case_revision.abort_reviewer_session(a.run_root, a.attempt, a.reason)


def _cmd_read_ledger(a):
    return pilot_state.read_reviewer_session_ledger(a.run_root, a.attempt, a.digest)


def _cmd_read_effective(a):
    return pilot_state.read_effective_canonical(a.run_root, a.attempt)


def _cmd_open_automation_boundary(a):
    return pilot_state.open_automation_review_boundary(a.run_root, a.attempt, a.facts)


def _cmd_automation_boundary_facts(a):
    return pilot_state.automation_boundary_facts(a.run_root, a.attempt, a.revision, fresh_context=a.fresh_context,
                                                 reviewer_invocation_id=a.reviewer_invocation_id)


def _cmd_read_receipt(a):
    return pilot_state.read_attempt_receipt(a.run_root, a.attempt, a.kind, a.event)


def _cmd_automation_digest(a):
    return automation_validation.automation_sha256(a.automation)


def _cmd_relations_digest(a):
    return automation_validation.implementation_relations_sha256(a.relations)


def _cmd_validate_automation(a):
    return automation_validation.validate_automation_artifact(a.automation, a.canonical)


def _cmd_runner_compat(a):
    return run_tests.validate_artifact_runner_compatibility(a.module_root, a.language, a.canonical, a.automation, materialized=a.materialized)


def _cmd_step_inputs(a):
    return step_size.request_files(a.run_root, a.stage)


RUN = ("--run-root", "--attempt")
# FaT-U5: the reviewer-package files — optional, compared with the journal when given
REVIEW_FILES = (("--candidate", _json, None), ("--candidate-receipt", Path, None), ("--assembly-receipt", _json, None),
                ("--inventory-receipt", _json, None), ("--context-receipt", _json, "append", "optional"),
                ("--context-marker-output", _json, None), ("--generator-fragment", _json, "append", "optional"))
PROFILE = orchestrate_test_case_revision._PROFILE


def _diagnostics(result: Any) -> bool:
    return bool(result)


def _not_runnable(result: Any) -> bool:
    return getattr(result, "status", None) != "READY"


# name: (handler, options, invalid-when, named parts of a tuple result)
COMMANDS: dict[str, tuple[Callable, tuple, Callable | None, tuple[str, ...]]] = {
    "model-request": (_cmd_model_request, (*RUN, "--stage", "--model-id", "--invocation-id", ("--input-digest", str, "append", "optional"), ("--reuse-from", str, None)), None, ()),
    "model-response": (_cmd_model_response, (*RUN, "--stage", ("--artifact", _answer), ("--transport-attempts", int, 1)), None, ()),
    "review-requested": (_cmd_review_requested, (*RUN, "--stage", "--digest"), None, ()),
    "read-model-artifact": (_cmd_read_model_artifact, (*RUN, "--stage", "--digest"), None, ()),
    "read-context": (_cmd_read_context, (*RUN, "--digest"), None, ()),
    "cases-import": (_cmd_cases_import, RUN, None, ()),
    "plan-batches": (_cmd_plan_batches, (("--requirements", _json), ("--grouping", _json), ("--project", Path),
                                         ("--input-file", Path, "append"), ("--context-receipt", _json, "append"), ("--imported", _json, None),
                                         ("--run-root", Path, None), ("--attempt", str, None), ("--exclude", str, "append", "optional"),
                                         ("--reason", str, None), ("--continue", "flag")), None, ()),
    # P2: header + plan-batches + publish of both in one command; --out-plan is the plan (the file validate-fragment and assemble read)
    "plan": (_cmd_plan, (("--header", _json), ("--requirements", _json), ("--grouping", _json), ("--project", Path),
                         ("--input-file", Path, "append"), ("--context-receipt", _json, "append"), ("--imported", _json, None),
                         "--run-root", "--attempt", ("--exclude", str, "append", "optional"), ("--reason", str, None),
                         ("--continue", "flag")), None, ("plan", "published")),
    "validate-fragment": (_cmd_validate_fragment, (("--fragment", _json), ("--plan", _json), ("--header", _json), ("--context-receipt", _json),
                                                   ("--prior-fragment", _json, "append", "optional"), ("--imported", _json, None)), None, ()),
    "prior-capabilities": (_cmd_prior_capabilities, (*RUN, "--batch-id"), None, ()),
    "assemble": (_cmd_assemble, (("--header", _json), ("--plan", _json), ("--fragment", _json, "append")), None, ("candidate", "receipt")),
    "publish-generator-input": (_cmd_publish_generator_input, (*RUN, "--kind", ("--value", _json)), None, ()),
    "document-digest": (_cmd_document_digest, (("--document", _json),), None, ()),
    "validate-canonical": (_cmd_validate_canonical, (("--document", _json),), _diagnostics, ()),
    "read-ledger": (_cmd_read_ledger, (*RUN, ("--digest", str, None)), None, ()),
    "session-start": (_cmd_session_start, (*RUN, *REVIEW_FILES, ("--output-dir", Path, None), ("--csv-profile", str, PROFILE),
                                           ("--fresh-context", _yes_no), "--reviewer-invocation-id"), None, ()),
    # FaT-U5: the review layer in one CLI; the seven inputs come from the journal, a file passed is only compared
    "review-publish": (_cmd_review_publish, (*RUN, ("--candidate", _json, None), ("--output-dir", Path),
                                             ("--csv-profile", str, PROFILE)), None, ()),
    # P2: review-open [--model-id --invocation-id --fresh-context] opens the whole review; review-select [--verdict] [--dry-run] closes it
    "review-open": (_cmd_review_open, (*RUN, *REVIEW_FILES, ("--output-dir", Path), ("--csv-profile", str, PROFILE),
                                       ("--session-start", _json, None), ("--model-id", str, None), ("--invocation-id", str, None),
                                       ("--fresh-context", _yes_no, None)), None, ()),
    "review-select": (_cmd_review_select, (*RUN, *REVIEW_FILES, ("--output-dir", Path), ("--csv-profile", str, PROFILE),
                                           ("--reviewer-session", _json, None), ("--review", _json, None), ("--verdict", _answer, None),
                                           ("--transport-attempts", int, 1), ("--dry-run", "flag")), None, ()),
    "complete-review": (_cmd_complete_review, RUN, None, ()),
    "review-evidence": (_cmd_review_evidence, (*RUN, ("--path", "append")), None, ()),
    "review-abort": (_cmd_review_abort, (*RUN, "--reason"), None, ()),
    "read-effective": (_cmd_read_effective, RUN, None, ()),
    "automation-boundary-facts": (_cmd_automation_boundary_facts, (*RUN, ("--revision", int), ("--fresh-context", _yes_no),
                                                                     "--reviewer-invocation-id"), None, ()),
    "open-automation-boundary": (_cmd_open_automation_boundary, (*RUN, ("--facts", _json)), None, ()),
    "read-receipt": (_cmd_read_receipt, (*RUN, "--kind", "--event"), None, ()),
    "automation-digest": (_cmd_automation_digest, (("--automation", _json),), None, ()),
    "relations-digest": (_cmd_relations_digest, (("--relations", _json),), None, ()),
    "validate-automation": (_cmd_validate_automation, (("--automation", _json), ("--canonical", _json)), _diagnostics, ()),
    "runner-compat": (_cmd_runner_compat, (("--module-root", Path), ("--language", str), ("--canonical", _json), ("--automation", _json),
                                           ("--materialized", "flag")), _not_runnable, ()),
    "step-inputs": (_cmd_step_inputs, ("--run-root", "--stage"), None, ()),
}


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="python -m tools.api", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, options, _invalid, parts) in COMMANDS.items():
        command = sub.add_parser(name)
        for option in options:
            flag, *rest = option if isinstance(option, tuple) else (option,)
            dest = flag.removeprefix("--").replace("-", "_")
            if rest == ["flag"]:
                command.add_argument(flag, dest=dest, action="store_true")
            elif rest == ["append"]:
                command.add_argument(flag, dest=dest, action="append", required=True)
            elif rest[1:] == ["append", "optional"]:
                command.add_argument(flag, dest=dest, type=rest[0], action="append", default=[])
            elif len(rest) == 2 and rest[1] == "append":
                command.add_argument(flag, dest=dest, type=rest[0], action="append", required=True)
            elif len(rest) == 2:
                command.add_argument(flag, dest=dest, type=rest[0], default=rest[1])
            else:
                command.add_argument(flag, dest=dest, type=(rest[0] if rest else (Path if flag == "--run-root" else str)), required=True)
        if parts:
            for part in parts:
                command.add_argument(f"--out-{part}", dest=f"out_{part}", required=True)
        else:
            command.add_argument("--out", default=None)
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class _OutsideRun(ValueError):
    code = "API_OUT_OUTSIDE_RUN"


def _out_target(path: str) -> Path:
    target = Path(path).resolve()
    parts = target.parts
    if ".pilot-runs" not in parts:
        raise _OutsideRun("--out must name a file under <project>/.pilot-runs/<run_id>/tmp/")
    index = parts.index(".pilot-runs")
    if len(parts) < index + 4 or parts[index + 2] != "tmp":
        raise _OutsideRun("--out must name a file under <project>/.pilot-runs/<run_id>/tmp/")
    return target


def _write(target: Path, value: Any) -> dict[str, Any]:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return {"out": str(target), "bytes": len(data), "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def _record_refusal(args: argparse.Namespace, envelope: Mapping[str, Any]) -> None:
    rows = envelope.get("diagnostics")
    if envelope.get("code") == "SCOPE_PENDING":  # the pause of the human is routine: the report shows it, the refusals do not count it
        return
    code = envelope.get("code") or (rows[0].get("code") if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else None)
    record_refusal(getattr(args, "run_root", None), args.command, code, getattr(args, "stage", None), getattr(args, "batch_id", None))


# P2: the review in two commands; the nine of before work one more release and say so
_REVIEW_OPEN_NOW = "tools.api review-open --run-root <run> --attempt <id> --output-dir <dir> --model-id <модель> --invocation-id <id ревьюера> --fresh-context yes|no"
_REVIEW_SELECT_NOW = "tools.api review-select --verdict <файл ответа ревьюера> --run-root <run> --attempt <id> --output-dir <dir>"


def _deprecated(args: argparse.Namespace) -> str | None:
    """P2: ``deprecated: "tools.api review-open …"`` for a command of the nine of the canonical review, the one that replaces it."""
    stage = getattr(args, "stage", None)
    reviewer = stage == orchestrate_test_case_revision.REVIEW_STAGE
    if args.command == "session-start" or (args.command == "review-open" and args.session_start is not None) or (
            args.command in {"review-requested", "model-request"} and reviewer):
        return _REVIEW_OPEN_NOW
    if args.command == "complete-review" or (args.command == "model-response" and reviewer) or (
            args.command == "review-select" and args.reviewer_session is not None):
        return _REVIEW_SELECT_NOW
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handler, _options, invalid, parts = COMMANDS[args.command]
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        targets = {part: _out_target(getattr(args, f"out_{part}")) for part in parts}
        out = _out_target(args.out) if not parts and args.out is not None else None
        result = handler(args)
    except Exception as error:
        # FaT-U6: the refusal's own code and fix; FaT-U5: the one envelope {status, code, message, fix, next}
        envelope = {**error_envelope(error), "command": args.command,
                    **({"diagnostics": [dict(row) for row in error.diagnostics]} if hasattr(error, "diagnostics") else {})}
        _print(envelope)
        if not dry_run:  # a dry run leaves no file behind, a refusal line included
            _record_refusal(args, envelope)  # P1: <run>/refusals.jsonl, when the command names its run
        return 2
    status = "invalid" if invalid is not None and invalid(result) else "ok"
    payload: dict[str, Any] = {"status": status, "command": args.command}
    if dry_run:
        payload["dry_run"] = True
    deprecated = _deprecated(args)
    if deprecated is not None:
        payload["deprecated"] = deprecated
    if getattr(result, "notes", None):  # FaT-BO: plan-batches says how and why it split
        payload["notes"] = list(result.notes)
    if parts:
        payload["out"] = {part: _write(targets[part], plain(value)) for part, value in zip(parts, result, strict=True)}
    elif out is not None:
        payload.update(_write(out, plain(result)))
    else:
        payload["result"] = plain(result)
    _print(payload)
    if status == "invalid":
        _record_refusal(args, payload)
    return 1 if status == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
