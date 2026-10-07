"""The MUTATION stage on a real Maven project (step5 copy): PIT, attribution, FAIL, project inventory.

Needs a JDK 17+ in ``TEST_SKILLS_JAVA_HOME`` and Maven Central (or a warm ``~/.m2``).
The driver replays the recorded answers of ``9340016c``; review parts get a clean
compact answer.  Risks: W2-Р1 (attribution: weak and strong test of one case),
W2-Р2 (project unchanged), W2-Р3 (one FAIL: mutations on the passing methods only),
W2-Р15 (PIT finds and runs the generated JUnit 5 tests).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.live_step5 import Replay
from tests.review_scaling_helpers import clean_compact_answer, part_text

JAVA_HOME = os.environ.get("TEST_SKILLS_JAVA_HOME")
pytestmark = pytest.mark.skipif(not JAVA_HOME or not (Path(JAVA_HOME) / "bin").is_dir(),
                                reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven and PIT")
GENERATED = "src/test/java/net/javaguides/springboot/controller/StudentControllerPipelineTest.java"
CLASS = "net.javaguides.springboot.controller.StudentControllerPipelineTest"
# TC-B1-001 checks status, content type and the body; the weak variant keeps only the status.
STRONG_001 = '''        assertThat(response.getContentType()).as("ASSERT-B1-001-01-1-2").isEqualTo(APPLICATION_JSON);
        assertThat(jsonObject(response)).as("ASSERT-B1-001-01-1-3").isEqualTo(student(1, "Ramseh", "Mishra"));
        assertThat(text(response)).as("ASSERT-B1-001-01-1-4")
                .isEqualTo("{\\"id\\":1,\\"firstName\\":\\"Ramseh\\",\\"lastName\\":\\"Mishra\\"}");'''


def _replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit=None) -> Replay:
    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))

    def answer(task):
        if task.get("review_mode") == "compact-v1":
            return clean_compact_answer(part_text(task))
        if edit is not None and task["stage"].startswith("tc-to-autotest:"):
            value = next(value for label, value in replay.recorded.items() if label.startswith("tc-to-autotest."))
            value = {**value, "generated_files": [{**row, "content": edit(row["content"])} for row in value["generated_files"]]}
            return value
        return None

    replay = Replay("9340016c", tmp_path, override=answer)
    replay.review_mode = None  # the driver default (compact-v1)
    (replay.project / GENERATED).unlink()  # the fixture keeps the class retained by the live run
    skillsrc = replay.project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8") + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")
    return replay


def _receipt(replay: Replay, done: dict) -> dict:
    from tools.pilot_state import read_mutation_receipt_if_present

    return read_mutation_receipt_if_present(replay.run_root(done), done["result"]["attempt_id"])


def test_pass_run_measures_strength_without_changing_acceptance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import read_terminal_result

    replay = _replay(tmp_path, monkeypatch)
    code, done = replay.drive(replay.start_with("--mutation")[1])
    result = done["result"]
    assert (result["verification"], result["accepted"], code) == ("PASS", True, 0), result
    strength = result["test_strength"]
    assert strength["status"] == "MEASURED" and strength["killed"] + strength["survived"] > 0, strength
    receipt = _receipt(replay, done)
    assert receipt["project_inventory"]["unchanged"] is True and receipt["excluded_methods"] == []
    assert receipt["target_tests"] == [CLASS] and receipt["tool"]["launcher_version"].startswith("1.")
    assert all(row["pinned"] for row in receipt["tool"]["jars"] if row["file"].startswith("pitest"))
    assert (replay.run_root(done) / receipt["report"]["path"]).is_file()
    terminal = read_terminal_result(replay.run_root(done), result["attempt_id"])
    assert (terminal["test_strength"], terminal["accepted"]) == ("MEASURED", True)
    assert Path(result["paths"]["test_strength_markdown"]).read_text(encoding="utf-8").startswith("# Сила тестов")
    assert (replay.project / GENERATED).is_file()  # PASS keeps the generated file (RETAINED)


def test_a_failing_method_is_excluded_and_the_file_is_still_cleaned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _replay(tmp_path, monkeypatch, edit=lambda text: text.replace('.isEqualTo("Hello World!")', '.isEqualTo("Hello, World")', 1))
    code, done = replay.drive(replay.start_with("--mutation")[1])
    result = done["result"]
    assert (result["verification"], result["accepted"]) == ("FAIL", False), result
    receipt = _receipt(replay, done)
    assert receipt["status"] == "MEASURED" and receipt["project_inventory"]["unchanged"] is True
    [failed] = receipt["excluded_methods"]
    assert failed.startswith(CLASS + "#")
    covering = {method for group in receipt["survivor_groups"] for method in group["covering_methods"]}
    assert failed not in covering
    assert not (replay.project / GENERATED).exists()  # FAIL still cleans the file after the stage


def test_a_weak_test_leaves_its_mutant_alive_at_its_case_and_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    strong = _replay(tmp_path / "strong", monkeypatch)
    code, done = strong.drive(strong.start_with("--mutation")[1])
    strong_receipt = _receipt(strong, done)
    weak = _replay(tmp_path / "weak", monkeypatch, edit=lambda text: text.replace(STRONG_001, "", 1))
    code, done = weak.drive(weak.start_with("--mutation")[1])
    weak_receipt = _receipt(weak, done)

    def alive(receipt: dict) -> list[dict]:
        return [group for group in receipt["survivor_groups"] if group["method"] == "getStudent"]

    assert alive(strong_receipt) == []  # the strong TC-B1-001 kills every mutant of getStudent
    [group] = alive(weak_receipt)
    assert group["case_ids"] == ["TC-B1-001"]
    assert group["requirement_ids"] == ["CREQ-B1-001", "CREQ-B1-011", "CREQ-B1-013"]
    case = next(row for row in weak_receipt["cases"] if row["case_id"] == "TC-B1-001")
    assert case["killed"] < case["covered"]


def test_a_tampered_tool_is_not_runnable_and_verification_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from tools import mutation

    pins = json.loads(mutation.PINS_PATH.read_text(encoding="utf-8"))
    pins["java"]["jars"][0]["sha256"] = "0" * 64  # as if the jar in ~/.m2 had been replaced
    tampered = tmp_path / "pins.json"
    tampered.write_text(json.dumps(pins), encoding="utf-8")
    monkeypatch.setattr(mutation, "PINS_PATH", tampered)
    replay = _replay(tmp_path / "run", monkeypatch)
    code, done = replay.drive(replay.start_with("--mutation")[1])
    result = done["result"]
    assert (result["verification"], result["accepted"]) == ("PASS", True)
    assert (result["test_strength"]["status"], result["test_strength"]["reason_code"]) == ("NOT_RUNNABLE", "MUTATION_TOOL_DIGEST_MISMATCH")
    receipt = _receipt(replay, done)
    assert receipt["report"] is None and receipt["project_inventory"]["unchanged"] is True
    assert not (Path(str(replay.run_root(done)) + ".driver") / "mutation" / result["attempt_id"][:8] / "report").exists()
