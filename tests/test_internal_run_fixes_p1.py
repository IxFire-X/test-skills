"""P1 of the internal run fixes (handoff/plan-internal-run-fixes.md, report 2026-10-15, sections 2.2–2.5 and 3).

P1a: the context marker reads the selected context batch by batch (one ``context-marker:batch-NNN`` task per scan receipt) and
the driver merges the answers deterministically into ``context-marker:baseline``; a project with one receipt (Petclinic) keeps the
single task.  P1b: a task whose input exceeds the budget stops before the model is called.  P1c–P1e: doctor names a missing
module, ``module_required`` lists the module ids, a ``.py`` runner CLI starts through an interpreter.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_review_fixes_driver import SavedModel, _answer_and_submit, _drive, _load, _project, _run_root, _start
from tools import context_marker_merge as merge
from tools import pipeline_driver as driver

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "driver — источники кода не анализировались детерминированно"


def _artifact(sources: list[str], warnings: list[str], gaps: list[dict[str, str]] | None = None, version: str = "5.0.0") -> dict[str, Any]:
    value: dict[str, Any] = {"schema_version": version, "stage": "context-marker",
                             "artifacts": {"analytics_documentation": {"requirements": [{"id": "SREQ-1", "text": "Каталог хранит товары"}]},
                                           "source_code_and_diff": {"sources": sources}},
                             "warnings": warnings}
    if gaps is not None:
        value["requirement_gaps"] = gaps
    return value


def _gap(requirement: str, missing: str, question: str = "Что делать?") -> dict[str, str]:
    return {"requirement": requirement, "location": "docs/feature.md — Каталог", "missing": missing, "blocks": "проверки каталога", "question": question}


# --------------------------------------------------------------------------------------
# P1a: the merge is a pure function of the batch artifacts
# --------------------------------------------------------------------------------------

def test_merge_keeps_batch_order_and_drops_duplicates() -> None:
    first = _artifact([PLACEHOLDER, "src/a.py — функция a", "src/common.py — общий код"], ["docs/feature.md — нет лимита", "часть 1 — код для SREQ-2 не найден"])
    second = _artifact(["src/b.py — функция b", "src/common.py  —  общий код"], ["docs/feature.md —  нет лимита", "часть 2 — код для SREQ-2 не найден"],
                       [_gap("SREQ-1", "нет формата цены")], version="5.1.0")
    third = _artifact([PLACEHOLDER, "src/b.py — функция b"], ["без разделителя"], [_gap("SREQ-1", "нет формата цены", "Другой вопрос?"), _gap("SREQ-3", "нет ролей")])
    merged = merge.merge_marker_artifacts([first, second, third])
    assert merged["schema_version"] == "5.1.0" and merged["stage"] == "context-marker"
    assert merged["artifacts"]["analytics_documentation"] == first["artifacts"]["analytics_documentation"]
    # The draft placeholder goes once a batch observed real code; the same observation counts once.
    assert merged["artifacts"]["source_code_and_diff"]["sources"] == ["src/a.py — функция a", "src/common.py — общий код", "src/b.py — функция b"]
    assert merged["warnings"] == ["docs/feature.md — нет лимита", "часть 1 — код для SREQ-2 не найден", "часть 2 — код для SREQ-2 не найден", "без разделителя"]
    assert merged["requirement_gaps"] == [_gap("SREQ-1", "нет формата цены"), _gap("SREQ-3", "нет ролей")]  # the first wording of a gap stays
    assert merge.merge_marker_artifacts([first, second, third]) == merged  # deterministic


def test_merge_keeps_the_placeholder_when_no_batch_saw_code_and_omits_absent_gaps() -> None:
    merged = merge.merge_marker_artifacts([_artifact([PLACEHOLDER], []), _artifact([PLACEHOLDER], [])])
    assert merged["artifacts"]["source_code_and_diff"]["sources"] == [PLACEHOLDER] and "requirement_gaps" not in merged


def test_merge_refuses_batches_with_different_requirements() -> None:
    changed = _artifact([], [])
    changed["artifacts"]["analytics_documentation"]["requirements"][0]["text"] = "другое"
    with pytest.raises(ValueError):
        merge.merge_marker_artifacts([_artifact([], []), changed])
    with pytest.raises(ValueError):
        merge.merge_marker_artifacts([])


# --------------------------------------------------------------------------------------
# P1a: the driver
# --------------------------------------------------------------------------------------

class BatchModel(SavedModel):
    """The saved answers, plus one observation of its own and one shared observation per marker batch."""

    def answer(self, task: dict[str, Any]) -> Any:
        if not task["stage"].startswith("context-marker:batch-"):
            return super().answer(task)
        self.stages.append(task["stage"])
        number = int(task["stage"].rsplit("-", 1)[1])
        value = _load(task["draft_path"])
        value["artifacts"]["source_code_and_diff"]["sources"] = [f"часть {number} — свои файлы", "src/sample.py — функция combine склеивает два значения через двоеточие"]
        value["warnings"] = ["docs/feature.md — не указан формат", f"часть {number} — код для SREQ-001 не найден"]
        return value


def _split_project(tmp_path: Path, files: int = 3) -> Path:
    project = _project(tmp_path, local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["limits"] = {"context_batch_bytes": 1024}  # forces scan to split the context into several receipts
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    for index in range(files):
        (project / "src" / f"feature_{index}.py").write_text(f"VALUE_{index} = '" + "x" * 900 + "'\n", encoding="utf-8")
    return project


def _requests(run_root: Path, attempt_id: str) -> dict[str, dict[str, Any]]:
    from tools.pilot_state import read_model_request

    rows = [event for event in driver._events(run_root, attempt_id) if event["event_type"] == "MODEL_REQUESTED"]
    return {event["stage_instance_id"]: dict(read_model_request(run_root, attempt_id, event["stage_instance_id"], event["artifact_digest"])) for event in rows}


def test_one_receipt_keeps_the_single_marker_task_and_its_request(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    model = SavedModel("cases-only-v1")
    first = _start(project, "cases-only-v1")
    assert first["action"] == "llm" and first["stage"] == "context-marker:baseline"
    run_root = _run_root(project, first)
    receipts = driver._context_receipts(run_root, first["attempt_id"])
    assert len(receipts) == 1
    baseline, _inventory = driver._baseline_and_inventory(run_root, driver._attempt(run_root))
    task = _drive(project, first, model, until=lambda task: task.get("stage", "").startswith("tc-generator:"))
    stages = _requests(run_root, task["attempt_id"])
    assert [stage for stage in stages if stage.startswith("context-marker")] == ["context-marker:baseline"]
    assert stages["context-marker:baseline"]["input_digests"] == [baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]]
    assert model.stages == ["context-marker:baseline"]


def test_marker_reads_the_context_batch_by_batch_and_the_driver_merges_the_answers(tmp_path: Path) -> None:
    from tools.pilot_state import read_model_stage_artifact

    project = _split_project(tmp_path)
    model = BatchModel("cases-only-v1")
    first = driver.start_run(project, {"profile": "cases-only-v1", "docs": ["docs/feature.md"], "subject": "Каталог товаров", "reviewer_isolation": "fresh",
                                       "model_id": "model-test", "review_mode": "pairs"}, max_tasks=2)
    run_root = _run_root(project, first)
    attempt_id = first["attempt_id"]
    receipts = driver._marker_receipts(run_root, attempt_id)
    count = len(receipts)
    assert count >= 3, count
    # Up to --max-tasks batch tasks at once, as review parts.
    assert first["action"] == "batch" and [task["stage"] for task in first["tasks"]] == ["context-marker:batch-001", "context-marker:batch-002"]
    context_root = driver.work_dir(run_root) / "inputs" / "context"
    seen: list[str] = []
    payload = first
    for _ in range(count + 2):
        tasks = payload["tasks"] if payload["action"] == "batch" else [payload]
        if not tasks[0].get("stage", "").startswith("context-marker:batch-"):
            break
        for task in tasks:
            number = int(task["stage"].rsplit("-", 1)[1])
            receipt = receipts[number - 1]
            carried = {Path(path).relative_to(context_root).as_posix() for path in task["inputs"][1:]}
            assert carried == {item["project_path"] for item in receipt["files"]}  # only the files of its own batch
            assert task["input_bytes"] <= max(1024, int(receipt["byte_count"])) + Path(task["inputs"][0]).stat().st_size  # context_batch_bytes + draft
            assert f"часть {number} из {count}" in task["instructions"] and f"«часть {number} — код для <требование> не найден»" in task["instructions"]
            seen.append(task["stage"])
            answered = _answer_and_submit(project, task, model)
            assert answered.get("status") != "rejected", answered.get("errors")
        payload = driver.advance(project, run_root, max_tasks=2)
    assert seen == [f"context-marker:batch-{index:03d}" for index in range(1, count + 1)]
    assert payload["action"] == "llm" and payload["stage"].startswith("tc-generator:")

    requests = _requests(run_root, attempt_id)
    events = driver._events(run_root, attempt_id)
    answers = [driver._stage_events(events, f"context-marker:batch-{index:03d}")["MODEL_RESPONSE_RECEIVED"]["artifact_digest"] for index in range(1, count + 1)]
    baseline, _inventory = driver._baseline_and_inventory(run_root, driver._attempt(run_root))
    assert requests["context-marker:baseline"]["input_digests"] == [baseline["requirements"]["digest"], baseline["inventory_digest"],
                                                                    *[receipt["digest"] for receipt in receipts], *answers]
    assert requests["context-marker:baseline"]["invocation_id"].startswith("context-merge-")
    for index, receipt in enumerate(receipts, 1):
        assert requests[f"context-marker:batch-{index:03d}"]["input_digests"][2:] == [receipt["digest"]]
    marker = driver._marker_artifact(run_root, attempt_id)["artifact"]
    parts = [read_model_stage_artifact(run_root, attempt_id, f"context-marker:batch-{index:03d}", digest)["artifact"] for index, digest in enumerate(answers, 1)]
    assert marker == merge.merge_marker_artifacts(parts)
    assert marker["artifacts"]["source_code_and_diff"]["sources"] == [
        "часть 1 — свои файлы", "src/sample.py — функция combine склеивает два значения через двоеточие", *[f"часть {index} — свои файлы" for index in range(2, count + 1)]]
    assert marker["warnings"][0] == "docs/feature.md — не указан формат" and marker["warnings"].count("docs/feature.md — не указан формат") == 1
    assert _drive(project, payload, model)["result"]["completion"] == "COMPLETE"


def test_the_journal_binds_the_merged_marker_to_every_batch_answer(tmp_path: Path) -> None:
    from tools.pilot_state import publish_model_request, publish_model_stage_artifact

    project = _split_project(tmp_path)
    model = BatchModel("cases-only-v1")
    first = driver.start_run(project, {"profile": "cases-only-v1", "docs": ["docs/feature.md"], "subject": "Каталог товаров", "reviewer_isolation": "fresh",
                                       "model_id": "model-test", "review_mode": "pairs"}, max_tasks=16)
    run_root = _run_root(project, first)
    attempt_id = first["attempt_id"]
    receipts = driver._marker_receipts(run_root, attempt_id)
    config = driver._config(run_root)
    attempt = driver._attempt(run_root)
    assert len(first["tasks"]) == len(receipts)
    for task in first["tasks"]:
        Path(task["output_path"]).write_text(json.dumps(model.answer(task), ensure_ascii=False), encoding="utf-8")
        value = _load(task["output_path"])
        driver._submit_context_marker(project, run_root, attempt, config, task, value)  # the answer only, no merge yet
    events = driver._events(run_root, attempt_id)
    answers = [driver._stage_events(events, f"context-marker:batch-{index:03d}")["MODEL_RESPONSE_RECEIVED"]["artifact_digest"] for index in range(1, len(receipts) + 1)]
    baseline, _inventory = driver._baseline_and_inventory(run_root, attempt)
    head = [baseline["requirements"]["digest"], baseline["inventory_digest"], *[receipt["digest"] for receipt in receipts]]
    with pytest.raises(ValueError):  # the answers in another order
        publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="model-test", invocation_id="context-merge-x", input_digests=[*head, *reversed(answers)])
    with pytest.raises(ValueError):  # one answer missing
        publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="model-test", invocation_id="context-merge-x", input_digests=[*head, *answers[:-1]])
    publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="model-test", invocation_id="context-merge-x", input_digests=[*head, *answers])
    merged = driver._merged_marker(run_root, attempt_id, len(receipts))
    forged = copy.deepcopy(merged)
    forged["warnings"].append("подложено — не из ответа батча")
    with pytest.raises(ValueError):
        publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", forged)
    with pytest.raises(ValueError):  # a batch request after the merged request
        publish_model_request(run_root, attempt_id, f"context-marker:batch-{len(receipts) + 1:03d}", model_id="model-test", invocation_id="context-y",
                              input_digests=head[:3])
    publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", merged)
    assert driver._marker_artifact(run_root, attempt_id)["artifact"] == merged


# --------------------------------------------------------------------------------------
# P1b: the input budget stops the run before the model call
# --------------------------------------------------------------------------------------

def _no_model_request(run_root: Path) -> bool:
    from tools.pilot_state import derive_state

    return not [event for event in derive_state(run_root)["events"] if event["event_type"] == "MODEL_REQUESTED"]


def test_a_task_over_the_input_budget_stops_before_the_model_call(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    with pytest.raises(driver.DriverError) as raised:
        _start(project, "cases-only-v1", task_input_bytes=100)
    error = raised.value
    assert error.code == "TASK_INPUT_TOO_LARGE"
    row = error.diagnostics[0]
    assert row["stage"] == "context-marker:baseline" and row["input_bytes"] > row["threshold"] == 100
    assert "--target" in row["hint"] and "paths" in row["hint"] and "context_batch_bytes" in row["hint"]
    run_root = next((project / ".pilot-runs").iterdir())
    assert _no_model_request(run_root)


def test_the_input_budget_comes_from_skillsrc_limits_and_the_cli_prints_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path, local=False)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["limits"] = {"task_input_bytes": 1024}
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False) + "\n", encoding="utf-8")
    (project / "src" / "big.py").write_text("DATA = '" + "x" * 3000 + "'\n", encoding="utf-8")
    code = driver.main(["next", "--project", str(project), "--profile", "cases-only-v1", "--docs", "docs/feature.md", "--subject", "Каталог товаров",
                        "--reviewer-isolation", "fresh", "--model-id", "model-test"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2 and payload["action"] == "error" and payload["code"] == "TASK_INPUT_TOO_LARGE"
    assert payload["errors"][0]["threshold"] == 1024 and payload["errors"][0]["input_bytes"] > 1024
    assert _no_model_request(next((project / ".pilot-runs").iterdir()))


def test_the_default_input_budget_lets_a_small_project_through(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    task = _start(project, "cases-only-v1")
    assert task["action"] == "llm" and driver._task_input_budget(project, driver._config(_run_root(project, task))) == 1_000_000


# --------------------------------------------------------------------------------------
# P1c–P1e
# --------------------------------------------------------------------------------------

_BLOCKED = """
import importlib.abc, importlib.machinery, json, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'jsonschema', 'yaml'}:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return None
sys.meta_path.insert(0, Block())
import importlib.util
real = importlib.util.find_spec
importlib.util.find_spec = lambda name, *rest: None if name in {'jsonschema', 'yaml'} else real(name, *rest)
sys.path.insert(0, sys.argv[1])
from tools.doctor import inspect_environment
print(json.dumps(inspect_environment(__import__('pathlib').Path(sys.argv[1])), ensure_ascii=False))
"""


def test_doctor_names_a_missing_module_instead_of_a_broken_contract() -> None:
    completed = subprocess.run([sys.executable, "-c", _BLOCKED, str(ROOT)], capture_output=True, text=True, encoding="utf-8", check=True,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    report = json.loads(completed.stdout)
    assert report["status"] == "NOT_RUNNABLE"
    assert "invalid:pipeline_contract" not in report["integrity"]["missing"] and "invalid:release/manifest.json" not in report["integrity"]["missing"]
    assert any("jsonschema" in message and "не установлен" in message for message in report["messages"])
    assert any("yaml" in message for message in report["messages"])


def test_doctor_has_no_messages_when_everything_is_installed() -> None:
    from tools.doctor import inspect_environment

    assert inspect_environment(ROOT)["messages"] == []


def test_module_required_lists_the_module_ids() -> None:
    from tools.skillsrc_manifest import SkillsrcError, select_module

    with pytest.raises(SkillsrcError) as raised:
        select_module({"modules": [{"id": "api"}, {"id": "core"}]}, None)
    assert raised.value.code == "module_required" and "api" in str(raised.value) and "core" in str(raised.value) and "--module" in str(raised.value)
    assert raised.value.details == [{"path": "/modules", "ids": ["api", "core"]}]


def test_a_python_runner_cli_starts_through_an_interpreter(tmp_path: Path) -> None:
    from tools import model_runner

    script = tmp_path / "wrap.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    config = model_runner.configure({}, preset="codex", cli=str(script))
    argv = model_runner.build_argv(config, model="m", schema={"type": "object"}, directory=tmp_path / "run")
    assert argv[:3] == [sys.executable, str(script), "exec"]
    chosen = model_runner.configure({"review_runner": {"preset": "codex", "interpreter": sys.executable}}, cli=str(script))
    assert chosen.interpreter == sys.executable
    assert model_runner.build_argv(chosen, model="m", schema={"type": "object"}, directory=tmp_path / "run")[:2] == [sys.executable, str(script)]
    native = model_runner.configure({}, preset="codex", cli=str(tmp_path / "codex.exe"))
    assert model_runner.build_argv(native, model="m", schema={"type": "object"}, directory=tmp_path / "run")[0] == str(tmp_path / "codex.exe")
    with pytest.raises(model_runner.RunnerError):
        model_runner.configure({"review_runner": {"preset": "codex", "interpreter": "python3"}}, cli=str(script))  # a project file names an absolute path


def test_skillsrc_schema_accepts_the_new_fields() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "schemas" / "skillsrc.schema.json").read_text(encoding="utf-8"))
    limits = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], **schema["$defs"]["limits"]}
    runner = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], **schema["$defs"]["review_runner"]}
    assert Draft202012Validator(limits).is_valid({"task_input_bytes": 1_000_000})
    assert not Draft202012Validator(limits).is_valid({"task_input_bytes": 10})
    assert Draft202012Validator(runner).is_valid({"preset": "codex", "interpreter": "/usr/bin/python3"})
