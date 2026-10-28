"""FaT-JL: the Java level of a module's build, read without running it, reaches the generator and the static review.

jsoup (animal-sniffer check-java8-api with checkTestClasses) failed at test-compile on `List.of` and `orElseThrow()`:
nothing told the generator the module builds its tests for Java 8."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation

# The shape of the jsoup pom: no level in properties, the compiler level in executions and profiles, a Java 8 signature
# that checks the test classes, a JDK 11+ profile that compiles an overlay with release 11.
JSOUP_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion><groupId>org.jsoup</groupId><artifactId>jsoup</artifactId><version>1</version>
  <build><plugins>
    <plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>
      <executions><execution><id>compile-package-info</id><configuration><source>8</source><target>8</target></configuration></execution></executions>
    </plugin>
    <plugin><groupId>org.codehaus.mojo</groupId><artifactId>animal-sniffer-maven-plugin</artifactId>
      <executions>
        <execution><id>check-java8-api</id><phase>test-compile</phase><configuration><checkTestClasses>true</checkTestClasses>
          <signature><groupId>org.codehaus.mojo.signature</groupId><artifactId>java18</artifactId><version>1.0</version></signature>
        </configuration></execution>
      </executions>
    </plugin>
    <plugin><groupId>org.codehaus.mojo</groupId><artifactId>build-helper-maven-plugin</artifactId>
      <executions><execution><configuration><sources><source>${project.basedir}/src/test/java11</source></sources></configuration></execution></executions>
    </plugin>
  </plugins></build>
  <profiles><profile><id>multi-release</id><build><plugins>
    <plugin><artifactId>maven-compiler-plugin</artifactId><executions><execution><configuration><release>11</release></configuration></execution></executions></plugin>
  </plugins></build></profile></profiles>
</project>
"""


def _pom(body: str = "", *, parent: str = "", modules: str = "") -> str:
    return (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>{parent}'
            f'<groupId>g</groupId><artifactId>a</artifactId><version>1</version>{modules}{body}</project>')


def test_the_jsoup_shape_is_java_8_from_the_compiler_and_the_test_signature(tmp_path: Path) -> None:
    from tools.discover_project import java_api_level

    (tmp_path / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    level = java_api_level(tmp_path)
    assert level["level"] == 8, level
    assert "pom.xml: animal-sniffer java18 (checkTestClasses)" in level["evidence"]
    assert "pom.xml: maven-compiler-plugin source=8" in level["evidence"]


@pytest.mark.parametrize(("body", "parent", "expected"), [
    # commons-lang: the properties of its own pom
    ("<properties><maven.compiler.source>1.8</maven.compiler.source><maven.compiler.target>1.8</maven.compiler.target></properties>", "", 8),
    # a compiler release through a property
    ("<properties><jdk>11</jdk></properties><build><plugins><plugin><artifactId>maven-compiler-plugin</artifactId>"
     "<configuration><release>${jdk}</release></configuration></plugin></plugins></build>", "", 11),
    # Spring Boot: java.version is the level only under spring-boot-starter-parent
    ("<properties><java.version>17</java.version></properties>",
     "<parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>3</version></parent>", 17),
    ("<properties><java.version>17</java.version></properties>", "", None),
    # a signature that does not check the test classes says nothing about the tests
    ("<build><plugins><plugin><artifactId>animal-sniffer-maven-plugin</artifactId><configuration><signature>"
     "<artifactId>java16</artifactId></signature></configuration></plugin></plugins></build>", "", None),
    # an unresolved property is no level
    ("<properties><maven.compiler.release>${external.level}</maven.compiler.release></properties>", "", None),
])
def test_maven_levels(tmp_path: Path, body: str, parent: str, expected: int | None) -> None:
    from tools.discover_project import java_api_level

    (tmp_path / "pom.xml").write_text(_pom(body, parent=parent), encoding="utf-8")
    level = java_api_level(tmp_path)
    assert (level and level["level"]) == expected, level


def test_a_reactor_parent_gives_its_properties_and_an_outside_pom_is_never_read(tmp_path: Path) -> None:
    from tools.discover_project import java_api_level

    parent = "<parent><groupId>g</groupId><artifactId>root</artifactId><version>1</version></parent>"
    (tmp_path / "pom.xml").write_text(_pom("<properties><maven.compiler.release>8</maven.compiler.release></properties>",
                                           modules="<modules><module>core</module></modules>"), encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "pom.xml").write_text(_pom(parent=parent), encoding="utf-8")
    level = java_api_level(tmp_path / "core")
    assert level["level"] == 8 and level["evidence"] == [f"{tmp_path.name}/pom.xml: maven.compiler.release=8"], level

    # the same pom one level up that does not list this module is not its reactor: not read
    (tmp_path / "pom.xml").write_text(_pom("<properties><maven.compiler.release>8</maven.compiler.release></properties>"), encoding="utf-8")
    assert java_api_level(tmp_path / "core") is None


@pytest.mark.parametrize(("name", "text", "expected"), [
    ("build.gradle", "java { sourceCompatibility = JavaVersion.VERSION_1_8\n targetCompatibility = '1.8' }", 8),
    ("build.gradle.kts", "java { toolchain { languageVersion.set(JavaLanguageVersion.of(17)) } }", 17),
    ("build.gradle", "kotlin { jvmToolchain(11) }\ntasks.withType(JavaCompile) { options.release = 8 }", 8),
    ("build.gradle", "plugins { id 'java' }", None),
])
def test_gradle_levels(tmp_path: Path, name: str, text: str, expected: int | None) -> None:
    from tools.discover_project import java_api_level

    (tmp_path / name).write_text(text, encoding="utf-8")
    level = java_api_level(tmp_path)
    assert (level and level["level"]) == expected, level


def _java_artifact(body: str) -> tuple[dict, dict]:
    document = automated_document()
    artifact = automation(document)
    content = ("package sample;\nimport java.util.*;\nimport org.junit.jupiter.api.*;\n"
               f"class ProductsTest {{\n  @Test\n  void products() {{\n{body}\n  }}\n}}\n")
    artifact["artifacts"]["generated_files"][0].update(
        path="src/test/java/sample/ProductsTest.java", language="java", framework="junit5",
        content=content, content_digest="sha256:" + hashlib.sha256(content.encode()).hexdigest())
    artifact["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "java_class_method", "class_fqn": "sample.ProductsTest", "method_name": "products"}
    return document, artifact


JAVA_11_BODY = """    List<String> names = List.of("a", "b");
    var first = Optional.of(names.get(0)).orElseThrow();
    Map<String, Integer> sizes = Map.of("a", 1);
    if ("x".isBlank() || " y ".strip().isEmpty()) { "-".repeat(3); }
    String block = \"\"\"
        text
        \"\"\";"""


def test_the_static_review_rejects_newer_api_with_its_replacement(tmp_path: Path) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    (tmp_path / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    document, artifact = _java_artifact(JAVA_11_BODY)
    result = validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False)
    assert result.status == "NOT_RUNNABLE"
    rows = [row for row in result.diagnostics if row["code"] == "RUNNER_API_LEVEL"]
    names = {row["message"].split(" — ", 1)[0] for row in rows}
    # isBlank() is not on the list: AssertJ has an isBlank() assertion too — the build finds String.isBlank()
    assert names == {"List.of", "Optional.orElseThrow()", "var", "Map.of/ofEntries/entry", "String.strip()",
                     "String.repeat", 'текстовый блок """'}, names
    assert all(row["path"] == "/artifacts/generated_files/0" for row in rows)
    list_of = next(row["message"] for row in rows if row["message"].startswith("List.of"))
    assert "API Java 9" in list_of and "под Java 8" in list_of and "Arrays.asList" in list_of and "animal-sniffer java18" in list_of
    assert {row["code"] for row in result.diagnostics} == {"RUNNER_API_LEVEL"}


def test_java_8_api_comments_and_strings_pass(tmp_path: Path) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    (tmp_path / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    body = ("    // List.of(\"a\") and var x = 1 in a comment\n"
            "    String text = \"Map.of(1, 2).orElseThrow() isBlank()\";\n"
            "    List<String> names = Arrays.asList(\"a\", \"b\");\n"
            "    String first = Optional.of(names.get(0)).orElseThrow(NoSuchElementException::new);\n"
            "    List<String> copy = ImmutableList.of(\"a\");\n"
            "    Assertions.assertTrue(text.trim().isEmpty() || first != null);")
    document, artifact = _java_artifact(body)
    assert validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False).status == "READY"


@pytest.mark.parametrize(("body", "expected"), [
    ('    String line = "ab".repeat(3);', {"String.repeat"}),
    ('    String line = prefix(name.trim()).repeat(count + 1);', {"String.repeat"}),
    # the same method name in other libraries: another argument count
    ('    String line = StringUtils.repeat("a", 3) + Strings.repeat("a", 3) + StringUtils.repeat(\'a\', f(1, 2));', set()),
    ('    String a = StringUtils.strip(" a ");\n    String b = name.strip();', {"String.strip()"}),
    ('    Optional.of(1).ifPresentOrElse(v -> {}, () -> {});\n    option.ifPresentOrElse(v -> {});', {"Optional.ifPresentOrElse"}),
    ('    String v = maybe.orElseThrow(IllegalStateException::new);', set()),
    # an assertion, a stream read and an argument-less method of another library are left to the build
    ('    assertThat(name).isBlank();\n    byte[] all = in.readAllBytes();', set()),
    ('    String text = "List.of(1)".formatted();\n    String other = "%s".formatted(name);', {"String.formatted"}),
])
def test_the_rules_are_narrowed_by_arguments_and_receiver(tmp_path: Path, body: str, expected: set) -> None:
    from tools.run_tests import _java_api_level_diagnostics

    names = {row["message"].split(" — ", 1)[0] for row in _java_api_level_diagnostics("/f", body, {"level": 8, "evidence": ["pom.xml"]})}
    assert names == expected, names


@pytest.mark.parametrize(("imports", "body", "expected"), [
    ("import java.util.List;\n", '    List<Integer> xs = List.of(1);', {"List.of"}),
    ("import io.vavr.collection.List;\nimport io.vavr.collection.Map;\n", '    List<Integer> xs = List.of(1);\n    Map<String, Integer> m = Map.of("a", 1);', set()),
    ("import com.google.common.io.Files;\nimport com.google.common.base.Predicate;\n",
     '    String s = Files.readString(f);\n    Predicate<String> p = Predicate.not(q);', set()),
    ("", '    String s = Files.readString(path);\n    Predicate<String> p = Predicate.not(String::isEmpty);',
     {"Files.readString/writeString", "Predicate.not"}),
])
def test_an_imported_class_of_the_same_name_turns_its_rule_off(imports: str, body: str, expected: set) -> None:
    from tools.run_tests import _java_api_level_diagnostics

    text = f"package sample;\n{imports}class T {{\n  void t() {{\n{body}\n  }}\n}}\n"
    names = {row["message"].split(" — ", 1)[0] for row in _java_api_level_diagnostics("/f", text, {"level": 8, "evidence": ["pom.xml"]})}
    assert names == expected, names


@pytest.mark.parametrize("pom", [_pom("<properties><maven.compiler.release>17</maven.compiler.release></properties>"), None])
def test_a_newer_or_unknown_level_does_not_restrict(tmp_path: Path, pom: str | None) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    if pom:
        (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    document, artifact = _java_artifact(JAVA_11_BODY)
    assert validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False).status == "READY"


def test_ready_names_the_module_level(tmp_path: Path) -> None:
    from tools.ready import java_level_rows

    module = {"id": "root", "root": ".", "stack": {"language": "java"}}
    (tmp_path / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    [row] = java_level_rows(tmp_path, module)
    assert row["id"] == "execution:java-level" and row["level"] == "OK"
    assert "уровень Java модуля: 8" in row["message"] and "только API Java 8" in row["message"] and "RUNNER_API_LEVEL" in row["message"]
    (tmp_path / "pom.xml").write_text(_pom(), encoding="utf-8")
    [row] = java_level_rows(tmp_path, module)
    assert "не задан" in row["message"]
    assert java_level_rows(tmp_path, {**module, "stack": {"language": "python"}}) == []


def test_the_automation_step_inputs_carry_the_level(tmp_path: Path) -> None:
    from tests.test_finish import _project
    from tools.step_size import request_files

    project, run_root, _attempt_id, _values = _project(tmp_path)
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        assert "java_level" not in request_files(run_root, stage)
    (Path(project) / "pom.xml").write_text(JSOUP_POM, encoding="utf-8")
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        level = request_files(run_root, stage)["java_level"]
        assert level["level"] == 8 and level["rule"].startswith("только API Java 8"), level
