"""Bug hunt 8, direction 6 (05.11.2026), F9: regular expressions (and loops built like them) that read model, agent or project text and are SUPERLINEAR.

Each probe runs the production call in a child interpreter on an adversarial text of 100 KB (a few cases: 20-60 KB, the text is named in the test), and
asserts that it finishes inside a budget.  A linear pass over 100 KB takes 5-100 ms here; the budgets are 20-100 times that, so a failure is not a slow machine
but a growth of n squared (the ratio 10 KB -> 100 KB is 50-100 times in every failing probe) or worse.  A child that outlives ``HARD_LIMIT`` is killed, so a
failing probe costs at most that.  Tests named ``control`` run the neighbouring linear case and are expected to pass.

Run (in the copy of the package): python -m pytest tests/test_hunt8_d6_regex_time.py -q -n 2
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARD_LIMIT = 8.0  # seconds of wall time before the child is killed


def _time(body: str, budget: float) -> None:
    """Run *body* (statements, inside a function with ``ROOT`` importable) in a child, and fail if it takes more than *budget* seconds of its own clock."""
    program = ("import sys, time\nsys.path.insert(0, %r)\n" % str(ROOT)) + textwrap.dedent(body) + \
        "\nstarted = time.perf_counter()\nrun()\nprint(time.perf_counter() - started)\n"
    try:
        done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=HARD_LIMIT,
                              env={"PYTHONDONTWRITEBYTECODE": "1", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": __import__("os").environ.get("PATH", "")})
    except subprocess.TimeoutExpired:
        pytest.fail(f"did not finish in {HARD_LIMIT:.0f} s (budget {budget} s): superlinear")
    assert done.returncode == 0, done.stderr[-600:]
    elapsed = float(done.stdout.strip().splitlines()[-1])
    assert elapsed < budget, f"{elapsed:.2f} s for the adversarial text (budget {budget} s): superlinear"


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# model text: the human fields of a canonical document
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_trailing_json_of_a_human_field_with_100k_blank_lines_is_not_quadratic() -> None:
    """canonical_document._trailing_json: ``re.finditer(r"(?m)^\\s*([\\[{])", text)``.  At each of the n line starts ``\\s*`` swallows all the blank lines
    after it, then finds no bracket: n * n/2 steps.  A model that loops on newlines (the usual way a response degenerates) is enough: 10 KB takes 0.5 s,
    100 KB more than a minute."""
    _time("""
        from tools.canonical_document import _trailing_json
        TEXT = "\\n" * 100_000
        def run(): _trailing_json(TEXT)
    """, 1.0)


def test_trailing_json_of_a_human_field_with_blank_lines_and_spaces_is_not_quadratic() -> None:
    _time("""
        from tools.canonical_document import _trailing_json
        TEXT = " \\n" * 50_000
        def run(): _trailing_json(TEXT)
    """, 1.0)


def test_trailing_json_of_a_human_field_with_300k_opening_braces_on_lines_is_not_quadratic() -> None:
    """The same function, other shape: every line starts with ``{`` that is not JSON.  Each match is tried with ``raw_decode(text[start:])`` — a copy of the
    whole rest of the text per match: 1 MB of ``{x`` lines = 333 000 copies of up to 1 MB."""
    _time("""
        from tools.canonical_document import _trailing_json
        TEXT = "{x\\n" * 333_333
        def run(): _trailing_json(TEXT)
    """, 3.0)


def test_control_trailing_json_of_a_1mb_human_field_without_line_starts_is_linear() -> None:
    _time("""
        from tools.canonical_document import _trailing_json
        TEXT = "x" * 1_000_000
        def run(): _trailing_json(TEXT)
    """, 1.0)


def test_control_trailing_json_still_finds_the_json_at_the_end() -> None:
    from tools.canonical_document import _trailing_json

    assert _trailing_json('Ответ:\n{"a": 1}') == {"a": 1}


def test_projection_trailing_json_of_a_human_field_with_100k_lines_starting_with_a_brace_is_not_quadratic() -> None:
    """test_case_projections._trailing_json (the Markdown / Zephyr export of a case): for every line that starts with ``{`` or ``[`` it joins the rest of the
    lines (``"\\n".join(lines[index:])``) and tries ``json.loads``.  100 000 such lines (300 KB) = 100 000 joins of up to 300 KB: 200 KB takes 22 s, 400 KB 86 s."""
    _time("""
        from tools.test_case_projections import _trailing_json
        TEXT = "{x\\n" * 100_000
        def run(): _trailing_json(TEXT)
    """, 2.0)


def test_control_projection_trailing_json_still_splits_prose_and_body() -> None:
    from tools.test_case_projections import _trailing_json

    assert _trailing_json('Тело запроса:\n{"a": 1}') == ("Тело запроса:", '{"a": 1}')


def test_canonical_validation_of_a_field_of_30k_blanks_is_not_quadratic() -> None:
    """The schema def ``nonempty`` (used by every human text of the canonical document) is ``pattern: ".*\\S.*"`` — «has a non-blank character», written so that
    ``search`` tries every start and the greedy ``.*`` runs to the end of the blank run from each of them: n^2 for a string with no non-blank character.  A
    model that fills ``action`` (or ``test_data``, ``text`` ...) with blanks: 10 KB = 0.55 s, 40 KB = 8.8 s, 1 MB = about an hour, per field.  ``\\S`` alone means
    the same and is linear."""
    _time("""
        from tests.test_requirement_traceability import canonical_fixture
        from tools.canonical_document import validate_canonical_document
        DOC = canonical_fixture()
        DOC["test_cases"][0]["steps"][0]["action"] = " " * 30_000
        def run(): validate_canonical_document(DOC)
    """, 2.0)


def test_schema_engine_pattern_dot_star_non_blank_dot_star_on_30k_tabs_is_not_quadratic() -> None:
    _time("""
        from tools.schema_engine import Draft202012Validator
        VALIDATOR = Draft202012Validator({"type": "string", "minLength": 1, "pattern": ".*" + chr(92) + "S.*"})
        TEXT = chr(9) * 30_000
        def run(): VALIDATOR.is_valid(TEXT)
    """, 2.0)


def test_control_canonical_validation_of_a_1mb_field_with_a_non_blank_character_is_linear() -> None:
    _time("""
        from tests.test_requirement_traceability import canonical_fixture
        from tools.canonical_document import validate_canonical_document
        DOC = canonical_fixture()
        DOC["test_cases"][0]["steps"][0]["action"] = "x" + " " * 1_000_000
        def run(): validate_canonical_document(DOC)
    """, 2.0)


# (the sections on autotest_hints and run_tests — mode B, 8.D6-6 and 8.D6-7 — are not here: mode B is outside the cases-only delivery, v0.4)


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# project text: the requirement documents (--docs, the plan of the batches)
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_markdown_heading_with_50k_blanks_inside_is_not_quadratic() -> None:
    """doc_sections.headings: ``^ {0,3}(#{1,6})[ \\t]+(.*?)[ \\t]*$`` — the lazy ``(.*?)`` ends at every offset inside the blank run and ``[ \\t]*$`` runs to
    the end of the run: n^2.  ``_CLOSING = [ \\t]+#+$`` (``sub`` on the title) is the same again.  A requirement document with one such line (a table dump, a
    pasted block) makes ``--docs path#Heading`` and the section view hang: 10 KB = 0.7 s, 100 KB over a minute."""
    _time("""
        from tools.doc_sections import headings
        TEXT = "# a" + " " * 50_000 + "b\\n"
        def run(): headings(TEXT)
    """, 1.0)


def test_markdown_heading_with_50k_tabs_inside_is_not_quadratic() -> None:
    _time("""
        from tools.doc_sections import find_section
        TEXT = "# a" + "\\t" * 50_000 + "b\\n"
        def run():
            try:
                find_section(TEXT, "zzz")
            except ValueError:
                pass
    """, 1.0)


def test_control_markdown_heading_of_1mb_is_linear() -> None:
    _time("""
        from tools.doc_sections import headings
        TEXT = "# " + "a" * 1_000_000 + "\\n"
        def run(): headings(TEXT)
    """, 1.0)


def test_group_keys_of_4000_requirements_in_a_300kb_document_is_not_quadratic() -> None:
    """batch_assembly._group_keys, the fallback «document section»: for every requirement the document is walked line by line and every line is
    ``_norm``-ed again until the snippet is found.  4 000 requirements of one 290 KB document = 5.5 s, 14 000 (1 MB) a minute.  Not a regular expression, the
    same shape: n requirements x n lines."""
    _time("""
        from tools.batch_assembly import _group_keys
        LINES = ["# Spec"] + [f"Requirement number {i} must behave in a well defined way for module {i}" for i in range(4000)]
        ITEMS = [{"source_requirement_id": f"R{i}", "text": line} for i, line in enumerate(LINES[1:])]
        DOCS = {"docs/a.md": "\\n".join(LINES)}
        def run(): _group_keys(ITEMS, None, DOCS)
    """, 2.0)


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# project text: the secret rules of the inventory (no size limit on a file)
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_jwt_rule_on_100kb_of_eyj_pieces_joined_by_dashes_is_not_quadratic() -> None:
    """project_inventory jwt-v1 = ``\\beyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+``: every ``-eyJ`` is a word boundary and a start, and
    ``[A-Za-z0-9_-]+`` (the dash is in the class) runs to the end of the text before the dot is missed.  A file of base64url pieces glued with ``-`` hangs the scan."""
    _time("""
        from tools.project_inventory import token_signature_rule
        DATA = b"-eyJ" * 25_000
        def run(): token_signature_rule(DATA)
    """, 1.0)


def test_password_line_rule_on_a_100kb_yaml_line_of_pwd_is_not_quadratic() -> None:
    """_PASSWORD_LINE: ``^[ \\t]*...(['\\"]?)[\\w.-]*(?:password|passwd|pwd|secret)[\\w.-]*\\1[ \\t]*[:=]``.  Both ``[\\w.-]*`` can take the same word characters, so
    k keywords in a word run of n bytes cost k * n.  ``pwd`` x 33 333 on one line of a .yml / .properties file: 10 KB takes 0.5 s, 100 KB does not finish."""
    _time("""
        from pathlib import Path
        from tools.project_inventory import _has_password
        DATA = b"pwd" * 33_333 + b"\\n"
        def run(): _has_password(Path("application.yml"), DATA)
    """, 1.0)


def test_password_json_rule_on_a_100kb_quoted_run_of_pwd_is_not_quadratic() -> None:
    _time("""
        from pathlib import Path
        from tools.project_inventory import _has_password
        DATA = b'"' + b"pwd" * 33_333
        def run(): _has_password(Path("data.json"), DATA)
    """, 1.0)


def test_password_xml_rule_on_a_100kb_tag_name_of_pwd_is_not_quadratic() -> None:
    _time("""
        from pathlib import Path
        from tools.project_inventory import _has_password
        DATA = b"<" + b"pwd" * 33_333
        def run(): _has_password(Path("pom.xml"), DATA)
    """, 1.0)


def test_password_xml_attribute_rule_on_100kb_of_open_tags_without_a_closing_bracket_is_not_quadratic() -> None:
    """_PASSWORD_XML_ATTR: ``<[\\w:.-]+\\s(?=[^>]*\\b(?:name|key)\\s*=...)``: for every ``<a `` the look-ahead runs ``[^>]*`` to the end of the text when
    there is no ``>`` (a truncated or hand-written XML file)."""
    _time("""
        from pathlib import Path
        from tools.project_inventory import _has_password
        DATA = b"<a " * 33_333
        def run(): _has_password(Path("beans.xml"), DATA)
    """, 1.0)


def test_control_url_credentials_rule_is_linear_since_fat_s1() -> None:
    """The one rule that was fixed (an atomic group): 1 MB of letters costs 0.1 s.  The same bug lives on in the two copies below."""
    _time("""
        from tools.project_inventory import token_signature_rule
        DATA = b"a" * 1_000_000
        def run(): token_signature_rule(DATA)
    """, 1.0)


def test_control_password_rules_on_a_100kb_ordinary_config_are_linear() -> None:
    _time("""
        from pathlib import Path
        from tools.project_inventory import _has_password
        DATA = b"spring.datasource.url: jdbc:h2:mem:x\\nlogging.level.root: INFO\\n" * 2000
        def run():
            _has_password(Path("application.yml"), DATA)
            _has_password(Path("application.properties"), DATA)
            _has_password(Path("data.json"), b'{"a": "b", "c": [1, 2, 3]}' * 4000)
            _has_password(Path("pom.xml"), b"<project><name>x</name></project>\\n" * 3000)
    """, 1.0)


# ----------------------------------------------------------------------------------------------------------------------------------------------------
# the FaT-S1 bug (a run of letters re-read from each of its letters) in the copies that were not fixed
# ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_canonical_runner_output_of_64kb_without_a_newline_is_not_quadratic() -> None:
    """pilot_state._canonical_runner_output runs ``re.search(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\\s:@]+:[^/\\s@]+@", line)`` on every line of the runner output
    (stdout + stderr, at most 2 x 32 KB are kept).  Without the atomic group of project_inventory ``url-credentials-v1`` a line of 64 KB of letters or hex digits
    re-reads the run from each letter: about 3.5 s for the biggest text it can get (100 KB: 8 s).  Bounded by the output cap, so a slowdown, not a hang."""
    _time("""
        from tools.pilot_state import _canonical_runner_output
        TEXT = "a" * 64_000
        def run(): _canonical_runner_output(TEXT)
    """, 1.0)


def test_init_skillsrc_sanitize_of_a_100kb_value_is_not_quadratic() -> None:
    """init_skillsrc._sanitize: ``re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\\s]+@", ...)`` on every string of a receipt — the same pattern as the old url rule."""
    _time("""
        from tools.init_skillsrc import _sanitize
        VALUE = {"proposal": "a" * 100_000}
        def run(): _sanitize(VALUE)
    """, 1.0)


def test_xml_comment_strip_of_pom_with_100kb_of_unclosed_comments_is_not_quadratic() -> None:
    """execution_adapters._XML_COMMENTS = ``<!--.*?-->`` (DOTALL) is applied to every pom.xml: each ``<!--`` of a file with no ``-->`` scans to the end."""
    _time("""
        from tools.execution_adapters import _strip_xml_comments
        TEXT = "<!--" * 25_000
        def run(): _strip_xml_comments(TEXT)
    """, 1.0)


def test_gradle_comment_strip_of_build_file_with_100kb_of_unclosed_block_comments_is_not_quadratic() -> None:
    """execution_adapters._GRADLE_COMMENTS = ``/\\*.*?\\*/`` (DOTALL), applied to build.gradle / settings.gradle(.kts): each ``/*`` with no ``*/`` after it scans
    to the end of the file (20 KB = 0.8 s, 100 KB = 21 s)."""
    _time("""
        from tools.execution_adapters import _strip_gradle_comments
        TEXT = "/*a" * 33_333
        def run(): _strip_gradle_comments(TEXT)
    """, 1.0)


def test_existing_tests_outcomes_of_a_call_with_quoted_names_without_commas_is_not_exponential() -> None:
    """existing_tests._FIELD_ERRORS: ``((?:\\s*\\"\\w+\\"\\s*,?)+)\\)`` — the blank between two quoted names can belong to the trailing ``\\s*`` of the first or the
    leading ``\\s*`` of the second, so k names separated by blanks (no comma) and a call that is not closed with ``)`` cost 2^k to 4^k.  A 110-byte line takes 3 s,
    a 125-byte line 30 s."""
    _time("""
        from tools.existing_tests import _outcomes
        BODY = 'attributeHasFieldErrors("x",' + ' "a" ' * 16 + "X"
        def run(): _outcomes(BODY)
    """, 0.5)
