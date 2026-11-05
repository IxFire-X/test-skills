"""P8a-2: the registry by delivery profile, the small ``doctor``, one place for the package version, ``ci_gate --pack-only``.

A pack is ``universal`` (the repository) or ``cases-only`` (no ``tools/exec_*.py``, no maintainer files, no tool of development);
which one is told by the exact registry files on disk, and a copy that is neither is refused."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
DELIVERY_DOCS = ("release/manifest.json", "README.md", "HOW-TO-USE.md")


def copy_profile(destination: Path, profile: str) -> Path:
    """The files of ``profile`` (registry files by the profile, plus the manifest and the two texts), taken from the working tree."""
    from tools import pack_registry

    paths = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], profile)
    for path in (*paths, *DELIVERY_DOCS):
        (destination / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination / path)
    return destination


def _doctor(pack: Path) -> dict:
    from tools.doctor import inspect_environment

    return inspect_environment(pack)


def _cli(module: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *arguments], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


# ---- the profile ---------------------------------------------------------------------------------------------------

def test_the_registry_carries_the_generated_documents_and_the_cases_only_profile_leaves_out_what_it_must(pack_root: Path) -> None:
    manifest = json.loads((ROOT / "release" / "manifest.json").read_text(encoding="utf-8"))
    paths = {row["path"] for row in manifest["registry"]}
    assert {"CONTRACTS.md", "PIPELINE.md"} <= paths and not paths & {"README.md", "HOW-TO-USE.md", "USAGE.md", "requirements.txt"}
    from tools import pack_registry

    kept = set(pack_registry.profile_paths(paths, "cases-only"))
    left = paths - kept
    assert {"tools/ci_gate.py", "tools/compatibility_contract.py", "tools/contract_check.py", "tools/render_contract_docs.py",
            "tools/release_manifest.py", "evals/release_eval.py", "evals/scenarios/pilot-critical.json",
            "schemas/release-eval-run.schema.json", "schemas/release-eval-receipt.schema.json",
            "schemas/compatibility-evidence.schema.json", "schemas/scenario-observation-receipt.schema.json"} <= left
    assert not [path for path in kept if path.startswith("evals/")] and {"CONTRACTS.md", "PIPELINE.md", "tools/doctor.py", "tools/pack_registry.py"} <= kept
    assert manifest["delivery_profiles"][0]["skill_pack_digest"] == pack_registry.digest_bytes(
        pack_registry.canonical([row for row in manifest["registry"] if row["path"] in kept]))


def test_every_exec_module_is_left_out_and_the_gate_of_the_core_is_not() -> None:
    from tools import pack_registry

    names = ["tools/exec_commands.py", "tools/exec_runner.py"]
    assert all(pack_registry.is_cases_only_excluded(name) for name in names)
    # F6: the nine modules of mode B are left out as well; the gate and the Java lexer they share with mode A stay
    assert not any(pack_registry.is_cases_only_excluded(name) for name in ("tools/execution_gate.py", "tools/java_text.py"))
    assert all(pack_registry.is_cases_only_excluded(f"tools/{name}.py") for name in (
        "run_tests", "generated_delta", "automation_validation", "execution_adapters", "autotest_hints", "trace_check",
        "build_trace_document", "quarantine", "code_slices"))
    assert pack_registry.profile_paths([*names, "tools/api.py"], "cases-only") == ["tools/api.py"]


def test_a_cases_only_copy_is_recognised_whole_and_passes_the_three_checks_and_the_gate(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    report = _doctor(pack)
    assert report["status"] == "PASS" and report["profile"] == "cases-only" and report["integrity"] == {"valid": True, "missing": []}, report
    assert "languages" not in report and "adapters" not in report  # PT2: nothing to say about running projects
    for module, arguments in (("tools.contract_check", ("--full",)), ("tools.render_contract_docs", ("--check",)), ("tools.release_manifest", ("--check",))):
        done = _cli(module, "--root", str(pack), *arguments)
        assert done.returncode == 0, (module, done.stdout, done.stderr)
    gate = _cli("tools.ci_gate", "--root", str(pack), "--pack-only")
    assert gate.returncode == 0, (gate.stdout, gate.stderr)


def test_the_repository_is_the_universal_pack_and_still_passes() -> None:
    report = _doctor(ROOT)
    assert report["status"] == "PASS" and report["profile"] == "universal" and report["integrity"]["missing"] == [], report


def test_a_pack_without_a_manifest_says_so_in_one_line(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / "release" / "manifest.json").unlink()
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and report["integrity"]["missing"] == ["release/manifest.json"]
    assert "release/manifest.json" in " ".join(report["messages"])


# ---- the exact set of files ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", ["tools/evil.py", "tools/exec_commands.py", "tools/notes.txt", "skills/orchestrate/run.sh", "evals/release_eval.py", "tools/x.pyc",
                                   "schemas/extra.schema.json"])
def test_an_extra_file_is_refused_with_its_name_and_a_fix(tmp_path: Path, extra: str) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / extra).parent.mkdir(parents=True, exist_ok=True)
    (pack / extra).write_bytes(b"print('x')\n")
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and "invalid:release/manifest.json" in report["integrity"]["missing"], report
    text = " ".join(report["messages"])
    assert "match no delivery profile" in text and f"лишний файл {extra}: удалите его" in text, text


def _import_for_real(pack: Path, *flags: str) -> None:
    """The ordinary import of a few modules, with bytecode writing on (as at a tester's): Python makes the ``__pycache__`` itself."""
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    done = subprocess.run([sys.executable, *flags, "-c", "import tools.api, tools.doctor, tools.output_guard, tools.pack_registry"], cwd=pack, env=environment,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _plant(pack: Path, module: str = "output_guard") -> Path:
    """The probe of the orchestrator: the header (mtime, size) of the real source, the code of another text."""
    import importlib.util
    import marshal
    import struct

    source = pack / "tools" / f"{module}.py"
    code = compile(source.read_text(encoding="utf-8") + "\nPLANTED = True\n", str(source), "exec")
    stat = source.stat()
    cache = pack / "tools" / "__pycache__"
    cache.mkdir(exist_ok=True)
    target = cache / f"{module}.{sys.implementation.cache_tag}.pyc"
    target.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF) + marshal.dumps(code))
    return target


def test_the_pycache_python_makes_itself_passes_for_every_optimisation_level(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    _import_for_real(pack)
    _import_for_real(pack, "-O")
    _import_for_real(pack, "-OO")
    names = sorted(path.name for path in (pack / "tools" / "__pycache__").glob("*.pyc"))
    assert any(".opt-1." in name for name in names) and any(".opt-2." in name for name in names) and len(names) > 6, names
    report = _doctor(pack)
    assert report["status"] == "PASS" and report["integrity"]["missing"] == [], report


def test_a_planted_pyc_with_the_header_of_the_real_source_is_refused_and_the_cache_directory_named(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    _import_for_real(pack)
    assert _doctor(pack)["status"] == "PASS"
    planted = _plant(pack)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    ran = subprocess.run([sys.executable, "-c", "import tools.output_guard as g; print(getattr(g, 'PLANTED', False))"], cwd=pack, env=environment,
                         capture_output=True, text=True)
    assert ran.stdout.strip() == "True", "the probe is real: Python runs the planted code instead of the source"
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and "invalid:release/manifest.json" in report["integrity"]["missing"], report
    text = " ".join(report["messages"])
    assert "tools/__pycache__/output_guard." in text and "удалите каталог tools/__pycache__" in text, text
    planted.unlink()
    assert _doctor(pack)["status"] == "PASS"


def test_a_pyc_in_the_pycache_without_a_source_is_refused(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    _import_for_real(pack)
    cache = pack / "tools" / "__pycache__"
    shutil.copyfile(cache / f"api.{sys.implementation.cache_tag}.pyc", cache / f"ghost.{sys.implementation.cache_tag}.pyc")
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and "tools/__pycache__/ghost." in " ".join(report["messages"]), report


@pytest.mark.parametrize("damage", ["magic", "truncated", "garbage"])
def test_a_damaged_pyc_of_this_python_is_refused_without_a_traceback(tmp_path: Path, damage: str) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    _import_for_real(pack)
    target = pack / "tools" / "__pycache__" / f"api.{sys.implementation.cache_tag}.pyc"
    data = target.read_bytes()
    target.write_bytes({"magic": b"\0\0\0\0" + data[4:], "truncated": data[:40], "garbage": data[:16] + b"\xff" * 64}[damage])
    assert _doctor(pack)["status"] == "NOT_RUNNABLE"


def test_a_pyc_of_another_python_is_left_alone_because_this_python_never_reads_it(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    cache = pack / "tools" / "__pycache__"
    cache.mkdir()
    (cache / "api.cpython-39.pyc").write_bytes(b"\0")
    (cache / "api.cpython-310.opt-1.pyc").write_bytes(b"\0")
    assert _doctor(pack)["status"] == "PASS"


def test_a_sourceless_pyc_outside_pycache_is_refused(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / "tools" / "api.pyc").write_bytes(b"\0")  # `import tools.api` would take it when the source is gone
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and "лишний файл tools/api.pyc: удалите его" in " ".join(report["messages"]), report


@pytest.mark.parametrize("name", ["notes.txt", "evil.so", "api.py", "x.cpython-notpyc.pyc"])
def test_anything_but_a_cpython_pyc_in_pycache_is_refused(tmp_path: Path, name: str) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / "tools" / "__pycache__").mkdir()
    (pack / "tools" / "__pycache__" / name).write_bytes(b"x")
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE", report
    assert f"tools/__pycache__/{name}" in " ".join(report["messages"])


def test_a_missing_file_and_a_changed_byte_are_refused(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / "tools" / "api.py").unlink()
    report = _doctor(pack)
    assert report["status"] == "NOT_RUNNABLE" and "tools/api.py" in " ".join(report["messages"]), report
    pack = copy_profile(tmp_path / "pack2", "cases-only")
    (pack / "skills" / "tc-generator" / "SKILL.md").write_bytes((pack / "skills" / "tc-generator" / "SKILL.md").read_bytes() + b"x")
    assert "does not match runtime bytes" in " ".join(_doctor(pack)["messages"])


def test_a_copy_that_is_half_of_each_profile_is_refused_not_matched_to_the_nearest(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    (pack / "tools" / "release_manifest.py").write_text("", encoding="utf-8")  # one file of the universal profile only
    assert "match no delivery profile" in " ".join(_doctor(pack)["messages"])


def test_the_doctor_writes_nothing_and_starts_nothing(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    before = sorted((path.relative_to(pack).as_posix(), path.stat().st_mtime_ns) for path in pack.rglob("*") if path.is_file())
    _doctor(pack)
    assert sorted((path.relative_to(pack).as_posix(), path.stat().st_mtime_ns) for path in pack.rglob("*") if path.is_file()) == before
    imports = {node.names[0].name.split(".")[0] for node in ast.walk(ast.parse((ROOT / "tools" / "doctor.py").read_text(encoding="utf-8"))) if isinstance(node, ast.Import)}
    registry_imports = {node.names[0].name.split(".")[0] for node in ast.walk(ast.parse((ROOT / "tools" / "pack_registry.py").read_text(encoding="utf-8"))) if isinstance(node, ast.Import)}
    assert not (imports | registry_imports) & {"subprocess", "socket", "urllib", "http", "shutil", "tempfile", "os"}


# ---- the profile is closed -----------------------------------------------------------------------------------------

def _imported_tools(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module.split(".")[0] == "tools":
            found.add(node.module)
            if node.module == "tools":
                found.update(f"tools.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.split(".")[0] == "tools")
    return found


def test_the_cases_only_files_import_and_name_only_files_of_the_profile() -> None:
    from tools import pack_registry

    kept = set(pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only"))
    problems = []
    for path in sorted(kept):
        if not path.endswith(".py"):
            continue
        text = (ROOT / path).read_text(encoding="utf-8")
        for module in _imported_tools(ROOT / path):
            if module != "tools" and f"{module.replace('.', '/')}.py" not in kept:
                problems.append((path, module))
        for schema in set(re.findall(r"schemas/([A-Za-z0-9_-]+\.schema\.json)", text)) if path != "tools/pack_registry.py" else ():  # it names what it leaves out
            if f"schemas/{schema}" not in kept:
                problems.append((path, f"schemas/{schema}"))
    assert problems == []


def test_a_cases_only_copy_imports_every_module_of_the_profile(tmp_path: Path) -> None:
    from tools import pack_registry

    pack = copy_profile(tmp_path / "pack", "cases-only")
    modules = [path[:-3].replace("/", ".") for path in pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only")
               if path.startswith("tools/") and path.endswith(".py") and path != "tools/__init__.py"]
    script = ("import importlib, sys\n"
              "blocked = ('tools.ci_gate', 'tools.contract_check', 'tools.compatibility_contract', 'tools.release_manifest', 'tools.render_contract_docs')\n"
              "for name in blocked: sys.modules[name] = None\n"
              "failed = []\n"
              "for name in sys.argv[1:]:\n"
              "    try: importlib.import_module(name)\n"
              "    except Exception as error: failed.append((name, repr(error)))\n"
              "print(failed)\n")
    done = subprocess.run([sys.executable, "-c", script, *modules], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={"PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.stdout.strip() == "[]", (done.stdout, done.stderr)


# ---- one place for the version -------------------------------------------------------------------------------------

def test_the_version_is_written_once_in_the_contract_and_read_from_there(tmp_path: Path) -> None:
    from tests.test_release_manifest import _copy_runtime
    from tools.release_manifest import build_release_manifest, verify_release_manifest

    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    version = contract["release_qualification"]["package_version"]
    literals = [path.relative_to(ROOT).as_posix() for folder in ("tools", "schemas", "skills", "evals") for path in (ROOT / folder).rglob("*")
                if path.is_file() and path.suffix in {".py", ".json", ".md"} and version in path.read_text(encoding="utf-8")]
    assert literals == [], literals

    pack = _copy_runtime(ROOT, tmp_path / "pack")
    path = pack / "contracts" / "pipeline.json"
    path.write_text(path.read_text(encoding="utf-8").replace(f'"package_version":"{version}"', '"package_version":"0.9.9-test"'), encoding="utf-8")
    manifest = build_release_manifest(pack)
    assert manifest["package_version"] == "0.9.9-test"
    assert verify_release_manifest(pack, manifest) == manifest
    forged = dict(manifest, package_version=version)
    from tools.pack_registry import manifest_digest

    forged["digest"] = manifest_digest(forged)
    with pytest.raises(ValueError, match="metadata"):
        verify_release_manifest(pack, forged)


def test_a_version_that_is_not_a_version_is_refused_by_the_contract_check(tmp_path: Path) -> None:
    from tests.test_release_manifest import _copy_runtime
    from tools.contract_check import validate_pipeline_contract

    pack = _copy_runtime(ROOT, tmp_path / "pack")
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    contract["release_qualification"]["package_version"] = "latest"
    assert validate_pipeline_contract(contract, pack)["status"] == "failed"


# ---- the list of a delivery ----------------------------------------------------------------------------------------

def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", "-c", "safe.directory=*", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false", "-C", str(repo), *arguments],
                   check=True, capture_output=True)


def test_the_list_of_a_delivery_is_the_index_by_profile_and_never_an_untracked_file(tmp_path: Path) -> None:
    from tools import pack_registry
    from tools.release_manifest import delivery_files

    repo = copy_profile(tmp_path / "repo", "universal")
    for extra in ("tests/test_x.py", "docs/guide.md", ".github/workflows/portable.yml", "USAGE.md", "RELEASE.md", "requirements.txt", ".skillsrc.example"):
        (repo / extra).parent.mkdir(parents=True, exist_ok=True)
        (repo / extra).write_text("x\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    (repo / "tools" / "draft.py").write_text("untracked\n", encoding="utf-8")
    cases = delivery_files(repo, "cases-only")
    assert cases == sorted(cases) and "tools/draft.py" not in cases
    assert {"README.md", "HOW-TO-USE.md", ".skillsrc.example", "requirements.txt", "release/manifest.json", "CONTRACTS.md", "PIPELINE.md"} <= set(cases)
    assert not [path for path in cases if path.startswith(("tests/", "docs/", ".github/", "evals/")) or path in {"USAGE.md", "RELEASE.md", "tools/ci_gate.py", "tools/release_manifest.py"}]
    universal = delivery_files(repo, "universal", "HEAD")
    assert "evals/release_eval.py" in universal and "tools/release_manifest.py" in universal and "tests/test_x.py" not in universal
    listed = set(cases) - set(DELIVERY_DOCS) - {".skillsrc.example", "requirements.txt"}
    assert listed == set(pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(repo)], "cases-only")) - {"tools/draft.py"}
    with pytest.raises(ValueError, match="invalid commit"):
        delivery_files(repo, "cases-only", "--output=x")
    (repo / "tools" / "blob.bin").write_bytes(b"\0")
    _git(repo, "add", "tools/blob.bin")
    with pytest.raises(ValueError, match="no pack may carry"):
        delivery_files(repo, "cases-only")


def test_the_list_refuses_an_archive_in_a_delivery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import release_manifest

    repo = copy_profile(tmp_path / "repo", "universal")
    (repo / "bundle.zip").write_bytes(b"PK")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x")
    monkeypatch.setattr(release_manifest, "DELIVERY_FILES", (*release_manifest.DELIVERY_FILES, "bundle.zip"))
    with pytest.raises(ValueError, match="no archive"):
        release_manifest.delivery_files(repo, "cases-only")


# ---- ci_gate --pack-only -------------------------------------------------------------------------------------------

def test_ci_gate_pack_only_runs_three_checks_and_the_doctor_from_this_repository_and_no_pytest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools import ci_gate

    calls = []
    monkeypatch.setattr(ci_gate.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))
    assert ci_gate.main(["--root", str(tmp_path), "--pack-only"]) == 0
    assert [command[2] for command, _ in calls] == ["tools.contract_check", "tools.render_contract_docs", "tools.release_manifest", "tools.doctor"]
    assert all(kwargs["cwd"] == ROOT and str(tmp_path.resolve()) in command for command, kwargs in calls) and not any("pytest" in command for command, _ in calls)


def test_ci_gate_pack_only_fails_on_a_changed_byte_and_on_an_extra_file(tmp_path: Path) -> None:
    pack = copy_profile(tmp_path / "pack", "cases-only")
    skill = pack / "skills" / "tc-generator" / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\n")
    assert _cli("tools.ci_gate", "--root", str(pack), "--pack-only").returncode != 0
    pack = copy_profile(tmp_path / "pack2", "cases-only")
    (pack / "tools" / "exec_commands.py").write_text("", encoding="utf-8")
    assert _cli("tools.ci_gate", "--root", str(pack), "--pack-only").returncode != 0


def test_ci_gate_is_not_in_the_cases_only_pack() -> None:
    from tools import pack_registry

    assert pack_registry.is_cases_only_excluded("tools/ci_gate.py")
