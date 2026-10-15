"""Deterministic review linter: suspicions only, never findings.

A suspicion goes into the compact review envelope of every part that holds its
cases.  The reviewer answers each one in ``lint_dispositions`` (``confirmed`` or
``rejected``); only the reviewer's own finding can turn it into a defect.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

LINT_VERSION = "review-lint-v1"

Rule = Callable[[Mapping[str, Any]], list[dict[str, Any]]]


# --------------------------------------------------------------------------------------
# canonical documents
# --------------------------------------------------------------------------------------

def _json_fragments(text: str) -> list[Any]:
    """JSON objects and arrays written inside a human text (an expected response body)."""
    decoder = json.JSONDecoder()
    found, index = [], 0
    while index < len(text):
        if text[index] in "{[":
            try:
                value, end = decoder.raw_decode(text, index)
            except ValueError:
                index += 1
                continue
            if isinstance(value, (dict, list)) and value:
                found.append(value)
            index = end
        else:
            index += 1
    return found


def _literal_assertions(expectation: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Any]]:
    return [(assertion, assertion["expected"]["value"]) for assertion in expectation["assertions"]
            if assertion["operator"] == "equals" and isinstance(assertion.get("expected"), Mapping) and assertion["expected"].get("kind") == "literal"]


def _is_status(actual: Mapping[str, Any]) -> bool:
    return actual.get("kind") == "http_status" or (actual.get("kind") == "project_result" and str(actual.get("name", "")).lower() in {"status", "statuscode", "status_code"})


def _same_shape(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_shape(a, b) for a, b in zip(left, right))
    return False


def rule_expectation_text(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The HTTP status or JSON body written in an expectation differs from the literal its assertion checks."""
    rows = []
    for case in document["test_cases"]:
        for step in case["steps"]:
            for expectation in step["expectations"]:
                literals = _literal_assertions(expectation)
                text = expectation["text"]
                statuses = {int(value) for value in re.findall(r"\bHTTP\s+([1-5][0-9]{2})\b", text)}
                for assertion, value in literals:
                    if _is_status(assertion["actual"]) and isinstance(value, int) and statuses and value not in statuses:
                        rows.append({"case_ids": [case["case_id"]], "related_ids": [expectation["expectation_id"], assertion["assertion_id"]],
                                     "message": f"Текст ожидания называет статус {', '.join(map(str, sorted(statuses)))}, а проверка ждёт {value}."})
                for fragment in _json_fragments(text):
                    for assertion, value in literals:
                        if _same_shape(fragment, value) and fragment != value:
                            rows.append({"case_ids": [case["case_id"]], "related_ids": [expectation["expectation_id"], assertion["assertion_id"]],
                                         "message": "JSON в тексте ожидания отличается от литерала проверки: "
                                                    f"{json.dumps(fragment, ensure_ascii=False, sort_keys=True)[:160]} ≠ {json.dumps(value, ensure_ascii=False, sort_keys=True)[:160]}."})
    return rows


def rule_requirement_without_case(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A canonical requirement that no case links."""
    linked = {requirement for case in document["test_cases"] for requirement in case["requirement_ids"]}
    return [{"case_ids": [], "related_ids": [requirement["requirement_id"]],
             "message": f"Требование {requirement['requirement_id']} не связано ни с одним кейсом."}
            for requirement in document["requirements"] if requirement["requirement_id"] not in linked]


def _call(step: Mapping[str, Any]) -> tuple[str, str | None, str | None, dict[str, Any]] | None:
    """(operation, method, path, literal inputs) of a step, or None for a manual step."""
    operation = step.get("operation")
    if not operation:
        return None
    literals = {str(item["target"].get("name") or item["target"].get("pointer")): item["source"]["value"]
                for item in step.get("inputs", []) if item["source"].get("kind") == "literal"}
    if operation.get("kind") == "http":
        return f"http:{operation.get('method')} {operation.get('path')}", operation.get("method"), operation.get("path"), literals
    method = literals.get("method") if isinstance(literals.get("method"), str) else None
    path = literals.get("path") if isinstance(literals.get("path"), str) else None
    return str(operation.get("capability_id")), method, path, literals


def _template(path: str | None) -> str | None:
    return None if path is None else re.sub(r"/[0-9]+(?=/|$)", "/{n}", path.split("?", 1)[0])


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{prefix}/{key}"))
        return out
    return {prefix: value}


def _input_values(literals: Mapping[str, Any], path: str | None) -> set[str]:
    values = {json.dumps(leaf, sort_keys=True) for value in literals.values() for leaf in _leaves(value).values()}
    for segment in re.findall(r"/([0-9]+)(?=/|$)", path or ""):
        values.update({segment, json.dumps(int(segment)), json.dumps(segment)})
    return values


def rule_same_call_different_expectations(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Two cases make the same call (same operation, method and path template) but expect different values
    for a response field that does not echo one of their own inputs."""
    calls: dict[tuple, list[tuple[str, str, dict[str, Any], set[str]]]] = {}
    for case in document["test_cases"]:
        for step in case["steps"]:
            call = _call(step)
            if call is None or any(item["source"].get("kind") != "literal" for item in step.get("inputs", [])):
                continue  # a call fed by earlier steps depends on its own case's data
            operation, method, path, literals = call
            # Same call: same operation, method, path template and every other input equal.
            others = json.dumps({name: value for name, value in literals.items() if name != "path"}, sort_keys=True, ensure_ascii=False)
            key = (operation, method, _template(path), others)
            for expectation in step["expectations"]:
                for assertion, value in _literal_assertions(expectation):
                    actual = json.dumps(assertion["actual"], sort_keys=True)
                    for leaf, item in _leaves(value).items():
                        calls.setdefault((*key, actual, leaf), []).append((case["case_id"], assertion["assertion_id"], {"value": item},
                                                                             _input_values(literals, path)))
    rows, seen = [], set()
    for (operation, method, template, _others, _actual, leaf), entries in calls.items():
        for index, (case_a, assert_a, value_a, inputs_a) in enumerate(entries):
            for case_b, assert_b, value_b, inputs_b in entries[index + 1:]:
                if case_a == case_b or value_a == value_b:
                    continue
                encoded_a, encoded_b = json.dumps(value_a["value"], sort_keys=True), json.dumps(value_b["value"], sort_keys=True)
                if encoded_a in inputs_a or encoded_b in inputs_b:
                    continue  # the field echoes an input of its own case
                pair = tuple(sorted((case_a, case_b)))
                if (pair, leaf) in seen:
                    continue
                seen.add((pair, leaf))
                where = " ".join(item for item in (method, template) if item) or operation
                rows.append({"case_ids": list(pair), "related_ids": [assert_a, assert_b],
                             "message": f"Одинаковый вызов {where}: поле {leaf or '/'} ожидается {encoded_a} в {case_a} и {encoded_b} в {case_b}."})
    return rows


_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def rule_count_of_shared_resource(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A case checks an absolute count or the full list of a resource, and another case shows that resource
    changing after a mutating call (a different list or count for the same read)."""
    reads: dict[str, list[tuple[str, str, Any, bool]]] = {}
    for case in document["test_cases"]:
        mutated: set[str] = set()
        for step in case["steps"]:
            call = _call(step)
            if call is None:
                continue
            _operation, method, path, _literals = call
            resource = _template(path)
            if method in _MUTATING and resource:
                mutated.update(prefix for prefix in [resource.rsplit("/", index)[0] for index in range(resource.count("/"))] if prefix)
                mutated.add(resource)
            if method != "GET" or not resource:
                continue
            for expectation in step["expectations"]:
                for assertion in expectation["assertions"]:
                    expected = assertion.get("expected") or {}
                    value = expected.get("value") if expected.get("kind") == "literal" else None
                    if assertion["operator"] == "length_equals" and isinstance(value, int):
                        observed: Any = value
                    elif assertion["operator"] == "equals" and isinstance(value, list):
                        observed = len(value)
                    else:
                        continue
                    reads.setdefault(resource, []).append((case["case_id"], assertion["assertion_id"], observed, resource in mutated))
    rows, seen = [], set()
    for resource, entries in reads.items():
        changed = [entry for entry in entries if entry[3]]
        for case_id, assertion_id, observed, after_mutation in entries:
            if after_mutation:
                continue
            for other_case, other_assertion, other_observed, _ in changed:
                if other_case != case_id and other_observed != observed and (case_id, other_case, resource) not in seen:
                    seen.add((case_id, other_case, resource))
                    rows.append({"case_ids": [case_id, other_case], "related_ids": [assertion_id, other_assertion],
                                 "message": f"{case_id} проверяет абсолютное количество или полный список {resource} ({observed}), "
                                            f"а {other_case} после изменения ресурса ждёт {other_observed}: проверка зависит от состояния, которое меняют другие кейсы."})
    return rows


def rule_result_not_in_capability(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A step observes a project result its capability does not return."""
    results = {capability["capability_id"]: {row["name"] for row in capability.get("results", [])} for capability in document["operation_capabilities"]}
    rows = []
    for case in document["test_cases"]:
        for step in case["steps"]:
            operation = step.get("operation") or {}
            if operation.get("kind") != "project_action" or operation.get("capability_id") not in results:
                continue
            known = results[operation["capability_id"]]
            observed = [(output["output_id"], output["source"]) for output in step.get("outputs", [])]
            observed += [(assertion["assertion_id"], assertion["actual"]) for expectation in step["expectations"] for assertion in expectation["assertions"]]
            for identifier, actual in observed:
                if actual.get("kind") == "project_result" and actual.get("name") not in known:
                    rows.append({"case_ids": [case["case_id"]], "related_ids": [step["step_id"], identifier, operation["capability_id"]],
                                 "message": f"{identifier}: результата {actual.get('name')!r} нет у {operation['capability_id']} ({', '.join(sorted(known)) or 'нет результатов'})."})
    return rows


RULES: list[tuple[str, Rule]] = [
    ("expectation-text-differs", rule_expectation_text),
    ("requirement-without-case", rule_requirement_without_case),
    ("same-call-different-expectations", rule_same_call_different_expectations),
    ("count-of-shared-resource", rule_count_of_shared_resource),
    ("result-not-in-capability", rule_result_not_in_capability),
]


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


_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


def _contains_http_method(code: str, method: str) -> bool:
    """The request method as a literal, an enum constant, or a builder call that takes a URL/path literal.

    ``exchange("PUT", …)``, ``HttpMethod.PUT``, ``MockMvcRequestBuilders.put("/x")``,
    ``client.put("/x")``.  A builder call needs a literal starting with ``/`` or ``http``,
    so ``map.get("key")`` is not a GET.
    """
    if _contains_literal(code, method):
        return True
    upper = method.upper()
    if upper not in _HTTP_METHODS:
        return False
    if re.search(rf"\b(?:HttpMethod|RequestMethod)\.{upper}\b", code):
        return True
    return re.search(rf"\b{upper.lower()}\s*\(\s*f?[\"'](?:/|https?:)", code) is not None


# ``body.put("key", value)`` puts a body field; a request builder ``put("/path")`` does not.
_BODY_PUT = re.compile(r'(?<!RequestBuilders)\.put\(\s*"([^"/][^"]*)"')


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


def names_id(code: str, identifier: str) -> bool:
    """``identifier`` as a whole word inside a string literal of ``code``: ``"ASSERT-B1-0042"``, but also
    ``.as(x.label("ASSERT-B1-0042 response_status"))`` (live Petclinic run d: an exact-literal search raised 1285 false
    suspicions over ten parts); ``ASSERT-B1-001`` never matches ``ASSERT-B1-0010``."""
    word = rf"(?<![\w-]){re.escape(identifier)}(?![\w-])"
    return re.search(rf"\"[^\"\n]*{word}[^\"\n]*\"|'[^'\n]*{word}[^'\n]*'", code) is not None


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
                    if not names_id(reach, identifier):
                        rows.append({"rule": "assert-id-missing", "case_ids": [case["case_id"]], "related_ids": [identifier, relation["symbol_id"]],
                                     "message": f"{identifier} не найден в коде метода {member.name} и его хелперов."})
                        continue
                    expected = assertion.get("expected") or {}
                    if assertion["operator"] == "equals" and expected.get("kind") == "literal" and not isinstance(expected.get("value"), (dict, list)):
                        index = next((number for number, line in enumerate(method_lines) if names_id(line, identifier)), None)
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
                found = _contains_http_method(method, value) if label in {"метод", "method"} else _contains_literal(method, value)
                if value and not found:
                    rows.append({"rule": "request-differs", "case_ids": [case["case_id"]], "related_ids": [step["step_id"], relation["symbol_id"]],
                                 "message": f"{step['step_id']}: {label} запроса {value!r} не найден в методе {member.name}."})
            body = next((value for name, value in literals.items() if name in {"jsonBody", "body", ""} and isinstance(value, dict)), None)
            if isinstance(body, dict):
                for key, value in body.items():
                    if not isinstance(value, (dict, list)) and (f'"{key}"' in method or f"'{key}'" in method) is False and not _contains_literal(reach, value):
                        rows.append({"rule": "request-differs", "case_ids": [case["case_id"]], "related_ids": [step["step_id"], relation["symbol_id"]],
                                     "message": f"{step['step_id']}: поле тела {key}={value!r} не найдено в методе {member.name}."})
                for key in _BODY_PUT.findall(method):
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
