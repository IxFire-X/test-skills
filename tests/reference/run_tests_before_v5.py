"""The run_tests functions of the package before V5 (8.D6-7), kept only to be compared with the linear ones: ``_java_class``, ``_java_node``,
``_declared_after_type`` — the text is copied from tools/run_tests.py of commit bdc919b, nothing else of that module is here.

Used by tests/test_v5_equivalence.py (loaded as ``tools.run_tests_before_v5``); it imports nothing but the shared Java lexer.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from tools.java_text import mask_java_noncode

_mask_java_noncode = mask_java_noncode


def _java_class(path: Path, text: str) -> tuple[str, str] | None:
    masked = _mask_java_noncode(text)
    package = re.search(r"^\s*package\s+([\w$]+(?:\.[\w$]+)*)\s*;", masked, re.MULTILINE)
    class_fqn = (package.group(1) + "." if package else "") + path.stem
    class_name = path.stem
    if len(re.findall(r"\bclass\s+" + re.escape(class_name) + r"\b", masked)) != 1:
        return None
    declaration = re.search(r"\bclass\s+" + re.escape(class_name) + r"\b[^\{]*\{", masked)
    if declaration is None:
        return None
    depth, end = 0, None
    for index in range(declaration.end() - 1, len(masked)):
        if masked[index] == "{": depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return None
    body = list(masked[declaration.end():end])
    nested = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_$][\w$]*\b[^\{]*\{")
    search_at = 0
    while (match := nested.search("".join(body), search_at)) is not None:
        nested_depth, nested_end = 0, None
        for index in range(match.end() - 1, len(body)):
            if body[index] == "{": nested_depth += 1
            elif body[index] == "}":
                nested_depth -= 1
                if nested_depth == 0:
                    nested_end = index + 1
                    break
        if nested_end is None:
            return None
        body[match.start():nested_end] = " " * (nested_end - match.start())
        search_at = nested_end
    return class_fqn, "".join(body)


def _java_node(parsed: tuple[str, str], locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] != "java_class_method" or parsed[0] != locator["class_fqn"]:
        return None
    method = locator["method_name"]
    body = parsed[1]
    # FaT-RC: the declaration `<type> <name>(…) [throws …] {` found from each occurrence of the name, not by one pattern
    # tried at every token — `[\w<>\[\]., ?]+\s+name` backtracked quadratically on a long line of words (2 h on one file).
    count = sum(_declared_after_type(body, match.start())
                for match in re.finditer(r"\b" + re.escape(method) + r"\s*\([^;{}]*\)\s*(?:throws[^\{]+)?\{", body))
    return method if count == 1 else None


def _declared_after_type(body: str, start: int) -> bool:
    """Whitespace before the name, and before it a run of type characters (``[\\w<>[]., ?]``) holding a word character."""
    index = start
    if index == 0 or not body[index - 1].isspace():
        return False
    while index > 0 and body[index - 1].isspace():
        index -= 1
    while index > 0 and (body[index - 1].isalnum() or body[index - 1] in "_<>[]., ?"):
        if body[index - 1].isalnum() or body[index - 1] == "_":
            return True
        index -= 1
    return False
