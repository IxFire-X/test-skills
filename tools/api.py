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

from tools import automation_validation, batch_assembly, canonical_document, pilot_state, run_tests
from tools.json_cli import JsonArgumentParser
from tools.schema_validation import load_json_strict


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


def _bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise argparse.ArgumentTypeError(f"{path}: {error}") from error


# --------------------------------------------------------------------------------------
# commands: one package function each
# --------------------------------------------------------------------------------------

def _cmd_model_request(a):
    return pilot_state.publish_model_request(a.run_root, a.attempt, a.stage, model_id=a.model_id, invocation_id=a.invocation_id,
                                             input_digests=a.input_digest)


def _cmd_model_response(a):
    return pilot_state.publish_model_stage_artifact(a.run_root, a.attempt, a.stage, a.artifact, transport_attempts=a.transport_attempts)


def _cmd_read_model_artifact(a):
    return pilot_state.read_model_stage_artifact(a.run_root, a.attempt, a.stage, a.digest)


def _cmd_read_context(a):
    return pilot_state.read_context_selection(a.run_root, a.attempt, a.digest)


def _cmd_plan_batches(a):
    return batch_assembly.plan_batches(a.requirements, a.grouping)


def _cmd_with_digest(a):
    return batch_assembly.with_digest(a.value)


def _cmd_validate_fragment(a):
    return batch_assembly.validate_fragment(a.fragment, a.plan, a.header, a.context_receipt)


def _cmd_assemble(a):
    return batch_assembly.assemble_candidate(a.header, a.plan, a.fragment)


def _cmd_publish_artifact(a):
    return pilot_state.publish_run_artifact_bytes(a.run_root, a.attempt, a.name, a.file)


def _cmd_document_digest(a):
    return canonical_document.document_sha256(a.document)


def _cmd_validate_canonical(a):
    return canonical_document.validate_canonical_document(a.document)


def _cmd_read_ledger(a):
    return pilot_state.read_reviewer_session_ledger(a.run_root, a.attempt, a.digest)


def _cmd_read_effective(a):
    return pilot_state.read_effective_canonical(a.run_root, a.attempt)


def _cmd_open_automation_boundary(a):
    return pilot_state.open_automation_review_boundary(a.run_root, a.attempt, a.facts)


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


RUN = ("--run-root", "--attempt")


def _diagnostics(result: Any) -> bool:
    return bool(result)


def _not_runnable(result: Any) -> bool:
    return getattr(result, "status", None) != "READY"


# name: (handler, options, invalid-when, named parts of a tuple result)
COMMANDS: dict[str, tuple[Callable, tuple, Callable | None, tuple[str, ...]]] = {
    "model-request": (_cmd_model_request, (*RUN, "--stage", "--model-id", "--invocation-id", ("--input-digest", "append")), None, ()),
    "model-response": (_cmd_model_response, (*RUN, "--stage", ("--artifact", _json), ("--transport-attempts", int, 1)), None, ()),
    "read-model-artifact": (_cmd_read_model_artifact, (*RUN, "--stage", "--digest"), None, ()),
    "read-context": (_cmd_read_context, (*RUN, "--digest"), None, ()),
    "plan-batches": (_cmd_plan_batches, (("--requirements", _json), ("--grouping", _json)), None, ()),
    "with-digest": (_cmd_with_digest, (("--value", _json),), None, ()),
    "validate-fragment": (_cmd_validate_fragment, (("--fragment", _json), ("--plan", _json), ("--header", _json), ("--context-receipt", _json)), None, ()),
    "assemble": (_cmd_assemble, (("--header", _json), ("--plan", _json), ("--fragment", _json, "append")), None, ("candidate", "receipt")),
    "publish-artifact": (_cmd_publish_artifact, (*RUN, "--name", ("--file", _bytes)), None, ()),
    "document-digest": (_cmd_document_digest, (("--document", _json),), None, ()),
    "validate-canonical": (_cmd_validate_canonical, (("--document", _json),), _diagnostics, ()),
    "read-ledger": (_cmd_read_ledger, (*RUN, ("--digest", str, None)), None, ()),
    "read-effective": (_cmd_read_effective, RUN, None, ()),
    "open-automation-boundary": (_cmd_open_automation_boundary, (*RUN, ("--facts", _json)), None, ()),
    "read-receipt": (_cmd_read_receipt, (*RUN, "--kind", "--event"), None, ()),
    "automation-digest": (_cmd_automation_digest, (("--automation", _json),), None, ()),
    "relations-digest": (_cmd_relations_digest, (("--relations", _json),), None, ()),
    "validate-automation": (_cmd_validate_automation, (("--automation", _json), ("--canonical", _json)), _diagnostics, ()),
    "runner-compat": (_cmd_runner_compat, (("--module-root", Path), ("--language", str), ("--canonical", _json), ("--automation", _json),
                                           ("--materialized", "flag")), _not_runnable, ()),
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handler, _options, invalid, parts = COMMANDS[args.command]
    try:
        targets = {part: _out_target(getattr(args, f"out_{part}")) for part in parts}
        out = _out_target(args.out) if not parts and args.out is not None else None
        result = handler(args)
    except Exception as error:
        _print({"status": "error", "command": args.command, "code": str(getattr(error, "code", None) or type(error).__name__), "message": str(error)})
        return 2
    status = "invalid" if invalid is not None and invalid(result) else "ok"
    payload: dict[str, Any] = {"status": status, "command": args.command}
    if parts:
        payload["out"] = {part: _write(targets[part], plain(value)) for part, value in zip(parts, result, strict=True)}
    elif out is not None:
        payload.update(_write(out, plain(result)))
    else:
        payload["result"] = plain(result)
    _print(payload)
    return 1 if status == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
