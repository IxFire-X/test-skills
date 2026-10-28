"""FaT-Z5: one reader of a Zephyr Scale step-row CSV — the inverse of ``render_zephyr_csv`` for the human fields.

``parse_zephyr_csv(bytes)`` returns the cases of the CSV with their human text byte for byte: a row with ``Name`` opens
a case, the rows below without ``Name`` are its further steps.  The v5 profile (and a plain Zephyr export) has only
human text in the step cells; a v4 CSV of the package carries its machine model there and is read as it is.  The only
change to a value is the ``'`` the projection put before a formula start.  An unknown column, a step before the first
case or a case without steps is refused by name — never guessed.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable

from tools.test_case_projections import _CUSTOM_KEYS, _HEADERS, _PRIORITIES

_STEP, _DATA, _EXPECTED = (
    "Test Script (Step-by-Step) - Step", "Test Script (Step-by-Step) - Test Data", "Test Script (Step-by-Step) - Expected Result")
_PRIORITY = {"High": "HIGH", "Normal": "MEDIUM", "Low": "LOW"}
assert set(_PRIORITY) == set(_PRIORITIES.values())


class ZephyrCsvError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def detect_delimiter(text: str) -> str:
    """``,`` or ``;`` — the one under which the header has more Zephyr columns; a tie or no CSV is a ``ValueError``."""
    first = text.splitlines()[0] if text.strip() else ""
    splits = {delimiter: next(csv.reader([first], delimiter=delimiter), []) for delimiter in (",", ";")}
    if max(len(cells) for cells in splits.values()) < 2:
        raise ValueError("ZEPHYR_CSV_NOT_CSV")
    known = {delimiter: len({cell.strip() for cell in cells} & set(_HEADERS)) for delimiter, cells in splits.items()}
    if known[","] == known[";"]:
        raise ValueError("ZEPHYR_CSV_DELIMITER")
    return max(known, key=known.__getitem__)


def _unformula(value: str) -> str:
    """Undo ``_formula_safe``: the ``'`` before a formula start or before whitespace-only text with a line break."""
    if not value.startswith("'"):
        return value
    rest = value[1:]
    stripped = rest.lstrip(" \t\r\n")
    if stripped and stripped[0] in "=+-@" or not stripped and any(character in "\t\r\n" for character in rest):
        return rest
    return value


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_zephyr_csv(data: bytes) -> list[dict[str, Any]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ZephyrCsvError("ZEPHYR_CSV_ENCODING", "сохраните CSV в UTF-8 (Excel: Файл → Сохранить как → CSV UTF-8)") from None
    try:
        delimiter = detect_delimiter(text)
    except ValueError as error:
        raise ZephyrCsvError(str(error), "нужен экспорт тест-кейсов Zephyr Scale в CSV с разделителем «,» или «;»") from None
    records = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    header = [cell.strip() for cell in records[0]]
    unknown = [cell for cell in header if cell not in _HEADERS]
    if unknown or "Name" not in header or _STEP not in header:
        missing = [name for name in ("Name", _STEP) if name not in header]
        raise ZephyrCsvError("ZEPHYR_CSV_COLUMNS", "незнакомые колонки: " + ", ".join(unknown) if unknown else "нет колонок: " + ", ".join(missing))
    cases: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue
        row = {column: _unformula(record[index]) if index < len(record) else "" for index, column in enumerate(header)}
        if row["Name"].strip():
            priority = row.get("Priority", "")
            if priority and priority not in _PRIORITY:
                errors.append(f"строка {number}: приоритет «{priority}» не из High/Normal/Low")
            cases.append({
                "row": number, "key": row.get("Key", ""), "title": row["Name"], "objective": row.get("Objective", ""),
                "preconditions": row.get("Precondition", ""), "priority": _PRIORITY.get(priority),
                "management": {
                    "status": row.get("Status") or None, "folder": row.get("Folder") or None, "owner": row.get("Owner") or None,
                    "estimated_time": row.get("Estimated Time") or None, "components": _split(row.get("Component", "")),
                    "labels": _split(row.get("Labels", "")), "issues": _split(row.get("Coverage (Issues)", "")),
                    "pages": _split(row.get("Coverage (Pages)", "")),
                    "custom_fields": {key: row[key] for key in _CUSTOM_KEYS if row.get(key)},
                },
                "steps": [],
            })
        if any(row.get(column, "") for column in (_STEP, _DATA, _EXPECTED)):
            if not cases:
                errors.append(f"строка {number}: шаг до первого кейса (строки с Name)")
                continue
            cases[-1]["steps"].append({"row": number, "action": row[_STEP], "test_data": row.get(_DATA, ""), "expected": row.get(_EXPECTED, "")})
    errors += [f"строка {case['row']}: у кейса «{case['title']}» нет шагов" for case in cases if not case["steps"]]
    if errors:
        raise ZephyrCsvError("ZEPHYR_CSV_ROWS", "; ".join(errors))
    return cases


# FaT-REF: the first line the package's v4 profile puts into Test Data (``test_case_projections._operation_text``)
_V4_FIRST_LINE = re.compile(r"^(?:(Операция:) не определена$|(HTTP:) [A-Z]+ \S.*; базовый URL: |(Действие проекта:) \S+; адаптер: )")


def own_v4_marker(cells: Iterable[str]) -> str | None:
    """FaT-REF: the marker of a CSV in the package's own v4 profile — the machine part as the first line of a Test Data
    cell («Операция:», «HTTP:», «Действие проекта:»); None for a v5 or a plain Zephyr export.  One check for REF and IMP."""
    for cell in cells:
        match = _V4_FIRST_LINE.match(str(cell).split("\n", 1)[0])
        if match:
            return next(group for group in match.groups() if group)
    return None


def require_human_csv(cases: Iterable[dict[str, Any]]) -> None:
    """FaT-REF/IMP: refuse a CSV of the package's v4 profile — its Test Data is the machine part, not the human data."""
    rows = [(step["row"], str(step["test_data"]).split("\n", 1)[0]) for case in cases for step in case["steps"]
            if own_v4_marker([step["test_data"]]) is not None]
    if rows:
        raise ZephyrCsvError(
            "IMPORT_CSV_V4",
            "это выгрузка пакета в профиле v4 — распознано по первой строке Test Data, она машинная ("
            + "; ".join(f"строка {row}: «{line}»" for row, line in rows[:3]) + ")",
        )
