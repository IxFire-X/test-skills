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
