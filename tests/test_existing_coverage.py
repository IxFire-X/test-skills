"""v0.9: «Покрытие существующими тестами» — the classes and endpoints the accepted cases check, named by the project's own tests or not
(v09/coverage-map-plan.md), in a run of mode B only (the user, 26.11).  The texts of the section are approved by the user on 26.11 and pinned here."""
from __future__ import annotations

from pathlib import Path

import pytest


def _case(requirement: str, *steps: dict, setup: list[dict] | None = None) -> dict:
    return {"requirement_ids": [requirement], "steps": list(steps), **({"setup": setup} if setup else {})}


def _action(capability: str) -> dict:
    return {"operation": {"kind": "project_action", "capability_id": capability}}


DOCUMENT = {"requirements": [{"requirement_id": "CREQ-1"}, {"requirement_id": "CREQ-2"}],
            "operation_capabilities": [{"capability_id": "CAP-OWNER", "provenance": ["src/Owner.java:12-20 — the owner", "docs/req.md — R1"]},
                                       {"capability_id": "CAP-REPO", "provenance": ["src/Repo.java — saves"]}],
            "test_cases": [_case("CREQ-1", _action("CAP-OWNER"), setup=[_action("CAP-REPO")])]}


def test_a_class_the_operation_of_a_step_names_is_linked_to_the_requirement() -> None:
    from tools.existing_tests import linked_code

    assert linked_code(DOCUMENT, ["src/Owner.java", "src/Repo.java"]) == {"src/Owner.java": {"CREQ-1"}}  # the setup links nothing


def test_an_http_step_links_its_endpoint() -> None:
    from tools.existing_tests import linked_code

    step = {"operation": {"kind": "http", "method": "POST", "path": "/owners"}}
    assert linked_code({"test_cases": [_case("CREQ-1", step)]}, []) == {"POST /owners": {"CREQ-1"}}


def test_a_requirement_whose_classes_no_test_names_is_counted() -> None:
    from tools.existing_tests import coverage

    value = {"Owner": {"OwnerTests": {"createsOwner": ["201"]}}}
    linked = {"src/Owner.java": {"CREQ-1"}, "src/Pet.java": {"CREQ-2"}, "src/Visit.java": {"CREQ-1"}}
    found = coverage(DOCUMENT, value, linked)
    assert (found["linked"], found["unnamed"], [row["key"] for row in found["rows"]]) == (2, ["CREQ-2"], ["Pet", "Visit", "Owner"])


def test_the_provenance_names_the_file_and_the_endpoint_anywhere_in_its_text() -> None:
    from tools.existing_tests import linked_code

    document = {"operation_capabilities": [{"capability_id": "CAP-OPEN", "provenance": ["GET /owners/{ownerId}: OwnerController (src/Owner.java:45-60)"]}],
                "test_cases": [_case("CREQ-1", _action("CAP-OPEN"))]}
    assert linked_code(document, ["src/Owner.java"]) == {"src/Owner.java": {"CREQ-1"}, "GET /owners/{}": {"CREQ-1"}}


def test_the_data_of_a_literal_path_is_a_template() -> None:
    from tools.existing_tests import linked_code

    inputs = [{"target": {"name": name}, "source": {"kind": "literal", "value": value}} for name, value in (("method", "DELETE"), ("path", "/api/profiles/anna-b1-001/follow"))]
    assert linked_code({"test_cases": [_case("CREQ-1", {"inputs": inputs})]}, []) == {"DELETE /api/profiles/{}/follow": {"CREQ-1"}}


def test_a_value_of_the_case_is_any_segment_of_a_test_but_not_the_other_way() -> None:
    from tools.existing_tests import coverage

    value = {"GET /api/articles/{}": {"ArticleTests": {"readsArticle": ["200"]}}}
    linked = {"GET /api/articles/{}": {"CREQ-1"}, "GET /api/articles/feed": {"CREQ-2"}}
    assert coverage(DOCUMENT, value, linked)["unnamed"] == ["CREQ-2"]


def test_for_the_report_a_class_the_test_file_names_at_its_head_is_named_by_its_tests(tmp_path: Path) -> None:
    from tests.test_existing_tests import _write
    from tools.existing_tests import build_map

    tests = ("@WebMvcTest(OwnerController.class)\nclass OwnerControllerTests {\n    @Test\n    void findsOwners() throws Exception {\n"
             "        mockMvc.perform(get(\"/owners\")).andExpect(status().isOk());\n    }\n}\n")
    rows = [_write(tmp_path, "src/main/java/OwnerController.java", "class OwnerController {}\n"), _write(tmp_path, "src/test/java/OwnerControllerTests.java", tests)]
    context = ["src/main/java/OwnerController.java"]
    assert "OwnerController" in build_map(tmp_path, {"files": rows}, context, file_level=True)
    assert "OwnerController" not in build_map(tmp_path, {"files": rows}, context)  # the generator's map stays as it was


def _run(tmp_path: Path, with_tests: bool) -> tuple[Path, str]:
    from tests.test_existing_tests import _generator_run_with_tests
    from tools import pilot_state

    root, _context = _generator_run_with_tests(tmp_path, with_tests=with_tests)  # a run of cases only
    return root, str(pilot_state.derive_state(root)["attempts"][-1]["attempt_id"])


DOCUMENT_OF_PART0 = {"operation_capabilities": [{"capability_id": "CAP-X", "provenance": ["src/part0.py — X"]}], "test_cases": [_case("CREQ-1", _action("CAP-X"))]}


def test_a_run_of_cases_only_has_no_section(tmp_path: Path) -> None:
    from tools.existing_tests import run_coverage

    assert run_coverage(*_run(tmp_path, with_tests=True), DOCUMENT_OF_PART0) is None


def test_without_tests_of_its_own_the_project_has_no_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import existing_tests

    monkeypatch.setattr(existing_tests, "_profile", lambda _root: "local-pilot-v1")  # a run of mode B
    assert existing_tests.run_coverage(*_run(tmp_path, with_tests=False), DOCUMENT_OF_PART0) is None


def test_a_run_of_mode_b_whose_tests_name_the_linked_module_has_its_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import existing_tests

    monkeypatch.setattr(existing_tests, "_profile", lambda _root: "local-pilot-v1")
    assert existing_tests.run_coverage(*_run(tmp_path, with_tests=True), DOCUMENT_OF_PART0) == {
        "rows": [{"key": "part0", "tests": 1, "outcomes": [], "requirements": ["CREQ-1"]}], "linked": 1, "unnamed": []}


def test_the_section_says_the_approved_texts() -> None:
    from tools.run_report import _coverage_lines

    found = {"rows": [{"key": "Pet", "tests": 0, "outcomes": [], "requirements": ["CREQ-2"]},
                      {"key": "Owner", "tests": 2, "outcomes": ["201", "404"], "requirements": ["CREQ-1"]}], "linked": 2, "unnamed": ["CREQ-2"]}
    assert _coverage_lines({"existing_coverage": found}) == [
        "## Покрытие существующими тестами (по тексту тестов)", "",
        "Оценка по тексту тестов проекта, тесты не запускались. «Назван тестами» — класс или адрес прямо упомянут в тесте; "
        "класс может проверяться и косвенно, через другой класс.", "",
        "требований, у которых ни один связанный класс не назван тестами: 1 из 2 (CREQ-2)", "",
        "| Класс или адрес | Тестов, прямо называющих | Проверяемые исходы | Связанные требования |", "|---|---|---|---|",
        "| Pet | — | — | CREQ-2 |", "| Owner | 2 | 201, 404 | CREQ-1 |", ""]
