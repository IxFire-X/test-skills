"""The fixed factual waiting spine of the F4 run, kept for the tests that build a run through it.

Moved out of ``tools.run_pipeline``: no entry point of the package calls it (``scan`` builds the run).  The imports stay
inside the functions, so a ``monkeypatch.setattr`` on ``tools.pilot_state`` or ``tools.finalize_attempt`` still reaches them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]


def run_phase_one_spine(project: Path, policy_profile: str, authorization: Mapping[str, Any], identity: Mapping[str, Any], baseline: Mapping[str, Any], facts: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist the fixed factual waiting spine without calling model, project, or executor code."""
    from tools.pilot_state import (
        append_event,
        create_attempt,
        create_run,
        derive_state,
        exit_code,
        freeze_phase_two_inputs,
        module_selection_digest,
        publish_attempt_receipt,
        remove_attempt_receipt_if_created,
    )
    from tools.pilot_state import _PublicationUnknown

    if facts:
        raise ValueError("terminal facts are not allowed in the F4 waiting spine")
    project_root = Path(project).resolve()
    module = identity.get("module")
    selection_digest = module_selection_digest(project_root, module)
    run = create_run(project_root, policy_profile, authorization)
    root = Path(run["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=selection_digest)
    module_root = project_root if module == "." else project_root.joinpath(*str(module).replace("\\", "/").split("/"))
    frozen = freeze_phase_two_inputs(root, project_root, module_root, baseline)
    attempt = create_attempt(root, identity, frozen["baseline"])
    artifact = publish_attempt_receipt(root, attempt["attempt_id"], "phase1-artifact", {
        "module_selection_digest": selection_digest,
        "baseline_digest": attempt["baseline_digest"],
    })
    append_event(root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=artifact["digest"])
    result = publish_attempt_receipt(root, attempt["attempt_id"], "structured-result", {
        "attempt_state": "WAITING_FOR_MODEL", "completion": None, "verification": None, "coverage": None,
    })
    try:
        append_event(root, "WAITING_FOR_MODEL", actor="controller", attempt_id=attempt["attempt_id"], artifact_digest=result["digest"])
    except _PublicationUnknown:
        raise
    except Exception:
        if not remove_attempt_receipt_if_created(root, result):
            return {
                "run": run,
                "state": derive_state(root),
                "artifact": artifact,
                "artifact_digest": artifact["digest"],
                "result": None,
                "controller_error": {
                    "code": "WAITING_RESULT_CLEANUP_CONFLICT",
                    "run_id": run["manifest"]["run_id"],
                    "attempt_id": attempt["attempt_id"],
                },
                "exit_code": 2,
            }
        raise
    state = derive_state(root)
    return {"run": run, "state": state, "artifact": artifact, "artifact_digest": artifact["digest"], "result": result, "exit_code": exit_code(result["record"])}


def resume_phase_one_spine(run_root: Path) -> Mapping[str, Any]:
    """Read-only fresh-context resume of the one durable F4 waiting spine."""
    from tools.pilot_state import derive_state, exit_code, read_attempt_receipt, read_run
    from tools.project_inventory import (
        InventoryError,
        baseline_external_input_paths,
        build_inventory,
        read_execution_baseline,
        read_inventory_receipt,
        runtime_identity,
        validate_execution_baseline,
    )

    run = read_run(run_root)
    root = Path(run["run_root"])
    state = derive_state(root)
    if len(state["attempts"]) != 1 or state["attempts"][0]["state"] != "WAITING_FOR_MODEL":
        raise ValueError("not a resumable F4 waiting spine")
    attempt = state["attempts"][0]
    if [event["event_type"] for event in state["events"]] != [
        "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND",
        "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED", "ARTIFACT_READ_BACK",
        "WAITING_FOR_MODEL",
    ]:
        raise ValueError("invalid F4 event order")
    try:
        baseline_name = str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"
        baseline = read_execution_baseline(root / "baselines" / baseline_name)
        inventory_name = str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json"
        frozen_inventory = read_inventory_receipt(root / "inventories" / inventory_name)
        if frozen_inventory["digest"] != baseline["inventory_digest"]:
            raise InventoryError("baseline inventory binding is invalid")
        project = Path(attempt["project"]).resolve()
        module_root = project if attempt["module"] == "." else project.joinpath(*str(attempt["module"]).split("/"))
        current_inventory = build_inventory(
            project,
            module_root,
            skill_pack_root=ROOT,
            generated_roots=(root.parent,),
            proved_dependency_files=baseline_external_input_paths(project, baseline),
        )
        from tools.project_inventory import validate_execution_baseline_binding

        policy_profile = str(attempt["policy_profile"])
        validate_execution_baseline_binding(baseline, current_inventory, module_root, policy_profile=policy_profile)
        runtime_path = baseline.get("interpreter_path") or baseline.get("wrapper_path") or baseline.get("executable_path")
        current_runtime_identity = None
        if policy_profile == "local-pilot-v1":
            if not isinstance(runtime_path, str):
                raise InventoryError("baseline runtime path is invalid")
            current_runtime_identity = runtime_identity(module_root, runtime_path, adapter_id=baseline.get("adapter_id"))
        validation = validate_execution_baseline(
            baseline, current_inventory, policy_profile=policy_profile,
            requirements=baseline["requirements"], adapter_id=baseline.get("adapter_id"),
            build_profile=baseline.get("build_profile"), adapter_parameters=baseline.get("adapter_parameters"),
            runtime_identity_value=current_runtime_identity, project_root=project,
        )
    except (InventoryError, KeyError, TypeError) as error:
        raise ValueError("execution baseline revalidation failed: BASELINE_INCOMPLETE") from error
    if validation["status"] != "UNCHANGED":
        reason = validation.get("reason_code", validation["status"])
        raise ValueError(f"execution baseline revalidation failed: {reason}")
    artifact = read_attempt_receipt(root, attempt["attempt_id"], "phase1-artifact", "ARTIFACT_READ_BACK")
    result = read_attempt_receipt(root, attempt["attempt_id"], "structured-result", "WAITING_FOR_MODEL")
    record = result["record"]
    if record.get("attempt_state") != "WAITING_FOR_MODEL" or any(record.get(axis) is not None for axis in ("completion", "verification", "coverage")):
        raise ValueError("invalid F4 waiting result")
    return {"run": run, "state": state, "artifact": artifact, "artifact_digest": artifact["digest"], "result": result, "exit_code": exit_code(record)}


def finalize_phase_one_spine(
    run_root: Path, branch: Mapping[str, Any], delta: Mapping[str, Any] | None, *, verification: str, facts: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Durably close the selected attempt through the frozen Phase 7 order."""
    from tools.finalize_attempt import finalize_attempt
    from tools.pilot_state import derive_state, exit_code, read_terminal_result

    state = derive_state(run_root)
    matches = [item for item in state["attempts"] if item["attempt_id"] == branch.get("attempt_id")]
    if len(matches) != 1:
        raise ValueError("finalization attempt is not bound to this run")
    record = finalize_attempt(branch, delta, project=Path(matches[0]["project"]), verification=verification, facts=facts, run_root=run_root)
    if record["idempotent"]:
        return {"result": record["result"], "exit_code": exit_code(record["result"]), "idempotent": True}
    result = read_terminal_result(run_root, str(branch["attempt_id"]))
    return {**record, "result": result, "exit_code": exit_code(result), "idempotent": False}
