"""upd-a: ``scan --cases`` with ``--docs`` — a document requirement no case of the CSV links stays a requirement; the generator
writes new cases for it beside the imported ones (Key empty), the imported cases are checked as before."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import prepare_stage, submit_stage
from tests.test_cases_import_docs import LINKS, _changed, _fragment, _to_plan

NEW = 2  # the index of the new case in the fragment: after the two imported ones


def _receipt(run: dict) -> dict:
    from tools.pilot_state import read_attempt_receipt

    [batch] = run["plan"]["batches"]
    return read_attempt_receipt(run["root"], run["attempt_id"], "imported-cases-" + batch["batch_id"], "ARTIFACT_READ_BACK")["record"]["imported"]


def _submit(run: dict, value: dict) -> tuple[int, dict]:
    [batch] = run["plan"]["batches"]
    stage = f"tc-generator:{batch['batch_id']}"
    code, prepared = prepare_stage(run["root"], stage)
    assert code == 0, prepared
    return submit_stage(run["root"], stage, value)


def test_the_skeleton_of_the_batch_names_the_requirements_to_write_cases_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    assert _receipt(run)["write"] == ["SREQ-0002"] and len(_receipt(run)["cases"]) == 2


def test_new_cases_beside_the_imported_ones_are_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    code, answered = _submit(run, _fragment(run))
    assert code == 0, answered.get("message")


def test_a_requirement_without_a_case_left_without_a_new_case_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)

    def drop(value: dict) -> None:
        del value["test_cases"][NEW]

    code, refused = _submit(run, _changed(_fragment(run), drop))
    assert code == 2 and refused["code"] == "BATCH_UNCOVERED_REQUIREMENT", refused


def test_a_new_case_with_a_zephyr_key_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)

    def key(value: dict) -> None:
        value["test_cases"][NEW]["management"]["external_keys"] = {"zephyr_scale": "TC-1"}

    code, refused = _submit(run, _changed(_fragment(run), key))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and f"/test_cases/{NEW}/management" in refused["message"], refused


def test_a_case_traced_to_a_new_and_to_an_imported_requirement_counts_as_imported_and_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)

    def mixed(value: dict) -> None:
        first = value["test_cases"][0]["requirement_ids"][0]
        value["test_cases"][NEW]["requirement_ids"].insert(0, first)  # in the order of the requirements, as the canon wants

    code, refused = _submit(run, _changed(_fragment(run), mixed))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases:" in refused["message"], refused


def test_a_new_case_traced_only_to_an_imported_requirement_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)

    def stolen(value: dict) -> None:
        value["test_cases"][NEW]["requirement_ids"] = [value["test_cases"][0]["requirement_ids"][0]]

    code, refused = _submit(run, _changed(_fragment(run), stolen))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases: кейсов 3" in refused["message"], refused


def test_the_size_estimate_counts_a_requirement_without_a_case_by_its_text() -> None:
    from tools.batch_assembly import _size_split

    text = "т" * 100  # 200 bytes
    source = [{"source_requirement_id": "SREQ-1", "display_order": 1, "text": text}]
    imported = {"cases": [], "gaps": [{"source_requirement_id": "SREQ-1", "text": text}]}
    _groups, notes = _size_split(source, 1_000_000, 0, None, {}, imported)
    assert "оценка ответа 8400 Б" in notes[0], notes


@pytest.fixture
def every_requirement_has_a_case():
    saved = list(LINKS)
    LINKS[:] = [saved[0], {"row": 3, "source_requirement_ids": ["SREQ-0002"]}]  # the default links of the helpers of test_cases_import_docs
    yield
    LINKS[:] = saved


def test_a_run_without_a_requirement_lacking_a_case_keeps_its_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, every_requirement_has_a_case) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    assert run["marker"] == BASE_MARKER and run["imported"]["digest"] == BASE_IMPORTED
    assert "write" not in _receipt(run) and run["imported"]["gaps"] == []


# the digests of the merged marker and of the skeleton of this run on the base 9c7a0f78 (before upd-a)
BASE_MARKER = "sha256:17a9c0fbea13f035b99b7b8b71fc46c1c8de35710e2b5407c749c07e73848b78"
BASE_IMPORTED = "sha256:2aefa82e7ca59654409bb43caaa889e5d3472e0f650606b8363232ab7fcdeddb"
