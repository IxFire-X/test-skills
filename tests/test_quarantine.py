"""FaT-8: a FAIL run quarantines only its failed methods and keeps the rest of the generated tests."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

JAVA = """package org.example;

import org.junit.jupiter.api.Test;

class OwnerTest {
    @Test
    void createsOwner() {
        org.junit.jupiter.api.Assertions.assertEquals(1, 1);
    }

    @Test
    void findsOwner() {
        org.junit.jupiter.api.Assertions.assertEquals(2, 3);
    }

    @Test
    void deletesOwner() {
        org.junit.jupiter.api.Assertions.assertTrue(true);
    }
}
"""

THREE_FUNCTIONS = '''import pytest

from sample import combine


@pytest.mark.native
def test_selected(plugin_value: str, conftest_value: str) -> None:
    assert combine(plugin_value, conftest_value) == "from-project-plugin:from-conftest"


def test_other_one() -> None:
    assert 1 == 1


def test_other_two() -> None:
    assert True
'''

FAILURE = "ASSERT-batch-a-001 expected: <2> but was: <3>\n\tat org.example.OwnerTest.findsOwner"


def _java_locator(method: str) -> dict:
    return {"kind": "java_class_method", "class_fqn": "org.example.OwnerTest", "method_name": method}


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --- marks and the note -----------------------------------------------------------------------

def test_the_note_keeps_the_assertion_ref_and_a_short_safe_first_line() -> None:
    from tools.quarantine import failure_note

    assert failure_note(FAILURE) == ("ASSERT-batch-a-001", "expected: _2_ but was: _3_")
    ref, message = failure_note('boom "); System.exit(1); //' + "x" * 300)
    assert ref == "FAIL" and len(message) <= 120 and '"' not in message and ";" not in message
    assert failure_note("") == ("FAIL", "")


def test_java_three_methods_one_failed_marks_only_that_method() -> None:
    from tools.quarantine import failure_note, quarantine

    ref, message = failure_note(FAILURE)
    changed = quarantine("src/test/java/org/example/OwnerTest.java", JAVA, [(_java_locator("findsOwner"), ref, message)])
    added = [line for line in changed.splitlines() if line not in JAVA.splitlines()]
    assert added == [f'    @org.junit.jupiter.api.Disabled("test-skills quarantine: {ref} {message}")']
    assert changed.replace(added[0] + "\n", "") == JAVA


def test_java_all_methods_failed_marks_every_method_and_keeps_the_file() -> None:
    from tools.quarantine import quarantine

    methods = [(_java_locator(name), "FAIL", "boom") for name in ("createsOwner", "findsOwner", "deletesOwner")]
    changed = quarantine("src/test/java/org/example/OwnerTest.java", JAVA, methods)
    assert changed.count("@org.junit.jupiter.api.Disabled(") == 3 and "class OwnerTest" in changed


def test_pytest_marks_with_strict_xfail_and_adds_the_import() -> None:
    from tools.quarantine import quarantine

    source = "def test_a():\n    assert True\n\n\ndef test_b():\n    assert False\n"
    changed = quarantine("tests/test_x.py", source, [({"kind": "python_module_function", "function_name": "test_b"}, "FAIL", "boom")])
    ast.parse(changed)
    assert changed.splitlines()[0] == "import pytest"
    assert '@pytest.mark.xfail(strict=True, reason="test-skills quarantine: FAIL boom")' in changed.splitlines()
    assert "def test_a():\n    assert True\n" in changed


def test_two_files_one_failed_method_quarantines_one_file_and_retains_the_other() -> None:
    from tools.generated_delta import plan_quarantine

    other = "def test_second():\n    assert True\n"
    files = [
        {"file_id": "FILE-a", "path": "tests/test_a.py", "content": THREE_FUNCTIONS},
        {"file_id": "FILE-b", "path": "tests/test_b.py", "content": other},
    ]
    symbols = [
        {"file_id": "FILE-a", "symbol_id": "SYMBOL-a", "locator": {"kind": "python_module_function", "function_name": "test_selected"}},
        {"file_id": "FILE-b", "symbol_id": "SYMBOL-b", "locator": {"kind": "python_module_function", "function_name": "test_second"}},
    ]
    payload = {
        "verdict": "FAIL", "evidence_authoritative": True,
        "execution_evidence": [{"file_id": "FILE-a", "symbol_id": "SYMBOL-a", "status": "FAILED"}, {"file_id": "FILE-b", "symbol_id": "SYMBOL-b", "status": "PASSED"}],
        "failed_methods": [{"nodeid": "FILE-a::SYMBOL-a", "kind": "assertion_failed", "message": FAILURE}],
    }
    plan = plan_quarantine(files, symbols, payload)
    assert plan["tests/test_b.py"] == {"quarantine": [], "quarantined_digest": None, "content": None}
    entry = plan["tests/test_a.py"]
    assert entry["quarantine"] == [{"method": "test_selected", "ref": "ASSERT-batch-a-001", "message": "expected: _2_ but was: _3_"}]
    assert entry["quarantined_digest"] == _digest(entry["content"]) and "def test_other_one() -> None:\n    assert 1 == 1\n" in entry["content"].decode()


def test_java_plan_quarantine_marks_only_the_failed_method() -> None:
    from tools.generated_delta import plan_quarantine

    path = "src/test/java/org/example/OwnerTest.java"
    names = ("createsOwner", "findsOwner", "deletesOwner")
    files = [{"file_id": "FILE-j", "path": path, "content": JAVA}]
    symbols = [{"file_id": "FILE-j", "symbol_id": f"SYMBOL-{name}", "locator": _java_locator(name)} for name in names]
    payload = {
        "verdict": "FAIL", "evidence_authoritative": True,
        "execution_evidence": [
            {"file_id": "FILE-j", "symbol_id": f"SYMBOL-{name}", "status": "FAILED" if name == "findsOwner" else "PASSED"} for name in names
        ],
        "failed_methods": [{"nodeid": "FILE-j::SYMBOL-findsOwner", "kind": "assertion_failed", "message": FAILURE}],
    }
    entry = plan_quarantine(files, symbols, payload)[path]
    assert entry["quarantine"] == [{"method": "OwnerTest.findsOwner", "ref": "ASSERT-batch-a-001", "message": "expected: _2_ but was: _3_"}]
    text = entry["content"].decode()
    mark = '    @org.junit.jupiter.api.Disabled("test-skills quarantine: ASSERT-batch-a-001 expected: _2_ but was: _3_")'
    lines = text.splitlines()
    assert lines[lines.index("    void findsOwner() {") - 2] == mark
    assert text.replace(mark + "\n", "") == JAVA and entry["quarantined_digest"] == _digest(entry["content"])


@pytest.mark.parametrize("payload", [
    {"verdict": "FAIL", "evidence_authoritative": False, "execution_evidence": [{"file_id": "FILE-a", "symbol_id": "SYMBOL-a", "status": "FAILED"}], "failed_methods": None},
    {"verdict": "FAIL", "evidence_authoritative": True, "execution_evidence": [{"file_id": "FILE-a", "symbol_id": "SYMBOL-a", "status": "PASSED"}], "failed_methods": None},
    {"verdict": "FAIL", "evidence_authoritative": True, "execution_evidence": [{"file_id": "FILE-a", "symbol_id": "SYMBOL-x", "status": "ERROR"}], "failed_methods": None},
])
def test_a_failure_that_cannot_be_tied_to_a_method_is_unresolved(payload: dict) -> None:
    from tools.generated_delta import plan_quarantine

    files = [{"file_id": "FILE-a", "path": "tests/test_a.py", "content": THREE_FUNCTIONS}]
    symbols = [{"file_id": "FILE-a", "symbol_id": "SYMBOL-a", "locator": {"kind": "python_module_function", "function_name": "test_selected"}}]
    assert plan_quarantine(files, symbols, payload) is None


# --- the durable FAIL branch ------------------------------------------------------------------

def _fail(tmp_path: Path, *, content: str | None = None, message: str | None = FAILURE):
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts

    run_root, attempt_id, report, request, durable = _execution_facts(
        tmp_path, project=tmp_path, failed=True, generated_content=content,
    )
    if message is not None:
        report["failed_methods"] = [{"nodeid": "FILE-products::SYMBOL-products", "kind": "assertion_failed", "message": message}]
    _publish_execution(run_root, attempt_id, report, request=request)
    return run_root, attempt_id, durable["delta"]


def _receipt(run_root: Path, attempt_id: str) -> dict:
    from tools import pilot_state

    return pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]


def test_fail_quarantines_the_failed_method_and_keeps_the_others(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions

    run_root, attempt_id, delta = _fail(tmp_path, content=THREE_FUNCTIONS)
    generated = tmp_path / delta["files"][0]["path"]
    result = decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)

    assert [row["disposition"] for row in result["files"]] == ["QUARANTINED"]
    text = generated.read_text(encoding="utf-8")
    mark = '@pytest.mark.xfail(strict=True, reason="test-skills quarantine: ASSERT-batch-a-001 expected: _2_ but was: _3_")'
    assert [line for line in text.splitlines() if line not in THREE_FUNCTIONS.splitlines()] == [mark]
    [row] = _receipt(run_root, attempt_id)["files"]
    assert row["quarantine"] == [{"method": "test_selected", "ref": "ASSERT-batch-a-001", "message": "expected: _2_ but was: _3_"}]
    assert row["quarantined_digest"] == _digest(generated.read_bytes()) != row["content_digest"]


def test_an_interruption_after_the_quarantine_write_closes_once_on_retry(tmp_path: Path) -> None:
    from tools.generated_delta import GeneratedDeltaError, apply_dispositions

    run_root, attempt_id, delta = _fail(tmp_path, content=THREE_FUNCTIONS)
    relative = delta["files"][0]["path"]
    generated = tmp_path / relative
    with pytest.raises(GeneratedDeltaError, match="MATERIALIZATION_INTERRUPTED"):
        apply_dispositions(
            tmp_path, delta, {relative: "QUARANTINED"}, run_root=run_root, attempt_id=attempt_id,
            verification="FAIL", interrupt_after_effects=1,
        )
    result = apply_dispositions(tmp_path, delta, {relative: "QUARANTINED"}, run_root=run_root, attempt_id=attempt_id, verification="FAIL")

    assert [row["disposition"] for row in result["files"]] == ["QUARANTINED"]
    assert generated.read_text(encoding="utf-8").count("test-skills quarantine:") == 1
    [row] = _receipt(run_root, attempt_id)["files"]
    assert row["quarantined_digest"] == _digest(generated.read_bytes())


def test_a_file_changed_after_writing_is_not_touched(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions

    run_root, attempt_id, delta = _fail(tmp_path)
    generated = tmp_path / delta["files"][0]["path"]
    generated.write_text(generated.read_text(encoding="utf-8") + "# edited by a person\n", encoding="utf-8")
    before = generated.read_bytes()
    result = decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)

    assert [(row["disposition"], row["reason_code"]) for row in result["files"]] == [("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")]
    assert generated.read_bytes() == before


def test_an_unresolved_failure_cleans_as_before_and_says_why(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions

    twice = "def test_selected():\n    assert False\n\n\ndef test_selected():\n    assert False\n"
    run_root, attempt_id, delta = _fail(tmp_path, content=twice)
    result = decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)

    assert [(row["disposition"], row["reason_code"]) for row in result["files"]] == [("CLEANED", "QUARANTINE_UNRESOLVED")]
    assert not (tmp_path / delta["files"][0]["path"]).exists()


def test_cleanup_policy_keeps_the_old_cleaning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.finalize_attempt import decide_dispositions

    monkeypatch.setattr(pilot_state, "_disposition_policy_at", lambda project, root: "cleanup")
    run_root, attempt_id, delta = _fail(tmp_path)
    result = decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)

    assert [row["disposition"] for row in result["files"]] == ["CLEANED"]
    assert not (tmp_path / delta["files"][0]["path"]).exists()


def test_quarantine_summary_lists_file_method_and_ref(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions
    from tools.run_pipeline import _quarantine_summary

    run_root, attempt_id, delta = _fail(tmp_path)
    decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    summary = _quarantine_summary(run_root, attempt_id)
    assert summary["quarantine"] == [{"file": delta["files"][0]["path"], "method": "test_selected", "ref": "ASSERT-batch-a-001",
                                      "message": "expected: _2_ but was: _3_"}]
    assert "quarantine_unresolved" not in summary


def test_quarantine_summary_names_an_unresolved_cleanup(tmp_path: Path) -> None:
    from tools.finalize_attempt import decide_dispositions
    from tools.run_pipeline import _quarantine_summary

    twice = "def test_selected():\n    assert False\n\n\ndef test_selected():\n    assert False\n"
    run_root, attempt_id, delta = _fail(tmp_path, content=twice)
    decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    summary = _quarantine_summary(run_root, attempt_id)
    assert summary["quarantine"] == [] and "QUARANTINE_UNRESOLVED" in summary["quarantine_unresolved"]


def test_not_runnable_still_cleans(tmp_path: Path) -> None:
    from tests.test_durable_execution_trace import _prestart_report, _publish_execution
    from tests.test_execution_receipt import _execution_facts
    from tools.finalize_attempt import decide_dispositions

    run_root, attempt_id, _report, _request, durable = _execution_facts(tmp_path, project=tmp_path)
    _publish_execution(run_root, attempt_id, _prestart_report(run_root, durable))
    result = decide_dispositions(tmp_path, durable["delta"], "NOT_RUNNABLE", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    assert [row["disposition"] for row in result["files"]] == ["CLEANED"]


def test_fail_with_quarantine_is_still_not_accepted(tmp_path: Path) -> None:
    from tests.test_finalization_branches import _terminal_facts
    from tools import pilot_state

    run_root, attempt_id, _delta = _fail(tmp_path)
    run_id = pilot_state.read_run(run_root)["manifest"]["run_id"]
    terminal = pilot_state.terminal_result(_terminal_facts(run_id, attempt_id, verification="FAIL"), "local-pilot-v1")
    assert (terminal["verification"], terminal["accepted"]) == ("FAIL", False)


def test_finish_closes_a_failed_run_with_a_quarantined_method_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FaT-WT: FAIL with ``retained > 0`` through ``finish`` — the closing reads the quarantined file and the receipt and
    still never accepts the attempt (the unit test above builds the facts by hand, without quarantine)."""
    from types import SimpleNamespace

    from tools import pilot_state
    from tools.run_pipeline import cmd_finish

    run_root, attempt_id, delta = _fail(tmp_path, content=THREE_FUNCTIONS)
    code = cmd_finish(SimpleNamespace(project=str(tmp_path), run=run_root.name))
    output = json.loads(capsys.readouterr().out)
    assert output["branch"] == "execution" and output["verification"] == "FAIL" and output["accepted"] is False, output
    terminal = pilot_state.read_terminal_result(run_root, attempt_id)
    assert (terminal["verification"], terminal["accepted"]) == ("FAIL", False), terminal
    [row] = _receipt(run_root, attempt_id)["files"]
    assert row["disposition"] == "QUARANTINED" and [item["method"] for item in row["quarantine"]] == ["test_selected"], row
    text = (tmp_path / delta["files"][0]["path"]).read_text(encoding="utf-8")
    assert text.count("test-skills quarantine: ASSERT-batch-a-001") == 1 and "def test_other_one" in text
    assert code == 1, (code, output)  # a verdict other than PASS is not a green exit


# --- the choice is made once, at scan ---------------------------------------------------------

def _receipt_bytes(run: dict) -> bytes:
    return Path(run["run_root"], "run-authorization-receipt.json").read_bytes()


def test_cleanup_is_sealed_in_the_receipt_and_quarantine_leaves_it_unchanged(tmp_path: Path) -> None:
    from tools.pilot_state import create_run, disposition_policy

    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    plain = create_run(tmp_path / "a", "local-pilot-v1", {"request_id": "r", "execution_requested": True})
    assert b"disposition_policy" not in _receipt_bytes(plain) and disposition_policy(Path(plain["run_root"])) == "quarantine"
    cleanup = create_run(tmp_path / "b", "local-pilot-v1", {"request_id": "r", "execution_requested": True, "disposition_policy": "cleanup"})
    assert cleanup["authorization"]["disposition_policy"] == "cleanup" and disposition_policy(Path(cleanup["run_root"])) == "cleanup"
    with pytest.raises(ValueError):
        create_run(tmp_path / "c", "local-pilot-v1", {"request_id": "r", "execution_requested": True, "disposition_policy": "quarantine"})


def _skillsrc(policy: str) -> dict:
    fixture = Path(__file__).resolve().parent / "fixtures" / "projects" / "python-pytest" / ".skillsrc"
    return {**json.loads(fixture.read_text(encoding="utf-8")), "disposition_policy": policy}


@pytest.mark.parametrize(("skillsrc", "flag", "expected"), [
    (None, None, None),
    ("cleanup", None, "cleanup"),
    ("cleanup", "quarantine", None),
    (None, "cleanup", "cleanup"),
])
def test_scan_reads_skillsrc_once_and_the_flag_wins(tmp_path: Path, skillsrc: str | None, flag: str | None, expected: str | None) -> None:
    from types import SimpleNamespace

    from tools.run_pipeline import _scan_disposition_policy

    if skillsrc is not None:  # FaT-B: a valid .skillsrc (load_skillsrc checks the schema), here in JSON
        (tmp_path / ".skillsrc").write_text(json.dumps(_skillsrc(skillsrc)), encoding="utf-8")
    assert _scan_disposition_policy(tmp_path, SimpleNamespace(disposition_policy=flag)) == expected


def test_scan_reads_the_policy_from_a_yaml_skillsrc_fat_b(tmp_path: Path) -> None:
    from types import SimpleNamespace

    import yaml

    from tools.run_pipeline import _scan_disposition_policy

    (tmp_path / ".skillsrc").write_text(yaml.safe_dump(_skillsrc("cleanup"), allow_unicode=True), encoding="utf-8")
    assert _scan_disposition_policy(tmp_path, SimpleNamespace(disposition_policy=None)) == "cleanup"
    (tmp_path / ".skillsrc").write_text("disposition_policy: [\n", encoding="utf-8")
    assert _scan_disposition_policy(tmp_path, SimpleNamespace(disposition_policy=None)) is None


def test_a_junit_failure_keeps_its_first_line_for_the_note() -> None:
    from tools.run_tests import parse_junit_bytes

    parsed = parse_junit_bytes(b"<testsuite><testcase classname='a' name='b'><failure message='ASSERT-x boom&#10;more'/></testcase>"
                               b"<testcase classname='a' name='c'/></testsuite>")
    assert [(case.status, case.message) for case in parsed.cases] == [("FAILED", "ASSERT-x boom"), ("PASSED", "")]


def test_a_skillsrc_with_the_policy_key_still_validates(tmp_path: Path) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    document = json.loads(Path("tests/fixtures/projects/python-pytest/.skillsrc").read_text(encoding="utf-8"))
    document["disposition_policy"] = "cleanup"
    (tmp_path / ".skillsrc").write_text(json.dumps(document), encoding="utf-8")
    assert load_skillsrc(tmp_path / ".skillsrc")["disposition_policy"] == "cleanup"
