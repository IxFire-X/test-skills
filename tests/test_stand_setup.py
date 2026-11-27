"""Stand part 4: a module with tests only (``src/test``, no ``src/main``) is found, and ``init_skillsrc`` asks first for the variable of
its stand (or ``no-stand``), then for ``paths.context`` (or ``no-application-code``); ``ready`` warns of a context without the variable."""
from __future__ import annotations

from pathlib import Path

from tools.discover_project import discover_project
from tools.init_skillsrc import ensure_skillsrc
from tools.ready import check_test_level
from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>{}</artifactId><version>1</version></project>"


def _project(tmp_path: Path) -> Path:
    """``app/`` with its code and ``api-tests/`` with test code only (and a Maven wrapper: a test block without ``mvn`` on the host)."""
    for name, folder, java in (("app", "main", "Owner"), ("api-tests", "test", "OwnerApiTest")):
        package = tmp_path / name / "src" / folder / "java" / "org" / "example"
        package.mkdir(parents=True)
        (tmp_path / name / "pom.xml").write_text(POM.format(name), encoding="utf-8")
        (package / f"{java}.java").write_text(f"package org.example;\npublic class {java} {{}}\n", encoding="utf-8")
    for wrapper in ("mvnw", "mvnw.cmd"):
        (tmp_path / "api-tests" / wrapper).write_text("", encoding="utf-8")
    return tmp_path


def _init(root: Path, **chosen: str) -> dict:
    """Answer round by round: a question whose id ends with a key of *chosen* gets that value, any other its first option."""
    answers: dict[str, str] = {}
    for _round in range(4):
        report = ensure_skillsrc(root, answers, True)
        if report["status"] != "needs_input":
            return report
        for question in report["questions"]:
            answers[question["id"]] = next((value for key, value in chosen.items() if question["id"].endswith(key)), question["options"][0]["id"])
    raise AssertionError(report)


def _tests_module(root: Path) -> dict:
    return next(module for module in normalize_skillsrc(load_skillsrc(root / ".skillsrc"))["modules"] if module["root"] == "api-tests")


def test_a_module_with_test_code_only_is_asked_for_its_source(tmp_path: Path) -> None:
    questions = discover_project(_project(tmp_path))["questions"]
    assert ["api-tests/src/test/java:tests-only"] in [option["evidence"] for question in questions for option in question["options"]]


def test_the_first_question_is_the_stand_variable(tmp_path: Path) -> None:
    ids = [question["id"] for question in ensure_skillsrc(_project(tmp_path), {}, False)["questions"]]
    assert [item for item in ids if item.endswith((":service_url_env", ":paths.context"))] == ["module:api-tests:service_url_env"]


def test_no_stand_is_in_process_without_a_context(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _init(root, service_url_env="no-stand")["status"] == "created"
    module = _tests_module(root)
    assert module["test"]["level"] == "in-process" and "service_url_env" not in module and "context" not in module["paths"]


def test_a_stand_without_application_code_has_no_context(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _init(root, service_url_env="PETCLINIC_URL", **{"paths.context": "no-application-code"})["status"] == "created"
    module = _tests_module(root)
    assert (module["test"]["level"], module["service_url_env"]) == ("stand", "PETCLINIC_URL") and "context" not in module["paths"]


def test_ready_warns_of_a_context_without_the_variable() -> None:
    row = check_test_level({"id": "api-tests", "test": {"framework": "junit5"}, "paths": {"context": ["app"]}})
    assert row["level"] == "WARN" and "service_url_env" in row["fix"], row
