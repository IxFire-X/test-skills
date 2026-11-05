"""F6: the nine modules of mode B (generation and run of automated tests) are not in a cases-only delivery, and nothing in the delivery needs them.

``execution_gate.load_mode_b`` is the only way the core reaches them: a closed list, the module or ``ModeBMissing`` with a code
and a fix.  The checks run on the code (no import of the nine anywhere in the delivery, lazy or not) and on a process in which
the nine and the four ``exec_*`` files are blocked — the same as a delivery that does not carry them.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
MODE_B = ("run_tests", "generated_delta", "automation_validation", "execution_adapters", "autotest_hints", "trace_check",
          "build_trace_document", "quarantine", "code_slices")
EXEC = ("exec_commands", "exec_stand", "exec_probe", "exec_runner")
ABSENT = MODE_B + EXEC


def _delivery_files() -> list[Path]:
    from tools import pack_registry

    kept = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only")
    return [ROOT / path for path in kept if path.startswith("tools/") and path.endswith(".py")]


def _imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_the_nine_modules_are_listed_by_the_gate_and_left_out_of_a_cases_only_delivery() -> None:
    from tools import execution_gate, pack_registry

    assert tuple(execution_gate.MODE_B_MODULES) == MODE_B
    for name in MODE_B:
        assert (TOOLS / f"{name}.py").is_file(), name
        assert pack_registry.is_cases_only_excluded(f"tools/{name}.py"), name


def test_no_file_of_a_cases_only_delivery_imports_a_module_of_mode_b_lazily_or_not() -> None:
    offenders = []
    for path in _delivery_files():
        for name in MODE_B:
            if f"tools.{name}" in _imports_of(path):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_the_gate_loads_only_the_nine_and_says_with_a_code_when_one_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import execution_gate

    assert execution_gate.mode_b_available() is True
    for name in MODE_B:
        assert execution_gate.load_mode_b(name).__name__ == f"tools.{name}"
    for name in ("pilot_state", "os", "exec_stand", "tools.run_tests", "../run_tests", ""):
        with pytest.raises(ValueError):
            execution_gate.load_mode_b(name)
    monkeypatch.setitem(sys.modules, "tools.trace_check", None)  # as a delivery without the file
    assert execution_gate.mode_b_available() is False
    with pytest.raises(execution_gate.ModeBMissing) as caught:
        execution_gate.load_mode_b("trace_check")
    assert caught.value.code == "MODE_B_NOT_IN_PACKAGE" and "trace_check" in str(caught.value)
    from tools.fixes import fix_for

    assert fix_for("MODE_B_NOT_IN_PACKAGE")


def test_the_java_lexer_lives_in_its_own_module_and_both_users_take_it_from_there() -> None:
    from tools import java_text, run_tests

    assert run_tests._mask_java_noncode is java_text.mask_java_noncode
    assert "tools.run_tests" not in _imports_of(TOOLS / "existing_tests.py")
    assert "tools.java_text" in _imports_of(TOOLS / "existing_tests.py")
    assert java_text.mask_java_noncode("int x; // c\nString s = \"a{\";\n") == "int x;     \nString s =     ;\n"


# --- a process in which the nine and the four execution files are absent -----------------------------------------------

def _blocked(code: str, *argv: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    prelude = f"import sys\nfor name in {ABSENT!r}:\n    sys.modules['tools.' + name] = None\n"
    return subprocess.run([sys.executable, "-c", prelude + code, *argv], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_every_module_of_the_delivery_imports_without_the_nine() -> None:
    modules = [f"tools.{path.stem}" for path in _delivery_files() if path.stem != "__init__"]
    done = _blocked("import importlib\nfailed = []\nfor name in sys.argv[1:]:\n    try: importlib.import_module(name)\n"
                    "    except Exception as error: failed.append((name, repr(error)))\nprint(failed)\n", *modules)
    assert done.stdout.strip() == "[]", (done.stdout, done.stderr)


def test_every_command_of_api_and_of_the_pipeline_answers_help_without_the_nine() -> None:
    done = _blocked(
        "import contextlib, io\n"
        "from tools import api, run_pipeline\n"
        "bad = []\n"
        "def helped(call, *argv):\n"
        "    out = io.StringIO()\n"
        "    try:\n"
        "        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):\n"
        "            call(list(argv))\n"
        "    except SystemExit as stop:\n"
        "        if stop.code not in (0, None): bad.append((argv, stop.code, out.getvalue()[-200:]))\n"
        "    except Exception as error: bad.append((argv, repr(error)))\n"
        "helped(api.main, '--help')\n"
        "for command in api.COMMANDS: helped(api.main, command, '--help')\n"
        "helped(run_pipeline.main, '--help')\n"
        "for command in ('scan', 'status', 'finish', 'report'): helped(run_pipeline.main, command, '--help')\n"
        "print(sorted(api.COMMANDS)); print(bad)\n")
    assert done.returncode == 0, done.stderr
    commands, bad = done.stdout.strip().splitlines()[-2:]
    assert bad == "[]", bad
    for removed in ("automation-digest", "relations-digest", "validate-automation", "runner-compat"):
        assert removed not in commands


def _copy_delivery(destination: Path) -> Path:
    from tools import pack_registry

    for path in pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only"):
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    for name in ("release/manifest.json", "README.md", "HOW-TO-USE.md", ".skillsrc.example", "requirements.txt", "CONTRACTS.md", "PIPELINE.md"):
        if (ROOT / name).is_file():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination / name)
    return destination


def test_the_doctor_of_a_real_copy_of_the_delivery_passes_and_the_cli_starts(tmp_path: Path) -> None:
    pack = _copy_delivery(tmp_path / "pack")
    assert not [path for path in (pack / "tools").glob("*.py") if path.stem in MODE_B + EXEC]
    doctor = subprocess.run([sys.executable, "-m", "tools.doctor", "--root", str(pack)], cwd=pack, capture_output=True, text=True,
                            encoding="utf-8", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert doctor.returncode == 0, (doctor.stdout, doctor.stderr)
    report = json.loads(doctor.stdout)
    assert report["status"] == "PASS" and report.get("profile", "cases-only") == "cases-only", report
    for module in ("tools.api", "tools.run_pipeline", "tools.ready"):
        done = subprocess.run([sys.executable, "-m", module, "--help"], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        assert done.returncode == 0 and "ImportError" not in done.stderr, (module, done.stderr)


# --- the report of a cases-only run does not change by one byte ----------------------------------------------------------

def _command(tmp: Path, absent: bool, *argv: str) -> tuple[int, str]:
    script = ("from tools.run_pipeline import main\nraise SystemExit(main(sys.argv[1:]))\n")
    if absent:
        done = _blocked(script, *argv)
    else:
        done = subprocess.run([sys.executable, "-c", "import sys\n" + script, *argv], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return done.returncode, done.stdout


def test_status_report_and_finish_of_a_cases_only_run_are_byte_equal_with_and_without_the_nine(tmp_path: Path) -> None:
    """The early exits for a missing module of mode B give the same answers, bytes of the report included: the run is put back
    exactly between the two passes (a copy elsewhere would be a moved project)."""
    from tests.test_self_review import _reviewed

    project = tmp_path / "project"
    run_root, _attempt, _document, _selected = _reviewed(project, "cases-only-v1", flag=True)
    backup = tmp_path / "backup"
    shutil.copytree(project, backup)
    outputs = {}
    for label, absent in (("with", False), ("without", True)):
        row = {}
        for command in ("status", "report", "finish"):
            code, out = _command(project, absent, command, "--project", str(project), "--run", run_root.name)
            row[command] = (code, out)
        for name in ("report.json", "report.md", "questions-for-analyst.md"):
            row[name] = (run_root / name).read_bytes() if (run_root / name).exists() else None
        outputs[label] = row
        shutil.rmtree(project)
        shutil.copytree(backup, project)
    assert outputs["with"]["report"][0] == 0 and outputs["with"]["report.json"], outputs["with"]["report"]
    assert outputs["with"] == outputs["without"]
