"""Test design (D1a step 4): the generator covers the design — design_refs, design_uncovered, code-assigned priorities
and design coverage in the result and the analyst report."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tests.test_review_fixes_driver import _drive, _load, _project, _run_root, _start
from tests.test_test_design_stage import DESIGN, DesignModel, _design_setting
from tools import pipeline_driver as driver
from tools import test_design as design_tools
from tools.pilot_state import publish_model_stage_artifact, read_effective_canonical_if_present


class Generation(DesignModel):
    """The saved answers; ``design`` and ``generator`` change the design or the generator answer of a test."""

    def __init__(self, profile: str, *, design: Callable[[dict[str, Any]], None] | None = None,
                 generator: Callable[[dict[str, Any], dict[str, Any]], None] | None = None) -> None:
        super().__init__(profile)
        self.change_design, self.change_generator = design, generator
        self.generator_tasks: list[dict[str, Any]] = []

    def answer(self, task: dict[str, Any]) -> Any:
        value = super().answer(task)
        if task["stage"] == DESIGN and self.change_design is not None:
            self.change_design(value)
        if task["stage"].startswith("tc-generator:") and not task.get("rework"):
            self.generator_tasks.append(dict(task))
            if self.change_generator is not None:
                self.change_generator(value, _slice_of(task))
        return value


def _slice_of(task: dict[str, Any]) -> dict[str, Any]:
    return _load(next(path for path in task["inputs"] if Path(path).name.startswith("test-design-BATCH-")))


def _start_design(tmp_path: Path, profile: str = "cases-only-v1", **options: Any) -> tuple[Path, dict[str, Any]]:
    project = _project(tmp_path, local=profile == "local-pilot-v1")
    _design_setting(project, True)
    return project, _start(project, profile, **options)


def _cases(project: Path, done: dict[str, Any]) -> list[dict[str, Any]]:
    return read_effective_canonical_if_present(_run_root(project, done), done["result"]["attempt_id"])["document"]["test_cases"]


def _rejected(project: Path, model: Generation, start: dict[str, Any]) -> dict[str, Any]:
    task = _drive(project, start, model, until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
    return driver.submit(project, _run_root(project, task), task["task_id"])


# --- the generator task -------------------------------------------------------------------------------------------


def test_the_generator_brief_states_the_design_rules_and_the_task_schema_leaves_priority_to_code(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)
    task = _drive(project, start, Generation("cases-only-v1"), until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    brief = _load(task["inputs"][0])
    rules = " ".join(brief["design_rules"])
    assert brief["design_file"] == Path(next(path for path in task["inputs"] if "test-design-BATCH-" in path)).name
    assert "design_refs" in rules and "design_uncovered" in rules and "negative" in rules
    assert "BV" in rules and "just_outside" in rules  # a boundary case names the boundary and the partition its value lies in
    case_schema = json.loads(Path(task["schema_path"]).read_text(encoding="utf-8"))["properties"]["test_cases"]["items"]
    assert "priority" not in case_schema["properties"] and "priority" not in case_schema["required"] and "design_refs" in case_schema["properties"]
    assert "design_uncovered" in json.loads(Path(task["schema_path"]).read_text(encoding="utf-8"))["properties"]


# --- references -------------------------------------------------------------------------------------------------


def test_cases_carry_design_refs_and_code_priorities(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)
    model = Generation("cases-only-v1")
    done = _drive(project, start, model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    cases = _cases(project, done)
    assert [case["design_refs"] for case in cases] == [["DR-001"], ["DR-002"]] or sorted(case["design_refs"][0] for case in cases) == ["DR-001", "DR-002"]
    # The model wrote CRITICAL; code assigns the priority from the design (each case the first on its decision rule).
    assert [case["priority"] for case in cases] == ["MEDIUM", "MEDIUM"]


def test_a_case_without_design_refs_is_rejected(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)

    def without(value: dict[str, Any], _slice: dict[str, Any]) -> None:
        del value["test_cases"][0]["design_refs"]

    rejected = _rejected(project, Generation("cases-only-v1", generator=without), start)
    # The case names nothing, so its rule is left uncovered as well.
    assert rejected["status"] == "rejected" and {row["code"] for row in rejected["errors"]} == {"DESIGN_REFS_MISSING", "DESIGN_ELEMENT_UNCOVERED"}


def test_a_requirement_not_designed_needs_no_refs_for_its_case(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)

    def second_not_designed(value: dict[str, Any]) -> None:
        rule = value["artifacts"]["rules"].pop()
        value["artifacts"]["not_designed"] = [{"requirement_id": rule["requirement_ids"][0], "reason": "карточка — только отображение"}]

    model = Generation("cases-only-v1", design=second_not_designed)
    done = _drive(project, start, model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    assert sorted(len(case.get("design_refs") or []) for case in _cases(project, done)) == [0, 1]


def test_an_element_left_uncovered_is_rejected_unless_declared_with_a_reason(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)

    def both_on_the_first_rule(value: dict[str, Any], _slice: dict[str, Any]) -> None:
        for case in value["test_cases"]:
            case["design_refs"] = ["DR-001"]

    rejected = _rejected(project, Generation("cases-only-v1", generator=both_on_the_first_rule), start)
    assert {row["code"] for row in rejected["errors"]} == {"DESIGN_ELEMENT_UNCOVERED"}

    def declared(value: dict[str, Any], slice_value: dict[str, Any]) -> None:
        both_on_the_first_rule(value, slice_value)
        value["design_uncovered"] = [{"ref": "DR-002", "reason": "нужен второй товар в каталоге"}]

    project2, start2 = _start_design(tmp_path / "second")
    done = _drive(project2, start2, Generation("cases-only-v1", generator=declared))
    coverage = done["result"]["design_coverage"]
    assert coverage["rules"] == {"total": 2, "covered": 1, "uncovered": 1, "ratio": 0.5}
    assert coverage["uncovered"] == [{"ref": "DR-002", "reason": "нужен второй товар в каталоге"}]


def _with_invalid_partition(value: dict[str, Any]) -> None:
    source = value["artifacts"]["rules"][1]["requirement_ids"]
    value["artifacts"]["parameters"] = [{"id": "P-001", "name": "товар", "requirement_ids": source, "kind": "enum", "notes": ""}]
    value["artifacts"]["partitions"] = [
        {"id": "EQ-001", "parameter_id": "P-001", "valid": True, "condition": "товар есть в каталоге", "requirement_ids": source},
        {"id": "EQ-002", "parameter_id": "P-001", "valid": False, "condition": "товара нет в каталоге", "requirement_ids": source},
    ]


def test_an_invalid_partition_needs_a_negative_case(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path)
    rejected = _rejected(project, Generation("cases-only-v1", design=_with_invalid_partition), start)
    assert {row["code"] for row in rejected["errors"]} == {"DESIGN_NEGATIVE_MISSING"}

    def negative(value: dict[str, Any], slice_value: dict[str, Any]) -> None:
        for case in value["test_cases"]:
            if "EQ-002" in case["design_refs"]:
                case["categories"] = ["negative"]

    project2, start2 = _start_design(tmp_path / "second")
    done = _drive(project2, start2, Generation("cases-only-v1", design=_with_invalid_partition, generator=negative))
    cases = _cases(project2, done)
    assert [case["priority"] for case in cases if "EQ-002" in case["design_refs"]] == ["HIGH"]
    assert done["result"]["design_coverage"]["negative_partitions"]["ratio"] == 1.0


# --- the result -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_design_coverage_and_the_priority_spread_are_in_the_result(tmp_path: Path, profile: str) -> None:
    project, start = _start_design(tmp_path, profile)
    done = _drive(project, start, Generation(profile))
    result = done["result"]
    assert result["schema_version"] == "1.3.0"
    coverage = result["design_coverage"]
    assert coverage["rules"] == {"total": 2, "covered": 2, "uncovered": 0, "ratio": 1.0}
    assert coverage["partitions"]["ratio"] is None and coverage["priorities"] == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 2, "LOW": 0}
    # Two MEDIUM cases are one level over 70 %, but fewer than ten cases never warn about the spread.
    assert not any(item.get("code") == "PRIORITY_DEGENERATE" for item in result["warnings"])


def test_the_analyst_report_carries_the_design_coverage(tmp_path: Path) -> None:
    project, start = _start_design(tmp_path, analyst_report=True)
    done = _drive(project, start, Generation("cases-only-v1"))
    report = json.loads(Path(done["result"]["paths"]["analyst_report_json"]).read_text(encoding="utf-8"))
    assert report["design_coverage"] == done["result"]["design_coverage"]
    assert "Покрытие дизайна" in Path(done["result"]["paths"]["analyst_report_markdown"]).read_text(encoding="utf-8")


# --- the journal ----------------------------------------------------------------------------------------------------


def test_the_journal_refuses_a_fragment_with_a_model_priority_or_a_foreign_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, start = _start_design(tmp_path)
    model = Generation("cases-only-v1")
    task = _drive(project, start, model, until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    published: list[dict[str, Any]] = []
    original = publish_model_stage_artifact

    def capture(run_root: Path, attempt_id: str, stage: str, artifact: dict[str, Any], **options: Any) -> Any:
        if stage.startswith("tc-generator:"):
            published.append(copy.deepcopy(artifact))
            raise ValueError("captured")
        return original(run_root, attempt_id, stage, artifact, **options)

    monkeypatch.setattr("tools.pilot_state.publish_model_stage_artifact", capture)
    Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="captured"):
        driver.submit(project, _run_root(project, task), task["task_id"])
    monkeypatch.undo()
    [fragment] = published
    run_root = _run_root(project, task)

    def resealed(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        value = copy.deepcopy(fragment)
        change(value)
        body = {key: item for key, item in value.items() if key != "digest"}
        value["digest"] = driver._sha(body)
        return value

    with pytest.raises(ValueError, match="design"):
        publish_model_stage_artifact(run_root, task["attempt_id"], task["stage"], resealed(lambda value: value["test_cases"][0].__setitem__("priority", "CRITICAL")))
    with pytest.raises(ValueError, match="design"):
        publish_model_stage_artifact(run_root, task["attempt_id"], task["stage"], resealed(lambda value: value["test_cases"][0].__setitem__("design_refs", ["DR-077"])))
    publish_model_stage_artifact(run_root, task["attempt_id"], task["stage"], fragment)


# --- pure: which cases need references ------------------------------------------------------------------------------


def test_refs_are_optional_only_when_every_source_of_the_case_is_not_designed() -> None:
    sliced = {"artifacts": {"parameters": [], "partitions": [], "boundaries": [],
                            "rules": [{"id": "DR-001", "kind": "decision", "conditions": [{"text": "есть"}], "expected": "показан", "requirement_ids": ["SREQ-0001"]}],
                            "not_designed": [{"requirement_id": "SREQ-0002", "reason": "нет входов"}]}}
    mappings = [{"source_requirement_id": "SREQ-0001", "canonical_requirement_ids": ["CREQ-B1-001", "CREQ-B1-003"]},
                {"source_requirement_id": "SREQ-0002", "canonical_requirement_ids": ["CREQ-B1-002", "CREQ-B1-003"]}]
    covered = {"case_id": "TC-B1-001", "categories": ["positive"], "requirement_ids": ["CREQ-B1-001"], "design_refs": ["DR-001"]}
    only_not_designed = {"case_id": "TC-B1-002", "categories": ["positive"], "requirement_ids": ["CREQ-B1-002"]}
    mixed = {"case_id": "TC-B1-003", "categories": ["positive"], "requirement_ids": ["CREQ-B1-003"]}
    assert design_tools.fragment_design_problems(sliced, [covered, only_not_designed], [], mappings) == []
    rows = design_tools.fragment_design_problems(sliced, [covered, mixed], [], mappings)
    assert [(row["code"], row["path"]) for row in rows] == [("DESIGN_REFS_MISSING", "/test_cases/1/design_refs")]
