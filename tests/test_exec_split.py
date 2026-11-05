"""P8a-1, P4b: the package starts no process and contacts no network.

A human runs the build (``materialize`` prints the command, ``ingest`` takes the reports), so no file of the package, mode B included,
imports a process, network, signal or library-loading primitive: the ratchet below is on the code itself (imports and attribute calls of
every runtime ``.py``), with the exceptions of the development tree only (``ci_gate``, ``release_manifest``) and the one ``ctypes`` of the
confined writer (fixed kernel32/ntdll file calls).  The profile of a delivery is still the presence of its execution files
(``exec_commands``, ``exec_runner``): a copy of the pack without them has to behave as cases-only.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_first_run import _python_project

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
EXEC_MODULES = ("exec_commands", "exec_runner")
GATE = "execution_gate"

# the process, network, signal and loading primitives: no runtime file of the package imports or calls one (P4b), except
PRIMITIVE_MODULES = {"subprocess", "socket", "ssl", "signal", "ctypes", "pty", "multiprocessing", "http.client", "http.server", "urllib.request",
                     "ftplib", "smtplib", "telnetlib", "webbrowser", "asyncio.subprocess", "socketserver", "xmlrpc.client"}
PRIMITIVE_CALLS = {("os", "system"), ("os", "popen"), ("os", "startfile"), ("os", "fork"), ("os", "forkpty"), ("os", "kill"), ("os", "killpg"),
                   ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell"), ("os", "posix_spawn"), ("os", "posix_spawnp")}
PRIMITIVE_CALL_PREFIXES = {("os", "exec"), ("os", "spawn")}  # os.execv, os.execvp, os.spawnl, os.spawnv ...
ALLOWED_PRIMITIVES = {
    # CI helper of the development tree, not part of any delivered profile
    "ci_gate.py": {"subprocess"},
    # the maintainer's list of delivery files (git ls-files / ls-tree), a build-time tool of the package tree (P8a-2 A)
    "release_manifest.py": {"subprocess"},
    # Windows file-system calls of the confined writer (CreateFileW without FILE_SHARE_DELETE pins the parent directory; NtSetInformationFile
    # renames by handle): fixed kernel32/ntdll calls, no process, no network, no library loading by name (tests/test_p8a2_no_git.py holds that line)
    "confined_output.py": {"ctypes"},
}


def _runtime_files() -> list[Path]:
    """Every ``.py`` the pack can carry: the tools and the evals of the registry (the tests are not part of the package)."""
    return sorted([*TOOLS.glob("*.py"), *(ROOT / "evals").glob("*.py")])


def _primitives_of(tree: ast.AST) -> set[str]:
    """Names of the primitives a file reaches: an import (``import x``, ``from x import y``) or an ``os.system``-like attribute, also by a bare name."""
    found: set[str] = set()
    for name in _imported(tree):
        for primitive in PRIMITIVE_MODULES:
            if name == primitive or name.startswith(primitive + "."):
                found.add(primitive)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            pair = (node.value.id, node.attr)
            if pair in PRIMITIVE_CALLS or any(pair[0] == base and pair[1].startswith(prefix) for (base, prefix) in PRIMITIVE_CALL_PREFIXES):
                found.add(f"{pair[0]}.{pair[1]}")
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "asyncio"}:
            for alias in node.names:
                if (node.module, alias.name) in PRIMITIVE_CALLS or any(alias.name.startswith(prefix) for (base, prefix) in PRIMITIVE_CALL_PREFIXES if base == node.module):
                    found.add(f"{node.module}.{alias.name}")
    return found


def _core_files() -> list[Path]:
    return sorted(path for path in TOOLS.glob("*.py") if not path.name.startswith("exec_"))


def _imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_two_execution_modules_and_the_gate_exist_and_the_stand_and_the_probe_are_gone() -> None:
    for name in (*EXEC_MODULES, GATE):
        assert (TOOLS / f"{name}.py").is_file(), name
    assert sorted(path.stem for path in TOOLS.glob("exec_*.py")) == sorted(EXEC_MODULES)
    for gone in ("exec_stand", "exec_probe", "exec_git"):
        assert not (TOOLS / f"{gone}.py").exists(), gone


def test_the_core_never_imports_an_execution_module() -> None:
    offenders: list[str] = []
    for path in _core_files():
        if path.name == f"{GATE}.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                parts = [node.module or "", *(alias.name for alias in node.names)]
                if node.level and (node.module or "").startswith("exec_"):
                    offenders.append(f"{path.name}:{node.lineno}")
                elif any(part.startswith("tools.exec_") or part.split(".")[-1].startswith("exec_") for part in parts):
                    offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name.split(".")[-1].startswith("exec_") for alias in node.names):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_importlib_is_used_only_by_the_gate_and_only_for_the_closed_list() -> None:
    for path in sorted(TOOLS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__"]
        if path.name == f"{GATE}.py":
            assert calls == []
            continue
        assert not calls, f"{path.name} calls __import__"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "import_module" and isinstance(node.value, ast.Name) and node.value.id == "importlib":
                pytest.fail(f"{path.name}:{node.lineno} loads a module by name outside the gate")


def test_no_file_of_the_package_starts_a_process_opens_a_connection_or_loads_a_library() -> None:
    """The ratchet of P4b, without an exception for the execution files: ``exec_commands`` and ``exec_runner`` are inside it."""
    offenders: list[str] = []
    for path in _runtime_files():
        found = _primitives_of(ast.parse(path.read_text(encoding="utf-8")))
        offenders += [f"{path.name}: {name}" for name in sorted(found - ALLOWED_PRIMITIVES.get(path.name, set()))]
    assert offenders == []


@pytest.mark.parametrize("profile", ["mode-b", "cases-only"])
def test_a_delivery_has_no_file_that_starts_a_process_and_only_the_confined_writer_has_ctypes(profile: str) -> None:
    """V4b: the exceptions of the ratchet are files of development; a delivery carries none, so inside it the ratchet has no exception for a process
    (``ctypes`` stays in the one file that pins a directory by handle)."""
    from tools import pack_registry

    kept = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], profile)
    assert not {"tools/ci_gate.py", "tools/release_manifest.py"} & set(kept)
    offenders = []
    for path in kept:
        if path.endswith(".py"):
            found = _primitives_of(ast.parse((ROOT / path).read_text(encoding="utf-8")))
            offenders += [f"{path}: {name}" for name in sorted(found - ({"ctypes"} if path == "tools/confined_output.py" else set()))]
    assert offenders == []


def test_the_allowed_primitives_are_still_there_so_that_an_exception_cannot_outlive_its_reason() -> None:
    for name, primitives in ALLOWED_PRIMITIVES.items():
        found = _primitives_of(ast.parse((TOOLS / name).read_text(encoding="utf-8")))
        assert primitives <= found, f"{name} no longer uses {sorted(primitives - found)}: remove the exception"


def test_the_ratchet_sees_what_it_is_meant_to_see() -> None:
    """The scanner against planted sources: each form of reaching a primitive is found, a comment and a string are not."""
    planted = {
        "import subprocess": {"subprocess"},
        "from subprocess import run": {"subprocess"},
        "import os\nos.system('x')": {"os.system"},
        "import os\nos.popen('x')": {"os.popen"},
        "import os\nos.execvp('x', [])": {"os.execvp"},
        "import os\nos.spawnl(0, 'x')": {"os.spawnl"},
        "import os\nos.startfile('x')": {"os.startfile"},
        "from os import system": {"os.system"},
        "import asyncio\nasyncio.create_subprocess_exec('x')": {"asyncio.create_subprocess_exec"},
        "import http.client": {"http.client"},
        "import urllib.request": {"urllib.request"},
        "import multiprocessing": {"multiprocessing"},
        "from webbrowser import open": {"webbrowser"},
        "import smtplib, ftplib": {"smtplib", "ftplib"},
        "import ctypes": {"ctypes"},
        "import pty": {"pty"},
        "x = 'subprocess os.system'  # import socket": set(),
        "import os\nos.path.join('a')": set(),
    }
    for source, expected in planted.items():
        assert _primitives_of(ast.parse(source)) == expected, source


def test_the_launch_primitives_and_the_stand_are_gone_from_every_file() -> None:
    names = {node.name for path in _runtime_files() for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    for gone in ("run_subprocess", "_run_subprocess", "_terminate_process_tree", "_assign_windows_kill_job", "_close_windows_handle",
                 "_invoke_closed_request", "invoke_closed_request", "run_tests_v5", "stand_down", "service_stop", "build_probe",
                 "rerun_retained_tests", "cmd_rerun_retained", "record_process_stopped", "publish_retained_rerun_observation",
                 "consume_controller_process_stop_proof"):
        assert gone not in names, gone
    for path in _runtime_files():
        if "subprocess" in ALLOWED_PRIMITIVES.get(path.name, set()):
            continue
        source = path.read_text(encoding="utf-8")
        assert "Popen" not in source and "taskkill" not in source and "killpg" not in source, path.name


def test_the_gate_lists_exactly_the_two_modules_and_loads_nothing_else() -> None:
    from tools import execution_gate

    assert tuple(execution_gate.EXEC_MODULES) == EXEC_MODULES
    assert execution_gate.execution_available() is True
    assert execution_gate.default_profile() == "local-pilot-v1"
    assert execution_gate.load("exec_commands").__name__ == "tools.exec_commands"
    for name in ("pilot_state", "os", "exec_unknown", "exec_stand", "tools.exec_commands", "../exec_commands", ""):
        with pytest.raises(ValueError):
            execution_gate.load(name)


def test_the_gate_names_in_one_sentence_that_the_profile_is_the_presence_of_the_files() -> None:
    doc = ast.get_docstring(ast.parse((TOOLS / f"{GATE}.py").read_text(encoding="utf-8"))) or ""
    assert "presence" in doc and "exec_" in doc


# --- a delivery without the execution files --------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack = tmp_path_factory.mktemp("cases-only") / "pack"
    shutil.copytree(ROOT, pack, ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", ".pytest_cache"))
    for name in EXEC_MODULES:
        (pack / "tools" / f"{name}.py").unlink()
    return pack


def _run(pack: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})


def _json(text: str) -> dict:
    """The last JSON object of the output (a command prints it indented, or on one line)."""
    decoder, found, position = json.JSONDecoder(), None, 0
    while (position := text.find("{", position)) != -1:
        try:
            found, end = decoder.raw_decode(text, position)
        except ValueError:
            position += 1
            continue
        position = end
    if found is None:
        raise AssertionError(text)
    return found


def test_a_package_without_the_execution_files_is_cases_only(cases_only_pack: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "from tools import execution_gate as g; print(g.execution_available(), g.default_profile(), g.load('exec_commands'))"],
        cwd=cases_only_pack, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["False", "cases-only-v1", "None"]


def test_scan_without_a_profile_is_cases_only_there(cases_only_pack: Path, tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=False)
    completed = _run(cases_only_pack, "tools.run_pipeline", "scan", "--project", str(project), "--docs", "services/api/docs/feature.md",
                     "--module", "nested")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "cases-only-v1" in completed.stdout
    assert "local-pilot-v1" not in completed.stdout


def test_scan_for_local_pilot_is_refused_before_any_run_exists(cases_only_pack: Path, tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=True)
    completed = _run(cases_only_pack, "tools.run_pipeline", "scan", "--project", str(project), "--docs", "services/api/docs/feature.md",
                     "--module", "nested", "--profile", "local-pilot-v1")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    payload = _json(completed.stdout)
    assert payload["status"] == "error" and payload["reason"] == "PROFILE_NOT_IN_PACKAGE" and payload.get("stage") == "scan"
    assert payload.get("fix")
    assert not (project / ".pilot-runs").exists()


@pytest.mark.parametrize("command", ["exec", "materialize", "ingest"])
def test_the_execution_commands_are_not_in_the_parser_there(cases_only_pack: Path, tmp_path: Path, command: str) -> None:
    project = _python_project(tmp_path, runtime=False)
    completed = _run(cases_only_pack, "tools.run_pipeline", command, "--project", str(project), "--run", "RUN-x")
    assert completed.returncode == 2
    assert "invalid choice" in completed.stdout + completed.stderr


def test_ready_for_local_pilot_stops_on_the_profile_row_there(cases_only_pack: Path, tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=True)
    completed = _run(cases_only_pack, "tools.ready", "--project", str(project), "--docs", "services/api/docs/feature.md",
                     "--module", "nested", "--profile", "local-pilot-v1")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    report = _json(completed.stdout)
    rows = {row["id"]: row for row in report["checks"]}
    assert report["status"] == "BLOCKED"
    assert rows["profile"]["level"] == "STOP" and rows["profile"]["block"] == "cases" and rows["profile"]["fix"]
    assert not any(row["id"].startswith("execution:") for row in report["checks"])


def test_ready_without_a_profile_is_cases_only_there(cases_only_pack: Path, tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=False)
    completed = _run(cases_only_pack, "tools.ready", "--project", str(project), "--docs", "services/api/docs/feature.md", "--module", "nested")
    report = _json(completed.stdout)
    assert report["profile"] == "cases-only-v1"
    assert not any(row["id"].startswith("execution:") or row["id"] == "profile" for row in report["checks"])


# --- the full package keeps the CLI one module ------------------------------------------------------------------------

def test_exec_is_replaced_and_answers_with_the_way_out_as_json_under_python_m(tmp_path: Path) -> None:
    """``python -m tools.run_pipeline`` runs the file as ``__main__``; ``exec`` (one release) starts nothing, reads no run, and says
    what replaced it: the JSON envelope with the code, the fix with both commands, exit 2."""
    project = _python_project(tmp_path, runtime=False)
    completed = subprocess.run([sys.executable, "-m", "tools.run_pipeline", "exec", "--project", str(project), "--run", "RUN-missing"],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
    payload = _json(completed.stdout)
    assert payload["status"] == "error" and payload["reason"] == "EXEC_REPLACED"
    assert "materialize --project" in payload["fix"] and "ingest --project" in payload["fix"]
    assert not (project / ".pilot-runs").exists()


def test_a_stop_of_the_new_commands_is_a_json_answer_under_python_m(tmp_path: Path) -> None:
    """The HostStop of ``exec_commands`` is the class of ``tools.run_pipeline``: the file hands over to that module, or the stop would be a traceback."""
    project = _python_project(tmp_path, runtime=False)
    for command in ("materialize", "ingest"):
        completed = subprocess.run([sys.executable, "-m", "tools.run_pipeline", command, "--project", str(project), "--run", "RUN-missing"],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "Traceback" not in completed.stderr
        payload = _json(completed.stdout)
        assert payload["status"] == "error" and payload["reason"] != "INTERNAL"
