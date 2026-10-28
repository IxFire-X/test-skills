"""FaT-RL small items: java.version under any external parent, the license and no-format texts of the style hint, a
password in a Spring XML attribute."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_java_version_counts_under_any_external_parent(tmp_path: Path) -> None:
    from tools.discover_project import java_api_level

    (tmp_path / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<parent><groupId>org.mybatis</groupId><artifactId>mybatis-parent</artifactId><version>53</version></parent>"
        "<artifactId>jpetstore</artifactId><properties><java.version>11</java.version>"
        "<java.release.version>11</java.release.version></properties></project>", encoding="utf-8")
    level = java_api_level(tmp_path)
    assert level["level"] == 11 and level["evidence"][0] == "pom.xml: java.version=11 (родитель mybatis-parent)", level


def test_a_license_goal_says_it_rewrites_every_header() -> None:
    from tools.execution_adapters import MAVEN, style_notes

    hint = style_notes(MAVEN, "/p/mvnw", ("license-codehaus",), ("tests/test_generated.py",))["format_hint"]
    assert "license:update-file-header переписывает заголовки лицензий во всех файлах модуля" in hint, hint


def test_checks_without_a_format_goal_say_so_instead_of_a_cut_sentence() -> None:
    from tools.execution_adapters import MAVEN, style_notes

    hint = style_notes(MAVEN, "/p/mvnw", ("checkstyle", "apache-rat", "pmd", "spotbugs"), ("tests/test_generated.py",))["format_hint"]
    assert "(checkstyle, apache-rat, pmd, spotbugs): команды автоформатирования для этих проверок нет" in hint, hint
    assert "затем проверьте:" in hint and "): затем" not in hint


@pytest.mark.parametrize(("text", "secret"), [
    (b'<beans><bean id="ds"><property name="password" value="s3cr3t"/></bean></beans>', True),
    (b"<property value='s3cr3t' name='db.password'/>", True),
    (b'<util:map><entry key="secret" value="abc"/></util:map>', True),
    (b'<property name="password" value="${DB_PASSWORD}"/>', False),
    (b'<property name="password" value=""/>', False),
    (b'<property name="username" value="sa"/>', False),
    (b'<input name="password" type="password"/>', False),
])
def test_a_password_in_a_spring_xml_attribute_is_a_secret(text: bytes, secret: bool) -> None:
    from tools.project_inventory import _secret_rule

    rule = _secret_rule(Path("applicationContext.xml"), text, "src/main/resources/applicationContext.xml")
    assert (rule == ("password-assignment-v1", "token-signature")) is secret, rule


def test_a_jsp_form_field_is_not_a_password() -> None:
    from tools.project_inventory import _secret_rule

    assert _secret_rule(Path("SignonForm.jsp"), b'<input name="password" value="x"/>', "src/main/webapp/SignonForm.jsp") is None
