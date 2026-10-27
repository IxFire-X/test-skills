"""FaT-Q: code hints on vague expectations in the canon and weak assertions in generated tests — never a stop, never a rewrite."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

_NO = object()


# --- canon: a small document of plain dicts ------------------------------------------------------

def _assert(name: str, operator: str = "equals", value=_NO, *, kind: str = "project_result", expected_kind: str = "literal") -> dict:
    row = {"assertion_id": "ASSERT-x", "display_order": 1, "actual": {"kind": "http_status"} if kind == "http_status" else {"kind": kind, "name": name}, "operator": operator}
    if value is not _NO:
        row["expected"] = {"kind": "literal", "value": value} if expected_kind == "literal" else {"kind": "step_output", "step_id": "STEP-x-0", "output_id": value}
    return row


def _step(step_id: str, assertions: list[dict], *, method: str | None = None, capability: str = "CAP-x-mvc", http: str | None = None, manual: bool = False) -> dict:
    operation = None if manual else ({"kind": "http", "binding_profile": "http-binding-v1", "base_url_source": {"kind": "fixture", "name": "base"}, "method": http, "path": "/x"}
                                     if http else {"kind": "project_action", "capability_id": capability})
    inputs = [] if method is None else [{"input_id": "INPUT-x", "display_order": 1, "target": {"location": "arg", "name": "method", "sensitive": False},
                                         "source": {"kind": "literal", "value": method}, "semantic_type": {"kind": "json", "type": "string"}}]
    return {"step_id": step_id, "display_order": 1, "action": "Секретный текст действия", "manual_only": manual, "operation": operation, "inputs": inputs,
            "outputs": [], "expectations": [{"expectation_id": "EXP-x", "display_order": 1, "text": "Секретный текст ожидания", "assertions": assertions}]}


def _document(*steps_by_case: list[dict]) -> dict:
    capabilities = [{"capability_id": f"CAP-x-{action}", "action": action} for action in
                    ("mvc", "owners_count", "owner_read", "open_owner_create_form", "owner_address_read", "submit_owner_create_form")]
    cases = [{"case_id": f"TC-x-{index:03d}", "requirement_ids": ["CREQ-x-001"], "categories": ["negative"], "objective": "Секретная цель кейса", "steps": steps}
             for index, steps in enumerate(steps_by_case, start=1)]
    return {"operation_capabilities": capabilities, "requirements": [{"requirement_id": "CREQ-x-001", "display_order": 1, "text": "Требование"}], "test_cases": cases}


def _hints(document: dict) -> list[dict]:
    from tools.test_design_hints import expectation_hints

    return expectation_hints(document)


def _codes(document: dict) -> list[str]:
    return [hint["code"] for hint in _hints(document)]


READ_SAME = _step("STEP-x-3", [_assert("count", "equals", "n", expected_kind="step_output")], capability="CAP-x-owners_count")


# --- TD_VAGUE_EXPECTATION -------------------------------------------------------------------------

def test_a_bare_failure_flag_is_a_vague_expectation() -> None:
    document = _document([_step("STEP-x-1", [_assert("failed", "equals", True)], method="GET"), READ_SAME])
    hints = _hints(document)
    assert [hint["code"] for hint in hints] == ["TD_VAGUE_EXPECTATION"]
    assert hints[0]["case_id"] == "TC-x-001" and hints[0]["step_id"] == "STEP-x-1" and "failed" in hints[0]["detail"]


@pytest.mark.parametrize("specific", [
    _assert("errorFields", "equals", ["name"]),
    _assert("status", "equals", 404),
    _assert("status", "equals", 404, kind="http_status"),
    _assert("exceptionClass", "equals", "OwnerNotFound"),
])
def test_a_failure_flag_with_something_concrete_is_not_vague(specific: dict) -> None:
    document = _document([_step("STEP-x-1", [_assert("failed", "equals", True), specific], method="GET"), READ_SAME])
    assert "TD_VAGUE_EXPECTATION" not in _codes(document)


def test_a_status_range_without_an_exact_code_is_vague_and_a_success_flag_is_not() -> None:
    ranged = _document([_step("STEP-x-1", [_assert("status", "greater_or_equal", 400)], method="GET")])
    success = _document([_step("STEP-x-1", [_assert("failed", "equals", False)], method="GET")])
    assert _codes(ranged) == ["TD_VAGUE_EXPECTATION"]
    assert _codes(success) == []


def test_manual_steps_are_not_checked() -> None:
    assert _codes(_document([_step("STEP-x-1", [_assert("failed", "equals", True)], manual=True)])) == []


# --- TD_NEGATIVE_NO_STATE_CHECK -------------------------------------------------------------------

REJECTED = [_assert("status", "equals", 200), _assert("errorFields", "equals", ["name"])]


def test_a_rejected_write_without_a_state_check_is_hinted() -> None:
    assert _codes(_document([_step("STEP-x-2", REJECTED, method="POST")])) == ["TD_NEGATIVE_NO_STATE_CHECK"]
    assert _codes(_document([_step("STEP-x-2", [_assert("status", "equals", 409, kind="http_status")], http="DELETE")])) == ["TD_NEGATIVE_NO_STATE_CHECK"]


@pytest.mark.parametrize("check", ["later-read", "same-step-delta", "same-step-snapshot"])
def test_a_rejected_write_with_a_state_check_is_not_hinted(check: str) -> None:
    if check == "later-read":
        steps = [_step("STEP-x-2", REJECTED, method="POST"), READ_SAME]
    elif check == "same-step-delta":
        steps = [_step("STEP-x-2", [*REJECTED, _assert("owners_added", "equals", 0)], method="POST")]
    else:
        steps = [_step("STEP-x-2", [*REJECTED, _assert("count", "equals", "n", expected_kind="step_output")], method="POST")]
    assert _codes(_document(steps)) == []


@pytest.mark.parametrize(("capability", "mutating"), [
    ("CAP-x-open_owner_create_form", False),
    ("CAP-x-owner_address_read", False),
    ("CAP-x-submit_owner_create_form", True),
])
def test_writes_are_told_from_reads_by_the_capability_action(capability: str, mutating: bool) -> None:
    document = _document([_step("STEP-x-2", REJECTED, capability=capability)])
    assert ("TD_NEGATIVE_NO_STATE_CHECK" in _codes(document)) is mutating


# --- TD_WEAK_SUCCESS ------------------------------------------------------------------------------

GENERIC_OK = [_assert("status", "equals", 302), _assert("failed", "equals", False), _assert("redirectId", "greater_than", 0)]


def test_a_write_confirmed_only_by_status_and_flags_is_a_weak_success() -> None:
    hints = _hints(_document([_step("STEP-x-1", GENERIC_OK, method="POST")]))
    assert [hint["code"] for hint in hints] == ["TD_WEAK_SUCCESS"] and hints[0]["step_id"] == "STEP-x-1"
    only_exists = _document([_step("STEP-x-1", GENERIC_OK, method="POST"), _step("STEP-x-2", [_assert("exists", "exists")], capability="CAP-x-owner_read")])
    assert _codes(only_exists) == ["TD_WEAK_SUCCESS"]


def test_a_write_followed_by_a_concrete_read_is_not_weak() -> None:
    read = _step("STEP-x-2", [_assert("firstName", "equals", "Anna")], capability="CAP-x-owner_read")
    assert _codes(_document([_step("STEP-x-1", GENERIC_OK, method="POST"), read])) == []
    exact = _step("STEP-x-1", [*GENERIC_OK, _assert("firstName", "equals", "Anna")], method="POST")
    assert _codes(_document([exact])) == []


def test_only_the_last_write_of_a_case_is_judged() -> None:
    setup = _step("STEP-x-1", GENERIC_OK, method="POST")
    subject = _step("STEP-x-2", [*GENERIC_OK, _assert("lastName", "equals", "Smith")], method="POST")
    assert _codes(_document([setup, subject])) == []


# --- shape, order, silence on a strong set --------------------------------------------------------

def test_hints_carry_ids_only_and_fit_the_receipt_schema() -> None:
    from tools.batch_assembly import _ASSEMBLY_SCHEMA
    from tools.schema_validation import schema_diagnostics
    from tools.test_design_hints import capped

    document = _document([_step("STEP-x-1", [_assert("failed", "equals", True)], method="GET")],
                         [_step("STEP-x-2", REJECTED, method="POST")], [_step("STEP-x-3", GENERIC_OK, method="POST")])
    document["test_cases"][0]["case_id"] = "TC-" + "y" * 300
    hints = _hints(document)
    assert sorted(hint["code"] for hint in hints) == ["TD_NEGATIVE_NO_STATE_CHECK", "TD_VAGUE_EXPECTATION", "TD_WEAK_SUCCESS"]
    assert all(set(hint) == {"code", "case_id", "step_id", "detail"} and len(hint["detail"]) <= 200 for hint in hints)
    assert "Секрет" not in json.dumps(hints, ensure_ascii=False)
    receipt = {"schema_version": "1.0.0", "plan_digest": "sha256:" + "a" * 64, "header_digest": "sha256:" + "b" * 64,
               "fragment_digests": [{"batch_id": "BATCH-a", "digest": "sha256:" + "c" * 64}], "document_digest": "sha256:" + "d" * 64,
               "pre_review_audit": {"schema": "PASS", "semantic": "PASS", "coverage": "PASS", "provenance": "PASS"},
               "warnings": capped(hints), "digest": "sha256:" + "e" * 64}
    assert [row for row in schema_diagnostics(receipt, _ASSEMBLY_SCHEMA, Path.cwd()) if row["path"].startswith("/warnings")] == []


def test_code_hints_put_a_vague_expectation_after_a_missing_negative_case_and_before_a_boundary() -> None:
    from tests.test_test_design import _designed
    from tools.test_design_hints import code_hints

    document = _designed()
    vague = _document([_step("STEP-x-1", [_assert("failed", "equals", True)], method="GET")])
    document["operation_capabilities"] = vague["operation_capabilities"]
    document["test_cases"][0]["steps"] = vague["test_cases"][0]["steps"]
    codes = [hint["code"] for hint in code_hints(document)]
    assert codes.index("TD_NEGATIVE_MISSING") < codes.index("TD_VAGUE_EXPECTATION") < codes.index("TD_BOUNDARY_MISSING")


def test_the_strong_example_set_gives_no_expectation_hints() -> None:
    example = Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
    assert _hints(json.loads(example.read_text(encoding="utf-8"))) == []


def test_old_fixtures_give_no_expectation_hints() -> None:
    from tests.test_requirement_traceability import canonical_fixture
    from tests.test_test_design import _designed

    assert _hints(canonical_fixture()) == [] and _hints(_designed()) == []


def test_the_assembly_receipt_carries_a_vague_expectation() -> None:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh
    from tools.batch_assembly import assemble_candidate

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    _document_plain, plain = assemble_candidate(header, plan, fragments)
    fragment = fragments[0]
    fragment["operation_capabilities"][0]["results"] = [{"name": "failed", "semantic_type": {"kind": "json", "type": "boolean"}}]
    fragments[1]["operation_capabilities"][0]["results"] = copy.deepcopy(fragment["operation_capabilities"][0]["results"])
    step = fragment["test_cases"][0]["steps"][0]
    step.update(manual_only=False, manual_reason=None, operation={"kind": "project_action", "capability_id": "CAP-shared"})
    step["expectations"][0]["assertions"] = [{"assertion_id": "ASSERT-" + step["step_id"].removeprefix("STEP-"), "display_order": 1,
                                              "actual": {"kind": "project_result", "name": "failed"}, "operator": "equals",
                                              "expected": {"kind": "literal", "value": True}}]
    _refresh(fragment)
    _refresh(fragments[1])
    _document_vague, vague = assemble_candidate(header, plan, fragments)
    hints = [warning for warning in vague["warnings"] if warning["code"].startswith("TD_")]
    assert hints == [{"code": "TD_VAGUE_EXPECTATION", "case_id": fragment["test_cases"][0]["case_id"], "step_id": step["step_id"], "detail": "только failed=true"}]
    assert not [warning for warning in plain["warnings"] if warning["code"].startswith("TD_")]


def test_the_report_lists_expectation_hints_without_case_text() -> None:
    from tools.run_report import expectation_summary, render_expectations

    document = _document([_step("STEP-x-1", [_assert("failed", "equals", True)], method="GET")], [_step("STEP-x-2", REJECTED, method="POST")])
    summary = expectation_summary(document)
    assert summary["hints"] == {"TD_VAGUE_EXPECTATION": 1, "TD_NEGATIVE_NO_STATE_CHECK": 1}
    markdown = render_expectations(summary)
    assert markdown.startswith("## Ожидания кейсов") and "TC-x-001 STEP-x-1" in markdown
    assert "Секрет" not in markdown + json.dumps(summary, ensure_ascii=False)
    assert "нет" in render_expectations(expectation_summary(_document([READ_SAME])))


# --- generated tests: weak assertions -------------------------------------------------------------

JAVA = '''class OwnerTests {
    private static void ge(String assertionId, Object actual, Object expected) {
        assertThat(((Number) actual).longValue()).as(assertionId).isGreaterThanOrEqualTo(((Number) expected).longValue());
    }

    private static void contains(String assertionId, Object actual, Object expected) {
        assertThat((String) actual).as(assertionId).contains((String) expected);
    }

    private Map<String, Object> mvc() {
        try {
            return perform();
        }
        catch (Exception ex) {
            result.put("failed", true);
            return result;
        }
    }

    private Object owner() {
        try {
            return read();
        }
        catch (Exception ex) {
            throw new IllegalStateException("owner read failed", ex);
        }
    }

    @Test
    void tc_001() {
        // catch (Exception e) { in a comment is not code
        ge("ASSERT-001-1", s1.get("count"), 0);
        ge("ASSERT-001-2", s1.get("count"), 1);
        contains("ASSERT-001-3", s2.get("redirectedUrl"), "/owners/");
        contains("ASSERT-001-4", s2.get("content"), "Anna");
        assertThrows(Exception.class, () -> service.find(7));
        assertThrows(OwnerNotFoundException.class, () -> service.find(8));
        assertThat(count).isGreaterThanOrEqualTo(0);
        assertTrue(true);
    }

    @Test
    void tc_002() {
        Object owner = owner();
        assertNotNull(owner);
    }

    @Test
    void tc_003() {
        assertNotNull(owner());
        assertEquals("Anna", name);
    }

    void multi() {
        try { run(); } catch (IOException | RuntimeException e) { log(e); }
    }
}
'''

PYTHON = '''import pytest


def test_one(client):
    with pytest.raises(Exception):
        client.find(7)
    with pytest.raises(ValueError):
        client.find(8)
    assert count >= 0
    assert "/owners/" in url  # ASSERT-002-1
    assert url == "/owners/7"
    assert "#" in text


def test_two(client):
    owner = client.find(1)
    assert owner is not None


def helper():
    try:
        run()
    except Exception:
        pass
    try:
        run()
    except Exception as error:
        raise RuntimeError("wrapped") from error
'''


def _file_hints(content: str, language: str) -> list[tuple[str, int, str]]:
    from tools.autotest_hints import autotest_hints

    artifact = {"artifacts": {"generated_files": [{"file_id": "FILE-a", "language": language, "content": content}]}}
    return [(hint["code"], hint["line"], hint["rule"]) for hint in autotest_hints(artifact)]


def _line(content: str, needle: str) -> int:
    return next(index for index, line in enumerate(content.splitlines(), start=1) if needle in line)


def test_java_weak_assertions_are_found_with_their_lines() -> None:
    hints = _file_hints(JAVA, "java")
    assert ("AT_ANY_EXCEPTION", _line(JAVA, 'result.put("failed", true)') - 1, "catch-all") in hints
    assert ("AT_ANY_EXCEPTION", _line(JAVA, "assertThrows(Exception.class"), "broad-throws") in hints
    assert ("AT_ANY_EXCEPTION", _line(JAVA, "catch (IOException | RuntimeException e)"), "catch-all") in hints
    assert ("AT_ALWAYS_TRUE", _line(JAVA, '"ASSERT-001-1"'), "ge-zero") in hints
    assert ("AT_ALWAYS_TRUE", _line(JAVA, "isGreaterThanOrEqualTo(0)"), "ge-zero") in hints
    assert ("AT_ALWAYS_TRUE", _line(JAVA, "assertTrue(true)"), "assert-true") in hints
    assert ("AT_ALWAYS_TRUE", _line(JAVA, "assertNotNull(owner);"), "not-null-only") in hints
    assert ("AT_PARTIAL_MATCH", _line(JAVA, '"/owners/"'), "path-prefix") in hints
    assert len(hints) == 8  # the rethrowing catch, the comment, ge(…, 1), contains "Anna", the exact class and tc_003 stay silent


def test_python_weak_assertions_are_found_with_their_lines() -> None:
    hints = _file_hints(PYTHON, "python")
    assert sorted(hints) == sorted([
        ("AT_ANY_EXCEPTION", _line(PYTHON, "pytest.raises(Exception)"), "broad-throws"),
        ("AT_ALWAYS_TRUE", _line(PYTHON, "count >= 0"), "ge-zero"),
        ("AT_PARTIAL_MATCH", _line(PYTHON, '"/owners/" in url'), "path-prefix"),
        ("AT_ALWAYS_TRUE", _line(PYTHON, "owner is not None"), "not-null-only"),
        ("AT_ANY_EXCEPTION", _line(PYTHON, "except Exception:"), "catch-all"),
    ])


def test_a_hint_names_the_assertion_id_on_its_line_and_never_the_code() -> None:
    from tools.autotest_hints import autotest_hints

    hints = autotest_hints({"generated_files": [{"file_id": "FILE-a", "language": "java", "content": JAVA}, {"file_id": "FILE-b", "language": "kotlin", "content": "assertTrue(true)"}]})
    by_rule = {hint["rule"]: hint for hint in hints if hint["rule"] == "path-prefix"}
    assert by_rule["path-prefix"]["assertion_id"] == "ASSERT-001-3"
    assert all(set(hint) <= {"code", "file_id", "line", "rule", "assertion_id"} and hint["file_id"] == "FILE-a" for hint in hints)
    assert "redirectedUrl" not in json.dumps(hints)


def test_autotest_hints_are_capped() -> None:
    from tools.autotest_hints import autotest_hints, capped_autotest_hints
    from tools.test_design_hints import HINT_BYTES

    content = "\n".join(f'        assertTrue(true); // ASSERT-{index:04d}' for index in range(500))
    kept = capped_autotest_hints(autotest_hints({"generated_files": [{"file_id": "FILE-a", "language": "java", "content": content}]}))
    assert len(json.dumps(kept, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= HINT_BYTES
    assert kept[-1]["code"] == "AT_MORE" and kept[-1]["detail"] == f"и ещё {500 - (len(kept) - 1)}"


def test_the_cli_prints_json_and_never_fails_on_hints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.autotest_hints import main

    artifact = tmp_path / "automation.json"
    artifact.write_text(json.dumps({"artifacts": {"generated_files": [{"file_id": "FILE-a", "language": "java", "content": JAVA}]}}), encoding="utf-8")
    assert main(["--automation", str(artifact)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok" and output["counts"]["AT_ANY_EXCEPTION"] == 3 and len(output["hints"]) == 8
    assert main(["--automation", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_the_report_counts_weak_assertions_of_the_run_automation(tmp_path: Path) -> None:
    from tests.test_run_report import _finished_run
    from tools.run_report import write_report

    run_root = _finished_run(tmp_path)
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    summary = report["autotest_assertions"]
    assert summary["hints"] == {"AT_ALWAYS_TRUE": 1}  # `assert True` in test_other_two
    assert "## Слабые утверждения автотестов" in markdown and "assert True" not in markdown


# --- the skills name every code the reviewers can see ---------------------------------------------

def test_reviewer_skills_name_every_hint_code_and_the_generators_say_what_a_refusal_is() -> None:
    root = Path(__file__).resolve().parents[1] / "skills"
    reviewer = (root / "tc-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    for code in ("TD_VAGUE_EXPECTATION", "TD_NEGATIVE_NO_STATE_CHECK", "TD_WEAK_SUCCESS"):
        assert code in reviewer
    automation_reviewer = (root / "autotest-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    assert "python -m tools.autotest_hints --automation" in automation_reviewer
    for code in ("AT_ANY_EXCEPTION", "AT_ALWAYS_TRUE", "AT_PARTIAL_MATCH"):
        assert code in automation_reviewer
    generator = (root / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
    assert "что именно наблюдается" in generator and "из требований" in generator and "нет в документе" in generator
    assert "конкретный класс исключения" in (root / "tc-to-autotest" / "SKILL.md").read_text(encoding="utf-8")
