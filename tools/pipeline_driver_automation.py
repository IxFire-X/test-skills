"""`local-pilot-v1` half of the pipeline driver: automation, static review, execution.

Kept apart from :mod:`tools.pipeline_driver` only for size.  The contract is the
same: deterministic steps run in code, the model answers one task at a time.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from tools import pipeline_driver as driver
from tools.automation_parts import AUTOMATION_FIELDS as _AUTOMATION_FIELDS, normalize_automation  # noqa: F401  (normalize_automation is public here too)
from tools.pipeline_driver import DriverError, ROOT
_REGENERATION_REASON = "GENERATED_TEST_INVALID"


def _automation_schema() -> dict[str, Any]:
    schema = json.loads((ROOT / "schemas" / "tc-to-autotest-output.schema.json").read_text(encoding="utf-8"))
    artifacts = deepcopy(schema["$defs"]["artifacts"])
    properties = {name: artifacts["properties"][name] for name in _AUTOMATION_FIELDS}
    file_schema = deepcopy(schema["$defs"]["generated_file"])
    file_schema["required"] = [name for name in file_schema["required"] if name != "content_digest"]
    file_schema["properties"].pop("content_digest", None)
    definitions = {**schema["$defs"], "generated_file": file_schema}
    return {"$schema": schema.get("$schema"), "type": "object", "additionalProperties": False,
            "required": list(_AUTOMATION_FIELDS), "properties": {**properties, "warnings": schema["properties"]["warnings"]}, "$defs": definitions,
            "x-note": "source, automation_revision, lineage digests, content_digest and the order of relations are filled by the driver on submit."}


def _stage_artifact(run_root: Path, attempt_id: str, stage: str) -> dict[str, Any] | None:
    from tools.pilot_state import read_model_stage_artifact

    response = driver._stage_events(driver._events(run_root, attempt_id), stage).get("MODEL_RESPONSE_RECEIVED")
    if response is None:
        return None
    return dict(read_model_stage_artifact(run_root, attempt_id, stage, response["artifact_digest"]))


def _review_output(run_root: Path, attempt_id: str, key: str) -> dict[str, Any] | None:
    from tools.pilot_state import read_review_aggregate

    try:
        return dict(read_review_aggregate(run_root, attempt_id, key)["output"])
    except (KeyError, TypeError, ValueError):
        return None


def _module_root(project: Path, attempt: Mapping[str, Any]) -> Path:
    module = str(attempt["module"])
    return project if module == "." else project.joinpath(*module.split("/"))


def _automation_task(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any], revision: int) -> dict[str, Any]:
    from tools.pilot_state import publish_model_request

    attempt_id = str(attempt["attempt_id"])
    stage = f"tc-to-autotest:r{revision}"
    inputs = _automation_inputs(project, run_root, attempt, config, effective, revision)
    if "MODEL_REQUESTED" not in driver._stage_events(driver._events(run_root, attempt_id), stage):
        driver._check_task_input(project, config, stage, inputs)
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"automation-{uuid.uuid4().hex[:16]}",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"]])
    return driver._llm_task(
        run_root, attempt_id, f"tc-to-autotest.r{revision}", stage=stage, skill="tc-to-autotest", inputs=inputs, schema=_automation_schema(),
        instructions=_AUTOMATION_INSTRUCTIONS, extra={"automation_revision": revision},
    )


_AUTOMATION_INSTRUCTIONS = ("Первый файл — задание, второй — принятый набор кейсов (единственный источник смысла). Верни automation_status, generated_files "
                            "(file_id, path относительно корня модуля, language, framework, content), generated_symbols, implementation_relations, "
                            "manual_dispositions и diagnostics. Дайджесты, source, номер ревизии и порядок массивов заполнит драйвер.")
_DEFAULT_CASES_PER_PART = 12


def _cases_per_part(project: Path) -> int:
    """P2: ``.skillsrc`` ``limits.automation_cases_per_part``, else 12; 0 = no case limit."""
    from tools.human_gates import _skillsrc

    limits = _skillsrc(project).get("limits")
    value = limits.get("automation_cases_per_part") if isinstance(limits, Mapping) else None
    return value if type(value) is int and value >= 0 else _DEFAULT_CASES_PER_PART


def _part_plan(project: Path, run_root: Path, attempt: Mapping[str, Any], config: dict[str, Any], effective: Mapping[str, Any], revision: int) -> dict[str, Any] | None:
    """The part plan of a revision, published once; None when the single task fits both limits (the behaviour so far)."""
    from tools import automation_parts as parts
    from tools.pilot_state import publish_automation_part_plan

    key = f"r{revision}"
    known = (config.get("automation_plans") or {}).get(key)
    if known:
        return dict(known)
    document = effective["document"]
    inputs = _automation_inputs(project, run_root, attempt, config, effective, revision)
    single = sum(Path(path).stat().st_size for path in inputs if Path(path).is_file())
    budget, limit, count = driver._task_input_budget(project, config), _cases_per_part(project), len(document.get("test_cases", []))
    if single <= budget and (limit == 0 or count <= limit):
        return None
    document_path = driver.work_dir(run_root) / "inputs" / "effective-canonical.json"
    # Context, brief and the revision's extra inputs travel with every part; the brief of a part is a little longer.
    fixed = single - document_path.stat().st_size + 2_048
    header = len(parts.part_document_bytes(document, []))
    if fixed + header >= budget:
        raise DriverError("TASK_INPUT_TOO_LARGE", f"the context and the brief of an automation part need {fixed + header} bytes, over the task input "
                          f"budget of {budget} bytes", [{"code": "TASK_INPUT_TOO_LARGE", "stage": f"tc-to-autotest:{key}", "input_bytes": fixed + header,
                                                       "threshold": budget, "hint": "Сузьте контекст: --target <путь> или paths/exclusions модуля в .skillsrc; "
                                                       "либо поднимите --task-input-bytes под окно модели."}])
    try:
        plan = parts.plan_parts(document, budget - fixed, cases_per_part=limit)
    except parts.PartPlanError as error:
        raise DriverError(error.code, str(error)) from error
    receipt = {**parts.plan_receipt(document, plan, fixed_bytes=fixed, budget=budget - fixed, cases_per_part=limit, single_task_input_bytes=single),
               "revision": key}
    digest = publish_automation_part_plan(run_root, str(attempt["attempt_id"]), receipt)
    driver._log(driver._log_path(project, run_root.name), {"event": "automation_parts", "revision": key, "parts": receipt["parts"],
                                                           "total_input_bytes": receipt["total_input_bytes"], "single_task_input_bytes": single})
    config.setdefault("automation_plans", {})[key] = {**receipt, "digest": digest}
    driver._save_config(run_root, config)
    return dict(config["automation_plans"][key])


def _part_stage(key: str, part_id: str) -> str:
    return f"tc-to-autotest:{key}:{part_id}"


def _automation_part_task(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any],
                          revision: int, plan: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """One part: its brief, its part document (only its cases) and the context; r2 adds its r1 answer and its corrections."""
    from tools import automation_parts as parts
    from tools.pilot_state import publish_model_request

    attempt_id, key = str(attempt["attempt_id"]), f"r{revision}"
    stage = _part_stage(key, row["part_id"])
    directory = driver.work_dir(run_root) / "inputs" / "automation-parts"
    brief = {**parts.part_brief(row, count=int(plan["parts"])), "automation_revision": revision, "module": attempt["module"],
             "module_root": str(_module_root(project, attempt))}
    extra_inputs: list[Path] = []
    if revision == 2:
        previous = _part_artifact(run_root, attempt_id, "r1", row["part_id"])
        review = _review_output(run_root, attempt_id, "r1")
        extra_inputs.append(driver._write_json(directory / f"r1-{row['part_id']}.json", previous["artifact"]))
        extra_inputs.append(driver._write_json(directory / f"r1-{row['part_id']}-corrections.json",
                                               parts.corrections_of_part(review, effective["document"], row)))
        brief["correction"] = "Примени corrections из ревью r1, относящиеся к твоей части, и верни полный исправленный набор файлов своей части."
    brief_path = driver._write_json(directory / f"{key}-{row['part_id']}-brief.json", brief)
    document_path = directory / f"{key}-{row['part_id']}-document.json"
    document_path.write_bytes(parts.part_document_bytes(effective["document"], row["case_ids"]))
    inputs = [brief_path, document_path, *extra_inputs, *driver._context_inputs(project, run_root, attempt)]
    if "MODEL_REQUESTED" not in driver._stage_events(driver._events(run_root, attempt_id), stage):
        driver._check_task_input(project, config, stage, inputs)
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"automation-{uuid.uuid4().hex[:16]}",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"], plan["digest"], row["document_digest"]])
    return driver._llm_task(
        run_root, attempt_id, f"tc-to-autotest.{key}.{row['part_id']}", stage=stage, skill="tc-to-autotest", inputs=inputs, schema=_automation_schema(),
        instructions=_AUTOMATION_INSTRUCTIONS + " " + brief["instructions"],
        extra={"automation_revision": revision, "part_id": row["part_id"], "plan_digest": plan["digest"]},
    )


def _part_artifact(run_root: Path, attempt_id: str, key: str, part_id: str) -> dict[str, Any] | None:
    return _stage_artifact(run_root, attempt_id, _part_stage(key, part_id))


def _lineage(run_root: Path, attempt_id: str, revision: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if revision != 2:
        return None, None
    return _stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"], _review_output(run_root, attempt_id, "r1")


def _carry_part(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any],
                plan: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    """r2: a part no r1 correction names is carried over unchanged, without a model call (the journal checks the equality)."""
    from tools import automation_parts as parts
    from tools.pilot_state import publish_model_request, publish_model_stage_artifact

    attempt_id = str(attempt["attempt_id"])
    stage = _part_stage("r2", row["part_id"])
    previous = _part_artifact(run_root, attempt_id, "r1", row["part_id"])["artifact"]
    predecessor, review = _lineage(run_root, attempt_id, 2)
    artifact = normalize_automation(parts.answer_of(previous), effective["document"], revision=2, effective=effective,
                                    predecessor=predecessor, previous_review=review)
    if "MODEL_REQUESTED" not in driver._stage_events(driver._events(run_root, attempt_id), stage):
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"automation-carry-{uuid.uuid4().hex[:16]}",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"], plan["digest"], row["document_digest"]])
    publish_model_stage_artifact(run_root, attempt_id, stage, artifact)
    driver._log(driver._log_path(project, run_root.name), {"event": "automation_part_carried", "part_id": row["part_id"]})


def _merged_automation(run_root: Path, attempt_id: str, key: str) -> dict[str, Any]:
    from tools import automation_parts as parts
    from tools.pilot_state import read_effective_canonical

    config = driver._config(run_root)
    plan = config["automation_plans"][key]
    effective = dict(read_effective_canonical(run_root, attempt_id))
    predecessor, review = _lineage(run_root, attempt_id, int(key[1]))
    answers = [_part_artifact(run_root, attempt_id, key, row["part_id"])["artifact"] for row in plan["rows"]]
    return parts.merged_artifact(answers, effective["document"], revision=int(key[1]), effective=effective, predecessor=predecessor, previous_review=review)


def _request_merged_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], key: str) -> None:
    from tools.pilot_state import publish_model_request, read_effective_canonical

    attempt_id = str(attempt["attempt_id"])
    stage = f"tc-to-autotest:{key}"
    if "MODEL_REQUESTED" in driver._stage_events(driver._events(run_root, attempt_id), stage):
        return
    plan = config["automation_plans"][key]
    effective = read_effective_canonical(run_root, attempt_id)
    events = driver._events(run_root, attempt_id)
    answers = [driver._stage_events(events, _part_stage(key, row["part_id"]))["MODEL_RESPONSE_RECEIVED"]["artifact_digest"] for row in plan["rows"]]
    publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"automation-merge-{uuid.uuid4().hex[:16]}",
                          input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"], plan["digest"], *answers])


def _publish_merged_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], key: str) -> None:
    from tools.automation_validation import validate_automation_artifact
    from tools.pilot_state import publish_model_stage_artifact, read_effective_canonical

    attempt_id = str(attempt["attempt_id"])
    merged = _merged_automation(run_root, attempt_id, key)
    rows = validate_automation_artifact(merged, read_effective_canonical(run_root, attempt_id)["document"])
    if rows:
        raise DriverError("AUTOMATION_PARTS_INVALID", "the merged automation is invalid", [
            {"path": str(row.get("path", "")), "code": row.get("code", ""), "message": row.get("message", "")} for row in rows])
    publish_model_stage_artifact(run_root, attempt_id, f"tc-to-autotest:{key}", merged)
    driver._log(driver._log_path(project, run_root.name), {"event": "automation_merged", "revision": key, "parts": len(driver._config(run_root)["automation_plans"][key]["rows"])})


def _automation_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: dict[str, Any], effective: Mapping[str, Any],
                     revision: int) -> dict[str, Any] | None:
    """The automation task(s) of a revision, or None once its artifact is published.

    Within both limits (task_input_bytes, automation_cases_per_part): the single task, as before.  Otherwise one task per
    part, up to ``--max-tasks`` at once; in r2 a part no correction names is carried over; then the driver merges.
    """
    from tools import automation_parts as parts

    attempt_id, key = str(attempt["attempt_id"]), f"r{revision}"
    if _stage_artifact(run_root, attempt_id, f"tc-to-autotest:{key}") is not None:
        return None
    plan = _part_plan(project, run_root, attempt, config, effective, revision)
    if plan is None:
        return _automation_task(project, run_root, attempt, config, effective, revision)
    rows = list(plan["rows"])
    if revision == 2:
        review = _review_output(run_root, attempt_id, "r1")
        for row in rows:
            if _part_artifact(run_root, attempt_id, key, row["part_id"]) is None and not parts.corrections_of_part(review, effective["document"], row):
                _carry_part(project, run_root, attempt, config, effective, plan, row)
    open_rows = [row for row in rows if _part_artifact(run_root, attempt_id, key, row["part_id"]) is None]
    if not open_rows:
        _request_merged_automation(project, run_root, attempt, config, key)
        _publish_merged_automation(project, run_root, attempt, key)
        return None
    limit = max(1, int(config.get("_max_tasks") or 1))
    tasks = [_automation_part_task(project, run_root, attempt, config, effective, revision, plan, row) for row in open_rows[:limit]]
    if len(tasks) == 1:
        return tasks[0]
    return {"action": "batch", "run_id": run_root.name, "attempt_id": attempt_id, "tasks": tasks,
            "instructions": "Независимые части автоматизации: выполни каждую задачу в отдельном свежем вызове, затем отправь ответы по одному."}


def _automation_inputs(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any],
                       revision: int) -> list[Path]:
    """The inputs of the single automation task (written to the driver's inputs)."""
    attempt_id = str(attempt["attempt_id"])
    directory = driver.work_dir(run_root)
    document_path = driver._write_json(directory / "inputs" / "effective-canonical.json", effective["document"])
    inputs = [document_path]
    brief: dict[str, Any] = {
        "automation_revision": revision, "module": attempt["module"], "module_root": str(_module_root(project, attempt)),
        "filled_by_driver": ["schema_version", "stage", "source", "automation_revision", "predecessor_automation_sha256", "correction_review_sha256",
                             "generated_files[].content_digest", "order of implementation_relations and manual_dispositions"],
    }
    if revision == 2:
        previous = _stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")
        review = _review_output(run_root, attempt_id, "r1")
        inputs.append(driver._write_json(directory / "inputs" / "automation-r1.json", previous["artifact"]))
        inputs.append(driver._write_json(directory / "inputs" / "automation-review-r1.json", review))
        brief["correction"] = "Примени все corrections из ревью r1 и верни полный исправленный набор файлов."
    hint = config.get("regeneration_hint")
    if isinstance(hint, Mapping) and hint.get("attempt_id") == attempt.get("parent_attempt_id"):
        brief["previous_gate_failure"] = dict(hint)
        for name in hint.get("output_paths", []):
            if Path(name).is_file():
                inputs.append(Path(name))
    inputs.insert(0, driver._write_json(directory / "inputs" / f"automation-r{revision}-brief.json", brief))
    inputs.extend(driver._context_inputs(project, run_root, attempt))
    return inputs



def submit_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], task: Mapping[str, Any], value: Any) -> None:
    from tools.automation_validation import validate_automation_artifact
    from tools.pilot_state import publish_model_stage_artifact, read_effective_canonical
    from tools.schema_validation import schema_diagnostics

    if not isinstance(value, dict):
        raise DriverError("TASK_OUTPUT_INVALID", "automation output must be a JSON object")
    unknown = sorted(set(value) - set(_AUTOMATION_FIELDS) - {"warnings"})
    if unknown:
        raise DriverError("TASK_OUTPUT_INVALID", "automation output has fields the driver owns", [
            {"path": "/" + name, "code": "DRIVER_OWNED_FIELD", "message": "Remove this field: the driver fills service fields and digests."} for name in unknown])
    attempt_id = str(attempt["attempt_id"])
    revision = int(task["automation_revision"])
    effective = dict(read_effective_canonical(run_root, attempt_id))
    predecessor, previous_review = _lineage(run_root, attempt_id, revision)
    document = effective["document"]
    artifact = normalize_automation(value, document, revision=revision, effective=effective, predecessor=predecessor, previous_review=previous_review)
    if task.get("part_id"):
        # P2: a part answers for its cases only: its prefix, marker and cases, then the validator without the other parts' coverage.
        from tools import automation_parts as parts

        row = next(item for item in driver._config(run_root)["automation_plans"][f"r{revision}"]["rows"] if item["part_id"] == task["part_id"])
        rows = (parts.part_answer_problems(row["part_id"], row["case_ids"], value)
                or schema_diagnostics(artifact, ROOT / "schemas" / "tc-to-autotest-output.schema.json", ROOT)
                or parts.validate_part_artifact(artifact, document, row["case_ids"]))
    else:
        rows = schema_diagnostics(artifact, ROOT / "schemas" / "tc-to-autotest-output.schema.json", ROOT) or validate_automation_artifact(artifact, document)
    if rows:
        raise DriverError("TASK_OUTPUT_INVALID", "automation artifact is invalid", [
            {"path": str(row.get("path", "")).replace("/artifacts/", "/", 1), "code": row.get("code", ""), "message": row.get("message", "")} for row in rows])
    publish_model_stage_artifact(run_root, attempt_id, str(task["stage"]), artifact, transport_attempts=int(task.get("transport_attempts", 1)))


def _finalize_rejected_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], effective: Mapping[str, Any], reason: str) -> None:
    from tools.finalize_attempt import finalize_attempt

    facts = driver._terminal_facts(run_root, attempt, document=effective["document"])
    facts.update({"completion": "PARTIAL", "coverage": None, "reason_code": reason, "prior_stage_cause": reason})
    finalize_attempt({"attempt_id": attempt["attempt_id"], "evidence": {}, "stage_causes": [reason]}, None, project=project,
                     verification="NOT_APPLICABLE", facts=facts, run_root=run_root)


def _materialize(project: Path, run_root: Path, attempt: Mapping[str, Any], effective: Mapping[str, Any],
                 automation: Mapping[str, Any], review: Mapping[str, Any], key: str) -> None:
    """Write the accepted files into the project once; nothing runs yet."""
    from tools.generated_delta import materialize_delta
    from tools.pilot_state import read_attempt_receipt

    attempt_id = str(attempt["attempt_id"])
    baseline, _inventory = driver._baseline_and_inventory(run_root, attempt)
    try:
        read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
    except (KeyError, TypeError, ValueError):
        # Not materialized yet.  After an interrupted `next` the delta is already durable
        # and `exec` resumes the started execution instead of writing files again.
        history: dict[str, Any] = {}
        if key == "r2":
            history = {"automation_history": [_stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"]],
                       "review_history": [_review_output(run_root, attempt_id, "r1")]}
        materialize_delta(project, _module_root(project, attempt), baseline, automation, review, canonical_document=effective["document"],
                          run_root=run_root, attempt_id=attempt_id, **history)


def _approval_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any] | None:
    """S3: with execution.review human the build and the tests start only after a person approved the exact written files.

    The question lists every generated file with its content digest; the one accepted answer is the digest of that list,
    so an approval never covers other files or other bytes.  None when the run needs no approval or it is recorded.
    """
    from tools.human_gates import file_rows
    from tools.pilot_state import execution_review_required, generated_tests_approval_digest, read_attempt_receipt, record_execution_approval

    attempt_id = str(attempt["attempt_id"])
    if not execution_review_required(run_root):
        return None
    if any(event["event_type"] in {"EXECUTION_APPROVED", "EXECUTION_STARTED"} for event in driver._events(run_root, attempt_id)):
        return None
    digest = generated_tests_approval_digest(run_root, attempt_id)
    task_id = driver._task_id(attempt_id, "ask.approve-generated-tests")
    if config.get("answers", {}).get(task_id) == digest:
        record_execution_approval(run_root, attempt_id, digest)
        driver._log(driver._log_path(project, run_root.name), {"event": "execution_approved", "attempt_id": attempt_id, "approval_digest": digest})
        return None
    files = file_rows(read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]["files"])
    listing = "; ".join(f"{row['path']} ({row['content_digest']})" for row in files)
    return driver._ask_task(
        run_root, attempt_id, "approve-generated-tests",
        f"Сгенерированные тесты записаны в проект и ещё не запускались: {listing}. Проверьте эти файлы. Одобрение запускает сборку, "
        f"тесты{' и мутации' if config.get('mutation') else ''} проекта на этих байтах: ответ approve-generated-tests={digest}. "
        "Пока ответа нет, ничего не запускается; правка файла меняет дайджест — тогда нужен новый ответ.",
        [{"value": digest, "label": f"Одобрить запуск {len(files)} файл(ов) с этими дайджестами"}],
        extra={"files": files, "approval_digest": digest})


def _execute(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any],
             automation: Mapping[str, Any], review: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Run the materialized files once through the existing ``exec`` command."""
    from tools import run_pipeline
    from tools.pilot_state import read_attempt_receipt, read_run

    attempt_id = str(attempt["attempt_id"])
    delta = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]
    boundary = read_attempt_receipt(run_root, attempt_id, f"automation-review-boundary-{key}", "ARTIFACT_READ_BACK")["record"]
    authorization = dict(read_run(run_root)["authorization"])
    carriers = driver.work_dir(run_root) / "carriers"
    paths = {
        "canonical_document": driver._write_json(carriers / "canonical-document.json", effective["document"]),
        "automation_artifact": driver._write_json(carriers / "automation-artifact.json", automation),
        "autotest_review": driver._write_json(carriers / "autotest-review.json", review),
        "authorization_receipt": driver._write_json(carriers / "authorization-receipt.json", authorization),
        "host_isolation_receipt": driver._write_json(carriers / "host-isolation-receipt.json", boundary),
        "generated_delta_receipt": driver._write_json(carriers / "generated-delta-receipt.json", delta),
    }
    human = dict(config.get("human_report") or {})
    arguments = SimpleNamespace(project=str(project), run=run_root.name, module=config.get("module_id"), language=None, target=config.get("target"),
                                executor="local", docs=None, junit=list(human.get("junit") or []), exit_code=human.get("exit_code"),
                                gate_exit_code=human.get("gate_exit_code"), **{name: str(path) for name, path in paths.items()})
    driver._log(driver._log_path(project, run_root.name), {"event": "execution_launch", "attempt_id": attempt_id, "build_offline": _build_offline(project, attempt),
                                                           **({"execution_mode": "human", "reports": len(arguments.junit)} if human or config.get("execution_mode") == "human" else {})})
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = run_pipeline.cmd_exec(arguments)
    try:
        output = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        output = {"status": "error", "raw": buffer.getvalue()[-2000:]}
    if output.get("status") == "human_exec" or output.get("reason") == "HUMAN_REPORT_INVALID":
        # S9: nothing ran or nothing was accepted yet; the attempt waits for the person's reports.
        if human:
            config = driver._config(run_root)
            config.pop("human_report", None)
            driver._save_config(run_root, config)
        if output.get("reason") == "HUMAN_REPORT_INVALID":
            raise DriverError("HUMAN_REPORT_INVALID", str(output.get("message")))
        return {"exit_code": code, "output": output, "human_exec": True}
    driver._write_json(_execution_path(run_root, attempt_id), {"exit_code": code, "output": output})
    return {"exit_code": code, "output": output}


def _build_offline(project: Path, attempt: Mapping[str, Any]) -> bool:
    """S2a: whether the module's builds run offline (`.skillsrc` test.offline); the receipt's argv is the proof."""
    from tools.skillsrc_manifest import SkillsrcError, load_module_by_root

    try:
        return (load_module_by_root(project, str(attempt["module"])).get("test") or {}).get("offline") is True
    except (SkillsrcError, OSError, KeyError, ValueError):
        return False


def _execution_path(run_root: Path, attempt_id: str) -> Path:
    return driver.work_dir(run_root) / "artifacts" / f"exec-output-{attempt_id[:8]}.json"


def terminal_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """A terminal local attempt: report it, or offer the one allowed regeneration."""
    path = _execution_path(run_root, str(attempt["attempt_id"]))
    if not path.is_file():
        return driver._done(run_root, driver._result_summary(run_root, attempt))
    return finish_execution(project, run_root, config, dict(driver._read_json(path)))


def advance_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import finish_review, prepare_review

    attempt_id = str(attempt["attempt_id"])
    revision = 1
    while True:
        key = f"r{revision}"
        stage = f"tc-to-autotest:{key}"
        waiting = _automation_step(project, run_root, attempt, config, effective, revision)
        if waiting is not None:
            return waiting
        published = _stage_artifact(run_root, attempt_id, stage)
        automation = published["artifact"]
        ledger = driver._ledger(run_root, attempt_id, key)
        if ledger is None:
            prepare_review(run_root, attempt_id,
                           driver._review_payload(project, run_root, attempt, config, effective["document"], package=None, automation=automation),
                           input_byte_budget=int(config["review_input_bytes"]), response_reserve_bytes=int(config["review_reserve_bytes"]),
                           instructions=driver._REVIEW_INSTRUCTIONS["automation"], session_id=f"automation-{key}-{attempt_id[:12]}")
            ledger = driver._ledger(run_root, attempt_id, key)
        if ledger["status"] == "WAITING":
            task = driver._review_step(project, run_root, attempt, config, key)
            if task is not None:
                return task
            finish_review(run_root, attempt_id, key)
            ledger = driver._ledger(run_root, attempt_id, key)
        review = _review_output(run_root, attempt_id, key)
        verdict = None if review is None or ledger["status"] != "COMPLETED" else review["artifacts"]["autotest_review"]["verdict"]
        if verdict == "AUTO_FIX_APPLIED" and revision == 1:
            revision = 2
            continue
        if verdict != "ПРИНЯТО":
            # The canonical review was accepted, so the canonical reason codes (REWORK,
            # REVIEW_CONTEXT_LIMIT) do not describe this stop; automation gets its own labels.
            reason = "AUTOMATION_REVIEW_REJECTED"
            if verdict == "AUTO_FIX_APPLIED":
                reason = "AUTOMATION_REVISION_BUDGET"
            elif ledger["status"] == "ABORTED":
                aborted = next((event.get("reason_code") for event in ledger["events"] if event["event_type"] == "REVIEW_SESSION_ABORTED"), None)
                reason = {"REVIEW_CONTEXT_LIMIT": "AUTOMATION_REVIEW_CONTEXT_LIMIT", "REVIEW_TRANSPORT_FAILED": "AUTOMATION_REVIEW_TRANSPORT_FAILED",
                          "REVIEW_INCOMPLETE": "AUTOMATION_REVIEW_INCOMPLETE"}.get(aborted, reason)
            _finalize_rejected_automation(project, run_root, attempt, effective, reason)
            return driver._done(run_root, driver._result_summary(run_root, driver._attempt(run_root)))
        _materialize(project, run_root, attempt, effective, automation, review, key)
        waiting = _approval_step(project, run_root, attempt, config)
        if waiting is not None:
            return waiting
        execution = _execute(project, run_root, attempt, config, effective, automation, review, key)
        if execution.get("human_exec"):
            output = execution["output"]
            return {"action": "human_exec", "run_id": run_root.name, "attempt_id": attempt_id,
                    **{name: output[name] for name in ("kind", "step", "step_index", "steps_total", "commands", "selectors", "report_path", "report_dir", "continue")
                       if name in output},
                    "instructions": ("Выполните команды по порядку сами, в своём окружении (cwd указан у каждой); пакет их не запускает. "
                                     "Затем передайте отчёт(ы): " + str(output.get("continue")))}
        return finish_execution(project, run_root, config, execution)


def finish_execution(project: Path, run_root: Path, config: Mapping[str, Any], execution: Mapping[str, Any]) -> dict[str, Any]:
    attempt = driver._attempt(run_root)
    output = execution.get("output", {})
    if attempt["state"] != "TERMINAL":
        return driver._done(run_root, {"status": "stopped", "stop_reason": output.get("reason") or "EXECUTION_NOT_FINALIZED", "run_id": run_root.name,
                                       "attempt_id": attempt["attempt_id"], "exit_code": execution.get("exit_code", 2), "exec": output})
    regeneration = output.get("regeneration")
    if isinstance(regeneration, Mapping) and regeneration.get("allowed") is True and config.get("answers", {}).get(_regeneration_task_id(attempt)) is None:
        return driver._ask_task(
            run_root, str(attempt["attempt_id"]), "regenerate-after-gate",
            "Сгенерированные тесты не прошли компиляцию или сбор и убраны из проекта. Создать дочернюю попытку и сгенерировать исправленную ревизию "
            "(кейсы и ревью в ней проходят заново)? Это разрешено один раз.",
            [{"value": "regenerate", "label": "Да, создать дочернюю попытку"}, {"value": "stop", "label": "Нет, завершить с текущим результатом"}])
    # Opt-in mutations: survivor triage tasks come before `done` (they change nothing in the attempt).
    from tools.pipeline_driver_strength import triage_step

    task = triage_step(project, run_root, attempt, config)
    if task is not None:
        return task
    summary = driver._result_summary(run_root, attempt)
    summary["exec"] = {key: output.get(key) for key in ("verdict", "accepted", "reason", "automation_revision", "regeneration") if key in output}
    return driver._done(run_root, summary)


def _regeneration_task_id(attempt: Mapping[str, Any]) -> str:
    return driver._task_id(str(attempt["attempt_id"]), "ask.regenerate-after-gate")


def start_regeneration(project: Path, run_root: Path, config: dict[str, Any]) -> None:
    """Create the one allowed child attempt after ``GENERATED_TEST_INVALID`` and give it its context."""
    from tools.pilot_state import create_attempt, publish_context_selection, read_attempt_receipt, read_execution_baseline_for_attempt
    from tools.project_inventory import select_context_batches

    parent = driver._attempt(run_root)
    parent_id = str(parent["attempt_id"])
    baseline = read_execution_baseline_for_attempt(run_root, parent_id)
    outputs: list[str] = []
    try:
        receipt = read_attempt_receipt(run_root, parent_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
        outputs = [str(run_root / row["path"]) for row in receipt["execution"].get("artifact_evidence", []) if row.get("kind") == "runner_output"]
    except (KeyError, TypeError, ValueError):
        outputs = []
    child = create_attempt(run_root, {"project": parent["project"], "module": parent["module"], "policy_profile": parent["policy_profile"],
                                      "parent_attempt_id": parent_id, "retry_reason": _REGENERATION_REASON}, baseline)
    _baseline, inventory = driver._baseline_and_inventory(run_root, child)
    for receipt in driver._context_receipts(run_root, parent_id):
        ids = [item["opaque_id"] for item in receipt["files"]]
        budget = max(int(receipt.get("byte_budget") or 0), int(receipt.get("byte_count") or 0), 1)
        for batch in select_context_batches(inventory, project, ids, byte_budget=budget, include_closed_manifests=False):
            publish_context_selection(run_root, str(child["attempt_id"]), batch["receipt"])
    config["regeneration_hint"] = {"attempt_id": parent_id, "reason": _REGENERATION_REASON, "output_paths": outputs}
    driver._save_config(run_root, config)
