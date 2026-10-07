"""R2: automation review in compact-v1.

* step5 ``9340016c`` automation review is one part (instead of 6), with the exact method
  slice of every case, the SUPPORT code once and a shared-state table;
* the automation case view keeps every anchor and binding; Petclinic ``b7733d39`` fits
  six parts (Р14), the full capabilities only in the SUPPORT home part;
* code checks before the model flag a changed literal, a changed path and an extra body
  field as suspicions, and nothing on the clean class;
* a file that cannot be sliced goes whole into the part, or blocks it when it does not fit;
* a local-pilot run on the Python project passes end to end in compact-v1.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from tests.live_step5 import review_state
from tests.review_scaling_helpers import clean_compact_answer, eval_data, part_text
from tests.test_review_fixes_driver import SavedModel, _drive, _project, _start
from tools import review_compact
from tools.review_modes import build_offline_plan
from tools.review_parts import review_digest
from tools.review_projection import _compact_data, anchors, automation_case_text, case_text


def _automation_snapshot() -> dict:
    return copy.deepcopy(review_state("9340016c", "review-snapshot-r1")["payload"])


def _with_java(snapshot: dict, edit) -> dict:
    file = snapshot["automation"]["artifacts"]["generated_files"][0]
    file["content"] = edit(file["content"])
    return snapshot


def test_step5_automation_review_is_one_part_with_exact_slices() -> None:
    plan = build_offline_plan(_automation_snapshot(), mode="compact-v1", review_key="r1")
    [part] = plan["parts"]
    assert part["blocked_reason"] is None and part["input_byte_count"] < 60_000
    kinds = [area["kind"] for area in part["areas"]]
    assert kinds[0] == "support" and kinds.count("local") == 12 and kinds[-1] == "cross"
    text = part["text"]
    assert re.search(r'L0093\|\s+assertThat\(response\.getStatus\(\)\)\.as\("ASSERT-B1-001-01-1-1"\)\.isEqualTo\(200\);', text)
    assert "Таблица общего состояния" in text and "хелперы (в общем коде): exchange L43–L56" in text
    assert plan["lint"] == []
    ranges = part["case_ranges"]["TC-B1-002"]
    assert [88 + 12, 110] in [row[:2] for row in ranges] and [78, 84] in [row[:2] for row in ranges]  # method and fourStudents()


def test_the_automation_case_view_keeps_every_anchor_and_every_binding() -> None:
    documents = [_automation_snapshot()["document"], eval_data.petclinic_b7733d39_canonical(), eval_data.petclinic_document()]
    for document in documents:
        for case in document["test_cases"]:
            dense = automation_case_text(case)
            assert anchors(dense) == anchors(case_text(case))
            for step in case["steps"]:
                for output in step.get("outputs") or []:
                    assert re.search(rf"\b{re.escape(output['output_id'])}\b", dense)
                if not step.get("inputs"):
                    assert _compact_data(step["test_data"]).split("\n")[0] in dense
    full = sum(len(case_text(case)) for case in documents[1]["test_cases"])
    assert sum(len(automation_case_text(case)) for case in documents[1]["test_cases"]) < 0.7 * full


def test_compact_test_data_drops_only_whitespace_between_json_tokens() -> None:
    text = '{\n  "a": "x  y\\" z",\n  "b": [1.50, 1e5, -0],\n  "c": "<D из шага 1>"\n}'
    assert _compact_data(text) == '{"a":"x  y\\" z","b":[1.50,1e5,-0],"c":"<D из шага 1>"}'
    assert json.loads(_compact_data(text)) == json.loads(text)
    for other in ("D — дата сервера", "[не JSON", "", None, {"kind": "x"}):
        assert _compact_data(other) == other


def test_petclinic_automation_review_fits_six_parts() -> None:
    plan = build_offline_plan(eval_data.petclinic_automation_snapshot(), mode="compact-v1", review_key="r1")
    parts = plan["parts"]
    assert len(parts) <= 6 and not any(part["blocked_reason"] for part in parts)
    assert sorted(case for part in parts for case in part["case_ids"]) == sorted(case["case_id"] for case in
                                                                                 eval_data.petclinic_b7733d39_canonical()["test_cases"])
    # Only the part that homes the SUPPORT code carries the full capabilities.
    assert [("## Возможности (полностью)" in part["text"]) for part in parts] == [True] + [False] * (len(parts) - 1)
    assert [any(area["kind"] == "support" for area in part["areas"]) for part in parts] == [True] + [False] * (len(parts) - 1)


def test_code_checks_flag_literal_path_and_extra_body_field() -> None:
    def seed(java: str) -> str:
        java = java.replace('as("ASSERT-B1-006-01-1-1").isEqualTo(400);', 'as("ASSERT-B1-006-01-1-1").isEqualTo(404);')
        java = java.replace('exchange("PUT", "/students/10/update", null, body);', 'exchange("PUT", "/students/42/update", null, body);')
        return java.replace('        body.put("firstName", "Olga");\n', '        body.put("firstName", "Olga");\n        body.put("lastName", null);\n')

    plan = build_offline_plan(_with_java(_automation_snapshot(), seed), mode="compact-v1", review_key="r1")
    found = {(row["rule"], row["case_ids"][0]) for row in plan["lint"]}
    assert found == {("assert-literal-differs", "TC-B1-006"), ("request-differs", "TC-B1-009"), ("request-differs", "TC-B1-008")}
    assert plan["parts"][0]["lint_ids"] == [row["lint_id"] for row in plan["lint"]]


def test_an_unsliceable_file_goes_whole_or_blocks_the_part() -> None:
    snapshot = _with_java(_automation_snapshot(), lambda java: java.replace("class StudentControllerPipelineTest {", "class StudentControllerPipelineTest {{"))
    plan = build_offline_plan(snapshot, mode="compact-v1", review_key="r1")
    assert plan["slice_failures"][0]["file_id"] == "FILE-B1-student-controller-pipeline-test"
    text = plan["parts"][0]["text"]
    assert "Срез не удался" in text and "L0218| }" in text and plan["parts"][0]["blocked_reason"] is None
    blocked = build_offline_plan(snapshot, mode="compact-v1", review_key="r1", input_byte_budget=12_000, response_reserve_bytes=1_000)
    assert all(part["blocked_reason"] == "REVIEW_CONTEXT_LIMIT" for part in blocked["parts"])


def test_a_case_area_may_cite_lines_of_its_own_method_or_helpers_only() -> None:
    snapshot = _automation_snapshot()
    plan = build_offline_plan(snapshot, mode="compact-v1", review_key="r1")
    part = plan["parts"][0]
    answer = clean_compact_answer(part["text"])
    bound = {"schema_version": "2.0.0", "plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
             "part_id": part["part_id"], "input_digest": review_digest(review_compact.part_input(plan, part)), **answer}

    def codes(refs):
        row = next(item for item in bound["coverage"] if item["area_id"] == "local-TC-B1-002")
        row["refs"] = refs
        return [item["code"] for item in review_compact.validate_answer(plan, part, bound, snapshot["document"], snapshot["automation"])]

    assert codes(["L105"]) == []          # its own method
    assert codes(["L80"]) == []           # fourStudents(), a helper it calls
    assert "REVIEW_REF_FOREIGN" in codes(["L117"])   # the method of TC-B1-003
    assert "REVIEW_REF_UNKNOWN" in codes(["L999"])


def test_local_pilot_python_run_passes_with_compact_reviews(tmp_path: Path) -> None:
    from tools.pilot_state import read_review_plan

    class CompactModel(SavedModel):
        def answer(self, task):
            if task.get("review_mode") == "compact-v1":
                self.stages.append(task["stage"])
                return clean_compact_answer(part_text(task))
            return super().answer(task)

    project = _project(tmp_path, local=True)
    model = CompactModel("local-pilot-v1")
    done = _drive(project, _start(project, "local-pilot-v1", review_mode="compact-v1"), model)
    result = done["result"]
    assert (result["verification"], result["accepted"], result["reason_code"]) == ("PASS", True, None), result
    reviews = [stage for stage in model.stages if stage.startswith(("tc-reviewer:", "autotest-reviewer:"))]
    assert reviews == ["tc-reviewer:canonical:part-000001", "autotest-reviewer:r1:part-000001"]
    root = project / ".pilot-runs" / done["run_id"]
    plan = read_review_plan(root, result["attempt_id"], "r1")
    assert plan["mode"] == "compact-v1" and "Общий код (SUPPORT)" in plan["parts"][0]["text"]
    # Code checks on the Python class: what they raised is recorded for Р10.
    assert all(row["rule"] in {"assert-id-missing", "assert-literal-differs", "request-differs"} for row in plan["lint"])
