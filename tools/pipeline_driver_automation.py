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
from tools.pipeline_driver import DriverError, ROOT

_AUTOMATION_FIELDS = ("automation_status", "generated_files", "generated_symbols", "implementation_relations", "manual_dispositions", "diagnostics")
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
    if "MODEL_REQUESTED" not in driver._stage_events(driver._events(run_root, attempt_id), stage):
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"automation-{uuid.uuid4().hex[:16]}",
                              input_digests=[effective["document_digest"], effective["effective_bundle_receipt_digest"]])
    return driver._llm_task(
        run_root, attempt_id, f"tc-to-autotest.r{revision}", stage=stage, skill="tc-to-autotest", inputs=inputs, schema=_automation_schema(),
        instructions=("Первый файл — задание, второй — принятый набор кейсов (единственный источник смысла). Верни automation_status, generated_files "
                      "(file_id, path относительно корня модуля, language, framework, content), generated_symbols, implementation_relations, "
                      "manual_dispositions и diagnostics. Дайджесты, source, номер ревизии и порядок массивов заполнит драйвер."),
        extra={"automation_revision": revision},
    )


def normalize_automation(content: Mapping[str, Any], document: Mapping[str, Any], *, revision: int, effective: Mapping[str, Any],
                         predecessor: Mapping[str, Any] | None, previous_review: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build the full automation artifact: digests, lineage and canonical order come from code."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256

    value = deepcopy({name: content.get(name) for name in _AUTOMATION_FIELDS})
    for row in value["generated_files"] if isinstance(value["generated_files"], list) else []:
        if isinstance(row, dict) and isinstance(row.get("content"), str):
            row["content_digest"] = "sha256:" + hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
    cases = {case["case_id"]: (index, case) for index, case in enumerate(document.get("test_cases", []))}

    def position(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
        case = cases.get(row.get("case_id"))
        if case is None:
            return None
        steps = {step["step_id"]: (index, step) for index, step in enumerate(case[1]["steps"])}
        step = steps.get(row.get("step_id"))
        if step is None:
            return None
        expectation_index = assertion_index = -1
        if row.get("kind") == "assertion":
            expectations = {item["expectation_id"]: (index, item) for index, item in enumerate(step[1]["expectations"])}
            expectation = expectations.get(row.get("expectation_id"))
            if expectation is None:
                return None
            expectation_index = expectation[0]
            assertions = {item["assertion_id"]: index for index, item in enumerate(expectation[1]["assertions"])}
            if row.get("assertion_id") not in assertions:
                return None
            assertion_index = assertions[row["assertion_id"]]
        return (case[0], step[0], 0 if row.get("kind") == "operation" else 1, expectation_index, assertion_index, str(row.get("file_id")), str(row.get("symbol_id")))

    for name in ("implementation_relations", "manual_dispositions"):
        rows = value[name]
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            keys = [position(row) for row in rows]
            if all(key is not None for key in keys):
                value[name] = [row for _key, row in sorted(zip(keys, rows), key=lambda pair: pair[0])]
    artifact = {
        "schema_version": "5.0.0", "stage": "tc-to-autotest",
        "artifacts": {
            "automation_status": value["automation_status"],
            "source": {"document_id": document["document_id"], "revision": document["revision"], "source_digest": effective["document_digest"],
                       "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"]},
            "automation_revision": revision,
            "predecessor_automation_sha256": None if predecessor is None else automation_sha256(predecessor),
            "correction_review_sha256": None if previous_review is None else autotest_review_sha256(previous_review),
            "generated_files": value["generated_files"], "generated_symbols": value["generated_symbols"],
            "implementation_relations": value["implementation_relations"], "manual_dispositions": value["manual_dispositions"], "diagnostics": value["diagnostics"],
        },
        "warnings": list(content.get("warnings") or []),
    }
    return artifact


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
    predecessor = previous_review = None
    if revision == 2:
        predecessor = _stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"]
        previous_review = _review_output(run_root, attempt_id, "r1")
    artifact = normalize_automation(value, effective["document"], revision=revision, effective=effective, predecessor=predecessor, previous_review=previous_review)
    rows = schema_diagnostics(artifact, ROOT / "schemas" / "tc-to-autotest-output.schema.json", ROOT) or validate_automation_artifact(artifact, effective["document"])
    if rows:
        raise DriverError("TASK_OUTPUT_INVALID", "automation artifact is invalid", [
            {"path": str(row.get("path", "")).replace("/artifacts/", "/", 1), "code": row.get("code", ""), "message": row.get("message", "")} for row in rows])
    publish_model_stage_artifact(run_root, attempt_id, f"tc-to-autotest:r{revision}", artifact, transport_attempts=int(task.get("transport_attempts", 1)))


def _finalize_rejected_automation(project: Path, run_root: Path, attempt: Mapping[str, Any], effective: Mapping[str, Any], reason: str) -> None:
    from tools.finalize_attempt import finalize_attempt

    facts = driver._terminal_facts(run_root, attempt, document=effective["document"])
    facts.update({"completion": "PARTIAL", "coverage": None, "reason_code": reason, "prior_stage_cause": reason})
    finalize_attempt({"attempt_id": attempt["attempt_id"], "evidence": {}, "stage_causes": [reason]}, None, project=project,
                     verification="NOT_APPLICABLE", facts=facts, run_root=run_root)


def _execute(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], effective: Mapping[str, Any],
             automation: Mapping[str, Any], review: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Materialize the accepted files and run them once through the existing ``exec`` command."""
    from tools import run_pipeline
    from tools.generated_delta import materialize_delta
    from tools.pilot_state import read_attempt_receipt, read_run

    attempt_id = str(attempt["attempt_id"])
    baseline, _inventory = driver._baseline_and_inventory(run_root, attempt)
    try:
        read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
    except (KeyError, TypeError, ValueError):
        # Not materialized yet.  After an interrupted `next` the delta is already durable
        # and `exec` below resumes the started execution instead of writing files again.
        history: dict[str, Any] = {}
        if key == "r2":
            history = {"automation_history": [_stage_artifact(run_root, attempt_id, "tc-to-autotest:r1")["artifact"]],
                       "review_history": [_review_output(run_root, attempt_id, "r1")]}
        materialize_delta(project, _module_root(project, attempt), baseline, automation, review, canonical_document=effective["document"],
                          run_root=run_root, attempt_id=attempt_id, **history)
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
    arguments = SimpleNamespace(project=str(project), run=run_root.name, module=config.get("module_id"), language=None, target=config.get("target"),
                                executor="local", docs=None, **{name: str(path) for name, path in paths.items()})
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = run_pipeline.cmd_exec(arguments)
    try:
        output = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        output = {"status": "error", "raw": buffer.getvalue()[-2000:]}
    driver._write_json(_execution_path(run_root, attempt_id), {"exit_code": code, "output": output})
    return {"exit_code": code, "output": output}


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
        published = _stage_artifact(run_root, attempt_id, stage)
        if published is None:
            return _automation_task(project, run_root, attempt, config, effective, revision)
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
        execution = _execute(project, run_root, attempt, config, effective, automation, review, key)
        return finish_execution(project, run_root, config, execution)


def finish_execution(project: Path, run_root: Path, config: Mapping[str, Any], execution: Mapping[str, Any]) -> dict[str, Any]:
    attempt = driver._attempt(run_root)
    output = execution.get("output", {})
    if attempt["state"] != "TERMINAL":
        return driver._done(run_root, {"status": "stopped", "stop_reason": output.get("reason") or "EXECUTION_NOT_FINALIZED", "run_id": run_root.name,
                                       "attempt_id": attempt["attempt_id"], "exit_code": execution.get("exit_code", 2), "exec": output})
    summary = driver._result_summary(run_root, attempt)
    summary["exec"] = {key: output.get(key) for key in ("verdict", "accepted", "reason", "automation_revision", "regeneration") if key in output}
    regeneration = output.get("regeneration")
    if isinstance(regeneration, Mapping) and regeneration.get("allowed") is True and config.get("answers", {}).get(_regeneration_task_id(attempt)) is None:
        return driver._ask_task(
            run_root, str(attempt["attempt_id"]), "regenerate-after-gate",
            "Сгенерированные тесты не прошли компиляцию или сбор и убраны из проекта. Создать дочернюю попытку и сгенерировать исправленную ревизию "
            "(кейсы и ревью в ней проходят заново)? Это разрешено один раз.",
            [{"value": "regenerate", "label": "Да, создать дочернюю попытку"}, {"value": "stop", "label": "Нет, завершить с текущим результатом"}])
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
