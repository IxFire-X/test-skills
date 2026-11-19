"""The lexer of Java text shared by the map of existing tests (mode A) and the checks of generated tests (mode B)."""

from __future__ import annotations


def mask_java_noncode(text: str, *, keep_quotes: bool = False) -> str:
    """Replace comments and quoted literals with spaces while preserving braces/newlines (``keep_quotes``: a string keeps its quotes)."""
    result = list(text)
    index, state, escaped = 0, "code", False
    while index < len(text):
        char = text[index]
        if state == "code":
            if text.startswith("//", index):
                result[index:index + 2] = "  "; index += 2; state = "line"; continue
            if text.startswith("/*", index):
                result[index:index + 2] = "  "; index += 2; state = "block"; continue
            if text.startswith('"""', index):
                if not keep_quotes: result[index:index + 3] = "   "
                index += 3; state = "text_block"; escaped = False; continue
            if char == '"': result[index] = '"' if keep_quotes else " "; state = "string"; escaped = False
            elif char == "'": result[index] = " "; state = "char"; escaped = False
        elif state == "line":
            if char == "\n": state = "code"
            else: result[index] = " "
        elif state == "block":
            if text.startswith("*/", index): result[index:index + 2] = "  "; index += 1; state = "code"
            elif char != "\n": result[index] = " "
        elif state == "text_block":
            if not escaped and text.startswith('"""', index):
                if not keep_quotes: result[index:index + 3] = "   "
                index += 3; state = "code"; continue
            escaped = not escaped and char == "\\"
            if char != "\n": result[index] = " "
        else:
            if char == "\n": state = "code"
            elif escaped: result[index] = " "; escaped = False
            elif char == "\\": result[index] = " "; escaped = True
            elif (state == "string" and char == '"') or (state == "char" and char == "'"): result[index] = '"' if keep_quotes and state == "string" else " "; state = "code"
            else: result[index] = " "
        index += 1
    return "".join(result)
