"""V2: ``prepare`` and ``submit`` — the cycle of the agent over the marker: the request from the journal, the answer from the slot of the stage."""
from __future__ import annotations

import ast
import json
import os
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
    code, out = _call(capsys, "prepare", root, "tc-generator:BATCH-1")
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
