"""Deterministic human Markdown and fixed-profile Zephyr CSV projections."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from tools.canonical_document import require_valid_canonical_document


_PROFILE = "zephyr-scale-step-row-24-v1"
_CUSTOM_KEYS = ("АС", "Автоматизирован", "Вид тестирования", "Команда", "Приоритет теста", "Статус")
_PRIORITIES = {"CRITICAL": "Highest", "HIGH": "High", "MEDIUM": "Normal", "LOW": "Low"}
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


def _markdown_subject(subject: dict[str, Any]) -> str:
    if subject["kind"] == "http_endpoint":
        return f"# Тест-кейсы метода {escape_inline(subject['method'])} {escape_inline(subject['path'])}"
    return f"# Тест-кейсы: {escape_inline(subject['name'])}"


def render_markdown(document: dict[str, Any]) -> Projection:
    """Project a validated canonical test document to exact human Markdown bytes."""
    require_valid_canonical_document(document)
    return _render_markdown_validated(document)


def _render_markdown_validated(document: dict[str, Any]) -> Projection:
    """Render Markdown after the public facade has accepted the canonical document."""
    metadata = document["metadata"]
    documentation = metadata["documentation"]
    warnings: tuple[str, ...] = ()
    if documentation:
        rendered_documentation = "<br>".join(escape_inline(item) for item in documentation)
    else:
        rendered_documentation = "Не предоставлена"
        warnings = ('MISSING_DOCUMENTATION: metadata.documentation is empty; rendered as "Не предоставлена"',)

    lines = [
        _markdown_subject(metadata["subject"]), "",
        f"**Документация:** {rendered_documentation}",
        f"**Project:** {escape_inline(metadata['project'])}",
        f"**Автор:** {escape_inline(metadata['author'])}",
        f"**Дата:** {escape_inline(metadata['date'])}",
    ]
    for case_number, case in enumerate(document["test_cases"], 1):
        lines.extend(["", "---", "", f"## ТК-{case_number}. {escape_inline(case['title'])}", ""])
        lines.extend([f"**Цель:** {escape_inline(case['objective'])}", "", "**Предусловия:**"])
        if case["preconditions"]:
            lines.extend("- " + escape_inline(item) for item in case["preconditions"])
        else:
            lines.append("- Не требуются.")
        lines.extend(["", "**Шаги:**", "", "| № | Действие | Ожидаемый результат |", "|---|---|---|"])
        for step_number, step in enumerate(case["steps"], 1):
            expectations = "<br>".join(escape_inline(item["text"]) for item in step["expectations"])
            lines.append(f"| {step_number} | {escape_inline(step['action'])} | {expectations} |")
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
    """Project a validated document to the sole fixed Zephyr Scale step-row profile."""
    if profile != _PROFILE:
        raise ValueError(f"unknown Zephyr CSV profile: {profile}")
    require_valid_canonical_document(document)
    return _render_zephyr_csv_validated(document)


def _render_zephyr_csv_validated(document: dict[str, Any]) -> Projection:
    """Render the fixed CSV profile after the public facade has validated input."""
    records: list[list[Any]] = [list(_HEADERS)]
    warnings: list[str] = []
    for case in document["test_cases"]:
        warnings.extend(_custom_field_warnings(case))
        display_orders = {step["step_id"]: step["display_order"] for step in case["steps"]}
        metadata = _case_metadata(case)
        for step_index, step in enumerate(case["steps"]):
            record = metadata if step_index == 0 else [""] * 19
            record = list(record) + [
                step["action"], _step_data(step, display_orders),
                "\n".join(item["text"] for item in step["expectations"]), "", "",
            ]
            records.append([_formula_safe(value) for value in record])
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=",", quotechar='"', lineterminator="\r\n")
    writer.writerows(records)
    return Projection(output.getvalue().encode("utf-8-sig"), tuple(warnings))
