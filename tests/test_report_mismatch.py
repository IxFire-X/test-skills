"""FaT-DN: an UNKNOWN exec whose JUnit report renamed the class says why and how to fix it — never the report text."""

from __future__ import annotations

from pathlib import Path

import pytest

ATTEMPT = "a" * 32
# The shape of the Petclinic campaign report (Surefire 3.0.2): the suite name and the file keep the FQN, classname is the
# class display name, testcase names stay method names despite a method @DisplayName.
CLASS = "org.springframework.samples.petclinic.owner.OwnerLifecycleFlowTests"
REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<testsuite name="{CLASS}" time="9.445" tests="2" errors="0" skipped="0" failures="1" flakes="0">\n'
    '  <testcase name="tc_TC_B1_001" classname="{classname}" time="0.768"/>\n'
    '  <testcase name="tc_TC_B1_002" classname="{classname}" time="0.196"><failure message="secret-ish text"/></testcase>\n'
    '</testsuite>\n'
)


def _run(tmp_path: Path, monkeypatch, classname: str, *, verdict: str = "UNKNOWN", kind: str = "JUNIT_INVALID") -> dict:
    from tools import pilot_state
    from tools.run_pipeline import _report_mismatch

    report = tmp_path / "artifacts" / ATTEMPT / "reports" / f"TEST-{CLASS}.xml"
    report.parent.mkdir(parents=True)
    report.write_text(REPORT.replace("{classname}", classname), encoding="utf-8")
    payload = {
        "verdict": verdict, "process_evidence": [{"kind": kind}],
        "execution": {
            "selectors": [f"{CLASS}#tc_TC_B1_001", f"{CLASS}#tc_TC_B1_002"],
            "artifact_evidence": [{"kind": "native_report", "path": f"artifacts/{ATTEMPT}/reports/TEST-{CLASS}.xml"}],
        },
    }

    def receipt(run_root, attempt_id, kind, event):
        assert (attempt_id, kind, event) == (ATTEMPT, "execution-receipt", "ARTIFACT_READ_BACK")
        return {"record": {"payload": payload}}

    monkeypatch.setattr(pilot_state, "read_attempt_receipt", receipt)
    return _report_mismatch(tmp_path, ATTEMPT)


def test_class_display_name_in_report_gives_reason_and_fix_without_report_text(tmp_path: Path, monkeypatch) -> None:
    notes = _run(tmp_path, monkeypatch, "Owner, pet and visit lifecycle")
    assert notes == {
        "report_mismatch": f"отчёт JUnit не сопоставлен с тестами: методы класса {CLASS} записаны в отчёт под другим classname — "
                           "так делает @DisplayName на классе теста",
        "report_fix": "убери @DisplayName с класса теста (у методов можно) и начни новый прогон тем же scan",
    }
    assert CLASS in notes["report_mismatch"] and "@DisplayName" in notes["report_mismatch"]
    assert "у методов можно" in notes["report_fix"]
    assert "Owner, pet" not in str(notes) and "secret-ish" not in str(notes)


@pytest.mark.parametrize(("classname", "verdict", "kind"), [
    (CLASS, "UNKNOWN", "JUNIT_INVALID"),  # the classname matches: some other cause
    ("Renamed", "FAIL", "JUNIT_INVALID"),
    ("Renamed", "UNKNOWN", "TIMEOUT"),
])
def test_no_hint_outside_a_renamed_class(tmp_path: Path, monkeypatch, classname: str, verdict: str, kind: str) -> None:
    assert _run(tmp_path, monkeypatch, classname, verdict=verdict, kind=kind) == {}


def test_unreadable_receipt_never_fails_the_step(tmp_path: Path) -> None:
    from tools.run_pipeline import _report_mismatch

    assert _report_mismatch(tmp_path, ATTEMPT) == {}
