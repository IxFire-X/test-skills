"""D7: context and envelopes.

Live step5 runs: the context-marker got mvnw, mvnw.cmd, .mvn/wrapper/*, run-tests.ps1 and
.gitattributes as sources; the case reviewer could not see the files capability
provenance names (CAPABILITY_PROVENANCE_NOT_VERIFIABLE_IN_ENVELOPE); static review
parts lacked the global bindings step 1 of the reviewer SKILL asked for
(REVIEW_PART_GLOBAL_BINDINGS_NOT_IN_ENVELOPE).
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.live_step5 import Replay, outputs
from tools import pipeline_driver as driver

ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = {"mvnw", "mvnw.cmd", ".mvn/wrapper/maven-wrapper.properties", "run-tests.ps1", ".gitattributes", ".gitignore"}


def test_live_findings_name_the_envelope_gaps() -> None:
    codes = {finding["code"] for run in ("2c10d733", "9340016c") for label, answer in outputs(run).items() if label.startswith("review.")
             for finding in answer["findings"]}
    assert {"CAPABILITY_PROVENANCE_NOT_VERIFIABLE_IN_ENVELOPE", "REVIEW_PART_GLOBAL_BINDINGS_NOT_IN_ENVELOPE"} <= codes


def _context_paths(task: dict) -> set[str]:
    base = Path(task["output_path"]).parents[1] / "inputs" / "context"
    return {Path(path).relative_to(base).as_posix() for path in task["inputs"] if Path(path).is_relative_to(base)}


def test_build_wrappers_and_launch_scripts_never_reach_the_model(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, marker = replay.start()
    paths = _context_paths(marker)
    assert not paths & WRAPPERS, paths & WRAPPERS
    assert {"pom.xml", "src/main/java/net/javaguides/springboot/controller/StudentController.java", "docs/requirements/student-controller.md"} <= paths
    code, generator = replay.drive(marker, until=lambda task: str(task.get("stage", "")).startswith("tc-generator:"))
    assert not _context_paths(generator) & WRAPPERS
    # The execution baseline still binds the wrapper the project runs with.
    from tools.pilot_state import derive_state

    root = replay.run_root(generator)
    attempt = derive_state(root)["attempts"][-1]
    baseline, inventory = driver._baseline_and_inventory(root, attempt)
    assert "mvnw" in {item["project_path"] for item in inventory["files"]}


def test_case_reviewer_sees_the_sources_capability_provenance_names(tmp_path: Path) -> None:
    replay = Replay("2c10d733", tmp_path)
    code, task = replay.drive(until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    envelope = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))
    contents = [item.get("content") or "" for scope in envelope["scopes"] for item in scope["inputs"]]
    assert any("class StudentController" in text for text in contents)
    assert any("class HelloWorldController" in text for text in contents)
    from tools.pilot_state import read_attempt_receipt

    root = replay.run_root(task)
    snapshot = read_attempt_receipt(root, task["attempt_id"], "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]
    paths = [item["path"] for item in snapshot["contexts"]]
    assert "src/main/java/net/javaguides/springboot/controller/StudentController.java" in paths
    assert not set(paths) & WRAPPERS
    budget = (200_000 - 20_000) // 4
    assert sum(len(item["content"].encode("utf-8")) for item in snapshot["contexts"]) <= budget


def test_static_review_skill_leaves_global_bindings_to_the_controller() -> None:
    skill = (ROOT / "skills" / "autotest-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    assert "Independently verify source digest" not in skill
    assert "Controller-verified bindings" in skill
    contract = (ROOT / "skills" / "autotest-reviewer" / "references" / "autotest-review-contract.md").read_text(encoding="utf-8")
    assert "REVIEW_PART_GLOBAL_BINDINGS_NOT_IN_ENVELOPE" in contract
