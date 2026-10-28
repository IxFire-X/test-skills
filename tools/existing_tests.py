"""FaT-EC: the map of what the project's own tests already exercise — a hint to the case generator and its reviewer.

Code reads only the test files of the frozen scan inventory (by their content digest) and never runs them.  A key is an
HTTP endpoint the tests call (``МЕТОД /путь``, numbers and ``{x}`` as ``{}``) or a production class (Java) or module
(Python) of the batch context that a test names.  Under a key: the test classes and up to ``NAMES`` method names, else
their count, and per method up to ``OUTCOMES`` outcomes — an HTTP status, a view name, an exception class.  No code, no
test bodies, no data literals.  The map is cut at ``LIMIT`` bytes with a line «ещё N ключей».

The map is a pure function of the inventory, the project files behind it and the classes of the context: the same inputs
give the same bytes.  ``repeats`` — the cases whose HTTP step calls an endpoint a project test calls (path compared by
whole segments from the end: ``/owners`` is ``/api/owners``, not ``/petowners``) with the same outcome; one function for
the reviewer's hint ``CV_EXISTING_TEST`` and the run report.  A hint, never a verdict: a case for a requirement stays.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

HINT = "CV_EXISTING_TEST"
LIMIT = 16_000
NAMES = 3
OUTCOMES = 3
SUFFIX = ".existing-tests.md"

_TEST_ANNOTATION = re.compile(r"@\s*(?:Test|ParameterizedTest|RepeatedTest)\b")
_METHOD_NAME = re.compile(r"\b(\w+)\s*\(")
_HTTP_CALL = re.compile(r"\b(get|post|put|delete|patch)\s*\(\s*\"(/[^\"]*)\"", re.IGNORECASE)
_STATUS = re.compile(r"status\(\)\s*\.\s*(is\w+)\s*\(\s*(\d{3})?\s*\)|HttpStatus\s*\.\s*([A-Z_]+)|statusCode\s*\(\s*(\d{3})\s*\)"
                     r"|status_code\s*==\s*(\d{3})")
_VIEW = re.compile(r"view\(\)\s*\.\s*name\s*\(\s*\"([^\"]+)\"\s*\)")
# the model attribute (first quoted name) is left out, the field names after it are kept
_FIELD_ERRORS = re.compile(r"attributeHasFieldErrors\s*\(\s*\"\w+\"\s*,((?:\s*\"\w+\"\s*,?)+)\)"
                           r"|attributeHasFieldErrorsCode\s*\(\s*\"\w+\"\s*,(\s*\"\w+\")")
_EXCEPTION = re.compile(r"\b([A-Z]\w*(?:Exception|Error))\s*\.\s*class\b|pytest\.raises\s*\(\s*([A-Z]\w*(?:Exception|Error))\b")
_NAMED_STATUS = {"isOk": 200, "isCreated": 201, "isAccepted": 202, "isNoContent": 204, "isFound": 302, "isBadRequest": 400,
                 "isUnauthorized": 401, "isForbidden": 403, "isNotFound": 404, "isConflict": 409, "OK": 200, "CREATED": 201,
                 "NO_CONTENT": 204, "FOUND": 302, "BAD_REQUEST": 400, "UNAUTHORIZED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404,
                 "CONFLICT": 409}
_PY_TEST = re.compile(r"^(?:async\s+)?def\s+(test_\w+)\s*\(", re.MULTILINE)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ]+)|import\s+([\w., ]+))", re.MULTILINE)


def _normal_path(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path.split("?")[0])
    path = re.sub(r"(?<=/)\d+(?=/|$)", "{}", path)
    return path.rstrip("/") or "/"


def _outcomes(body: str) -> list[str]:
    """Up to ``OUTCOMES`` names and numbers, the most specific first: fields with errors, exception classes, statuses, views."""
    found = [f"field {name}" for fields, code_field in _FIELD_ERRORS.findall(body) for name in re.findall(r"\"(\w+)\"", fields or code_field)]
    found += [java or python for java, python in _EXCEPTION.findall(body)]
    for named, number, constant, code, python_code in _STATUS.findall(body):
        value = number or code or python_code or _NAMED_STATUS.get(named or constant)
        if value:
            found.append(str(value))
    found += [f"view {name}" for name in _VIEW.findall(body)]
    return list(dict.fromkeys(found))[:OUTCOMES]


def _without_comments(text: str) -> str:
    """Java text with comments blanked and string literals kept (the paths and view names are in strings)."""
    result, index, state = list(text), 0, "code"
    while index < len(text):
        char = text[index]
        if state == "code":
            if text.startswith("//", index):
                state = "line"
                continue
            if text.startswith("/*", index):
                state = "block"
                continue
            if char == '"':
                state = "string"
        elif state == "string":
            if char == "\\":
                index += 2
                continue
            if char == '"' or char == "\n":
                state = "code"
        elif state == "line":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
        elif state == "block":
            if text.startswith("*/", index):
                result[index:index + 2] = "  "
                index += 2
                state = "code"
                continue
            if char != "\n":
                result[index] = " "
        index += 1
    return "".join(result)


def _java_methods(text: str) -> Iterable[tuple[str, str]]:
    """(name, body) of each test method: from its annotation to the next test annotation (comments and strings of the
    method head masked, its body kept for the quoted paths and view names)."""
    from tools.run_tests import _mask_java_noncode

    masked = _mask_java_noncode(text)
    text = _without_comments(text)
    marks = [match.start() for match in _TEST_ANNOTATION.finditer(masked)]
    for index, start in enumerate(marks):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        head = masked[start:end]
        brace = head.find("{")
        name = _METHOD_NAME.findall(head[:brace] if brace >= 0 else head)
        if name:
            yield name[-1], text[start:end]


def _python_tests(text: str) -> Iterable[tuple[str, str]]:
    marks = list(_PY_TEST.finditer(text))
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        yield match.group(1), text[match.start():end]


def _python_names(text: str) -> set[str]:
    names: set[str] = set()
    for module, imported, plain in _PY_IMPORT.findall(text):
        for part in (module.split(".") if module else []) + [item.strip() for item in imported.split(",")]:
            names.add(part.strip())
        for item in plain.split(","):
            names.update(item.strip().split(" ")[0].split("."))
    return {name for name in names if name}


def _excluded(project: Path) -> set[str]:
    """Files the package wrote into the project (materialization ownership of any run): not the project's own tests."""
    paths: set[str] = set()
    for receipt in sorted((project / ".pilot-runs").glob("*/materialization-ownership/*.json")):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8")).get("payload") or {}
        except (OSError, ValueError):
            continue
        for row in payload.get("files") or []:
            if isinstance(row, Mapping):
                paths.update(str(row[key]) for key in ("path", "project_path") if isinstance(row.get(key), str))
    return paths


def build_map(project: Path, inventory: Mapping[str, Any], context_paths: Iterable[str]) -> dict[str, dict[str, dict[str, list[str]]]]:
    """``{key: {test class: {method: [outcome, …]}}}`` from the test files of ``inventory`` whose bytes still match."""
    project = Path(project)
    sources = {Path(path).stem for path in context_paths if Path(path).suffix in {".java", ".py"} and not Path(path).stem.startswith("test_")
               and not Path(path).stem.endswith(("Test", "Tests", "IT"))}
    owned = _excluded(project)
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for row in sorted(inventory.get("files") or [], key=lambda item: str(item.get("project_path"))):
        relative = str(row.get("project_path"))
        if row.get("kind") != "test" or Path(relative).suffix not in {".java", ".py"} or relative in owned:
            continue
        try:
            raw = (project / relative).read_bytes()
        except OSError:
            continue
        if "sha256:" + hashlib.sha256(raw).hexdigest() != row.get("content_digest"):
            continue  # a file changed after the scan is not the scanned project
        text = raw.decode("utf-8", errors="replace")
        test_class = Path(relative).stem
        java = relative.endswith(".java")
        methods = list(_java_methods(text)) if java else list(_python_tests(text))
        named = set(re.findall(r"\b[A-Z]\w*\b", text)) if java else _python_names(text)
        for name, body in methods:
            outcomes = _outcomes(body)
            keys = {f"{verb.upper()} {_normal_path(path)}" for verb, path in _HTTP_CALL.findall(body)}
            keys |= (set(re.findall(r"\b[A-Z]\w*\b", body)) if java else named) & sources
            for key in keys:
                result.setdefault(key, {}).setdefault(test_class, {})[name] = outcomes
    return result


def render_map(value: Mapping[str, Mapping[str, Mapping[str, list[str]]]]) -> bytes:
    """The map as lines «ключ: Класс (m1 [исходы], m2) ; …», cut at ``LIMIT`` bytes; empty map — empty bytes."""
    lines: list[str] = []
    size = 0
    keys = sorted(value)
    for index, key in enumerate(keys):
        parts = []
        for test_class, methods in sorted(value[key].items()):
            names = sorted(methods)
            if len(names) > NAMES:
                outcomes = sorted({item for name in names for item in methods[name]})[:OUTCOMES]
                parts.append(f"{test_class} ({len(names)} тестов" + (f"; исходы: {', '.join(outcomes)})" if outcomes else ")"))
            else:
                parts.append(f"{test_class} (" + ", ".join(name + (f" [{', '.join(methods[name])}]" if methods[name] else "")
                                                         for name in names) + ")")
        line = f"- {key}: " + "; ".join(parts)
        if size + len(line.encode("utf-8")) + 1 > LIMIT:
            lines.append(f"- ещё {len(keys) - index} ключей")
            break
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def view_bytes(value: Mapping[str, Any]) -> bytes:
    """The map file the generator reads: a fixed head and the lines; empty map — empty bytes (no file)."""
    body = render_map(value)
    if not body:
        return b""
    head = ("# Существующие тесты проекта\n\n"
            "Карта кода: какие эндпоинты и классы уже вызывают тесты самого проекта, без кода и тел тестов. Это справка: кейс по "
            "требованию пишется всё равно и требования к нему не снижаются.\n\n")
    return head.encode("utf-8") + body


def map_path(run_root: Path, context_digest: str) -> Path:
    return Path(run_root) / "review-views" / (str(context_digest).removeprefix("sha256:") + SUFFIX)


def _segments(path: str) -> list[str]:
    return [part for part in _normal_path(path).split("/") if part]


def _same_endpoint(case_key: str, test_key: str) -> bool:
    """Same method, and the shorter path is the tail of the longer one by whole segments."""
    case_method, _, case_path = case_key.partition(" ")
    test_method, _, test_path = test_key.partition(" ")
    if case_method != test_method:
        return False
    left, right = _segments(case_path), _segments(test_path)
    short, long_ = (left, right) if len(left) <= len(right) else (right, left)
    return bool(short) and long_[len(long_) - len(short):] == short


def _case_steps(case: Mapping[str, Any]) -> Iterable[tuple[str, set[str]]]:
    """(``МЕТОД /путь``, outcomes) of each HTTP step: the operation or the literal method/path inputs; asserted status
    numbers, view names and exception classes."""
    for step in case.get("steps") or []:
        literal = {str((item.get("target") or {}).get("name")): (item.get("source") or {}).get("value")
                   for item in step.get("inputs") or [] if (item.get("source") or {}).get("kind") == "literal"}
        operation = step.get("operation") or {}
        method = operation.get("method") or literal.get("method") or literal.get("http_method")
        path = operation.get("path") or literal.get("path") or literal.get("path_template") or literal.get("url")
        if not (isinstance(method, str) and isinstance(path, str) and path.startswith("/")):
            continue
        outcomes: set[str] = set()
        for expectation in step.get("expectations") or []:
            for assertion in expectation.get("assertions") or []:
                expected = assertion.get("expected") or {}
                actual = json.dumps(assertion.get("actual") or {}, ensure_ascii=False).casefold()
                value = expected.get("value") if expected.get("kind") == "literal" else None
                if isinstance(value, int) and "status" in actual:
                    outcomes.add(str(value))
                elif isinstance(value, list) and "field" in actual:
                    outcomes.update(f"field {item}" for item in value if isinstance(item, str))
                elif isinstance(value, str) and "view" in actual:
                    outcomes.add(f"view {value}")
                elif isinstance(value, str) and re.fullmatch(r"[A-Z]\w*(?:Exception|Error)", value):
                    outcomes.add(value)
        yield f"{method.upper()} {_normal_path(path)}", outcomes


def repeats(document: Mapping[str, Any], value: Mapping[str, Mapping[str, Mapping[str, list[str]]]]) -> list[str]:
    """IDs of the cases that add no new (endpoint, outcome): every HTTP step with a checked outcome has its endpoint and
    that outcome in a project test (a setup step alone that a test also calls does not make a repeat)."""
    found = []
    endpoints = {key: rows for key, rows in value.items() if " " in key}
    for case in document.get("test_cases") or []:
        checked = [(key, outcomes) for key, outcomes in _case_steps(case) if outcomes]
        if checked and all(outcomes <= {item for test_key, rows in endpoints.items() if _same_endpoint(key, test_key)
                                        for methods in rows.values() for items in methods.values() for item in items}
                           for key, outcomes in checked):
            found.append(str(case["case_id"]))
    return found


def hint_line(case_ids: list[str]) -> str | None:
    if not case_ids:
        return None
    shown = ", ".join(case_ids[:10]) + (f" и ещё {len(case_ids) - 10}" if len(case_ids) > 10 else "")
    return (f"- {HINT}: кейсов {len(case_ids)} не добавляют нового сочетания «эндпоинт + исход» к тестам проекта — {shown}. "
            "Входных данных карта не видит, это верхняя оценка. Справка: кейс по требованию остаётся, требования к нему не "
            "снижаются; дубль без нового условия — замечание.")


def for_context(project: Path, context_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """The map for one context receipt: its frozen inventory (by digest, from the run that froze it) and its files."""
    from tools.project_inventory import read_inventory_receipt

    project = Path(project).resolve()
    name = str(context_receipt.get("inventory_digest", "")).removeprefix("sha256:") + ".json"
    frozen = sorted((project / ".pilot-runs").glob("*/inventories/" + name))
    if not frozen:
        return {}
    inventory = read_inventory_receipt(frozen[0])
    return build_map(project, inventory, [str(row["project_path"]) for row in context_receipt.get("files") or []])


def ensure_view(run_root: Path, context_digest: str) -> Path | None:
    """Write (or rewrite, when the bytes differ) the map file of one batch context; None when the project has no hits."""
    from tools.confined_output import atomic_write_confined_bytes_at_root
    from tools.pilot_state import derive_state, read_context_selection

    root = Path(run_root).resolve()
    attempt_id = str(derive_state(root)["attempts"][-1]["attempt_id"])
    data = view_bytes(for_context(root.parent.parent, read_context_selection(root, attempt_id, str(context_digest))))
    target = map_path(root, context_digest)
    if not data:
        return None
    try:
        current = target.read_bytes()
    except OSError:
        current = None
    if current != data:
        atomic_write_confined_bytes_at_root(root.parent.parent, root, target, data)
    return target
