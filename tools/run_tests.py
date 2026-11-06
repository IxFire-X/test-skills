#!/usr/bin/env python3
"""V5 generated-test runner with exact ``(file_id, symbol_id)`` evidence.

The package starts no process: a human runs the build, ``tools/exec_runner.py`` takes the reports (``ingest``).
"""
from __future__ import annotations

import ast
import bisect
import hashlib
import json
import re
from bisect import bisect_left
from functools import lru_cache
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from tools.skillsrc_manifest import (
    SkillsrcError,
    load_skillsrc,
    normalize_skillsrc,
    resolve_module_root,
    select_module,
)

from tools.java_text import mask_java_noncode  # the Java lexer is shared with the map of existing tests

_mask_java_noncode = mask_java_noncode
_ROOT = Path(__file__).resolve().parents[1]
_AUTOTEST_REVIEW_SCHEMA = _ROOT / "schemas" / "autotest-reviewer-output.schema.json"
DURABLE_NATIVE_REPORT_MAX_BYTES = 1024 * 1024
NATIVE_REPORT_MAX_BYTES = 8 * 1024 * 1024
NATIVE_REPORT_SET_MAX_BYTES = 32 * 1024 * 1024
DURABLE_NATIVE_REPORT_NORMALIZATION = "junit-semantic-v1"
_PROCESS_ERROR_CLASSES = {
    "TIMEOUT": "TimeoutExpired",
    "OS_ERROR": "OSError",
    "COLLECTION_ERROR": "CollectionError",
    "EXECUTION_ERROR": "ExecutionError",
    "JUNIT_MISSING": "JUnitMissing",
    "JUNIT_INVALID": "ParseError",
    "SOURCE_CHANGED": "SourceChanged",
    "BASELINE_DRIFT": "BaselineDrift",
    "NO_TESTS_COLLECTED": "NoTestsCollected",
}


class DurableNativeReportError(ValueError):
    """A framework report cannot be preserved inside the durable evidence bound."""


@dataclass(frozen=True)
class RunnerCompatibility:
    status: Literal["READY", "NOT_RUNNABLE"]
    required_pairs: tuple[tuple[str, str], ...]
    bindings: Mapping[tuple[str, str], Mapping[str, Any]]
    verified_file_digests: Mapping[str, str]
    diagnostics: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int
    stdout: str
    stderr: str
    kind: Literal["EXIT", "TIMEOUT", "OS_ERROR", "NOT_OBSERVED"] = "EXIT"

    def __iter__(self):
        yield self.exit_code
        yield self.stdout
        yield self.stderr


def resolve_execution_context(
    project_root: Path,
    skillsrc_path: Path,
    module_id: str | None,
    language_override: str | None,
) -> tuple[Path, str, dict[str, Any]]:
    """Resolve one manifest module into a project-confined execution context."""
    return resolve_execution_context_document(project_root, load_skillsrc(skillsrc_path), module_id, language_override)


def resolve_execution_context_document(
    project_root: Path,
    document: Mapping[str, Any],
    module_id: str | None,
    language_override: str | None,
) -> tuple[Path, str, dict[str, Any]]:
    """Resolve an already validated manifest without reading it again."""
    document = normalize_skillsrc(document)
    module = select_module(document, module_id)
    execution_root = resolve_module_root(project_root, module)
    detected_language = module["stack"]["language"]
    if language_override and language_override != detected_language:
        raise SkillsrcError("language_conflict", "--language conflicts with selected module")
    return execution_root, language_override or detected_language, module


class RunnerInputError(ValueError):
    def __init__(self, code: str, diagnostics: Sequence[Mapping[str, str]] | None = None) -> None:
        object.__setattr__(self, "code", code)
        rows = diagnostics or ({"path": "/artifacts/automation_status", "code": code, "message": "Automation artifact is not directly runnable."},)
        object.__setattr__(self, "diagnostics", tuple(MappingProxyType(dict(row)) for row in rows))
        super().__init__(code)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"code", "diagnostics"} and hasattr(self, name):
            raise AttributeError("RunnerInputError is immutable")
        super().__setattr__(name, value)


def _diag(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _compat(status: Literal["READY", "NOT_RUNNABLE"], pairs: Sequence[tuple[str, str]] = (),
            bindings: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
            digests: Mapping[str, str] | None = None, diagnostics: Sequence[Mapping[str, str]] = ()) -> RunnerCompatibility:
    return RunnerCompatibility(status, tuple(sorted(pairs)), MappingProxyType({key: _freeze(value) for key, value in (bindings or {}).items()}), MappingProxyType(dict(digests or {})), tuple(MappingProxyType(dict(row)) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"]))))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _path(root: Path, raw_path: str) -> Path | None:
    raw = Path(raw_path)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    try:
        value = (root / raw).resolve(strict=True)
        value.relative_to(root)
    except (OSError, ValueError):
        return None
    return value if value.is_file() else None


def _python_node(tree: ast.Module, locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] == "python_module_function":
        found = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == locator["function_name"]]
        return locator["function_name"] if len(found) == 1 else None
    if locator["kind"] != "python_class_method":
        return None
    members: list[ast.stmt] = list(tree.body)
    for name in locator["qualified_class_name"].split("."):
        classes = [node for node in members if isinstance(node, ast.ClassDef) and node.name == name]
        if len(classes) != 1:
            return None
        members = list(classes[0].body)
    found = [node for node in members if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == locator["method_name"]]
    return "::".join([*locator["qualified_class_name"].split("."), locator["method_name"]]) if len(found) == 1 else None


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
    text = masked[declaration.end():end]
    nested = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_$][\w$]*\b[^\{]*\{")
    # the body is searched where it is and the nested classes are blanked while it is copied out, once — joining the body into a new
    # string for every nested class found was n squared (7 500 empty nested classes: 4 s).  The pairs of braces are found in one pass.
    pairs = _brace_pairs(text)
    pieces: list[str] = []
    search_at = copied = 0
    while (match := nested.search(text, search_at)) is not None:
        nested_end = pairs.get(match.end() - 1)
        if nested_end is None:
            return None
        pieces.append(text[copied:match.start()])
        pieces.append(" " * (nested_end + 1 - match.start()))
        search_at = copied = nested_end + 1
    pieces.append(text[copied:])
    return class_fqn, "".join(pieces)


def _brace_pairs(text: str) -> dict[int, int]:
    """Position of every ``{`` of *text* with the position of the ``}`` that closes it; a brace never closed has no entry."""
    pairs: dict[int, int] = {}
    opened: list[int] = []
    for match in re.finditer(r"[{}]", text):
        if match.group() == "{":
            opened.append(match.start())
        elif opened:
            pairs[opened.pop()] = match.start()
    return pairs


# Surefire writes a class display name that differs from the simple class name as the report classname,
# so the report can no longer match the class FQN; a method display name leaves the testcase name alone.
_CLASS_DISPLAY_ANNOTATION = re.compile(r"@\s*(?:[\w$]+\s*\.\s*)*(?:DisplayName|DisplayNameGeneration|IndicativeSentencesGeneration)\b")
_CLASS_DISPLAY_NAME_FIX = (
    "аннотация класса @DisplayName (@DisplayNameGeneration, @IndicativeSentencesGeneration) меняет classname в отчёте "
    "Surefire — отчёт не сопоставится с классом теста: убери @DisplayName с класса; у методов можно"
)


def _java_class_display_name(class_name: str, text: str) -> bool:
    """Whether the top-level test class carries an annotation that renames it in the Surefire report."""
    masked = _mask_java_noncode(text)
    declaration = re.search(r"\bclass\s+" + re.escape(class_name) + r"\b", masked)
    if declaration is None:
        return False
    head = masked[:declaration.start()]
    return _CLASS_DISPLAY_ANNOTATION.search(head[head.rfind(";") + 1:]) is not None


def _java_node(parsed: tuple[str, str], locator: Mapping[str, Any]) -> str | None:
    if locator["kind"] != "java_class_method" or parsed[0] != locator["class_fqn"]:
        return None
    method = locator["method_name"]
    body = parsed[1]
    # the declaration `<type> <name>(…) [throws …] {` found from each occurrence of the name, not by one pattern
    # tried at every token — `[\w<>\[\]., ?]+\s+name` backtracked quadratically on a long line of words (2 h on one file).
    count = sum(_declared_after_type(body, start) for start in _declaration_starts(body, method))
    return method if count == 1 else None


_TAIL_OF_A_HEADER = re.compile(r"\s*")


def _declaration_starts(body: str, method: str) -> list[int]:
    """Where ``\\bNAME\\s*\\([^;{}]*\\)\\s*(?:throws[^\\{]+)?\\{`` matches in *body*, as ``finditer`` finds them (8.D6-7: that pattern read to the end of the
    body from every ``foo(`` when no ``;``, ``{`` or ``}`` followed — 100 KB took 4 s).  A match is a name and its opening parenthesis, a closing parenthesis
    before the first of ``;{}`` that the header ends on (the last such one that does: ``[^;{}]*`` is greedy), blanks, and either ``{`` or ``throws`` and the
    first ``{`` after it.  Which parenthesis ends a header does not depend on the name, so it is worked out once per parenthesis."""
    stops = [match.start() for match in re.finditer(r"[;{}]", body)]
    braces = [match.start() for match in re.finditer(r"\{", body)]
    closers = [match.start() for match in re.finditer(r"\)", body)]
    ends: dict[int, int] = {}  # a closing parenthesis -> the end of the match it gives, or -1
    good: list[int] = []  # closing parentheses that give a match, in order

    def end_of(close: int) -> int:
        found = ends.get(close)
        if found is None:
            at = _TAIL_OF_A_HEADER.match(body, close + 1).end()
            found = -1
            if body.startswith("{", at):
                found = at + 1
            elif body.startswith("throws", at):
                brace = bisect.bisect_left(braces, at + 6)
                if brace < len(braces) and braces[brace] > at + 6:
                    found = braces[brace] + 1
            ends[close] = found
        return found

    for close in closers:
        if end_of(close) >= 0:
            good.append(close)
    starts: list[int] = []
    resume = 0
    for occurrence in re.finditer(r"\b" + re.escape(method) + r"\s*\(", body):
        if occurrence.start() < resume:
            continue
        paren = occurrence.end() - 1
        stop = bisect.bisect_right(stops, paren)
        limit = stops[stop] if stop < len(stops) else len(body)
        index = bisect.bisect_left(good, limit) - 1  # the last good closing parenthesis before the first stop
        if index >= 0 and good[index] > paren:
            starts.append(occurrence.start())
            resume = end_of(good[index])
    return starts


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


# a closed list of Java APIs newer than a module's level that a regular expression finds without a compiler —
# (pattern on code without comments and literal contents, Java version, name, replacement, receiver class, argument count).
# A rule names a JDK class as its receiver, so an explicit import of a same-named class from elsewhere (vavr ``List``,
# Guava ``Files``/``Predicate``) turns it off; a method other libraries also have is narrowed by its argument count, and
# one that cannot be narrowed (``isBlank()`` is an AssertJ assertion too, ``readAllBytes()``) is left to the build.
_JAVA_API_RULES = (
    (r"\bList\s*\.\s*of\s*\(", 9, "List.of", "Arrays.asList(...), Collections.singletonList/emptyList", "List", None),
    (r"\bSet\s*\.\s*of\s*\(", 9, "Set.of", "new HashSet<>(Arrays.asList(...)), Collections.singleton/emptySet", "Set", None),
    (r"\bMap\s*\.\s*(?:of|ofEntries|entry)\s*\(", 9, "Map.of/ofEntries/entry", "HashMap с put, AbstractMap.SimpleEntry", "Map", None),
    (r"\bObjects\s*\.\s*requireNonNullElse(?:Get)?\s*\(", 9, "Objects.requireNonNullElse", "тернарный оператор", "Objects", None),
    (r"\.\s*ifPresentOrElse\s*\(", 9, "Optional.ifPresentOrElse", "isPresent() и get()", None, 2),
    (r"\bStream\s*\.\s*ofNullable\s*\(", 9, "Stream.ofNullable", "x == null ? Stream.empty() : Stream.of(x)", "Stream", None),
    (r"\.\s*orElseThrow\s*\(", 10, "Optional.orElseThrow()", "orElseThrow(NoSuchElementException::new) или get()", None, 0),
    (r"\b(?:List|Set|Map)\s*\.\s*copyOf\s*\(", 10, "List/Set/Map.copyOf", "Collections.unmodifiableList(new ArrayList<>(...))", None, None),
    (r"(?<![\w$.])var\s+[A-Za-z_$][\w$]*\s*[=:]", 10, "var", "явный тип переменной", None, None),
    (r"\bCollectors\s*\.\s*toUnmodifiable(?:List|Set|Map)\s*\(", 10, "Collectors.toUnmodifiable*", "Collectors.toList() и Collections.unmodifiableList", "Collectors", None),
    (r"\.\s*strip(?:Leading|Trailing)?\s*\(", 11, "String.strip()", "trim()", None, 0),
    (r"\.\s*repeat\s*\(", 11, "String.repeat", "цикл со StringBuilder", None, 1),
    (r"\bFiles\s*\.\s*(?:readString|writeString)\s*\(", 11, "Files.readString/writeString", "new String(Files.readAllBytes(p), UTF_8), Files.write", "Files", None),
    (r"\bPath\s*\.\s*of\s*\(", 11, "Path.of", "Paths.get", "Path", None),
    (r"\bPredicate\s*\.\s*not\s*\(", 11, "Predicate.not", "лямбда с !", "Predicate", None),
    (r"\bjava\s*\.\s*net\s*\.\s*http\b", 11, "java.net.http", "HttpURLConnection или клиент проекта", None, None),
    (r"\bCollectors\s*\.\s*teeing\s*\(", 12, "Collectors.teeing", "два прохода по данным", "Collectors", None),
    (r"\bcase\b[^:;{}]*->", 14, "switch с ->", "case ...: с break", None, None),
    (r'"\s*\.\s*formatted\s*\(', 15, "String.formatted", "String.format", None, None),
    (r"\binstanceof\s+[\w.]+(?:<[^<>]*>)?\s+[a-z_$][\w$]*\s*[)&|;?:]", 16, "instanceof с переменной", "instanceof и явное приведение", None, None),
    (r"\brecord\s+[A-Z][\w$]*\s*[(<]", 16, "record", "обычный класс", None, None),
    (r"\.\s*stream\s*\(\s*\)[^;]*\.\s*toList\s*\(\s*\)", 16, "Stream.toList()", "collect(Collectors.toList())", None, None),
    (r"\bsealed\s+(?:class|interface)\b", 17, "sealed", "обычный класс", None, None),
)
_JAVA_TEXT_BLOCK = re.compile(r'"""[ \t]*\r?\n')


_CALL_TOKENS = re.compile(r"[()\[\]{},;]")


def _call_arguments(masked: str, start: int) -> int | None:
    """Top-level arguments of the call whose ``(`` ends at ``start``; None when the parenthesis never closes."""
    # only brackets, commas and semicolons matter, so the scan jumps between them — an unclosed call no longer walks
    # the rest of the file character by character from every match; a ``;`` outside brackets ends the statement.
    depth, commas = 0, 0
    for token in _CALL_TOKENS.finditer(masked, start):
        char = token.group()
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return 0 if not masked[start:token.start()].strip() else commas + 1
            depth -= 1
        elif char == "," and depth == 0:
            commas += 1
        elif char == ";" and depth == 0:
            return None
    return None


def _java_api_level_diagnostics(pointer: str, text: str, level: Mapping[str, Any]) -> list[dict[str, str]]:
    """RUNNER_API_LEVEL for each closed-list API newer than the module's Java level, with its replacement."""
    target, masked = int(level["level"]), _mask_java_noncode(text, keep_quotes=True)
    found = []
    for pattern, since, name, fix, receiver, arguments in _JAVA_API_RULES:
        # [ \t]*, not \s* — from every line start \s* ran over a whole blank (masked) tail and back: quadratic
        if since <= target or receiver and re.search(rf"^[ \t]*import\s+(?!java\.)[\w.]+\.{receiver}\s*;", masked, re.MULTILINE):
            continue
        if any(arguments is None or _call_arguments(masked, match.end()) == arguments for match in re.finditer(pattern, masked)):
            found.append((since, name, fix))
    if 15 > target and _JAVA_TEXT_BLOCK.search(text):
        found.append((15, 'текстовый блок """', "конкатенация строк"))
    source = "; ".join(level["evidence"])
    return [_diag(pointer, "RUNNER_API_LEVEL", f"{name} — API Java {since}, а модуль собирает тесты под Java {target} ({source}): "
                  f"замени на {fix}; используй только API Java {target}") for since, name, fix in found]


# a param_group of the canonical is one @ParameterizedTest whose rows are literals in the same class — the first
# argument of a row is its case ID, the rows follow the display order of the group.  Surefire writes an invocation as
# `method(Type, …)[N]` without the display name, so N is all the report gives on Maven; the literal rows prove N → case ID.
_PARAM_ROWS_FIX = ("группа param_group — один @ParameterizedTest(name = \"[{index}] {0}\") со строками-литералами в том же классе: "
                   "@MethodSource провайдер `static Stream<Arguments> rows() { return Stream.of(Arguments.of(\"<case ID>\", …), …); }` "
                   "или @CsvSource({\"<case ID>, …\", …}); первый аргумент строки — case ID, строки — все кейсы группы по display_order; "
                   "без вычислений, циклов, внешних вызовов, @CsvFileSource/@ValueSource/@ArgumentsSource и провайдера в другом классе")
_PARAMS_UNAVAILABLE_FIX = ("в зависимостях тестов модуля нет junit-jupiter-params (junit-jupiter, spring-boot-starter-test): "
                           "сделай по методу @Test на каждый кейс группы; зависимость пакет не добавляет")
_PARAM_TEST_NAME = re.compile(r'@\s*(?:org\s*\.\s*junit\s*\.\s*jupiter\s*\.\s*params\s*\.\s*)?ParameterizedTest\s*\(\s*name\s*=\s*"\[\{index\}\] \{0\}"\s*\)')
_PARAM_ANNOTATION = re.compile(r"@\s*(?:[\w$]+\s*\.\s*)*([A-Za-z_$][\w$]*)\b")
_PARAM_OTHER_SOURCES = frozenset({"CsvFileSource", "ValueSource", "ArgumentsSource", "EnumSource", "NullSource", "EmptySource",
                                  "NullAndEmptySource", "FieldSource", "ArgumentsSources", "MethodSources", "CsvSources"})
_PARAM_TOKEN = re.compile(r"\s+|(?P<string>\"(?:[^\"\\\n])*\")|(?P<char>'(?:[^'\\\n])')|(?P<number>-?(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)[lLfFdD]?)|(?P<name>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)|(?P<punct>[(){},;=])")
_ROW_FACTORIES = frozenset({"Arguments.of", "Arguments.arguments", "arguments", "org.junit.jupiter.params.provider.Arguments.of",
                            "org.junit.jupiter.params.provider.Arguments.arguments"})


def _param_tokens(raw: str) -> list[tuple[str, str]] | None:
    """Tokens of a literal-only fragment, or None when anything else (an operator, a comment, a text block) is there."""
    tokens, index = [], 0
    while index < len(raw):
        match = _PARAM_TOKEN.match(raw, index)
        if match is None:
            return None
        index = match.end()
        if match.lastgroup is not None:
            value = match.group(match.lastgroup)
            tokens.append((match.lastgroup, re.sub(r"\s+", "", value) if match.lastgroup == "name" else value))
    return tokens


def _literal_rows(tokens: list[tuple[str, str]]) -> list[str] | None:
    """First string literal of each `Arguments.of(<literals>)` of `{ return Stream.of(row, …); }`, or None."""
    head = [("punct", "{"), ("name", "return")]
    if len(tokens) < 8 or tokens[:2] != head or tokens[2] not in {("name", "Stream.of"), ("name", "java.util.stream.Stream.of")} or tokens[3:4] != [("punct", "(")]:
        return None
    if tokens[-3:] != [("punct", ")"), ("punct", ";"), ("punct", "}")]:
        return None
    ids, index, body = [], 4, tokens[:-3]
    while index < len(body):
        if body[index][0] != "name" or body[index][1] not in _ROW_FACTORIES or body[index + 1:index + 2] != [("punct", "(")]:
            return None
        index += 2
        literals = []
        while index < len(body) and body[index] != ("punct", ")"):
            if body[index][0] not in {"string", "char", "number"} and body[index] not in {("name", "true"), ("name", "false"), ("name", "null")}:
                return None
            literals.append(body[index])
            index += 1
            if index < len(body) and body[index] == ("punct", ","):
                index += 1
        if index >= len(body) or not literals or literals[0][0] != "string":
            return None
        ids.append(literals[0][1][1:-1])
        index += 1
        if index < len(body):
            if body[index] != ("punct", ","):
                return None
            index += 1
    return ids


def _csv_rows(raw: str) -> list[str] | None:
    """First column of each literal row of `@CsvSource({"…", …})` / `@CsvSource(value = {…})` / `@CsvSource("…")`, or None."""
    tokens = _param_tokens(raw)
    if not tokens or tokens[0] != ("punct", "(") or tokens[-1] != ("punct", ")"):
        return None
    inner = tokens[1:-1]
    if inner[:2] == [("name", "value"), ("punct", "=")]:
        inner = inner[2:]
    if inner[:1] == [("punct", "{")] and inner[-1:] == [("punct", "}")]:
        inner = inner[1:-1]
    strings = [token for token in inner if token != ("punct", ",")]
    if not strings or any(kind != "string" for kind, _value in strings) or len(inner) != 2 * len(strings) - 1:
        return None
    return [value[1:-1].split(",", 1)[0].strip().strip("'") for _kind, value in strings]


def _annotation_region(masked: str, start: int) -> int:
    """Where the annotations of the member declared at ``start`` begin: after the previous `;`, `}` or the class `{`."""
    depth, index = 0, start
    while index > 0:
        char = masked[index - 1]
        if char in ")}" and not (char == "}" and depth == 0):
            depth += 1
        elif char in "({" and depth > 0:
            depth -= 1
        elif depth == 0 and char in ";{}":
            break
        index -= 1
    return index


def _annotation_regions(masked: str, starts: Sequence[int]) -> dict[int, int]:
    """``_annotation_region`` of every start in one pass.  A walk that reaches an earlier start at depth 0 goes
    on exactly as the walk from that start did, so it takes that start's answer: linear instead of a walk per member."""
    regions: dict[int, int] = {}
    for start in sorted(set(starts)):
        depth, index = 0, start
        while index > 0:
            if depth == 0 and index != start and index in regions:
                index = regions[index]
                break
            char = masked[index - 1]
            if char in ")}" and not (char == "}" and depth == 0):
                depth += 1
            elif char in "({" and depth > 0:
                depth -= 1
            elif depth == 0 and char in ";{}":
                break
            index -= 1
        regions[start] = index
    return regions


@lru_cache(maxsize=4)
def _masked_java(text: str) -> tuple[str, tuple[int, ...]]:
    """The masked text once per file (not once per group) and where ``ParameterizedTest`` stands in it."""
    masked = _mask_java_noncode(text)
    return masked, tuple(match.start() for match in re.finditer("ParameterizedTest", masked))


def _declaration(masked: str, pattern: str) -> list[re.Match[str]]:
    return [match for match in re.finditer(pattern, masked) if _declared_after_type(masked, match.start("name"))]


def _param_group_rows(text: str, method: str) -> list[str] | None:
    """The case IDs of the literal rows of the @ParameterizedTest ``method`` of a generated Java class, or None."""
    masked, marks = _masked_java(text)
    # the test method, not a same-named provider of an unnamed @MethodSource: the one with @ParameterizedTest before it
    candidates = _declaration(masked, r"\b(?P<name>" + re.escape(method) + r")\s*\(")
    regions = _annotation_regions(masked, [match.start("name") for match in candidates])

    def marked(match: re.Match[str]) -> bool:
        start = match.start("name")
        at = bisect_left(marks, regions[start])
        return at < len(marks) and marks[at] + len("ParameterizedTest") <= start

    declarations = [match for match in candidates if marked(match)]
    if len(declarations) != 1:
        return None
    start = declarations[0].start("name")
    region_start = regions[start]
    raw_region, masked_region = text[region_start:start], masked[region_start:start]
    names = [match.group(1) for match in _PARAM_ANNOTATION.finditer(masked_region)]
    if not _PARAM_TEST_NAME.search(raw_region) or _PARAM_OTHER_SOURCES & set(names) or names.count("MethodSource") + names.count("CsvSource") != 1:
        return None
    if "CsvSource" in names:
        found = re.search(r"@\s*(?:[\w$]+\s*\.\s*)*CsvSource\s*(?=\()", masked_region)
        open_at = found.end()
        depth, close = 0, None
        for index in range(open_at, len(masked_region)):
            depth += {"(": 1, ")": -1}.get(masked_region[index], 0)
            if depth == 0:
                close = index + 1
                break
        return None if close is None else _csv_rows(raw_region[open_at:close])
    source = re.search(r"@\s*(?:[\w$]+\s*\.\s*)*MethodSource\b\s*(?:\(\s*(?:value\s*=\s*)?\"(?P<provider>[A-Za-z_$][\w$]*)\"\s*\))?", raw_region)
    if source is None or re.search(r"@\s*(?:[\w$]+\s*\.\s*)*MethodSource\s*\(", raw_region) and source.group("provider") is None:
        return None  # a provider in another class, several providers or a computed name
    provider = source.group("provider") or method
    # a bounded run between ``static`` and the name — the unbounded lazy one re-read the text from every ``static``
    found = _declaration(masked, r"\bstatic\b[^;{}()=]{0,200}?\b(?P<name>" + re.escape(provider) + r")\s*\(\s*\)\s*\{")
    if len(found) != 1:
        return None
    open_at = found[0].end() - 1
    depth = 0
    for index in range(open_at, len(masked)):
        depth += {"{": 1, "}": -1}.get(masked[index], 0)
        if depth == 0:
            tokens = _param_tokens(text[open_at:index + 1])
            return None if tokens is None else _literal_rows(tokens)
    return None


def _param_group_diagnostics(document: Mapping[str, Any], automation_artifact: Mapping[str, Any], texts: Mapping[str, str],
                             params: bool) -> tuple[list[dict[str, str]], dict[tuple[str, str], tuple[str, ...]]]:
    """RUNNER_PARAM_ROWS / RUNNER_PARAMS_UNAVAILABLE per Java param_group, and each group's (file, symbol) → case IDs."""
    from tools.canonical_document import param_groups

    relations = automation_artifact["artifacts"]["implementation_relations"]
    symbols = {(row["file_id"], row["symbol_id"]): row for row in automation_artifact["artifacts"]["generated_symbols"]}
    diagnostics: list[dict[str, str]] = []
    rows_by_pair: dict[tuple[str, str], tuple[str, ...]] = {}
    for group in param_groups(dict(document)):
        case_ids = [case["case_id"] for case in group["cases"]]
        pairs_of = {case_id: {(row["file_id"], row["symbol_id"]) for row in relations if row["case_id"] == case_id} for case_id in case_ids}
        bound = [case_id for case_id in case_ids if pairs_of[case_id]]
        if not bound:
            continue  # a group without automation (manual or blocked cases) has no rows to run
        pairs = set().union(*pairs_of.values())
        pointer = "/artifacts/implementation_relations"
        if len(bound) != len(case_ids):
            diagnostics.append(_diag(pointer, "RUNNER_PARAM_ROWS", f"{group['group_id']}: автоматизированы не все кейсы группы; {_PARAM_ROWS_FIX}"))
            continue
        if len(pairs) > 1:
            if params:
                diagnostics.append(_diag(pointer, "RUNNER_PARAM_ROWS", f"{group['group_id']}: кейсы группы связаны с {len(pairs)} методами; {_PARAM_ROWS_FIX}"))
            continue  # without junit-jupiter-params a method per case is the way
        (pair,) = pairs
        if not params:
            diagnostics.append(_diag(pointer, "RUNNER_PARAMS_UNAVAILABLE", f"{group['group_id']}: {_PARAMS_UNAVAILABLE_FIX}"))
            continue
        symbol = symbols.get(pair)
        text = texts.get(pair[0])
        locator = symbol["locator"] if symbol else {}
        rows = _param_group_rows(text, str(locator.get("method_name"))) if text is not None and locator.get("kind") == "java_class_method" else None
        if rows != case_ids:
            found = "строки не литералы или не тот вид" if rows is None else f"строки {rows}"
            diagnostics.append(_diag(pointer, "RUNNER_PARAM_ROWS", f"{group['group_id']}: {found}, ожидаются {case_ids}; {_PARAM_ROWS_FIX}"))
            continue
        rows_by_pair[pair] = tuple(case_ids)
    return diagnostics, rows_by_pair


# the bytes of generated code one runner-compat call reads.  The campaign's largest automation had
# 184 KB of code (spring-petclinic, one file of 62 methods); 1 MiB is ~5.7x that, and the checks stay linear up to it
# (the Java parsing; the @ParameterizedTest rows — one mask per file, one pass over the members, a
# bounded provider pattern: a degenerate 200 KB class in well under 5 s).
RUNNER_INPUT_MAX_BYTES = 1024 * 1024


def _runner_input_size(project: Path, automation_artifact: Mapping[str, Any], materialized: bool) -> dict[str, str] | None:
    """RUNNER_INPUT_TOO_LARGE when the generated files hold more than RUNNER_INPUT_MAX_BYTES; checked before anything is parsed."""
    from tools import output_guard

    files = (automation_artifact.get("artifacts") or {}).get("generated_files") if isinstance(automation_artifact, Mapping) else None
    if not isinstance(files, list):
        return None  # the shape is the automation validation's own refusal
    total = 0
    for row in files:
        if not isinstance(row, Mapping):
            continue
        if materialized:
            try:
                total += output_guard.project_file(Path(project), str(row.get("path"))).stat().st_size  # judged by its text first
            except (OSError, ValueError):
                continue
        elif isinstance(row.get("content"), str):
            total += len(row["content"].encode("utf-8"))
    if total <= RUNNER_INPUT_MAX_BYTES:
        return None
    return _diag("/artifacts/generated_files", "RUNNER_INPUT_TOO_LARGE",
                 f"сгенерированный код {total} Б больше предела {RUNNER_INPUT_MAX_BYTES} Б одной проверки; fix: раздели автоматизацию "
                 "— меньше кейсов на прогон (scan с более узким --target) или кейсы по нескольким файлам меньшего размера")


def validate_artifact_runner_compatibility(project: Path, language: Literal["python", "java"], document: Mapping[str, Any], automation_artifact: Mapping[str, Any], *, materialized: bool = True) -> RunnerCompatibility:
    """Check inline proposals before review, or exact on-disk bytes before execution.

    ``materialized=False`` never reads or writes generated files and proves only
    static compatibility. Execution callers keep the default on-disk verification.
    Source parsing is shared and bounded to this invocation, once per file.
    """
    from tools.automation_validation import AutomationArtifactError, required_symbol_pairs
    from tools.canonical_document import validate_canonical_document
    too_large = _runner_input_size(project, automation_artifact, materialized)
    if too_large is not None:
        return _compat("NOT_RUNNABLE", diagnostics=[too_large])
    canonical = validate_canonical_document(dict(document))
    if canonical:
        return _compat("NOT_RUNNABLE", diagnostics=[_diag(row["path"], row["code"], "Canonical document is invalid.") for row in canonical])
    try:
        pairs = required_symbol_pairs(dict(automation_artifact), dict(document))
    except AutomationArtifactError as error:
        return _compat("NOT_RUNNABLE", diagnostics=error.diagnostics)
    root, diagnostics, physical, paths, digests, parsed = project.resolve(), [], set(), {}, {}, {}
    expected = (language, "pytest" if language == "python" else "junit5")
    files = automation_artifact["artifacts"]["generated_files"]
    file_map = {row["file_id"]: row for row in files}
    symbols = automation_artifact["artifacts"]["generated_symbols"]
    texts: dict[str, str] = {}
    if language == "java":
        from tools.discover_project import java_api_level

        api_level = java_api_level(root)
    for index, row in enumerate(files):
        file = _path(root, row["path"]) if materialized else root / row["path"]
        pointer = f"/artifacts/generated_files/{index}"
        if file is None or file in physical:
            diagnostics.append(_diag(pointer + "/path", "RUNNER_FILE_CONFINEMENT", "Generated file is unavailable or escapes the project.")); continue
        physical.add(file)
        try:
            content = file.read_bytes() if materialized else row["content"].encode("utf-8")
        except OSError:
            diagnostics.append(_diag(pointer + "/path", "RUNNER_FILE_CONFINEMENT", "Generated file is unavailable or escapes the project.")); continue
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual != row["content_digest"]: diagnostics.append(_diag(pointer + "/content_digest", "RUNNER_FILE_DIGEST", "Generated file bytes do not match the artifact digest."))
        if (row["language"], row["framework"]) != expected: diagnostics.append(_diag(pointer, "RUNNER_LANGUAGE_FRAMEWORK", "Generated file is incompatible with the requested runner."))
        paths[row["file_id"]], digests[row["file_id"]] = file, actual
        try:
            text = content.decode("utf-8")
            texts[row["file_id"]] = text
            parsed[row["file_id"]] = ast.parse(text, filename=str(file)) if language == "python" else _java_class(file, text)
        except (UnicodeDecodeError, SyntaxError):
            parsed[row["file_id"]] = None
            continue
        if language == "java" and _java_class_display_name(file.stem, text):
            diagnostics.append(_diag(pointer, "RUNNER_CLASS_DISPLAY_NAME", _CLASS_DISPLAY_NAME_FIX))
        if language == "java" and api_level is not None:
            diagnostics.extend(_java_api_level_diagnostics(pointer, text, api_level))
    symbol_map = {(row["file_id"], row["symbol_id"]): (index, row) for index, row in enumerate(symbols)}
    bindings: dict[tuple[str, str], Mapping[str, Any]] = {}
    for pair in sorted(pairs):
        index, symbol = symbol_map.get(pair, (-1, None)); file = paths.get(pair[0])
        source = parsed.get(pair[0])
        node = None if symbol is None or source is None else (_python_node(source, symbol["locator"]) if language == "python" else _java_node(source, symbol["locator"]))
        if node is None:
            diagnostics.append(_diag(f"/artifacts/generated_symbols/{index}/locator", "RUNNER_LOCATOR", "Generated locator is absent or ambiguous.")); continue
        bindings[pair] = {"path": file, "relative_path": file_map[pair[0]]["path"], "locator": symbol["locator"], "node": node}
    if language == "java":
        from tools.discover_project import junit_params_available

        group_rows, param_rows = _param_group_diagnostics(document, automation_artifact, texts, junit_params_available(root))
        diagnostics.extend(group_rows)
        for pair, case_ids in param_rows.items():
            if pair in bindings:
                bindings[pair] = {**bindings[pair], "param_rows": case_ids}
    return _compat("NOT_RUNNABLE", diagnostics=diagnostics) if diagnostics else _compat("READY", pairs, bindings, digests)


def _source_changed_file_ids(process_evidence: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        file_id
        for row in process_evidence
        if row.get("kind") == "SOURCE_CHANGED" and isinstance(row.get("affected_file_ids"), list)
        for file_id in row["affected_file_ids"]
        if isinstance(file_id, str)
    }


def validate_process_evidence(process_evidence: Sequence[Mapping[str, Any]], run_id: str | None, source: Mapping[str, Any], exit_code: int | None, duration_sec: float | None, target: Mapping[str, Any] | None, prefix: str = "RUNNER", required_file_ids: Sequence[str] = ()) -> list[dict[str, str]]:
    """Validate one process outcome against its enclosing, authoritative run metadata."""
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    rows: list[dict[str, str]] = []
    profiles = {"pytest": (PYTEST,), "maven": (MAVEN, SYSTEM_MAVEN), "gradle": (GRADLE,)}
    known_file_ids = set(required_file_ids)
    source_changed_rows = 0
    ordinary_rows = 0
    for row in process_evidence:
        if row.get("run_id") != run_id: rows.append(_diag("/process_evidence", f"{prefix}_STALE_PROCESS_EVIDENCE", "Process evidence must belong to the current run."))
        if row.get("source_digest") != source.get("source_digest"): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_EVIDENCE_SOURCE", "Process evidence source digest does not match."))
        kind = row.get("kind")
        if kind != "SOURCE_CHANGED":
            ordinary_rows += 1
        if _PROCESS_ERROR_CLASSES.get(kind) != row.get("error_class"): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_CLASS", "Process kind and error class must match."))
        scope_stopped = row.get("process_scope_stopped")
        stop_proof = row.get("stop_proof")
        if (
            (scope_stopped is not None or stop_proof is not None)
            and not (
                kind == "TIMEOUT"
                and scope_stopped is True
                and stop_proof in {
                    "WINDOWS_JOB_OBJECT", "WINDOWS_TASKKILL_TREE", "POSIX_PROCESS_GROUP",
                }
            )
        ):
            rows.append(_diag(
                "/process_evidence", f"{prefix}_PROCESS_STOP_PROOF",
                "Process-stop proof must be a controller-confirmed timeout scope.",
            ))
        cause = row.get("exit_cause")
        if cause == "NOT_OBSERVED":  # a human ran the build; its exit code is not known to the package
            exit_valid = exit_code is None and kind in {"NO_TESTS_COLLECTED", "JUNIT_MISSING", "JUNIT_INVALID", "SOURCE_CHANGED", "BASELINE_DRIFT"}
        elif kind == "TIMEOUT":
            exit_valid = type(exit_code) is int and exit_code == 124 and cause == "TIMEOUT"
        elif kind == "OS_ERROR":
            exit_valid = type(exit_code) is int and exit_code == 127 and cause == "OS_ERROR"
        elif kind == "NO_TESTS_COLLECTED":
            exit_valid = type(exit_code) is int and exit_code >= 0 and (
                (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code > 0 and cause == "NONZERO_EXIT")
            )
        elif kind in {"COLLECTION_ERROR", "EXECUTION_ERROR"}:
            exit_valid = type(exit_code) is int and exit_code > 0 and cause == "NONZERO_EXIT"
        elif kind in {"JUNIT_MISSING", "JUNIT_INVALID"}:
            exit_valid = (type(exit_code) is int and exit_code == 0 and cause == "ZERO_EXIT") or (type(exit_code) is int and exit_code != 0 and cause == "NONZERO_EXIT")
        elif kind == "SOURCE_CHANGED":
            exit_valid = type(exit_code) is int and (
                (exit_code == 124 and cause == "TIMEOUT")
                or (exit_code == 127 and cause == "OS_ERROR")
                or (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code != 0 and cause == "NONZERO_EXIT")
            )
            source_changed_rows += 1
            affected = row.get("affected_file_ids")
            if not isinstance(affected, list) or not affected or any(not isinstance(file_id, str) for file_id in affected) or len(set(affected)) != len(affected) or (known_file_ids and not set(affected).issubset(known_file_ids)):
                rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_SOURCE_CHANGED", "Source change evidence must name distinct executed generated files."))
        elif kind == "BASELINE_DRIFT":
            exit_valid = type(exit_code) is int and (
                (exit_code == 124 and cause == "TIMEOUT")
                or (exit_code == 127 and cause == "OS_ERROR")
                or (exit_code == 0 and cause == "ZERO_EXIT")
                or (exit_code != 0 and cause == "NONZERO_EXIT")
            )
        else:
            exit_valid = False
        if not exit_valid:
            rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_EXIT", "Process cause must match the enclosing exit code."))
        if not isinstance(target, Mapping) or row.get("command_profile") != target.get("command") or row.get("command_profile") not in profiles.get(target.get("runner"), ()): rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_PROFILE", "Process profile must match the enclosing target."))
        if row.get("duration_sec") != duration_sec: rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_DURATION", "Process duration must match the enclosing run."))
        if row.get("stdout_tail") != "" or row.get("stderr_tail") != "": rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_OUTPUT", "Process output tails must be empty."))
    if source_changed_rows > 1:
        rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_SOURCE_CHANGED", "A run may record source mutation once."))
    if ordinary_rows > 1:
        rows.append(_diag("/process_evidence", f"{prefix}_PROCESS_CARDINALITY", "A run may record one runner outcome and one source mutation at most."))
    return rows


def validate_execution_evidence(verdict: str, run_id: str | None, source: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], authoritative: bool, required_pairs: Sequence[tuple[str, str]], verified_file_digests: Mapping[str, str] | None = None, exit_code: int | None = None, stats: Mapping[str, Any] | None = None, reported_diagnostics: Sequence[Mapping[str, Any]] | None = None, process_evidence: Sequence[Mapping[str, Any]] = (), target: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    required, diagnostics, seen = set(required_pairs), [], {}
    if verdict == "NOT_RUNNABLE" and run_id is None:
        return [_diag("/execution_evidence", "RUNNER_PRESTART_EVIDENCE", "Pre-start NOT_RUNNABLE cannot contain evidence.")] if evidence or process_evidence or authoritative else []
    factual_diagnostics = []
    if verdict == "UNKNOWN" and run_id is not None and not evidence and not process_evidence:
        factual_diagnostics.append(_diag(
            "/execution", "EXECUTION_RESULT_LOST",
            "Execution started, but no authoritative framework or process result was durably recovered.",
        ))
    source_changed = _source_changed_file_ids(process_evidence)
    for row in evidence:
        if row.get("run_id") != run_id:
            diagnostics.append(_diag("/execution_evidence", "RUNNER_STALE_EVIDENCE", "Evidence must belong to the current run.")); continue
        pair = row.get("file_id"), row.get("symbol_id")
        if pair not in required: diagnostics.append(_diag("/execution_evidence", "RUNNER_FOREIGN_EVIDENCE", "Current evidence names a non-required pair.")); continue
        if row.get("source_digest") != source.get("source_digest"): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_SOURCE", "Current evidence source digest does not match.")); continue
        if not verified_file_digests or row.get("file_digest") != verified_file_digests.get(pair[0]): diagnostics.append(_diag("/execution_evidence", "RUNNER_EVIDENCE_FILE_DIGEST", "Current evidence file digest is not verified.")); continue
        if pair[0] in source_changed: diagnostics.append(_diag("/execution_evidence", "RUNNER_SOURCE_CHANGED_EVIDENCE", "Source-changed files cannot retain execution evidence.")); continue
        seen.setdefault(pair, []).append(row)
    if any(len(rows) != 1 for rows in seen.values()): diagnostics.append(_diag("/execution_evidence", "RUNNER_DUPLICATE_EVIDENCE", "A required pair has duplicate or conflicting current evidence."))
    statuses = {pair: rows[0]["status"] for pair, rows in seen.items() if len(rows) == 1}
    diagnostics.extend(validate_process_evidence(process_evidence, run_id, source, exit_code, None if stats is None else stats.get("duration_sec"), target, required_file_ids=tuple(file_id for file_id, _ in required_pairs)))
    if verdict == "PASS" and exit_code is not None and exit_code != 0:
        diagnostics.append(_diag("/exit_code", "RUNNER_EXIT_CODE_VERDICT", "PASS requires a zero process exit code."))
    if verdict == "FAIL" and run_id is not None and not any(value in {"FAILED", "ERROR"} for value in statuses.values()) and not process_evidence:
        diagnostics.append(_diag("/execution_evidence", "RUNNER_FAIL_EVIDENCE", "FAIL requires FAILED or ERROR current evidence."))
    complete = (
        not diagnostics
        and not process_evidence
        and len(statuses) == len(required)
        and "SKIPPED" not in statuses.values()
    )
    zero_collect = (
        not diagnostics
        and not evidence
        and len(process_evidence) == 1
        and process_evidence[0].get("kind") == "NO_TESTS_COLLECTED"
        and isinstance(stats, Mapping)
        and all(stats.get(key) == 0 for key in ("total", "passed", "failed", "errors", "skipped"))
    )
    if zero_collect or complete and any(value in {"FAILED", "ERROR"} for value in statuses.values()):
        derived = "FAIL"
    elif complete and exit_code in (None, 0):
        derived = "PASS"
    else:
        # Once execution has started, incomplete or process-only evidence proves
        # neither product success nor exact-test failure.  The frozen contract
        # therefore classifies it as UNKNOWN.
        derived = "UNKNOWN"
    if verdict != derived: diagnostics.append(_diag("/verdict", "RUNNER_EVIDENCE_VERDICT", "Verdict does not match current execution evidence."))
    if authoritative != (complete or zero_collect): diagnostics.append(_diag("/evidence_authoritative", "RUNNER_EVIDENCE_AUTHORITATIVE", "Authoritative flag does not match current execution evidence."))
    if run_id is not None and stats is not None:
        current = [row for row in evidence if row.get("run_id") == run_id]
        expected_stats = {"total": len(current), **{name: sum(row.get("status") == status for row in current) for name, status in (("passed", "PASSED"), ("failed", "FAILED"), ("errors", "ERROR"), ("skipped", "SKIPPED"))}}
        if any(stats.get(name) != value for name, value in expected_stats.items()):
            diagnostics.append(_diag("/stats", "RUNNER_STATS", "Stats must exactly equal current execution evidence."))
    if run_id is not None and reported_diagnostics is not None:
        expected_rows = sorted(
            (dict(row) for row in [*diagnostics, *factual_diagnostics]),
            key=lambda row: (row["path"], row["code"], row["message"]),
        )
        actual_rows = [dict(row) for row in reported_diagnostics]
        if actual_rows != expected_rows:
            diagnostics.append(_diag("/diagnostics", "RUNNER_DIAGNOSTICS", "Diagnostics must exactly equal recomputed execution diagnostics."))
    return diagnostics


@dataclass(frozen=True)
class JUnitCase:
    classname: str
    name: str
    file: str | None
    status: str
    message: str = field(default="", compare=False)

    @property
    def identity(self) -> tuple[str, str, str | None]:
        return (self.classname, self.name, self.file)


@dataclass(frozen=True)
class JUnitParseResult:
    cases: tuple[JUnitCase, ...]
    malformed: tuple[str, ...]
    duplicate_identities: tuple[tuple[str, str, str | None], ...]


def _norm_junit_file(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value.replace("\\", "/")


def _testcase_status(item: ET.Element) -> str:
    if item.find("failure") is not None:
        return "FAILED"
    if item.find("error") is not None:
        return "ERROR"
    if item.find("skipped") is not None:
        return "SKIPPED"
    return "PASSED"


def masked_failure_text(text: str) -> str:
    """A line of framework output that looks like a secret (the check the runner output gets) is ``<redacted>``: the text of a failure goes
    into the receipt, the report and the annotation of a quarantined method."""
    from tools.pilot_state import canonical_runner_output

    lead = re.match(r"ASSERT-[A-Za-z0-9_.:-]+", text)  # the label of the assertion is the reference of the quarantine mark, not free text
    head, rest = (lead.group(0), text[lead.end():]) if lead else ("", text)
    masked = canonical_runner_output(rest, limit=500).decode("utf-8", errors="replace") if rest.strip() else rest
    return head + (" " + masked.strip() if head and masked.strip() == "<redacted>" else masked)


def _failure_message(item: ET.Element) -> str:
    """The first line of a failure or error, kept for the quarantine note (500 characters at most)."""
    found = item.find("failure")
    if found is None:
        found = item.find("error")
    if found is None:
        return ""
    text = (found.attrib.get("message") or found.text or "").strip()
    return masked_failure_text(text.splitlines()[0][:500]) if text else ""


def parse_junit_element(root: ET.Element) -> JUnitParseResult:
    cases: list[JUnitCase] = []
    malformed: list[str] = []
    seen: dict[tuple[str, str, str | None], int] = {}
    duplicates: list[tuple[str, str, str | None]] = []
    for item in root.iter("testcase"):
        name = item.attrib.get("name")
        if not isinstance(name, str) or not name:
            malformed.append("missing testcase name")
            continue
        classname = item.attrib.get("classname", "")
        row = JUnitCase(classname, name, _norm_junit_file(item.attrib.get("file")), _testcase_status(item), _failure_message(item))
        identity = row.identity
        count = seen.get(identity, 0) + 1
        seen[identity] = count
        if count == 2:
            duplicates.append(identity)
        cases.append(row)
    return JUnitParseResult(tuple(cases), tuple(malformed), tuple(duplicates))


def parse_junit_bytes(data: bytes) -> JUnitParseResult:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        return JUnitParseResult((), (str(error),), ())
    return parse_junit_element(root)


def junit_method_name(name: str) -> str:
    """The Java method of a JUnit testcase name — Surefire 3 writes an invocation `method(Type, …)[N]`, Surefire 2.22
    `method{Type, …}[N]`; the one reading of a name for the evidence and for the run_pipeline hint."""
    return re.split(r"[\[({]", name, maxsplit=1)[0]


def _case_matches_pair(case: JUnitCase, binding: Mapping[str, Any], java: bool) -> bool:
    loc = binding["locator"]
    if java:
        return case.classname == loc["class_fqn"] and junit_method_name(case.name) == loc["method_name"]
    base_name = case.name.split("[", 1)[0]
    relative = str(binding.get("relative_path") or Path(binding["path"]).as_posix()).replace("\\", "/")
    expected_module = relative.removesuffix(".py").replace("/", ".")
    class_module = expected_module if loc["kind"] == "python_module_function" else expected_module + "." + loc["qualified_class_name"]
    same_file = case.file == relative or (case.file is None and case.classname == class_module)
    expected_name = loc["function_name"] if loc["kind"] == "python_module_function" else loc["method_name"]
    return same_file and case.classname == class_module and base_name == expected_name


# an invocation of a group method — Surefire `method(Type, …)[N]`, Gradle `[N] <case ID>` (the display name).
_SUREFIRE_INVOCATION = re.compile(r"\[(\d+)\]$")
_DISPLAY_INVOCATION = re.compile(r"\[(\d+)\] (\S+)$")


def _group_invocation(case: JUnitCase) -> tuple[int, str | None] | None:
    """``(N, case ID in the name or None)`` of an invocation of a group method, or None when it names no row."""
    found = _DISPLAY_INVOCATION.fullmatch(case.name)
    if found is not None:
        return int(found.group(1)), found.group(2)
    found = _SUREFIRE_INVOCATION.search(case.name)
    return (int(found.group(1)), None) if found is not None else None


def _group_pair(case: JUnitCase, bindings: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Gradle names a group invocation `[N] <case ID>` without the method: the pair is the group of that class with that ID."""
    found = _DISPLAY_INVOCATION.fullmatch(case.name)
    if found is None:
        return []
    return [pair for pair, binding in bindings.items() if binding.get("param_rows") and case.classname == binding["locator"].get("class_fqn")
            and found.group(2) in binding["param_rows"]]


def classify_evidence(evidence: Sequence[Mapping[str, Any]], *, controller_timed_out: bool) -> str:
    """The verdict of the rows ``match_junit_cases`` gave for every required pair: an exact framework failure is authoritative
    even after a controller timeout; a clean report after it is not (the process end is unknown), nor is a skipped test."""
    if any(row["status"] in {"FAILED", "ERROR"} for row in evidence):
        return "FAIL"
    if controller_timed_out or any(row["status"] == "SKIPPED" for row in evidence):
        return "UNKNOWN"
    return "PASS"


def selected_classes_renamed(parsed: JUnitParseResult, selectors: Sequence[str]) -> set[str]:
    """The selected Java classes whose selected methods the report writes under another classname (what a
    class display name does) — read with the same ``junit_method_name`` as ``match_junit_cases``."""
    selected: dict[str, set[str]] = {}
    for selector in selectors:
        class_fqn, _, method = str(selector).partition("#")
        if method:
            selected.setdefault(method, set()).add(class_fqn)
    classes: set[str] = set()
    for case in parsed.cases:
        method = junit_method_name(case.name)
        if method in selected and case.classname not in selected[method]:
            classes |= selected[method]
    return classes


def match_junit_cases(
    parsed: JUnitParseResult,
    compatibility: RunnerCompatibility,
    run_id: str,
    source: Mapping[str, Any],
    java: bool = False,
    *,
    strict: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    if parsed.malformed:
        errors.append("malformed JUnit testcase")
    if parsed.duplicate_identities:
        errors.append("duplicate JUnit testcase identity")
    observed: dict[tuple[str, str], list[str]] = {pair: [] for pair in compatibility.required_pairs}
    invocations: dict[tuple[str, str], list[tuple[int, str | None, str]]] = {}
    unmatched: list[JUnitCase] = []
    ambiguous = False
    for case in parsed.cases:
        matches = [pair for pair, binding in compatibility.bindings.items() if _case_matches_pair(case, binding, java)]
        if java and not matches:
            matches = _group_pair(case, compatibility.bindings)
        if len(matches) == 1:
            observed[matches[0]].append(case.status)
            binding = compatibility.bindings[matches[0]]
            if binding.get("param_rows"):
                row = _group_invocation(case)
                if row is None:
                    errors.append("parameterized invocation without its row index")
                else:
                    invocations.setdefault(matches[0], []).append((row[0], row[1], case.status))
        elif len(matches) > 1:
            ambiguous = True
        else:
            unmatched.append(case)
    if ambiguous:
        errors.append("ambiguous JUnit testcase")
    # a group method answers each of its rows exactly once, row N being the N-th case ID of the literal rows
    group_rows: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    for pair, binding in compatibility.bindings.items():
        rows = binding.get("param_rows")
        seen = invocations.get(pair, [])
        if not rows or not observed.get(pair):
            continue
        indices = [index for index, _name_id, _status in seen]
        if sorted(indices) != list(range(1, len(rows) + 1)):
            errors.append("parameterized rows do not match the group (missing, extra or repeated row index)")
            continue
        if any(name_id is not None and name_id != rows[index - 1] for index, name_id, _status in seen):
            errors.append("parameterized row names another case ID than the group order")
            continue
        group_rows[pair] = [{"index": index, "case_id": rows[index - 1], "status": status} for index, _name_id, status in sorted(seen)]
    if strict and unmatched:
        errors.append("unmatched JUnit testcase")
    if strict:
        missing = [pair for pair, values in observed.items() if not values]
        if missing:
            errors.append("missing required pair")
    severity = {"PASSED": 0, "SKIPPED": 1, "FAILED": 2, "ERROR": 3}
    rows = []
    for pair, values in observed.items():
        if values:
            rows.append(
                {
                    "run_id": run_id,
                    "source_digest": source["source_digest"],
                    "file_id": pair[0],
                    "symbol_id": pair[1],
                    "file_digest": compatibility.verified_file_digests[pair[0]],
                    "status": max(values, key=lambda value: severity[value]),
                    **({"rows": group_rows[pair]} if pair in group_rows else {}),
                }
            )
    return rows, errors


def _report_snapshots(paths: Sequence[Path]) -> dict[Path, bytes]:
    """Read each fresh framework report once; all parsing and digests use these bytes."""
    snapshots: dict[Path, bytes] = {}
    total = 0
    for path in paths:
        try:
            with path.open("rb") as stream:
                content = stream.read(NATIVE_REPORT_MAX_BYTES + 1)
            total += len(content)
            if len(content) > NATIVE_REPORT_MAX_BYTES or total > NATIVE_REPORT_SET_MAX_BYTES:
                # An invalid sentinel rejects the whole report set, never a partial PASS.
                return {path.resolve(): b""}
            snapshots[path.resolve()] = content
        except OSError:
            continue
    return snapshots


def _normalize_durable_junit_report(content: bytes) -> bytes:
    """Keep only JUnit identity, counts, and outcome tags in canonical XML."""
    from tools.project_inventory import token_signature_rule

    if len(content) > NATIVE_REPORT_MAX_BYTES:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_LIMIT")
    try:
        source = ET.fromstring(content)
    except ET.ParseError as error:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_INVALID") from error
    if source.tag not in {"testsuite", "testsuites"}:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_INVALID")
    if any(
        token_signature_rule(item.get(key, "").encode("utf-8"))
        for item in source.iter() for key in ("name", "classname", "file", "tests", "failures", "errors", "skipped", "disabled")
    ):
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_SECRET")

    def normalized_suite(item: ET.Element) -> ET.Element:
        attributes = {
            key: item.attrib[key]
            for key in ("name", "tests", "failures", "errors", "skipped", "disabled")
            if key in item.attrib
        }
        result = ET.Element(item.tag, attributes)
        for child in item:
            if child.tag == "testsuite":
                result.append(normalized_suite(child))
            elif child.tag == "testcase":
                case_attributes = {
                    key: child.attrib[key]
                    for key in ("classname", "name", "file")
                    if key in child.attrib
                }
                case = ET.SubElement(result, "testcase", case_attributes)
                for status in ("failure", "error", "skipped"):
                    if child.find(status) is not None:
                        ET.SubElement(case, status)
        return result

    data = ET.tostring(
        normalized_suite(source), encoding="utf-8", xml_declaration=True,
        short_empty_elements=True,
    ) + b"\n"
    if len(data) > DURABLE_NATIVE_REPORT_MAX_BYTES:
        raise DurableNativeReportError("RUNNER_NATIVE_REPORT_LIMIT")
    return data


def _durable_execution_artifacts(
    snapshots: Mapping[Path, bytes],
    module: Path,
    generated_delta_receipt: Mapping[str, Any],
    outcome: ProcessOutcome,
    run_root: Path | None,
    attempt_id: str | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Copy runner reports and limited output into the run before cleanup."""
    reports = _report_digest_evidence(snapshots, module)
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return reports, []
    from tools.pilot_state import _canonical_runner_output, publish_run_artifact_bytes

    artifacts: list[dict[str, str]] = []
    used_names: dict[str, int] = {}
    prepared_reports = [
        (report, path, _normalize_durable_junit_report(content))
        for report, (path, content) in zip(
            reports, sorted(snapshots.items(), key=lambda row: str(row[0])),
        )
    ]
    for report, path, content in prepared_reports:
        name = path.name or "report.xml"
        count = used_names.get(name, 0)
        used_names[name] = count + 1
        if count:
            stem = Path(name).stem
            suffix = Path(name).suffix
            name = f"{stem}-{count}{suffix}"
        published = publish_run_artifact_bytes(run_root, attempt_id, f"reports/{name}", content)
        artifacts.append({
            "kind": "native_report", "source_path": report["path"],
            "source_digest": report["digest"],
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": published["path"], "digest": published["digest"],
        })
    output = _canonical_runner_output(f"{outcome.stdout or ''}\n{outcome.stderr or ''}")
    if output:
        published = publish_run_artifact_bytes(run_root, attempt_id, "runner-output.txt", output)
        artifacts.append({
            "kind": "runner_output", "path": published["path"],
            "digest": published["digest"],
        })
    delta = generated_delta_receipt.get("delta")
    files = delta.get("files") if isinstance(delta, Mapping) else None
    for row in files if isinstance(files, list) else ():
        relative = row.get("path") if isinstance(row, Mapping) else None
        expected_digest = row.get("content_digest") if isinstance(row, Mapping) else None
        if not isinstance(relative, str) or row.get("materialization") != "MATERIALIZED":
            continue
        source = module / Path(relative)
        try:
            data = source.read_bytes()
        except OSError:
            continue
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if digest != expected_digest:
            continue
        published = publish_run_artifact_bytes(
            run_root, attempt_id, "generated/" + relative.replace("\\", "/"), data,
        )
        artifacts.append({
            "kind": "generated_test", "source_path": relative.replace("\\", "/"),
            "path": published["path"], "digest": published["digest"],
        })
    return reports, artifacts


def _report_digest_evidence(snapshots: Mapping[Path, bytes], module: Path) -> list[dict[str, str]]:
    """Record only fresh framework-report paths and digests, never report contents."""
    rows: list[dict[str, str]] = []
    for path, content in sorted(snapshots.items(), key=lambda row: str(row[0])):
        try:
            relative = path.resolve().relative_to(module.resolve()).as_posix()
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
        except ValueError:
            continue
        rows.append({"path": relative, "digest": digest})
    return rows


def _records_from_snapshots(snapshots: Mapping[Path, bytes], compatibility: RunnerCompatibility, run_id: str, source: Mapping[str, Any], *, java: bool) -> tuple[list[dict[str, str]], list[str]]:
    cases: list[JUnitCase] = []
    malformed: list[str] = []
    duplicates: list[tuple[str, str, str | None]] = []
    identities: set[tuple[str, str, str | None]] = set()
    for content in snapshots.values():
        try:
            parsed = parse_junit_element(ET.fromstring(content))
        except ET.ParseError:
            malformed.append("invalid JUnit report")
            continue
        for case in parsed.cases:
            if case.identity in identities:
                duplicates.append(case.identity)
            identities.add(case.identity)
            cases.append(case)
        malformed.extend(parsed.malformed)
    return match_junit_cases(
        JUnitParseResult(tuple(cases), tuple(malformed), tuple(duplicates)),
        compatibility, run_id, source, java, strict=True,
    )


def _failed_methods(snapshots: Mapping[Path, bytes], compatibility: RunnerCompatibility, evidence: Sequence[Mapping[str, Any]], *, java: bool) -> list[dict[str, str]]:
    """One row per failed generated method of the authoritative evidence, with its framework message."""
    messages: dict[tuple[str, str], str] = {}
    for content in snapshots.values():
        try:
            parsed = parse_junit_element(ET.fromstring(content))
        except ET.ParseError:
            continue
        for case in parsed.cases:
            if case.status not in {"FAILED", "ERROR"}:
                continue
            for pair, binding in compatibility.bindings.items():
                if _case_matches_pair(case, binding, java):
                    messages.setdefault(pair, case.message)
    return [
        {"nodeid": f"{row['file_id']}::{row['symbol_id']}", "kind": "assertion_failed" if row["status"] == "FAILED" else "error",
         "message": messages.get((row["file_id"], row["symbol_id"]), ""),
         **({"failed_rows": [item["case_id"] for item in row["rows"] if item["status"] in {"FAILED", "ERROR"}]} if row.get("rows") else {})}
        for row in evidence if row["status"] in {"FAILED", "ERROR"}
    ]


def _post_run_file_digests(compatibility: RunnerCompatibility) -> Mapping[str, str]:
    """Re-read every executed generated file before accepting its evidence."""
    values: dict[str, str] = {}
    paths = {pair[0]: binding["path"] for pair, binding in compatibility.bindings.items()}
    for file_id, path in paths.items():
        try:
            values[file_id] = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            continue
    return MappingProxyType(values)


def _changed_file_ids(compatibility: RunnerCompatibility, post_run_digests: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(file_id for file_id, digest in compatibility.verified_file_digests.items() if post_run_digests.get(file_id) != digest))


def _process_row(kind: str, outcome: ProcessOutcome, run_id: str, source: Mapping[str, Any], command_profile: str, duration_sec: float, affected_file_ids: Sequence[str] = ()) -> dict[str, Any]:
    row = {
        "run_id": run_id, "source_digest": source["source_digest"], "kind": kind,
        "error_class": _PROCESS_ERROR_CLASSES[kind],
        "exit_cause": "TIMEOUT" if outcome.kind == "TIMEOUT" else "OS_ERROR" if outcome.kind == "OS_ERROR" else "NOT_OBSERVED" if outcome.kind == "NOT_OBSERVED" else "ZERO_EXIT" if outcome.exit_code == 0 else "NONZERO_EXIT",
        "command_profile": command_profile, "duration_sec": duration_sec,
        "stdout_tail": "", "stderr_tail": "",
    }
    if kind == "SOURCE_CHANGED":
        row["affected_file_ids"] = list(affected_file_ids)
    return row


def _stats(evidence: Sequence[Mapping[str, Any]], duration_sec: float | None = None) -> dict[str, Any]:
    count = {value: sum(row["status"] == value for row in evidence) for value in ("PASSED", "FAILED", "ERROR", "SKIPPED")}
    return {"total": sum(count.values()), "passed": count["PASSED"], "failed": count["FAILED"], "errors": count["ERROR"], "skipped": count["SKIPPED"], "duration_sec": duration_sec}


def _execution_receipt(request: Any, report_evidence: Sequence[Mapping[str, str]], artifact_evidence: Sequence[Mapping[str, str]], *, run_root: Path | None = None, attempt_id: str | None = None) -> dict[str, Any]:
    """Project only launch-safe request facts into a portable V5 execution receipt."""
    from tools.execution_adapters import ExecutionRequest, request_digest

    if not isinstance(request, ExecutionRequest):
        return {"adapter_id": None, "executable_path": None, "cwd": None, "run_id": None, "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None, "build_profile": None, "typed_parameters": {}, "argv": [], "selectors": [], "timeout_seconds": None, "report_paths": [], "request_digest": None, "report_evidence": [], "artifact_evidence": []}
    binding = {"run_id": None, "attempt_id": None, "baseline_digest": None, "generated_delta_digest": None}
    if isinstance(run_root, Path) and isinstance(attempt_id, str):
        try:
            from tools.pilot_state import derive_state, read_attempt_receipt, read_run

            run = read_run(run_root)
            attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
            delta = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
            binding = {"run_id": run["manifest"]["run_id"], "attempt_id": attempt_id, "baseline_digest": attempt["baseline_digest"], "generated_delta_digest": delta["digest"]}
        except (KeyError, OSError, StopIteration, TypeError, ValueError):
            pass
    return {
        "adapter_id": request.adapter_id,
        "executable_path": request.executable,
        "cwd": request.cwd,
        **binding,
        "build_profile": request.build_profile,
        "typed_parameters": dict(request.typed_parameters),
        "argv": list(request.argv),
        "selectors": list(request.selectors),
        "timeout_seconds": request.timeout_seconds,
        "report_paths": list(request.report_paths),
        "request_digest": request_digest(request),
        "report_evidence": [dict(row) for row in report_evidence],
        "artifact_evidence": [dict(row) for row in artifact_evidence],
    }


def _report(verdict: str, project: Path, language: str, source: Mapping[str, Any], automation_digest: str, review_digest: str, diagnostics: Sequence[Mapping[str, str]] = (), run_id: str | None = None, evidence: Sequence[Mapping[str, Any]] = (), process_evidence: Sequence[Mapping[str, Any]] = (), authoritative: bool = False, exit_code: int | None = None, runner: str = "not_applicable", interpreter: str | None = None, command_profile: str | None = None, duration_sec: float | None = None, safe_key_labels: Sequence[str] = (), *, request: Any = None, report_evidence: Sequence[Mapping[str, str]] = (), artifact_evidence: Sequence[Mapping[str, str]] = (), run_root: Path | None = None, attempt_id: str | None = None, failed_methods: Sequence[Mapping[str, str]] | None = None, execution_source: Mapping[str, Any] | None = None, ran_at: str | None = None) -> dict[str, Any]:
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST

    prestart = verdict == "NOT_RUNNABLE" and run_id is None
    request_backed = _request_report_path(request) is not None
    if request_backed:
        runner, command_profile = {
            PYTEST: ("pytest", PYTEST),
            MAVEN: ("maven", MAVEN),
            SYSTEM_MAVEN: ("maven", SYSTEM_MAVEN),
            GRADLE: ("gradle", GRADLE),
        }[request.adapter_id]
        interpreter = request.executable
        safe_key_labels = request.environment_labels
    source = dict(source)
    source.setdefault("effective_bundle_receipt_digest", "sha256:" + "0" * 64)
    report = {"schema_version": "5.0.0", "stage": "run-tests", "source": source, "automation_sha256": automation_digest, "autotest_review_sha256": review_digest, "verdict": verdict, "target": {"language": language, "framework": "pytest" if language == "python" else "junit5", "runner": runner, "command": command_profile}, "environment": {"status": "ready" if request_backed or not prestart else "partial", "interpreter": interpreter, "interpreter_path": interpreter, "working_dir": str(project), "missing": None if request_backed or not prestart else ["runner_precondition"], "safe_key_labels": sorted(set(safe_key_labels))}, "execution": _execution_receipt(request, report_evidence, artifact_evidence, run_root=run_root, attempt_id=attempt_id), "stats": _stats(evidence, duration_sec) if run_id else None, "failed_methods": [dict(row) for row in failed_methods] if failed_methods else None, "root_cause": None, "raw_output_excerpt": None, "ran_at": ran_at or datetime.now(timezone.utc).isoformat(), "exit_code": exit_code, "run_id": run_id, "execution_evidence": [dict(row) for row in sorted(evidence, key=lambda row: (row["file_id"], row["symbol_id"]))], "process_evidence": [dict(row) for row in process_evidence], "evidence_authoritative": authoritative, "diagnostics": [dict(row) for row in sorted(diagnostics, key=lambda row: (row["path"], row["code"], row["message"]))],
              **({"execution_source": dict(execution_source)} if execution_source is not None else {})}  # the run was a human's, not ours
    from tools.schema_validation import schema_diagnostics
    if schema_diagnostics(report, _ROOT / "schemas" / "run-tests-output.schema.json", _ROOT):
        raise RuntimeError("RUNNER_REPORT_SCHEMA")
    return report


def _authorization_allows_execution(receipt: Any, run_root: Path | None) -> bool:
    """Accept only the sealed full-pipeline authorization bound to this run root."""
    from tools.schema_validation import schema_diagnostics

    if not isinstance(receipt, Mapping):
        return False
    value = dict(receipt)
    if schema_diagnostics(value, _ROOT / "schemas" / "run-authorization-receipt.schema.json", _ROOT):
        return False
    digest = value.pop("digest", None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if not (
        digest == "sha256:" + hashlib.sha256(encoded).hexdigest()
        and value["policy_profile"] == "local-pilot-v1"
        and value["execution_requested"] is True
    ):
        return False
    try:
        from tools.pilot_state import read_run

        bound = read_run(Path(run_root))["authorization"] if run_root is not None else None
    except (OSError, TypeError, ValueError, KeyError):
        return False
    return isinstance(bound, Mapping) and dict(bound) == dict(receipt)


def _durable_delta_allows_execution(
    receipt: Any,
    project: Path,
    run_root: Path | None,
    attempt_id: str | None,
    automation_digest: str,
    review_digest: str,
    automation_artifact: Mapping[str, Any],
) -> bool:
    """Require the exact attempt-owned materialization readback, never caller data."""
    try:
        from tools.generated_delta import inspect_delta
        from tools.pilot_state import read_attempt_receipt

        durable = read_attempt_receipt(Path(run_root), str(attempt_id), "generated-delta", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if not isinstance(receipt, Mapping) or dict(receipt) != dict(durable):
        return False
    delta = durable.get("delta")
    artifacts = automation_artifact.get("artifacts") if isinstance(automation_artifact, Mapping) else None
    files = artifacts.get("generated_files") if isinstance(artifacts, Mapping) else None
    if not isinstance(delta, Mapping) or not isinstance(files, list):
        return False
    expected = [(row.get("file_id"), row.get("path"), row.get("content_digest")) for row in files if isinstance(row, Mapping)]
    actual_files = delta.get("files")
    actual = [
        (row.get("file_id"), row.get("path"), row.get("content_digest"))
        for row in actual_files if isinstance(row, Mapping)
    ] if isinstance(actual_files, list) else []
    facts = delta.get("facts")
    return (
        expected == actual
        and delta.get("automation_digest") == automation_digest
        and delta.get("review_digest") == review_digest
        and isinstance(facts, Mapping)
        and facts.get("completion") == "COMPLETE"
        and facts.get("verification") is None
        and all(row.get("materialization") == "MATERIALIZED" and row.get("disposition") is None for row in actual_files)
        and inspect_delta(project, delta) == {"valid": True}
    )


def _attempt_execution_scope(run_root: Path | None, attempt_id: str | None) -> tuple[Path, Path] | None:
    """Recover the immutable project/module pair; callers never choose this scope."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        return None
    try:
        from tools.pilot_state import derive_state, read_run

        run = read_run(run_root)
        manifest = run["manifest"]
        attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
        project = Path(str(manifest["project"])).resolve(strict=True)
        module_name = str(attempt["module"])
        raw_module = Path(module_name)
        if raw_module.is_absolute() or ".." in raw_module.parts:
            return None
        module = (project / raw_module).resolve(strict=True)
        module.relative_to(project)
        if not module.is_dir():
            return None
        return project, module
    except (KeyError, OSError, StopIteration, TypeError, ValueError):
        return None


def _request_is_attempt_local(request: Any, project: Path, module: Path) -> bool:
    """Reject cross-project replay and undeclared host tools before a process can start."""
    from tools.execution_adapters import ExecutionRequest, SYSTEM_MAVEN
    from tools.project_inventory import module_runtime_path, system_maven_path

    if not isinstance(request, ExecutionRequest):
        return False
    try:
        cwd = Path(request.cwd).resolve(strict=True)
        if request.adapter_id == SYSTEM_MAVEN:
            executable = system_maven_path(request.executable)
            if str(executable) != request.executable:
                return False
        else:
            relative = Path(request.executable).relative_to(module).as_posix()
            executable = module_runtime_path(module, relative)
    except (OSError, ValueError):
        return False
    return cwd == module and executable.is_file() and module.is_relative_to(project)


def validate_execution_eligibility(
    request: Any,
    authorization_receipt: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
    autotest_review_artifact: Mapping[str, Any],
    *,
    generated_delta_receipt: Mapping[str, Any] | None,
    run_root: Path | None,
    attempt_id: str | None,
) -> Mapping[str, Any]:
    """Perform durable, process-free Phase 6 admission checks for one attempt."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256

    scope = _attempt_execution_scope(run_root, attempt_id)
    if scope is None:
        return {"ready": False, "reason_code": "RUNNER_ATTEMPT_SCOPE"}
    project, module = scope
    if not _authorization_allows_execution(authorization_receipt, run_root):
        return {"ready": False, "reason_code": "RUNNER_AUTHORIZATION"}
    automation_digest = automation_sha256(automation_artifact)
    review_digest = autotest_review_sha256(autotest_review_artifact)
    if not _durable_delta_allows_execution(
        generated_delta_receipt, project, run_root, attempt_id, automation_digest, review_digest, automation_artifact,
    ):
        return {"ready": False, "reason_code": "RUNNER_GENERATED_DELTA"}
    if not _request_is_attempt_local(request, project, module):
        return {"ready": False, "reason_code": "RUNNER_REQUEST_PROVENANCE"}
    if _request_report_path(request) is None:
        return {"ready": False, "reason_code": "RUNNER_REQUEST"}
    return {"ready": True, "project": project, "module": module}


def build_closed_execution_request(
    execution_root: Path,
    language: Literal["python", "java"],
    module: Mapping[str, Any],
    canonical_document: Mapping[str, Any],
    automation_artifact: Mapping[str, Any],
) -> tuple[Any, RunnerCompatibility]:
    """Bind one reviewed artifact to the selected module's closed adapter request.

    This is deliberately the sole public construction seam: callers cannot supply
    argv, a command template, or a process environment.
    """
    from tools.execution_adapters import (
        GRADLE,
        MAVEN,
        SYSTEM_MAVEN,
        PYTEST,
        AdapterRequestError,
        build_request,
    )

    test = module.get("test") if isinstance(module, Mapping) else None
    adapter_id = test.get("adapter_id") if isinstance(test, Mapping) else None
    expected = {"python": {PYTEST}, "java": {MAVEN, SYSTEM_MAVEN, GRADLE}}[language]
    if adapter_id not in expected:
        raise RunnerInputError(
            "RUNNER_ADAPTER",
            [_diag("/.skillsrc/test/adapter_id", "RUNNER_ADAPTER", "Selected module has no compatible closed adapter.")],
        )
    compatibility = validate_artifact_runner_compatibility(
        execution_root.resolve(), language, canonical_document, automation_artifact
    )
    if compatibility.status != "READY":
        raise RunnerInputError("RUNNER_COMPATIBILITY", compatibility.diagnostics)
    selectors = []
    for pair in compatibility.required_pairs:
        binding = compatibility.bindings[pair]
        if language == "python":
            selector = f"{binding['relative_path']}::{binding['node']}"
        else:
            locator = binding["locator"]
            selector = f"{locator['class_fqn']}#{locator['method_name']}"
        selectors.append({"selector": selector})
    try:
        request_module = dict(module)
        request_module["module_root"] = str(execution_root.resolve())
        # ``framework`` establishes manifest/schema selection; the adapter seam
        # intentionally accepts only launch-significant closed fields.
        runtime_key = "interpreter" if adapter_id == PYTEST else "executable" if adapter_id == SYSTEM_MAVEN else "wrapper"
        request_module["test"] = {
            "adapter_id": test["adapter_id"],
            "build_profile": test["build_profile"],
            "adapter_parameters": test["adapter_parameters"],
            runtime_key: test[runtime_key],
        }
        return build_request(adapter_id, request_module, selectors), compatibility
    except AdapterRequestError as error:
        raise RunnerInputError(
            "RUNNER_REQUEST",
            [_diag("/.skillsrc/test", "RUNNER_REQUEST", "Closed module execution request is unavailable.")],
        ) from error


def _request_report_path(request: Any) -> Path | None:
    from tools.execution_adapters import GRADLE, MAVEN, SYSTEM_MAVEN, PYTEST, SAFE_ENVIRONMENT_LABELS, ExecutionRequest, command_for

    if not isinstance(request, ExecutionRequest) or request.adapter_id not in {PYTEST, MAVEN, SYSTEM_MAVEN, GRADLE}:
        return None
    if not request.argv or request.argv[0] != request.executable or request.timeout_seconds <= 0 or len(request.selectors) != len(set(request.selectors)):
        return None
    if set(request.environment_labels) - SAFE_ENVIRONMENT_LABELS or request.environment_labels != ("PROJECT_NATIVE_ENV",):
        return None
    if request.adapter_id in {MAVEN, SYSTEM_MAVEN, GRADLE} and not request.build_profile:
        return None
    try:
        cwd = Path(request.cwd).resolve(strict=True)
    except OSError:
        return None
    if not cwd.is_dir() or len(request.report_paths) != 1:
        return None
    raw_report = Path(request.report_paths[0])
    if "\x00" in request.report_paths[0] or raw_report.is_absolute() or ".." in raw_report.parts:
        return None
    report = (cwd / raw_report).resolve()
    try:
        report.relative_to(cwd)
    except ValueError:
        return None
    reports, expected = command_for(request.adapter_id, request.executable, request.build_profile, request.selectors)
    return report if request.argv == expected and request.report_paths == reports and request.selectors else None


# Public names for execution_core: the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.
runner_report = _report
runner_diag = _diag
process_evidence_row = _process_row
report_snapshots = _report_snapshots
records_from_snapshots = _records_from_snapshots
report_digest_evidence = _report_digest_evidence
durable_execution_artifacts = _durable_execution_artifacts
request_report_path = _request_report_path
request_is_attempt_local = _request_is_attempt_local
attempt_execution_scope = _attempt_execution_scope
authorization_allows_execution = _authorization_allows_execution
durable_delta_allows_execution = _durable_delta_allows_execution
changed_file_ids = _changed_file_ids
post_run_file_digests = _post_run_file_digests
failed_methods = _failed_methods
