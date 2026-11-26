"""q5: the generator reads the files the marker named first — the plan names them (``context_files``), the bytes are those of the
context batches it takes today; a plan that has nothing to add keeps its bytes."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_context_batches import _generator_journal, _grouping, _number, _requirements, three  # noqa: F401 — the fixture
from tools import pilot_state

NAMED = ["src/part2.py:5 — наблюдение"]  # a file of the third context batch


def _narrow(monkeypatch: pytest.MonkeyPatch, project: Path, receipts: list[dict]) -> None:
    """A window where a second context batch would add a batch: the plan takes the first one only."""
    from tools import step_size
    from tools.batch_assembly import WINDOW_SHARE, plan_batches_for_project

    one = plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], receipts[0])
    step, answer = _number(one, r"вход шага (\d+) Б"), _number(one, r"оценка ответа (\d+) Б")
    monkeypatch.setattr(step_size, "step_window", lambda _project: int((step + answer + 10_000) / WINDOW_SHARE))


def _plan(found, sources: list[str]):
    from tools.batch_assembly import plan_batches_for_project

    project, receipts = found[0], found[4]
    return plan_batches_for_project(_requirements(), _grouping(receipts), project, [], receipts, None, None, sources)


def test_a_file_the_marker_named_in_the_third_batch_is_read(three, monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow(monkeypatch, three[0], three[4])
    plan = _plan(three, NAMED)
    assert plan["context_files"][0] == "src/part2.py"  # then the small files that fill the room
    assert plan["context_receipt_digests"] == [receipt["digest"] for receipt in three[4]]
    assert not any("контекст:" in note for note in plan.notes), plan.notes  # every file the marker named is read: no line


def test_a_file_the_marker_named_that_did_not_fit_is_named_in_the_plan(three, monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow(monkeypatch, three[0], three[4])
    plan = _plan(three, ["src/part1.py:1 — a", "src/part2.py:1 — b", "tests/test_part0.py:1 — c"])  # room for part1 and the small file
    line = next(note for note in plan.notes if note.startswith("контекст:"))
    assert line == ("контекст: генератору не хватило места для 1 из 3 файлов, нужных по разметке: src/part2.py; "
                    "по ним у генератора только пометки разметки"), plan.notes


def test_the_files_it_reads_are_no_more_bytes_than_the_batches_it_takes_today(three, monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow(monkeypatch, three[0], three[4])
    receipts, plan = three[4], _plan(three, NAMED)
    sizes = {row["project_path"]: row["size"] for receipt in receipts for row in receipt["files"]}
    assert sum(sizes[path] for path in plan["context_files"]) <= receipts[0]["byte_count"]


def test_a_plan_that_reads_every_file_says_context_skeleton_too(three) -> None:  # v0.8: every new plan, the skeleton empty
    plan = _plan(three, [])
    assert ("context_files" in plan, plan["context_skeleton"]) == (False, True)


def test_a_marker_that_names_nothing_leaves_the_plan_as_it_was(three, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.batch_assembly import canonical_bytes

    _narrow(monkeypatch, three[0], three[4])
    plan = _plan(three, ["nowhere.py:1 — x"])
    assert "context_files" not in plan and canonical_bytes(plan) == canonical_bytes(_plan(three, []))


def test_a_file_that_is_read_already_is_the_only_file_read_whole(three, monkeypatch: pytest.MonkeyPatch) -> None:  # v0.8: the rest goes as its skeleton
    _narrow(monkeypatch, three[0], three[4])
    assert _plan(three, ["src/part0.py:5 — x"])["context_files"] == ["src/part0.py"]


def test_the_plan_has_no_more_batches_than_without_the_marker(three, monkeypatch: pytest.MonkeyPatch) -> None:
    _narrow(monkeypatch, three[0], three[4])
    assert len(_plan(three, NAMED)["batches"]) <= len(_plan(three, [])["batches"])


def test_a_document_a_requirement_cites_is_read_first_whatever_the_tail_of_the_reference(three, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.batch_assembly import plan_batches_for_project

    project, receipts = three[0], three[4]
    _narrow(monkeypatch, project, receipts)
    requirements = [{**row, "provenance": ["src/part2.py:12 — см.: что-то"]} for row in _requirements()]
    plan = plan_batches_for_project(requirements, _grouping(receipts), project, [], receipts, None, None, ["src/part1.py:1 — x"])
    assert plan["context_files"][0] == "src/part2.py" and "src/part1.py" not in plan["context_files"]  # the cited document first


def test_the_api_contract_is_read_first_though_the_marker_did_not_name_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_marker_parts import _run

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "openapi.yml").write_text("openapi: 3.0.1\ninfo: {title: x}\n", encoding="utf-8")
    root, attempt_id, baseline, receipts = _run(tmp_path, files=3)
    _narrow(monkeypatch, tmp_path, receipts)
    assert _plan((tmp_path, root, attempt_id, baseline, receipts), NAMED)["context_files"][:2] == ["src/openapi.yml", "src/part2.py"]


def test_a_file_changed_after_the_scan_is_refused_as_before(three) -> None:
    project, root, attempt_id, *_rest = three
    (project / "src" / "part2.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CONTEXT_DRIFTED"):
        pilot_state.context_receipts(root, attempt_id, pilot_state.derive_state(root))


def test_a_plan_that_names_a_file_no_context_receipt_holds_is_refused(three) -> None:
    with pytest.raises(ValueError, match="GENERATOR_INPUT_CONTEXT"):
        _generator_journal(three, three[4], context_files=["src/nowhere.py"])


def test_the_generator_reads_only_the_files_its_plan_names(three) -> None:
    from tools.step_size import request_files

    receipts = three[4]
    root, attempt_id, marker, plan, header = _generator_journal(three, receipts, context_files=["src/part2.py"])
    contexts = [receipt["digest"] for receipt in receipts]
    pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="g", invocation_id="gen",
                                      input_digests=[marker, *contexts, plan, header])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert [Path(row["path"]).name for row in found["files"] if row["digest"] in contexts and not row.get("basis")] == ["part2.py"], found


def test_the_reviewer_view_has_no_batch_line_when_the_plan_names_its_files(three) -> None:
    from tools.review_view import run_context_note

    root, attempt_id, *_rest = _generator_journal(three, three[4], context_files=["src/part2.py"])
    assert run_context_note(root, attempt_id) is None
