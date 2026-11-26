"""Durable, append-only Phase 1 controller state with safe local receipts."""
from __future__ import annotations

import sys
import types
from typing import Any, Callable, Sequence

from tools import execution_gate
from tools.confined_output import OutputConfinementError, atomic_write_confined_bytes_at_root, create_confined_bytes_exclusive, create_confined_directory_exclusive, ensure_project_child_directory, read_confined_bytes, remove_confined_bytes_if_equal
from tools.run_store import (  # the files of a run, below the journal (П1 of the cut of v0.8): what the code here reads and serves
    CLOSURE_CACHE as _CLOSURE_CACHE,
    DIGEST as _DIGEST,
    InputRefusal as _InputRefusal,
    PACK_ROOT as _PACK_ROOT,
    SCHEMA_ROOT as _SCHEMA_ROOT,
    UNSAFE_RUN_ROOT as _UNSAFE_RUN_ROOT,
    canonical_bytes as _canonical_bytes,
    canonical_digest as _digest,
    check_sealed_value as _check_sealed,
    credential_like as _credential_like,
    exit_code,
    is_reparse as _is_reparse,
    moved_project_allowed,
    moved_recorded as _moved_recorded,
    parse_json_bytes as _parse_json_bytes,
    project_root as _project_root,
    project_state_for as _project_state_for,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    read_run,
    run_boundary as _run_boundary,
    run_project_move,
    run_root_is_unsafe,
    run_root_of as _run_root,
    safe_label as _safe_label,
    safe_model_label as _safe_model_label,
    sealed as _sealed,
    terminal_result,
    validate_authorization as _validate_authorization,
    validate_result_record as _validate_result_record,
    validate_schema as _validate_schema,
)
from tools.run_journal import (  # the journal of events, right above the files of a run (П1 of the cut of v0.8): what the code here reads and serves
    CLOSURE_KINDS as _CLOSURE_KINDS,
    MODEL_STAGE_INSTANCE as _MODEL_STAGE_INSTANCE,
    REWORK_BATCH,
    REWORK_REVIEW_STAGE as _REWORK_REVIEW_STAGE,
    REWORK_STAGE as _REWORK_STAGE,
    append_event,
    append_journal_event as _append_event,
    check_phase1_artifact as _check_phase1_artifact,
    closure_cache,
    closure_cached,
    closure_target as _closure_target,
    derive_state,
    frozen_baseline,
    frozen_inventory as _frozen_inventory,
    model_lifecycle_projection_with_state as _model_lifecycle_projection_with_state,
    read_closure_artifact,
    validate_execution_event_branch as _validate_execution_event_branch,
)
from tools.run_receipts import (  # the receipts of an attempt (П1 of the cut of v0.8): what the code here reads and serves
    PRIOR_KIND as _PRIOR_KIND,
    ReceiptFileUnbound,
    SKELETON_KIND as _SKELETON_KIND,
    bind_events as _bind_events,
    execution_inputs_frontier_closed as _execution_inputs_frontier_closed,
    phase5_generated_delta as _phase5_generated_delta,
    publish_bound,
    read_attempt_receipt,
    read_attempt_receipt_with_state as _read_attempt_receipt_with_state,
    read_resume_validation_if_present,
    receipt_target as _receipt_target,
    recover_execution_inputs_events,
)
from tools.review_ledger import (  # the ledger of the reviewer (П1 of the cut of v0.8): what the code here reads and serves
    LEFT_MARK,
    disposition_policy_at as _disposition_policy_at,
    read_reviewer_session_ledger,
    read_reviewer_session_ledger_with_state as _read_reviewer_session_ledger_with_state,
    require_fresh_context_statement,
    self_review_accepted_at as _self_review_accepted_at,
    terminal_reviewer_evidence,
    terminal_reviewer_evidence_with_state as _terminal_reviewer_evidence_with_state,
)


_MODEL_STAGE_REGISTRY = {
    "context-marker": ("generator", "context-marker-v1"),
    "tc-generator": ("generator", "tc-generator-v1"),
    "tc-reviewer": ("canonical-reviewer", "canonical-reviewer-v1"),
    "tc-to-autotest": ("automation-generator", "tc-to-autotest-v1"),
    "autotest-reviewer": ("automation-reviewer", "autotest-static-reviewer-v1"),
}


# Public names for execution_core / finalize_attempt: the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.
append_journal_event = _append_event
check_sealed_value = _check_sealed
frozen_inventory = _frozen_inventory
publish_artifact_value = _publish
run_root_of = _run_root
validate_execution_event_branch = _validate_execution_event_branch
publish_fixed_receipt_resuming = publish_bound  # the public name (execution_core, exec_commands) is the one contract; drop it when they call publish_bound
canonical_digest = _digest


# what ``tools.execution_journal`` (mode B) takes from here — public names of the same objects (П1 of the cut of v0.8)
DIGEST = _DIGEST
PACK_ROOT = _PACK_ROOT
SCHEMA_ROOT = _SCHEMA_ROOT
credential_like = _credential_like
disposition_policy_at = _disposition_policy_at
execution_inputs_frontier_closed = _execution_inputs_frontier_closed
model_lifecycle_projection_with_state = _model_lifecycle_projection_with_state
phase5_generated_delta = _phase5_generated_delta
read_artifact = _read_artifact
read_attempt_receipt_with_state = _read_attempt_receipt_with_state
receipt_target = _receipt_target
run_boundary = _run_boundary
safe_label = _safe_label
sealed = _sealed
self_review_accepted_at = _self_review_accepted_at
validate_schema = _validate_schema

# what the modules of the journal above this one (the records and the inputs of a step of the model, the step, the effective canonical
# set, the closure, the observations) take from here — public names of the same objects (П1 of the cut of v0.8)
CLOSURE_CACHE = _CLOSURE_CACHE
CLOSURE_KINDS = _CLOSURE_KINDS
MODEL_STAGE_INSTANCE = _MODEL_STAGE_INSTANCE
MODEL_STAGE_REGISTRY = _MODEL_STAGE_REGISTRY
PRIOR_KIND = _PRIOR_KIND
REWORK_REVIEW_STAGE = _REWORK_REVIEW_STAGE
REWORK_STAGE = _REWORK_STAGE
SKELETON_KIND = _SKELETON_KIND
bind_events = _bind_events
canonical_bytes = _canonical_bytes
closure_target = _closure_target
parse_json_bytes = _parse_json_bytes
read_reviewer_session_ledger_with_state = _read_reviewer_session_ledger_with_state
safe_model_label = _safe_model_label
terminal_reviewer_evidence_with_state = _terminal_reviewer_evidence_with_state
validate_result_record = _validate_result_record


# The facade of the journal for the cut of v0.8 (handoff: design-core-split.md §2.2 П2; tests: orch-tools/tests/test_facade.py):
# a name moved out is read from the module that took it, and a patch of a name reaches every module of the journal binding it.
_MISSING = object()


def _facade(name: str, owners: Callable[[], Sequence[types.ModuleType]],
            optional: Callable[[], Sequence[types.ModuleType]] = tuple) -> None:
    """Make the loaded module ``name`` the facade of ``owners()`` and ``optional()``."""
    def modules() -> tuple[types.ModuleType, ...]:
        return (*owners(), *optional())

    class Facade(types.ModuleType):
        def __getattr__(self, attribute: str) -> Any:
            if attribute.startswith("__") and attribute.endswith("__"):  # the import machinery asks for these: never an owner's
                raise AttributeError(attribute)
            for module in modules():
                value = vars(module).get(attribute, _MISSING)
                if value is not _MISSING:
                    return value
            raise AttributeError(f"module {name!r} has no attribute {attribute!r}")

        def __setattr__(self, attribute: str, value: Any) -> None:
            own = vars(self)
            if attribute.startswith("__") and attribute.endswith("__"):
                types.ModuleType.__setattr__(self, attribute, value)
                return
            current = own.get(attribute, _MISSING)
            if current is _MISSING:
                current = next((vars(module)[attribute] for module in modules() if attribute in vars(module)), _MISSING)
            bound = [module for module in modules() if current is not _MISSING and vars(module).get(attribute, _MISSING) is current]
            if attribute in own or not bound:
                types.ModuleType.__setattr__(self, attribute, value)
            for module in bound:
                setattr(module, attribute, value)

    sys.modules[name].__class__ = Facade


def _journal_modules() -> tuple[types.ModuleType, ...]:
    """The modules that took the code of the journal, the lowest first (imported here: those above it import this module at their top;
    ``run_store``, ``run_journal``, ``run_receipts`` and ``review_ledger`` below it are imported at the top of this one)."""
    from tools import (effective_canonical, model_inputs, model_records, model_steps, review_ledger, run_closure, run_journal, run_receipts,
                       run_store, scenario_observations)

    return (run_store, run_journal, run_receipts, review_ledger, model_records, effective_canonical, run_closure, model_inputs, model_steps,
            scenario_observations)


def _journal_mode_b() -> tuple[types.ModuleType, ...]:
    """The module of the journal of mode B, when the delivery carries mode B: once loaded, from ``sys.modules`` — a miss of the facade
    asks no finder (a blocked module, ``None`` there, still reads as absent); before that, through the gate."""
    loaded = sys.modules.get("tools.execution_journal")
    if loaded is not None:
        return (loaded,)
    return (execution_gate.load_mode_b("execution_journal"),) if execution_gate.mode_b_available() else ()


_facade(__name__, _journal_modules, _journal_mode_b)
