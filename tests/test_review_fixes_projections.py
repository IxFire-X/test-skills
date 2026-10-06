"""Regression tests for the 2026-10-05 pipeline review: projections, blockers and automation validation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation
from tests.test_requirement_traceability import canonical_fixture
from tools.canonical_document import document_sha256, validate_canonical_document


V4 = "zephyr-scale-step-row-24-v4"
V5 = "zephyr-scale-step-row-24-v5"
ROOT = Path(__file__).resolve().parents[1]


def _string_input(input_id: str, location: str, name: str, value: str) -> dict:
    return {
        "input_id": input_id, "display_order": 1,
        "target": {"location": location, "name": name, "sensitive": False},
        "source": {"kind": "literal", "value": value},
        "semantic_type": {"kind": "json", "type": "string"},
    }


def http_post_document() -> dict:
    """One automatable POST /owners/{ownerId}/pets case with a literal path parameter and JSON body."""
    document = canonical_fixture()
    step = document["test_cases"][0]["steps"][0]
    step.update({
        "manual_only": False, "manual_reason": None,
        "action": "Отправить запрос POST /owners/{ownerId}/pets на создание питомца.",
        "operation": {
            "kind": "http", "binding_profile": "http-binding-v1",
            "base_url_source": {"kind": "environment", "name": "BASE_URL", "provenance": ["test"]},
            "method": "POST", "path": "/owners/{ownerId}/pets",
        },
        "inputs": [
            _string_input("INPUT-batch-a-001", "path", "ownerId", "42"),
            {
                "input_id": "INPUT-batch-a-002", "display_order": 2,
                "target": {"location": "body", "pointer": "/name", "sensitive": False},
                "source": {"kind": "literal", "value": "Барсик"},
                "semantic_type": {"kind": "json", "type": "string"},
            },
        ],
        "test_data": "{\n  \"name\": \"Барсик\"\n}",
    })
    step["expectations"][0]["text"] = "Питомец создан и привязан к владельцу.\n\nHTTP 201 Created"
    step["expectations"][0]["assertions"] = [{
        "assertion_id": "ASSERT-batch-a-001", "display_order": 1,
        "actual": {"kind": "http_status"}, "operator": "equals",
        "expected": {"kind": "literal", "value": 201},
    }]
    return document


def _codes(document: dict) -> set[str]:
    return {item["code"] for item in validate_canonical_document(document)}


def test_fixture_documents_are_valid() -> None:
    assert validate_canonical_document(http_post_document()) == []
    assert validate_canonical_document(automated_document()) == []


def _csv_rows(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


# --- B8: the default Zephyr CSV profile carries only the human scenario ---------------------------------

def test_b8_default_csv_profile_is_v5_with_human_fields_only() -> None:
    from tools.test_case_projections import render_zephyr_csv

    document = http_post_document()
    step = document["test_cases"][0]["steps"][0]
    rows = _csv_rows(render_zephyr_csv(document).payload)
    header, row = rows[0], rows[1]
    action, data, expected = (row[header.index(f"Test Script (Step-by-Step) - {name}")] for name in ("Step", "Test Data", "Expected Result"))
    assert action == "Отправить запрос POST /owners/42/pets на создание питомца."
    assert data == step["test_data"]
    assert expected == step["expectations"][0]["text"]
    payload = render_zephyr_csv(document).payload.decode("utf-8-sig")
    for machine in ("HTTP: POST", "базовый URL", "environment:BASE_URL", "Проверка:", "http_status equals", "path:ownerId", "body:/name"):
        assert machine not in payload
    assert render_zephyr_csv(document, V5).payload == render_zephyr_csv(document).payload


def test_b8_v4_csv_stays_available_as_explicit_opt_in(tmp_path: Path) -> None:
    from tools.publish_test_case_bundle import _parser, publish_bundle, verify_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = http_post_document()
    legacy = render_zephyr_csv(document, V4).payload.decode("utf-8-sig")
    assert "HTTP: POST /owners/{ownerId}/pets; базовый URL: environment:BASE_URL" in legacy
    assert "Проверка: http_status equals 201" in legacy
    assert next(action.default for action in _parser()._actions if action.dest == "csv_profile") == V5
    default = publish_bundle(document, tmp_path / "v5")
    assert default.csv_profile == V5 and default.preview_path.endswith(".html")
    assert verify_bundle(document, tmp_path / "v5") == default
    opted = publish_bundle(document, tmp_path / "v4", V4)
    assert opted.csv_profile == V4 and opted.preview_path.endswith(".html")
    assert verify_bundle(document, tmp_path / "v4", V4) == opted
    assert Path(default.csv_path).read_bytes() != Path(opted.csv_path).read_bytes()
    assert Path(default.preview_path).read_bytes() == Path(opted.preview_path).read_bytes()


def test_b8_contract_registers_v5_default_and_v4_opt_in() -> None:
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    profiles = {row["id"]: row["mode"] for row in contract["projection_profiles"]}
    assert profiles[V5] == "default"
    assert profiles[V4] == "opt_in_compatibility"
    receipt = json.loads((ROOT / "schemas" / "orchestrator-output.schema.json").read_text(encoding="utf-8"))["$defs"]["receipt"]
    assert {V4, V5} <= set(receipt["properties"]["csv_profile"]["enum"])


def test_b8_orchestrator_accepts_default_and_opt_in_profiles() -> None:
    import tools.orchestrate_test_case_revision as orchestration

    assert orchestration._PROFILE == V5
    orchestration._validate_profile(V5)
    orchestration._validate_profile(V4)
    with pytest.raises(orchestration.OrchestrationError):
        orchestration._validate_profile("zephyr-scale-step-row-24-v3")


# --- M20: an unknown request-body field can be blocked on /inputs ------------------------------------------

def _inputs_blocker() -> dict:
    return {
        "blocker_id": "BLOCK-batch-a-001", "code": "UNRESOLVED_INPUT_BINDING", "field_path": "/inputs",
        "reason": "Поле species тела запроса не описано в спецификации.", "provenance": ["docs/feature.md"],
    }


def test_m20_inputs_blocker_is_accepted_for_an_unknown_body_field() -> None:
    document = http_post_document()
    step = document["test_cases"][0]["steps"][0]
    step["automation_blockers"] = [_inputs_blocker()]
    step["test_data"] = "{\n  \"name\": \"Барсик\",\n  \"species\": \"<вид питомца>\"\n}"
    assert validate_canonical_document(document) == []


def test_m20_blocker_without_any_unresolved_element_is_still_spurious() -> None:
    document = http_post_document()
    step = document["test_cases"][0]["steps"][0]
    step["automation_blockers"] = [{**_inputs_blocker(), "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions"}]
    assert "SEMANTIC_SPURIOUS_BLOCKER" in _codes(document)


# --- M21: a JSON literal is compatible with a named type of the same representation ---------------------------

_LOCAL_DATE = {"kind": "named", "name": "LocalDate", "representation": "string"}


def project_action_document(literal: object = "2024-01-01") -> dict:
    document = canonical_fixture()
    document["operation_capabilities"] = [{
        "capability_id": "CAP-booking-create", "adapter": "booking", "action": "create",
        "arguments": [{"name": "date", "semantic_type": dict(_LOCAL_DATE), "required": True}],
        "results": [{"name": "date", "semantic_type": dict(_LOCAL_DATE)}],
        "provenance": ["docs/feature.md"],
    }]
    literal_type = {"kind": "json", "type": "integer" if isinstance(literal, int) else "string"}
    step = document["test_cases"][0]["steps"][0]
    step.update({
        "manual_only": False, "manual_reason": None,
        "operation": {"kind": "project_action", "capability_id": "CAP-booking-create"},
        "inputs": [{
            "input_id": "INPUT-batch-a-001", "display_order": 1,
            "target": {"location": "arg", "name": "date", "sensitive": False},
            "source": {"kind": "literal", "value": literal}, "semantic_type": literal_type,
        }],
    })
    step["expectations"][0]["assertions"] = [{
        "assertion_id": "ASSERT-batch-a-001", "display_order": 1,
        "actual": {"kind": "project_result", "name": "date"}, "operator": "equals",
        "expected": {"kind": "literal", "value": literal},
    }]
    return document


def test_m21_string_literal_is_compatible_with_named_string_type() -> None:
    assert validate_canonical_document(project_action_document()) == []


def test_m21_literal_of_another_representation_is_still_rejected() -> None:
    codes = _codes(project_action_document(20240101))
    assert {"SEMANTIC_CAPABILITY_TYPE_MISMATCH", "SEMANTIC_OPERATOR_TYPE"} <= codes


def test_m21_non_literal_json_source_is_still_incompatible_with_named_type() -> None:
    document = project_action_document()
    step = document["test_cases"][0]["steps"][0]
    step["inputs"][0]["source"] = {"kind": "fixture", "name": "booking_date"}
    step["inputs"][0]["type_provenance"] = ["docs/feature.md"]
    assert "SEMANTIC_CAPABILITY_TYPE_MISMATCH" in _codes(document)


# --- M23: regex operands are parsed with the runtime dialect during document validation -----------------------

def _regex_document(pattern: str) -> dict:
    document = automated_document()
    expectation = document["test_cases"][0]["steps"][0]["expectations"][0]
    expectation["assertions"].append({
        "assertion_id": "ASSERT-batch-a-002", "display_order": 2,
        "actual": {"kind": "http_header", "name": "X-Request-Id"}, "operator": "matches",
        "expected": {"kind": "regex", "dialect": "portable-regex-v1", "pattern": pattern},
    })
    return document


@pytest.mark.parametrize("pattern", ["\\d+", "^[0-9]+$", "^[0-9]+", "[0-9]+$", "[А-Я]+", "[0-9", "(ab)+"])
def test_m23_patterns_that_fail_or_mislead_at_runtime_are_rejected_by_validation(pattern: str) -> None:
    rows = [row for row in validate_canonical_document(_regex_document(pattern)) if row["code"] == "SEMANTIC_PORTABLE_REGEX"]
    assert [row["path"] for row in rows] == ["/test_cases/0/steps/0/expectations/0/assertions/1/expected/pattern"]


@pytest.mark.parametrize("pattern, value", [("[0-9]+", "123"), ("[a-f0-9]{8}-req", "0a1b2c3d-req"), ("(^)a[$]", "^a$")])
def test_m23_valid_portable_patterns_pass_validation_and_match_at_runtime(pattern: str, value: str) -> None:
    from tools.assertion_dsl import portable_fullmatch

    assert validate_canonical_document(_regex_document(pattern)) == []
    assert portable_fullmatch(pattern, value)


# --- M24: body-less HTTP steps and non-literal URL parameters stay usable for a manual tester ---------------

@pytest.mark.parametrize("test_data", ["—", "Тело запроса отсутствует.", "Тело запроса не передаётся, параметры указаны в адресе."])
def test_m24_http_step_without_body_does_not_require_one_literal_phrase(test_data: str) -> None:
    document = automated_document()
    document["test_cases"][0]["steps"][0]["test_data"] = test_data
    assert validate_canonical_document(document) == []


def test_m24_bodyless_http_step_still_rejects_unbound_json() -> None:
    document = automated_document()
    document["test_cases"][0]["steps"][0]["test_data"] = "{\n  \"name\": \"Барсик\"\n}"
    assert "SEMANTIC_HUMAN_BODY_UNBOUND" in _codes(document)


def _two_step_document() -> dict:
    """Step 1 creates an owner; step 2 uses its id in the path and an environment value in the query."""
    document = http_post_document()
    case = document["test_cases"][0]
    first = case["steps"][0]
    first["action"] = "Отправить запрос POST /owners на создание владельца."
    first["operation"]["path"] = "/owners"
    first["inputs"] = [first["inputs"][1]]
    first["inputs"][0]["display_order"] = 1
    first["outputs"] = [{
        "output_id": "ownerId", "display_order": 1, "source": {"kind": "http_header", "name": "X-Owner-Id"},
        "semantic_type": {"kind": "json", "type": "string"},
    }]
    second = deepcopy(first)
    second.update({
        "step_id": "STEP-batch-a-002", "display_order": 2, "outputs": [],
        "action": "Отправить запрос GET /owners/{ownerId}/pets на получение питомцев владельца.",
        "test_data": "Тело запроса отсутствует.",
        "inputs": [
            {
                "input_id": "INPUT-batch-a-003", "display_order": 1,
                "target": {"location": "path", "name": "ownerId", "sensitive": False},
                "source": {"kind": "step_output", "step_id": "STEP-batch-a-001", "output_id": "ownerId"},
                "semantic_type": {"kind": "json", "type": "string"},
            },
            {
                "input_id": "INPUT-batch-a-004", "display_order": 2,
                "target": {"location": "query", "name": "region", "sensitive": False},
                "source": {"kind": "environment", "name": "REGION"},
                "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["docs/feature.md"],
            },
            _string_input("INPUT-batch-a-005", "query", "kind", "кот"),
        ],
    })
    second["inputs"][2]["display_order"] = 3
    second["operation"] = {**first["operation"], "method": "GET", "path": "/owners/{ownerId}/pets"}
    second["expectations"] = [{
        "expectation_id": "EXP-batch-a-002", "display_order": 1,
        "text": "Возвращается список питомцев владельца.\n\nHTTP 200 OK",
        "assertions": [{
            "assertion_id": "ASSERT-batch-a-002", "display_order": 1,
            "actual": {"kind": "http_status"}, "operator": "equals", "expected": {"kind": "literal", "value": 200},
        }],
    }]
    case["steps"].append(second)
    return document


def test_m24_non_literal_url_parameters_render_as_human_placeholders() -> None:
    from tools.test_case_projections import render_html_preview, render_markdown, render_zephyr_csv

    document = _two_step_document()
    assert validate_canonical_document(document) == []
    page = render_html_preview(document).payload.decode("utf-8")
    assert "{ownerId}" not in page
    assert "GET /owners/&lt;ownerId из шага 1&gt;/pets?region=&lt;переменная окружения REGION&gt;&amp;kind=%D0%BA%D0%BE%D1%82" in page
    assert "{ownerId}" not in render_markdown(document).payload.decode("utf-8")
    rows = _csv_rows(render_zephyr_csv(document).payload)
    assert "GET /owners/<ownerId из шага 1>/pets?region=<переменная окружения REGION>&kind=%D0%BA%D0%BE%D1%82" in rows[2][19]


def test_m24_bodyless_http_step_shows_authored_test_data_in_html() -> None:
    from tools.test_case_projections import render_html_preview

    document = automated_document()
    document["test_cases"][0]["steps"][0]["test_data"] = "Тело запроса не передаётся, параметры указаны в адресе."
    assert "<td><p>Тело запроса не передаётся, параметры указаны в адресе.</p></td>" in render_html_preview(document).payload.decode("utf-8")


# --- M25: Markdown projection keeps every JSON block and literal text; U+2028 does not break JSON cells -------

def _body_assertion(assertion_id: str, pointer: str, value: object) -> dict:
    return {
        "assertion_id": assertion_id, "display_order": 1,
        "actual": {"kind": "http_body", "pointer": pointer, "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["docs/feature.md"]},
        "operator": "equals", "expected": {"kind": "literal", "value": value},
    }


def test_m25_markdown_fences_the_json_of_every_expectation() -> None:
    from tools.test_case_projections import render_markdown

    document = automated_document()
    step = document["test_cases"][0]["steps"][0]
    first = step["expectations"][0]
    first["text"] = "Возвращается первый товар.\n\nHTTP 200 OK\n\n{\n  \"first\": \"чай\"\n}"
    first["assertions"].append({**_body_assertion("ASSERT-batch-a-002", "/first", "чай"), "display_order": 2})
    step["expectations"].append({
        "expectation_id": "EXP-batch-a-002", "display_order": 2,
        "text": "Возвращается второй товар.\n\n{\n  \"second\": \"кофе\"\n}",
        "assertions": [_body_assertion("ASSERT-batch-a-003", "/second", "кофе")],
    })
    assert validate_canonical_document(document) == []
    text = render_markdown(document).payload.decode("utf-8")
    assert "```json\n{\n  \"first\": \"чай\"\n}\n```" in text
    assert "```json\n{\n  \"second\": \"кофе\"\n}\n```" in text
    assert text.count("```json") == 2


def test_m25_markdown_escapes_block_markers_at_line_start() -> None:
    from tools.test_case_projections import render_markdown

    document = canonical_fixture()
    case = document["test_cases"][0]
    case["preconditions"] = ["1. Пользователь авторизован.", "- каталог не пуст"]
    step = case["steps"][0]
    step["action"] = "1. Открыть каталог товаров."
    step["test_data"] = "Данные:\n---\n1. первый товар\n2) второй товар\n- третий товар\n+ четвёртый товар\n10 товаров"
    step["expectations"][0]["text"] = "---"
    assert validate_canonical_document(document) == []
    lines = render_markdown(document).payload.decode("utf-8").split("\n")
    assert "- 1\\. Пользователь авторизован." in lines
    assert "- \\- каталог не пуст" in lines
    assert "1\\. Открыть каталог товаров." in lines
    for expected in ("\\---", "1\\. первый товар", "2\\) второй товар", "\\- третий товар", "\\+ четвёртый товар", "10 товаров"):
        assert expected in lines
    for raw in ("1. первый товар", "2) второй товар", "- третий товар", "+ четвёртый товар"):
        assert raw not in lines
    # The only thematic breaks left are the case separators the renderer writes itself.
    assert lines.count("---") == len(document["test_cases"])


def test_m25_line_separator_inside_json_string_keeps_the_json_block() -> None:
    from tools.test_case_projections import render_html_preview, render_markdown

    document = http_post_document()
    step = document["test_cases"][0]["steps"][0]
    name = "Бар сик"
    step["inputs"][1]["source"]["value"] = name
    step["test_data"] = "{\n  \"name\": \"" + name + "\"\n}"
    assert validate_canonical_document(document) == []
    page = render_html_preview(document).payload.decode("utf-8")
    assert '<td><pre><code class="language-json">{\n  &quot;name&quot;: &quot;' + name + '&quot;\n}</code></pre></td>' in page
    assert "```json\n{\n  \"name\": \"" + name + "\"\n}\n```" in render_markdown(document).payload.decode("utf-8")


# --- M26: XML export receipt digest prefix; XML-incompatible characters are caught by validation --------------

def test_m26_xml_receipt_digest_is_prefixed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.export_test_cases_xml import main as export_xml

    source = tmp_path / "canonical.json"
    source.write_text(json.dumps(canonical_fixture(), ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "candidate.xml"
    assert export_xml(["--input", str(source), "--output", str(target)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["sha256"] == "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    assert export_xml(["--input", str(source), "--output", str(target), "--verify-only"]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == receipt["sha256"]


@pytest.mark.parametrize("field", ["title", "objective", "action", "test_data", "expectation", "precondition", "label"])
def test_m26_xml_incompatible_control_characters_fail_document_validation(field: str) -> None:
    from tools.test_case_projections import render_zephyr_xml

    document = canonical_fixture()
    case = document["test_cases"][0]
    step = case["steps"][0]
    bad = "Открыть\u0001каталог"
    if field in {"title", "objective"}:
        case[field] = bad
    elif field in {"action", "test_data"}:
        step[field] = bad
    elif field == "expectation":
        step["expectations"][0]["text"] = bad
    elif field == "precondition":
        case["preconditions"] = [bad]
    else:
        case["management"]["labels"] = [bad]
    rows = [row for row in validate_canonical_document(document) if row["code"] == "SEMANTIC_HUMAN_CONTROL_CHARACTER"]
    assert len(rows) == 1 and rows[0]["path"].startswith("/test_cases/0/")
    with pytest.raises(ValueError):
        render_zephyr_xml(document)


def test_m26_tabs_and_newlines_stay_valid_and_exportable() -> None:
    from tools.test_case_projections import render_zephyr_xml

    document = canonical_fixture()
    document["test_cases"][0]["steps"][0]["test_data"] = "Колонка\tзначение\r\nвторая строка"
    assert validate_canonical_document(document) == []
    assert render_zephyr_xml(document).payload


# --- B7: a blocker blocks only its own case ---------------------------------------------------------------

def _blocked_case(number: int = 2, *, with_ready_step: bool = False) -> dict:
    """A case whose HTTP step has an unresolved body field; optionally preceded by a ready step."""
    suffix = f"{number:03d}"
    case = deepcopy(http_post_document()["test_cases"][0])
    case.update({"case_id": f"TC-batch-a-{suffix}", "display_order": number, "title": "Создание питомца с неизвестным полем"})
    blocked = case["steps"][0]
    blocked["step_id"] = f"STEP-batch-a-{suffix}"
    blocked["automation_blockers"] = [{**_inputs_blocker(), "blocker_id": f"BLOCK-batch-a-{suffix}"}]
    blocked["expectations"][0]["expectation_id"] = f"EXP-batch-a-{suffix}"
    blocked["expectations"][0]["assertions"][0]["assertion_id"] = f"ASSERT-batch-a-{suffix}"
    if with_ready_step:
        ready = deepcopy(automated_document()["test_cases"][0]["steps"][0])
        ready["step_id"] = f"STEP-batch-a-{suffix}-ready"
        ready["expectations"][0]["expectation_id"] = f"EXP-batch-a-{suffix}-ready"
        ready["expectations"][0]["assertions"][0]["assertion_id"] = f"ASSERT-batch-a-{suffix}-ready"
        blocked["display_order"] = 2
        case["steps"] = [ready, blocked]
    return case


def partially_blocked_document(*, with_ready_step: bool = False) -> dict:
    """Case 1 is fully automatable, case 2 carries one blocker."""
    document = automated_document()
    document["test_cases"].append(_blocked_case(with_ready_step=with_ready_step))
    assert validate_canonical_document(document) == []
    return document


def _blocked_diagnostic(path: str = "/test_cases/1") -> dict:
    return {"path": path, "code": "CASE_BLOCKED", "message": "Кейс не автоматизирован: в шаге есть неразрешённый blocker."}


def partial_automation(document: dict) -> dict:
    """Automation for the ready case plus dispositions and diagnostics for the blocked one."""
    artifact = automation(document)
    artifact["artifacts"]["manual_dispositions"] = [
        {"case_id": case["case_id"], "step_id": step["step_id"]}
        for case in document["test_cases"] if any(step["automation_blockers"] for step in case["steps"])
        for step in case["steps"]
    ]
    artifact["artifacts"]["diagnostics"] = [_blocked_diagnostic()]
    return artifact


def blocked_automation(document: dict) -> dict:
    artifact = automation(document)
    artifact["artifacts"].update({
        "automation_status": "BLOCKED", "generated_files": [], "generated_symbols": [],
        "implementation_relations": [], "manual_dispositions": [], "diagnostics": [_blocked_diagnostic()],
    })
    return artifact


def _automation_codes(artifact: dict, document: dict) -> list[str]:
    from tools.automation_validation import validate_automation_artifact

    return [row["code"] for row in validate_automation_artifact(artifact, document)]


@pytest.mark.parametrize("with_ready_step", [False, True])
def test_b7_one_blocked_case_does_not_block_automation_of_the_others(with_ready_step: bool) -> None:
    from tools.automation_validation import required_symbol_pairs

    document = partially_blocked_document(with_ready_step=with_ready_step)
    artifact = partial_automation(document)
    assert _automation_codes(artifact, document) == []
    assert required_symbol_pairs(artifact, document) == {("FILE-products", "SYMBOL-products")}


def test_b7_blocked_status_is_rejected_while_another_case_is_automatable() -> None:
    document = partially_blocked_document()
    assert _automation_codes(blocked_automation(document), document) == ["AUTOMATION_BLOCKED_HAS_AUTOMATABLE_CASE"]


def test_b7_blocked_status_is_still_required_when_nothing_is_automatable() -> None:
    document = partially_blocked_document()
    document["test_cases"] = [_blocked_case(1)]
    assert validate_canonical_document(document) == []
    assert _automation_codes(blocked_automation(document), document) == []
    generated = blocked_automation(document)
    generated["artifacts"]["automation_status"] = "GENERATED"
    assert "AUTOMATION_BLOCKER_REQUIRES_BLOCKED" in _automation_codes(generated, document)

    manual = canonical_fixture()["test_cases"][0]
    document["test_cases"] = [manual, _blocked_case(2)]
    assert validate_canonical_document(document) == []
    assert _automation_codes(blocked_automation(document), document) == []


def test_b7_blocked_case_needs_dispositions_and_diagnostics_and_no_relations() -> None:
    document = partially_blocked_document(with_ready_step=True)

    missing = partial_automation(document)
    missing["artifacts"]["manual_dispositions"].pop()
    assert _automation_codes(missing, document) == ["AUTOMATION_MISSING_BLOCKED_DISPOSITION"]

    silent = partial_automation(document)
    silent["artifacts"]["diagnostics"] = []
    assert _automation_codes(silent, document) == ["AUTOMATION_BLOCKED_CASE_DIAGNOSTICS"]

    leaking = partial_automation(document)
    leaking["artifacts"]["implementation_relations"].append({
        "kind": "operation", "case_id": "TC-batch-a-002", "step_id": "STEP-batch-a-002-ready",
        "file_id": "FILE-products", "symbol_id": "SYMBOL-products",
    })
    assert "AUTOMATION_NONREADY_TARGET" in _automation_codes(leaking, document)

    stray = partial_automation(document)
    stray["artifacts"]["manual_dispositions"].insert(0, {"case_id": "TC-batch-a-001", "step_id": "STEP-batch-a-001"})
    assert "AUTOMATION_MANUAL_DISPOSITION_READY" in _automation_codes(stray, document)


def test_b7_document_without_blockers_keeps_generated_rules() -> None:
    document = automated_document()
    assert _automation_codes(automation(document), document) == []
    noisy = automation(document)
    noisy["artifacts"]["diagnostics"] = [_blocked_diagnostic("/test_cases/0")]
    assert _automation_codes(noisy, document) == ["AUTOMATION_GENERATED_DIAGNOSTICS"]
    assert _automation_codes(blocked_automation(document), document) == ["AUTOMATION_BLOCKED_WITHOUT_BLOCKER"]


def _trace_codes(document: dict, artifact: dict) -> set[str]:
    from tools.build_trace_document import _construct
    from tools.schema_validation import schema_diagnostics
    from tools.trace_check import _internal

    trace = _construct(document, artifact, {"review": "stub"}, None)
    assert schema_diagnostics(trace, ROOT / "schemas" / "trace-document.schema.json", ROOT) == []
    return {row["code"] for row in _internal(trace, False)}


@pytest.mark.parametrize("with_ready_step", [False, True])
def test_b7_trace_audit_accepts_partial_automation_and_still_requires_execution(with_ready_step: bool) -> None:
    document = partially_blocked_document(with_ready_step=with_ready_step)
    # The ready case has runnable symbols, so the only thing missing from a run-less trace is execution.
    assert _trace_codes(document, partial_automation(document)) == {"TRACE_EXECUTION_REQUIRED"}


def test_b7_trace_audit_rejects_inconsistent_blocker_branches() -> None:
    document = partially_blocked_document(with_ready_step=True)
    silent = partial_automation(document)
    silent["artifacts"]["diagnostics"] = []
    assert "TRACE_GENERATED_BRANCH" in _trace_codes(document, silent)
    undisposed = partial_automation(document)
    undisposed["artifacts"]["manual_dispositions"].pop()
    assert "TRACE_MANUAL_COVERAGE" in _trace_codes(document, undisposed)
    assert "TRACE_BLOCKED_BRANCH" in _trace_codes(document, blocked_automation(document))

    fully_blocked = deepcopy(document)
    fully_blocked["test_cases"] = [_blocked_case(1)]
    assert _trace_codes(fully_blocked, blocked_automation(fully_blocked)) == set()
    with_manual = deepcopy(document)
    with_manual["test_cases"] = [canonical_fixture()["test_cases"][0], _blocked_case(2, with_ready_step=True)]
    assert validate_canonical_document(with_manual) == []
    assert _trace_codes(with_manual, blocked_automation(with_manual)) == set()


def test_b7_execution_preflight_ignores_ready_steps_of_a_blocked_case() -> None:
    from tools.execution_preflight import _collect_uses

    document = partially_blocked_document(with_ready_step=True)
    providers, _adapters = _collect_uses(document)
    assert {use.path.split("/steps/")[0] for use in providers} == {"/test_cases/0"}


def test_b7_acceptance_predicates_still_forbid_unresolved_blockers() -> None:
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    for profile in ("cases-only-v1", "local-pilot-v1"):
        assert "no_unresolved_blocker" in contract["acceptance_predicates"][profile]
