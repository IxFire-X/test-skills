"""Deterministic terminal human previews and fixed-profile Zephyr CSV projections."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote, urlencode

from tools.canonical_document import require_valid_canonical_document


_PROFILE_V1 = "zephyr-scale-step-row-24-v1"
_PROFILE_V2 = "zephyr-scale-step-row-24-v2"
_PROFILE_V3 = "zephyr-scale-step-row-24-v3"
_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE_V5 = "zephyr-scale-step-row-24-v5"
_PROFILE = _PROFILE_V5
_PROFILES = frozenset((_PROFILE_V1, _PROFILE_V2, _PROFILE_V3, _PROFILE_V4, _PROFILE_V5))
# Profiles a new bundle may be published with: V5 is the human-only default, V4 keeps the
# machine-model step columns as an explicit compatibility opt-in.  Their HTML/Markdown are identical.
_PUBLISHABLE_PROFILES = frozenset((_PROFILE_V4, _PROFILE_V5))
_CUSTOM_KEYS = ("АС", "Автоматизирован", "Вид тестирования", "Команда", "Приоритет теста", "Статус")
_XML_PROFILE = "zephyr-scale-xml-observed-v1"
# Zephyr Scale's default priorities are High / Normal / Low: a `Highest` value does not import (live run g, finding 10).
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


def escape_inline(value: str) -> str:
    """Escape one user-originated value for its Markdown inline position."""
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\\", "\\\\")
    for marker in "`*_[]<>#|":
        value = value.replace(marker, "\\" + marker)
    return value.replace("\n", "<br>")


_ORDERED_MARKER = re.compile(r"^(\s*[0-9]{1,9})([.)])(?=\s|$)")
_BULLET_MARKER = re.compile(r"^(\s*)([-+])(?=\s|$)")
_DASH_RULE = re.compile(r"^(\s*)(-)(?=(?:[ \t]*-){2,}[ \t]*$)")


def _escape_line(value: str) -> str:
    """Escape one single-line value that starts its own Markdown line.

    ``escape_inline`` covers inline markup; a line start additionally gives meaning to
    ``1.``/``1)`` (ordered list), ``-``/``+`` (bullet) and ``---`` (thematic break).
    """
    escaped = escape_inline(value)
    escaped = _ORDERED_MARKER.sub(lambda match: match.group(1) + "\\" + match.group(2), escaped, count=1)
    escaped = _DASH_RULE.sub(lambda match: match.group(1) + "\\" + match.group(2), escaped, count=1)
    return _BULLET_MARKER.sub(lambda match: match.group(1) + "\\" + match.group(2), escaped, count=1)


def _readable_table_cell(value: str) -> str:
    return escape_inline(value).replace("\\_", "_")


def _markdown_human_blocks(value: str, *, literal: bool = False) -> list[str]:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    trailing_json: str | None = None
    # Only "\n" is a line break here: str.splitlines() would also split on U+2028, U+2029,
    # form feed and NEL, which are legal inside a JSON string and must stay in its code fence.
    source_lines = value.split("\n")
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
        rendered.append("\n\n".join(_escape_line(line) if literal else line for line in paragraph.split("\n")))
    if trailing_json is not None:
        rendered.append(f"```json\n{trailing_json}\n```")
    return "\n\n".join(rendered).split("\n") if rendered else []


def _escape_precondition(value: str) -> str:
    """Escape a list item whose first line would otherwise open a nested list or a rule."""
    first, separator, rest = value.replace("\r\n", "\n").replace("\r", "\n").partition("\n")
    return _escape_line(first) + (escape_inline(separator + rest) if separator else "")


def _markdown_expected_blocks(step: dict[str, Any]) -> list[str]:
    """Render every expectation on its own so each trailing JSON template gets a code fence."""
    lines: list[str] = []
    for expectation in step["expectations"]:
        block = _markdown_human_blocks(expectation["text"], literal=True)
        if block and lines:
            lines.append("")
        lines.extend(block)
    return lines


def _markdown_subject(subject: dict[str, Any]) -> str:
    if subject["kind"] == "http_endpoint":
        return f"# Тест-кейсы метода {escape_inline(subject['method'])} {escape_inline(subject['path'])}"
    return f"# Тест-кейсы: {escape_inline(subject['name'])}"


def _reject_unknown_profile(profile: str) -> None:
    if profile not in _PROFILES:
        raise ValueError(f"unknown Zephyr CSV profile: {profile}")


def _trailing_json(value: str) -> tuple[str, str | None]:
    """Split a human field into its prose and a trailing JSON object or array."""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    for index, line in enumerate(lines):
        if not line.lstrip().startswith(("{", "[")):
            continue
        candidate = "\n".join(lines[index:]).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            return "\n".join(lines[:index]).strip(), candidate
    return "\n".join(lines).strip(), None


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


def _html_test_data(step: dict[str, Any]) -> str:
    marker = step["test_data"].strip()
    if marker in {"—", "Тело запроса отсутствует."}:
        return marker
    prose, body = _trailing_json(step["test_data"])
    operation = step["operation"]
    if operation is None or operation["kind"] != "http":
        parts = [f"<p>{_html_text(prose)}</p>"] if prose else []
        if body is not None:
            parts.append(_json_html(body))
        return "".join(parts) or "—"
    if body is not None:
        return _json_html(body)
    if prose:
        # Body-less request with authored notes (for example about URL parameters): show them.
        return f"<p>{_html_text(prose)}</p>"
    if not any(item["target"]["location"] == "body" for item in step["inputs"]):
        return "Тело запроса отсутствует."
    return "—"


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
        raise ValueError(f"HTML preview is only available for {_PROFILE_V5} and {_PROFILE_V4}")
    require_valid_canonical_document(document)
    return _render_html_preview_validated(document, profile)


def _render_html_preview_validated(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Render the current preview after the caller has validated the document."""
    if profile not in _PUBLISHABLE_PROFILES:
        raise ValueError(f"HTML preview is only available for {_PROFILE_V5} and {_PROFILE_V4}")
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
    rows = [
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>" + html.escape(title, quote=True) + "</title>",
        "<style>body{font:16px/1.45 system-ui,sans-serif;margin:2rem;max-width:1200px}table{border-collapse:collapse;table-layout:fixed;width:100%;margin:1rem 0 2rem}th,td{border:1px solid #bbb;padding:.75rem;vertical-align:top;text-align:left;overflow-wrap:anywhere}th{background:#f3f3f3}pre{margin:.5rem 0;white-space:pre-wrap}p{margin:.5rem 0}code{font-family:ui-monospace,monospace}</style></head><body>",
        f"<h1>{html.escape(title, quote=True)}</h1><p><strong>Проект:</strong> {_html_text(metadata['project'])}<br><strong>Автор:</strong> {_html_text(metadata['author'])}<br><strong>Дата:</strong> {_html_text(metadata['date'])}</p><h2>Документация</h2>{docs}",
    ]
    for case_number, case in enumerate(document["test_cases"], 1):
        rows.extend((
            f"<section><h2>ТК-{case_number}. {_html_text(case['title'])}</h2><p><strong>Цель:</strong> {_html_text(case['objective'])}</p><h3>Предусловия</h3>",
            "<ul>" + "".join(f"<li>{_html_text(item)}</li>" for item in case["preconditions"]) + "</ul>" if case["preconditions"] else "<p>Не требуются.</p>",
            "<table><colgroup><col style=\"width:27%\"><col style=\"width:41%\"><col style=\"width:32%\"></colgroup><thead><tr><th>Шаг</th><th>Тестовые данные / запрос</th><th>Ожидаемый результат</th></tr></thead><tbody>",
        ))
        display_orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
        for step_number, step in enumerate(case["steps"], 1):
            rows.append(f"<tr><td><strong>Шаг {step_number}</strong><br>{_html_action(step, display_orders)}</td><td>{_html_test_data(step)}</td><td>{_html_expected(step)}</td></tr>")
        rows.append("</tbody></table></section>")
    rows.append("</body></html>\n")
    return Projection("".join(rows).encode("utf-8"), warnings)


def render_markdown(document: dict[str, Any], profile: str = _PROFILE) -> Projection:
    """Render a current human-readable Markdown companion from canonical JSON."""
    _reject_unknown_profile(profile)
    if profile in _PUBLISHABLE_PROFILES:
        require_valid_canonical_document(document)
        return _render_markdown_validated(document, profile)
    raise ValueError("historical Markdown projections are verification-only through an explicit bundle receipt")


def _render_markdown_validated(document: dict[str, Any], profile: str) -> Projection:
    """Render Markdown after the public facade has accepted the canonical document."""
    if profile == _PROFILE_V5:
        profile = _PROFILE_V4  # the human Markdown companion is identical for both publishable profiles
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

    lines = [
        _markdown_subject(metadata["subject"]), "",
        f"**Документация:** {rendered_documentation}",
        f"**{'Project' if profile == _PROFILE_V1 else 'Проект'}:** {escape_inline(metadata['project'])}",
        f"**Автор:** {escape_inline(metadata['author'])}",
        f"**Дата:** {escape_inline(metadata['date'])}",
    ]
    for case_number, case in enumerate(document["test_cases"], 1):
        lines.extend(["", "---", "", f"## ТК-{case_number}. {escape_inline(case['title'])}", ""])
        lines.extend([f"**Цель:** {escape_inline(case['objective'])}", "", "**Предусловия:**"])
        if case["preconditions"]:
            precondition = _escape_precondition if profile == _PROFILE_V4 else escape_inline
            lines.extend("- " + precondition(item) for item in case["preconditions"])
        else:
            lines.append("- Не требуются.")
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
                        "", f"### Шаг {step_number}", "", _escape_line(_human_action(step, display_orders)) if profile == _PROFILE_V4 else step["action"], "",
                        "**Тестовые данные / запрос**", "", *_markdown_human_blocks(data, literal=profile == _PROFILE_V4), "",
                        "**Ожидаемый результат**", "", *(_markdown_expected_blocks(step) if profile == _PROFILE_V4 else _markdown_human_blocks(expected)),
                    ])
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


def _xml_rich_text(value: str) -> str:
    """Render observed Zephyr rich text without exposing XML markup to input."""
    if any(
        ord(character) not in (0x09, 0x0A, 0x0D)
        and not 0x20 <= ord(character) <= 0xD7FF
        and not 0xE000 <= ord(character) <= 0xFFFD
        and not 0x10000 <= ord(character) <= 0x10FFFF
        for character in value
    ):
        raise ValueError("Zephyr XML text contains an XML 1.0-incompatible character")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    escaped = normalized.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return escaped.replace("]]>", "]]&gt;").replace("\n", "<br />")


def _xml_cdata(name: str, value: str, level: int) -> str:
    return "    " * level + f"<{name}><![CDATA[{_xml_rich_text(value)}]]></{name}>"


def _xml_export_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Zephyr XML metadata.date must be an ISO date") from None
    if parsed.isoformat() != value:
        raise ValueError("Zephyr XML metadata.date must be an ISO date")
    return f"{value} 00:00:00 UTC"


def _xml_empty_or_values(container: str, item: str, values: list[str], level: int) -> list[str]:
    indent = "    " * level
    if not values:
        return [f"{indent}<{container}/>"]
    rows = [f"{indent}<{container}>"]
    rows.extend(_xml_cdata(item, value, level + 1) for value in values)
    rows.append(f"{indent}</{container}>")
    return rows


def _xml_management_warnings(case: dict[str, Any]) -> tuple[str, ...]:
    management = case["management"]
    unsupported = []
    for field in ("folder", "components", "estimated_time", "external_keys"):
        if management[field]:
            unsupported.append(field)
    if management["external_links"]["pages"]:
        unsupported.append("external_links.pages")
    for name, value in management["custom_fields"].items():
        if name not in _CUSTOM_KEYS or type(value) is not str:
            unsupported.append(f"custom_fields.{name}")
    return tuple(
        f"UNSUPPORTED_ZEPHYR_XML_MANAGEMENT: case_id={json.dumps(case['case_id'], ensure_ascii=False)}, field={json.dumps(field, ensure_ascii=False)}"
        for field in sorted(unsupported)
    )


def render_zephyr_xml(document: dict[str, Any], profile: str = _XML_PROFILE) -> Projection:
    """Render the narrow, sample-faithful Zephyr XML candidate profile."""
    if profile != _XML_PROFILE:
        raise ValueError(f"unknown Zephyr XML profile: {profile}")
    require_valid_canonical_document(document)
    metadata = document["metadata"]
    warnings: list[str] = []
    rows = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<project>",
        "    <modelVersion>1.0</modelVersion>",
        f"    <exportDate>{_xml_export_date(metadata['date'])}</exportDate>",
        "    <testCases>",
    ]
    for case in document["test_cases"]:
        management = case["management"]
        warnings.extend(_xml_management_warnings(case))
        rows.append("        <testCase>")
        custom_fields = [
            (name, management["custom_fields"][name])
            for name in _CUSTOM_KEYS
            if type(management["custom_fields"].get(name)) is str
        ]
        if not custom_fields:
            rows.append("            <customFields/>")
        else:
            rows.append("            <customFields>")
            for name, value in custom_fields:
                rows.extend((
                    f'                <customField name="{name}" type="SINGLE_CHOICE_SELECT_LIST">',
                    _xml_cdata("value", value, 5),
                    "                </customField>",
                ))
            rows.append("            </customFields>")
        issues = management["external_links"]["issues"]
        if not issues:
            rows.append("            <issues/>")
        else:
            rows.append("            <issues>")
            for issue in issues:
                rows.extend(("                <issue>", _xml_cdata("key", issue, 5), "                </issue>"))
            rows.append("            </issues>")
        rows.extend(_xml_empty_or_values("labels", "label", management["labels"], 3))
        rows.extend((
            _xml_cdata("name", case["title"], 3),
            _xml_cdata("objective", case["objective"], 3),
        ))
        if management["owner"] is not None:
            rows.append(_xml_cdata("owner", management["owner"], 3))
        if case["preconditions"]:
            rows.append(_xml_cdata("precondition", "\n\n".join(case["preconditions"]), 3))
        rows.append(_xml_cdata("priority", _PRIORITIES[case["priority"]], 3))
        if management["status"] is not None:
            rows.append(_xml_cdata("status", management["status"], 3))
        rows.extend(("            <testScript type=\"steps\">", "                <steps>"))
        for index, step in enumerate(case["steps"]):
            expected = "\n\n".join(expectation["text"] for expectation in step["expectations"])
            rows.extend((
                f'                    <step index="{index}">',
                "                        <customFields/>",
                _xml_cdata("description", step["action"], 6),
                _xml_cdata("expectedResult", expected, 6),
                _xml_cdata("testData", step["test_data"], 6),
                "                    </step>",
            ))
        rows.extend(("                </steps>", "            </testScript>", "        </testCase>"))
    rows.extend(("    </testCases>", "</project>"))
    return Projection("\r\n".join(rows).encode("utf-8"), tuple(warnings))


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
            if profile == _PROFILE_V5:
                # Human scenario only: the action with its resolved URL, the authored test data
                # and the expectation prose.  No operation, binding or assertion model.
                expected = "\n\n".join(item["text"] for item in step["expectations"])
                record = list(record) + [_human_action(step, display_orders), step["test_data"], expected, "", ""]
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
