"""Deterministic terminal human previews and fixed-profile Zephyr CSV projections."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode

from tools.canonical_document import param_groups, require_valid_canonical_document
from tools.test_design_hints import technique_name


_PROFILE_V1 = "zephyr-scale-step-row-24-v1"
_PROFILE_V2 = "zephyr-scale-step-row-24-v2"
_PROFILE_V3 = "zephyr-scale-step-row-24-v3"
_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE_V5 = "zephyr-scale-step-row-24-v5"
_PROFILE_V6 = "zephyr-scale-step-row-24-v6"
_PROFILE = _PROFILE_V6
_PROFILES = frozenset((_PROFILE_V1, _PROFILE_V2, _PROFILE_V3, _PROFILE_V4, _PROFILE_V5, _PROFILE_V6))
# Profiles a new bundle may be published with: V6 is the default (V5 and the request block of an HTTP step), V5 the human-only
# one of the sets before it, V4 keeps the machine-model step columns as an explicit compatibility opt-in.  Their Markdown is identical.
_PUBLISHABLE_PROFILES = frozenset((_PROFILE_V4, _PROFILE_V5, _PROFILE_V6))
_CUSTOM_KEYS = ("АС", "Автоматизирован", "Вид тестирования", "Команда", "Приоритет теста", "Статус")
# The company's Zephyr accepts only High/Normal/Low: CRITICAL goes out as High.
_PRIORITIES = {"CRITICAL": "High", "HIGH": "High", "MEDIUM": "Normal", "LOW": "Low"}
_HEADERS = (
    "Key", "Name", "Status", "Precondition", "Objective", "Folder", "Priority",
    "Component", "Labels", "Owner", "Estimated Time", "Coverage (Issues)",
    "Coverage (Pages)", "АС", "Автоматизирован", "Вид тестирования", "Команда",
    "Приоритет теста", "Статус", "Test Script (Step-by-Step) - Step",
    "Test Script (Step-by-Step) - Test Data",
    "Test Script (Step-by-Step) - Expected Result", "Test Script (Plain Text)",
    "Test Script (BDD)",
)


@dataclass(frozen=True)
class Projection:
    payload: bytes
    warnings: tuple[str, ...]


_CODE_SPAN = re.compile(r"`[^`\n]+`")


def _escape_plain(value: str) -> str:
    value = value.replace("\\", "\\\\")
    for marker in "`*_[]<>#|":
        value = value.replace(marker, "\\" + marker)
    return value


def escape_inline(value: str) -> str:
    """Escape one user-originated value for its Markdown inline position.

    A paired backtick span (`Range.of`) is code in the human text and stays a code span — only its ``|`` is
    escaped for a table cell; an unpaired backtick and the text around the spans are escaped as before."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    last = 0
    for span in _CODE_SPAN.finditer(value):
        parts += [_escape_plain(value[last:span.start()]), span.group(0).replace("|", "\\|")]
        last = span.end()
    parts.append(_escape_plain(value[last:]))
    return "".join(parts).replace("\n", "<br>")


def _readable_table_cell(value: str) -> str:
    return escape_inline(value).replace("\\_", "_")


def _markdown_human_blocks(value: str, *, literal: bool = False) -> list[str]:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    trailing_json: str | None = None
    source_lines = value.splitlines()
    for index, line in enumerate(source_lines):
        if not line.lstrip().startswith(("{", "[")):
            continue
        candidate = "\n".join(source_lines[index:]).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            value = "\n".join(source_lines[:index]).strip()
            trailing_json = candidate
            break

    rendered: list[str] = []
    for paragraph in value.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        rendered.append("\n\n".join(escape_inline(line) if literal else line for line in paragraph.splitlines()))
    if trailing_json is not None:
        fence = "`" * max(3, max(map(len, re.findall(r"`+", trailing_json)), default=0) + 1)  # longer than any run of backticks in the body, or the body closes it
        rendered.append(f"{fence}json\n{trailing_json}\n{fence}")
    return "\n\n".join(rendered).splitlines()


def _markdown_subject(subject: dict[str, Any]) -> str:
    if subject["kind"] == "http_endpoint":
        return f"# Тест-кейсы метода {escape_inline(subject['method'])} {escape_inline(subject['path'])}"
    return f"# Тест-кейсы: {escape_inline(subject['name'])}"


def _reject_unknown_profile(profile: str) -> None:
    if profile not in _PROFILES:
        raise ValueError(f"ZEPHYR_CSV_PROFILE: unknown Zephyr CSV profile: {profile}; known: {', '.join(sorted(_PROFILES))}")


_BLANK_TO_THE_END = re.compile(r"\s*\Z")
_CLOSER = {"{": "}", "[": "]"}
_MAY_OPEN_JSON = re.compile(r"[\[{]\s*[\]}\[{\"\-0-9tfnNI]")  # a necessary condition for a JSON container (the parser decides); it spares the parser the lines that cannot be JSON


def _trailing_json(value: str) -> tuple[str, str | None]:
    """Split a human field into its prose and a trailing JSON object or array."""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
    joined = "\n".join(lines)
    last = joined[-1:]  # the value must end the text: a line whose bracket has another closer is not tried (a failing parse costs a pass over the text)
    decoder = json.JSONDecoder()
    offset = 0  # where the line begins in `joined`: the rest of the text is read in place, not joined again for every line
    for index, line in enumerate(lines):
        start = offset + len(line) - len(line.lstrip())
        offset += len(line) + 1
        if not line.lstrip().startswith(("{", "[")) or _CLOSER[joined[start]] != last or _MAY_OPEN_JSON.match(joined, start) is None:
            continue
        try:
            parsed, end = decoder.raw_decode(joined, start)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(parsed, (dict, list)) and _BLANK_TO_THE_END.match(joined, end):
            return "\n".join(lines[:index]).strip(), joined[start:].strip()
    return joined.strip(), None


# a parameter group is shown once — the texts of its first case with a slot where the variants differ, and a
# table with a row per case; the slot becomes ``<имя параметра>``. The canonical keeps one case per variant.
SLOT = "\ue000"


_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|\w+|\W", re.S)


_PAIRS = ("()", "[]", "{}", "«»")


def _whole(tokens: list[str]) -> bool:
    """No bracket or quote of the tokens is left without its pair."""
    return all(tokens.count(opening) == tokens.count(closing) for opening, closing in _PAIRS) and all(tokens.count(quote) % 2 == 0 for quote in '"`')


def template_texts(texts: list[str]) -> tuple[str, list[str]]:
    """The common text of the variants with one slot, cut between whole words and numbers and never through a pair of brackets or quotes, and each variant's middle."""
    if len(set(texts)) == 1:
        return texts[0], []
    tokens = [_TOKEN.findall(text) for text in texts]
    head = len(os.path.commonprefix(tokens))
    tail = len(os.path.commonprefix([own[head:][::-1] for own in tokens]))
    while (head or tail) and not _whole(tokens[0][:head] + tokens[0][len(tokens[0]) - tail:]):  # the template as a whole keeps its pairs; the slot sits between them
        head, tail = (head, tail - 1) if tail else (head - 1, tail)
    rests = [own[head:] for own in tokens]
    middles = ["".join(rest[:len(rest) - tail]) for rest in rests]
    return "".join(tokens[0][:head]) + SLOT + "".join(tokens[0][len(tokens[0]) - tail:]), middles


def _group_view(group: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The first case of a group with slotted texts, and a row per case: its values, its expected result, its title, its ID."""
    cases = group["cases"]
    view = json.loads(json.dumps(cases[0]))
    values: list[list[str]] = [[] for _ in cases]
    expected: list[list[str]] = [[] for _ in cases]

    def slot(texts: list[str], into: list[list[str]]) -> str:
        text, middles = template_texts(texts)
        for own, middle in zip(into, middles):
            shown = middle.strip() or json.dumps(middle, ensure_ascii=False)  # an empty or blank value is told apart by its quotes, as in the reviewer's view
            if shown not in own:
                own.append(shown)
        return text

    view["title"], _ = template_texts([case["title"] for case in cases])
    view["objective"], _ = template_texts([case["objective"] for case in cases])
    for step_index, step in enumerate(view["steps"]):
        steps = [case["steps"][step_index] for case in cases]
        step["action"] = slot([item["action"] for item in steps], values)
        step["test_data"] = slot([item["test_data"] for item in steps], values)
        for expectation_index, expectation in enumerate(step["expectations"]):
            expectation["text"] = slot([item["expectations"][expectation_index]["text"] for item in steps], expected)
    parameter = group["parameter"]["id"]
    rows = []
    for index, case in enumerate(cases):
        if not values[index]:  # no text differs: the variant as the design names it
            ref = next(ref for ref in case["design_refs"] if ref["parameter"] == parameter)
            values[index] = [str(ref.get("value") or ref.get("bound") or ref.get("class"))]
        own = [item for item in expected[index] if item not in values[index]]  # the value itself is no extra expectation
        rows.append({"values": "; ".join(values[index]), "expected": "; ".join(own) or "—", "title": case["title"],
                     "case_id": case["case_id"]})
    return view, rows


def _grouped_cases(document: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, str]]]]:
    """The cases as shown: a group at the place of its first case, its other cases folded into the group's table."""
    groups = {group["cases"][0]["case_id"]: group for group in param_groups(document)} if SLOT not in json.dumps(document, ensure_ascii=False) else {}
    folded = {case["case_id"] for group in groups.values() for case in group["cases"][1:]}
    shown = []
    for case in document["test_cases"]:
        if case["case_id"] in groups:
            view, rows = _group_view(groups[case["case_id"]])
            shown.append((view, groups[case["case_id"]], rows))
        elif case["case_id"] not in folded:
            shown.append((case, None, []))
    return shown


def section_labels(document: dict[str, Any]) -> dict[str, int]:
    """The number «ТК-n» of every case of a document as it is shown: the ordinal of its section (a group is one section)."""
    labels: dict[str, int] = {}
    for number, (case, group, _rows) in enumerate(_grouped_cases(document), 1):
        labels.update({item["case_id"]: number for item in (group["cases"] if group else [case])})
    return labels


def _number(extras: dict[str, Any] | None, number: int, case: dict[str, Any], group: dict[str, Any] | None) -> int:
    """The label of a section: its place in the shown set, or — in a set the code took cases out of — its place in the set the reviewer read."""
    labels = (extras or {}).get("labels")
    return number if labels is None else labels[(group["cases"][0] if group else case)["case_id"]]


def _notes(extras: dict[str, Any] | None, case: dict[str, Any]) -> list[tuple[str, str]]:
    return ((extras or {}).get("notes") or {}).get(case.get("case_id"), [])


def _facts(shown: list[tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, str]]]], extras: dict[str, Any] | None) -> list[tuple[str, ...]]:
    """What the tester reads of every shown section in the table and under its title: ``(ТК-n, title, folder, priority, requirements)``."""
    rows = []
    for number, (case, group, _rows) in enumerate(shown, 1):
        title = case["title"].replace(SLOT, "<" + group["parameter"]["name"].replace("\n", " ") + ">") if group else case["title"]
        requirements = dict.fromkeys(item for own in (group["cases"] if group else [case]) for item in own["requirement_ids"])
        rows.append((f"ТК-{_number(extras, number, case, group)}", title, case["management"]["folder"] or "—", _PRIORITIES[case["priority"]], ", ".join(requirements)))
    return rows


def _overview(facts: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The table of the set, its head first; the folder column only when some case has a folder."""
    keep = [index for index in range(5) if index != 2 or any(row[2] != "—" for row in facts)]
    return [tuple(row[index] for index in keep) for row in (("№", "Название", "Папка", "Приоритет", "Требования"), *facts)]


_SETUP_TAIL = " — готовит автотест"  # the tail of a precondition line an autotest prepares (mode B; approved by the user on 25.11)


def _prepared(extras: dict[str, Any] | None, case: dict[str, Any]) -> set[str]:
    """The precondition lines of *case* its setup prepares — only when the set is of mode B (``extras["setup_by_autotest"]``)."""
    if not (extras or {}).get("setup_by_autotest"):
        return set()
    lines = case["preconditions"]
    return {lines[item["precondition_index"]] for item in case.get("setup", []) if item["precondition_index"] < len(lines)}


def _tailed(items: list[str], prepared: set[str]) -> list[str]:
    return [item + _SETUP_TAIL if item in prepared else item for item in items]


def _common_tailed(shown: list[tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, str]]]], common: list[str], extras: dict[str, Any] | None) -> list[str]:
    """The common block: a line gets the tail only when every shown case prepares it — the block speaks for all of them."""
    prepared = set.intersection(*(_prepared(extras, case) for case, _group, _rows in shown)) if shown and common else set()
    return _tailed(common, prepared)


def _common_preconditions(shown: list[tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, str]]]]) -> list[str]:
    """The preconditions word for word the same in every shown case, in a set of three cases or more."""
    cases = [case for case, _group, _rows in shown]
    return [item for item in cases[0]["preconditions"] if all(item in own["preconditions"] for own in cases)] if len(cases) >= 3 else []


def _variant_columns(rows: list[dict[str, str]]) -> tuple[bool, bool]:
    """A title column only when titles differ, an expected column only when some row expects more than its value."""
    return len({row["title"] for row in rows}) > 1, any(row["expected"] != "—" for row in rows)


def _html_variants(group: dict[str, Any], rows: list[dict[str, str]]) -> str:
    name = _html_text(group["parameter"]["name"])
    titled, expects = _variant_columns(rows)
    head = "<th>№</th><th>" + name + "</th>" + ("<th>Ожидаемый результат</th>" if expects else "") + ("<th>Вариант</th>" if titled else "") + "<th>ID кейса</th>"
    body = "".join(
        f"<tr><td>{number}</td><td>{_html_text(row['values'])}</td>" + (f"<td>{_html_text(row['expected'])}</td>" if expects else "")
        + (f"<td>{_html_text(row['title'])}</td>" if titled else "") + f"<td>{html.escape(row['case_id'])}</td></tr>"
        for number, row in enumerate(rows, 1))
    return (f"<h3>Варианты данных</h3><p>Группа {html.escape(group['group_id'])}, параметр «{name}»: кейс на каждую строку.</p>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def _markdown_variants(group: dict[str, Any], rows: list[dict[str, str]]) -> list[str]:
    name = _readable_table_cell(group["parameter"]["name"])
    titled, expects = _variant_columns(rows)
    head = ["№", name, *(["Ожидаемый результат"] if expects else []), *(["Вариант"] if titled else []), "ID кейса"]
    lines = ["", "**Варианты данных**", "", f"Группа {group['group_id']}, параметр «{name}»: кейс на каждую строку.", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for number, row in enumerate(rows, 1):
        cells = [str(number), row["values"], *([row["expected"]] if expects else []), *([row["title"]] if titled else []), row["case_id"]]
        lines.append("| " + " | ".join(_readable_table_cell(cell) for cell in cells) + " |")
    return lines


def _html_text(value: str) -> str:
    return html.escape(value, quote=True).replace("\n", "<br>")


def _json_html(value: str) -> str:
    return f'<pre><code class="language-json">{html.escape(value, quote=True)}</code></pre>'


def _literal_url_value(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return _compact_json(value)
    return str(value)


def _url_placeholder(source: dict[str, Any], display_orders: dict[str, int] | None) -> str:
    """Tell a manual tester where a non-literal URL value comes from; never a secret handle."""
    kind = source["kind"]
    if kind == "step_output":
        number = (display_orders or {}).get(source["step_id"])
        origin = f"шага {number}" if number is not None else f"шага {source['step_id']}"
        return f"<{source['output_id']} из {origin}>"
    if kind == "fixture":
        return f"<фикстура {source['name']}>"
    if kind == "environment":
        return f"<переменная окружения {source['name']}>"
    return f"<{source['safe_label']}>"


def _http_url(step: dict[str, Any], display_orders: dict[str, int] | None = None) -> str | None:
    operation = step["operation"]
    if operation is None or operation["kind"] != "http":
        return None
    path = operation["path"]
    query: list[str] = []
    for item in sorted(step["inputs"], key=lambda row: row["display_order"]):
        target, source = item["target"], item["source"]
        if target["location"] not in {"path", "query"}:
            continue
        if source["kind"] == "literal" and not target.get("sensitive"):
            value = _literal_url_value(source["value"])
            path_value, query_value = quote(value, safe=""), urlencode([(target["name"], value)])
        else:
            # A dynamic or sensitive value cannot be printed; a readable placeholder replaces
            # the bare ``{name}`` so the tester knows what to substitute.
            path_value = _url_placeholder(source, display_orders)
            query_value = urlencode([(target["name"], "")]) + path_value
        if target["location"] == "path":
            path = path.replace("{" + target["name"] + "}", path_value)
        else:
            query.append(query_value)
    return path + ("?" + "&".join(query) if query else "")


def _human_action(step: dict[str, Any], display_orders: dict[str, int] | None = None) -> str:
    action = step["action"]
    operation = step["operation"]
    url = _http_url(step, display_orders)
    if url is None or operation is None:
        return action
    signature = f"{operation['method']} {operation['path']}"
    resolved = f"{operation['method']} {url}"
    if signature in action:
        action = action.replace(signature, resolved, 1)
    elif resolved not in action:
        action = f"{action.rstrip('.')} — {resolved}."
    return action


def _html_action(step: dict[str, Any], display_orders: dict[str, int] | None = None) -> str:
    return _html_text(_human_action(step, display_orders))


# The request block of an HTTP step in the v6 CSV and preview (texts approved by the user on 26.11, v09/postman-curl-plan.md §1):
# built by the code from the machine layer — `http`, or a project action of the strict form — never written by the model.
REQUEST_HEAD = "Запрос curl (Postman: Import → Raw text):"
_VERBS = frozenset(("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"))
_REQUEST_ARGS = frozenset(("method", "path", "body", "params", "parameters", "query", "headers", "session"))  # session: cookies, Postman keeps them
_SECRET_HEADER = re.compile(r"authorization|cookie|token|secret|password|api[-_]?key", re.I)
_TEMPLATE = re.compile(r"(?<!\{)\{[^{}]+\}(?!\})")  # a path template ``{name}`` left unfilled (not a ``{{variable}}``)


def _shell(value: str) -> str:
    """One bash argument: Postman's import reads the bash form of curl; a quote inside stays inside."""
    return "'" + value.replace("'", "'\\''") + "'"


def _variable(source: dict[str, Any], display_orders: dict[str, int], setups: dict[str, int], legend: dict[str, str]) -> str:
    """The Postman variable of a value the file must not or cannot carry, with its line of the legend; never a secret."""
    kind = source["kind"]
    if kind == "step_output":
        name, line = source["output_id"], f"{source['output_id']} из ответа шага {display_orders[source['step_id']]}"
    elif kind == "setup_output":
        name, line = source["output_id"], f"{source['output_id']} из подготовки к предусловию {setups[source['setup_id']] + 1}"
    elif kind == "environment":
        name, line = source["name"], f"переменная окружения {source['name']}"
    elif kind == "fixture":
        name, line = source["name"], f"фикстура {source['name']}"
    else:
        name, line = source["safe_label"], "секрет, в файл не попадает"
    legend.setdefault(name, line)
    return "{{" + name + "}}"


def _secret_header(name: str, legend: dict[str, str]) -> str:
    legend.setdefault(name, "секрет, в файл не попадает")
    return "{{" + name + "}}"


def _http_parts(step: dict[str, Any], display_orders: dict[str, int], setups: dict[str, int], legend: dict[str, str]) -> tuple[str, str, list[tuple[str, str]], str | None] | None:
    """Method, URL, headers and JSON body of an ``http`` operation; None when its body cannot be put together from the pointers."""
    operation = step["operation"]
    base = operation["base_url_source"]["name"]
    legend.setdefault(base, f"адрес стенда (переменная окружения {base})")
    path, query, headers, body, sentinels = operation["path"], [], [], None, {}
    for item in sorted(step["inputs"], key=lambda row: row["display_order"]):
        target, source = item["target"], item["source"]
        literal = source["kind"] == "literal"
        value = source["value"] if literal else _variable(source, display_orders, setups, legend)
        if target["location"] == "path":
            path = path.replace("{" + target["name"] + "}", quote(_literal_url_value(value), safe="") if literal else value)
        elif target["location"] == "query":
            query.append(urlencode([(target["name"], _literal_url_value(value))]) if literal else urlencode([(target["name"], "")]) + value)
        elif target["location"] == "header":
            headers.append((target["name"], _secret_header(target["name"], legend) if _SECRET_HEADER.search(target["name"]) else _literal_url_value(value) if literal else value))
        else:
            tokens = [token.replace("~1", "/").replace("~0", "~") for token in target["pointer"].split("/")[1:]]
            if not literal:  # the variable goes into the body after the dump: bare for a number, quoted for a string
                sentinels[f"{len(sentinels)}"] = value if item["semantic_type"].get("type", item["semantic_type"].get("representation")) != "string" else f'"{value}"'
                value = list(sentinels)[-1]
            if not tokens:
                body = value
                continue
            if any(token.isdigit() for token in tokens) or body is not None and not isinstance(body, dict):
                return None  # an array member or a body that is not an object: the code does not guess its shape
            body = {} if body is None else body
            holder = body
            for token in tokens[:-1]:
                holder = holder.setdefault(token, {})
                if not isinstance(holder, dict):
                    return None
            holder[tokens[-1]] = value
    if _TEMPLATE.search(path):
        return None
    text = None
    if body is not None:
        text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        for sentinel, variable in sentinels.items():
            text = text.replace(json.dumps(sentinel, ensure_ascii=False), variable)
        headers.append(("Content-Type", "application/json"))
    return operation["method"], "{{" + base + "}}" + path + ("?" + "&".join(query) if query else ""), headers, text


def _action_parts(step: dict[str, Any], display_orders: dict[str, int], setups: dict[str, int], legend: dict[str, str]) -> tuple[str, str, list[tuple[str, str]], str | None, list[str]] | None:
    """Method, URL, headers, body and form fields of a project action of the strict form; None for any other action (its helper builds the request)."""
    args = {item["target"].get("name"): item["source"] for item in step["inputs"]}
    if any(item["target"]["location"] != "arg" for item in step["inputs"]) or not {"method", "path"} <= set(args) or set(args) - _REQUEST_ARGS:
        return None
    method, path = args["method"], args["path"]
    if method["kind"] != "literal" or not isinstance(method["value"], str) or method["value"] not in _VERBS or path["kind"] != "literal" or not isinstance(path["value"], str):
        return None
    if not path["value"].startswith("/") or _TEMPLATE.search(path["value"]):
        return None
    legend.setdefault("baseUrl", "адрес стенда")
    query, form, headers, body = [], [], [], None
    for name in ("params", "parameters", "query"):
        source = args.get(name)
        if source is None:
            continue
        if source["kind"] != "literal" or not isinstance(source["value"], dict):
            return None
        pairs = [(key, _literal_url_value(item)) for key, value in source["value"].items() for item in (value if isinstance(value, list) else [value])]
        if name == "query" or method["value"] in {"GET", "HEAD"}:
            query.append(urlencode(pairs))
        else:
            form.extend(f"{key}={value}" for key, value in pairs)  # --data-urlencode encodes the value itself
    if "headers" in args:
        if args["headers"]["kind"] != "literal" or not isinstance(args["headers"]["value"], dict):
            return None
        headers = [(key, _secret_header(key, legend) if _SECRET_HEADER.search(key) else _literal_url_value(value)) for key, value in args["headers"]["value"].items()]
    if "body" in args:
        source = args["body"]
        if form or source["kind"] == "literal" and not isinstance(source["value"], (dict, list)):
            return None
        body = _compact_json(source["value"]) if source["kind"] == "literal" else _variable(source, display_orders, setups, legend)
        headers.append(("Content-Type", "application/json"))
    target = path["value"] + ("&" if "?" in path["value"] else "?") + "&".join(item for item in query if item) if any(query) else path["value"]
    return method["value"], "{{baseUrl}}" + target, headers, body, form


def request_block(step: dict[str, Any], display_orders: dict[str, int], case: dict[str, Any]) -> str | None:
    """The approved block «Запрос curl …» of an HTTP step: the head, one curl line and the legend of its variables; None when the
    machine layer does not say the whole request (no block is better than a wrong request)."""
    operation = step["operation"]
    if operation is None:
        return None
    setups = {item["setup_id"]: item["precondition_index"] for item in case.get("setup") or []}
    legend: dict[str, str] = {}
    if operation["kind"] == "http":
        parts = _http_parts(step, display_orders, setups, legend)
        form: list[str] = []
        if parts is None:
            return None
        method, url, headers, body = parts
    else:
        action = _action_parts(step, display_orders, setups, legend)
        if action is None:
            return None
        method, url, headers, body, form = action
    line = ["curl", "-X", method, _shell(url), *(word for name, value in headers for word in ("-H", _shell(f"{name}: {value}")))]
    line += ["--data-raw", _shell(body)] if body is not None else [word for field in form for word in ("--data-urlencode", _shell(field))]
    # the address of the stand first, then the variables in the order of the request
    names = sorted(legend, key=lambda name: not legend[name].startswith("адрес стенда"))
    return "\n".join((REQUEST_HEAD, " ".join(line), "; ".join("{{" + name + "}} — " + legend[name] for name in names) + "."))


def _html_test_data(step: dict[str, Any]) -> str:
    if SLOT in step["test_data"]:  # a slotted request is no JSON any more; shown as it is
        data = step["test_data"]
        return f"<pre>{html.escape(data, quote=True)}</pre>" if "{" in data or "[" in data else f"<p>{_html_text(data)}</p>"
    marker = step["test_data"].strip()
    if marker in {"—", "Тело запроса отсутствует."}:
        return "—"
    prose, body = _trailing_json(step["test_data"])
    operation = step["operation"]
    if operation is None or operation["kind"] != "http":
        parts = [f"<p>{_html_text(prose)}</p>"] if prose else []
        if body is not None:
            parts.append(_json_html(body))
        return "".join(parts) or "—"
    return _json_html(body) if body is not None else "—"


def _html_expected(step: dict[str, Any]) -> str:
    parts: list[str] = []
    for expectation in step["expectations"]:
        prose, body = _trailing_json(expectation["text"])
        if prose:
            parts.append(f"<p>{_html_text(prose)}</p>")
        if body is not None:
            parts.append(_json_html(body))
    return "".join(parts) or "—"


def render_html_preview(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Render the current standalone three-column human preview."""
    _reject_unknown_profile(profile)
    if profile not in _PUBLISHABLE_PROFILES:
        raise ValueError(f"HTML preview is only available for {_PROFILE_V6}, {_PROFILE_V5} and {_PROFILE_V4}")
    require_valid_canonical_document(document)
    return _render_html_preview_validated(document, profile)


def _render_html_preview_validated(document: dict[str, Any], profile: str = _PROFILE, extras: dict[str, Any] | None = None) -> Projection:
    """Render the current preview after the caller has validated the document."""
    if profile not in _PUBLISHABLE_PROFILES:
        raise ValueError(f"HTML preview is only available for {_PROFILE_V6}, {_PROFILE_V5} and {_PROFILE_V4}")
    metadata = document["metadata"]
    warnings: tuple[str, ...] = ()
    documentation = metadata["documentation"]
    if documentation:
        docs = "<ul>" + "".join(f"<li>{_html_text(item)}</li>" for item in documentation) + "</ul>"
    else:
        docs = "<p>Не предоставлена</p>"
        warnings = ('MISSING_DOCUMENTATION: metadata.documentation is empty; rendered as "Не предоставлена"',)
    subject = metadata["subject"]
    title = f"Тест-кейсы метода {subject['method']} {subject['path']}" if subject["kind"] == "http_endpoint" else f"Тест-кейсы: {subject['name']}"
    shown = _grouped_cases(document)
    facts, common = _facts(shown, extras), _common_preconditions(shown)
    table = _overview(facts)
    rows = [
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>" + html.escape(title, quote=True) + "</title>",
        "<style>body{font:16px/1.45 system-ui,sans-serif;margin:2rem;max-width:1200px}table{border-collapse:collapse;table-layout:fixed;width:100%;margin:1rem 0 2rem}th,td{border:1px solid #bbb;padding:.75rem;vertical-align:top;text-align:left;overflow-wrap:anywhere}th{background:#f3f3f3}pre{margin:.5rem 0;white-space:pre-wrap}p{margin:.5rem 0}code{font-family:ui-monospace,monospace}</style></head><body>",
        f"<h1>{html.escape(title, quote=True)}</h1><p><strong>Проект:</strong> {_html_text(metadata['project'])}<br><strong>Дата:</strong> {_html_text(metadata['date'])}</p><h2>Документация</h2>{docs}"
        + ("<h2>Общие предусловия</h2><ul>" + "".join(f"<li>{_html_text(item)}</li>" for item in _common_tailed(shown, common, extras)) + "</ul>" if common else "")
        + "<h2>Кейсы набора</h2><table><thead><tr>" + "".join(f"<th>{name}</th>" for name in table[0]) + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{_html_text(cell)}</td>" for cell in row) + "</tr>" for row in table[1:]) + "</tbody></table>",
    ]
    for case_number, (case, group, variants) in enumerate(shown, 1):
        start = len(rows)
        own = _tailed([item for item in case["preconditions"] if item not in common], _prepared(extras, case))
        notes = "".join(f"<p><strong>{_html_text(label)}</strong> {_html_text(text)}</p>" for label, text in _notes(extras, case))
        rows.extend((
            f"<section><h2>ТК-{_number(extras, case_number, case, group)}. {_html_text(case['title'])}</h2><p><strong>Приоритет:</strong> {facts[case_number - 1][3]}. <strong>Требования:</strong> {_html_text(facts[case_number - 1][4])}</p>{notes}<p><strong>Цель:</strong> {_html_text(case['objective'])}</p>"
            + (f"<p>Техника: {html.escape(technique_name(case['technique']))}</p>" if case.get("technique", "OTHER") != "OTHER" else "")
            + (("<h3>Предусловия</h3>" + ("<ul>" + "".join(f"<li>{_html_text(item)}</li>" for item in own) + "</ul>" if own else "<p>Не требуются.</p>")) if own or not common else ""),
            "<table><colgroup><col style=\"width:27%\"><col style=\"width:41%\"><col style=\"width:32%\"></colgroup><thead><tr><th>Шаг</th><th>Тестовые данные / запрос</th><th>Ожидаемый результат</th></tr></thead><tbody>",
        ))
        display_orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
        for step_number, step in enumerate(case["steps"], 1):
            block = request_block(step, display_orders, case) if profile == _PROFILE_V6 and group is None else None
            request = "" if block is None else f"<pre>{html.escape(block, quote=True)}</pre>"
            rows.append(f"<tr><td><strong>Шаг {step_number}</strong><br>{_html_action(step, display_orders)}</td><td>{_html_test_data(step)}{request}</td><td>{_html_expected(step)}</td></tr>")
        rows.append("</tbody></table>" + (_html_variants(group, variants) if group else "") + "</section>")
        if group:
            rows[start:] = [row.replace(SLOT, f"<code>&lt;{_html_text(group['parameter']['name'])}&gt;</code>") for row in rows[start:]]
    rows.append("</body></html>\n")
    return Projection("".join(rows).encode("utf-8"), warnings)


def render_markdown(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Render a current human-readable Markdown companion from canonical JSON."""
    _reject_unknown_profile(profile)
    if profile in _PUBLISHABLE_PROFILES:
        require_valid_canonical_document(document)
        return _render_markdown_validated(document, profile)
    raise ValueError("historical Markdown projections are verification-only through an explicit bundle receipt")


def _render_markdown_validated(document: dict[str, Any], profile: str, extras: dict[str, Any] | None = None) -> Projection:
    """Render Markdown after the public facade has accepted the canonical document."""
    if profile in {_PROFILE_V5, _PROFILE_V6}:
        profile = _PROFILE_V4  # the human Markdown companion is identical for the publishable profiles
    metadata = document["metadata"]
    documentation = metadata["documentation"]
    warnings: tuple[str, ...] = ()
    if documentation:
        if profile in {_PROFILE_V3, _PROFILE_V4}:
            rendered_documentation = " • ".join(_readable_table_cell(item) for item in documentation)
        else:
            rendered_documentation = "<br>".join(escape_inline(item) for item in documentation)
    else:
        rendered_documentation = "Не предоставлена"
        warnings = ('MISSING_DOCUMENTATION: metadata.documentation is empty; rendered as "Не предоставлена"',)

    current = profile == _PROFILE_V4  # the files of an older profile keep their bytes: a receipt of theirs still has to verify
    shown = _grouped_cases(document) if current else [(case, None, []) for case in document["test_cases"]]
    facts, common = _facts(shown, extras), _common_preconditions(shown) if current else []
    lines = [
        _markdown_subject(metadata["subject"]), "",
        f"**Документация:** {rendered_documentation}",
        f"**{'Project' if profile == _PROFILE_V1 else 'Проект'}:** {escape_inline(metadata['project'])}",
        *([] if current else [f"**Автор:** {escape_inline(metadata['author'])}"]),
        f"**Дата:** {escape_inline(metadata['date'])}",
    ]
    if common:
        lines.extend(["", "**Общие предусловия:**", *("- " + escape_inline(item) for item in _common_tailed(shown, common, extras))])
    if current:
        table = _overview(facts)
        lines.extend(["", "**Кейсы набора**", "", "| " + " | ".join(table[0]) + " |", "|" + "---|" * len(table[0]),
                      *("| " + " | ".join(_readable_table_cell(cell) for cell in row) + " |" for row in table[1:])])
    for case_number, (case, group, variants) in enumerate(shown, 1):
        start = len(lines)
        own = _tailed([item for item in case["preconditions"] if item not in common], _prepared(extras, case))
        lines.extend(["", "---", "", f"## ТК-{_number(extras, case_number, case, group)}. {escape_inline(case['title'])}", ""])
        if current:
            lines.extend([f"**Приоритет:** {facts[case_number - 1][3]}. **Требования:** {_readable_table_cell(facts[case_number - 1][4])}", ""])
        for label, text in _notes(extras, case):
            lines.extend([f"**{escape_inline(label)}** {escape_inline(text)}", ""])
        lines.extend([f"**Цель:** {escape_inline(case['objective'])}", ""])
        if case.get("technique", "OTHER") != "OTHER":
            lines.extend([f"Техника: {technique_name(case['technique'])}", ""])
        if own or not common:
            lines.extend(["**Предусловия:**", *("- " + escape_inline(item) for item in own or ["Не требуются."])])
        if profile == _PROFILE_V1:
            lines.extend(["", "**Шаги:**", "", "| № | Действие | Ожидаемый результат |", "|---|---|---|"])
        elif profile == _PROFILE_V2:
            lines.extend(["", "**Шаги:**", "", "| № | Действие | Тестовые данные / запрос | Ожидаемый результат |", "|---|---|---|---|"])
        else:
            lines.extend(["", "**Шаги:**"])
        display_orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
        capabilities = {item["capability_id"]: item for item in document["operation_capabilities"]}
        for step_number, step in enumerate(case["steps"], 1):
            if profile == _PROFILE_V1:
                expectations = "<br>".join(escape_inline(item["text"]) for item in step["expectations"])
                lines.append(f"| {step_number} | {escape_inline(step['action'])} | {expectations} |")
            else:
                if profile in {_PROFILE_V3, _PROFILE_V4}:
                    data = step["test_data"]
                    expected = "\n\n".join(item["text"] for item in step["expectations"])
                else:
                    data = _detailed_step_data(step, display_orders, capabilities)
                    expected = _detailed_expected_result(step, display_orders)
                render = escape_inline if profile == _PROFILE_V2 else _readable_table_cell
                if profile == _PROFILE_V2:
                    lines.append(f"| {step_number} | {render(step['action'])} | {render(data)} | {render(expected)} |")
                else:
                    lines.extend([
                        "", f"### Шаг {step_number}", "", escape_inline(_human_action(step, display_orders)) if profile == _PROFILE_V4 else step["action"], "",
                        "**Тестовые данные / запрос**", "", *_markdown_human_blocks(data, literal=profile == _PROFILE_V4), "",
                        "**Ожидаемый результат**", "", *_markdown_human_blocks(expected, literal=profile == _PROFILE_V4),
                    ])
        if group:
            slot = "<" + "".join("\\" + char if char in "\\<>[]" else char for char in group["parameter"]["name"].replace("\n", " ")) + ">"  # the name is the model's: no tag, no link
            lines[start:] = [line.replace(SLOT, slot) for line in lines[start:]] + _markdown_variants(group, variants)
    return Projection(("\n".join(lines) + "\n").encode("utf-8"), warnings)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _custom_value(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return ", ".join(str(_custom_value(item)) for item in value)


def _formula_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    after_ascii_whitespace = value.lstrip(" \t\r\n")
    if after_ascii_whitespace and after_ascii_whitespace[0] in "=+-@":
        return "'" + value
    if not after_ascii_whitespace and any(character in "\t\r\n" for character in value):
        return "'" + value
    return value


def _input_target(target: dict[str, Any]) -> str:
    if target["location"] == "body":
        return "body:" + target["pointer"]
    return target["location"] + ":" + target["name"]


def _input_source(source: dict[str, Any], display_orders: dict[str, int]) -> str:
    kind = source["kind"]
    if kind == "literal":
        return _compact_json(source["value"])
    if kind == "step_output":
        return f"{source['output_id']}, полученный на шаге {display_orders[source['step_id']]}"
    if kind == "fixture":
        return "fixture:" + source["name"]
    if kind == "environment":
        return "env:" + source["name"]
    return "secret:" + source["safe_label"]


def _operation_text(step: dict[str, Any], capabilities: dict[str, dict[str, Any]]) -> str:
    operation = step["operation"]
    if operation is None:
        return "Операция: не определена"
    if operation["kind"] == "http":
        base = operation["base_url_source"]
        return f"HTTP: {operation['method']} {operation['path']}; базовый URL: {base['kind']}:{base['name']}"
    capability = capabilities[operation["capability_id"]]
    return f"Действие проекта: {capability['capability_id']}; адаптер: {capability['adapter']}; действие: {capability['action']}"


def _output_source(source: dict[str, Any]) -> str:
    kind = source["kind"]
    if kind == "http_body":
        return "http_body:" + source["pointer"]
    if kind in {"http_header", "project_result"}:
        return kind + ":" + source["name"]
    return kind


def _assertion_value(value: dict[str, Any], display_orders: dict[str, int]) -> str:
    kind = value["kind"]
    if kind == "literal":
        return _compact_json(value["value"])
    if kind == "step_output":
        return f"{value['output_id']}, полученный на шаге {display_orders[value['step_id']]}"
    if kind in {"fixture", "environment"}:
        return kind + ":" + value["name"]
    if kind == "secret_handle":
        return "secret:" + value["safe_label"]
    if kind == "regex":
        return "regex:" + value["pattern"]
    if kind == "schema_ref":
        return "schema:" + value["uri"] + "; sha256: " + value["sha256"]
    if kind == "http_body":
        return "http_body:" + value["pointer"]
    if kind in {"http_header", "project_result"}:
        return kind + ":" + value["name"]
    return kind


def _detailed_step_data(step: dict[str, Any], display_orders: dict[str, int], capabilities: dict[str, dict[str, Any]]) -> str:
    rows = [_operation_text(step, capabilities)]
    rows.extend(f"{_input_target(item['target'])} = {_input_source(item['source'], display_orders)}" for item in step["inputs"])
    return "\n".join(rows)


def _detailed_expected_result(step: dict[str, Any], display_orders: dict[str, int]) -> str:
    rows = [item["text"] for item in step["expectations"]]
    rows.extend(f"Выход: {item['output_id']} = {_output_source(item['source'])}" for item in step["outputs"])
    rows.extend(
        f"Проверка: {_assertion_value(assertion['actual'], display_orders)} {assertion['operator']}"
        + (" " + _assertion_value(assertion["expected"], display_orders) if "expected" in assertion else "")
        for expectation in step["expectations"] for assertion in expectation["assertions"]
    )
    return "\n".join(rows)


def _step_data(step: dict[str, Any], display_orders: dict[str, int]) -> str:
    return "\n".join(
        f"{_input_target(item['target'])} = {_input_source(item['source'], display_orders)}"
        for item in step["inputs"]
    )


def _case_metadata(case: dict[str, Any]) -> list[Any]:
    management = case["management"]
    custom_fields = management["custom_fields"]
    return [
        management["external_keys"].get("zephyr_scale", ""), case["title"], management["status"] or "",
        "\n".join(case["preconditions"]), case["objective"], management["folder"] or "",
        _PRIORITIES[case["priority"]], ", ".join(management["components"]), ", ".join(management["labels"]),
        management["owner"] or "", management["estimated_time"] or "",
        ", ".join(management["external_links"]["issues"]), ", ".join(management["external_links"]["pages"]),
        *(_custom_value(custom_fields[key]) if key in custom_fields else "" for key in _CUSTOM_KEYS),
    ]


def _custom_field_warnings(case: dict[str, Any]) -> tuple[str, ...]:
    unmapped = sorted(key for key in case["management"]["custom_fields"] if key not in _CUSTOM_KEYS)
    return tuple(
        f"UNMAPPED_ZEPHYR_CUSTOM_FIELD: case_id={json.dumps(case['case_id'], ensure_ascii=False)}, key={json.dumps(key, ensure_ascii=False)}"
        for key in unmapped
    )


def render_zephyr_csv(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Project a new document to the human-only V5 default or the opt-in V4 step-row profile."""
    _reject_unknown_profile(profile)
    if profile not in _PUBLISHABLE_PROFILES:
        raise ValueError("historical Zephyr CSV profiles are verification-only through an explicit bundle receipt")
    require_valid_canonical_document(document)
    return _render_zephyr_csv_validated(document, profile)


def _render_zephyr_csv_validated(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Render the fixed CSV profile after the public facade has validated input."""
    records: list[list[Any]] = [list(_HEADERS)]
    warnings: list[str] = []
    for case in document["test_cases"]:
        warnings.extend(_custom_field_warnings(case))
        display_orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
        capabilities = {item["capability_id"]: item for item in document["operation_capabilities"]}
        metadata = _case_metadata(case)
        for step_index, step in enumerate(case["steps"]):
            record = metadata if step_index == 0 else [""] * 19
            if profile in {_PROFILE_V5, _PROFILE_V6}:
                # Human scenario only: the action with its resolved URL, the authored test data
                # and the expectation prose.  No operation, binding or assertion model.
                expected = "\n\n".join(item["text"] for item in step["expectations"])
                block = request_block(step, display_orders, case) if profile == _PROFILE_V6 else None
                data = step["test_data"] if block is None else step["test_data"] + "\n\n" + block
                record = list(record) + [_human_action(step, display_orders), data, expected, "", ""]
                records.append([_formula_safe(value) for value in record])
                continue
            data = _step_data(step, display_orders) if profile == _PROFILE_V1 else _detailed_step_data(step, display_orders, capabilities)
            expected = "\n".join(item["text"] for item in step["expectations"]) if profile == _PROFILE_V1 else _detailed_expected_result(step, display_orders)
            record = list(record) + [step["action"], data, expected, "", ""]
            records.append([_formula_safe(value) for value in record])
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=",", quotechar='"', lineterminator="\r\n")
    writer.writerows(records)
    return Projection(output.getvalue().encode("utf-8-sig"), tuple(warnings))


def restored_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """The records of skeleton rows for the CSV of the export: the cells ``_render_zephyr_csv_validated`` writes for a human case (no header, no byte order mark)."""
    from tools.cases_import import canonical_management

    records: list[list[Any]] = []
    for row in rows:
        head = _case_metadata({"title": row["title"], "objective": row["objective"], "preconditions": row["preconditions"], "priority": row["priority"], "management": canonical_management(row)})
        records += [[_formula_safe(value) for value in [*(head if number == 0 else [""] * len(head)), step["action"], step["test_data"], "\n\n".join(step["expectations"]), "", ""]]
                    for number, step in enumerate(row["steps"])]
    output = io.StringIO(newline="")
    csv.writer(output, delimiter=",", quotechar='"', lineterminator="\r\n").writerows(records)
    return output.getvalue().encode("utf-8")


# The public names of the CSV profiles and the shared parts of the CSV for the neighbours (publish_test_case_bundle, zephyr_csv,
# zephyr_template_export): one place of the profiles; the neighbours bind them under their old private names.
DEFAULT_PROFILE = _PROFILE
PUBLISHABLE_PROFILES = _PUBLISHABLE_PROFILES
CUSTOM_KEYS = _CUSTOM_KEYS
HEADERS = _HEADERS
PRIORITIES = _PRIORITIES
formula_safe = _formula_safe
reject_unknown_profile = _reject_unknown_profile
render_html_preview_validated = _render_html_preview_validated
render_markdown_validated = _render_markdown_validated
render_zephyr_csv_validated = _render_zephyr_csv_validated
