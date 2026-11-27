"""The one lexer of Java text (W2.3): the spans of comments and literals, and the masks built on them — for the map of existing tests
(mode A), the hints on generated tests and the checks of generated tests (mode B).  A literal ends at its quote or at the end of its
line (a Java string does not cross lines), a text block ``\"\"\"`` at an unescaped ``\"\"\"``, a comment at its end or at the end of the
text; a Kotlin name in backticks (``fun `doesn't fail`()``) is code up to its closing backtick on its line.  String operations only;
every mask keeps each offset and every line break."""

from __future__ import annotations

_STRINGS, _CHAR = frozenset({"string", "text"}), frozenset({"char"})  # the kinds of literals: strings and text blocks, chars

def java_spans(text: str) -> list[tuple[str, int, int, int, int]]:
    """``[(kind, start, end, opening, closing)]`` of the comments and literals in order: kind ``comment``, ``string``, ``text`` (a text
    block), ``char`` or ``name`` (a Kotlin name in backticks, kept by every mask); ``opening`` and ``closing`` — the length of the delimiters inside ``[start, end)``, ``closing`` 0 when unclosed."""
    found, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end < 0 else end
            found.append(("comment", index, end, 0, 0))
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            found.append(("comment", index, end, 0, 0))
        elif text.startswith('"""', index):
            end, escaped = index + 3, False
            while end < length and (escaped or not text.startswith('"""', end)):
                escaped = not escaped and text[end] == "\\"
                end += 1
            closed = 3 if end < length else 0
            end += closed
            found.append(("text", index, end, 3, closed))
        elif char == "`" and (end := text.find("`", index + 1)) > 0 and "\n" not in text[index:end]:  # a quote inside it opens nothing
            end += 1
            found.append(("name", index, end, 1, 1))
        elif char in "\"'":
            end, escaped = index + 1, False
            while end < length and text[end] != "\n" and (escaped or text[end] != char):
                escaped = not escaped and text[end] == "\\"
                end += 1
            closed = int(end < length and text[end] == char)
            end += closed
            found.append(("string" if char == '"' else "char", index, end, 1, closed))
        else:
            index += 1
            continue
        index = end
    return found


def _blank(piece: str) -> str:
    """Spaces for every character but a line break."""
    return "\n".join(" " * len(line) for line in piece.split("\n"))


def _masked(text: str, whole: frozenset[str], inner: frozenset[str]) -> str:
    """``text`` with every comment and every literal of a kind in ``whole`` blank, a literal of a kind in ``inner`` blank inside its
    delimiters, the other literals as they are."""
    out, at = [], 0
    for kind, start, end, opening, closing in java_spans(text):
        out.append(text[at:start])
        if kind == "comment" or kind in whole:
            out.append(_blank(text[start:end]))
        elif kind in inner:
            out += [text[start:start + opening], _blank(text[start + opening:end - closing]), text[end - closing:end]]
        else:
            out.append(text[start:end])
        at = end
    out.append(text[at:])
    return "".join(out)


def mask_java_noncode(text: str, *, keep_quotes: bool = False) -> str:
    """Comments and quoted literals blank, braces and line breaks kept (``keep_quotes``: a string keeps its quotes, a char never does)."""
    return _masked(text, _CHAR, _STRINGS) if keep_quotes else _masked(text, _STRINGS | _CHAR, frozenset())


def mask_java_comments(text: str) -> str:
    """Comments blank, literals as they are (the paths and view names of the existing tests are in strings)."""
    return _masked(text, frozenset(), frozenset())


def mask_java_contents(text: str) -> str:
    """Comments and the contents of every literal blank, the delimiters kept (chars too) — the code of the hints on generated tests."""
    return _masked(text, frozenset(), _STRINGS | _CHAR)
