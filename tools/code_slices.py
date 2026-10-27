"""Exact source slices of generated test symbols for compact automation review.

A slice is a contiguous run of whole lines of the original file (an exact substring,
original line numbers): a case method, or a helper it calls (transitively, inside the
same file).  Everything that is not a case method — package and imports, the class
header with its annotations, fields, lifecycle methods, fixtures, helpers and nested
types — is the file's SUPPORT code.  The shared-state table lists static or shared
mutable fields and the members that write or read them.

Java is scanned with a small lexer that knows line and block comments, string and
character literals and text blocks, so braces inside them never count; annotations,
nested classes and lambdas stay inside the member that holds them.  Python uses
``ast``.  A symbol that cannot be located exactly raises ``SliceError``: the caller
then sends the whole file or blocks the part, never a guessed slice.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


class SliceError(ValueError):
    """The file could not be split into exact member slices."""


@dataclass
class Member:
    name: str
    kind: str                 # method | field | type | initializer | function | class
    start: int                # first line (1-based, inclusive)
    end: int                  # last line (inclusive)
    owner: str = ""           # enclosing class (qualified with dots) or "" for module level
    modifiers: tuple[str, ...] = ()
    calls: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)      # identifiers used in the member
    writes: set[str] = field(default_factory=set)     # fields the member assigns or mutates


@dataclass
class FileSlices:
    file_id: str
    path: str
    lines: list[str]
    members: list[Member]
    header_end: int           # last line of the file header before the first member
    symbols: dict[str, Member]                 # symbol_id -> case member
    helpers: dict[str, list[Member]]           # symbol_id -> helpers called transitively
    shared_state: list[dict[str, Any]]

    def text(self, start: int, end: int) -> str:
        width = max(4, len(str(len(self.lines))))
        return "\n".join(f"L{number:0{width}d}| {self.lines[number - 1]}" for number in range(start, end + 1))

    def support_ranges(self) -> list[tuple[int, int]]:
        """Every line that is not inside a case method, as merged ranges."""
        case_lines = {line for member in self.symbols.values() for line in range(member.start, member.end + 1)}
        ranges: list[tuple[int, int]] = []
        for line in range(1, len(self.lines) + 1):
            if line in case_lines:
                continue
            if ranges and ranges[-1][1] == line - 1:
                ranges[-1] = (ranges[-1][0], line)
            else:
                ranges.append((line, line))
        return ranges


# --------------------------------------------------------------------------------------
# Java
# --------------------------------------------------------------------------------------

_TYPE_WORDS = {"class", "interface", "enum", "record"}
_MODIFIERS = {"public", "protected", "private", "static", "final", "abstract", "synchronized", "native", "transient", "volatile",
              "strictfp", "default", "sealed", "non-sealed"}
_MUTATORS = {"add", "addAll", "put", "putAll", "remove", "removeIf", "clear", "set", "offer", "push", "poll", "pop", "computeIfAbsent",
             "compute", "merge", "replace", "replaceAll", "sort", "incrementAndGet", "getAndIncrement", "decrementAndGet", "getAndSet", "accumulateAndGet"}
_KEYWORDS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "throw", "else", "do", "try", "super", "this",
             "case", "assert", "yield", "instanceof"}


@dataclass
class _Token:
    kind: str     # ident | string | char | number | punct
    text: str
    line: int


def java_tokens(source: str) -> list[_Token]:
    """Significant tokens; comments are skipped, literals are single tokens (braces inside never count)."""
    tokens: list[_Token] = []
    index, line, size = 0, 1, len(source)
    while index < size:
        char = source[index]
        if char == "\n":
            line += 1
            index += 1
        elif char in " \t\r\f":
            index += 1
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = size if end < 0 else end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise SliceError("unterminated block comment")
            line += source.count("\n", index, end + 2)
            index = end + 2
        elif source.startswith('"""', index):
            end = index + 3
            while True:
                end = source.find('"""', end)
                if end < 0:
                    raise SliceError("unterminated text block")
                backslashes = 0
                probe = end - 1
                while probe >= index + 3 and source[probe] == "\\":
                    backslashes += 1
                    probe -= 1
                if backslashes % 2 == 0:
                    break
                end += 1
            tokens.append(_Token("string", source[index:end + 3], line))
            line += source.count("\n", index, end + 3)
            index = end + 3
        elif char in "\"'":
            end = index + 1
            while end < size and source[end] != char:
                if source[end] == "\\":
                    end += 1
                elif source[end] == "\n":
                    raise SliceError("unterminated literal")
                end += 1
            if end >= size:
                raise SliceError("unterminated literal")
            tokens.append(_Token("string" if char == '"' else "char", source[index:end + 1], line))
            index = end + 1
        elif char.isalpha() or char in "_$":
            end = index
            while end < size and (source[end].isalnum() or source[end] in "_$"):
                end += 1
            tokens.append(_Token("ident", source[index:end], line))
            index = end
        elif char.isdigit():
            match = re.compile(r"[0-9][0-9A-Za-z_.]*(?:[eEpP][+-]?[0-9]+)?[fFdDlL]?").match(source, index)
            end = match.end() if match else index + 1
            tokens.append(_Token("number", source[index:end], line))
            index = end
        elif source.startswith("::", index) or source.startswith("->", index):
            tokens.append(_Token("punct", source[index:index + 2], line))
            index += 2
        else:
            tokens.append(_Token("punct", char, line))
            index += 1
    return tokens


def _matching(tokens: Sequence[_Token], start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].kind == "punct":
            if tokens[index].text == opening:
                depth += 1
            elif tokens[index].text == closing:
                depth -= 1
                if depth == 0:
                    return index
    raise SliceError(f"unbalanced {opening}{closing}")


def _java_members(tokens: Sequence[_Token], body_start: int, body_end: int, owner: str, out: list[Member]) -> None:
    """Members between the braces of one class body (indexes of ``{`` and ``}``)."""
    index = body_start + 1
    while index < body_end:
        first = index
        paren = 0
        saw_assign = False
        name = None
        kind = None
        type_name = None
        modifiers: list[str] = []
        end_index = None
        while index < body_end:
            token = tokens[index]
            if token.kind == "punct" and token.text == "@" and index + 1 < body_end and tokens[index + 1].text != "interface":
                # An annotation: @Name, @a.b.Name, optionally with (arguments).
                index += 2
                while index + 1 < body_end and tokens[index].text == "." and tokens[index + 1].kind == "ident":
                    index += 2
                if index < body_end and tokens[index].text == "(":
                    index = _matching(tokens, index, "(", ")") + 1
                continue
            if token.kind == "punct":
                if token.text == "(":
                    if paren == 0 and not saw_assign and kind is None and index > first and tokens[index - 1].kind == "ident":
                        name, kind = tokens[index - 1].text, "method"
                    paren += 1
                elif token.text == ")":
                    paren -= 1
                elif token.text == "=" and paren == 0 and not (index + 1 < body_end and tokens[index + 1].text == "="):
                    saw_assign = True
                    if kind is None and index > first and tokens[index - 1].kind == "ident":
                        name, kind = tokens[index - 1].text, "field"
                elif token.text == ";" and paren == 0:
                    if kind is None:
                        previous = tokens[index - 1] if index > first else None
                        name, kind = (previous.text if previous and previous.kind == "ident" else "?"), "field"
                    end_index = index
                    break
                elif token.text == "{" and paren == 0:
                    if saw_assign:
                        index = _matching(tokens, index, "{", "}") + 1  # array initializer, anonymous class body
                        continue
                    close = _matching(tokens, index, "{", "}")
                    if type_name is not None:
                        kind, name = "type", type_name
                        _java_members(tokens, index, close, f"{owner}.{type_name}" if owner else type_name, out)
                    elif kind is None:
                        kind, name = "initializer", "static" if "static" in modifiers else "instance"
                    end_index = close
                    break
                elif token.text == "}":
                    raise SliceError("unexpected closing brace in a class body")
            elif token.kind == "ident":
                if token.text in _TYPE_WORDS and paren == 0 and not saw_assign and type_name is None and kind is None:
                    if index + 1 < body_end and tokens[index + 1].kind == "ident":
                        type_name = tokens[index + 1].text
                elif token.text in _MODIFIERS and paren == 0 and not saw_assign:
                    modifiers.append(token.text)
            index += 1
        if end_index is None:
            if index >= body_end and first < body_end:
                raise SliceError("member without an end")
            break
        member = Member(name=str(name), kind=str(kind), start=tokens[first].line, end=tokens[end_index].line, owner=owner, modifiers=tuple(modifiers))
        _java_uses(tokens[first:end_index + 1], member)
        out.append(member)
        index = end_index + 1
        # Enum constants and the like end at ";" with no member name: keep them as fields of the header.


def _java_uses(tokens: Sequence[_Token], member: Member) -> None:
    for index, token in enumerate(tokens):
        if token.kind != "ident":
            continue
        member.names.add(token.text)
        previous = tokens[index - 1].text if index else ""
        before = tokens[index - 2].text if index > 1 else ""
        following = tokens[index + 1].text if index + 1 < len(tokens) else ""
        qualified_self = previous == "." and before == "this"
        if following == "(" and token.text not in _KEYWORDS and (previous != "." or qualified_self) and previous != "new":
            member.calls.add(token.text)
        if previous == "::" and before in {"this", member.owner.rsplit(".", 1)[-1]}:
            member.calls.add(token.text)
        # Writes: name = …, name++, ++name, name += …, name.add(…) and other mutators.
        nxt2 = tokens[index + 2].text if index + 2 < len(tokens) else ""
        if previous != "." or qualified_self:
            if following in {"=", "+", "-", "*", "/", "%", "&", "|", "^", "<", ">"} and (following != "=" or nxt2 != "="):
                if following == "=" or nxt2 == "=" or (following in {"+", "-"} and nxt2 == following):
                    member.writes.add(token.text)
            if previous in {"+", "-"} and before == previous:
                member.writes.add(token.text)
            if following == "." and nxt2 in _MUTATORS:
                member.writes.add(token.text)


def java_file(file_id: str, path: str, source: str) -> tuple[list[Member], int]:
    tokens = java_tokens(source)
    members: list[Member] = []
    index, header_end = 0, None
    found = False
    while index < len(tokens):
        token = tokens[index]
        if token.kind == "ident" and token.text in _TYPE_WORDS and index + 1 < len(tokens) and tokens[index + 1].kind == "ident" \
                and (index == 0 or tokens[index - 1].text != "."):
            name = tokens[index + 1].text
            opening = next((position for position in range(index, len(tokens)) if tokens[position].text == "{"), None)
            if opening is None:
                raise SliceError("type without a body")
            close = _matching(tokens, opening, "{", "}")
            if header_end is None:
                header_end = tokens[opening].line
            _java_members(tokens, opening, close, name, members)
            found = True
            index = close + 1
            continue
        index += 1
    if not found:
        raise SliceError("no type declaration")
    return members, int(header_end or 1)


# --------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------

def _python_members(tree: ast.Module) -> list[Member]:
    members: list[Member] = []

    def start(node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", []) or []
        return min([node.lineno, *(item.lineno for item in decorators)])

    def uses(node: ast.AST, member: Member, owner: str) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                member.names.add(child.id)
                if isinstance(child.ctx, ast.Store):
                    member.writes.add(child.id)
            elif isinstance(child, ast.Attribute):
                member.names.add(child.attr)
                if isinstance(child.value, ast.Name) and child.value.id in {"self", "cls"} and isinstance(child.ctx, ast.Store):
                    member.writes.add(child.attr)
            elif isinstance(child, ast.Global):
                member.writes.update(child.names)
            if isinstance(child, ast.Call):
                function = child.func
                if isinstance(function, ast.Name):
                    member.calls.add(function.id)
                elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                    if function.value.id in {"self", "cls", owner.rsplit(".", 1)[-1] if owner else ""}:
                        member.calls.add(function.attr)
                    elif function.attr in _MUTATORS | {"append", "extend", "insert", "update", "setdefault", "discard"}:
                        member.writes.add(function.value.id)

    def visit(body: Sequence[ast.stmt], owner: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                member = Member(name=node.name, kind="method" if owner else "function", start=start(node), end=int(node.end_lineno), owner=owner)
                uses(node, member, owner)
                # A decorator wraps the test: it is a helper the test runs through.
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(target, ast.Name):
                        member.calls.add(target.id)
                members.append(member)
            elif isinstance(node, ast.ClassDef):
                members.append(Member(name=node.name, kind="class", start=start(node), end=int(node.end_lineno), owner=owner))
                visit(node.body, f"{owner}.{node.name}" if owner else node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        member = Member(name=target.id, kind="field", start=node.lineno, end=int(node.end_lineno), owner=owner)
                        uses(node, member, owner)
                        members.append(member)
    visit(tree.body, "")
    return members


# --------------------------------------------------------------------------------------
# slices
# --------------------------------------------------------------------------------------

def _locate(members: Sequence[Member], locator: Mapping[str, Any]) -> Member:
    kind = locator.get("kind")
    if kind == "java_class_method":
        owner = str(locator["class_fqn"]).rsplit(".", 1)[-1]
        found = [member for member in members if member.kind == "method" and member.name == locator["method_name"]
                 and (member.owner == owner or member.owner.endswith("." + owner) or member.owner.split(".")[0] == owner)
                 and member.owner.rsplit(".", 1)[-1] == owner]
    elif kind == "python_module_function":
        found = [member for member in members if member.kind == "function" and member.owner == "" and member.name == locator["function_name"]]
    elif kind == "python_class_method":
        found = [member for member in members if member.kind == "method" and member.owner == locator["qualified_class_name"] and member.name == locator["method_name"]]
    else:
        raise SliceError(f"unsupported locator {kind}")
    if len(found) != 1:
        raise SliceError(f"symbol {locator} found {len(found)} times")
    return found[0]


def _helpers(member: Member, members: Sequence[Member], cases: set[int]) -> list[Member]:
    """Methods of the same file the member calls, transitively; case methods are not helpers."""
    callable_members = [item for item in members if item.kind in {"method", "function"}]
    seen: list[Member] = []
    queue = [member]
    while queue:
        current = queue.pop(0)
        for name in sorted(current.calls):
            for target in callable_members:
                if target.name == name and target is not member and id(target) not in cases and target not in seen:
                    # A nested function is inside its parent's lines already.
                    if not any(outer.start <= target.start and target.end <= outer.end for outer in [member, *seen]):
                        seen.append(target)
                        queue.append(target)
    return sorted(seen, key=lambda item: item.start)


def _shared_state(members: Sequence[Member], language: str, header: str) -> list[dict[str, Any]]:
    per_class = "PER_CLASS" in header
    rows = []
    fields = [member for member in members if member.kind == "field"]
    for item in fields:
        static = "static" in item.modifiers or (language == "python" and item.owner == "")
        final = "final" in item.modifiers
        if not static and not per_class and language == "java":
            continue
        writers = sorted({member.name for member in members if member.kind in {"method", "function", "initializer"} and item.name in member.writes})
        readers = sorted({member.name for member in members if member.kind in {"method", "function", "initializer"} and item.name in member.names} - set(writers))
        if final and not writers:
            continue  # a constant: never shared mutable state
        rows.append({"name": item.name, "owner": item.owner, "line": item.start, "scope": "static" if static else "instance(PER_CLASS)",
                     "final": final, "written_by": writers, "read_by": readers})
    return rows


def slice_file(generated_file: Mapping[str, Any], symbols: Sequence[Mapping[str, Any]]) -> FileSlices:
    """Exact slices of every symbol of one generated file; raises ``SliceError`` when any is not exact."""
    source = str(generated_file["content"])
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    language = str(generated_file.get("language") or ("python" if str(generated_file.get("path", "")).endswith(".py") else "java"))
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            raise SliceError(f"python syntax: {error}") from error
        members = _python_members(tree)
        first = min((member.start for member in members), default=len(lines) + 1)
        header_end = first - 1
    else:
        members, header_end = java_file(str(generated_file["file_id"]), str(generated_file.get("path", "")), source)
    by_symbol = {str(symbol["symbol_id"]): _locate(members, symbol["locator"]) for symbol in symbols}
    case_ids = {id(member) for member in by_symbol.values()}
    if len(case_ids) != len(by_symbol):
        raise SliceError("two symbols share one member")
    helpers = {symbol_id: _helpers(member, members, case_ids) for symbol_id, member in by_symbol.items()}
    for member in members:
        if member.end > len(lines) or member.start < 1:
            raise SliceError("member outside the file")
    header = "\n".join(lines[:max(0, header_end)])
    return FileSlices(file_id=str(generated_file["file_id"]), path=str(generated_file.get("path", "")), lines=lines, members=list(members),
                      header_end=header_end, symbols=by_symbol, helpers=helpers, shared_state=_shared_state(members, language, header))


def shared_state_text(slices: FileSlices) -> str:
    if not slices.shared_state:
        return "Общих изменяемых полей нет (только константы и поля экземпляра с жизненным циклом PER_METHOD)."
    out = ["поле | где | строка | область | пишут | читают"]
    for row in slices.shared_state:
        out.append(f"{row['name']} | {row['owner'] or 'модуль'} | L{row['line']} | {row['scope']}{' final' if row['final'] else ''} | "
                   f"{', '.join(row['written_by']) or '—'} | {', '.join(row['read_by']) or '—'}")
    return "\n".join(out)
