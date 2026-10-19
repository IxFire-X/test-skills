"""S5 of the security fixes: a person confirms which project files go to the model, once per run.

``context.confirm`` is on by default (decision of 2026-10-19): after scan the driver asks ``confirm-context`` with every
selected file and its size; the answer is the digest of that list and is recorded as ``CONTEXT_CONFIRMED``; without
it the journal refuses ``MODEL_REQUESTED``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver
from tools.pilot_state import context_confirmation_digest, derive_state, publish_model_request, record_context_confirmation

pytestmark = pytest.mark.human_gates


def _events(run_root: Path, kind: str) -> list[dict]:
    return [event for event in derive_state(run_root)["events"] if event["event_type"] == kind]


def _question(project: Path, **options) -> tuple[Path, dict]:
    task = _start(project, "cases-only-v1", execution_review="none", **options)
    return _run_root(project, task), task


def test_the_run_asks_before_any_file_goes_to_the_model(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    run_root, task = _question(project)
    assert task["action"] == "ask_user" and task["task_id"].endswith("ask.confirm-context") and driver._exit_code(task) == 3
    attempt_id = task["attempt_id"]
    digest = context_confirmation_digest(run_root, attempt_id)
    assert task["confirmation_digest"] == digest and task["options"][0]["value"] == digest
    receipts = driver._context_receipts(run_root, attempt_id)
    selected = {item["project_path"]: item["size"] for receipt in receipts for item in receipt["files"]}
    documents = {row["path"]: row["size"] for row in task["documents"]}
    # A requirement document inside the project is listed once, as a document (kit run, 2026-10-19).
    assert {row["path"]: row["size"] for row in task["files"]} == {path: size for path, size in selected.items() if path not in documents}
    selected = {path: size for path, size in selected.items() if path not in documents}
    assert ".skillsrc" not in selected  # the package's configuration never goes to the model (contract revision 2026-10-20)
    assert documents == {"docs/feature.md": len((project / "docs" / "feature.md").read_bytes())}  # the requirements go to the model too
    total = sum(selected.values()) + sum(documents.values())
    assert str(total) in task["question"] and str(len(selected)) in task["question"] and "docs/feature.md" in task["question"]
    listing = Path(task["listing_path"])
    text = listing.read_text(encoding="utf-8")
    assert listing.is_file() and all(path in text for path in [*selected, *documents]) and str(total) in text
    assert not _events(run_root, "MODEL_REQUESTED")
    with pytest.raises(ValueError):  # the journal refuses a model call before the confirmation
        baseline, _inventory = driver._baseline_and_inventory(run_root, driver._attempt(run_root))
        publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="context-x",
                              input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, task["task_id"], answer="sha256:" + "0" * 64)
    following = driver.submit(project, run_root, task["task_id"], answer=digest)
    assert following["action"] == "llm" and following["stage"] == "context-marker:baseline"
    confirmations = _events(run_root, "CONTEXT_CONFIRMED")
    assert [(event["attempt_id"], event["artifact_digest"]) for event in confirmations] == [(attempt_id, digest)]
    assert confirmations[0]["seq"] < _events(run_root, "MODEL_REQUESTED")[0]["seq"]
    done = _drive(project, following, SavedModel("cases-only-v1"))
    assert done["action"] == "done" and len(_events(run_root, "CONTEXT_CONFIRMED")) == 1


def test_a_confirmation_names_exactly_the_selected_files(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    run_root, task = _question(project)
    with pytest.raises(ValueError):
        record_context_confirmation(run_root, task["attempt_id"], "sha256:" + "1" * 64)
    record_context_confirmation(run_root, task["attempt_id"], task["confirmation_digest"])
    with pytest.raises(ValueError):  # once per attempt
        record_context_confirmation(run_root, task["attempt_id"], task["confirmation_digest"])


def test_context_confirm_false_in_skillsrc_or_the_flag_skips_the_question(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["context"] = {"confirm": False}
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    run_root, task = _question(project)
    assert task["action"] == "llm" and not _events(run_root, "CONTEXT_CONFIRMED")
    flagged = _project(tmp_path / "flag", local=False)
    run_root, task = _question(flagged, confirm_context="no")
    assert task["action"] == "llm"
    asked = _project(tmp_path / "asked", local=False)
    (asked / ".skillsrc").write_text((project / ".skillsrc").read_text(encoding="utf-8"), encoding="utf-8")
    _run_root_asked, task = _question(asked, confirm_context="yes")  # the flag is stronger than .skillsrc
    assert task["action"] == "ask_user" and task["task_id"].endswith("ask.confirm-context")


def test_the_process_runner_stops_on_the_question_and_continues_with_the_answer(tmp_path: Path) -> None:
    from tools.pipeline_driver_run import run

    project = _project(tmp_path, local=False)
    options = {"profile": "cases-only-v1", "docs": ["docs/feature.md"], "subject": "Каталог товаров", "model_id": "m", "execution_review": "none"}
    stopped = run(project, options, answers={})
    assert stopped["stopped"] == "ASK_USER_UNANSWERED" and stopped["task_id"].endswith("ask.confirm-context")
