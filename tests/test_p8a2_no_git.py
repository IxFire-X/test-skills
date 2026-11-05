"""P8a-2 B: a run of ``cases-only-v1`` never starts ``git`` — in the repository build and in the delivery of cases alike.

``git status`` can run a clean filter or a hook of the project's own configuration, so the cases-only profile does not call it:
the manifest of such a run says ``project_state: null`` ("not taken"), the drift of the project is the inventory's.  V4a: no profile
starts ``git`` any more (``tools/exec_git.py`` is gone; see ``test_p8b_git``).  The profile of cases has no process, network or library-loading
primitive, and one ``ctypes`` (the Windows calls of the confined writer)."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_first_run import _python_project

ROOT = Path(__file__).resolve().parents[1]
DOCS = "services/api/docs/feature.md"
SITECUSTOMIZE = (
    "import os, subprocess\n"
    "_init = subprocess.Popen.__init__\n"
    "def _logged(self, args, *a, **k):\n"
    "    with open(os.environ['P8A2_POPEN_LOG'], 'a', encoding='utf-8') as log:\n"
    "        log.write(repr(args) + chr(10))\n"
    "    return _init(self, args, *a, **k)\n"
    "subprocess.Popen.__init__ = _logged\n"
)


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tools import pack_registry

    pack = tmp_path_factory.mktemp("cases-only") / "pack"
    paths = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only")
    for path in (*paths, "release/manifest.json"):
        (pack / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, pack / path)
    return pack


def _git_project(tmp_path: Path) -> tuple[Path, Path]:
    """A python project in git whose configuration names a hook for git status (the hook leaves a marker)."""
    project = _python_project(tmp_path, runtime=False)
    marker = tmp_path / "hook-ran.txt"
    hook = tmp_path / "hook.sh"
    hook.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\nexit 1\n', encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "config", "core.fsmonitor", hook.as_posix()], check=True)
    (project / "dirty.txt").write_text("dirty", encoding="utf-8")
    subprocess.run(["git", "-c", f"safe.directory={project.as_posix()}", "-C", str(project), "status", "--porcelain"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not marker.exists():
        pytest.skip("this git does not run core.fsmonitor hooks")
    marker.unlink()
    return project, marker


def _json(text: str) -> dict:
    decoder, found, position = json.JSONDecoder(), None, 0
    while (position := text.find("{", position)) != -1:
        try:
            found, end = decoder.raw_decode(text, position)
        except ValueError:
            position += 1
            continue
        position = end
    assert found is not None, text
    return found


def _run(pack: Path, log: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    site = log.parent / "site"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "P8A2_POPEN_LOG": str(log), "PYTHONPATH": str(site)}
    return subprocess.run([sys.executable, "-m", *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8", env=environment)


@pytest.mark.parametrize("build", ["universal", "cases-only"])
def test_a_cases_only_run_in_a_git_project_starts_no_process_and_takes_no_project_state(tmp_path: Path, cases_only_pack: Path, build: str) -> None:
    pack = ROOT if build == "universal" else cases_only_pack
    project, marker = _git_project(tmp_path)
    log = tmp_path / "popen.log"
    log.write_text("", encoding="utf-8")
    ready = _run(pack, log, "tools.ready", "--project", str(project), "--docs", DOCS, "--module", "nested", "--profile", "cases-only-v1")
    rows = {row["id"]: row for row in _json(ready.stdout)["checks"]}
    assert rows["git"]["level"] == "OK" and "git не вызывается" in rows["git"]["message"], rows["git"]
    scan = _run(pack, log, "tools.run_pipeline", "scan", "--project", str(project), "--docs", DOCS, "--module", "nested", "--profile", "cases-only-v1")
    assert scan.returncode == 0, scan.stdout + scan.stderr
    run_id = _json(scan.stdout)["run_id"]
    status = _run(pack, log, "tools.run_pipeline", "status", "--project", str(project), "--run", run_id)
    assert status.returncode in {0, 3}, status.stdout + status.stderr
    assert log.read_text(encoding="utf-8") == "", "no process was started"
    assert not marker.exists(), "the hook of the project's configuration did not run"
    manifest = json.loads((project / ".pilot-runs" / run_id / "run-manifest.json").read_text(encoding="utf-8"))
    assert "project_state" in manifest and manifest["project_state"] is None


def test_the_drift_of_a_cases_only_run_is_the_inventorys_and_names_the_path(tmp_path: Path, cases_only_pack: Path) -> None:
    project, _marker = _git_project(tmp_path)
    log = tmp_path / "popen.log"
    log.write_text("", encoding="utf-8")
    scan = _run(cases_only_pack, log, "tools.run_pipeline", "scan", "--project", str(project), "--docs", DOCS, "--module", "nested", "--profile", "cases-only-v1")
    run_id = _json(scan.stdout)["run_id"]
    changed = project / DOCS
    changed.write_bytes(changed.read_bytes() + b"\nchanged after the scan\n")
    status = _run(cases_only_pack, log, "tools.run_pipeline", "status", "--project", str(project), "--run", run_id)
    assert DOCS in status.stdout and "changed" in status.stdout.lower(), status.stdout + status.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_the_project_state_of_a_run_starts_no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    project, _marker = _git_project(tmp_path)

    def refuse(*_args, **_kwargs):
        raise AssertionError("git must not be started")

    monkeypatch.setattr(subprocess, "run", refuse)
    assert pilot_state._project_state_for("cases-only-v1", project) is None
    assert pilot_state._project_state_for("local-pilot-v1", project) is None  # V4a: the tree is compared by the inventory at the end


def test_a_null_project_state_is_valid_and_is_not_a_scenario_observation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The readers of ``project_state``: the observation of a ``cases-only-v1`` run (``None`` is "not taken": no observation is
    published, the answer is ``None``) and its validation.  A ``None`` state of a ``local-pilot-v1`` run is the precondition of
    ``tree-preservation`` (V4a), which a cases-only attempt never takes."""
    from tools import pilot_state
    from tools.schema_validation import load_json_strict, schema_diagnostics

    manifest = {"schema_version": "1.0.0", "run_id": "a" * 32, "project": str(tmp_path), "policy_profile": "cases-only-v1",
                "authorization_digest": "sha256:" + "0" * 64, "project_state": None}
    manifest["digest"] = pilot_state._sealed({key: value for key, value in manifest.items()})["digest"]
    assert not schema_diagnostics(manifest, ROOT / "schemas" / "run-manifest.schema.json", ROOT)

    attempt = {"attempt_id": "b" * 32, "state": "TERMINAL"}
    monkeypatch.setattr(pilot_state, "_run_root", lambda run_root: (tmp_path, tmp_path))
    monkeypatch.setattr(pilot_state, "derive_state", lambda root: {"attempts": [attempt], "events": []})
    monkeypatch.setattr(pilot_state, "read_terminal_result", lambda root, attempt_id: {"accepted": True, "verification": "PASS", "digest": "sha256:" + "1" * 64})
    monkeypatch.setattr(pilot_state, "read_run", lambda root: {"manifest": manifest})
    assert pilot_state.publish_project_state_observation(tmp_path, "b" * 32) is None
    assert load_json_strict  # the strict reader is the one the run boundary uses for the manifest


# ---- the gate lists two files -------------------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["exec_commands", "exec_runner"])
def test_the_execution_is_available_only_with_both_files(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    from tools import execution_gate

    assert tuple(execution_gate.EXEC_MODULES) == ("exec_commands", "exec_runner")
    assert execution_gate.execution_available() is True
    original = execution_gate._present
    monkeypatch.setattr(execution_gate, "_present", lambda name: False if name == missing else original(name))
    assert execution_gate.execution_available() is False and execution_gate.default_profile() == "cases-only-v1"


def test_every_exec_file_on_disk_is_listed_by_the_gate_and_left_out_of_the_cases_only_profile() -> None:
    from tools import execution_gate, pack_registry

    on_disk = sorted(path.stem for path in (ROOT / "tools").glob("exec_*.py"))
    assert on_disk == sorted(execution_gate.EXEC_MODULES), "a new exec_ file must be in EXEC_MODULES (and then it is left out of the profile)"
    assert all(pack_registry.is_cases_only_excluded(f"tools/{name}.py") for name in on_disk)
    # F6: the gate stays in the profile (the core reaches the left-out files only through it); every file of its closed lists is left out
    assert not pack_registry.is_cases_only_excluded("tools/execution_gate.py")
    for listed in (execution_gate.MODE_B_MODULES, execution_gate.LEGACY_MODULES):
        assert all(pack_registry.is_cases_only_excluded(f"tools/{name}.py") for name in listed), listed


# ---- the profile of cases has no launch, network or library loading --------------------------------------------------

FORBIDDEN_IMPORTS = {"subprocess", "socket", "ssl", "signal", "http.client", "urllib.request", "multiprocessing", "pty"}
LOADING_NAMES = {"LoadLibrary", "CDLL", "WinDLL", "OleDLL", "PyDLL", "cdll", "oledll", "pydll", "find_library", "dlopen", "LoadLibraryW", "LoadLibraryA"}
WIN32_LIBRARIES = {"kernel32", "ntdll"}


def _profile_files() -> list[str]:
    from tools import pack_registry

    return [path for path in pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only") if path.endswith(".py")]


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_cases_only_profile_has_no_process_network_or_signal_primitive() -> None:
    offenders = []
    for path in _profile_files():
        names = _imports(ast.parse((ROOT / path).read_text(encoding="utf-8")))
        offenders += [f"{path}: {name}" for name in sorted(names) if any(name == item or name.startswith(item + ".") for item in FORBIDDEN_IMPORTS)]
    assert offenders == []


def test_ctypes_is_in_one_file_of_the_profile_with_fixed_win32_libraries_and_no_loading_by_name() -> None:
    with_ctypes, loading = [], []
    for path in _profile_files():
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        if any(name == "ctypes" or name.startswith("ctypes.") for name in _imports(tree)):
            with_ctypes.append(path)
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name in LOADING_NAMES:
                loading.append(f"{path}: {name}")
    assert with_ctypes == ["tools/confined_output.py"] and loading == []
    libraries = {node.attr for node in ast.walk(ast.parse((ROOT / "tools" / "confined_output.py").read_text(encoding="utf-8")))
                 if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "windll"}
    assert libraries == WIN32_LIBRARIES


def test_the_core_never_imports_an_execution_file() -> None:
    assert not (ROOT / "tools" / "exec_git.py").exists()  # V4a: the one place that ran git is gone
    for path in sorted((ROOT / "tools").glob("*.py")):
        if path.name.startswith("exec_"):
            continue
        assert not any(name.startswith("tools.exec_") for name in _imports(ast.parse(path.read_text(encoding="utf-8")))), path.name
