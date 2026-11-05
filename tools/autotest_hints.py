"""FaT-Q: code hints on weak assertions in generated tests (Java and pytest) — never a stop, never a rewrite.

Reads the inline ``content`` of every generated file of a ``tc-to-autotest`` artifact with plain regular expressions,
without a compiler: an assertion that accepts any exception, one that is always true, a path prefix instead of an
exact value, an expected value that the test computes with the very call it checks (PQ4: ``AT_SELF_COMPUTED``), a rejected write after which nothing
shows that the data did not change (PQ4: ``AT_NO_STATE_CHECK``).  A hint names the file ID, the line, a short rule and the ASSERT ID on that line — never the code.

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

CODES = ("AT_ANY_EXCEPTION", "AT_ALWAYS_TRUE", "AT_PARTIAL_MATCH", "AT_SELF_COMPUTED", "AT_NO_STATE_CHECK")
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
# the three patterns below read to the end of the text from every start when nothing closes them (a cut-off or looping model answer: 100 KB
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
# how much of a test method that is never closed is read; its body is the rest of the file, and a cut-off file that opens 6 000 of them made that n squared
_UNCLOSED_BODY_LIMIT = 4096
_NESTED_BODY_LIMIT = 256  # the same for a test method found inside the body of another one
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
    and a comma before it, or straight after the parenthesis), as ``finditer`` found them for ``_JAVA_PATH`` (that pattern read to the end of the text
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


# --- an expected value computed by the call it checks; a rejected write whose state is not checked ----------------------------------------------

_ARGUMENT_WINDOW = 4000  # an assertion longer than this is not read: the scan of its arguments is bounded
_JAVA_EQUALS_CALL = re.compile(r"\bassert(?:Equals|ArrayEquals|IterableEquals)\s*\(")  # not assertSame, isSameAs: an identity check is about repeated calls
_JAVA_ASSERT_THAT = re.compile(r"\bassertThat\s*\(")
_JAVA_EQUALITY_IN_CHAIN = re.compile(r"\.\s*(?:isEqualTo|isEqualToIgnoringCase|isEqualByComparingTo|containsExactly|containsExactlyInAnyOrder)\s*\(")
_JAVA_MATCHER = re.compile(r"\s*(?:is|equalTo)\s*\(")  # used with ``match(code, start, end)``: no ``^`` (it would not stand at ``start``)
_VARIABLE = re.compile(r"[A-Za-z_$][\w$]*")
# a variable that says in its name that it is the expected value (the generator's habit); a variable called ``text`` or ``before`` that is read again after the call
# is the idiom «the answer is stable», which people write on purpose (commons-lang, jsoup: 9 of 10 hits were that)
_EXPECTED_NAME = re.compile(r"(?i)(?:expected|exp|want|wanted|should|reference|golden|baseline|oracle)[A-Z_0-9]?\w*|(?:expected|exp)$")
# a call (a chain of calls), not a cast or a parenthesised value: a name, a dotted path, an opening bracket, and a closing one at the very end. Read as a head
# match and an ``endswith``; the one-pattern form ``name(?:.name|(...))*(.*)`` backtracked over every ``(a)`` of ``f(a)(a)(a)…x`` and over every dot of ``a.a.a.…``
_CALL_HEAD = re.compile(r"[A-Za-z_$][\w$]*+(?:\.[A-Za-z_$][\w$]*+)*+\(")
# how a value is made twice to compare the two values, and the methods whose contract is to be compared with themselves
_FACTORY = re.compile(r"(?:^|\.)(?:of|ofNullable|valueOf|from|copyOf|parse\w*|create\w*|new\w*|getInstance|instance|asList|singleton\w*|empty\w*|unmodifiable\w*|builder|build|mock|spy)\(")
_CONTRACT = re.compile(r"(?:^|\.)(?:hashCode|equals|toString|compareTo|clone|getClass)\(")
# What makes a value read before and a value read after two different answers: any call between them that is not a plain read (a getter, a size) — a request, a
# write, an execution, whatever its name.  A variable read again after one is the check «nothing changed», which AT_NO_STATE_CHECK asks for.
_ANY_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_PLAIN_READ = re.compile(r"(?:get|is|has)[A-Z]\w*|size|length|isEmpty|toString|hashCode|equals|name|id|value")


def _does_something(piece: str) -> bool:
    """Whether the statement calls anything that is not a plain read (a getter, ``size()``)."""
    return any(_PLAIN_READ.fullmatch(call.group(1)) is None for call in _ANY_CALL.finditer(piece))


def _is_call(expression: str, head: re.Pattern[str] = _CALL_HEAD) -> bool:
    """Whether the blank-free ``expression`` is a call: ``head`` (a path and an opening bracket) at its start and a closing bracket after it at its end."""
    opened = head.match(expression)
    return opened is not None and expression.endswith(")") and opened.end() < len(expression)


# «before and after»: how far back a variable is followed from its initialiser to the assertion.  A longer way is taken for «something happened in between» —
# the careful answer, no finding — so the look back costs a bounded amount per assertion (K statements and A assertions were K x A).
_BETWEEN_STATEMENTS = 200
_BETWEEN_CHARS = 4000


class _Between:
    """The statements (or lines) of one test, asked «was something done with this receiver between statement ``first`` and statement ``last``?»."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self._does: list[int] | None = None  # how many statements up to each one call something that is not a plain read
        self._chars: list[int] | None = None

    def changed(self, first: int, last: int, receiver: str) -> bool:
        """The statements ``first`` … ``last - 1``: any call that is not a plain read, or any mention of the receiver; too far back counts as a change."""
        if last <= first:
            return False
        if last - first > _BETWEEN_STATEMENTS:
            return True
        if self._chars is None or self._does is None:
            self._chars, self._does = [0], [0]
            for text in self.texts:
                self._chars.append(self._chars[-1] + len(text) + 1)
                self._does.append(self._does[-1] + (1 if _does_something(text) else 0))
        if self._chars[last] - self._chars[first] > _BETWEEN_CHARS or self._does[last] > self._does[first]:
            return True
        return re.search(rf"\b{re.escape(receiver)}\b", "\n".join(self.texts[first:last])) is not None




def _closing(code: str, opening: int, limit: int) -> int:
    """The index of the bracket that closes the one at ``opening`` (``(``, ``[`` or ``{``), or -1 when it is not closed within ``limit`` characters."""
    depth = 0
    for position in range(opening, min(len(code), opening + limit)):
        char = code[position]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return position
    return -1


def _top_level_arguments(code: str, opening: int, closing: int) -> list[tuple[int, int]]:
    """The (start, end) of every argument of the call whose parentheses are at ``opening`` and ``closing``: split at the commas of the first level."""
    arguments, depth, start = [], 0, opening + 1
    for position in range(opening + 1, closing):
        char = code[position]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append((start, position))
            start = position + 1
    arguments.append((start, closing))
    return arguments


def _equality_sides(code: str, start: int, end: int) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """For the statement ``[start, end)``: the (expected, actual) spans of an equality assertion — ``assertEquals(expected, actual)``, ``assertThat(actual).isEqualTo(expected)``
    (AssertJ) or ``assertThat(actual, is(expected))`` (Hamcrest) — or None."""
    found = _JAVA_EQUALS_CALL.search(code, start, end)
    if found:
        close = _closing(code, found.end() - 1, min(_ARGUMENT_WINDOW, end - found.end() + 1))
        if close < 0:
            return None
        arguments = _top_level_arguments(code, found.end() - 1, close)
        if len(arguments) == 3 and re.fullmatch(r"\s*\"\s*\"\s*", code[arguments[0][0]:arguments[0][1]]):  # a message (a string, blanked) first: JUnit 4
            arguments = arguments[1:]
        return (arguments[0], arguments[1]) if len(arguments) >= 2 else None
    found = _JAVA_ASSERT_THAT.search(code, start, end)
    if not found:
        return None
    close = _closing(code, found.end() - 1, min(_ARGUMENT_WINDOW, end - found.end() + 1))
    if close < 0:
        return None
    arguments = _top_level_arguments(code, found.end() - 1, close)
    if len(arguments) == 2:  # Hamcrest
        matcher = _JAVA_MATCHER.match(code, arguments[1][0], arguments[1][1])
        inner = _closing(code, matcher.end() - 1, min(_ARGUMENT_WINDOW, arguments[1][1] - matcher.end() + 1)) if matcher else -1
        return (((matcher.end(), inner)), arguments[0]) if matcher and 0 < inner <= arguments[1][1] else None
    equality = _JAVA_EQUALITY_IN_CHAIN.search(code, close, end)
    if not equality:
        return None
    inner = _closing(code, equality.end() - 1, min(_ARGUMENT_WINDOW, end - equality.end() + 1))
    return ((equality.end(), inner), arguments[0]) if inner > 0 else None


def _self_computed(statements: list[tuple[int, str, str]], code: str, plain: str) -> Iterator[tuple[str, int, str]]:
    """``AT_SELF_COMPUTED``: an equality assertion whose expected side is — or holds, or is a variable that was initialised with — the very call it checks, so
    the test asks the code what it must answer.  The whole of the checked expression, text without blanks, must stand in the expected one: another receiver,
    another argument or another method is a different question.  The variable counts only when its name says that it is the expected value.  Left alone: how a value is made twice to compare the values (``of``, ``valueOf``,
    ``new``), the contract methods (``hashCode``, ``equals``, ``toString``), and a variable read again after the state or the receiver changed in between
    (before and after — the very check ``AT_NO_STATE_CHECK`` asks for).  One rule, ``same-call``, whatever the call is — a getter included: a check that asks the same getter twice can never fail."""
    initialisers: dict[str, tuple[int, str]] = {}  # variable -> (index of the statement that last set it, what it holds)
    between = _Between([piece for _at, piece, _raw in statements])
    for index, (at, piece, raw) in enumerate(statements):
        sides = _equality_sides(code, at, at + len(piece)) if re.search(r"\bassert\w*\s*\(", piece) else None
        if sides is not None:
            (e_start, e_end), (a_start, a_end) = sides
            actual = re.sub(r"\s+", "", plain[a_start:a_end])
            if _is_call(actual) and not _FACTORY.search(actual) and not _CONTRACT.search(actual):
                expected = [re.sub(r"\s+", "", plain[e_start:e_end])]
                if _VARIABLE.fullmatch(expected[0]) and _EXPECTED_NAME.fullmatch(expected[0]) and expected[0] in initialisers:
                    init_index, initialiser = initialisers[expected[0]]
                    # between the initialiser and the assertion something was done with the receiver, or the state was written: a value read before and after
                    if not between.changed(init_index + 1, index, re.match(r"[A-Za-z_$][\w$]*", actual).group()):
                        expected.append(initialiser)
                if any(actual in text for text in expected):
                    yield "AT_SELF_COMPUTED", at + len(piece) - len(piece.lstrip()), "same-call"
        declared = re.match(r"\s*(?:final\s+)?(?:[\w<>\[\],.?]+\s+)?([A-Za-z_$][\w$]*)\s*=(?!=)\s*(.+)$", piece, re.DOTALL)
        if declared:
            initialisers[declared.group(1)] = (index, re.sub(r"\s+", "", raw[declared.start(2):]))


_JAVA_FAILURE = re.compile(
    r"\b(?:assertThrows|expectThrows|assertThatThrownBy|assertThatExceptionOfType|assertThatIllegal\w+|assertThatNullPointerException)\s*\("
    r"|\bstatus\s*\(\s*\)\s*\.\s*(?:is(?:4xxClientError|5xxServerError|BadRequest|Unauthorized|Forbidden|NotFound|Conflict|Gone|MethodNotAllowed"
    r"|NotAcceptable|UnprocessableEntity|UnsupportedMediaType|TooManyRequests|PreconditionFailed|InternalServerError)\b|is\s*\(\s*[45]\d\d\b)"
    r"|\.\s*(?:isEqualTo|isBetween|hasStatus|statusCode)\s*\(\s*(?:HttpStatus\.(?:BAD_REQUEST|UNAUTHORIZED|FORBIDDEN|NOT_FOUND|CONFLICT|UNPROCESSABLE_ENTITY"
    r"|INTERNAL_SERVER_ERROR)|[45]\d\d)\b"
    r"|\battributeHas(?:Errors|FieldErrors|FieldErrorCode)\s*\(|\bhasFieldErrors\b|\bhasErrors\b")
_JAVA_ERROR_CONTENT = re.compile(r"[\"'](?:field_)?error\w*[\"']|\bConstraintViolation\b|\brejectedValue\b")
# The boundary of an application — a repository, a service, a mapper, a controller, a client, MockMvc, a template, an entity manager, a request helper — is what
# keeps a state a test can look at again; without one in the method a «write» is a call of a library, and a library has no state to keep (commons-lang, jsoup: noise).
_JAVA_BOUNDARY = re.compile(
    r"\b\w*(?:Repository|Repo|Service|Dao|DAO|Mapper|Controller|Client|MockMvc|mockMvc|Template|EntityManager|entityManager|Session|WebTestClient|RequestBuilders)\b"
    r"|\bmvc\b|\bperformRequest\b|\bjdbcTemplate\b|\b(?:repository|service|repo|dao|client|api)\b|\bperform\s*\(")
_JAVA_WRITE = re.compile(
    r"\b(?:save\w*|create\w*|insert\w*|update\w*|delete\w*|remove\w*|register\w*|submit\w*|persist\w*|merge\w*|store\w*)\s*\("
    r"|\b(?:post|put|patch|delete)\s*\(")
_JAVA_WRITE_BY_VERB = re.compile(r"\bperform\w*\s*\(\s*\"(?:POST|PUT|PATCH|DELETE)\"")  # the verb is a string literal: read where strings are kept
_JAVA_OBSERVATION = re.compile(
    r"\b(?:count|find|get\w*By\w*|getAll|list|exists|fetch|select|query|read|load|search|lookup)\w*\s*\("
    r"|\.\s*(?:size|isEmpty|hasSize|isNotEmpty|isPresent)\s*\(|\bverifyNo(?:Interactions|MoreInteractions)\b|\bnever\s*\(\s*\)|\btimes\s*\(\s*0\s*\)"
    r"|\bget\s*\(|\bjdbcTemplate\b|\bentityManager\b")
_JAVA_OBSERVATION_BY_VERB = re.compile(r"\bperform\w*\s*\(\s*\"GET\"")


def _unchecked_rejection(statements: list[tuple[int, str, str]]) -> Iterator[tuple[str, int, str]]:
    """``AT_NO_STATE_CHECK``: a test method that makes a write (a save, a POST, a delete …), expects it to be refused, and has nothing after the last such
    expectation that looks at the state again (a count, a find, a read, ``verify(…, never())``).  A method that writes nothing is left alone: there is no state to keep."""
    failure = None
    for index, (at, piece, raw) in enumerate(statements):
        if _JAVA_FAILURE.search(piece) or (_JAVA_ERROR_CONTENT.search(raw) and re.search(r"\bassert\w*\s*\(", piece)):
            failure = index
    if failure is None:
        return
    if not any(_JAVA_WRITE.search(piece) or _JAVA_WRITE_BY_VERB.search(raw) for _at, piece, raw in statements[:failure + 1]):
        return
    if not any(_JAVA_BOUNDARY.search(raw) for _at, _piece, raw in statements):
        return
    if any(_JAVA_OBSERVATION.search(piece) or _JAVA_OBSERVATION_BY_VERB.search(raw) for _at, piece, raw in statements[failure + 1:]):
        return
    at, piece, _raw = statements[failure]
    yield "AT_NO_STATE_CHECK", at + len(piece) - len(piece.lstrip()), "rejected-without-state-check"


def _java(text: str) -> Iterator[tuple[str, int, str]]:
    code, plain = _mask_java(text, strings=True), _mask_java(text, strings=False)
    masked = _Code(code)
    for match in _JAVA_BROAD_THROWS.finditer(code):
        yield "AT_ANY_EXCEPTION", match.start(), "broad-throws"
    for match in _JAVA_THROWN_BY.finditer(code):
        # assertThatThrownBy without a type or a message in its chain passes on any exception
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
    covered = -1
    for brace in _java_test_braces(code):
        start, end = masked.block(brace)
        # a method that is never closed (a cut-off answer) has the rest of the file for a body — and so has every one after it; and a test method inside the body of
        # another one (Java has no nested methods: a local or anonymous class, or a text that opens ``@Test void a() {`` again and again) is read to the same bound
        if brace not in masked.pairs:
            end = min(end, start + _UNCLOSED_BODY_LIMIT)
        elif brace < covered:
            end = min(end, start + _NESTED_BODY_LIMIT)
        covered = max(covered, end)
        statements, offset = [], start
        for piece in code[start:end].split(";"):
            statements.append((offset, piece, plain[offset:offset + len(piece)]))
            offset += len(piece) + 1
        checks = [(at, piece) for at, piece, raw in statements if re.search(r"\b(?:assert\w*|verify\w*|andExpect\w*)\s*\(", piece) or _ASSERT_ID.search(raw)]
        not_null = [(at, piece) for at, piece in checks if _JAVA_NOT_NULL.match(piece)]
        if not_null and len(not_null) == len(checks):
            at, piece = not_null[0]
            yield "AT_ALWAYS_TRUE", at + len(piece) - len(piece.lstrip()), "not-null-only"
        if brace in masked.pairs:  # a method that is never closed is a cut-off one: it is not judged for what it expects
            yield from _self_computed(statements, code, plain)
            yield from _unchecked_rejection(statements)
    for start in _path_calls(plain):
        yield "AT_PARTIAL_MATCH", start, "path-prefix"


# --- Python --------------------------------------------------------------------------------------------

_PY_BROAD_THROWS = re.compile(r"\b(?:pytest\.raises|assertRaises)\(\s*(?:builtins\.)?(?:Exception|BaseException)\s*[,)]")
_PY_EXCEPT_HEAD = re.compile(r"^(\s*)except\b")
_PY_TYPE_LIST = re.compile(r"[\w.,\s]*")


def _py_except(line: str) -> tuple[str, str, str] | None:
    """``(indent, types, rest of the line)`` of ``except [(]types[)] [as name]:``, else None. The one pattern with a lazy ``[\\w.,\\s]*?`` between three
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
# ``^\s*assert\s+.+\s+is\s+not\s+None\s*(?:,.*)?$`` (a greedy ``.+`` before a blank run) backtracked over every blank of ``assert x   …   y``: the pattern now reads the line as the head
# ``assert`` and, after at least one character, a blank, ``is not None`` and the end of the line or a comma
_PY_NOT_NULL_TAIL = re.compile(r"(?<=\s)is\s++not\s++None\s*+(?:,|$)")


def _py_not_null(line: str) -> bool:
    head = re.match(r"\s*assert\s", line)
    return head is not None and any(found.start() >= head.end() + 2 for found in _PY_NOT_NULL_TAIL.finditer(line, head.end()))


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
            not_null = [position for position in checks if _py_not_null(lines[position])]
            if not_null and len(not_null) == len(checks):
                yield "AT_ALWAYS_TRUE", starts[not_null[0]] + _indent(lines[not_null[0]]), "not-null-only"
            yield from _python_self_computed(lines, body, starts)
            yield from _python_unchecked_rejection(lines, body, starts)


_PY_CALL_HEAD = re.compile(r"[A-Za-z_][\w.]*+\(")
_PY_ASSERT_HEAD = re.compile(r"\s*assert\s")
_PY_ASSERT_EQUAL_HEAD = re.compile(r"\s*self\.assertEqual\(")


def _top_level_split(text: str, separator: str, limit: int | None = None) -> list[str]:
    """``text`` cut at the ``separator`` (one character, or ``==``) that stands outside every bracket and every string; at most ``limit`` cuts.  One pass.
    ``assert (.+?) == (.+?)`` backtracked over every blank of ``assert x   …   y`` (8 000 blanks: 0.16 s, twice the blanks four times the time)."""
    parts, depth, quote, start, position = [], 0, "", 0, 0
    while position < len(text):
        char = text[position]
        if quote:
            if char == "\\":
                position += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(separator, position) and (separator != "==" or (text[position - 1:position] not in ("!", "<", ">", "=") and text[position + 2:position + 3] != "=")):
            parts.append(text[start:position])
            start = position + len(separator)
            position = start - 1
            if limit is not None and len(parts) >= limit:
                break
        position += 1
    parts.append(text[start:])
    return parts


def _py_equality(line: str) -> tuple[str, str] | None:
    """The (left, right) of an equality assertion on one line — ``assert left == right[, message]`` or ``self.assertEqual(left, right[, message])`` — or None."""
    opened = _PY_ASSERT_HEAD.match(line)
    if opened:
        halves = _top_level_split(line[opened.end():], "==", 1)
        if len(halves) != 2 or not halves[0].strip():
            return None
        right = _top_level_split(halves[1], ",", 1)[0].strip()
        return (halves[0].strip(), right) if right else None
    opened = _PY_ASSERT_EQUAL_HEAD.match(line)
    text = line.strip()
    if not opened or not text.endswith(")"):
        return None
    arguments = _top_level_split(text[len(line[:opened.end()].lstrip()):-1], ",")
    if len(arguments) < 2 or not arguments[0].strip() or not arguments[1].strip():
        return None
    if len(arguments) >= 3 and arguments[2].lstrip()[:1] in ("\"", "'"):  # a message after the two values
        return arguments[0].strip(), arguments[1].strip()
    return arguments[0].strip(), ",".join(arguments[1:]).strip()
_PY_FAILURE = re.compile(
    r"\bpytest\.raises\(|\bassertRaises\(|\bstatus_code\s*+(?:==|>=|>|in)\s*+\(?\s*+[45]\d\d\b|\bstatus_code\s*+\)?\s*+(?:==|in)\s*+(?:HTTPStatus|status)\.\w*(?:BAD_REQUEST|UNAUTHORIZED|FORBIDDEN|NOT_FOUND|CONFLICT|UNPROCESSABLE)")
_PY_CALLED_ONCE = re.compile(r"\bassert_called_once_with\(")
_PY_ERROR_WORD = re.compile(r"\b(?:error|exception)\b")


def _py_failure(line: str) -> bool:
    """Whether the line expects a refusal.  ``assert_called_once_with(…error…)`` is read as the first call and an error word after it: the one-pattern form
    ``assert_called_once_with\\(.*\\berror`` ran to the end of the line for every call on it."""
    if _PY_FAILURE.search(line):
        return True
    opened = _PY_CALLED_ONCE.search(line)
    return opened is not None and _PY_ERROR_WORD.search(line, opened.end()) is not None

_PY_BOUNDARY = re.compile(r"\b(?:client|session|db|repo|repository|service|api|app|requests|conn|cursor|engine|store|dao)\b")
_PY_WRITE = re.compile(
    r"\b(?:client|requests|api|app)\.(?:post|put|patch|delete)\(|\.(?:save|add|add_all|commit|insert|update|delete|remove|create\w*|register\w*|persist\w*)\(|\b(?:create|update|delete|save|insert|remove|register|persist)_\w+\(")
_PY_OBSERVATION = re.compile(
    r"\b(?:client|requests|api|app)\.get\(|\.(?:count|query|filter|filter_by|all|first|one|scalar|fetchall|fetchone|exists|find\w*|select\w*|list\w*)\(|\blen\(|\b(?:get|find|list|count|read|load|fetch|select)_\w+\(|\bassert_not_called\b|\bsession\.")


def _python_self_computed(lines: list[str], body: list[int], starts: list[int]) -> Iterator[tuple[str, int, str]]:
    """``AT_SELF_COMPUTED`` of a pytest function: ``assert f(x) == f(x)`` (or ``self.assertEqual``) — the same call on both sides — or a variable whose name says it is the expected
    value and that holds the very call checked (the same rules as for Java: the contract methods and the factories are left alone)."""
    assigned: dict[str, tuple[str, int]] = {}  # variable -> (what it holds, the index in ``body`` of the line it was set on)
    between = _Between([lines[position] for position in body])
    for index, position in enumerate(body):
        text = lines[position].strip()
        declared = re.match(r"([A-Za-z_]\w*)\s*=\s*(.+)$", text)
        if declared and not re.match(r"[A-Za-z_]\w*\s*==", text):
            assigned[declared.group(1)] = (re.sub(r"\s+", "", declared.group(2)), index)
        sides_of = _py_equality(lines[position])
        if sides_of is None:
            continue
        left, right = re.sub(r"\s+", "", sides_of[0]), re.sub(r"\s+", "", sides_of[1])
        if not _is_call(left, _PY_CALL_HEAD) or _FACTORY.search(left) or _CONTRACT.search(left):
            continue
        sides = [right]
        if _VARIABLE.fullmatch(right) and _EXPECTED_NAME.fullmatch(right) and right in assigned:
            held, set_at = assigned[right]
            # the same guard as for Java: a request, a write or a use of the receiver between the two reads makes them a before and an after
            if not between.changed(set_at + 1, index, re.match(r"[A-Za-z_]\w*", left).group()):
                sides.append(held)
        if any(left in side for side in sides):
            yield "AT_SELF_COMPUTED", starts[position] + _indent(lines[position]), "same-call"


def _python_unchecked_rejection(lines: list[str], body: list[int], starts: list[int]) -> Iterator[tuple[str, int, str]]:
    """``AT_NO_STATE_CHECK`` of a pytest function: a write (a ``client.post``, a ``save``, a ``create_…``), a refusal expected (``pytest.raises``, a status 4xx or 5xx),
    and no read of the state (a ``client.get``, a ``count``, a ``query``, a ``len``) after the last refusal.  A function that writes nothing, or touches no
    client, session or repository, is left alone."""
    failure = None
    for position in body:
        if _py_failure(lines[position]):
            failure = position
    if failure is None:
        return
    end = failure
    if re.match(r"\s*with\s+pytest\.raises\b", lines[failure]):  # the call under test is inside the block: the state is read after it
        inside = _body(lines, failure)
        end = inside[-1] if inside else failure
    if not any(_PY_WRITE.search(lines[position]) for position in body if position <= end):
        return
    if not any(_PY_BOUNDARY.search(lines[position]) for position in body):
        return
    if any(_PY_OBSERVATION.search(lines[position]) for position in body if position > end):
        return
    yield "AT_NO_STATE_CHECK", starts[failure] + _indent(lines[failure]), "rejected-without-state-check"


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
