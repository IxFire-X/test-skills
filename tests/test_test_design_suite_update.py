"""D1c c2: suite-update rebuilds the test design of changed requirements only.

The DESIGN step (between IMPACT and UPDATE, when ``.skillsrc`` design.enabled) asks the model for the design of the
changed and added requirements; code merges it into the suite's design — elements of unchanged requirements keep their
IDs, new ones are numbered after the highest, removed requirements drop out, combinations keep the IDs of unchanged
partition sets — and cases naming a removed or rebuilt element join the update.  The generator covers the slice of
the changed requirements; coverage is recounted over the whole suite.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.test_suite_manifest import _local, needs_java
from tests.test_suite_update import CONTROLLER, DOCS, NEW, OLD, _answer, _drive, _replace_in
from tests.test_test_design_suite import _design_on
from tools import test_design as design_tools
from tools.suite_manifest import read_suite


def _base(sreq_rule: str = "SREQ-0002") -> dict[str, Any]:
    design = {"schema_version": "1.0.0", "stage": "test-design", "warnings": [], "artifacts": {
        "parameters": [{"id": "P-001", "name": "роль", "requirement_ids": ["SREQ-0001"], "kind": "enum", "notes": ""},
                       {"id": "P-002", "name": "страница", "requirement_ids": ["SREQ-0001"], "kind": "enum", "notes": ""}],
        "partitions": [{"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "админ", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-002", "parameter_id": "P-001", "valid": True, "condition": "менеджер", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-003", "parameter_id": "P-001", "valid": False, "condition": "гость", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-004", "parameter_id": "P-002", "valid": True, "condition": "первая", "requirement_ids": ["SREQ-0001"]},
                       {"id": "EQ-005", "parameter_id": "P-002", "valid": False, "condition": "нет", "requirement_ids": ["SREQ-0001"]}],
        "boundaries": [],
        "rules": [{"id": "DR-001", "kind": "decision", "conditions": [{"text": "список"}], "expected": "показан", "requirement_ids": [sreq_rule]}],
        "not_designed": []}}
    return design_tools.with_combinations(design, design_tools.combinations(design, 16))


def _answer_for(requirement: str) -> dict[str, Any]:
    """A model design of one requirement, numbered from 1 like a fresh design."""
    return {"schema_version": "1.0.0", "stage": "test-design", "warnings": [], "artifacts": {
        "parameters": [{"id": "P-001", "name": "формат", "requirement_ids": [requirement], "kind": "enum", "notes": ""}],
        "partitions": [{"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "дата", "requirement_ids": [requirement]},
                       {"id": "EQ-002", "parameter_id": "P-001", "valid": False, "condition": "текст", "requirement_ids": [requirement]}],
        "boundaries": [],
        "rules": [{"id": "DR-001", "kind": "error", "conditions": [{"ref": "EQ-002"}], "expected": "ошибка формата", "requirement_ids": [requirement]}],
        "not_designed": []}}


# --- the merge ----------------------------------------------------------------------------------------------------


def test_a_redesigned_requirement_gets_new_ids_after_the_highest_and_the_rest_keeps_its_ids() -> None:
    merged = design_tools.merge_design(_base(), _answer_for("SREQ-0002"), ["SREQ-0002"], 16)
    artifacts = merged["artifacts"]
    assert [row["id"] for row in artifacts["parameters"]] == ["P-001", "P-002", "P-003"]
    assert [row["id"] for row in artifacts["partitions"]] == ["EQ-001", "EQ-002", "EQ-003", "EQ-004", "EQ-005", "EQ-006", "EQ-007"]
    # The old rule of SREQ-0002 is gone; its ID is never reused.
    assert [(row["id"], row["conditions"]) for row in artifacts["rules"]] == [("DR-002", [{"ref": "EQ-007"}])]
    assert artifacts["partitions"][-1]["parameter_id"] == "P-003"
    plain = {**merged, "artifacts": {name: rows for name, rows in artifacts.items() if name != "combinations"}}
    assert design_tools.validate_design(plain, ["SREQ-0001", "SREQ-0002"]) == []


def test_unchanged_combinations_keep_their_ids_and_new_ones_follow() -> None:
    base = _base()
    before = {tuple(row["partition_ids"]): row["id"] for row in base["artifacts"]["combinations"]}
    answer = _answer_for("SREQ-0001")
    answer["artifacts"]["parameters"][0]["requirement_ids"] = ["SREQ-0001"]
    merged = design_tools.merge_design(base, answer, ["SREQ-0003"], 16)
    rows = merged["artifacts"]["combinations"]
    kept = [row for row in rows if tuple(row["partition_ids"]) in before]
    assert not kept or all(row["id"] == before[tuple(row["partition_ids"])] for row in kept)
    new = [row["id"] for row in rows if tuple(row["partition_ids"]) not in before]
    assert new and min(new) > max(before.values())


def test_a_suite_without_a_design_takes_the_answer_as_it_is() -> None:
    merged = design_tools.merge_design(None, _answer_for("SREQ-0001"), ["SREQ-0001"], 16)
    assert [row["id"] for row in merged["artifacts"]["rules"]] == ["DR-001"]
    assert merged["artifacts"]["combinations"] == []


# --- the run ------------------------------------------------------------------------------------------------------


def _design_answer(task: dict[str, Any]) -> Any:
    """The suite answers of tests/test_suite_update.py plus a design (one error rule per requirement of the brief) and
    design references on every updated case."""
    stage = task["stage"]
    if stage == "test-design:update":
        brief = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))
        rules = [{"id": f"DR-{index:03d}", "kind": "decision", "conditions": [{"text": "запрос выполнен"}], "expected": "сообщение об успехе",
                  "requirement_ids": [row["source_requirement_id"]]} for index, row in enumerate(brief["requirements"], 1)]
        return {"artifacts": {"parameters": [], "partitions": [], "boundaries": [], "rules": rules, "not_designed": []}, "warnings": []}
    value = _answer(task)
    if stage == "tc-generator:update":
        design = next((path for path in task["inputs"] if Path(path).name == "test-design-update.json"), None)
        if design is not None:
            rules = [row["id"] for row in json.loads(Path(design).read_text(encoding="utf-8"))["artifacts"]["rules"]]
            for case in value["test_cases"]:
                case["design_refs"] = rules
                case.pop("priority", None)
    return value


@pytest.fixture(scope="module")
def design_suite(tmp_path_factory: pytest.TempPathFactory):
    """step5 after local-pilot-v1 --suite with the design stage on (its record declares every requirement not designed)."""
    if not os.environ.get("TEST_SKILLS_JAVA_HOME"):
        pytest.skip("set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
    monkeypatch = pytest.MonkeyPatch()
    replay = _local(tmp_path_factory.mktemp("design-suite"), monkeypatch)
    _design_on(replay.project)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    assert done["result"]["suite"]["status"] == "WRITTEN", done["result"]["suite"]
    return replay


def _copy(replay, tmp_path: Path):
    origin = getattr(replay, "origin", None) or replay.project
    replay.origin = origin
    project = tmp_path / "project"
    shutil.copytree(origin, project)
    replay.project = project
    return replay


@needs_java
def test_a_changed_requirement_is_redesigned_alone_and_its_cases_cover_it(design_suite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _copy(design_suite, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    before = read_suite(project, "test-cases/")
    stored_before = json.loads((project / before["suite_files"]["design"]["path"]).read_text(encoding="utf-8"))
    _replace_in(project / DOCS, OLD, NEW)
    _replace_in(project / CONTROLLER, OLD, NEW)
    seen: list[dict[str, Any]] = []
    done = _drive(replay, seen=seen, answer=_design_answer)
    assert done["result"]["outcome"] == "UPDATED", done["result"]
    [design_task] = [task for task in seen if task.get("stage") == "test-design:update"]
    brief = json.loads(Path(design_task["inputs"][0]).read_text(encoding="utf-8"))
    after = read_suite(project, "test-cases/")
    # Only the changed requirements are designed again (the new message is in AC-7 and in its section text).
    assert 1 <= len(brief["requirements"]) < len(after["requirements"])
    assert after["suite_files"]["design"]["sha256"] != before["suite_files"]["design"]["sha256"]
    stored = json.loads((project / after["suite_files"]["design"]["path"]).read_text(encoding="utf-8"))
    rules = stored["design"]["artifacts"]["rules"]
    redesigned = {rule["requirement_ids"][0] for rule in rules}
    assert len(rules) == len(brief["requirements"]) and any(key.endswith("#AC-7") for key in redesigned)
    assert [rule["id"] for rule in rules] == [f"DR-{index:03d}" for index in range(1, len(rules) + 1)]
    # Every other requirement keeps its entry; the redesigned ones left not_designed.
    kept = {row["requirement_id"] for row in stored["design"]["artifacts"]["not_designed"]}
    assert kept == {row["requirement_id"] for row in stored_before["design"]["artifacts"]["not_designed"]} - redesigned
    assert after["design_coverage"]["rules"] == {"total": len(rules), "covered": len(rules), "uncovered": 0, "ratio": 1.0}
    document = json.loads((project / "test-cases" / "test-cases.json").read_text(encoding="utf-8"))
    updated = [case for case in document["test_cases"] if case.get("design_refs")]
    assert updated and all(case["priority"] == "MEDIUM" or case["priority"] == "LOW" for case in updated)
    # The case review of the update sees the design of the updated cases.
    from tests.review_scaling_helpers import part_text

    reviews = [part_text(task) for task in seen if str(task.get("stage", "")).startswith("tc-reviewer:") and task.get("review_mode") == "compact-v1"]
    assert reviews and any("## Тест-дизайн" in text and "[DR-001]" in text for text in reviews)


@needs_java
def test_with_the_design_turned_off_the_stored_design_is_kept_and_recounted(design_suite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _copy(design_suite, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    path = project / ".skillsrc"
    path.write_text(path.read_text(encoding="utf-8").replace("design:\n  enabled: true\n", "design:\n  enabled: false\n"), encoding="utf-8")
    before = read_suite(project, "test-cases/")
    _replace_in(project / DOCS, OLD, NEW)
    _replace_in(project / CONTROLLER, OLD, NEW)
    seen: list[dict[str, Any]] = []
    done = _drive(replay, seen=seen)
    assert done["result"]["outcome"] == "UPDATED", done["result"]
    assert not any(task.get("stage") == "test-design:update" for task in seen)
    after = read_suite(project, "test-cases/")
    assert after["suite_files"]["design"] == before["suite_files"]["design"]
    assert "design_coverage" in after


@needs_java
def test_a_suite_without_a_design_is_designed_in_full_on_its_first_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path / "plain", monkeypatch)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    assert "design" not in read_suite(replay.project, "test-cases/")["suite_files"]
    project = replay.project
    _design_on(project)
    _replace_in(project / DOCS, OLD, NEW)
    _replace_in(project / CONTROLLER, OLD, NEW)
    seen: list[dict[str, Any]] = []
    done = _drive(replay, seen=seen, answer=_design_answer)
    assert done["result"]["outcome"] == "UPDATED", done["result"]
    [design_task] = [task for task in seen if task.get("stage") == "test-design:update"]
    manifest = read_suite(project, "test-cases/")
    brief = json.loads(Path(design_task["inputs"][0]).read_text(encoding="utf-8"))
    assert len(brief["requirements"]) == len(manifest["requirements"])  # every requirement of the suite
    stored = json.loads((project / manifest["suite_files"]["design"]["path"]).read_text(encoding="utf-8"))
    assert {rule["requirement_ids"][0] for rule in stored["design"]["artifacts"]["rules"]} == {row["key"] for row in manifest["requirements"]}
