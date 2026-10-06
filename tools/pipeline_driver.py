"""Deterministic pipeline driver: the model only answers tasks, the code runs the pipeline.

Three commands, all printing one JSON object::

    python -m tools.pipeline_driver next   --project <abs> --profile cases-only-v1 --docs <path> ...   # start a run
    python -m tools.pipeline_driver next   --project <abs> --run <id>                                    # current task
    python -m tools.pipeline_driver submit --project <abs> --run <id> --task-id <id> --output <path>    # answer a task
    python -m tools.pipeline_driver status --project <abs> --run <id>

``next`` performs every deterministic step itself (scan, inventory, baseline,
attempt, batches, assembly, publication, review bookkeeping, selection,
materialization, execution, finalization) and stops only when a model or a
person has to answer.  Then it prints one task::

    {"action": "llm", "task_id", "stage", "skill_path", "inputs": [...], "output_path", "schema_path"}
    {"action": "ask_user", "task_id", "question", "options": [...]}
    {"action": "done", "result": {...}}

``submit`` validates the answer, computes every digest and canonical ordering
itself, publishes the artifact, advances the run and prints the next task.  The
model never computes SHA-256, never sorts arrays by code point and never
carries paths between calls: every path it needs is in the task.

The durable truth stays in ``.pilot-runs/<run_id>/`` (journal and receipts).
The driver keeps only rebuildable working files next to it, in
``.pilot-runs/<run_id>.driver/``.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import uuid
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("cases-only-v1", "local-pilot-v1")
_DEFAULT_REVIEW_INPUT_BYTES = 200_000
_DEFAULT_REVIEW_RESERVE_BYTES = 20_000
_REVIEW_INSTRUCTIONS = {
    "canonical": "Проверь назначенные области этой части по исходным требованиям: полноту, корректность шагов и ожидаемых результатов, "
                 "согласованность между кейсами. Верни только coverage, findings, corrections и required_checks.",
    "automation": "Проверь назначенные области этой части: соответствие автотестов кейсам, реальную границу приложения, "
                  "литералы, привязки и сравнения. Верни только coverage, findings, corrections и required_checks.",
}
_CATEGORY_ORDER_SOURCE = "tools.schema_validation"


class DriverError(ValueError):
    """A driver-level failure with a stable code; the run itself is never left half-written."""

    def __init__(self, code: str, message: str, diagnostics: Sequence[Mapping[str, Any]] = ()) -> None:
        self.code = code
        self.diagnostics = [dict(row) for row in diagnostics]
        super().__init__(f"{code}: {message}")


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------

def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> Any:
    from tools.schema_validation import load_json_strict

    return load_json_strict(path)


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex[:12]}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex[:12]}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


def _run_root(project: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
        raise DriverError("DRIVER_INPUT", "run id must be the 32-character id printed by `next`")
    root = Path(project).resolve() / ".pilot-runs" / run_id
    if not root.is_dir():
        raise DriverError("DRIVER_INPUT", "unknown run")
    return root


def work_dir(run_root: Path) -> Path:
    """Rebuildable driver files live beside the run, never inside its durable root."""
    return run_root.with_name(run_root.name + ".driver")


def _config(run_root: Path) -> dict[str, Any]:
    path = work_dir(run_root) / "config.json"
    if not path.is_file():
        raise DriverError("DRIVER_INPUT", "this run was not started by the driver")
    return dict(_read_json(path))


def _save_config(run_root: Path, config: Mapping[str, Any]) -> None:
    _write_json(work_dir(run_root) / "config.json", dict(config))


def _task_path(run_root: Path, task_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", task_id or ""):
        raise DriverError("DRIVER_INPUT", "invalid task id")
    return work_dir(run_root) / "tasks" / f"{task_id}.json"


def _task_id(attempt_id: str, label: str) -> str:
    return f"{attempt_id[:8]}.{re.sub(r'[^A-Za-z0-9._-]', '.', label)}"


def _attempt(run_root: Path) -> dict[str, Any]:
    from tools.pilot_state import derive_state

    attempts = derive_state(run_root)["attempts"]
    if not attempts:
        raise DriverError("DRIVER_STATE", "the run has no attempt")
    return dict(attempts[-1])


def _events(run_root: Path, attempt_id: str) -> list[dict[str, Any]]:
    from tools.pilot_state import derive_state

    return [event for event in derive_state(run_root)["events"] if event.get("attempt_id") == attempt_id]


def _stage_events(events: Sequence[Mapping[str, Any]], stage: str) -> dict[str, Mapping[str, Any]]:
    return {event["event_type"]: event for event in events if event.get("stage_instance_id") == stage}


def _baseline_and_inventory(run_root: Path, attempt: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt

    baseline = read_execution_baseline(run_root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"))
    return baseline, inventory


def _context_receipts(run_root: Path, attempt_id: str) -> list[dict[str, Any]]:
    from tools.pilot_state import read_context_selection

    digests = [event["artifact_digest"] for event in _events(run_root, attempt_id) if event["event_type"] == "CONTEXT_SELECTED"]
    return [dict(read_context_selection(run_root, attempt_id, digest)) for digest in dict.fromkeys(digests)]


def _generation_context(project: Path, run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any]:
    """The one context receipt a generator fragment is bound to: every selected file.

    ``scan`` may split a large project into several receipts.  A fragment binds
    exactly one receipt, so the driver publishes one more receipt that carries
    all of them; the generator then reads the same files as the other roles.
    Files that scan reported as gaps stay gaps.
    """
    from tools.pilot_state import publish_context_selection
    from tools.project_inventory import select_context_batches

    attempt_id = str(attempt["attempt_id"])
    receipts = _context_receipts(run_root, attempt_id)
    if not receipts:
        raise DriverError("DRIVER_STATE", "the run has no context receipt")
    if len(receipts) == 1:
        return receipts[0]
    everything = {item["opaque_id"] for receipt in receipts for item in receipt["files"]}
    for receipt in receipts:
        if {item["opaque_id"] for item in receipt["files"]} == everything:
            return receipt
    _baseline, inventory = _baseline_and_inventory(run_root, attempt)
    budget = sum(max(int(receipt.get("byte_count") or 0), 1) for receipt in receipts)
    batches = select_context_batches(inventory, project, sorted(everything), byte_budget=budget, include_closed_manifests=False)
    if len(batches) != 1 or batches[0]["receipt"].get("gaps"):
        raise DriverError("DRIVER_STATE", "the combined generation context could not be selected as one receipt")
    return dict(publish_context_selection(run_root, attempt_id, batches[0]["receipt"]))


def _skill(stage: str) -> str:
    return str(ROOT / "skills" / stage / "SKILL.md")


# --------------------------------------------------------------------------------------
# task construction
# --------------------------------------------------------------------------------------

def _llm_task(run_root: Path, attempt_id: str, label: str, *, stage: str, skill: str, inputs: Sequence[Path], schema: Mapping[str, Any],
              instructions: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    task_id = _task_id(attempt_id, label)
    directory = work_dir(run_root)
    schema_path = _write_json(directory / "tasks" / f"{task_id}.schema.json", schema)
    task = {
        "action": "llm", "task_id": task_id, "stage": stage, "skill_path": _skill(skill),
        "inputs": [str(path) for path in inputs], "output_path": str(directory / "outputs" / f"{task_id}.json"),
        "schema_path": str(schema_path), "instructions": instructions,
        # Total size of the inputs, so the host can compare it with the model capacity before the call.
        "input_bytes": sum(Path(path).stat().st_size for path in inputs if Path(path).is_file()),
        "run_id": run_root.name, "attempt_id": attempt_id,
        **dict(extra or {}),
    }
    (directory / "outputs").mkdir(parents=True, exist_ok=True)
    _write_json(_task_path(run_root, task_id), task)
    return task


def _ask_task(run_root: Path, attempt_id: str, label: str, question: str, options: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    task_id = _task_id(attempt_id, "ask." + label)
    task = {"action": "ask_user", "task_id": task_id, "question": question, "options": [dict(option) for option in options],
            "run_id": run_root.name, "attempt_id": attempt_id}
    _write_json(_task_path(run_root, task_id), task)
    return task


def _done(run_root: Path | None, result: Mapping[str, Any]) -> dict[str, Any]:
    payload = {"action": "done", "result": dict(result)}
    if run_root is not None:
        payload["run_id"] = run_root.name
    return payload


def _subschema(schema_name: str, keep: Sequence[str]) -> dict[str, Any]:
    """A task schema: the pack schema restricted to the fields the model writes."""
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    result = {
        "$schema": schema.get("$schema"), "type": "object", "additionalProperties": False, "required": list(keep),
        "properties": {name: schema["properties"][name] for name in keep},
        "x-pack-schema": f"schemas/{schema_name}",
        "x-note": "References ($ref) resolve against the pack schemas directory. Service fields, digests and ordering are filled by the driver on submit.",
    }
    if "$defs" in schema:
        result["$defs"] = schema["$defs"]
    return result


# --------------------------------------------------------------------------------------
# start: scan
# --------------------------------------------------------------------------------------

def start_run(project: Path, options: Mapping[str, Any]) -> dict[str, Any]:
    """Create the run with the existing ``scan`` command and remember driver options."""
    from tools import run_pipeline

    profile = options.get("profile")
    docs = list(options.get("docs") or [])
    if profile not in PROFILES:
        raise DriverError("DRIVER_INPUT", "starting a run requires --profile cases-only-v1 or local-pilot-v1")
    if not docs:
        raise DriverError("DRIVER_INPUT", "starting a run requires at least one --docs requirement document")
    arguments = SimpleNamespace(project=str(project), profile=profile, docs=docs, module=options.get("module"), target=options.get("target"))
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = run_pipeline.cmd_scan(arguments)
    except Exception as error:  # the same stops the `scan` command line reports as exit 2
        from tools.skillsrc_manifest import SkillsrcError

        if not isinstance(error, (run_pipeline.HostStop, SkillsrcError, RuntimeError, FileNotFoundError, ValueError)):
            raise
        return {"action": "done", "result": {"status": "error", "stage": "scan", "exit_code": 2, "reason": getattr(error, "code", None) or type(error).__name__,
                                             "message": str(error), "errors": list(getattr(error, "diagnostics", None) or [])}}
    try:
        payload = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        payload = {}
    if code != 0 or payload.get("status") != "ok":
        # exit 3 from scan means .skillsrc needs a human decision (needs_input / conflict)
        return {"action": "done", "result": {"status": "stopped" if code == 3 else "error", "stage": "scan", "exit_code": code if code else 2,
                                             **{key: payload.get(key) for key in ("reason", "detail", "message", "run_id", "stop_reason", "scope") if key in payload}}}
    run_root = Path(payload["run_root"])
    snapshot = run_pipeline._docs_entries(project, docs)
    config = {
        "schema_version": "1.0.0", "profile": profile, "module_id": payload.get("module_id"), "target": options.get("target"),
        "docs": [row["path"] for row in snapshot],
        "subject": options.get("subject") or project.name, "document_id": options.get("document_id"),
        "author": options.get("author") or "pipeline-driver", "date": options.get("date") or date.today().isoformat(),
        "model_id": options.get("model_id"), "host_cli": options.get("host_cli") or "unspecified", "host_cli_version": options.get("host_cli_version") or "unspecified",
        "host_settings": options.get("host_settings") or "driver-default",
        "reviewer_isolation": options.get("reviewer_isolation"),
        "review_input_bytes": int(options.get("review_input_bytes") or _DEFAULT_REVIEW_INPUT_BYTES),
        "review_reserve_bytes": int(options.get("review_reserve_bytes") or _DEFAULT_REVIEW_RESERVE_BYTES),
        "context_gaps": payload.get("context_gaps", []),
    }
    _save_config(run_root, config)
    return advance(project, run_root)


# --------------------------------------------------------------------------------------
# context-marker
# --------------------------------------------------------------------------------------

def _context_inputs(project: Path, run_root: Path, attempt: Mapping[str, Any], *, only: Mapping[str, Any] | None = None) -> list[Path]:
    """Write the exact, already masked context bytes the model is allowed to read.

    ``only`` limits the files to one context receipt: a generator fragment is bound
    to exactly one receipt, so its task must not carry bytes of another one.
    """
    from tools.project_inventory import select_context_batches

    _baseline, inventory = _baseline_and_inventory(run_root, attempt)
    directory = work_dir(run_root) / "inputs" / "context"
    paths: list[Path] = []
    for receipt in [only] if only is not None else _context_receipts(run_root, str(attempt["attempt_id"])):
        ids = [item["opaque_id"] for item in receipt["files"]]
        budget = max(int(receipt.get("byte_budget") or 0), int(receipt.get("byte_count") or 0), 1)
        batches = select_context_batches(inventory, project, ids, byte_budget=max(budget, 1), include_closed_manifests=False) if ids else []
        for batch in batches:
            for item in batch["files"]:
                target = directory / item["project_path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item["bytes"])
                if target not in paths:
                    paths.append(target)
    return paths


def _context_draft(project: Path, run_root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    from tools import run_pipeline
    from tools.build_context import build_context

    snapshot = run_pipeline._docs_entries(project, list(config["docs"]))
    context = build_context(project, docs_snapshot=snapshot)
    draft = {key: value for key, value in context.items() if key != "status"}
    if not draft.get("artifacts", {}).get("source_code_and_diff", {}).get("sources"):
        draft.setdefault("artifacts", {}).setdefault("source_code_and_diff", {})["sources"] = ["driver — источники кода не анализировались детерминированно"]
    return draft


def _context_marker_task(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import publish_model_request

    attempt_id = str(attempt["attempt_id"])
    stage = "context-marker:baseline"
    baseline, _inventory = _baseline_and_inventory(run_root, attempt)
    receipts = _context_receipts(run_root, attempt_id)
    draft = _context_draft(project, run_root, config)
    draft_path = _write_json(work_dir(run_root) / "inputs" / "context-marker-draft.json", draft)
    inputs = [draft_path, *_context_inputs(project, run_root, attempt)]
    if "MODEL_REQUESTED" not in _stage_events(_events(run_root, attempt_id), stage):
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"context-{uuid.uuid4().hex[:16]}",
                              input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], *[receipt["digest"] for receipt in receipts]])
    schema = json.loads((ROOT / "schemas" / "context-marker-output.schema.json").read_text(encoding="utf-8"))
    return _llm_task(
        run_root, attempt_id, "context-marker", stage=stage, skill="context-marker", inputs=inputs, schema=schema,
        instructions=("Первый файл — готовый черновик ответа: требования уже нормализованы кодом, их нельзя менять, удалять или переставлять. "
                      "Дополни `artifacts.source_code_and_diff.sources` наблюдениями по коду и `warnings` пробелами требований "
                      "(каждая строка в виде «источник — наблюдение») и сохрани весь объект в output_path."),
        extra={"draft_path": str(draft_path)},
    )


def _submit_context_marker(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], task: Mapping[str, Any], value: Any) -> None:
    from tools.pilot_state import publish_model_stage_artifact
    from tools.schema_validation import schema_diagnostics

    rows = schema_diagnostics(value, ROOT / "schemas" / "context-marker-output.schema.json", ROOT)
    if rows:
        raise DriverError("TASK_OUTPUT_INVALID", "context-marker output does not match its schema", rows)
    draft = _context_draft(project, run_root, config)
    if value["artifacts"]["analytics_documentation"]["requirements"] != draft["artifacts"]["analytics_documentation"]["requirements"]:
        raise DriverError("TASK_OUTPUT_INVALID", "normalized source requirements must stay exactly as in the draft", [
            {"path": "/artifacts/analytics_documentation/requirements", "code": "REQUIREMENTS_CHANGED",
             "message": "Copy the requirements array from the draft unchanged; report gaps in `warnings`."}])
    publish_model_stage_artifact(run_root, str(attempt["attempt_id"]), "context-marker:baseline", value, transport_attempts=int(task.get("transport_attempts", 1)))


def _marker_artifact(run_root: Path, attempt_id: str) -> dict[str, Any] | None:
    from tools.pilot_state import read_model_stage_artifact

    response = _stage_events(_events(run_root, attempt_id), "context-marker:baseline").get("MODEL_RESPONSE_RECEIVED")
    if response is None:
        return None
    return dict(read_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", response["artifact_digest"]))


# --------------------------------------------------------------------------------------
# batches and generation
# --------------------------------------------------------------------------------------

def _header(project: Path, run_root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", str(config.get("document_id") or project.name).lower()).strip("-") or "project"
    document_id = config.get("document_id") or f"TCDOC-{slug}-{run_root.name[:8]}"
    return {
        "schema_version": "1.0.0", "document_id": document_id, "revision": 1, "parent_sha256": None, "content_locale": "ru-RU",
        "metadata": {"subject": {"kind": "generic", "name": str(config["subject"])}, "documentation": list(config["docs"]),
                     "project": project.name, "author": str(config["author"]), "date": str(config["date"])},
    }


def _plan(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], marker: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Deterministic batch plan: one batch unless the configuration proves independent groups."""
    from tools.batch_assembly import plan_batches

    header = _header(project, run_root, config)
    context = _generation_context(project, run_root, attempt)
    requirements = marker["artifact"]["artifacts"]["analytics_documentation"]["requirements"]
    plan = plan_batches(requirements, {"header_digest": _sha(header), "context_receipt_digest": context["digest"]})
    return header, plan, context


def _generator_schema() -> dict[str, Any]:
    schema = _subschema("candidate-fragment.schema.json", ["requirements", "source_to_canonical_mappings", "operation_capabilities", "test_cases", "diagnostics"])
    for name in ("requirements", "source_to_canonical_mappings", "operation_capabilities", "test_cases"):
        items = schema["properties"][name].get("items")
        if isinstance(items, dict) and str(items.get("$ref", "")).startswith("canonical-test-document.schema.json"):
            schema["properties"][name] = {"type": "array", "items": {"$ref": str(ROOT / "schemas" / items["$ref"])}}
    return schema


def _generator_task(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], marker: Mapping[str, Any],
                    header: Mapping[str, Any], plan: Mapping[str, Any], context: Mapping[str, Any], batch: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import publish_model_request

    attempt_id = str(attempt["attempt_id"])
    stage = f"tc-generator:{batch['batch_id']}"
    owned = set(batch["owned_source_requirement_ids"])
    namespace = batch["namespace"]
    brief = {
        "batch_id": batch["batch_id"], "namespace": namespace,
        "owned_source_requirements": [row for row in plan["source_requirements"] if row["source_requirement_id"] in owned],
        "id_prefixes": {"requirement_id": f"CREQ-{namespace}-", "case_id": f"TC-{namespace}-", "step_id": f"STEP-{namespace}-", "input_id": f"INPUT-{namespace}-",
                        "expectation_id": f"EXP-{namespace}-", "assertion_id": f"ASSERT-{namespace}-", "blocker_id": f"BLOCK-{namespace}-"},
        "document_header": dict(header),
        "context_gaps": list(config.get("context_gaps", [])),
        "filled_by_driver": ["batch_id", "namespace", "plan_digest", "header_digest", "context_receipt_digest", "context_receipt", "owned_source_requirement_ids",
                             "status", "schema_version", "digest", "display_order", "ordering of capabilities, arguments, results, blockers and categories"],
    }
    brief_path = _write_json(work_dir(run_root) / "inputs" / f"generator-{batch['batch_id']}.json", brief)
    marker_path = _write_json(work_dir(run_root) / "inputs" / "context-marker-output.json", marker["artifact"])
    inputs = [brief_path, marker_path, *_context_inputs(project, run_root, attempt, only=context)]
    if "MODEL_REQUESTED" not in _stage_events(_events(run_root, attempt_id), stage):
        publish_model_request(run_root, attempt_id, stage, model_id=config.get("model_id"), invocation_id=f"generator-{uuid.uuid4().hex[:16]}",
                              input_digests=[marker["content_digest"], context["digest"], plan["digest"], plan["header_digest"]])
    return _llm_task(
        run_root, attempt_id, f"tc-generator.{batch['batch_id']}", stage=stage, skill="tc-generator", inputs=inputs, schema=_generator_schema(),
        instructions=("Первый файл — задание батча: какие исходные требования покрыть и какие префиксы идентификаторов использовать. "
                      "Верни только содержательную часть фрагмента: requirements, source_to_canonical_mappings, operation_capabilities, test_cases, diagnostics. "
                      "Служебные поля, дайджесты, display_order и порядок массивов заполнит драйвер."),
        extra={"batch_id": batch["batch_id"]},
    )


def _category_order() -> list[str]:
    from tools import schema_validation

    for name in dir(schema_validation):
        value = getattr(schema_validation, name)
        if name.isupper() and "CATEGOR" in name and isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return list(value)
    schema = json.loads((ROOT / "schemas" / "canonical-test-document.schema.json").read_text(encoding="utf-8"))
    categories = schema["$defs"]["test_case"]["properties"]["categories"]["items"].get("enum", [])
    return list(categories)


def normalize_fragment_content(content: Mapping[str, Any]) -> dict[str, Any]:
    """Apply every mechanical canonical rule so the model never has to: order and numbering."""
    value = deepcopy({key: content.get(key, []) for key in ("requirements", "source_to_canonical_mappings", "operation_capabilities", "test_cases", "diagnostics")})

    def renumber(items: Any) -> None:
        if isinstance(items, list):
            for index, item in enumerate(items, start=1):
                if isinstance(item, dict):
                    item["display_order"] = index

    capabilities = value["operation_capabilities"]
    if isinstance(capabilities, list) and all(isinstance(item, dict) and isinstance(item.get("capability_id"), str) for item in capabilities):
        capabilities.sort(key=lambda item: item["capability_id"])
        for capability in capabilities:
            for field in ("arguments", "results"):
                rows = capability.get(field)
                if isinstance(rows, list) and all(isinstance(row, dict) and isinstance(row.get("name"), str) for row in rows):
                    rows.sort(key=lambda row: row["name"])
    renumber(value["requirements"])
    order = {item.get("requirement_id"): index for index, item in enumerate(value["requirements"]) if isinstance(item, dict)} if isinstance(value["requirements"], list) else {}
    precedence = {name: index for index, name in enumerate(_category_order())}
    cases = value["test_cases"]
    renumber(cases)
    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        references = case.get("requirement_ids")
        if isinstance(references, list) and all(reference in order for reference in references):
            case["requirement_ids"] = sorted(dict.fromkeys(references), key=order.__getitem__)
        categories = case.get("categories")
        if isinstance(categories, list) and all(category in precedence for category in categories):
            case["categories"] = sorted(dict.fromkeys(categories), key=precedence.__getitem__)
        steps = case.get("steps")
        renumber(steps)
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            for field in ("inputs", "outputs", "expectations"):
                renumber(step.get(field))
            blockers = step.get("automation_blockers")
            if isinstance(blockers, list) and all(isinstance(row, dict) and isinstance(row.get("blocker_id"), str) for row in blockers):
                blockers.sort(key=lambda row: row["blocker_id"])
            for expectation in step.get("expectations") if isinstance(step.get("expectations"), list) else []:
                if isinstance(expectation, dict):
                    renumber(expectation.get("assertions"))
    return value


def _build_fragment(content: Mapping[str, Any], plan: Mapping[str, Any], context: Mapping[str, Any], batch: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_fragment_content(content)
    fragment = {
        "schema_version": "1.0.0", "status": "COMPLETE", "batch_id": batch["batch_id"], "namespace": batch["namespace"],
        "plan_digest": plan["digest"], "header_digest": plan["header_digest"], "context_receipt_digest": context["digest"],
        "context_receipt": dict(context), "owned_source_requirement_ids": list(batch["owned_source_requirement_ids"]),
        **normalized,
    }
    fragment["digest"] = _sha(fragment)
    return fragment


def _submit_generator(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], task: Mapping[str, Any], value: Any) -> None:
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.pilot_state import publish_model_stage_artifact

    if not isinstance(value, dict):
        raise DriverError("TASK_OUTPUT_INVALID", "generator output must be a JSON object")
    attempt_id = str(attempt["attempt_id"])
    marker = _marker_artifact(run_root, attempt_id)
    header, plan, context = _plan(project, run_root, attempt, config, marker)
    batch = next((item for item in plan["batches"] if item["batch_id"] == task.get("batch_id")), None)
    if batch is None:
        raise DriverError("DRIVER_STATE", "the task batch is not in the current plan")
    unknown = sorted(set(value) - {"requirements", "source_to_canonical_mappings", "operation_capabilities", "test_cases", "diagnostics"})
    if unknown:
        raise DriverError("TASK_OUTPUT_INVALID", "generator output has fields the driver owns", [
            {"path": "/" + name, "code": "DRIVER_OWNED_FIELD", "message": "Remove this field: the driver fills service fields and digests."} for name in unknown])
    fragment = _build_fragment(value, plan, context, batch)
    try:
        validate_fragment(fragment, plan, header, context)
        if len(plan["batches"]) == 1:
            assemble_candidate(header, plan, [fragment])
    except BatchAssemblyError as error:
        raise DriverError("TASK_OUTPUT_INVALID", str(error), error.diagnostics or [{"path": "", "code": error.code, "message": str(error)}]) from error
    publish_model_stage_artifact(run_root, attempt_id, f"tc-generator:{batch['batch_id']}", fragment, transport_attempts=int(task.get("transport_attempts", 1)))


def _fragments(run_root: Path, attempt_id: str, plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    from tools.pilot_state import read_model_stage_artifact

    events = _events(run_root, attempt_id)
    result: dict[str, dict[str, Any]] = {}
    for batch in plan["batches"]:
        stage = f"tc-generator:{batch['batch_id']}"
        response = _stage_events(events, stage).get("MODEL_RESPONSE_RECEIVED")
        if response is not None:
            result[batch["batch_id"]] = dict(read_model_stage_artifact(run_root, attempt_id, stage, response["artifact_digest"]))
    return result


# --------------------------------------------------------------------------------------
# assembly, review, selection
# --------------------------------------------------------------------------------------

def _receipt_to_json(receipt: Any) -> dict[str, Any]:
    from tools.orchestrate_test_case_revision import _receipt_json

    return dict(_receipt_json(receipt))


def _assembled(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble (deterministically, again if needed) and publish the UNREVIEWED candidate once."""
    from tools.batch_assembly import assemble_candidate
    from tools.orchestrate_test_case_revision import publish_unreviewed_candidate
    from tools.publish_test_case_bundle import verify_bundle

    attempt_id = str(attempt["attempt_id"])
    marker = _marker_artifact(run_root, attempt_id)
    header, plan, _context = _plan(project, run_root, attempt, config, marker)
    fragments = _fragments(run_root, attempt_id, plan)
    document, assembly = assemble_candidate(header, plan, [fragments[batch["batch_id"]]["artifact"] for batch in plan["batches"]])
    bundle = _bundle_dir(run_root, attempt)
    published = any(event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly" for event in _events(run_root, attempt_id))
    receipt = verify_bundle(document, bundle) if published else publish_unreviewed_candidate(document, bundle, run_root=run_root, attempt_id=attempt_id)
    directory = work_dir(run_root) / "artifacts"
    _write_json(directory / "candidate.json", document)
    _write_json(directory / "assembly-receipt.json", assembly)
    _write_json(directory / "candidate-receipt.json", _receipt_to_json(receipt))
    return {"document": document, "assembly": assembly, "receipt": receipt, "plan": plan, "fragments": fragments, "marker": marker, "bundle": bundle}


def _bundle_dir(run_root: Path, attempt: Mapping[str, Any]) -> Path:
    """The first attempt publishes into ``candidate-bundle``; a child attempt gets its own directory."""
    name = "candidate-bundle" if attempt.get("parent_attempt_id") is None else f"candidate-bundle-{str(attempt['attempt_id'])[:8]}"
    return work_dir(run_root) / name


def _review_payload(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], document: Mapping[str, Any], *,
                    package: Mapping[str, Any] | None, automation: Mapping[str, Any] | None) -> dict[str, Any]:
    from tools import run_pipeline
    from tools.project_inventory import redact_text

    snapshot = run_pipeline._docs_entries(project, list(config["docs"]))
    sources = [{"path": row["path"], "sha256": row["sha256"], "content": row["content"]} for row in snapshot]
    del redact_text
    return {"document": dict(document), "automation": None if automation is None else dict(automation), "package_binding": None if package is None else dict(package),
            "sources": sources, "contexts": [],
            "requirements_binding": {"module_id": config.get("module_id"), "selected_target": config.get("target"),
                                     "docs": [{"path": row["path"], "sha256": row["sha256"]} for row in sources]}}


def _host_evidence(config: Mapping[str, Any], review_key: str) -> dict[str, Any]:
    from tools.review_parts import review_digest

    isolation = {"fresh_context": config.get("reviewer_isolation") == "fresh", "distinct_invocations": config.get("reviewer_isolation") == "fresh",
                 "role_policy": "canonical-reviewer-v2" if review_key == "canonical" else "autotest-static-reviewer-v2"}
    isolation["evidence_digest"] = review_digest(isolation)
    return {"reviewer_invocation_id": f"review-{review_key}-{uuid.uuid4().hex[:16]}", "model_id": config.get("model_id"), "host_isolation": isolation,
            "cli": str(config["host_cli"]), "cli_version": str(config["host_cli_version"]), "settings": str(config["host_settings"])}


def _review_schema() -> dict[str, Any]:
    schema = json.loads((ROOT / "schemas" / "review-part-output.schema.json").read_text(encoding="utf-8"))
    keep = ["coverage", "findings", "corrections", "required_checks"]
    return {"$schema": schema.get("$schema"), "type": "object", "additionalProperties": False, "required": keep,
            "properties": {name: schema["properties"][name] for name in keep},
            "x-note": "Service fields (plan, snapshot, part and input digests) are bound by the driver on submit."}


def _ledger(run_root: Path, attempt_id: str, review_key: str) -> dict[str, Any] | None:
    from tools.pilot_state import read_reviewer_session_ledger

    try:
        return dict(read_reviewer_session_ledger(run_root, attempt_id, review_key=review_key))
    except ValueError:
        return None


def _open_review_task(run_root: Path, attempt_id: str, review_key: str) -> dict[str, Any] | None:
    """The review task that is already open (requested, not answered), if any."""
    directory = work_dir(run_root) / "tasks"
    events = _events(run_root, attempt_id)
    for path in sorted(directory.glob(f"{attempt_id[:8]}.review.{review_key}.*.json")):
        if path.name.endswith(".schema.json"):
            continue
        task = _read_json(path)
        stage_events = _stage_events(events, task["stage"])
        if "MODEL_REQUESTED" in stage_events and "MODEL_RESPONSE_RECEIVED" not in stage_events:
            return dict(task)
    return None


def _review_step(project: Path, run_root: Path, attempt: Mapping[str, Any], config: Mapping[str, Any], review_key: str) -> dict[str, Any] | None:
    """Return the next reviewer task, or None when every available part has been handled."""
    from tools.pilot_state import next_review_part, open_review_part

    attempt_id = str(attempt["attempt_id"])
    pending = _open_review_task(run_root, attempt_id, review_key)
    if pending is not None:
        return pending
    envelope = next_review_part(run_root, attempt_id, review_key)
    if envelope is None:
        return None
    opened = open_review_part(run_root, attempt_id, review_key, _host_evidence(config, review_key))
    label = f"review.{review_key}.{opened['stage_instance_id'].rsplit(':', 1)[1]}"
    input_path = _write_json(work_dir(run_root) / "inputs" / f"{_task_id(attempt_id, label)}.input.json", opened["input"])
    return _llm_task(
        run_root, attempt_id, label, stage=opened["stage_instance_id"], skill="tc-reviewer" if review_key == "canonical" else "autotest-reviewer",
        inputs=[input_path], schema=_review_schema(),
        instructions=("Выполни эту задачу в свежем изолированном контексте (отдельный вызов без истории генерации). Единственный вход — точный конверт части ревью. "
                      "У повторяющихся входов вместо content стоит content_ref на первое вхождение в этой же части. "
                      "Верни только coverage, findings, corrections и required_checks. Проверку за пределами своей части адресуй "
                      "в required_checks по case_ids и requirement_ids из document_index, а не по своим scope_ids. Если получить пригодную оценку не удалось — "
                      "вызови submit с --failed TRANSPORT или --failed CONTENT и --reason."),
        extra={"review_key": review_key, "part_id": opened["input"]["part_id"], "try": opened.get("try", 1), "requires_fresh_context": True},
    )


def _submit_review(run_root: Path, attempt: Mapping[str, Any], task: Mapping[str, Any], value: Any, *, failed: str | None, reason: str | None) -> None:
    from tools.pilot_state import fail_review_part, submit_review_part

    attempt_id = str(attempt["attempt_id"])
    if failed is not None:
        fail_review_part(run_root, attempt_id, task["review_key"], task["part_id"], failed, reason or "")
        return
    if not isinstance(value, dict):
        raise DriverError("TASK_OUTPUT_INVALID", "review assessment must be a JSON object")
    try:
        submit_review_part(run_root, attempt_id, task["review_key"], task["part_id"], value, transport_attempts=int(task.get("transport_attempts", 1)))
    except ValueError as error:
        from tools.schema_validation import schema_diagnostics

        rows = schema_diagnostics(value, Path(task["schema_path"]), ROOT) if False else []
        raise DriverError("TASK_OUTPUT_INVALID", f"review assessment was not accepted: {error}", rows or [
            {"path": "", "code": "REVIEW_ASSESSMENT_INVALID",
             "message": "Every scope of the part needs one coverage row with status CHECKED or UNCHECKED, non-empty evidence and assessment; "
                        "findings and corrections must follow the task schema. Fix the answer and submit again, or submit --failed CONTENT."}]) from error


def _package(run_root: Path, attempt_id: str, assembled: Mapping[str, Any]) -> dict[str, Any]:
    from tools.orchestrate_test_case_revision import reviewer_package_binding

    _baseline, inventory = _baseline_and_inventory(run_root, _attempt(run_root))
    contexts = _context_receipts(run_root, attempt_id)
    used = {fragment["artifact"]["context_receipt_digest"] for fragment in assembled["fragments"].values()}
    return reviewer_package_binding(
        assembled["document"], assembled["receipt"], assembled["assembly"], inventory, [item for item in contexts if item["digest"] in used] or contexts,
        context_marker_output=assembled["marker"]["artifact"], generator_fragments=[fragment["artifact"] for fragment in assembled["fragments"].values()],
        run_root=run_root, attempt_id=attempt_id,
    )


# --------------------------------------------------------------------------------------
# finalization
# --------------------------------------------------------------------------------------

def _terminal_facts(run_root: Path, attempt: Mapping[str, Any], *, document: Mapping[str, Any] | None) -> dict[str, Any]:
    """Result facts of a branch that never executes project code, derived from the durable ledger."""
    from tools.pilot_state import read_run, terminal_reviewer_evidence

    attempt_id = str(attempt["attempt_id"])
    reviewer = terminal_reviewer_evidence(run_root, attempt_id)
    verdict = reviewer["authoritative_verdict"]
    accepted_review = verdict == "ACCEPTED" and reviewer["isolation"] == "verified"
    steps = [step for case in (document or {}).get("test_cases", []) for step in case.get("steps", [])]
    manual = sum(step.get("manual_only") is True for step in steps)
    coverage = None if not accepted_review else "MANUAL_ONLY" if steps and manual == len(steps) else "MIXED" if manual else "FULL"
    reason = None if accepted_review else "REWORK" if verdict == "REJECTED" else reviewer.get("abort_reason") or "REVIEW_INCOMPLETE"
    return {
        "run_id": read_run(run_root)["manifest"]["run_id"], "attempt_id": attempt_id, "attempt_state": "TERMINAL",
        "completion": "COMPLETE" if accepted_review else "PARTIAL", "verification": "NOT_APPLICABLE", "coverage": coverage, "reason_code": reason,
        "canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True,
        "reviewer_session_complete": reviewer["session_complete"], "authoritative_verdict": verdict,
        "authoritative_verdict_count": reviewer["authoritative_verdict_count"], "reviewer_pre_verdict_abort": reviewer["pre_verdict_abort"],
        "reviewer_isolation_state": reviewer["isolation"],
        "blocker_count": sum(len(step.get("automation_blockers", [])) for step in steps), "trace_valid": True,
        "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
        "materialization_applicability": "NOT_APPLICABLE", "execution_applicability": "NOT_APPLICABLE",
        "automation_accepted": False, "generated_required_count": 0, "generated_materialized_count": 0, "generated_retained_count": 0,
        "exact_target_pass": False, "mixed_manual_traceable": False, "operational_reliable": True,
        "prior_stage_cause": "CANONICAL_COMPLETE" if accepted_review else reason,
        "policy_profile": attempt["policy_profile"],
    }


def _finalize_without_execution(project: Path, run_root: Path, attempt: Mapping[str, Any], *, document: Mapping[str, Any] | None) -> dict[str, Any]:
    from tools.finalize_attempt import finalize_attempt

    facts = _terminal_facts(run_root, attempt, document=document)
    branch: dict[str, Any] = {"attempt_id": attempt["attempt_id"], "evidence": {}}
    if facts["reason_code"] is not None:
        branch["stage_causes"] = [facts["reason_code"]]
    return dict(finalize_attempt(branch, None, project=project, verification="NOT_APPLICABLE", facts=facts, run_root=run_root))


def _result_summary(run_root: Path, attempt: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pilot_state import exit_code, read_effective_canonical_if_present, read_terminal_result

    attempt_id = str(attempt["attempt_id"])
    terminal = dict(read_terminal_result(run_root, attempt_id))
    directory = work_dir(run_root)
    effective = read_effective_canonical_if_present(run_root, attempt_id)
    paths = {name: str(path) for name, path in (
        ("candidate_bundle", _bundle_dir(run_root, attempt)),
        ("run_root", run_root), ("driver_dir", directory)) if path.exists()}
    return {
        "status": "terminal", "run_id": run_root.name, "attempt_id": attempt_id, "policy_profile": attempt["policy_profile"],
        "completion": terminal.get("completion"), "verification": terminal.get("verification"), "coverage": terminal.get("coverage"),
        "accepted": terminal.get("accepted"), "reason_code": terminal.get("reason_code"), "exit_code": exit_code(terminal),
        "effective_document_digest": None if effective is None else effective.get("document_digest"), "paths": paths,
    }


# --------------------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------------------

def advance(project: Path, run_root: Path) -> dict[str, Any]:
    """Run deterministic steps until a model or a person has to act, or the attempt is terminal."""
    from tools.pilot_state import finish_review, prepare_review, read_effective_canonical_if_present

    project = Path(project).resolve()
    config = _config(run_root)
    attempt = _attempt(run_root)
    attempt_id = str(attempt["attempt_id"])
    if attempt["state"] == "TERMINAL":
        if attempt["policy_profile"] == "local-pilot-v1":
            from tools.pipeline_driver_automation import terminal_step

            return terminal_step(project, run_root, attempt, config)
        return _done(run_root, _result_summary(run_root, attempt))

    _generation_context(project, run_root, attempt)
    events = _events(run_root, attempt_id)
    if "MODEL_RESPONSE_RECEIVED" not in _stage_events(events, "context-marker:baseline"):
        return _context_marker_task(project, run_root, attempt, config)

    marker = _marker_artifact(run_root, attempt_id)
    header, plan, context = _plan(project, run_root, attempt, config, marker)
    fragments = _fragments(run_root, attempt_id, plan)
    for batch in plan["batches"]:
        if batch["batch_id"] not in fragments:
            return _generator_task(project, run_root, attempt, config, marker, header, plan, context, batch)

    assembled = _assembled(project, run_root, attempt, config)
    ledger = _ledger(run_root, attempt_id, "canonical")
    if ledger is None:
        if config.get("reviewer_isolation") not in {"fresh", "none"}:
            return _ask_task(run_root, attempt_id, "reviewer-isolation",
                             "Может ли хост выполнять каждую часть ревью в свежем изолированном контексте (отдельный вызов без истории генерации)?",
                             [{"value": "fresh", "label": "Да, каждая часть ревью пойдёт в отдельном свежем контексте"},
                              {"value": "none", "label": "Нет, отдельного контекста нет — ревью не будет принято как независимое"}])
        if config["reviewer_isolation"] == "none":
            return _done(run_root, {"status": "stopped", "stop_reason": "REVIEWER_ISOLATION_UNAVAILABLE", "run_id": run_root.name, "attempt_id": attempt_id,
                                    "detail": "Кандидат опубликован как UNREVIEWED; независимое ревью невозможно без свежего контекста.",
                                    "paths": {"candidate_bundle": str(assembled["bundle"])}})
        package = _package(run_root, attempt_id, assembled)
        prepare_review(run_root, attempt_id, _review_payload(project, run_root, attempt, config, assembled["document"], package=package, automation=None),
                       input_byte_budget=int(config["review_input_bytes"]), response_reserve_bytes=int(config["review_reserve_bytes"]),
                       instructions=_REVIEW_INSTRUCTIONS["canonical"], session_id=f"canonical-{attempt_id[:12]}")
        ledger = _ledger(run_root, attempt_id, "canonical")
    if ledger["status"] == "WAITING":
        task = _review_step(project, run_root, attempt, config, "canonical")
        if task is not None:
            return task
        finish_review(run_root, attempt_id, "canonical")
        ledger = _ledger(run_root, attempt_id, "canonical")

    effective = read_effective_canonical_if_present(run_root, attempt_id)
    if ledger["status"] == "COMPLETED" and effective is None:
        effective = _select(project, run_root, attempt, assembled, ledger)
    if effective is None or attempt["policy_profile"] == "cases-only-v1":
        _finalize_without_execution(project, run_root, attempt, document=None if effective is None else effective["document"])
        return _done(run_root, _result_summary(run_root, _attempt(run_root)))
    from tools.pipeline_driver_automation import advance_automation

    return advance_automation(project, run_root, attempt, config, effective)


def _select(project: Path, run_root: Path, attempt: Mapping[str, Any], assembled: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any] | None:
    from tools.orchestrate_test_case_revision import orchestrate_revision
    from tools.pilot_state import read_effective_canonical_if_present, read_review_aggregate

    attempt_id = str(attempt["attempt_id"])
    review = read_review_aggregate(run_root, attempt_id)["output"]
    package = _package(run_root, attempt_id, assembled)
    orchestrate_revision(assembled["document"], assembled["receipt"], package, ledger, review, assembled["bundle"], run_root=run_root, attempt_id=attempt_id)
    effective = read_effective_canonical_if_present(run_root, attempt_id)
    return None if effective is None else dict(effective)


# --------------------------------------------------------------------------------------
# submit
# --------------------------------------------------------------------------------------

def submit(project: Path, run_root: Path, task_id: str, *, output: Path | None = None, answer: str | None = None,
           failed: str | None = None, reason: str | None = None, transport_attempts: int = 1) -> dict[str, Any]:
    project = Path(project).resolve()
    path = _task_path(run_root, task_id)
    if not path.is_file():
        raise DriverError("DRIVER_INPUT", "unknown task id")
    task = dict(_read_json(path))
    config = _config(run_root)
    attempt = _attempt(run_root)
    if task.get("attempt_id") != attempt["attempt_id"]:
        return advance(project, run_root)
    if task["action"] == "ask_user":
        allowed = [option["value"] for option in task["options"]]
        if answer not in allowed:
            raise DriverError("DRIVER_INPUT", f"--answer must be one of: {', '.join(allowed)}")
        if config.get("answers", {}).get(task_id) is not None:
            return advance(project, run_root)  # already answered: submit is idempotent
        if task_id.endswith("ask.reviewer-isolation"):
            config["reviewer_isolation"] = answer
        config.setdefault("answers", {})[task_id] = answer
        _save_config(run_root, config)
        if task_id.endswith("ask.regenerate-after-gate") and answer == "regenerate":
            from tools.pipeline_driver_automation import start_regeneration

            start_regeneration(project, run_root, config)
        return advance(project, run_root)
    if attempt["state"] == "TERMINAL":
        return advance(project, run_root)

    task["transport_attempts"] = transport_attempts
    stage_events = _stage_events(_events(run_root, str(attempt["attempt_id"])), task["stage"])
    if "MODEL_RESPONSE_RECEIVED" in stage_events:
        return advance(project, run_root)  # already answered: submit is idempotent
    is_review = task["stage"].startswith(("tc-reviewer:", "autotest-reviewer:"))
    if failed is not None and not is_review:
        raise DriverError("DRIVER_INPUT", "--failed applies to review tasks only")
    if failed is not None and not (reason or "").strip():
        raise DriverError("DRIVER_INPUT", "--failed requires --reason")
    value: Any = None
    try:
        if failed is None:
            source = output or Path(task["output_path"])
            try:
                value = _read_json(Path(source))
            except Exception as error:  # strict JSON loader raises its own error types
                raise DriverError("TASK_OUTPUT_INVALID", f"task output is not strict UTF-8 JSON: {error}") from error
        if is_review:
            _submit_review(run_root, attempt, task, value, failed=failed, reason=reason)
        elif task["stage"] == "context-marker:baseline":
            _submit_context_marker(project, run_root, attempt, config, task, value)
        elif task["stage"].startswith("tc-generator:"):
            _submit_generator(project, run_root, attempt, config, task, value)
        elif task["stage"].startswith("tc-to-autotest:"):
            from tools.pipeline_driver_automation import submit_automation

            submit_automation(project, run_root, attempt, config, task, value)
        else:
            raise DriverError("DRIVER_STATE", "unsupported task stage")
    except DriverError as error:
        if error.code != "TASK_OUTPUT_INVALID":
            raise
        return {**task, "status": "rejected", "errors": error.diagnostics or [{"path": "", "code": error.code, "message": str(error)}], "message": str(error)}
    return advance(project, run_root)


def status(project: Path, run_root: Path) -> dict[str, Any]:
    from tools.pilot_state import derive_state

    state = derive_state(run_root)
    attempt = state["attempts"][-1] if state["attempts"] else None
    stages: dict[str, str] = {}
    for event in state["events"]:
        if attempt is not None and event.get("attempt_id") == attempt["attempt_id"] and event.get("stage_instance_id"):
            stages[event["stage_instance_id"]] = event["event_type"]
    return {"action": "status", "run_id": run_root.name, "attempt_id": None if attempt is None else attempt["attempt_id"],
            "attempt_state": None if attempt is None else attempt["state"], "event_count": len(state["events"]), "stages": stages}


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic pipeline driver: next / submit / status.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("next", "submit", "status"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True, help="Absolute path of the target project.")
        command.add_argument("--run", required=name != "next", help="Run id printed by the first `next`.")
        if name == "next":
            command.add_argument("--profile", choices=PROFILES, help="Required to start a run.")
            command.add_argument("--docs", action="append", default=None, help="Requirement document inside the project; repeatable.")
            command.add_argument("--module", help="Exact module ID when the project has several.")
            command.add_argument("--target")
            command.add_argument("--subject", help="Human title of the document (Russian).")
            command.add_argument("--document-id")
            command.add_argument("--model-id")
            command.add_argument("--host-cli")
            command.add_argument("--host-cli-version")
            command.add_argument("--host-settings")
            command.add_argument("--reviewer-isolation", choices=("fresh", "none"))
            command.add_argument("--review-input-bytes", type=int)
            command.add_argument("--review-reserve-bytes", type=int)
        if name == "submit":
            command.add_argument("--task-id", required=True)
            command.add_argument("--output", type=Path, help="Answer file; defaults to the task's output_path.")
            command.add_argument("--answer", help="Answer to an ask_user task.")
            command.add_argument("--failed", choices=("TRANSPORT", "CONTENT"), help="A review call produced no usable assessment.")
            command.add_argument("--reason")
            command.add_argument("--transport-attempts", type=int, choices=(1, 2, 3), default=1)
    return parser


def _exit_code(payload: Mapping[str, Any]) -> int:
    if payload.get("action") == "done":
        result = payload.get("result", {})
        return int(result.get("exit_code", 0 if result.get("status") == "terminal" else 2))
    if payload.get("action") in {"llm", "ask_user"}:
        return 3  # waiting for a model or a person, same meaning as the pipeline's exit 3
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        project = Path(args.project).resolve()
        if args.command == "next" and not args.run:
            payload = start_run(project, {key: getattr(args, key) for key in (
                "profile", "docs", "module", "target", "subject", "document_id", "model_id", "host_cli", "host_cli_version", "host_settings",
                "reviewer_isolation", "review_input_bytes", "review_reserve_bytes")})
        else:
            run_root = _run_root(project, args.run)
            if args.command == "next":
                payload = advance(project, run_root)
            elif args.command == "status":
                payload = status(project, run_root)
            else:
                payload = submit(project, run_root, args.task_id, output=args.output, answer=args.answer, failed=args.failed, reason=args.reason,
                                 transport_attempts=args.transport_attempts)
    except DriverError as error:
        print(json.dumps({"action": "error", "code": error.code, "message": str(error), "errors": error.diagnostics}, ensure_ascii=False, sort_keys=True))
        return 2
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"action": "error", "code": "DRIVER_FAILURE", "message": f"{type(error).__name__}: {error}"}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return _exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())
