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
    found = coverage(DOCUMENT, value, linked, {})
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


def _called(test: str, *codes: str) -> dict:
    return {"tests": {("Tests", test)}, "outcomes": set(codes), "codes": set(codes)}


def test_a_literal_route_beside_the_endpoint_does_not_name_it() -> None:
    from tools.existing_tests import coverage

    calls = {"GET /articles/feed": _called("readsFeed", "200"), "GET /vets": _called("listsVets", "200")}
    linked = {"GET /api/articles/{}": {"CREQ-1"}, "GET /owners/{}": {"CREQ-2"}}
    assert coverage(DOCUMENT, {}, linked, calls)["unnamed"] == ["CREQ-1", "CREQ-2"]


def test_a_check_belongs_to_the_call_it_follows() -> None:
    from tools.existing_tests import _calls

    body = ('mockMvc.perform(put("/api/pets/3")).andExpect(status().isNoContent());\n'
            'mockMvc.perform(get("/api/pets/3")).andExpect(status().isOk());')
    assert [(endpoint, set(codes)) for endpoint, _outcomes, codes in _calls(body)] == [("PUT /api/pets/{}", {"204"}), ("GET /api/pets/{}", {"200"})]


def test_a_python_status_after_its_call_belongs_to_it() -> None:
    from tools.existing_tests import _calls

    body = 'def test_missing(client):\n    response = client.get("/items/17")\n    assert response.status_code == 404\n'
    assert [(endpoint, set(codes)) for endpoint, _outcomes, codes in _calls(body)] == [("GET /items/{}", {"404"})]


def test_a_path_built_with_plus_is_a_template() -> None:
    from tools.existing_tests import _calls

    body = 'mockMvc.perform(put("/api/owners/" + ownerId + "/pets/" + pet.getId()).content(json));'
    assert [endpoint for endpoint, _outcomes, _codes in _calls(body)] == ["PUT /api/owners/{}/pets/{}"]


def test_the_contract_table_counts_and_orders_its_rows() -> None:
    from tools.existing_tests import contract_table

    endpoints = {("GET", "/items/{itemId}"): ["200", "404"], ("DELETE", "/items/{itemId}"): ["200"], ("GET", "/oops"): ["200"]}
    calls = {"GET /api/items/{}": {"tests": {("T", "a"), ("T", "b")}, "outcomes": {"200"}, "codes": {"200"}}, "DELETE /api/items/{}": _called("c", "204")}
    table = contract_table("openapi.yml", endpoints, [], calls)
    assert ([row["endpoint"] for row in table["rows"]], table["untested"], table["checked_pairs"], table["pairs"], table["extra"]) == (
        ["GET /oops", "DELETE /items/{itemId}", "GET /items/{itemId}"], 1, 1, 4, 1)


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


def test_a_run_of_cases_only_without_a_contract_has_no_section(tmp_path: Path) -> None:
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


CONTRACT = "openapi: 3.0.1\npaths:\n  /items/{itemId}:\n    get:\n      responses:\n        '200':\n          description: ok\n"


def test_a_contract_in_the_test_data_is_not_the_project_contract(tmp_path: Path) -> None:
    from tools.existing_tests import run_coverage

    (tmp_path / "src" / "test" / "resources").mkdir(parents=True)
    (tmp_path / "src" / "test" / "resources" / "openapi.yml").write_text(CONTRACT, encoding="utf-8")
    assert run_coverage(*_run(tmp_path, with_tests=True), DOCUMENT_OF_PART0) is None  # a run of cases only: nothing else to show


def test_an_unreadable_contract_is_said_under_its_heading() -> None:
    from tools.run_report import _coverage_lines

    lines = _coverage_lines({"existing_coverage": {"unreadable_contracts": [{"path": "api/openapi.yml", "reason": "табы в отступах"}]}})
    assert lines[-4:] == ["### По контракту `api/openapi.yml`", "", "контракт не прочитан: табы в отступах — таблицы по нему нет", ""]  # approved on 26.11


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_the_contract_table_is_in_both_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    from tools import existing_tests

    (tmp_path / "openapi.yml").write_text(CONTRACT, encoding="utf-8")  # in the project before its scan
    monkeypatch.setattr(existing_tests, "_profile", lambda _root: profile)
    found = existing_tests.run_coverage(*_run(tmp_path, with_tests=True), DOCUMENT_OF_PART0)
    assert ([table["path"] for table in found["contracts"]], "rows" in found) == (["openapi.yml"], profile == "local-pilot-v1")


def test_the_contract_part_says_the_approved_texts() -> None:
    from tools.run_report import _contract_lines

    table = {"path": "src/main/resources/openapi.yml", "endpoints": 2, "untested": 1, "pairs": 5, "checked_pairs": 1, "extra": 1, "unread": 1,
             "rows": [{"endpoint": "GET /oops", "codes": ["200", "304"], "checked": [], "extra": [], "tests": 0},
                      {"endpoint": "DELETE /owners/{ownerId}", "codes": ["200", "404", "500"], "checked": ["404"], "extra": ["204"], "tests": 2}]}
    assert _contract_lines(table) == [
        "### По контракту `src/main/resources/openapi.yml`", "",
        "адресов без единого теста: 1 из 2", "",
        "проверенных пар «адрес + код ответа»: 1 из 5", "",
        "тесты ждут код ответа, которого нет в контракте: 1 адрес", "",
        "в контракте не прочитано: 1 место ($ref на весь путь, ответы в одну строку) — эти адреса в таблице без кодов", "",
        "| Адрес | Коды ответа в контракте | Проверены тестами проекта | Тестов |", "|---|---|---|---|",
        "| `GET /oops` | 200, 304 | — | 0 |",
        "| `DELETE /owners/{ownerId}` | 200, 404, 500 | 404 (тесты ждут 204 — не из контракта) | 2 |", ""]


def test_a_big_contract_shows_a_hundred_rows_and_names_the_rest() -> None:
    from tools.run_report import _contract_lines

    rows = [{"endpoint": f"GET /items{index}", "codes": ["200"], "checked": ["200"], "extra": [], "tests": 1} for index in range(102)]
    table = {"path": "openapi.yml", "endpoints": 102, "untested": 0, "pairs": 102, "checked_pairs": 102, "extra": 0, "unread": 0, "rows": rows}
    lines = _contract_lines(table)
    assert (sum(line.startswith("| `GET") for line in lines), lines[-2]) == (100, "ещё 2 адреса")


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


def test_a_contract_path_with_a_backtick_stays_one_code_span() -> None:
    from tools.run_report import _coverage_lines

    lines = _coverage_lines({"existing_coverage": {"unreadable_contracts": [{"path": "api/we`ird.yml", "reason": "табы в отступах"}]}})
    assert "### По контракту `` api/we`ird.yml ``" in lines

