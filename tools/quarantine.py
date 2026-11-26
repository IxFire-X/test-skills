"""Marking of the failed test methods as findings (wave 3, F; contract amendment A4; q1).

A test that failed by the requirement is a finding, not a quarantined test: it stays active, and the package only labels it, one line
directly above the method, so the human can leave the label out of the build with one option:

* JUnit 5 — ``@org.junit.jupiter.api.Tag("test-skills-finding") // test-skills finding: <ref> <note>`` (fully qualified, no import changes);
* pytest — ``@pytest.mark.test_skills_finding  # test-skills finding: <ref> <note>`` (``import pytest`` is added when the module has none).

A file of an earlier run may still carry the old line (``@Disabled("test-skills quarantine: …")`` / strict ``xfail``): it is recognised, not doubled.
Nothing else in the file changes.
"""
from __future__ import annotations

import collections
import re
from typing import Any, Iterable, Mapping, Sequence

from tools.code_slices import slice_file

TAG = "test-skills-finding"
MARK = "test-skills finding"
_OLD = re.escape("test-skills quarantine")
_JAVA_LINE = re.compile(r'^\s*(?:@org\.junit\.jupiter\.api\.Tag\("' + TAG + r'"\) // ' + re.escape(MARK) + r':[^\n]*|'
                        r'@org\.junit\.jupiter\.api\.Disabled\("' + _OLD + r':[^\n]*\))\s*$')
_PYTHON_LINE = re.compile(r'^\s*(?:@pytest\.mark\.test_skills_finding  # ' + re.escape(MARK) + r':[^\n]*|'
                          r'@pytest\.mark\.xfail\(strict=True, reason="' + _OLD + r':[^\n]*\))\s*$')
_SAFE = re.compile(r"[^A-Za-z0-9 ._:/#@=+-]")


_ASSERT_REF = re.compile(r"ASSERT-[A-Za-z0-9_.:-]+")
NOTE_LIMIT = 120
_SEVERAL = re.compile(r"\(\d+ failures?\)$")  # the head of several failures: JUnit assertAll and AssertJ soft assertions end it with «(N failures)»
_EXCEPTION_CLASS = re.compile(r"^[\w.$]+(?:Error|Exception): ")
# what ends the lines of one check in a failure of several: the next failure of AssertJ, an exception (the next check of JUnit) or a frame
_CHECK_END = re.compile(r"^(?:-- failure \d+ --|[\w.$]+(?:Error|Exception)(?::|$)|at [\w.$<>]+\()")


def failure_line(text: str) -> str:
    """The line of a framework failure that names it: the first one; for several failures (whose head names no check) the line of the
    first ``ASSERT-…`` label, without its indent and the class of its exception, with the lines of that check after it («expected: 201»,
    «but was: 403») joined by a space — up to the next label, ``-- failure N --``, an exception or a frame of the stack."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if lines and _SEVERAL.search(lines[0]):
        start = next((index for index, line in enumerate(lines) if index and _ASSERT_REF.search(line)), None)
        if start is not None:
            rest = []
            for line in lines[start + 1:]:
                if _CHECK_END.match(line) or _ASSERT_REF.search(line):
                    break
                rest.append(line)
            return " ".join([_EXCEPTION_CLASS.sub("", lines[start]), *rest])
    return lines[0] if lines else ""


def failure_note(message: str) -> tuple[str, str]:
    """``(ref, message)`` of a framework failure — the ``ASSERT-…`` label or ``FAIL``, and its first line made safe."""
    from tools.run_tests import masked_failure_text  # the same check as the receipt: a secret is not written into the source file

    first = masked_failure_text((message or "").strip().splitlines()[0].strip()) if (message or "").strip() else ""
    match = _ASSERT_REF.search(first)
    if match and match.start() == int(first.startswith("[")):  # AssertJ writes the label of ``as(…)`` in brackets
        # The mark already shows the ref, so the note does not repeat it.
        first = first[match.end():].lstrip(" :-]")
    return (match.group(0) if match else "FAIL"), _SAFE.sub("_", first)[:NOTE_LIMIT].rstrip()


# --- the causes of the failures (handoff/v08/failure-groups-design.md) --------------------------------------------------------------

# rule 2: the code of the answer, the expected and the actual one (``<201>`` of JUnit, ``_201_`` of a note); rule 3: the class of an error
_STATUS = re.compile(r"expected:?\s*[<_]?\s*([1-5]\d\d)\s*[>_]?\s*but was:?\s*[<_]?\s*([1-5]\d\d)(?!\d)", re.IGNORECASE)
_EXCEPTION = re.compile(r"\b([A-Z]\w*(?:Exception|Error))\b")
_NOT_A_CAUSE = frozenset({"MultipleFailuresError", "AssertionFailedError", "AssertionError"})  # the wrappers of the checks themselves
_HEAD = re.compile(r"[(_]\d+ failures?[)_]$")  # the head of several failures that names no check, as the report wrote it or as a note
_LABEL = re.compile(r"^(?:\[[^\]]*\]|ASSERT-[A-Za-z0-9_.:-]+)\s*")  # the label of the check: the mark has it, the cause is what follows
_PLACE = re.compile(r" at [\w.$<>]+\S*$")  # the place of the check, not its cause
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_LITERAL = re.compile(r""""[^"]*"|'[^']*'|<[^<>]*>""")  # a literal as AssertJ ("…"), pytest ('…') and JUnit (<…>) write it
CAUSE_LIMIT = 80
CAUSE_RULES = ("status", "exception")  # the rules that name a cause (the code of the answer, the class); a text is a kind of mismatch
ONE_CAUSE_MIN = 5  # «one cause» of the summary: at least this many methods and at least half of the failed ones
# no version number in the words: it lives in the contract only (tests/test_pack_profile.py)
UNKNOWN_CAUSE = "текст падения не сохранён (прогон старой версии пакета или проверка без текста)"


def _literal(match: re.Match[str]) -> str:
    """A literal of the test data never leaves for the report: a date is ``"<дата>"`` (a shift of a day stays one cause), the rest
    ``"…"``, in the quotes it had; the empty one and the ``<redacted>`` of the mask hold no data."""
    value = match.group(0)
    inner, close = value[1:-1], value[-1]
    if not inner or value == "<redacted>":
        return value
    return "<дата>" if close == ">" and _DATE.fullmatch(inner) else f"{value[0]}<дата>{close}" if _DATE.fullmatch(inner) else f"{value[0]}…{close}"


def failure_cause(kind: str, text: str, whole: bool) -> tuple[str, str, str | None]:
    """``(rule, key, expected code)`` of one failure, the first rule that fits: ``status`` — the actual code of the answer (the key)
    and the expected one; ``exception`` — the last class of an error; ``text`` — the text without the literals, the dates, the place
    of the check and long numbers; ``unknown`` — no text.  *whole*: the text is the one before the note, its quotes whole; a note has
    its quotes as ``_`` already, a literal could not be cut out of it, so it gives the code and the class only."""
    text = _LABEL.sub("", (text or "").strip())
    status = _STATUS.search(text)
    if status:
        return "status", status.group(2), status.group(1)
    found = [name for name in _EXCEPTION.findall(text) if name not in _NOT_A_CAUSE]
    if kind == "error" and found:
        return "exception", found[-1], None
    if not whole or not text or _HEAD.search(text):
        return "unknown", "", None
    text = text.splitlines()[0].strip()  # the check, not a frame of the stack after it (a text not cut by failure_line)
    text = _DATE.sub("<дата>", _LITERAL.sub(_literal, _PLACE.sub("", text)))
    return "text", re.sub(r"\d{4,}", "#", text)[:CAUSE_LIMIT].rstrip(), None


def failure_texts(execution: Mapping[str, Any], automation: Mapping[str, Any] | None) -> dict[tuple[str, str], str]:
    """``(path, method) -> text`` of each failure before its note: ``failed_methods[].message`` of the execution receipt (masked, the
    quotes whole), tied to its method by the symbols of the automation; nothing when the automation is not the one the run executed."""
    from tools.automation_validation import automation_sha256
    from tools.generated_delta import method_name

    try:
        if not automation or automation_sha256(automation) != execution.get("automation_sha256"):
            return {}
        artifacts = automation["artifacts"]
        paths = {row["file_id"]: row["path"] for row in artifacts["generated_files"]}
        names = {f"{row['file_id']}::{row['symbol_id']}": (paths[row["file_id"]], method_name(row["locator"])) for row in artifacts["generated_symbols"]}
    except (KeyError, TypeError, ValueError):
        return {}
    return {names[row["nodeid"]]: str(row.get("message") or "") for row in execution.get("failed_methods") or []
            if isinstance(row, Mapping) and row.get("nodeid") in names}


def failure_groups(rows: Sequence[Mapping[str, Any]], texts: Mapping[tuple[str, str], str] | None = None) -> list[dict[str, Any]]:
    """The failed methods of the findings (``file, method, kind, message``) grouped by cause, the biggest group first:
    ``{"cause", "rule", "count", "methods"}``.  A status groups by the actual code, whatever was expected («all answered 403» is one
    cause).  The text of a method comes from *texts* (``failure_texts``); without it the note decides by the code and the class only."""
    texts = texts or {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        whole = texts.get((str(row.get("file")), str(row.get("method"))))
        rule, key, expected = failure_cause(str(row.get("kind") or ""), str(row.get("message") or "") if whole is None else whole, whole is not None)
        group = groups.setdefault((rule, key), {"rule": rule, "key": key, "expected": collections.Counter(), "methods": []})
        group["methods"].append(str(row.get("method")))
        if expected:
            group["expected"][expected] += 1
    found = []
    for group in groups.values():
        expected = ", ".join(f"{code} ({count})" for code, count in sorted(group["expected"].items(), key=lambda item: (-item[1], item[0])))
        cause = {"status": f"ответ {group['key']} вместо ожидаемого {expected}", "exception": f"исключение {group['key']}",
                 "text": group["key"]}.get(group["rule"], UNKNOWN_CAUSE)
        found.append({"cause": cause, "rule": group["rule"], "count": len(group["methods"]), "methods": group["methods"]})
    return sorted(found, key=lambda group: -group["count"])  # stable: among equal groups the first seen stays first


def one_cause(groups: Sequence[Mapping[str, Any]], failed: int) -> Mapping[str, Any] | None:
    """The group of the summary line: the biggest group of a rule that names a cause, when it holds at least ``ONE_CAUSE_MIN``
    methods and at least half of the *failed* ones; a text without its literals is a kind of mismatch, not one cause (R7, R8)."""
    top = next((group for group in groups if group["rule"] in CAUSE_RULES), None)
    return top if top and top["count"] >= ONE_CAUSE_MIN and 2 * top["count"] >= failed else None


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
    """Label each ``(locator, reason, ref)`` method as a finding; an already marked method keeps its mark."""
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
        text = (f'@org.junit.jupiter.api.Tag("{TAG}") // {_note(reason, ref)}' if language == "java"
                else f"@pytest.mark.test_skills_finding  # {_note(reason, ref)}")
        inserts.append((member.start - 1, indent + text))
    for index, line in sorted(inserts, reverse=True):
        lines.insert(index, line)
    if language == "python" and inserts and not any(_imports_pytest(line) for line in lines):
        lines.insert(_python_import_line(lines), "import pytest")
    return _join(lines, newline, trailing)


def _imports_pytest(line: str) -> bool:
    """A top-level ``import pytest`` that binds the name ``pytest`` (not ``from pytest import …``, not ``as pt``)."""
    match = re.match(r"^import\s+(.+?)\s*(?:#.*)?$", line)
    return bool(match) and any(re.fullmatch(r"pytest", item.strip()) for item in match.group(1).split(","))


def _python_import_line(lines: Sequence[str]) -> int:
    """The line after the module's leading comments, docstring and ``from __future__`` imports (review 2.1 item 17)."""
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index += 1
    opening = re.match(r'^\s*[rRuUbB]{0,2}("""|\'\'\'|"|\')', lines[index]) if index < len(lines) else None
    if opening:
        quote = opening.group(1)
        rest = lines[index][opening.end():]
        if len(quote) == 1 or quote in rest:
            index += 1
        else:
            index = next((number + 1 for number in range(index + 1, len(lines)) if quote in lines[number]), index + 1)
    after = index
    cursor = index
    while cursor < len(lines):
        line = lines[cursor].strip()
        if not line or line.startswith("#"):
            cursor += 1
            continue
        if re.match(r"from\s+__future__\s+import\b", line):
            if "(" in line and ")" not in line:
                while cursor < len(lines) and ")" not in lines[cursor]:
                    cursor += 1
            cursor += 1
            after = cursor
            continue
        break
    return after