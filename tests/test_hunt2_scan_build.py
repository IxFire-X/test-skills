"""FaT-H2a: the scan and build findings of the second bug hunt (2.1, 2.4, 2.14, 2.15, 2.16).

- 2.1: a non-source file in secrets/ or credentials/ is left out whatever its extension; the password rule reads the
  value on the same line in more forms (YAML list item, quoted key, XML element, any .json, ${X:default}, short
  secret); message bundles and true/false-like flags are not passwords; nothing left out before reaches the model now;
- 2.4: Gradle runs the tests past the build cache, and an old recorded request is refused with its reason;
- 2.14: a task document over a batch may be passed by raising the batch over the step budget;
- 2.15: the format hint says what the command formats;
- 2.16: the [ERROR] lines of the ready build probe carry no credentials and no long lines.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_build_style import _module

LEAK = {
    "src/main/resources/secrets/db.txt": "password=hunter2!\n",
    "src/main/resources/credentials/settings.xml": "<settings><servers><server><id>corp</id><username>ci</username>"
                                                   "<password>hunter2!</password></server></servers></settings>\n",
    "src/main/resources/secrets/vault.conf": "db.password = hunter2!\n",
    "src/main/resources/application-list.yml": "users:\n  - name: admin\n    password: hunter2!\n",
    "src/main/resources/users.yml": "users:\n  - password: hunter2!\n    name: admin\n",
    "src/main/resources/app.json": "{\"datasource\": {\"password\": \"hunter2!\"}}\n",
    "src/main/resources/application-default.properties": "spring.datasource.password=${DB_PASSWORD:hunter2!}\n",
    "src/main/resources/application-quoted.yml": "spring:\n  datasource:\n    \"password\": hunter2!\n",
    "src/main/resources/jwt.yml": "app:\n  jwt:\n    secret: s3cr3t!\n",
}
KEPT = {
    "src/main/resources/messages/messages.properties": "user.password.invalid=Неверный пароль\n"
                                                       "user.password.mismatch=Пароли не совпадают\n",
    "src/main/resources/ValidationMessages_ru.properties": "password.size=Пароль от 8 символов\n",
    "src/main/resources/application-policy.yml": "security:\n  password-policy:\n    min-length: 8\n",
    "src/test/resources/application-test.properties": "spring.datasource.username=sa\nspring.datasource.password=\n",
    "src/main/resources/application-flags.properties": "MYSQL_ALLOW_EMPTY_PASSWORD=true\napp.password-reset.enabled=true\n"
                                                       "app.secret-rotation: off\n",
    "src/main/resources/application-env.properties": "spring.datasource.password=${DB_PASSWORD}\napp.secret=${APP_SECRET:}\n",
    "src/main/resources/openapi.yml": "components:\n  schemas:\n    User:\n      properties:\n        password:\n"
                                      "          type: string\n          example: 1234abc\n        username:\n          type: string\n",
}
# Left out before this package and still left out: a number is a value, the encoder strength stays a "password".
STILL_LEFT_OUT = {
    "src/main/resources/application-encoder.properties": "app.passwordEncoder.strength=10\n",
    "src/main/resources/messages_en.properties": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
}


def _inventory(tmp_path: Path, files: dict[str, str]) -> tuple[set[str], dict[str, str]]:
    from tools.project_inventory import build_inventory

    project = tmp_path / "project"
    for relative, text in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    inventory = build_inventory(project, project)
    return ({row["project_path"] for row in inventory["files"]},
            {row["project_path"]: row["scanner_rule_id"] for row in inventory["exclusions"]})


def test_every_file_with_a_password_is_left_out_and_the_harmless_ones_stay(tmp_path: Path) -> None:
    files, rules = _inventory(tmp_path, {**LEAK, **KEPT, **STILL_LEFT_OUT})
    assert sorted(path for path in LEAK if path in files) == []
    assert {path: rules[path] for path in LEAK if path.split("/")[-2] in {"secrets", "credentials"}} == dict.fromkeys(
        ["src/main/resources/secrets/db.txt", "src/main/resources/credentials/settings.xml", "src/main/resources/secrets/vault.conf"],
        "secret-directory-v1")
    assert sorted(path for path in KEPT if path not in files) == [], {path: rules.get(path) for path in KEPT}
    assert sorted(path for path in STILL_LEFT_OUT if path in files) == []


def test_a_source_file_in_a_secrets_directory_is_judged_by_its_content(tmp_path: Path) -> None:
    files, _rules = _inventory(tmp_path, {"src/main/java/app/secrets/SecretsConfig.java": "class SecretsConfig {}\n"})
    assert "src/main/java/app/secrets/SecretsConfig.java" in files


# The password rule before FaT-H2a, kept here to prove the new one never lets through what this one caught.
_OLD_PASSWORD_ASSIGNMENT = re.compile(rb"(?im)^[ \t]*[\w.-]*(?:password|passwd|pwd)[\w.-]*[ \t]*[:=][ \t]*(?![ \t]*$)(?!['\"]?\$\{)(?!['\"]{2})\S")
_KEYS = ("password", "db.password", "spring.datasource.password", "PASSWD", "app.pwd", "keyPassword", "password-hash")
_VALUES = ("hunter2!", "x", "10", "'quoted'", "\"dq\"", "'", "a b c", "null", "~", "${X}tail", "!vault |", "true", "OFF", "Yes",
           "''", "\"\"", "${DB}", "${DB:}", "${DB:x}", "")
# The two named exceptions: true/false/yes/no/on/off flags, and the message bundles (their files below).
_FLAGS = frozenset({"true", "false", "yes", "no", "on", "off"})


@pytest.mark.parametrize("suffix", [".properties", ".yml"])
def test_the_password_rule_catches_everything_the_old_one_did_except_flags(suffix: str) -> None:
    from tools.project_inventory import _secret_rule

    separator = "=" if suffix == ".properties" else ": "
    missed = []
    for key in _KEYS:
        for value in _VALUES:
            line = f"  {key}{separator}{value}\n".encode("utf-8")
            if not _OLD_PASSWORD_ASSIGNMENT.search(line) or value.casefold() in _FLAGS:
                continue
            if _secret_rule(Path("application" + suffix), line, "src/main/resources/application" + suffix) is None:
                missed.append(line)
    assert missed == []
    bundle = b"user.password.invalid=hunter2!\n"
    assert _OLD_PASSWORD_ASSIGNMENT.search(bundle)
    for name in ("messages.properties", "messages_ru.properties", "ValidationMessages.properties"):
        assert _secret_rule(Path(name), bundle, f"src/main/resources/{name}") is None, name


@pytest.mark.parametrize(("name", "text"), [
    ("a.yml", "spring:\n  datasource:\n    password: ${DB_PASSWORD:hunter2!}\n"),
    ("a.yml", "- secret: s\n"),
    ("a.yml", "'db.password': 'x'\n"),
    ("a.json", "{\"user\":{\"apiSecret\":\"x\"}}"),
    ("a.xml", "<server><password>p</password></server>"),
    ("a.xml", "<x><secret attr=\"1\">p</secret></x>"),
    ("a.properties", "password=hunter2!\r\nname=x\r\n"),
])
def test_new_password_forms_are_caught(name: str, text: str) -> None:
    from tools.project_inventory import _secret_rule

    assert _secret_rule(Path(name), text.encode("utf-8"), "src/main/resources/" + name) == ("password-assignment-v1", "token-signature")


@pytest.mark.parametrize(("name", "text"), [
    ("a.json", "{\"password\": \"\", \"passwordHint\": \"${HINT}\"}"),
    ("a.xml", "<server><password>${env.REPO_PASSWORD}</password><password/></server>"),
    ("a.yml", "password:\n  type: string\n  example: 1234abc\n"),
    ("a.yml", "with:\n  password: ${{ secrets.DOCKERHUB_TOKEN }}\n  CI_DEPLOY_PASSWORD: ${{ secrets.CI_DEPLOY_PASSWORD }}\n"),
])
def test_empty_values_and_placeholders_are_no_secret(name: str, text: str) -> None:
    from tools.project_inventory import _secret_rule

    assert _secret_rule(Path(name), text.encode("utf-8"), "src/main/resources/" + name) is None


# --------------------------------------------------------------------------------------
# 2.4 Gradle past the build cache
# --------------------------------------------------------------------------------------


def test_gradle_runs_the_tests_past_the_build_cache(tmp_path: Path) -> None:
    from tools.execution_adapters import GRADLE, build_request

    request = build_request(GRADLE, _module(tmp_path, GRADLE, "gradlew"), [{"selector": "pkg.SampleTest#works"}])
    assert request.argv[1:4] == ("cleanTest", "test", "--no-build-cache")


def test_an_old_gradle_request_is_refused_for_rerun_with_its_reason(tmp_path: Path) -> None:
    from tools.execution_adapters import GRADLE, MAVEN, build_request
    from tools.run_pipeline import HostStop, _rerun_request_current

    request = build_request(GRADLE, _module(tmp_path / "g", GRADLE, "gradlew"), [{"selector": "pkg.SampleTest#works"}])
    _rerun_request_current(request)
    old = replace(request, argv=tuple(token for token in request.argv if token != "--no-build-cache"))
    with pytest.raises(HostStop) as stop:
        _rerun_request_current(old)
    assert stop.value.code == "RERUN_REQUEST_OUTDATED"
    assert "--no-build-cache" in str(stop.value) and "команда Gradle изменилась" in str(stop.value)
    assert "новый прогон" in (stop.value.fix or "")
    _rerun_request_current(build_request(MAVEN, _module(tmp_path / "m", MAVEN, "mvnw"), [{"selector": "pkg.SampleTest#works"}]))


# --------------------------------------------------------------------------------------
# 2.14, 2.15, 2.16 hints
# --------------------------------------------------------------------------------------


def test_a_task_document_over_a_batch_may_raise_the_batch_over_the_step_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_ready_b2 import _big_task_document
    from tests.test_context_filter import _project, _scan

    project = _project(tmp_path)
    _big_task_document(project)
    _code, scan = _scan(project, monkeypatch)
    assert "не выше бюджета шага" not in scan["fix"]
    assert "только предупреждение" in scan["fix"] and "окно модели" in scan["fix"]


def test_the_format_hint_says_what_the_command_formats() -> None:
    from tools.execution_adapters import MAVEN, style_notes

    kept = ("src/test/java/x/GenTests.java", "src/test/java/x/OtherTests.java")
    whole = style_notes(MAVEN, "/p/mvnw", ("spring-javaformat",), kept)["format_hint"]
    assert "весь модуль" in whole and all(path in whole for path in kept)
    spotless = style_notes(MAVEN, "/p/mvnw", ("spotless",), kept)["format_hint"]
    assert "-DspotlessFiles=" in spotless and "весь модуль" not in spotless
    files = re.search(r'-DspotlessFiles="([^"]+)"', spotless).group(1).split(",")
    for path in ("C:\\work\\app\\src\\test\\java\\x\\GenTests.java", "/work/app/src/test/java/x/OtherTests.java"):
        assert any(re.fullmatch(pattern, path) for pattern in files), path
    assert not any(re.fullmatch(pattern, "/work/app/src/main/java/x/App.java") for pattern in files)


def test_the_build_probe_error_lines_carry_no_credentials(tmp_path: Path) -> None:
    from tests.test_build_style import _petclinic_like
    from tools.run_tests import ProcessOutcome
    from tools.ready import build_probe

    project, module = _petclinic_like(tmp_path)
    stdout = ("[ERROR] Failed to read artifact from https://ci:hunter2@repo.corp/maven2 (token=abcdef123456)\n"
              "[ERROR] Rule 0: requireProperty failed: db.password=hunter2 secret: 's3 cr3t'\n"
              "[ERROR] " + "x" * 400 + "\n")
    row = build_probe(project, module, runner=lambda *_a, **_k: ProcessOutcome(exit_code=1, stdout=stdout, stderr=""))
    message = row["message"]
    assert "hunter2" not in message and "abcdef123456" not in message and "s3 cr3t" not in message
    assert "://***@repo.corp" in message
    assert all(len(line) <= 200 for line in message.split(" | ")[1:]) and "x" * 201 not in message


def test_the_build_probe_reads_the_real_process_outcome(tmp_path: Path) -> None:
    """FaT-H2a P1: the probe runs through run_tests.run_subprocess — the outcome form can never drift from a test double."""
    import sys

    from tests.test_build_style import _petclinic_like
    from tools.ready import _probe_runner, build_probe

    project, module = _petclinic_like(tmp_path)
    fake = tmp_path / "fake_mvn.py"
    fake.write_text("print('[ERROR] Rule 0: RequireJavaVersion failed with message: Detected JDK 24 is not in [21,22)')\n"
                    "raise SystemExit(1)\n", encoding="utf-8")
    calls: list[tuple] = []

    def runner(argv, cwd, *, timeout):
        calls.append(tuple(argv))
        return _probe_runner([sys.executable, str(fake)], cwd, timeout=timeout)

    row = build_probe(project, module, runner=runner)
    assert calls and row["level"] == "WARN" and "RequireJavaVersion" in row["message"], row
    assert "выбранные тесты не запустятся" in row["message"]


def test_the_project_checks_row_names_every_file_outside_the_projects_git(tmp_path: Path) -> None:
    """FaT-H2a P6: RAT fell on a task document, not on .tools/ — the row names all such files."""
    from tests.test_build_style import _petclinic_like
    from tools.ready import style_rows

    project, module = _petclinic_like(tmp_path)
    row = {row["id"]: row for row in style_rows(project, module)}["execution:project-checks"]
    assert "нет в git проекта" in row["message"] and "новые документы" in row["message"] and ".pilot-runs/" in row["message"]
    assert "пропускает" in row["message"]


@pytest.mark.parametrize("text", [
    "public void setPassword(String password) {\n    this.password = password;\n}\n",
    "final Token token = readToken();\nToken.PI piToken = token.asPI();\n",
    "final String token = parseToken(pattern, indexRef);\n",
    'public static final String JAVA_NET_SOCKS_PASSWORD = "java.net.socks.password";\n',
    'if (token == "x") { secret = Secrets.read(name); }\n',
])
def test_a_variable_or_a_property_name_in_source_is_no_secret(text: str) -> None:
    """FaT-H2a P5: Account.java, User.java, jsoup's tree builders and commons-lang fell out on these forms."""
    from tools.project_inventory import _secret_rule

    assert _secret_rule(Path("Account.java"), text.encode("utf-8"), "src/main/java/app/Account.java") is None


@pytest.mark.parametrize(("name", "text"), [
    ("Config.java", 'private String password = "hunter2!";\n'),
    ("config.py", "API_KEY = 'abcdef0123456789'\n"),
    ("client.ts", 'const auth = { token: "s3cr3t-t0ken" };\n'),
    ("Keys.java", 'String k = "AKIAABCDEFGHIJKLMNOP";\n'),
])
def test_a_string_literal_secret_in_source_is_still_left_out(name: str, text: str) -> None:
    from tools.project_inventory import _secret_rule

    assert _secret_rule(Path(name), text.encode("utf-8"), "src/" + name) is not None


def test_a_literal_with_spaces_in_source_is_still_a_secret() -> None:
    """jsoup ConnectTest: `String password = "Netscape engineers are weenies!";` fell out before and still does."""
    from tools.project_inventory import _secret_rule

    text = b'String password = "Netscape engineers are weenies!";\n'
    assert _secret_rule(Path("ConnectTest.java"), text, "src/test/java/ConnectTest.java") == ("token-assignment-v1", "token-signature")


_FLYWAY_POM = """<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>app</artifactId><version>1</version>
<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version>
<scope>test</scope></dependency></dependencies>
<build><plugins><plugin><groupId>org.flywaydb</groupId><artifactId>flyway-maven-plugin</artifactId>
<configuration><url>jdbc:h2:mem:db</url><user>sa</user><password>PASSWORD</password></configuration></plugin></plugins></build>
</project>
"""


def _maven_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, password: str) -> tuple[Path, int, dict]:
    import json
    import os
    from types import SimpleNamespace

    from tests.test_first_run import _java_project, _write
    from tools import run_pipeline
    from tools.init_skillsrc import _payload

    project = _java_project(tmp_path)
    _write(project, "pom.xml", _FLYWAY_POM.replace("PASSWORD", password))
    wrapper = "mvnw.cmd" if os.name == "nt" else "mvnw"
    _write(project, wrapper, "@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
    (project / ".skillsrc").write_bytes(_payload({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "java"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{"id": "root", "root": ".", "stack": {"language": "java", "build_tool": "maven"},
                     "paths": {"source": ["src/main/java"], "tests": ["src/test/java"]},
                     "test": {"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": wrapper,
                              "build_profile": "default", "adapter_parameters": {}},
                     "detected_from": ["pom.xml"]}],
    }))
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    code = run_pipeline.cmd_scan(SimpleNamespace(project=str(project), module="root", target=None, docs=["docs/feature.md"],
                                                 profile="local-pilot-v1"))
    return project, code, json.loads(json.dumps(payloads[-1]))


def test_a_pom_with_a_literal_password_leaves_the_context_but_binds_the_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pom.xml is a closed manifest; a flyway <password>sa</password> keeps it out of the model, never stops local-pilot."""
    import hashlib
    import json

    project, code, scan = _maven_scan(tmp_path / "literal", monkeypatch, "sa")
    assert code == 0 and scan["policy_profile"] == "local-pilot-v1" and "execution_skipped" not in scan, scan
    root = Path(scan["run_root"])
    summary = json.dumps(scan.get("exclusion_summary"), ensure_ascii=False)
    assert "pom.xml" in summary and "password-assignment-v1" in summary, summary
    [baseline_path] = list((root / "baselines").glob("*.json"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    digest = "sha256:" + hashlib.sha256((project / "pom.xml").read_bytes()).hexdigest()
    assert {"project_path": "pom.xml", "content_digest": digest, "byte_count": (project / "pom.xml").stat().st_size} in baseline["secret_inputs"]
    for path in root.rglob("*"):
        if path.is_file():
            assert b"<password>sa</password>" not in path.read_bytes(), path

    project, code, scan = _maven_scan(tmp_path / "placeholder", monkeypatch, "${env.DB_PASS}")
    assert code == 0 and scan["policy_profile"] == "local-pilot-v1", scan
    assert "pom.xml" not in json.dumps(scan.get("exclusion_summary"), ensure_ascii=False)
    [baseline_path] = list((Path(scan["run_root"]) / "baselines").glob("*.json"))
    assert not json.loads(baseline_path.read_text(encoding="utf-8")).get("secret_inputs")
