"""R1, Р5, Р6: case review in compact-v1 through the driver.

* the review mode is a run setting (``--review-mode``), recorded in config and in the
  review snapshot; the default stays ``pairs``;
* step5 ``2c10d733`` is reviewed in one compact part and accepted end to end;
* Р5: every field of the correction dictionary maps to one JSON pointer; a wrong
  ``before``, an unknown field or target and an ambiguous ID are refused; the
  ``3e852e76`` correction in the compact format gives the same effective r2 as legacy;
* Р6: a ref that is not an anchor of the part, a case area citing another case, an
  unanswered lint suspicion, too many INFO findings and unknown finding IDs are refused.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.live_step5 import Replay
from tests.review_scaling_helpers import clean_compact_answer, part_text, step5_snapshot
from tools import review_compact, review_lint
from tools.review_modes import build_offline_plan
from tools.review_parts import review_digest


def _compact(task):
    return clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None


def _run_root(replay: Replay, task: dict) -> Path:
    return replay.project / ".pilot-runs" / str(task["run_id"])


def test_default_review_mode_is_compact_and_pairs_stays_available(tmp_path: Path) -> None:
    from tools.pilot_state import read_review_plan

    def until(item):
        return str(item.get("stage", "")).startswith("tc-reviewer:")

    default = Replay("2c10d733", tmp_path / "default")
    default.review_mode = None
    code, task = default.drive(default.start()[1], until=until)
    config = json.loads((default.project / ".pilot-runs" / f"{task['run_id']}.driver" / "config.json").read_text(encoding="utf-8"))
    assert config["review_mode"] == "compact-v1" and task["review_mode"] == "compact-v1"
    assert read_review_plan(_run_root(default, task), task["attempt_id"])["mode"] == "compact-v1"

    replay = Replay("2c10d733", tmp_path / "pairs")  # pins --review-mode pairs, the mode of the recorded answers
    code, task = replay.drive(until=until)
    config = json.loads((replay.project / ".pilot-runs" / f"{task['run_id']}.driver" / "config.json").read_text(encoding="utf-8"))
    assert config["review_mode"] == "pairs"
    plan = read_review_plan(_run_root(replay, task), task["attempt_id"])
    assert plan["schema_version"] == "1.0.0" and "mode" not in plan
    assert task["inputs"][0].endswith(".input.json") and "review_mode" not in task


def test_compact_case_review_is_one_part_and_accepted_end_to_end(tmp_path: Path) -> None:
    from tools.pilot_state import read_attempt_receipt, read_review_aggregate, read_review_plan

    replay = Replay("2c10d733", tmp_path, override=_compact)
    code, done = replay.drive(replay.start_with("--review-mode", "compact-v1")[1])
    result = done["result"]
    # cases-only never accepts a draft; the review itself completed like the legacy one.
    assert (result["status"], result["completion"], result["coverage"], result["reason_code"]) == ("terminal", "COMPLETE", "FULL", None), done
    tasks = replay.tasks("tc-reviewer:")
    assert len(tasks) == 1 and tasks[0]["review_mode"] == "compact-v1" and tasks[0]["inputs"][0].endswith(".input.md")
    root = replay.project / ".pilot-runs" / done["run_id"]
    attempt = done["result"]["attempt_id"]
    plan = read_review_plan(root, attempt)
    assert (plan["schema_version"], plan["mode"], len(plan["parts"])) == ("2.0.0", "compact-v1", 1)
    part = plan["parts"][0]
    assert Path(tasks[0]["inputs"][0]).read_text(encoding="utf-8") == part["text"]
    assert part["input_byte_count"] == len(part["text"].encode("utf-8")) < 80_000
    snapshot = read_attempt_receipt(root, attempt, "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert snapshot["review_mode"] == "compact-v1" and snapshot["projection"]["digest"] == plan["snapshot"]["projection_digest"]
    assert snapshot["review_policy"]["role_policy"] == "canonical-reviewer-v2" and snapshot["review_policy"]["model_id"] == replay.config["model_id"]
    aggregate = read_review_aggregate(root, attempt)
    assert aggregate["aggregate"]["mode"] == "compact-v1" and aggregate["output"]["artifacts"]["validation_report"]["verdict"] == "ПРИНЯТО"
    config = json.loads((root.parent / f"{done['run_id']}.driver" / "config.json").read_text(encoding="utf-8"))
    assert config["review_mode"] == "compact-v1"


# --------------------------------------------------------------------------- Р5


def test_every_dictionary_field_maps_to_exactly_one_pointer() -> None:
    document = step5_snapshot()["document"]
    case = document["test_cases"][4]
    step = case["steps"][0]
    expectation = step["expectations"][0]
    requirement = document["requirements"][2]
    rows = [(case["case_id"], "title", "/test_cases/4/title"), (case["case_id"], "objective", "/test_cases/4/objective"),
            (case["case_id"], "preconditions[0]", "/test_cases/4/preconditions/0"),
            (case["case_id"], "management.folder", "/test_cases/4/management/folder"),
            (case["case_id"], "management.components[0]", "/test_cases/4/management/components/0"),
            (step["step_id"], "action", "/test_cases/4/steps/0/action"), (step["step_id"], "test_data", "/test_cases/4/steps/0/test_data"),
            (expectation["expectation_id"], "text", "/test_cases/4/steps/0/expectations/0/text"),
            (requirement["requirement_id"], "text", "/requirements/2/text")]
    for target, field, pointer in rows:
        assert review_compact.correction_pointer(document, target, field)[0] == pointer
    pointers = [pointer for _target, _field, pointer in rows]
    assert len(set(pointers)) == len(pointers)


@pytest.mark.parametrize("target,field,code", [
    ("TC-B1-005", "steps", "REVIEW_CORRECTION_FIELD"),              # not in the dictionary
    ("TC-B1-005", "preconditions", "REVIEW_CORRECTION_FIELD"),      # a list needs an index
    ("TC-B1-005", "title[0]", "REVIEW_CORRECTION_FIELD"),           # a scalar takes no index
    ("TC-B1-005", "preconditions[9]", "REVIEW_CORRECTION_FIELD"),   # no such item
    ("TC-B1-005", "management.owner", "REVIEW_CORRECTION_FIELD"),   # null is not text
    ("ASSERT-B1-005-01-1-1", "text", "REVIEW_CORRECTION_TARGET"),   # assertions are machine semantics
    ("TC-NOPE", "title", "REVIEW_CORRECTION_TARGET"),
])
def test_unknown_fields_and_targets_are_refused(target: str, field: str, code: str) -> None:
    with pytest.raises(review_compact.CompactReviewError) as error:
        review_compact.correction_pointer(step5_snapshot()["document"], target, field)
    assert error.value.rows[0]["code"] == code


def test_an_ambiguous_id_and_a_wrong_before_are_refused() -> None:
    document = step5_snapshot()["document"]
    duplicate = copy.deepcopy(document)
    duplicate["test_cases"][5]["steps"][0]["step_id"] = duplicate["test_cases"][4]["steps"][0]["step_id"]
    with pytest.raises(review_compact.CompactReviewError) as error:
        review_compact.correction_pointer(duplicate, duplicate["test_cases"][4]["steps"][0]["step_id"], "action")
    assert error.value.rows[0]["code"] == "REVIEW_CORRECTION_AMBIGUOUS"
    with pytest.raises(review_compact.CompactReviewError) as error:
        review_compact.legacy_corrections(document, "part-000001", [{"target_id": "TC-B1-005", "field": "title", "before": "не то", "after": "x", "why": "y"}])
    assert error.value.rows[0]["code"] == "REVIEW_CORRECTION_BEFORE"


def _effective_after_review(replay: Replay, payload: dict) -> dict:
    from tools.pilot_state import read_effective_canonical_if_present, read_review_aggregate

    code, task = replay.drive(payload, until=lambda item: str(item.get("stage", "")).startswith("tc-to-autotest:"))
    assert str(task.get("stage", "")).startswith("tc-to-autotest:"), task
    root = _run_root(replay, task)
    assert read_review_aggregate(root, task["attempt_id"])["output"]["artifacts"]["validation_report"]["verdict"] == "AUTO_FIX_APPLIED"
    return dict(read_effective_canonical_if_present(root, task["attempt_id"]))


def test_the_3e852e76_correction_in_compact_form_gives_the_legacy_effective_r2(tmp_path: Path) -> None:
    legacy = Replay("3e852e76", tmp_path / "legacy")
    expected = _effective_after_review(legacy, legacy.start()[1])

    def review(task):
        if task.get("review_mode") != "compact-v1":
            return None
        answer = clean_compact_answer(part_text(task))
        answer["corrections"] = [{"target_id": "TC-B1-011", "field": "management.components[0]", "before": "StudentController",
                                  "after": "HelloWorldController", "why": "GET /hello-world обслуживает HelloWorldController."}]
        return answer

    compact = Replay("3e852e76", tmp_path / "compact", override=review)
    actual = _effective_after_review(compact, compact.start_with("--review-mode", "compact-v1")[1])
    assert actual["document"]["test_cases"][10]["management"]["components"] == ["HelloWorldController"]
    assert review_digest(actual["document"]) == review_digest(expected["document"])
    # A service correction is not re-checked: the compact review stayed one part.
    assert len(compact.tasks("tc-reviewer:")) == 1


# --------------------------------------------------------------------------- Р6


@pytest.fixture()
def linted(monkeypatch):
    """A plan whose part carries one lint suspicion (the real rules arrive in R3)."""
    monkeypatch.setattr(review_lint, "RULES", [("test-rule", lambda document: [
        {"case_ids": ["TC-B1-005"], "related_ids": ["TC-B1-005", "ASSERT-B1-005-01-1-2"], "message": "Проверь литерал id."}])])
    snapshot = step5_snapshot()
    plan = build_offline_plan(snapshot, mode="compact-v1")
    return plan, snapshot


def _bound(plan: dict, answer: dict) -> dict:
    part = plan["parts"][0]
    return {"schema_version": "2.0.0", "plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
            "part_id": part["part_id"], "input_digest": review_digest(review_compact.part_input(plan, part)), **answer}


def _codes(plan: dict, snapshot: dict, answer: dict) -> list[str]:
    return [row["code"] for row in review_compact.validate_answer(plan, plan["parts"][0], _bound(plan, answer), snapshot["document"])]


def test_a_clean_answer_is_accepted(linted) -> None:
    plan, snapshot = linted
    answer = clean_compact_answer(plan["parts"][0]["text"])
    assert [row["lint_id"] for row in answer["lint_dispositions"]] == plan["parts"][0]["lint_ids"] != []
    assert _codes(plan, snapshot, answer) == []


def test_a_ref_that_is_not_an_anchor_of_the_part_is_refused(linted) -> None:
    plan, snapshot = linted
    answer = clean_compact_answer(plan["parts"][0]["text"])
    answer["coverage"][1]["refs"] = ["ASSERT-B1-999-01-1-1"]
    assert "REVIEW_REF_UNKNOWN" in _codes(plan, snapshot, answer)
    answer["coverage"][1]["refs"] = ["SRC-1:L99999"]
    assert "REVIEW_REF_UNKNOWN" in _codes(plan, snapshot, answer)


def test_a_case_area_citing_only_another_case_is_refused(linted) -> None:
    plan, snapshot = linted
    answer = clean_compact_answer(plan["parts"][0]["text"])
    row = next(item for item in answer["coverage"] if item["area_id"] == "local-TC-B1-005")
    row["refs"] = ["TC-B1-006", "ASSERT-B1-006-01-1-1"]
    assert "REVIEW_REF_FOREIGN" in _codes(plan, snapshot, answer)
    row["refs"] = ["TC-B1-006", "STEP-B1-005-01"]  # one own anchor is enough
    assert "REVIEW_REF_FOREIGN" not in _codes(plan, snapshot, answer)


def test_an_unanswered_lint_suspicion_is_refused(linted) -> None:
    plan, snapshot = linted
    answer = clean_compact_answer(plan["parts"][0]["text"])
    answer["lint_dispositions"] = []
    assert "REVIEW_LINT_UNANSWERED" in _codes(plan, snapshot, answer)


def test_info_limit_unknown_ids_and_area_order_are_enforced(linted) -> None:
    plan, snapshot = linted
    answer = clean_compact_answer(plan["parts"][0]["text"])
    answer["findings"] = [{"severity": "INFO", "code": "STYLE", "related_ids": ["TC-B1-001"], "message": "Замечание."}] * 6
    assert "REVIEW_INFO_LIMIT" in _codes(plan, snapshot, answer)
    answer["findings"] = [{"severity": "WARNING", "code": "UNKNOWN_CASE", "related_ids": ["TC-B1-404"], "message": "Нет такого кейса."}]
    assert "REVIEW_FINDING_UNKNOWN_ID" in _codes(plan, snapshot, answer)
    answer = clean_compact_answer(plan["parts"][0]["text"])
    answer["coverage"].reverse()
    assert "REVIEW_PART_COVERAGE" in _codes(plan, snapshot, answer)
    answer = clean_compact_answer(plan["parts"][0]["text"])
    answer["coverage"][0]["note"] = "ж" * 201
    assert _codes(plan, snapshot, answer)  # schema: note is at most 200 characters


def test_the_driver_rejects_a_foreign_ref_with_its_code(tmp_path: Path) -> None:
    def bad(task):
        if task.get("review_mode") != "compact-v1":
            return None
        answer = clean_compact_answer(part_text(task))
        answer["coverage"][1]["refs"] = ["TC-B1-999"]
        return answer

    replay = Replay("2c10d733", tmp_path, override=bad)
    code, task = replay.drive(replay.start_with("--review-mode", "compact-v1")[1], until=lambda item: str(item.get("stage", "")).startswith("tc-reviewer:"))
    Path(task["output_path"]).write_text(json.dumps(bad(task), ensure_ascii=False), encoding="utf-8")
    code, rejected = replay.submit(task)
    assert rejected["status"] == "rejected" and "REVIEW_REF_UNKNOWN" in {row["code"] for row in rejected["errors"]}
    Path(task["output_path"]).write_text(json.dumps(_compact(task), ensure_ascii=False), encoding="utf-8")
    code, after = replay.submit(task)
    assert after.get("status") != "rejected"
