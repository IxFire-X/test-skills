"""P2: the automation generator works part by part when the whole document does not fit the task input budget.

The part plan is a pure function of the effective document and the byte budget left after the context and the brief:
whole cases in document order, every case exactly once, every part within the budget.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tools import automation_parts as parts

ROOT = Path(__file__).resolve().parents[1]
PETCLINIC = ROOT / "tests" / "fixtures" / "replay-petclinic-20261008"
PETCLINIC_CONTEXT_BYTES = 405_624  # inputs/context of the live Petclinic run d (113 files)


def _petclinic_document() -> dict:
    return json.loads(gzip.open(PETCLINIC / "canonical-snapshot.json.gz").read())["payload"]["document"]


def test_petclinic_splits_into_parts_that_fit_the_default_budget() -> None:
    document = _petclinic_document()
    budget = 1_000_000 - PETCLINIC_CONTEXT_BYTES - 4_096  # context and brief travel with every part
    plan = parts.plan_parts(document, budget)
    assert len(plan) >= 3, len(plan)
    ids = [case_id for part in plan for case_id in part["case_ids"]]
    assert ids == [case["case_id"] for case in document["test_cases"]]  # document order, every case exactly once
    for part in plan:
        assert len(parts.part_document_bytes(document, part["case_ids"])) <= budget, part["part_id"]
    assert [part["part_id"] for part in plan] == [f"part-{index:03d}" for index in range(1, len(plan) + 1)]
    assert parts.plan_parts(document, budget) == plan  # deterministic


def test_one_part_when_the_document_fits() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, 10_000_000)
    assert len(plan) == 1 and plan[0]["case_ids"] == [case["case_id"] for case in document["test_cases"]]


def test_a_case_larger_than_the_budget_is_refused() -> None:
    document = _petclinic_document()
    largest = max(len(parts.part_document_bytes(document, [case["case_id"]])) for case in document["test_cases"])
    with pytest.raises(parts.PartPlanError):
        parts.plan_parts(document, largest - 1)


def test_the_part_document_keeps_the_header_and_only_its_cases() -> None:
    document = _petclinic_document()
    chosen = [document["test_cases"][1]["case_id"], document["test_cases"][3]["case_id"]]
    subset = json.loads(parts.part_document_bytes(document, chosen))
    assert [case["case_id"] for case in subset["test_cases"]] == chosen
    assert {key: value for key, value in subset.items() if key != "test_cases"} == {key: value for key, value in document.items() if key != "test_cases"}


# --------------------------------------------------------------------------------------
# step (a): the case limit, balancing, the part brief, the part answer and the merge
# --------------------------------------------------------------------------------------

def _budget() -> int:
    return 1_000_000 - PETCLINIC_CONTEXT_BYTES - 4_096


def test_petclinic_splits_into_balanced_parts_of_at_most_twelve_cases() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, _budget(), cases_per_part=12)
    sizes = [len(part["case_ids"]) for part in plan]
    assert len(plan) == 7 and max(sizes) <= 12 and max(sizes) - min(sizes) <= 1, sizes  # 81 cases: 7 parts of 11-12
    assert [case_id for part in plan for case_id in part["case_ids"]] == [case["case_id"] for case in document["test_cases"]]
    assert all(len(parts.part_document_bytes(document, part["case_ids"])) <= _budget() for part in plan)


def test_balancing_adds_parts_until_every_part_fits_its_bytes() -> None:
    """Even counts are not even sizes: a part of equal count may exceed the budget, then k grows and the split repeats."""
    big = "x" * 4_000
    cases = [{"case_id": f"TC-{index:02d}", "title": big if index < 3 else "s"} for index in range(12)]
    document = {"document_id": "D", "test_cases": cases}
    budget = len(parts.part_document_bytes(document, ["TC-00", "TC-01"])) + 50  # two big cases fit, three do not
    plan = parts.plan_parts(document, budget, cases_per_part=6)
    assert all(len(parts.part_document_bytes(document, part["case_ids"])) <= budget for part in plan)
    assert len(plan) >= 3 and [case_id for part in plan for case_id in part["case_ids"]] == [case["case_id"] for case in cases]
    assert parts.plan_parts(document, budget, cases_per_part=6) == plan


def test_no_case_limit_with_zero_and_one_part_under_both_limits() -> None:
    document = _petclinic_document()
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=0)) == 1
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=81)) == 1
    assert len(parts.plan_parts(document, 10_000_000, cases_per_part=40)) == 3


def test_a_repeated_case_id_is_refused_before_the_split() -> None:
    document = {"document_id": "D", "test_cases": [{"case_id": "TC-1"}, {"case_id": "TC-2"}, {"case_id": "TC-1"}]}
    with pytest.raises(parts.PartPlanError) as raised:
        parts.plan_parts(document, 10_000_000)
    assert raised.value.code == "AUTOMATION_PARTS_INVALID"


def test_the_plan_receipt_states_the_cost_of_the_split() -> None:
    document = _petclinic_document()
    plan = parts.plan_parts(document, _budget(), cases_per_part=12)
    receipt = parts.plan_receipt(document, plan, fixed_bytes=PETCLINIC_CONTEXT_BYTES + 4_096, budget=_budget(), cases_per_part=12,
                                 single_task_input_bytes=2_345_593)  # measured by the driver for the live Petclinic run
    assert receipt["parts"] == len(plan) and [row["part_id"] for row in receipt["rows"]] == [part["part_id"] for part in plan]
    assert receipt["total_input_bytes"] == sum(row["input_bytes"] for row in receipt["rows"])
    assert all(row["input_bytes"] == PETCLINIC_CONTEXT_BYTES + 4_096 + len(parts.part_document_bytes(document, row["case_ids"])) for row in receipt["rows"])
    assert receipt["single_task_input_bytes"] == 2_345_593


def _part_answer(part_id: str, case_ids: list[str], *, path: str | None = None, extra_case: str | None = None) -> dict:
    marker = parts.part_marker(part_id)
    file_id = parts.file_id_prefix(part_id) + "1"
    relations = [{"case_id": case_id, "step_id": f"STEP-{case_id}", "kind": "operation", "file_id": file_id, "symbol_id": f"SYM-{case_id}"}
                 for case_id in [*case_ids, *([extra_case] if extra_case else [])]]
    return {"automation_status": "GENERATED",
            "generated_files": [{"file_id": file_id, "path": path or f"src/test/java/x/Pet{marker}Test.java", "language": "java", "framework": "junit5",
                                 "content": f"class Pet{marker}Test {{}}"}],
            "generated_symbols": [{"file_id": file_id, "symbol_id": f"SYM-{case_id}", "locator": {"method_name": f"m{case_id}"}} for case_id in case_ids],
            "implementation_relations": relations, "manual_dispositions": [], "diagnostics": [], "warnings": [f"{part_id} — ok", "общее — ok"]}


def test_a_part_answer_keeps_to_its_prefix_marker_and_cases() -> None:
    good = _part_answer("part-002", ["TC-3", "TC-4"])
    assert parts.part_answer_problems("part-002", ["TC-3", "TC-4"], good) == []
    codes = lambda value: {row["code"] for row in parts.part_answer_problems("part-002", ["TC-3", "TC-4"], value)}
    assert "AUTOMATION_PART_PATH" in codes(_part_answer("part-002", ["TC-3", "TC-4"], path="src/test/java/x/PetTest.java"))
    assert "AUTOMATION_PART_CASE" in codes(_part_answer("part-002", ["TC-3", "TC-4"], extra_case="TC-9"))
    wrong = _part_answer("part-002", ["TC-3", "TC-4"])
    wrong["generated_files"][0]["file_id"] = "FILE-1"
    assert "AUTOMATION_PART_FILE_ID" in codes(wrong)
    assert "AUTOMATION_PART_COVERAGE" in codes(_part_answer("part-002", ["TC-3"]))


def test_the_merge_unites_the_parts_in_order() -> None:
    first, second = _part_answer("part-001", ["TC-1", "TC-2"]), _part_answer("part-002", ["TC-3"])
    second["automation_status"] = "BLOCKED"
    second["diagnostics"] = [{"code": "X", "message": "m"}]
    merged = parts.merge_part_answers([first, second])
    assert [row["file_id"] for row in merged["generated_files"]] == [first["generated_files"][0]["file_id"], second["generated_files"][0]["file_id"]]
    assert len(merged["generated_symbols"]) == 3 and len(merged["implementation_relations"]) == 3
    assert merged["automation_status"] == "BLOCKED" and merged["diagnostics"] == [{"code": "X", "message": "m"}]
    assert merged["warnings"] == ["part-001 — ok", "общее — ok", "part-002 — ok"]
    assert parts.merge_part_answers([first, second]) == merged
    clash = _part_answer("part-003", ["TC-4"], path=first["generated_files"][0]["path"])
    with pytest.raises(parts.PartPlanError):
        parts.merge_part_answers([first, clash])


def test_the_part_brief_names_its_cases_prefix_and_marker() -> None:
    brief = parts.part_brief({"part_id": "part-003", "case_ids": ["TC-7", "TC-8"]}, count=7)
    assert brief["owned_case_ids"] == ["TC-7", "TC-8"] and brief["file_id_prefix"] == "FILE-P003-" and brief["name_marker"] == "Part003"
    assert "3 из 7" in brief["instructions"] and "Part003" in brief["instructions"]


# --------------------------------------------------------------------------------------
# step (b): the driver runs the parts, the journal binds the merge
# --------------------------------------------------------------------------------------

from tests.test_review_fixes_driver import SavedModel, _drive, _load, _project, _run_root, _saved, _start  # noqa: E402
from tools import pipeline_driver as driver  # noqa: E402


class PartModel(SavedModel):
    """The saved automation answer, cut to the cases of each part (its file named and identified for the part)."""

    def answer(self, task):
        if not (task.get("stage", "").startswith("tc-to-autotest:") and ":part-" in task["stage"]):
            return super().answer(task)
        from tools.pilot_state import read_effective_canonical

        self.stages.append(task["stage"])
        brief = _load(task["inputs"][0])
        run_root = Path(task["output_path"]).parents[1].with_name(task["run_id"])
        document = read_effective_canonical(run_root, task["attempt_id"])["document"]
        automatable = next(case for case in document["test_cases"] if not case["steps"][0]["manual_only"])
        manual = next(case for case in document["test_cases"] if case["steps"][0]["manual_only"])
        bindings = {}
        for index, case in ((1, automatable), (2, manual)):
            step = case["steps"][0]
            bindings.update({f"case:{index}": case["case_id"], f"step:{index}": step["step_id"], f"expectation:{index}": step["expectations"][0]["expectation_id"]})
            if step["expectations"][0]["assertions"]:
                bindings[f"assertion:{index}"] = step["expectations"][0]["assertions"][0]["assertion_id"]
        value = _saved("tc-to-autotest.passing.answer.json", bindings)
        owned, prefix, marker = set(brief["owned_case_ids"]), brief["file_id_prefix"], brief["name_marker"].lower()
        value["implementation_relations"] = [row for row in value["implementation_relations"] if row["case_id"] in owned]
        value["manual_dispositions"] = [row for row in value["manual_dispositions"] if row["case_id"] in owned]
        if value["implementation_relations"]:
            for row in [*value["generated_files"], *value["generated_symbols"], *value["implementation_relations"]]:
                row["file_id"] = prefix + "1"
            value["generated_files"][0]["path"] = f"tests/test_generated_{marker}.py"
        else:
            value["generated_files"], value["generated_symbols"] = [], []
        return value


def _split_project(tmp_path: Path) -> Path:
    project = _project(tmp_path, local=True)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["limits"] = {"automation_cases_per_part": 1}  # two cases: two parts
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    return project


def test_the_driver_generates_the_automation_by_parts_and_the_run_passes(tmp_path: Path) -> None:
    from tools.pilot_state import derive_state, read_model_request

    project = _split_project(tmp_path)
    model = PartModel("local-pilot-v1")
    done = _drive(project, _start(project, "local-pilot-v1"), model)
    assert done["action"] == "done" and done["result"]["verification"] == "PASS", done
    assert [stage for stage in model.stages if stage.startswith("tc-to-autotest")] == ["tc-to-autotest:r1:part-001", "tc-to-autotest:r1:part-002"]
    run_root = _run_root(project, done)
    events = [event for event in derive_state(run_root)["events"] if event.get("attempt_id") == done["result"]["attempt_id"]]
    merged = next(event for event in events if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == "tc-to-autotest:r1")
    request = read_model_request(run_root, done["result"]["attempt_id"], "tc-to-autotest:r1", merged["artifact_digest"])
    answers = [event["artifact_digest"] for event in events if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and str(event.get("stage_instance_id")).startswith("tc-to-autotest:r1:part-")]
    assert request["input_digests"][3:] == answers and request["invocation_id"].startswith("automation-merge-")
    candidates = [event.get("stage_instance_id") for event in events if event["event_type"] == "CANDIDATE_PUBLISHED" and "tc-to-autotest" in str(event.get("stage_instance_id"))]
    assert candidates == ["tc-to-autotest:r1"]  # parts are no candidates
    from tools.pilot_state import read_model_stage_artifact

    response = next(event for event in events if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == "tc-to-autotest:r1")
    merged_artifact = read_model_stage_artifact(run_root, done["result"]["attempt_id"], "tc-to-autotest:r1", response["artifact_digest"])["artifact"]
    [generated] = merged_artifact["artifacts"]["generated_files"]  # the automatable case lives in one part
    number = generated["file_id"][len("FILE-P"):len("FILE-P") + 3]
    assert generated["path"] == f"tests/test_generated_part{number}.py"
    assert len(merged_artifact["artifacts"]["manual_dispositions"]) == 1  # the manual case came from part 2


def test_a_forged_merged_automation_and_a_part_outside_its_part_are_refused_by_the_journal(tmp_path: Path) -> None:
    from tools import pipeline_driver_automation as automation
    from tools.pilot_state import publish_model_request, publish_model_stage_artifact, read_effective_canonical

    project = _split_project(tmp_path)
    model = PartModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage") == "tc-to-autotest:r1:part-002")
    run_root, attempt_id = _run_root(project, task), task["attempt_id"]
    attempt, config = driver._attempt(run_root), driver._config(run_root)
    effective = read_effective_canonical(run_root, attempt_id)
    plan = config["automation_plans"]["r1"]
    with pytest.raises(ValueError):  # a part request must name its own part document
        publish_model_request(run_root, attempt_id, "tc-to-autotest:r1:part-002", model_id="m", invocation_id="automation-x",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"], plan["digest"], plan["rows"][0]["document_digest"]])
    automation.submit_automation(project, run_root, attempt, config, task, model.answer(task))  # the part only: no merge yet
    with pytest.raises(ValueError):  # the merged request names every part answer, in plan order
        publish_model_request(run_root, attempt_id, "tc-to-autotest:r1", model_id="m", invocation_id="automation-merge-x",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"], plan["digest"]])
    with pytest.raises(ValueError):  # a split revision is never closed by a single-task answer past the merge
        publish_model_request(run_root, attempt_id, "tc-to-autotest:r1", model_id="m", invocation_id="automation-x",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"]])
    automation._request_merged_automation(project, run_root, attempt, config, "r1")
    merged = automation._merged_automation(run_root, attempt_id, "r1")
    forged = json.loads(json.dumps(merged))
    forged["artifacts"]["generated_files"][0]["content"] += "\n# not from a part\n"
    forged["artifacts"]["generated_files"][0]["content_digest"] = "sha256:" + __import__("hashlib").sha256(
        forged["artifacts"]["generated_files"][0]["content"].encode("utf-8")).hexdigest()
    with pytest.raises(ValueError):
        publish_model_stage_artifact(run_root, attempt_id, "tc-to-autotest:r1", forged)
    publish_model_stage_artifact(run_root, attempt_id, "tc-to-autotest:r1", merged)


def test_r2_regenerates_only_the_corrected_part_and_carries_the_other(tmp_path: Path) -> None:
    from tests.test_review_fixes_driver import review_answer
    from tools.pilot_state import derive_state, publish_model_request, publish_model_stage_artifact, read_effective_canonical, read_model_request

    project = _split_project(tmp_path)
    model = PartModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage", "").startswith("autotest-reviewer:r1"))
    run_root, attempt_id = _run_root(project, task), task["attempt_id"]
    plan = driver._config(run_root)["automation_plans"]["r1"]
    from tools import pipeline_driver_automation as automation

    merged = automation._stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"]
    generated = merged["artifacts"]["generated_files"][0]
    envelope = _load(task["inputs"][0])
    scope = envelope["scopes"][0]
    correction = {"id": "FIX-001", "correction_kind": "MECHANICAL", "path": "/artifacts/generated_files/0/content", "before": generated["content"],
                  "after": generated["content"] + "\n", "related_ids": [scope["scope_id"], generated["file_id"]], "description": "Убрать лишнее.",
                  "evidence": [scope["inputs"][0]["artifact_digest"]]}
    Path(task["output_path"]).write_text(json.dumps(review_answer(task, corrections=[correction]), ensure_ascii=False), encoding="utf-8")
    second = driver.submit(project, run_root, task["task_id"])
    assert second.get("status") != "rejected", second.get("errors")
    corrected = "part-" + generated["file_id"][len("FILE-P"):len("FILE-P") + 3]
    carried = next(row["part_id"] for row in plan["rows"] if row["part_id"] != corrected)
    assert second["stage"] == f"tc-to-autotest:r2:{corrected}", second  # only the corrected part goes to the model
    events = [event for event in derive_state(run_root)["events"] if event.get("attempt_id") == attempt_id]
    request = next(event for event in events if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == f"tc-to-autotest:r2:{carried}")
    assert read_model_request(run_root, attempt_id, f"tc-to-autotest:r2:{carried}", request["artifact_digest"])["invocation_id"].startswith("automation-carry-")
    done = _drive(project, second, model)
    assert (done["result"]["verification"], done["result"]["accepted"]) == ("PASS", True), done
    assert automation._stage_artifact(run_root, attempt_id, "tc-to-autotest:r2")["artifact"]["artifacts"]["automation_revision"] == 2


def test_a_forged_carried_part_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A part carried into r2 without a model call must equal its r1 answer: the journal refuses anything else."""
    from tests.test_review_fixes_driver import review_answer
    from tools import pipeline_driver_automation as automation
    from tools.pilot_state import derive_state

    project = _split_project(tmp_path)
    model = PartModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage", "").startswith("autotest-reviewer:r1"))
    run_root, attempt_id = _run_root(project, task), task["attempt_id"]
    merged = automation._stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"]
    generated = merged["artifacts"]["generated_files"][0]
    scope = _load(task["inputs"][0])["scopes"][0]
    correction = {"id": "FIX-001", "correction_kind": "MECHANICAL", "path": "/artifacts/generated_files/0/content", "before": generated["content"],
                  "after": generated["content"] + "\n", "related_ids": [scope["scope_id"], generated["file_id"]], "description": "Убрать лишнее.",
                  "evidence": [scope["inputs"][0]["artifact_digest"]]}
    Path(task["output_path"]).write_text(json.dumps(review_answer(task, corrections=[correction]), ensure_ascii=False), encoding="utf-8")
    genuine = automation.normalize_automation

    def forging(content, *args, **kwargs):  # the carry of the driver writes something the r1 part never answered
        return genuine({**content, "warnings": [*content.get("warnings", []), "подложено — не из r1"]}, *args, **kwargs)

    monkeypatch.setattr(automation, "normalize_automation", forging)
    with pytest.raises(ValueError):
        driver.submit(project, run_root, task["task_id"])
    carried = [event for event in derive_state(run_root)["events"] if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
               and str(event.get("stage_instance_id")).startswith("tc-to-autotest:r2:")]
    assert carried == []  # nothing was recorded for the forged part
