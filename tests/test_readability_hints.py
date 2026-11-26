"""W4.2: readability of a generated Java test — the code shows the reviewer of the automation a hint (never a refusal): the date taken
in a helper or twice in a test, ``findAll().stream()``, a literal of one character 50 times or more."""
from __future__ import annotations

import hashlib

from tests.test_hunt8_d6_regex_time import _time


def _hints(code: str, path: str = "src/test/java/a/T.java") -> list[str]:
    from tools.automation_text import readability_hints

    return readability_hints([{"path": path, "content": code}])


def test_a_date_taken_in_a_helper_is_a_hint() -> None:
    code = "class T {\n @Test void t() { visit(); }\n void visit() { LocalDate d = LocalDate.now(); }\n}\n"
    assert _hints(code) == ["- LocalDate.now() в помощнике T.java:3 — дата одна на тест: LocalDate today = LocalDate.now() в начале теста, помощнику — параметром"]


def test_one_date_per_test_is_no_hint_and_two_are() -> None:
    assert _hints("class T {\n @Test void t() { LocalDate d = LocalDate.now(); }\n}\n") == []
    assert _hints("class T {\n @Test void t() { LocalDate.now(); LocalDate.now(); }\n}\n") == ["- LocalDate.now() 2 раза в тесте T.java:2 — одна дата на тест"]


def test_find_all_stream_is_a_hint_but_not_in_a_comment() -> None:
    assert _hints("class T {\n void read() { repo.findAll().stream(); }\n}\n") == ["- findAll().stream() в T.java:2 — findById"]
    assert _hints("class T {\n // repo.findAll().stream()\n}\n") == []


def test_a_uniform_literal_of_fifty_is_a_hint_and_of_forty_nine_is_not() -> None:
    assert _hints('class T { String s = "' + "A" * 50 + '"; }') == ['- литерал из 50 одинаковых символов в T.java:1 — "A".repeat(50) (Java 11+) или константа']
    assert _hints('class T { String s = "' + "A" * 49 + '"; }') == []


def test_a_python_file_is_not_read() -> None:
    assert _hints("def test_x():\n    assert 'A' * 60\n" + "x = '" + "A" * 60 + "'\n", path="tests/test_x.py") == []


def test_without_a_finding_the_tables_keep_their_bytes() -> None:
    from tests.test_o2a_automation_text import DIGEST, _artifact
    from tools.automation_text import render

    tables = next(data for name, _basis, data in render(_artifact(), DIGEST) if name.endswith(".tables.txt"))
    assert hashlib.sha256(tables).hexdigest() == "ad9b26749fa162cbfe300fb139a9dae2372012fd9163c3611e0deea14592fbfe"  # cb34a090, before W4.2


def test_the_hints_are_linear_on_a_hostile_file() -> None:
    _time("""
        from tools.automation_text import readability_hints
        TEXT = 'class T { String s = "' + "AB" * 50_000 + '"; ' + "void f() { " * 5_000 + "LocalDate.now(); " * 5_000 + "}" * 5_000 + " }"
        def run(): readability_hints([{"path": "T.java", "content": TEXT}])
    """, 1.0)
