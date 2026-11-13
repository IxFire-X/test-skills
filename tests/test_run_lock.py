"""J2: one OS lock per run for every command, and typed refusals of the journal.

The lock belongs to the commands (processes): ``tests/conftest.py`` switches it off in this process, the subprocesses here keep it."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import fixes, json_cli, pilot_state

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}
_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

# a writer: the event copy is made, then it waits for the reader (or for ever) before the journal is replaced
_WRITER = """
import sys, time
from pathlib import Path
from tools import pilot_state
root, flags, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
real = pilot_state.atomic_write_confined_bytes_at_root
def pause(*args):
    (flags / "copy-made").write_text("x")
    end = time.time() + (600 if mode == "hang" else 20)
    while time.time() < end and not (mode == "meet" and (flags / "reader-ready").exists()):
        time.sleep(0.02)
    time.sleep(1.0)
    return real(*args)
if mode != "plain":
    pilot_state.atomic_write_confined_bytes_at_root = pause
digest = pilot_state.module_selection_digest(root.parent.parent, ".")
pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=digest)
"""
# a holder: takes the run as any command does and keeps it until the flag ``release`` appears
_HOLDER = """
import sys, time
from pathlib import Path
from tools import pilot_state
root, flags = Path(sys.argv[1]), Path(sys.argv[2])
pilot_state._run_root(root)
(flags / "held").write_text("x")
end = time.time() + 60
while time.time() < end and not (flags / "release").exists():
    time.sleep(0.02)
"""
# a command: ``status`` as the CLI runs it, with the flag of «about to start» and a short wait when asked
_STATUS = """
import sys
from pathlib import Path
from tools import run_lock
from tools.run_pipeline import main
project, run_id, flags, wait = sys.argv[1], sys.argv[2], Path(sys.argv[3]), float(sys.argv[4])
if wait:
    run_lock.WAIT_SECONDS = wait
(flags / "reader-ready").write_text("x")
sys.exit(main(["status", "--project", project, "--run", run_id]))
"""


def _run(tmp_path: Path) -> Path:
    return Path(pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])


def _spawn(script: str, *args: object) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", script, *map(str, args)], cwd=ROOT, env=_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def _wait_for(path: Path, seconds: float = 30) -> None:
    end = time.time() + seconds
    while time.time() < end and not path.exists():
        time.sleep(0.02)
    assert path.exists(), f"{path.name} did not appear"


def _answer(process: subprocess.Popen, seconds: float = 60) -> dict:
    out, _err = process.communicate(timeout=seconds)
    return json.loads(out[out.index("{"):])


def _cli(*args: str) -> dict:
    done = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=_ENV, capture_output=True, text=True, encoding="utf-8")
    return json.loads(done.stdout[done.stdout.index("{"):])


def test_a_status_between_the_copy_and_the_journal_leaves_the_journal_readable(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    writer = _spawn(_WRITER, root, flags, "meet")
    _wait_for(flags / "copy-made")
    reader = _spawn(_STATUS, tmp_path, root.name, flags, 0)
    assert _answer(reader)["status"] == "ok"
    assert writer.wait(timeout=60) == 0, writer.stderr.read()
    assert [event["seq"] for event in pilot_state.derive_state(root)["events"]] == [1, 2]


def test_a_killed_writer_frees_the_lock_and_the_next_command_finishes_its_record(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    writer = _spawn(_WRITER, root, flags, "hang")
    _wait_for(flags / "copy-made")
    writer.kill()
    writer.wait()
    assert _spawn(_WRITER, root, flags, "plain").wait(timeout=60) == 0
    assert [event["seq"] for event in pilot_state.derive_state(root)["events"]] == [1, 2]
    assert not list((root / "pending-events").glob("*.json"))


def test_a_command_waits_for_the_command_that_holds_the_run(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    holder = _spawn(_HOLDER, root, flags)
    _wait_for(flags / "held")
    reader = _spawn(_STATUS, tmp_path, root.name, flags, 0)
    time.sleep(2)
    assert reader.poll() is None
    (flags / "release").write_text("x")
    assert _answer(reader)["status"] == "ok"
    assert holder.wait(timeout=60) == 0


def test_a_wait_that_is_too_long_is_concurrent_writer_with_its_fix(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    holder = _spawn(_HOLDER, root, flags)
    _wait_for(flags / "held")
    answer = _answer(_spawn(_STATUS, tmp_path, root.name, flags, 0.3))
    (flags / "release").write_text("x")
    holder.wait(timeout=60)
    assert answer["code"] == "CONCURRENT_WRITER"
    assert answer["fix"] == fixes.FIXES["CONCURRENT_WRITER"]


def test_a_corrupt_journal_is_journal_corrupt_with_its_fix_not_input_invalid(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "events" / "0000000001.json").write_bytes(b"x")
    answer = _cli("tools.api", "read-effective", "--run-root", str(root), "--attempt", "a")
    assert answer["code"] == "JOURNAL_CORRUPT"
    assert answer["fix"] == fixes.FIXES["JOURNAL_CORRUPT"]
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["code"] == "JOURNAL_CORRUPT"


def test_a_corrupt_journal_stays_the_refusal_after_the_refusal_is_recorded(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "events" / "0000000001.json").write_bytes(b"x")
    _cli("tools.api", "read-effective", "--run-root", str(root), "--attempt", "a")
    assert _cli("tools.api", "read-effective", "--run-root", str(root), "--attempt", "a")["code"] == "JOURNAL_CORRUPT"


def test_a_missing_journal_or_run_is_still_an_input_error(tmp_path: Path) -> None:
    root = _run(tmp_path)
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", "0" * 32)["code"] == "EXECUTION_COORDINATES"
    (root / "events.jsonl").unlink()
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["code"] == "EXECUTION_COORDINATES"
    assert _cli("tools.api", "read-effective", "--run-root", str(root), "--attempt", "a")["code"] == "INPUT_INVALID"


def test_a_publication_that_cannot_be_proved_is_publication_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    digest = pilot_state.module_selection_digest(tmp_path, ".")
    original = pilot_state.read_confined_bytes
    target = root / "events" / "0000000002.json"

    def unreadable(*args: object, **kwargs: object):
        if Path(args[2]) == target:
            raise OSError("gone")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_state, "read_confined_bytes", unreadable)
    with pytest.raises(ValueError) as caught:
        pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=digest)
    envelope = json_cli.error_envelope(caught.value)
    assert envelope["code"] == "PUBLICATION_UNKNOWN"
    assert envelope["fix"] == fixes.FIXES["PUBLICATION_UNKNOWN"]


def test_a_publication_rolled_back_in_full_asks_for_the_same_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    digest = pilot_state.module_selection_digest(tmp_path, ".")
    monkeypatch.setattr(pilot_state, "atomic_write_confined_bytes_at_root", lambda *_args: (_ for _ in ()).throw(OSError("journal")))
    with pytest.raises(ValueError, match="event publication") as caught:
        pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=digest)
    assert json_cli.error_envelope(caught.value)["code"] == "PUBLICATION_UNKNOWN"


def test_the_envelope_of_a_journal_refusal_has_no_new_field() -> None:
    from tools.run_lock import JournalRefusal

    assert set(json_cli.error_envelope(JournalRefusal("CONCURRENT_WRITER", "busy"))) == {"status", "code", "message", "fix", "next"}


def test_status_leaves_the_run_directory_as_it_was(tmp_path: Path) -> None:
    root = _run(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["status"] == "ok"
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_a_manifest_that_is_a_directory_is_refused_not_locked(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "run-manifest.json").replace(tmp_path / "manifest.moved")
    (root / "run-manifest.json").mkdir()
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["status"] == "error"


def test_a_manifest_that_is_a_link_is_refused_not_followed(tmp_path: Path) -> None:
    root = _run(tmp_path)
    moved = tmp_path / "manifest.moved"
    (root / "run-manifest.json").replace(moved)
    try:
        (root / "run-manifest.json").symlink_to(moved)
    except OSError:
        pytest.skip("no symlinks here")
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["status"] == "error"


def test_a_read_command_does_not_recover_the_record_of_a_killed_writer(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    writer = _spawn(_WRITER, root, flags, "hang")
    _wait_for(flags / "copy-made")
    writer.kill()
    writer.wait()
    before = sorted(path.name for path in root.rglob("*.json"))
    assert _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)["status"] == "ok"
    assert sorted(path.name for path in root.rglob("*.json")) == before


def test_no_tool_switches_the_lock_off() -> None:
    for path in (ROOT / "tools").glob("*.py"):
        calls = [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Call)
                 and getattr(node.func, "attr", getattr(node.func, "id", None)) == "disable"]
        assert not calls or path.name == "run_lock.py", path.name


def test_the_lock_module_is_the_standard_library_without_processes() -> None:
    tree = ast.parse((ROOT / "tools" / "run_lock.py").read_text(encoding="utf-8"))
    names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert names <= {"__future__", "os", "sys", "time", "msvcrt", "fcntl", "pathlib", "tools"}


def _modules_with_a_run_argument() -> set[str]:
    found = set()
    for path in (ROOT / "tools").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument" and node.args \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value in {"--run", "--run-root"}:
                found.add(path.stem)
    return found


def _commands(project: Path, root: Path) -> dict[str, tuple[str, list[str]]]:
    run, name = ["--project", str(project), "--run", root.name], str(root)
    return {
        "run_pipeline": ("tools.run_pipeline", ["status", *run]),
        "finish": ("tools.run_pipeline", ["finish", *run]),
        "report": ("tools.run_pipeline", ["report", *run]),
        "exec_commands": ("tools.run_pipeline", ["materialize", *run]),
        "ingest": ("tools.run_pipeline", ["ingest", *run, "--no-reports", "--reason", "BUILD_FAILED"]),
        "api": ("tools.api", ["read-effective", "--run-root", name, "--attempt", "a"]),
        "review_view": ("tools.review_view", [*run, "--effective"]),
        "step_size": ("tools.step_size", ["--project", str(project), "--run", root.name, "--stage", "tc-generator:b1"]),
        "zephyr_template_export": ("tools.zephyr_template_export", [*run, "--cases", name]),
    }


def test_every_command_with_a_run_argument_is_in_the_table_below() -> None:
    assert _modules_with_a_run_argument() == {"api", "exec_commands", "review_view", "run_pipeline", "step_size", "zephyr_template_export"}


_TABLE = ["run_pipeline", "finish", "report", "exec_commands", "ingest", "api", "review_view", "step_size",
          "zephyr_template_export"]


def _call(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, hold) -> tuple[Path, str]:
    """The command of the table run in this process with ``run_lock.hold`` replaced by ``hold``; what it printed."""
    import importlib

    from tools import run_lock, zephyr_template_export

    root = _run(tmp_path)
    (tmp_path / "c.json").write_text("{}")
    monkeypatch.setattr(run_lock, "hold", hold)
    monkeypatch.setattr(zephyr_template_export, "_template_setting", lambda _project: "template.csv")  # without a template it never opens the run
    monkeypatch.setattr(zephyr_template_export, "read_template", lambda *_args: b"")
    name, argv = _commands(tmp_path, root)[module]
    try:
        importlib.import_module(name).main(argv)
    except SystemExit:
        pass
    return root, capsys.readouterr().out


@pytest.mark.parametrize("module", _TABLE)
def test_a_command_with_a_run_argument_takes_the_run_lock(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    held: list[Path] = []
    root, _out = _call(module, tmp_path, monkeypatch, capsys, lambda _project, run: held.append(Path(run)))
    assert root.resolve() in held


@pytest.mark.parametrize("module", _TABLE)
def test_a_busy_run_is_concurrent_writer_in_every_command(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from tools.run_lock import JournalRefusal

    def busy(_project: Path, _run: Path) -> None:
        raise JournalRefusal("CONCURRENT_WRITER", "another command is working with this run")

    _root, out = _call(module, tmp_path, monkeypatch, capsys, busy)
    answer = json.loads(out[out.index("{"):])
    assert answer["code"] == "CONCURRENT_WRITER"
    assert answer["fix"] == fixes.FIXES["CONCURRENT_WRITER"]
