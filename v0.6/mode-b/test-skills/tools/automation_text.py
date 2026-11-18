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


def _key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return row["case_id"], row["step_id"], row.get("assertion_id", "")


def _relations(rows: list[Any], facts: Mapping[tuple[str, str, str], str]) -> str | None:
    """``SYMBOL FILE TC STEP | operation`` for the consecutive rows of a symbol, a case and a step, then ``  EXP`` and ``    ASSERT actual operator expected`` for its assertions
    (the operation and the check as the document has them — *facts*; ``operation`` and none without); the order is the rows'.  An operation row after another row of its head stays JSON."""
    lines: list[str] = []
    head = expectation = None
    for row in rows:
        if set(row) != _ROW.get(row["kind"]) or not all(_ID.match(item) for key, item in row.items() if key != "kind"):
            return None
        new = " ".join(row[name] for name in ("symbol_id", "file_id", "case_id", "step_id"))
        if new != head:
            head, expectation = new, None
            lines.append(new)
        elif row["kind"] == "operation":
            return None
        if row["kind"] == "operation":
            lines[-1] += " | " + facts.get(_key(row), "operation")
        else:
            if row["expectation_id"] != expectation:
                expectation = row["expectation_id"]
                lines.append(f"  {expectation}")
            lines.append(f"    {row['assertion_id']} {facts.get(_key(row), '')}".rstrip())
    return "\n".join(lines)


PART_BYTES, PART_LINES = 60_000, 1_800  # a part of the view of the cases: an agent's read is cut near 25 thousand tokens (about 65 KB of this text) and at 2 000 lines


def _view_parts(stem: str, text: str) -> list[tuple[str, str, bytes]]:
    """The view of the cases in files of at most PART_BYTES and PART_LINES: the head (requirements, capabilities, boundaries) comes first — in as many parts as it needs,
    cut between its items — and a case is never cut.  A part starts with a line that says where it is, where the assertions are and where the legend is; the bodies, joined by a line break, are the view."""
    groups: list[list[str]] = [[]]
    size = lines = 0
    head, *cases = re.split(r"\n(?=### TC-)", text)
    items = [piece for item in re.split(r"\n(?=## |- )", head) for piece in ([item] if len(item.encode("utf-8")) < PART_BYTES - 400 else item.split("\n"))]
    for block in [*items, *cases]:  # the head by its sections and items (an item longer than a part by lines; a line is never cut), a case whole
        weight, height = len(block.encode("utf-8")) + 1, block.count("\n") + 1
        if groups[-1] and (size + weight > PART_BYTES - 400 or lines + height > PART_LINES - 1):  # 400: room of the first line
            groups.append([])
            size = lines = 0
        groups[-1].append(block)
        size, lines = size + weight, lines + height
    parts = []
    for number, group in enumerate(groups, 1):
        cases = [found[1] for block in group if (found := re.match(r"### (TC-\S+)", block))]
        note = (f"часть {number} из {len(groups)}, " + (f"кейсы {cases[0]} … {cases[-1]}" if cases else "голова вида без кейсов")
                + f"; утверждения (ASSERT) — в {stem}.tables.txt рядом с символом; легенда записи — в части 1")
        parts.append((f"{stem}.autotest-review{f'.{number}' if len(groups) > 1 else ''}.md", "вид кейсов", (note + "\n" + "\n".join(group)).encode("utf-8")))
    return parts


def render(artifact: Mapping[str, Any], digest: str, document: Mapping[str, Any] | None = None) -> list[tuple[str, str, bytes]] | None:
    """``(file name, basis, bytes)`` of the files the reviewer reads instead of the JSON of the answer ``digest``; None leaves it whole.
    With the effective *document* the files hold its view of the cases in the short form too (the generator's view is not changed)."""
    stem, body, files = digest.removeprefix("sha256:"), artifact["artifacts"], artifact["artifacts"]["generated_files"]
    names = [f"{stem}.{re.sub(r'[^A-Za-z0-9._-]', '_', row['file_id'])}{re.sub(r'[^A-Za-z0-9.]', '', Path(row['path']).suffix)[:12]}.txt" for row in files]  # .txt: inert text, no file of a language is written before the review
    if len({name.casefold() for name in names}) != len(names):  # a case-blind file system keeps FILE-A and FILE-a as one file
        return None
    rest = {**artifact, "artifacts": {**{key: item for key, item in body.items() if key not in ("generated_symbols", "implementation_relations")},
                                      "generated_files": [{key: item for key, item in row.items() if key != "content"} for row in files]}}
    facts = {} if document is None else review_view.autotest_facts(document)
    relations = _relations(body["implementation_relations"], facts)
    parts = (("символы", _symbols(body["generated_symbols"]), body["generated_symbols"]), ("связи", relations, body["implementation_relations"]))
    tables = "\n".join([f"# Автоматизация r{body['automation_revision']}, id полные; код — файлы {stem}.FILE-… байт в байт; символы: SYMBOL FILE вид класс#метод; связи: SYMBOL FILE TC STEP | "
                        "операция (capability), под ней `  EXP` и `    ASSERT актуальное оператор ожидаемое` — то, чего нет в виде кейсов (запись вида — в его части 1)\n",
                        f"## прочие поля (JSON)\n{_json(rest)}\n",
                        *(f"## {title}\n{text}\n" if text is not None else f"## {title} (JSON)\n{_json(rows)}\n" for title, text, rows in parts)])
    moved = [key for row in body["implementation_relations"] if relations is not None and (key := _key(row)) in facts]  # what the tables hold, the view leaves out
    view = [] if document is None else _view_parts(stem, review_view.render(document, "autotest-review", moved=moved).decode("utf-8"))
    return [*((name, "код", row["content"].encode("utf-8")) for name, row in zip(names, files)), (f"{stem}.tables.txt", "таблицы", tables.encode("utf-8")), *view]
