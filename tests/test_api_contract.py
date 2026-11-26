"""v0.9: the API contract as the coverage report reads it — endpoints and their response codes, nothing else (v09/coverage-contract-plan.md)."""
from __future__ import annotations

import json

import pytest

YAML = """openapi: 3.0.1
info:
  title: Shop
paths:
  /items/{itemId}:
    parameters:
      - name: itemId
        in: path
    get:
      description: 'reads: one item'
      responses:
        200:
          description: ok
        '404':
          description: none
        "2XX":
          description: any success
        default:
          description: an error
  /orders:
    $ref: './orders.yml'
  /carts:
    post:
      responses: {201: {description: made}}
"""


def test_a_yaml_contract_gives_its_endpoints_and_codes() -> None:
    from tools.api_contract import read_contract

    assert read_contract(YAML.encode("utf-8"))[0] == {("GET", "/items/{itemId}"): ["200", "404", "2XX", "default"], ("POST", "/carts"): []}


def test_what_the_reader_does_not_take_is_named() -> None:
    from tools.api_contract import read_contract

    assert read_contract(YAML.encode("utf-8"))[1] == ["/orders: $ref на весь путь", "POST /carts: ответы в одну строку"]


def test_a_json_contract_under_leading_comment_lines_is_read() -> None:
    from tools.api_contract import read_contract

    body = {"swagger": "2.0", "paths": {"/users": {"post": {"responses": {"201": {}, "422": {}}}}, "/profile": {"$ref": "other.json"}}}
    data = ("// 20210424222319\n// https://example.org/swagger.json\n\n" + json.dumps(body)).encode("utf-8")
    assert read_contract(data) == ({("POST", "/users"): ["201", "422"]}, ["/profile: $ref на весь путь"])


@pytest.mark.parametrize("data", [b"x" * (2 * 1024 * 1024 + 1), b"paths:\n\t/items:\n", b"{\"paths\": "], ids=["over-2-MB", "tab", "broken-json"])
def test_a_file_too_big_with_a_tab_or_broken_json_is_not_read(data: bytes) -> None:
    from tools.api_contract import ContractUnreadable, read_contract

    with pytest.raises(ContractUnreadable):
        read_contract(data)
