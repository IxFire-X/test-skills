"""FaT-R: from the folder to the first model task — every problem at once, the real cause, cases without a build.

``python -m tools.ready`` checks everything before a run and never stops at the first problem; the doctor names a
missing dependency instead of an invalid contract; a local-pilot run without its build facts becomes a cases-only run
that says why; a missing Maven wrapper falls back to the system ``mvn``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_phase2_scan import _args, _skillsrc
from tools import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
POM = """<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>app</artifactId><version>1</version>
<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version>
<scope>test</scope></dependency></dependencies></project>
"""


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _python_project(tmp_path: Path, *, runtime: bool) -> Path:
    project = tmp_path / "project"
    module = project / "services" / "api"
    _write(module, "src/app.py", "X = 1\n")
    _write(module, "pyproject.toml", "[project]\nname = 'api'\n")
    _write(project, "docs/feature.md", "Пользователь видит список.\n")
    (project / ".skillsrc").write_bytes(_skillsrc())
    if runtime:
        _write(module, ".venv/Scripts/python.exe", "fixture-runtime-v1")
    return project


def _java_project(tmp_path: Path) -> Path:
    project = tmp_path / "java"
    _write(project, "pom.xml", POM)
    _write(project, "src/main/java/x/App.java", "package x;\npublic class App {}\n")
    _write(project, "src/test/java/x/AppTest.java", "package x;\nclass AppTest {}\n")
    _write(project, "docs/feature.md", "Требование.\n")
    return project


def _fake_mvn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    launcher = _write(bin_dir, "mvn.cmd" if os.name == "nt" else "mvn", "@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
    if os.name != "nt":
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return launcher


def _levels(report: dict) -> dict[str, str]:
    return {check["id"]: check["level"] for check in report["checks"]}


# --------------------------------------------------------------------------------------
# ready: every problem at once
# --------------------------------------------------------------------------------------

def test_ready_reports_every_problem_in_one_run_with_a_fix_each(tmp_path: Path) -> None:
    from tools.ready import check

    project = _python_project(tmp_path, runtime=False)
    document = json.loads(json.dumps(__import__("tools.skillsrc_manifest", fromlist=["x"]).parse_skillsrc_bytes(_skillsrc())))
    second = json.loads(json.dumps(document["modules"][0]))
    second.update(id="web", root="services/web", detected_from=["services/web/pyproject.toml"])
    document["modules"].append(second)
    _write(project, "services/web/pyproject.toml", "[project]\nname = 'web'\n")
    from tools.init_skillsrc import _payload

    (project / ".skillsrc").write_bytes(_payload(document))
    report = check(project, docs=["docs/missing.md", "../outside.md", "docs/feature.md"], profile="local-pilot-v1")
    assert report["status"] == "BLOCKED"
    stops = [item for item in report["checks"] if item["level"] == "STOP"]
    assert {item["id"] for item in stops} >= {"docs:docs/missing.md", "docs:../outside.md", "module"}
    assert all(item.get("fix") for item in stops)
    module = next(item for item in stops if item["id"] == "module")
    assert "nested" in module["message"] and "web" in module["message"] and "--module" in module["fix"]
    assert _levels(report)["docs:docs/feature.md"] == "OK"


def test_ready_lets_cases_start_without_a_build(tmp_path: Path) -> None:
    from tools.ready import check

    project = _python_project(tmp_path, runtime=False)
    report = check(project, docs=["docs/feature.md"], profile="local-pilot-v1", module="nested")
    assert report["status"] == "READY", report
    execution = [item for item in report["checks"] if item["block"] == "execution"]
    assert execution and any(item["level"] == "WARN" and item.get("fix") for item in execution)
    scan = next(item for item in report["checks"] if item["id"] == "scan")
    assert scan["details"]["files"] >= 2 and scan["details"]["seconds"] >= 0


def test_ready_cli_exit_code_and_table(tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=False)
    ok = subprocess.run([sys.executable, "-m", "tools.ready", "--project", str(project), "--docs", "docs/feature.md", "--module", "nested"],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads(ok.stdout)["status"] == "READY" and "ВНИМАНИЕ" in ok.stderr
    bad = subprocess.run([sys.executable, "-m", "tools.ready", "--project", str(project), "--docs", "docs/missing.md", "--module", "nested"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert bad.returncode == 2 and "СТОП" in bad.stderr


def test_ready_runs_without_jsonschema_and_yaml(tmp_path: Path) -> None:
    project = _python_project(tmp_path, runtime=False)
    script = ("import runpy, sys\nsys.modules['jsonschema'] = None\nsys.modules['yaml'] = None\n"
              f"sys.argv = ['ready', '--project', {str(project)!r}, '--docs', 'docs/feature.md']\n"
              "runpy.run_module('tools.ready', run_name='__main__')\n")
    completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert "Traceback" not in completed.stderr, completed.stderr[-3000:]
    report = json.loads(completed.stdout)
    deps = next(item for item in report["checks"] if item["id"] == "deps")
    assert deps["level"] == "STOP" and "--require-hashes" in deps["fix"] and "--no-cache-dir" in deps["fix"]
    levels = _levels(report)
    assert levels["python"] == "OK" and levels["git"] == "OK" and levels["docs:docs/feature.md"] == "OK"
    assert completed.returncode == 2


def test_ready_checks_git_exactly_like_the_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import ready

    project = _python_project(tmp_path, runtime=False)
    assert ready.check_git(project)["level"] == "OK" and "не git" in ready.check_git(project)["message"]
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert ready.check_git(project)["level"] == "OK"

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(ready.subprocess, "run", missing)
    result = ready.check_git(project)
    assert result["level"] == "STOP" and result.get("fix")


def test_ready_offers_the_system_mvn_for_a_missing_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import _payload
    from tools.ready import check

    project = _java_project(tmp_path)
    document = {
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "java"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "root", "root": ".", "stack": {"language": "java", "build_tool": "maven"},
                     "paths": {"source": ["src/main/java"], "tests": ["src/test/java"]},
                     "test": {"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1",
                              "wrapper": "mvnw.cmd" if os.name == "nt" else "mvnw", "build_profile": "default", "adapter_parameters": {}},
                     "detected_from": ["pom.xml"]}],
    }
    (project / ".skillsrc").write_bytes(_payload(document))
    _fake_mvn(tmp_path, monkeypatch)
    report = check(project, docs=["docs/feature.md"], profile="local-pilot-v1")
    switch = next(item for item in report["checks"] if item["id"] == "execution:skillsrc-switch")
    assert switch["level"] == "WARN" and "maven:selected-symbols-v1" in json.dumps(switch["details"]["diff"])
    assert "--replacement-approval" in switch["fix"] and switch["details"]["approval"]["proposed_digest"].startswith("sha256:")
    assert report["status"] == "READY"


def test_a_gradle_project_without_wrapper_is_a_warning(tmp_path: Path) -> None:
    from tools.ready import execution_facts

    project = tmp_path / "gradle"
    _write(project, "build.gradle", "dependencies { testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0' }\n")
    module = {"id": "root", "root": ".", "stack": {"language": "java", "build_tool": "gradle"},
              "test": {"framework": "junit5", "adapter_id": "gradle-wrapper:selected-symbols-v1", "wrapper": "gradlew",
                       "build_profile": "default", "adapter_parameters": {}}}
    facts = execution_facts(project, module)
    assert not facts["ready"] and "gradle" in facts["reason"] and "wrapper" in facts["fix"]


# --------------------------------------------------------------------------------------
# doctor names the real cause
# --------------------------------------------------------------------------------------

def test_doctor_without_jsonschema_names_it_and_the_pip_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    from tools import doctor

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *rest: None if name == "jsonschema" else real(name, *rest))
    report = doctor.inspect_environment(ROOT)
    assert report["status"] == "NOT_RUNNABLE"
    assert not [item for item in report["integrity"]["missing"] if item.startswith("invalid:")], report["integrity"]
    message = " ".join(report["messages"])
    assert "jsonschema" in message and "--require-hashes" in message and "--no-cache-dir" in message


# --------------------------------------------------------------------------------------
# cases without a build; system mvn
# --------------------------------------------------------------------------------------

def test_a_local_pilot_scan_without_build_facts_runs_cases_only_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=False)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "local-pilot-v1"
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
    scan = payloads[-1]
    assert scan["policy_profile"] == "cases-only-v1" and scan["requested_policy_profile"] == "local-pilot-v1"
    assert scan["execution_skipped"]["reason"] and scan["execution_skipped"]["fix"]
    manifest = json.loads((Path(scan["run_root"]) / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["policy_profile"] == "cases-only-v1" and manifest["requested_policy_profile"] == "local-pilot-v1"
    assert manifest["execution_skipped"] == scan["execution_skipped"]
    status: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", status.append)
    run_pipeline.cmd_status(type("A", (), {"project": str(project), "run": scan["run_id"]})())
    assert status[-1]["requested_policy_profile"] == "local-pilot-v1" and status[-1]["execution_skipped"] == scan["execution_skipped"]


def test_a_local_pilot_scan_with_build_facts_stays_local_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=True)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project)) == 0, payloads[-1]
    assert payloads[-1]["policy_profile"] == "local-pilot-v1" and "execution_skipped" not in payloads[-1]


def test_a_maven_project_without_wrapper_gets_the_system_mvn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    project = _java_project(tmp_path)
    _fake_mvn(tmp_path, monkeypatch)
    receipt = ensure_skillsrc(project, {}, write=True)
    assert receipt["status"] in {"created", "updated"}, receipt
    text = (project / ".skillsrc").read_text(encoding="utf-8")
    assert "maven:selected-symbols-v1" in text and "mvnw" not in text


def test_a_lowered_run_cannot_start_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cases-only run made from a local-pilot request has no execution authorization: exec refuses, no test is written."""
    project = _python_project(tmp_path, runtime=False)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(_args(project)) == 0
    scan = payloads[-1]
    assert scan["policy_profile"] == "cases-only-v1"
    authorization = json.loads((Path(scan["run_root"]) / "run-authorization-receipt.json").read_text(encoding="utf-8"))
    assert authorization["execution_requested"] is False
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    completed = subprocess.run([sys.executable, "-m", "tools.run_pipeline", "exec", "--project", str(project), "--run", scan["run_id"]],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    payload = json.loads(completed.stdout)
    assert completed.returncode == 2 and payload["status"] == "error" and payload["reason"] == "EXECUTION_COORDINATES", payload
    after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*") if ".pilot-runs" not in path.parts)
    assert after == before
