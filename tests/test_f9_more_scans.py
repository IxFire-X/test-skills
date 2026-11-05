"""F9: the other scans of text that were superlinear (8.D6-10, 8.D6-11) give the answers they gave, each held to the old implementation kept here as the reference."""
from __future__ import annotations

import random
import re

import pytest

_OLD_URL_CREDENTIALS = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
_OLD_URL_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
_OLD_SECRET_VALUE = re.compile(r"(?i)((?:password|passwd|pwd|token|secret)[\w.-]*['\"]?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)]+)")
_OLD_URL_USERINFO_PROBE = re.compile(r"://[^/\s@]+@")
_OLD_XML_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_OLD_GRADLE_COMMENTS = re.compile(r"(?<![:\"'])//[^\n]*|/\*.*?\*/", re.S)
_OLD_FIELD_ERRORS = re.compile(r"attributeHasFieldErrors\s*\(\s*\"\w+\"\s*,((?:\s*\"\w+\"\s*,?)+)\)"
                               r"|attributeHasFieldErrorsCode\s*\(\s*\"\w+\"\s*,(\s*\"\w+\")")


def _texts(seed: int, count: int, pieces: list[str], longest: int = 18):
    randomizer = random.Random(seed)
    for _ in range(count):
        yield "".join(randomizer.choice(pieces) for _ in range(randomizer.randint(0, longest)))


def test_the_url_credentials_and_user_info_forms_find_what_the_old_ones_found() -> None:
    from tools.project_inventory import URL_CREDENTIALS_TEXT, URL_USERINFO_TEXT

    pieces = ["http", "https", "ftp", "a", "Z", "1", "+", ".", "-", "://", "://", ":", "@", "/", "u", "pw", " ", "\n", "x-y", "é", "?", "=", "git+ssh", "https://u:p@", "ftp://user:pw@", "a://b:c@h"]
    hits = 0
    for text in _texts(20261112, 40000, pieces):
        old = _OLD_URL_CREDENTIALS.search(text) is not None
        assert (URL_CREDENTIALS_TEXT.search(text) is not None) == old, repr(text)
        assert URL_USERINFO_TEXT.sub(r"\1[redacted]@", text) == _OLD_URL_USERINFO.sub(r"\1[redacted]@", text), repr(text)
        hits += old
    assert hits > 300, hits


def test_the_probe_scrub_keeps_what_it_kept() -> None:
    from tools.exec_probe import _SECRET_VALUE

    pieces = ["password", "pwd", "token", "Secret", "passwd", "my", ".", "-", "_", "x", "=", ":", " ", "\t", "'", "\"", "abc", "s3cr3t", ",", ";", ")", "a b", "\n"]
    hits = 0
    for text in _texts(20261113, 40000, pieces, 14):
        old = _OLD_SECRET_VALUE.sub(r"\1***", text)
        assert _SECRET_VALUE.sub(r"\1***", text) == old, repr(text)
        hits += old != text
    assert hits > 2000, hits


def test_the_xml_comments_go_as_before() -> None:
    from tools.execution_adapters import _strip_xml_comments

    pieces = ["<!--", "-->", "<!---->", "<", "!", "--", ">", "a", "\n", " ", "<a>", "</a>"]
    hits = 0
    for text in _texts(20261114, 40000, pieces, 14):
        old = _OLD_XML_COMMENTS.sub(" ", text)
        assert _strip_xml_comments(text) == old, repr(text)
        hits += old != text
    assert hits > 3000, hits


def test_the_gradle_comments_go_as_before() -> None:
    from tools.execution_adapters import _strip_gradle_comments

    pieces = ["//", "/*", "*/", "/", "*", "///", "/**/", ":", "\"", "'", "http:", "a", "b c", "\n", " ", "x=1", "\r", "\n\n"]
    hits = 0
    for text in _texts(20261115, 60000, pieces, 14):
        old = _OLD_GRADLE_COMMENTS.sub(" ", text)
        assert _strip_gradle_comments(text) == old, repr(text)
        hits += old != text
    assert hits > 5000, hits


def test_the_field_errors_of_a_call_are_the_names_of_before() -> None:
    from tools.existing_tests import _FIELD_ERRORS

    pieces = ["attributeHasFieldErrors", "attributeHasFieldErrorsCode", "(", ")", "\"model\"", "\"a\"", "\"b1\"", ",", " ", "\n", "\t", "x", "\"", ".", ";",
              "attributeHasFieldErrors(\"m\",", "attributeHasFieldErrors(\"m\", \"a\",", "attributeHasFieldErrorsCode(\"m\",", " \"f\"", " \"g\",", ")"]
    hits = 0
    for text in _texts(20261116, 60000, pieces, 14):
        old = [(first, code) for first, code in _OLD_FIELD_ERRORS.findall(text)]
        new = [(first, code) for first, code in _FIELD_ERRORS.findall(text)]
        names = lambda found: [re.findall(r"\"(\w+)\"", first or code) for first, code in found]  # noqa: E731  what existing_tests does with a match
        assert names(new) == names(old), repr(text)
        hits += bool(old)
    assert hits > 300, hits


@pytest.mark.parametrize("text", [
    "x = mockMvc.perform(post(\"/a\")).andExpect(model().attributeHasFieldErrors(\"owner\", \"firstName\", \"lastName\"))",
    "model().attributeHasFieldErrors(\"owner\", \"firstName\",\n   \"lastName\" )",
    "model().attributeHasFieldErrorsCode(\"owner\", \"firstName\", \"required\")",
])
def test_a_call_of_the_real_shapes_still_gives_its_names(text: str) -> None:
    from tools.existing_tests import _FIELD_ERRORS

    def names(pattern) -> list[list[str]]:
        return [re.findall(r"\"(\w+)\"", first or code) for first, code in pattern.findall(text)]

    assert names(_FIELD_ERRORS) == names(_OLD_FIELD_ERRORS) and names(_FIELD_ERRORS)
