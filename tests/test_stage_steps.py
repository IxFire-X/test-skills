"""V2: ``prepare`` and ``submit`` — the cycle of the agent over the marker: the request from the journal, the answer from the slot of the stage."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_marker_parts import _marker, _requirements, _run
from tools import api, pilot_state, run_pipeline, stage_steps


def _call(capsys: pytest.CaptureFixture[str], command: str, root: Path, stage: str, *extra: str) -> tuple[int, dict]:
    code = api.main([command, "--run-root", str(root), "--stage", stage, *extra])
    return code, json.loads(capsys.readouterr().out)


def _put(root: Path, stage: str, value: object) -> None:
    slot = stage_steps.slot(root, stage)
    slot.parent.mkdir(exist_ok=True)
    slot.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _events(root: Path) -> list[str]:
    """The facts of the run; the line a refusal leaves (``COMMAND_REFUSED``) is not a step."""
    return [f"{row['event_type']}:{row.get('stage_instance_id')}" for row in pilot_state.derive_state(root)["events"] if row["event_type"] != "COMMAND_REFUSED"]


def _status(root: Path) -> dict:
    return run_pipeline._durable_status(root.parents[1], root, stage="status")


BASELINE = "context-marker:baseline"
PART = "context-marker:batch-001"
REQUIREMENTS = "context-marker:requirements"


def _requirements_step(capsys: pytest.CaptureFixture[str], root: Path) -> None:
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    assert _call(capsys, "prepare", root, REQUIREMENTS)[0] == 0
    _put(root, REQUIREMENTS, _marker([REQUIREMENTS_PLACEHOLDER], ["док — требование без ожидаемого результата"]))
    assert _call(capsys, "submit", root, REQUIREMENTS)[0] == 0


def test_prepare_publishes_the_request_and_names_the_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt, _baseline, _receipts = _run(tmp_path, files=1)
    code, out = _call(capsys, "prepare", root, BASELINE)
    assert code == 0 and out["result"]["answer_slot"] == str(stage_steps.slot(root, BASELINE))
    assert out["result"]["read"]["count"] > 0 and out["result"]["budget"]["fits"]
    assert _events(root)[-1] == f"MODEL_REQUESTED:{BASELINE}" and (root / "incoming").is_dir()


def test_a_repeated_prepare_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    _code, first = _call(capsys, "prepare", root, BASELINE)
    before = _events(root)
    code, again = _call(capsys, "prepare", root, BASELINE)
    assert code == 0 and _events(root) == before and again["result"]["request_digest"] == first["result"]["request_digest"]


def test_submit_records_the_answer_of_the_slot_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    _call(capsys, "prepare", root, BASELINE)
    _put(root, BASELINE, _marker(["src/part0.py — x"]))
    code, out = _call(capsys, "submit", root, BASELINE)
    assert code == 0 and _events(root)[-1] == f"MODEL_RESPONSE_RECEIVED:{BASELINE}" and "artifact" not in out["result"]
    before = _events(root)
    code, again = _call(capsys, "submit", root, BASELINE)
    assert code == 0 and again["result"]["answered"] is True and _events(root) == before


def test_submit_before_prepare_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    code, out = _call(capsys, "submit", root, BASELINE)
    assert code == 2 and out["code"] == "STAGE_NOT_PREPARED" and "prepare" in out["fix"]


def test_an_answer_in_the_slot_of_another_stage_or_run_is_not_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path)
    (tmp_path / "second").mkdir()
    other, *_ = _run(tmp_path / "second")
    _call(capsys, "prepare", root, REQUIREMENTS)
    _put(root, PART, _marker(["src/part0.py — x"]))  # the slot of a part, not of the requirements
    _put(other, REQUIREMENTS, _marker(["src/part0.py — x"]))  # the slot of the same stage in another run
    before = _events(root)
    code, out = _call(capsys, "submit", root, REQUIREMENTS)
    assert code == 2 and out["code"] == "ANSWER_FILE_UNREADABLE" and _events(root) == before


def test_a_slot_that_is_a_link_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    _call(capsys, "prepare", root, BASELINE)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(_marker(["src/part0.py — x"])), encoding="utf-8")
    try:
        stage_steps.slot(root, BASELINE).symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")
    before = _events(root)
    code, out = _call(capsys, "submit", root, BASELINE)
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _events(root) == before


def _junction(link: Path, target: Path) -> None:
    """``link`` leads to ``target``: a junction on Windows (no rights needed), a symbolic link elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_an_incoming_directory_that_is_a_junction_gives_no_answer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    _call(capsys, "prepare", root, BASELINE)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "context-marker-baseline.json").write_text(json.dumps(_marker(["src/part0.py — x"])), encoding="utf-8")
    (root / "incoming").rmdir()
    _junction(root / "incoming", outside)
    before = _events(root)
    code, out = _call(capsys, "submit", root, BASELINE)
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _events(root) == before


def test_prepare_does_not_take_a_junction_for_its_incoming_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(root / "incoming", outside)
    before = _events(root)
    code, out = _call(capsys, "prepare", root, BASELINE)
    assert code == 2 and out["code"] == "STAGE_INBOX_UNSAFE" and _events(root) == before and list(outside.iterdir()) == []


def test_a_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    _call(capsys, "prepare", root, BASELINE)
    _put(root, BASELINE, _marker(["src/part0.py — x"]))
    tree = {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    code, out = _call(capsys, "submit", root, BASELINE, "--dry-run")
    assert code == 0 and out["dry_run"] is True
    assert {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()} == tree


def test_the_last_part_merges_the_marker_and_only_the_last(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.context_marker_merge import merge_marker_artifacts

    root, attempt_id, _baseline, receipts = _run(tmp_path)
    _requirements_step(capsys, root)
    parts = [_marker([f"src/part{index}.py — наблюдение {index}"]) for index in range(len(receipts))]
    for index, value in enumerate(parts, start=1):
        stage = f"context-marker:batch-{index:03d}"
        assert _call(capsys, "prepare", root, stage)[0] == 0
        _put(root, stage, value)
        before = len(_events(root))
        code, out = _call(capsys, "submit", root, stage)
        assert code == 0 and ("merged" in out["result"]) == (index == len(parts))
        if index < len(parts):
            assert len(_events(root)) == before + 1 and f"MODEL_RESPONSE_RECEIVED:{BASELINE}" not in _events(root)
    merged = pilot_state.read_model_stage_artifact(root, attempt_id, BASELINE, out["result"]["merged"]["content_digest"])["artifact"]
    answer = _marker(["шаг требований — код не анализировался"], ["док — требование без ожидаемого результата"])
    assert merged == merge_marker_artifacts(answer, parts) and merged["artifacts"]["analytics_documentation"]["requirements"] == _requirements()


def test_status_names_each_step_with_its_stage_slot_and_size(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path)
    first = _status(root)
    assert (first["state"], first["waiting_for"], first["next"]) == ("marker", "agent", {"verb": "prepare", "stage": REQUIREMENTS})
    assert first["answer_slot"] == str(stage_steps.slot(root, REQUIREMENTS)) and first["budget"]["fits"]
    _call(capsys, "prepare", root, REQUIREMENTS)
    asked = _status(root)
    assert (asked["waiting_for"], asked["next"]) == ("model", {"verb": "submit", "stage": REQUIREMENTS})
    _put(root, REQUIREMENTS, _marker(["src/part0.py — x"]))
    _call(capsys, "submit", root, REQUIREMENTS)
    assert _status(root)["next"] == {"verb": "prepare", "stage": PART}


def test_a_stage_the_table_does_not_know_is_refused_with_the_way(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    code, out = _call(capsys, "prepare", root, "tc-reviewer:canonical")
    assert code == 2 and out["code"] == "STAGE_UNKNOWN" and "next.verb" in out["fix"]


def test_a_batch_waits_for_the_requirements(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path)
    code, out = _call(capsys, "prepare", root, PART)
    assert code == 2 and out["code"] == "STAGE_WAITS" and _events(root)[-1].startswith("CONTEXT_SELECTED")


def test_the_calls_start_no_process_and_no_model() -> None:
    tree = ast.parse(Path(stage_steps.__file__).read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not names & {"subprocess", "os", "multiprocessing", "socket", "urllib", "tools.exec_runner", "tools.run_tests"}


def test_a_reuse_is_answered_from_the_source_run_and_never_from_the_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_marker_reuse import _fresh, _marker as source_marker, _source

    source_root, source_attempt = _source(tmp_path)
    _marker, digest = source_marker(source_root, source_attempt)
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    code, out = _call(capsys, "prepare", run_root, BASELINE, "--reuse-from", source_root.name)
    assert code == 0 and out["result"]["model_call"] is False and out["result"]["reused_from"] == source_root.name
    _put(run_root, BASELINE, {"changed": "after prepare"})  # the agent's file is no source of a reused answer
    code, again = _call(capsys, "submit", run_root, BASELINE)
    [answer] = [row for row in pilot_state.derive_state(run_root)["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    assert code == 0 and again["result"]["answered"] is True and answer["artifact_digest"] == digest


# ---- the pause after the marker: the human's answer in incoming/scope.json, taken by `submit --stage scope` ----

SCOPE = "scope"
EXCLUDE = {"decision": "exclude", "excluded": ["SREQ-batch-a-001"], "reason": "нет стенда"}
CONTINUE = {"decision": "continue"}


def _paused(tmp_path: Path) -> tuple[Path, str]:
    from tests.test_scope_pause import _paused_run

    root, attempt_id, *_ = _paused_run(tmp_path)
    return root, attempt_id


def _decision(root: Path, attempt_id: str) -> dict | None:
    from tools import scope_pause

    return scope_pause.read_decision(root, attempt_id)


def test_prepare_scope_prints_the_gaps_the_slot_and_the_two_forms_and_writes_the_draft(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt = _paused(tmp_path)
    code, out = _call(capsys, "prepare", root, SCOPE)
    result = out["result"]
    assert code == 0 and result["answer_slot"] == str(stage_steps.slot(root, SCOPE)) and (root / "incoming").is_dir()
    assert result["scope_gaps"][0]["source_requirement_ids"] == ["SREQ-batch-a-001"] and result["questions_draft"] == str(root / "questions-for-analyst.md")
    assert '"exclude"' in result["answers"] and '"continue"' in result["answers"] and (root / "questions-for-analyst.md").is_file()


def test_a_repeated_prepare_scope_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt = _paused(tmp_path)
    _code, first = _call(capsys, "prepare", root, SCOPE)
    before = (_events(root), (root / "questions-for-analyst.md").read_bytes())
    code, again = _call(capsys, "prepare", root, SCOPE)
    assert code == 0 and again == first and (_events(root), (root / "questions-for-analyst.md").read_bytes()) == before


def test_submit_scope_records_an_exclusion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, EXCLUDE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and out["result"]["answered"] is True
    assert _decision(root, attempt_id)["excluded"] == [{"source_requirement_id": "SREQ-batch-a-001", "reason": "нет стенда"}]


def test_submit_scope_records_a_continue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, CONTINUE)
    code, _out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and _decision(root, attempt_id)["decision"] == "continue"


def test_submit_scope_without_a_file_records_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "ANSWER_FILE_UNREADABLE" and _decision(root, attempt_id) is None


@pytest.mark.parametrize("answer", [{**CONTINUE, "reason": "x"}, {**EXCLUDE, "extra": 1}, {**EXCLUDE, "excluded": []},
                                    {**EXCLUDE, "excluded": "SREQ-batch-a-001"}, {"decision": "exclude", "excluded": ["SREQ-batch-a-001"]}, ["continue"]])
def test_submit_scope_refuses_a_wrong_form_with_both_forms_in_the_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str], answer: object) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, answer)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "SCOPE_ANSWER_INVALID" and '"exclude"' in out["fix"] and '"continue"' in out["fix"]
    assert _decision(root, attempt_id) is None


def test_submit_scope_refuses_a_slot_that_is_a_link(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(CONTINUE), encoding="utf-8")
    try:
        stage_steps.slot(root, SCOPE).symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _decision(root, attempt_id) is None


def test_a_dry_run_of_submit_scope_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, EXCLUDE)
    tree = {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    code, out = _call(capsys, "submit", root, SCOPE, "--dry-run")
    assert code == 0 and out["result"]["dry_run"] is True and _decision(root, attempt_id) is None
    assert {path.relative_to(root): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()} == tree


def test_the_same_answer_again_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, EXCLUDE)
    _call(capsys, "submit", root, SCOPE)
    before = _events(root)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and out["result"]["answered"] is True and _events(root) == before


def test_another_answer_after_the_recorded_one_is_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, EXCLUDE)
    _call(capsys, "submit", root, SCOPE)
    _put(root, SCOPE, CONTINUE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "SCOPE_DECISION_CONFLICT" and _decision(root, attempt_id)["decision"] == "exclude"


def test_the_stage_scope_waits_for_the_marker(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    for command in ("prepare", "submit"):
        assert _call(capsys, command, root, SCOPE)[1]["code"] == "STAGE_UNKNOWN"


def test_prepare_scope_without_gaps_has_no_draft_and_names_the_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _paused_run

    root, *_ = _paused_run(tmp_path, warnings=[])
    code, out = _call(capsys, "prepare", root, SCOPE)
    assert code == 0 and "questions_draft" not in out["result"] and out["result"]["answer_slot"] == str(stage_steps.slot(root, SCOPE))
    assert not (root / "questions-for-analyst.md").exists()


def test_a_requirement_is_excluded_on_a_marker_without_gaps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _paused_run

    root, attempt_id, *_ = _paused_run(tmp_path, warnings=[])
    _put(root, SCOPE, EXCLUDE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and _decision(root, attempt_id)["decision"] == "exclude"


def test_a_requirement_is_excluded_on_a_run_without_pause(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import scope_pause

    root, attempt_id = _paused(tmp_path)
    monkeypatch.setattr(scope_pause, "_no_pause", lambda _root: True)
    _put(root, SCOPE, EXCLUDE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and _decision(root, attempt_id)["decision"] == "exclude"


def test_no_decision_is_taken_after_the_plan_is_published(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _marker_rows, _paused_run
    from tools.batch_assembly import plan_batches

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path, warnings=[])
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})["digest"]
    plan = dict(plan_batches(_marker_rows(), {"header_digest": header, "context_receipt_digest": receipts[0]["digest"]}))
    pilot_state.publish_generator_input(root, attempt_id, "plan", plan)
    _put(root, SCOPE, EXCLUDE)
    before = _events(root)
    for command in ("submit", "prepare"):
        code, out = _call(capsys, command, root, SCOPE)
        assert code == 2 and out["code"] == "SCOPE_DECISION_CONFLICT" and "scan --new-run" in out["fix"]
    assert _events(root) == before and _decision(root, attempt_id) is None


def test_the_same_answer_after_the_plan_is_published_is_answered_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _published_plan

    root, _attempt, _plan = _published_plan(tmp_path, excluded=["SREQ-batch-a-001"], reason="нет стенда", proceed=False)
    _put(root, SCOPE, EXCLUDE)
    before = _events(root)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and out["result"]["answered"] is True and out["result"]["decision"] == "exclude" and _events(root) == before


def test_another_answer_after_the_plan_is_published_is_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _published_plan

    root, attempt_id, _plan = _published_plan(tmp_path, excluded=["SREQ-batch-a-001"], reason="нет стенда", proceed=False)
    _put(root, SCOPE, CONTINUE)
    before = _events(root)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "SCOPE_DECISION_CONFLICT" and _events(root) == before and _decision(root, attempt_id)["decision"] == "exclude"


def test_status_follows_the_pause_before_prepare_with_the_file_and_after_submit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt = _paused(tmp_path)
    paused = _status(root)
    assert (paused["state"], paused["waiting_for"], paused["next"]) == ("scope_pause", "human", {"verb": "prepare", "stage": SCOPE}), paused
    assert paused["answer_slot"] == str(stage_steps.slot(root, SCOPE)) and paused["scope_gaps"]
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, CONTINUE)
    assert _status(root)["next"] == {"verb": "submit", "stage": SCOPE}
    _call(capsys, "submit", root, SCOPE)
    answered = _status(root)
    assert answered["state"] == "generator" and "scope_gaps" not in answered


def test_a_recorded_decision_stands_without_the_file_and_is_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, EXCLUDE)
    _call(capsys, "submit", root, SCOPE)
    stage_steps.slot(root, SCOPE).unlink()
    before = _events(root)
    shown = {"stage": SCOPE, "answered": True, "decision": "exclude", "excluded": [{"source_requirement_id": "SREQ-batch-a-001", "reason": "нет стенда"}]}
    for command in ("submit", "prepare"):
        code, out = _call(capsys, command, root, SCOPE)
        assert code == 0 and out["result"] == shown and _events(root) == before


def test_the_same_requirement_twice_does_not_exclude_them_all(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _paused(tmp_path)
    _call(capsys, "prepare", root, SCOPE)
    _put(root, SCOPE, {**EXCLUDE, "excluded": ["SREQ-batch-a-001", "SREQ-batch-a-001"]})
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 0 and [row["source_requirement_id"] for row in _decision(root, attempt_id)["excluded"]] == ["SREQ-batch-a-001"]


def test_a_decision_checked_without_a_marker_is_a_refusal_not_an_exception(tmp_path: Path) -> None:
    from tools import scope_pause

    root, attempt_id, *_ = _run(tmp_path, files=1)
    with pytest.raises(scope_pause.ScopeRefused, match="SCOPE_NO_MARKER"):
        scope_pause.prepare_decision(root, attempt_id, CONTINUE)
# ---- the batches of the generator: ``prepare`` / ``submit`` over the plan the journal holds -------------------------------------------

def _generator(tmp_path: Path) -> dict:
    """A run with the context, the marker, the published header and plan (two batches) and a fragment for each; no request of the generator yet."""
    from tests.test_review_requested import _generated_run

    run = _generated_run(tmp_path, generated=False)
    header = json.loads(Path(run["header"]).read_bytes())
    header.pop("digest", None)
    pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "header", header)
    pilot_state.publish_generator_input(run["run_root"], run["attempt_id"], "plan", json.loads(Path(run["plan"]).read_bytes()))
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    return {**run, "root": run["run_root"], "stages": [f"tc-generator:{item['batch_id']}" for item in fragments], "values": fragments}


def _answer(capsys: pytest.CaptureFixture[str], run: dict, index: int, *extra: str) -> tuple[int, dict]:
    """``prepare`` the batch, put its fragment in the slot, ``submit``."""
    assert _call(capsys, "prepare", run["root"], run["stages"][index])[0] == 0
    _put(run["root"], run["stages"][index], run["values"][index])
    return _call(capsys, "submit", run["root"], run["stages"][index], *extra)


def test_prepare_of_a_batch_names_the_slot_the_files_and_the_control_word_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    code, first = _call(capsys, "prepare", run["root"], run["stages"][0])
    assert code == 0 and first["result"]["answer_slot"] == str(stage_steps.slot(run["root"], run["stages"][0])) and first["result"]["budget"]["bytes"] > 0
    assert first["result"]["nonce"] in first["result"]["nonce_note"] and first["result"]["read"]["count"] > 0
    before = _events(run["root"])
    code, again = _call(capsys, "prepare", run["root"], run["stages"][0])
    assert code == 0 and _events(run["root"]) == before and "nonce" not in again["result"] and "CODEWORD_LOST" in again["result"]["nonce_note"]


def test_a_batch_waits_for_the_answer_of_the_batch_before_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    before = _events(run["root"])
    code, out = _call(capsys, "prepare", run["root"], run["stages"][1])
    assert code == 2 and out["code"] == "STAGE_WAITS" and run["stages"][0] in out["message"] and _events(run["root"]) == before


def test_the_second_batch_gets_the_capabilities_of_the_first_from_the_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    assert _answer(capsys, run, 0)[0] == 0
    code, out = _call(capsys, "prepare", run["root"], run["stages"][1])
    assert code == 0 and (run["root"] / "prior-capabilities").is_dir() and _events(run["root"])[-1] == f"MODEL_REQUESTED:{run['stages'][1]}"
    request = pilot_state.read_model_request(run["root"], run["attempt_id"], run["stages"][1], out["result"]["request_digest"])
    assert len(request["input_digests"]) == 5


def test_submit_of_a_batch_records_the_fragment_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    code, out = _answer(capsys, run, 0)
    assert code == 0 and f"MODEL_RESPONSE_RECEIVED:{run['stages'][0]}" in _events(run["root"]) and "candidate" not in out["result"]
    before = _events(run["root"])
    code, again = _call(capsys, "submit", run["root"], run["stages"][0])
    assert code == 0 and again["result"]["answered"] is True and _events(run["root"]) == before


def test_submit_of_a_batch_before_its_prepare_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    code, out = _call(capsys, "submit", run["root"], run["stages"][0])
    assert code == 2 and out["code"] == "STAGE_NOT_PREPARED"


@pytest.mark.parametrize("batch, other", [(1, 0), (0, 1)])
def test_the_fragment_of_another_batch_in_the_slot_is_refused_with_its_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str], batch: int, other: int) -> None:
    run = _generator(tmp_path)
    if batch == 1:  # batch 2 is prepared only after batch 1
        assert _answer(capsys, run, 0)[0] == 0
    stage = run["stages"][batch]
    _call(capsys, "prepare", run["root"], stage)
    _put(run["root"], stage, run["values"][other])
    before = _events(run["root"])
    code, out = _call(capsys, "submit", run["root"], stage)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and stage.split(":")[1] in out["fix"] and _events(run["root"]) == before


def test_a_fragment_with_the_digests_of_another_plan_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    _call(capsys, "prepare", run["root"], run["stages"][0])
    _put(run["root"], run["stages"][0], {**run["values"][0], "plan_digest": "sha256:" + "0" * 64})
    code, out = _call(capsys, "submit", run["root"], run["stages"][0])
    assert code == 2 and out["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH"


def test_a_batch_slot_that_is_a_link_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    _call(capsys, "prepare", run["root"], run["stages"][0])
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(run["values"][0]), encoding="utf-8")
    try:
        stage_steps.slot(run["root"], run["stages"][0]).symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")
    before = _events(run["root"])
    code, out = _call(capsys, "submit", run["root"], run["stages"][0])
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _events(run["root"]) == before


def test_a_dry_run_of_a_batch_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    _call(capsys, "prepare", run["root"], run["stages"][0])
    _put(run["root"], run["stages"][0], run["values"][0])
    tree = {path.relative_to(run["root"]): path.stat().st_mtime_ns for path in run["root"].rglob("*") if path.is_file()}
    code, out = _call(capsys, "submit", run["root"], run["stages"][0], "--dry-run")
    assert code == 0 and out["dry_run"] is True and {path.relative_to(run["root"]): path.stat().st_mtime_ns for path in run["root"].rglob("*") if path.is_file()} == tree


def test_a_refused_fragment_has_the_code_of_the_check_and_the_one_retry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import batch_assembly

    run = _generator(tmp_path)
    bad = {**{key: value for key, value in run["values"][0].items() if key != "digest"}, "namespace": "B9"}
    _call(capsys, "prepare", run["root"], run["stages"][0])
    _put(run["root"], run["stages"][0], bad)
    before = _events(run["root"])
    code, out = _call(capsys, "submit", run["root"], run["stages"][0], "--dry-run")
    plan = json.loads(Path(run["plan"]).read_bytes())
    with pytest.raises(batch_assembly.BatchAssemblyError) as refused:
        batch_assembly.validate_fragment(bad, plan, json.loads(Path(run["header"]).read_bytes()), json.loads(Path(run["context"]).read_bytes()))
    assert code == 2 and out["code"] == refused.value.code and "--transport-attempts 2" in out["fix"] and _events(run["root"]) == before
    _put(run["root"], run["stages"][0], run["values"][0])  # the one retry is there
    assert _call(capsys, "submit", run["root"], run["stages"][0], "--transport-attempts", "2")[0] == 0


def test_the_last_batch_assembles_the_candidate_the_review_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.batch_assembly import assemble_candidate, canonical_bytes
    from tools.orchestrate_test_case_revision import journal_assembly

    run = _generator(tmp_path)
    assert _answer(capsys, run, 0)[0] == 0
    code, out = _answer(capsys, run, 1)
    candidate, receipt, _fragments = journal_assembly(run["root"], run["attempt_id"])
    expected, expected_receipt = assemble_candidate(json.loads(Path(run["header"]).read_bytes()), json.loads(Path(run["plan"]).read_bytes()), run["values"])
    assert code == 0 and out["result"]["candidate"]["document_digest"] == receipt["document_digest"] == expected_receipt["document_digest"]
    assert canonical_bytes(candidate) == canonical_bytes(expected) and out["result"]["candidate"]["assembly_receipt_digest"] == receipt["digest"]
    assert not list(run["root"].rglob("candidate*.json")) and _status(run["root"])["next"] == {"verb": "review-open", "stage": "tc-reviewer"}
    code, again = _call(capsys, "submit", run["root"], run["stages"][1])  # the recorded answer stands; the candidate is said again
    assert code == 0 and again["result"]["candidate"] == out["result"]["candidate"]


def test_an_assembly_that_refuses_leaves_the_last_answer_unwritten_and_the_retry_takes(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import batch_assembly

    run = _generator(tmp_path)
    assert _answer(capsys, run, 0)[0] == 0
    real = batch_assembly.assemble_candidate

    def refuse(*_args):
        raise batch_assembly.BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "the document of all the batches is not canonical")

    monkeypatch.setattr(batch_assembly, "assemble_candidate", refuse)
    _call(capsys, "prepare", run["root"], run["stages"][1])
    _put(run["root"], run["stages"][1], run["values"][1])
    before = _events(run["root"])
    code, out = _call(capsys, "submit", run["root"], run["stages"][1])
    assert code == 2 and out["code"] == "BATCH_SEMANTIC_CONFLICT" and _events(run["root"]) == before
    monkeypatch.setattr(batch_assembly, "assemble_candidate", real)
    code, out = _call(capsys, "submit", run["root"], run["stages"][1], "--transport-attempts", "2")
    assert code == 0 and "candidate" in out["result"]


def test_a_mostly_manual_candidate_of_a_project_with_code_is_warned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    assert _answer(capsys, run, 0)[0] == 0
    code, out = _answer(capsys, run, 1)
    steps = [step for value in run["values"] for case in value["test_cases"] for step in case["steps"]]
    assert code == 0 and all(step["manual_only"] for step in steps)  # the fixture has no automated step
    assert out["result"]["candidate"]["warnings"].count("MANUAL_SHARE_HIGH") == 1 and out["result"]["candidate"]["manual_share"]["manual_steps"] == len(steps)


def test_the_status_names_each_step_of_the_generator(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    first, second = run["stages"]
    seen = _status(run["root"])
    assert (seen["state"], seen["waiting_for"], seen["next"]) == ("generator", "agent", {"verb": "prepare", "stage": first}) and "budget" not in seen
    _call(capsys, "prepare", run["root"], first)
    seen = _status(run["root"])
    assert (seen["waiting_for"], seen["next"], seen["answer_slot"]) == ("model", {"verb": "submit", "stage": first}, str(stage_steps.slot(run["root"], first)))
    assert seen["budget"]["bytes"] > 0
    _put(run["root"], first, run["values"][0])
    _call(capsys, "submit", run["root"], first)
    assert _status(run["root"])["next"] == {"verb": "prepare", "stage": second}


def test_the_invocation_of_a_batch_is_a_label_the_reviewer_session_takes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import re

    run = _generator(tmp_path)
    _code, out = _call(capsys, "prepare", run["root"], run["stages"][0])
    request = pilot_state.read_model_request(run["root"], run["attempt_id"], run["stages"][0], out["result"]["request_digest"])
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", request["invocation_id"]), request["invocation_id"]  # reviewer-session.schema.json: label


def test_the_invocation_label_of_the_host_goes_into_the_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    _code, out = _call(capsys, "prepare", run["root"], run["stages"][0], "--invocation-id", "host-session-7")
    request = pilot_state.read_model_request(run["root"], run["attempt_id"], run["stages"][0], out["result"]["request_digest"])
    assert request["invocation_id"] == "host-session-7"


def test_the_same_label_for_the_generator_and_the_reviewer_is_no_independence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _generator(tmp_path)
    for index in range(2):
        assert _call(capsys, "prepare", run["root"], run["stages"][index], "--invocation-id", "host-session-7")[0] == 0
        _put(run["root"], run["stages"][index], run["values"][index])
        assert _call(capsys, "submit", run["root"], run["stages"][index])[0] == 0
    code = api.main(["review-open", "--run-root", str(run["root"]), "--attempt", run["attempt_id"], "--output-dir", str(run["root"] / "published"),
                     "--model-id", "model-reviewer", "--invocation-id", "host-session-7", "--fresh-context", "yes"])
    opened = json.loads(capsys.readouterr().out)
    assert code == 0 and opened["result"]["reviewer_session_boundary"]["host_isolation"]["distinct_invocations"] is False, opened


# ---- the plan of the batches: ``prepare`` / ``submit --stage plan`` — the agent names the subject, the journal gives the rest ----------------

PLAN = "plan"
SUBJECT = {"subject": "Оплата и возврат заказа"}


def _plannable(tmp_path: Path, warnings: list[str] | None = None) -> tuple[Path, str, list[dict]]:
    """A run whose marker is answered (two requirements, no gaps by default) and whose context is selected; no plan yet."""
    from tests.test_scope_pause import _paused_run

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path, warnings=[] if warnings is None else warnings)
    return root, attempt_id, receipts


def _published(root: Path, attempt_id: str, kind: str) -> dict:
    return dict(pilot_state.generator_input(root.parents[1], root, pilot_state.derive_state(root), attempt_id, kind))


def test_prepare_plan_makes_the_inbox_and_names_the_slot_the_form_and_the_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt, _receipts = _plannable(tmp_path)
    code, out = _call(capsys, "prepare", root, PLAN)
    result = out["result"]
    assert code == 0 and result["answer_slot"] == str(stage_steps.slot(root, PLAN)) and (root / "incoming").is_dir()
    assert result["answer"] == {"subject": "<название предмета проверки по-русски>"} and result["requirements"] == 2 and result["context_receipts"] == 1


def test_a_repeated_prepare_plan_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt, _receipts = _plannable(tmp_path)
    _code, first = _call(capsys, "prepare", root, PLAN)
    before = _events(root)
    code, again = _call(capsys, "prepare", root, PLAN)
    assert code == 0 and again == first and _events(root) == before


def test_submit_plan_publishes_the_header_and_the_plan_of_the_journal_in_one_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _marker_rows
    from tools.batch_assembly import plan_batches_for_project

    root, attempt_id, receipts = _plannable(tmp_path)
    _call(capsys, "prepare", root, PLAN)
    _put(root, PLAN, SUBJECT)
    code, out = _call(capsys, "submit", root, PLAN)
    header, plan = _published(root, attempt_id, "header"), _published(root, attempt_id, "plan")
    batch = plan["batches"][0]
    assert code == 0 and out["result"]["plan"]["digest"] == plan["digest"] and out["result"]["plan"]["batches"][0]["stage"] == f"tc-generator:{batch['batch_id']}"
    created = next(row for row in pilot_state.derive_state(root)["events"] if row["event_type"] == "RUN_CREATED")
    assert header["document_id"] == "TCDOC-run" and header["metadata"] == {"subject": {"kind": "generic", "name": SUBJECT["subject"]}, "documentation": [],
                                                                          "project": tmp_path.name, "author": "controller", "date": created["observed_at"][:10]}
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "canonical-test-document.schema.json").read_text(encoding="utf-8"))
    version = re.search(r"(\d+\.\d+\.\d+)$", schema["title"]).group(1)  # the header is the version of the canonical document
    assert (header["schema_version"], header["revision"], header["parent_sha256"], header["content_locale"]) == (version, 1, None, "ru-RU")
    evidence = {"header_digest": header["digest"], "context_receipt_digest": receipts[0]["digest"], "context_receipt_digests": [receipts[0]["digest"]]}
    digest = next(row["artifact_digest"] for row in pilot_state.derive_state(root)["events"] if row.get("stage_instance_id") == BASELINE and row["event_type"] == "MODEL_RESPONSE_RECEIVED")
    answer = root / pilot_state.read_model_stage_artifact(root, attempt_id, BASELINE, digest)["path"]  # the file of the marker answer: its size is measured
    expected = plan_batches_for_project(_marker_rows(), evidence, tmp_path, [answer], receipts, None, None)
    assert plan["source_requirements"] == expected["source_requirements"] and [row["owned_source_requirement_ids"] for row in plan["batches"]] == [
        row["owned_source_requirement_ids"] for row in expected["batches"]]


@pytest.mark.parametrize("docs, module, wanted", [(["docs/Cart Checkout.md"], ".", "TCDOC-cart-checkout"), (["docs/Требования.md"], "python-pytest", "TCDOC-python-pytest"),
                                                  (["docs/!!!.md"], ".", "TCDOC-run"), (["a/Спец.md", "b/ok.md"], "m", "TCDOC-m"), (["docs/ok.md", "x.md"], ".", "TCDOC-ok"),
                                                  (["docs/" + "d" * 200 + ".md"], ".", "TCDOC-" + "d" * 80), ([], ".", "TCDOC-run"), (["docs/a.b.md#Раздел"], ".", "TCDOC-a.b")])
def test_the_document_id_is_a_slug_that_is_never_empty(docs: list[str], module: str, wanted: str) -> None:
    import re

    found = pilot_state.header_document_id(docs, module)
    assert found == wanted and re.fullmatch(r"TCDOC-[a-z0-9](?:[a-z0-9_.-]*[a-z0-9_-])?", found)


def test_submit_plan_before_the_marker_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _run(tmp_path, files=1)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "STAGE_UNKNOWN"


def test_submit_plan_with_gaps_and_no_decision_is_pending_and_names_the_scope_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import GAP

    root, _attempt, _receipts = _plannable(tmp_path, warnings=[GAP])
    _put(root, PLAN, SUBJECT)
    before = _events(root)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "SCOPE_PENDING" and "prepare --stage scope" in out["fix"] and _events(root) == before


def test_submit_plan_carries_the_recorded_exclusion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_scope_pause import _record

    root, attempt_id, _receipts = _plannable(tmp_path)
    _record(root, attempt_id, excluded=["SREQ-batch-a-001"], reason="нет стенда", proceed=False)
    _put(root, PLAN, SUBJECT)
    code, _out = _call(capsys, "submit", root, PLAN)
    plan = _published(root, attempt_id, "plan")
    assert code == 0 and plan["out_of_scope"] == [{"source_requirement_id": "SREQ-batch-a-001", "reason": "нет стенда"}] and len(plan["source_requirements"]) == 1


@pytest.mark.parametrize("answer", [{}, {"subject": ""}, {"subject": "  "}, {"subject": 7}, {"subject": ["a"]}, {"subject": "a\nb"}, {"subject": "я" * 201},
                                    {**SUBJECT, "document_id": "TCDOC-x"}, [SUBJECT], "Оплата"])
def test_submit_plan_refuses_a_wrong_form_with_the_form_in_the_fix_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str], answer: object) -> None:
    root, *_ = _plannable(tmp_path)
    _put(root, PLAN, answer)
    before = _events(root)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "PLAN_ANSWER_INVALID" and '"subject"' in out["fix"] and _events(root) == before


def test_submit_plan_refuses_a_slot_that_is_a_link(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, *_ = _plannable(tmp_path)
    _call(capsys, "prepare", root, PLAN)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(SUBJECT), encoding="utf-8")
    try:
        stage_steps.slot(root, PLAN).symlink_to(target)
    except OSError:
        pytest.skip("no symbolic links here")
    before = _events(root)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "INPUT_OUTSIDE_PROJECT" and _events(root) == before


def _tree_of(root: Path) -> dict[str, int]:
    return {path.relative_to(root).as_posix(): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


def test_a_dry_run_of_submit_plan_checks_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _receipts = _plannable(tmp_path)
    _call(capsys, "prepare", root, PLAN)
    _put(root, PLAN, SUBJECT)
    before = (_events(root), _tree_of(root))
    code, out = _call(capsys, "submit", root, PLAN, "--dry-run")
    assert code == 0 and out["dry_run"] is True and out["result"]["plan"]["batches"]
    assert (_events(root), _tree_of(root)) == before
    with pytest.raises(pilot_state.ModelInputsMismatch):
        _published(root, attempt_id, "plan")


def test_after_the_plan_is_published_a_repeat_answers_and_never_reads_the_slot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _receipts = _plannable(tmp_path)
    _put(root, PLAN, SUBJECT)
    _call(capsys, "submit", root, PLAN)
    plan, header, before = _published(root, attempt_id, "plan"), _published(root, attempt_id, "header"), _events(root)
    stage_steps.slot(root, PLAN).write_text("not json", encoding="utf-8")
    for command in ("submit", "prepare"):
        code, out = _call(capsys, command, root, PLAN)
        assert code == 0 and out["result"]["answered"] is True and out["result"]["plan"]["digest"] == plan["digest"]
    assert _events(root) == before and _published(root, attempt_id, "plan") == plan and _published(root, attempt_id, "header") == header


def test_a_decision_after_the_plan_is_still_refused_by_the_plan_of_the_journal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt, _receipts = _plannable(tmp_path)
    _put(root, PLAN, SUBJECT)
    _call(capsys, "submit", root, PLAN)
    _put(root, SCOPE, EXCLUDE)
    code, out = _call(capsys, "submit", root, SCOPE)
    assert code == 2 and out["code"] == "SCOPE_DECISION_CONFLICT"


def test_a_context_file_changed_after_the_scan_is_refused_at_submit_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, _attempt, _receipts = _plannable(tmp_path)
    (tmp_path / "src" / "part0.py").write_text("# changed\n", encoding="utf-8")
    _put(root, PLAN, SUBJECT)
    before = _events(root)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "CONTEXT_DRIFTED" and _events(root) == before


def test_the_status_names_prepare_then_submit_of_the_plan_then_the_first_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _receipts = _plannable(tmp_path)
    first = _status(root)
    assert (first["state"], first["waiting_for"], first["next"]) == ("generator", "agent", {"verb": "prepare", "stage": PLAN})
    assert first["answer_slot"] == str(stage_steps.slot(root, PLAN))
    _put(root, PLAN, SUBJECT)
    assert _status(root)["next"] == {"verb": "submit", "stage": PLAN}
    _call(capsys, "submit", root, PLAN)
    batch_id = _published(root, attempt_id, "plan")["batches"][0]["batch_id"]
    assert _status(root)["next"] == {"verb": "prepare", "stage": f"tc-generator:{batch_id}"}


def test_a_project_that_asks_for_the_own_context_is_refused_at_submit_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_generator_context import _project

    root, *_ = _plannable(tmp_path)
    (tmp_path / ".skillsrc").write_bytes((_project(tmp_path / "other", {"generator_context": "own"}) / ".skillsrc").read_bytes())
    _put(root, PLAN, SUBJECT)
    before = _events(root)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 2 and out["code"] == "GENERATOR_CONTEXT_NOT_IMPLEMENTED" and _events(root) == before


def test_no_command_plans_the_batches_or_imports_the_cases_by_files_of_the_agent() -> None:
    assert not {"plan", "plan-batches", "publish-generator-input", "cases-import"} & set(api.COMMANDS)


# ---- the import of ready cases on the agent's path: ``prepare`` answers the requirements by code, ``submit --stage plan`` publishes the skeletons ----

PART = "context-marker:batch-001"


def _import_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], **flags) -> tuple[Path, str]:
    from tests.test_cases_import import _project, _scan

    _code, scan = _scan(_project(tmp_path), monkeypatch, **flags)
    capsys.readouterr()
    return Path(scan["run_root"]), scan["attempt_id"]


def _answer_the_part(capsys: pytest.CaptureFixture[str], root: Path, attempt_id: str) -> None:
    """The marker part of the code: the model's answer is the requirements answer with the sources of the code."""
    digest = next(row["artifact_digest"] for row in pilot_state.derive_state(root)["events"]
                  if row.get("stage_instance_id") == REQUIREMENTS and row["event_type"] == "MODEL_RESPONSE_RECEIVED")
    answer = pilot_state.read_model_stage_artifact(root, attempt_id, REQUIREMENTS, digest)["artifact"]
    assert _call(capsys, "prepare", root, PART)[0] == 0
    _put(root, PART, {**answer, "artifacts": {**answer["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine склеивает два значения"]}}})
    assert _call(capsys, "submit", root, PART)[0] == 0


def _skeletons(root: Path, attempt_id: str) -> list[bool]:
    plan = _published(root, attempt_id, "plan")
    return [(root / "imported-cases" / f"{attempt_id}.{row['batch_id']}.json").is_file() for row in plan["batches"]]


def test_an_import_without_documents_goes_by_prepare_and_submit_to_a_plan_with_a_skeleton_for_each_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id = _import_run(tmp_path, monkeypatch, capsys)
    assert _status(root)["next"] == {"verb": "prepare", "stage": REQUIREMENTS} and "answer_slot" not in _status(root)
    code, out = _call(capsys, "prepare", root, REQUIREMENTS)  # the code answers: the request, the answer and the skeleton are one transition
    assert code == 0 and out["result"]["imported"]["cases"] == 2 and "artifact" not in out["result"]
    _answer_the_part(capsys, root, attempt_id)
    _put(root, PLAN, SUBJECT)
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 0 and out["result"]["plan"]["batches"], out
    assert _skeletons(root, attempt_id) == [True] * len(out["result"]["plan"]["batches"])
    header = _published(root, attempt_id, "header")
    assert header["metadata"]["documentation"] == ["cases/zephyr.csv"] and header["document_id"] == "TCDOC-zephyr"  # the CSV is the document of the run
    first = out["result"]["plan"]["batches"][0]["stage"]
    code, prepared = _call(capsys, "prepare", root, first)
    assert code == 0 and prepared["result"]["request_digest"], prepared


def test_an_import_with_documents_publishes_the_skeleton_at_submit_plan_and_a_dry_run_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                              capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_cases_import_docs import _answer

    docs = str((tmp_path / "project" / "docs" / "req.md").resolve())  # typed as an absolute path: the header names the document relative to the project
    root, attempt_id = _import_run(tmp_path, monkeypatch, capsys, docs=[docs])
    assert _call(capsys, "prepare", root, REQUIREMENTS)[0] == 0
    _put(root, REQUIREMENTS, _answer())  # the model's answer, with the links of the cases
    assert _call(capsys, "submit", root, REQUIREMENTS)[0] == 0
    _answer_the_part(capsys, root, attempt_id)
    assert not list((root / "imported-cases").glob("*")) if (root / "imported-cases").exists() else True  # no skeleton before the plan
    _put(root, PLAN, SUBJECT)
    code, out = _call(capsys, "submit", root, PLAN, "--dry-run")
    assert code == 0 and out["result"]["plan"]["batches"]
    assert not list((root / "imported-cases").glob("*")) if (root / "imported-cases").exists() else True  # a dry run publishes no skeleton
    code, out = _call(capsys, "submit", root, PLAN)
    assert code == 0 and list((root / "imported-cases").glob(f"{attempt_id}.json")) and all(_skeletons(root, attempt_id))
    header = _published(root, attempt_id, "header")
    assert header["metadata"]["documentation"] == ["docs/req.md"] and header["document_id"] == "TCDOC-req", header  # nothing absolute in the header


def test_an_import_with_documents_refuses_an_answer_without_the_links_of_the_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_cases_import_docs import _answer

    root, _attempt_id = _import_run(tmp_path, monkeypatch, capsys, docs=["docs/req.md"])
    assert _call(capsys, "prepare", root, REQUIREMENTS)[0] == 0
    _put(root, REQUIREMENTS, _answer(None))
    before = _events(root)
    code, out = _call(capsys, "submit", root, REQUIREMENTS)
    assert code == 2 and out["code"] == "IMPORT_CASE_LINKS" and _events(root) == before
