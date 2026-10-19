"""S3 of the security fixes (handoff/plan-security-fixes.md): a person approves the generated tests before they run.

``execution.review: human`` is the default (decision of 2026-10-19): after the generated files are written and before
any build or test run the driver asks ``approve-generated-tests`` with the exact files; the answer is the digest of
that list and is recorded as ``EXECUTION_APPROVED``; without it the journal refuses ``EXECUTION_STARTED``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver
from tools.pilot_state import (claim_execution_start, derive_state, execution_review_required, generated_tests_approval_digest,
                               record_execution_approval)

pytestmark = pytest.mark.human_gates


def _events(run_root: Path, kind: str) -> list[dict]:
    return [event for event in derive_state(run_root)["events"] if event["event_type"] == kind]


def _until_approval(tmp_path: Path, project: Path | None = None, **options) -> tuple[Path, Path, dict, SavedModel]:
    project = project or _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1", **options), model,
                  until=lambda task: task["action"] == "ask_user" and task["task_id"].endswith("ask.approve-generated-tests"))
    return project, _run_root(project, task), task, model


def test_the_driver_stops_after_writing_the_generated_tests_and_runs_them_after_the_approval(tmp_path: Path) -> None:
    project, run_root, task, model = _until_approval(tmp_path)
    assert execution_review_required(run_root)
    attempt_id = task["attempt_id"]
    digest = generated_tests_approval_digest(run_root, attempt_id)
    assert task["approval_digest"] == digest and task["options"] == [{"value": digest, "label": task["options"][0]["label"]}]
    assert task["files"] and all((project / row["path"]).is_file() for row in task["files"])  # written, for the person to read
    assert all(row["path"] in task["question"] and row["content_digest"] in task["question"] for row in task["files"])
    assert not _events(run_root, "EXECUTION_STARTED") and not _events(run_root, "EXECUTION_APPROVED")
    assert driver._exit_code(task) == 3

    # The same question again until a person answers; another digest is no answer.
    assert driver.advance(project, run_root)["task_id"] == task["task_id"]
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, task["task_id"], answer="sha256:" + "0" * 64)
    with pytest.raises(ValueError):  # the journal refuses the execution without the approval
        claim_execution_start(run_root, attempt_id, "sha256:" + "1" * 64)

    done = _drive(project, driver.submit(project, run_root, task["task_id"], answer=digest), model)
    approvals = _events(run_root, "EXECUTION_APPROVED")
    assert [(event["attempt_id"], event["artifact_digest"]) for event in approvals] == [(attempt_id, digest)]
    assert approvals[0]["seq"] < _events(run_root, "EXECUTION_STARTED")[0]["seq"]
    assert done["action"] == "done" and done["result"]["verification"] == "PASS", done


def test_an_approval_covers_only_the_exact_files(tmp_path: Path) -> None:
    project, run_root, task, _model = _until_approval(tmp_path)
    attempt_id = task["attempt_id"]
    with pytest.raises(ValueError):
        record_execution_approval(run_root, attempt_id, "sha256:" + "2" * 64)
    path = project / task["files"][0]["path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited after the question\n", encoding="utf-8")
    # The recorded delta is what the approval names; the build itself refuses drifted bytes (generated_delta), so the
    # digest stays the one of the written files.
    assert generated_tests_approval_digest(run_root, attempt_id) == task["approval_digest"]
    record_execution_approval(run_root, attempt_id, task["approval_digest"])
    with pytest.raises(ValueError):  # once per attempt
        record_execution_approval(run_root, attempt_id, task["approval_digest"])


def test_execution_review_none_runs_without_the_question(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["execution"] = {"review": "none"}
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    model = SavedModel("local-pilot-v1")
    done = _drive(project, _start(project, "local-pilot-v1"), model)
    run_root = _run_root(project, done)
    assert done["result"]["verification"] == "PASS" and not execution_review_required(run_root) and not _events(run_root, "EXECUTION_APPROVED")


def test_the_flag_overrides_skillsrc_and_cases_only_never_asks(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["execution"] = {"review": "none"}
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    _project_path, run_root, task, _model = _until_approval(tmp_path, project, execution_review="human")
    assert task["action"] == "ask_user" and execution_review_required(run_root)
    cases = _project(tmp_path / "cases", local=False)
    done = _drive(cases, _start(cases, "cases-only-v1"), SavedModel("cases-only-v1"))
    assert done["action"] == "done" and not execution_review_required(_run_root(cases, done))
    with pytest.raises(driver.DriverError):
        _start(_project(tmp_path / "bad", local=True), "local-pilot-v1", execution_review="maybe")
