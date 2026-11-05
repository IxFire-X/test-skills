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
_JAVA_GE_ZERO = re.compile(rf"\bisGreaterThanOrEqualTo\(\s*{_ZERO}\s*\)|\bassert\w*\s*\([^;]*?>=\s*{_ZERO}\s*\)")
_JAVA_ASSERT_TRUE = re.compile(r"\bassertTrue\(\s*true\s*\)|\bassertFalse\(\s*false\s*\)|\bassertThat\(\s*true\s*\)\s*\.isTrue\(\)")
_JAVA_METHOD = re.compile(r"\b[\w<>\[\],.?]+\s+(\w+)\s*\([^()]*\)\s*(?:throws\s+[\w.,\s]+)?\{")
_JAVA_TEST = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest)\b[^{;]*?\b\w+\s*\([^)]*\)\s*(?:throws\s+[\w.,\s]+)?\{")
_JAVA_PATH = re.compile(rf"\b(?:contains\w*|startsWith|endsWith)\s*\((?:[^;]*?,\s*)?\"({_PATH_PREFIX})\"\s*\)")
_JAVA_NOT_NULL = re.compile(r"^\s*(?:assertNotNull\s*\(|assertThat\s*\(.*\)\s*\.isNotNull\(\)\s*$)", re.DOTALL)
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


def _block(code: str, brace: int) -> tuple[int, int]:
    """The body between the brace at ``brace`` and its pair (strings and comments are already blank)."""
    depth = 0
    for position in range(brace, len(code)):
        if code[position] == "{":
            depth += 1
        elif code[position] == "}":
            depth -= 1
            if depth == 0:
                return brace + 1, position
    return brace + 1, len(code)


def _statement_end(code: str, start: int) -> int:
    """The ``;`` that ends the statement at ``start``, outside parentheses and braces (lambda bodies)."""
    depth = 0
    for position in range(start, len(code)):
        char = code[position]
        if char in "({":
            depth += 1
        elif char in ")}":
            depth -= 1
        elif char == ";" and depth <= 0:
            return position
    return len(code)


def _java(text: str) -> Iterator[tuple[str, int, str]]:
    code, plain = _mask_java(text, strings=True), _mask_java(text, strings=False)
    for match in _JAVA_BROAD_THROWS.finditer(code):
        yield "AT_ANY_EXCEPTION", match.start(), "broad-throws"
    for match in _JAVA_THROWN_BY.finditer(code):
        # FaT-Q2 (2.12): assertThatThrownBy without a type or a message in its chain passes on any exception
        if not _JAVA_THROWN_TYPED.search(code, match.end(), _statement_end(code, match.start())):
            yield "AT_ANY_EXCEPTION", match.start(), "thrown-by-untyped"
    for match in _JAVA_CATCH.finditer(code):
        types = {name.strip().rsplit(".", 1)[-1] for name in match.group(1).split("|")}
        start, end = _block(code, match.end() - 1)
        if types & _BROAD and not re.search(r"\bthrow\b", code[start:end]):
            yield "AT_ANY_EXCEPTION", match.start(), "catch-all"
    for match in _JAVA_GE_ZERO.finditer(code):
        yield "AT_ALWAYS_TRUE", match.start(), "ge-zero"
    helpers = set()
    for match in _JAVA_METHOD.finditer(code):
        if match.group(1) in _KEYWORDS:
            continue
        start, end = _block(code, match.end() - 1)
        if end - start <= 400 and re.search(r"\bisGreaterThanOrEqualTo\(|>=", code[start:end]):
            helpers.add(match.group(1))
    for name in sorted(helpers):
        for match in re.finditer(rf"\b{re.escape(name)}\s*\([^;{{}}]*?,\s*{_ZERO}\s*\)\s*;", plain):
            yield "AT_ALWAYS_TRUE", match.start(), "ge-zero"
    for match in _JAVA_ASSERT_TRUE.finditer(code):
        yield "AT_ALWAYS_TRUE", match.start(), "assert-true"
    for match in _JAVA_TEST.finditer(code):
        start, end = _block(code, match.end() - 1)
        statements, offset = [], start
        for piece in code[start:end].split(";"):
            statements.append((offset, piece, plain[offset:offset + len(piece)]))
            offset += len(piece) + 1
        checks = [(at, piece) for at, piece, raw in statements if re.search(r"\b(?:assert\w*|verify\w*|andExpect\w*)\s*\(", piece) or _ASSERT_ID.search(raw)]
        not_null = [(at, piece) for at, piece in checks if _JAVA_NOT_NULL.match(piece)]
        if not_null and len(not_null) == len(checks):
            at, piece = not_null[0]
            yield "AT_ALWAYS_TRUE", at + len(piece) - len(piece.lstrip()), "not-null-only"
    for match in _JAVA_PATH.finditer(plain):
        yield "AT_PARTIAL_MATCH", match.start(), "path-prefix"


# --- Python --------------------------------------------------------------------------------------------

_PY_BROAD_THROWS = re.compile(r"\b(?:pytest\.raises|assertRaises)\(\s*(?:builtins\.)?(?:Exception|BaseException)\s*[,)]")
_PY_EXCEPT = re.compile(r"^(\s*)except\b\s*\(?\s*([\w.,\s]*?)\s*\)?\s*(?:as\s+\w+\s*)?:(.*)$")
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
        handler = _PY_EXCEPT.match(line)
        if handler:
            types = {name.strip().rsplit(".", 1)[-1] for name in handler.group(2).split(",") if name.strip()}
            block = handler.group(3) + "\n" + "\n".join(lines[position] for position in _body(lines, index))
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
