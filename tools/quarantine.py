"""Quarantine of individual test methods (wave 3, F; contract amendment A4).

The package marks only its own failed methods, one line each, directly above the method:

* JUnit 5 — ``@org.junit.jupiter.api.Disabled("test-skills quarantine: <REASON> <ref>")`` (fully
  qualified, so no import changes);
* pytest — ``@pytest.mark.xfail(strict=True, reason="test-skills quarantine: <REASON> <ref>")``
  (``import pytest`` is added when the module has none).

``release`` removes exactly those lines.  A quarantined method is still run explicitly on the next
suite run (``EXPLICIT_RUN``), so a fixed defect shows up as a pass.  Nothing else in the file changes.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

from tools.code_slices import SliceError, slice_file

MARK = "test-skills quarantine"
EXPLICIT_RUN = {"junit5": "-Djunit.jupiter.conditions.deactivate=org.junit.*DisabledCondition", "pytest": "--runxfail"}
_JAVA_LINE = re.compile(r'^\s*@org\.junit\.jupiter\.api\.Disabled\("' + re.escape(MARK) + r':[^\n]*\)\s*$')
_PYTHON_LINE = re.compile(r'^\s*@pytest\.mark\.xfail\(strict=True, reason="' + re.escape(MARK) + r':[^\n]*\)\s*$')
_SAFE = re.compile(r"[^A-Za-z0-9 ._:/#@=+-]")


def _note(reason: str, ref: str) -> str:
    """The mark's text: only characters that need no escaping in a Java or Python string."""
    return f"{MARK}: {_SAFE.sub('_', reason)} {_SAFE.sub('_', ref)}".strip()


def _split(content: str) -> tuple[list[str], str, bool]:
    newline = "\r\n" if "\r\n" in content else "\n"
    trailing = content.endswith(("\n",))
    lines = content.replace("\r\n", "\n").split("\n")
    if trailing:
        lines = lines[:-1]
    return lines, newline, trailing


def _join(lines: Sequence[str], newline: str, trailing: bool) -> str:
    return newline.join(lines) + (newline if trailing else "")


def _members(path: str, content: str, locators: Sequence[Mapping[str, object]]):
    symbols = [{"symbol_id": f"Q{index}", "locator": dict(locator)} for index, locator in enumerate(locators)]
    language = "python" if path.endswith(".py") else "java"
    slices = slice_file({"file_id": "quarantine", "path": path, "content": content.replace("\r\n", "\n"), "language": language}, symbols)
    return [slices.symbols[f"Q{index}"] for index in range(len(locators))], language


def is_marked(line: str) -> bool:
    return bool(_JAVA_LINE.match(line) or _PYTHON_LINE.match(line))


def quarantine(path: str, content: str, methods: Iterable[tuple[Mapping[str, object], str, str]]) -> str:
    """Mark each ``(locator, reason, ref)`` method; an already marked method keeps its mark."""
    methods = list(methods)
    if not methods:
        return content
    lines, newline, trailing = _split(content)
    members, language = _members(path, content, [locator for locator, _reason, _ref in methods])
    inserts = []
    for member, (_locator, reason, ref) in zip(members, methods):
        first = lines[member.start - 1]
        if member.start >= 2 and is_marked(lines[member.start - 2]) or any(is_marked(line) for line in lines[member.start - 1:member.end]):
            continue
        indent = first[:len(first) - len(first.lstrip())]
        text = f'@org.junit.jupiter.api.Disabled("{_note(reason, ref)}")' if language == "java" else f'@pytest.mark.xfail(strict=True, reason="{_note(reason, ref)}")'
        inserts.append((member.start - 1, indent + text))
    for index, line in sorted(inserts, reverse=True):
        lines.insert(index, line)
    if language == "python" and inserts and not any(re.match(r"^\s*(?:import pytest\b|from pytest\b)", line) for line in lines):
        lines.insert(_python_import_line(lines), "import pytest")
    return _join(lines, newline, trailing)


def _python_import_line(lines: Sequence[str]) -> int:
    """After the module docstring and ``from __future__`` imports."""
    index = 0
    if lines and re.match(r'^\s*[rRuU]?("""|\'\'\')', lines[0]):
        quote = re.match(r'^\s*[rRuU]?("""|\'\'\')', lines[0]).group(1)
        if lines[0].count(quote) >= 2:
            index = 1
        else:
            index = next((number + 1 for number in range(1, len(lines)) if quote in lines[number]), 1)
    while index < len(lines) and re.match(r"^\s*from __future__ import", lines[index]):
        index += 1
    return index


def release(path: str, content: str, locators: Sequence[Mapping[str, object]]) -> str:
    """Remove the package's mark above each method (a mark a person wrote differently stays)."""
    lines, newline, trailing = _split(content)
    try:
        members, _language = _members(path, content, locators)
    except SliceError:
        return content
    drop = set()
    for member in members:
        for number in range(member.start - 1, member.end):
            if is_marked(lines[number]):
                drop.add(number)
        if member.start >= 2 and is_marked(lines[member.start - 2]):
            drop.add(member.start - 2)
    return _join([line for number, line in enumerate(lines) if number not in drop], newline, trailing)


def marked(path: str, content: str, locators: Sequence[Mapping[str, object]]) -> list[bool]:
    """Whether each method carries the package's quarantine mark."""
    lines, _newline, _trailing = _split(content)
    members, _language = _members(path, content, locators)
    return [any(is_marked(line) for line in lines[max(0, member.start - 2):member.end]) for member in members]
