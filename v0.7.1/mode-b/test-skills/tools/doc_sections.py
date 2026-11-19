"""``--docs путь#Раздел`` — one section of a Markdown analytics file instead of the whole file.

A section is an ATX heading (``# …`` to ``###### …``, outside code fences) and everything up to the next heading of the same or
a higher level.  The model reads a *view* of it: the file with every line outside the section left empty, so the lines of the
section keep the numbers they have in the source file — the provenance ``путь:строка`` and the order check
``SOURCE_REQUIREMENT_ORDER`` depend on them.  Where the section starts below line 1, line 1 names the file and the lines.
"""

from __future__ import annotations

import bisect
import re
from typing import NamedTuple, Sequence

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+")  # the opening of a heading line; the title is cut by hand below: a lazy `(.*?)[ \t]*$` and a `[ \t]+#+$` are n squared on a long run of blanks


def _title_of(rest: str) -> str:
    """The title of a heading: *rest* (what follows the hashes and the blank after them) without trailing blanks and without a closing run of hashes
    that a blank sets off (``Title ##``), its words separated by one space."""
    rest = rest.rstrip(" \t")
    bare = rest.rstrip("#")
    if bare != rest and bare.endswith((" ", "\t")):
        rest = bare
    return " ".join(rest.split())


SECTION_JOIN = " || "  # the headings of several sections of one file in the one ``section`` of an entry


class Section(NamedTuple):
    heading: str
    level: int
    first: int  # 1-based line of the heading
    last: int   # 1-based line of the last line of the section


class SectionMissing(ValueError):
    code = "TASK_DOC_SECTION_MISSING"


def split_spec(raw: str) -> tuple[str, str | None]:
    """``docs/a.md#Раздел`` → ``("docs/a.md", "Раздел")``; without ``#`` the whole file (``None``)."""
    path, found, heading = str(raw).partition("#")
    return (path, " ".join(heading.split()) or None) if found else (str(raw), None)


def headings(text: str) -> list[tuple[int, int, str]]:
    """``(line, level, title)`` of every ATX heading outside code fences, in file order."""
    found: list[tuple[int, int, str]] = []
    fence: str | None = None
    # Notepad saves with a BOM; a heading on the first line is still a heading (the digests stay those of the bytes)
    for number, line in enumerate(text.removeprefix("﻿").splitlines(), start=1):
        opening = _FENCE.match(line)
        if opening:
            mark = opening.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = _HEADING.match(line)
        if match:
            title = _title_of(line[match.end():])
            found.append((number, len(match.group(1)), title))
    return found


def find_section(text: str, heading: str, path: str = "") -> Section:
    """The first heading whose title is *heading* (whitespace-normalised, case-sensitive) with the section it opens."""
    rows = headings(text)
    wanted = " ".join(heading.split())
    for index, (line, level, title) in enumerate(rows):
        if title == wanted:
            last = len(text.splitlines())
            for later_line, later_level, _later in rows[index + 1:]:
                if later_level <= level:
                    last = later_line - 1
                    break
            return Section(title, level, line, last)
    listing = "; ".join(f"{'#' * level} {title} (строка {line})" for line, level, title in rows) or "заголовков нет"
    raise SectionMissing(f"TASK_DOC_SECTION_MISSING: в {path or 'файле'} нет заголовка «{wanted}»; заголовки файла: {listing}; "
                         "fix: --docs путь#Заголовок — точный текст заголовка без # (регистр важен)")


def split_sections(text: str, lines: Sequence[int], cap: int, within: Sequence[Section] = ()) -> list[tuple[str | None, int]] | None:
    """The sections a file (or the sections *within* it) is told apart by, in file order, as ``(heading, numbers)``; *lines* — the sorted 1-based lines that carry a number.
    A section of more than *cap* numbers whose own lines above its first sub-heading carry none gives its sub-sections instead; a section without numbers below another is left out.
    None where headings do not tell the numbers apart: one section only, or a title another heading repeats (``find_section`` would take the first)."""
    rows = headings(text)

    def count(first: int, last: int) -> int:
        return bisect.bisect_right(lines, last) - bisect.bisect_left(lines, first)

    def expand(title: str | None, line: int, level: int, last: int) -> list[tuple[str | None, int, int]]:
        found = count(line, last)
        below = [row for row in rows if line < row[0] <= last and row[1] > level]
        if found <= cap or not below:
            return [(title, found, line)]
        top = min(row[1] for row in below)
        tops = [row for row in below if row[1] == top]
        if count(line, tops[0][0] - 1):
            return [(title, found, line)]
        ends = [row[0] - 1 for row in tops[1:]] + [last]
        return [leaf for (child, _level, name), end in zip(tops, ends) for leaf in expand(name, child, top, end) if leaf[1]]

    roots = [(item.heading, item.first, item.level, item.last) for item in within] or [(None, 0, 0, len(text.splitlines()))]
    leaves = [leaf for root in roots for leaf in expand(*root)]
    if (not within and len(leaves) < 2) or any(title is not None and find_section(text, title).first != line for title, _n, line in leaves):
        return None
    return [(title, found) for title, found, _line in leaves]


def view(text: str, section: Section | Sequence[Section], path: str, *, note: bool = True) -> str:
    """The file with the lines outside *section* (one, or several of one file) empty: the lines of the section keep their source numbers.  ``note`` puts
    on the empty line 1 what the file is (for the model); without it the text is the section alone (for the code's inventory)."""
    sections = [section] if isinstance(section, Section) else sorted(section, key=lambda item: item.first)
    lines = text.splitlines()
    out = [""] * len(lines)
    for item in sections:
        out[item.first - 1:item.last] = lines[item.first - 1:item.last]
    if note and sections[0].first > 1:
        names = ", ".join(f"«{item.heading}»" for item in sections)
        out[0] = (f"<!-- {'раздел' if len(sections) == 1 else 'разделы'} {names} файла {path}, строки {', '.join(f'{item.first}–{item.last}' for item in sections)}; "
                  "остальные строки пусты, номера строк исходные -->")
    return "\n".join(out) + "\n"


def resolve_spec(project, raw: str) -> tuple[str, str | None]:
    """The path and the heading of a ``--docs`` value: a file that really has ``#`` in its name stays a whole file."""
    from pathlib import Path

    from tools.output_guard import local_path_text

    candidate = Path(raw)
    if "#" not in raw or (local_path_text(raw) and (candidate if candidate.is_absolute() else Path(project) / candidate).is_file()):
        return str(raw), None
    return split_spec(raw)


def view_name(path: str, heading: str | Sequence[str]) -> str:
    """The file name of the view of the sections of a file inside ``<run>/doc-sections/`` (one view per path and headings; one heading — as it always was)."""
    import hashlib

    names = [heading] if isinstance(heading, str) else list(heading)
    return hashlib.sha256((f"{path}#" + "#".join(" ".join(name.split()) for name in names)).encode("utf-8")).hexdigest()[:16] + ".md"
