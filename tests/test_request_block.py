"""v0.9: the request block «Запрос curl …» of an HTTP step in the v6 CSV and preview — built by the code from the machine layer
(`http`, or a project action of the strict form), texts approved by the user on 26.11 (v09/postman-curl-plan.md §1)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shlex
from pathlib import Path

from tools.test_case_projections import REQUEST_HEAD, render_html_preview, render_zephyr_csv, request_block

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
V5, V6 = "zephyr-scale-step-row-24-v5", "zephyr-scale-step-row-24-v6"
CASE = {"setup": []}


def _in(order: int, target: dict, source: dict, kind: str = "string") -> dict:
    return {"display_order": order, "target": {"sensitive": source["kind"] == "secret_handle", **target}, "source": source,
            "semantic_type": {"kind": "json", "type": kind}}


def _arg(order: int, name: str, value) -> dict:
    return _in(order, {"location": "arg", "name": name}, {"kind": "literal", "value": value})


def _action(*inputs: dict) -> dict:
    return {"operation": {"kind": "project_action", "capability_id": "CAP-web"}, "inputs": list(inputs)}


def test_an_http_step_gives_path_query_header_and_body() -> None:
    step = {"operation": {"kind": "http", "binding_profile": "http-binding-v1", "method": "PUT", "path": "/owners/{ownerId}",
                          "base_url_source": {"kind": "environment", "name": "PETCLINIC_URL", "provenance": ["README"]}},
            "inputs": [_in(1, {"location": "path", "name": "ownerId"}, {"kind": "step_output", "step_id": "STEP-1", "output_id": "ownerId"}),
                       _in(2, {"location": "query", "name": "lang"}, {"kind": "literal", "value": "ru"}),
                       _in(3, {"location": "header", "name": "X-Trace"}, {"kind": "literal", "value": "t1"}),
                       _in(4, {"location": "body", "pointer": "/firstName"}, {"kind": "literal", "value": "Анна"}),
                       _in(5, {"location": "body", "pointer": "/age"}, {"kind": "step_output", "step_id": "STEP-1", "output_id": "age"}, "integer")]}
    assert request_block(step, {"STEP-1": 1}, CASE) == "\n".join((
        REQUEST_HEAD,
        "curl -X PUT '{{PETCLINIC_URL}}/owners/{{ownerId}}?lang=ru' -H 'X-Trace: t1' -H 'Content-Type: application/json' "
        "--data-raw '{\"firstName\":\"Анна\",\"age\":{{age}}}'",
        "{{PETCLINIC_URL}} — адрес стенда (переменная окружения PETCLINIC_URL); {{ownerId}} — ownerId из ответа шага 1; "
        "{{age}} — age из ответа шага 1."))


def test_a_project_action_of_the_strict_form_gives_a_json_body() -> None:
    step = _action(_arg(1, "method", "POST"), _arg(2, "path", "/owners"), _arg(3, "body", {"lastName": "Петрова", "firstName": "Анна"}))
    assert request_block(step, {}, CASE) == "\n".join((
        REQUEST_HEAD,
        "curl -X POST '{{baseUrl}}/owners' -H 'Content-Type: application/json' --data-raw '{\"firstName\":\"Анна\",\"lastName\":\"Петрова\"}'",
        "{{baseUrl}} — адрес стенда."))


def test_params_are_the_query_of_a_get_and_the_form_of_a_post() -> None:
    get = _action(_arg(1, "method", "GET"), _arg(2, "path", "/owners"), _arg(3, "params", {"lastName": "Петрова"}))
    post = _action(_arg(1, "method", "POST"), _arg(2, "path", "/owners/new"), _arg(3, "params", {"city": "Тверь, 1"}))
    assert request_block(get, {}, CASE).splitlines()[1] == "curl -X GET '{{baseUrl}}/owners?lastName=%D0%9F%D0%B5%D1%82%D1%80%D0%BE%D0%B2%D0%B0'"
    assert request_block(post, {}, CASE).splitlines()[1] == "curl -X POST '{{baseUrl}}/owners/new' --data-urlencode 'city=Тверь, 1'"


def test_an_action_whose_helper_builds_the_request_has_no_block() -> None:
    step = _action(_arg(1, "method", "GET"), _arg(2, "path", "/owners"), _arg(3, "ownerId", 7))
    odd = _action(_arg(1, "method", {"verb": "GET"}), _arg(2, "path", "/owners"))
    assert request_block(step, {}, CASE) is None and request_block(odd, {}, CASE) is None


def test_a_secret_is_only_a_variable() -> None:
    http = {"operation": {"kind": "http", "binding_profile": "http-binding-v1", "method": "GET", "path": "/owners",
                          "base_url_source": {"kind": "environment", "name": "BASE", "provenance": ["README"]}},
            "inputs": [_in(1, {"location": "query", "name": "token"}, {"kind": "secret_handle", "handle": "vault://never", "safe_label": "токен API"})]}
    action = _action(_arg(1, "method", "GET"), _arg(2, "path", "/owners"), _arg(3, "headers", {"Authorization": "Basic c2VjcmV0"}))
    blocks = request_block(http, {}, CASE) + request_block(action, {}, CASE)
    assert "vault://" not in blocks and "c2VjcmV0" not in blocks
    assert "{{токен API}} — секрет, в файл не попадает" in blocks and "-H 'Authorization: {{Authorization}}'" in blocks


def test_a_quote_in_a_value_stays_one_argument() -> None:
    body = {"name": "x'; rm -rf ~; echo '"}
    line = request_block(_action(_arg(1, "method", "POST"), _arg(2, "path", "/owners"), _arg(3, "body", body)), {}, CASE).splitlines()[1]
    assert shlex.split(line)[-2:] == ["--data-raw", json.dumps(body, ensure_ascii=False, separators=(",", ":"))]


def test_the_v5_bytes_stay() -> None:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert [hashlib.sha256(render(document, V5).payload).hexdigest() for render in (render_zephyr_csv, render_html_preview)] == [
        "a2c0228dee0585e891cb5c7dd5b3cc15e936e712f51bdbc48d8c51d6a2632b78", "a76975e3318a38bf5e9dd00753a24b4a3c5af80a87829a381aa158680a56e77f"]


def test_the_reader_drops_the_block_but_not_an_edited_one() -> None:
    from tools.zephyr_csv import parse_zephyr_csv

    payload = render_zephyr_csv(json.loads(EXAMPLE.read_text(encoding="utf-8")), V6).payload
    assert REQUEST_HEAD.encode() in payload and REQUEST_HEAD not in json.dumps(parse_zephyr_csv(payload), ensure_ascii=False)
    edited = payload.decode("utf-8-sig").replace(REQUEST_HEAD, "Мой запрос:").encode("utf-8-sig")
    assert "Мой запрос:" in json.dumps(parse_zephyr_csv(edited), ensure_ascii=False)


def test_the_preview_shows_the_block_outside_a_group_only() -> None:
    from tests.test_param_groups import grouped
    from tools.canonical_document import param_groups

    document = grouped()
    in_groups = {case["case_id"] for group in param_groups(document) for case in group["cases"]}
    outside = sum(request_block(step, {item["step_id"]: item["display_order"] for item in case["steps"]}, case) is not None
                  for case in document["test_cases"] if case["case_id"] not in in_groups for step in case["steps"])
    assert render_html_preview(document, V6).payload.decode("utf-8").count(REQUEST_HEAD) == outside > 0
    assert REQUEST_HEAD not in render_html_preview(document, V5).payload.decode("utf-8")
    rows = list(csv.reader(io.StringIO(render_zephyr_csv(document, V6).payload.decode("utf-8-sig"), newline="")))
    assert sum(REQUEST_HEAD in cell for row in rows for cell in row) > outside  # the CSV has the block of every variant too
