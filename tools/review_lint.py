"""Deterministic review linter: suspicions only, never findings.

A suspicion goes into the compact review envelope of every part that holds its
cases.  The reviewer answers each one in ``lint_dispositions`` (``confirmed`` or
``rejected``); only the reviewer's own finding can turn it into a defect.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

LINT_VERSION = "review-lint-v1"

Rule = Callable[[Mapping[str, Any]], list[dict[str, Any]]]
RULES: list[tuple[str, Rule]] = []


def _number(item: dict[str, Any]) -> dict[str, Any]:
    from tools.review_parts import review_digest

    return {"lint_id": "LINT-" + review_digest(item)[7:17].upper(), **item}


# --------------------------------------------------------------------------------------
# automation: checks of generated code against the accepted cases (before the model)
# --------------------------------------------------------------------------------------

def _java_literal(value: Any) -> list[str]:
    """Spellings of a scalar literal in Java or Python source."""
    if isinstance(value, bool):
        return ["true", "True"] if value else ["false", "False"]
    if value is None:
        return ["null", "None"]
    if isinstance(value, (int, float)):
        return [str(value)]
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return [f'"{escaped}"', f"'{text}'"]


def _contains_literal(code: str, value: Any) -> bool:
    if isinstance(value, str) and re.fullmatch(r"(?:[a-z_][a-z0-9_]*\.)+([A-Z][A-Za-z0-9_$]*)", value):
        # A class name may be written as Name.class(.getName()) instead of a string.
        simple = value.rsplit(".", 1)[1]
        if re.search(rf"\b{re.escape(simple)}\.class\b", code):
            return True
    for spelling in _java_literal(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if re.search(rf"(?<![\w.]){re.escape(spelling)}(?![\w.])", code):
                return True
        elif spelling in code:
            return True
    return False


def _statement(lines: list[str], index: int) -> str:
    """The whole statement around ``lines[index]``: back to the end of the previous one, on to its own ``;``.

    An assertion message may come first (AssertJ ``.as(id)``) or last (JUnit ``assertEquals(..., id)``).
    """
    start = index
    while start > 0 and index - start < 12 and not lines[start - 1].rstrip().endswith((";", "{", "}")):
        start -= 1
    end = index
    while end < len(lines) - 1 and end - index < 12 and not lines[end].rstrip().endswith(";"):
        end += 1
    return "\n".join(lines[start:end + 1])


def lint_automation(document: Mapping[str, Any], automation: Mapping[str, Any], slices: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Code checks before the model: ASSERT IDs, simple literals, request method/path/body.

    ``slices`` maps ``file_id`` to ``tools.code_slices.FileSlices``.  Every result is a
    suspicion for the reviewer, never an automatic rejection.
    """
    generated = automation["artifacts"]
    symbols = {(row["case_id"]): row for row in generated["implementation_relations"]}
    rows: list[dict[str, Any]] = []
    for case in document["test_cases"]:
        relation = symbols.get(case["case_id"])
        if relation is None or relation["file_id"] not in slices:
            continue
        file = slices[relation["file_id"]]
        member = file.symbols.get(relation["symbol_id"])
        if member is None:
            continue
        method_lines = file.lines[member.start - 1:member.end]
        constants = {item.name: "\n".join(file.lines[item.start - 1:item.end]) for item in file.members if item.kind == "field" and "final" in item.modifiers}
        method = "\n".join(method_lines)
        reach = "\n".join([method, *("\n".join(file.lines[helper.start - 1:helper.end]) for helper in file.helpers[relation["symbol_id"]])])
        for step in case["steps"]:
            for expectation in step["expectations"]:
                for assertion in expectation["assertions"]:
                    identifier = assertion["assertion_id"]
                    if f'"{identifier}"' not in reach and f"'{identifier}'" not in reach:
                        rows.append({"rule": "assert-id-missing", "case_ids": [case["case_id"]], "related_ids": [identifier, relation["symbol_id"]],
                                     "message": f"{identifier} не найден в коде метода {member.name} и его хелперов."})
                        continue
                    expected = assertion.get("expected") or {}
                    if assertion["operator"] == "equals" and expected.get("kind") == "literal" and not isinstance(expected.get("value"), (dict, list)):
                        index = next((number for number, line in enumerate(method_lines) if identifier in line), None)
                        statement = None if index is None else _statement(method_lines, index)
                        if statement is not None:
                            # A named constant of the file stands for its literal.
                            statement += "\n" + "\n".join(constants[name] for name in sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", statement))) if name in constants)
                        if statement is not None and not _contains_literal(statement, expected["value"]):
                            rows.append({"rule": "assert-literal-differs", "case_ids": [case["case_id"]], "related_ids": [identifier, relation["symbol_id"]],
                                         "message": f"Проверка {identifier} (строка L{member.start + index}) не содержит ожидаемый литерал {_java_literal(expected['value'])[0]}."})
            operation = step.get("operation") or {}
            literals = {item["target"].get("name") or item["target"].get("pointer"): item["source"].get("value")
                        for item in step.get("inputs", []) if item["source"].get("kind") == "literal"}
            requested = []
            if operation.get("kind") == "http":
                requested = [("метод", operation.get("method")), ("путь", operation.get("path"))]
            requested += [(name, value) for name, value in literals.items() if name in {"method", "path"} and isinstance(value, str)]
            for label, value in requested:
                if value and not _contains_literal(method, value):
                    rows.append({"rule": "request-differs", "case_ids": [case["case_id"]], "related_ids": [step["step_id"], relation["symbol_id"]],
                                 "message": f"{step['step_id']}: {label} запроса {value!r} не найден в методе {member.name}."})
            body = next((value for name, value in literals.items() if name in {"jsonBody", "body", ""} and isinstance(value, dict)), None)
            if isinstance(body, dict):
                for key, value in body.items():
                    if not isinstance(value, (dict, list)) and (f'"{key}"' in method or f"'{key}'" in method) is False and not _contains_literal(reach, value):
                        rows.append({"rule": "request-differs", "case_ids": [case["case_id"]], "related_ids": [step["step_id"], relation["symbol_id"]],
                                     "message": f"{step['step_id']}: поле тела {key}={value!r} не найдено в методе {member.name}."})
                for key in re.findall(r'\.put\("([^"]+)"', method):
                    if key not in body:
                        rows.append({"rule": "request-differs", "case_ids": [case["case_id"]], "related_ids": [step["step_id"], relation["symbol_id"]],
                                     "message": f"{step['step_id']}: метод {member.name} кладёт в тело поле {key!r}, которого нет во входе шага."})
    unique: list[dict[str, Any]] = []
    for row in rows:
        item = _number(row)
        if item["lint_id"] not in {existing["lint_id"] for existing in unique}:
            unique.append(item)
    return unique


def lint_document(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every suspicion of every rule in a stable order.

    The ID is derived from the suspicion itself, so a change elsewhere in the
    document does not renumber it (carried review areas keep their inputs).
    """
    from tools.review_parts import review_digest

    rows: list[dict[str, Any]] = []
    for rule, check in RULES:
        for row in check(document):
            item = {"rule": rule, "case_ids": list(row["case_ids"]), "related_ids": list(row["related_ids"]), "message": str(row["message"])}
            item = {"lint_id": "LINT-" + review_digest(item)[7:17].upper(), **item}
            if item["lint_id"] not in {existing["lint_id"] for existing in rows}:
                rows.append(item)
    return rows
