"""F9: the scans of model and project text that were quadratic give the same answers as before, and give them in linear time.

Each new implementation is held to the old one (kept here as the reference) on random texts built from the pieces that matter: brackets, quotes, blanks of every
kind (a tab, a form feed, a no-break space, a line separator), newlines.  The time of the worst texts is checked by tests/test_hunt8_d6_regex_time.py, in a child
process with a budget.
"""
from __future__ import annotations

import json
import random
import re

import pytest

PIECES = ["{", "[", "}", "]", '"a"', '"', ":", ",", "1", "-1.5", "true", "null", "NaN", "x", "\n", "\n", " ", " ", "\t", "\x0b", "\x0c", "\xa0", " ", "\r\n", "\r",
          '{"a": 1}', "[1, 2]", '{"a": [1, {"b": null}]}', "\\", "é"]


def _texts(seed: int, count: int):
    randomizer = random.Random(seed)
    for _ in range(count):
        yield "".join(randomizer.choice(PIECES) for _ in range(randomizer.randint(0, 24)))


def _old_canonical_trailing_json(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*([\[{])", text):
        start = match.start(1)
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)) and not text[start + end:].strip():
            return value
    return None


def _old_projection_trailing_json(value: str):
    lines = value.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith(("{", "[")):
            continue
        candidate = "\n".join(lines[index:]).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)):
            return "\n".join(lines[:index]).strip(), candidate
    return "\n".join(lines).strip(), None


def test_the_trailing_json_of_a_canonical_field_is_the_one_found_before() -> None:
    from tools.canonical_document import _NO_JSON, _trailing_json

    found = 0
    for text in _texts(20261105, 30000):
        try:
            old = _old_canonical_trailing_json(text)
        except RecursionError:
            continue
        new = _trailing_json(text)
        assert (None if new is _NO_JSON else new) == old, repr(text)
        found += old is not None
    assert found > 500, found  # the comparison is not between two "nothing"s


def test_the_trailing_json_of_a_projected_field_is_the_one_found_before() -> None:
    from tools.test_case_projections import _trailing_json

    found = 0
    for text in _texts(20261106, 30000):
        old = _old_projection_trailing_json(text)
        assert _trailing_json(text) == old, repr(text)
        found += old[1] is not None
    assert found > 500, found


@pytest.mark.parametrize("text", [
    'Ответ:\n{"a": 1}', 'Ответ:\n  [1, 2]  \n', '{"a": 1}\n{"b": 2}', '{"a": 1} x', 'x\n  {"a": [1,\n 2]}', '\n\n\n{"a": 1}\n\n\n', '[]', '{}', '[\n]', '{"a":\n1}\x0b',
    '{"a": 1}\xa0', 'text {"a": 1}', ' {"a": 1}', '{"a": 1}\n\n{"a"', 'x\n{"a": 1}\n{"b"', '[1, 2]\n[3',
])
def test_the_hand_made_texts_give_the_answer_of_before(text: str) -> None:
    from tools.canonical_document import _NO_JSON, _trailing_json as canonical
    from tools.test_case_projections import _trailing_json as projected

    new = canonical(text)
    assert (None if new is _NO_JSON else new) == _old_canonical_trailing_json(text)
    assert projected(text) == _old_projection_trailing_json(text)


# --- the section of a requirement in the documents ---------------------------------------------------------------------------------------------------

def _old_section_of(text: str, documents: dict[str, str]):
    from tools.batch_assembly import _HEADING, _norm

    first = _norm(text.split("\n", 1)[0])
    own = _HEADING.match(first)
    if own:
        return own.group(1)
    snippet = first[:120]
    if len(snippet) < 8:
        return None
    for path in sorted(documents):
        section = None
        for line in documents[path].splitlines():
            heading = _HEADING.match(line.strip())
            if heading:
                section = f"{path}#{_norm(heading.group(1))}"
            elif section is not None and snippet in _norm(line):
                return section
    return None


def test_the_section_of_a_requirement_is_the_one_found_before() -> None:
    from tools.batch_assembly import _SectionIndex, _section_of

    words = ["alpha", "beta", "gamma", "delta", "login", "owner", "visit", "pet", "must", "returns", "an", "error"]
    randomizer = random.Random(20261107)
    found = 0
    for _ in range(300):
        documents = {}
        for name in ("docs/b.md", "docs/a.md", "README.md")[:randomizer.randint(1, 3)]:
            lines = []
            for _line in range(randomizer.randint(0, 25)):
                kind = randomizer.random()
                body = " ".join(randomizer.choice(words) for _ in range(randomizer.randint(1, 9)))
                lines.append(("# " if kind < 0.1 else "## " if kind < 0.2 else "   ") + ("\t" if kind > 0.9 else "") + body)
            documents[name] = "\n".join(lines)
        index = _SectionIndex(documents)
        for _query in range(40):
            first = " ".join(randomizer.choice(words) for _ in range(randomizer.randint(1, 10)))
            text = ("# " if randomizer.random() < 0.05 else "") + first + "\n" + randomizer.choice(words)
            old = _old_section_of(text, documents)
            assert _section_of(text, documents, index) == old == _section_of(text, documents), (documents, text)
            found += old is not None
    assert found > 300, found


# --- the headings of a Markdown document --------------------------------------------------------------------------------------------------------------

def _old_heading_title(line: str):
    match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$", line)
    if not match:
        return None
    return len(match.group(1)), " ".join(re.sub(r"[ \t]+#+$", "", match.group(2)).split())


def test_a_heading_is_cut_as_before() -> None:
    from tools.doc_sections import headings

    pieces = ["#", "##", "###", "#######", " ", "  ", "\t", "Title", "a", "b c", "#", "##", " ##", " ", "é", "x#", "`", "~"]
    randomizer = random.Random(20261108)
    seen = 0
    for _ in range(20000):
        line = "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, 9)))
        old = _old_heading_title(line)
        new = headings(line)
        assert new == ([] if old is None else [(1, old[0], old[1])]), repr(line)
        seen += old is not None
    assert seen > 1000, seen


# --- the secret rules of the inventory ------------------------------------------------------------------------------------------------------------------

_OLD_KEY = rb"[\w.-]*(?:password|passwd|pwd|secret)[\w.-]*"
_OLD_JWT = re.compile(rb"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_OLD_LINE = re.compile(rb"(?im)^[ \t]*(?:-[ \t]+)?(['\"]?)" + _OLD_KEY + rb"\1[ \t]*[:=][ \t]*(?P<value>[^\r\n]*)")
_OLD_JSON = re.compile(rb'(?i)"' + _OLD_KEY + rb'"\s*:\s*"(?P<value>(?:[^"\\]|\\.)*)"')
_OLD_XML = re.compile(rb"(?is)<(?P<tag>" + _OLD_KEY + rb")(?:\s[^>]*)?(?<!/)>(?P<value>[^<]*)</(?P=tag)\s*>")
_OLD_XML_ATTR = re.compile(
    rb"(?is)<[\w:.-]+\s(?=[^>]*\b(?:name|key)\s*=\s*([\"'])" + _OLD_KEY + rb"\1)[^>]*?\bvalue\s*=\s*([\"'])(?P<value>[^\"'>]*)\2")


def _byte_texts(seed: int, count: int, pieces: list[bytes], longest: int = 22):
    randomizer = random.Random(seed)
    for _ in range(count):
        yield b"".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, longest)))


def test_the_jwt_rule_finds_what_it_found() -> None:
    from tools.project_inventory import _TOKEN_SIGNATURES

    new = dict(_TOKEN_SIGNATURES)["jwt-v1"]
    pieces = [b"eyJ", b"eyJa", b"-", b"-", b".", b".", b"a", b"b_", b"Z9", b" ", b"\n", b"x", b"Bearer ", b"eyJ.", b"\xc3\xa9", b'"']
    hits = 0
    for text in _byte_texts(20261109, 40000, pieces, 18):
        old = _OLD_JWT.search(text) is not None
        assert (new.search(text) is not None) == old, text
        hits += old
    assert hits > 300, hits


@pytest.mark.parametrize("name,old_name", [("_PASSWORD_LINE", "_OLD_LINE"), ("_PASSWORD_JSON", "_OLD_JSON")])
def test_the_password_rules_of_config_files_find_what_they_found(name: str, old_name: str) -> None:
    from tools import project_inventory

    new, old = getattr(project_inventory, name), globals()[old_name]
    pieces = [b"password", b"pwd", b"secret", b"passwd", b"Pass", b"a", b".", b"-", b"_", b":", b"=", b" ", b"\t", b"\n", b'"', b"'", b"- ", b"x", b"\\", b"\\\"",
              b"${X}", b"true", b"s3cret", b",", b"{", b"}", b'"password"', b'"pwd"', b'"secret"', b': "', b':"', b'x"']
    hits = 0
    for text in _byte_texts(20261110, 30000, pieces, 20):
        expected = [match.group("value") for match in old.finditer(text)]
        assert [match.group("value") for match in new.finditer(text)] == expected, text
        hits += bool(expected)
    assert hits > 500, hits


def test_the_xml_password_rules_find_what_they_found_in_well_formed_tags() -> None:
    """The tag is read up to its «>» and not over a «<» (the one change); the texts here have no «<» inside a tag, so the answers must be the old ones."""
    from tools import project_inventory

    randomizer = random.Random(20261111)
    words = [b"password", b"pwd", b"secret", b"name", b"key", b"value", b"x", b"bean", b"a-b", b"Password1"]

    def attr() -> bytes:
        quote = randomizer.choice([b'"', b"'"])
        return randomizer.choice(words) + b"=" + quote + b"".join(randomizer.choice(words + [b" ", b".", b"-", b"${X}"]) for _ in range(randomizer.randint(0, 3))) + quote

    def tag() -> bytes:
        name = randomizer.choice(words + [b"property", b"entry"])
        attributes = b" ".join(attr() for _ in range(randomizer.randint(0, 3)))
        body = b"".join(randomizer.choice(words + [b" ", b"\n"]) for _ in range(randomizer.randint(0, 2)))
        if randomizer.random() < 0.4:
            return b"<" + name + (b" " + attributes if attributes else b"") + b">" + body + b"</" + name + b">"
        return b"<" + name + (b" " + attributes if attributes else b"") + (b"/>" if randomizer.random() < 0.5 else b">")

    hits = 0
    for _ in range(8000):
        text = b"".join(tag() + randomizer.choice([b"", b"\n", b" "]) for _ in range(randomizer.randint(1, 4)))
        for new, old in ((project_inventory._PASSWORD_XML, _OLD_XML), (project_inventory._PASSWORD_XML_ATTR, _OLD_XML_ATTR)):
            expected = [match.group("value") for match in old.finditer(text)]
            assert [match.group("value") for match in new.finditer(text)] == expected, text
            hits += bool(expected)
    assert hits > 500, hits
