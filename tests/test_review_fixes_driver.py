"""A1: the deterministic driver (`next` / `submit` / `status`).

Model answers are pre-saved JSON files under ``tests/fixtures/driver``.  They carry
only content; ``${...}`` marks the identifiers a real model copies from its task
(ID prefixes from the brief, IDs of the accepted cases, scope IDs of a review part).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import pipeline_driver as driver

ROOT = Path(__file__).resolve().parents[1]
ANSWERS = ROOT / "tests" / "fixtures" / "driver"


def _project(tmp_path: Path, *, local: bool) -> Path:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "tests" / "fixtures" / "projects" / "python-pytest", project)
    (project / "docs").mkdir()
    shutil.copyfile(ANSWERS / "requirements.md", project / "docs" / "feature.md")
    if local:
        from tests.helpers import MODULE_PYTHON
        from tests.test_project_native_pytest import _module_python

        _module_python(project)
        skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
        skillsrc["modules"][0]["test"]["interpreter"] = MODULE_PYTHON
        (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    return project


def _saved(name: str, bindings: dict[str, str]) -> Any:
    text = (ANSWERS / name).read_text(encoding="utf-8")
    return json.loads(re.sub(r"\$\{([a-z_]+(?::\d+)?)\}", lambda match: bindings[match.group(1)], text))


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class SavedModel:
    """Answers each task from the saved files; never computes a digest or an order."""

    def __init__(self, profile: str, automation: list[str] | None = None) -> None:
        self.profile = profile
        self.automation = list(automation or ["tc-to-autotest.passing.answer.json"])
        self.stages: list[str] = []

    def answer(self, task: dict[str, Any]) -> Any:
        stage = task["stage"]
        self.stages.append(stage)
        if stage == "context-marker:baseline":
            value = _load(task["draft_path"])
            additions = _saved("context-marker.additions.json", {})
            value["artifacts"]["source_code_and_diff"]["sources"] = additions["sources"]
            value["warnings"] = additions["warnings"]
            return value
        if stage.startswith("tc-generator:"):
            brief = _load(task["inputs"][0])
            bindings = dict(brief["id_prefixes"])
            bindings.update({f"source:{index}": row["source_requirement_id"] for index, row in enumerate(brief["owned_source_requirements"], 1)})
            return _saved(f"tc-generator.{self.profile.removesuffix('-v1')}.answer.json", bindings)
        if stage.startswith(("tc-reviewer:", "autotest-reviewer:")):
            return review_answer(task)
        if stage.startswith("tc-to-autotest:"):
            document = _load(task["inputs"][1])
            bindings: dict[str, str] = {}
            automatable = next(case for case in document["test_cases"] if not case["steps"][0]["manual_only"])
            manual = next(case for case in document["test_cases"] if case["steps"][0]["manual_only"])
            for index, case in ((1, automatable), (2, manual)):
                step = case["steps"][0]
                bindings.update({f"case:{index}": case["case_id"], f"step:{index}": step["step_id"], f"expectation:{index}": step["expectations"][0]["expectation_id"]})
                if step["expectations"][0]["assertions"]:
                    bindings[f"assertion:{index}"] = step["expectations"][0]["assertions"][0]["assertion_id"]
            return _saved(self.automation.pop(0) if len(self.automation) > 1 else self.automation[0], bindings)
        raise AssertionError(f"no saved answer for {stage}")


def review_answer(task: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    envelope = _load(task["inputs"][0])
    saved = _saved("review.accept.answer.json", {})
    row = saved.pop("coverage_row")
    saved["coverage"] = [{"scope_id": scope["scope_id"], **row, "evidence": [scope["inputs"][0]["artifact_digest"] + scope["inputs"][0]["pointer"]]} for scope in envelope["scopes"]]
    saved.update(overrides)
    return saved


def _run_root(project: Path, task: dict[str, Any]) -> Path:
    return project / ".pilot-runs" / task["run_id"]


def _start(project: Path, profile: str, **options: Any) -> dict[str, Any]:
    return driver.start_run(project, {"profile": profile, "docs": ["docs/feature.md"], "subject": "Каталог товаров", "reviewer_isolation": "fresh",
                                      "model_id": "model-test", **options})


def _answer_and_submit(project: Path, task: dict[str, Any], model: SavedModel) -> dict[str, Any]:
    Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
    return driver.submit(project, _run_root(project, task), task["task_id"])


def _drive(project: Path, task: dict[str, Any], model: SavedModel, *, until: Callable[[dict[str, Any]], bool] | None = None,
           answers: dict[str, str] | None = None) -> dict[str, Any]:
    for _ in range(60):
        if task["action"] == "done" or (until is not None and until(task)):
            return task
        if task["action"] == "ask_user":
            label = task["task_id"].split(".ask.", 1)[1]
            task = driver.submit(project, _run_root(project, task), task["task_id"], answer=(answers or {})[label])
            continue
        assert task["action"] == "llm"
        assert Path(task["skill_path"]).is_file() and Path(task["schema_path"]).is_file()
        assert all(Path(path).is_file() for path in task["inputs"])
        task = _answer_and_submit(project, task, model)
        assert task.get("status") != "rejected", task.get("errors")
    raise AssertionError("the driver did not finish")


# --------------------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------------------

def test_driver_cli_drives_cases_only_run_to_done(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    model = SavedModel("cases-only-v1")

    def call(*arguments: str) -> tuple[int, dict[str, Any]]:
        code = driver.main(list(arguments))
        return code, json.loads(capsys.readouterr().out)

    code, task = call("next", "--project", str(project), "--profile", "cases-only-v1", "--docs", "docs/feature.md", "--subject", "Каталог товаров",
                      "--reviewer-isolation", "fresh", "--model-id", "model-test")
    run_id = task["run_id"]
    for _ in range(20):
        if task["action"] == "done":
            break
        assert code == 3 and task["action"] == "llm"
        assert set(task) >= {"task_id", "stage", "skill_path", "inputs", "output_path", "schema_path"}
        Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
        code, task = call("submit", "--project", str(project), "--run", run_id, "--task-id", task["task_id"])
    result = task["result"]
    assert model.stages[:2] == ["context-marker:baseline", model.stages[1]] and model.stages[1].startswith("tc-generator:")
    assert model.stages[-1].startswith("tc-reviewer:canonical:")
    assert (result["status"], result["completion"], result["verification"], result["coverage"]) == ("terminal", "COMPLETE", "NOT_APPLICABLE", "MANUAL_ONLY")
    assert code == result["exit_code"]
    assert (Path(result["paths"]["candidate_bundle"]) / "test-cases.md").is_file() or any(Path(result["paths"]["candidate_bundle"]).iterdir())

    # `next` on a finished run only repeats the result; `status` is a read-only view.
    code_again, again = call("next", "--project", str(project), "--run", run_id)
    assert again == task and code_again == code
    _code, status = call("status", "--project", str(project), "--run", run_id)
    assert status["attempt_state"] == "TERMINAL" and status["stages"]["context-marker:baseline"] == "MODEL_RESPONSE_RECEIVED"


def test_driver_drives_local_pilot_run_to_passing_execution(tmp_path: Path) -> None:
    from tools.pilot_state import read_effective_canonical, read_execution_inputs

    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    done = _drive(project, _start(project, "local-pilot-v1"), model)
    result = done["result"]
    assert (result["completion"], result["verification"], result["coverage"], result["accepted"], result["exit_code"]) == ("COMPLETE", "PASS", "MIXED", True, 0)
    assert [stage.split(":")[0] for stage in model.stages] == ["context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer"]
    assert (project / "tests" / "test_generated.py").is_file()

    # The saved answers carry display_order 99 everywhere and relations assertion-first:
    # numbering and the canonical relation order were filled by the driver.
    run_root = project / ".pilot-runs" / done["run_id"]
    effective = read_effective_canonical(run_root, result["attempt_id"])["document"]
    assert [case["display_order"] for case in effective["test_cases"]] == [1, 2]
    published = read_execution_inputs(run_root, result["attempt_id"])["automation_artifact"]["artifacts"]
    assert [row["kind"] for row in published["implementation_relations"]] == ["operation", "assertion"]
    assert published["source"]["source_digest"].startswith("sha256:") and published["generated_files"][0]["content_digest"].startswith("sha256:")


def test_driver_offers_one_regeneration_after_broken_generated_test(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1", ["tc-to-autotest.broken-import.answer.json", "tc-to-autotest.passing.answer.json"])
    question = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task["action"] == "ask_user")
    assert question["task_id"].endswith("ask.regenerate-after-gate") and [option["value"] for option in question["options"]] == ["regenerate", "stop"]
    assert not (project / "tests" / "test_generated.py").exists()
    run_root = _run_root(project, question)
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, question["task_id"], answer="maybe")

    first = driver.submit(project, run_root, question["task_id"], answer="regenerate")
    assert first["action"] == "llm" and first["attempt_id"] != question["attempt_id"]
    done = _drive(project, first, model)
    assert (done["result"]["verification"], done["result"]["accepted"]) == ("PASS", True)
    automation_tasks = [stage for stage in model.stages if stage.startswith("tc-to-autotest:")]
    assert automation_tasks == ["tc-to-autotest:r1", "tc-to-autotest:r1"]
    brief = json.loads((driver.work_dir(run_root) / "inputs" / "automation-r1-brief.json").read_text(encoding="utf-8"))
    assert brief["previous_gate_failure"]["reason"] == "GENERATED_TEST_INVALID"
    # Answering the old question again changes nothing.
    assert driver.submit(project, run_root, question["task_id"], answer="regenerate") == driver.advance(project, run_root)


def test_driver_stops_when_user_declines_regeneration(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1", ["tc-to-autotest.broken-import.answer.json"])
    done = _drive(project, _start(project, "local-pilot-v1"), model, answers={"regenerate-after-gate": "stop"})
    result = done["result"]
    assert (result["verification"], result["reason_code"], result["accepted"]) == ("NOT_RUNNABLE", "GENERATED_TEST_INVALID", False)
    assert result["exec"]["regeneration"]["allowed"] is True


def test_driver_finalizes_rejected_static_review_without_materialization(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage", "").startswith("autotest-reviewer:"))
    envelope = _load(task["inputs"][0])
    finding = {"severity": "BLOCKING", "code": "ASSERTION_MISSING", "message": "Тест не проверяет код ответа.",
               "evidence": [envelope["scopes"][0]["inputs"][0]["artifact_digest"]], "related_ids": [envelope["scopes"][0]["scope_id"]]}
    Path(task["output_path"]).write_text(json.dumps(review_answer(task, findings=[finding]), ensure_ascii=False), encoding="utf-8")
    done = driver.submit(project, _run_root(project, task), task["task_id"])
    result = done["result"]
    assert done["action"] == "done" and (result["completion"], result["verification"], result["reason_code"], result["accepted"]) == (
        "PARTIAL", "NOT_APPLICABLE", "AUTOMATION_REVIEW_REJECTED", False)
    assert not (project / "tests" / "test_generated.py").exists()


# --------------------------------------------------------------------------------------
# submit: rejection, idempotency, questions, failed review calls
# --------------------------------------------------------------------------------------

def test_submit_rejects_invalid_answer_and_accepts_the_corrected_one(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    model = SavedModel("cases-only-v1")
    task = _drive(project, _start(project, "cases-only-v1"), model, until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    run_root = _run_root(project, task)
    good = model.answer(task)

    Path(task["output_path"]).write_text("{not json", encoding="utf-8")
    rejected = driver.submit(project, run_root, task["task_id"])
    assert rejected["status"] == "rejected" and rejected["task_id"] == task["task_id"]

    broken = json.loads(json.dumps(good))
    broken["test_cases"][0]["requirement_ids"] = ["REQ-unknown-999"]
    Path(task["output_path"]).write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    rejected = driver.submit(project, run_root, task["task_id"])
    assert rejected["status"] == "rejected" and rejected["errors"] and all({"path", "code", "message"} <= set(row) for row in rejected["errors"])
    assert driver.advance(project, run_root)["task_id"] == task["task_id"]  # nothing was published

    owned = {**good, "schema_version": "5.0.0"}
    Path(task["output_path"]).write_text(json.dumps(owned, ensure_ascii=False), encoding="utf-8")
    assert driver.submit(project, run_root, task["task_id"])["status"] == "rejected"

    elsewhere = tmp_path / "answer.json"
    elsewhere.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    following = driver.submit(project, run_root, task["task_id"], output=elsewhere)
    assert following["action"] == "llm" and following["stage"].startswith("tc-reviewer:")
    # A repeated submit of an answered task is a no-op that returns the current task.
    assert driver.submit(project, run_root, task["task_id"], output=elsewhere) == following
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, "no-such-task")
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, following["task_id"].replace("review", "../review"))


def test_driver_asks_about_reviewer_isolation_and_continues_without_it(tmp_path: Path) -> None:
    # D8: `none` no longer stops the run (REVIEWER_ISOLATION_UNAVAILABLE); the review runs as SELF.
    project = _project(tmp_path, local=False)
    model = SavedModel("cases-only-v1")
    question = _drive(project, _start(project, "cases-only-v1", reviewer_isolation=None), model, until=lambda task: task["action"] == "ask_user")
    assert question["task_id"].endswith("ask.reviewer-isolation") and {option["value"] for option in question["options"]} == {"fresh", "none"}
    assert driver._exit_code(question) == 3
    review = driver.submit(project, _run_root(project, question), question["task_id"], answer="none")
    assert review["action"] == "llm" and review["stage"].startswith("tc-reviewer:") and review["requires_fresh_context"] is False
    done = _drive(project, review, model)
    assert (done["result"]["review_independence"], done["result"]["reason_code"]) == ("SELF", "REVIEW_NOT_INDEPENDENT")


def test_failed_review_call_is_retried_in_a_new_task(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    model = SavedModel("cases-only-v1")
    task = _drive(project, _start(project, "cases-only-v1"), model, until=lambda task: task.get("stage", "").startswith("tc-reviewer:"))
    run_root = _run_root(project, task)
    assert task["requires_fresh_context"] is True and task["try"] == 1
    with pytest.raises(driver.DriverError):
        driver.submit(project, run_root, task["task_id"], failed="TRANSPORT", reason="")
    retry = driver.submit(project, run_root, task["task_id"], failed="TRANSPORT", reason="reviewer call timed out")
    assert retry["action"] == "llm" and retry["part_id"] == task["part_id"] and retry["try"] == 2 and retry["task_id"] != task["task_id"]
    done = _drive(project, retry, model)
    assert done["result"]["completion"] == "COMPLETE"


def test_failed_flag_is_only_for_review_tasks(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    with pytest.raises(driver.DriverError):
        driver.submit(project, _run_root(project, task), task["task_id"], failed="CONTENT", reason="x")


def test_start_requires_profile_and_documents(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    with pytest.raises(driver.DriverError):
        driver.start_run(project, {"profile": "unknown", "docs": ["docs/feature.md"]})
    with pytest.raises(driver.DriverError):
        driver.start_run(project, {"profile": "cases-only-v1", "docs": []})
    assert driver.main(["status", "--project", str(project), "--run", "0" * 32]) == 2


# --------------------------------------------------------------------------------------
# normalization: digests and order come from code
# --------------------------------------------------------------------------------------

def test_normalize_fragment_content_orders_and_numbers_model_arrays() -> None:
    answer = _saved("tc-generator.local-pilot.answer.json", {
        "requirement_id": "REQ-b-", "case_id": "TC-b-", "step_id": "STEP-b-", "expectation_id": "EXP-b-", "assertion_id": "ASSERT-b-",
        "source:1": "SRC-1", "source:2": "SRC-2"})
    normalized = driver.normalize_fragment_content(answer)
    # The model chooses the reading order; the numbers are mechanical and come from code.
    assert [row["requirement_id"] for row in normalized["requirements"]] == ["REQ-b-002", "REQ-b-001"]
    assert [row["display_order"] for row in normalized["requirements"]] == [1, 2]
    assert [case["display_order"] for case in normalized["test_cases"]] == [1, 2]
    assert all(step["display_order"] == 1 and step["expectations"][0]["display_order"] == 1 for case in normalized["test_cases"] for step in case["steps"])
    assert answer["test_cases"][0]["display_order"] == 99  # the input is not mutated
    shuffled = json.loads(json.dumps(answer))
    shuffled["test_cases"][0]["categories"] = ["functional", "negative", "functional"]
    assert driver.normalize_fragment_content(shuffled)["test_cases"][0]["categories"] == ["negative", "functional"]


def test_normalize_automation_fills_digests_lineage_and_canonical_order() -> None:
    from tests.test_automation_revision_budget import automated_document, automation
    from tools.automation_validation import automation_sha256, validate_automation_artifact
    from tools.canonical_document import document_sha256
    from tools.pipeline_driver_automation import normalize_automation

    document = automated_document()
    reference = automation(document)
    effective = {"document_digest": document_sha256(document), "effective_bundle_receipt_digest": "sha256:" + "0" * 64}
    content = {name: json.loads(json.dumps(reference["artifacts"][name])) for name in (
        "automation_status", "generated_files", "generated_symbols", "implementation_relations", "manual_dispositions", "diagnostics")}
    content["implementation_relations"].reverse()
    del content["generated_files"][0]["content_digest"]
    artifact = normalize_automation(content, document, revision=1, effective=effective, predecessor=None, previous_review=None)
    assert artifact == reference
    assert validate_automation_artifact(artifact, document) == []
    file_row = artifact["artifacts"]["generated_files"][0]
    assert file_row["content_digest"] == "sha256:" + hashlib.sha256(file_row["content"].encode("utf-8")).hexdigest()

    second = normalize_automation(content, document, revision=2, effective=effective, predecessor=reference, previous_review=None)
    assert second["artifacts"]["predecessor_automation_sha256"] == automation_sha256(reference)


def test_driver_runs_one_corrected_automation_revision_after_auto_fix(tmp_path: Path) -> None:
    from tools.automation_validation import automation_sha256, autotest_review_sha256
    from tools.pilot_state import read_execution_inputs, read_review_aggregate

    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage", "").startswith("autotest-reviewer:r1"))
    run_root = _run_root(project, task)
    first = json.loads((driver.work_dir(run_root) / "outputs" / f"{task['attempt_id'][:8]}.tc-to-autotest.r1.json").read_text(encoding="utf-8"))
    envelope = _load(task["inputs"][0])
    scope = envelope["scopes"][0]
    correction = {"id": "FIX-001", "correction_kind": "MECHANICAL", "path": "/artifacts/generated_files/0/content", "before": first["generated_files"][0]["content"],
                  "after": first["generated_files"][0]["content"] + "\n", "related_ids": [scope["scope_id"]], "description": "Убрать лишнее.",
                  "evidence": [scope["inputs"][0]["artifact_digest"]]}
    Path(task["output_path"]).write_text(json.dumps(review_answer(task, corrections=[correction]), ensure_ascii=False), encoding="utf-8")
    second = driver.submit(project, run_root, task["task_id"])
    assert second.get("status") != "rejected", second.get("errors")
    assert second["stage"] == "tc-to-autotest:r2" and second["automation_revision"] == 2
    assert any(path.endswith("automation-review-r1.json") for path in second["inputs"]) and any(path.endswith("automation-r1.json") for path in second["inputs"])
    done = _drive(project, second, model)
    assert (done["result"]["verification"], done["result"]["accepted"]) == ("PASS", True)
    published = read_execution_inputs(run_root, done["result"]["attempt_id"])["automation_artifact"]
    assert published["artifacts"]["automation_revision"] == 2
    assert published["artifacts"]["predecessor_automation_sha256"] == automation_sha256(_load(str(driver.work_dir(run_root) / "inputs" / "automation-r1.json")))
    assert published["artifacts"]["correction_review_sha256"] == autotest_review_sha256(read_review_aggregate(run_root, done["result"]["attempt_id"], "r1")["output"])


def test_next_resumes_after_interruption_between_materialization_and_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline

    project = _project(tmp_path, local=True)
    model = SavedModel("local-pilot-v1")
    task = _drive(project, _start(project, "local-pilot-v1"), model, until=lambda task: task.get("stage", "").startswith("autotest-reviewer:"))
    run_root = _run_root(project, task)
    Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
    original = run_pipeline.cmd_exec

    def interrupted(_arguments: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(run_pipeline, "cmd_exec", interrupted)
    with pytest.raises(KeyboardInterrupt):
        driver.submit(project, run_root, task["task_id"])
    assert (project / "tests" / "test_generated.py").is_file()
    monkeypatch.setattr(run_pipeline, "cmd_exec", original)
    done = driver.advance(project, run_root)
    assert done["action"] == "done" and (done["result"]["verification"], done["result"]["accepted"]) == ("PASS", True)


def test_generator_reads_every_context_portion_through_one_combined_receipt(tmp_path: Path) -> None:
    from tools.pilot_state import read_model_stage_artifact

    project = _project(tmp_path, local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["limits"] = {"context_batch_bytes": 1024}  # forces scan to split the context into several receipts
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    for index in range(3):
        (project / "src" / f"feature_{index}.py").write_text(f"VALUE_{index} = '" + "x" * 700 + "'\n", encoding="utf-8")
    model = SavedModel("cases-only-v1")
    first = _start(project, "cases-only-v1")
    run_root = _run_root(project, first)
    receipts = driver._context_receipts(run_root, first["attempt_id"])
    scanned = [receipt for receipt in receipts[:-1]]
    assert len(scanned) > 1  # scan really split the project
    everything = {item["project_path"] for receipt in scanned for item in receipt["files"]}
    combined = receipts[-1]
    assert {item["project_path"] for item in combined["files"]} == everything and not combined.get("gaps")

    task = _drive(project, first, model, until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    context_root = driver.work_dir(run_root) / "inputs" / "context"
    carried = {Path(path).relative_to(context_root).as_posix() for path in task["inputs"][2:]}
    assert carried == everything  # the generator sees the same files as the other roles
    assert task["input_bytes"] >= sum(Path(path).stat().st_size for path in task["inputs"][2:])
    assert "warnings" not in task and "context_files_not_in_this_task" not in _load(task["inputs"][0])

    # Asking again publishes nothing new, and the fragment is bound to the combined receipt.
    assert driver._generation_context(project, run_root, driver._attempt(run_root))["digest"] == combined["digest"]
    assert len(driver._context_receipts(run_root, first["attempt_id"])) == len(receipts)
    following = _answer_and_submit(project, task, model)
    assert following.get("status") != "rejected", following.get("errors")
    response = driver._stage_events(driver._events(run_root, task["attempt_id"]), task["stage"])["MODEL_RESPONSE_RECEIVED"]
    fragment = read_model_stage_artifact(run_root, task["attempt_id"], task["stage"], response["artifact_digest"])["artifact"]
    assert fragment["context_receipt_digest"] == combined["digest"]
    assert _drive(project, following, model)["result"]["completion"] == "COMPLETE"


def test_start_reports_scan_stop_as_done_error(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["limits"] = {"context_batch_bytes": 1}  # below the schema minimum
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    stopped = _start(project, "cases-only-v1")
    assert stopped["action"] == "done" and stopped["result"]["status"] == "error" and stopped["result"]["stage"] == "scan"
    assert driver._exit_code(stopped) == 2


def test_child_attempt_keeps_the_combined_context_receipt(tmp_path: Path) -> None:
    project = _project(tmp_path, local=True)
    cfg = json.loads((project/".skillsrc").read_text()); cfg["limits"] = {"context_batch_bytes": 1024}
    (project/".skillsrc").write_text(json.dumps(cfg)+"\n")
    for i in range(3): (project/"src"/f"feature_{i}.py").write_text(f"VALUE_{i} = '"+"x"*700+"'\n")
    model = SavedModel("local-pilot-v1", ["tc-to-autotest.broken-import.answer.json", "tc-to-autotest.passing.answer.json"])
    done = _drive(project, _start(project, "local-pilot-v1"), model, answers={"regenerate-after-gate": "regenerate"})
    assert done["result"]["verification"] == "PASS", done
    run_root = project/".pilot-runs"/done["run_id"]
    child = driver._attempt(run_root)
    assert child["parent_attempt_id"]
    parent_n = len(driver._context_receipts(run_root, child["parent_attempt_id"]))
    assert len(driver._context_receipts(run_root, child["attempt_id"])) == parent_n, (parent_n, len(driver._context_receipts(run_root, child["attempt_id"])))
