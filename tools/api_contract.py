"""The API contract of a project (OpenAPI 3 or Swagger 2) as the coverage report reads it: the endpoints it lists and the response codes
each one documents — nothing else of the file (no description, no schema, no example reaches the report).

The package carries no YAML library, so JSON is read with ``json`` and YAML only in the subset the report needs, line by line with string
operations — no regular expression over a line, no recursion over the nesting: ``paths`` → a path → a method → ``responses`` → its codes.
What it does not take it does not guess: a path that is only a ``$ref`` and ``responses`` written in one line (``{…}``) are named in
``unread`` and their endpoints have no codes.  A file over ``LIMIT`` bytes, a tab in the indentation or JSON that does not parse is not
read at all (``ContractUnreadable``).  ``read_contract`` is pure; ``is_api_contract`` reads the head of a project file through
``output_guard``."""
from __future__ import annotations

import json
import re
from pathlib import Path

LIMIT = 2 * 1024 * 1024
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
SUFFIXES = (".yml", ".yaml", ".json")
REF = "$ref на весь путь"
FLOW = "ответы в одну строку"
_CODE = re.compile(r"[1-5](?:\d\d|XX)|default", re.IGNORECASE)
_HEAD = re.compile(rb"(?m)^(?:[ \t{]*\"(?:openapi|swagger)\"|(?:openapi|swagger))[ \t]*:[ \t]*[\"']?[0-9]")  # YAML — the key at the line start; JSON — quoted, maybe indented


class ContractUnreadable(ValueError):
    """The file as a whole is not read: too big, a tab in the indentation, JSON that does not parse."""


def is_api_contract(project: Path, project_path: str) -> bool:
    """A ``.yml``/``.yaml``/``.json`` file of the project whose first 4 KB declare ``openapi`` or ``swagger`` with a version."""
    from tools.output_guard import project_file

    if not project_path.lower().endswith(SUFFIXES):
        return False
    try:
        with project_file(project, project_path).open("rb") as handle:
            return _HEAD.search(handle.read(4096)) is not None
    except (OSError, ValueError):
        return False


def _key(text: str) -> tuple[str | None, str]:
    """``(key, the rest after the colon)`` of a mapping line without its indentation, or ``(None, "")``."""
    if text[0] in "'\"":
        close = text.find(text[0], 1)
        after = text[close + 1:].lstrip(" ") if close > 0 else ""
        return (text[1:close], after[1:].strip()) if after.startswith(":") else (None, "")
    colon = text.find(": ")
    if colon >= 0:
        return text[:colon].rstrip(), text[colon + 2:].strip()
    bare = text.rstrip()
    return (bare[:-1].rstrip(), "") if bare.endswith(":") else (None, "")


def _codes(responses: object) -> list[str]:
    return [str(code) for code in responses if _CODE.fullmatch(str(code))] if isinstance(responses, dict) else []


def _from_json(text: str) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    try:
        paths = json.loads(text).get("paths")
    except (ValueError, RecursionError, AttributeError) as error:
        raise ContractUnreadable("JSON не разбирается") from error
    endpoints: dict[tuple[str, str], list[str]] = {}
    unread: list[str] = []
    for path, item in (paths.items() if isinstance(paths, dict) else []):
        if not isinstance(item, dict):
            continue
        methods = [method for method in METHODS if isinstance(item.get(method), dict)]
        if "$ref" in item and not methods:
            unread.append(f"{path}: {REF}")
        for method in methods:
            endpoints[(method.upper(), str(path))] = _codes(item[method].get("responses"))
    return endpoints, unread


def _from_yaml(lines: list[str]) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    endpoints: dict[tuple[str, str], list[str]] = {}
    unread: list[str] = []
    stack: list[tuple[int, str]] = []  # (indentation, key) from the top of the document to this line
    for line in lines:
        text = line.lstrip(" ")
        if not text or text.startswith("#"):
            continue
        if text.startswith("\t"):
            raise ContractUnreadable("табы в отступах")
        if text.startswith("-"):
            continue  # an item of a list (servers, tags, parameters) is no key on the way paths → method → responses
        key, rest = _key(text)
        if key is None:
            continue
        indent = len(line) - len(text)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if stack[0] != (0, "paths") or len(stack) < 3:
            continue
        path, method = stack[1][1], stack[2][1].lower()
        if len(stack) == 3 and key == "$ref":
            unread.append(f"{path}: {REF}")
        elif len(stack) == 3 and method in METHODS:
            endpoints[(method.upper(), path)] = []
            if rest.startswith("{"):
                unread.append(f"{method.upper()} {path}: {FLOW}")
        elif len(stack) == 4 and method in METHODS and key == "responses" and rest.startswith("{"):
            unread.append(f"{method.upper()} {path}: {FLOW}")
        elif len(stack) == 5 and method in METHODS and stack[3][1] == "responses" and _CODE.fullmatch(key):
            endpoints[(method.upper(), path)].append(key)
    return endpoints, unread


def read_contract(data: bytes) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    """``({(METHOD, path as written): [codes]}, [what was not read and why])`` of the bytes of a contract."""
    if len(data) > LIMIT:
        raise ContractUnreadable(f"больше {LIMIT // (1024 * 1024)} МБ")
    lines = data.decode("utf-8", errors="replace").lstrip("﻿").splitlines()
    first = 0
    while first < len(lines) and (not lines[first].strip() or lines[first].lstrip().startswith("//")):
        first += 1  # whole leading lines only: a JSON file saved with a comment on top (realworld doc/swagger.json)
    lines = lines[first:]
    if lines and lines[0].lstrip().startswith("{"):
        return _from_json("\n".join(lines))
    return _from_yaml(lines)
