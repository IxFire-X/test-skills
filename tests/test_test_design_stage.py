"""Test design (D1a step 3): the ``test-design:baseline`` stage in the driver and the journal.

Without ``design_required`` in the run authorization (``.skillsrc`` ``design.enabled: false`` and every older run) the
run is exactly as before: no design stage, the generator binds four inputs.  With it the design is answered after the
marker and before any generator request, the generator binds a fifth input — the design slice of its batch — and the
journal recomputes the brief, the slice and the design checks on write and on read.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _load, _project, _run_root, _saved, _start, review_answer
from tools import pipeline_driver as driver
from tools import test_design as design_tools
from tools.pilot_state import derive_state, publish_model_request, publish_model_stage_artifact, read_model_request, read_run

DESIGN = "test-design:baseline"


def _design_setting(project: Path, enabled: bool | None) -> None:
    """``.skillsrc`` ``design.enabled``; None removes the section (the package default)."""
    path = project / ".skillsrc"
    skillsrc = json.loads(path.read_text(encoding="utf-8"))
    skillsrc.pop("design", None)
    if enabled is not None:
        skillsrc["design"] = {"enabled": enabled}
    path.write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")


def design_refs_of(case: dict[str, Any], answer: dict[str, Any], slice_value: dict[str, Any]) -> list[str]:
    """The design elements of the case's source requirements (through the answer's mappings), in design order."""
    sources = {row["source_requirement_id"] for row in answer.get("source_to_canonical_mappings", [])
               if set(row["canonical_requirement_ids"]) & set(case["requirement_ids"])}
    artifacts = slice_value["artifacts"]
    partitions = [row["id"] for row in artifacts["partitions"] if sources & set(row["requirement_ids"])]
    boundaries = [row["id"] for row in artifacts["boundaries"] if row["partition_id"] in partitions]
    rules = [row["id"] for row in artifacts["rules"] if sources & set(row["requirement_ids"])]
    return [*rules, *partitions, *boundaries]


class DesignModel(SavedModel):
    """The saved answers plus the saved design (two decision rules, one per source requirement); the generator answer
    names the design elements of each case's requirements and leaves a model priority the driver must replace."""

    def __init__(self, profile: str, **options: Any) -> None:
        super().__init__(profile, **options)
        self.design_tasks: list[dict[str, Any]] = []

    def answer(self, task: dict[str, Any]) -> Any:
        if task["stage"].startswith("tc-generator:") and not task.get("rework"):
            value = super().answer(task)
            design = next((path for path in task["inputs"] if Path(path).name.startswith("test-design-BATCH-")), None)
            for case in value["test_cases"] if design is not None else []:
                refs = design_refs_of(case, value, _load(design))
                if refs:
                    case["design_refs"] = refs
                case["priority"] = "CRITICAL"
            return value
        if task["stage"] != DESIGN:
            return super().answer(task)
        self.stages.append(task["stage"])
        self.design_tasks.append(dict(task))
        brief = _load(task["inputs"][0])
        return _saved("test-design.answer.json", {f"source:{index}": row["source_requirement_id"] for index, row in enumerate(brief["requirements"], 1)})


def _events(run_root: Path, attempt_id: str | None = None) -> list[dict[str, Any]]:
    return [event for event in derive_state(run_root)["events"] if attempt_id is None or event.get("attempt_id") == attempt_id]


def _request(run_root: Path, attempt_id: str, stage: str) -> dict[str, Any]:
    [event] = [event for event in _events(run_root, attempt_id) if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    return dict(read_model_request(run_root, attempt_id, stage, event["artifact_digest"]))


def _authorization(run_root: Path) -> dict[str, Any]:
    return dict(read_run(run_root)["authorization"])


def _generator_stage(stages: list[str]) -> str:
    return next(stage for stage in stages if stage.startswith("tc-generator:"))


# --- without design_required: exactly the previous run ------------------------------------------------------------


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_design_turned_off_leaves_the_run_as_before(tmp_path: Path, profile: str) -> None:
    project = _project(tmp_path, local=profile == "local-pilot-v1")
    _design_setting(project, False)
    model = SavedModel(profile)
    done = _drive(project, _start(project, profile), model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    assert model.stages[:2] == ["context-marker:baseline", model.stages[1]] and model.stages[1].startswith("tc-generator:")
    assert DESIGN not in model.stages
    run_root = _run_root(project, done)
    assert "design_required" not in _authorization(run_root)
    attempt_id = done["result"]["attempt_id"]
    assert not any(event.get("stage_instance_id") == DESIGN for event in _events(run_root))
    assert len(_request(run_root, attempt_id, _generator_stage(model.stages))["input_digests"]) == 4
    assert "design_coverage" not in done["result"]


def test_a_run_without_design_refuses_a_design_request(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, False)
    task = _drive(project, _start(project, "cases-only-v1"), SavedModel("cases-only-v1"), until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    run_root = _run_root(project, task)
    marker = [event for event in _events(run_root, task["attempt_id"]) if event.get("stage_instance_id") == "context-marker:baseline"
              and event["event_type"] == "MODEL_RESPONSE_RECEIVED"][0]
    with pytest.raises(ValueError, match="test design"):
        publish_model_request(run_root, task["attempt_id"], DESIGN, model_id="model-test", invocation_id="design-forged",
                              input_digests=[marker["artifact_digest"], "sha256:" + "1" * 64])


def test_a_run_without_design_refuses_a_fifth_generator_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, False)
    original = driver._generator_input_digests

    def fifth(*arguments: Any) -> list[str]:
        return [*original(*arguments), "sha256:" + "5" * 64]

    monkeypatch.setattr(driver, "_generator_input_digests", fifth)
    with pytest.raises(ValueError, match="tc-generator input digests"):
        _drive(project, _start(project, "cases-only-v1"), SavedModel("cases-only-v1"))


# --- the package default and the stage ----------------------------------------------------------------------------


def test_the_package_default_turns_design_on(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, None)
    task = _drive(project, _start(project, "cases-only-v1"), DesignModel("cases-only-v1"), until=lambda task: task.get("stage") == DESIGN)
    assert _authorization(_run_root(project, task))["design_required"] is True


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
def test_the_design_stage_runs_between_the_marker_and_the_generator(tmp_path: Path, profile: str) -> None:
    project = _project(tmp_path, local=profile == "local-pilot-v1")
    _design_setting(project, True)
    model = DesignModel(profile)
    done = _drive(project, _start(project, profile), model)
    assert done["action"] == "done" and done["result"]["completion"] == "COMPLETE", done
    assert model.stages[:3] == ["context-marker:baseline", DESIGN, model.stages[2]] and model.stages[2].startswith("tc-generator:")
    run_root = _run_root(project, done)
    attempt_id = done["result"]["attempt_id"]
    assert _authorization(run_root)["design_required"] is True

    # The stage reads the requirements only: the brief, no marker sources and no project files.
    [task] = model.design_tasks
    assert len(task["inputs"]) == 1 and Path(task["skill_path"]).parent.name == "test-design"
    brief = _load(task["inputs"][0])
    assert set(brief) == {"schema_version", "requirements", "id_formats"}
    events = _events(run_root, attempt_id)
    marker = next(event for event in events if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    request = _request(run_root, attempt_id, DESIGN)
    assert request["input_digests"] == [marker["artifact_digest"], design_tools.digest(brief)]
    assert (request["role"], request["role_policy"]) == ("generator", "test-design-v1")
    response = next(event for event in events if event.get("stage_instance_id") == DESIGN and event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    assert not any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == DESIGN for event in events)

    # The generator binds the design slice of its batch as its fifth input and reads it as a file.
    generator = _request(run_root, attempt_id, _generator_stage(model.stages))
    assert len(generator["input_digests"]) == 5
    design = driver._design_artifact(run_root, attempt_id)
    assert design["content_digest"] == response["artifact_digest"]
    owned = [row["source_requirement_id"] for row in brief["requirements"]]
    assert generator["input_digests"][4] == design_tools.digest(design_tools.design_slice(design["artifact"], owned))


def test_an_invalid_design_answer_is_rejected_and_the_corrected_one_accepted(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    model = DesignModel("cases-only-v1")
    task = _drive(project, _start(project, "cases-only-v1"), model, until=lambda task: task.get("stage") == DESIGN)
    run_root = _run_root(project, task)
    good = model.answer(task)

    broken = copy.deepcopy(good)
    broken["artifacts"]["rules"][1]["requirement_ids"] = ["SREQ-9999"]
    Path(task["output_path"]).write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    rejected = driver.submit(project, run_root, task["task_id"])
    assert rejected["status"] == "rejected"
    assert {row["code"] for row in rejected["errors"]} == {"DESIGN_REQUIREMENT_UNKNOWN", "DESIGN_REQUIREMENT_UNCOVERED"}

    owned = {**good, "stage": "test-design"}
    Path(task["output_path"]).write_text(json.dumps(owned, ensure_ascii=False), encoding="utf-8")
    assert driver.submit(project, run_root, task["task_id"])["status"] == "rejected"

    Path(task["output_path"]).write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    following = driver.submit(project, run_root, task["task_id"])
    assert following["action"] == "llm" and following["stage"].startswith("tc-generator:")


# --- the journal --------------------------------------------------------------------------------------------------


def _at_design(tmp_path: Path) -> tuple[Path, dict[str, Any], DesignModel]:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    model = DesignModel("cases-only-v1")
    return project, _drive(project, _start(project, "cases-only-v1"), model, until=lambda task: task.get("stage") == DESIGN), model


def test_the_journal_refuses_a_design_request_with_another_brief(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    monkeypatch.setattr(driver, "_design_input_digests", lambda marker, brief: [marker["content_digest"], "sha256:" + "2" * 64])
    with pytest.raises(ValueError, match="test design input digests"):
        _drive(project, _start(project, "cases-only-v1"), DesignModel("cases-only-v1"))


def test_the_journal_refuses_a_design_that_fails_the_design_checks(tmp_path: Path) -> None:
    project, task, model = _at_design(tmp_path)
    run_root = _run_root(project, task)
    good = model.answer(task)
    forged = {"schema_version": "1.0.0", "stage": "test-design", **copy.deepcopy(good)}
    forged["artifacts"]["rules"].pop()
    with pytest.raises(ValueError, match="test design"):
        publish_model_stage_artifact(run_root, task["attempt_id"], DESIGN, forged)
    assert not any(event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == DESIGN for event in _events(run_root))
    Path(task["output_path"]).write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    assert driver.submit(project, run_root, task["task_id"])["stage"].startswith("tc-generator:")


def test_the_journal_refuses_a_generator_request_before_the_design_answer(tmp_path: Path) -> None:
    project, task, _model = _at_design(tmp_path)
    run_root = _run_root(project, task)
    marker = next(event for event in _events(run_root, task["attempt_id"])
                  if event.get("stage_instance_id") == "context-marker:baseline" and event["event_type"] == "MODEL_RESPONSE_RECEIVED")
    with pytest.raises(ValueError):
        publish_model_request(run_root, task["attempt_id"], "tc-generator:BATCH-main", model_id="model-test", invocation_id="generator-forged",
                              input_digests=[marker["artifact_digest"], *("sha256:" + digit * 64 for digit in "3456")])


@pytest.mark.parametrize("forge", [
    lambda digests: digests[:4],                                  # the design slice left out
    lambda digests: [*digests[:4], "sha256:" + "6" * 64],         # another slice
])
def test_the_journal_binds_the_design_slice_of_the_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: Any) -> None:
    project, task, model = _at_design(tmp_path)
    run_root = _run_root(project, task)
    original = driver._generator_input_digests
    monkeypatch.setattr(driver, "_generator_input_digests", lambda *arguments: forge(original(*arguments)))
    Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="tc-generator input digests"):
        driver.submit(project, run_root, task["task_id"])


# --- rework -------------------------------------------------------------------------------------------------------


class RejectOnce(DesignModel):
    """Rejects the first canonical review with a BLOCKING finding on the first case; the rework changes that case."""

    def __init__(self, profile: str) -> None:
        super().__init__(profile)
        self.rejected = False

    def answer(self, task: dict[str, Any]) -> Any:
        stage = task["stage"]
        if stage.startswith("tc-generator:") and task.get("rework"):
            self.stages.append(stage)
            brief = _load(task["inputs"][0])
            case = copy.deepcopy(next(item for item in brief["r1_cases"] if item["case_id"] in brief["affected_case_ids"]))
            case["objective"] = "Подтвердить выполнение требования после исправления."
            return {"test_cases": [case]}
        if stage.startswith("tc-reviewer:") and not self.rejected:
            self.stages.append(stage)
            self.rejected = True
            envelope = _load(task["inputs"][0])
            scope = next(scope for scope in envelope["scopes"] if scope["scope_id"].startswith("local-"))
            finding = {"severity": "BLOCKING", "code": "EXPECTATION_CLAIM_WITHOUT_ASSERTION", "message": "Ожидание ничем не проверяется.",
                       "evidence": [scope["inputs"][0]["artifact_digest"]], "related_ids": [scope["scope_id"].removeprefix("local-")]}
            return review_answer(task, findings=[finding])
        return super().answer(task)


def test_a_rework_attempt_carries_the_design_without_a_model_call(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _design_setting(project, True)
    model = RejectOnce("cases-only-v1")
    done = _drive(project, _start(project, "cases-only-v1"), model)
    run_root = _run_root(project, done)
    attempts = derive_state(run_root)["attempts"]
    assert len(attempts) == 2 and attempts[1]["retry_reason"] == "CANONICAL_REWORK", attempts
    assert model.stages.count(DESIGN) == 1
    parent, child = (attempt["attempt_id"] for attempt in attempts)
    assert driver._design_artifact(run_root, child)["artifact"] == driver._design_artifact(run_root, parent)["artifact"]
    assert _request(run_root, child, DESIGN)["invocation_id"].startswith("design-carry-")
    rework_stage = [stage for stage in model.stages if stage.startswith("tc-generator:")][-1]
    assert len(_request(run_root, child, rework_stage)["input_digests"]) == 5


# --- compatibility evidence ---------------------------------------------------------------------------------------


def test_compatibility_evidence_of_a_design_run_binds_the_design(tmp_path: Path, pack_root: Path) -> None:
    from tests.test_compatibility_contract import _digest, _durable_evidence
    from tools.compatibility_contract import validate_compatibility_evidence
    from tools.release_manifest import build_release_manifest

    project = _project(tmp_path, local=True)
    _design_setting(project, True)
    done = _drive(project, _start(project, "local-pilot-v1"), DesignModel("local-pilot-v1"))
    run_root, attempt_id = _run_root(project, done), done["result"]["attempt_id"]
    manifest = build_release_manifest(pack_root)
    evidence = _durable_evidence(manifest, run_root, attempt_id)
    assert [row["stage"] for row in evidence["stages"]][:3] == ["orchestrate", "context-marker", "test-design"]
    assert validate_compatibility_evidence(evidence, manifest, pack_root=pack_root, run_root=run_root) == {
        "level": "compatible", "independence": "verified", "errors": []}

    # Without its design row the evidence of a design run is invalid.
    forged = {key: value for key, value in evidence.items() if key != "digest"}
    forged["stages"] = [row for row in evidence["stages"] if row["stage"] != "test-design"]
    forged["digest"] = _digest(forged)
    assert validate_compatibility_evidence(forged, manifest, pack_root=pack_root, run_root=run_root)["level"] == "incompatible"
