"""FaT-10: thin JSON commands over the package functions the Skills tell the agent to call.

One command calls exactly one function and prints what it returned. A refusal of the function is the
command's refusal (code 2) with the same code and message; a validator's diagnostics are ``invalid``
(code 1) with the list unchanged. No command decides a next step, loops or launches anything.

python -m tools.api prepare|submit --run-root <run> --stage <id> ...
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

from tools import canonical_document, output_guard, stage_steps, step_size
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
    """Every JSON input file of the command (``--document``), read only after ``output_guard.input_file`` took its path."""
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
        if isinstance(value, _JsonFile):
            setattr(args, name, read("--" + name.replace("_", "-"), value))


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

def _cmd_prepare(a):
    return stage_steps.prepare(a.run_root, a.stage, model_id=a.model_id, invocation_id=a.invocation_id, reuse_from=a.reuse_from, fresh_context=a.fresh_context,
                               accept_self_review=a.accept_self_review)


def _cmd_submit(a):
    return stage_steps.submit(a.run_root, a.stage, _load_answer, dry_run=a.dry_run, transport_attempts=a.transport_attempts, abort=a.abort)


def _cmd_validate_canonical(a):
    from tools.revision_selection import candidate_rows

    return candidate_rows(a.document) or canonical_document.validate_canonical_document(a.document)


def _diagnostics(result: Any) -> bool:
    return bool(result)


# name: (handler, options, invalid-when)
COMMANDS: dict[str, tuple[Callable, tuple, Callable | None]] = {
    "prepare": (_cmd_prepare, ("--run-root", "--stage", ("--model-id", str, None), ("--invocation-id", str, None),
                                                 ("--reuse-from", str, None), ("--fresh-context", _yes_no, None), ("--accept-self-review", "flag")), None),
    "submit": (_cmd_submit, ("--run-root", "--stage", ("--transport-attempts", int, 1), ("--dry-run", "flag"), ("--abort", str, None)), None),
    "validate-canonical": (_cmd_validate_canonical, (("--document", _json),), _diagnostics),
}


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
        if not any(action.dest == "run_root" for action in command._actions):
            # a command that has no run of its own takes the run as the anchor of its input file and the place of its refusals
            command.add_argument("--run-root", dest="run_root", type=Path, default=None, help="the run: the anchor of the input file; the refusals of the command are journaled there")
    return parser


def _print(payload: Mapping[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _record_refusal(args: argparse.Namespace, envelope: Mapping[str, Any]) -> None:
    rows = envelope.get("diagnostics")
    if envelope.get("code") == "SCOPE_PENDING":  # the pause of the human is routine: the report shows it, the refusals do not count it
        return
    code = envelope.get("code") or (rows[0].get("code") if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else None)
    record_refusal(getattr(args, "run_root", None), args.command, code, getattr(args, "stage", None))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handler, _options, invalid = COMMANDS[args.command]
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        _read_inputs(args)
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
    payload["result"] = plain(result)
    _print(payload)
    if status == "invalid":
        _record_refusal(args, payload)
    return 1 if status == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
