"""The answer of ``tc-to-autotest`` as the reviewer reads it: the code of each file as a file of its own, byte for byte (its sha256 is the
``content_digest``); the symbols and the relations as tables of whole ids, the other fields as JSON; nothing dropped, no id shortened.  A part with
a row the tables do not hold stays JSON; an answer whose files would get one name stays whole (None).  Pure."""
from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path
from typing import Any, Mapping

from tools import review_view

_ID = re.compile(r"[^\s|=#]+\Z")  # an id a table line can hold
_LOCATORS = {"java_class_method": ("class_fqn", "method_name"), "python_module_function": ("function_name",), "python_class_method": ("qualified_class_name", "method_name")}
_ROW = {"operation": {"kind", "case_id", "step_id", "file_id", "symbol_id"}, "assertion": {"kind", "case_id", "step_id", "file_id", "symbol_id", "expectation_id", "assertion_id"}}
_json = partial(json.dumps, ensure_ascii=False, separators=(",", ":"))


def _symbols(rows: list[Any]) -> str | None:
    lines = []
    for row in rows:
        locator, names = row["locator"], _LOCATORS.get(row["locator"].get("kind"))
        ids = [row["symbol_id"], row["file_id"], locator["kind"], *(locator[name] for name in names or ())]
        if names is None or set(row) != {"file_id", "symbol_id", "locator"} or set(locator) != {"kind", *names} or not all(_ID.match(item) for item in ids):
            return None
        lines.append(" ".join(ids[:3]) + " " + "#".join(ids[3:]))
    return "\n".join(lines)


def _relations(rows: list[Any]) -> str | None:
    """One line for the consecutive rows of a symbol, a case and a step: ``SYMBOL FILE TC STEP | operation | EXP = ASSERT ASSERT``; the order is the rows'."""
    lines: list[str] = []
    head = expectation = None
    for row in rows:
        if set(row) != _ROW.get(row["kind"]) or not all(_ID.match(item) for key, item in row.items() if key != "kind"):
            return None
        new = " ".join(row[name] for name in ("symbol_id", "file_id", "case_id", "step_id"))
        if new != head:
            head, expectation = new, None
            lines.append(new)
        if row["kind"] == "operation":
            lines[-1], expectation = lines[-1] + " | operation", None
        elif row["expectation_id"] == expectation:
            lines[-1] += " " + row["assertion_id"]
        else:
            lines[-1], expectation = lines[-1] + f" | {row['expectation_id']} = {row['assertion_id']}", row["expectation_id"]
    return "\n".join(lines)


def render(artifact: Mapping[str, Any], digest: str, document: Mapping[str, Any] | None = None) -> list[tuple[str, str, bytes]] | None:
    """``(file name, basis, bytes)`` of the files the reviewer reads instead of the JSON of the answer ``digest``; None leaves it whole.
    With the effective *document* the files hold its view of the cases in the short form too (the generator's view is not changed)."""
    stem, body, files = digest.removeprefix("sha256:"), artifact["artifacts"], artifact["artifacts"]["generated_files"]
    names = [f"{stem}.{re.sub(r'[^A-Za-z0-9._-]', '_', row['file_id'])}{re.sub(r'[^A-Za-z0-9.]', '', Path(row['path']).suffix)[:12]}.txt" for row in files]  # .txt: inert text, no file of a language is written before the review
    if len({name.casefold() for name in names}) != len(names):  # a case-blind file system keeps FILE-A and FILE-a as one file
        return None
    rest = {**artifact, "artifacts": {**{key: item for key, item in body.items() if key not in ("generated_symbols", "implementation_relations")},
                                      "generated_files": [{key: item for key, item in row.items() if key != "content"} for row in files]}}
    parts = (("символы", _symbols(body["generated_symbols"]), body["generated_symbols"]), ("связи", _relations(body["implementation_relations"]), body["implementation_relations"]))
    tables = "\n".join([f"# Автоматизация r{body['automation_revision']}, id полные; код — файлы {stem}.FILE-… байт в байт; символы: SYMBOL FILE вид класс#метод; "
                        "связи: SYMBOL FILE TC STEP | operation | EXP = ASSERT …\n", f"## прочие поля (JSON)\n{_json(rest)}\n",
                        *(f"## {title}\n{text}\n" if text is not None else f"## {title} (JSON)\n{_json(rows)}\n" for title, text, rows in parts)])
    view = [] if document is None else [(f"{stem}.autotest-review.md", "вид кейсов", review_view.render(document, "autotest-review"))]
    return [*((name, "код", row["content"].encode("utf-8")) for name, row in zip(names, files)), (f"{stem}.tables.txt", "таблицы", tables.encode("utf-8")), *view]
