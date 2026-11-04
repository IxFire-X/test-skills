"""P8a-1: everything that runs a build or contacts the stand lives in ``tools/exec_*.py``.

A delivery without those four files is a cases-only package: it cannot start a build, a test process or a request to the
stand, and it does not offer ``local-pilot-v1``.  The profile of a delivery is the presence of its execution files — a file
cannot be "enabled" without copying the code — so the checks below are on the code itself: the import boundary (the core
never imports an ``exec_*`` module), the ratchet of process and network primitives, and a copy of the pack without the
four files that has to behave as cases-only.
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
EXEC_MODULES = ("exec_commands", "exec_stand", "exec_probe", "exec_runner")
GATE = "execution_gate"

# the process and network primitives: where they may be imported (the exec files, and the explicit exceptions below)
PRIMITIVES = {"subprocess", "socket", "ctypes", "signal", "http.client", "urllib.request", "ssl"}
ALLOWED_OUTSIDE_EXEC = {
    # a CI helper of the development tree, not part of the delivered profile
    "ci_gate.py": {"subprocess"},
    # the git probe of ``ready`` and of the pilot state: removed with the cases-only delivery (P8a-2)
    "ready.py": {"subprocess"},
    "pilot_state.py": {"subprocess"},
    # the maintainer's list of delivery files (git ls-files / ls-tree) — a build-time tool of the package tree (P8a-2 A)
    "release_manifest.py": {"subprocess"},
    # Windows file-system calls of the confined writer — no process, no network
    "confined_output.py": {"ctypes"},
}


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


def test_the_four_execution_modules_and_the_gate_exist() -> None:
    for name in (*EXEC_MODULES, GATE):
        assert (TOOLS / f"{name}.py").is_file(), name


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


def test_process_and_network_primitives_are_imported_only_by_the_execution_modules() -> None:
    offenders: list[str] = []
    for path in _core_files():
        found = _imported(ast.parse(path.read_text(encoding="utf-8")))
        for primitive in sorted(PRIMITIVES):
            if any(name == primitive or name.startswith(primitive + ".") for name in found) and primitive not in ALLOWED_OUTSIDE_EXEC.get(path.name, set()):
                offenders.append(f"{path.name}: {primitive}")
    assert offenders == []


def test_the_launch_primitives_are_gone_from_the_run_tests_core() -> None:
    source = (TOOLS / "run_tests.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    for moved in ("run_subprocess", "_run_subprocess", "_terminate_process_tree", "_assign_windows_kill_job", "_close_windows_handle",
                  "_invoke_closed_request", "run_tests_v5"):
        assert moved not in names
        assert moved in {node.name for node in ast.walk(ast.parse((TOOLS / "exec_runner.py").read_text(encoding="utf-8")))
                         if isinstance(node, ast.FunctionDef)}
    assert "Popen" not in source and "taskkill" not in source and "killpg" not in source


def test_the_gate_lists_exactly_the_four_modules_and_loads_nothing_else() -> None:
    from tools import execution_gate

    assert tuple(execution_gate.EXEC_MODULES) == EXEC_MODULES
    assert execution_gate.execution_available() is True
    assert execution_gate.default_profile() == "local-pilot-v1"
    assert execution_gate.load("exec_stand").__name__ == "tools.exec_stand"
    for name in ("pilot_state", "os", "exec_unknown", "tools.exec_stand", "../exec_stand", ""):
        with pytest.raises(ValueError):
            execution_gate.load(name)


def test_the_gate_names_in_one_sentence_that_the_profile_is_the_presence_of_the_files() -> None:
    doc = ast.get_docstring(ast.parse((TOOLS / f"{GATE}.py").read_text(encoding="utf-8"))) or ""
    assert "presence" in doc and "exec_" in doc


# --- a delivery without the four files --------------------------------------------------------------------------------

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
        [sys.executable, "-c", "from tools import execution_gate as g; print(g.execution_available(), g.default_profile(), g.load('exec_stand'))"],
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


@pytest.mark.parametrize("command", ["exec", "rerun-retained"])
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


def test_finish_cannot_check_the_stand_there(cases_only_pack: Path, tmp_path: Path) -> None:
    """``finish --abandon STAND_UNAVAILABLE`` asks the stand module; without it the fact is undetermined, never assumed."""
    source = (TOOLS / "finalize_attempt.py").read_text(encoding="utf-8")
    assert "execution_gate" in source and "FINISH_FACT_UNDETERMINED" in source


# --- the full package keeps the CLI one module ------------------------------------------------------------------------

def test_a_stop_of_an_execution_command_is_a_json_answer_under_python_m(tmp_path: Path) -> None:
    """``python -m tools.run_pipeline`` runs the file as ``__main__``; the HostStop of ``exec_commands`` is the class of
    ``tools.run_pipeline`` — the file must hand over to that module, or the stop would be a traceback."""
    project = _python_project(tmp_path, runtime=False)
    completed = subprocess.run([sys.executable, "-m", "tools.run_pipeline", "exec", "--project", str(project), "--run", "RUN-missing"],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
    payload = _json(completed.stdout)
    assert payload["status"] == "error" and payload["reason"] != "INTERNAL"
