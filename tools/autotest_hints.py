"""FaT-Q: code hints on weak assertions in generated tests (Java and pytest) — never a stop, never a rewrite.

Reads the inline ``content`` of every generated file of a ``tc-to-autotest`` artifact with plain regular expressions,
without a compiler: an assertion that accepts any exception, one that is always true, a path prefix instead of an
exact value.  A hint names the file ID, the line, a short rule and the ASSERT ID on that line — never the code.

``python -m tools.autotest_hints --automation <artifact.json>`` prints the hints as JSON for the static reviewer;
the run report counts them on its own from the run's automation artifact.
"""

from __future__ import annotations

import bisect
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Mapping

from tools.json_cli import JsonArgumentParser, emit_error
from tools.test_design_hints import capped

CODES = ("AT_ANY_EXCEPTION", "AT_ALWAYS_TRUE", "AT_PARTIAL_MATCH")
_ASSERT_ID = re.compile(r"ASSERT-[A-Za-z0-9_.:-]+")
_BROAD = {"Exception", "Throwable", "RuntimeException", "BaseException"}
_ZERO = r"0[LlDdFf]?"
_PATH_PREFIX = r"/[^\"'\s]*/"

# --- Java ----------------------------------------------------------------------------------------------

_JAVA_BROAD_THROWS = re.compile(
    r"\b(?:assertThrows|expectThrows|assertThatExceptionOfType)\s*\(\s*(?:java\.lang\.)?(?:Exception|Throwable|RuntimeException)\.class"
    r"|\.isInstanceOf\(\s*(?:java\.lang\.)?(?:Exception|Throwable|RuntimeException)\.class\s*\)")
_JAVA_THROWN_BY = re.compile(r"\bassertThatThrownBy\s*\(")
_JAVA_THROWN_TYPED = re.compile(r"\.\s*(?:isInstanceOf\w*|isExactlyInstanceOf|hasMessage\w*|hasCause\w*|hasRootCause\w*)\s*\(")
_JAVA_CATCH = re.compile(r"\bcatch\s*\(\s*(?:final\s+)?([\w.]+(?:\s*\|\s*[\w.]+)*)\s+\w+\s*\)\s*\{")
_JAVA_GE_ZERO_CALL = re.compile(rf"\bisGreaterThanOrEqualTo\(\s*{_ZERO}\s*\)")
_JAVA_ASSERT_OPEN = re.compile(r"\bassert\w*\s*\(")
_JAVA_GE_ZERO_TAIL = re.compile(rf">=\s*{_ZERO}\s*\)")
_JAVA_ASSERT_TRUE = re.compile(r"\bassertTrue\(\s*true\s*\)|\bassertFalse\(\s*false\s*\)|\bassertThat\(\s*true\s*\)\s*\.isTrue\(\)")
# V5 (8.D6-6): the three patterns below read to the end of the text from every start when nothing closes them (a cut-off or looping model answer: 100 KB
# of «@Test a(» did not finish).  The method header starts at the start of a run of type characters (what follows a start inside a run is the same
# run to its end, so the same match), and the test header and the two tails are found from positions worked out once (``_java_test_braces``,
# ``_ge_zero_assertions``, ``_path_calls``).
_JAVA_METHOD = re.compile(r"(?<![\w<>\[\],.?])[\w<>\[\],.?]++\s+(\w+)\s*\([^()]*\)\s*(?:throws\s+[\w.,\s]+)?\{")
_JAVA_TEST_ANNOTATION = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest)\b")
_JAVA_CALL_OPEN = re.compile(r"\b\w+\s*\(")
_JAVA_HEADER_TAIL = re.compile(r"\s*(?:throws\s+[\w.,\s]+)?\{")
_JAVA_PATH_OPEN = re.compile(r"\b(?:contains\w*|startsWith|endsWith)\s*\(")
_JAVA_PATH_LITERAL = re.compile(rf"\"({_PATH_PREFIX})\"\s*\)")
_JAVA_PATH_AFTER_COMMA = re.compile(rf",\s*\"({_PATH_PREFIX})\"\s*\)")
_JAVA_NOT_NULL = re.compile(r"^\s*(?:assertNotNull\s*\(|assertThat\s*\(.*\)\s*\.isNotNull\(\)\s*$)", re.DOTALL)
# V5 (8.D6-6): how much of a test method that is never closed is read; its body is the rest of the file, and a cut-off file that opens 6 000 of them made that n squared
_UNCLOSED_BODY_LIMIT = 4096
_KEYWORDS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "else", "try", "do"}


def _mask_java(text: str, *, strings: bool) -> str:
    """Blank comments (and, with ``strings``, string and char contents) keeping every offset and line break."""
    out = list(text)
    index, length = 0, len(text)

    def blank(start: int, end: int) -> None:
        for position in range(start, end):
            if out[position] != "\n":
                out[position] = " "

    while index < length:
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end < 0 else end
            blank(index, end)
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            blank(index, end)
            index = end
        elif text.startswith('"""', index):
            end = text.find('"""', index + 3)
            end = length if end < 0 else end + 3
            if strings:
                blank(index + 3, max(index + 3, end - 3))
            index = end
        elif text[index] in "\"'":
            quote, end = text[index], index + 1
            while end < length and text[end] != quote and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            if strings:
                blank(index + 1, min(end, length))
            index = end + 1
        else:
            index += 1
    return "".join(out)


def _brace_pairs(code: str) -> dict[int, int]:
    """Position of every ``{`` with the position of the ``}`` that closes it (strings and comments are already blank); one pass over the file."""
    pairs: dict[int, int] = {}
    opened: list[int] = []
    for match in re.finditer(r"[{}]", code):
        if match.group() == "{":
            opened.append(match.start())
        elif opened:
            pairs[opened.pop()] = match.start()
    return pairs


class _Code:
    """What the Java rules ask of the masked code again and again, worked out once: the pairs of braces (``block``) and where a statement ends
    (``statement_end``).  Walking the file character by character for every method and every ``assertThatThrownBy`` made a cut-off file n squared."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.pairs = _brace_pairs(code)
        self.bracket_positions: list[int] = []
        self.bracket_depths: list[int] = []  # the depth after the bracket at the same index
        self.semicolons: list[int] = []
        self.semicolon_depths: list[int] = []
        depth = 0
        for match in re.finditer(r"[(){};]", code):
            char = match.group()
            if char == ";":
                self.semicolons.append(match.start())
                self.semicolon_depths.append(depth)
                continue
            depth += 1 if char in "({" else -1
            self.bracket_positions.append(match.start())
            self.bracket_depths.append(depth)
        # next_lower[i]: the first later semicolon that is not deeper than semicolon i
        self.next_lower = [len(self.semicolons)] * len(self.semicolons)
        waiting: list[int] = []
        for index, depth in enumerate(self.semicolon_depths):
            while waiting and self.semicolon_depths[waiting[-1]] >= depth:
                self.next_lower[waiting.pop()] = index
            waiting.append(index)

    def block(self, brace: int) -> tuple[int, int]:
        """The body between the brace at ``brace`` and its pair; an unclosed brace has the rest of the file."""
        return brace + 1, self.pairs.get(brace, len(self.code))

    def statement_end(self, start: int) -> int:
        """The ``;`` that ends the statement at ``start``, outside parentheses and braces (lambda bodies)."""
        before = bisect.bisect_left(self.bracket_positions, start)
        depth = self.bracket_depths[before - 1] if before else 0
        index = bisect.bisect_left(self.semicolons, start)
        while index < len(self.semicolons) and self.semicolon_depths[index] > depth:
            index = self.next_lower[index]
        return self.semicolons[index] if index < len(self.semicolons) else len(self.code)


def _java_test_braces(code: str) -> list[int]:
    """The ``{`` of every test method header, as ``_JAVA_TEST`` found them (``@Test`` [anything but ``{;``] ``name(`` [anything but ``)``] ``)``
    [``throws`` types] ``{``): the first ``name(`` after the annotation whose parenthesis is closed into a header before any ``{`` or ``;`` comes
    between the annotation and the name; an annotation inside the header just found is not read again."""
    stops = [match.start() for match in re.finditer(r"[{;]", code)]
    closers = [match.start() for match in re.finditer(r"\)", code)]
    brace_after: dict[int, int] = {}  # a closing parenthesis -> the ``{`` that ends the header it closes, or -1
    starts: list[int] = []
    braces: list[int] = []
    for call in _JAVA_CALL_OPEN.finditer(code):
        index = bisect.bisect_left(closers, call.end())
        if index == len(closers):
            break  # no later call has a closing parenthesis either
        close = closers[index]
        if close not in brace_after:
            tail = _JAVA_HEADER_TAIL.match(code, close + 1)
            brace_after[close] = tail.end() - 1 if tail else -1
        if brace_after[close] >= 0:
            starts.append(call.start())
            braces.append(brace_after[close])
    found: list[int] = []
    resume = 0
    for annotation in _JAVA_TEST_ANNOTATION.finditer(code):
        if annotation.start() < resume:
            continue
        stop = bisect.bisect_left(stops, annotation.end())
        limit = stops[stop] if stop < len(stops) else len(code)
        first = bisect.bisect_left(starts, annotation.end())
        if first < len(starts) and starts[first] < limit:
            found.append(braces[first])
            resume = braces[first] + 1
    return found


def _ge_zero_assertions(code: str) -> list[int]:
    """Where ``isGreaterThanOrEqualTo(0)`` or ``assertX( … >= 0 )`` (nothing but ``;`` ends the arguments) starts, as ``finditer`` found them for the
    pattern ``\\bisGreaterThanOrEqualTo\\(\\s*0\\s*\\)|\\bassert\\w*\\s*\\([^;]*?>=\\s*0\\s*\\)``: the ``>= 0 )`` tails are found once, an ``assertX(`` takes the first one
    before the next ``;``."""
    semicolons = [match.start() for match in re.finditer(r";", code)]
    tails = [match for match in _JAVA_GE_ZERO_TAIL.finditer(code)]
    tail_starts = [match.start() for match in tails]
    candidates: list[tuple[int, int]] = [(match.start(), match.end()) for match in _JAVA_GE_ZERO_CALL.finditer(code)]
    for match in _JAVA_ASSERT_OPEN.finditer(code):
        stop = bisect.bisect_left(semicolons, match.end())
        limit = semicolons[stop] if stop < len(semicolons) else len(code)
        index = bisect.bisect_left(tail_starts, match.end())
        if index < len(tails) and tail_starts[index] < limit:
            candidates.append((match.start(), tails[index].end()))
    found: list[int] = []
    resume = 0
    for start, end in sorted(candidates):
        if start >= resume:
            found.append(start)
            resume = end
    return found


def _path_calls(plain: str) -> list[int]:
    """Where ``contains…(``, ``startsWith(`` or ``endsWith(`` takes a path-prefix literal as its last argument (``"/x/"``, nothing but ``;``-free text
    and a comma before it, or straight after the parenthesis), as ``finditer`` found them for ``_JAVA_PATH`` (V5: that pattern read to the end of the text
    from every ``contains(`` when no ``;`` followed)."""
    semicolons = [match.start() for match in re.finditer(r";", plain)]
    after_comma = list(_JAVA_PATH_AFTER_COMMA.finditer(plain))
    comma_positions = [match.start() for match in after_comma]
    found: list[int] = []
    resume = 0
    for match in _JAVA_PATH_OPEN.finditer(plain):
        if match.start() < resume:
            continue
        stop = bisect.bisect_left(semicolons, match.end())
        limit = semicolons[stop] if stop < len(semicolons) else len(plain)
        end = -1
        index = bisect.bisect_left(comma_positions, match.end())
        if index < len(after_comma) and comma_positions[index] < limit:
            end = after_comma[index].end()
        else:
            direct = _JAVA_PATH_LITERAL.match(plain, match.end())
            if direct:
                end = direct.end()
        if end >= 0:
            found.append(match.start())
            resume = end
    return found


def _java(text: str) -> Iterator[tuple[str, int, str]]:
    code, plain = _mask_java(text, strings=True), _mask_java(text, strings=False)
    masked = _Code(code)
    for match in _JAVA_BROAD_THROWS.finditer(code):
        yield "AT_ANY_EXCEPTION", match.start(), "broad-throws"
    for match in _JAVA_THROWN_BY.finditer(code):
        # FaT-Q2 (2.12): assertThatThrownBy without a type or a message in its chain passes on any exception
        if not _JAVA_THROWN_TYPED.search(code, match.end(), masked.statement_end(match.start())):
            yield "AT_ANY_EXCEPTION", match.start(), "thrown-by-untyped"
    for match in _JAVA_CATCH.finditer(code):
        types = {name.strip().rsplit(".", 1)[-1] for name in match.group(1).split("|")}
        start, end = masked.block(match.end() - 1)
        if types & _BROAD and not re.search(r"\bthrow\b", code[start:end]):
            yield "AT_ANY_EXCEPTION", match.start(), "catch-all"
    for start in _ge_zero_assertions(code):
        yield "AT_ALWAYS_TRUE", start, "ge-zero"
    helpers = set()
    for match in _JAVA_METHOD.finditer(code):
        if match.group(1) in _KEYWORDS:
            continue
        start, end = masked.block(match.end() - 1)
        if end - start <= 400 and re.search(r"\bisGreaterThanOrEqualTo\(|>=", code[start:end]):
            helpers.add(match.group(1))
    for name in sorted(helpers):
        for match in re.finditer(rf"\b{re.escape(name)}\s*\([^;{{}}]*?,\s*{_ZERO}\s*\)\s*;", plain):
            yield "AT_ALWAYS_TRUE", match.start(), "ge-zero"
    for match in _JAVA_ASSERT_TRUE.finditer(code):
        yield "AT_ALWAYS_TRUE", match.start(), "assert-true"
    for brace in _java_test_braces(code):
        start, end = masked.block(brace)
        if brace not in masked.pairs:  # a method that is never closed (a cut-off answer) has the rest of the file for a body — and so has every one after it
            end = min(end, start + _UNCLOSED_BODY_LIMIT)
        statements, offset = [], start
        for piece in code[start:end].split(";"):
            statements.append((offset, piece, plain[offset:offset + len(piece)]))
            offset += len(piece) + 1
        checks = [(at, piece) for at, piece, raw in statements if re.search(r"\b(?:assert\w*|verify\w*|andExpect\w*)\s*\(", piece) or _ASSERT_ID.search(raw)]
        not_null = [(at, piece) for at, piece in checks if _JAVA_NOT_NULL.match(piece)]
        if not_null and len(not_null) == len(checks):
            at, piece = not_null[0]
            yield "AT_ALWAYS_TRUE", at + len(piece) - len(piece.lstrip()), "not-null-only"
    for start in _path_calls(plain):
        yield "AT_PARTIAL_MATCH", start, "path-prefix"


# --- Python --------------------------------------------------------------------------------------------

_PY_BROAD_THROWS = re.compile(r"\b(?:pytest\.raises|assertRaises)\(\s*(?:builtins\.)?(?:Exception|BaseException)\s*[,)]")
_PY_EXCEPT_HEAD = re.compile(r"^(\s*)except\b")
_PY_TYPE_LIST = re.compile(r"[\w.,\s]*")


def _py_except(line: str) -> tuple[str, str, str] | None:
    """``(indent, types, rest of the line)`` of ``except [(]types[)] [as name]:``, else None.  V5 (8.D6-6): the one pattern with a lazy ``[\\w.,\\s]*?`` between three
    ``\\s*`` was cubic on a blank run (10 KB of blanks did not finish); the line is cut at its first colon and the head read piece by piece."""
    opening = _PY_EXCEPT_HEAD.match(line)
    colon = line.find(":", opening.end()) if opening else -1
    if colon < 0:
        return None
    head = line[opening.end():colon].strip()
    if head.startswith("("):
        head = head[1:].lstrip()
    name_and_rest = head.rsplit(None, 1)
    if len(name_and_rest) == 2 and re.fullmatch(r"\w+", name_and_rest[1]):  # ``... as name``
        before = name_and_rest[0].rsplit(None, 1)
        if len(before) == 2 and before[1] == "as":
            head = before[0]
    head = head.rstrip()
    if head.endswith(")"):
        head = head[:-1].rstrip()
    if not _PY_TYPE_LIST.fullmatch(head):
        return None
    return opening.group(1), head, line[colon + 1:]


_PY_GE_ZERO = re.compile(r"^\s*assert\s+[^#]*>=\s*0\s*(?:,.*)?$")
_PY_ASSERT_TRUE = re.compile(r"^\s*assert\s+True\s*(?:,.*)?$")
_PY_NOT_NULL = re.compile(r"^\s*assert\s+.+\s+is\s+not\s+None\s*(?:,.*)?$")
_PY_PATH = re.compile(rf"\bassert\s+([\"'])({_PATH_PREFIX})\1\s+in\b|\.(?:startswith|endswith)\(\s*([\"'])({_PATH_PREFIX})\3\s*\)")
_PY_TEST = re.compile(r"^(\s*)(?:async\s+)?def\s+test\w*\s*\(")


def _strip_python_comment(line: str) -> str:
    quote = None
    for position, char in enumerate(line):
        if quote:
            if char == "\\":
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:position]
    return line


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _body(lines: list[str], index: int) -> list[int]:
    """Indices of the lines indented deeper than ``lines[index]`` right after it (blank lines included)."""
    own, body = _indent(lines[index]), []
    for position in range(index + 1, len(lines)):
        if lines[position].strip() and _indent(lines[position]) <= own:
            break
        body.append(position)
    return body


def _python(text: str) -> Iterator[tuple[str, int, str]]:
    lines = [_strip_python_comment(line) for line in text.splitlines()]
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    for index, line in enumerate(lines):
        at = starts[index] + _indent(line)
        if _PY_BROAD_THROWS.search(line):
            yield "AT_ANY_EXCEPTION", starts[index] + _PY_BROAD_THROWS.search(line).start(), "broad-throws"
        handler = _py_except(line)
        if handler:
            types = {name.strip().rsplit(".", 1)[-1] for name in handler[1].split(",") if name.strip()}
            block = handler[2] + "\n" + "\n".join(lines[position] for position in _body(lines, index))
            if (not types or types & _BROAD) and not re.search(r"\braise\b", block):
                yield "AT_ANY_EXCEPTION", at, "catch-all"
        if _PY_GE_ZERO.match(line):
            yield "AT_ALWAYS_TRUE", at, "ge-zero"
        if _PY_ASSERT_TRUE.match(line):
            yield "AT_ALWAYS_TRUE", at, "assert-true"
        path = _PY_PATH.search(line)
        if path:
            yield "AT_PARTIAL_MATCH", starts[index] + path.start(), "path-prefix"
        if _PY_TEST.match(line):
            body = [position for position in _body(lines, index) if lines[position].strip()]
            checks = [position for position in body if re.match(r"\s*(?:assert\b|self\.assert\w*\(|with\s+pytest\.raises\b)", lines[position])]
            not_null = [position for position in checks if _PY_NOT_NULL.match(lines[position])]
            if not_null and len(not_null) == len(checks):
                yield "AT_ALWAYS_TRUE", starts[not_null[0]] + _indent(lines[not_null[0]]), "not-null-only"


# --- the artifact ----------------------------------------------------------------------------------------

_SCANNERS = {"java": _java, "python": _python}


def autotest_hints(automation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every weak-assertion hint of the generated Java and Python files, in file and line order; other languages are skipped."""
    artifact = automation.get("artifacts") if isinstance(automation.get("artifacts"), Mapping) else automation
    hints: list[dict[str, Any]] = []
    for item in artifact.get("generated_files") or []:
        scanner = _SCANNERS.get(str(item.get("language", "")).lower())
        content = item.get("content")
        if scanner is None or not isinstance(content, str):
            continue
        raw_lines = content.splitlines()
        breaks = [index for index, char in enumerate(content) if char == "\n"]
        seen = set()
        for code, offset, rule in scanner(content):
            line = bisect.bisect_left(breaks, offset) + 1
            if (code, line, rule) in seen:
                continue
            seen.add((code, line, rule))
            assertion = _ASSERT_ID.search(raw_lines[line - 1]) if line <= len(raw_lines) else None
            hints.append({"code": code, "file_id": str(item.get("file_id")), "line": line, "rule": rule,
                          **({"assertion_id": assertion.group(0)} if assertion else {})})
    order = {str(item.get("file_id")): index for index, item in enumerate(artifact.get("generated_files") or [])}
    return sorted(hints, key=lambda hint: (order.get(hint["file_id"], 0), hint["line"], CODES.index(hint["code"]), hint["rule"]))


def capped_autotest_hints(hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return capped(hints, more="AT_MORE")


def counts(hints: list[Mapping[str, Any]]) -> dict[str, int]:
    found = Counter(hint["code"] for hint in hints)
    return {code: found[code] for code in CODES if found[code]}


def main(argv: list[str] | None = None) -> int:
    from tools.schema_validation import StrictJsonError, load_json_strict

    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--automation", required=True, type=Path, help="tc-to-autotest artifact JSON")
    args = parser.parse_args(argv)
    try:
        automation = load_json_strict(args.automation)
        if not isinstance(automation, Mapping):
            raise ValueError("not an object")
        hints = autotest_hints(automation)
    except (StrictJsonError, OSError, ValueError, TypeError, AttributeError):
        emit_error("automation artifact is unavailable or not an object")
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"status": "ok", "counts": counts(hints), "hints": capped_autotest_hints(hints)}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
