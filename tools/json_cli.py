"""Small shared argparse adapter for deterministic JSON command-line tools."""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.clock import utc_now_z
from tools.fixes import CODE_ANSWER_FIX, CODE_ANSWER_HUMAN, RETRY_SPENT, SPENT_HUMAN, fix_for, human_for


_CODE_PREFIX = re.compile(r"^([A-Z][A-Z0-9_]+)(?::|$)")
_FIX_TAIL = "; fix: "


def error_code_prefix(message: str) -> str | None:
    """The refusal code a message starts with (``CODE: …``), or None."""
    found = _CODE_PREFIX.match(message)
    return found.group(1) if found else None


_PACK_DIRS = ("contracts", "schemas", "skills", "tools")


def _missing_pack_file(error: BaseException) -> str | None:
    """The path, relative to the root of the pack, of a file of the pack that is not there (a delivery that lacks a file); else None."""
    if not isinstance(error, FileNotFoundError) or not error.filename:
        return None
    try:
        relative = Path(os.fsdecode(error.filename)).resolve().relative_to(Path(__file__).resolve().parents[1])
    except (ValueError, OSError):
        return None
    return relative.as_posix() if relative.parts and relative.parts[0] in _PACK_DIRS else None


def error_fields(error: BaseException, table: bool = True) -> dict[str, str]:
    """``{code, message[, fix]}`` of a refusal — the error's own code, else the ``CODE:`` its message starts with,
    else its type; the fix is the error's own, else the ``; fix: …`` tail of the message.  The message stays whole: its
    readers look for the fix text in it, the separate field is for the code.  An OSError names its file — the agent's own
    input, not a secret. With no fix of its own the code's fix is the one of ``tools.fixes`` — unless ``table`` is
    False, for a call site that has a more exact fix of its own to fall back to; the error's own fix is above both."""
    missing = _missing_pack_file(error)
    if missing:
        return {"code": "PACK_INCOMPLETE", "message": f"PACK_INCOMPLETE: поставка неполна: нет файла пакета {missing}", "fix": str(fix_for("PACK_INCOMPLETE"))}
    message = str(error)
    if isinstance(error, OSError):
        message = f"{type(error).__name__}: {error.strerror or 'input/output error'}" + (f": {error.filename}" if error.filename else "")
    # a bare ValueError names no refusal — INPUT_INVALID, as run_pipeline.main says it, with the fix of the table
    code = getattr(error, "code", None) or error_code_prefix(message) or ("INPUT_INVALID" if type(error) is ValueError else type(error).__name__)
    fix = getattr(error, "fix", None) or (message.split(_FIX_TAIL, 1)[1] if _FIX_TAIL in message else None) or (fix_for(str(code), message) if table else None)
    return {"code": str(code), "message": message, **({"fix": str(fix)} if fix else {})}


def error_envelope(error: BaseException, next_step: str | None = None) -> dict[str, object]:
    """The one refusal envelope of every CLI — ``{status, code, message, fix, next}`` (fix/next may be null); ``human`` — the tester's phrase, when the code has one."""
    fields = error_fields(error)
    fix = str(fields.get("fix") or "")
    human = (SPENT_HUMAN if fix.startswith(RETRY_SPENT)  # a spent retry says the same whatever the code
             else CODE_ANSWER_HUMAN if fix == CODE_ANSWER_FIX  # an answer the code built: no repeat of a model mends it
             else human_for(fields["code"], fields["message"]))
    return {"status": "error", "code": fields["code"], "message": fields["message"], "fix": fields.get("fix"), "next": next_step, **({"human": human} if human else {})}


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
DRY_RUN_REFUSALS = "dry-run-refusals.jsonl"  # the refusals of ``submit --dry-run``: a line each, no event — the journal is not touched by a check


def emit_json(payload: object, *, indent: int | None = None, sort_keys: bool = True, separators: tuple[str, str] | None = (",", ":")) -> None:
    """Print one JSON answer to stdout in UTF-8.  A reader that went away (``| head -c``: BrokenPipeError, on Windows EINVAL) is no
    fault of the command: stdout goes to the null device, so the flush at exit meets the pipe no more."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys, indent=indent, separators=separators))
        sys.stdout.flush()
    except OSError:
        try:
            null = os.open(os.devnull, os.O_WRONLY)
        except OSError:
            return
        try:
            os.dup2(null, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        finally:
            os.close(null)  # dup2 made its own copy on the descriptor of stdout


def jsonl_rows(path: Path) -> list[object]:
    """The values of the lines of a JSONL file of the run, in order: none when there is no file to read; a line that does not parse is
    skipped, the others still count."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def jsonl_lines(path: Path) -> list[bytes] | None:
    """The raw non-empty lines of a JSONL file, as they are (a line is checked by its sha256); None when there is no file to read."""
    try:
        return [line for line in Path(path).read_bytes().split(b"\n") if line.strip()]
    except OSError:
        return None
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")  # a stage or a batch id; anything else is not a label of the run
_ROW_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")  # a code of a diagnostic row: an identifier, never a text, a path or a message


def record_refusal(run_root: object, command: str, code: object, stage: object = None, batch: object = None, spent: bool = False,
                   codes: Iterable[object] = (), dry_run: bool = False) -> None:
    """One line ``{at, command, code, stage, batch}`` in ``<run>/refusals.jsonl`` for a refusal envelope (status error or
    invalid) of a command that names its run; the run report counts the file.  No content, no paths, no messages — the code
    of the refusal and where in the run it was met; without a known run nothing is written; a line that cannot be written
    never changes the refusal the agent gets. The line goes with a ``COMMAND_REFUSED`` event of the journal whose digest is the sha256 of
    the line's bytes — the report counts the events, the line only carries the detail.  Both under the lock of the run (2 s at most: a run
    another command keeps longer gets neither), the event first: a journal that takes no event gets no line, so no line is ever alone.
    ``dry_run`` (``submit --dry-run``, campaign v0.8 C1): the same line, without ``spent``, into ``DRY_RUN_REFUSALS`` and with no event — a check
    writes neither the answer nor the journal; the report counts these lines apart (``refusals.dry_run``)."""
    from pathlib import Path

    from tools.confined_output import atomic_write_confined_bytes_at_root, read_confined_bytes

    if run_root is None or code in ("CONCURRENT_WRITER", "JOURNAL_CORRUPT"):  # a busy run is written by no one else's command; a journal that fails its checks takes nothing
        return
    try:
        from tools.output_guard import local_path_text

        if not local_path_text(str(run_root)) or re.fullmatch(r"[0-9a-f]{32}", Path(str(run_root)).name) is None:  # a run id, judged by its text first
            return
        root = Path(str(run_root)).resolve()
        if root.parent.name != ".pilot-runs" or not (root / "run-manifest.json").is_file():
            return
        stage = stage if isinstance(stage, str) and _LABEL.fullmatch(stage) else None
        batch = batch if isinstance(batch, str) and _LABEL.fullmatch(batch) else (stage.split(":", 1)[1] if stage and stage.startswith("tc-generator:") else None)
        rows = sorted({str(item) for item in codes if isinstance(item, str) and _ROW_CODE.fullmatch(item)})  # the codes of the rows a bag code hides (R&D-29)
        row = {"at": utc_now_z(), "command": str(command),
               "code": str(code or "INVALID"), "stage": stage, "batch": batch, **({"spent": True} if spent and not dry_run else {}),  # spent: the one retry of the stage was refused (a bool, no text)
               **({"codes": rows} if rows else {})}
        path, project = root / (DRY_RUN_REFUSALS if dry_run else _REFUSALS), root.parent.parent  # = run_store.project_of: this leaf does not load run_store
        line = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        from tools import pilot_state, run_lock

        with run_lock.for_one_read(project, root, wait=2.0):  # the fact is the event, bound to its line by the digest: written first (a check has none)
            if not dry_run:
                pilot_state.append_event(root, "COMMAND_REFUSED", actor="controller", artifact_digest="sha256:" + hashlib.sha256(line).hexdigest())
            atomic_write_confined_bytes_at_root(project, root, path, (read_confined_bytes(project, root, path) or b"") + line + b"\n")
    except (OSError, ValueError, KeyError, TypeError):
        return


def dry_run_refusals(root: Path) -> dict[str, Any] | None:
    """``refusals.dry_run`` of report.json: the refusals of ``submit --dry-run`` by their file — ``total``, ``by_code`` and, when a line carries
    them, ``row_codes`` (lines per code).  No event stands behind them: the count is the file's.  None when the run has no such line."""
    rows = [row for row in jsonl_rows(Path(root) / DRY_RUN_REFUSALS) if isinstance(row, dict)]
    if not rows:
        return None
    count = lambda values: {value: values.count(value) for value in sorted(set(values))}  # noqa: E731
    found = [str(code) for row in rows if isinstance(row.get("codes"), list) for code in row["codes"] if isinstance(code, str) and _ROW_CODE.fullmatch(code)]
    return {"total": len(rows), "by_code": count([str(row["code"]) for row in rows if isinstance(row.get("code"), str) and _ROW_CODE.fullmatch(row["code"])]),
            **({"row_codes": count(found)} if found else {})}


def spent_stages(run_root: object) -> set[str]:
    """The stages whose one retry was refused, by the lines of ``refusals.jsonl`` (a dry run writes none)."""
    rows = jsonl_rows(Path(str(run_root)) / _REFUSALS)  # a damaged line is skipped: it never gives a spent retry back
    return {str(row["stage"]) for row in rows if isinstance(row, dict) and row.get("spent") is True and row.get("stage")}


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
    payload: dict[str, object] = {"status": "error", "errors": [{"path": "", "message": message}]}
    if message.startswith("argument error: "):  # the one envelope of every refusal, the old field stays
        flags = [flag for flag in _MODEL_REQUEST_FLAGS if flag in message]
        fix = ("флаги " + ", ".join(flags) + " — у prepare; submit получает только --run-root, --stage, --dry-run, --transport-attempts, --abort; "
               "--attempt у prepare и submit нет") if flags else f"проверь флаги команды: {_module_name(prog)} <команда> --help"
        payload.update(code="INVALID_ARGUMENT", message=message, fix=fix)
    emit_json(payload, separators=None)


def one_closure(command):
    """One ``closure_cache()`` for a command: the journal is checked once, not on every read; what the command prints is let out after the final full check."""
    @functools.wraps(command)
    def wrapper(args):
        from tools.pilot_state import closure_cache  # imported when the command runs: pilot_state is heavy

        held = io.StringIO()
        with contextlib.redirect_stdout(held), closure_cache():
            code = command(args)
        sys.stdout.write(held.getvalue())
        return code
    wrapper.__closure_cached__ = True
    return wrapper


class JsonArgumentParser(argparse.ArgumentParser):
    """Argparse parser that never writes prose usage as the only stdout result."""

    def error(self, message: str) -> None:
        emit_error(f"argument error: {message}", self.prog)
        raise SystemExit(2)

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        """The arguments as argparse reads them.  Any value of any flag — an item of a list too — that names a share or a device
        (``\\\\…`` or ``//…``) is refused here, before any command reads, resolves or stats it; the subparsers are of this class too."""
        from tools.output_guard import local_path_text

        namespace, extras = super().parse_known_args(args, namespace)
        for name, value in vars(namespace).items():
            for item in value if isinstance(value, (list, tuple)) else (value,):
                if isinstance(item, (str, os.PathLike)) and not local_path_text(os.fspath(item)):
                    self.error(f"--{name.replace('_', '-')}: путь UNC или путь устройства не принимается — нужен локальный путь")
        return namespace, extras
