"""upd-a: ``scan --cases`` with ``--docs`` — a document requirement no case of the CSV links stays a requirement; the generator
writes new cases for it beside the imported ones (Key empty), the imported cases are checked as before."""
from __future__ import annotations

import json
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


def test_a_refusal_points_at_the_array_of_the_fragment_when_a_new_case_stands_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)

    def first(value: dict) -> None:
        cases = value["test_cases"]
        cases.insert(0, cases.pop(NEW))  # the array: new, imported, imported
        for number, case in enumerate(cases, start=1):
            case["display_order"] = number
        cases[2]["title"] += "!"

    code, refused = _submit(run, _changed(_fragment(run), first))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/2/title" in refused["message"], refused


def test_a_requirement_the_human_left_out_on_the_pause_gets_no_new_cases_in_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_report import write_report

    run = _to_plan(tmp_path, monkeypatch, exclude=("SREQ-0002",))
    write_report(run["project"], run["root"].name)
    report = json.loads((run["root"] / "report.json").read_text(encoding="utf-8"))
    assert report["import"]["gaps"] == [{"source_requirement_id": "SREQ-0002"}] and report["import"]["new"] == []
    assert "новые кейсы под них пишет генератор): 0" in (run["root"] / "report.md").read_text(encoding="utf-8")
    assert "генератор пишет под него" not in (run["root"] / "questions-for-analyst.md").read_text(encoding="utf-8")


def test_the_review_view_names_no_new_cases_for_a_requirement_left_out_on_the_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import derive_state
    from tools.review_view import _import_gaps

    run = _to_plan(tmp_path, monkeypatch, exclude=("SREQ-0002",))
    assert _import_gaps(run["root"], derive_state(run["root"]))[0] == []


def _rework(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], number: int, links=LINKS) -> tuple[int, dict]:
    """The review sends case ``number`` (1-based) back; the generator answers the rework with a new title: ``submit`` of ``tc-generator:rework-1``."""
    import copy

    from tests.test_review_canaries import _answer, _boundary_and_removed, _candidate, _finding, _open, _select
    from tests.test_rework_stage import STAGE
    from tests.test_stage_steps import _call, _put
    from tools import orchestrate_test_case_revision as orchestrate

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    run = _to_plan(tmp_path, monkeypatch, links=links)
    run["run_root"] = run["root"]
    assert _submit(run, _fragment(run))[0] == 0
    _open(run, monkeypatch)
    target = _candidate(run)["test_cases"][number - 1]
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [target["case_id"]], "Нет проверки состояния.")]
    assert _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))["status"] == "REWORK_PENDING"
    assert _call(capsys, "prepare", run["root"], STAGE)[0] == 0
    _put(run["root"], STAGE, {"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1",
                              "test_cases": [{**copy.deepcopy(target), "title": "Доработанный кейс"}]})
    return _call(capsys, "submit", run["root"], STAGE)


def test_a_rework_may_not_rewrite_the_human_title_of_an_imported_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, refused = _rework(tmp_path, monkeypatch, capsys, 1)
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/0/title" in refused["message"], refused


def test_a_rework_may_rewrite_the_text_of_a_new_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, answered = _rework(tmp_path, monkeypatch, capsys, NEW + 1)
    assert code == 0, answered


def test_the_review_view_names_the_new_cases_by_their_ids() -> None:
    from tests.test_requirement_traceability import canonical_fixture
    from tools.review_view import render

    text = render(canonical_fixture(), imported=True, import_gaps=(["SREQ-batch-a-001", "SREQ-batch-a-002"], [])).decode("utf-8")
    assert "новые кейсы (TC-batch-a-001) написал генератор, а не человек — их текст оценивай и правь как обычно" in text


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
    LINKS[:] = [saved[0], {"row": 3, "source_requirement_ids": ["SREQ-0002"], "verdict": "fits"}]  # the default links of the helpers of test_cases_import_docs
    yield
    LINKS[:] = saved


def test_a_run_without_a_requirement_lacking_a_case_keeps_its_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, every_requirement_has_a_case) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    assert run["marker"] == BASE_MARKER and run["imported"]["digest"] == BASE_IMPORTED
    assert "write" not in _receipt(run) and run["imported"]["gaps"] == []


# the digests of the merged marker and of the skeleton of this run (before upd-a: 17a9c0fb…, 2aefa82e…; upd-b2: every row of a plain document carries its verdict)
BASE_MARKER = "sha256:d02d57a1c43c786005c64ee4fbd043ef988ececba9759395a4f65c212ea9eb99"
BASE_IMPORTED = "sha256:e8c13a359655b28d931c4a459bcf3c67769981072c68528b7472999f8c60ff00"


REVIEW = "tc-reviewer:canonical"


def _autofix(run: dict, number: int, before_submit=None) -> tuple[int, dict]:
    """The generator answers the batch, the reviewer fixes the title of case ``number`` (1-based, as the generator numbers): ``submit`` of the review."""
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import journal_assembly
    from tools.review_edits import reviewer_successor

    assert _submit(run, _fragment(run))[0] == 0
    code, prepared = prepare_stage(run["root"], REVIEW, "--invocation-id", "reviewer-1", "--fresh-context", "yes")
    assert code == 0, prepared
    if before_submit is not None:
        before_submit()
    candidate = journal_assembly(run["root"], run["attempt_id"])[0]
    case = candidate["test_cases"][number - 1]
    selected = {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": document_sha256(candidate)}
    edits = [{"correction_id": "FIX-1", "case_id": case["case_id"], "field": "title", "old": case["title"], "new": case["title"] + "."}]
    report = {"verdict": "AUTO_FIX_APPLIED", "candidate": selected, "reviewed_case_ids": [item["case_id"] for item in candidate["test_cases"]],
              "corrections": [{"id": "FIX-1", "correction_kind": "MECHANICAL", "related_ids": [case["case_id"]], "description": "опечатка в названии", "evidence": ["REQ"]}],
              "findings": []}
    successor = reviewer_successor(candidate, {"validation_report": report, "successor_edits": edits})
    report["effective"] = {"document_id": successor["document_id"], "revision": successor["revision"], "document_sha256": document_sha256(successor)}
    return submit_stage(run["root"], REVIEW, {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": {"validation_report": report, "successor_edits": edits}, "warnings": []})


def test_a_review_fix_of_the_text_of_a_new_case_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, answered = _autofix(_to_plan(tmp_path, monkeypatch), NEW + 1)
    assert code == 0, answered


def test_a_review_fix_of_an_import_is_refused_when_the_receipt_of_the_import_is_lost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    code, refused = _autofix(run, 1, lambda: (run["root"] / "imported-cases" / f"{run['attempt_id']}.json").unlink())
    assert code == 2 and "MODEL_INPUTS_MISMATCH" in str(refused), refused  # was: the check of the human fields went off and the fix of an imported case was accepted


def test_a_review_fix_of_the_text_of_an_imported_case_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, refused = _autofix(_to_plan(tmp_path, monkeypatch), 1)
    assert code == 2 and "IMPORT_HUMAN_FIELD_CHANGED" in refused["message"], refused
