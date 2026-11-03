"""FaT-P3: ``--docs путь#Раздел`` — one section of a Markdown analytics file instead of the whole file.

A section is an ATX heading (``# …`` to ``###### …``, outside code fences) and everything up to the next heading of the same or
a higher level.  The model reads a *view* of it: the file with every line outside the section left empty, so the lines of the
section keep the numbers they have in the source file — the provenance ``путь:строка`` and the order check
``SOURCE_REQUIREMENT_ORDER`` depend on them.  Where the section starts below line 1, line 1 names the file and the lines.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_CLOSING = re.compile(r"[ \t]+#+$")


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
    for number, line in enumerate(text.splitlines(), start=1):
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
            title = " ".join(_CLOSING.sub("", match.group(2)).split())
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


def view(text: str, section: Section, path: str, *, note: bool = True) -> str:
    """The file with the lines outside *section* empty: the lines of the section keep their source numbers.  ``note`` puts
    on the empty line 1 what the file is (for the model); without it the text is the section alone (for the code's inventory)."""
    lines = text.splitlines()
    out = [""] * len(lines)
    out[section.first - 1:section.last] = lines[section.first - 1:section.last]
    if note and section.first > 1:
        out[0] = f"<!-- раздел «{section.heading}» файла {path}, строки {section.first}–{section.last}; остальные строки пусты, номера строк исходные -->"
    return "\n".join(out) + "\n"


def resolve_spec(project, raw: str) -> tuple[str, str | None]:
    """The path and the heading of a ``--docs`` value: a file that really has ``#`` in its name stays a whole file."""
    from pathlib import Path

    candidate = Path(raw)
    if "#" not in raw or (candidate if candidate.is_absolute() else Path(project) / candidate).is_file():
        return str(raw), None
    return split_spec(raw)


def view_name(path: str, heading: str) -> str:
    """The file name of the view of a section inside ``<run>/doc-sections/`` (one view per path and heading)."""
    import hashlib

    return hashlib.sha256(f"{path}#{' '.join(heading.split())}".encode("utf-8")).hexdigest()[:16] + ".md"
