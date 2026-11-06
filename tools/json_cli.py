"""Small shared argparse adapter for deterministic JSON command-line tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from tools.fixes import fix_for


_CODE_PREFIX = re.compile(r"^([A-Z][A-Z0-9_]+)(?::|$)")
_FIX_TAIL = "; fix: "


def error_code_prefix(message: str) -> str | None:
    """The refusal code a message starts with (``CODE: …``), or None."""
    found = _CODE_PREFIX.match(message)
    return found.group(1) if found else None


def error_fields(error: BaseException, table: bool = True) -> dict[str, str]:
    """``{code, message[, fix]}`` of a refusal — the error's own code, else the ``CODE:`` its message starts with,
    else its type; the fix is the error's own, else the ``; fix: …`` tail of the message.  The message stays whole: its
    readers look for the fix text in it, the separate field is for the code.  An OSError names its file — the agent's own
    input, not a secret. With no fix of its own the code's fix is the one of ``tools.fixes`` — unless ``table`` is
    False, for a call site that has a more exact fix of its own to fall back to; the error's own fix is above both."""
    message = str(error)
    if isinstance(error, OSError):
        message = f"{type(error).__name__}: {error.strerror or 'input/output error'}" + (f": {error.filename}" if error.filename else "")
    # a bare ValueError names no refusal — INPUT_INVALID, as run_pipeline.main says it, with the fix of the table
    code = getattr(error, "code", None) or error_code_prefix(message) or ("INPUT_INVALID" if type(error) is ValueError else type(error).__name__)
    fix = getattr(error, "fix", None) or (message.split(_FIX_TAIL, 1)[1] if _FIX_TAIL in message else None) or (fix_for(str(code)) if table else None)
    return {"code": str(code), "message": message, **({"fix": str(fix)} if fix else {})}


def error_envelope(error: BaseException, next_step: str | None = None) -> dict[str, object]:
    """The one refusal envelope of every CLI — ``{status, code, message, fix, next}`` (fix/next may be null)."""
    fields = error_fields(error)
    return {"status": "error", "code": fields["code"], "message": fields["message"], "fix": fields.get("fix"), "next": next_step}


def cli_error(error: BaseException, *, fix: str, code: str = "INVALID_INPUT") -> dict[str, object]:
    """The envelope ``{status, code, message, fix, next}`` of a CLI that had printed ``{status, message}`` — the refusal's own
    code and fix when it has them (``CODE: …; fix: …``), else *code* (``FILE_UNREADABLE`` for a file that cannot be read) and the *fix* of
    the CLI.  ``status`` and ``message`` stay as they were."""
    envelope = error_envelope(error)
    if envelope["code"] == type(error).__name__:  # no code of its own: the class of the exception is no code
        envelope["code"] = "FILE_UNREADABLE" if isinstance(error, OSError) else code
    envelope["fix"] = envelope["fix"] or fix
    if isinstance(error, OSError):
        envelope["message"] = str(error)
    return envelope


_MODEL_REQUEST_FLAGS = ("--model-id", "--invocation-id", "--input-digest", "--reuse-from")


_REFUSALS = "refusals.jsonl"
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")  # a stage or a batch id; anything else is not a label of the run


def record_refusal(run_root: object, command: str, code: object, stage: object = None, batch: object = None) -> None:
    """One line ``{at, command, code, stage, batch}`` in ``<run>/refusals.jsonl`` for a refusal envelope (status error or
    invalid) of a command that names its run; the run report counts the file.  No content, no paths, no messages — the code
    of the refusal and where in the run it was met; without a known run nothing is written; a line that cannot be written
    never changes the refusal the agent gets. The line is followed by a ``COMMAND_REFUSED`` event of the journal whose digest is
    the sha256 of the line's bytes — the report counts the events, the line only carries the detail."""
    import datetime
    from pathlib import Path

    from tools.confined_output import atomic_write_confined_bytes_at_root

    if run_root is None:
        return
    try:
        root = Path(str(run_root)).resolve()
        if root.parent.name != ".pilot-runs" or not (root / "run-manifest.json").is_file():
            return
        stage = stage if isinstance(stage, str) and _LABEL.fullmatch(stage) else None
        batch = batch if isinstance(batch, str) and _LABEL.fullmatch(batch) else (stage.split(":", 1)[1] if stage and stage.startswith("tc-generator:") else None)
        row = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "command": str(command),
               "code": str(code or "INVALID"), "stage": stage, "batch": batch}
        path = root / _REFUSALS
        before = path.read_bytes() if path.is_file() else b""
        line = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        atomic_write_confined_bytes_at_root(root.parent.parent, root, path, before + line + b"\n")
    except (OSError, ValueError, KeyError, TypeError):
        return
    try:  # the fact of the refusal is an event of the journal, bound to its line by the digest; a journal that does not pass its checks takes none
        from tools import pilot_state

        pilot_state.append_event(root, "COMMAND_REFUSED", actor="controller", artifact_digest="sha256:" + hashlib.sha256(line).hexdigest())
    except (OSError, ValueError, KeyError, TypeError):
        return


def _module_name(prog: str | None) -> str:
    """The module of the parser that refused: ``python -m tools.api`` as it is, a script name (``run_pipeline.py``) as its module."""
    if not prog:
        return "tools.api"
    if prog.startswith("python -m "):
        return prog.removeprefix("python -m ").strip()
    stem = Path(prog).stem
    return f"tools.{stem}" if stem and stem != "__main__" else "tools.api"


def emit_error(message: str, prog: str | None = None) -> None:
    """Emit the one machine-readable response allowed for usage/input errors."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    payload: dict[str, object] = {"status": "error", "errors": [{"path": "", "message": message}]}
    if message.startswith("argument error: "):  # the one envelope of every refusal, the old field stays
        flags = [flag for flag in _MODEL_REQUEST_FLAGS if flag in message]
        fix = ("флаги " + ", ".join(flags) + " — у model-request; model-response получает только --run-root, --attempt, --stage, "
               "--artifact, --transport-attempts") if flags else f"проверь флаги команды: {_module_name(prog)} <команда> --help"
        payload.update(code="INVALID_ARGUMENT", message=message, fix=fix)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class JsonArgumentParser(argparse.ArgumentParser):
    """Argparse parser that never writes prose usage as the only stdout result."""

    def error(self, message: str) -> None:
        emit_error(f"argument error: {message}", self.prog)
        raise SystemExit(2)

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        """The arguments as argparse reads them; a ``--project`` or ``--run-root`` on a share or a device is refused here, before any
        command resolves it (the subparsers are of this class too)."""
        from tools.output_guard import local_path_text

        namespace, extras = super().parse_known_args(args, namespace)
        for name in ("project", "run_root"):
            value = getattr(namespace, name, None)
            if value is not None and not local_path_text(str(value)):
                self.error(f"--{name.replace('_', '-')}: локальный каталог, не UNC и не путь устройства")
        return namespace, extras
