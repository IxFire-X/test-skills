"""r2in: the second revision of the automation reads the files the reviewer of r1 read (code, tables, the view of the cases in parts) and the review of r1."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import prepare_stage
from tests.test_autotest_cycle import REVIEWER, autotest_run, run_generator, run_reviewer
from tools import pilot_state, step_size

SECOND = "tc-to-autotest:r2"
OF_THE_REVIEWER = ("код", "таблицы", "вид кейсов")


def _second(tmp_path: Path) -> dict:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "ТРЕБУЕТ ДОРАБОТКИ")
    return run


def test_the_second_revision_reads_code_tables_view_in_parts_and_the_review(tmp_path: Path) -> None:
    run = _second(tmp_path)
    code, out = prepare_stage(run["root"], SECOND)
    by_basis = out["result"]["read"]["by_basis"]
    assert code == 0 and {"код", "таблицы", "вид кейсов", "ревью r1"} <= set(by_basis) and not {"r1", "вид"} & set(by_basis), by_basis


def test_its_files_are_those_the_reviewer_of_r1_read(tmp_path: Path) -> None:
    run = _second(tmp_path)
    prepare_stage(run["root"], SECOND)

    def rows(stage: str) -> list[tuple[str, int]]:
        return [(row["path"], row["bytes"]) for row in step_size.stage_inputs(run["root"], stage)["files"] if row.get("basis") in OF_THE_REVIEWER]

    assert rows(SECOND) == rows(REVIEWER) != []


def test_without_the_files_the_second_revision_reads_what_it_read_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _second(tmp_path)
    monkeypatch.setattr(step_size, "automation_text_rows", lambda *args: None)
    code, out = prepare_stage(run["root"], SECOND)
    by_basis = out["result"]["read"]["by_basis"]
    assert code == 0 and {"r1", "вид", "ревью r1"} <= set(by_basis) and not set(OF_THE_REVIEWER) & set(by_basis), by_basis


def test_without_the_effective_document_the_second_revision_reads_what_it_read_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _second(tmp_path)
    prepare_stage(run["root"], SECOND)
    monkeypatch.setattr(pilot_state, "read_effective_canonical_if_present", lambda *args: None)
    by_basis = {row["basis"] for row in step_size.stage_inputs(run["root"], SECOND)["files"]}
    assert {"r1", "вид", "ревью r1"} <= by_basis and not set(OF_THE_REVIEWER) & by_basis, by_basis


def test_the_request_of_the_second_revision_does_not_depend_on_the_form_of_its_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _second(tmp_path)
    prepare_stage(run["root"], SECOND)
    files = step_size.stage_inputs(run["root"], SECOND)
    monkeypatch.setattr(step_size, "automation_text_rows", lambda *args: None)
    before = step_size.stage_inputs(run["root"], SECOND)
    request = pilot_state.read_model_request(run["root"], run["attempt"], SECOND, files["request_digest"])
    assert files["request_digest"] == before["request_digest"] and request["input_digests"] and files["files"] != before["files"]
