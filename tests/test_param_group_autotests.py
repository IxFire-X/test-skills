"""FaT-PT step 3: a param_group is one @ParameterizedTest — literal rows prove N → case ID, the report gives N (Surefire)
or N and the ID (Gradle), each row has its own status, and a quarantine names the failed and the passed rows it disabled.

Surefire 2.22.2 and 3.5.2 write an invocation as `method(Type, …)[N]` without its display name (fixtures/param-groups from
a real offline run, JUnit 5.10.2); the match moves from the name into the code and stays exact while the rows are literals."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "param-groups"
IDS = ["TC-B1-001", "TC-B1-002", "TC-B1-003"]
PAIR = ("FILE-group", "SYMBOL-group")


def _class(method_part: str, provider: str = "", imports: str = "") -> str:
    return ("package p;\nimport java.util.stream.Stream;\nimport org.junit.jupiter.api.Test;\n"
            "import org.junit.jupiter.params.ParameterizedTest;\nimport org.junit.jupiter.params.provider.*;\n" + imports +
            "class GroupTest {\n" + provider + method_part + "  @Test void plain() {}\n}\n")


METHOD = ('  @ParameterizedTest(name = "[{index}] {0}")\n  @MethodSource("rows")\n'
          "  void tc_group_age(String caseId, int value) { }\n")


def _provider(*rows: str) -> str:
    return "  static Stream<Arguments> rows() {\n    return Stream.of(\n      " + ",\n      ".join(rows) + ");\n  }\n"


VALID = _provider('Arguments.of("TC-B1-001", 1)', 'Arguments.of("TC-B1-002", 2)', 'Arguments.of("TC-B1-003", -3L)')


def _rows(text: str) -> list[str] | None:
    from tools.run_tests import _param_group_rows

    return _param_group_rows(text, "tc_group_age")


# --------------------------------------------------------------------------------------
# the literal rows
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    _class(METHOD, VALID),
    _class(METHOD, _provider('arguments("TC-B1-001", "a", true)', 'arguments("TC-B1-002", \'b\', null)', 'arguments("TC-B1-003", 2.5)'),
           "import static org.junit.jupiter.params.provider.Arguments.arguments;\n"),
    _class(METHOD.replace('@MethodSource("rows")', '@MethodSource(value = "rows")'), VALID),
    _class('  @ParameterizedTest(name = "[{index}] {0}")\n  @CsvSource({"TC-B1-001, 1", "TC-B1-002, 2", "TC-B1-003, 3"})\n'
           "  void tc_group_age(String caseId, int value) { }\n"),
    _class('  @ParameterizedTest(name = "[{index}] {0}")\n  @CsvSource(value = {"\'TC-B1-001\', 1", "TC-B1-002,2", "TC-B1-003 , 3"})\n'
           "  void tc_group_age(String caseId, int value) { }\n"),
])
def test_literal_rows_in_the_same_class_give_the_case_ids_in_order(text: str) -> None:
    assert _rows(text) == IDS


def test_an_unnamed_method_source_uses_the_method_s_own_name() -> None:
    from tools.run_tests import _param_group_rows

    method = METHOD.replace('@MethodSource("rows")', "@MethodSource")
    provider = VALID.replace("rows()", "tc_group_age()")
    assert _param_group_rows(_class(method, provider), "tc_group_age") == IDS


@pytest.mark.parametrize("text", [
    # a computed provider: a loop, an external call, a stream pipeline, a filter
    _class(METHOD, "  static Stream<Arguments> rows() {\n    java.util.List<Arguments> all = new java.util.ArrayList<>();\n"
                   "    for (int i = 1; i <= 3; i++) all.add(Arguments.of(\"TC-B1-00\" + i, i));\n    return all.stream();\n  }\n"),
    _class(METHOD, _provider('Arguments.of(Ids.first(), 1)', 'Arguments.of("TC-B1-002", 2)', 'Arguments.of("TC-B1-003", 3)')),
    _class(METHOD, "  static Stream<Arguments> rows() {\n    return Stream.of(Arguments.of(\"TC-B1-001\", 1), Arguments.of(\"TC-B1-002\", 2), "
                   "Arguments.of(\"TC-B1-003\", 3)).filter(row -> true);\n  }\n"),
    _class(METHOD, _provider('Arguments.of("TC-B1-" + "001", 1)', 'Arguments.of("TC-B1-002", 2)', 'Arguments.of("TC-B1-003", 3)')),
    _class(METHOD, _provider('Arguments.of(ID, 1)', 'Arguments.of("TC-B1-002", 2)', 'Arguments.of("TC-B1-003", 3)')),
    # other sources and a provider in another class
    _class(METHOD.replace('@MethodSource("rows")', '@CsvFileSource(resources = "/rows.csv")'), VALID),
    _class(METHOD.replace('@MethodSource("rows")', '@ValueSource(strings = {"TC-B1-001", "TC-B1-002", "TC-B1-003"})'), VALID),
    _class(METHOD.replace('@MethodSource("rows")', "@ArgumentsSource(Rows.class)"), VALID),
    _class(METHOD.replace('@MethodSource("rows")', '@MethodSource("p.Other#rows")'), VALID),
    _class(METHOD.replace('@MethodSource("rows")', '@MethodSource({"rows", "more"})'), VALID),
    _class(METHOD.replace('@MethodSource("rows")', '@MethodSource("rows")\n  @ValueSource(ints = 1)'), VALID),
    # the display name is required
    _class(METHOD.replace('(name = "[{index}] {0}")', ""), VALID),
    _class(METHOD.replace('"[{index}] {0}"', '"{0}"'), VALID),
    # a comment or a text block inside the provider is not a literal row
    _class(METHOD, VALID.replace("return", "// rows\n    return")),
])
def test_anything_but_literal_rows_is_refused(text: str) -> None:
    assert _rows(text) is None


def _artifact(pairs: dict[str, tuple[str, str]]) -> dict:
    relations = [{"kind": "operation", "case_id": case_id, "step_id": "S", "file_id": pair[0], "symbol_id": pair[1]} for case_id, pair in pairs.items()]
    symbols = [{"file_id": pair[0], "symbol_id": pair[1], "locator": {"kind": "java_class_method", "class_fqn": "p.GroupTest",
                                                                        "method_name": "tc_group_age" if pair == PAIR else pair[1].lower()}}
               for pair in sorted(set(pairs.values()))]
    return {"artifacts": {"implementation_relations": relations, "generated_symbols": symbols}}


@pytest.fixture()
def document(monkeypatch: pytest.MonkeyPatch):
    from tools import canonical_document

    group = {"group_id": "PG-age", "parameter": None, "cases": [{"case_id": case_id} for case_id in IDS]}
    monkeypatch.setattr(canonical_document, "param_groups", lambda _document: [group])
    return {}


def _diagnose(document, pairs, text, params=True):
    from tools.run_tests import _param_group_diagnostics

    return _param_group_diagnostics(document, _artifact(pairs), {PAIR[0]: text}, params)


def test_a_valid_group_binds_its_rows_to_its_one_method(document) -> None:
    rows, by_pair = _diagnose(document, {case_id: PAIR for case_id in IDS}, _class(METHOD, VALID))
    assert rows == [] and by_pair == {PAIR: tuple(IDS)}


@pytest.mark.parametrize(("order", "why"), [
    (["TC-B1-002", "TC-B1-001", "TC-B1-003"], "two rows swapped"),
    (["TC-B1-001", "TC-B1-002"], "a row missing"),
    (["TC-B1-001", "TC-B1-002", "TC-B1-003", "TC-B1-004"], "an extra row"),
    (["TC-B1-001", "TC-B1-002", "TC-B1-002"], "a repeated case ID"),
])
def test_rows_must_be_the_group_in_display_order(document, order: list, why: str) -> None:
    provider = _provider(*(f'Arguments.of("{case_id}", {index})' for index, case_id in enumerate(order)))
    rows, by_pair = _diagnose(document, {case_id: PAIR for case_id in IDS}, _class(METHOD, provider))
    assert [row["code"] for row in rows] == ["RUNNER_PARAM_ROWS"] and by_pair == {}, why
    assert "Arguments.of" in rows[0]["message"] and "display_order" in rows[0]["message"]


def test_a_group_on_several_methods_is_refused_while_junit_params_is_there(document) -> None:
    pairs = {case_id: (PAIR[0], f"SYMBOL-{index}") for index, case_id in enumerate(IDS)}
    rows, _ = _diagnose(document, pairs, _class(METHOD, VALID))
    assert [row["code"] for row in rows] == ["RUNNER_PARAM_ROWS"]
    assert _diagnose(document, pairs, _class(METHOD, VALID), params=False) == ([], {})  # a method per case


def test_one_method_for_a_group_without_junit_params_is_refused(document) -> None:
    rows, _ = _diagnose(document, {case_id: PAIR for case_id in IDS}, _class(METHOD, VALID), params=False)
    assert [row["code"] for row in rows] == ["RUNNER_PARAMS_UNAVAILABLE"] and "по методу @Test" in rows[0]["message"]


def test_a_partly_automated_group_is_refused(document) -> None:
    rows, _ = _diagnose(document, {case_id: PAIR for case_id in IDS[:2]}, _class(METHOD, VALID))
    assert [row["code"] for row in rows] == ["RUNNER_PARAM_ROWS"]


# --------------------------------------------------------------------------------------
# junit-jupiter-params in the build
# --------------------------------------------------------------------------------------


def _pom(dependencies: str, management: str = "") -> str:
    return (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><artifactId>a</artifactId>'
            f"{management}<dependencies>{dependencies}</dependencies></project>")


def _dependency(artifact: str) -> str:
    return f"<dependency><groupId>g</groupId><artifactId>{artifact}</artifactId><scope>test</scope></dependency>"


@pytest.mark.parametrize(("name", "text", "available"), [
    ("pom.xml", _pom(_dependency("junit-jupiter")), True),
    ("pom.xml", _pom(_dependency("junit-jupiter-params")), True),
    ("pom.xml", _pom(_dependency("spring-boot-starter-test")), True),
    ("pom.xml", _pom(_dependency("spring-boot-starter-data-jpa-test")), True),  # Spring Boot 4 test starters
    ("pom.xml", _pom(_dependency("junit-jupiter-engine")), False),  # the engine does not bring params (jpetstore)
    ("pom.xml", _pom("", f"<dependencyManagement><dependencies>{_dependency('junit-jupiter')}</dependencies></dependencyManagement>"), False),
    ("build.gradle", "dependencies { testImplementation 'org.junit.jupiter:junit-jupiter:5.10.2' }", True),
    ("build.gradle.kts", 'dependencies { testImplementation("org.springframework.boot:spring-boot-starter-test") }', True),
    ("build.gradle", "dependencies { testImplementation 'junit:junit:4.13.2' }", False),
])
def test_junit_params_are_read_from_the_build(tmp_path: Path, name: str, text: str, available: bool) -> None:
    from tools.discover_project import junit_params_available

    (tmp_path / name).write_text(text, encoding="utf-8")
    assert junit_params_available(tmp_path) is available


# --------------------------------------------------------------------------------------
# the report: one status per row
# --------------------------------------------------------------------------------------


def _compat(rows: tuple[str, ...] = tuple(IDS)):
    from tools.run_tests import _compat as compat

    binding = {"path": Path("GroupTest.java"), "relative_path": "src/test/java/p/GroupTest.java",
               "locator": {"kind": "java_class_method", "class_fqn": "p.GroupTest", "method_name": "tc_group_age"}, "node": "tc_group_age",
               "param_rows": rows}
    plain = {"path": Path("GroupTest.java"), "relative_path": "src/test/java/p/GroupTest.java",
             "locator": {"kind": "java_class_method", "class_fqn": "p.GroupTest", "method_name": "plain"}, "node": "plain"}
    pairs = [PAIR, (PAIR[0], "SYMBOL-plain")]
    return compat("READY", pairs, {PAIR: binding, pairs[1]: plain}, {PAIR[0]: "sha256:" + "0" * 64})


def _match(xml: bytes, rows: tuple[str, ...] = tuple(IDS)):
    from tools.run_tests import match_junit_cases, parse_junit_bytes

    return match_junit_cases(parse_junit_bytes(xml), _compat(rows), "RUN-x", {"source_digest": "sha256:" + "1" * 64}, java=True, strict=True)


@pytest.mark.parametrize("fixture", ["surefire-2.22.2.xml", "surefire-3.5.2.xml", "gradle-sample.xml"])
def test_each_row_has_its_own_status_and_case_id(fixture: str) -> None:
    rows, errors = _match((FIXTURES / fixture).read_bytes())
    assert errors == []
    group = next(row for row in rows if row["symbol_id"] == PAIR[1])
    assert group["status"] == "FAILED"
    assert group["rows"] == [{"index": 1, "case_id": "TC-B1-001", "status": "PASSED"},
                             {"index": 2, "case_id": "TC-B1-002", "status": "FAILED"},
                             {"index": 3, "case_id": "TC-B1-003", "status": "PASSED"}]
    assert "rows" not in next(row for row in rows if row["symbol_id"] == "SYMBOL-plain")


def test_all_rows_passing_pass_the_method() -> None:
    xml = (FIXTURES / "surefire-3.5.2.xml").read_text(encoding="utf-8")
    xml = xml.replace('failures="1"', 'failures="0"')
    start = xml.index("<failure")
    xml = xml[:start] + xml[xml.index("</failure>") + len("</failure>"):]
    rows, errors = _match(xml.encode("utf-8"))
    assert errors == [] and {row["status"] for row in rows} == {"PASSED"}


@pytest.mark.parametrize(("edit", "error"), [
    (("tc_group_age(String, int)[3]", "tc_group_age(String, int)"), "row index"),
    (("tc_group_age(String, int)[3]", "tc_group_age(String, int)[4]"), "do not match the group"),
    (('<testcase name="tc_group_age(String, int)[3]" classname="p.GroupTest"/>', ""), "do not match the group"),
])
def test_a_missing_unknown_or_extra_row_index_is_an_error_not_a_pass(edit: tuple, error: str) -> None:
    xml = (FIXTURES / "surefire-3.5.2.xml").read_text(encoding="utf-8").replace(*edit)
    _rows_out, errors = _match(xml.encode("utf-8"))
    assert any(error in message for message in errors), errors


def test_a_repeated_row_index_is_an_error() -> None:
    xml = (FIXTURES / "surefire-3.5.2.xml").read_text(encoding="utf-8")
    extra = '  <testcase name="tc_group_age(String, int)[1]" classname="p.GroupTest" time="1"/>\n</testsuite>'
    _rows_out, errors = _match(xml.replace("</testsuite>", extra).encode("utf-8"))
    assert errors, errors


def test_a_gradle_row_naming_another_case_id_is_an_error() -> None:
    xml = (FIXTURES / "gradle-sample.xml").read_text(encoding="utf-8").replace("[3] TC-B1-003", "[3] TC-B1-002")
    _rows_out, errors = _match(xml.encode("utf-8"))
    assert errors, errors


# --------------------------------------------------------------------------------------
# quarantine: the method whole, its failed and passed rows named
# --------------------------------------------------------------------------------------


def test_a_failed_row_quarantines_the_method_and_names_the_rows() -> None:
    from tools.generated_delta import plan_quarantine

    content = _class(METHOD, VALID)
    files = [{"file_id": PAIR[0], "path": "src/test/java/p/GroupTest.java", "content": content}]
    symbols = [{"file_id": PAIR[0], "symbol_id": PAIR[1],
                "locator": {"kind": "java_class_method", "class_fqn": "p.GroupTest", "method_name": "tc_group_age"}}]
    rows = [{"index": 1, "case_id": "TC-B1-001", "status": "PASSED"}, {"index": 2, "case_id": "TC-B1-002", "status": "FAILED"},
            {"index": 3, "case_id": "TC-B1-003", "status": "PASSED"}]
    payload = {"verdict": "FAIL", "evidence_authoritative": True,
               "execution_evidence": [{"file_id": PAIR[0], "symbol_id": PAIR[1], "status": "FAILED", "rows": rows}],
               "failed_methods": [{"nodeid": f"{PAIR[0]}::{PAIR[1]}", "kind": "assertion_failed", "message": "expected: not equal",
                                   "failed_rows": ["TC-B1-002"]}]}
    plan = plan_quarantine(files, symbols, payload)
    [item] = plan["src/test/java/p/GroupTest.java"]["quarantine"]
    assert item["failed_rows"] == ["TC-B1-002"] and item["disabled_passed_rows"] == ["TC-B1-001", "TC-B1-003"]
    assert item["message"].startswith("rows TC-B1-002")
    marked = plan["src/test/java/p/GroupTest.java"]["content"].decode("utf-8")
    assert '@org.junit.jupiter.api.Disabled("test-skills quarantine: FAIL rows TC-B1-002' in marked
    assert marked.count("@org.junit.jupiter.api.Disabled") == 1  # the whole method, the plain test untouched


def test_the_report_names_the_passed_rows_disabled_with_the_method(tmp_path: Path) -> None:
    from tests.test_run_report import _finished_run
    from tools.run_report import build_report, render_markdown

    run_root = _finished_run(tmp_path)
    report = build_report(tmp_path, run_root.name)[0]
    report["quarantine"] = [{"file": "GroupTest.java", "method": "tc_group_age", "ref": "FAIL", "failed_rows": ["TC-B1-002"],
                             "disabled_passed_rows": ["TC-B1-001", "TC-B1-003"]}]
    markdown = render_markdown(report)
    assert ("- GroupTest.java :: tc_group_age (FAIL); упали строки TC-B1-002; вместе с методом отключены прошедшие строки: "
            "TC-B1-001, TC-B1-003") in markdown


# --------------------------------------------------------------------------------------
# the real Maven, offline: a group method run by its selector, one status per row
# --------------------------------------------------------------------------------------

_JAVA_HOME = os.environ.get("TEST_SKILLS_JAVA_HOME")
_M2 = Path.home() / ".m2" / "repository"


@pytest.mark.skipif(not _JAVA_HOME or not shutil.which("mvn") or not (_M2 / "org/junit/jupiter/junit-jupiter/5.10.2").is_dir()
                    or not (_M2 / "org/apache/maven/plugins/maven-surefire-plugin/3.5.2").is_dir(),
                    reason="needs TEST_SKILLS_JAVA_HOME, mvn and junit-jupiter 5.10.2 / surefire 3.5.2 in ~/.m2 (offline)")
def test_real_maven_runs_the_group_by_its_selector_and_gives_each_row(tmp_path: Path) -> None:
    from tools.execution_adapters import command_for
    from tools.run_tests import _param_group_rows, match_junit_cases, parse_junit_bytes

    project = tmp_path / "real"
    (project / "src/test/java/p").mkdir(parents=True)
    pinned = "".join(f"<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>{name}</artifactId><version>{version}</version></plugin>"
                     for name, version in (("maven-compiler-plugin", "3.13.0"), ("maven-surefire-plugin", "3.5.2"),
                                           ("maven-resources-plugin", "3.3.1")))
    (project / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><groupId>p</groupId><artifactId>real</artifactId><version>1</version>"
        "<properties><maven.compiler.source>8</maven.compiler.source><maven.compiler.target>8</maven.compiler.target>"
        "<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties><dependencies><dependency>"
        "<groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.2</version><scope>test</scope>"
        f"</dependency></dependencies><build><plugins>{pinned}</plugins></build></project>", encoding="utf-8")
    body = METHOD.replace("{ }", "{ org.junit.jupiter.api.Assertions.assertNotEquals(2, value); }")
    source = _class(body, VALID.replace("-3L", "3"))
    (project / "src/test/java/p/GroupTest.java").write_text(source, encoding="utf-8")
    assert _param_group_rows(source, "tc_group_age") == IDS
    _reports, argv = command_for("maven:selected-symbols-v1", shutil.which("mvn"), "default", ("p.GroupTest#tc_group_age",))
    completed = subprocess.run([argv[0], "-o", *argv[1:]], cwd=project, env={**os.environ, "JAVA_HOME": _JAVA_HOME},
                               capture_output=True, text=True, timeout=300)
    assert completed.returncode != 0  # row 2 fails
    report = (project / "target/surefire-reports/TEST-p.GroupTest.xml").read_bytes()
    compat = _compat()
    only_group = type(compat)(compat.status, (PAIR,), {PAIR: compat.bindings[PAIR]}, compat.verified_file_digests, compat.diagnostics)
    rows, errors = match_junit_cases(parse_junit_bytes(report), only_group, "RUN-x", {"source_digest": "sha256:" + "1" * 64}, java=True, strict=True)
    assert errors == [], errors  # the selector ran the group only: no `plain` in the report
    assert [(row["case_id"], row["status"]) for row in rows[0]["rows"]] == [("TC-B1-001", "PASSED"), ("TC-B1-002", "FAILED"), ("TC-B1-003", "PASSED")]


@pytest.mark.parametrize(("name", "method"), [
    ("tc_group_age", "tc_group_age"), ("tc_group_age()", "tc_group_age"),
    ("tc_group_age(String, int)[2]", "tc_group_age"), ("tc_group_age{String, int}[2]", "tc_group_age"),
])
def test_one_reading_of_a_testcase_name_for_the_evidence_and_the_hint(name: str, method: str) -> None:
    """Audit U4: the evidence (_case_matches_pair) and the run_pipeline hint (_report_mismatch) read a name the same way."""
    from tools.run_tests import junit_method_name

    assert junit_method_name(name) == method
