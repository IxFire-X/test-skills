"""Wave 3 F: the quarantine disposition policy of local-pilot-v1 (W3-Р4, W3-Р6) on real Maven."""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from tests.test_suite_manifest import GENERATED, _local, needs_java
from tools.pilot_state import derive_state, read_attempt_receipt
from tools.quarantine import EXPLICIT_RUN

CONTROLLER = "src/main/java/net/javaguides/springboot/controller/StudentController.java"
CLASS = "net.javaguides.springboot.controller.StudentControllerPipelineTest"


def _change_product(project: Path) -> str:
    """The product deletes with another message: behaviour changed, the requirement did not."""
    path = project / CONTROLLER
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"Student Successfully Deleted!"', '"Student removed"'), encoding="utf-8")
    return original


def _maven(project: Path, *extra: str) -> dict[str, str]:
    wrapper = project / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    subprocess.run([str(wrapper), "-q", "-B", "-ntp", f"-Dtest={CLASS}", *extra, "test"], cwd=project, capture_output=True, text=True, timeout=600, check=False)
    report = project / "target" / "surefire-reports" / f"TEST-{CLASS}.xml"
    statuses = {}
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        statuses[case.get("name")] = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
    return statuses


@needs_java
def test_quarantine_keeps_the_file_and_disables_only_the_failed_method(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    original = _change_product(replay.project)
    code, done = replay.drive(replay.start()[1])  # quarantine is the default of local-pilot-v1 since the wave-3 gate
    result = done["result"]
    assert (result["verification"], result["accepted"]) == ("FAIL", False) and result["reason_code"] is None  # acceptance unchanged
    run_root = replay.run_root(done)
    attempt_id = result["attempt_id"]
    plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    final = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    [plan_row] = plan["files"]
    [final_row] = final["files"]
    assert plan_row["operation"] == "QUARANTINE_IF_EXACT" and final_row["disposition"] == "QUARANTINED"
    assert final_row["reason_code"] == "FAILED_METHODS_QUARANTINED"
    [symbol] = plan_row["quarantine_symbols"]
    derive_state(run_root)  # every receipt re-validates
    text = (replay.project / GENERATED).read_text(encoding="utf-8")
    [mark] = [line for line in text.splitlines() if "test-skills quarantine" in line]
    assert mark.strip() == f'@org.junit.jupiter.api.Disabled("test-skills quarantine: ASSERTION_FAILED run {run_root.name[:8]} {symbol}")'
    # The ordinary run of the project skips only the quarantined method; the rest is green.
    ordinary = _maven(replay.project)
    assert sorted(name for name, status in ordinary.items() if status == "skipped") == [name for name, status in ordinary.items() if status == "skipped"]
    assert list(ordinary.values()).count("skipped") == 1 and "failed" not in ordinary.values()
    # Run explicitly, the quarantined method still fails while the defect is there ...
    explicit = _maven(replay.project, EXPLICIT_RUN["junit5"])
    assert list(explicit.values()).count("failed") == 1 and "skipped" not in explicit.values()
    # ... and passes once the product behaves as the requirement says again.
    (replay.project / CONTROLLER).write_text(original, encoding="utf-8")
    fixed = _maven(replay.project, EXPLICIT_RUN["junit5"])
    assert set(fixed.values()) == {"passed"}
    # Calling the driver again changes nothing.
    before = (replay.project / GENERATED).read_bytes()
    replay.next(result["run_id"])
    assert (replay.project / GENERATED).read_bytes() == before


@needs_java
def test_the_cleanup_policy_still_cleans_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    _change_product(replay.project)
    _code, done = replay.drive(replay.start_with("--disposition-policy", "cleanup")[1])
    assert done["result"]["verification"] == "FAIL"
    final = read_attempt_receipt(replay.run_root(done), done["result"]["attempt_id"], "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [row["disposition"] for row in final["files"]] == ["CLEANED"] and not (replay.project / GENERATED).exists()


@pytest.mark.parametrize("evidence", [[], [{"file_id": "F1", "symbol_id": "S1", "status": "PASSED"}], None])
def test_a_fail_without_failed_methods_falls_back_to_cleanup(evidence) -> None:
    """Review 2.1 item 2 (A4.1): only a FAIL with per-method outcomes is quarantined; an empty report
    (NO_TESTS_COLLECTED, a wrong module) keeps the frozen cleanup of §17 item 2 instead of retaining every file."""
    from tools.generated_delta import quarantine_modes

    payload = {"execution_evidence": evidence, "process_evidence": [{"kind": "NO_TESTS_COLLECTED"}]}
    assert quarantine_modes({"disposition_policy": "quarantine"}, "FAIL", payload) is None
    failed = {"execution_evidence": [{"file_id": "F1", "symbol_id": "S1", "status": "FAILED"}]}
    assert quarantine_modes({"disposition_policy": "quarantine"}, "FAIL", failed) == {"F1": {"S1": "FAILED"}}


@needs_java
def test_the_suite_after_a_quarantined_fail_keeps_the_quarantine_with_reason_ref_and_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2.1 item 6 (A5.2): --suite after a FAIL writes the failed method's case as QUARANTINED, not ACTIVE."""
    from tools.suite_manifest import read_suite, verify

    replay = _local(tmp_path, monkeypatch)
    _change_product(replay.project)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    result = done["result"]
    assert result["verification"] == "FAIL" and result["suite"]["status"] == "WRITTEN", result["suite"]
    run_id = result["run_id"]
    manifest = read_suite(replay.project, "test-cases/")
    quarantined = [case for case in manifest["cases"] if case["status"] == "QUARANTINED"]
    [case] = quarantined
    plan = read_attempt_receipt(replay.run_root(done), result["attempt_id"], "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    [symbol] = plan["files"][0]["quarantine_symbols"]
    assert case["quarantine"]["reason"] == "ASSERTION_FAILED" and case["quarantine"]["since_run"] == run_id
    assert case["quarantine"]["ref"] == f"run {run_id[:8]} {symbol}"  # the text of the @Disabled mark
    assert case["case_id"] in case["quarantine"]["question"] and "Student removed" in case["quarantine"]["question"]
    assert case["last_green_run"] is None and {row["status"] for row in manifest["cases"] if row is not case} == {"ACTIVE"}
    assert verify(replay.project, manifest)["methods"] == []  # the manifest describes the quarantined bytes on disk
    drafts = Path(result["paths"]["quarantine_markdown"]).read_text(encoding="utf-8")
    assert "Черновик баг-репорта" in drafts and case["case_id"] in drafts and case["quarantine"]["question"] in drafts
    assert result["suite"]["quarantined"] == 1


@needs_java
def test_a_person_edit_before_the_suite_is_written_stays_a_person_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2.1 item 6: the manifest describes the bytes the package left (disposition receipts), not whatever is on disk."""
    from tests.test_suite_migrate import _legacy
    from tools.suite_manifest import read_suite, verify
    from tools.suite_migrate import migrate

    replay, _done, _document, _edited = _legacy(tmp_path, monkeypatch, edit_case=False)
    target = replay.project / GENERATED
    text = target.read_text(encoding="utf-8")
    first = next(line for line in text.splitlines() if ".as(\"ASSERT-" in line)
    target.write_text(text.replace(first, "        // уточнено вручную\n" + first, 1), encoding="utf-8")
    migrate(replay.project)
    manifest = read_suite(replay.project, "test-cases/")
    assert len(verify(replay.project, manifest)["methods"]) == 1  # the person's method, never the package's own


@needs_java
def test_the_driver_summary_with_a_quarantined_suite_matches_its_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.schema_validation import schema_diagnostics

    replay = _local(tmp_path, monkeypatch)
    _change_product(replay.project)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    root = Path(__file__).resolve().parents[1]
    assert done["result"]["suite"]["quarantined"] == 1
    assert schema_diagnostics(done["result"], root / "schemas" / "driver-summary.schema.json", root) == []



def test_pytest_quarantine_end_to_end_on_a_synthetic_project(tmp_path: Path) -> None:
    """Review 2.1 item 17: the default quarantine on a pytest project — the module still imports, the failed test is
    an expected failure in the ordinary run and fails again when run with --runxfail."""
    import json as _json
    import subprocess as _subprocess
    import sys as _sys

    from tests.helpers import MODULE_PYTHON
    from tests.test_review_fixes_driver import SavedModel, _drive, _project, _start

    project = _project(tmp_path, local=True)
    (project / "src" / "sample.py").write_text('def combine(left: str, right: str) -> str:\n    return f"{left}-{right}"\n', encoding="utf-8")
    model = SavedModel("local-pilot-v1")
    original = model.answer

    def answer(task):
        value = original(task)
        if task["stage"].startswith("tc-to-autotest:"):
            row = value["generated_files"][0]
            row["content"] = ("# generated\nfrom __future__ import annotations\n\nfrom pytest import mark\n\nfrom sample import combine\n\n\n@mark.native\n"
                              + row["content"].split("@pytest.mark.native\n", 1)[1])
        return value

    model.answer = answer
    done = _drive(project, _start(project, "local-pilot-v1"), model)
    result = done["result"]
    assert (result["verification"], result["accepted"]) == ("FAIL", False), result
    final = read_attempt_receipt(project / ".pilot-runs" / done["run_id"], result["attempt_id"], "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert [row["disposition"] for row in final["files"]] == ["QUARANTINED"]
    text = (project / "tests" / "test_generated.py").read_text(encoding="utf-8")
    assert "@pytest.mark.xfail(strict=True" in text and "import pytest" in text
    python = MODULE_PYTHON if Path(MODULE_PYTHON).is_file() else _sys.executable
    ordinary = _subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_generated.py"], cwd=project, capture_output=True, text=True)
    assert ordinary.returncode == 0 and "xfailed" in ordinary.stdout, ordinary.stdout + ordinary.stderr
    explicit = _subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--runxfail", "tests/test_generated.py"], cwd=project, capture_output=True, text=True)
    assert explicit.returncode == 1 and "failed" in explicit.stdout, explicit.stdout
