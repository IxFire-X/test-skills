"""FaT-SKB: the skeleton of an import goes to tc-generator by batch — the last input of batch k is the skeleton of its own
cases only (a receipt per batch, published with the plan), not the whole import; the whole receipt stays the code's record."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.journal_helpers import assert_refused_once

from tests.helpers import prepare_stage, submit_stage
from tests.test_cases_import import HEADER, _batch_stage, _manual_fragment, _skeleton, _to_generator


def _three_cases_csv() -> str:
    rows = [f"Кейс {n},Цель {n},,High,Шаг {n},\"{'д' * 2500}\",Итог {n}" for n in range(1, 4)]
    rows[1] = f"Кейс 2 {'к' * 1500},,,High,Шаг 2,,Итог 2"  # normalized cells in the second case only
    return HEADER + "\r\n" + "\r\n".join(rows) + "\r\n"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    return _to_generator(tmp_path, monkeypatch, _three_cases_csv(), window_bytes=120_000, input_bytes=40_000)


def _receipt(run: dict, batch_id: str) -> dict:
    from tools.pilot_state import read_attempt_receipt

    return read_attempt_receipt(run["root"], run["attempt_id"], "imported-cases-" + batch_id, "ARTIFACT_READ_BACK")["record"]["imported"]


def test_the_plan_publishes_one_skeleton_per_batch_with_its_own_cases(run: dict) -> None:
    from tests.test_review_requested import _journal
    from tools.pilot_state import publish_generator_input

    batches = run["plan"]["batches"]
    assert len(batches) == 3 and set(run["batch_skeletons"]) == {batch["batch_id"] for batch in batches}
    for batch in batches:
        value = _receipt(run, batch["batch_id"])
        assert value["digest"] == run["batch_skeletons"][batch["batch_id"]] and value["batch_id"] == batch["batch_id"]
        assert [case["source_requirement_id"] for case in value["cases"]] == batch["owned_source_requirement_ids"]
        assert value["plan_digest"] == run["plan_digest"] and value["csv_digest"] == run["imported"]["csv_digest"]
    second = _receipt(run, batches[1]["batch_id"])
    assert second["normalized"] == [{"row": 3, "field": "objective"}, {"row": 3, "field": "test_data"}]
    assert "normalized" not in _receipt(run, batches[0]["batch_id"])  # only the rows of its own cases
    before = _journal(run["root"])
    again = publish_generator_input(run["root"], run["attempt_id"], "plan", run["plan"])
    assert again["batch_skeletons"] == run["batch_skeletons"] and _journal(run["root"]) == before


def test_the_last_input_of_a_batch_is_its_skeleton_not_the_whole_import(run: dict) -> None:
    from tools.pilot_state import read_model_request

    code, prepared = prepare_stage(run["root"], _batch_stage(run))
    assert code == 0, prepared
    inputs = read_model_request(run["root"], run["attempt_id"], _batch_stage(run), prepared["result"]["request_digest"])["input_digests"]
    assert inputs[-1] == _skeleton(run, 0) and run["imported"]["digest"] not in inputs, inputs


def test_a_forged_batch_skeleton_is_refused_on_read(run: dict) -> None:
    from tools.pilot_state import _receipt_target, read_attempt_receipt

    batch_id = run["plan"]["batches"][0]["batch_id"]
    target = _receipt_target(run["root"], run["attempt_id"], "imported-cases-" + batch_id)
    assert target.parent.name == "imported-cases" and target.name == f"{run['attempt_id']}.{batch_id}.json"
    forged = json.loads(target.read_bytes())
    forged["imported"]["cases"][0]["title"] = "Подделка"
    target.write_text(json.dumps(forged, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        read_attempt_receipt(run["root"], run["attempt_id"], "imported-cases-" + batch_id, "ARTIFACT_READ_BACK")


def test_a_case_of_another_batch_in_the_answer_is_refused_by_the_skeleton_of_the_batch(run: dict) -> None:
    from tests.test_review_requested import _journal
    from tools.batch_assembly import with_digest

    assert prepare_stage(run["root"], _batch_stage(run))[0] == 0
    good = _manual_fragment(run["plan"], run["context"], run["imported"], 0)
    foreign = _manual_fragment(run["plan"], run["context"], run["imported"], 1)
    wrong = {key: value for key, value in good.items() if key != "digest"}
    extra = dict(foreign["test_cases"][0], case_id="TC-B1-002", display_order=2, requirement_ids=["CREQ-B1-001"],
                 steps=[dict(step, step_id=f"STEP-B1-002-{n}", expectations=[dict(item, expectation_id=f"EXP-B1-002-{n}-{k}") for k, item in enumerate(step["expectations"], 1)])
                        for n, step in enumerate(foreign["test_cases"][0]["steps"], 1)])
    wrong["test_cases"] = [*wrong["test_cases"], extra]
    before = _journal(run["root"])
    code, refused = submit_stage(run["root"], _batch_stage(run), with_digest(wrong))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases" in refused["message"], refused
    assert _journal(run["root"]) == before
    assert_refused_once(run["root"], before, refused["code"], refused.get("command"))  # J1: the one event the refusal itself added
    assert submit_stage(run["root"], _batch_stage(run), good, "--transport-attempts", "2")[0] == 0


def test_a_run_from_before_with_the_whole_skeleton_as_its_last_input_still_answers(run: dict) -> None:
    """A request published before FaT-SKB took the whole imported-cases as its last input: submit checks the
    answer by that whole receipt and records it."""
    from tests.helpers import generator_requests_before_dg

    from tools.pilot_state import publish_model_request

    with generator_requests_before_dg():  # the request as a run published it before FaT-SKB, in this process
        publish_model_request(run["root"], run["attempt_id"], _batch_stage(run), model_id="model-generator", invocation_id="invoke-old",
                              input_digests=[run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"], run["imported"]["digest"]])
    code, answered = submit_stage(run["root"], _batch_stage(run), _manual_fragment(run["plan"], run["context"], run["imported"], 0))
    assert code == 0, answered


def test_the_step_input_of_each_batch_reads_only_its_skeleton(run: dict) -> None:
    """e2e of the budget: three batches, each batch's step input holds its own skeleton, never the whole import."""
    from tools.pilot_state import _receipt_target
    from tools.step_size import request_files

    whole = _receipt_target(run["root"], run["attempt_id"], "imported-cases").stat().st_size
    sizes = []
    for index, batch in enumerate(run["plan"]["batches"]):
        stage = _batch_stage(run, index)
        code, prepared = prepare_stage(run["root"], stage)
        assert code == 0, prepared
        files = request_files(run["root"], stage)
        [row] = [row for row in files["files"] if row["digest"] == _skeleton(run, index)]
        assert Path(row["path"]) == _receipt_target(run["root"], run["attempt_id"], "imported-cases-" + batch["batch_id"]), files
        assert not files.get("unresolved"), files
        sizes.append(row["bytes"])
        assert submit_stage(run["root"], stage, _manual_fragment(run["plan"], run["context"], run["imported"], index))[0] == 0  # the next batch waits for it
    assert all(size < whole for size in sizes) and sum(sizes) < whole + 3 * 600, (sizes, whole)  # each batch: its share
