"""J1 (A1-5, A1-9): the decisions of the run are read from the journal, not from a file.

The mark «оставлен до ревью» (``left-before-review.json``) and the human's answer about the scope (``scope-decision.json``) are
written through ``bind_bytes`` — the file and the pair ARTIFACT_PUBLISHED / ARTIFACT_READ_BACK with a batch_id of the family
(``left-v1``, ``scope-decision-v1``).  A file without its events decides nothing; a cut command is finished by the same call; a file
of other bytes is RECEIPT_FILE_UNBOUND and nothing is written."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_cut_review import _crash_event
from tests.test_release_ways_out import _main
from tests.test_scope_pause import _api, _paused_run, _plan_arguments


def _events(root: Path, batch: str) -> list[tuple]:
    rows = [json.loads(line) for line in (root / "events.jsonl").read_bytes().decode("utf-8").splitlines()]
    return [(row["event_type"], row.get("attempt_id")) for row in rows if row.get("batch_id") == batch]


def _left_run(tmp_path: Path, monkeypatch):
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch)
    assert code == 0, first
    return project, first, Path(first["run_root"])


def _left_bytes(attempt_id: str) -> bytes:
    return json.dumps({"attempt_id": attempt_id, "reason": "LEFT_BEFORE_REVIEW"}, ensure_ascii=False, sort_keys=True).encode("utf-8")


# --- A1-5: left-before-review is an event -----------------------------------------------------------------------------------------

def test_the_mark_is_written_with_its_pair_of_events(tmp_path: Path, capsys, monkeypatch) -> None:
    from tools.pilot_state import LEFT_MARK, left_attempt

    project, first, root = _left_run(tmp_path, monkeypatch)
    code, left = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 0, left
    assert (root / LEFT_MARK).read_bytes() == _left_bytes(first["attempt_id"])
    assert _events(root, "left-v1") == [("ARTIFACT_PUBLISHED", first["attempt_id"]), ("ARTIFACT_READ_BACK", first["attempt_id"])]
    assert left_attempt(root) == first["attempt_id"]
    before = (root / "events.jsonl").read_bytes()
    code, again = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 0 and (root / "events.jsonl").read_bytes() == before, again  # the same call again writes nothing


def test_a_planted_mark_without_events_leaves_nothing_and_the_same_command_binds_it(tmp_path: Path, capsys, monkeypatch) -> None:
    from tools.pilot_state import LEFT_MARK, left_attempt
    from tools.run_pipeline import _left_mark

    project, first, root = _left_run(tmp_path, monkeypatch)
    (root / LEFT_MARK).write_bytes(_left_bytes(first["attempt_id"]))
    assert left_attempt(root) is None and _left_mark(root) is None  # the file is not the decision
    code, status = _main(capsys, ["status", "--project", str(project), "--run", first["run_id"]])
    assert status["state"] != "left", status
    code, left = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 0, left
    assert left_attempt(root) == first["attempt_id"]
    assert _events(root, "left-v1") == [("ARTIFACT_PUBLISHED", first["attempt_id"]), ("ARTIFACT_READ_BACK", first["attempt_id"])]


def test_a_mark_of_other_bytes_is_refused_with_its_code_and_nothing_is_written(tmp_path: Path, capsys, monkeypatch) -> None:
    from tools.pilot_state import LEFT_MARK

    project, first, root = _left_run(tmp_path, monkeypatch)
    (root / LEFT_MARK).write_bytes(b'{"attempt_id": "x", "reason": "something else"}')
    before = (root / "events.jsonl").read_bytes()
    code, refused = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code != 0 and refused.get("code") == "RECEIPT_FILE_UNBOUND" and "--new-run" in json.dumps(refused, ensure_ascii=False), refused
    assert (root / "events.jsonl").read_bytes().startswith(before) and not _events(root, "left-v1")


@pytest.mark.parametrize("cut", ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"])
def test_a_cut_between_the_mark_and_its_events_is_not_a_decision_and_the_repeat_finishes_it(tmp_path: Path, capsys, monkeypatch, cut: str) -> None:
    from tools.pilot_state import LEFT_MARK, left_attempt

    project, first, root = _left_run(tmp_path, monkeypatch)
    with monkeypatch.context() as broken:
        _crash_event(broken, cut, "left-v1")
        code, cut_answer = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
        assert code != 0, cut_answer
    assert (root / LEFT_MARK).is_file() and left_attempt(root) is None  # the file alone leaves nothing
    code, left = _main(capsys, ["finish", "--project", str(project), "--run", first["run_id"], "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 0, left
    assert left_attempt(root) == first["attempt_id"]
    assert _events(root, "left-v1") == [("ARTIFACT_PUBLISHED", first["attempt_id"]), ("ARTIFACT_READ_BACK", first["attempt_id"])]


# --- A1-9: the scope decision is an event -----------------------------------------------------------------------------------------

def _decision_bytes(excluded: list[str] | None = None, reason: str = "r") -> bytes:
    value = ({"schema_version": "1.0.0", "kind": "scope-decision", "decision": "exclude",
              "excluded": [{"source_requirement_id": item, "reason": reason} for item in sorted(excluded)]} if excluded
             else {"schema_version": "1.0.0", "kind": "scope-decision", "decision": "continue", "excluded": []})
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_the_decision_is_written_with_its_pair_of_events_and_read_from_the_journal(tmp_path: Path, capsys) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, answered = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
    assert code == 0, answered
    assert _events(root, "scope-decision-v1") == [("ARTIFACT_PUBLISHED", attempt_id), ("ARTIFACT_READ_BACK", attempt_id)]
    assert scope_pause.read_decision(root, attempt_id)["decision"] == "continue"


def test_a_planted_decision_of_the_right_shape_without_events_is_none_and_the_pause_stays(tmp_path: Path) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    target = root / "artifacts" / attempt_id / scope_pause.DECISION_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_decision_bytes())
    assert scope_pause.read_decision(root, attempt_id) is None
    state = scope_pause.scope_state(root, attempt_id)
    assert state["pending"] is True and state["decision"] is None
    assert "без событий журнала" in scope_pause.pending_message(root, attempt_id, state["gaps"])


def test_the_same_answer_after_a_planted_file_binds_it_and_another_is_refused(tmp_path: Path, capsys) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    target = root / "artifacts" / attempt_id / scope_pause.DECISION_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_decision_bytes(["SREQ-batch-a-001"], "planted"))
    before = (root / "events.jsonl").read_bytes()
    code, refused = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
    assert code == 2 and refused["code"] == "RECEIPT_FILE_UNBOUND", refused  # other bytes than the file: nothing written
    assert not _events(root, "scope-decision-v1") and (root / "events.jsonl").read_bytes().startswith(before)
    code, answered = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--exclude", "SREQ-batch-a-001", "--reason", "planted"))
    assert code == 0, answered  # the same bytes: the events are written
    assert _events(root, "scope-decision-v1") == [("ARTIFACT_PUBLISHED", attempt_id), ("ARTIFACT_READ_BACK", attempt_id)]
    assert scope_pause.read_decision(root, attempt_id)["decision"] == "exclude"


@pytest.mark.parametrize("cut", ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"])
def test_a_cut_between_the_decision_and_its_events_is_finished_by_the_repeat(tmp_path: Path, capsys, monkeypatch, cut: str) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    with monkeypatch.context() as broken:
        _crash_event(broken, cut, "scope-decision-v1")
        code, cut_answer = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
        assert code != 0, cut_answer
    assert scope_pause.read_decision(root, attempt_id) is None and scope_pause.scope_state(root, attempt_id)["pending"] is True
    code, answered = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
    assert code == 0, answered
    assert _events(root, "scope-decision-v1") == [("ARTIFACT_PUBLISHED", attempt_id), ("ARTIFACT_READ_BACK", attempt_id)]
    assert scope_pause.read_decision(root, attempt_id)["decision"] == "continue"


# --- A2-6: nothing is written before the input is checked -------------------------------------------------------------------------

def test_a_refused_input_of_plan_batches_leaves_no_decision_and_another_answer_still_goes(tmp_path: Path, capsys) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    argv = _plan_arguments(root, attempt_id, receipts, "--continue")
    (root / "tmp" / "req.json").write_text(json.dumps([{"not": "a requirement"}]), encoding="utf-8")  # the inputs of the plan are refused
    before = (root / "events.jsonl").read_bytes()
    code, refused = _api(capsys, argv)
    assert code == 2 and refused["status"] == "error", refused
    assert not (root / "artifacts" / attempt_id / scope_pause.DECISION_NAME).exists()  # the answer was not recorded
    assert not _events(root, "scope-decision-v1") and scope_pause.read_decision(root, attempt_id) is None
    assert [row for row in (root / "events.jsonl").read_bytes().replace(before, b"", 1).split(b"\n") if row and b"COMMAND_REFUSED" not in row] == []
    argv = _plan_arguments(root, attempt_id, receipts, "--exclude", "SREQ-batch-a-001", "--reason", "другое решение после отказа")  # rewrites req.json
    code, answered = _api(capsys, argv)
    assert code == 0, answered  # not SCOPE_DECISION_CONFLICT: the first answer never became a decision
    assert scope_pause.read_decision(root, attempt_id)["decision"] == "exclude"


def test_a_refused_input_of_plan_leaves_no_decision(tmp_path: Path, capsys) -> None:
    from tests.test_plan_command import _files, _plan_argv
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    files = _files(root, receipts, "sha256:" + "c" * 64)  # a header digest of the grouping that is not the header's: refused
    code, refused = _api(capsys, _plan_argv(root, attempt_id, files, "--continue"))
    assert code == 2 and refused["code"] == "GENERATOR_INPUT_HEADER", refused
    assert not (root / "artifacts" / attempt_id / scope_pause.DECISION_NAME).exists() and not _events(root, "scope-decision-v1")
    files = _files(root, receipts, None)
    code, answered = _api(capsys, _plan_argv(root, attempt_id, files, "--exclude", "SREQ-batch-a-001", "--reason", "другое решение после отказа"))
    assert code == 0, answered
    assert scope_pause.read_decision(root, attempt_id)["decision"] == "exclude"


def test_a_scan_refused_by_its_skillsrc_leaves_no_run(tmp_path: Path, monkeypatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _runs, _scan

    project = _python_project(tmp_path, runtime=False)
    (project / ".skillsrc").write_text("{ not json", encoding="utf-8")
    runs = (project / ".pilot-runs")
    before = _runs(project) if runs.is_dir() else []
    try:
        code, refused = _scan(project, monkeypatch)
    except Exception as error:  # the refusal may leave scan as an exception of the CLI layer; the run is what this test is about
        code, refused = 2, {"error": type(error).__name__}
    assert code != 0, refused
    assert (_runs(project) if runs.is_dir() else []) == before  # no orphan run without MODULE_SELECTED
