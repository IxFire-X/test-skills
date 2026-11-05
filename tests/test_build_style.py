"""FaT-L: the project's build and its style checks.

The selected-tests run skips a closed list of the project's style, license-header and static-analysis checks (they never
change how a test behaves), Gradle reruns only the test task, ``ready`` starts nothing (P4b: no probe of the validate phase),
and the files left in the project get a "format them" hint.  The package itself carries no plain-http links.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_execution_adapters import _module
from tests.test_first_run import _write

ROOT = Path(__file__).resolve().parents[1]
PLAIN = "http" + "://"  # kept split so this file does not trip the guard below
SKIPS = (
    "-Dcheckstyle.skip=true", "-Dspring-javaformat.validate.skip=true", "-Dspotless.check.skip=true", "-Drat.skip=true",
    "-Dlicense.skip=true", "-Dlicense.skipCheckLicense=true", "-Dlicense.skipUpdateLicense=true", "-Dformatter.skip=true",
    "-Dimpsort.skip=true", "-Dpmd.skip=true", "-Dcpd.skip=true", "-Dspotbugs.skip=true",
)
PARENT_POM = """<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>parent</artifactId><version>1</version>
<packaging>pom</packaging><modules><module>app</module></modules>
<build><plugins>
  <plugin><groupId>org.apache.rat</groupId><artifactId>apache-rat-plugin</artifactId><version>0.16.1</version></plugin>
  <!-- <plugin><artifactId>maven-pmd-plugin</artifactId></plugin> is switched off -->
</plugins></build></project>
"""
APP_POM = """<project><modelVersion>4.0.0</modelVersion><parent><groupId>x</groupId><artifactId>parent</artifactId><version>1</version></parent>
<artifactId>app</artifactId>
<build><plugins>
  <plugin><groupId>io.spring.javaformat</groupId><artifactId>spring-javaformat-maven-plugin</artifactId><version>0.0.47</version>
    <executions><execution><phase>validate</phase><goals><goal>validate</goal></goals></execution></executions></plugin>
  <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-checkstyle-plugin</artifactId><version>3.6.0</version>
    <dependencies><dependency><groupId>io.spring.nohttp</groupId><artifactId>nohttp-checkstyle</artifactId><version>0.0.11</version></dependency></dependencies>
    <executions><execution><id>nohttp-checkstyle-validation</id><phase>validate</phase><goals><goal>check</goal></goals></execution></executions></plugin>
  <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-enforcer-plugin</artifactId><version>3.6.3</version></plugin>
</plugins></build></project>
"""


# --------------------------------------------------------------------------------------
# (в)(г) one closed command policy
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(("adapter_id", "runtime"), [("maven-wrapper:selected-symbols-v1", "mvnw"), ("maven:selected-symbols-v1", None)])
def test_maven_runs_in_batch_mode_and_skips_only_the_closed_style_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         adapter_id: str, runtime: str | None) -> None:
    from tools import execution_adapters
    from tools.execution_adapters import MAVEN_STYLE_SKIPS, build_request

    if runtime is None:
        module = _module(tmp_path, "maven-wrapper:selected-symbols-v1", "mvnw")
        executable = str((tmp_path / "mvnw").resolve())
        module["test"] = {"adapter_id": adapter_id, "executable": "mvn", "build_profile": "default", "adapter_parameters": {}}
        monkeypatch.setattr(execution_adapters, "_runtime", lambda *_args: Path(executable))
    else:
        module = _module(tmp_path, adapter_id, runtime)
        executable = str((tmp_path / runtime).resolve())
    request = build_request(adapter_id, module, [{"selector": "pkg.SampleTest#works"}])
    assert MAVEN_STYLE_SKIPS == SKIPS
    assert request.argv == (executable, "-B", "-Pdefault", *SKIPS, "-Dtest=pkg.SampleTest#works", "test")
    assert not [token for token in request.argv if "enforcer" in token]


def test_gradle_reruns_only_the_test_task(tmp_path: Path) -> None:
    from tools.execution_adapters import GRADLE, build_request

    request = build_request(GRADLE, _module(tmp_path, GRADLE, "gradlew"), [{"selector": "pkg.SampleTest#works"}])
    assert request.argv == (str((tmp_path / "gradlew").resolve()), "cleanTest", "test", "--no-build-cache", "-Pprofile=default",
                            "--tests", "pkg.SampleTest.works")
    assert "--rerun-tasks" not in request.argv


def test_the_receipt_policy_refuses_any_other_flag_set(tmp_path: Path) -> None:
    from tools.execution_adapters import GRADLE, MAVEN, build_request
    from tools.run_tests import _request_report_path

    maven = build_request(MAVEN, _module(tmp_path / "m", MAVEN, "mvnw"), [{"selector": "pkg.SampleTest#works"}])
    assert _request_report_path(maven) == (tmp_path / "m").resolve() / "target/surefire-reports"
    without_skip = tuple(token for token in maven.argv if token != "-Dcheckstyle.skip=true")
    with_enforcer_skip = (*maven.argv[:-1], "-Denforcer.skip=true", maven.argv[-1])
    for argv in (without_skip, with_enforcer_skip, tuple(token for token in maven.argv if token != "-B")):
        assert _request_report_path(replace(maven, argv=argv)) is None, argv
    gradle = build_request(GRADLE, _module(tmp_path / "g", GRADLE, "gradlew"), [{"selector": "pkg.SampleTest#works"}])
    rerun = (gradle.argv[0], "test", "--rerun-tasks", *gradle.argv[4:])
    cached = tuple(token for token in gradle.argv if token != "--no-build-cache")
    for argv in (rerun, cached):
        assert _request_report_path(replace(gradle, argv=argv)) is None, argv


# --------------------------------------------------------------------------------------
# what the project checks, read statically
# --------------------------------------------------------------------------------------

def _multi_module(tmp_path: Path) -> Path:
    project = tmp_path / "multi"
    _write(project, "pom.xml", PARENT_POM)
    _write(project, "app/pom.xml", APP_POM)
    return project


def test_style_checks_are_read_from_the_module_pom_and_its_parents(tmp_path: Path) -> None:
    from tools.execution_adapters import project_style_checks

    project = _multi_module(tmp_path)
    assert project_style_checks(project, project / "app") == ("spring-javaformat", "checkstyle", "nohttp", "apache-rat")
    assert project_style_checks(project, project) == ("apache-rat",)
    gradle = tmp_path / "gradle"
    _write(gradle, "build.gradle", "plugins {\n  id 'io.spring.javaformat' version '0.0.47'\n  id \"io.spring.nohttp\" version '0.0.11'\n}\n")
    assert project_style_checks(gradle, gradle) == ("spring-javaformat", "nohttp")
    plain = tmp_path / "plain"
    _write(plain, "pom.xml", "<project><artifactId>plain</artifactId></project>\n")
    assert project_style_checks(plain, plain) == ()


def test_style_notes_name_the_skipped_checks_and_the_format_command() -> None:
    from tools.execution_adapters import GRADLE, MAVEN, PYTEST, style_notes

    notes = style_notes(MAVEN, "/p/mvnw", ("spring-javaformat", "checkstyle", "nohttp"), ("src/test/java/x/GenTests.java",))
    assert notes["style_checks_skipped"] == ("проверки стиля проекта при запуске тестов пакета пропущены: "
                                             "spring-javaformat, checkstyle, nohttp")
    hint = notes["format_hint"]
    assert "src/test/java/x/GenTests.java" in hint and '"/p/mvnw" spring-javaformat:apply' in hint and '"/p/mvnw" -B validate' in hint
    unseen = style_notes(MAVEN, "/p/mvnw", (), ("src/test/java/x/GenTests.java",))
    assert "в сборке проекта не найдены" in unseen["style_checks_skipped"] and "format_hint" not in unseen
    assert "format_hint" not in style_notes(MAVEN, "/p/mvnw", ("spring-javaformat",), ())
    gradle = style_notes(GRADLE, "/p/gradlew", ("spring-javaformat", "spotless"), ("src/test/java/x/GenTests.java",))
    assert "style_checks_skipped" not in gradle and '"/p/gradlew" format' in gradle["format_hint"] and "spotlessApply" in gradle["format_hint"]
    assert style_notes(PYTEST, "/p/python", (), ("tests/test_x.py",)) == {}


# --------------------------------------------------------------------------------------
# (а) ready: style rows, the project's own exclusions, the offline validate probe
# --------------------------------------------------------------------------------------

def _maven_module(project: Path) -> dict:
    wrapper = "mvnw.cmd" if os.name == "nt" else "mvnw"
    path = _write(project, wrapper, "@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
    if os.name != "nt":
        path.chmod(0o755)
    return {"id": "app", "root": ".", "stack": {"language": "java", "build_tool": "maven"},
            "test": {"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": wrapper,
                     "build_profile": "default", "adapter_parameters": {}}}


def _petclinic_like(tmp_path: Path) -> tuple[Path, dict]:
    project = tmp_path / "petclinic"
    _write(project, "pom.xml", APP_POM.replace("<parent><groupId>x</groupId><artifactId>parent</artifactId><version>1</version></parent>", ""))
    return project, _maven_module(project)


def test_ready_names_the_style_checks_and_the_exclusions_for_the_project_build(tmp_path: Path) -> None:
    from tools.ready import style_rows

    project, module = _petclinic_like(tmp_path)
    rows = {row["id"]: row for row in style_rows(project, module)}
    style = rows["execution:style"]
    assert style["level"] == "WARN" and "spring-javaformat, checkstyle, nohttp" in style["message"]
    assert "пропуска" in style["message"] and "spring-javaformat:apply" in style["fix"]
    own = rows["execution:project-checks"]
    assert own["level"] == "WARN" and "nohttp" in own["message"] and ".pilot-runs" in own["message"]
    assert "<excludes>" in own["fix"] and "**/.pilot-runs/**" in own["fix"] and "**/.tools/**" in own["fix"]
    plain = tmp_path / "plain"
    _write(plain, "pom.xml", "<project><artifactId>plain</artifactId></project>\n")
    assert style_rows(plain, _maven_module(plain)) == []


# --------------------------------------------------------------------------------------
# ingest and finish: the skipped checks and the format hint for the files left in the project
# --------------------------------------------------------------------------------------

def test_ready_makes_no_probe_of_the_build_and_starts_no_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P4b: ``ready`` used to run the project's validate phase offline (``execution:build-probe``); the package starts no process now, so
    the row is gone for every build, and the style row — read from the poms — stays."""
    from tools import ready

    project, module = _petclinic_like(tmp_path)
    _write(project, "docs/feature.md", "Требование.\n")
    _write(project, "src/main/java/x/App.java", "package x;\npublic class App {}\n")
    from tools.init_skillsrc import _payload

    (project / ".skillsrc").write_bytes(_payload({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "petclinic"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{**module, "paths": {"source": ["src/main/java"], "tests": ["src/test/java"]}, "detected_from": ["pom.xml"]}],
    }))

    def refuse(*_args, **_kwargs):
        raise AssertionError("ready started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    report = ready.check(project, ["docs/feature.md"], profile="local-pilot-v1")
    rows = {row["id"]: row for row in report["checks"]}
    assert "execution:build-probe" not in rows, rows
    assert report["status"] == "READY", [row for row in report["checks"] if row["level"] == "STOP" and row["block"] == "cases"]
    assert rows["execution:style"]["level"] == "WARN"


def test_ingest_and_finish_hint_to_format_the_files_left_in_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import run_the_build
    from tests.test_finish import _project
    from tools import execution_adapters
    from tools.run_pipeline import cmd_finish

    project, run_root, _attempt_id, _values = _project(tmp_path)
    seen: list[tuple] = []

    def notes(adapter_id, executable, checks, kept_files):
        seen.append((adapter_id, Path(executable).name, tuple(checks), tuple(kept_files)))
        return {"format_hint": "отформатируйте: " + ", ".join(kept_files)}

    monkeypatch.setattr(execution_adapters, "project_style_checks", lambda *_args: ("spring-javaformat",))
    monkeypatch.setattr(execution_adapters, "style_notes", notes)
    code, output = run_the_build(project, run_root, capsys)
    assert code == 0
    assert output["verdict"] == "PASS" and output["format_hint"] == "отформатируйте: tests/test_generated.py", output
    assert seen[-1][0] == "pytest:selected-symbols-v1" and seen[-1][2:] == (("spring-javaformat",), ("tests/test_generated.py",)), seen
    cmd_finish(SimpleNamespace(project=str(project), run=run_root.name))
    finished = json.loads(capsys.readouterr().out)
    assert finished["format_hint"] == output["format_hint"], finished


# --------------------------------------------------------------------------------------
# (б) the package carries no plain-http links except XML namespace identifiers
# --------------------------------------------------------------------------------------

_NAMESPACE = re.compile(rb'(?:xmlns(?::[\w.-]+)?|Type|xsi:schemaLocation)="' + re.escape(PLAIN.encode()))


def test_the_package_has_no_plain_http_outside_xml_namespaces() -> None:
    listed = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    offenders = []
    for name in filter(None, listed.decode("utf-8").split("\0")):
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        allowed = {match.end() - len(PLAIN) for match in _NAMESPACE.finditer(data)}
        hits = [index.start() for index in re.finditer(re.escape(PLAIN.encode()), data) if index.start() not in allowed]
        if hits:
            offenders.append(f"{name}: {len(hits)}")
    assert not offenders, offenders


# --------------------------------------------------------------------------------------
# the real Maven: an unformatted generated test passes the selected run, the project's own validate rejects it
# --------------------------------------------------------------------------------------

_JAVA_HOME = os.environ.get("TEST_SKILLS_JAVA_HOME")
_M2 = Path.home() / ".m2" / "repository"


@pytest.mark.skipif(not _JAVA_HOME or not shutil.which("mvn")
                    or not (_M2 / "io/spring/javaformat/spring-javaformat-maven-plugin/0.0.47").is_dir()
                    or not (_M2 / "org/junit/jupiter/junit-jupiter/5.10.2").is_dir(),
                    reason="needs TEST_SKILLS_JAVA_HOME, mvn and the plugins in ~/.m2 (offline)")
def test_real_maven_skips_spring_javaformat_only_in_the_selected_tests_run(tmp_path: Path) -> None:
    from tools.execution_adapters import command_for

    project = tmp_path / "real"
    pinned = "".join(f"<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>{name}</artifactId><version>{version}</version></plugin>"
                     for name, version in (("maven-compiler-plugin", "3.13.0"), ("maven-surefire-plugin", "3.5.2"),
                                           ("maven-resources-plugin", "3.3.1")))
    _write(project, "pom.xml", f"""<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>real</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.2</version><scope>test</scope></dependency></dependencies>
<build><plugins>{pinned}<plugin><groupId>io.spring.javaformat</groupId><artifactId>spring-javaformat-maven-plugin</artifactId><version>0.0.47</version>
<executions><execution><phase>validate</phase><goals><goal>validate</goal></goals></execution></executions></plugin></plugins></build></project>
""")
    _write(project, "src/test/java/x/GenTests.java", "package x;\nimport org.junit.jupiter.api.Test;\nclass GenTests {\n@Test void works( ) {   }\n}\n")
    environment = {**os.environ, "JAVA_HOME": _JAVA_HOME}
    mvn = shutil.which("mvn")

    def run(argv: tuple[str, ...]) -> subprocess.CompletedProcess:
        return subprocess.run([*argv[:1], "-o", *argv[1:]], cwd=project, env=environment, capture_output=True, text=True, timeout=300)

    _reports, argv = command_for("maven:selected-symbols-v1", mvn, "default", ("x.GenTests#works",))
    selected = run(argv)
    assert selected.returncode == 0, selected.stdout[-3000:]
    assert (project / "target/surefire-reports/TEST-x.GenTests.xml").is_file()
    own_validate = run((mvn, "-B", "-q", "validate"))
    assert own_validate.returncode != 0 and "Formatting violations" in own_validate.stdout, own_validate.stdout[-3000:]
