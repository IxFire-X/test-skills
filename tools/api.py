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
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from tools import canonical_document, confined_output, execution_gate, orchestrate_test_case_revision, output_guard, pilot_state, stage_steps, step_size
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


@dataclasses.dataclass(frozen=True)
class _JsonFile:
    path: str

    def __fspath__(self) -> str:  # a path to the parser: a share is refused while the arguments are read
        return self.path


def _json(path: str) -> _JsonFile:
    """A JSON input: read in ``main``, once its path is known to be inside the project (``_read_inputs``)."""
    return _JsonFile(path)


def _input_project(args: argparse.Namespace) -> Path | None:
    """The project an input file must be in — strictly the anchor: the project of ``--run-root`` (a path that is no run is refused);
    None — no anchor at all, the file lies in a run (``output_guard.input_file``)."""
    run_root = getattr(args, "run_root", None)
    return output_guard.existing_run(run_root)[0] if run_root is not None else None


def _read_inputs(args: argparse.Namespace) -> None:
    """Every JSON input and answer file of the command, each read only after ``output_guard.input_file`` took its path."""
    anchor: list[Path | None] = []

    def project() -> Path | None:
        if not anchor:
            anchor.append(_input_project(args))
        return anchor[0]

    def read(flag: str, value: _JsonFile) -> Any:
        root = project()  # a --run-root that is no run is refused as it is
        try:
            return load_json_strict(output_guard.input_file(value.path, root))
        except output_guard.OutputRefused:
            raise
        except Exception as error:
            raise ValueError(f"INVALID_ARGUMENT: argument {flag}: {value.path}: {error}; fix: {flag} — путь к JSON-файлу на диске") from error

    for name, value in list(vars(args).items()):
        flag = "--" + name.replace("_", "-")
        if isinstance(value, _JsonFile):
            setattr(args, name, read(flag, value))
        elif isinstance(value, list) and any(isinstance(item, _JsonFile) for item in value):
            setattr(args, name, [read(flag, item) if isinstance(item, _JsonFile) else item for item in value])
    for name in ("artifact", "verdict"):  # the answer file: its refusal has its code and fix and a line in refusals.jsonl
        if isinstance(getattr(args, name, None), str):
            setattr(args, name, _load_answer(getattr(args, name), project()))


@dataclasses.dataclass(frozen=True)
class _Answer:
    value: Any
    raw_bytes: int


def _load_answer(path: str, project: Path | None = None) -> _Answer:
    """A model answer file — its strict JSON and its raw size. Read in ``main`` and refused with the code of the
    refusal and its fix (a JSON cut in the middle is MODEL_OUTPUT_TRUNCATED), not by argparse as «check the flags»; the
    path inside the project first (``output_guard.input_file``)."""
    try:
        raw = output_guard.input_file(path, project).read_bytes()
    except OSError as error:
        raise ValueError(f"ANSWER_FILE_UNREADABLE: {path}: {error.strerror or error}; fix: ответ модели — файл в слоте стадии (answer_slot из prepare или status) "
                         "или путь к файлу ответа на диске; запиши ответ в файл и повтори") from error
    try:
        return _Answer(loads_json_strict(raw.decode("utf-8")), len(raw))
    except (UnicodeDecodeError, StrictJsonError) as error:
        if step_size.looks_truncated(raw.decode("utf-8", "replace")):
            raise ValueError(step_size.truncated_message(len(raw)) + "; fix: " + step_size.TRUNCATED_FIX) from error
        raise ValueError(f"ANSWER_JSON_INVALID: {path}: {error}; fix: файл ответа — один JSON-объект в UTF-8 без комментариев и дублей ключей; "
                         "исправь файл и подай его единственным повтором стадии (--transport-attempts 2)") from error


def _yes_no(value: str) -> bool:
    """The host's own statement (`--fresh-context yes|no`), never derived by the code."""
    if value not in {"yes", "no"}:
        raise argparse.ArgumentTypeError(f"{value}: expected yes or no")
    return value == "yes"


# --------------------------------------------------------------------------------------
# commands: one package function each
# --------------------------------------------------------------------------------------

def _cmd_model_request(a):
    return pilot_state.publish_model_request(a.run_root, a.attempt, a.stage, model_id=a.model_id, invocation_id=a.invocation_id,
                                             input_digests=a.input_digest)


def _cmd_model_response(a):
    published = pilot_state.publish_model_stage_artifact(a.run_root, a.attempt, a.stage, a.artifact.value, transport_attempts=a.transport_attempts,
                                                         raw_bytes=a.artifact.raw_bytes)
    # PF-1: the receipt of the record (digest, path, size, filled, normalized, warning), not the answer's body — hundreds of KB the agent
    # already has; the body is read apart with read-model-artifact
    return {key: value for key, value in published.items() if key != "artifact"}


def _cmd_review_requested(a):
    return pilot_state.request_review(a.run_root, a.attempt, a.stage, a.digest)


def _cmd_read_model_artifact(a):
    return pilot_state.read_model_stage_artifact(a.run_root, a.attempt, a.stage, a.digest)


def _cmd_prepare(a):
    return stage_steps.prepare(a.run_root, a.stage, model_id=a.model_id, invocation_id=a.invocation_id, reuse_from=a.reuse_from, fresh_context=a.fresh_context)


def _cmd_submit(a):
    return stage_steps.submit(a.run_root, a.stage, _load_answer, dry_run=a.dry_run, transport_attempts=a.transport_attempts, abort=a.abort)


def _cmd_validate_canonical(a):
    from tools.revision_selection import candidate_rows

    return candidate_rows(a.document) or canonical_document.validate_canonical_document(a.document)


def _review_files(a):
    """The reviewer-package files the agent passed — compared with the journal, never the source."""
    return {"candidate": getattr(a, "candidate", None), "candidate_receipt": getattr(a, "candidate_receipt", None), "assembly_receipt": getattr(a, "assembly_receipt", None),
            "inventory_receipt": getattr(a, "inventory_receipt", None), "context_receipts": getattr(a, "context_receipt", None),
            "context_marker_output": getattr(a, "context_marker_output", None), "generator_fragments": getattr(a, "generator_fragment", None)}


def _cmd_session_start(a):
    return orchestrate_test_case_revision.review_step("session-start", a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir,
                                                      csv_profile=a.csv_profile, fresh_context=a.fresh_context,
                                                      reviewer_invocation_id=a.reviewer_invocation_id)


def _cmd_review_publish(a):
    return orchestrate_test_case_revision.review_step("publish", a.run_root, a.attempt, files={"candidate": getattr(a, "candidate", None)},
                                                      output_dir=a.output_dir, csv_profile=getattr(a, "csv_profile", PROFILE))


def _refuse_legacy_flags(a, *names: str) -> None:
    """A flag of the nine commands where their old CLI is not — a refusal with the way in, not a failure deep in the old path."""
    if execution_gate.legacy_available():
        return
    given = [f"--{name.replace('_', '-')}" for name in names if getattr(a, name, None) is not None]
    if given:
        raise ValueError(f"LEGACY_REVIEW_NOT_IN_PACKAGE: {', '.join(given)} — флаг прежнего порядка ревью, которого в этой поставке нет; "
                         "fix: ревью — две команды: tools.api review-open --model-id … --invocation-id … --fresh-context yes|no → вызов ревьюера → "
                         "tools.api review-select --verdict <файл ответа ревьюера>")


def _refuse_canonical_review_request(a) -> None:
    """MB1: where mode B keeps ``review-requested`` but the old CLI of the review is not, the command is for the review of the automation
    only (``autotest-reviewer:rN``); the review of the cases has one way in."""
    if a.stage == orchestrate_test_case_revision.REVIEW_STAGE and not execution_gate.legacy_available():
        raise ValueError(f"LEGACY_REVIEW_NOT_IN_PACKAGE: {a.stage} — в этой поставке review-requested только для autotest-reviewer:rN; "
                         "fix: ревью кейсов — две команды: tools.api review-open --model-id … --invocation-id … --fresh-context yes|no → "
                         "вызов ревьюера → tools.api review-select --verdict <файл ответа ревьюера>")


def _cmd_review_open(a):
    """Without ``--session-start`` the whole opening of the review (publish, session start, boundary, request); with it the
    command of the nine, as before — one package function either way."""
    return orchestrate_test_case_revision.review_open_step(
        a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir, csv_profile=getattr(a, "csv_profile", PROFILE), model_id=a.model_id,
        invocation_id=a.invocation_id, fresh_context=a.fresh_context, session_start=getattr(a, "session_start", None))


def _cmd_review_select(a):
    """``--verdict`` records the answer and selects; ``--reviewer-session`` (the nine) is optional — the last revision of the
    ledger when it is not given; ``--dry-run`` writes nothing."""
    verdict = a.verdict
    return orchestrate_test_case_revision.review_select_step(
        a.run_root, a.attempt, files=_review_files(a), output_dir=a.output_dir, csv_profile=getattr(a, "csv_profile", PROFILE),
        verdict=None if verdict is None else verdict.value, verdict_bytes=None if verdict is None else verdict.raw_bytes,
        transport_attempts=a.transport_attempts, reviewer_session=getattr(a, "reviewer_session", None), review=getattr(a, "review", None),
        dry_run=a.dry_run)


def _cmd_complete_review(a):
    return orchestrate_test_case_revision.complete_reviewer_session(a.run_root, a.attempt)


def _cmd_review_evidence(a):
    return orchestrate_test_case_revision.provide_reviewer_evidence(a.run_root, a.attempt, a.path)


def _cmd_review_abort(a):
    return orchestrate_test_case_revision.abort_reviewer_session(a.run_root, a.attempt, a.reason)


def _cmd_read_ledger(a):
    return pilot_state.read_reviewer_session_ledger(a.run_root, a.attempt, a.digest, unconfirmed_ok=True)


def _cmd_read_effective(a):
    """PT2: before the verdict there is no effective document — that is an answer (``effective: null``), not a refusal that the
    journal of refusals counts; a run or an attempt that does not exist is still a refusal."""
    return pilot_state.effective_canonical_or_reason(a.run_root, a.attempt)


def _cmd_open_automation_boundary(a):
    return pilot_state.open_automation_review_boundary(a.run_root, a.attempt, a.facts)


def _cmd_automation_boundary_facts(a):
    return pilot_state.automation_boundary_facts(a.run_root, a.attempt, a.revision, fresh_context=a.fresh_context,
                                                 reviewer_invocation_id=a.reviewer_invocation_id)


def _cmd_read_receipt(a):
    return pilot_state.attempt_receipt_or_reason(a.run_root, a.attempt, a.kind, a.event)  # «not yet» is decided by the journal


def _cmd_automation_digest(a):
    return execution_gate.load_mode_b("automation_validation").automation_sha256(a.automation)


def _cmd_relations_digest(a):
    return execution_gate.load_mode_b("automation_validation").implementation_relations_sha256(a.relations)


def _cmd_validate_automation(a):
    return execution_gate.load_mode_b("automation_validation").validate_automation_artifact(a.automation, a.canonical)


def _cmd_runner_compat(a):
    return execution_gate.load_mode_b("run_tests").validate_artifact_runner_compatibility(a.module_root, a.language, a.canonical, a.automation, materialized=a.materialized)


def _cmd_step_inputs(a):
    """PT2: the summary and the path of the file with the whole list — a generator batch reads a hundred files."""
    return step_size.step_inputs(a.run_root, a.stage)


RUN = ("--run-root", "--attempt")
# the reviewer-package files — optional, compared with the journal when given
REVIEW_FILES = (("--candidate", _json, None), ("--candidate-receipt", Path, None), ("--assembly-receipt", _json, None),
                ("--inventory-receipt", _json, None), ("--context-receipt", _json, "append", "optional"),
                ("--context-marker-output", _json, None), ("--generator-fragment", _json, "append", "optional"))
PROFILE = orchestrate_test_case_revision._PROFILE


def _diagnostics(result: Any) -> bool:
    return bool(result)


def _not_runnable(result: Any) -> bool:
    return getattr(result, "status", None) != "READY"


# name: (handler, options, invalid-when)
COMMANDS: dict[str, tuple[Callable, tuple, Callable | None]] = {
    "model-request": (_cmd_model_request, (*RUN, "--stage", "--model-id", "--invocation-id", ("--input-digest", str, "append", "optional")), None),
    "model-response": (_cmd_model_response, (*RUN, "--stage", "--artifact", ("--transport-attempts", int, 1)), None),
    "review-requested": (_cmd_review_requested, (*RUN, "--stage", "--digest"), None),
    "read-model-artifact": (_cmd_read_model_artifact, (*RUN, "--stage", "--digest"), None),
    "prepare": (_cmd_prepare, ("--run-root", "--stage", ("--model-id", str, None), ("--invocation-id", str, None),
                                                 ("--reuse-from", str, None), ("--fresh-context", _yes_no, None)), None),
    "submit": (_cmd_submit, ("--run-root", "--stage", ("--transport-attempts", int, 1), ("--dry-run", "flag"), ("--abort", str, None)), None),
    "validate-canonical": (_cmd_validate_canonical, (("--document", _json),), _diagnostics),
    "read-ledger": (_cmd_read_ledger, (*RUN, ("--digest", str, None)), None),
    "session-start": (_cmd_session_start, (*RUN, *REVIEW_FILES, ("--output-dir", Path, None), ("--csv-profile", str, PROFILE),
                                           ("--fresh-context", _yes_no), "--reviewer-invocation-id"), None),
    # the review layer in one CLI; the seven inputs come from the journal, a file passed is only compared
    "review-publish": (_cmd_review_publish, (*RUN, ("--candidate", _json, None), ("--output-dir", Path),
                                             ("--csv-profile", str, PROFILE)), None),
    # review-open [--model-id --invocation-id --fresh-context] opens the whole review; review-select [--verdict] [--dry-run] closes it
    "review-open": (_cmd_review_open, (*RUN, *REVIEW_FILES, ("--output-dir", Path), ("--csv-profile", str, PROFILE),
                                       ("--session-start", _json, None), ("--model-id", str, None), ("--invocation-id", str, None),
                                       ("--fresh-context", _yes_no, None)), None),
    "review-select": (_cmd_review_select, (*RUN, *REVIEW_FILES, ("--output-dir", Path), ("--csv-profile", str, PROFILE),
                                           ("--reviewer-session", _json, None), ("--review", _json, None), ("--verdict", str, None),
                                           ("--transport-attempts", int, 1), ("--dry-run", "flag")), None),
    "complete-review": (_cmd_complete_review, RUN, None),
    "review-evidence": (_cmd_review_evidence, (*RUN, ("--path", "append")), None),
    "review-abort": (_cmd_review_abort, (*RUN, "--reason"), None),
    "read-effective": (_cmd_read_effective, RUN, None),
    "automation-boundary-facts": (_cmd_automation_boundary_facts, (*RUN, ("--revision", int), ("--fresh-context", _yes_no),
                                                                     "--reviewer-invocation-id"), None),
    "open-automation-boundary": (_cmd_open_automation_boundary, (*RUN, ("--facts", _json)), None),
    "read-receipt": (_cmd_read_receipt, (*RUN, "--kind", "--event"), None),
    "automation-digest": (_cmd_automation_digest, (("--automation", _json),), None),
    "relations-digest": (_cmd_relations_digest, (("--relations", _json),), None),
    "validate-automation": (_cmd_validate_automation, (("--automation", _json), ("--canonical", _json)), _diagnostics),
    "runner-compat": (_cmd_runner_compat, (("--module-root", Path), ("--language", str), ("--canonical", _json), ("--automation", _json),
                                           ("--materialized", "flag")), _not_runnable),
    "step-inputs": (_cmd_step_inputs, ("--run-root", "--stage"), None),
}


# PT2: the commands of mode B (the automation of tests) exist only where the execution files are — the seam of the profile
_AUTOMATION_COMMANDS = ("automation-boundary-facts", "open-automation-boundary", "automation-digest", "relations-digest", "validate-automation",
                        "runner-compat")
if not execution_gate.execution_available():
    for _name in _AUTOMATION_COMMANDS:
        COMMANDS.pop(_name, None)
# the review of a delivery of cases has one way in — review-open, review-select; the three commands of the nine stay where the old CLI is.
# MB1: review-requested also stays where mode B is (its text asks for it on autotest-reviewer:rN); there it refuses the canonical review
_LEGACY_REVIEW_COMMANDS = ("session-start", "complete-review") + (() if execution_gate.execution_available() else ("review-requested",))
if not execution_gate.legacy_available():
    for _name in _LEGACY_REVIEW_COMMANDS:
        COMMANDS.pop(_name, None)
    # nor the flags of that way in on the commands that stay — package files the code takes from the journal, and the profile of the nine;
    # `--session-start`, `--reviewer-session` and `--review` stay: a text of before names them, and they are refused with the way in
    _LEGACY_REVIEW_FLAGS = frozenset(("--candidate", "--candidate-receipt", "--assembly-receipt", "--inventory-receipt", "--context-receipt",
                                      "--context-marker-output", "--generator-fragment", "--csv-profile"))
    for _name in ("review-publish", "review-open", "review-select"):
        _function, _specs, _check = COMMANDS[_name]
        COMMANDS[_name] = (_function, tuple(spec for spec in _specs if (spec if isinstance(spec, str) else spec[0]) not in _LEGACY_REVIEW_FLAGS), _check)


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(prog="python -m tools.api", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_handler, options, _invalid) in COMMANDS.items():
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
        command.add_argument("--out", default=None)
        if not any(action.dest == "run_root" for action in command._actions):
            # an --out file is confined to <run>/tmp/: a command that has no run of its own takes the run for that check only
            command.add_argument("--run-root", dest="run_root", type=Path, default=None, help="the run: --out writes into its tmp/; the refusals of the command are journaled there")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _out_target(path: str, run_root: object = None) -> Path:
    """The one ``--out`` policy (tools.output_guard): a file directly in ``<run>/tmp/`` of the run ``--run-root`` names."""
    return output_guard.out_file(path, run_root)


def _write(target: Path, value: Any, run_root: object) -> dict[str, Any]:
    """``--out``: the file in ``<run>/tmp/`` written through confined_output (a temporary of its own, no link followed)."""
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    project, run = output_guard.existing_run(run_root)
    confined_output.atomic_write_confined_bytes_at_root(project, run, target, data)
    return {"out": str(target), "bytes": len(data), "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def _record_refusal(args: argparse.Namespace, envelope: Mapping[str, Any]) -> None:
    rows = envelope.get("diagnostics")
    if envelope.get("code") == "SCOPE_PENDING":  # the pause of the human is routine: the report shows it, the refusals do not count it
        return
    code = envelope.get("code") or (rows[0].get("code") if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else None)
    record_refusal(getattr(args, "run_root", None), args.command, code, getattr(args, "stage", None))


# the review in two commands; the nine of before work one more release and say so
_REVIEW_OPEN_NOW = "tools.api review-open --run-root <run> --attempt <id> --output-dir <dir> --model-id <модель> --invocation-id <id ревьюера> --fresh-context yes|no"
_REVIEW_SELECT_NOW = "tools.api review-select --verdict <файл ответа ревьюера> --run-root <run> --attempt <id> --output-dir <dir>"


def _deprecated(args: argparse.Namespace) -> str | None:
    """``deprecated: "tools.api review-open …"`` for a command of the nine of the canonical review, the one that replaces it."""
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
    handler, _options, invalid = COMMANDS[args.command]
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        if args.command == "review-open":  # before the guards below — the flag is refused for what it is
            _refuse_legacy_flags(args, "session_start")
        elif args.command == "review-select":
            _refuse_legacy_flags(args, "reviewer_session", "review")
        elif args.command == "review-requested":
            _refuse_canonical_review_request(args)
        _read_inputs(args)
        run_root = getattr(args, "run_root", None)
        out = _out_target(args.out, run_root) if args.out is not None else None
        if getattr(args, "output_dir", None) is not None:  # the publication directory: inside the project of the run, never in .git
            output_guard.run_output_directory(args.output_dir, run_root)
        result = handler(args)
    except Exception as error:
        # the refusal's own code and fix; the one envelope {status, code, message, fix, next}
        envelope = {**error_envelope(error), "command": args.command,
                    **({"diagnostics": [dict(row) for row in error.diagnostics]} if hasattr(error, "diagnostics") else {})}
        _print(envelope)
        if not dry_run:  # a dry run leaves no file behind, a refusal line included
            _record_refusal(args, envelope)  # <run>/refusals.jsonl, when the command names its run
        return 2
    status = "invalid" if invalid is not None and invalid(result) else "ok"
    payload: dict[str, Any] = {"status": status, "command": args.command}
    if dry_run:
        payload["dry_run"] = True
    deprecated = _deprecated(args)
    if deprecated is not None:
        payload["deprecated"] = deprecated
    if out is not None:
        payload.update(_write(out, plain(result), run_root))
    else:
        payload["result"] = plain(result)
    _print(payload)
    if status == "invalid":
        _record_refusal(args, payload)
    return 1 if status == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
